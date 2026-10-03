#!/usr/bin/env python3
"""美股电话会议纪要 — 有限批次采集入口（现代 transcript_api 的薄编排层）。

职责边界：
- 所有 HTTP 经 transcript_api（流式 byte/deadline/host 约束、ProviderSettings、
  具名错误）；本文件不保留任何直连 Session 的第二套网络实现。
- ``--periods 2025Q4,2026Q1`` 是新的明确期间列表（精确 FY/Q）；legacy
  ``--quarters N`` 只在 metadata discovery 给出明确 FY/Q 后有限展开，
  无法唯一确定则 ``period_unresolved``、零正文抓取、不猜 Q4。
- 批次限额 ``--max-requests/--max-seconds/--max-response-bytes/
  --max-output-bytes`` 在每次 HTTP 前核对；触发后返回具名 partial/failure
  与已完成文档，不回滚、不覆盖已有原件。
- 默认原语言保存；只有显式 ``--translate`` 才初始化翻译器。
- ``--list`` 只做 metadata（零正文、零写入）；``--dry-run`` 零 HTTP 零写入；
  两者在目录/日志/锁初始化之前结束。
- ``--output`` 覆盖本次原件/日志/缓存/临时/锁全部写入位置。

退出码：0 成功；1 具名失败；2 用法/锁冲突；3 限额 partial。
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import sys
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

import requests

from common import (
    FileNaming,
    JsonCache,
    SingleInstanceLock,
    TranslatorFactory,
    build_bilingual_data,
    extract_body,
    get_path,
    load_companies,
    load_config,
    parse_transcript_header,
    split_paragraphs,
    translate_paragraphs,
)
from transcript_api import (
    CANDIDATE_FETCH_REQUEST_SCHEMA,
    DEFAULT_PROVIDER_SETTINGS,
    MAX_BODY_BYTES,
    REQUEST_SCHEMA,
    ProviderSettings,
    fetch_transcript,
    fetch_transcript_candidate,
    list_transcript_candidates,
)

log = logging.getLogger("scraper")

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_PARTIAL = 3

DEFAULT_MAX_REQUESTS = 64
DEFAULT_MAX_SECONDS = 600.0
DEFAULT_MAX_RESPONSE_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_OUTPUT_BYTES = 32 * 1024 * 1024
DEFAULT_REQUEST_TIMEOUT = 30

MANIFEST_NAME = "run_manifest.json"
_PERIOD_TOKEN_RE = re.compile(r"^(20\d{2})Q([1-4])$", re.IGNORECASE)
_FISCAL_LABEL_RE = re.compile(r"^(20\d{2})-Q([1-4])$")

# provider 范畴的失败：同批次后续请求只会重复同一失败，立即停批。
_PROVIDER_SCOPE_STATUSES = frozenset(
    {"unavailable", "rate_limited", "unsupported", "not_authorized"}
)
# 计入退出码 1 的具名失败（not_found/ambiguous 是合法结果，不算失败）。
_ERROR_STATUSES = frozenset({
    "unavailable", "rate_limited", "unsupported", "not_authorized",
    "provider_error", "deadline_exceeded", "content_too_large",
    "provenance_rejected", "invalid_request", "period_unresolved",
    "output_conflict", "output_limit",
})


class BatchBudgetExceeded(Exception):
    """Batch-level resource cap reached; carries the named reason."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class BatchBudget:
    """Whole-run quotas checked before every HTTP request and every write."""

    def __init__(
        self,
        max_requests: int,
        max_seconds: float,
        max_response_bytes: int,
        max_output_bytes: int,
    ):
        self.max_requests = max_requests
        self.requests_left = max_requests
        self.deadline = time.monotonic() + max_seconds
        self.max_response_bytes = max_response_bytes
        self.response_bytes_left = max_response_bytes
        self.max_output_bytes = max_output_bytes
        self.output_bytes_left = max_output_bytes
        self.exhausted: str | None = None

    def _exhaust(self, reason: str) -> None:
        if self.exhausted is None:
            self.exhausted = reason
        raise BatchBudgetExceeded(reason)

    def remaining_seconds(self) -> float:
        return self.deadline - time.monotonic()

    def check_request(self) -> None:
        if self.exhausted is not None:
            raise BatchBudgetExceeded(self.exhausted)
        if self.requests_left <= 0:
            self._exhaust("request_limit")
        if self.remaining_seconds() <= 0:
            self._exhaust("batch_deadline")
        if self.response_bytes_left <= 0:
            self._exhaust("response_bytes")

    def record_response(self, size: int) -> None:
        if size > self.response_bytes_left:
            self._exhaust("response_bytes")
        self.response_bytes_left -= size
        if self.remaining_seconds() <= 0:
            self._exhaust("batch_deadline")

    def take_output(self, size: int) -> None:
        if self.exhausted is not None:
            raise BatchBudgetExceeded(self.exhausted)
        if size > self.output_bytes_left:
            self._exhaust("output_bytes")
        self.output_bytes_left -= size

    def report(self) -> dict[str, Any]:
        return {
            "max_requests": self.max_requests,
            "requests_used": self.max_requests - self.requests_left,
            "max_response_bytes": self.max_response_bytes,
            "response_bytes_used": self.max_response_bytes - self.response_bytes_left,
            "max_output_bytes": self.max_output_bytes,
            "output_bytes_used": self.max_output_bytes - self.output_bytes_left,
            "exhausted": self.exhausted,
        }


class _BudgetResponse:
    """Delegating response that counts streamed bytes against the batch."""

    def __init__(self, inner: Any, budget: BatchBudget):
        self._inner = inner
        self._budget = budget

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def iter_content(self, chunk_size: int):
        for chunk in self._inner.iter_content(chunk_size=chunk_size):
            self._budget.record_response(len(chunk))
            yield chunk

    def close(self) -> None:
        self._inner.close()


class _BudgetSession:
    """Delegating session that enforces the batch quota before every HTTP GET."""

    def __init__(self, inner: Any, budget: BatchBudget):
        self._inner = inner
        self._budget = budget
        self.headers = inner.headers

    def get(self, url: str, **kwargs: Any) -> _BudgetResponse:
        self._budget.check_request()
        self._budget.requests_left -= 1
        return _BudgetResponse(self._inner.get(url, **kwargs), self._budget)

    def close(self) -> None:
        self._inner.close()


def new_request_id() -> str:
    return f"et-s3-{uuid.uuid4().hex}"


def quarter_label(year: int, quarter: int) -> str:
    return f"Q{quarter} {year}"


def fiscal_label(year: int, quarter: int) -> str:
    return f"{year}-Q{quarter}"


def parse_periods(raw: str) -> list[tuple[int, int]]:
    """Parse ``2025Q4,2026Q1`` into exact (FY, Q) pairs; raise ValueError otherwise."""
    periods: list[tuple[int, int]] = []
    for token in raw.split(","):
        token = token.strip()
        match = _PERIOD_TOKEN_RE.match(token)
        if not match:
            raise ValueError(token)
        pair = (int(match.group(1)), int(match.group(2)))
        if pair not in periods:
            periods.append(pair)
    if not periods:
        raise ValueError(raw)
    return periods


def _expand_recent(
    candidates: list[dict[str, Any]], limit: int
) -> tuple[list[tuple[int, int]], int]:
    """Expand recent-N from metadata periods only; never guess a quarter."""
    resolvable: dict[tuple[int, int], str] = {}
    unresolved = 0
    for candidate in candidates:
        fiscal_period = candidate.get("fiscal_period")
        match = _FISCAL_LABEL_RE.fullmatch(fiscal_period or "")
        if not match:
            unresolved += 1
            continue
        key = (int(match.group(1)), int(match.group(2)))
        published = candidate.get("published_date") or ""
        if key not in resolvable or published > resolvable[key]:
            resolvable[key] = published
    ordered = sorted(resolvable, reverse=True)
    return ordered[:limit], unresolved


def _request_timeout(budget: BatchBudget) -> int:
    return max(1, min(DEFAULT_REQUEST_TIMEOUT, int(budget.remaining_seconds())))


def _base_request(
    budget: BatchBudget,
    *,
    ticker: str,
    exchange: str,
    year: int,
    quarter: int,
    provider: str,
) -> dict[str, Any]:
    """One exact-period request whose per-request caps are bounded by the batch."""
    return {
        "schema_version": REQUEST_SCHEMA,
        "request_id": new_request_id(),
        "ticker": ticker,
        "exchange": exchange,
        "fiscal_year": year,
        "fiscal_quarter": quarter,
        "as_of_date": date.today().isoformat(),
        "provider": provider,
        "download_authorized": True,
        "timeout_seconds": _request_timeout(budget),
        "max_body_bytes": max(1, min(MAX_BODY_BYTES, budget.response_bytes_left)),
    }


def _configure_run_logging(cfg: dict, log_file: Path) -> None:
    """Per-run logging for this entry only (no global basicConfig side effects)."""
    log_cfg = cfg.get("logging", {})
    formatter = logging.Formatter(
        log_cfg.get("format", "%(asctime)s [%(levelname)s] %(message)s")
    )
    log.setLevel(getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO))
    log.propagate = False
    for handler in list(log.handlers):
        handler.close()
        log.removeHandler(handler)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    log.addHandler(stream_handler)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    log.addHandler(file_handler)


def _record_entry(
    entries: list[dict[str, Any]],
    *,
    request_id: str,
    company: dict,
    fiscal_period: str | None,
    status: str,
    error_code: str | None = None,
    content_bytes: int | None = None,
    canonical_sha256: str | None = None,
    file: Path | None = None,
    elapsed_seconds: float = 0.0,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "request_id": request_id,
        "ticker": company["ticker"],
        "exchange": company.get("exchange") or "auto",
        "fiscal_period": fiscal_period,
        "status": status,
        "elapsed_seconds": round(elapsed_seconds, 3),
    }
    if error_code:
        entry["error_code"] = error_code
    if content_bytes is not None:
        entry["content_bytes"] = content_bytes
    if canonical_sha256:
        entry["canonical_content_sha256"] = canonical_sha256
    if file is not None:
        entry["file"] = str(file)
    entries.append(entry)
    log.info(
        "request %s status=%s bytes=%s elapsed=%.2fs",
        request_id, status, content_bytes if content_bytes is not None else 0,
        elapsed_seconds,
    )
    return entry


# ──────────────────────────────────────────────
# 本地原件路径 / 计划 / 保存（纯本地，无网络）
# ──────────────────────────────────────────────
def find_english_file(fn: FileNaming, ticker: str, quarter: str, output_dir: Path = None) -> Path:
    """返回该季度英文原文应处的路径（不保证存在）。与保存落盘规则保持一致。"""
    if output_dir:
        q = quarter.replace(" ", "_")
        return output_dir / ticker / f"{ticker}_{q}{fn.english_suffix}{fn.english_ext}"
    return fn.english_path(ticker, quarter)


def plan_action(
    fn: FileNaming,
    ticker: str,
    quarter: str,
    output_dir: Path = None,
    translate_enabled: bool = False,
) -> str:
    """这个季度接下来会做什么：skip / translate / download。--dry-run 与真实跳过判断共用。"""
    english = find_english_file(fn, ticker, quarter, output_dir)
    if not english.exists():
        return "download"
    if translate_enabled and not fn.english_to_bilingual(english).exists():
        return "translate"
    return "skip"


def completed_entry(cfg: dict, english_path: Path, company: dict, quarter: str, url: str = "N/A") -> dict:
    """从已存在的英文原文中读取元信息，构造结果条目（供复用报告使用）。"""
    ticker = company["ticker"]
    entry = {
        "title": f"{company['name_en']} ({ticker}) {quarter} Earnings Call Transcript",
        "quarter": quarter,
        "source": "motley_fool",
        "url": url,
        "char_count": 0,
        "local_file": str(english_path),
    }
    try:
        meta = parse_transcript_header(
            english_path.read_text(encoding="utf-8", errors="ignore"), cfg=cfg
        )
    except OSError:
        meta = {}

    if meta.get("Source"):
        entry["source"] = meta["Source"]
    if entry["url"] == "N/A" and meta.get("URL") and meta["URL"] != "N/A":
        entry["url"] = meta["URL"]
    try:
        entry["char_count"] = int(str(meta.get("Characters", 0)).replace(",", ""))
    except ValueError:
        entry["char_count"] = 0
    return entry


def quarter_from_filename(fn: FileNaming, ticker: str, path: Path) -> str:
    """从文件名反解季度，如 MSFT_Q3_2026_earnings_call.txt → 'Q3 2026'。"""
    stem = path.name[len(ticker) + 1:]
    suffix = fn.english_suffix + fn.english_ext
    if stem.endswith(suffix):
        stem = stem[: -len(suffix)]
    return stem.replace("_", " ")


def save_summary(cfg: dict, fn: FileNaming, companies: list, output_dir: Path = None):
    """按磁盘上的实际文件生成本地语料清单（不按本次运行结果截断）。"""
    transcripts_dir = output_dir or get_path(cfg, "transcripts_dir")
    summary_path = transcripts_dir / "summary.txt"
    sep_char = cfg.get("format", {}).get("separator_char", "=")
    sep_width = cfg.get("format", {}).get("separator_width", 70)
    sep = sep_char * sep_width

    def newest_first(q: str):
        m = re.search(r"Q(\d)\s+(\d{4})", q)
        return (-int(m.group(2)), -int(m.group(1))) if m else (0, 0)

    lines = [
        sep,
        "Earnings Call Transcripts - Summary Report",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        sep, "",
    ]
    total = 0
    for company in companies:
        ticker = company["ticker"]
        quarters = sorted(
            (quarter_from_filename(fn, ticker, p) for p in fn.find_english_files(ticker)),
            key=newest_first,
        )
        total += len(quarters)
        lines.append(f"{company['name_en']} ({company['name_cn']}) [{ticker}]")
        if not quarters:
            lines.append("  [NO TRANSCRIPTS FOUND]")
        else:
            for quarter in quarters:
                entry = completed_entry(
                    cfg, find_english_file(fn, ticker, quarter, output_dir), company, quarter
                )
                lines.append(f"  - {quarter}: {entry['title']}")
                lines.append(f"    Source: {entry['source']} | Chars: {entry['char_count']}")
                lines.append(f"    URL: {entry['url']}")
        lines.append("")
    lines.append(f"Total: {total} transcripts across {len(companies)} companies")
    lines.append(sep)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("Summary saved: %s (%d transcripts)", summary_path, total)


def render_original(cfg: dict, company: dict, transcript: dict) -> str:
    """渲染英文原件全文（与既有 *_earnings_call.txt 头部格式完全一致）。"""
    sep_char = cfg.get("format", {}).get("separator_char", "=")
    sep_width = cfg.get("format", {}).get("separator_width", 70)
    sep = sep_char * sep_width
    header = f"""{sep}
Earnings Call Transcript
Company: {company['name_en']} ({company['name_cn']})
Ticker: {company['ticker']}
Quarter: {transcript.get('quarter', 'N/A')}
Source: {transcript.get('source', 'N/A')}
URL: {transcript.get('url', 'N/A')}
Scraped: {transcript.get('scraped_at', 'N/A')}
Characters: {transcript.get('char_count', 'N/A')}
{sep}

"""
    return header + transcript["content"]


def _existing_status(cfg: dict, existing_text: str, transcript: dict) -> str:
    """已有原件：身份（URL）匹配且正文一致 → reused；否则 → output_conflict。"""
    meta = parse_transcript_header(existing_text, cfg=cfg)
    url = meta.get("URL")
    if url and url not in ("N/A",) and url != transcript.get("url"):
        return "output_conflict"
    if extract_body(existing_text, cfg=cfg).strip() == transcript["content"].strip():
        return "reused"
    return "output_conflict"


def _store_original(
    cfg: dict,
    fn: FileNaming,
    company: dict,
    transcript: dict,
    output_dir: Path,
    budget: BatchBudget,
) -> tuple[str, Path]:
    """保存一份原件：绝不覆盖已有文件；重复内容复用；冲突具名失败。

    新文件先写同目录临时文件再原子替换；任何失败路径都在 finally 清理临时文件。
    """
    ticker = company["ticker"]
    path = find_english_file(fn, ticker, transcript["quarter"], output_dir)
    if path.exists():
        existing = path.read_text(encoding="utf-8", errors="ignore")
        return (_existing_status(cfg, existing, transcript), path)

    payload = render_original(cfg, company, transcript).encode("utf-8")
    budget.take_output(len(payload))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = path.parent / f".{path.name}.tmp-{os.getpid()}"
    try:
        tmp_path.write_bytes(payload)
        if path.exists():  # 并发竞态：已有原件绝不覆盖
            existing = path.read_text(encoding="utf-8", errors="ignore")
            return (_existing_status(cfg, existing, transcript), path)
        os.replace(tmp_path, path)
        tmp_path = None
        return ("fetched", path)
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def translate_after_download(cfg: dict, fn: FileNaming, english_path: Path, skip: bool = False):
    """把已下载的英文原文翻译成 bilingual。仅在显式 --translate 时被调用。"""
    if skip:
        return None

    bilingual_path = fn.english_to_bilingual(english_path)
    if bilingual_path.exists():
        log.info("  Bilingual exists, skipping: %s", bilingual_path.name)
        return bilingual_path

    content = english_path.read_text(encoding="utf-8")
    header_meta = parse_transcript_header(content)
    body = extract_body(content)
    paragraphs = split_paragraphs(body)

    translator = TranslatorFactory.create(cfg, "auto")
    tcache_path = get_path(cfg, "translate_cache")
    tcache = JsonCache(tcache_path)

    log.info("  Translating with %s...", translator.name)
    translated_parts = translate_paragraphs(paragraphs, tcache, translator, cfg, log)
    bilingual_data = build_bilingual_data(header_meta, paragraphs, translated_parts, translator.name)
    bilingual_path.write_text(json.dumps(bilingual_data, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("  Bilingual saved: %s (%d pairs)", bilingual_path.name, len(bilingual_data["pairs"]))

    try:
        from make_interleaved import make_interleaved
        txt_path = make_interleaved(cfg, bilingual_path)
        if txt_path:
            log.info("  Interleaved saved: %s", txt_path.name)
    except Exception as e:
        log.warning("  Interleaved generation failed: %s", e)

    return bilingual_path


# ──────────────────────────────────────────────
# 批次执行
# ──────────────────────────────────────────────
def _maybe_translate(cfg: dict, fn: FileNaming, english_path: Path, translate_enabled: bool) -> None:
    if not translate_enabled:
        return
    try:
        translate_after_download(cfg, fn, english_path, skip=False)
    except Exception as exc:
        log.warning("translation failed for %s: %s", english_path.name, exc)


def _finalize_existing(
    cfg: dict,
    fn: FileNaming,
    company: dict,
    label: str,
    fiscal: str,
    path: Path,
    expected_url: str | None,
    entries: list[dict[str, Any]],
    translate_enabled: bool,
) -> None:
    """已有原件：身份核验后复用，绝不覆盖；身份不符 → 具名 output_conflict。"""
    request_id = new_request_id()
    if expected_url is not None:
        meta = parse_transcript_header(
            path.read_text(encoding="utf-8", errors="ignore"), cfg=cfg
        )
        header_url = meta.get("URL")
        if header_url and header_url not in ("N/A",) and header_url != expected_url:
            print(
                f"error: {company['ticker']} {fiscal}: output_conflict/identity_mismatch",
                file=sys.stderr,
            )
            _record_entry(
                entries, request_id=request_id, company=company, fiscal_period=fiscal,
                status="output_conflict", error_code="identity_mismatch", file=path,
            )
            return
    info = completed_entry(cfg, path, company, label, expected_url or "N/A")
    _record_entry(
        entries, request_id=request_id, company=company, fiscal_period=fiscal,
        status="reused", content_bytes=(info["char_count"] or None), file=path,
    )
    _maybe_translate(cfg, fn, path, translate_enabled)


def _finalize_fetched(
    cfg: dict,
    fn: FileNaming,
    company: dict,
    label: str,
    fiscal: str,
    result: dict[str, Any],
    entries: list[dict[str, Any]],
    translate_enabled: bool,
    transcripts_root: Path,
    budget: BatchBudget,
    request_id: str,
    elapsed: float,
) -> str:
    """Store a fetched result or record its named outcome; returns 'budget' to stop."""
    status = result["status"]
    if status != "fetched":
        print(
            f"error: {company['ticker']} {fiscal}: {status}/{result.get('error_code') or '-'}",
            file=sys.stderr,
        )
        _record_entry(
            entries, request_id=request_id, company=company, fiscal_period=fiscal,
            status=status, error_code=result.get("error_code"), elapsed_seconds=elapsed,
        )
        if status in _PROVIDER_SCOPE_STATUSES:
            return "stop"
        return ""
    transcript = {
        "quarter": label,
        "content": result["content_utf8"],
        "url": result["source_url"],
        "title": result["title"],
        "source": "fmp_api" if result.get("provider") == "fmp" else "motley_fool",
        "char_count": result["content_bytes"],
        "scraped_at": datetime.now().isoformat(),
    }
    try:
        store_status, path = _store_original(
            cfg, fn, company, transcript, transcripts_root, budget
        )
    except BatchBudgetExceeded:
        return "budget"
    if store_status == "output_conflict":
        print(
            f"error: {company['ticker']} {fiscal}: output_conflict/existing_original",
            file=sys.stderr,
        )
    _record_entry(
        entries, request_id=request_id, company=company, fiscal_period=fiscal,
        status=store_status,
        error_code="existing_original_conflict" if store_status == "output_conflict" else None,
        content_bytes=result["content_bytes"],
        canonical_sha256=result.get("canonical_content_sha256"),
        file=path, elapsed_seconds=elapsed,
    )
    if store_status == "fetched":
        _maybe_translate(cfg, fn, path, translate_enabled)
    return ""


def _process_fool_company(
    *,
    cfg: dict,
    fn: FileNaming,
    company: dict,
    args: argparse.Namespace,
    periods: list[tuple[int, int]] | None,
    budget: BatchBudget,
    session_factory: Callable[[], Any],
    provider_settings: ProviderSettings,
    entries: list[dict[str, Any]],
    transcripts_root: Path,
    translate_enabled: bool,
) -> str:
    """Metadata listing → explicit periods → candidate fetch. Returns '' | 'budget' | 'stop'."""
    ticker = company["ticker"]
    exchange = company.get("exchange") or "auto"
    listing_request_id = new_request_id()
    listing_started = time.monotonic()
    listing = list_transcript_candidates(
        ticker=ticker,
        exchange=exchange,
        as_of_date=date.today().isoformat(),
        request_id=listing_request_id,
        timeout_seconds=_request_timeout(budget),
        session_factory=session_factory,
        provider_settings=provider_settings,
    )
    if budget.exhausted:
        return "budget"
    listing_status = listing["status"]
    if listing_status not in ("discovered", "not_found"):
        print(
            f"error: {ticker}: {listing_status}/{listing.get('error_code') or '-'}",
            file=sys.stderr,
        )
        _record_entry(
            entries, request_id=listing_request_id, company=company, fiscal_period=None,
            status=listing_status, error_code=listing.get("error_code"),
            elapsed_seconds=time.monotonic() - listing_started,
        )
        if listing_status in _PROVIDER_SCOPE_STATUSES:
            return "stop"
        return ""

    candidates = listing["candidates"]
    if periods is None:
        resolved, unresolved = _expand_recent(candidates, args.quarters or 1)
        if unresolved:
            log.info(
                "%s: %d candidate(s) without explicit FY/Q skipped", ticker, unresolved
            )
        if not resolved:
            print(
                f"error: {ticker}: period_unresolved: recent quarters require explicit FY/Q metadata",
                file=sys.stderr,
            )
            _record_entry(
                entries, request_id=new_request_id(), company=company, fiscal_period=None,
                status="period_unresolved", error_code="need_explicit_periods",
                elapsed_seconds=time.monotonic() - listing_started,
            )
            return ""
    else:
        resolved = periods

    for year, quarter in resolved:
        if budget.exhausted:
            return "budget"
        label = quarter_label(year, quarter)
        fiscal = fiscal_label(year, quarter)
        period_candidates = [
            c for c in candidates if c["fiscal_period"] == fiscal
        ]
        path = find_english_file(fn, ticker, label, transcripts_root)
        if path.exists():
            expected_url = (
                period_candidates[0]["source_url"] if len(period_candidates) == 1 else None
            )
            _finalize_existing(
                cfg, fn, company, label, fiscal, path, expected_url,
                entries, translate_enabled,
            )
            continue
        if not period_candidates:
            _record_entry(
                entries, request_id=new_request_id(), company=company, fiscal_period=fiscal,
                status="not_found",
            )
            continue
        if len(period_candidates) > 1:
            _record_entry(
                entries, request_id=new_request_id(), company=company, fiscal_period=fiscal,
                status="ambiguous",
            )
            continue

        candidate = period_candidates[0]
        request = _base_request(
            budget, ticker=ticker, exchange=exchange,
            year=year, quarter=quarter, provider="motley_fool",
        )
        request_id = request["request_id"]
        request["schema_version"] = CANDIDATE_FETCH_REQUEST_SCHEMA
        request["candidate"] = {
            "provider_document_id": candidate["provider_document_id"],
            "source_url": candidate["source_url"],
            "published_date": candidate["published_date"],
        }
        started = time.monotonic()
        result = fetch_transcript_candidate(
            request,
            session_factory=session_factory,
            provider_settings=provider_settings,
        )
        elapsed = time.monotonic() - started
        if budget.exhausted:
            return "budget"
        outcome = _finalize_fetched(
            cfg, fn, company, label, fiscal, result, entries,
            translate_enabled, transcripts_root, budget, request_id, elapsed,
        )
        if outcome:
            return outcome
    return ""


def _process_fmp_company(
    *,
    cfg: dict,
    fn: FileNaming,
    company: dict,
    periods: list[tuple[int, int]],
    budget: BatchBudget,
    session_factory: Callable[[], Any],
    provider_settings: ProviderSettings,
    entries: list[dict[str, Any]],
    transcripts_root: Path,
    translate_enabled: bool,
    api_key: str | None,
) -> str:
    """Exact-period FMP fetches; no discovery endpoint exists, so periods must be explicit."""
    ticker = company["ticker"]
    exchange = company.get("exchange") or "auto"
    for year, quarter in periods:
        if budget.exhausted:
            return "budget"
        label = quarter_label(year, quarter)
        fiscal = fiscal_label(year, quarter)
        path = find_english_file(fn, ticker, label, transcripts_root)
        if path.exists():
            _finalize_existing(
                cfg, fn, company, label, fiscal, path, None, entries, translate_enabled
            )
            continue
        request = _base_request(
            budget, ticker=ticker, exchange=exchange,
            year=year, quarter=quarter, provider="fmp",
        )
        request_id = request["request_id"]
        started = time.monotonic()
        result = fetch_transcript(
            request,
            session_factory=session_factory,
            fmp_api_key=api_key,
            provider_settings=provider_settings,
        )
        elapsed = time.monotonic() - started
        if budget.exhausted:
            return "budget"
        outcome = _finalize_fetched(
            cfg, fn, company, label, fiscal, result, entries,
            translate_enabled, transcripts_root, budget, request_id, elapsed,
        )
        if outcome:
            return outcome
    return ""


def _write_manifest(
    transcripts_root: Path,
    args: argparse.Namespace,
    budget: BatchBudget | None,
    entries: list[dict[str, Any]],
    started_at: str,
    periods: list[tuple[int, int]] | None,
) -> None:
    manifest = {
        "manifest_version": "et-batch-manifest/1",
        "started_at": started_at,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "source": args.source,
        "periods": [fiscal_label(y, q) for y, q in periods] if periods else None,
        "recent_quarters": None if periods else (args.quarters or 1),
        "translate": bool(args.translate),
        "budget": budget.report() if budget is not None else None,
        "entries": entries,
    }
    transcripts_root.mkdir(parents=True, exist_ok=True)
    (transcripts_root / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def _report(budget: BatchBudget, entries: list[dict[str, Any]]) -> int:
    print("=" * 70)
    print("BATCH REPORT")
    print("=" * 70)
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
        print(
            f"  {entry['ticker']:6s} {(entry['fiscal_period'] or '-'):10s} "
            f"{entry['status']:20s} bytes={entry.get('content_bytes', 0)}  "
            f"{entry['elapsed_seconds']:.2f}s"
        )
    print("-" * 70)
    print("  " + ("  ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "no work"))
    report = budget.report()
    print(
        f"  budget: requests={report['requests_used']}/{report['max_requests']}  "
        f"response_bytes={report['response_bytes_used']}/{report['max_response_bytes']}  "
        f"output_bytes={report['output_bytes_used']}/{report['max_output_bytes']}"
    )
    if budget.exhausted:
        print(f"  exit: limit_exceeded: {budget.exhausted}")
        code = EXIT_PARTIAL
    elif any(entry["status"] in _ERROR_STATUSES for entry in entries):
        print("  exit: failure")
        code = EXIT_FAILURE
    else:
        print("  exit: ok")
        code = EXIT_OK
    print("=" * 70)
    return code


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────
def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="美股电话会议纪要有限批次采集（transcript_api 薄编排，默认原语言）"
    )
    parser.add_argument("--ticker", help="只处理指定股票代码")
    parser.add_argument(
        "--quarters", type=int, default=None,
        help="legacy 最近 N 期；仅当 metadata discovery 给出明确 FY/Q 时有限展开（未给 --periods 时默认 1）",
    )
    parser.add_argument(
        "--periods", default=None,
        help="明确期间列表（精确 FY/Q），如 2025Q4,2026Q1",
    )
    parser.add_argument("--source", choices=["fool", "fmp"], default="fool")
    parser.add_argument("--api-key", help="FMP API key（缺省读 FMP_API_KEY 环境变量）")
    parser.add_argument("--output", type=Path, default=None,
                        help="覆盖本次原件/日志/缓存/临时/锁全部写入位置")
    parser.add_argument("--list", action="store_true",
                        help="只做 metadata 发现/列表：零正文、零写入")
    parser.add_argument("--dry-run", action="store_true",
                        help="只报告本地计划与 unknown 项：零 HTTP、零写入")
    parser.add_argument("--translate", action="store_true",
                        help="显式启用翻译（默认原语言，不创建翻译器）")
    parser.add_argument(
        "--no-translate", "--disable-translation", dest="no_translate",
        action="store_true",
        help="只保存原文（兼容旧旗标；当前默认即为不翻译）",
    )
    parser.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS,
                        help=f"批次累计 HTTP 请求上限（默认 {DEFAULT_MAX_REQUESTS}）")
    parser.add_argument("--max-seconds", type=float, default=DEFAULT_MAX_SECONDS,
                        help=f"批次总时长上限，正有限秒（默认 {DEFAULT_MAX_SECONDS:g}）")
    parser.add_argument("--max-response-bytes", type=int, default=DEFAULT_MAX_RESPONSE_BYTES,
                        help=f"批次累计响应字节上限（默认 {DEFAULT_MAX_RESPONSE_BYTES}）")
    parser.add_argument("--max-output-bytes", type=int, default=DEFAULT_MAX_OUTPUT_BYTES,
                        help=f"本次新保存原件字节上限（默认 {DEFAULT_MAX_OUTPUT_BYTES}）")
    return parser


def _validate_args(args: argparse.Namespace) -> str | None:
    if args.periods is not None and args.quarters is not None:
        return "--periods 与 --quarters 不能同时使用"
    if args.list and args.dry_run:
        return "--list 与 --dry-run 不能同时使用"
    if args.translate and args.no_translate:
        return "--translate 与 --no-translate 不能同时使用"
    if args.quarters is not None and args.quarters < 1:
        return "--quarters 必须是正整数"
    if args.max_requests < 1:
        return "--max-requests 必须是正整数"
    if not (args.max_seconds > 0 and math.isfinite(args.max_seconds)):
        return "--max-seconds 必须是正有限秒"
    if args.max_response_bytes < 1:
        return "--max-response-bytes 必须是正整数"
    if args.max_output_bytes < 1:
        return "--max-output-bytes 必须是正整数"
    if args.periods:
        try:
            parse_periods(args.periods)
        except ValueError as exc:
            return f"invalid --periods token: {exc.args[0]}"
    return None


def _cfg_with_output(cfg: dict, output_root: Path) -> dict:
    """把本批次所有写入路径（原件/日志/锁/缓存）指到 --output 根下。"""
    cfg = dict(cfg)
    paths = dict(cfg.get("paths") or {})
    paths["transcripts_dir"] = str(output_root)
    paths["logs_dir"] = str(output_root / "logs")
    paths["lock_file"] = str(output_root / ".instance.lock")
    paths["translate_cache"] = str(output_root / ".translate_cache.json")
    paths["cache_file"] = str(output_root / ".cache.json")
    cfg["paths"] = paths
    return cfg


def _run_dry_plan(
    cfg: dict,
    args: argparse.Namespace,
    companies: list[dict],
    periods: list[tuple[int, int]] | None,
    transcripts_root: Path,
    translate_enabled: bool,
) -> int:
    """本地可知的计划 + unknown 项；零 HTTP、零写入、零翻译。"""
    fn = FileNaming(cfg)
    counts = {"download": 0, "skip": 0, "translate": 0, "unknown": 0}
    print("=" * 70)
    print("DRY RUN — 零 HTTP、零写入、零翻译")
    print("=" * 70)
    for company in companies:
        ticker = company["ticker"]
        if periods is None:
            counts["unknown"] += 1
            print(
                f"  [unknown  ] {ticker} "
                f"(recent {args.quarters or 1} quarter(s) need metadata discovery)"
            )
            continue
        for year, quarter in periods:
            label = quarter_label(year, quarter)
            action = plan_action(
                fn, ticker, label, transcripts_root, translate_enabled=translate_enabled
            )
            counts[action] += 1
            print(f"  [{action:9s}] {ticker} {label}")
    print("-" * 70)
    print(
        f"  download: {counts['download']}  skip: {counts['skip']}  "
        f"translate: {counts['translate']}  unknown: {counts['unknown']}"
    )
    print("=" * 70)
    return EXIT_OK


def _run_list(
    args: argparse.Namespace,
    companies: list[dict],
    periods: list[tuple[int, int]] | None,
    session_factory: Callable[[], Any] | None,
    provider_settings: ProviderSettings,
) -> int:
    """metadata-only 列表：零正文、零写入；metadata 请求计入批次额度。"""
    if args.source == "fmp":
        if periods is None:
            print(
                "error: period_unresolved: --source fmp has no metadata listing; pass explicit --periods",
                file=sys.stderr,
            )
        else:
            print(
                "error: candidate_discovery_unavailable: --source fmp has no metadata-only listing",
                file=sys.stderr,
            )
        return EXIT_FAILURE

    budget = BatchBudget(
        args.max_requests, args.max_seconds,
        args.max_response_bytes, args.max_output_bytes,
    )
    base_factory = session_factory or requests.Session
    budget_factory = lambda: _BudgetSession(base_factory(), budget)  # noqa: E731
    failed = False
    today = date.today().isoformat()
    print("=" * 70)
    print("AVAILABLE TRANSCRIPTS (metadata only, no bodies)")
    print("=" * 70)
    for company in companies:
        ticker = company["ticker"]
        listing = list_transcript_candidates(
            ticker=ticker,
            exchange=company.get("exchange") or "auto",
            as_of_date=today,
            request_id=new_request_id(),
            timeout_seconds=_request_timeout(budget),
            session_factory=budget_factory,
            provider_settings=provider_settings,
        )
        if budget.exhausted:
            print(f"  exit: limit_exceeded: {budget.exhausted}")
            return EXIT_PARTIAL
        if listing["status"] not in ("discovered", "not_found"):
            print(
                f"error: {ticker}: {listing['status']}/{listing.get('error_code') or '-'}",
                file=sys.stderr,
            )
            failed = True
            if listing["status"] in _PROVIDER_SCOPE_STATUSES:
                return EXIT_FAILURE
            continue
        candidates = listing["candidates"]
        if periods is None:
            resolved, unresolved = _expand_recent(candidates, args.quarters or 1)
            if unresolved:
                print(
                    f"  [period_unresolved] {ticker}: {unresolved} candidate(s) without explicit FY/Q skipped"
                )
            if not resolved:
                print(
                    f"error: {ticker}: period_unresolved: recent quarters require explicit FY/Q metadata",
                    file=sys.stderr,
                )
                failed = True
                continue
            wanted = {fiscal_label(y, q) for y, q in resolved}
        else:
            wanted = {fiscal_label(y, q) for y, q in periods}
        shown = [c for c in candidates if c["fiscal_period"] in wanted]
        print(f"\n{company['name_en']} ({company['name_cn']}) [{ticker}]")
        if not shown:
            print("  [NONE FOUND]")
        else:
            for candidate in shown:
                print(
                    f"  {candidate['fiscal_period']:10s}  {candidate['published_date']}  "
                    f"{candidate['source_url']}"
                )
    print("=" * 70)
    return EXIT_FAILURE if failed else EXIT_OK


def _run_batch(
    cfg: dict,
    args: argparse.Namespace,
    companies: list[dict],
    periods: list[tuple[int, int]] | None,
    session_factory: Callable[[], Any] | None,
    provider_settings: ProviderSettings,
    translate_enabled: bool,
) -> int:
    if args.source == "fmp" and periods is None:
        print(
            "error: period_unresolved: --source fmp requires explicit --periods "
            "(no metadata discovery for recent-N)",
            file=sys.stderr,
        )
        return EXIT_FAILURE

    transcripts_root = get_path(cfg, "transcripts_dir")
    logs_dir = get_path(cfg, "logs_dir")
    lock_file = get_path(cfg, "lock_file")

    lock = SingleInstanceLock(lock_file)
    ok, holder = lock.acquire()
    if not ok:
        msg = (
            f"已有 scraper/translate 实例在运行：{holder}\n"
            f"并发运行会互相覆盖文件、重复消耗翻译额度。\n"
            f"确认没有其它实例后删除锁文件重试：{lock_file}"
        )
        print(f"\n{msg}\n", file=sys.stderr)
        return EXIT_USAGE

    budget: BatchBudget | None = None
    fn: FileNaming | None = None
    entries: list[dict[str, Any]] = []
    started_at = datetime.now().isoformat(timespec="seconds")
    exit_code = EXIT_FAILURE
    try:
        transcripts_root.mkdir(parents=True, exist_ok=True)
        logs_dir.mkdir(parents=True, exist_ok=True)
        _configure_run_logging(cfg, logs_dir / "scraper.log")
        budget = BatchBudget(
            args.max_requests, args.max_seconds,
            args.max_response_bytes, args.max_output_bytes,
        )
        base_factory = session_factory or requests.Session
        budget_factory = lambda: _BudgetSession(base_factory(), budget)  # noqa: E731
        fn = FileNaming(cfg)
        api_key = args.api_key or os.environ.get("FMP_API_KEY")

        for company in companies:
            if budget.exhausted:
                break
            if args.source == "fool":
                outcome = _process_fool_company(
                    cfg=cfg, fn=fn, company=company, args=args, periods=periods,
                    budget=budget, session_factory=budget_factory,
                    provider_settings=provider_settings, entries=entries,
                    transcripts_root=transcripts_root,
                    translate_enabled=translate_enabled,
                )
            else:
                outcome = _process_fmp_company(
                    cfg=cfg, fn=fn, company=company, periods=periods,
                    budget=budget, session_factory=budget_factory,
                    provider_settings=provider_settings, entries=entries,
                    transcripts_root=transcripts_root,
                    translate_enabled=translate_enabled, api_key=api_key,
                )
            if outcome in ("budget", "stop"):
                break

        save_summary(cfg, fn, companies, transcripts_root)
        exit_code = _report(budget, entries)
    except BatchBudgetExceeded:
        log.warning("batch budget exceeded: %s", budget.exhausted if budget else "unknown")
        if budget is not None and fn is not None:
            try:
                save_summary(cfg, fn, companies, transcripts_root)
            except Exception:
                pass
            exit_code = _report(budget, entries)
    except Exception as exc:
        print(f"error: unexpected_batch_failure: {exc}", file=sys.stderr)
        log.exception("unexpected batch failure")
        exit_code = EXIT_FAILURE
    finally:
        try:
            _write_manifest(transcripts_root, args, budget, entries, started_at, periods)
        except Exception as exc:
            print(f"error: manifest_write_failed: {exc}", file=sys.stderr)
        for handler in list(log.handlers):
            handler.close()
            log.removeHandler(handler)
        lock.release()
    return exit_code


def main(
    argv: list[str] | None = None,
    *,
    _session_factory: Callable[[], Any] | None = None,
    _provider_settings: ProviderSettings = DEFAULT_PROVIDER_SETTINGS,
    _companies: list[dict] | None = None,
) -> int:
    """CLI entry; private injection points are for offline tests only."""
    parser = build_argument_parser()
    args = parser.parse_args(argv)

    error = _validate_args(args)
    if error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_USAGE
    try:
        periods = parse_periods(args.periods) if args.periods else None
    except ValueError as exc:
        print(f"error: invalid --periods token: {exc.args[0]}", file=sys.stderr)
        return EXIT_USAGE
    translate_enabled = bool(args.translate)

    try:
        cfg = load_config()
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    if args.output is not None:
        cfg = _cfg_with_output(cfg, args.output.resolve())
    transcripts_root = get_path(cfg, "transcripts_dir")

    if _companies is not None:
        companies = _companies
    else:
        companies = load_companies(cfg, filter_ticker=args.ticker)
    if not companies:
        print("error: no companies found in companies.txt", file=sys.stderr)
        return EXIT_FAILURE

    if args.dry_run:
        return _run_dry_plan(
            cfg, args, companies, periods, transcripts_root, translate_enabled
        )
    if args.list:
        return _run_list(
            args, companies, periods, _session_factory, _provider_settings
        )
    return _run_batch(
        cfg, args, companies, periods, _session_factory, _provider_settings,
        translate_enabled,
    )


if __name__ == "__main__":
    raise SystemExit(main())

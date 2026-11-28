#!/usr/bin/env python3
"""美股电话会议纪要 — 有限批次采集入口（现代 transcript_api 的薄编排层）。

职责边界：
- 所有 HTTP 经 transcript_api（流式 byte/deadline/host 约束、ProviderSettings、
  具名错误）；本文件不保留任何直连 Session 的第二套网络实现。
- ``--periods 2025Q4,2026Q1`` 是新的明确期间列表（精确 FY/Q）；legacy
  ``--quarters N`` 只在 metadata discovery 给出明确 FY/Q 后有限展开，
  无法唯一确定则 ``period_unresolved``、零正文抓取、不猜 Q4。
- 批次限额 ``--max-requests/--max-seconds/--max-response-bytes/
  --max-output-bytes`` 在每次正式采集前核对。``--max-seconds`` 是**硬截止**：
  批次自创建起共用同一 deadline，逐文件只给剩余额度、不重置，到期在统一
  清理宽限内终止内部采集 worker（retrieval_runtime/retrieval_worker）；
  触发后返回具名 partial/failure 与已完成文档，不回滚、不覆盖已有原件。
  该限额只约束网络采集，显式 ``--translate`` 的翻译耗时不在其中。
- 默认原语言保存；只有显式 ``--translate`` 才初始化翻译器。
- ``--list`` 只做 metadata（零正文、零写入）；``--dry-run`` 零 HTTP 零写入；
  两者在目录/日志/锁初始化之前结束。
- ``--output`` 覆盖本次原件/日志/缓存/临时/锁全部写入位置。

退出码：0 成功；1 具名失败；2 用法/锁冲突；3 限额 partial。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

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
from retrieval_budget import (
    BatchBudget,
    BatchBudgetExceeded,
    BudgetSession as _BudgetSession,  # noqa: F401 - compatibility for existing budget callers
)
from retrieval_diagnostics import exception_trace, log_exception_trace
from retrieval_runtime import RetrievalOutcome, run_retrieval
from runtime_logging import EntryDiagnostics, configure_run_logging, record_local_failure
from runtime_scope import redact_text
import transcript_feedback
from transcript_feedback import BatchJournal
from transcript_api import (
    CANDIDATE_FETCH_REQUEST_SCHEMA,
    DEFAULT_PROVIDER_SETTINGS,
    MAX_BODY_BYTES,
    REQUEST_SCHEMA,
    ProviderSettings,
)
from transcript_artifact import (
    OUTCOME_IDENTITY_MISSING,
    OUTCOME_LEGACY_UNVERIFIED,
    OUTCOME_VERIFIED,
    build_download_receipt,
    parse_quarter_label,
    receipt_json_bytes,
    receipt_path,
    verify_stored_original,
    write_atomic,
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


@dataclass(frozen=True)
class _RetrievalContext:
    """Supervisor wiring for one run; the launcher fields are a private test seam."""

    launcher: str | None = None
    launcher_spec: dict[str, Any] | None = None
    temp_root: Path | None = None
    api_key: str | None = None
    diagnostic_identity: tuple[str, str] | None = None


def _supervise(
    budget: BatchBudget,
    ctx: _RetrievalContext,
    provider_settings: ProviderSettings,
    operation: str,
    payload: dict[str, Any],
) -> RetrievalOutcome | None:
    """Budget-checked hard-deadline retrieval; ``None`` means stop the batch.

    The shared batch quota is checked before any worker is spawned, the
    worker receives only the remaining float seconds/request/byte budget, and
    its reported usage is folded back. A deadline or an unknown usage marks
    the batch terminal without restoring any budget.
    """
    try:
        budget.check_request()
    except BatchBudgetExceeded:
        return None
    outcome = run_retrieval(
        operation,
        payload,
        remaining_seconds=budget.remaining_seconds(),
        requests_left=budget.requests_left,
        response_bytes_left=budget.response_bytes_left,
        provider_settings=provider_settings,
        fmp_api_key=ctx.api_key,
        launcher=ctx.launcher,
        launcher_spec=ctx.launcher_spec,
        temp_root=ctx.temp_root,
        diagnostic_identity=ctx.diagnostic_identity,
    )
    budget.apply_usage(outcome.usage)
    if outcome.reason == "deadline":
        budget.stop_with("batch_deadline")
    if outcome.usage is None:
        budget.mark_usage_unknown()
    return outcome


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


def _configure_run_logging(cfg: dict, log_file: Path, runtime: EntryDiagnostics | None) -> None:
    """Capture all ET components in the batch's existing file log."""
    configure_run_logging(cfg.get("logging", {}), log_file, runtime=runtime)


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
# 逐项观测：每个工作项的原生身份、阶段、结果与计量
# ──────────────────────────────────────────────
#: 一条原因为「解析阶段失败」而不是「请求阶段失败」时的原因码。
_PARSE_STAGE_REASONS = frozenset(
    {
        "provider_response",
        "provider_identity_or_host",
        "candidate_effective_url_or_mime",
        "provider_credentials_leaked",
    }
)


def _item_request(
    request_id: str, company: dict, fiscal_period: str | None
) -> dict[str, Any]:
    """The native request facts of one item, for the feedback detail.

    Only the stable identity fields: no path, no credential, no raw argv.
    """
    request: dict[str, Any] = {
        "request_id": request_id,
        "ticker": company["ticker"],
        "exchange": company.get("exchange") or "auto",
    }
    if fiscal_period:
        year, _, quarter = fiscal_period.partition("-Q")
        if year.isdigit() and quarter.isdigit():
            request["fiscal_year"] = int(year)
            request["fiscal_quarter"] = int(quarter)
    return request


def _stage_for(status: str, error_code: str | None) -> str:
    """这一项结束在哪个真实阶段：抓取、解析还是 TXT 保存。"""
    if status in ("fetched", "reused", "legacy_unverified", "output_conflict", "unknown"):
        return "save"
    if error_code in _PARSE_STAGE_REASONS:
        return "parse"
    return "fetch"


def _could_have_dispatched(diagnostics: dict[str, Any] | None) -> bool:
    return (diagnostics or {}).get("provider_activity") != "not_started"


def _observe_fetched(
    journal: BatchJournal | None,
    *,
    entry: dict[str, Any],
    operation: str,
    request: dict[str, Any] | None,
    result: dict[str, Any],
    usage: dict[str, Any] | None,
    worker: dict[str, Any] | None,
) -> None:
    """一个真正到过提供方的项：它的原阶段、原结果与原计量都在这里。"""
    if journal is None:
        return
    status = entry.get("status")
    error_code = entry.get("error_code")
    _observe_item(
        journal,
        entry=entry,
        operation=operation,
        request=request or {},
        result=result,
        dispatched=_could_have_dispatched(worker),
        usage=usage,
        worker=worker,
        stage=_stage_for(str(status), error_code),
    )


def _observe_interrupted(
    journal: BatchJournal | None,
    *,
    entry: dict[str, Any],
    operation: str,
    request: dict[str, Any] | None,
    result: dict[str, Any],
    usage: dict[str, Any] | None,
    worker: dict[str, Any] | None,
    stopped_by: str,
) -> None:
    """一个已经派发、但没有走完的项：用量未知，绝不用零代替。"""
    if journal is None:
        return
    _observe_item(
        journal,
        entry=entry,
        operation=operation,
        request=request or {},
        result=result,
        dispatched=_could_have_dispatched(worker),
        usage=usage,
        worker=worker,
        stage=_stage_for(str(entry.get("status")), entry.get("error_code")),
        stopped_by=stopped_by,
        ledger_entry=False,
        branch="interrupted",
    )


def _observe_not_dispatched(
    journal: BatchJournal | None,
    *,
    company: dict,
    fiscal: str | None,
    request_id: str,
    reason: str,
) -> None:
    """一个明确没有派发的项：计量已知为零请求、零字节，费用不适用。"""
    if journal is None:
        return
    request = _item_request(request_id, company, fiscal)
    _observe_item(
        journal,
        entry={
            "request_id": request_id,
            "ticker": company["ticker"],
            "exchange": company.get("exchange") or "auto",
            "fiscal_period": fiscal,
            "status": "not_dispatched",
            "error_code": reason,
            "elapsed_seconds": 0.0,
        },
        operation="fetch",
        request=request,
        result={"status": "not_dispatched", "error_code": reason},
        dispatched=False,
        usage={"requests_used": 0, "response_bytes_used": 0, "exhausted": None},
        worker=None,
        stage="fetch",
        ledger_entry=False,
        stopped_by=reason,
        branch="not-dispatched",
    )


def _write_batch_outcome(
    journal: BatchJournal | None,
    *,
    mode: str,
    source: str,
    periods: list[tuple[int, int]] | None,
    companies: int,
    exit_code: int,
    budget: BatchBudget | None,
    manifest: dict[str, Any] | None,
    publication_failure: str | None = None,
    execution_failure: list[dict] | None = None,
) -> None:
    """写下一批的整体结果：真实退出码、原 manifest 引用与计量。"""
    if journal is None:
        return
    journal.note_run(
        mode=mode,
        source=source,
        periods=[fiscal_label(year, quarter) for year, quarter in (periods or [])],
        companies=companies,
        manifest=manifest,
        budget=budget.report() if budget is not None else None,
        exhausted=budget.exhausted if budget is not None else None,
        publication_failure=publication_failure,
        execution_failure=execution_failure,
    )
    journal.summary(exit_code)


def _observe_local(
    journal: BatchJournal | None,
    entry: dict[str, Any],
    *,
    company: dict,
    stage: str,
) -> None:
    """一个只在本机判定的项：没有发出任何请求，计量已知为零。"""
    if journal is None:
        return
    request = _item_request(entry["request_id"], company, entry.get("fiscal_period"))
    _observe_item(
        journal,
        entry=entry,
        operation="local",
        request=request,
        result={"status": entry["status"], "error_code": entry.get("error_code")},
        dispatched=False,
        usage={"requests_used": 0, "response_bytes_used": 0, "exhausted": None},
        worker=None,
        stage=stage,
    )


def _observe_item(
    journal: BatchJournal | None,
    *,
    entry: dict[str, Any],
    operation: str,
    request: dict[str, Any],
    result: dict[str, Any],
    dispatched: bool,
    usage: dict[str, Any] | None,
    worker: dict[str, Any] | None,
    stage: str,
    ledger_entry: bool = True,
    stopped_by: str | None = None,
    answer_bytes: int | None = None,
    candidate_count: int | None = None,
    branch: str = "batch-item",
) -> None:
    """把一个工作项的原生终态写入反馈旁路。

    这里只写旁路：业务 stdout、退出码、manifest 与原件一个字节都不变。
    路径只留文件名，完整路径仍在本仓的 manifest 里。
    """
    if journal is None:
        return
    facts: dict[str, Any] = {
        "request_id": entry.get("request_id"),
        "ticker": entry.get("ticker"),
        "exchange": entry.get("exchange"),
        "fiscal_period": entry.get("fiscal_period"),
        "operation": operation,
        "stage": stage,
        "dispatched": dispatched,
        "status": entry.get("status"),
        "error_code": entry.get("error_code"),
        "ledger_entry": ledger_entry,
        "stopped_by": stopped_by,
        "answer_bytes": answer_bytes,
        "content_bytes": entry.get("content_bytes"),
        "canonical_sha256": entry.get("canonical_content_sha256"),
        "file": Path(entry["file"]).name if entry.get("file") else None,
        "candidate_count": candidate_count,
        "elapsed_seconds": entry.get("elapsed_seconds"),
    }
    journal.complete(
        operation=operation,
        request=request,
        result=result,
        usage=usage,
        worker=worker,
        item=facts,
        branch=branch,
    )


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


def _download_receipt_bytes(
    cfg: dict, company: dict, transcript: dict, text: str, payload: bytes
) -> bytes:
    """新原件的 sidecar 收据字节；季度不可解析时跳过并具名 log（原件不受影响）。"""
    period = parse_quarter_label(transcript.get("quarter", ""))
    if period is None:
        log.warning(
            "receipt_write_skipped: unparseable quarter %r for %s",
            transcript.get("quarter"),
            company["ticker"],
        )
        return b""
    receipt = build_download_receipt(
        ticker=company["ticker"],
        fiscal_year=period[0],
        fiscal_quarter=period[1],
        source=transcript.get("source"),
        url=transcript.get("url"),
        obtained_at=datetime.now().isoformat(timespec="seconds"),
        text=text,
        payload=payload,
        cfg=cfg,
    )
    return receipt_json_bytes(receipt)


def _store_original(
    cfg: dict,
    fn: FileNaming,
    company: dict,
    transcript: dict,
    output_dir: Path,
    budget: BatchBudget,
) -> tuple[str, Path]:
    """保存一份原件：绝不覆盖已有文件；重复内容复用；冲突具名失败。

    新文件先写同目录临时文件再原子替换，落盘成功后附 ``et-local-text-receipt/1``
    收据（同目录 sidecar，同样原子）；收据字节计入输出额度，任何失败路径都在
    finally 清理临时文件，原件不丢。
    """
    ticker = company["ticker"]
    path = find_english_file(fn, ticker, transcript["quarter"], output_dir)
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ("output_conflict", path)
        return (_existing_status(cfg, existing, transcript), path)

    text = render_original(cfg, company, transcript)
    payload = text.encode("utf-8")
    receipt_bytes = _download_receipt_bytes(cfg, company, transcript, text, payload)
    budget.take_output(len(payload) + len(receipt_bytes))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = path.parent / f".{path.name}.tmp-{os.getpid()}"
    try:
        tmp_path.write_bytes(payload)
        if path.exists():  # 并发竞态：已有原件绝不覆盖
            try:
                existing = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                return ("output_conflict", path)
            return (_existing_status(cfg, existing, transcript), path)
        os.replace(tmp_path, path)
        tmp_path = None
        if receipt_bytes:
            try:
                write_atomic(receipt_path(path), receipt_bytes)
            except OSError as exc:
                log_exception_trace(log, logging.WARNING, "receipt_write_failed", exc)
        return ("fetched", path)
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError as error:
                log_exception_trace(log, logging.WARNING, "partial_original_cleanup_failed", error)


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
    except Exception as error:
        log_exception_trace(log, logging.WARNING, "interleaved_generation_failed", error)

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
        log_exception_trace(log, logging.WARNING, "translation_after_download_failed", exc)


_ENTRY_STATUS_FOR_OUTCOME = {
    OUTCOME_VERIFIED: "reused",
    OUTCOME_LEGACY_UNVERIFIED: "legacy_unverified",
    OUTCOME_IDENTITY_MISSING: "unknown",
}


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
    journal: BatchJournal | None = None,
) -> None:
    """已有原件：验证（身份/期间/字节/收据）后复用，绝不覆盖。

    - 头部或收据与期望矛盾、文件损坏、收据实际绑定不匹配 → 具名
      ``output_conflict/<code>`` 失败，原件原样保留；
    - 坏非权威旁注只记录diagnostics，不更改原件或升级verified；
    - download 收据逐项吻合 → ``reused``（verified）；无收据但头部可证明
      身份+期间且正文非空 → ``legacy_unverified``；
    - 证明不了身份/期间 → ``unknown``/``identity_missing``（非 verified reused）。
    ``content_bytes`` 一律为实算正文 UTF-8 字节，头部 ``Characters`` 只作诊断。
    """
    request_id = new_request_id()
    verdict = verify_stored_original(
        path,
        ticker=company["ticker"],
        quarter=label,
        cfg=cfg,
        expected_url=expected_url,
    )
    status = _ENTRY_STATUS_FOR_OUTCOME.get(verdict.outcome, "output_conflict")
    if verdict.outcome in (OUTCOME_VERIFIED, OUTCOME_LEGACY_UNVERIFIED):
        entry = _record_entry(
            entries,
            request_id=request_id,
            company=company,
            fiscal_period=fiscal,
            status=status,
            content_bytes=verdict.body_bytes,
            file=path,
        )
        if verdict.diagnostics:
            entry["diagnostics"] = list(verdict.diagnostics)
            log.warning("local receipt observation unavailable: %s", ",".join(verdict.diagnostics))
        _observe_local(journal, entry, company=company, stage="save")
        _maybe_translate(cfg, fn, path, translate_enabled)
        return
    if verdict.outcome == OUTCOME_IDENTITY_MISSING:
        print(
            f"warning: {company['ticker']} {fiscal}: unknown/identity_missing",
            file=sys.stderr,
        )
    else:
        print(
            f"error: {company['ticker']} {fiscal}: output_conflict/{verdict.error_code}",
            file=sys.stderr,
        )
    entry = _record_entry(
        entries,
        request_id=request_id,
        company=company,
        fiscal_period=fiscal,
        status=status,
        error_code=verdict.error_code,
        content_bytes=verdict.body_bytes,
        file=path,
    )
    _observe_local(journal, entry, company=company, stage="save")


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
    journal: BatchJournal | None = None,
    request: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
    worker: dict[str, Any] | None = None,
    operation: str = "fetch",
) -> str:
    """Store a fetched result or record its named outcome; returns 'budget' to stop."""
    if (
        operation == "fetch"
        and isinstance(request, dict)
        and request.get("schema_version") == CANDIDATE_FETCH_REQUEST_SCHEMA
    ):
        operation = "fetch-candidate"
    status = result["status"]
    if status != "fetched":
        print(
            f"error: {company['ticker']} {fiscal}: {status}/{result.get('error_code') or '-'}",
            file=sys.stderr,
        )
        entry = _record_entry(
            entries, request_id=request_id, company=company, fiscal_period=fiscal,
            status=status, error_code=result.get("error_code"), elapsed_seconds=elapsed,
        )
        _observe_fetched(
            journal, entry=entry, operation=operation, request=request, result=result,
            usage=usage, worker=worker,
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
    entry = _record_entry(
        entries, request_id=request_id, company=company, fiscal_period=fiscal,
        status=store_status,
        error_code="existing_original_conflict" if store_status == "output_conflict" else None,
        content_bytes=result["content_bytes"],
        canonical_sha256=result.get("canonical_content_sha256"),
        file=path, elapsed_seconds=elapsed,
    )
    _observe_fetched(
        journal, entry=entry, operation=operation, request=request, result=result,
        usage=usage, worker=worker,
    )
    if store_status == "fetched":
        _maybe_translate(cfg, fn, path, translate_enabled)
    return ""



def _interrupted_result(outcome: RetrievalOutcome) -> dict[str, Any]:
    """The named outcome of an attempt that was dispatched but never finished.

    The batch stopped on a limit while this attempt was in flight: the
    provider may already have been asked, and the usage of that attempt is
    unknown unless the worker reported it.
    """
    if outcome.reason == "deadline":
        return {"status": "deadline_exceeded", "error_code": "provider_deadline"}
    result = outcome.result
    if isinstance(result, dict) and result.get("status"):
        return {
            "status": result["status"],
            "error_code": result.get("error_code"),
        }
    return {"status": "provider_error", "error_code": "retrieval_worker_failure"}
def _process_fool_company(
    *,
    cfg: dict,
    fn: FileNaming,
    company: dict,
    args: argparse.Namespace,
    periods: list[tuple[int, int]] | None,
    budget: BatchBudget,
    ctx: _RetrievalContext,
    provider_settings: ProviderSettings,
    entries: list[dict[str, Any]],
    transcripts_root: Path,
    translate_enabled: bool,
    journal: BatchJournal | None = None,
) -> str:
    """Metadata listing → explicit periods → candidate fetch. Returns '' | 'budget' | 'stop'."""
    ticker = company["ticker"]
    exchange = company.get("exchange") or "auto"
    listing_request_id = new_request_id()
    listing_started = time.monotonic()
    listing_request = {
        "request_id": listing_request_id,
        "ticker": ticker,
        "exchange": exchange,
        "as_of_date": date.today().isoformat(),
        "timeout_seconds": _request_timeout(budget),
        "download_authorized": True,
    }
    if journal is not None:
        journal.plan({"ticker": ticker, "fiscal_period": None})
        journal.begin(operation="list", request=listing_request)
    sup = _supervise(budget, ctx, provider_settings, "list", listing_request)
    if sup is None or budget.exhausted:
        if journal is not None:
            _observe_not_dispatched(
                journal, company=company, fiscal=None, request_id=listing_request_id,
                reason=budget.exhausted or "request_limit",
            )
        return "budget"
    if sup.result is None:
        print(
            f"error: {ticker}: provider_error/retrieval_worker_failure",
            file=sys.stderr,
        )
        entry = _record_entry(
            entries, request_id=listing_request_id, company=company, fiscal_period=None,
            status="provider_error", error_code="retrieval_worker_failure",
            elapsed_seconds=time.monotonic() - listing_started,
        )
        _observe_fetched(
            journal, entry=entry, operation="list", request=listing_request,
            result={"status": "provider_error", "error_code": "retrieval_worker_failure"},
            usage=sup.usage, worker=sup.diagnostics,
        )
        return "stop"
    listing = sup.result
    listing_status = listing["status"]
    listing_failed = listing_status not in ("discovered", "not_found")
    if listing_failed:
        print(
            f"error: {ticker}: {listing_status}/{listing.get('error_code') or '-'}",
            file=sys.stderr,
        )
        entry = _record_entry(
            entries, request_id=listing_request_id, company=company, fiscal_period=None,
            status=listing_status, error_code=listing.get("error_code"),
            elapsed_seconds=time.monotonic() - listing_started,
        )
    else:
        entry = None
    if journal is not None:
        _observe_item(
            journal,
            entry={
                "request_id": listing_request_id,
                "ticker": ticker,
                "exchange": exchange,
                "fiscal_period": None,
                "status": listing_status,
                "error_code": listing.get("error_code"),
                "elapsed_seconds": round(time.monotonic() - listing_started, 3),
            },
            operation="list",
            request=listing_request,
            result={"status": listing_status, "error_code": listing.get("error_code")},
            dispatched=_could_have_dispatched(sup.diagnostics),
            usage=sup.usage,
            worker=sup.diagnostics,
            stage="discovery",
            ledger_entry=listing_failed,
            candidate_count=len(listing.get("candidates", [])),
        )
    if listing_failed:
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
            entry = _record_entry(
                entries, request_id=new_request_id(), company=company, fiscal_period=None,
                status="period_unresolved", error_code="need_explicit_periods",
                elapsed_seconds=time.monotonic() - listing_started,
            )
            _observe_local(journal, entry, company=company, stage="discovery")
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
                entries, translate_enabled, journal=journal,
            )
            continue
        if not period_candidates:
            entry = _record_entry(
                entries, request_id=new_request_id(), company=company, fiscal_period=fiscal,
                status="not_found",
            )
            _observe_local(journal, entry, company=company, stage="discovery")
            continue
        if len(period_candidates) > 1:
            entry = _record_entry(
                entries, request_id=new_request_id(), company=company, fiscal_period=fiscal,
                status="ambiguous",
            )
            _observe_local(journal, entry, company=company, stage="discovery")
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
        if journal is not None:
            journal.begin(operation="fetch-candidate", request=request)
        sup = _supervise(
            budget, ctx, provider_settings, "fetch-candidate", request
        )
        elapsed = time.monotonic() - started
        if sup is None or budget.exhausted:
            if journal is not None:
                if sup is None:
                    _observe_not_dispatched(
                        journal, company=company, fiscal=fiscal, request_id=request_id,
                        reason=budget.exhausted or "request_limit",
                    )
                else:
                    _observe_interrupted(
                        journal,
                        entry={
                            "request_id": request_id,
                            "ticker": ticker,
                            "exchange": exchange,
                            "fiscal_period": fiscal,
                            "elapsed_seconds": round(elapsed, 3),
                            **_interrupted_result(sup),
                        },
                        operation="fetch-candidate",
                        request=request,
                        result=_interrupted_result(sup),
                        usage=sup.usage,
                        worker=sup.diagnostics,
                        stopped_by=budget.exhausted,
                    )
            return "budget"
        if sup.result is None:
            _finalize_fetched(
                cfg, fn, company, label, fiscal,
                {"status": "provider_error", "error_code": "retrieval_worker_failure"},
                entries, translate_enabled, transcripts_root, budget, request_id, elapsed,
                journal=journal, request=request, usage=sup.usage, worker=sup.diagnostics,
            )
            return "stop"
        outcome = _finalize_fetched(
            cfg, fn, company, label, fiscal, sup.result, entries,
            translate_enabled, transcripts_root, budget, request_id, elapsed,
            journal=journal, request=request, usage=sup.usage, worker=sup.diagnostics,
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
    ctx: _RetrievalContext,
    provider_settings: ProviderSettings,
    entries: list[dict[str, Any]],
    transcripts_root: Path,
    translate_enabled: bool,
    journal: BatchJournal | None = None,
) -> str:
    """Exact-period FMP fetches; no discovery endpoint exists, so periods must be explicit."""
    ticker = company["ticker"]
    exchange = company.get("exchange") or "auto"
    for year, quarter in periods:
        if budget.exhausted:
            return "budget"
        label = quarter_label(year, quarter)
        fiscal = fiscal_label(year, quarter)
        if journal is not None:
            journal.plan({"ticker": ticker, "fiscal_period": fiscal})
        path = find_english_file(fn, ticker, label, transcripts_root)
        if path.exists():
            _finalize_existing(
                cfg, fn, company, label, fiscal, path, None, entries, translate_enabled,
                journal=journal,
            )
            continue
        request = _base_request(
            budget, ticker=ticker, exchange=exchange,
            year=year, quarter=quarter, provider="fmp",
        )
        request_id = request["request_id"]
        started = time.monotonic()
        if journal is not None:
            journal.begin(operation="fetch", request=request)
        sup = _supervise(budget, ctx, provider_settings, "fetch", request)
        elapsed = time.monotonic() - started
        if sup is None or budget.exhausted:
            if journal is not None:
                if sup is None:
                    _observe_not_dispatched(
                        journal, company=company, fiscal=fiscal, request_id=request_id,
                        reason=budget.exhausted or "request_limit",
                    )
                else:
                    _observe_interrupted(
                        journal,
                        entry={
                            "request_id": request_id,
                            "ticker": ticker,
                            "exchange": exchange,
                            "fiscal_period": fiscal,
                            "elapsed_seconds": round(elapsed, 3),
                            **_interrupted_result(sup),
                        },
                        operation="fetch",
                        request=request,
                        result=_interrupted_result(sup),
                        usage=sup.usage,
                        worker=sup.diagnostics,
                        stopped_by=budget.exhausted,
                    )
            return "budget"
        if sup.result is None:
            _finalize_fetched(
                cfg, fn, company, label, fiscal,
                {"status": "provider_error", "error_code": "retrieval_worker_failure"},
                entries, translate_enabled, transcripts_root, budget, request_id, elapsed,
                journal=journal, request=request, usage=sup.usage, worker=sup.diagnostics,
            )
            return "stop"
        outcome = _finalize_fetched(
            cfg, fn, company, label, fiscal, sup.result, entries,
            translate_enabled, transcripts_root, budget, request_id, elapsed,
            journal=journal, request=request, usage=sup.usage, worker=sup.diagnostics,
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
) -> dict[str, Any]:
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
    payload = json.dumps(manifest, ensure_ascii=False, indent=1).replace("\n", os.linesep).encode("utf-8")
    write_atomic(transcripts_root / MANIFEST_NAME, payload)
    return {"reference": MANIFEST_NAME, "sha256": hashlib.sha256(payload).hexdigest(), "entries": len(entries)}


def _report(
    budget: BatchBudget, entries: list[dict[str, Any]], *, publication_failed: bool = False,
) -> int:
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
    if report["usage_unknown"]:
        print(
            "  budget: requests=unknown  response_bytes=unknown  "
            f"output_bytes={report['output_bytes_used']}/{report['max_output_bytes']}  "
            "(worker ended without a usage report)"
        )
    else:
        print(
            f"  budget: requests={report['requests_used']}/{report['max_requests']}  "
            f"response_bytes={report['response_bytes_used']}/{report['max_response_bytes']}  "
            f"output_bytes={report['output_bytes_used']}/{report['max_output_bytes']}"
        )
    if publication_failed:
        print("  exit: failure: manifest_write_failed")
        code = EXIT_FAILURE
    elif budget.exhausted:
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
def build_argument_parser() -> transcript_feedback.RefusalCapturingParser:
    parser = transcript_feedback.RefusalCapturingParser(
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
                        help=f"批次采集总时长上限（硬截止，正有限秒；只约束网络采集，"
                             f"不含显式 --translate 的翻译耗时；默认 {DEFAULT_MAX_SECONDS:g}）")
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
    ctx: _RetrievalContext,
    provider_settings: ProviderSettings,
    journal: BatchJournal | None = None,
) -> int:
    """metadata-only 列表：零正文、零写入；metadata 请求计入批次额度。"""
    if args.source == "fmp":
        if periods is None:
            print(
                "error: period_unresolved: --source fmp has no metadata listing; pass explicit --periods",
                file=sys.stderr,
            )
            if journal is not None:
                journal.refusal(
                    entry="scraper",
                    stage="listing-capability",
                    refusal_code="period_unresolved",
                    location="scraper.py#_run_list",
                    argument="--source",
                )
        else:
            print(
                "error: candidate_discovery_unavailable: --source fmp has no metadata-only listing",
                file=sys.stderr,
            )
            if journal is not None:
                journal.refusal(
                    entry="scraper",
                    stage="listing-capability",
                    refusal_code="candidate_discovery_unavailable",
                    location="scraper.py#_run_list",
                    argument="--source",
                )
        return EXIT_FAILURE

    budget = BatchBudget(
        args.max_requests, args.max_seconds,
        args.max_response_bytes, args.max_output_bytes,
    )
    failed = False
    today = date.today().isoformat()
    print("=" * 70)
    print("AVAILABLE TRANSCRIPTS (metadata only, no bodies)")
    print("=" * 70)
    for company in companies:
        ticker = company["ticker"]
        listing_request_id = new_request_id()
        listing_request = {
            "request_id": listing_request_id,
            "ticker": ticker,
            "exchange": company.get("exchange") or "auto",
            "as_of_date": today,
            "timeout_seconds": _request_timeout(budget),
            "download_authorized": True,
        }
        if journal is not None:
            journal.plan({"ticker": ticker, "fiscal_period": None})
            journal.begin(operation="list", request=listing_request)
        sup = _supervise(
            budget, ctx, provider_settings, "list", listing_request
        )
        if sup is None or budget.exhausted:
            print(f"  exit: limit_exceeded: {budget.exhausted}")
            if journal is not None:
                _observe_not_dispatched(
                    journal, company=company, fiscal=None, request_id=listing_request_id,
                    reason=budget.exhausted or "request_limit",
                )
                _write_batch_outcome(
                    journal, mode="list", source=args.source, periods=periods,
                    companies=len(companies), exit_code=EXIT_PARTIAL, budget=budget,
                    manifest=None,
                )
            return EXIT_PARTIAL
        if sup.result is None:
            print(
                f"error: {ticker}: provider_error/retrieval_worker_failure",
                file=sys.stderr,
            )
            if journal is not None:
                _observe_item(
                    journal,
                    entry={
                        "request_id": listing_request_id,
                        "ticker": ticker,
                        "exchange": listing_request["exchange"],
                        "fiscal_period": None,
                        "status": "provider_error",
                        "error_code": "retrieval_worker_failure",
                        "elapsed_seconds": 0.0,
                    },
                    operation="list",
                    request=listing_request,
                    result={
                        "status": "provider_error",
                        "error_code": "retrieval_worker_failure",
                    },
                    dispatched=_could_have_dispatched(sup.diagnostics),
                    usage=sup.usage,
                    worker=sup.diagnostics,
                    stage="discovery",
                    ledger_entry=False,
                )
                _write_batch_outcome(
                    journal, mode="list", source=args.source, periods=periods,
                    companies=len(companies), exit_code=EXIT_FAILURE, budget=budget,
                    manifest=None,
                )
            return EXIT_FAILURE  # unknown usage stops the listing
        listing = sup.result
        listing_status = listing["status"]
        listing_failed = listing_status not in ("discovered", "not_found")
        if journal is not None:
            _observe_item(
                journal,
                entry={
                    "request_id": listing_request_id,
                    "ticker": ticker,
                    "exchange": listing_request["exchange"],
                    "fiscal_period": None,
                    "status": listing_status,
                    "error_code": listing.get("error_code"),
                    "elapsed_seconds": 0.0,
                },
                operation="list",
                request=listing_request,
                result={
                    "status": listing_status,
                    "error_code": listing.get("error_code"),
                },
                dispatched=_could_have_dispatched(sup.diagnostics),
                usage=sup.usage,
                worker=sup.diagnostics,
                stage="discovery",
                ledger_entry=False,
                candidate_count=len(listing.get("candidates", [])),
            )
        if listing_failed:
            print(
                f"error: {ticker}: {listing_status}/{listing.get('error_code') or '-'}",
                file=sys.stderr,
            )
            failed = True
            if listing_status in _PROVIDER_SCOPE_STATUSES:
                if journal is not None:
                    _write_batch_outcome(
                        journal, mode="list", source=args.source, periods=periods,
                        companies=len(companies), exit_code=EXIT_FAILURE, budget=budget,
                        manifest=None,
                    )
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
                if journal is not None:
                    unresolved_id = new_request_id()
                    _observe_item(
                        journal,
                        entry={
                            "request_id": unresolved_id,
                            "ticker": ticker,
                            "exchange": listing_request["exchange"],
                            "fiscal_period": None,
                            "status": "period_unresolved",
                            "error_code": "need_explicit_periods",
                            "elapsed_seconds": 0.0,
                        },
                        operation="list",
                        request=_item_request(unresolved_id, company, None),
                        result={
                            "status": "period_unresolved",
                            "error_code": "need_explicit_periods",
                        },
                        dispatched=False,
                        usage={"requests_used": 0, "response_bytes_used": 0, "exhausted": None},
                        worker=None,
                        stage="discovery",
                        ledger_entry=False,
                    )
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
    exit_code = EXIT_FAILURE if failed else EXIT_OK
    if journal is not None:
        _write_batch_outcome(
            journal, mode="list", source=args.source, periods=periods,
            companies=len(companies), exit_code=exit_code, budget=budget, manifest=None,
        )
    return exit_code


def _run_batch(
    cfg: dict,
    args: argparse.Namespace,
    companies: list[dict],
    periods: list[tuple[int, int]] | None,
    ctx: _RetrievalContext,
    provider_settings: ProviderSettings,
    translate_enabled: bool,
    journal: BatchJournal | None = None,
    *, runtime: EntryDiagnostics | None = None,
) -> int:
    if args.source == "fmp" and periods is None:
        print(
            "error: period_unresolved: --source fmp requires explicit --periods "
            "(no metadata discovery for recent-N)",
            file=sys.stderr,
        )
        if journal is not None:
            journal.refusal(
                entry="scraper",
                stage="period-resolution",
                refusal_code="period_unresolved",
                location="scraper.py#_run_batch",
                argument="--source",
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
        if journal is not None:
            journal.refusal(
                entry="scraper",
                stage="instance-lock",
                refusal_code="instance_lock_held",
                location="scraper.py#_run_batch",
            )
        return EXIT_USAGE

    budget: BatchBudget | None = None
    fn: FileNaming | None = None
    entries: list[dict[str, Any]] = []
    started_at = datetime.now().isoformat(timespec="seconds")
    exit_code = EXIT_FAILURE
    manifest_facts: dict[str, Any] | None = None
    publication_failure: str | None = None
    execution_failure: list[dict] | None = None
    report_ready = False
    try:
        transcripts_root.mkdir(parents=True, exist_ok=True)
        _configure_run_logging(cfg, logs_dir / "scraper.log", runtime)
        budget = BatchBudget(
            args.max_requests, args.max_seconds,
            args.max_response_bytes, args.max_output_bytes,
        )
        fn = FileNaming(cfg)

        for company in companies:
            if budget.exhausted:
                break
            if args.source == "fool":
                outcome = _process_fool_company(
                    cfg=cfg, fn=fn, company=company, args=args, periods=periods,
                    budget=budget, ctx=ctx,
                    provider_settings=provider_settings, entries=entries,
                    transcripts_root=transcripts_root,
                    translate_enabled=translate_enabled,
                    journal=journal,
                )
            else:
                outcome = _process_fmp_company(
                    cfg=cfg, fn=fn, company=company, periods=periods,
                    budget=budget, ctx=ctx,
                    provider_settings=provider_settings, entries=entries,
                    transcripts_root=transcripts_root,
                    translate_enabled=translate_enabled,
                    journal=journal,
                )
            if outcome in ("budget", "stop"):
                break

        save_summary(cfg, fn, companies, transcripts_root)
        report_ready = True
    except BatchBudgetExceeded as error:
        log_exception_trace(log, logging.WARNING, "batch budget exceeded: %s", error,
                            budget.exhausted if budget else "unknown")
        if budget is not None and fn is not None:
            try:
                save_summary(cfg, fn, companies, transcripts_root)
            except Exception as error:
                log_exception_trace(log, logging.WARNING, "batch_summary_failed", error)
            else:
                report_ready = True
    except Exception as exc:
        execution_failure = log_exception_trace(log, logging.ERROR, "unexpected batch failure", exc)
        print(f"error: unexpected_batch_failure ({execution_failure[0]['type']})", file=sys.stderr)
        record_local_failure(
            "unexpected_batch_failure", {"trace": execution_failure}, identity=ctx.diagnostic_identity,
        )
        exit_code = EXIT_FAILURE
    finally:
        try:
            manifest_facts = _write_manifest(transcripts_root, args, budget, entries, started_at, periods)
        except Exception as exc:
            publication_failure = type(exc).__name__
            print(f"error: manifest_write_failed ({publication_failure})", file=sys.stderr)
            record_local_failure(
                "manifest_write_failed",
                {"exception_type": publication_failure, "entry_count": len(entries), "exception_trace": exception_trace(exc)},
                identity=ctx.diagnostic_identity,
            )
        finally:
            for handler in list(log.handlers):
                handler.close()
                log.removeHandler(handler)
            lock.release()
    if report_ready and budget is not None:
        exit_code = _report(budget, entries, publication_failed=publication_failure is not None)
    if journal is not None:
        _write_batch_outcome(
            journal, mode="batch", source=args.source, periods=periods,
            companies=len(companies), exit_code=exit_code, budget=budget,
            manifest=manifest_facts,
            publication_failure=publication_failure,
            execution_failure=execution_failure,
        )
    return exit_code


@transcript_feedback.observed_entry("scraper")
def main(
    argv: list[str] | None = None,
    *,
    runtime: EntryDiagnostics,
    _provider_settings: ProviderSettings = DEFAULT_PROVIDER_SETTINGS,
    _companies: list[dict] | None = None,
    _retrieval_launcher: str | None = None,
    _retrieval_spec: dict[str, Any] | None = None,
    _retrieval_temp_root: Path | None = None,
) -> int:
    """CLI entry; private injection points are for offline tests only."""
    # 普通启动在参数解析与副作用之前建立本次身份：被拒绝的调用同样可观察，
    # 导出失败绝不改变业务原返回。
    recorder = transcript_feedback.recorder_from_env(identity=runtime.identity)
    # 逐项观测与普通拒绝共用同一个 recorder：一个位置只有一个写者。
    journal = transcript_feedback.BatchJournal(recorder)
    parser = build_argument_parser()
    try:
        try:
            args = parser.parse_args(argv)
        except transcript_feedback.InvocationRefusal as refusal:
            return transcript_feedback.refuse_invocation(
                parser,
                refusal,
                recorder,
                entry="scraper",
                stage="argument-parsing",
                refusal_code="invalid_cli_arguments",
                location="scraper.py#parse_args",
                runtime=runtime,
            )

        error = _validate_args(args)
        if error:
            transcript_feedback.emit_invocation_refusal(
                recorder,
                entry="scraper",
                stage="argument-validation",
                refusal_code="invalid_argument_value",
                location="scraper.py#_validate_args",
                runtime=runtime,
                argument="--periods" if error.startswith("invalid --periods") else None,
            )
            print(redact_text(f"error: {error}"), file=sys.stderr)
            return EXIT_USAGE
        try:
            periods = parse_periods(args.periods) if args.periods else None
        except ValueError as exc:
            transcript_feedback.emit_invocation_refusal(
                recorder,
                entry="scraper",
                stage="argument-validation",
                refusal_code="invalid_argument_value",
                location="scraper.py#parse_periods",
                runtime=runtime,
                argument="--periods",
            )
            print(redact_text(f"error: invalid --periods token: {exc.args[0]}"), file=sys.stderr)
            return EXIT_USAGE
        translate_enabled = bool(args.translate)

        runtime.begin("config-load", persist=not (args.dry_run or args.list))
        try:
            cfg = load_config()
        except FileNotFoundError as exc:
            transcript_feedback.emit_invocation_refusal(
                recorder,
                entry="scraper",
                stage="config-load",
                refusal_code="config_unavailable",
                location="scraper.py#main",
                runtime=runtime,
            )
            print(redact_text(f"error: {exc}"), file=sys.stderr)
            return EXIT_FAILURE
        runtime.stage = "company-selection"
        if args.output is not None:
            cfg = _cfg_with_output(cfg, args.output.resolve())
        transcripts_root = get_path(cfg, "transcripts_dir")

        if _companies is not None:
            companies = _companies
        else:
            companies = load_companies(cfg, filter_ticker=args.ticker)
        if not companies:
            transcript_feedback.emit_invocation_refusal(
                recorder,
                entry="scraper",
                stage="company-selection",
                refusal_code="empty_company_selection",
                location="scraper.py#main",
                runtime=runtime,
                argument="--ticker" if args.ticker else None,
            )
            print("error: no companies found in companies.txt", file=sys.stderr)
            return EXIT_FAILURE

        runtime.stage = "batch-plan" if args.dry_run else "metadata-discovery" if args.list else "batch-execution"
        if args.dry_run:
            return _run_dry_plan(
                cfg, args, companies, periods, transcripts_root, translate_enabled
            )
        retrieval_ctx = _RetrievalContext(
            launcher=_retrieval_launcher,
            launcher_spec=_retrieval_spec,
            temp_root=_retrieval_temp_root,
            api_key=args.api_key or None,
            diagnostic_identity=(recorder.run_id, recorder.attempt_id) if recorder is not None else runtime.identity,
        )
        if args.list:
            return _run_list(
                args, companies, periods, retrieval_ctx, _provider_settings, journal
            )
        return _run_batch(
            cfg, args, companies, periods, retrieval_ctx, _provider_settings,
            translate_enabled, journal, runtime=runtime,
        )
    finally:
        transcript_feedback.finalize_delivery(recorder)
        transcript_feedback.warn_incomplete_feedback(recorder)


if __name__ == "__main__":
    raise SystemExit(main())

"""ET 本地原件（*_earnings_call.txt）的字节/身份验证与小收据。

设计边界（与 N5-ET-TXT 卡一致）：
- 只读本地文件：严格 UTF-8 解码，实算 canonical body 与 stored file 的
  SHA-256/UTF-8 字节数，与 sidecar 收据 ``*.receipt.json``
  （schema ``et-local-text-receipt/1``）对比。
- 身份/期间只从**原文头部**证明（``Ticker``，或调用方给定且头部写明的
  ``URL``；``Quarter`` 证明期间）；文件名/路径只产生“期望”，永远不是证据。
- 收据是 ET 本地附件：不宣称 provider 原始 HTTP hash、公开日/as-of 证明或
  CWP 准入，也不构成第二个 canonical 来源库；``published_date`` 恒为 null。
- ``Characters`` 只作头部历史诊断，从不参与字节计算。
- 纯本地：0 网络、0 翻译、0 外部 LLM；仅依赖 parser 的头部/正文切分。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from parser import extract_body, parse_transcript_header

RECEIPT_SCHEMA = "et-local-text-receipt/1"
EXTRACTION_VERSION = "et-store-original/1"
KIND_DOWNLOAD = "download"
KIND_AUDIT_LEGACY = "audit-legacy"

OUTCOME_VERIFIED = "verified"
OUTCOME_LEGACY_UNVERIFIED = "legacy_unverified"
OUTCOME_IDENTITY_MISSING = "identity_missing"
OUTCOME_IDENTITY_MISMATCH = "identity_mismatch"
OUTCOME_PERIOD_MISMATCH = "period_mismatch"
OUTCOME_CORRUPT = "corrupt"
OUTCOME_RECEIPT_INVALID = "receipt_invalid"
OUTCOME_RECEIPT_MISMATCH = "receipt_mismatch"

#: 具名失败类 outcome：原件保留，报告方按 output_conflict 语义计为失败。
FAILURE_OUTCOMES = frozenset(
    {
        OUTCOME_IDENTITY_MISMATCH,
        OUTCOME_PERIOD_MISMATCH,
        OUTCOME_CORRUPT,
        OUTCOME_RECEIPT_INVALID,
        OUTCOME_RECEIPT_MISMATCH,
    }
)

_LABEL_RE = re.compile(r"^Q([1-4])\s+(20\d{2})$", re.IGNORECASE)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ReceiptError(ValueError):
    """收据缺失以外的收据问题（坏 JSON/schema/不可读）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ArtifactVerdict:
    """一次本地原件核验的结果；bytes/hash 为实算值（不可算时为 None）。"""

    outcome: str
    error_code: str | None
    ticker: str | None = None
    fiscal_year: int | None = None
    fiscal_quarter: int | None = None
    body_sha256: str | None = None
    body_bytes: int | None = None
    file_sha256: str | None = None
    file_bytes: int | None = None
    receipt_kind: str | None = None
    header: dict = field(default_factory=dict)


def receipt_path(txt_path: Path) -> Path:
    """sidecar 收据路径：``X_earnings_call.txt`` → ``X_earnings_call.receipt.json``。"""
    return txt_path.parent / f"{txt_path.stem}.receipt.json"


def parse_quarter_label(label: str) -> tuple[int, int] | None:
    """``"Q3 2026"`` → ``(2026, 3)``；无法解析返回 None（不当作证据也不当矛盾）。"""
    match = _LABEL_RE.match((label or "").strip())
    if match is None:
        return None
    return int(match.group(2)), int(match.group(1))


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_body(text: str, cfg: dict | None = None) -> str:
    """规范化正文：头部之后的全部内容，仅裁掉首尾空白（内部字节按存储原样）。"""
    return extract_body(text, cfg=cfg).strip()


def body_digest(text: str, cfg: dict | None = None) -> tuple[str, int]:
    data = canonical_body(text, cfg).encode("utf-8")
    return sha256_hex(data), len(data)


def _clean_optional_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or value == "N/A":
        return None
    return value


def build_receipt(
    *,
    kind: str,
    ticker: str | None,
    fiscal_year: int | None,
    fiscal_quarter: int | None,
    body_sha256: str,
    body_bytes: int,
    file_sha256: str,
    file_bytes: int,
    provider: str | None = None,
    source_url: str | None = None,
    extraction_version: str | None = None,
    obtained_at: str | None = None,
) -> dict:
    """构建 ``et-local-text-receipt/1`` 收据（published_date 恒 null，不宣称公开日）。"""
    fiscal_period = (
        f"{fiscal_year}-Q{fiscal_quarter}"
        if fiscal_year is not None and fiscal_quarter is not None
        else None
    )
    return {
        "schema": RECEIPT_SCHEMA,
        "kind": kind,
        "ticker": ticker,
        "fiscal_year": fiscal_year,
        "fiscal_quarter": fiscal_quarter,
        "fiscal_period": fiscal_period,
        "provider": provider,
        "source_url": source_url,
        "extraction_version": extraction_version,
        "obtained_at": obtained_at,
        "published_date": None,
        "body_sha256": body_sha256,
        "body_bytes": body_bytes,
        "file_sha256": file_sha256,
        "file_bytes": file_bytes,
    }


def build_download_receipt(
    *,
    ticker: str,
    fiscal_year: int,
    fiscal_quarter: int,
    source: str | None,
    url: str | None,
    obtained_at: str,
    text: str,
    payload: bytes,
    cfg: dict | None = None,
) -> dict:
    """新落盘原件的收据：摘要全部取自实际写入的字节。"""
    provider = "fmp" if source == "fmp_api" else _clean_optional_str(source)
    body_sha, body_size = body_digest(text, cfg)
    return build_receipt(
        kind=KIND_DOWNLOAD,
        ticker=ticker,
        fiscal_year=fiscal_year,
        fiscal_quarter=fiscal_quarter,
        provider=provider,
        source_url=_clean_optional_str(url),
        extraction_version=EXTRACTION_VERSION,
        obtained_at=obtained_at,
        body_sha256=body_sha,
        body_bytes=body_size,
        file_sha256=sha256_hex(payload),
        file_bytes=len(payload),
    )


def build_audit_receipt(verdict: ArtifactVerdict) -> dict:
    """从已证明（legacy_unverified）的 verdict 构造 audit-legacy 收据。

    只记录当前字节与头部可证明字段；``obtained_at``/``extraction_version``
    保持 null——不补造下载时时间、来源或 hash。
    """
    header = verdict.header or {}
    source = _clean_optional_str(header.get("Source"))
    return build_receipt(
        kind=KIND_AUDIT_LEGACY,
        ticker=verdict.ticker,
        fiscal_year=verdict.fiscal_year,
        fiscal_quarter=verdict.fiscal_quarter,
        provider="fmp" if source == "fmp_api" else source,
        source_url=_clean_optional_str(header.get("URL")),
        extraction_version=None,
        obtained_at=None,
        body_sha256=verdict.body_sha256 or "",
        body_bytes=verdict.body_bytes or 0,
        file_sha256=verdict.file_sha256 or "",
        file_bytes=verdict.file_bytes or 0,
    )


def receipt_json_bytes(receipt: dict) -> bytes:
    return json.dumps(receipt, ensure_ascii=False, indent=1).encode("utf-8")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReceiptError("receipt_invalid", message)


def parse_receipt(raw: bytes) -> dict:
    """严格解码 + JSON + schema 校验；任何问题抛 ``ReceiptError``。"""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReceiptError(
            "receipt_invalid", f"receipt is not valid UTF-8: {exc}"
        ) from exc
    try:
        receipt = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReceiptError(
            "receipt_invalid", f"receipt is not valid JSON: {exc}"
        ) from exc
    _require(isinstance(receipt, dict), "receipt must be a JSON object")
    _require(
        receipt.get("schema") == RECEIPT_SCHEMA,
        f"receipt schema must be {RECEIPT_SCHEMA}",
    )
    kind = receipt.get("kind")
    _require(
        kind in (KIND_DOWNLOAD, KIND_AUDIT_LEGACY),
        "receipt kind must be download or audit-legacy",
    )
    ticker = receipt.get("ticker")
    _require(
        isinstance(ticker, str) and bool(ticker.strip()),
        "receipt ticker must be a non-empty string",
    )
    fiscal_year = receipt.get("fiscal_year")
    fiscal_quarter = receipt.get("fiscal_quarter")
    _require(
        isinstance(fiscal_year, int)
        and not isinstance(fiscal_year, bool)
        and isinstance(fiscal_quarter, int)
        and not isinstance(fiscal_quarter, bool)
        and 1 <= fiscal_quarter <= 4,
        "receipt fiscal year/quarter must be explicit ints",
    )
    _require(
        receipt.get("fiscal_period") == f"{fiscal_year}-Q{fiscal_quarter}",
        "receipt fiscal_period must match fiscal year/quarter",
    )
    for key in ("body_sha256", "file_sha256"):
        value = receipt.get(key)
        _require(
            isinstance(value, str) and bool(_SHA256_RE.match(value or "")),
            f"receipt {key} must be a sha256 hex",
        )
    for key in ("body_bytes", "file_bytes"):
        value = receipt.get(key)
        _require(
            isinstance(value, int) and not isinstance(value, bool) and value >= 0,
            f"receipt {key} must be a non-negative int",
        )
    for key in (
        "provider",
        "source_url",
        "extraction_version",
        "obtained_at",
        "published_date",
    ):
        _require(
            receipt.get(key) is None or isinstance(receipt.get(key), str),
            f"receipt {key} must be str or null",
        )
    _require(
        receipt.get("published_date") is None, "receipt must not claim a published date"
    )
    if kind == KIND_DOWNLOAD:
        _require(
            isinstance(receipt.get("extraction_version"), str)
            and bool(receipt["extraction_version"]),
            "download receipt needs extraction_version",
        )
        _require(
            isinstance(receipt.get("obtained_at"), str)
            and bool(receipt["obtained_at"]),
            "download receipt needs obtained_at (acquisition time)",
        )
    else:
        _require(
            receipt.get("extraction_version") is None,
            "audit receipt must not claim an extraction version",
        )
        _require(
            receipt.get("obtained_at") is None,
            "audit receipt must not claim an obtained_at",
        )
    return receipt


def read_receipt(txt_path: Path) -> dict | None:
    """读取 sidecar 收据：不存在 → None；坏收据 → 抛 ``ReceiptError``。"""
    path = receipt_path(txt_path)
    if not path.exists():
        return None
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ReceiptError("receipt_unreadable", f"{path.name}: {exc}") from exc
    return parse_receipt(raw)


def write_atomic(path: Path, payload: bytes) -> None:
    """tmp + ``os.replace`` 原子落盘；失败清理临时文件，绝不先删既有文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = path.parent / f".{path.name}.tmp-{os.getpid()}"
    try:
        tmp_path.write_bytes(payload)
        os.replace(tmp_path, path)
        tmp_path = None
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def verify_stored_original(
    txt_path: Path,
    *,
    ticker: str,
    quarter: str,
    cfg: dict | None = None,
    expected_url: str | None = None,
) -> ArtifactVerdict:
    """核验已有原件能否被复用；只读，绝不修改任何文件。

    判定顺序：严格解码 → 正文非空 → 头部矛盾（Ticker/URL/Quarter）→
    收据（schema/身份/期间/字节绑定）→ 无收据时头部可证明性。
    完整性边界：只证明“当前字节 = 收据字节、身份/期间相符”，
    不证明内容完整（无法凭关键词判断截断）。
    """
    expected = parse_quarter_label(quarter)
    if expected is None:
        raise ValueError(f"unparseable expected quarter label: {quarter!r}")
    expected_year, expected_quarter = expected
    ticker = (ticker or "").strip()

    try:
        raw = txt_path.read_bytes()
    except OSError:
        return ArtifactVerdict(
            OUTCOME_CORRUPT,
            "unreadable",
            ticker=ticker,
            fiscal_year=expected_year,
            fiscal_quarter=expected_quarter,
        )
    file_sha, file_size = sha256_hex(raw), len(raw)
    base: dict = {
        "ticker": ticker,
        "fiscal_year": expected_year,
        "fiscal_quarter": expected_quarter,
        "file_sha256": file_sha,
        "file_bytes": file_size,
    }
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return ArtifactVerdict(OUTCOME_CORRUPT, "invalid_encoding", **base)

    meta = parse_transcript_header(text, cfg=cfg)
    body = canonical_body(text, cfg)
    body_data = body.encode("utf-8")
    body_sha, body_size = sha256_hex(body_data), len(body_data)
    base.update(header=meta, body_sha256=body_sha, body_bytes=body_size)
    if not body:
        return ArtifactVerdict(OUTCOME_CORRUPT, "empty_original", **base)

    header_ticker = (meta.get("Ticker") or "").strip()
    header_url = (meta.get("URL") or "").strip()
    header_quarter = parse_quarter_label((meta.get("Quarter") or "").strip())

    if header_ticker and header_ticker.upper() != ticker.upper():
        return ArtifactVerdict(OUTCOME_IDENTITY_MISMATCH, "identity_mismatch", **base)
    if (
        expected_url
        and header_url
        and header_url not in ("N/A",)
        and header_url != expected_url
    ):
        return ArtifactVerdict(OUTCOME_IDENTITY_MISMATCH, "identity_mismatch", **base)
    if header_quarter is not None and header_quarter != (
        expected_year,
        expected_quarter,
    ):
        return ArtifactVerdict(OUTCOME_PERIOD_MISMATCH, "period_mismatch", **base)

    try:
        receipt = read_receipt(txt_path)
    except ReceiptError as exc:
        return ArtifactVerdict(OUTCOME_RECEIPT_INVALID, exc.code, **base)

    if receipt is not None:
        base["receipt_kind"] = receipt["kind"]
        if receipt["ticker"].upper() != ticker.upper():
            return ArtifactVerdict(
                OUTCOME_IDENTITY_MISMATCH, "identity_mismatch", **base
            )
        if (receipt["fiscal_year"], receipt["fiscal_quarter"]) != (
            expected_year,
            expected_quarter,
        ):
            return ArtifactVerdict(OUTCOME_PERIOD_MISMATCH, "period_mismatch", **base)
        if (
            receipt["file_sha256"] != file_sha
            or receipt["file_bytes"] != file_size
            or receipt["body_sha256"] != body_sha
            or receipt["body_bytes"] != body_size
        ):
            return ArtifactVerdict(OUTCOME_RECEIPT_MISMATCH, "receipt_mismatch", **base)
        outcome = (
            OUTCOME_VERIFIED
            if receipt["kind"] == KIND_DOWNLOAD
            else OUTCOME_LEGACY_UNVERIFIED
        )
        return ArtifactVerdict(outcome, None, **base)

    identity_proven = bool(header_ticker) and header_ticker.upper() == ticker.upper()
    identity_proven = identity_proven or (
        bool(expected_url)
        and header_url not in ("", "N/A")
        and header_url == expected_url
    )
    period_proven = header_quarter == (expected_year, expected_quarter)
    if identity_proven and period_proven:
        return ArtifactVerdict(OUTCOME_LEGACY_UNVERIFIED, None, **base)
    return ArtifactVerdict(OUTCOME_IDENTITY_MISSING, "identity_missing", **base)

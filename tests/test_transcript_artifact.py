"""transcript_artifact 纯函数单元测试（N5-ET-TXT）。

覆盖：有效中文/英文、空/截断/乱码、角色头部、不同季度、哈希冲突、
缺失/坏收据、不同 newline；完整性边界=字节/身份绑定，不做关键词完整性推定。
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from transcript_artifact import (  # noqa: E402
    EXTRACTION_VERSION,
    KIND_AUDIT_LEGACY,
    KIND_DOWNLOAD,
    RECEIPT_SCHEMA,
    ArtifactVerdict,
    ReceiptError,
    body_digest,
    build_audit_receipt,
    build_download_receipt,
    build_receipt,
    canonical_body,
    parse_quarter_label,
    parse_receipt,
    receipt_json_bytes,
    receipt_path,
    read_receipt,
    sha256_hex,
    verify_stored_original,
    write_atomic,
)

SEP = "=" * 70
DEFAULT_BODY = "Operator — Good morning, and welcome."


def make_text(header: dict | None = None, body: str = DEFAULT_BODY) -> str:
    if header is None:
        header = {
            "Company": "Acme (亚克力)",
            "Ticker": "ACME",
            "Quarter": "Q3 2026",
            "Source": "fmp_api",
            "URL": "N/A",
            "Characters": "12",
        }
    lines = "".join(
        f"{key}: {value}\n" for key, value in header.items() if value is not None
    )
    return f"{SEP}\nEarnings Call Transcript\n{lines}{SEP}\n\n{body}"


def write_txt(
    tmp_path: Path, text: str | bytes, name: str = "ACME_Q3_2026_earnings_call.txt"
) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    # 按字节写入：LF 保真（Windows write_text 会换行成 CRLF，断言需跨平台稳定）
    path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
    return path


def attach_receipt(
    txt_path: Path,
    *,
    kind: str = KIND_DOWNLOAD,
    ticker: str = "ACME",
    fiscal_year: int = 2026,
    fiscal_quarter: int = 3,
    cfg: dict | None = None,
) -> dict:
    raw = txt_path.read_bytes()
    body_sha, body_bytes = body_digest(raw.decode("utf-8"), cfg)
    receipt = build_receipt(
        kind=kind,
        ticker=ticker,
        fiscal_year=fiscal_year,
        fiscal_quarter=fiscal_quarter,
        provider="fmp",
        source_url=None,
        extraction_version=EXTRACTION_VERSION if kind == KIND_DOWNLOAD else None,
        obtained_at="2026-10-06T00:00:00" if kind == KIND_DOWNLOAD else None,
        body_sha256=body_sha,
        body_bytes=body_bytes,
        file_sha256=sha256_hex(raw),
        file_bytes=len(raw),
    )
    receipt_path(txt_path).write_bytes(receipt_json_bytes(receipt))
    return receipt


# ── 基础解析/命名 ───────────────────────────────────────────────


def test_parse_quarter_label_accepts_only_render_format():
    assert parse_quarter_label("Q3 2026") == (2026, 3)
    assert parse_quarter_label(" q1 2025 ") == (2025, 1)
    assert parse_quarter_label("2026-Q3") is None
    assert parse_quarter_label("Q3/2026") is None
    assert parse_quarter_label("") is None
    assert parse_quarter_label(None) is None


def test_receipt_path_is_txt_sidecar(tmp_path):
    txt = tmp_path / "ACME" / "ACME_Q3_2026_earnings_call.txt"
    assert receipt_path(txt) == txt.parent / "ACME_Q3_2026_earnings_call.receipt.json"


def test_unparseable_expected_quarter_is_caller_error(tmp_path):
    txt = write_txt(tmp_path, make_text())
    with pytest.raises(ValueError):
        verify_stored_original(txt, ticker="ACME", quarter="2026-Q3")


# ── 有效中英文 / 空 / 截断 / 乱码 ──────────────────────────────


@pytest.mark.parametrize(
    "body",
    [
        "Prepared Remarks\n\nOperator — Good morning everyone.",
        "运营方——大家早上好。今天我们讨论第三季度业绩与展望。",
    ],
)
def test_valid_english_and_chinese_legacy_files(tmp_path, body):
    txt = write_txt(tmp_path, make_text(body=body))
    verdict = verify_stored_original(txt, ticker="ACME", quarter="Q3 2026")
    assert verdict.outcome == "legacy_unverified"
    assert verdict.error_code is None
    assert verdict.body_bytes == len(body.encode("utf-8"))
    assert verdict.body_sha256 == hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert verdict.file_bytes == txt.stat().st_size


def test_empty_and_whitespace_only_bodies_are_corrupt(tmp_path):
    empty = write_txt(tmp_path, make_text(header={}, body=""))
    verdict = verify_stored_original(empty, ticker="ACME", quarter="Q3 2026")
    assert (verdict.outcome, verdict.error_code) == ("corrupt", "empty_original")

    blank = write_txt(tmp_path, make_text(body="   \n\t  "))
    verdict = verify_stored_original(blank, ticker="ACME", quarter="Q3 2026")
    assert (verdict.outcome, verdict.error_code) == ("corrupt", "empty_original")


def test_missing_file_is_named_unreadable(tmp_path):
    verdict = verify_stored_original(
        tmp_path / "NOPE_Q3_2026_earnings_call.txt",
        ticker="NOPE",
        quarter="Q3 2026",
    )
    assert (verdict.outcome, verdict.error_code) == ("corrupt", "unreadable")


def test_invalid_utf8_is_corrupt_even_with_header_like_bytes(tmp_path):
    txt = write_txt(tmp_path, b"\xff\xfe\x00 broken \xc3\x28")
    verdict = verify_stored_original(txt, ticker="ACME", quarter="Q3 2026")
    assert (verdict.outcome, verdict.error_code) == ("corrupt", "invalid_encoding")
    assert verdict.file_bytes == txt.stat().st_size


def test_truncated_body_reports_actual_bytes_not_characters(tmp_path):
    body = "Truncated prepared remarks."
    txt = write_txt(
        tmp_path,
        make_text(
            header={
                "Company": "Acme (亚克力)",
                "Ticker": "ACME",
                "Quarter": "Q3 2026",
                "Source": "fmp_api",
                "URL": "N/A",
                "Characters": "500000",
            },
            body=body,
        ),
    )
    verdict = verify_stored_original(txt, ticker="ACME", quarter="Q3 2026")
    # 不凭关键词推定完整：能证明身份/期间 → legacy_unverified，字节为实算值
    assert verdict.outcome == "legacy_unverified"
    assert verdict.body_bytes == len(body.encode("utf-8"))
    assert verdict.body_bytes != 500000


# ── 角色头部 / 含分隔符正文 ────────────────────────────────────


def test_role_header_body_roundtrip_with_embedded_separator(tmp_path):
    body = (
        "Microsoft(MSFT)Q1 2025 Earnings CallOct 30, 2024,5:30 p.m. ET\n\n"
        "Prepared Remarks\n\nOperator\n\nGood morning.\n\n"
        f"{SEP}\n\nQuestions and Answers\n\nOperator — First question."
    )
    txt = write_txt(tmp_path, make_text(body=body))
    verdict = verify_stored_original(txt, ticker="ACME", quarter="Q3 2026")
    assert verdict.outcome == "legacy_unverified"
    assert verdict.body_bytes == len(body.encode("utf-8"))
    assert verdict.body_sha256 == hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert canonical_body(txt.read_text(encoding="utf-8")) == body


# ── 不同季度 / 身份矛盾 ────────────────────────────────────────


def test_wrong_quarter_header_is_period_mismatch(tmp_path):
    txt = write_txt(
        tmp_path,
        make_text(
            header={
                "Ticker": "ACME",
                "Quarter": "Q1 2026",
                "Source": "fmp_api",
                "URL": "N/A",
                "Characters": "12",
            }
        ),
    )
    verdict = verify_stored_original(txt, ticker="ACME", quarter="Q3 2026")
    assert (verdict.outcome, verdict.error_code) == (
        "period_mismatch",
        "period_mismatch",
    )


def test_wrong_ticker_header_is_identity_mismatch(tmp_path):
    txt = write_txt(
        tmp_path,
        make_text(
            header={
                "Ticker": "MSFT",
                "Quarter": "Q3 2026",
                "Source": "fmp_api",
                "URL": "N/A",
                "Characters": "12",
            }
        ),
    )
    verdict = verify_stored_original(txt, ticker="ACME", quarter="Q3 2026")
    assert (verdict.outcome, verdict.error_code) == (
        "identity_mismatch",
        "identity_mismatch",
    )


def test_ticker_match_is_case_insensitive(tmp_path):
    txt = write_txt(
        tmp_path,
        make_text(
            header={
                "Ticker": "acme",
                "Quarter": "Q3 2026",
                "Source": "fmp_api",
                "URL": "N/A",
                "Characters": "12",
            }
        ),
    )
    verdict = verify_stored_original(txt, ticker="ACME", quarter="Q3 2026")
    assert verdict.outcome == "legacy_unverified"


# ── URL 规则（fool 路径 expected_url） ─────────────────────────


def test_url_mismatch_is_identity_mismatch(tmp_path):
    txt = write_txt(
        tmp_path,
        make_text(
            header={
                "Ticker": "ACME",
                "Quarter": "Q3 2026",
                "URL": "https://www.fool.com/earnings/call-transcripts/1999/01/01/x/",
                "Characters": "12",
            }
        ),
    )
    verdict = verify_stored_original(
        txt,
        ticker="ACME",
        quarter="Q3 2026",
        expected_url="https://www.fool.com/earnings/call-transcripts/2026/09/01/y/",
    )
    assert (verdict.outcome, verdict.error_code) == (
        "identity_mismatch",
        "identity_mismatch",
    )


def test_url_na_header_does_not_contradict_expected_url(tmp_path):
    txt = write_txt(
        tmp_path,
        make_text(
            header={
                "Ticker": "ACME",
                "Quarter": "Q3 2026",
                "URL": "N/A",
                "Characters": "12",
            }
        ),
    )
    verdict = verify_stored_original(
        txt, ticker="ACME", quarter="Q3 2026", expected_url="https://example.com/x"
    )
    assert verdict.outcome == "legacy_unverified"


def test_url_match_proves_identity_when_ticker_absent(tmp_path):
    url = "https://www.fool.com/earnings/call-transcripts/2026/09/01/acme-q3-2026/"
    txt = write_txt(tmp_path, make_text(header={"URL": url, "Quarter": "Q3 2026"}))
    verdict = verify_stored_original(
        txt, ticker="ACME", quarter="Q3 2026", expected_url=url
    )
    assert verdict.outcome == "legacy_unverified"


def test_url_only_without_quarter_is_identity_missing(tmp_path):
    url = "https://www.fool.com/earnings/call-transcripts/2026/09/01/acme-q3-2026/"
    txt = write_txt(tmp_path, make_text(header={"URL": url}))
    verdict = verify_stored_original(
        txt, ticker="ACME", quarter="Q3 2026", expected_url=url
    )
    assert (verdict.outcome, verdict.error_code) == (
        "identity_missing",
        "identity_missing",
    )


# ── 证明不了身份/期间 ──────────────────────────────────────────


@pytest.mark.parametrize(
    "header",
    [
        {},  # 无头部结构
        {"Source": "motley_fool", "Characters": "12"},  # 缺 Ticker/Quarter
        {"Ticker": "ACME", "Characters": "12"},  # 缺 Quarter
        {"Quarter": "Q3 2026", "Characters": "12"},  # 缺 Ticker
        {"Ticker": "ACME", "Quarter": "Q3/2026"},  # Quarter 不可解析 → 不作证据
    ],
)
def test_unprovable_identity_reports_identity_missing(tmp_path, header):
    txt = write_txt(
        tmp_path, make_text(header=header) if header else make_text(header={})
    )
    verdict = verify_stored_original(txt, ticker="ACME", quarter="Q3 2026")
    assert (verdict.outcome, verdict.error_code) == (
        "identity_missing",
        "identity_missing",
    )


# ── 收据：download / audit-legacy / 冲突 ───────────────────────


def test_download_receipt_roundtrip_is_verified(tmp_path):
    txt = write_txt(tmp_path, make_text())
    attach_receipt(txt, kind=KIND_DOWNLOAD)
    verdict = verify_stored_original(txt, ticker="ACME", quarter="Q3 2026")
    assert verdict.outcome == "verified"
    assert verdict.error_code is None
    assert verdict.receipt_kind == KIND_DOWNLOAD


def test_audit_receipt_match_stays_legacy_unverified(tmp_path):
    txt = write_txt(tmp_path, make_text())
    attach_receipt(txt, kind=KIND_AUDIT_LEGACY)
    verdict = verify_stored_original(txt, ticker="ACME", quarter="Q3 2026")
    assert verdict.outcome == "legacy_unverified"
    assert verdict.receipt_kind == KIND_AUDIT_LEGACY


def test_missing_receipt_is_none(tmp_path):
    txt = write_txt(tmp_path, make_text())
    assert read_receipt(txt) is None


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schema": "et-local-text-receipt/1", "kind": "down',
        b"not json at all",
        b'\xff\xfe{"schema": "et-local-text-receipt/1"}',
        b"[]",
        b'{"schema": "other/9", "kind": "download"}',
        b'{"schema": "et-local-text-receipt/1", "kind": "evil"}',
    ],
)
def test_bad_receipt_bytes_are_receipt_invalid(tmp_path, raw):
    txt = write_txt(tmp_path, make_text())
    receipt_path(txt).write_bytes(raw)
    verdict = verify_stored_original(txt, ticker="ACME", quarter="Q3 2026")
    assert (verdict.outcome, verdict.error_code) == (
        "receipt_invalid",
        "receipt_invalid",
    )


def test_receipt_schema_violations_are_invalid(tmp_path):
    txt = write_txt(tmp_path, make_text())
    valid = build_receipt(
        kind=KIND_DOWNLOAD,
        ticker="ACME",
        fiscal_year=2026,
        fiscal_quarter=3,
        extraction_version=EXTRACTION_VERSION,
        obtained_at="2026-10-06T00:00:00",
        body_sha256="0" * 64,
        body_bytes=1,
        file_sha256="1" * 64,
        file_bytes=2,
    )
    bad_variants = [
        dict(valid, schema="nope/1"),
        dict(valid, fiscal_period="2026-Q4"),
        dict(valid, fiscal_quarter=5),
        dict(valid, ticker=""),
        dict(valid, body_sha256="zz"),
        dict(valid, file_bytes=-1),
        dict(valid, published_date="2026-01-01"),
        dict(valid, obtained_at=None),  # download 缺取得时间
        dict(valid, extraction_version=None),  # download 缺 extraction
        dict(valid, provider=123),
    ]
    for variant in bad_variants:
        receipt_path(txt).write_bytes(receipt_json_bytes(variant))
        verdict = verify_stored_original(txt, ticker="ACME", quarter="Q3 2026")
        assert (verdict.outcome, verdict.error_code) == (
            "receipt_invalid",
            "receipt_invalid",
        ), variant


def test_audit_receipt_must_not_fabricate_download_fields(tmp_path):
    txt = write_txt(tmp_path, make_text())
    base = build_receipt(
        kind=KIND_AUDIT_LEGACY,
        ticker="ACME",
        fiscal_year=2026,
        fiscal_quarter=3,
        body_sha256="0" * 64,
        body_bytes=1,
        file_sha256="1" * 64,
        file_bytes=2,
    )
    for forged in (
        dict(base, obtained_at="2026-10-06T00:00:00"),
        dict(base, extraction_version=EXTRACTION_VERSION),
    ):
        receipt_path(txt).write_bytes(receipt_json_bytes(forged))
        verdict = verify_stored_original(txt, ticker="ACME", quarter="Q3 2026")
        assert (verdict.outcome, verdict.error_code) == (
            "receipt_invalid",
            "receipt_invalid",
        )


def test_parse_receipt_exposes_named_code():
    with pytest.raises(ReceiptError) as excinfo:
        parse_receipt(b"{")
    assert excinfo.value.code == "receipt_invalid"


def test_tampered_bytes_conflict_with_download_receipt(tmp_path):
    txt = write_txt(tmp_path, make_text())
    attach_receipt(txt, kind=KIND_DOWNLOAD)
    txt.write_bytes(txt.read_bytes() + b"tampered")
    verdict = verify_stored_original(txt, ticker="ACME", quarter="Q3 2026")
    assert (verdict.outcome, verdict.error_code) == (
        "receipt_mismatch",
        "receipt_mismatch",
    )


def test_receipt_identity_contradiction_is_named_mismatch(tmp_path):
    txt = write_txt(tmp_path, make_text())
    attach_receipt(txt, ticker="MSFT")
    verdict = verify_stored_original(txt, ticker="ACME", quarter="Q3 2026")
    assert (verdict.outcome, verdict.error_code) == (
        "identity_mismatch",
        "identity_mismatch",
    )

    attach_receipt(txt, fiscal_year=2026, fiscal_quarter=1)
    verdict = verify_stored_original(txt, ticker="ACME", quarter="Q3 2026")
    assert (verdict.outcome, verdict.error_code) == (
        "period_mismatch",
        "period_mismatch",
    )


# ── newline 差异 ───────────────────────────────────────────────


def test_crlf_file_verifies_against_its_own_receipt(tmp_path):
    text = make_text(body="Line one.\r\nLine two.\r\n")
    txt = write_txt(tmp_path, text.replace("\n", "\r\n").encode("utf-8"))
    attach_receipt(txt, kind=KIND_DOWNLOAD)
    verdict = verify_stored_original(txt, ticker="ACME", quarter="Q3 2026")
    assert verdict.outcome == "verified"


def test_newline_conversion_invalidates_receipt(tmp_path):
    txt = write_txt(tmp_path, make_text().replace("\n", "\r\n").encode("utf-8"))
    attach_receipt(txt, kind=KIND_DOWNLOAD)
    converted = txt.read_bytes().replace(b"\r\n", b"\n")
    txt.write_bytes(converted)
    verdict = verify_stored_original(txt, ticker="ACME", quarter="Q3 2026")
    assert (verdict.outcome, verdict.error_code) == (
        "receipt_mismatch",
        "receipt_mismatch",
    )


# ── 收据构建绑定真实字节 ───────────────────────────────────────


def test_download_receipt_binds_actual_payload_bytes(tmp_path):
    text = make_text(body="中文正文 123")
    payload = text.encode("utf-8")
    receipt = build_download_receipt(
        ticker="ACME",
        fiscal_year=2026,
        fiscal_quarter=3,
        source="fmp_api",
        url="N/A",
        obtained_at="2026-10-06T12:00:00",
        text=text,
        payload=payload,
    )
    assert receipt["schema"] == RECEIPT_SCHEMA
    assert receipt["kind"] == KIND_DOWNLOAD
    assert receipt["provider"] == "fmp"
    assert receipt["source_url"] is None  # "N/A" 不算实际已知
    assert receipt["fiscal_period"] == "2026-Q3"
    assert receipt["file_sha256"] == hashlib.sha256(payload).hexdigest()
    assert receipt["file_bytes"] == len(payload)
    body = text.split(SEP)[-1].strip().encode("utf-8")
    assert receipt["body_sha256"] == hashlib.sha256(body).hexdigest()
    assert receipt["body_bytes"] == len(body)
    assert receipt["published_date"] is None


def test_audit_receipt_keeps_unprovable_fields_null(tmp_path):
    txt = write_txt(
        tmp_path,
        make_text(
            header={
                "Ticker": "ACME",
                "Quarter": "Q3 2026",
                "Source": "motley_fool",
                "URL": "https://example.com/x",
                "Characters": "12",
            }
        ),
    )
    verdict = verify_stored_original(txt, ticker="ACME", quarter="Q3 2026")
    receipt = build_audit_receipt(verdict)
    assert receipt["kind"] == KIND_AUDIT_LEGACY
    assert receipt["provider"] == "motley_fool"
    assert receipt["source_url"] == "https://example.com/x"
    assert receipt["obtained_at"] is None
    assert receipt["extraction_version"] is None
    assert receipt["published_date"] is None
    assert receipt["file_sha256"] == verdict.file_sha256


def test_download_receipt_provider_mapping(tmp_path):
    text = make_text()
    for source, provider in (
        ("motley_fool", "motley_fool"),
        ("fmp_api", "fmp"),
        (None, None),
    ):
        receipt = build_download_receipt(
            ticker="ACME",
            fiscal_year=2026,
            fiscal_quarter=3,
            source=source,
            url="https://example.com/x",
            obtained_at="2026-10-06T00:00:00",
            text=text,
            payload=text.encode("utf-8"),
        )
        assert receipt["provider"] == provider


# ── 原子落盘 ───────────────────────────────────────────────────


def test_write_atomic_replaces_and_leaves_no_temp(tmp_path):
    target = tmp_path / "x.receipt.json"
    write_atomic(target, b"one")
    write_atomic(target, b"two")
    assert target.read_bytes() == b"two"
    assert [p.name for p in tmp_path.glob("*.tmp-*")] == []


def test_write_atomic_failure_keeps_existing_and_cleans_temp(tmp_path, monkeypatch):
    target = tmp_path / "x.receipt.json"
    target.write_bytes(b"original")

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("transcript_artifact.os.replace", boom)
    with pytest.raises(OSError):
        write_atomic(target, b"new")
    assert target.read_bytes() == b"original"
    assert [p.name for p in tmp_path.glob("*.tmp-*")] == []


def test_verdict_dataclass_defaults_are_inert():
    verdict = ArtifactVerdict(outcome="corrupt", error_code="unreadable")
    assert verdict.header == {}
    assert verdict.body_bytes is None

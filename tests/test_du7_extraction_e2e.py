"""DU7-ET-EXTRACTION end-to-end through the real entries (card C).

Runs the real ``transcript_tool.main`` / ``scraper.main`` dispatch, the
supervisor and a real worker subprocess with only the HTTP transport faked via
the existing launcher seam. Verifies one extraction per fetch, raw payload SHA,
the honest extraction version, real provider flags, reuse of an existing TXT
without re-fetching, and failure paths that leave no half-written files.

Sentinel and pre-existing v1 files are byte-restored at the end; every download
or receipt created by these tests is deleted afterwards.
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import scraper  # noqa: E402
import transcript_tool  # noqa: E402
import transcript_extraction  # noqa: E402
from transcript_api import (  # noqa: E402
    DEFAULT_PROVIDER_SETTINGS,
    ProviderSettings,
)
from tests.test_retrieval_runtime import (  # noqa: E402
    LAUNCHER,
    fake_spec,
    get_urls,
)
from tests.test_transcript_api import (  # noqa: E402
    Q1_URL,
    Q2_URL,
    QUOTE_URL,
    FakeResponse,
    make_candidate_request,
    quote_html,
    transcript_html,
)

FIXTURES = Path(__file__).parent / "fixtures" / "du7_extraction"
ACME = {"ticker": "ACME", "name_en": "Acme", "name_cn": "亚克力", "exchange": "nyse"}
ENABLED_FOOL = ProviderSettings(motley_fool_enabled=True)
SENTINEL = b"sentinel-keep-bytes\x00\xff"
OLD_V1_TEXT = (
    "pre-existing v1 original body, never rewritten by this test run\n" * 4
)


def _page(name: str) -> bytes:
    return (FIXTURES / f"{name}.html").read_bytes()


def _forbid_translator(monkeypatch: pytest.MonkeyPatch) -> None:
    import translator

    def explode(*args: Any, **kwargs: Any) -> None:
        pytest.fail("translator must not be constructed by these runs")

    monkeypatch.setattr(translator.TranslatorFactory, "create", explode)
    monkeypatch.setattr(scraper, "TranslatorFactory", translator.TranslatorFactory)


def _run_tool(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    request: dict[str, Any],
    *,
    spec: dict[str, Any],
    temp_root: Path,
    settings: ProviderSettings | None = None,
) -> tuple[int, dict[str, Any]]:
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
    stdout, stderr = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    rc = transcript_tool.main(
        argv,
        _provider_settings=settings or ProviderSettings(),
        _retrieval_launcher=LAUNCHER,
        _retrieval_spec=spec,
        _retrieval_temp_root=temp_root,
    )
    line = json.loads(stdout.getvalue().strip().splitlines()[-1])
    return rc, line


def test_cli_candidate_fetch_single_extraction_payload_sha_and_version(
    monkeypatch, tmp_path
):
    page = _page("business_sentence")
    calls_file = tmp_path / "calls.jsonl"
    temp_root = tmp_path / "rt"
    temp_root.mkdir()
    spec = fake_spec({Q2_URL: FakeResponse(Q2_URL, page)}, calls_file=calls_file)
    request = make_candidate_request()
    request["schema_version"] = "earnings-transcript-candidate-fetch-request/1"

    rc, result = _run_tool(
        monkeypatch,
        ["--request-stdin", "--operation", "fetch-candidate"],
        request,
        spec=spec,
        temp_root=temp_root,
        settings=ENABLED_FOOL,
    )
    assert rc == 0
    assert result["status"] == "fetched"
    # exactly one extraction: one body HTTP call, one owner invocation
    assert get_urls(calls_file) == [Q2_URL]
    # raw payload SHA binds the untouched original bytes
    assert result["provider_payload_sha256"] == hashlib.sha256(page).hexdigest()
    assert result["extraction_version"] == transcript_extraction.EXTRACTION_VERSION
    body = result["content_utf8"]
    assert result["canonical_content_sha256"] == hashlib.sha256(
        body.encode("utf-8")
    ).hexdigest()
    assert "Advertising revenue grew 20% and advertisement pricing declined." in body
    assert list(temp_root.iterdir()) == []


def test_real_provider_flags_motley_disabled_by_default_without_network(
    monkeypatch, tmp_path
):
    calls_file = tmp_path / "calls.jsonl"
    temp_root = tmp_path / "rt"
    temp_root.mkdir()
    spec = fake_spec({}, calls_file=calls_file)
    request = make_candidate_request()
    rc, result = _run_tool(
        monkeypatch,
        ["--request-stdin", "--operation", "fetch-candidate"],
        request,
        spec=spec,
        temp_root=temp_root,
        settings=DEFAULT_PROVIDER_SETTINGS,
    )
    assert rc == 0
    assert result["status"] == "unavailable"
    assert result["error_code"] == "provider_disabled"
    assert get_urls(calls_file) == []
    assert DEFAULT_PROVIDER_SETTINGS.motley_fool_enabled is False
    assert DEFAULT_PROVIDER_SETTINGS.fmp_enabled is True


def test_scraper_reuses_existing_txt_without_refetch_and_restores_files(
    monkeypatch, tmp_path
):
    _forbid_translator(monkeypatch)
    out = tmp_path / "out"
    old_v1 = out / "ACME" / "ACME_Q1_2026_earnings_call.txt"
    old_v1.parent.mkdir(parents=True)
    old_v1.write_bytes(OLD_V1_TEXT.encode("utf-8"))
    sentinel = tmp_path / "sentinel.bin"
    sentinel.write_bytes(SENTINEL)
    before = {
        str(p.relative_to(tmp_path)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in tmp_path.rglob("*")
        if p.is_file()
    }

    calls_file = tmp_path / "calls.jsonl"
    temp_root = tmp_path / "rt"
    temp_root.mkdir()
    spec = fake_spec(
        {
            QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(Q2_URL)),
            Q2_URL: FakeResponse(Q2_URL, _page("ad_container")),
        },
        calls_file=calls_file,
    )
    rc = scraper.main(
        ["--periods", "2026Q1,2026Q2", "--output", str(out)],
        _provider_settings=ENABLED_FOOL,
        _companies=[ACME],
        _retrieval_launcher=LAUNCHER,
        _retrieval_spec=spec,
        _retrieval_temp_root=temp_root,
    )
    assert rc == 0
    urls = get_urls(calls_file)
    # Q1 original exists: never re-fetched; only listing + Q2 body are requested
    assert Q1_URL not in urls
    assert urls.count(Q2_URL) == 1
    assert len(urls) == 2

    new_original = out / "ACME" / "ACME_Q2_2026_earnings_call.txt"
    assert new_original.exists()
    new_text = new_original.read_text(encoding="utf-8")
    assert "Our SaaS revenue grew 30%" in new_text
    assert "Motley Fool Stock Advisor" not in new_text
    # pre-existing v1 file untouched, byte for byte
    assert old_v1.read_bytes() == OLD_V1_TEXT.encode("utf-8")
    assert sentinel.read_bytes() == SENTINEL

    created = [
        p for p in tmp_path.rglob("*")
        if p.is_file() and str(p.relative_to(tmp_path)) not in before
    ]
    receipt_files = [p for p in created if p.name.endswith(".receipt.json")]
    assert receipt_files, "new original must carry its download receipt"
    for receipt_file in receipt_files:
        receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
        assert receipt["schema"] == "et-local-text-receipt/1"
        assert receipt["file_sha256"] and receipt["body_sha256"]
    # cleanup: delete everything these runs created; pre-existing files survive
    for path in created:
        path.unlink()
    after = {
        str(p.relative_to(tmp_path)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in tmp_path.rglob("*")
        if p.is_file()
    }
    assert after == before
    assert list(temp_root.iterdir()) == []


def test_failure_path_leaves_no_half_files(monkeypatch, tmp_path):
    _forbid_translator(monkeypatch)
    out = tmp_path / "out"
    out.mkdir()
    sentinel = tmp_path / "sentinel.bin"
    sentinel.write_bytes(SENTINEL)
    before = {
        str(p.relative_to(tmp_path)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in tmp_path.rglob("*")
        if p.is_file()
    }
    calls_file = tmp_path / "calls.jsonl"
    temp_root = tmp_path / "rt"
    temp_root.mkdir()
    spec = fake_spec(
        {
            QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(Q2_URL)),
            Q2_URL: FakeResponse(Q2_URL, b"", status_code=500),
        },
        calls_file=calls_file,
    )
    rc = scraper.main(
        ["--periods", "2026Q2", "--output", str(out)],
        _provider_settings=ENABLED_FOOL,
        _companies=[ACME],
        _retrieval_launcher=LAUNCHER,
        _retrieval_spec=spec,
        _retrieval_temp_root=temp_root,
    )
    assert rc != 0
    # failure must not write half a success file: no original, receipt or tmp
    half_files = [
        p for p in tmp_path.rglob("*")
        if p.is_file()
        and (
            p.name.endswith("_earnings_call.txt")
            or p.name.endswith(".receipt.json")
            or ".tmp-" in p.name
        )
    ]
    assert half_files == [], f"failure must not leave half files: {half_files}"
    assert sentinel.read_bytes() == SENTINEL
    assert list(temp_root.iterdir()) == []

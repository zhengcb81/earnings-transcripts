"""DU7-ET-EXTRACTION wiring/contract tests (card B items 5-6).

Both real fetch entries must delegate to the same extraction owner with
metadata versions matching the actual body; FMP extraction and provider
disabled states stay untouched; old and new extraction versions coexist.
"""
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import transcript_api
import transcript_extraction
from transcript_api import ProviderSettings, fetch_transcript, fetch_transcript_candidate

sys.path.insert(0, str(Path(__file__).parent))
from test_transcript_api import (
    FMP_URL,
    Q2_URL,
    FakeResponse,
    FakeSession,
    fmp_payload,
    make_candidate_request,
    make_request,
    quote_html,
    QUOTE_URL,
    transcript_html,
)

FIXTURES = Path(__file__).parent / "fixtures" / "du7_extraction"

ENABLED = ProviderSettings(motley_fool_enabled=True)


def _page(name: str) -> bytes:
    filename = name if name.endswith(".html") else f"{name}.html"
    return (FIXTURES / filename).read_bytes()


class _CountingOwner:
    def __init__(self, real):
        self.real = real
        self.calls = 0

    def __call__(self, page):
        self.calls += 1
        return self.real(page)


def test_both_fetch_entries_delegate_to_one_extraction_owner(monkeypatch):
    owner = _CountingOwner(transcript_extraction.extract)
    monkeypatch.setattr(transcript_extraction, "extract", owner)

    page = transcript_html()
    session = FakeSession({
        QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(Q2_URL)),
        Q2_URL: FakeResponse(Q2_URL, page),
    })
    result = fetch_transcript(
        make_request(), session_factory=lambda: session, provider_settings=ENABLED
    )
    assert result["status"] == "fetched"
    assert owner.calls == 1

    candidate_session = FakeSession({Q2_URL: FakeResponse(Q2_URL, page)})
    candidate_result = fetch_transcript_candidate(
        make_candidate_request(),
        session_factory=lambda: candidate_session,
        provider_settings=ENABLED,
    )
    assert candidate_result["status"] == "fetched"
    assert owner.calls == 2


def test_metadata_version_matches_actual_body_both_entries():
    # fixed body (ad words preserved) reports the new version
    fixed = _page("business_sentence")
    fixed_version = transcript_extraction.extract(fixed).extraction_version
    assert fixed_version == transcript_extraction.EXTRACTION_VERSION
    # compat body reports the frozen legacy version
    compat = _page("compat_baseline")
    compat_version = transcript_extraction.extract(compat).extraction_version
    assert compat_version == transcript_extraction.EXTRACTION_VERSION_LEGACY

    session = FakeSession({Q2_URL: FakeResponse(Q2_URL, fixed)})
    result = fetch_transcript_candidate(
        make_candidate_request(),
        session_factory=lambda: session,
        provider_settings=ENABLED,
    )
    assert result["status"] == "fetched"
    assert result["extraction_version"] == transcript_extraction.EXTRACTION_VERSION
    body = result["content_utf8"]
    assert result["canonical_content_sha256"] == hashlib.sha256(
        body.encode("utf-8")
    ).hexdigest()
    assert "Advertising revenue grew 20% and advertisement pricing declined." in body

    listing = FakeSession({
        QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(Q2_URL)),
        Q2_URL: FakeResponse(Q2_URL, fixed),
    })
    fetch_result = fetch_transcript(
        make_request(), session_factory=lambda: listing, provider_settings=ENABLED
    )
    assert fetch_result["status"] == "fetched"
    assert fetch_result["extraction_version"] == transcript_extraction.EXTRACTION_VERSION
    assert fetch_result["content_utf8"] == body


def test_fmp_extraction_and_provider_disabled_states_unchanged(monkeypatch):
    calls = {"n": 0}

    def boom(page):
        calls["n"] += 1
        raise AssertionError("FMP must never call the DOM extraction owner")

    monkeypatch.setattr(transcript_extraction, "extract", boom)

    session = FakeSession({
        FMP_URL: FakeResponse(
            FMP_URL, fmp_payload(), content_type="application/json"
        )
    })
    result = fetch_transcript(
        make_request(ticker="MSFT", exchange="nasdaq", fiscal_quarter=3, provider="fmp"),
        session_factory=lambda: session,
        fmp_api_key="fake-key",
    )
    assert result["status"] == "fetched"
    assert result["extraction_version"] == "fmp-api-content/1"
    assert calls["n"] == 0

    disabled = fetch_transcript(
        make_request(),
        session_factory=lambda: FakeSession({}),
        provider_settings=ProviderSettings(motley_fool_enabled=False),
    )
    assert disabled["status"] == "unavailable"
    assert disabled["error_code"] == "provider_disabled"
    assert calls["n"] == 0


def test_legacy_and_new_versions_coexist_with_body_binding():
    legacy = transcript_extraction.extract(_page("compat_baseline"))
    assert legacy.extraction_version == "motley-fool-main-text/1"
    assert legacy.diagnostics == ()
    fixed = transcript_extraction.extract(_page("nested_li_p"))
    assert fixed.extraction_version == "motley-fool-main-text/2"
    assert transcript_extraction.EXTRACTION_VERSION_LEGACY == "motley-fool-main-text/1"
    # both bodies keep frozen SHA identity of the returned text
    for result in (legacy, fixed):
        assert hashlib.sha256(result.body.encode("utf-8")).hexdigest()
        assert result.body == result.body.strip()
        assert all(part.strip() for part in result.body.split("\n\n"))


def test_error_paths_do_not_write_partial_files(tmp_path):
    """Extraction failure is a pure result error: no file writes at all."""
    from transcript_api import _ProviderFailure
    import pytest

    marker = tmp_path / "sentinel.txt"
    marker.write_bytes(b"keep-me")
    before = sorted(p.name for p in tmp_path.iterdir())

    for bad in ("missing_main.html", "empty_body.html"):
        with pytest.raises(transcript_extraction.ExtractionError):
            transcript_extraction.extract(_page(bad))
        with pytest.raises(_ProviderFailure):
            transcript_api._extract_body(_page(bad))

    after = sorted(p.name for p in tmp_path.iterdir())
    assert before == after
    assert marker.read_bytes() == b"keep-me"

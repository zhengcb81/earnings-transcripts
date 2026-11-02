"""Explicit raw-response quotas survive every ET provider/serialization layer."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest  # noqa: E402
import retrieval_runtime  # noqa: E402
import transcript_tool  # noqa: E402
import transcript_api as api  # noqa: E402
from tests.test_retrieval_runtime import LAUNCHER, fake_spec  # noqa: E402
from tests.test_transcript_api import (  # noqa: E402
    FMP_URL, Q2_URL, QUOTE_URL, FakeResponse, FakeSession,
    make_request, quote_html, transcript_html,
)

FAKE_KEY = "explicit-budget-test-no-real-key"


def fmp_request(**changes):
    return make_request(provider="fmp", max_body_bytes=1000, **changes)


def fmp_payload(padding=0):
    return b" " * padding + json.dumps([{
        "symbol": "ACME", "year": 2026, "quarter": 2,
        "date": "2026-09-01", "content": "CEO: Original language business progress.",
    }]).encode()


def fetch_fmp(payload, request, *, source_payload=False):
    response = FakeResponse(FMP_URL, payload, content_type="application/json")
    session = FakeSession({FMP_URL: response})
    result = api.fetch_transcript(request, session_factory=lambda: session,
                                  fmp_api_key=FAKE_KEY, include_source_payload=source_payload)
    return result, session, response


def test_explicit_fmp_raw_budget_is_not_min_clamped_to_16mib():
    payload = fmp_payload(17 * 1024 * 1024)
    result, session, response = fetch_fmp(payload, fmp_request(max_response_bytes=20 * 1024 * 1024))
    assert result["status"] == "fetched"
    assert result["provider_payload_sha256"] == hashlib.sha256(payload).hexdigest()
    assert result["content_utf8"] == "CEO: Original language business progress."
    assert len(session.calls) == 1 and response.closed


def test_opt_in_payload_has_no_second_base64_representation_top_cap():
    payload = fmp_payload(19 * 1024 * 1024)
    result, _, response = fetch_fmp(payload, fmp_request(max_response_bytes=20 * 1024 * 1024), source_payload=True)
    assert result["status"] == "fetched"
    assert len(result["provider_payload_base64"]) > 24 * 1024 * 1024
    assert hashlib.sha256(base64.b64decode(result["provider_payload_base64"])).hexdigest() == hashlib.sha256(payload).hexdigest()
    assert response.closed


def test_html_body_wrapper_uses_the_explicit_operation_budget():
    page = b" " * (11 * 1024 * 1024) + transcript_html()
    session = FakeSession({QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(Q2_URL)),
                           Q2_URL: FakeResponse(Q2_URL, page)})
    result = api.fetch_transcript(make_request(max_response_bytes=15 * 1024 * 1024),
        session_factory=lambda: session, provider_settings=api.ProviderSettings(motley_fool_enabled=True))
    assert result["status"] == "fetched"
    assert result["provider_payload_sha256"] == hashlib.sha256(page).hexdigest()
    assert len(session.calls) == 2


def test_listing_wrapper_shares_the_same_explicit_operation_budget():
    listing = b" " * (3 * 1024 * 1024) + quote_html(Q2_URL)
    session = FakeSession({QUOTE_URL: FakeResponse(QUOTE_URL, listing),
                           Q2_URL: FakeResponse(Q2_URL, transcript_html())})
    result = api.fetch_transcript(make_request(max_response_bytes=5 * 1024 * 1024),
        session_factory=lambda: session, provider_settings=api.ProviderSettings(motley_fool_enabled=True))
    assert result["status"] == "fetched" and len(session.calls) == 2


def test_declared_canonical_body_budget_has_no_hidden_10mib_top():
    request = make_request(provider="fmp", max_body_bytes=12 * 1024 * 1024,
                           max_response_bytes=20 * 1024 * 1024)
    result, _, _ = fetch_fmp(fmp_payload(), request)
    assert result["status"] == "fetched"


def test_actual_explicit_raw_quota_still_refuses_overrun_and_closes():
    result, session, response = fetch_fmp(fmp_payload(2000), fmp_request(max_response_bytes=100))
    assert result["status"] == "content_too_large"
    assert len(session.calls) == 1 and response.closed


def test_omitted_raw_quota_keeps_legacy_default_instead_of_unbounded_http():
    result, _, response = fetch_fmp(fmp_payload(17 * 1024 * 1024), fmp_request())
    assert result["status"] == "content_too_large" and response.closed


def test_supervisor_result_bound_tracks_the_explicit_operation_budget(tmp_path, monkeypatch):
    observed = []
    original = retrieval_runtime._load_result_file
    def record(*args, **kwargs):
        observed.append(kwargs["max_bytes"])
        return original(*args, **kwargs)
    monkeypatch.setattr(retrieval_runtime, "_load_result_file", record)
    response = FakeResponse(FMP_URL, fmp_payload(), content_type="application/json")
    request = fmp_request(max_response_bytes=100 * 1024 * 1024)
    outcome = retrieval_runtime.run_retrieval(
        "fetch", request, remaining_seconds=10, requests_left=1,
        response_bytes_left=100 * 1024 * 1024, fmp_api_key=FAKE_KEY,
        launcher=LAUNCHER,
        launcher_spec=fake_spec({FMP_URL: response}, calls_file=tmp_path / "calls.jsonl"),
        temp_root=tmp_path / "workers",
    )
    assert outcome.result is not None and outcome.result["status"] == "fetched"
    assert observed and observed[0] > retrieval_runtime.MAX_RESULT_BYTES
    assert list((tmp_path / "workers").iterdir()) == []


def test_explicit_time_quota_is_not_an_immutable_api_maximum():
    result, session, _ = fetch_fmp(fmp_payload(), fmp_request(timeout_seconds=120))
    assert result["status"] == "fetched" and len(session.calls) == 1


def test_explicit_time_quota_reaches_metadata_listing():
    session = FakeSession({QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(Q2_URL))})
    result = api.list_transcript_candidates(
        ticker="ACME", exchange="nyse", as_of_date="2026-09-30",
        request_id="long-time-listing", timeout_seconds=120,
        session_factory=lambda: session,
        provider_settings=api.ProviderSettings(motley_fool_enabled=True),
    )
    assert result["status"] == "discovered" and len(session.calls) == 1


def test_public_cli_preserves_time_quota_in_real_supervisor(tmp_path, monkeypatch):
    observed = []
    original = retrieval_runtime.run_retrieval
    def run(*args, **kwargs):
        observed.append(kwargs["remaining_seconds"])
        return original(*args, **kwargs)
    monkeypatch.setattr(retrieval_runtime, "run_retrieval", run)
    request = fmp_request(timeout_seconds=120, max_response_bytes=100_000)
    response = FakeResponse(FMP_URL, fmp_payload(), content_type="application/json")
    output = io.StringIO()
    monkeypatch.setenv("FMP_API_KEY", FAKE_KEY)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
    monkeypatch.setattr(sys, "stdout", output)
    code = transcript_tool.main(
        ["--request-stdin", "--include-source-payload"],
        _retrieval_launcher=LAUNCHER,
        _retrieval_spec=fake_spec({FMP_URL: response}),
        _retrieval_temp_root=tmp_path / "workers",
    )
    assert code == 0
    assert json.loads(output.getvalue())["status"] == "fetched"
    assert observed == [120.0]
    assert list((tmp_path / "workers").iterdir()) == []


@pytest.mark.parametrize("timeout", [0, True, math.inf, math.nan],
                         ids=["zero", "bool", "infinity", "nan"])
def test_invalid_time_quota_still_refuses_before_http(timeout):
    result, session, _ = fetch_fmp(fmp_payload(), fmp_request(timeout_seconds=timeout))
    assert result["status"] == "invalid_request" and session.calls == []


@pytest.mark.parametrize("timeout", [0, True, math.inf, math.nan],
                         ids=["zero", "bool", "infinity", "nan"])
def test_invalid_metadata_time_quota_still_refuses_before_http(timeout):
    session = FakeSession({})
    result = api.list_transcript_candidates(
        ticker="ACME", exchange="nyse", as_of_date="2026-09-30",
        request_id="invalid-time-listing", timeout_seconds=timeout,
        session_factory=lambda: session,
        provider_settings=api.ProviderSettings(motley_fool_enabled=True),
    )
    assert result["status"] == "invalid_request" and session.calls == []

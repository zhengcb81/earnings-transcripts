import base64
import hashlib
import json
import subprocess
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent))
from transcript_api import ProviderSettings, discover_transcripts, fetch_transcript, fetch_transcript_candidate


QUOTE_URL = "https://www.fool.com/quote/nyse/acme/"
Q1_URL = "https://www.fool.com/earnings/call-transcripts/2026/05/01/acme-q1-2026-earnings-transcript/"
Q2_URL = "https://www.fool.com/earnings/call-transcripts/2026/09/01/acme-q2-2026-earnings-transcript/"


def make_request(**updates):
    request = {
        "schema_version": "earnings-transcript-request/1",
        "request_id": "test-request-001",
        "ticker": "ACME",
        "exchange": "nyse",
        "fiscal_year": 2026,
        "fiscal_quarter": 2,
        "as_of_date": "2026-09-30",
        "provider": "motley_fool",
        "download_authorized": True,
        "timeout_seconds": 10,
        "max_body_bytes": 1_000_000,
    }
    request.update(updates)
    return request


def make_candidate_request(**updates):
    request = make_request()
    request.pop("schema_version")
    request["schema_version"] = "earnings-transcript-candidate-fetch-request/1"
    request["candidate"] = {
        "provider_document_id": Q2_URL.split("www.fool.com", 1)[1].rstrip("/"),
        "source_url": Q2_URL,
        "published_date": "2026-09-01",
    }
    request.update(updates)
    return request


def quote_html(*urls):
    links = "".join(f'<a href="{url}">transcript</a>' for url in urls)
    return f"<html><body>{links}</body></html>".encode("utf-8")


def transcript_html(title="ACME Q2 2026 Earnings Call"):
    paragraph = (
        "Management described the commercial rollout, customer qualification, "
        "capacity expansion and overseas launch schedule in detail. "
    ) * 4
    return (
        f"<html><body><h1>{title}</h1><main>"
        f"<p>Operator: Welcome to the call.</p><p>{paragraph}</p>"
        "</main></body></html>"
    ).encode("utf-8")


class FakeResponse:
    def __init__(self, url, payload, status_code=200, final_url=None, content_type="text/html; charset=utf-8"):
        self.url = final_url or url
        self.status_code = status_code
        self._payload = payload
        self.closed = False
        self.headers = {"Content-Type": content_type}

    def iter_content(self, chunk_size):
        for i in range(0, len(self._payload), chunk_size):
            yield self._payload[i:i + chunk_size]

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses):
        self.responses = dict(responses)
        self.calls = []
        self.headers = {}

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        pass


def test_fetch_returns_exact_period_body_and_both_hashes():
    quote = quote_html(Q1_URL, Q2_URL)
    page = transcript_html()
    session = FakeSession({
        QUOTE_URL: FakeResponse(QUOTE_URL, quote),
        Q2_URL: FakeResponse(Q2_URL, page),
    })

    result = fetch_transcript(make_request(), session_factory=lambda: session, provider_settings=ProviderSettings(motley_fool_enabled=True))

    assert result["status"] == "fetched"
    assert result["source_url"] == Q2_URL
    assert result["fiscal_period"] == "2026-Q2"
    assert result["content_utf8"].startswith("Operator: Welcome to the call.")
    assert "Management described the commercial rollout" in result["content_utf8"]
    assert result["canonical_content_sha256"] == hashlib.sha256(
        result["content_utf8"].encode("utf-8")
    ).hexdigest()
    assert result["provider_payload_sha256"] == hashlib.sha256(page).hexdigest()
    assert len(session.calls) == 2
    assert all(call[1]["allow_redirects"] is False for call in session.calls)


def test_as_of_date_excludes_later_publication_without_fetching_body():
    session = FakeSession({QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(Q2_URL))})
    result = fetch_transcript(
        make_request(as_of_date="2026-08-31"), session_factory=lambda: session, provider_settings=ProviderSettings(motley_fool_enabled=True)
    )
    assert result["status"] == "not_found"
    assert len(session.calls) == 1


def test_discovery_returns_exact_candidate_metadata_without_fetching_transcript_body():
    session = FakeSession({QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(Q2_URL))})
    result = discover_transcripts(make_request(), session_factory=lambda: session, provider_settings=ProviderSettings(motley_fool_enabled=True))
    assert result["schema_version"] == "earnings-transcript-discovery-result/1"
    assert result["status"] == "discovered"
    assert result["candidate_count"] == 1
    assert result["candidates"] == [{
        "provider": "motley_fool",
        "ticker": "ACME",
        "exchange": "nyse",
        "fiscal_period": "2026-Q2",
        "provider_document_id": "/earnings/call-transcripts/2026/09/01/acme-q2-2026-earnings-transcript",
        "source_url": Q2_URL,
        "published_date": "2026-09-01",
    }]
    assert [call[0] for call in session.calls] == [QUOTE_URL]


def test_discovery_ambiguous_candidates_are_listed_without_body_fetch():
    second = "https://www.fool.com/earnings/call-transcripts/2026/09/02/acme-q2-2026-special-transcript/"
    session = FakeSession({QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(Q2_URL, second))})
    result = discover_transcripts(make_request(), session_factory=lambda: session, provider_settings=ProviderSettings(motley_fool_enabled=True))
    assert result["status"] == "ambiguous"
    assert result["candidate_count"] == 2
    assert len(result["candidates"]) == 2
    assert [call[0] for call in session.calls] == [QUOTE_URL]


def test_candidate_fetch_requests_only_the_bound_candidate_and_returns_raw_bytes():
    page = transcript_html()
    session = FakeSession({Q2_URL: FakeResponse(Q2_URL, page)})
    result = fetch_transcript_candidate(
        make_candidate_request(),
        session_factory=lambda: session, provider_settings=ProviderSettings(motley_fool_enabled=True),
        include_source_payload=True,
    )
    assert result["status"] == "fetched"
    assert result["schema_version"] == "earnings-transcript-result/2"
    assert base64.b64decode(result["provider_payload_base64"]) == page
    assert result["source_url"] == Q2_URL
    assert result["effective_url"] == Q2_URL
    assert "content_utf8" not in result
    assert [call[0] for call in session.calls] == [Q2_URL]


def test_candidate_fetch_rejects_wrong_period_or_cross_host_before_network():
    session = FakeSession({})
    wrong_period = make_candidate_request(
        fiscal_quarter=1,
        candidate={
            "provider_document_id": "/earnings/call-transcripts/2026/09/01/acme-q2-2026-earnings-transcript",
            "source_url": Q2_URL,
            "published_date": "2026-09-01",
        },
    )
    result = fetch_transcript_candidate(wrong_period, session_factory=lambda: session, provider_settings=ProviderSettings(motley_fool_enabled=True))
    assert result["status"] == "invalid_request"
    evil_host = make_candidate_request(candidate={
        "provider_document_id": "evil",
        "source_url": "https://example.com/acme-q2-2026.html",
        "published_date": "2026-09-01",
    })
    result = fetch_transcript_candidate(evil_host, session_factory=lambda: session, provider_settings=ProviderSettings(motley_fool_enabled=True))
    assert result["status"] == "invalid_request"
    assert session.calls == []


def test_candidate_fetch_without_download_authorization_makes_no_network_call():
    session = FakeSession({})
    result = fetch_transcript_candidate(
        make_candidate_request(download_authorized=False),
        session_factory=lambda: session, provider_settings=ProviderSettings(motley_fool_enabled=True),
        include_source_payload=True,
    )
    assert result["status"] == "not_authorized"
    assert result["schema_version"] == "earnings-transcript-result/2"
    assert session.calls == []


def test_two_exact_period_candidates_are_ambiguous_and_neither_is_fetched():
    second = "https://www.fool.com/earnings/call-transcripts/2026/09/02/acme-q2-2026-earnings-transcript/"
    session = FakeSession({QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(Q2_URL, second))})
    result = fetch_transcript(make_request(), session_factory=lambda: session, provider_settings=ProviderSettings(motley_fool_enabled=True))
    assert result["status"] == "ambiguous"
    assert result["candidate_count"] == 2
    assert len(session.calls) == 1


def test_unknown_provider_fails_closed_without_network():
    session = FakeSession({})
    result = fetch_transcript(
        make_request(provider="unknown"), session_factory=lambda: session, provider_settings=ProviderSettings(motley_fool_enabled=True)
    )
    assert result["status"] == "unsupported"
    assert session.calls == []


def test_missing_authorization_fails_closed_without_network():
    session = FakeSession({})
    result = fetch_transcript(
        make_request(download_authorized=False), session_factory=lambda: session, provider_settings=ProviderSettings(motley_fool_enabled=True)
    )
    assert result["status"] == "not_authorized"
    assert session.calls == []


def test_non_fool_redirect_is_rejected_before_follow_up_request():
    redirected = FakeResponse(QUOTE_URL, b"", status_code=302)
    redirected.headers["Location"] = "https://example.com/steal"
    session = FakeSession({QUOTE_URL: redirected})
    result = fetch_transcript(make_request(), session_factory=lambda: session, provider_settings=ProviderSettings(motley_fool_enabled=True))
    assert result["status"] == "provenance_rejected"
    assert len(session.calls) == 1


def test_body_size_limit_is_enforced():
    quote = quote_html(Q2_URL)
    page = transcript_html()
    session = FakeSession({
        QUOTE_URL: FakeResponse(QUOTE_URL, quote),
        Q2_URL: FakeResponse(Q2_URL, page),
    })
    result = fetch_transcript(
        make_request(max_body_bytes=128), session_factory=lambda: session, provider_settings=ProviderSettings(motley_fool_enabled=True)
    )
    assert result["status"] == "content_too_large"


def test_request_schema_rejects_unknown_fields():
    session = FakeSession({})
    result = fetch_transcript(
        make_request(unreviewed_option=True), session_factory=lambda: session, provider_settings=ProviderSettings(motley_fool_enabled=True)
    )
    assert result["status"] == "invalid_request"
    assert session.calls == []


def test_cli_without_request_network_intent_does_not_use_network():
    tool = Path(__file__).parent.parent / "transcript_tool.py"
    payload = make_request(download_authorized=False)
    completed = subprocess.run(
        [sys.executable, str(tool), "--request-stdin"],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )
    assert completed.returncode == 0
    assert completed.stderr == ""
    assert json.loads(completed.stdout)["status"] == "not_authorized"


def test_cli_legacy_flag_cannot_override_missing_request_intent():
    tool = Path(__file__).parent.parent / "transcript_tool.py"
    completed = subprocess.run(
        [sys.executable, str(tool), "--request-stdin", "--allow-download", "--include-source-payload"],
        input=json.dumps(make_request(download_authorized=False)),
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )
    assert completed.returncode == 0
    result = json.loads(completed.stdout)
    assert result["schema_version"] == "earnings-transcript-result/2"
    assert result["status"] == "not_authorized"
    assert completed.stderr == ""


def test_cli_candidate_fetch_legacy_flag_cannot_override_request_intent():
    tool = Path(__file__).parent.parent / "transcript_tool.py"
    completed = subprocess.run(
        [sys.executable, str(tool), "--request-stdin", "--operation", "fetch-candidate", "--allow-download", "--include-source-payload"],
        input=json.dumps(make_candidate_request(download_authorized=False)),
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )
    assert completed.returncode == 0
    result = json.loads(completed.stdout)
    assert result["status"] == "not_authorized"
    assert result["schema_version"] == "earnings-transcript-result/2"
    assert completed.stderr == ""

def test_auto_exchange_checks_both_pages_before_declaring_unique():
    second = "https://www.fool.com/earnings/call-transcripts/2026/09/02/acme-q2-2026-special-transcript/"
    nasdaq_url = "https://www.fool.com/quote/nasdaq/acme/"
    session = FakeSession({
        nasdaq_url: FakeResponse(nasdaq_url, quote_html(Q2_URL)),
        QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(second)),
    })
    result = fetch_transcript(
        make_request(exchange="auto"), session_factory=lambda: session, provider_settings=ProviderSettings(motley_fool_enabled=True)
    )
    assert result["status"] == "ambiguous"
    assert result["candidate_count"] == 2
    assert [call[0] for call in session.calls] == [nasdaq_url, QUOTE_URL]

FMP_URL = "https://financialmodelingprep.com/stable/earning-call-transcript"


def fmp_payload(*, symbol="MSFT", period="Q3", year=2026, date="2026-07-22"):
    content = (
        "Management discussed customer demand, product launches, commercial execution, "
        "international expansion, data-center capacity, margins, and operating risks. "
    ) * 5
    return json.dumps([{
        "symbol": symbol,
        "period": period,
        "year": year,
        "date": date,
        "content": content,
    }]).encode("utf-8")


def test_fmp_fetches_exact_period_and_keeps_credentials_out_of_provenance():
    page = fmp_payload()
    session = FakeSession({FMP_URL: FakeResponse(FMP_URL, page)})
    result = fetch_transcript(
        make_request(ticker="MSFT", exchange="nasdaq", fiscal_quarter=3, provider="fmp"),
        session_factory=lambda: session, provider_settings=ProviderSettings(motley_fool_enabled=True),
        fmp_api_key="fake-secret-for-test",
    )
    assert result["status"] == "fetched"
    assert result["provider"] == "fmp"
    assert result["fiscal_period"] == "2026-Q3"
    assert result["call_date"] == "2026-07-22"
    assert result["publication_date"] is None
    assert result["as_of_cutoff_verified"] is False
    assert result["provider_document_id"] == "fmp:MSFT:2026:Q3:2026-07-22"
    assert "apikey" not in result["source_url"].lower()
    assert "fake-secret-for-test" not in json.dumps(result)
    assert result["provider_payload_sha256"] == hashlib.sha256(page).hexdigest()
    assert result["canonical_content_sha256"] == hashlib.sha256(
        result["content_utf8"].encode("utf-8")
    ).hexdigest()
    assert session.calls[0][1]["params"] == {
        "symbol": "MSFT", "year": 2026, "quarter": 3, "apikey": "fake-secret-for-test"
    }


def test_motley_fool_opt_in_returns_exact_bounded_original_bytes_and_effective_url():
    redirected_url = Q2_URL + "?session-secret=must-not-leak#fragment"
    redirect = FakeResponse(Q2_URL, b"", status_code=302)
    redirect.headers["Location"] = "?session-secret=must-not-leak#fragment"
    page = transcript_html()
    session = FakeSession({
        QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(Q2_URL)),
        Q2_URL: redirect,
        redirected_url: FakeResponse(redirected_url, page),
    })

    result = fetch_transcript(
        make_request(), session_factory=lambda: session, provider_settings=ProviderSettings(motley_fool_enabled=True), include_source_payload=True
    )

    assert result["status"] == "fetched"
    assert result["schema_version"] == "earnings-transcript-result/2"
    assert result["provider_payload_encoding"] == "base64"
    assert base64.b64decode(result["provider_payload_base64"]) == page
    assert result["provider_payload_mime_type"] == "text/html"
    assert result["effective_url"] == Q2_URL
    assert result["http_status"] == 200
    assert result["adapter_name"] == "earnings-transcripts-transcript-tool"
    assert result["adapter_version"] == "1.0.0"
    assert result["retrieved_at"].endswith("Z")
    assert result["provider_payload_sha256"] == hashlib.sha256(page).hexdigest()
    assert "content_utf8" not in result
    assert "session-secret" not in json.dumps(result)


def test_motley_fool_opt_in_rejects_non_html_payload_mime():
    page = transcript_html()
    session = FakeSession({
        QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(Q2_URL)),
        Q2_URL: FakeResponse(Q2_URL, page, content_type="application/octet-stream"),
    })
    result = fetch_transcript(
        make_request(), session_factory=lambda: session, provider_settings=ProviderSettings(motley_fool_enabled=True), include_source_payload=True
    )
    assert result["status"] == "provenance_rejected"
    assert result["schema_version"] == "earnings-transcript-result/2"


def test_fmp_opt_in_returns_exact_json_bytes_without_credentials_or_duplicate_text():
    page = fmp_payload()
    session = FakeSession({
        FMP_URL: FakeResponse(
            FMP_URL, page, content_type="application/json; charset=utf-8"
        )
    })
    result = fetch_transcript(
        make_request(ticker="MSFT", exchange="nasdaq", fiscal_quarter=3, provider="fmp"),
        session_factory=lambda: session, provider_settings=ProviderSettings(motley_fool_enabled=True),
        fmp_api_key="fake-secret-for-test",
        include_source_payload=True,
    )
    assert result["status"] == "fetched"
    assert result["schema_version"] == "earnings-transcript-result/2"
    assert base64.b64decode(result["provider_payload_base64"]) == page
    assert result["provider_payload_mime_type"] == "application/json"
    assert result["effective_url"] == (
        f"{FMP_URL}?symbol=MSFT&year=2026&quarter=3"
    )
    assert "content_utf8" not in result
    assert "fake-secret-for-test" not in json.dumps(result)
    assert "apikey" not in result["effective_url"].lower()
    assert result["http_status"] == 200
    assert result["adapter_version"] == "1.0.0"
    assert result["retrieved_at"].endswith("Z")


def test_fmp_opt_in_rejects_non_json_payload_mime():
    session = FakeSession({
        FMP_URL: FakeResponse(FMP_URL, fmp_payload(), content_type="text/plain")
    })
    result = fetch_transcript(
        make_request(ticker="MSFT", exchange="nasdaq", fiscal_quarter=3, provider="fmp"),
        session_factory=lambda: session, provider_settings=ProviderSettings(motley_fool_enabled=True),
        fmp_api_key="fake-key",
        include_source_payload=True,
    )
    assert result["status"] == "provenance_rejected"


def test_fmp_without_key_is_unavailable_and_makes_no_request():
    session = FakeSession({})
    result = fetch_transcript(
        make_request(provider="fmp"), session_factory=lambda: session, provider_settings=ProviderSettings(motley_fool_enabled=True), fmp_api_key=None
    )
    assert result["status"] == "unavailable"
    assert result["error_code"] == "provider_credentials_missing"
    assert session.calls == []


def test_fmp_rejects_response_for_wrong_quarter():
    session = FakeSession({FMP_URL: FakeResponse(FMP_URL, fmp_payload(period="Q2"))})
    result = fetch_transcript(
        make_request(ticker="MSFT", exchange="nasdaq", provider="fmp", fiscal_quarter=3),
        session_factory=lambda: session, provider_settings=ProviderSettings(motley_fool_enabled=True),
        fmp_api_key="fake-key",
    )
    assert result["status"] == "provenance_rejected"


def test_fmp_excludes_call_after_requested_as_of_date():
    session = FakeSession({FMP_URL: FakeResponse(FMP_URL, fmp_payload())})
    result = fetch_transcript(
        make_request(
            ticker="MSFT", exchange="nasdaq", fiscal_quarter=3,
            provider="fmp", as_of_date="2026-07-21",
        ),
        session_factory=lambda: session, provider_settings=ProviderSettings(motley_fool_enabled=True),
        fmp_api_key="fake-key",
    )
    assert result["status"] == "not_found"


def test_fmp_rejects_redirects():
    redirected = FakeResponse(FMP_URL, b"", status_code=302)
    redirected.headers["Location"] = "https://example.com/leak"
    session = FakeSession({FMP_URL: redirected})
    result = fetch_transcript(
        make_request(provider="fmp"), session_factory=lambda: session, provider_settings=ProviderSettings(motley_fool_enabled=True), fmp_api_key="fake-key"
    )
    assert result["status"] == "provenance_rejected"
    assert len(session.calls) == 1


def test_fmp_http_error_exposes_only_status_code_not_response_body():
    session = FakeSession({FMP_URL: FakeResponse(FMP_URL, b"sensitive provider error", status_code=403)})
    result = fetch_transcript(
        make_request(provider="fmp"), session_factory=lambda: session, provider_settings=ProviderSettings(motley_fool_enabled=True), fmp_api_key="fake-secret"
    )
    assert result["status"] == "unavailable"
    assert result["error_code"] == "provider_entitlement_denied"
    assert "sensitive provider error" not in json.dumps(result)
    assert "fake-secret" not in json.dumps(result)


def test_fmp_enforces_canonical_content_byte_limit():
    session = FakeSession({FMP_URL: FakeResponse(FMP_URL, fmp_payload())})
    result = fetch_transcript(
        make_request(provider="fmp", ticker="MSFT", exchange="nasdaq", fiscal_quarter=3, max_body_bytes=128),
        session_factory=lambda: session, provider_settings=ProviderSettings(motley_fool_enabled=True),
        fmp_api_key="fake-key",
    )
    assert result["status"] == "content_too_large"


def test_cli_single_request_intent_runs_real_fmp_chain(monkeypatch, tmp_path):
    """One explicit request is enough; the CLI must use the real API and fake HTTP."""
    import io
    import transcript_tool
    import translator

    def no_translation(*args, **kwargs):
        raise AssertionError("transcript tool must not initialize translation")

    monkeypatch.setattr(translator.TranslatorFactory, "create", no_translation)
    monkeypatch.chdir(tmp_path)

    page = fmp_payload()
    session = FakeSession({FMP_URL: FakeResponse(FMP_URL, page, content_type="application/json")})
    request = make_request(
        ticker="MSFT", exchange="nasdaq", fiscal_quarter=3, provider="fmp"
    )
    output = io.StringIO()
    monkeypatch.setenv("FMP_API_KEY", "fake-key-for-test")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
    monkeypatch.setattr(sys, "stdout", output)

    exit_code = transcript_tool.main(
        ["--request-stdin", "--include-source-payload"],
        _session_factory=lambda: session,
    )

    result = json.loads(output.getvalue())
    assert exit_code == 0
    assert result["status"] == "fetched"
    assert result["schema_version"] == "earnings-transcript-result/2"
    assert base64.b64decode(result["provider_payload_base64"]) == page
    assert result["provider_payload_sha256"] == hashlib.sha256(page).hexdigest()
    assert len(session.calls) == 1
    assert "content_utf8" not in result
    assert "fake-key-for-test" not in output.getvalue()
    assert list(tmp_path.iterdir()) == []


def test_motley_fool_is_disabled_by_default_at_all_three_api_entries():
    import transcript_api

    cases = (
        (transcript_api.fetch_transcript, make_request()),
        (transcript_api.discover_transcripts, make_request()),
        (transcript_api.fetch_transcript_candidate, make_candidate_request()),
    )
    for function, request in cases:
        session = FakeSession({})
        result = function(request, session_factory=lambda: session)
        assert result["status"] == "unavailable"
        assert result["error_code"] == "provider_disabled"
        assert session.calls == []


def test_fmp_missing_credentials_and_402_have_named_unavailable_results():
    import transcript_api

    request = make_request(provider="fmp")
    session = FakeSession({})
    missing = transcript_api.fetch_transcript(
        request, session_factory=lambda: session, fmp_api_key=None
    )
    assert missing["status"] == "unavailable"
    assert missing["error_code"] == "provider_credentials_missing"
    assert session.calls == []

    no_intent = transcript_api.fetch_transcript(
        make_request(provider="fmp", download_authorized=False),
        session_factory=lambda: session,
        fmp_api_key="fake-key",
    )
    assert no_intent["status"] == "not_authorized"
    assert no_intent["provider"] == "fmp"
    assert session.calls == []

    session = FakeSession({FMP_URL: FakeResponse(FMP_URL, b"", status_code=402)})
    denied = transcript_api.fetch_transcript(
        request, session_factory=lambda: session, fmp_api_key="fake-key"
    )
    assert denied["status"] == "unavailable"
    assert denied["error_code"] == "provider_entitlement_required"
    assert len(session.calls) == 1


def test_fmp_rate_limit_is_distinct_from_bad_provider_response():
    import transcript_api

    session = FakeSession({FMP_URL: FakeResponse(FMP_URL, b"", status_code=429)})
    result = transcript_api.fetch_transcript(
        make_request(provider="fmp"),
        session_factory=lambda: session,
        fmp_api_key="fake-key",
    )
    assert result["status"] == "rate_limited"
    assert result["error_code"] == "provider_http_429"
    assert len(session.calls) == 1


def test_fy_only_request_makes_zero_http_calls():
    import transcript_api

    request = make_request(provider="fmp")
    request.pop("fiscal_quarter")
    session = FakeSession({})
    result = transcript_api.fetch_transcript(
        request, session_factory=lambda: session, fmp_api_key="fake-key"
    )
    assert result["status"] == "invalid_request"
    assert result["error_code"] == "request_schema"
    assert session.calls == []


def test_fmp_rejects_bad_effective_host_before_parsing_payload():
    import transcript_api

    session = FakeSession({
        FMP_URL: FakeResponse(
            FMP_URL, fmp_payload(), final_url="https://other.example/transcript",
            content_type="application/json",
        )
    })
    result = transcript_api.fetch_transcript(
        make_request(ticker="MSFT", exchange="nasdaq", fiscal_quarter=3, provider="fmp"),
        session_factory=lambda: session,
        fmp_api_key="fake-key",
        include_source_payload=True,
    )
    assert result["status"] == "provenance_rejected"
    assert len(session.calls) == 1


def test_fmp_timeout_and_raw_response_limit_are_named_failures():
    import requests
    import transcript_api

    request = make_request(provider="fmp", ticker="MSFT", exchange="nasdaq", fiscal_quarter=3)
    timed_out = FakeSession({FMP_URL: requests.Timeout()})
    timeout_result = transcript_api.fetch_transcript(
        request, session_factory=lambda: timed_out, fmp_api_key="fake-key"
    )
    assert timeout_result["status"] == "deadline_exceeded"
    assert timeout_result["error_code"] == "provider_deadline"
    assert len(timed_out.calls) == 1

    oversized = FakeSession({
        FMP_URL: FakeResponse(
            FMP_URL, b"x" * (transcript_api.MAX_FMP_RESPONSE_BYTES + 1),
            content_type="application/json",
        )
    })
    size_result = transcript_api.fetch_transcript(
        request, session_factory=lambda: oversized, fmp_api_key="fake-key",
        include_source_payload=True,
    )
    assert size_result["status"] == "content_too_large"
    assert size_result["error_code"] == "byte_limit"
    assert len(oversized.calls) == 1


def test_same_exact_fmp_request_has_stable_content_identity():
    import transcript_api

    request = make_request(provider="fmp", ticker="MSFT", exchange="nasdaq", fiscal_quarter=3)
    payload = fmp_payload()
    results = []
    for _ in range(2):
        session = FakeSession({
            FMP_URL: FakeResponse(FMP_URL, payload, content_type="application/json")
        })
        result = transcript_api.fetch_transcript(
            request, session_factory=lambda: session, fmp_api_key="fake-key",
            include_source_payload=True,
        )
        assert result["status"] == "fetched"
        assert len(session.calls) == 1
        results.append({k: v for k, v in result.items() if k != "retrieved_at"})
    assert results[0] == results[1]


def test_real_cli_v2_golden_files_match_current_serializer():
    from tests.generate_transcript_goldens import GOLDEN_DIR, build_goldens

    generated = build_goldens()
    assert generated
    for name, content in generated.items():
        assert (GOLDEN_DIR / name).read_bytes() == content


def test_cli_invalid_json_and_usage_have_stable_exit_codes_without_network():
    tool = Path(__file__).parent.parent / "transcript_tool.py"
    malformed = subprocess.run(
        [sys.executable, str(tool), "--request-stdin", "--include-source-payload"],
        input="{bad",
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )
    assert malformed.returncode == 0
    assert malformed.stderr == ""
    assert json.loads(malformed.stdout) == {
        "schema_version": "earnings-transcript-result/2",
        "request_id": None,
        "status": "invalid_request",
        "error_code": "invalid_json",
        "provider": "motley_fool",
    }

    misuse = subprocess.run(
        [sys.executable, str(tool), "--request-stdin", "--operation", "wrong"],
        input="",
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )
    assert misuse.returncode == 2
    assert misuse.stdout == ""

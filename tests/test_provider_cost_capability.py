"""Subscription entitlement, operation capability and request fee are distinct."""

import io
import json
import sys

import pytest

from .test_transcript_api import FakeResponse, FakeSession, FMP_URL, fmp_payload, make_request
from transcript_api import ProviderSettings, fetch_transcript


def test_fmp_operation_capability_is_not_account_entitlement():
    from transcript_api import provider_operation_capability
    exact = provider_operation_capability("fmp", "fetch", settings=ProviderSettings())
    assert exact["enabled"] and exact["supported"]
    assert exact["incremental_cost_usd"] == "0"
    assert exact["billing_model"] == "subscription_quota"
    assert exact["entitlement"] == "runtime_unknown"
    discovery = provider_operation_capability("fmp", "discover", settings=ProviderSettings())
    assert not discovery["supported"]
    assert provider_operation_capability("fmp", "fetch", settings=ProviderSettings(fmp_enabled=False))["enabled"] is False


@pytest.mark.parametrize("ceiling", ["0", "0.00", "1.00"])
def test_exact_subscription_fetch_accepts_explicit_zero_incremental_fee(ceiling):
    body = fmp_payload()
    session = FakeSession({FMP_URL: FakeResponse(FMP_URL, body, content_type="application/json")})
    result = fetch_transcript(make_request(ticker="MSFT", exchange="nasdaq", fiscal_quarter=3,
        provider="fmp", max_cost_usd=ceiling), session_factory=lambda: session, fmp_api_key="fake-key")
    assert result["status"] == "fetched"
    assert len(session.calls) == 1


@pytest.mark.parametrize("ceiling", [False, 0, -1, "-1", "NaN", "Infinity", "unknown"])
def test_invalid_fee_ceiling_rejected_before_http(ceiling):
    session = FakeSession({})
    result = fetch_transcript(make_request(provider="fmp", max_cost_usd=ceiling),
        session_factory=lambda: session, fmp_api_key="fake-key")
    assert result["status"] == "invalid_request"
    assert not session.calls


def test_incrementally_charged_operation_does_not_use_zero_ceiling(monkeypatch):
    import transcript_api
    monkeypatch.setattr(transcript_api, "provider_operation_capability", lambda *a, **kw: {
        "enabled": True, "supported": True, "incremental_cost_usd": "0.25"}, raising=False)
    session = FakeSession({})
    result = fetch_transcript(make_request(provider="fmp", max_cost_usd="0.00"),
        session_factory=lambda: session, fmp_api_key="fake-key")
    assert result["error_code"] == "provider_cost_budget_exceeded"
    assert not session.calls


def test_capabilities_cli_is_local_and_does_not_read_request_or_key(monkeypatch):
    import transcript_tool
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setenv("FMP_API_KEY", "fake-secret-never-output")
    assert transcript_tool.main(["--capabilities"]) == 0
    result = json.loads(output.getvalue())
    assert result["schema_version"] == "earnings-provider-capabilities/1"
    assert result["providers"]["fmp"]["fetch"]["entitlement"] == "runtime_unknown"
    assert "fake-secret" not in output.getvalue()


@pytest.mark.parametrize("usage,complete", [
    ({"requests_used": 0, "response_bytes_used": 0, "exhausted": None}, True),
    ({"requests_used": 1, "response_bytes_used": 17, "exhausted": None}, True),
    (None, False),
])
def test_supervisor_reports_actual_usage_separately_from_content_schema(monkeypatch, capsys, usage, complete):
    from types import SimpleNamespace
    import transcript_tool
    request = make_request(provider="fmp", max_cost_usd="0.00")
    result = {"schema_version": "earnings-transcript-result/2", "request_id": request["request_id"],
              "status": "unavailable", "error_code": "provider_entitlement_required", "provider": "fmp"}
    outcome = SimpleNamespace(result=result if complete else None, usage=usage, reason=None if complete else "deadline")
    monkeypatch.setattr(transcript_tool.retrieval_runtime, "run_retrieval", lambda *a, **kw: outcome)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
    assert transcript_tool.main(["--request-stdin", "--include-source-payload", "--report-usage"]) == 0
    captured = capsys.readouterr()
    receipt = json.loads(captured.err)
    assert receipt == {"schema_version": "earnings-retrieval-usage/1", "request_id": request["request_id"],
                       "usage_complete": complete, "usage": usage}
    content = json.loads(captured.out)
    assert "usage" not in content and "provider_calls" not in content

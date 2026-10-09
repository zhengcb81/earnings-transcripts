"""Independent operation raw-response quotas and observed consumption, offline."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import retrieval_runtime  # noqa: E402 - explicit repository bootstrap
import transcript_api as api  # noqa: E402 - explicit repository bootstrap
import transcript_tool  # noqa: E402 - explicit repository bootstrap
from retrieval_budget import (  # noqa: E402 - explicit repository bootstrap
    BatchBudget,
    BatchBudgetExceeded,
    UsageCounter,
    _BudgetResponse,
    _BudgetSession,
)
from tests.test_transcript_api import (  # noqa: E402 - explicit repository bootstrap
    FakeSession,
    FakeResponse,
    FMP_URL,
    Q2_URL,
    QUOTE_URL,
    make_request,
    make_candidate_request,
    quote_html,
    transcript_html,
)
from tests.test_retrieval_runtime import LAUNCHER, StallResponse, fake_spec, get_urls  # noqa: E402 - explicit repository bootstrap
from tests.test_retrieval_cli_e2e import run_tool  # noqa: E402 - explicit repository bootstrap

KEY = "offline-not-a-real-fmp-key"
ENABLED = api.ProviderSettings(motley_fool_enabled=True)


def padded_payload(size=20386, *, ticker="NVDA", quarter=2, content="A" * 286):
    row = dict(
        symbol=ticker,
        year=2026,
        period=f"Q{quarter}",
        date="2026-09-01",
        content=content,
        padding="",
    )
    payload = json.dumps([row], ensure_ascii=False).encode("utf-8")
    assert size >= len(payload)
    row["padding"] = "x" * (size - len(payload))
    result = json.dumps([row], ensure_ascii=False).encode("utf-8")
    assert len(result) == size
    return result


def fmp_request(**changes):
    request = make_request(
        ticker="NVDA", exchange="nasdaq", provider="fmp", max_body_bytes=1000
    )
    request.update(changes)
    return request


class Chunks(FakeResponse):
    def __init__(self, chunks):
        super().__init__(FMP_URL, b"", content_type="application/json")
        self.chunks = chunks
        self.read_chunks = 0
        self.requested_sizes = []

    def iter_content(self, chunk_size):
        self.requested_sizes.append(chunk_size)
        for chunk in self.chunks:
            self.read_chunks += 1
            yield chunk


def test_oversized_chunk_is_counted_before_budget_failure():
    budget = BatchBudget(1, 10, 1024, 0)
    budget.record_request()
    with pytest.raises(BatchBudgetExceeded, match="response_bytes"):
        budget.record_response(20386)
    assert budget.usage_snapshot() == dict(
        requests_used=1, response_bytes_used=20386, exhausted="response_bytes"
    )
    assert budget.report()["response_bytes_used"] == 20386


def test_second_chunk_is_counted_and_third_is_not_read():
    response = Chunks([b"a" * 700, b"b" * 700, b"never-read"])
    budget = BatchBudget(1, 10, 1024, 0)
    wrapped = _BudgetResponse(response, budget)
    iterator = wrapped.iter_content(65536)
    assert len(next(iterator)) == 700
    with pytest.raises(BatchBudgetExceeded, match="response_bytes"):
        next(iterator)
    assert response.read_chunks == 2
    assert budget.usage_snapshot()["response_bytes_used"] == 1400


@pytest.mark.parametrize(
    "requests_left,bytes_left,reason",
    [(None, 1024, "response_bytes"), (1, None, "request_limit")],
)
def test_optional_request_and_response_quotas_are_independent(
    requests_left, bytes_left, reason
):
    budget = BatchBudget(requests_left, 10, bytes_left, 0)
    budget.check_request()
    budget.record_request()
    if reason == "response_bytes":
        with pytest.raises(BatchBudgetExceeded, match=reason):
            budget.record_response(1025)
    else:
        budget.record_response(20386)
        with pytest.raises(BatchBudgetExceeded, match=reason):
            budget.check_request()
    assert budget.usage_snapshot()["requests_used"] == 1


@pytest.mark.parametrize(
    "limit,expected", [(1024, "fetched"), (1023, "content_too_large")]
)
def test_raw_boundary_and_small_canonical_content_are_independent(limit, expected):
    raw = padded_payload(1024)
    response = Chunks([raw])
    session = FakeSession({FMP_URL: response})
    result = api.fetch_transcript(
        fmp_request(max_response_bytes=limit),
        session_factory=lambda: session,
        fmp_api_key=KEY,
        include_source_payload=True,
    )
    assert result["status"] == expected
    assert len(session.calls) == 1
    assert response.closed
    if expected == "fetched":
        assert base64.b64decode(result["provider_payload_base64"]) == raw
        assert result["provider_payload_sha256"] == hashlib.sha256(raw).hexdigest()
    else:
        assert result["error_code"] == "byte_limit"
        assert "provider_payload_base64" not in result


def test_injected_stream_stop_reports_observed_not_clamped_bytes():
    raw = padded_payload()
    response = Chunks([raw, b"not-read"])
    budget = UsageCounter()
    session = _BudgetSession(FakeSession({FMP_URL: response}), budget)
    result = api.fetch_transcript(
        fmp_request(max_response_bytes=1024),
        session_factory=lambda: session,
        fmp_api_key=KEY,
    )
    assert result["status"] == "content_too_large"
    assert response.read_chunks == 1
    assert budget.usage_snapshot() == dict(
        requests_used=1, response_bytes_used=len(raw), exhausted="response_bytes"
    )
    assert response.requested_sizes[0] <= 1024


@pytest.mark.parametrize("bad", [True, False, 0, -1, 1.5, "1024", None])
def test_response_quota_is_optional_but_supplied_value_is_positive_nonbool_integer(bad):
    session = FakeSession({})
    result = api.fetch_transcript(
        fmp_request(max_response_bytes=bad),
        session_factory=lambda: session,
        fmp_api_key=KEY,
    )
    assert result["status"] == "invalid_request"
    assert session.calls == []


def test_128mib_operation_quota_does_not_change_single_response_hardcap(monkeypatch):
    monkeypatch.setattr(api, "MAX_FMP_RESPONSE_BYTES", 512)
    response = Chunks([padded_payload(1024)])
    budget = UsageCounter()
    session = _BudgetSession(FakeSession({FMP_URL: response}), budget)
    result = api.fetch_transcript(
        fmp_request(max_response_bytes=128 * 1024 * 1024),
        session_factory=lambda: session,
        fmp_api_key=KEY,
    )
    assert result["status"] == "content_too_large"
    assert result["error_code"] == "byte_limit"
    assert budget.usage_snapshot()["response_bytes_used"] == 1024
    assert budget.usage_snapshot()["exhausted"] is None
    assert len(session._inner.calls) == 1


def test_large_operation_and_small_body_quota_are_separate():
    raw = padded_payload(1024)
    session = FakeSession(
        {FMP_URL: FakeResponse(FMP_URL, raw, content_type="application/json")}
    )
    request = fmp_request(max_response_bytes=128 * 1024 * 1024)
    request["max_body_bytes"] = 285
    result = api.fetch_transcript(
        request, session_factory=lambda: session, fmp_api_key=KEY
    )
    assert result["status"] == "content_too_large"
    assert len(session.calls) == 1


def test_omitted_new_quota_retains_legacy_body_behavior():
    raw = padded_payload()
    session = FakeSession(
        {FMP_URL: FakeResponse(FMP_URL, raw, content_type="application/json")}
    )
    result = api.fetch_transcript(
        fmp_request(), session_factory=lambda: session, fmp_api_key=KEY
    )
    assert result["status"] == "fetched"
    assert result["content_bytes"] == 286


@pytest.mark.parametrize("operation", ["fetch", "discover"])
def test_motley_requests_share_operation_quota(operation):
    quote = quote_html(Q2_URL)
    page = transcript_html()
    response = Chunks([quote])
    response.url = QUOTE_URL
    response.headers = {"Content-Type": "text/html"}
    body = Chunks([page])
    body.url = Q2_URL
    body.headers = {"Content-Type": "text/html"}
    responses = {QUOTE_URL: response, Q2_URL: body}
    request = make_request(max_response_bytes=len(quote) + len(page) - 1)
    if operation == "discover":
        other = "https://www.fool.com/quote/nasdaq/acme/"
        response2 = Chunks([quote])
        response2.url = other
        response2.headers = {"Content-Type": "text/html"}
        responses = {QUOTE_URL: response, other: response2}
        request.update(exchange="auto", max_response_bytes=2 * len(quote) - 1)
    budget = UsageCounter()
    inner = FakeSession(responses)
    session = _BudgetSession(inner, budget)
    result = getattr(
        api, "fetch_transcript" if operation == "fetch" else "discover_transcripts"
    )(request, session_factory=lambda: session, provider_settings=ENABLED)
    assert result["status"] == "content_too_large"
    assert len(inner.calls) == 2
    assert budget.usage_snapshot()["requests_used"] == 2
    assert budget.usage_snapshot()["response_bytes_used"] == (
        len(quote) + len(page) if operation == "fetch" else 2 * len(quote)
    )
    assert budget.usage_snapshot()["exhausted"] == "response_bytes"


def test_candidate_raw_wrapper_is_separate_from_canonical_body_limit():
    page = transcript_html()
    session = FakeSession({Q2_URL: FakeResponse(Q2_URL, page)})
    request = make_candidate_request(max_response_bytes=len(page))
    _, content = api._extract_body(page)
    request["max_body_bytes"] = len(content.encode("utf-8"))
    assert len(page) > request["max_body_bytes"]
    result = api.fetch_transcript_candidate(
        request,
        session_factory=lambda: session,
        provider_settings=ENABLED,
        include_source_payload=True,
    )
    assert result["status"] == "fetched"
    assert base64.b64decode(result["provider_payload_base64"]) == page


def test_private_tool_session_path_cannot_bypass_response_quota(monkeypatch):
    response = Chunks([padded_payload()])
    session = FakeSession({FMP_URL: response})
    out = io.StringIO()
    with (
        patch.object(
            sys, "stdin", io.StringIO(json.dumps(fmp_request(max_response_bytes=1024)))
        ),
        patch.object(sys, "stdout", out),
        patch.dict("os.environ", {"FMP_API_KEY": KEY}),
    ):
        assert (
            transcript_tool.main(["--request-stdin"], _session_factory=lambda: session)
            == 0
        )
    assert json.loads(out.getvalue())["status"] == "content_too_large"
    assert len(session.calls) == 1


@pytest.mark.parametrize(
    "ticker,quarter,content",
    [("NVDA", 2, "A" * 286), ("MSFT", 3, "业务描述；原语言正文。" * 30)],
)
def test_supervised_cli_oversize_reports_actual_usage_without_artifacts(
    monkeypatch, tmp_path, ticker, quarter, content
):
    raw = padded_payload(ticker=ticker, quarter=quarter, content=content)
    response = StallResponse(
        FMP_URL, raw, content_type="application/json", split_chunks=1
    )
    calls = tmp_path / "calls.jsonl"
    rt = tmp_path / "rt"
    rt.mkdir()
    rc, out, err = run_tool(
        monkeypatch,
        ["--request-stdin", "--include-source-payload", "--report-usage"],
        fmp_request(ticker=ticker, fiscal_quarter=quarter, max_response_bytes=1024),
        spec=fake_spec({FMP_URL: response}, calls_file=calls),
        temp_root=rt,
        key=KEY,
    )
    result = json.loads(out)
    receipt = json.loads(err)
    assert rc == 0 and result["status"] == "content_too_large"
    assert result["error_code"] == "byte_limit"
    assert "provider_payload_base64" not in result
    assert receipt["schema_version"] == "earnings-retrieval-usage/1"
    assert receipt["usage_complete"] is True
    assert receipt["usage"] == dict(
        requests_used=1, response_bytes_used=len(raw), exhausted="response_bytes"
    )
    assert get_urls(calls) == [FMP_URL]
    assert list(rt.iterdir()) == []


def test_worker_response_quota_is_effective_when_request_quota_is_none(tmp_path):
    quote = quote_html(Q2_URL)
    rt = tmp_path / "rt"
    rt.mkdir()
    outcome = retrieval_runtime.run_retrieval(
        "list",
        dict(
            ticker="ACME",
            exchange="nyse",
            as_of_date="2026-09-30",
            request_id="only-bytes",
            timeout_seconds=10,
            download_authorized=True,
        ),
        remaining_seconds=10,
        requests_left=None,
        response_bytes_left=len(quote) - 1,
        provider_settings=ENABLED,
        launcher=LAUNCHER,
        launcher_spec=fake_spec(
            {QUOTE_URL: StallResponse(QUOTE_URL, quote, split_chunks=1)}
        ),
        temp_root=rt,
    )
    assert outcome.result["status"] == "content_too_large"
    assert outcome.usage == dict(
        requests_used=1, response_bytes_used=len(quote), exhausted="response_bytes"
    )
    assert outcome.reason is None and list(rt.iterdir()) == []


def test_request_only_worker_quota_retains_request_failure_not_byte_failure(tmp_path):
    quote = quote_html(Q2_URL)
    first = "https://www.fool.com/quote/nasdaq/acme/"
    rt = tmp_path / "rt"
    rt.mkdir()
    outcome = retrieval_runtime.run_retrieval(
        "list",
        dict(
            ticker="ACME",
            exchange="auto",
            as_of_date="2026-09-30",
            request_id="only-requests",
            timeout_seconds=10,
            download_authorized=True,
        ),
        remaining_seconds=10,
        requests_left=1,
        response_bytes_left=None,
        provider_settings=ENABLED,
        launcher=LAUNCHER,
        launcher_spec=fake_spec(
            {
                first: FakeResponse(first, quote),
                QUOTE_URL: FakeResponse(QUOTE_URL, quote),
            }
        ),
        temp_root=rt,
    )
    assert outcome.result["status"] == "provider_error"
    assert outcome.result["error_code"] == "unexpected_provider_failure"
    assert outcome.usage == dict(
        requests_used=1, response_bytes_used=len(quote), exhausted="request_limit"
    )
    assert list(rt.iterdir()) == []


def test_supervised_cli_exact_boundary_and_128mib_acceptance(monkeypatch, tmp_path):
    raw = padded_payload(1024, content="业务原文。" * 30)
    for number, cap in enumerate((1024, 128 * 1024 * 1024)):
        rt = tmp_path / f"rt{number}"
        rt.mkdir()
        rc, out, err = run_tool(
            monkeypatch,
            ["--request-stdin", "--include-source-payload", "--report-usage"],
            fmp_request(max_response_bytes=cap),
            spec=fake_spec(
                {FMP_URL: FakeResponse(FMP_URL, raw, content_type="application/json")}
            ),
            temp_root=rt,
            key=KEY,
        )
        result = json.loads(out)
        receipt = json.loads(err)
        assert rc == 0 and result["status"] == "fetched"
        assert base64.b64decode(result["provider_payload_base64"]) == raw
        assert (
            result["canonical_content_sha256"]
            == hashlib.sha256(("业务原文。" * 30).encode("utf-8")).hexdigest()
        )
        assert receipt["usage_complete"] is True
        assert receipt["usage"] == dict(
            requests_used=1, response_bytes_used=1024, exhausted=None
        )
        assert list(rt.iterdir()) == []


@pytest.mark.parametrize("case", ["known-zero", "worker-loss", "deadline"])
def test_final_receipt_distinguishes_known_zero_from_lost_worker(
    monkeypatch, tmp_path, case
):
    rt = tmp_path / "rt"
    rt.mkdir()
    request = fmp_request(max_response_bytes=1024)
    if case == "known-zero":
        spec = fake_spec({})
        key = None
    elif case == "worker-loss":
        spec = fake_spec(build_failure="SystemExit:7")
        key = KEY
    else:
        spec = fake_spec({FMP_URL: StallResponse(FMP_URL, stall_in_get=True)})
        key = KEY
        request["timeout_seconds"] = 2
    rc, out, err = run_tool(
        monkeypatch,
        ["--request-stdin", "--report-usage"],
        request,
        spec=spec,
        temp_root=rt,
        key=key,
    )
    result = json.loads(out)
    receipt = json.loads(err)
    assert rc == 0 and "provider_payload_base64" not in result
    if case == "known-zero":
        assert result["status"] == "unavailable"
        assert receipt["usage_complete"] is True
        assert receipt["usage"] == dict(
            requests_used=0, response_bytes_used=0, exhausted=None
        )
    else:
        assert result["status"] == (
            "provider_error" if case == "worker-loss" else "deadline_exceeded"
        )
        assert receipt["usage_complete"] is False
        assert receipt["usage"] is None
    assert list(rt.iterdir()) == []


def test_cleanup_failure_keeps_primary_byte_reason_and_observed_usage(
    monkeypatch, tmp_path
):
    rt = tmp_path / "rt"
    rt.mkdir()
    response = StallResponse(
        FMP_URL, padded_payload(), content_type="application/json", split_chunks=1
    )
    with patch.object(
        retrieval_runtime.shutil,
        "rmtree",
        side_effect=OSError("owned fixture temporarily locked"),
    ):
        rc, out, err = run_tool(
            monkeypatch,
            ["--request-stdin", "--report-usage"],
            fmp_request(max_response_bytes=1024),
            spec=fake_spec({FMP_URL: response}),
            temp_root=rt,
            key=KEY,
        )
    result = json.loads(out)
    receipt = json.loads(
        next(line for line in err.splitlines() if line.startswith("{"))
    )
    assert rc == 0 and result["status"] == "content_too_large"
    assert result["error_code"] == "byte_limit"
    assert receipt["usage_complete"] is True
    assert receipt["usage"] == dict(
        requests_used=1, response_bytes_used=20386, exhausted="response_bytes"
    )
    assert "retrieval_cleanup_failed" in err
    # The parent truthfully leaves its owned workdir if deletion failed. The
    # surrounding pytest TEMP owner removes it after this assertion.
    assert len(list(rt.iterdir())) == 1


@pytest.mark.parametrize(
    "ticker,year,quarter,case,response_size,cap",
    [
        ("MSFT", 2025, 4, "success", 1024, 128 * 1024 * 1024),
        ("GLOBAL", 2024, 1, "success", 1024, 1024),
        ("NVDA", 2026, 2, "padded-json", 20386, 1024),
    ],
)
def test_transparent_cli_proxy_uses_real_worker_and_requested_identity(
    tmp_path, ticker, year, quarter, case, response_size, cap
):
    import os
    import subprocess
    from tests.test_retrieval_runtime import envelopes, get_urls

    rt = tmp_path / "rt"
    rt.mkdir()
    calls = tmp_path / "calls.jsonl"
    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        json.dumps(
            dict(
                case=case,
                response_bytes=response_size,
                calls_file=str(calls),
                worker_temp_root=str(rt),
                call_date="2026-09-01",
                content_utf8="业务原文。" * 30,
            )
        ),
        encoding="utf-8",
    )
    request = fmp_request(
        ticker=ticker,
        fiscal_year=year,
        fiscal_quarter=quarter,
        max_body_bytes=1000,
        max_response_bytes=cap,
    )
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
    env.pop("FMP_API_KEY", None)
    completed = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "-B",
            str(ROOT / "tests/request_budget_cli_proxy.py"),
            str(fixture),
            "--request-stdin",
            "--include-source-payload",
            "--report-usage",
        ],
        input=json.dumps(request),
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=20,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    receipt = json.loads(completed.stderr)
    assert get_urls(calls) == [FMP_URL]
    assert len(envelopes(calls)) == 1
    assert envelopes(calls)[0]["budget"]["response_bytes_left"] == cap
    assert receipt["request_id"] == request["request_id"]
    assert receipt["usage_complete"] is True
    assert receipt["usage"] == dict(
        requests_used=1,
        response_bytes_used=response_size,
        exhausted="response_bytes" if case == "padded-json" else None,
    )
    if case == "padded-json":
        assert result["status"] == "content_too_large"
        assert result["error_code"] == "byte_limit"
        assert "provider_payload_base64" not in result
    else:
        assert result["status"] == "fetched"
        raw = base64.b64decode(result["provider_payload_base64"])
        row = json.loads(raw)[0]
        assert len(raw) == response_size
        assert row["symbol"] == ticker and row["year"] == year
        assert row["period"] == f"Q{quarter}"
        assert row["content"] == "业务原文。" * 30
        assert result["provider_payload_sha256"] == hashlib.sha256(raw).hexdigest()
    assert list(rt.iterdir()) == []

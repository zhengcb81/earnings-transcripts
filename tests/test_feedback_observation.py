"""Feedback projection tests: ET's own side of the research feedback loop.

Every case runs the real ``transcript_tool.main`` boundary. Only the HTTP
transport is fake (in-process session, or the private worker launcher seam),
no provider is contacted, no translation is constructed, and no observation
ever reaches the business stdout or stderr.
"""
from __future__ import annotations

import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import transcript_api  # noqa: E402
import transcript_feedback  # noqa: E402
import transcript_tool  # noqa: E402
from tests.test_retrieval_runtime import (  # noqa: E402
    LAUNCHER,
    StallResponse,
    fake_spec,
    get_urls,
)
from tests.test_transcript_api import (  # noqa: E402
    FMP_URL,
    Q2_URL,
    QUOTE_URL,
    FakeResponse,
    FakeSession,
    fmp_payload,
    make_request,
    quote_html,
)

# Fake provider credential for the in-process fake HTTP sessions below. It is
# a placeholder, never a real key; the tests assert that this exact value
# never reaches a result, a receipt, an observation or a detail record.
FEEDBACK_FAKE_KEY = "fake-feedback-key-for-offline-tests"

FMP_REQUEST = {
    "ticker": "MSFT",
    "exchange": "nasdaq",
    "fiscal_quarter": 3,
    "provider": "fmp",
}


class _FixedDateTime:
    @staticmethod
    def now(tz=None):
        return datetime(2026, 9, 29, 12, 0, 0, tzinfo=timezone.utc)


def fmp_request(**updates: Any) -> dict[str, Any]:
    request = make_request(**FMP_REQUEST, **updates)
    return request


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def checks_by_id(path: Path) -> dict[str, dict[str, Any]]:
    return {record["check_id"]: record for record in read_jsonl(path)}


def recorder_for(tmp_path: Path, **kwargs: Any) -> transcript_feedback.FeedbackRecorder:
    return transcript_feedback.FeedbackRecorder(
        tmp_path / "feedback.jsonl",
        run_id=kwargs.pop("run_id", "fb-run-001"),
        attempt_id=kwargs.pop("attempt_id", "fb-attempt-001"),
        **kwargs,
    )


def run_cooperative(
    monkeypatch: pytest.MonkeyPatch,
    request: dict[str, Any],
    session: FakeSession,
    *,
    argv: list[str] | None = None,
    recorder: Any = None,
    settings: Any = None,
) -> tuple[int, str, str]:
    """Run the public boundary on the cooperative (injected session) path."""
    monkeypatch.delenv("FMP_API_KEY_FILE", raising=False)
    monkeypatch.setenv("FMP_API_KEY", FEEDBACK_FAKE_KEY)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    rc = transcript_tool.main(
        argv or ["--request-stdin", "--include-source-payload"],
        _session_factory=lambda: session,
        _provider_settings=settings or transcript_api.ProviderSettings(),
        _feedback_recorder=recorder,
    )
    return rc, out.getvalue(), err.getvalue()


def run_supervised(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    request: dict[str, Any],
    *,
    spec: dict[str, Any],
    temp_root: Path,
    recorder: Any = None,
) -> tuple[int, str, str]:
    """Run the public boundary through the supervisor and the real worker."""
    monkeypatch.delenv("FMP_API_KEY_FILE", raising=False)
    monkeypatch.setenv("FMP_API_KEY", FEEDBACK_FAKE_KEY)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    rc = transcript_tool.main(
        argv,
        _retrieval_launcher=LAUNCHER,
        _retrieval_spec=spec,
        _retrieval_temp_root=temp_root,
        _feedback_recorder=recorder,
    )
    return rc, out.getvalue(), err.getvalue()


def forbid_translator(monkeypatch: pytest.MonkeyPatch) -> None:
    import translator

    def explode(*args: Any, **kwargs: Any) -> None:
        pytest.fail("the transcript tool must never construct a translator")

    monkeypatch.setattr(translator.TranslatorFactory, "create", explode)


# ── the frozen projection protocol ───────────────────────────────────


def test_the_projection_exposes_the_frozen_observation_protocol():
    assert transcript_feedback.OBSERVATION_SCHEMA == "feedback-observation/1"
    assert transcript_feedback.OWNER == "earnings-transcripts"
    assert transcript_feedback.DISPOSITIONS == ("expected", "deviation", "unknown")
    assert transcript_feedback.FEEDBACK_OUT_ENV == "RESEARCH_FEEDBACK_OUT"
    assert transcript_feedback.FEEDBACK_RUN_ID_ENV == "RESEARCH_FEEDBACK_RUN_ID"
    assert transcript_feedback.FEEDBACK_ATTEMPT_ID_ENV == "RESEARCH_FEEDBACK_ATTEMPT_ID"


def test_one_observation_carries_every_required_field(monkeypatch, tmp_path):
    recorder = recorder_for(tmp_path)
    request = fmp_request(request_id="fb-fields-001")
    page = fmp_payload()
    session = FakeSession({FMP_URL: FakeResponse(FMP_URL, page, content_type="application/json")})
    run_cooperative(monkeypatch, request, session, recorder=recorder)

    records = read_jsonl(tmp_path / "feedback.jsonl")
    assert records
    for record in records:
        for field in (
            "schema_version",
            "owner",
            "run_id",
            "attempt_id",
            "step_id",
            "check_id",
            "expected",
            "actual",
        ):
            assert isinstance(record[field], str) and record[field]
        assert record["schema_version"] == "feedback-observation/1"
        assert record["owner"] == "earnings-transcripts"
        assert record["run_id"] == "fb-run-001"
        assert record["attempt_id"] == "fb-attempt-001"
        assert record["step_id"] == "fb-fields-001"
        assert record["disposition"] in transcript_feedback.DISPOSITIONS
        # the observation names the version of the observed run
        assert record["version"] == transcript_tool.PRODUCT_VERSION


# ── the legal FMP shapes ────────────────────────────────────────────


@pytest.mark.parametrize(
    "case,payload,expected_status",
    [
        ("exact-period", fmp_payload(), "fetched"),
        ("no-target-period", b"[]", "not_found"),
        ("wrong-quarter", fmp_payload(period="Q2"), "provenance_rejected"),
    ],
    ids=["exact-period", "no-target-period", "wrong-quarter"],
)
def test_a_legal_provider_shape_is_projected_as_expected(
    monkeypatch, tmp_path, case, payload, expected_status
):
    recorder = recorder_for(tmp_path)
    request = fmp_request(request_id=f"fb-legal-{case}")
    session = FakeSession(
        {FMP_URL: FakeResponse(FMP_URL, payload, content_type="application/json")}
    )
    rc, out, err = run_cooperative(monkeypatch, request, session, recorder=recorder)

    result = json.loads(out)
    assert rc == 0
    assert err == ""
    assert result["status"] == expected_status
    checks = checks_by_id(tmp_path / "feedback.jsonl")
    assert checks["outcome-naming"]["disposition"] == "expected"
    assert checks["exact-period-ownership"]["disposition"] == "expected"
    assert checks["content-reference"]["disposition"] == "expected"
    assert all(
        record["disposition"] != "deviation" for record in read_jsonl(tmp_path / "feedback.jsonl")
    )


def test_a_legal_discovery_is_projected_as_expected(monkeypatch, tmp_path):
    """A discovery that finds a candidate is a normal outcome, not a deviation."""
    recorder = recorder_for(tmp_path)
    request = make_request(request_id="fb-discovery-legal-001")
    session = FakeSession({QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(Q2_URL))})
    rc, out, err = run_cooperative(
        monkeypatch,
        request,
        session,
        argv=["--request-stdin", "--operation", "discover"],
        recorder=recorder,
        settings=transcript_api.ProviderSettings(motley_fool_enabled=True),
    )

    result = json.loads(out)
    assert rc == 0
    assert err == ""
    assert result["status"] == "discovered"
    checks = checks_by_id(tmp_path / "feedback.jsonl")
    assert checks["outcome-naming"]["expected"] == "discovered"
    assert checks["outcome-naming"]["disposition"] == "expected"
    assert all(
        record["disposition"] != "deviation"
        for record in read_jsonl(tmp_path / "feedback.jsonl")
    )


#: Every published outcome of the two result contracts. Each one is a normal
#: end state of a real run, so none of them may be reported as a deviation.
PUBLISHED_OUTCOMES = [
    ("fetched", None),
    ("discovered", None),
    ("not_found", None),
    ("not_found", "call_after_as_of"),
    ("not_found", "publication_after_as_of"),
    ("ambiguous", None),
    ("unsupported", "provider_unavailable"),
    ("unsupported", "unsupported_market"),
    ("unsupported", "unsupported_exchange"),
    ("unsupported", "candidate_discovery_unavailable"),
    ("unsupported", "candidate_fetch_unavailable"),
    ("unavailable", "provider_disabled"),
    ("unavailable", "provider_credentials_missing"),
    ("unavailable", "provider_credentials_file_unavailable"),
    ("unavailable", "provider_credentials_file_invalid"),
    ("unavailable", "provider_credentials_file_empty"),
    ("unavailable", "provider_credentials_rejected"),
    ("unavailable", "provider_entitlement_required"),
    ("unavailable", "provider_entitlement_denied"),
    ("unavailable", "provider_cost_unknown"),
    ("unavailable", "provider_cost_budget_exceeded"),
    ("not_authorized", None),
    ("rate_limited", "provider_http_429"),
    ("provider_error", "provider_http_500"),
    ("provider_error", "provider_response"),
    ("provider_error", "provider_credentials_leaked"),
    ("provider_error", "unexpected_provider_failure"),
    ("provider_error", "retrieval_worker_failure"),
    ("deadline_exceeded", "provider_deadline"),
    ("content_too_large", "byte_limit"),
    ("provenance_rejected", "provider_identity_or_host"),
    ("provenance_rejected", "candidate_effective_url_or_mime"),
    ("invalid_request", "request_schema"),
    ("invalid_request", "invalid_json"),
    ("invalid_request", "request_too_large"),
    ("invalid_request", "source_payload_flag"),
    ("invalid_request", "source_payload_not_valid_for_discovery"),
    ("invalid_request", "candidate_request_schema_or_identity"),
]


@pytest.mark.parametrize(
    "status,reason",
    PUBLISHED_OUTCOMES,
    ids=[f"{status}-{reason or 'no-reason'}" for status, reason in PUBLISHED_OUTCOMES],
)
def test_no_published_outcome_is_projected_as_a_deviation(status, reason):
    """A legal end state must never open an issue."""
    result: dict[str, Any] = {"status": status}
    if reason is not None:
        result["error_code"] = reason
    verdict = transcript_feedback.evaluate_outcome_naming(result)
    assert verdict["disposition"] == "expected", verdict
    assert verdict["expected"] == status


def test_an_undocumented_named_reason_is_a_deviation():
    verdict = transcript_feedback.evaluate_outcome_naming(
        {"status": "fetched", "error_code": "a_reason_this_release_never_published"}
    )
    assert verdict["disposition"] == "deviation"
    assert verdict["issue_key"] == "transcript-outcome-naming"
    assert verdict["actual"] == "undocumented_status"


def test_the_default_result_schema_projects_every_check(monkeypatch, tmp_path):
    """Without the source payload the checks still run, one name each."""
    recorder = recorder_for(tmp_path)
    request = fmp_request(request_id="fb-default-schema-001")
    page = fmp_payload()
    session = FakeSession({FMP_URL: FakeResponse(FMP_URL, page, content_type="application/json")})
    rc, out, err = run_cooperative(
        monkeypatch, request, session, argv=["--request-stdin"], recorder=recorder
    )

    result = json.loads(out)
    assert rc == 0
    assert err == ""
    assert result["schema_version"] == "earnings-transcript-result/1"
    records = read_jsonl(tmp_path / "feedback.jsonl")
    assert [record["check_id"] for record in records] == [
        "action-started",
        "outcome-naming",
        "exact-period-ownership",
        "content-reference",
        "usage-accounting",
    ]
    reference = checks_by_id(tmp_path / "feedback.jsonl")["content-reference"]
    assert reference["actual"] == "reference_unverifiable"
    assert reference["disposition"] == "expected"


def test_the_owning_job_can_read_an_incomplete_diagnostic(monkeypatch, tmp_path):
    """A refused sidecar write must be reported to the job, not swallowed."""
    recorder = transcript_feedback.FeedbackRecorder(
        tmp_path / "feedback.jsonl",
        run_id="fb-run-001",
        attempt_id="fb-attempt-001",
    )
    transcript_feedback.set_active_recorder(recorder)
    monkeypatch.setattr(
        transcript_feedback, "_ACTIVE_RECORDER", recorder, raising=False
    )

    def refuse(line: str) -> None:
        raise PermissionError(13, "Access is denied")

    recorder._sink = refuse
    recorder._details_sink = refuse
    request = fmp_request(request_id="fb-diagnostic-001")
    page = fmp_payload()
    session = FakeSession({FMP_URL: FakeResponse(FMP_URL, page, content_type="application/json")})
    run_cooperative(monkeypatch, request, session, recorder=recorder)

    assert recorder.write_failures >= 1
    assert transcript_feedback.active_diagnostics()
    reported = read_jsonl(tmp_path / "feedback.jsonl.diagnostics.jsonl")
    assert reported
    assert reported[0]["run_id"] == "fb-run-001"
    assert reported[0]["diagnostic"].startswith("feedback_write_failed:")


def test_every_status_and_reason_the_product_can_emit_is_documented():
    """The vocabulary follows the product, not a hand-written guess."""
    import ast

    statuses: set[str] = set()
    reasons: set[str] = set()
    for name in ("transcript_api.py", "transcript_preflight.py", "transcript_tool.py"):
        tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                function = node.func
                called = getattr(function, "id", None) or getattr(function, "attr", None)
                # _base_result(request_id, status, ...) and
                # _wire_failure(schema_version, request_id, status, ...)
                # put the status word at a different position.
                position = {"_base_result": 1, "_wire_failure": 2}.get(called)
                if position is not None:
                    if len(node.args) > position and isinstance(
                        node.args[position], ast.Constant
                    ):
                        value = node.args[position].value
                        if isinstance(value, str):
                            statuses.add(value)
                for keyword in node.keywords:
                    if keyword.arg == "error_code" and isinstance(
                        keyword.value, ast.Constant
                    ):
                        reasons.add(keyword.value.value)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id == "status"
                        and isinstance(node.value, ast.Constant)
                    ):
                        statuses.add(node.value.value)
    assert statuses, "the scan found no status word"
    assert reasons, "the scan found no named reason"
    undocumented_status = sorted(statuses - transcript_feedback.STATUS_VOCABULARY)
    undocumented_reason = sorted(
        reason for reason in reasons if transcript_feedback.reason_status(reason) is None
    )
    assert undocumented_status == []
    assert undocumented_reason == []


def test_an_entitlement_402_is_a_legal_refusal(monkeypatch, tmp_path):
    recorder = recorder_for(tmp_path)
    request = fmp_request(request_id="fb-entitlement-402")
    session = FakeSession({FMP_URL: FakeResponse(FMP_URL, b"", status_code=402)})
    rc, out, err = run_cooperative(monkeypatch, request, session, recorder=recorder)

    result = json.loads(out)
    assert rc == 0
    assert err == ""
    assert (result["status"], result["error_code"]) == (
        "unavailable",
        "provider_entitlement_required",
    )
    checks = checks_by_id(tmp_path / "feedback.jsonl")
    assert checks["outcome-naming"]["expected"] == "unavailable"
    assert checks["outcome-naming"]["actual"] == "unavailable"
    assert checks["outcome-naming"]["disposition"] == "expected"
    assert checks["exact-period-ownership"]["disposition"] == "expected"


def test_a_fetch_claiming_a_foreign_period_is_a_deviation(monkeypatch, tmp_path):
    """Controlled fault injection: the adapter accepts another quarter's call.

    The provider response still describes 2026-Q2 while the request asks for
    2026-Q3. The business result claims the requested period, so the
    ownership check must report the deviation instead of passing it on.
    """
    monkeypatch.setattr(transcript_api, "_fmp_period", lambda item: (2026, 3))
    recorder = recorder_for(tmp_path)
    request = fmp_request(request_id="fb-foreign-period-001")
    page = fmp_payload(period="Q2")
    session = FakeSession({FMP_URL: FakeResponse(FMP_URL, page, content_type="application/json")})
    rc, out, err = run_cooperative(monkeypatch, request, session, recorder=recorder)

    result = json.loads(out)
    assert rc == 0
    assert err == ""
    assert result["status"] == "fetched"
    assert result["fiscal_period"] == "2026-Q3"
    checks = checks_by_id(tmp_path / "feedback.jsonl")
    ownership = checks["exact-period-ownership"]
    assert ownership["disposition"] == "deviation"
    assert ownership["expected"] == "not_attributed"
    assert ownership["actual"] == "requested_period_attributed"
    assert ownership["issue_key"] == "transcript-period-attribution"
    assert ownership["details_ref"]


# ── usage that cannot be known ──────────────────────────────────────


def test_an_aborted_worker_is_unknown_and_is_never_retried(monkeypatch, tmp_path):
    forbid_translator(monkeypatch)
    recorder = recorder_for(tmp_path)
    calls_file = tmp_path / "calls.jsonl"
    temp_root = tmp_path / "rt"
    temp_root.mkdir()
    spec = fake_spec(
        {FMP_URL: StallResponse(FMP_URL, stall_in_get=True)}, calls_file=calls_file
    )
    request = fmp_request(request_id="fb-aborted-worker-001", timeout_seconds=2)
    rc, out, err = run_supervised(
        monkeypatch,
        ["--request-stdin", "--include-source-payload", "--report-usage"],
        request,
        spec=spec,
        temp_root=temp_root,
        recorder=recorder,
    )

    result = json.loads(out)
    receipt = json.loads(err)
    assert rc == 0
    assert result["status"] == "deadline_exceeded"
    assert receipt["usage_complete"] is False
    assert receipt["usage"] is None
    checks = checks_by_id(tmp_path / "feedback.jsonl")
    usage = checks["usage-accounting"]
    assert usage["expected"] == "usage_unknown"
    assert usage["actual"] == "usage_unknown"
    assert usage["disposition"] == "unknown"
    # at most one provider request: an unknown usage is never re-bought (a
    # loaded machine may stop the worker before it reaches the provider at
    # all, which still satisfies the contract)
    assert len(get_urls(calls_file)) <= 1
    assert list(temp_root.iterdir()) == []


def test_a_known_usage_is_reported_as_known(monkeypatch, tmp_path):
    forbid_translator(monkeypatch)
    recorder = recorder_for(tmp_path)
    calls_file = tmp_path / "calls.jsonl"
    temp_root = tmp_path / "rt"
    temp_root.mkdir()
    page = fmp_payload()
    spec = fake_spec(
        {FMP_URL: FakeResponse(FMP_URL, page, content_type="application/json")},
        calls_file=calls_file,
    )
    request = fmp_request(request_id="fb-known-usage-001")
    rc, out, err = run_supervised(
        monkeypatch,
        ["--request-stdin", "--include-source-payload", "--report-usage"],
        request,
        spec=spec,
        temp_root=temp_root,
        recorder=recorder,
    )

    receipt = json.loads(err)
    assert rc == 0
    assert receipt["usage_complete"] is True
    assert receipt["usage"]["requests_used"] == 1
    usage = checks_by_id(tmp_path / "feedback.jsonl")["usage-accounting"]
    assert usage["expected"] == "usage_known"
    assert usage["actual"] == "usage_known"
    assert usage["disposition"] == "expected"


# ── the business contract is untouched ──────────────────────────────


def test_feedback_enabled_and_disabled_publish_the_same_business_bytes(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(transcript_api, "datetime", _FixedDateTime)
    page = fmp_payload()
    request = fmp_request(request_id="fb-parity-001")
    argv = ["--request-stdin", "--include-source-payload", "--report-usage"]

    off_session = FakeSession(
        {FMP_URL: FakeResponse(FMP_URL, page, content_type="application/json")}
    )
    off = run_cooperative(monkeypatch, request, off_session, argv=argv)
    on_session = FakeSession(
        {FMP_URL: FakeResponse(FMP_URL, page, content_type="application/json")}
    )
    recorder = recorder_for(tmp_path)
    on = run_cooperative(monkeypatch, request, on_session, argv=argv, recorder=recorder)

    assert off[0] == on[0]
    assert off[1] == on[1]
    assert off[2] == on[2]
    assert json.loads(off[1])["status"] == "fetched"
    assert len(read_jsonl(tmp_path / "feedback.jsonl")) == 5


def test_the_supervisor_receipt_is_identical_with_and_without_feedback(
    monkeypatch, tmp_path
):
    forbid_translator(monkeypatch)
    page = fmp_payload()
    request = fmp_request(request_id="fb-parity-supervised-001")
    argv = ["--request-stdin", "--include-source-payload", "--report-usage"]

    off_root = tmp_path / "rt-off"
    off_root.mkdir()
    off = run_supervised(
        monkeypatch,
        argv,
        request,
        spec=fake_spec(
            {FMP_URL: FakeResponse(FMP_URL, page, content_type="application/json")},
            calls_file=tmp_path / "calls-off.jsonl",
        ),
        temp_root=off_root,
    )
    on_root = tmp_path / "rt-on"
    on_root.mkdir()
    recorder = recorder_for(tmp_path)
    on = run_supervised(
        monkeypatch,
        argv,
        request,
        spec=fake_spec(
            {FMP_URL: FakeResponse(FMP_URL, page, content_type="application/json")},
            calls_file=tmp_path / "calls-on.jsonl",
        ),
        temp_root=on_root,
        recorder=recorder,
    )

    assert off[0] == on[0]
    assert off[2] == on[2]  # the native final usage receipt is byte-identical
    assert json.loads(on[2])["usage_complete"] is True
    off_result = json.loads(off[1])
    on_result = json.loads(on[1])
    assert off_result.pop("retrieved_at") != ""
    assert on_result.pop("retrieved_at") != ""
    assert off_result == on_result
    assert len(read_jsonl(tmp_path / "feedback.jsonl")) == 5


def test_an_unwritable_sidecar_leaves_the_business_result_unchanged(
    monkeypatch, tmp_path
):
    forbid_translator(monkeypatch)

    def refuse(line: str) -> None:
        raise PermissionError(13, "Access is denied")

    recorder = transcript_feedback.FeedbackRecorder(
        tmp_path / "feedback.jsonl",
        run_id="fb-run-001",
        attempt_id="fb-attempt-001",
        sink=refuse,
        details_sink=refuse,
    )
    request = fmp_request(request_id="fb-unwritable-001")
    page = fmp_payload()
    session = FakeSession({FMP_URL: FakeResponse(FMP_URL, page, content_type="application/json")})
    rc, out, err = run_cooperative(monkeypatch, request, session, recorder=recorder)

    result = json.loads(out)
    assert rc == 0
    assert err == ""
    assert result["status"] == "fetched"
    assert len(session.calls) == 1  # the provider was called exactly once
    assert recorder.write_failures >= 1
    assert recorder.diagnostics
    assert not (tmp_path / "feedback.jsonl").exists()
    # an observation that never reached the sidecar keeps no identity, so a
    # later attempt may still deliver it
    assert recorder.written_ids == set()


def test_a_failed_write_reports_no_delivery(tmp_path):
    def refuse(line: str) -> None:
        raise PermissionError(13, "Access is denied")

    recorder = transcript_feedback.FeedbackRecorder(
        tmp_path / "unused-feedback.jsonl",
        run_id="fb-run-001",
        attempt_id="fb-attempt-001",
        sink=refuse,
        details_sink=refuse,
    )
    assert (
        recorder.record(step_id="step-1", check_id="check-1", expected="x", actual="x")
        is False
    )
    assert recorder.write_failures == 1
    assert recorder.diagnostics
    assert recorder.written_ids == set()


def test_no_observation_reaches_the_business_streams(monkeypatch, tmp_path):
    recorder = recorder_for(tmp_path)
    request = fmp_request(request_id="fb-streams-001")
    page = fmp_payload()
    session = FakeSession({FMP_URL: FakeResponse(FMP_URL, page, content_type="application/json")})
    rc, out, err = run_cooperative(monkeypatch, request, session, recorder=recorder)

    assert rc == 0
    assert out.count("\n") == 1
    assert err == ""
    assert "feedback-observation" not in out + err
    assert FEEDBACK_FAKE_KEY not in out + err


# ── the receipt boundary ────────────────────────────────────────────


@pytest.mark.parametrize(
    "case,stdin_text,argv",
    [
        ("invalid-json", "not json at all", ["--request-stdin", "--report-usage"]),
        (
            "oversized-request",
            "x" * (128 * 1024 + 8),
            ["--request-stdin", "--report-usage"],
        ),
        (
            "discovery-with-source-payload",
            json.dumps(fmp_request(request_id="fb-discovery-001")),
            ["--request-stdin", "--operation", "discover", "--include-source-payload", "--report-usage"],
        ),
    ],
    ids=["invalid-json", "oversized-request", "discovery-with-source-payload"],
)
def test_report_usage_publishes_a_receipt_on_every_terminal_path(
    monkeypatch, tmp_path, case, stdin_text, argv
):
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin_text))
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    rc = transcript_tool.main(argv)

    assert rc == 0
    lines = [line for line in err.getvalue().splitlines() if line.strip()]
    assert len(lines) == 1, f"{case} published {len(lines)} receipt lines"
    receipt = json.loads(lines[0])
    assert receipt["schema_version"] == "earnings-retrieval-usage/1"
    assert receipt["usage_complete"] is True
    assert receipt["usage"] == {
        "requests_used": 0,
        "response_bytes_used": 0,
        "exhausted": None,
    }


def test_a_missing_receipt_is_projected_as_a_deviation():
    """Without the published receipt a consumer cannot reconcile the cost."""
    native = {
        "schema_version": "earnings-retrieval-usage/1",
        "request_id": "fb-missing-receipt-001",
        "usage_complete": True,
        "usage": {"requests_used": 0, "response_bytes_used": 0, "exhausted": None},
    }
    verdict = transcript_feedback.evaluate_usage(native, None, receipt_required=True)
    assert verdict["expected"] == "usage_known"
    assert verdict["actual"] == "usage_missing"
    assert verdict["disposition"] == "deviation"
    assert verdict["issue_key"] == "transcript-usage-receipt"


def test_a_published_receipt_on_an_early_rejection_is_projected_as_known(
    monkeypatch, tmp_path
):
    recorder = recorder_for(tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json at all"))
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    rc = transcript_tool.main(["--request-stdin", "--report-usage"], _feedback_recorder=recorder)

    assert rc == 0
    usage = checks_by_id(tmp_path / "feedback.jsonl")["usage-accounting"]
    assert usage["expected"] == "usage_known"
    assert usage["actual"] == "usage_known"
    assert usage["disposition"] == "expected"


# ── replay, idempotence and redaction ──────────────────────────────


def test_a_replayed_observation_adds_no_second_line(monkeypatch, tmp_path):
    request = fmp_request(request_id="fb-replay-001")
    page = fmp_payload()

    first = recorder_for(tmp_path)
    session = FakeSession(
        {FMP_URL: FakeResponse(FMP_URL, page, content_type="application/json")}
    )
    run_cooperative(monkeypatch, request, session, recorder=first)
    after_first = len(read_jsonl(tmp_path / "feedback.jsonl"))

    second = recorder_for(tmp_path)
    session = FakeSession(
        {FMP_URL: FakeResponse(FMP_URL, page, content_type="application/json")}
    )
    run_cooperative(monkeypatch, request, session, recorder=second)
    records = read_jsonl(tmp_path / "feedback.jsonl")

    assert after_first == 5
    assert len(records) == after_first
    assert len({(r["run_id"], r["attempt_id"], r["step_id"], r["check_id"]) for r in records}) == len(
        records
    )


def test_the_details_record_keeps_the_native_facts_without_credentials(
    monkeypatch, tmp_path
):
    recorder = recorder_for(tmp_path)
    request = fmp_request(request_id="fb-details-001")
    page = fmp_payload()
    session = FakeSession({FMP_URL: FakeResponse(FMP_URL, page, content_type="application/json")})
    run_cooperative(monkeypatch, request, session, recorder=recorder)

    details = read_jsonl(tmp_path / "feedback.jsonl.details.jsonl")
    assert details
    record = [item for item in details if item["phase"] == "finished"][0]
    assert record["schema_version"] == "earnings-transcripts-feedback-detail/1"
    assert record["operation"] == "fetch"
    assert record["request"]["request_id"] == "fb-details-001"
    assert record["request"]["ticker"] == "MSFT"
    assert record["requested_period"] == "2026-Q3"
    assert record["native_period"] == "2026-Q3"
    assert record["result"]["status"] == "fetched"
    assert record["result"]["provider"] == "fmp"
    assert record["result"]["provider_payload_sha256"]
    assert record["result"]["canonical_content_sha256"]
    assert record["started_at"] and record["finished_at"]
    assert record["retried"] is False
    assert record["reused"] is False
    assert record["branch"] == "provider"
    assert record["error_type"] is None
    assert record["reason_chain"] == []
    # the detail record names the projection that wrote it
    assert record["projection_version"] == transcript_feedback.ADAPTER_VERSION

    blob = json.dumps(details, ensure_ascii=False)
    assert FEEDBACK_FAKE_KEY not in blob
    assert "Management discussed customer demand" not in blob
    assert str(ROOT) not in blob
    assert "provider_payload_base64" not in blob


def test_the_start_is_recorded_before_the_provider_is_called(monkeypatch, tmp_path):
    """An interrupted run must leave a started record, not nothing."""
    recorder = recorder_for(tmp_path)
    request = fmp_request(request_id="fb-start-before-provider-001")
    page = fmp_payload()
    seen: dict[str, list[str]] = {}

    class _WatchingSession(FakeSession):
        def get(self, url: str, **kwargs: Any):
            seen["checks"] = [
                record["check_id"] for record in read_jsonl(tmp_path / "feedback.jsonl")
            ]
            return super().get(url, **kwargs)

    session = _WatchingSession(
        {FMP_URL: FakeResponse(FMP_URL, page, content_type="application/json")}
    )
    run_cooperative(monkeypatch, request, session, recorder=recorder)
    assert seen["checks"] == ["action-started"]


def test_the_observation_stream_separates_the_start_from_the_result(monkeypatch, tmp_path):
    """An action that starts but never ends leaves a started record alone."""
    recorder = recorder_for(tmp_path)
    request = fmp_request(request_id="fb-start-result-001")
    page = fmp_payload()
    session = FakeSession({FMP_URL: FakeResponse(FMP_URL, page, content_type="application/json")})
    run_cooperative(monkeypatch, request, session, recorder=recorder)

    records = read_jsonl(tmp_path / "feedback.jsonl")
    assert [record["check_id"] for record in records] == [
        "action-started",
        "outcome-naming",
        "exact-period-ownership",
        "content-reference",
        "usage-accounting",
    ]
    started = records[0]
    assert started["expected"] == "started"
    assert started["actual"] == "started"
    assert started["disposition"] == "expected"
    assert all(record["step_id"] == "fb-start-result-001" for record in records)

    details = read_jsonl(tmp_path / "feedback.jsonl.details.jsonl")
    phases = {item["details_id"]: item["phase"] for item in details}
    assert phases[started["details_ref"]] == "started"
    assert all(
        phases[record["details_ref"]] == "finished" for record in records[1:]
    )
    started_record = [
        item for item in details if item["details_id"] == started["details_ref"]
    ][0]
    assert started_record["result"] == {}
    assert started_record["operation"] == "fetch"


def test_the_observation_reference_points_at_the_detail_record(monkeypatch, tmp_path):
    recorder = recorder_for(tmp_path)
    request = fmp_request(request_id="fb-reference-001")
    page = fmp_payload()
    session = FakeSession({FMP_URL: FakeResponse(FMP_URL, page, content_type="application/json")})
    run_cooperative(monkeypatch, request, session, recorder=recorder)

    records = read_jsonl(tmp_path / "feedback.jsonl")
    details = read_jsonl(tmp_path / "feedback.jsonl.details.jsonl")
    identifiers = {record["details_id"] for record in details}
    for record in records:
        assert record["details_ref"] in identifiers
        assert "/" not in record["details_ref"]
        assert "\\" not in record["details_ref"]


def test_only_one_writer_reaches_the_sidecar(monkeypatch, tmp_path):
    """The worker inherits the environment; it must not write the sidecar."""
    forbid_translator(monkeypatch)
    out_path = tmp_path / "feedback.jsonl"
    monkeypatch.setenv(transcript_feedback.FEEDBACK_OUT_ENV, str(out_path))
    monkeypatch.setenv(transcript_feedback.FEEDBACK_RUN_ID_ENV, "fb-one-writer")
    monkeypatch.setenv(transcript_feedback.FEEDBACK_ATTEMPT_ID_ENV, "attempt-1")
    calls_file = tmp_path / "calls.jsonl"
    temp_root = tmp_path / "rt"
    temp_root.mkdir()
    page = fmp_payload()
    spec = fake_spec(
        {FMP_URL: FakeResponse(FMP_URL, page, content_type="application/json")},
        calls_file=calls_file,
    )
    request = fmp_request(request_id="fb-one-writer-001")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    rc = transcript_tool.main(
        ["--request-stdin", "--include-source-payload"],
        _retrieval_launcher=LAUNCHER,
        _retrieval_spec=spec,
        _retrieval_temp_root=temp_root,
    )

    records = read_jsonl(out_path)
    assert rc == 0
    assert len(records) == 5
    assert len({record["step_id"] for record in records}) == 1
    assert all(record["run_id"] == "fb-one-writer" for record in records)
    # without a host-supplied reference the native request id is the evidence
    assert all(
        record["evidence_ref"] == "fb-one-writer-001" for record in records
    )
    assert list(temp_root.iterdir()) == []


def test_a_damaged_observation_line_does_not_stop_the_import(tmp_path):
    """One damaged line is its own diagnostic; the rest is processed."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "et_feedback_issue_cards", ROOT / "tools" / "feedback_issue_cards.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    sidecar = tmp_path / "observations.jsonl"
    good = {
        "schema_version": "feedback-observation/1",
        "owner": "earnings-transcripts",
        "run_id": "run-damaged",
        "attempt_id": "attempt-1",
        "step_id": "step-1",
        "check_id": "exact-period-ownership",
        "expected": "not_attributed",
        "actual": "requested_period_attributed",
        "disposition": "deviation",
        "issue_key": "transcript-period-attribution",
    }
    sidecar.write_text(
        json.dumps(good) + "\n{not json\n" + json.dumps(good) + "\n", encoding="utf-8"
    )
    cards_dir = tmp_path / "issues"
    summary = module.import_observations(sidecar, cards_dir=cards_dir)

    assert summary["damaged_lines"] == [2]
    assert summary["added"] == 1
    assert summary["skipped"] == 1
    card = cards_dir / "transcript-period-attribution.md"
    assert card.is_file()
    assert "not_attributed" in card.read_text(encoding="utf-8")


def test_the_controlled_replay_is_byte_for_byte_reproducible(tmp_path):
    """Two replays of one named fixture produce the same bytes, clock included."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "et_replay_repro", ROOT / "tools" / "replay_feedback_case.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    case = module.CASES["fmp-exact-period"]
    first = module.replay(case, out_path=tmp_path / "first.jsonl")
    second = module.replay(case, out_path=tmp_path / "second.jsonl")

    assert (tmp_path / "first.jsonl").read_bytes() == (
        tmp_path / "second.jsonl"
    ).read_bytes()
    assert (tmp_path / "first.jsonl.details.jsonl").read_bytes() == (
        tmp_path / "second.jsonl.details.jsonl"
    ).read_bytes()
    assert first["native"] == second["native"]
    assert first["details"][0]["started_at"] == module.FIXED_INSTANT
    assert first["details"][1]["finished_at"] == module.FIXED_INSTANT


def test_the_controlled_replay_never_opens_a_socket(monkeypatch, tmp_path):
    """The replay is the only reproduction entry; it must never download.

    Name resolution and connection attempts are blocked, not socket
    construction itself: importing ``requests`` probes the local IPv6 stack by
    creating a socket without connecting or resolving a name, and a download
    could not happen without a connect or a name lookup.
    """
    import importlib.util
    import socket

    spec = importlib.util.spec_from_file_location(
        "et_replay_feedback_case", ROOT / "tools" / "replay_feedback_case.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("the replay must not resolve a name or connect")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket.socket, "sendto", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket, "gethostbyname", refuse)
    monkeypatch.setattr(socket, "gethostbyname_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    report = module.replay(
        module.CASES["fmp-exact-period"], out_path=tmp_path / "feedback.jsonl"
    )
    assert report["matches_expected"] is True
    assert report["golden"]["match"] is True
    assert report["provider_calls"] == 1
    assert report["provider_calls_match"] is True
    assert report["deviation_observed"] is False
    assert report["deviation_matches_expectation"] is True
    assert read_jsonl(tmp_path / "feedback.jsonl")

    faulted = module.replay(
        module.CASES["fmp-foreign-period-fault"],
        out_path=tmp_path / "fault.jsonl",
    )
    assert faulted["deviation_observed"] is True
    assert faulted["issue_keys"] == ["transcript-period-attribution"]
    assert faulted["deviation_matches_expectation"] is True


def test_the_public_evidence_of_the_host_is_carried_through(monkeypatch, tmp_path):
    """The host may name the public evidence of its run; it stays opaque."""
    recorder = recorder_for(tmp_path)
    request = fmp_request(request_id="fb-evidence-001")
    page = fmp_payload()
    session = FakeSession({FMP_URL: FakeResponse(FMP_URL, page, content_type="application/json")})
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    transcript_tool.main(
        ["--request-stdin", "--include-source-payload"],
        _session_factory=lambda: session,
        _feedback_recorder=recorder,
        _feedback_evidence_ref="et-case:fmp-exact-period",
    )

    records = read_jsonl(tmp_path / "feedback.jsonl")
    assert records
    for record in records:
        assert record["evidence_ref"] == "et-case:fmp-exact-period"
        assert "details_ref" in record

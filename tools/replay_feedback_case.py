"""Replay one named ET fixture through the real transcript tool boundary.

This is the only reproduction entry the repair skill may use. No case can
reach a provider: the HTTP transport is either the in-process fake session or
the private worker launcher seam, so a replay never downloads and never spends
provider quota. Each named case fixes one request and one provider response,
states the native facts it must reproduce, and names the golden file it must
match when one exists. The old golden files are read, never rewritten.

    python tools/replay_feedback_case.py --case fmp-exact-period --out sidecar.jsonl
    python tools/replay_feedback_case.py --list
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
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
    FakeResponse,
    FakeSession,
    fmp_payload,
)

GOLDEN_DIR = ROOT / "tests" / "golden"

# Fake provider credential for the fake HTTP sessions below. It is a
# placeholder, never a real key; the replay asserts that this exact value
# never reaches a result, a receipt, an observation or a detail record.
REPLAY_FAKE_KEY = "fake-replay-key-for-offline-tests"

__all__ = ["CASES", "ReplayCase", "replay", "main"]


class _FixedDateTime:
    @staticmethod
    def now(tz=None):
        return datetime(2026, 9, 29, 12, 0, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class ReplayCase:
    """One named fixture: a fixed request plus a fixed provider answer."""

    name: str
    summary: str
    request: dict[str, Any]
    expected_status: str
    expected_error_code: str | None = None
    golden: str | None = None
    payload: bytes | None = None
    http_status: int = 200
    content_type: str = "application/json"
    key: str | None = REPLAY_FAKE_KEY
    fault_period: tuple[int, int] | None = None
    fault_suppress_receipt: bool = False
    argv: tuple[str, ...] = ("--request-stdin", "--include-source-payload")
    mode: str = "cooperative"
    timeout_seconds: int | None = None
    expect_deviation: bool = False
    issue_key: str | None = None
    provider_calls: int = 1


def _golden_request() -> dict[str, Any]:
    """The request of the existing producer golden, read but never rewritten."""
    raw = json.loads((GOLDEN_DIR / "fmp_v2.request.json").read_text(encoding="utf-8"))
    return dict(raw)


def _request(
    *,
    request_id: str,
    ticker: str = "MSFT",
    exchange: str = "nasdaq",
    fiscal_year: int = 2026,
    fiscal_quarter: int = 3,
    as_of_date: str = "2026-09-30",
    timeout_seconds: int = 10,
) -> dict[str, Any]:
    return {
        "schema_version": "earnings-transcript-request/1",
        "request_id": request_id,
        "ticker": ticker,
        "exchange": exchange,
        "fiscal_year": fiscal_year,
        "fiscal_quarter": fiscal_quarter,
        "as_of_date": as_of_date,
        "provider": "fmp",
        "download_authorized": True,
        "timeout_seconds": timeout_seconds,
        "max_body_bytes": 1000000,
    }


CASES: dict[str, ReplayCase] = {
    "fmp-exact-period": ReplayCase(
        name="fmp-exact-period",
        summary="The provider returns the exact requested quarter.",
        request=_golden_request(),
        expected_status="fetched",
        golden="fmp_v2.fetched.json",
        payload=fmp_payload(),
    ),
    "fmp-no-target-period": ReplayCase(
        name="fmp-no-target-period",
        summary="The provider holds no document for the requested quarter.",
        request=_request(request_id="fb-no-target-period-001"),
        expected_status="not_found",
        payload=b"[]",
    ),
    "fmp-wrong-quarter": ReplayCase(
        name="fmp-wrong-quarter",
        summary="The provider returns another quarter; the run must refuse it.",
        request=_golden_request(),
        expected_status="provenance_rejected",
        expected_error_code="provider_identity_or_host",
        golden="fmp_v2.wrong_quarter.json",
        payload=fmp_payload(period="Q2"),
    ),
    "fmp-entitlement-402": ReplayCase(
        name="fmp-entitlement-402",
        summary="The provider answers HTTP 402: the account has no entitlement.",
        request=_golden_request(),
        expected_status="unavailable",
        expected_error_code="provider_entitlement_required",
        golden="fmp_v2.entitlement_402.json",
        payload=b"",
        http_status=402,
    ),
    "fmp-credentials-missing": ReplayCase(
        name="fmp-credentials-missing",
        summary="No provider credential: the run must refuse before any request.",
        request=_golden_request(),
        expected_status="unavailable",
        expected_error_code="provider_credentials_missing",
        golden="fmp_v2.credentials_missing.json",
        key=None,
        provider_calls=0,
    ),
    "fmp-foreign-period-fault": ReplayCase(
        name="fmp-foreign-period-fault",
        summary=(
            "Controlled fault: the adapter accepts another quarter's call and "
            "claims the requested period."
        ),
        request=_request(request_id="fb-foreign-period-msft-2026q3"),
        expected_status="fetched",
        payload=fmp_payload(period="Q2"),
        fault_period=(2026, 3),
        expect_deviation=True,
        issue_key="transcript-period-attribution",
    ),
    "fmp-foreign-period-fault-alt": ReplayCase(
        name="fmp-foreign-period-fault-alt",
        summary=(
            "The same controlled fault on another company and period: the "
            "adjudicated cause stays one issue."
        ),
        request=_request(
            request_id="fb-foreign-period-aapl-2025q2",
            ticker="AAPL",
            fiscal_year=2025,
            fiscal_quarter=2,
            as_of_date="2025-09-30",
        ),
        expected_status="fetched",
        payload=fmp_payload(symbol="AAPL", period="Q1", year=2025, date="2025-04-30"),
        fault_period=(2025, 2),
        expect_deviation=True,
        issue_key="transcript-period-attribution",
    ),
    "fmp-worker-aborted": ReplayCase(
        name="fmp-worker-aborted",
        summary=(
            "The worker is stopped at the request deadline: the final usage is "
            "unknown and the provider is never asked twice."
        ),
        request=_request(request_id="fb-worker-aborted-001", timeout_seconds=2),
        expected_status="deadline_exceeded",
        expected_error_code="provider_deadline",
        argv=("--request-stdin", "--include-source-payload", "--report-usage"),
        mode="supervised",
        timeout_seconds=2,
    ),
    "usage-receipt-suppressed": ReplayCase(
        name="usage-receipt-suppressed",
        summary=(
            "The pre-fix publication behaviour re-created: the run promises a "
            "native final usage receipt and publishes none."
        ),
        request=_golden_request(),
        expected_status="invalid_request",
        expected_error_code="source_payload_not_valid_for_discovery",
        argv=(
            "--request-stdin",
            "--operation",
            "discover",
            "--include-source-payload",
            "--report-usage",
        ),
        fault_suppress_receipt=True,
        expect_deviation=True,
        issue_key="transcript-usage-receipt",
        provider_calls=0,
    ),
}


@contextlib.contextmanager
def _foreign_period_fault(period: tuple[int, int]) -> Iterator[None]:
    """Make the provider adapter accept any period as the requested one."""
    original = transcript_api._fmp_period
    transcript_api._fmp_period = lambda item: period
    try:
        yield
    finally:
        transcript_api._fmp_period = original


@contextlib.contextmanager
def _suppressed_receipt() -> Iterator[None]:
    """Re-create the pre-fix behaviour: the promised receipt is not published."""
    original = transcript_tool._publish_usage_receipt

    def _silent(receipt: dict[str, Any], report_usage: bool) -> None:
        return None

    transcript_tool._publish_usage_receipt = _silent
    try:
        yield
    finally:
        transcript_tool._publish_usage_receipt = original


def _session(case: ReplayCase) -> FakeSession:
    response = FakeResponse(
        FMP_URL,
        case.payload if case.payload is not None else b"",
        status_code=case.http_status,
        content_type=case.content_type,
    )
    return FakeSession({FMP_URL: response})


def _golden_comparison(case: ReplayCase, result: dict[str, Any]) -> dict[str, Any]:
    """Compare this run's native result with the existing golden, read-only."""
    if case.golden is None:
        return {"file": None, "match": None, "differing_keys": []}
    path = GOLDEN_DIR / case.golden
    expected = json.loads(path.read_text(encoding="utf-8"))
    differing = sorted(
        key for key in set(expected) | set(result) if expected.get(key) != result.get(key)
    )
    return {
        "file": f"tests/golden/{case.golden}",
        "match": not differing,
        "differing_keys": differing,
    }


def replay(
    case: ReplayCase,
    *,
    run_id: str | None = None,
    attempt_id: str | None = None,
    out_path: Path | None = None,
) -> dict[str, Any]:
    """Run one case and return its native facts and its new observations."""
    run = run_id or f"replay-{case.name}"
    attempt = attempt_id or "attempt-1"
    recorder = (
        transcript_feedback.FeedbackRecorder(out_path, run_id=run, attempt_id=attempt)
        if out_path is not None
        else None
    )
    request = dict(case.request)
    old_stdout, old_stderr = sys.stdout, sys.stderr
    old_stdin = sys.stdin
    old_datetime = transcript_api.datetime
    old_key = os.environ.get("FMP_API_KEY")
    old_key_file = os.environ.get("FMP_API_KEY_FILE")
    out, err = io.StringIO(), io.StringIO()
    provider_calls: list[str] = []
    temp_root: Path | None = None
    try:
        sys.stdin = io.StringIO(json.dumps(request))
        sys.stdout, sys.stderr = out, err
        os.environ.pop("FMP_API_KEY_FILE", None)
        if case.key is None:
            os.environ.pop("FMP_API_KEY", None)
        else:
            os.environ["FMP_API_KEY"] = case.key
        transcript_api.datetime = _FixedDateTime
        with _fault(case):
            if case.mode == "supervised":
                temp_root = Path(tempfile.mkdtemp(prefix="et-replay-"))
                calls_file = temp_root / "calls.jsonl"
                spec = fake_spec(
                    {
                        FMP_URL: StallResponse(
                            FMP_URL,
                            case.payload if case.payload is not None else b"",
                            stall_in_get=True,
                            content_type=case.content_type,
                        )
                    },
                    calls_file=calls_file,
                )
                exit_code = transcript_tool.main(
                    list(case.argv),
                    _retrieval_launcher=LAUNCHER,
                    _retrieval_spec=spec,
                    _retrieval_temp_root=temp_root,
                    _feedback_recorder=recorder,
                )
                provider_calls = get_urls(calls_file)
            else:
                session = _session(case)
                exit_code = transcript_tool.main(
                    list(case.argv),
                    _session_factory=lambda: session,
                    _feedback_recorder=recorder,
                )
                provider_calls = [url for url, _ in session.calls]
    finally:
        sys.stdin, sys.stdout, sys.stderr = old_stdin, old_stdout, old_stderr
        transcript_api.datetime = old_datetime
        if old_key is None:
            os.environ.pop("FMP_API_KEY", None)
        else:
            os.environ["FMP_API_KEY"] = old_key
        if old_key_file is None:
            os.environ.pop("FMP_API_KEY_FILE", None)
        else:
            os.environ["FMP_API_KEY_FILE"] = old_key_file
        if temp_root is not None:
            _remove_tree(temp_root)
    result = json.loads(out.getvalue())
    observations = _read_jsonl(out_path) if out_path is not None else []
    details = _read_jsonl(
        Path(str(out_path) + ".details.jsonl") if out_path is not None else None
    )
    return {
        "case": case.name,
        "summary": case.summary,
        "run_id": run,
        "attempt_id": attempt,
        "exit_code": exit_code,
        "native": {
            "status": result.get("status"),
            "error_code": result.get("error_code"),
            "provider": result.get("provider"),
            "fiscal_period": result.get("fiscal_period"),
            "provider_payload_sha256": result.get("provider_payload_sha256"),
            "canonical_content_sha256": result.get("canonical_content_sha256"),
            "content_bytes": result.get("content_bytes"),
        },
        "expected": {
            "status": case.expected_status,
            "error_code": case.expected_error_code,
        },
        "matches_expected": (
            result.get("status") == case.expected_status
            and result.get("error_code") == case.expected_error_code
        ),
        "golden": _golden_comparison(case, result),
        "provider_calls": len(provider_calls),
        "expect_deviation": case.expect_deviation,
        "issue_key": case.issue_key,
        "observations": observations,
        "details": details,
        "stderr": err.getvalue(),
    }


def _fault(case: ReplayCase) -> contextlib.AbstractContextManager[Any]:
    if case.fault_suppress_receipt:
        return _suppressed_receipt()
    if case.fault_period is None:
        return contextlib.nullcontext()
    return _foreign_period_fault(case.fault_period)


def _read_jsonl(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _remove_tree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replay one named ET fixture offline and emit its observations."
    )
    parser.add_argument("--case", choices=sorted(CASES), help="The named fixture to replay.")
    parser.add_argument("--list", action="store_true", help="List the named fixtures.")
    parser.add_argument("--out", help="Sidecar path for the observations (JSONL).")
    parser.add_argument("--run-id", help="Run number of this replay.")
    parser.add_argument("--attempt-id", help="Attempt number of this replay.")
    args = parser.parse_args(argv)
    if args.list:
        for name in sorted(CASES):
            case = CASES[name]
            print(f"{name}\t{case.summary}")
        return 0
    if not args.case:
        parser.error("--case is required")
    out_path = Path(args.out) if args.out else None
    if out_path is None:
        configured = os.environ.get(transcript_feedback.FEEDBACK_OUT_ENV)
        out_path = Path(configured) if configured else None
    report = replay(
        CASES[args.case],
        run_id=args.run_id,
        attempt_id=args.attempt_id,
        out_path=out_path,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["matches_expected"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

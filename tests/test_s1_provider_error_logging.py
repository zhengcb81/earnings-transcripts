"""S1-04 tests for structural, credential-free provider and worker diagnostics."""

from __future__ import annotations

from hashlib import sha256
import json
import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import retrieval_runtime  # noqa: E402
import transcript_api  # noqa: E402
from transcript_provider_settings import ProviderSettings  # noqa: E402


PRIVATE_WORDS = (
    "FAKE_QUERY_987",
    "FAKE_BEARER_987",
    "FAKE_HEADER_987",
    "FAKE_QUOTED_987",
    "FAKE_COOKIE_987",
    "FAKE_ARBITRARY_987",
)
PRIVATE_ERROR = (
    "https://example.invalid/api?apikey=FAKE_QUERY_987"
    " Authorization: Bearer FAKE_BEARER_987"
    """ headers={'Authorization': "Bearer FAKE_HEADER_987", 'x-api-key': 'FAKE_QUOTED_987'}"""
    " Cookie: session=FAKE_COOKIE_987 FAKE_ARBITRARY_987"
)

SETTINGS = ProviderSettings(motley_fool_enabled=True)


class _CrashingSession:
    headers: dict[str, str] = {}

    def get(self, *args, **kwargs):
        try:
            raise ValueError(PRIVATE_ERROR)
        except ValueError as exc:
            raise KeyError(PRIVATE_ERROR) from exc

    def close(self):
        pass


def _fetch_request():
    return {
        "schema_version": "earnings-transcript-request/1",
        "request_id": "s1-et-001",
        "ticker": "NVDA",
        "exchange": "nasdaq",
        "fiscal_year": 2025,
        "fiscal_quarter": 2,
        "as_of_date": "2026-07-01",
        "provider": "motley_fool",
        "download_authorized": True,
        "timeout_seconds": 10,
        "max_body_bytes": 1024 * 1024,
    }


def _list_request():
    return {
        key: value for key, value in _fetch_request().items()
        if key in {"ticker", "exchange", "as_of_date", "request_id", "timeout_seconds", "download_authorized"}
    }


def assert_private_data_absent(surface):
    assert all(word not in surface for word in PRIVATE_WORDS)
    assert "example.invalid" not in surface
    assert str(ROOT) not in surface


@pytest.mark.parametrize("operation", ["fetch", "list"])
def test_unexpected_provider_failure_retains_identity_and_cause_without_exception_text(caplog, operation):
    with caplog.at_level(logging.ERROR, logger="transcript_api"):
        if operation == "fetch":
            answer = transcript_api.fetch_transcript(
                _fetch_request(), session_factory=_CrashingSession, provider_settings=SETTINGS,
            )
        else:
            answer = transcript_api.list_transcript_candidates(
                **_list_request(), session_factory=_CrashingSession, provider_settings=SETTINGS,
            )

    assert (answer["status"], answer["error_code"]) == ("provider_error", "unexpected_provider_failure")
    assert_private_data_absent(caplog.text)
    records = [record for record in caplog.records if "unexpected_provider_failure" in record.getMessage()]
    entry, = records
    assert entry.exc_info is None
    assert "request_ref=" + sha256(b"s1-et-001").hexdigest()[:16] in entry.getMessage()
    assert "KeyError" in entry.getMessage() and "ValueError" in entry.getMessage()
    assert "cause" in entry.getMessage()
    assert "transcript_api.py" in entry.getMessage()


def worker_session(spec, context):
    """Offline session factory used by the actual internal worker process."""
    workdir = Path(context["workdir"])
    sys.stderr.write(PRIVATE_ERROR + "\n")
    # Legacy worker_stderr.log was named and remained in this directory even
    # though its raw contents were deleted only after the parent returned.
    Path(spec["proof"]).write_text(
        json.dumps({"raw_stderr_file": (workdir / "worker_stderr.log").exists()}),
        encoding="utf-8",
    )
    if spec["hard_exit"]:
        try:
            raise ValueError(PRIVATE_ERROR)
        except ValueError as exc:
            raise SystemExit(17) from exc
    return _CrashingSession()


@pytest.mark.parametrize("hard_exit", [False, True])
def test_real_worker_keeps_structural_details_without_spooling_stderr(tmp_path, caplog, hard_exit):
    proof = tmp_path / "proof.json"
    launcher = "tests.test_s1_provider_error_logging:worker_session"
    with caplog.at_level(logging.ERROR, logger="retrieval_runtime"):
        outcome = retrieval_runtime.run_retrieval(
            "list", _list_request(), remaining_seconds=15, requests_left=2,
            response_bytes_left=128_000, provider_settings=SETTINGS, launcher=launcher,
            launcher_spec={"proof": str(proof), "hard_exit": hard_exit}, temp_root=tmp_path,
        )
    assert json.loads(proof.read_text(encoding="utf-8")) == {"raw_stderr_file": False}
    if hard_exit:
        assert (outcome.reason, outcome.exit_code, outcome.usage) == ("worker_failure", 17, None)
        expected_types = ["SystemExit", "ValueError"]
    else:
        assert (outcome.reason, outcome.exit_code) == (None, 0)
        assert outcome.result["error_code"] == "unexpected_provider_failure"
        assert outcome.usage["requests_used"] == 1
        expected_types = ["KeyError", "ValueError"]
    assert list(tmp_path.iterdir()) == [proof]
    assert outcome.diagnostics["worker_exceptions"] == expected_types
    trace = outcome.diagnostics["worker_exception_trace"]
    assert [item["type"] for item in trace] == expected_types
    outer = trace[0]["frames"]
    assert all(set(frame) == {"file", "line", "function"} for frame in outer)
    assert any(frame["file"] == "test_s1_provider_error_logging.py" for frame in outer)
    assert trace[1]["relation"] == "cause"
    assert_private_data_absent(caplog.text + json.dumps(outcome.diagnostics))
    assert expected_types[0] in caplog.text

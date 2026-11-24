"""S1-04 regression tests: unexpected_provider_failure logging and worker stderr preservation."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import retrieval_runtime  # noqa: E402
import transcript_api  # noqa: E402
from transcript_provider_settings import ProviderSettings  # noqa: E402


def test_unexpected_provider_failure_logs_exception_traceback(caplog):
    class _CrashingSession:
        headers: dict[str, str] = {}

        def get(self, *args, **kwargs):
            raise KeyError("unexpected-internal-key-bug")

        def close(self):
            pass

    req = {
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
    with caplog.at_level(logging.ERROR, logger="transcript_api"):
        res = transcript_api.fetch_transcript(
            req,
            session_factory=lambda: _CrashingSession(),
            provider_settings=ProviderSettings(motley_fool_enabled=True),
        )

    assert res["status"] == "provider_error"
    assert res["error_code"] == "unexpected_provider_failure"
    assert any(
        "unexpected_provider_failure" in rec.getMessage() and rec.exc_info is not None
        for rec in caplog.records
    )


def test_final_stderr_type_extracts_exception_name_even_when_message_has_url(tmp_path):
    stderr_file = tmp_path / "worker_stderr.log"
    stderr_file.write_text(
        "Traceback (most recent call last):\n"
        '  File "retrieval_worker.py", line 42, in main\n'
        "ValueError: malformed response from https://example.com/api/v1\n",
        encoding="utf-8",
    )
    assert retrieval_runtime._final_stderr_type(stderr_file) == "ValueError"


def test_child_diagnostics_logs_redacted_stderr_tail_before_workdir_removal(tmp_path, caplog):
    stderr_file = tmp_path / "worker_stderr.log"
    stderr_file.write_text(
        "Traceback (most recent call last):\n"
        '  File "C:\\Users\\secret\\worker.py", line 99, in run\n'
        "RuntimeError: boom apikey=TOPSECRET123\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.ERROR, logger="retrieval_runtime"):
        diag = retrieval_runtime._child_diagnostics(
            workdir=tmp_path,
            stderr_path=stderr_file,
            exit_code=1,
            stage="worker_exit",
        )
    assert diag["worker_stderr_type"] == "RuntimeError"
    assert any("RuntimeError" in rec.getMessage() and "TOPSECRET123" not in rec.getMessage() for rec in caplog.records)

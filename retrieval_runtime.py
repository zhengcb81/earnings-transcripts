"""Parent-side supervisor that makes provider retrieval a hard deadline.

One retrieval operation is executed by one internal worker subprocess
(``retrieval_worker.py``). The parent starts a monotonic deadline when the
formal operation starts, waits with exactly that remaining float budget,
terminates then kills the worker inside a single fixed cleanup grace, and only
then reads the bounded atomic result file. A result that finishes after the
deadline is never accepted as fetched, and a worker that dies without a
trustworthy usage report is recorded as unknown consumption.

The child records structural failure details in its private workdir. Its raw
stderr may contain a provider's request data and is discarded at launch. The
parent reads and logs the diagnostic after the wait; only the owner merges it
into the detail record. This leaves public results and usage semantics intact.

The worker envelope is internal: it never appears on the public tool stdout,
never carries credentials (the FMP key travels only through the controlled
process environment), and never changes any request/response schema.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from retrieval_diagnostics import read_exception_trace, safe_exception_types
from retrieval_protocol import (
    REQUEST_FILENAME,
    RESULT_FILENAME,
    WORKER_ERROR_FILENAME,  # re-exported: the parent reads what the child wrote
    WORKER_ERROR_SCHEMA,
    WORKER_PROTOCOL,  # noqa: F401  re-exported: existing callers/tests read it here
    build_request_envelope,
    encode_envelope,
    expected_request_id,
    expected_schemas,
    parse_result_envelope,
    result_transfer_limit,
)
from transcript_feedback import FEEDBACK_DISABLED_ENV
from transcript_provider_settings import DEFAULT_PROVIDER_SETTINGS, ProviderSettings

_logger = logging.getLogger(__name__)
_WORKER_PHASES = {"envelope", "budget", "launcher", "dispatch", "encode", "write"}

WORKER_PATH = Path(__file__).resolve().parent / "retrieval_worker.py"
# One fixed total for terminate → kill → wait; every wait in one reclamation
# shares this window, no wait gets a fresh grace.
CLEANUP_GRACE_SECONDS = 1.0


@dataclass(frozen=True)
class RetrievalOutcome:
    """Result of one supervised retrieval.

    ``result`` is the existing wire result, or ``None`` when the worker outcome
    cannot be trusted. ``usage`` is the internal consumption report, or
    ``None`` when it is unknown (never substitute zero). ``reason`` is
    ``None`` | ``"deadline"`` | ``"worker_failure"``. ``diagnostics`` is the
    sanitized supervisor/child diagnostic, or ``None`` on a trusted result.
    """

    result: dict[str, Any] | None
    usage: dict[str, Any] | None
    reason: str | None
    exit_code: int | None
    diagnostics: dict[str, Any] | None = field(default=None)


def _read_worker_error(workdir: Path) -> dict[str, Any]:
    """The child's own sanitized diagnostic; unreadable stays unknown."""
    try:
        payload = json.loads((workdir / WORKER_ERROR_FILENAME).read_bytes())
    except (OSError, ValueError, RecursionError):
        return {}
    if not isinstance(payload, dict) or payload.get("schema_version") != WORKER_ERROR_SCHEMA:
        return {}
    phase = payload.get("phase")
    trace = read_exception_trace(payload.get("exception_trace"))
    exceptions = [fact["type"] for fact in trace] or safe_exception_types(payload.get("exception_types"))
    result = {
        "worker_phase": phase if isinstance(phase, str) and phase in _WORKER_PHASES else None,
        "worker_exceptions": exceptions,
    }
    if trace:
        result["worker_exception_trace"] = trace
    return result


def _child_diagnostics(
    *,
    workdir: Path,
    exit_code: int | None,
    stage: str,
    detail: str | None = None,
) -> dict[str, Any]:
    """One sanitized diagnostic dict for this supervised retrieval."""
    child = _read_worker_error(workdir)
    exceptions = child.get("worker_exceptions") or []
    if exceptions or exit_code not in (None, 0):
        _logger.error(
            "retrieval worker failure stage=%s detail=%s exit_code=%s worker_phase=%s exception_trace=%s",
            stage,
            detail,
            exit_code,
            child.get("worker_phase"),
            child.get("worker_exception_trace", exceptions),
        )
    return {
        "supervisor_stage": stage,
        "supervisor_detail": detail,
        "worker_exit_code": exit_code,
        "worker_pid": None,
        "worker_phase": None,
        "worker_exceptions": None,
        **child,
        "worker_stderr_type": exceptions[0] if exceptions else None,
        "load_failure": None,
    }


def run_retrieval(
    operation: str,
    payload: dict[str, Any],
    *,
    remaining_seconds: float,
    requests_left: int | None,
    response_bytes_left: int | None,
    include_source_payload: bool = False,
    provider_settings: ProviderSettings = DEFAULT_PROVIDER_SETTINGS,
    fmp_api_key: str | None = None,
    launcher: str | None = None,
    launcher_spec: dict[str, Any] | None = None,
    temp_root: Path | None = None,
) -> RetrievalOutcome:
    """Run one provider retrieval in a worker under one absolute deadline.

    ``remaining_seconds`` is the caller's remaining float budget (a batch
    passes its shared remaining time, never a fresh full budget per file).
    ``requests_left`` / ``response_bytes_left`` are the batch quotas the worker
    must enforce mid-flight independently; each ``None`` omits only that quota.
    A response-only operation must not silently become unlimited.
    """
    deadline = time.monotonic() + max(0.0, remaining_seconds)
    if remaining_seconds <= 0:
        return RetrievalOutcome(
            result=None,
            usage={
                "requests_used": 0,
                "response_bytes_used": 0,
                "exhausted": None,
            },
            reason="deadline",
            exit_code=None,
            diagnostics={
                "supervisor_stage": "deadline",
                "supervisor_detail": "budget_already_spent",
                "worker_exit_code": None,
                "worker_pid": None,
                "worker_phase": None,
                "worker_exceptions": None,
                "worker_stderr_type": None,
                "load_failure": None,
            },
        )

    if temp_root is not None:
        temp_root = Path(temp_root)
        temp_root.mkdir(parents=True, exist_ok=True)
    workdir = Path(
        tempfile.mkdtemp(
            prefix="et-retrieval-",
            dir=str(temp_root) if temp_root is not None else None,
        )
    )
    request_path = workdir / REQUEST_FILENAME
    result_path = workdir / RESULT_FILENAME

    envelope = build_request_envelope(
        operation,
        payload,
        include_source_payload=include_source_payload,
        provider_settings=provider_settings,
        budget={
            "seconds_remaining": remaining_seconds,
            "requests_left": requests_left,
            "response_bytes_left": response_bytes_left,
        },
        launcher=launcher,
        launcher_spec=launcher_spec,
    )
    request_path.write_bytes(encode_envelope(envelope))

    env = os.environ.copy()
    if fmp_api_key:
        env["FMP_API_KEY"] = fmp_api_key
    # The parent owns every feedback location; a worker path that ever grew
    # recording code of its own must stay silent. Cleared here for every
    # launch, on top of the caller's own clearing.
    env[FEEDBACK_DISABLED_ENV] = "1"
    process = subprocess.Popen(
        [sys.executable, "-u", str(WORKER_PATH), str(request_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )

    def child_diagnostics(stage: str, detail: str | None = None) -> dict[str, Any]:
        return _child_diagnostics(
            workdir=workdir,
            exit_code=process.returncode,
            stage=stage,
            detail=detail,
        ) | {"worker_pid": process.pid}

    if not _wait_until(process, deadline):
        stopped = _stop_process(process)
        diagnostics = child_diagnostics(
            "deadline", None if stopped else "exit_unconfirmed"
        )
        if not stopped:
            _report_cleanup_failure(
                f"worker exit unconfirmed within grace; keeping {workdir}"
            )
            return RetrievalOutcome(None, None, "deadline", None, diagnostics)
        _remove_workdir(workdir)
        return RetrievalOutcome(
            None, None, "deadline", process.returncode, diagnostics
        )

    exit_code = process.returncode
    if exit_code != 0:
        diagnostics = child_diagnostics("worker_exit")
        _remove_workdir(workdir)
        return RetrievalOutcome(None, None, "worker_failure", exit_code, diagnostics)

    result, usage, reason = _load_result_file(
        result_path,
        expected_request_id=expected_request_id(operation, payload),
        expected_schemas=expected_schemas(operation, include_source_payload),
        deadline=deadline,
        max_bytes=result_transfer_limit(payload, response_bytes_left),
    )
    if reason is not None:
        diagnostics = child_diagnostics("result_load")
        diagnostics["load_failure"] = (
            "result_after_deadline"
            if reason == "deadline" and usage is not None
            else _result_load_cause(
                result_path, max_bytes=result_transfer_limit(payload, response_bytes_left)
            )
        )
        _remove_workdir(workdir)
        return RetrievalOutcome(result, usage, reason, exit_code, diagnostics)
    diagnostics = (
        child_diagnostics("provider_error")
        if result is not None and result.get("error_code") == "unexpected_provider_failure"
        else None
    )
    _remove_workdir(workdir)
    return RetrievalOutcome(result, usage, None, exit_code, diagnostics)


def _wait_until(process: subprocess.Popen, deadline: float) -> bool:
    """Wait for exit with exactly the remaining deadline; never round it."""
    try:
        process.wait(timeout=max(0.0, deadline - time.monotonic()))
        return True
    except subprocess.TimeoutExpired:
        return process.poll() is not None


def _stop_process(process: subprocess.Popen) -> bool:
    """terminate, then kill if needed — all waits share one grace window."""
    grace_deadline = time.monotonic() + CLEANUP_GRACE_SECONDS
    try:
        process.terminate()
    except OSError:
        pass
    killed = False
    while True:
        try:
            remaining = grace_deadline - time.monotonic()
            if remaining <= 0:
                break
            process.wait(timeout=remaining)
            return True
        except subprocess.TimeoutExpired:
            if killed:
                continue
            killed = True
            try:
                process.kill()
            except OSError:
                pass
    return process.poll() is not None


def _report_cleanup_failure(detail: str) -> None:
    print(f"error: retrieval_cleanup_failed: {detail}", file=sys.stderr)


def _remove_workdir(workdir: Path) -> None:
    """Delete this operation's temp results only after exit is confirmed."""
    try:
        shutil.rmtree(workdir)
    except FileNotFoundError:
        return
    except OSError as exc:
        _report_cleanup_failure(f"{exc}; keeping {workdir}")


def _result_load_cause(path: Path, *, max_bytes: int) -> str:
    """The sanitized parent-side load-failure word for the diagnostics.

    Kept out of ``_load_result_file`` so the loader's 3-tuple contract —
    frozen by the runtime contract isolation test — stays unchanged; the
    classification re-derives the same bounded conditions on failure paths
    only.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return "result_unreadable"
    if size > max_bytes:
        return "result_too_large"
    try:
        json.loads(path.read_bytes())
    except (OSError, ValueError):
        return "result_unparseable"
    return "result_rejected"


def _load_result_file(
    path: Path,
    *,
    expected_request_id: str | None,
    expected_schemas: set[str] | None,
    deadline: float,
    max_bytes: int,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
    """Bounded read + protocol validation + post-read deadline check.

    The protocol module owns envelope identity/shape/usage rules; this function
    only applies the file-size bound and the wall-clock deadline.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return None, None, "worker_failure"
    if size > max_bytes:
        return None, None, "worker_failure"
    try:
        envelope = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None, None, "worker_failure"
    result, usage, reason = parse_result_envelope(
        envelope,
        expected_request_id=expected_request_id,
        expected_schemas=expected_schemas,
    )
    if reason is not None:
        return result, usage, reason
    if time.monotonic() > deadline:
        return None, usage, "deadline"
    return result, usage, None

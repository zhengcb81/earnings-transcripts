"""Parent-side supervisor that makes provider retrieval a hard deadline.

One retrieval operation is executed by one internal worker subprocess
(``retrieval_worker.py``). The parent starts a monotonic deadline when the
formal operation starts, waits with exactly that remaining float budget,
stops the worker tree of an expired operation inside a fixed cleanup grace, and
only then reads the bounded atomic result file. A result that finishes after the
deadline is never accepted as fetched, and a worker that dies without a
trustworthy usage report is recorded as unknown consumption.

The child records structural failure details in its private workdir. The
parent drains stderr and retains only known exception categories as a fallback
when those details are unavailable. Raw stderr is never stored or forwarded.
Only the owner merges diagnostics into the local log and feedback record.

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
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypedDict

from retrieval_diagnostics import WorkerStderr, exception_trace, log_exception_trace, read_exception_trace, safe_exception_types
from retrieval_lifecycle import TRACE_ENV, RetrievalTrace
from retrieval_process import worker_launch_options, kill_worker_group
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
from runtime_logging import record_local_failure
from runtime_scope import (
    FEEDBACK_ATTEMPT_ID_ENV,
    FEEDBACK_DIR_ENV,
    FEEDBACK_DISABLED_ENV,
    FEEDBACK_OUT_ENV,
    FEEDBACK_RUN_ID_ENV,
    public_request,
    run_identity,
    safe_public_word,
)
from transcript_provider_settings import DEFAULT_PROVIDER_SETTINGS, ProviderSettings

_logger = logging.getLogger(__name__)
_WORKER_PHASES = {"envelope", "budget", "bootstrap", "launcher", "dispatch", "encode", "write", "process_scope"}
_WORKER_REFUSALS = {
    "invalid_request_envelope", "invalid_provider_settings", "invalid_budget",
    "invalid_budget_seconds", "invalid_budget_quota", "result_transfer_limit",
}

WORKER_PATH = Path(__file__).resolve().parent / "retrieval_worker.py"
# Reaping a stopped worker shares one fixed cleanup window.
CLEANUP_GRACE_SECONDS = 1.0


class _CleanupFailure(TypedDict):
    stage: Literal["remove_workdir", "exit_unconfirmed", "stop_descendants"]
    exception_type: str | None
    errno: int | None


@dataclass(frozen=True)
class RetrievalOutcome:
    """Result of one supervised retrieval.

    ``result`` is the existing wire result, or ``None`` when the worker outcome
    cannot be trusted. ``usage`` is the internal consumption report, or
    ``None`` when it is unknown (never substitute zero). ``reason`` is
    ``None`` | ``"deadline"`` | ``"worker_failure"`` | ``"worker_not_started"``. ``diagnostics`` is the
    sanitized supervisor/child diagnostic, or ``None`` on normal completion.
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
    reason = payload.get("failure_code")
    if isinstance(reason, str) and reason in _WORKER_REFUSALS:
        result["worker_failure_code"] = reason
    if trace:
        result["worker_exception_trace"] = trace
    return result


def _child_diagnostics(
    *,
    workdir: Path,
    exit_code: int | None,
    stage: str,
    detail: str | None = None,
    log_to_console: bool = True,
    stderr: WorkerStderr,
) -> dict[str, Any]:
    """One sanitized diagnostic dict for this supervised retrieval."""
    child = _read_worker_error(workdir)
    if exit_code is not None:
        stderr.finish()
    if not child.get("worker_exception_trace") and stderr.exception_trace:
        child["worker_exception_trace"] = stderr.exception_trace
        child["worker_exceptions"] = [fact["type"] for fact in stderr.exception_trace]
    exceptions = child.get("worker_exceptions") or []
    stderr_type = exceptions[0] if exceptions else stderr.type_hint
    if log_to_console and (exceptions or exit_code not in (None, 0)):
        _logger.error(
            "retrieval worker failure stage=%s detail=%s exit_code=%s worker_phase=%s "
            "stderr_type=%s exception_trace=%s",
            stage,
            detail,
            exit_code,
            child.get("worker_phase"),
            stderr_type,
            child.get("worker_exception_trace", exceptions),
        )
    diagnostics = {
        "supervisor_stage": stage,
        "supervisor_detail": detail,
        "worker_exit_code": exit_code,
        "worker_pid": None,
        "worker_phase": None,
        "worker_exceptions": None,
        **child,
        "worker_stderr_type": stderr_type,
        "load_failure": None,
    }
    if stderr.diagnostic_write_error is not None:
        diagnostics["worker_diagnostic_write_error"] = stderr.diagnostic_write_error
    return diagnostics


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
    log_worker_diagnostics: bool = True,
    diagnostic_identity: tuple[str, str] | None = None,
) -> RetrievalOutcome:
    """Run one provider retrieval in a worker under one absolute deadline.

    ``remaining_seconds`` is the caller's remaining float budget (a batch
    passes its shared remaining time, never a fresh full budget per file).
    ``requests_left`` / ``response_bytes_left`` are the batch quotas the worker
    must enforce mid-flight independently; each ``None`` omits only that quota.
    A response-only operation must not silently become unlimited.
    """
    deadline = time.monotonic() + max(0.0, remaining_seconds)
    diagnostic_identity = diagnostic_identity or run_identity()
    trace = RetrievalTrace.create(operation, payload, diagnostic_identity)
    history_written = trace.emit("started", request=public_request(payload), usage=None)
    if remaining_seconds <= 0:
        outcome = RetrievalOutcome(
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
        trace.emit("supervisor_finished", result_status="deadline_exceeded",
                   provider_activity="not_started", usage=outcome.usage)
        return outcome

    workdir: Path | None = None
    process: subprocess.Popen | None = None
    stderr: WorkerStderr | None = None

    def child_diagnostics(stage: str, detail: str | None = None) -> dict[str, Any]:
        assert workdir is not None and process is not None and stderr is not None
        return _child_diagnostics(
            workdir=workdir,
            exit_code=process.returncode,
            stage=stage,
            detail=detail,
            log_to_console=log_worker_diagnostics,
            stderr=stderr,
        ) | {"worker_pid": process.pid}

    def finish(
        result: dict[str, Any] | None,
        usage: dict[str, Any] | None,
        reason: str | None,
        diagnostics: dict[str, Any] | None,
        *,
        exit_confirmed: bool = True,
    ) -> RetrievalOutcome:
        if process is not None and process.stdin is not None:
            with suppress(OSError):
                process.stdin.close()
        cleanup = None
        if process is not None and exit_confirmed:
            try:
                kill_worker_group(process.pid)
            except OSError as error:
                cleanup = _CleanupFailure(
                    stage="stop_descendants", exception_type=type(error).__name__, errno=error.errno
                )
        if workdir is not None and cleanup is None:
            cleanup = (
                _remove_workdir(workdir) if exit_confirmed
                else _CleanupFailure(stage="exit_unconfirmed", exception_type=None, errno=None)
            )
        if cleanup is not None:
            if diagnostics is None:
                diagnostics = child_diagnostics("cleanup")
            diagnostics["cleanup_failure"] = cleanup
            if log_worker_diagnostics:
                print(
                    "error: retrieval_cleanup_failed: "
                    f"stage={cleanup['stage']} exception_type={cleanup['exception_type']} "
                    f"errno={cleanup['errno']}",
                    file=sys.stderr,
                )
        if stderr is not None and diagnostics is None and exit_confirmed:
            stderr.finish()
        recorded = trace.emit(
            "supervisor_finished",
            result_status=safe_public_word(result.get("status"), fallback="status") if result else None,
            outcome=reason or "completed",
            usage=usage,
            worker_exit_code=process.returncode if process else None,
            provider_activity=("not_started" if process is None else "possibly_dispatched"),
            diagnostics=diagnostics,
        )
        if not (recorded and history_written):
            diagnostics = {**(diagnostics or {}), "lifecycle_log_unavailable": True}
        if diagnostics is not None:
            record_local_failure("retrieval_worker", diagnostics, identity=diagnostic_identity)
        return RetrievalOutcome(result, usage, reason, process.returncode if process else None, diagnostics)

    # Prepare, stage the envelope and launch within the same attempt as the
    # worker result. No provider activity is possible until Popen succeeds.
    stage = "prepare_workdir"
    try:
        if temp_root is not None:
            temp_root = Path(temp_root)
            temp_root.mkdir(parents=True, exist_ok=True)
        workdir = Path(tempfile.mkdtemp(
            prefix="et-retrieval-", dir=str(temp_root) if temp_root is not None else None,
        ))
        stage = "write_request"
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

        stage = "start_worker"
        env = os.environ.copy()
        if fmp_api_key:
            env["FMP_API_KEY"] = fmp_api_key
        # Only the owning parent can publish feedback artifacts.
        for name in (FEEDBACK_OUT_ENV, FEEDBACK_RUN_ID_ENV, FEEDBACK_ATTEMPT_ID_ENV, FEEDBACK_DIR_ENV):
            env.pop(name, None)
        env[FEEDBACK_DISABLED_ENV] = "1"
        env[TRACE_ENV] = trace.environment()
        history_written = trace.emit("worker_start_requested", usage=None) and history_written
        process = subprocess.Popen(
            [sys.executable, "-u", "-X", "faulthandler", str(WORKER_PATH), str(request_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=env,
            **worker_launch_options(),
        )
    except (OSError, TypeError, ValueError) as error:
        exception_facts = exception_trace(error)
        diagnostics = {
            "supervisor_stage": stage,
            "supervisor_exception_trace": exception_facts,
            "provider_activity": "not_started",
            "worker_exit_code": None,
            "worker_pid": None,
        }
        if log_worker_diagnostics:
            log_exception_trace(_logger, logging.ERROR, "retrieval worker did not start stage=%s", error, stage)
        return finish(
            None, {"requests_used": 0, "response_bytes_used": 0, "exhausted": None},
            "worker_not_started", diagnostics,
        )
    assert process.stderr is not None
    stderr = WorkerStderr(process.stderr)

    if not _wait_until(process, deadline):
        stopped = _stop_process(process)
        diagnostics = child_diagnostics(
            "deadline", None if stopped else "exit_unconfirmed"
        )
        return finish(None, None, "deadline", diagnostics, exit_confirmed=stopped)

    if process.returncode != 0:
        return finish(None, None, "worker_failure", child_diagnostics("worker_exit"))

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
        return finish(result, usage, reason, diagnostics)
    diagnostics = (
        child_diagnostics("provider_error")
        if result is not None and result.get("error_code") == "unexpected_provider_failure"
        else None
    )
    return finish(result, usage, None, diagnostics)


def _wait_until(process: subprocess.Popen, deadline: float) -> bool:
    """Wait for exit with exactly the remaining deadline; never round it."""
    try:
        process.wait(timeout=max(0.0, deadline - time.monotonic()))
        return True
    except subprocess.TimeoutExpired:
        return process.poll() is not None


def _stop_process(process: subprocess.Popen) -> bool:
    """Hard-stop the owned tree and give the OS one bounded interval to reap it."""
    grace_deadline = time.monotonic() + CLEANUP_GRACE_SECONDS
    with suppress(OSError):
        kill_worker_group(process.pid)
    # On Windows, the worker's own job handle closes when this process exits.
    # On POSIX this also covers a failed group signal to the direct child.
    with suppress(OSError):
        process.kill()
    return _wait_until(process, grace_deadline)


def _remove_workdir(workdir: Path) -> _CleanupFailure | None:
    """Reclaim this operation's temp results, retaining only structural failure facts."""
    try:
        shutil.rmtree(workdir)
    except FileNotFoundError:
        return None
    except OSError as exc:
        types = safe_exception_types([type(exc).__name__])
        return _CleanupFailure(
            stage="remove_workdir",
            exception_type=types[0] if types else "OSError",
            errno=exc.errno if type(exc.errno) is int else None,
        )
    return None


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

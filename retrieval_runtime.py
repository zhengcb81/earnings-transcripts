"""Parent-side supervisor that makes provider retrieval a hard deadline.

One retrieval operation is executed by one internal worker subprocess
(``retrieval_worker.py``). The parent starts a monotonic deadline when the
formal operation starts, waits with exactly that remaining float budget,
terminates then kills the worker inside a single fixed cleanup grace, and only
then reads the bounded atomic result file. A result that finishes after the
deadline is never accepted as fetched, and a worker that dies without a
trustworthy usage report is recorded as unknown consumption.

The worker envelope is internal: it never appears on the public tool stdout,
never carries credentials (the FMP key travels only through the controlled
process environment), and never changes any request/response schema.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from transcript_api import (
    DISCOVERY_RESULT_SCHEMA,
    MAX_BODY_BYTES,
    MAX_SOURCE_PAYLOAD_RESULT_BYTES,
    RESULT_SCHEMA,
    RESULT_SCHEMA_WITH_SOURCE_PAYLOAD,
    DEFAULT_PROVIDER_SETTINGS,
    ProviderSettings,
)

WORKER_PROTOCOL = "et-retrieval-worker/1"
WORKER_PATH = Path(__file__).resolve().parent / "retrieval_worker.py"
# One fixed total for terminate → kill → wait; every wait in one reclamation
# shares this window, no wait gets a fresh grace.
CLEANUP_GRACE_SECONDS = 1.0
MAX_REQUEST_FILE_BYTES = 4 * 1024 * 1024
# JSON escaping expands a byte by at most 6x (\uXXXX for control characters).
# content_utf8 and the extracted title are each bounded by one capped page;
# the opt-in base64 payload (mutually exclusive with content_utf8) is bounded
# by MAX_SOURCE_PAYLOAD_RESULT_BYTES. The ceiling never rejects a legal
# maximum-size payload; normal results are kilobytes.
MAX_RESULT_BYTES = (
    6 * (2 * MAX_BODY_BYTES) + MAX_SOURCE_PAYLOAD_RESULT_BYTES + 2 * 1024 * 1024
)
_REQUEST_FILENAME = "request.json"
_RESULT_FILENAME = "result.json"


@dataclass(frozen=True)
class RetrievalOutcome:
    """Result of one supervised retrieval.

    ``result`` is the existing wire result, or ``None`` when the worker outcome
    cannot be trusted. ``usage`` is the internal consumption report, or
    ``None`` when it is unknown (never substitute zero). ``reason`` is
    ``None`` | ``"deadline"`` | ``"worker_failure"``.
    """

    result: dict[str, Any] | None
    usage: dict[str, Any] | None
    reason: str | None
    exit_code: int | None


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
    must enforce mid-flight; ``None`` means the single-operation path.
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
    request_path = workdir / _REQUEST_FILENAME
    result_path = workdir / _RESULT_FILENAME

    envelope = {
        "protocol": WORKER_PROTOCOL,
        "operation": operation,
        "payload": payload,
        "include_source_payload": bool(include_source_payload),
        "provider_settings": {
            "motley_fool_enabled": bool(provider_settings.motley_fool_enabled),
            "fmp_enabled": bool(provider_settings.fmp_enabled),
        },
        "budget": {
            "seconds_remaining": float(remaining_seconds),
            "requests_left": requests_left,
            "response_bytes_left": response_bytes_left,
        },
        "launcher": (
            {"factory": launcher, "spec": launcher_spec}
            if launcher is not None
            else None
        ),
    }
    request_path.write_bytes(
        json.dumps(envelope, ensure_ascii=False).encode("utf-8")
    )

    env = os.environ.copy()
    if fmp_api_key:
        env["FMP_API_KEY"] = fmp_api_key
    process = subprocess.Popen(
        [sys.executable, "-u", str(WORKER_PATH), str(request_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )

    if not _wait_until(process, deadline):
        if not _stop_process(process):
            _report_cleanup_failure(
                f"worker exit unconfirmed within grace; keeping {workdir}"
            )
            return RetrievalOutcome(None, None, "deadline", None)
        _remove_workdir(workdir)
        return RetrievalOutcome(None, None, "deadline", process.returncode)

    exit_code = process.returncode
    if exit_code != 0:
        _remove_workdir(workdir)
        return RetrievalOutcome(None, None, "worker_failure", exit_code)

    result, usage, reason = _load_result_file(
        result_path,
        expected_request_id=_expected_request_id(operation, payload),
        expected_schemas=_expected_schemas(operation, include_source_payload),
        deadline=deadline,
        max_bytes=MAX_RESULT_BYTES,
    )
    _remove_workdir(workdir)
    return RetrievalOutcome(result, usage, reason, exit_code)


def _expected_request_id(operation: str, payload: Any) -> str | None:
    if operation == "list":
        return payload.get("request_id") if isinstance(payload, dict) else None
    if isinstance(payload, dict) and isinstance(payload.get("request_id"), str):
        return payload["request_id"]
    return None


def _expected_schemas(operation: str, include_source_payload: bool) -> set[str] | None:
    if operation == "list":
        return None  # internal listing helper, not a wire schema
    if operation == "discover":
        return {DISCOVERY_RESULT_SCHEMA}
    if include_source_payload:
        return {RESULT_SCHEMA_WITH_SOURCE_PAYLOAD}
    return {RESULT_SCHEMA}


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


def _parse_usage(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    requests_used = value.get("requests_used")
    response_bytes_used = value.get("response_bytes_used")
    exhausted = value.get("exhausted")
    if type(requests_used) is not int or requests_used < 0:
        return None
    if type(response_bytes_used) is not int or response_bytes_used < 0:
        return None
    if exhausted is not None and not isinstance(exhausted, str):
        return None
    return {
        "requests_used": requests_used,
        "response_bytes_used": response_bytes_used,
        "exhausted": exhausted,
    }


def _load_result_file(
    path: Path,
    *,
    expected_request_id: str | None,
    expected_schemas: set[str] | None,
    deadline: float,
    max_bytes: int,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
    """Bounded read + identity/shape validation + post-read deadline check."""
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
    if not isinstance(envelope, dict) or envelope.get("protocol") != WORKER_PROTOCOL:
        return None, None, "worker_failure"
    usage = _parse_usage(envelope.get("usage"))
    if usage is None:
        return None, None, "worker_failure"
    result = envelope.get("result")
    if not isinstance(result, dict):
        return None, usage, "worker_failure"
    if expected_request_id is not None and result.get("request_id") != expected_request_id:
        return None, usage, "worker_failure"
    if expected_schemas is not None and result.get("schema_version") not in expected_schemas:
        return None, usage, "worker_failure"
    if time.monotonic() > deadline:
        return None, usage, "deadline"
    return result, usage, None

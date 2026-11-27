"""Internal retrieval worker: existing HTTP API under the supervisor's budget.

Launched only by ``retrieval_runtime.run_retrieval`` with one request-file
path argument. It performs no translation, writes no official originals, spawns
no further processes, and communicates solely through one bounded atomic
result file in its own temp directory. Credentials reach it only through the
process environment (``FMP_API_KEY``) and are never serialized.

The request/result envelope definitions come from ``retrieval_protocol`` — this
module must not import the supervisor. The provider implementation is imported
here, in the worker process, and never becomes a parent-side dependency.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from retrieval_budget import BatchBudget, BudgetSession, UsageCounter
from retrieval_diagnostics import ExceptionFact, capture_provider_failure, exception_trace
from retrieval_lifecycle import RetrievalTrace, WorkerLifetime
from retrieval_process import enter_windows_job
from retrieval_protocol import (
    RESULT_FILENAME,
    WORKER_ERROR_FILENAME,
    WORKER_ERROR_SCHEMA,
    encode_result_envelope,
    read_request_envelope,
    result_within_limit,
)
from transcript_provider_settings import ProviderSettings


def _provider_settings(envelope: dict[str, Any]) -> ProviderSettings | None:
    raw = envelope.get("provider_settings")
    if not isinstance(raw, dict):
        return None
    if set(raw) != {"motley_fool_enabled", "fmp_enabled"}:
        return None
    if (
        type(raw["motley_fool_enabled"]) is not bool
        or type(raw["fmp_enabled"]) is not bool
    ):
        return None
    return ProviderSettings(
        motley_fool_enabled=raw["motley_fool_enabled"],
        fmp_enabled=raw["fmp_enabled"],
    )


def _launcher_factory(envelope: dict[str, Any]) -> Callable[[dict, dict], Any] | None:
    """Private test seam: an import path supplied in the internal envelope."""
    launcher = envelope.get("launcher")
    if launcher is None:
        return None
    if not isinstance(launcher, dict):
        raise ValueError("malformed launcher")
    reference = launcher.get("factory")
    if not isinstance(reference, str) or ":" not in reference:
        raise ValueError("malformed launcher reference")
    module_name, _, attribute = reference.partition(":")
    module = importlib.import_module(module_name)
    factory = getattr(module, attribute)
    if not callable(factory):
        raise ValueError("launcher factory is not callable")
    return factory


def _dispatch(
    envelope: dict[str, Any],
    session_factory: Callable[[], Any],
    settings: ProviderSettings,
    *,
    provider_api: ModuleType | None = None,
) -> dict[str, Any]:
    if provider_api is None:
        provider_api = importlib.import_module("transcript_api")
    operation = envelope.get("operation")
    payload = envelope.get("payload")
    include_source_payload = bool(envelope.get("include_source_payload"))
    if operation == "fetch":
        return provider_api.fetch_transcript(
            payload,
            session_factory=session_factory,
            fmp_api_key=os.environ.get("FMP_API_KEY"),
            include_source_payload=include_source_payload,
            provider_settings=settings,
        )
    if operation == "discover":
        return provider_api.discover_transcripts(
            payload,
            session_factory=session_factory,
            provider_settings=settings,
        )
    if operation == "fetch-candidate":
        return provider_api.fetch_transcript_candidate(
            payload,
            session_factory=session_factory,
            include_source_payload=include_source_payload,
            provider_settings=settings,
        )
    if operation == "list":
        if not isinstance(payload, dict):
            raise ValueError("list payload must be an object")
        return provider_api.list_transcript_candidates(
            **payload,
            session_factory=session_factory,
            provider_settings=settings,
        )
    raise ValueError(f"unknown operation: {operation!r}")


def _write_error_diagnostic(
    request_path: Path, phase: str, trace: list[ExceptionFact] | None,
    *, failure_code: str | None = None,
) -> None:
    """Save only the stage and exception facts, including caught provider errors."""
    trace = trace or []
    payload = {
        "schema_version": WORKER_ERROR_SCHEMA,
        "phase": phase,
        "exception_types": [fact["type"] for fact in trace],
        "exception_trace": trace,
    }
    if failure_code is not None:
        payload["failure_code"] = failure_code
    try:
        target = request_path.parent / WORKER_ERROR_FILENAME
        tmp_path = request_path.parent / f"{WORKER_ERROR_FILENAME}.tmp"
        tmp_path.write_bytes(json.dumps(payload).encode("utf-8"))
        os.replace(tmp_path, target)
    except OSError as exc:
        # The supervisor consumes only this known exception category from its
        # private pipe. No path, message, provider data or credential follows.
        try:
            print(f"ET worker diagnostic write failed: {type(exc).__name__}", file=sys.stderr)
        except (OSError, ValueError):
            pass


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        return 2
    request_path = Path(argv[0])
    trace = RetrievalTrace.from_environment()
    phase = ["process_scope"]

    def failing(code: int, reason: str) -> int:
        _write_error_diagnostic(request_path, phase[0], None, failure_code=reason)
        return code

    try:
        # The supervised worker must join its job before loading provider code
        # or creating any descendants. Direct module calls have no supervisor.
        if trace is not None:
            enter_windows_job()
        lifetime = WorkerLifetime(request_path, trace) if trace is not None else None
        phase[0] = "envelope"
        envelope = read_request_envelope(request_path)
        if envelope is None:
            return failing(1, "invalid_request_envelope")
        settings = _provider_settings(envelope)
        if settings is None:
            return failing(1, "invalid_provider_settings")

        phase[0] = "budget"
        budget_cfg = envelope.get("budget")
        if not isinstance(budget_cfg, dict):
            return failing(1, "invalid_budget")
        seconds_remaining = budget_cfg.get("seconds_remaining")
        requests_left = budget_cfg.get("requests_left")
        response_bytes_left = budget_cfg.get("response_bytes_left")
        if not isinstance(seconds_remaining, (int, float)) or seconds_remaining < 0:
            return failing(1, "invalid_budget_seconds")

        if any(
            value is not None and (type(value) is not int or value < 0)
            for value in (requests_left, response_bytes_left)
        ):
            return failing(1, "invalid_budget_quota")
        if requests_left is not None or response_bytes_left is not None:
            budget: BatchBudget | UsageCounter = BatchBudget(
                requests_left, float(seconds_remaining), response_bytes_left, 0
            )
        else:
            budget = UsageCounter()

        factory = _launcher_factory(envelope)
        context = {"workdir": str(request_path.parent)}
        launcher_spec = (envelope.get("launcher") or {}).get("spec")

        # Dependency loading can fail before a session starts. Keep this phase
        # inside the same structural diagnostics path as other worker faults.
        phase[0] = "bootstrap"
        provider_api = importlib.import_module("transcript_api")
        from requests import Session

        def session_factory() -> Any:
            # The failing stage must survive the exception: the phase word
            # stays "launcher" when the factory itself raises, and only a
            # successfully built session moves the stage to "dispatch".
            phase[0] = "launcher"
            inner = (
                factory(launcher_spec, context)
                if factory is not None
                else Session()
            )
            phase[0] = "dispatch"
            return BudgetSession(inner, budget)

        phase[0] = "dispatch"
        if lifetime is not None:
            lifetime.activity = "possibly_dispatched"
            lifetime.trace.emit("provider_phase_entered", provider_activity=lifetime.activity, usage=None)
        with capture_provider_failure() as provider_trace:
            result = _dispatch(envelope, session_factory, settings, provider_api=provider_api)
        if provider_trace:
            _write_error_diagnostic(request_path, phase[0], provider_trace)
        phase[0] = "encode"
        usage = budget.usage_snapshot()
        if lifetime is not None:
            lifetime.usage = usage
            lifetime.trace.emit("provider_returned", provider_activity=lifetime.activity, usage=usage)
        data = encode_result_envelope(usage=usage, result=result)
        # Same derived allowance as the supervisor: this request's own quotas plus
        # the computable encoding overhead, never one module-level fixed cap.
        if not result_within_limit(data, envelope.get("payload"), response_bytes_left):
            return failing(2, "result_transfer_limit")
        phase[0] = "write"
        result_path = request_path.parent / RESULT_FILENAME
        tmp_path = request_path.parent / f"{RESULT_FILENAME}.tmp"
        tmp_path.write_bytes(data)
        os.replace(tmp_path, result_path)
        return 0
    except BaseException as exc:
        _write_error_diagnostic(request_path, phase[0], exception_trace(exc))
        raise


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

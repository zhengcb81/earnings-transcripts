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
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

import requests

from retrieval_budget import BatchBudget, UsageCounter, _BudgetSession
from retrieval_protocol import (
    RESULT_FILENAME,
    WORKER_ERROR_FILENAME,
    WORKER_ERROR_SCHEMA,
    encode_result_envelope,
    read_request_envelope,
    result_within_limit,
)
from transcript_api import (
    ProviderSettings,
    discover_transcripts,
    fetch_transcript,
    fetch_transcript_candidate,
    list_transcript_candidates,
)


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
) -> dict[str, Any]:
    operation = envelope.get("operation")
    payload = envelope.get("payload")
    include_source_payload = bool(envelope.get("include_source_payload"))
    if operation == "fetch":
        return fetch_transcript(
            payload,
            session_factory=session_factory,
            fmp_api_key=os.environ.get("FMP_API_KEY"),
            include_source_payload=include_source_payload,
            provider_settings=settings,
        )
    if operation == "discover":
        return discover_transcripts(
            payload,
            session_factory=session_factory,
            provider_settings=settings,
        )
    if operation == "fetch-candidate":
        return fetch_transcript_candidate(
            payload,
            session_factory=session_factory,
            include_source_payload=include_source_payload,
            provider_settings=settings,
        )
    if operation == "list":
        if not isinstance(payload, dict):
            raise ValueError("list payload must be an object")
        return list_transcript_candidates(
            **payload,
            session_factory=session_factory,
            provider_settings=settings,
        )
    raise ValueError(f"unknown operation: {operation!r}")


def _exception_chain(exc: BaseException) -> list[str]:
    """The sanitized exception type chain: type names only, never messages."""
    names: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(names) < 8:
        seen.add(id(current))
        names.append(type(current).__name__)
        current = current.__cause__ or current.__context__
    return names


def _write_error_diagnostic(request_path: Path, phase: str, exceptions: list[str] | None) -> None:
    """Leave one sanitized diagnostic in the workdir before a failing exit.

    The parent owns the details and merges this once after the wait. It holds
    only the stage word and the exception type chain: no message text, no
    path, no payload.
    """
    payload = {
        "schema_version": WORKER_ERROR_SCHEMA,
        "phase": phase,
        "exception_types": exceptions or [],
    }
    try:
        target = request_path.parent / WORKER_ERROR_FILENAME
        tmp_path = request_path.parent / f"{WORKER_ERROR_FILENAME}.tmp"
        tmp_path.write_bytes(json.dumps(payload).encode("utf-8"))
        os.replace(tmp_path, target)
    except OSError:
        pass


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        return 2
    request_path = Path(argv[0])
    phase = ["envelope"]

    def failing(code: int, exceptions: list[str] | None = None) -> int:
        _write_error_diagnostic(request_path, phase[0], exceptions)
        return code

    try:
        envelope = read_request_envelope(request_path)
        if envelope is None:
            return failing(1)
        settings = _provider_settings(envelope)
        if settings is None:
            return failing(1)

        phase[0] = "budget"
        budget_cfg = envelope.get("budget")
        if not isinstance(budget_cfg, dict):
            return failing(1)
        seconds_remaining = budget_cfg.get("seconds_remaining")
        requests_left = budget_cfg.get("requests_left")
        response_bytes_left = budget_cfg.get("response_bytes_left")
        if not isinstance(seconds_remaining, (int, float)) or seconds_remaining < 0:
            return failing(1)

        if any(
            value is not None and (type(value) is not int or value < 0)
            for value in (requests_left, response_bytes_left)
        ):
            return failing(1)
        if requests_left is not None or response_bytes_left is not None:
            budget: BatchBudget | UsageCounter = BatchBudget(
                requests_left, float(seconds_remaining), response_bytes_left, 0
            )
        else:
            budget = UsageCounter()

        factory = _launcher_factory(envelope)
        context = {"workdir": str(request_path.parent)}
        launcher_spec = (envelope.get("launcher") or {}).get("spec")

        def session_factory() -> Any:
            # The failing stage must survive the exception: the phase word
            # stays "launcher" when the factory itself raises, and only a
            # successfully built session moves the stage to "dispatch".
            phase[0] = "launcher"
            inner = (
                factory(launcher_spec, context)
                if factory is not None
                else requests.Session()
            )
            phase[0] = "dispatch"
            return _BudgetSession(inner, budget)

        phase[0] = "dispatch"
        result = _dispatch(envelope, session_factory, settings)
        phase[0] = "encode"
        data = encode_result_envelope(usage=budget.usage_snapshot(), result=result)
        # Same derived allowance as the supervisor: this request's own quotas plus
        # the computable encoding overhead, never one module-level fixed cap.
        if not result_within_limit(data, envelope.get("payload"), response_bytes_left):
            return failing(2)
        phase[0] = "write"
        result_path = request_path.parent / RESULT_FILENAME
        tmp_path = request_path.parent / f"{RESULT_FILENAME}.tmp"
        tmp_path.write_bytes(data)
        os.replace(tmp_path, result_path)
        return 0
    except BaseException as exc:
        _write_error_diagnostic(request_path, phase[0], _exception_chain(exc))
        raise


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

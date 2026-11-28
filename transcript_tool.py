"""JSON-lines/stdin command boundary for the transcript companion API.

The production path (no private session injection) executes every retrieval in
an internal worker subprocess supervised by ``retrieval_runtime``: the
request's ``timeout_seconds`` becomes a hard wall-clock deadline enforced by
process termination, not by cooperative blocking checks alone. The private
``_session_factory`` injection keeps the pure-Python cooperative path for
offline tests; its blocking behavior is not the hard-deadline guarantee.

Every terminal path publishes the same native final usage receipt when
``--report-usage`` is given, including the paths that reject a request
before any provider call. The optional feedback projection
(``transcript_feedback``) only reads what this module already knows; it never
changes stdout, stderr, the exit code or the retry behaviour.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

import retrieval_runtime
import transcript_feedback
from retrieval_lifecycle import RetrievalTrace
from runtime_logging import EntryDiagnostics
from runtime_scope import public_request, safe_public_word, step_id
from transcript_contracts import (
    ADAPTER_VERSION,
    DISCOVERY_RESULT_SCHEMA,
    MAX_TIMEOUT_SECONDS,
    RESULT_SCHEMA,
    RESULT_SCHEMA_WITH_SOURCE_PAYLOAD,
)
from transcript_provider_settings import (
    DEFAULT_PROVIDER_SETTINGS,
    ProviderSettings,
    provider_operation_capability,
)

#: The version of the product that a run observed. The observation names this
#: version; the projection that wrote the observation names its own version in
#: the detail record.
PRODUCT_VERSION = f"earnings-transcripts-transcript-tool/{ADAPTER_VERSION}"


def _wire_failure(
    schema_version: str,
    request_id: Any,
    status: str,
    error_code: str,
    provider: str,
) -> dict[str, Any]:
    return {
        "schema_version": schema_version,
        "request_id": request_id,
        "status": status,
        "error_code": error_code,
        "provider": provider,
    }


def _operation_deadline(request: Any) -> float:
    """Remaining float seconds for one formal retrieval operation."""
    if isinstance(request, dict):
        value = request.get("timeout_seconds")
        if type(value) is int and value >= 1:
            return float(value)
    return float(MAX_TIMEOUT_SECONDS)


def _supervised_result(
    request: Any,
    args: argparse.Namespace,
    *,
    result_schema: str,
    provider_settings: ProviderSettings,
    launcher: str | None,
    launcher_spec: dict[str, Any] | None,
    temp_root: Path | None,
    fmp_api_key: str | None = None,
    diagnostic_identity: tuple[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    """Run one retrieval through the supervisor and map hard failures.

    The third value is the supervisor's sanitized child diagnostic; it joins
    the detail record only and never the wire result or the receipt.
    """
    outcome = retrieval_runtime.run_retrieval(
        args.operation,
        request,
        remaining_seconds=_operation_deadline(request),
        requests_left=None,
        response_bytes_left=(
            request.get("max_response_bytes")
            if isinstance(request, dict)
            and type(request.get("max_response_bytes")) is int
            and request["max_response_bytes"] > 0
            else None
        ),
        include_source_payload=args.include_source_payload,
        provider_settings=provider_settings,
        fmp_api_key=fmp_api_key,
        launcher=launcher,
        launcher_spec=launcher_spec,
        # Machine callers decode stderr as the usage receipt. F4 and the
        # ET-owned local log still receive the supervised diagnostics.
        log_worker_diagnostics=not args.report_usage,
        temp_root=temp_root,
        diagnostic_identity=diagnostic_identity,
    )
    # Keep acquisition diagnostics out of the immutable content schema.
    receipt = {
        "schema_version": "earnings-retrieval-usage/1",
        "request_id": request.get("request_id") if isinstance(request, dict) else None,
        "usage_complete": outcome.usage is not None and (
            (outcome.result is not None and outcome.reason is None)
            or outcome.reason == "worker_not_started"
        ),
        "usage": outcome.usage,
    }
    if outcome.result is not None and outcome.reason is None:
        return outcome.result, receipt, getattr(outcome, "diagnostics", None)
    request_id = request.get("request_id") if isinstance(request, dict) else None
    provider = "motley_fool"
    if isinstance(request, dict) and isinstance(request.get("provider"), str):
        if request["provider"]:
            provider = request["provider"]
    if outcome.reason == "deadline":
        status, error_code = "deadline_exceeded", "provider_deadline"
    else:
        status, error_code = "provider_error", "retrieval_worker_failure"
    return (
        _wire_failure(result_schema, request_id, status, error_code, provider),
        receipt,
        getattr(outcome, "diagnostics", None),
    )


def _configured_fmp_key() -> tuple[str | None, str | None]:
    """Read one explicit source; never scan files or put key/path in a result."""
    configured = os.environ.get("FMP_API_KEY_FILE")
    if configured is None:
        value = os.environ.get("FMP_API_KEY")
        return value if isinstance(value, str) and value.strip() else None, None
    if not configured.strip():
        return None, "provider_credentials_file_unavailable"
    try:
        with Path(configured).open("rb") as stream:
            raw = stream.read(4097)
    except OSError:
        return None, "provider_credentials_file_unavailable"
    if len(raw) > 4096:
        return None, "provider_credentials_file_invalid"
    try:
        value = raw.decode("utf-8-sig").strip()
    except UnicodeError:
        return None, "provider_credentials_file_invalid"
    if not value:
        return None, "provider_credentials_file_empty"
    if any(not 33 <= ord(char) <= 126 for char in value):
        return None, "provider_credentials_file_invalid"
    return value, None


def _zero_usage_receipt(request: Any) -> dict[str, Any]:
    """The native usage receipt of a run that made no provider request."""
    return {
        "schema_version": "earnings-retrieval-usage/1",
        "request_id": request.get("request_id") if isinstance(request, dict) else None,
        "usage_complete": True,
        "usage": {"requests_used": 0, "response_bytes_used": 0, "exhausted": None},
    }


def _publish_usage_receipt(
    receipt: dict[str, Any], report_usage: bool
) -> dict[str, Any] | None:
    """Write the native final usage receipt to stderr; return it when published."""
    if not report_usage:
        return None
    sys.stderr.write(json.dumps(receipt, separators=(",", ":")) + "\n")
    return receipt


@transcript_feedback.observed_entry("transcript_tool")
def main(
    argv: list[str] | None = None,
    *,
    runtime: EntryDiagnostics,
    _session_factory: Callable[[], Any] | None = None,
    _provider_settings: ProviderSettings = DEFAULT_PROVIDER_SETTINGS,
    _retrieval_launcher: str | None = None,
    _retrieval_spec: dict[str, Any] | None = None,
    _retrieval_temp_root: Path | None = None,
    _feedback_recorder: Any = None,
    _feedback_evidence_ref: str | None = None,
    _cooperative_fetch: Callable[..., dict[str, Any]] | None = None,
    _usage_publisher: Callable[[dict[str, Any], bool], dict[str, Any] | None] | None = None,
) -> int:
    """Run the JSON boundary with explicit seams for isolated fixtures."""
    def initialize_recorder():
        recorder = (
            _feedback_recorder
            if _feedback_recorder is not None
            else transcript_feedback.recorder_from_env(identity=runtime.identity)
        )
        if recorder is not None:
            runtime.identity = (recorder.run_id, recorder.attempt_id)
            recorder.version = PRODUCT_VERSION
            transcript_feedback.set_active_recorder(recorder)
        return recorder

    parser = transcript_feedback.RefusalCapturingParser(
        description="Fetch one exact-period untranslated earnings transcript."
    )
    entry = parser.add_mutually_exclusive_group(required=True)
    entry.add_argument("--request-stdin", action="store_true")
    entry.add_argument(
        "--capabilities",
        action="store_true",
        help="Local operation/pricing metadata; no provider requests.",
    )
    entry.add_argument(
        "--flush-observations", action="store_true",
        help="Republish saved observations for the configured run/attempt; no provider requests.",
    )
    parser.add_argument(
        "--report-usage",
        action="store_true",
        help="Emit the final supervisor usage receipt on stderr; content schema is unchanged.",
    )
    parser.add_argument(
        "--operation",
        choices=("fetch", "discover", "fetch-candidate"),
        default="fetch",
        help="Use discovery-only, candidate-bound fetch, or the legacy combined fetch.",
    )
    parser.add_argument(
        "--allow-download",
        action="store_true",
        help="Legacy compatibility flag; the request's download_authorized field controls network intent.",
    )
    parser.add_argument(
        "--include-source-payload",
        action="store_true",
        help="Opt in to a bounded base64 original provider response; never writes files.",
    )
    try:
        args = parser.parse_args(argv)
    except transcript_feedback.InvocationRefusal as refusal:
        recorder = initialize_recorder()
        code = transcript_feedback.refuse_invocation(
            parser,
            refusal,
            recorder,
            entry="transcript_tool",
            stage="argument-parsing",
            refusal_code="invalid_cli_arguments",
            location="transcript_tool.py#parse_args",
            runtime=runtime,
        )
        identity = (recorder.run_id, recorder.attempt_id) if recorder is not None else runtime.identity
        RetrievalTrace.create("transcript-tool", None, identity).emit(
            "invocation_refused", exit_code=code, reason="invalid_cli_arguments",
            usage={"requests_used": 0, "response_bytes_used": 0, "exhausted": None},
        )
        transcript_feedback.finalize_delivery(recorder)
        return code
    if args.capabilities:
        result = {
            "schema_version": "earnings-provider-capabilities/1",
            "providers": {
                provider: {
                    operation: provider_operation_capability(
                        provider, operation, settings=_provider_settings
                    )
                    for operation in ("fetch", "discover", "fetch-candidate")
                }
                for provider in ("fmp", "motley_fool")
            },
        }
        sys.stdout.write(
            json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n"
        )
        return 0
    recorder = initialize_recorder()
    if args.flush_observations:
        transcript_feedback.finalize_delivery(recorder)
        return 0
    identity = (recorder.run_id, recorder.attempt_id) if recorder is not None else runtime.identity
    invocation = RetrievalTrace.create("transcript-tool", None, identity)
    invocation.emit("invocation_started", requested_operation=args.operation, usage=None)
    publish_usage = _usage_publisher if _usage_publisher is not None else _publish_usage_receipt
    result_schema = (
        DISCOVERY_RESULT_SCHEMA
        if args.operation == "discover"
        else RESULT_SCHEMA_WITH_SOURCE_PAYLOAD
        if args.include_source_payload
        else RESULT_SCHEMA
    )
    request: Any = None
    native_receipt: dict[str, Any] | None = None
    published_receipt: dict[str, Any] | None = None
    worker_diagnostics: dict[str, Any] | None = None
    branch = "invalid-request"
    started_at: str | None = None
    runtime.begin("input-decode")
    try:
        raw = sys.stdin.read(128 * 1024 + 1)
        if len(raw.encode("utf-8")) > 128 * 1024:
            result = _wire_failure(
                result_schema,
                None,
                "invalid_request",
                "request_too_large",
                "motley_fool",
            )
            native_receipt = _zero_usage_receipt(None)
            published_receipt = publish_usage(native_receipt, args.report_usage)
        else:
            request = json.loads(raw)
            runtime.stage = "request-validation"
            invocation.emit("input_parsed", request=public_request(request),
                            retrieval_step_id=step_id(request, args.operation))
            started_at = transcript_feedback.begin_step(
                recorder,
                request=request,
                operation=args.operation,
                evidence_ref=_feedback_evidence_ref,
            )
            started_at = started_at.get("started_at") if started_at else None
            # Provider implementation is imported only on the paths that need
            # it; validation/gating is pure and stays in the parent.
            preflight = None
            if args.operation == "fetch":
                from transcript_preflight import preflight_transcript_fetch

                preflight = preflight_transcript_fetch(
                    request,
                    result_schema=result_schema,
                    settings=_provider_settings,
                )
            runtime.stage = "provider-credentials"
            fmp_api_key, credential_error = (None, None)
            if preflight is None and args.operation == "fetch" and isinstance(request, dict) and request.get("provider") == "fmp":
                fmp_api_key, credential_error = _configured_fmp_key()
            if preflight is not None:
                result = preflight
                branch = "preflight"
                native_receipt = _zero_usage_receipt(request)
                published_receipt = publish_usage(native_receipt, args.report_usage)
            elif credential_error is not None:
                result = _wire_failure(result_schema, request.get("request_id"), "unavailable", credential_error, "fmp")
                branch = "credential"
                native_receipt = _zero_usage_receipt(request)
                published_receipt = publish_usage(native_receipt, args.report_usage)
            elif args.operation == "discover" and args.include_source_payload:
                provider = "motley_fool"
                request_id = None
                if isinstance(request, dict):
                    request_id = request.get("request_id")
                    raw_provider = request.get("provider")
                    if isinstance(raw_provider, str) and raw_provider:
                        provider = raw_provider
                result = _wire_failure(
                    DISCOVERY_RESULT_SCHEMA,
                    request_id,
                    "invalid_request",
                    "source_payload_not_valid_for_discovery",
                    provider,
                )
                branch = "discovery-source-payload"
                native_receipt = _zero_usage_receipt(request)
                published_receipt = publish_usage(native_receipt, args.report_usage)
            elif _session_factory is not None:
                # Cooperative path: injected custom session (offline tests).
                from transcript_api import (
                    discover_transcripts,
                    fetch_transcript,
                    fetch_transcript_candidate,
                )

                provider_options: dict[str, Any] = {
                    "provider_settings": _provider_settings,
                    "session_factory": _session_factory,
                }
                runtime.stage = "provider-dispatch"
                branch = "provider"
                if args.operation == "discover":
                    result = discover_transcripts(request, **provider_options)
                elif args.operation == "fetch-candidate":
                    result = fetch_transcript_candidate(
                        request,
                        include_source_payload=args.include_source_payload,
                        **provider_options,
                    )
                else:
                    cooperative_fetch = _cooperative_fetch if _cooperative_fetch is not None else fetch_transcript
                    result = cooperative_fetch(
                        request,
                        fmp_api_key=fmp_api_key,
                        include_source_payload=args.include_source_payload,
                        **provider_options,
                    )
            else:
                # Production path: one internal worker under a hard deadline.
                runtime.stage = "provider-dispatch"
                result, native_receipt, worker_diagnostics = _supervised_result(
                    request,
                    args,
                    result_schema=result_schema,
                    provider_settings=_provider_settings,
                    launcher=_retrieval_launcher,
                    launcher_spec=_retrieval_spec,
                    temp_root=_retrieval_temp_root,
                    fmp_api_key=fmp_api_key,
                    diagnostic_identity=identity,
                )
                branch = (
                    "worker-startup" if (worker_diagnostics or {}).get("provider_activity") == "not_started"
                    else "provider"
                )
                published_receipt = publish_usage(native_receipt, args.report_usage)
    except (json.JSONDecodeError, UnicodeError) as exc:
        transcript_feedback.report_entry_failure("transcript_tool", "input-decode", exc, expected=True, publish=False, runtime=runtime)
        result = _wire_failure(
            result_schema, None, "invalid_request", "invalid_json", "motley_fool"
        )
        native_receipt = _zero_usage_receipt(None)
        published_receipt = publish_usage(native_receipt, args.report_usage)
    invocation.emit(
        "invocation_finished", exit_code=0, branch=branch,
        result_status=safe_public_word(result.get("status"), fallback="status"),
        reason=safe_public_word(result.get("error_code"), fallback="reason"),
        usage=native_receipt.get("usage") if native_receipt else None,
        usage_complete=bool(native_receipt and native_receipt.get("usage_complete")),
    )
    transcript_feedback.emit_observations(
        recorder,
        request=request,
        result=result,
        operation=args.operation,
        native_receipt=native_receipt,
        published_receipt=published_receipt,
        receipt_required=bool(args.report_usage),
        evidence_ref=_feedback_evidence_ref,
        branch=branch,
        started_at=started_at,
        finished_at=transcript_feedback.now(),
        worker_diagnostics=worker_diagnostics,
    )
    sys.stdout.write(
        json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n"
    )
    transcript_feedback.finalize_delivery(recorder)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""JSON-lines/stdin command boundary for the transcript companion API.

The production path (no private session injection) executes every retrieval in
an internal worker subprocess supervised by ``retrieval_runtime``: the
request's ``timeout_seconds`` becomes a hard wall-clock deadline enforced by
process termination, not by cooperative blocking checks alone. The private
``_session_factory`` injection keeps the pure-Python cooperative path for
offline tests; its blocking behavior is not the hard-deadline guarantee.
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
from transcript_api import (
    DEFAULT_PROVIDER_SETTINGS,
    DISCOVERY_RESULT_SCHEMA,
    MAX_TIMEOUT_SECONDS,
    ProviderSettings,
    discover_transcripts,
    fetch_transcript,
    fetch_transcript_candidate,
    provider_operation_capability,
    preflight_transcript_fetch,
)


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
        if type(value) is int and 1 <= value <= MAX_TIMEOUT_SECONDS:
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
) -> dict[str, Any]:
    """Run one retrieval through the supervisor and map hard failures."""
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
        temp_root=temp_root,
    )
    # Keep acquisition diagnostics out of the immutable content schema.
    receipt = {
        "schema_version": "earnings-retrieval-usage/1",
        "request_id": request.get("request_id") if isinstance(request, dict) else None,
        "usage_complete": outcome.result is not None
        and outcome.reason is None
        and outcome.usage is not None,
        "usage": outcome.usage,
    }
    if args.report_usage:
        sys.stderr.write(json.dumps(receipt, separators=(",", ":")) + "\n")
    if outcome.result is not None:
        return outcome.result
    request_id = request.get("request_id") if isinstance(request, dict) else None
    provider = "motley_fool"
    if isinstance(request, dict) and isinstance(request.get("provider"), str):
        if request["provider"]:
            provider = request["provider"]
    if outcome.reason == "deadline":
        status, error_code = "deadline_exceeded", "provider_deadline"
    else:
        status, error_code = "provider_error", "retrieval_worker_failure"
    return _wire_failure(result_schema, request_id, status, error_code, provider)


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


def _emit_preflight_usage(request: Any, args: argparse.Namespace) -> None:
    if args.report_usage:
        receipt = {"schema_version": "earnings-retrieval-usage/1",
                   "request_id": request.get("request_id") if isinstance(request, dict) else None,
                   "usage_complete": True,
                   "usage": {"requests_used": 0, "response_bytes_used": 0, "exhausted": None}}
        sys.stderr.write(json.dumps(receipt, separators=(",", ":")) + "\n")


def main(
    argv: list[str] | None = None,
    *,
    _session_factory: Callable[[], Any] | None = None,
    _provider_settings: ProviderSettings = DEFAULT_PROVIDER_SETTINGS,
    _retrieval_launcher: str | None = None,
    _retrieval_spec: dict[str, Any] | None = None,
    _retrieval_temp_root: Path | None = None,
) -> int:
    """Run the JSON boundary; private injection points are for offline tests only."""
    parser = argparse.ArgumentParser(
        description="Fetch one exact-period untranslated earnings transcript."
    )
    entry = parser.add_mutually_exclusive_group(required=True)
    entry.add_argument("--request-stdin", action="store_true")
    entry.add_argument(
        "--capabilities",
        action="store_true",
        help="Local operation/pricing metadata; no provider requests.",
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
    args = parser.parse_args(argv)
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
    result_schema = (
        DISCOVERY_RESULT_SCHEMA
        if args.operation == "discover"
        else "earnings-transcript-result/2"
        if args.include_source_payload
        else "earnings-transcript-result/1"
    )
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
        else:
            request = json.loads(raw)
            preflight = preflight_transcript_fetch(request, result_schema=result_schema,
                                                  settings=_provider_settings) if args.operation == "fetch" else None
            fmp_api_key, credential_error = (None, None)
            if preflight is None and args.operation == "fetch" and isinstance(request, dict) and request.get("provider") == "fmp":
                fmp_api_key, credential_error = _configured_fmp_key()
            if preflight is not None:
                result = preflight
                _emit_preflight_usage(request, args)
            elif credential_error is not None:
                result = _wire_failure(result_schema, request.get("request_id"), "unavailable", credential_error, "fmp")
                _emit_preflight_usage(request, args)
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
            elif _session_factory is not None:
                # Cooperative path: injected custom session (offline tests).
                provider_options: dict[str, Any] = {
                    "provider_settings": _provider_settings,
                    "session_factory": _session_factory,
                }
                if args.operation == "discover":
                    result = discover_transcripts(request, **provider_options)
                elif args.operation == "fetch-candidate":
                    result = fetch_transcript_candidate(
                        request,
                        include_source_payload=args.include_source_payload,
                        **provider_options,
                    )
                else:
                    result = fetch_transcript(
                        request,
                        fmp_api_key=fmp_api_key,
                        include_source_payload=args.include_source_payload,
                        **provider_options,
                    )
            else:
                # Production path: one internal worker under a hard deadline.
                result = _supervised_result(
                    request,
                    args,
                    result_schema=result_schema,
                    provider_settings=_provider_settings,
                    launcher=_retrieval_launcher,
                    launcher_spec=_retrieval_spec,
                    temp_root=_retrieval_temp_root,
                    fmp_api_key=fmp_api_key,
                )
    except (json.JSONDecodeError, UnicodeError):
        result = _wire_failure(
            result_schema, None, "invalid_request", "invalid_json", "motley_fool"
        )
    sys.stdout.write(
        json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

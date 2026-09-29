"""JSON-lines/stdin command boundary for the transcript companion API."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from transcript_api import (
    DEFAULT_PROVIDER_SETTINGS,
    DISCOVERY_RESULT_SCHEMA,
    ProviderSettings,
    fetch_transcript,
    fetch_transcript_candidate,
    discover_transcripts,
)


def main(
    argv: list[str] | None = None,
    *,
    _session_factory: Callable[[], Any] | None = None,
    _provider_settings: ProviderSettings = DEFAULT_PROVIDER_SETTINGS,
) -> int:
    """Run the JSON boundary; private injection points are for offline tests only."""
    parser = argparse.ArgumentParser(
        description="Fetch one exact-period untranslated earnings transcript."
    )
    parser.add_argument("--request-stdin", action="store_true", required=True)
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
    result_schema = (
        DISCOVERY_RESULT_SCHEMA
        if args.operation == "discover"
        else
        "earnings-transcript-result/2"
        if args.include_source_payload
        else "earnings-transcript-result/1"
    )
    try:
        raw = sys.stdin.read(128 * 1024 + 1)
        if len(raw.encode("utf-8")) > 128 * 1024:
            result = {
                "schema_version": result_schema,
                "request_id": None,
                "status": "invalid_request",
                "error_code": "request_too_large",
                "provider": "motley_fool",
            }
        else:
            request = json.loads(raw)
            provider_options: dict[str, Any] = {
                "provider_settings": _provider_settings,
            }
            if _session_factory is not None:
                provider_options["session_factory"] = _session_factory
            if args.operation == "discover":
                if args.include_source_payload:
                    result = {
                        "schema_version": DISCOVERY_RESULT_SCHEMA,
                        "request_id": request.get("request_id") if isinstance(request, dict) else None,
                        "status": "invalid_request",
                        "error_code": "source_payload_not_valid_for_discovery",
                        "provider": request.get("provider", "motley_fool") if isinstance(request, dict) else "motley_fool",
                    }
                else:
                    result = discover_transcripts(request, **provider_options)
            elif args.operation == "fetch-candidate":
                result = fetch_transcript_candidate(
                    request, include_source_payload=args.include_source_payload,
                    **provider_options,
                )
            else:
                result = fetch_transcript(
                    request,
                    fmp_api_key=os.environ.get("FMP_API_KEY"),
                    include_source_payload=args.include_source_payload,
                    **provider_options,
                )
    except (json.JSONDecodeError, UnicodeError):
        result = {
            "schema_version": result_schema,
            "request_id": None,
            "status": "invalid_request",
            "error_code": "invalid_json",
            "provider": "motley_fool",
        }
    sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

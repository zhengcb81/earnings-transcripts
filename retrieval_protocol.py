"""Internal supervisor ↔ worker protocol.

One retrieval operation runs in one worker subprocess. The parent writes one
bounded request file, the child writes one bounded result file, and both sides
read the same pure definitions here. This module is stdlib-only on purpose: it
must not import ``retrieval_runtime`` (the supervisor) or ``transcript_api``
(the provider implementation). That keeps the dependency direction
parent → protocol ← child, so a provider import in the child cannot leak into
the parent and a supervisor change cannot break the worker.

The transfer limit is derived from the same explicit request/budget the
supervisor already enforces, plus the computable JSON/base64 encoding overhead.
There is deliberately no module-level fixed result cap: such a cap rejects a
legal result whose size was authorized by the caller's own quota.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from transcript_contracts import (
    DISCOVERY_RESULT_SCHEMA,
    MAX_BODY_BYTES,
    MAX_FMP_RESPONSE_BYTES,
    RESULT_SCHEMA,
    RESULT_SCHEMA_WITH_SOURCE_PAYLOAD,
)

WORKER_PROTOCOL = "et-retrieval-worker/1"
REQUEST_FILENAME = "request.json"
RESULT_FILENAME = "result.json"
MAX_REQUEST_FILE_BYTES = 4 * 1024 * 1024

# JSON escaping expands one byte by at most 6x (\uXXXX for control characters);
# base64 expands by 4/3. Both content_utf8 and the extracted title are bounded
# by one capped page, and the opt-in source payload by the same raw response
# quota, so the same raw bytes bound every representation.
JSON_ESCAPE_EXPANSION = 6
# Fixed envelope fields (protocol, usage, schema names, request id) plus slack
# for provider-specific metadata. It is protocol overhead, not a data cap.
PROTOCOL_OVERHEAD_BYTES = 2 * 1024 * 1024


def result_transfer_limit(
    payload: Any, response_bytes_left: int | None
) -> int:
    """Bytes allowed for one result file, from this operation's own quotas.

    ``payload`` is the same request payload the provider call receives;
    ``response_bytes_left`` is the same batch/operation quota the worker
    enforces. A caller that explicitly raises the quota raises this limit with
    it — the encoding allowance is computed, never a separate constant.
    """
    if not isinstance(payload, dict):
        payload = {}
    declared = payload.get("max_response_bytes")
    limits = [
        value
        for value in (declared, response_bytes_left)
        if type(value) is int and value >= 0
    ]
    raw = min(limits) if limits else MAX_FMP_RESPONSE_BYTES
    body = payload.get("max_body_bytes", MAX_BODY_BYTES)
    body = min(body, raw) if type(body) is int and body >= 0 else raw
    return JSON_ESCAPE_EXPANSION * (2 * body + raw) + PROTOCOL_OVERHEAD_BYTES


def result_within_limit(
    data: bytes, payload: Any, response_bytes_left: int | None
) -> bool:
    """True when the encoded result fits the derived transfer allowance."""
    return len(data) <= result_transfer_limit(payload, response_bytes_left)


def build_request_envelope(
    operation: str,
    payload: dict[str, Any],
    *,
    include_source_payload: bool,
    provider_settings: Any,
    budget: dict[str, Any],
    launcher: str | None = None,
    launcher_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the internal request envelope written by the supervisor."""
    return {
        "protocol": WORKER_PROTOCOL,
        "operation": operation,
        "payload": payload,
        "include_source_payload": bool(include_source_payload),
        "provider_settings": {
            "motley_fool_enabled": bool(provider_settings.motley_fool_enabled),
            "fmp_enabled": bool(provider_settings.fmp_enabled),
        },
        "budget": {
            "seconds_remaining": float(budget["seconds_remaining"]),
            "requests_left": budget.get("requests_left"),
            "response_bytes_left": budget.get("response_bytes_left"),
        },
        "launcher": (
            {"factory": launcher, "spec": launcher_spec}
            if launcher is not None
            else None
        ),
    }


def encode_envelope(envelope: dict[str, Any]) -> bytes:
    return json.dumps(envelope, ensure_ascii=False).encode("utf-8")


def parse_request_envelope(raw: bytes) -> dict[str, Any] | None:
    """Validate one already-bounded request payload; ``None`` means unusable."""
    if len(raw) > MAX_REQUEST_FILE_BYTES:
        return None
    try:
        envelope = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(envelope, dict) or envelope.get("protocol") != WORKER_PROTOCOL:
        return None
    return envelope


def read_request_envelope(path: Path) -> dict[str, Any] | None:
    """Read one bounded request file without trusting its size."""
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_REQUEST_FILE_BYTES + 1)
    except OSError:
        return None
    return parse_request_envelope(raw)


def parse_usage(value: Any) -> dict[str, Any] | None:
    """Parse the internal consumption report; ``None`` means unknown."""
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


def build_result_envelope(
    *,
    usage: dict[str, Any],
    result: dict[str, Any],
    protocol: str = WORKER_PROTOCOL,
) -> dict[str, Any]:
    return {"protocol": protocol, "usage": usage, "result": result}


def encode_result_envelope(
    *,
    usage: dict[str, Any],
    result: dict[str, Any],
    protocol: str = WORKER_PROTOCOL,
) -> bytes:
    return encode_envelope(
        build_result_envelope(usage=usage, result=result, protocol=protocol)
    )


def expected_request_id(operation: str, payload: Any) -> str | None:
    if operation == "list":
        return payload.get("request_id") if isinstance(payload, dict) else None
    if isinstance(payload, dict) and isinstance(payload.get("request_id"), str):
        return payload["request_id"]
    return None


def expected_schemas(operation: str, include_source_payload: bool) -> set[str] | None:
    if operation == "list":
        return None  # internal listing helper, not a wire schema
    if operation == "discover":
        return {DISCOVERY_RESULT_SCHEMA}
    if include_source_payload:
        return {RESULT_SCHEMA_WITH_SOURCE_PAYLOAD}
    return {RESULT_SCHEMA}


def parse_result_envelope(
    envelope: Any,
    *,
    expected_request_id: str | None,
    expected_schemas: set[str] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
    """Validate identity/shape/usage; the caller still applies the deadline."""
    if not isinstance(envelope, dict) or envelope.get("protocol") != WORKER_PROTOCOL:
        return None, None, "worker_failure"
    usage = parse_usage(envelope.get("usage"))
    if usage is None:
        return None, None, "worker_failure"
    result = envelope.get("result")
    if not isinstance(result, dict):
        return None, usage, "worker_failure"
    if (
        expected_request_id is not None
        and result.get("request_id") != expected_request_id
    ):
        return None, usage, "worker_failure"
    if (
        expected_schemas is not None
        and result.get("schema_version") not in expected_schemas
    ):
        return None, usage, "worker_failure"
    return result, usage, None

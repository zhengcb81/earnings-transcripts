"""Worker result transfer budget (MB-ET ET-02).

The worker writes one internal result file per operation. Its only size rule
must come from the same explicit request/budget the supervisor uses, plus the
computable JSON/base64 envelope overhead — never from a module-level fixed cap.
Real source quota overruns (HTTP response bytes) must still be reported with
measured usage instead of a representation veto.

All cases are offline: the worker runs in-process with the existing private
launcher seam and a fake transport. No case builds a multi-hundred-MB fixture;
the legacy-cap case shrinks the old constant instead.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import retrieval_protocol  # noqa: E402
import retrieval_worker  # noqa: E402
import transcript_contracts  # noqa: E402
from tests.test_retrieval_runtime import (  # noqa: E402
    LAUNCHER,
    FakeResponse,
    fake_spec,
    quote_html,
)
from tests.test_transcript_api import Q2_URL, QUOTE_URL  # noqa: E402

USAGE = {"requests_used": 1, "response_bytes_used": 64, "exhausted": None}


def list_payload(**overrides: Any) -> dict[str, Any]:
    # ``list`` forwards the payload as keyword arguments to the existing API,
    # which accepts only these fields; its explicit quota travels in ``budget``.
    payload = {
        "ticker": "ACME",
        "exchange": "nyse",
        "as_of_date": "2026-09-30",
        "request_id": "budget-list-001",
        "timeout_seconds": 10,
        "download_authorized": True,
    }
    payload.update(overrides)
    return payload


def write_request(
    tmp_path: Path,
    *,
    payload: dict[str, Any],
    spec: dict[str, Any],
    response_bytes_left: int | None,
    requests_left: int | None = 4,
) -> Path:
    envelope = {
        "protocol": retrieval_protocol.WORKER_PROTOCOL,
        "operation": "list",
        "payload": payload,
        "include_source_payload": False,
        "provider_settings": {"motley_fool_enabled": True, "fmp_enabled": False},
        "budget": {
            "seconds_remaining": 10.0,
            "requests_left": requests_left,
            "response_bytes_left": response_bytes_left,
        },
        "launcher": {"factory": LAUNCHER, "spec": spec},
    }
    path = tmp_path / retrieval_protocol.REQUEST_FILENAME
    path.write_bytes(json.dumps(envelope, ensure_ascii=False).encode("utf-8"))
    return path


def run_worker(request_path: Path) -> tuple[int, dict[str, Any] | None]:
    code = retrieval_worker.main([str(request_path)])
    result_path = request_path.parent / retrieval_protocol.RESULT_FILENAME
    if not result_path.exists():
        return code, None
    return code, json.loads(result_path.read_bytes())


# ── ET-02: no fixed representation veto ─────────────────────────────


def test_worker_result_has_no_fixed_legacy_representation_veto(tmp_path, monkeypatch):
    """A legal result inside the explicit quota must survive a shrunk legacy cap."""
    legacy_present = hasattr(retrieval_worker, "MAX_RESULT_BYTES")
    # The old gate compared against one module constant. Shrinking it proves
    # the decision came from that constant, not from the request's quota.
    monkeypatch.setattr(retrieval_worker, "MAX_RESULT_BYTES", 64, raising=False)

    spec = fake_spec(
        {QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(Q2_URL))},
        calls_file=tmp_path / "calls.jsonl",
    )
    request_path = write_request(
        tmp_path,
        payload=list_payload(),
        spec=spec,
        response_bytes_left=1_000_000,
    )

    code, envelope = run_worker(request_path)

    assert code == 0
    assert envelope is not None
    data = (tmp_path / retrieval_protocol.RESULT_FILENAME).read_bytes()
    assert len(data) > 64  # the legacy cap would have rejected this result
    assert envelope["protocol"] == retrieval_protocol.WORKER_PROTOCOL
    assert envelope["result"]["status"] in ("discovered", "not_found")
    assert envelope["usage"]["requests_used"] == 1
    assert not legacy_present  # the fixed cap is gone, not merely relocated


def test_result_transfer_limit_follows_explicit_request_quota():
    declared = retrieval_protocol.result_transfer_limit(
        {"max_response_bytes": 1_000_000, "max_body_bytes": 500_000}, 1_000_000
    )
    assert declared == 6 * (2 * 500_000 + 1_000_000) + 2 * 1024 * 1024

    # No declared values: the documented defaults apply, not a separate cap.
    default = retrieval_protocol.result_transfer_limit({}, None)
    assert default == 6 * (
        2 * transcript_contracts.MAX_BODY_BYTES
        + transcript_contracts.MAX_FMP_RESPONSE_BYTES
    ) + 2 * 1024 * 1024

    # The limit tracks the caller's explicit quota in both directions.
    smaller = retrieval_protocol.result_transfer_limit(
        {"max_response_bytes": 100_000, "max_body_bytes": 50_000}, 100_000
    )
    larger = retrieval_protocol.result_transfer_limit(
        {"max_response_bytes": 200_000_000, "max_body_bytes": 50_000}, 200_000_000
    )
    assert smaller < declared < larger

    # body is narrowed by the smaller of body/response quota, never widened.
    narrowed = retrieval_protocol.result_transfer_limit(
        {"max_response_bytes": 100_000, "max_body_bytes": 500_000}, 100_000
    )
    assert narrowed == 6 * (2 * 100_000 + 100_000) + 2 * 1024 * 1024

    # The old fixed cap was 153_092_096 bytes (146 MiB). An explicit 200 MB
    # response quota can legally produce a base64 envelope above it, so the
    # legacy gate was reachable, not merely theoretical.
    explicit = retrieval_protocol.result_transfer_limit(
        {"max_response_bytes": 200_000_000, "max_body_bytes": 10_000_000},
        200_000_000,
    )
    assert explicit > 153_092_096


def test_transfer_limit_accounts_for_base64_and_json_escaping_overhead():
    raw = 4_000_000
    body = 1_000_000
    limit = retrieval_protocol.result_transfer_limit(
        {"max_response_bytes": raw, "max_body_bytes": body}, raw
    )
    base64_size = (4 * raw + 2) // 3
    escaped_body = 6 * body
    assert limit >= base64_size + escaped_body

    # A legal maximum-shape envelope (escaped content + base64 source payload)
    # fits and round-trips through the same protocol the supervisor reads.
    result = {
        "schema_version": transcript_contracts.RESULT_SCHEMA_WITH_SOURCE_PAYLOAD,
        "request_id": "budget-big-001",
        "status": "fetched",
        "content_utf8": "x" * body,
        "provider_payload_base64": "A" * base64_size,
    }
    data = retrieval_protocol.encode_result_envelope(usage=USAGE, result=result)
    assert len(data) <= limit
    parsed, usage, reason = retrieval_protocol.parse_result_envelope(
        json.loads(data),
        expected_request_id="budget-big-001",
        expected_schemas={transcript_contracts.RESULT_SCHEMA_WITH_SOURCE_PAYLOAD},
    )
    assert (reason, usage) == (None, USAGE)
    assert parsed["status"] == "fetched"

    assert retrieval_protocol.result_within_limit(
        data, {"max_response_bytes": 1, "max_body_bytes": 1}, 1
    ) is False


# ── ET-02: real quota overrun still wins over representation ────────


def test_real_response_byte_overrun_is_measured_not_a_result_veto(tmp_path):
    oversized = b"<html><body>" + b"x" * 8192 + b"</body></html>"
    spec = fake_spec(
        {QUOTE_URL: FakeResponse(QUOTE_URL, oversized)},
        calls_file=tmp_path / "calls.jsonl",
    )
    request_path = write_request(
        tmp_path,
        payload=list_payload(),
        spec=spec,
        response_bytes_left=1024,
    )

    code, envelope = run_worker(request_path)

    assert code == 0  # the worker still reports the operation's own outcome
    assert envelope is not None
    assert envelope["result"]["status"] == "content_too_large"
    assert envelope["result"]["error_code"] == "byte_limit"
    assert envelope["usage"]["exhausted"] == "response_bytes"
    assert envelope["usage"]["response_bytes_used"] >= 1024
    # The error envelope itself is small and inside the derived limit.
    assert len(
        (tmp_path / retrieval_protocol.RESULT_FILENAME).read_bytes()
    ) <= retrieval_protocol.result_transfer_limit(list_payload(), 1024)

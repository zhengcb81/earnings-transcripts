"""Test-only transparent ET stdin/stdout proxy for cross-repository offline E2E.

The first argument names a fixture JSON. Remaining arguments go unchanged to
transcript_tool.main. It starts the real retrieval worker and only replaces
that worker's HTTP session. No production mock flag or environment backdoor.
"""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import transcript_tool  # noqa: E402
from tests.test_retrieval_runtime import LAUNCHER, StallResponse, fake_spec  # noqa: E402
from tests.test_transcript_api import FMP_URL, FakeResponse  # noqa: E402


KEY = "offline-not-a-real-fmp-key"


def provider_payload(request, fixture):
    """Return exact-length mock raw JSON, matching the incoming fiscal identity."""
    row = dict(
        symbol=request.get("ticker", "INVALID"),
        year=request.get("fiscal_year", 2026),
        period=f"Q{request.get('fiscal_quarter', 1)}",
        date=fixture.get("call_date", request.get("as_of_date", "2026-09-01")),
        content=fixture.get("content_utf8", "A" * 286),
        padding="",
    )
    raw = json.dumps([row], ensure_ascii=False).encode("utf-8")
    size = fixture.get("response_bytes", 1024)
    if type(size) is not int or size < len(raw):
        raise ValueError("fixture response_bytes must fit its complete JSON payload")
    row["padding"] = "x" * (size - len(raw))
    raw = json.dumps([row], ensure_ascii=False).encode("utf-8")
    assert len(raw) == size
    return raw


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        raise ValueError("first argument must be an owned fixture-spec JSON path")
    fixture = json.loads(Path(args.pop(0)).read_text(encoding="utf-8"))
    # Capabilities still use the actual production metadata entry; no request
    # or supplier session is created for this command.
    if "--capabilities" in args:
        return transcript_tool.main(args)
    runtime = Path(fixture["worker_temp_root"])
    calls = Path(fixture["calls_file"])
    if not runtime.is_absolute() or not runtime.is_dir() or not calls.is_absolute():
        raise ValueError("fixture worker_temp_root and calls_file must be absolute")
    case = fixture.get("case", "success")
    if case not in ("padded-json", "success", "deadline", "worker-loss"):
        raise ValueError("unsupported offline fixture case")
    original_stdin = sys.stdin.read()
    try:
        request = json.loads(original_stdin)
    except ValueError:
        request = {}
    if not isinstance(request, dict):
        request = {}
    if case == "worker-loss":
        spec = fake_spec(build_failure="SystemExit:7", calls_file=calls)
    elif case == "deadline":
        spec = fake_spec(
            {FMP_URL: StallResponse(FMP_URL, stall_in_get=True)}, calls_file=calls
        )
    else:
        raw = provider_payload(request, fixture)
        response = (
            StallResponse(FMP_URL, raw, content_type="application/json", split_chunks=1)
            if case == "padded-json"
            else FakeResponse(FMP_URL, raw, content_type="application/json")
        )
        spec = fake_spec({FMP_URL: response}, calls_file=calls)
    # This fixture process always replaces any inherited real credential with
    # a non-secret mock value. Only the existing private worker launcher uses
    # the serialized fake session; ordinary transcript_tool remains unchanged.
    with (
        patch.object(sys, "stdin", io.StringIO(original_stdin)),
        patch.dict(os.environ, {"FMP_API_KEY": KEY}),
    ):
        return transcript_tool.main(
            args,
            _retrieval_launcher=LAUNCHER,
            _retrieval_spec=spec,
            _retrieval_temp_root=runtime,
        )


if __name__ == "__main__":
    raise SystemExit(main())

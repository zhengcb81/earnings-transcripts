"""Offline replay through the formal ET entry and real retrieval subprocess.

Only the worker HTTP session is mocked. No real FMP credentials or network.
Requires the project's existing pytest test dependency; it is not a production
provider launcher flag or an alternative source of content.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import sys
import tempfile  # noqa: E402 - explicit repository bootstrap
from pathlib import Path  # noqa: E402 - explicit repository bootstrap
from unittest.mock import patch  # noqa: E402 - explicit repository bootstrap

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import transcript_tool  # noqa: E402 - explicit repository bootstrap
from tests.test_retrieval_runtime import LAUNCHER, StallResponse, fake_spec, get_urls  # noqa: E402 - explicit repository bootstrap
from tests.test_transcript_api import FMP_URL, FakeResponse, make_request  # noqa: E402 - explicit repository bootstrap


KEY = "offline-not-a-real-fmp-key"


def padded_payload(size, *, ticker, quarter):
    row = dict(
        symbol=ticker,
        year=2026,
        period=f"Q{quarter}",
        date="2026-09-01",
        content="A" * 286,
        padding="",
    )
    raw = json.dumps([row], ensure_ascii=False).encode("utf-8")
    row["padding"] = "x" * (size - len(raw))
    raw = json.dumps([row], ensure_ascii=False).encode("utf-8")
    assert len(raw) == size
    return raw


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case",
        choices=("padded-json", "success", "deadline", "worker-loss"),
        default="padded-json",
    )
    parser.add_argument("--max-response-bytes", type=int, default=1024)
    parser.add_argument("--ticker", choices=("NVDA", "MSFT"), default="NVDA")
    parser.add_argument("--temp-root", type=Path)
    args = parser.parse_args(argv)
    if args.max_response_bytes <= 0:
        parser.error("max-response-bytes must be positive")
    parent = (args.temp_root or Path(tempfile.gettempdir())).resolve(strict=True)
    if not parent.is_dir():
        parser.error("temp-root must be an existing owned short directory")
    raw = padded_payload(
        1024 if args.case == "success" else 20386,
        ticker=args.ticker,
        quarter=3 if args.ticker == "MSFT" else 2,
    )
    with tempfile.TemporaryDirectory(prefix="etW02-e2e-", dir=parent) as temp:
        owned = Path(temp).resolve()
        assert owned.parent == parent and owned.name.startswith("etW02-e2e-")
        sentinel = owned / "existing-original.txt"
        sentinel.write_bytes(b"original fixture remains immutable")
        old_sha = hashlib.sha256(sentinel.read_bytes()).hexdigest()
        runtime = owned / "rt"
        runtime.mkdir()
        calls = owned / "calls.jsonl"
        if args.case == "deadline":
            spec = fake_spec(
                {FMP_URL: StallResponse(FMP_URL, stall_in_get=True)}, calls_file=calls
            )
        elif args.case == "worker-loss":
            spec = fake_spec(build_failure="SystemExit:7", calls_file=calls)
        else:
            response = (
                StallResponse(
                    FMP_URL, raw, content_type="application/json", split_chunks=1
                )
                if args.case == "padded-json"
                else FakeResponse(FMP_URL, raw, content_type="application/json")
            )
            spec = fake_spec({FMP_URL: response}, calls_file=calls)
        request = make_request(
            provider="fmp",
            exchange="nasdaq",
            max_body_bytes=1000,
            ticker=args.ticker,
            fiscal_quarter=3 if args.ticker == "MSFT" else 2,
            max_response_bytes=args.max_response_bytes,
        )
        if args.case == "deadline":
            request["timeout_seconds"] = 2
        out, err = io.StringIO(), io.StringIO()
        with (
            patch.object(sys, "stdin", io.StringIO(json.dumps(request))),
            patch.object(sys, "stdout", out),
            patch.object(sys, "stderr", err),
            patch.dict(
                os.environ,
                {"FMP_API_KEY": KEY, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"},
            ),
        ):
            rc = transcript_tool.main(
                ["--request-stdin", "--include-source-payload", "--report-usage"],
                _retrieval_launcher=LAUNCHER,
                _retrieval_spec=spec,
                _retrieval_temp_root=runtime,
            )
        result = json.loads(out.getvalue())
        receipt = json.loads(err.getvalue())
        decoded = (
            base64.b64decode(result["provider_payload_base64"])
            if result["status"] == "fetched"
            else None
        )
        assert rc == 0
        if args.case == "padded-json":
            assert (
                result["status"] == "content_too_large"
                and result["error_code"] == "byte_limit"
            )
            assert receipt["usage"] == dict(
                requests_used=1,
                response_bytes_used=len(raw),
                exhausted="response_bytes",
            )
            assert receipt["usage_complete"] is True and decoded is None
        elif args.case == "success":
            assert result["status"] == "fetched" and decoded == raw
            assert receipt["usage"] == dict(
                requests_used=1, response_bytes_used=len(raw), exhausted=None
            )
            assert receipt["usage_complete"] is True
        else:
            assert result["status"] == (
                "deadline_exceeded" if args.case == "deadline" else "provider_error"
            )
            assert (
                receipt["usage"] is None
                and receipt["usage_complete"] is False
                and decoded is None
            )
        assert list(runtime.iterdir()) == []
        assert hashlib.sha256(sentinel.read_bytes()).hexdigest() == old_sha
        # No operation creates links, but verify ownership and reparse entries
        # before the TemporaryDirectory owner performs its recursive cleanup.
        for entry in owned.rglob("*"):
            assert (
                not entry.is_symlink()
                and not getattr(entry, "is_junction", lambda: False)()
            )
        summary = dict(
            case=args.case,
            ticker=args.ticker,
            formal_entry="transcript_tool.main -> supervisor -> worker -> API -> mock HTTP",
            real_provider_request=False,
            max_response_bytes=args.max_response_bytes,
            status=result["status"],
            error_code=result.get("error_code"),
            receipt=receipt,
            observed_mock_get_calls=len(get_urls(calls)),
            source_payload_returned=decoded is not None,
            source_payload_open_sha256=hashlib.sha256(decoded).hexdigest()
            if decoded is not None
            else None,
            old_fixture_sha256=old_sha,
            old_fixture_unchanged=True,
            worker_temp_empty=True,
            paid_calls=0,
        )
        temp_path = str(owned)
    summary["temporary_root_restored"] = not Path(temp_path).exists()
    assert summary["temporary_root_restored"]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

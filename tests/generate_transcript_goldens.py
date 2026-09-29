"""Rebuild exact /2 CLI contract goldens with fake HTTP and a fixed test clock.

No real provider is contacted. The Motley success case uses an explicit
test-only provider setting; the production default stays disabled.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import transcript_api  # noqa: E402
import transcript_tool  # noqa: E402
from tests.test_transcript_api import (  # noqa: E402
    FMP_URL,
    Q2_URL,
    FakeResponse,
    FakeSession,
    fmp_payload,
    make_candidate_request,
    make_request,
    transcript_html,
)

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
_FAKE_KEY = "golden-fake-key"
_ORIGINAL_DATETIME = transcript_api.datetime


class _FixedDateTime:
    @staticmethod
    def now(tz=None):
        return datetime(2026, 9, 29, 12, 0, 0, tzinfo=timezone.utc)


def _request_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _run_cli(
    argv: list[str],
    request_bytes: bytes,
    session: FakeSession,
    *,
    key: str | None = _FAKE_KEY,
    settings: transcript_api.ProviderSettings = transcript_api.DEFAULT_PROVIDER_SETTINGS,
    expected_calls: int,
) -> bytes:
    old_stdin, old_stdout = sys.stdin, sys.stdout
    old_key = os.environ.get("FMP_API_KEY")
    output = io.StringIO()
    try:
        sys.stdin = io.StringIO(request_bytes.decode("utf-8"))
        sys.stdout = output
        if key is None:
            os.environ.pop("FMP_API_KEY", None)
        else:
            os.environ["FMP_API_KEY"] = key
        exit_code = transcript_tool.main(
            argv, _session_factory=lambda: session, _provider_settings=settings
        )
    finally:
        sys.stdin, sys.stdout = old_stdin, old_stdout
        if old_key is None:
            os.environ.pop("FMP_API_KEY", None)
        else:
            os.environ["FMP_API_KEY"] = old_key
    wire = output.getvalue()
    if exit_code != 0 or wire.count("\n") != 1 or not wire.endswith("\n"):
        raise AssertionError("CLI did not emit one successful protocol line")
    if len(session.calls) != expected_calls:
        raise AssertionError("unexpected fake HTTP call count")
    if _FAKE_KEY in wire:
        raise AssertionError("fake key leaked into result")
    if not isinstance(json.loads(wire), dict):
        raise AssertionError("CLI output is not one JSON object")
    return wire.encode("utf-8")


def build_goldens() -> dict[str, bytes]:
    fmp_request = make_request(
        ticker="MSFT", exchange="nasdaq", fiscal_quarter=3, provider="fmp"
    )
    fmp_request_bytes = _request_bytes(fmp_request)
    candidate_request = make_candidate_request()
    candidate_request_bytes = _request_bytes(candidate_request)
    fy_only_request = dict(fmp_request)
    fy_only_request.pop("fiscal_quarter")

    goldens: dict[str, bytes] = {
        "fmp_v2.request.json": fmp_request_bytes,
        "motley_candidate_v2.request.json": candidate_request_bytes,
    }
    transcript_api.datetime = _FixedDateTime
    try:
        goldens["fmp_v2.fetched.json"] = _run_cli(
            ["--request-stdin", "--include-source-payload"],
            fmp_request_bytes,
            FakeSession({
                FMP_URL: FakeResponse(
                    FMP_URL, fmp_payload(), content_type="application/json"
                )
            }),
            expected_calls=1,
        )
        goldens["fmp_v2.credentials_missing.json"] = _run_cli(
            ["--request-stdin", "--include-source-payload"],
            fmp_request_bytes,
            FakeSession({}),
            key=None,
            expected_calls=0,
        )
        goldens["fmp_v2.entitlement_402.json"] = _run_cli(
            ["--request-stdin", "--include-source-payload"],
            fmp_request_bytes,
            FakeSession({FMP_URL: FakeResponse(FMP_URL, b"", status_code=402)}),
            expected_calls=1,
        )
        goldens["fmp_v2.wrong_quarter.json"] = _run_cli(
            ["--request-stdin", "--include-source-payload"],
            fmp_request_bytes,
            FakeSession({
                FMP_URL: FakeResponse(
                    FMP_URL, fmp_payload(period="Q2"), content_type="application/json"
                )
            }),
            expected_calls=1,
        )
        goldens["fmp_v2.fy_only_invalid.json"] = _run_cli(
            ["--request-stdin", "--include-source-payload"],
            _request_bytes(fy_only_request),
            FakeSession({}),
            expected_calls=0,
        )
        goldens["motley_candidate_v2.disabled.json"] = _run_cli(
            [
                "--request-stdin", "--operation", "fetch-candidate",
                "--include-source-payload",
            ],
            candidate_request_bytes,
            FakeSession({}),
            expected_calls=0,
        )
        goldens["motley_candidate_v2.fetched_test_only.json"] = _run_cli(
            [
                "--request-stdin", "--operation", "fetch-candidate",
                "--include-source-payload",
            ],
            candidate_request_bytes,
            FakeSession({Q2_URL: FakeResponse(Q2_URL, transcript_html())}),
            settings=transcript_api.ProviderSettings(motley_fool_enabled=True),
            expected_calls=1,
        )
    finally:
        transcript_api.datetime = _ORIGINAL_DATETIME
    manifest = {
        "producer": "earnings-transcripts/transcript_tool.main",
        "result_schema": transcript_api.RESULT_SCHEMA_WITH_SOURCE_PAYLOAD,
        "fixture_only": True,
        "fixed_retrieved_at": "2026-09-29T12:00:00Z",
        "files": {
            name: {
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
            }
            for name, content in sorted(goldens.items())
        },
    }
    goldens["manifest.json"] = _request_bytes(manifest)
    return goldens


def main(argv: list[str]) -> int:
    if argv not in (["--write"], ["--check"]):
        raise SystemExit("usage: python tests/generate_transcript_goldens.py --write|--check")
    goldens = build_goldens()
    if argv == ["--write"]:
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        for name, content in goldens.items():
            (GOLDEN_DIR / name).write_bytes(content)
    else:
        mismatched = [
            name for name, content in goldens.items()
            if not (GOLDEN_DIR / name).is_file()
            or (GOLDEN_DIR / name).read_bytes() != content
        ]
        if mismatched:
            raise SystemExit("golden mismatch: " + ", ".join(mismatched))
    print(f"{len(goldens)} goldens {'written' if argv == ['--write'] else 'matched'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

"""W07 true CLI capability and explicit credentials source contracts."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from transcript_api import provider_operation_capability, fetch_transcript
import transcript_tool
from tests.test_transcript_api import make_request, FMP_URL, fmp_payload, FakeResponse
from tests.test_retrieval_runtime import LAUNCHER, fake_spec, get_urls

SENTINEL = "w07-synthetic-key-no-public-output"
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("exchange,reason", [("HKEX", "unsupported_market"),
                                           ("TOKYO", "unsupported_exchange"),
                                           ("NASDAQ", "provider_credentials_missing")])
def test_real_cli_classifies_exchange_before_http(exchange, reason):
    env = dict(os.environ)
    env.pop("FMP_API_KEY", None)
    env.pop("FMP_API_KEY_FILE", None)
    request = make_request(provider="fmp", ticker="MSFT", exchange=exchange)
    completed = subprocess.run([sys.executable, str(ROOT / "transcript_tool.py"),
        "--request-stdin", "--include-source-payload", "--report-usage"],
        input=json.dumps(request).encode(), capture_output=True, env=env, timeout=15, check=False)
    assert completed.returncode == 0
    result, usage = json.loads(completed.stdout), json.loads(completed.stderr)
    assert result["error_code"] == reason
    assert result["status"] == ("unsupported" if reason.startswith("unsupported") else "unavailable")
    assert usage["usage_complete"] is True
    assert usage["usage"]["requests_used"] == 0
    assert usage["usage"]["response_bytes_used"] == 0


def test_descriptor_exposes_market_exchange_dimensions_without_account_claim():
    capability = provider_operation_capability("fmp", "fetch")
    assert capability["supported_markets"] == ["US"]
    assert capability["supported_exchanges"] == ["auto", "nasdaq", "nyse"]
    assert capability["entitlement"] == "runtime_unknown"


@pytest.mark.parametrize("kind,reason", [("absent", "provider_credentials_file_unavailable"),
    ("empty", "provider_credentials_file_empty"), ("invalid", "provider_credentials_file_invalid")])
def test_cli_bad_explicit_key_file_named_zero_usage(tmp_path, kind, reason):
    key = tmp_path / "explicit.key"
    if kind == "empty":
        key.write_text(" \n", encoding="utf-8")
    if kind == "invalid":
        key.write_bytes(b"first\nsecond")
    env = dict(os.environ, FMP_API_KEY_FILE=str(key), FMP_API_KEY="ignored-fallback")
    request = make_request(provider="fmp", ticker="MSFT", exchange="NASDAQ")
    completed = subprocess.run([sys.executable, str(ROOT / "transcript_tool.py"),
        "--request-stdin", "--include-source-payload", "--report-usage"],
        input=json.dumps(request).encode(), capture_output=True, env=env, timeout=15, check=False)
    assert completed.returncode == 0
    result, receipt = json.loads(completed.stdout), json.loads(completed.stderr)
    assert result["error_code"] == reason
    assert receipt["usage_complete"] is True
    assert receipt["usage"]["requests_used"] == 0
    assert str(key).encode() not in completed.stdout + completed.stderr
    assert b"ignored-fallback" not in completed.stdout + completed.stderr


def test_explicit_key_file_reaches_supervised_worker_with_no_serialized_secret(tmp_path, monkeypatch):
    key_file = tmp_path / "explicit.key"
    key_file.write_text(SENTINEL + "\n", encoding="utf-8")
    monkeypatch.setenv("FMP_API_KEY_FILE", str(key_file))
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    calls = tmp_path / "calls.jsonl"
    spec = fake_spec({FMP_URL: FakeResponse(FMP_URL, fmp_payload(), content_type="application/json")}, calls_file=calls)
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    request = make_request(provider="fmp", ticker="MSFT", exchange="NASDAQ", fiscal_quarter=3)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    rc = transcript_tool.main(["--request-stdin", "--include-source-payload", "--report-usage"],
        _retrieval_launcher=LAUNCHER, _retrieval_spec=spec, _retrieval_temp_root=runtime_root)
    assert rc == 0
    assert json.loads(out.getvalue())["status"] == "fetched"
    assert json.loads(err.getvalue())["usage"]["requests_used"] == 1
    assert get_urls(calls) == [FMP_URL]
    assert SENTINEL not in out.getvalue() + err.getvalue() + calls.read_text(encoding="utf-8")
    assert list(runtime_root.iterdir()) == []
    assert "FMP_API_KEY" not in os.environ


def test_fmp_echoed_key_never_becomes_immutable_original_or_source_payload():
    from tests.test_transcript_api import FakeSession
    payload = json.loads(fmp_payload())
    payload[0]["content"] += SENTINEL
    raw = json.dumps(payload).encode()
    session = FakeSession({FMP_URL: FakeResponse(FMP_URL, raw, content_type="application/json")})
    result = fetch_transcript(make_request(provider="fmp", ticker="MSFT", exchange="NASDAQ", fiscal_quarter=3),
        fmp_api_key=SENTINEL, include_source_payload=True, session_factory=lambda: session)
    assert result["status"] == "provider_error"
    assert result["error_code"] == "provider_credentials_leaked"
    assert len(session.calls) == 1
    assert SENTINEL not in json.dumps(result)
    assert "provider_payload_base64" not in result

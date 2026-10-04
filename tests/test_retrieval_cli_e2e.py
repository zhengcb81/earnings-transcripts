"""Real-entry hard-deadline end-to-end tests (ET-DEADLINE).

Every case dispatches through the formal CLI entry (``transcript_tool.main``
without a session injection, and ``scraper.main``), through the supervisor,
through a real worker subprocess, and through the existing ``transcript_api``
code — only the HTTP transport inside the worker is fake, via the private
launcher seam. No production CLI flag or environment backdoor selects it, no
real provider is contacted, and no test may construct a translator.
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import scraper  # noqa: E402
import transcript_tool  # noqa: E402
import retrieval_runtime  # noqa: E402
from transcript_api import ProviderSettings  # noqa: E402
from tests.test_retrieval_runtime import (  # noqa: E402
    LAUNCHER,
    STARTUP_MARGIN,
    StallResponse,
    envelopes,
    fake_spec,
    get_urls,
)
from tests.test_transcript_api import (  # noqa: E402
    FMP_URL,
    Q1_URL,
    Q2_URL,
    QUOTE_URL,
    FakeResponse,
    fmp_payload,
    make_candidate_request,
    make_request,
    quote_html,
    transcript_html,
)

ACME = {"ticker": "ACME", "name_en": "Acme", "name_cn": "亚克力", "exchange": "nyse"}
ENABLED_FOOL = ProviderSettings(motley_fool_enabled=True)
E2E_KEY = "fake-key-for-e2e-test"


def run_tool(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    request: dict[str, Any],
    *,
    spec: dict[str, Any],
    temp_root: Path,
    key: str | None = E2E_KEY,
    settings: ProviderSettings | None = None,
) -> tuple[int, str, str]:
    if key is None:
        monkeypatch.delenv("FMP_API_KEY", raising=False)
    else:
        monkeypatch.setenv("FMP_API_KEY", key)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
    stdout, stderr = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    rc = transcript_tool.main(
        argv,
        _provider_settings=settings or ProviderSettings(),
        _retrieval_launcher=LAUNCHER,
        _retrieval_spec=spec,
        _retrieval_temp_root=temp_root,
    )
    return rc, stdout.getvalue(), stderr.getvalue()


def forbid_translator(monkeypatch: pytest.MonkeyPatch) -> None:
    import translator

    def explode(*args: Any, **kwargs: Any) -> None:
        pytest.fail("translator must not be constructed by these runs")

    monkeypatch.setattr(translator.TranslatorFactory, "create", explode)
    monkeypatch.setattr(scraper, "TranslatorFactory", translator.TranslatorFactory)


# ── transcript_tool: real dispatch → supervisor → worker → API ──────


def test_tool_fmp_200_runs_the_whole_real_chain(monkeypatch, tmp_path):
    forbid_translator(monkeypatch)
    monkeypatch.chdir(tmp_path)
    page = fmp_payload()
    raw_content = json.loads(page.decode("utf-8"))[0]["content"]
    expected = raw_content.replace("\r\n", "\n").replace("\r", "\n").strip()
    spec = fake_spec(
        {FMP_URL: FakeResponse(FMP_URL, page, content_type="application/json")},
        calls_file=tmp_path / "calls.jsonl",
    )
    temp_root = tmp_path / "rt"
    temp_root.mkdir()
    request = make_request(
        ticker="MSFT", exchange="nasdaq", fiscal_quarter=3, provider="fmp"
    )

    # FF's production invocation: schema /2 with the bounded payload
    rc, out, err = run_tool(
        monkeypatch,
        ["--request-stdin", "--include-source-payload"],
        request,
        spec=spec,
        temp_root=temp_root,
    )
    result = json.loads(out)
    assert rc == 0
    assert err == ""
    assert result["status"] == "fetched"
    assert result["schema_version"] == "earnings-transcript-result/2"
    assert result["content_bytes"] == len(expected.encode("utf-8"))
    assert result["canonical_content_sha256"] == hashlib.sha256(
        expected.encode("utf-8")
    ).hexdigest()
    assert result["provider_payload_sha256"] == hashlib.sha256(page).hexdigest()
    assert "content_utf8" not in result
    assert E2E_KEY not in out + err

    # default schema /1 keeps the untranslated original language
    rc, out, err = run_tool(
        monkeypatch, ["--request-stdin"], request, spec=spec, temp_root=temp_root
    )
    result = json.loads(out)
    assert rc == 0
    assert result["status"] == "fetched"
    assert result["schema_version"] == "earnings-transcript-result/1"
    assert result["content_utf8"] == expected
    assert "Management discussed customer demand" in expected

    assert get_urls(tmp_path / "calls.jsonl") == [FMP_URL, FMP_URL]
    assert all(not env["translator_loaded"] for env in envelopes(tmp_path / "calls.jsonl"))
    assert list(temp_root.iterdir()) == []
    assert E2E_KEY not in (tmp_path / "calls.jsonl").read_text(encoding="utf-8")


def test_tool_discover_and_fetch_candidate_route_through_supervisor(
    monkeypatch, tmp_path
):
    calls_file = tmp_path / "calls.jsonl"
    temp_root = tmp_path / "rt"
    temp_root.mkdir()

    discover_spec = fake_spec(
        {QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(Q2_URL))},
        calls_file=calls_file,
    )
    rc, out, _ = run_tool(
        monkeypatch,
        ["--request-stdin", "--operation", "discover"],
        make_request(),
        spec=discover_spec,
        temp_root=temp_root,
        settings=ENABLED_FOOL,
    )
    result = json.loads(out)
    assert rc == 0
    assert result["schema_version"] == "earnings-transcript-discovery-result/1"
    assert result["status"] == "discovered"
    assert result["candidates"][0]["source_url"] == Q2_URL

    candidate_spec = fake_spec(
        {Q2_URL: FakeResponse(Q2_URL, transcript_html())}, calls_file=calls_file
    )
    rc, out, _ = run_tool(
        monkeypatch,
        ["--request-stdin", "--operation", "fetch-candidate"],
        make_candidate_request(),
        spec=candidate_spec,
        temp_root=temp_root,
        settings=ENABLED_FOOL,
    )
    result = json.loads(out)
    assert rc == 0
    assert result["status"] == "fetched"
    assert result["source_url"] == Q2_URL
    assert get_urls(calls_file) == [QUOTE_URL, Q2_URL]
    assert list(temp_root.iterdir()) == []


def test_tool_named_provider_failures_survive_the_supervisor(
    monkeypatch, tmp_path
):
    temp_root = tmp_path / "rt"
    temp_root.mkdir()
    request = make_request(
        ticker="MSFT", exchange="nasdaq", fiscal_quarter=3, provider="fmp"
    )

    for status_code, expect in ((402, ("unavailable", "provider_entitlement_required")),
                                (429, ("rate_limited", "provider_http_429"))):
        calls_file = tmp_path / f"calls-{status_code}.jsonl"
        spec = fake_spec(
            {FMP_URL: FakeResponse(FMP_URL, b"", status_code=status_code)},
            calls_file=calls_file,
        )
        _, out, _ = run_tool(
            monkeypatch, ["--request-stdin"], request, spec=spec, temp_root=temp_root
        )
        result = json.loads(out)
        assert (result["status"], result["error_code"]) == expect
        assert get_urls(calls_file) == [FMP_URL]

    calls_file = tmp_path / "calls-nokey.jsonl"
    spec = fake_spec(
        {FMP_URL: FakeResponse(FMP_URL, fmp_payload())}, calls_file=calls_file
    )
    _, out, _ = run_tool(
        monkeypatch, ["--request-stdin"], request,
        spec=spec, temp_root=temp_root, key=None,
    )
    result = json.loads(out)
    assert (result["status"], result["error_code"]) == (
        "unavailable", "provider_credentials_missing",
    )
    assert not calls_file.exists()  # missing key: zero outbound


def test_tool_blocked_worker_is_killed_at_the_request_deadline(
    monkeypatch, tmp_path
):
    calls_file = tmp_path / "calls.jsonl"
    temp_root = tmp_path / "rt"
    temp_root.mkdir()
    spec = fake_spec(
        {FMP_URL: StallResponse(FMP_URL, stall_in_get=True)}, calls_file=calls_file
    )
    request = make_request(
        ticker="MSFT", exchange="nasdaq", fiscal_quarter=3,
        provider="fmp", timeout_seconds=4,
    )
    started = time.monotonic()
    rc, out, err = run_tool(
        monkeypatch, ["--request-stdin"], request, spec=spec, temp_root=temp_root
    )
    elapsed = time.monotonic() - started
    result = json.loads(out)
    assert rc == 0
    assert err == ""
    assert result["status"] == "deadline_exceeded"
    assert result["error_code"] == "provider_deadline"
    assert result["request_id"] == request["request_id"]
    assert elapsed < (
        4 + retrieval_runtime.CLEANUP_GRACE_SECONDS + STARTUP_MARGIN
    )
    assert get_urls(calls_file) == [FMP_URL]  # explicit blocking marker
    assert list(temp_root.iterdir()) == []  # worker reaped, temp removed


def test_tool_worker_failure_never_leaks_key_or_body(monkeypatch, tmp_path):
    calls_file = tmp_path / "calls.jsonl"
    temp_root = tmp_path / "rt"
    temp_root.mkdir()
    spec = fake_spec(build_failure="SystemExit:7", calls_file=calls_file)
    request = make_request(
        ticker="MSFT", exchange="nasdaq", fiscal_quarter=3, provider="fmp"
    )
    rc, out, err = run_tool(
        monkeypatch, ["--request-stdin"], request, spec=spec, temp_root=temp_root
    )
    result = json.loads(out)
    assert rc == 0
    assert (result["status"], result["error_code"]) == (
        "provider_error", "retrieval_worker_failure",
    )
    assert result["request_id"] == request["request_id"]
    assert E2E_KEY not in out + err
    assert "Management discussed customer demand" not in out + err
    assert list(temp_root.iterdir()) == []


# ── scraper batch: one shared deadline across documents ─────────────


def test_two_document_batch_second_gets_only_remaining_budget(
    tmp_path, monkeypatch, capsys
):
    forbid_translator(monkeypatch)
    out = tmp_path / "out"
    keep = out / "ACME" / "ACME_Q3_2026_earnings_call.txt"
    keep.parent.mkdir(parents=True)
    keep_text = "pre-existing keep original, never rewritten\n"
    keep.write_text(keep_text, encoding="utf-8")

    calls_file = tmp_path / "calls.jsonl"
    temp_root = tmp_path / "rt"
    temp_root.mkdir()
    spec = fake_spec(
        {
            QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(Q1_URL, Q2_URL)),
            Q1_URL: FakeResponse(Q1_URL, transcript_html(title="ACME Q1 2026")),
            Q2_URL: StallResponse(Q2_URL, stall_in_get=True),
        },
        calls_file=calls_file,
    )
    max_seconds = 7.0
    started = time.monotonic()
    rc = scraper.main(
        [
            "--periods", "2026Q1,2026Q2",
            "--max-seconds", str(max_seconds),
            "--output", str(out),
        ],
        _provider_settings=ENABLED_FOOL,
        _companies=[ACME],
        _retrieval_launcher=LAUNCHER,
        _retrieval_spec=spec,
        _retrieval_temp_root=temp_root,
    )
    elapsed = time.monotonic() - started
    captured = capsys.readouterr()

    assert rc == 3
    assert "batch_deadline" in captured.out
    # first document stored; the preset keep is untouched; second has no original
    first = out / "ACME" / "ACME_Q1_2026_earnings_call.txt"
    assert first.exists()
    assert first.read_text(encoding="utf-8") != keep_text
    assert keep.read_text(encoding="utf-8") == keep_text
    assert not (out / "ACME" / "ACME_Q2_2026_earnings_call.txt").exists()

    # the blocked second document received only the remaining batch budget
    env_records = envelopes(calls_file)
    assert len(env_records) == 3  # listing + Q1 body + Q2 body
    assert all(not env["translator_loaded"] for env in env_records)
    second_budget = env_records[-1]["budget"]
    assert second_budget["seconds_remaining"] < max_seconds
    assert second_budget["requests_left"] < 64  # listing + Q1 already consumed
    assert second_budget["response_bytes_left"] < 64 * 1024 * 1024

    # blocking marker reached; worker reaped; hard total bound respected
    assert Q2_URL in get_urls(calls_file)
    assert elapsed < (
        max_seconds + retrieval_runtime.CLEANUP_GRACE_SECONDS + STARTUP_MARGIN
    )
    assert list(temp_root.iterdir()) == []
    assert not any(out.rglob("*_bilingual*"))
    assert not any(out.rglob("*_interleaved*"))

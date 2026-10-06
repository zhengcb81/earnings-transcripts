"""Bounded batch runtime contract for scraper.py (ET-S3, ET-DEADLINE seams).

Every test runs offline: HTTP happens inside a real worker subprocess whose
transport is installed by the private launcher seam (no production CLI flag or
environment backdoor), FMP keys are fake, and all writes are confined to
pytest temporary directories or the explicit ``--output`` root. No test may
contact a live provider.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import scraper  # noqa: E402
from transcript_api import DEFAULT_PROVIDER_SETTINGS, ProviderSettings  # noqa: E402
from tests.test_retrieval_runtime import (  # noqa: E402
    LAUNCHER,
    StallResponse,
    fake_spec,
    get_urls,
    read_records,
)
from tests.test_transcript_api import (  # noqa: E402
    FMP_URL,
    QUOTE_URL,
    Q1_URL,
    Q2_URL,
    FakeResponse,
    FakeSession,
    fmp_payload,
    quote_html,
    transcript_html,
)

ACME = {"ticker": "ACME", "name_en": "Acme", "name_cn": "亚克力", "exchange": "nyse"}
ENABLED_FOOL = ProviderSettings(motley_fool_enabled=True)
FAKE_KEY = "fake-key-for-batch-test"


def run_main(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    *,
    calls_file: Path,
    responses: dict | None = None,
    settings=DEFAULT_PROVIDER_SETTINGS,
    companies: list[dict] | None = None,
    key: str | None = None,
    temp_root: Path | None = None,
) -> int:
    """Dispatch through the real CLI entry with the private worker seam."""
    if key is None:
        monkeypatch.delenv("FMP_API_KEY", raising=False)
    else:
        monkeypatch.setenv("FMP_API_KEY", key)
    return scraper.main(
        argv,
        _retrieval_launcher=LAUNCHER,
        _retrieval_spec=fake_spec(responses or {}, calls_file=calls_file),
        _provider_settings=settings,
        _companies=companies if companies is not None else [ACME],
        _retrieval_temp_root=temp_root,
    )


# ── provider gates & zero-HTTP paths ───────────────────────────────


def test_default_fool_real_run_is_disabled_with_zero_http(tmp_path, monkeypatch):
    calls_file = tmp_path / "calls.jsonl"
    rc = run_main(
        monkeypatch,
        ["--periods", "2026Q2", "--output", str(tmp_path / "out")],
        calls_file=calls_file,
        responses={},
    )
    assert rc == 1
    assert get_urls(calls_file) == []


def test_default_fool_list_is_disabled_with_zero_http_and_no_writes(
    tmp_path, monkeypatch, capsys
):
    out = tmp_path / "out"
    calls_file = tmp_path / "calls.jsonl"
    rc = run_main(
        monkeypatch,
        ["--list", "--periods", "2026Q2", "--output", str(out)],
        calls_file=calls_file,
        responses={},
    )
    assert rc == 1
    assert get_urls(calls_file) == []
    assert "provider_disabled" in capsys.readouterr().err
    assert not out.exists()


def test_fmp_list_is_unsupported_with_zero_http_and_no_writes(
    tmp_path, monkeypatch, capsys
):
    out = tmp_path / "out"
    calls_file = tmp_path / "calls.jsonl"
    rc = run_main(
        monkeypatch,
        ["--source", "fmp", "--list", "--periods", "2026Q3", "--output", str(out)],
        calls_file=calls_file,
        responses={},
        key=FAKE_KEY,
    )
    assert rc == 1
    assert get_urls(calls_file) == []
    assert "candidate_discovery_unavailable" in capsys.readouterr().err
    assert not out.exists()


def test_fmp_recent_quarters_is_period_unresolved_before_any_http(
    tmp_path, monkeypatch, capsys
):
    out = tmp_path / "out"
    calls_file = tmp_path / "calls.jsonl"
    rc = run_main(
        monkeypatch,
        ["--source", "fmp", "--quarters", "2", "--output", str(out)],
        calls_file=calls_file,
        responses={},
        key=FAKE_KEY,
    )
    assert rc == 1
    assert get_urls(calls_file) == []
    assert "period_unresolved" in capsys.readouterr().err
    assert not out.exists()


# ── dry-run: zero HTTP, zero writes ────────────────────────────────


def test_dry_run_with_explicit_periods_is_zero_http_and_creates_nothing(
    tmp_path, monkeypatch, capsys
):
    out = tmp_path / "out"
    calls_file = tmp_path / "calls.jsonl"
    rc = run_main(
        monkeypatch,
        ["--source", "fmp", "--periods", "2026Q3", "--dry-run", "--output", str(out)],
        calls_file=calls_file,
        responses={},
        key=FAKE_KEY,
    )
    captured = capsys.readouterr()
    assert rc == 0
    assert get_urls(calls_file) == []
    assert not out.exists()
    assert "download" in captured.out
    assert "ACME" in captured.out


def test_dry_run_recent_quarters_reports_unknown_without_inventing_candidates(
    tmp_path, monkeypatch, capsys
):
    out = tmp_path / "out"
    calls_file = tmp_path / "calls.jsonl"
    rc = run_main(
        monkeypatch,
        ["--source", "fmp", "--quarters", "2", "--dry-run", "--output", str(out)],
        calls_file=calls_file,
        responses={},
        key=FAKE_KEY,
    )
    captured = capsys.readouterr()
    assert rc == 0
    assert get_urls(calls_file) == []
    assert not out.exists()
    assert "unknown" in captured.out.lower()
    for fake_label in ("2026Q1", "2026Q2", "Q1 2026", "Q2 2026"):
        assert fake_label not in captured.out


# ── exact periods ──────────────────────────────────────────────────


def test_explicit_periods_send_exact_fiscal_year_and_quarter(
    tmp_path, monkeypatch
):
    calls_file = tmp_path / "calls.jsonl"
    rc = run_main(
        monkeypatch,
        ["--source", "fmp", "--periods", "2025Q4", "--output", str(tmp_path / "out")],
        calls_file=calls_file,
        responses={
            FMP_URL: FakeResponse(FMP_URL, fmp_payload(symbol="ACME", period="Q4", year=2025),
                                  content_type="application/json"),
        },
        key=FAKE_KEY,
    )
    assert rc == 0
    gets = [record for record in read_records(calls_file) if record["op"] == "get"]
    assert len(gets) == 1
    params = gets[0]["params"]
    assert params["year"] == 2025
    assert params["quarter"] == 4
    saved = list((tmp_path / "out").glob("ACME/ACME_Q4_2025_earnings_call.txt"))
    assert len(saved) == 1


def test_usage_validation_exits_2(tmp_path, monkeypatch, capsys):
    calls_file = tmp_path / "calls.jsonl"
    cases = [
        ["--periods", "2025Q4,garbage", "--output", str(tmp_path / "a")],
        ["--periods", "2025Q4", "--quarters", "2", "--output", str(tmp_path / "b")],
        ["--max-requests", "0", "--periods", "2025Q4", "--output", str(tmp_path / "c")],
        ["--max-seconds", "0", "--periods", "2025Q4", "--output", str(tmp_path / "d")],
        ["--list", "--dry-run", "--periods", "2025Q4", "--output", str(tmp_path / "e")],
    ]
    for argv in cases:
        rc = run_main(monkeypatch, argv, calls_file=calls_file, responses={}, key=FAKE_KEY)
        assert rc == 2, argv
    assert get_urls(calls_file) == []


# ── translation defaults ───────────────────────────────────────────


def test_default_run_never_translates_or_constructs_translator(
    tmp_path, monkeypatch
):
    import translator

    def explode(*args, **kwargs):
        pytest.fail("translator must not be constructed without --translate")

    monkeypatch.setattr(translator.TranslatorFactory, "create", explode)
    monkeypatch.setattr(scraper, "TranslatorFactory", translator.TranslatorFactory)
    calls_file = tmp_path / "calls.jsonl"
    out = tmp_path / "out"
    rc = run_main(
        monkeypatch,
        ["--source", "fmp", "--periods", "2026Q3", "--output", str(out)],
        calls_file=calls_file,
        responses={
            FMP_URL: FakeResponse(FMP_URL, fmp_payload(symbol="ACME"),
                                  content_type="application/json"),
        },
        key=FAKE_KEY,
    )
    assert rc == 0
    files = [p.name for p in out.rglob("*") if p.is_file()]
    assert any(name.endswith("_earnings_call.txt") for name in files)
    assert not any("_bilingual" in name or "_interleaved" in name for name in files)


# ── legacy recent-N expansion through metadata only ────────────────


def test_fool_recent_quarters_expand_only_from_resolvable_metadata(
    tmp_path, monkeypatch
):
    calls_file = tmp_path / "calls.jsonl"
    out = tmp_path / "out"
    rc = run_main(
        monkeypatch,
        ["--quarters", "1", "--output", str(out)],
        calls_file=calls_file,
        responses={
            QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(Q1_URL, Q2_URL)),
            Q2_URL: FakeResponse(Q2_URL, transcript_html()),
        },
        settings=ENABLED_FOOL,
    )
    assert rc == 0
    assert get_urls(calls_file) == [QUOTE_URL, Q2_URL]
    assert (out / "ACME" / "ACME_Q2_2026_earnings_call.txt").exists()
    assert not (out / "ACME" / "ACME_Q1_2026_earnings_call.txt").exists()


def test_fool_recent_quarters_without_resolvable_periods_is_period_unresolved(
    tmp_path, monkeypatch, capsys
):
    unparsed = "https://www.fool.com/earnings/call-transcripts/2026/09/01/acme-transcript/"
    calls_file = tmp_path / "calls.jsonl"
    out = tmp_path / "out"
    rc = run_main(
        monkeypatch,
        ["--quarters", "2", "--output", str(out)],
        calls_file=calls_file,
        responses={QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(unparsed))},
        settings=ENABLED_FOOL,
    )
    assert rc == 1
    assert "period_unresolved" in capsys.readouterr().err
    assert get_urls(calls_file) == [QUOTE_URL]  # metadata only, zero body
    assert not any(out.rglob("*_earnings_call.txt"))


def test_list_with_explicit_periods_prints_only_matching_metadata(
    tmp_path, monkeypatch, capsys
):
    calls_file = tmp_path / "calls.jsonl"
    out = tmp_path / "out"
    rc = run_main(
        monkeypatch,
        ["--list", "--periods", "2026Q2", "--output", str(out)],
        calls_file=calls_file,
        responses={QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(Q1_URL, Q2_URL))},
        settings=ENABLED_FOOL,
    )
    captured = capsys.readouterr()
    assert rc == 0
    assert get_urls(calls_file) == [QUOTE_URL]  # metadata only, zero body fetch
    assert Q2_URL in captured.out
    assert Q1_URL not in captured.out
    assert not out.exists()


# ── batch limits enforced at the execution layer ───────────────────


def test_budget_session_blocks_http_once_requests_are_exhausted():
    budget = scraper.BatchBudget(
        max_requests=1, max_seconds=60.0,
        max_response_bytes=10 * 1024 * 1024, max_output_bytes=10 * 1024 * 1024,
    )
    inner = FakeSession({QUOTE_URL: FakeResponse(QUOTE_URL, quote_html())})
    session = scraper._BudgetSession(inner, budget)
    response = session.get(QUOTE_URL, stream=True)
    for _ in response.iter_content(chunk_size=1024):
        pass
    with pytest.raises(scraper.BatchBudgetExceeded):
        session.get(QUOTE_URL, stream=True)
    assert len(inner.calls) == 1  # exhaustion never triggers a retry


def test_budget_session_with_expired_deadline_makes_zero_http():
    budget = scraper.BatchBudget(
        max_requests=5, max_seconds=0.0,
        max_response_bytes=1024, max_output_bytes=1024,
    )
    inner = FakeSession({})
    session = scraper._BudgetSession(inner, budget)
    with pytest.raises(scraper.BatchBudgetExceeded):
        session.get(QUOTE_URL, stream=True)
    assert inner.calls == []


def test_expired_batch_budget_starts_no_worker(tmp_path):
    """Zero remaining quota must be rejected before any worker is spawned."""
    budget = scraper.BatchBudget(5, 0.0, 1024, 1024)
    calls_file = tmp_path / "calls.jsonl"
    temp_root = tmp_path / "rt"
    temp_root.mkdir()
    ctx = scraper._RetrievalContext(
        launcher=LAUNCHER,
        launcher_spec=fake_spec({}, calls_file=calls_file),
        temp_root=temp_root,
        api_key=None,
    )
    outcome = scraper._supervise(
        budget, ctx, ENABLED_FOOL, "list",
        {
            "ticker": "ACME", "exchange": "nyse", "as_of_date": "2026-09-30",
            "request_id": "zero-budget-1", "timeout_seconds": 1,
            "download_authorized": True,
        },
    )
    assert outcome is None
    assert budget.exhausted == "batch_deadline"
    assert list(temp_root.iterdir()) == []
    assert not calls_file.exists()


def test_batch_max_requests_stops_batch_with_named_partial(
    tmp_path, monkeypatch, capsys
):
    calls_file = tmp_path / "calls.jsonl"
    out = tmp_path / "out"
    rc = run_main(
        monkeypatch,
        ["--periods", "2026Q1,2026Q2", "--max-requests", "1", "--output", str(out)],
        calls_file=calls_file,
        responses={
            QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(Q1_URL, Q2_URL)),
            Q1_URL: FakeResponse(Q1_URL, transcript_html(title="ACME Q1 2026")),
            Q2_URL: FakeResponse(Q2_URL, transcript_html()),
        },
        settings=ENABLED_FOOL,
    )
    captured = capsys.readouterr()
    assert rc == 3
    assert "request_limit" in captured.out
    assert get_urls(calls_file) == [QUOTE_URL]  # listing only; body blocked, no retry
    assert not any(out.rglob("*_earnings_call.txt"))


def test_batch_max_response_bytes_stops_mid_stream_with_named_failure(
    tmp_path, monkeypatch, capsys
):
    payload = fmp_payload(symbol="ACME")
    calls_file = tmp_path / "calls.jsonl"
    out = tmp_path / "out"
    rc = run_main(
        monkeypatch,
        [
            "--source", "fmp", "--periods", "2026Q3",
            "--max-response-bytes", str(len(payload) - 10),
            "--output", str(out),
        ],
        calls_file=calls_file,
        responses={
            FMP_URL: FakeResponse(FMP_URL, payload, content_type="application/json"),
        },
        key=FAKE_KEY,
    )
    captured = capsys.readouterr()
    assert rc == 3
    assert "response_bytes" in captured.out
    assert len(get_urls(calls_file)) == 1
    assert not any(out.rglob("*_earnings_call.txt"))


def test_slow_stream_hits_batch_deadline_and_stops_the_batch(
    tmp_path, monkeypatch, capsys
):
    """A permanently stalled stream is killed at the shared batch deadline."""
    calls_file = tmp_path / "calls.jsonl"
    temp_root = tmp_path / "rt"
    temp_root.mkdir()
    out = tmp_path / "out"
    started = time.monotonic()
    rc = run_main(
        monkeypatch,
        ["--periods", "2026Q2", "--max-seconds", "6.0", "--output", str(out)],
        calls_file=calls_file,
        responses={
            QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(Q2_URL)),
            Q2_URL: StallResponse(Q2_URL, transcript_html(), stall=(1, None)),
        },
        settings=ENABLED_FOOL,
        temp_root=temp_root,
    )
    elapsed = time.monotonic() - started
    captured = capsys.readouterr()
    assert rc == 3
    assert "batch_deadline" in captured.out
    assert get_urls(calls_file) == [QUOTE_URL, Q2_URL]  # body fetch started, then killed
    assert elapsed < 6.0 + 1.0 + 10.0  # deadline + unified grace + startup margin
    assert list(temp_root.iterdir()) == []  # worker reaped, temp results removed
    assert not any(out.rglob("*_earnings_call.txt"))


# ── output isolation, reuse, and conflict ──────────────────────────


def test_output_writes_are_isolated_under_output_flag(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "out"
    rc = run_main(
        monkeypatch,
        ["--source", "fmp", "--periods", "2026Q3", "--output", str(out)],
        calls_file=out / "calls.jsonl",
        responses={
            FMP_URL: FakeResponse(FMP_URL, fmp_payload(symbol="ACME"),
                                  content_type="application/json"),
        },
        key=FAKE_KEY,
    )
    assert rc == 0
    assert {p.name for p in tmp_path.iterdir()} == {"out"}
    names = {p.name for p in out.rglob("*")}
    assert "run_manifest.json" in names
    assert "scraper.log" in names
    assert any(name.endswith("_earnings_call.txt") for name in names)
    assert not (tmp_path / ".instance.lock").exists()
    assert not (tmp_path / "logs").exists()


def test_existing_original_is_reused_without_body_fetch(
    tmp_path, monkeypatch
):
    out = tmp_path / "out"
    seeded = out / "ACME" / "ACME_Q2_2026_earnings_call.txt"
    seeded.parent.mkdir(parents=True)
    seeded.write_text(
        f"{'=' * 70}\nEarnings Call Transcript\nURL: {Q2_URL}\n"
        f"{'=' * 70}\n\nPreviously stored original body.\n",
        encoding="utf-8",
    )
    before = seeded.read_bytes()
    calls_file = tmp_path / "calls.jsonl"
    rc = run_main(
        monkeypatch,
        ["--periods", "2026Q2", "--output", str(out)],
        calls_file=calls_file,
        responses={QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(Q2_URL))},
        settings=ENABLED_FOOL,
    )
    assert rc == 0
    assert get_urls(calls_file) == [QUOTE_URL]  # metadata only
    assert seeded.read_bytes() == before


def test_identity_conflict_is_named_and_never_overwrites(
    tmp_path, monkeypatch, capsys
):
    out = tmp_path / "out"
    seeded = out / "ACME" / "ACME_Q2_2026_earnings_call.txt"
    seeded.parent.mkdir(parents=True)
    seeded.write_text(
        f"{'=' * 70}\nEarnings Call Transcript\n"
        "URL: https://www.fool.com/earnings/call-transcripts/1999/01/01/acme-q2-2026-earnings-transcript/\n"
        f"{'=' * 70}\n\nStored body.\n",
        encoding="utf-8",
    )
    before = seeded.read_bytes()
    calls_file = tmp_path / "calls.jsonl"
    rc = run_main(
        monkeypatch,
        ["--periods", "2026Q2", "--output", str(out)],
        calls_file=calls_file,
        responses={QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(Q2_URL))},
        settings=ENABLED_FOOL,
    )
    captured = capsys.readouterr()
    assert rc == 1
    assert "output_conflict" in captured.err + captured.out
    assert seeded.read_bytes() == before
    assert get_urls(calls_file) == [QUOTE_URL]


def test_store_original_reports_byte_conflict_and_reused(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    cfg = scraper.load_config()
    fn = scraper.FileNaming(cfg)
    out = tmp_path / "out"
    out.mkdir()
    company = ACME
    existing = out / "ACME" / "ACME_Q2_2026_earnings_call.txt"
    existing.parent.mkdir(parents=True)
    existing.write_text("stored original\n", encoding="utf-8")
    budget = scraper.BatchBudget(10, 60.0, 10 * 1024 * 1024, 10 * 1024 * 1024)

    conflict = scraper._store_original(
        cfg, fn, company,
        {"quarter": "Q2 2026", "content": "different body", "url": Q2_URL,
         "title": "t", "source": "motley_fool", "char_count": 15,
         "scraped_at": "2026-10-03"},
        out, budget,
    )
    assert conflict[0] == "output_conflict"
    assert existing.read_text(encoding="utf-8") == "stored original\n"

    reused = scraper._store_original(
        cfg, fn, company,
        {"quarter": "Q2 2026", "content": "stored original", "url": Q2_URL,
         "title": "t", "source": "motley_fool", "char_count": 15,
         "scraped_at": "2026-10-03"},
        out, budget,
    )
    assert reused[0] == "reused"
    assert existing.read_text(encoding="utf-8") == "stored original\n"


def test_existing_fmp_original_is_reused_without_credentials_or_http(
    tmp_path, monkeypatch
):
    out = tmp_path / "out"
    seeded = out / "ACME" / "ACME_Q3_2026_earnings_call.txt"
    seeded.parent.mkdir(parents=True)
    seeded.write_text("stored original\n", encoding="utf-8")
    calls_file = tmp_path / "calls.jsonl"
    rc = run_main(
        monkeypatch,
        ["--source", "fmp", "--periods", "2026Q3", "--output", str(out)],
        calls_file=calls_file,
        responses={},
        key=None,  # no credentials available at all
    )
    assert rc == 0
    assert get_urls(calls_file) == []


# ── existing-original verification: receipt + identity/period ──────


def _seed_header_original(
    out: Path, filename: str, header_lines: list[str], body: str
) -> Path:
    seeded = out / "ACME" / filename
    seeded.parent.mkdir(parents=True, exist_ok=True)
    sep = "=" * 70
    seeded.write_text(
        f"{sep}\nEarnings Call Transcript\n"
        + "\n".join(header_lines)
        + f"\n{sep}\n\n{body}",
        encoding="utf-8",
    )
    return seeded


def _manifest_entries(out: Path) -> list[dict]:
    manifest = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    return manifest["entries"]


def test_existing_empty_original_is_named_failure_not_reused(
    tmp_path, monkeypatch, capsys
):
    """0 字节原件必须具名失败并保持原样，绝不能报 reused。"""
    out = tmp_path / "out"
    seeded = out / "ACME" / "ACME_Q3_2026_earnings_call.txt"
    seeded.parent.mkdir(parents=True)
    seeded.write_bytes(b"")
    calls_file = tmp_path / "calls.jsonl"
    rc = run_main(
        monkeypatch,
        ["--source", "fmp", "--periods", "2026Q3", "--output", str(out)],
        calls_file=calls_file,
        responses={},
        key=None,
    )
    captured = capsys.readouterr()
    assert rc == 1
    assert "output_conflict" in captured.err
    assert seeded.read_bytes() == b""
    assert get_urls(calls_file) == []
    entry = _manifest_entries(out)[0]
    assert entry["status"] == "output_conflict"
    assert entry["error_code"] == "empty_original"


def test_existing_wrong_ticker_header_is_identity_mismatch(
    tmp_path, monkeypatch, capsys
):
    """头部 Ticker 与路径期望不符 → 具名 identity_mismatch，原件保留。"""
    out = tmp_path / "out"
    seeded = _seed_header_original(
        out,
        "ACME_Q3_2026_earnings_call.txt",
        [
            "Company: Microsoft (微软)",
            "Ticker: MSFT",
            "Quarter: Q3 2026",
            "Source: fmp_api",
            "URL: N/A",
            "Characters: 33",
        ],
        "Stored body for the wrong company.",
    )
    before = seeded.read_bytes()
    calls_file = tmp_path / "calls.jsonl"
    rc = run_main(
        monkeypatch,
        ["--source", "fmp", "--periods", "2026Q3", "--output", str(out)],
        calls_file=calls_file,
        responses={},
        key=None,
    )
    captured = capsys.readouterr()
    assert rc == 1
    assert "output_conflict/identity_mismatch" in captured.err
    assert seeded.read_bytes() == before
    assert get_urls(calls_file) == []
    entry = _manifest_entries(out)[0]
    assert entry["status"] == "output_conflict"
    assert entry["error_code"] == "identity_mismatch"


def test_existing_wrong_period_header_is_period_mismatch(tmp_path, monkeypatch, capsys):
    """头部 Quarter 与请求期间不符 → 具名 period_mismatch，不凭文件名纠正。"""
    out = tmp_path / "out"
    seeded = _seed_header_original(
        out,
        "ACME_Q3_2026_earnings_call.txt",
        [
            "Company: Acme (亚克力)",
            "Ticker: ACME",
            "Quarter: Q1 2026",
            "Source: fmp_api",
            "URL: N/A",
            "Characters: 30",
        ],
        "Stored body for another quarter.",
    )
    before = seeded.read_bytes()
    calls_file = tmp_path / "calls.jsonl"
    rc = run_main(
        monkeypatch,
        ["--source", "fmp", "--periods", "2026Q3", "--output", str(out)],
        calls_file=calls_file,
        responses={},
        key=None,
    )
    captured = capsys.readouterr()
    assert rc == 1
    assert "output_conflict/period_mismatch" in captured.err
    assert seeded.read_bytes() == before
    entry = _manifest_entries(out)[0]
    assert entry["status"] == "output_conflict"
    assert entry["error_code"] == "period_mismatch"


def test_characters_header_is_never_reported_as_content_bytes(tmp_path, monkeypatch):
    """头部 Characters: 500000 只是历史诊断，content_bytes 必须是实算字节。"""
    out = tmp_path / "out"
    body = "Truncated prepared remarks."
    seeded = _seed_header_original(
        out,
        "ACME_Q3_2026_earnings_call.txt",
        [
            "Company: Acme (亚克力)",
            "Ticker: ACME",
            "Quarter: Q3 2026",
            "Source: fmp_api",
            "URL: N/A",
            "Characters: 500000",
        ],
        body,
    )
    before = seeded.read_bytes()
    calls_file = tmp_path / "calls.jsonl"
    rc = run_main(
        monkeypatch,
        ["--source", "fmp", "--periods", "2026Q3", "--output", str(out)],
        calls_file=calls_file,
        responses={},
        key=None,
    )
    assert rc == 0
    assert seeded.read_bytes() == before
    assert get_urls(calls_file) == []
    entry = _manifest_entries(out)[0]
    assert entry["status"] == "legacy_unverified"
    assert entry["content_bytes"] == len(body.encode("utf-8"))
    assert entry["content_bytes"] != 500000


def test_url_only_legacy_original_reports_unknown_identity_missing(
    tmp_path, monkeypatch, capsys
):
    """头部只有 URL（身份可证、期间不可证）→ unknown/identity_missing，非 verified reused。"""
    out = tmp_path / "out"
    seeded = out / "ACME" / "ACME_Q2_2026_earnings_call.txt"
    seeded.parent.mkdir(parents=True)
    seeded.write_text(
        f"{'=' * 70}\nEarnings Call Transcript\nURL: {Q2_URL}\n"
        f"{'=' * 70}\n\nPreviously stored original body.\n",
        encoding="utf-8",
    )
    before = seeded.read_bytes()
    calls_file = tmp_path / "calls.jsonl"
    rc = run_main(
        monkeypatch,
        ["--periods", "2026Q2", "--output", str(out)],
        calls_file=calls_file,
        responses={QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(Q2_URL))},
        settings=ENABLED_FOOL,
    )
    captured = capsys.readouterr()
    assert rc == 0
    assert "unknown/identity_missing" in captured.err
    assert seeded.read_bytes() == before
    assert get_urls(calls_file) == [QUOTE_URL]  # metadata only, zero body
    entry = _manifest_entries(out)[0]
    assert entry["status"] == "unknown"
    assert entry["error_code"] == "identity_missing"
    assert entry["content_bytes"] == len(
        "Previously stored original body.".encode("utf-8")
    )


def test_new_fmp_original_writes_receipt_and_second_run_reuses_zero_http(
    tmp_path, monkeypatch
):
    """真实批次 CLI → 原子 TXT+收据 → 再次运行零 HTTP 且收据逐项吻合。"""
    import hashlib

    from transcript_artifact import canonical_body, receipt_path

    out = tmp_path / "out"
    rc = run_main(
        monkeypatch,
        ["--source", "fmp", "--periods", "2026Q3", "--output", str(out)],
        calls_file=tmp_path / "calls1.jsonl",
        responses={
            FMP_URL: FakeResponse(
                FMP_URL, fmp_payload(symbol="ACME"), content_type="application/json"
            ),
        },
        key=FAKE_KEY,
    )
    assert rc == 0
    txt = out / "ACME" / "ACME_Q3_2026_earnings_call.txt"
    assert txt.exists()
    rpath = receipt_path(txt)
    assert rpath.exists()
    assert [p.name for p in out.rglob("*.tmp-*")] == []
    assert _manifest_entries(out)[0]["status"] == "fetched"

    receipt = json.loads(rpath.read_text(encoding="utf-8"))
    assert receipt["schema"] == "et-local-text-receipt/1"
    assert receipt["kind"] == "download"
    assert receipt["ticker"] == "ACME"
    assert (receipt["fiscal_year"], receipt["fiscal_quarter"]) == (2026, 3)
    assert receipt["fiscal_period"] == "2026-Q3"
    assert receipt["provider"] == "fmp"
    assert receipt["extraction_version"]
    assert receipt["obtained_at"]
    assert receipt["published_date"] is None

    raw = txt.read_bytes()
    assert receipt["file_sha256"] == hashlib.sha256(raw).hexdigest()
    assert receipt["file_bytes"] == len(raw)
    body = canonical_body(raw.decode("utf-8")).encode("utf-8")
    assert receipt["body_sha256"] == hashlib.sha256(body).hexdigest()
    assert receipt["body_bytes"] == len(body)
    assert receipt["body_bytes"] < receipt["file_bytes"]

    calls2 = tmp_path / "calls2.jsonl"
    rc2 = run_main(
        monkeypatch,
        ["--source", "fmp", "--periods", "2026Q3", "--output", str(out)],
        calls_file=calls2,
        responses={},
        key=FAKE_KEY,
    )
    assert rc2 == 0
    assert get_urls(calls2) == []  # zero HTTP on verified reuse
    assert txt.read_bytes() == raw
    entry = _manifest_entries(out)[0]
    assert entry["status"] == "reused"
    assert entry["content_bytes"] == receipt["body_bytes"]


def test_tampered_original_is_receipt_mismatch_and_keeps_bytes(
    tmp_path, monkeypatch, capsys
):
    """收据与当前字节不符 → 具名 receipt_mismatch，篡改后的原件也原样保留。"""
    out = tmp_path / "out"
    rc = run_main(
        monkeypatch,
        ["--source", "fmp", "--periods", "2026Q3", "--output", str(out)],
        calls_file=tmp_path / "calls1.jsonl",
        responses={
            FMP_URL: FakeResponse(
                FMP_URL, fmp_payload(symbol="ACME"), content_type="application/json"
            ),
        },
        key=FAKE_KEY,
    )
    assert rc == 0
    txt = out / "ACME" / "ACME_Q3_2026_earnings_call.txt"
    tampered = txt.read_bytes() + b"\ntampered line\n"
    txt.write_bytes(tampered)

    calls2 = tmp_path / "calls2.jsonl"
    rc2 = run_main(
        monkeypatch,
        ["--source", "fmp", "--periods", "2026Q3", "--output", str(out)],
        calls_file=calls2,
        responses={},
        key=FAKE_KEY,
    )
    captured = capsys.readouterr()
    assert rc2 == 1
    assert "output_conflict/receipt_mismatch" in captured.err
    assert get_urls(calls2) == []
    assert txt.read_bytes() == tampered
    entry = _manifest_entries(out)[0]
    assert entry["status"] == "output_conflict"
    assert entry["error_code"] == "receipt_mismatch"


def test_bad_receipt_json_is_named_failure_and_original_kept(
    tmp_path, monkeypatch, capsys
):
    """半写/坏 JSON 收据 → 具名 receipt_invalid，TXT 不动、收据不被“修好”。"""
    out = tmp_path / "out"
    rc = run_main(
        monkeypatch,
        ["--source", "fmp", "--periods", "2026Q3", "--output", str(out)],
        calls_file=tmp_path / "calls1.jsonl",
        responses={
            FMP_URL: FakeResponse(
                FMP_URL, fmp_payload(symbol="ACME"), content_type="application/json"
            ),
        },
        key=FAKE_KEY,
    )
    assert rc == 0
    txt = out / "ACME" / "ACME_Q3_2026_earnings_call.txt"
    rpath = txt.parent / "ACME_Q3_2026_earnings_call.receipt.json"
    txt_before = txt.read_bytes()
    rpath.write_bytes(b'{"schema": "et-local-text-receipt/1", "kind": "down')

    calls2 = tmp_path / "calls2.jsonl"
    rc2 = run_main(
        monkeypatch,
        ["--source", "fmp", "--periods", "2026Q3", "--output", str(out)],
        calls_file=calls2,
        responses={},
        key=FAKE_KEY,
    )
    captured = capsys.readouterr()
    assert rc2 == 1
    assert "output_conflict/receipt_invalid" in captured.err
    assert get_urls(calls2) == []
    assert txt.read_bytes() == txt_before
    assert rpath.read_bytes() == b'{"schema": "et-local-text-receipt/1", "kind": "down'


def test_interrupted_receipt_write_keeps_original_and_reports_legacy(
    tmp_path, monkeypatch
):
    """收据写入中断：原件已落盘不丢，下次按 legacy 头部证明保守报告。"""

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(scraper, "write_atomic", boom)
    out = tmp_path / "out"
    rc = run_main(
        monkeypatch,
        ["--source", "fmp", "--periods", "2026Q3", "--output", str(out)],
        calls_file=tmp_path / "calls1.jsonl",
        responses={
            FMP_URL: FakeResponse(
                FMP_URL, fmp_payload(symbol="ACME"), content_type="application/json"
            ),
        },
        key=FAKE_KEY,
    )
    assert rc == 0
    txt = out / "ACME" / "ACME_Q3_2026_earnings_call.txt"
    assert txt.exists()
    rpath = txt.parent / "ACME_Q3_2026_earnings_call.receipt.json"
    assert not rpath.exists()
    raw = txt.read_bytes()
    assert _manifest_entries(out)[0]["status"] == "fetched"
    assert [p.name for p in out.rglob("*.tmp-*")] == []

    monkeypatch.undo()
    calls2 = tmp_path / "calls2.jsonl"
    rc2 = run_main(
        monkeypatch,
        ["--source", "fmp", "--periods", "2026Q3", "--output", str(out)],
        calls_file=calls2,
        responses={},
        key=FAKE_KEY,
    )
    assert rc2 == 0
    assert get_urls(calls2) == []
    assert txt.read_bytes() == raw
    entry = _manifest_entries(out)[0]
    assert entry["status"] == "legacy_unverified"


def test_reuse_of_verified_legacy_file_never_constructs_translator(
    tmp_path, monkeypatch
):
    """默认（无 --translate）复用路径同样零翻译器构造。"""
    import translator

    def explode(*args, **kwargs):
        pytest.fail("translator must not be constructed without --translate")

    monkeypatch.setattr(translator.TranslatorFactory, "create", explode)
    monkeypatch.setattr(scraper, "TranslatorFactory", translator.TranslatorFactory)
    out = tmp_path / "out"
    seeded = _seed_header_original(
        out,
        "ACME_Q3_2026_earnings_call.txt",
        [
            "Company: Acme (亚克力)",
            "Ticker: ACME",
            "Quarter: Q3 2026",
            "Source: fmp_api",
            "URL: N/A",
            "Characters: 33",
        ],
        "Stored body for the right company.",
    )
    before = seeded.read_bytes()
    calls_file = tmp_path / "calls.jsonl"
    rc = run_main(
        monkeypatch,
        ["--source", "fmp", "--periods", "2026Q3", "--output", str(out)],
        calls_file=calls_file,
        responses={},
        key=None,
    )
    assert rc == 0
    assert seeded.read_bytes() == before
    assert get_urls(calls_file) == []
    assert _manifest_entries(out)[0]["status"] == "legacy_unverified"


def test_output_limit_leaves_no_temp_or_original(tmp_path, monkeypatch, capsys):
    calls_file = tmp_path / "calls.jsonl"
    out = tmp_path / "out"
    rc = run_main(
        monkeypatch,
        ["--source", "fmp", "--periods", "2026Q3",
         "--max-output-bytes", "5", "--output", str(out)],
        calls_file=calls_file,
        responses={
            FMP_URL: FakeResponse(FMP_URL, fmp_payload(symbol="ACME"),
                                  content_type="application/json"),
        },
        key=FAKE_KEY,
    )
    captured = capsys.readouterr()
    assert rc == 3
    assert "output_bytes" in captured.out
    assert not any(out.rglob("*_earnings_call.txt"))
    leftovers = [p.name for p in out.rglob("*") if ".tmp-" in p.name]
    assert leftovers == []


def test_temp_file_is_cleaned_when_write_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = scraper.load_config()
    fn = scraper.FileNaming(cfg)
    out = tmp_path / "out"
    out.mkdir()
    budget = scraper.BatchBudget(10, 60.0, 10 * 1024 * 1024, 10 * 1024 * 1024)

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(scraper.os, "replace", boom)
    with pytest.raises(OSError):
        scraper._store_original(
            cfg, fn, ACME,
            {"quarter": "Q2 2026", "content": "fresh body", "url": Q2_URL,
             "title": "t", "source": "motley_fool", "char_count": 10,
             "scraped_at": "2026-10-03"},
            out, budget,
        )
    assert [p.name for p in out.rglob("*.tmp-*")] == []
    assert not (out / "ACME" / "ACME_Q2_2026_earnings_call.txt").exists()


def test_list_metadata_requests_count_against_batch_limit(
    tmp_path, monkeypatch, capsys
):
    second = {"ticker": "ZETA", "name_en": "Zeta", "name_cn": "泽塔", "exchange": "nyse"}
    calls_file = tmp_path / "calls.jsonl"
    rc = run_main(
        monkeypatch,
        ["--list", "--periods", "2026Q2", "--max-requests", "1",
         "--output", str(tmp_path / "out")],
        calls_file=calls_file,
        responses={QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(Q2_URL))},
        settings=ENABLED_FOOL,
        companies=[ACME, second],
    )
    captured = capsys.readouterr()
    assert rc == 3
    assert "request_limit" in captured.out
    assert len(get_urls(calls_file)) == 1  # metadata counts; second ticker blocked
    assert not (tmp_path / "out").exists()


# ── manifest / request log hygiene ─────────────────────────────────


def test_manifest_records_requests_without_key_or_body(
    tmp_path, monkeypatch, capsys
):
    calls_file = tmp_path / "calls.jsonl"
    out = tmp_path / "out"
    rc = run_main(
        monkeypatch,
        ["--source", "fmp", "--periods", "2026Q3", "--output", str(out)],
        calls_file=calls_file,
        responses={
            FMP_URL: FakeResponse(FMP_URL, fmp_payload(symbol="ACME"),
                                  content_type="application/json"),
        },
        key=FAKE_KEY,
    )
    assert rc == 0
    manifest = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["entries"]
    entry = manifest["entries"][0]
    assert entry["status"] == "fetched"
    assert entry["request_id"]
    assert entry["ticker"] == "ACME"
    assert entry["fiscal_period"] == "2026-Q3"
    assert isinstance(entry["content_bytes"], int) and entry["content_bytes"] > 0
    assert isinstance(entry["elapsed_seconds"], float)
    manifest_text = (out / "run_manifest.json").read_text(encoding="utf-8")
    assert FAKE_KEY not in manifest_text
    assert "Management discussed customer demand" not in manifest_text
    captured = capsys.readouterr()
    assert FAKE_KEY not in captured.out + captured.err
    assert "Management discussed customer demand" not in captured.out + captured.err
    if calls_file.exists():
        assert FAKE_KEY not in calls_file.read_text(encoding="utf-8")


# ── real subprocess exit paths (offline-safe by construction) ──────


def _run_cli_subprocess(*args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, str(ROOT / "scraper.py"), *args],
        cwd=str(ROOT),
        env=env,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        timeout=60,
    )


def test_subprocess_dry_run_real_exit_path(tmp_path):
    out = tmp_path / "out"
    completed = _run_cli_subprocess(
        "--periods", "2026Q2", "--dry-run", "--output", str(out),
    )
    assert completed.returncode == 0
    assert not out.exists()


def test_subprocess_fmp_list_real_exit_path(tmp_path):
    out = tmp_path / "out"
    completed = _run_cli_subprocess(
        "--source", "fmp", "--list", "--periods", "2026Q3", "--output", str(out),
    )
    assert completed.returncode == 1
    assert "candidate_discovery_unavailable" in completed.stderr
    assert not out.exists()


def test_subprocess_default_disabled_explicit_periods_exit_path(tmp_path):
    out = tmp_path / "out"
    completed = _run_cli_subprocess(
        "--ticker", "MSFT", "--periods", "2026Q2", "--output", str(out),
    )
    assert completed.returncode == 1
    assert "provider_disabled" in completed.stderr
    assert not any(out.rglob("*_earnings_call.txt")) if out.exists() else True

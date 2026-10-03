"""Bounded batch runtime contract for scraper.py (ET-S3).

Every test runs offline: fake transports are injected, FMP keys are fake, and
all writes are confined to pytest temporary directories or the explicit
``--output`` root. No test may contact a live provider.
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
    session: FakeSession | None = None,
    settings=DEFAULT_PROVIDER_SETTINGS,
    companies: list[dict] | None = None,
    key: str | None = None,
) -> int:
    """Dispatch through the real CLI entry with injected offline plumbing."""
    if key is None:
        monkeypatch.delenv("FMP_API_KEY", raising=False)
    else:
        monkeypatch.setenv("FMP_API_KEY", key)
    return scraper.main(
        argv,
        _session_factory=(lambda: session) if session is not None else None,
        _provider_settings=settings,
        _companies=companies if companies is not None else [ACME],
    )


class SlowStreamResponse(FakeResponse):
    """First chunk immediately, then stall past the batch deadline."""

    def __init__(self, url, payload, stall_seconds: float, **kwargs):
        super().__init__(url, payload, **kwargs)
        self._stall = stall_seconds

    def iter_content(self, chunk_size):
        half = max(1, len(self._payload) // 2)
        yield self._payload[:half]
        time.sleep(self._stall)
        yield self._payload[half:]


# ── provider gates & zero-HTTP paths ───────────────────────────────


def test_default_fool_real_run_is_disabled_with_zero_http(tmp_path, monkeypatch):
    session = FakeSession({})  # any HTTP raises KeyError
    rc = run_main(
        monkeypatch,
        ["--periods", "2026Q2", "--output", str(tmp_path / "out")],
        session=session,
    )
    assert rc == 1
    assert session.calls == []


def test_default_fool_list_is_disabled_with_zero_http_and_no_writes(
    tmp_path, monkeypatch, capsys
):
    out = tmp_path / "out"
    session = FakeSession({})
    rc = run_main(
        monkeypatch,
        ["--list", "--periods", "2026Q2", "--output", str(out)],
        session=session,
    )
    assert rc == 1
    assert session.calls == []
    assert "provider_disabled" in capsys.readouterr().err
    assert not out.exists()


def test_fmp_list_is_unsupported_with_zero_http_and_no_writes(
    tmp_path, monkeypatch, capsys
):
    out = tmp_path / "out"
    session = FakeSession({})
    rc = run_main(
        monkeypatch,
        ["--source", "fmp", "--list", "--periods", "2026Q3", "--output", str(out)],
        session=session,
        key=FAKE_KEY,
    )
    assert rc == 1
    assert session.calls == []
    assert "candidate_discovery_unavailable" in capsys.readouterr().err
    assert not out.exists()


def test_fmp_recent_quarters_is_period_unresolved_before_any_http(
    tmp_path, monkeypatch, capsys
):
    out = tmp_path / "out"
    session = FakeSession({})
    rc = run_main(
        monkeypatch,
        ["--source", "fmp", "--quarters", "2", "--output", str(out)],
        session=session,
        key=FAKE_KEY,
    )
    assert rc == 1
    assert session.calls == []
    assert "period_unresolved" in capsys.readouterr().err
    assert not out.exists()


# ── dry-run: zero HTTP, zero writes ────────────────────────────────


def test_dry_run_with_explicit_periods_is_zero_http_and_creates_nothing(
    tmp_path, monkeypatch, capsys
):
    out = tmp_path / "out"
    session = FakeSession({})
    rc = run_main(
        monkeypatch,
        ["--source", "fmp", "--periods", "2026Q3", "--dry-run", "--output", str(out)],
        session=session,
        key=FAKE_KEY,
    )
    captured = capsys.readouterr()
    assert rc == 0
    assert session.calls == []
    assert not out.exists()
    assert "download" in captured.out
    assert "ACME" in captured.out


def test_dry_run_recent_quarters_reports_unknown_without_inventing_candidates(
    tmp_path, monkeypatch, capsys
):
    out = tmp_path / "out"
    session = FakeSession({})
    rc = run_main(
        monkeypatch,
        ["--source", "fmp", "--quarters", "2", "--dry-run", "--output", str(out)],
        session=session,
        key=FAKE_KEY,
    )
    captured = capsys.readouterr()
    assert rc == 0
    assert session.calls == []
    assert not out.exists()
    assert "unknown" in captured.out.lower()
    for fake_label in ("2026Q1", "2026Q2", "Q1 2026", "Q2 2026"):
        assert fake_label not in captured.out


# ── exact periods ──────────────────────────────────────────────────


def test_explicit_periods_send_exact_fiscal_year_and_quarter(
    tmp_path, monkeypatch
):
    session = FakeSession({
        FMP_URL: FakeResponse(FMP_URL, fmp_payload(symbol="ACME", period="Q4", year=2025),
                              content_type="application/json"),
    })
    rc = run_main(
        monkeypatch,
        ["--source", "fmp", "--periods", "2025Q4", "--output", str(tmp_path / "out")],
        session=session,
        key=FAKE_KEY,
    )
    assert rc == 0
    assert len(session.calls) == 1
    params = session.calls[0][1]["params"]
    assert params["year"] == 2025
    assert params["quarter"] == 4
    saved = list((tmp_path / "out").glob("ACME/ACME_Q4_2025_earnings_call.txt"))
    assert len(saved) == 1


def test_usage_validation_exits_2(tmp_path, monkeypatch, capsys):
    session = FakeSession({})
    cases = [
        ["--periods", "2025Q4,garbage", "--output", str(tmp_path / "a")],
        ["--periods", "2025Q4", "--quarters", "2", "--output", str(tmp_path / "b")],
        ["--max-requests", "0", "--periods", "2025Q4", "--output", str(tmp_path / "c")],
        ["--max-seconds", "0", "--periods", "2025Q4", "--output", str(tmp_path / "d")],
        ["--list", "--dry-run", "--periods", "2025Q4", "--output", str(tmp_path / "e")],
    ]
    for argv in cases:
        rc = run_main(monkeypatch, argv, session=session, key=FAKE_KEY)
        assert rc == 2, argv
    assert session.calls == []


# ── translation defaults ───────────────────────────────────────────


def test_default_run_never_translates_or_constructs_translator(
    tmp_path, monkeypatch
):
    import translator

    def explode(*args, **kwargs):
        pytest.fail("translator must not be constructed without --translate")

    monkeypatch.setattr(translator.TranslatorFactory, "create", explode)
    monkeypatch.setattr(scraper, "TranslatorFactory", translator.TranslatorFactory)
    session = FakeSession({
        FMP_URL: FakeResponse(FMP_URL, fmp_payload(symbol="ACME"),
                              content_type="application/json"),
    })
    out = tmp_path / "out"
    rc = run_main(
        monkeypatch,
        ["--source", "fmp", "--periods", "2026Q3", "--output", str(out)],
        session=session,
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
    session = FakeSession({
        QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(Q1_URL, Q2_URL)),
        Q2_URL: FakeResponse(Q2_URL, transcript_html()),
    })
    out = tmp_path / "out"
    rc = run_main(
        monkeypatch,
        ["--quarters", "1", "--output", str(out)],
        session=session,
        settings=ENABLED_FOOL,
    )
    assert rc == 0
    assert [call[0] for call in session.calls] == [QUOTE_URL, Q2_URL]
    assert (out / "ACME" / "ACME_Q2_2026_earnings_call.txt").exists()
    assert not (out / "ACME" / "ACME_Q1_2026_earnings_call.txt").exists()


def test_fool_recent_quarters_without_resolvable_periods_is_period_unresolved(
    tmp_path, monkeypatch, capsys
):
    unparsed = "https://www.fool.com/earnings/call-transcripts/2026/09/01/acme-transcript/"
    session = FakeSession({QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(unparsed))})
    out = tmp_path / "out"
    rc = run_main(
        monkeypatch,
        ["--quarters", "2", "--output", str(out)],
        session=session,
        settings=ENABLED_FOOL,
    )
    assert rc == 1
    assert "period_unresolved" in capsys.readouterr().err
    assert [call[0] for call in session.calls] == [QUOTE_URL]  # metadata only, zero body
    assert not any(out.rglob("*_earnings_call.txt"))


def test_list_with_explicit_periods_prints_only_matching_metadata(
    tmp_path, monkeypatch, capsys
):
    session = FakeSession({QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(Q1_URL, Q2_URL))})
    out = tmp_path / "out"
    rc = run_main(
        monkeypatch,
        ["--list", "--periods", "2026Q2", "--output", str(out)],
        session=session,
        settings=ENABLED_FOOL,
    )
    captured = capsys.readouterr()
    assert rc == 0
    assert len(session.calls) == 1
    assert session.calls[0][0] == QUOTE_URL  # metadata only, zero body fetch
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


def test_batch_max_requests_stops_batch_with_named_partial(
    tmp_path, monkeypatch, capsys
):
    session = FakeSession({
        QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(Q1_URL, Q2_URL)),
        Q1_URL: FakeResponse(Q1_URL, transcript_html(title="ACME Q1 2026")),
        Q2_URL: FakeResponse(Q2_URL, transcript_html()),
    })
    out = tmp_path / "out"
    rc = run_main(
        monkeypatch,
        ["--periods", "2026Q1,2026Q2", "--max-requests", "1", "--output", str(out)],
        session=session,
        settings=ENABLED_FOOL,
    )
    captured = capsys.readouterr()
    assert rc == 3
    assert "request_limit" in captured.out
    assert len(session.calls) == 1  # listing only; body blocked, no retry
    assert not any(out.rglob("*_earnings_call.txt"))


def test_batch_max_response_bytes_stops_mid_stream_with_named_failure(
    tmp_path, monkeypatch, capsys
):
    payload = fmp_payload(symbol="ACME")
    session = FakeSession({
        FMP_URL: FakeResponse(FMP_URL, payload, content_type="application/json"),
    })
    out = tmp_path / "out"
    rc = run_main(
        monkeypatch,
        [
            "--source", "fmp", "--periods", "2026Q3",
            "--max-response-bytes", str(len(payload) - 10),
            "--output", str(out),
        ],
        session=session,
        key=FAKE_KEY,
    )
    captured = capsys.readouterr()
    assert rc == 3
    assert "response_bytes" in captured.out
    assert len(session.calls) == 1
    assert not any(out.rglob("*_earnings_call.txt"))


def test_slow_stream_hits_batch_deadline_and_closes_connection(
    tmp_path, monkeypatch, capsys
):
    slow_body = SlowStreamResponse(Q2_URL, transcript_html(), stall_seconds=1.6)
    session = FakeSession({
        QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(Q2_URL)),
        Q2_URL: slow_body,
    })
    out = tmp_path / "out"
    rc = run_main(
        monkeypatch,
        ["--periods", "2026Q2", "--max-seconds", "1.0", "--output", str(out)],
        session=session,
        settings=ENABLED_FOOL,
    )
    captured = capsys.readouterr()
    assert rc == 3
    assert "batch_deadline" in captured.out
    assert len(session.calls) == 2
    assert slow_body.closed is True  # deadline closes the connection
    assert not any(out.rglob("*_earnings_call.txt"))


# ── output isolation, reuse, and conflict ──────────────────────────


def test_output_writes_are_isolated_under_output_flag(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    session = FakeSession({
        FMP_URL: FakeResponse(FMP_URL, fmp_payload(symbol="ACME"),
                              content_type="application/json"),
    })
    out = tmp_path / "out"
    rc = run_main(
        monkeypatch,
        ["--source", "fmp", "--periods", "2026Q3", "--output", str(out)],
        session=session,
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
    session = FakeSession({QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(Q2_URL))})
    rc = run_main(
        monkeypatch,
        ["--periods", "2026Q2", "--output", str(out)],
        session=session,
        settings=ENABLED_FOOL,
    )
    assert rc == 0
    assert [call[0] for call in session.calls] == [QUOTE_URL]  # metadata only
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
    session = FakeSession({QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(Q2_URL))})
    rc = run_main(
        monkeypatch,
        ["--periods", "2026Q2", "--output", str(out)],
        session=session,
        settings=ENABLED_FOOL,
    )
    captured = capsys.readouterr()
    assert rc == 1
    assert "output_conflict" in captured.err + captured.out
    assert seeded.read_bytes() == before
    assert [call[0] for call in session.calls] == [QUOTE_URL]


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
    session = FakeSession({})
    rc = run_main(
        monkeypatch,
        ["--source", "fmp", "--periods", "2026Q3", "--output", str(out)],
        session=session,
        key=None,  # no credentials available at all
    )
    assert rc == 0
    assert session.calls == []


def test_output_limit_leaves_no_temp_or_original(tmp_path, monkeypatch, capsys):
    session = FakeSession({
        FMP_URL: FakeResponse(FMP_URL, fmp_payload(symbol="ACME"),
                              content_type="application/json"),
    })
    out = tmp_path / "out"
    rc = run_main(
        monkeypatch,
        ["--source", "fmp", "--periods", "2026Q3",
         "--max-output-bytes", "5", "--output", str(out)],
        session=session,
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
    session = FakeSession({QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(Q2_URL))})
    rc = run_main(
        monkeypatch,
        ["--list", "--periods", "2026Q2", "--max-requests", "1",
         "--output", str(tmp_path / "out")],
        session=session,
        settings=ENABLED_FOOL,
        companies=[ACME, second],
    )
    captured = capsys.readouterr()
    assert rc == 3
    assert "request_limit" in captured.out
    assert len(session.calls) == 1  # metadata counts; second ticker blocked
    assert not (tmp_path / "out").exists()


# ── manifest / request log hygiene ─────────────────────────────────


def test_manifest_records_requests_without_key_or_body(
    tmp_path, monkeypatch, capsys
):
    session = FakeSession({
        FMP_URL: FakeResponse(FMP_URL, fmp_payload(symbol="ACME"),
                              content_type="application/json"),
    })
    out = tmp_path / "out"
    rc = run_main(
        monkeypatch,
        ["--source", "fmp", "--periods", "2026Q3", "--output", str(out)],
        session=session,
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

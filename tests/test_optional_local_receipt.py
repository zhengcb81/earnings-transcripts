"""Bad optional metadata cannot outrank readable local original evidence."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import scraper  # noqa: E402
from transcript_artifact import receipt_path, verify_stored_original  # noqa: E402
from tests.test_transcript_artifact import attach_receipt, make_text, write_txt  # noqa: E402


@pytest.mark.parametrize("sidecar", [
    b"{half-written", b"\xff", b"[]",
    b'{"schema":"future/9","kind":"download"}',
    b'{"schema":"et-local-text-receipt/1"}',
    b"[" * 3000 + b"]" * 3000,
], ids=["half-json", "encoding", "array", "future", "missing-fields", "deep-invalid"])
def test_optional_bad_sidecar_downgrades_but_preserves_proven_local_original(tmp_path, sidecar):
    txt = write_txt(tmp_path, make_text())
    before = txt.read_bytes()
    receipt_path(txt).write_bytes(sidecar)
    verdict = verify_stored_original(txt, ticker="ACME", quarter="Q3 2026")
    assert verdict.outcome == "legacy_unverified"
    assert verdict.receipt_kind is None
    assert "receipt_invalid" in verdict.diagnostics
    assert verdict.file_sha256 == hashlib.sha256(before).hexdigest()
    assert txt.read_bytes() == before and receipt_path(txt).read_bytes() == sidecar


@pytest.mark.parametrize("pin,outcome", [
    ({"ticker": "OTHER"}, "identity_mismatch"),
    ({"fiscal_period": "2026-Q4"}, "period_mismatch"),
    ({"body_sha256": "0" * 64}, "receipt_mismatch"),
])
def test_explicit_conflicting_pin_remains_refusal_even_in_incomplete_sidecar(tmp_path, pin, outcome):
    txt = write_txt(tmp_path, make_text())
    raw = json.dumps({"schema": "future/9", **pin}).encode()
    receipt_path(txt).write_bytes(raw)
    verdict = verify_stored_original(txt, ticker="ACME", quarter="Q3 2026")
    assert verdict.outcome == outcome
    assert receipt_path(txt).read_bytes() == raw


def test_bad_note_cannot_supply_missing_header_identity(tmp_path):
    txt = write_txt(tmp_path, make_text(header={}))
    receipt_path(txt).write_bytes(b"{")
    verdict = verify_stored_original(txt, ticker="ACME", quarter="Q3 2026")
    assert verdict.outcome == "identity_missing"
    assert verdict.diagnostics


def test_actual_receipt_checksum_mismatch_never_becomes_verified(tmp_path):
    txt = write_txt(tmp_path, make_text())
    attach_receipt(txt)
    txt.write_bytes(txt.read_bytes() + b" changed original")
    before = txt.read_bytes()
    verdict = verify_stored_original(txt, ticker="ACME", quarter="Q3 2026")
    assert verdict.outcome == "receipt_mismatch" and txt.read_bytes() == before


def test_actual_cli_reuses_bad_note_original_without_llm_or_download(tmp_path, monkeypatch):
    out = tmp_path / "out"
    txt = write_txt(out / "ACME", make_text())
    note = receipt_path(txt)
    note.write_bytes(b"{broken-note")
    before = txt.read_bytes()
    monkeypatch.setattr(scraper, "load_config", lambda: {"paths": {"base_dir": str(tmp_path)}})
    def forbidden(*_args, **_kwargs):
        raise AssertionError("reuse must not translate or download")
    monkeypatch.setattr(scraper, "run_retrieval", forbidden)
    monkeypatch.setattr(scraper.TranslatorFactory, "create", forbidden)
    rc = scraper.main(["--source", "fmp", "--periods", "2026Q3", "--output", str(out)],
        _companies=[{"ticker": "ACME", "name_en": "Acme", "name_cn": "示例", "exchange": "nyse"}])
    assert rc == 0
    manifest = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    assert any(e["status"] == "legacy_unverified" and e.get("diagnostics") for e in manifest["entries"])
    assert txt.read_bytes() == before and note.read_bytes() == b"{broken-note"


@pytest.mark.parametrize("failure", [OSError, MemoryError], ids=["sidecar-io", "sidecar-memory"])
def test_optional_sidecar_capacity_failure_cannot_deny_healthy_original(tmp_path, monkeypatch, failure):
    txt = write_txt(tmp_path, make_text())
    note = receipt_path(txt)
    note.write_bytes(b"{}")
    original_read = Path.read_bytes
    def read(path):
        if path == note:
            raise failure("synthetic sidecar capacity failure")
        return original_read(path)
    monkeypatch.setattr(Path, "read_bytes", read)
    verdict = verify_stored_original(txt, ticker="ACME", quarter="Q3 2026")
    assert verdict.outcome == "legacy_unverified"
    assert verdict.diagnostics == ("receipt_unreadable",)

"""transcript_audit CLI 测试：只读默认、独立报告目录、显式补收据、真实 MSFT audit。

全部离线：0 网络、0 翻译、0 外部 LLM；对真实 transcripts 只读，收据绝不写进
源语料目录（报告一律落在 pytest 临时目录）。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from transcript_audit import main as audit_main  # noqa: E402
from transcript_artifact import (  # noqa: E402
    KIND_AUDIT_LEGACY,
    RECEIPT_SCHEMA,
    receipt_path,
)

SEP = "=" * 70
REPORT_NAME = "transcript_audit_report.json"


def make_text(header: dict | None = None, body: str = "Stored body.") -> str:
    if header is None:
        header = {
            "Company": "Acme (亚克力)",
            "Ticker": "ACME",
            "Quarter": "Q3 2026",
            "Source": "motley_fool",
            "URL": "N/A",
            "Characters": "12",
        }
    lines = "".join(
        f"{key}: {value}\n" for key, value in header.items() if value is not None
    )
    return f"{SEP}\nEarnings Call Transcript\n{lines}{SEP}\n\n{body}"


def seed(root: Path, name: str, text: str | bytes) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
    return path


def snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    return {
        str(path.relative_to(root)): (
            path.stat().st_size,
            path.stat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


# ── 只读默认 ───────────────────────────────────────────────────


def test_default_audit_is_read_only_json_to_stdout(tmp_path, capsys):
    root = tmp_path / "corpus"
    seed(root, "ACME/ACME_Q3_2026_earnings_call.txt", make_text())
    seed(
        root,
        "BAD/BAD_Q3_2026_earnings_call.txt",
        make_text(header={"Ticker": "MSFT", "Quarter": "Q3 2026"}),
    )
    seed(root, "EMP/EMP_Q3_2026_earnings_call.txt", b"")
    seed(root, "UNK/UNK_Q3_2026_earnings_call.txt", "no header here.\n")
    before = snapshot(root)

    rc = audit_main(["--root", str(root)])

    captured = capsys.readouterr()
    assert rc == 1  # identity_mismatch + empty 具名失败
    assert "audit_failures: 2" in captured.err
    report = json.loads(captured.out)
    assert report["report_schema"] == "et-local-text-audit/1"
    assert report["write_receipts"] is False
    assert report["counts"] == {
        "legacy_unverified": 1,
        "identity_mismatch": 1,
        "corrupt": 1,
        "identity_missing": 1,
    }
    assert snapshot(root) == before  # 零写入：字节/大小/mtime 全不变
    assert not list(root.rglob("*.receipt.json"))


def test_report_dir_writes_small_report_and_touches_nothing(tmp_path, capsys):
    root = tmp_path / "corpus"
    seed(root, "ACME/ACME_Q3_2026_earnings_call.txt", make_text())
    report_dir = tmp_path / "report"  # 测试前不存在：pytest tmp 结束回收
    keep = report_dir / "keep.txt"
    report_dir.mkdir()
    keep.write_text("keep me", encoding="utf-8")
    before = snapshot(root)
    keep_before = keep.read_bytes()

    rc = audit_main(["--root", str(root), "--report-dir", str(report_dir)])

    captured = capsys.readouterr()
    assert rc == 0
    assert "audit: files=1" in captured.out
    assert "report:" in captured.out
    assert captured.err == ""
    report_path = report_dir / REPORT_NAME
    assert report_path.exists()
    assert keep.read_bytes() == keep_before
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["counts"] == {"legacy_unverified": 1}
    assert report["files"][0]["file"] == "ACME/ACME_Q3_2026_earnings_call.txt".replace(
        "/", os.sep
    )
    assert len(report["files"][0]["file_sha256"]) == 64
    assert snapshot(root) == before


def test_missing_root_is_named_failure(tmp_path, capsys):
    rc = audit_main(["--root", str(tmp_path / "absent")])
    captured = capsys.readouterr()
    assert rc == 1
    assert "transcripts_root_missing" in captured.err


def test_unparsable_filename_reports_identity_missing(tmp_path, capsys):
    root = tmp_path / "corpus"
    seed(root, "X/weird_earnings_call.txt", make_text())
    rc = audit_main(["--root", str(root)])
    report = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert report["files"][0]["error_code"] == "filename_unparsed"
    assert report["files"][0]["outcome"] == "identity_missing"


# ── 显式 write-receipts 模式 ───────────────────────────────────


def test_write_receipts_adds_sidecar_without_touching_original(tmp_path, capsys):
    root = tmp_path / "corpus"
    provable = seed(root, "ACME/ACME_Q3_2026_earnings_call.txt", make_text())
    unprovable = seed(root, "UNK/UNK_Q3_2026_earnings_call.txt", "no header.\n")
    provable_before = snapshot(root)

    rc = audit_main(["--root", str(root), "--write-receipts"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "written=1" in captured.out or "legacy_unverified" in captured.out

    # 原件完全没动：字节/大小/mtime 一致
    for name, sig in provable_before.items():
        path = root / name
        assert (
            path.stat().st_size,
            path.stat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        ) == sig

    rpath = receipt_path(provable)
    receipt = json.loads(rpath.read_text(encoding="utf-8"))
    assert receipt["schema"] == RECEIPT_SCHEMA
    assert receipt["kind"] == KIND_AUDIT_LEGACY
    assert receipt["ticker"] == "ACME"
    assert (receipt["fiscal_year"], receipt["fiscal_quarter"]) == (2026, 3)
    assert receipt["obtained_at"] is None
    assert receipt["extraction_version"] is None
    assert receipt["published_date"] is None
    assert receipt["provider"] == "motley_fool"
    raw = provable.read_bytes()
    assert receipt["file_sha256"] == hashlib.sha256(raw).hexdigest()
    assert receipt["file_bytes"] == len(raw)
    assert not receipt_path(unprovable).exists()  # 证明不了不补造

    # 第二次：不覆盖已有收据
    receipt_before = rpath.read_bytes()
    rc2 = audit_main(["--root", str(root), "--write-receipts"])
    assert rc2 == 0
    assert rpath.read_bytes() == receipt_before


def test_write_receipts_never_overwrites_conflicting_receipt(tmp_path, capsys):
    root = tmp_path / "corpus"
    txt = seed(root, "ACME/ACME_Q3_2026_earnings_call.txt", make_text())
    receipt_path(txt).write_bytes(b'{"schema": "et-local-text-receipt/1", "kind": "dow')
    bad_before = receipt_path(txt).read_bytes()

    rc = audit_main(["--root", str(root), "--write-receipts"])

    assert rc == 1  # 坏收据是具名失败
    assert receipt_path(txt).read_bytes() == bad_before  # 不修、不覆盖、不删
    report = json.loads(capsys.readouterr().out)
    assert report["files"][0]["receipt_action"] == "conflict"


# ── 结构保证：0 网络 / 0 翻译 ─────────────────────────────────


def test_audit_module_imports_no_network_translator_or_scraper():
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json, sys; import transcript_audit; "
            "mods = ['requests', 'transcript_api', 'retrieval_runtime', "
            "'translator', 'scraper']; "
            "print(json.dumps({m: m in sys.modules for m in mods}))",
        ],
        cwd=str(ROOT),
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    flags = json.loads(completed.stdout.strip().splitlines()[-1])
    assert flags == {
        "requests": False,
        "transcript_api": False,
        "retrieval_runtime": False,
        "translator": False,
        "scraper": False,
    }


# ── 真实 MSFT 原语言 TXT 只读 audit ───────────────────────────


def test_real_msft_originals_are_audited_readonly(tmp_path, capsys):
    msft_dir = ROOT / "transcripts" / "MSFT"
    files = sorted(msft_dir.glob("*_earnings_call.txt"))
    if not files:
        pytest.skip("worktree has no tracked MSFT originals")
    before = snapshot(msft_dir)

    report_dir = tmp_path / "msft-report"  # 仓库外目标目录：不存在 → tmp 回收
    rc = audit_main(["--root", str(msft_dir), "--report-dir", str(report_dir)])

    captured = capsys.readouterr()
    assert rc == 0
    assert "audit_failures" not in captured.err
    assert snapshot(msft_dir) == before  # SHA/size/mtime 前后一致
    assert not list(msft_dir.glob("*.receipt.json"))  # 源语料不生成收据

    report = json.loads((report_dir / REPORT_NAME).read_text(encoding="utf-8"))
    assert report["counts"] == {"legacy_unverified": len(files)}
    spot = next(
        record
        for record in report["files"]
        if record["file"].endswith("MSFT_Q1_2025_earnings_call.txt")
    )
    assert spot["outcome"] == "legacy_unverified"
    assert spot["ticker"] == "MSFT"
    assert spot["quarter"] == "Q1 2025"
    assert spot["body_bytes"] > 0

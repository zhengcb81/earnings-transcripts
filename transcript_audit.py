#!/usr/bin/env python3
"""ET 本地原件只读 audit CLI（默认 0 写入、0 网络、0 翻译、0 外部 LLM）。

对 ``transcripts`` 下既有 ``*_earnings_call.txt`` 逐一执行与批次复用相同的
验证实现（``transcript_artifact.verify_stored_original``），输出小报告：

- 默认：只读，JSON 报告打到 stdout，不写任何文件；
- ``--report-dir DIR``：把同一份小报告原子写到独立目标目录；
- ``--write-receipts``：显式追加模式——只对“头部可证明身份+期间且正文非空、
  尚无收据”的文件写 ``audit-legacy`` 收据（记录当前字节与可证明字段，
  ``obtained_at``/``extraction_version`` 保持 null，不补造下载时来源）；
  绝不修改、删除或覆盖任何 TXT 原件，也绝不覆盖已存在的收据。

退出码：0 无具名失败；1 存在损坏/矛盾/坏收据（具名失败）或根目录缺失；2 用法错误。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from config import get_path, load_config
from transcript_artifact import (
    FAILURE_OUTCOMES,
    OUTCOME_LEGACY_UNVERIFIED,
    build_audit_receipt,
    receipt_json_bytes,
    receipt_path,
    verify_stored_original,
    write_atomic,
)

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2

REPORT_NAME = "transcript_audit_report.json"
REPORT_SCHEMA = "et-local-text-audit/1"


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ET 本地原件只读 audit（与批次复用同一验证实现；默认零写入）"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="被审目录（默认 config 的 transcripts_dir）",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=None,
        help="把小报告原子写到该独立目录（缺省时 JSON 打到 stdout）",
    )
    parser.add_argument(
        "--write-receipts",
        action="store_true",
        help="显式为可证明且尚无收据的原件补 audit-legacy 收据（只加 sidecar，绝不改 TXT）",
    )
    return parser


def _expected_from_name(name: str, suffix: str, ext: str) -> tuple[str, str] | None:
    """``ACME_Q3_2026_earnings_call.txt`` → ``("ACME", "Q3 2026")``；无法解析 → None。"""
    pattern = re.compile(
        rf"^(?P<ticker>[^_]+)_(?P<q>Q[1-4])_(?P<y>20\d{{2}})"
        rf"{re.escape(suffix)}{re.escape(ext)}$"
    )
    match = pattern.match(name)
    if match is None:
        return None
    return match.group("ticker"), f"{match.group('q')} {match.group('y')}"


def audit_files(
    root: Path,
    *,
    cfg: dict,
    suffix: str,
    ext: str,
    write_receipts: bool,
) -> list[dict]:
    records: list[dict] = []
    for path in sorted(root.rglob(f"*{suffix}{ext}")):
        record: dict = {
            "file": str(path.relative_to(root)),
            "outcome": None,
            "error_code": None,
            "ticker": None,
            "quarter": None,
            "body_bytes": None,
            "file_bytes": None,
            "file_sha256": None,
            "receipt_kind": None,
        }
        expected = _expected_from_name(path.name, suffix, ext)
        if expected is None:
            record["outcome"] = "identity_missing"
            record["error_code"] = "filename_unparsed"
            records.append(record)
            continue
        ticker, label = expected
        record["ticker"] = ticker
        record["quarter"] = label
        verdict = verify_stored_original(path, ticker=ticker, quarter=label, cfg=cfg)
        record["outcome"] = verdict.outcome
        record["error_code"] = verdict.error_code
        record["body_bytes"] = verdict.body_bytes
        record["file_bytes"] = verdict.file_bytes
        record["file_sha256"] = verdict.file_sha256
        record["receipt_kind"] = verdict.receipt_kind
        if write_receipts:
            record["receipt_action"] = _receipt_action(path, verdict)
        records.append(record)
    return records


def _receipt_action(path: Path, verdict) -> str:
    if receipt_path(path).exists():
        return "conflict" if verdict.outcome in FAILURE_OUTCOMES else "exists"
    if verdict.outcome != OUTCOME_LEGACY_UNVERIFIED:
        return "skipped"
    try:
        write_atomic(
            receipt_path(path),
            receipt_json_bytes(build_audit_receipt(verdict)),
        )
    except OSError:
        return "failed"
    return "written"


def build_report(root: Path, records: list[dict], write_receipts: bool) -> dict:
    counts: dict[str, int] = {}
    for record in records:
        counts[record["outcome"]] = counts.get(record["outcome"], 0) + 1
    return {
        "report_schema": REPORT_SCHEMA,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "root": str(root),
        "write_receipts": bool(write_receipts),
        "counts": counts,
        "files": records,
    }


def _ensure_utf8_stdout() -> None:
    """JSON 报告含中文路径：管道/重定向输出固定为 UTF-8（已是 UTF-8 时不动）。"""
    if (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "") == "utf8":
        return
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdout()
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    try:
        cfg = load_config()
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    root = (args.root or get_path(cfg, "transcripts_dir")).resolve()
    if not root.is_dir():
        print(f"error: transcripts_root_missing: {root}", file=sys.stderr)
        return EXIT_FAILURE

    naming = cfg.get("naming", {})
    suffix = naming.get("english_suffix", "_earnings_call")
    ext = naming.get("english_ext", ".txt")
    records = audit_files(
        root, cfg=cfg, suffix=suffix, ext=ext, write_receipts=args.write_receipts
    )
    report = build_report(root, records, args.write_receipts)
    failures = sum(1 for record in records if record["outcome"] in FAILURE_OUTCOMES)

    if args.report_dir is not None:
        report_path = args.report_dir.resolve() / REPORT_NAME
        write_atomic(
            report_path,
            json.dumps(report, ensure_ascii=False, indent=1).encode("utf-8"),
        )
        summary = "  ".join(
            f"{key}={value}" for key, value in sorted(report["counts"].items())
        )
        print(f"audit: files={len(records)}  {summary}")
        print(f"report: {report_path}")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    if failures:
        print(
            f"error: audit_failures: {failures} file(s) named-failed", file=sys.stderr
        )
        return EXIT_FAILURE
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())

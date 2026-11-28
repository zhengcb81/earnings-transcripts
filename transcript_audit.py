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

import json
import re
import sys
from datetime import datetime
from pathlib import Path

from config import get_path, load_config
import transcript_feedback
from runtime_logging import EntryDiagnostics
from runtime_scope import redact_text
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


def build_argument_parser() -> transcript_feedback.RefusalCapturingParser:
    parser = transcript_feedback.RefusalCapturingParser(
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
    runtime: EntryDiagnostics | None = None,
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
        if verdict.diagnostics:
            record["diagnostics"] = list(verdict.diagnostics)
        if write_receipts:
            try:
                record["receipt_action"] = _receipt_action(path, verdict)
            except OSError as error:
                transcript_feedback.report_entry_failure(
                    "transcript_audit", "receipt-write", error,
                    runtime=runtime, argument=verdict.file_sha256,
                )
                record["receipt_action"] = "failed"
        records.append(record)
    return records


def _receipt_action(path: Path, verdict) -> str:
    if receipt_path(path).exists():
        return "conflict" if verdict.outcome in FAILURE_OUTCOMES else "exists"
    if verdict.outcome != OUTCOME_LEGACY_UNVERIFIED:
        return "skipped"
    write_atomic(
        receipt_path(path),
        receipt_json_bytes(build_audit_receipt(verdict)),
    )
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


@transcript_feedback.observed_entry("transcript_audit")
def main(argv: list[str] | None = None, *, runtime: EntryDiagnostics) -> int:
    _ensure_utf8_stdout()
    # 普通启动在参数解析与副作用之前建立本次身份：根缺失、配置缺失与逐文件
    # 失败都按本次 ID 可查；导出失败绝不改变业务原返回。
    recorder = transcript_feedback.recorder_from_env(identity=runtime.identity)
    parser = build_argument_parser()
    try:
        try:
            args = parser.parse_args(argv)
        except transcript_feedback.InvocationRefusal as refusal:
            return transcript_feedback.refuse_invocation(
                parser,
                refusal,
                recorder,
                entry="transcript_audit",
                stage="argument-parsing",
                refusal_code="invalid_cli_arguments",
                location="transcript_audit.py#parse_args",
                runtime=runtime,
            )
        runtime.stage = "config-load"
        try:
            cfg = load_config()
        except FileNotFoundError as exc:
            transcript_feedback.emit_invocation_refusal(
                recorder,
                entry="transcript_audit",
                stage="config-load",
                refusal_code="config_unavailable",
                location="transcript_audit.py#main",
                runtime=runtime,
                error=exc,
            )
            sys.stderr.write(f"error: {redact_text(str(exc))}\n")
            return EXIT_FAILURE
        runtime.stage = "input-resolution"
        root = (args.root or get_path(cfg, "transcripts_dir")).resolve()
        if not root.is_dir():
            transcript_feedback.emit_invocation_refusal(
                recorder,
                entry="transcript_audit",
                stage="input-resolution",
                refusal_code="input_root_missing",
                location="transcript_audit.py#main",
                runtime=runtime,
            )
            sys.stderr.write(f"error: transcripts_root_missing: {redact_text(str(root))}\n")
            return EXIT_FAILURE

        naming = cfg.get("naming", {})
        suffix = naming.get("english_suffix", "_earnings_call")
        ext = naming.get("english_ext", ".txt")
        runtime.begin("stored-original-audit")
        records = audit_files(
            root, cfg=cfg, suffix=suffix, ext=ext, write_receipts=args.write_receipts, runtime=runtime
        )
        transcript_feedback.emit_stored_original_audit(recorder, records)
        report = build_report(root, records, args.write_receipts)
        failures = sum(1 for record in records if record["outcome"] in FAILURE_OUTCOMES)

        if args.report_dir is not None:
            runtime.stage = "report-write"
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
    finally:
        transcript_feedback.finalize_delivery(recorder)


if __name__ == "__main__":
    raise SystemExit(main())

"""CLI for the standard-library S6 reference implementation."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .evidence import baseline, changed_inventory, compare, coverage, renamed_waivers, review_errors, test_records
from .static_scan import scan


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read JSON evidence: {type(error).__name__}") from None


def _emit(value):
    print(json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2))


def _scan(args):
    root = args.root.resolve(strict=True)
    result = scan(root, args.source, args.storage, args.machine_entry, args.log_context)
    recorded = {
        **baseline(result.findings),
        "source_files": list(result.files),
        "constant_shapes": result.constant_shapes,
    }
    if args.command == "baseline":
        _emit(recorded)
        return 0
    if args.baseline:
        before = _read(args.baseline)
        new = compare(recorded, before) + renamed_waivers(recorded, before)
        omitted = changed_inventory(result, before, root)
    else:
        new, omitted = result.findings, ()
    _emit({
        "scope": "source_ast",
        "source_files": result.files,
        "issues": [asdict(item) for item in result.findings],
        "new_issues": [asdict(item) for item in new],
        "omitted_existing_sources": omitted,
        "metrics_only_at_09": result.metrics,
        "cycles": result.cycles,
        "ci_execution_evidence": "run the separate coverage command",
    })
    return int(args.strict and bool(new or omitted))


def _coverage(args):
    expected, executed = test_records(
        args.revision, [_read(path) for path in args.evidence], _read(args.policy)
    )
    findings = coverage(expected, executed, required=args.platform)
    _emit({
        "scope": "pytest_execution",
        "revision": args.revision,
        "collected_by_platform": {key: len(value) for key, value in expected.items()},
        "issues": [asdict(item) for item in findings],
    })
    return int(bool(findings))


def _review(args):
    receipt = _read(args.document)
    problems = review_errors(receipt, args.revision, root=args.root)
    _emit({"valid": not problems, "errors": problems})
    return int(bool(problems))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    choices = parser.add_subparsers(dest="command", required=True)
    for name in ("scan", "baseline"):
        command = choices.add_parser(name)
        command.add_argument("--root", type=Path, default=Path.cwd())
        command.add_argument("--source", action="append", required=True, help="Repository-relative Python source directory or file")
        command.add_argument("--storage", action="append", default=[], help="Repository-relative path of an existing storage owner")
        command.add_argument("--machine-entry", action="append", default=[], help="Source file whose stdout is a machine protocol")
        command.add_argument("--log-context", action="append", default=[], help="Qualified path:name of a logging-only ContextVar")
        if name == "scan":
            command.add_argument("--baseline", type=Path, help="Versioned baseline from the unchanged integration target")
            command.add_argument("--strict", action="store_true", help="Fail when the scan contains new findings or narrows the baseline's source inventory")
    command = choices.add_parser("coverage")
    command.add_argument("--revision", required=True)
    command.add_argument("--platform", action="append", required=True)
    command.add_argument("--policy", type=Path, required=True, help="Reviewed canonical test roots and markers")
    command.add_argument("evidence", nargs="+")
    command = choices.add_parser("review")
    command.add_argument("--revision", required=True)
    command.add_argument("--root", type=Path)
    command.add_argument("document")
    args = parser.parse_args()
    handler = {"scan": _scan, "baseline": _scan, "coverage": _coverage, "review": _review}[args.command]
    try:
        return handler(args)
    except (OSError, ValueError) as error:
        message = str(error) if isinstance(error, ValueError) else type(error).__name__
        print(f"Cannot evaluate evidence: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

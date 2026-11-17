"""Import feedback observations into this repository's issue cards.

One adjudicated cause, one card. Observations that carry the same
``issue_key`` land on the same card even when they come from different
companies, periods or runs. Importing the same observation twice adds
nothing: an occurrence is identified by its run, attempt, step and check.

    python tools/feedback_issue_cards.py --import docs/.../samples/x.jsonl
    python tools/feedback_issue_cards.py --list

The card body is authored by the owner; the tool only rewrites the
occurrence table between its markers and appends to the machine-readable
occurrence log beside it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CARDS_DIR = ROOT / "docs" / "plans" / "feedback-loop-2026-11-17" / "issues"

BEGIN_MARKER = "<!-- occurrences:begin -->"
END_MARKER = "<!-- occurrences:end -->"

CARD_TEMPLATE = """# {card_id} · {issue_key}

- issue_key: `{issue_key}`
- status: open
- owner: earnings-transcripts
- first_seen: {first_seen}
- evidence_class: unclassified

## Expected and actual

- expected: {expected}
- actual: {actual}
- disposition: {disposition}

## Impact

Not yet described.

## Occurrences

{BEGIN_MARKER}
| run_id | attempt_id | step_id | check_id | expected | actual | disposition | evidence_ref |
| --- | --- | --- | --- | --- | --- | --- | --- |
{rows}
{END_MARKER}

## Reproduction

The named fixture of this repository reproduces this cause offline; the
controlled replay entry never contacts a provider.

## Ownership and root cause

- responsible layer: unassigned
- proven: unassigned
- unproven: unassigned

## Fix

- commit: none
- regression: none

## Consumer re-verification

- state: pending
"""

__all__ = ["import_observations", "list_cards", "main"]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if isinstance(record, dict):
            records.append(record)
    return records


def _occurrence_key(record: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(record.get("run_id", "")),
        str(record.get("attempt_id", "")),
        str(record.get("step_id", "")),
        str(record.get("check_id", "")),
    )


def _card_path(cards_dir: Path, issue_key: str) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", issue_key.lower()).strip("-")
    return cards_dir / f"{slug}.md"


def _occurrences_path(cards_dir: Path, issue_key: str) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", issue_key.lower()).strip("-")
    return cards_dir / f"{slug}.occurrences.jsonl"


def _next_card_id(cards_dir: Path) -> str:
    used = []
    for path in sorted(cards_dir.glob("*.md")) if cards_dir.is_dir() else []:
        match = re.search(r"ET-FB-(\d+)", path.read_text(encoding="utf-8")[:200])
        if match:
            used.append(int(match.group(1)))
    return f"ET-FB-{max(used) + 1:03d}" if used else "ET-FB-001"


def _render_rows(occurrences: list[dict[str, Any]]) -> str:
    rows = []
    for record in occurrences:
        rows.append(
            "| {run_id} | {attempt_id} | {step_id} | {check_id} | {expected} | {actual} | {disposition} | {evidence_ref} |".format(
                run_id=record.get("run_id", ""),
                attempt_id=record.get("attempt_id", ""),
                step_id=record.get("step_id", ""),
                check_id=record.get("check_id", ""),
                expected=record.get("expected", ""),
                actual=record.get("actual", ""),
                disposition=record.get("disposition", ""),
                evidence_ref=record.get("evidence_ref", ""),
            )
        )
    return "\n".join(rows)


def _replace_block(text: str, rows: str) -> str:
    pattern = re.compile(
        re.escape(BEGIN_MARKER) + r".*?" + re.escape(END_MARKER), re.DOTALL
    )
    block = f"{BEGIN_MARKER}\n{rows}\n{END_MARKER}"
    if pattern.search(text):
        return pattern.sub(block, text)
    return text.rstrip("\n") + "\n\n" + block + "\n"


def import_observations(
    observations_path: Path, *, cards_dir: Path = DEFAULT_CARDS_DIR
) -> dict[str, Any]:
    """Attach every adjudicated deviation to its own repository issue card."""
    records = _read_jsonl(observations_path)
    deviations = [
        record
        for record in records
        if record.get("disposition") == "deviation" and record.get("issue_key")
    ]
    cards_dir.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {"added": 0, "skipped": 0, "cards": {}}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in deviations:
        grouped.setdefault(str(record["issue_key"]), []).append(record)
    for issue_key in sorted(grouped):
        card_path = _card_path(cards_dir, issue_key)
        log_path = _occurrences_path(cards_dir, issue_key)
        known = {
            tuple(record.get("_key", ()))
            for record in _read_jsonl(log_path)
            if record.get("_key")
        }
        kept: list[dict[str, Any]] = []
        added = 0
        for record in grouped[issue_key]:
            key = _occurrence_key(record)
            if key in known:
                summary["skipped"] += 1
                continue
            known.add(key)
            kept.append(record)
            added += 1
        if not kept:
            summary["cards"][issue_key] = {"card": card_path.name, "added": 0}
            continue
        occurrences = _read_jsonl(log_path)
        with log_path.open("a", encoding="utf-8", newline="\n") as handle:
            for record in kept:
                entry = dict(record)
                entry["_key"] = list(_occurrence_key(record))
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
                occurrences.append(record)
        if not card_path.is_file():
            first = kept[0]
            card_path.write_text(
                CARD_TEMPLATE.format(
                    card_id=_next_card_id(cards_dir),
                    issue_key=issue_key,
                    first_seen=f"{first.get('run_id')}/{first.get('attempt_id')}/{first.get('step_id')}",
                    expected=first.get("expected", ""),
                    actual=first.get("actual", ""),
                    disposition=first.get("disposition", ""),
                    BEGIN_MARKER=BEGIN_MARKER,
                    END_MARKER=END_MARKER,
                    rows=_render_rows(occurrences),
                ),
                encoding="utf-8",
                newline="\n",
            )
        else:
            text = card_path.read_text(encoding="utf-8")
            card_path.write_text(
                _replace_block(text, _render_rows(occurrences)), encoding="utf-8", newline="\n"
            )
        summary["added"] += added
        summary["cards"][issue_key] = {"card": card_path.name, "added": added}
    return summary


def list_cards(*, cards_dir: Path = DEFAULT_CARDS_DIR) -> list[dict[str, Any]]:
    cards = []
    for path in sorted(cards_dir.glob("*.md")) if cards_dir.is_dir() else []:
        text = path.read_text(encoding="utf-8")
        status = re.search(r"- status: (\w+)", text)
        issue_key = re.search(r"- issue_key: `([^`]+)`", text)
        cards.append(
            {
                "file": path.name,
                "issue_key": issue_key.group(1) if issue_key else None,
                "status": status.group(1) if status else None,
                "occurrences": len(_read_jsonl(_occurrences_path(cards_dir, (issue_key.group(1) if issue_key else "")))),
            }
        )
    return cards


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Attach feedback observations to this repository's issue cards."
    )
    parser.add_argument("--import", dest="import_path", help="Observation sidecar (JSONL).")
    parser.add_argument("--cards-dir", default=str(DEFAULT_CARDS_DIR))
    parser.add_argument("--list", action="store_true", help="List the issue cards.")
    args = parser.parse_args(argv)
    cards_dir = Path(args.cards_dir)
    if args.list:
        print(json.dumps(list_cards(cards_dir=cards_dir), ensure_ascii=False, indent=2))
        return 0
    if not args.import_path:
        parser.error("--import is required")
    summary = import_observations(Path(args.import_path), cards_dir=cards_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

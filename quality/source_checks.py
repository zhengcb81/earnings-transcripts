"""Run the S6 source-inventory check over the ET owner boundary."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

_STORAGE_OWNER = "tools/feedback_owner.py"
_MACHINE_ENTRY_POINTS = (
    "transcript_tool.py",
    "tools/feedback_owner.py",
    "tools/replay_feedback_case.py",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--record-baseline", action="store_true")
    settings = parser.parse_args()
    root = settings.root.resolve(strict=True)
    mode = "baseline" if settings.record_baseline else "scan"
    command = [sys.executable, "-B", "-m", "quality.anti_trick_gate_spec", mode, "--root", str(root)]
    for source in sorted(root.glob("*.py")):
        command.extend(("--source", source.name))
    command.extend(("--source", "tools", "--storage", _STORAGE_OWNER))
    for source in _MACHINE_ENTRY_POINTS:
        command.extend(("--machine-entry", source))
    if not settings.record_baseline:
        command.extend(("--strict", "--baseline", str(root / "quality" / "ratchet_baseline.json")))
    result = subprocess.run(
        command, check=False, stdin=subprocess.DEVNULL, capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    sys.stdout.buffer.write(result.stdout)
    sys.stderr.buffer.write(result.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())

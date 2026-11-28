"""ET retrieval facts shared by the supervisor, worker and read-only owner port."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread
from typing import Any

from retrieval_process import kill_worker_group
from retrieval_protocol import REQUEST_FILENAME, RESULT_FILENAME, WORKER_ERROR_FILENAME
from runtime_logging import append_failure
from runtime_scope import OWNER, run_identity, safe_public_word, state_root, step_id, storage_path

TRACE_ENV = "ET_INTERNAL_RETRIEVAL_TRACE"
IMPLEMENTATION = "earnings-transcripts-retrieval/1"
_LOG = "runtime_retrieval.log"
_WORK_FILES = (REQUEST_FILENAME, RESULT_FILENAME, WORKER_ERROR_FILENAME,
               RESULT_FILENAME + ".tmp", WORKER_ERROR_FILENAME + ".tmp")


@dataclass(frozen=True)
class RetrievalTrace:
    run_id: str
    attempt_id: str
    step_id: str
    operation: str

    @classmethod
    def create(cls, operation: str, request: Any, identity: tuple[str, str] | None = None) -> RetrievalTrace:
        run_id, attempt_id = identity or run_identity()
        return cls(safe_public_word(run_id, fallback="run") or "run",
                   safe_public_word(attempt_id, fallback="attempt") or "attempt",
                   step_id(request, operation), operation)

    def environment(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=True)

    @classmethod
    def from_environment(cls) -> RetrievalTrace | None:
        raw = os.environ.get(TRACE_ENV)
        if not raw:
            return None
        try:
            values = json.loads(raw)
            if not isinstance(values, dict) or set(values) != {"run_id", "attempt_id", "step_id", "operation"}:
                return None
            if not all(isinstance(value, str) and value for value in values.values()):
                return None
            return cls(**values)
        except (ValueError, TypeError):
            return None

    def emit(self, phase: str, **facts: Any) -> bool:
        root = state_root()
        if root is None:
            return False
        return append_failure(storage_path(root / "logs" / _LOG), {
            **asdict(self),
            "owner": OWNER,
            "implementation": IMPLEMENTATION,
            "at": datetime.now(timezone.utc).isoformat(),
            "phase": phase,
            **facts,
        })


def read_history(run_id: str, attempt_id: str, step: str | None = None) -> dict[str, Any]:
    """Read retained lifecycle rows without starting delivery, a provider or a database."""
    root = state_root()
    if root is None:
        return {"status": "unavailable", "events": []}
    run_id = safe_public_word(run_id, fallback="run")
    attempt_id = safe_public_word(attempt_id, fallback="attempt")
    step = safe_public_word(step, fallback="request")
    events = []
    unavailable = False
    logs = (_LOG, "runtime_errors.log") if step is None else (_LOG,)
    for name in (log + suffix for log in logs for suffix in (".2", ".1", "")):
        try:
            with storage_path(root / "logs" / name).open("rb") as stream:
                for line in stream:
                    try:
                        row = json.loads(line)
                    except (ValueError, RecursionError):
                        continue
                    if (isinstance(row, dict) and row.get("owner") == OWNER
                            and row.get("run_id") == run_id and row.get("attempt_id") == attempt_id
                            and (step is None or row.get("step_id") == step)):
                        if name.startswith("runtime_errors.log"):
                            if row.get("event") not in {"invocation_failure", "invocation_refusal", "translation_failure"}:
                                continue
                            row["phase"] = row["event"]
                        events.append(row)
        except FileNotFoundError:
            continue
        except OSError:
            unavailable = True
    events.sort(key=lambda row: row.get("at", ""))
    return {"status": "found" if events else "unavailable" if unavailable else "absent", "events": events}


def _remove_staged_files(request_path: Path) -> str | None:
    """Remove only this worker's known temporary files after loss of its parent."""
    try:
        workdir = request_path.parent.absolute()
        if (request_path.name != REQUEST_FILENAME or not workdir.name.startswith("et-retrieval-")
                or workdir.resolve(strict=True) != workdir):
            return "unverified_workdir"
        for name in _WORK_FILES:
            (workdir / name).unlink(missing_ok=True)
        workdir.rmdir()
    except FileNotFoundError:
        return None
    except OSError as error:
        return type(error).__name__
    return None


class WorkerLifetime:
    """The parent owns the write end of this process lease for its entire lifetime."""

    def __init__(self, request_path: Path, trace: RetrievalTrace) -> None:
        self.trace = trace
        self.activity = "not_started"
        self.usage: dict[str, Any] | None = None
        Thread(target=self._watch, args=(request_path,), daemon=True).start()

    def _watch(self, request_path: Path) -> None:
        try:
            try:
                # The lease carries no data. EOF is independent of provider I/O.
                os.read(sys.stdin.fileno(), 1)
            except (AttributeError, OSError, ValueError):
                pass
            cleanup = _remove_staged_files(request_path)
            self.trace.emit("supervisor_disconnected", provider_activity=self.activity,
                            usage=self.usage, cleanup_error=cleanup)
        finally:
            try:
                if os.name != "nt" and os.getpgrp() == os.getpid():
                    kill_worker_group(os.getpid())
            finally:
                os._exit(1)

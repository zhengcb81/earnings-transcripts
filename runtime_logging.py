"""Credential-safe local diagnostics shared by ET's runtime entries."""

from __future__ import annotations

import argparse
import atexit
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from copy import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
from time import monotonic, sleep
from typing import Mapping

from lock import SingleInstanceLock
from retrieval_diagnostics import exception_trace
from runtime_scope import OWNER, redact_diagnostic_tree, redact_text, run_identity, safe_public_word, state_root, storage_path

_ROTATION_BYTES = 2 * 1024 * 1024
_LOCK_SECONDS = 1.0
FailureReporter = Callable[[str, BaseException], None]


@dataclass
class EntryDiagnostics:
    entry: str
    identity: tuple[str, str] = field(default_factory=run_identity)
    stage: str = "argument-parsing"
    warnings: set[tuple[str, str]] = field(default_factory=set)
    published_warnings: set[tuple[str, str]] = field(default_factory=set)
    handlers: list[logging.Handler] = field(default_factory=list)

    def begin(self, stage: str, *, persist: bool = True) -> None:
        """Record actual work, leaving legitimate read-only modes alone."""
        self.stage = stage
        if persist:
            self.record("invocation_started", {"entry": self.entry, "stage": self.stage})

    def record(self, event: str, facts: Mapping[str, object]) -> bool:
        return record_local_failure(event, facts, identity=self.identity, report=self.write_failed)

    def write_failed(self, kind: str, error: BaseException) -> None:
        self.warnings.add((kind, type(error).__name__))

    def flush_warnings(self) -> None:
        for kind, exception_type in sorted(self.warnings - self.published_warnings):
            warn_optional_failure(kind, exception_type)
        self.published_warnings.update(self.warnings)


@contextmanager
def track_entry(entry: str) -> Iterator[EntryDiagnostics]:
    root = logging.getLogger()
    previous_level = root.level
    diagnostics = EntryDiagnostics(entry)
    try:
        yield diagnostics
    finally:
        for handler in diagnostics.handlers:
            root.removeHandler(handler)
            handler.close()
        if diagnostics.handlers:
            root.setLevel(previous_level)


class DiagnosticArgumentParser(argparse.ArgumentParser):
    """Preserve native usage output without echoing credential-shaped tokens."""

    def error(self, message: str) -> None:
        super().error(redact_text(message))


def warn_optional_failure(kind: str, exception_type: str) -> None:
    """Warn on human stderr without exposing paths or exception text."""
    if sys.stderr is None:
        return
    try:
        sys.stderr.write(f"warning: {kind} ({exception_type})\n")
    except (OSError, ValueError):
        return


def _warn_on_stderr(kind: str, error: BaseException) -> None:
    warn_optional_failure(kind, type(error).__name__)


class SafeFormatter(logging.Formatter):
    """Keep ordinary messages and structural trace facts, omitting exception values."""

    def format(self, record: logging.LogRecord) -> str:
        safe = copy(record)
        safe.exc_text = None
        return redact_text(super().format(safe))

    def formatException(self, ei) -> str:
        return json.dumps(exception_trace(ei[1]), ensure_ascii=True) if ei[1] is not None else ""


@contextmanager
def _write_lock(path: Path) -> Iterator[None]:
    lock = SingleInstanceLock(path.with_suffix(".lock"))
    deadline = monotonic() + _LOCK_SECONDS
    try:
        while True:
            acquired, _ = lock.acquire()
            if acquired:
                break
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError("ET diagnostic writer lock busy")
            sleep(min(0.02, remaining))
        yield
    finally:
        lock.release()
        atexit.unregister(lock.release)


class _OwnerFileHandler(RotatingFileHandler):
    failed = False
    report: FailureReporter | None = None

    def doRollover(self) -> None:
        try:
            super().doRollover()
        except PermissionError as error:
            # A Windows reader can deny rename while allowing append.
            if self.report is not None:
                self.report("et_log_rotation_deferred", error)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            with _write_lock(Path(self.baseFilename)):
                try:
                    super().emit(record)
                finally:
                    # An open stream would prevent a sibling Windows writer
                    # from renaming this file after taking the same lock.
                    self.close()
        except (OSError, RuntimeError, TypeError, ValueError):
            self.handleError(record)

    def handleError(self, record: logging.LogRecord) -> None:
        error = sys.exc_info()[1]
        if error is not None and not self.failed and self.report is not None:
            self.report("et_log_write_failed", error)
        self.failed = True


def rotating_file_handler(
    path: Path, *, report: FailureReporter | None,
) -> _OwnerFileHandler:
    path = storage_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = _OwnerFileHandler(
        path, maxBytes=_ROTATION_BYTES, backupCount=2, encoding="utf-8",
        delay=True, errors="backslashreplace",
    )
    handler.report = report
    return handler


def configure_run_logging(
    settings: Mapping[str, object], path: Path | None = None, *,
    runtime: EntryDiagnostics | None = None,
) -> logging.Logger:
    """Route ET modules through one scoped formatter and file transaction."""
    root = logging.getLogger()
    fmt = settings.get("format")
    formatter = SafeFormatter(fmt if isinstance(fmt, str) else "%(asctime)s [%(levelname)s] %(message)s")
    if (runtime is None and root.handlers) or (runtime is not None and runtime.handlers):
        return root
    root.setLevel(getattr(logging, str(settings.get("level", "INFO")).upper(), logging.INFO))
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    handlers: list[logging.Handler] = [stream]
    report = runtime.write_failed if runtime is not None else _warn_on_stderr
    if path is not None:
        try:
            file = rotating_file_handler(path, report=report)
            file.setFormatter(formatter)
            handlers.append(file)
        except (OSError, ValueError) as error:
            report("et_log_setup_failed", error)
    for handler in handlers:
        root.addHandler(handler)
    if runtime is not None:
        runtime.handlers.extend(handlers)
    return root


def append_failure(
    path: Path, payload: Mapping[str, object], *, report: FailureReporter | None = None,
) -> bool:
    """Persist one diagnostic using the same transaction as text log writers."""
    try:
        with closing(rotating_file_handler(path, report=report)) as handler:
            handler.handle(logging.makeLogRecord({
                "name": OWNER, "levelno": logging.ERROR, "levelname": "ERROR",
                "msg": json.dumps(redact_diagnostic_tree(dict(payload)), ensure_ascii=True),
            }))
            return not handler.failed
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        if report is not None:
            report("et_log_write_failed", error)
        return False


def record_local_failure(
    event: str, facts: Mapping[str, object], *, identity: tuple[str, str] | None = None,
    report: FailureReporter | None = None,
) -> bool:
    """Persist owner facts independently of F4 delivery."""
    root = state_root()
    if root is None:
        if report is not None:
            report("et_log_root_unavailable", OSError("no resolved ET state root"))
        return False
    run_id, attempt_id = identity or run_identity()
    return append_failure(root / "logs" / "runtime_errors.log", {
        "at": datetime.now(timezone.utc).isoformat(),
        "owner": OWNER,
        "run_id": safe_public_word(run_id, fallback="run"),
        "attempt_id": safe_public_word(attempt_id, fallback="attempt"),
        "event": event,
        "facts": dict(facts),
    }, report=report)

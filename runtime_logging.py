"""Local rotating logs shared by the public ET command boundaries."""

from __future__ import annotations

import atexit
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
from typing import Mapping

from lock import SingleInstanceLock


def warn_optional_failure(kind: str, exception_type: str) -> None:
    """Warn on a human stderr stream without exposing paths or exception text."""
    if sys.stderr is None:
        return
    try:
        print(f"warning: {kind} ({exception_type})", file=sys.stderr)
    except (OSError, ValueError):
        pass


class _OptionalFileHandler(RotatingFileHandler):
    failed = False
    warn_on_failure = False

    def handleError(self, record: logging.LogRecord) -> None:
        if self.warn_on_failure and not self.failed:
            warn_optional_failure("et_log_write_failed", type(sys.exc_info()[1]).__name__)
        self.failed = True


def rotating_file_handler(path: Path, *, warn_on_failure: bool = True) -> _OptionalFileHandler:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = _OptionalFileHandler(
        path, maxBytes=2 * 1024 * 1024, backupCount=2,
        encoding="utf-8", delay=True, errors="backslashreplace",
    )
    handler.warn_on_failure = warn_on_failure
    return handler


def append_failure(path: Path, payload: Mapping[str, object]) -> bool:
    """Write one ET-owned structural record without changing ambient loggers."""
    lock = SingleInstanceLock(path.with_suffix(".lock"))
    try:
        with lock:
            handler = rotating_file_handler(path, warn_on_failure=False)
            try:
                handler.handle(logging.makeLogRecord({
                    "name": "earnings-transcripts", "levelno": logging.ERROR,
                    "levelname": "ERROR", "msg": json.dumps(payload, ensure_ascii=True),
                }))
                return not handler.failed
            finally:
                handler.close()
    except (OSError, RuntimeError, ValueError):
        return False
    finally:
        atexit.unregister(lock.release)

"""Project exception locations and causality without formatting raw errors."""

from __future__ import annotations

import builtins
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import sha256
import json
import logging
from pathlib import PureWindowsPath
import re
from threading import Thread
from typing import BinaryIO, TypedDict


class Frame(TypedDict):
    file: str
    line: int
    function: str


class ExceptionFact(TypedDict):
    type: str
    relation: str
    frames: list[Frame]


_TYPE = re.compile(r"[A-Za-z_][A-Za-z_0-9]{0,127}")
_FILE = re.compile(r"(?:[A-Za-z_0-9][A-Za-z_0-9.-]{0,127}|<frozen [A-Za-z_][A-Za-z_0-9.]{0,118}>)")
_FUNCTION = re.compile(r"(?:[A-Za-z_][A-Za-z_0-9]{0,127}|<[a-z]+>)")
_RELATIONS = {"raised", "cause", "context", "cleanup"}
_BUILTIN_ERRORS = {
    name.encode("ascii"): name
    for name, value in vars(builtins).items()
    if isinstance(value, type) and issubclass(value, BaseException)
}
_DIAGNOSTIC_WRITE_PREFIX = b"ET worker diagnostic write failed: "
_TYPED_WORKER_PREFIX = b"ET worker exception fact: "
_TRACEBACK_HEADER = b"Traceback (most recent call last):"
_NATIVE_FAILURE_PREFIXES = (b"Fatal Python error:", b"Windows fatal exception:")
_capture: ContextVar[list[ExceptionFact] | None] = ContextVar(
    "retrieval_provider_failure", default=None,
)


def _matches(pattern: re.Pattern, value: object) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def safe_exception_types(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [name for name in value if _matches(_TYPE, name)]


def read_exception_trace(value: object) -> list[ExceptionFact]:
    """Decode only the approved fields of a child-process diagnostic."""
    if not isinstance(value, list):
        return []
    facts = []
    for item in value:
        if not isinstance(item, dict) or not _matches(_TYPE, item.get("type")):
            continue
        relation = item.get("relation")
        if not isinstance(relation, str) or relation not in _RELATIONS:
            continue
        frames = []
        for frame in item.get("frames") if isinstance(item.get("frames"), list) else []:
            if not isinstance(frame, dict):
                continue
            file, line, function = frame.get("file"), frame.get("line"), frame.get("function")
            if (_matches(_FILE, file) and type(line) is int and line > 0
                    and _matches(_FUNCTION, function)):
                frames.append(Frame(file=file, line=line, function=function))
        facts.append(ExceptionFact(type=item["type"], relation=relation, frames=frames))
    return facts


def exception_trace(error: BaseException) -> list[ExceptionFact]:
    """Preserve the exception types and frame locations, never args or source lines."""
    facts: list[ExceptionFact] = []
    seen = set()
    pending = [(error, "raised")]
    while pending:
        current, relation = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        frames = []
        trace = BaseException.__traceback__.__get__(current)
        while trace is not None:
            code = trace.tb_frame.f_code
            file, function = PureWindowsPath(code.co_filename).name, code.co_name
            if _matches(_FILE, file) and _matches(_FUNCTION, function):
                frames.append(Frame(file=file, line=trace.tb_lineno, function=function))
            trace = trace.tb_next
        name = type(current).__name__
        facts.append(ExceptionFact(
            type=name if _matches(_TYPE, name) else "BaseException",
            relation=relation,
            frames=frames,
        ))
        cause = BaseException.__cause__.__get__(current)
        context = BaseException.__context__.__get__(current)
        if context is not None and context is not cause:
            pending.append((context, "context"))
        if cause is not None:
            pending.append((cause, "cause"))
    return facts


def worker_trace_line(fact: ExceptionFact) -> str:
    """Private pipe framing when the worker cannot persist its full envelope."""
    terminal = {**fact, "frames": fact["frames"][-1:]}
    return _TYPED_WORKER_PREFIX.decode("ascii") + json.dumps(terminal, ensure_ascii=True)


class WorkerStderr:
    """Drain the child pipe without forwarding or storing any stderr bytes."""

    def __init__(self, stream: BinaryIO) -> None:
        self.type_hint: str | None = None
        self.exception_trace: list[ExceptionFact] = []
        self.diagnostic_write_error: str | None = None
        self._reader = Thread(target=self._drain, args=(stream,), daemon=True)
        self._reader.start()

    def _drain(self, stream: BinaryIO) -> None:
        in_traceback = False
        continuation = False
        try:
            with stream:
                while chunk := stream.readline(4096):
                    if not continuation:
                        line = chunk.rstrip(b"\r\n")
                        if line.startswith(_DIAGNOSTIC_WRITE_PREFIX):
                            self.diagnostic_write_error = _BUILTIN_ERRORS.get(
                                line.removeprefix(_DIAGNOSTIC_WRITE_PREFIX)
                            )
                        elif line.startswith(_TYPED_WORKER_PREFIX):
                            try:
                                payload = json.loads(line.removeprefix(_TYPED_WORKER_PREFIX))
                            except (ValueError, RecursionError):
                                continue
                            self.exception_trace.extend(read_exception_trace([payload]))
                        elif line.startswith(_NATIVE_FAILURE_PREFIXES):
                            self.type_hint = "NativeFailure"
                        elif line == _TRACEBACK_HEADER:
                            in_traceback = True
                        elif in_traceback and not line.startswith((b" ", b"\t")):
                            label = _BUILTIN_ERRORS.get(line.partition(b":")[0])
                            if label is not None:
                                self.type_hint = label
                                in_traceback = False
                    continuation = not chunk.endswith(b"\n")
        except (OSError, ValueError):
            pass

    def finish(self) -> None:
        # Worker code spawns no children. Keep even unexpected pipe ownership
        # from delaying recovery indefinitely after the worker exits.
        self._reader.join(timeout=0.1)


@contextmanager
def capture_provider_failure() -> Iterator[list[ExceptionFact]]:
    """Collect the provider diagnostic alongside the unchanged worker result."""
    captured: list[ExceptionFact] = []
    token = _capture.set(captured)
    try:
        yield captured
    finally:
        _capture.reset(token)


def log_exception_trace(
    logger: logging.Logger, level: int, label: str, error: BaseException, *arguments: object,
) -> list[ExceptionFact]:
    """Log typed, detached traceback data instead of rendering exception text."""
    trace = exception_trace(error)
    logger.log(level, label + " exception_trace=%s", *arguments, trace)
    return trace


def log_provider_failure(
    logger: logging.Logger, operation: str, provider: str, request_id: str, error: BaseException, *,
    cleanup: bool = False,
) -> None:
    raw_reference = request_id.encode("utf-8", "surrogatepass") if isinstance(request_id, str) else b""
    reference = sha256(raw_reference).hexdigest()[:16]
    label = ("provider_cleanup_failure" if cleanup else "unexpected_provider_failure")
    trace = log_exception_trace(logger, logging.WARNING if cleanup else logging.ERROR,
                                label + " operation=%s provider=%s request_ref=%s",
                                error, operation, provider, reference)
    captured = _capture.get()
    if captured is not None:
        if cleanup and trace:
            trace[0]["relation"] = "cleanup"
            captured.extend(trace)
        else:
            captured[:] = trace

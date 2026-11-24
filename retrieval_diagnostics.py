"""Project exception locations and causality without formatting raw errors."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import sha256
import logging
from pathlib import Path
import re
from typing import TypedDict


class Frame(TypedDict):
    file: str
    line: int
    function: str


class ExceptionFact(TypedDict):
    type: str
    relation: str
    frames: list[Frame]


_TYPE = re.compile(r"[A-Za-z_][A-Za-z_0-9]{0,127}")
_FILE = re.compile(r"[A-Za-z_][A-Za-z_0-9.]{0,127}")
_FUNCTION = re.compile(r"(?:[A-Za-z_][A-Za-z_0-9]{0,127}|<[a-z]+>)")
_RELATIONS = {"raised", "cause", "context"}
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
    current: BaseException | None = error
    relation = "raised"
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        frames = []
        trace = BaseException.__traceback__.__get__(current)
        while trace is not None:
            code = trace.tb_frame.f_code
            file, function = Path(code.co_filename).name, code.co_name
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
        if cause is not None:
            current, relation = cause, "cause"
        elif BaseException.__suppress_context__.__get__(current):
            current = None
        else:
            current, relation = BaseException.__context__.__get__(current), "context"
    return facts


@contextmanager
def capture_provider_failure() -> Iterator[list[ExceptionFact]]:
    """Collect the provider diagnostic alongside the unchanged worker result."""
    captured: list[ExceptionFact] = []
    token = _capture.set(captured)
    try:
        yield captured
    finally:
        _capture.reset(token)


def log_provider_failure(
    logger: logging.Logger, operation: str, provider: str, request_id: str, error: BaseException,
) -> None:
    trace = exception_trace(error)
    captured = _capture.get()
    if captured is not None:
        captured[:] = trace
    reference = sha256(request_id.encode("utf-8", "surrogatepass")).hexdigest()[:16]
    logger.error(
        "unexpected_provider_failure operation=%s provider=%s request_ref=%s exception_trace=%s",
        operation, provider, reference, trace,
    )

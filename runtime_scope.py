"""Credential-free owner identity and file locations shared by runtime ports."""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from pathlib import Path
from typing import Mapping, Any

OWNER = "earnings-transcripts"
FEEDBACK_OUT_ENV = "RESEARCH_FEEDBACK_OUT"
FEEDBACK_RUN_ID_ENV = "RESEARCH_FEEDBACK_RUN_ID"
FEEDBACK_ATTEMPT_ID_ENV = "RESEARCH_FEEDBACK_ATTEMPT_ID"
FEEDBACK_DIR_ENV = "RESEARCH_FEEDBACK_DIR"
FEEDBACK_DISABLED_ENV = "RESEARCH_FEEDBACK_DISABLED"
FEEDBACK_STATE_ROOT_ENV = "RESEARCH_FEEDBACK_STATE_ROOT"

_UNSAFE_PUBLIC_WORD = re.compile(r"[\s/?#&=\\]")
_AUTH = re.compile(
    r"(?i)\b(bearer|basic)(?:[ \t]+|\r?\n[ \t]+)(?:\r?\n[ \t]+)*"
    r"[^\s,;\"'<>]+(?:[ \t]*\r?\n[ \t]+[^\r\n]*)*"
)
_COOKIE = re.compile(r"(?im)(\b(?:set-cookie|cookie)[\"']?\s*:\s*)[^\r\n]*(?:\r?\n[ \t]+[^\r\n]*)*")
_USERINFO = re.compile(r"(?i)(https?://)[^/@\s\"'<>]+@")
_CREDENTIAL = re.compile(
    r"(?i)(\b(?:[\w-]*[-_])?(?:api[-_]?key|token|password|secret|credential)\b[\"']?\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s&,;<>]+)"
)
_CLI_SECRET = re.compile(r"(?i)(--(?:[\w-]*[-_])?(?:api[-_]?key|token|password|secret|credential)\b[\s=]+)[^\s,<>]+")
_PREFIX = re.compile(r"(?<![\w])(?:sk-|tvly-|ghp_|AIza)[A-Za-z0-9_-]{8,}")


def redact_text(value: str) -> str:
    value = _AUTH.sub(r"\1 [REDACTED]", value)
    value = _COOKIE.sub(r"\1[REDACTED]", value)
    value = _USERINFO.sub(r"\1[REDACTED]@", value)
    value = _CREDENTIAL.sub(r"\1[REDACTED]", value)
    value = _CLI_SECRET.sub(r"\1[REDACTED]", value)
    return _PREFIX.sub("[REDACTED]", value)


def safe_public_word(value: Any, *, fallback: str) -> str | None:
    """Keep an opaque word or hash an arbitrary value without publishing it."""
    if not isinstance(value, str) or not value:
        return None
    if len(value) <= 128 and not _UNSAFE_PUBLIC_WORD.search(value) and redact_text(value) == value:
        return value
    return f"{fallback}-{hashlib.sha256(value.encode('utf-8', errors='surrogatepass')).hexdigest()[:16]}"


def step_id(request: Any, operation: str) -> str:
    if isinstance(request, Mapping):
        request_id = safe_public_word(request.get("request_id"), fallback="request")
        if request_id is not None:
            return request_id
    return f"{operation}-without-request-id"


def public_request(request: Any) -> dict[str, Any]:
    """Return only typed request fields sufficient to reproduce its selection and budget."""
    if not isinstance(request, Mapping):
        return {}
    result = {}
    for name in ("request_id", "ticker", "exchange", "provider", "as_of_date", "candidate_id"):
        value = safe_public_word(request.get(name), fallback="redacted")
        if value is not None:
            result[name] = value
    for name in ("fiscal_year", "fiscal_quarter", "timeout_seconds", "max_body_bytes", "max_response_bytes"):
        if type(request.get(name)) is int:
            result[name] = request[name]
    if type(request.get("download_authorized")) is bool:
        result["download_authorized"] = request["download_authorized"]
    return result


def storage_path(value: str | os.PathLike[str]) -> Path:
    """Normalize long Windows paths for files, siblings, locks and SQLite."""
    path = Path(value)
    if os.name != "nt":
        return path
    absolute = os.path.abspath(path)
    if absolute.startswith(("\\\\?\\", "\\\\.\\")):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)


def source_fingerprint(source: str) -> str | None:
    """Report the actual entry bytes when source is available in this installation."""
    try:
        return hashlib.sha256(storage_path(source).read_bytes()).hexdigest()
    except (OSError, ValueError):
        return None


def default_state_root() -> Path:
    return Path.home() / ".earnings-transcripts" / "feedback"


def redact_diagnostic_tree(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {redact_text(key) if isinstance(key, str) else key: redact_diagnostic_tree(item)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_diagnostic_tree(item) for item in value]
    return value


def protected_reference(value: str, *, kind: str) -> str:
    """Keep opaque IDs stable unless they contain a credential-shaped value."""
    if redact_text(value) == value:
        return value
    return f"{kind}-{hashlib.sha256(value.encode('utf-8', errors='surrogatepass')).hexdigest()[:16]}"


def run_identity(environ: Mapping[str, str] | None = None) -> tuple[str, str]:
    env = os.environ if environ is None else environ
    run = env.get(FEEDBACK_RUN_ID_ENV) or "run-" + uuid.uuid4().hex[:12]
    attempt = env.get(FEEDBACK_ATTEMPT_ID_ENV) or "attempt-1"
    return protected_reference(run, kind="run"), protected_reference(attempt, kind="attempt")


def state_root(environ: Mapping[str, str] | None = None) -> Path | None:
    env = os.environ if environ is None else environ
    raw = env.get(FEEDBACK_STATE_ROOT_ENV)
    if raw and raw.strip():
        return Path(raw.strip())
    try:
        return default_state_root()
    except (RuntimeError, OSError):
        return None

"""ET's own feedback projection: one ``feedback-observation/1`` record per check.

The cross-repository format is frozen by the research feedback loop: one UTF-8
JSON object per line, with the required string fields ``schema_version``,
``owner``, ``run_id``, ``attempt_id``, ``step_id``, ``check_id``, ``expected``
and ``actual``, and the optional ``issue_key``, ``evidence_ref``, ``version``,
``parent_step_id``, ``details_ref``, ``disposition`` and ``origin_owner``.
``expected`` and ``actual`` are the stable result words of one check; the
numbers, severities and qualifiers behind them stay in this repository's own
evidence.

This module never changes the business contract. It writes only to the
sidecar file named by ``RESEARCH_FEEDBACK_OUT``, to that file's sibling
details file, and — best effort, when a write was refused — to the sibling
diagnostics file; it never writes to stdout or stderr, never touches a
transcript body, and never raises. A failed write is reported as an
incomplete diagnostic on the recorder, so the caller keeps its own exit code,
result, cost, original and retry behaviour.

The observation line is a projection, not the log. The full native facts
(request, provider answer, usage receipt) stay in this repository's run
directory as the sibling ``*.details.jsonl`` record; the observation carries
only stable result words and opaque, credential-free references.

Only a retrieval step produces observations. The local capability listing
(``--capabilities``) contacts no provider and completes no native step, so it
produces none.

Ordinary-start identity and delivery. The run and attempt numbers a parent
passes are associated as-is; a run without host-supplied numbers generates its
own recoverable identity, so two concurrent tasks stay isolated. When
``RESEARCH_FEEDBACK_OUT`` is absent the run is a direct start: records are
saved first under the owner's own durable state root (``pending/``), then
published atomically to the public inbox (``RESEARCH_FEEDBACK_DIR`` or
``<state root>/observations``) as ``feedback-<32 lowercase hex>.jsonl`` with a
sibling details file and a same-basename ``.ready`` mark written only after
the sanitized details are in place. An explicit ``RESEARCH_FEEDBACK_OUT``
keeps the single-run file and its existing detail format, and wins over
``RESEARCH_FEEDBACK_DIR``. A pending record that outlived its process is
redelivered read-only by a later process running under the same
owner/run/attempt identity; the same occurrence is registered exactly once,
and ROOT's ``.ack`` mark stops redelivery. Write failures never change the
business return.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

OBSERVATION_SCHEMA = "feedback-observation/1"
DETAIL_SCHEMA = "earnings-transcripts-feedback-detail/1"
OWNER = "earnings-transcripts"
ADAPTER_VERSION = "earnings-transcripts-feedback/1.0.0"

FEEDBACK_OUT_ENV = "RESEARCH_FEEDBACK_OUT"
FEEDBACK_RUN_ID_ENV = "RESEARCH_FEEDBACK_RUN_ID"
FEEDBACK_ATTEMPT_ID_ENV = "RESEARCH_FEEDBACK_ATTEMPT_ID"
FEEDBACK_DIR_ENV = "RESEARCH_FEEDBACK_DIR"
FEEDBACK_DISABLED_ENV = "RESEARCH_FEEDBACK_DISABLED"
FEEDBACK_STATE_ROOT_ENV = "RESEARCH_FEEDBACK_STATE_ROOT"

def default_state_root() -> Path:
    """The owner's own machine default: ``~/.earnings-transcripts/feedback``.

    Resolved lazily, so importing this module never depends on a resolvable
    home directory; only a run that actually uses the default does.
    """
    return Path.home() / ".earnings-transcripts" / "feedback"

DISPOSITIONS = ("expected", "deviation", "unknown")

#: The published status vocabulary of the two result contracts. The
#: transcript result (``earnings-transcript-result/1`` and ``/2``) and the
#: discovery result (``earnings-transcript-discovery-result/1``) share every
#: failure status and differ in their success status: ``fetched`` for a
#: document, ``discovered`` for a candidate. A status outside this vocabulary
#: is an undocumented status word.
STATUS_VOCABULARY = frozenset(
    {
        "fetched",
        "discovered",
        "not_found",
        "ambiguous",
        "unsupported",
        "unavailable",
        "not_authorized",
        "rate_limited",
        "provider_error",
        "deadline_exceeded",
        "content_too_large",
        "provenance_rejected",
        "invalid_request",
    }
)

#: The published mapping from one named reason to its mandated status word.
#: A named reason that is absent here is an undocumented reason word.
REASON_STATUS = {
    # request and contract
    "request_schema": "invalid_request",
    "invalid_json": "invalid_request",
    "request_too_large": "invalid_request",
    "source_payload_flag": "invalid_request",
    "source_payload_not_valid_for_discovery": "invalid_request",
    "candidate_request_schema_or_identity": "invalid_request",
    # provider availability
    "provider_unavailable": "unsupported",
    "provider_disabled": "unavailable",
    "provider_cost_unknown": "unavailable",
    "provider_cost_budget_exceeded": "unavailable",
    "candidate_discovery_unavailable": "unsupported",
    "candidate_fetch_unavailable": "unsupported",
    "unsupported_market": "unsupported",
    "unsupported_exchange": "unsupported",
    # credentials and entitlement
    "provider_credentials_missing": "unavailable",
    "provider_credentials_file_unavailable": "unavailable",
    "provider_credentials_file_invalid": "unavailable",
    "provider_credentials_file_empty": "unavailable",
    "provider_credentials_rejected": "unavailable",
    "provider_entitlement_required": "unavailable",
    "provider_entitlement_denied": "unavailable",
    # provenance and identity
    "provider_identity_or_host": "provenance_rejected",
    "candidate_effective_url_or_mime": "provenance_rejected",
    # throttling and failure
    "provider_http_429": "rate_limited",
    "provider_response": "provider_error",
    "provider_credentials_leaked": "provider_error",
    "unexpected_provider_failure": "provider_error",
    "retrieval_worker_failure": "provider_error",
    "provider_deadline": "deadline_exceeded",
    # content limits
    "byte_limit": "content_too_large",
    # period and cutoff
    "call_after_as_of": "not_found",
    "publication_after_as_of": "not_found",
}

#: Issue keys are the owner-approved rule signatures of a deviation.
ISSUE_OUTCOME_NAMING = "transcript-outcome-naming"
ISSUE_PERIOD_ATTRIBUTION = "transcript-period-attribution"
ISSUE_CONTENT_REFERENCE = "transcript-content-reference"
ISSUE_USAGE_RECEIPT = "transcript-usage-receipt"
ISSUE_AUDIT_OUTCOME = "transcript-audit-outcome"

#: The stable names of the checks this projection emits. Every check carries
#: its own name; nothing infers a name from the result words, because two
#: different checks can produce the same words.
CHECK_OUTCOME_NAMING = "outcome-naming"
CHECK_PERIOD_OWNERSHIP = "exact-period-ownership"
CHECK_CONTENT_REFERENCE = "content-reference"
CHECK_USAGE_ACCOUNTING = "usage-accounting"
CHECK_ACTION_STARTED = "action-started"
CHECK_INVOCATION_REFUSAL = "invocation-refusal"
CHECK_STORED_ORIGINAL = "stored-original-audit"

#: The stable step id of one refused ordinary invocation; the owning entry is
#: a word inside the detail record, never a path or command.
REFUSAL_STEP_ID = "cli-refusal"

#: The documented per-file outcomes of the local stored-original audit. An
#: outcome outside this vocabulary is an undocumented outcome word.
AUDIT_OUTCOME_VOCABULARY = frozenset(
    {
        "verified",
        "legacy_unverified",
        "identity_missing",
        "identity_mismatch",
        "period_mismatch",
        "corrupt",
        "receipt_invalid",
        "receipt_mismatch",
    }
)

#: Result statuses that mean the requested source material was not obtained,
#: with the refusal documented. A status outside success/refusal is unknown.
SOURCE_UNAVAILABLE_STATUSES = frozenset(
    {
        "not_found",
        "ambiguous",
        "unsupported",
        "unavailable",
        "not_authorized",
        "rate_limited",
        "invalid_request",
        "content_too_large",
        "provenance_rejected",
    }
)
COMPLETION_SUCCESS_STATUSES = frozenset({"fetched", "discovered"})

MAX_SCANNED_BYTES = 8 * 1024 * 1024

#: The same-identity writer lock: how long a process waits for a busy lock
#: before it defers the delivery to the next attempt, and how long a lock
#: untouched by its owner survives before it is treated as abandoned.
IDENTITY_LOCK_ATTEMPTS = 12
IDENTITY_LOCK_SLEEP_SECONDS = 0.02
IDENTITY_LOCK_STALE_SECONDS = 60


def _is_str(value: Any) -> bool:
    return isinstance(value, str) and value != ""


#: Characters that would smuggle URL queries, paths or forged stderr text
#: through a public identifier word. A colon stays allowed: the repository's
#: own opaque evidence references use ``et-case:<name>``, and every URL
#: shape still carries at least one of the characters below.
_UNSAFE_PUBLIC_WORD = re.compile(r"[\s/?#&=\\]")


def safe_public_word(value: Any, *, fallback: str) -> str | None:
    """One publication-safe identifier word.

    A bounded value free of URL/whitespace/path punctuation is published
    as-is; anything else — a URL with a query string, a forged traceback
    line, an oversized string — becomes a stable digest word, so the
    identity stays reproducible across processes without publishing the
    unsafe content.
    """
    if not _is_str(value):
        return None
    if len(value) <= 128 and not _UNSAFE_PUBLIC_WORD.search(value):
        return value
    return f"{fallback}-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:16]}"


def observation_id(
    *, owner: str, run_id: str, attempt_id: str, step_id: str, check_id: str
) -> str:
    """The stable identity of one observation: a replay reuses the same id."""
    raw = "|".join((owner, run_id, attempt_id, step_id, check_id))
    return "fb-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def details_id(details: Mapping[str, Any]) -> str:
    """A content-derived id for one detail record."""
    raw = json.dumps(details, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "fb-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def requested_period(request: Any) -> str | None:
    """The exact fiscal period one request binds, as ``YYYY-Qn``."""
    if not isinstance(request, Mapping):
        return None
    year = request.get("fiscal_year")
    quarter = request.get("fiscal_quarter")
    if type(year) is not int or type(quarter) is not int:
        return None
    if not 1990 <= year <= 2100 or not 1 <= quarter <= 4:
        return None
    return f"{year}-Q{quarter}"


def _item_period(item: Mapping[str, Any]) -> str | None:
    year = item.get("year")
    if isinstance(year, str) and year.isdigit():
        year = int(year)
    if type(year) is not int:
        return None
    quarter = item.get("period", item.get("quarter"))
    if isinstance(quarter, str):
        stripped = quarter.strip().upper()
        if stripped.startswith("Q") and stripped[1:].isdigit():
            quarter = int(stripped[1:])
    if type(quarter) is not int:
        return None
    if not 1990 <= year <= 2100 or not 1 <= quarter <= 4:
        return None
    return f"{year}-Q{quarter}"


def native_payload_bytes(result: Any) -> bytes | None:
    """The original provider response bytes, when the result carries them."""
    if not isinstance(result, Mapping):
        return None
    payload = result.get("provider_payload_base64")
    if not isinstance(payload, str) or not payload:
        return None
    try:
        return base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        return None


def native_period(result: Any) -> str | None:
    """Re-derive the provider document's period from the original bytes.

    Only the FMP JSON envelope is independently readable here. Any other
    payload shape stays ``None``: the native truth is unknown, never guessed.
    """
    raw = native_payload_bytes(result)
    if raw is None:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, list):
        return None
    periods = {
        period
        for item in data
        if isinstance(item, Mapping)
        for period in [_item_period(item)]
        if period is not None
    }
    if len(periods) != 1:
        return None
    return periods.pop()


def content_reference_state(result: Any) -> str:
    """Compare the declared provider-payload reference with the returned bytes.

    ``reference_matches_bytes`` — the declared digest is the digest of the
    returned original response. ``reference_mismatch`` — the digest does not
    describe those bytes. ``reference_absent`` — the result declares no
    digest. ``reference_unverifiable`` — a digest is declared but the result
    carries no original bytes to check it against.
    """
    if not isinstance(result, Mapping):
        return "reference_absent"
    declared = result.get("provider_payload_sha256")
    if not _is_str(declared):
        return "reference_absent"
    raw = native_payload_bytes(result)
    if raw is None:
        return "reference_unverifiable"
    if hashlib.sha256(raw).hexdigest() != declared:
        return "reference_mismatch"
    return "reference_matches_bytes"


def reason_status(reason: Any) -> str | None:
    """The mandated status word of one named reason, or ``None``."""
    if not _is_str(reason):
        return None
    if reason in REASON_STATUS:
        return REASON_STATUS[reason]
    if reason.startswith("provider_http_"):
        return "provider_error"
    return None


def evaluate_outcome_naming(result: Any) -> dict[str, str]:
    """Check the business status word against the published named reasons."""
    status = result.get("status") if isinstance(result, Mapping) else None
    if not _is_str(status):
        return {
            "check_id": CHECK_OUTCOME_NAMING,
            "expected": "documented_status",
            "actual": "undocumented_status",
            "disposition": "deviation",
            "issue_key": ISSUE_OUTCOME_NAMING,
        }
    reason = result.get("error_code") if isinstance(result, Mapping) else None
    if _is_str(reason):
        mandated = reason_status(reason)
        if mandated is None:
            return {
                "check_id": CHECK_OUTCOME_NAMING,
                "expected": "documented_status",
                "actual": "undocumented_status",
                "disposition": "deviation",
                "issue_key": ISSUE_OUTCOME_NAMING,
            }
        expected = mandated
    else:
        expected = status if status in STATUS_VOCABULARY else "documented_status"
        if expected == "documented_status":
            return {
                "check_id": CHECK_OUTCOME_NAMING,
                "expected": "documented_status",
                "actual": "undocumented_status",
                "disposition": "deviation",
                "issue_key": ISSUE_OUTCOME_NAMING,
            }
    if expected == status:
        return {
            "check_id": CHECK_OUTCOME_NAMING,
            "expected": expected,
            "actual": status,
            "disposition": "expected",
        }
    return {
        "check_id": CHECK_OUTCOME_NAMING,
        "expected": expected,
        "actual": status,
        "disposition": "deviation",
        "issue_key": ISSUE_OUTCOME_NAMING,
    }


def evaluate_period_ownership(request: Any, result: Any) -> dict[str, str]:
    """Check that a claimed document really is the requested period.

    The requirement is one-directional: the run may attribute the requested
    period to a document only when the original provider response itself
    proves that period. A run that attributes nothing is never a deviation,
    and a run whose native proof is unavailable stays ``unknown`` instead of
    being called wrong.
    """
    status = result.get("status") if isinstance(result, Mapping) else None
    if status != "fetched":
        return {
            "check_id": CHECK_PERIOD_OWNERSHIP,
            "expected": "not_attributed",
            "actual": "not_attributed",
            "disposition": "expected",
        }
    period = native_period(result)
    if period is None:
        return {
            "check_id": CHECK_PERIOD_OWNERSHIP,
            "expected": "unknown",
            "actual": "requested_period_attributed",
            "disposition": "unknown",
        }
    wanted = requested_period(request)
    if wanted is None or period != wanted:
        return {
            "check_id": CHECK_PERIOD_OWNERSHIP,
            "expected": "not_attributed",
            "actual": "requested_period_attributed",
            "disposition": "deviation",
            "issue_key": ISSUE_PERIOD_ATTRIBUTION,
        }
    return {
        "check_id": CHECK_PERIOD_OWNERSHIP,
        "expected": "requested_period_attributed",
        "actual": "requested_period_attributed",
        "disposition": "expected",
    }


def evaluate_content_reference(result: Any) -> dict[str, str]:
    """Check the declared content reference against the returned original bytes."""
    state = content_reference_state(result)
    if state == "reference_mismatch":
        return {
            "check_id": CHECK_CONTENT_REFERENCE,
            "expected": "reference_matches_bytes",
            "actual": state,
            "disposition": "deviation",
            "issue_key": ISSUE_CONTENT_REFERENCE,
        }
    return {
        "check_id": CHECK_CONTENT_REFERENCE,
        "expected": state,
        "actual": state,
        "disposition": "expected",
    }


def evaluate_usage(
    native_receipt: Any, published_receipt: Any, *, receipt_required: bool
) -> dict[str, str]:
    """Check the native usage fact against what the run actually published.

    ``native_receipt`` is the supervisor's final usage receipt, or ``None``
    when this run had no supervisor (the cooperative test seam), in which case
    the final usage of the operation is unknown by construction. A receipt
    that ``--report-usage`` promised but never published is a deviation: a
    consumer that reconciles cost from the receipt must not have to guess
    whether the provider was called.
    """
    if not isinstance(native_receipt, Mapping):
        return {
            "check_id": CHECK_USAGE_ACCOUNTING,
            "expected": "usage_unknown",
            "actual": "usage_unknown",
            "disposition": "unknown",
        }
    complete = (
        native_receipt.get("usage_complete") is True
        and isinstance(native_receipt.get("usage"), Mapping)
    )
    expected = "usage_known" if complete else "usage_unknown"
    if receipt_required and not isinstance(published_receipt, Mapping):
        actual = "usage_missing"
    elif isinstance(published_receipt, Mapping):
        published_complete = (
            published_receipt.get("usage_complete") is True
            and isinstance(published_receipt.get("usage"), Mapping)
        )
        actual = "usage_known" if published_complete else "usage_unknown"
    else:
        actual = expected
    if actual == expected:
        disposition = "expected" if expected == "usage_known" else "unknown"
        return {
            "check_id": CHECK_USAGE_ACCOUNTING,
            "expected": expected,
            "actual": actual,
            "disposition": disposition,
        }
    return {
        "check_id": CHECK_USAGE_ACCOUNTING,
        "expected": expected,
        "actual": actual,
        "disposition": "deviation",
        "issue_key": ISSUE_USAGE_RECEIPT,
    }


def completion_state(result: Any, *, attempted: bool | None) -> dict[str, Any]:
    """Whether the operation actually completed, kept apart from the status word.

    ``attempted`` is ``True`` when the run reached the provider path, ``False``
    when it was refused before any provider work, and ``None`` when the fact is
    unknown (a started record that has not reached a terminal state).
    """
    status = result.get("status") if isinstance(result, Mapping) else None
    completed = _is_str(status) and status in COMPLETION_SUCCESS_STATUSES
    return {"operation_completed": completed, "attempted": attempted}


def source_availability_state(result: Any) -> str | None:
    """Whether the requested source material is available after this run.

    ``source_delivered`` — the document body was fetched. ``source_located`` —
    a candidate was discovered without the body. ``source_unavailable`` — the
    refusal that denied the material is documented. ``source_unknown`` — the
    run failed after attempting, so availability is unknown. ``None`` — no
    terminal result exists yet.
    """
    status = result.get("status") if isinstance(result, Mapping) else None
    if not _is_str(status):
        return None
    if status == "fetched":
        return "source_delivered"
    if status == "discovered":
        return "source_located"
    if status in SOURCE_UNAVAILABLE_STATUSES:
        return "source_unavailable"
    return "source_unknown"


def _redacted_request(request: Any) -> dict[str, Any]:
    """The request facts a reproduction needs, without any other field."""
    if not isinstance(request, Mapping):
        return {}
    keep = (
        "request_id",
        "ticker",
        "exchange",
        "fiscal_year",
        "fiscal_quarter",
        "as_of_date",
        "provider",
        "download_authorized",
    )
    redacted: dict[str, Any] = {}
    for name in keep:
        if name not in request:
            continue
        value = request[name]
        if name in ("request_id", "ticker"):
            value = safe_public_word(value, fallback="redacted")
        if value is not None:
            redacted[name] = value
    return redacted


def _redacted_result(result: Any) -> dict[str, Any]:
    """The native result facts, without the body, the original bytes or a path."""
    if not isinstance(result, Mapping):
        return {}
    keep = (
        "status",
        "error_code",
        "provider",
        "fiscal_period",
        "provider_document_id",
        "call_date",
        "publication_date",
        "as_of_date",
        "provider_payload_sha256",
        "canonical_content_sha256",
        "content_bytes",
        "http_status",
        "candidate_count",
    )
    return {name: result[name] for name in keep if name in result}


def _redact_receipt_request_id(receipt: Any) -> Any:
    """One embedded usage receipt copy with a publication-safe request id.

    The receipt on stderr is the business contract and stays raw; the copy in
    the published detail replaces an unsafe request id with the same stable
    digest word the request facts use.
    """
    if not isinstance(receipt, Mapping):
        return receipt
    redacted = dict(receipt)
    value = redacted.get("request_id")
    if isinstance(value, str):
        safe = safe_public_word(value, fallback="redacted")
        if safe is not None:
            redacted["request_id"] = safe
    return redacted


def build_details(
    *,
    request: Any,
    result: Any,
    operation: str,
    native_receipt: Any,
    published_receipt: Any,
    phase: str = "finished",
    branch: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    attempted: bool | None = None,
    worker: Any = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """One credential-free detail record holding this run's native facts.

    The record is this repository's own step log: it keeps the public
    operation, the time, the native request id, the result, the branch the
    run took, whether the run was retried or reused, the stable error type
    and the reason chain. ``completion`` and ``source_availability`` record
    whether the operation actually completed and whether the requested source
    material is available, separate from the status word and from usage.
    ``worker`` carries the supervisor's sanitized child-process diagnostics
    (exit code, phase, exception type chain) merged once by the process that
    owns the details. The ``started`` phase is written before the action can
    end, so an interrupted run leaves a started record with no result.
    """
    error_code = result.get("error_code") if isinstance(result, Mapping) else None
    record: dict[str, Any] = {
        "schema_version": DETAIL_SCHEMA,
        "phase": phase,
        "operation": operation,
        "started_at": started_at,
        "finished_at": finished_at,
        "branch": branch,
        "retried": False,
        "reused": False,
        "request": _redacted_request(request),
        "result": _redacted_result(result),
        "requested_period": requested_period(request),
        "native_period": native_period(result),
        "content_reference": content_reference_state(result),
        "error_type": error_code if _is_str(error_code) else None,
        "reason_chain": [error_code] if _is_str(error_code) else [],
        "projection_version": ADAPTER_VERSION,
        "usage": {
            "native_receipt": _redact_receipt_request_id(native_receipt),
            "published_receipt": _redact_receipt_request_id(published_receipt),
        },
    }
    record["completion"] = completion_state(result, attempted=attempted)
    availability = source_availability_state(result)
    if availability is not None:
        record["source_availability"] = availability
    if worker is not None:
        record["worker"] = worker
    if extra:
        record.update(extra)
    return record


def now() -> str:
    """The UTC time stamp of this run, second resolution."""
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def begin_step(
    recorder: FeedbackRecorder | None,
    *,
    request: Any,
    operation: str,
    branch: str | None = None,
    evidence_ref: str | None = None,
) -> dict[str, Any] | None:
    """Record that the action started, before it can reach a terminal state.

    A run that is interrupted after this record and before its result leaves
    a started observation with no result: the final state is unknown, and an
    unknown is reconciled rather than retried.
    """
    if recorder is None:
        return None
    try:
        started_at = now()
        details = build_details(
            request=request,
            result={},
            operation=operation,
            native_receipt=None,
            published_receipt=None,
            phase="started",
            branch=branch,
            started_at=started_at,
        )
        reference = safe_public_word(evidence_ref, fallback="evidence")
        if not _is_str(reference) and isinstance(request, Mapping):
            reference = safe_public_word(request.get("request_id"), fallback="request")
        recorder.record(
            step_id=_step_id(request, operation),
            check_id=CHECK_ACTION_STARTED,
            expected="started",
            actual="started",
            disposition="expected",
            evidence_ref=reference,
            details=details,
        )
        return details
    except Exception:  # pragma: no cover - defensive, never a crash
        recorder._note_failure("feedback_write_failed:projection_error")
        return None


class FeedbackRecorder:
    """A single-writer, append-only JSONL sink for one run's observations."""

    def __init__(
        self,
        out_path: str | os.PathLike[str],
        *,
        run_id: str,
        attempt_id: str,
        version: str = ADAPTER_VERSION,
        owner: str = OWNER,
        details_path: str | os.PathLike[str] | None = None,
        sink: Any = None,
        details_sink: Any = None,
        pending_path: str | os.PathLike[str] | None = None,
        identity_lock_path: str | os.PathLike[str] | None = None,
    ) -> None:
        self.out_path = Path(out_path)
        self.details_path = (
            Path(details_path)
            if details_path is not None
            else self.out_path.with_name(self.out_path.name + ".details.jsonl")
        )
        self.run_id = run_id
        self.attempt_id = attempt_id
        self.version = version
        self.owner = owner
        # An injected sink replaces the file for one target. It exists so an
        # unwritable destination can be exercised without depending on the
        # host's permission model.
        self._sink = sink
        self._details_sink = details_sink
        # The owner's own durable pending store: when the intended sink cannot
        # take a record, the record is saved here first and redelivered by a
        # later process running under the same identity.
        self.pending_path = Path(pending_path) if pending_path is not None else None
        # Cross-process serialization point for same-identity writers: every
        # check-then-append of one occurrence identity happens under this lock
        # so two concurrent producers can never both believe they are first.
        self.identity_lock_path = (
            Path(identity_lock_path) if identity_lock_path is not None else None
        )
        self.diagnostics_path = self.out_path.with_name(
            self.out_path.name + ".diagnostics.jsonl"
        )
        self.write_failures = 0
        self.diagnostics: list[str] = []
        self.written_ids: set[str] = set()
        self._details_ids: set[str] = set()
        # Records this process already saved durably after a refused sink;
        # they are not re-stashed, and the drain delivers each exactly once.
        self._stashed_ids: set[str] = set()
        self._scan_existing()
        if self.pending_path is not None:
            self._drain_pending()

    def _note_failure(self, kind: str) -> None:
        """Record an incomplete diagnostic for the owning job to report.

        The diagnostic is kept in memory for a job that drives this process
        and, best effort, in a sibling file for a job that reads the run
        directory afterwards. It never reaches stdout or stderr: the business
        contract of the run stays exactly as it was.
        """
        self.write_failures += 1
        self.diagnostics.append(kind)
        try:
            self.diagnostics_path.parent.mkdir(parents=True, exist_ok=True)
            with self.diagnostics_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(
                    json.dumps(
                        {
                            "run_id": self.run_id,
                            "attempt_id": self.attempt_id,
                            "diagnostic": kind,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
        except OSError:
            pass

    # ── reading what a previous attempt already wrote ──────────────
    def _scan_existing(self) -> None:
        for path, sink in (
            (self.out_path, self.written_ids),
            (self.details_path, self._details_ids),
        ):
            for record in _read_jsonl(path):
                if not isinstance(record, Mapping):
                    continue
                if "schema_version" not in record and "check_id" not in record:
                    continue
                if "check_id" in record:
                    sink.add(
                        observation_id(
                            owner=str(record.get("owner", self.owner)),
                            run_id=str(record.get("run_id", self.run_id)),
                            attempt_id=str(record.get("attempt_id", self.attempt_id)),
                            step_id=str(record.get("step_id", "")),
                            check_id=str(record.get("check_id", "")),
                        )
                    )
                else:
                    sink.add(str(record.get("details_id", "")))

    # ── the owner's own durable pending store ──────────────────────
    @property
    def _pending_details_path(self) -> Path:
        assert self.pending_path is not None
        return self.pending_path.with_name(self.pending_path.name + ".details.jsonl")

    def _acquire_identity_lock(self):
        """Take the same-identity writer lock; ``None`` means best-effort mode.

        Every check-then-append of one occurrence identity, every drain and
        every publish happens under this lock, so two concurrent processes
        running under the same owner/run/attempt can never both believe they
        are registering an occurrence first. Acquisition retries briefly,
        reaps a lock left behind by a killed process, and then proceeds
        unlocked — never deadlocks, never crashes. A lock that stays busy
        defers the delivery of the next attempt instead of losing it.
        """
        path = self.identity_lock_path
        if path is None:
            return None
        for _ in range(IDENTITY_LOCK_ATTEMPTS):
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.mkdir()
                return path
            except FileExistsError:
                self._reap_stale_identity_lock(path)
                time.sleep(IDENTITY_LOCK_SLEEP_SECONDS)
            except OSError:
                return None
        return None

    @staticmethod
    def _reap_stale_identity_lock(path: Path) -> None:
        """A lock left behind by a killed process must not block recovery forever."""
        try:
            if time.time() - path.stat().st_mtime > IDENTITY_LOCK_STALE_SECONDS:
                path.rmdir()
        except OSError:
            pass

    @staticmethod
    def _release_identity_lock(lock: Path | None) -> None:
        if lock is None:
            return
        try:
            lock.rmdir()
        except OSError:
            pass

    def _drain_pending(self) -> None:
        """Redeliver saved records into the intended sink, read-only reconcile.

        A process that died between saving and confirming leaves its records
        here; a later process running under the same identity re-appends every
        record the sink is still missing and only then removes the pending
        files. The same occurrence is registered exactly once: records already
        present in the sink are skipped by identity, and a detail is delivered
        only when a saved observation references it, so re-runs under the same
        identity never leave orphan detail rows behind.
        """
        if self.pending_path is None:
            return
        saved_observations = _read_jsonl(self.pending_path)
        saved_details = _read_jsonl(self._pending_details_path)
        if not saved_observations and not saved_details:
            return
        lock = self._acquire_identity_lock()
        if lock is None and self.identity_lock_path is not None:
            return  # another same-identity process is mid-delivery; it will finish
        try:
            pending_observations = [
                record
                for record in saved_observations
                if isinstance(record, Mapping) and "check_id" in record
            ]
            pending_details = [
                record
                for record in saved_details
                if isinstance(record, Mapping) and _is_str(record.get("details_id"))
            ]
            referenced_details = {
                str(record.get("details_ref"))
                for record in pending_observations
                if _is_str(record.get("details_ref"))
            }
            missing_observations = []
            seen_observations: set[str] = set()
            for record in pending_observations:
                identifier = observation_id(
                    owner=str(record.get("owner", self.owner)),
                    run_id=str(record.get("run_id", self.run_id)),
                    attempt_id=str(record.get("attempt_id", self.attempt_id)),
                    step_id=str(record.get("step_id", "")),
                    check_id=str(record.get("check_id", "")),
                )
                if identifier in self.written_ids or identifier in seen_observations:
                    continue
                seen_observations.add(identifier)
                missing_observations.append((identifier, record))
            missing_details = []
            seen_details: set[str] = set()
            for record in pending_details:
                identifier = str(record["details_id"])
                if identifier not in referenced_details:
                    continue  # an orphan detail no observation will reference
                if identifier in self._details_ids or identifier in seen_details:
                    continue
                seen_details.add(identifier)
                missing_details.append(record)
            delivered = 0
            for identifier, record in missing_observations:
                if self._append_jsonl(self.out_path, record):
                    self.written_ids.add(identifier)
                    delivered += 1
            for record in missing_details:
                if self._append_jsonl(self.details_path, record):
                    self._details_ids.add(str(record["details_id"]))
                    delivered += 1
            if delivered == len(missing_observations) + len(missing_details):
                # Every deliverable saved record reached the sink; the pending
                # copy is done.
                for path in (self.pending_path, self._pending_details_path):
                    try:
                        path.unlink()
                    except OSError:
                        pass
        finally:
            self._release_identity_lock(lock)

    def _stash_pending(self, record: Mapping[str, Any]) -> None:
        """Save one record the sink refused, best effort, before giving up."""
        if self.pending_path is None:
            return
        path = (
            self._pending_details_path
            if "details_id" in record and "check_id" not in record
            else self.pending_path
        )
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(
                    json.dumps(record, ensure_ascii=False, separators=(",", ":"))
                    + "\n"
                )
        except OSError as exc:
            self._note_failure(f"feedback_save_failed:{type(exc).__name__}")

    # ── writing ────────────────────────────────────────────────────
    def record(
        self,
        *,
        step_id: str,
        check_id: str,
        expected: str,
        actual: str,
        disposition: str | None = None,
        issue_key: str | None = None,
        evidence_ref: str | None = None,
        details: Mapping[str, Any] | None = None,
        parent_step_id: str | None = None,
        origin_owner: str | None = None,
    ) -> bool:
        """Append one observation. Returns True when a new line was written.

        The method never raises: an unwritable sidecar is reported through
        ``write_failures`` and ``diagnostics`` so the caller keeps its own
        business contract.
        """
        try:
            return self._record(
                step_id=step_id,
                check_id=check_id,
                expected=expected,
                actual=actual,
                disposition=disposition,
                issue_key=issue_key,
                evidence_ref=evidence_ref,
                details=details,
                parent_step_id=parent_step_id,
                origin_owner=origin_owner,
            )
        except Exception as exc:  # pragma: no cover - defensive, never a crash
            self._note_failure(f"feedback_write_failed:{type(exc).__name__}")
            return False

    def _record(
        self,
        *,
        step_id: str,
        check_id: str,
        expected: str,
        actual: str,
        disposition: str | None,
        issue_key: str | None,
        evidence_ref: str | None,
        details: Mapping[str, Any] | None,
        parent_step_id: str | None,
        origin_owner: str | None,
    ) -> bool:
        if not all(_is_str(value) for value in (step_id, check_id, expected, actual)):
            self._note_failure("feedback_write_failed:invalid_observation")
            return False
        if disposition is None:
            disposition = "expected" if expected == actual else "deviation"
        if disposition not in DISPOSITIONS:
            self._note_failure("feedback_write_failed:invalid_disposition")
            return False
        identifier = observation_id(
            owner=self.owner,
            run_id=self.run_id,
            attempt_id=self.attempt_id,
            step_id=step_id,
            check_id=check_id,
        )
        # One occurrence identity is registered at most once per sink, across
        # processes: the check-then-append runs under the same-identity lock
        # with a fresh scan of what is already on disk.
        lock = self._acquire_identity_lock()
        try:
            self._scan_existing()
            if identifier in self.written_ids:
                # A replayed event keeps its original identity and adds no line.
                return False
            reference = evidence_ref
            detail_reference: str | None = None
            if details is not None:
                detail_record = dict(details)
                detail_identifier = details_id(detail_record)
                detail_record["details_id"] = detail_identifier
                if detail_identifier not in self._details_ids:
                    if self._append_jsonl(
                        self.details_path, detail_record, sink=self._details_sink
                    ):
                        self._details_ids.add(detail_identifier)
                    elif detail_identifier not in self._stashed_ids:
                        # Save first: the detail stays deliverable even though
                        # this sink refused it.
                        self._stash_pending(detail_record)
                        self._stashed_ids.add(detail_identifier)
                detail_reference = detail_identifier
            observation: dict[str, Any] = {
                "schema_version": OBSERVATION_SCHEMA,
                "owner": self.owner,
            }
            if origin_owner and origin_owner != self.owner:
                observation["origin_owner"] = origin_owner
            observation.update(
                {
                    "run_id": self.run_id,
                    "attempt_id": self.attempt_id,
                    "step_id": step_id,
                    "check_id": check_id,
                    "expected": expected,
                    "actual": actual,
                    "disposition": disposition,
                }
            )
            if issue_key:
                observation["issue_key"] = issue_key
            if reference:
                observation["evidence_ref"] = reference
            observation["version"] = self.version
            if detail_reference:
                observation["details_ref"] = detail_reference
            if parent_step_id:
                observation["parent_step_id"] = parent_step_id
            if not self._append_jsonl(self.out_path, observation, sink=self._sink):
                # The observation did not reach the sidecar; it keeps no identity
                # here, so a later attempt may still deliver it. Save it under the
                # owner's own durable store first.
                if identifier not in self._stashed_ids:
                    self._stash_pending(observation)
                    self._stashed_ids.add(identifier)
                return False
            self.written_ids.add(identifier)
            return True
        finally:
            self._release_identity_lock(lock)

    def _append_jsonl(
        self,
        path: Path,
        record: Mapping[str, Any],
        *,
        sink: Any = None,
    ) -> bool:
        """Append one JSON line. False means the line did not reach the file."""
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        if sink is not None:
            try:
                sink(line)
            except Exception as exc:  # the injected destination refused the write
                self._note_failure(f"feedback_write_failed:{type(exc).__name__}")
                return False
            return True
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)
        except OSError as exc:
            self._note_failure(f"feedback_write_failed:{type(exc).__name__}")
            return False
        return True


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read one bounded JSONL file; unreadable content yields no record."""
    try:
        if not path.is_file():
            return []
        raw = path.read_bytes()[:MAX_SCANNED_BYTES]
    except OSError:
        return []
    records: list[dict[str, Any]] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def run_identity(environ: Mapping[str, str] | None = None) -> tuple[str, str]:
    """This run's (run_id, attempt_id), host-supplied as-is or generated.

    A generated identity is recoverable across processes: it names the owner's
    pending store and binds every observation, so a later process can reconcile
    and redeliver by the same identity. Two concurrent tasks each get their own.
    """
    env = os.environ if environ is None else environ
    run_id = env.get(FEEDBACK_RUN_ID_ENV)
    if not _is_str(run_id):
        run_id = "run-" + uuid.uuid4().hex[:12]
    attempt_id = env.get(FEEDBACK_ATTEMPT_ID_ENV)
    if not _is_str(attempt_id):
        attempt_id = "attempt-1"
    return run_id, attempt_id


def state_root(environ: Mapping[str, str] | None = None) -> Path | None:
    """The owner's own durable state root, or ``None`` when unresolvable.

    A home-less process (no ``USERPROFILE``/``HOME``) has no durable root; the
    run then keeps its business return untouched and publishes nothing rather
    than crashing on feedback.
    """
    env = os.environ if environ is None else environ
    raw = env.get(FEEDBACK_STATE_ROOT_ENV)
    if _is_str(raw) and raw.strip():
        return Path(raw.strip())
    try:
        return default_state_root()
    except (RuntimeError, OSError):
        return None


def _safe_identity_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", value) or "unnamed"


def _identity_lock_for(pending_observations: Path | None) -> Path | None:
    """The shared writer lock for one identity, beside its pending store."""
    if pending_observations is None:
        return None
    return pending_observations.parent / (pending_observations.name + ".identity-lock")


def pending_paths(root: Path, run_id: str, attempt_id: str) -> tuple[Path, Path]:
    """The owner's durable pending observation/detail pair for one identity."""
    name = f"{_safe_identity_part(run_id)}--{_safe_identity_part(attempt_id)}.jsonl"
    observations = root / "pending" / name
    return observations, observations.with_name(observations.name + ".details.jsonl")


def inbox_path(environ: Mapping[str, str] | None = None) -> Path:
    """The public inbox ROOT consumes: ``RESEARCH_FEEDBACK_DIR`` or the default."""
    env = os.environ if environ is None else environ
    raw = env.get(FEEDBACK_DIR_ENV)
    if _is_str(raw) and raw.strip():
        return Path(raw.strip())
    return state_root(env) / "observations"


def _jsonl_bytes(records: list[Mapping[str, Any]]) -> bytes:
    return b"".join(
        (
            json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        for record in records
    )


def _atomic_write(path: Path, payload: bytes) -> None:
    """Same-directory temporary file, closed, then one atomic replace."""
    handle_fd, temp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix="." + path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(handle_fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


class DeliveryRecorder(FeedbackRecorder):
    """Direct-start recorder: save under the owner's store, publish to the inbox.

    The recorder's own out/details pair lives under the durable ``pending/``
    store, so every record is saved before it is confirmed. ``publish`` writes
    one atomically named public bundle ``feedback-<32 lowercase hex>.jsonl``
    with its sanitized details sibling and, only after both are in place, the
    same-basename ``.ready`` mark. The delivery hex is derived from the
    identity, so a redelivery under the same owner/run/attempt converges on
    the same bundle name and the same occurrence is registered exactly once.
    ROOT owns the ``.ack`` confirmation mark; the owner never writes it.
    """

    def __init__(
        self,
        out_path: str | os.PathLike[str],
        *,
        run_id: str,
        attempt_id: str,
        inbox_dir: str | os.PathLike[str],
        **kwargs: Any,
    ) -> None:
        super().__init__(out_path, run_id=run_id, attempt_id=attempt_id, **kwargs)
        self.inbox_dir = Path(inbox_dir)
        raw = "|".join((self.owner, self.run_id, self.attempt_id))
        self.delivery_hex = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
        self._recover_bundle()

    @property
    def bundle_path(self) -> Path:
        return self.inbox_dir / f"feedback-{self.delivery_hex}.jsonl"

    @property
    def bundle_details_path(self) -> Path:
        return self.inbox_dir / f"feedback-{self.delivery_hex}.jsonl.details.jsonl"

    @property
    def ready_path(self) -> Path:
        return self.inbox_dir / f"feedback-{self.delivery_hex}.ready"

    @property
    def ack_path(self) -> Path:
        return self.inbox_dir / f"feedback-{self.delivery_hex}.ack"

    def _delivered_intact(self, bundle: bytes, details: bytes) -> bool:
        try:
            return (
                self.ready_path.is_file()
                and self.bundle_path.is_file()
                and self.bundle_details_path.is_file()
                and self.bundle_path.read_bytes()[:MAX_SCANNED_BYTES] == bundle
                and self.bundle_details_path.read_bytes()[:MAX_SCANNED_BYTES] == details
            )
        except OSError:
            return False

    def _recover_bundle(self) -> None:
        """Redeliver a pending bundle a dead process left behind."""
        try:
            if self.ack_path.exists():
                return
            self.publish()
        except OSError as exc:
            self._note_failure(f"feedback_delivery_failed:{type(exc).__name__}")

    def publish(self) -> bool:
        """Publish saved records as one bundle; idempotent, never raises.

        Rows are deduplicated by occurrence identity under the same-identity
        lock, and a detail is published only when a published observation
        references it, so concurrent same-identity producers or re-runs can
        never make one occurrence appear twice in the public bundle.
        """
        lock = self._acquire_identity_lock()
        try:
            if lock is None and self.identity_lock_path is not None:
                return True  # another same-identity process is publishing now
            if self.ack_path.exists():
                return True
            raw_rows = [
                record
                for record in _read_jsonl(self.out_path)
                if isinstance(record, Mapping)
                and record.get("schema_version") == OBSERVATION_SCHEMA
            ]
            unique_rows: dict[tuple[str, str, str, str], Mapping[str, Any]] = {}
            for record in raw_rows:
                key = (
                    str(record.get("run_id", "")),
                    str(record.get("attempt_id", "")),
                    str(record.get("step_id", "")),
                    str(record.get("check_id", "")),
                )
                unique_rows.setdefault(key, record)
            rows = list(unique_rows.values())
            if not rows:
                return True
            referenced_details = {
                str(record.get("details_ref"))
                for record in rows
                if _is_str(record.get("details_ref"))
            }
            unique_details: dict[str, Mapping[str, Any]] = {}
            for record in _read_jsonl(self.details_path):
                if not isinstance(record, Mapping) or not _is_str(
                    record.get("details_id")
                ):
                    continue
                identifier = str(record["details_id"])
                if identifier not in referenced_details or identifier in unique_details:
                    continue
                unique_details[identifier] = record
            details = list(unique_details.values())
            bundle = _jsonl_bytes(rows)
            details_bytes = _jsonl_bytes(details)
            if self._delivered_intact(bundle, details_bytes):
                return True
            self.inbox_dir.mkdir(parents=True, exist_ok=True)
            _atomic_write(self.bundle_path, bundle)
            _atomic_write(self.bundle_details_path, details_bytes)
            mark = {
                "schema_version": "feedback-delivery-ready/1",
                "owner": self.owner,
                "run_id": self.run_id,
                "attempt_id": self.attempt_id,
                "step_ids": sorted(
                    {str(record.get("step_id", "")) for record in rows}
                ),
                "observations": len(rows),
                "details": len(details),
            }
            _atomic_write(
                self.ready_path,
                json.dumps(mark, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
                + b"\n",
            )
            return True
        except OSError as exc:
            self._note_failure(f"feedback_delivery_failed:{type(exc).__name__}")
            return False
        finally:
            self._release_identity_lock(lock)


def finalize_delivery(recorder: FeedbackRecorder | None) -> None:
    """Confirm this run's delivery at its normal end; never touch the business return."""
    if recorder is None:
        return
    publish = getattr(recorder, "publish", None)
    if not callable(publish):
        return
    try:
        publish()
    except Exception as exc:  # pragma: no cover - defensive, never a crash
        recorder._note_failure(f"feedback_delivery_failed:{type(exc).__name__}")


def recorder_from_env(
    environ: Mapping[str, str] | None = None,
) -> FeedbackRecorder | None:
    """Build the recorder of this run, or ``None`` when feedback is off.

    A non-empty ``RESEARCH_FEEDBACK_OUT`` keeps the host-assigned single-run
    file and its existing detail format, and wins over ``RESEARCH_FEEDBACK_DIR``.
    An explicitly present-but-empty OUT is the historical off-switch. When OUT
    is absent the run is a direct start: records are saved first under the
    owner's own durable state root and published to the public inbox
    (``RESEARCH_FEEDBACK_DIR`` or ``<state root>/observations``).
    ``RESEARCH_FEEDBACK_DISABLED`` turns everything off; the supervisor sets it
    for worker children so exactly one process ever writes a location.
    """
    env = os.environ if environ is None else environ
    disabled = env.get(FEEDBACK_DISABLED_ENV)
    if _is_str(disabled) and disabled.strip():
        return None
    raw = env.get(FEEDBACK_OUT_ENV)
    run_id, attempt_id = run_identity(env)
    root = state_root(env)
    if isinstance(raw, str) and raw.strip():
        pending_observations, _pending_details = (
            pending_paths(root, run_id, attempt_id) if root is not None else (None, None)
        )
        recorder: FeedbackRecorder = FeedbackRecorder(
            raw.strip(),
            run_id=run_id,
            attempt_id=attempt_id,
            pending_path=pending_observations,
            identity_lock_path=_identity_lock_for(pending_observations),
        )
    elif FEEDBACK_OUT_ENV in env:
        # Explicitly disabled by the host: a blank OUT stays an off-switch.
        return None
    elif root is None:
        # No resolvable durable root: publish nothing, keep the business
        # return exactly as it was.
        return None
    else:
        pending_observations, _pending_details = pending_paths(
            root, run_id, attempt_id
        )
        recorder = DeliveryRecorder(
            pending_observations,
            run_id=run_id,
            attempt_id=attempt_id,
            inbox_dir=inbox_path(env),
            identity_lock_path=_identity_lock_for(pending_observations),
        )
    set_active_recorder(recorder)
    return recorder


_ACTIVE_RECORDER: FeedbackRecorder | None = None


def set_active_recorder(recorder: FeedbackRecorder | None) -> None:
    """Remember this process's recorder so a job can read its diagnostics."""
    global _ACTIVE_RECORDER
    _ACTIVE_RECORDER = recorder


def active_diagnostics() -> list[str]:
    """The incomplete diagnostics of this process's recorder, if any."""
    return list(_ACTIVE_RECORDER.diagnostics) if _ACTIVE_RECORDER is not None else []


def emit_observations(
    recorder: FeedbackRecorder | None,
    *,
    request: Any,
    result: Any,
    operation: str,
    native_receipt: Any,
    published_receipt: Any,
    receipt_required: bool,
    evidence_ref: str | None = None,
    branch: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    worker_diagnostics: Any = None,
) -> list[dict[str, Any]]:
    """Project this run's native facts into the cross-repository observation.

    One observation per check, one independent object each. The projection
    never touches stdout or stderr and never raises. ``evidence_ref`` is the
    public evidence of this run — an opaque repository-local identifier the
    host or the controlled replay entry already owns; the redacted detail
    record is referenced separately through ``details_ref``. The child
    occurrence is merged once, into the single detail record the owning
    parent process writes, so a sibling success never masks this failure.
    """
    if recorder is None:
        return []
    try:
        step_id = _step_id(request, operation)
        details = build_details(
            request=request,
            result=result,
            operation=operation,
            native_receipt=native_receipt,
            published_receipt=published_receipt,
            phase="finished",
            branch=branch,
            started_at=started_at,
            finished_at=finished_at,
            attempted=branch == "provider",
            worker=worker_diagnostics,
        )
        checks = (
            evaluate_outcome_naming(result),
            evaluate_period_ownership(request, result),
            evaluate_content_reference(result),
            evaluate_usage(
                native_receipt, published_receipt, receipt_required=receipt_required
            ),
        )
        emitted: list[dict[str, Any]] = []
        # Without a host-supplied reference the native request id is this run's
        # public evidence: an opaque identifier of the native receipt, with no
        # credential, path or command in it. A host-supplied reference is
        # published under the same rule, so an unsafe value can never carry a
        # URL query or forged text into a public artifact.
        reference = safe_public_word(evidence_ref, fallback="evidence")
        if not _is_str(reference) and isinstance(request, Mapping):
            reference = safe_public_word(request.get("request_id"), fallback="request")
        for check in checks:
            recorder.record(
                step_id=step_id,
                check_id=check["check_id"],
                expected=check["expected"],
                actual=check["actual"],
                disposition=check["disposition"],
                issue_key=check.get("issue_key"),
                evidence_ref=reference,
                details=details,
            )
            emitted.append(check)
        return emitted
    except Exception:  # pragma: no cover - defensive, never a crash
        recorder._note_failure("feedback_write_failed:projection_error")
        return []


def _step_id(request: Any, operation: str) -> str:
    """The native stable identifier of the step this observation belongs to.

    A caller-controlled request id is published only as a safe word; a
    URL-shaped or otherwise unsafe value becomes a stable digest word, so the
    step identity stays reproducible without republishing the unsafe text.
    """
    if isinstance(request, Mapping):
        request_id = safe_public_word(request.get("request_id"), fallback="request")
        if request_id is not None:
            return request_id
    return f"{operation}-without-request-id"


def emit_invocation_refusal(
    recorder: FeedbackRecorder | None,
    *,
    entry: str,
    stage: str,
    refusal_code: str,
    location: str,
    argument: str | None = None,
) -> None:
    """Record one refused ordinary invocation: reason and place, honestly.

    A normal refusal is kept as a fact with ``expected`` disposition — it
    needs no repair. The sanitized reason words, the stage and the
    repository-relative location live in the detail record; raw argv never
    does.
    """
    if recorder is None:
        return
    details = build_details(
        request={},
        result={},
        operation="cli",
        native_receipt=None,
        published_receipt=None,
        phase="refused",
        branch="refusal",
        extra={
            "entry": entry,
            "refusal_code": refusal_code,
            "stage": stage,
            "location": location,
            "argument": argument,
        },
    )
    recorder.record(
        step_id=REFUSAL_STEP_ID,
        check_id=CHECK_INVOCATION_REFUSAL,
        expected="refusal_recorded",
        actual="refusal_recorded",
        disposition="expected",
        details=details,
    )


def emit_stored_original_audit(
    recorder: FeedbackRecorder | None, records: list[Mapping[str, Any]]
) -> None:
    """One observation per audited stored original, located by its relative file
    reference from the native report. The transcript body never enters feedback.

    The outcome word is the documented vocabulary of the audit; a documented
    outcome (including ``corrupt``) is a fact with ``expected`` disposition —
    the audit did its job. Every file in the batch is emitted, so a successful
    sibling never masks a failed or unknown one.
    """
    if recorder is None:
        return
    for record in records:
        outcome = record.get("outcome")
        documented = outcome in AUDIT_OUTCOME_VOCABULARY
        extra = {
            name: record.get(name)
            for name in (
                "file",
                "outcome",
                "error_code",
                "ticker",
                "quarter",
                "file_sha256",
                "file_bytes",
                "body_bytes",
                "receipt_kind",
            )
            if record.get(name) is not None
        }
        diagnostics = record.get("diagnostics")
        if diagnostics:
            extra["diagnostics"] = list(diagnostics)
        details = build_details(
            request={},
            result={},
            operation="audit",
            native_receipt=None,
            published_receipt=None,
            phase="finished",
            branch="audit",
            extra=extra,
        )
        recorder.record(
            step_id=str(record.get("file", "")),
            check_id=CHECK_STORED_ORIGINAL,
            expected=str(outcome),
            actual=str(outcome),
            disposition="expected" if documented else "deviation",
            issue_key=None if documented else ISSUE_AUDIT_OUTCOME,
            details=details,
        )


class InvocationRefusal(Exception):
    """One refused ordinary invocation, raised before argparse exits."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class RefusalCapturingParser(argparse.ArgumentParser):
    """ArgumentParser that hands its refusal to the entry instead of exiting.

    The entry prints exactly argparse's native usage and error text to stderr
    and keeps argparse's exit code, so the native CLI contract is unchanged —
    only the missing observation is added.
    """

    def error(self, message: str) -> None:  # noqa: D401 - argparse contract
        raise InvocationRefusal(message)


def refuse_invocation(
    parser: argparse.ArgumentParser,
    refusal: InvocationRefusal,
    recorder: FeedbackRecorder | None,
    *,
    entry: str,
    stage: str,
    refusal_code: str,
    location: str,
    argument: str | None = None,
) -> int:
    """Emit argparse's native refusal output plus the missing observation.

    Returns the exit code argparse would have used, so callers can return it
    without raising.
    """
    parser.print_usage(sys.stderr)
    sys.stderr.write(f"{parser.prog}: error: {refusal.message}\n")
    emit_invocation_refusal(
        recorder,
        entry=entry,
        stage=stage,
        refusal_code=refusal_code,
        location=location,
        argument=argument,
    )
    return 2

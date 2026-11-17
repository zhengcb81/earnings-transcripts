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
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
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

#: The stable names of the checks this projection emits. Every check carries
#: its own name; nothing infers a name from the result words, because two
#: different checks can produce the same words.
CHECK_OUTCOME_NAMING = "outcome-naming"
CHECK_PERIOD_OWNERSHIP = "exact-period-ownership"
CHECK_CONTENT_REFERENCE = "content-reference"
CHECK_USAGE_ACCOUNTING = "usage-accounting"
CHECK_ACTION_STARTED = "action-started"

MAX_SCANNED_BYTES = 8 * 1024 * 1024


def _is_str(value: Any) -> bool:
    return isinstance(value, str) and value != ""


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
    return {name: request[name] for name in keep if name in request}


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
) -> dict[str, Any]:
    """One credential-free detail record holding this run's native facts.

    The record is this repository's own step log: it keeps the public
    operation, the time, the native request id, the result, the branch the
    run took, whether the run was retried or reused, the stable error type
    and the reason chain. The ``started`` phase is written before the action
    can end, so an interrupted run leaves a started record with no result.
    """
    error_code = result.get("error_code") if isinstance(result, Mapping) else None
    return {
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
            "native_receipt": native_receipt if isinstance(native_receipt, Mapping) else None,
            "published_receipt": (
                published_receipt if isinstance(published_receipt, Mapping) else None
            ),
        },
    }


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
        reference = evidence_ref
        if not _is_str(reference) and isinstance(request, Mapping):
            reference = request.get("request_id") if _is_str(
                request.get("request_id")
            ) else None
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
        self.diagnostics_path = self.out_path.with_name(
            self.out_path.name + ".diagnostics.jsonl"
        )
        self.write_failures = 0
        self.diagnostics: list[str] = []
        self.written_ids: set[str] = set()
        self._details_ids: set[str] = set()
        self._scan_existing()

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
            # here, so a later attempt may still deliver it.
            return False
        self.written_ids.add(identifier)
        return True

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


def recorder_from_env(environ: Mapping[str, str] | None = None) -> FeedbackRecorder | None:
    """Build the recorder of this run, or ``None`` when feedback is disabled.

    The output location is supplied by the host (``RESEARCH_FEEDBACK_OUT``);
    the run and attempt numbers may come from the host as well. A run without
    a host-supplied run number is its own run.
    """
    env = os.environ if environ is None else environ
    raw = env.get(FEEDBACK_OUT_ENV)
    if not isinstance(raw, str) or not raw.strip():
        return None
    run_id = env.get(FEEDBACK_RUN_ID_ENV)
    if not _is_str(run_id):
        run_id = "run-" + uuid.uuid4().hex[:12]
    attempt_id = env.get(FEEDBACK_ATTEMPT_ID_ENV)
    if not _is_str(attempt_id):
        attempt_id = "attempt-1"
    recorder = FeedbackRecorder(raw.strip(), run_id=run_id, attempt_id=attempt_id)
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
) -> list[dict[str, Any]]:
    """Project this run's native facts into the cross-repository observation.

    One observation per check, one independent object each. The projection
    never touches stdout or stderr and never raises. ``evidence_ref`` is the
    public evidence of this run — an opaque repository-local identifier the
    host or the controlled replay entry already owns; the redacted detail
    record is referenced separately through ``details_ref``.
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
        # credential, path or command in it.
        reference = evidence_ref
        if not _is_str(reference) and isinstance(request, Mapping):
            reference = request.get("request_id") if _is_str(
                request.get("request_id")
            ) else None
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
    """The native stable identifier of the step this observation belongs to."""
    if isinstance(request, Mapping):
        request_id = request.get("request_id")
        if _is_str(request_id):
            return request_id
    return f"{operation}-without-request-id"

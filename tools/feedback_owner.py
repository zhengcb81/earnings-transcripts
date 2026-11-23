"""Public offline control port for ET retrieval usage publications."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

# The fixed deployment command also works when invoked by absolute script path.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import transcript_feedback as feedback  # noqa: E402
from retrieval_protocol import parse_usage  # noqa: E402

OWNER = feedback.OWNER
ACTIONS = ("triage", "repair", "replay", "report")
IDENTITY = ("owner", "origin_owner", "run_id", "attempt_id", "step_id", "check_id")
PUBLICATION = "et-usage-publication/1"
GENERATION = "usage-recovery-v1"
HEX = re.compile(r"[a-f0-9]{64}")


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode("ascii")).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _is_digest(value):
    return isinstance(value, str) and HEX.fullmatch(value) is not None


def _occurrence(row):
    if (
        isinstance(row, dict)
        and row.get("schema_version") == feedback.OBSERVATION_SCHEMA
        and row.get("owner") == row.get("origin_owner") == OWNER
        and all(isinstance(row.get(key), str) and row[key].strip() for key in IDENTITY)
    ):
        return _digest([row[key] for key in IDENTITY])
    return None


def _validate(action, request):
    if not isinstance(request, dict):
        raise ValueError("expected a feedback action")
    key, body = request.get("action_id"), request.get("payload")
    if (
        request.get("schema_version") != "feedback-action/1"
        or request.get("owner") != OWNER
        or request.get("kind") != action
        or request.get("method") != ("lookup" if action == "reconcile" else "submit")
        or not _is_digest(key)
        or not isinstance(body, dict)
        or request.get("request_digest") != _digest([OWNER, action, key, body])
    ):
        raise ValueError("invalid action envelope")
    occurrence = body.get("occurrence_id")
    if action in ("triage", "reconcile"):
        scope = [occurrence]
    elif action == "repair":
        scope = [body.get("issue_ref"), body.get("generation")]
    else:
        scope = [occurrence, OWNER, body.get("issue_ref"), body.get("fixed_version")]
        if action == "report":
            scope.append(body.get("verdict"))
    if key != _digest([action, OWNER, *scope]):
        raise ValueError("invalid action scope")
    return key, body


def _rows(path):
    try:
        with path.open("rb") as stream:
            for line in stream:
                try:
                    row = json.loads(line, object_pairs_hook=_unique)
                except (ValueError, RecursionError):
                    continue
                if isinstance(row, dict):
                    yield row
    except FileNotFoundError:
        return


def _finished_detail(path, reference):
    for detail in _rows(path.with_name(path.name + ".details.jsonl")):
        if (
            detail.get("details_id") == reference
            and detail.get("phase") == "finished"
            and detail.get("schema_version") == feedback.DETAIL_SCHEMA
        ):
            original = {
                key: value for key, value in detail.items() if key != "details_id"
            }
            if feedback.details_id(original) == reference:
                return detail
    return None


def _native_usage(detail, step_id):
    stored = detail.get("usage")
    request = detail.get("request")
    if not isinstance(stored, dict) or not isinstance(request, dict):
        return None
    receipt = stored.get("native_receipt")
    if not isinstance(receipt, dict):
        return None
    usage = parse_usage(receipt.get("usage"))
    operation = detail.get("operation")
    request_id = request.get("request_id")
    if (
        operation not in ("discover", "fetch", "fetch-candidate")
        or (request_id is not None and not isinstance(request_id, str))
        or step_id != (request_id or f"{operation}-without-request-id")
        or receipt.get("schema_version") != "earnings-retrieval-usage/1"
        or receipt.get("request_id") != request_id
        or receipt.get("usage_complete") is not True
        or usage is None
    ):
        return None
    # Project only the native wire fields. Additional local diagnostics do not
    # become a consumer gate or enter the recovered publication.
    return {
        "schema_version": receipt["schema_version"],
        "request_id": request_id,
        "usage_complete": True,
        "usage": usage,
    }


def _version(publication):
    return f"{PUBLICATION}/{_digest(publication)}"


class OwnerRuntime:
    def __init__(self, db, sidecars, sidecar_dir):
        self.db = feedback.storage_path(db)
        self.sidecars = [feedback.storage_path(path) for path in sidecars]
        self.sidecar_dir = feedback.storage_path(sidecar_dir) if sidecar_dir else None

    def _original(self, row):
        """Bind the exact immutable observation to its native terminal detail."""
        scope = tuple(row[field] for field in IDENTITY[:-1])
        paths = list(self.sidecars)
        if self.sidecar_dir is not None:
            paths.extend(
                path
                for path in sorted(self.sidecar_dir.glob("*.jsonl"))
                if not path.name.endswith((".details.jsonl", ".diagnostics.jsonl"))
            )
        checks, terminal_paths = {}, []
        for path in dict.fromkeys(paths):
            for saved in _rows(path):
                saved.setdefault("origin_owner", saved.get("owner"))
                check = saved.get("check_id")
                if (
                    not isinstance(check, str)
                    or tuple(saved.get(field) for field in IDENTITY[:-1]) != scope
                ):
                    continue
                prior = checks.setdefault(check, saved)
                if prior != saved:
                    return None, None
                if check == feedback.CHECK_OUTCOME_NAMING:
                    terminal_paths.append(path)
        original = checks.get(row["check_id"])
        if original != row:
            return None, None
        terminal = checks.get(feedback.CHECK_OUTCOME_NAMING)
        if terminal is None or not isinstance(terminal.get("details_ref"), str):
            return original, None
        for field in ("schema_version", "version", "evidence_ref"):
            if terminal.get(field) != original.get(field):
                return original, None
        if (
            original["check_id"] != feedback.CHECK_ACTION_STARTED
            and original.get("details_ref") != terminal["details_ref"]
        ):
            return original, None
        for path in dict.fromkeys(terminal_paths):
            detail = _finished_detail(path, terminal["details_ref"])
            if detail is not None:
                return original, detail
        return original, None

    @staticmethod
    def _get(connection, key):
        row = connection.execute(
            "SELECT receipt FROM actions WHERE action_id = ?", (key,)
        ).fetchone()
        return json.loads(row[0]) if row is not None else None

    def read(self, key):
        if not _is_digest(key) or not self.db.is_file():
            return None
        # Quote the native filename as URI data, including Windows UNC or
        # extended-path markers. Read-only lookup must never create a database.
        uri = "file:" + quote(str(self.db.absolute()), safe="/") + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            return self._get(connection, key)

    def reconcile(self, body):
        row = body.get("observation")
        occurrence = _occurrence(row)
        if occurrence is None or occurrence != body.get("occurrence_id"):
            return {"state": "unknown"}
        original, detail = self._original(row)
        if original is None:
            return {"state": "unknown"}
        result = detail.get("result") if detail else None
        status = result.get("status") if isinstance(result, dict) else None
        job = "unconfirmed"
        if isinstance(status, str) and status in feedback.STATUS_VOCABULARY:
            job = "succeeded" if status in ("fetched", "discovered") else "failed"
        native = _native_usage(detail, original["step_id"]) if detail else None
        no_provider = native is not None and (
            native["usage"]["requests_used"]
            == native["usage"]["response_bytes_used"]
            == 0
        )
        known = job != "unconfirmed" and no_provider
        return {
            "state": "done",
            "result": {
                "status": "known" if known else "cost_unknown",
                "job_status": job,
                "cost_state": "not_applicable" if known else "unknown",
                "evidence_ref": original.get(
                    "evidence_ref", f"et-occurrence/{occurrence}"
                ),
            },
        }

    def _triage(self, body):
        row = body.get("observation")
        occurrence = _occurrence(row)
        if occurrence is None or occurrence != body.get("occurrence_id"):
            return {"decision": "needs_evidence"}, None
        original, detail = self._original(row)
        if original is None:
            return {"decision": "needs_evidence"}, None
        if original.get("disposition") == "expected":
            return {"decision": "expected"}, None
        if (
            original.get("disposition") != "deviation"
            or original.get("issue_key") != feedback.ISSUE_USAGE_RECEIPT
            or original["check_id"] != feedback.CHECK_USAGE_ACCOUNTING
            or original.get("expected") != "usage_known"
            or original.get("actual") != "usage_missing"
            or detail is None
        ):
            return {"decision": "needs_evidence"}, None
        native = _native_usage(detail, original["step_id"])
        if native is None or detail["usage"].get("published_receipt") is not None:
            return {"decision": "needs_evidence"}, None
        publication = {
            "schema_version": PUBLICATION,
            "occurrence_id": occurrence,
            "run_id": original["run_id"],
            "attempt_id": original["attempt_id"],
            "step_id": original["step_id"],
            "observed_version": original.get("version"),
            "original_evidence_ref": original.get("evidence_ref"),
            "native_details_id": detail["details_id"],
            "usage_receipt": native,
        }
        return {
            "decision": "repair",
            "issue_ref": f"{PUBLICATION}/{occurrence}",
            "generation": GENERATION,
        }, publication

    def _repair(self, connection, key, body):
        issue = body.get("issue_ref")
        prefix = PUBLICATION + "/"
        occurrence = (
            issue[len(prefix) :]
            if isinstance(issue, str) and issue.startswith(prefix)
            else None
        )
        if not _is_digest(occurrence) or body.get("generation") != GENERATION:
            return None, None
        triage = self._get(connection, _digest(["triage", OWNER, occurrence]))
        if triage is None or triage["result"].get("issue_ref") != issue:
            return None, None
        publication = triage["publication"]
        return {
            "fixed_version": _version(publication),
            "evidence_ref": f"{PUBLICATION}/{key}",
        }, publication

    def _replay(self, connection, body):
        row = body.get("observation")
        occurrence = _occurrence(row)
        issue, version = body.get("issue_ref"), body.get("fixed_version")
        if (
            occurrence is None
            or occurrence != body.get("occurrence_id")
            or issue != f"{PUBLICATION}/{occurrence}"
            or body.get("issue_owner") != OWNER
            or body.get("acquisition") != "reuse_only"
            or body.get("original_occurrence_id")
            != row.get("original_occurrence_id", occurrence)
        ):
            return None
        triage = self._get(connection, _digest(["triage", OWNER, occurrence]))
        repaired = self._get(connection, _digest(["repair", OWNER, issue, GENERATION]))
        if (
            triage is None
            or repaired is None
            or repaired["result"].get("fixed_version") != version
            or triage["request"]["payload"]["observation"] != row
        ):
            return {"verdict": "unknown"}
        publication = repaired["publication"]
        check = feedback.evaluate_usage(
            triage["publication"]["usage_receipt"],
            publication["usage_receipt"],
            receipt_required=True,
        )
        verified = (
            check["actual"] == row["expected"]
            and publication == triage["publication"]
            and _version(publication) == version
        )
        return {
            "verdict": "verified" if verified else "regressed",
            "verified_version": version,
            "evidence_ref": repaired["result"]["evidence_ref"],
        }

    def _report(self, connection, body):
        occurrence = body.get("occurrence_id")
        issue, version = body.get("issue_ref"), body.get("fixed_version")
        prior = self._get(
            connection, _digest(["replay", OWNER, occurrence, OWNER, issue, version])
        )
        if (
            prior is None
            or body.get("origin_owner") != OWNER
            or body.get("verdict") not in ("verified", "regressed")
        ):
            return None
        result = prior["result"]
        if (
            result.get("verified_version") != version
            or result.get("verdict") != body["verdict"]
            or result.get("evidence_ref") != body.get("evidence_ref")
            or prior["request"]["payload"].get("original_occurrence_id")
            != body.get("original_occurrence_id")
        ):
            return None
        return {"recorded": True}

    def dispatch(self, action, request):
        key, body = _validate(action, request)
        if action == "reconcile":
            return self.reconcile(body)
        self.db.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db, timeout=20)) as connection, connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS actions (action_id TEXT PRIMARY KEY, receipt TEXT NOT NULL)"
            )
            connection.execute("BEGIN IMMEDIATE")
            saved = self._get(connection, key)
            if saved is not None:
                if _digest(saved["request"]) != _digest(request):
                    raise ValueError("conflicting action request")
                return {"result": saved["result"]}

            publication = None
            if action == "triage":
                result, publication = self._triage(body)
            elif action == "repair":
                result, publication = self._repair(connection, key, body)
            elif action == "replay":
                result = self._replay(connection, body)
            else:
                result = self._report(connection, body)
            if result is None:
                return {"state": "unknown"}
            receipt = {"request": request, "result": result}
            if publication is not None:
                receipt["publication"] = publication
            connection.execute(
                "INSERT INTO actions VALUES (?, ?)", (key, _json(receipt))
            )
            return {"result": result}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--sidecar", action="append", type=Path, default=[])
    parser.add_argument("--sidecar-dir", type=Path)
    commands = parser.add_subparsers(dest="action", required=True)
    for action in (*ACTIONS, "reconcile"):
        commands.add_parser(action)
    for action in ("get-action", "get-usage"):
        commands.add_parser(action).add_argument("action_id")
    args = parser.parse_args(argv)
    owner = OwnerRuntime(args.db, args.sidecar, args.sidecar_dir)
    try:
        if args.action in ("get-action", "get-usage"):
            receipt = owner.read(args.action_id)
            payload = {}
            if receipt is not None:
                if args.action == "get-action":
                    payload = {
                        "receipt": {
                            name: receipt[name] for name in ("request", "result")
                        }
                    }
                elif receipt["request"]["kind"] == "repair":
                    publication = receipt.get("publication")
                    if publication is not None and _version(publication) == receipt[
                        "result"
                    ].get("fixed_version"):
                        payload = {"publication": publication}
        else:
            payload = owner.dispatch(
                args.action, json.load(sys.stdin, object_pairs_hook=_unique)
            )
        print(_json(payload))
    except (ValueError, OSError, sqlite3.Error, RecursionError) as error:
        print(_json({"state": "unknown", "error_type": type(error).__name__}))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

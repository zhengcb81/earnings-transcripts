"""Native feedback owner tests. All ET provider calls are forbidden here."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

import transcript_feedback as feedback

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "docs" / "plans" / "feedback-loop-2026-11-17" / "samples"
OWNER = feedback.OWNER
IDENTITY = ("owner", "origin_owner", "run_id", "attempt_id", "step_id", "check_id")


def digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def row_for(path, check):
    return next(
        {**row, "origin_owner": OWNER}
        for row in (json.loads(line) for line in path.read_bytes().splitlines())
        if row["check_id"] == check
    )


def occurrence(row):
    return digest([row[key] for key in IDENTITY])


def action(kind, identity, payload):
    key = digest([kind, OWNER, *identity])
    return {
        "schema_version": "feedback-action/1",
        "owner": OWNER,
        "action_id": key,
        "kind": kind,
        "method": "lookup" if kind == "reconcile" else "submit",
        "payload": payload,
        "request_digest": digest([OWNER, kind, key, payload]),
    }


@pytest.fixture
def host(tmp_path):
    # Inject a network tripwire into each real subprocess through its test-only
    # sitecustomize. The worker environment carries no provider/translator secrets.
    guard = tmp_path / "guard"
    guard.mkdir()
    (guard / "sitecustomize.py").write_text(
        "import sys\n"
        "def check(event, args):\n"
        "    if event in ('socket.connect', 'socket.getaddrinfo'):\n"
        "        raise RuntimeError('network forbidden in ET feedback replay')\n"
        "sys.addaudithook(check)\n",
        encoding="utf-8",
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper()
        in {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC"}
    }
    env.update(PYTHONPATH=str(guard), PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    return tmp_path / "et-feedback.sqlite", env


def command(db, sidecar, name, *args):
    return [
        sys.executable,
        "-B",
        str(ROOT / "tools" / "feedback_owner.py"),
        "--db",
        str(db),
        "--sidecar-dir",
        str(sidecar.parent),
        name,
        *args,
    ]


def call(host, sidecar, name, payload=None, *args):
    db, env = host
    result = subprocess.run(
        command(db, sidecar, name, *args),
        cwd=sidecar.parent,
        input=json.dumps(payload).encode("utf-8"),
        capture_output=True,
        timeout=15,
        env=env,
        check=True,
    )
    assert not result.stderr
    return json.loads(result.stdout)


def copy_case(tmp_path, name):
    source = SAMPLES / (name + ".jsonl")
    sidecar = tmp_path / source.name
    shutil.copyfile(source, sidecar)
    shutil.copyfile(str(source) + ".details.jsonl", str(sidecar) + ".details.jsonl")
    return sidecar


def lose_reply(host, sidecar, request):
    db, env = host
    # The committed native action has returned to this short-lived caller.
    # Kill the caller before it forwards the reply to its supervisor.
    lost = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "import os, subprocess, sys; "
            "reply = subprocess.run(sys.argv[1:], input=sys.stdin.buffer.read(), capture_output=True); "
            "if_bad = 91 if reply.returncode != 0 or not reply.stdout else 79; "
            "os._exit(if_bad)",
            *command(db, sidecar, request["kind"]),
        ],
        cwd=sidecar.parent,
        input=json.dumps(request).encode("utf-8"),
        env=env,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert lost.returncode == 79 and not lost.stdout and not lost.stderr


def test_recovers_publication_and_report_after_lost_replies(tmp_path, host):
    sidecar = copy_case(tmp_path, "deviation-usage-receipt")
    details_path = Path(str(sidecar) + ".details.jsonl")
    original = (sidecar.read_bytes(), details_path.read_bytes())
    row = row_for(sidecar, feedback.CHECK_USAGE_ACCOUNTING)
    key = occurrence(row)
    observation = {"occurrence_id": key, "observation": row}
    assert not call(host, sidecar, "get-action", None, "0" * 64)
    assert not host[0].exists()

    triage = action("triage", [key], observation)
    verdict = call(host, sidecar, "triage", triage)["result"]
    assert verdict["decision"] == "repair"
    issue = verdict["issue_ref"]
    generation = verdict["generation"]

    repair = action(
        "repair", [issue, generation], {"issue_ref": issue, "generation": generation}
    )
    lose_reply(host, sidecar, repair)
    repair_proof = call(host, sidecar, "get-action", None, repair["action_id"])[
        "receipt"
    ]
    assert repair_proof["request"] == repair
    assert call(host, sidecar, "repair", repair) == {"result": repair_proof["result"]}
    version = repair_proof["result"]["fixed_version"]
    recovered = call(host, sidecar, "get-usage", None, repair["action_id"])[
        "publication"
    ]
    assert version.endswith("/" + digest(recovered))

    saved = json.loads(original[1].splitlines()[-1])
    # Same public CLI that now publishes exactly one native use receipt. The
    # discover/source-payload combination is rejected before any provider work.
    native = subprocess.run(
        [
            sys.executable,
            "-B",
            str(ROOT / "transcript_tool.py"),
            "--request-stdin",
            "--operation",
            "discover",
            "--include-source-payload",
            "--report-usage",
        ],
        input=json.dumps(saved["request"]).encode("utf-8"),
        capture_output=True,
        check=True,
        timeout=15,
        env=host[1],
        cwd=tmp_path,
    )
    assert json.loads(native.stdout)["error_code"] == saved["result"]["error_code"]
    assert native.stderr.count(b"\n") == 1
    assert (
        recovered["usage_receipt"]
        == saved["usage"]["native_receipt"]
        == json.loads(native.stderr)
    )
    assert recovered["usage_receipt"]["usage"]["requests_used"] == 0
    assert recovered["occurrence_id"] == key

    replay = action(
        "replay",
        [key, OWNER, issue, version],
        {
            **observation,
            "original_occurrence_id": key,
            "issue_owner": OWNER,
            "issue_ref": issue,
            "fixed_version": version,
            "acquisition": "reuse_only",
        },
    )
    checked = call(host, sidecar, "replay", replay)["result"]
    assert checked["verdict"] == "verified" and checked["verified_version"] == version
    report = action(
        "report",
        [key, OWNER, issue, version, "verified"],
        {
            "occurrence_id": key,
            "original_occurrence_id": key,
            "origin_owner": OWNER,
            "issue_ref": issue,
            "fixed_version": version,
            "verdict": "verified",
            "evidence_ref": checked["evidence_ref"],
        },
    )
    lose_reply(host, sidecar, report)
    assert call(host, sidecar, "get-action", None, report["action_id"]) == {
        "receipt": {"request": report, "result": {"recorded": True}},
    }
    assert call(host, sidecar, "report", report) == {"result": {"recorded": True}}
    with closing(sqlite3.connect(host[0])) as connection:
        assert connection.execute("SELECT COUNT(*) FROM actions").fetchone() == (4,)
    assert (sidecar.read_bytes(), details_path.read_bytes()) == original


@pytest.mark.parametrize("fault", ["attempt", "detail", "conflict"])
def test_untrusted_or_ambiguous_history_cannot_supply_a_repair(tmp_path, host, fault):
    sidecar = copy_case(tmp_path, "deviation-usage-receipt")
    row = row_for(sidecar, feedback.CHECK_USAGE_ACCOUNTING)
    if fault == "attempt":
        row["attempt_id"] = "different-attempt"
    elif fault == "detail":
        details = Path(str(sidecar) + ".details.jsonl")
        details.write_bytes(
            details.read_bytes().replace(b'"requests_used":0', b'"requests_used":5')
        )
    else:
        conflicting = {**row, "actual": "usage_unknown"}
        (tmp_path / "ambiguous.jsonl").write_text(
            json.dumps(conflicting) + "\n", encoding="utf-8"
        )
    key = occurrence(row)
    request = action("triage", [key], {"occurrence_id": key, "observation": row})
    assert call(host, sidecar, "triage", request) == {
        "result": {"decision": "needs_evidence"}
    }


@pytest.mark.parametrize("has_terminal", [False, True])
def test_read_only_reconcile_preserves_unknown_and_does_not_create_a_ledger(
    tmp_path, host, has_terminal
):
    sidecar = copy_case(tmp_path, "unknown-usage")
    start = row_for(sidecar, feedback.CHECK_ACTION_STARTED)
    if not has_terminal:
        sidecar.write_text(json.dumps(start) + "\n", encoding="utf-8")
    key = occurrence(start)
    request = action("reconcile", [key], {"occurrence_id": key, "observation": start})
    reply = call(host, sidecar, "reconcile", request)
    assert reply["state"] == "done"
    assert reply["result"]["job_status"] == (
        "failed" if has_terminal else "unconfirmed"
    )
    assert reply["result"]["status"] == "cost_unknown"
    assert reply["result"]["cost_state"] == "unknown"
    assert reply["result"]["evidence_ref"] == start["evidence_ref"]
    assert not host[0].exists()


def test_known_metered_request_preserves_original_usage_and_unknown_price(
    tmp_path, host
):
    sidecar = tmp_path / "metered.jsonl"
    recorder = feedback.FeedbackRecorder(
        sidecar, run_id="metered", attempt_id="original"
    )
    native = {
        "schema_version": "earnings-retrieval-usage/1",
        "request_id": "previous-provider-request",
        "usage_complete": True,
        "usage": {"requests_used": 1, "response_bytes_used": 812, "exhausted": None},
    }
    feedback.emit_observations(
        recorder,
        request={"request_id": native["request_id"]},
        result={"status": "discovered"},
        operation="discover",
        native_receipt=native,
        published_receipt=None,
        receipt_required=True,
        evidence_ref="et-case:previous-provider-request",
    )
    row = row_for(sidecar, feedback.CHECK_USAGE_ACCOUNTING)
    key = occurrence(row)
    observation = {"occurrence_id": key, "observation": row}
    check = action("reconcile", [key], observation)
    assert call(host, sidecar, "reconcile", check)["result"] == {
        "job_status": "succeeded",
        "cost_state": "unknown",
        "status": "cost_unknown",
        "evidence_ref": row["evidence_ref"],
    }
    assert not host[0].exists()

    old_input = action("triage", [key], observation)
    triage = call(host, sidecar, "triage", old_input)["result"]
    issue, generation = triage["issue_ref"], triage["generation"]
    repair = action(
        "repair", [issue, generation], {"issue_ref": issue, "generation": generation}
    )
    assert call(host, sidecar, "repair", repair)["result"]["fixed_version"]
    assert (
        call(host, sidecar, "get-usage", None, repair["action_id"])["publication"][
            "usage_receipt"
        ]
        == native
    )

    forged = action(
        "triage", [key], {**observation, "observation": {**row, "run_id": "new"}}
    )
    db, env = host
    result = subprocess.run(
        command(db, sidecar, "triage"),
        cwd=tmp_path,
        input=json.dumps(forged).encode("utf-8"),
        capture_output=True,
        timeout=15,
        env=env,
        check=False,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["state"] == "unknown"
    assert call(host, sidecar, "get-action", None, old_input["action_id"]) == {
        "receipt": {"request": old_input, "result": triage},
    }
    assert call(host, sidecar, "reconcile", check)["result"]["cost_state"] == "unknown"

"""ET S2. The provider dependency fault crosses the real worker and F4 boundaries."""

from __future__ import annotations

import errno
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_f4_ordinary_failures import ROOT, second_process_readback
from tests.test_feedback_owner import action, occurrence
from tests.test_retrieval_runtime import get_urls
from tests.test_transcript_api import FMP_URL, make_request


PRODUCER = """
import sys
from pathlib import Path

import transcript_tool
from transcript_provider_settings import ProviderSettings

raise SystemExit(transcript_tool.main(
    ["--request-stdin", *sys.argv[2:]],
    _provider_settings=ProviderSettings(motley_fool_enabled=True, fmp_enabled=False),
    _retrieval_temp_root=Path(sys.argv[1]),
    _feedback_evidence_ref="et-s2:dependency-case",
))
"""


CLEANUP_PRODUCER = """
import errno
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

import transcript_tool
from tools.run_request_budget_e2e import FMP_URL, FakeResponse, LAUNCHER, fake_spec, padded_payload
from transcript_provider_settings import ProviderSettings

runtime = Path(sys.argv[1])
raw = padded_payload(1024, ticker="NVDA", quarter=2)
spec = fake_spec(
    {FMP_URL: FakeResponse(FMP_URL, raw, content_type="application/json")},
    calls_file=runtime.parent / "calls.jsonl",
)
remove = shutil.rmtree

def deny_worker_cleanup(path):
    if Path(path).parent == runtime:
        raise PermissionError(errno.EACCES, "FAKE_ET_S2_CLEANUP_TEXT", "FAKE_ET_S2_PRIVATE_PATH")
    return remove(path)

os.environ["FMP_API_KEY"] = "FAKE_ET_S2_FMP_KEY"
with patch.object(shutil, "rmtree", deny_worker_cleanup):
    raise SystemExit(transcript_tool.main(
        ["--request-stdin", *sys.argv[2:]],
        _provider_settings=ProviderSettings(motley_fool_enabled=False, fmp_enabled=True),
        _retrieval_launcher=LAUNCHER,
        _retrieval_spec=spec,
        _retrieval_temp_root=runtime,
        _feedback_evidence_ref="et-s2:cleanup-case",
    ))
"""

BOOTSTRAP_TRIPWIRE = """
import os
from pathlib import Path
import sys

def refuse_network(event, args):
    # HTTP libraries may create a socket only to check local IPv6 support.
    if event in ("socket.connect", "socket.getaddrinfo", "socket.sendto"):
        Path(os.environ["ET_S2_NETWORK_MARKER"]).touch()
        raise RuntimeError("network blocked by the ET S2 test")
    if (event == "os.rename" and os.environ.get("ET_S5_DENY_DIAGNOSTIC")
            and Path(args[1]).name == "worker_error.json"):
        raise PermissionError(13, "FAKE_ET_S5_WRITE_DENIED", "FAKE_ET_S5_PATH")

sys.addaudithook(refuse_network)

class MissingDependency:
    def find_spec(self, name, path=None, target=None):
        if name == os.environ["ET_S2_DEPENDENCY"] and Path(sys.argv[0]).name == "retrieval_worker.py":
            marker = Path(os.environ["ET_S2_BOOTSTRAP_MARKER"])
            with marker.open("ab") as stream:
                stream.write(b"import-attempt\\n")
            # Deliberately sensitive-looking synthetic text. Nothing here is a credential.
            try:
                raise LookupError('{"x-api-key":"FAKE_ET_S2_QUERY_713"}')
            except LookupError as cause:
                raise ModuleNotFoundError("Authorization: Bearer FAKE_ET_S2_BEARER_713") from cause
        return None

sys.meta_path.insert(0, MissingDependency())
"""


def run(env: dict, root: Path, args: list, *, stdin: bytes = b"") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-B", *map(str, args)],
        cwd=root,
        env=env,
        input=stdin,
        capture_output=True,
        timeout=25,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=True,
    )


def isolated_env(tmp_path: Path, dependency: str = "") -> dict[str, str]:
    guard = tmp_path / "guard"
    guard.mkdir()
    (guard / "sitecustomize.py").write_text(BOOTSTRAP_TRIPWIRE, encoding="utf-8")
    out = tmp_path / "observations.jsonl"
    env = {
        name: os.environ[name]
        for name in ("PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC")
        if name in os.environ
    }
    env.update(
        PYTHONPATH=os.pathsep.join((str(guard), str(ROOT))),
        PYTHONUTF8="1",
        PYTHONDONTWRITEBYTECODE="1",
        HOME=str(tmp_path),
        USERPROFILE=str(tmp_path),
        TEMP=str(tmp_path),
        TMP=str(tmp_path),
        TMPDIR=str(tmp_path),
        RESEARCH_FEEDBACK_OUT=str(out),
        RESEARCH_FEEDBACK_STATE_ROOT=str(tmp_path / "state"),
        RESEARCH_FEEDBACK_RUN_ID="et-s2-startup",
        RESEARCH_FEEDBACK_ATTEMPT_ID="one",
        ET_S2_DEPENDENCY=dependency,
        ET_S2_BOOTSTRAP_MARKER=str(tmp_path / "import-attempts.txt"),
        ET_S2_NETWORK_MARKER=str(tmp_path / "unexpected-network"),
    )
    return env


@pytest.mark.parametrize("dependency, report_usage", [("requests", True), ("bs4", True), ("requests", False)])
def test_missing_provider_dependency_is_cold_readable_and_never_dispatched(tmp_path, dependency, report_usage):
    env = isolated_env(tmp_path, dependency)
    out = tmp_path / "observations.jsonl"
    runtime = tmp_path / "runtime"
    request = make_request(provider="motley_fool", request_id=f"et-s2-{dependency}", timeout_seconds=12)
    flags = ["--report-usage"] if report_usage else []
    completed = run(env, tmp_path, ["-c", PRODUCER, runtime, *flags], stdin=json.dumps(request).encode())
    response = json.loads(completed.stdout)
    assert (response["status"], response["error_code"]) == ("provider_error", "retrieval_worker_failure")
    if report_usage:
        # Filing-fetch reads stderr as a whole, while stdout is the content wire.
        assert json.loads(completed.stderr) == {
            "schema_version": "earnings-retrieval-usage/1",
            "request_id": request["request_id"],
            "usage_complete": False,
            "usage": None,
        }
    else:
        assert b"worker_phase=bootstrap" in completed.stderr
        assert b"ModuleNotFoundError" in completed.stderr
        assert b"earnings-retrieval-usage/1" not in completed.stderr
    assert "worker" not in response

    first = second_process_readback(env, out)
    second = second_process_readback(env, out)
    assert first == second
    finished, = (item for item in first["details"] if item["phase"] == "finished")
    worker = finished["worker"]
    assert worker["worker_phase"] == "bootstrap"
    assert worker["supervisor_stage"] == "worker_exit"
    assert worker["worker_exit_code"] == 1
    assert isinstance(worker["worker_pid"], int)
    assert worker["worker_exceptions"] == ["ModuleNotFoundError", "LookupError"]
    trace = worker["worker_exception_trace"]
    assert trace[1]["relation"] == "cause"
    assert any(frame["file"] == "retrieval_worker.py" and frame["line"] > 0
               for frame in trace[0]["frames"])
    row, = (item for item in first["observations"] if item["check_id"] == "usage-accounting")
    assert row["details_ref"] == finished["details_id"]
    row = {**row, "origin_owner": row["owner"]}
    key = occurrence(row)
    query = action("reconcile", [key], {"occurrence_id": key, "observation": row})
    db = tmp_path / "owner.sqlite"
    owner = run(env, tmp_path, [ROOT / "tools/feedback_owner.py", "--db", db, "--sidecar", out, "reconcile"],
                stdin=json.dumps(query).encode())
    assert json.loads(owner.stdout) == {
        "state": "done",
        "result": {
            "status": "cost_unknown",
            "job_status": "failed",
            "cost_state": "unknown",
            "evidence_ref": row["evidence_ref"],
        },
    }
    assert not db.exists()
    assert (tmp_path / "import-attempts.txt").read_bytes() == b"import-attempt\n"
    assert not (tmp_path / "unexpected-network").exists()
    assert list(runtime.iterdir()) == []
    public = completed.stdout + completed.stderr + owner.stdout + json.dumps(first).encode()
    assert b"FAKE_ET_S2" not in public
    assert b"Authorization: Bearer" not in public


@pytest.mark.parametrize("report_usage", [True, False])
def test_cleanup_error_preserves_fetched_transcript_and_known_receipt(tmp_path, report_usage):
    env = isolated_env(tmp_path)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    request = make_request(
        provider="fmp", exchange="nasdaq", max_body_bytes=1000,
        ticker="NVDA", fiscal_quarter=2, max_response_bytes=2048,
        request_id="et-s2-cleanup", timeout_seconds=12,
    )
    flags = ["--report-usage"] if report_usage else []
    try:
        completed = run(env, tmp_path, ["-c", CLEANUP_PRODUCER, runtime, *flags],
                        stdin=json.dumps(request).encode())
        assert json.loads(completed.stdout)["status"] == "fetched"
        receipt = {
            "schema_version": "earnings-retrieval-usage/1",
            "request_id": request["request_id"],
            "usage_complete": True,
            "usage": {"requests_used": 1, "response_bytes_used": 1024, "exhausted": None},
        }
        if report_usage:
            assert json.loads(completed.stderr) == receipt
        else:
            assert b"retrieval_cleanup_failed" in completed.stderr
            assert b"PermissionError" in completed.stderr
            assert b"earnings-retrieval-usage/1" not in completed.stderr
        readback = second_process_readback(env, tmp_path / "observations.jsonl")
        finished, = (item for item in readback["details"] if item["phase"] == "finished")
        worker = finished["worker"]
        assert worker["supervisor_stage"] == "cleanup"
        assert worker["worker_exit_code"] == 0
        assert isinstance(worker["worker_pid"], int)
        assert worker["cleanup_failure"] == {
            "stage": "remove_workdir",
            "exception_type": "PermissionError",
            "errno": errno.EACCES,
        }
        assert finished["usage"]["native_receipt"] == receipt
        assert finished["usage"]["published_receipt"] == (receipt if report_usage else None)
        assert b"FAKE_ET_S2" not in completed.stdout + completed.stderr + json.dumps(readback).encode()
        assert get_urls(tmp_path / "calls.jsonl") == [FMP_URL]
        assert not (tmp_path / "unexpected-network").exists()
        assert len(list(runtime.iterdir())) == 1
    finally:
        for leftover in runtime.iterdir():
            assert leftover.resolve().parent == runtime.resolve()
            assert leftover.name.startswith("et-retrieval-") and not leftover.is_symlink()
            shutil.rmtree(leftover)
    assert list(runtime.iterdir()) == []


@pytest.mark.parametrize("dependency, deny_diagnostic, deny_log", [
    ("retrieval_budget", False, False),
    ("requests", True, False),
    ("retrieval_budget", False, True),
])
def test_worker_stderr_fallback_keeps_machine_receipt_and_cold_evidence(
    tmp_path, dependency, deny_diagnostic, deny_log,
):
    env = isolated_env(tmp_path, dependency)
    if deny_diagnostic:
        env["ET_S5_DENY_DIAGNOSTIC"] = "1"
    if deny_log:
        (tmp_path / "state").mkdir()
        (tmp_path / "state" / "logs").write_bytes(b"blocked")
    runtime = tmp_path / "runtime"
    request = make_request(
        provider="motley_fool", request_id=f"et-s5-{dependency}", timeout_seconds=12,
    )
    completed = run(
        env, tmp_path, ["-c", PRODUCER, runtime, "--report-usage"],
        stdin=json.dumps(request).encode(),
    )
    assert json.loads(completed.stdout)["error_code"] == "retrieval_worker_failure"
    receipt = {
        "schema_version": "earnings-retrieval-usage/1",
        "request_id": request["request_id"],
        "usage_complete": False,
        "usage": None,
    }
    assert completed.stderr == json.dumps(receipt, separators=(",", ":")).encode() + os.linesep.encode()
    readback = second_process_readback(env, tmp_path / "observations.jsonl")
    finished, = (item for item in readback["details"] if item["phase"] == "finished")
    worker = finished["worker"]
    assert worker["worker_stderr_type"] == "ModuleNotFoundError"
    assert worker["worker_exit_code"] == 1
    assert worker["worker_phase"] is None
    if deny_diagnostic:
        assert worker["worker_diagnostic_write_error"] == "PermissionError"
    path = tmp_path / "state" / "logs" / "runtime_errors.log"
    log = b"" if deny_log else path.read_bytes()
    if deny_log:
        assert not path.exists()
    else:
        assert b"ModuleNotFoundError" in log and b"et-s2-startup" in log
    assert b"FAKE_ET_" not in log + completed.stdout + completed.stderr + json.dumps(readback).encode()
    assert (tmp_path / "import-attempts.txt").read_bytes() == b"import-attempt\n"
    assert not (tmp_path / "unexpected-network").exists()
    assert not list(runtime.iterdir())

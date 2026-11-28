"""Native ET owner entry checks with only disposable input and loopback HTTP."""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

import pytest


ROOT = Path(__file__).resolve().parents[1]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SECRETS = (
    "fake-et-p2-bearer",
    "ZXRwMjpzZWNyZXQ=",
    "et-p2-url-password",
    "sk-ETP2FakeOpenAI123",
    "tvly-ETP2FakeTavily123",
    "ghp_ETP2FakeGithub123",
    "AIzaETP2FakeGoogle123",
)
PRIVATE_ARGUMENT = (
    f"Authorization:\r\n Bearer {SECRETS[0]}\nAuthorization:\r\n Basic {SECRETS[1]}\n"
    f"https://et:{SECRETS[2]}@invalid.example/resource " + " ".join(SECRETS[3:])
)
ENTRY_FILES = (
    ("transcript_tool.py", "transcript_tool"),
    ("scraper.py", "scraper"),
    ("transcript_audit.py", "transcript_audit"),
    ("translate.py", "translate"),
    ("make_interleaved.py", "make_interleaved"),
    ("reader.py", "reader"),
    ("tools/feedback_owner.py", "feedback_owner"),
    ("retrieval_worker.py", "retrieval_worker"),
)


@pytest.fixture
def isolated(tmp_path):
    runtime = tmp_path / "et"
    runtime.mkdir()
    for source in ROOT.glob("*.py"):
        shutil.copyfile(source, runtime / source.name)
    for directory, name in (("templates", "index.html"), ("tools", "feedback_owner.py")):
        (runtime / directory).mkdir()
        shutil.copyfile(ROOT / directory / name, runtime / directory / name)

    data = tmp_path / "data"
    data.mkdir()
    (data / "companies.txt").write_text("ETPX|测试公司|ET Example|nasdaq\n", encoding="utf-8")
    (runtime / "config.yaml").write_text(json.dumps({
        "paths": {"base_dir": str(data), "companies_file": "companies.txt",
                  "transcripts_dir": "transcripts"},
    }), encoding="utf-8")
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "LANG", "TMP", "TEMP", "TMPDIR"}}
    env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1",
               RESEARCH_FEEDBACK_STATE_ROOT=str(tmp_path / "state"),
               RESEARCH_FEEDBACK_OUT=str(tmp_path / "output" / "observations.jsonl"),
               RESEARCH_FEEDBACK_RUN_ID="run-et-p2", RESEARCH_FEEDBACK_ATTEMPT_ID="attempt-et-p2")
    (tmp_path / "output").mkdir()
    return runtime, env, data


def invoke(runtime, env, script, *arguments, body=b""):
    return subprocess.run(
        [sys.executable, "-B", str(runtime / script), *arguments],
        input=body, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=runtime, env=env, timeout=20, creationflags=NO_WINDOW, check=False,
    )


def errors(env):
    directory = Path(env["RESEARCH_FEEDBACK_STATE_ROOT"]) / "logs"
    for path in sorted(directory.glob("runtime_errors.log*")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def assert_private_absent(*bodies):
    for body in bodies:
        assert all(secret not in body for secret in SECRETS)


@pytest.mark.parametrize(("script", "entry"), ENTRY_FILES)
def test_native_invalid_arguments_are_correlated_and_redacted(isolated, script, entry):
    runtime, env, _ = isolated
    result = invoke(runtime, env, script, "--unknown-et-p2", PRIVATE_ARGUMENT)
    assert result.returncode == 2
    assert result.stdout == b""
    rows = list(errors(env))
    assert any(row["event"] in {"invocation_failure", "invocation_refusal"}
               and row["facts"].get("entry") == entry and row["run_id"] == "run-et-p2"
               and row["attempt_id"] == "attempt-et-p2" for row in rows)
    assert_private_absent(result.stderr.decode("utf-8", "replace"), json.dumps(rows))


def test_real_machine_failure_keeps_the_exact_usage_envelope(isolated):
    runtime, env, _ = isolated
    result = invoke(
        runtime, env, "transcript_tool.py", "--request-stdin", "--report-usage",
        body=("bad json " + PRIVATE_ARGUMENT).encode(),
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["error_code"] == "invalid_json"
    receipt = json.loads(result.stderr)
    assert receipt["usage"]["requests_used"] == receipt["usage"]["response_bytes_used"] == 0
    rows = list(errors(env))
    assert any(
        any(cause["type"] == "JSONDecodeError" and cause["frames"]
            for cause in row["facts"].get("exception_trace", []))
        for row in rows
    )
    assert_private_absent(result.stdout.decode(), result.stderr.decode(), json.dumps(rows))


def test_two_unwritable_diagnostic_destinations_keep_native_refusal(isolated, tmp_path):
    runtime, env, _ = isolated
    for key in ("RESEARCH_FEEDBACK_STATE_ROOT", "RESEARCH_FEEDBACK_OUT"):
        blocked = tmp_path / (key.lower() + "-block")
        blocked.write_bytes(b"preserve")
        env[key] = str(blocked / "destination")
    result = invoke(runtime, env, "make_interleaved.py", "--unknown-et-p2", PRIVATE_ARGUMENT)
    assert result.returncode == 2
    assert result.stdout == b""
    stderr = result.stderr.decode("utf-8", "replace")
    assert "warning: et_" in stderr
    assert_private_absent(stderr)
    machine = invoke(runtime, env, "transcript_tool.py", "--request-stdin", "--report-usage",
                     body=("bad json " + PRIVATE_ARGUMENT).encode())
    assert machine.returncode == 0
    assert json.loads(machine.stdout)["error_code"] == "invalid_json"
    assert json.loads(machine.stderr)["usage"] == {
        "requests_used": 0, "response_bytes_used": 0, "exhausted": None,
    }
    assert_private_absent(machine.stdout.decode(), machine.stderr.decode())
    assert all(path.read_bytes() == b"preserve" for path in tmp_path.glob("*-block"))


def test_native_reader_recovers_a_failed_request_without_spilling_query(isolated):
    runtime, env, data = isolated
    transcript_dir = data / "transcripts" / "ETPX"
    transcript_dir.mkdir(parents=True)
    (transcript_dir / "ETPX_Q1_2026_earnings_call.txt").write_text("Original business update", encoding="utf-8")
    (transcript_dir / "ETPX_Q1_2026_bilingual.json").write_text(PRIVATE_ARGUMENT, encoding="utf-8")
    saved = queue.Queue()
    process = subprocess.Popen(
        [sys.executable, "-B", "-u", str(runtime / "reader.py"), "--port", "0", "--no-browser"],
        cwd=runtime, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env, creationflags=NO_WINDOW, text=True, encoding="utf-8",
    )
    assert process.stdout is not None
    thread = Thread(target=lambda: saved.put(process.stdout.readline()), daemon=True)
    thread.start()
    try:
        ready = saved.get(timeout=12)
        match = re.search(r"localhost:(\d+)", ready)
        assert match and int(match.group(1)) > 0, ready
        client = HTTPConnection("127.0.0.1", int(match.group(1)), timeout=5)
        try:
            for uri, expected in (("/", 200), ("/api/data", 200), ("/missing", 404)):
                client.request("GET", uri + "?opaque=" + SECRETS[-1])
                response = client.getresponse()
                assert response.status == expected
                content = response.read()
                if uri == "/api/data":
                    assert json.loads(content)["transcripts"]["ETPX"][0]["bilingual_data"] is None
            cfg = runtime / "config.yaml"
            original = cfg.read_bytes()
            cfg.write_text("broken: [" + SECRETS[-1], encoding="utf-8")
            client.request("GET", "/api/data")
            response = client.getresponse()
            assert response.status == 500
            assert_private_absent(response.read().decode("utf-8", "replace"))
            cfg.write_bytes(original)
            client.request("GET", "/api/data")
            response = client.getresponse()
            assert response.status == 200
            response.read()
        finally:
            client.close()
    finally:
        process.kill()
        process.wait(timeout=8)
        thread.join(timeout=3)
        assert process.stderr is not None
        stderr = process.stderr.read()
        process.stderr.close()
        process.stdout.close()

    rows = list(errors(env))
    assert any(row["facts"].get("entry") == "reader" and row["facts"].get("route") == "/api/data"
               and row["facts"].get("exception_trace") for row in rows)
    assert_private_absent(stderr, json.dumps(rows))


def test_native_concurrent_writers_rotate_without_losing_failures(isolated):
    runtime, env, _ = isolated
    logfile = Path(env["RESEARCH_FEEDBACK_STATE_ROOT"]) / "logs" / "runtime_errors.log"
    logfile.parent.mkdir(parents=True)
    logfile.write_text(json.dumps({"seed": "x" * (2 * 1024 * 1024)}) + "\n", encoding="utf-8")

    def write(index):
        return invoke(
            runtime, {**env, "RESEARCH_FEEDBACK_RUN_ID": f"rotation-et-{index}",
                      "RESEARCH_FEEDBACK_OUT": ""},
            "translate.py", "--unknown-et-p2", PRIVATE_ARGUMENT,
        )

    with ThreadPoolExecutor(max_workers=3) as workers:
        results = list(workers.map(write, range(3)))
    assert [item.returncode for item in results] == [2, 2, 2]
    assert logfile.with_name("runtime_errors.log.1").exists()
    rows = list(errors(env))
    recorded = {row["run_id"] for row in rows if row.get("event") == "invocation_failure"}
    assert recorded == {"rotation-et-0", "rotation-et-1", "rotation-et-2"}
    assert_private_absent(json.dumps(rows))


def test_owner_cold_reads_the_original_entry_without_mutating_any_artifact(isolated, tmp_path):
    runtime, env, _ = isolated
    refusal = invoke(runtime, env, "make_interleaved.py", "--bad-et-command", PRIVATE_ARGUMENT)
    assert refusal.returncode == 2

    def snapshot():
        return {
            str(path.relative_to(tmp_path)): path.read_bytes()
            for folder in (tmp_path / "state", tmp_path / "output")
            for path in folder.rglob("*") if path.is_file()
        }

    before = snapshot()
    argv = ("runtime-history", "--run-id", "run-et-p2", "--attempt-id", "attempt-et-p2")
    first = invoke(runtime, env, "tools/feedback_owner.py", *argv)
    second = invoke(runtime, env, "tools/feedback_owner.py", *argv)
    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout and not first.stderr and not second.stderr
    history = json.loads(first.stdout)
    assert history["status"] == "found"
    assert any(row.get("facts", {}).get("entry") == "make_interleaved"
               and row["phase"] == "invocation_failure" for row in history["events"])
    assert snapshot() == before
    harmless_selector = invoke(runtime, env, "tools/feedback_owner.py", "runtime-history",
                               "--run-id", "triage", "--attempt-id", "attempt-et-p2")
    assert harmless_selector.returncode == 0 and not harmless_selector.stderr
    assert json.loads(harmless_selector.stdout) == {"status": "absent", "events": []}
    assert snapshot() == before


def test_legacy_f4_run_identity_remains_addressable_after_local_projection(isolated):
    runtime, env, _ = isolated
    run_id = "legacy/et/caller"
    env["RESEARCH_FEEDBACK_RUN_ID"] = run_id
    result = invoke(runtime, env, "make_interleaved.py", "--invalid-et-p2")
    assert result.returncode == 2
    observations = Path(env["RESEARCH_FEEDBACK_OUT"]).read_text(encoding="utf-8").splitlines()
    assert {json.loads(line)["run_id"] for line in observations} == {run_id}
    cold = invoke(runtime, env, "tools/feedback_owner.py", "runtime-history",
                  "--run-id", run_id, "--attempt-id", "attempt-et-p2")
    assert cold.returncode == 0 and not cold.stderr
    assert json.loads(cold.stdout)["status"] == "found"


def test_capability_introspection_does_not_create_diagnostics_or_delivery(isolated):
    runtime, env, _ = isolated
    result = invoke(runtime, env, "transcript_tool.py", "--capabilities")
    assert result.returncode == 0
    assert json.loads(result.stdout)["schema_version"] == "earnings-provider-capabilities/1"
    assert not result.stderr
    assert not Path(env["RESEARCH_FEEDBACK_STATE_ROOT"]).exists()
    assert not Path(env["RESEARCH_FEEDBACK_OUT"]).exists()


def test_real_translation_failure_keeps_original_and_retains_typed_error(isolated):
    runtime, env, data = isolated
    calls = []

    class Provider(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            calls.append(self.path)
            if len(calls) == 1:
                payload = {"choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}}]}
                self.send_response(200)
            else:
                payload = {"error": {"message": PRIVATE_ARGUMENT}}
                self.send_response(401)
            body = json.dumps(payload).encode()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return

    source_dir = data / "transcripts" / "ETPX"
    source_dir.mkdir(parents=True)
    source = source_dir / "ETPX_Q1_2026_earnings_call.txt"
    original = b"Company: ET Example\nQuarter: Q1 2026\n" + b"=" * 70 + b"\nA longer original business update.\n"
    source.write_bytes(original)
    with HTTPServer(("127.0.0.1", 0), Provider) as server:
        config = json.loads((runtime / "config.yaml").read_text(encoding="utf-8"))
        config["paths"].update(lock_file="batch.lock", translate_cache="translated.json", config_json="config.json")
        config["minimax"] = {"system_prompt": "Translate", "sleep_between_calls": 0}
        (runtime / "config.yaml").write_text(json.dumps(config), encoding="utf-8")
        (data / "config.json").write_text(json.dumps({
            "minimax_api_key": SECRETS[3], "minimax_base_url": f"http://127.0.0.1:{server.server_port}/v1",
        }), encoding="utf-8")
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = invoke(runtime, env, "translate.py", "--ticker", "ETPX", "--backend", "minimax")
        finally:
            server.shutdown()
            thread.join(timeout=5)

    assert result.returncode == 0
    assert calls == ["/v1/chat/completions", "/v1/chat/completions"]
    assert source.read_bytes() == original
    bilingual = next(source_dir.glob("*_bilingual.json"))
    assert "A longer original business update." in bilingual.read_text(encoding="utf-8")
    rows = list(errors(env))
    assert any(
        row["facts"].get("stage") == "translation-provider"
        and any(cause["type"] == "AuthenticationError" for cause in row["facts"].get("exception_trace", []))
        for row in rows
    )
    translation_log = Path(env["RESEARCH_FEEDBACK_STATE_ROOT"]) / "logs" / "translation.log"
    assert "AuthenticationError" in translation_log.read_text(encoding="utf-8")
    assert_private_absent(result.stdout.decode(), result.stderr.decode(), json.dumps(rows),
                          translation_log.read_text(encoding="utf-8"))


def test_real_worker_falls_back_to_detached_types_when_envelope_cannot_be_written(isolated, tmp_path):
    from io import BytesIO

    from retrieval_diagnostics import WorkerStderr
    from retrieval_protocol import WORKER_ERROR_FILENAME, build_request_envelope, encode_envelope
    from transcript_provider_settings import DEFAULT_PROVIDER_SETTINGS

    runtime, env, _ = isolated
    (runtime / "rejected_fixture.py").write_text(
        "class CustomCredentialContext(Exception): pass\n"
        "class CustomProviderFailure(Exception): pass\n"
        "try:\n"
        f"    raise CustomCredentialContext({PRIVATE_ARGUMENT!r})\n"
        "except CustomCredentialContext:\n"
        f"    raise CustomProviderFailure({PRIVATE_ARGUMENT!r}) from None\n",
        encoding="utf-8",
    )
    envelope = build_request_envelope(
        "fetch", {}, include_source_payload=False,
        provider_settings=DEFAULT_PROVIDER_SETTINGS,
        budget={"seconds_remaining": 10},
        launcher="rejected_fixture:unused_factory",
    )
    request = tmp_path / "request.json"
    request.write_bytes(encode_envelope(envelope))
    (tmp_path / WORKER_ERROR_FILENAME).mkdir()
    child = invoke(runtime, env, "retrieval_worker.py", str(request))
    assert child.returncode == 1 and child.stdout == b""
    assert_private_absent(child.stderr.decode("utf-8", "replace"))
    captured = WorkerStderr(BytesIO(child.stderr))
    captured.finish()
    assert captured.diagnostic_write_error in {"PermissionError", "IsADirectoryError"}
    projection = {fact["type"]: fact for fact in captured.exception_trace}
    assert projection["CustomProviderFailure"]["relation"] == "raised"
    assert projection["CustomCredentialContext"]["relation"] == "context"
    assert all(projection[name]["frames"][-1]["file"] == "rejected_fixture.py" for name in projection)


def test_rotation_preserves_a_record_while_an_external_reader_holds_the_file(isolated):
    runtime, env, _ = isolated
    path = Path(env["RESEARCH_FEEDBACK_STATE_ROOT"]) / "logs" / "runtime_errors.log"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"seed": "x" * (2 * 1024 * 1024)}) + "\n", encoding="utf-8")
    with path.open("rb") as reader:
        result = invoke(runtime, env, "translate.py", "--invalid", PRIVATE_ARGUMENT)
        assert not reader.closed
    assert result.returncode == 2
    if sys.platform == "win32":
        assert b"et_log_rotation_deferred" in result.stderr
    else:
        assert path.with_name("runtime_errors.log.1").exists()
    assert any(row.get("event") == "invocation_failure" for row in errors(env))
    assert_private_absent(result.stderr.decode("utf-8", "replace"))


def test_native_reader_bind_failure_retains_the_os_type_and_exit(isolated):
    runtime, env, _ = isolated
    with socket.socket() as existing:
        if sys.platform == "win32":
            existing.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        existing.bind(("127.0.0.1", 0))
        existing.listen()
        result = invoke(runtime, env, "reader.py", "--port", str(existing.getsockname()[1]), "--no-browser")
    assert result.returncode == 1 and result.stdout == b""
    rows = list(errors(env))
    assert any(row["facts"].get("stage") == "server-bind"
               and any(cause["type"] in {"OSError", "PermissionError"} and cause["frames"]
                       for cause in row["facts"].get("exception_trace", [])) for row in rows)
    assert b"Traceback" not in result.stderr

def test_native_audit_receipt_io_failure_survives_in_f4_history(isolated):
    runtime, env, data = isolated
    root = data / "transcripts"
    root.mkdir()
    # A normal original and final receipt fit the filesystem limit, but the
    # atomic writer's longer temporary filename does not.
    for width in range(255, 40, -1):
        probe = root / ("Z" * width)
        try:
            probe.touch()
        except OSError:
            continue
        probe.unlink()
        break
    else:
        pytest.fail("could not find the filesystem filename limit")
    suffix = "_Q1_2026_earnings_call.txt"
    ticker = "Z" * (width - 10 - len(suffix))
    transcript = root / (ticker + suffix)
    original = ("=" * 70 + f"\nEarnings Call Transcript\nTicker: {ticker}\n"
                "Quarter: Q1 2026\nSource: motley_fool\nURL: N/A\n"
                + "=" * 70 + "\n\nA business update.").encode()
    transcript.write_bytes(original)
    result = invoke(runtime, env, "transcript_audit.py", "--write-receipts")
    assert result.returncode == 0 and not result.stderr
    row, = json.loads(result.stdout)["files"]
    assert row["receipt_action"] == "failed" and row["outcome"] == "legacy_unverified"
    assert transcript.read_bytes() == original
    assert not list(root.glob("*.receipt.json"))
    cold = invoke(runtime, env, "tools/feedback_owner.py", "runtime-history",
                  "--run-id", "run-et-p2", "--attempt-id", "attempt-et-p2")
    assert cold.returncode == 0 and not cold.stderr
    assert any(fact["facts"].get("stage") == "receipt-write"
               and fact["facts"].get("argument") == row["file_sha256"]
               and any(cause["type"] in {"OSError", "FileNotFoundError"} and cause["frames"]
                       for cause in fact["facts"].get("exception_trace", []))
               for fact in json.loads(cold.stdout)["events"])


def test_native_audit_missing_config_retains_its_real_exception(isolated):
    runtime, env, _ = isolated
    (runtime / "config.yaml").unlink()
    result = invoke(runtime, env, "transcript_audit.py")
    assert result.returncode == 1 and not result.stdout and b"Config not found" in result.stderr
    assert any(row["facts"].get("refusal_code") == "config_unavailable"
               and any(cause["type"] == "FileNotFoundError" and cause["frames"]
                       for cause in row["facts"].get("exception_trace", []))
               for row in errors(env))


def test_audit_keeps_native_names_but_redacts_f4_values_and_references(isolated):
    runtime, env, data = isolated
    supplied_name = f"{SECRETS[3]}_Q1_2026_earnings_call.txt"
    root = data / "transcripts"
    root.mkdir()
    source = root / supplied_name
    source.write_bytes(b"Untrusted synthetic transcript.")
    result = invoke(runtime, env, "transcript_audit.py")
    assert result.returncode == 0
    assert json.loads(result.stdout)["files"][0]["file"] == supplied_name
    records = Path(env["RESEARCH_FEEDBACK_OUT"])
    assert json.loads(records.read_text(encoding="utf-8"))["step_id"].startswith("step-")
    durable = [file.read_text(encoding="utf-8") for file in records.parent.glob("*.jsonl")]
    assert_private_absent(*durable)
    assert source.read_bytes() == b"Untrusted synthetic transcript."

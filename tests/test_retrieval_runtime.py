"""Hard-deadline retrieval supervisor contract (ET-DEADLINE).

Every test runs offline. The HTTP transport inside the worker is installed by
a private launcher seam (an import path in the internal worker envelope, never
a production CLI flag or environment backdoor); no test contacts a live
provider. Elapsed assertions use an explicit blocking marker plus a generous
platform startup margin on top of the single cleanup grace.
"""
from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import retrieval_runtime  # noqa: E402
from transcript_api import ProviderSettings  # noqa: E402
from tests.test_transcript_api import (  # noqa: E402
    Q2_URL,
    QUOTE_URL,
    FakeResponse,
    quote_html,
)

LAUNCHER = "tests.test_retrieval_runtime:build_fake_session"
ENABLED_FOOL = ProviderSettings(motley_fool_enabled=True)
STARTUP_MARGIN = 10.0


# ── private launcher seam (runs inside the worker) ──────────────────


class StallResponse(FakeResponse):
    """Fake response that can stall at get time or mid-stream.

    ``stall_in_get`` blocks inside ``Session.get`` after the call marker is
    recorded; ``stall`` = ``(after_yields, seconds)`` blocks inside the stream
    (``seconds=None`` blocks forever).
    """

    def __init__(
        self,
        url: str,
        payload: bytes = b"",
        *,
        stall: tuple[int, float | None] | None = None,
        stall_in_get: bool = False,
        split_chunks: int | None = None,
        **kwargs: Any,
    ):
        super().__init__(url, payload, **kwargs)
        self.stall = stall
        self.stall_in_get = stall_in_get
        self.split_chunks = split_chunks


def fake_spec(
    responses: dict[str, Any] | None = None,
    *,
    calls_file: Path | str | None = None,
    build_failure: str | None = None,
    corrupt_result_at_exit: str | None = None,
    record_envelope: bool = True,
) -> dict[str, Any]:
    """Build a JSON-serializable launcher spec for the in-worker fake session."""
    return {
        "calls_file": str(calls_file) if calls_file is not None else None,
        "responses": _serialize_responses(responses or {}),
        "build_failure": build_failure,
        "corrupt_result_at_exit": corrupt_result_at_exit,
        "record_envelope": record_envelope,
    }


def _serialize_responses(responses: dict[str, Any]) -> dict[str, Any]:
    serialized: dict[str, Any] = {}
    for url, response in responses.items():
        if isinstance(response, Exception):
            serialized[url] = {"raise": type(response).__name__}
            continue
        entry: dict[str, Any] = {
            "payload_b64": base64.b64encode(response._payload).decode("ascii"),
            "status": response.status_code,
            "content_type": response.headers.get(
                "Content-Type", "text/html; charset=utf-8"
            ),
            "final_url": response.url,
        }
        if "Location" in response.headers:
            entry["location"] = response.headers["Location"]
        stall = getattr(response, "stall", None)
        if stall is not None:
            entry["stall_after_chunks"], entry["stall_seconds"] = stall
        if getattr(response, "stall_in_get", False):
            entry["stall_in_get"] = True
        if getattr(response, "split_chunks", None):
            entry["split_chunks"] = response.split_chunks
        serialized[url] = entry
    return serialized


def build_fake_session(spec: dict[str, Any], context: dict[str, Any]):
    """Launcher entry imported by the worker; installed only through the seam."""
    if spec.get("build_failure"):
        kind, _, argument = str(spec["build_failure"]).partition(":")
        if kind == "SystemExit":
            raise SystemExit(int(argument or "1"))
        raise RuntimeError(argument or kind)
    if spec.get("corrupt_result_at_exit"):
        import atexit

        target = Path(context["workdir"]) / "result.json"
        mode = spec["corrupt_result_at_exit"]

        def _corrupt() -> None:
            if mode == "garbage":
                target.write_bytes(b"{not-json")
            else:  # "huge"
                target.write_bytes(b"x" * (4 * 1024 * 1024))

        atexit.register(_corrupt)
    return _SpecSession(spec, context)


class _SpecSession:
    def __init__(self, spec: dict[str, Any], context: dict[str, Any]):
        self.headers: dict[str, str] = {}
        self._responses = spec.get("responses") or {}
        self._calls_file = spec.get("calls_file")
        self._context = context
        if spec.get("record_envelope"):
            self._record_envelope()

    def _append(self, record: dict[str, Any]) -> None:
        if not self._calls_file:
            return
        path = Path(self._calls_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()

    def _record_envelope(self) -> None:
        import sys as _sys

        try:
            envelope = json.loads(
                (Path(self._context["workdir"]) / "request.json").read_text(
                    encoding="utf-8"
                )
            )
        except (OSError, ValueError):
            envelope = {}
        self._append({
            "op": "envelope",
            "budget": envelope.get("budget"),
            "translator_loaded": "translator" in _sys.modules,
            "scraper_loaded": "scraper" in _sys.modules,
        })

    def get(self, url: str, **kwargs: Any) -> "_SpecResponse":
        record: dict[str, Any] = {"op": "get", "url": url}
        if "timeout" in kwargs:
            record["timeout"] = list(kwargs["timeout"]) if isinstance(
                kwargs["timeout"], tuple
            ) else kwargs["timeout"]
        params = kwargs.get("params")
        if isinstance(params, dict):
            record["params"] = {
                key: ("***" if key == "apikey" else value)
                for key, value in params.items()
            }
        self._append(record)
        entry = self._responses.get(url)
        if entry is None:
            raise KeyError(f"unregistered fake url: {url}")
        if entry.get("stall_in_get"):
            while True:
                time.sleep(60)
        if "raise" in entry:
            if entry["raise"] == "Timeout":
                raise requests.Timeout()
            raise requests.RequestException(entry["raise"])
        return _SpecResponse(url, entry, append=self._append)

    def close(self) -> None:
        pass


class _SpecResponse:
    def __init__(self, url: str, entry: dict[str, Any], *, append):
        self.url = entry.get("final_url") or url
        self.status_code = entry.get("status", 200)
        self._payload = (
            base64.b64decode(entry["payload_b64"]) if entry.get("payload_b64") else b""
        )
        self.headers: dict[str, str] = {
            "Content-Type": entry.get("content_type", "text/html; charset=utf-8")
        }
        if entry.get("location"):
            self.headers["Location"] = entry["location"]
        self.closed = False
        self._append = append
        self._url = url
        self._stall_after = entry.get("stall_after_chunks")
        self._stall_seconds = entry.get("stall_seconds")
        self._split = entry.get("split_chunks")

    def iter_content(self, chunk_size: int):
        payload = self._payload
        if self._split:
            size = max(1, len(payload) // self._split)
            chunks = [payload[i:i + size] for i in range(0, len(payload), size)]
        else:
            chunks = [
                payload[i:i + chunk_size] for i in range(0, len(payload), chunk_size)
            ] or [b""]
        for index, chunk in enumerate(chunks, start=1):
            yield chunk
            if self._stall_after is not None and index >= self._stall_after:
                if self._stall_seconds is None:
                    while True:
                        time.sleep(60)
                time.sleep(self._stall_seconds)

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._append({"op": "close", "url": self._url})


# ── parent-side helpers ─────────────────────────────────────────────


def read_records(calls_file: Path) -> list[dict[str, Any]]:
    if not calls_file.exists():
        return []
    records = []
    for line in calls_file.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def get_urls(calls_file: Path) -> list[str]:
    return [record["url"] for record in read_records(calls_file) if record["op"] == "get"]


def envelopes(calls_file: Path) -> list[dict[str, Any]]:
    return [
        record for record in read_records(calls_file) if record["op"] == "envelope"
    ]


def list_params(**overrides: Any) -> dict[str, Any]:
    params = {
        "ticker": "ACME",
        "exchange": "nyse",
        "as_of_date": "2026-09-30",
        "request_id": "rt-list-001",
        "timeout_seconds": 10,
        "download_authorized": True,
    }
    params.update(overrides)
    return params


def make_usage(requests_used: int = 1, response_bytes_used: int = 64) -> dict[str, Any]:
    return {
        "requests_used": requests_used,
        "response_bytes_used": response_bytes_used,
        "exhausted": None,
    }


def write_envelope(path: Path, **overrides: Any) -> None:
    envelope = {
        "protocol": retrieval_runtime.WORKER_PROTOCOL,
        "usage": make_usage(),
        "result": {
            "schema_version": "earnings-transcript-discovery-result/1",
            "request_id": "rt-list-001",
            "status": "not_found",
        },
    }
    envelope.update(overrides)
    path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")


# ── supervisor: hard deadline & resource reclamation ────────────────


def test_blocked_get_is_reaped_within_deadline_and_cleanup_grace(tmp_path):
    calls_file = tmp_path / "calls.jsonl"
    temp_root = tmp_path / "rt"
    temp_root.mkdir()
    spec = fake_spec(
        {QUOTE_URL: StallResponse(QUOTE_URL, stall_in_get=True)},
        calls_file=calls_file,
    )
    started = time.monotonic()
    outcome = retrieval_runtime.run_retrieval(
        "list",
        list_params(),
        remaining_seconds=3.0,
        requests_left=4,
        response_bytes_left=8 * 1024 * 1024,
        provider_settings=ENABLED_FOOL,
        launcher=LAUNCHER,
        launcher_spec=spec,
        temp_root=temp_root,
    )
    elapsed = time.monotonic() - started
    assert outcome.reason == "deadline"
    assert outcome.result is None
    assert outcome.usage is None
    assert outcome.exit_code is not None  # exit confirmed before cleanup
    assert elapsed < (
        3.0 + retrieval_runtime.CLEANUP_GRACE_SECONDS + STARTUP_MARGIN
    )
    assert get_urls(calls_file) == [QUOTE_URL]  # explicit blocking marker
    assert list(temp_root.iterdir()) == []  # per-worker temp result removed


def test_successful_retrieval_returns_result_usage_and_cleans_temp(tmp_path):
    calls_file = tmp_path / "calls.jsonl"
    temp_root = tmp_path / "rt"
    temp_root.mkdir()
    spec = fake_spec({QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(Q2_URL))},
                     calls_file=calls_file)
    outcome = retrieval_runtime.run_retrieval(
        "list",
        list_params(),
        remaining_seconds=10.0,
        requests_left=4,
        response_bytes_left=8 * 1024 * 1024,
        provider_settings=ENABLED_FOOL,
        launcher=LAUNCHER,
        launcher_spec=spec,
        temp_root=temp_root,
    )
    assert outcome.reason is None
    assert outcome.usage is not None
    assert outcome.usage["requests_used"] == 1
    assert outcome.usage["response_bytes_used"] > 0
    assert outcome.usage["exhausted"] is None
    assert outcome.result["status"] in ("discovered", "not_found")
    assert outcome.result["request_id"] == "rt-list-001"
    assert outcome.exit_code == 0
    assert list(temp_root.iterdir()) == []
    # child loaded neither the translator nor the batch CLI module
    envelope = envelopes(calls_file)[0]
    assert envelope["translator_loaded"] is False
    assert envelope["scraper_loaded"] is False


def test_single_operation_without_batch_limits_still_reports_usage(tmp_path):
    """The tool path enforces no batch quota but must never claim 0 consumed."""
    calls_file = tmp_path / "calls.jsonl"
    temp_root = tmp_path / "rt"
    temp_root.mkdir()
    spec = fake_spec({QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(Q2_URL))},
                     calls_file=calls_file)
    outcome = retrieval_runtime.run_retrieval(
        "list",
        list_params(),
        remaining_seconds=10.0,
        requests_left=None,
        response_bytes_left=None,
        provider_settings=ENABLED_FOOL,
        launcher=LAUNCHER,
        launcher_spec=spec,
        temp_root=temp_root,
    )
    assert outcome.reason is None
    assert outcome.usage is not None
    assert outcome.usage["requests_used"] == 1
    assert outcome.usage["exhausted"] is None


def test_zero_remaining_budget_starts_no_worker(tmp_path):
    temp_root = tmp_path / "rt"
    temp_root.mkdir()
    outcome = retrieval_runtime.run_retrieval(
        "list",
        list_params(),
        remaining_seconds=0.0,
        requests_left=1,
        response_bytes_left=1024,
        provider_settings=ENABLED_FOOL,
        launcher=LAUNCHER,
        launcher_spec=fake_spec({}, calls_file=tmp_path / "calls.jsonl"),
        temp_root=temp_root,
    )
    assert outcome.reason == "deadline"
    assert outcome.result is None
    assert outcome.usage == {
        "requests_used": 0,
        "response_bytes_used": 0,
        "exhausted": None,
    }
    assert outcome.exit_code is None
    assert list(temp_root.iterdir()) == []
    assert not (tmp_path / "calls.jsonl").exists()


def test_worker_crash_is_failure_with_unknown_usage(tmp_path):
    temp_root = tmp_path / "rt"
    temp_root.mkdir()
    outcome = retrieval_runtime.run_retrieval(
        "list",
        list_params(),
        remaining_seconds=10.0,
        requests_left=4,
        response_bytes_left=1024,
        provider_settings=ENABLED_FOOL,
        launcher=LAUNCHER,
        launcher_spec=fake_spec(build_failure="SystemExit:7"),
        temp_root=temp_root,
    )
    assert outcome.reason == "worker_failure"
    assert outcome.result is None
    assert outcome.usage is None
    assert outcome.exit_code == 7
    assert list(temp_root.iterdir()) == []


def test_worker_exit_zero_without_result_is_failure(tmp_path):
    temp_root = tmp_path / "rt"
    temp_root.mkdir()
    outcome = retrieval_runtime.run_retrieval(
        "list",
        list_params(),
        remaining_seconds=10.0,
        requests_left=4,
        response_bytes_left=1024,
        provider_settings=ENABLED_FOOL,
        launcher=LAUNCHER,
        launcher_spec=fake_spec(build_failure="SystemExit:0"),
        temp_root=temp_root,
    )
    assert outcome.reason == "worker_failure"
    assert outcome.result is None
    assert outcome.usage is None
    assert outcome.exit_code == 0
    assert list(temp_root.iterdir()) == []


def test_worker_result_corrupted_at_exit_is_not_accepted(tmp_path):
    temp_root = tmp_path / "rt"
    temp_root.mkdir()
    spec = fake_spec(
        {QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(Q2_URL))},
        calls_file=tmp_path / "calls.jsonl",
        corrupt_result_at_exit="garbage",
    )
    outcome = retrieval_runtime.run_retrieval(
        "list",
        list_params(),
        remaining_seconds=10.0,
        requests_left=4,
        response_bytes_left=1024,
        provider_settings=ENABLED_FOOL,
        launcher=LAUNCHER,
        launcher_spec=spec,
        temp_root=temp_root,
    )
    assert outcome.reason == "worker_failure"
    assert outcome.result is None
    assert outcome.usage is None
    assert list(temp_root.iterdir()) == []


def test_oversized_worker_result_is_not_accepted(tmp_path, monkeypatch):
    monkeypatch.setattr(retrieval_runtime, "MAX_RESULT_BYTES", 512)
    temp_root = tmp_path / "rt"
    temp_root.mkdir()
    spec = fake_spec(
        {QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(Q2_URL))},
        calls_file=tmp_path / "calls.jsonl",
    )
    outcome = retrieval_runtime.run_retrieval(
        "list",
        list_params(),
        remaining_seconds=10.0,
        requests_left=4,
        response_bytes_left=1024,
        provider_settings=ENABLED_FOOL,
        launcher=LAUNCHER,
        launcher_spec=spec,
        temp_root=temp_root,
    )
    assert outcome.reason == "worker_failure"
    assert outcome.result is None
    assert outcome.usage is None
    assert list(temp_root.iterdir()) == []


def test_cleanup_failure_is_reported_by_name_without_pretending(tmp_path,
                                                                 monkeypatch, capsys):
    def boom(_path):
        raise OSError("disk locked")

    monkeypatch.setattr("shutil.rmtree", boom)
    temp_root = tmp_path / "rt"
    temp_root.mkdir()
    spec = fake_spec({QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(Q2_URL))},
                     calls_file=tmp_path / "calls.jsonl")
    outcome = retrieval_runtime.run_retrieval(
        "list",
        list_params(),
        remaining_seconds=10.0,
        requests_left=4,
        response_bytes_left=1024,
        provider_settings=ENABLED_FOOL,
        launcher=LAUNCHER,
        launcher_spec=spec,
        temp_root=temp_root,
    )
    assert outcome.reason is None
    assert "retrieval_cleanup_failed" in capsys.readouterr().err
    assert len(list(temp_root.iterdir())) == 1  # not pretending it was reclaimed


# ── result file acceptance rules ────────────────────────────────────


def test_result_file_loader_accepts_valid_envelope(tmp_path):
    path = tmp_path / "result.json"
    write_envelope(path)
    result, usage, reason = retrieval_runtime._load_result_file(
        path,
        expected_request_id="rt-list-001",
        expected_schemas=None,
        deadline=time.monotonic() + 30,
        max_bytes=retrieval_runtime.MAX_RESULT_BYTES,
    )
    assert reason is None
    assert usage == make_usage()
    assert result["status"] == "not_found"


def test_result_file_after_deadline_is_not_accepted_as_fetched(tmp_path):
    path = tmp_path / "result.json"
    write_envelope(path)
    result, usage, reason = retrieval_runtime._load_result_file(
        path,
        expected_request_id="rt-list-001",
        expected_schemas=None,
        deadline=time.monotonic() - 1,
        max_bytes=retrieval_runtime.MAX_RESULT_BYTES,
    )
    assert reason == "deadline"
    assert result is None
    assert usage == make_usage()  # usage itself is known


def test_result_file_loader_rejects_untrusted_envelopes(tmp_path):
    cases = (
        "bad_json",
        "wrong_protocol",
        "bad_usage",
        "result_not_object",
        "wrong_request_id",
        "wrong_schema",
    )
    for mutation in cases:
        path = tmp_path / f"{mutation}.json"
        if mutation == "bad_json":
            path.write_bytes(b"{not-json")
            expected_usage = None
        elif mutation == "wrong_protocol":
            write_envelope(path, protocol="other/9")
            expected_usage = None
        elif mutation == "bad_usage":
            write_envelope(path, usage={"requests_used": "many"})
            expected_usage = None
        elif mutation == "result_not_object":
            write_envelope(path, result=["nope"])
            expected_usage = make_usage()
        elif mutation == "wrong_request_id":
            write_envelope(path)
            expected_usage = make_usage()
        else:  # wrong_schema
            write_envelope(
                path,
                result={
                    "schema_version": "earnings-transcript-result/1",
                    "request_id": "rt-list-001",
                    "status": "not_found",
                },
            )
            expected_usage = make_usage()

        kwargs: dict[str, Any] = {
            "expected_request_id": "rt-list-001",
            "expected_schemas": None,
        }
        if mutation == "wrong_request_id":
            kwargs["expected_request_id"] = "other-id"
        elif mutation == "wrong_schema":
            kwargs["expected_schemas"] = {"earnings-transcript-discovery-result/1"}

        result, usage, reason = retrieval_runtime._load_result_file(
            path,
            deadline=time.monotonic() + 30,
            max_bytes=retrieval_runtime.MAX_RESULT_BYTES,
            **kwargs,
        )
        assert reason == "worker_failure", mutation
        assert result is None, mutation
        assert usage == expected_usage, mutation


def test_missing_result_file_is_worker_failure(tmp_path):
    result, usage, reason = retrieval_runtime._load_result_file(
        tmp_path / "absent.json",
        expected_request_id=None,
        expected_schemas=None,
        deadline=time.monotonic() + 30,
        max_bytes=retrieval_runtime.MAX_RESULT_BYTES,
    )
    assert (result, usage, reason) == (None, None, "worker_failure")


def test_second_operation_shares_the_original_deadline(tmp_path):
    """Each operation gets only the remaining slice of one shared deadline."""
    calls_file = tmp_path / "calls.jsonl"
    temp_root = tmp_path / "rt"
    temp_root.mkdir()
    shared_deadline = time.monotonic() + 5.0
    fast = retrieval_runtime.run_retrieval(
        "list",
        list_params(request_id="rt-fast"),
        remaining_seconds=shared_deadline - time.monotonic(),
        requests_left=4,
        response_bytes_left=1024 * 1024,
        provider_settings=ENABLED_FOOL,
        launcher=LAUNCHER,
        launcher_spec=fake_spec(
            {QUOTE_URL: FakeResponse(QUOTE_URL, quote_html(Q2_URL))},
            calls_file=calls_file,
        ),
        temp_root=temp_root,
    )
    assert fast.reason is None
    blocked = retrieval_runtime.run_retrieval(
        "list",
        list_params(request_id="rt-blocked"),
        remaining_seconds=shared_deadline - time.monotonic(),
        requests_left=3,
        response_bytes_left=1024 * 1024,
        provider_settings=ENABLED_FOOL,
        launcher=LAUNCHER,
        launcher_spec=fake_spec(
            {QUOTE_URL: StallResponse(QUOTE_URL, stall_in_get=True)},
            calls_file=calls_file,
        ),
        temp_root=temp_root,
    )
    assert blocked.reason == "deadline"
    assert blocked.usage is None
    # the blocked operation ran right up to the shared deadline …
    assert time.monotonic() >= shared_deadline - 0.2
    # … and never past deadline + one cleanup grace (+ platform margin)
    assert time.monotonic() < (
        shared_deadline + retrieval_runtime.CLEANUP_GRACE_SECONDS + STARTUP_MARGIN
    )
    assert list(temp_root.iterdir()) == []

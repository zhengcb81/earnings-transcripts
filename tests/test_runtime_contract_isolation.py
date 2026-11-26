"""Runtime contract isolation (MB-ET ET-01).

The supervisor and the internal worker exchange one protocol. Neither side may
need the other's implementation, and the parent must not import provider HTTP or
parser code merely to supervise an operation. These tests run the imports in a
fresh interpreter with an import guard, because an in-process check cannot prove
what a first import loads.

The guard is a test-only ``sys.meta_path`` finder; production code contains no
such switch. All cases are offline: the worker dispatch uses a hand-written fake
transport, no provider is contacted.
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _run_script(source: str, *args: str) -> subprocess.CompletedProcess:
    script = textwrap.dedent(source).replace("__ROOT__", json.dumps(str(ROOT)))
    return subprocess.run(
        [sys.executable, "-B", "-c", script, *args],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=120,
        check=False,
    )


def test_local_reader_loads_without_the_legacy_facade_or_translation_stack():
    result = _run_script(
        """
        import sys
        sys.path.insert(0, __ROOT__)

        class Blocker:
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] in {"common", "translator", "openai", "translatepy"}:
                    raise ImportError("reader reached translation or legacy facade")
                return None

        sys.meta_path.insert(0, Blocker())
        import reader
        assert reader.ReaderHandler.build_data
        """
    )
    assert result.returncode == 0, result.stderr


# ── ET-01: supervisor imports no provider implementation ────────────


def test_supervisor_imports_without_provider_http_or_parser(tmp_path):
    """Importing the supervisor must not load requests/bs4/extraction/API."""
    result = _run_script(
        """
        import json, sys, tempfile, time
        sys.path.insert(0, __ROOT__)

        BLOCKED = ("transcript_api", "transcript_extraction", "requests", "bs4")

        class Blocker:
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] in BLOCKED:
                    raise ImportError("provider module blocked: " + name)
                return None

        sys.meta_path.insert(0, Blocker())

        import retrieval_runtime
        import retrieval_protocol
        import transcript_contracts
        import transcript_provider_settings

        loaded = sorted(name for name in BLOCKED if name in sys.modules)

        payload = {"request_id": "iso-1", "timeout_seconds": 5, "max_response_bytes": 4096}
        request = retrieval_protocol.build_request_envelope(
            "list",
            payload,
            include_source_payload=False,
            provider_settings=transcript_provider_settings.DEFAULT_PROVIDER_SETTINGS,
            budget={"seconds_remaining": 5.0, "requests_left": 1, "response_bytes_left": 4096},
        )
        envelope = retrieval_protocol.build_result_envelope(
            usage={"requests_used": 1, "response_bytes_used": 12, "exhausted": None},
            result={
                "schema_version": transcript_contracts.DISCOVERY_RESULT_SCHEMA,
                "request_id": "iso-1",
                "status": "not_found",
            },
        )
        parsed = retrieval_protocol.parse_result_envelope(
            envelope, expected_request_id="iso-1", expected_schemas=None
        )

        workdir = tempfile.mkdtemp()
        path = __import__("pathlib").Path(workdir) / "result.json"
        path.write_bytes(retrieval_protocol.encode_result_envelope(
            usage={"requests_used": 1, "response_bytes_used": 12, "exhausted": None},
            result=envelope["result"],
        ))
        loaded_result, usage, reason = retrieval_runtime._load_result_file(
            path,
            expected_request_id="iso-1",
            expected_schemas=None,
            deadline=time.monotonic() + 30,
            max_bytes=retrieval_protocol.result_transfer_limit(payload, 4096),
        )

        print(json.dumps({
            "blocked_loaded": loaded,
            "protocol": retrieval_protocol.WORKER_PROTOCOL,
            "request_protocol": request["protocol"],
            "parsed_status": parsed[0]["status"] if parsed[0] else None,
            "parsed_reason": parsed[2],
            "loaded_status": loaded_result["status"] if loaded_result else None,
            "usage": usage,
            "reason": reason,
            "supervisor_has_fixed_result_cap": hasattr(retrieval_runtime, "MAX_RESULT_BYTES"),
        }))
        """
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["blocked_loaded"] == []
    assert observed["protocol"] == "et-retrieval-worker/1"
    assert observed["request_protocol"] == observed["protocol"]
    assert observed["parsed_status"] == "not_found"
    assert observed["parsed_reason"] is None
    assert observed["loaded_status"] == "not_found"
    assert observed["usage"] == {
        "requests_used": 1,
        "response_bytes_used": 12,
        "exhausted": None,
    }
    assert observed["reason"] is None
    assert observed["supervisor_has_fixed_result_cap"] is False


def test_worker_protocol_does_not_import_supervisor():
    """The worker must run its dispatch with ``retrieval_runtime`` blocked."""
    result = _run_script(
        """
        import json, sys
        sys.path.insert(0, __ROOT__)

        class Blocker:
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] == "retrieval_runtime":
                    raise ImportError("supervisor module blocked: " + name)
                return None

        sys.meta_path.insert(0, Blocker())

        import retrieval_worker
        import retrieval_protocol
        from transcript_provider_settings import ProviderSettings

        class Response:
            def __init__(self, url, payload):
                self.url = url
                self.status_code = 200
                self.headers = {"Content-Type": "text/html; charset=utf-8"}
                self._payload = payload

            def iter_content(self, chunk_size):
                for index in range(0, len(self._payload), chunk_size):
                    yield self._payload[index:index + chunk_size]

            def close(self):
                return None

        class Session:
            headers = {}

            def get(self, url, **kwargs):
                return Response(url, QUOTE_HTML)

            def close(self):
                return None

        QUOTE_HTML = (
            b"<html><body>"
            b'<a href="https://www.fool.com/earnings/call-transcripts/2026/09/01/acme-q2-2026-earnings-transcript/">t</a>'
            b"</body></html>"
        )

        payload = {
            "ticker": "ACME",
            "exchange": "nyse",
            "as_of_date": "2026-09-30",
            "request_id": "iso-worker-1",
            "timeout_seconds": 10,
            "download_authorized": True,
        }
        envelope = {
            "operation": "list",
            "payload": payload,
            "include_source_payload": False,
        }
        result = retrieval_worker._dispatch(
            envelope, lambda: Session(), ProviderSettings(motley_fool_enabled=True)
        )
        print(json.dumps({
            "supervisor_loaded": "retrieval_runtime" in sys.modules,
            "protocol": retrieval_protocol.WORKER_PROTOCOL,
            "status": result["status"],
            "request_id": result["request_id"],
            "transfer_limit": retrieval_protocol.result_transfer_limit(payload, 4096),
        }))
        """
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["supervisor_loaded"] is False
    assert observed["protocol"] == "et-retrieval-worker/1"
    assert observed["status"] == "discovered"
    assert observed["request_id"] == "iso-worker-1"
    assert observed["transfer_limit"] > 0


# ── ET-01: public CLI capabilities stay local ───────────────────────


def test_capabilities_cli_is_provider_import_free():
    """``--capabilities`` must answer from pure settings with zero provider code."""
    result = _run_script(
        """
        import json, runpy, sys
        sys.path.insert(0, __ROOT__)

        BLOCKED = ("transcript_api", "transcript_extraction", "requests", "bs4")

        class Blocker:
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] in BLOCKED:
                    raise ImportError("provider module blocked: " + name)
                return None

        sys.meta_path.insert(0, Blocker())
        sys.argv = ["transcript_tool.py", "--capabilities"]
        try:
            runpy.run_path(str(__import__("pathlib").Path(__ROOT__) / "transcript_tool.py"),
                           run_name="__main__")
        except SystemExit as exc:
            code = 0 if exc.code is None else exc.code
        else:
            code = 0
        print(json.dumps({"exit": code,
                          "blocked_loaded": [n for n in BLOCKED if n in sys.modules]}))
        """
    )
    assert result.returncode == 0, result.stderr
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    observed = json.loads(lines[-1])
    assert observed["exit"] == 0
    assert observed["blocked_loaded"] == []

    capability = json.loads(lines[0])
    assert capability["schema_version"] == "earnings-provider-capabilities/1"
    assert capability["providers"]["fmp"]["fetch"]["supported"] is True
    assert capability["providers"]["fmp"]["discover"]["supported"] is False
    assert capability["providers"]["motley_fool"]["fetch"]["enabled"] is False
    assert capability["providers"]["fmp"]["fetch"]["supported_exchanges"] == [
        "auto", "nasdaq", "nyse"
    ]

    # Full golden from the pure settings module: the provider-free CLI path must
    # produce exactly the same descriptor, not a similar subset.
    from transcript_provider_settings import (
        DEFAULT_PROVIDER_SETTINGS,
        provider_operation_capability,
    )

    expected = {
        "schema_version": "earnings-provider-capabilities/1",
        "providers": {
            provider: {
                operation: provider_operation_capability(
                    provider, operation, settings=DEFAULT_PROVIDER_SETTINGS
                )
                for operation in ("fetch", "discover", "fetch-candidate")
            }
            for provider in ("fmp", "motley_fool")
        },
    }
    assert capability == expected


def test_explicit_provider_settings_do_not_pollute_the_shared_default():
    """Settings are pure values: one caller's explicit settings stay local."""
    from transcript_provider_settings import (
        DEFAULT_PROVIDER_SETTINGS,
        ProviderSettings,
        provider_operation_capability,
    )

    baseline = provider_operation_capability("motley_fool", "fetch")
    custom = ProviderSettings(motley_fool_enabled=True, fmp_enabled=False)
    enabled = provider_operation_capability("motley_fool", "fetch", settings=custom)
    assert enabled["enabled"] is True
    assert baseline["enabled"] is False
    assert provider_operation_capability("motley_fool", "fetch") == baseline
    assert DEFAULT_PROVIDER_SETTINGS == ProviderSettings()
    assert DEFAULT_PROVIDER_SETTINGS.motley_fool_enabled is False
    assert DEFAULT_PROVIDER_SETTINGS.fmp_enabled is True


def test_fetch_preflight_is_provider_import_free():
    """A rejected fetch is decided in the parent without provider/HTTP code."""
    result = _run_script(
        """
        import io, json, sys
        sys.path.insert(0, __ROOT__)

        BLOCKED = ("transcript_api", "transcript_extraction", "requests", "bs4")

        class Blocker:
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] in BLOCKED:
                    raise ImportError("provider module blocked: " + name)
                return None

        sys.meta_path.insert(0, Blocker())

        import transcript_tool

        request = {
            "schema_version": "earnings-transcript-request/1",
            "request_id": "iso-preflight-1",
            "ticker": "MSFT",
            "exchange": "HKEX",
            "fiscal_year": 2026,
            "fiscal_quarter": 3,
            "as_of_date": "2026-07-30",
            "provider": "fmp",
            "download_authorized": True,
            "timeout_seconds": 10,
            "max_body_bytes": 1000000,
        }
        sys.stdin = io.StringIO(json.dumps(request))
        out = io.StringIO()
        sys.stdout = out
        rc = transcript_tool.main(["--request-stdin"])
        sys.__stdout__.write(json.dumps({
            "rc": rc,
            "result": json.loads(out.getvalue()),
            "blocked_loaded": [n for n in BLOCKED if n in sys.modules],
        }) + "\\n")
        """
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["rc"] == 0
    assert observed["result"]["status"] == "unsupported"
    assert observed["result"]["error_code"] == "unsupported_market"
    assert observed["blocked_loaded"] == []


def test_old_public_contract_imports_preserve_values_and_settings_identity():
    """Existing consumers keep importing from ``transcript_api``; no second copy."""
    import transcript_api as api
    import transcript_contracts as contracts
    import transcript_preflight
    import transcript_provider_settings as settings

    for name in (
        "REQUEST_SCHEMA",
        "RESULT_SCHEMA",
        "RESULT_SCHEMA_WITH_SOURCE_PAYLOAD",
        "DISCOVERY_RESULT_SCHEMA",
        "CANDIDATE_FETCH_REQUEST_SCHEMA",
        "EXTRACTION_VERSION",
        "EXTRACTION_VERSION_LEGACY",
        "ADAPTER_VERSION",
        "MAX_TIMEOUT_SECONDS",
        "MAX_BODY_BYTES",
        "MAX_LISTING_BYTES",
        "MAX_FMP_RESPONSE_BYTES",
        "MAX_SOURCE_PAYLOAD_RESULT_BYTES",
        "MIN_CONTENT_CHARS",
        "ALLOWED_HOST",
        "FMP_ALLOWED_HOST",
    ):
        assert getattr(api, name) is getattr(contracts, name), name

    assert api.ProviderSettings is settings.ProviderSettings
    assert api.DEFAULT_PROVIDER_SETTINGS is settings.DEFAULT_PROVIDER_SETTINGS
    assert api.provider_operation_capability is settings.provider_operation_capability
    assert api.EXCHANGES is settings.EXCHANGES
    assert api.preflight_transcript_fetch is transcript_preflight.preflight_transcript_fetch

    assert contracts.RESULT_SCHEMA == "earnings-transcript-result/1"
    assert contracts.RESULT_SCHEMA_WITH_SOURCE_PAYLOAD == "earnings-transcript-result/2"
    assert contracts.DISCOVERY_RESULT_SCHEMA == "earnings-transcript-discovery-result/1"
    assert contracts.REQUEST_SCHEMA == "earnings-transcript-request/1"

    # Identity alone could pass through string interning. Prove there is no
    # second definition: transcript_api only imports these names.
    import ast

    source = (ROOT / "transcript_api.py").read_text(encoding="utf-8")
    assigned = {
        target.id
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (
            node.targets if isinstance(node, ast.Assign) else [node.target]
        )
        if isinstance(target, ast.Name)
    }
    reexported = set(contracts.__dict__) | set(settings.__dict__)
    assert not (assigned & {name for name in reexported if name.isupper()})


# ── ET-01: refactored validation keeps the old accept/reject rules ──


def test_protocol_refactor_preserves_request_id_schema_usage_and_deadline(tmp_path):
    import retrieval_protocol
    import retrieval_runtime

    valid_result = {
        "schema_version": "earnings-transcript-discovery-result/1",
        "request_id": "rt-list-001",
        "status": "not_found",
    }
    usage = {"requests_used": 1, "response_bytes_used": 64, "exhausted": None}

    def write(name: str, envelope: dict) -> Path:
        path = tmp_path / name
        path.write_bytes(
            retrieval_protocol.encode_result_envelope(
                usage=envelope.pop("usage"), result=envelope.pop("result"),
                protocol=envelope.pop("protocol", retrieval_protocol.WORKER_PROTOCOL),
            )
        )
        return path

    accepted = write("accepted.json", {"usage": usage, "result": valid_result})
    result, seen_usage, reason = retrieval_runtime._load_result_file(
        accepted,
        expected_request_id="rt-list-001",
        expected_schemas=None,
        deadline=__import__("time").monotonic() + 30,
        max_bytes=retrieval_protocol.result_transfer_limit({}, None),
    )
    assert (reason, seen_usage) == (None, usage)
    assert result["status"] == "not_found"

    late = write("late.json", {"usage": usage, "result": valid_result})
    result, seen_usage, reason = retrieval_runtime._load_result_file(
        late,
        expected_request_id="rt-list-001",
        expected_schemas=None,
        deadline=__import__("time").monotonic() - 1,
        max_bytes=retrieval_protocol.result_transfer_limit({}, None),
    )
    assert (result, reason) == (None, "deadline")
    assert seen_usage == usage

    cases = {
        "wrong_protocol": {"protocol": "other/9", "usage": usage, "result": valid_result},
        "bad_usage": {"usage": {"requests_used": "many"}, "result": valid_result},
        "result_not_object": {"usage": usage, "result": ["nope"]},
        "wrong_request_id": {"usage": usage, "result": valid_result},
        "wrong_schema": {
            "usage": usage,
            "result": {
                "schema_version": "earnings-transcript-result/1",
                "request_id": "rt-list-001",
                "status": "not_found",
            },
        },
    }
    for name, envelope in cases.items():
        path = write(f"{name}.json", dict(envelope))
        kwargs = {"expected_request_id": "rt-list-001", "expected_schemas": None}
        if name == "wrong_request_id":
            kwargs["expected_request_id"] = "other-id"
        elif name == "wrong_schema":
            kwargs["expected_schemas"] = {"earnings-transcript-discovery-result/1"}
        result, seen_usage, reason = retrieval_runtime._load_result_file(
            path,
            deadline=__import__("time").monotonic() + 30,
            max_bytes=retrieval_protocol.result_transfer_limit({}, None),
            **kwargs,
        )
        assert reason == "worker_failure", name
        assert result is None, name
        assert seen_usage == (None if name in ("wrong_protocol", "bad_usage") else usage), name


def test_worker_and_provider_consume_the_published_budget_and_preflight():
    import retrieval_budget
    import retrieval_worker
    import transcript_api
    import transcript_preflight
    from decimal import Decimal

    assert retrieval_worker.BudgetSession is retrieval_budget.BudgetSession
    assert transcript_api.BudgetSession is retrieval_budget.BudgetSession
    assert retrieval_budget._BudgetSession is retrieval_budget.BudgetSession

    for name in ("InvalidRequest", "validate_request", "build_result", "check_provider_request", "is_valid_ticker"):
        assert getattr(transcript_api, name) is getattr(transcript_preflight, name)

    assert transcript_preflight.is_valid_ticker("BRK.B")
    assert not transcript_preflight.is_valid_ticker("BRK B")
    result = transcript_preflight.check_provider_request(
        "fixture", "unknown-provider", operation="fetch",
        result_schema=transcript_api.RESULT_SCHEMA,
        settings=transcript_api.DEFAULT_PROVIDER_SETTINGS,
        cost_limit=Decimal("0"),
    )
    assert result == {
        "schema_version": transcript_api.RESULT_SCHEMA,
        "request_id": "fixture",
        "status": "unsupported",
        "provider": "unknown-provider",
        "error_code": "provider_unavailable",
    }

"""F4-ET-BATCH-REPLAY：批量电话会逐项复原的隔离回归。

对应施工卡 company-wiki ``docs/plans/feedback-loop-2026-11-17/owner-cards/
F4-ET-BATCH-REPLAY.md`` 与本仓交接 ``docs/plans/feedback-loop-2026-11-17/
F4-ET-BATCH-REPLAY-handoff.md``。

基线：``scraper.py`` 批量路径的逐项结果只经 ``_record_entry`` 写日志和最终的
``run_manifest.json``。某公司某期读取失败而整批正常退出、或在下一项强停时，反馈端
零文件，无法从普通入口查回已经完成的失败；被批次限额结束的那一项更强停更早——它连
账本条目都没有。

每个用例在真实 Python 子进程里运行真实的 ``scraper.main``——``scraper.py`` 的
``__main__`` 块就是 ``raise SystemExit(main())``，驱动照此调用——经真实 supervisor
（``retrieval_runtime``）和真实 worker 子进程。worker 的 HTTP 会话由既有私有
launcher 接缝替换，即 ``tests/test_batch_runtime.run_main`` 的同一批注入点；没有生产
CLI 旗标或环境后门。公司清单和配置读取本仓真实的 ``companies.txt``/``config.yaml``，
工作目录是本仓根目录，与普通调用一致。Motley Fool 在生产默认关闭，Fool 路径经同一
私有接缝的 ``_provider_settings`` 启用，对应 ``run_main(settings=ENABLED_FOOL)``。

配置、状态根、输出、日志、锁、暂存和调用记录全部落在同一个新建的短临时根，用例
结束即删除并核对。网络由测试专用 ``sitecustomize`` 审计钩子阻断；不需要凭据，FMP
密钥是离线占位常量。原生 CLI 退出码、业务 stdout/stderr 与文件操作全部保留。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

import pytest

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
NO_CONSOLE = getattr(subprocess, "CREATE_NO_WINDOW", 0)

try:
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None
    import fcntl

import transcript_feedback as feedback  # noqa: E402
from tests.test_feedback_observation import FEEDBACK_FAKE_KEY  # noqa: E402
from tests.test_retrieval_runtime import (  # noqa: E402
    StallResponse,
    _SpecSession,
    fake_spec,
    read_records,
)
from tests.test_transcript_api import (  # noqa: E402
    FMP_URL,
    FakeResponse,
    fmp_payload,
    quote_html,
    transcript_html,
)

OWNER = feedback.OWNER
IDENTITY = ("owner", "origin_owner", "run_id", "attempt_id", "step_id", "check_id")
#: 测试专用密钥常量：只进入离线假会话，断言其绝不落在任何观察、明细或输出里。
OFFLINE_KEY = FEEDBACK_FAKE_KEY

#: The published provider checks of one dispatched item, besides the item's own
#: terminal check and the started row.
PROVIDER_CHECKS = {
    "outcome-naming",
    "exact-period-ownership",
    "content-reference",
    "usage-accounting",
}
STARTED = feedback.CHECK_ACTION_STARTED
ITEM = feedback.CHECK_BATCH_ITEM
BATCH = feedback.BATCH_STEP_ID
OUTCOME = feedback.CHECK_BATCH_OUTCOME

MSFT_QUOTE_URL = "https://www.fool.com/quote/nasdaq/msft/"
MSFT_Q2_URL = (
    "https://www.fool.com/earnings/call-transcripts/2026/09/01/"
    "msft-q2-2026-earnings-transcript/"
)
MSFT_Q1_URL = (
    "https://www.fool.com/earnings/call-transcripts/2026/05/01/"
    "msft-q1-2026-earnings-transcript/"
)
UNDATED_URL = "https://www.fool.com/earnings/call-transcripts/2026/02/01/msft-earnings-call/"
#: A 200 answer whose page carries no parseable transcript body.
UNPARSEABLE_PAGE = b"<html><body><p>Operator: hello.</p></body></html>"

GUARD_SITECUSTOMIZE = (
    "import sys\n"
    "def check(event, args):\n"
    "    if event in ('socket.connect', 'socket.getaddrinfo'):\n"
    "        raise RuntimeError('audit forbids external access')\n"
    "sys.addaudithook(check)\n"
)

#: The real ``scraper.py`` entry in a real subprocess. ``scraper.py``'s
#: ``__main__`` block is ``raise SystemExit(main())``; the driver only hands the
#: existing private seams to ``main``.
DRIVER = (
    "import json, sys\n"
    "import scraper\n"
    "from tests.test_f4_batch_replay import BATCH_LAUNCHER\n"
    "from transcript_api import ProviderSettings\n"
    "settings = (\n"
    "    ProviderSettings(motley_fool_enabled=True)\n"
    "    if sys.argv[3] == 'fool'\n"
    "    else ProviderSettings()\n"
    ")\n"
    "code = scraper.main(\n"
    "    sys.argv[4:],\n"
    "    _provider_settings=settings,\n"
    "    _retrieval_launcher=BATCH_LAUNCHER,\n"
    "    _retrieval_spec=json.loads(sys.argv[1]),\n"
    "    _retrieval_temp_root=sys.argv[2],\n"
    ")\n"
    "raise SystemExit(code)\n"
)

BATCH_LAUNCHER = "tests.test_f4_batch_replay:build_batch_session"


# ── 私有 launcher 接缝（在 worker 内运行） ──────────────────────


def build_batch_session(spec: dict[str, Any], context: dict[str, Any]):
    """The existing offline fake session, answering FMP per fiscal period.

    ``fake_spec``'s serialization is reused unchanged. ``periods`` maps
    ``YYYY-Qn`` to one answer for the FMP URL, so a two-period batch can hold
    two different provider answers. A held answer blocks inside
    ``Session.get`` for at most ``stall_seconds`` and then raises
    ``requests.Timeout``: a worker whose parent was killed still ends by
    itself and leaves no orphan behind.
    """
    session = _SpecSession(spec, context)
    periods = spec.get("periods") or {}
    bound = float(spec.get("stall_seconds") or 0)
    inner = session.get

    def get(url: str, **kwargs: Any):
        params = kwargs.get("params")
        stall = False
        if isinstance(params, dict):
            entry = periods.get(f"{params.get('year')}-Q{params.get('quarter')}")
            if entry is not None:
                stall = bool(entry.get("stall_in_get"))
                session._responses[url] = dict(entry, stall_in_get=False) if stall else entry
        response = inner(url, **kwargs)
        if stall:
            time.sleep(bound)  # still inside the provider's Session.get
            response.close()
            import requests

            raise requests.Timeout()
        return response

    session.get = get
    return session


def batch_spec(
    responses: dict[str, Any] | None = None,
    *,
    periods: dict[str, Any] | None = None,
    calls_file: Path | None = None,
    stall_seconds: float | None = None,
) -> dict[str, Any]:
    """``fake_spec`` plus one answer per fiscal period."""
    spec = fake_spec(responses or {}, calls_file=calls_file)
    spec["periods"] = {
        label: fake_spec({FMP_URL: response})["responses"][FMP_URL]
        for label, response in (periods or {}).items()
    }
    if stall_seconds is not None:
        spec["stall_seconds"] = stall_seconds
    return spec


# ── 隔离装置 ────────────────────────────────────────────────────


@pytest.fixture
def short_root():
    """A new short temporary root; deleted and checked when the case ends."""
    root = Path(tempfile.mkdtemp(prefix="et-f4b-"))
    try:
        yield root
    finally:
        for _ in range(120):
            shutil.rmtree(root, ignore_errors=True)
            if not root.exists():
                break
            time.sleep(0.5)
        assert not root.exists(), f"temporary root not restored: {root}"


def new_case(root: Path, name: str) -> Path:
    case = root / name
    case.mkdir(parents=True)
    guard = case / "guard"
    guard.mkdir()
    (guard / "sitecustomize.py").write_text(GUARD_SITECUSTOMIZE, encoding="utf-8")
    return case


def make_env(case: Path) -> dict:
    """One ordinary process environment: only start-up variables, state root in
    the temporary root, no ``RESEARCH_FEEDBACK_OUT``."""
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper()
        in {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC"}
    }
    for name in (
        "RESEARCH_FEEDBACK_OUT",
        "RESEARCH_FEEDBACK_RUN_ID",
        "RESEARCH_FEEDBACK_ATTEMPT_ID",
        "RESEARCH_FEEDBACK_DIR",
        "RESEARCH_FEEDBACK_DISABLED",
        "RESEARCH_FEEDBACK_STATE_ROOT",
        "FMP_API_KEY_FILE",
    ):
        env.pop(name, None)
    env.update(
        PYTHONPATH=os.pathsep.join([str(case / "guard"), str(ROOT)]),
        PYTHONUTF8="1",
        PYTHONDONTWRITEBYTECODE="1",
        FMP_API_KEY=OFFLINE_KEY,
        RESEARCH_FEEDBACK_STATE_ROOT=str(case / "state"),
    )
    return env


def with_out(case: Path) -> tuple[dict, Path]:
    """The explicit ``RESEARCH_FEEDBACK_OUT`` mode: the host's single file."""
    env = make_env(case)
    (case / "sink").mkdir()
    out_path = case / "sink" / "observations.jsonl"
    env["RESEARCH_FEEDBACK_OUT"] = str(out_path)
    return env, out_path


def run_batch(
    case: Path,
    argv: list[str],
    spec: dict[str, Any] | None = None,
    *,
    env: dict | None = None,
    mode: str = "default",
    timeout: float = 240,
) -> subprocess.CompletedProcess:
    """The working directory is this repository's root: an ordinary call starts
    here and ``companies.txt`` is read from here."""
    env = make_env(case) if env is None else env
    return subprocess.run(
        [
            PYTHON,
            "-B",
            "-c",
            DRIVER,
            json.dumps(batch_spec() if spec is None else spec),
            str(case / "rt"),
            mode,
            *argv,
        ],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        creationflags=NO_CONSOLE,
        env=env,
        cwd=str(ROOT),
        timeout=timeout,
        check=False,
    )


def start_batch(
    case: Path, argv: list[str], spec: dict[str, Any], *, env: dict
) -> subprocess.Popen:
    options: dict[str, Any] = {"start_new_session": True} if os.name != "nt" else {}
    return subprocess.Popen(
        [
            PYTHON,
            "-B",
            "-c",
            DRIVER,
            json.dumps(spec),
            str(case / "rt"),
            "default",
            *argv,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        env=env,
        cwd=str(ROOT),
        creationflags=NO_CONSOLE,
        **options,
    )


def redeliver(env: dict) -> subprocess.CompletedProcess:
    """Another process under the same identity that requests nothing."""
    return subprocess.run(
        [PYTHON, "-B", str(ROOT / "transcript_tool.py"), "--flush-observations"],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        creationflags=NO_CONSOLE,
        env=env,
        cwd=str(ROOT),
        timeout=60,
        check=False,
    )


def kill_tree(process: subprocess.Popen) -> None:
    """Hard-stop the isolated process and its worker child."""
    if process.poll() is None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                creationflags=NO_CONSOLE,
                check=False,
                timeout=60,
            )
        else:  # pragma: no cover - POSIX
            os.killpg(process.pid, signal.SIGKILL)
        if process.poll() is None:
            process.kill()
    process.wait(timeout=60)


def wait_until(
    condition: Callable[[], bool], process: subprocess.Popen, timeout: float = 120
) -> None:
    """Wait for the condition; an early exit of the child fails the wait."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        if process.poll() is not None:
            break
        time.sleep(0.05)
    raise AssertionError("condition not reached before the deadline")


# ── 读取原生记录与公开产物 ──────────────────────────────────────


def read_rows(path: Path) -> list[dict]:
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    rows = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def gets(calls_file: Path) -> list[dict]:
    return [record for record in read_records(calls_file) if record["op"] == "get"]


def error_lines(completed: subprocess.CompletedProcess) -> list[str]:
    """The named failure lines of the business stderr; log lines carry a time
    stamp and are compared separately."""
    return [
        line
        for line in completed.stderr.decode("utf-8", "replace").splitlines()
        if line.startswith("error: ")
    ]


def manifest_of(case: Path) -> dict:
    return json.loads((case / "out" / "run_manifest.json").read_text(encoding="utf-8"))


def inbox(case: Path) -> Path:
    return case / "state" / "observations"


def pending_store(case: Path) -> Path:
    """The owner's durable pending store: where a killed run's records live."""
    return case / "state" / "pending"


def delivered(case: Path) -> tuple[list[dict], list[dict]]:
    """The published bundles of the public inbox, each ready mark checked
    against the bytes it describes."""
    bundles = sorted(
        path
        for path in inbox(case).glob("feedback-*.jsonl")
        if not path.name.endswith(".details.jsonl")
    )
    assert bundles, "nothing was published to the public inbox"
    rows: list[dict] = []
    details: list[dict] = []
    for bundle in bundles:
        sidecar = bundle.with_name(bundle.name + ".details.jsonl")
        mark = json.loads(bundle.with_suffix(".ready").read_bytes())
        assert mark["schema_version"] == "feedback-delivery-ready/1"
        assert mark["observations_sha256"] == hashlib.sha256(bundle.read_bytes()).hexdigest()
        assert mark["details_sha256"] == hashlib.sha256(sidecar.read_bytes()).hexdigest()
        assert mark["observations"] == len(read_rows(bundle))
        rows += read_rows(bundle)
        details += read_rows(sidecar)
    return rows, details


def out_rows(out_path: Path) -> tuple[list[dict], list[dict]]:
    """The single file and its details sibling of the explicit OUT mode."""
    return read_rows(out_path), read_rows(Path(str(out_path) + ".details.jsonl"))


def step_rows(rows: list[dict], step_id: str | None) -> list[dict]:
    return [row for row in rows if row.get("step_id") == step_id]


def check_row(rows: list[dict], step_id: str | None, check_id: str) -> dict:
    matching = [row for row in step_rows(rows, step_id) if row["check_id"] == check_id]
    assert len(matching) == 1, (step_id, check_id)
    return matching[0]


def detail_for(details: list[dict], reference: str) -> dict:
    matching = [row for row in details if row.get("details_id") == reference]
    assert len(matching) == 1, reference
    return matching[0]


def item_steps(rows: list[dict], *known: str) -> list[str]:
    """The item step ids, apart from the whole-batch step and known ones."""
    return sorted({row["step_id"] for row in rows} - {BATCH, *known})


def assert_once_each(rows: list[dict]) -> None:
    identities = [
        (row["run_id"], row["attempt_id"], row["step_id"], row["check_id"]) for row in rows
    ]
    assert len(identities) == len(set(identities)), identities


def assert_sanitized(payload: object, *needles: str) -> None:
    """The key, the temporary paths and the original arguments never enter a
    public artifact."""
    blob = json.dumps(payload, ensure_ascii=False)
    for needle in (OFFLINE_KEY, *needles):
        assert needle not in blob, needle


def assert_item(
    rows: list[dict],
    details: list[dict],
    request_id: str,
    *,
    status: str,
    error_code: str | None = None,
    stage: str,
    dispatched: bool,
    ledger_entry: bool = True,
    operation: str | None = None,
    ticker: str | None = None,
    fiscal_period: str | None = None,
) -> dict:
    """One item's observations agree with its own native facts."""
    own = step_rows(rows, request_id)
    checks = [row["check_id"] for row in own]
    assert len(checks) == len(set(checks)), checks
    item_row = check_row(rows, request_id, ITEM)
    assert (item_row["expected"], item_row["actual"]) == (status, status)
    assert item_row["disposition"] == "expected"
    assert {row.get("parent_step_id") for row in own} == {BATCH}
    assert {row.get("evidence_ref") for row in own} == {request_id}
    terminal = [row for row in own if row["check_id"] != STARTED]
    assert {row.get("details_ref") for row in terminal} == {item_row["details_ref"]}
    provider = set(checks) - {ITEM, STARTED}
    assert provider == (PROVIDER_CHECKS if dispatched else set()), checks
    assert (STARTED in checks) is dispatched

    detail = detail_for(details, item_row["details_ref"])
    assert detail["phase"] == "finished"
    assert detail["schema_version"] == feedback.DETAIL_SCHEMA
    if ticker is not None:
        assert detail["request"]["ticker"] == ticker
    if fiscal_period is not None:
        assert detail["requested_period"] == fiscal_period
    if operation is not None:
        assert detail["operation"] == operation
    item = detail["batch_item"]
    assert item["status"] == status
    assert item["error_code"] == error_code
    assert (item["stage"], item["dispatched"], item["ledger_entry"]) == (
        stage,
        dispatched,
        ledger_entry,
    )
    if ticker is not None:
        assert item["ticker"] == ticker
    if fiscal_period is not None:
        assert item["fiscal_period"] == fiscal_period
    if dispatched:
        started = detail_for(details, check_row(rows, request_id, STARTED)["details_ref"])
        assert started["phase"] == "started"
        assert started["started_at"] <= detail["started_at"] <= detail["finished_at"]
        assert detail["completion"]["attempted"] is True
    else:
        assert detail["completion"] == {"operation_completed": False, "attempted": False}
    return detail


def assert_batch(
    rows: list[dict], details: list[dict], *, exit_code: int, disposition: str
) -> dict:
    """The whole batch's own outcome: its real exit code, its manifest and its
    measured budget."""
    row = check_row(rows, BATCH, OUTCOME)
    assert (row["expected"], row["actual"], row["disposition"]) == (
        "exit:0",
        f"exit:{exit_code}",
        disposition,
    )
    assert row["evidence_ref"] == BATCH
    detail = detail_for(details, row["details_ref"])
    assert detail["phase"] == "finished"
    assert detail["batch"]["exit_code"] == exit_code
    return detail["batch"]


def _digest(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def owner(
    case: Path,
    env: dict,
    kind: str,
    payload: dict | None = None,
    *,
    directory: Path | None = None,
    sidecar: Path | None = None,
) -> subprocess.CompletedProcess:
    """ET's existing public owner command, run in another real process."""
    argv = [
        PYTHON,
        "-B",
        str(ROOT / "tools" / "feedback_owner.py"),
        "--db",
        str(case / "owner.sqlite"),
    ]
    if directory is not None:
        argv += ["--sidecar-dir", str(directory)]
    if sidecar is not None:
        argv += ["--sidecar", str(sidecar)]
    return subprocess.run(
        [*argv, kind],
        input=json.dumps(payload).encode("utf-8") if payload is not None else b"",
        capture_output=True,
        creationflags=NO_CONSOLE,
        cwd=str(case),
        env=env,
        timeout=60,
        check=False,
    )


def reconcile(
    case: Path,
    env: dict,
    row: dict,
    *,
    directory: Path | None = None,
    sidecar: Path | None = None,
) -> dict:
    """A read-only reconciliation of the original attempt (the existing F4
    recovery lookup entry)."""
    observation = {**row, "origin_owner": OWNER}
    occurrence = _digest([observation[name] for name in IDENTITY])
    body = {"occurrence_id": occurrence, "observation": observation}
    action_id = _digest(["reconcile", OWNER, occurrence])
    request = {
        "schema_version": "feedback-action/1",
        "owner": OWNER,
        "action_id": action_id,
        "kind": "reconcile",
        "method": "lookup",
        "payload": body,
        "request_digest": _digest([OWNER, "reconcile", action_id, body]),
    }
    completed = owner(case, env, "reconcile", request, directory=directory, sidecar=sidecar)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert not completed.stderr
    return json.loads(completed.stdout)


def expect_reconcile(
    request_id: str, *, job_status: str, cost_state: str, status: str
) -> dict:
    """The owner's read-only answer for one item."""
    return {
        "state": "done",
        "result": {
            "status": status,
            "job_status": job_status,
            "cost_state": cost_state,
            "evidence_ref": request_id,
        },
    }


def business_facts(case: Path, completed: subprocess.CompletedProcess) -> dict:
    """The business result, without the facts that differ on every run."""
    out = case / "out"
    manifest = manifest_of(case)
    return {
        "exit": completed.returncode,
        "stdout": re.sub(
            r"\d+\.\d\ds", "Xs", completed.stdout.decode("utf-8", "replace")
        ),
        "stderr": [
            re.sub(r"\d+\.\d\ds", "Xs", line)
            for line in completed.stderr.decode("utf-8", "replace").splitlines()
            if " [INFO] " not in line and " [WARNING] " not in line
        ],
        "manifest": {
            "manifest_version": manifest["manifest_version"],
            "source": manifest["source"],
            "periods": manifest["periods"],
            "translate": manifest["translate"],
            "budget": manifest["budget"],
            "entries": [
                {
                    key: value
                    for key, value in entry.items()
                    if key not in ("request_id", "elapsed_seconds", "file")
                }
                for entry in manifest["entries"]
            ],
        },
        "txt": {
            path.relative_to(out).as_posix(): [
                line
                for line in path.read_text(encoding="utf-8").splitlines()
                if not line.startswith("Scraped: ")
            ]
            for path in sorted(out.rglob("*_earnings_call.txt"))
        },
        "files": sorted(
            path.relative_to(out).as_posix() for path in out.rglob("*") if path.is_file()
        ),
    }


# ── 夹具 ────────────────────────────────────────────────────────


def http_500(url: str = FMP_URL) -> FakeResponse:
    return FakeResponse(url, b"", status_code=500)


def fmp_json(payload: bytes) -> FakeResponse:
    return FakeResponse(FMP_URL, payload, content_type="application/json")


def stalled() -> FakeResponse:
    """An FMP answer held inside ``Session.get`` (the existing StallResponse)."""
    return StallResponse(FMP_URL, stall_in_get=True)


EMPTY_FMP = fmp_json(b"[]")
MSFT_Q4 = fmp_json(fmp_payload(symbol="MSFT", period="Q4", year=2025, date="2025-11-20"))
MSFT_Q1 = fmp_json(fmp_payload(symbol="MSFT", period="Q1", year=2026, date="2026-04-22"))
SNOW_Q4 = fmp_json(fmp_payload(symbol="SNOW", period="Q4", year=2025, date="2025-12-03"))
SNOW_Q1 = fmp_json(fmp_payload(symbol="SNOW", period="Q1", year=2026, date="2026-05-28"))
TWO_PERIODS = ["--periods", "2025Q4,2026Q1"]


def fmp_argv(ticker: str, output: Path, *extra: str) -> list[str]:
    return ["--source", "fmp", "--ticker", ticker, *TWO_PERIODS, *extra, "--output", str(output)]


def fool_argv(output: Path, *extra: str) -> list[str]:
    return ["--source", "fool", "--ticker", "MSFT", *extra, "--output", str(output)]


def fool_listing(page: FakeResponse, *links: str) -> dict[str, FakeResponse]:
    """One roster discovery plus one body page; ``links`` defaults to 2026 Q2."""
    listed = quote_html(*(links or (MSFT_Q2_URL,)))
    return {MSFT_QUOTE_URL: FakeResponse(MSFT_QUOTE_URL, listed), MSFT_Q2_URL: page}


def seeded_original(
    out: Path, ticker: str, label: str, header_ticker: str, header_quarter: str
) -> Path:
    """A stored original in the existing header format."""
    path = out / ticker / f"{ticker}_{label.replace(' ', '_')}_earnings_call.txt"
    path.parent.mkdir(parents=True)
    sep = "=" * 70
    body = "Stored body."
    path.write_text(
        f"{sep}\nEarnings Call Transcript\n"
        f"Company: Stored ({ticker})\nTicker: {header_ticker}\nQuarter: {header_quarter}\n"
        f"Source: fmp_api\nURL: N/A\nCharacters: {len(body)}\n{sep}\n\n{body}",
        encoding="utf-8",
    )
    return path


# ── 卡面基线：两期 HTTP 500 在两种投递模式下逐项可查 ────────────


@pytest.mark.parametrize("mode", ["default", "out"])
def test_two_http_500_periods_are_recoverable_in_both_delivery_modes(short_root, mode):
    """The card's baseline: two periods answer HTTP 500. The explicit OUT mode
    and the default directory mode each exit 1, each keep two native
    ``provider_error/provider_http_500`` records and two fixture requests.
    Before the fix the feedback files were zero; now every period is
    retrievable by its original identity, the cost stays unknown and the whole
    batch's exit code and manifest stay the ones the business produced."""
    case = new_case(short_root, f"two-500-{mode}")
    env = make_env(case)
    out_path = None
    if mode == "out":
        env, out_path = with_out(case)
    env["RESEARCH_FEEDBACK_RUN_ID"] = "f4-two-500"
    env["RESEARCH_FEEDBACK_ATTEMPT_ID"] = "one"
    calls = case / "calls.jsonl"

    completed = run_batch(
        case,
        fmp_argv("MSFT", case / "out"),
        batch_spec(periods={"2025-Q4": http_500(), "2026-Q1": http_500()}, calls_file=calls),
        env=env,
    )

    assert completed.returncode == 1
    assert error_lines(completed) == [
        "error: MSFT 2025-Q4: provider_error/provider_http_500",
        "error: MSFT 2026-Q1: provider_error/provider_http_500",
    ]
    stdout = completed.stdout.decode("utf-8")
    assert "  provider_error=2" in stdout
    assert "  exit: failure" in stdout
    manifest = manifest_of(case)
    assert [
        (entry["fiscal_period"], entry["status"], entry["error_code"])
        for entry in manifest["entries"]
    ] == [
        ("2025-Q4", "provider_error", "provider_http_500"),
        ("2026-Q1", "provider_error", "provider_http_500"),
    ]
    assert len(gets(calls)) == 2 == manifest["budget"]["requests_used"]
    assert not list((case / "out").rglob("*_earnings_call.txt"))

    rows, details = out_rows(out_path) if mode == "out" else delivered(case)
    assert_once_each(rows)
    assert {(row["run_id"], row["attempt_id"]) for row in rows} == {("f4-two-500", "one")}
    for entry in manifest["entries"]:
        detail = assert_item(
            rows,
            details,
            entry["request_id"],
            status="provider_error",
            error_code="provider_http_500",
            stage="fetch",
            dispatched=True,
            operation="fetch",
            ticker="MSFT",
            fiscal_period=entry["fiscal_period"],
        )
        assert detail["result"]["status"] == "provider_error"
        assert detail["reason_chain"] == ["provider_http_500"]
        assert detail["source_availability"] == "source_unknown"
        assert detail["usage"]["native_receipt"] == {
            "schema_version": "earnings-retrieval-usage/1",
            "request_id": entry["request_id"],
            "usage_complete": True,
            "usage": {"requests_used": 1, "response_bytes_used": 0, "exhausted": None},
        }
        assert detail["batch_item"]["answer_bytes"] == 0
        naming = check_row(rows, entry["request_id"], "outcome-naming")
        assert (naming["expected"], naming["actual"], naming["disposition"]) == (
            "provider_error",
            "provider_error",
            "expected",
        )
        usage = check_row(rows, entry["request_id"], "usage-accounting")
        assert (usage["expected"], usage["actual"], usage["disposition"]) == (
            "usage_known",
            "usage_known",
            "expected",
        )
    summary = assert_batch(rows, details, exit_code=1, disposition="deviation")
    assert summary["manifest"] == {
        "reference": "run_manifest.json",
        "sha256": hashlib.sha256((case / "out" / "run_manifest.json").read_bytes()).hexdigest(),
        "entries": 2,
    }
    assert summary["items"] == {"total": 2, "by_status": {"provider_error": 2}}
    assert summary["not_reached"] == []
    assert_sanitized((rows, details), str(case), "--periods", "--ticker", "--output")

    # A second process: the existing owner command reconciles the original
    # attempt by its own identity; each period is a failure of unknown cost.
    query = {"directory": inbox(case)} if mode == "default" else {"sidecar": out_path}
    directory = inbox(case) if mode == "default" else out_path.parent
    before = sorted(path.name for path in directory.iterdir())
    for entry in manifest["entries"]:
        answer = reconcile(case, env, check_row(rows, entry["request_id"], ITEM), **query)
        assert answer == expect_reconcile(
            entry["request_id"],
            job_status="failed",
            cost_state="unknown",
            status="cost_unknown",
        )
        for check_id in (*PROVIDER_CHECKS, STARTED):
            same = reconcile(case, env, check_row(rows, entry["request_id"], check_id), **query)
            assert same["result"] == answer["result"], check_id
    batch_answer = reconcile(case, env, check_row(rows, BATCH, OUTCOME), **query)
    assert batch_answer["result"]["job_status"] == "failed"
    assert (batch_answer["result"]["status"], batch_answer["result"]["cost_state"]) == (
        "cost_unknown",
        "unknown",
    )
    assert not (case / "owner.sqlite").exists()
    assert len(gets(calls)) == 2
    assert sorted(path.name for path in directory.iterdir()) == before


# ── T1：第一期明确失败，第二期合法无命中或成功，不传 OUT ────────


def test_first_period_failure_is_found_by_its_original_identity(short_root):
    """The ordinary entry without ``RESEARCH_FEEDBACK_OUT``: the run number is
    generated by the ordinary start. A second process reads the first period's
    failure back by the request ID of the native ledger, the run/attempt of
    the public package and the delivery directory; both periods and the whole
    batch agree with the manifest and with the real exit code."""
    case = new_case(short_root, "first-fails")
    calls = case / "calls.jsonl"
    completed = run_batch(
        case,
        fmp_argv("MSFT", case / "out"),
        batch_spec(periods={"2025-Q4": http_500(), "2026-Q1": EMPTY_FMP}, calls_file=calls),
    )

    assert completed.returncode == 1
    manifest = manifest_of(case)
    first, second = manifest["entries"]
    assert (
        first["status"],
        first["error_code"],
        second["status"],
        "error_code" in second,
    ) == ("provider_error", "provider_http_500", "not_found", False)
    recorded = gets(calls)
    assert [(call["params"]["year"], call["params"]["quarter"]) for call in recorded] == [
        (2025, 4),
        (2026, 1),
    ]
    assert len(recorded) == 2 == manifest["budget"]["requests_used"]
    assert not list((case / "out").rglob("*_earnings_call.txt"))

    rows, details = delivered(case)
    assert_once_each(rows)
    identities = {(row["run_id"], row["attempt_id"]) for row in rows}
    assert len(identities) == 1
    (run_id, attempt_id), = identities
    assert re.fullmatch(r"run-[0-9a-f]{12}", run_id) and attempt_id == "attempt-1"
    first_detail = assert_item(
        rows,
        details,
        first["request_id"],
        status="provider_error",
        error_code="provider_http_500",
        stage="fetch",
        dispatched=True,
        operation="fetch",
        ticker="MSFT",
        fiscal_period="2025-Q4",
    )
    second_detail = assert_item(
        rows,
        details,
        second["request_id"],
        status="not_found",
        stage="fetch",
        dispatched=True,
        operation="fetch",
        ticker="MSFT",
        fiscal_period="2026-Q1",
    )
    assert (first_detail["batch_item"]["item_index"], second_detail["batch_item"]["item_index"]) == (
        1,
        2,
    )
    assert second_detail["result"]["status"] == "not_found"
    assert second_detail["source_availability"] == "source_unavailable"
    assert second_detail["usage"]["native_receipt"]["usage"]["response_bytes_used"] == 2
    summary = assert_batch(rows, details, exit_code=1, disposition="deviation")
    assert summary["manifest"]["sha256"] == hashlib.sha256(
        (case / "out" / "run_manifest.json").read_bytes()
    ).hexdigest()
    assert summary["items"] == {
        "total": 2,
        "by_status": {"not_found": 1, "provider_error": 1},
    }
    assert (summary["mode"], summary["source"], summary["companies"]) == ("batch", "fmp", 1)
    assert summary["periods"] == ["2025-Q4", "2026-Q1"]
    assert_sanitized((rows, details), str(case), "--ticker")

    env = make_env(case)
    where = {"directory": inbox(case)}
    first_answer = reconcile(case, env, check_row(rows, first["request_id"], ITEM), **where)
    assert first_answer == expect_reconcile(
        first["request_id"], job_status="failed", cost_state="unknown", status="cost_unknown"
    )
    second_answer = reconcile(case, env, check_row(rows, second["request_id"], ITEM), **where)
    assert second_answer["result"]["job_status"] == "failed"
    assert (second_answer["result"]["status"], second_answer["result"]["cost_state"]) == (
        "cost_unknown",
        "unknown",
    )
    names = sorted(path.name for path in inbox(case).iterdir())
    assert reconcile(case, env, check_row(rows, first["request_id"], ITEM), **where) == first_answer
    assert sorted(path.name for path in inbox(case).iterdir()) == names
    assert not (case / "owner.sqlite").exists()
    assert len(gets(calls)) == 2


def test_first_period_failure_is_not_covered_by_second_period_success(short_root):
    """A period's failure in one batch is not covered by another period's
    success: the batch still exits 1 and both periods keep their own result."""
    case = new_case(short_root, "fail-then-ok")
    completed = run_batch(
        case,
        fmp_argv("MSFT", case / "out"),
        batch_spec(
            periods={"2025-Q4": http_500(), "2026-Q1": MSFT_Q1},
            calls_file=case / "calls.jsonl",
        ),
    )

    assert completed.returncode == 1
    first, second = manifest_of(case)["entries"]
    assert (first["status"], first["error_code"], second["status"]) == (
        "provider_error",
        "provider_http_500",
        "fetched",
    )
    stored = case / "out" / "MSFT" / "MSFT_Q1_2026_earnings_call.txt"
    assert Path(second["file"]) == stored
    original = stored.read_text(encoding="utf-8")
    assert "Ticker: MSFT\n" in original and "Source: fmp_api\n" in original
    assert "Management discussed customer demand" in original

    rows, details = delivered(case)
    first_detail = assert_item(
        rows,
        details,
        first["request_id"],
        status="provider_error",
        error_code="provider_http_500",
        stage="fetch",
        dispatched=True,
    )
    second_detail = assert_item(
        rows,
        details,
        second["request_id"],
        status="fetched",
        stage="save",
        dispatched=True,
    )
    assert first_detail["source_availability"] == "source_unknown"
    assert second_detail["source_availability"] == "source_delivered"
    assert second_detail["result"]["status"] == "fetched"
    assert second_detail["batch_item"]["file"] == stored.name
    assert second_detail["batch_item"]["content_bytes"] == second["content_bytes"]
    naming = [
        check_row(rows, entry["request_id"], "outcome-naming")["actual"]
        for entry in (first, second)
    ]
    assert naming == ["provider_error", "fetched"]
    summary = assert_batch(rows, details, exit_code=1, disposition="deviation")
    assert summary["items"]["by_status"] == {"fetched": 1, "provider_error": 1}
    where = {"directory": inbox(case)}
    env = make_env(case)
    answers = [
        reconcile(case, env, check_row(rows, entry["request_id"], ITEM), **where)["result"]
        for entry in (first, second)
    ]
    assert [(answer["job_status"], answer["cost_state"]) for answer in answers] == [
        ("failed", "unknown"),
        ("succeeded", "unknown"),
    ]


def test_normal_sample_of_an_unremarkable_company(short_root):
    """Another company without any special case: both periods succeed, the
    original language text is stored and the batch exits 0."""
    case = new_case(short_root, "normal-snow")
    completed = run_batch(
        case,
        fmp_argv("SNOW", case / "out"),
        batch_spec(
            periods={"2025-Q4": SNOW_Q4, "2026-Q1": SNOW_Q1},
            calls_file=case / "calls.jsonl",
        ),
    )

    assert completed.returncode == 0
    assert error_lines(completed) == []
    entries = manifest_of(case)["entries"]
    assert [entry["status"] for entry in entries] == ["fetched", "fetched"]
    stored = sorted(path.name for path in (case / "out").rglob("*_earnings_call.txt"))
    assert stored == ["SNOW_Q1_2026_earnings_call.txt", "SNOW_Q4_2025_earnings_call.txt"]
    text = (case / "out" / "SNOW" / "SNOW_Q4_2025_earnings_call.txt").read_text(encoding="utf-8")
    assert "Ticker: SNOW\n" in text and "Management discussed customer demand" in text

    rows, details = delivered(case)
    for entry in entries:
        assert_item(
            rows,
            details,
            entry["request_id"],
            status="fetched",
            stage="save",
            dispatched=True,
            ticker="SNOW",
        )
    summary = assert_batch(rows, details, exit_code=0, disposition="expected")
    assert summary["items"] == {"total": 2, "by_status": {"fetched": 2}}
    env = make_env(case)
    for entry in entries:
        answer = reconcile(
            case, env, check_row(rows, entry["request_id"], ITEM), directory=inbox(case)
        )
        assert answer == expect_reconcile(
            entry["request_id"],
            job_status="succeeded",
            cost_state="unknown",
            status="cost_unknown",
        )
    assert len(gets(case / "calls.jsonl")) == 2


def test_http_200_empty_list_is_a_legitimate_empty_result(short_root):
    """The card's contrast: two periods answer HTTP 200 with an empty list;
    the batch exits 0 and both items are ``not_found``."""
    case = new_case(short_root, "empty-control")
    completed = run_batch(
        case,
        fmp_argv("MSFT", case / "out"),
        batch_spec(
            periods={"2025-Q4": EMPTY_FMP, "2026-Q1": EMPTY_FMP},
            calls_file=case / "calls.jsonl",
        ),
    )

    assert completed.returncode == 0
    assert error_lines(completed) == [
        "error: MSFT 2025-Q4: not_found/-",
        "error: MSFT 2026-Q1: not_found/-",
    ]
    assert "  exit: ok" in completed.stdout.decode("utf-8")
    entries = manifest_of(case)["entries"]
    assert [(entry["status"], "error_code" in entry) for entry in entries] == [
        ("not_found", False)
    ] * 2
    rows, details = delivered(case)
    for entry in entries:
        detail = assert_item(
            rows,
            details,
            entry["request_id"],
            status="not_found",
            stage="fetch",
            dispatched=True,
        )
        assert detail["batch_item"]["dispatched"] is True
        assert check_row(rows, entry["request_id"], ITEM)["disposition"] == "expected"
    assert_batch(rows, details, exit_code=0, disposition="expected")
    assert not list((case / "out").rglob("*_earnings_call.txt"))


# ── T2：发现后逐项抓取、--list、同外层结果的两个原因 ────────────


@pytest.mark.parametrize(
    "name, page, status, error_code, stage, exit_code, requests",
    [
        ("success", FakeResponse(MSFT_Q2_URL, transcript_html()), "fetched", None, "save", 0, 2),
        ("http-500", http_500(MSFT_Q2_URL), "provider_error", "provider_http_500", "fetch", 1, 2),
        (
            "unparseable",
            FakeResponse(MSFT_Q2_URL, UNPARSEABLE_PAGE),
            "provider_error",
            "provider_response",
            "parse",
            1,
            2,
        ),
    ],
    ids=["success", "http-500", "unparseable"],
)
def test_fool_items_after_discovery_distinguish_two_causes_of_one_result(
    short_root, name, page, status, error_code, stage, exit_code, requests
):
    """Fool: the roster discovery and then the per-period fetch. HTTP 500 and
    an unparseable page share the outer result ``provider_error``, but their
    reason chain and their real stage (fetch / parse) are readable apart; the
    success and the legal empty result are their own contrast."""
    case = new_case(short_root, f"fool-{name}")
    calls = case / "calls.jsonl"
    completed = run_batch(
        case,
        fool_argv(case / "out", "--periods", "2026Q2"),
        batch_spec(fool_listing(page), calls_file=calls),
        mode="fool",
    )

    assert completed.returncode == exit_code, name
    (entry,) = manifest_of(case)["entries"]
    assert (entry["status"], entry.get("error_code")) == (status, error_code)
    assert [call["url"] for call in gets(calls)] == [MSFT_QUOTE_URL, MSFT_Q2_URL]
    assert (case / "out" / "MSFT" / "MSFT_Q2_2026_earnings_call.txt").exists() == (
        name == "success"
    )

    rows, details = delivered(case)
    assert_once_each(rows)
    detail = assert_item(
        rows,
        details,
        entry["request_id"],
        status=status,
        error_code=error_code,
        stage=stage,
        dispatched=True,
        operation="fetch-candidate",
        ticker="MSFT",
        fiscal_period="2026-Q2",
    )
    assert detail["reason_chain"] == ([error_code] if error_code else [])
    (listing_id,) = item_steps(rows, entry["request_id"])
    listing = detail_for(details, check_row(rows, listing_id, ITEM)["details_ref"])
    assert listing["operation"] == "list"
    assert listing["batch_item"]["status"] == "discovered"
    assert listing["batch_item"]["stage"] == "discovery"
    assert listing["batch_item"]["candidate_count"] == 1
    assert listing["batch_item"]["ledger_entry"] is False
    assert listing["batch_item"]["item_index"] == 1
    assert detail["batch_item"]["item_index"] == 2
    summary = assert_batch(
        rows,
        details,
        exit_code=exit_code,
        disposition="deviation" if exit_code else "expected",
    )
    assert summary["mode"] == "batch"
    assert summary["source"] == "fool"
    assert summary["items"]["total"] == 2
    assert summary["items"]["by_status"]["discovered"] == 1
    assert summary["items"]["by_status"][status] == 1
    assert_sanitized((rows, details), str(case), MSFT_Q2_URL, "--periods")
    answer = reconcile(
        case, make_env(case), check_row(rows, entry["request_id"], ITEM), directory=inbox(case)
    )
    assert answer["result"]["job_status"] == ("succeeded" if name == "success" else "failed")
    assert answer["result"]["cost_state"] == "unknown"
    assert len(gets(calls)) == requests


def test_fool_period_without_candidate_is_a_local_empty_item(short_root):
    """The roster holds no candidate for that period: no body request is
    dispatched, the result is a legal empty one and the cost does not apply."""
    case = new_case(short_root, "fool-no-candidate")
    calls = case / "calls.jsonl"
    completed = run_batch(
        case,
        fool_argv(case / "out", "--periods", "2026Q2"),
        batch_spec(
            {MSFT_QUOTE_URL: FakeResponse(MSFT_QUOTE_URL, quote_html(MSFT_Q1_URL))},
            calls_file=calls,
        ),
        mode="fool",
    )

    assert completed.returncode == 0
    (entry,) = manifest_of(case)["entries"]
    assert (entry["status"], "error_code" in entry) == ("not_found", False)
    assert [call["url"] for call in gets(calls)] == [MSFT_QUOTE_URL]
    rows, details = delivered(case)
    detail = assert_item(
        rows,
        details,
        entry["request_id"],
        status="not_found",
        stage="discovery",
        dispatched=False,
        ticker="MSFT",
        fiscal_period="2026-Q2",
    )
    assert detail["completion"] == {"operation_completed": False, "attempted": False}
    assert detail["usage"]["native_receipt"] == {
        "schema_version": "earnings-retrieval-usage/1",
        "request_id": entry["request_id"],
        "usage_complete": True,
        "usage": {"requests_used": 0, "response_bytes_used": 0, "exhausted": None},
    }
    assert sorted(row["check_id"] for row in step_rows(rows, entry["request_id"])) == [ITEM]
    assert_batch(rows, details, exit_code=0, disposition="expected")
    answer = reconcile(
        case, make_env(case), check_row(rows, entry["request_id"], ITEM), directory=inbox(case)
    )
    assert answer == expect_reconcile(
        entry["request_id"],
        job_status="failed",
        cost_state="not_applicable",
        status="known",
    )


def test_default_fool_refusal_is_a_zero_request_item(short_root):
    """Fool is off in the production defaults: an ordinary ``--source fool``
    start is a named refusal that sends no request."""
    case = new_case(short_root, "fool-disabled")
    calls = case / "calls.jsonl"
    completed = run_batch(
        case,
        fool_argv(case / "out", "--periods", "2026Q2"),
        batch_spec(calls_file=calls),
    )

    assert completed.returncode == 1
    assert error_lines(completed) == ["error: MSFT: unavailable/provider_disabled"]
    assert gets(calls) == []
    (entry,) = manifest_of(case)["entries"]
    assert (entry["status"], entry["error_code"], entry["fiscal_period"]) == (
        "unavailable",
        "provider_disabled",
        None,
    )
    rows, details = delivered(case)
    detail = assert_item(
        rows,
        details,
        entry["request_id"],
        status="unavailable",
        error_code="provider_disabled",
        stage="discovery",
        dispatched=True,
        operation="list",
    )
    assert detail["usage"]["native_receipt"]["usage"] == {
        "requests_used": 0,
        "response_bytes_used": 0,
        "exhausted": None,
    }
    assert check_row(rows, entry["request_id"], "usage-accounting")["actual"] == "usage_known"
    assert_batch(rows, details, exit_code=1, disposition="deviation")
    answer = reconcile(
        case, make_env(case), check_row(rows, entry["request_id"], ITEM), directory=inbox(case)
    )
    assert answer == expect_reconcile(
        entry["request_id"],
        job_status="failed",
        cost_state="not_applicable",
        status="known",
    )


@pytest.mark.parametrize(
    "quote, argv, status, error_code, exit_code, shown, stderr_line",
    [
        (
            http_500(MSFT_QUOTE_URL),
            ["--periods", "2026Q2"],
            "provider_error",
            "provider_http_500",
            1,
            None,
            "error: MSFT: provider_error/provider_http_500",
        ),
        (
            FakeResponse(MSFT_QUOTE_URL, b"<html><body>no links</body></html>"),
            ["--periods", "2026Q2"],
            "not_found",
            None,
            0,
            "  [NONE FOUND]",
            None,
        ),
        (
            FakeResponse(MSFT_QUOTE_URL, quote_html(MSFT_Q2_URL)),
            ["--periods", "2026Q2"],
            "discovered",
            None,
            0,
            MSFT_Q2_URL,
            None,
        ),
        (
            FakeResponse(MSFT_QUOTE_URL, quote_html(UNDATED_URL)),
            ["--quarters", "2"],
            "period_unresolved",
            "need_explicit_periods",
            1,
            None,
            "error: MSFT: period_unresolved: recent quarters require explicit FY/Q metadata",
        ),
    ],
    ids=["http-500", "legitimate-empty", "listed", "period-unresolved"],
)
def test_list_mode_items_are_recoverable_without_any_business_write(
    short_root, quote, argv, status, error_code, exit_code, shown, stderr_line
):
    """T2 (``--list``): a failure, a legal empty result, a normal listing and
    an unresolvable period. ``--list`` stays zero-body and zero-write: there
    is no manifest, no log and no lock, and the feedback only lands beside."""
    case = new_case(short_root, "list-" + status)
    calls = case / "calls.jsonl"
    completed = run_batch(
        case,
        fool_argv(case / "out", "--list", *argv),
        batch_spec({MSFT_QUOTE_URL: quote}, calls_file=calls),
        mode="fool",
    )

    assert completed.returncode == exit_code
    assert not (case / "out").exists()
    assert len(gets(calls)) == 1
    stdout = completed.stdout.decode("utf-8")
    assert error_lines(completed) == ([stderr_line] if stderr_line else [])
    if shown is not None:
        assert shown in stdout

    rows, details = delivered(case)
    assert_once_each(rows)
    item_ids = item_steps(rows)
    assert len(item_ids) == (2 if status == "period_unresolved" else 1)
    for item_id in item_ids:
        own = step_rows(rows, item_id)
        assert ITEM in [row["check_id"] for row in own]
    # Items are told apart by their own outcome word, never by file order.
    by_status = {
        detail_for(details, check_row(rows, item_id, ITEM)["details_ref"])[
            "batch_item"
        ]["status"]: item_id
        for item_id in item_ids
    }
    listing_id = by_status.get("discovered", item_ids[0])
    listing = detail_for(details, check_row(rows, listing_id, ITEM)["details_ref"])
    assert listing["operation"] == "list"
    if status == "period_unresolved":
        # The discovery itself succeeded; the unresolvable period is another
        # item that never dispatched a request.
        assert listing["batch_item"]["status"] == "discovered"
        assert listing["batch_item"]["stage"] == "discovery"
        unresolved_id = by_status["period_unresolved"]
        unresolved = detail_for(details, check_row(rows, unresolved_id, ITEM)["details_ref"])
        assert unresolved["batch_item"]["status"] == "period_unresolved"
        assert unresolved["batch_item"]["error_code"] == "need_explicit_periods"
        assert unresolved["batch_item"]["dispatched"] is False
        assert unresolved["batch_item"]["ledger_entry"] is False
        assert sorted(row["check_id"] for row in step_rows(rows, unresolved_id)) == [ITEM]
    else:
        assert listing["batch_item"]["status"] == status
        assert listing["batch_item"]["error_code"] == error_code
        assert listing["batch_item"]["candidate_count"] == (1 if status == "discovered" else 0)
        assert listing["batch_item"]["fiscal_period"] is None
        assert listing["batch_item"]["ledger_entry"] is False
    summary = assert_batch(
        rows,
        details,
        exit_code=exit_code,
        disposition="deviation" if exit_code else "expected",
    )
    assert summary["mode"] == "list"
    assert summary["manifest"] is None
    assert summary["budget"] is not None
    assert summary["items"]["by_status"][status] == 1
    answer = reconcile(
        case, make_env(case), check_row(rows, listing_id, ITEM), directory=inbox(case)
    )
    # The listing itself is judged by its own outcome: a roster that was read
    # succeeded, a roster that failed did not.
    listing_status = listing["batch_item"]["status"]
    assert answer["result"]["job_status"] == (
        "succeeded" if listing_status == "discovered" else "failed"
    )
    assert answer["result"]["cost_state"] == "unknown"  # a request went out; its price is unknown
    if status == "period_unresolved":
        unresolved_answer = reconcile(
            case,
            make_env(case),
            check_row(rows, by_status["period_unresolved"], ITEM),
            directory=inbox(case),
        )
        assert unresolved_answer["result"]["job_status"] == "failed"
        assert unresolved_answer["result"]["cost_state"] == "not_applicable"


def test_budget_exhausted_before_dispatch_is_explicitly_not_dispatched(short_root):
    """T2: the budget runs out before the request is sent. The second period is
    registered, explicitly not dispatched, and its cost does not apply; it has
    no ledger entry and the batch exits 3."""
    case = new_case(short_root, "budget-before-dispatch")
    calls = case / "calls.jsonl"
    completed = run_batch(
        case,
        fmp_argv("MSFT", case / "out", "--max-requests", "1"),
        batch_spec(periods={"2025-Q4": MSFT_Q4, "2026-Q1": MSFT_Q1}, calls_file=calls),
    )

    assert completed.returncode == 3
    assert "  exit: limit_exceeded: request_limit" in completed.stdout.decode("utf-8")
    manifest = manifest_of(case)
    (first,) = manifest["entries"]
    assert first["status"] == "fetched"
    assert (manifest["budget"]["exhausted"], manifest["budget"]["requests_used"]) == (
        "request_limit",
        1,
    )
    assert len(gets(calls)) == 1

    rows, details = delivered(case)
    assert_item(
        rows,
        details,
        first["request_id"],
        status="fetched",
        stage="save",
        dispatched=True,
        ticker="MSFT",
        fiscal_period="2025-Q4",
    )
    (second,) = item_steps(rows, first["request_id"])
    assert sorted(row["check_id"] for row in step_rows(rows, second)) == [STARTED, ITEM]
    item_row = check_row(rows, second, ITEM)
    assert (item_row["expected"], item_row["actual"], item_row["disposition"]) == (
        "not_dispatched",
        "not_dispatched",
        "expected",
    )
    detail = detail_for(details, item_row["details_ref"])
    assert detail["operation"] == "fetch"
    assert (detail["requested_period"], detail["request"]["ticker"]) == ("2026-Q1", "MSFT")
    assert detail["branch"] == "not-dispatched"
    assert detail["completion"] == {"operation_completed": False, "attempted": False}
    assert detail["usage"]["native_receipt"] == {
        "schema_version": "earnings-retrieval-usage/1",
        "request_id": second,
        "usage_complete": True,
        "usage": {"requests_used": 0, "response_bytes_used": 0, "exhausted": None},
    }
    item = detail["batch_item"]
    assert (item["stage"], item["dispatched"], item["ledger_entry"]) == ("fetch", False, False)
    assert (item["status"], item["error_code"], item["stopped_by"]) == (
        "not_dispatched",
        "request_limit",
        "request_limit",
    )
    assert item["item_index"] == 2
    summary = assert_batch(rows, details, exit_code=3, disposition="deviation")
    assert (summary["exhausted"], summary["not_reached"]) == ("request_limit", [])
    assert summary["items"] == {"total": 2, "by_status": {"fetched": 1, "not_dispatched": 1}}
    answer = reconcile(case, make_env(case), item_row, directory=inbox(case))
    assert answer == expect_reconcile(
        second,
        job_status="interrupted",
        cost_state="not_applicable",
        status="known",
    )
    assert len(gets(calls)) == 1


def test_started_item_interrupted_by_batch_deadline_keeps_unknown_usage(short_root):
    """T2: an item already started is interrupted. The request went out, the
    batch's hard deadline stopped the worker; the usage stays unknown, zero
    is never substituted, the item has no ledger entry and the batch exits 3."""
    case = new_case(short_root, "deadline")
    calls = case / "calls.jsonl"
    completed = run_batch(
        case,
        fmp_argv("MSFT", case / "out", "--max-seconds", "5"),
        batch_spec(
            periods={"2025-Q4": MSFT_Q4, "2026-Q1": stalled()},
            calls_file=calls,
            stall_seconds=30,
        ),
    )

    assert completed.returncode == 3
    stdout = completed.stdout.decode("utf-8")
    assert "  exit: limit_exceeded: batch_deadline" in stdout
    assert "(worker ended without a usage report)" in stdout
    manifest = manifest_of(case)
    (first,) = manifest["entries"]
    assert first["status"] == "fetched"
    assert (manifest["budget"]["exhausted"], manifest["budget"]["usage_unknown"]) == (
        "batch_deadline",
        True,
    )
    assert manifest["budget"]["requests_used"] is None

    rows, details = delivered(case)
    assert_item(
        rows,
        details,
        first["request_id"],
        status="fetched",
        stage="save",
        dispatched=True,
        ticker="MSFT",
        fiscal_period="2025-Q4",
    )
    (second,) = item_steps(rows, first["request_id"])
    assert sorted(row["check_id"] for row in step_rows(rows, second)) == sorted(
        [STARTED, ITEM, *PROVIDER_CHECKS]
    )
    usage_row = check_row(rows, second, "usage-accounting")
    assert (usage_row["expected"], usage_row["actual"], usage_row["disposition"]) == (
        "usage_unknown",
        "usage_unknown",
        "unknown",
    )
    assert check_row(rows, second, "outcome-naming")["actual"] == "deadline_exceeded"
    detail = detail_for(details, usage_row["details_ref"])
    assert detail["result"]["error_code"] == "provider_deadline"
    assert detail["reason_chain"] == ["provider_deadline"]
    assert detail["worker"]["supervisor_stage"] == "deadline"
    assert detail["usage"]["native_receipt"] == {
        "schema_version": "earnings-retrieval-usage/1",
        "request_id": second,
        "usage_complete": False,
        "usage": None,
    }
    item = detail["batch_item"]
    assert (item["stage"], item["dispatched"], item["ledger_entry"]) == ("fetch", True, False)
    assert (item["status"], item["error_code"], item["stopped_by"]) == (
        "deadline_exceeded",
        "provider_deadline",
        "batch_deadline",
    )
    summary = assert_batch(rows, details, exit_code=3, disposition="deviation")
    assert (summary["budget"]["usage_unknown"], summary["not_reached"]) == (True, [])
    answer = reconcile(case, make_env(case), usage_row, directory=inbox(case))
    assert answer == expect_reconcile(
        second, job_status="failed", cost_state="unknown", status="cost_unknown"
    )


# ── T3：第一期终态落盘后、下一项尚未完成时硬停 ──────────────────


@pytest.mark.parametrize(
    "ticker, first_page, first_status, job_status",
    [
        ("MSFT", http_500(), "provider_error", "failed"),
        ("SNOW", SNOW_Q4, "fetched", "succeeded"),
    ],
    ids=["completed-failure", "normal-sample"],
)
@pytest.mark.parametrize("mode", ["default", "out"])
def test_hard_stop_between_items_leaves_completed_item_and_unconfirmed_item(
    short_root, mode, ticker, first_page, first_status, job_status
):
    """The first period's terminal state is on disk while the next one has not
    finished: the isolated process is hard-stopped. A new process finds the
    completed first period and the unconfirmed second one by their original
    identity; two queries answer the same, a repeated delivery adds no
    business call, and no terminal state is invented for the unconfirmed
    item. The second sample is an ordinary company with an ordinary success."""
    case = new_case(short_root, f"hard-stop-{ticker}-{mode}")
    env = make_env(case)
    out_path = None
    if mode == "out":
        env, out_path = with_out(case)
    env["RESEARCH_FEEDBACK_RUN_ID"] = "f4-hard-stop"
    env["RESEARCH_FEEDBACK_ATTEMPT_ID"] = "one"
    calls = case / "calls.jsonl"
    journal = (
        pending_store(case) / "f4-hard-stop--one.jsonl" if mode == "default" else out_path
    )
    process = start_batch(
        case,
        fmp_argv(ticker, case / "out"),
        batch_spec(
            periods={"2025-Q4": first_page, "2026-Q1": stalled()},
            calls_file=calls,
            stall_seconds=30,
        ),
        env=env,
    )
    try:
        # The second request must really have been sent before the stop, so
        # the item is known to be dispatched and its metering unknown.
        wait_until(
            lambda: len(gets(calls)) == 2
            and len([row for row in read_rows(journal) if row.get("check_id") == ITEM]) == 1
            and len([row for row in read_rows(journal) if row.get("check_id") == STARTED]) == 2,
            process,
        )
        assert not (case / "out" / "run_manifest.json").exists()
    finally:
        kill_tree(process)

    rows = read_rows(journal)
    details = read_rows(Path(str(journal) + ".details.jsonl"))
    assert_once_each(rows)
    started = {row["step_id"]: row for row in rows if row["check_id"] == STARTED}
    completed = {row["step_id"]: row for row in rows if row["check_id"] == ITEM}
    assert len(started) == 2 and len(completed) == 1
    (done,) = completed
    (unconfirmed,) = set(started) - set(completed)
    assert completed[done]["actual"] == first_status
    assert [row["check_id"] for row in step_rows(rows, unconfirmed)] == [STARTED]
    assert step_rows(rows, BATCH) == []
    failure = detail_for(details, completed[done]["details_ref"])
    assert failure["batch_item"]["ledger_entry"] is True
    assert failure["batch_item"]["status"] == first_status
    assert not (case / "out" / "run_manifest.json").exists()
    stored = sorted(path.name for path in (case / "out").rglob("*_earnings_call.txt"))
    assert stored == ([f"{ticker}_Q4_2025_earnings_call.txt"] if first_status == "fetched" else [])
    assert_sanitized((rows, details), str(case), "--ticker")

    where = {"directory": pending_store(case)} if mode == "default" else {"sidecar": out_path}
    query = make_env(case)
    done_answer = reconcile(case, query, completed[done], **where)
    assert done_answer == expect_reconcile(
        done, job_status=job_status, cost_state="unknown", status="cost_unknown"
    )
    unknown_answer = reconcile(case, query, started[unconfirmed], **where)
    assert unknown_answer == expect_reconcile(
        unconfirmed, job_status="unconfirmed", cost_state="unknown", status="cost_unknown"
    )
    # The unconfirmed item has no terminal state: a forged one is not found.
    forged = {**started[unconfirmed], "check_id": ITEM, "expected": "fetched", "actual": "fetched"}
    assert reconcile(case, query, forged, **where) == {"state": "unknown"}
    # Asking again answers the same; the reconciliation is read-only.
    assert reconcile(case, query, completed[done], **where) == done_answer
    assert reconcile(case, query, started[unconfirmed], **where) == unknown_answer
    assert not (case / "owner.sqlite").exists()

    # A repeated delivery: two more processes under the same identity that
    # request nothing register each occurrence exactly once.
    for _ in range(2):
        again = redeliver(env)
        assert again.returncode == 0, again.stderr
        assert again.stdout == b""
        assert again.stderr == b""
    again_rows, again_details = out_rows(out_path) if mode == "out" else delivered(case)
    assert_once_each(again_rows)
    key = lambda row: (row["step_id"], row["check_id"])  # noqa: E731
    assert sorted(again_rows, key=key) == sorted(rows, key=key)
    assert {row["details_id"] for row in again_details} == {row["details_id"] for row in details}
    assert len(gets(calls)) == 2
    names = sorted(path.name for path in inbox(case).iterdir()) if mode == "default" else []
    assert redeliver(env).returncode == 0
    if mode == "default":
        assert sorted(path.name for path in inbox(case).iterdir()) == names
        third, _ = delivered(case)
        assert sorted(third, key=key) == sorted(rows, key=key)
    assert len(gets(calls)) == 2


# ── 本地原件：不派发的项与同外层结果的不同原因 ──────────────────


def test_existing_originals_are_verified_locally_without_any_request(short_root):
    """The original is already on this machine: every item is verified
    locally, no request is sent and the cost does not apply; a legitimate
    reuse and two different causes of the same ``output_conflict`` are
    readable apart."""
    first_case = new_case(short_root, "existing-first")
    completed = run_batch(
        first_case,
        fmp_argv("MSFT", first_case / "out"),
        batch_spec(
            periods={"2025-Q4": MSFT_Q4, "2026-Q1": MSFT_Q1},
            calls_file=first_case / "calls.jsonl",
        ),
    )
    assert completed.returncode == 0

    reused_case = new_case(short_root, "existing-reused")
    reused_calls = reused_case / "calls.jsonl"
    # The second run points at the same output root; no answer is registered,
    # so any HTTP request would fail the worker.
    reused = run_batch(
        reused_case,
        fmp_argv("MSFT", first_case / "out"),
        batch_spec(calls_file=reused_calls),
    )
    assert reused.returncode == 0
    assert gets(reused_calls) == []
    reused_entries = manifest_of(first_case)["entries"]
    assert [entry["status"] for entry in reused_entries] == ["reused", "reused"]
    rows, details = delivered(reused_case)
    for entry in reused_entries:
        assert_item(
            rows,
            details,
            entry["request_id"],
            status="reused",
            stage="save",
            dispatched=False,
            ticker="MSFT",
        )
    answer = reconcile(
        case=reused_case,
        env=make_env(reused_case),
        row=check_row(rows, reused_entries[0]["request_id"], ITEM),
        directory=inbox(reused_case),
    )
    assert answer == expect_reconcile(
        reused_entries[0]["request_id"],
        job_status="succeeded",
        cost_state="not_applicable",
        status="known",
    )

    conflicts = {}
    for name, label, header_ticker, header_quarter, reason in (
        ("identity", "Q4 2025", "OTHER", "Q4 2025", "identity_mismatch"),
        ("period", "Q4 2025", "MSFT", "Q1 2026", "period_mismatch"),
    ):
        case = new_case(short_root, f"existing-{name}")
        seeded = seeded_original(case / "out", "MSFT", label, header_ticker, header_quarter)
        before = seeded.read_bytes()
        case_calls = case / "calls.jsonl"
        completed = run_batch(
            case,
            [
                "--source",
                "fmp",
                "--ticker",
                "MSFT",
                "--periods",
                "2025Q4",
                "--output",
                str(case / "out"),
            ],
            batch_spec(calls_file=case_calls),
        )
        assert completed.returncode == 1
        assert error_lines(completed) == [f"error: MSFT 2025-Q4: output_conflict/{reason}"]
        assert gets(case_calls) == [] and seeded.read_bytes() == before
        (entry,) = manifest_of(case)["entries"]
        assert (entry["status"], entry["error_code"]) == ("output_conflict", reason)
        rows, details = delivered(case)
        conflicts[name] = assert_item(
            rows,
            details,
            entry["request_id"],
            status="output_conflict",
            error_code=reason,
            stage="save",
            dispatched=False,
        )
        answer = reconcile(
            case, make_env(case), check_row(rows, entry["request_id"], ITEM), directory=inbox(case)
        )
        assert answer == expect_reconcile(
            entry["request_id"],
            job_status="failed",
            cost_state="not_applicable",
            status="known",
        )
    assert (
        conflicts["identity"]["batch_item"]["status"]
        == conflicts["period"]["batch_item"]["status"]
    )
    assert {name: detail["batch_item"]["error_code"] for name, detail in conflicts.items()} == {
        "identity": "identity_mismatch",
        "period": "period_mismatch",
    }


# ── 业务原返回不变 ──────────────────────────────────────────────


@pytest.mark.parametrize("off", ["empty-out", "disabled"])
def test_business_output_is_identical_with_and_without_feedback(short_root, off):
    """The business stdout, exit code, manifest and original language text do
    not change because of the feedback side channel: the same fixture with
    the feedback off and with the ordinary direct start are item for item the
    same, apart from the facts that differ on every run."""
    scenarios = {
        "fail-and-empty": {"2025-Q4": http_500(), "2026-Q1": EMPTY_FMP},
        "fail-and-fetched": {"2025-Q4": http_500(), "2026-Q1": MSFT_Q1},
    }
    for name, periods in scenarios.items():
        silent = new_case(short_root, f"identity-{off}-{name}-off")
        silent_env = make_env(silent)
        if off == "empty-out":
            silent_env["RESEARCH_FEEDBACK_OUT"] = ""  # the host switched it off
        else:
            silent_env["RESEARCH_FEEDBACK_DISABLED"] = "1"
        ordinary = new_case(short_root, f"identity-{off}-{name}-on")
        argv = ["--source", "fmp", "--ticker", "MSFT", *TWO_PERIODS]
        facts = []
        for case, env in ((silent, silent_env), (ordinary, make_env(ordinary))):
            completed = run_batch(
                case,
                [*argv, "--output", str(case / "out")],
                batch_spec(periods=periods, calls_file=case / "calls.jsonl"),
                env=env,
            )
            facts.append(business_facts(case, completed))
        assert facts[0] == facts[1], name
        assert not pending_store(silent).exists()
        assert not inbox(silent).exists()
        local_trace = read_rows(silent / "state" / "logs" / "runtime_retrieval.log")
        assert any(row["phase"] == "provider_phase_entered" for row in local_trace)
        rows, _ = delivered(ordinary)
        assert len([row for row in rows if row["check_id"] == ITEM]) == 2


def test_dry_run_stays_zero_http_and_zero_write(short_root):
    """``--dry-run`` has no work item at all: zero HTTP, zero writes and no
    observation."""
    case = new_case(short_root, "dry-run")
    completed = run_batch(
        case,
        [
            "--source",
            "fmp",
            "--ticker",
            "MSFT",
            *TWO_PERIODS,
            "--dry-run",
            "--output",
            str(case / "out"),
        ],
        batch_spec(calls_file=case / "calls.jsonl"),
    )
    assert completed.returncode == 0
    assert "download: 2" in completed.stdout.decode("utf-8")
    assert gets(case / "calls.jsonl") == [] and not (case / "out").exists()
    published = list((case / "state").rglob("*.jsonl"))
    assert published == []


def test_unwritable_feedback_location_keeps_the_business_return(short_root):
    """A failure of the feedback side channel does not change the business
    result: with an unwritable single file the records are saved in the
    owner's durable store. The human stderr gets one safe advisory, while
    the exit code, business output, manifest and text match the feedback-off run."""
    reference = new_case(short_root, "unwritable-off")
    reference_env = make_env(reference)
    reference_env["RESEARCH_FEEDBACK_OUT"] = ""
    blocked = new_case(short_root, "unwritable-on")
    (blocked / "sink").write_bytes(b"not a directory")
    env = make_env(blocked)
    env["RESEARCH_FEEDBACK_OUT"] = str(blocked / "sink" / "observations.jsonl")
    argv = ["--source", "fmp", "--ticker", "MSFT", *TWO_PERIODS]
    periods = {"2025-Q4": http_500(), "2026-Q1": MSFT_Q1}
    facts = []
    for case, case_env in ((reference, reference_env), (blocked, env)):
        completed = run_batch(
            case,
            [*argv, "--output", str(case / "out")],
            batch_spec(periods=periods, calls_file=case / "calls.jsonl"),
            env=case_env,
        )
        facts.append(business_facts(case, completed))
    warnings = [line for line in facts[1]["stderr"] if line.startswith("warning: et_feedback_incomplete")]
    warning, = warnings
    assert re.fullmatch(r"warning: et_feedback_incomplete \([A-Za-z_]+\)", warning)
    facts[1]["stderr"].remove(warning)
    assert facts[0] == facts[1]
    assert facts[0]["exit"] == 1
    assert not (blocked / "sink" / "observations.jsonl").exists()
    saved = [
        row
        for path in (blocked / "state" / "pending").glob("*.jsonl")
        if not path.name.endswith(".details.jsonl")
        for row in read_rows(path)
    ]
    assert len([row for row in saved if row["check_id"] == ITEM]) == 2
    native_log = (blocked / "state" / "logs" / "runtime_errors.log").read_text(encoding="utf-8")
    assert "feedback_sidecar_failure" in native_log
    assert "blocked" not in native_log and OFFLINE_KEY not in native_log


@pytest.mark.parametrize(
    "argv, refusal_code",
    [
        (["--source", "fmp", "--list", "--periods", "2026Q3"], "candidate_discovery_unavailable"),
        (["--source", "fmp", "--quarters", "2"], "period_unresolved"),
    ],
    ids=["fmp-list", "fmp-recent-quarters"],
)
def test_batch_entry_gates_leave_their_original_code(short_root, argv, refusal_code):
    """A gate of the batch entry keeps its original code: zero HTTP, zero
    business writes, exit 1 and the refusal recorded with its own code."""
    case = new_case(short_root, "gate-" + refusal_code)
    completed = run_batch(
        case,
        [*argv, "--output", str(case / "out")],
        batch_spec(calls_file=case / "calls.jsonl"),
    )
    assert completed.returncode == 1
    assert gets(case / "calls.jsonl") == [] and not (case / "out").exists()
    rows, details = delivered(case)
    refusals = [row for row in rows if row["check_id"] == feedback.CHECK_INVOCATION_REFUSAL]
    assert len(refusals) == 1
    record = detail_for(details, refusals[0]["details_ref"])
    assert (record["entry"], record["refusal_code"]) == ("scraper", refusal_code)
    assert record["stage"] and record["location"]
    assert not [row for row in rows if row["check_id"] in (ITEM, OUTCOME)]


def test_instance_lock_conflict_leaves_its_original_code(short_root):
    """Another instance holds the lock: exit 2, the original message and the
    refusal recorded with its own code."""
    case = new_case(short_root, "lock")
    lock_path = case / "out" / ".instance.lock"
    lock_path.parent.mkdir()
    handle = open(lock_path, "a+b")  # noqa: SIM115 - the lock is held until the child exits
    try:
        if os.name == "nt":
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:  # pragma: no cover - POSIX
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        completed = run_batch(
            case,
            fmp_argv("MSFT", case / "out"),
            batch_spec(calls_file=case / "calls.jsonl"),
        )
    finally:
        handle.close()
    assert completed.returncode == 2
    assert "已有 scraper/translate 实例在运行" in completed.stderr.decode("utf-8")
    assert gets(case / "calls.jsonl") == []
    assert not (case / "out" / "run_manifest.json").exists()
    rows, details = delivered(case)
    (refusal,) = rows
    assert refusal["check_id"] == feedback.CHECK_INVOCATION_REFUSAL
    record = detail_for(details, refusal["details_ref"])
    assert (record["entry"], record["refusal_code"]) == ("scraper", "instance_lock_held")


# ── 进程内单元：旁路自身绝不影响业务 ────────────────────────────


def test_batch_journal_writes_one_terminal_per_item(tmp_path):
    """The journal's own contract: one started row before a request may be
    dispatched, one terminal group per item, one whole-batch outcome, and no
    write failure that reaches the caller."""
    out_path = tmp_path / "unit.jsonl"
    recorder = feedback.FeedbackRecorder(out_path, run_id="unit", attempt_id="one")
    journal = feedback.BatchJournal(recorder)
    journal.begin(
        operation="fetch",
        request={"request_id": "et-s3-b", "ticker": "ACME"},
    )
    journal.complete(
        operation="fetch",
        request={"request_id": "et-s3-b", "ticker": "ACME", "fiscal_year": 2025, "fiscal_quarter": 4},
        result={"status": "provider_error", "error_code": "retrieval_worker_failure"},
        usage=None,
        worker={"supervisor_stage": "worker_exit", "worker_exit_code": 7},
        item={
            "ticker": "ACME",
            "fiscal_period": "2025-Q4",
            "stage": "fetch",
            "dispatched": True,
            "status": "provider_error",
            "error_code": "retrieval_worker_failure",
            "ledger_entry": True,
        },
    )
    journal.complete(
        operation="fetch",
        request={"request_id": "et-s3-c", "ticker": "ACME"},
        result={},
        usage={"requests_used": 0, "response_bytes_used": 0, "exhausted": None},
        worker=None,
        item={
            "ticker": "ACME",
            "fiscal_period": None,
            "stage": "fetch",
            "dispatched": False,
            "status": "not_dispatched",
            "error_code": "request_limit",
            "ledger_entry": False,
            "stopped_by": "request_limit",
        },
    )
    journal.note_run(
        mode="batch",
        source="fmp",
        periods=["2025-Q4"],
        companies=1,
        manifest={"reference": "run_manifest.json", "sha256": "0" * 64, "entries": 1},
    )
    journal.summary(1)

    assert (recorder.write_failures, recorder.diagnostics) == (0, [])
    rows, details = out_rows(out_path)
    assert sorted((row["step_id"], row["check_id"]) for row in rows) == sorted(
        [
            ("et-s3-b", STARTED),
            ("et-s3-b", "outcome-naming"),
            ("et-s3-b", "exact-period-ownership"),
            ("et-s3-b", "content-reference"),
            ("et-s3-b", "usage-accounting"),
            ("et-s3-b", ITEM),
            ("et-s3-c", ITEM),
            (BATCH, OUTCOME),
        ]
    )
    worker = detail_for(details, check_row(rows, "et-s3-b", ITEM)["details_ref"])
    assert worker["result"]["error_code"] == "retrieval_worker_failure"
    assert worker["worker"]["worker_exit_code"] == 7
    assert worker["reason_chain"] == ["retrieval_worker_failure"]
    assert check_row(rows, "et-s3-b", "usage-accounting")["disposition"] == "unknown"
    local = detail_for(details, check_row(rows, "et-s3-c", ITEM)["details_ref"])
    assert local["requested_period"] is None
    assert local["batch_item"]["dispatched"] is False
    assert local["batch_item"]["stopped_by"] == "request_limit"
    batch = detail_for(details, check_row(rows, BATCH, OUTCOME)["details_ref"])
    assert batch["batch"]["exit_code"] == 1
    assert batch["batch"]["items"]["total"] == 2


def test_batch_journal_never_propagates_a_recorder_failure():
    """A failure of the feedback side channel stays inside the diagnostic."""

    class Exploding:
        def __init__(self) -> None:
            self.notes: list[str] = []

        def _note_failure(self, kind: str) -> None:
            self.notes.append(kind)

        def record(self, **kwargs: Any) -> bool:
            raise RuntimeError("recorder unavailable")

    recorder = Exploding()
    journal = feedback.BatchJournal(recorder)
    journal.begin(
        operation="fetch",
        request={"request_id": "et-s3-a", "ticker": "ACME"},
    )
    journal.complete(
        operation="fetch",
        request={"request_id": "et-s3-b", "ticker": "ACME"},
        result={},
        usage=None,
        worker=None,
        item={
            "ticker": "ACME",
            "fiscal_period": None,
            "stage": "fetch",
            "dispatched": True,
            "status": "provider_error",
            "error_code": "retrieval_worker_failure",
        },
    )
    journal.note_run(mode="batch", source="fmp", periods=None, companies=1, manifest=None)
    journal.summary(0)
    assert recorder.notes
    assert all(note.startswith("batch_feedback_failed") for note in recorder.notes)

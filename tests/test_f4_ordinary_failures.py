"""F4-ET-FAILURES：普通入口、坏 TXT 与 worker 失败的隔离回归。

对应计划卡 ``docs/plans/feedback-loop-2026-11-17/F4-ET-FAILURES.md``，覆盖中央
档案 ``failure-coverage-20261121.jsonl`` 第 10/11 行的两条原发生：

- 第 10 行 ``native-failure-not-forwarded``：普通 CLI 拒绝、scraper 参数拒绝、
  TXT 审计（根缺失与逐文件损坏）此前不产生任何观察；
- 第 11 行 ``provider-cause-lost``：worker 子进程退出码、阶段与脱敏异常内因
  此前被丢弃，用量只能停在 unknown。

所有隔离入口都在真实 Python 子进程中调用。每个用例把配置、用户数据根、工作
目录、日志、数据库、暂存与输出映射到同一个测试专用临时根；进程环境继承自本
进程但清除继承的 ``RESEARCH_FEEDBACK_*``，默认路径用例把本机状态根映射到临时
目录；显式设置过的单次文件用例另行覆盖。网络由测试专用 ``sitecustomize``
审计钩子阻断；worker 的 HTTP 会话由既有私有 launcher 接缝替换，绝不访问提供
方。原生 CLI 退出码、业务 stdout/stderr 与文件操作全部保留。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable

from tests.test_feedback_observation import FEEDBACK_FAKE_KEY  # noqa: E402
from tests.test_retrieval_runtime import LAUNCHER, fake_spec  # noqa: E402
from tests.test_transcript_api import make_request  # noqa: E402

#: 测试专用密钥常量：只进入离线假会话，断言其绝不落在任何观察、明细或收据里。
OFFLINE_KEY = FEEDBACK_FAKE_KEY

GUARD_SITECUSTOMIZE = (
    "import sys\n"
    "def check(event, args):\n"
    "    if event in ('socket.connect', 'socket.getaddrinfo'):\n"
    "        raise RuntimeError('audit forbids external access')\n"
    "sys.addaudithook(check)\n"
)

READBACK_SCRIPT = (
    "import json, sys\n"
    "out, details = sys.argv[1], sys.argv[2]\n"
    "def rows(path):\n"
    "    try:\n"
    "        raw = open(path, 'rb').read()\n"
    "    except OSError:\n"
    "        return []\n"
    "    rows = []\n"
    "    for line in raw.decode('utf-8', errors='replace').splitlines():\n"
    "        line = line.strip()\n"
    "        if not line:\n"
    "            continue\n"
    "        try:\n"
    "            rows.append(json.loads(line))\n"
    "        except ValueError:\n"
    "            rows.append({'unparseable': True})\n"
    "    return rows\n"
    "print(json.dumps({'observations': rows(out), 'details': rows(details)}))\n"
)

AUDIT_REPORT_NAME = "transcript_audit_report.json"
AUDIT_SEPARATOR = "=" * 70


# ── 隔离装置 ────────────────────────────────────────────────────


def make_env(tmp_root: Path, *, state_root: Path | None = None) -> dict:
    """One ordinary process environment, everything mapped into ``tmp_root``."""
    guard = tmp_root / "guard"
    guard.mkdir(parents=True, exist_ok=True)
    (guard / "sitecustomize.py").write_text(GUARD_SITECUSTOMIZE, encoding="utf-8")
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper()
        in {
            "PATH",
            "PATHEXT",
            "SYSTEMROOT",
            "WINDIR",
            "SYSTEMDRIVE",
            "COMSPEC",
            "USERPROFILE",
            "HOME",
        }
    }
    for name in (
        "RESEARCH_FEEDBACK_OUT",
        "RESEARCH_FEEDBACK_RUN_ID",
        "RESEARCH_FEEDBACK_ATTEMPT_ID",
        "RESEARCH_FEEDBACK_DIR",
        "RESEARCH_FEEDBACK_DISABLED",
        "RESEARCH_FEEDBACK_STATE_ROOT",
        "FMP_API_KEY",
        "FMP_API_KEY_FILE",
    ):
        env.pop(name, None)
    temp = tmp_root / "temp"
    temp.mkdir(parents=True, exist_ok=True)
    env.update(
        PYTHONPATH=str(guard),
        PYTHONUTF8="1",
        PYTHONDONTWRITEBYTECODE="1",
        TEMP=str(temp),
        TMP=str(temp),
        TMPDIR=str(temp),
        # 默认路径用例：本机状态根映射到测试临时根（卡内 A 节约定）。
        RESEARCH_FEEDBACK_STATE_ROOT=str(state_root or tmp_root / "state"),
        FMP_API_KEY=OFFLINE_KEY,
    )
    return env


def run_entry(
    env: dict,
    argv: list[str],
    *,
    stdin: bytes = b"",
    cwd: Path | None = None,
    timeout: float = 60,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PYTHON, "-B", *(str(part) for part in argv)],
        input=stdin,
        capture_output=True,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        timeout=timeout,
        check=False,
    )

def inspection_path(path: Path) -> Path:
    """Use the host file API independently of ET when inspecting test artifacts."""
    if os.name != "nt":
        return path
    text = os.path.abspath(path)
    if text.startswith(("\\\\?\\", "\\\\.\\")):
        return Path(text)
    if text.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + text[2:])
    return Path("\\\\?\\" + text)


def second_process_readback(env: dict, out_path: Path) -> dict:
    """Read one sidecar pair back from a second, independent Python process."""
    out_path = inspection_path(out_path)
    result = run_entry(
        env,
        ["-c", READBACK_SCRIPT, str(out_path), str(out_path) + ".details.jsonl"],
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    return json.loads(result.stdout)


def read_jsonl(path: Path) -> list[dict]:
    try:
        raw = inspection_path(path).read_bytes()
    except OSError:
        return []
    rows = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            rows.append({"unparseable": True})
    return rows


def rows_for_check(rows: list[dict], check_id: str) -> list[dict]:
    return [row for row in rows if row.get("check_id") == check_id]


def finished_details(env: dict, out_path: Path) -> dict:
    """The finished detail record of a single-step run, read by a second process."""
    payload = second_process_readback(env, out_path)
    finished = [row for row in payload["details"] if row.get("phase") == "finished"]
    assert len(finished) == 1, payload["details"]
    return finished[0]


def e2e_case_dir(tmp_path: Path, name: str) -> Path:
    folder = tmp_path / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def audit_text(header: dict | None = None, body: str = "Stored body.") -> str:
    if header is None:
        header = {
            "Company": "Acme (亚克力)",
            "Ticker": "ACME",
            "Quarter": "Q3 2026",
            "Source": "motley_fool",
            "URL": "N/A",
            "Characters": "12",
        }
    lines = "".join(f"{key}: {value}\n" for key, value in header.items())
    return f"{AUDIT_SEPARATOR}\nEarnings Call Transcript\n{lines}{AUDIT_SEPARATOR}\n\n{body}"


def discover_request(request_id: str) -> dict:
    """A legal request whose discover+source-payload combination is refused
    before any provider call: the zero-request business contrast."""
    return make_request(
        provider="fmp",
        exchange="nasdaq",
        max_body_bytes=1000,
        ticker="MSFT",
        fiscal_quarter=3,
        request_id=request_id,
    )


DISCOVER_ARGV = [
    ROOT / "transcript_tool.py",
    "--request-stdin",
    "--operation",
    "discover",
    "--include-source-payload",
    "--report-usage",
]


# ── A 节：普通入口拒绝与坏 TXT 逐文件可观察 ──────────────────────


def test_ordinary_entry_refusals_are_observable(tmp_path):
    """第 10 行原发生：四个普通拒绝此前零观察，修复后逐一可查。

    ``bad-wire`` 是既有正控：JSON 提取路径早已发布观察，保持不变。
    """
    env = make_env(tmp_path)
    cases = [
        (
            "bad-cli",
            [ROOT / "transcript_tool.py", "--operation", "bad"],
            b"",
            2,
        ),
        (
            "bad-wire",
            [ROOT / "transcript_tool.py", "--request-stdin", "--report-usage"],
            b"{",
            0,
        ),
        (
            "bad-periods",
            [ROOT / "scraper.py", "--periods", "not-a-period"],
            b"",
            2,
        ),
        (
            "txt-root-missing",
            [ROOT / "transcript_audit.py", "--root", "{case}/absent"],
            b"",
            1,
        ),
    ]
    for name, argv, stdin, expected_exit in cases:
        case = e2e_case_dir(tmp_path, name)
        out_path = case / "observations.jsonl"
        argv = [str(part).replace("{case}", str(case)) for part in argv]
        case_env = dict(env)
        case_env["RESEARCH_FEEDBACK_OUT"] = str(out_path)
        case_env["RESEARCH_FEEDBACK_RUN_ID"] = f"f4-{name}"
        case_env["RESEARCH_FEEDBACK_ATTEMPT_ID"] = "one"

        completed = run_entry(case_env, argv, stdin=stdin, cwd=case)

        assert completed.returncode == expected_exit, (
            name,
            completed.returncode,
            completed.stderr.decode("utf-8", "replace"),
        )
        payload = second_process_readback(case_env, out_path)
        refusals = rows_for_check(payload["observations"], "invocation-refusal")
        if name == "bad-wire":
            # 既有正控：JSON 提取拒绝走 invalid_request，且不新增拒绝行。
            assert refusals == []
            naming = rows_for_check(payload["observations"], "outcome-naming")
            assert naming and naming[0]["actual"] == "invalid_request"
            continue
        # RED→GREEN：普通拒绝在第二个进程按本次 ID 可查，一次、带原因与位置。
        assert len(refusals) == 1, (name, payload["observations"])
        refusal = refusals[0]
        assert refusal["disposition"] == "expected"
        assert refusal["run_id"] == f"f4-{name}"
        assert refusal["attempt_id"] == "one"
        details = [row for row in payload["details"] if row.get("refusal_code")]
        assert len(details) == 1, (name, payload["details"])
        record = details[0]
        assert record["entry"]
        assert record["stage"]
        assert record["location"]
        # 未脱敏 argv、密钥与临时路径一律不进入明细。
        blob = json.dumps(payload, ensure_ascii=False)
        assert OFFLINE_KEY not in blob
        assert str(tmp_path) not in blob
        assert "--operation" not in blob

    # 拒绝码与发生位置逐案核对。
    bad_cli = second_process_readback(env, tmp_path / "bad-cli" / "observations.jsonl")
    record = [row for row in bad_cli["details"] if row.get("refusal_code")][0]
    assert record["refusal_code"] == "invalid_cli_arguments"
    assert record["stage"] == "argument-parsing"
    assert record["entry"] == "transcript_tool"
    periods = second_process_readback(
        env, tmp_path / "bad-periods" / "observations.jsonl"
    )
    record = [row for row in periods["details"] if row.get("refusal_code")][0]
    assert record["refusal_code"] == "invalid_argument_value"
    assert record["stage"] == "argument-validation"
    assert record["entry"] == "scraper"
    assert record["argument"] == "--periods"
    txt_root = second_process_readback(
        env, tmp_path / "txt-root-missing" / "observations.jsonl"
    )
    record = [row for row in txt_root["details"] if row.get("refusal_code")][0]
    assert record["refusal_code"] == "input_root_missing"
    assert record["stage"] == "input-resolution"
    assert record["entry"] == "transcript_audit"


def test_corrupt_txt_and_same_batch_success_are_per_file_observable(tmp_path):
    """损坏 TXT 在原生报告里统计为 corrupt；逐文件失败按本次 ID 在第二个进程可查，
    期望原因是解码损坏；同批成功文件同样可查；反馈不存电话会正文。"""
    case = e2e_case_dir(tmp_path, "corrupt-txt")
    txt_root = case / "txt"
    txt_root.mkdir()
    # 损坏原件：字节 ff 00（解码损坏）。
    (txt_root / "ACME_Q3_2026_earnings_call.txt").write_bytes(b"\xff\x00")
    # 同批可证明原件（legacy_unverified，非失败结局）。
    good_dir = txt_root / "GOOD"
    good_dir.mkdir()
    (good_dir / "GOOD_Q3_2026_earnings_call.txt").write_text(
        audit_text(header={"Ticker": "GOOD", "Quarter": "Q3 2026"}), encoding="utf-8"
    )
    report_dir = case / "report"
    report_dir.mkdir()

    out_path = case / "observations.jsonl"
    env = make_env(tmp_path)
    env["RESEARCH_FEEDBACK_OUT"] = str(out_path)
    env["RESEARCH_FEEDBACK_RUN_ID"] = "f4-corrupt-txt"
    env["RESEARCH_FEEDBACK_ATTEMPT_ID"] = "one"

    completed = run_entry(
        env,
        [
            ROOT / "transcript_audit.py",
            "--root",
            txt_root,
            "--report-dir",
            report_dir,
        ],
        cwd=case,
    )

    # 原生行为保留：具名失败退出码 1，报告逐文件统计 corrupt。
    assert completed.returncode == 1
    report = json.loads((report_dir / AUDIT_REPORT_NAME).read_text(encoding="utf-8"))
    assert report["counts"].get("corrupt") == 1
    by_name = {record["file"]: record for record in report["files"]}
    corrupt = by_name["ACME_Q3_2026_earnings_call.txt"]
    assert corrupt["outcome"] == "corrupt"

    # 逐文件观察：损坏与成功同批文件都在第二个进程按本次 ID 可查。step 即
    # 原报告中的相对文件引用（逐字绑定，Windows 下为反斜杠形式）。
    payload = second_process_readback(env, out_path)
    per_file = rows_for_check(payload["observations"], "stored-original-audit")
    report_files = {record["file"] for record in report["files"]}
    steps = {row["step_id"] for row in per_file}
    assert steps == report_files
    assert len(report_files) == 2
    assert all(row["disposition"] == "expected" for row in per_file)
    assert all(row["run_id"] == "f4-corrupt-txt" for row in per_file)

    corrupt_ref = next(ref for ref in report_files if "ACME" in ref)
    good_ref = next(ref for ref in report_files if "GOOD" in ref)
    observation_by_step = {row["step_id"]: row for row in per_file}
    detail_by_id = {
        row["details_id"]: row
        for row in payload["details"]
        if row.get("operation") == "audit"
    }
    corrupt_detail = detail_by_id[observation_by_step[corrupt_ref]["details_ref"]]
    # 期望原因：解码损坏（invalid_encoding），以相对文件引用定位，无正文。
    assert corrupt_detail["outcome"] == "corrupt"
    assert corrupt_detail["error_code"] == "invalid_encoding"
    assert corrupt_detail["file"] == corrupt_ref
    good_detail = detail_by_id[observation_by_step[good_ref]["details_ref"]]
    assert good_detail["outcome"] == "legacy_unverified"

    blob = json.dumps(payload, ensure_ascii=False)
    assert "Stored body." not in blob and "\xff" not in blob


def test_empty_root_audit_stays_a_legal_empty_result(tmp_path):
    """合法空结果对照：空根审计零文件、零逐文件行、退出码 0。"""
    case = e2e_case_dir(tmp_path, "empty-txt")
    txt_root = case / "txt"
    txt_root.mkdir()
    out_path = case / "observations.jsonl"
    env = make_env(tmp_path)
    env["RESEARCH_FEEDBACK_OUT"] = str(out_path)
    env["RESEARCH_FEEDBACK_RUN_ID"] = "f4-empty-txt"
    env["RESEARCH_FEEDBACK_ATTEMPT_ID"] = "one"

    completed = run_entry(
        env, [ROOT / "transcript_audit.py", "--root", txt_root], cwd=case
    )
    assert completed.returncode == 0
    payload = second_process_readback(env, out_path)
    assert rows_for_check(payload["observations"], "stored-original-audit") == []
    assert rows_for_check(payload["observations"], "invocation-refusal") == []


# ── A 节：worker 失败的退出状态与阶段 ────────────────────────────


def run_budget_e2e(env: dict, case: Path, name: str, case_name: str) -> dict:
    out_path = case / "observations.jsonl"
    case_env = dict(env)
    case_env["RESEARCH_FEEDBACK_OUT"] = str(out_path)
    case_env["RESEARCH_FEEDBACK_RUN_ID"] = f"f4-{case_name}"
    case_env["RESEARCH_FEEDBACK_ATTEMPT_ID"] = "one"
    runtime = case / "rt"
    runtime.mkdir(exist_ok=True)
    completed = run_entry(
        case_env,
        [
            ROOT / "tools" / "run_request_budget_e2e.py",
            "--case",
            name,
            "--temp-root",
            case,
        ],
        cwd=case,
        timeout=90,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    summary = json.loads(completed.stdout)
    payload = second_process_readback(case_env, out_path)
    return {"summary": summary, "payload": payload, "out_path": out_path, "env": case_env}


def test_worker_loss_exit_state_is_preserved(tmp_path):
    """第 11 行原发生：worker 离线退出此前只留 unknown 用量；修复后退出码、
    阶段与脱敏异常内因在明细中可查，业务 JSON 与收据不变。"""
    result = run_budget_e2e(make_env(tmp_path), e2e_case_dir(tmp_path, "worker-loss"), "worker-loss", "worker-loss")
    summary, payload = result["summary"], result["payload"]
    assert summary["status"] == "provider_error"
    assert summary["error_code"] == "retrieval_worker_failure"
    receipt = summary["receipt"]
    assert receipt["usage"] is None and receipt["usage_complete"] is False

    naming = rows_for_check(payload["observations"], "outcome-naming")
    assert naming and naming[0]["disposition"] == "expected"
    usage = rows_for_check(payload["observations"], "usage-accounting")
    assert usage and usage[0]["disposition"] == "unknown"

    detail = finished_details(result["env"], result["out_path"])
    worker = detail["worker"]
    assert worker["worker_exit_code"] == 7
    assert "SystemExit" in worker["worker_exceptions"]
    assert isinstance(worker["worker_pid"], int)
    # 失败阶段可独立恢复：launcher 工厂内注入的 SystemExit 记为 launcher。
    assert worker["worker_phase"] == "launcher"
    assert worker["supervisor_stage"] == "worker_exit"
    # 业务 JSON 与收据不携带任何诊断字段。
    assert "worker" not in json.dumps(summary["receipt"])
    assert "worker_pid" not in json.dumps(summary)


def test_two_worker_inner_causes_stay_distinguishable(tmp_path):
    """两种同外层异常、不同内因的 worker 失败冷读可分别复现：
    非零子进程退出（SystemExit:7）对照超时硬截止。"""
    loss = run_budget_e2e(
        make_env(tmp_path), e2e_case_dir(tmp_path, "wf-exit"), "worker-loss", "wf-exit"
    )
    timeout = run_budget_e2e(
        make_env(tmp_path), e2e_case_dir(tmp_path, "wf-deadline"), "deadline", "wf-deadline"
    )
    loss_detail = finished_details(loss["env"], loss["out_path"])["worker"]
    timeout_detail = finished_details(timeout["env"], timeout["out_path"])["worker"]
    assert loss_detail["worker_exit_code"] == 7
    assert loss_detail["supervisor_stage"] == "worker_exit"
    assert timeout_detail["supervisor_stage"] == "deadline"
    assert timeout_detail["worker_exit_code"] != 7
    assert timeout["summary"]["status"] == "deadline_exceeded"


def test_worker_result_load_failure_keeps_child_exit_and_parent_stage(tmp_path, monkeypatch):
    """同一外层 provider_error 下的另一内因：子进程正常退出但结果文件损坏；
    明细同时保留子进程退出码与父进程加载阶段词。"""
    import io
    from unittest.mock import patch

    import transcript_tool

    case = e2e_case_dir(tmp_path, "wf-garbage")
    out_path = case / "observations.jsonl"
    monkeypatch.setenv("RESEARCH_FEEDBACK_OUT", str(out_path))
    monkeypatch.setenv("RESEARCH_FEEDBACK_RUN_ID", "f4-wf-garbage")
    monkeypatch.setenv("RESEARCH_FEEDBACK_ATTEMPT_ID", "one")
    monkeypatch.setenv("RESEARCH_FEEDBACK_STATE_ROOT", str(tmp_path / "state-wf-garbage"))
    monkeypatch.setenv("FMP_API_KEY", OFFLINE_KEY)

    from tests.test_feedback_observation import read_jsonl

    request = make_request(
        provider="fmp",
        exchange="nasdaq",
        max_body_bytes=1000,
        ticker="NVDA",
        fiscal_quarter=2,
        max_response_bytes=1024,
    )
    runtime = case / "rt"
    runtime.mkdir()
    out, err = io.StringIO(), io.StringIO()
    with (
        patch.object(sys, "stdin", io.StringIO(json.dumps(request))),
        patch.object(sys, "stdout", out),
        patch.object(sys, "stderr", err),
    ):
        rc = transcript_tool.main(
            ["--request-stdin", "--include-source-payload", "--report-usage"],
            _retrieval_launcher=LAUNCHER,
            _retrieval_spec=fake_spec(corrupt_result_at_exit="garbage"),
            _retrieval_temp_root=runtime,
            _feedback_evidence_ref="et-case:f4-garbage",
        )
    assert rc == 0
    result = json.loads(out.getvalue())
    assert result["status"] == "provider_error"
    assert result["error_code"] == "retrieval_worker_failure"

    details = read_jsonl(out_path.parent / (out_path.name + ".details.jsonl"))
    finished = [row for row in details if row.get("phase") == "finished"]
    assert len(finished) == 1
    worker = finished[0]["worker"]
    assert worker["worker_exit_code"] == 0
    assert worker["load_failure"] == "result_unparseable"
    assert worker["supervisor_stage"] == "result_load"
    # 同外层不同内因：exit 7 案是 worker_exit，本案是 result_load。
    assert worker["worker_exceptions"] in (None, []) or "SystemExit" not in worker[
        "worker_exceptions"
    ]


# ── A 节：既有正控（402 与漏发布用量）保持有效 ──────────────────


def test_entitlement_402_stays_a_source_availability_fact(tmp_path):
    """402 是该来源权限/可用性事实：业务结果 unavailable，费用不推断为零。"""
    case = e2e_case_dir(tmp_path, "account-refusal")
    out_path = case / "case.jsonl"
    env = make_env(tmp_path)
    env.pop("RESEARCH_FEEDBACK_STATE_ROOT", None)
    env["RESEARCH_FEEDBACK_STATE_ROOT"] = str(tmp_path / "state-402")

    completed = run_entry(
        env,
        [
            ROOT / "tools" / "replay_feedback_case.py",
            "--case",
            "fmp-entitlement-402",
            "--out",
            out_path,
        ],
        cwd=case,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    report = json.loads(completed.stdout)
    assert report["matches_expected"]
    assert report["native"]["status"] == "unavailable"
    assert report["native"]["error_code"] == "provider_entitlement_required"
    # 402 案的原请求没有取得资料：来源不可用，费用未知不推断。
    usage = rows_for_check(report["observations"], "usage-accounting")
    assert usage and usage[0]["expected"] == "usage_unknown"
    payload = second_process_readback(env, out_path)
    assert rows_for_check(payload["observations"], "outcome-naming")


def test_usage_receipt_suppression_positive_control(tmp_path):
    """漏用量发布的正控继续报偏差（transcript-usage-receipt），不因本次改动失效。"""
    case = e2e_case_dir(tmp_path, "lost-receipt")
    out_path = case / "case.jsonl"
    env = make_env(tmp_path)
    env["RESEARCH_FEEDBACK_STATE_ROOT"] = str(tmp_path / "state-suppressed")

    completed = run_entry(
        env,
        [
            ROOT / "tools" / "replay_feedback_case.py",
            "--case",
            "usage-receipt-suppressed",
            "--out",
            out_path,
        ],
        cwd=case,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    report = json.loads(completed.stdout)
    assert report["deviation_observed"] is True
    assert report["issue_keys"] == ["transcript-usage-receipt"]


# ── B 节：落盘与确认之间断开后的补投 ─────────────────────────────


def test_pending_delivery_survives_unwritable_sink_and_is_redelivered_once(tmp_path):
    """外部反馈文件不可写时先保存再确认：业务原返回不变，第二、三个进程按同一
    owner/run/attempt 只读对账并补投，同一发生只出现一次，新增请求为零。"""
    env = make_env(tmp_path)
    state_root = Path(env["RESEARCH_FEEDBACK_STATE_ROOT"])
    run_id, attempt_id = "f4-redeliver", "one"
    # 不可写的外部反馈文件：父位置是一个已存在的普通文件，所有写入必然失败。
    blocking = tmp_path / "unwritable-sink"
    blocking.write_bytes(b"not a directory")
    broken_out = blocking / "observations.jsonl"

    env["RESEARCH_FEEDBACK_OUT"] = str(broken_out)
    env["RESEARCH_FEEDBACK_RUN_ID"] = run_id
    env["RESEARCH_FEEDBACK_ATTEMPT_ID"] = attempt_id
    case = e2e_case_dir(tmp_path, "redeliver")
    request = discover_request("f4-redeliver-001")

    first = run_entry(
        env, DISCOVER_ARGV, stdin=json.dumps(request).encode("utf-8"), cwd=case
    )
    # 导出失败不覆盖业务原返回。
    assert first.returncode == 0
    business = json.loads(first.stdout.decode("utf-8"))
    assert business["status"] == "invalid_request"
    assert json.loads(first.stderr.decode("utf-8"))["usage"]["requests_used"] == 0

    # 待投递记录已在 owner 自有持久状态根：先保存。
    pending = state_root / "pending" / f"{run_id}--{attempt_id}.jsonl"
    pending_details = Path(str(pending) + ".details.jsonl")
    saved = read_jsonl(pending)
    saved_details = read_jsonl(pending_details)
    assert rows_for_check(saved, "action-started")
    assert rows_for_check(saved, "outcome-naming")

    # 另起两个进程补投同一待投递项（--capabilities 不发起任何请求）。
    env2 = dict(env)
    env2["RESEARCH_FEEDBACK_OUT"] = str(tmp_path / "restored" / "observations.jsonl")
    for index in (2, 3):
        completed = run_entry(env2, [ROOT / "transcript_tool.py", "--capabilities"], cwd=case)
        assert completed.returncode == 0
        assert "earnings-provider-capabilities/1" in completed.stdout.decode("utf-8")

    delivered = read_jsonl(tmp_path / "restored" / "observations.jsonl")
    identities = [
        (row["run_id"], row["attempt_id"], row["step_id"], row["check_id"])
        for row in delivered
        if "check_id" in row
    ]
    assert len(identities) == len(set(identities))
    assert rows_for_check(delivered, "action-started")
    assert rows_for_check(delivered, "outcome-naming")
    # 补投后的明细与原始保存一致（同一发生只出现一次），且只投递有观察引用
    # 的明细：同身份重跑不留下孤儿明细行。
    delivered_details = read_jsonl(
        tmp_path / "restored" / "observations.jsonl.details.jsonl"
    )
    assert len(delivered_details) == len({row["details_id"] for row in delivered_details})
    original_ids = {row["details_id"] for row in saved_details}
    assert original_ids <= {row["details_id"] for row in delivered_details}
    referenced = {
        row["details_ref"] for row in delivered if row.get("details_ref")
    }
    assert {row["details_id"] for row in delivered_details} == referenced


def test_concurrent_same_identity_producers_register_once(tmp_path):
    """并发恢复窗口：同身份并发生产者（3 个真实子进程）登记原发生恰好一次，
    显式 OUT 与直启 bundle 两种模式都不出现重复行。"""
    env = make_env(tmp_path)
    case = e2e_case_dir(tmp_path, "concurrent")
    stdin = json.dumps(discover_request("f4-concurrent-001")).encode("utf-8")

    for mode in ("direct", "out"):
        run_env = dict(env)
        run_env.pop("RESEARCH_FEEDBACK_OUT", None)
        if mode == "direct":
            run_env["RESEARCH_FEEDBACK_DIR"] = str(tmp_path / f"inbox-{mode}")
        else:
            out_dir = tmp_path / f"sink-{mode}"
            out_dir.mkdir()
            run_env["RESEARCH_FEEDBACK_OUT"] = str(out_dir / "observations.jsonl")
        run_env["RESEARCH_FEEDBACK_RUN_ID"] = f"f4-concurrent-{mode}"
        run_env["RESEARCH_FEEDBACK_ATTEMPT_ID"] = "one"

        procs = [
            subprocess.Popen(
                [
                    PYTHON,
                    "-B",
                    str(ROOT / "transcript_tool.py"),
                    "--request-stdin",
                    "--operation",
                    "discover",
                    "--include-source-payload",
                    "--report-usage",
                ],
                cwd=str(case),
                env=run_env,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            for _ in range(3)
        ]
        for proc in procs:
            proc.stdin.write(stdin)
            proc.stdin.close()
        assert all(proc.wait(timeout=90) == 0 for proc in procs)

        if mode == "direct":
            bundle_dir = tmp_path / f"inbox-{mode}"
            bundles = [
                path
                for path in bundle_dir.glob("feedback-*.jsonl")
                if not path.name.endswith(".details.jsonl")
            ]
            assert len(bundles) == 1
            rows = read_jsonl(bundles[0])
        else:
            rows = read_jsonl(Path(run_env["RESEARCH_FEEDBACK_OUT"]))
        identities = [
            (row["run_id"], row["attempt_id"], row["step_id"], row["check_id"])
            for row in rows
            if "check_id" in row
        ]
        assert len(rows) == 5, (mode, rows)
        assert len(identities) == len(set(identities)), (mode, identities)


def test_unsafe_request_id_is_published_as_a_digest_word(tmp_path):
    """调用方可控的标识不得把 URL 查询或伪造 stderr 文本带进公开产物：
    不安全取值发布为稳定摘要词，业务身份仍可跨进程复现。"""
    env = make_env(tmp_path)
    case = e2e_case_dir(tmp_path, "unsafe-id")
    hostile = discover_request(
        "https://evil.example/x?token=abc123&api_key=SK-LIVE-KEY"
    )
    hostile["ticker"] = "Traceback (most recent call last):"
    out_path = case / "observations.jsonl"
    env["RESEARCH_FEEDBACK_OUT"] = str(out_path)
    env["RESEARCH_FEEDBACK_RUN_ID"] = "f4-unsafe-id"
    env["RESEARCH_FEEDBACK_ATTEMPT_ID"] = "one"

    completed = run_entry(
        env, DISCOVER_ARGV, stdin=json.dumps(hostile).encode("utf-8"), cwd=case
    )
    assert completed.returncode == 0

    payload = second_process_readback(env, out_path)
    blob = json.dumps(payload, ensure_ascii=False)
    assert "evil.example" not in blob
    assert "SK-LIVE-KEY" not in blob
    assert "token=abc123" not in blob
    assert "Traceback" not in blob
    # step_id 与 evidence_ref 都是稳定摘要词：同值再次运行得到同一身份。
    step_ids = {row["step_id"] for row in payload["observations"]}
    assert step_ids
    assert all(
        word.startswith("request-") and len(word) == len("request-") + 16
        for word in step_ids
    )
    assert all(
        row.get("evidence_ref") in step_ids for row in payload["observations"]
    )


def test_host_supplied_evidence_ref_is_published_safely(tmp_path, monkeypatch):
    """宿主传入的公开证据同样受消毒规则约束：URL 查询不逐字进入公开产物，
    本仓自己的不透明证据引用（``et-case:<name>``）保持原样。"""
    import io
    from unittest.mock import patch

    import transcript_tool

    case = e2e_case_dir(tmp_path, "host-evidence")
    out_path = case / "observations.jsonl"
    request = discover_request("f4-host-evidence-001")
    monkeypatch.setenv("RESEARCH_FEEDBACK_OUT", str(out_path))
    monkeypatch.setenv("RESEARCH_FEEDBACK_RUN_ID", "f4-host-evidence")
    monkeypatch.setenv("RESEARCH_FEEDBACK_ATTEMPT_ID", "one")
    monkeypatch.setenv(
        "RESEARCH_FEEDBACK_STATE_ROOT", str(tmp_path / "state-host-evidence")
    )

    def run(reference):
        out, err = io.StringIO(), io.StringIO()
        with (
            patch.object(sys, "stdin", io.StringIO(json.dumps(request))),
            patch.object(sys, "stdout", out),
            patch.object(sys, "stderr", err),
        ):
            rc = transcript_tool.main(
                DISCOVER_ARGV[1:],
                _feedback_evidence_ref=reference,
            )
        assert rc == 0
        rows = read_jsonl(out_path.parent / (out_path.name))
        return rows

    hostile = run("https://evil.example/q?token=abc123")
    blob = json.dumps(hostile, ensure_ascii=False)
    assert "evil.example" not in blob
    assert "token=abc123" not in blob
    assert hostile
    assert all(
        str(row.get("evidence_ref", "")).startswith("evidence-")
        for row in hostile
        if row.get("evidence_ref")
    )

    # 本仓不透明证据引用原样发布（既有冻结样例依赖这个形状）。
    out_path.write_bytes(b"")
    legitimate = run("et-case:f4-host-evidence")
    assert {
        row["evidence_ref"] for row in legitimate if row.get("evidence_ref")
    } == {"et-case:f4-host-evidence"}


def test_started_only_cut_point_is_recovered_without_new_requests(tmp_path):
    """仅开始截止点：进程在开始记录落盘后被强停，第二个进程对账不新增请求，
    开始发生只登记一次。"""
    env = make_env(tmp_path)
    case = e2e_case_dir(tmp_path, "kill-started")
    out_path = case / "observations.jsonl"
    env["RESEARCH_FEEDBACK_OUT"] = str(out_path)
    env["RESEARCH_FEEDBACK_RUN_ID"] = "f4-kill-started"
    env["RESEARCH_FEEDBACK_ATTEMPT_ID"] = "one"
    runtime = case / "rt"
    runtime.mkdir()

    process = subprocess.Popen(
        [
            PYTHON,
            "-B",
            str(ROOT / "tools" / "run_request_budget_e2e.py"),
            "--case",
            "deadline",
            "--temp-root",
            str(case),
        ],
        cwd=str(case),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 15
    started_seen = False
    while time.monotonic() < deadline:
        rows = read_jsonl(out_path)
        if rows_for_check(rows, "action-started"):
            started_seen = True
            break
        if process.poll() is not None:
            break
        time.sleep(0.05)
    assert started_seen, "the started row must land before the hard stop"
    process.kill()
    process.wait(timeout=15)

    rows = read_jsonl(out_path)
    assert len(rows_for_check(rows, "action-started")) == 1
    assert not rows_for_check(rows, "outcome-naming")

    # 第二个进程按同一身份只读对账：不重跑业务、不新增请求、不重复登记。
    completed = run_entry(env, [ROOT / "transcript_tool.py", "--capabilities"], cwd=case)
    assert completed.returncode == 0
    rows_after = read_jsonl(out_path)
    assert len(rows_for_check(rows_after, "action-started")) == 1
    assert not rows_for_check(rows_after, "outcome-naming")
    identities = [
        (row["run_id"], row["attempt_id"], row["step_id"], row["check_id"])
        for row in rows_after
        if "check_id" in row
    ]
    assert len(identities) == len(set(identities))


# ── 直启发布接口：显式 OUT、仅 DIR、默认状态根三组隔离运行 ────────


def test_direct_start_publication_covers_three_configurations(tmp_path):
    env = make_env(tmp_path)
    case = e2e_case_dir(tmp_path, "publish")
    request = discover_request("f4-publish-001")
    stdin = json.dumps(request).encode("utf-8")

    # 组一：显式 OUT —— 单次文件及既有明细格式，无 bundle。
    out_root = tmp_path / "cfg-out"
    out_root.mkdir()
    env_out = dict(env)
    env_out["RESEARCH_FEEDBACK_DIR"] = str(out_root / "inbox")
    env_out["RESEARCH_FEEDBACK_OUT"] = str(out_root / "observations.jsonl")
    env_out["RESEARCH_FEEDBACK_RUN_ID"] = "f4-pub-out"
    env_out["RESEARCH_FEEDBACK_ATTEMPT_ID"] = "one"
    completed = run_entry(env_out, DISCOVER_ARGV, stdin=stdin, cwd=case)
    assert completed.returncode == 0
    assert (inspection_path(out_root) / "observations.jsonl").is_file()
    assert (inspection_path(out_root) / "observations.jsonl.details.jsonl").is_file()
    assert not (inspection_path(out_root) / "inbox").exists()

    # 组二：仅 DIR —— 公开收件目录收到 bundle + ready。
    dir_root = tmp_path / "cfg-dir"
    dir_root.mkdir()
    env_dir = dict(env)
    env_dir["RESEARCH_FEEDBACK_DIR"] = str(dir_root / "inbox")
    env_dir["RESEARCH_FEEDBACK_RUN_ID"] = "f4-pub-dir"
    env_dir["RESEARCH_FEEDBACK_ATTEMPT_ID"] = "one"
    completed = run_entry(env_dir, DISCOVER_ARGV, stdin=stdin, cwd=case)
    assert completed.returncode == 0
    bundles = sorted(
        path
        for path in inspection_path(dir_root / "inbox").glob("feedback-*.jsonl")
        if not path.name.endswith(".details.jsonl")
    )
    assert len(bundles) == 1
    bundle = bundles[0]
    stem = bundle.name[len("feedback-") : -len(".jsonl")]
    assert len(stem) == 32 and all(c in "0123456789abcdef" for c in stem)
    assert Path(str(bundle) + ".details.jsonl").is_file()
    ready = bundle.parent / (bundle.name[: -len(".jsonl")] + ".ready")
    assert ready.is_file()
    mark = json.loads(ready.read_text(encoding="utf-8"))
    assert mark["owner"] == "earnings-transcripts"
    assert mark["run_id"] == "f4-pub-dir"
    assert mark["attempt_id"] == "one"
    assert mark["step_ids"]
    # 公开 bundle 不携带路径或命令。
    blob = bundle.read_text(encoding="utf-8")
    details_blob = Path(str(bundle) + ".details.jsonl").read_text(encoding="utf-8")
    for text in (blob, details_blob, ready.read_text(encoding="utf-8")):
        assert str(tmp_path) not in text
        assert "transcript_tool.py" not in text
        assert OFFLINE_KEY not in text

    # 组三：OUT 与 DIR 均未设置 —— owner 默认状态根下 observations 收件。
    default_root = Path(env["RESEARCH_FEEDBACK_STATE_ROOT"])
    env_default = dict(env)
    env_default["RESEARCH_FEEDBACK_RUN_ID"] = "f4-pub-default"
    env_default["RESEARCH_FEEDBACK_ATTEMPT_ID"] = "one"
    completed = run_entry(env_default, DISCOVER_ARGV, stdin=stdin, cwd=case)
    assert completed.returncode == 0
    default_inbox = inspection_path(default_root / "observations")
    default_bundles = sorted(
        path
        for path in default_inbox.glob("feedback-*.jsonl")
        if not path.name.endswith(".details.jsonl")
    )
    assert len(default_bundles) == 1
    assert (
        default_inbox / (default_bundles[0].name[: -len(".jsonl")] + ".ready")
    ).is_file()


def test_bundle_redelivery_never_duplicates_and_respects_ack(tmp_path):
    """数据已落而 ready 未发、已发但回告丢失、并发消费三个窗口：
    同一交付身份补投不产生第二份 bundle；ROOT 确认标记后不再补投。"""
    env = make_env(tmp_path)
    case = e2e_case_dir(tmp_path, "redelivery")
    request = discover_request("f4-bundle-001")
    stdin = json.dumps(request).encode("utf-8")
    inbox = tmp_path / "inbox"
    env["RESEARCH_FEEDBACK_DIR"] = str(inbox)
    env["RESEARCH_FEEDBACK_RUN_ID"] = "f4-bundle"
    env["RESEARCH_FEEDBACK_ATTEMPT_ID"] = "one"

    completed = run_entry(env, DISCOVER_ARGV, stdin=stdin, cwd=case)
    assert completed.returncode == 0
    bundle = sorted(
        path
        for path in inbox.glob("feedback-*.jsonl")
        if not path.name.endswith(".details.jsonl")
    )[0]
    original = bundle.read_bytes()

    # 回告丢失窗口：重跑同身份入口（--capabilities），同名 bundle 内容不变。
    completed = run_entry(env, [ROOT / "transcript_tool.py", "--capabilities"], cwd=case)
    assert completed.returncode == 0
    assert sorted(
        path
        for path in inbox.glob("feedback-*.jsonl")
        if not path.name.endswith(".details.jsonl")
    ) == [bundle]
    assert bundle.read_bytes() == original

    # 并发消费窗口：两个消费者读同一 bundle，发生身份集合一致且唯一。
    first = second_process_readback(env, bundle)
    second = second_process_readback(env, bundle)
    keys = lambda payload: sorted(  # noqa: E731
        (row["run_id"], row["attempt_id"], row["step_id"], row["check_id"])
        for row in payload["observations"]
        if "check_id" in row
    )
    assert keys(first) == keys(second)
    assert len(keys(first)) == len(set(keys(first)))

    # ROOT 确认标记（.ack）后：owner 不再补投。
    ack = bundle.parent / (bundle.name[: -len(".jsonl")] + ".ack")
    ack.write_text("confirmed", encoding="utf-8")
    completed = run_entry(env, [ROOT / "transcript_tool.py", "--capabilities"], cwd=case)
    assert completed.returncode == 0
    assert bundle.read_bytes() == original


def test_generated_identity_is_recoverable_across_processes(tmp_path):
    """缺省运行号时生成的 ID 可跨进程查回：pending 目录按身份命名并可在第二个
    进程枚举、冷读；两个并发任务各自隔离。"""
    env = make_env(tmp_path)
    case = e2e_case_dir(tmp_path, "identity")
    request = discover_request("f4-identity-001")
    state_root = Path(env["RESEARCH_FEEDBACK_STATE_ROOT"])

    first = run_entry(env, DISCOVER_ARGV, stdin=json.dumps(request).encode("utf-8"), cwd=case)
    assert first.returncode == 0
    pending_files = sorted(
        path
        for path in (state_root / "pending").glob("*.jsonl")
        if not path.name.endswith(".details.jsonl")
    )
    assert len(pending_files) == 1
    generated = pending_files[0].name[: -len(".jsonl")]
    run_id, _, attempt_id = generated.partition("--")
    assert run_id and attempt_id

    # 第二个进程只读查回：观察里带同一 run/attempt 身份。
    payload = second_process_readback(env, pending_files[0])
    assert payload["observations"]
    assert all(row["run_id"] == run_id for row in payload["observations"])

    # 另一个并发任务：全新身份，互不串写。
    second_env = make_env(tmp_path / "second")
    second_case = e2e_case_dir(tmp_path / "second", "identity")
    completed = run_entry(
        second_env,
        DISCOVER_ARGV,
        stdin=json.dumps(discover_request("f4-identity-002")).encode("utf-8"),
        cwd=second_case,
    )
    assert completed.returncode == 0
    other = sorted(
        path
        for path in (
            Path(second_env["RESEARCH_FEEDBACK_STATE_ROOT"]) / "pending"
        ).glob("*.jsonl")
        if not path.name.endswith(".details.jsonl")
    )
    assert len(other) == 1 and other[0].name != pending_files[0].name


# ── 清理核对 ────────────────────────────────────────────────────


def test_no_stray_temp_roots_outside_the_test_root(tmp_path):
    """收尾核对：e2e 正常退出的临时根已恢复原样，无残留。"""
    result = run_budget_e2e(
        make_env(tmp_path), e2e_case_dir(tmp_path, "cleanup"), "worker-loss", "cleanup"
    )
    assert result["summary"]["temporary_root_restored"] is True
    assert result["summary"]["worker_temp_empty"] is True
    assert result["summary"]["paid_calls"] == 0



@pytest.mark.parametrize("target", ("out", "dir", "default"))
def test_ordinary_refusal_in_deep_state_survives_cold_read_and_ack(tmp_path, target):
    env = make_env(tmp_path)
    root = tmp_path
    while len(str(root)) < 375:
        root /= "nested-feedback-owner-state"
    env["RESEARCH_FEEDBACK_STATE_ROOT"] = str(root / "state")
    env["RESEARCH_FEEDBACK_RUN_ID"] = "deep-state"
    env["RESEARCH_FEEDBACK_ATTEMPT_ID"] = "original"
    if target == "out":
        logical_output = root / "sidecar" / "observation.jsonl"
        env["RESEARCH_FEEDBACK_OUT"] = str(logical_output)
    else:
        inbox = root / "state" / "observations"
        if target == "dir":
            inbox = root / "configured-inbox"
            env["RESEARCH_FEEDBACK_DIR"] = str(inbox)
        inbox = inspection_path(inbox)

    def refuse():
        return run_entry(env, [ROOT / "transcript_tool.py", "--operation", "invalid"])

    failed = refuse()
    assert failed.returncode == 2
    assert b"invalid choice" in failed.stderr
    if target == "out":
        bundle = inspection_path(logical_output)
    else:
        markers = list(inbox.glob("feedback-*.ready"))
        assert len(markers) == 1
        marker = markers[0]
        mark = json.loads(marker.read_bytes())
        assert (mark["owner"], mark["run_id"], mark["attempt_id"]) == (
            "earnings-transcripts", "deep-state", "original",
        )
        bundle = marker.with_suffix(".jsonl")
    first = second_process_readback(env, bundle)
    rows = rows_for_check(first["observations"], "invocation-refusal")
    assert len(rows) == 1
    (cause,) = first["details"]
    assert rows[0]["details_ref"] == cause["details_id"]
    assert (cause["entry"], cause["refusal_code"]) == ("transcript_tool", "invalid_cli_arguments")
    assert len(list(bundle.parent.glob("*.diagnostics.jsonl"))) == 0
    assert second_process_readback(env, bundle) == first

    if target != "out":
        # Simulate the consumer acknowledging and taking its data away.
        # A new producer under the same identity must see the ACK even at depth.
        marker.with_suffix(".ack").write_bytes(b"confirmed")
        marker.unlink()
        bundle.unlink()
        bundle.with_name(bundle.name + ".details.jsonl").unlink()
        assert refuse().returncode == 2
        assert marker.with_suffix(".ack").is_file()
        assert not marker.exists() and not bundle.exists()


def test_deep_owner_port_reconciles_and_cold_reads_the_existing_ledger(tmp_path):
    from tests.test_feedback_owner import action, copy_case, occurrence, row_for

    env = make_env(tmp_path)
    root = tmp_path
    while len(str(root)) < 375:
        root /= "nested-owner-ledger"
    disk_root = inspection_path(root)
    disk_root.mkdir(parents=True)
    physical_sidecar = copy_case(disk_root, "deviation-usage-receipt")
    row = row_for(physical_sidecar, "usage-accounting")
    identifier = occurrence(row)
    args = [
        ROOT / "tools" / "feedback_owner.py",
        "--db",
        root / "ledger #% 证.sqlite",
        "--sidecar-dir",
        root,
    ]

    def invoke(argv, payload=None):
        body = json.dumps(payload).encode("utf8") if payload else b""
        proc = run_entry(env, [*args, *argv], stdin=body)
        assert proc.returncode == 0, (proc.stdout, proc.stderr)
        return json.loads(proc.stdout)

    request = action("triage", [identifier], {"observation": row, "occurrence_id": identifier})
    receipt = invoke(["triage"], request)
    assert receipt["result"]["decision"] == "repair"
    for _ in range(2):
        recovered = invoke(["get-action", request["action_id"]])
        assert recovered["receipt"]["result"] == receipt["result"]


def test_late_failure_is_published_from_complete_native_journals(tmp_path):
    env = make_env(tmp_path)
    env["RESEARCH_FEEDBACK_RUN_ID"] = "rolling"
    env["RESEARCH_FEEDBACK_ATTEMPT_ID"] = "one"
    first = run_entry(env, [ROOT / "transcript_tool.py", "--request-stdin"], stdin=b"{")
    assert first.returncode == 0

    state = inspection_path(Path(env["RESEARCH_FEEDBACK_STATE_ROOT"]))
    pending = state / "pending" / "rolling--one.jsonl"
    for journal in (pending, pending.with_name(pending.name + ".details.jsonl")):
        assert journal.is_file()
        # Legal trailing JSON whitespace models a large historical journal
        # without changing or manufacturing a single historical fact.
        with journal.open("ab") as stream:
            stream.write(b" " * (8 * 1024 * 1024) + b"\n")

    second = run_entry(env, [ROOT / "scraper.py", "--periods", "not-a-period"])
    assert second.returncode == 2
    markers = list((state / "observations").glob("feedback-*.ready"))
    assert len(markers) == 1
    bundle = markers[0].with_suffix(".jsonl")
    saved = second_process_readback(env, bundle)
    initial = rows_for_check(saved["observations"], "outcome-naming")
    assert len(initial) == 1 and initial[0]["actual"] == "invalid_request"
    late = rows_for_check(saved["observations"], "invocation-refusal")
    assert len(late) == 1
    by_ref = {detail["details_id"]: detail for detail in saved["details"]}
    assert by_ref[late[0]["details_ref"]]["entry"] == "scraper"


def test_two_refusal_sites_in_one_attempt_survive_replay_of_legacy_receipt(tmp_path):
    env = make_env(tmp_path)
    env["RESEARCH_FEEDBACK_RUN_ID"] = "shared"
    env["RESEARCH_FEEDBACK_ATTEMPT_ID"] = "one"
    output = tmp_path / "shared.jsonl"
    env["RESEARCH_FEEDBACK_OUT"] = str(output)
    transcript_cli = [ROOT / "transcript_tool.py", "--operation", "invalid"]
    scraper_cli = [ROOT / "scraper.py", "--periods", "not-a-period"]

    assert run_entry(env, transcript_cli).returncode == 2
    (receipt,) = read_jsonl(output)
    # This is exactly the legacy public key on disk before the upgrade.
    receipt["step_id"] = "cli-refusal"
    inspection_path(output).write_text(json.dumps(receipt) + "\n", encoding="utf-8")
    for argv in (transcript_cli, scraper_cli, scraper_cli):
        assert run_entry(env, argv).returncode == 2

    saved = second_process_readback(env, output)
    rows = rows_for_check(saved["observations"], "invocation-refusal")
    assert len(rows) == 2
    assert rows[0] == receipt
    by_ref = {detail["details_id"]: detail for detail in saved["details"]}
    assert {by_ref[row["details_ref"]]["refusal_code"] for row in rows} == {
        "invalid_cli_arguments", "invalid_argument_value",
    }
    assert rows[0]["step_id"] != rows[1]["step_id"]

# Findings — ET-DEADLINE（截至 Stage 1 摸底）

## 缺口复核（与 lane 描述一致）

- `transcript_api.py::_read_bounded` / `_read_fmp_payload` 的 `session.get(...)`（阻塞）与
  `iter_content` 都是**阻塞返回后**才查 `deadline`：同步 requests 下慢连接/慢流可以越过
  批次 deadline 才被拒绝。`scraper.py::_request_timeout = max(1, min(30, int(remaining)))`
  还把 <1s 的剩余额度抬到 1s，所以当前实现不能宣称硬总截止。
- 批次 deadline 只在 `BatchBudget.check_request/record_response` 里被协作式检查；
  `transcript_tool.py`（FF 正式入口）完全没有批次层，只有 API 内部 `timeout_seconds` 单请求预算。
- 显式 `--translate` 的翻译耗时不经过 `BatchBudget`（`_maybe_translate` 在预算检查之外），
  而 README 把 `--max-seconds` 写成“批次总时长”。本包只修正范围声明，不建翻译预算系统。

## 结构事实（决定实现形态）

- worker 需要 `BatchBudget`/`_BudgetSession`/`_BudgetResponse`，但 `scraper → common → translator/config`
  栈太重且 worker 永不翻译 → 抽 `retrieval_budget.py` 是“确有必要”；scraper 以
  `from retrieval_budget import ...` 保持 `scraper.BatchBudget` 等旧名（既有单测不改语义）。
- `transcript_tool.main(_session_factory=...)` 的注入路径 = 合作式检查（lane §3 明确允许保留），
  goldens（`generate_transcript_goldens.py`）与 `test_cli_single_request_intent_*` 走该路径 → **golden 字节不变**。
  硬保证只由生产路径（无注入）+ 新 e2e 测试承担。
- key 的受控通道：child 只经进程环境 `FMP_API_KEY`（`--api-key` 由 parent 写进 child env）；
  envelope/请求文件/结果文件/日志/交接文件一律无 key。`ProviderSettings` 仅两个 bool，按 bool 序列化。
- 结果文件上限推导：`content_utf8 ≤ MAX_BODY_BYTES`（API 已断言）与 title ≤ 单页上限，
  JSON `ensure_ascii=False` 最坏控制字符 `\uXXXX` = 6×；`/2` base64 ≤ `MAX_SOURCE_PAYLOAD_RESULT_BYTES`
  （与 content_utf8 互斥）→ 上限 = `6×(2×MAX_BODY_BYTES) + MAX_SOURCE_PAYLOAD_RESULT_BYTES + 2MiB` 余量。
  正常结果只有 KB 级，父进程先查 size 再限量读。
- Windows 注意：`terminate()` 即 TerminateProcess，`finally` 不会执行 → 被 kill 的 worker 不会有
  close 记录；“连接随 worker 退出”只能断言进程已确认退出 + 临时目录已清，旧
  `slow_body.closed is True` 断言必须改为新缝下的等价证据（列进 handoff）。

## 测试缝决定

- child 内安装 fake session：envelope 私有字段 `launcher = {"factory": "tests.<mod>:<func>", "spec": {...}}`，
  worker import 后调用得到 session_factory。无 CLI 参数、无环境变量后门。
- parent 观测 child 侧 HTTP：spec 里带 `calls_file`，fake session 逐行 append `get/close`（含 timeout kwargs），
  父进程测试读文件断言；文件不存在 = 零外发。
- 阻塞 marker：fake response 先 yield 首块再永久 sleep（或 sleep N 秒），预算 0.5~1.0s（非 0.02s 演示值），
  elapsed 断言 = deadline + 1s 统一宽限 + ≥10s 平台启动余量。

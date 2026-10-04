# ET-DEADLINE 交接：电话会议采集硬截止时间收口

## 1. 分支与基线

- 基线仓：`C:\Users\郑曾波\Projects\earnings-transcripts\earnings-transcripts`，main/origin/main
  `93fe52c`（ET-S3 合入提交；未改动，仅含既有 untracked `.workbuddy-ai/`、`eval_results.json`，
  未复制、未清理）。
- 本交付分支：`codex/et-s3-deadline`（从 `93fe52c` 建立），唯一 worktree：
  `C:\Users\郑曾波\Projects\earnings-transcripts-s3-deadline`。
- 本包 PWF：`.planning/s3-et-deadline-20261004/{task_plan,findings,progress}.md`（随分支提交）。
- 基线测试 151 passed + 10 goldens matched；本包完成后 **172 passed + 10 goldens matched**。
- worktree 本地复制了 gitignored `config.json`（LLM keys，仅供既有翻译器测试），未提交、不属交付。

## 2. 修改文件

| 文件 | 改动 |
|---|---|
| `retrieval_runtime.py` | **新增** parent supervisor：monotonic 统一 deadline、Popen 等待/terminate/kill、固定 1s 统一清理宽限、确认退出才删临时结果、`retrieval_cleanup_failed` 具名清理报告、`_load_result_file` 大小/JSON/协议/usage/请求标识/schema/超期校验 |
| `retrieval_worker.py` | **新增** 内部 HTTP worker：只做现有 API 调用 + 预算包装 + 一个原子结果文件；无翻译、无原件、无孙进程；key 只经环境变量 |
| `retrieval_budget.py` | **新增**（确有必要）：从 scraper 抽出 `BatchBudget/BatchBudgetExceeded/_BudgetSession`（`_BudgetSession.get` 改调 `record_request()`，行为不变），新增 `usage_snapshot/apply_usage/stop_with/mark_usage_unknown`、单操作 `UsageCounter`。worker 需要这些类但不能拖入 `scraper→common→translator/config` 栈 |
| `transcript_tool.py` | 生产路径（无 `_session_factory` 注入）改为 supervisor 路由；stdin 过大/坏 JSON/`discover+--include-source-payload` 仍零 spawn 本地拒绝；注入路径保持原合作式行为 |
| `scraper.py` | 删除内嵌预算类（改 re-export，旧名不变）与全部进程内直调；`--list` metadata、fool listing、fetch-candidate、fmp fetch 全部经 `_supervise`（预算前检 → supervisor → 回折 usage → deadline/未知用量停批）；`_report`/manifest 支持 usage 未知；`--max-seconds` help 范围修正 |
| `tests/test_retrieval_runtime.py` | **新增** 14 个：阻塞回收/成功/单操作用量/零额度不 spawn/坏退出/缺结果/坏 JSON/超大结果/loader 六种不可信信封/超期拒收/清理失败具名报告/共享 deadline |
| `tests/test_retrieval_cli_e2e.py` | **新增** 6 个：真实 CLI→supervisor→worker→API→serializer 的 FMP `/1`+`/2` 假 200、discover/fetch-candidate 路由、402/429/缺 key 具名、deadline 击杀、坏 worker 不泄 key/body、两文档批次第二份只剩余额度 |
| `tests/test_batch_runtime.py` | 旧 `_session_factory` seam → child launcher seam（`responses` + `calls_file` 断言，读 calls 文件等价替代 `session.calls`）；`slow_body.closed` 断言改为“calls 记录 body 已开始 + 进程确认回收 + 临时根清完”（Windows 下被 kill 的 worker 不会执行 close，连接随进程消亡）；新增零额度不 spawn 用例 |
| `README.md` | `--max-seconds` 改为采集硬截止 + 翻译不在限额内；硬截止实现段；`usage_unknown` 语义；退出码 1 增 `retrieval_worker_failure`；测试计数 151→172；机器接口段补外部硬截止说明 |
| `docs/implementation/s3-et-deadline-handoff.md` | 本文件 |

未改动：`transcript_api.py`、全部 `/1`/`/2`/discovery/candidate 协议、goldens、`config.yaml`、
`companies.txt`、翻译/阅读器/解析模块、CWP/FF 侧代码。

## 3. 两正式入口路由

| 入口 | 路由 |
|---|---|
| `transcript_tool.py`（FF/CWP 走 `--request-stdin --include-source-payload`） | `main()` 在无 `_session_factory` 注入时 → `_supervised_result` → `retrieval_runtime.run_retrieval(operation ∈ fetch/discover/fetch-candidate)`；deadline = 请求 `timeout_seconds`（非法/缺失回退 60s 上限）；`requests_left=None`（单操作无批次额度，worker 侧用 `UsageCounter` 只计数） |
| `scraper.py`（`--list` metadata 与批次 fetch/discover/fetch-candidate） | `_supervise`：`budget.check_request()` 前检（零额度不 spawn）→ `run_retrieval(remaining=budget.remaining_seconds(), requests_left, response_bytes_left)` → `budget.apply_usage` → deadline→`stop_with("batch_deadline")`、usage 未知→`mark_usage_unknown()` |

worker 内仍只有 `transcript_api` 一套 HTTP 实现；`_BudgetSession` 在 worker 侧包住
`requests.Session`（或测试 launcher 的 fake），逐请求/逐 chunk 执行批次额度，usage 经
内部结果信封回折父进程。dry-run 零 spawn；disabled provider / 缺 key 在 worker 的现有
gate 判定，零 HTTP；私有测试缝只有 Python 私有参数
`_retrieval_launcher/_retrieval_spec/_retrieval_temp_root`，无 CLI 参数、无环境变量后门。

## 4. 保证范围与宽限

- deadline 从**正式操作开始**（含写请求文件与 spawn 耗时）按 `time.monotonic()` 计时；
  父进程等待使用同一剩余浮点值，不四舍五入到 1 秒；批次逐文件只传剩余值，不重置。
- 硬保证 = worker 采集在 **deadline + 1.0s 统一清理宽限**内停止：先 `terminate()`，
  必要时 `kill()`；terminate→wait→kill→wait 全部共享同一个宽限窗口，任何 wait 不获得新宽限。
- 只回收本函数创建的那一个 Popen；**确认退出后**才 `rmtree` 该次临时目录；删除异常按
  `error: retrieval_cleanup_failed: ...` 具名报告并保留目录；退出无法确认时不读结果、不删目录。
- 结果文件上限 `MAX_RESULT_BYTES = 6×(2×MAX_BODY_BYTES) + MAX_SOURCE_PAYLOAD_RESULT_BYTES + 2MiB`
  （内容/标题各受一页 `MAX_BODY_BYTES` 约束，`\uXXXX` 最坏 6×，base64 与 content 互斥），
  不会误拒合法最大 payload；请求文件上限 4MiB。
- 超期结果（本地读+校验后已过 deadline）不接受为 `fetched`；父进程本地返回开销
  （读文件+JSON 解析，KB 级正常结果）单独计在 deadline 检查内，不冒称 `Popen.wait` 能抢占解析。
- 纯 Python API 与 `_session_factory` 注入仍是合作式检查，不作为硬保证证据。

## 5. wall-clock / worker 退出测试证据

- `test_blocked_get_is_reaped_within_deadline_and_cleanup_grace`：真实 get 阻塞 marker
  （先记录 call 再永久阻塞），`run_retrieval(remaining=3.0)` 返回
  `reason="deadline"`、`exit_code is not None`（退出已确认）、`usage is None`，
  elapsed < deadline + 1s 宽限 + 10s 平台余量，临时根清空。
- `test_second_operation_shares_the_original_deadline`：第二份操作结束后时钟 ≥ 共享
  deadline（跑满剩余）且 < deadline + 宽限 + 余量。
- `test_slow_stream_hits_batch_deadline_and_stops_the_batch`（batch）：首块送达后永久
  阻塞，rc=3/`batch_deadline`，calls 显示 body fetch 已开始，elapsed < 6+1+10，临时根清完。
- `test_two_document_batch_second_gets_only_remaining_budget`（batch e2e）：第一份成功、
  第二份阻塞；child 信封证明第二份只拿到 `seconds_remaining < --max-seconds` 与
  `requests_left < 64`（前序已消耗），第一份与预置 keep 字节不变，第二份无原件，
  总 elapsed < max + 宽限 + 余量，临时根清完。
- `test_tool_blocked_worker_is_killed_at_the_request_deadline`：工具侧同款（`timeout_seconds=4`）。
- 预算取值依据本机实测：worker 从 spawn 到发出首个 HTTP ≈ 1.1–1.5s（Windows、
  requests+bs4 导入为主），断言预算取约 2 倍余量；未用 0.02 秒演示预算做 CI 临界断言。
  **生产预算包含启动耗时**（deadline 在 spawn 前起算）。

## 6. usage 未知语义

- worker 正常退出（0）且信封完整可信 → `usage = {requests_used, response_bytes_used, exhausted}`
  逐项回折父预算；子报 `exhausted` 原样作为批次终因。
- worker 被 kill / 非 0 退出 / 缺结果 / 坏 JSON / 超大 / 信封或身份不符 → `usage=None`：
  - deadline 原因 → 批次终因 `batch_deadline`（exit 3 partial）；
  - 其他坏 worker → 具名条目 `provider_error/retrieval_worker_failure`（exit 1）+ 停批；
  - 两种情况都 `mark_usage_unknown()`：`budget.report()`/manifest 中
    `requests_used`、`response_bytes_used` 记 `null`、`usage_unknown: true`，
    打印 `budget: requests=unknown ...`，**不宣称 0 消耗、不恢复完整预算**；
    `output_bytes_used` 为父进程侧真实值，保持已知。
- 第一份成功原件与预置 keep 不回滚；失败文档无正式原件。

## 7. golden 与协议

- `python tests/generate_transcript_goldens.py --check` → **10 goldens matched**，
  `tests/golden/` 零 diff。goldens 走 `_session_factory` 合作式路径（lane §3 允许），
  字节不变。
- wire 不变：`earnings-transcript-request/1`、result `/1`、`/2`、discovery `/1`、
  candidate 请求、原语言、payload/hash/size、全部字段；内部 worker 信封
  `et-retrieval-worker/1` 只存在于临时文件，不进公共 stdout。新增的仅是错误码取值
  `retrieval_worker_failure`（挂在既有 `provider_error` 状态下，README 已记）。

## 8. 测试与临时根

```powershell
C:/Miniconda/python.exe -m pytest tests/test_transcript_api.py tests/test_batch_runtime.py tests/test_translation_controls.py tests/test_retrieval_runtime.py tests/test_retrieval_cli_e2e.py -q   # 92 passed
C:/Miniconda/python.exe -m pytest tests/ -q            # 172 passed
C:/Miniconda/python.exe tests/generate_transcript_goldens.py --check   # 10 goldens matched
C:/Miniconda/python.exe -m ruff check scraper.py transcript_tool.py retrieval_runtime.py retrieval_worker.py retrieval_budget.py tests/test_retrieval_runtime.py tests/test_retrieval_cli_e2e.py tests/test_batch_runtime.py tests/test_transcript_api.py   # clean
git diff --check    # 无空白错误
```

- 全部测试离线：fake HTTP 走私有 launcher seam，FMP key 均为假值，写入限 pytest
  `tmp_path` 或 `--output`；每操作临时目录在确认 worker 退出后删除，pytest 临时根自动回收。
- 生产冒烟（零外发）：`transcript_tool.py` 缺 key/坏 JSON/用法错误（rc 0/0/2、输出稳定）；
  `scraper.py --dry-run`（rc 0、零写入）、`--source fmp` 缺 key（rc 1、
  `provider_credentials_missing`、真实 worker ~0.6s）、默认 fool disabled（rc 1、
  `provider_disabled`）。
- 生成资料处置：本次基准脚本 `_bench_*.py` 已删除；smoke 输出目录已删除；
  生产 ET/CWP 原件与配置全程未读未写；未改任何全局 Git 设置、未并 main、未装全局技能。

## 9. 未覆盖事项（交给 MAIN）

1. **真实付费网络路径未离线覆盖**（零网络约束）：生产 `requests.Session` 发真请求、
   真实慢流/重定向/402 的 live 行为仍由 [ET-LIVE](et_transcript_live_import_acceptance.md)
   最多一次承担；本包零真实 paid/LLM 请求。endpoint 权益仍 402/unknown。
2. 清理失败路径（`retrieval_cleanup_failed`）只做了 rmtree 注入故障的具名报告测试；
   “kill 后仍无法确认退出”分支本机不可复现，属防御分支。
3. `--api-key` CLI 显式传参 → child env 覆盖的路径无独立测试（env 继承路径有覆盖）。
4. 子进程 Windows 服务/受限环境下的 Popen 行为未验证；宽限 1s 在极慢 CI 上可能
   使“退出确认”分支落入具名清理报告（不会假装回收，但会保留临时目录）。
5. MAIN 接收后按 lane 要求做一次 FF→ET→CWP 受影响离线接口联调再合入；
   跨仓 wire 无变化，理论上无需 FF/CWP 代码改动，但联调仍需执行。

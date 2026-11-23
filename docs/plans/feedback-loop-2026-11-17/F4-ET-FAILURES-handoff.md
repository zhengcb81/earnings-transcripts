# F4-ET-FAILURES 交接 · 电话会普通入口、坏 TXT 和 worker 失败可复现

本仓（`earnings-transcripts`）按 [F4-ET-FAILURES 施工卡](F4-ET-FAILURES.md) 完成的交付。总控（ROOT）运行号 `f4-coverage-audit-20261121`，发现方 company-wiki；本卡关闭中央档案 [`failure-coverage-20261121.jsonl`](../../../../../company-wiki/docs/plans/feedback-loop-2026-11-17/issues/failure-coverage-20261121.jsonl) 第 10、11 行两条原发生，验收由本仓自己的测试独立完成。

## 一句话结论

普通入口的每一次拒绝（transcript_tool 参数解析、scraper 参数校验、TXT 审计根缺失/配置缺失）、TXT 审计的每个文件（损坏与成功同批 alike）、以及 worker 子进程的真实退出状态（退出码、阶段、脱敏异常类型链、子进程 pid）现在都按本次 run/attempt 身份可跨进程查回；落盘与确认之间断开后，新进程按同一 owner/run/attempt/step 只读对账、补投一次；显式 OUT 保持单次文件语义，直启（未传 OUT）落 owner 持久状态根并向公开收件目录原子发布 `feedback-<32 hex>.jsonl` + 同名 `.ready`；所有业务 stdout、退出码、收据形状与既有正控（402、漏发布用量）逐字节不变。

## 开工状态与写集合

- **工作目录**：`C:/Users/郑曾波/Projects/earnings-transcripts/earnings-transcripts`；发布线 `main`，远端 `https://github.com/zhengcb81/earnings-transcripts.git`。
- **开工基线**：审计基线 `61fe94e201eee0f253a1084b9876d023820ea01c`；开工 HEAD `0bfa07b9350eaa5f2259d286373cecb571099d07`（`docs(pwf): dispatch ET ordinary-failure feedback coverage`，即本卡派工提交）。
- **开工时 `git status --short --branch`**：`## main...origin/main`，仅 `?? .mimosa/`、`?? .workbuddy-ai/`（卡片声明的已有工具目录，全程保留、未提交）；无在途修改，与卡面一致。
- **本卡写集合**（相对开工 HEAD）：修改 7 个源文件 — `transcript_feedback.py`（投影/持久化/发布）、`retrieval_runtime.py`（stderr 捕获 + 脱敏诊断）、`retrieval_worker.py`（阶段 + 失败诊断）、`retrieval_protocol.py`（worker 诊断常量）、`transcript_tool.py`、`scraper.py`、`transcript_audit.py`（入口接线）；新增 `tests/test_f4_ordinary_failures.py`（17 项）、RED 回执、直启 bundle 样例与本交接。
- **最终源码 SHA（候选提交）**：`44e2a2716ecbc27d509acee31c38dd02d9475139`（`main`，`Make ordinary entries, bad TXT and worker failures reproducible`）。交接提交见本文件末尾。

## 对应原发生 → 修复

| 原发生 | 卡内要求 | 交付 |
|---|---|---|
| 第 10 行 `native-failure-not-forwarded`（4 案无观察 + 坏 TXT 仅统计） | 普通拒绝与逐文件失败可按本次 ID 查回 | 四个普通拒绝各自发布 `invocation-refusal` 观察（`expected`/`refusal_recorded`），明细带 `refusal_code`/`stage`/`location`/`entry`/`argument`（无 argv）；TXT 审计逐文件发布 `stored-original-audit` 观察，step 即原报告相对文件引用，损坏原因 `invalid_encoding` 在明细；成功同批文件同批可查；正文零进入反馈 |
| 第 11 行 `provider-cause-lost`（退出码缺失、用量仅 unknown） | 子进程退出事实与原 worker 身份可查，费用语义不变 | `RetrievalOutcome` 新增脱敏 `diagnostics`（supervisor_stage / worker_exit_code / worker_pid / worker_phase / worker_exceptions 类型链 / worker_stderr_type / load_failure），合并进父进程唯一的 finished 明细一次；收据保持 `earnings-retrieval-usage/1` 单行原状，unknown 用量保持 unknown；exit 7 与超时两案冷读可分（`worker_exit` vs `deadline`），与结果文件损坏案（`result_load`/`result_unparseable`、子进程退出 0）三案互异 |

## RED → GREEN 实测

- **RED**（实现前，`tests/test_f4_ordinary_failures.py` 全量）：**9 failed / 5 passed**，回执 [`receipts/red_f4_ordinary_failures.txt`](receipts/red_f4_ordinary_failures.txt)。5 个通过项是卡内要求的正控与对照：bad-wire 既有观察、合法空根审计、402、漏发布用量、强停后 started 行只登记一次（既有去重语义）。
- **GREEN**（实现后）：`tests/test_f4_ordinary_failures.py` **17 passed**。
- **规定回归**：`python -B -m pytest -q tests/test_feedback_owner.py tests/test_feedback_observation.py tests/test_retrieval_cli_e2e.py tests/test_worker_result_budget_contract.py tests/test_transcript_audit.py` = **101 passed**。
- **全量**：`python -B -m pytest -q tests/` = **761 passed / 0 failed**（开工基线 744 + 本卡 17）。
- **Ruff**（本卡改动文件 8 个）：**All checks passed**（`retrieval_runtime.py` 中 `WORKER_PROTOCOL` 重导出标了 `# noqa: F401`，`tests/test_retrieval_runtime.py` 依赖该重导出；`WORKER_ERROR_FILENAME` 确有使用，不加 noqa）。Black：本机 black 版本对未改动文件（如 `common.py`、`config.py`）同样报重排，属版本差异而非本卡引入；本卡不重排并行作者代码。
- **Mimosa 预提交门禁**：通过（仅 low 级提示：`eval_translation.py`/`tests/test_du9_legacy_comparison.py` 的随机数用法与 `retrieval_runtime.py:3` 的命令注入启发式 —— 第 3 行是基线 `61fe94e2` 逐字相同的文档字符串，属预存误报，本卡未引入）。

## 冷读复现（第二个进程，直接可跑）

```powershell
# 1) 生成：一次直启运行（状态根映射到临时目录），业务 stdout = invalid_request、stderr 收据 = 已知零请求
$env:RESEARCH_FEEDBACK_STATE_ROOT = "$env:TEMP\f4-coldread\state"
python -B transcript_tool.py --request-stdin --operation discover --include-source-payload --report-usage `
  < (合法请求 JSON)   # 见 samples 生成命令
# 2) 冷读：第二个 Python 进程逐行读公开 bundle 与 pending 存储
Get-Content $env:TEMP\f4-coldread\state\observations\feedback-*.jsonl
Get-Content $env:TEMP\f4-coldread\state\observations\feedback-*.ready
```

已归档的现成样例（真实直启运行产物，固定身份 `run_id=f4-direct-start-001` / `attempt_id=one`，脱敏后逐字节拷贝）：

- [`samples/direct-start-bundle.jsonl`](samples/direct-start-bundle.jsonl) — 5 条观察（action-started + 四项结果检查，step_id `f4-direct-start-001`）；
- [`samples/direct-start-bundle.jsonl.details.jsonl`](samples/direct-start-bundle.jsonl.details.jsonl) — 同一尝试的 started + finished 明细，含 `completion`（`operation_completed:false`、`attempted:false`）与 `source_availability`（`source_unavailable`）分列事实；
- [`samples/direct-start-bundle.ready`](samples/direct-start-bundle.ready) — 交付就绪标记（owner/run_id/attempt_id/step_ids/计数，无路径无命令）。

复现命令（固定身份；bundle 文件名与 `.ready` 逐字节可复现）：

```powershell
$env:RESEARCH_FEEDBACK_STATE_ROOT = "$env:TEMP\f4-coldread\state"
$env:RESEARCH_FEEDBACK_RUN_ID = "f4-direct-start-001"
$env:RESEARCH_FEEDBACK_ATTEMPT_ID = "one"
python -B transcript_tool.py --request-stdin --operation discover --include-source-payload --report-usage `
  < (合法请求 JSON)   # request_id 固定为 f4-direct-start-001
```

逐次运行之间会变的只有"本次实例事实"：`details_ref`（它是含 `started_at`/`finished_at` 的明细内容哈希，既有设计）与明细里的两个时间戳；`step_id`/`check_id`/`expected`/`actual`/`disposition`/`evidence_ref`/`version`、bundle 文件名与 `.ready` 全部稳定。未设固定身份时 `run_id` 为生成值（形如 `run-<12 hex>`），其可恢复性由 pending 目录文件名给出。

worker 失败明细示例的生成命令：`python -B tools/run_request_budget_e2e.py --case worker-loss --temp-root <临时目录>`（环境带 `RESEARCH_FEEDBACK_OUT`），其 finished 明细含 `worker.worker_exit_code=7`、`worker_exceptions=["SystemExit"]`、`worker_pid`、`worker_phase="launcher"`、`supervisor_stage="worker_exit"`；`--case deadline` 得 `supervisor_stage="deadline"`，两者冷读可分。

## 持久化与公开接口（ROOT 需要的事实）

- **显式 `RESEARCH_FEEDBACK_OUT`**：单次文件 + `*.details.jsonl` 兄弟语义不变；F4-ET 宿主合同完全兼容。落盘失败（含宿主传入不可写路径）时记录先存 owner 持久状态根 `pending/<run>--<attempt>.jsonl`，后续进程按同一身份只读对账补投一次。
- **直启（未传 OUT）**：先存 `<状态根>/pending/<run>--<attempt>.jsonl`（含明细兄弟），运行终态后原子发布公开 bundle —— `<收件目录>/feedback-<32 位小写 hex>.jsonl` + `feedback-<hex>.jsonl.details.jsonl`，脱敏明细就绪后再原子写同名 `feedback-<hex>.ready`。hex 取 `sha256(owner|run_id|attempt_id)` 前 32 位，同一身份补发收敛到同一文件名，同一发生只登记一次。ROOT 确认标记为同基名 `.ack`（owner 从不写它）；无 ack 重跑同身份入口不产生第二份 bundle，有 ack 后不再补投。
- **`RESEARCH_FEEDBACK_DIR`** 指定隔离收件目录；OUT 与 DIR 同设时 OUT 优先；两者均未设置时收件目录为状态根下 `observations`。
- **本机正式默认收件目录（绝对路径）**：`C:\Users\郑曾波\.earnings-transcripts\feedback\observations`。相对状态根的计算方法：状态根 = `RESEARCH_FEEDBACK_STATE_ROOT`（缺省 `Path.home()/.earnings-transcripts/feedback`），收件目录 = `<状态根>/observations`，pending = `<状态根>/pending`。`native_observation_dirs` 建议直接配置上述绝对路径。
- **`RESEARCH_FEEDBACK_DISABLED`**：内部 worker 子进程一律显式关闭反馈（父进程是唯一写者）；无 `USERPROFILE` 的进程无持久状态根，本轮不发布、业务原返回不变。

## 独立审查

独审 agent 按卡内要求复核全部覆盖、真实入口、错误/费用语义、故障恢复、数据脱敏与并发幂等，并实际执行上述命令、亲跑对抗性探针。

### 第一轮（候选工作树，结论 FAIL）发现与修复

| # | 级别 | 发现 | 修复（落 `44e2a271`） |
|---|---|---|---|
| 1 | MAJOR | 同身份并发生产者会重复登记原发生：显式 OUT 与直启 bundle 两种模式各出现重复行（`obs=10 unique=5`），重复永久留在持久存储并每次重新发布 | `transcript_feedback.py` 新增同身份跨进程写锁 `<pending>.identity-lock`（目录锁，超 60s 的僵尸锁自动回收）；`_record()` 在锁内重新扫描 sink 后再做 check-then-append；`DeliveryRecorder.publish()` 按 `(run_id, attempt_id, step_id, check_id)` 去重并只发布被观察引用的明细；`_drain_pending()` 同样在锁内只投递被引用明细。新回归 `test_concurrent_same_identity_producers_register_once`（3 并发真实子进程 × 两种模式，断言 5 行且 5 个唯一身份） |
| 2 | MINOR | launcher 工厂失败时 worker 阶段被记成 `dispatch`，失败阶段无法独立恢复 | `retrieval_worker.py` 的 `session_factory` 去掉 `try/finally`，`phase="dispatch"` 只在工厂成功返回后设置，异常路径保留 `launcher`；回归改为显式断言 `worker_phase == "launcher"` |
| 3 | MINOR | Ruff 未净（`WORKER_PROTOCOL` 重导出报 F401），交接却称已通过 | 两个协议常量重导出加 `# noqa: F401`（该重导出被 `tests/test_retrieval_runtime.py` 使用，必须保留）；交接该行改为按实际结果陈述 |
| 4 | NOTE | 调用方可控标识逐字进入公开产物：URL 查询串、伪造 traceback 文本可经 `request_id`/`ticker` 进入观察与明细 | 新增 `safe_public_word()`：≤128 字且无空白/`/`/`?`/`#`/`&`/`=`/`\` 才原样发布，否则发布 `redacted-<sha256[:16]>` 稳定摘要词（冒号仍属安全字符，本仓 `et-case:<name>` 形状不变）；应用于公开 `step_id`、`evidence_ref`、明细请求事实的 `request_id`/`ticker`，以及明细内嵌收据副本的 `request_id`（stderr 上的业务收据保持原样）。新回归 `test_unsafe_request_id_is_published_as_a_digest_word` |
| 5 | NOTE | 同身份重跑在明细文件留下孤儿行（无观察引用） | 随 #1 一并修复：投递与发布都只处理被观察引用的明细 |
| 6 | NOTE | 交接 Markdown 占位符未填 | 本文件：候选 SHA、测试计数、Ruff 与 Mimosa 事实、样例复现命令、审查结论均按实际填写 |
| 7 | NOTE | 写入不完整诊断无生产消费方 | `active_diagnostics()` / `write_failures` 记入本文件"边界"一节：所有可写介质不可用时由驱动作业读取，业务返回逐字节不变 |

### 第二轮（最终源码版本）复核结论

**PASS** —— 无 blocker、无 major。第一轮全部发现经修复后逐条复验通过：并发同身份生产者
（3 真实子进程 × 5 轮 × 两种模式，共 10 轮）**0 重复**，持久 pending 存储亦 0 重复；
`worker_phase` 现为 `launcher`、超时案仍为 `deadline`、结果文件损坏案仍为 `result_load`；
Ruff 全净；不安全 `request_id`/`ticker`/明细内嵌收据副本全部转为摘要词（stderr 上的业务收据保持原样），
`et-case:<name>` 这类本仓不透明证据引用保持原样。

### 第二轮遗留（交接前已关闭）

| # | 级别 | 发现 | 关闭方式 |
|---|---|---|---|
| 1 | MINOR | 宿主传入的 `evidence_ref` 绕过消毒规则，可把 URL 查询逐字带进公开产物 | `begin_step` 与 `emit_observations` 的宿主证据引用改为经 `safe_public_word()` 发布（不安全取值成为 `evidence-<摘要>`）；本仓 `et-case:<name>` 因冒号仍属安全字符而保持原样，冻结样例不变。新回归 `test_host_supplied_evidence_ref_is_published_safely` |
| 2 | MINOR | 重新生成的样例未提交，提交的样例与交接的冷读命令不一致 | 三份样例以固定身份（`run_id=f4-direct-start-001` / `attempt_id=one`）重新生成并随本次交接提交；交接命令即按该身份固定，bundle 与 `.ready` 逐字节可复现（仅 `details_ref` 因明细时间戳而随运行变化） |
| 3 | NOTE | 遗留的同身份锁会推迟发布（不丢记录），但每次取锁都等满预算，单次运行多耗约 5.7s | 取锁预算由 40×25ms 收紧为 12×20ms；被占用的锁改为"推迟到下一次尝试"而非丢失，僵尸锁（>60s）自动回收；边界一节已写明该语义 |
| 4 | NOTE | 取锁预算耗尽后无锁追加，理论上仍有极窄重复窗口 | 未复现（3 轮 × 3 并发 + 人为占锁共 0 重复）；僵尸锁回收与 60s 窗口共同限定了暴露面，不改代码 |
| 5 | NOTE | `WORKER_ERROR_FILENAME` 上的 `# noqa: F401` 多余（该常量确有使用） | 去掉该行的 noqa，保留 `WORKER_PROTOCOL` 上确有必要的 noqa |
| 6 | NOTE | 拒绝明细里的 `argument: "--periods"` 是静态选项名 | 保留：它是卡内要求的"发生位置"常量，用户输入值（`not-a-period`）从不发布 |

最终源码版本：`44e2a2716ecbc27d509acee31c38dd02d9475139`（第二轮 PASS 所绑定的版本）；
其后为关闭两项 MINOR 的收据提交 `01e3a772575613036962cfb292c3e52d6d964244`（`transcript_feedback.py`
两处证据引用消毒 + 字符集调整、取锁预算收紧、多余 noqa 移除、三份样例重生成、
新回归 `test_host_supplied_evidence_ref_is_published_safely`、交接 Markdown、PWF 状态入口）。

### 第三轮最终确认的状态

审查员对 `01e3a772` 的第三轮最终确认因 **Token Plan 用量上限（错误 2056）** 中断，未能出结论。
两项 MINOR 关闭由本方按执行验证收口（逐项实测，不依赖既有声明）：

- 消毒回归 + 并发回归 + worker 阶段回归：`tests/test_f4_ordinary_failures.py` 17 项全过
  （含 `test_host_supplied_evidence_ref_is_published_safely`、`test_unsafe_request_id_is_published_as_a_digest_word`、
  `test_concurrent_same_identity_producers_register_once`、`test_worker_loss_exit_state_is_preserved`）；
- 全量 `python -B -m pytest -q tests/` = **761 passed / 0 failed**；改动文件 Ruff **All checks passed**；
- 样例复现：按交接命令重跑，bundle 字段级一致（仅 `details_ref` 随明细时间戳变化）、`.ready` 逐字节一致；
- 冒烟遗留清理：本机默认状态根的临时产物已删除。

**额度恢复后的待办**：请审查员对 `01e3a772` 做一次简短复核（范围 = 本节列出的收据提交增量：
evidence_ref 消毒行为、锁预算延迟语义、noqa/样例一致性），确认无新发现后本卡即完全收口。

## 拒绝门禁审视（卡内 C 节要求）

四个普通拒绝逐一按业务目的审视过门禁必要性，结论均为**必要门禁、如实留痕、无需修复**：
`--operation bad`（choices 白名单，非法取值）、`--periods not-a-period`（精确期次 token 语法）、
TXT 审计根缺失（无输入即无从审计）、config 缺失（仓库配置是审计实现的前提）。没有任何
"正常请求被误拒"的情形，因此无需在本仓 PWF 记减门方案；全部按 `expected`/`refusal_recorded` 留痕。

## 调用数与清理结果

- 本卡全部隔离运行零外网（sitecustomize 审计钩子阻断）、零下载、零翻译、零付费调用；唯一涉及的
  402 案由离线假传输回放，`paid_calls=0`（`tools/run_request_budget_e2e.py` 自证字段）。
- 全量 pytest 的临时目录由 pytest 自行回收；e2e harness 断言 `temporary_root_restored=true`、
  `worker_temp_empty=true`；测试结束后无残留目录（`test_no_stray_temp_roots_outside_the_test_root` 钉住）。
- 实现期间的冒烟与调试在本机默认状态根落过 3210 个测试残留文件，收尾时已全部删除，默认状态根
  恢复为会话前不存在的状态；`.mimosa/`、`.workbuddy-ai/` 两个并行工具目录全程未动。

## 边界与未竟事项

- `--capabilities` 仍不产生任何观察（无 retrieval 步）；scraper 批量运行本身不新增逐项观察（本卡范围仅普通入口拒绝），失败逐项归并将随 ROOT 后续派工进入原修复途径。
- 全量可写介质不可用时的"未确认区间"语义由既有 started 行承担：started 先于副作用落盘，终态缺失即未确认；所有介质均不可用时本轮无任何新增落盘，由外层已有启动收据标出。驱动作业要读取"哪些原因已不可恢复"，调用 `transcript_feedback.active_diagnostics()`（或 recorder 的 `write_failures`）；该信息只存在于进程内，不写入业务流。
- scraper 批量采集的逐公司/逐期次失败投影（`_process_*_company` 内层）不在本卡两个原发生范围内，保持原状。
- 公开 `step_id`/`evidence_ref` 对不安全取值改为摘要词：调用方仍可用原生 `request_id` 在自己的命名空间内定位业务，但反馈产物里不再逐字出现 URL 查询串、路径或伪造 stderr 文本。宿主传入的公开证据引用同样受此规则约束（`et-case:<name>` 这类不透明引用保持原样）。
- 同身份写锁被别的进程占用时，本次运行**推迟**发布而不丢记录：观察与明细完整留在 pending 存储，由下一个同身份进程按只读对账补投；被强杀进程留下的锁超过 60s 自动回收。取锁预算（12×20ms）用尽后本进程以无锁方式继续，绝不死锁或崩溃。

## 2026-11-23 总控复验补充

作者交接基线为 `5399205`。总控在 Windows 默认长临时根复跑得到 15/17。普通 CLI 能返回原退出码，但 JSONL 的明细、默认发布包和锁比主文件的路径更长，有时只剩主文件或旁路诊断。独立进程使用 Win32 扩展路径检查磁盘也缺文件。ET 现从同一处处理本地文件、UNC、同目录原子替换、锁、收件目录和 SQLite 的物理路径。调用方继续传普通目录。公开 `get-action` 以只读方式打开原库，并正确处理文件名里的空格、中文、井号和百分号。

另有两个独立的漏记原因。旧 reader 虽读入整个文件，却仅解析前 8 MiB。完整日志现在逐行解析到末尾，复用判断分块比较完整内容。旧版还让同一运行中不同入口的拒绝共用一个步骤号。现在按原生入口、阶段、错误码、源码位置和参数角色区分。相同位置重放只登记一次，既有 `cli-refusal` 行按其原生明细识别，原记录保持原字节。

本仓的跨进程验收位于 `tests/test_f4_ordinary_failures.py`。

- 在至少 375 字符的普通 Windows 路径下分别走单次 OUT、指定 DIR 和 owner 默认根。真实参数拒绝返回 2，两个新进程能读回同一原因和明细。收件端确认并取走成品后，再运行同一身份，成品不会重投。
- 使用本仓既存的漏发用量样本，从深路径通过公开 triage 打开现有证据，两次冷 `get-action` 读回同一份决定。SQLite 路径和旁路路径都包含在测试中。
- 先从正常 CLI 保存一次退回，再在两个原生 JSONL 加超过 8 MiB 的合法空白，第二个公开入口发生的退回能在同一份交付中查回。旧代码缺此行。
- 将现存早期拒绝按旧格式冻结，再重复同一入口，并从不同位置产生第二个拒绝。冷读恰有两条，旧行逐字段相等，再次重放没有第三条。

这些用例通过测试专用网络阻断和临时根运行，未接触公司原件、模型、收费服务或用户账本。对应原施工卡的 scraper 批量任务逐公司/逐期间失败仍在独立项中。

冻结前在 Windows 宿主运行普通失败、owner 公开端口、反馈投影、真实 CLI 子进程、worker 结果预算与 TXT 审计六份测试文件，共 124 项通过（86.93 秒）。最终源码再单独复跑上述 6 个新增用例，6 项通过（8.76 秒）。Ruff 检查和 `git diff --check` 均通过。ROOT 将以此候选独立复验后发布。

## 2026-11-23 最终补投及 ACK 复验

在 `991ea45` 上，用真实 Windows 子进程分别复现了三个遗漏原因。

- 仅让明细文件不可写时，显式 OUT 的观察仍然写出，旧恢复器只在 pending 的观察里找引用，随后移除了最后一份原生明细。现在按同一 owner、run 和 attempt 同时核对已写出的观察和临时保存的观察，等明细落入原目标后才释放恢复副本。默认模式只有遇到写入故障才使用本仓 `recovery` 目录。缺少任何被引用明细时暂缓发布该组，原 CLI 照常结束。
- 空公司列表的真实 `scraper --dry-run` 返回 1 并打印原因，过去未发布观察。现在记录 `empty_company_selection`、入口和公司选择阶段。原退出码和 stderr 保持，重复同一选择只留一条。这是预期的无匹配结果，无需启动业务修复。
- 消费方确认第一份后，同一运行后到的不同拒绝曾永远藏在 pending。现在第一组的成员摘要在发布前原子写入本地。每个晚到的不同发生都有稳定的后续投递名和单独的 ACK；已有的公开包保持原字节。中断后只读原生记录重建同名文件，期间不重新采集或请求供应商。旧版没有成员文件时，先读取既有原公开包，或根据旧确认收据的步骤和计数恢复范围。若历史数据和确认内容均丢失，记入 `legacy_delivery_membership_unknown` 并以原发生标识向宿主补投，由宿主原有的幂等登记去重。

新版 `.ready` 中的 `observations_sha256` 和 `details_sha256` 分别对应 JSONL 和明细的完整原始字节，包含换行。消费方仍将原 `.ready` 原子改名为同名 `.ack`。`run_id`、`attempt_id` 以及总控的发生去重协议不变。首次正常交付仍产生一份包；已发布成品之后出现的增量可以产生多份。

本仓用 `tests/test_f4_ordinary_failures.py` 的 `unavailable_details`、`empty_company_selection` 和 `ack_only_confirms` 复验。显式和默认两种模式均在只坏明细的情况下保留可恢复证据，两次冷启动都取回同一条明细。对首份做 ACK 后通过另外两个 CLI 产生两个不同的真实拒绝，并验证旧包及 ACK 逐字相同。模拟新数据已落、ready 尚未发出，后一个进程恢复同名标记；所有新组确认后再次启动，零新增投递。

最终六份模块回归共 128 项通过（89.07 秒），另在冻结源码上重跑四个新增反例，4 项通过（8.13 秒）。回归均使用隔离目录，原生日志可由独立进程再次打开。Ruff 与 `git diff --check` 通过。

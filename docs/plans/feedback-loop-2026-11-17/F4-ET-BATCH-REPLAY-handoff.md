# F4-ET-BATCH-REPLAY 交接 · 批量电话会逐项复原

本仓（`earnings-transcripts`）按施工卡 [`F4-ET-BATCH-REPLAY`](../../../../../company-wiki/docs/plans/feedback-loop-2026-11-17/owner-cards/F4-ET-BATCH-REPLAY.md) 完成的交付。总控运行号见总控 [replay-coverage-20261123](../../../../../company-wiki/docs/plans/feedback-loop-2026-11-17/replay-coverage-20261123.md)；本卡关闭上一张 F4-ET-FAILURES 交接声明的余项——"scraper 批量采集的逐公司/逐期次失败投影（`_process_*_company` 内层）不在原发生范围、保持原状"，验收由本仓自己的测试独立完成。

## 一句话结论

批量路径的每一个工作项（FMP 逐期、Fool 名单发现 + 逐期抓取、`--list` 逐公司、本地原件复用/冲突、明确未派发、已开始被批截止中断的项）现在都按原生 request ID 作为步骤身份可跨进程冷读：请求发出前有 started 行，项终态立即落 `batch-item` 行（原阶段 fetch/parse/save/discovery、结果词、脱敏原因链、是否派发、原计量收据、批截止停止原因），整批另有 `batch-outcome` 行（真实退出码、manifest 引用与 SHA、计量快照）。第二个进程经现有公开 owner 命令只读对账：完成项报原 job_status，明确未派发报 `not_applicable`，费用未核到保留 `unknown`，强停未完成报 `unconfirmed`，伪造终态查不回。业务原 stdout、退出码、manifest 与原语言 TXT 逐字节不变。

## 开工状态与写集合

- **工作目录**：`C:/Users/郑曾波/Projects/earnings-transcripts/earnings-transcripts`；发布线 `main`，远端 `https://github.com/zhengcb81/earnings-transcripts.git`。
- **开工基线**：`f2512176baf1642f534649291a235487ea9d6d3d`（`main == origin/main`，工作区仅 `?? .mimosa/`、`?? .workbuddy-ai/` 两个既有工具目录，全程保留、未提交）。
- **最终源码 SHA（候选提交）**：`65e82f6`（`Make batch items, list runs and batch outcomes reproducible`）。独立审查绑定该版本；交接与指针提交随后。
- **本卡写集合**（相对基线）：`scraper.py`（批量/列表路径逐项接线 + 三个门禁留痕）、`transcript_feedback.py`（`BatchJournal`、`CHECK_BATCH_ITEM`/`CHECK_BATCH_OUTCOME`/`BATCH_STEP_ID`/`ISSUE_BATCH_ITEM`、`BATCH_ITEM_*` 词表、started 行 `terminal_check_id`、失败前缀参数）、`tools/feedback_owner.py`（reconcile 的批量分支：`_terminal_detail`/`_batch_job_status`/`_batch_outcome_status`、终态检查扩展）；新增 `tests/test_f4_batch_replay.py`（31 项）与 RED 回执、样例包；`tests/test_batch_runtime.py`、`test_retrieval_cli_e2e.py`、`test_du7_extraction_e2e.py`、`test_du9_legacy_comparison.py`、`test_optional_local_receipt.py` 五个进程内批量 harness 增加 `RESEARCH_FEEDBACK_DISABLED=1`（业务回归不验证反馈旁路，本机状态根零写入；新套件另证开/关两种模式业务返回逐字节一致）。
- **未改动**：FF、CWP、总控协议、真实电话会目录、provider 与计量接口（`earnings-retrieval-usage/1` 原状）。

## RED → GREEN 实测

- **基线复现**（实现前探针，真实 `scraper.main` 子进程 + 真实 supervisor + 真实 worker，私有 launcher 接缝，全离线，状态根映射临时目录）：两期 HTTP 500 → 退出 1、manifest 两条 `provider_error/provider_http_500`、两次夹具请求、**反馈端零文件**；两期 HTTP 200 空列表 → 退出 0、两项 `not_found`、零反馈文件；`--max-requests 1` 与批次硬截止的"已开始项"**连账本条目都没有**（manifest 缺失该期）。回执：[`receipts/red_f4_batch_replay.txt`](receipts/red_f4_batch_replay.txt)。
- **RED**：新套件在基线源码（`git archive` 提取的干净副本，不用 stash）上收集即失败——`AttributeError: module 'transcript_feedback' has no attribute 'CHECK_BATCH_ITEM'`。
- **GREEN**：`tests/test_f4_batch_replay.py` **31 passed**（独立审查复跑 135.56s）。
- **受影响回归**：`test_batch_runtime + test_feedback_owner + test_feedback_observation + test_retrieval_cli_e2e + test_worker_result_budget_contract + test_transcript_audit` = **142 passed**；六个文件 + 新套件合计 318 项通过（另有 1 项为**上卡遗留**偶发，见下节）。
- **全量**：`python -B -m pytest -q tests/` = **803 passed / 0 failed**（312.96s）。
- **Ruff**（九个改动文件）：**All checks passed**。`git diff --check` 干净。
- **Mimosa 预提交门禁**：通过；仅**预存** low 提示（`eval_translation.py`、`tests/test_du9_legacy_comparison.py` 随机数用法，`retrieval_runtime.py:3` 命令注入启发式——均为基线已报、上卡已记录的项，本卡未引入）。按门禁要求如实说明：该扫描覆盖不完整，不能解释为项目安全。

## 分母（按实际启动运行计数）

每案的夹具调用数由 `calls.jsonl` 逐条断言（真实子进程、真实 supervisor、真实 worker）：

| 场景 | 实际派发运行 | 断言 |
|---|---|---|
| 两期 HTTP 500（两种投递模式） | 2 | `gets==2==manifest.budget.requests_used` |
| 500 + 合法空命中 | 2 | 逐期 `(year,quarter)` 顺序断言 |
| 500 + 成功（A 失败不被 B 覆盖） | 2 | 同上 |
| 普通公司两期成功 | 2 | TXT 落盘两份 |
| Fool 名单 + 逐期 | 2（discovery+fetch-candidate） | URL 顺序断言 |
| `--list` 各案 | 1 | 零正文零写入 |
| 预算发出前耗尽 | 1（第二期未派发，`not_dispatched`） | `gets==1` |
| 已开始项被批截止 | 2（第二期用量 unknown） | `gets==2` |
| 第一期终态后强停 | 2（1 完成 + 1 未确认） | `gets==2` 贯穿始终 |
| 本地原件复用/冲突 | 0 | `gets==0` |

## 冷读复现（第二个进程，直接可跑）

固定身份样例（真实直启运行产物，逐字节归档，脱敏后原样拷贝）：

- [`samples/batch-two-500.jsonl`](samples/batch-two-500.jsonl) — 13 条观察（2 started + 8 provider checks + 2 `batch-item` + 1 `batch-outcome`）；
- [`samples/batch-two-500.jsonl.details.jsonl`](samples/batch-two-500.jsonl.details.jsonl) — 5 条明细（2 started + 2 finished 项 + 1 batch）；
- [`samples/batch-two-500.ready`](samples/batch-two-500.ready) — 交付就绪标记（含两份字节 SHA）。

复现命令（固定身份 `run_id=f4-batch-two-500` / `attempt_id=one`；bundle 文件名 `feedback-83a5cd561cb5335b757e7b65fff2412e.jsonl` 由 `sha256(owner|run|attempt)[:32]` 决定，跨运行稳定；`details_ref` 随明细时间戳变化，既有设计）：

```powershell
$env:RESEARCH_FEEDBACK_STATE_ROOT = "$env:TEMP\f4-batch-coldread\state"
$env:RESEARCH_FEEDBACK_RUN_ID = "f4-batch-two-500"
$env:RESEARCH_FEEDBACK_ATTEMPT_ID = "one"
# 两期都返回 HTTP 500 的离线夹具走法见 tests/test_f4_batch_replay.py 的
# run_batch/batch_spec 接缝（私有 launcher，零外网）。
```

冷读与查证（owner 命令在本仓等价公开入口）：

```powershell
python -B tools/feedback_owner.py --db <临时目录>\owner.sqlite `
  --sidecar-dir "$env:TEMP\f4-batch-coldread\state\observations" reconcile
```

- `batch-item` 行 → `job_status=failed|succeeded|interrupted`、`cost_state=unknown|not_applicable`、`status=cost_unknown|known`；
- started 行按 `terminal_check_id=batch-item` 配对；伪造终态（started 行改 check_id）→ `state=unknown`；
- reconcile 只读：不建库、不新增任何 provider 调用。

## 持久化与公开接口（ROOT 需要的事实）

- 批量观察走**既有** recorder/inbox/`.ready` 交付机制，新增的只有行内容：`parent_step_id=batch`、`terminal_check_id=batch-item`（总控 `unfinished_starts` 据此配对）、`evidence_ref=<原生 request_id>`。显式 OUT、默认收件目录两种模式行为一致；`RESEARCH_FEEDBACK_DIR`/状态根计算方法沿用 [F4-ET-FAILURES 交接](F4-ET-FAILURES-handoff.md)的事实，不变。
- **owner 查证入口**即既有 `tools/feedback_owner.py`（部署 argv 见 [F4-ET](F4-ET.md)）。reconcile 新增批量回答：`_batch_job_status`（成功=文档在手 fetched/discovered/reused/legacy_unverified；interrupted=`not_dispatched`；其余文档化词=failed；未文档化词=unconfirmed）与 `_batch_outcome_status`（整批 exit 0=succeeded / 3=interrupted / 其余=failed）。合法空结果（`not_found`/`ambiguous`）对账为 `job_status=failed`、`cost_state` 按是否派发为 `unknown`/`not_applicable`——**与普通入口既有语义一致**（transcript 步的 not_found 同样对账 failed）；观察行本身 `disposition=expected`，总控分诊可据 disposition 判"合法空"，不进修复途径。独审标记此为 note，维持与既有端口一致。
- `RESEARCH_FEEDBACK_DISABLED=1` 或空 `RESEARCH_FEEDBACK_OUT`：批量路径同样零旁路写入，业务返回不变（套件 identity 两组 × 两种关闭方式逐字节对照）。

## 独立审查

独审 agent 未参与实现，按最终源码（`65e82f6` 工作树）复核卡片全部要求并实际执行命令与对抗探针。**结论：PASS，无 blocker。**

### 复跑与探针（全部由独审实际执行）

- 套件复跑：新套件 31 passed；六文件受影响回归 142 passed；Ruff All checks passed。
- 业务对照（独审自写驱动）：两期 500 在 `RESEARCH_FEEDBACK_DISABLED=1` 与默认直启两种模式下 `business_facts` **EQUAL**，退出 1/1。
- 冷读：13 行全部 `feedback-observation/1`、身份稳定唯一；`.ready` 双 SHA 核对通过；**假密钥与临时路径零出现**于 bundle/details/ready 三件字节。
- owner 对账：两项 failed/unknown；重放同答；伪造终态 `state=unknown`；**不建库**（只读证实）。
- 对抗加测（独审自选、实现者未跑）：小写 ticker 两期 500 对照 EQUAL；未知 ticker `ZZZZ` → 退出 1、`empty_company_selection` 拒绝一行、业务文件对照 EQUAL；取走成品 + `--capabilities` 补投（ACK 模拟）→ **不重发**，pending 存储保留原行与 `.delivery.json`——同一发生恰投递一次。

### 审查发现与关闭（本轮全部修完）

| # | 级别 | 发现 | 关闭方式（落 `65e82f6` 之后的复审提交） |
|---|---|---|---|
| 1 | minor | `transcript_feedback.py` 常量块 `CHECK_BATCH_ITEM`/`CHECK_BATCH_OUTCOME` 各定义两次 | 删除重复块（复审提交） |
| 2 | minor | 死参数：`BatchJournal.begin(item=)`、`complete(dispatched=)`、`_observe_fetched(stopped_by=)` 无任何调用方使用 | 三处签名收紧，单测同步 |
| 3 | minor | 卡内交付物"交接 Markdown + 提交推送"当时缺失 | 本文件与提交即关闭 |
| 4 | note | `.mimosa/`、`.workbuddy-ai/` 未 gitignore | 维持上两卡惯例：既有工具目录，保留不提交 |
| 5 | note | 合法空结果（`not_found`）对账为 `job_status=failed` | 维持与普通入口既有 owner 语义一致（transcript 步同判 failed）；disposition=expected 已区分"合法空"，记录于此供分诊使用 |

### 上卡遗留复现报告（移交 ROOT 裁决）

`tests/test_f4_ordinary_failures.py::test_concurrent_same_identity_producers_register_once` 偶发失败（本卡与干净 HEAD 各 ~2/20）——同一身份多进程并发时，后启动进程 `__init__` 的恢复性发布可能在前一进程**写入中途**冻结一份"半程"基础组成员（实测 3+2 两包）；随后两行走"成品后增量"的后续投递包（`sha256(hex/occurrence)` 稳定名 + 独立 ACK）。这正是 F4-ET-FAILURES 交接第二轮遗留 #4 写明"**理论上仍有极窄重复窗口｜未复现（不改代码）**"的那条；现**已复现**。投递协议本身允许成品后的多份增量包（宿主按 ACK 去重），且行的去重不变（观察存储仍恰一次）；属交付分组语义而非数据完整性缺陷。按写入边界（交付分组属上卡冻结验收），本卡不改，交 ROOT 判定是否安排上卡后续。

## 拒绝门禁审视（卡内要求）

新增/触及的拒绝路径逐一按业务目的审视，均为**必要门禁、如实留痕、无需修复**：

- `candidate_discovery_unavailable`（FMP 无 metadata-only 列表，能力事实）；
- `period_unresolved`（FMP 无发现端点，明确期间是唯一安全用法）；
- `instance_lock_held`（并发写互斥，防互相覆盖与重复消耗翻译额度）；
- `empty_company_selection`（既有，本卡仅验证其可查回）。

无"正常请求被误拒"情形，无需 PWF 减门方案。

## 调用数与清理结果

- 本卡全部隔离运行零外网（sitecustomize 审计钩子）、零下载、零翻译、零付费调用；FMP 密钥为测试常量，独审核实其**零出现**于任何公开产物。
- 收尾清理：本机默认状态根 `~/.earnings-transcripts/feedback` 曾被**既有**进程内 `transcript_tool` 测试写入 201 个文件（预存行为，与本卡无关；已核实单测探针在改动前后行为一致），收尾时全部删除，状态根恢复为会话前不存在的状态；测试临时根由 fixture 断言删除（`short_root` 收尾 `assert not root.exists()`）；独审 scratch 与 RED 用干净 HEAD 副本已删除。
- `.mimosa/`、`.workbuddy-ai/` 两个并行工具目录全程未动。

## 边界与未竟事项

- `--translate` 的翻译耗时与结果不在逐项观测内（沿用 `--max-seconds` 只约束采集的既有边界）；翻译失败仍走原 log warning 路径。
- `--dry-run` 零 HTTP 零写入零观察（套件钉住）；`--capabilities` 不产生观察（既有边界）。
- `manifest.entries[].file` 在观察明细里只留文件名；完整路径仍在 manifest 原状中。
- 批次硬截止下 worker 诊断（`supervisor_stage=deadline`、异常类型链）按上卡既有脱敏机制并入项明细。
- ROOT 接收后按卡执行隔离组合验证；上卡并发窗口的裁决与（若安排）修复属上卡后续。

# MB-ET handoff

## 1. 基线与交付

| 项 | 值 |
|---|---|
| 原仓 | `C:/Users/郑曾波/Projects/earnings-transcripts/earnings-transcripts` |
| 基线 commit | `003c1c065109d0b02c7bd4de09ad3e8461a99c0e`（= `origin/main`） |
| 工区 | `C:/Users/郑曾波/Projects/mb11-et` |
| 分支 | `codex/mb11-et` |
| 目标主线 | `main` |
| 代码 commit | `8e116a4`（最终代码 tip） |
| 代码提交序列 | `99b37cd` 实现；`8a00531`/`85456ff` 测试增强；`9f362ea` P1 修复；`3fc129c` AST 重导出检查；`60b1050` preflight 纯模块化；`8e116a4` 清理 |
| 文档 commit | 本文件所在提交（仅 PWF/证据，未改源码） |
| remote | `origin` = `https://github.com/zhengcb81/earnings-transcripts.git` |
| 推送 | 代码 tip `8e116a4` 已推送；本文件所在文档提交随后推送同一分支 |

实际代码/测试 diff 文件表（全部在卡内写集合或其直接边界内）：

| 文件 | 类型 | 内容 |
|---|---|---|
| `transcript_contracts.py` | 新增 | 16 个公共 wire schema/默认额度常量；仅标准库 |
| `transcript_provider_settings.py` | 新增 | `ProviderSettings`、`DEFAULT_PROVIDER_SETTINGS`、`provider_operation_capability`、`EXCHANGES`；仅标准库 |
| `transcript_preflight.py` | 新增 | 纯请求校验/provider gate/preflight（`_validate_request`、`_provider_gate`、`_base_result`、`preflight_transcript_fetch`、`_InvalidRequest`、`_TICKER_RE`）；仅标准库 + contracts/settings |
| `retrieval_protocol.py` | 新增 | worker 协议：`WORKER_PROTOCOL`、请求/结果文件名与上限、envelope 构造/校验、usage 解析、按实际额度派生的 `result_transfer_limit`；仅标准库 + contracts |
| `transcript_api.py` | 修改 | 从纯模块导入并兼容重导出；HTTP/身份/期间/抽取/provider 行为未改 |
| `retrieval_runtime.py` | 修改 | 只依赖 protocol/settings；删除 `MAX_RESULT_BYTES`、`_result_read_limit`、`_expected_*`、`_parse_usage` |
| `retrieval_worker.py` | 修改 | 不再导入 supervisor；请求读取/结果写出走 protocol；结果写入前用同源派生限额 |
| `transcript_tool.py` | 修改 | capabilities/契约走纯模块；preflight 走 `transcript_preflight`；provider 操作函数只在协作 fetch 路径惰性导入 |
| `tests/test_runtime_contract_isolation.py` | 新增 | ET-01 隔离与公开兼容证明（7 项） |
| `tests/test_worker_result_budget_contract.py` | 新增 | ET-02 结果限额正反例（5 项） |
| `tests/test_retrieval_runtime.py` | 修改 | 替换已删除常量的引用；固定帽测试改为派生限额断言 |
| `tests/test_explicit_response_budget.py` | 修改 | 读上限断言改为与 `result_transfer_limit` 同源比较 |
| `tests/test_provider_cost_capability.py` | 修改 | capability patch 点随 `_provider_gate` 迁移到 `transcript_preflight` |
| `docs/plans/mb11-et-2026-11-11/*` | 新增 | 本 PWF、证据与交接 |

未改：`scraper.py`、`transcript_extraction.py`、`legacy_projection_comparison.py`、`translator.py`/`translate.py`、`retrieval_budget.py`、原件/缓存/凭证/配置、全局技能目录、外层 `Projects/earnings-transcripts`。

精确 diff（`git diff 003c1c0..8e116a4`，仅代码/测试，不含 PWF）：

```
A  transcript_preflight.py                       223 ++++++++++
A  transcript_contracts.py                        27 ++
A  transcript_provider_settings.py                61 +++
A  retrieval_protocol.py                         229 ++++++++++
A  tests/test_runtime_contract_isolation.py      479 ++++++++++++++++++++
A  tests/test_worker_result_budget_contract.py   236 ++++++++++
M  transcript_api.py                             293 ++---------------
M  retrieval_runtime.py                          155 +++------
M  retrieval_worker.py                            50 ++--
M  transcript_tool.py                             35 ++-
M  tests/test_retrieval_runtime.py                28 ++-
M  tests/test_explicit_response_budget.py          9 +-
M  tests/test_provider_cost_capability.py          6 +-
13 files changed, 1402 insertions(+), 429 deletions(-)
```

## 2. 逐项要求

### 卡内要求逐条对照

| 卡内条目 | 结果 | 证据 |
|---|---|---|
| 任务定位/唯一工区：原仓、基线、目标 main、新工区、分支 | PASS | `git worktree list`；§1 |
| PWF 三件套（task_plan/findings/progress）+ inputs 复制 | PASS | `docs/plans/mb11-et-2026-11-11/` |
| 开工只读本卡 + interfaces/acceptance/runtime_verification + 本仓 AGENTS | PASS | 本仓无 `AGENTS.md`（已确认）；`findings.md` |
| 共用规则：活动入口/逻辑模块/公开面/状态 owner/依赖方向 + 兼容/退休分类 | PASS | `findings.md` 表；`retrieval_budget.py` 未改 |
| 共用规则：保留公开协议、只写自己代码和状态、不擅自改邻仓 | PASS | §3；diff 仅 13 个代码/测试文件 |
| A→B→C 连续执行，不需逐步批准 | PASS | `progress.md` |
| 负例体现实际能力/依赖错误，不把缺依赖/沙箱失败当产品 RED | PASS | `evidence/red_et01_*.log`、`red_et02_*.log` |
| 已有边界通过就保留，不为凑改动重写 | PASS | 既有 suite 174 passed；仅改被删除常量的引用和 patch 点 |
| 独审覆盖完整度/架构归属/回归/所有新增测试；修复后复核 | PASS | `review.md` 三轮；P1 → `9f362ea` → APPROVE；preflight 迁移 → PASS |
| 测试用新临时目录、恢复初始状态；生产原件/配置/WIP 不动 | PASS | §6；`evidence/INDEX.md` |
| 只跑受影响测试与必要最终包级 CI，不删断言换绿 | PASS | §4；无删除断言 |
| 提交并推送分支；不合并 main；不改 ROOT 总 PWF | PASS | `origin/codex/mb11-et`；未合 main |
| 交付 handoff.md/json、独审报告、测试/恢复证据索引 | PASS | 本文件、`handoff.json`、`review.md`、`evidence/INDEX.md` |
| 目标：分离纯合同/settings/worker wire 与 provider 实现 | PASS | ET-01 |
| 目标：保留原语言采集、预算和恢复行为 | PASS | `scraper.py`/翻译未改；既有 suite 通过 |
| 目标：移除 worker 固定 `MAX_RESULT_BYTES` 与显式 quota 冲突 | PASS | ET-02 |
| 写集合：纯模块 + 边界导入 + 两个新测试 | PASS | 精确 diff；`transcript_preflight.py` 是为满足 B4 字面要求拆出的纯校验层，非新业务模块 |
| 写集合：必要时 retrieval_budget/交付清单接线 | PASS | budget 未改；`evidence/runtime_manifest.json` 列全 11 个 runtime 文件 |
| 写集合：其他 provider 算法、scraper、translation 不扩改 | PASS | 精确 diff |
| 不写 FF/CWP/SID、真实 output/缓存/凭证、全局技能、外层仓 | PASS | `git status`；无跨仓写入 |
| A1 新进程禁 provider 仍可导入合同/settings 并构造协议状态 | PASS | `test_supervisor_imports_without_provider_http_or_parser` |
| A2 fetch/discover/capability 行为/字段/usage 不变；settings 不污染；capabilities 无 key/HTTP | PASS | 既有 suite；`test_explicit_provider_settings_*`；capabilities 逐字节 golden |
| A3 显式额度边界正反例，覆盖 JSON/base64；先复现固定帽；不搬入新协议 | PASS | 5 个 worker 预算测试；RED 日志 |
| B4 抽纯值/兼容重导出；HTTP/parser 仅在 worker provider 层加载；保留 deadline/预算/unknown/清理 | PASS | supervisor/协议/preflight 均不加载 provider；父进程 fetch 拒绝在 `transcript_preflight` 完成；`test_fetch_preflight_is_provider_import_free` |
| B5 worker 限额同源；日志/receipt 体积不决定成功；真实上限生效；不加 output-cap/默认翻译 | PASS | `result_transfer_limit` 单一公式；真实超额测试 |
| C 临时 runtime/空 cwd/公开 CLI + 测试 provider；不建 wheel、无生产后门；独审后交付 | PASS（seam 说明见下） | `evidence/runtime_verify.log` |
| 已有测试命令覆盖卡列 7 个文件 | PASS（跑 10 文件超集） | `evidence/tests_affected.log` |
| 公开 CLI 字段/exit 保持；FF 用原 CLI/wire，不需导入新模块 | PASS | capabilities/`--help` 与基线一致；wire 未变 |
| 出口 5 条（协议/worker/额度/真实资源/安装 CLI 原语言 + 独审） | PASS | §4、§5、§7 |
| 并线步骤 1：最终 tip、精确 diff、包级测试、独审绑定 tip、隔离恢复 | PASS | §1、§4、§5、§6 |

需要说明的执行口径（不是未完成项）：

- C 的 "公开 transcript_tool 命令"：`--capabilities` 用真实 CLI 进程验证；fetch 流程通过 `transcript_tool.main` 的公开参数 + **既有**私有 `_retrieval_launcher` 测试 seam 注入离线 provider。未新增生产 test-only 后门；这是本仓唯一可离线驱动真实 supervisor→worker 链路的既有机制。
- `transcript_preflight.py` 是第四个纯模块，原因是 B4 的字面要求 "HTTP/parser 仅在 worker provider 层加载"：只把纯校验/gate 从 `transcript_api` 拆出，不新增业务能力，`transcript_api` 仍重导出同一对象。

### ET-01 协议层与 provider 实现分离

- 原调用链根因：`retrieval_runtime.py:29` 为 7 个纯值 `from transcript_api import ...`，导入 supervisor 即执行 `transcript_api.py:21-25` 的 `requests`/`bs4`/`transcript_extraction`；`retrieval_worker.py:24` 反向 `from retrieval_runtime import MAX_REQUEST_FILE_BYTES, MAX_RESULT_BYTES, WORKER_PROTOCOL`；`transcript_tool.py` 在 fetch 路径为 preflight 导入 `transcript_api`。
- 先红证据：新进程禁 `transcript_api`/`transcript_extraction`/`requests`/`bs4` 后 `import retrieval_runtime` → `ImportError: provider module blocked: transcript_api`（`evidence/red_et01_supervisor_import.log`）；禁 `retrieval_runtime` 后 `import retrieval_worker` → `ImportError: supervisor module blocked: retrieval_runtime`；`--capabilities` 与 fetch preflight 同样被 provider 导入拦住。
- 修复归属：纯值进 `transcript_contracts.py`/`transcript_provider_settings.py`；wire 与 envelope 规则进 `retrieval_protocol.py`；请求校验/gate 进 `transcript_preflight.py`；`transcript_api` 仅做兼容重导出（同一对象，不复制定义）；supervisor/worker 都只依赖 protocol。
- 保持兼容：`transcript_api.RESULT_SCHEMA` 等 16 个常量、`ProviderSettings`、`DEFAULT_PROVIDER_SETTINGS`、`provider_operation_capability`、`EXCHANGES`、`preflight_transcript_fetch` 全部仍是原对象；`scraper.py` 的 5 个旧导入不变。
- 最终证明：`test_supervisor_imports_without_provider_http_or_parser`、`test_worker_protocol_does_not_import_supervisor`、`test_capabilities_cli_is_provider_import_free`（完整 capability golden）、`test_fetch_preflight_is_provider_import_free`、`test_old_public_contract_imports_preserve_values_and_settings_identity`、`test_explicit_provider_settings_do_not_pollute_the_shared_default`。

### ET-02 worker 结果限额与显式额度同源

- 根因：`retrieval_worker.py:183` 用模块级固定 `MAX_RESULT_BYTES`（实测 `153092096`，146 MiB）决定结果是否写出；父进程 `_result_read_limit` 已按本次 `max_response_bytes`/`max_body_bytes`/`response_bytes_left` 派生，两边不同源。
- 先红证据：把 worker 旧常量缩到 64 字节、喂一个远小于显式额度的合法 `list` 结果 → 基线 **rc=2 且不写 result.json**（`evidence/red_et02_worker_veto.log`）；父进程只能报 `worker_failure`。
- 修复归属：`retrieval_protocol.result_transfer_limit(payload, response_bytes_left)` 成为唯一公式（JSON 转义 6x、base64 4/3、2 MiB 固定协议开销），父读上限与子写上限使用同一函数、同一 payload、同一 `response_bytes_left`；固定常量删除，不搬入新协议。
- 保持兼容：deadline、累计 HTTP/response 预算、unknown usage、清理失败语义未改；真实 response 字节超额仍由 `_BudgetSession` 抛出并映射 `content_too_large`/`byte_limit`。
- 最终证明：`test_worker_result_has_no_fixed_legacy_representation_veto`、`test_result_transfer_limit_follows_explicit_request_quota`、`test_body_only_quota_is_not_capped_by_the_default_response_ceiling`、`test_transfer_limit_accounts_for_base64_and_json_escaping_overhead`、`test_real_response_byte_overrun_is_measured_not_a_result_veto`；既有 runtime/CLI/batch suite 全绿。

### A/B/C 工程步骤

- A：两个新测试文件先红（见上），另用基线脚本复现固定帽拒绝；未把缺依赖/环境问题当产品 RED。
- B：抽取四个纯模块、改边界导入、worker 限额同源、父进程 fetch 路径去掉 provider 依赖；旧公开名兼容。
- C：受影响 suite + 包级 CI；显式 runtime manifest 临时 artifact，空 cwd 跑公开 CLI；独立审查后交付。

无 `NO_CHANGE_PROVEN` 项：两项确认缺口都实际复现并修复。

## 3. 公开接口变化

默认无变化，实测：

- wire schema 字符串、默认额度、`ProviderSettings` 对象身份不变；`transcript_api.preflight_transcript_fetch` 与 `transcript_preflight.preflight_transcript_fetch` 是同一对象。
- `transcript_tool.py` 参数（`--request-stdin`、`--capabilities`、`--operation`、`--allow-download`、`--include-source-payload`、`--report-usage`）、stdout/stderr 结构与 exit code 不变；`--capabilities` 输出与基线 `003c1c0` **逐字节相同**（1597 bytes）。
- 内部 worker envelope 仍为 `et-retrieval-worker/1`，字段不变；新纯模块是内部实现，FF/CWP 不需要导入。
- 新增可选能力：无。ROOT 无需新 fixture；组合阶段继续用原 CLI/wire。

## 4. 实际测试

环境：Python 3.13.9（`C:/Miniconda/python.exe`），pytest 9.1.1，Windows。所有 pytest 运行使用独立 `--basetemp`（本机 safe-delete 策略会拦 pytest 默认临时根的批量清理，不影响产品）。

| # | 命令 | 结果 |
|---|---|---|
| 1 | 卡内受影响 suite（7 个既有 + `test_explicit_response_budget.py` + 2 个新增） | exit 0，**174 passed in 148.90s**，failed 0，skip 0（`evidence/tests_affected.log`） |
| 2 | `python -B -m pytest tests/ -q -p no:cacheprovider`（包级 CI） | exit 1，**662 passed, 1 failed in 236.42s**，skip 0；唯一失败为基线既有 `test_transcript_audit.py::test_write_receipts_never_overwrites_conflicting_receipt`（`evidence/tests_package.log`、`evidence/baseline_preexisting_failure.log`） |
| 3 | runtime artifact（空 cwd，公开 `--capabilities`） | exit 0，stderr 0，输出与基线 `003c1c0` **逐字节相同**（1597 bytes） |
| 4 | runtime artifact（空 cwd，公开 `--request-stdin --include-source-payload --report-usage` + 测试 provider） | exit 0，`fetched` / `/2` / usage_complete=true / 1 request / 862 bytes；模块全部从 artifact 解析；worker 临时根清空（`evidence/runtime_verify.log`） |

RED 证据（基线 `003c1c0`，独立复跑）：`evidence/red_et01_supervisor_import.log`、`evidence/red_et02_worker_veto.log`。

已知失败（非本卡）：`tests/test_transcript_audit.py::test_write_receipts_never_overwrites_conflicting_receipt` 在基线 `main`（未改动 checkout）上同样失败，属既有缺陷，未在本卡修复。

未执行：真实 provider 联网/收费调用（本轮不属默认测试）；wheel 构建（本仓无 wheel/setup）；全局技能部署；400 MB 端到端 fixture（按卡避免数百 MB 文件，用公式断言 + 缩小常量的 RED 复现替代）。

## 5. 独立审查

三轮，均为独立只读 agent，报告见 `review.md`。

- 第一轮（tip `85456ff`）：**BLOCK**，1 个 P1 —— `result_transfer_limit` 在 body-only 请求上把 body 钳到 FMP 16 MiB 默认，重新引入 304 MiB 固定帽；另有 8 条非阻断。
- 修复（`9f362ea`）：无显式响应配额时 `raw = max(MAX_FMP_RESPONSE_BYTES, body)`；补纯公式 RED；处理文档/测试非阻断项。
- 第二轮（tip `3fc129c`）：**APPROVE**，P1 已修复、无新问题；定向快速测试 30 passed。
- 第三轮（preflight 迁移，tip `8e116a4`）：代码 **PASS**（新进程禁 provider 下 `import transcript_tool` + fetch preflight 拒绝 + `--capabilities` 均成功，公开面同一对象，无循环导入，定向测试 88 passed）；同时指出交接清单在代码变更后过期，已在本次文档提交中修正并绑定新 tip。
- 未发现本卡范围内未修复问题；范围外问题见 §4 既有失败。

## 6. 隔离与恢复

- 测试目录：使用新建的 `%TEMP%\mb11-pytest-base-*` / `mb11-pytest-pkg-*` 作为 `--basetemp`（初始为空），运行后已删除；仓库内未新增测试输出。pytest 默认临时根只被读取，不依赖其状态。
- runtime artifact：新建空临时目录（初始清单 `[]`），复制 11 个 runtime 文件，验证后整目录删除；证据见 `evidence/runtime_manifest.json`、`evidence/runtime_verify.log`。
- 未改生产原件/配置/既有 WIP；原仓 `main` checkout 只用于只读基线对比（`--capabilities`、RED 复现、既有失败复核）。
- 网络调用：0；模型/翻译调用：0；费用 unknown：0。
- 未写全局技能目录、未在仓库外新建第二个项目。

## 7. 安装/部署/CI/合并状态

- 安装/部署：无（本仓无安装器，本轮不部署）。
- CI：仅本地包级 pytest，未接远端 CI。
- 合并：未合 `main`，仅交付分支。
- ROOT 下一步（一条可执行命令，输入为本卡分支与 `main`）：

```powershell
git -C 'C:/Users/郑曾波/Projects/earnings-transcripts/earnings-transcripts' fetch origin; git -C 'C:/Users/郑曾波/Projects/earnings-transcripts/earnings-transcripts' log --oneline origin/main..origin/codex/mb11-et
```

在独立集成工区核对 `origin/main` 祖先关系与本卡写集合后，运行第 4 节 #1/#2 命令一次；冲突解决若改源码，测试与独审需绑定最终 tip。

# MB-ET handoff

## 1. 基线与交付

| 项 | 值 |
|---|---|
| 原仓 | `C:/Users/郑曾波/Projects/earnings-transcripts/earnings-transcripts` |
| 基线 commit | `003c1c065109d0b02c7bd4de09ad3e8461a99c0e`（= `origin/main`） |
| 工区 | `C:/Users/郑曾波/Projects/mb11-et` |
| 分支 | `codex/mb11-et` |
| 目标主线 | `main` |
| 代码 commit | `3fc129c`（最终 tip；实现 `99b37cd`，测试增强 `8a00531`/`85456ff`，P1 修复 `9f362ea`，AST 重导出检查 `3fc129c`） |
| 文档 commit | 本文件所在提交（仅 PWF/证据，未改源码） |
| remote | `origin` = `https://github.com/zhengcb81/earnings-transcripts.git` |
| 推送 | 已推送 `origin/codex/mb11-et`（代码 tip `3fc129c` 已先行推送；本文件所在文档提交随后推送同一分支） |

实际 diff 文件表（全部在卡内写集合内）：

| 文件 | 类型 | 内容 |
|---|---|---|
| `transcript_contracts.py` | 新增 | 16 个公共 wire schema/默认额度常量；仅标准库 |
| `transcript_provider_settings.py` | 新增 | `ProviderSettings`、`DEFAULT_PROVIDER_SETTINGS`、`provider_operation_capability`、`EXCHANGES`；仅标准库 |
| `retrieval_protocol.py` | 新增 | worker 协议：`WORKER_PROTOCOL`、请求/结果文件名与上限、envelope 构造/校验、usage 解析、按实际额度派生的 `result_transfer_limit`；仅标准库 + contracts |
| `transcript_api.py` | 修改 | 改为从两个纯模块导入并兼容重导出；provider 行为未改 |
| `retrieval_runtime.py` | 修改 | 只依赖 protocol/settings；删除 `MAX_RESULT_BYTES`、`_result_read_limit`、`_expected_*`、`_parse_usage` |
| `retrieval_worker.py` | 修改 | 不再导入 supervisor；请求读取/结果写出走 protocol；结果写入前用同源派生限额 |
| `transcript_tool.py` | 修改 | capabilities/契约走纯模块；provider 操作函数仅在 preflight/协作 fetch 路径惰性导入 |
| `tests/test_runtime_contract_isolation.py` | 新增 | ET-01 隔离与公开兼容证明（6 项） |
| `tests/test_worker_result_budget_contract.py` | 新增 | ET-02 结果限额正反例（4 项） |
| `tests/test_retrieval_runtime.py` | 修改 | 仅替换已删除常量的引用，并把固定帽测试改为派生限额断言 |
| `tests/test_explicit_response_budget.py` | 修改 | 读上限断言改为与 `result_transfer_limit` 同源比较（该测试直接引用被删除的常量） |
| `docs/plans/mb11-et-2026-11-11/*` | 新增 | 本 PWF、证据与交接 |

未改：`scraper.py`、`transcript_extraction.py`、`legacy_projection_comparison.py`、`translator.py`/`translate.py`、`retrieval_budget.py`、原件/缓存/凭证/配置、全局技能目录、外层 `Projects/earnings-transcripts`。

## 2. 逐项要求

### ET-01 协议层与 provider 实现分离

- 原调用链根因：`retrieval_runtime.py:29` 为 7 个纯值 `from transcript_api import ...`，导入 supervisor 即执行 `transcript_api.py:21-25` 的 `requests`/`bs4`/`transcript_extraction`；`retrieval_worker.py:24` 反向 `from retrieval_runtime import MAX_REQUEST_FILE_BYTES, MAX_RESULT_BYTES, WORKER_PROTOCOL`。
- 先红证据：新进程禁 `transcript_api`/`transcript_extraction`/`requests`/`bs4` 后 `import retrieval_runtime` → `ImportError: provider module blocked: transcript_api`；禁 `retrieval_runtime` 后 `import retrieval_worker` → `ImportError: supervisor module blocked: retrieval_runtime`；`transcript_tool.py --capabilities` 同样被 provider 导入拦住。
- 修复归属：纯值进 `transcript_contracts.py`/`transcript_provider_settings.py`；wire 与 envelope 规则进 `retrieval_protocol.py`；`transcript_api` 仅做兼容重导出（同一对象，不复制定义）；supervisor/worker 都只依赖 protocol。
- 保持兼容：`transcript_api.RESULT_SCHEMA` 等 16 个常量、`ProviderSettings`、`DEFAULT_PROVIDER_SETTINGS`、`provider_operation_capability`、`EXCHANGES` 全部仍是原对象；`scraper.py` 的 5 个旧导入不变。
- 最终证明：`test_supervisor_imports_without_provider_http_or_parser`、`test_worker_protocol_does_not_import_supervisor`、`test_capabilities_cli_is_provider_import_free`（含完整 capability golden 对比）、`test_old_public_contract_imports_preserve_values_and_settings_identity`、`test_explicit_provider_settings_do_not_pollute_the_shared_default`。

### ET-02 worker 结果限额与显式额度同源

- 根因：`retrieval_worker.py:183` 用模块级固定 `MAX_RESULT_BYTES`（实测 `153092096`，146 MiB）决定结果是否写出；父进程 `_result_read_limit` 已按本次 `max_response_bytes`/`max_body_bytes`/`response_bytes_left` 派生，两边不同源。
- 先红证据：把 worker 旧常量缩到 64 字节、喂一个远小于显式额度的合法 `list` 结果 → 基线 **rc=2 且不写 result.json**；父进程只能报 `worker_failure`。
- 修复归属：`retrieval_protocol.result_transfer_limit(payload, response_bytes_left)` 成为唯一公式（JSON 转义 6x、base64 4/3、2 MiB 固定协议开销），父读上限与子写上限使用同一函数、同一 payload、同一 `response_bytes_left`；固定常量删除，不搬入新协议。
- 保持兼容：deadline、累计 HTTP/response 预算、unknown usage、清理失败语义未改；真实 response 字节超额仍由 `_BudgetSession` 抛出并映射 `content_too_large`/`byte_limit`。
- 最终证明：`test_worker_result_has_no_fixed_legacy_representation_veto`、`test_result_transfer_limit_follows_explicit_request_quota`、`test_transfer_limit_accounts_for_base64_and_json_escaping_overhead`、`test_real_response_byte_overrun_is_measured_not_a_result_veto`；既有 runtime/CLI/batch suite 全绿。

### A/B/C 工程步骤

- A：两个新测试文件先红（见上），另用一次性脚本复现固定帽拒绝；未把缺依赖/环境问题当产品 RED。
- B：抽取三个纯模块、改四处边界导入、worker 限额同源；旧公开名兼容。
- C：受影响 suite + 包级 CI；显式 runtime manifest 临时 artifact，空 cwd 跑公开 CLI；独立审查后交付。

无 `NO_CHANGE_PROVEN` 项：两项确认缺口都实际复现并修复。

## 3. 公开接口变化

默认无变化，实测：

- wire schema 字符串、默认额度、`ProviderSettings` 对象身份不变。
- `transcript_tool.py` 参数（`--request-stdin`、`--capabilities`、`--operation`、`--allow-download`、`--include-source-payload`、`--report-usage`）、stdout/stderr 结构与 exit code 不变；`--capabilities` 输出与基线 `003c1c0` **逐字节相同**（1597 bytes）。
- 内部 worker envelope 仍为 `et-retrieval-worker/1`，字段不变；新纯模块是内部实现，FF/CWP 不需要导入。
- 新增可选能力：无。ROOT 无需新 fixture；组合阶段继续用原 CLI/wire。

## 4. 实际测试

环境：Python 3.13.9（`C:/Miniconda/python.exe`），pytest 9.1.1，Windows。

| # | 命令 | 结果 |
|---|---|---|
| 1 | 卡内受影响 suite（7 个既有 + `test_explicit_response_budget.py` + 2 个新增） | **173 passed in 111.89s**，exit 0（`evidence/tests_affected.log`） |
| 2 | `python -B -m pytest tests/ -q -p no:cacheprovider`（包级 CI） | **661 passed, 1 failed in 195.68s**，exit 1；唯一失败为基线既有 `test_transcript_audit.py::test_write_receipts_never_overwrites_conflicting_receipt`（`evidence/tests_package.log`、`evidence/baseline_preexisting_failure.log`） |
| 3 | runtime artifact（空 cwd，公开 `--capabilities`） | exit 0，stderr 0，输出与基线 `003c1c0` **逐字节相同**（1597 bytes） |
| 4 | runtime artifact（空 cwd，公开 `--request-stdin --include-source-payload --report-usage` + 测试 provider） | exit 0，`fetched` / `/2` / usage_complete=true / 1 request / 862 bytes；模块全部从 artifact 解析；worker 临时根清空（`evidence/runtime_verify.log`） |

已知失败（非本卡）：`tests/test_transcript_audit.py::test_write_receipts_never_overwrites_conflicting_receipt` 在基线 `main`（未改动 checkout）上同样失败，属既有缺陷，未在本卡修复。

未执行：真实 provider 联网/收费调用（本轮不属默认测试）；wheel 构建（本仓无 wheel/setup）；全局技能部署。

## 5. 独立审查

两轮，均为独立只读 agent（`Explore-1`），报告见 `review.md`。

- 第一轮（tip `85456ff`）：**BLOCK**，1 个 P1 —— `result_transfer_limit` 在 body-only 请求上把 body 钳到 FMP 16 MiB 默认，重新引入 304 MiB 固定帽；另有 8 条非阻断。
- 修复（`9f362ea`）：无显式响应配额时 `raw = max(MAX_FMP_RESPONSE_BYTES, body)`；补纯公式 RED；处理文档/测试非阻断项。
- 第二轮（tip `3fc129c`）：**APPROVE**，P1 已修复、无新问题；定向快速测试 30 passed。
- 未发现本卡范围内未修复问题；范围外问题见第 4 节既有失败。

## 6. 隔离与恢复

- 测试全部使用 pytest `tmp_path` 新临时目录；仓库内未新增测试输出。
- runtime artifact 使用新建空临时目录（初始清单为空），结束后整目录删除；证据见 `evidence/runtime_manifest.json`、`evidence/runtime_verify.log`。
- 未改生产原件/配置/既有 WIP；原仓 `main` checkout 只用于只读基线对比（`--capabilities` 与失败测试复核）。
- 网络调用：0；模型/翻译调用：0；费用 unknown：0。
- 未写全局技能目录、未在仓库外新建第二个项目。

## 7. 安装/部署/CI/合并状态

- 安装/部署：无（本仓无安装器，本轮不部署）。
- CI：仅本地包级 pytest，未接远端 CI。
- 合并：未合 `main`，仅交付分支。
- ROOT 下一步（一条可执行命令，输入为本卡分支与 `main`）：

```powershell
git -C 'C:/Users/郑曾波/Projects/earnings-transcripts/earnings-transcripts' fetch origin
git -C 'C:/Users/郑曾波/Projects/earnings-transcripts/earnings-transcripts' log --oneline origin/main..origin/codex/mb11-et
```

在独立集成工区核对 `origin/main` 祖先关系与本卡写集合后，运行第 4 节 #1/#2 命令一次；冲突解决若改源码，测试与独审需绑定最终 tip。

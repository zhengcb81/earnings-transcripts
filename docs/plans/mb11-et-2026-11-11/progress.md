# MB-ET progress

按时间顺序追加。命令均在 `C:/Users/郑曾波/Projects/mb11-et` 下执行，Python 为 `C:/Miniconda/python.exe`。

## 2026-11-12

1. 只读核卡：`inputs/MB-ET.md` + `interfaces.md` + `acceptance.md` + `runtime_verification.md` + `handoff_contract.md` + `review/acquisition_architecture.md`（ET 段）。
2. 核对原仓：`main` = `origin/main` = `003c1c0`，工作区仅有未跟踪 `.workbuddy-ai/`（不动）。
3. 建工区：`git worktree add -b codex/mb11-et C:/Users/郑曾波/Projects/mb11-et 003c1c0` → HEAD `003c1c0`。
4. 基线测试：`python -B -m pytest tests/test_retrieval_runtime.py tests/test_retrieval_cli_e2e.py tests/test_launch_capability_contract.py tests/test_provider_cost_capability.py tests/test_request_response_budget.py tests/test_batch_runtime.py tests/test_optional_local_receipt.py -q -p no:cacheprovider` → **143 passed in 149.63s**。
5. 缺口实测（见 findings.md）：导入 supervisor 会加载 requests/bs4/transcript_extraction；worker 反向导入 supervisor；worker 固定 `MAX_RESULT_BYTES=153092096`。
6. A（RED）：
   - `tests/test_runtime_contract_isolation.py` 5 项全红，根因分别为 `ImportError: provider module blocked: transcript_api`（supervisor）、`ImportError: supervisor module blocked: retrieval_runtime`（worker）、`ImportError: provider module blocked: transcript_api`（capabilities CLI）、`ModuleNotFoundError: transcript_contracts`、`ModuleNotFoundError: retrieval_protocol`。
   - `tests/test_worker_result_budget_contract.py` 无法收集（`retrieval_protocol` 缺失）；另用一次性脚本把 worker 旧常量缩到 64 字节复现固定表示帽：合法结果 **rc=2、result.json 未写出**。
7. B（GREEN）：
   - 新增 `transcript_contracts.py`、`transcript_provider_settings.py`、`retrieval_protocol.py`（均仅标准库；protocol 另依赖 contracts）。
   - `transcript_api.py` 改为兼容重导出（对象同一性由测试锁定）；`retrieval_runtime.py` 只依赖 protocol/settings，删除 `MAX_RESULT_BYTES`/`_result_read_limit`；`retrieval_worker.py` 不再导入 supervisor，结果写入改用 `result_within_limit`；`transcript_tool.py` 的 `--capabilities` 不再导入 provider，fetch/preflight 路径惰性导入。
   - 迁移相关断言：`tests/test_retrieval_runtime.py` 的固定常量测试改为「无固定帽 + 限额随显式额度增长」；`tests/test_explicit_response_budget.py` 的读上限断言改为与 `result_transfer_limit` 同源比较。
8. 受影响 suite：`tests/test_retrieval_runtime.py test_retrieval_cli_e2e.py test_launch_capability_contract.py test_provider_cost_capability.py test_request_response_budget.py test_batch_runtime.py test_optional_local_receipt.py test_explicit_response_budget.py test_runtime_contract_isolation.py test_worker_result_budget_contract.py` → **171 passed / 123.87s**。
9. 包级全量 `tests/`：**659 passed, 1 failed**。唯一失败 `test_transcript_audit.py::test_write_receipts_never_overwrites_conflicting_receipt` 在基线 `main`（未改动仓）上同样失败 → 既有缺陷，非本卡引入，列入 out_of_scope。
10. C（runtime artifact）：按 `inputs/runtime_verification.md` 建显式 manifest（10 文件 / 138172 字节），复制到新临时 artifact；空 cwd 跑 `transcript_tool.py --capabilities`（rc=0，输出与基线逐字节相同）与公开 `--request-stdin --include-source-payload --report-usage` 流程（测试 provider，rc=0，status=fetched，schema /2，usage_complete=true，requests_used=1，模块全部从 artifact 解析，worker 临时根清空）。证据：`evidence/runtime_manifest.json`、`evidence/runtime_verify.log`；临时目录已完整删除。
11. 代码提交：`99b37cd`（主实现）；`8a00531`（测试增强：capabilities 完整 golden、settings 不污染默认值、runtime 重导出注释）。工作区只剩待提交的 PWF/证据。
12. 基线既有失败证据：`evidence/baseline_preexisting_failure.log`（在原仓 `main` = `003c1c0` 上 rc=1，同一测试）。
13. 独立审查（第一轮，tip `85456ff`）：**BLOCK**，1 个 P1 —— `result_transfer_limit` 在 body-only 请求上把 body 钳到 FMP 16MiB 默认，重新引入 304MiB 固定帽（Motley Fool 实际 read cap 是 `max_body_bytes`）。另有 8 条非阻断。详见 `review.md`。
14. 修复 P1（`9f362ea`）：无显式响应配额时 `raw = max(MAX_FMP_RESPONSE_BYTES, body)`；新增 `test_body_only_quota_is_not_capped_by_the_default_response_ceiling`（旧公式必红：304MiB < 6*400MB）。处理非阻断：runtime 日志 source commit、独立公式断言、AST 无第二份定义检查（`3fc129c`）。
15. 第二轮复核（tip `3fc129c`）：**APPROVE**，P1 已修复、无新问题；定向快速测试 30 passed。
16. 最终测试（tip `3fc129c`）：受影响 suite **173 passed in 111.89s**（`evidence/tests_affected.log`）；包级 `tests/` **661 passed, 1 failed in 195.68s**（`evidence/tests_package.log`，唯一失败为基线既有 audit 测试，见 `evidence/baseline_preexisting_failure.log`）。
17. 最终 runtime artifact（tip `3fc129c`）：10 文件 / 138671 字节；空 cwd `--capabilities` exit 0、与基线逐字节相同（1597 bytes）；公开 `--request-stdin --include-source-payload --report-usage` + 测试 provider → fetched / `/2` / usage_complete=true / 1 request / 862 bytes；模块全部从 artifact 解析；临时目录完整删除。证据已更新 `evidence/runtime_manifest.json`、`evidence/runtime_verify.log`。
18. 交付文档：`handoff.md`、`handoff.json`、`review.md` 与证据索引已回填；推送分支待执行。
19. 逐条对照复核发现 B4 字面缺口：`transcript_tool` 的 fetch preflight 仍在父进程导入 `transcript_api`（连带 requests/bs4）。新增纯模块 `transcript_preflight.py`，把 `_validate_request`/`_provider_gate`/`_base_result`/`preflight_transcript_fetch`/`_InvalidRequest`/`_TICKER_RE` 迁出，`transcript_api` 兼容重导出；`transcript_tool` 改从纯模块导入。新增 `test_fetch_preflight_is_provider_import_free`；`test_provider_cost_capability.py` 的 patch 点随 gate 迁移。提交 `60b1050`，清理 `8e116a4`。
20. 第三轮独立复核（tip `8e116a4`）：preflight 迁移代码 **PASS**（禁 provider 下 fetch preflight/`--capabilities` 均成功，公开面同一对象，无循环导入，定向 88 passed）；指出交接清单在代码变更后过期，已在最终文档提交修正。
21. 最终测试（tip `8e116a4`，`--basetemp` 新目录）：受影响 suite **174 passed in 148.90s**（`evidence/tests_affected.log`）；包级 `tests/` **662 passed, 1 failed in 236.42s**（`evidence/tests_package.log`，唯一失败为基线既有 audit 测试）。
22. 最终 runtime artifact（tip `8e116a4`）：11 文件 / 139524 字节；空 cwd `--capabilities` exit 0、与基线逐字节相同；公开 fetch 流程 fetched / `/2` / usage_complete=true / 1 request / 862 bytes；`transcript_preflight` 从 artifact 解析；临时目录删除。





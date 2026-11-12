# MB-ET 独立审查

审查对象：`C:/Users/郑曾波/Projects/mb11-et`，分支 `codex/mb11-et`，基线 `003c1c0`。
审查方式：只读 agent（Explore-1），先读卡与公共接口，再实际执行 import guard、diff 范围核对与测试。

## 第一轮（审查 tip `85456ff`，结论 BLOCK）

审查 agent：`Explore-1`（独立只读，非作者）。审查时间：2026-11-12 19:16–19:44。

### 阻断问题（P1）

**P1-1 `result_transfer_limit` 在 body-only 请求上重新引入固定帽**

- 位置：`retrieval_protocol.py` 的 `result_transfer_limit`。
- 现象：请求只给 `max_body_bytes`、没有 `max_response_bytes` 时，`raw` 回落到 `MAX_FMP_RESPONSE_BYTES`（16 MiB），且 `body = min(body, raw)` 把 body 钳到 16 MiB，派生上限固定为 `6*(2*16MiB+16MiB)+2MiB = 304 MiB`。
- 为什么是缺陷：Motley Fool 的实际读取上限是 `max_body_bytes`（`transcript_api._body_response_cap`），不是 16 MiB。显式 body 配额更大时（例如 400 MB），worker 的合法结果可超过 304 MiB，会被这个新固定帽拒绝——与 ET-02 要修的是同一类缺陷，只是从 worker 常量搬到了公式里。
- 审查建议：无显式响应配额时不要用 FMP 默认钳 body；`raw` 至少取 `max(MAX_FMP_RESPONSE_BYTES, body)` 或按 provider 实际 read cap；补 worker 级测试。

### 非阻断发现

1. `evidence/runtime_verify.log` 的 source commit 写成基线 `003c1c0`，与 `runtime_manifest.json` 的 `99b37cd` 不一致（文档一致性）。
2. manifest 绑定 `99b37cd`，不是最终 tip。
3. `handoff.md` 的 `TBD_*` 占位需填。
4. `progress.md` 的 "171 passed" 需更新为最终数。
5. `tests/test_explicit_response_budget.py` 的上限断言是 `result_transfer_limit` 的镜像，建议加独立断言。
6. capabilities golden 由 `provider_operation_capability` 现算，属自引用；更强可冻结字面 golden。
7. 字符串常量用 `is` 比较在 CPython 下可能因驻留恒真，建议 `==` 或比对 `__dict__`。
8. `test_worker_result_has_no_fixed_legacy_representation_veto` 只断言 `hasattr` 消失，未直接证明 worker 用了新公式（审查认为已有其他测试覆盖，非阻断）。

### 审查未覆盖

- 审查 agent 未给出完整的逐条 requirement 表；其在收敛前被作者停止（运行 28 分钟后仍在重复跑测试）。已完成的检查：diff 范围、依赖方向、ET-02 公式推演、P1 复现推演。

## 修复（作者，commit `9f362ea`）

- `result_transfer_limit`：无显式响应配额时 `raw = max(MAX_FMP_RESPONSE_BYTES, body)`，不再把 body 钳到 FMP 默认；有显式配额时保持 `raw=min(declared, left)`、`body=min(body, raw)`。
- 新增 `test_body_only_quota_is_not_capped_by_the_default_response_ceiling`：`max_body_bytes=400MB` 时上限必须 ≥ `6*400MB`（旧公式为 304MiB，必红）。
- 非阻断 1/2/5/7 已处理：
  - runtime 日志 source commit 改为 `99b37cd` 并注明哈希形态；
  - `test_explicit_response_budget.py` 增加独立公式断言；
  - 常量重导出测试增加 AST 检查（`transcript_api` 不得再赋值这些名字），避免字符串驻留导致的假绿。
- 非阻断 3/4 在最终 handoff/progress 中回填；6 保留现算 golden（跨进程对比 + 证据中的逐字节基线对比已覆盖），8 由 `test_real_response_byte_overrun...` 与 worker 写入路径共同覆盖。

### 关于审查建议的 worker 级测试

按建议用缩小常量 + `max_body_bytes=4096` 无法复现旧公式：旧公式硬编码 2 MiB 协议开销，其最小上限仍约 2.1 MiB，而 body=4096 的合法结果只有几 KB，旧公式不会拒绝（作者实测 rc=0）。要真正复现，需要 body > 16 MiB 且合法结果 > 304 MiB（约 40–50 MB body 起步），与卡内"测试避免数百 MB 文件"冲突。因此采用纯公式断言作为诚实 RED：`result_transfer_limit({"max_body_bytes": 400_000_000}, None)` 在旧公式下为 304 MiB、新公式下为 7.2 GiB，能稳定区分；worker 使用该函数由 `test_worker_result_has_no_fixed_legacy_representation_veto`（缩小旧常量后仍写出结果）证明。

## 第二轮复核（审查 tip `3fc129c`，结论 APPROVE）

复核 agent：`Explore-1`（新的只读子任务，聚焦 P1 修复；未跑全量，仅定向验证）。时间：2026-11-12 19:45–19:47。

- **P1 已修复**：`raw = max(MAX_FMP_RESPONSE_BYTES, body)`（无显式响应配额时）；`max_body_bytes=400MB` 时上限 7.2 GiB ≥ `6*400MB`；`max_response_bytes` 存在时仍 `raw=min(declared,left)`、`body=min(body,raw)`，语义不变；Motley Fool 与 FMP 两种 read cap 都不再被低估。
- 未发现新问题。
- 快速测试（`test_worker_result_budget_contract.py`、`test_runtime_contract_isolation.py`、`test_explicit_response_budget.py`）：**30 passed**。
- 复核时指出的剩余文档项：runtime 证据仍绑定 `99b37cd`（作者已在复核后重跑到 `3fc129c`）；`handoff.md`/`progress.md` 的 TBD 与 171 需回填。二者均已在最终文档提交中处理。
- 非阻断备注：无效 `max_body_bytes`（非 int/负数）在有显式配额时由旧的 `body=raw` 变为 `body=min(10MiB, raw)`；该输入被 `_validate_request` 拒绝，不可达，方向更紧，接受。

## 第三轮复核（preflight 迁移，审查 tip `60b1050`/`8e116a4`，结论：代码 PASS，交接清单需回填）

第三轮 agent：`mb-et-auditor`（Explore，只读，agent-56b829fd-08a4-44b5-b540-9ff2aff7c6bc）。触发原因：第二轮后作者为满足 B4 的字面要求（HTTP/parser 仅在 worker provider 层加载），新增 `transcript_preflight.py` 把纯校验/gate 从 `transcript_api` 拆出。

- **代码 PASS**：`3fc129c..60b1050` 只改 5 个文件（`transcript_api.py`、`transcript_preflight.py`、`transcript_tool.py`、两个测试），无其它运行逻辑改动；`_TICKER_RE`/`_InvalidRequest`/`_validate_request`/`_base_result`/`_provider_gate` 只在 preflight 定义，`transcript_api` 导入同一对象；无循环导入。
- 新进程禁 `transcript_api`/`transcript_extraction`/`requests`/`bs4`：`import transcript_tool` 成功；FMP+HKEX fetch 请求 → rc=0、`unsupported_market`、`blocked_loaded=[]`；`--capabilities` rc=0、输出不变。
- 公开面：16 个常量、settings、`provider_operation_capability`、`preflight_transcript_fetch` 均为同一对象；5 个内部名仍可从 `transcript_api` 访问。
- 定向测试：88 passed in 9.40s。
- 未引入新问题；唯一语义细节是 `test_provider_cost_capability.py` 的 patch 点随 gate 迁移到 `transcript_preflight`（已同步修改，属边界相关）。
- 交接清单 GAP（已在最终文档提交修正）：`handoff.json.commit`/`pushed`/`changed_paths` 未绑定 `8e116a4`、diff 表与测试数字过期、B4 行仍写旧口径、`reviewed_code_commit` 未绑定新 tip、`evidence/INDEX.md` 与 RED 日志未入库。

## 结论

- 阻断问题 0；最终代码 tip `8e116a4` 通过第三轮复核（preflight 迁移代码 PASS，交接清单已在文档提交中回填）。
- 交付状态：REVIEWED（作者已推送分支；未合 main，合并由 ROOT 执行）。



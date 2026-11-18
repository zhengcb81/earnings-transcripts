# FB-ET 交接 · 电话会采集反馈与离线复现

本仓（`earnings-transcripts`）按 [FB-ET 施工卡](FB-ET.md) 完成的交付。ROOT 负责派工、跨仓原场景复验与公司池调度；本仓只交本仓能证明的事实。

## 一句话结论

请求可解析的一次电话会取数现在会留下五条可跨仓比较的观察（动作开始，以及业务状态词、期次归属、内容引用、原生用量四项结果检查；非法 JSON 与超大请求路径无开始记录、只有四项检查），本仓同时确认并修复了一个真实 ET 缺陷（`--report-usage` 在三条零 provider 请求的终态路径不发布原生用量收据），并交付了具名离线回放、幂等问题卡、修理技能与三轮独立审查收口。

## 开工状态与写集合

- **开工基线**：HEAD `68836237be8000f77ba51da0f28e35e8838e63b7`（卡面写 `50d9190`，是 `6883623` 的父提交；dispatch 表的首发版本为 `main@6883623`，以实际 HEAD 为准）。
- **开工时 `git status --porcelain`**：仅 `?? .mimosa/`、`?? .workbuddy-ai/`（卡片声明的已有本机状态，全程保留、未提交）。
- **本卡写集合**（`git diff --name-status 6883623..79d3563`）：29 个文件 = **26 新增 + 3 修改**；修改的三个是 `transcript_tool.py`（入口接线 + 收据边界 + 单写者机制）、`README.md`（协议章节）、`task_plan.md`（本仓状态入口）。产品代码中未触碰 `transcript_api.py`、`retrieval_runtime.py`、`scraper.py`、`transcript_audit.py` 与任何 golden（`--write` 从未运行）。
- **提交序列**：`base..HEAD` 共 **13** 个提交，全部经正常提交钩子（无 `--no-verify`）；本交接是其后的收据提交。

## 交付物与所在

| 交付物 | 位置 | 说明 |
|---|---|---|
| 反馈投影 | [`transcript_feedback.py`](../../../transcript_feedback.py) | `feedback-observation/1` 观察的生成、去重、脱敏与写入；stdlib-only，不导入 provider 实现 |
| 入口接线 | [`transcript_tool.py`](../../../transcript_tool.py) | **公开 CLI 的**每条取数终态路径构造原生用量收据并按 `--report-usage` 发布；启动 worker 时清除反馈环境量（单写者机制）；运行结束后投影观察。stdout/stderr/退出码/结果不变 |
| 离线回归 | [`tests/test_feedback_observation.py`](../../../tests/test_feedback_observation.py) | **73 项**，含 38 组「已发布状态词×具名原因不得报偏差」的源码派生守卫 |
| 具名回放入口 | [`tools/replay_feedback_case.py`](../../../tools/replay_feedback_case.py) | 9 个具名 fixture；假 HTTP/本机回环，时钟固定到生产 golden 同一时刻，逐字节可复现；永不联网 |
| 问题卡导入 | [`tools/feedback_issue_cards.py`](../../../tools/feedback_issue_cards.py) | 一因一卡；重复导入零新增；坏行单列 `damaged_lines` 不中断批次 |
| 修理技能 | [`skills/repair-earnings-transcripts-issue/SKILL.md`](../../../skills/repair-earnings-transcripts-issue/SKILL.md) | 一次处理一个根因：核事实 → 公开入口复现 → 同类入口与合法对照 → 四类归因 → 先写 RED → 修共同责任层 → 定向 GREEN → 原场景复验 → 记录版本/用例/消费端状态 → 同版本同输入不循环重跑 |
| 问题卡 | [`issues/`](issues/) | ET-FB-002（本仓确认的缺陷，已修、`verified`）、ET-FB-001（期次归属的受控演练，未见生产发生，`open`） |
| 收据 | [`receipts/`](receipts/) | 三份 RED 收据：适配器缺失、用量收据缺失（基线前后对比）、合法 discovery 被误报为偏差 |
| 样本 | [`samples/`](samples/) | 交 ROOT 的 6 份脱敏 sidecar（见下） |

## 本仓确认并修复的 ET 缺陷

`--report-usage` 承诺在 stderr 上给出恰好一条原生最终用量收据。基线 `6883623` 上，收据在**三处**发布（preflight 拒绝路径、凭据错误路径、supervisor 结果），而另外**三条**同样零 provider 请求的终态路径（非法 JSON、超大请求、带 source payload 的 discovery）不发布。消费端（filing-fetch 按 `earnings-retrieval-usage/1` 对账用量）因此无法区分「本次没有调用 provider」与「工具没来得及报告」。修复后**公开 CLI 的**每条取数终态路径都发布恰好一条收据；结果 schema、退出码、收据形状与「不重试」承诺均不变。

复现与修复前后的对比：[`receipts/red_missing_usage_receipt.txt`](receipts/red_missing_usage_receipt.txt)；登记与修复状态：[`issues/transcript-usage-receipt.md`](issues/transcript-usage-receipt.md)。

## 观察协议

一行一个独立 JSON 对象。必填 `schema_version`、`owner`、`run_id`、`attempt_id`、`step_id`、`check_id`、`expected`、`actual`；可选 `issue_key`、`evidence_ref`、`details_ref`、`version`、`parent_step_id`、`disposition`、`origin_owner`。

- `version` = **被观察运行的产品版本**（`earnings-transcripts-transcript-tool/1.0.0`）；明细里的 `projection_version` 才是投影自身版本；被测代码 SHA 见本交接的 `reviewed_commit`。
- `evidence_ref` = 本次运行的公开证据（本仓不透明标识：回放为 `et-case:<fixture>`，环境驱动运行为原生 `request_id`）。
- `details_ref` = 脱敏明细记录的标识；明细在 sidecar 的同名 `*.details.jsonl` 兄弟文件里。
- `disposition` 只取 `expected` / `deviation` / `unknown`；`unknown` 表示只读对账（未知用量不重开单）。
- `parent_step_id` 与 `origin_owner` 协议支持但**本仓运行不产生**：ET 自己的取数没有上游步骤，观察的 `owner` 恒为 `earnings-transcripts`（无 origin_owner 差异）。

**观察流**：请求可解析时先写 `action-started`（在 provider 调用之前，中断即留下无终态的开始记录 → 终态保持未知），再写四项结果检查；请求不可解析的路径（非法 JSON、超大请求）没有开始记录，只有四项检查。

| check_id | 比较什么 |
|---|---|
| `outcome-naming` | 业务状态词对已发布的具名原因（`REASON_STATUS` 表；词表由 AST 扫描产品源码的测试守卫） |
| `exact-period-ownership` | 请求期次对原生 provider 响应里重新推出的期次；只有原生字节能证明时才允许归属 |
| `content-reference` | 声明的 `provider_payload_sha256` 对返回的原始字节 |
| `usage-accounting` | 原生最终用量对本次真正发布的收据 |

合法终态一律记正常处置：38 组「状态词 × 具名原因」全部被守卫断言为 `expected`，含 402 权益不足、错期文档、查无目标期次、discovery 成功、限流、缺凭据与未知用量；三轮独审各自独立生成合法场景复核为 **0 偏差、0 issue_key**。真正的产品偏差（业务把错期文档标为 `fetched` 且退出 0）由 `exact-period-ownership` 报为 `deviation` 并带 `issue_key`。

**单写者机制**（卡片规定）：父进程在启动 worker 期间从环境弹出 `RESEARCH_FEEDBACK_OUT/RUN_ID/ATTEMPT_ID`，结束后还原自己的值——supervisor 启动子进程时复制 `os.environ`，因此子进程拿不到 sidecar 路径。RED 实测：摘掉该机制后子进程确实继承到全部三个值；恢复后子进程三项全无（由**在 worker 进程内运行**的 launcher 记录实证），父进程仍是唯一写者。worker 侧从不 import `transcript_feedback`（构造层的第二重保证）。

## 验证与数字（全部在最终源码 `79d3563f2d733a2025e5aa39926d77457012ef8f` 上）

| 命令 | 结果 |
|---|---|
| `C:/Miniconda/python.exe -B -m pytest -q tests/test_feedback_observation.py tests/test_retrieval_cli_e2e.py tests/test_worker_result_budget_contract.py tests/test_optional_local_receipt.py -p no:cacheprovider --basetemp <fresh>` | **99 passed**（0 failed、0 skipped；新文件 73 + 其余三文件 26） |
| `C:/Miniconda/python.exe -B tests/generate_transcript_goldens.py --check` | **10 goldens matched**（exit 0；`--write` 从未运行） |
| `C:/Miniconda/python.exe -B -m pytest -q tests/ -p no:cacheprovider --basetemp <fresh>` | **1 failed, 735 passed**；唯一失败为基线 `6883623` 既有的 `tests/test_transcript_audit.py::test_write_receipts_never_overwrites_conflicting_receipt`（已在基线独立复证），**无新增失败、无 skip** |

定向组合断言覆盖：具体内容、调用次数、单条 wire、逐字节可复现与收尾清理；本卡新文件无任何 skip/xfail 标记，整包运行亦无 skipped 计数。

**交 ROOT 的 6 份脱敏 sidecar 样本**（`samples/`，各配 `*.details.jsonl`；从新进程回放逐字节复现）：

1. `success.jsonl` — 合法成功（fetched，四项检查 `expected`，用量 `unknown` 因合作式缝无 supervisor 收据）
2. `entitlement-402.jsonl` — 预期 402 权益不足（`expected`，零偏差）
3. `unknown-usage.jsonl` — worker 中止（`usage_complete:false`、`usage:null`、provider 至多 1 次请求、处置 `unknown`）
4. `deviation-msft-2026q3.jsonl` — 已判偏差（期次归属，MSFT/2026-Q3）
5. `deviation-aapl-2025q2.jsonl` — 同一 `issue_key` 的第二家公司/期次（AAPL/2025-Q2）
6. `deviation-usage-receipt.jsonl` — 已判偏差（用量收据缺失，复现修复前发布行为）

## 独立审查记录

| 轮次 | 被审 SHA | 判定 | 处置 |
|---|---|---|---|
| round-1（独立 agent，未告知作者预期结论） | `7ecacb2` | PASS WITH FINDINGS（12 项，含 2 major：默认 schema 下 `content-reference` 被静默丢弃、交接未完成） | 全部关闭于 `2bf7c89` |
| round-2 | `2bf7c89` → 复核 `1934313` | PASS WITH FINDINGS（N1–N9；N1 为「审查中途 tip 移动」的流程发现） | N2–N9 关闭于 `951aa5a`；N1 以冻结 SHA 流程关闭 |
| round-3 | `951aa5a` | PASS WITH FINDINGS（N2–N9 复核关闭 + F1–F4 文档级） | F1 关闭于 `f4be8c3`（同轮补上自查发现的单写者机制）；F-a/F-b 关闭于 `79d3563` |
| delta 复核 | `f4be8c3` | PASS WITH FINDINGS（机制/RED/三命令独立复算通过；剩两条 low 措辞） | 两条措辞按审查开的方子逐字修于 `79d3563` |
| **最终判定** | **`79d3563`** | **PASS**（三命令独立复算 99 / 10 / 735 与自报一致；无「必须先修」项） | — |

审查均以执行为准（基线用 `git archive` 只读导出复跑、audit hook 验零网络、敏感性注入验证守卫区分力），未获知作者预期结论；仓库在审查期间未被审查方修改。

## 本仓不能证明的 / 已知边界

- **真实 FMP 账户 entitlement 与留存能力**：全部证据为固定夹具与假 HTTP，本仓没有付费账户事实。
- **真实线上 Motley Fool DOM 抽取适应性**：仍属 11-14 登记项，本卡未触碰。
- **跨仓行为**：消费端是否真的按 `usage-accounting` 对账、是否接受一张错期卡——归 ROOT 与消费端 owner；ET-FB-002 的消费端复验 `state: pending`（需在 ≥`59fc207`、含 `79d3563` 的版本上重跑原场景），ET 不能替消费端关单。
- **N8 私有测试缝边界**：`_session_factory` 协作式测试缝在 `--report-usage` 下不发布收据（公开 CLI 不可达：keyword-only 参数、无 flag/env 可选），与基线一致，非公开合同缺口。
- **成功明细轮转**：明细写在宿主指定的输出路径旁（每次调用两份有界记录）；删除/轮转属宿主对该路径的运维策略，本仓不删除宿主文件。若要求本仓实施轮转，另开卡。
- **`scraper.py`** 也直接调 `run_retrieval` 但零 feedback 接线（零写者，不变量平凡成立）；卡片明文「入口接线限 `transcript_tool.py`」，若日后给 scraper 接 feedback 必须补同款环境清除。
- **基线既有失败**（卡外）：`tests/test_transcript_audit.py::test_write_receipts_never_overwrites_conflicting_receipt`，归原 ET 业务线的 `transcript_audit.py --write-receipts` 退出码缺陷，其写集合与本卡互不重叠。

## 交接 JSON

~~~json
{"lane":"FB-ET","owner":"earnings-transcripts","base":"68836237be8000f77ba51da0f28e35e8838e63b7","reviewed_commit":"79d3563f2d733a2025e5aa39926d77457012ef8f","published_commit":"已发布：origin/main 由基线 6883623 fast-forward 发布，发布检查点 e8f0b9703617da80d269560d2987f0d8bde9da47（承载本交接首次发布的提交，已用 git ls-remote origin refs/heads/main 核对一致）；本回填是发布后的收据增量，仅改本文件的 published_commit 字段，最终远端 tip 以 git ls-remote origin refs/heads/main 为准","status":"完成：卡片三个实施节点、修理技能、离线回归、三轮独立审查与最终 PASS 判定均已在本仓交付；余项均为跨仓复验（归 ROOT/消费端 owner）与已登记的既有项，见 remaining","tests":[{"command":"C:/Miniconda/python.exe -B -m pytest -q tests/test_feedback_observation.py tests/test_retrieval_cli_e2e.py tests/test_worker_result_budget_contract.py tests/test_optional_local_receipt.py -p no:cacheprovider --basetemp <fresh>","result":"99 passed（0 failed, 0 skipped；新文件 73 + 其余 26）"},{"command":"C:/Miniconda/python.exe -B tests/generate_transcript_goldens.py --check","result":"10 goldens matched（exit 0；--write 从未运行）"},{"command":"C:/Miniconda/python.exe -B -m pytest -q tests/ -p no:cacheprovider --basetemp <fresh>","result":"1 failed, 735 passed；唯一失败为基线 6883623 既有的 tests/test_transcript_audit.py::test_write_receipts_never_overwrites_conflicting_receipt（已在基线复证），无新增失败、无 skip"}],"native_refs":["tests/test_feedback_observation.py::test_report_usage_publishes_a_receipt_on_every_terminal_path（3 参数化：invalid-json / oversized-request / discovery-with-source-payload，各恰好 1 条 earnings-retrieval-usage/1）","docs/plans/feedback-loop-2026-11-17/receipts/red_missing_usage_receipt.txt（基线 6883623 三条静默路径 vs 修复后全覆盖的执行对比）","earnings-retrieval-usage/1：request_id=fb-worker-aborted-001、usage_complete=false、usage=null（未知用量，不补零、不重试）","tests/golden/manifest.json（10 个 producer golden，--check 逐字节 matched）"],"sample_refs":["docs/plans/feedback-loop-2026-11-17/samples/success.jsonl（+ 同名 .details.jsonl，回放逐字节复现）","docs/plans/feedback-loop-2026-11-17/samples/entitlement-402.jsonl（+ .details.jsonl）","docs/plans/feedback-loop-2026-11-17/samples/unknown-usage.jsonl（+ .details.jsonl）","docs/plans/feedback-loop-2026-11-17/samples/deviation-msft-2026q3.jsonl（+ .details.jsonl）","docs/plans/feedback-loop-2026-11-17/samples/deviation-aapl-2025q2.jsonl（+ .details.jsonl）","docs/plans/feedback-loop-2026-11-17/samples/deviation-usage-receipt.jsonl（+ .details.jsonl）"],"review":"三轮独立审查 + 增量复核，均以执行为准且未获知作者结论：round-1@7ecacb2 PASS WITH FINDINGS(12)→2bf7c89 全关；round-2@2bf7c89/1934313 PASS WITH FINDINGS(N1–N9)→951aa5a 关 N2–N9；round-3@951aa5a PASS WITH FINDINGS(F1–F4)→f4be8c3 补单写者机制并关 F1→按审查方子逐字关 F-a/F-b@79d3563；最终判定 79d3563 = PASS（三命令 99/10/735 独立复算一致，无必须先修项）","cleanup":"仅本轮临时目录：仓库内 .pytest-tmp-* 共 5 个已全部删除，git status 仅剩开工遗留的 .mimosa/ 与 .workbuddy-ai/（各 4 文件，未跟踪未提交）；独审 scratch 位于系统 Temp（fbet-r2、fbet-r3），非本仓文件；未提交任何运行目录、整份业务产物或调试副本","remaining":["跨仓原场景复验：ET-FB-002 消费端复验 state=pending，需 ROOT/filing-fetch 在 >=59fc207（含 79d3563）重跑确认每条终态路径收据存在；ET 不能替消费端关单","跨仓行为：消费端是否按 usage-accounting 对账、是否接受错期卡，归 ROOT 与消费端 owner","基线既有失败 tests/test_transcript_audit.py::test_write_receipts_never_overwrites_conflicting_receipt 归原 ET 业务线（transcript_audit.py --write-receipts 冲突退出码），卡外","不可离线证明项：真实 FMP entitlement/留存能力、真实线上 Motley Fool DOM 抽取适应性（仍属 11-14 登记项）","若要求本仓实施成功明细轮转，需另开卡（当前为宿主路径运维策略，本仓不删宿主文件）"]}
~~~

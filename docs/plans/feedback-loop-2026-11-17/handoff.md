# FB-ET 交接 · 电话会采集反馈与离线复现

本仓（`earnings-transcripts`）按 [FB-ET 施工卡](FB-ET.md) 完成的交付。ROOT 负责派工、跨仓原场景复验与公司池调度；本仓只交本仓能证明的事实。

## 一句话结论

一次完成的电话会取数现在会留下四条可跨仓比较的观察（业务状态词、期次归属、内容引用、原生用量），本仓同时确认并修复了一个真实 ET 缺陷（`--report-usage` 在两条零 provider 请求的终态路径不发布原生用量收据），并交付了具名离线回放、幂等问题卡与修理技能。

## 交付物与所在

| 交付物 | 位置 | 说明 |
|---|---|---|
| 反馈投影 | [`transcript_feedback.py`](../../../transcript_feedback.py) | `feedback-observation/1` 观察的生成、去重、脱敏与写入；stdlib-only，不导入 provider 实现 |
| 入口接线 | [`transcript_tool.py`](../../../transcript_tool.py) | 每条终态路径构造原生用量收据并按 `--report-usage` 发布；运行结束后投影观察。stdout/stderr/退出码/结果不变 |
| 离线回归 | [`tests/test_feedback_observation.py`](../../../tests/test_feedback_observation.py) | 62 项，含 38 项「已发布结果词不得报偏差」参数化守卫 |
| 具名回放入口 | [`tools/replay_feedback_case.py`](../../../tools/replay_feedback_case.py) | 9 个具名 fixture；只用假 HTTP/本机回环，永不联网 |
| 问题卡导入 | [`tools/feedback_issue_cards.py`](../../../tools/feedback_issue_cards.py) | 一因一卡；重复导入零新增 |
| 修理技能 | [`skills/repair-earnings-transcripts-issue/SKILL.md`](../../../skills/repair-earnings-transcripts-issue/SKILL.md) | 一次处理一个根因：核事实 → 公开入口复现 → 同类入口与合法对照 → 归因 → 先写 RED → 修共同责任层 → 定向 GREEN → 原场景复验 → 记录版本/用例/消费端状态 |
| 问题卡 | [`issues/`](issues/) | ET-FB-002（本仓确认的缺陷，已修）、ET-FB-001（期次归属的受控演练，未见生产发生） |
| 收据 | [`receipts/`](receipts/) | 三份 RED 收据：适配器缺失、用量收据缺失、合法 discovery 被误报为偏差 |
| 样本 | [`samples/`](samples/) | 交 ROOT 的脱敏 sidecar：成功、预期 402、未知用量、已判偏差（两份） |

## 本仓确认并修复的 ET 缺陷

`--report-usage` 承诺在 stderr 上给出恰好一条原生最终用量收据。基线 `6883623` 上，preflight 拒绝路径发布收据，而另外两条同样零 provider 请求的终态路径（非法 JSON、超大请求、带 source payload 的 discovery）**不发布**。消费端（filing-fetch 按 `earnings-retrieval-usage/1` 对账用量）因此无法区分「本次没有调用 provider」与「工具没来得及报告」。修复后每条终态路径都发布恰好一条收据；结果 schema、退出码、收据形状与「不重试」承诺均不变。

复现与修复前后的对比：[`receipts/red_missing_usage_receipt.txt`](receipts/red_missing_usage_receipt.txt)。

## 观察协议

一行一个独立 JSON 对象，必填 `schema_version`、`owner`、`run_id`、`attempt_id`、`step_id`、`check_id`、`expected`、`actual`；可选 `issue_key`、`evidence_ref`、`details_ref`、`version`、`parent_step_id`、`disposition`、`origin_owner`。

- `evidence_ref` = 本次运行的公开证据（本仓不透明标识，如 `et-case:fmp-exact-period`）。
- `details_ref` = 脱敏明细记录的标识；明细在 sidecar 的同名 `*.details.jsonl` 兄弟文件里。
- `disposition` 只取 `expected` / `deviation` / `unknown`；`unknown` 表示只读对账（未知用量不重开单）。

四个检查：

| check_id | 比较什么 |
|---|---|
| `outcome-naming` | 业务状态词对已发布的具名原因（`REASON_STATUS` 表） |
| `exact-period-ownership` | 请求期次对原生 provider 响应里重新推出的期次；只有原生字节能证明时才允许归属 |
| `content-reference` | 声明的 `provider_payload_sha256` 对返回的原始字节 |
| `usage-accounting` | 原生最终用量对本次真正发布的收据 |

合法终态一律记正常处置：已发布的 38 组「状态词 × 具名原因」全部被守卫断言为 `expected`，包括 402 权益不足、错期文档、查无目标期次、discovery 成功、限流、缺凭据与未知用量。真正的产品偏差（例如业务把错期文档标为 fetched）由 `exact-period-ownership` 报为 `deviation` 并带 `issue_key`。

## 本仓不能证明的

- 真实 FMP 账户的 entitlement 与留存能力：全部证据都是固定夹具与假 HTTP，本仓没有付费账户事实。
- 真实线上 Motley Fool DOM 的抽取适应性（仍属 11-14 登记项）。
- 跨仓行为：消费端是否真的按 `usage-accounting` 对账、是否接受一张错期卡——归 ROOT 与消费端 owner。

## 交接 JSON

~~~json
{"lane":"FB-ET","owner":"earnings-transcripts","base":"68836237be8000f77ba51da0f28e35e8838e63b7","reviewed_commit":"PLACEHOLDER_REVIEWED","published_commit":"PLACEHOLDER_PUBLISHED","status":"PLACEHOLDER_STATUS","tests":[{"command":"C:/Miniconda/python.exe -B -m pytest -q tests/test_feedback_observation.py tests/test_retrieval_cli_e2e.py tests/test_worker_result_budget_contract.py tests/test_optional_local_receipt.py","result":"PLACEHOLDER_T1"},{"command":"C:/Miniconda/python.exe -B tests/generate_transcript_goldens.py --check","result":"PLACEHOLDER_T2"},{"command":"C:/Miniconda/python.exe -B -m pytest -q tests/","result":"PLACEHOLDER_T3"}],"native_refs":["PLACEHOLDER_N1"],"sample_refs":["PLACEHOLDER_S1"],"review":"PLACEHOLDER_REVIEW","cleanup":"PLACEHOLDER_CLEANUP","remaining":["PLACEHOLDER_R1"]}
~~~

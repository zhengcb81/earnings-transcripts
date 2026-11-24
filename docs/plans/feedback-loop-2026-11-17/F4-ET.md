# ET 原始用量收据的自动恢复

**2026-11-24 当前施工入口**为[批量电话会逐项复原](F4-ET-BATCH-REPLAY-handoff.md)。这份交接关闭上一卡的余项——批量路径逐公司、逐期次的终态现在按原 request ID 可跨进程冷读，owner 查证支持 `not_applicable`/`unknown`/`unconfirmed`。下文保留原运行端口与此前验收范围。

2026-11-24 的 S1 诊断脱敏和复验单独记录在[本仓回执](S1-04-diagnostic-redaction.md)。

2026-11-23 的 Windows 深路径、长日志尾部和同一运行多处拒绝的修正见[本仓交接补充](F4-ET-FAILURES-handoff.md#2026-11-23-总控复验补充)。

明细恢复、空公司选择、已确认后晚到发生和宿主摘要字段见[最终补投及 ACK 复验](F4-ET-FAILURES-handoff.md#2026-11-23-最终补投及-ack-复验)。

本轮从已签收的 [FB-ET](FB-ET.md) 继续，基线为 `main@6b512daddc7e739351d10fec29442784b10dbcd6`。ET 保持原始观察和明细，提供本仓的分诊、补发、复验、回告和原动作查询。总控接生产启动事件并执行组合复验。

## 问题和责任

[ET-FB-002](issues/transcript-usage-receipt.md) 已修复当前 JSON CLI 的终态收据发布缺口。F4 恢复负责处理历史侧车中已有完整原生用量而公开发布遗漏的同一发生。原生收据、运行、尝试、步骤、出处和观测版本都与同一已完成明细绑定。按原协议补发用量，保留旧事件和价格证据。一次已有计量且没有账单的请求仍保留历史费用未知。只看到开始或不完整明细时直接返回原作业未知，不重新拉供应商、原文或译文。其他问题继续走 [本仓修复技能](../../../skills/repair-earnings-transcripts-issue/SKILL.md)。

从 `transcript_tool.py` 的已有 JSON CLI 运行号和尝试号接入。宿主通过 `RESEARCH_FEEDBACK_RUN_ID`、`RESEARCH_FEEDBACK_ATTEMPT_ID` 和 `RESEARCH_FEEDBACK_OUT` 为每份 producer 进程分配独占 JSONL。原终态旁路使用该路径，同名后缀 `.details.jsonl` 保存脱敏的原生结果和原生及已发布收据。父进程负责写入，调用 worker 时清除旁路环境。由消费方或 ROOT 在终态后交给有限 tick，恢复时沿用同一文件和原 action。

## 公开端口

宿主配置固定 argv 前缀 `[python, "-B", "<ET 根>/tools/feedback_owner.py", "--db", "<ET 数据根>/owner.sqlite", "--sidecar-dir", "<ET 数据根>/observations"]`。存在两个或更多原始观察目录时，对每个目录重复 `--sidecar-dir DIR`；单次交接也可传可重复的 `--sidecar FILE`。数据目录和命令由部署给定，来文仅承载公开动作和不透明引用。用独占 ET 回执库保存原提交、结果和本仓补发版本。

| 命令 | 结果 |
|---|---|
| `triage` | 把原始用量发布偏差和原生已完成明细绑定，给出对应发生的修复标识。合法结果仍按预期返回，证据不足有具名结果。 |
| `repair` | 在本仓小型回执库持久补发 `et-usage-publication/1`，`fixed_version` 带完整内容 SHA。 |
| `get-usage ACTION_ID` | 按同一修复动作只读完整 `publication`，其中 `usage_receipt` 继续使用 `earnings-retrieval-usage/1`。 |
| `replay` | 从回执库读补发结果，与原冻结明细和指定版本核对。保留首次发生和尝试。 |
| `report` | 以已保存原复验版本、结果和证据回报责任仓，事务一次落盘。 |
| `get-action ACTION_ID` | 用于四个写动作的 `lookup_argv`，只读原始 `receipt.request` 和 `receipt.result`。收据原子写入，同 action 的异内容不覆盖。 |
| `reconcile` | 只读按原 run、attempt、step、check 及版本核对。同一终态证实零请求和零字节时才说明 provider 未调用。只见开始保留 `unconfirmed`，无历史价格证据保留 `cost_unknown`。 |

## 复验与部署

使用 [缺发布发生](samples/deviation-usage-receipt.jsonl) 及同名明细。当前 `transcript_tool.py --request-stdin --operation discover --include-source-payload --report-usage` 命中同一业务前置拒绝，输出恰好一条 native 用量。补发的三件原数据分别与该 CLI、原明细和公开查询一致。恢复和回告丢回复案例启动真实本仓动作进程，提交后硬停回传方，再启动查原收据。另用 [用量未知](samples/unknown-usage.jsonl) 与仅开始行验证原作业状态，用一期保留原生计量样例核对已有请求和未确认金额。测试的护栏禁止出站请求，记录中的历史计量源于离线样例。

`python -B -m pytest -q tests/test_feedback_owner.py tests/test_feedback_observation.py tests/test_retrieval_cli_e2e.py tests/test_worker_result_budget_contract.py`


本仓定向 92 项通过，全量 744 项通过，10 份黄金内容逐字节一致。原先缺少运行端口的 RED 与最终源码对应。Black、Ruff 和暂存差异检查通过。本轮 pytest 独占临时目录已清理，候选工作树保留给总控组合。

## Next Step

ROOT 组合使用实际宿主作业 ID 和本仓 `get-action` 复验落盘丢回复与再导入，按 filing-fetch 公开用量合同核对同一原生回执。通过后发布候选并登记实际部署版本。

## 2026-11-23 补验 owner 原始证据路由

总控的投递收件与 owner 的冷读是两个步骤。ET 原生收件目录与历史总控旁路分开时，在现有 owner 命令的 `--sidecar-dir` 后继续追加独立 ET 目录。旧版 CLI 只保留最后一个参数，使用两份真实历史样例复现了单目录都可查、双目录时早先目录的原发生返回 `unknown`。通过可重复配置后，两份记录都能经新进程从原目录查回。原来的单目录 CLI、可重复的 `--sidecar FILE` 及 `OwnerRuntime(..., sidecar_dir=...)` 保持兼容。复验比较两个请求的原生回执、文件原字节与无 SQLite 或供应商工作副作用。

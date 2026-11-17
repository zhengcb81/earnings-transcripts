# FB-ET　电话会采集反馈与离线复现

施工状态：可立即交给一名独立 harness。唯一工作目录为 `C:/Users/郑曾波/Projects/earnings-transcripts/earnings-transcripts`，主线 `main`。本卡从 `50d9190` 核对可用路径；启动时记录实际 HEAD 与 dirty 状态。根 [task_plan.md](../../../task_plan.md) 是本仓唯一状态入口。

## 交付范围和现有依据

目标是从一次电话会公开请求追到原生结果、用量与可复现偏差，并在 ET 本地修理已确认的 ET 问题。实现独占 ET 的新反馈投影、修理技能 `skills/repair-earnings-transcripts-issue/SKILL.md`、离线测试及本目录。必要的入口接线限 `transcript_tool.py` 与其原有本仓收据边界。既有 `transcript_audit.py --write-receipts` 冲突退出码事项由原 ET 业务计划单列，本卡与其写集合彼此独立。旧 `.mimosa/`、`.workbuddy-ai/` 属已有本机状态。

- `README.md` 的 JSON CLI 与用量章节定义协议。stdout 恰好一条业务结果，正常业务级失败也可退出 0。显式 `--report-usage` 时 stderr 才带原生最终用量收据，保留 request_id 与 usage_complete。翻译、真实网络调用与账号服务均不在这次离线验收路径。
- [已有生产形状 golden](../../../tests/golden/README.md) 通过真实 tool→API→假 HTTP 构造。可复用 fetched、entitlement_402、wrong_quarter、credentials_missing 以及 canonical 内容引用。已有 `retrieval_runtime.py` 负责真实 worker 超时。ET 拥有 provider 和原语言 TXT，CWP 拥有虚拟存储与入库，FF 拥有补齐编排。

## 与 ROOT 的交接格式

观察格式为 feedback-observation/1。一行一个独立对象。必填字符串字段为 schema_version、owner、run_id、attempt_id、step_id、check_id、expected、actual。expected 和 actual 是适用于该检查的稳定结果词。可选字段为 issue_key、evidence_ref、version、parent_step_id、details_ref、disposition、origin_owner。disposition 仅取 expected、deviation 或 unknown；issue_key 是有证据支持的责任仓规则签名。引用为不含凭据、路径和可执行命令的本仓不透明标识。version 记录被观察运行的版本，交接报告另列被测代码版本。合法拒绝按实际预期归类。例子仅说明协议字段，运行时产生真实 ID 和版本。

~~~json
{"schema_version":"feedback-observation/1","owner":"earnings-transcripts","run_id":"run-example","attempt_id":"try-1","step_id":"step-1","check_id":"entitlement-response","expected":"unavailable","actual":"unavailable","disposition":"expected","evidence_ref":"case-example","version":"fixture-v1"}
~~~

实现和校验 owner 只在本仓完成。ROOT 从公开交接文件读取，负责派工、跨仓原场景复验与公司池调度。ROOT 计划位于公司总控仓 docs/plans/feedback-loop-2026-11-17/task_plan.md，当前文档已包含单仓实施所需接口，允许独立并行开工。


总调度与各 owner 约定三个可选环境量。RESEARCH_FEEDBACK_OUT 提供本次独占 sidecar 路径，RESEARCH_FEEDBACK_RUN_ID 和 RESEARCH_FEEDBACK_ATTEMPT_ID 提供父运行及尝试编号；独立 E2E 可自建编号。输出位置通过宿主配置或测试入口注入，避免把任意写路径暴露给财报正文或网络输入。业务协议允许省略这些诊断字段。父进程启动子 worker 时分配不同的输出文件，或清除其 OUT 并由父进程记录该子调用的公开结果，确保每个文件只有一个写者。已写入的事件重放时复用原 ID。另一次真实尝试要单独设置 attempt_id。

## 三个实施节点

1. **TDD 固定差异。** 在隔离 tmp_path 下，新建 `tests/test_feedback_observation.py`。用现有 FMP 假响应构造精确期次 fetched、查无目标期次、HTTP 402 权益不足。对照“HTTP 退出 0 但业务把 wrong-quarter 标为 fetched”的小型错误响应，验证归属检查会报偏差，合法的 402 记正常处置。用模拟已中止 worker 且 usage_complete=false 的原生收据覆盖未知，断言不会发第二次请求。记录新适配器缺失等真实 RED。
2. **接入真实操作。** 归一化 request_id、operation、provider 结果/具名原因、期次/截止与内容引用、已知用量或 unknown。在正常调用中由测试/作业启动上下文启用 sidecar；同时分别验证启用/关闭的 stdout 字节、退出码与既有 --report-usage 行。单独检查当输出位置不可写时业务结果、HTTP 次数、翻译调用次数均不变。必要时用只返回 EACCES 的注入 sink 模拟 Windows 权限差异。
3. **完成技能与闭环。** 交两次相同问题、两次合法但相似事件，复放现有 golden 后输出原生事实及新观察，旧报告原样保留。将同一问题不同公司/期次挂到一张本仓问题卡。检查在新进程中从卡片运行本仓具名 fixture 的 replay，重复导入不新建卡也不下载。由独审在未知预期的情况下分出合法拒绝与真正产品偏差。跨仓试验留给 ROOT。

## 本仓实施与修理技能

把观察分为动作开始和动作结果。记录公开操作、时间、原生请求或作业编号、结果、是否有重试或复用、错误的稳定类型和原因链。开始后中断而没有终态时保留未知，新的尝试沿用运行编号并更换 attempt。诊断明细按需保存在本次运行目录，敏感头、凭据、原文正文、原始响应、未脱敏的异常文本和物理绝对路径留在反馈数据之外。成功日志短期轮转；开放问题绑定可随仓复现的小样本或现有永久来源引用。原件和正式运行产物继续由原 owner 保存。

每个外部参加联调的值仅通过文件内容公开。启用反馈的实际运行及模块 E2E 在原生步骤完成时自动产出观察。独立用例自行提供运行编号，调用方有上下文时继承其运行编号。由调用环境指定独占输出位置，每次调用由一个写者写一份 UTF-8 JSONL。观察不混入业务 stdout/stderr。写入失败由现有测试/作业记录报告诊断不完整，业务退出码、返回值、已有费用、原件和重试行为保持原合同。

当相同已判定根因跨运行出现时，把新的运行和证据引用挂到同一张本仓问题卡。原生观察保留原始 attempt，根因可以待定；只在有区分证据时分拆或合并。第一版沿用本仓存放回归用例的 Git 与本计划，需修理的共因可登记在本计划下的 issues/，一因一卡。开发用的缺陷编号与真实运行证据相链。重复导入同一次观察应为零新增，同一个错误码下不同根因保持可分，临时外部条件写恢复条件。

修理技能从本仓 issue 或给定运行引用出发。先核预期、实际、代码/配置版本及真实输入来源，从公开入口复现第一次偏离合同的位置，再查同类入口和相似合法对照。业务错误、测试夹具、部署环境和外部条件分别归因。可在本仓实施的修复及其测试写进 canonical PWF；属于邻仓的偏差交 ROOT 提供公开输入输出和证据。依据旧错误和对照先写 RED，再修共同责任层，在定向 GREEN 后用原场景复验。记录修复版本、回归用例、消费者待验或已验状态。相同版本、相同输入和同一失败不触发循环重跑。技能放在本仓 skills/ 下，只消费本仓受控的回放入口和不可执行的证据引用。

## 定向验证入口

根目录用本仓可用的 Python；本机宿主示例为 `C:/Miniconda/python.exe`。先单跑新增文件捕获 RED，再跑下列组合。测试运行器使用独占临时目录和假 HTTP/本机回环，禁用生产凭据及远端调用。goldens 的 --check 仅读取固定预期；不得在产品测试中运行 --write。

~~~powershell
& 'C:/Miniconda/python.exe' -B -m pytest -q tests/test_feedback_observation.py tests/test_retrieval_cli_e2e.py tests/test_worker_result_budget_contract.py tests/test_optional_local_receipt.py
& 'C:/Miniconda/python.exe' -B tests/generate_transcript_goldens.py --check
~~~

断言具体内容、调用次数、单条 wire、可重现性与收尾清理；检查 pytest 跳过理由。给 ROOT 至少各一条成功、预期 402、未知用量和已判偏差的脱敏 sidecar 样本。

## 独审、清理与交接

先让独立审查 agent 在未获知作者预期结论的情况下，仅凭本卡和可复现样例验证一次完整性、质量、协议兼容、误开单、重放和恢复。审查覆盖整段生产路径及故障路径，逐项修复并在最终源码上复核。正常 hooks 提交；远端发布前核本仓主线和并行变动，只发布本卡文件。业务链的跨仓再验交给 ROOT。

在本目录交 handoff.md（含一段 JSON），字段如下。记录实际事实，尚未运行的现场测试写明条件。git status 逐路径区分开工遗留和本卡变化，测试临时目录在采集必要证据后恢复原样。不要提交运行目录、整份业务产物或调试副本。

~~~json
{"lane":"FB-ET","owner":"<owner>","base":"<开工 SHA>","reviewed_commit":"<最终受审源码 SHA>","published_commit":"<实际发布 SHA 或本地说明>","status":"<完成或具体缺口>","tests":[{"command":"<定向命令>","result":"<通过数/失败/跳过及原因>"}],"native_refs":["<原生收据或测试标识>"],"sample_refs":["<可重放的观察样本>"],"review":"<独审范围/版本/复验和余项>","cleanup":"<仅本轮临时目录及结果>","remaining":["<组合验收归属>"]}
~~~

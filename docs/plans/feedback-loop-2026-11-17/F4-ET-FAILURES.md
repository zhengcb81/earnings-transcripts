# F4-ET-FAILURES 电话会普通入口、坏 TXT 和 worker 失败可复现

状态为待实施。工作目录为 `C:/Users/郑曾波/Projects/earnings-transcripts/earnings-transcripts`，发布线 `main`，审计基线 `61fe94e201eee0f253a1084b9876d023820ea01c`。本仓原端口、原用量收据及历史修复见 [F4-ET](F4-ET.md)。本次检查源码路径没有在途修改；`.mimosa/`、`.workbuddy-ai/` 属现有工具目录。

## 工作约定

先在下述唯一工作目录执行 `git status --short --branch`、`git worktree list`、`git diff --name-only HEAD --` 和 `git log -4 --oneline`，记录起点。以下源码位置是审计时的定位，按当前方法和行为接续。若现有 harness 已改同一路径，将本卡排在该线后，先读取其差异和验收再接续。三个 owner 各有自己的目录、状态库、测试和提交。保留并行作者的暂存区、配置和产物。遇到确实需要改公共 wire 或其他模块的事项，把最小输入与期望交 ROOT，继续本仓可完成的部分。

隔离测试在真实 Python 子进程中调用普通入口。配置、用户数据根、工作目录、日志、数据库、暂存与输出全部映射到同一测试专用临时根；使用进程自己的环境，清除继承的 `RESEARCH_FEEDBACK_OUT`、运行号、尝试号再测默认路径，并单测过去显式设置的调用。替身仅放在本仓的磁盘故障注入点或真正外部传输、计费、引擎边界。保留原生 CLI、父子进程、结算、查询及文件操作。收尾核对临时目录恢复到原样。

## 起点与排他写入范围

总控审计运行号为 `f4-coverage-audit-20261121`，发现方是 company-wiki。中央档案 `company-wiki/docs/plans/feedback-loop-2026-11-17/issues/failure-coverage-20261121.jsonl` 有以下两条原发生，版本与上面的基线一致。离线复现参考同目录 `probes/et.py`，只读。验收要由 ET 自己的测试独立完成。

| 原发生位置和编号 | 已观察到的根因与修复目标 |
|---|---|
| 第 10 行，`baseline-61fe94e2 / txt-and-cli / failure-coverage`，`native-failure-not-forwarded` | 原审计 8 案中 4 案无观察。普通 scraper、参数解析、TXT 审计与 JSON 提取走不同例外路径。坏 TXT 已在原生报告中统计为 corrupt，进程返回并不能替代逐文件结果。 |
| 第 11 行，`baseline-61fe94e2 / retrieval-worker / failure-coverage`，`provider-cause-lost` | worker 离线退出和 402 已有观察，原有命名检查仍判 expected，费用未知；子进程诊断被丢掉，无法独立找回事发阶段和根因。用量发布遗漏的正控会报偏差，应继续有效。 |

拥有普通入口 `transcript_tool.py`、`scraper.py`、`transcript_audit.py`，父子运行边界 `retrieval_runtime.py`、`retrieval_worker.py` 与既有 `transcript_feedback.py`、`tools/feedback_owner.py`，以及直接关联的本仓测试和本计划。仅在公开运行结果确有需要时扩充本仓已有 retrieval 协议，保持旧消费方兼容。FF、CWP、下载器、MC 以及全局安装由各自 owner 负责。

## A 先冻结失败

首先写独立临时目录测试，分别放入正常 UTF-8 文本和损坏的 `ACME_Q3_2026_earnings_call.txt`（字节 `ff 00`），关闭可选 sidecar 后运行 `python -B transcript_audit.py --root <tmp/txt> --report-dir <tmp/report>`。断言原报告的逐文件失败可按本次 ID 在**第二个进程**查到，期望原因是解码损坏；另涵盖“成功同批”的文件。将基线原生结果、F4 缺项和失败断言记为 RED。另起两种不同内因的 worker 失败，例如非零子进程退出与超时，保存实际退出状态/阶段。分别运行以下普通调用与已有正控，记录原生退出、业务状态、来源可用性、费用状态和冷读结果。

| 隔离入口 | 要看见的证据 |
|---|---|
| `transcript_tool.py --operation bad`；传入损坏 JSON 至 `--request-stdin --report-usage`；`scraper.py --periods not-a-period`；TXT root 缺失 | 取到请求拒绝原因及发生位置，合法空结果与成功审计另设对照。 |
| `tools/run_request_budget_e2e.py --case worker-loss` | 进程退出事实和原 worker 身份可查。未完成收据可为 unknown，同时仍可分诊真实故障。 |
| `tools/replay_feedback_case.py --case fmp-entitlement-402 --out <tmp/case.jsonl>`；同命令换 `usage-receipt-suppressed` | 402 作为该来源权限/可用性事实；按原请求是否仍取得资料判业务结果。原生成本不足以定价时不推断费用，漏用量继续进入原修复途径。 |

## B 接通本仓并恢复

`transcript_tool.py:244`、`scraper.py:1278`、`transcript_audit.py:167` 在各自解析前承接一个公共运行上下文。TXT 结果以原报告中的相对文件引用定位，不在反馈里存电话会正文。处理 `retrieval_runtime.py:130` 附近抛弃 worker stderr 的路径，用脱敏结构诊断保留原子进程退出码、阶段、异常内因，兼容已有业务 JSON 和 stdout。投影层 `transcript_feedback.py:293` 分别记录操作是否实际完成、回退后来源是否可用、收据是否已有及用量已知程度。child 发生仅由拥有详情的进程/父归并一次，同伴成功不能掩盖本次失败。

在专用测试中暂时让外部反馈文件不可写，然后停止第一个进程。另起两个进程补发同一待投递项、重复查原作业；核对同一发生只出现一次，下载、发现、翻译及任何付费请求新增均为零。用仅开始、终态已写和回执已送达三种截止点。两种出错虽有相同外层类型仍能查见不同脱敏内因。链接和部署测试沿用 [原调用协议](F4-ET.md)。

本卡新增可执行回归 `tests/test_f4_ordinary_failures.py`。在实际 ET checkout 的测试 Python 执行以下命令，所有夹具和输出由测试映射到临时目录，启动子进程前阻断外网。

```powershell
python -B -m pytest -q tests/test_f4_ordinary_failures.py
python -B -m pytest -q tests/test_feedback_owner.py tests/test_feedback_observation.py tests/test_retrieval_cli_e2e.py tests/test_worker_result_budget_contract.py tests/test_transcript_audit.py
```

## 持久化和公开交接

沿用本仓既有日志、动作回执及 `feedback-observation/1`。普通启动在参数解析和副作用前建立本次身份，父调用传入的 ID 原样关联，缺省时生成可跨进程查回的 ID，两个并发任务隔离。利用原生业务终态或本仓已有持久目录保留待投递记录；先保存再确认，导出失败不覆盖业务原返回。新进程依据同一 owner/run/attempt/step 或 action 只读对账、补投。断在落盘与确认之间、重复消费或并发恢复均只登记一次原发生。所有可写介质均不可用时，外层已有启动收据才能标出未确认区间，明确哪些原因已经不可恢复。成功的兄弟任务不能覆盖失败或未知的那个尝试。

交给 ROOT 的公开观察沿用 `owner`、`origin_owner`、`run_id`、`attempt_id`、`step_id`、`check_id`、`version`、`expected`、`actual`、`disposition`、`issue_key`（如适用）、`evidence_ref`、`details_ref` 及已有拒绝码。原生明细补足未执行/已尝试/未知、退出码、阶段/输入引用、业务结果、脱敏异常类型链及本仓相对代码位置、使用量和其依据。未知费用保留未知，确知未发送才可报零。发现拒绝先按业务目的审视，识别正常请求被误拒时在本仓 PWF 记根因和减门方案；正常拒绝如实留痕，不必启动修复。向外只给既有可解析引用；不复制原始 argv、原文、密钥、完整 URL 查询或未脱敏 stderr。

### 直启发布接口

显式 `RESEARCH_FEEDBACK_OUT` 沿用单次文件及其既有明细格式。未传 OUT 时写 owner 自有的持久状态根下 `observations`，`RESEARCH_FEEDBACK_DIR` 可指定隔离的公开收件目录；两个变量都有值时 OUT 优先。使用同目录临时文件、关闭写入，原子发布 `feedback-<32位小写UUID hex>.jsonl`，对应的脱敏明细就绪后，再原子写同 basename 的 `.ready`。该 JSON 标记写 `owner`、`run_id`、`attempt_id`、`step_id` 并绑定同一份原发生，不放路径或命令。ROOT 从公开收件目录消费并负责确认标记；owner 保留原生证据，用记录过的交付身份补发。覆盖数据已落而 ready 未发、已发但回告丢失、并发消费三个窗口。确认失败只影响交付状态；业务返回、原请求和计量保持原样。用三组隔离运行覆盖显式 OUT、仅 DIR，以及两者均未设置而将本机状态根映射至临时目录。交接写本机正式默认收件目录的**绝对路径**、相对状态根的计算方法以及示例文件名，供 ROOT 在 `native_observation_dirs` 配置。若已有实现符合这些语义直接复用。

## 大节点 C 与交付

用新进程逐项对照入口受理清单、原生终态和 F4，列出每次运行及尝试的实测结果。分母用本次实际注入的入口事件，另外标明已覆盖、原因仍未知、待投递和未在现场实测的路径。冷读两种同外层异常但不同内因，核对是否可分别复现。有效拒绝、降级成功、失败、强退未知及正常成功都要有对照。针对拒绝先看门禁是否必要及能否简化，再决定修复。

实现和集中测试完成后，调用**独立审查 agent**，给它实际候选提交、两条原始发生及隔离复现命令，要求检查本卡全部覆盖、真实入口、错误/费用语义、故障恢复、数据脱敏及并发幂等。发现的可修问题全部修完，审查 agent 在最终源码版本复核，完成后才交接。把卡号、工作目录、基线和最终源码 SHA、分支/远端、改动路径、对应原发生、RED 前后实测、独审发现与修复版本、回归/冷恢复命令与退出码、调用数和清理结果写入本目录的交接 Markdown。提供可直接冷读的一份脱敏观察和同一尝试的明细示例或生成命令。发布本仓修复和交接，ROOT 通过公开接口做跨仓组合。

# MB-ET — 独立施工卡

## A0：旧分支与材料已核销（2026-11-11新增）

`review/repo_reconciliation_20261111/acquisition_review.md` 已逐项审查19个refs/13工区：全部refs均为main祖先，没有漏并代码。ROOT已删除只剩.git指针的旧Temp工区与2026-07-07的eval_results.json（旧翻译评估生成物，无运行读取引用）；工具记忆保留。不要重新合旧分支、执行翻译评价或重复采集。

本卡按原协议/worker表示限额/隔离方案继续即可；开始时核实际main增量，已满足的合同可NO_CHANGE_PROVEN。旧工区/branch退休由ROOT核对原件和部署引用后执行，ET作者不对外层Projects/earnings-transcripts或其他仓做递归清理。完整before/执行后状态见总计划reconciliation.md。

## 任务定位与唯一工区

- 原仓：`C:/Users/郑曾波/Projects/earnings-transcripts/earnings-transcripts`；基线 `003c1c065109d0b02c7bd4de09ad3e8461a99c0e`；目标主线 `main`。
- 新工区：`C:/Users/郑曾波/Projects/mb11-et`；分支 `codex/mb11-et`。本卡尚未开工，不把准备好的卡称为已派发。
- 本仓PWF：`docs/plans/mb11-et-2026-11-11/`。先建立 task_plan.md、findings.md、progress.md，记录真实基线与工作量；本卡及公共接口说明可复制到该目录inputs。
- 总控计划：`C:/Users/郑曾波/Projects/company-wiki/docs/plans/module-boundaries-2026-11-11/`。开工只需读本卡、该目录 interfaces.md、acceptance.md、runtime_verification.md 和本仓AGENTS；无需回放旧聊天/全部历史PWF。

```powershell
git -C 'C:/Users/郑曾波/Projects/earnings-transcripts/earnings-transcripts' worktree add -b codex/mb11-et 'C:/Users/郑曾波/Projects/mb11-et' 003c1c065109d0b02c7bd4de09ad3e8461a99c0e
```

若工区/分支已存在，先只读核归属；不能 reset、清别人的目录或接管未知工作区。可以用新的唯一后缀并记录。用仓库实际可用的Python/离线依赖；不要重新抓取原件或部署到全局技能目录。

## 全卡共用完成规则

- 先列本仓实际交付runtime的活动入口、逻辑模块、公开面、状态owner和依赖方向；包含兼容/退休分类。确认缺口只是入口，不是允许忽略其他已登记模块。发现同根绕行在本卡owner内归并修复，新增业务需求交父计划。
- 保留现有公开协议；各模块只写自己代码和状态。公开协议真需要改时先把失败例和最小兼容方案交ROOT协调，继续其余独立工作；不擅自改邻仓。
- A写行为/隔离测试 → B修owner根因 → C模块验收。A/B/C是工程步骤，完成A即可继续B/C，不需ROOT或用户逐步批准。
- 接口负例应体现实际能力/依赖错误；fixture/缺依赖/沙箱权限失败不冒作产品RED。已有边界通过就保留，不为凑改动重写。
- 只在模块最终交付前调用独立审查agent，审完整度、架构归属、回归和所有新增测试。修复所有本卡可行动问题并复核，才交付；非本范围新发现明确列给ROOT。
- 测试用新临时目录，恢复初始状态；生产原件/配置/既有WIP不动。只运行受影响测试与必要最终包级CI，不每小步重跑全量，不删断言换绿。
- 提交并推送自己的交付分支（存在remote时）；不自行合并主线、不修改ROOT总PWF。交付本仓PWF下 handoff.md/json、独审报告、测试/恢复证据索引，格式见总计划handoff_contract.md。

## 目标

当前ET跨仓CLI已独立，但supervisor为常量/settings导入完整transcript_api，worker又反向依赖supervisor内部协议。明确分离纯合同/settings/worker wire与provider实现，保留原语言采集、预算和恢复行为。另核实并移除worker固定MAX_RESULT_BYTES表示门与显式operation quota冲突。

## 互斥写集合

新增或归并纯模块 `transcript_contracts.py`、`transcript_provider_settings.py`、`retrieval_protocol.py`（名称可按本仓现有风格精简，不增加同义副本）；调整 `transcript_api.py`、`retrieval_runtime.py`、`retrieval_worker.py`、`transcript_tool.py` 的边界导入。必要时 `retrieval_budget.py` 与交付清单/现有安装入口只作相应接线；tests新增 `test_runtime_contract_isolation.py`、`test_worker_result_budget_contract.py`，复用retrieval/runtime/CLI/capability测试。其他provider算法、scraper、translation不扩改。

不写FF、CWP、SID、真实output/缓存/凭证、全局技能或外层earnings-transcripts目录。真正Git根是上述内层，不能在外层新建第二项目。

## A/B/C

1. A：在新进程禁止requests/BeautifulSoup/transcript_extraction/provider实现，仍能导入合同/settings并构造supervisor的协议状态。worker只通过纯worker协议与runtime交换typed结果，不依赖supervisor私有符号。
2. A：已有正常fetch/discover/capability fixtures行为/字段/usage不变；显式不同provider settings不会通过全局共享污染，capabilities本地运行无需key/HTTP。
3. A：对worker结果使用显式额度的边界正反例，覆盖JSON/base64 envelope开销。静态发现retrieval_worker固定MAX_RESULT_BYTES可能拒绝额度内合法数据，先复现；固定表示帽不原样搬到新protocol。测试应避免在日常CI反复生成数百MB文件。
4. B：从transcript_api/runtime抽取真正纯值，旧public import兼容重导出；HTTP/parser仅在worker provider层加载。保留操作deadline、累计HTTP/response预算、unknown计费、失败清理，不重置worker被杀后的预算。
5. B：worker传输限额来自同一实际请求/配置与可计算编码开销。可选日志/receipt体积不决定业务成功，真实响应读取/费用/时间上限继续生效。不要另加output-cap批准或默认翻译。
6. C：用当前分发方式放临时runtime，空cwd经公开transcript_tool命令和测试provider完成有限流程；不强制建wheel，不加生产test-only启动后门。独审修复后交付。

## 已有测试与新增入口

```powershell
python -B -m pytest tests/test_retrieval_runtime.py tests/test_retrieval_cli_e2e.py tests/test_launch_capability_contract.py tests/test_provider_cost_capability.py tests/test_request_response_budget.py tests/test_batch_runtime.py tests/test_optional_local_receipt.py -q -p no:cacheprovider
python -B -m pytest tests/test_runtime_contract_isolation.py tests/test_worker_result_budget_contract.py -q -p no:cacheprovider
```

公开CLI字段和exit由现行ET合同定义保持。FF用原CLI/wire即可消费，不能要求FF同步导入ET新模块。

## 出口

协议层/生命周期不依赖provider实现，worker不反向导入supervisor私有协议；额度内输出不被固定表示帽拒绝，真实资源超额/超时/unknown仍准确；安装/CLI/原语言行为与独审通过。

## 并线交接步骤（目标分支：ET `main`）

1. 作者提交并推送卡片分支，交付最终tip、精确diff、包级测试、独审绑定tip及隔离测试恢复记录；不把未提交工作区当成卡片交付。
2. ROOT在独立集成工区核对最新 `origin/main`、提交祖先关系与本卡写集合，只接收交付提交；保留其他工区未提交内容与历史运行材料。
3. 在集成后ET树集中运行本卡受影响suite一次。若冲突解决改变源码，测试和独审绑定最终代码tip；不整树覆盖、不更改FF owner实现。
4. 通过后由ROOT推送 `main`，在本卡handoff与总控reconciliation记录实际SHA、测试和状态`MERGED`；具体失败交回ET作者。

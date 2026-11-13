# MB-ET 任务计划（本仓 PWF）

## 基线

- 原仓：`C:/Users/郑曾波/Projects/earnings-transcripts/earnings-transcripts`
- 基线 commit：`003c1c065109d0b02c7bd4de09ad3e8461a99c0e`（= 本工区 HEAD = `origin/main`）
- 工区：`C:/Users/郑曾波/Projects/mb11-et`；分支 `codex/mb11-et`
- 目标主线：`main`
- 开工前实测：`python -B -m pytest` 卡内既有 7 个 suite，**143 passed / 149.63s**（见 progress.md 记录）

## 范围（本卡写集合）

新增：

- `transcript_contracts.py`：公共 wire schema 字符串、请求/响应默认额度等纯契约值，仅标准库。
- `transcript_provider_settings.py`：`ProviderSettings` / `DEFAULT_PROVIDER_SETTINGS` / `provider_operation_capability` / `EXCHANGES`，仅标准库。
- `retrieval_protocol.py`：worker 协议（`WORKER_PROTOCOL`、请求/结果文件名、envelope 构造与校验、usage 解析、**按实际请求额度与可计算编码开销派生的传输限额**），仅标准库 + `transcript_contracts`。
- `tests/test_runtime_contract_isolation.py`
- `tests/test_worker_result_budget_contract.py`

修改：

- `transcript_api.py`：改为从纯模块导入并兼容重导出旧公开名；HTTP/身份/期间/抽取/provider 行为不变。
- `retrieval_runtime.py`：只依赖纯协议/settings；删除固定 `MAX_RESULT_BYTES` 常量与 `_result_read_limit`，改用协议派生限额。
- `retrieval_worker.py`：不再 `from retrieval_runtime import ...`；请求/结果 envelope 走 `retrieval_protocol`；结果写入前用同一派生限额判断。
- `transcript_tool.py`：契约/settings/capability 走纯模块；provider 操作函数只在 preflight/fetch/discover 路径惰性装配，`--capabilities` 不导入 provider。
- 既有测试中仅改与本边界直接相关的断言/fixture。

不动：`scraper.py`、`translation`/`translator`、`transcript_extraction.py`、原件/缓存/凭证、全局技能目录、外层 `Projects/earnings-transcripts`。

## 已确认缺口（RED 目标）

1. ET-01：`retrieval_runtime.py:29` 为取常量导入整个 `transcript_api` → 导入 supervisor 即加载 `requests`/`bs4`/`transcript_extraction`；`retrieval_worker.py:24` 反向 `from retrieval_runtime import MAX_REQUEST_FILE_BYTES, MAX_RESULT_BYTES, WORKER_PROTOCOL`。
2. ET-02：`retrieval_worker.py:183` 的 `if len(data) > MAX_RESULT_BYTES: return 2` 只看模块级固定常量（实测 `153092096` = 146 MiB），不看本次请求/配置的显式额度；父进程 `_result_read_limit` 已按实际额度派生，子进程没有。

## A/B/C 步骤

- A：先写两个新测试文件复现上述缺口（新进程 import guard + 缩小旧常量的 worker 结果门），并覆盖 capabilities 无 provider 导入、旧公开导入值/对象身份、协议校验语义保留、显式额度边界正反例。
- B：抽取三个纯模块，改四处边界导入，worker 结果限额改为同源派生；旧 `transcript_api` 导入保持同一对象（不复制第二份定义）。
- C：跑受影响 suite + 包级最终测试；按 `inputs/runtime_verification.md` 复制临时 runtime artifact，在空 cwd 用公开 `transcript_tool` 命令完成有限流程；独立审查 agent 审完整度/归属/回归；交付 `handoff.md`/`handoff.json`/`review.md` + 证据索引。

## 验收判据（来自卡）

- 协议层/生命周期不依赖 provider 实现；worker 不反向导入 supervisor 私有协议。
- 额度内输出不被固定表示帽拒绝；真实资源超额/超时/unknown 仍准确。
- 安装/CLI/原语言行为不变；独审通过。
- 公开 CLI 字段和 exit code 保持；FF 仍用原 CLI/wire，不要求 FF 导入新模块。


## ROOT 并线收尾（2026-11-13）

- [x] 交付代码已存在 ET main。
- [x] 主线受影响 suite 174/174 通过，独审 PASS。
- [x] ROOT PWF receipt 已提交推送，origin/main 已核实。
- [x] 保留原始转录和 .workbuddy-ai。
- 已知一个整包基线失败不属于本卡回归，按 handoff 记录保留。

## Next Step

无；MB-ET 已并入主线并推送。


## ROOT 工区清理（2026-11-13）

- [x] 两个 clean 专用 worktree 已回收，交付分支保留。
- [x] 原始转录及工具状态目录保留。
- [x] 本卡关闭，无下一步。

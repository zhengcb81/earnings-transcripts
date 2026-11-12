# MB-ET findings

## 实测基线与缺口证据（2026-11-12）

### ET-01 依赖方向

`python -B -c "import retrieval_runtime"` 在基线 commit 上的实测：

```
requests loaded: True
bs4 loaded: True
transcript_api loaded: True
transcript_extraction loaded: True
```

原因链：`retrieval_runtime.py:29 from transcript_api import (...)` → `transcript_api.py:21-25`
`import requests` / `from bs4 import BeautifulSoup` / `import transcript_extraction`。
supervisor 只用 7 个纯值（4 个 schema 字符串、3 个字节上限、1 个 settings 类型），却付出整个 provider 实现的导入代价。

`python -B -c "import retrieval_worker"`：

```
retrieval_runtime loaded: True
has MAX_RESULT_BYTES True
```

`retrieval_worker.py:24-28` 反向导入 supervisor 的 `MAX_REQUEST_FILE_BYTES` / `MAX_RESULT_BYTES` /
`WORKER_PROTOCOL`。worker 是 supervisor 的子进程，依赖方向应为「父 → 协议 ← 子」，现在是「子 → 父」。

### ET-02 固定表示帽

- `retrieval_runtime.py:51-53` 的 `MAX_RESULT_BYTES` 实测值 `153092096`（146 MiB），由全局最大值
  `6*(2*MAX_BODY_BYTES)+MAX_SOURCE_PAYLOAD_RESULT_BYTES+2MiB` 算出。
- `retrieval_worker.py:183` 用它做唯一结果写入判据：`if len(data) > MAX_RESULT_BYTES: return 2`。
- 父进程 `_result_read_limit(payload, response_bytes_left)` 已改为按**本次** `max_response_bytes` /
  `max_body_bytes` / `response_bytes_left` 派生；子进程仍用固定常量，两边不同源。
- 触发条件：请求显式 `max_response_bytes` / `max_body_bytes` 大于全局默认（`_validate_request`
  只要求 >0，`_operation_read_cap` 取 `min(declared, remaining)`，不封顶到 `MAX_FMP_RESPONSE_BYTES`），
  worker 合法结果可超过 146 MiB，被判为 worker failure。
- 复现方式（低内存，按 review 建议）：测试内把 worker 的旧常量缩小（`raising=False`，改后该属性不存在即 no-op），
  喂一个远小于显式额度的合法结果 → 基线返回 2（拒绝），改后写出结果并 exit 0。
- 现状下默认常量 146 MiB 高于合法数据上界（content 10 MiB×6 + base64 ≤ 4/3×raw），所以默认配置
  不会触发；风险在显式放大额度时。这一点在 handoff 中如实记录，不夸大成默认路径必现。

### 活动入口与依赖方向清单（开工时核对）

| 模块/入口 | 分类 | 公开面 | 状态 owner | 依赖（本仓） |
|---|---|---|---|---|
| `transcript_tool.py` | 活动交付入口 | JSON stdin/stdout：result `/1`、`/2`、discovery `/1`、`--capabilities`、exit 0 | 无持久状态（不写文件/不读 config） | retrieval_runtime、contracts/settings；transcript_api 仅 preflight/协作 fetch 路径惰性导入 |
| `scraper.py` | 活动交付入口（批量） | CLI 参数、output root 内 TXT/receipt/manifest/log/lock | 输出目录与运行账本 | common、make_interleaved、retrieval_budget、retrieval_runtime、transcript_api、transcript_artifact |
| `retrieval_runtime.py` | 活动内部模块 | `run_retrieval`、`RetrievalOutcome`、`CLEANUP_GRACE_SECONDS` | worker 生命周期、deadline、临时 workdir | retrieval_protocol、transcript_provider_settings |
| `retrieval_worker.py` | 活动内部子进程 | `main(argv)`（仅 supervisor 启动） | 单次操作 result.json | retrieval_budget、retrieval_protocol、transcript_api |
| `retrieval_budget.py` | 活动内部模块 | `BatchBudget`/`UsageCounter`/`_BudgetSession` | 请求/字节/时间计数 | 标准库 |
| `retrieval_protocol.py` | 新增纯模块 | `WORKER_PROTOCOL`、envelope 构造/校验、`result_transfer_limit` | 无（纯函数） | transcript_contracts |
| `transcript_contracts.py` | 新增纯模块 | wire schema 与默认额度常量 | 无 | 标准库 |
| `transcript_provider_settings.py` | 新增纯模块 | `ProviderSettings`、capability 描述 | 无（frozen dataclass） | 标准库 |
| `transcript_api.py` | 活动 provider 实现 | `fetch_transcript`/`discover_transcripts`/`fetch_transcript_candidate`/`list_transcript_candidates`/`preflight_transcript_fetch`、兼容重导出常量 | 无持久状态 | requests、bs4、transcript_extraction、retrieval_budget、contracts、settings |
| `transcript_extraction.py` | 活动 provider 实现 | 抽取版本与正文 | 无 | bs4、legacy_projection_comparison |
| `legacy_projection_comparison.py` | 兼容（被抽取使用） | 旧投影对比辅助 | 无 | bs4 |
| `transcript_artifact.py` | 活动支撑 | 本地原件校验/receipt | 只读原件，不写 | parser |
| `transcript_audit.py` | 活动支撑入口 | audit CLI/report | 只读语料，可选写 receipt | config、transcript_artifact |
| `translate.py`/`translator.py`/`common.py`/`cache.py`/`config.py`/`lock.py`/`naming.py`/`parser.py`/`reader.py`/`make_interleaved.py`/`eval_translation.py` | 兼容/legacy 栈 | 批量翻译/夹排/旧读取/评估入口 | config、cache、输出文件 | 本卡不改；仅 `scraper --translate` 路径使用 |
| `tools/run_request_budget_e2e.py` | 测试工具 | 手工 e2e | 临时目录 | tests fixtures |

核对结论：本轮边界缺口只在 `transcript_runtime` 的导入入口、worker 反向依赖和 tool 的 capabilities 路径；其他模块的 provider 依赖（scraper/audit/artifact）属于各自活动入口的正常依赖，未借"入口"名义忽略。`retrieval_budget.py` 未改。

### 其他已核实

- `MAX_SOURCE_PAYLOAD_RESULT_BYTES` 只在 `transcript_api` 定义、只被 `retrieval_runtime` 的固定常量使用，
  没有任何 provider 代码消费；`include_source_payload` 的 base64 实际受 `max_response_bytes` 约束。
- `transcript_tool.py:23-33` 模块级导入 `transcript_api`，因此 `--capabilities` 也会加载 `requests`/`bs4`；
  `provider_operation_capability` 本身是纯函数（只用 settings + 常量），可完全脱离 provider。
- `preflight_transcript_fetch` 依赖 `_validate_request`/`_provider_gate`，全是标准库逻辑；本卡不搬它，
  只在 `--request-stdin` 路径惰性导入，避免扩大写集合。
- 基线卡内 7 个 suite：143 passed / 149.63s（Python 3.13.9，pytest 9.1.1，`C:/Miniconda/python.exe`）。
- 仓库无 wheel/setup/安装器；按 `inputs/runtime_verification.md` 用显式 runtime manifest + 临时 artifact 验证。
- `git remote` 存在（origin, github.com/zhengcb81/earnings-transcripts.git），交付需推送分支。
- 外层 `Projects/earnings-transcripts` 只是容器目录；真实 Git 根是内层同名目录，本卡不在外层建第二个项目。

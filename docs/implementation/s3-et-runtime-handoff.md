# ET-S3 交接：统一电话会采集入口与有限批次

## 1. 分支与基线

- 基线仓：`C:\Users\郑曾波\Projects\earnings-transcripts\earnings-transcripts`，main/origin/main `4924d57044ae061d5fec3ccd4f1b7e74633f013a`（未改动，tracked 干净）。
- 本交付分支：`codex/et-s3-bounded-runtime`（基于 4924d57），worktree：`C:\Users\郑曾波\Projects\earnings-transcripts-s3-runtime`。
- 本包 PWF：`.planning/s3-et-bounded-runtime-20261003/{task_plan,findings,progress}.md`（随分支提交）。
- 基线测试：120 passed；本包完成后 151 passed + 10 个 `/2` goldens 匹配。
- 未复制进交付、也未清理的旧笔记：基线仓未跟踪 `.workbuddy-ai/`、`eval_results.json`。

## 2. 真实改动清单

| 文件 | 改动 |
|---|---|
| `scraper.py` | 重写为现代 API 薄编排：退休 `MotleyFoolScraper`/`FMPScraper` 直连 HTTP 与旧 `/api/v3`；新增期间解析、批次预算（`BatchBudget`/`_BudgetSession`/`_BudgetResponse`）、`--list`/`--dry-run` 前置只读路径、`--output` 全隔离、`_store_original` 原子保存与冲突处理、`run_manifest.json`、退出码 |
| `transcript_api.py` | 抽出共享 `_listing_candidates`/`_candidate_entries`；`discover_transcripts` 改用共享实现（行为不变）；新增进程内 `list_transcript_candidates`（metadata-only，不进 wire） |
| `tests/test_batch_runtime.py` | 新增 29 个离线契约测试（fake transport + 假 key + 临时根） |
| `tests/test_scraper.py` | `plan_action` 改为 `translate_enabled` 语义（默认 skip 不补翻译） |
| `tests/test_translation_controls.py` | 新增默认不翻译 / `--translate` 显式开启断言 |
| `README.md` | scraper 用法、期间语义、限额默认值、退出码、输出格式、测试计数 |
| `.planning/s3-et-bounded-runtime-20261003/*` | 本包 PWF |
| `docs/implementation/s3-et-runtime-handoff.md` | 本文件 |

未改动：`transcript_tool.py`、`/2` 协议、goldens、`config.yaml`、`companies.txt`、翻译/阅读器/解析模块、CWP importer。

## 3. legacy → 现代路由表

| legacy 行为 | 现在的路由 |
|---|---|
| `MotleyFoolScraper.find_transcript_urls`（直接 Session.get + 正则） | `transcript_api.list_transcript_candidates`（`_read_bounded` 流式、deadline、host 白名单、redirect 白名单） |
| `MotleyFoolScraper.scrape_transcript`（resp.text 整页解析） | `transcript_api.fetch_transcript_candidate`（绑定候选、byte cap、provenance 校验、具名错误） |
| `FMPScraper.find_and_scrape`（旧 `/api/v3`，异常吞成 `[]`） | `transcript_api.fetch_transcript`（`/stable/earning-call-transcript` 精确 FY/Q，缺 key/402/429/坏响应具名） |
| 下载后默认翻译 | 默认原语言；仅显式 `--translate` 调 `translate_after_download(skip=False)` |
| `--source both` 的多 provider fallback | 已移除（`--source {fool,fmp}` 单一 provider，不自动换源） |
| `--force` 覆盖原件 / `.cache.json` 判重 | 已移除：原件绝不覆盖（复用/`output_conflict`）；`run_manifest.json` + 磁盘文件判重 |
| “现代失败回退旧 Session”后门 | 不存在：所有 HTTP 只经 `transcript_api` |

无外部真实 caller 依赖旧类：`MotleyFoolScraper`/`FMPScraper` 仅旧 `main()` 内部使用；跨仓（FF/CWP）只走 `transcript_tool.py`。

## 4. CLI 参数 / 默认值 / 退出码

参数：`--ticker`、`--periods`、`--quarters`（legacy，未给 `--periods` 时默认 1）、
`--source {fool,fmp}`（默认 `fool`）、`--api-key`（缺省读 `FMP_API_KEY`）、
`--output`、`--list`、`--dry-run`、`--translate`、`--no-translate`/`--disable-translation`（兼容，默认即关闭）、
`--max-requests`（64）、`--max-seconds`（600）、`--max-response-bytes`（67108864）、`--max-output-bytes`（33554432）。

互斥/校验（退出码 2）：`--periods`+`--quarters`、`--list`+`--dry-run`、`--translate`+`--no-translate`、
非正/非有限限额、非法 period token（仅接受 `YYYYQ[1-4]`）。

退出码：`0` 成功（含 `not_found`/`ambiguous` 这类合法结果）；`1` 具名失败
（`unavailable`、`period_unresolved`、`candidate_discovery_unavailable`、`output_conflict`、
provider 停批等）；`2` 用法错误/单实例锁冲突；`3` 批次限额 partial
（`limit_exceeded: request_limit|batch_deadline|response_bytes|output_bytes`，
报告已完成文档）。

## 5. periods 与 recent-N 语义

- `--periods 2025Q4,2026Q1`：每个 token 精确解析为 FY/Q，逐项走 exact 请求；
  绝不解释为“最近 N 期”。
- `--quarters N`（legacy）：fool 侧先做一次 quote listing metadata，仅当条目 slug
  给出明确 FY/Q 时按最新优先展开至多 N 个期间；无法唯一确定（slug 无期间）则
  `period_unresolved`、零正文抓取、不猜 Q4；解析不出任何期间同样 `period_unresolved`。
- `--source fmp`：无 metadata 发现能力——`--list` → `candidate_discovery_unavailable`（零 HTTP），
  `--quarters` → `period_unresolved`（零 HTTP，且在锁/目录初始化之前返回）。
- `--dry-run`：零 HTTP；显确期间给本地计划（download/skip/translate），recent-N 只报
  `unknown` 条目，不虚构候选。

## 6. limits 前置与流式执行证据

- 每次 HTTP 前在 `_BudgetSession.get` 内 `BatchBudget.check_request()`：
  请求数、批次 deadline、剩余响应额度任一耗尽即抛 `BatchBudgetExceeded`，
  **不触达底层 session**（单测 `test_budget_session_*` 断言内层 calls 不增）。
- 响应在 `_BudgetResponse.iter_content` 逐 chunk 累计 `record_response`：
  超 `--max-response-bytes` 或过批次 deadline 立即抛出并经 `finally: response.close()`
  断开连接（`test_slow_stream_*` 断言 `closed is True`、`test_batch_max_response_bytes_*`）。
- 单请求参数取 `min(现代配置, 剩余批次额度)`：`timeout_seconds = max(1, min(30, 剩余秒))`、
  `max_body_bytes = min(10MiB, 剩余响应额度)`。
- 批次层在每次现代 API 调用返回后读 `budget.exhausted`，映射为具名 partial/failure 并立即停批；
  现代 API 内部无重试，限额无法被重试绕过。
- `--max-output-bytes` 在 `_store_original` 写临时文件**之前** `take_output` 核对；
  保存走同目录临时文件 + `os.replace`，任何失败路径 finally 清理临时文件，
  已有原件绝不覆盖（身份不一致 → `output_conflict`；正文一致 → `reused`）。

## 7. 现代 `/2` golden

`python tests/generate_transcript_goldens.py --check` → `10 goldens matched`；
`tests/golden/` 无 diff。`transcript_tool.py` 的 wire（`fetch`/`discover`/`fetch-candidate`、
`--include-source-payload`、`/2` 字段集、`provider_payload_sha256`、
`canonical_content_sha256/content_bytes`、publication 未知语义）完全未动，
**无需 root 协调的跨仓 diff**。`list_transcript_candidates` 仅为进程内 Python 函数，
未加入 stdin 协议。批次输出的 `run_manifest.json`/`summary.txt` 是本地文件，
不是新 wire，不产生 CWP source ID。

## 8. 测试与临时根

```powershell
C:/Miniconda/python.exe -m pytest tests/ -q          # 151 passed
C:/Miniconda/python.exe tests/generate_transcript_goldens.py --check   # 10 goldens matched
C:/Miniconda/python.exe -m ruff check scraper.py transcript_api.py tests/test_batch_runtime.py tests/test_translation_controls.py  # clean
```

- 所有新测试离线：fake `Session`/`Response`、假 FMP key（`fake-key-for-batch-test`）、
  `--output` 指向 pytest `tmp_path`；subprocess 用例走真实 CLI 退出路径但构造上零 HTTP
  （disabled provider / fmp list / dry-run）。
- 失败测试的临时根由 pytest 自动回收；`_store_original` 失败路径 finally 清理 `*.tmp-*`。
- 生产 ET 旧 output、CWP 配置、raw、catalog 全程未读未写。
- worktree 本地复制了 gitignored `config.json`（LLM keys，仅用于既有翻译器测试），未提交。

## 9. Live 权益状态（明确未知）

- 本次未读取 live FMP 凭证、未发起任何真实 paid provider 或 LLM 请求、
  未把 fake 能力报成 live 权益。
- 此前用户授权的 live canary 返回 HTTP 402；FMP `/stable/earning-call-transcript`
  的端点权益与保留权利仍为 **unknown/未验证**。现代具名失败
  （`provider_credentials_missing` / `provider_entitlement_required` / `provider_http_429`）
  保持原样，不重试、不换源。
- 生产 `DEFAULT_PROVIDER_SETTINGS` 仍是 Motley Fool disabled：真实 fool 批量路径
  零 HTTP，直到 root 侧更新 provider 配置（本包无权也不意图绕过）。

## 10. 交给 root 的验证建议

FF → ET → CWP 受影响链一次验证：原语言、FY/Q 精确性、两个 hash、限额 partial 报告、
不可用降级（disabled/402/缺 key 各具名）。`scraper.py` 批次入口不进入该链
（FF 仍走 `transcript_tool.py`），本包改动不改变其契约。

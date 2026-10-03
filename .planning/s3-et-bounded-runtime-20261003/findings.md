# 发现记录 — ET-S3 bounded runtime

## 现状（读码结论）
- `transcript_api.py`：三入口 fetch/discover/fetch-candidate，共享 `_provider_gate`（DEFAULT=motley disabled、fmp enabled）、`_read_bounded`（流式、deadline、host 校验、64KB chunk、byte cap、redirect 白名单）、具名错误映射完备。FMP 走 `/stable/earning-call-transcript` 精确 FY/Q。
- `transcript_tool.py`：stdin/stdout JSON 边界，注入点 `_session_factory`/`_provider_settings`（仅离线测试）。
- `scraper.py` 问题清单（与计划一致）：
  - MotleyFoolScraper/FMPScraper 直接 `Session.get`、`resp.text/json`，无流式/deadline/host 约束；FMP 用旧 `/api/v3`，异常吞成 `[]`。
  - `main()` 在解析 list/dry-run 前就 mkdir + setup_logging + 建 cache。
  - 默认下载后翻译（`translate_after_download` 无条件调用）。
  - `--source fmp --list`：list 分支只在 fool 路径里，fmp+list 会落到 Phase 3 下载/保存/翻译。
  - `--source fmp --dry-run`：`dry_counts` 仅在 fool 分支定义 → NameError。
  - `--quarters N` = 最近 N（按 URL 日期截断），非精确 FY/Q。
  - 单请求 timeout/sleep 不能限制整批。
  - `--output` 只改原件目录，日志/锁/缓存仍走 config 路径。

## legacy 实际调用者
- `MotleyFoolScraper` / `FMPScraper`：仅 scraper.py `main()` 内部使用；测试未直接引用 → 可随实现退休。
- 测试引用的 helper（保留）：`find_english_file`、`plan_action`、`completed_entry`、`quarter_from_filename`、`save_summary`、`save_transcript`、`translate_after_download`、`build_argument_parser`。
- 跨仓：FF/CWP 只走 `transcript_tool.py`（`EARNINGS_TRANSCRIPTS_TOOL`），不调用 scraper.py；无外部真实 CLI caller 需薄兼容。

## 设计决策
- 批次实现放 `scraper.py`（薄编排）+ 新 `batch_limits.py`？→ 决定：全部放 scraper.py 内保持单文件入口，网络细节零复制（只 import transcript_api 公共函数）。
- fool 侧批次流：listing metadata（一次）→ 期间解析/expansion → 逐期 candidate fetch（`fetch_transcript_candidate`），避免重复 listing。
  → 需要 transcript_api 新增一个“列出该 ticker 全部候选（带明确 FY/Q）”的 metadata 函数，供 `--list` 与 `--quarters N` expansion 复用；不进 transcript_tool wire、不改 `/2`。
- fmp 侧：逐期 `fetch_transcript`；`--list` → unsupported（零 HTTP）；recent-N → `period_unresolved`（零 HTTP）。
- 批次额度在 session 包装层逐次 HTTP 前核对；耗尽时抛出 → 现代 API 吞成 provider_error 后，批次层读 budget 状态转成具名 partial/failure 并立即停批（不回滚已存原件）。
- 已有原件：预检存在 → reuse（零 HTTP）；保存时目标已存在 → body 相等 reused / 不等具名 `output_conflict`，绝不覆盖。
- 翻译：仅显式 `--translate`；`--no-translate`/`--disable-translation` 兼容为关闭（默认即关闭）。

## 环境
- worktree 缺 gitignored `config.json`（LLM keys）→ 已从基线仓复制，仅本地，不入 Git；否则 `test_factory_auto_prefers_llm_when_keyed` 红。
- 生产 ET 旧 output/CWP 配置/raw/catalog 不读不写；本包测试用独立临时根。

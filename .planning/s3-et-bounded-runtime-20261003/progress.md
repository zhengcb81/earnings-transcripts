# 进度 — ET-S3 bounded runtime

## 2026-10-03
- T1 完成：基于 4924d57 建 `codex/et-s3-bounded-runtime`，worktree 到 `C:\Users\郑曾波\Projects\earnings-transcripts-s3-runtime`；PWF 三文件落 `.planning/s3-et-bounded-runtime-20261003/`；基线 `pytest tests/ -q` = 120 passed（worktree 需本地复制 gitignored config.json，已做、不入 Git）。
- T2 完成：读完 transcript_api.py、transcript_tool.py、scraper.py、tests、config、README、goldens。
  - legacy 类调用者：MotleyFoolScraper/FMPScraper 仅 scraper.main 内部 → 随实现退休；测试只引用 helper（保留）；跨仓只用 transcript_tool.py，无外部 scraper CLI caller。
- T3 完成（RED）：`tests/test_batch_runtime.py` 29 用例 + plan_action/翻译开关更新，首跑 32 failed 确认红灯。
- T4/T5 完成（GREEN）：
  - `transcript_api.py`：共享 `_listing_candidates`/`_candidate_entries`，新增进程内 `list_transcript_candidates`（不进 wire）；`discover_transcripts` 行为不变。
  - `scraper.py` 重写：薄编排 + `BatchBudget`/`_BudgetSession` 流式限额 + `--list`/`--dry-run` 前置只读 + `--output` 全隔离（`_cfg_with_output` 绝对路径覆盖 paths）+ `_store_original` 原子保存/复用/`output_conflict` + `run_manifest.json` + 退出码 0/1/2/3。
  - 全量 `pytest tests/ -q` = **151 passed**；`generate_transcript_goldens.py --check` = **10 goldens matched**；ruff 对本次改动文件 clean（test_scraper 3 个旧项为基线已有）。
  - 真实 CLI 离线冒烟：`--help`、`--dry-run`（rc0 零写入）、fmp `--list`（rc1 零写入）、默认 fool disabled 真实运行（rc1 零 HTTP、全部写入在 --output）、fmp recent-N（rc1 零写入）。
- T6 进行中：README 已更新；`docs/implementation/s3-et-runtime-handoff.md` 已写；待 commit/push。

## 关键实现笔记（供复核）
- 批次耗尽发生在现代 API 内部会被其 catch-all 吞成 provider_error；批次层以 `budget.exhausted` 为准转具名 partial/failure 并立即停批。
- 单请求 timeout = `max(1, min(30, int(剩余批次秒)))`；批次 deadline 同时在包装层逐 chunk 校验，慢流不能超时逃逸。
- `--output` 通过把 cfg.paths 的 transcripts/logs/lock/translate_cache 指成绝对路径实现全覆盖；不改 config.yaml。
- 日志用模块 logger `scraper` 每次运行重建 handler（FileHandler 关闭于 finally），避免 basicConfig 全局一次性的坑。


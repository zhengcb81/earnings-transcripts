# 进度 — ET-DEADLINE

## 2026-10-04
- T1 已建：`codex/et-s3-deadline` 从 `93fe52c` 建唯一 worktree
  `C:\Users\郑曾波\Projects\earnings-transcripts-s3-deadline`；PWF 落
  `.planning/s3-et-deadline-20261004/`。
- Stage 1 完成：基线 `pytest tests/ -q` = **151 passed**（worktree 本地复制 gitignored
  `config.json` 后全绿，未入 Git）、`generate_transcript_goldens.py --check` = **10 goldens matched**；
  owner 目录 `.workbuddy-ai/`、`eval_results.json` 未复制未清理；主仓 main 未动。
- 摸底结论记入 findings.md：同步阻塞 + `_request_timeout` 抬到 ≥1s 构成缺口；
  worker 需要预算类但不能拖 translator 栈 → 抽 `retrieval_budget.py`；goldens 走
  `_session_factory` 合作式路径故字节不变；测试缝 = 私有 `_retrieval_launcher/_spec/_temp_root`。

## 2026-10-04（Stage 2–4 完成）
- RED 确认：三个目标测试文件因 `ModuleNotFoundError: retrieval_runtime` 首跑全红。
- GREEN 实现：
  - `retrieval_budget.py`（抽出 `BatchBudget`/`_BudgetSession` + `record_request`/
    `usage_snapshot`/`apply_usage`/`stop_with`/`mark_usage_unknown` + 单操作 `UsageCounter`；
    scraper 以原名 re-export，旧单测语义不变）；
  - `retrieval_runtime.py`（supervisor：monotonic 统一 deadline、float 剩余等待、
    terminate→kill 共享 1s 宽限、确认退出才删临时目录、`_load_result_file` 六类校验 +
    超期拒收、`retrieval_cleanup_failed` 具名报告）；
  - `retrieval_worker.py`（内部 worker：现有 API + 预算包装 + 原子结果信封，key 只走环境）；
  - `transcript_tool.py` 无注入路径、`scraper.py` 的 list/fool/fmp 全部接 `_supervise`。
- 调试记录：
  - worker 内 `RuntimeError` 会被 API catch-all 吞成 `unexpected_provider_failure`
    → 坏退出用例改 `SystemExit`（不被 `except Exception` 捕获）；
  - 实测 worker spawn→首个 HTTP ≈ 1.1–1.5s（requests 0.55s + bs4 0.38s 导入为主），
    去掉测试模块顶层 `import pytest` 省 ~0.5s，各测试预算改为实测 ~2 倍余量
    （3.0/4.0/5.0/6.0/7.0s 档），避免 marker 未达即被杀的假红灯。
- 集中责任包（lane §6 命令）：**92 passed**；全量 `tests/ -q` = **172 passed**；
  goldens **10 matched**；ruff clean；`git diff --check` exit 0。
- 生产冒烟（零外发）：tool 缺 key/坏 JSON/误用 = rc 0/0/2 输出稳定；
  scraper dry-run rc0 零写入、fmp 缺 key rc1 具名、默认 fool disabled rc1 具名。
- 文档：README（硬截止/`--max-seconds` 范围/usage 未知/172 计数）、
  `docs/implementation/s3-et-deadline-handoff.md` 已写；`_bench_*.py` 与 smoke 目录已删。

## 待办
- commit/push 分支 `codex/et-s3-deadline`（不并 main）。

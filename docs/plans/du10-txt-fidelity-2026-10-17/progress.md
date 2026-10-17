# DU10 进度日志

## 2026-10-17 会话 1（作者）

- 建 worktree `C:/Users/郑曾波/Projects/earnings-transcripts-du10-txt-fidelity`，分支 `codex/du10-et-txt-fidelity` @ `7d06777e250ee3f7ae58f0be2cee62944bd2c104`。
- 勘察：数据流/白名单/既有契约/真实语料（见 findings.md）。
- 基线测试（基线 SHA 下）：`python -m pytest tests/test_du7_extraction_behavior.py tests/test_du7_extraction_edges.py tests/test_du7_extraction_e2e.py tests/test_du7_extraction_wiring.py tests/test_root_du7_extraction_robustness.py tests/test_du9_legacy_comparison.py -q` → **284 passed**（39.83s）。
- RED 实验 REPRO-1..6 完成（findings.md），根因定位到 `_collect_runs` 所有权白名单与缺 EOF 截断检测。
- PWF 三件套创建。
- Phase A：`tests/test_du10_transcript_fidelity.py` + 6 夹具 + expected.json（手工推导，机械回溯校验 ALL TRACEABLE 后才运行）。
- RED 首跑 7 failed：其中 speaker_glue 期望 `/1` 是**作者期望值错误**——v1 `get_text(" ")` 在标签边界注入空格而 v2 保真原字节，页面诚实 `/2`；修正期望值并记录该发现（v1 曾隐式改写无空格粘连，v2 不改写）。
- RED 终态 6 failed / 4 passed，精确对应 RC-1（3 个恢复夹具）、RC-2（截断 fail-closed）、缺失 Q&A 标题顺序、CJK 透传；4 个冻结钉定（胶水字节、br/时间戳/免责 `/1` 对照、孤立 `<`、冻结契约常量）通过。收据 `receipts/red_run.txt`。
- Next: Phase B 实施。

## 测试结果台账

| 时点 | 套件 | 结果 |
|-----|------|------|
| 基线 7d06777 | DU7×4 + robustness + DU9 | 284 passed |

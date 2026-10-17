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

## 2026-10-17 会话 2（Phase B/C）

- Phase B 提交 `71103cf`：`_V2_OWNERS`（v2 所有权扩展，comparator 仍收冻结 `_BLOCK_TAGS`）+ EOF 悬挂标记 `truncated_markup` fail-closed。DU10 10 passed；旧 DU7+DU9 284 passed。
- 全仓回归：608 passed（不含时序敏感）+ test_batch_runtime/test_retrieval_cli_e2e 47 passed = 655 全绿；ruff `transcript_extraction.py tests/test_du10_transcript_fidelity.py` 全绿。
- 真实语料只读验证：分母 = 6 家 43 份 `*_earnings_call.txt`；深检 3 份（MSFT EN 152¶、NVO EN 301¶、TAL 中企 90¶），段落回溯零失败；合成 MF 形状标记 + 真实内容往返逐段精确一致（MSFT/NVO/TAL 全 exact，诚实 `/2`）；CJK 头部字面完好。首轮往返夹具曾自插空格造成伪差，改为字节忠实拼接后全 exact（夹具缺陷非解析器缺陷）。
- 清理：130 语料文件 SHA 不变；主树 `.workbuddy-ai/`、`eval_results.json` 哨兵不变；temp 残留 0。收据 `receipts/cleanup.md`。

## 2026-10-17 会话 3（Phase D 独立审查）

- 提交 `fc91325`（receipts + handoff.md）冻结后交独立审查员（general-purpose agent，与实施者不同上下文）。
- 审查范围核验：18 个变更文件全在白名单内；独立 RED 复现（临时 worktree @7d06777 只装作者测试+夹具）= 6 failed/4 passed 与收据逐项一致；DU10+旧 goldens 294 passed；全仓 561 passed；ruff 全绿；截断检测 10 种完整特殊尾页零误报、7 种截断形态全拦；9 例新对抗样张内容/次序/回溯/版本标签全过；130 语料 SHA 复算一致；主树哨兵核实。
- 审查判定 **PASS、零未解决**；7 条低度记录性备注（`review/independent_review.md/json`）。
- 按备注整改：补 `handoff.json`（含 reviewer 与所审 SHA）、cleanup.md 记载目录聚合 SHA 复算方法、progress 台账补全；随后请同一审查员按新最终 SHA 复核收尾。

## 测试结果台账

| 时点 | 套件 | 结果 |
|-----|------|------|
| 基线 7d06777 | DU7×4 + robustness + DU9 | 284 passed |

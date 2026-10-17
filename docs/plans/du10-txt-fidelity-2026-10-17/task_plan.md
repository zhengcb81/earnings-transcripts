# DU10-ET-TXT-FIDELITY 任务计划

- 卡片: `company-wiki/docs/plans/document-understanding-2026-10-13/parallel/du10-2026-10-17/du10_et_txt_fidelity.md`
- 仓库: `C:/Users/郑曾波/Projects/earnings-transcripts-du10-txt-fidelity`（独立 worktree）
- 分支: `codex/du10-et-txt-fidelity`
- 基线: main `7d06777e250ee3f7ae58f0be2cee62944bd2c104`
- 基线文件 SHA256: `transcript_extraction.py` = `9a9c2f3fb28309d888539dc947beeba08fc566ee4dda463513f97adbb1439083`；`legacy_projection_comparison.py` = `2f9acacf6d11f23f495a530e4eba82997c3019d20124e2c8a9f6c0fb2051b661`（git blob `0b76980d820e212fed52eac482e64ec30f9bb570`）
- 基线测试: DU7+DU9 相关 6 个套件 284 passed（2026-10-17 记录，见 progress.md）

## Goal

验证并修复 ET 转写文本解析（HTML DOM 抽取 → 下载 TXT/JSON 的源头）中的内容、说话人与段落边界丢失，使 CWP 可从原语言文本形成可靠叙述摘要。不重做 comparator；不把字节相等当语义正确；不引入翻译。

## Scope（白名单）

- 可改源码：`transcript_extraction.py`（仅此一个）
- 新增测试：`tests/test_du10_transcript_fidelity.py`、`tests/fixtures/du10_transcript_fidelity/`
- 交接：本 PWF（handoff.md/json、receipts/）
- 只读：`transcript_tool.py`、下载/provider/HTTP（`transcript_api.py`/`scraper.py`）、预算/限流、翻译、CWP、FF、`legacy_projection_comparison.py`（DU9 交付，冻结）
- ET 主树 `.workbuddy-ai/`、`eval_results.json` 未跟踪物不动；真实本地转写只读

## 已确认根因（证据见 findings.md）

- RC-1 内容/边界丢失：v2 所有权白名单 `_BLOCK_TAGS = {p,h2,h3,li}` 之外的块级结构（h4/h5/h6 标题、div 直挂文本、table 单元格）的全部文本被静默丢弃；丢失页仍报告 `/1` 合规版本（字节相等掩盖语义缺失——卡片明令禁止的情形）。
- RC-2 截断生成貌似完整正文：以悬挂标记截断的输入产出以半句结尾的"完整"正文，无任何失败信号；卡片要求故障/截断路径不生成貌似完整 transcript。
- 冻结不动（兼容契约钉定，仅补钉定测试+文档）：`<br>`→空格折叠（DU9 br 形状钉定字节等于 v1）、说话人标签与正文无空白时的原字节粘连（源 DOM 本身无空白，插入空格才是改写）。

## Phases

### Phase A — 基线与行为 RED — Status: complete
- 冻结基线 SHA/goldens/测试结果（本文档头部 + progress.md）。
- 新增 `tests/test_du10_transcript_fidelity.py` RED 行为测试（RC-1/RC-2 + 冻结行为钉定），先证 RED。
- RED 收据存 `receipts/red_run.txt`。

### Phase B — 最小根因修复 — Status: pending
- 解耦 v2 所有权集合（扩展块级所有权）与冻结 legacy 契约集合（`_BLOCK_TAGS` 原样传 comparator）。
- EOF 悬挂标记截断检测 → `ExtractionError`（typed，沿用既有 error contract）。
- 旧 goldens/DU9 兼容必须继续全绿；ruff lint。

### Phase C — 全量样本与清理 — Status: pending
- 本卡全量套件 + DU9 比较套件 + DU7 套件 + 相邻回归（transcript_api/artifact/audit/scraper/common）。
- 真实本地样本只读验证：英文（MSFT/NVO）+ 中文或中英混合（TAL 等），核对正文可回溯原文件行、次序稳定；覆盖分母与未测项记录。
- 清理 owned temp；核对 ET 主树未跟踪物与原始 TXT SHA 不变。

### Phase D — 独立审查与交接 — Status: pending
- 冻结最终 HEAD；由不同 agent 审查代码/测试/定位/边界/无翻译/旧 goldens/目录保护/PWF。
- 审查报告 PASS 且零未解决方可交接；否则修复后按最终 SHA 再审。
- 产出 handoff.md/json + receipts。

## Decisions Made
- 2026-10-17: 不扩展 `_BLOCK_TAGS` 本身（会经 `legacy_projection_matches(block_tags=...)` 篡改冻结 v1 投影语义）；新增独立 v2 所有权集合。
- 2026-10-17: br 折叠与无空格粘连不改：前者被 DU9 字节兼容金钉定，后者插入空白才是对原字面的改写；均以钉定测试+局限记录。
- 2026-10-17: 不给 ExtractionResult 加 provenance 字段（消费契约不变）；"每个定位可回溯"以测试按构造保证（正文段落=原文档片段依序拼接）。

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| （暂无） | | |

## Next Step
Phase B：在 `transcript_extraction.py` 实施所有权集合解耦 + EOF 截断检测，跑 RED→GREEN。

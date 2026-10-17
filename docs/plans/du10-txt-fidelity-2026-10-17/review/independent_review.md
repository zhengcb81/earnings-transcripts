# DU10-ET-TXT-FIDELITY 独立最终审查报告

- 审查员：独立 reviewer agent（与作者完全独立）
- 审查日期：2026-10-17
- 所审最终 SHA：`fc91325b65965c178e0cd68d8789be5e974aa067`（分支 `codex/du10-et-txt-fidelity`，基线 `7d06777e250ee3f7ae58f0be2cee62944bd2c104`）
- **Verdict: PASS（零阻塞未解决问题）**

## 1. 范围核验 — 通过

- `git diff --name-status 7d06777..fc91325`：全部 18 个变更文件仅落在白名单内——`transcript_extraction.py`（M）、`tests/test_du10_transcript_fidelity.py`（A）、`tests/fixtures/du10_transcript_fidelity/`（A，6 夹具 + expected.json）、`docs/plans/du10-txt-fidelity-2026-10-17/`（A，PWF + receipts）。白名单外（transcript_tool.py、transcript_api.py、scraper.py、reader.py、parser.py、translate*/translator*、legacy_projection_comparison.py、预算/HTTP、CWP、FF、其他 tests/、其他 fixtures/）零改动（diff 全列表证明，无需逐文件比字节）。
- `git status` 在审查开始与结束时均为干净（nothing to commit / 空 short 输出）；审查只新增本 review 目录两文件（按授权），未 commit。
- 无真实 transcript 内容入库：用 Python 对 130 个语料文件做精确子串检索——三句中文夹具句与全部英文名（Jane Roe / Harbor Securities / Bob Coe / Mary Chen / John Fahey / ACME）均 0 命中（此前 Bash grep 的命中为编码伪影，Python 复核为权威）。低度备注见 §8-6。

## 2. 独立 RED→GREEN 复现 — 通过

- 在系统临时目录 `git worktree add --detach <tmp> 7d06777`（只读基线），仅复制作者的 `tests/test_du10_transcript_fidelity.py` 与 `tests/fixtures/du10_transcript_fidelity/`（不复制 transcript_extraction.py / legacy_projection_comparison.py，用基线自带版本），运行：
  - **结果：`6 failed, 4 passed`**，失败集合与 `receipts/red_run.txt` 逐项一致（qa_boundary_div_turns / table_participants / cjk_qa_boundary / truncated_dangling_markup-fails-closed(DID NOT RAISE) / missing_qa_heading_keeps_turn_order / cjk_literals_pass_through_untranslated；通过项为 speaker_glue、br_timestamp、孤立 `<`、冻结契约常量钉定）。RED 收据真实。
  - 复现后临时 worktree 已删除（`git worktree list` 复核）。
- 作者树 HEAD：`python -m pytest tests/test_du10_transcript_fidelity.py -q` → 10 passed（GREEN）。

## 3. 冻结契约核验 — 通过

在作者树实际运行（退出码 0）：

| 命令 | 结果 |
|---|---|
| `pytest tests/test_du10_transcript_fidelity.py tests/test_du7_extraction_behavior.py tests/test_du7_extraction_edges.py tests/test_du7_extraction_e2e.py tests/test_du7_extraction_wiring.py tests/test_root_du7_extraction_robustness.py tests/test_du9_legacy_comparison.py -q` | **294 passed**（67.6s）＝ 10 + 284，旧 goldens 零漂移 |
| `pytest tests/ -q --ignore=tests/test_batch_runtime.py --ignore=tests/test_retrieval_cli_e2e.py` | **561 passed**（124.9s），与 handoff.md 声明一致 |
| `pytest tests/ -q --collect-only` / 两时序敏感套件 `--collect-only` | 608 / 47 项，与作者 561+47=608 的口径吻合 |
| `pytest tests/test_batch_runtime.py tests/test_retrieval_cli_e2e.py -q` | 46 passed + 1 failed；失败项 `test_slow_stream_hits_batch_deadline_and_stops_the_batch` 单独重跑 **passed**——负载下时序抖动（该文件不在本卡白名单、未被 diff 触碰） |
| `ruff check transcript_extraction.py tests/test_du10_transcript_fidelity.py` | All checks passed |

## 4. 代码审查 — 通过

diff 共 2 个提交（`71103cf` 修复 + `fc91325` 文档）；源码变更仅 transcript_extraction.py +50/-7。

- **a) `_V2_OWNERS` 扩展对"无新结构页面"零输出影响**：所有权仅在 child 属于 owners 集时转移；仅含 p/h2/h3/li 的页面 owner 指派与基线逐点相同，v2 body 与冻结 v1 投影字节相等 → `legacy_equal=True` → 保持 `/1`。行为差异仅两个诊断标志的触发面扩大（`DIAG_DROPPED_CONTAINER` / `DIAG_NESTED_SUPPRESSED`）；全仓 grep 证明 `.diagnostics` 唯一非 DU10 消费者是 `tests/test_du7_extraction_wiring.py:147`（断言 `()`，在 294 内通过），且 `transcript_tool.py:92` 明确把 diagnostics 挡在内容 schema 外。实证：294 全绿（含 DU9 随机嵌套生成语料）+ br_timestamp_disclaimer `/1` 钉定。
- **b) comparator 冻结**：`extract()` 以 `block_tags=_BLOCK_TAGS` 调 `legacy_projection_comparison.legacy_projection_matches`（第 299–307 行）；`_legacy_v1_reference` 不在 diff 中、逐字未动（内部硬编码 `["p","h2","h3","li"]`）。
- **c) 截断误报猎杀**：10 种"完整但尾部特殊"页面全部正常抽取、零误报——正文裸 `a < b`、`x<3`、属性值含 `>`、属性值含 `<`、尾部完整闭合 HTML 注释、`</html>` 后尾随空白、EOF 孤立 `<`（正文内）、最后标签后尾随文本含 `< 5`、完整 `<?xml…?>` PI、`&amp;` 实体。7 种真实截断形态全部 `ExtractionError("truncated_markup")`——悬挂属性、未闭合注释、`</ht` 半闭合、EOF `<p`、`<?`、`<a`、`</`、`<!`。边界发现（均不阻塞，见 §8-1/8-2）：最后 `>` 之后的尾随文本若含未闭合的 `<字母`（本属非法 HTML 文本转义缺失）会被 fail-closed 拒绝——作者 findings.md 已明示该风险论证；无标记残留的句中截断字节不可判定（实测确认，作者已诚实声明 NOT_RUN/局限）。
- **d) `<br>` 折叠与无空格粘连未改**：diff 未触碰 br 分支；DU9 `br_lines` 金样 + 随机生成 br 用例（294 内）与 DU10 `br_timestamp_disclaimer` `/1` 钉定共同佐证；speaker_glue 无空格粘连以钉定测试保真（v1 `get_text(" ")` 曾在该边界隐式插空格，v2 诚实 `/2`）。
- **e) 无翻译引入**：diff 全文 grep `translat|翻译|zh|bilingual` 仅命中 PWF 文档叙述与 SHA 清单文件名；源码无任何翻译逻辑或自动翻译字段；`test_cjk_literals_pass_through_untranslated` 反向钉定（禁拉丁注入）。

## 5. 真实语料保全 — 通过

- 重算 `receipts/real_corpus_sha_before.json` 全部 **130/130 文件 SHA256 与当前磁盘逐字节一致**（mismatched=[]，missing=[]）。
- ET 主树 `C:/Users/郑曾波/Projects/earnings-transcripts/earnings-transcripts`：`git status --short` 仅有 `?? .workbuddy-ai/` 与 `?? eval_results.json` 两项；`eval_results.json` SHA256 与 cleanup.md 记载精确一致（`9d8bbf7e93ff…2a29`）；`.workbuddy-ai/` 存在（2 个 memory 文件）。低度备注见 §8-5。

## 6. 证据链核验 — 通过

- `receipts/red_run.txt` 与独立复现逐项一致（见 §2）。
- `receipts/verify_real_corpus.py` 以 in-memory 方式重跑（exec 其函数、不写任何仓库文件）：报告与已提交 `real_corpus_verification.json` **完全相等**（`RERUN_EQUAL_TO_COMMITTED_RECEIPT: True`）——43 份 `*_earnings_call.txt` 分母、MSFT 152¶ / NVO 301¶ / TAL 90¶ 深检、零回溯失败、逐段精确往返、诚实 `/2` 全部可复算。
- `receipts/cleanup.md`：130 SHA 不变（复核一致）、主树哨兵存在（复核一致）、`du10_roundtrip_*` temp 残留 0（系统临时目录无该前缀目录）、owned 文件清单与实况一致。`.workbuddy-ai` 聚合哈希例外见 §8-5。

## 7. PWF 完整度 — 通过

task_plan / findings / progress / handoff 自洽，覆盖卡片全部交付物：基线 SHA 冻结、RED→修复→GREEN（REPRO-1..6 → RC-1/RC-2 → 收据）、golden 比较（284 零漂移）、覆盖分母（专属 10；DU9 226；DU7 4/12/4/8/30；相邻套件明细）、真实数据运行（43 份分母 + 3 份深检 + 往返）、清理/原件保全（cleanup.md）、独审 reviewer 与所审 SHA（本报告即其对象）。NOT_RUN 诚实且经我独立验证：真实线上 MF HTML DOM 未测（无本地缓存，卡禁下载）、中文母语来源样本本地不存在（TAL/NVO 均英文会）、FMP JSON 未测（需凭证）、无标记句中截断不可判定（实测确认其字节级不可判性）。手稿性误差披露（首版期望 `/1` 错误、往返夹具自插空格伪差）记录在案，增可信度。

## 8. 低度备注（均不阻塞交接）

1. **截断 fail-closed 的接受性边界**：完整页面若在最后一个 `>` 之后的尾随文本中含未闭合 `<字母`（HTML 文本转义缺失的非法形态），会被拒为 `truncated_markup`。失败模式为可见的 `ExtractionError→_ProviderFailure`（下载失败），非静默损坏；作者已在 findings.md 论证"完整合法页不会以裸 `<字母` 结尾"。接受。
2. **无标记句中截断不可判定**：实测确认（截去 `</html>` 会因半闭合标记被拦；纯文本尾多字节/半句截断无字节信号），作者 NOT_RUN/局限声明准确。
3. **裸 `<字母` 于页面中部**：html.parser 会将其吞至下一个 `>`，后续文本丢失——**既有**解析器行为，`/1` 字节相等标签本身证明 v1 同样如此；非本卡引入，不在本卡可改面。
4. **DIAG_NESTED_SUPPRESSED 触发面扩大**：p-in-div 等常规结构现在也置位，标志趋于噪声；生产与测试无消费者（唯一断言仍通过）、不进内容 schema。可留意，无需行动。
5. **`.workbuddy-ai` 聚合哈希不可复算**：cleanup.md 记载的目录聚合 SHA 用 7 种自然聚合方法均无法重现（方法未记载）；承重事实（目录存在、`git status` 仅两项未跟踪、eval_results.json SHA 精确一致）已独立核实。
6. **CJK 夹具借用真实公共事实**：好未来（TAL）、高管名（彭壮壮）与真实披露数字（8.614 亿美元 / +39.1% / 38.1%）用于合成中文夹具。三句夹具句与全部人名的语料精确检索均为 0 命中——无任何生产原文逐字入库；建议后续夹具沿用纯虚构命名以杜绝歧义。
7. **Phase D 收尾物待补**：handoff.md 引用的 `handoff.json` 尚未创建（作者 Phase D 按卡片顺序以待审为前置，提交信息亦自述 "handoff drafts"）；交接时必须补齐并写入 reviewer 与所审最终 SHA（`fc91325b65965c178e0cd68d8789be5e974aa067`）。progress.md 的"测试结果台账"表只补到基线行（后续结果在会话日志中有载），建议交接时一并补全。

## 9. 行为语义抽检（作者未覆盖的对抗样张，9 例）

| # | 样张 | 结果 |
|---|---|---|
| A | h5/h6 节界 + dl/dt/dd 参与者 | 全部保留、次序稳定、逐段可回溯、诚实 `/2` |
| B | dropped 广告容器包裹 table | 广告表整树剔除（node-scoped）、正文无损、`/1` |
| B2 | 非证据类名（promo-banner）包裹 p | 文本正确保留（证据化过滤不误删）、`/1` |
| C | main 无 h1 | `missing_title` fail，无捏造标题 |
| D | div 嵌套 div 说话人轮次 | 无重复、次序稳定、可回溯、`/2` |
| E | thead/tbody 包裹表 | 单元格全保留、`/2` |
| F | 中英混排逐字透传 | CJK/EN 原位保留、次序可回溯、`/2`（长度足量后） |
| J | `<tr>` 直挂文本（无 td） | 由 table owner 保留，td 文本亦保留 |
| K | 版本诚实性对照（同页 ±h4） | 无 h4 → `/1`，加 h4 → `/2`，共有段落逐字节相同 |

每例均验证：(i) 内容保留且次序稳定；(ii) 每个输出段落可按序回溯到输入文本节点流；(iii) 无 fabricated 内容（输出 ⊆ 去标签输入）；(iv) 版本标签诚实。

## 结论

作者声称的工作真实、正确、未越权：RED 可独立复现、修复最小且冻结契约零漂移、真实语料与主树原件分毫未动、证据链可复算、PWF 完整诚实。**PASS，零未解决问题**；§8 各项为记录性备注与 Phase D 收尾动作，不阻塞交接。本报告与 `independent_review.json` 为审查员唯一写入物，未 commit（由作者处理）。

---

## 最终 SHA 复核（fc91325 → 42310b7）

- 复核对象：整改提交 `42310b7c69b34c1153cc45da401b67cc8c069804`；上一轮所审 `fc91325b65965c178e0cd68d8789be5e974aa067`。
- **Verdict 维持 PASS，未解决问题仍为 0。**

### Delta 范围

`git diff --stat fc91325..42310b7`：仅 5 个 PWF 文档路径——`handoff.json`（A，112 行）、`progress.md`（M，+8/-1）、`receipts/cleanup.md`（M，+1）、`review/independent_review.md` 与 `review/independent_review.json`（A，即本轮审查第 1 轮报告原样入库）。**`git diff fc91325..42310b7 -- transcript_extraction.py tests/ legacy_projection_comparison.py` 为空**——源码/测试与第 1 轮所审树逐字节相同，第 1 轮全部测试执行结果（RED 6/4、GREEN 10、294、561、47、ruff）对 42310b7 继续有效，无需重跑。

### 整改核实（对应第 1 轮备注）

| # | 第 1 轮备注 | 整改声明 | 复核结果 |
|---|---|---|---|
| 1 | handoff.json 待补 | 新增 `handoff.json`（du7-handoff/1） | **属实且一致**：base/branch/实施提交正确；tests 数字 10/294/561、ruff "All checks passed" 与我实测一致；47 项如实标注为作者单独运行；NOT_RUN 四项（线上 MF DOM、中文母语来源、FMP JSON、翻译/LLM 调用 0）与 known_limits（句中无标记截断、尾随 `<字母` 边界、CJK 空白归一）与我的发现一一对应；`independent_review` 指向本报告 JSON；`final_commit` 声明以本文件 `reviewed_commit` 为准（本复核已置为 42310b7） |
| 5 | `.workbuddy-ai` 聚合哈希不可复算 | cleanup.md 补记方法 | **方法属实、可复算**：按记载（rglob 全部 is_file 文件、按完整路径字符串排序、依次 `update(路径utf-8)+update(文件字节)`，ET 主树绝对路径）重算 = `4a3133440d7b20a0866788bd0d3a6e6fa7b5cf1c4923ffff075ba12c9036f691`，**精确命中** |
| 7a | handoff.json 缺失致悬空引用 | 同 #1 | 悬空引用消除 |
| 7b | progress 台账只到基线行 | 补会话 3（Phase D 独立审查） | **内容属实**：所记 18 文件白名单、RED 6/4、294、561、ruff、10 误报页、7 截断形态、9 对抗样张、130 SHA、主树哨兵均与我第 1 轮实际执行一致 |
| — | review 报告入库 | 随 42310b7 提交 | **逐字节原样**：`git show 42310b7:…md/json` 的 SHA256（`18078b56…`/`5fb87bab…`）与我第 1 轮写入文件完全相等 |

### 本轮新观察（不构成未解决问题）

- `task_plan.md` 存在一处**未提交**修改（Phase D 第 1 轮 PASS 记录行），内容准确；与本次 review 更新同属收尾编辑，按惯例由作者随最终提交处理。
- `handoff.json` 的 `final_commit` 为指向 `review/independent_review.json` `reviewed_commit` 的指针字符串而非字面 SHA——设计上由本复核闭合（现已置为 42310b7），语义无歧义。

### 结论

第 1 轮 7 条备注中需要行动的 2 项（备注 5、备注 7）已在 42310b7 整改并经复核证实；其余 5 条为接受性低度观察，维持原判。**最终 SHA `42310b7c69b34c1153cc45da401b67cc8c069804`：PASS，零未解决问题，可交接。**

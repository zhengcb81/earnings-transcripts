# DU10-ET-TXT-FIDELITY 交接

- 分支 `codex/du10-et-txt-fidelity`；基线 main `7d06777e250ee3f7ae58f0be2cee62944bd2c104`；实施提交 `71103cf`（最终 SHA 见 handoff.json `final_commit`，审查冻结以它为准）。
- 供应商调用 0；生产写入 0。

## 做了什么 / 根因

**RC-1 内容/说话人/段落边界丢失**：`transcript_extraction._collect_runs` 的所有权仅在 `p/h2/h3/li` 转移，其余块级结构的直挂文本（h4/h5/h6 节界标题如 "Question And Answer/问答环节"、容器 div 的说话人轮次、表格单元格的参与者/指标）全部静默丢弃。冻结 v1 投影有同洞 → 字节相等 → 页面仍报 `/1`，丢失被合规标签掩盖（卡片明令禁止的情形）。
**修复**：新增 `_V2_OWNERS`（标准块级容器全集：全部标题层级、p/li、div/section/article/blockquote/figure/details/address/hgroup/center/pre、ul/ol/dl/dt/dd、table/caption/td/th）。**comparator 与 `_legacy_v1_reference` 仍收冻结 `_BLOCK_TAGS`**——没有无直挂文本结构的页面 v2 输出逐字节不变、保持 `/1`（旧 goldens 全绿实证）；有恢复内容的页面诚实报 `/2`。

**RC-2 截断生成貌似完整 transcript**：以悬挂标记（如 `<p class="trunc`）截断的输入被 html.parser 静默补全，产出貌似完整正文。**修复**：`_ends_in_unfinished_markup`（EOF 尾部 `<`+`[a-zA-Z/?!]` 且其后无 `>`）→ `ExtractionError("truncated_markup")` fail-closed。孤立 `<`（文本字符）不误报（钉定测试）；`<!--` 未闭合注释同样拦截。

**不改的冻结行为（钉定测试+局限）**：`<br>`→空格折叠（DU9 字节兼容金钉定）；说话人标签与正文无空白的原字节粘连（源 DOM 字面如此，v1 `get_text(" ")` 曾在该边界隐式插空格，v2 不改写并诚实报 `/2`）。

## 接口

- 输入不变：`extract(page: bytes) -> ExtractionResult(title, body, extraction_version, diagnostics)`；错误仍 `ExtractionError(reason)` → `transcript_api._ProviderFailure()`（新增 reason 值 `truncated_markup`，错误契约机制不变）。
- 输出契约不变：`EXTRACTION_VERSION_LEGACY="motley-fool-main-text/1"`、`EXTRACTION_VERSION="motley-fool-main-text/2"`、`MIN_CONTENT_CHARS=200`、`LEGACY_MIN_PARAGRAPH_CHARS=5`、`SKIP_TEXT`、`_BLOCK_TAGS` 全部冻结（`test_frozen_compatibility_surface_unchanged` 钉定）。
- 未新增 provenance/翻译字段；"每个定位可回溯原文、次序稳定"由测试按构造保证（每条期望段落须在夹具原文文本节点流中按序找到）。

## 覆盖（对照卡片缺口）

| 缺口 | 覆盖 |
|---|---|
| 管理层陈述与问答分界 | qa_boundary_div_turns / cjk_qa_boundary（h4 节界恢复） |
| 说话人标签/角色与相邻文本绑定 | label+speech 同段钉定（有空格/无空格两形态，胶水字节保真） |
| 多说话人连续轮次 | Operator→Analyst→CEO 连续轮次顺序断言 |
| 中文/英文或中英混合 | cjk_qa_boundary 全中夹具 + `test_cjk_literals_pass_through_untranslated`（禁拉丁注入）+ 真实 TAL（中企英会）往返 |
| 标题/免责声明/主持人过场 | br_timestamp_disclaimer（时间戳/免责声明/Operator 轮次 `/1` 对照）；h1 标题各夹具断言 |
| 缺失 Q&A 标题 | test_missing_qa_heading_keeps_turn_order |
| 换行/标点/时间戳 | br 冻结折叠 + 时间戳字面逐字保留 |
| 截断与畸形输入 | truncated_dangling_markup fail-closed + 孤立 `<` 不误报 + DU7 malformed 既有钉定保持 |

## 测试结果（命令与退出码 0）

| 套件 | 结果 | 收据 |
|---|---|---|
| RED（基线 7d06777）| 6 failed / 4 passed（精确对应两根因+冻结钉定） | `receipts/red_run.txt` |
| `pytest tests/test_du10_transcript_fidelity.py` | 10 passed | 修复后 |
| 旧 DU7×4+robustness+DU9（旧 goldens）| 284 passed（与基线相同） | — |
| 全仓 `pytest tests/`（除时序敏感 2 套件）| 561 passed | — |
| `test_batch_runtime`+`test_retrieval_cli_e2e` | 47 passed | — |
| `ruff check transcript_extraction.py tests/test_du10_transcript_fidelity.py` | All checks passed | — |

分母：本卡专属 10；DU9 226；DU7 behavior/edges/e2e/wiring/robustness = 4/12/4/8/30；相邻 transcript_api 46、artifact 44、audit 8、scraper 16、common 49、translation_controls 4；全仓收集 608 + 时序敏感两套件 47。

## 真实数据（只读）

- 分母：`transcripts/` 6 家 43 份 `*_earnings_call.txt`（130 文件含派生物，SHA 前后一致）。
- 深检 3 份：MSFT_Q1_2026（EN，152 段）、NVO_Q2_2024（EN，301 段）、TAL_Q2_2026（中企好未来，英会，90 段）。全部：段落回溯零失败；以真实段落内容 + 合成 MF 形状标记（p/strong/div/h2）往返，`extract()` 逐段精确一致、诚实 `/2`（`receipts/verify_real_corpus.py` → `real_corpus_verification.json`）。
- CJK 头部字面（"好未来"/"微软"）完好；未引入任何翻译。

## 局限 / NOT_RUN

- **真实线上 MF HTML DOM 未实测**（本地无原始 HTML 缓存；卡片禁下载补样）→ 真实页面 DOM 形状泛化 NOT_RUN；真实内容往返的真实性限于"真实文本+合成标记"。
- **中文母语来源 transcript 本地不存在**（TAL/NVO 等原文均为英文）→ 中文只到夹具级 + 中企样本级。
- FMP JSON 真实载荷未测（需凭证；JSON 路径不在本卡可改面）。
- 截断检测只覆盖 EOF 悬挂标记形态；无标记残留的句中截断（如 `…mid-wo`）从字节不可判定，仍可能产出看似完整正文——记录为本质局限。
- CJK 全角空格 U+3000/NBSP 被空白归一为 ASCII 空格：与冻结 v1 一致的兼容面，未改。
- 旧 TXT 语料中 "ARPU:"/"Finance lease:" 等词典行会被下游说话人启发式误认——语料形态观察，非解析器丢失（全部段落回溯可证）。

## ROOT 接线步骤

1. 合并 `codex/du10-et-txt-fidelity`（源码仅 `transcript_extraction.py`；测试 `tests/test_du10_transcript_fidelity.py` + `tests/fixtures/du10_transcript_fidelity/`；本 PWF）。
2. 无需 CWP/FF 字段同步：extraction_version 合同不变；`/2` 语义 DU7 已核对消费端为自由字符串。
3. 精确 CI 建议：du10 10 + du9 226 + DU7 58 + 全仓 655 + ruff。
4. 已有 `*_earnings_call.txt` 原件不重刷（本卡不改写任何已下载产物；`/2` 只影响后续新下载）。

## 清理与原件保全

`receipts/cleanup.md`：130 语料文件 SHA 不变；主树 `.workbuddy-ai/`、`eval_results.json` 哨兵不变；temp 残留 0；无 owned 外路径写入。

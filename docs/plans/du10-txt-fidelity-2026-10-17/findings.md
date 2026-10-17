# DU10 勘察发现

## 数据流（谁解析什么）

1. 下载：`transcript_api.py`（只读）→ HTML 页面字节 → `transcript_extraction.extract(page)`（唯一可改点）→ title/body → `scraper.py render_original` 渲染 `transcripts/{TICKER}/{TICKER}_Q*_20*_earnings_call.txt`（头部分隔线 + 正文）。FMP JSON 路径独立（`fmp-api-content/1`，不经本模块）。
2. 派生：`translate.py` → `_bilingual.json`（en/zh 段落对）；`make_interleaved.py` → `_interleaved.txt`。
3. 消费：CWP 经 filing-fetch 读取 `_earnings_call.txt` 原文做叙述摘要；`reader.py` 本地 Web 阅读。TXT 一旦抽取期丢内容，下游无法恢复——保真必须在 `transcript_extraction.py` 保证。

## 本地真实语料（只读）

`transcripts/`：FIG/MDB/MSFT/NVO/SNOW/TAL 六家。样本头部均 `Source: motley_fool`。TAL（北京，好未来）正文为英文（业绩会为英文+交传）。MSFT Q1 2026 正文含 `Jonathan Neilson:Good afternoon`（说话人与正文无空白粘连，属源 DOM 字面）与超长 fused 段落（旧管线产物，不能直接归罪现行代码）。无本地原始 HTML 缓存；`_bilingual.json` 含翻译内容（翻译为只读禁区）。

## 既有契约（不可破坏）

- `EXTRACTION_VERSION_LEGACY = "motley-fool-main-text/1"`：v2 输出与冻结 v1 投影字节相等时报告 `/1`，否则 `/2`（诚实降版本机制）。
- `legacy_projection_comparison.legacy_projection_matches(soup, title, body, block_tags=_BLOCK_TAGS, ...)`：DU9 有界等价判定；**接收 `_BLOCK_TAGS` 作为冻结 v1 语义参数**——直接扩 `_BLOCK_TAGS` 会篡改冻结投影（把字节相等建立在虚假基线上）。`_legacy_v1_reference` 内部硬编码 `["p","h2","h3","li"]`。
- `ExtractionError(reason)` → `transcript_api._ProviderFailure()`（error contract 不需改）。
- `MIN_CONTENT_CHARS=200`、`LEGACY_MIN_PARAGRAPH_CHARS=5`、`SKIP_TEXT` 元组冻结。
- DU9 钉定形状：`br` 分行、NBSP、脚本包裹等页与 v1 字节相等（tests/test_du9_legacy_comparison.py:336,455 的 br 形状）→ br 折叠行为不可改。
- DU7 夹具 `div_p_inline.html` 全部为 `<div><p>` 结构（无 div 直挂文本），`malformed.html` 以 `</main>` 收尾（无悬挂标记）。

## RED 实验证据（现行代码，2026-10-17）

REPRO-1（英文，h4+div 直挂）：`<h2>Prepared Remarks</h2><p>CEO…(长)</p><h4>Question And Answer</h4><div><strong>Operator:</strong>…</div><div><strong>Analyst Bob Lee:</strong>…</div><p>Jane Roe:…</p>` → 输出仅 3 段：h2 文本、两个 p；**h4 边界标题与两条 div 说话人轮次全部丢失**，且报告 `/1`（宣称与 v1 字节相等——v1 同样丢，字节相等掩盖语义缺失）。
REPRO-2（表格参与者）：`<table><tr><th>Role</th><th>Name</th></tr><tr><td>CEO</td><td>Jane Roe</td></tr>…</table><p>(长)</p>` → 表格整表丢失，仅剩 p，报告 `/1`。
REPRO-3（中文，h4+div）：`<h4>问答环节</h4><div><strong>分析师:</strong>…</div>` 同样整块丢失。
REPRO-4（截断）：正文以 `…cut off mid-wo` 半句收尾的截断输入 → 正常返回"貌似完整" transcript，无任何诊断/失败。
REPRO-5（短正文强阈值混淆）：小样本因 `MIN_CONTENT_CHARS=200` 报 `empty_body`，须在测试中用足量正文隔离所有权丢失与长度门槛。
REPRO-6（钉定确认）：`<p>Line one<br>Line two</p>` → br 变空格（内容保留、行界丢失）——DU9 兼容钉定，不改，转钉定测试。

## 根因（代码级）

- RC-1: `transcript_extraction.py` `_collect_runs` 中所有权仅在 `child.name in _BLOCK_TAGS`（p/h2/h3/li）时转移；其余块级结构的直挂文本 owner=None → L155-179 `if current_dropped or current_owner is None: continue` **静默丢弃**。受影响：h4/h5/h6、div、section、blockquote、td/th、dl/dt/dd、ul/ol、table、figure、pre 等一切非白名单块的直挂文本。
- RC-2: `extract()` 对 EOF 悬挂标记（如 `…<p class="x`）无检测；html.parser 静默补全，产出貌似完整正文。

## 修复设计（Phase B）

1. 新增 `_V2_OWNERS = _BLOCK_TAGS | {h1,h4,h5,h6,div,section,article,blockquote,td,th,dl,dt,dd,ul,ol,table,caption,figure,pre,address,hgroup,details,center}`；`_collect_runs`/嵌套诊断用它；**comparator 与 `_legacy_v1_reference` 继续用冻结 `_BLOCK_TAGS`**。无新标签直挂文本页 → v2 输出逐字节不变 → 仍 `/1`（旧 goldens 不动）；有 → 内容恢复且诚实 `/2`。
2. EOF 截断检测：对解码文本取最后一个 `<`；若其位于最后一个 `>` 之后且后随字母/`/`/`!`/`?`（疑似未完成标记/注释），抛 `ExtractionError("truncated_markup")`，不产出貌似完整 transcript。

## 风险与对策

- 误报截断：完整合法页不会以裸 `<字母` 结尾（`<` 在文本中必须转义）；DU7/DU9 全部既有夹具已核对无此形态；全量回归把关。
- 扩集副作用仅限诊断标志（nested/dropped 容器计数），无测试钉定诊断值（grep 确认）。
- 中文：所有权不按语言区分；`" ".join(text.split())` 的空白归一（U+3000→ASCII 空格等）与 v1 一致且为冻结兼容面，记录局限不改。

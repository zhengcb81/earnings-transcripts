# 独立审查 DU9-ET-LEGACY-COMPARISON

reviewer_agent_id：agent_7f887c6e-2676-4840-954e-1a15365921ef（general-purpose，
与实施者不同一agent；自建fuzz与反例，不复述作者报告）。

## 第1轮（2026-10-16）

reviewed_commit：`36c9b38`；reviewed_paths_sha256（LF规范化）：

| path | sha256 |
|---|---|
| transcript_extraction.py | `80df9169eea1b0eff9d32e78e36439cbdacd0fb3542708e8bfaa6e7b369cb75c` |
| legacy_projection_comparison.py | `e5183001a11a52110bd5433e544ef4b73ca5ac3b0eeac5fda8b68bdb36c1ba5f` |
| tests/test_du9_legacy_comparison.py | `396ca5c8ecf283cb6aa7140f608f746c96b613b16b201bf0fbedf48cf6312e95` |

Verdict：**FAIL**（1 high / 1 medium / 1 low）。

### 审查者报告（原文）

```
# INDEPENDENT REVIEW ROUND 1
## Verdict: FAIL
## Summary (5-10 sentences, what you actually ran and measured)
I reviewed baseline `0cb71731`..`36c9b38` end-to-end: the full diff,
`legacy_projection_comparison.py`, `transcript_extraction.py`, the 796-line test
module, and both fixtures. The correctness half of the claim holds: an independent
fuzz harness (2 generators, own seeds, 3,840 pages, 2,209 reaching the version
decision — 267 labeled /1, 1,942 /2) found **zero** disagreements between the new
decision and the frozen `_legacy_v1_reference(soup) == (title, body)` oracle, plus
3,000 `_SkipWindow` property cases and 800 random-tree `find_all`-order checks all
passed; all required suites pass (du9: 193, du7 x5: 58, goldens: 10 matched, and
the goldens do pin `/1` on the real-entry page). The wiring is within the
whitelist, `_legacy_v1_reference` is byte-identical (source sha256 `d056bbe7…`
identical at baseline and HEAD), and the production path performs exactly 1
BeautifulSoup parse with 0 `get_text`/`_all_strings` calls on every shape I tried.
However, the efficiency half of the claim fails: on legitimate deep-nesting shapes
where outer blocks are legacy-**dropped** (whitespace-only, <5-char, or skip-text
inner blocks), `_body_matches` scans every block's full subtree, so the comparator
stays Θ(depth²) — the author's own `stats.nodes_visited` reaches 8,638,803 visits
for 7,207 DOM nodes at depth 2400 (1,199× the author's own 10× linear bound; exact
4× growth per depth doubling) — and on those shapes the new comparator is 2.6–6.9×
**slower in wall-clock than the frozen replay it replaced** (e.g. skip-chain
d=2400: 26.10s vs 4.09s on pre-parsed soups). The author's cost tests only
exercise the text-dense chain where the comparator early-exits after 2 blocks
(`blocks_scanned=2` in their own receipt), so the whole suite passes despite
this. Decision parity is unaffected, and the memory pathology (materialized
duplicated legacy body) is genuinely fixed; the remaining defect is time-only but
it falsifies the central "bounded single-DOM comparison / quadratic removed" claim
and the package's own RED requirement ("text-piece visits must be linear at
200/1200/2400 depth").
## Findings
**R1-1 — severity: high — component: `legacy_projection_comparison._body_matches` / `_scan_block`.**
Cause: `_body_matches` calls `scan_block(block)` for every block in
`_iter_legacy_blocks` order, and each `_scan_block` walks that block's entire
subtree through `_iter_text_pieces`. Early exits only fire on a *non-dropped*
size/mismatch; when outer blocks are dropped (empty/whitespace text →
`token_count==0`, `len<5`, or a SKIP_TEXT hit), the loop continues and a
depth-d nested chain re-walks overlapping subtrees Σ≈ d²/2 times. The author's
text-dense shape avoids this only because block 2 mismatches immediately
(`blocks_scanned=2`).
Evidence (author's own `ComparisonStats`, my shapes): empty_chain nodes_visited
d=300→134,853; d=600→539,703; d=1200→2,159,403; d=2400→8,638,803 for 7,207 DOM
nodes — 149×/299×/599×/1199× nodes, exact 4× per depth doubling;
`blocks_scanned`=301…2,401 (every block). short_chain d=2400 → 8,646,004 visits.
skip_chain d=2400 → 5,764,804 visits. Fair wall-clock, both sides on pre-parsed
soups: new vs frozen — empty d=2400 10.40s vs 3.60s; short d=2400 10.42s vs
3.73s; skip d=2400 26.10s vs 4.09s; while text-chain d=2400 is 0.050s vs 5.01s
(100× faster). Parity held on every timed page.
Suggested fix: make the per-block scan share work. A single bottom-up post-order
pass can memoize per block (token count, char total, skip verdict, head/tail
boundary windows of `max_pattern_len` chars): any skip occurrence is either
inside a child block (inherited verdict) or spans a boundary fully contained in
the cached head/tail regions, so each node is visited O(1) times and the legacy
body is still never materialized; exact confirmation can then stream from the
same cached token lists.
**R1-2 — severity: medium — component: tests cost anchors + cost receipt.**
Cause: the RED cost anchors use only the text-dense `_nested_p_page`, where the
comparator exits after 2 blocks; `piece_yields`/`returned_chars` measure
bs4-side iteration (always 0 for the comparator) and cannot detect the
comparator's own re-walks. A comparator quadratic on dropped-dense nesting passes
the entire suite — the suite is structurally blind to R1-1.
Suggested fix: add empty/short/skip inner-block chains at 200/1200/2400 depth to
the cost tests, asserting `stats.nodes_visited <= c * nodes` and comparator
wall-time ≤ frozen `_legacy_v1_reference` wall-time on the same pre-parsed soup
(regression guard), then re-run after fixing R1-1.
**R1-3 — severity: low — component: `ComparisonStats.max_intermediate_chars` proxy.**
Cause: `max_intermediate_chars` tracks max *token* length, but `_scan_block`'s
`piece.split()` transiently materializes all tokens of a piece, so the counter
understates peak intermediate allocation (bounded by one DOM text node, not by
the duplicated body — the no-materialization conclusion still holds). The tests
assert `<= 512`, which would spuriously trip on any fixture containing an
unbroken >512-char token.
Suggested fix: either count `len(piece)` for the intermediate bound or document
the counter as "max token", and use a bound derived from fixture content.
## Independent measurements (your own numbers: fuzz cases run, disagreements
found, visit counts, parse/get_text counts)
- Parity fuzz: 3,840 generated pages (2 independent generators — fragment pool
  and random-tree, 12 seed families…); 2,209 reached the decision (267 /1,
  1,942 /2); **0 disagreements** vs the frozen oracle.
- Unit property fuzz: `_SkipWindow` 3,000 random streams — 0 mismatches;
  block-order 800 random trees vs `find_all` — 0 mismatches.
- Production path counts (patched `Tag.get_text`/`Tag._all_strings`, parse
  counter), every shape: **parse=1, get_text=0, all_strings=0**.
- Linear shapes: text-chain d=2400 → comparator 4,804 node visits / 4,810 DOM
  nodes (1.0×), wall 0.050s vs frozen 5.007s; siblings d=3,200 → 3,203 visits /
  6,408 nodes; balanced tree (10,940 nodes) → early exit, 3,646 visits.
- Quadratic shapes (author's own counter): empty d=2400 → 8,638,803 visits
  (1,199× nodes); short d=2400 → 8,646,004; skip d=2400 → 5,764,804; growth
  exactly 4× per depth doubling; fair wall new-vs-frozen: 10.40/3.60s,
  10.42/3.73s, 26.10/4.09s.
- Suites: du9 193 passed; five DU7/root extraction suites 58 passed; goldens 10
  matched (goldens pin `motley-fool-main-text/1`).
- Boundary: `git diff --name-only` = only the whitelist; no caps/limits/config
  touched; `_legacy_v1_reference` sha256 identical baseline vs HEAD.
```

### 处置

| id | severity | cause | status | fix_commit | verification_receipt |
|---|---|---|---|---|---|
| R1-1 | high | 逐块子树重走，丢弃链上 Θ(深度²) | fix_committed，待同agent复核 | `5dc4e00` | receipts/c_e2e_cost_evidence.json（dropped_* 形状 visits≈nodes、墙钟 0.011–0.015s vs 冻结 ~1.0s）+ tests `test_dropped_chain_comparator_visits_stay_linear`（9例）/`test_dropped_chain_adjacent_scale_growth_is_linear`（3例） |
| R1-2 | medium | 代价锚点结构上看不见R1-1 | fix_committed，待同agent复核 | `5dc4e00` | 同上：三类丢弃链 × 200/1200/2400 全部纳入 `nodes_visited<=10*nodes`、相邻规模增长与 `blocks_scanned>=depth` 断言；收据含全部15形状与墙钟参考 |
| R1-3 | low | 中间物化计数低估 | fix_committed，待同agent复核 | `5dc4e00` | 计数改为单个文本片（piece）长度，断言上界由 `_max_text_piece(page)` 从夹具内容推导；深嵌套另断言 `max_intermediate*100 < 冻结重复正文长度` |

修复后本地全套（待第2轮独立复核确认）：du9 **205 passed**、DU7/ROOT **58 passed**、
集中回归 **170 passed**、golden **10/10 matched**、ruff 全绿。

## 第2轮（2026-10-16）

reviewer_agent_id：agent_2654a408-ab25-4c35-8acf-edbc811806e2（fresh agent——
第1轮 agent 实例在本平台不可恢复（SendMessage: no active local_agent task），
已在本记录声明；两轮均独立于实施者）。
reviewed_commit：`6a9ddc5`（含第1轮修复 `5dc4e00`）。

Verdict：**FAIL**（1 high：R2-1）。

### 审查者报告（要点原文）

- 三个第1轮修复全部复核通过：丢弃链访问≈1.0×节点、墙钟30–120×优于冻结；
  收据/测试确实覆盖；中间物化计数正确。
- 自建fuzz 13,155+ 比较（skip跨块边界网格1,128例、ß/İ/ſ/Å/ﬀ casefold、
  CData/Comment/PI/Doctype、hidden/ad/template、深混合链、等价侧+变异）
  **0 分歧**；生产 1 parse / 0 get_text / 0 _all_strings；白名单干净；
  `_legacy_v1_reference` 冻结函数 sha 与基线一致；测试质量检查
  （恒True比较器 159/205 失败、恒False 25/205 失败）通过。
- **R2-1（high）**：确认阶段（walk2）对**零direct文本的嵌套保留块**仍可
  Θ(深度²)——所有祖先组合到同一文本，正文重复同长段落时全部完整确认
  （kept_chain d=2400：2,888,402 访问=401×节点、精确4×倍增；比较器 3–6×
  慢于冻结）；docstring/findings 的"确认子树互不重叠"断言被证伪；
  建议：保留块计数预检 / 记忆化 / 补该形状测试 / 修正文档断言。

### 处置

| id | severity | cause | status | fix_commit | verification_receipt |
|---|---|---|---|---|---|
| R2-1 | high | 确认阶段对嵌套保留块重复走子树 Θ(深度²)；"子树互不重叠"断言被证伪 | fix_committed，待同agent复核 | `3dc576c` | 计数预检（长度不同即判）+ 仅最外层保留块DOM确认（构造性两两不重叠）+ 嵌套块按 `offset_in_parent` 从已验证祖先段落切片逐字节派生（零DOM重走，切片量被当前正文长度和约束）；R2-1三族×200/1200/2400 测试（nodes_visited≤10×nodes、full_confirmations≤1、oracle一致）；收据23形状零违规（kept_chain_2400: 7,201 vs 7,208节点）；另600页自建嵌套结构对抗fuzz 0分歧 |

修复后本地全套（待第3轮独立复核确认）：du9 **214 passed**、DU7/ROOT **58 passed**、
集中回归 **170 passed**、golden **10/10 matched**、ruff 全绿。

## 第3轮（2026-10-16）

reviewer_agent_id：agent_2654a408-ab25-4c35-8acf-edbc811806e2（同第2轮 agent）。
reviewed_commit：`38953ea`（含第2轮修复 `3dc576c`）。

Verdict：**FAIL**（2 medium：R3-1、R3-2）。

### 审查者报告（要点原文）

- R2-1 修复属实：其round-2反例 d=200..4800 现为 1.00×节点访问、计数预检 O(1) 判定、
  d=4800 快于冻结 23×；新增族测试对旧模块有真实回归网（旧模块 100.6× 违界）；
  本轮自建fuzz 21,306 比较 **0 分歧**；1 parse/0 get_text/白名单/冻结sha/文档/收据
  全部符合实际；恒True/恒False 比较器变异均被现有测试拦截。
- **R3-1（medium）**：`resolve_anchor` 无路径压缩——被污染祖先链下的
  star 形状（每深度一个保留块）走 θ(n²) 纯Python父链步（n=2400: 0.56s、
  74% profile 时间；n=4800: 5.4–5.9s），`nodes_visited` 不可见（1.33×）。
  建议：首走缓存全部中间节点 / 证据段即算终锚；star形状进代价测试并用
  显式resolve步数断言。
- **R3-2（medium）**：锚定/偏移的**正向路径（decision=True）无仓内覆盖**——
  把每个 `offset_in_parent` +1 后 du9 214 与 golden 10 全绿；其fuzz中
  `extraction_version==/1 且 derived_confirmations>0` 的页有212例，仓内0例。
  建议：加正向嵌套保留等价页fixture（断言 derived≥1 + oracle）+ 偏移损坏哨兵。
- 观察（非发现）：平铺等价页是常数因子最差情形（siblings_2000: 0.308s vs
  冻结0.126s，访问线性），收据已披露。

### 处置

| id | severity | cause | status | fix_commit | verification_receipt |
|---|---|---|---|---|---|
| R3-1 | medium | resolve_anchor 无路径压缩，star形状 θ(n²) 父链步且stats不可见 | fix_committed，待同agent复核 | （本轮提交） | 首走缓存全部已访问节点（含非候选中间块）+ `anchor_resolve_steps` 计数断言 `<= blocks_scanned`；`test_resolve_star_anchor_walks_stay_linear`×200/1200/2400；收据26形状零违规（star_2400: steps 4800 == blocks 4800，0.22s vs 冻结2.69s） |
| R3-2 | medium | 正向派生路径无仓内覆盖，偏移损坏全绿 | OPEN——已向同审查agent索要其212例 /1+derived 的最小反例 | - | 作者侧穷举40k+结构候选与1000页对抗fuzz均未找到 decision=True 且 derived≥1 的页（构造分析指向Xfull配对槽结构性障碍）；需审查方提供可复现页后立即入fixture |

修复后本地全套（待第4轮复核）：du9 **217 passed**、DU7/ROOT 58、集中回归 170、
golden 10/10、ruff 全绿。

## 第4轮（2026-10-16）——PASS

reviewer_agent_id：agent_2654a408-ab25-4c35-8acf-edbc811806e2（同第2/3轮 agent）。
reviewed_commit：`ea6983b`（含 R3-1 修复 `c407ce5` 与偏移哨兵 `ea6983b`）。

Verdict：**PASS**（0 未解决）。

- **R3-1 CLOSED**：star 形状 n=300..9600 六个规模 `anchor_resolve_steps ==
  blocks_scanned` 恒等（1.00×），访问1.33×节点，n=9600 墙钟 0.288s vs 冻结
  41.17s（143×）；另3个新攻击族（不相交污染链/每层保留链/star-of-stars）
  步数恒等、parity 全过；其建议的步数断言已入测试。
- **R3-2 RETRACTED（审查方撤回）**："212例 /1+derived" 系其fuzz把**冻结legacy
  tuple**（及段落相同的变异）直接喂给比较器API所致，生产从不传入该元组——
  其给出的显式反例：2个嵌套簇+2个skip污染段页面，冻结tuple → True
  （full=2/derived=2），同DOM的v2元组 → False（oracle一致）。审查方独立确认
  作者的结构性分析（嵌套保留对必然产生v2不发射的legacy段落；短段/SKIP补偿器
  与保留段文本矛盾），并认定已提交的偏移地面真值哨兵即 R3-2 所求覆盖
  （clean 224 / +1损坏 7例失败，7/7无误报）。
- 新鲜种子 fuzz：17,812 比较 0 分歧；全部套件、边界（白名单、冻结sha
  `85d1d45c…`、生产 1 parse/0 get_text）、文档收据全部与实测一致。

审查方建议的两项收尾（作者随即实现，delta 复核请求见本轮之后）：
冻结tuple正向完整性测试（含审查方原样页面 + 偏移损坏翻转断言）与模块
docstring 的 derived-True 不可达性说明。

**最终状态：审查 PASS，0 未解决问题。**

## 第5轮（2026-10-16，delta复核）——PASS

reviewer_agent_id：agent_2654a408-ab25-4c35-8acf-edbc811806e2。
reviewed_commit：`da82f3a`（delta `ea6983b..da82f3a`）。

Verdict：**PASS**（0 未解决）。delta 恰为第4轮建议项：5行docstring段落、
两个新测试（+97行）、计划文档/收据更新；`transcript_extraction.py` 未动，
冻结函数sha `85d1d45c…` 不变，树干净。du9 226 / DU7+ROOT 58 / golden 10/10 /
ruff 全绿。docstring 主张与其第4轮证明逐字相符；正向完整性测试前提（喂冻结tuple
时 `frozen == (title, body)` 平凡成立 ⇒ 判True）在≥40页上钉住。
正向哨兵实测为真哨兵：全局+1偏移、全局−1偏移、拆死derived路径（4次DOM走仍判True）
三种独立破坏全部被其断言捕获。

**全部发现关闭**：R1-1/2/3（第1轮）、R2-1（第2轮）、R3-1（第3轮）逐轮验证修复；
R3-2 第4轮由审查方撤回并由冻结tuple正向钉+偏移地面真值哨兵彻底覆盖。
五轮独立审查累计 ~80,000+ 比较，与冻结oracle **0 分歧**。
**最终判定：READY for handoff。**

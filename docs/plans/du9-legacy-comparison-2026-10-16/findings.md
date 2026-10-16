# 调查

卡内给出ROOT已调查的根因；作者仍须在A自行验证。尚未运行业务测试，不能称组件或生产PASS。

## A：已实测确认的根因（2026-10-16，基线 0cb71731cef6c6b3582f595d44f5b8a077392ac1）

生产路径 `transcript_extraction.extract` 第258行调用冻结 `_legacy_v1_reference(soup)`：
`main.find_all(["p","h2","h3","li"])` 后对**每个**块 `get_text(" ", strip=True)`。
bs4 4.14.3 的 `get_text` 经 `_all_strings` 遍历整棵子树——每个嵌套块重复走其全部后代，
并把带重复的整份旧正文物化成字符串。

实测（patch `bs4.element.Tag._all_strings` 计数，`python` = 3.13.9 / bs4 4.14.3）：

| 嵌套 p 深度 | DOM节点数 | get_text 调用数 | 文本片访问(yields) | 旧重复正文物化 | 耗时 |
|---|---|---|---|---|---|
| 200 | 409 | 203 | 20,303 | 是 | 0.020s |
| 1200 | 2,409 | 1,203 | 721,803 | 是 | 0.473s |
| 2400 | 4,809 | 2,403 | 2,883,603 | 是 | 1.795s |

节点数随深度线性（409/2409/4809），片访问随深度**二次**（2400/1200 深度比 ≈4.0×，
2400 深度下片访问 ≈ 0.6×节点²——2,883,603 vs 4,809）。大量**兄弟**块为线性对照
（每块子树不相交），病态项仅来自合法深嵌套块（p/h2/h3/li 互套），与DU7/ROOT已验收的
单次主walker无关：主提取仍是1次BeautifulSoup观察，病态只在版本兼容比较侧。

## 语义基线（冻结v1 精确语义，bs4 4.14.3 实测）

- `get_text(" ", strip=True)` 只收**精确类型** `NavigableString`/`CData`
  （`_all_strings` 按 `type(x) in {NavigableString, CData}` 过滤）：Comment、
  Script、Stylesheet、TemplateString、Doctype、Declaration、ProcessingInstruction
  全部排除；CData（CDATA字面）**包含**。片先 strip 去空，再以 `" "` 连接。
- v1 title = h1 的 `get_text(" ", strip=True)`，**不再**做空白折叠（保留双空格、\xa0、\n）。
- v1 每块正文 = `" ".join(get_text(...).split())`（全空白折叠，\xa0→空格），
  `len<5` 或 casefold 后含 SKIP_TEXT 子串则整块丢弃，`"\n\n"` 连接后 strip。
- `main`/`h1` 取 `find_all`/`find` 第一个；find_all 按文档前序、**不剪**隐藏/广告子树、
  深入 template。v1 无 title 或 body<200 返回 None → 版本判 /2。
- v2 侧（不动）：所有权一次发射、节点作用域剪枝、inline 邻接、br→"\n"、title 折叠。
  已确认会产生 /2 的分歧：嵌套块重复、CData、hidden/ad 容器、SKIP_TEXT、短段、
  inline 邻接（$1.<strong>2</strong>）、注释分词（alpha<!--c-->beta）、title 双空格。
- 已确认可判 /1 的等价面：平铺普通段落（oracle True 实测）、br、NBSP 中置、
  脚本在 p 内（两侧都排除）、业务近似词容器（两侧都保留）、重复同文、多 main/h1、
  template 内 p（两侧都无输出）。

## RED 期望锚点

正确性断言以**冻结 `_legacy_v1_reference`（不改其函数体）与现 v2 `extract`
输出分别独立生成** oracle：`(冻结v1 == (v2.title, v2.body))` ⇔
`extraction_version == motley-fool-main-text/1`，不与新函数自比。
行为 RED 锚在成本侧：生产路径不得再回放冻结参考（get_text 调用数=0）、
文本片访问须线性（≤常数×节点，200/1200/2400 深度及大量兄弟对照）。

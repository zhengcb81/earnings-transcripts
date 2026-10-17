# DU9-ET-LEGACY-COMPARISON 交接

CARD_ID：DU9-ET-LEGACY-COMPARISON；PLAN_ID：du9-legacy-comparison-2026-10-16
分支 `codex/du9-legacy-comparison-20261016`；源码基线 `0cb71731cef6c6b3582f595d44f5b8a077392ac1`
（PWF 种子提交 `306ce2d`）。工区 `C:/Users/郑曾波/Projects/earnings-transcripts-du9-legacy-comparison`
（sparse checkout）。python 3.13.9 / bs4 4.14.3。

## 做了什么

把 `transcript_extraction.extract` 末尾的生产版本兼容判断（/1 vs /2）从
"回放冻结 `_legacy_v1_reference`（逐块 `get_text` 重走子树 + 物化整份重复旧正文，
深嵌套下时间/空间二次）"改为**同一次DOM上的有界比较**：新增纯模块
`legacy_projection_comparison.py`（无IO/网络/配置，无二次BeautifulSoup.parse），
`transcript_extraction` 仅薄接线（import + 一个显式冻结常量 + 调用替换）。
v2 的 title/body/diagnostics/过滤语义零改动；10个gold、DU7/ROOT 集中套件原样通过。

## 根因（两处，均由独立审查钉死并修复）

1. **回放二次**（卡片根因，A 实测）：`main.find_all` 后每块 `get_text` 重走整棵
   子树：200/1200/2400 层文本片访问 20,303/721,803/2,883,603（节点 409/2,409/4,809
   线性），2,403 次 get_text，物化重复旧正文。
2. **确认二次**（第1轮审查修复后的残余，第2轮审查发现 R2-1）：逐块确认在
   **外层被v1丢弃**（空白/<5字符/SKIP链）与**零direct嵌套保留块**（重复同长段落）
   两族上仍可 Θ(深度²)（8,638,803 / 2,888,402 访问@2400层），且首版
   "确认子树互不重叠"断言被反例证伪。

最终实现两段有界：
- **证据段**：一次迭代前序遍历访问每节点一次；每块组合计数（token数/字符和，
  子块关闭O(1)合并）+ 全局流式SKIP匹配器（casefold流坐标，命中标到最内含块、
  关闭时向祖先传播；窗口只留最大模式长度尾串）；每块首token时 O(1) 记录
  `offset_in_parent`（该块归一化文本在块父文本内起点）。
- **确认段**：保留块计数 vs 段落数预检（长度不同即判）；仅**无已验证保留祖先**
  的最外层保留块做DOM逐字符确认（构造性两两不重叠）；嵌套保留块从已验证
  祖先的段落切片按组合偏移逐字节比较（零DOM重走，切片量被当前正文长度和约束）。
  绝不物化拼接旧正文；只从精确字符/令牌相等宣称equal（无hash）。

## 接口（公开面不变；新模块API）

- `transcript_extraction.extract(page) -> ExtractionResult(title, body,
  extraction_version, diagnostics)`、`ExtractionError(reason)`、
  `_extract_body` 错误合同、`EXTRACTION_VERSION_LEGACY/_EXTRACTION_VERSION`
  常量、`MIN_CONTENT_CHARS=200`、`SKIP_TEXT` 全部不变；新增
  `LEGACY_MIN_PARAGRAPH_CHARS = 5`（冻结v1段落阈值显式命名，行为零变化）。
- 冻结 `_legacy_v1_reference` 函数体与基线逐字节一致（LF规范化源码sha256
  `85d1d45c52f14b39768120184e034945b0e4e8887ce622b4a2069e1b27bfad2b`，
  测试 `test_frozen_legacy_reference_source_is_untouched` 钉住），仅作受控
  测试/基准 oracle，不在生产回放。
- 新 `legacy_projection_comparison.legacy_projection_matches(soup,
  current_title, current_body, *, block_tags, skip_text, min_paragraph_chars,
  min_content_chars, stats: ComparisonStats | None = None) -> bool`：冻结语义
  输入由抽取owner注入（无第二份常量副本）；`stats` 仅测试用
  （blocks_scanned/nodes_visited/text_strings_visited/chars_compared/
  full_confirmations/derived_confirmations/max_intermediate_chars）。

## 如何复现

```powershell
C:/Miniconda/python.exe -m pytest tests/test_du9_legacy_comparison.py -q
C:/Miniconda/python.exe -m pytest tests/test_du7_extraction_behavior.py tests/test_du7_extraction_edges.py tests/test_du7_extraction_wiring.py tests/test_du7_extraction_e2e.py tests/test_root_du7_extraction_robustness.py -q
C:/Miniconda/python.exe -m pytest tests/test_transcript_api.py tests/test_request_response_budget.py tests/test_retrieval_cli_e2e.py tests/test_transcript_artifact.py tests/test_batch_runtime.py -q
C:/Miniconda/python.exe tests/generate_transcript_goldens.py --check
ruff check legacy_projection_comparison.py transcript_extraction.py tests/test_du9_legacy_comparison.py
```

## 测试结果（receipts/）

- 首个行为 RED：5 failed / 152 passed（`a_first_behavior_red.txt`）——
  冻结回放、get_text调用、200/1200/2400层文本片线性界均以行为断言复现（非import级）。
- du9 最终 **226 passed**（`b_du9_suite.txt`）：oracle矩阵24例+双fixture+120种子
  生成样本（双侧覆盖）+200/1200/2400层与兄弟对照+丢弃链3族×3深度+嵌套保留3族×
  3深度+star形状3深度（anchor步数界）+偏移地面真值哨兵7页+冻结tuple正向完整性2测试+冻结函数源码钉+
  块序parity+真实入口E2E（假HTTP seam、0外网、0翻译、单次DOM构建、数字不变、
  /1与/2双侧、TXT复用、失败零半文件、输入/配置SHA+mtime不变）。
- DU7/ROOT 集中 58 passed（`b_du7_root_suites.txt`）；跨模块集中回归 170 passed
  （`b_concentrated_regression.txt`）；golden check 10/10 matched（`b_golden_check.txt`，
  旧/1标签未动）；ruff 全绿（`b_ruff.txt`）。
- 算法计数收据 `c_e2e_cost_evidence.json`：23形状，访问≈节点（最坏 1.17×），
  oracle 全一致；墙钟仅参考（全部≤冻结）。清理收据 `c_cleanup.json`：
  白名单零违规、既有测试零改动。

## 独立审查与整改（review/）

强制独立审查进行至第4轮（详见 `review/independent_review.md/json`）：
1. 第1轮 FAIL（R1-1 高：丢弃链 Θ(深度²)/R1-2 中：代价锚点盲区/R1-3 低：中间计数）→
   共享证据遍历修复（`5dc4e00`），fuzz 3,840页 0 分歧复核确认。
   round-1 agent实例平台不可恢复，第2轮起由fresh独立agent续审（均独立于实施者）。
2. 第2轮 FAIL（R2-1 高：嵌套保留块确认 Θ(深度²)，"不重叠"断言证伪）→
   计数预检+最外层DOM确认+锚定切片派生修复（`3dc576c`），自建fuzz 13,155+ 0 分歧。
3. 第3轮 FAIL（R3-1 中：anchor解析无路径压缩，star形状θ(n²)父链步 → 修复
   `c407ce5` 全链缓存+`anchor_resolve_steps`断言+star测试；R3-2 中：正向派生
   路径仓内无覆盖 → 已加偏移地面真值哨兵（7页，变异核验7/7拦截），并同审查agent
   索要其声称的 /1+derived 212例最小反例）。
4. 第4轮 **PASS（0未解决）**：R3-1 关闭（star n=300..9600 步数恒等、n=9600
   0.288s vs 冻结41s）；R3-2 由审查方**撤回**（212例系冻结tuple harness输入、
   非生产页；结构性不可能获独立确认；偏移哨兵获认可）。其建议的冻结tuple
   正向完整性测试与docstring说明已实现（delta复核请求已提交同agent）。

## 版本兼容表

| 场景 | body | 报告版本 |
|---|---|---|
| 与旧v1字节一致的页面（gold/平铺/br/NBSP/脚本等） | 与v1一致 | `motley-fool-main-text/1` |
| 修复语义（邻接/注释/嵌套/hidden/SKIP/短段/CData等） | 与v1不同 | `motley-fool-main-text/2` |
| FMP JSON | 未变 | `fmp-api-content/1` |

## 局限（真实未覆盖 vs 实现缺陷分开）

- 线性结论限定在**兼容比较模块的算法计数**（节点访问/比较字符/中间物化，
  23形状+相邻规模）；bs4 html.parser 的DOM构造本身仍随页面线性成本（既有解析器，
  非本卡引入）；**整个HTTP supervisor/worker硬限额路径未在本卡重新测量**，
  不把"比较线性"写成"全系统硬实时"。
- 合成夹具为主；真实Motley live页面未实测（默认 provider 禁用，不为凑C改生产
  策略）——live DOM 适应性 NOT_RUN。
- `test_batch_runtime`/`test_retrieval_cli_e2e` 含偶发时序敏感子进程用例
  （重负载后偶见1例失败，隔离重跑即绿，与本卡改动正交：两条失败用例均不触及
  抽取路径）；不属本卡，未改。
- 第2轮审查为fresh agent续审（第1轮实例平台不可恢复），已在review记录声明。

## NOT_RUN

- live 页面逐段独立试读（默认 provider 禁用）。
- 翻译/LLM：0 调用（e2e 断言 translator 不被构造）。

## ROOT 接线步骤

1. 合并 `codex/du9-legacy-comparison-20261016`（源码：`legacy_projection_comparison.py`
   + `transcript_extraction.py` 薄改 + `tests/test_du9_legacy_comparison.py` +
   `tests/fixtures/du9_legacy_comparison/` + 本PWF）。
2. 无需 CWP/FF 字段同步（extraction_version 合同不变：Motley 自由字符串，FMP 不变；
   DU7已静态核对过消费端）。
3. 精确CI：du9 214 + DU7/ROOT 58 + 集中回归 170 + golden 10/10 + ruff；
   `test_scraper` 缺目录3例与 `test_common` 翻译1例为sparse工区/环境基线既有失败，
   不计入本卡。
4. 可选：对真实Motley页面抽查一次 `extract`（需ROOT决定是否临时启用provider）。

supplier_calls=0，production_writes=0。收到本交接后 owned 源码冻结。

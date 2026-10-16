# DU7-ET-EXTRACTION 交接

CARD_ID：DU7-ET-EXTRACTION；PLAN_ID：du7-extraction-2026-10-16
分支 `codex/du7-et-extraction-20261016`；源码基线 `d0ac42c60dcca992db78860b8469cfed3dd527e5`（PWF 种子提交 `7986b3eb829a313440b816bea7d54ebea9ad3509`）。
工区 `C:/Users/郑曾波/Projects/earnings-transcripts-du7-extraction`（sparse checkout，无 `transcripts/` 目录）。

## 做了什么

把既有 Motley 网页正文抽取从"平铺 p/h2/h3/li + SKIP_TEXT 子串 + 长度<5 删除"改为一次 DOM 语义所有权抽取：
新增纯模块 `transcript_extraction.py`（无 IO/无网络/无配置），`transcript_api._extract_body` 窄委派到它，
两处 fetch 调用（`fetch_transcript` / `fetch_transcript_candidate`）通过同一 owner `_extract_page` 获取
title/body/extraction_version。public `/1`、`/2` 请求/响应字段不增不减；FMP 路径与 provider 默认禁用状态零改动。

## 根因

三个反例同源两类缺陷，不是三个字面例外：

1. **粗文字过滤**：SKIP_TEXT 子串匹配丢掉整句业务文本（"Advertising revenue grew 20% and advertisement
   pricing declined." 被当广告丢弃）；`len(text) < 5` 删掉短结构标题（`Q&A`）。
2. **DOM 所有权缺失**：`main.find_all([...])` 把 li 与其子 p 都发射一次，同一业务段落输出两遍。

修复：文本按最近结构块（p/h2/h3/li）归属一次发射；过滤按节点作用域（非内容元素、hidden、
带 class/id/role/data-* 广告导航赞助证据的容器，整词 token 匹配），业务句中的
advertisement/sponsored/cookie 等词绝不整句丢弃。

## 接口

- 保留旧端口 `transcript_api._extract_body(page: bytes) -> tuple[str, str]` 及 `_ProviderFailure` 错误合同
  （main 缺失 / 无标题 / body<MIN_CONTENT_CHARS=200）。
- 新 `transcript_extraction.extract(page) -> ExtractionResult(title, body, extraction_version, diagnostics)`；
  失败抛 `ExtractionError(reason)`，由端口映射 `_ProviderFailure`。
- 版本诚实策略（逐结果）：同一次 DOM 上跑冻结的 v1 参考投影（`_legacy_v1_reference`），
  新输出与其字节相同 → `motley-fool-main-text/1`，否则 → `motley-fool-main-text/2`。
  旧 v1 语义冻结不变；FMP `fmp-api-content/1` 不改。

## 如何复现

```powershell
C:/Miniconda/python.exe -m pytest tests/test_du7_extraction_behavior.py tests/test_du7_extraction_edges.py tests/test_du7_extraction_wiring.py tests/test_du7_extraction_e2e.py -q
C:/Miniconda/python.exe -m pytest tests/test_transcript_api.py tests/test_request_response_budget.py -q
C:/Miniconda/python.exe tests/generate_transcript_goldens.py --check
```

## 测试结果

- 首个行为 RED（`receipts/a_first_behavior_red.txt`）：3 failed / 1 passed——业务句误删、li>p 双发射、
  Q&A 短标题删除三个共因均以行为断言复现（非 import 级）；兼容基线当时即绿。
- DU7 最终 24/24 绿（`receipts/c_final_du7_suite.txt`）：行为 4、边缘/过滤 11、wiring/版本 5、真实入口 e2e 4。
- 集中回归 190/190 绿（`receipts/b_concentrated_regression.txt`）：新测试 + test_transcript_api +
  test_request_response_budget + test_retrieval_cli_e2e + test_transcript_artifact + test_batch_runtime。
- golden check 10/10 匹配（旧 v1 冻结 gold 未动）。

## 新/旧提取版本兼容表

| 场景 | body | 报告版本 |
|---|---|---|
| 旧正常小页面（含 golden fixture 语义） | 与 v1 字节一致 | `motley-fool-main-text/1` |
| 修复后的业务句/去重/短标题/节点作用域过滤 | 与 v1 不同 | `motley-fool-main-text/2` |
| FMP JSON 正文 | 未变 | `fmp-api-content/1` |

消费者合同（company-wiki `transcript_tool_contract`）：Motley 路径只校验 extraction_version 为 ≤128 字符串，
仅 FMP 精确匹配 `fmp-api-content/1`。新 `/2` 不需要任何 CWP/FF 接线（已静态核对，无等值断言）。

## 两 fetch 实际路径

`fetch_transcript`（listing→候选→body）与 `fetch_transcript_candidate`（绑定候选→body）都经
`transcript_api._extract_page` → `transcript_extraction.extract`，metadata 版本取自实际抽取结果
（`test_both_fetch_entries_delegate_to_one_extraction_owner`、`test_metadata_version_matches_actual_body_both_entries`）。
默认 `DEFAULT_PROVIDER_SETTINGS.motley_fool_enabled=False`，e2e 实测 CLI 在默认 flags 下返回
`unavailable/provider_disabled` 且零 HTTP 调用。

## 局限

- 合成 fixtures 为主（`receipts/fixture_shas.json`）；真实 Motley 网页 DOM 未实测——默认 provider 禁用，
  不为凑真实 C 改生产策略。live 页面适应性 NOT_RUN（见下）。
- 多 main 保持现行为（取第一个 main）；main 缺失/空正文按现失败合同处理；畸形 HTML 提取存活文本不伪造。
- v1 参考投影随新代码同跑（同一 DOM 两次纯函数投影），仅为版本标注；抽取本身仍是一次 BeautifulSoup 观察。
- sparse 工区无 `transcripts/` 目录，3 个 test_scraper 目录用例在基线上即因缺目录失败（与本卡无关，未改）；
  `test_common.TestTranslators` 1 例依赖环境 LLM key，基线同样失败（不属本卡，未改）。

## NOT_RUN

- live 页面逐段独立试读：未获取真实公开网页小样本（默认 provider 禁用，卡允许离线合成先行）。
  live DOM 适应性未验证；三个合成共因独立交付。
- 翻译/LLM：按卡不测试翻译、0 LLM 付费调用（e2e 断言 translator 不被构造）。

## ROOT 接线步骤

1. 合并 `codex/du7-et-extraction-2026-10-16`（源码：transcript_extraction.py + transcript_api.py 窄改 + 4 个新测试 + fixtures）。
2. 无需 CWP/FF 字段同步（extraction_version 合同已核对：Motley 自由字符串，FMP 不变）。
3. 可选：对真实 Motley 页面跑一次 `transcript_extraction.extract` 抽查（需 ROOT 决定是否临时启用 provider）。
4. 精确 CI：跑 DU7 测试 + 集中回归 + golden check；`test_scraper` 缺目录 3 例与 `test_common` 翻译 1 例
   为基线既有失败，不计入本卡。

supplier_calls=0，production_writes=0。收到本交接后 owned 源码冻结。

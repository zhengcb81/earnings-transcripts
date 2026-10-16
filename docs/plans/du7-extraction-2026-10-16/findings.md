# 调查

ROOT已核对真实已提交代码基线。卡内已复现/静态/待验证范围分开；不得把旧stale PWF重派为任务。

- `_extract_body`（transcript_api.py:525）：main 内 p/h2/h3/li 平铺 get_text，SKIP_TEXT 子串 + len<5 删除 → 三类共因（业务句误删/li>p双发射/短标题删除）。
- 两 fetch 调用点（fetch_transcript / fetch_transcript_candidate）均调 `_extract_body`，metadata 版本取常量。
- CWP 消费合同（company-wiki/source_catalog/transcript_tool_contract.py）：extraction_version 仅校验 ≤128 字符串；FMP 精确匹配 fmp-api-content/1；Motley 无等值断言 → 新 /2 无需消费端接线。
- golden 测试经 build_goldens 逐字节比对 frozen 文件（旧 v1 行为冻结锚点）；compat 基线 body SHA 97b5b19f… 与 golden canonical_content_sha256 一致。
- 基线既有失败（与本卡无关，未动）：test_scraper 3 例（sparse 工区缺 transcripts/ 目录）、test_common 翻译 1 例（缺环境 LLM key）。
- bs4 对无 charset 声明的 UTF-8 字节按 windows-1252 猜测 → 自有 Unicode fixtures 加 `<meta charset="utf-8">`（真实 fool.com 页面带 charset 声明）。

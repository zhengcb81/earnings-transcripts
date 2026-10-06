# Findings — N5-ET-TXT

## 环境与基线

- 源仓 `C:/Users/郑曾波/Projects/earnings-transcripts/earnings-transcripts`，基线 `63c4090083cd34ece2ebb23affab81a419bc79dc`（= main HEAD，卡上 63c4090）。
- 独占 worktree：`C:/Users/郑曾波/Projects/cwp-lanes-20261006/et-local-text`，分支 `codex/n5-et-local-text`。
- CodeGraph 未初始化：按卡只报告、不初始化；用直接读文件定位（scraper.py/parser.py/naming.py/tests）。
- 基线测试：全量 172 passed（需本地 gitignored `config.json`——worktree 初始缺失导致 1 failed，从源仓复制后 46/46 过，与 S3 交接先例一致，不提交）。
- 工具：Python 3.13.9（C:/Miniconda/python.exe）、pytest 9.1.1、ruff 0.15.18。仓库无 ruff/pyproject 配置，沿用既有调用方式 `python -m ruff check <files>`。
- 其它 worktree（s3-deadline、s3-runtime）与 cwp-lanes 下另两包目录不动、不切、不 reset。

## 已证实缺口（scraper.py）

- `_finalize_existing`（scraper.py:508）：`expected_url is None`（FMP 路径 `_process_fmp_company` scraper.py:774 传 None；fool 路径候选非唯一时也为 None）→ **完全跳过身份校验**，直接 `completed_entry` → `reused`。不校验 ticker、期间、provider、正文是否为空/截断。
- `completed_entry`（scraper.py:308）：`read_text(errors="ignore")` 容忍坏字节；`char_count` 取头部 `Characters` 并作为 `_finalize_existing` 的 `content_bytes` 上报（scraper.py:539）→ Characters: 500000 的截断文件被报成 500000 字节。
- 无收据体系：已有文件与新文件都没有字节级绑定，复用无法区分“验证过”与“碰巧在那儿”。
- 现有测试 `test_batch_runtime.py::test_existing_original_is_reused_without_body_fetch`（:487）只断言零正文抓取+不覆盖，未断言状态；`test_existing_fmp_original_is_reused_without_credentials_or_http`（:575）seed 的是无头部 `stored original\n` 却 rc=0——反例缺失（卡所述）。

## 关键既有行为（必须保持/锁定）

- 输出码：`_ERROR_STATUSES` → rc=1；`not_found`/`ambiguous` 合法 → rc=0。`output_conflict` 已在 `_ERROR_STATUSES`。
- `_existing_status`（scraper.py:411）已有先例：正文不匹配/空 → `output_conflict`（store 路径），即“损坏当冲突”与既有词汇一致。
- `_store_original` 签名被测试直接调用（test_batch_runtime.py:554,629），不可改参数表；内部 `os.replace` 被 monkeypatch 注入故障的测试存在。
- `completed_entry` 的 `char_count` 有独立测试（test_scraper.py:76）要求保留 header Characters 读取（诊断字段），不能删。
- 真实文件头（transcripts/MSFT/...）：`Company/Ticker/Quarter/Source/URL/Scraped/Characters`，`Quarter: Q1 2025` 格式 = `quarter_label` 的 `Q{n} {year}`。transcripts/ 是 **git 跟踪目录** → 测试绝不能在其生成收据。
- `render_original` 头尾都是 70×`=`，`extract_body` 对含分隔符正文用 join 还原，正文=头部之后全部（含前导空行）。
- `parse_transcript_header` 用 `v.strip()`，CRLF 下字段值不含 `\r` ✓。

## 设计要点（与卡对齐）

- 证明规则：身份 = 头部 `Ticker`==期望 **或**（期望 URL 已知且头部 `URL`==期望）；期间 = 头部 `Quarter`==期望 label。文件名/路径只产生“期望”，永远不是证据（卡：错误身份不能仅凭文件名“纠正”）。
- 无收据：身份+期间都能证明且正文非空 → `legacy_unverified`（rc=0，保持原文件）；证明不了 → `unknown`/`identity_missing`（rc=0，非 verified reused）；头部明确矛盾 → `output_conflict/identity_mismatch|period_mismatch`（rc=1）；空/乱码/不可读 → `output_conflict/empty_original|invalid_encoding|unreadable`（rc=1，原件保留）。
- 有收据：严格解码→实算 stored/body sha256+字节→对比收据与期望身份；收据坏 JSON/schema → `receipt_invalid`；哈希不符 → `receipt_mismatch`（均 rc=1 且不碰原件）。kind=download 且全对 → `reused`；kind=audit-legacy 且全对 → 仍 `legacy_unverified`（audit 收据不冒充下载证明）。
- receipt schema `et-local-text-receipt/1`：ticker、fiscal_year/fiscal_quarter/fiscal_period、provider（fmp|motley_fool）、source_url（如已知否则 null）、extraction_version（download 固定常量，audit 为 null）、obtained_at（取得时间；audit 为 null）、published_date 恒 null（卡：收据不宣称公开日/as-of 证明）、body_sha256/body_bytes、file_sha256/file_bytes、kind（download|audit-legacy）。**不包含** provider HTTP hash、公开日、CWP 准入声明。
- 完整性边界：只证明“当前字节=收据字节、身份/期间相符”，**不证明内容完整性**（无法凭关键词判断截断）；Characters 恒为历史诊断。
- 落盘：收据 `*.receipt.json` sidecar，tmp+`os.replace`+finally 清理（复用 `_store_original` 模式）；字节计入 `--max-output-bytes`（一次 take_output，避免写后超限）；收据写失败仅具名 log.warning，原件不动。
- audit CLI：不 import scraper/translator/retrieval*（结构上保证 0 翻译 0 网络），只用 config+parser+transcript_artifact；默认只读打印/报告，`--write-receipts` 显式才写 audit-legacy 收据，绝不改 TXT、绝不覆盖已存在收据。

## 风险/边界

- 收据与 TXT 是两次 replace，非单事务：中断后 TXT 无收据 → 下次走 legacy 路径（头部可证明则 legacy_unverified），原件不丢。
- `unknown` 不进 `_ERROR_STATUSES`（保持输出码）；将以 stderr `warning: <ticker> <fiscal>: unknown/identity_missing` 具名提示。
- 既有 fool seed 测试（URL-only 头）新语义下状态变 `unknown` 但 rc/零正文抓取断言不变。

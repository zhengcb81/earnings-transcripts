# 研究发现

## 发现 1：reader.py 代码重复
- 日期：2026-07-08
- 内容：`reader.py:569-584` 重新实现 `load_companies()`，用硬编码 `|` 分隔符；`reader.py:514-526` 手动解析 header 字段
- 影响：修改公司清单格式需要同步两处；header 字段名变更会导致 reader 与 common 不一致
- 修复：改为 import common 的函数

## 发现 2：translate.py --backend 选项缺失
- 日期：2026-07-08
- 内容：`translate.py:96` 的 argparse choices 只有 `["deepseek", "google", "auto"]`
- 影响：用户无法通过命令行指定 mimo 或 minimax 后端
- 修复：添加 `"mimo"` 和 `"minimax"` 到 choices

## 发现 3：config.yaml system_prompt 重复
- 日期：2026-07-08
- 内容：mimo/minimax/deepseek 三个区块的 system_prompt 完全相同（60行）
- 影响：修改翻译规则需要改三处，容易遗漏
- 修复：提取为共享的 `translate.system_prompt`

## 发现 4：README 与代码不同步
- 日期：2026-07-08
- 内容：优先级文本写 "MiMo → MiniMax → DeepSeek"，代码实际是 "MiniMax → MiMo → DeepSeek"；测试数量写 32 实际 33；config.json 注释仍写 DeepSeek
- 影响：用户阅读文档会产生误解
- 修复：统一更新 README

## 发现 5：common.py 职责过重
- 日期：2026-07-08
- 内容：476 行代码承担 6 个职责域（配置、命名、缓存、分类、解析、翻译）
- 影响：任何修改都影响所有导入 common 的模块；代码导航困难
- 修复：拆分为独立模块

## 发现 6：get_path 死代码
- 日期：2026-07-08
- 内容：`common.py:30` 的 `if base == Path(".")` 永远不会触发，因为 resolve() 返回绝对路径
- 影响：无功能影响，但增加理解成本
- 修复：删除该判断

## 发现 7：全局 logger 变量
- 日期：2026-07-08
- 内容：scraper.py 和 translate.py 都用 `global log` 赋值模块级变量
- 影响：测试困难、函数不可重入、多实例运行时日志交叉
- 修复：改为 logging.getLogger(name)

## 发现 8：翻译评测偏差
- 日期：2026-07-08
- 内容：eval_translation.py 用同类模型互评，可能有系统性偏好
- 影响：评测分数可能不够客观
- 修复：建议用独立强模型做裁判（P2 优先级）

## 发现 9：translate.py 未使用代码
- 日期：2026-07-08
- 内容：`translate.py:40-57` 有 emoji 前缀的 bilingual_parts 构建逻辑，实际未使用
- 影响：增加代码理解成本
- 修复：删除

## 发现 10：scraper.py 未使用 import
- 日期：2026-07-08
- 内容：`hashlib` 和 `urljoin` 被导入但未使用
- 影响：代码整洁
- 修复：删除

## 2026-09-27 — Findings for company-wiki transcript companion

- Existing scraper.py is an interactive batch downloader/translator. It reads project config/company list, initializes general cache/lock/log/output paths, and interprets quarters as recent-N. Its --output option does not isolate all side effects. It is unsuitable as the company-wiki acquisition boundary.
- A separate JSON CLI uses a pure injectable provider function and does not call the legacy CLI or translation stack. It requires one fiscal year/quarter and as_of_date; a document is eligible only if its URL slug exactly matches the requested period and its publication date is not later than the cutoff.
- The legacy scraper's FMP records emit the literal placeholder URL `FMP API`, so that legacy output is not suitable provenance. Current official FMP docs separately define a stable exact-period API endpoint and structured transcript payload; this is implemented as a new adapter rather than trusting the legacy placeholder.
- The default result schema `/1` extracts provider text and returns provider_payload_sha256 plus canonical_content_sha256. The opt-in result schema `/2` now returns exact bounded provider response bytes (base64), normalized MIME, and safe effective URL while omitting duplicate content text; the tool still never persists files. A downstream writer must decode and validate these bytes, independently check rights, and retain extraction version, source URL, provider ID, and both hashes.
- The CLI requires both download_authorized=true in the request and --allow-download in the process command. It never conflates not_found with provider_error; duplicate exact-period candidates return ambiguous.
- Remaining integration is cross-project: versioned filing-fetch companion orchestration, canonical company-wiki import, idempotence, and G1e E2E. Revenue-forecast I-16-A was still in-flight at last inspection; avoid edits to shared files while its snapshot/lease is active.

## 2026-09-27 — FMP API verification and provider-specific limits

- Official FMP documentation describes `GET https://financialmodelingprep.com/stable/earning-call-transcript?symbol=...&year=...&quarter=...`, with JSON fields `symbol`, `period`, `year`, `date`, and `content`. The new E-T adapter validates identity and exact period, limits the response/body, hashes the JSON payload and canonical UTF-8 content separately, and does not expose the API key in its result or provenance URL.
- FMP documentation describes `date` as the call date. The API does not expose a verified publication date in the documented response, so the adapter returns `call_date`, `publication_date: null`, and `as_of_cutoff_verified: false`. A downstream historical-as-of query must not pretend otherwise.
- FMP account access and the right to retain/display/redistribute transcript text are separate questions. FMP's current pricing page says transcript access depends on plan, and its terms/pricing warn that display or redistribution needs a specific agreement. Company-wiki persistent import must check the user's actual account terms first; this is not inferred from possessing an API key.
- At the initial check, `FMP_API_KEY` was not in the process environment or E-T config.json. The user then supplied an external key-file path and explicitly authorized a live test. One authenticated exact-period request returned HTTP 402 (Payment Required); the adapter exposed only the sanitized status code. No transcript was returned or written, and no additional live calls should be made until account endpoint access is confirmed. Offline tests cover exact symbol/period, future call-date exclusion, redirects, limits, hashes, HTTP status sanitization, and key redaction.
## 2026-09-27 — Candidate authorization must precede transcript body fetch

- The initial exact-period API combined quote-page discovery and transcript-page fetch in one call. This is compatible with a caller-level download flag but does not let company-wiki bind its existing DownloadAuthorization/provider-rights decision to one exact candidate before body bytes are retrieved.
- Added `discover_transcripts` / `--operation discover` for candidate metadata only, and `fetch_transcript_candidate` / `--operation fetch-candidate` for one strict candidate-bound body request. The candidate contract binds canonical HTTPS URL, ticker slug, FY/Q, URL-derived publication date, provider document ID and as-of; body fetch rejects an effective URL whose sanitized path differs from the authorized candidate.
- The old `fetch_transcript`/`--operation fetch` combined path remains for backward compatibility, but must not be used by company-wiki/filing-fetch production integration. `discover` still performs network access to a listing endpoint; caller must authorize the `discover` action before invoking it. FMP remains unsupported for candidate discovery pending an exact-resource preauthorization contract, plus its current HTTP 402 and retention-rights hold.
- Full offline suite after candidate gate: **106 passed, 1 deselected**; one skipped test needs the unavailable LLM key. No real provider request was made. The unique pytest root was removed and verified absent; Ruff and `git diff --check` passed.

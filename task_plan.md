# 美股电话会议纪要工具 — 实施计划

> 基于深度分析报告，按优先级分阶段修复问题。

## Phase 0（P0 必须修复）— 状态：completed

### 0.1 README 与代码不一致
- [x] 修复优先级文本：`MiMo → MiniMax → DeepSeek` → `MiniMax → MiMo → DeepSeek`
- [x] 修复测试数量：`32个` → `33个`
- [x] 修复 config.json 注释：`DeepSeek API key` → `LLM API keys`

### 0.2 translate.py --backend 选项过时
- [x] 添加 `"mimo"` 和 `"minimax"` 到 argparse choices

### 0.3 config.yaml system_prompt 重复
- [x] 提取公共 `translate.system_prompt` 区块
- [x] 三个 LLM 区块改为引用共享 prompt
- [x] 代码 fallback: `section.get("system_prompt", "") or cfg["translate"]["system_prompt"]`

### 0.4 reader.py 代码重复
- [x] reader.py `load_companies()` 改为 `from common import load_companies`
- [x] reader.py header 解析改用 `parse_transcript_header`
- [x] Characters 字段转为 int，load_companies 传入 cfg

### 0.5 TranslatorFactory docstring 与代码不一致
- [x] 更新 `create` 方法 docstring 中的优先级顺序

## Phase 1（P1 结构改进）— 状态：completed

### 1.1 拆分 common.py
- [x] `config.py` — 配置加载
- [x] `naming.py` — FileNaming
- [x] `cache.py` — JsonCache
- [x] `parser.py` — 段落解析、文件头解析、行分类
- [x] `translator.py` — 翻译体系（_OpenAITranslator、子类、工厂）
- [x] common.py 变为 re-export 门面（保持向后兼容）

### 1.2 reader.py 提取 HTML 模板
- [x] 创建 `templates/index.html`（20,829 bytes）
- [x] reader.py 改为读取模板文件（610 → 138 行）

### 1.3 消除 bilingual 构建重复
- [x] 提取 `common.build_bilingual_data()` 函数
- [x] scraper.py 和 translate.py 改为调用共享函数
- [x] 清理 translate.py 未使用的 imports（datetime, re, time, hashlib, LineClassifier）
- [x] 清理 scraper.py 未使用的 imports（hashlib, urljoin）
- [x] 删除 translate.py 未使用的 emoji 前缀逻辑（bilingual_parts）

### 1.4 消除全局 logger
- [x] 改为模块级 `log = logging.getLogger(__name__)`，main() 中只调用 setup_logging

### 1.5 测试使用 mock 配置
- [x] 创建 `mock_cfg` fixture 生成完整 mock config

## Phase 2（P2 质量提升）— 状态：completed

### 2.1 清理死代码
- [x] 删除 get_path 中 `if base == Path(".")` 判断
- [x] 删除 translate.py 中未使用的 bilingual_parts 构建逻辑
- [x] 删除 scraper.py 中未使用的 hashlib、urljoin import

### 2.2 添加 scraper 测试
- [x] 测试 FileNaming、parse+extract 往返、配置区块（5 个测试）

### 2.3 添加 reader 测试
- [x] 测试 build_data 返回结构、字段完整性、模板文件（7 个测试）

### 2.4 翻译错误日志
- [x] translate_paragraphs 中翻译失败时添加日志标记（错误计数+摘要日志）

## Errors Encountered
| 错误 | 尝试 | 解决 |
|------|------|------|
| （暂无） | | |

## Phase 3 (company-wiki / filing-fetch transcript companion) — status: in_progress

> This phase is split by repository. The internal E-T interface can be completed independently; cross-repository integration waits until the current revenue-forecast snapshot/lease is clear. Never change revenue-forecast for this feature.

### 3.1 Earnings-transcripts machine boundary (E-T repo; may proceed now)

- [x] Add a pure provider API for one ticker/exchange/fiscal year/quarter/as-of date. Exact period semantics must not be confused with scraper.py --quarters N (recent N). Support Motley Fool source pages and FMP's structured exact-period API.
- [x] Add one-shot stdin/JSON CLI. Network access is off by default. Both request.download_authorized=true and CLI --allow-download are required.
- [x] Restrict Motley Fool URLs to HTTPS www.fool.com and FMP URLs to HTTPS financialmodelingprep.com. Reject Fool cross-host redirects and all FMP redirects.
- [x] Bound execution: one shared deadline (1–60 seconds), quote listing at 2 MiB, FMP JSON response at 16 MiB, requested canonical body at 1 B–10 MiB, and at most 3 same-host Motley Fool redirects.
- [x] Keep the default `/1` result as untranslated English UTF-8 text with separate provider-payload and canonical-text hashes; do not write transcript files, logs, caches, locks, or config. FMP_API_KEY is read from process environment by the CLI and never returned or included in provenance URLs.
- [x] Add explicitly opt-in result schema `/2` for immutable-raw handoff: bounded base64 of the exact provider response, normalized MIME, and a safe effective URL; omit duplicate `content_utf8`, never write files, keep the default result `/1`, and retain both download authorization gates. Raw-mode MIME and size are checked; URL query credentials/fragments are stripped or excluded.
- [x] Verify opt-in provider payload bytes/hash, MIME rejection, effective URL secret handling, FMP API-key non-disclosure, default compatibility, and CLI no-network authorization through injected fake HTTP/subprocess tests.
- [x] Define explicit statuses: fetched / not_found / ambiguous / unsupported / not_authorized / provider_error / deadline_exceeded / content_too_large / provenance_rejected / invalid_request. Network failure must never become not_found.
- [x] Write injectable offline HTTP-session tests for exact period, as-of behavior, cross-exchange ambiguity, both authorization gates, redirect policy, byte limits, strict fields, CLI output, FMP response identity, and secret-free provenance.
- [x] Add a two-phase Motley Fool API for exact-period orchestration: `--operation discover` returns bounded candidate metadata only, without transcript-page requests; `--operation fetch-candidate` accepts a strict candidate-bound request and fetches only that URL. Keep `--operation fetch` as a compatibility-only combined mode; company-wiki/filing-fetch must not use it because it cannot preauthorize the exact candidate before body retrieval.
- [x] Bind candidate-fetch to the discovery candidate by HTTPS provider host/path, canonical URL, ticker slug, fiscal year/quarter, publication date/as-of, and provider document ID. Reject changed/ambiguous/malformed candidates before network; keep the explicit request authorization and `--allow-download` gates.
- [x] Add fake HTTP tests proving discovery never requests transcript pages, ambiguous discovery never fetches a body, candidate-bound fetch makes one body request, wrong identity/host/period and missing authorization make zero body requests, and CLI candidate-fetch still needs its process download flag.
- [x] Keep the companion API and `transcript_tool.py` translation-free by contract; their output is the original English text/raw payload and they do not initialize translation services.
- [x] Make the legacy scraper's translation opt-out explicit: preserve `--no-translate`, add `--disable-translation` as its clear alias, and test both parser paths plus the early return before translator initialization or file writes.
- [x] Full E-T offline regression after `/2` raw handoff, two-phase candidate authorization, and translation-control coverage: **108 passed, 2 deselected**. The excluded cases are the pre-existing automatic-LLM preference test (no local LLM credentials) and `test_google_translate` (may contact Google with a synthetic phrase). See current session receipt for legacy Ruff findings. The isolated run directory was verified under TEMP and removed.
- [x] Attempted one authenticated FMP live canary for AAPL 2020-Q3 using the user-supplied local key-file path; FMP returned sanitized HTTP 402 (Payment Required). No transcript was returned or persisted. Do not retry until endpoint entitlement is confirmed.
- [x] Record FMP date semantics: its documented `date` field is the call date, not verified publication date. Return publication_date=null and as_of_cutoff_verified=false so downstream cannot claim historical publication-time coverage from FMP alone.
- [x] Rerun all E-T tests with a unique company-wiki basetemp; record the existing LLM-key environment failure without adding real credentials.
- [x] Keep README/findings/progress synchronized with both provider contracts, FMP date/retention limits, and live-canary status.

### 3.2 filing-fetch companion orchestration (wait for revenue-forecast synchronization window)

Keep the existing filing request schema byte-semantics unchanged. Implement in this order; do not place transcript state inside the primary filing handle:

1. Re-read the live revenue-forecast REMEDIATION_REGISTER, current in-flight card, repo triplet, lease/lock, and file allowlist. Do not edit filing-fetch or company-wiki until the active card permits it and its snapshot is refreshed.
2. Add a versioned transcript companion request/result contract. Do not insert fields into the old strict schema. The request binds canonical security/ticker, market, fiscal year/quarter, as_of_date, provider, request ID, originating download authorization, timeout, and byte limit. Preserve provider-specific date precision: Motley Fool has a source publication date; FMP provides call date but not a verified publication date.
3. Call the companion only when the financial-filing request is authorized for download and include_related_transcript=true. Existing old-schema requests retain their behavior. New filing-fetch skill invocations opt in by default for financial filings; explicit user opt-out skips it.
4. Invoke through subprocess.run([python, transcript_tool.py, --request-stdin, --allow-download], input=json, capture_output=True, timeout=...). Resolve the executable/tool root from explicit config or EARNINGS_TRANSCRIPTS_TOOL; missing configuration returns unsupported, never guesses a path. Timeout must terminate the full child process tree and map to deadline_exceeded.
5. Treat stdout as protocol only: require one parseable JSON object, supported schema_version, matching request_id, and recognized status. Bound stderr; store only sanitized error codes, never transcript body or secrets. Malformed results affect only the transcript sub-result, not a successful filing result.
6. Only on fetched, stage content in company-wiki and recheck body SHA/length, source URL/host, available date semantics, period, provider document ID before invoking the existing canonical writer. Save provenance and date precision in sidecar; write body as-is in UTF-8. Never call translation, legacy E-T save_transcript, or bilingual generators. Before persisting FMP text, verify the account terms permit internal retention and derived summaries; do not infer that right from API access alone.
7. Idempotency key is company identity + provider document ID/source URL + provider payload hash. Identical source reuses the existing canonical source; changed body hash creates an explicit version and never overwrites old bytes. Return transcript canonical source_id separately.
8. Preserve distinct error states: not_found / ambiguous / unsupported / not_authorized / provider_error / deadline_exceeded / content_too_large / provenance_rejected / invalid_request. Never turn missing/error into an empty successful transcript or roll back an already successful filing source.

### 3.3 G1e end-to-end acceptance (milestone gate; restore test root after run)

- Use a new unique root company-wiki/tests/e2e/runs/<run_id>/ and first record recursive path + SHA-256 + byte length baseline.
- Use temporary company-wiki catalog/raw roots and a fake transcript process/provider. Do not use live Motley Fool/FMP, real company directories, production config/database/cache/lock.
- Successful path must cross: new filing-fetch request → existing filing resolver (fake source) → companion CLI JSON → HTTPS/period/hash validation → company-wiki canonical writer → sidecar/catalog → source-oriented summary/locator. Calling parser directly does not count as orchestrator E2E.
- Run the same request twice. On resolver hit, provider calls must be 0; in all cases canonical-source additions must be 0 on exact repeat. Source ID, body SHA, and locator stay stable. Filing and transcript statuses stay separately observable.
- Failure subcases: no authorization, not_found, ambiguous, wrong request ID/schema, non-HTTPS provenance, timeout, oversized body, and simulated restart after catalog commit. Each must preserve filing success while correctly reporting transcript failure; never publish partial content.
- Remove only this run_id directory. Compare before/after paths, hashes, and lengths: prior files unchanged and every new file absent. Cleanup failure makes the test fail. Never remove pre-existing tests/e2e content.
- One G1e review gate checks contract/code diff, test receipt, and run-root restoration receipt. Unit tests do not substitute for this E2E.

### 3.4 Current cross-repository coordination

- Latest inspected revenue-forecast register: I-16-A in flight (eb9a824f, read-card, as of 2026-09-26); the root working tree has 4,087 dirty status lines, and registered reviewer worktrees are detached August snapshots.
- Therefore, this turn only changes the independent E-T repository. Wait until the active card's current closure receipt, owner lock, and repo snapshots are re-read before changing filing-fetch/company-wiki.
- Official FMP API documentation was checked on 2026-09-27: `/stable/earning-call-transcript?symbol=...&year=...&quarter=...` returns JSON with symbol, year, period, date, and content. One authenticated call using the user-supplied key file returned HTTP 402; no transcript body was returned or persisted. FMP pricing and terms say transcript coverage and display/redistribution permissions depend on plan/agreement. Do not retry until access is confirmed.
- On resumption, read the actual control entry/card and machine snapshot. Use their current accepted snapshot; do not edit their plan or characterize plan-only hash drift as a code conflict.

## 2026-09-29 ET owner revision (supersedes the earlier two-gate bullets above)

- [x] Saved the existing relevant WIP as recoverable commit `21336d2`; left the unrelated July `eval_results.json` and old `.workbuddy-ai/memory/*` untracked.
- [x] Made `download_authorized` the one request-level network intent. The legacy `--allow-download` spelling remains accepted but has no separate gating authority; a false request still makes zero HTTP calls.
- [x] Added one immutable provider configuration shared by fetch, discover, and candidate-fetch. Motley Fool defaults disabled at all three entrypoints before HTTP session creation. Fake-only tests can explicitly enable its parser/candidate path.
- [x] Named FMP missing key and 401/402/403 as `unavailable` with distinct codes, and HTTP 429 as `rate_limited`. Wrong host/redirect/period, timeout, raw-byte and canonical-body budgets remain fail-closed.
- [x] Added a private session-factory seam in the real CLI dispatch. Fake FMP HTTP plus a fake key now passes true stdin JSON → CLI → API → transport → stdout JSON without provider access, translation, or extra files.
- [x] Generated stable `earnings-transcript-result/2` producer goldens from the real CLI serializer under a fixed test clock. FMP remains 26 fields; test-only Motley candidate remains 24 fields. See `tests/golden/manifest.json`.
- [x] Focused API/translation suite: 41 passed; whole offline suite: 118 passed, 2 deselected. Both used a unique ET `tests/et-run-*` root and restored it in `finally`.
- [ ] Cross-repository CWP FMP importer contract and FF companion integration remain outside this ET owner scope. FMP endpoint entitlement and retention rights remain unverified; the historical live attempt returned 402 and was not repeated.

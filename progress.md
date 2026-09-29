# 进度日志

## 2026-07-08

### 会话开始
- 完成：深度分析报告（设计、架构、代码质量、测试、文档）
- 输出：20 个改进建议，分 P0/P1/P2/P3 四级
- 规划文件：task_plan.md, findings.md, progress.md 已创建

### Phase 0 完成
- README: 修复优先级文本、测试数量、config.json 注释
- translate.py: --backend choices 添加 mimo/minimax
- config.yaml: system_prompt 去重，提取到 translate 区块，三个 LLM 区块移除重复
- common.py: _OpenAITranslator 添加 fallback 到 translate.system_prompt
- reader.py: 改用 common.load_companies 和 parse_transcript_header，删除重复代码
- common.py: TranslatorFactory.create docstring 优先级顺序修正
- 测试：33 passed, 4 skipped

### Phase 1 完成
- 1.1 完成: common.py 拆分为 5 个子模块
  - config.py (64行): load_config, get_path, setup_logging, load_companies
  - naming.py (62行): FileNaming
  - cache.py (36行): JsonCache
  - parser.py (83行): LineClassifier, split_paragraphs, parse_transcript_header, extract_body, build_bilingual_data
  - translator.py (242行): _OpenAITranslator, 3个子类, TranslatorFactory, GoogleTranslator, text_hash, translate_paragraphs
  - common.py (26行): re-export 门面，保持向后兼容
- 1.2 完成: reader.py HTML 模板提取到 templates/index.html（610→138行）
- 1.3 完成: build_bilingual_data 提取到 parser.py，scraper.py/translate.py 改用共享函数
- 1.3 附带: 清理 translate.py/scraper.py 未使用 imports
- 1.4 完成: 全局 logger 改为模块级 `log = logging.getLogger(__name__)`
- 1.5 完成: 创建 mock_cfg fixture

### Phase 2 完成
- 2.1 完成: 删除 get_path 死代码（`if base == Path(".")` 判断）
- 2.2 完成: 添加 scraper 测试（5 个）：test_scraper.py
- 2.3 完成: 添加 reader 测试（7 个）：test_reader.py
- 2.4 完成: translate_paragraphs 添加翻译失败摘要日志
- 测试：45 passed, 4 skipped

## 2026-09-27 — transcript companion boundary

- The user authorized the earnings-transcripts upgrade and supplied its GitHub URL. Inner repository: C:\Users\郑曾波\Projects\earnings-transcripts\earnings-transcripts; origin is https://github.com/zhengcb81/earnings-transcripts.git.
- Preserved pre-existing untracked notes/data; created branch codex/transcript-companion-adapter.
- Valid baseline from the E-T repository root with isolated basetemp: 78 passed, 1 failed. Sole failure: test_factory_auto_prefers_llm_when_keyed expects an LLM API key that is unavailable in this environment. Earlier wrong-CWD runs produced unrelated temp/config path errors and are not the baseline.
- Added tests/test_transcript_api.py first; RED confirmed the missing transcript_api module. Implemented transcript_api.py and transcript_tool.py.
- The public boundary is exact-period, as-of bounded, Motley-Fool-only, HTTPS-host checked, redirect bounded, byte bounded, and deadline bounded. It has no disk/config/cache/log/translation effects. Provider-page and canonical UTF-8-body hashes are separate.
- Focused tests: 10 passed using a unique company-wiki .tmp-e2e basetemp, removed after the run. No live provider was called.
- Revenue-forecast coordination was refreshed: .planning/2026-09-19-three-project-history-audit/REMEDIATION_REGISTER.md marks I-16-A in flight (eb9a824f, read-card, 2026-09-26); many reviewer worktrees and 2026-09-27 run outputs exist. Do not edit RF; defer filing-fetch/company-wiki changes until active card/lock/snapshot is rechecked and clear.
- Remaining: rerun E-T regression excluding/isolating the existing environment-only LLM-key test; finish README/plan review; after RF synchronization, wire filing-fetch and company-wiki canonical import and run the isolated G1e E2E. No live download canary is planned.

### E-T companion verification (2026-09-27)
- Full existing suite plus the new companion tests: 88 passed, 1 deselected, 2 existing deprecation warnings. The deselected test is `test_factory_auto_prefers_llm_when_keyed`; it failed on the baseline because this machine has no configured LLM API key. No credential was added.
- Isolated pytest basetemp was created under company-wiki `.tmp-e2e`, validated as a child of that path, then removed in `finally`; pre-existing parent state was preserved. No provider/network call was made.
- `ruff check --no-cache transcript_api.py transcript_tool.py tests/test_transcript_api.py`: all checks passed. AST parse passed for all three files.
- Cross-repository handoff remains pending: RF I-16-A is still recorded as in-flight. The RF root has 4,087 dirty status lines and the registered reviewer worktrees are detached August snapshots; no shared company-wiki or filing-fetch file was changed.

### FMP provider expansion and live-canary outcome (2026-09-27)
- Official FMP documentation verified the stable exact-quarter transcript endpoint and JSON fields. The E-T adapter now supports both Motley Fool and FMP, with FMP credentials read only from `FMP_API_KEY` in the caller process and redacted from all returned metadata/source URLs.
- Added provider identity, exact quarter/year, HTTP redirect, content-size, call-date/as-of caveat, payload/body hash, missing-credential, and sanitized HTTP-status tests. FMP `date` is represented as `call_date`; `publication_date` stays null and `as_of_cutoff_verified=false`.
- User supplied a local FMP API-key file path and explicitly authorized a real test. A single exact AAPL 2020-Q3 request returned sanitized HTTP 402 (Payment Required). No transcript was returned to the application, no body/key was printed, no file was written, and no further live request will occur until endpoint entitlement is confirmed.
- Final offline regression: 95 passed, 1 deselected (the pre-existing test requiring an unavailable LLM API key). `ruff check --no-cache transcript_api.py transcript_tool.py tests/test_transcript_api.py` and AST syntax checks passed. The isolated company-wiki pytest basetemp was removed; no project production config/data was modified.
- Current broader FMP research: candidate data categories and ownership boundaries are being assessed across company-wiki, filing-fetch, revenue-forecast, and StockWiki. Do not import FMP response data into the canonical company-wiki store until provider date semantics and the account's retention/display rights are addressed; do not alter shared repositories while RF I-16-A remains active.
## Session: opt-in 原始 provider payload handoff（2026-09-27）

- 为 `fetch_transcript` 增加显式 `include_source_payload`，CLI 增加 `--include-source-payload`。默认 schema `/1` 与原先英文文本结果保持兼容；启用后使用 result `/2`，仅对成功结果返回受限 base64 原始响应、规范 MIME 和安全 effective URL，不重复返回 `content_utf8`，且仍不写文件。
- Motley Fool 仅接受 HTML/XHTML 原件 MIME，effective URL 必须仍在 HTTPS `www.fool.com` 且移除 query/fragment；FMP 仅接受 JSON，effective URL 只含 symbol/year/quarter、绝不含 API key。源 bytes 受原有 provider 请求限额约束，编码 payload 上限 24 MiB。无 raw MIME、超限及身份错误均 fail closed。
- 新增假 HTTP 覆盖 Motley Fool 完整 redirect→raw bytes→hash、secret/query 清理、错误 MIME、FMP 原始 JSON/hash/API key 不泄漏、默认兼容，以及 CLI 未同时 `--allow-download` 时 zero-network 授权拒绝。
- 定向验收 `tests/test_transcript_api.py`: **22 passed in 2.28s**；`ruff check --no-cache` 全绿；`git diff --check` 通过。唯一 pytest basetemp 使用本次随机独立 TEMP 目录，运行前确认不存在、运行后按精确路径确认 containment/reparse 并清理至不存在。未调用真实 provider/FMP，无任何下载或原件持久化。
- 下一步 company-wiki 只做 isolated importer：验证 `/2` protocol、base64 解码/hash/MIME/effective URL 后复用既有 request-time 与 post-fetch rights gates、CanonicalSourceWriter、紧凑 lineage；fake E2E 测试运行根必须回到原始不存在状态。filing-fetch、revenue-forecast 及其共享 schema/role DAG 继续冻结。
## Session: exact candidate fetch 前置授权（2026-09-27）

- G1e-B schema `/2` 后做时序复核发现：兼容 `fetch_transcript` 把 Motley Fool quote-listing discovery 与 transcript body retrieval 合在一条调用中，公司-Wiki 无法在 body request 前对精确 URL/文档 ID 做 rights 与 DownloadAuthorization check。
- 新增 `discover_transcripts` 和 CLI `--operation discover`：只返回有界候选 metadata，不访问候选正文；ambiguous 返回候选元数据但不自动选择。新增严格 `earnings-transcript-candidate-fetch-request/1` 与 `fetch_transcript_candidate` / `--operation fetch-candidate`：调用者提交 exact provider_document_id、source_url、published_date 后，仅请求该 URL；在网络前校验 host、规范 URL、ticker slug、FY/Q、URL 日期、as-of 与 document ID；返回 `/1` 正文或 opt-in `/2` 原始 bytes/MIME/effective URL。旧 combined `--operation fetch` 只为向后兼容保留，CWP/filing-fetch 不得调用。
- 新测试覆盖 discovery 不取正文、ambiguous 不 fetch、candidate-bound fetch 单 body 请求、错 host/期次/ID/未授权零请求，以及 CLI operator gate。接口定向 **28 passed in 2.03s**；全量 E-T 离线回归 **106 passed, 1 deselected, 2 existing deprecation warnings in 3.45s**。Ruff 与 `git diff --check` 全绿；pytest 唯一 run root 开始前确认不存在，运行后核对 TEMP containment/reparse，再精确删除并验证不存在。
- 安全边界：discovery 本身仍发 listing 网络请求，集成调用方须在调用前通过 policy `discover` action；候选 fetch 前必须复用 CWP 现存精确下载授权 + rights policy。FMP 仍不支持此两阶段候选流程，且当前 402/留存权未解，不纳入接线。

## Session: 翻译关闭开关与 company-wiki 原文路径（2026-09-27）

- 复查发现 `scraper.py` 原来已有 `--no-translate`，并且 Motley Fool/FMP 下载后路径、已有英文文件的增量判断、dry-run 计划都使用这个布尔值；README 也已有旧参数示例。没有另造第二套翻译机制。
- 为明确表达“只保存英文原文”的新调用意图，将 argument parser 提取为可测函数，并增加 `--disable-translation` 同义旗标；旧 `--no-translate` 保持兼容。README 示例改用新名称。
- 新增无网络测试：两个参数都解析为同一 `no_translate=true`；skip 分支必须在访问原文路径、创建 TranslatorFactory、读写文件之前返回。company-wiki 使用的 `transcript_tool.py`/`transcript_api.py` 本来就没有翻译器导入、配置读取或双语输出；因此不向这个已翻译关闭的 API 增加空操作旗标。
- 第一次全量回归为 **109 passed, 1 deselected, 2 warnings**。随后检查到既有 `test_google_translate` 会把合成短句交给 GoogleTranslator，若 translator 可用可能产生外部翻译请求；该次运行没有处理任何公司文档，但这项测试不适合作为离线门禁。此后明确排除该测试及缺本机 LLM 凭证的 factory-preference 测试，离线回归 **108 passed, 2 deselected**，无 warnings。未来离线验收继续排除这两项。
- 新翻译控制测试验证 `--no-translate` 与 `--disable-translation` 等价，并断言 skip 路径不初始化翻译器、不创建文件。company-wiki companion API 的既有 fake HTTP 回归一起通过。
- Ruff：新测试/API 文件全绿；scraper.py 使用 `--ignore F401,F541` 的有界复核全绿。普通全文件 Ruff 仍报告三个本次 diff 之外的旧项（LineClassifier/text_hash 未使用导入及多余 f-string），没有顺手改动这些无关代码。`git diff --check` 通过。
- 测试过程中没有读取任何 API 凭证、没有下载 transcript、没有处理或翻译公司文档；那次既有翻译 smoke test 只可能触发对合成短句的 Google 翻译。离线回归的唯一 pytest run tree 经路径/reparse 校验后已删除；没有改 `transcripts/`、生产配置或缓存。

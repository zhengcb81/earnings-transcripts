# Progress Log — N5-ET-TXT

## Session: 2026-10-06

### Phase 1: 复现与 RED 测试 — complete

- **Status:** complete
- Actions taken:
  - 读卡（n5_et_local_text_integrity.md）+ 参考包（n5_parallel_packages_2026-10-06.md）+ 源仓根 task_plan/progress + `docs/implementation/s3-et-deadline-handoff.md`、`s3-et-runtime-handoff.md`
  - 解析基线 `63c4090083cd34ece2ebb23affab81a419bc79dc`；`git worktree add -b codex/n5-et-local-text` → `C:/Users/郑曾波/Projects/cwp-lanes-20261006/et-local-text`
  - 独立 PWF：`.planning/n5-et-local-text/{task_plan,findings,progress}.md`（不覆盖根历史计划；`.active_plan` 属卡外路径，未提交并已删除）
  - CodeGraph 未初始化 → 按卡报告不初始化，直接读 scraper.py（`_finalize_existing` :508、`completed_entry` :308、`_store_original`、两个调用点）、parser.py、naming.py、tests
  - RED：`tests/test_batch_runtime.py` 新增 5 例（空文件/错ticker/错期间/Characters 500000/URL-only）→ 运行 **5 failed**（全部仍报 reused）＝缺口复现
- Files created/modified: `tests/test_batch_runtime.py`（+5 RED 例与 `_seed_header_original`/`_manifest_entries` 助手）

### Phase 2: transcript_artifact.py — complete

- **Status:** complete
- Actions taken:
  - 新增 `transcript_artifact.py`：`verify_stored_original`（严格 UTF-8 → 正文非空 → 头部矛盾 → 收据 schema/身份/期间/字节绑定 → 无收据可证明性）、`build_download_receipt`/`build_audit_receipt`/`parse_receipt`/`read_receipt`/`write_atomic`、digest/label 纯函数
  - 新增 `tests/test_transcript_artifact.py`：44 例（中英文、空/截断/乱码/不可读、角色头部+含分隔符正文、不同季度、URL 规则、证明不了、download/audit 收据、坏收据 6 型+schema 10 违例+伪造下载字段、篡改、LF/CRLF、原子写失败）
- Files created/modified: `transcript_artifact.py`、`tests/test_transcript_artifact.py`

### Phase 3: scraper 接线 — complete

- **Status:** complete
- Actions taken:
  - `_finalize_existing` 改调 `verify_stored_original`：verified→`reused`、legacy→`legacy_unverified`、不可证→`unknown`+stderr warning、矛盾/损坏→stderr `output_conflict/<code>`+`_ERROR_STATUSES` 既有映射（rc 1）；`content_bytes`=实算 body 字节（不再用 Characters）；仅 verified/legacy 走 `_maybe_translate`
  - `_store_original`：既有文件严格 UTF-8 读（坏编码→output_conflict）；新文件 render 后按实际写入字节建 download 收据，与 TXT 一起 `take_output` 计额，txt 原子落盘后 `write_atomic` 收据，收据失败仅 `log.warning receipt_write_failed`（原件不动）；函数签名未改
  - RED 5 例转绿；既有复用/冲突/completed_entry 测试不改全过
- Files created/modified: `scraper.py`

### Phase 4: audit CLI — complete

- **Status:** complete
- Actions taken:
  - 新增 `transcript_audit.py`：只读默认 JSON stdout、`--report-dir` 原子小报告、`--write-receipts` 显式补 audit-legacy 收据（已有收据 conflict 不覆盖、不可证 skipped、TXT 永不碰）；退出码 0/1/2
  - 结构保证：不 import scraper/translator/requests/transcript_api/retrieval_runtime（子进程测试断言）
  - 实跑：43 个真实 TXT → `legacy_unverified`×43、rc 0、`git status transcripts` 干净
- Files created/modified: `transcript_audit.py`、`tests/test_transcript_audit.py`（8 例）

### Phase 5: 集中测试节点 — complete

- **Status:** complete
- Actions taken:
  - Integration/E2E（test_batch_runtime 新增）：假 HTTP→原子 TXT+receipt（schema/哈希/字段逐项断言）→二跑 **零 HTTP** `reused`→篡改 `receipt_mismatch` rc1 原件保留→坏收据 `receipt_invalid` rc1 不修复→收据写失败原件保留且二跑 `legacy_unverified`→复用路径 translator 陷阱 0
  - 真实 MSFT 只读 audit（test_transcript_audit）：前后 size/mtime_ns/SHA-256 快照一致、零 `*.receipt.json`、counts 全 legacy_unverified、spot-check Q1 2025
  - 集中一次验证见下表
- Files created/modified: `tests/test_batch_runtime.py`（+6 集成例）、`tests/test_transcript_audit.py`

### Phase 6: 文档与交接 — in_progress

- **Status:** in_progress
- Actions taken:
  - README：项目结构（+2 模块）、增量执行核验收据表+完整性边界、audit CLI 段、输出格式收据行、测试计数 172→234
  - 待办：HANDOFF.md + handoff.json → 两段提交
- Files created/modified: `README.md`

## Test Results

| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| RED 复现（5 例） | 实现前跑 `tests/test_batch_runtime.py` 新例 | 全 failed（仍 reused） | 5 failed | PASS（红=复现） |
| `pytest tests/test_batch_runtime.py tests/test_scraper.py` | 接线后 | 兼容（输出码不变） | 51 passed | PASS |
| `pytest tests/test_transcript_artifact.py` | 单元 | 44 passed | 44 passed | PASS |
| `pytest tests/test_transcript_audit.py` | audit CLI | 8 passed | 8 passed | PASS |
| **`pytest tests/ -q`（集中全量）** | 基线 172 | ≥172 且新增全过 | **234 passed, 99.45s** | PASS |
| `python tests/generate_transcript_goldens.py --check` | 10 goldens | matched | **10 goldens matched** | PASS |
| `ruff check scraper.py transcript_artifact.py transcript_audit.py tests/test_transcript_artifact.py tests/test_transcript_audit.py tests/test_batch_runtime.py` | 本包文件 | clean | All checks passed | PASS |
| `git diff --check` | 工作区 | 无空白错误 | 干净 | PASS |
| `transcript_audit.py` 实跑（真实语料 43 TXT） | 只读 | rc0、零写入 | legacy_unverified×43、git status 干净 | PASS |
| 基线 config.json 缺失 | worktree 首跑 1 failed | 按 S3 先例补本地 config.json | 复跑 46/46 过 | PASS |

## Error Log

| Timestamp | Error | Attempt | Resolution |
|-----------|-------|---------|------------|
| 2026-10-06 | 基线 `test_factory_auto_prefers_llm_when_keyed` failed | 1 | worktree 缺 gitignored `config.json`；从源仓复制（S3 交接同款做法，不提交） |
| 2026-10-06 | audit CLI 首跑 `files: []` | 1 | `Path.rglob` 不支持 `re.escape` 的 `\\.txt`；改裸 glob 模式（与 FileNaming 一致） |
| 2026-10-06 | 管道重定向 JSON 中文解码失败（cp936） | 1 | stdout 非 UTF-8 时 `reconfigure(encoding="utf-8")`；UTF-8 场景零副作用 |
| 2026-10-06 | 单测 `body_bytes 55≠53` | 1 | Windows `write_text` 写成 CRLF；helper 改按字节写 + CRLF 专测；查明真语料 43/43 均 CRLF |
| 2026-10-06 | ruff F401 `json` unused | 1 | `ruff --fix` 后复跑 44 passed |
| 2026-10-06 | 编辑器保存时对整文件自动格式化，scraper.py/test_batch_runtime.py 出现 59 个无关 hunk | 1 | 存当前版本 → `git checkout` 回 HEAD → 脚本按锚点只重放本包区域（import、`_download_receipt_bytes`+`_store_original`、`_ENTRY_STATUS_FOR_OUTCOME`+`_finalize_existing`、测试块）；hunk 降到 5+1，复跑全量 234 passed + 10 goldens + ruff clean |

## 5-Question Reboot Check

| Question | Answer |
|----------|--------|
| Where am I? | Phase 6（文档与交接） |
| Where am I going? | HANDOFF.md + handoff.json → 两段提交 |
| What's the goal? | 本地 TXT 正确复用 + 小收据 + 只读 audit，原件不丢、wire 不动 |
| What have I learned? | See findings.md |
| What have I done? | See phases 1–5 above |

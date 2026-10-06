# N5-ET-TXT 交接：本地电话会 TXT 正确复用与小收据

## 1. 分支与基线

- 源仓：`C:/Users/郑曾波/Projects/earnings-transcripts/earnings-transcripts`（未改动；
  仅含既有 untracked `.workbuddy-ai/`、`eval_results.json`，未复制、未清理）。
- 基线 `63c4090083cd34ece2ebb23affab81a419bc79dc`（= 卡上 63c4090，源仓解析的完整 SHA）。
- 交付分支 `codex/n5-et-local-text`（从该基线建立），唯一 worktree：
  `C:/Users/郑曾波/Projects/cwp-lanes-20261006/et-local-text`。
- 本包 PWF：`.planning/n5-et-local-text/{task_plan,findings,progress}.md`（随分支提交）。
- 基线测试 172 passed + 10 goldens matched；本包完成 **234 passed + 10 goldens matched**。
- worktree 本地复制了 gitignored `config.json`（LLM keys，仅供既有翻译器测试），
  未提交、不属交付（与 ET-DEADLINE 交接同款处置）。
- CodeGraph 在源仓未初始化：按卡只报告、不初始化；全程直接读文件定位。
- 代码提交（delivery_head）：`96c9bc8b0b4610a4e4660918bbfb9cbe80fa0395`；
  本交接文档为随后的 docs 提交。

## 2. 修改文件

| 文件 | 改动 |
|---|---|
| `transcript_artifact.py` | **新增**：唯一验证实现 `verify_stored_original`（严格 UTF-8 → 正文非空 → 头部 Ticker/URL/Quarter 矛盾 → 收据 schema/身份/期间/字节绑定 → 无收据时头部可证明性）；`et-local-text-receipt/1` 收据构建/解析/读写、`write_atomic`（tmp+`os.replace`+finally 清理）、digest/label 纯函数。仅依赖 `parser`，0 网络 0 翻译 |
| `transcript_audit.py` | **新增**：只读默认的 audit CLI（JSON stdout / `--report-dir` 原子小报告 / 显式 `--write-receipts` 补 audit-legacy 收据）；不 import scraper/translator/requests/transcript_api/retrieval_runtime（子进程测试断言） |
| `scraper.py` | `_finalize_existing` 改调共享验证（不再用 `completed_entry`/Characters 当字节）；新增 `_ENTRY_STATUS_FOR_OUTCOME`、`_download_receipt_bytes`；`_store_original` 新文件附原子 sidecar 收据（字节计入 `--max-output-bytes`，收据失败仅 `log.warning receipt_write_failed`、原件不动）、既有文件改严格 UTF-8 读。函数签名、`completed_entry`（summary 诊断用）、输出码词汇均未改 |
| `tests/test_transcript_artifact.py` | **新增** 44 例：中英文、空/截断/乱码/不可读、角色头部+含分隔符正文、不同季度、URL 规则、证明不了、download/audit 收据、坏收据 6 型 + schema 10 违例 + 伪造下载字段、篡改、LF/CRLF、原子写失败 |
| `tests/test_transcript_audit.py` | **新增** 8 例：只读默认、报告目录不碰语料、缺根具名失败、文件名不可解析、补收据不改原件不覆盖、坏收据 conflict、结构零依赖子进程、**真实 MSFT 只读 audit** |
| `tests/test_batch_runtime.py` | 新增 11 例：RED 复现（空/错 ticker/错期间/Characters 500000/URL-only）+ 集成（原子 TXT+收据→二跑零 HTTP `reused`→篡改 `receipt_mismatch`→坏收据 `receipt_invalid`→收据中断原件不丢→复用路径 translator 陷阱 0）；既有用例一行未改 |
| `README.md` | 增量执行核验收据表与完整性边界、audit CLI 段、输出格式收据行、测试计数 172→234 |
| `.planning/n5-et-local-text/` | 本包独立 PWF（不覆盖根历史计划） |
| `docs/implementation/handoffs/N5-ET-TXT/` | 本文件与 `handoff.json` |

未改动：`transcript_tool.py`、`transcript_api.py`、全部 `/1`/`/2`/discovery/candidate
协议与 wire、`tests/golden/`（10 份 goldens 零 diff）、provider policy、`config.yaml`、
翻译配置、CI 策略、`parser.py`/`naming.py`/`common.py`、源仓 owner 文件、跨仓代码。

## 3. 本地旧格式兼容策略（复用判定）

期望只来自**调用方参数**（ticker、`Q{n} {yyyy}` label、可选 expected_url）；文件名/
路径只产生期望，**永远不是证据**（“错误身份不能仅凭文件名纠正”）。

| 证据状态 | 条目 status | error_code | rc |
|---|---|---|---|
| kind=download 收据与当前字节/身份/期间逐项吻合 | `reused`（verified） | — | 0 |
| 无收据，头部 `Ticker`（或给定且头部写明的 `URL`）+ `Quarter` 都能证明、正文非空 | `legacy_unverified` | — | 0 |
| kind=audit-legacy 收据吻合 | `legacy_unverified`（audit 收据不冒充下载证明） | — | 0 |
| 证明不了身份/期间（仅 URL、无头部、缺 Quarter、文件名不可解析） | `unknown` | `identity_missing` | 0 |
| 头部或收据与期望矛盾 | `output_conflict` | `identity_mismatch` / `period_mismatch` | 1 |
| 空/仅空白正文、乱码、不可读 | `output_conflict` | `empty_original` / `invalid_encoding` / `unreadable` | 1 |
| 收据坏 JSON/schema/伪造字段、收据与字节不符 | `output_conflict` | `receipt_invalid` / `receipt_mismatch` | 1 |

- 输出码兼容：`unknown`/`legacy_unverified` 不进 `_ERROR_STATUSES`（rc 0，沿用
  `not_found`/`ambiguous` “合法结果”先例）；冲突/损坏沿用既有 `output_conflict` 词汇
  → rc 1。`unknown` 以 stderr `warning: <ticker> <fiscal>: unknown/identity_missing` 具名。
- `content_bytes` 一律为**实算正文 UTF-8 字节**；头部 `Characters` 只留在
  `completed_entry.char_count`（summary 诊断），从不作字节长度。
- **完整性边界**：只证明“当前字节 = 收据字节、身份/期间相符”，不证明内容完整
  （无法凭关键词判断截断）；截断文件若头部可证明 → `legacy_unverified`。
- 旧语料兼容：真实 43 份 TXT 全为 CRLF（历史 `write_text` 所致），验证按实际字节
  计算哈希，全部 `legacy_unverified`；新落盘走 `write_bytes`（LF），两种 newline 均有单测。
- 翻译：仅 `reused`/`legacy_unverified` 在显式 `--translate` 时补翻译；`unknown` 与
  冲突不派生翻译；默认零翻译器构造（陷阱测试）。
- `_store_original` 既有文件分支（仅并发竞态可达）同步改严格 UTF-8：坏编码 →
  `output_conflict`（原 `errors="ignore"` 会静默吞坏字节）。

## 4. 小收据（et-local-text-receipt/1）

- 位置：与 TXT 同目录 sidecar `*_earnings_call.receipt.json`；**实测 618 B**
  （含长 source_url；audit-legacy 版更小），约 0.5–0.7 KB。
- 字段：`schema`、`kind(download|audit-legacy)`、`ticker`、`fiscal_year`、
  `fiscal_quarter`、`fiscal_period`、`provider`、`source_url`（实际已知才填，
  `"N/A"` → null）、`extraction_version`（download=`et-store-original/1`，audit=null）、
  `obtained_at`（取得时间；audit=null）、`published_date` **恒 null**、
  `body_sha256`/`body_bytes`（canonical 正文=头部后全部裁首尾空白）、
  `file_sha256`/`file_bytes`（整文件原字节）。
- 收据是 **ET 本地附件**：不宣称 provider 原始 HTTP hash、公开日/as-of 证明、
  CWP 准入，不构成第二个 canonical 来源库；timestamp 不冒充公开日；无需人工签名
  或授权文件。
- 落盘：与 TXT 相同原子机制（tmp+`os.replace`+finally 清理）；TXT 先落、收据后写，
  中断则下次走 legacy 头部证明路径，原件不丢；收据字节与 TXT 一起计入
  `--max-output-bytes`（先计额后写，避免写后超限）。
- audit-legacy 收据强制 `obtained_at`/`extraction_version` 为 null（schema 拒绝伪造），
  只记录当前字节与头部可证明字段（provider/URL 来自头部，不可证即 null）。

## 5. audit CLI 具体命令

```bash
C:/Miniconda/python.exe transcript_audit.py                      # 只读，JSON 到 stdout
C:/Miniconda/python.exe transcript_audit.py --report-dir OUT     # 小报告原子写独立目录
C:/Miniconda/python.exe transcript_audit.py --write-receipts     # 显式补 audit-legacy 收据
C:/Miniconda/python.exe transcript_audit.py --root DIR [...]     # 指定被审目录
```

- 默认 0 写入、0 网络、0 翻译、0 外部 LLM；退出码 0=无具名失败，1=有损坏/矛盾/
  坏收据或根缺失，2=用法（argparse）。
- `--write-receipts` 只加 sidecar：可证明且无收据 → `written`；已有收据 → `exists`
  （矛盾时 `conflict`，不覆盖）；不可证 → `skipped`；TXT 的 SHA/size/mtime 全程不变。
- 实测（真实语料）：43 文件全部 `legacy_unverified`、0 具名失败、`git status transcripts`
  干净（**未**对真实语料执行 `--write-receipts`，遵守“不在源仓 transcripts 生成收据”）。

## 6. 测试与验证（实测，非 fixture 估计）

```powershell
C:/Miniconda/python.exe -m pytest tests/ -q                    # 234 passed (77.76s)
C:/Miniconda/python.exe tests/generate_transcript_goldens.py --check   # 10 goldens matched (0.68s)
C:/Miniconda/python.exe -m ruff check scraper.py transcript_artifact.py transcript_audit.py tests/test_transcript_artifact.py tests/test_transcript_audit.py tests/test_batch_runtime.py   # clean (0.19s)
git diff --check                                                # 无空白错误
```

- 全部测试离线：假 HTTP 走既有私有 launcher seam（FMP key 全为假值/缺省），写入限
  pytest `tmp_path` 或 `--output`；translator 陷阱断言默认与复用路径 0 构造。
- **实际 vs fixture**：真实样本仅 MSFT 只读 audit——
  `transcripts/MSFT/MSFT_Q1_2025_earnings_call.txt`，
  SHA-256 `ac1cc6170dcfaa68e586dfcdc6c5bf2e4de05cc0bfb7836013c37cc60eb701ae`，
  60363 bytes，前后 size/mtime_ns/SHA 一致、无收据生成（`metadata_is_fixture: false`）；
  其余批次/收据用例全部为 tmp 目录内合成 fixture。
- RED→GREEN：5 个复现例实现前全 failed（旧路径确实 reused），实现后全绿；
  goldens 未被改动来“变绿”（`git diff tests/golden` 为空）。
- 真实语料 inventory：43 份 `*_earnings_call.txt`、0 份 `*.receipt.json`（前后一致）。

## 7. 残留问题

1. CodeGraph 源仓未初始化（按卡未动）；本包用直接读文件完成定位。
2. 仓库其余文件存在 41 个**既有** ruff 错误（`common.py`/`reader.py`/`translate.py` 等，
   基线即有）；本包相关 6 个文件 clean，未顺手修（外科手术边界）。
3. `_existing_status`（store 路径）仍按 `.strip()` 字节比较，对“CRLF 旧原件 + LF 新抓
   理论同文”会判 conflict——该分支仅竞态可达（存在即复用不再抓取），属既有行为，未改。
4. `--write-receipts` 未对真实语料执行；如需给旧语料补收据，请在副本或自有输出目录
   显式运行（卡禁止在源仓 transcripts 生成收据）。
5. 中断一致性为两段 replace（TXT 先、收据后）：收据半写由 `receipt_invalid` 具名失败，
   原件保留；不提供跨两文件的单事务。
6. 本分支尚未推送远端（等待 MAIN/用户决定合入方式）。

## 8. 声明边界

本包只覆盖 N5-ET-TXT 卡内范围：**不能声称免费 provider 已可用、endpoint 权益已升级
或 CWP 准入已升级**；不改变 FF→ET→CWP 公共合同与任何公共 wire；MAIN 统一验收合入。

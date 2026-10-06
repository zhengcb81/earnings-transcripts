# Task Plan: N5-ET-TXT 本地电话会TXT正确复用与小收据

## Goal

在 `codex/n5-et-local-text` 分支上修复 ET 本地批次对已有 TXT 的错复用（空文件/错ticker/错期间/Characters虚报仍报 reused），为每个新落盘原件写 `et-local-text-receipt/1` 小收据，并提供只读默认的本地 audit CLI；原件不丢、不覆盖、公共 wire 不动。

## Next Step

写 `docs/implementation/handoffs/N5-ET-TXT/{HANDOFF.md,handoff.json}` 并按两段提交（先代码提交取 delivery_head，再交 handoff 提交）。

## Current Phase

Phase 6

## Phases

### Phase 1: 复现与 RED 测试

- [x] 读卡/参考包/源仓规划与 S3 交接；解析基线完整 SHA 63c4090083cd34ece2ebb23affab81a419bc79dc
- [x] 建独占 worktree `cwp-lanes-20261006/et-local-text`、分支 `codex/n5-et-local-text`、独立 PWF `.planning/n5-et-local-text/`
- [x] 定位缺口：`scraper._finalize_existing`（expected_url=None 时不校验）、`completed_entry`（errors=ignore + Characters 当字节）
- [x] 写 RED：空文件/错ticker/错期间/Characters虚报/URL-only 仍 reused → 5 个全红（复现成功）
- **Status:** complete

### Phase 2: transcript_artifact.py 纯函数

- [x] 严格解码/字节与哈希/身份期间/收据验证纯函数 + receipt 构建读写（原子落盘）
- [x] 单元 44 个：中英文、空/截断/乱码、角色头部、含分隔符正文、不同季度、哈希冲突、缺失/坏收据、schema 违例、LF/CRLF、原子写失败
- **Status:** complete

### Phase 3: scraper 接线

- [x] `_finalize_existing` 调共享验证：`reused`/`legacy_unverified`/`unknown(identity_missing)`/`output_conflict/*`；输出码兼容（conflict→1、unknown/legacy→0）
- [x] `_store_original` 新文件写 receipt（原子、预算计入、失败仅具名 warning、签名未改、严格 UTF-8 读既有文件）
- [x] translate=false translator 陷阱为 0（fetch 路径既有 + 复用路径新增）；unknown/冲突不派生翻译
- **Status:** complete

### Phase 4: audit CLI（transcript_audit.py）

- [x] 只读默认 stdout JSON + `--report-dir` 小报告 + 显式 `--write-receipts`（audit-legacy 收据、不覆盖原件/已有收据）
- [x] 0 网络、0 翻译、0 外部 LLM（子进程结构测试断言未 import requests/translator/scraper/transcript_api/retrieval_runtime）
- **Status:** complete

### Phase 5: 集中测试节点

- [x] Integration/E2E：假HTTP→原子TXT+receipt→二跑零HTTP verified reuse→篡改 receipt_mismatch→坏收据 receipt_invalid→收据中断原件不丢→legacy 复用；原件断言全保留
- [x] 真实 MSFT 只读 audit：43 个原语言 TXT 前后 SHA/size/mtime 不变、零收据写入、全部 legacy_unverified
- [x] 集中跑一次：全量 **234 passed**（基线172+新增62）、**10 goldens matched**、Ruff 相关文件 clean、`git diff --check` 干净
- **Status:** complete

### Phase 6: 文档与交接

- [x] README：增量执行核验表、收据 schema/边界、audit CLI、输出格式、测试计数 234
- [ ] `docs/implementation/handoffs/N5-ET-TXT/HANDOFF.md` + `handoff.json`（cwp-independent-handoff/1）
- [ ] 两段正常提交本分支（代码 → handoff）
- **Status:** in_progress

## Key Questions

1. `unknown/identity_missing` 与 `legacy_unverified` 不进 `_ERROR_STATUSES` → 保持既有输出码 rc=0；冲突/损坏（含空文件、乱码、收据坏）走 `output_conflict/*` → rc=1。已验证。
2. Characters 只作 header 诊断字段 `char_count`（`completed_entry`/summary 保留），`content_bytes` 一律实算 body UTF-8 字节。已验证。
3. receipt 为同目录 sidecar `{stem}.receipt.json`，JSON ensure_ascii=False/indent=1，实测 618 B（含长 source_url）。已验证。

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| 收据为 TXT 同目录 sidecar `*.receipt.json` | 卡要求“ET本地附件”，不建第二来源库；随原件原子落盘 |
| 证明身份=头部 `Ticker` 或（fool）URL 与期望相符；证明期间=头部 `Quarter` 与期望相符；文件名只作期望不作证据 | 卡：错误身份不能仅凭文件名“纠正” |
| 无收据且头部可证明→`legacy_unverified`；证明不了→`unknown/identity_missing`；audit 收据命中→仍 `legacy_unverified`；download 收据命中→`reused` | 收据 kind 区分来源，audit 收据不冒充下载证明 |
| 空文件/乱码/不可读/收据坏/哈希冲突 → `output_conflict/<detail>` 具名失败，原件保留 | 卡：冲突/损坏具名失败 |
| receipt 字节计入 `--max-output-bytes`（与 TXT 一次 take_output） | 避免写后超限；README 说明 |
| 不为 `unknown`/冲突文件派生翻译 | 身份不明不生成派生产物 |
| 单测 write_txt 按字节写（LF 保真），另设 CRLF 专测 | Windows `write_text` 会转 CRLF，真语料 43/43 均为 CRLF；哈希按实际字节，断言需跨平台稳定 |
| `published_date` 恒 null、audit 收据 `obtained_at`/`extraction_version` 必须 null（schema 强制） | 卡：不宣称公开日/as-of 证明，不补造下载时来源 |
| 审计报告 stdout 前仅在编码非 UTF-8 时 `reconfigure` | 管道 GBK 下中文路径 JSON 才可读；已是 UTF-8（pytest capsys）零副作用 |

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| 基线 `test_factory_auto_prefers_llm_when_keyed` 失败 | 1 | worktree 缺 gitignored config.json；从源仓复制（同 S3 交接先例，不提交） |
| audit CLI 首跑 files=[] | 1 | `Path.rglob` 不认 `re.escape` 产物（`\\.txt`），改回与 `FileNaming` 一致的裸 glob 模式 |
| 管道输出 JSON 中文乱码（GBK） | 1 | `stdout.reconfigure(encoding="utf-8")`（仅当现编码非 utf-8） |
| 单测 body 字节数 55≠53 | 1 | 根因 Windows `write_text` 转 CRLF；helper 改按字节写 + 独立 CRLF 用例 |
| ruff F401 `json` 未用（test_transcript_artifact） | 1 | `ruff --fix` 移除，复跑 44 passed |

## Notes

- CodeGraph 未初始化：按卡报告、不初始化（交接注明）。
- 源仓 `.workbuddy-ai/`、`eval_results.json` 不动；不装 skills；零 LLM/零付费/零真实下载；goldens、公共 wire、`transcript_tool.py`、`transcript_api.py` 未改。
- 仓库全量 ruff 有 41 个**既有**错误（common/reader/translate/eval_translation 等未触碰文件），本包相关文件 clean；不顺手修（外科手术式改动）。

# DU10 Phase C 清理与原件保全收据（2026-10-17）

## 真实语料只读核验

- 验证前对 `transcripts/**` 全量 130 个文件记录 SHA256（`real_corpus_sha_before.json`）。
- 验证后逐文件复核：**130/130 字节不变**（changed=[]）。验证全程仅读，派生输出只写本 receipts 目录；往返验证的合成页与抽取结果在 `tempfile.TemporaryDirectory(prefix="du10_roundtrip_")` 中构造，with 块结束即删。
- 验证后检查系统临时目录：`du10_roundtrip_*` 残留 = 0。

## ET 主树未跟踪物保全

- `.workbuddy-ai/`（目录聚合 SHA256 = `4a3133440d7b20a0866788bd0d3a6e6fa7b5cf1c4923ffff075ba12c9036f691`）与 `eval_results.json`（SHA256 = `9d8bbf7e93fffdc2287f612556881b349f4972fee7c898ff338c36b3311e2a29`）存在且未被触碰；主树 `git status --short` 仅有这两个未跟踪项，与基线一致。
- 目录聚合方法（可复算）：对该目录 `rglob("*")` 取全部 `is_file()` 文件，按完整路径字符串排序（Python 默认序），依次 `sha256.update(完整路径字符串的 utf-8 字节)` 后 `sha256.update(文件全部字节)`，最终十六进制摘要即上值。独立审查员已另行核实承重事实（存在性 + git status 仅两项 + eval_results.json SHA 精确一致）。

## 本卡 owned 文件清单（worktree 内，全部已提交或为本 PWF 交接物）

- `transcript_extraction.py`（修改，唯一源码变更）
- `tests/test_du10_transcript_fidelity.py`、`tests/fixtures/du10_transcript_fidelity/`（6 夹具 + expected.json）
- `docs/plans/du10-txt-fidelity-2026-10-17/`（PWF + receipts）

## 无其他临时物

未创建仓库外文件；未修改下载/预算/翻译/CWP/FF 任何模块；无供应商调用、无生产写入。

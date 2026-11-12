# MB-ET 证据索引

所有日志为原始文本（未裁剪失败内容）。运行环境：Windows，`C:/Miniconda/python.exe`（Python 3.13.9），pytest 9.1.1。最终代码 tip：`8e116a4`。

| 证据 | 生成方式 | 结果 | 覆盖 |
|---|---|---|---|
| `red_et01_supervisor_import.log` | 基线 `003c1c0` checkout，新进程 `sys.meta_path` 禁止 `transcript_api`/`transcript_extraction`/`requests`/`bs4` 后 `import retrieval_runtime` | `ImportError: provider module blocked: transcript_api` | ET-01 先红 |
| `red_et02_worker_veto.log` | 基线 `003c1c0` checkout，把 `retrieval_worker.MAX_RESULT_BYTES` 缩到 64，喂合法 `list` 结果 | `worker rc = 2`，`result.json exists = False` | ET-02 先红 |
| `tests_affected.log` | `python -B -m pytest <卡内 10 文件> -q -p no:cacheprovider --basetemp=<fresh>`（tip `8e116a4`） | exit 0，`174 passed in 148.90s`，skip 0 | 受影响 suite / 回归 / 新增测试 |
| `tests_package.log` | `python -B -m pytest tests/ -q -p no:cacheprovider --basetemp=<fresh>`（tip `8e116a4`） | exit 1，`662 passed, 1 failed in 236.42s`，skip 0 | 包级 CI |
| `baseline_preexisting_failure.log` | 基线 `003c1c0` checkout 单跑 `tests/test_transcript_audit.py::test_write_receipts_never_overwrites_conflicting_receipt` | exit 1，`1 failed` | 证明唯一失败是既有缺陷 |
| `runtime_manifest.json` | 从 tip `8e116a4` 工作树复制 11 个 runtime 文件，逐文件 SHA-256 | 11 文件 / 139524 字节；`source_commit=8e116a4` | C 的交付清单与可复现输入 |
| `runtime_verify.log` | 空 cwd：`transcript_tool.py --capabilities`；以及 `transcript_tool.main(--request-stdin --include-source-payload --report-usage)` + 测试 provider（既有 `_retrieval_launcher` seam） | 两者 exit 0；capabilities 与基线逐字节相同（1597 bytes）；fetch `fetched`/`/2`/usage_complete=true/1 request/862 bytes；模块（含 `transcript_preflight`）从 artifact 解析；worker 临时根清空 | C 的实际交付包隔离验收 |

## 恢复记录

- 测试目录：每次 pytest 使用新建的 `%TEMP%\mb11-pytest-base-*` / `mb11-pytest-pkg-*` 作为 `--basetemp`（初始为空），运行后删除；仓库内无新增输出。使用独立 basetemp 是因为本机 safe-delete 策略会拦截 pytest 默认临时根的批量清理，属环境限制，不影响测试结论。
- runtime artifact：新建空临时目录（初始清单 `[]`），验证后整目录删除；manifest/log 已复制到本目录。
- 生产原件/配置/凭证/既有 WIP：未改。原仓 `main` checkout 只读用于基线对比与 RED 复现。
- 网络调用 0；模型/翻译调用 0；费用 unknown 0。

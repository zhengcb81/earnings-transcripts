# ET-S3 统一电话会采集入口与有限批次 — 任务计划

Worktree: `C:\Users\郑曾波\Projects\earnings-transcripts-s3-runtime`
Branch: `codex/et-s3-bounded-runtime` @ 4924d57（基线 120 passed，worktree 需本地 config.json，已复制、不入 Git）

## 阶段

### Stage 1: 读三入口与 legacy 调用者
**Goal**: 摸清 scraper/transcript_api/transcript_tool 合同与 legacy 类实际调用者
**Success**: 调用者清单确认（仅 scraper.main 内部 + 测试引用 helper）
**Status**: Complete

### Stage 2: RED 测试
**Goal**: 先写下失败测试锁定新行为
**Success Criteria**:
- Fool 默认 disabled：旧/现代入口均零 HTTP
- FMP `--list` 零正文/零写入；FMP `--dry-run` 零 HTTP/目录不变
- 默认原语言不创建 translator（默认无 --translate）
- `--periods` 是精确 FY/Q，不是 recent-N；`--quarters N` 无法唯一确定 → `period_unresolved`、零正文
- 批次 max-requests / max-seconds / max-response-bytes 不能被绕过（执行层前置核额度）
- `--output` 隔离全部写入；已有原件不覆盖；限额触发具名 partial/failure
- subprocess 真实退出路径：list / dry-run / 显式 periods
**Tests**: `tests/test_scraper.py` 扩展 + 新 `tests/test_batch_runtime.py`
**Status**: Not Started

### Stage 3: 重构 scraper 为薄编排
**Goal**: 删除 MotleyFoolScraper/FMPScraper 重复 HTTP，统一走 transcript_api（ProviderSettings + 流式 transport + 具名错误）
**Success Criteria**: 一个 provider 边界；无“现代失败回退旧 Session”后门；FMP 走 `/stable` 精确期；异常不再吞成 []
**Status**: Not Started

### Stage 4: 批次 limits 与 output 隔离
**Goal**: `--max-requests/--max-seconds/--max-response-bytes/--max-output-bytes` + `--output` 全覆盖 + 只存原件与小 manifest
**Success Criteria**: 每次 HTTP 前核剩余额度；单请求 timeout/cap = min(现代配置, 剩余批次额度)；临时文件 finally 清理；正文不入日志
**Status**: Not Started

### Stage 5: 责任包 + 离线 E2E + 文档 + 交接
**Goal**: 一次集中责任包；README 最少更新；`docs/implementation/s3-et-runtime-handoff.md`；commit/push 自己的 codex 分支
**Success**: 现代 `/2` goldens 无变化；测试命令与结果记录；live 权益标注 unknown
**Status**: Not Started

## 接口决策（本包）
- CLI 参数：新增 `--periods`、`--max-*`、`--translate`；保留 `--ticker/--source/--api-key/--output/--list/--dry-run/--no-translate/--disable-translation/--quarters(legacy)`；移除 `--force/--no-cache/--source both`（不覆盖已有原件、manifest 取代 .cache.json、不为吞吐做多 provider fallback）。
- 默认：原语言、限额有限默认值（README 记载）、fool provider 仍由 DEFAULT_PROVIDER_SETTINGS 决定（默认 disabled → 零 HTTP）。
- 退出码：0 成功；1 具名失败；2 用法错误；3 限额 partial（已完成文档 + 具名限额报告）。

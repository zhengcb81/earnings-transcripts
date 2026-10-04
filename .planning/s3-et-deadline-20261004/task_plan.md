# ET-DEADLINE 电话会议采集硬截止时间收口 — 任务计划

Worktree: `C:\Users\郑曾波\Projects\earnings-transcripts-s3-deadline`
Branch: `codex/et-s3-deadline` @ `93fe52c`（合入主线的 ET-S3 基线；基线 `pytest tests/ -q` = 151 passed，
goldens 10 matched；worktree 需本地复制 gitignored `config.json`，已复制、不入 Git）

来源：`docs/plans/narrative-evidence-pilot-2026-09-26/harness_lanes/et_retrieval_deadline_closeout.md`

## 阶段

### Stage 1: 摸底与固定基线
**Goal**: worktree/分支/PWF 落位，读完三入口与既有测试缝，确认缺口复现面
**Success Criteria**:
- 唯一 worktree 从 `93fe52c` 建出；PWF 三文件落 `.planning/s3-et-deadline-20261004/`
- 基线 151 passed + 10 goldens matched；owner 目录 `.workbuddy-ai/`、`eval_results.json` 未动
**Status**: Complete

### Stage 2: RED 测试
**Goal**: 先写下硬截止/资源回收/坏worker的失败测试，锁住新行为与新缝
**Success Criteria**:
- 新 `tests/test_retrieval_runtime.py`：阻塞 get 在 deadline+统一清理宽限内返回且 worker 已退出、
  临时目录清完；坏退出/缺结果/坏JSON/超大结果/请求标识不符/超期不接受 fetched（均不成功、不泄 key/body）
- 新 `tests/test_retrieval_cli_e2e.py`：真实 CLI dispatch→supervisor→worker→现有 API→serializer 的
  假 FMP 200（原语言/hash/size 正确，不 mock 整个 fetch）；402/429/缺 key 具名状态保留；默认不构造 translator
- `tests/test_batch_runtime.py` 换到 child launcher 缝（calls 文件断言），首跑红灯
**Tests**: 上述三个文件
**Status**: Complete

### Stage 3: GREEN 实现
**Goal**: 按最小方案实现 supervisor/worker 并接入两个正式入口
**Success Criteria**:
- 新 `retrieval_runtime.py`（parent supervisor）+ `retrieval_worker.py`（内部 HTTP worker）
- `retrieval_budget.py` 仅为抽出 worker 所需既有预算类而新增；scraper 仍以原名暴露旧类
- `transcript_tool.py`（无 `_session_factory` 注入时）与 `scraper.py`（list/fetch/candidate 全部）走同一 supervisor
- deadline 从正式操作起 monotonic 计时；parent 等待同一 float 剩余值；统一 1s 清理宽限；
  确认退出才删临时结果；清理异常具名报告
- 干净 API/custom session 注入保持合作式检查（不冒称硬保证）
**Status**: Complete

### Stage 4: 责任包 + 文档 + 交接
**Goal**: 一次集中运行全绿；README 范围修正；交接文档；commit/push 自己分支
**Success Criteria**:
- `python -m pytest tests/test_transcript_api.py tests/test_batch_runtime.py tests/test_translation_controls.py tests/test_retrieval_runtime.py tests/test_retrieval_cli_e2e.py -q` 全绿
- `python tests/generate_transcript_goldens.py --check` 10 goldens matched；`git diff --check` 干净
- README：`--max-seconds` 只约束采集、硬截止与宽限语义；不声称翻译受该限额
- `docs/implementation/s3-et-deadline-handoff.md` 按 lane §7 要求写全；不自行并 main
**Status**: Complete

## 接口决策（本包）

- wire 不变：`earnings-transcript-request/1`、result `/1`、`/2`、discovery `/1`、candidate 请求、
  原语言、payload/hash/size、全部 serializer goldens；FF 仍 `--request-stdin --include-source-payload`。
  新增只在内部：worker 信封 `et-retrieval-worker/1`（request 文件 + 原子 result 文件，均不进公共 stdout）。
- 两正式入口：`transcript_tool.main`（生产路径 = 无 `_session_factory` 注入）与 `scraper.main`
  （`--list` metadata + fetch/discover/fetch-candidate 批次）全部经同一 supervisor；
  dry-run/disabled/缺 key/零额度零外发（disabled/缺 key 由 worker 内现有 gate 判定，零 HTTP）。
- 测试缝（私有、非 CLI/非环境变量）：`_retrieval_launcher`（child 内 import 的 fake session 工厂点路径）、
  `_retrieval_spec`（JSON 可序列化响应描述/calls 文件）、`_retrieval_temp_root`（临时根，便于断言清理）。
  生产调用方永远不传；不加 host 后门。
- `--max-seconds` 语义修正为**采集硬截止**（batch 自创建起共用同一 deadline，逐文件只给剩余额度；
  worker 在 deadline + 统一 1s 宽限内被回收）；显式 `--translate` 不在该限额内，help/README 如实声明。
- 用量未知（worker 被 kill / 坏退出 / 结果不可信）：usage 标未知、停止该批、报告不宣称 0 消耗、
  不恢复完整预算；deadline 原因 → `batch_deadline` partial(exit 3)，坏 worker →
  `provider_error/retrieval_worker_failure` 具名失败(exit 1) + 停批。

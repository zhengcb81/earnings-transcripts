# ET（earnings-transcripts）计划与状态入口

## 2026-11-21 · ET F4 普通入口、坏 TXT 与 worker 失败可复现

本仓按 [F4-ET-FAILURES 施工卡](docs/plans/feedback-loop-2026-11-17/F4-ET-FAILURES.md) 关闭总控运行号
`f4-coverage-audit-20261121` 的两条 ET 原发生（中央档案 `failure-coverage-20261121.jsonl` 第 10、11 行）。
交接在 [`docs/plans/feedback-loop-2026-11-17/F4-ET-FAILURES-handoff.md`](docs/plans/feedback-loop-2026-11-17/F4-ET-FAILURES-handoff.md)。

- **本仓新能力**：普通入口在参数解析与副作用之前建立本次身份并登记本次 recorder，于是
  `transcript_tool.py --operation bad`、`scraper.py --periods not-a-period`、`transcript_audit.py --root <缺失>`
  三类普通拒绝各发布一条 `invocation-refusal` 观察（明细带拒绝原因、发生阶段与本仓相对位置，无 argv）；
  本地原件审计按原报告的相对文件引用逐文件发布 `stored-original-audit` 观察，损坏 TXT 的解码损坏原因
  （`invalid_encoding`）可查，同批成功文件同样可查，电话会正文零进入反馈。
- **worker 失败语义**：supervisor 把子进程 stderr 收进自己的临时目录并合并脱敏诊断（退出码、失败阶段、
  异常类型链、子进程 pid、结果文件加载失败词），只进父进程写的那一条 finished 明细；原生用量收据形状与
  退出码不变，费用未知仍是未知。exit 7、超时、结果文件损坏三种同外层不同内因可冷读分辨。
- **落盘与交付**：记录先存本机 owner 持久状态根再确认；直启（未传 `RESEARCH_FEEDBACK_OUT`）向公开收件目录
  原子发布 `feedback-<32 位小写 hex>.jsonl` + 同名明细 + `.ready` 就绪标记；同身份并发生产者与补投在共享
  写锁下只登记一次原发生，补投不产生第二份 bundle；显式 OUT 保持 F4-ET 既有单次文件语义。
- **业务契约不变**：原生 CLI 的 usage 文本、stdout、退出码、收据与文件操作逐字节不变（`--capabilities` 仍零观察）；
  402 仍是来源权限事实、费用保持未知；漏发布用量的正控继续报 `transcript-usage-receipt` 偏差。
- **回归**：新增 `tests/test_f4_ordinary_failures.py`（16 项隔离子进程回归，RED 9 失败/5 通过 → GREEN 16 通过）；
  卡内规定回归 101 项通过；全量 760 项通过（基线 744 + 本卡 16）；改动文件 Ruff 全净。
- **独审**：第一轮结论 FAIL（1 MAJOR + 2 MINOR + 4 NOTE），全部修复后第二轮在 `44e2a271` 复核 **PASS**；
  关闭两项 MINOR 的 `01e3a772` 由本方执行验证收口（17 项回归、全量 761、Ruff 净、样例复现一致），
  审查员第三轮最终确认因 Token Plan 用量上限中断，待额度恢复补做（见交接"第三轮最终确认的状态"）。
- **交给 ROOT 的**：跨仓组合复验、公开收件目录的 `native_observation_dirs` 配置（本机默认收件目录绝对路径
  `C:\Users\郑曾波\.earnings-transcripts\feedback\observations`）、以及本卡两个原发生的关闭确认。

## 2026-11-20 ET F4 原始用量恢复

本仓增加 [F4 运行和交接](docs/plans/feedback-loop-2026-11-17/F4-ET.md)。从原冻结终态为已知用量补发公开披露，通过本仓事务收据保留原请求、尝试、复验和回告。缺原证据以及未知金额仍按原状态处理。下一步由 ROOT 接生产配置并作组合验收。

## 2026-11-18 · FB-ET 自反馈接入（ROOT 已验收）

FB-ET 施工卡（[卡](docs/plans/feedback-loop-2026-11-17/FB-ET.md)）在本仓的实施已完成，交接在
[`docs/plans/feedback-loop-2026-11-17/handoff.md`](docs/plans/feedback-loop-2026-11-17/handoff.md)。

- **本仓新能力**：`transcript_feedback.py` 把取数步骤投影为 `feedback-observation/1` 观察：
  请求可解析时先记动作开始（`action-started`），再记四项结果检查（业务状态词对已发布具名原因、
  请求期次对原生 provider 响应、内容引用对返回的原始字节、原生最终用量对本次真正发布的收据）；
  请求不可解析的路径（非法 JSON、超大请求）没有开始记录，只有四项结果检查。输出位置由宿主用
  `RESEARCH_FEEDBACK_OUT` 注入；观察与明细只写自己的 sidecar，从不进入 stdout/stderr，退出码、结果、
  收据与重试行为不变；sidecar 写失败只报「诊断不完整」，不改业务结果。父进程启动 worker 时会清除
  反馈环境量（卡片规定的单写者机制），父进程是 sidecar 的唯一写者。
- **本仓确认并修复的 ET 缺陷**：`--report-usage` 的原生最终用量收据原本在三处发布
  （preflight 拒绝路径、凭据错误路径、supervisor 结果），而另外三条同样零 provider 请求的终态路径
  （非法 JSON、超大请求、带 source payload 的 discovery）不发布，
  消费端因此无法区分「没有调用 provider」与「工具没来得及报告」。修复后公开 CLI 的每条取数终态路径
  都发布恰好一条收据（私有 `_session_factory` 测试缝不发布，公开入口不可达，见交接 N8 边界），
  结果 schema、退出码与收据形状不变。见
  [`issues/transcript-usage-receipt.md`](docs/plans/feedback-loop-2026-11-17/issues/transcript-usage-receipt.md)。
- **本仓交付物**：受控回放入口 `tools/replay_feedback_case.py`（只用具名 fixture，永不联网）、
  幂等问题卡导入 `tools/feedback_issue_cards.py`、修理技能
  [`skills/repair-earnings-transcripts-issue/SKILL.md`](skills/repair-earnings-transcripts-issue/SKILL.md)、
  离线回归 `tests/test_feedback_observation.py`、本目录的收据/样本/问题卡。
- **交给 ROOT 的**：跨仓复验与派工（ET 不能替消费端关单）；两张问题卡的消费端复验状态仍为 pending。
- **ROOT 复验**：737 项本仓测试及 10 份 golden 通过。历史红灯的根因是测试仍按旧策略
  阻断损坏的可选收据；现回归固定「诊断并保留」和「实际字节不符仍拒绝」两个对照。
  FF 的 ET 公开契约/传输 42 项离线通过；真实供应商与自动对账仍依总控计划。

> **本文件是 ET 仓唯一的 canonical 状态入口。**
> ET 的任何计划、进度、发现或交接都以本文件为准；`progress.md` 与 `findings.md` 只保留历史记录并链接回本文件，
> 不再是并列入口，也不另建 `START_HERE`。
>
> 状态快照：2026-11-14 · 仓库 `C:/Users/郑曾波/Projects/earnings-transcripts/earnings-transcripts` ·
> 分支 `main` · remote `origin` = `https://github.com/zhengcb81/earnings-transcripts.git`。
> - **本次收尾基线**：`915f216`（= 收尾前的 `origin/main`，也是最新一次主线收据提交）。
> - **本次收尾交付序列**：`bba1b97`（测试假凭据常量清理）→ `08da5dc`（根计划收敛 + 交接 + 清理，**round-1 独审 PASS 绑定的被审候选**）→ 其后均为 reviewer 要求的审查收据提交（`09a31d4`、`f76a168`、N-01…N-05 精度修正等）。
> - **最新 tip 的权威读法**：`git log --oneline -6` 或 `git ls-remote origin refs/heads/main`。
>   任何收据文件都无法包含承载它自己的提交的 SHA，因此本文件与交接 JSON 只记录**被审候选 SHA**（`08da5dc`）与**核对命令**，不记录会立刻过期的 tip 值；交接 JSON 的 `final.head` / `remote_head` 记录的是卡内「推送后确认远端 = 已审 HEAD」检查点的 `08da5dc`，其后的增量应只含审查收据文件（`git diff --stat 08da5dc..origin/main`）。
> - **最近一次收尾**：[`docs/plans/module-pwf-reconciliation-2026-11-14/review/et-closeout-handoff.md`](docs/plans/module-pwf-reconciliation-2026-11-14/review/et-closeout-handoff.md)（+ `.json` + `et-closeout-review.md`）。
> - **FB-ET 自反馈线**：开工基线 `6883623`；实现提交 `59fc207`（投影 + 接线 + 收据边界修复 + 具名回放 +
>   修理技能 + 离线回归），问题卡 `04f990f`，误开单修复 `df60513`；交接见本目录 `handoff.md`。

---

## 2026-11-14 仓库收尾对账补充

- PWF 更新前核对本地 `main` 与 `origin/main` 均为 `c777bf1ca82ba7cadf783dee4d0c7341dd48ec7b`；除 `main` 外的 15 个本地分支均为 `main` 祖先，当前只有 1 个注册工区，没有代码分支等待并线。
- 删除的仅是根目录及测试目录中的生成性 Python/test 缓存（`.pytest_cache`、`__pycache__`，以及空的 `.benchmarks`）：47 个文件、815,153 bytes。源码、原始 transcript、计划/审查收据及 Git 分支均未动。
- 主工区现有 8 个未跟踪文件仍保留：`.mimosa/` 下 4 个当前 hook session/status/lock 文件，以及 `.workbuddy-ai/memory/` 下 4 个用户/工具记忆文件。它们属于运行状态或记忆资料，不作为临时缓存删除。无 tracked/staged 代码改动。
- 本轮只做仓库状态/PWF与缓存对账，不改 ET 产品代码；因此保留唯一下一步 `transcript_audit.py --write-receipts` 收据冲突退出码缺陷。未运行产品测试，文档通过 diff 与 Git 状态检查后提交并核对远端。

## 当前状态摘要（2026-11-14）

| 维度 | 状态 |
|---|---|
| 电话会采集合同 | **已交付并冻结**。精确期次 + as-of 截止日、provider 发现/下载、未翻译原语言文本输出 |
| MB-ET（ET-01 协议与 provider 分离 / ET-02 worker 限额同源） | **已完工并入 `main`**（代码 tip `8e116a4`，交付分支 `codex/mb11-et` = `22050ed`）。**不是待办** |
| DU7 抽取 / DU9 legacy 比对 / DU10 TXT 保真 / ET-S3 deadline·runtime | **全部已完工并入 `main`**；PWF、收据、证据随主线保留在 `docs/plans/` |
| 本仓 worktree | 12 → **1**（只剩 `main` 工区）。11 个已结案交付工区按收据删除，Git 历史与分支未动 |
| 本仓非祖先本地 ref | **0**。除 `main` 外 15 个本地分支全部是 `main` 的祖先，保留为交付收据 |
| ET 自身未完成项 | **唯一下一步只有一条**，见下方；另有 2 条登记待办（真实 MF 页面 DOM 适应性验收、时序敏感用例稳健性）与 1 类本质不可消除的限制，见「ET 自身剩余项」 |
| FB-ET 自反馈接入（2026-11-17） | **ROOT 已验收**。观察投影 `transcript_feedback.py` + `transcript_tool.py` 接线；具名回放 `tools/replay_feedback_case.py`；幂等卡导入 `tools/feedback_issue_cards.py`；修理技能 `skills/repair-earnings-transcripts-issue/SKILL.md`；离线回归 `tests/test_feedback_observation.py`。本卡确认并修复了 `--report-usage` 在三条零请求终态路径不发布用量收据的 ET 缺陷。交接与余项见 `docs/plans/feedback-loop-2026-11-17/handoff.md` |
| FF / CWP 侧待办 | 写在「消费端 owner 边界」，**不计入 ET 未完成**，由各该仓 owner 自行推进 |
| 原件与用户资料 | 原样保留：`transcripts/`（130 个 tracked 原件/派生物）、`.workbuddy-ai/`（未跟踪，工具私有笔记）、gitignored 的 `config.json` / 缓存 / 日志 |
| 仓库预提交门禁 | **已可正常提交**。原先 19 个 high「硬编码凭据」全部是测试里的假凭据字面量（不是真实凭据），已提取为具名常量且**取值逐字节不变**；`mimosa audit .` 复测 high 0 |

---

## 唯一下一步（ET owner）

**修复 `transcript_audit.py --write-receipts` 在「收据冲突」时不返回非零退出码的既有缺陷。**

- **现状**：`tests/test_transcript_audit.py::test_write_receipts_never_overwrites_conflicting_receipt` 在 `main@915f216` 上失败
  （断言 `rc == 1`，实际 `rc == 0`）。该缺陷自基线 `003c1c0` 起可复现，MB-ET 卡已记录并明确未修
  （`docs/plans/mb11-et-2026-11-11/handoff.md:140` 与 `:146`）。复现命令见下方「测试与收据」。
- **性质**：纯 ET 内部缺陷，不涉及 FF/CWP 合同，不阻塞任何消费端，也不涉及原件或翻译。
- **验收**：RED 已由既有测试钉住 → 修复后 `pytest tests/test_transcript_audit.py` 全绿，且 `pytest tests/` 整包不再出现该失败。
- **完成后**：更新本文件「当前状态摘要」与「ET 自身剩余项」，并把新状态写回本文件顶部。

---

## ET 提供端：已冻结的电话会发现 / 下载 / 原语言文本合同

以下为 ET **已经交付并冻结**的公开面。消费端（FF、CWP）按此接线即可，不需要 ET 再改 wire。

### 公开 wire（`transcript_contracts.py`，stdlib-only，公开名稳定）

| 名称 | 值 | 说明 |
|---|---|---|
| 请求 | `earnings-transcript-request/1` | 严格字段：schema_version / request_id / ticker / exchange / fiscal_year / fiscal_quarter / as_of_date / provider / download_authorized / timeout_seconds / max_body_bytes（必需）+ max_response_bytes / max_cost_usd（可选） |
| 候选请求 | `earnings-transcript-candidate-fetch-request/1` | 额外严格绑定 candidate（`provider_document_id` / `source_url` / `published_date`） |
| 发现结果 | `earnings-transcript-discovery-result/1` | 只含候选元数据，不取正文 |
| 结果 | `earnings-transcript-result/1` | 默认；未翻译正文 + provider payload SHA-256 + canonical content SHA-256 |
| 原始交接 | `earnings-transcript-result/2` | opt-in（`--include-source-payload`）；受限 base64 原始响应 + 规范 MIME + 安全 effective URL；不重复返回正文 |
| 能力 | `earnings-provider-capabilities/1` | `python transcript_tool.py --capabilities`，无 HTTP、无凭证 |
| 用量 | `earnings-retrieval-usage/1` | `--report-usage` 时写 stderr，与 stdout 内容 JSON 分离 |
| 内部 worker | `et-retrieval-worker/1` | ET 内部协议，FF/CWP 不需要知道 |

### 行为合同

- **精确期次**：一次请求绑定 ticker + exchange + fiscal year/quarter + as-of 截止日。与 legacy `scraper.py --quarters N`（最近 N 期）**不是一回事**，不得混用。
- **两阶段（仅支持 discovery 的 provider，即 Motley Fool）**：`--operation discover` 只回候选元数据 → `--operation fetch-candidate` 绑定同一候选只取一次正文。
  一步式 `--operation fetch` 只是向后兼容路径，**不允许生产集成使用**（见 `README.md` 与本文件「开放 wire 与依赖」第 3 条）。
- **FMP 只有精确 `fetch`**：`--capabilities` 中 FMP 的 `discover` / `fetch-candidate` 为 `supported: false`。FMP 的 `date` 是**会议日**，不是已验证发表日；结果因此是 `call_date` + `publication_date: null` + `as_of_cutoff_verified: false`，下游不得当成发表时间。
- **网络意图唯一**：`download_authorized` 是唯一网络开关；旧 `--allow-download` 仍被接受但**不能**覆盖 `false` 的请求字段。
- **默认禁用**：Motley Fool 在 fetch / discover / fetch-candidate 三个入口默认禁用（`enabled: false`），任务意图不解除禁用、也不代表账户能力。
- **有界**：共享 deadline（1–60 秒）、listing 2 MiB、FMP 响应 16 MiB、canonical 正文 1 B–10 MiB、原始载荷编码上限 24 MiB；Motley Fool 最多 3 次同主机跳转，跨主机跳转与全部 FMP 跳转一律拒绝。
- **状态**：`fetched` / `not_found` / `ambiguous` / `unsupported` / `unavailable` / `not_authorized` / `rate_limited` / `provider_error` / `deadline_exceeded` / `content_too_large` / `provenance_rejected` / `invalid_request`。
  缺 key → `unavailable/provider_credentials_missing`；HTTP 402 → `unavailable/provider_entitlement_required`；HTTP 429 → `rate_limited/provider_http_429`。
- **原语言**：companion API 与 `transcript_tool.py` 合同上不翻译，输出即原始英文文本 / 原始响应字节；不初始化翻译服务，不写文件、日志、缓存、锁或配置。
  legacy 批处理另有显式翻译关闭开关 `--no-translate` / `--disable-translation`。
- **架构（MB-ET 已并入）**：纯合同/设置/协议/preflight 层与 provider 实现分离——`transcript_contracts.py`、`transcript_provider_settings.py`、`retrieval_protocol.py`、`transcript_preflight.py` 不加载 `requests` / `bs4` / 抽取器；`transcript_api` 保留全部旧公开名的兼容重导出（同一对象）。worker 结果限额由 `retrieval_protocol.result_transfer_limit` 单一公式派生，父进程读上限与子进程写上限同源。

---

## 消费端 owner 边界（ET / FF / CWP）

**ET 是 provider 侧 owner，只交付上面的 wire 与原语言文本。ET 不负责，也不应该替别人做下面两列。**

| 责任 | 归属 | 说明 |
|---|---|---|
| 调用 ET CLI、组装请求、决定是否发起网络意图、把结果转交下游 | **FF（filing-fetch）owner** | FF 已有 `scripts/transcript_companion.py`、`scripts/transcript_tool_transport.py`、`scripts/ff_v2_envelope.py` 等实现并已并入 `filing-fetch/main`；ET 不改 FF 代码、不代其集成 |
| 统一存储、canonical source 落库、provenance sidecar、幂等、原文保留、切片摘要与 source 定位 | **CWP（company-wiki）owner** | CWP 已有 `src/company_wiki/source_catalog/transcript_import.py` / `transcript_tool_contract.py` / `transcript_fmp_contract.py` / `canonical_writer.py`；切片摘要与 source-oriented locator 由 CWP 持有 |
| 精确期次发现/下载、原语言文本、provider 限额与状态、provider 抽取与保真 | **ET owner（本仓）** | 已交付并冻结，见上一节 |

### 开放 wire 与依赖（记录用；owner 是 FF / CWP，ET 不代做）

1. **FMP endpoint entitlement（HTTP 402）** — 账户/套餐事实，不是 ET 代码缺陷。ET 侧错误路径已实现并有测试；真实成功路径在 entitlement 未确认前无法证明，不得用合成 fixture 冒称。
2. **FMP 留存与再分发权利** — ET `task_plan.md` 旧文要求「持久化 FMP 文本前先确认账户条款允许内部留存与派生摘要」。当前 CWP 的 transcript importer **没有**权利门（其导入路径显式声明不需要 per-document 授权收据），FF 侧也没有对应实现。这条**归属 CWP/FF 的合规决策**，ET 只提供 provider 事实。
3. **一步式 `fetch` 的生产使用** — FF 现状用默认 `--operation fetch` + `provider=fmp`；FMP 不支持 discovery 阶段，所以这是 FMP 路径上的唯一可行调用形态。ET `README.md` 里「集成必须用 discover → fetch-candidate」这句需要限定到支持 discovery 的 provider（Motley Fool），否则与 ET 自己的 `--capabilities` 输出矛盾。**该表述修正是 ET 自己的文档债**，已列入下方剩余项。
4. **G1e 跨仓端到端验收** — ET/FF/CWP 三方曾约定用真实编排 + CWP importer/writer + ET 工具边界跑一次 G1e。截至各自仓的收据，该链路只做到分段 fake provider 级别。**归 CWP ROOT 组合验收**，不属于 ET 单仓待办。
5. **状态词表差异** — ET 的 12 个 provider 状态在 FF 侧被折叠为 `provider_unavailable` + 受限 `reason` 词表。ET 不改自己的状态词表；如需一一映射，由 FF owner 在其仓内决定。

---

## ET 自身剩余项（只列 ET owner 真正未完成的）

1. **【唯一 Next Step】`transcript_audit.py --write-receipts` 收据冲突退出码缺陷** — 见上方「唯一下一步」。
2. **`README.md` 一步式 `fetch` 表述限定** — **已于 2026-11-14 收尾完成**：把「集成必须用 discover → fetch-candidate」限定到支持 discovery 的 provider（Motley Fool），并写明 FMP 只有精确 `fetch`、没有 discovery 阶段。纯文档，无 wire 变更。
3. **真实 Motley Fool 页面 DOM 适应性验收（live DOM adaptability）** — DU7 / DU10 收据都诚实记录该项 `NOT_RUN`：现有抽取器只在合成夹具、真实 TXT 与合成标记层被验证过，从未在真实线上 DOM 上逐段独立试读。
   下一步前提：一次**无凭证、无付费**的公开 earnings-call 页面小样本授权（样本写入 ET 自有测试目录并记录 URL / SHA / 取样范围，验收后清理）。
   **不得**为凑真实样本改动生产 provider 开关或生产配置，也**不得**把真实 TXT 冒充网页 DOM 样本。
4. **已被记录、且本质不可由 ET 单独消除的限制**（不属于可执行待办，仅登记）：
   - 无标记残留的句中截断从字节不可判定，仍可能产出看似完整的正文（DU10 收据已写明）。
   - CJK 母语来源 transcript 本地不存在，中文覆盖只到夹具级 + 中企样本级。
   - 真实线上 MF HTML DOM 未下载重测；`README.md` 不得把「真实文本 + 合成标记」的往返说成线上 DOM 验收。
5. **时序敏感测试的稳健性** — 三个预算/截止日测试在慢主机或负载下会失败：
   `tests/test_retrieval_cli_e2e.py::test_two_document_batch_second_gets_only_remaining_budget`、
   `tests/test_explicit_response_budget.py::test_supervisor_result_bound_tracks_the_explicit_operation_budget`、
   `tests/test_retrieval_runtime.py::test_blocked_get_is_reaped_within_deadline_and_cleanup_grace`。
   2026-11-14 实测：在**有其他负载**的机器上，同一份代码连跑 5 次只有 1 次全绿（`1 failed, 2 passed` × 4、`3 passed` × 1）；
   同日整包运行在负载下是 `4 failed, 659 passed`，机器空闲时是 `1 failed, 662 passed`。
   独立 reviewer 在**空闲**主机上连跑 5 次**全部通过**，未能复现失败态 —— 即失败态是负载相关的，未被独立复现。
   这不是产品缺陷，也不是本次收尾引入（本次收尾只动了 Markdown 与 4 个测试文件的假凭据字面量提取，取值逐字节不变），
   但它会让整包回归在不同负载下给出不同结论，需在需要稳定门禁时单独立卡处理。

---

## 测试与收据

- 既有 ET 离线回归入口：`python -B -m pytest tests/ -q -p no:cacheprovider --basetemp <新建临时目录>`。
  本机 safe-delete 策略会拦 pytest 默认临时根的批量清理，因此必须给独立 `--basetemp`，用完删除。
- 离线门禁固定排除两项：`test_factory_auto_prefers_llm_when_keyed`（本机无可用 LLM 凭证）与 `test_google_translate`（可能对合成短句发起外部翻译请求）。
- **2026-11-14 收尾复测（`main@915f216` 基线 + 本轮测试常量清理）**：`1 failed, 662 passed in 196.73s`。
  - 唯一失败是确定性既有缺陷 `tests/test_transcript_audit.py::test_write_receipts_never_overwrites_conflicting_receipt`（即「唯一下一步」），基线 `003c1c0` 起可复现。
  - 同日早一次在负载下的整包运行是 `4 failed, 659 passed`，多出的 3 项为时序敏感的预算/截止日用例，见上方剩余项 5。
  - 本轮同时清除了仓库预提交门禁的 19 个 high「硬编码凭据」误报（全是测试里的假凭据字面量，已提取为具名常量，**取值逐字节不变**），门禁复测 high 0。
- 2026-11-14 完整收据见 `docs/plans/module-pwf-reconciliation-2026-11-14/review/et-closeout-handoff.md`。
- **2026-11-17 FB-ET 自反馈线复测**（基线 `6883623`，实现 `59fc207` + 误开单修复 `df60513`）：
  定向组合 `tests/test_feedback_observation.py tests/test_retrieval_cli_e2e.py
  tests/test_worker_result_budget_contract.py tests/test_optional_local_receipt.py` = **60 + 46 项全绿**
  （其中 `test_feedback_observation.py` 60 项含 38 项「已发布结果词不得报偏差」参数化守卫）；
  `tests/generate_transcript_goldens.py --check` = **10 goldens matched**；
  整包 `pytest tests/` = **1 failed, 682 passed**，唯一失败仍是基线既有的
  `tests/test_transcript_audit.py::test_write_receipts_never_overwrites_conflicting_receipt`
  （已在基线 `6883623` 复现，属原 ET 业务线，不在本卡写集合内）。
  反馈投影默认关闭，启用后 stdout 字节、stderr 字节与退出码与关闭时逐字节相同（回归已钉住）。

---

## 历史记录（只读，不是状态入口）

| 文件 | 性质 |
|---|---|
| [`progress.md`](progress.md) | 2026-07 起的会话进度流水，**历史记录**；当前状态以本文件为准 |
| [`findings.md`](findings.md) | 2026-07 起的发现流水，**历史记录**；当前状态以本文件为准 |
| [`docs/plans/mb11-et-2026-11-11/`](docs/plans/mb11-et-2026-11-11/) | MB-ET 交付 PWF + handoff + 独审 + 证据（2026-11-13 结案）。**收据 HEAD**：代码 tip `8e116a4` → 交付分支 `codex/mb11-et` = `22050ed` → 并入主线的收据 `ef7fb36` → `571b021` → `915f216` |
| [`docs/plans/du10-txt-fidelity-2026-10-17/`](docs/plans/du10-txt-fidelity-2026-10-17/) | DU10 TXT 保真交付 PWF + ROOT 接收。**收据 HEAD**：交付 `cb8e29c`（`codex/du10-et-txt-fidelity` tip），ROOT 接收见同目录 `ROOT_RECEPTION.md` |
| [`docs/plans/du9-legacy-comparison-2026-10-16/`](docs/plans/du9-legacy-comparison-2026-10-16/) | DU9 legacy 比对交付 PWF + ROOT 接收。**收据 HEAD**：交付 `6d94c38`（`codex/du9-legacy-comparison-20261016` tip） |
| [`docs/plans/du7-extraction-2026-10-16/`](docs/plans/du7-extraction-2026-10-16/) | DU7 抽取交付 PWF + ROOT 接收。**收据 HEAD**：作者交付 `511fe49` → ROOT 并线 `0cb7173`（`codex/root-du7-et-20261016` tip） |
| （ET-S3 deadline / runtime，无独立 plan 目录） | ET-S3 收据 HEAD：`63c4090`（deadline）、`53e1e60`（bounded runtime），二者均已并入 `main` |
| [`docs/plans/module-pwf-reconciliation-2026-11-14/`](docs/plans/module-pwf-reconciliation-2026-11-14/) | 2026-11-14 跨模块 PWF/工区收尾的 ET 交接与独审 |
| [`.planning/`](.planning/) | 早期 lane 级计划目录（`n5-et-local-text`、`s3-et-bounded-runtime-20261003`、`s3-et-deadline-20261004`、`w06-cutoff-result`）。**历史 lane 计划，不是状态入口**；其中的 `task_plan.md` 是各 lane 自己的执行计划，状态一律以本文件为准 |

### 已完成的历史阶段（不再列为待办）

- **Phase 0（P0 必修，2026-07-08 完成）**：README 与代码不一致（优先级文本 / 测试数量 / config.json 注释）、`translate.py --backend` 缺 `mimo`/`minimax`、`config.yaml` system_prompt 重复、`reader.py` 重复实现 `load_companies()` 与 header 解析、`TranslatorFactory` docstring 过期。
- **Phase 1（P1 结构，2026-07-08 完成）**：`common.py` 拆为 `config.py` / `naming.py` / `cache.py` / `parser.py` / `translator.py`（`common.py` 保留 re-export 门面）；HTML 模板抽到 `templates/index.html`；bilingual 构建去重；全局 logger 改模块级 `log`；测试改用 `mock_cfg` fixture。
- **Phase 2（P2 质量，2026-07-08 完成）**：清理死代码与未使用 import、补 `test_scraper.py`（5）与 `test_reader.py`（7）、翻译失败计数与摘要日志。
- **2026-09-27 → 2026-09-29 companion 期**：精确期次 provider API、stdin/JSON CLI、授权双闸、主机/跳转/字节/截止日边界、`/1` 与 `/2` 结果、显式状态、两阶段 candidate 授权、翻译关闭开关、单意图网络语义、`/2` 生产者 golden。全部已在主线。

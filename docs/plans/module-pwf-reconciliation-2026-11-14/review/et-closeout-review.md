# ET 收尾独立审查报告（Independent review of the ET closeout）

**审查者**：未参与实施的独立 reviewer agent（ZCode / MiniMax-M3.1-Flash 会话）
**卡**：`C:/Users/郑曾波/Projects/company-wiki/docs/plans/module-pwf-reconciliation-2026-11-14/owner_cards/et_closeout.md`（CWP 母仓，只读引用）
**唯一写入仓**：`C:/Users/郑曾波/Projects/earnings-transcripts/earnings-transcripts`
**绑定 HEAD**：`08da5dca4f680457775e8184730fa4499a8d3dd5`（`main`）
**基线 base**：`915f216f764e83012e8ce6f418111c9ef9eacace`（`main`，= `origin/main`）
**审查日期**：2026-11-14

---

## 1. Verdict / 结论

**PASS**（含 0 项 P0、0 项 P1、12 项 P2）。

候选 HEAD `08da5dc` 满足卡内完成条件：根 `task_plan.md` 已是 ET 唯一 canonical 状态入口；已完成的 MB-ET / DU7 / DU9 / DU10 / ET-S3 没有被写成待做；FF/CWP 待办被单列为 external-owner 依赖，没有被冒记为 ET 未完成；12 个注册 worktree、16 个本地分支、4 个远端 ref、8 个 Projects 级 ET 候选全部有 `ACTIVE_KEEP / EVIDENCE_KEEP / ABSORBED / UNCLASSIFIED_HOLD / DELETE_AFTER_OWNER_CONFIRMATION` 词表内的处置与真实依据；JSON schema 合规；本轮唯一的代码改动（4 个测试文件的假凭据字面量提取）经独立 AST 校验为逐字节等值；整包回归实测 `1 failed, 662 passed`，唯一失败即声明的唯一下一步，且该缺陷在基线 `003c1c0` 同样复现。

**没有 P0 / P1；没有阻断项。**

两项**推前必做**的收据级动作（不改变被审 HEAD 的结论，只补字段）：
1. 把 `et-closeout-handoff.json` 的 `final.head` / `final.remote_head` / `independent_review.*` 从 `PENDING_*` 回填为真实值（见 F-01）。
2. 修正根 `task_plan.md` 状态摘要表里「ET 自身未完成项 | 只剩一条」与本文「ET 自身剩余项」三节并列的内部不一致（见 F-02）。

两项都只改文档，不涉及任何产品代码、wire、行为或收据数字；按 handoff §10 第 4 步与本报告一并提交为**仅含审查收据**的提交即可，不需要重跑门禁与测试。

---

## 2. Scope reviewed / 审查范围

### 2.1 审了什么

| 对象 | 位置 |
|---|---|
| 被审提交（2 个） | `bba1b97`（test: 提取假凭据常量）、`08da5dc`（docs: 根计划收敛 + 工区收尾） |
| 交付物 | `task_plan.md`、`progress.md`、`findings.md`、`README.md`、`docs/plans/module-pwf-reconciliation-2026-11-14/review/et-closeout-handoff.md`、`.../et-closeout-handoff.json`、4 个测试文件改动 |
| 外部只读参照 | `C:/Users/郑曾波/Projects/filing-fetch`、`C:/Users/郑曾波/Projects/company-wiki`（只读，用于核对 FF/CWP 断言） |
| 卡 | `company-wiki/docs/plans/module-pwf-reconciliation-2026-11-14/owner_cards/et_closeout.md` |

### 2.2 实际执行的命令（全部只读；唯一写入是本报告文件与仓外 scratch 目录）

Git（`--no-optional-locks`，未 add/commit/checkout/remove/push）：
```
git --no-optional-locks log --oneline -8 ; status --porcelain=v1 ; branch -vv
git --no-optional-locks worktree list ; worktree list --porcelain
git --no-optional-locks branch --no-merged main            # 空
git --no-optional-locks for-each-ref refs/remotes/ ; for-each-ref (all refs)
git --no-optional-locks merge-base --is-ancestor <ref> main       # 15/15 全部 ancestor
git --no-optional-locks diff --diff-filter=D --name-only <branch> main   # 15/15 = 0
git --no-optional-locks diff --name-only <branch> main -- transcripts/  # 15/15 = 0
git --no-optional-locks diff --check 915f216..HEAD           # rc=0
git --no-optional-locks diff --stat 915f216 ; diff --name-status 915f216..HEAD
git --no-optional-locks show bba1b97 ; show --stat 08da5dc
git --no-optional-locks ls-files transcripts | docs/plans | .planning
git --no-optional-locks check-ignore -v config.json .cache.json .translate_cache.json
git --no-optional-locks ls-tree -r --name-only 915f216 -- docs/plans
git --no-optional-locks stash list                          # 空
```
文件系统：`ls`/`find`（11 个已删除工区路径、两个容器目录、ET 容器目录）；`du -sb`（transcripts、基线树体积）；`grep -rn`（FF/CWP/ET 运行配置对已删路径的引用）。

测试（全部用仓外 scratch 下的独立 `--basetemp`，`-B`、`-p no:cacheprovider`，运行后删除，仓内无测试输出）：
```
python -B -m pytest tests/ -q -p no:cacheprovider --basetemp <scratch>/tmp_full
python -B -m pytest tests/test_transcript_api.py tests/test_provider_cost_capability.py \
        tests/test_common.py tests/test_du7_extraction_wiring.py -q -p no:cacheprovider --basetemp <scratch>/tmp_a
python -B -m pytest tests/test_transcript_audit.py::test_write_receipts_never_overwrites_conflicting_receipt -q ...
python -B -m pytest <三个时序敏感用例> -q ...        # 连跑 5 次
python -B -m pytest <该用例> -q ...                   # 在 003c1c0 解包树里复跑
```
等值校验（自写脚本，**放在仓外** `C:/Users/郑曾波/Projects/earnings-transcripts/_closeout_scratch/`）：
AST 解析（解析模块常量 + 跨模块 import 常量）、全字符串字面量多重集比对、JSON schema 校验、markdown 相对链接与行内路径解析、config.json 凭据泄漏扫描。

门禁（按要求，未联网、未 `--update-rules`、未 `--with-glm`、未调用 `security-scan` MCP）：
```
node ".../mimosa/1.0.4/payload/dist/cli.js" audit . --json --no-deps      # 在 ET 仓内
node ".../mimosa/1.0.4/payload/dist/cli.js" audit . --json --no-deps      # 在 915f216 解包副本里（基线对照）
```
`git archive 915f216` / `git archive 003c1c0` 解包到仓外 scratch，只读复制，未触碰仓库。

---

## 3. Findings / 发现

严重度：P0 = 阻断交付；P1 = 必须在推送前修复；P2 = 文档/收据精度问题，不阻断。
状态：open = 尚未关闭；closed = 我已复核关闭；accepted-with-rationale = 接受但记录理由。

| # | 严重度 | 问题 | 证据（file:line / 命令输出） | 状态 |
|---|---|---|---|---|
| F-01 | P2 | `et-closeout-handoff.json` 在被审 HEAD 上仍是占位符：`final.head` / `final.remote_head` = `PENDING_CANDIDATE_HEAD`；`independent_review.reviewer_agent` / `reviewed_head` / `result` = `PENDING_INDEPENDENT_REVIEW` / `PENDING_CANDIDATE_HEAD` / `PENDING_INDEPENDENT_REVIEW`。卡要求 handoff 记录 base/final branch+HEAD、remote HEAD 与绑定 HEAD 的独立审查结论 | `docs/.../et-closeout-handoff.json:13-15, 445-447`；schema 校验输出 `final.head='PENDING_CANDIDATE_HEAD'`、`independent_review.result='PENDING_INDEPENDENT_REVIEW'` | **open（推前必做）**：按 handoff §10 第 4 步在审查收据提交中回填真实值；属纯收据回填，不改产品，故不需重审 |
| F-02 | P2 | 根 `task_plan.md` 状态摘要表写「ET 自身未完成项 \| **只剩一条**」，与本文「ET 自身剩余项」并列的三条**仍未关闭**的 ET 项矛盾（① `transcript_audit --write-receipts` 退出码缺陷；② 真实 Motley Fool DOM 适应性验收 `NOT_RUN`；③ 三个预算/截止日用例的时序稳健性）。JSON `remaining_items` 同样列了 3 条 open 的 ET 项。读者会误以为 ET 只剩一件事 | `task_plan.md:22`（表行）vs `task_plan.md:98,100,107-114`（剩余项 1/3/5）；`et-closeout-handoff.json:452-467` | **open（推前必做）**：把该表行改成与正文一致的表述（例如「仅剩一条唯一下一步，另有 2 条登记待办 + 1 项不可消除限制」）。属一行文档修正 |
| F-03 | P2 | handoff §1 表格「final 分支 / HEAD」写「见 §7「提交与推送顺序」」，但「提交与推送顺序」是 **§10**，§7 是「测试与检查」 | `docs/.../et-closeout-handoff.md:21` | open（文档交叉引用错误，推前一行修正） |
| F-04 | P2 | handoff §7 第 1 行与 JSON `tests[0]` 描述 `git diff --stat 915f216` 的结果为「4 个 Markdown + 4 个测试文件」。实际输出是 **10 个文件**：4 个根 Markdown + handoff 的 md 与 json + 4 个测试文件。**实质结论「生产模块一行未动」经我独立核对为真** | `et-closeout-handoff.md:212`；`et-closeout-handoff.json:396-397`；实测 `git diff --stat 915f216` → 10 files changed, 1000 insertions(+), 165 deletions(-) | open（收据计数不准，实质结论成立） |
| F-05 | P2 | JSON `dirty_exceptions[2]` 把 `.ruff_cache/` 与 `.benchmarks/` 列为 ET 仓的「gitignored working-tree state」；二者都不在 ET 仓 `.gitignore`（共 9 条：`.cache.json`, `.translate_cache.json`, `__pycache__/`, `*.pyc`, `logs/`, `config.json`, `.env`, `*.egg-info/`, `.pytest_cache/`）。`.ruff_cache/` 根本不在 ET 仓根（它在上一级容器 `Projects/earnings-transcripts/.ruff_cache`），`.benchmarks/` 存在但为空、git 因此不报。同一字段的证据串「.gitignore lines 1-2, 5, 7」把 `config.json`（实为 **第 6 行**）引成第 7 行（第 7 行是 `.env`） | `et-closeout-handoff.json:362-366`；`git check-ignore -v .benchmarks` → rc=1（未忽略）；`.gitignore` 全文 | open（证据引用精度） |
| F-06 | P2 | JSON `_harness_worktrees` 容器的证据串列出 `cmrf-20261008 still holds ff-diagnostics, ff-permission-residue, rf-inputs, rf-permission-residue, sw-permission-residue`；今日实测 `cmrf-20261008` 只剩 `ff-diagnostics, ff-permission-residue, sw-permission-residue`（两个 rf 工区已被**别的 lane** 事后移除）。结论（其他 lane 的工区，ET 不动）仍成立 | `et-closeout-handoff.json:306-311`；实测 `ls Projects/_harness_worktrees/cmrf-20261008` | closed（他人 lane 后续动作所致，非 ET 所为；处置 `UNCLASSIFIED_HOLD` 不受影响） |
| F-07 | P2 | JSON `remaining_items` 用「FF scripts/transcript_tool_transport.py closed error vocabulary at lines 47-60」支撑「ET 的 12 个状态在 FF 侧折叠为 `provider_unavailable` + 受限 reason 词表」。该文件的 47-60 行其实是 `_KNOWN_ET_ERRORS` 白名单；真正的折叠映射在 `filing-fetch/scripts/et_v2_contract.py:39-49` | `et-closeout-handoff.json:497`；`filing-fetch/scripts/transcript_tool_transport.py:47-60`；`filing-fetch/scripts/et_v2_contract.py:39-49`（`("unavailable","provider_credentials_missing") → "provider_unavailable"` 等 10 条） | open（**断言本身我已核实为真**，仅引用指错构造） |
| F-08 | P2 | handoff §7 第 1b 行 / JSON `tests[1]` 报告 AST 等值校验的计数为 20 / 3 / 7 / 1。我自写的 AST 枚举（解析模块常量 + 跨模块 import）得到的使用点计数是 18 / 3 / 1 / 1（外加 4 个新增常量声明位）。按「逐用例展开」口径作者的数字可自洽（`test_transcript_api.py` 另有 2 处 `fmp_api_key=None` → 18+2=20；`test_common.py` 的 4 个参数化用例展开键共 6 + 1 = 7），但口径未写明 | `et-closeout-handoff.md:213`；`et-closeout-handoff.json:400-401`；我的枚举脚本输出 | accepted-with-rationale：**实质结论（取值逐字节不变）已被更强的检查证实**——全字符串字面量多重集比对显示 4 个文件中没有任何一个字面量值被新增或丢失，只有重复内联字面量被去重到具名常量（见 F-11）。仅口径需注明 |
| F-09 | P2 | 新 canonical 入口把 `progress.md` / `findings.md` / `docs/plans/*` 标注为历史，但**没有提到仓内被 track 的 `.planning/` 计划目录**（12 个 tracked 文件，其中 4 个是 `.planning/<lane>/task_plan.md`：`n5-et-local-text`、`s3-et-bounded-runtime-20261003`、`s3-et-deadline-20261004`、`w06-cutoff-result`）。读者无法判断这些 lane 级 `task_plan.md` 是否是与根入口并列的状态入口 | `git ls-files .planning` → 12 条，含 4 个 `task_plan.md`；`task_plan.md:131-141`（历史记录表未列 `.planning/`） | open（建议补一行说明其为历史 lane 计划） |
| F-10 | P2 | 「远端 ref（4 个）」只统计了 4 个远端分支；`refs/remotes/origin/HEAD`（符号引用，指向 `origin/main`）也是一个 remote-tracking ref。未被单列 disposition，但它的目标 `origin/main` 已 `ACTIVE_KEEP`，无实际遗漏 | `git for-each-ref refs/remotes/` → 5 条（`origin/HEAD` + 4 分支）；`et-closeout-handoff.md:139` | accepted-with-rationale（符号引用不是独立远端分支，不构成处置遗漏） |
| F-11 | P2 | `company-wiki` 工作树**有 3 个被 track 的文件处于 modified 状态**（`docs/plans/module-pwf-reconciliation-2026-11-14/review/cwp-closeout-handoff.{json,md}` 与 `cwp-closeout-review.md`），并非「只有预先存在的 untracked 项」。经核对：这些是 **CWP lane 自己的收尾文件**，其 diff 中 ET 相关字样命中数为 **0**，且 company-wiki 今天已有 3 个 CWP 收尾提交（`7361aad82` / `ca2602a2f` / `8ae20ce6f`）。ET lane 未向 company-wiki 写入任何内容 | `cd company-wiki && git status --porcelain -uall` → 3 个 ` M`；`git diff --stat` → 3 files changed, 146 insertions(+), 13 deletions(-)；`git diff | grep -ciE 'earnings-transcripts\|ET owner'` → **0**；`git log --format='%h %ad %s' --date=iso -3` | closed（属并行 CWP lane，非 ET 收尾产物；ET 跨仓边界未被破坏） |
| F-12 | P2 | handoff §7 第 1b 行把「取值逐字节不变」和「AST 等值校验 PASS」绑定；而 `bba1b97` 的提交说明写的是「verified by an AST value-equivalence check over the four files」，但**该校验脚本没有随仓交付**（仓内不存在该脚本），外部 reviewer 只能自行重写 | `bba1b97` commit message；`git ls-files | grep -iE 'equiv|ast'` → 无 | accepted-with-rationale：卡不要求交付校验脚本；我已用独立脚本重做并确认结论为真（见 §4.1） |

### 3.1 明确「无发现」的检查项

以下检查项我逐条执行后**未发现任何问题**，逐项列明以免被当作默认通过：

| 检查 | 结果 |
|---|---|
| canonical 唯一性 | 根 `task_plan.md` 为唯一状态入口；`progress.md` / `findings.md` 顶部各有「本文件是历史流水，不是状态入口」并回链 `task_plan.md`（`progress.md:3-6`、`findings.md:3-6`）；**未新建 START_HERE**（`git ls-files \| grep -i start_here` → 无；仓内 `find -iname '*start*here*'` → 无） |
| 不新建第二个 canonical PWF | ET 仓 `docs/plans/module-pwf-reconciliation-2026-11-14/` 下只有 `review/et-closeout-handoff.{md,json}`，无 `task_plan.md` |
| 已完成工作未被写成待做 | 根计划把 MB-ET / DU7 / DU9 / DU10 / ET-S3 全部标为「已完工并入 main」；旧版 `task_plan.md` 的 `Phase 3 … status: in_progress` 与 2026-09-26 RF 活动卡行已整体删除（`git show 915f216:task_plan.md` 第 76、127 行 → 新文件无对应内容） |
| FF/CWP 待办未被冒记为 ET 未完成 | 根计划「消费端 owner 边界」与「开放 wire 与依赖」独立成节；JSON `remaining_items` 用 `owner` 字段区分（ET 6 条 / FF 2 条 / CWP-FF 1 条 / CWP ROOT 1 条，其中 3 条 open 的 ET 项与外部项分列） |
| ET 剩余项与外部依赖均真实 | ET 4 项 open/登记项分别指向 `docs/plans/mb11-et-2026-11-11/handoff.md:140` 与 `:146`、`docs/plans/du7-extraction-2026-10-16/handoff.md` 的 NOT_RUN 段、`docs/plans/du10-txt-fidelity-2026-10-17/handoff.md` 的限制段；外部 4 项分别指向 FF `docs/plans/fresh-transcript-launch-2026-10-09/green_summary.json:50`、CWP `src/company_wiki/source_catalog/transcript_import.py:107-113` 与 `tests/contract/test_transcript_import_cli_e2e.py:238-240`、CWP `docs/plans/narrative-evidence-pilot-2026-09-26/end_to_end_test_plan.md:86` 与 `.../cross_repo_mainline_and_delivery_plan_2026-09-28.md:179`、FF `scripts/transcript_tool_transport.py:47-60`——**这些行号与内容我逐条打开核对，全部命中** |
| disposition 词表 | JSON 47 条 disposition，`status` 取值仅 5 种，全部落在规定词表内（`DELETE_AFTER_OWNER_CONFIRMATION` 18、`ABSORBED` 15、`ACTIVE_KEEP` 6、`EVIDENCE_KEEP` 6、`UNCLASSIFIED_HOLD` 2） |
| 处置覆盖率 | `worktree` 12 条（= 原 12 个注册 worktree）、`branch` 16 条（`main` + 15）、`remote_ref` 4 条、`projects_dir` 8 条（容器 + 7 个带任务名目录）、另加 `container_dir` 2、`repo_path` 5；每条都有 `kind/path/status/reason/evidence` 五字段，无缺项 |
| JSON schema | 裸 JSON（无 ``` 围栏）、`json.load` 解析通过；`schema_version == "1"`；顶层键**恰好**为卡定 13 个（顺序一致，无缺无多）；`base` 含 `branch/head/remote_head` 且无多余键；`final` 含 `branch/head/remote_head`（多一个 `note`，卡对该层只要求「含」）；`cleanup` 含 `paths`(11)/`file_count`(2183)/`bytes`(126336536)；12 条 `tests` 每条都有 `command/result`；`independent_review` 有 `reviewer_agent/reviewed_head/result/report_path` 四键 |
| 相对链接 | `task_plan.md` 7 条相对链接全部可解析；`progress.md` 1 条、`findings.md` 1 条、`README.md` 1 条全部可解析；handoff 内引用的仓内路径（`docs/plans/mb11-et-2026-11-11/handoff.md` 等）均存在（跨仓路径 `scripts/transcript_companion.py`、`src/company_wiki/source_catalog/transcript_import.py` 属 FF/CWP，已在 FF/CWP 仓内实测存在） |
| `git diff --check` | `git diff --check 915f216..HEAD` → rc=0（无空白错误）；工作树同检亦 rc=0 |
| 无生产代码改动 | `git diff --name-status 915f216..HEAD` 只有 10 个文件：4 个根 Markdown + 2 个交接文件 + 4 个测试文件；`transcript_api.py` / `transcript_preflight.py` / `transcript_tool.py` / `retrieval_*.py` / `scraper.py` / `transcript_extraction.py` 一行未动；`bba1b97` 的改动文件全部在 `tests/` 下 |
| 假凭据取值等值 | 独立 AST 校验：4 个文件的凭据使用点取值**逐字节相同**；全字符串字面量多重集比对显示**没有任何新字面量被引入、没有任何字面量丢失**（详见 §4.1） |
| worktree 收尾 | `git worktree list` 只剩主工区（`08da5dc [main]`）；11 个已删工区路径实测**全部不存在**；两个共享容器保留（`cwp-lanes-20261006` 现为空目录，与 JSON 证据一致） |
| 0 非祖先本地 ref | `git branch --no-merged main` → 输出为空；更强检查：`for-each-ref` 全 21 个 ref（本地 16 + 远端 5）**全部**是 `main` 的祖先；无 stash、无 tag |
| 分支内容已被主线吸收 | 15 个分支逐一 `git diff --diff-filter=D --name-only <branch> main` → 全部 **0**（没有任何分支独有文件） |
| transcripts 一致 | 15 个分支逐一 `git diff --name-only <branch> main -- transcripts/` → 全部 **0**；`git ls-files transcripts` = **130** |
| 保留项存在 | `transcripts/` 130 个 tracked（43 个 `*_earnings_call.txt`）；`.workbuddy-ai/memory/` 4 个 untracked（`2026-09-03.md`, `2026-11-02.md`, `2026-11-12.md`, `MEMORY.md`）；`docs/plans` 下 `receipts/` 31、`evidence/` 9、`review/` 4（base 时 `docs/plans` 共 84 个 tracked，与 JSON 证据一致；本次新增 2 个交接文件后为 86） |
| 无运行配置指向已删工区 | `filing-fetch/config/**`、`filing-fetch/scripts/**`、`company-wiki/config/**`、`company-wiki/src/**` 以及 ET 仓全部 `*.py/*.json/*.yaml/*.toml`（排除 `docs/`）对 11 个工区名 / `_harness_worktrees` / `cwp-lanes-20261006` 的 grep → **0 命中**；`filing-fetch/config/company_wiki.json` 的 `earnings_transcripts_tool` 仍指向主线仓 `…/earnings-transcripts/earnings-transcripts/transcript_tool.py`（实测存在，12 422 字节） |
| 跨仓边界 | `filing-fetch`：**0 个 tracked 修改**（仅 `.ff_jobs/`、`.mimosa/` 等 untracked 运行态）；`company-wiki`：仅 3 个 CWP lane 自身收据文件 modified，diff 中 ET 字样 0 命中（见 F-11）。ET 仓内无 FF/CWP/RF 代码改动 |
| 凭据未泄漏 | `config.json` 为 gitignored（`.gitignore:6`）、未被 track，内含 3 个 api-key 项（仅键名 `mimo_api_key` / `minimax_api_key` / `deepseek_api_key`，值未打印）；对其值做全量扫描：**未出现在** handoff.md / handoff.json / task_plan.md / README.md，也**未出现在任何 tracked 文件**中 |
| 门禁 | ET 仓内 `mimosa audit . --json --no-deps` → **high 0 / medium 0 / low 8 / info 0**；8 个 low 全部为既有项：`eval_translation.py:60,61,62,67`（`random.sample` ×3、`random.choice` ×1）、`tests/test_du9_legacy_comparison.py:510,517,866`（`random.Random` ×3）、`retrieval_runtime.py:3`（一条模块 docstring 被判 `subprocess` sink）——与 handoff 描述一致 |
| 门禁基线 | 在 `915f216` 的解包副本内同款审计 → **high 19 / low 8**，19 个 high 全部是 `mimosa.hardcoded-secret`，全部落在 4 个测试文件（`test_transcript_api.py` 15、`test_provider_cost_capability.py` 2、`test_common.py` 1、`test_du7_extraction_wiring.py` 1），取值全为 `fake-key` / `fake-secret-for-test` / `fake-cutoff-key` / `synthetic-not-real-*`。**「19 → 0」被独立复现** |

---

## 4. 关键复核细节（执行级证据）

### 4.1 `bba1b97` 的取值等值（独立重做，未采信交接自述）

交接 §7 第 1b 行声称做过 AST 等值校验但未随仓交付脚本；我自己重写了两套检查（脚本置于仓外 `…/_closeout_scratch/`，未写入仓库）：

**(a) 凭据使用点解析比对**（解析模块级常量 + 跨模块 import 的常量）：

| 文件 | base 使用点 | head 使用点 | 结论 |
|---|---|---|---|
| `tests/test_transcript_api.py` | 18 | 18（+3 个新常量声明位） | 每个使用点取值完全相同；新增的只是 `FAKE_FMP_KEY_VALUE="fake-key"` / `FAKE_FMP_SECRET_VALUE="fake-secret-for-test"` / `FAKE_FMP_CUTOFF_KEY_VALUE="fake-cutoff-key"` 三个声明位 |
| `tests/test_provider_cost_capability.py` | 3 | 3 | 全部相同（从 `test_transcript_api` import 常量后取值不变） |
| `tests/test_common.py` | 1（字典推导 key `name + "_api_key"`） | 1（`name + CONFIG_KEY_SUFFIX`，`CONFIG_KEY_SUFFIX == "_api_key"`） | 运行时表达式逐字等价 |
| `tests/test_du7_extraction_wiring.py` | 1 | 1 | 相同 |

**(b) 全字符串字面量多重集比对（更强）**：`test_common.py` 372 → 372（无增无减）；`test_transcript_api.py` 474 → 462（净减 12，全部是重复内联字面量被去重到 3 个常量声明位，**没有任何新字面量值出现**）；`test_provider_cost_capability.py` 92 → 89；`test_du7_extraction_wiring.py` 75 → 74。三处字面量计数变化恰好等于 18 个使用点内联值的去重，**取值逐字节未变，语义未变**。受影响测试实测 `119 passed`（交接称 119 passed，一致）。

另注：`tests/test_transcript_api.py:507` 的 `fmp_api_key="fake-secret"`（与 `fake-secret-for-test` 不同的另一个字面量）**未被 mimosa 命中**，改动前后都在原地，未被本轮触碰。

### 4.2 测试收据（实跑，非引用）

| 项 | 交接声明 | 我的实测 | 差异说明 |
|---|---|---|---|
| 整包 `pytest tests/` | `1 failed, 662 passed in 196.73s` | **`1 failed, 662 passed in 175.15s`** | 计数与失败用例完全一致，仅耗时不同（主机负载差异） |
| 唯一失败 | `tests/test_transcript_audit.py::test_write_receipts_never_overwrites_conflicting_receipt` | **同一条**，`tests/test_transcript_audit.py:199  assert 0 == 1`（期望 `rc == 1`，实得 `rc == 0`） | 一致 |
| 缺陷是否本轮引入 | 基线 `003c1c0` 起可复现 | 在 `git archive 003c1c0` 解包出的**未修改**基线树里单独复跑 → **同样 1 failed**；且 `git diff --stat 003c1c0..HEAD -- tests/test_transcript_audit.py transcript_audit.py` 为空（两文件自基线起逐字节未变） | 一致，确认为既有缺陷 |
| 受影响 4 文件 | `119 passed in 7.33s` | **`119 passed in 2.42s`** | 计数一致 |
| 3 个预算/截止日用例的负载敏感性 | 连跑 5 次：1 次全绿 / 4 次 `1 failed, 2 passed` | 空闲主机上连跑 5 次：**5/5 全绿（3 passed，11.2 s/次）** | 我**未能复现**该 flaky（空闲主机不触发），但该现象与「主机负载敏感」的说法方向一致，且整包运行在本次也未出现这 3 项失败。**此项我无法独立确认其可复现性**，见 §5 |

### 4.3 FF / CWP 断言的诚实性核对（只读打开源仓核对，不写入）

| 交接断言 | 我的核对 | 结论 |
|---|---|---|
| FF 已有 `scripts/transcript_companion.py` / `transcript_tool_transport.py` / `ff_v2_envelope.py` 并已并入 `filing-fetch/main` | 三文件存在，且 `git ls-tree main` 均在 | **准确** |
| FF 现状用默认 `--operation fetch` + `provider=fmp` | `transcript_tool_transport.py:705,841` 构造 `"provider": "fmp"`，调用 CLI 只带 `--request-stdin`，**不带** `--operation` → 走 ET 默认 `fetch` | **准确** |
| CWP 已有 `transcript_import.py` / `transcript_tool_contract.py` / `transcript_fmp_contract.py` / `canonical_writer.py` | 四个文件均在 `src/company_wiki/source_catalog/` 下 | **准确** |
| CWP importer 显式声明不需要 per-document 授权收据 | `transcript_import.py:107-113` docstring 原文：「This importer needs no per-document authorization receipt or provider policy file」；`tests/contract/test_transcript_import_cli_e2e.py:238-240` 断言 `rights_policy_sha256` / `download_authorization_hash` 不存在、`provider_use_policy.json` 不生成 | **准确（行号与引文均对得上）** |
| FF 侧无对应实现 | `grep -riE 'retention|redistribut|rights_policy' filing-fetch/{scripts,src,config}` → **0 命中** | **准确** |
| FMP entitlement 402 仍开放 | ET 侧 402 → `provider_entitlement_required`（`transcript_api.py:377-378`）并有 3 处测试 + 1 个 golden；FF `green_summary.json:50` `real_authentication: "NOT_RUN_MAIN_M3"`；CWP 计划书「FMP 当前 key 的 transcript 返回 402」 | **准确** |
| G1e 仍只有分段 fake 级别 | `end_to_end_test_plan.md:86`「Transcript acquisition E2E 尚未实现/执行」；`cross_repo_mainline_and_delivery_plan_2026-09-28.md:179`「ET/FF companion 仅局部 fake 测过：G-A 必须经过真实编排…」 | **准确（行号命中）** |
| ET 12 个状态在 FF 侧折叠为 `provider_unavailable` + 受限 reason 词表 | 折叠映射在 `filing-fetch/scripts/et_v2_contract.py:39-49`（10 条 status+error_code → provider_unavailable / provenance_rejected / request_error）；`transcript_tool_transport.py:47-60` 是「已知 ET 错误码白名单」，是相邻但不同的构造 | **断言为真，引用指错（F-07）** |
| README 的 FMP 表述修正与 ET 自身能力一致 | `python transcript_tool.py --capabilities`：FMP `discover`/`fetch-candidate` 均 `supported: false`、`fetch` `supported: true`；Motley Fool `discover`/`fetch-candidate` `supported: true` 但 `enabled: false` | **准确**（README 新句与 `--capabilities` 输出一致） |
| ET wire 名称与限额 | `earnings-transcript-request/1`、`earnings-transcript-candidate-fetch-request/1`、`earnings-transcript-discovery-result/1`、`earnings-transcript-result/1`、`earnings-transcript-result/2`、`earnings-provider-capabilities/1`、`earnings-retrieval-usage/1`、内部 `et-retrieval-worker/1` 均在代码/测试中出现；`transcript_contracts.py:21-24` = 10 MiB / 2 MiB / 16 MiB / 24 MiB | **准确** |

**未发现任何夸大、缩小或错误的 FF/CWP 断言。** 唯一偏差是 F-07 的引用行号。

---

## 5. Checks that could not be verified / 未能验证的检查

1. **清理收据的 `file_count = 2183` 与 `bytes = 126336536`**：这两个数字是在**删除之前**测量的，目录已不存在，**我无法重新测量**。
   - 我能做的替代核验（已完成）：
     - **内部自洽**：交接 §6 的 11 行逐目录表求和 = **2 183 文件 / 126 336 536 字节**，与总计**逐位相符**；
     - **与仓库实测体积相符**：`git archive 915f216` 解包出的**仅 tracked 文件**树为 **17 257 659 字节 / 343 文件**；工作树（排除 `.git`）为 **18 462 227 字节**；`transcripts/` 为 **15 953 731 字节 / 130 文件**。收据里 7 个「大」工区各记 16 608 109 – 18 687 601 字节 / 212 – 362 文件，落在一次完整 checkout + 可重建缓存的量级内；另 4 个「小」工区（59 / 114 / 142 / 156 文件，0 566 697 – 2 113 762 字节）是部分 checkout，量级自洽；
     - **结论**：数字内部一致且与仓库实测体积相容，**但不可复测**；这是一个**验证方法的固有限制**，不是作者的错误。
2. **11 个被删工区删除前的 `git status --porcelain -uall` 为空、仅有可重建缓存**：目录已删，无法复测。我只能确认**当前**主工区同样干净（仅 `.mimosa/`、`.workbuddy-ai/` 两个 untracked 目录），且 15 个分支相对 `main` 均无独有文件——这与「删除前各工区 clean」的结论方向一致，但不等价。
3. **`earnings-transcripts-du6-ff-readonly` 的 detached HEAD `d0ac42c`**：工区已删，无法复测其 detached 状态。**旁证**：仓内没有任何 `du6` 分支（分支清单 15 个中无 `du6`），而 `codex/root-w06-et-cutoff-20261011` 的 tip 正是 `d0ac42c`——与「detached、移除不删任何 ref」一致。
4. **3 个预算/截止日用例的负载敏感性 flaky**：我在**空闲**主机上连跑 5 次全部通过（5×3 passed），**未能复现**作者的失败态。该断言只能判定为「与观察方向一致、无法独立复现」，不作为 PASS 依据。它们的**存在性**已确认（3 个 node id 均能 collect）。
5. **`mimosa` 门禁在真实 pre-commit hook 上的端到端行为**（含 `--no-ignore-comments` 抑制注释不可用这一点）：我只跑了 `audit . --json --no-deps`（按任务指定的命令），未触发实际 hook 提交流程，也未运行 `--update-rules` / `--with-glm` / `security-scan`。交接 §1 第 6 条关于「抑制注释在门禁 audit 中不生效」的说明属工具行为推断，本次未复现。
6. **`company-wiki` 与 `filing-fetch` 在本次收尾窗口内是否曾被 ET lane 写入**：我只能核对**当前**状态（见 F-11 与 §3.1「跨仓边界」行）。ET 仓内的改动集不含任何 FF/CWP/RF 产物，且交接声明与其提交内容一致；但严格意义上无法从 Git 事后证明「另一个仓的某一时刻的文件写入一定不是本 lane 做的」。F-11 提供了强证据（改动文件属于 CWP lane、diff 无 ET 内容、company-wiki 有独立的 CWP 提交序列）。

---

## 6. Final verdict / 最终结论

**PASS — 绑定 HEAD `08da5dca4f680457775e8184730fa4499a8d3dd5`（`main`）。**

复述：候选 HEAD 在卡内全部完成条件上成立——canonical 入口唯一且内容正确、已完成工作未冒充待做、外部依赖与 ET 自身剩余工作分离且每条有出处、12 worktree / 16 分支 / 4 远端 ref / 8 Projects 候选全部有词表内处置与真实依据、JSON schema 合规且为裸 JSON、相对链接可解析、`git diff --check` 干净、唯一的代码改动为取值逐字节不变的测试常量提取、整包回归实测 `1 failed / 662 passed` 且唯一失败即已声明的唯一下一步并可在基线 `003c1c0` 复现、门禁 `high 19 → 0`（`low 8` 全为既有项）被独立复现、无真实凭据进入任何交付物或版本库、无 FF/CWP/RF 越界写入。

**发现计数：P0 = 0，P1 = 0，P2 = 12**（F-01…F-12；其中 F-01 与 F-02 建议在推送前一并修正，均为纯文档/收据改动，不触及产品代码与任何收据数字，无需重跑门禁与测试；其余为引用精度与陈述精度问题，标注为 open / accepted-with-rationale）。

本报告仅写入 `docs/plans/module-pwf-reconciliation-2026-11-14/review/et-closeout-review.md` 一个文件；未在 ET 仓做任何 add / commit / checkout / restore / merge / push / worktree remove；未在 `filing-fetch`、`company-wiki` 或任何其他仓写入、暂存或提交任何内容；所有临时脚本与 `--basetemp` 均置于仓外 `C:/Users/郑曾波/Projects/earnings-transcripts/_closeout_scratch/`，并已在审查结束后删除。

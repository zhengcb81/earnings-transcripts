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

---
---

# 7. 第二轮独立复核（RE-CHECK）— 绑定 HEAD `f76a1682aa488d71e6f998f12c59eb24651432b3`

**复核者**：未参与实施的独立 reviewer agent（ZCode / MiniMax-M3.1-Flash 会话），与第一轮审查不是同一次执行
**绑定 HEAD**：`f76a1682aa488d71e6f998f12c59eb24651432b3`（`main`）
**基线 base**：`915f216f764e83012e8ce6f418111c9ef9eacace`
**第一轮绑定 HEAD**：`08da5dca4f680457775e8184730fa4499a8d3dd5`
**复核日期**：2026-11-14
**唯一写入仓**：`C:/Users/郑曾波/Projects/earnings-transcripts/earnings-transcripts`（本报告文件除外，未写入任何其他文件）

## 7.1 复核范围

复核对象是作者在第一轮审查之后的三个提交：

| 提交 | 说明 | 本轮判定 |
|---|---|---|
| `bba1b97` | test(et): 提取假凭据字面量为具名常量 | 未被触碰（`08da5dc..f76a168` 中无 `.py` 改动） |
| `08da5dc` | docs(et): 根计划收敛 + 工区收尾 | 第一轮已审；本轮作对照基线 |
| `09a31d4` | docs(et): 记录独立审查收据并关闭 F-01…F-11 | 本轮逐条复核 |
| `f76a168` | docs(et): 刷新根计划快照与收据 HEAD | 本轮逐条复核 |

本轮做了四件事：(1) 对 F-01…F-12 **逐条执行式复核**是否真的关闭；(2) 「别的东西有没有被动过」检查；(3) 两个后续缺口的诚实性判断；(4) 独立抽查（不采信作者自述）。

### 7.1.1 实际执行的命令（全部只读；唯一写入是本报告文件与仓外 scratch 目录）

Git（`--no-optional-locks`，未 add/commit/checkout/remove/push/restore/worktree-remove）：
```
git --no-optional-locks log --oneline -10 ; status --porcelain=v1 -uall ; branch -vv ; rev-parse HEAD
git --no-optional-locks diff --name-only 08da5dc..f76a168 ; diff --numstat 08da5dc..f76a168
git --no-optional-locks diff --check 08da5dc..f76a168      # rc=0
git --no-optional-locks diff --check 915f216..HEAD          # rc=0
git --no-optional-locks diff --check                          # 工作树 rc=0
git --no-optional-locks diff --name-only 915f216..HEAD -- '*.py'   # 仅 4 个 tests/ 文件
git --no-optional-locks diff --name-only 08da5dc..HEAD -- '*.py'   # 空
git --no-optional-locks diff --name-only 08da5dc..HEAD | grep -vE '\.(md|json)$'  # 空
git --no-optional-locks diff --stat 915f216..HEAD ; diff --name-only 915f216..HEAD   # 11 个文件
git --no-optional-locks diff --stat 08da5dc..HEAD ; diff --name-only 08da5dc..HEAD   # 4 个文件
git --no-optional-locks show --stat --format='%H %s %b' 09a31d4 ; show 09a31d4:<handoff.json>
git --no-optional-locks worktree list ; worktree list --porcelain
git --no-optional-locks branch --no-merged main            # 空
git --no-optional-locks for-each-ref refs/remotes/          # 5 条（含 origin/HEAD）
git --no-optional-locks ls-remote origin                    # refs/heads/main = 09a31d4
git --no-optional-locks reflog show refs/remotes/origin/main
git --no-optional-locks diff --name-only 915f216..HEAD -- transcripts/   # 空
git --no-optional-locks ls-files transcripts | wc -l        # 130
git --no-optional-locks ls-files docs/plans | wc -l         # 87
git --no-optional-locks ls-tree -r --name-only 915f216 -- docs/plans | wc -l  # 84
git --no-optional-locks cat-file -t <每一个被点名的 SHA>
git --no-optional-locks check-ignore -v config.json .cache.json .translate_cache.json .benchmarks .ruff_cache
git --no-optional-locks ls-files --error-unmatch config.json    # rc=1（未 tracked）
```
文件系统：`ls`（Projects 层 11 个已清理路径 + 2 个共享容器 + 仓内 `.benchmarks`、`.ruff_cache`）；`nl -ba .gitignore`；
`filing-fetch` / `company-wiki` 只读核对（`status`、`log -4`、`diff | grep -c`、逐行 `sed -n`）。

自写脚本（**全部置于仓外** `C:/Users/郑曾波/Projects/earnings-transcripts/_closeout_scratch/recheck_20261114/scripts/`，用 `python -I` 运行，复核结束后整个目录删除）：
- `recheck.py` —— 裸 JSON 解析 + 卡定 13 个顶层键（集合与顺序）+ schema_version + `base/final` 字段 + 49 条 disposition 的五字段与 5 词表状态 + `cleanup`/`tests`/`independent_review` 结构 + 全文件 `PENDING` 扫描 + markdown 相对链接解析。
- `jsondiff.py` —— `08da5dc` 与 `f76a168` 两版交接 JSON 的**语义 diff**（逐路径标量比对 + disposition `path→status` 映射 diff + `tests` 逐条比对 + `remaining_items` 三元组比对 + 数字收据字段单列）。
- `astequiv.py` —— 独立 AST 取值等值校验（解析模块常量 + 跨模块 import 常量），比较 base `915f216` 与 HEAD 的 4 个测试文件。
- `credscan.py` —— 读取 gitignored 的 `config.json`，把其中的密钥值与**全部 tracked 文件**逐字节比对（**不打印任何取值**，只打印键名与长度）。

测试（`--basetemp` 全部在仓外 scratch 下，`-B`、`-p no:cacheprovider`，运行后删除，仓内无任何测试输出）：
```
python -B -m pytest tests/test_transcript_api.py tests/test_provider_cost_capability.py \
        tests/test_common.py tests/test_du7_extraction_wiring.py -q -p no:cacheprovider --basetemp <scratch>/tmp/t_aff
python -B -m pytest tests/test_transcript_audit.py::test_write_receipts_never_overwrites_conflicting_receipt -q ... --basetemp <scratch>/tmp/t_audit
python -B -m pytest <3 个时序敏感用例> -q ... --basetemp <scratch>/tmp/t_time
```

**未执行**（按任务约束）：未跑 mimosa CLI 的 `--update-rules` / `--with-glm`，未调用任何 `security-scan` MCP 工具，未联网调用 provider / 翻译 / 模型，未做任何 Git 写操作。

## 7.2 F-01 … F-12 逐条关闭表

| # | 严重度 | 第一轮要求 | 本轮实测证据 | 本轮结论 |
|---|---|---|---|---|
| F-01 | P2 | `final.head` / `final.remote_head` / `independent_review.*` 回填真实值，文件内不得残留 `PENDING_*` | `et-closeout-handoff.json:13` = `08da5dca4f680457775e8184730fa4499a8d3dd5`、`:14` = 同值、`:465` `reviewer_agent` = 真实描述、`:466` `reviewed_head` = `08da5dc…`、`:467` `result` = `PASS - P0=0, P1=0, P2=12…`；**全文件 `grep -n PENDING` → rc=1（0 命中）**；自写 `recheck.py` 的「no PENDING_* token anywhere」= PASS | **closed**（占位符问题已消除；字段取值本身的时效性问题另立 N-01 / N-04，均为 P2，不影响本条关闭） |
| F-02 | P2 | 根计划摘要行不得与「ET 自身剩余项」并列内容自相矛盾 | `task_plan.md:26` 现为「**唯一下一步只有一条**，见下方；另有 2 条登记待办（真实 MF 页面 DOM 适应性验收、时序敏感用例稳健性）与 1 类本质不可消除的限制，见「ET 自身剩余项」」；正文剩余项 5 节（`:102`–`:119`）与 JSON `remaining_items`（ET 6 条：1 条唯一下一步 + 2 条 open 登记待办 + 1 条 limitation + 2 条门禁说明）逐条对应 | **closed**（摘要行与正文不再冲突） |
| F-03 | P2 | handoff §1 的「提交与推送顺序」交叉引用应为 §10 | `et-closeout-handoff.md:21` = 「见 §10「提交与推送顺序」」；实测章节标题 `:276` = `## 10. 提交与推送顺序（供 ROOT 核对）` | **closed** |
| F-04 | P2 | 「4 个 Markdown + 4 个测试文件」应改为真实文件数 | `et-closeout-handoff.md:218` 与 `et-closeout-handoff.json:417` 已改为「10 个文件 / 10 files」；**但见 N-02**：在 HEAD `f76a168` 上实测 `git diff --name-only 915f216..f76a168` = **11** 个文件（`et-closeout-review.md` 由 `09a31d4` 加入树） | **closed as originally required**（F-04 的原始错误「4+4=8 / 10」已修正且实质结论「生产模块一行未动」成立：`git diff --name-only 915f216..HEAD -- '*.py'` 仅 4 个 `tests/` 文件）；**新见 N-02：该修正值本身已过期 1** |
| F-05 | P2 | `.ruff_cache/` / `.benchmarks/` 不在 ET 仓 `.gitignore`；`config.json` 实为第 6 行 | `et-closeout-handoff.json:376` 已删除 `.ruff_cache/`、`.benchmarks/`，并写明 9 条 `.gitignore` 条目全名单；`:379` 改为「config.json (line 6 of .gitignore)」。实测 `nl -ba .gitignore` → 9 行，`config.json` 确在**第 6 行**；`git check-ignore -v .benchmarks` → **未命中（rc≠0）**；仓根 `.benchmarks` 存在但 `ls -A` = 0 项（与 JSON 「目录为空，git 不报」一致）；仓根**无** `.ruff_cache`，它在上级容器 `Projects/earnings-transcripts/.ruff_cache` | **closed** |
| F-06 | P2 | `_harness_worktrees` 容器清单须标注为时点观察 | `et-closeout-handoff.json:317` 已改为「Point-in-time observation: right after the ET cleanup … The independent reviewer later observed cmrf-20261008 with only ff-diagnostics, ff-permission-residue and sw-permission-residue … The ET lane touched none of them.」；`et-closeout-handoff.md:143` 同步加了「**该清单是时点观察**」 | **closed** |
| F-07 | P2 | FF 折叠映射的引用应指向真正的构造 | `et-closeout-handoff.json:517` 已改为「filing-fetch/scripts/et_v2_contract.py lines 39-49 hold the collapse map …；filing-fetch/scripts/transcript_tool_transport.py lines 47-60 hold the adjacent closed whitelist」。只读实测：`et_v2_contract.py:39` = `_FAILURES = {`，`:40`–`:50` = **11 条** status+error_code → provider_unavailable / provenance_rejected / request_error 映射，`:51` = `}`；`transcript_tool_transport.py:47` = `_KNOWN_ET_ERRORS = frozenset({` | **closed**（指向的构造正确；末行应为 51 而非 49，属 §7.5 的轻微精度记） |
| F-08 | P2 | AST 校验口径未写明（作者计数 20/3/7/1 vs 复核计数 18/3/1/1） | `et-closeout-handoff.json:419` 已在 `command` 里写明枚举口径（「关键字实参名或字典键名含 key/secret/token/password/credential 的使用点，跨模块 import 的常量按定义值解析」），`:421` 的 `result` 改为不含逐文件计数、并注明「The independent reviewer re-derived this with a stronger method (full string-literal multiset comparison) and confirmed that no literal value was added or lost」；`et-closeout-handoff.md:219` 同步 | **closed**（口径已写明；争议计数被移除而非保留）——本轮**独立复现**该结论，见 §7.4.3 |
| F-09 | P2 | 被 track 的 `.planning/` lane 计划目录须归类为历史 | `task_plan.md:148` 新增一行：「[`.planning/`](.planning/) | 早期 lane 级计划目录（`n5-et-local-text`、`s3-et-bounded-runtime-20261003`、`s3-et-deadline-20261004`、`w06-cutoff-result`）。**历史 lane 计划，不是状态入口**」；`et-closeout-handoff.json:341` 新增 `kind=repo_path`、`status=EVIDENCE_KEEP` 的处置，`:344` 证据「git ls-files .planning = 12 files; each lane directory holds task_plan.md, findings.md and progress.md」；`et-closeout-handoff.md:155` 同步。实测 `git ls-files .planning` = 12 个文件 | **closed** |
| F-10 | P2 | `refs/remotes/origin/HEAD` 须有 disposition | `et-closeout-handoff.json:279` 新增 `{"kind":"remote_ref","path":"refs/remotes/origin/HEAD","status":"ACTIVE_KEEP", …}`；`et-closeout-handoff.md:137` 标题改为「4.4 远端 ref（4 个分支 + 1 个符号引用）」并在正文点名 `refs/remotes/origin/HEAD` → `ACTIVE_KEEP`。实测 `git for-each-ref refs/remotes/` = **5** 条，`origin/HEAD` 在列 | **closed** |
| F-11 | P2 | CWP 工作树的 3 个 tracked 修改须记录为「非 ET 所为」 | `et-closeout-handoff.json:382` 新增 `dirty_exceptions[3]`：`path` =「C:/Users/郑曾波/Projects/company-wiki (3 tracked files modified)」、`state` 列出 `cwp-closeout-handoff.json/.md` 与 `cwp-closeout-review.md`、`disposition` =「not the ET lane's state - left untouched」、`reason` 记「0 hits for ET-related text … company-wiki has its own three CWP closeout commits from the same day … The ET lane wrote nothing in either repository」；`et-closeout-handoff.md:178`–`:181` 同步 | **closed**（本轮只读复核：`filing-fetch` 的 tracked 修改 = **0**；`company-wiki` 现有 **1** 个 tracked 修改（`cwp-closeout-handoff.md`），其 diff 中 ET 字样命中 = **0**，CWP 另有独立提交 `7361aad82`/`ca2602a2f`/`8ae20ce6f`/`2cbc01156` —— 归属判断成立；数量漂移见 N-05） |
| F-12 | P2 | 校验脚本未随仓交付 → 接受并记录理由 | `et-closeout-handoff.md:255`「P2 关闭情况」行明确写「F-12（校验脚本未随仓交付）**接受并记录理由**：卡不要求交付校验脚本，独立 reviewer 已用更强的口径（全字符串字面量多重集比对）独立重做并确认结论为真」；`et-closeout-handoff.json:467` 的 `independent_review.result` 亦复述该处置 | **accepted with rationale（已记录）** —— 本轮**独立重做**同一校验并确认结论为真，见 §7.4.3 |

**F-01…F-12 关闭计数：closed 11（F-01…F-07、F-09、F-10、F-11）+ accepted-with-rationale 1（F-12，理由已入册）+ still open 0。**

## 7.3 Nothing-else-moved 检查

| 检查 | 命令 | 结果 |
|---|---|---|
| 变更文件集合 | `git diff --name-only 08da5dc..f76a168` | **4 个文件**：`task_plan.md`、`docs/.../et-closeout-handoff.md`、`docs/.../et-closeout-handoff.json`、`docs/.../et-closeout-review.md`（后者是 `09a31d4` 把第一轮报告**由未跟踪纳入版本库**，内容 203 行、0 删除） |
| 无产品代码 | `git diff --name-only 08da5dc..f76a168 -- '*.py'` | **空** |
| 无 wire/schema/contract | `git diff --name-only 08da5dc..f76a168 | grep -vE '\.(md\|json)$'` | **空**（唯一被改的 `.json` 是交接收据本身，不是任何 wire / schema / contract） |
| `git diff --check` | `git diff --check 08da5dc..f76a168` / `915f216..HEAD` / 工作树 | 三次均 **rc=0** |
| 数字收据（清理） | 语义 diff | `cleanup.file_count` = **2183 → 2183**、`cleanup.bytes` = **126336536 → 126336536**、`cleanup.paths` = **11 → 11**（逐位未变） |
| 数字收据（测试） | 语义 diff | `tests` 条目数 = **12 → 12**；`tests[2]`–`tests[11]`（mimosa 门禁 high 19→0 / low 8、`1 failed, 662 passed`、`119 passed`、`1 of 5 runs fully green`、`git diff --check`、JSON 可解析、链接、临时根、0 次网络）**逐字未变**；仅 `tests[0]`（F-04 改写）与 `tests[1]`（F-08 改写）文本变化，且**均未改动任何既有的实质数字**（`tests[1]` 是把有争议的「20/3/7/1」**移除**并改为写明口径，不是改动数字） |
| disposition 状态 | `path→status` 映射 diff | **没有任何一条既有处置的状态被改动**；只有 2 条**新增**：`refs/remotes/origin/HEAD` → `ACTIVE_KEEP`（F-10）、`.planning/…` → `EVIDENCE_KEEP`（F-09）；条数 47 → 49；状态分布 `ACTIVE_KEEP 7 / DELETE_AFTER_OWNER_CONFIRMATION 18 / ABSORBED 15 / EVIDENCE_KEEP 7 / UNCLASSIFIED_HOLD 2`，**全部落在卡定 5 词表内** |
| HEAD 值 | 语义 diff | `base.head` / `base.remote_head` = `915f216…`（**未变**）；`final.head` / `final.remote_head` / `independent_review.reviewed_head` 由 `PENDING_CANDIDATE_HEAD` 改为 `08da5dc…`（F-01 要求的回填）；`remaining_items` 的 10 条 `owner/item/status` 三元组**逐条未变**；`next_step` **逐字未变** |
| 裸 JSON + 13 键 + 5 状态 | 自写 `recheck.py` | **PASS**（无 ``` 围栏、无 BOM、`json.loads` 通过、`schema_version == "1"`、顶层键集合与**顺序**均 = 卡定 13 个、`base/final` 均含 `branch/head/remote_head`、49 条处置五字段齐全、`cleanup` 含 `paths/file_count/bytes`、`tests` 12 条均含 `command/result`、`independent_review` 含四键、**全文 0 个 `PENDING`**） |
| 相对链接 | 自写 `recheck.py` | `task_plan.md` / `et-closeout-handoff.md` / `progress.md` / `findings.md` / `README.md` 共 **12 条**相对链接，**0 条断链** |
| 仓内证据路径 | 逐条 `sed -n` 核对 | `docs/plans/mb11-et-2026-11-11/handoff.md:140`（`662 passed, 1 failed`，唯一失败即该用例）与 `:146`（「已知失败（非本卡）… 属既有缺陷，未在本卡修复」）**均命中**；`docs/plans/du7-extraction-2026-10-16/handoff.md:92` 有 `## NOT_RUN` 段；`docs/plans/du10-txt-fidelity-2026-10-17/handoff.md:58` 有「无标记残留的句中截断…从字节不可判定」；FF `green_summary.json:50` = `"real_authentication": "NOT_RUN_MAIN_M3"`；CWP `transcript_import.py:107-113` = 「This importer needs no per-document authorization receipt or provider policy file」；CWP `test_transcript_import_cli_e2e.py:238-240` 断言 `rights_policy_sha256` / `download_authorization_hash` 不存在且 `provider_use_policy.json` 不生成；CWP `end_to_end_test_plan.md:86` = 「Transcript acquisition E2E 尚未实现/执行」；CWP `cross_repo_mainline_and_delivery_plan_2026-09-28.md:179` = 「ET/FF companion 仅局部 fake 测过…G-A 必须经过真实编排」——**逐条命中** |

## 7.4 独立抽查（不采信作者自述）

### 7.4.1 仓库状态

| 检查 | 实测 |
|---|---|
| `git branch --no-merged main` | **空**（0 个非祖先本地分支） |
| `git worktree list` | **1 行**：`C:/Users/郑曾波/Projects/earnings-transcripts/earnings-transcripts  f76a168 [main]`；`--porcelain` 同 |
| 11 个已清理工区路径 | 逐个 `test -e`（用 JSON `cleanup.paths` 的**绝对路径**）：**全部不存在**；两个共享容器仍存在（`Projects/cwp-lanes-20261006` 现为空目录；`Projects/_harness_worktrees` 仍由其他 lane 占用） |
| `transcripts/` 未变 | `git diff --name-only 915f216..HEAD -- transcripts/` = **空**；`git ls-files transcripts` = **130**（其中 43 份 `*_earnings_call.txt`、6 家 provider 子目录 + `summary.txt`） |
| 远端 ref | `git for-each-ref refs/remotes/` = 5 条（`origin/HEAD`、`origin/main`、`origin/codex/{mb11-et,et-s3-deadline,et-s3-bounded-runtime}`） |
| 远端实际 HEAD | `git ls-remote origin` → `refs/heads/main = 09a31d4583eda2b7fa334bfa05a040a3ab639019`；本地 `main` = `f76a168`（`ahead 1`） |
| 跨仓边界 | `filing-fetch`：**0** 个 tracked 修改；`company-wiki`：1 个 tracked 修改（CWP 自己的收据），ET 字样命中 **0** |

### 7.4.2 收据 HEAD 可解析性（卡内步骤 1「刷新 MB-ET、DU10 TXT fidelity 和最新主线收据的 HEAD」）

根 `task_plan.md:142`–`:146` 现已逐条写明收据 HEAD：

| 根计划所写 SHA | `git cat-file -t` | 与实际分支 tip 对照 |
|---|---|---|
| `8e116a4`（MB-ET 代码 tip） | `commit` | `git log --oneline 8e116a4 -1` = `chore: drop an unused import from the preflight module` ✔ |
| `22050ed`（`codex/mb11-et`） | `commit` | `git rev-parse codex/mb11-et` = `22050ed` ✔ |
| `ef7fb36` / `571b021` / `915f216`（并入主线的收据链） | `commit` ×3 | 均为 `main` 祖先，链尾 = base ✔ |
| `cb8e29c`（`codex/du10-et-txt-fidelity` tip） | `commit` | `git rev-parse codex/du10-et-txt-fidelity` = `cb8e29c` ✔；`docs/plans/du10-txt-fidelity-2026-10-17/ROOT_RECEPTION.md` 存在 ✔ |
| `6d94c38`（`codex/du9-legacy-comparison-20261016` tip） | `commit` | `git rev-parse codex/du9-legacy-comparison-20261016` = `6d94c38` ✔ |
| `511fe49`（`codex/du7-et-extraction-20261016` tip） | `commit` | `git rev-parse codex/du7-et-extraction-20261016` = `511fe49` ✔ |
| `0cb7173`（`codex/root-du7-et-20261016` tip） | `commit` | `git rev-parse codex/root-du7-et-20261016` = `0cb7173` ✔ |
| `63c4090`（ET-S3 deadline）/ `53e1e60`（ET-S3 bounded runtime） | `commit` ×2 | 与 `codex/et-s3-deadline` / `codex/et-s3-bounded-runtime` 的 tip 一致；`git merge-base --is-ancestor` 均为 `main` 祖先 ✔ |
| 交付序列 `bba1b97` / `08da5dc` / `09a31d4` / `f76a168` | `commit` ×4 | 全部存在 ✔ |

**结论：根计划点名的每一个 SHA 都真实存在于本仓历史，无一是编造或笔误。**

### 7.4.3 假凭据取值等值（独立重做，脚本置于仓外，未采信交接自述）

自写 `astequiv.py`（解析模块常量 + 跨模块 import 常量）比较 base `915f216` 与 HEAD `f76a168`：

| 文件 | 凭据形使用点（base → HEAD） | 取值逐字节相同 | 全字符串字面量多重集：新增 | 丢失 |
|---|---|---|---|---|
| `tests/test_transcript_api.py` | 18 → 18 | **是** | **0** | 12（仅重复内联字面量被去重为 3 个常量声明位；`fake-key` / `fake-secret-for-test` 的**取值**在 head 中仍存在） |
| `tests/test_provider_cost_capability.py` | 3 → 3 | **是**（改为 import `FAKE_FMP_KEY_VALUE` 等，解析后同值） | **0** | 3（`fake-key` 的内联副本） |
| `tests/test_common.py` | 0 → 0 | **是**（`name + CONFIG_KEY_SUFFIX`，`CONFIG_KEY_SUFFIX == "_api_key"`，运行时表达式逐字等价） | **0** | 0 |
| `tests/test_du7_extraction_wiring.py` | 1 → 1 | **是** | **0** | 1（`fake-key` 的内联副本） |

**没有任何一个字面量值被新增；被「丢失」的只是重复内联副本，值本身未变。** 这独立证实了 F-08 / F-12 的实质结论。

### 7.4.4 凭据未泄漏（不打印任何取值）

`credscan.py` 读取 gitignored 的 `config.json`，抽出 3 个密钥项（键名 `mimo_api_key` / `minimax_api_key` / `deepseek_api_key`，**只打印键名与长度，不打印取值**），再与 **346 个 tracked 文件**逐字节比对：**0 命中**。
另：`git check-ignore -v config.json` → `.gitignore:6:config.json`；`git ls-files --error-unmatch config.json` → **rc=1（未 tracked）**。**PASS。**

### 7.4.5 测试（实跑，非引用）

| 项 | 收据声明 | 本轮实测 |
|---|---|---|
| 受影响 4 文件 | `119 passed` | **`119 passed in 2.28s`** ✔ 一致 |
| 唯一下一步用例 | `1 failed`（断言 `rc == 1`，实得 `rc == 0`） | **同一条 1 failed**（`tests/test_transcript_audit.py::test_write_receipts_never_overwrites_conflicting_receipt`，输出 `assert 0 == 1`）✔ |
| 3 个时序敏感用例 | 负载下 1/5 全绿；空闲主机全部通过 | **空闲主机 3 passed in 11.20s** ✔ 与第一轮独立观察（5/5 全绿、11.2 s/次）一致 |

## 7.5 两个后续缺口的判断

### 7.5.1 根计划头部不再写陈旧 HEAD —— 诚实性判断

`task_plan.md:8`–`:13` 现为：

```
> 分支 `main` · remote `origin` = `https://github.com/zhengcb81/earnings-transcripts.git`。
> - **本次收尾基线**：`915f216`（= 收尾前的 `origin/main`，也是最新一次主线收据提交）。
> - **本次收尾交付序列**：`bba1b97` → `08da5dc` → `09a31d4` → 收尾复核收据。
>   **最终 SHA 以交接文件的 `final.head` / `independent_review.reviewed_head` 为准**
>   （该 JSON 与承载它的提交是同一次提交，无法在自身内容里写自己的 SHA；用 `git log --oneline -3` 核对）。
```

**判断：诚实，且读者仍能找到权威最终 SHA，但指针方式有一处不精确。**

- 头部**不再宣称**「HEAD = `915f216`」（那是第一轮 reviewer 指出的陈旧值），改为陈述「收尾基线 = `915f216`」并把它明确限定为「收尾**前**的 `origin/main`」——这是准确的（`git reflog show origin/main` 显示 `915f216` 确实是本轮开工前的远端 tip）。
- **自指限制被公开说明**（「无法在自身内容里写自己的 SHA」），这是诚实且正确的技术事实，不是搪塞。
- **读者仍可定位权威 SHA**：按头部指示 `git log --oneline -3` 即见 `f76a168` / `09a31d4` / `08da5dc`；`git rev-parse HEAD` = `f76a168…`。
- **不精确之处**：头部把 `final.head` / `independent_review.reviewed_head` 称作「最终 SHA」，但两者的值都是 `08da5dc` —— 即**已审候选** SHA，不是交付 HEAD（`f76a168`），也不是远端 tip（`09a31d4`）。JSON 的 `final.note` 把这一点讲清楚了（"head is the reviewed candidate commit … use `git log --oneline -3` to see it"），但根计划的头部没有同步这个限定语。读者若不点开 JSON，只读根计划头部，会把 `08da5dc` 误当成最终交付 SHA。→ **N-04（P2）**。

### 7.5.2 卡内步骤 1 的「刷新 MB-ET、DU10 TXT fidelity 和最新主线收据的 HEAD」

**已满足**：根计划历史表 `:142`–`:146` 逐条写出了 MB-ET（代码 tip → 交付分支 → 并入主线的三段收据链）、DU10 TXT fidelity（`cb8e29c` + `ROOT_RECEPTION.md`）、DU9（`6d94c38`）、DU7（`511fe49` → `0cb7173`）、ET-S3（`63c4090` / `53e1e60`）的收据 HEAD。**本轮用 `git cat-file -t` 逐个验证，全部为真实存在的 commit，且与对应分支 tip 一致**（见 §7.4.2）。

## 7.6 新发现

> 均为**收据精度**类问题（P2 = 文档/收据精度问题，不阻断交付，沿用第一轮严重度定义）；无 P0、无 P1。
> N-02 / N-03 / N-01 有同一个根因：**审查收据提交本身把新文件写进了树里，而收据里的计数与 SHA 是在这一步之前测的，于是刚修好的数字又过期了。**

| # | 严重度 | 问题 | 证据（实测） | 建议 |
|---|---|---|---|---|
| N-01 | P2 | 交接 JSON 的 `final.remote_head` 记录的是**已被推过一次的旧远端 tip**。字段值 `08da5dca…`，但真实远端 `refs/heads/main` = `09a31d4583eda2b7fa334bfa05a040a3ab639019`（`git ls-remote origin`）。reflog 佐证：`origin/main` 于 17:03:34 推到 `08da5dc`，17:04:15 又推到 `09a31d4`（即收据提交本身被推送）。`final.note` 里「origin/main was verified to equal this SHA right after the reviewed push」如实披露了它记录的是**那一刻**的值，但字段名 `remote_head` 会被读成「远端当前 head」。卡内要求「base/final 含 branch/head/remote_head」，此处是**过期值**，不是占位符 | `et-closeout-handoff.json:14` = `08da5dc…` vs `git ls-remote origin` → `09a31d4…`；`git reflog show refs/remotes/origin/main` | 推送 `f76a168` 前后各刷新一次该字段，或把字段语义改写为「被审候选推送时的远端 tip」并在字段旁注明 |
| N-02 | P2 | 「变更文件数 = 10」已过期 1：HEAD 上 `git diff --name-only 915f216..f76a168` = **11** 个文件（`et-closeout-review.md` 由 `09a31d4` 加入树）。同一根因还有第二处：`et-closeout-handoff.json:338` 的处置证据「git ls-files docs/plans = 84 files, of which 31 under receipts/, 9 under evidence/, **4 under review/**」在 HEAD 上应为 **87 / 31 / 9 / 7**（实测 `git ls-files docs/plans | wc -l` = 87；第 4 段聚合 receipts 31、evidence 9、review 7），`et-closeout-handoff.md:155` 同样写「（31）、（9）、（4）」 | `et-closeout-handoff.md:218`、`et-closeout-handoff.json:417`、`:338`、`et-closeout-handoff.md:155`；实测 `git diff --name-only 915f216..f76a168 \| wc -l` = 11、`git ls-files docs/plans \| wc -l` = 87 | 一并刷新为 11 与 87/31/9/7。**实质结论不受影响**：`git diff --name-only 915f216..HEAD -- '*.py'` = 仅 4 个 `tests/` 文件，生产模块一行未动 |
| N-03 | P2 | 「收据提交只含一个文件」的说法已过期：`et-closeout-handoff.md:281`（§10 第 4 步）写 C2「**只包含** `et-closeout-review.md`（及 `et-closeout-handoff.json` 中 … 指针回填）」，`et-closeout-handoff.json:15` 的 `final.note` 写「**One** follow-up commit adds only the review receipt …」。实测从 `08da5dc` 到 HEAD 有**两个**后续提交（`09a31d4`、`f76a168`），`git diff --stat 08da5dc..f76a168` = **4 个文件**（handoff.json / handoff.md / review.md / task_plan.md），不是 1–2 个。§10 的核对指引「`git diff --stat C1..C2` 看增量只含审查收据」在当前树上得到 4 个文件 | `et-closeout-handoff.md:281`、`:283`；`et-closeout-handoff.json:15`；`git diff --stat 08da5dc..f76a168` | 把「One follow-up commit / 只包含」改为「两个仅含文档与审查收据的后续提交」并列出实际 4 个文件；**实质结论仍成立**（增量 100% 是 `.md`/`.json`，无 `.py`，无 wire/schema/contract） |
| N-04 | P2 | 根计划头部把 `final.head` / `independent_review.reviewed_head` 称作「最终 SHA」，但两者的值都是 `08da5dc`（已审候选），既不是交付 HEAD `f76a168`、也不是远端 tip `09a31d4`；只读根计划头部的读者会误取 `08da5dc` 当最终 SHA。与 N-01 叠加后，**JSON 里的两个 SHA 字段都不是「当前远端 / 当前交付」SHA** | `task_plan.md:11`–`:12`；`et-closeout-handoff.json:13`、`:466`；`git rev-parse HEAD` = `f76a168…`；`git ls-remote origin` = `09a31d4…` | 在根计划头部把措辞改为「**已审候选** SHA」（并保留 `git log --oneline -3` 的指引）；或让 `final` 段同时给出「reviewed candidate」与「delivery tip」两个字段 |
| N-05 | P2（轻微） | CWP 工作树「3 个 tracked 修改」已漂移为 **1** 个（`cwp-closeout-handoff.md`）。`et-closeout-handoff.md:178` 写的是「**独立审查时** company-wiki 工作树有 3 个 tracked 文件处于 modified」——**时点限定写得对，诚实**；但 JSON 的 `dirty_exceptions[3].state`（`et-closeout-handoff.json:383`）没有同样的时点限定 | 只读复核 `company-wiki`：`git status --porcelain=v1` → 1 个 ` M`；`git diff \| grep -ciE 'earnings-transcripts\|ET owner'` → **0** | 在 JSON 该条加「at independent-review time」限定语（与 `.md` 对齐）。**归属结论不变**：CWP lane 自己的收据，非 ET 产物 |

### 7.6.1 复核中新发现的更细小记（不单列编号）

- `et-closeout-handoff.json:517` 引「`et_v2_contract.py` lines 39-49」：`_FAILURES` 字典实际跨 39–51 行（`:39` 开头、11 条映射 `:40`–`:50`、`:51` 收尾）。起始行正确，结束行少 2；不影响 F-07 的关闭判定。
- `et-closeout-handoff.md:193` 的「保留」行仍写「4 个远端 ref」，而 §4.4 已改为「4 个分支 + 1 个符号引用」（F-10 的修正只改了 §4.4，未同步这一行）。属同一处口径微差。

## 7.7 复核最终结论

**PASS —— 绑定 HEAD `f76a1682aa488d71e6f998f12c59eb24651432b3`（`main`）。**

- **F-01 … F-12 全部处置完毕**：11 项 closed、1 项（F-12）accepted-with-rationale 且理由已入册、**0 项仍 open**。逐条均以本轮实测证据复验，未采信任何提交说明。
- **Nothing-else-moved 通过**：`08da5dc..f76a168` 只动 4 个 `.md`/`.json` 文档，**零 `.py`、零 wire/schema/contract**；`git diff --check` 三次 rc=0；交接 JSON 仍是**裸 JSON**、顶层**恰好**卡定 13 键且顺序一致、49 条处置的状态**只落在 5 词表内**且**无一条既有处置被改状态**（只新增 F-09 / F-10 两条）；**清理收据 `2183` / `126336536`、`tests` 12 条、`next_step`、全部 `remaining_items` 三元组逐位未变**；`task_plan.md` 与交接的 12 条相对链接**全部可解析**。
- **两个后续缺口**：根计划头部不再写陈旧 HEAD，自指限制被公开披露、读者仍可经 `git log --oneline -3` 定位权威 SHA（**诚实**，但指针措辞不精确 → N-04）；卡内「刷新 MB-ET / DU10 / 最新主线收据 HEAD」已满足，**所点名的每一个 SHA 经 `git cat-file -t` 验证真实存在**且与对应分支 tip 一致。
- **独立抽查全部通过**：`git branch --no-merged main` 为空；worktree 只剩 1 个；11 个已清理路径全部不存在；`transcripts/` 与 `915f216` 零差异（130 tracked）；**gitignored `config.json` 的 3 个真实密钥值未出现在任何 tracked 文件中**（0 命中，未打印取值）；假凭据取值等值经独立 AST + 全字面量多重集双重复核为真；`119 passed` / 唯一下一步 `1 failed` / 时序用例 `3 passed` 三个测试收据均复现。
- **新发现 5 项，全部 P2**（N-01 `final.remote_head` 过期、N-02 两处文件计数过期、N-03 §10/`final.note` 的后续提交数过期、N-04 根计划头部措辞、N-05 JSON 侧缺时点限定），**根因同一**：审查收据提交把新文件写进树里后，先前测的计数/SHA 没再刷新。**均不涉及产品代码、wire、行为或任何实质收据数字**（清理/测试/处置/next_step 的数字全部未动），按第一轮同样的口径属**不阻断**；建议在推送前用一个仅含文档的收据提交一并刷新。

**发现计数（本次复核新增）：P0 = 0，P1 = 0，P2 = 5。** 第一轮的 12 项 P2 已全部关闭或按理由接受。

---

**本轮（第二轮复核）的写入边界**：本报告仅追加到 `docs/plans/module-pwf-reconciliation-2026-11-14/review/et-closeout-review.md` 一个文件；**未在 ET 仓做任何 add / commit / push / checkout / restore / remove / worktree-remove**；未在 `filing-fetch`、`company-wiki` 或任何其他仓写入、暂存或提交任何内容（对这两个仓只有只读的 `status` / `log` / `diff` / `sed`）；所有临时脚本与 pytest `--basetemp` 均置于仓外 `C:/Users/郑曾波/Projects/earnings-transcripts/_closeout_scratch/recheck_20261114/`，并在复核结束后整体删除（仓内无任何测试输出）。

---
---

# 8. 第三轮有界复核（ROUND-3 RE-CHECK）— 绑定增量 `f76a168…` → `924116bf0d0c2f5ebfe664b4cc423bc6ebe27a0b`

**复核者**：未参与实施的独立 reviewer agent（ZCode / GLM 会话；即第 7 节 round-2 报告的作者，对 `924116b` 这一修复提交本身不做实施）
**绑定增量**：`f76a1682aa488d71e6f998f12c59eb24651432b3` → `924116bf0d0c2f5ebfe664b4cc423bc6ebe27a0b`（`924116b` = 作者对 round-2 五项 N-01…N-05 的修复提交）
**复核日期**：2026-11-14
**唯一写入仓**：`C:/Users/郑曾波/Projects/earnings-transcripts/earnings-transcripts`（本报告文件除外）

## 8.1 执行内容（全部只读）

```
git --no-optional-locks log --oneline -8 ; rev-parse HEAD ; status --porcelain=v1 -uall
git --no-optional-locks diff --name-only f76a168..924116b          # 3 个文件
git --no-optional-locks diff --numstat f76a168..924116b ; show --stat 924116b
git --no-optional-locks diff f76a168..924116b（逐文件人工读全文）
git --no-optional-locks diff --check f76a168..924116b / 915f216..924116b / 工作树   # 三次 rc=0
git --no-optional-locks reflog show --date=iso refs/remotes/origin/main   # N-01 检查点佐证
git --no-optional-locks ls-remote origin refs/heads/main                    # 实时远端 tip
git --no-optional-locks diff --stat 915f216..08da5dc ; diff --name-only … | wc -l   # = 10
git --no-optional-locks diff --name-only 915f216..f76a168 | wc -l           # = 11
git --no-optional-locks diff --name-only 08da5dc..origin/main               # 仅收据文件
git --no-optional-locks log --oneline -6                                    # 根计划头部所给命令
git ls-tree -r --name-only 915f216 -- docs/plans（+ awk 分段计数）            # N-02 锚点
git ls-files docs/plans（+ awk 分段计数）                                    # 实时对照
自写脚本（仓外 _closeout_scratch/round3_20261114/scripts/，python -I 运行，结束后删除）：
  recheck.py  —— 裸 JSON / 13 键 / base·final 结构 / 49 条处置五字段与 5 词表 /
                 cleanup 2183·126336536·11 / tests 12 行 / PENDING 扫描 /
                 N-01·N-02·N-03·N-05 与双轮 result 的文本断言 / 12 条相对链接
  jsondiff.py —— f76a168 vs 924116b 交接 JSON 语义 diff（逐路径标量、disposition
                 path→status 映射、tests 逐行、remaining_items 三元组、next_step）
```
未执行 mimosa `--update-rules` / `--with-glm` / MCP `security-scan`；未联网调用 provider；未做任何 Git 写操作；未在本仓写入除本报告外的任何文件；未在任何其他仓写入。

## 8.2 N-01 … N-05 逐条关闭表

| # | round-2 要求 | 924116b 实测证据 | 结论 |
|---|---|---|---|
| N-01 | `final.head`/`final.remote_head` 须真实描述卡内检查点并指向实时 tip | JSON `final.head`/`final.remote_head` = `08da5dc…`，`final.note` 重写为「record the card's own checkpoint: after the round-1 PASS, the reviewed candidate 08da5dc was pushed and origin/main was verified equal to it (git reflog show origin/main …)」并指向 `git ls-remote origin refs/heads/main` 与 `git diff --stat 08da5dc..origin/main`。我实跑 `git reflog show --date=iso refs/remotes/origin/main`：`08da5dc … 2026-11-14 17:03:34: update by push`（前一格 `915f216`、后一格 `09a31d4`）——**该检查点推送确实发生过**，字段语义由「实时 tip」改为「已验证的检查点」并公开自引用限制，不再是 stale-wrong | **closed** |
| N-02 | 文件计数锚定到被审候选 / base | `tests[0].command` 改为 `git diff --stat 915f216..08da5dc (the reviewed candidate)`——我实测该范围 = **10 files, 1000 insertions, 165 deletions**（与收据一致）；result 中的「11 files total at the round-2 re-check HEAD f76a168」实测 `git diff --name-only 915f216..f76a168 \| wc -l` = **11** ✔。`dispositions[45].evidence`（docs/plans）改为「At the closeout base 915f216 (git ls-tree -r --name-only 915f216 -- docs/plans): 84 tracked files, of which 31 under receipts/, 9 under evidence/, 4 under review/ … live counts are 87 total / 7 under review/」——我实测 base = **84 / 31 / 9 / 4**，HEAD = **87 / 7**（closeout 新增的 3 个 review 文件逐一点名），逐位相符 | **closed** |
| N-03 | §10 不得再写「one follow-up commit」 | handoff §10 重写为真实交付链表（`bba1b97` 前置修复 → **`08da5dc` = C1 被审候选** → `09a31d4` / `f76a168` / （后续）逐行标注「审查收据（reviewer 要求）」角色）+ 4 条 ROOT 核对命令（`git show --stat 08da5dc`、`git diff --stat 08da5dc..origin/main`、`git ls-remote origin refs/heads/main`、`git log --oneline 915f216..origin/main`）+ 显式披露 SHA 自引用限制；「只包含」字样已从 §10 消失。`final.note` 的「One follow-up commit」同步删除 | **closed** |
| N-04 | 根计划头部须点名被审候选并给权威 tip 读法 | `task_plan.md:10` 现写「`08da5dc`（…**round-1 独审 PASS 绑定的被审候选**）→ 其后均为 reviewer 要求的审查收据提交」；`:11`「**最新 tip 的权威读法**：`git log --oneline -6` 或 `git ls-remote origin refs/heads/main`」（两命令我均实跑，输出含 `924116b` 当前 tip 与 `09a31d4` 当前远端）；`:12` 明确「只记录**被审候选 SHA**（`08da5dc`）与**核对命令**，不记录会立刻过期的 tip 值」 | **closed** |
| N-05 | CWP dirty 条目加时点限定 | JSON `dirty_exceptions[3].path` = 「C:/Users/郑曾波/Projects/company-wiki (3 tracked files modified **at the round-1 independent-review time**)」，`state` = 「at the round-1 review: …; CWP lane may have advanced since」——与 `.md` §5 的 round-2 限定语对齐 | **closed** |

**N-01…N-05：5/5 closed，0 项仍 open。**

## 8.3 Nothing-else-moved（增量范围）

| 检查 | 结果 |
|---|---|
| `git diff --name-only f76a168..924116b` | **恰好 3 个文件**：`et-closeout-handoff.json`、`et-closeout-handoff.md`、`task_plan.md`（numstat 7/7、18/8、3/3） |
| 产品代码 / wire | **0 个 `.py`**，无非 md/json 文件，无 wire/schema/contract 触碰 |
| 收据数字 | 语义 diff：`cleanup.file_count` **2183→2183**、`cleanup.bytes` **126336536→126336536**、`cleanup.paths` 11→11；`dispositions` 49→49 且 `path→status` 映射**零变化**（无一新增/删除/改状态）；`tests` 12→12，仅 `tests[0]` 变化（N-02 要求的锚定改写），`tests[1]`–`tests[11]` **逐字未变**；`remaining_items` 10 条三元组逐字未变；`next_step` 逐字未变；`base` 段逐字未变 |
| JSON 结构 | 仍是裸 JSON（无围栏、无 BOM），顶层**恰好**卡定 13 键且顺序一致，`schema_version="1"`，49 条处置五字段齐全、状态全部落在 5 词表内，全文件 **0 个 `PENDING`** |
| `git diff --check` | `f76a168..924116b`、`915f216..924116b`、工作树三次均 **rc=0** |
| 相对链接 | 3 个被改文档（task_plan.md / handoff.md；json 无链接）及 progress/findings/README 共 **12 条相对链接全部可解析** |

## 8.4 round-2 报告完整性与双轮记录准确性

- **第 7 节（round-2 报告）原样完好**：`924116b` **未触碰** `et-closeout-review.md`（`git diff --name-only f76a168..924116b` 不含该文件）；工作树中第 7 节全部小节标题（7.1–7.7）逐条在位，相对已提交基线 `f76a168` 的 diff **0 行删除**（纯追加）。
- **handoff §8 与 JSON `independent_review.result` 的双轮记录与我的 round-2 报告逐项一致**：§8「绑定 HEAD」= round-1 `08da5dc` + round-2 `f76a168`；「结论」= round-1 PASS（P0=0/P1=0/P2=12）+ round-2 PASS（F-01…F-12 全关 + N-01…N-05 五项 P2）+ 修正方式（时间锚定）；JSON result 明确写「Round 1: PASS bound to 08da5dc … Round 2: PASS bound to f76a168 … N-01..N-05 … fixed in the following receipt commit by time-anchoring every volatile value」。**无夸大、无缩小、与事实相符。**

## 8.5 新发现

**无新增发现（P0 = 0，P1 = 0，P2 = 0）。**

两条非缺陷记录（不编号）：
1. §8 / JSON result 未提 round-3、§10 表中 N-01…N-05 修复行写「（后续）」而非具体 SHA——这正是 §10 顶部已公开披露的 **SHA 自引用限制**的必然结果（收据文件无法包含承载它自己的提交的 SHA），且 round-2 已将「收据提交落在计数之后」定性为根因并改用时间锚定，属设计内的诚实处理，不再计为发现。
2. `git diff --stat 08da5dc..origin/main` 当前实测 = 4 个文件（交接 md/json、`et-closeout-review.md`、`task_plan.md`），全部为收据/文档文件，与 §10「增量应只含审查收据文件」的断言一致（`09a31d4` 已在远端；`f76a168`、`924116b` 与本轮复审收据提交推送后该命令仍是 ROOT 的实时核对点）。

## 8.6 第三轮复核最终结论

**PASS —— 绑定增量 `f76a168… → 924116bf0d0c2f5ebfe664b4cc423bc6ebe27a0b`（`main`，当前本地 HEAD）。**

`924116b` 是一次**纯收据时间锚定**提交：N-01…N-05 全部以我独立实测的证据关闭（reflog 检查点、10/11/84/31/9/4/87/7 计数逐一复跑），增量恰好 3 个文档文件，零产品代码、零 wire、零收据数字变化，JSON schema 与相对链接完好，round-2 报告原样未被触碰，双轮记录准确。**三轮累计：F-01…F-12 全部关闭或按理由接受（round-1），N-01…N-05 全部关闭（round-2 → round-3），无任何一轮遗留 open 项。**

**第三轮写入边界**：本节仅追加到本报告文件；未在 ET 仓做任何 add / commit / push / checkout / restore / remove / worktree-remove；未在任何其他仓写入；临时脚本置于仓外 `_closeout_scratch/round3_20261114/` 并在复核结束后删除。

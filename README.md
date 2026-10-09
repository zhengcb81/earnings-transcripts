# 美股电话会议纪要工具

自动获取美股上市公司 Earnings Call Transcripts，支持 MiMo/MiniMax/DeepSeek LLM 中英对照翻译和 Web 阅读器。

## 功能

- 从 Motley Fool 自动下载英文电话会议纪要
- MiMo / MiniMax / DeepSeek LLM 翻译（Google Translate 兜底）
- Web 阅读器：左右分屏中英对照、段落对齐、KPI 高亮
- 三种输出格式：英文原文 / 中英JSON / 中英夹排TXT
- 配置驱动，无硬编码

## 快速开始

```bash
pip install requests beautifulsoup4 lxml openai translatepy pyyaml

# 1. 下载（默认原语言；翻译需显式 --translate）
python3 scraper.py --periods 2026Q1,2026Q2

# 2. 启动阅读器
python3 reader.py
# 浏览器打开 http://localhost:8765
```

> **Windows 注意**：本机依赖装在 Miniconda 里，用 `C:/Miniconda/python.exe` 代替 `python3`
> （WorkBuddy 自带的托管 Python 没装依赖，直接跑会 ImportError）。
> 缺 `translatepy` 时只影响 Google 兜底后端，三个 LLM 后端照常可用。

## 项目结构

```
earnings-transcripts/
├── config.yaml          # 统一配置（所有参数集中管理）
├── config.json          # LLM API keys（需手动创建）
├── config.py            # 配置加载/路径解析
├── naming.py            # 文件命名规则
├── cache.py             # JSON 缓存
├── lock.py              # 单实例锁（防并发运行互相覆盖）
├── parser.py            # 段落解析/文件头解析/行分类
├── translator.py        # 翻译体系（MiniMax/MiMo/DeepSeek/Google）
├── common.py            # 共享模块（re-export 门面）
├── scraper.py           # 有限批次采集入口（transcript_api 薄编排，默认原语言）
├── transcript_artifact.py # 本地原件字节/身份核验 + et-local-text-receipt/1 小收据
├── transcript_audit.py  # 本地原件 audit CLI（默认只读、零网络、零翻译）
├── translate.py         # 独立翻译器
├── make_interleaved.py  # 中英夹排TXT生成器
├── reader.py            # Web 阅读器
├── templates/
│   └── index.html       # 阅读器 HTML 模板
├── companies.txt        # 公司清单
├── tests/               # 测试套件
│   ├── test_common.py   # 共享模块测试
│   ├── test_lock.py     # 单实例锁测试
│   ├── test_reader.py   # 阅读器测试
│   └── test_scraper.py  # 爬虫测试（含增量跳过、summary 生成）
├── transcripts/         # 下载的数据
│   ├── MSFT/
│   │   ├── MSFT_Q3_2026_earnings_call.txt    # 英文原文
│   │   ├── MSFT_Q3_2026_bilingual.json       # 中英对照JSON
│   │   └── MSFT_Q3_2026_interleaved.txt      # 中英夹排TXT
│   └── ...
└── README.md
```

## 配置说明

所有参数集中在 `config.yaml`，主要配置项：

| 区块 | 说明 |
|------|------|
| `paths` | 目录结构和文件路径（含 `lock_file` 单实例锁位置） |
| `naming` | 文件命名规则（后缀/扩展名） |
| `fool` | Motley Fool 爬虫参数 |
| `minimax` | MiniMax LLM 翻译参数（auto 模式首选） |
| `mimo` | MiMo LLM 翻译参数 |
| `deepseek` | DeepSeek LLM 翻译参数 |
| `translate` | 翻译提示词与段落缓存参数 |
| `reader` | 阅读器端口和UI参数 |
| `line_classification` | 行分类规则（标题/参与者/正文） |

API key 放在 `config.json`（单独文件，不入git）：

```bash
cat > ~/earnings-transcripts/config.json << 'EOF'
{
  "mimo_api_key": "your-mimo-key",
  "minimax_api_key": "your-minimax-key",
  "deepseek_api_key": "your-deepseek-key"
}
EOF
```

翻译后端优先级（auto 模式）：MiniMax → MiMo → DeepSeek → Google Translate

## 使用方法

### 爬虫 (scraper.py)

`scraper.py` 现在是现代 `transcript_api` 的薄批量编排层：唯一的 provider 边界、
精确期间、批次限额、`--output` 全隔离；默认保存原语言，不初始化翻译器。

```bash
python3 scraper.py --periods 2026Q1,2026Q2   # 明确期间列表（精确 FY/Q，推荐）
python3 scraper.py --ticker MSFT --periods 2026Q2
python3 scraper.py --list --periods 2026Q2   # 只做 metadata 列表：零正文、零写入
python3 scraper.py --dry-run --periods 2026Q2  # 零 HTTP、零写入、零翻译
python3 scraper.py --quarters 4              # legacy 最近 N 期：仅当 metadata 给出明确 FY/Q 才展开
python3 scraper.py --source fmp --periods 2026Q2   # FMP 精确期；--list/--quarters 对 FMP 具名失败
python3 scraper.py --translate               # 显式启用翻译（默认不翻译）
python3 scraper.py --no-translate            # 兼容旧旗标；当前默认即为不翻译
python3 scraper.py --output D:/et-run        # 原件/日志/锁/临时/manifest 全部写到这里
```

期间语义：

- `--periods 2025Q4,2026Q1` 是**精确 fiscal year/quarter 列表**，不是“最近 N 期”。
- 未给 `--periods` 时走 legacy `--quarters N`（默认 1）：只在 metadata discovery
  给出明确 FY/Q 后有限展开；无法唯一确定则报 `period_unresolved`、零正文抓取、不猜 Q4。
  `--source fmp` 没有 metadata 发现，`--quarters` 直接 `period_unresolved`（零 HTTP）。
- `--periods` 与 `--quarters` 不能同时给；`--list` 与 `--dry-run` 不能同时给。

批次限额（默认值，均为有限值；每次正式采集前核对，不设 unlimited、无隐式重试）：

| 参数 | 默认 | 语义 |
|---|---|---|
| `--max-requests` | 64 | 批次累计 HTTP 请求（含 metadata）上限 |
| `--max-seconds` | 600 | 批次**采集**总时长（硬截止，正有限秒）：全批共用同一 deadline，逐文档只给剩余额度、不重置；单请求 timeout 取 min(30s, 剩余额度)。**只约束网络采集**，显式 `--translate` 的翻译耗时不在该限额内 |
| `--max-response-bytes` | 67108864 | 批次累计响应字节上限（流式累计，超限中断连接） |
| `--max-output-bytes` | 33554432 | 本次**新保存原件**字节上限（不计 manifest/日志） |

硬截止实现（`transcript_tool.py` 与 `scraper.py` 的 fetch/discover/fetch-candidate/list
metadata 统一走同一条链）：每次 provider 采集由内部 supervisor
（`retrieval_runtime.py`）在独立 worker 子进程（`retrieval_worker.py`）里执行，
deadline 从正式操作开始按 monotonic 计时，supervisor 用同一份剩余浮点秒等待
（不四舍五入到 1 秒）；到期先 terminate、必要时 kill，全部等待共享同一个
**1 秒清理宽限**，确认退出后才删除该次临时结果，删除失败则具名报
`retrieval_cleanup_failed`、绝不假装已回收。worker 结果文件有大小/JSON/请求
标识/schema 校验，晚于 deadline 的结果不接受为 `fetched`；worker 被杀或结果
不可信时用量记为**未知**并停止该批（报告不宣称 0 消耗、不恢复完整预算）。
内部用量与 worker 信封不进公共 stdout，不改变任何请求/响应协议字段；
零额度（不 spawn）、disabled provider、缺 key 仍然零外发。默认构造 0 个
translator；只有显式 `--translate` 才在采集完成后由父进程补翻译。

退出码：`0` 成功；`1` 具名失败（provider 不可用、`period_unresolved`、
`output_conflict`、采集 worker 异常 `retrieval_worker_failure` 等）；`2` 用法错误/锁冲突；
`3` 触发批次限额（partial，报告已完成文档，不回滚、不覆盖已有原件）。

Provider 可用性由 `transcript_api.ProviderSettings` 决定：生产默认
Motley Fool disabled（零 HTTP、具名 `unavailable/provider_disabled`），
FMP 需要 `FMP_API_KEY` 或 `--api-key`；缺 key/402/限流/坏响应分别具名报告，
不自动换期间或 provider。所有 HTTP 走 `transcript_api` 的流式
byte/deadline/host 约束，旧的直连 Session 实现已退休。

每次真实运行在输出根写 `run_manifest.json`（request_id/状态/bytes/时间，
不含 key、不含正文；批次预算块在 worker 用量未知时把 requests/response_bytes
记为 null 并置 `usage_unknown: true`，绝不虚报 0 消耗）与
`logs/scraper.log`；`--list`/`--dry-run` 在
目录/日志/锁初始化之前结束，不产生任何写入。

不确定跑下去会发生什么时，先来一次 `--dry-run`：

```
$ python3 scraper.py --periods 2026Q2 --dry-run
======================================================================
DRY RUN — 零 HTTP、零写入、零翻译
======================================================================
  [download ] MSFT Q2 2026
  ...
----------------------------------------------------------------------
  download: 1  skip: 0  translate: 0  unknown: 0
======================================================================
```

`--quarters` 形式的 dry-run 对无法本地确定的期间只报 `unknown`，
不虚构候选。计划与真实跳过判断共用同一个 `plan_action()`。

### 增量执行（重复运行是安全的）

跳过与否看**磁盘上英文原件是否存在 + 复用前的严格核验**
（`transcript_artifact.verify_stored_original`：严格 UTF-8 解码、实算正文与整文件
SHA-256/UTF-8 字节数、头部身份/期间、sidecar 收据；头部 `Characters` 只作历史
诊断、从不当字节长度；文件名只产生“期望”，不是证据）：

| 本地状态 | 报告 / 退出码 |
|---|---|
| download 收据逐项吻合 | `reused`（verified），不下载不覆盖，rc 0 |
| 无收据，但头部 `Ticker`（或已知 URL）+ `Quarter` 可证明且正文非空 | `legacy_unverified`，保持原文件，rc 0 |
| 证明不了身份/期间（仅 URL、无头部、缺 Quarter 等） | `unknown`/`identity_missing`（**非** verified reused），rc 0 |
| 头部或收据与期望矛盾（错 ticker/错期间/URL 不符） | 具名 `output_conflict/identity_mismatch\\|period_mismatch`，原件保留，rc 1 |
| 空文件/乱码/不可读/收据坏/收据哈希不符 | 具名 `output_conflict/empty_original\\|invalid_encoding\\|unreadable\\|receipt_invalid\\|receipt_mismatch`，原件保留，rc 1 |
| 原件缺失 | 下载并原子保存 + 同目录 `*.receipt.json` 小收据（临时文件失败 finally 清理） |
| 加了 `--translate` 且缺译文 | 只补翻译，不重新下载；`unknown` 与冲突不派生翻译 |

完整性边界：核验证明“当前字节 = 收据字节、身份/期间相符”，**不证明内容完整**
（无法凭关键词判断截断）。已有文件绝不自动覆盖、删除或悄悄补下载。

新落盘原件旁附 `et-local-text-receipt/1` 收据（同目录 sidecar，实测约 0.5–0.7 KB，
原子写入、计入 `--max-output-bytes`），绑定 ticker、明确 fiscal year/quarter、
provider、source_url（实际已知才填）、extraction/version、取得时间 `obtained_at`、
canonical 正文与整文件的 SHA-256/UTF-8 字节数。收据只是 **ET 本地附件**：
不宣称 provider 原始 HTTP hash、公开日/as-of 证明或 CWP 准入，也不是第二个
canonical 来源库；`published_date` 恒为 null，`timestamp` 是取得时间不是公开日。

### 本地原件 audit（transcript_audit.py，默认只读）

```bash
python3 transcript_audit.py                    # 只读：JSON 小报告打到 stdout，零写入
python3 transcript_audit.py --report-dir out   # 报告原子写到独立目标目录
python3 transcript_audit.py --write-receipts   # 显式：为可证明且无收据的原件补收据
```

0 网络、0 翻译、0 外部 LLM，与批次复用同一套验证实现。`--write-receipts` 只加
`audit-legacy` 收据（记录当前字节与头部可证明字段，`obtained_at`/`extraction_version`
保持 null、不补造下载时来源），绝不改、删、覆盖 TXT 原件或已有收据。
退出码：0 无具名失败；1 存在损坏/矛盾/坏收据。

翻译本身另有段落级缓存（`.translate_cache.json`，按内容 MD5 命中），
仅在显式 `--translate` 时才会初始化。

### 不能并发运行

`scraper.py` 和 `translate.py` 共用一把文件锁（`.instance.lock`）。第二个实例会直接
退出并报出占用者的 PID：

```
已有 scraper/translate 实例在运行：pid=12345 started=2026-09-03T23:09:33
并发运行会互相覆盖文件、重复消耗翻译额度。
确认没有其它实例后删除锁文件重试：.instance.lock
```

两个实例一起跑时，除了互相覆盖文件，还会争抢同一个 LLM 额度（实测翻译速度掉到 1/4），
并且并发写 `.translate_cache.json` 有损坏风险。锁由操作系统保证在进程退出时释放，
崩溃也不会留下死锁。`--list` 和 `--dry-run` 是只读操作，不受锁限制。

### 翻译器 (translate.py)

```bash
python3 translate.py                   # 翻译所有（auto模式: MiniMax→MiMo→DeepSeek→Google）
python3 translate.py --ticker FIG      # 只翻译Figma
python3 translate.py --backend mimo    # 强制用MiMo
python3 translate.py --backend minimax # 强制用MiniMax
python3 translate.py --backend google  # 强制用Google
python3 translate.py --force           # 重译已有的中英对照文件
```

默认跳过已有译文的文件，只补缺失的。

### 夹排生成 (make_interleaved.py)

```bash
python3 make_interleaved.py            # 从bilingual JSON生成夹排TXT
python3 make_interleaved.py --ticker MSFT
```

### 阅读器 (reader.py)

```bash
python3 reader.py                      # 默认端口8765
python3 reader.py --port 9000          # 自定义端口
```

阅读器功能：
- 左侧公司列表，点击切换
- 季度Tab（← → 方向键切换）
- English / 中英对照 模式切换
- 左右分屏段落对齐，编号一致
- 全文搜索（/ 键聚焦）
- 字号调节（A- / A+）
- 快速跳转栏（Revenue/Q&A 等章节）
- KPI 数字高亮

## 测试

```bash
python3 -m pytest tests/ -v
```

234个单元测试覆盖：配置加载、公司解析、文件命名、缓存、行分类、段落解析、哈希、
transcript解析、翻译器、阅读器数据构建、单实例锁、summary 按磁盘生成、
**批次限额（请求/秒/累计字节/输出字节）**、**精确期间 vs recent-N**、
**provider disabled 与 FMP list/dry-run 零 HTTP**、**原件复用与
`output_conflict` 不覆盖**、**硬截止（阻塞 worker 在 deadline+统一清理宽限内
回收且进程确认退出、超期结果拒收、坏 worker/坏 JSON/超大结果不成功、临时根清完、
用量未知不停报 0）**、**真实 CLI → supervisor → worker → 现有 API → serializer
的假 FMP 200/402/429/缺 key**、**`/2` goldens**、以及 subprocess 真实退出路径、
**本地原件核验与 `et-local-text-receipt/1` 收据（空/截断/乱码/错 ticker/错期间/
Characters 虚报/坏收据/篡改/不同 newline/CRLF、二跑零 HTTP 复用、收据中断原件不丢）**
和 **audit CLI（只读、报告目录、显式补 audit-legacy 收据、真实 MSFT 前后
SHA/size/mtime 不变）**。

```bash
# 本机依赖装在 Miniconda，用这个跑
C:/Miniconda/python.exe -m pytest tests/ -q
```

## 添加公司

编辑 `companies.txt`：

```
TICKER|中文名|英文名|交易所
AAPL|苹果|Apple|nasdaq
```

## 数据源

| 来源 | 费用 | 说明 |
|------|------|------|
| Motley Fool | 旧版 scraper 的历史来源 | 批次与 JSON 工具共用 ProviderSettings，生产默认 disabled（零 HTTP） |
| FMP API | 当前计划与访问权限以官方定价页为准 | 结构化精确期来源（`/stable/earning-call-transcript`） |

## 输出格式

默认每篇 transcript 只生成一个原语言文件（新保存的原文旁附小收据）：

1. `*_earnings_call.txt` — 原文
2. `*_earnings_call.receipt.json` — `et-local-text-receipt/1` 小收据（实测约 0.5–0.7 KB，ET 本地附件）

显式 `--translate` 时额外生成：

3. `*_bilingual.json` — 中英对照JSON（段落对齐）
4. `*_interleaved.txt` — 中英夹排TXT（一段英文一段中文）

真实批次运行另在输出根写 `run_manifest.json` 与 `logs/scraper.log`
（request_id/状态/bytes/时间；不含 key、不含正文）。

## Company-wiki / filing-fetch machine interface (new, isolated)

The files transcript_tool.py and transcript_api.py expose a JSON subprocess boundary for one exact fiscal quarter. This interface is separate from scraper.py: it never loads config.yaml or companies.txt, writes files, initializes logs/locks/caches, invokes translation, or creates bilingual/interleaved outputs. The production provider settings disable Motley Fool before any HTTP session is created. FMP's structured exact-quarter endpoint remains available when the process has a valid key and entitlement.

Carry forward the authorized company task and existing session authorization to
its needed transcript retrieval through currently configured providers and
budgets. Do not request permission again for each transcript or supplier, create
a permission file, or require a live canary. Preserve account capability,
credentials, byte/time/fee limits, unknown prior usage and platform security
review; an unavailable capability remains a named gap.

Example request on stdin (`download_authorized=true` is the single network intent derived from that task; it is not another human approval):

~~~json
{
  "schema_version": "earnings-transcript-request/1",
  "request_id": "fetch-01J...",
  "ticker": "MSFT",
  "exchange": "nasdaq",
  "fiscal_year": 2026,
  "fiscal_quarter": 3,
  "as_of_date": "2026-09-27",
  "provider": "motley_fool",
  "download_authorized": true,
  "timeout_seconds": 30,
  "max_body_bytes": 5000000,
  "max_response_bytes": 8000000,
  "max_cost_usd": "0.00"
}
~~~

Invoke it with the request's network intent set to true:

~~~powershell
$requestJson | C:/Miniconda/python.exe transcript_tool.py --request-stdin --include-source-payload
~~~

For candidate-bound retrieval when the configured provider supports discovery,
use two phases. FMP supports exact fetch only; these phases do not add another
permission step or enable a disabled provider.

1. Run the exact-period request with `--operation discover` and the task's network intent. It returns candidate metadata (`provider_document_id`, canonical URL, exact fiscal period, and publication date); it does not fetch candidate pages.
2. With the unique matching candidate and unchanged resource limits, call `--operation fetch-candidate` using `earnings-transcript-candidate-fetch-request/1`. It makes one body request to that candidate. Candidate host, path, ticker, fiscal period, publication date and provider document ID are validated locally before any request. Add `--include-source-payload` when CWP needs the bounded schema `/2` original response for immutable-raw import.

Candidate-fetch request example (all fields are strict; the nested candidate has exactly these three fields):

~~~json
{
  "schema_version": "earnings-transcript-candidate-fetch-request/1",
  "request_id": "fetch-01J...",
  "ticker": "MSFT",
  "exchange": "nasdaq",
  "fiscal_year": 2026,
  "fiscal_quarter": 3,
  "as_of_date": "2026-09-27",
  "provider": "motley_fool",
  "download_authorized": true,
  "timeout_seconds": 30,
  "max_body_bytes": 5000000,
  "max_response_bytes": 8000000,
  "candidate": {
    "provider_document_id": "/earnings/call-transcripts/2026/09/01/msft-q3-2026-earnings-transcript",
    "source_url": "https://www.fool.com/earnings/call-transcripts/2026/09/01/msft-q3-2026-earnings-transcript/",
    "published_date": "2026-09-01"
  }
}
~~~

The default `--operation fetch` remains the legacy one-step discover-and-fetch behavior for compatibility. It cannot provide an external caller a chance to authorize the exact candidate before the body request, so company-wiki and filing-fetch integrations must use `discover` followed by `fetch-candidate`; the one-step mode is not approved for production integration.

The JSON `download_authorized` field is the single network intent. The old `--allow-download` switch remains accepted for existing callers but cannot override a false request field and is no longer required. Default runtime settings disable Motley Fool in fetch, discover, and candidate-fetch even when intent is true. For FMP, set `FMP_API_KEY` in the process environment; the key is never written to the result or canonical source URL. The tool checks the exact quarter and year in the provider response and returns ambiguous instead of choosing if more than one exact-period document remains. Motley Fool redirects must remain HTTPS on www.fool.com; FMP redirects are rejected. Responses and extracted bodies have hard byte limits and one shared deadline. The CLI enforces that deadline from the outside: each retrieval runs in an internal supervised worker subprocess (`retrieval_runtime.py` → `retrieval_worker.py`) whose monotonic budget starts when the operation starts; the parent terminates then kills the worker at the deadline plus one fixed 1-second cleanup grace, and a result that completes after the deadline is never returned as `fetched`. Internal usage accounting and the worker envelope never appear on stdout, and the key only travels through the controlled process environment.

A default successful result (schema `/1`) contains the untranslated English body, stable provider document ID/source URL, extraction version, the SHA-256 of the provider payload, and a separate SHA-256 of canonical UTF-8 text. To hand the exact bounded provider response to a downstream immutable-raw importer, callers may additionally pass `include_source_payload=True` to the Python API or add `--include-source-payload` to the CLI. That opt-in returns schema `/2`, base64-encoded response bytes, normalized MIME type, a safe effective URL, successful HTTP status, UTC retrieval time, and adapter name/version; it omits the duplicate `content_utf8` field and still performs no file writes. The default remains schema `/1` and does not return the raw response. Schema `/2` has a bounded 24 MiB encoded field ceiling; source responses remain limited by their provider/request byte caps. Motley Fool effective URLs are HTTPS `www.fool.com` path-only URLs with query values and fragments removed. FMP effective URLs may include only symbol/year/quarter, never the API key. Unsupported MIME types fail closed in raw-payload mode.

For Motley Fool, `published_date` comes from the dated source URL. Current ET defaults disable Motley Fool; task intent does not enable an unconfigured provider or establish account capability. FMP returns a structured JSON payload whose `date` field is the call date, not a verified publication date, so its result has `call_date`, `publication_date: null`, and `as_of_cutoff_verified: false`. Downstream historical as-of queries must not treat that call date as publication time. The company-wiki writer owns source intake validation of provider provenance, raw bytes, MIME, date precision, extraction version and hashes under the current configured capability; this is not a per-transcript human permission receipt.

Statuses are fetched, not_found, ambiguous, unsupported, unavailable, not_authorized, rate_limited, provider_error, deadline_exceeded, content_too_large, provenance_rejected, and invalid_request. A missing FMP key reports `unavailable/provider_credentials_missing`, HTTP 402 reports `unavailable/provider_entitlement_required`, and HTTP 429 reports `rate_limited/provider_http_429`. The CLI emits exactly one JSON result on stdout and performs no retries or fallback translation. Unknown providers return unsupported; FMP requires an API key and uses `https://financialmodelingprep.com/stable/earning-call-transcript`. See [FMP's official endpoint documentation](https://site.financialmodelingprep.com/developer/docs/stable/search-transcripts). FMP terms and the account plan must be checked before persisting or redistributing transcript text; FMP says public display/redistribution requires a specific agreement ([terms](https://site.financialmodelingprep.com/terms-of-service), [pricing and access](https://site.financialmodelingprep.com/developer/docs/pricing)). This is a JSON CLI contract, not an MCP protocol server; an MCP wrapper can be added later without changing these schemas.

**Historical integration observation:** The offline CLI-to-API-to-fake-FMP-HTTP route and deterministic `/2` producer goldens are in [tests/golden](tests/golden/README.md). A previous live FMP canary returned HTTP 402; that run did not prove account entitlement or retention capability. At that checkpoint the FMP `/2` result had 26 fields, JSON MIME and a safe period query, while the consumer supported only the 24-field Motley shape; filing-fetch invocation and cross-repository import were still pending. Preserve those historical receipts. They do not impose a new canary, observation window or permission step on current configured requests; current CWP/FF contracts and actual provider responses determine present capability.

### Operation capability and incremental request cost

`python transcript_tool.py --capabilities` returns local `earnings-provider-capabilities/1` metadata without HTTP or credentials. FMP supports exact `fetch`, not `discover` or `fetch-candidate`. Enabled capability is not proof of account entitlement: existing subscription/API quota requests have zero incremental fee, but a real 402/403 is still returned as `provider_entitlement_required`. No plan is purchased or upgraded. Motley Fool remains disabled by default.

The exact request accepts optional `max_cost_usd` as non-negative decimal text with at most two decimal places (default `"0"` for legacy callers). Unknown or above-ceiling incremental prices fail before HTTP. Fee ceilings do not replace the byte limit, timeout or explicit requested network intent.

`--report-usage` additionally emits a final `earnings-retrieval-usage/1` receipt on stderr, separate from content JSON: request ID, `usage_complete`, and measured `requests_used` / `response_bytes_used`. A hard-killed worker can have unknown final usage (`usage: null`, `usage_complete: false`); do not invent zero requests or automatically retry such an operation. Without the flag, legacy stdout/stderr behavior is preserved. Neither option changes untranslated raw bytes or fills an unknown publication date from the call date.

### Optional operation response-byte quota

Exact `earnings-transcript-request/1` and candidate `earnings-transcript-candidate-fetch-request/1` accept optional `max_response_bytes`, a positive integer (booleans, null, zero and fractional values are invalid). It limits the cumulative HTTP response-stream bytes consumed by this operation: listing/discovery, redirects, candidate/body requests all share one remaining quota. It is independent of canonical UTF-8 `max_body_bytes`, which still has the existing10MiB ceiling. FMP JSON wrapper/metadata bytes count before text extraction; an oversized wrapper cannot pass merely because its content is short.

An operation quota of128MiB is valid; it does not increase the fixed per-response FMP16MiB, Motley listing2MiB or transcript-page10MiB limits. For explicit operation quotas, Motley HTML wrapper bytes use the fixed page limit and canonical text uses `max_body_bytes`. Omitting the new field preserves legacy single-operation behavior and its original raw/body limits. Request-count and response-byte quotas inside the existing batch/worker budget are independently optional, not an all-or-nothing pair.

`response_bytes_used` records bytes actually yielded by the provider response stream, including a chunk that crosses a quota. It may be greater than `max_response_bytes`; neither remaining quota nor used amount is clamped. The reader stops at that chunk, discards incomplete payload/content, returns `content_too_large/byte_limit`, and its normal final receipt carries `exhausted: "response_bytes"`. A completed worker report can be `usage_complete: true` for this terminal failure; it does not mean the whole provider response was downloaded. A lost/deadline-killed worker remains incomplete with null usage. Cleanup failures do not replace the primary reason or measured usage, and are reported separately.

Offline replay (the formal tool entry and real worker, only HTTP transport mocked; pytest test dependency required):

```text
python -X utf8 -B tools/run_request_budget_e2e.py --case padded-json --max-response-bytes 1024
python -X utf8 -B tools/run_request_budget_e2e.py --case success --ticker MSFT --max-response-bytes 134217728
python -X utf8 -B tools/run_request_budget_e2e.py --case deadline
python -X utf8 -B tools/run_request_budget_e2e.py --case worker-loss
```

The driver uses a fake key and no network, preserves its immutable original fixture, restores its owned temporary root, and emits a small receipt/SHA proof. It cannot demonstrate real FMP entitlement or paid-account availability. Full focused tests use pytest because the existing test modules are pytest functions with fixtures; a unittest import reporting zero tests is not validation.

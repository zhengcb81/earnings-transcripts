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

# 1. 下载 + 自动翻译
python3 scraper.py --quarters 8

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
├── scraper.py           # 爬虫：下载 + 自动翻译
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

```bash
python3 scraper.py                     # 所有公司，最近 1 个季度（--quarters 默认 1）
python3 scraper.py --quarters 8        # 所有公司，最近8个季度（2年）
python3 scraper.py --ticker MSFT       # 只处理微软
python3 scraper.py --list              # 只列出可用transcripts，不下载
python3 scraper.py --disable-translation  # 只保存英文原文；旧参数 --no-translate 仍可用
python3 scraper.py --force             # 强制重新下载并覆盖已有英文原文
python3 scraper.py --no-cache          # 不读写 .cache.json（不影响基于磁盘文件的跳过判断）
python3 scraper.py --dry-run           # 只报告会做什么，不改动任何文件
```

不确定跑下去会发生什么时，先来一次 `--dry-run`：

```
$ python3 scraper.py --quarters 8 --dry-run
  [skip     ] MSFT Q4 2026
  [skip     ] MSFT Q3 2026
  ...
================================================================
DRY RUN — 未下载、未翻译、未改动任何文件
================================================================
  跳过 skip       : 42
  补翻译 translate: 0
  下载 download   : 0
================================================================
```

它和真正的跳过判断走的是同一个 `plan_action()`，所以结果可以直接信。
（只有 Phase 1 的公司页面探测会联网，那是为了拿到 transcript 链接列表。）

### 增量执行（重复运行是安全的）

`--quarters` 调大不会重跑已完成的季度。跳过与否看**磁盘上产物是否齐全**
（英文原文 + 中英对照都在），而不是看 `.cache.json`——所以缓存文件丢了也没关系。

| 本地状态 | 行为 |
|---|---|
| 英文 + 双语都在 | 跳过，不下载不翻译 |
| 只有英文，缺双语 | 只补翻译，不重新下载 |
| 都没有 | 正常下载 + 翻译 |
| 加了 `--force` | 无视以上，全部重新下载并覆盖英文原文 |

翻译本身另有段落级缓存（`.translate_cache.json`，按内容 MD5 命中），
重跑不会重复烧 token。

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

79个单元测试覆盖：配置加载、公司解析、文件命名、缓存、行分类、段落解析、哈希、
transcript解析、翻译器、阅读器数据构建、爬虫配置、**单实例锁**、**增量跳过的三种
判定分支（skip / translate / download）**、summary 按磁盘生成。

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
| Motley Fool | 免费 | 主力源 |
| FMP API | 当前计划与访问权限以官方定价页为准 | 结构化补充源 |

## 输出格式

每篇transcript生成3个文件：

1. `*_earnings_call.txt` — 英文原文
2. `*_bilingual.json` — 中英对照JSON（段落对齐）
3. `*_interleaved.txt` — 中英夹排TXT（一段英文一段中文）

## Company-wiki / filing-fetch machine interface (new, isolated)

The files transcript_tool.py and transcript_api.py expose a JSON subprocess boundary for one exact fiscal quarter. This interface is separate from scraper.py: it never loads config.yaml or companies.txt, writes files, initializes logs/locks/caches, invokes translation, or creates bilingual/interleaved outputs. It supports Motley Fool HTML pages and FMP's structured transcript endpoint.

Example request on stdin (the caller must only set download_authorized=true after its normal explicit download authorization):

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
  "max_body_bytes": 5000000
}
~~~

Invoke it with both gates enabled:

~~~powershell
$requestJson | C:/Miniconda/python.exe transcript_tool.py --request-stdin --allow-download
~~~

For an orchestrator that must authorize a selected source **before requesting its transcript body**, use two phases:

1. Run the exact-period request with `--operation discover`. It returns only candidate metadata (`provider_document_id`, canonical URL, exact fiscal period, and publication date); it does not fetch candidate pages. The orchestrator must first permit the discovery request itself under its `discover` policy action.
2. After checking the unique candidate, current source rights, the exact download authorization and byte cap, call `--operation fetch-candidate` with the `earnings-transcript-candidate-fetch-request/1` contract. This makes one body request to that candidate only. Candidate host, path, ticker, fiscal period, publication date and provider document ID are checked locally before any request. Add `--include-source-payload` only when the caller needs the schema `/2` bounded raw response for immutable-raw import.

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
  "candidate": {
    "provider_document_id": "/earnings/call-transcripts/2026/09/01/msft-q3-2026-earnings-transcript",
    "source_url": "https://www.fool.com/earnings/call-transcripts/2026/09/01/msft-q3-2026-earnings-transcript/",
    "published_date": "2026-09-01"
  }
}
~~~

The default `--operation fetch` remains the legacy one-step discover-and-fetch behavior for compatibility. It cannot provide an external caller a chance to authorize the exact candidate before the body request, so company-wiki and filing-fetch integrations must use `discover` followed by `fetch-candidate`; the one-step mode is not approved for production integration.

The JSON authorization field is the caller's authorization decision; the --allow-download switch is a second explicit operator/process gate. For FMP, set `FMP_API_KEY` in the process environment; the key is never written to the result or canonical source URL. The tool checks the exact quarter and year in the provider response and returns ambiguous instead of choosing if more than one exact-period document remains. Motley Fool redirects must remain HTTPS on www.fool.com; FMP redirects are rejected. Responses and extracted bodies have hard byte limits and one shared deadline.

A default successful result (schema `/1`) contains the untranslated English body, stable provider document ID/source URL, extraction version, the SHA-256 of the provider payload, and a separate SHA-256 of canonical UTF-8 text. To hand the exact bounded provider response to a downstream immutable-raw importer, callers may additionally pass `include_source_payload=True` to the Python API or add `--include-source-payload` to the CLI. That opt-in returns schema `/2`, base64-encoded response bytes, normalized MIME type, a safe effective URL, successful HTTP status, UTC retrieval time, and adapter name/version; it omits the duplicate `content_utf8` field and still performs no file writes. The default remains schema `/1` and does not return the raw response. Schema `/2` has a bounded 24 MiB encoded field ceiling; source responses remain limited by their provider/request byte caps. Motley Fool effective URLs are HTTPS `www.fool.com` path-only URLs with query values and fragments removed. FMP effective URLs may include only symbol/year/quarter, never the API key. Unsupported MIME types fail closed in raw-payload mode.

For Motley Fool, `published_date` comes from the dated source URL. This interface capability does not grant automated-fetch, retention, or derivation rights; the company-wiki policy currently denies Motley Fool production use. FMP returns a structured JSON payload whose `date` field is the call date, not a verified publication date, so its result has `call_date`, `publication_date: null`, and `as_of_cutoff_verified: false`. Downstream historical as-of queries must not treat that call date as publication time. Before persistence, the company-wiki writer must independently verify current rights and validate provider provenance, raw bytes, MIME, date precision, extraction version, and both hashes.

Statuses are fetched, not_found, ambiguous, unsupported, not_authorized, provider_error, deadline_exceeded, content_too_large, provenance_rejected, and invalid_request. The CLI emits exactly one JSON result on stdout and performs no retries or fallback translation. Unknown providers return unsupported; FMP requires an API key and uses `https://financialmodelingprep.com/stable/earning-call-transcript`. See [FMP's official endpoint documentation](https://site.financialmodelingprep.com/developer/docs/stable/search-transcripts). FMP terms and the account plan must be checked before persisting or redistributing transcript text; FMP says public display/redistribution requires a specific agreement ([terms](https://site.financialmodelingprep.com/terms-of-service), [pricing and access](https://site.financialmodelingprep.com/developer/docs/pricing)). This is a JSON CLI contract, not an MCP protocol server; an MCP wrapper can be added later without changing these schemas.

**Integration status:** both provider boundaries are implemented and offline contract-tested. One user-authorized authenticated FMP canary using the supplied local key-file path returned HTTP 402 (Payment Required); it provided no transcript, and no response content was persisted. Do not retry until the account's endpoint entitlement is confirmed. The CLI reads `FMP_API_KEY` from its environment. filing-fetch invocation, company-wiki canonical import, provider-specific date-quality handling, FMP retention-rights confirmation, and their end-to-end test remain pending the current revenue-forecast cross-repository snapshot/lock being clear. Do not edit or refresh another repository's snapshot from this project.

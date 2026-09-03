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
├── parser.py            # 段落解析/文件头解析/行分类
├── translator.py        # 翻译体系（MiMo/MiniMax/DeepSeek/Google）
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
│   ├── test_reader.py   # 阅读器测试
│   └── test_scraper.py  # 爬虫测试
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
| `paths` | 目录结构和文件路径 |
| `naming` | 文件命名规则（后缀/扩展名） |
| `fool` | Motley Fool 爬虫参数 |
| `mimo` | MiMo LLM 翻译参数（默认） |
| `minimax` | MiniMax LLM 翻译参数 |
| `deepseek` | DeepSeek LLM 翻译参数 |
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
python3 scraper.py --no-translate      # 跳过翻译
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
崩溃也不会留下死锁。`--list` 是只读操作，不受锁限制。

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

45个单元测试覆盖：配置加载、公司解析、文件命名、缓存、行分类、段落解析、哈希、transcript解析、翻译器、阅读器数据构建、爬虫配置。

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
| FMP API | $19-99/月 | 补充源 |

## 输出格式

每篇transcript生成3个文件：

1. `*_earnings_call.txt` — 英文原文
2. `*_bilingual.json` — 中英对照JSON（段落对齐）
3. `*_interleaved.txt` — 中英夹排TXT（一段英文一段中文）

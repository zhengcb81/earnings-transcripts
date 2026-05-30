# 美股电话会议纪要工具

自动获取美股上市公司 Earnings Call Transcripts，支持 DeepSeek LLM 中英对照翻译和 Web 阅读器。

## 功能

- 从 Motley Fool 自动下载英文电话会议纪要
- DeepSeek LLM 翻译（Google Translate 兜底）
- Web 阅读器：左右分屏中英对照、段落对齐、KPI 高亮
- 三种输出格式：英文原文 / 中英JSON / 中英夹排TXT
- 配置驱动，无硬编码

## 快速开始

```bash
pip install requests beautifulsoup4 lxml openai translatepy pyyaml

# 1. 下载 + 自动翻译
python3 scraper.py

# 2. 启动阅读器
python3 reader.py
# 浏览器打开 http://localhost:8765
```

## 项目结构

```
earnings-transcripts/
├── config.yaml          # 统一配置（所有参数集中管理）
├── config.json          # DeepSeek API key（需手动创建）
├── common.py            # 共享模块（配置/命名/缓存/翻译）
├── scraper.py           # 爬虫：下载 + 自动翻译
├── translate.py         # 独立翻译器
├── make_interleaved.py  # 中英夹排TXT生成器
├── reader.py            # Web 阅读器
├── companies.txt        # 公司清单
├── tests/               # 测试套件
│   └── test_common.py   # 32个单元测试
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
| `deepseek` | DeepSeek LLM 翻译参数 |
| `reader` | 阅读器端口和UI参数 |
| `line_classification` | 行分类规则（标题/参与者/正文） |

DeepSeek API key 放在 `config.json`（单独文件，不入git）：

```bash
cat > ~/earnings-transcripts/config.json << 'EOF'
{
  "deepseek_api_key": "***',
  "deepseek_base_url": "https://api.deepseek.com",
  "deepseek_model": "deepseek-v4-flash"
}
EOF
```

## 使用方法

### 爬虫 (scraper.py)

```bash
python3 scraper.py                     # 下载所有公司 + 自动翻译
python3 scraper.py --ticker MSFT       # 只处理微软
python3 scraper.py --quarters 12       # 最近12个季度（3年）
python3 scraper.py --list              # 只列出可用transcripts
python3 scraper.py --no-translate      # 跳过翻译
```

### 翻译器 (translate.py)

```bash
python3 translate.py                   # 翻译所有
python3 translate.py --ticker FIG      # 只翻译Figma
python3 translate.py --backend google  # 强制用Google
```

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

32个单元测试覆盖：配置加载、公司解析、文件命名、缓存、行分类、段落解析、哈希、transcript解析、翻译器。

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

# 美股电话会议纪要工具

自动获取美股上市公司 Earnings Call Transcripts，支持中英对照阅读。

## 功能

- 自动从 Motley Fool 下载英文电话会议纪要
- DeepSeek LLM 翻译为中英对照格式（Google Translate 兜底）
- Web 阅读器：左右分屏、段落对齐、KPI 高亮
- 公司清单配置化，支持批量管理

## 快速开始

```bash
# 安装依赖
pip install requests beautifulsoup4 lxml openai translatepy

# 1. 下载 + 自动翻译（推荐）
python3 scraper.py

# 2. 启动阅读器
python3 reader.py
# 浏览器打开 http://localhost:8765
```

## 文件说明

```
earnings-transcripts/
├── companies.txt          # 公司清单（TICKER|中文名|英文名|交易所）
├── scraper.py             # 爬虫：下载 + 自动翻译
├── translate.py           # 独立翻译器（补翻/重新翻译）
├── reader.py              # Web 阅读器
├── config.json            # DeepSeek API 配置（需手动创建）
├── transcripts/           # 下载的文件
│   ├── MSFT/
│   │   ├── MSFT_Q3_2026_earnings_call.txt    # 英文原文
│   │   └── MSFT_Q3_2026_bilingual.json       # 中英对照（JSON）
│   └── ...
├── .cache.json            # 下载缓存
└── .translate_cache.json  # 翻译缓存
```

## 使用方法

### 爬虫 (scraper.py)

```bash
python3 scraper.py                     # 下载所有公司最新1期 + 自动翻译
python3 scraper.py --ticker MSFT       # 只处理微软
python3 scraper.py --quarters 4        # 最近4个季度
python3 scraper.py --list              # 只列出可用transcripts
python3 scraper.py --no-translate      # 跳过翻译
python3 scraper.py --no-cache          # 忽略缓存重新下载
```

### 翻译器 (translate.py)

```bash
python3 translate.py                   # 翻译所有（DeepSeek优先，Google兜底）
python3 translate.py --ticker FIG      # 只翻译Figma
python3 translate.py --backend google  # 强制用Google Translate
python3 translate.py --backend deepseek # 强制用DeepSeek
```

### 阅读器 (reader.py)

```bash
python3 reader.py                      # 启动，默认端口8765
python3 reader.py --port 9000          # 自定义端口
python3 reader.py --no-browser         # 不自动打开浏览器
```

阅读器功能：
- 左侧公司列表，点击切换
- 季度Tab切换（← → 方向键）
- **English / 中英对照** 模式切换
- 中英对照：左右分屏、段落逐段对齐、同步编号
- 全文搜索（按 / 聚焦搜索框）
- 字号调节（A- / A+）
- 快速跳转栏（Revenue/Q&A 等章节）
- KPI 数字自动高亮（金额、百分比）
- 日期/参与者/章节标题图标标记

## 配置 DeepSeek API

翻译优先使用 DeepSeek LLM（质量更好），自动降级到 Google Translate。

在你的终端（非Hermes）执行：

```bash
cat > ~/earnings-transcripts/config.json << 'EOF'
{
  "deepseek_api_key": "你的API Key",
  "deepseek_base_url": "https://api.deepseek.com",
  "deepseek_model": "deepseek-v4-flash"
}
EOF
```

不配置也能用，会自动使用 Google Translate。

## 添加公司

编辑 `companies.txt`，每行格式：

```
TICKER|中文名|英文名|交易所
```

交易所：`nasdaq` 或 `nyse`。添加后运行 `python3 scraper.py` 即可。

## 数据源

| 来源 | 费用 | 说明 |
|------|------|------|
| Motley Fool | 免费 | 主力源，覆盖面广 |
| Financial Modeling Prep | $19-99/月 | 补充源，API接口 |
| Seeking Alpha Premium | $240/年 | 最广，但反爬严格 |
| Bloomberg/Refinitiv | $20000+/年 | 机构级 |

## 翻译质量

| 后端 | 质量 | 速度 | 费用 |
|------|------|------|------|
| DeepSeek LLM | 高（理解金融语境） | ~2-3分钟/篇 | 按token计费 |
| Google Translate | 中（字面翻译） | ~1分钟/篇 | 免费 |

翻译结果缓存在 `.translate_cache.json`，相同段落不会重复翻译。

## 注意事项

- Motley Fool 不覆盖所有公司，小型中概股可能没有
- Figma (FIG) 2025年7月IPO，transcript从Q3 2025开始
- Generate Biomedicines (GENB) 2026年2月IPO，暂无transcript
- 电话会议纪要通常在 earnings 后 1-2 天发布
- 内容为英文原文，翻译为辅助参考

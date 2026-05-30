"""
common.py - 共享模块：配置加载、文件命名、行分类、翻译逻辑
所有脚本共用此模块，消除硬编码和代码重复。
"""

import json
import re
import hashlib
import logging
import time
from pathlib import Path
from datetime import datetime

# ── 配置加载 ──

def load_config(config_path: Path = None) -> dict:
    """加载 config.yaml，返回嵌套 dict。"""
    import yaml  # 延迟导入，仅此处使用
    if config_path is None:
        config_path = Path(__file__).parent / "config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_path(cfg: dict, key: str) -> Path:
    """从 config.paths 获取绝对路径。"""
    base = Path(cfg["paths"]["base_dir"]).resolve()
    if base == Path("."):
        base = Path(__file__).parent
    return base / cfg["paths"][key]


def setup_logging(cfg: dict, log_file: str = None) -> logging.Logger:
    """根据配置初始化日志。"""
    log_cfg = cfg.get("logging", {})
    fmt = log_cfg.get("format", "%(asctime)s [%(levelname)s] %(message)s")
    level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)

    handlers = [logging.StreamHandler()]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(level=level, format=fmt, handlers=handlers)
    return logging.getLogger()


# ── 公司清单 ──

def load_companies(cfg: dict, filter_ticker: str = None) -> list[dict]:
    """从 companies.txt 加载公司列表。"""
    comp_cfg = cfg.get("companies", {})
    delimiter = comp_cfg.get("delimiter", "|")
    comment = comp_cfg.get("comment_prefix", "#")
    fields = comp_cfg.get("fields", ["ticker", "name_cn", "name_en", "exchange"])

    path = get_path(cfg, "companies_file")
    if not path.exists():
        return []

    companies = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(comment):
            continue
        parts = [p.strip() for p in line.split(delimiter)]
        if len(parts) >= 3:
            entry = {fields[i]: parts[i] for i in range(min(len(parts), len(fields)))}
            if len(parts) < 4:
                entry.setdefault("exchange", "auto")
            companies.append(entry)

    if filter_ticker:
        companies = [c for c in companies if c["ticker"].upper() == filter_ticker.upper()]
    return companies


# ── 文件命名 ──

class FileNaming:
    """统一的文件路径/命名规则。"""

    def __init__(self, cfg: dict):
        n = cfg.get("naming", {})
        self.english_suffix = n.get("english_suffix", "_earnings_call")
        self.bilingual_suffix = n.get("bilingual_suffix", "_bilingual")
        self.interleaved_suffix = n.get("interleaved_suffix", "_interleaved")
        self.english_ext = n.get("english_ext", ".txt")
        self.bilingual_ext = n.get("bilingual_ext", ".json")
        self.interleaved_ext = n.get("interleaved_ext", ".txt")
        self.transcripts_dir = get_path(cfg, "transcripts_dir")

    def company_dir(self, ticker: str) -> Path:
        return self.transcripts_dir / ticker

    def english_path(self, ticker: str, quarter: str) -> Path:
        q = quarter.replace(" ", "_")
        return self.company_dir(ticker) / f"{ticker}_{q}{self.english_suffix}{self.english_ext}"

    def bilingual_path(self, ticker: str, quarter: str) -> Path:
        q = quarter.replace(" ", "_")
        return self.company_dir(ticker) / f"{ticker}_{q}{self.bilingual_suffix}{self.bilingual_ext}"

    def interleaved_path(self, ticker: str, quarter: str) -> Path:
        q = quarter.replace(" ", "_")
        return self.company_dir(ticker) / f"{ticker}_{q}{self.interleaved_suffix}{self.interleaved_ext}"

    def english_to_bilingual(self, english_path: Path) -> Path:
        return english_path.parent / english_path.name.replace(self.english_suffix, self.bilingual_suffix).replace(self.english_ext, self.bilingual_ext)

    def bilingual_to_interleaved(self, bilingual_path: Path) -> Path:
        return bilingual_path.parent / bilingual_path.name.replace(self.bilingual_suffix, self.interleaved_suffix).replace(self.bilingual_ext, self.interleaved_ext)

    def find_english_files(self, ticker: str = None) -> list[Path]:
        pattern = f"*{self.english_suffix}{self.english_ext}"
        if ticker:
            d = self.company_dir(ticker)
            return sorted(d.glob(pattern)) if d.exists() else []
        files = []
        for d in sorted(self.transcripts_dir.iterdir()):
            if d.is_dir():
                files.extend(d.glob(pattern))
        return sorted(files)

    def find_bilingual_files(self, ticker: str = None) -> list[Path]:
        pattern = f"*{self.bilingual_suffix}{self.bilingual_ext}"
        if ticker:
            d = self.company_dir(ticker)
            return sorted(d.glob(pattern)) if d.exists() else []
        files = []
        for d in sorted(self.transcripts_dir.iterdir()):
            if d.is_dir():
                files.extend(d.glob(pattern))
        return sorted(files)


# ── 缓存 ──

class JsonCache:
    """JSON 文件缓存。"""

    def __init__(self, path: Path):
        self.path = path
        self._data = {}
        if path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value):
        self._data[key] = value

    def __contains__(self, key: str):
        return key in self._data

    def __getitem__(self, key: str):
        return self._data[key]

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=1), encoding="utf-8")

    def __len__(self):
        return len(self._data)


# ── 行分类 ──

class LineClassifier:
    """根据行内容分类（datetime / participant / header / body）。"""

    def __init__(self, cfg: dict):
        lc = cfg.get("line_classification", {})
        self.max_participant = lc.get("max_participant_length", 120)
        self.max_header = lc.get("max_header_length", 100)
        keywords = lc.get("header_keywords", [])
        self.header_pattern = re.compile(
            r"^(" + "|".join(re.escape(k) for k in keywords) + r")",
            re.IGNORECASE
        ) if keywords else None

    def classify(self, line: str) -> str:
        s = line.strip()
        if re.match(r"^\w+,\s+\w+\.\s+\d+", s) or re.match(r"^\d{1,2}:\d{2}", s):
            return "datetime"
        if "\u2014" in s and len(s) < self.max_participant:
            return "participant"
        if self.header_pattern and len(s) < self.max_header and self.header_pattern.search(s):
            return "header"
        return "body"


# ── 段落解析 ──

def split_paragraphs(text: str, min_length: int = 5) -> list[str]:
    """将文本按空行分割为段落列表。"""
    paragraphs = []
    current = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            if current:
                paragraphs.append("\n".join(current))
                current = []
        else:
            current.append(stripped)
    if current:
        paragraphs.append("\n".join(current))
    return [p for p in paragraphs if len(p.strip()) >= min_length]


# ── 翻译 ──

class TranslatorFactory:
    """翻译后端工厂。"""

    @staticmethod
    def create(cfg: dict, backend: str = "auto"):
        """
        创建翻译后端。
        backend: "deepseek" / "google" / "auto"（deepseek优先，google兜底）
        """
        if backend in ("deepseek", "auto"):
            ds = DeepSeekTranslator(cfg)
            if ds.available():
                return ds
            if backend == "deepseek":
                logging.warning("DeepSeek unavailable, falling back to Google")

        if backend in ("google", "auto"):
            return GoogleTranslator(cfg)

        raise ValueError(f"Unknown backend: {backend}")


class DeepSeekTranslator:
    """DeepSeek LLM 翻译。"""

    def __init__(self, cfg: dict):
        from openai import OpenAI
        ds = cfg.get("deepseek", {})
        # 兼容 config.json 格式
        json_cfg = {}
        json_path = get_path(cfg, "config_json")
        if json_path.exists():
            try:
                json_cfg = json.loads(json_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

        self.api_key = json_cfg.get("deepseek_api_key", "")
        self.base_url = json_cfg.get("deepseek_base_url", ds.get("base_url", "https://api.deepseek.com"))
        self.model = json_cfg.get("deepseek_model", ds.get("model", "deepseek-v4-flash"))
        self.max_tokens = ds.get("max_tokens", 4000)
        self.temperature = ds.get("temperature", 0.1)
        self.system_prompt = ds.get("system_prompt", "你是专业金融翻译。翻译为中文。")
        self.user_prompt_tpl = ds.get("user_prompt_template", "翻译为中文：\n\n{text}")
        self.availability_prompt = ds.get("availability_prompt", "Say OK")
        self.test_tokens = ds.get("availability_test_tokens", 5)
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def available(self) -> bool:
        if not self.api_key:
            return False
        try:
            r = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": self.availability_prompt}],
                max_tokens=self.test_tokens,
            )
            return bool(r.choices[0].message.content)
        except Exception:
            return False

    def translate(self, text: str) -> str:
        r = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": self.user_prompt_tpl.format(text=text)},
            ],
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )
        return r.choices[0].message.content.strip()

    @property
    def name(self) -> str:
        return "deepseek"


class GoogleTranslator:
    """Google Translate 翻译。"""

    def __init__(self, cfg: dict):
        from translatepy import Translator
        g = cfg.get("google", {})
        self.translator = Translator()
        self.target = g.get("target_language", "Chinese")
        self.test_text = g.get("availability_test_text", "test")

    def available(self) -> bool:
        try:
            r = self.translator.translate(self.test_text, self.target)
            return bool(r.result)
        except Exception:
            return False

    def translate(self, text: str) -> str:
        r = self.translator.translate(text, self.target)
        return r.result

    @property
    def name(self) -> str:
        return "google"


# ── 翻译缓存（文本哈希） ──

def text_hash(text: str, length: int = 16) -> str:
    """MD5 哈希，截取前 N 位。"""
    return hashlib.md5(text.strip().encode()).hexdigest()[:length]


def translate_paragraphs(
    paragraphs: list[str],
    cache: JsonCache,
    translator,
    cfg: dict,
    logger: logging.Logger = None,
) -> list[str]:
    """
    翻译段落列表，带缓存。
    返回翻译后的列表（与输入等长）。
    """
    t_cfg = cfg.get("translate", {})
    hash_len = t_cfg.get("hash_length", 16)
    min_len = t_cfg.get("min_paragraph_length", 5)
    ds_cfg = cfg.get("deepseek", {})
    g_cfg = cfg.get("google", {})
    save_interval = ds_cfg.get("cache_save_interval", 10)
    sleep_ds = ds_cfg.get("sleep_between_calls", 0.3)
    sleep_g = g_cfg.get("sleep_between_calls", 0.5)
    sleep = sleep_ds if translator.name == "deepseek" else sleep_g

    log = logger or logging.getLogger()
    results = []
    total = len(paragraphs)

    for i, p in enumerate(paragraphs):
        h = text_hash(p, hash_len)
        if h in cache:
            results.append(cache[h])
            continue

        if len(p.strip()) < min_len:
            results.append(p)
            continue

        try:
            result = translator.translate(p)
            cache.set(h, result)
            results.append(result)
        except Exception as e:
            log.warning(f"  Translate error [{i}]: {e}")
            results.append(p)

        if (i + 1) % save_interval == 0:
            log.info(f"  Translation progress: {i+1}/{total}")
            cache.save()

        time.sleep(sleep)

    cache.save()
    return results


# ── 文件头解析 ──

def parse_transcript_header(content: str, sep: str = "=" * 70) -> dict:
    """从 transcript 文件内容解析头部元数据。"""
    parts = content.split(sep)
    if len(parts) < 3:
        return {}
    header = parts[1]
    meta = {}
    for line in header.strip().split("\n"):
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta


def extract_body(content: str, sep: str = "=" * 70) -> str:
    """从 transcript 文件内容提取正文（头部之后的部分）。"""
    parts = content.split(sep)
    if len(parts) < 3:
        return content
    return sep.join(parts[2:])

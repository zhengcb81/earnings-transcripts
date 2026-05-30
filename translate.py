#!/usr/bin/env python3
"""
Transcript翻译器: DeepSeek LLM优先, Google Translate兜底
Usage: python3 translate.py              # 翻译所有
       python3 translate.py --ticker MSFT # 只翻译指定
       python3 translate.py --backend google # 强制用Google
"""

import json, re, time, logging, hashlib, argparse
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent
TRANSCRIPTS_DIR = BASE_DIR / "transcripts"
CACHE_FILE = BASE_DIR / ".translate_cache.json"
CONFIG_FILE = BASE_DIR / "config.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("translate")

# ── Load config ──
def load_config():
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}

# ── Translation backends ──
class DeepSeekBackend:
    def __init__(self, api_key, base_url, model):
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.system = "你是专业金融翻译。将英文财报电话会议纪要翻译为中文。保留所有数字、金额、百分比、ticker原样不动。人名保留英文。专业术语用标准翻译：constant currency=固定汇率, revenue=营收, operating income=营业利润, EPS=每股收益, gross margin=毛利率, RPO=剩余履约义务。公司名/产品名保留英文。每段翻译后换行附原文。"

    def translate(self, text: str) -> str:
        r = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.system},
                {"role": "user", "content": f"翻译为中文，翻译后换行附原文：\n\n{text}"},
            ],
            max_tokens=4000,
            temperature=0.1,
        )
        return r.choices[0].message.content.strip()

    def available(self) -> bool:
        try:
            r = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "Say OK"}],
                max_tokens=5,
            )
            return "OK" in r.choices[0].message.content
        except Exception:
            return False

    @property
    def name(self): return "deepseek"


class GoogleBackend:
    def __init__(self):
        from translatepy import Translator
        self.translator = Translator()

    def translate(self, text: str) -> str:
        r = self.translator.translate(text, "Chinese")
        return r.result

    def available(self) -> bool:
        try:
            r = self.translator.translate("test", "Chinese")
            return bool(r.result)
        except Exception:
            return False

    @property
    def name(self): return "google"


# ── Cache ──
def load_tcache():
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}

def save_tcache(cache):
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=1))

def text_hash(text):
    return hashlib.md5(text.strip().encode()).hexdigest()[:16]


# ── Line classifier ──
def classify_line(line):
    s = line.strip()
    if re.match(r'^\w+,\s+\w+\.\s+\d+', s) or re.match(r'^\d{1,2}:\d{2}', s):
        return 'datetime'
    if '—' in s and len(s) < 120:
        return 'participant'
    if len(s) < 100 and re.match(r'^(Revenue|Operating|Earnings|Gross|Net|Free Cash|Guidance|Q&A|Question|Call participants|Highlights|Summary|Financial|Cash Flow|Capital|More Personal|Intelligent Cloud|Productivity|AI Business|Copilot|GitHub|Fabric|Azure|Dynamics|LinkedIn|Windows|Search|Gaming)', s, re.I):
        return 'header'
    return 'body'


# ── Translate paragraphs ──
def translate_paragraphs(paragraphs: list, cache: dict, backend) -> list:
    results = []
    total = len(paragraphs)

    for i, p in enumerate(paragraphs):
        h = text_hash(p)
        if h in cache:
            results.append(cache[h])
            continue

        if len(p.strip()) < 5:
            results.append(p)
            continue

        try:
            translated = backend.translate(p)
            cache[h] = translated
            results.append(translated)
        except Exception as e:
            log.warning(f"  Translate error [{i}]: {e}")
            results.append(p)  # fallback to original

        if (i + 1) % 10 == 0:
            log.info(f"  Progress: {i+1}/{total} ({100*(i+1)//total}%)")
            save_tcache(cache)

        time.sleep(0.5 if backend.name == "google" else 1.0)

    return results


# ── Main translate function ──
def translate_transcript(filepath: Path, cache: dict, backend) -> Path:
    content = filepath.read_text(encoding="utf-8")
    sep = "=" * 70
    parts = content.split(sep)
    if len(parts) < 3:
        return None

    header = parts[1]
    body = sep.join(parts[2:])

    header_meta = {}
    for line in header.strip().split("\n"):
        if ":" in line:
            key, val = line.split(":", 1)
            header_meta[key.strip()] = val.strip()

    # Split into paragraphs
    body_lines = body.split("\n")
    paragraphs = []
    current = []
    line_types = []

    for line in body_lines:
        stripped = line.strip()
        if not stripped:
            if current:
                paragraphs.append("\n".join(current))
                line_types.append(classify_line(current[0]))
                current = []
        else:
            current.append(stripped)
    if current:
        paragraphs.append("\n".join(current))
        line_types.append(classify_line(current[0]))

    log.info(f"  {len(paragraphs)} paragraphs, backend={backend.name}")

    # Translate
    translated = translate_paragraphs(paragraphs, cache, backend)

    # Build bilingual output
    bilingual_parts = []
    for i, (orig, trans) in enumerate(zip(paragraphs, translated)):
        lt = line_types[i] if i < len(line_types) else 'body'

        if lt == 'datetime':
            bilingual_parts.append(f"📅 {trans}")
        elif lt == 'participant':
            bilingual_parts.append(f"👤 {trans}")
        elif lt == 'header':
            bilingual_parts.append(f"━━ {trans} ━━")
        else:
            # For Google: trans is just Chinese. Show Chinese then English
            if backend.name == "google":
                bilingual_parts.append(f"{trans}\n  {orig}")
            else:
                # For DeepSeek: trans already contains both
                bilingual_parts.append(trans)


    # Build aligned pairs JSON
    pairs = []
    for i, (orig, trans) in enumerate(zip(paragraphs, translated)):
        pairs.append({"en": orig, "zh": trans})

    bilingual_data = {
        "meta": {
            "company": header_meta.get("Company", ""),
            "quarter": header_meta.get("Quarter", ""),
            "source": header_meta.get("Source", ""),
            "url": header_meta.get("URL", ""),
            "translated": datetime.now().isoformat(),
            "backend": backend.name,
        },
        "pairs": pairs,
    }

    out_path = filepath.parent / filepath.name.replace("_earnings_call", "_bilingual").replace(".txt", ".json")
    out_path.write_text(json.dumps(bilingual_data, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info(f"  Saved: {out_path.name} ({len(pairs)} pairs)")

    # Generate interleaved txt
    try:
        from make_interleaved import make_interleaved
        txt_path = make_interleaved(out_path)
        if txt_path:
            log.info(f"  Interleaved: {txt_path.name}")
    except Exception as e:
        log.warning(f"  Interleaved failed: {e}")

    return out_path


def main():
    parser = argparse.ArgumentParser(description="Transcript翻译器")
    parser.add_argument("--ticker", help="只翻译指定股票")
    parser.add_argument("--backend", choices=["deepseek", "google", "auto"], default="auto")
    args = parser.parse_args()

    cache = load_tcache()
    log.info(f"Cache: {len(cache)} entries")

    # Init backend
    config = load_config()
    backend = None

    if args.backend in ("deepseek", "auto"):
        api_key = config.get("deepseek_api_key", "")
        if api_key:
            ds = DeepSeekBackend(api_key, config.get("deepseek_base_url", "https://api.deepseek.com"), config.get("deepseek_model", "deepseek-v4-flash"))
            if ds.available():
                backend = ds
                log.info("Using DeepSeek backend")
            else:
                log.warning("DeepSeek unavailable, falling back to Google")

    if backend is None:
        backend = GoogleBackend()
        log.info("Using Google Translate backend")

    # Find companies
    companies = []
    if args.ticker:
        companies = [args.ticker.upper()]
    else:
        for d in sorted(TRANSCRIPTS_DIR.iterdir()):
            if d.is_dir() and list(d.glob("*earnings_call*.txt")):
                companies.append(d.name)

    log.info(f"Companies: {companies}")

    total = 0
    for ticker in companies:
        d = TRANSCRIPTS_DIR / ticker
        files = sorted(d.glob("*earnings_call*.txt"))
        log.info(f"\n{'─'*50}")
        log.info(f"{ticker}: {len(files)} file(s)")
        log.info(f"{'─'*50}")

        for f in files:
            log.info(f"  {f.name}")
            result = translate_transcript(f, cache, backend)
            if result:
                total += 1
            save_tcache(cache)

    save_tcache(cache)
    print(f"\n{'='*50}")
    print(f"翻译完成: {total} 个文件, {len(cache)} 条缓存")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()

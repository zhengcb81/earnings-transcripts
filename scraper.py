#!/usr/bin/env python3
"""
美股投资者电话会议纪要爬虫 (v2)
Strategy: 从Motley Fool公司页面提取transcript链接，直接下载

Sources:
  1. Motley Fool (免费) - 公司quote页 → 提取transcript URLs → 下载全文
  2. Financial Modeling Prep API (免费250次/天, 补充源)

Usage:
  python3 scraper.py                     # 抓取所有公司最新1期
  python3 scraper.py --ticker MSFT       # 只抓取指定公司
  python3 scraper.py --quarters 4        # 抓取最近4个季度
  python3 scraper.py --source fmp --api-key YOUR_KEY  # 使用FMP API
  python3 scraper.py --list              # 只列出可用的transcripts
"""

import os
import re
import sys
import json
import time
import logging
import argparse
import hashlib
from pathlib import Path
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
COMPANIES_FILE = BASE_DIR / "companies.txt"
TRANSCRIPTS_DIR = BASE_DIR / "transcripts"
LOGS_DIR = BASE_DIR / "logs"
CACHE_FILE = BASE_DIR / ".cache.json"

TRANSCRIPTS_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOGS_DIR / "scraper.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("scraper")

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
})


def load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}

def save_cache(cache: dict):
    CACHE_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False))


def load_companies(path: Path = COMPANIES_FILE, filter_ticker: str = None) -> list[dict]:
    companies = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 4:
            companies.append({
                "ticker": parts[0],
                "name_cn": parts[1],
                "name_en": parts[2],
                "exchange": parts[3].lower(),
            })
        elif len(parts) >= 3:
            # Auto-detect exchange
            companies.append({
                "ticker": parts[0],
                "name_cn": parts[1],
                "name_en": parts[2],
                "exchange": "auto",
            })
    if filter_ticker:
        companies = [c for c in companies if c["ticker"].upper() == filter_ticker.upper()]
    return companies


# ──────────────────────────────────────────────
# Source 1: Motley Fool (quote page approach)
# ──────────────────────────────────────────────
class MotleyFoolScraper:
    """Extract transcript URLs from Motley Fool company quote pages."""

    QUOTE_URL = "https://www.fool.com/quote/{exchange}/{ticker}/"
    EXCHANGES = ["nasdaq", "nyse"]

    def __init__(self, max_quarters: int = 1):
        self.max_quarters = max_quarters

    def find_transcript_urls(self, ticker: str, exchange: str = "auto") -> list[dict]:
        """
        Go to the company's Fool quote page and extract transcript URLs.
        Returns list of {url, title, quarter} sorted by date (newest first).
        """
        ticker_lower = ticker.lower()
        found_urls = []

        # Try exchanges
        exchanges_to_try = [exchange] if exchange != "auto" else self.EXCHANGES

        for ex in exchanges_to_try:
            url = self.QUOTE_URL.format(exchange=ex, ticker=ticker_lower)
            log.info(f"  Checking: {url}")

            try:
                resp = session.get(url, timeout=15)
            except Exception as e:
                log.error(f"  Request failed: {e}")
                continue

            if resp.status_code != 200:
                continue

            title_match = re.search(r'<title>(.*?)</title>', resp.text)
            page_title = title_match.group(1) if title_match else ""
            if "404 Error" in page_title or "Page Not Found" in page_title:
                continue

            # Extract transcript URLs from page source
            pattern = (
                r'https?://www\.fool\.com/earnings/call-transcripts/'
                r'(\d{4})/(\d{2})/(\d{2})/'
                r'([\w-]+)-' + re.escape(ticker_lower) + r'([\w-]*transcript/)'
            )
            matches = re.findall(pattern, resp.text)

            for m in matches:
                year, month, day = m[0], m[1], m[2]
                full_url = f"https://www.fool.com/earnings/call-transcripts/{year}/{month}/{day}/{m[3]}-{ticker_lower}{m[4]}"

                # Extract quarter from URL slug
                q_match = re.search(r'q(\d)-(\d{4})', m[3] + m[4], re.IGNORECASE)
                quarter = f"Q{q_match.group(1)} {q_match.group(2)}" if q_match else f"{year}-{month}"

                found_urls.append({
                    "url": full_url,
                    "quarter": quarter,
                    "date": f"{year}-{month}-{day}",
                    "ticker": ticker,
                    "source": "motley_fool",
                })

            if found_urls:
                break  # Found on this exchange, no need to try others

        # Deduplicate and sort by date (newest first)
        seen = set()
        unique = []
        for u in found_urls:
            if u["url"] not in seen:
                seen.add(u["url"])
                unique.append(u)
        unique.sort(key=lambda x: x["date"], reverse=True)

        log.info(f"  Found {len(unique)} transcript URL(s) for {ticker}")
        return unique[:self.max_quarters]

    def scrape_transcript(self, url: str) -> dict:
        """Download and parse a single transcript page."""
        log.info(f"  Downloading: {url}")

        try:
            resp = session.get(url, timeout=20)
            resp.raise_for_status()
        except Exception as e:
            log.error(f"  Download failed: {e}")
            return None

        soup = BeautifulSoup(resp.text, "lxml")

        # Title
        title_tag = soup.find("h1")
        title = title_tag.get_text(strip=True) if title_tag else "Unknown"

        # Content from <main>
        main = soup.find("main")
        if not main:
            log.warning(f"  No <main> found")
            return None

        skip_patterns = [
            "motley fool stock advisor", "click here to learn more",
            "advertisement", "get access now", "join the motley fool",
            "our ceo is handing", "need a quote from a motley fool analyst",
            "[email", "image source:",
        ]

        paragraphs = []
        for el in main.find_all(["p", "h2", "h3", "li"]):
            text = el.get_text(strip=True)
            if not text or len(text) < 5:
                continue
            if any(skip.lower() in text.lower() for skip in skip_patterns):
                continue
            if el.name in ("h2", "h3") and len(text) < 15:
                continue
            paragraphs.append(text)

        content = "\n\n".join(paragraphs)

        if len(content) < 500:
            log.warning(f"  Content too short ({len(content)} chars)")
            return None

        return {
            "title": title,
            "url": url,
            "content": content,
            "char_count": len(content),
            "scraped_at": datetime.now().isoformat(),
            "source": "motley_fool",
        }


# ──────────────────────────────────────────────
# Source 2: FMP API
# ──────────────────────────────────────────────
class FMPScraper:
    BASE_URL = "https://financialmodelingprep.com/api/v3"

    def __init__(self, api_key: str, max_quarters: int = 1):
        self.api_key = api_key
        self.max_quarters = max_quarters

    def find_and_scrape(self, ticker: str) -> list[dict]:
        log.info(f"  Fetching from FMP API for {ticker}")
        try:
            resp = session.get(
                f"{self.BASE_URL}/earning_call_transcript/{ticker}",
                params={"apikey": self.api_key}, timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.error(f"  FMP API error: {e}")
            return []

        if not data or (isinstance(data, dict) and "Error Message" in data):
            return []

        results = []
        for item in data[:self.max_quarters]:
            content = item.get("content", "")
            if not content or len(content) < 200:
                continue
            quarter = f"Q{item.get('quarter', '?')} {item.get('year', '?')}"
            results.append({
                "title": f"{ticker} {quarter} Earnings Call Transcript",
                "url": "FMP API",
                "content": content,
                "char_count": len(content),
                "scraped_at": datetime.now().isoformat(),
                "source": "fmp_api",
                "quarter": quarter,
                "date": item.get("date", ""),
            })
        log.info(f"  Got {len(results)} transcript(s) from FMP")
        return results


# ──────────────────────────────────────────────
# File output
# ──────────────────────────────────────────────
def save_transcript(company: dict, transcript: dict, output_dir: Path = TRANSCRIPTS_DIR):
    ticker = company["ticker"]
    company_dir = output_dir / ticker
    company_dir.mkdir(exist_ok=True)

    quarter = transcript.get("quarter", "unknown").replace(" ", "_")
    filename = f"{ticker}_{quarter}_earnings_call.txt"
    filepath = company_dir / filename

    header = f"""{'='*70}
Earnings Call Transcript
Company: {company['name_en']} ({company['name_cn']})
Ticker: {ticker}
Quarter: {transcript.get('quarter', 'N/A')}
Source: {transcript.get('source', 'N/A')}
URL: {transcript.get('url', 'N/A')}
Scraped: {transcript.get('scraped_at', 'N/A')}
Characters: {transcript.get('char_count', 'N/A')}
{'='*70}

"""
    filepath.write_text(header + transcript["content"], encoding="utf-8")
    log.info(f"  Saved: {filepath}")
    return filepath


def save_summary(companies: list, all_results: dict, output_dir: Path = TRANSCRIPTS_DIR):
    summary_path = output_dir / "summary.txt"
    lines = [
        "=" * 70,
        "Earnings Call Transcripts - Summary Report",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 70, "",
    ]
    for company in companies:
        ticker = company["ticker"]
        results = all_results.get(ticker, [])
        lines.append(f"{company['name_en']} ({company['name_cn']}) [{ticker}]")
        if not results:
            lines.append("  [NO TRANSCRIPTS FOUND]")
        else:
            for r in results:
                lines.append(f"  - {r.get('quarter', 'N/A')}: {r.get('title', 'N/A')}")
                lines.append(f"    Source: {r.get('source', 'N/A')} | Chars: {r.get('char_count', 'N/A')}")
                lines.append(f"    URL: {r.get('url', 'N/A')}")
        lines.append("")
    lines.append("=" * 70)
    summary_path.write_text("\n".join(lines), encoding="utf-8")
    log.info(f"Summary saved: {summary_path}")


# ──────────────────────────────────────────────
# Translation (auto after download)
# ──────────────────────────────────────────────
def load_translate_config():
    cfg_file = BASE_DIR / "config.json"
    if cfg_file.exists():
        try:
            return json.loads(cfg_file.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}

def translate_after_download(english_path: Path, skip: bool = False):
    """Translate a downloaded transcript to bilingual. Called right after save_transcript."""
    if skip:
        return None

    bilingual_path = english_path.parent / english_path.name.replace("_earnings_call", "_bilingual").replace(".txt", ".json")
    if bilingual_path.exists():
        log.info(f"  Bilingual exists, skipping: {bilingual_path.name}")
        return bilingual_path

    cfg = load_translate_config()
    api_key = cfg.get("deepseek_api_key", "")

    # Try DeepSeek first
    backend = None
    if api_key:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key, base_url=cfg.get("deepseek_base_url", "https://api.deepseek.com"))
            model = cfg.get("deepseek_model", "deepseek-v4-flash")
            # Quick test
            r = client.chat.completions.create(model=model, messages=[{"role":"user","content":"Say OK"}], max_tokens=5)
            backend = ("deepseek", client, model)
            log.info(f"  Translating with DeepSeek ({model})...")
        except Exception as e:
            log.warning(f"  DeepSeek unavailable ({e}), falling back to Google Translate")

    if backend is None:
        try:
            from translatepy import Translator
            backend = ("google", Translator(), None)
            log.info(f"  Translating with Google Translate...")
        except Exception as e:
            log.error(f"  No translation backend available: {e}")
            return None

    # Read english file
    content = english_path.read_text(encoding="utf-8")
    sep = "=" * 70
    parts = content.split(sep)
    if len(parts) < 3:
        return None
    header = parts[1]
    body = sep.join(parts[2:])

    # Parse header
    header_meta = {}
    for line in header.strip().split("\n"):
        if ":" in line:
            k, v = line.split(":", 1)
            header_meta[k.strip()] = v.strip()

    # Split into paragraphs
    body_lines = body.split("\n")
    paragraphs = []
    current = []
    for line in body_lines:
        stripped = line.strip()
        if not stripped:
            if current:
                paragraphs.append("\n".join(current))
                current = []
        else:
            current.append(stripped)
    if current:
        paragraphs.append("\n".join(current))

    # Translate each paragraph
    tcache = {}
    tcache_file = BASE_DIR / ".translate_cache.json"
    if tcache_file.exists():
        tcache = json.loads(tcache_file.read_text())

    def t_hash(t):
        return hashlib.md5(t.strip().encode()).hexdigest()[:16]

    translated_parts = []
    for i, p in enumerate(paragraphs):
        h = t_hash(p)
        if h in tcache:
            translated_parts.append(tcache[h])
            continue

        if len(p.strip()) < 5:
            translated_parts.append(p)
            continue

        try:
            if backend[0] == "deepseek":
                r = backend[1].chat.completions.create(
                    model=backend[2],
                    messages=[
                        {"role": "system", "content": "你是专业金融翻译。翻译为中文，保留数字/金额/百分比/ticker原样，人名保留英文，公司名/产品名保留英文。翻译后换行附原文。"},
                        {"role": "user", "content": f"翻译为中文，翻译后换行附原文：\n\n{p}"},
                    ],
                    max_tokens=4000, temperature=0.1,
                )
                result = r.choices[0].message.content.strip()
            else:
                result = backend[1].translate(p, "Chinese").result

            tcache[h] = result
            translated_parts.append(result)
        except Exception as e:
            log.warning(f"  Translate error [{i}]: {e}")
            translated_parts.append(p)

        if (i + 1) % 10 == 0:
            log.info(f"  Translation progress: {i+1}/{len(paragraphs)}")
            tcache_file.write_text(json.dumps(tcache, ensure_ascii=False, indent=1))

        time.sleep(0.3)

    # Save cache
    tcache_file.write_text(json.dumps(tcache, ensure_ascii=False, indent=1))

    # Build aligned pairs JSON
    pairs = []
    for i, (orig, trans) in enumerate(zip(paragraphs, translated_parts)):
        pairs.append({"en": orig, "zh": trans})

    bilingual_data = {
        "meta": {
            "company": header_meta.get('Company', ''),
            "quarter": header_meta.get('Quarter', ''),
            "source": header_meta.get('Source', ''),
            "url": header_meta.get('URL', ''),
            "translated": datetime.now().isoformat(),
            "backend": backend[0],
        },
        "pairs": pairs,
    }
    bilingual_path.write_text(json.dumps(bilingual_data, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info(f"  Bilingual saved: {bilingual_path.name} ({len(pairs)} pairs)")
    return bilingual_path


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="美股电话会议纪要爬虫 v2")
    parser.add_argument("--ticker", help="只抓取指定股票代码")
    parser.add_argument("--quarters", type=int, default=1, help="抓取最近N个季度")
    parser.add_argument("--source", choices=["fool", "fmp", "both"], default="fool")
    parser.add_argument("--api-key", help="FMP API key")
    parser.add_argument("--output", type=Path, default=TRANSCRIPTS_DIR)
    parser.add_argument("--list", action="store_true", help="只列出可用transcripts，不下载")
    parser.add_argument("--no-cache", action="store_true", help="忽略缓存")
    parser.add_argument("--no-translate", action="store_true", help="跳过翻译（默认下载后自动翻译）")
    args = parser.parse_args()

    companies = load_companies(filter_ticker=args.ticker)
    if not companies:
        log.error("No companies found in companies.txt")
        sys.exit(1)

    log.info(f"Loaded {len(companies)} companies: {[c['ticker'] for c in companies]}")

    cache = {} if args.no_cache else load_cache()
    all_results = {c["ticker"]: [] for c in companies}

    # ── Phase 1: Discover URLs ──
    if args.source in ("fool", "both"):
        fool = MotleyFoolScraper(max_quarters=args.quarters)
        log.info(f"\n{'─'*50}")
        log.info("Phase 1: Discovering transcript URLs from Motley Fool")
        log.info(f"{'─'*50}")

        url_index = {}  # ticker -> [url_info]
        for company in companies:
            ticker = company["ticker"]
            urls = fool.find_transcript_urls(ticker, company["exchange"])
            url_index[ticker] = urls

        if args.list:
            print(f"\n{'='*70}")
            print("AVAILABLE TRANSCRIPTS")
            print(f"{'='*70}")
            for company in companies:
                t = company["ticker"]
                urls = url_index.get(t, [])
                print(f"\n{company['name_en']} ({company['name_cn']}) [{t}]")
                if not urls:
                    print("  [NONE FOUND]")
                else:
                    for u in urls:
                        print(f"  {u['quarter']:10s}  {u['date']}  {u['url']}")
            print(f"\n{'='*70}")
            return

        # ── Phase 2: Download ──
        log.info(f"\n{'─'*50}")
        log.info("Phase 2: Downloading transcripts")
        log.info(f"{'─'*50}")

        for company in companies:
            ticker = company["ticker"]
            urls = url_index.get(ticker, [])
            if not urls:
                log.warning(f"  No transcripts for {ticker}")
                continue

            for u in urls:
                url = u["url"]
                if url in cache and not args.no_cache:
                    log.info(f"  Skipping (cached): {u['quarter']}")
                    all_results[ticker].append(cache[url])
                    continue

                transcript = fool.scrape_transcript(url)
                if transcript:
                    transcript["quarter"] = u["quarter"]
                    filepath = save_transcript(company, transcript, args.output)
                    translate_after_download(filepath, skip=args.no_translate)
                    transcript["local_file"] = str(filepath)
                    all_results[ticker].append(transcript)
                    cache[url] = {
                        "title": transcript["title"],
                        "quarter": transcript["quarter"],
                        "source": transcript["source"],
                        "char_count": transcript["char_count"],
                        "local_file": str(filepath),
                    }
                time.sleep(2)

    # ── Phase 3: FMP fallback ──
    if args.source in ("fmp", "both") and args.api_key:
        fmp = FMPScraper(args.api_key, max_quarters=args.quarters)
        for company in companies:
            ticker = company["ticker"]
            if len(all_results[ticker]) >= args.quarters:
                continue
            try:
                fmp_results = fmp.find_and_scrape(ticker)
                for r in fmp_results:
                    filepath = save_transcript(company, r, args.output)
                    translate_after_download(filepath, skip=args.no_translate)
                    r["local_file"] = str(filepath)
                all_results[ticker].extend(fmp_results)
            except Exception as e:
                log.error(f"  FMP error for {ticker}: {e}")

    # ── Save ──
    save_cache(cache)
    save_summary(companies, all_results, args.output)

    # ── Report ──
    print(f"\n{'='*70}")
    print("SCRAPING COMPLETE")
    print(f"{'='*70}")
    total = 0
    for company in companies:
        t = company["ticker"]
        results = all_results[t]
        total += len(results)
        status = f"{len(results)} transcript(s)" if results else "NO TRANSCRIPTS"
        print(f"  {t:6s} ({company['name_en']:30s}): {status}")
    print(f"{'─'*70}")
    print(f"Total: {total} transcript(s) from {len(companies)} companies")
    print(f"Output: {args.output}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()

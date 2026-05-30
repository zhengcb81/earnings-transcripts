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

from common import (
    load_config, get_path, load_companies, setup_logging,
    FileNaming, JsonCache, LineClassifier, split_paragraphs,
    text_hash, parse_transcript_header, extract_body,
    TranslatorFactory, translate_paragraphs,
)


# ──────────────────────────────────────────────
# Source 1: Motley Fool (quote page approach)
# ──────────────────────────────────────────────
class MotleyFoolScraper:
    """Extract transcript URLs from Motley Fool company quote pages."""

    def __init__(self, cfg: dict, max_quarters: int = 1):
        self.max_quarters = max_quarters
        fool_cfg = cfg.get("fool", {})
        self.quote_url = fool_cfg.get("quote_url", "https://www.fool.com/quote/{exchange}/{ticker}/")
        self.exchanges = fool_cfg.get("exchanges", ["nasdaq", "nyse"])
        self.request_timeout = fool_cfg.get("request_timeout", 15)
        self.download_timeout = fool_cfg.get("download_timeout", 20)
        self.min_content_length = fool_cfg.get("min_content_length", 500)
        self.min_paragraph_length = fool_cfg.get("min_paragraph_length", 5)
        self.min_header_length = fool_cfg.get("min_header_length", 15)
        self.skip_patterns = fool_cfg.get("skip_patterns", [
            "motley fool stock advisor", "click here to learn more",
            "advertisement", "get access now", "join the motley fool",
            "our ceo is handing", "need a quote from a motley fool analyst",
            "[email", "image source:",
        ])

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": fool_cfg.get("user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": fool_cfg.get("accept_language", "en-US,en;q=0.9"),
        })

    def find_transcript_urls(self, ticker: str, exchange: str = "auto") -> list[dict]:
        """
        Go to the company's Fool quote page and extract transcript URLs.
        Returns list of {url, title, quarter} sorted by date (newest first).
        """
        ticker_lower = ticker.lower()
        found_urls = []

        # Try exchanges
        exchanges_to_try = [exchange] if exchange != "auto" else self.exchanges

        for ex in exchanges_to_try:
            url = self.quote_url.format(exchange=ex, ticker=ticker_lower)
            log.info(f"  Checking: {url}")

            try:
                resp = self.session.get(url, timeout=self.request_timeout)
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
            resp = self.session.get(url, timeout=self.download_timeout)
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

        paragraphs = []
        for el in main.find_all(["p", "h2", "h3", "li"]):
            text = el.get_text(strip=True)
            if not text or len(text) < self.min_paragraph_length:
                continue
            if any(skip.lower() in text.lower() for skip in self.skip_patterns):
                continue
            if el.name in ("h2", "h3") and len(text) < self.min_header_length:
                continue
            paragraphs.append(text)

        content = "\n\n".join(paragraphs)

        if len(content) < self.min_content_length:
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
    def __init__(self, cfg: dict, api_key: str, max_quarters: int = 1):
        fmp_cfg = cfg.get("fmp", {})
        self.base_url = fmp_cfg.get("base_url", "https://financialmodelingprep.com/api/v3")
        self.endpoint = fmp_cfg.get("endpoint", "/earning_call_transcript/{ticker}")
        self.request_timeout = fmp_cfg.get("request_timeout", 15)
        self.min_content_length = fmp_cfg.get("min_content_length", 200)
        self.api_key = api_key
        self.max_quarters = max_quarters
        self.session = requests.Session()

    def find_and_scrape(self, ticker: str) -> list[dict]:
        log.info(f"  Fetching from FMP API for {ticker}")
        try:
            url = self.base_url + self.endpoint.format(ticker=ticker)
            resp = self.session.get(
                url,
                params={"apikey": self.api_key}, timeout=self.request_timeout,
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
            if not content or len(content) < self.min_content_length:
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
def save_transcript(cfg: dict, fn: FileNaming, company: dict, transcript: dict, output_dir: Path = None):
    ticker = company["ticker"]
    quarter = transcript.get("quarter", "unknown")

    if output_dir:
        company_dir = output_dir / ticker
        company_dir.mkdir(exist_ok=True)
        q = quarter.replace(" ", "_")
        filepath = company_dir / f"{ticker}_{q}{fn.english_suffix}{fn.english_ext}"
    else:
        filepath = fn.english_path(ticker, quarter)
        filepath.parent.mkdir(parents=True, exist_ok=True)

    sep_char = cfg.get("format", {}).get("separator_char", "=")
    sep_width = cfg.get("format", {}).get("separator_width", 70)
    sep = sep_char * sep_width

    header = f"""{sep}
Earnings Call Transcript
Company: {company['name_en']} ({company['name_cn']})
Ticker: {ticker}
Quarter: {transcript.get('quarter', 'N/A')}
Source: {transcript.get('source', 'N/A')}
URL: {transcript.get('url', 'N/A')}
Scraped: {transcript.get('scraped_at', 'N/A')}
Characters: {transcript.get('char_count', 'N/A')}
{sep}

"""
    filepath.write_text(header + transcript["content"], encoding="utf-8")
    log.info(f"  Saved: {filepath}")
    return filepath


def save_summary(cfg: dict, companies: list, all_results: dict, output_dir: Path = None):
    transcripts_dir = output_dir or get_path(cfg, "transcripts_dir")
    summary_path = transcripts_dir / "summary.txt"
    sep_char = cfg.get("format", {}).get("separator_char", "=")
    sep_width = cfg.get("format", {}).get("separator_width", 70)
    sep = sep_char * sep_width

    lines = [
        sep,
        "Earnings Call Transcripts - Summary Report",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        sep, "",
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
    lines.append(sep)
    summary_path.write_text("\n".join(lines), encoding="utf-8")
    log.info(f"Summary saved: {summary_path}")


# ──────────────────────────────────────────────
# Translation (auto after download)
# ──────────────────────────────────────────────
def translate_after_download(cfg: dict, fn: FileNaming, english_path: Path, skip: bool = False):
    """Translate a downloaded transcript to bilingual. Called right after save_transcript."""
    if skip:
        return None

    bilingual_path = fn.english_to_bilingual(english_path)
    if bilingual_path.exists():
        log.info(f"  Bilingual exists, skipping: {bilingual_path.name}")
        return bilingual_path

    # Read english file
    content = english_path.read_text(encoding="utf-8")
    header_meta = parse_transcript_header(content)
    body = extract_body(content)

    # Split into paragraphs
    paragraphs = split_paragraphs(body)

    # Create translator and cache
    translator = TranslatorFactory.create(cfg, "auto")
    tcache_path = get_path(cfg, "translate_cache")
    tcache = JsonCache(tcache_path)

    log.info(f"  Translating with {translator.name}...")

    # Translate each paragraph
    translated_parts = translate_paragraphs(paragraphs, tcache, translator, cfg, log)

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
            "backend": translator.name,
        },
        "pairs": pairs,
    }
    bilingual_path.write_text(json.dumps(bilingual_data, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info(f"  Bilingual saved: {bilingual_path.name} ({len(pairs)} pairs)")

    # Generate interleaved txt
    try:
        from make_interleaved import make_interleaved
        txt_path = make_interleaved(cfg, bilingual_path)
        if txt_path:
            log.info(f"  Interleaved saved: {txt_path.name}")
    except Exception as e:
        log.warning(f"  Interleaved generation failed: {e}")

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
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--list", action="store_true", help="只列出可用transcripts，不下载")
    parser.add_argument("--no-cache", action="store_true", help="忽略缓存")
    parser.add_argument("--no-translate", action="store_true", help="跳过翻译（默认下载后自动翻译）")
    args = parser.parse_args()

    # Load config
    cfg = load_config()
    fn = FileNaming(cfg)
    transcripts_dir = get_path(cfg, "transcripts_dir")
    logs_dir = get_path(cfg, "logs_dir")
    transcripts_dir.mkdir(exist_ok=True)
    logs_dir.mkdir(exist_ok=True)

    global log
    log = setup_logging(cfg, str(logs_dir / "scraper.log"))
    log = logging.getLogger("scraper")

    output_dir = args.output or transcripts_dir

    companies = load_companies(cfg, filter_ticker=args.ticker)
    if not companies:
        log.error("No companies found in companies.txt")
        sys.exit(1)

    log.info(f"Loaded {len(companies)} companies: {[c['ticker'] for c in companies]}")

    # Cache
    if args.no_cache:
        cache = JsonCache(Path(os.devnull))  # dummy
        cache._data = {}
    else:
        cache_path = get_path(cfg, "cache_file")
        cache = JsonCache(cache_path)

    all_results = {c["ticker"]: [] for c in companies}

    # ── Phase 1: Discover URLs ──
    if args.source in ("fool", "both"):
        fool = MotleyFoolScraper(cfg, max_quarters=args.quarters)
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
        sleep_between = cfg.get("fool", {}).get("sleep_between_downloads", 2)
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
                    filepath = save_transcript(cfg, fn, company, transcript, output_dir)
                    translate_after_download(cfg, fn, filepath, skip=args.no_translate)
                    transcript["local_file"] = str(filepath)
                    all_results[ticker].append(transcript)
                    cache.set(url, {
                        "title": transcript["title"],
                        "quarter": transcript["quarter"],
                        "source": transcript["source"],
                        "char_count": transcript["char_count"],
                        "local_file": str(filepath),
                    })
                time.sleep(sleep_between)

    # ── Phase 3: FMP fallback ──
    if args.source in ("fmp", "both") and args.api_key:
        fmp = FMPScraper(cfg, args.api_key, max_quarters=args.quarters)
        for company in companies:
            ticker = company["ticker"]
            if len(all_results[ticker]) >= args.quarters:
                continue
            try:
                fmp_results = fmp.find_and_scrape(ticker)
                for r in fmp_results:
                    filepath = save_transcript(cfg, fn, company, r, output_dir)
                    translate_after_download(cfg, fn, filepath, skip=args.no_translate)
                    r["local_file"] = str(filepath)
                all_results[ticker].extend(fmp_results)
            except Exception as e:
                log.error(f"  FMP error for {ticker}: {e}")

    # ── Save ──
    if not args.no_cache:
        cache.save()
    save_summary(cfg, companies, all_results, output_dir)

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
    print(f"Output: {output_dir}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()

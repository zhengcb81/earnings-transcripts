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

from common import (
    load_config, get_path, setup_logging,
    FileNaming, JsonCache, LineClassifier,
    split_paragraphs, text_hash,
    parse_transcript_header, extract_body,
    TranslatorFactory, translate_paragraphs,
)


# ── Main translate function ──
def translate_transcript(cfg: dict, fn: FileNaming, filepath: Path, cache: JsonCache, backend) -> Path:
    content = filepath.read_text(encoding="utf-8")
    header_meta = parse_transcript_header(content)
    body = extract_body(content)

    # Split into paragraphs
    paragraphs = split_paragraphs(body)

    # Classify lines for formatting
    classifier = LineClassifier(cfg)
    line_types = [classifier.classify(p.split("\n")[0]) for p in paragraphs]

    log.info(f"  {len(paragraphs)} paragraphs, backend={backend.name}")

    # Translate using common translate_paragraphs
    translated = translate_paragraphs(paragraphs, cache, backend, cfg, log)

    # Build bilingual output with emoji prefixes
    bilingual_parts = []
    for i, (orig, trans) in enumerate(zip(paragraphs, translated)):
        lt = line_types[i] if i < len(line_types) else 'body'

        if lt == 'datetime':
            bilingual_parts.append(f"\U0001f4c5 {trans}")
        elif lt == 'participant':
            bilingual_parts.append(f"\U0001f464 {trans}")
        elif lt == 'header':
            bilingual_parts.append(f"\u2501\u2501 {trans} \u2501\u2501")
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

    out_path = fn.english_to_bilingual(filepath)
    out_path.write_text(json.dumps(bilingual_data, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info(f"  Saved: {out_path.name} ({len(pairs)} pairs)")

    # Generate interleaved txt
    try:
        from make_interleaved import make_interleaved
        txt_path = make_interleaved(cfg, out_path)
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

    # Load config
    cfg = load_config()
    fn = FileNaming(cfg)
    transcripts_dir = get_path(cfg, "transcripts_dir")

    global log
    log = setup_logging(cfg)
    log = logging.getLogger("translate")

    # Cache
    cache_path = get_path(cfg, "translate_cache")
    cache = JsonCache(cache_path)
    log.info(f"Cache: {len(cache)} entries")

    # Init backend
    backend = TranslatorFactory.create(cfg, args.backend)
    log.info(f"Using {backend.name} backend")

    # Find companies
    companies = []
    if args.ticker:
        companies = [args.ticker.upper()]
    else:
        files = fn.find_english_files()
        companies = sorted(set(f.parent.name for f in files))

    log.info(f"Companies: {companies}")

    total = 0
    for ticker in companies:
        files = fn.find_english_files(ticker)
        log.info(f"\n{'─'*50}")
        log.info(f"{ticker}: {len(files)} file(s)")
        log.info(f"{'─'*50}")

        for f in files:
            log.info(f"  {f.name}")
            result = translate_transcript(cfg, fn, f, cache, backend)
            if result:
                total += 1
            cache.save()

    cache.save()
    print(f"\n{'='*50}")
    print(f"翻译完成: {total} 个文件, {len(cache)} 条缓存")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()

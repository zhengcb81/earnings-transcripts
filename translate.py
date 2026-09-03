#!/usr/bin/env python3
"""
Transcript翻译器: 多后端LLM翻译 (MiniMax → MiMo → DeepSeek fallback), Google Translate兜底
Usage: python3 translate.py              # 翻译所有
       python3 translate.py --ticker MSFT # 只翻译指定
       python3 translate.py --backend google # 强制用Google
"""

import json, logging, argparse, sys
from pathlib import Path

from common import (
    load_config, get_path, setup_logging,
    FileNaming, JsonCache,
    split_paragraphs, text_hash,
    parse_transcript_header, extract_body,
    TranslatorFactory, translate_paragraphs,
    build_bilingual_data, SingleInstanceLock,
)

log = logging.getLogger(__name__)


# ── Main translate function ──
def translate_transcript(cfg: dict, fn: FileNaming, filepath: Path, cache: JsonCache, backend) -> Path:
    content = filepath.read_text(encoding="utf-8")
    header_meta = parse_transcript_header(content)
    body = extract_body(content)

    # Split into paragraphs
    paragraphs = split_paragraphs(body)

    log.info(f"  {len(paragraphs)} paragraphs, backend={backend.name}")

    # Translate using common translate_paragraphs
    translated = translate_paragraphs(paragraphs, cache, backend, cfg, log)

    # Build and save bilingual JSON
    bilingual_data = build_bilingual_data(header_meta, paragraphs, translated, backend.name)
    out_path = fn.english_to_bilingual(filepath)
    out_path.write_text(json.dumps(bilingual_data, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info(f"  Saved: {out_path.name} ({len(bilingual_data['pairs'])} pairs)")

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
    parser.add_argument("--backend", choices=["minimax", "mimo", "deepseek", "google", "auto"], default="auto")
    parser.add_argument("--force", action="store_true", help="重译已存在的中英对照文件")
    args = parser.parse_args()

    # Load config
    cfg = load_config()
    fn = FileNaming(cfg)
    transcripts_dir = get_path(cfg, "transcripts_dir")

    setup_logging(cfg)

    # 单实例锁：与 scraper 共用同一把锁，避免并发写同一批文件
    lock_file = get_path(cfg, "lock_file")
    ok, holder = SingleInstanceLock(lock_file).acquire()
    if not ok:
        msg = (
            f"已有 scraper/translate 实例在运行：{holder}\n"
            f"并发运行会互相覆盖文件、重复消耗翻译额度。\n"
            f"确认没有其它实例后删除锁文件重试：{lock_file}"
        )
        log.error(msg)
        print(f"\n{msg}\n", file=sys.stderr)
        sys.exit(2)

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
    skipped = 0
    for ticker in companies:
        files = fn.find_english_files(ticker)
        log.info(f"\n{'─'*50}")
        log.info(f"{ticker}: {len(files)} file(s)")
        log.info(f"{'─'*50}")

        for f in files:
            if not args.force and fn.english_to_bilingual(f).exists():
                log.info(f"  Skip (already translated): {f.name}")
                skipped += 1
                continue
            log.info(f"  {f.name}")
            result = translate_transcript(cfg, fn, f, cache, backend)
            if result:
                total += 1
            cache.save()

    cache.save()
    print(f"\n{'='*50}")
    print(f"翻译完成: 新译 {total} 个, 跳过 {skipped} 个（已有译文）, {len(cache)} 条缓存")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()

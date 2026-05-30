#!/usr/bin/env python3
"""
从中英对照JSON生成夹排TXT：一段英文、一段中文交替显示
Usage:
  python3 make_interleaved.py              # 处理所有已有的bilingual JSON
  python3 make_interleaved.py --ticker MSFT # 只处理指定公司
"""

import json, re, argparse
from pathlib import Path

from common import load_config, get_path, FileNaming, LineClassifier


def make_interleaved(cfg: dict, json_path: Path) -> Path:
    """Generate interleaved txt from bilingual JSON."""
    data = json.loads(json_path.read_text(encoding="utf-8"))
    meta = data.get("meta", {})
    pairs = data.get("pairs", [])
    if not pairs:
        return None

    # Config values
    fmt = cfg.get("format", {})
    sep_char = fmt.get("separator_char", "=")
    sep_width = fmt.get("separator_width", 70)
    dt_sep_char = fmt.get("datetime_separator_char", "\u2500")
    dt_sep_width = fmt.get("datetime_separator_width", 50)
    labels = fmt.get("language_labels", {})
    en_label = labels.get("en", "[EN]")
    zh_label = labels.get("zh", "[中]")

    classifier = LineClassifier(cfg)

    lines = []
    sep = sep_char * sep_width

    # Header
    lines.append(sep)
    lines.append("电话会议纪要 · 中英夹排版")
    lines.append(f"Company: {meta.get('company', '')}")
    lines.append(f"Quarter: {meta.get('quarter', '')}")
    lines.append(f"Source: {meta.get('source', '')}")
    lines.append(f"URL: {meta.get('url', '')}")
    lines.append(f"Translated: {meta.get('translated', '')}")
    lines.append(f"Backend: {meta.get('backend', '')}")
    lines.append(f"Paragraphs: {len(pairs)}")
    lines.append(sep)
    lines.append("")

    # Interleaved paragraphs
    for i, pair in enumerate(pairs):
        en = pair.get("en", "").strip()
        zh = pair.get("zh", "").strip()
        if not en:
            continue

        ptype = classifier.classify(en)
        num = f"[{i+1:3d}]"

        if ptype == 'datetime':
            lines.append(f"{dt_sep_char*dt_sep_width}")
            lines.append(f"  \U0001f4c5 {en}")
            lines.append(f"  \U0001f4c5 {zh}")
            lines.append(f"{dt_sep_char*dt_sep_width}")
        elif ptype == 'participant':
            lines.append(f"  \U0001f464 {en}")
            lines.append(f"     {zh}")
        elif ptype == 'header':
            lines.append(f"")
            lines.append(f"  \u2501\u2501 {en} \u2501\u2501")
            lines.append(f"  \u2501\u2501 {zh} \u2501\u2501")
        else:
            lines.append(f"  {num} {en_label} {en}")
            lines.append(f"       {zh_label} {zh}")

        lines.append("")

    # Footer
    lines.append(sep)
    lines.append(f"共 {len(pairs)} 段中英对照")
    lines.append(sep)

    # Save using FileNaming
    fn = FileNaming(cfg)
    out_path = fn.bilingual_to_interleaved(json_path)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def main():
    parser = argparse.ArgumentParser(description="生成中英夹排TXT")
    parser.add_argument("--ticker", help="只处理指定股票")
    args = parser.parse_args()

    cfg = load_config()
    fn = FileNaming(cfg)
    transcripts_dir = get_path(cfg, "transcripts_dir")

    if args.ticker:
        bilingual_files = fn.find_bilingual_files(args.ticker.upper())
        dirs = sorted(set(f.parent for f in bilingual_files))
    else:
        dirs = sorted(d for d in transcripts_dir.iterdir() if d.is_dir())

    total = 0
    for d in dirs:
        jsons = sorted(d.glob(f"*{fn.bilingual_suffix}{fn.bilingual_ext}"))
        if not jsons:
            continue
        print(f"{d.name}: {len(jsons)} file(s)")
        for jf in jsons:
            out = make_interleaved(cfg, jf)
            if out:
                print(f"  \u2713 {out.name} ({out.stat().st_size:,} bytes)")
                total += 1

    print(f"\n生成完成: {total} 个中英夹排TXT")


if __name__ == "__main__":
    main()

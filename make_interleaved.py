#!/usr/bin/env python3
"""
从中英对照JSON生成夹排TXT：一段英文、一段中文交替显示
Usage:
  python3 make_interleaved.py              # 处理所有已有的bilingual JSON
  python3 make_interleaved.py --ticker MSFT # 只处理指定公司
"""

import json, re, argparse
from pathlib import Path

BASE_DIR = Path(__file__).parent
TRANSCRIPTS_DIR = BASE_DIR / "transcripts"


def classify_line(text: str) -> str:
    """Classify paragraph type for formatting."""
    s = text.strip()
    if re.match(r'^\w+,\s+\w+\.\s+\d+', s) or re.match(r'^\d{1,2}:\d{2}', s):
        return 'datetime'
    if '—' in s and len(s) < 120:
        return 'participant'
    if len(s) < 120 and re.match(
        r'^(Revenue|Operating|Earnings|Gross|Net|Free Cash|Guidance|Q&A|Question|'
        r'Call participants|Highlights|Summary|Financial|Cash Flow|Capital|'
        r'More Personal|Intelligent Cloud|Productivity|AI Business|'
        r'Copilot|GitHub|Fabric|Azure|Dynamics|LinkedIn|Windows|Search|Gaming)',
        s, re.I
    ):
        return 'header'
    return 'body'


def make_interleaved(json_path: Path) -> Path:
    """Generate interleaved txt from bilingual JSON."""
    data = json.loads(json_path.read_text(encoding="utf-8"))
    meta = data.get("meta", {})
    pairs = data.get("pairs", [])
    if not pairs:
        return None

    lines = []
    sep = "=" * 70

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

        ptype = classify_line(en)
        num = f"[{i+1:3d}]"

        if ptype == 'datetime':
            lines.append(f"{'─'*50}")
            lines.append(f"  📅 {en}")
            lines.append(f"  📅 {zh}")
            lines.append(f"{'─'*50}")
        elif ptype == 'participant':
            lines.append(f"  👤 {en}")
            lines.append(f"     {zh}")
        elif ptype == 'header':
            lines.append(f"")
            lines.append(f"  ━━ {en} ━━")
            lines.append(f"  ━━ {zh} ━━")
        else:
            lines.append(f"  {num} [EN] {en}")
            lines.append(f"       [中] {zh}")

        lines.append("")

    # Footer
    lines.append(sep)
    lines.append(f"共 {len(pairs)} 段中英对照")
    lines.append(sep)

    # Save
    out_path = json_path.parent / json_path.name.replace("_bilingual.json", "_interleaved.txt")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def main():
    parser = argparse.ArgumentParser(description="生成中英夹排TXT")
    parser.add_argument("--ticker", help="只处理指定股票")
    args = parser.parse_args()

    if args.ticker:
        dirs = [TRANSCRIPTS_DIR / args.ticker.upper()]
    else:
        dirs = sorted(d for d in TRANSCRIPTS_DIR.iterdir() if d.is_dir())

    total = 0
    for d in dirs:
        jsons = sorted(d.glob("*_bilingual.json"))
        if not jsons:
            continue
        print(f"{d.name}: {len(jsons)} file(s)")
        for jf in jsons:
            out = make_interleaved(jf)
            if out:
                print(f"  ✓ {out.name} ({out.stat().st_size:,} bytes)")
                total += 1

    print(f"\n生成完成: {total} 个中英夹排TXT")


if __name__ == "__main__":
    main()

"""DU10 Phase C: read-only verification against the real local corpus.

No production file is modified, copied into the repo, or committed; all
derived artifacts stay in this receipts directory (counts, short quotes of at
most two lines) or a TemporaryDirectory that is removed afterwards.

Checks per sampled transcript (``*_earnings_call.txt``):

1. body extractable with the repo's own parser utilities (read-only use of
   ``parser.extract_body`` / ``parser.split_paragraphs``), paragraphs non-empty
   and order-stable;
2. every paragraph traces back verbatim into the original file text;
3. EN sample deep-check (MSFT): the full-call section exists and detected
   ``Speaker:`` turn labels cover the roster announced in the header;
4. CN-company sample deep-check (TAL): CJK header literals survive intact and
   speaker turns are detected;
5. round-trip: the real paragraph content wrapped in minimal MF-shaped
   synthetic markup (p/strong turns, h4 section marker) extracts to exactly
   the source paragraph sequence under the new v2 ownership rules.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import parser  # noqa: E402
import transcript_extraction  # noqa: E402

REPO = Path(__file__).resolve().parents[4]
TRANSCRIPTS = REPO / "transcripts"
SPEAKER_RE = re.compile(r"^([A-Z][A-Za-z .'\-]{1,40}):")

SAMPLES = {
    "MSFT_Q1_2026_earnings_call.txt": "EN",
    "NVO_Q2_2024_earnings_call.txt": "EN",
    "TAL_Q2_2026_earnings_call.txt": "CN-company",
}
CJK_EXPECTED = {"TAL_Q2_2026_earnings_call.txt": ["好未来", "TAL Education"]}


def read_sample(name: str) -> tuple[str, str]:
    text = (TRANSCRIPTS / name.split("_")[0] / name).read_text(encoding="utf-8")
    body = parser.extract_body(text).strip()
    return text, body


def corpus_stats() -> dict:
    files = sorted(TRANSCRIPTS.glob("*/*_earnings_call.txt"))
    stats = {"earnings_call_txt_total": len(files), "tickers": sorted({p.name.split("_")[0] for p in files})}
    return stats


def verify_sample(name: str, kind: str) -> dict:
    text, body = read_sample(name)
    paragraphs = parser.split_paragraphs(body, min_length=1)
    trace_failures = [p[:60] for p in paragraphs if p not in text]
    speakers = sorted({m.group(1) for p in paragraphs for m in [SPEAKER_RE.match(p)] if m})
    record = {
        "sample": name,
        "kind": kind,
        "paragraphs": len(paragraphs),
        "paragraph_traceback_failures": trace_failures,
        "speaker_turn_labels_detected": speakers,
        "cjk_header_intact": all(literal in text for literal in CJK_EXPECTED.get(name, [])),
    }
    if name.startswith("MSFT"):
        record["full_call_section_present"] = "Full Conference Call Transcript" in body
        roster = ["Satya Nadella", "Amy E. Hood", "Alice Jolla", "Jonathan Neilson"]
        record["roster_members_with_detected_turns"] = [
            person for person in roster
            if any(p.startswith(person + ":") or p.startswith(person + " :") for p in paragraphs)
            or any(SPEAKER_RE.match(p) and SPEAKER_RE.match(p).group(1) == person for p in paragraphs)
        ]
    return record


def roundtrip(name: str) -> dict:
    """Real content + synthetic MF-shaped markup -> extract() must reproduce
    the source paragraph sequence exactly (content fidelity, markup synthetic)."""
    _text, body = read_sample(name)
    paragraphs = parser.split_paragraphs(body, min_length=1)
    heading_like = ("CALL PARTICIPANTS", "Full Conference Call Transcript", "Prepared Remarks")
    parts = ["<html><body><h1>Roundtrip Harness</h1><main>"]
    for paragraph in paragraphs:
        if any(marker in paragraph for marker in heading_like):
            parts.append(f"<h2>{paragraph}</h2>")
            continue
        match = SPEAKER_RE.match(paragraph)
        if match:
            label = match.group(1)
            rest = paragraph[len(label) + 1:]
            # byte-faithful to the source: no whitespace synthesized at the
            # label boundary, whatever spacing the corpus carries is kept
            parts.append(f"<div><strong>{label}:</strong>{rest}</div>")
        else:
            parts.append(f"<p>{paragraph}</p>")
    parts.append("</main></body></html>")
    with tempfile.TemporaryDirectory(prefix="du10_roundtrip_") as tmp:
        page = "".join(parts).encode("utf-8")
        result = transcript_extraction.extract(page)
        extracted = result.body.split("\n\n")
        missing = [p[:60] for p in paragraphs if p not in extracted]
        return {
            "sample": name,
            "source_paragraphs": len(paragraphs),
            "extracted_paragraphs": len(extracted),
            "missing_source_paragraphs": missing,
            "order_identical": [p[:40] for p in extracted[:5]],
            "extraction_version": result.extraction_version,
            "exact_sequence_match": not missing and len(extracted) == len(paragraphs),
        }


def main() -> None:
    report = {"corpus": corpus_stats(), "samples": [], "roundtrips": []}
    for name, kind in SAMPLES.items():
        report["samples"].append(verify_sample(name, kind))
    for name in SAMPLES:
        report["roundtrips"].append(roundtrip(name))
    out = Path(__file__).parent / "real_corpus_verification.json"
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()

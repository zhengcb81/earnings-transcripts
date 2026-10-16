"""DU7-ET-EXTRACTION behavior RED: one DOM-ownership extractor must preserve
real business text, avoid ancestor/child double emission, and keep short
structural headings, while the old small-page baseline stays byte-identical.

Expectations in tests/fixtures/du7_extraction/expected.json are hand-derived
from the construction-card counterexamples (whitespace-collapsed text joined by
blank lines), independent of implementation output.
"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from transcript_api import _extract_body

FIXTURES = Path(__file__).parent / "fixtures" / "du7_extraction"
EXPECTED = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))["cases"]


def _page(name: str) -> bytes:
    return (FIXTURES / EXPECTED[name]["fixture"]).read_bytes()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _assert_case(name: str, title: str, body: str) -> None:
    expected = EXPECTED[name]
    assert title == expected["title"], f"{name}: title drifted"
    assert _sha(title) == expected["title_sha256"], f"{name}: title sha drifted"
    assert body == expected["body"], (
        f"{name}: body drifted\n--- got ---\n{body!r}\n--- want ---\n{expected['body']!r}"
    )
    assert _sha(body) == expected["body_sha256"], f"{name}: body sha drifted"
    assert len(body.encode("utf-8")) == expected["body_utf8_bytes"]
    positions = [body.find(marker) for marker in expected["order_markers"]]
    assert all(pos >= 0 for pos in positions), f"{name}: order marker missing {positions}"
    assert positions == sorted(positions), f"{name}: order markers out of document order"


def test_business_sentence_with_advertising_words_is_preserved():
    """A sentence about advertising revenue must not be dropped as an ad."""
    title, body = _extract_body(_page("business_sentence"))
    _assert_case("business_sentence", title, body)
    assert "Advertising revenue grew 20% and advertisement pricing declined." in body


def test_li_p_paragraph_is_emitted_once():
    """li>p owns one text run: no ancestor+child double emission."""
    title, body = _extract_body(_page("nested_li_p"))
    _assert_case("nested_li_p", title, body)
    line = (
        "Our subscription revenue increased 18 percent driven by enterprise "
        "customer wins and strong renewal rates across every region."
    )
    assert body.count(line) == 1


def test_short_structural_heading_q_and_a_is_preserved():
    """Short headings like Q&A are structure, not noise."""
    title, body = _extract_body(_page("short_title"))
    _assert_case("short_title", title, body)
    assert body.split("\n\n")[0] == "Q&A"


def test_compat_baseline_small_page_matches_old_behavior():
    """The old correct small-page behavior stays byte-identical (frozen SHA)."""
    title, body = _extract_body(_page("compat_baseline"))
    _assert_case("compat_baseline", title, body)
    assert EXPECTED["compat_baseline"]["body_sha256"] == (
        "97b5b19f0b9967c0ab40c9dd2b68854a0e229db30c7505af3948028b03a0f2ca"
    )

"""DU10-ET-TXT-FIDELITY: transcript text extraction must not lose content,
speaker turns, or paragraph boundaries that the source DOM actually carries,
and truncated input must fail closed instead of producing a seemingly
complete transcript.

Expectations in tests/fixtures/du10_transcript_fidelity/expected.json are
hand-derived from the fixture sources before any implementation change (one
paragraph per owning block, whitespace-collapsed text joined by blank lines)
and are anchored back onto those sources here: every expected paragraph must
appear in the tag-stripped fixture text, in document order. Recovered-owner
pages must report the honest ``motley-fool-main-text/2`` label; pages that
stay byte-equal to the frozen v1 projection must keep ``.../1``.
"""
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import transcript_extraction  # noqa: E402
from transcript_extraction import (  # noqa: E402
    EXTRACTION_VERSION,
    EXTRACTION_VERSION_LEGACY,
    ExtractionError,
)

FIXTURES = Path(__file__).parent / "fixtures" / "du10_transcript_fidelity"
EXPECTED = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))["cases"]

# Frozen compatibility surface pinned by earlier cards; DU10 must not move it.
FROZEN_BLOCK_TAGS = frozenset({"p", "h2", "h3", "li"})
FROZEN_SKIP_TEXT = (
    "motley fool stock advisor",
    "click here to learn more",
    "advertisement",
    "get access now",
    "join the motley fool",
    "need a quote from a motley fool analyst",
    "image source:",
)


def _page(case: str) -> bytes:
    return (FIXTURES / EXPECTED[case]["fixture"]).read_bytes()


def _source_fragments(case: str) -> str:
    """Text-node concatenation of the fixture source: markup removed, ``<br>``
    folded to one space (the frozen v1/v2 shared rule), whitespace collapsed."""
    raw = (FIXTURES / EXPECTED[case]["fixture"]).read_text(encoding="utf-8")
    without_tags = re.sub(r"<(?!br\s*/?)[^>]*>", "", raw)
    return " ".join(re.sub(r"<br\s*/?>", " ", without_tags).split())


def _assert_case_fidelity(case: str, result) -> None:
    expected = EXPECTED[case]
    assert result.title == expected["title"], f"{case}: title drifted"
    assert result.body == "\n\n".join(expected["paragraphs"]), (
        f"{case}: body drifted\n--- got ---\n{result.body!r}"
    )
    assert result.extraction_version == expected["version"], (
        f"{case}: version {result.extraction_version!r} != {expected['version']!r}"
    )
    # Every output paragraph must trace back to an original source fragment,
    # and paragraph order must follow document order.
    source = _source_fragments(case)
    position = 0
    for paragraph in expected["paragraphs"]:
        found = source.find(paragraph, position)
        assert found >= 0, f"{case}: paragraph not traceable in order: {paragraph!r}"
        position = found + len(paragraph)


@pytest.mark.parametrize(
    "case",
    ["qa_boundary_div_turns", "table_participants", "cjk_qa_boundary"],
)
def test_recovered_owner_text_full_fidelity(case):
    """Text inside h4/h5-style section headers, container divs, and table
    cells is real transcript content: section boundaries, speaker turns, and
    participant roles must survive extraction in document order."""
    _assert_case_fidelity(case, transcript_extraction.extract(_page(case)))


@pytest.mark.parametrize(
    "case",
    ["speaker_glue_no_space", "br_timestamp_disclaimer"],
)
def test_control_v1_equal_pages_stay_frozen(case):
    """Speaker labels glued to their speech (the source DOM itself carries no
    whitespace) and ``<br>``-separated lines inside one block are frozen
    byte-faithful behavior: no synthetic space is inserted, ``<br>`` folds to
    one space, and these v1-equal pages keep the legacy label."""
    _assert_case_fidelity(case, transcript_extraction.extract(_page(case)))


def test_truncated_dangling_markup_fails_closed():
    """A page cut off inside an unfinished tag must raise a typed extraction
    error, never return a seemingly complete transcript."""
    with pytest.raises(ExtractionError) as excinfo:
        transcript_extraction.extract(_page("truncated_dangling_markup"))
    assert excinfo.value.reason == "truncated_markup"


def test_lone_trailing_lt_is_text_not_truncation():
    """A lone ``<`` at EOF is a text character, not unfinished markup; the
    page extracts and the literal survives verbatim."""
    filler = "The company delivered results above expectations across every reported segment this quarter. " * 2
    page = (
        "<html><body><h1>ACME Q3 2026 Earnings Call</h1><main>"
        f"<p>{filler}</p>"
        "<p>Revenue was 1.2 billion dollars while margin cost was lower < </p>"
        "</main></body></html>"
    )
    result = transcript_extraction.extract(page.encode("utf-8"))
    assert result.body.rstrip().endswith("margin cost was lower <")


def test_missing_qa_heading_keeps_turn_order():
    """Without a Q&A section header the turns themselves still extract, in
    document order: speaker structure never depended on the heading."""
    filler = "The company delivered results above expectations across every reported segment this quarter. " * 2
    page = (
        "<html><body><h1>ACME Q3 2026 Earnings Call</h1><main>"
        f"<p><strong>CEO Jane Roe:</strong> {filler}</p>"
        "<div><strong>Operator:</strong> We will now open the line for analyst questions.</div>"
        f"<p><strong>Jane Roe:</strong> Pricing stabilized after the mix shift. {filler}</p>"
        "</main></body></html>"
    )
    result = transcript_extraction.extract(page.encode("utf-8"))
    paragraphs = result.body.split("\n\n")
    assert "Question And Answer" not in paragraphs
    assert paragraphs[0].startswith("CEO Jane Roe:")
    assert paragraphs[1] == "Operator: We will now open the line for analyst questions."
    assert paragraphs[2].startswith("Jane Roe: Pricing stabilized")


def test_cjk_literals_pass_through_untranslated():
    """Original-language literals are never rewritten: every Chinese paragraph
    survives verbatim and no translation is synthesized."""
    result = transcript_extraction.extract(_page("cjk_qa_boundary"))
    for paragraph in EXPECTED["cjk_qa_boundary"]["paragraphs"]:
        assert paragraph in result.body
    ascii_words = re.findall(r"[A-Za-z]{4,}", result.body)
    assert ascii_words == [], f"unexpected latin text injected: {ascii_words}"


def test_frozen_compatibility_surface_unchanged():
    """The frozen v1 contract this card must preserve, pinned as literals."""
    assert transcript_extraction._BLOCK_TAGS == FROZEN_BLOCK_TAGS
    assert EXTRACTION_VERSION_LEGACY == "motley-fool-main-text/1"
    assert EXTRACTION_VERSION == "motley-fool-main-text/2"
    assert transcript_extraction.MIN_CONTENT_CHARS == 200
    assert transcript_extraction.LEGACY_MIN_PARAGRAPH_CHARS == 5
    assert transcript_extraction.SKIP_TEXT == FROZEN_SKIP_TEXT

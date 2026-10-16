"""DU9-ET-LEGACY-COMPARISON: bounded legacy-compatibility decision over one DOM.

Behavior RED (node A) targets the production cost pathology: ``extract`` must
not replay the frozen ``_legacy_v1_reference`` (per-block ``get_text`` rewalks
of every nested subtree plus materialization of the whole duplicated legacy
body); text-piece visits must stay within a linear bound of DOM nodes at
200/1200/2400-deep nested blocks, with many-sibling blocks as the linear
control shape.

Correctness expectations are anchored from two independent sides, never from
the new comparator against itself:

* frozen v1 = ``transcript_extraction._legacy_v1_reference`` (function body
  untouched, used only as the controlled baseline), and
* current v2 = the ``ExtractionResult`` actually returned by ``extract``.

The expected legacy label is ``frozen_v1 == (v2.title, v2.body)``; the actual
label is ``extraction_version == motley-fool-main-text/1``. Both the equal and
the unequal side are asserted across a hand-derived semantic matrix, the
frozen fixture pages, seeded bounded generated samples, and adjacent-scale
deep/sibling shapes.
"""
from __future__ import annotations

import hashlib
import inspect
import random
import sys
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import legacy_projection_comparison  # noqa: E402
import transcript_extraction  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "du9_legacy_comparison"

FILLER = (
    "Operator: Welcome to the quarterly earnings call. Management will discuss "
    "business operations, customer demand, overseas capacity expansion and "
    "the revenue outlook with financial analysts. We will then take questions "
    "from investors."
)
FACT = (
    "Subscription revenue grew 18% to $1.2 billion and advertising revenue "
    "increased 20% across every region we serve today."
)


def _main_page(main_html: str, *, title: str = "ACME Q2 2026 Earnings Call",
               extra_body: str = "") -> bytes:
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>'
        f"<h1>{title}</h1>{main_html}{extra_body}</body></html>"
    ).encode("utf-8")


def _filler_main(extra: str = "") -> str:
    return f"<main><p>{FILLER}</p>{extra}</main>"


def _nested_p_page(depth: int) -> bytes:
    # Every level carries its own text run: each nested block's legacy
    # get_text re-collects all ancestor runs, so the frozen replay visits
    # theta(depth^2) text pieces while the DOM only has theta(depth) nodes.
    inner = "<p>The innermost paragraph ends the deep nesting chain.</p>"
    for level in range(depth):
        inner = f"<p>Level {level} wraps the chain. {inner}</p>"
    return _main_page(_filler_main(inner))


def _sibling_page(count: int) -> bytes:
    paragraphs = "".join(
        f"<p>Paragraph number {i:04d} carries stable unique scaling text.</p>"
        for i in range(count)
    )
    return _main_page(_filler_main(paragraphs))


def _node_count(page: bytes) -> int:
    return sum(1 for _ in BeautifulSoup(page, "html.parser").descendants)


def _actual_is_legacy(page: bytes) -> bool:
    result = transcript_extraction.extract(page)
    return result.extraction_version == transcript_extraction.EXTRACTION_VERSION_LEGACY


def _expected_is_legacy(page: bytes) -> bool:
    """Independent oracle: frozen v1 projection vs the actual v2 result."""
    result = transcript_extraction.extract(page)
    frozen = transcript_extraction._legacy_v1_reference(
        BeautifulSoup(page, "html.parser")
    )
    return frozen == (result.title, result.body)


def _assert_oracle_agreement(page: bytes) -> bool:
    expected = _expected_is_legacy(page)
    actual = _actual_is_legacy(page)
    assert actual == expected, (
        f"version label disagrees with frozen oracle: actual={actual} "
        f"expected={expected}"
    )
    return expected


def _install_visit_counter(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    import bs4.element as bs4_element

    counters = {"get_text_calls": 0, "piece_yields": 0, "returned_chars": 0}
    original_all_strings = bs4_element.Tag._all_strings
    original_get_text = bs4_element.Tag.get_text

    def counting_all_strings(self, *args, **kwargs):
        def generator():
            for piece in original_all_strings(self, *args, **kwargs):
                counters["piece_yields"] += 1
                yield piece

        return generator()

    def counting_get_text(self, *args, **kwargs):
        counters["get_text_calls"] += 1
        text = original_get_text(self, *args, **kwargs)
        counters["returned_chars"] += len(text)
        return text

    monkeypatch.setattr(bs4_element.Tag, "_all_strings", counting_all_strings)
    monkeypatch.setattr(bs4_element.Tag, "get_text", counting_get_text)
    return counters


# --------------------------------------------------------------------------
# Node A behavior RED: the production compatibility check must stop replaying
# the frozen legacy reference and stop per-block get_text rewalks.
# --------------------------------------------------------------------------


def test_production_extract_never_replays_frozen_legacy_reference(monkeypatch):
    def explode(soup):
        raise AssertionError(
            "production compatibility check must not replay _legacy_v1_reference"
        )

    monkeypatch.setattr(transcript_extraction, "_legacy_v1_reference", explode)
    result = transcript_extraction.extract(_nested_p_page(400))
    assert result.extraction_version == transcript_extraction.EXTRACTION_VERSION


def test_production_extract_never_calls_get_text(monkeypatch):
    counters = _install_visit_counter(monkeypatch)
    transcript_extraction.extract(_nested_p_page(64))
    assert counters["get_text_calls"] == 0, (
        "legacy comparison must not call bs4 get_text in production"
    )


@pytest.mark.parametrize("depth", [200, 1200, 2400])
def test_text_piece_visits_stay_linear_on_deep_nested_blocks(depth, monkeypatch):
    page = _nested_p_page(depth)
    nodes = _node_count(page)
    assert nodes >= depth
    counters = _install_visit_counter(monkeypatch)
    result = transcript_extraction.extract(page)
    assert result.extraction_version == transcript_extraction.EXTRACTION_VERSION
    assert counters["piece_yields"] <= 8 * nodes, (
        f"depth={depth}: text-piece visits {counters['piece_yields']} exceed "
        f"the linear bound 8*nodes={8 * nodes} (quadratic legacy replay)"
    )
    assert counters["returned_chars"] <= 64 * nodes, (
        f"depth={depth}: materialized legacy text {counters['returned_chars']} "
        f"chars exceeds 64*nodes={64 * nodes}"
    )


def test_text_piece_visits_stay_linear_on_many_sibling_blocks(monkeypatch):
    """Linear control shape: sibling blocks already walk disjoint subtrees."""
    page = _sibling_page(2000)
    nodes = _node_count(page)
    counters = _install_visit_counter(monkeypatch)
    transcript_extraction.extract(page)
    assert counters["piece_yields"] <= 8 * nodes, (
        f"sibling visits {counters['piece_yields']} exceed 8*nodes={8 * nodes}"
    )
    assert counters["returned_chars"] <= 64 * nodes


def test_single_dom_observation_with_comparison_active(monkeypatch):
    import transcript_extraction as module

    original_parser = module.BeautifulSoup
    calls = {"count": 0}

    def counting_parser(*args, **kwargs):
        calls["count"] += 1
        return original_parser(*args, **kwargs)

    monkeypatch.setattr(module, "BeautifulSoup", counting_parser)
    transcript_extraction.extract(_nested_p_page(2400))
    assert calls["count"] == 1


# --------------------------------------------------------------------------
# Correctness anchors: frozen v1 and actual v2 generate every expectation.
# --------------------------------------------------------------------------

_MATRIX_PAGES: dict[str, bytes] = {
    "plain_flat": _main_page(_filler_main(
        f"<p>{FACT}</p><p>Management raised the full year cash flow outlook.</p>"
        "<h2>Prepared Remarks</h2>"
        "<p>Analyst: Can you discuss the advertising pricing trend this quarter?</p>"
    )),
    "inline_numeric": _main_page(_filler_main(
        "<p>Revenue was $1.<strong>2</strong> billion this quarter while the "
        "operating margin held above the guided range.</p>"
    )),
    "comment_split": _main_page(_filler_main(
        "<p>alpha<!--c-->beta continues with stable disclosure language here.</p>"
    )),
    "br_lines": _main_page(_filler_main(
        "<p>Line one<br>line two<br>line three of the prepared section.</p>"
    )),
    "nbsp_middle": _main_page(_filler_main(
        "<p>We retain a&nbsp;space between these words in the transcript.</p>"
    )),
    "entity_pct": _main_page(_filler_main(
        "<p>Margin was <em>-</em>2.5<strong>%</strong> versus the prior period "
        "after adjusting for the one-time items.</p>"
    )),
    "cjk_numeric": _main_page(_filler_main(
        "<p>金额为<span>1.</span><b>2</b><i>亿</i>元，同比增速保持稳定。</p>"
    )),
    "nested_p": _main_page(_filler_main(
        "<p>Outer paragraph starts here.<p>The nested inner paragraph repeats "
        "business context the legacy projection would duplicate again.</p>Outer "
        "paragraph resumes after the child block.</p>"
    )),
    "nested_li_p": _main_page(_filler_main(
        "<ul><li>Prefix <em>one</em><p>Nested body paragraph with extra "
        "detail.</p> Tail two.</li><li>Other entry with more detail text.</li></ul>"
    )),
    "hidden_p": _main_page(_filler_main(
        "<p hidden>Hidden paragraph text that only the legacy flat projection "
        "collects from the document.</p>"
    )),
    "ad_class_container": _main_page(_filler_main(
        '<div class="ad-container"><p>Buy our promotion before the window '
        "closes today.</p></div>"
    )),
    "business_near_word": _main_page(_filler_main(
        f'<div class="advertising-revenue"><p>{FACT}</p></div>'
    )),
    "skip_text_paragraph": _main_page(_filler_main(
        "<p>Advertisement: this sponsored placement note is legacy-skipped "
        "while the new owner keeps every business sentence intact.</p>"
    )),
    "duplicate_same_text": _main_page(_filler_main(
        f"<p>{FACT}</p><p>{FACT}</p>"
    )),
    "multi_h1": _main_page(_filler_main(f"<p>{FACT}</p>"),
                           extra_body="<h1>Second decoy heading outside main</h1>"),
    "h1_in_main": _main_page(
        f"<main><p>{FILLER}</p><h1>Decorative heading inside main content</h1>"
        f"<p>{FACT}</p></main>"
    ),
    "multi_main": _main_page(_filler_main(f"<p>{FACT}</p>"),
                             extra_body="<main><p>Second main decoy content.</p></main>"),
    "script_in_p": _main_page(_filler_main(
        f"<p>keep me <script>var track = 1;</script> tail {FACT}</p>"
    )),
    "template_p": _main_page(_filler_main(
        "<p>before <template><p>templated block paragraph text</p></template> "
        "after this marker.</p>"
    )),
    "cdata": _main_page(_filler_main(
        "<p>alpha <![CDATA[ raw payload data here ]]> beta of the section.</p>"
    )),
    "title_double_space": _main_page(
        _filler_main(f"<p>{FACT}</p>"), title="ACME  Q2 Call"
    ),
    "malformed_unclosed": _main_page(_filler_main(
        f"<p>{FACT}<p>Second malformed paragraph also missing its closing tag"
    )),
    "skip_phrase_across_comment": _main_page(_filler_main(
        "<p>click<!--disc--> here to learn more about the quarter results.</p>"
    )),
    "skip_word_split_inline": _main_page(_filler_main(
        "<p>adver<strong>tisement</strong> pricing held steady through the "
        "final stretch of the reporting period.</p>"
    )),
}


@pytest.mark.parametrize("name", sorted(_MATRIX_PAGES))
def test_matrix_version_label_matches_frozen_oracle(name):
    expected = _assert_oracle_agreement(_MATRIX_PAGES[name])
    assert isinstance(expected, bool)


def test_matrix_covers_equal_and_unequal_sides():
    outcomes = {
        name: _expected_is_legacy(page) for name, page in sorted(_MATRIX_PAGES.items())
    }
    assert True in outcomes.values(), f"no equal-side sample: {outcomes}"
    assert False in outcomes.values(), f"no unequal-side sample: {outcomes}"


@pytest.mark.parametrize("fixture_name, expect_legacy", [
    ("equal_flat", True),
    ("unequal_semantics", False),
])
def test_fixture_pages_match_frozen_oracle(fixture_name, expect_legacy):
    page = (FIXTURES / f"{fixture_name}.html").read_bytes()
    assert _assert_oracle_agreement(page) is expect_legacy


@pytest.mark.parametrize("depth", [200, 1200, 2400])
def test_deep_nested_pages_match_frozen_oracle(depth):
    assert _assert_oracle_agreement(_nested_p_page(depth)) is False


def test_many_sibling_pages_stay_legacy_at_scale():
    """Equal side at scale: a flat page must not be labeled /2 for speed."""
    assert _assert_oracle_agreement(_sibling_page(600)) is True


# --------------------------------------------------------------------------
# Seeded bounded generated samples: equal and unequal sides, small pages.
# --------------------------------------------------------------------------

def _fragment_builders():
    return [
        lambda rng: f"<p>Generated plain paragraph {rng.randint(0, 9999)} "
                    "with stable business narrative inside.</p>",
        lambda rng: f"<p>Revenue was $1.<strong>{rng.randint(2, 9)}</strong> "
                    "billion during the reported period.</p>",
        lambda rng: f"<p>alpha{rng.randint(0, 99)}<!--split-->beta continues "
                    "the generated sentence here.</p>",
        lambda rng: f"<p>Line A{rng.randint(0, 99)}<br>Line B of the same "
                    "generated paragraph block.</p>",
        lambda rng: f"<p>We retain a&nbsp;space token {rng.randint(0, 99)} in "
                    "this generated line.</p>",
        lambda rng: f"<p>金额为<span>{rng.randint(1, 9)}.</span><b>"
                    f"{rng.randint(1, 9)}</b><i>亿</i>元的规模。</p>",
        lambda rng: (
            "<p>Outer generated block starts.<p>Nested generated child "
            f"repeats content {rng.randint(0, 9999)} that duplicates.</p>Outer "
            "generated block resumes.</p>"
        ),
        lambda rng: (
            "<ul><li>Prefix <em>item</em><p>Nested generated list body "
            f"{rng.randint(0, 999)}.</p> Tail.</li><li>Second generated "
            "entry with more detail.</li></ul>"
        ),
        lambda rng: f"<p hidden>Hidden generated paragraph {rng.randint(0, 9999)} "
                    "collected only by the flat legacy projection.</p>",
        lambda rng: f'<div class="ad-container"><p>Buy promotion generated '
                    f"slot {rng.randint(0, 9999)} now.</p></div>",
        lambda rng: f'<div class="advertising-revenue"><p>{FACT}</p></div>',
        lambda rng: "<p>Advertisement: generated sponsored note that the frozen "
                    "legacy substring filter drops entirely.</p>",
        lambda rng: f"<p>{FACT}</p><p>{FACT}</p>",
        lambda rng: f"<p>keep <script>var g = {rng.randint(0, 99)};</script> tail "
                    "generated marker text.</p>",
        lambda rng: "<p>before <template><p>generated templated paragraph</p>"
                    "</template> after marker.</p>",
        lambda rng: "<p>alpha <![CDATA[ generated raw payload ]]> beta marker.</p>",
        lambda rng: "<p>Q&amp;A",
        lambda rng: f"<p>Margin was <em>-</em>2.{rng.randint(0, 9)}<strong>%</strong> "
                    "after adjustments this period.</p>",
    ]


def _generated_page(rng: random.Random) -> bytes:
    title = rng.choice([
        "ACME Q2 2026 Earnings Call",
        "ACME  Q2 Call",
        "ACME Q<strong>4</strong> Call",
    ])
    parts = [f"<p>{FILLER}</p>"]
    for builder in rng.sample(_fragment_builders(), rng.randint(2, 6)):
        parts.append(builder(rng))
    main_html = "<main>" + "".join(parts) + "</main>"
    extra = ""
    if rng.random() < 0.3:
        extra = "<main><p>Decoy second main paragraph for the sample.</p></main>"
    if rng.random() < 0.3:
        extra += "<h1>Decoy second heading for the sample</h1>"
    return _main_page(main_html, title=title, extra_body=extra)


@pytest.mark.parametrize("iteration", range(120))
def test_generated_samples_match_frozen_oracle(iteration):
    rng = random.Random(20261016 + iteration)
    _assert_oracle_agreement(_generated_page(rng))


def test_generated_sample_set_covers_both_outcomes():
    outcomes = set()
    for iteration in range(120):
        rng = random.Random(20261016 + iteration)
        outcomes.add(_expected_is_legacy(_generated_page(rng)))
    assert True in outcomes, "generated samples never hit the equal side"
    assert False in outcomes, "generated samples never hit the unequal side"


# --------------------------------------------------------------------------
# Node B: comparator-internal bounds (algorithmic counts, never wall-clock).
# --------------------------------------------------------------------------


def _compare_with_stats(
    page: bytes, stats: legacy_projection_comparison.ComparisonStats
) -> tuple[bool, bool]:
    """Drive the comparator directly with fresh stats; returns
    ``(decision, oracle_agreement_expected)``."""
    result = transcript_extraction.extract(page)
    soup = BeautifulSoup(page, "html.parser")
    matched = legacy_projection_comparison.legacy_projection_matches(
        soup,
        result.title,
        result.body,
        block_tags=transcript_extraction._BLOCK_TAGS,
        skip_text=transcript_extraction.SKIP_TEXT,
        min_paragraph_chars=transcript_extraction.LEGACY_MIN_PARAGRAPH_CHARS,
        min_content_chars=transcript_extraction.MIN_CONTENT_CHARS,
        stats=stats,
    )
    frozen = transcript_extraction._legacy_v1_reference(
        BeautifulSoup(page, "html.parser")
    )
    expected = frozen == (result.title, result.body)
    assert matched is expected
    return matched, expected


def test_frozen_legacy_reference_source_is_untouched():
    """The frozen v1 baseline stays byte-identical to the released function."""
    source = inspect.getsource(transcript_extraction._legacy_v1_reference)
    normalized = "\n".join(source.splitlines())
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    assert digest == (
        "85d1d45c52f14b39768120184e034945b0e4e8887ce622b4a2069e1b27bfad2b"
    )


@pytest.mark.parametrize("name", sorted(_MATRIX_PAGES))
def test_block_iteration_order_matches_find_all(name):
    soup = BeautifulSoup(_MATRIX_PAGES[name], "html.parser")
    main = soup.find("main")
    expected = main.find_all(["p", "h2", "h3", "li"])
    mine = list(legacy_projection_comparison._iter_legacy_blocks(
        main, transcript_extraction._BLOCK_TAGS
    ))
    assert [id(block) for block in mine] == [id(block) for block in expected]


@pytest.mark.parametrize("depth", [200, 1200, 2400])
def test_comparator_visits_stay_linear_on_adjacent_deep_scales(depth, monkeypatch):
    page = _nested_p_page(depth)
    nodes = _node_count(page)
    result = transcript_extraction.extract(page)
    counters = _install_visit_counter(monkeypatch)
    stats = legacy_projection_comparison.ComparisonStats()
    matched = legacy_projection_comparison.legacy_projection_matches(
        BeautifulSoup(page, "html.parser"),
        result.title,
        result.body,
        block_tags=transcript_extraction._BLOCK_TAGS,
        skip_text=transcript_extraction.SKIP_TEXT,
        min_paragraph_chars=transcript_extraction.LEGACY_MIN_PARAGRAPH_CHARS,
        min_content_chars=transcript_extraction.MIN_CONTENT_CHARS,
        stats=stats,
    )
    assert matched is False
    # no bs4 text iteration happens anywhere in the comparison itself
    assert counters["get_text_calls"] == 0
    assert counters["piece_yields"] == 0
    assert stats.nodes_visited <= 10 * nodes, (
        f"depth={depth}: comparator visited {stats.nodes_visited} nodes for "
        f"{nodes} DOM nodes"
    )
    assert stats.max_intermediate_chars <= 512
    assert stats.chars_compared <= len(result.title) + len(result.body) + 64


def test_comparator_adjacent_scale_growth_is_linear():
    visited = {}
    for depth in (200, 1200, 2400):
        page = _nested_p_page(depth)
        stats = legacy_projection_comparison.ComparisonStats()
        _compare_with_stats(page, stats)
        visited[depth] = stats.nodes_visited
    assert visited[1200] <= 8 * visited[200]
    assert visited[2400] <= 2.5 * visited[1200]


def test_deep_comparison_never_materializes_the_legacy_body():
    page = _nested_p_page(1200)
    soup = BeautifulSoup(page, "html.parser")
    frozen = transcript_extraction._legacy_v1_reference(soup)
    result = transcript_extraction.extract(page)
    legacy_body_len = len(frozen[1])
    # the frozen path materializes a duplicated legacy body of this size
    assert legacy_body_len > 100_000
    stats = legacy_projection_comparison.ComparisonStats()
    matched = legacy_projection_comparison.legacy_projection_matches(
        BeautifulSoup(page, "html.parser"),
        result.title,
        result.body,
        block_tags=transcript_extraction._BLOCK_TAGS,
        skip_text=transcript_extraction.SKIP_TEXT,
        min_paragraph_chars=transcript_extraction.LEGACY_MIN_PARAGRAPH_CHARS,
        min_content_chars=transcript_extraction.MIN_CONTENT_CHARS,
        stats=stats,
    )
    assert matched is (frozen == (result.title, result.body)) is False
    assert stats.max_intermediate_chars <= 512, (
        "comparison must never buffer the duplicated legacy body"
    )
    assert stats.nodes_visited <= 10 * _node_count(page)


def test_comparator_confirms_every_paragraph_on_equal_pages():
    for page in (
        (FIXTURES / "equal_flat.html").read_bytes(),
        _sibling_page(600),
    ):
        stats = legacy_projection_comparison.ComparisonStats()
        matched, expected = _compare_with_stats(page, stats)
        assert matched is True
        assert expected is True
        result = transcript_extraction.extract(page)
        assert stats.full_confirmations == len(result.body.split("\n\n"))
        assert stats.nodes_visited <= 10 * _node_count(page)
        assert stats.max_intermediate_chars <= 512
        assert stats.chars_compared <= len(result.title) + len(result.body) + 64

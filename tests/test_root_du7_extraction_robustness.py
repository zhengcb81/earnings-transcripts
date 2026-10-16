"""ROOT extraction regressions: metadata ambiguity and depth-safe DOM ownership."""
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import transcript_extraction as extraction

FILLER = ("Operator: Welcome to the quarterly earnings call. Management will discuss "
          "business operations, customer demand, overseas capacity expansion and "
          "the revenue outlook with financial analysts. We will then take questions from investors.")
FACT = "Subscription revenue grew 18% to $1.2 billion and advertising revenue increased 20%."


def page(fragment):
    return ('<html><head><meta charset="utf-8"></head><body><h1>ACME Earnings Call</h1>'
            '<main><p>' + FILLER + '</p>' + fragment + '</main></body></html>').encode()


@pytest.mark.parametrize("attributes", [
    'data-adoption-rate="0.8"',
    'data-adjusted-growth="18"',
    'class="subscription-revenue"',
    'class="advertising-revenue"',
    'class="cookie-revenue"',
    'class="banner-revenue"',
    'data-ad="false"',
])
def test_business_metadata_is_not_advertisement_evidence(attributes):
    result = extraction.extract(page('<div ' + attributes + '><p>' + FACT + '</p></div>'))
    assert result.body == FILLER + "\n\n" + FACT
    assert result.extraction_version == extraction.EXTRACTION_VERSION_LEGACY


@pytest.mark.parametrize("hidden", [False, True])
def test_deep_inline_dom_does_not_depend_on_python_call_stack(hidden):
    original_limit = sys.getrecursionlimit()
    fragment = ('<p' + (' hidden' if hidden else '') + '>' + '<span>' * 1200
                + FACT + '</span>' * 1200 + '</p>')
    result = extraction.extract(page(fragment))
    assert result.body == (FILLER if hidden else FILLER + "\n\n" + FACT)
    assert sys.getrecursionlimit() == original_limit


@pytest.mark.parametrize("attributes", [
    'class="ad-container"',
    'data-ad-slot="synthetic-slot"',
    'data-ad-client="synthetic-client"',
    'role="navigation"',
])
def test_actual_advertisement_or_ui_structure_is_still_excluded(attributes):
    result = extraction.extract(page('<div ' + attributes + '><p>Buy our promotion.</p></div>'))
    assert result.body == FILLER
    assert result.extraction_version == extraction.EXTRACTION_VERSION


def test_genuine_repeated_dialogue_keeps_both_distinct_locations():
    result = extraction.extract(page('<p>' + FACT + '</p><p>' + FACT + '</p>'))
    assert result.body.count(FACT) == 2
    assert result.extraction_version == extraction.EXTRACTION_VERSION_LEGACY


def test_dom_filter_work_grows_with_nodes_without_a_second_observation():
    actual_filter = extraction._is_droppable
    actual_parser = extraction.BeautifulSoup
    with patch.object(extraction, '_is_droppable', wraps=actual_filter) as filtered, \
            patch.object(extraction, 'BeautifulSoup', wraps=actual_parser) as parsed:
        extraction.extract(page('<p>' + '<span>' * 2400 + FACT + '</span>' * 2400 + '</p>'))
    assert parsed.call_count == 1
    assert filtered.call_count <= 2404


@pytest.mark.parametrize("fragment, expected, changed", [
    ('Revenue was $1.<strong>2</strong> billion.', 'Revenue was $1.2 billion.', True),
    ('Margin was <em>-</em>2.5<strong>%</strong>.', 'Margin was -2.5%.', True),
    ('金额为<span>1.</span><b>2</b><i>亿</i>元。', '金额为1.2亿元。', True),
    ('We won inter<strong>national</strong> customers.', 'We won international customers.', True),
    ('We have <strong>strong</strong> demand.', 'We have strong demand.', False),
    ('<span>two</span> <em>words</em>', 'two words', False),
    ('<span>no</span><em>space</em>', 'nospace', True),
    ('<span>  spaces  </span>\n\t remain', 'spaces remain', False),
    ('Line one<br>line two', 'Line one line two', False),
    ('Line one<br/>line two', 'Line one line two', False),
    ('$<em>1.2</em><b> billion</b>', '$1.2 billion', True),
    ('We retain a&nbsp;<strong>space</strong>.', 'We retain a space.', True),
])
def test_inline_text_preserves_actual_dom_adjacency(fragment, expected, changed):
    result = extraction.extract(page('<p>' + fragment + '</p>'))
    assert result.body == FILLER + "\n\n" + expected
    assert result.extraction_version == (
        extraction.EXTRACTION_VERSION if changed else extraction.EXTRACTION_VERSION_LEGACY)


def test_title_inline_adjacency_has_honest_new_version():
    raw = page('<p>' + FACT + '</p>').replace(b'ACME Earnings Call', b'ACME Q<strong>4</strong> Call')
    result = extraction.extract(raw)
    assert result.title == 'ACME Q4 Call'
    assert result.extraction_version == extraction.EXTRACTION_VERSION


def test_block_boundaries_are_not_lost_when_inline_runs_join():
    result = extraction.extract(page('<p>Part <span>one</span>.</p><p>Part <span>two</span>.</p>'))
    assert result.body == FILLER + "\n\nPart one.\n\nPart two."


def test_list_parent_runs_keep_order_around_a_distinct_child_block():
    result = extraction.extract(page('<ul><li>Prefix <em>one</em><p>Nested <strong>body</strong>.</p> '
                                     'Tail two.</li><li>Other entry.</li></ul>'))
    assert result.body == FILLER + "\n\nPrefix one\n\nNested body.\n\nTail two.\n\nOther entry."

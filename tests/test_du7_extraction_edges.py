"""DU7-ET-EXTRACTION structure/edge RED: DOM ownership and node-scope filtering.

Covers construction-card B items 1-4 at the pure extraction level: ad-container
node scope, nested/inline emission, structural labels and entities, and the
main-missing / multi-main / empty-body / script-hidden / malformed contracts.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from transcript_api import _extract_body

FIXTURES = Path(__file__).parent / "fixtures" / "du7_extraction"


def _page(name: str) -> bytes:
    return (FIXTURES / f"{name}.html").read_bytes()


def test_ad_container_nodes_are_dropped_but_business_ad_words_kept():
    title, body = _extract_body(_page("ad_container"))
    assert title == "ACME Q2 2026 Earnings Call"
    assert "Our SaaS revenue grew 30% and our free cash flow margin reached 24%" in body
    assert "Management described the commercial rollout" in body
    assert "Operator: Good afternoon" in body
    # true ad containers (class/data-testid scoped) are removed whole
    assert "Motley Fool Stock Advisor" not in body
    assert "click here to learn more" not in body
    assert "Join the Motley Fool" not in body
    assert "Advertisement sponsorship message" not in body


def test_business_ad_words_at_any_position_or_language_are_kept():
    title, body = _extract_body(_page("mixed_cjk"))
    assert "advertisement revenue" in body
    assert "Advertisement disclosure: 广告定价下降属于业务事实，不应被当作推广内容删除。" in body
    assert "Sponsored segments within business commentary stay intact." in body
    # sentence-start / sentence-middle occurrences are treated identically
    assert body.lower().count("advertisement") >= 2
    assert body.count("Sponsored") == 1


def test_div_p_and_inline_markup_emit_once_in_document_order():
    title, body = _extract_body(_page("div_p_inline"))
    parts = body.split("\n\n")
    assert parts[0] == "Operator: Good afternoon, and welcome to the ACME second quarter 2026 earnings conference call."
    assert parts[1] == "Revenue for the second quarter was $1.2 billion, up 20% from the prior year, driven by SaaS demand."
    assert parts[2].startswith("CEO: We continue to invest in capacity expansion & overseas launch")
    assert len(parts) == 3


def test_real_repeated_dialogue_kept_while_structural_duplication_removed():
    title, body = _extract_body(_page("real_duplicate"))
    line = "CEO: We are very proud of the team this quarter."
    assert body.count(line) == 2
    parts = body.split("\n\n")
    assert parts[0] == line
    assert parts[2] == line


def test_structural_labels_entities_numbers_and_section_order():
    title, body = _extract_body(_page("labels_entities"))
    parts = body.split("\n\n")
    assert parts[0] == "Prepared Remarks"
    assert parts[1].startswith("Operator: Good afternoon")
    assert parts[2] == "Jane Smith — CEO"
    assert "GAAP revenue was $1.2 billion, up 20% year over year, with 30% EBITDA margin." in parts[3]
    assert parts[4] == "John Doe, CFO"
    assert "repaid €200 million of debt" in parts[5]
    assert "£1.4 billion of cash" in parts[5]
    assert parts[6] == "Questions and Answers"
    assert parts[7].startswith("Operator: We will now open the line")
    assert len(parts) == 8


def test_missing_main_is_provider_failure():
    from transcript_api import _ProviderFailure
    import pytest

    with pytest.raises(_ProviderFailure):
        _extract_body(_page("missing_main"))


def test_multi_main_keeps_current_first_main_behavior():
    """Current contract: soup.find('main') takes the first main only."""
    title, body = _extract_body(_page("multi_main"))
    assert "Operator: Good afternoon" in body
    assert "Management described the commercial rollout" not in body


def test_empty_main_body_fails_like_today():
    from transcript_api import _ProviderFailure
    import pytest

    with pytest.raises(_ProviderFailure):
        _extract_body(_page("empty_body"))


def test_script_style_and_hidden_nodes_never_leak_into_body():
    title, body = _extract_body(_page("script_hidden"))
    assert "Operator: Good afternoon" in body
    assert "Management described the commercial rollout" in body
    assert "tracking" not in body
    assert "hidden promotional block" not in body
    assert "display: none" not in body
    assert "invisible advertisement block" not in body
    parts = body.split("\n\n")
    assert len(parts) == 2


def test_malformed_html_extracts_surviving_text_without_fabricating():
    title, body = _extract_body(_page("malformed"))
    assert title
    assert "Operator: Good afternoon" in body
    assert "Management described the commercial rollout" in body


def test_dual_emission_regression_shapes():
    """Consecutive short blocks stay separate; li>p emits one paragraph."""
    li_body = _extract_body(_page("nested_li_p"))[1]
    assert li_body.count("Our subscription revenue increased 18 percent") == 1
    short_body = _extract_body(_page("short_title"))[1]
    assert short_body.split("\n\n")[0] == "Q&A"
    assert short_body.count("CEO: Sure.") == 1


def test_business_compound_class_names_are_not_treated_as_ad_containers():
    """follow-up / share-price / promo-code class tokens must not eat content."""
    html = (
        b'<html><head><meta charset="utf-8"></head><body>'
        b"<h1>ACME Q2 2026 Earnings Call</h1><main>"
        b'<div class="follow-up-question"><p>Analyst: Could you clarify the follow-up '
        b"on the enterprise pipeline and the expected conversion rate in the second "
        b"half of this fiscal year?</p></div>"
        b'<div class="share-price-metrics"><p>Revenue guidance was raised and the '
        b"share price metrics in our dashboard remain consistent with the outlook "
        b"we published in the prior quarter for the full fiscal year.</p></div>"
        b'<div class="promo-code-terms"><p>Promotional code terms were disclosed in '
        b"the appendix of the annual report filed with the regulator last week and "
        b"are unchanged from the previously published description of the program.</p></div>"
        b"</main></body></html>"
    )
    title, body = _extract_body(html)
    assert "Could you clarify the follow-up" in body
    assert "share price metrics in our dashboard" in body
    assert "Promotional code terms were disclosed" in body

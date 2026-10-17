"""Source-shaped boundaries missing from the DU10 author fixtures."""
import pytest

from transcript_extraction import EXTRACTION_VERSION, ExtractionError, extract


TEXT = "The company expanded its service business during the quarter. " * 5


@pytest.mark.parametrize("tail", [
    '<p title="unfinished >',
    "<p title='unfinished >",
    '<p title="first > second > still open',
    '<!-- unfinished >',
    '<!-- first > second > still open',
])
def test_incomplete_markup_uses_token_state_instead_of_last_angle(tail):
    page = f"<h1>Quarterly call</h1><main><p>{TEXT}</p>{tail}"
    with pytest.raises(ExtractionError, match="truncated_markup"):
        extract(page.encode())


@pytest.mark.parametrize("control", [
    '<p title="complete > attribute">Outlook is unchanged.</p>',
    '<!-- complete > comment -->',
    '<script>const marker = "<XYZ >";</script>',
    '<style>.example::after { content: "<XYZ >"; }</style>',
])
def test_complete_non_body_markup_does_not_reject_valid_transcript(control):
    page = f"<h1>Quarterly call</h1><main><p>{TEXT}</p>{control}</main>"
    assert TEXT.strip() in extract(page.encode()).body


def test_main_direct_and_inline_text_keep_order_and_honest_version():
    page = (
        '<h1>Quarterly call</h1><main>Prepared remarks.'
        f'<p>{TEXT}</p><strong>CFO:</strong> Revenue outlook rises.'
        '<p>Analyst: What changed?</p>CEO: New customers signed.</main>'
    )
    result = extract(page.encode())
    assert result.body.split("\n\n") == [
        'Prepared remarks.', TEXT.strip(),
        'CFO: Revenue outlook rises.',
        'Analyst: What changed?', 'CEO: New customers signed.',
    ]
    assert result.extraction_version == EXTRACTION_VERSION

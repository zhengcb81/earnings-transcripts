"""Pure DOM-ownership extraction for Motley Fool transcript pages.

One BeautifulSoup/DOM observation per call, original order, original language.
Every text run is owned by its nearest block-level container (any standard
block structure: headings at every level, paragraphs, list items, container
div/section/article, table cells, quotes, definition and preformatted blocks),
so an ancestor and its child never double-emit the same run and section
boundaries, speaker turns, or table content carried by non-``p`` containers
survive extraction. Filtering is node-scoped (non-content elements, hidden
content, evidenced ad/nav/sponsor containers); business text is never dropped
for merely mentioning advertising words. Input cut off inside an unfinished
tag fails closed with ``ExtractionError`` instead of parsing to a seemingly
complete transcript.

The frozen legacy semantics (``motley-fool-main-text/1``) are kept as a
reference projection over the same parsed DOM: when the new output is
byte-identical to it the result reports the legacy version, otherwise the
honest new version ``motley-fool-main-text/2``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup
from bs4.element import (
    CData,
    Comment,
    Declaration,
    Doctype,
    NavigableString,
    ProcessingInstruction,
    Tag,
)

import legacy_projection_comparison

EXTRACTION_VERSION_LEGACY = "motley-fool-main-text/1"
EXTRACTION_VERSION = "motley-fool-main-text/2"
MIN_CONTENT_CHARS = 200
# Frozen ``motley-fool-main-text/1`` per-paragraph rule: the legacy
# projection drops a block whose collapsed text is shorter than 5 chars.
LEGACY_MIN_PARAGRAPH_CHARS = 5

# Legacy v1 reference only: coarse whole-text drops. Never used to filter v2.
SKIP_TEXT = (
    "motley fool stock advisor",
    "click here to learn more",
    "advertisement",
    "get access now",
    "join the motley fool",
    "need a quote from a motley fool analyst",
    "image source:",
)

_BLOCK_TAGS = frozenset({"p", "h2", "h3", "li"})

# Frozen ``motley-fool-main-text/1`` block contract: the legacy projection and
# the compatibility decision are defined over exactly these tags and must
# never grow — ``legacy_projection_matches`` is called with this set below.
#
# v2 ownership: every standard block-level container owns its direct text
# runs, so Q&A/section markers on heading levels beyond h2/h3, speaker turns
# in container divs/sections, and table/list/quote content keep their text and
# boundaries instead of being silently discarded. Extraction-internal only;
# the frozen legacy contract above stays the compatibility baseline.
_V2_OWNERS = _BLOCK_TAGS | frozenset({
    "h1", "h4", "h5", "h6",
    "div", "section", "article", "blockquote", "figure", "details",
    "address", "hgroup", "center", "pre",
    "ul", "ol", "dl", "dt", "dd",
    "table", "caption", "td", "th",
})

# A page cut off inside an unfinished tag would parse with the incomplete tail
# silently discarded and return a seemingly complete transcript; fail closed
# instead. Only markup-looking tails (``<letter``, ``</``, ``<!``, ``<?``)
# count, so a lone ``<`` in trailing text stays a text character.
_DANGLING_MARKUP_RE = re.compile(rb"<[a-zA-Z/?!][^>]*\Z")

_NON_CONTENT_TAGS = frozenset({
    "script", "style", "noscript", "template", "svg", "iframe", "object",
    "embed", "form", "button", "input", "select", "textarea",
    "nav", "aside", "footer", "figcaption",
})

# Ambiguous business words in metadata are not enough to delete content.
# Recognize a declared UI/ad node or an adjacent ad/UI structural marker,
# never an arbitrary token from e.g. subscription-revenue/data-adoption-rate.
_EXPLICIT_UI_MARKERS = frozenset({
    "ad", "ads", "advert", "advertisement", "advertorial", "adslot",
    "adsbygoogle", "paywall", "interstitial", "outbrain", "taboola",
    "breadcrumb", "navbar", "navigation", "sidebar", "popup", "modal",
    "pagination", "comments", "disqus",
})
_PROMOTION_MARKERS = frozenset({
    "ad", "ads", "advert", "advertisement", "advertising", "advertorial",
    "sponsor", "sponsored", "sponsorship", "newsletter", "subscribe",
    "subscription", "cookie", "cookies", "consent", "comments",
})
_UI_STRUCTURES = frozenset({
    "container", "wrapper", "slot", "unit", "banner", "block", "widget",
    "popup", "overlay", "signup", "prompt", "consent", "notice", "form",
    "paywall", "panel", "menu", "navigation", "nav",
})
_AD_ATTRIBUTES = frozenset({
    "data-ad", "data-ad-slot", "data-ad-unit", "data-ad-container",
    "data-ad-client", "data-ad-format", "data-advertisement",
})
_FALSE_MARKERS = frozenset({"false", "0", "off", "none"})

_ROLE_TOKENS = frozenset({
    "navigation", "banner", "contentinfo", "complementary", "menu",
    "menubar", "tablist", "search",
})

_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")
_HIDDEN_STYLE_RE = re.compile(
    r"(?:^|;|\s)(?:display\s*:\s*none|visibility\s*:\s*hidden)(?:\s|;|$)",
    re.IGNORECASE,
)
_SKIPPED_STRING_TYPES = (Comment, CData, Doctype, Declaration, ProcessingInstruction)

DIAG_KEPT_SHORT = "kept_short_text"
DIAG_KEPT_SKIP = "kept_skip_text"
DIAG_NESTED_SUPPRESSED = "suppressed_nested_duplicate"
DIAG_DROPPED_CONTAINER = "dropped_node_scope"


class ExtractionError(Exception):
    """Typed extraction failure; the API port maps it to its error contract."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ExtractionResult:
    title: str
    body: str
    extraction_version: str
    diagnostics: tuple[str, ...]


def _metadata_marks_ui(tag: Tag) -> bool:
    for attr in ("class", "id", "data-testid", "data-type"):
        values = tag.get(attr)
        if not isinstance(values, list):
            values = [values]
        for value in values:
            if not isinstance(value, str):
                continue
            normalized = value.strip().lower()
            if normalized in _EXPLICIT_UI_MARKERS:
                return True
            tokens = [token for token in _TOKEN_SPLIT_RE.split(normalized) if token]
            if any(left in _PROMOTION_MARKERS and right in _UI_STRUCTURES
                   for left, right in zip(tokens, tokens[1:])):
                return True
    for attr in _AD_ATTRIBUTES:
        if tag.has_attr(attr) and str(tag.get(attr, "")).strip().lower() not in _FALSE_MARKERS:
            return True
    return False


def _is_droppable(tag: Tag) -> bool:
    if tag.name in _NON_CONTENT_TAGS:
        return True
    if tag.has_attr("hidden"):
        return True
    aria_hidden = tag.get("aria-hidden")
    if isinstance(aria_hidden, str) and aria_hidden.strip().lower() == "true":
        return True
    style = tag.get("style")
    if isinstance(style, str) and _HIDDEN_STYLE_RE.search(style):
        return True
    role = tag.get("role")
    if isinstance(role, str) and set(role.lower().split()) & _ROLE_TOKENS:
        return True
    return _metadata_marks_ui(tag)


def _collect_runs(
    node: Tag,
    owner: Tag | None,
    dropped: bool,
    runs: list[tuple[int, Tag, str]],
    state: dict[str, bool],
) -> None:
    # Iterator frames use O(depth) storage and never recurse on the Python
    # call stack. Dropped subtrees are pruned rather than repeatedly visited.
    stack = [(iter(node.children), owner, dropped)]
    while stack:
        children, current_owner, current_dropped = stack[-1]
        try:
            child = next(children)
        except StopIteration:
            stack.pop()
            continue
        if isinstance(child, _SKIPPED_STRING_TYPES):
            continue
        if isinstance(child, NavigableString):
            if current_dropped or current_owner is None:
                continue
            piece = str(child)
            if piece:
                runs.append((id(current_owner), current_owner, piece))
        elif isinstance(child, Tag):
            if current_dropped or _is_droppable(child):
                if child.name in _V2_OWNERS or child.find(list(_BLOCK_TAGS)) is not None:
                    state[DIAG_DROPPED_CONTAINER] = True
                continue
            if child.name == "br":
                if current_owner is not None:
                    runs.append((id(current_owner), current_owner, "\n"))
                continue
            child_owner = current_owner
            if child.name in _V2_OWNERS:
                child_owner = child
                if current_owner is not None and current_owner.name in _V2_OWNERS:
                    state[DIAG_NESTED_SUPPRESSED] = True
            stack.append((iter(child.children), child_owner, False))


def _paragraphs(runs: list[tuple[int, Tag, str]]) -> list[str]:
    paragraphs: list[str] = []
    run_ids: list[int] = []
    run_pieces: list[str] = []
    for run_id, _tag, piece in runs:
        if run_ids and run_id != run_ids[-1]:
            paragraphs.append("".join(run_pieces))
            run_pieces = []
        run_ids.append(run_id)
        run_pieces.append(piece)
    if run_pieces:
        paragraphs.append("".join(run_pieces))
    return [" ".join(text.split()) for text in paragraphs if text.split()]


def _legacy_v1_reference(soup: BeautifulSoup) -> tuple[str, str] | None:
    """Frozen v1 projection over the same DOM; ``None`` when v1 would fail."""
    title_node = soup.find("h1")
    title = title_node.get_text(" ", strip=True) if title_node else ""
    main = soup.find("main")
    if main is None:
        return None
    paragraphs: list[str] = []
    for node in main.find_all(["p", "h2", "h3", "li"]):
        text = " ".join(node.get_text(" ", strip=True).split())
        lowered = text.casefold()
        if len(text) < 5 or any(skip in lowered for skip in SKIP_TEXT):
            continue
        paragraphs.append(text)
    body = "\n\n".join(paragraphs).strip()
    if not title or len(body) < MIN_CONTENT_CHARS:
        return None
    return title, body


def _ends_in_unfinished_markup(page: bytes) -> bool:
    _head, sep, tail = page.rpartition(b">")
    return _DANGLING_MARKUP_RE.search(tail if sep else page) is not None


def extract(page: bytes) -> ExtractionResult:
    if _ends_in_unfinished_markup(page):
        raise ExtractionError("truncated_markup")
    soup = BeautifulSoup(page, "html.parser")
    title_node = soup.find("h1")
    title_runs: list[tuple[int, Tag, str]] = []
    if title_node is not None:
        _collect_runs(title_node, title_node, _is_droppable(title_node), title_runs, {})
    title = " ".join(_paragraphs(title_runs))
    main = soup.find("main")
    if main is None:
        raise ExtractionError("missing_main")

    runs: list[tuple[int, Tag, str]] = []
    state: dict[str, bool] = {}
    _collect_runs(main, None, _is_droppable(main), runs, state)
    paragraphs = _paragraphs(runs)
    body = "\n\n".join(paragraphs).strip()

    if not title:
        raise ExtractionError("missing_title")
    if len(body) < MIN_CONTENT_CHARS:
        raise ExtractionError("empty_body")

    diagnostics: list[str] = []
    for text in paragraphs:
        if len(text) < 5:
            state[DIAG_KEPT_SHORT] = True
        lowered = text.casefold()
        if any(skip in lowered for skip in SKIP_TEXT):
            state[DIAG_KEPT_SKIP] = True
    diagnostics.extend(sorted(key for key, present in state.items() if present))

    legacy_equal = legacy_projection_comparison.legacy_projection_matches(
        soup,
        title,
        body,
        block_tags=_BLOCK_TAGS,
        skip_text=SKIP_TEXT,
        min_paragraph_chars=LEGACY_MIN_PARAGRAPH_CHARS,
        min_content_chars=MIN_CONTENT_CHARS,
    )
    version = (
        EXTRACTION_VERSION_LEGACY if legacy_equal else EXTRACTION_VERSION
    )
    return ExtractionResult(
        title=title,
        body=body,
        extraction_version=version,
        diagnostics=tuple(diagnostics),
    )

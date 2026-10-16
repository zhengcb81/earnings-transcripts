"""Pure DOM-ownership extraction for Motley Fool transcript pages.

One BeautifulSoup/DOM observation per call, original order, original language.
Every text run is owned by its nearest structural block (p/h2/h3/li), so an
ancestor and its child never double-emit the same run. Filtering is node-scoped
(non-content elements, hidden content, evidenced ad/nav/sponsor containers);
business text is never dropped for merely mentioning advertising words.

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

EXTRACTION_VERSION_LEGACY = "motley-fool-main-text/1"
EXTRACTION_VERSION = "motley-fool-main-text/2"
MIN_CONTENT_CHARS = 200

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

_NON_CONTENT_TAGS = frozenset({
    "script", "style", "noscript", "template", "svg", "iframe", "object",
    "embed", "form", "button", "input", "select", "textarea",
    "nav", "aside", "footer", "figcaption",
})

# Whole tokens only (class/id values are tokenized on non-alphanumerics), so a
# business class like "load-more" can never match the "ad" token. Tokens that
# also read as business words in compound class names ("follow-up",
# "share-price", "promo-code") are deliberately not listed: node-scope
# filtering must not eat real transcript containers.
_CONTAINER_TOKENS = frozenset({
    "ad", "ads", "advert", "advertisement", "advertising", "adslot",
    "advertorial", "sponsor", "sponsored", "sponsorship",
    "newsletter", "subscribe", "subscription", "cookie",
    "cookies", "consent", "paywall", "interstitial", "outbrain", "taboola",
    "breadcrumb", "navbar", "navigation", "sidebar", "popup", "modal",
    "pagination", "comment", "comments", "disqus",
})

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


def _evidence_tokens(tag: Tag) -> set[str]:
    tokens: set[str] = set()
    for attr in ("class", "id", "role", "data-testid", "data-type"):
        value = tag.get(attr)
        if isinstance(value, list):
            value = " ".join(str(item) for item in value)
        if isinstance(value, str):
            tokens.update(
                token for token in _TOKEN_SPLIT_RE.split(value.lower()) if token
            )
    for attr in tag.attrs:
        if attr.startswith("data-ad"):
            tokens.add("ad")
    return tokens


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
    tokens = _evidence_tokens(tag)
    if tokens & _CONTAINER_TOKENS:
        return True
    return bool(tokens & _ROLE_TOKENS)


def _collect_runs(
    node: Tag,
    owner: Tag | None,
    dropped: bool,
    runs: list[tuple[int, Tag, str]],
    state: dict[str, bool],
) -> None:
    for child in node.children:
        if isinstance(child, _SKIPPED_STRING_TYPES):
            continue
        if isinstance(child, NavigableString):
            if dropped or owner is None:
                continue
            piece = child.strip()
            if not piece:
                continue
            runs.append((id(owner), owner, piece))
        elif isinstance(child, Tag):
            child_dropped = dropped or _is_droppable(child)
            if child_dropped and not dropped:
                if child.find(list(_BLOCK_TAGS)) is not None:
                    state[DIAG_DROPPED_CONTAINER] = True
            child_owner = owner
            if not child_dropped and child.name in _BLOCK_TAGS:
                child_owner = child
                if owner is not None and owner.name in _BLOCK_TAGS:
                    state[DIAG_NESTED_SUPPRESSED] = True
            _collect_runs(child, child_owner, child_dropped, runs, state)


def _paragraphs(runs: list[tuple[int, Tag, str]]) -> list[str]:
    paragraphs: list[str] = []
    run_ids: list[int] = []
    run_pieces: list[str] = []
    for run_id, _tag, piece in runs:
        if run_ids and run_id != run_ids[-1]:
            paragraphs.append(" ".join(run_pieces))
            run_pieces = []
        run_ids.append(run_id)
        run_pieces.append(piece)
    if run_pieces:
        paragraphs.append(" ".join(run_pieces))
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


def extract(page: bytes) -> ExtractionResult:
    soup = BeautifulSoup(page, "html.parser")
    title_node = soup.find("h1")
    title = title_node.get_text(" ", strip=True) if title_node else ""
    main = soup.find("main")
    if main is None:
        raise ExtractionError("missing_main")

    runs: list[tuple[int, Tag, str]] = []
    state: dict[str, bool] = {}
    _collect_runs(main, None, False, runs, state)
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

    legacy = _legacy_v1_reference(soup)
    version = (
        EXTRACTION_VERSION_LEGACY
        if legacy == (title, body)
        else EXTRACTION_VERSION
    )
    return ExtractionResult(
        title=title,
        body=body,
        extraction_version=version,
        diagnostics=tuple(diagnostics),
    )

"""Bounded ``motley-fool-main-text/1`` compatibility decision over one DOM.

The frozen legacy projection (``transcript_extraction._legacy_v1_reference``)
answers "is the current v2 title/body byte-identical to what v1 produced?"
by calling ``get_text`` for every legacy block, so each nested block
re-collects its whole subtree: legitimate deep nesting replays the DOM a
quadratic number of times and materializes the entire duplicated legacy
body. Production must answer the same boolean without that replay.

This module decides it from the already-parsed DOM with bounded work:

* one pre-order scan of the legacy blocks (the same order as
  ``main.find_all(["p", "h2", "h3", "li"])``) and at most two piece walks
  per candidate block: a length/``skip_text`` verdict, then an exact
  confirmation against the current paragraph;
* the legacy drop rules (``len(text) < min_paragraph_chars`` and the frozen
  ``skip_text`` substring after ``casefold``) are evaluated over the same
  token stream with a fixed-size sliding window, so the space-joined legacy
  text is never stored; the frozen whole-body floor ``min_content_chars``
  needs no legacy-side work: an equal body would share the current body's
  (already validated) length.
* equality is claimed only from exact token/character agreement (never from
  a hash), and comparison stops at the first divergence.

The decision is exactly ``_legacy_v1_reference(soup) == (title, body)``: the
frozen projection returns ``None`` exactly when the title side diverges or
the legacy body is shorter than ``min_content_chars``, and both of those
outcomes fail here on the same evidence.

Pure: no IO, no network, no configuration, and no second
``BeautifulSoup`` parse. ``stats`` is optional and exists so tests can prove
the visit/materialization bounds.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from bs4 import BeautifulSoup
from bs4.element import CData, NavigableString, Tag

# Exact-type membership mirrors bs4's get_text(): subclasses such as Comment,
# Script, Stylesheet, TemplateString, Doctype, Declaration and
# ProcessingInstruction are excluded; CData is included.
_STRING_TYPES = frozenset({NavigableString, CData})


@dataclass
class ComparisonStats:
    """Optional counters proving the bounded-work contract of one decision."""

    blocks_scanned: int = 0
    nodes_visited: int = 0
    text_strings_visited: int = 0
    chars_compared: int = 0
    full_confirmations: int = 0
    max_intermediate_chars: int = 0


class _SkipWindow:
    """Boolean ``skip_text`` detection over casefolded tokens without
    materializing the space-joined text.

    Only the trailing ``max_pattern_len`` characters of the stream are kept,
    which is enough for every fixed pattern to appear complete in at least
    one window; duplicates across windows are harmless because the verdict is
    a boolean. ``casefold`` never introduces whitespace, so the window is
    byte-equal to ``casefold(" ".join(tokens))`` over the same tokens.
    """

    def __init__(self, patterns: tuple[str, ...]) -> None:
        self._patterns = tuple(pattern.casefold() for pattern in patterns)
        self._carry = ""
        self._needs_space = False
        self._carry_chars = max((len(p) for p in self._patterns), default=0)
        self.found = False

    def feed(self, token: str) -> None:
        if self.found:
            return
        segment = (" " if self._needs_space else "") + token.casefold()
        window = self._carry + segment
        for pattern in self._patterns:
            if pattern and pattern in window:
                self.found = True
                return
        self._needs_space = True
        self._carry = window[-self._carry_chars:] if self._carry_chars else ""


def _iter_legacy_blocks(main: Tag, block_tags: frozenset[str]) -> Iterator[Tag]:
    """Yield block descendants in ``find_all`` document order.

    Droppable (hidden/ad/UI) containers are *not* pruned here: the frozen
    projection used ``find_all`` over the whole subtree.
    """
    stack: list[Iterator[Tag | NavigableString]] = [iter(main.children)]
    while stack:
        children = stack[-1]
        try:
            child = next(children)
        except StopIteration:
            stack.pop()
            continue
        if not isinstance(child, Tag):
            continue
        if child.name in block_tags:
            yield child
        stack.append(iter(child.children))


def _iter_text_pieces(node: Tag, stats: ComparisonStats | None) -> Iterator[str]:
    """Yield the exact string pieces ``get_text`` would concatenate, in
    document order, without recursion."""
    stack: list[Iterator[Tag | NavigableString]] = [iter(node.children)]
    while stack:
        children = stack[-1]
        try:
            child = next(children)
        except StopIteration:
            stack.pop()
            continue
        if stats is not None:
            stats.nodes_visited += 1
        if isinstance(child, NavigableString):
            if type(child) in _STRING_TYPES:
                if stats is not None:
                    stats.text_strings_visited += 1
                yield str(child)
        elif isinstance(child, Tag):
            stack.append(iter(child.children))


def _title_matches(
    soup: BeautifulSoup,
    current_title: str,
    stats: ComparisonStats | None,
) -> bool:
    """Replicate ``h1.get_text(" ", strip=True) == current_title`` with an
    early-exit streaming comparison (the v1 title is not whitespace-collapsed).

    An empty v1 title (no ``h1`` or no non-blank piece) means the frozen
    projection returns ``None``, which never equals the current tuple.
    """
    title_node = soup.find("h1")
    if title_node is None:
        return False
    position = 0
    first = True
    for piece in _iter_text_pieces(title_node, stats):
        stripped = piece.strip()
        if not stripped:
            continue
        if not first:
            if position >= len(current_title) or current_title[position] != " ":
                return False
            position += 1
            if stats is not None:
                stats.chars_compared += 1
        end = position + len(stripped)
        if current_title[position:end] != stripped:
            return False
        position = end
        if stats is not None:
            stats.chars_compared += len(stripped)
        first = False
    if first:
        return False
    return position == len(current_title)


def _scan_block(
    block: Tag,
    *,
    skip_text: tuple[str, ...],
    min_paragraph_chars: int,
    stats: ComparisonStats | None,
) -> tuple[int, bool]:
    """Walk 1: the normalized legacy length plus the frozen drop verdict.

    Returns ``(normalized_len, dropped)``. The text is never stored: tokens
    are counted as they stream and fed to the skip window. ``dropped``
    mirrors the frozen per-paragraph rule
    ``len(text) < min_paragraph_chars or any(skip in text.casefold())``.
    """
    char_total = 0
    token_count = 0
    window = _SkipWindow(skip_text)
    for piece in _iter_text_pieces(block, stats):
        for token in piece.split():
            if token_count:
                char_total += 1
            char_total += len(token)
            token_count += 1
            window.feed(token)
            if stats is not None:
                stats.max_intermediate_chars = max(
                    stats.max_intermediate_chars, len(token)
                )
    if token_count == 0:
        return 0, True
    dropped = char_total < min_paragraph_chars or window.found
    return char_total, dropped


def _block_matches(
    block: Tag,
    expected: str,
    stats: ComparisonStats | None,
) -> bool:
    """Walk 2: exact streaming confirmation of ``" ".join(tokens) == expected``
    for one candidate paragraph, stopping at the first divergence."""
    position = 0
    first = True
    for piece in _iter_text_pieces(block, stats):
        for token in piece.split():
            if not first:
                if position >= len(expected) or expected[position] != " ":
                    return False
                position += 1
                if stats is not None:
                    stats.chars_compared += 1
            end = position + len(token)
            if expected[position:end] != token:
                return False
            position = end
            if stats is not None:
                stats.chars_compared += len(token)
            first = False
    return position == len(expected)


def _body_matches(
    soup: BeautifulSoup,
    current_body: str,
    *,
    block_tags: frozenset[str],
    skip_text: tuple[str, ...],
    min_paragraph_chars: int,
    min_content_chars: int,
    stats: ComparisonStats | None,
) -> bool:
    """Lockstep the legacy paragraph sequence against the current one.

    The legacy sequence is every non-dropped block in ``find_all`` order; the
    current sequence is ``current_body.split("\\n\\n")`` (v2 paragraphs are
    whitespace-normalized, so the split is the exact inverse of the join).
    Positional equality of both sequences with equal lengths is exactly
    equality of the joined bodies, which is the frozen decision.
    """
    main = soup.find("main")
    if main is None:
        return False
    # The frozen projection returns None when the legacy body is shorter than
    # min_content_chars; an equal body would share the current length, so a
    # short current body can only ever be unequal.
    if len(current_body) < min_content_chars:
        return False
    expected_paragraphs = current_body.split("\n\n")
    index = 0
    for block in _iter_legacy_blocks(main, block_tags):
        if stats is not None:
            stats.blocks_scanned += 1
        size, dropped = _scan_block(
            block, skip_text=skip_text, min_paragraph_chars=min_paragraph_chars,
            stats=stats,
        )
        if dropped:
            continue
        if index >= len(expected_paragraphs):
            return False
        expected = expected_paragraphs[index]
        if size != len(expected):
            return False
        if not _block_matches(block, expected, stats):
            return False
        if stats is not None:
            stats.full_confirmations += 1
        index += 1
    return index == len(expected_paragraphs)


def legacy_projection_matches(
    soup: BeautifulSoup,
    current_title: str,
    current_body: str,
    *,
    block_tags: frozenset[str],
    skip_text: tuple[str, ...],
    min_paragraph_chars: int,
    min_content_chars: int,
    stats: ComparisonStats | None = None,
) -> bool:
    """Return whether the frozen v1 projection equals ``(current_title,
    current_body)`` on this parsed DOM.

    The frozen inputs (``block_tags``, ``skip_text``,
    ``min_paragraph_chars``, ``min_content_chars``) come from the extraction
    owner so this module keeps no second copy of the legacy semantics.
    """
    if not _title_matches(soup, current_title, stats):
        return False
    return _body_matches(
        soup,
        current_body,
        block_tags=block_tags,
        skip_text=skip_text,
        min_paragraph_chars=min_paragraph_chars,
        min_content_chars=min_content_chars,
        stats=stats,
    )

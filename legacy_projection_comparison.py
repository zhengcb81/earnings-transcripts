"""Bounded ``motley-fool-main-text/1`` compatibility decision over one DOM.

The frozen legacy projection (``transcript_extraction._legacy_v1_reference``)
answers "is the current v2 title/body byte-identical to what v1 produced?"
by calling ``get_text`` for every legacy block, so each nested block
re-collects its whole subtree: legitimate deep nesting replays the DOM a
quadratic number of times and materializes the entire duplicated legacy
body. Production must answer the same boolean without that replay.

This module decides it from the already-parsed DOM in two bounded phases:

* **Evidence pass** — one iterative pre-order walk of ``main`` that visits
  every node once. It yields the legacy blocks in ``find_all`` order, keeps
  per-block compositional counts (token count and summed token length,
  merged child totals at block close), and runs a *single* streaming
  ``skip_text`` matcher over the global casefolded token stream. Every match
  is tagged to the innermost block whose span contains it and inherited by
  ancestors at close, so the space-joined legacy text is never stored and
  each subtree contributes work only for its own tokens.
* **Confirmation phase** — for each kept block in order (drop rule
  ``size < min_paragraph_chars`` or ``skip_text`` hit), the composed length
  is compared with the current paragraph first; only on equal lengths does
  an exact streaming walk compare characters, stopping at the first
  divergence. A block whose composed length already diverges costs O(1).

Equality is claimed only from exact token/character agreement (never from a
hash), and ``"\\n\\n"``-joined bodies equal their paragraph sequences, so the
decision is exactly ``_legacy_v1_reference(soup) == (title, body)``: the
frozen projection returns ``None`` exactly when the title side diverges or
the legacy body is shorter than ``min_content_chars``, and both of those
fail here on the same evidence (an equal body would share the already
validated current length).

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


@dataclass
class _BlockEvidence:
    """Compositional legacy text facts for one block, filled by the single
    evidence pass: ``count``/``sum_len`` describe the block's whole subtree
    token stream; ``tainted`` records a ``skip_text`` occurrence inside it."""

    count: int = 0
    sum_len: int = 0
    tainted: bool = False
    start: int | None = None  # casefolded-stream offset of the first token

    @property
    def size(self) -> int:
        """Normalized legacy text length (tokens joined by single spaces)."""
        if self.count == 0:
            return 0
        return self.sum_len + self.count - 1


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
                piece = str(child)
                if stats is not None:
                    stats.max_intermediate_chars = max(
                        stats.max_intermediate_chars, len(piece)
                    )
                yield piece
        elif isinstance(child, Tag):
            stack.append(iter(child.children))


class _GlobalSkipStream:
    """Boolean ``skip_text`` detection over the global casefolded token
    stream, reporting each match's character span for containment tagging.

    Only the trailing ``max_pattern_len`` characters are kept, which is
    enough for every fixed pattern to appear complete in at least one
    window; re-reported duplicates are harmless because downstream marking
    is idempotent. ``casefold`` never introduces whitespace, so the stream is
    byte-equal to ``casefold(" ".join(tokens))`` over the same tokens.
    """

    def __init__(self, patterns: tuple[str, ...]) -> None:
        self._patterns = tuple(pattern.casefold() for pattern in patterns)
        self._max_len = max((len(p) for p in self._patterns), default=0)
        self.chars = 0  # casefolded-stream offset after the last token
        self.emitted = False
        self._carry = ""

    def feed(self, token: str) -> tuple[int, int, str]:
        """Advance one token; returns ``(token_start, window_start, window)``
        where ``token_start`` is the token's casefolded-stream start offset."""
        sep = 1 if self.emitted else 0
        folded = token.casefold()
        token_start = self.chars + sep
        segment = (" " if sep else "") + folded
        window = self._carry + segment
        window_start = self.chars - len(self._carry)
        self.chars = token_start + len(folded)
        self.emitted = True
        self._carry = window[-self._max_len:] if self._max_len else ""
        return token_start, window_start, window

    def matches_in(self, window: str, window_start: int) -> Iterator[tuple[int, int]]:
        for pattern in self._patterns:
            if not pattern:
                continue
            index = window.find(pattern)
            while index != -1:
                start = window_start + index
                yield start, start + len(pattern)
                index = window.find(pattern, index + 1)


def _collect_evidence(
    main: Tag,
    block_tags: frozenset[str],
    skip_text: tuple[str, ...],
    stats: ComparisonStats | None,
) -> tuple[list[Tag], dict[int, _BlockEvidence]]:
    """Single pre-order walk producing the legacy blocks in ``find_all``
    order plus per-block evidence.

    Droppable (hidden/ad/UI) containers are *not* pruned here: the frozen
    projection used ``find_all`` over the whole subtree. Every node is
    visited once; per-block totals merge child totals at close (O(1)), and
    skip matches are tagged to the innermost containing block at the moment
    they complete, then inherited upward at close.
    """
    ordered: list[Tag] = []
    evidence: dict[int, _BlockEvidence] = {}
    frames: list[_BlockEvidence] = []
    stream = _GlobalSkipStream(skip_text)

    def feed_token(piece: str) -> None:
        for token in piece.split():
            token_start, window_start, window = stream.feed(token)
            if stats is not None:
                stats.max_intermediate_chars = max(
                    stats.max_intermediate_chars, len(token)
                )
            for frame in reversed(frames):
                if frame.start is not None:
                    break
                frame.start = token_start
            if frames:
                innermost = frames[-1]
                innermost.count += 1
                innermost.sum_len += len(token)
            for match_start, _match_end in stream.matches_in(window, window_start):
                for frame in reversed(frames):
                    if frame.start is not None and frame.start <= match_start:
                        frame.tainted = True
                        break

    # Traversal entries: (children iterator, block evidence or None for
    # non-block tags). ``frames`` mirrors only the block entries.
    stack: list[tuple[Iterator[Tag | NavigableString], _BlockEvidence | None]] = [
        (iter(main.children), None)
    ]
    while stack:
        children, block_evidence = stack[-1]
        try:
            child = next(children)
        except StopIteration:
            stack.pop()
            if block_evidence is not None:
                frames.pop()
                if frames:
                    parent = frames[-1]
                    parent.count += block_evidence.count
                    parent.sum_len += block_evidence.sum_len
                    parent.tainted = parent.tainted or block_evidence.tainted
            continue
        if stats is not None:
            stats.nodes_visited += 1
        if isinstance(child, NavigableString):
            if type(child) in _STRING_TYPES:
                if stats is not None:
                    stats.text_strings_visited += 1
                piece = str(child)
                if stats is not None:
                    stats.max_intermediate_chars = max(
                        stats.max_intermediate_chars, len(piece)
                    )
                feed_token(piece)
            continue
        if not isinstance(child, Tag):
            continue
        if child.name in block_tags:
            ordered.append(child)
            child_evidence = _BlockEvidence()
            evidence[id(child)] = child_evidence
            if stats is not None:
                stats.blocks_scanned += 1
            frames.append(child_evidence)
            stack.append((iter(child.children), child_evidence))
        else:
            stack.append((iter(child.children), None))
    return ordered, evidence


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


def _block_matches(
    block: Tag,
    expected: str,
    stats: ComparisonStats | None,
) -> bool:
    """Exact streaming confirmation of ``" ".join(tokens) == expected`` for
    one kept candidate, stopping at the first divergence.

    Only reached when the composed length already equals ``len(expected)``,
    which (together with equal lengths at every position) keeps these walks
    over pairwise disjoint subtrees.
    """
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

    The legacy sequence is every non-dropped block in ``find_all`` order;
    the current sequence is ``current_body.split("\\n\\n")`` (v2 paragraphs are
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
    ordered, evidence = _collect_evidence(main, block_tags, skip_text, stats)
    expected_paragraphs = current_body.split("\n\n")
    index = 0
    for block in ordered:
        facts = evidence[id(block)]
        if facts.count == 0:
            continue
        size = facts.size
        if size < min_paragraph_chars or facts.tainted:
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

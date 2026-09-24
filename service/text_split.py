"""Shared sentence segmentation with absolute character offsets.

Why this module exists
----------------------
"One sentence" used to mean four different regexes in this project:

* ``core/sentence_tracing.py``         -> ``re.split(r'[。！？.!?\\n]', text)``
* ``core/evaluator.py``                -> the same expression, copy-pasted
* ``core/retriever.py`` (compression)  -> ``re.split(r'[。！？\\n]', text)``
* ``static/js/provenance.js``         -> ``split(/([。！？.!?\\n])/)``

Splitting on a bare ``.`` destroys exactly the content a provenance feature has
to protect: numbers. ``"BM25 weight is 0.4 and vector weight is 0.6."`` became
three "sentences" (``...is 0`` / ``4 and vector weight is 0`` / ``6``), so
per-sentence verdicts were attached to fragments of numbers, drift and
hallucination rates counted those fragments as separate sentences, and the
browser (which re-split the text with its own regex) could not keep its
sentence indexes aligned with the backend's.

This module fixes both halves of the problem:

1. it is the single splitter, aware of decimals / versions / file names,
   English abbreviations, ellipsis, closing quotes, citation markers, fenced
   and inline code, and Markdown lists;
2. it returns **spans carrying absolute character offsets**, so evidence can be
   attributed by offset instead of by guessed index (see
   ``SentenceTracer.trace`` and the ``sentence_provenance`` SSE event).

Nothing here needs a model, a vector store or a running service: it is pure
text handling and therefore unit-testable end to end.
"""
import re
from dataclasses import dataclass
from typing import Iterator, List, Tuple

__all__ = [
    'SentenceSpan',
    'CITATION_MARKER_RE',
    'split_sentences',
    'split_sentence_spans',
    'split_complete_sentences',
    'iter_sentence_spans',
    'normalize_sentence',
]

# Sentence terminators: full-width CJK punctuation (。！？) and their
# half-width ASCII equivalents (! ?). Note that "." is intentionally NOT
# listed here because its behavior depends on surrounding characters and is
# decided by _is_period_boundary().
_TERMINATORS = frozenset('。！？!?')
# An ellipsis (single-glyph "…" or repeated "……") also ends a sentence.
_ELLIPSIS = frozenset('…')
# Closing quotes, brackets and braces that visually belong to the sentence
# that was just terminated, so they are consumed into the same span.
_CLOSERS = frozenset('"\'”’)}]】》〉」』〕｝')

# Citation markers that the system prompts force the model to emit:
# "[Document N]", "[chunk_N]", "[N]". This regex is shared by the splitter,
# the sentence tracer and the citation validator so all three agree on what
# counts as a citation marker.
CITATION_MARKER_RE = re.compile(
    r'\[\s*(?:(?:document|doc|chunk)\s*_?)?\s*(\d+)\s*\]',
    re.IGNORECASE,
)

# English abbreviations whose trailing dot must NOT be treated as a sentence
# boundary. For example "Mr. Smith went home." should not split after "Mr.".
_ABBREVIATIONS = frozenset({
    'mr', 'mrs', 'ms', 'dr', 'prof', 'sr', 'jr', 'st', 'vs', 'etc', 'eg',
    'ie', 'cf', 'al', 'fig', 'figs', 'no', 'nos', 'inc', 'ltd', 'co', 'corp',
    'approx', 'dept', 'est', 'vol', 'pp', 'sec', 'min', 'max', 'e.g', 'i.e',
})

# Markdown / plain-text list markers that start a new block:
#   numbered:  "1. ", "2) ", "1、"
#   bulleted:  "- ", "* ", "+ ", "• ", "· "
#   headings:  "# ", "## ", ...
# Each list item is treated as its own sentence regardless of terminators.
_LIST_MARKER_RE = re.compile(r'^(?:\(?\d{1,3}[.)、]|[-*+•·]|#{1,6})\s*')


@dataclass(frozen=True)
class SentenceSpan:
    """A single sentence together with its position in the source text.

    Invariant: ``text == source[start:end]`` always holds. The ``end`` offset
    excludes the whitespace that separated this sentence from the next one, so
    callers can reconstruct the original string (including paragraph breaks)
    by joining the gaps between consecutive spans.
    """

    text: str
    start: int
    end: int
    # True when the sentence was terminated by a punctuation mark or a blank
    # line. False for trailing fragments during streaming (no terminator yet).
    closed: bool


def _is_alnum(ch: str) -> bool:
    """Check whether ``ch`` is an alphanumeric character or an underscore.

    Python's built-in ``str.isalnum()`` excludes underscores, but for the
    purpose of detecting word boundaries (decimals, versions, identifiers) we
    need the underscore to behave like a word character.
    """
    return bool(ch) and (ch.isalnum() or ch == '_')


def _previous_token(text: str, index: int) -> str:
    """Return the word-like token that ends immediately before ``index``.

    Scans backward from ``index`` collecting characters that are alphanumeric,
    underscore, hyphen, apostrophe or dot. This is used to detect abbreviations
    ("Mr.", "U.S.A.") and initials ("J.") before a period.
    """
    i = index
    while i > 0 and (_is_alnum(text[i - 1]) or text[i - 1] in "-.'"):
        i -= 1
    return text[i:index]


def _line_start(text: str, index: int) -> int:
    """Return the index of the first character on the line containing ``index``."""
    newline = text.rfind('\n', 0, index)
    return 0 if newline == -1 else newline + 1


def _line_prefix_blank(text: str, index: int) -> bool:
    """Return True when every character between line start and ``index`` is whitespace.

    This is used to detect list markers ("1. foo") that appear at the beginning
    of a line, where the dot should not end a sentence.
    """
    return not text[_line_start(text, index):index].strip()


def _skip_run(text: str, index: int, ch: str) -> int:
    """Return the index just past a consecutive run of ``ch`` starting at ``index``."""
    i = index
    while i < len(text) and text[i] == ch:
        i += 1
    return i


def _skip_fence(text: str, index: int) -> int:
    """Return the index just past a fenced code block ("```") starting at ``index``.

    The closing fence must be another "```" on its own. If no closing fence is
    found before end of text, returns ``len(text)``.
    """
    end = text.find('```', index + 3)
    return len(text) if end == -1 else end + 3


def _skip_inline_code(text: str, index: int, run: int) -> int:
    """Return the index just past an inline code span of ``run`` backticks.

    For example, with ``run=1`` it matches "`code`"; with ``run=2`` it matches
    "``code``". Returns ``len(text)`` if no matching close is found.
    """
    end = text.find('`' * run, index + run)
    return len(text) if end == -1 else end + run


def _is_blank_line_break(text: str, index: int) -> bool:
    """Return True when the newline sequence at ``index`` contains a blank line.

    A blank line (two or more newlines, possibly with carriage returns) marks a
    paragraph break and therefore acts as a sentence boundary.
    """
    i = index
    while i < len(text) and text[i] in '\r\n':
        i += 1
    return text[index:i].count('\n') >= 2


def _next_line_starts_a_block(text: str, index: int) -> bool:
    """Return True when the line following the newline at ``index`` starts a list/heading block.

    Skips the newline characters and any leading whitespace, then checks whether
    the remaining text begins with a Markdown list marker or heading.
    """
    i = index
    while i < len(text) and text[i] in '\r\n':
        i += 1
    while i < len(text) and text[i] in ' \t':
        i += 1
    if i >= len(text):
        return False
    return bool(_LIST_MARKER_RE.match(text[i:]))


def _is_period_boundary(text: str, index: int) -> bool:
    """Decide whether the period (".") at ``index`` ends a sentence.

    Returns False for dots that are part of:
      - decimals or numbers: "0.4", "3.14"
      - version strings: "v1.2.3"
      - file names or domains: "FAQ.txt", "example.com"
      - numbered list markers at line start: "1. First item"
      - English abbreviations: "Mr.", "Dr.", "etc."
      - initials or dotted acronyms: "J. Smith", "U.S.A."
    Returns True in all other cases (e.g. "End of sentence.")
    """
    prev = text[index - 1] if index > 0 else ''
    nxt = text[index + 1] if index + 1 < len(text) else ''

    # Dot between two word characters: never a sentence end.
    # Examples: "0.4", "v1.2.3", "FAQ.txt", "example.com".
    if _is_alnum(prev) and _is_alnum(nxt):
        return False

    # Numbered list marker at the start of a line: "1. First item".
    # The digits must be at the beginning of the line (only whitespace before
    # them) and the character after the dot must be end-of-text or whitespace.
    if prev.isdigit():
        digits_start = index
        while digits_start > 0 and text[digits_start - 1].isdigit():
            digits_start -= 1
        if _line_prefix_blank(text, digits_start) and (nxt == '' or nxt.isspace()):
            return False

    # Check if the word before the dot is a known abbreviation.
    token = _previous_token(text, index)
    if _is_abbreviation(text, index, token):
        return False
    # Initials or dotted acronyms: "J. Smith", "U.S.A.".
    # A two-character token where the first is uppercase and the second is "."
    # indicates an initial, so the dot does not end a sentence.
    if len(token) == 2 and token[0].isupper() and token[1] == '.':
        return False
    return True


def _is_abbreviation(text: str, index: int, token: str) -> bool:
    """Return True when ``token`` is an abbreviation whose dot does not end a sentence.

    Most abbreviations are looked up directly in ``_ABBREVIATIONS``. The special
    case is "et al.": a standalone "al." could legitimately end a sentence, so
    it is only treated as an abbreviation when preceded by "et".
    """
    lowered = token.lower().rstrip('.')
    if not lowered:
        return False
    if lowered == 'al':
        previous = _previous_token(text, max(0, index - len(token) - 1))
        return previous.lower().rstrip('.') == 'et'
    return lowered in _ABBREVIATIONS


def _consume_terminator_tail(text: str, index: int) -> Tuple[int, int]:
    """Return ``(end, run_end)`` for the terminator run starting at ``index``.

    ``end`` covers the full terminator run plus any trailing closing
    quotes/brackets and citation markers, because these all belong to the
    sentence that was just terminated. For example, ``Hello! [1]`` stays one
    sentence with the citation marker included.

    ``run_end`` marks where the pure terminator characters stopped (before any
    closers or markers). This is needed by the streaming logic to decide
    whether the sentence is fully closed or could still receive more text.
    """
    # First pass: consume consecutive terminator/ellipsis/dot characters.
    i = index + 1
    while i < len(text) and (text[i] in _TERMINATORS or text[i] in _ELLIPSIS or text[i] == '.'):
        i += 1
    run_end = i
    # Second pass: consume closing punctuation and citation markers that
    # visually belong to the terminated sentence.
    while i < len(text):
        if text[i] in _CLOSERS:
            i += 1
            continue
        marker = CITATION_MARKER_RE.match(text, i)
        if marker:
            i = marker.end()
            continue
        break
    return i, run_end


def _rstrip_end(text: str, start: int) -> int:
    """Return the index of the last non-whitespace character in ``text``.

    Trims trailing whitespace so that sentence spans do not include the spaces
    or newlines that separate them from the following sentence.
    """
    end = len(text)
    while end > start and text[end - 1].isspace():
        end -= 1
    return end


def _tail_is_incomplete(remainder: str) -> bool:
    """Return True when text after a terminator is an unfinished citation marker.

    During streaming, the model may output "..." followed later by
    "[Document 1]". If we closed the sentence at "..." before the marker
    arrived, the marker would end up in the next sentence and shift all
    subsequent offsets. So a remainder that starts with "[" but has no closing
    "]" yet is treated as incomplete.

    A remainder that already contains "]" (e.g. "[Document 1]") or does not
    start with "[" at all is real content and the sentence can be closed.
    """
    stripped = remainder.strip()
    return stripped.startswith('[') and ']' not in stripped


def _is_boundary(text: str, index: int) -> bool:
    """Return True when the character at ``index`` marks a sentence boundary.

    Three types of boundaries are recognized:
      1. Hard terminators ("。", "！", "？", "!", "?") and ellipsis ("…").
      2. Periods (".") — but only when they are not part of a number,
         abbreviation, initial, or list marker (see ``_is_period_boundary``).
      3. Newlines that form a blank line (paragraph break) or precede a
         Markdown list/heading block.
    """
    ch = text[index]
    if ch in _TERMINATORS or ch in _ELLIPSIS:
        return True
    if ch == '.':
        return _is_period_boundary(text, index)
    if ch == '\n':
        return _is_blank_line_break(text, index) or _next_line_starts_a_block(text, index)
    return False


def iter_sentence_spans(text: str) -> Iterator[SentenceSpan]:
    """Yield every sentence of ``text`` in order, including a trailing fragment.

    Code fences (```...```) and inline code (`...`) are skipped over without
    inspection, so a period inside a code snippet never acts as a boundary.

    A trailing fragment that has no terminator is still yielded with
    ``closed=False``. This lets streaming callers keep that fragment pending
    until more text arrives (see :func:`split_complete_sentences`).
    """
    if not text:
        return
    total = len(text)
    i = 0
    start = -1
    while i < total:
        ch = text[i]
        if start == -1:
            # Skip leading whitespace until the first character of a sentence.
            if ch.isspace():
                i += 1
                continue
            start = i

        if ch == '`':
            # Skip over fenced code blocks or inline code without parsing them.
            run = _skip_run(text, i, '`') - i
            i = _skip_fence(text, i) if run >= 3 else _skip_inline_code(text, i, run)
            continue
        if _is_boundary(text, i):
            end, run_end = _consume_terminator_tail(text, i)
            # Decide whether to mark the sentence as closed.
            # The splitter also runs on partial text during streaming, so:
            #   * A terminator run that reaches the end of text may still grow
            #     (e.g. "is 0." is about to become "is 0.4", or "…" may become
            #     "……"), so it stays open.
            #   * A run followed only by an unfinished citation marker ("["
            #     without "]") stays open, otherwise the marker lands in the
            #     next sentence and shifts all later offsets.
            #   * Otherwise the sentence is closed.
            # The span's text and offsets are identical in all cases, which is
            # why streamed offsets stay aligned with the final trace.
            closed = run_end < total and not _tail_is_incomplete(text[run_end:])
            end = _rstrip_end(text[:end], start)
            if end > start:
                yield SentenceSpan(text[start:end], start, end, closed)
            start = -1
            i = max(end, i + 1)
            continue
        i += 1

    # Emit any trailing fragment that was not terminated.
    if start != -1:
        end = _rstrip_end(text, start)
        if end > start:
            yield SentenceSpan(text[start:end], start, end, False)


def split_sentence_spans(text: str) -> List[SentenceSpan]:
    """Return every sentence of ``text`` as a list of :class:`SentenceSpan` objects."""
    return list(iter_sentence_spans(text))


def split_sentences(text: str) -> List[str]:
    """Return the sentence texts of ``text`` (character offsets are discarded)."""
    return [span.text for span in iter_sentence_spans(text)]


def split_complete_sentences(text: str) -> Tuple[List[SentenceSpan], int]:
    """Split a (possibly partial) buffer into finished sentences and a pending tail.

    Used by the streaming pipeline: sentences that are already complete are
    returned immediately for tracing, while the unterminated remainder stays in
    the buffer waiting for more tokens.

    Returns:
        A tuple ``(sentences, pending_start)`` where ``sentences`` is the list
        of completed spans and ``pending_start`` is the offset where the
        unfinished remainder begins. ``pending_start`` equals ``len(text)``
        when nothing is left pending.
    """
    spans = list(iter_sentence_spans(text))
    completed: List[SentenceSpan] = []
    pending_start = len(text)
    for span in spans:
        if span.closed:
            completed.append(span)
        else:
            pending_start = span.start
    return completed, pending_start


def normalize_sentence(text: str) -> str:
    """Collapse all whitespace runs into single spaces and trim the ends.

    This allows two sentences that differ only in whitespace (e.g. extra
    spaces, tabs, or line breaks) to compare as equal.
    """
    return re.sub(r'\s+', ' ', (text or '').strip())
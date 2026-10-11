# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Internal links in the docs corpus resolve, including the ones mkdocs cannot see.

``mkdocs build --strict`` only validates links it can *resolve*. A reference-style use
``[text][key]`` whose ``key`` has no ``[key]: …`` definition is not a link at all to
Python-Markdown — it renders as the literal source text — so there is no target for the
build to complain about. That is how the parity gallery reached the published site
showing ``[McCann 2010][mccann2010]`` fourteen times with the required ``docs-build``
gate green (#183, repaired by hand in #207). This module closes that blind spot, and
re-checks the targets the build does see so the same guard runs on all three operating
systems rather than only on the Linux ``docs-build`` job. These are enforced, over the
forms the parse models (below):

* every ``[text][key]`` (and collapsed ``[text][]``) has a definition in the same file;
* every Markdown link, image and reference definition with a relative target names a
  file that exists inside the published ``docs/`` tree — not a directory, which mkdocs
  leaves unrewritten and ``--strict`` rejects, and not the excluded ``PRD.md``;
* every ``#fragment`` on such a link names a heading slug or an HTML anchor in the target
  page — an ``id`` on any element, or a ``name`` on an ``<a>``, the two a browser scrolls
  to — such as the inline ``<a id="…">`` bibliography form #207 standardised on;
* a raw HTML ``<a href>``, quoted or not, is either a URL with a scheme or a same-page
  ``#fragment`` that resolves; any other target is refused, because mkdocs does not
  rewrite raw HTML.

External URLs are out of scope here (the advisory ``external-links`` job checks them). The
walk is ``docs/**/*.md`` minus ``docs/PRD.md``, which ``mkdocs.yml`` excludes from the site.

**The parse is a model of Python-Markdown 3.11 with a stated scope, not a renderer.** It is
stdlib only (``re``, ``html.parser``, ``pathlib``), like ``tests/test_adr_index.py``:
``python-markdown`` is not in the base conda-lock, so it cannot be imported on the 3-OS
``test`` matrix. ``mkdocs.yml`` leaves Markdown at mkdocs' default extensions (``toc``,
``tables``, ``fenced_code``). What this module promises is narrower than "the renderer's
verdict on any page". It promises this:

* the forms listed below are modelled, most of them with Python-Markdown's own regular
  expressions and procedures, copied and named. Each is pinned here by a test whose page
  was rendered with Python-Markdown 3.11, and during review of #511 a differential fuzzer
  compared the model with an instrumented real render over tens of thousands of generated
  pages and headings. That fuzzer needs ``python-markdown`` and is not part of this suite;
* a form outside the list that the checker can detect, and that could change its
  verdict, fails with ``unsupported Markdown form, the checker cannot vouch for it`` and
  the file and line. Fix such a failure by rewriting the page in a listed form, or by
  extending the model with a test checked against a real render — never by guessing;
* any other form is not guaranteed. The checker may misread it either way.

Known gaps are tracked in #515. A form not listed here is out of scope until it is listed.

The modelled forms:

* whitespace as ``NormalizeWhitespace`` leaves it, with tabs expanded to four spaces;
* fenced blocks, by ``FENCED_BLOCK_RE`` itself, so only at the root. A fence-shaped line
  anywhere else, inside a blockquote or indented, is ordinary text to the renderer and
  here too: its backticks pair as code spans and its other lines are read;
* HTML comments, read in page order as ``html.parser`` reads them, so nothing inside one
  opens a comment or a block of its own; one that opens a line is stashed whole by
  ``HtmlBlockPreprocessor``. Raw HTML blocks as its ``HTMLExtractor`` cuts them, whose
  Markdown is not parsed;
* blockquotes, lazy continuation lines included;
* the block boundaries that hold in every context: blank lines, ATX headings
  (``HashHeaderProcessor.RE``, at column zero inside any blockquotes), horizontal rules
  (``HRProcessor.SEARCH_RE``), and the first line of a blockquote;
* setext headings whose title certainly opens a block, that is, it follows one of those
  boundaries, and whose underline is at column zero (``SetextHeaderProcessor.RE``). An
  underline anywhere else makes no heading;
* reference definitions (``ReferenceProcessor.RE``), with keys normalised as the
  renderer normalises them;
* within each block: code spans (``BacktickInlineProcessor``, with its backslash parity
  and its fallback pairing), backslash escapes, link text nested to any depth
  (``getText``), inline link targets (``getLink``, nested parentheses and all), reference
  uses (``ReferenceInlineProcessor``), and inline HTML (``HTML_RE``). A reference with a
  defined key takes its span before the link pass, in the renderer's pattern order. Inline
  HTML attributes are read by ``html.parser``, so an unquoted ``href`` counts;
* heading slugs, by ``toc.slugify`` with its ``_1``/``_2`` de-duplication, over the
  heading's text as rendered. Its links, images and references are read by the same
  passes, in the renderer's pattern order. A link or a defined reference gives its text,
  an image gives none, and an undefined reference stays as written.

These forms are refused, because how the renderer reads them depends on context that the
model does not track:

* a code span, link or inline tag that runs from one line onto a line that may start a
  new block — a list item, a reference definition, a table row, or the first line after
  an indented block;
* a heading, rule or definition line inside a table-like block, which is a row if the
  block is a table and structure if it is not;
* an HTML ``id`` or ``name`` on a line of an indented block, which is code after a
  paragraph and prose inside a list;
* an ``<a>`` tag that ``html.parser`` cannot read as one start tag.

Some forms are read *short* on purpose. A heading or definition that the model cannot
place is not counted, for example one in a list item's indented continuation or a
definition inside a blockquote. Leaving one out can only make a link to it fail. For the
same reason the lines of an indented block are read as prose, so a ``[a][b]`` inside a
real indented code block is reported. None exists in ``docs/`` today.
"""

from __future__ import annotations

import bisect
import html
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from functools import cache
from html.parser import HTMLParser
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parents[1] / "docs"

# `mkdocs.yml` `exclude_docs:` — not part of the site, so neither walked nor linkable.
_EXCLUDED = {DOCS / "PRD.md"}

_UNSUPPORTED = "unsupported Markdown form, the checker cannot vouch for it"

# --- Python-Markdown 3.11, copied rather than paraphrased -------------------------------
# Each pattern names the Python-Markdown attribute it is copied from, so a reader can diff
# it against the renderer instead of trusting a description of it.

_TAB = 4  # Markdown.tab_length
# fenced_code.FencedBlockPreprocessor.FENCED_BLOCK_RE: `^` at the root only.
_FENCED_BLOCK_RE = re.compile(
    r"""
(?P<fence>^(?:~{3,}|`{3,}))[ ]*                          # opening fence
((\{(?P<attrs>[^\n]*)\})|                                # (optional {attrs} or
(\.?(?P<lang>[\w#.+-]*)[ ]*)?                            # optional (.)lang
(hl_lines=(?P<quot>"|')(?P<hl_lines>.*?)(?P=quot)[ ]*)?) # optional hl_lines)
\n                                                       # newline (end of opening fence)
(?P<code>.*?)(?<=\n)                                     # the code block
(?P=fence)[ ]*$                                          # closing fence
""",
    re.MULTILINE | re.DOTALL | re.VERBOSE,
)
# blockprocessors.HashHeaderProcessor.RE, matched against one whole line. A line ending
# in a single backslash is therefore no heading, as it is not to the renderer.
_HASH_HEADER_RE = re.compile(r"(?P<level>#{1,6})(?P<header>(?:\\.|[^\\])*?)#*")
# blockprocessors.SetextHeaderProcessor.RE's second line: column zero, one character kind.
_SETEXT_UNDERLINE_RE = re.compile(r"(?:=+|-+)[ ]*")
# blockprocessors.HRProcessor.SEARCH_RE, matched against one whole line.
_HR_RE = re.compile(
    r"[ ]{0,3}(?=(?P<atomicgroup>(-+[ ]{0,2}){3,}|(_+[ ]{0,2}){3,}|(\*+[ ]{0,2}){3,}))"
    r"(?P=atomicgroup)[ ]*"
)
# blockprocessors.BlockQuoteProcessor.RE's marker, stripped once per nesting level.
_QUOTE_RE = re.compile(r"[ ]{0,3}>[ ]?")
# blockprocessors.OListProcessor.RE / UListProcessor.RE: a block that opens with one is a list.
_LIST_START_RE = re.compile(r"[ ]{0,3}(?:\d+\.|[*+-])[ ]+")
# OListProcessor.CHILD_RE (up to three spaces in) and INDENT_RE (four to seven): an item
# line, which starts an item wherever it sits in a list block.
_LIST_CHILD_RE = re.compile(r"[ ]{0,7}(?:\d+\.|[*+-])[ ]+")
# The same marker at any indentation: a line that may start an item somewhere.
_LIST_ITEM_RE = re.compile(r"[ ]*(?:\d+\.|[*+-])[ ]+")
# A definition four or more spaces in: `ReferenceProcessor.RE` accepts it only once a list
# item's indentation has been stripped, which the model does not do.
_DEF_INDENTED_RE = re.compile(r"[ ]{4,}\[[^\[\]]*\]:")
# blockprocessors.ReferenceProcessor.RE
_REF_DEF_RE = re.compile(
    r"^[ ]{0,3}\[([^\[\]]*)\]:[ ]*(?:\n[ ]*)?([^\s]+)[ ]*(?:\n[ ]*)?"
    r"(([\"'])(.*)\4[ ]*|\((.*)\)[ ]*)?$",
    re.MULTILINE,
)
# inlinepatterns.BACKTICK_RE (see `_code_span_end` for how the runs pair).
_BACKTICK_RE = re.compile(r"(?:(?<!\\)((?:\\{2})+)(?=`+)|(?<!\\)`)")
# inlinepatterns.ESCAPE_RE and Markdown.ESCAPED_CHARS, plus the `|` the tables extension adds.
_ESCAPE_RE = re.compile(r"\\(.)", re.DOTALL)
_ESCAPED_CHARS = frozenset("\\`*_{}[]()>#+-.!|")
# inlinepatterns.ReferenceInlineProcessor.RE_LINK
_REF_TAIL_RE = re.compile(r"\s?\[([^\]]*)\]", re.DOTALL)
# inlinepatterns.LinkInlineProcessor.RE_LINK (see `_link_target` for the rest of getLink).
_LINK_TAIL_RE = re.compile(r"""\(\s*(?:(<[^<>]*>)\s*(?:('[^']*'|"[^"]*")\s*)?\))?""", re.DOTALL)
# inlinepatterns.HTML_RE
_HTML_RE = re.compile(
    r"(<(\/?[a-zA-Z][^<>@ ]*( [^<>]*)?|!--(?:(?!<!--|-->).)*--|[?](?:(?!<[?]|[?]>).)*[?]"
    r"|!\[CDATA\[(?:(?!<!\[CDATA\[|\]\]>).)*\]\])>)",
    re.DOTALL,
)

# htmlparser.commentclose. The renderer's HTML preprocessor reads the page in order: a
# comment runs from `<!--` to the first close, and one with no close is a literal `<`. A
# comment that opens a line (at most three spaces in, outside any blockquote) is a raw
# block; any other is inline HTML, which `_HTML_RE` reads in place — but either way what it
# spans is consumed, so nothing inside it opens a comment or a raw block of its own.
_COMMENT_CLOSE_RE = re.compile(r"--!?>")
_LINE_START_RE = re.compile(r"[ ]{0,3}")
# A raw HTML block opens with a block-level start tag at most three spaces into a line —
# not inside a blockquote — and Python-Markdown parses no Markdown inside it.
# `markdown.util.BLOCK_LEVEL_ELEMENTS` (3.11) minus `hr`, which its HTMLExtractor treats
# as empty, so it opens nothing; the tag-name pattern is `html.parser`'s.
_BLOCK_TAGS = frozenset(
    ("address", "article", "aside", "blockquote", "details", "div", "dl", "fieldset")
    + ("figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6")
    + ("header", "hgroup", "main", "menu", "nav", "ol", "p", "pre", "section", "table", "ul")
    + ("canvas", "colgroup", "dd", "body", "dt", "group", "html", "iframe", "li", "legend")
    + ("math", "map", "noscript", "output", "object", "option", "progress", "script", "center")
    + ("style", "summary", "tbody", "td", "textarea", "tfoot", "th", "thead", "tr", "video")
)
_RAW_OPEN_RE = re.compile(r"^[ \t]{0,3}<([A-Za-z][^\t\n\r\f />\x00]*)", re.MULTILINE)
_TAG_OPEN_RE = re.compile(r"<([A-Za-z][^\t\n\r\f />\x00]*)")
# A pair of lines `tables.TableProcessor.test` could accept as header and separator: the
# header holds a pipe and the separator holds a pipe and nothing but `|`, `:`, `-` and
# spaces. This is a superset of the renderer's test, which also needs the cells to line
# up and the pair to open a block; the checker only ever refuses inside such a block.
_TABLE_SEPARATOR_RE = re.compile(r"[ |:-]*\|[ |:-]*")
_A_TAG_RE = re.compile(r"<a[\s/>]", re.IGNORECASE)
# The sources MkDocs turns into a directory's index.html (named in the error as the fix).
_INDEX_PAGES = ("index.md", "README.md")
_SCHEME_RE = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.-]*:|//)")

# Placeholders, each the length of what it replaces so offsets survive: a code span, and
# an escaped character (kept, shifted into the private-use area, so a target can be read).
_CODE = "\x00"
_ESC = "\x01"
_ESC_SHIFT = 0xE000


def _blank(match: re.Match[str]) -> str:
    """Replace a match with spaces, keeping its newlines so line numbers survive."""
    return re.sub(r"[^\n]", " ", match.group(0))


def _blank_spans(text: str, spans: tuple[tuple[int, int], ...]) -> str:
    for start, end in spans:
        text = text[:start] + re.sub(r"[^\n]", " ", text[start:end]) + text[end:]
    return text


def _normalize(source: str) -> str:
    """``preprocessors.NormalizeWhitespace``, minus the trailing blank lines it appends."""
    source = source.replace("\r\n", "\n").replace("\r", "\n").expandtabs(_TAB)
    return re.sub(r"(?<=\n) +\n", "\n", source)


def _comments(text: str) -> tuple[tuple[int, int], ...]:
    """Spans of the HTML comments in ``text``, read in order as ``html.parser`` reads them."""
    spans = []
    pos = 0
    while (start := text.find("<!--", pos)) != -1:
        close = _COMMENT_CLOSE_RE.search(text, start + 4)
        if close is None:
            break  # unclosed: a literal `<`, and no later `<!--` can close either
        spans.append((start, close.end()))
        pos = close.end()
    return tuple(spans)


def _raw_html_blocks(text: str) -> tuple[tuple[int, int], ...]:
    """Spans of the raw HTML blocks in ``text``, as Python-Markdown 3.11's HTMLExtractor cuts them.

    A block opens with a block-level start tag at the start of a line (up to three spaces in)
    and runs to the end tag that closes it, counting nested tags of the same name, or to the
    end of the page if none does — an unclosed ``<div>`` swallows the rest. A block-level
    start tag later on the closing line opens the next block at once. Where this misjudges,
    it treats too much as raw rather than too little, so a heading is lost and a link to it
    is reported.
    """
    spans = []
    pos = 0
    while (m := _RAW_OPEN_RE.search(text, pos)) is not None:
        if m.group(1).lower() not in _BLOCK_TAGS:
            pos = m.end()
            continue
        start = m.start()
        while True:
            tag = re.escape(m.group(1))
            tag_re = re.compile(rf"<(/?){tag}(?=[\s/>])[^>]*>", re.IGNORECASE)
            depth, end = 0, len(text)
            for t in tag_re.finditer(text, m.start(1) - 1):
                depth += -1 if t.group(1) else 1
                if depth == 0:
                    end = t.end()
                    break
            line_end = text.find("\n", end)
            line_end = len(text) if line_end == -1 else line_end
            tail = next(
                (
                    t
                    for t in _TAG_OPEN_RE.finditer(text, end, line_end)
                    if t.group(1).lower() in _BLOCK_TAGS
                ),
                None,
            )
            if tail is None:
                break
            m = tail
        spans.append((start, line_end))
        pos = line_end
    return tuple(spans)


# --- block structure ---------------------------------------------------------------------


@dataclass(frozen=True)
class _Line:
    start: int  # offset of the line in the page
    depth: int  # blockquote markers in front of it
    body: str  # the line inside its blockquotes


def _lines(text: str) -> list[_Line]:
    out, pos = [], 0
    for line in text.split("\n"):
        depth, body = 0, line
        while (m := _QUOTE_RE.match(body)) is not None:
            depth, body = depth + 1, body[m.end() :]
        out.append(_Line(pos, depth, body))
        pos += len(line) + 1
    return out


@dataclass(frozen=True)
class _Def:
    key: str  # as ReferenceProcessor stores it
    target: str
    first: int  # line indices, inclusive
    last: int


@dataclass
class _Unit:
    """A run of lines the renderer parses inline as one piece: a paragraph or a heading."""

    first: int  # line indices, inclusive
    last: int
    depth: int  # the deepest blockquote it has reached; a deeper line opens a new one
    heading: str | None = None  # the heading text, for an ATX or setext heading
    soft: set[int] = field(default_factory=set)  # lines that may start a block of their own
    code: set[int] = field(default_factory=set)  # lines that may be an indented code block


def _table_lines(lines: list[_Line]) -> set[int]:
    """Every line of every block that could be a table (see ``_TABLE_SEPARATOR_RE``)."""
    out: set[int] = set()
    i = 0
    while i + 1 < len(lines):
        head, sep = lines[i], lines[i + 1]
        if "|" in head.body and sep.depth == head.depth and _TABLE_SEPARATOR_RE.fullmatch(sep.body):
            while i < len(lines) and lines[i].body.strip():
                out.add(i)
                i += 1
        i += 1
    return out


def _units(lines: list[_Line], tables: set[int], problem) -> list[_Unit]:
    """Cut the page into inline units at the boundaries that hold in every context.

    A unit starts where the model is sure a block starts: at the top of the page, after a
    blank line, a heading or a rule, or where a deeper blockquote opens. That is also the
    only place a setext title is recognised. Inside a block that opens with a list item, as
    the list processors read it, each item line at most seven spaces in also starts a unit.

    Blockquote depth is tracked as ``BlockQuoteProcessor`` reads it: once a quote opens,
    the rest of its block belongs to it, lazy lines without a ``>`` included, until a blank
    line, or until a heading, rule or list item at a shallower depth splits the enclosing
    block first and so closes it.
    """
    units: list[_Unit] = []
    cur: _Unit | None = None
    open_ = 0  # the depth of the innermost open blockquote
    fresh = 0  # blocks at this depth and deeper open afresh at the next unit
    listy: dict[int, bool] = {}  # whether the open block at each depth is a list
    underlines: set[int] = set()

    def open_unit(i: int, level: int, *, item: bool = False) -> None:
        nonlocal cur, fresh
        body = lines[i].body
        if not item:
            for d in range(min(fresh, level), level + 1):
                listy[d] = d == level and bool(_LIST_START_RE.match(body))
        for d in [d for d in listy if d > level]:
            del listy[d]
        fresh = sys.maxsize
        cur = _Unit(i, i, level)
        units.append(cur)

    i = 0
    while i < len(lines):
        line = lines[i]
        body, depth = line.body, line.depth
        if not body.strip():
            cur, fresh, open_ = None, min(fresh, depth), depth
            i += 1
            continue
        if depth > open_:  # a blockquote opens here, and its first line opens a block
            cur, fresh, open_ = None, min(fresh, open_ + 1), depth
        atx = _HASH_HEADER_RE.fullmatch(body)
        rule = _HR_RE.fullmatch(body)
        if i in tables and (atx or rule):
            problem(line.start, "a heading or rule line inside a table-like block")
            atx = rule = None
        if atx or rule:
            # Split off at its own depth before any deeper blockquote sees it.
            if atx:
                units.append(_Unit(i, i, depth, heading=atx["header"].strip()))
            cur, fresh, open_ = None, min(fresh, depth), depth
            i += 1
            continue
        item = _LIST_CHILD_RE.match(body)
        if cur is not None and item and (listy.get(depth) or listy.get(open_)):
            level = depth if listy.get(depth) else open_
            open_ = level
            open_unit(i, level, item=True)
            i += 1
            continue
        if cur is None:
            nxt = lines[i + 1] if i + 1 < len(lines) else None
            if (
                depth == open_
                and fresh <= depth
                and not body.startswith(" " * _TAB)
                and nxt is not None
                and nxt.depth == depth
                and _SETEXT_UNDERLINE_RE.fullmatch(nxt.body)
            ):
                units.append(_Unit(i, i, depth, heading=body.strip()))
                fresh = min(fresh, depth)
                i += 2
                continue
            open_unit(i, open_)
            if body.startswith(" " * _TAB):
                cur.soft.add(i)  # may open an indented code block: see `_mark_code`
            i += 1
            continue
        cur.last = i
        if _LIST_ITEM_RE.match(body) or i in tables or i - 1 in underlines:
            cur.soft.add(i)
        if _SETEXT_UNDERLINE_RE.fullmatch(body):
            underlines.add(i)  # if the line above opens a block, the pair is a heading
        i += 1
    return units


def _mark_code(lines: list[_Line], units: list[_Unit]) -> None:
    """Mark the lines that may be an indented code block, and the line after each run.

    A block that opens four spaces in is a code block after a paragraph but continuation
    text inside a list, so its leading indented lines may be code, and the first line
    after them may open the next block. A block may open at a unit's first line or at any
    soft line.
    """
    for unit in units:
        if unit.heading is not None:
            continue
        run = False
        for k in range(unit.first, unit.last + 1):
            indented = lines[k].body.startswith(" " * _TAB)
            if (k == unit.first or k in unit.soft) and indented:
                run = True
            if run and indented:
                unit.code.add(k)
            elif run:
                run = False
                unit.soft.add(k)


def _definitions(lines: list[_Line], units: list[_Unit], tables: set[int], problem) -> list[_Def]:
    """The reference definitions the renderer certainly registers, read inside blockquotes.

    ``ReferenceProcessor`` reads each block, so this reads each unit, whose lines lie in one
    block or in blocks that a soft line separates. A definition that runs onto a soft line,
    sits in a table-like block, has a setext underline after it (the pair may be a heading)
    or is indented four spaces (a definition inside a list, code elsewhere) is refused: the
    renderer keeps the *last* definition of a key, so one the model cannot place could
    redirect a use it has checked.
    """
    out = []
    for unit in units:
        if unit.heading is not None:
            continue
        rows = range(unit.first, unit.last + 1)
        text = "\n".join(lines[k].body for k in rows)
        starts = _line_starts(text.split("\n"))
        placed: set[int] = set()
        for m in _REF_DEF_RE.finditer(text):
            first = unit.first + bisect.bisect_right(starts, m.start()) - 1
            last = unit.first + bisect.bisect_right(starts, max(m.end() - 1, m.start())) - 1
            placed.update(range(first, last + 1))
            nxt = lines[last + 1] if last + 1 < len(lines) else None
            if any(k in tables for k in range(first, last + 1)):
                problem(lines[first].start, "a reference definition inside a table-like block")
            elif any(k in unit.soft for k in range(first + 1, last + 1)):
                problem(
                    lines[first].start,
                    "a reference definition that runs onto a line which may start a new block",
                )
            elif nxt is not None and _SETEXT_UNDERLINE_RE.fullmatch(nxt.body):
                problem(
                    lines[first].start,
                    "a reference definition with a setext underline "
                    "after it, which may make it a heading instead",
                )
            else:
                # The renderer consumes these lines and splits the block around them, but a
                # tight list item glues the text on either side back into one inline run:
                # nothing may cross them either way.
                unit.soft.add(first)
                if last + 1 <= unit.last:
                    unit.soft.add(last + 1)
                out.append(
                    _Def(
                        m.group(1).strip().lower(), m.group(2).lstrip("<").rstrip(">"), first, last
                    )
                )
        for k in rows:
            if k not in placed and _DEF_INDENTED_RE.match(lines[k].body):
                problem(
                    lines[k].start,
                    "a reference definition indented four spaces or more, "
                    "which is a definition inside a list item and code elsewhere",
                )
    return out


# --- inline --------------------------------------------------------------------------------


def _code_span_end(text: str, begin: int) -> int | None:
    """Where the code span opened at ``begin`` ends: ``BacktickInlineProcessor``, verbatim.

    The opening run pairs with the next run of the same length; failing that, with the
    longest later run, the surplus opening ticks becoming code. Same result, same quirks.
    """
    last, start, max_ticks = len(text), begin, 0
    while start < last and text[start] == "`":
        max_ticks += 1
        start += 1
    longest_span = end = 0
    i = start
    while i < last:
        span_length = 0
        while i < last and text[i] == "`":
            span_length += 1
            i += 1
        if not span_length:
            i += 1
            continue
        if span_length == max_ticks:
            return i
        if span_length > longest_span:
            longest_span, end = span_length, i
    return end if longest_span else None


def _mask_code(text: str) -> tuple[str, list[tuple[int, int]]]:
    """``text`` with its code spans masked, and their spans, as the backtick pattern runs.

    An even run of backslashes before a backtick is escaped backslashes, so the backtick
    stays live; an odd run escapes it.
    """
    spans = []
    pos = 0
    while True:
        for m in _BACKTICK_RE.finditer(text, pos):
            if m.group(1):
                start, end = m.span()
                break
            end_ = _code_span_end(text, m.start())
            if end_ is not None:
                start, end = m.start(), end_
                spans.append((start, end))
                break
        else:
            return text, spans
        text = text[:start] + _CODE * (end - start) + text[end:]
        pos = end


def _mask_escapes(text: str) -> str:
    """``EscapeInlineProcessor``: an escaped character is no longer syntax."""

    def sub(m: re.Match[str]) -> str:
        c = m.group(1)
        return _ESC + chr(_ESC_SHIFT + ord(c)) if c in _ESCAPED_CHARS else m.group(0)

    return _ESCAPE_RE.sub(sub, text)


def _unescape(text: str) -> str:
    return re.sub(_ESC + "(.)", lambda m: chr(ord(m.group(1)) - _ESC_SHIFT), text, flags=re.DOTALL)


def _link_text_end(text: str, start: int) -> int | None:
    """Offset just past the ``]`` that closes the ``[`` at ``start``: ``getText``.

    Brackets nest to any depth, so ``[[array[i]] result][key]`` is one reference use. Escaped
    brackets and code spans are already masked.
    """
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "[":
            depth += 1
        elif text[i] == "]":
            depth -= 1
            if depth == 0:
                return i + 1
    return None


def _link_target(text: str, index: int) -> tuple[str, int] | None:
    """``LinkInlineProcessor.getLink`` at ``index``: the href and the end of ``(…)``, or None.

    Ported line for line, title tracking included, because the title rules decide where
    the href ends: ``[a](x (t))`` links to ``x (t)``, ``[a](b c d)`` to ``b c d``.
    """
    m = _LINK_TAIL_RE.match(text, index)
    if m is None:
        return None
    if m.group(1):
        return _unescape(m.group(1)[1:-1].strip()).strip(), m.end()
    bracket_count = backtrack_count = 1
    start_index = index = m.end()
    last_bracket = -1
    quote = alt_quote = None
    start_quote = exit_quote = start_alt_quote = exit_alt_quote = -1
    ignore_matches = False
    last = ""
    href = ""
    for pos in range(index, len(text)):
        c = text[pos]
        if c == "(":
            if not ignore_matches:
                bracket_count += 1
            elif backtrack_count > 0:
                backtrack_count -= 1
        elif c == ")":
            if (exit_quote != -1 and quote == last) or (exit_alt_quote != -1 and alt_quote == last):
                bracket_count = 0
            elif not ignore_matches:
                bracket_count -= 1
            elif backtrack_count > 0:
                backtrack_count -= 1
                if backtrack_count == 0:
                    last_bracket = index + 1
        elif c in ("'", '"'):
            if not quote:
                ignore_matches = True
                backtrack_count = bracket_count
                bracket_count = 1
                start_quote = index + 1
                quote = c
            elif c != quote and not alt_quote:
                start_alt_quote = index + 1
                alt_quote = c
            elif c == quote:
                exit_quote = index + 1
            elif alt_quote and c == alt_quote:
                exit_alt_quote = index + 1
        index += 1
        if bracket_count == 0:
            if exit_quote >= 0 and quote == last:
                href = text[start_index : start_quote - 1]
            elif exit_alt_quote >= 0 and alt_quote == last:
                href = text[start_index : start_alt_quote - 1]
            else:
                href = text[start_index : index - 1]
            break
        if c != " ":
            last = c
    if bracket_count != 0 and backtrack_count == 0:
        href = text[start_index : last_bracket - 1]
        index = last_bracket
        bracket_count = 0
    if bracket_count != 0:
        return None
    return _unescape(href).strip(), index


def _key_tail(text: str, index: int) -> tuple[str, int] | None:
    """``evalId``: the key and the end of ``[key]``, or None."""
    m = _REF_TAIL_RE.match(text, index)
    return None if m is None else (m.group(1), m.end())


def _ref_tail(text: str, index: int) -> tuple[str, int] | None:
    """``_key_tail`` for a reference use the checker reports, or None.

    One exception of the checker's own: when ``[key](…)`` forms a link, as in
    ``[text][key](url)``, no literal reference is left on the page — with ``key`` undefined
    the renderer shows ``[text]`` and a link, and a defined one takes the span before the
    link pass (see ``_doc``) — so that is not a reference use here.
    """
    t = _key_tail(text, index)
    if t is not None and text.startswith("(", t[1]) and _link_target(text, t[1]) is not None:
        return None
    return t


def _bracketed(text: str, tail, image: bool | None = None) -> list[tuple[int, int, str, int]]:
    """``(start, text_end, payload, end)`` for each ``[text]`` that ``tail`` reads on from.

    Scans left to right as the inline pattern does: a ``[`` whose text does not close or is
    not followed by ``tail`` is retried one character on; a match resumes after its end.
    With ``image`` set, only a ``[`` after a ``!`` (True) or not after one (False) opens.
    """
    out = []
    pos = 0
    while (start := text.find("[", pos)) != -1:
        pos = start + 1
        if image is not None and (start > 0 and text[start - 1] == "!") != image:
            continue
        end = _link_text_end(text, start)
        if end is None or (t := tail(text, end)) is None:
            continue
        out.append((start, end, t[0], t[1]))
        pos = t[1]
    return out


class _Tags(HTMLParser):
    """Start tags as ``html.parser`` reads them: names lower-cased, values unescaped."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str], tuple[int, int]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        first: dict[str, str] = {}
        for name, value in attrs:  # a browser keeps the first of a repeated attribute
            first.setdefault(name, value or "")
        self.tags.append((tag, first, self.getpos()))

    handle_startendtag = handle_starttag


def _start_tag(source: str) -> tuple[str, dict[str, str]] | None:
    """The single start tag ``source`` is, or None if ``html.parser`` reads anything else."""
    parser = _Tags()
    parser.feed(source)
    parser.close()
    if len(parser.tags) == 1 and parser.get_starttag_text() == source:
        return parser.tags[0][0], parser.tags[0][1]
    return None


# --- one page ------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Ref:
    start: int
    source: str  # as written
    shown: str  # the key the message names: the key, or the text of a collapsed use
    key: str | None  # as the renderer looks it up; None when it cannot match any definition


@dataclass(frozen=True)
class _Doc:
    path: Path
    starts: tuple[int, ...]  # line offsets
    headings: tuple[str, ...]  # raw heading texts, in page order
    definitions: frozenset[str]
    links: tuple[tuple[int, str], ...]  # Markdown links, images and definitions: (offset, target)
    refs: tuple[_Ref, ...]
    tags: tuple[tuple[int, str, dict[str, str]], ...]  # HTML start tags: (offset, name, attrs)
    problems: tuple[str, ...]

    def line_of(self, offset: int) -> int:
        return bisect.bisect_right(self.starts, offset)


def _line_starts(lines: list[str]) -> list[int]:
    out, pos = [], 0
    for line in lines:
        out.append(pos)
        pos += len(line) + 1
    return out


@cache
def _doc(path: Path) -> _Doc:
    text = _normalize(path.read_text(encoding="utf-8"))
    lines_ = text.split("\n")
    starts = tuple(_line_starts(lines_))
    problems: list[str] = []

    def problem(offset: int, what: str) -> None:
        line = bisect.bisect_right(starts, offset)
        problems.append(f"{_rel(path)}:{line}: {_UNSUPPORTED}: {what}")

    fenced = _FENCED_BLOCK_RE.sub(_blank, text)
    comments = _comments(fenced)
    raw = _raw_html_blocks(_blank_spans(fenced, comments))
    blocks = _blank_spans(
        fenced,
        tuple(
            (start, end)
            for start, end in comments
            if _LINE_START_RE.fullmatch(fenced, fenced.rfind("\n", 0, start) + 1, start)
        ),
    )
    md = _blank_spans(blocks, raw)
    lines = _lines(md)
    tables = _table_lines(lines)
    units = _units(lines, tables, problem)
    defs = _definitions(lines, units, tables, problem)
    _mark_code(lines, units)
    definitions = frozenset(d.key for d in defs)

    links: list[tuple[int, str]] = [(lines[d.first].start, d.target) for d in defs]
    refs: list[_Ref] = []
    tags: list[tuple[int, str, dict[str, str]]] = []

    def lookup_of(view: str, start: int, end: int, key: str) -> str | None:
        lookup = re.sub(r"\s+", " ", (key or view[start + 1 : end - 1]).lower())
        # An escape or code span in an id never matches a definition.
        return None if _CODE in lookup or _ESC in lookup else lookup

    def refs_in(view: str, a: int, consumed: list[tuple[int, int]]) -> None:
        for start, end, key, stop in _bracketed(view, _ref_tail):
            lookup = lookup_of(view, start, end, key)
            if lookup in definitions:
                consumed.append((end, stop))
            source = blocks[a + start : a + stop]
            shown = blocks[a + end : a + stop].strip()[1:-1] or blocks[a + start + 1 : a + end - 1]
            refs.append(_Ref(a + start, source, shown, lookup))

    for unit in units:
        a = lines[unit.first].start
        b = lines[unit.last].start + len(lines_[unit.last])
        soft = [lines[k].start for k in sorted(unit.soft)]

        def crosses(start: int, end: int, what: str, a: int = a, soft: list[int] = soft) -> None:
            i = bisect.bisect_right(soft, a + start)
            if i < len(soft) and soft[i] < a + end:
                problem(a + start, f"{what} that runs onto a line which may start a new block")

        chunk = blocks[a:b]
        for d in defs:
            if unit.first <= d.first <= unit.last:  # a definition is not inline text
                s_, e_ = lines[d.first].start - a, lines[d.last].start + len(lines_[d.last]) - a
                chunk = chunk[:s_] + _CODE * (e_ - s_) + chunk[e_:]
        view, spans = _mask_code(chunk)
        for start, end in spans:
            crosses(start, end, "a code span")
        view = _mask_escapes(view)
        consumed: list[tuple[int, int]] = []
        # The renderer's pattern order, as in `_heading_text`: a reference whose key is
        # defined takes its span before the link pass, which reads its text on its own.
        # An image reference runs after the link pass, so it takes nothing here.
        taken = [
            (start, end, stop)
            for start, end, key, stop in _bracketed(view, _key_tail, image=False)
            if lookup_of(view, start, end, key) in definitions
        ]
        left = view
        for start, end, stop in taken:
            left = left[:start] + _CODE * (stop - start) + left[stop:]
            consumed.append((end, stop))
        found = _bracketed(left, _link_target) + [
            (t + 1 + s, t + 1 + e, target, t + 1 + p)
            for t, t_end, _ in taken
            for s, e, target, p in _bracketed(view[t + 1 : t_end - 1], _link_target)
        ]
        for start, end, target, stop in sorted(found):
            crosses(start, stop, "a link")
            links.append((a + start, target))
            consumed.append((end, stop))
        before = len(refs)
        refs_in(view, a, consumed)
        for ref in refs[before:]:
            crosses(ref.start - a, ref.start - a + len(ref.source), "a reference")
        for start, end in consumed:
            view = view[:start] + _CODE * (end - start) + view[end:]
        for m in _HTML_RE.finditer(view):
            if m.group(0).startswith("<!") or m.group(0).startswith("</"):
                continue
            crosses(m.start(), m.end(), "an inline HTML tag")
            source = blocks[a + m.start() : a + m.end()]
            tag = _start_tag(source)
            if tag is None:
                if _A_TAG_RE.match(source):
                    problem(a + m.start(), "an <a> tag html.parser cannot read as one start tag")
                continue
            line = bisect.bisect_right(starts, a + m.start()) - 1
            name, attrs = tag
            if line in unit.code and ("id" in attrs or (name == "a" and "name" in attrs)):
                problem(a + m.start(), "an HTML anchor on a line of an indented block")
                continue
            tags.append((a + m.start(), name, attrs))

    for a, b in raw:
        chunk = blocks[a:b]
        parser = _Tags()
        parser.feed(chunk)
        parser.close()
        if len(_A_TAG_RE.findall(chunk)) != sum(1 for t in parser.tags if t[0] == "a"):
            problem(a, "an <a> tag html.parser cannot read as one start tag")
        for name, attrs, (row, col) in parser.tags:
            offset = starts[bisect.bisect_right(starts, a) - 1 + row - 1] + col
            tags.append((offset, name, attrs))
        # Raw HTML is passed through as text, so a link or reference here is literal: read
        # them anyway, which can only report something, never hide it.
        links.extend((a + s, t) for s, _, t, _ in _bracketed(chunk, _link_target))
        refs_in(chunk, a, [])

    headings = tuple(u.heading for u in units if u.heading is not None)
    return _Doc(
        path,
        starts,
        headings,
        definitions,
        tuple(links),
        tuple(refs),
        tuple(tags),
        tuple(problems),
    )


def _pages() -> list[Path]:
    return sorted(p for p in DOCS.rglob("*.md") if p not in _EXCLUDED)


def _rel(path: Path) -> str:
    return path.relative_to(DOCS.parent).as_posix()


def _slugify(value: str) -> str:
    """``markdown.extensions.toc.slugify`` with its default ``-`` separator."""
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^\w\s-]", "", value).strip().lower()
    return re.sub(r"[-\s]+", "-", value)


def _heading_text(raw: str, definitions: frozenset[str]) -> str:
    """The plain text the toc extension slugifies: the heading as rendered, tags stripped.

    The inline structure is read with the passes a paragraph gets: code spans, escapes,
    links by ``getLink`` and reference uses. A link, or a reference whose key is defined,
    reduces to its text; an image, of either kind, renders no text. An undefined reference
    stays on the page as written, brackets and all, and so in the slug.
    """
    view, spans = _mask_code(raw)
    view = _mask_escapes(view)

    def key(start: int, end: int, tail: str) -> str:
        return re.sub(r"\s+", " ", (tail or view[start + 1 : end - 1]).lower())

    def any_tail(text: str, index: int) -> tuple[str, int]:
        return "", index

    # The inline patterns in priority order, each over what the ones before left: reference,
    # link, image link, image reference, short image reference. A short reference's text is
    # its key, which slugifies as the bracketed source does, so it needs no stage.
    stages = (
        (_key_tail, False, True),
        (_link_target, False, False),
        (_link_target, True, False),
        (_key_tail, True, True),
        (any_tail, True, True),
    )
    links: list[tuple[int, int, int]] = []  # (start, text_end, end); an image has no text
    left = view  # what the stages so far have not consumed
    for tail, image, by_key in stages:
        for start, end, payload, stop in _bracketed(left, tail, image):
            if by_key and key(start, end, payload) not in definitions:
                continue
            begin = start - 1 if image else start
            links.append((begin, -1 if image else end, stop))
            left = left[:begin] + _CODE * (stop - begin) + left[stop:]

    def prose(a: int, b: int) -> str:
        text = _HTML_RE.sub("", view[a:b]).replace("*", "")
        text = re.sub(r"(?<!\w)_+|_+(?!\w)", "", text)  # emphasis, not snake_case
        return html.unescape(_unescape(text))

    def plain(a: int, b: int) -> str:
        out, pos = [], a
        for start, end in spans:
            if a <= start and end <= b:
                code = raw[start:end]
                ticks = min(len(code) - len(code.lstrip("`")), len(code) - len(code.rstrip("`")))
                out += [prose(pos, start), code[ticks : len(code) - ticks].strip()]
                pos = end
        return "".join(out) + prose(pos, b)

    out, pos = [], 0
    for start, end, stop in sorted(links):
        out.append(plain(pos, start))
        if end != -1:  # link text is inline Markdown of its own: an image in it adds nothing
            out.append(_heading_text(raw[start + 1 : end - 1], definitions))
        pos = stop
    return "".join(out) + plain(pos, len(raw))


@cache
def _anchors(path: Path) -> frozenset[str]:
    """Every fragment a page serves: heading slugs, any element's ``id``, an ``<a name>``."""
    doc = _doc(path)
    ids: set[str] = set()
    for raw in doc.headings:
        slug = _slugify(_heading_text(raw, doc.definitions))
        while slug in ids or not slug:
            m = re.match(r"^(.*)_([0-9]+)$", slug)
            slug = f"{m.group(1)}_{int(m.group(2)) + 1}" if m else f"{slug}_1"
        ids.add(slug)
    for _, name, attrs in doc.tags:
        if attrs.get("id"):
            ids.add(attrs["id"])
        if name == "a" and attrs.get("name"):
            ids.add(attrs["name"])
    return frozenset(ids)


def _target_problem(source: Path, target: str) -> str | None:
    """Why ``target`` (as written in ``source``) does not resolve, or None if it does."""
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    if not target or _SCHEME_RE.match(target):
        return None
    if target.startswith("/"):
        return "absolute path (mkdocs resolves links relative to the page)"
    path_part, _, fragment = target.partition("#")
    path_part = path_part.split("?", 1)[0]
    if path_part:
        resolved = (source.parent / path_part).resolve()
        try:
            resolved.relative_to(DOCS)
        except ValueError:
            return "target is outside docs/, so it is not part of the published site"
        if resolved in _EXCLUDED:
            return "target is excluded from the site by mkdocs.yml exclude_docs"
        if not resolved.exists():
            return "no such file"
        if resolved.is_dir():
            # MkDocs only rewrites links to source files: `dir/` is left as is and
            # `--strict` aborts on it ("unrecognized relative link"), index page or not.
            index = [n for n in _INDEX_PAGES if (resolved / n).is_file()]
            hint = f"link {path_part.rstrip('/')}/{index[0]}" if index else "no index page"
            return f"directory link, which mkdocs leaves unresolved ({hint})"
    else:
        resolved = source
    is_page = resolved.suffix == ".md" and resolved.is_file()
    if fragment and is_page and fragment not in _anchors(resolved):
        return f"no heading or HTML anchor '#{fragment}' in {_rel(resolved)}"
    return None


def _raw_html_problem(source: Path, href: str) -> str | None:
    """Why a raw ``<a href>`` breaks on the site, or None if it does not.

    MkDocs rewrites Markdown links but passes raw HTML through untouched, so a relative
    path there is resolved by the browser against the *rendered* page URL, not against
    the source file. Only a same-page ``#fragment`` (checked) or a URL with a scheme
    (left to the advisory external check) is safe; any other target is refused.
    """
    if not href or _SCHEME_RE.match(href):
        return None
    if href.startswith("#"):
        return _target_problem(source, href)
    return "raw HTML href is not rewritten by mkdocs; write it as a Markdown link"


def _undefined_references(path: Path) -> list[str]:
    doc = _doc(path)
    return [
        f"{_rel(path)}:{doc.line_of(ref.start)}: reference-style link "
        f"{' '.join(ref.source.split())} has no [{ref.shown}]: definition "
        "in this file, so it renders as literal text"
        for ref in doc.refs
        if ref.key not in doc.definitions
    ]


def _broken_targets(path: Path) -> list[str]:
    doc = _doc(path)
    out = []
    for start, target in doc.links:
        if problem := _target_problem(path, target):
            out.append(f"{_rel(path)}:{doc.line_of(start)}: link to '{target}': {problem}")
    for start, name, attrs in doc.tags:
        if name == "a" and "href" in attrs and (problem := _raw_html_problem(path, attrs["href"])):
            out.append(f"{_rel(path)}:{doc.line_of(start)}: <a href='{attrs['href']}'>: {problem}")
    return out


def _unsupported(path: Path) -> list[str]:
    return list(_doc(path).problems)


# --- the corpus ------------------------------------------------------------------------


def test_walk_covers_the_site_and_skips_the_prd() -> None:
    """Sanity: the walk is the published corpus (guards a broken DOCS path or glob)."""
    pages = _pages()
    assert len(pages) >= 50, f"expected the docs corpus; found only {len(pages)} pages"
    assert DOCS / "analysis" / "parity-gallery.md" in pages
    assert DOCS / "PRD.md" not in pages


def test_every_page_stays_inside_the_modelled_forms() -> None:
    """No page uses a form the checker refuses, so the two checks below can vouch for it."""
    problems = [p for page in _pages() for p in _unsupported(page)]
    assert not problems, "\n".join(problems)


def test_no_undefined_reference_style_links() -> None:
    """No ``[text][key]`` without a ``[key]: …`` definition — mkdocs cannot see these."""
    problems = [p for page in _pages() for p in _undefined_references(page)]
    assert not problems, "\n".join(problems)


def test_internal_link_targets_and_anchors_resolve() -> None:
    """Every relative link names a real page in the site and a real anchor on it."""
    problems = [p for page in _pages() for p in _broken_targets(page)]
    assert not problems, "\n".join(problems)


# --- the checker itself ----------------------------------------------------------------


def _check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pages: dict[str, str]) -> list[str]:
    """Run every check over a synthetic ``docs/`` tree under ``tmp_path``."""
    docs = tmp_path / "docs"
    for name, body in pages.items():
        (docs / name).parent.mkdir(parents=True, exist_ok=True)
        (docs / name).write_text(body, encoding="utf-8")
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "DOCS", docs)
    monkeypatch.setattr(module, "_EXCLUDED", {docs / "PRD.md"})
    _doc.cache_clear()
    _anchors.cache_clear()
    try:
        files = sorted(p for p in docs.rglob("*.md") if p.name != "PRD.md")
        return [
            p
            for f in files
            for p in _unsupported(f) + _undefined_references(f) + _broken_targets(f)
        ]
    finally:
        _doc.cache_clear()
        _anchors.cache_clear()


def test_flags_an_undefined_reference_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = _check(tmp_path, monkeypatch, {"a.md": "# A\n\nSee [McCann 2010][mccann2010].\n"})
    assert out == [
        "docs/a.md:3: reference-style link [McCann 2010][mccann2010] has no [mccann2010]: "
        "definition in this file, so it renders as literal text"
    ]


def test_defined_references_and_code_are_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (
        "# A\n\n"
        "See [McCann 2010][MCCANN2010] and [McCann 2010][].\n\n"
        "[mccann2010]: https://doi.org/10.1016/j.bpj.2010.04.063\n"
        "[McCann 2010]: #a\n\n"
        "Inline `data['condition_id'][mask] = x` and ``a[b][c]`` are code.\n\n"
        "```python\nx = table['a'][mask]\n```\n\n"
        "<!-- [commented][out] -->\n"
        "Citations [Hellenkamp2018], [Lee2005] are plain text, not references.\n"
    )
    assert _check(tmp_path, monkeypatch, {"a.md": body}) == []


def test_definition_target_and_title_may_sit_on_the_next_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Python-Markdown reads ``[k]:`` + an indented target line as one definition."""
    body = (
        "# A\n\n"
        "See [x][k], [y][t] and [z][g].\n\n"
        "[k]:\n    https://example.org\n"
        '[t]: #a\n    "Title on the next line"\n'
        "[g]:\n    sub/missing.md\n\n"
        "[Götz2022]: eleven tools (14 analyses) is prose, not a definition, so [w][Götz2022]\n"
    )
    out = _check(tmp_path, monkeypatch, {"a.md": body})
    assert len(out) == 2, out
    assert out[0].startswith("docs/a.md:12: reference-style link [w][Götz2022]")
    assert out[1].startswith("docs/a.md:9: link to 'sub/missing.md': no such file")


def test_adjacent_bracket_pairs_form_a_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``[A][B]`` and ``[A] [B]`` are both reference uses to Python-Markdown."""
    out = _check(tmp_path, monkeypatch, {"a.md": "# A\n\n[Hellenkamp2018][Lee2005] and [X] [Y]\n"})
    assert len(out) == 2 and "lee2005" in out[0].lower() and "[Y]" in out[1]


def test_flags_missing_pages_and_anchors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pages = {
        "a.md": (
            "# Page A\n\n## The `tdp` view & more\n\n## Dup\n\n## Dup\n\n"
            '- <a id="mccann2010"></a>**[McCann 2010]** ...\n\n'
            "[ok](#page-a) [ok](#the-tdp-view-more) [ok](#dup_1) [ok](#mccann2010) "
            "[ok](sub/b.md#section-b) [ok](https://example.org/x.md#nope)\n"
            "[bad](#nope)\n[bad](sub/missing.md)\n[bad](sub/b.md#nope)\n[bad](PRD.md)\n"
            "[bad](../outside.md)\n"
        ),
        "sub/b.md": "# B\n\n## Section B\n",
        "PRD.md": "# PRD\n",
    }
    out = _check(tmp_path, monkeypatch, pages)
    assert [o.split(":", 2)[1] for o in out] == ["12", "13", "14", "15", "16"], out
    assert "'#nope'" in out[0] and "no such file" in out[1] and "'#nope' in docs/sub/b.md" in out[2]


def test_deleting_an_inline_anchor_breaks_its_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The #207 bibliography form: removing ``<a id>`` leaves its inline links dangling."""
    with_anchor = '# G\n\nPer [McCann 2010](#mccann2010).\n\n- <a id="mccann2010"></a>McCann 2010\n'
    assert _check(tmp_path, monkeypatch, {"g.md": with_anchor}) == []
    without = with_anchor.replace('<a id="mccann2010"></a>', "")
    out = _check(tmp_path, monkeypatch, {"g.md": without})
    assert len(out) == 1 and out[0].startswith("docs/g.md:3:") and "#mccann2010" in out[0]


def test_a_directory_link_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """MkDocs leaves ``dir/`` unrewritten and ``--strict`` aborts on it, index page or not."""
    pages = {
        "a.md": "# A\n\n[ok](withreadme/README.md#r) [bad](withreadme/) [bad](bare/)\n",
        "withreadme/README.md": "# R\n",
        "bare/page.md": "# P\n",
    }
    out = _check(tmp_path, monkeypatch, pages)
    assert len(out) == 2, out
    assert "'withreadme/'" in out[0] and "link withreadme/README.md" in out[0]
    assert "'bare/'" in out[1] and "no index page" in out[1]


def test_raw_html_links_are_checked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """MkDocs never rewrites a raw ``<a href>``: a fragment is checked, a path is refused."""
    body = (
        '# A\n\n<a id="x"></a>\n'
        '<a href="#x">ok</a> <a href="https://example.org/">ok</a>\n'
        '<a href="#nope">bad</a>\n<a href="b.md">bad</a>\n'
        '`<a href="b.md">code</a>` is code.\n'
    )
    out = _check(tmp_path, monkeypatch, {"a.md": body, "b.md": "# B\n"})
    assert len(out) == 2, out
    assert out[0].startswith("docs/a.md:5: ") and "'#nope'" in out[0]
    assert out[1].startswith("docs/a.md:6: ") and "Markdown link" in out[1]


def test_link_text_nests_brackets_to_any_depth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Python-Markdown counts brackets without bound, so a deeply nested text is still a link."""
    body = (
        "# A\n\n"
        "See [[array[i]] result][missing] and [[a[b[c]]] d][k].\n\n"
        "Inline [[x[y]] z](nope.md) and [[x[y]] z](#a).\n\n"
        r"Escaped \[ is no bracket: [a \[ b][k]." + "\n\n"
        "A stray [ does not reach past the paragraph.\n\n"
        "[late][missing2]\n\n"
        "[k]: #a\n"
    )
    out = _check(tmp_path, monkeypatch, {"a.md": body})
    assert len(out) == 3, out
    assert out[0].startswith("docs/a.md:3: reference-style link [[array[i]] result][missing]")
    assert out[1].startswith("docs/a.md:11: reference-style link [late][missing2]")
    assert out[2].startswith("docs/a.md:5: link to 'nope.md': no such file")


def test_raw_html_blocks_hold_no_headings_or_definitions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Python-Markdown passes a raw HTML block through unparsed (``md_in_html`` is off)."""
    body = (
        "# Real\n\n"
        "<div>\n# Ghost\n[gk]: #real\n</div>\n\n"
        '<DIV class="x">\n<div>\n</div>\n# Nested Ghost\n</div>\n'
        "# After\n\n"
        "text\n  <details><summary>s</summary>\n\n## Para Ghost\n\n"
        "</details><p>\n# Tail Ghost\n</p>\n\n"
        "<span>\n# Inline Kept\n</span>\n\n"
        "> <div>\n> # Quoted Kept\n> </div>\n\n"
        "<hr>\n# Hr Kept\n\n"
        "[ok](#real) [ok](#after) [ok](#inline-kept) [ok](#quoted-kept) "
        "[ok](#hr-kept) [ok](#in-raw)\n"
        "[bad](#ghost)\n[bad](#nested-ghost)\n[bad](#para-ghost)\n[bad](#tail-ghost)\n"
        "[bad][gk]\n\n"
        '<section>\n<a id="in-raw"></a>\n# Unclosed Ghost\n'
    )
    out = _check(tmp_path, monkeypatch, {"a.md": body})
    assert [o.split(":", 2)[1] for o in out] == ["40", "36", "37", "38", "39"], out
    assert "[bad][gk]" in out[0]
    assert "'#ghost'" in out[1] and "'#nested-ghost'" in out[2]
    assert "'#para-ghost'" in out[3] and "'#tail-ghost'" in out[4]


# --- forms a naive parse reads differently from the renderer (#511) ---------------------
# Each body below was rendered with Python-Markdown 3.11 and mkdocs' default extensions.
# The first five passed silently at 60061f6, one per review finding; the rest pin forms the
# parse models now, and the refusals that bound it.


def test_a_fence_inside_a_blockquote_is_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``fenced_code`` works at the root only, so a quoted fence hides nothing."""
    body = (
        "# A\n\n"
        "> ~~~\n> see [x][missing]\n> ~~~\n\n"
        "> ```\n> [y][missing] sits in a code span, since the backticks pair\n> ```\n"
    )
    out = _check(tmp_path, monkeypatch, {"a.md": body})
    assert len(out) == 1, out
    assert out[0].startswith("docs/a.md:4: reference-style link [x][missing]")


def test_name_is_an_anchor_only_on_an_a_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A browser scrolls to any element's ``id`` but to a ``name`` only on ``<a>``."""
    body = (
        "# A\n\n"
        '<div name="ghost"></div>\n\n'
        'Text <span name="ghost2">s</span> <a name="real"></a> <b id="any"></b>\n\n'
        "[bad](#ghost) [bad](#ghost2) [ok](#real) [ok](#any)\n"
    )
    out = _check(tmp_path, monkeypatch, {"a.md": body})
    assert len(out) == 2, out
    assert "'#ghost'" in out[0] and "'#ghost2'" in out[1]


def test_an_unquoted_href_is_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``html.parser`` reads the attributes, so an unquoted value is a value."""
    body = (
        "# A\n\n"
        "<a href=missing.md>x</a> <a href=#nope>y</a> <a href=#a>ok</a>\n\n"
        "<p><a href=raw.md>z</a></p>\n"
    )
    out = _check(tmp_path, monkeypatch, {"a.md": body})
    assert len(out) == 3, out
    assert out[0].startswith("docs/a.md:3: <a href='missing.md'>") and "Markdown link" in out[0]
    assert out[1].startswith("docs/a.md:3: <a href='#nope'>")
    assert out[2].startswith("docs/a.md:5: <a href='raw.md'>")


def test_a_setext_underline_must_start_at_column_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``Text`` over an indented ``---`` is a paragraph and a rule, not a heading."""
    body = "# A\n\nText\n  ---\n\nReal\n---\n\n[bad](#text) [ok](#real)\n"
    out = _check(tmp_path, monkeypatch, {"a.md": body})
    assert len(out) == 1 and "'#text'" in out[0], out


def test_an_escaped_backslash_leaves_the_bracket_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only an odd run of backslashes escapes the ``[`` or backtick after it."""
    body = "# A\n\n\\\\[text][missing], \\[esc][k] and \\\\`[code][m3]`\n"
    out = _check(tmp_path, monkeypatch, {"a.md": body})
    assert len(out) == 1, out
    assert out[0].startswith("docs/a.md:3: reference-style link [text][missing]")


def test_a_comment_hides_the_tags_inside_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A comment inside a raw block hides a closing tag, so the block runs on past it."""
    body = "# A\n\n<div>\nx <!-- </div> -->\n# Ghost\n</div>\n\n[bad](#ghost)\n"
    out = _check(tmp_path, monkeypatch, {"a.md": body})
    assert len(out) == 1 and "'#ghost'" in out[0], out


def test_an_undefined_reference_stays_in_its_heading_slug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A heading slug is taken from the rendered text, where an undefined use is literal."""
    body = (
        "# A\n\n## See [x][k]\n\n## Also [y][missing]\n\n[k]: #a\n\n"
        "[ok](#see-x) [bad](#also-y) [ok](#also-ymissing)\n"
    )
    out = _check(tmp_path, monkeypatch, {"a.md": body})
    assert len(out) == 2, out
    assert out[0].startswith("docs/a.md:5: reference-style link [y][missing]")
    assert out[1].startswith("docs/a.md:9: ") and "'#also-y'" in out[1]


@pytest.mark.parametrize(
    ("body", "line", "what"),
    [
        ("Text `code\n- item` more\n", 3, "a code span that runs onto a line"),
        ("| a | b |\n|---|---|\n# c\n", 5, "a heading or rule line inside a table-like"),
        ('Para\n\n    <a id="x"></a>\n', 5, "an HTML anchor on a line of an indented block"),
        ("See <a href='x.md>y</a> here.\n", 3, "an <a> tag html.parser cannot read"),
        ("- item\n\n        [k]: #a\n", 5, "a reference definition indented four spaces"),
    ],
    ids=["crosses-a-block-start", "heading-in-a-table", "indented-anchor", "broken-a", "deep-def"],
)
def test_a_form_outside_the_model_fails_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str, line: int, what: str
) -> None:
    """A form whose rendering turns on context the model does not track is refused by name."""
    out = _check(tmp_path, monkeypatch, {"a.md": "# A\n\n" + body})
    assert out[0].startswith(f"docs/a.md:{line}: {_UNSUPPORTED}: {what}"), out


def test_a_heading_slug_reads_links_as_the_renderer_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A link target is read by ``getLink``, and an image adds nothing to the slug."""
    body = (
        "# A\n\n"
        "## [X](https://example.com/a(b)c)\n\n"
        "## A ![alt](https://example.org/x.png) B\n\n"
        "## [a](https://example.org/b c d) e\n\n"
        "[ok](#x) [ok](#a-b) [ok](#a-e) [bad](#xc) [bad](#a-alt-b)\n"
    )
    out = _check(tmp_path, monkeypatch, {"a.md": body})
    assert len(out) == 2, out
    assert out[0].startswith("docs/a.md:9: ") and "'#xc'" in out[0]
    assert out[1].startswith("docs/a.md:9: ") and "'#a-alt-b'" in out[1]


def test_a_defined_reference_takes_its_span_before_the_link_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ReferenceInlineProcessor`` runs before ``LinkInlineProcessor`` and an undefined key yields.

    A defined ``[a][k]`` consumes its span, so a ``(…)`` after it is text; its own text is
    read by the later patterns alone. An undefined one consumes nothing, and ``[k](…)`` is a
    link. After ``![i]`` it is a link whatever ``k`` is: an image reference runs after the
    link pass, and the reference pattern skips a ``[`` after ``!``, so ``[u]`` pairs with
    ``[g]`` below.
    """
    defined = (
        "# A\n\n"
        "[a][k](missing.md), [b] [k](gone.md), [c][](lost.md) and ![i][k](img.png)\n\n"
        "[[d](nested.md)][k] and [[e](x ][k] y.md)\n\n"
        "![f][u]\n[g] [k](far.md)\n\n"
        "[k]: #a\n[c]: #a\n"
    )
    out = _check(tmp_path, monkeypatch, {"a.md": defined})
    assert out == [
        "docs/a.md:7: reference-style link [f][u] has no [u]: definition in this file, "
        "so it renders as literal text",
        "docs/a.md:3: link to 'img.png': no such file",
        "docs/a.md:5: link to 'nested.md': no such file",
        "docs/a.md:8: link to 'far.md': no such file",
    ], out
    undefined = "# A\n\n[a][u](missing.md) and [b][](gone.md)\n"
    out = _check(tmp_path, monkeypatch, {"a.md": undefined})
    assert out == [
        "docs/a.md:3: link to 'missing.md': no such file",
        "docs/a.md:3: link to 'gone.md': no such file",
    ], out

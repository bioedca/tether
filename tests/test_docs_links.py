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

**The parse is a model of Python-Markdown 3.11.3 with a stated scope, not a renderer.** That
is the version ``requirements-docs.txt`` pins for the docs build (#1013). The model is
stdlib only (``re``, ``html.parser``, ``pathlib``), like ``tests/test_adr_index.py``:
``python-markdown`` is not in the base conda-lock, so it cannot be imported on the 3-OS
``test`` matrix. ``mkdocs.yml`` leaves Markdown at mkdocs' default extensions (``toc``,
``tables``, ``fenced_code``), and a test below fails if it names any. What this module
promises is narrower than "the renderer's verdict on any page". It promises this:

* the forms listed below are modelled, most of them with Python-Markdown's own regular
  expressions and procedures, copied and named. Each is pinned here by a test whose page
  was rendered with Python-Markdown 3.11.3. A seeded differential test,
  :func:`test_the_model_matches_python_markdown`, also compares the model with an
  instrumented real render over generated pages built from these forms: heading ids, link
  targets, and which reference uses stay literal. It needs ``python-markdown``, so the
  3-OS matrix skips it and the required ``docs-build`` job runs it;
* a form outside the list that the checker can detect, and that could change its
  verdict, fails with ``unsupported Markdown form, the checker cannot vouch for it`` and
  the file and line. Fix such a failure by rewriting the page in a listed form, or by
  extending the model with a test checked against a real render — never by guessing;
* any other form is not guaranteed. The checker may misread it either way.

A form not listed here is out of scope until it is listed. A bare ``[k]`` is not a
reference use, by design and not as a gap: a citation key such as
``[Lee2005]`` stays plain text, so an undefined one is not reported.

The modelled forms:

* whitespace as ``NormalizeWhitespace`` leaves it, with tabs expanded to four spaces;
* fenced blocks, by ``FENCED_BLOCK_RE`` itself, so only at the root. A fence-shaped line
  anywhere else, inside a blockquote or indented, is ordinary text to the renderer and
  here too: its backticks pair as code spans and its other lines are read;
* HTML comments, read in page order as ``html.parser`` reads them, so nothing inside one
  opens a comment or a block of its own; one that opens a line is stashed whole by
  ``HtmlBlockPreprocessor``, and so is a comment that starts later on that line, in the
  stashed one's tail. Any other comment is inline: the link patterns run inside it, but
  what they make stays inside the comment, so a reader sees none of it and it is not
  checked. Raw HTML blocks as its ``HTMLExtractor`` cuts them, whose Markdown is not
  parsed;
* blockquotes, lazy continuation lines included. Their markers are stripped before the
  inline patterns run, so a reference key may run across them;
* indented code blocks that open after a blank line at the root, where the block before
  them is certainly not a list (``_code_blocks``): their lines are code, read as a fenced
  block is, and the first line after them opens a paragraph that is read (#1013);
* the block boundaries that hold in every context: blank lines, ATX headings
  (``HashHeaderProcessor.RE``, at column zero inside any blockquotes, a ``#`` allowed in
  the text), horizontal rules
  (``HRProcessor.SEARCH_RE``), and the first line of a blockquote;
* setext headings whose title certainly opens a block, that is, it follows one of those
  boundaries, and whose underline is at column zero (``SetextHeaderProcessor.RE``). An
  underline anywhere else makes no heading. A title that is itself a rule, such as
  ``___``, makes a heading too, as ``SetextHeaderProcessor`` runs before ``HRProcessor``;
* reference definitions (``ReferenceProcessor.RE``), with keys normalised as the
  renderer normalises them. A definition cuts its block in two, and one block may hold
  several, so no code span, link or reference runs across one;
* within each block: code spans (``BacktickInlineProcessor``, with its backslash parity,
  its fallback that pairs a run only with a shorter later one, and the characters it skips
  when nothing pairs), backslash escapes, link text nested to any depth (``getText``),
  inline link targets (``getLink``, nested parentheses and an unclosed title included),
  and inline HTML (``HTML_RE``). Inline HTML attributes are read by ``html.parser``, so an
  unquoted ``href`` counts;
* links, images and references as ``InlineProcessor`` makes them, by one routine
  (``_inline``) that the page pass and the heading slugs share. The six link patterns run
  in the renderer's order (reference, link, image link, image reference, and the two short
  forms), each over what the ones before it left. A ``[`` after ``!`` is skipped by the
  reference pattern, so in ``![i][k] [t][k]`` it pairs ``[k] [t]``. A match resumes the
  scan at its end, and so does a pair whose key is undefined, which stays as text: every
  pair the renderer tried and could not resolve is reported, and no pair it skipped is
  read as a link. A node's own text is read by the patterns after its own. Nothing in an
  image's text counts, since it becomes the ``alt`` attribute, and nothing in a link's
  target, which becomes the ``href``;
* heading slugs, by ``toc.slugify`` with its ``_1``/``_2`` de-duplication, over the
  heading's text as rendered. Its links, images and references are read by the same
  passes, in the renderer's pattern order. A link or a defined reference gives its text,
  an image gives none, and an undefined reference stays as written. Emphasis markers are
  read by ``DelimiterProcessor``'s own boundary patterns (``_delimiter_boundary``) where
  they pair flat, one pair open at a time, or not at all: a paired marker is dropped and
  an unpaired one stays, so ``# _a_b`` slugs ``_a_b`` (#1385). The de-duplication
  starts empty. ``toc`` seeds it only from ids that are already elements when it runs,
  and with mkdocs' default extensions no element has one: raw HTML, ``<a id>`` included,
  is stashed before ``toc`` runs (#515).

These forms are refused, because how the renderer reads them depends on context that the
model does not track:

* a code span, link or inline tag that runs from one line onto a line that may start a
  new block — a list item, a reference definition, a table row, or the first line after
  an indented block;
* a bracket in a span that ``InlineProcessor.run`` reads a second time with every
  pattern: inside a link or reference, the text of a node nested in it, and what follows
  a nested node or a code span up to the next one; in a heading, also inside emphasis,
  and after emphasis in a link's text;
* emphasis markers in a heading that may nest or pair unevenly, where the 3.11.3 pass
  can cut letters out of the slug or leave a placeholder's name in it (#1385);
* text after a comment that opens a line, on the same line, and a comment that opens a
  line with a setext underline after it;
* an HTML start tag with ``<!--``, ``>`` or a block-level tag's start in a quoted
  attribute value, which ``html.parser`` reads whole and the raw-HTML passes do not;
* a heading, rule or definition line inside a table-like block, which is a row if the
  block is a table and structure if it is not;
* an HTML ``id`` or ``name`` on a line of an indented block that is not certainly code,
  which is code after a paragraph and prose inside a list;
* an ``<a>`` tag that ``html.parser`` cannot read as one start tag.

Some forms are read *short* on purpose. A heading or definition that the model cannot
place is not counted, for example one in a list item's indented continuation or a
definition inside a blockquote. Leaving one out can only make a link to it fail. For the
same reason ambiguous indentation is read as prose, including inside a blockquote or
immediately after a fence without a blank line.
"""

from __future__ import annotations

import ast
import bisect
import collections
import html
import importlib.util
import random
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from functools import cache
from html.parser import HTMLParser
from pathlib import Path

import pytest

from _docs_source import python_markdown as _python_markdown

DOCS = Path(__file__).resolve().parents[1] / "docs"
_MKDOCS_YML = DOCS.parent / "mkdocs.yml"

# `mkdocs.yml` `exclude_docs:` — not part of the site, so neither walked nor linkable.
_EXCLUDED = {DOCS / "PRD.md"}

# Files the site serves that are not under docs/: ``scripts/mkdocs_hooks.py`` adds each one to the
# build from its repository path (#612). A link to one resolves when that source file exists.
_hooks_spec = importlib.util.spec_from_file_location(
    "mkdocs_hooks_links", DOCS.parent / "scripts" / "mkdocs_hooks.py"
)
assert _hooks_spec is not None and _hooks_spec.loader is not None
_hooks = importlib.util.module_from_spec(_hooks_spec)
_hooks_spec.loader.exec_module(_hooks)
_GENERATED = {(DOCS / site).resolve(): source for site, source in _hooks.PUBLISHED_FILES.items()}

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
# blockprocessors.HashHeaderProcessor.RE (3.11.3), matched against one whole line. A line
# ending in a single backslash is therefore no heading, as it is not to the renderer.
_HASH_HEADER_RE = re.compile(r"(?P<level>#{1,6})(?P<header>(?:\\.|[^\\#]|#+(?=[^#\n]))*?)#*")
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
# Anything that may be a definition, at any indentation and with a label over several
# lines, as `ReferenceProcessor.RE`'s label allows.
_DEF_ANY_RE = re.compile(r"\[[^\[\]]*\]:")
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
_REF_TAIL_RE = re.compile(r"\s?\x02*\[([^\]]*)\]", re.DOTALL)  # \x02: a stripped quote marker
# inlinepatterns.LinkInlineProcessor.RE_LINK (see `_link_target` for the rest of getLink).
_LINK_TAIL_RE = re.compile(r"""\(\s*(?:(<[^<>]*>)\s*(?:('[^']*'|"[^"]*")\s*)?\))?""", re.DOTALL)
# inlinepatterns.HTML_RE
_HTML_RE = re.compile(
    r"(<(\/?[a-zA-Z][^<>@ ]*( [^<>]*)?|!--(?:(?!<!--|-->).)*--|[?](?:(?!<[?]|[?]>).)*[?]"
    r"|!\[CDATA\[(?:(?!<!\[CDATA\[|\]\]>).)*\]\])>)",
    re.DOTALL,
)
# inlinepatterns.ENTITY_RE and AUTOMAIL_RE: like HTML_RE, each match becomes a placeholder.
_ENTITY_RE = re.compile(r"(&(?:\#[0-9]+|\#x[0-9a-fA-F]+|[a-zA-Z0-9]+);)")
_AUTOMAIL_RE = re.compile(r"<([^<> !]+@[^@<> ]+)>")


def _delimiter_boundary(token: str, smart: bool) -> re.Pattern[str]:
    """``inlinepatterns.Delimiter._build_patterns``, copied: which side a run can close.

    ``build_inlinepatterns`` gives one ``DelimiterProcessor`` both tokens, ``*`` plain and
    ``_`` smart (3.11.3). A run of the token matches ``start``, ``end`` or ``ambiguous`` (it
    can do either), or none of them, and then it stays as text.
    """
    etoken = re.escape(token)
    xstart = rf"(?:(?<=_)|(?<![\w{etoken}]))" if token != "_" else rf"(?<![\w{etoken}])"
    xend = rf"(?:(?=_)|(?![\w{etoken}]))" if token != "_" else rf"(?![\w{etoken}])"
    if smart:
        pattern = rf"""(?x)
                (?P<ambiguous>(?<!^)(?<![\s{etoken}]){xstart}{etoken}{{1,}}{xend}(?![\s{etoken}])(?!$))|
                (?P<end>(?<!^)(?<![\s{etoken}]){etoken}{{1,}}{xend})|
                (?P<start>{xstart}{etoken}{{1,}}(?![\s{etoken}])(?!$))
                """
    else:
        pattern = rf"""(?x)
                (?P<ambiguous>(?<!^)(?<![\s{etoken}]){etoken}{{1,}}(?![\s{etoken}])(?!$))|
                (?P<end>(?<!^)(?<![\s{etoken}]){etoken}{{1,}})|
                (?P<start>{etoken}{{1,}}(?![\s{etoken}])(?!$))
                """
    return re.compile(pattern, flags=re.UNICODE)


_DELIMITERS = {
    "*": _delimiter_boundary("*", smart=False),
    "_": _delimiter_boundary("_", smart=True),
}

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
# A blockquote marker the renderer strips before the inline patterns run, kept as a
# character so offsets survive and read as nothing at all.
_GONE = "\x02"
# What the emphasis pass sees where an earlier pattern left a placeholder. The renderer's
# placeholders open with STX and close with ETX: neither a word character, a space nor a
# delimiter, and this stands for both. A link or reference whose text holds a bracket is
# marked apart (see `_emphasis_hazard`).
_STASHED = "\x04"
_BRACKETED = "\x05"


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


def _stashed_comments(
    text: str, comments: tuple[tuple[int, int], ...]
) -> tuple[tuple[int, int], ...]:
    """The comments ``HtmlBlockPreprocessor`` stashes whole, as raw blocks.

    A comment that opens a line (at most three spaces in) is a raw block. Its extractor is
    then in the block's tail until the next newline, and a comment that starts there is
    stashed into raw HTML too, though it is mid-line (3.11.3): ``<!-- a -->x <!-- b -->``
    renders ``<!-- a -->``, ``<p>x </p>`` and ``<!-- b -->``. Any other comment is inline.
    """
    out = []
    tail = -1  # the end of the stretch a stashed comment's tail runs to
    for start, end in comments:
        if start < tail or _LINE_START_RE.fullmatch(text, text.rfind("\n", 0, start) + 1, start):
            out.append((start, end))
            newline = text.find("\n", end)
            tail = len(text) if newline == -1 else newline
    return tuple(out)


# A start tag as the HTML standard spells one, its quoted values allowed to hold anything.
_START_TAG_RE = re.compile(
    r"""<[A-Za-z][^\t\n\r\f />\x00]*"""
    r"""(?:\s+[^\s"'>/=]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'=<>`]+))?)*\s*/?>"""
)
_QUOTED_VALUE_RE = re.compile(r"""=\s*(?:"([^"]*)"|'([^']*)')""")


def _tag_value_hazards(text: str, comments: tuple[tuple[int, int], ...]) -> list[int]:
    """Where a start tag has ``<!--``, ``>`` or a block-level tag's start in a quoted value.

    ``html.parser`` reads such a value whole, but the renderer's raw-HTML block search, its
    ``HTML_RE`` and the model's comment pass do not, so the page order the model assumes is
    wrong from there on (#1013, gap 5): ``<span id="a" title="x > y">`` renders the anchor
    ``a``, which ``HTML_RE``, cut at the first ``>``, never shows the model. A tag inside a
    comment is not one.
    """
    out = []
    for m in _START_TAG_RE.finditer(text):
        if any(start <= m.start() < end for start, end in comments):
            continue
        for value in _QUOTED_VALUE_RE.finditer(m.group()):
            value = value.group(1) if value.group(1) is not None else value.group(2)
            if (
                "<!--" in value
                or ">" in value
                or any(
                    t.group(1).lower() in _BLOCK_TAGS
                    for t in re.finditer(r"</?([A-Za-z][^\t\n\r\f />\x00]*)", value)
                )
            ):
                out.append(m.start())
                break
    return out


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

    def setext(i: int) -> bool:
        """Whether line ``i``, which opens a block, is a setext title."""
        line, nxt = lines[i], lines[i + 1] if i + 1 < len(lines) else None
        return bool(
            line.depth == open_
            and fresh <= line.depth
            and not line.body.startswith(" " * _TAB)
            and nxt is not None
            and nxt.depth == line.depth
            and _SETEXT_UNDERLINE_RE.fullmatch(nxt.body)
        )

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
        if rule and cur is None and setext(i):
            rule = None  # `SetextHeaderProcessor` runs first: `___` over `===` is a heading
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
            if setext(i):
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


def _code_blocks(
    lines: list[_Line], units: list[_Unit], source: list[str], masked: str, fence_ends: set[int]
) -> set[int]:
    """Find root code blocks whose preceding sibling cannot be a list.

    Across source blanks, skip indented runs and reference-only blocks that can leave
    the same list in place. A fence or a standalone line comment ends a preceding list.
    """
    masked_lines = masked.split("\n")

    def plain(k: int) -> bool:  # an unaltered line at the root
        return lines[k].depth == 0 and lines[k].body == source[k]

    def after_no_list(first: int) -> bool:
        k = first - 1
        if k >= 0 and source[k]:
            return False  # no blank line: a lazy line, or a block the model split itself
        while True:
            while k >= 0 and not source[k]:
                k -= 1
            if k < 0 or k in fence_ends:
                return True
            start = k
            while start > 0 and source[start - 1]:
                start -= 1
            block = source[start : k + 1]
            if block != masked_lines[start : k + 1]:
                comment = re.fullmatch(r" {0,3}<!--(?:(?!--!?>).)*--!?> *", block[0])
                return len(block) == 1 and not masked_lines[k].strip() and comment is not None
            text = "\n".join(block)
            if not _REF_DEF_RE.sub("", text).strip() and not any(map(_LIST_START_RE.match, block)):
                k = start - 1  # standalone definitions do not add a sibling
                continue
            if not block[0].startswith(" " * _TAB):
                if _LIST_START_RE.match(block[0]) or _DEF_ANY_RE.search(text):
                    return False
                # A later item marker is lazy text unless a heading or rule can split it off.
                return not (
                    any(_LIST_START_RE.match(row) for row in block[1:])
                    and any(
                        _HASH_HEADER_RE.fullmatch(row)
                        or _HR_RE.fullmatch(row)
                        or _SETEXT_UNDERLINE_RE.fullmatch(row)
                        for row in block
                    )
                )
            if not all(row.startswith(" " * _TAB) for row in block):
                return False
            k = start - 1

    out: set[int] = set()
    for unit in units:
        first = unit.first
        if unit.heading is not None or not lines[first].body.startswith(" " * _TAB):
            continue
        if not plain(first) or not after_no_list(first):
            continue
        k = first
        while k <= unit.last and lines[k].body.startswith(" " * _TAB) and plain(k):
            out.add(k)
            k += 1
    return out


def _definitions(
    lines: list[_Line], units: list[_Unit], tables: set[int], code: set[int], problem
) -> list[_Def]:
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
        text = "\n".join("" if k in code else lines[k].body for k in rows)
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
            if k not in placed and k not in code and _DEF_INDENTED_RE.match(lines[k].body):
                problem(
                    lines[k].start,
                    "a reference definition indented four spaces or more, "
                    "which is a definition inside a list item and code elsewhere",
                )
    return out


# --- inline --------------------------------------------------------------------------------


def _code_span_end(text: str, begin: int) -> int | None:
    """Where the code span opened at ``begin`` ends: ``find_code_spans`` (3.11.3), verbatim.

    The opening run pairs with the next run of the same length; failing that, with the
    longest later run if that is shorter, the surplus opening ticks becoming code. With
    neither, the span does not open (None) and its ticks stay literal.
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
    return end if 0 < longest_span < max_ticks else None


def _mask_code(text: str) -> tuple[str, list[tuple[int, int]]]:
    """``text`` with its code spans masked, and their spans, as the backtick pattern runs.

    An even run of backslashes before a backtick is escaped backslashes, so the backtick
    stays live; an odd run escapes it. An opening run that pairs with nothing is skipped
    whole, and the scan resumes after it, as ``BacktickInlineProcessor`` resumes (3.11.3).
    """
    spans = []
    pos = 0
    while (m := _BACKTICK_RE.search(text, pos)) is not None:
        if m.group(1):
            start, end = m.span()
        else:
            start, end_ = m.start(), _code_span_end(text, m.start())
            if end_ is None:
                # ``handleMatch`` resumes at the index ``find_code_spans`` returns, which is
                # past the run by its own length again: ``\`\`k\`x\``` resumes after the
                # first ``k\```, so that tick never opens a span (3.11.3).
                run = start
                while run < len(text) and text[run] == "`":
                    run += 1
                pos = run + (run - start)
                continue
            end = end_
            spans.append((start, end))
        text = text[:start] + _CODE * (end - start) + text[end:]
        pos = end
    return text, spans


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


@dataclass(frozen=True)
class _Node:
    """An element an inline pattern makes: a link, an image or a reference with a defined key."""

    kind: str  # the pattern's name in `markdown.inlinepatterns.build_inlinepatterns`
    begin: int  # where its source starts, the `!` of an image included
    text: tuple[int, int]  # its text, inside the outer brackets
    stop: int  # the end of its source
    payload: str  # a link's target, or a reference's lookup key
    children: tuple[_Node, ...]  # the nodes inside its text


@dataclass(frozen=True)
class _Try:
    """A pair the reference pattern tried and left as text, its key being undefined."""

    begin: int  # the `[` of its text
    text_end: int
    stop: int
    lookup: str | None  # as the renderer looks it up; None when it cannot match any definition
    image: bool


def _lookup(view: str, start: int, end: int, key: str) -> str | None:
    """``ReferenceInlineProcessor``'s id: the key, or a collapsed use's text, normalised."""
    lookup = re.sub(r"\s+", " ", (key or view[start + 1 : end - 1]).replace(_GONE, "").lower())
    # An escape or code span in an id never matches a definition.
    return None if _CODE in lookup or _ESC in lookup else lookup


def _no_tail(text: str, index: int) -> tuple[str, int]:
    return "", index


# The link patterns of `build_inlinepatterns` in priority order: (name, image, tail, by_key).
# A pattern marked `by_key` makes a node only for a defined key; the others make one for any
# match their tail accepts.
_INLINE_STAGES = (
    ("reference", False, _key_tail, True),
    ("link", False, _link_target, False),
    ("image_link", True, _link_target, False),
    ("image_reference", True, _key_tail, True),
    ("short_reference", False, _no_tail, True),
    ("short_image_ref", True, _no_tail, True),
)


def _inline(
    view: str, definitions: frozenset[str] | dict, stage: int = 0, a: int = 0, b: int | None = None
) -> tuple[list[_Node], list[_Try]]:
    """The link elements of ``view[a:b]``, made as ``InlineProcessor`` makes them (#1013).

    This is the one pairing routine of the page pass and the heading slugs. Each pattern
    runs over what the ones before it left, from ``stage`` on: a node's source becomes a
    placeholder, and its text is read by the patterns after its own, as ``__applyPattern``
    reads a node's text. A match resumes the scan at its end, and so does a pair whose key is
    undefined, which makes no node and stays as text (3.11.3). A ``[`` whose text does not
    close, or whose tail does not follow, is retried one character on. Code spans and
    escapes are already masked in ``view``.
    """
    b = len(view) if b is None else b
    left = view[:b]
    nodes: list[_Node] = []
    tries: list[_Try] = []
    for index in range(stage, len(_INLINE_STAGES)):
        name, image, tail, by_key = _INLINE_STAGES[index]
        pos = a
        while (start := left.find("[", pos, b)) != -1:
            pos = start + 1
            if (start > 0 and left[start - 1] == "!") != image:
                continue
            end = _link_text_end(left[:b], start)
            if end is None or (t := tail(left[:b], end)) is None:
                continue
            payload, stop = t
            if stop < 0:
                # ``getLink`` can return the index -1 (an unclosed title quote), and the
                # renderer then keeps ``data[-1:]``: the source runs to the last character.
                stop += b
            pos = stop
            if by_key:
                payload = _lookup(left, start, end, payload) or ""
                if payload not in definitions:
                    if tail is _key_tail:
                        tries.append(_Try(start, end, stop, payload or None, image))
                    continue
            begin = start - 1 if image else start
            # A node made earlier inside this one's text is its child; one inside its tail,
            # the target of a link, became part of an attribute and is not in the page, and
            # so is one the source ends inside of (the ``-1`` above cuts its placeholder).
            inner = [n for n in nodes if start < n.begin and n.stop < end]
            nodes = [n for n in nodes if not begin <= n.begin < stop]
            if not image:  # an image's text is its alt attribute, which no pattern reads
                more, more_tries = _inline(left, definitions, index + 1, start + 1, end - 1)
                inner += more
                tries += more_tries
            inner.sort(key=lambda n: n.begin)
            nodes.append(_Node(name, begin, (start + 1, end - 1), stop, payload, tuple(inner)))
            left = left[:begin] + _CODE * (stop - begin) + left[stop:]
    nodes.sort(key=lambda n: n.begin)
    tries.sort(key=lambda t: t.begin)
    return nodes, tries


def _reread(nodes: list[_Node], spans: list[tuple[int, int]] = ()) -> list[tuple[int, int]]:
    """The spans ``InlineProcessor.run`` passes through every pattern a second time.

    ``run`` pops each node it made from a stack and runs the patterns afresh, from the
    first, over the text and the tail of each of that node's children. So inside a link or
    a reference, the text of a node nested in it, and what follows such a node up to the
    next one, are read twice; the second pass can make links and references that the
    first, which started at a later pattern, did not. The model reads them once. A code
    span, one of ``spans``, is a child too: its text is atomic, but its tail is read again.
    """
    out: list[tuple[int, int]] = []

    def visit(node: _Node, nested: bool) -> None:
        if node.kind.startswith(("image", "short_image")):
            return  # its text is an attribute, read by no pattern
        kids: list[tuple[int, int, _Node | None]] = [(k.begin, k.stop, k) for k in node.children]
        lo, hi = node.text
        kids += [
            (s, e, None)
            for s, e in spans
            if lo <= s and e <= hi and not any(k.begin <= s < k.stop for k in node.children)
        ]
        kids.sort(key=lambda k: k[0])
        if nested:
            out.append((lo, kids[0][0] if kids else hi))
        for i, (_, stop, kid) in enumerate(kids):
            out.append((stop, kids[i + 1][0] if i + 1 < len(kids) else hi))
            if kid is not None:
                visit(kid, True)

    for node in nodes:
        visit(node, False)
    return out


def _walk(nodes) -> list[_Node]:
    """The nodes and every node nested in them, except inside an image.

    An image's text is its ``alt`` attribute: a link made inside it first is not in the
    page, so ``![[a](x.md)][k]`` shows an image and no link to ``x.md``.
    """
    out = []
    for n in nodes:
        out.append(n)
        if not n.kind.startswith(("image", "short_image")):
            out += _walk(n.children)
    return out


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

    fence_ends: set[int] = set()

    def blank_fence(match: re.Match[str]) -> str:
        fence_ends.add(bisect.bisect_right(starts, match.end()) - 1)
        return re.sub(r"[^\n]", " ", match.group())

    fenced = _FENCED_BLOCK_RE.sub(blank_fence, text)
    comments = _comments(fenced)
    for offset in _tag_value_hazards(fenced, comments):
        problem(
            offset, "an HTML tag with `<!--`, `>` or a block-level tag in a quoted attribute value"
        )
    raw = _raw_html_blocks(_blank_spans(fenced, comments))
    stashed = _stashed_comments(fenced, comments)
    blocks = _blank_spans(fenced, stashed)
    for start, end in stashed:
        newline = blocks.find("\n", end)
        line_end = len(blocks) if newline == -1 else newline
        if blocks[end:line_end].strip():
            # The renderer ends the block before the comment and opens a new one after it,
            # at a column the model does not track (#1013).
            problem(start, "text after a comment that opens a line, on the same line")
        following = blocks.find("\n", line_end + 1)
        nxt = blocks[line_end + 1 : len(blocks) if following == -1 else following]
        if newline != -1 and _SETEXT_UNDERLINE_RE.fullmatch(nxt):
            # Its placeholder is a paragraph to the block parser: the pair is a heading.
            problem(start, "a comment that opens a line with a setext underline after it")
    md = _blank_spans(blocks, raw)
    lines = _lines(md)
    tables = _table_lines(lines)
    units = _units(lines, tables, problem)
    code = _code_blocks(lines, units, lines_, md, fence_ends)
    defs = _definitions(lines, units, tables, code, problem)
    _mark_code(lines, units)
    definitions = frozenset(d.key for d in defs)

    links: list[tuple[int, str]] = [(lines[d.first].start, d.target) for d in defs]
    refs: list[_Ref] = []
    tags: list[tuple[int, str, dict[str, str]]] = []

    def ref_of(view: str, a: int, t: _Try) -> _Ref:
        source = blocks[a + t.begin : a + t.stop]
        shown = blocks[a + t.text_end : a + t.stop].strip()[1:-1]
        return _Ref(
            a + t.begin, source, shown or blocks[a + t.begin + 1 : a + t.text_end - 1], t.lookup
        )

    for unit in units:
        a = lines[unit.first].start
        b = lines[unit.last].start + len(lines_[unit.last])
        soft = [lines[k].start for k in sorted(unit.soft)]

        def crosses(start: int, end: int, what: str, a: int = a, soft: list[int] = soft) -> None:
            i = bisect.bisect_right(soft, a + start)
            if i < len(soft) and soft[i] < a + end:
                problem(a + start, f"{what} that runs onto a line which may start a new block")

        chunk = blocks[a:b]
        for k in range(unit.first + 1, unit.last + 1):
            # The renderer strips each line's quote markers before the inline patterns run,
            # so `[a]\n> [b]` is one reference use, of the key `b`.
            s_, n_ = lines[k].start - a, len(lines_[k]) - len(lines[k].body)
            chunk = chunk[:s_] + _GONE * n_ + chunk[s_ + n_ :]
        for k in sorted(code):
            if unit.first <= k <= unit.last:  # code is not inline text: blank it as a fence
                s_ = lines[k].start - a
                chunk = chunk[:s_] + " " * len(lines_[k]) + chunk[s_ + len(lines_[k]) :]
        # A definition is not inline text, and the renderer cuts the block around it: what
        # comes before it and what comes after are separate blocks, so no code span, link or
        # reference runs from one into the other.
        segments: list[tuple[int, int]] = []
        pos = 0
        for d in defs:
            if unit.first <= d.first <= unit.last:
                s_, e_ = lines[d.first].start - a, lines[d.last].start + len(lines_[d.last]) - a
                chunk = chunk[:s_] + _CODE * (e_ - s_) + chunk[e_:]
                segments.append((pos, s_))
                pos = e_
        segments.append((pos, len(chunk)))
        view, spans = chunk, []
        for s_, e_ in segments:
            masked, inner = _mask_code(chunk[s_:e_])
            view = view[:s_] + masked + view[e_:]
            spans += [(start + s_, end + s_) for start, end in inner]
        for start, end in spans:
            crosses(start, end, "a code span")
        view = _mask_escapes(view)
        hidden = [
            (start - a, end - a)
            for start, end in comments
            if a <= start < b and blocks[start] == "<"
        ]

        def shown(offset: int, hidden: list[tuple[int, int]] = hidden) -> bool:
            # A comment that does not open a line is inline HTML, which runs after the link
            # patterns: what they make inside it is in the page but inside the comment, so a
            # reader sees none of it and the strict build does not check it (#1013, gap 10).
            return not any(start <= offset < end for start, end in hidden)

        nodes: list[_Node] = []
        tries: list[tuple[_Try, int]] = []  # each with the end of its segment
        for s_, e_ in segments:
            more, more_tries = _inline(view, definitions, 0, s_, e_)
            nodes += more
            tries += [(t, e_) for t in more_tries]
        for start, end in _reread(nodes, spans):
            if "[" in view[start:end]:
                problem(
                    a + start,
                    "a bracket in or after a link or reference nested inside another one, "
                    "which the renderer reads a second time",
                )
                break
        consumed: list[tuple[int, int]] = []
        for node in _walk(nodes):
            # An image's text is its ``alt`` attribute, so no tag in it is in the page.
            image = node.kind.startswith(("image", "short_image"))
            consumed.append((node.begin if image else node.text[1] + 1, node.stop))
            if not shown(node.begin):
                continue
            if node.kind in ("link", "image_link"):
                crosses(node.text[0] - 1, node.stop, "a link")
                links.append((a + node.text[0] - 1, node.payload))
            elif node.kind in ("reference", "image_reference"):
                crosses(node.text[0] - 1, node.stop, "a reference")
        for t, e_ in tries:
            if not shown(t.begin):
                continue
            # Its pairing turns on the line break either way, so this comes before the
            # exception below.
            crosses(t.begin, t.stop, "a reference")
            # One exception of the checker's own: when ``[key](…)`` forms a link, as in
            # ``[text][key](url)``, no literal reference is left on the page — the renderer
            # shows ``[text]`` and a link — so that is not a reference use here.
            if view.startswith("(", t.stop) and _link_target(view[:e_], t.stop) is not None:
                continue
            refs.append(ref_of(view, a, t))
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
        refs.extend(ref_of(chunk, a, t) for t in _inline(chunk, definitions)[1])

    for unit in units:
        if unit.heading is not None:
            hazard = _heading_read(unit.heading, definitions)[1]
            if hazard is not None:
                problem(lines[unit.first].start, hazard)
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


def _emphasis_pairs(scope: str, nested: bool = False) -> tuple[str | None, list[int]]:
    """How the emphasis pass pairs ``scope``'s markers: the offsets it consumes, or why not.

    ``scope`` is one string the pass runs over, as it sees it: the heading's text, or a
    link's text, with each match of an earlier pattern as one ``_STASHED`` character. Every
    run of ``*`` or ``_`` is read by its token's ``_DELIMITERS`` pattern. The model follows
    the pass where its pairs are flat (#1385): one pair open at a time, opened by a run that
    can open, three long at most, and closed by the next run of its token that can close,
    if that run is as long. A run that matches no side stays as text, and so does a closer
    with nothing of its token open, which the pass skips; so does an opener left open at
    the end, as ``_`` in ``_a_b``. Anything else is refused: ``DelimiterProcessor``
    (3.11.3) pairs both tokens in one pass and builds the elements from offsets it caches,
    and for nested or uneven runs those offsets can cut letters and placeholders out of
    the text or leave a placeholder's name in it: ``*a**b*`d``` slugs ``abklzzwxh0000``.
    """
    paired: list[int] = []
    open_: tuple[str, int, int] | None = None  # the open run: its token and span
    for m in re.finditer(r"\*+|_+", scope):
        token, (start, end) = m.group()[0], m.span()
        side = _DELIMITERS[token].match(scope, start)
        kind = side.lastgroup if side else None
        if kind is None or (kind == "end" and (open_ is None or open_[0] != token)):
            continue
        if open_ is None and kind in ("start", "ambiguous") and end - start <= 3:
            open_ = (token, start, end)
        elif (
            open_ is not None
            and open_[0] == token
            and kind in ("end", "ambiguous")
            and end - start == open_[2] - open_[1]
        ):
            # ``InlineProcessor.run`` reads what an emphasis element holds a second time
            # with every pattern, as it reads the text of a node nested in a link; in a
            # link's own text, ``nested``, it reads what follows the element too.
            again = scope[open_[2] : len(scope) if nested else start]
            if "[" in again or _BRACKETED in again:
                return "a bracket inside emphasis in a heading, which the renderer reads twice", []
            paired += [*range(open_[1], open_[2]), *range(start, end)]
            open_ = None
        else:
            return "emphasis markers in a heading that may nest or pair unevenly", []
    return None, paired


def _heading_read(raw: str, definitions: frozenset[str]) -> tuple[str, str | None]:
    """The heading's slug text, as ``_heading_text`` says, and why it is refused, or None."""
    view, spans = _mask_code(raw)
    view = _mask_escapes(view)
    nodes, _ = _inline(view, definitions)
    # A comment is stripped whole, with whatever the link patterns made inside it (#1013).
    comments = _comments(view)
    hazards: list[str] = []
    gone: list[int] = []  # the offsets of the `_` markers the emphasis pass pairs

    def emphasis(nodes: tuple[_Node, ...] | list[_Node], a: int, b: int, nested: bool) -> None:
        """Pair the markers of ``view[a:b]``, then of each node's text, as the pass runs."""
        chars, at = [], a  # (character, offset in view), each node stashed
        for node in nodes:
            chars += [(view[i], i) for i in range(at, node.begin)]
            text = view[node.text[0] : node.text[1]]
            chars.append((_BRACKETED if "[" in text else _STASHED, node.begin))
            at = node.stop
            if not node.kind.startswith(("image", "short_image")):
                emphasis(node.children, *node.text, nested=True)
        chars += [(view[i], i) for i in range(at, b)]
        chars = [(c, i) for c, i in chars if c != _GONE]
        # Each match of a later pattern is one placeholder too: a code span, an escape, a
        # mail link, an HTML tag or comment, an entity.
        for pattern in (re.compile(f"{_CODE}+|{_ESC}.", re.DOTALL), _AUTOMAIL_RE, _HTML_RE):
            chars = _stash(chars, pattern)
        chars = _stash(chars, _ENTITY_RE)
        hazard, paired = _emphasis_pairs("".join(c for c, _ in chars), nested)
        if hazard is not None:
            hazards.append(hazard)
        gone.extend(chars[k][1] for k in paired if chars[k][0] == "_")

    emphasis(nodes, 0, len(raw), nested=False)
    for i in gone:
        view = view[:i] + "*" + view[i + 1 :]  # read as a `*`, which the slug drops

    def prose(a: int, b: int) -> str:
        text = _HTML_RE.sub("", view[a:b]).replace("*", "")
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

    def text_of(nodes: tuple[_Node, ...] | list[_Node], a: int, b: int) -> str:
        out, pos = [], a
        for node in nodes:
            if any(start <= node.begin < end for start, end in comments):
                continue
            out.append(plain(pos, node.begin))
            if not node.kind.startswith(("image", "short_image")):
                out.append(text_of(node.children, *node.text))
            pos = node.stop
        return "".join(out) + plain(pos, b)

    return text_of(nodes, 0, len(raw)), (hazards[0] if hazards else None)


def _stash(chars: list[tuple[str, int]], pattern: re.Pattern[str]) -> list[tuple[str, int]]:
    """``chars`` with each match of ``pattern`` in their text made one ``_STASHED``."""
    text, out, pos = "".join(c for c, _ in chars), [], 0
    for m in pattern.finditer(text):
        out += [*chars[pos : m.start()], (_STASHED, chars[m.start()][1])]
        pos = m.end()
    return out + chars[pos:]


def _heading_text(raw: str, definitions: frozenset[str]) -> str:
    """The plain text the toc extension slugifies: the heading as rendered, tags stripped.

    The inline structure is read with the passes a paragraph gets: code spans, escapes and
    the link patterns, by ``_inline``, the page pass's own routine. A link, or a reference
    whose key is defined, reduces to its text; an image, of either kind, renders no text. An
    undefined reference stays on the page as written, brackets and all, and so in the slug.
    Every ``*`` is dropped, as ``toc.slugify`` drops it, and a ``_`` is dropped where the
    emphasis pass pairs it and kept where it stays as text, by ``_emphasis_pairs`` (#1385).
    """
    return _heading_read(raw, definitions)[0]


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
    ids |= _api_anchors(path)
    return frozenset(ids)


#: A mkdocstrings block (#179): ``::: tether.analysis.histogram`` at column zero.
_API_BLOCK_RE = re.compile(r"^::: (tether(?:\.\w+)*)[ \t]*$", re.MULTILINE)
#: A block the generator does not run: an HTML comment, or a fenced block at the root.
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_FENCE_RE = re.compile(r"^(`{3,}|~{3,}).*?^\1[ \t]*$", re.DOTALL | re.MULTILINE)
_SRC = DOCS.parent / "src"


def _api_anchors(path: Path) -> set[str]:
    """The anchors a generated API page serves for its modules and their ``__all__`` names.

    mkdocstrings writes one heading for each rendered object, with the object's dotted path as
    its ``id``. This reads the module's ``__all__`` with ``ast``, as the generator does, and
    gives the module path and ``<module>.<name>`` for each listed name that the module itself
    defines. Members of a class and re-exported names are not modelled, so a link to one of
    them fails here; ``mkdocs build --strict`` checks the rendered anchors in full.
    """
    anchors: set[str] = set()
    text = _FENCE_RE.sub("", _COMMENT_RE.sub("", path.read_text(encoding="utf-8")))
    for dotted in _API_BLOCK_RE.findall(text):
        base = _SRC.joinpath(*dotted.split("."))
        source = base / "__init__.py" if base.is_dir() else base.with_suffix(".py")
        if not source.is_file():
            continue
        anchors.add(dotted)
        tree = ast.parse(source.read_text(encoding="utf-8"))
        defined = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
            ):
                names = ast.literal_eval(node.value)
                anchors.update(f"{dotted}.{n}" for n in names if n in defined)
    return anchors


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
        if resolved in _GENERATED:
            return None if _GENERATED[resolved].is_file() else "the hook's source file is missing"
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


def test_an_api_page_serves_the_anchors_of_its_modules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``:::`` block serves its module and the ``__all__`` names the module defines (#179)."""
    api = "# API\n\n::: tether.fret.gamma\n    options:\n      heading_level: 2\n"
    good = "# A\n\n[g](api.md#tether.fret.gamma) and [f](api.md#tether.fret.gamma.estimate_gamma)\n"
    bad = "# B\n\n[x](api.md#tether.fret.gamma.not_a_name) and [y](api.md#tether.fret.leakage)\n"
    out = _check(tmp_path, monkeypatch, {"api.md": api, "good.md": good, "bad.md": bad})
    assert [p.split(": ", 1)[0] for p in out] == ["docs/bad.md:3", "docs/bad.md:3"], out


def test_a_fenced_or_commented_api_block_serves_no_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``:::`` line in a fence or an HTML comment is not run, so it makes no heading."""
    api = "# API\n\n```\n::: tether.fret.gamma\n```\n\n<!--\n::: tether.fret.leakage\n-->\n"
    page = "# A\n\n[g](api.md#tether.fret.gamma) and [l](api.md#tether.fret.leakage)\n"
    out = _check(tmp_path, monkeypatch, {"api.md": api, "page.md": page})
    assert [p.split(": ", 1)[0] for p in out] == ["docs/page.md:3", "docs/page.md:3"], out


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
        ('- item\n\n    <a id="x"></a>\n', 5, "an HTML anchor on a line of an indented block"),
        ("See <a href='x.md>y</a> here.\n", 3, "an <a> tag html.parser cannot read"),
        ("- item\n\n        [k]: #a\n", 5, "a reference definition indented four spaces"),
        # Python-Markdown 3.11.3 renders `<!-- separator -->`, then a list (#1013).
        ("<!-- separator -->- item\n\n    x[t][nokey]\n", 3, "text after a comment that opens"),
        # The reference's text is read from the link pattern on, which leaves `[b](c.md)`
        # as text; 3.11.3 then runs every pattern again over the nested link's own text and
        # renders a link to `c.md` inside the link to `e.md` (#1013).
        ("[[a [b](c.md)](e.md)][k]\n\n[k]: #a\n", 3, "a bracket in or after a link"),
        # The pair `[t]` + `[k]` spans the break after a line that is code after a
        # paragraph and text in a list item: 3.11.3 renders `x[t]` and a link to `a.md` in
        # the list. It is refused even where `[k](a.md)` would be a link (#1013).
        ("- item\n\n    x[t]\n[k](a.md)\n", 5, "a reference that runs onto a line"),
    ],
    ids=["crosses-a-block-start", "heading-in-a-table", "indented-anchor", "broken-a", "deep-def"]
    + ["comment-then-text", "read-twice", "pair-across-indented"],
)
def test_a_form_outside_the_model_fails_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str, line: int, what: str
) -> None:
    """A form whose rendering turns on context the model does not track is refused by name."""
    out = _check(tmp_path, monkeypatch, {"a.md": "# A\n\n" + body})
    assert out[0].startswith(f"docs/a.md:{line}: {_UNSUPPORTED}: {what}"), out


# These fixtures were rendered with Python-Markdown 3.11.3 (`toc`, `tables`, `fenced_code`).
@pytest.mark.parametrize(
    "body",
    [
        pytest.param("    x[a][b]\n", id="start-of-page"),
        pytest.param("Text.\n\n    x[a][b] = 1\n", id="after-paragraph"),
        pytest.param("# A\n\n[k]: #a\n\n    x[a][b] = 1\n", id="after-definition"),
        pytest.param("# A\n\n[k\n]: #a\n\n    x[a][b]\n", id="after-two-line-definition"),
        pytest.param("- item\n\n<!-- separator -->\n\n    x[a][b]\n", id="after-comment"),
        pytest.param("***\n\n    x[a][b]\n", id="after-rule"),
        pytest.param("Title\n=====\n\n    x[a][b]\n", id="after-setext"),
        pytest.param("a | b\n--|--\n1 | 2\n\n    x[a][b]\n", id="after-table"),
        pytest.param("Para\n\n    a\n\n    b[a][b]\n", id="after-code"),
        # The list is a child of the quote, so the code is still at the root.
        pytest.param("> - item\n\n    x[a][b]\n", id="after-quote"),
        pytest.param("Text\n* item\n\n    x[a][b]\n", id="after-lazy-item-marker"),
        # The fence separates the list from the indented code.
        pytest.param("- item\n```\nc\n```\n\n    x[a][b]\n", id="after-fence-and-blank"),
    ],
)
def test_an_indented_code_block_after_a_blank_line_is_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    """The renderer emits ``<pre><code>`` for each indented root block (#1013)."""
    assert _check(tmp_path, monkeypatch, {"a.md": body}) == []


def test_indentation_after_a_fence_without_a_blank_is_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The model requires a source blank line, although the fence adds one in the renderer."""
    body = "# A\n\n```\nc\n```\n    x[a][b]\n"
    out = _check(tmp_path, monkeypatch, {"a.md": body})
    assert len(out) == 1 and "reference-style link [a][b] has no [b]:" in out[0], out


def test_an_indented_code_block_holds_no_anchors_or_definitions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Code is not Markdown: an ``id`` there serves nothing, and ``[k]:`` defines nothing.

    Python-Markdown 3.11.3 renders the two code lines as ``<pre><code>&lt;a id="x"&gt;…``
    and ``<pre><code>[k]: #a``, so ``#x`` has no target and ``[t][k]`` stays literal.
    """
    body = '# A\n\nPara.\n\n    <a id="x"></a>\n\nPara.\n\n    [k]: #a\n\nSee [x](#x) and [t][k].\n'
    out = _check(tmp_path, monkeypatch, {"a.md": body})
    assert len(out) == 2, out
    assert out[0].startswith("docs/a.md:11: reference-style link [t][k] has no [k]:"), out
    assert out[1].startswith("docs/a.md:11: link to '#x': no heading or HTML anchor"), out


def test_text_after_an_indented_code_block_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first line out of the code opens a paragraph, which is checked.

    Python-Markdown 3.11.3: ``<pre><code>a[a][b]`` then ``<p>next [t][nokey]</p>``.
    """
    body = "# A\n\nPara\n\n    a[a][b]\nnext [t][nokey]\n"
    out = _check(tmp_path, monkeypatch, {"a.md": body})
    assert len(out) == 1 and out[0].startswith("docs/a.md:6: reference-style link [t][nokey]"), out


@pytest.mark.parametrize(
    ("body", "line"),
    [
        # <ul><li><p>item</p><p>cont [t][nokey]</p></li></ul>
        ("- item\n\n    cont [t][nokey]\n", 5),
        # <ol><li><p>item</p><p>cont [t][nokey]</p></li></ol>
        ("1. item\n\n    cont [t][nokey]\n", 5),
        # <ul><li>…<p>cont</p><p>more [t][nokey]</p></li></ul>: still the same item
        ("- item\n\n    cont\n\n    more [t][nokey]\n", 7),
        # <h1>H</h1><ul><li><p>item</p><p>x[t][nokey]</p></li></ul>: the heading splits
        ("## H\n- item\n\n    x[t][nokey]\n", 6),
        # <ul><li><p>item</p><p>x[t][nokey]</p></li></ul>: a definition leaves no block
        ("- item\n\n[k]: #a\n\n    x[t][nokey]\n", 7),
        # the same, with a label that runs over two lines, which the renderer allows
        ("- item\n\n[k\n]: #a\n\n    x[t][nokey]\n", 8),
        ("- item\n\n    <!-- separator -->\n\n    x[t][nokey]\n", 7),
        # A quote without a separating blank stays inside the root list.
        ("- item\n>\n> q\n\n    x[t][nokey]\n", 7),
        # Definitions and setext headings can split a block before an item line.
        ("Text\n[k]: #a\n- item\n\n    x[t][nokey]\n", 7),
        ("Text\n=====\n- item\n\n    x[t][nokey]\n", 7),
    ],
    ids=["bullet", "ordered", "second-paragraph", "list-after-heading", "after-definition"]
    + [
        "after-two-line-definition",
        "indented-comment",
        "quote-inside-list",
        "definition-before-item",
        "setext-before-item",
    ],
)
def test_an_indented_list_continuation_is_still_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str, line: int
) -> None:
    """An indented block whose list the model cannot rule out is read as prose (#1013)."""
    out = _check(tmp_path, monkeypatch, {"a.md": "# A\n\n" + body})
    assert len(out) == 1, out
    assert out[0].startswith(f"docs/a.md:{line}: reference-style link [t][nokey]"), out


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
    ``[g]`` below. Both stay literal: Python-Markdown 3.11.3 renders ``![f][u]`` and ``[g]``
    as text before the link, so both pairs are reported (#1013, gap 2).
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
        "docs/a.md:7: reference-style link [u] [g] has no [g]: definition in this file, "
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


# --- one row per closed gap (#1013) and per emphasis form (#1385) ----------------------
# Each row is a page, the checker's verdict on it, and a piece of what Python-Markdown
# 3.11.3 renders for it (`toc`, `tables`, `fenced_code`, as MkDocs configures them). The
# verdict was read off that render; `test_the_gap_rows_render_as_python_markdown_does`
# keeps the piece true of the installed renderer.
_REF = "definition in this file, so it renders as literal text"
_GAP_ROWS = [
    pytest.param(
        # The reference pattern skips the `[` after `!`, then pairs `[k] [t]`: `t` is
        # undefined, so `[t]` stays literal, and the image reference and `[k]` render after.
        "![i][k] [t][k]\n\n[k]: #a\n",
        [f"docs/a.md:3: reference-style link [k] [t] has no [t]: {_REF}"],
        '<img alt="i" src="#a" /> [t]<a href="#a">k</a>',
        id="gap-2-pairing-after-an-image",
    ),
    pytest.param(
        # A bare `[k]` is never a reference use, so a citation key stays plain text.
        "See [Lee2005].\n",
        [],
        "<p>See [Lee2005].</p>",
        id="gap-3-a-bare-key-is-text",
    ),
    pytest.param(
        # The scan resumes after the undefined `[t][a b]`, so `[a b][k]` is never tried and
        # the defined `[k]` renders alone.
        "[t][a b][k]\n\n[k]: #a\n",
        [f"docs/a.md:3: reference-style link [t][a b] has no [a b]: {_REF}"],
        '<p>[t][a b]<a href="#a">k</a></p>',
        id="gap-4-resume-after-an-undefined-pair",
    ),
    pytest.param(
        # `HTML_RE` stops at the `>` in the value; the anchor `s` is in the page all the same.
        'Text <span id="s" title="x > y">t</span>\n',
        [
            f"docs/a.md:3: {_UNSUPPORTED}: an HTML tag with `<!--`, `>` or a block-level tag "
            "in a quoted attribute value"
        ],
        '<span id="s" title="x > y">t</span>',
        id="gap-5-a-hazard-in-an-attribute-value",
    ),
    pytest.param(
        'Text <span id="s" title="x y">t</span> [ok](#s)\n',
        [],
        '<span id="s" title="x y">t</span> <a href="#s">ok</a>',
        id="gap-5-a-plain-attribute-value",
    ),
    pytest.param(
        # No closing run of two backticks: the first one pairs with nothing and stays
        # literal, and so do the two, so the slug keeps the space between them.
        "# x` ``ax\n\n[ok](#x-ax) [bad](#xax)\n",
        ["docs/a.md:5: link to '#xax': no heading or HTML anchor '#xax' in docs/a.md"],
        '<h1 id="x-ax">x` ``ax</h1>',
        id="gap-9-unpaired-backticks-in-a-heading",
    ),
    pytest.param(
        # The link pattern runs inside a comment that does not open a line, but what it makes
        # stays inside the comment: a reader sees none of it, and the strict build passes.
        "Before <!-- [hidden][missing] and [x](gone.md) --> after\n",
        [],
        '<!-- [hidden][missing] and <a href="gone.md">x</a> -->',
        id="gap-10-links-inside-a-mid-line-comment",
    ),
    # Emphasis markers in heading slugs (#1385).
    pytest.param(
        # The first `_` can open but nothing closes it, and the second sits inside a word,
        # so both stay: the id is `_a_b`, where the slug once dropped the first one.
        "# _a_b\n\n[ok](#_a_b) [bad](#a_b)\n",
        ["docs/a.md:5: link to '#a_b': no heading or HTML anchor '#a_b' in docs/a.md"],
        '<h1 id="_a_b">_a_b</h1>',
        id="1385-an-unpaired-underscore",
    ),
    pytest.param(
        # The `*` can open but nothing closes it, and the `_` after it can only close, with
        # nothing open: both stay as text. The slug drops the `*` and keeps the `_`, where
        # it once dropped both.
        "# k *_ x\n\n[ok](#k-_-x) [bad](#k-x)\n",
        ["docs/a.md:5: link to '#k-x': no heading or HTML anchor '#k-x' in docs/a.md"],
        '<h1 id="k-_-x">k *_ x</h1>',
        id="1385-an-unpaired-asterisk",
    ),
    pytest.param(
        # A flat pair is dropped and the `_` inside a word is kept.
        "# _a_ b_c\n\n[ok](#a-b_c) [bad](#_a_-b_c)\n",
        ["docs/a.md:5: link to '#_a_-b_c': no heading or HTML anchor '#_a_-b_c' in docs/a.md"],
        '<h1 id="a-b_c"><em>a</em> b_c</h1>',
        id="1385-a-flat-pair",
    ),
    pytest.param(
        # Uneven runs: the 3.11.3 pass leaves the code span's placeholder name in the id.
        "# *a**b*`d`\n",
        [
            f"docs/a.md:3: {_UNSUPPORTED}: emphasis markers in a heading that may nest or "
            "pair unevenly"
        ],
        '<h1 id="abklzzwxh0000">',
        id="1385-uneven-runs-are-refused",
    ),
    pytest.param(
        # A bracket inside emphasis is read a second time, so its pairing is not followed.
        "# *a [t][k]*\n",
        [
            f"docs/a.md:3: {_UNSUPPORTED}: a bracket inside emphasis in a heading, which the "
            "renderer reads twice",
            f"docs/a.md:3: reference-style link [t][k] has no [k]: {_REF}",
        ],
        '<h1 id="a-tk"><em>a [t][k]</em></h1>',
        id="1385-a-bracket-inside-emphasis-is-refused",
    ),
    pytest.param(
        # A rule over a setext underline is a heading: the title stays as text.
        "___\n===\n\n[ok](#___)\n",
        [],
        '<h1 id="___">___</h1>',
        id="1385-a-rule-as-a-setext-title",
    ),
]


@pytest.mark.parametrize(("body", "expected", "rendered"), _GAP_ROWS)
def test_a_closed_gap_is_read_as_the_renderer_reads_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str, expected: list[str], rendered: str
) -> None:
    assert _check(tmp_path, monkeypatch, {"a.md": "# A\n\n" + body}) == expected


def test_the_gap_rows_render_as_python_markdown_does() -> None:
    """One test for all the rows, so the 3-OS matrix, which has no Markdown, skips it once."""
    markdown = _python_markdown("markdown")
    assert markdown.__version__ == _MARKDOWN_VERSION
    for row in _GAP_ROWS:
        body, _, rendered = row.values
        html = markdown.markdown("# A\n\n" + body, extensions=["toc", "tables", "fenced_code"])
        assert rendered in html, (row.id, html)


# --- heading-id de-duplication is not seeded (#515) -------------------------------------
# Python-Markdown's ``toc`` seeds its ``used_ids`` from the ids of elements already in the
# tree. Raw HTML is stashed before ``toc`` runs, so an ``<a id>`` never seeds it. Only an
# extension such as ``attr_list`` (``{#foo}``) or ``md_in_html`` could, and ``mkdocs.yml``
# enables neither.


def test_raw_html_ids_do_not_seed_heading_deduplication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``<a id="foo">`` plus two ``## Foo`` gives ``foo`` and ``foo_1``, as the site does.

    Checked with a real ``mkdocs build`` of this repository's ``mkdocs.yml`` (mkdocs 1.6.1,
    mkdocs-material 9.8.1, Python-Markdown 3.11.3): the page rendered ``<h2 id="foo">``,
    ``<h2 id="foo_1">``, ``<h2 id="bar">`` and ``<h2 id="baz-baz">``. Seeded ids would have
    made the headings ``foo_1``, ``foo_2`` and ``bar_1``. With ``attr_list`` off, ``{#baz}``
    is heading text.
    """
    body = (
        "# A\n\n"
        '<a id="foo"></a>\n\n'
        "## Foo\n\n## Foo\n\n"
        'Inline <span id="bar">x</span>.\n\n'
        "## Bar\n\n"
        "## Baz {#baz}\n\n"
        "[ok](#foo) [ok](#foo_1) [ok](#bar) [ok](#baz-baz)\n"
        "[bad](#foo_2)\n[bad](#bar_1)\n[bad](#baz)\n"
    )
    out = _check(tmp_path, monkeypatch, {"a.md": body})
    assert [o.split(":", 2)[1] for o in out] == ["16", "17", "18"], out
    assert "'#foo_2'" in out[0] and "'#bar_1'" in out[1] and "'#baz'" in out[2]


# --- the model against the renderer (#1013) ---------------------------------------------
# A differential test: generated pages built from the modelled forms, each read by the
# model and rendered by the real Python-Markdown, must agree on three things. It needs
# Python-Markdown, which the base lock does not carry, so the 3-OS matrix skips it; the
# required `docs-build` job installs the pinned docs toolchain and sets
# `TETHER_REQUIRE_PYTHON_MARKDOWN=1`, which turns the skip into a failure (#517).

#: The renderer the model follows: `requirements-docs.txt` pins it for the docs build.
_MARKDOWN_VERSION = "3.11.3"
_FUZZ_SEED = 1013
_FUZZ_PAGES = 4000
#: The share of a heading's pieces that are emphasis markers (#1385).
_FUZZ_EMPHASIS = 0.15


def _fuzz_page(rng: random.Random) -> str:
    """One page of the modelled forms, with every target and key named so it can be traced.

    Inline link targets are ``L<n>.md`` and definition targets ``D<n>.md``. A comment that
    does not open a line holds only the key ``zz`` and targets ``C<n>.md``: the model does
    not check what such a comment hides (gap 10), so the comparison leaves those out of the
    render's side. Headings also hold emphasis markers, paired and unpaired (#1385).
    """
    serial = iter(range(10**6))
    words = ("a", "b", "k", "K", "t", "x y", "a_b")

    def w() -> str:
        return rng.choice(words)

    atoms = (
        lambda: w(),
        lambda: " ",
        lambda: f"[{w()}]",
        lambda: f"[{w()}][{w()}]",
        lambda: f"[{w()}] [{w()}]",
        lambda: f"[{w()}][]",
        lambda: f"![{w()}][{w()}]",
        lambda: f"![{w()}]",
        lambda: f"[{w()}](L{next(serial)}.md)",
        lambda: f"![{w()}](L{next(serial)}.md)",
        lambda: f"[[{w()}](L{next(serial)}.md)]",
        lambda: f"[{w()} [{w()}][{w()}]](L{next(serial)}.md)",
        lambda: f"[{w()}](L{next(serial)}.md (t))",
        lambda: f"`{w()}`",
        lambda: "`",
        lambda: "``",
        lambda: "\\",
        lambda: "\\[",
        lambda: "!",
        lambda: "[",
        lambda: "]",
        lambda: "(",
        lambda: ")",
        lambda: "#",
        lambda: f'<span id="s{next(serial)}">',
        lambda: "<!-- c -->",
        lambda: f"<!-- [{w()}][zz] [{w()}](C{next(serial)}.md) -->",
    )

    # Emphasis markers, paired and unpaired, go in headings alone: elsewhere they change no
    # slug and no link target, and they are outside the model there (#1385).
    emphasis = (
        lambda: "*",
        lambda: "**",
        lambda: "_",
        lambda: "__",
        lambda: f"*{w()}*",
        lambda: f"**{w()}**",
        lambda: f"_{w()}_",
        lambda: f"_{w()}",
    )

    def atom(heading: bool) -> str:
        return rng.choice(emphasis if heading and rng.random() < _FUZZ_EMPHASIS else atoms)()

    def line(heading: bool = False) -> str:
        return "".join(atom(heading) for _ in range(rng.randint(1, 8)))

    out = []
    for _ in range(rng.randint(1, 7)):
        kind = rng.random()
        if kind < 0.22:
            out.append("#" * rng.randint(1, 3) + " " + line(heading=True))
        elif kind < 0.27:
            out.append(rng.choice(("## Same", "## Same", "## Same_1")))
        elif kind < 0.30:
            out += ["", line(heading=True), rng.choice(("===", "---"))]
        elif kind < 0.38:
            out.append(f"[{rng.choice(words)}]: D{next(serial)}.md")
        elif kind < 0.45:
            out.append("> " + line())
        elif kind < 0.49:
            out += ["", "```", line(), "```"]
        elif kind < 0.52:
            out += ["", "    " + line()]
        elif kind < 0.55:
            out += ["", "<!-- c -->"]
        elif kind < 0.58:
            out += ["", f'<div id="d{next(serial)}">', "", "text", "", "</div>"]
        else:
            out.append(line())
        if rng.random() < 0.6:
            out.append("")
    return "\n".join(out) + "\n"


def _model_reading(path: Path) -> tuple[list[str], list[str], list[str]] | None:
    """Anchors, undefined reference keys and inline link targets, or None if refused."""
    _doc.cache_clear()
    _anchors.cache_clear()
    doc = _doc(path)
    if doc.problems:
        return None
    # A key holding `zz` comes from a comment the generator wrote; the render's side
    # leaves those out too.
    refs = sorted(
        r.key or "" for r in doc.refs if r.key not in doc.definitions and "zz" not in (r.key or "")
    )
    links = sorted(t for _, t in doc.links if t.startswith("L"))
    return sorted(_anchors(path)), refs, links


class _Rendered(HTMLParser):
    """The anchors and link targets of a rendered page, as a browser reads them."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: set[str] = set()
        self.links: list[str] = []
        self.heading = 0
        self.loose_emphasis = False  # an emphasis element outside a heading

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.heading += tag in ("h1", "h2", "h3", "h4", "h5", "h6")
        self.loose_emphasis |= tag in ("em", "strong") and not self.heading
        found = dict(attrs)
        if found.get("id"):
            self.anchors.add(found["id"])
        if tag == "a" and found.get("name"):
            self.anchors.add(found["name"])
        self.links += [v for k, v in attrs if k in ("href", "src") and v and v.startswith("L")]

    def handle_endtag(self, tag: str) -> None:
        self.heading -= tag in ("h1", "h2", "h3", "h4", "h5", "h6")

    handle_startendtag = handle_starttag


def test_the_model_matches_python_markdown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Over seeded pages of the modelled forms, the model reads what the renderer renders.

    The render is instrumented: every pair the reference and image-reference patterns try
    and leave as text, its key undefined, is recorded, as the checker reports it. The model
    and the render must give the same anchors, the same undefined keys and the same inline
    link targets for every page the model does not refuse; a refused page is the loud
    outcome and is skipped, and most pages must be read. The seed and the count are fixed,
    so a failure reproduces.
    """
    markdown = _python_markdown("markdown")
    inline = _python_markdown("markdown.inlinepatterns")
    assert markdown.__version__ == _MARKDOWN_VERSION, (
        f"the docs build uses Python-Markdown {markdown.__version__}; the model follows "
        f"{_MARKDOWN_VERSION}. Re-check the model against the new version, then update both."
    )
    # The emphasis patterns the slug model copies are the renderer's own (#1385).
    delimiters = markdown.Markdown().delimiters.delimiters
    assert {t: d.boundary.pattern for t, d in delimiters.items()} == {
        t: d.pattern for t, d in _DELIMITERS.items()
    }
    assert (_ENTITY_RE.pattern, _AUTOMAIL_RE.pattern) == (inline.ENTITY_RE, inline.AUTOMAIL_RE)
    tried: list[str] = []
    handle = inline.ReferenceInlineProcessor.handleMatch

    def recording(self, m, data):
        node, start, end = handle(self, m, data)
        exact = type(self) in (
            inline.ReferenceInlineProcessor,
            inline.ImageReferenceInlineProcessor,
        )
        if exact and node is None and start is not None:
            text, index, _ = self.getText(data, m.end(0))
            key, after, _ = self.evalId(data, index, text)
            key = self.NEWLINE_CLEANUP_RE.sub(" ", key)
            # The checker's own exception: `[key](…)` forming a link leaves no literal use.
            link = inline.LinkInlineProcessor(inline.LINK_RE, self.md)
            if not (data.startswith("(", after) and link.getLink(data, after)[3]):
                tried.append("" if "\x02" in key else key)  # a placeholder in the key
        return node, start, end

    monkeypatch.setattr(inline.ReferenceInlineProcessor, "handleMatch", recording)
    docs = tmp_path / "docs"
    docs.mkdir()
    monkeypatch.setattr(sys.modules[__name__], "DOCS", docs)
    page = docs / "page.md"
    rng = random.Random(_FUZZ_SEED)
    read, mismatches = 0, []
    try:
        for _ in range(_FUZZ_PAGES):
            source = _fuzz_page(rng)
            page.write_text(source, encoding="utf-8")
            model = _model_reading(page)
            if model is None:
                continue
            tried.clear()
            rendered = _Rendered()
            rendered.feed(markdown.markdown(source, extensions=["toc", "tables", "fenced_code"]))
            if rendered.loose_emphasis:
                # A heading line the renderer did not read as one put emphasis in a
                # paragraph, which is outside the model (#1385): skipped, as a refusal is.
                continue
            read += 1
            real = (
                sorted(rendered.anchors),
                sorted(k for k in tried if "zz" not in k),
                sorted(rendered.links),
            )
            if any(row.startswith(" " * _TAB) for row in source.split("\n")):
                # Ambiguous indentation is read as prose on purpose, which can only report
                # more: hold such a page to the safe direction alone. The model may find
                # extra undefined keys and extra targets to check, and fewer anchors.
                agree = (
                    not collections.Counter(real[1]) - collections.Counter(model[1])
                    and not collections.Counter(real[2]) - collections.Counter(model[2])
                    and set(model[0]) <= set(real[0])
                )
            else:
                agree = model == real
            if not agree and len(mismatches) < 5:
                mismatches.append(f"{source!r}\n  model:  {model}\n  render: {real}")
    finally:
        _doc.cache_clear()
        _anchors.cache_clear()
    assert not mismatches, "\n".join(mismatches)
    assert read >= _FUZZ_PAGES * 0.7, f"the model refused {_FUZZ_PAGES - read} of {_FUZZ_PAGES}"


def test_mkdocs_yml_names_no_markdown_extension() -> None:
    """The model above assumes mkdocs' default extensions; adding one must re-check it.

    The literal word ``markdown_extensions`` fails anywhere in the file, comments included,
    so the plain, quoted, explicit ``? key`` and flow-mapping spellings of the key all
    fail, wherever a ``#`` sits on the line. A key spelled with an escape sequence, such
    as ``"markdown_\\x65xtensions"``, is not caught: the module stays stdlib only, so the
    file is not parsed as YAML. A stray use of the word in a value or comment fails too,
    which errs loud.
    """
    named = [
        line
        for line in _MKDOCS_YML.read_text(encoding="utf-8").splitlines()
        if "markdown_extensions" in line
    ]
    assert not named, (
        "mkdocs.yml now sets markdown_extensions. An extension such as attr_list or "
        "md_in_html puts ids in the tree before toc runs, so they seed the heading "
        "de-duplication that _anchors() models as empty (#515). Check _anchors() against "
        "a real mkdocs build before enabling one."
    )

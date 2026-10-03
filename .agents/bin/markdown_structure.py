# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The block structure of a Markdown document, read with a real CommonMark parser.

``claim.py`` reads an issue body to decide whether its *Execution autonomy* admits an autonomous
claim, and it used to read the body's *structure* - which lines belong to a heading's section,
where a list item ends, whether a line is a table row or a fenced example - with hand-written
regular expressions. Those were widened seven times under review on #462 and the eighth read still
found four misreads, every one of which failed **open**: a restriction the expressions did not see
is a claim the gate admits. #469 ends that by reading structure with ``markdown-it-py``, the
CommonMark parser the base lock already pins (ADR-0066).

This module is the whole of that boundary. It knows nothing about autonomy: it turns text into a
tree of the blocks below, each carrying its 0-based source line, and ``claim.py`` applies its own
rules on top. Keeping the two apart is the point - the parser is the one component here that
cannot be improved by another regular expression.

Only block structure is modelled, but each block of inline text is returned two ways. ``text``
is the source as the parser hands it over - between the block's own markup and the end of the
block, soft line breaks as ``\\n``, emphasis, links and inline HTML left in place - for a caller
that must find something the page hides, such as a marker written as a comment. ``plain`` is
what GitHub shows of it: the text of a link rather than its syntax, emphasis and code markup
removed, entities and escapes decoded, a comment gone, a tag and a line break each a space.
``## [Execution autonomy](url)`` renders as the heading *Execution autonomy*, and a caller matching
a key against the source read that heading as a link (#462); ``plain`` is rendered from the
parser's inline tokens so no caller has to do that with a pattern.

**Code is literal.** A fenced or indented code block is returned as :class:`Code` so a caller can
see it, and never as the structure its text resembles: a quoted table row or bullet in an example
is neither.

**Anything this module cannot read raises** :class:`MarkdownStructureError`, and nothing is
dropped. The parser is configured once, below, and the token kinds it can emit are a closed set
this module names in full; a kind outside that set means the configuration changed. A body the
parser itself cannot read raises the same error. A mutex gate must stop on either rather than
silently read past a block, and the caller decides what stopping means.
"""

from __future__ import annotations

import html
import re
import sys
from collections.abc import Iterator
from typing import Any, NamedTuple

try:
    from markdown_it import MarkdownIt
    from markdown_it.token import Token
except ModuleNotFoundError as exc:  # pragma: no cover - environment, not logic
    # `<py>` is the lane's own interpreter, not the restored project environment, so the lock
    # does not provision it; the install line names the interpreter that just failed.
    raise ModuleNotFoundError(
        "markdown-it-py is required by .agents/bin (ADR-0066) and this interpreter lacks it; "
        f"install it once, at the version the base lock pins: {sys.executable} -m pip install "
        '"markdown-it-py==4.2.0"'
    ) from exc

__all__ = [
    "BLOCK_TAGS",
    "Block",
    "BlockQuote",
    "Code",
    "Heading",
    "Html",
    "KEPT_TAGS",
    "ListBlock",
    "ListItem",
    "MarkdownStructureError",
    "Paragraph",
    "Rule",
    "Shown",
    "Table",
    "TableRow",
    "has_tag",
    "inline_html",
    "inline_tags",
    "parse",
    "tags",
    "walk",
]

#: The ``commonmark`` preset plus the GFM ``table`` rule, which is what GitHub renders an issue
#: body with for every shape the gate reads. The preset keeps ``html`` on, so an HTML comment such
#: as the ``tether-grooming-v1`` marker arrives as an :class:`Html` block rather than as text.
#: Not ``gfm-like``: that preset also enables ``linkify``, an optional extra the lock does not
#: carry, and inline autolinking is not structure.
_PARSER = MarkdownIt("commonmark").enable("table")

#: What may precede a block's own first character on its source line: indentation, and the ``>``
#: of each enclosing block quote. Nothing else can - a list marker, a ``#`` and heading text are
#: none of these characters - so the end of this prefix is the block's column. The scan starts
#: at the content column of a list item that opened on the same line, since that marker and its
#: padding precede a same-line child too and are not in this class.
_PREFIX = re.compile(r"[ >]*")

#: Tab stops are every four columns from the start of the line, which is how CommonMark measures
#: indentation and the one measurement a regular expression kept getting wrong (#462). Every
#: line is expanded once, so a column is then simply an index into it.
_TAB = 4

#: The table scaffolding the ``table`` rule emits around the cells, named in full so that a token
#: outside this set raises like one outside the block set rather than being stepped over.
_TABLE_SCAFFOLD = frozenset({"thead_open", "thead_close", "tbody_open", "tbody_close"})
_CELL_OPEN = frozenset({"th_open", "td_open"})
_CELL_CLOSE = frozenset({"th_close", "td_close"})

#: Inline HTML, as it reaches ``plain``. A comment is not rendered and leaves nothing behind, so
#: ``Auto<!-- note -->nomy`` is the one word the page shows; nor is a processing instruction, a
#: declaration or a CDATA section, the three other forms the CommonMark inline HTML grammar
#: admits - the HTML parser reads ``<?note?>`` and ``<![CDATA[x]]>`` as bogus comments and
#: ignores a declaration in the body - so ``Auto<?note?>nomy`` is that one word too (Codex on
#: #462). ``_HTML_HIDDEN`` names all four. A tag is read as the page lays it out, and the
#: page lays out only what GitHub keeps: an issue body passes through html-pipeline's
#: ``SanitizationFilter`` (v3.2.4; its allowlist last changed 2024-02-02), which keeps exactly
#: the elements in ``KEPT_TAGS`` and strips every other tag while leaving its text in place, so
#: ``Auto<foo></foo>nomy`` shows ``Autonomy``, one word - and rendering the unknown tag as a
#: space split the key and lost the restriction after it (Codex on #462). The sanitizer (Selma
#: 0.5.3, ``Config::DEFAULT``) refines that twice: the block-ish stripped tags in its
#: ``whitespace_elements`` - ``<section>``, ``<article>``, ``<nav>`` - leave a space on each side
#: of their text, and ``<svg>``, ``<math>``, ``<noscript>`` and the rest of its
#: ``remove_contents`` go with their text. That last rule is the one not mirrored: text the
#: page hides can only refuse, never admit, so reading it is the safe error. (GFM's tag filter
#: shows ``<script>``, ``<style>`` and seven more as literal text before the sanitizer runs,
#: text and all, so those hide nothing.) Among the kept tags, a *phrasing* one - ``<b>``,
#: ``<em>``, ``<code>``, ``<a>``, ``<span>`` - wraps text without breaking it and leaves nothing,
#: as a stripped tag does, so ``Auto<b>nomy</b>`` is one word (Greptile on #462: a space there
#: split a key the page shows whole); ``<wbr>`` is a break *opportunity* that draws nothing, and
#: an empty ``<picture>`` or ``<source>`` draws nothing either. A *block* one - ``<li>``,
#: ``<p>``, ``<td>``, ``<h2>`` - is laid out as a block of its own, so it leaves a space and
#: ``maintainer</li><li>decision`` stays the two words the page shows, and ``<br>`` and
#: ``<img>`` draw a break and a picture *inside* a block, a space each (Codex on #462). Two
#: kinds of kept tag draw something. ``<q>`` renders quotation marks around its content, so it
#: leaves a ``"`` on each side and ``Auto<q></q>nomy`` is the defaced key the page shows rather
#: than the key. And ``<del>``, ``<s>`` and ``<strike>`` strike their content out, which is a
#: retraction, so they leave the ``~~`` their Markdown spelling keeps (see ``_plain``):
#: ``<del>agent-can-do-alone</del>`` reads as ``~~agent-can-do-alone~~`` and a caller matching
#: a token against it fails to, as it should (both Codex on #462). Either way the text reads as
#: it renders, so a caller sees neither a word the page joins nor one it splits. A character
#: reference in a raw block - ``&#32;``, ``&amp;`` - is decoded after the tags are read, so
#: ``&lt;b&gt;`` is the literal ``<b>`` the page shows and never a tag (Codex on #462). The tag
#: pattern is the CommonMark open and close tag grammar - a name, then attributes whose values
#: may be quoted - so ``n < 5 and m > 3`` stays prose, as on the page, and a ``>`` inside a
#: quoted attribute value does not end the tag early (Codex on #462).
_HTML_HIDDEN = re.compile(r"<!--.*?-->|<\?.*?\?>|<!\[CDATA\[.*?\]\]>|<![A-Za-z][^>]*>", re.S)
_HTML_TAG = re.compile(
    r"<(?:/(?P<close>[A-Za-z][A-Za-z0-9-]*)\s*"
    r"|(?P<open>[A-Za-z][A-Za-z0-9-]*)"
    r"(?:\s+[A-Za-z_:][A-Za-z0-9_.:-]*(?:\s*=\s*(?:[^\s\"'=<>`]+|'[^']*'|\"[^\"]*\"))?)*"
    r"\s*/?)>"
)
#: The kept tags that wrap text and draw nothing.
_PHRASING_TAGS = frozenset(
    {"a", "abbr", "b", "bdo", "cite", "code", "dfn", "em", "i", "ins", "kbd", "mark", "picture"}
    | {"rp", "rt", "ruby", "samp", "small", "source", "span", "strong", "sub", "sup", "time"}
    | {"tt", "var", "wbr"}
)
#: The kept tags that draw a mark around their text.
_DRAWN_TAGS = {"q": '"', "del": "~~", "s": "~~", "strike": "~~"}
#: The kept tags that draw *inside* a block without wrapping text: a line break and a picture.
_INSIDE_TAGS = frozenset({"br", "img"})
#: The kept tags the page lays out as a block of their own - the boundaries a raw run is cut at.
BLOCK_TAGS = frozenset(
    {"blockquote", "caption", "dd", "details", "div", "dl", "dt", "figcaption", "figure", "h1"}
    | {"h2", "h3", "h4", "h5", "h6", "hr", "li", "ol", "p", "pre", "summary", "table", "tbody"}
    | {"td", "tfoot", "th", "thead", "tr", "ul"}
)
#: Every element GitHub keeps: the html-pipeline allowlist, partitioned above by what it draws.
KEPT_TAGS = _PHRASING_TAGS | frozenset(_DRAWN_TAGS) | _INSIDE_TAGS | BLOCK_TAGS
#: The stripped tags whose text the sanitizer wraps in spaces - Selma's ``whitespace_elements``
#: less the ones GitHub keeps.
_STRIPPED_SPACED_TAGS = frozenset(
    {"address", "article", "aside", "footer", "header", "hgroup", "nav", "section"}
)
#: The tags that leave a space: every other tag, kept or stripped, leaves nothing.
_SPACED_TAGS = BLOCK_TAGS | _INSIDE_TAGS | _STRIPPED_SPACED_TAGS
#: The stripped elements the sanitizer removes *with their text*: Selma's ``remove_contents``
#: less the tags GFM's tag filter has already turned into literal text before the sanitizer
#: runs (``<script>``, ``<style>``, ``<iframe>``, ``<xmp>``, ``<noembed>``, ``<noframes>``,
#: ``<plaintext>``, whose text the page therefore shows). What is inside ``<svg>``, ``<math>``
#: or ``<noscript>`` is gone from the page, so ``Auto<svg>x</svg>nomy`` shows ``Autonomy``,
#: and keeping the ``x`` read ``Autoxnomy``, no key, past a restriction (Codex on #462). The
#: element's own tags stay, so a source that carries one is still not plain Markdown, and a
#: tag written inside one - ``<svg></details></svg>`` - is no tag, since the page never sees
#: it. An element never closed runs to the end of its run; one closed in its own tag -
#: ``<svg/>`` - holds nothing, and the text after it stays, where reading it as an opener
#: swallowed the rest of the key (Codex on #462).
_HTML_REMOVED = re.compile(
    r"(?P<void><(?:math|noscript|svg)\b[^>]*/>)"
    r"|(?P<open><(?P<name>math|noscript|svg)\b[^>]*>).*?(?P<close></(?P=name)\s*>|\Z)",
    re.S | re.I,
)
#: An empty ``<del>``, ``<s>`` or ``<strike>`` strikes nothing out and draws nothing, so
#: ``Auto<del></del>nomy`` shows the key whole, where drawing the marks for both tags read
#: ``Auto~~~~nomy`` (Codex on #462). Whitespace alone inside is struck whitespace, which the
#: page shows as the whitespace. An empty ``<q>`` still draws its two quotation marks.
_HTML_EMPTY_STRIKE = re.compile(
    r"<(?P<name>del|s|strike)\b[^>]*>(?P<inside>\s*)</(?P=name)\s*>", re.I
)


def _sanitized(text: str) -> str:
    """``text`` as it reaches the page's tags: the hidden forms gone, then the text of every
    removed element gone with its tags kept. An empty strike element is still a tag here - a
    source carrying one is not plain Markdown - and draws nothing only in :func:`_visible_html`.
    """
    return _HTML_REMOVED.sub(
        lambda m: m.group("void") or m.group("open") + m.group("close"),
        _HTML_HIDDEN.sub("", text),
    )


def _unstruck(text: str) -> str:
    """``text`` with every empty strike element gone, innermost first."""
    while True:
        emptied = _HTML_EMPTY_STRIKE.sub(lambda m: m.group("inside"), text)
        if emptied == text:
            return text
        text = emptied


class MarkdownStructureError(ValueError):
    """The parser emitted a block this module does not model, or could not read the text."""


class Paragraph(NamedTuple):
    """A run of inline text. ``text`` keeps soft line breaks as ``\\n``; ``plain`` is rendered.

    ``pictured`` is whether the inline carries a Markdown image. The page draws the picture there
    and ``plain`` shows its alternative text in its place, so a caller that admits only what the
    page shows as text has to be told (Codex on #462).

    ``footnote`` marks a GitHub footnote definition - a paragraph whose source opens with
    ``[^label]:`` - which the page draws at its foot, label gone, and not where the definition
    sits: ``plain`` is the text behind the label, ``text`` the whole source, and a caller
    reading what sits under a heading, or what an item leads with, skips it (Codex on #462,
    twice; see :func:`_paragraphs`).
    """

    text: str
    plain: str
    line: int
    pictured: bool = False
    footnote: bool = False


class Heading(NamedTuple):
    """An ATX (``#``) or setext (underlined) heading.

    ``column`` is where the first ``#`` - or, for a setext heading, the text - sits on its source
    line, measured from the start of the line even inside a list item or block quote and even
    when the heading opens on the same line as the item's marker, so only a top-level, unquoted,
    unindented heading has column zero. ``markup`` is the ``#`` run or the ``=``/``-`` underline,
    so the two forms are distinguishable too. ``text`` is the heading content with any closing
    ``#`` sequence already removed, and ``plain`` is that content rendered. ``pictured`` is as
    on :class:`Paragraph`.
    """

    level: int
    text: str
    plain: str
    line: int
    column: int
    markup: str
    pictured: bool = False


class ListItem(NamedTuple):
    """One item of a list, with every block the item contains.

    ``marker`` is the bullet character (``-``, ``*`` or ``+``) or the ordered delimiter (``.`` or
    ``)``); ``number`` is the ordered item's digits, or ``None`` for a bullet. ``column`` is where
    the marker - for an ordered item, its first digit - sits on its source line, measured from the
    start of the line even inside a block quote and even for an item that opens on the same line
    as its parent's marker, so only a top-level, unquoted, unindented item has column zero. A
    tight item's single paragraph is a :class:`Paragraph` like any other.
    """

    marker: str
    number: str | None
    line: int
    column: int
    blocks: tuple[Block, ...]


class ListBlock(NamedTuple):
    """A bullet or ordered list."""

    ordered: bool
    line: int
    items: tuple[ListItem, ...]


class BlockQuote(NamedTuple):
    """A ``>`` block quote, with every block it contains."""

    line: int
    blocks: tuple[Block, ...]


class Code(NamedTuple):
    """A fenced or indented code block. ``text`` is literal and ``info`` the fence's info string."""

    text: str
    line: int
    fenced: bool
    info: str


class Shown(NamedTuple):
    """One block the page lays out from a raw HTML run: its rendered ``text`` and the lower-cased
    name of the ``tag`` that opened it - ``h2`` for a heading, ``p``, ``td``, ``li`` - or the
    empty string for text that no tag opened, such as text after a closing tag. A caller reads
    a heading as it reads a Markdown heading and anything else as a paragraph (Codex on #462).
    """

    text: str
    tag: str


class Html(NamedTuple):
    """A block-level run of raw HTML, which is how an HTML comment on its own lines arrives.

    ``shown`` is the text GitHub shows inside it, one :class:`Shown` per block the page lays
    out: a ``<div>`` holding two ``<p>`` is one Markdown block and two rendered paragraphs, and
    a caller matching a key against the whole run read ``Notes Autonomy: ...`` where the page
    shows ``Notes`` over ``Autonomy: ...`` (Codex on #462). Every kept tag the page lays out
    as a block - ``BLOCK_TAGS`` - splits, so a run is never joined across a boundary the page
    draws, and no other tag does, so a run is never cut where the page shows it whole (Codex on
    #462, both ways); a block a tag opens that shows no text is a piece
    with no text, since the page draws its opening; and a comment block shows nothing.
    ``plain`` is the same text as one string, which is what a caller reading for a token wants.
    """

    text: str
    plain: str
    line: int
    shown: tuple[Shown, ...]


class Rule(NamedTuple):
    """A thematic break."""

    line: int


class TableRow(NamedTuple):
    """One row of a GFM table; ``header`` marks the row above the delimiter line.

    ``cells`` are the cells' inline source and ``plain`` the same cells rendered, in order.
    """

    cells: tuple[str, ...]
    plain: tuple[str, ...]
    line: int
    header: bool


class Table(NamedTuple):
    """A GFM table, which GitHub recognizes with or without the outer ``|`` on each row."""

    line: int
    rows: tuple[TableRow, ...]


Block = Paragraph | Heading | ListBlock | BlockQuote | Code | Html | Rule | Table


def parse(text: str) -> tuple[Block, ...]:
    """The top-level blocks of ``text``, in document order, with the GitHub footnote
    definitions the parser swallowed as reference definitions put back as the footnote
    paragraphs the page draws (:func:`_footnotes`, :func:`_paragraphs`).

    Line endings are folded to ``\\n`` first, as the parser folds them, so every ``line`` indexes
    ``text.splitlines()`` whichever convention the source used. A missing final newline is
    supplied: it changes no block, and the pinned parser (4.2.0) indexes past the end of a body
    that ends in a quoted table followed by a bare ``>`` without one (markdown-it-py #415). Any
    error the parser still raises becomes :class:`MarkdownStructureError`, so a body this module
    cannot read is reported as unreadable rather than as a traceback from inside the parser.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if text and not text.endswith("\n"):
        text += "\n"
    lines = [line.expandtabs(_TAB) for line in text.split("\n")]
    env: dict[str, Any] = {}
    try:
        tokens = _PARSER.parse(text, env)
    except Exception as exc:
        raise MarkdownStructureError(f"the parser could not read this text: {exc!r}") from exc
    blocks, end = _blocks(tokens, 0, None, lines, None, env)
    if end != len(tokens):  # pragma: no cover - the parser balances its own tokens
        raise MarkdownStructureError(f"unbalanced token stream at {end}")
    footnotes = _footnotes(env, lines)
    if not footnotes:
        return blocks
    return tuple(sorted(blocks + footnotes, key=lambda block: block.line))


#: A GitHub footnote definition's label at the start of a source line: cmark-gfm's grammar,
#: ``[^``, anything but ``]`` and whitespace, ``]:``, then any blank. Up to three spaces may
#: precede it, as before any block opener.
_FOOTNOTE_LABEL = re.compile(r" {0,3}\[\^[^\]\s]+\]:[ \t]*")


def _footnote(text: str, label: re.Match[str], line: int, env: dict[str, Any]) -> Paragraph:
    """The footnote paragraph whose source is ``text``, ``label`` its ``_FOOTNOTE_LABEL`` match."""
    rest = text[label.end() :]
    if not rest.strip():
        return Paragraph(text, "", line, footnote=True)
    inline = _PARSER.parseInline(rest, env)[0]
    return Paragraph(text, _plain(inline), line, _pictured(inline), footnote=True)


def _paragraphs(inline: Token, line: int, env: dict[str, Any]) -> list[Paragraph]:
    """The paragraph the inline token holds - or the paragraphs, where GitHub's footnote
    definitions cut it.

    GitHub renders footnotes and the preset has no rule for them, so ``[^1]: Autonomy:
    maintainer decision required`` is a paragraph here whose text opens with the label, while
    the page draws the restriction at its foot, label gone (Codex on #462). Such a paragraph is
    a footnote paragraph: ``plain`` is the text behind the label. And cmark-gfm opens a footnote
    definition on any line that starts with one, closing the paragraph above it as a block
    quote opener would, where CommonMark reads that line as a lazy continuation of the
    paragraph; so a paragraph is cut at every later line that opens a definition, each cut a
    footnote paragraph at its own line.
    """
    text = inline.content
    cuts = [0] + [
        at for at, piece in enumerate(text.split("\n")) if at and _FOOTNOTE_LABEL.match(piece)
    ]
    if len(cuts) == 1:
        label = _FOOTNOTE_LABEL.match(text)
        if label is not None:
            return [_footnote(text, label, line, env)]
        return [Paragraph(text, _plain(inline), line, _pictured(inline))]
    pieces = text.split("\n")
    found: list[Paragraph] = []
    for start, end in zip(cuts, cuts[1:] + [len(pieces)], strict=True):
        chunk = "\n".join(pieces[start:end])
        label = _FOOTNOTE_LABEL.match(chunk)
        if label is None:
            shown = _PARSER.parseInline(chunk, env)[0]
            found.append(Paragraph(chunk, _plain(shown), line, _pictured(shown)))
        else:
            found.append(_footnote(chunk, label, line + start, env))
    return found


def _footnotes(env: dict[str, Any], lines: list[str]) -> tuple[Paragraph, ...]:
    """The GitHub footnote definitions the reference rule swallowed, as the footnote
    paragraphs the page draws, each at its source line.

    ``[^1]: Autonomy:`` is a link reference definition to CommonMark - a label, a colon, a
    destination - so the parser files it under ``env["references"]`` and emits no block, while
    GitHub, whose footnotes extension reads ``[^`` first, draws it as a footnote at the foot of
    the body: a paragraph holding the rest of the line. A caller that reads what the page shows
    then never saw the declaration (Codex on #462). Each such reference comes back here as that
    footnote paragraph - its source from the ``[^`` on - sorted in by line, after a block that
    opens on the same line. Only a definition whose text is a bare destination, one word with
    an optional quoted title, is swallowed: one with a sentence after the label is no
    definition and is already the paragraph. A reference whose label does not open with ``^``
    is a link definition on GitHub too, and draws nothing - as is one whose label opens with
    ``^`` but is no footnote label, ``[^release note]: /url`` with its space, which the parser
    files under a ``^`` key all the same (Codex on #462): the source line decides, read by the
    footnote grammar, and a line that fails it draws nothing.
    """
    found: list[Paragraph] = []
    for key, reference in (env.get("references") or {}).items():
        if not key.startswith("^") or "map" not in reference:
            continue
        start, end = reference["map"]
        source = "\n".join(lines[start:end])
        text = source[source.find("[^") :]
        label = _FOOTNOTE_LABEL.match(text)
        if label is not None:
            found.append(_footnote(text, label, start, env))
    return tuple(found)


def walk(blocks: tuple[Block, ...] | list[Block]) -> Iterator[Block]:
    """Every block in ``blocks``, depth first, each container before its contents."""
    for block in blocks:
        yield block
        if isinstance(block, ListBlock):
            for item in block.items:
                yield from walk(item.blocks)
        elif isinstance(block, BlockQuote):
            yield from walk(block.blocks)


def _column(lines: list[str], line: int, origin: tuple[int, int] | None) -> int:
    """The column of the first character on ``line`` that is not indentation or a quote marker.

    ``origin`` is where the enclosing list item's content begins, as ``(line, column)``, or
    ``None`` outside any item. A block that opens on that same line - ``- # Notes``, ``- - b`` -
    is scanned from that column, past the parent's marker and padding; every other line is scanned
    from its start, where indentation and ``>`` markers are all that can precede the block.
    """
    start = origin[1] if origin is not None and origin[0] == line else 0
    prefix = _PREFIX.match(lines[line], start)
    assert prefix is not None  # a `*` pattern always matches
    return prefix.end()


def _content_column(text: str, column: int) -> int:
    """Where the content of the list item whose marker sits at ``column`` begins on ``text``.

    CommonMark's rule, applied to the expanded line: the marker, then one to four columns of
    padding, is the content column. Five or more columns of padding mean the item opens with
    indented code and the content column is one past the marker; so is an empty marker.
    """
    end = column + 1
    if text[column] not in "-*+":
        # An ordered marker: the digits, then the `.` or `)` the parser already accepted.
        while text[end].isdigit():
            end += 1
        end += 1
    padded = end
    while padded < len(text) and text[padded] == " ":
        padded += 1
    padding = padded - end
    return end + padding if 1 <= padding <= 4 else end + 1


def _plain(inline: Token) -> str:
    """The inline token's content as GitHub shows it, whitespace collapsed to single spaces.

    Rendered from the children the parser produced, not from the source: a link is its text, the
    markup of emphasis and inline code is gone (the code's content stays - it is shown), an image
    is its alternative text, a soft or hard break is a space, and the parser has already decoded
    entities and backslash escapes into the ``text`` children. Inline HTML follows the rule the
    module-level patterns state, and the children are rendered *together* - the text escaped,
    the inline HTML raw, the whole laid out as :func:`_visible_html` lays out a raw run - so a
    tag's effect on what sits between it and its close is read across the children: the text
    inside ``<svg>x</svg>`` is removed and an empty ``<del></del>`` draws nothing, where
    rendering each tag on its own kept the ``x`` and drew ``~~~~`` (Codex on #462). The
    escaping is undone with the character references at the end, so ``n < 5`` and a code
    span's ``<b>`` are the text they were. Strike-through is a GFM extension the parser does
    not enable, so ``~~x~~`` keeps its tildes: GitHub shows that text struck out, and a caller
    matching a token against it then fails to - the right direction, since a struck-out value
    is a retracted one. An inline the parser gave no children is its content as it stands,
    which only happens for an inline it did not need to tokenize.
    """
    if inline.children is None:
        return " ".join(inline.content.split())
    parts: list[str] = []
    for child in inline.children:
        if child.type in ("text", "code_inline", "image"):
            parts.append(html.escape(child.content, quote=False))
        elif child.type in ("softbreak", "hardbreak"):
            parts.append(" ")
        elif child.type == "html_inline":
            parts.append(child.content)
        # Every other child is the open or close of a span - emphasis, a link - and has no text.
    return " ".join(_visible_html("".join(parts)).split())


def _pictured(inline: Token) -> bool:
    """Whether the inline token carries an image, by the parser's reading of it.

    Read off the children rather than the source so that an escaped `\\![x](y)`, a `![x](y)`
    inside a code span and a reference-style `![x][ref]` whose definition sits elsewhere in the
    body are each answered as the page answers them: the first two are text, the last is a
    picture. An image inside a link or emphasis is still a top-level child here.
    """
    return any(child.type == "image" for child in inline.children or ())


def tags(text: str) -> Iterator[tuple[str, bool]]:
    """Every HTML tag in ``text`` - a block's source ``text`` - in order, as ``(name, closing)``.

    Read by the grammar above with the hidden forms removed first, so a tag written inside a
    comment is not a tag: ``<!-- </details> -->`` closes nothing on the page and must close
    nothing for a caller counting nesting (Codex on #462) - and the text of a removed element
    with them, so ``<svg></details></svg>`` closes nothing either, while the ``<svg>`` itself
    is still a tag. Names are lower-cased.
    """
    for tag in _HTML_TAG.finditer(_sanitized(text)):
        closing = tag.group("close") is not None
        yield (tag.group("close") or tag.group("open")).lower(), closing


def inline_html(text: str) -> Iterator[str]:
    """Every run of inline HTML in the inline ``text`` of a paragraph, heading or table cell,
    as the parser found it - a tag, a comment - in order.

    A tag or a comment inside a code span or behind a backslash is text on the page and is not
    here. A caller looking for a marker the page hides has to look here rather than in the
    source, where ``Use `<!-- tether-grooming-v1 -->` when re-grooming`` reads as the marker
    it only quotes (Codex on #462).
    """
    for token in _PARSER.parseInline(text, {}):
        for child in token.children or ():
            if child.type == "html_inline":
                yield child.content


def inline_tags(text: str) -> Iterator[tuple[str, bool]]:
    """Every HTML tag in the inline ``text`` of a paragraph, heading or table cell, as
    :func:`tags` reads them, taken from the inline HTML the parser found there.

    A tag inside a code span or behind a backslash is text on the page and is not here, which
    :func:`has_tag` - a search of the source - cannot tell. A caller counting what a `<details>`
    opened in running text collapses needs the page's reading, because the HTML parser closes
    the paragraph where that tag opens and the widget takes everything up to its `</details>`.
    The chunks are read together, so a tag inside a removed element - ``<svg></details></svg>``
    - is no tag here either (Codex on #462).
    """
    yield from tags("".join(inline_html(text)))


def has_tag(text: str) -> bool:
    """Whether ``text`` - a block's source ``text`` - carries an HTML tag, by the grammar above.

    A comment is not a tag: it draws nothing and a caller has its own reasons to look for one. A
    caller that admits only plain Markdown asks this of a declaration's source, because every
    rendering rule above is an approximation of a browser and the one direction that approximation
    must never err in is the admitting one (Codex on #462, repeatedly).
    """
    return next(tags(text), None) is not None


def _visible_html(text: str) -> str:
    """Raw HTML as GitHub shows it: a tag that draws something what it draws, a tag in
    ``_SPACED_TAGS`` a space, every other tag - hidden, phrasing or stripped - nothing, and
    character references decoded last."""

    def laid_out(tag: re.Match[str]) -> str:
        name = (tag.group("open") or tag.group("close")).lower()
        if name in _DRAWN_TAGS:
            return _DRAWN_TAGS[name]
        return " " if name in _SPACED_TAGS else ""

    return html.unescape(_HTML_TAG.sub(laid_out, _unstruck(_sanitized(text))))


def _shown_html(text: str) -> tuple[Shown, ...]:
    """Raw HTML as GitHub lays it out, one :class:`Shown` per block it draws.

    The run is cut at every tag in ``BLOCK_TAGS`` - the kept tags the page lays out as a block
    of their own - each piece then rendered as :func:`_visible_html` renders the whole and
    named for the opening tag it follows. Cutting at a tag the page draws inside a block would
    only split a run the page shows whole - a caller reading a key off a piece then reads an
    empty value, which fails closed - while *not* cutting at one the page draws as a boundary
    joins two blocks into text the page never shows, which is the direction that admitted
    (Codex on #462). So the rule errs toward cutting, among the tags the page keeps; a tag it
    strips is no boundary at all, and cutting ``<p>Auto<foo></foo>nomy:</p>`` at the unknown
    tag read a key the page shows whole as two pieces (Codex on #462).

    A piece with no text is kept when a tag opened it: the page draws something for the
    opening of a block - a rule for ``<hr>``, a widget for ``<details>``, a box - and a caller
    reading the first thing drawn after a key must see it, where dropping it let the paragraph
    past an ``<hr>`` stand as a raw heading's own value (Codex on #462). Text that no tag
    opened and that renders to nothing is nothing.
    """
    stripped = _sanitized(text)
    pieces: list[tuple[str, str]] = []
    at, opener = 0, ""
    for tag in _HTML_TAG.finditer(stripped):
        name = (tag.group("open") or tag.group("close")).lower()
        if name not in BLOCK_TAGS:
            continue
        pieces.append((stripped[at : tag.start()], opener))
        at = tag.end()
        opener = "" if tag.group("close") is not None else name
    pieces.append((stripped[at:], opener))
    shown = ((" ".join(_visible_html(piece).split()), opener) for piece, opener in pieces)
    return tuple(Shown(piece, opener) for piece, opener in shown if piece or opener)


def _line(token: Token) -> int:
    if token.map is None:  # pragma: no cover - every block token the parser opens carries a map
        raise MarkdownStructureError(f"{token.type} token carries no source line")
    return token.map[0]


def _blocks(
    tokens: list[Token],
    at: int,
    until: str | None,
    lines: list[str],
    origin: tuple[int, int] | None,
    env: dict[str, Any],
) -> tuple[tuple[Block, ...], int]:
    """Read blocks from ``tokens[at:]`` up to the ``until`` closing token, which is consumed.

    Returns the blocks and the index just past what was read. ``until`` is ``None`` at the top
    level, where reading stops at the end of the stream. ``origin`` is the enclosing list item's
    content position, passed to :func:`_column`. ``env`` is the parser's environment, which
    holds the reference definitions a footnote's text may link through.
    """
    found: list[Block] = []
    while at < len(tokens):
        token = tokens[at]
        if until is not None and token.type == until:
            return tuple(found), at + 1
        if token.type == "paragraph_open":
            found.extend(_paragraphs(tokens[at + 1], _line(token), env))
            at += 3
        elif token.type == "heading_open":
            line = _line(token)
            inline = tokens[at + 1]
            found.append(
                Heading(
                    int(token.tag[1:]),
                    inline.content,
                    _plain(inline),
                    line,
                    _column(lines, line, origin),
                    token.markup,
                    _pictured(inline),
                )
            )
            at += 3
        elif token.type in ("bullet_list_open", "ordered_list_open"):
            items, at = _items(
                tokens, at + 1, token.type.replace("open", "close"), lines, origin, env
            )
            found.append(ListBlock(token.type == "ordered_list_open", _line(token), items))
        elif token.type == "blockquote_open":
            inner, at = _blocks(tokens, at + 1, "blockquote_close", lines, origin, env)
            found.append(BlockQuote(_line(token), inner))
        elif token.type == "fence":
            found.append(Code(token.content, _line(token), True, token.info.strip()))
            at += 1
        elif token.type == "code_block":
            found.append(Code(token.content, _line(token), False, ""))
            at += 1
        elif token.type == "html_block":
            text = token.content.rstrip("\n")
            shown = _shown_html(text)
            plain = " ".join(piece.text for piece in shown if piece.text)
            found.append(Html(text, plain, _line(token), shown))
            at += 1
        elif token.type == "hr":
            found.append(Rule(_line(token)))
            at += 1
        elif token.type == "table_open":
            rows, at = _rows(tokens, at + 1)
            found.append(Table(_line(token), rows))
        else:
            raise MarkdownStructureError(f"unmodelled block token {token.type!r}")
    if until is not None:  # pragma: no cover - the parser balances its own tokens
        raise MarkdownStructureError(f"missing {until}")
    return tuple(found), at


def _items(
    tokens: list[Token],
    at: int,
    until: str,
    lines: list[str],
    origin: tuple[int, int] | None,
    env: dict[str, Any],
) -> tuple[tuple[ListItem, ...], int]:
    items: list[ListItem] = []
    while tokens[at].type != until:
        token = tokens[at]
        if token.type != "list_item_open":
            raise MarkdownStructureError(f"unexpected {token.type!r} inside a list")
        line = _line(token)
        column = _column(lines, line, origin)
        content = (line, _content_column(lines[line], column))
        inner, at = _blocks(tokens, at + 1, "list_item_close", lines, content, env)
        items.append(ListItem(token.markup, token.info or None, line, column, inner))
    return tuple(items), at + 1


def _rows(tokens: list[Token], at: int) -> tuple[tuple[TableRow, ...], int]:
    """Read every ``tr`` of a table up to ``table_close``, which is consumed.

    Every token between is named: the head and body scaffolding, a row, a cell and its inline
    content. Anything else raises, as an unmodelled block does, rather than being stepped over.
    """
    rows: list[TableRow] = []
    header = False
    while tokens[at].type != "table_close":
        token = tokens[at]
        if token.type in _TABLE_SCAFFOLD:
            header = token.type == "thead_open"
        elif token.type == "tr_open":
            cells: list[str] = []
            plain: list[str] = []
            line = _line(token)
            at += 1
            while tokens[at].type != "tr_close":
                if tokens[at].type not in _CELL_OPEN:
                    raise MarkdownStructureError(f"unmodelled table token {tokens[at].type!r}")
                if tokens[at + 1].type != "inline" or tokens[at + 2].type not in _CELL_CLOSE:
                    raise MarkdownStructureError("a table cell without its inline content")
                cells.append(tokens[at + 1].content)
                plain.append(_plain(tokens[at + 1]))
                at += 3
            rows.append(TableRow(tuple(cells), tuple(plain), line, header))
        else:
            raise MarkdownStructureError(f"unmodelled table token {token.type!r}")
        at += 1
    return tuple(rows), at + 1

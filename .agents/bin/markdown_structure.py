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
from typing import NamedTuple

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
    "Block",
    "BlockQuote",
    "Code",
    "Heading",
    "Html",
    "ListBlock",
    "ListItem",
    "MarkdownStructureError",
    "Paragraph",
    "Rule",
    "Table",
    "TableRow",
    "has_tag",
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
#: ``Auto<!-- note -->nomy`` is the one word the page shows. A tag is read as the page lays it
#: out: a *phrasing* tag - ``<b>``, ``<em>``, ``<code>``, ``<a>``, ``<span>`` and the rest of the
#: set below - wraps text without breaking it, so it leaves nothing and ``Auto<b>nomy</b>`` is
#: one word (Greptile on #462: a space there split a key the page shows whole); any other tag -
#: ``<li>``, ``<br>``, ``<p>``, ``<td>`` - is laid out as a break, so it leaves a space and
#: ``maintainer</li><li>decision`` stays the two words the page shows; ``<wbr>`` is a break
#: *opportunity* that draws nothing, and an empty ``<picture>`` or ``<source>`` draws nothing
#: either, so they are phrasing, while ``<img>`` draws a picture between two words and is a
#: space (Codex on #462). Two kinds of phrasing tag
#: draw something. ``<q>`` renders quotation marks around its content, so it leaves a ``"`` on
#: each side and ``Auto<q></q>nomy`` is the defaced key the page shows rather than the key. And
#: ``<del>``, ``<s>`` and ``<strike>`` strike their content out, which is a retraction, so they
#: leave the ``~~`` their Markdown spelling keeps (see ``_plain``): ``<del>agent-can-do-alone``
#: ``</del>`` reads as ``~~agent-can-do-alone~~`` and a caller matching a token against it fails
#: to, as it should (both Codex on #462). Either way the text reads as it renders, so a caller
#: sees neither a word the page joins nor one it splits. A character reference in a raw block -
#: ``&#32;``, ``&amp;`` - is decoded after the tags are read, so ``&lt;b&gt;`` is the literal
#: ``<b>`` the page shows and never a tag (Codex on #462). The tag pattern is the CommonMark open
#: and close tag grammar - a name, then attributes whose values may be quoted - so
#: ``n < 5 and m > 3`` stays prose, as on the page, and a ``>`` inside a quoted attribute value
#: does not end the tag early (Codex on #462).
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_HTML_TAG = re.compile(
    r"<(?:/(?P<close>[A-Za-z][A-Za-z0-9-]*)\s*"
    r"|(?P<open>[A-Za-z][A-Za-z0-9-]*)"
    r"(?:\s+[A-Za-z_:][A-Za-z0-9_.:-]*(?:\s*=\s*(?:[^\s\"'=<>`]+|'[^']*'|\"[^\"]*\"))?)*"
    r"\s*/?)>"
)
_PHRASING_TAGS = frozenset(
    {"a", "abbr", "b", "bdi", "bdo", "cite", "code", "dfn", "em", "font", "i", "ins", "kbd", "mark"}
    | {"picture", "rp", "rt", "ruby", "samp", "small", "source", "span", "strong", "sub", "sup"}
    | {"time", "tt", "u", "var", "wbr"}
)
_DRAWN_TAGS = {"q": '"', "del": "~~", "s": "~~", "strike": "~~"}


class MarkdownStructureError(ValueError):
    """The parser emitted a block this module does not model, or could not read the text."""


class Paragraph(NamedTuple):
    """A run of inline text. ``text`` keeps soft line breaks as ``\\n``; ``plain`` is rendered."""

    text: str
    plain: str
    line: int


class Heading(NamedTuple):
    """An ATX (``#``) or setext (underlined) heading.

    ``column`` is where the first ``#`` - or, for a setext heading, the text - sits on its source
    line, measured from the start of the line even inside a list item or block quote and even
    when the heading opens on the same line as the item's marker, so only a top-level, unquoted,
    unindented heading has column zero. ``markup`` is the ``#`` run or the ``=``/``-`` underline,
    so the two forms are distinguishable too. ``text`` is the heading content with any closing
    ``#`` sequence already removed, and ``plain`` is that content rendered.
    """

    level: int
    text: str
    plain: str
    line: int
    column: int
    markup: str


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


class Html(NamedTuple):
    """A block-level run of raw HTML, which is how an HTML comment on its own lines arrives.

    ``plain`` is the text GitHub shows inside it - a ``<summary>``'s words, say - with every tag
    and comment removed by the same rule inline HTML gets, so a comment block is empty.
    """

    text: str
    plain: str
    line: int


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
    """The top-level blocks of ``text``, in document order.

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
    try:
        tokens = _PARSER.parse(text)
    except Exception as exc:
        raise MarkdownStructureError(f"the parser could not read this text: {exc!r}") from exc
    blocks, end = _blocks(tokens, 0, None, lines, None)
    if end != len(tokens):  # pragma: no cover - the parser balances its own tokens
        raise MarkdownStructureError(f"unbalanced token stream at {end}")
    return blocks


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
    module-level patterns state. Strike-through is a GFM extension the parser does not enable, so
    ``~~x~~`` keeps its tildes: GitHub shows that text struck out, and a caller matching a token
    against it then fails to - the right direction, since a struck-out value is a retracted one.
    An inline the parser gave no children is its content as it stands, which only happens for an
    inline it did not need to tokenize.
    """
    if inline.children is None:
        return " ".join(inline.content.split())
    parts: list[str] = []
    for child in inline.children:
        if child.type in ("text", "code_inline", "image"):
            parts.append(child.content)
        elif child.type in ("softbreak", "hardbreak"):
            parts.append(" ")
        elif child.type == "html_inline":
            parts.append(_visible_html(child.content))
        # Every other child is the open or close of a span - emphasis, a link - and has no text.
    return " ".join("".join(parts).split())


def tags(text: str) -> Iterator[tuple[str, bool]]:
    """Every HTML tag in ``text`` - a block's source ``text`` - in order, as ``(name, closing)``.

    Read by the grammar above with comments removed first, so a tag written inside a comment is
    not a tag: ``<!-- </details> -->`` closes nothing on the page and must close nothing for a
    caller counting nesting (Codex on #462). Names are lower-cased.
    """
    for tag in _HTML_TAG.finditer(_HTML_COMMENT.sub("", text)):
        closing = tag.group("close") is not None
        yield (tag.group("close") or tag.group("open")).lower(), closing


def has_tag(text: str) -> bool:
    """Whether ``text`` - a block's source ``text`` - carries an HTML tag, by the grammar above.

    A comment is not a tag: it draws nothing and a caller has its own reasons to look for one. A
    caller that admits only plain Markdown asks this of a declaration's source, because every
    rendering rule above is an approximation of a browser and the one direction that approximation
    must never err in is the admitting one (Codex on #462, repeatedly).
    """
    return next(tags(text), None) is not None


def _visible_html(text: str) -> str:
    """Raw HTML as GitHub shows it: a comment or phrasing tag leaves nothing, other tags a space,
    a tag that draws something what it draws, and character references decoded last."""

    def laid_out(tag: re.Match[str]) -> str:
        name = (tag.group("open") or tag.group("close")).lower()
        if name in _DRAWN_TAGS:
            return _DRAWN_TAGS[name]
        return "" if name in _PHRASING_TAGS else " "

    return html.unescape(_HTML_TAG.sub(laid_out, _HTML_COMMENT.sub("", text)))


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
) -> tuple[tuple[Block, ...], int]:
    """Read blocks from ``tokens[at:]`` up to the ``until`` closing token, which is consumed.

    Returns the blocks and the index just past what was read. ``until`` is ``None`` at the top
    level, where reading stops at the end of the stream. ``origin`` is the enclosing list item's
    content position, passed to :func:`_column`.
    """
    found: list[Block] = []
    while at < len(tokens):
        token = tokens[at]
        if until is not None and token.type == until:
            return tuple(found), at + 1
        if token.type == "paragraph_open":
            inline = tokens[at + 1]
            found.append(Paragraph(inline.content, _plain(inline), _line(token)))
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
                )
            )
            at += 3
        elif token.type in ("bullet_list_open", "ordered_list_open"):
            items, at = _items(tokens, at + 1, token.type.replace("open", "close"), lines, origin)
            found.append(ListBlock(token.type == "ordered_list_open", _line(token), items))
        elif token.type == "blockquote_open":
            inner, at = _blocks(tokens, at + 1, "blockquote_close", lines, origin)
            found.append(BlockQuote(_line(token), inner))
        elif token.type == "fence":
            found.append(Code(token.content, _line(token), True, token.info.strip()))
            at += 1
        elif token.type == "code_block":
            found.append(Code(token.content, _line(token), False, ""))
            at += 1
        elif token.type == "html_block":
            text = token.content.rstrip("\n")
            found.append(Html(text, " ".join(_visible_html(text).split()), _line(token)))
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
) -> tuple[tuple[ListItem, ...], int]:
    items: list[ListItem] = []
    while tokens[at].type != until:
        token = tokens[at]
        if token.type != "list_item_open":
            raise MarkdownStructureError(f"unexpected {token.type!r} inside a list")
        line = _line(token)
        column = _column(lines, line, origin)
        content = (line, _content_column(lines[line], column))
        inner, at = _blocks(tokens, at + 1, "list_item_close", lines, content)
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

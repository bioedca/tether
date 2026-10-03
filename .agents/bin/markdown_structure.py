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
    "TABLE_PART_TAGS",
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
#: #462). ``_HTML_HIDDEN`` names all four, **as the page reads them**: a bogus comment ends at
#: the first ``>``, wherever cmark's own grammar closed the form, and the rest of the form is
#: text the page draws - ``<?note <!-- x --> ?>`` draws ``?>`` and ``<![CDATA[<b>y</b>]]>``
#: draws ``y]]>`` (GitHub's markdown endpoint, 2026-10-03). The instruction and the CDATA
#: section are forms at all only where cmark's close follows, so each requires it ahead;
#: without it the ``<`` is the text the page shows. A comment is read the same way: the page's
#: parser takes ``<!-->`` and ``<!--->`` as empty comments and closes one at ``--!>`` as at
#: ``-->``, so ``<!-->Autonomy:`` shows the key at the front of its line, where reading the
#: five characters as text pushed the key off it, and ``<!-- x --!> b -->`` draws ``b -->``,
#: where reading to cmark's close hid it (GitHub's markdown endpoint, 2026-10-03); the parser
#: pinned here already takes those spans as inline HTML by the same CommonMark rule, so only
#: the grammar here had to follow. A raw block opening a comment it never closes is a comment
#: to the end, as the page's parser has it. A tag is read as the page lays it out, and the
#: page lays out only what GitHub keeps: an issue body passes through html-pipeline's
#: ``SanitizationFilter`` (v3.2.4; its allowlist last changed 2024-02-02), which keeps exactly
#: the elements in ``KEPT_TAGS`` and strips every other tag while leaving its text in place, so
#: ``Auto<foo></foo>nomy`` shows ``Autonomy``, one word - and rendering the unknown tag as a
#: space split the key and lost the restriction after it (Codex on #462). The sanitizer (Selma
#: 0.5.3, ``Config::DEFAULT``) refines that once on the page: the block-ish stripped tags in
#: its ``whitespace_elements`` - ``<section>``, ``<article>``, ``<nav>`` - leave a space on each
#: side of their text. Its ``remove_contents`` - ``<svg>``, ``<math>``, ``<noscript>`` - is
#: **not** in force there: GitHub's markdown endpoint renders ``Auto<svg>y</svg>nomy: x`` as
#: ``Autoynomy: x`` and ``<svg>maintainer decision required</svg>`` as that paragraph
#: (2026-10-03), so those three are stripped tags like any other, text kept, and a rule that
#: dropped their text - taken from the sanitizer's source rather than from the page at read 29
#: of #462 - hid a restriction the page shows, which is the one direction this module must not
#: err in; read 31 reversed it. (GFM's tag filter shows ``<script>``, ``<style>`` and seven
#: more as literal text before the sanitizer runs, text and all, so those hide nothing
#: either.) Among the kept tags, a *phrasing* one - ``<b>``,
#: ``<em>``, ``<code>``, ``<a>``, ``<span>`` - wraps text without breaking it and leaves nothing,
#: as a stripped tag does, so ``Auto<b>nomy</b>`` is one word (Greptile on #462: a space there
#: split a key the page shows whole); ``<wbr>`` is a break *opportunity* that draws nothing, and
#: an empty ``<picture>`` or ``<source>`` draws nothing either. A *block* one - ``<li>``,
#: ``<p>``, ``<td>``, ``<h2>`` - is laid out as a block of its own, so it leaves a space and
#: ``maintainer</li><li>decision`` stays the two words the page shows, and ``<br>`` and
#: ``<img>`` draw a break and a picture *inside* a block, a space each (Codex on #462) - the
#: picture with its ``alt`` text between the spaces, since the page shows that text where the
#: picture is not shown, as a Markdown image's ``plain`` shows its alternative text (Codex on
#: #462; ``_attributes``). Two
#: kinds of kept tag draw something. ``<q>`` renders quotation marks around its content, so it
#: leaves a ``"`` on each side and ``Auto<q></q>nomy`` is the defaced key the page shows rather
#: than the key. And ``<del>``, ``<s>`` and ``<strike>`` strike their content out, which is a
#: retraction, so the text they strike is laid out between the ``~~`` marks its Markdown
#: spelling keeps (see ``_struck_marks``): ``<del>agent-can-do-alone</del>`` reads as
#: ``~~agent-can-do-alone~~`` and a caller matching the registered value against it fails to,
#: as it should, while ``<del>maintainer decision required</del>`` still names its restriction
#: to a caller scanning for one - the marks retract an admission and never a restriction (all
#: Codex on #462). Either way the text reads as it renders, so a caller sees neither a word
#: the page joins nor one it splits. A character
#: reference in a raw block - ``&#32;``, ``&amp;`` - is decoded after the tags are read, so
#: ``&lt;b&gt;`` is the literal ``<b>`` the page shows and never a tag (Codex on #462). The tag
#: pattern is the CommonMark open and close tag grammar - a name, then attributes whose values
#: may be quoted - so ``n < 5 and m > 3`` stays prose, as on the page, and a ``>`` inside a
#: quoted attribute value does not end the tag early (Codex on #462). The attribute grammar is
#: one fragment, ``_HTML_ATTRIBUTES``, and every pattern that reads a tag shares it: a pattern
#: with a ``[^>]*`` of its own stopped at ``title=">"`` and read the rest of the key as the
#: element's inside (Codex on #462).
_HTML_COMMENT = r"<!-->|<!--->|<!--(?:.*?(?:-->|--!>)|.*)"
_HTML_HIDDEN = re.compile(
    rf"{_HTML_COMMENT}|<\?(?=.*?\?>)[^>]*>|<!\[CDATA\[(?=.*?\]\]>)[^>]*>|<![A-Za-z][^>]*>",
    re.S,
)
_ATTRIBUTE_NAME = r"[A-Za-z_:][A-Za-z0-9_.:-]*"
_ATTRIBUTE_VALUE = r"(?:[^\s\"'=<>`]+|'[^']*'|\"[^\"]*\")"
_HTML_ATTRIBUTES = rf"(?:\s+{_ATTRIBUTE_NAME}(?:\s*=\s*{_ATTRIBUTE_VALUE})?)*\s*"
_HTML_TAG = re.compile(
    r"<(?:/(?P<close>[A-Za-z][A-Za-z0-9-]*)\s*"
    r"|(?P<open>[A-Za-z][A-Za-z0-9-]*)" + _HTML_ATTRIBUTES + r"/?)>"
)
#: One attribute of an opening tag, matched *at* a position, so that :func:`_attributes` reads
#: them in order and a quoted value is consumed whole: an ``alt=`` inside another attribute's
#: value is that value's text, where a search for ``alt=`` anywhere in the tag found it first
#: and read ``note`` for ``<img title=" alt='note'" alt="Autonomy: ...">`` (Codex on #462).
_HTML_ATTRIBUTE = re.compile(
    rf"\s+(?P<name>{_ATTRIBUTE_NAME})(?:\s*=\s*(?P<value>{_ATTRIBUTE_VALUE}))?"
)
_HTML_TAG_NAME = re.compile(r"<[A-Za-z][A-Za-z0-9-]*")


def _attributes(tag: str) -> dict[str, str]:
    """The attributes of an opening tag's source, name to value, read one after another from
    the end of the name by the CommonMark attribute grammar. A quoted value loses its quotes;
    an attribute with no value is ``""``. A name written twice keeps its first value, as the
    HTML parser keeps it (checked on GitHub's markdown endpoint: ``<img alt="a" alt="b">``
    reaches the page as ``<img alt="a">``). Names are lower-cased."""
    opener = _HTML_TAG_NAME.match(tag)
    at = opener.end() if opener else 0
    found: dict[str, str] = {}
    while (attribute := _HTML_ATTRIBUTE.match(tag, at)) is not None:
        value = attribute.group("value") or ""
        if value[:1] in ("'", '"'):
            value = value[1:-1]
        found.setdefault(attribute.group("name").lower(), value)
        at = attribute.end()
    return found


#: The kept tags that wrap text and draw nothing.
_PHRASING_TAGS = frozenset(
    {"a", "abbr", "b", "bdo", "cite", "code", "dfn", "em", "i", "ins", "kbd", "mark", "picture"}
    | {"rp", "rt", "ruby", "samp", "small", "source", "span", "strong", "sub", "sup", "time"}
    | {"tt", "var", "wbr"}
)
#: The kept tag that draws a mark around its text: ``<q>`` its quotation marks.
_DRAWN_TAGS = {"q": '"'}
#: The kept tags that strike their text out, laid out as the struck runs the page draws
#: (:func:`_struck_marks`).
_STRIKE_TAGS = frozenset({"del", "s", "strike"})
#: The kept tags that draw *inside* a block without wrapping text: a line break and a picture.
_INSIDE_TAGS = frozenset({"br", "img"})
#: The kept tags the page lays out as a block of their own - the boundaries a raw run is cut at.
BLOCK_TAGS = frozenset(
    {"blockquote", "caption", "dd", "details", "div", "dl", "dt", "figcaption", "figure", "h1"}
    | {"h2", "h3", "h4", "h5", "h6", "hr", "li", "ol", "p", "pre", "summary", "table", "tbody"}
    | {"td", "tfoot", "th", "thead", "tr", "ul"}
)
#: The block tags that hold nothing: the page draws the element and whatever follows it is
#: outside it.
_VOID_BLOCK_TAGS = frozenset({"hr"})
#: The kept tags that hold nothing, block or not, so the page's tree builder never has one open
#: for an end tag to close: ``</hr>``, ``</img>``, ``</wbr>`` and ``</source>`` are ignored and
#: draw nothing - ``<hr>Auto</hr>nomy:`` is a rule and then the key whole - save ``</br>``,
#: which it reads as a ``<br>`` (GitHub's markdown endpoint, 2026-10-03; :class:`_Run`).
_VOID_TAGS = _VOID_BLOCK_TAGS | frozenset({"br", "img", "source", "wbr"})
#: The block tags the page's tree builder acts on only inside a table. With no ``<table>``
#: open, a table-part start tag is ignored and draws nothing, so the text around it joins:
#: ``<td>Auto</td>nomy:`` is the key whole, as a raw block and in running text, and so is one
#: after the table closed (GitHub's markdown endpoint, 2026-10-03; :class:`_Run`). ``col`` and
#: ``colgroup`` are not kept and draw nothing either way.
TABLE_PART_TAGS = frozenset({"caption", "tbody", "td", "tfoot", "th", "thead", "tr"})
#: Every element GitHub keeps: the html-pipeline allowlist, partitioned above by what it draws.
KEPT_TAGS = _PHRASING_TAGS | frozenset(_DRAWN_TAGS) | _STRIKE_TAGS | _INSIDE_TAGS | BLOCK_TAGS
#: The stripped tags whose text the sanitizer wraps in spaces - Selma's ``whitespace_elements``
#: less the ones GitHub keeps.
_STRIPPED_SPACED_TAGS = frozenset(
    {"address", "article", "aside", "footer", "header", "hgroup", "nav", "section"}
)
#: The tags that leave a space: every other tag, kept or stripped, leaves nothing.
_SPACED_TAGS = BLOCK_TAGS | _INSIDE_TAGS | _STRIPPED_SPACED_TAGS
#: The one character GitHub drops from a page outright: a literal U+E000 renders as nothing,
#: where U+E001 and the rest of the private-use area, every format character and an unassigned
#: code point are kept (its markdown endpoint, 2026-10-03). So the page shows ``Auto\ue000nomy``
#: as the key, and so the character is free to stand for a strike tag while raw HTML is laid
#: out - ``\ue000+`` for the opening tag, ``\ue000-`` for the closing - before
#: :func:`_struck_marks` lays the spans out as marks.
_DROPPED = "\ue000"
_STRUCK_OPEN = _DROPPED + "+"
_STRUCK_CLOSE = _DROPPED + "-"
#: A run of tildes, of which one or two with no tilde beside them is a strike mark.
_TILDE_RUN = re.compile(r"~+")


#: A comment, another hidden form or a tag, whichever opens first: what every reader of raw
#: HTML here walks (:func:`_pieces`, :func:`without_tags`).
_HIDDEN_OR_TAG = re.compile(
    rf"(?P<comment>{_HTML_COMMENT})|{_HTML_HIDDEN.pattern}|{_HTML_TAG.pattern}", re.S
)


def _pieces(text: str) -> Iterator[str | re.Match[str]]:
    """``text`` walked once as the page's parser walks it: each run of text between the
    constructs, and each tag as its match; a hidden form yields nothing.

    A comment, another hidden form or a tag, whichever opens first, as the one grammar above
    has them, so a comment opener inside a quoted attribute value is the tag's and a tag
    inside a comment is the comment's. Every reader of raw HTML walks this once and lays out
    what it yields, where removing the hidden forms first and reading the tags off what was
    left let an unclosed ``<!--`` in a ``title=""`` swallow the tag and the restriction after
    it, which the page draws (Codex on #462), and would read ``<b<!-- x -->>``, which the page
    shows as text, as the tag the removal joined. An empty strike element is still a tag here
    - a source carrying one is not plain Markdown - and draws nothing only in
    :func:`_struck_marks`.
    """
    at = 0
    for found in _HIDDEN_OR_TAG.finditer(text):
        yield text[at : found.start()]
        if found.group("open") is not None or found.group("close") is not None:
            yield found
        at = found.end()
    yield text[at:]


def without_tags(text: str) -> str:
    """``text`` with everything but its comments gone, so that what sits inside a tag or
    another hidden form is not read as a comment: ``<img alt="<!-- tether-grooming-v1 -->">``
    is a picture whose alternative text quotes the marker, not a comment, and a search of the
    run for the marker found it there and refused the body for a marker the page does not
    carry (Codex on #462); ``<?note <!-- tether-grooming-v1 --> ?>`` is a processing
    instruction to cmark and a bogus comment to the page, holding the marker's bytes and no
    comment, and keeping it whole for the search refused the same way (Codex on #462), as
    would a CDATA section or a declaration holding them. The text is walked left to right
    taking a comment, another hidden form or a tag, whichever opens first, as the page reads
    it: a comment inside a quoted attribute value goes with the tag, a tag inside a comment is
    the comment's text - ``<!-- tether-grooming-v1 <b> -->`` is no marker on the page, and
    dropping the tag out of it made one (found beside Codex on #462) - and a bogus comment
    ends at the first ``>``, so what cmark read as the rest of the form is walked as the text
    the page draws it as. A comment beside any of them stays."""
    return _HIDDEN_OR_TAG.sub(lambda m: m.group("comment") or "", text)


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
    twice; see :func:`_paragraphs`). A definition's *continuation* - the lines indented four
    spaces after it, which cmark-gfm reads as more of the footnote and CommonMark as an
    indented code block - is the blocks it holds, as the parser read them, each carrying the
    note (:func:`_continuation`; Codex on #462).

    ``note`` is the definition the block belongs to - the source line it opens on, which no
    two definitions share - or ``None`` for a block of the body; every block type carries
    one, and :func:`at_foot` reads it. Every block of one footnote carries the same ``note``,
    so a caller reading what a bare key in a footnote heads can stop at the next definition:
    `[^1]: **Autonomy**` over `[^2]: agent-can-do-alone` is an empty declaration and a value,
    not the one over the other (Codex on #462). ``footnote`` is whether ``note`` is set.
    """

    text: str
    plain: str
    line: int
    pictured: bool = False
    note: int | None = None

    @property
    def footnote(self) -> bool:
        return self.note is not None


class Heading(NamedTuple):
    """An ATX (``#``) or setext (underlined) heading.

    ``column`` is where the first ``#`` - or, for a setext heading, the text - sits on its source
    line, measured from the start of the line even inside a list item or block quote and even
    when the heading opens on the same line as the item's marker, so only a top-level, unquoted,
    unindented heading has column zero. ``markup`` is the ``#`` run or the ``=``/``-`` underline,
    so the two forms are distinguishable too. ``text`` is the heading content with any closing
    ``#`` sequence already removed, and ``plain`` is that content rendered. ``pictured`` and
    ``note`` are as on :class:`Paragraph`.
    """

    level: int
    text: str
    plain: str
    line: int
    column: int
    markup: str
    pictured: bool = False
    note: int | None = None


class ListItem(NamedTuple):
    """One item of a list, with every block the item contains.

    ``marker`` is the bullet character (``-``, ``*`` or ``+``) or the ordered delimiter (``.`` or
    ``)``); ``number`` is the ordered item's digits, or ``None`` for a bullet. ``column`` is where
    the marker - for an ordered item, its first digit - sits on its source line, measured from the
    start of the line even inside a block quote and even for an item that opens on the same line
    as its parent's marker, so only a top-level, unquoted, unindented item has column zero. A
    tight item's single paragraph is a :class:`Paragraph` like any other. ``note`` is as on
    :class:`Paragraph`, on every block type: a container of a footnote's continuation carries
    the note its blocks do.
    """

    marker: str
    number: str | None
    line: int
    column: int
    blocks: tuple[Block, ...]
    note: int | None = None


class ListBlock(NamedTuple):
    """A bullet or ordered list."""

    ordered: bool
    line: int
    items: tuple[ListItem, ...]
    note: int | None = None


class BlockQuote(NamedTuple):
    """A ``>`` block quote, with every block it contains."""

    line: int
    blocks: tuple[Block, ...]
    note: int | None = None


class Code(NamedTuple):
    """A fenced or indented code block. ``text`` is literal and ``info`` the fence's info string."""

    text: str
    line: int
    fenced: bool
    info: str
    note: int | None = None


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
    note: int | None = None


class Rule(NamedTuple):
    """A thematic break."""

    line: int
    note: int | None = None


class TableRow(NamedTuple):
    """One row of a GFM table; ``header`` marks the row above the delimiter line.

    ``cells`` are the cells' inline source and ``plain`` the same cells rendered, in order.
    """

    cells: tuple[str, ...]
    plain: tuple[str, ...]
    line: int
    header: bool
    note: int | None = None


class Table(NamedTuple):
    """A GFM table, which GitHub recognizes with or without the outer ``|`` on each row."""

    line: int
    rows: tuple[TableRow, ...]
    note: int | None = None


Block = Paragraph | Heading | ListBlock | BlockQuote | Code | Html | Rule | Table


def at_foot(block: Block | ListItem | TableRow) -> bool:
    """Whether the page draws ``block`` at its foot - a footnote definition, or a block of one's
    continuation, which carries the definition's ``note`` - and not where it sits in the
    source. A caller reading the body skips it, and a caller reading a footnote reads the
    blocks that carry its note by the rules it reads the body by: the foot used to come back
    flattened into footnote paragraphs, and every reading the body had was then missing at
    the foot one shape at a time (Codex on #462, three reads; :func:`_continuation`).
    """
    return block.note is not None


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
    return _document(tokens, lines, env)


def _document(tokens: list[Token], lines: list[str], env: dict[str, Any]) -> tuple[Block, ...]:
    """The blocks of a parsed token stream, the swallowed footnote definitions sorted in.

    The swallowed definitions are found before the blocks are read, and left in ``env`` under
    ``_SWALLOWED`` by the line each ends on, so that :func:`_blocks` can tell an indented block
    that continues one from an indented code block.
    """
    footnotes = _footnotes(env, lines)
    env[_SWALLOWED] = {_last_line(footnote): footnote for footnote in footnotes}
    blocks, end = _blocks(tokens, 0, None, lines, None, env)
    if end != len(tokens):  # pragma: no cover - the parser balances its own tokens
        raise MarkdownStructureError(f"unbalanced token stream at {end}")
    if footnotes:
        blocks = tuple(sorted(blocks + footnotes, key=lambda block: block.line))
    shown, cut = _until_hidden(blocks)
    if cut:
        return tuple(block for block in shown if block.note is None)
    return shown


def _until_hidden(blocks: tuple[Block, ...]) -> tuple[tuple[Block, ...], bool]:
    """``blocks`` up to and including the first raw block that opens a comment it never
    closes, at any depth, and whether there was one.

    The page's parser reads the rendered document whole, so a comment a raw block opens and
    never closes runs to the end of it: ``<div>``, ``<!-- x``, ``</div>`` and then an admitting
    bullet draws an empty ``<div>`` and nothing after, where reading the block on its own hid
    the comment to the block's end and read the bullet (GitHub's markdown endpoint,
    2026-10-03). Everything after that block in source order goes - the rest of its
    container, and every block after - and :func:`_document` drops the foot with them, since
    the page draws the footnotes after the body, inside the comment. Only a *body* block
    cuts: a raw block of a footnote's continuation sits at its source line here and at the
    page's foot there, after every body block, so a comment it opens hides nothing of the
    body - an admitting bullet, the definition, then a restriction the page draws, and the
    cut in source order dropped the restriction (Codex on #462). What such a comment hides
    of the foot - the rest of its footnote and the footnotes the page draws after it, in the
    order the body refers to them - is read as drawn, which only ever refuses, since nothing
    at the foot admits. A comment inside a tag's attribute value is the tag's, as
    :func:`without_tags` walks it. Inline HTML is raw only where the parser found the close,
    so only a block can open one.
    """
    kept: list[Block] = []
    for block in blocks:
        if isinstance(block, ListBlock):
            items: list[ListItem] = []
            cut = False
            for item in block.items:
                inner, cut = _until_hidden(item.blocks)
                items.append(item._replace(blocks=inner))
                if cut:
                    break
            kept.append(block._replace(items=tuple(items)))
        elif isinstance(block, BlockQuote):
            inner, cut = _until_hidden(block.blocks)
            kept.append(block._replace(blocks=inner))
        else:
            kept.append(block)
            cut = (
                isinstance(block, Html)
                and block.note is None
                and _opens_unclosed_comment(block.text)
            )
        if cut:
            return tuple(kept), True
    return tuple(kept), False


def _opens_unclosed_comment(text: str) -> bool:
    """Whether ``text`` opens a comment it never closes, walked as the page reads it."""
    for found in _HIDDEN_OR_TAG.finditer(text):
        comment = found.group("comment")
        if comment is not None and not comment.endswith(("-->", "--!>")):
            return True
    return False


#: The ``env`` key under which :func:`_document` leaves the swallowed footnote paragraphs, by
#: the source line each ends on.
_SWALLOWED = "tether_swallowed_footnotes"


def _last_line(paragraph: Paragraph) -> int:
    """The source line a footnote paragraph ends on: its ``text`` keeps one line per source
    line."""
    return paragraph.line + paragraph.text.count("\n")


def _continued_note(
    found: list[Block],
    lines: list[str],
    line: int,
    env: dict[str, Any],
    origin: tuple[int, int] | None,
    quoted: bool,
) -> int | None:
    """The definition an indented code block opening at ``line`` continues - its ``note`` - or
    ``None`` for a code block: the nearest line above it that is not blank ends a footnote
    paragraph, the last block read or a swallowed definition, **in the container the code
    block is in**.

    cmark-gfm continues a definition into the lines indented four spaces after it inside the
    list item both are in, and never into a container the definition is not in: `[^1]: /url`
    over `-     Autonomy: human review required` is the footnote and then an item holding a
    code block, which the page shows literal, where a lookup by adjacent source lines alone
    took the code for the footnote's continuation, read it as Markdown, and refused an issue
    for a declaration the page shows as code (Codex on #462). ``origin`` is the enclosing
    item's content position, so a swallowed definition continues only from its own item - a
    line at or after the item's; the last block read is in this container already. And a
    definition inside a block quote absorbs nothing, four spaces or six, sentence or bare
    destination, the code inside the quote or after it closes (GitHub's markdown endpoint,
    2026-10-03): ``quoted`` ends it for the code inside, and a swallowed definition whose
    line carries a quote marker - the parser files a quoted ``[^1]: /url`` under the same
    references, and the map it leaves records no container - ends it for the code after.
    """
    if quoted:
        return None
    above = line - 1
    while above >= 0 and not lines[above].strip():
        above -= 1
    if above < 0:
        return None
    swallowed = env.get(_SWALLOWED, {}).get(above)
    if swallowed is not None:
        if _in_quote(lines, swallowed.line) or swallowed.line < (origin[0] if origin else 0):
            return None
        return swallowed.note
    last = found[-1] if found else None
    if isinstance(last, Paragraph) and last.footnote and _last_line(last) == above:
        return last.note
    return None


def _in_quote(lines: list[str], line: int) -> bool:
    """Whether the footnote definition opening on ``line`` sits inside a block quote: a quote
    marker before its label, where only container markers and indentation can be."""
    text = lines[line]
    label = text.find("[^")
    return label >= 0 and ">" in text[:label]


def _continuation(content: str, line: int, note: int, env: dict[str, Any]) -> list[Block]:
    """The blocks a definition's continuation draws, read from ``content``, the indented code
    block CommonMark made of its lines, with the indent gone - each carrying ``note``, at its
    source line.

    cmark-gfm reads the lines indented four spaces after a footnote definition as the rest of
    the definition, blank lines between included, and draws them inside the footnote at the
    page's foot: `[^1]: first line` over a blank line over `    Autonomy: maintainer decision
    required` is a footnote of two paragraphs, the second the restriction, while CommonMark
    reads the indented line as a code block and a caller reading only the first paragraph
    never saw it (Codex on #462). The lines are read again as the Markdown they are, and the
    blocks come back as the parser read them - a heading as a heading, a list as a list of its
    items, a table as a table, raw HTML as the raw block it is - with ``note`` set on every
    block, containers included, and ``line`` moved to the source. The foot used to come back
    flattened, as one footnote paragraph per block the foot draws text for, and every reading
    the body has was then missing at the foot one shape at a time: a raw block read a piece
    at a time, a block drawn with no text keeping its place, an item drawing nothing standing
    in for itself, then a heading's qualifier, a raw heading, a table's cells and an item's
    boundary (Codex on #462, three reads). GitHub draws the continuation's headings, lists,
    tables and raw HTML inside the footnote (its markdown endpoint, 2026-10-03), and a caller
    reads them by the body's rules over the blocks that carry the note (:func:`at_foot`),
    nothing of which admits. A definition nested in the continuation keeps a note of its own,
    moved to the source as well.

    ``env`` is the document's: GitHub resolves a reference link in the continuation against
    the definitions anywhere in the body, so ``[Autonomy: human review required][ref]`` under
    a definition draws the key, where reading the lines in an environment of their own left
    the brackets literal and the restriction unread (Codex on #462). The references are
    handed down without their source maps, which index the body's lines and would otherwise
    be read again here as definitions of the continuation's.
    """
    text = content if content.endswith("\n") else content + "\n"
    lines = text.split("\n")
    inherited = {
        label: {key: value for key, value in reference.items() if key != "map"}
        for label, reference in (env.get("references") or {}).items()
    }
    own: dict[str, Any] = {"references": inherited}
    tokens = _PARSER.parse(text, own)
    return [_placed(block, line, note) for block in _document(tokens, lines, own)]


def _placed(block: Any, line: int, note: int) -> Any:
    """``block`` and everything in it moved ``line`` lines down and marked as footnote
    ``note`` - or, for a block carrying a note already, a definition nested in the
    continuation or a block of that definition's own continuation, its own note moved down
    the same."""
    own = note if block.note is None else line + block.note
    moved = block._replace(line=line + block.line, note=own)
    if isinstance(block, ListBlock):
        return moved._replace(items=tuple(_placed(item, line, note) for item in block.items))
    if isinstance(block, (ListItem, BlockQuote)):
        return moved._replace(blocks=tuple(_placed(inner, line, note) for inner in block.blocks))
    if isinstance(block, Table):
        return moved._replace(rows=tuple(_placed(row, line, note) for row in block.rows))
    return moved


#: A GitHub footnote definition's label at the start of a source line: cmark-gfm's grammar,
#: ``[^``, anything but ``]`` and whitespace, ``]:``, then any blank. Up to three spaces may
#: precede it, as before any block opener.
_FOOTNOTE_LABEL = re.compile(r" {0,3}\[\^[^\]\s]+\]:[ \t]*")


def _footnote(text: str, label: re.Match[str], line: int, env: dict[str, Any]) -> Paragraph:
    """The footnote paragraph whose source is ``text``, ``label`` its ``_FOOTNOTE_LABEL`` match."""
    rest = text[label.end() :]
    if not rest.strip():
        return Paragraph(text, "", line, note=line)
    inline = _PARSER.parseInline(rest, env)[0]
    return Paragraph(text, _plain(inline), line, _pictured(inline), note=line)


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
    is its alternative text - the label *rendered*, as cmark-gfm renders it, so
    ``![**Autonomy:** human review required](x)`` shows the key and not ``**Autonomy:**``,
    which no key matched (Codex on #462; :func:`_alternative`) - a soft or
    hard break is a space, and the parser has already decoded entities and backslash escapes
    into the ``text`` children. Inline HTML follows the rule the
    module-level patterns state, and the children are rendered *together* - the text escaped,
    the inline HTML raw, the whole laid out as :func:`_shown_html` lays out a raw run - so a
    tag's effect on what sits between it and its close is read across the children: an empty
    ``<del></del>`` draws nothing, where rendering each tag on its own drew ``~~~~`` (Codex on
    #462). The escaping is undone with the character references at the end, so ``n < 5`` and
    a code span's ``<b>`` are the text they were. Strike-through is a GFM extension the parser
    does not enable, so ``~~x~~`` keeps its tildes: GitHub shows that text struck out, and the
    tildes keep the retraction in view - as the marks :func:`_struck_marks` lays out for a
    strike tag's text do, the tag's span and the Markdown pair joined where the page joins
    them. They cut one way. A struck-out value is not the
    registered one, so ``~~agent-can-do-alone~~`` never admits; a struck-out restriction still
    names its token to a caller scanning for one, since the words are on the page and the scan
    reads past the mark - a retraction the gate cannot read as one refuses, which is the safe
    error (Codex on #462, which read the first half as a rule for both). An inline the parser
    gave no children is its content as it stands, which only happens for an inline it did not
    need to tokenize.
    """
    if inline.children is None:
        return " ".join(inline.content.split())
    parts: list[str] = []
    for child in inline.children:
        if child.type in ("text", "code_inline"):
            parts.append(html.escape(child.content, quote=False))
        elif child.type == "image":
            parts.append(html.escape(_alternative(child), quote=False))
        elif child.type in ("softbreak", "hardbreak"):
            parts.append(" ")
        elif child.type == "html_inline":
            parts.append(child.content)
        # Every other child is the open or close of a span - emphasis, a link - and has no text.
    marked = _struck_marks(_laid_out("".join(parts)), markdown=True)
    return " ".join(html.unescape(marked).split())


def _alternative(image: Token) -> str:
    """An image token's alternative text: its label rendered as text, as cmark-gfm renders it
    into the ``alt`` - text and code as their content, an image inside the label as its own
    alternative text, a break as a space, markup and inline HTML as nothing. A character
    reference or a backslash escape inside a label is still a ``text_special`` child here, the
    parser's joining pass not reaching into a label, and is its character; the parser's own
    renderer drops those, the code and the nested image (``![b &amp; `c`](f)`` renders
    ``alt="b   "`` in markdown-it-py 4.2.0), so it is not the oracle for this."""
    parts: list[str] = []
    for child in image.children or ():
        if child.type in ("text", "text_special", "code_inline"):
            parts.append(child.content)
        elif child.type == "image":
            parts.append(_alternative(child))
        elif child.type in ("softbreak", "hardbreak"):
            parts.append(" ")
    return "".join(parts)


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

    Read by the grammar above, a hidden form or a tag whichever opens first, so a tag written
    inside a comment is not a tag: ``<!-- </details> -->`` closes nothing on the page and must
    close nothing for a caller counting nesting (Codex on #462). A tag inside a stripped element is
    one: the page drops the ``<svg>`` of ``<svg></details></svg>`` and keeps what it held, so
    that ``</details>`` closes the widget there, and a rule that dropped it with the element -
    read off the sanitizer's source rather than the page - kept open a widget the page had
    closed (read 29 of #462, reversed at read 31). Names are lower-cased.
    """
    for piece in _pieces(text):
        if isinstance(piece, str):
            continue
        closing = piece.group("close") is not None
        yield (piece.group("close") or piece.group("open")).lower(), closing


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
    The chunks are read as one run, as :func:`_plain` renders them.
    """
    yield from tags("".join(inline_html(text)))


def draws(block: Block) -> bool:
    """Whether the page draws anything for ``block``.

    Prose is drawn, and so is what shows no prose: a picture with no alternative text, a raw
    HTML block whose tags draw a widget or a picture, a code block, a rule, a table, and a
    container - a list, a block quote - whatever it holds, since the page draws the bullet and
    the bar. A heading is drawn whatever it holds too: `## <!-- note -->` is an empty `<h2>`
    on the page, a block with its own height - and, at the first two levels, its own rule
    beneath - so an item that opens with one does not open with the paragraph after it, which
    read as the item's lead let `- ## <!-- note -->` over `Autonomy: agent-can-do-alone` admit
    as the registered bullet (Codex on #462). And so is a paragraph, whatever it holds: the
    page lays out a `<p>` for one however little it shows - `&nbsp;` is a line of its own
    height, `[](x)` or `&#32;` a block with its margin (GitHub's markdown endpoint) - and a
    paragraph was drawn only when its text, a picture or a tag was, so `- &nbsp;` over
    `Autonomy: agent-can-do-alone` opened with the paragraph after it, which admitted as the
    registered bullet (found beside Codex's read of `35710e3` on #462). Only a raw HTML block
    the page shows nothing for - a comment on its own lines - is not. A tag counts as drawn
    whatever the rendering made of it, for the reason :func:`has_tag` gives: the
    approximation may not err in the admitting direction. claim.py's ``_drawn`` is this at
    the leaf level, where a table row and a list's item are leaves too. *Where* the page
    draws the block - in the body, or at its foot - is :func:`at_foot`'s to say; this is
    whether it draws it at all.
    """
    if isinstance(block, Html):
        return bool(block.plain) or has_tag(block.text)
    return True


def has_tag(text: str) -> bool:
    """Whether ``text`` - a block's source ``text`` - carries an HTML tag, by the grammar above.

    A comment is not a tag: it draws nothing and a caller has its own reasons to look for one. A
    caller that admits only plain Markdown asks this of a declaration's source, because every
    rendering rule above is an approximation of a browser and the one direction that approximation
    must never err in is the admitting one (Codex on #462, repeatedly).
    """
    return next(tags(text), None) is not None


def _laid_out(text: str) -> str:
    """Raw HTML as GitHub lays it out, with the strike tags standing as spans: the character
    the page drops gone, the hidden forms and the empty strike elements gone, a tag that draws
    something what it draws, a tag in ``_SPACED_TAGS`` a space, a strike tag its sentinel, every
    other tag - phrasing or stripped - nothing. Character references are still encoded, so a
    reference to a tilde is not yet a tilde."""
    run = _Run()
    return "".join(
        piece if isinstance(piece, str) else run.draw(piece)
        for piece in _pieces(text.replace(_DROPPED, ""))
    )


class _Run:
    """The tags of one raw run laid out in order, with the elements open so far counted by
    name, because the page's tree builder ignores an end tag with no element to close and
    draws nothing for it: ``Auto</q>nomy`` shows the key whole, where drawing a quotation
    mark for the end tag alone read ``Auto"nomy`` and missed the restriction after it (Codex
    on #462), and ``Auto</section>nomy``, ``Auto</div>nomy``, ``Auto</li>nomy`` and
    ``Auto</details>nomy`` show it whole too, where the end tag alone drew a space (GitHub's
    markdown endpoint, 2026-10-03). Two end tags the builder never ignores: ``</p>`` with no
    paragraph open inserts an empty one, a boundary, and ``</br>`` is a ``<br>``. A stray
    end tag of a strike tag closed nothing already (:func:`_struck_marks`). It ignores a
    start tag too, one of ``TABLE_PART_TAGS`` with no table open - ``<td>Auto</td>nomy:``
    is the key whole, where cutting at the cell split it and the restriction after it went
    unread beside an admitting bullet - and a void element, ``_VOID_TAGS``, is never open
    for its end tag to close, so ``<hr>Auto</hr>nomy:`` is the key whole after the rule
    (GitHub's markdown endpoint, 2026-10-03). An element is counted open for the run it
    opens in, so an end tag closing one opened in an earlier raw block is ignored here where
    the page acts on it, as is a cell of a table opened there - a boundary not drawn, which
    only joins text the page separates, and so only ever refuses.
    """

    def __init__(self) -> None:
        self._open: dict[str, int] = {}

    def live(self, tag: re.Match[str]) -> bool:
        """Whether the tree builder acts on ``tag``: an opener, unless a table part with no
        table open in this run, or an end tag that closes an element open in this run - or
        ``</p>`` or ``</br>``, which it never ignores."""
        name = (tag.group("open") or tag.group("close")).lower()
        if tag.group("close") is None:
            if name in TABLE_PART_TAGS and not self._open.get("table", 0):
                return False
            if name not in _VOID_TAGS:
                self._open[name] = self._open.get(name, 0) + 1
            return True
        if name in _UNIGNORED_END_TAGS:
            return True
        if self._open.get(name, 0):
            self._open[name] -= 1
            return True
        return False

    def draw(self, tag: re.Match[str]) -> str:
        """What the page lays out for ``tag``: see :func:`_laid_out`."""
        if not self.live(tag):
            return ""
        name = (tag.group("open") or tag.group("close")).lower()
        if name in _STRIKE_TAGS:
            return _STRUCK_CLOSE if tag.group("close") is not None else _STRUCK_OPEN
        if name in _DRAWN_TAGS:
            return _DRAWN_TAGS[name]
        if name == "img" and tag.group("open") is not None:
            shown = _attributes(tag.group(0)).get("alt", "")
            return f" {shown} " if shown else " "
        return " " if name in _SPACED_TAGS else ""


#: The end tags the page's tree builder acts on with nothing open to close (:class:`_Run`).
_UNIGNORED_END_TAGS = frozenset({"p", "br"})


def strike_pairs(text: str) -> tuple[tuple[int, int, int], ...]:
    """The balanced pairs of strike marks in ``text``, each ``(opener, closer, length)`` - the
    offsets of its two runs and their length - in opener order.

    A run of one or two tildes with no tilde beside it is a mark, as GitHub reads one: it
    strikes between one tilde as between two, and ``~~x~`` is literal (its markdown endpoint,
    2026-10-03). The marks of one length pair in turn, the first with the second, the third
    with the fourth, whatever sits between them: ``~~a~~~~b~~`` is one pair holding a literal
    ``~~~~``, as the page strikes it, and ``~~a ~b~ c~~`` is a pair holding a pair. A mark
    with no partner is literal and stays. This is the pairing a leftmost-first reading gives -
    take the first mark that has a partner, pair it with the nearest, repeat - checked against
    that reading on two hundred thousand random strings, and it is one pass where the reading
    rescanned the text once per pair (Codex on #462).
    """
    starts: dict[int, list[int]] = {1: [], 2: []}
    for run in _TILDE_RUN.finditer(text):
        length = run.end() - run.start()
        if length <= 2:
            starts[length].append(run.start())
    pairs = [
        (opens[at], opens[at + 1], length)
        for length, opens in starts.items()
        for at in range(0, len(opens) - 1, 2)
    ]
    return tuple(sorted(pairs))


def _struck_marks(laid: str, markdown: bool) -> str:
    """``laid`` with its struck text between ``~~`` marks, one pair of marks per struck run.

    The page strikes what a strike tag holds and - in a paragraph, a heading or a cell, where
    ``markdown`` is read, and not in a raw block, whose tildes are literal - what a Markdown
    pair holds (:func:`strike_pairs`); and it draws two struck spans that abut, nest or
    overlap as one struck run, with no break the eye can see. So the marks are laid out for
    the *runs*: a character is struck while a tag is open over it or a pair holds it, and a
    mark goes wherever that changes. ``<del>Auto</del><del>nomy</del>``,
    ``<del>Auto</del>~~nomy~~``, ``<del><del>Autonomy</del></del>`` and
    ``~~<del>Auto</del>nomy~~`` are each the struck key ``~~Autonomy~~`` the page shows (its
    markdown endpoint, 2026-10-03), where drawing each tag's own marks laid
    ``~~Auto~~~~nomy~~`` - a four-tilde run no reader of pairs can read, and what the page
    shows for the *literal* ``~~Auto~~~~nomy~~``, which is not the key - so a restriction
    written any of those ways went unread beside an admitting bullet (found beside Codex's
    read of ``6c060e7`` on #462). A literal tilde stays where it is: a mark with no partner,
    or a run of three, is text the page shows. A closing tag with no open one is ignored, as
    a browser ignores it. A run that strikes nothing draws nothing: an empty ``<del>``,
    ``<s>`` or ``<strike>`` leaves ``Auto<del></del>nomy`` the key the page shows whole,
    where drawing the marks for both tags read ``Auto~~~~nomy``, and one holding whitespace
    alone is struck whitespace, which the page shows as the whitespace - so neither takes
    marks (Codex on #462). That falls out of the one pass here, where a grammar that removed
    the empty elements innermost first rescanned the text once per element, and nine
    thousand nested in a body near GitHub's limit took seconds (Codex on #462). An empty
    ``<q>`` still draws its two quotation marks.
    """
    pairs = strike_pairs(laid) if markdown else ()
    marks: set[int] = set()
    held = [0] * (len(laid) + 1)
    for opener, closer, length in pairs:
        marks.update(range(opener, opener + length))
        marks.update(range(closer, closer + length))
        held[opener + length] += 1
        held[closer] -= 1
    pieces: list[str] = []
    run: list[str] = []
    depth = inside = 0
    at = 0
    while at < len(laid):
        inside += held[at]
        if laid.startswith(_DROPPED, at):
            depth = depth + 1 if laid[at + 1] == "+" else max(depth - 1, 0)
            at += 2
            continue
        if at not in marks:
            if depth > 0 or inside > 0:
                run.append(laid[at])
            else:
                if run:
                    pieces.append(_marked(run))
                    run = []
                pieces.append(laid[at])
        at += 1
    if run:
        pieces.append(_marked(run))
    return "".join(pieces)


def _marked(run: list[str]) -> str:
    """A struck run between its marks, or the whitespace it is."""
    struck = "".join(run)
    return struck if not struck.strip() else f"~~{struck}~~"


def _shown_html(text: str) -> tuple[Shown, ...]:
    """Raw HTML as GitHub lays it out, one :class:`Shown` per block it draws.

    The run is cut at every tag in ``BLOCK_TAGS`` - the kept tags the page lays out as a block
    of their own - each piece laid out as it is walked, as GitHub shows raw HTML: a tag that
    draws something what it draws, a tag in ``_SPACED_TAGS`` a space, every other tag - hidden,
    phrasing or stripped - nothing, the text a strike tag holds between ``~~`` marks, and
    character references decoded last; a tilde in a raw block is literal, the page reading no
    Markdown there (its markdown endpoint, 2026-10-03), so only the tags strike anything here -
    and named for the opening tag it follows. Cutting at a tag the page draws inside a block would
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
    opened and that renders to nothing is nothing. A void block - ``<hr>`` - holds nothing,
    so it is a piece of its own at once and what follows it is text no tag opened:
    ``<hr>agent-can-do-alone`` is the rule and then the text, where naming the text's piece
    ``hr`` let a caller take the text as the first block drawn after a raw heading, with the
    rule the page draws between them gone (Codex on #462). A block end tag that closes
    nothing open in the run is no boundary: ``<p>Auto</div>nomy:</p>`` is the one paragraph
    the page shows, the stray ``</div>`` dropped by its tree builder (:class:`_Run`), and
    nor is a table part with no table open in the run: ``<td>Auto</td>nomy:`` is the one
    line the page shows, both tags dropped, where inside a table it is the cell drawn.
    """
    pieces: list[tuple[list[str], str]] = []
    laid: list[str] = []
    opener = ""
    run = _Run()
    for piece in _pieces(text.replace(_DROPPED, "")):
        if isinstance(piece, str):
            laid.append(piece)
            continue
        name = (piece.group("open") or piece.group("close")).lower()
        if name not in BLOCK_TAGS:
            laid.append(run.draw(piece))
            continue
        if not run.live(piece):
            continue  # ignored by the page's tree builder: no boundary (:class:`_Run`)
        pieces.append((laid, opener))
        laid = []
        opener = "" if piece.group("close") is not None else name
        if opener in _VOID_BLOCK_TAGS:
            pieces.append(([], opener))
            opener = ""
    pieces.append((laid, opener))
    shown = (
        (" ".join(html.unescape(_struck_marks("".join(laid), markdown=False)).split()), opener)
        for laid, opener in pieces
    )
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
    quoted: bool = False,
) -> tuple[tuple[Block, ...], int]:
    """Read blocks from ``tokens[at:]`` up to the ``until`` closing token, which is consumed.

    Returns the blocks and the index just past what was read. ``until`` is ``None`` at the top
    level, where reading stops at the end of the stream. ``origin`` is the enclosing list item's
    content position, passed to :func:`_column`. ``env`` is the parser's environment, which
    holds the reference definitions a footnote's text may link through. ``quoted`` is whether
    a block quote encloses this, however deep, which :func:`_continued_note` needs.
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
                tokens, at + 1, token.type.replace("open", "close"), lines, origin, env, quoted
            )
            found.append(ListBlock(token.type == "ordered_list_open", _line(token), items))
        elif token.type == "blockquote_open":
            inner, at = _blocks(tokens, at + 1, "blockquote_close", lines, origin, env, True)
            found.append(BlockQuote(_line(token), inner))
        elif token.type == "fence":
            found.append(Code(token.content, _line(token), True, token.info.strip()))
            at += 1
        elif token.type == "code_block":
            line = _line(token)
            note = _continued_note(found, lines, line, env, origin, quoted)
            if note is not None:
                found.extend(_continuation(token.content, line, note, env))
            else:
                found.append(Code(token.content, line, False, ""))
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
    quoted: bool,
) -> tuple[tuple[ListItem, ...], int]:
    items: list[ListItem] = []
    while tokens[at].type != until:
        token = tokens[at]
        if token.type != "list_item_open":
            raise MarkdownStructureError(f"unexpected {token.type!r} inside a list")
        line = _line(token)
        column = _column(lines, line, origin)
        content = (line, _content_column(lines[line], column))
        inner, at = _blocks(tokens, at + 1, "list_item_close", lines, content, env, quoted)
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

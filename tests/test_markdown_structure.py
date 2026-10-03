# SPDX-FileCopyrightText: 2026 The Tether Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Behavioural tests for the Markdown block-structure reader behind the autonomy gate (#469).

Each case below is a shape that the hand-written structure regexes in ``claim.py`` misread under
review on #462, or a shape the gate relies on. The property under test is always the same: the
tree this module returns is the one GitHub renders, so a restriction written in any of these
shapes is where a reader of the rendered issue would see it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / ".agents" / "bin" / "markdown_structure.py"

_spec = importlib.util.spec_from_file_location("tether_markdown_structure", MODULE)
assert _spec is not None and _spec.loader is not None
md = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(md)


def kinds(blocks) -> list[str]:
    return [type(block).__name__ for block in blocks]


def test_a_heading_and_its_first_paragraph_are_separate_blocks_with_their_source_lines():
    doc = md.parse("## Execution autonomy ##\n\nagent-can-do-alone\nunless told otherwise\n")
    assert doc == (
        md.Heading(
            level=2,
            text="Execution autonomy",
            plain="Execution autonomy",
            line=0,
            column=0,
            markup="##",
        ),
        md.Paragraph(
            text="agent-can-do-alone\nunless told otherwise",
            plain="agent-can-do-alone unless told otherwise",
            line=2,
        ),
    )


def test_closing_hashes_are_markup_and_not_heading_text():
    (heading,) = md.parse("# Autonomy ###")
    assert heading.text == "Autonomy"


def test_a_heading_indented_as_markdown_allows_keeps_its_column():
    (heading,) = md.parse("   ### Autonomy")
    assert heading == md.Heading(
        level=3, text="Autonomy", plain="Autonomy", line=0, column=3, markup="###"
    )


def test_four_spaces_before_a_hash_is_code_not_a_heading():
    (code,) = md.parse("    ## Autonomy\n")
    assert code == md.Code(text="## Autonomy\n", line=0, fenced=False, info="")


def test_a_setext_heading_is_a_heading_with_its_underline_as_markup():
    doc = md.parse("Execution autonomy\n---\n\nagent-can-do-alone\n")
    assert doc[0] == md.Heading(
        level=2, text="Execution autonomy", plain="Execution autonomy", line=0, column=0, markup="-"
    )
    assert doc[1] == md.Paragraph(text="agent-can-do-alone", plain="agent-can-do-alone", line=3)


def test_a_tab_before_a_hash_inside_a_list_item_is_a_heading_inside_that_item():
    # Codex on #462: a tab is four columns, so this heading belongs to the item, not to the
    # section around it - and the regex reader ended the section there instead.
    (lst,) = md.parse("- Autonomy: agent-can-do-alone\n\t## Notes\n")
    (item,) = lst.items
    assert kinds(item.blocks) == ["Paragraph", "Heading"]
    assert item.blocks[1] == md.Heading(
        level=2, text="Notes", plain="Notes", line=1, column=4, markup="##"
    )


def test_a_list_item_carries_its_marker_number_and_column():
    doc = md.parse("- a\n* b\n+ c\n 2) d\n01. e\n")
    items = [item for block in doc for item in block.items]
    assert [(i.marker, i.number, i.line, i.column) for i in items] == [
        ("-", None, 0, 0),
        ("*", None, 1, 0),
        ("+", None, 2, 0),
        (")", "2", 3, 1),
        (".", "01", 4, 0),
    ]
    assert [block.ordered for block in doc] == [False, False, False, True, True]


def test_a_wrapped_item_is_one_paragraph_and_the_next_item_is_not_part_of_it():
    (lst,) = md.parse(
        "- **Autonomy:** agent-can-do-alone\n  unless the note says otherwise\n- Next\n"
    )
    first, second = lst.items
    assert first.blocks == (
        md.Paragraph(
            text="**Autonomy:** agent-can-do-alone\nunless the note says otherwise",
            plain="Autonomy: agent-can-do-alone unless the note says otherwise",
            line=0,
        ),
    )
    assert second.blocks == (md.Paragraph(text="Next", plain="Next", line=2),)


def test_a_lazy_continuation_inside_a_nested_item_stays_in_that_item():
    # Codex on #462: `lazy` is indented less than the nested item's content column, yet Markdown
    # continues the nested paragraph with it. The regex reader dropped the line.
    (lst,) = md.parse("- outer\n  - nested needs-maintainer-input\n   lazy\n")
    (outer,) = lst.items
    (nested_list,) = [b for b in outer.blocks if isinstance(b, md.ListBlock)]
    (nested,) = nested_list.items
    assert nested.column == 2
    assert nested.blocks == (
        md.Paragraph(
            text="nested needs-maintainer-input\nlazy",
            plain="nested needs-maintainer-input lazy",
            line=1,
        ),
    )


def test_only_a_list_starting_at_one_interrupts_a_paragraph():
    # `2.` cannot interrupt a paragraph, so under a heading's value it is continuation text, and
    # `1.` can, so it ends the value. Both are CommonMark's rules, not this module's.
    assert md.parse("agent-can-do-alone\n2. unless the maintainer decides\n") == (
        md.Paragraph(
            text="agent-can-do-alone\n2. unless the maintainer decides",
            plain="agent-can-do-alone 2. unless the maintainer decides",
            line=0,
        ),
    )
    assert kinds(md.parse("agent-can-do-alone\n1. unless the maintainer decides\n")) == [
        "Paragraph",
        "ListBlock",
    ]


def test_a_numbered_line_after_a_bullet_item_starts_a_new_list_rather_than_continuing_it():
    # The paragraph-interruption rule protects a paragraph's own lines; a line at column zero
    # after a list item is outside the item, where any list may begin - and GitHub renders it so.
    doc = md.parse("- agent-can-do-alone\n2. unless the maintainer decides\n")
    assert kinds(doc) == ["ListBlock", "ListBlock"]
    assert doc[0].items[0].blocks == (
        md.Paragraph(text="agent-can-do-alone", plain="agent-can-do-alone", line=0),
    )
    assert doc[1].ordered and doc[1].items[0].number == "2"


def test_the_rest_of_an_item_is_split_into_the_blocks_markdown_renders():
    (lst,) = md.parse(
        "- Autonomy: agent-can-do-alone\n\n  Prose about a human.\n\n  Action items.\n"
    )
    (item,) = lst.items
    assert item.blocks == (
        md.Paragraph(
            text="Autonomy: agent-can-do-alone", plain="Autonomy: agent-can-do-alone", line=0
        ),
        md.Paragraph(text="Prose about a human.", plain="Prose about a human.", line=2),
        md.Paragraph(text="Action items.", plain="Action items.", line=4),
    )


def test_fenced_code_is_literal_whatever_its_text_resembles():
    doc = md.parse(
        "```md\n- Autonomy: human-executed\n| Autonomy | x |\n<!-- tether-grooming-v1 -->\n```\n"
    )
    assert doc == (
        md.Code(
            text="- Autonomy: human-executed\n| Autonomy | x |\n<!-- tether-grooming-v1 -->\n",
            line=0,
            fenced=True,
            info="md",
        ),
    )


def test_a_fence_closes_only_on_its_own_character_at_least_as_long():
    # Codex on #462: the regex closer accepted `~~~` for a backtick fence, and a shorter run.
    doc = md.parse("````\n~~~~\n```\n- Autonomy: human action\n````\nafter\n")
    assert kinds(doc) == ["Code", "Paragraph"]
    assert doc[0].text == "~~~~\n```\n- Autonomy: human action\n"


def test_an_unclosed_fence_runs_to_the_end_of_the_document():
    (code,) = md.parse("```\n- Autonomy: human action\n")
    assert code.fenced and code.text == "- Autonomy: human action\n"


def test_indented_code_is_not_a_table_row_or_a_bullet():
    # Codex on #462: `    Autonomy | maintainer decision` opens indented code, and the regex
    # table-row reader scanned it anyway.
    doc = md.parse("para\n\n    Autonomy | maintainer decision\n    - bullet\n")
    assert kinds(doc) == ["Paragraph", "Code"]
    assert doc[1] == md.Code(
        text="Autonomy | maintainer decision\n- bullet\n", line=2, fenced=False, info=""
    )


def test_a_table_is_read_with_or_without_outer_pipes_and_marks_its_header_row():
    piped = md.parse("| Field | Value |\n|---|---|\n| Autonomy | maintainer decision |\n")
    bare = md.parse("Field | Value\n--|--\nAutonomy | maintainer decision\n")
    assert (
        piped
        == bare
        == (
            md.Table(
                line=0,
                rows=(
                    md.TableRow(
                        cells=("Field", "Value"), plain=("Field", "Value"), line=0, header=True
                    ),
                    md.TableRow(
                        cells=("Autonomy", "maintainer decision"),
                        plain=("Autonomy", "maintainer decision"),
                        line=2,
                        header=False,
                    ),
                ),
            ),
        )
    )


def test_a_lone_piped_line_without_a_delimiter_row_is_a_paragraph():
    (para,) = md.parse("Autonomy | maintainer decision\n")
    assert para == md.Paragraph(
        text="Autonomy | maintainer decision", plain="Autonomy | maintainer decision", line=0
    )


def test_an_html_comment_on_its_own_line_is_an_html_block():
    doc = md.parse(
        "<!-- tether-grooming-v1 -->\n- Autonomy: agent-can-do-alone\n<!-- aside\nspans -->\n"
    )
    assert kinds(doc) == ["Html", "ListBlock", "Html"]
    assert doc[0] == md.Html(text="<!-- tether-grooming-v1 -->", plain="", line=0, shown=())
    assert doc[2] == md.Html(text="<!-- aside\nspans -->", plain="", line=2, shown=())


def test_an_html_comment_inside_a_paragraph_stays_in_that_paragraphs_text():
    (para,) = md.parse("text <!-- tether-grooming-v1 --> more\n")
    assert para.text == "text <!-- tether-grooming-v1 --> more"
    # ... and is absent from what the page shows, so a caller reading the rendering never sees it.
    assert para.plain == "text more"


def test_plain_is_a_links_text_and_not_its_syntax():
    # Codex on #462: `## [Execution autonomy](url)` renders as the heading *Execution autonomy*,
    # and a key matched against the source saw a link and never the heading.
    (heading,) = md.parse("## [Execution autonomy](https://example.test)\n")
    assert heading.text == "[Execution autonomy](https://example.test)"
    assert heading.plain == "Execution autonomy"
    (para,) = md.parse("[maintainer decision](https://example.test 'title') required\n")
    assert para.plain == "maintainer decision required"


def test_plain_drops_markup_decodes_the_source_and_keeps_what_the_page_shows():
    (para,) = md.parse(
        "**Auto<!-- note -->nomy:** &amp; \\*x\\* `co de` ![alt](i.png) <b>b</b> a\nb  \nc\n"
    )
    # Emphasis and code markup are gone and their content stays; the comment splitting the key
    # vanishes so the key is one word; an entity and a backslash escape are decoded; an image is
    # its alternative text; a tag is a space, as are both kinds of break.
    assert para.plain == "Autonomy: & *x* co de alt b a b c"
    # Strike-through is not enabled, so a struck-out token keeps its tildes and a caller matching
    # the token against it fails to: GitHub shows the text crossed out, which is a retraction.
    (para,) = md.parse("~~agent-can-do-alone~~\n")
    assert para.plain == "~~agent-can-do-alone~~"


def test_pictured_is_whether_the_inline_carries_an_image_by_the_parsers_reading():
    # Codex on #462: an image renders to its alternative text, which the page shows only when
    # the picture fails to load, so a caller admitting only what the page shows as text is told.
    (para,) = md.parse("![agent-can-do-alone](v.png)\n")
    assert para.pictured and para.plain == "agent-can-do-alone"
    (heading,) = md.parse("## ![Execution autonomy](k.png)\n")
    assert heading.pictured and heading.plain == "Execution autonomy"
    # Read off the children, not the source: an escaped `!`, a code span and a reference-style
    # image whose definition sits elsewhere are each answered as the page answers them.
    (para,) = md.parse("\\![x](y) `![x](y)`\n")
    assert not para.pictured and para.plain == "!x ![x](y)"
    (para,) = md.parse("![x][r]\n\n[r]: u\n")
    assert para.pictured and para.plain == "x"
    # Inside a link or emphasis the image is still a child the parser lists.
    (para,) = md.parse("[![x](i)](u) and *![y](j)*\n")
    assert para.pictured
    # Without one, and on a record built without saying, it is false.
    (para,) = md.parse("plain\n")
    assert not para.pictured
    assert not md.Paragraph("t", "t", 1).pictured


def test_a_tag_is_read_as_the_page_lays_it_out():
    # A tag the page lays out as a break leaves a space, so two words stay two words.
    (para,) = md.parse("maintainer<br>decision<li>required</li>\n")
    assert para.plain == "maintainer decision required"
    # Greptile on #462: a phrasing tag wraps text without breaking it, so it leaves nothing -
    # `Auto<b>nomy</b>` is the one word the page shows, and a space there split the key.
    (para,) = md.parse("**Auto<b>nomy</b>:** <em>x</em>y <a href='u'>z</a> <span>w</span>\n")
    assert para.plain == "Autonomy: xy z w"
    (para,) = md.parse("maintainer<b>decision</b>\n")
    assert para.plain == "maintainerdecision"  # one word on the page too
    # Tag names are read regardless of case, and attributes do not change the kind of tag.
    (para,) = md.parse('Auto<B class="k">nomy</B> <DIV>d</DIV>\n')
    assert para.plain == "Autonomy d"
    # Codex on #462: a `>` inside a quoted attribute value is not the end of the tag. The tag
    # grammar is CommonMark's, with quoted, single-quoted and unquoted values alike.
    (para,) = md.parse("""Auto<b title="a>b">nomy</b> <a href='u>v'>x</a> <img src=y>z\n""")
    assert para.plain == "Autonomy x z"
    # Codex on #462: `<q>` draws quotation marks, so it is the one phrasing tag that leaves
    # something behind - and `Auto<q></q>nomy` is the defaced word the page shows, not the key.
    (para,) = md.parse("Auto<q></q>nomy: <q>x</q>\n")
    assert para.plain == 'Auto""nomy: "x"'
    # Codex on #462: `<wbr>` is a break opportunity that draws nothing, so it is phrasing; and
    # `<del>`, `<s>`, `<strike>` strike their content out, a retraction, so they leave the `~~`
    # their Markdown spelling keeps and the two spellings read the same.
    (para,) = md.parse("Auto<wbr>nomy <del>a</del> <s>b</s> <strike>c</strike>\n")
    assert para.plain == "Autonomy ~~a~~ ~~b~~ ~~c~~"
    # An empty `<picture>` or `<source>` draws nothing; an `<img>` draws a picture between words.
    (para,) = md.parse("Auto<picture><source></picture>nomy a<img src=x>b\n")
    assert para.plain == "Autonomy a b"


def test_inline_html_is_what_the_parser_found_and_a_code_span_is_text():
    """Codex on #462 (read of `f9fc1f0`): a code span quoting the grooming marker is text on the
    page, and searching the source for the marker read it as misplaced. `inline_html` yields
    the runs the parser found to be HTML - a comment among them - and nothing in a code span or
    behind a backslash."""
    assert list(md.inline_html("text <!-- tether-grooming-v1 --> more <b>x</b>")) == [
        "<!-- tether-grooming-v1 -->",
        "<b>",
        "</b>",
    ]
    assert list(md.inline_html("Use `<!-- tether-grooming-v1 -->` when re-grooming")) == []
    assert list(md.inline_html("\\<!-- not a comment -->")) == []
    assert list(md.inline_html("plain text")) == []


def test_inline_tags_are_the_inline_html_the_parser_found_and_not_the_source():
    # A tag in running text is read as `tags` reads it; one in a code span or behind a backslash
    # is text on the page and is not reported, which a search of the source could not tell.
    assert list(md.inline_tags("a <details> b </DETAILS> `<img>` \\<br> <!-- <p> -->")) == [
        ("details", False),
        ("details", True),
    ]
    assert list(md.inline_tags("plain text")) == []


def test_tags_are_read_with_comments_removed_first():
    # Codex on #462: a `</details>` inside a comment closes nothing on the page, and a caller
    # counting nesting off the raw text closed the span on it.
    assert list(md.tags('<details><!-- </details> --><B class="x">b</B>')) == [
        ("details", False),
        ("b", False),
        ("b", True),
    ]
    assert md.has_tag("<!-- <b> -->") is False and md.has_tag("a <b>b</b>") is True
    # A bare `<` that is not a tag is prose, as it is on the page.
    (para,) = md.parse("n < 5 and m > 3\n")
    assert para.plain == "n < 5 and m > 3"


def test_a_character_reference_in_a_raw_block_is_decoded_after_its_tags_are_read():
    # Codex on #462: markdown-it decodes references in text it tokenizes, but a raw block is
    # handed over whole, and `maintainer&#32;decision` kept its `&#32;` while the page shows a
    # space. Decoded after the tags are read, so `&lt;b&gt;` is the literal `<b>` the page shows.
    (html,) = md.parse("<div>maintainer&#32;decision required &amp; &lt;b&gt;</div>\n")
    assert html.plain == "maintainer decision required & <b>"


def test_an_html_blocks_plain_is_its_visible_text_and_a_comment_blocks_is_empty():
    (html,) = md.parse("<details>\n<summary>maintainer decision required</summary>\n</details>\n")
    assert html.plain == "maintainer decision required"
    (comment,) = md.parse("<!-- Autonomy: agent-can-do-alone -->\n")
    assert comment.plain == ""


def test_an_html_blocks_shown_is_one_block_per_block_the_page_draws():
    """Codex on #462 (read of `aa47973`): a `<div>` of two `<p>` is one Markdown block and two
    rendered paragraphs, and a caller matching a key against `plain` read text the page never
    shows on one line. `shown` cuts at every block-level tag; a phrasing tag, a `<br>` and an
    `<img>` draw inside a block and do not cut; a comment shows nothing; `plain` is the pieces
    joined, as before. Codex on #462 (read of `93ed23d`): each piece names the tag that opened
    it, so a caller can read an `<h2>` as a heading and a `<p>` as a paragraph."""
    (block,) = md.parse(
        "<div>\n<p>Notes</p>\n<p><strong>Autonomy:</strong> maintainer decision required</p>\n"
        "</div>\n"
    )
    assert block.shown == (
        md.Shown("", "div"),
        md.Shown("Notes", "p"),
        md.Shown("Autonomy: maintainer decision required", "p"),
    )
    assert block.plain == "Notes Autonomy: maintainer decision required"
    (cells,) = md.parse("<table><tr><td>Autonomy:</td><td>agent-can-do-alone</td></tr></table>\n")
    assert cells.shown == (
        md.Shown("", "table"),
        md.Shown("", "tr"),
        md.Shown("Autonomy:", "td"),
        md.Shown("agent-can-do-alone", "td"),
    )
    (inside,) = md.parse("<p><b>Autonomy:</b><br>maintainer <img src=x.png> decision</p>\n")
    assert inside.shown == (md.Shown("Autonomy: maintainer decision", "p"),)
    (details,) = md.parse(
        "<details>\n<summary>maintainer decision required</summary>\n</details>\n"
    )
    assert details.shown == (
        md.Shown("", "details"),
        md.Shown("maintainer decision required", "summary"),
    )
    (comment,) = md.parse("<!-- Autonomy: agent-can-do-alone -->\n")
    assert comment.shown == ()
    # A tag inside a comment is not a boundary, and a drawn phrasing tag draws what it draws.
    (commented,) = md.parse("<p>Auto<!-- <p> -->nomy: <del>agent-can-do-alone</del></p>\n")
    assert commented.shown == (md.Shown("Autonomy: ~~agent-can-do-alone~~", "p"),)
    # A heading is named for its tag; text after a closing tag was opened by nothing.
    (headed,) = md.parse("<div><h2>Execution autonomy</h2><p>maintainer decision</p></div>tail\n")
    assert headed.shown == (
        md.Shown("", "div"),
        md.Shown("Execution autonomy", "h2"),
        md.Shown("maintainer decision", "p"),
        md.Shown("tail", ""),
    )
    # Codex on #462 (read of `5f41bae`): a block a tag opens that shows no text is a piece with
    # no text - the page draws a rule for `<hr>` - so what follows it is not the first thing
    # drawn after the heading. Text no tag opened that renders to nothing is nothing.
    (ruled,) = md.parse("<h2>Execution autonomy</h2>\n<hr>\n<p>agent-can-do-alone</p>\n")
    assert ruled.shown == (
        md.Shown("Execution autonomy", "h2"),
        md.Shown("", "hr"),
        md.Shown("agent-can-do-alone", "p"),
    )
    assert ruled.plain == "Execution autonomy agent-can-do-alone"


def test_hidden_inline_html_leaves_nothing_behind_as_a_comment_does():
    """Codex on #462 (read of `5f41bae`): the CommonMark inline HTML grammar admits a processing
    instruction, a declaration and a CDATA section besides a comment and a tag, and the browser
    draws nothing for any of them, so `Auto<?note?>nomy:` is the key the page shows. On its own
    line each is a raw block that shows nothing and carries no tag."""
    for hidden in ("<?note?>", "<!DOCTYPE x>", "<![CDATA[y]]>", "<!x>", "<!-- c -->"):
        (para,) = md.parse(f"**Auto{hidden}nomy:** maintainer decision required\n")
        assert para.plain == "Autonomy: maintainer decision required", (hidden, para.plain)
        assert not md.has_tag(hidden), hidden
    for block in ('<?xml version="1"?>\n', "<!DOCTYPE html>\n", "<![CDATA[x]]>\n"):
        (raw,) = md.parse(block)
        assert isinstance(raw, md.Html) and raw.plain == "" and raw.shown == (), block


def test_raw_html_is_rendered_as_what_github_keeps_of_it():
    """Codex on #462 (read of `9fc6782`): GitHub sanitizes raw HTML before rendering, and a tag
    it does not keep is removed with its text left in place, so `Auto<foo></foo>nomy:` shows
    the key whole - where rendering every unknown tag as a space split it into `Auto nomy:`.
    The kept elements are html-pipeline's allowlist (v3.2.4, last changed 2024-02-02), pinned
    here so a drift in the module's partition of it is a red test; a stripped block-ish tag -
    Selma's `whitespace_elements` - leaves a space on each side of its text, as the sanitizer
    does; and a raw run is cut only at a kept block tag, never at one the page strips."""
    kept = {
        "h1", "h2", "h3", "h4", "h5", "h6", "br", "b", "i", "strong", "em", "a", "pre", "code",
        "img", "tt", "div", "ins", "del", "sup", "sub", "p", "picture", "ol", "ul", "table",
        "thead", "tbody", "tfoot", "blockquote", "dl", "dt", "dd", "kbd", "q", "samp", "var",
        "hr", "ruby", "rt", "rp", "li", "tr", "td", "th", "s", "strike", "summary", "details",
        "caption", "figure", "figcaption", "abbr", "bdo", "cite", "dfn", "mark", "small",
        "source", "span", "time", "wbr",
    }  # fmt: skip
    assert frozenset(kept) == md.KEPT_TAGS
    assert md.BLOCK_TAGS < md.KEPT_TAGS
    # A stripped tag joins; a kept phrasing tag joins; a kept block tag and a stripped
    # block-ish tag each leave a space.
    for text, plain in (
        (
            "**Auto<foo></foo>nomy:** maintainer decision required",
            "Autonomy: maintainer decision required",
        ),
        ("Auto<font>nomy</font>: x", "Autonomy: x"),
        ("Auto<u>nomy</u>: x", "Autonomy: x"),
        ("Auto<center/>nomy: x", "Autonomy: x"),
        ("needs<foo></foo>maintainer", "needsmaintainer"),
        ("Auto<b>nomy</b>: x", "Autonomy: x"),
        ("maintainer<br>decision", "maintainer decision"),
        ("Auto<section></section>nomy: x", "Auto nomy: x"),
        ("Auto<nav>nomy</nav>: x", "Auto nomy : x"),
    ):
        (para,) = md.parse(text + "\n")
        assert para.plain == plain, (text, para.plain)
    # Raw blocks are cut at kept block tags only: an unknown tag is no boundary, and nor is
    # a stripped block-ish one, whose text flows into the block around it with a space.
    (joined,) = md.parse("<p>Auto<foo></foo>nomy: maintainer decision required</p>\n")
    assert joined.shown == (md.Shown("Autonomy: maintainer decision required", "p"),)
    (spaced,) = md.parse("<div><section>Autonomy:</section> maintainer decision</div>\n")
    assert spaced.shown == (md.Shown("Autonomy: maintainer decision", "div"),)
    (cut,) = md.parse("<div><p>Autonomy:</p> maintainer decision</div>\n")
    assert cut.shown == (
        md.Shown("", "div"),
        md.Shown("Autonomy:", "p"),
        md.Shown("maintainer decision", ""),
    )
    # Every tag the page strips is still a tag to `has_tag`: the rendering may read it, and
    # the registered shapes may not carry it.
    assert md.has_tag("Auto<foo></foo>nomy") and md.has_tag("<section>x</section>")


def test_a_footnote_definition_is_a_footnote_paragraph_wherever_the_parser_put_it():
    """Codex on #462 (read of `9fc6782`): GitHub renders footnotes and the parser does not, so
    `[^1]: Autonomy: maintainer decision required` is a paragraph whose text starts with the
    label - and `[^1]: Autonomy:`, whose text is one word, is a link reference definition to
    CommonMark, swallowed into the parser's environment with no block at all, while the page
    draws a footnote holding `Autonomy:`. Both are footnote paragraphs: `plain` is the text
    behind the label, as the page draws it at its foot, `text` the source, and `footnote` set
    so a caller can keep them out of the sections and leads the page draws them away from
    (Codex on #462, read of `f5c37f0`). A swallowed one sits at its source line, sorted in
    after a block that opens on the same line; a reference whose label does not open with `^`
    draws nothing; and a line that opens a definition cuts the paragraph above it, as
    cmark-gfm closes a paragraph for one where CommonMark reads a lazy continuation."""
    note = md.Paragraph("[^1]: Autonomy:", "Autonomy:", 0, note=0)
    assert md.parse("[^1]: Autonomy:\n") == (note,) and note.footnote
    assert md.parse("[^1]:Autonomy:\n") == (md.Paragraph("[^1]:Autonomy:", "Autonomy:", 0, note=0),)
    (para,) = md.parse("[^1]: **Autonomy:** maintainer decision required\n")
    assert para == md.Paragraph(
        "[^1]: **Autonomy:** maintainer decision required",
        "Autonomy: maintainer decision required",
        0,
        note=0,
    )
    assert not md.Paragraph("x", "x", 0).footnote
    assert md.parse("[foo]: /url\n") == ()
    assert md.parse('[foo]: /url "Autonomy: x"\n') == ()
    # A quoted title is part of the footnote's text; an escaped label is text, not a footnote.
    (titled,) = md.parse('[^Note]: Autonomy: "maintainer decision required"\n')
    assert titled.plain == 'Autonomy: "maintainer decision required"' and titled.footnote
    (escaped,) = md.parse("\\[^1]: Autonomy: x\n")
    assert escaped == md.Paragraph("\\[^1]: Autonomy: x", "[^1]: Autonomy: x", 0)
    # Sorted in by line; after the list whose item it opens, since the list opens first.
    blocks = md.parse("intro\n\n[^1]: *Autonomy:*\n\n- item\n\n[^2]: x\n")
    assert kinds(blocks) == ["Paragraph", "Paragraph", "ListBlock", "Paragraph"]
    assert [block.line for block in blocks] == [0, 2, 4, 6]
    assert blocks[1].plain == "Autonomy:" and blocks[1].footnote and not blocks[0].footnote
    nested = md.parse("- [^1]: Autonomy:\n")
    assert kinds(nested) == ["ListBlock", "Paragraph"] and nested[1].line == 0
    (listed,) = md.parse("- [^1]: Autonomy: x\n")
    assert listed.items[0].blocks[0].footnote and listed.items[0].blocks[0].plain == "Autonomy: x"
    # A definition continued onto the next line is one paragraph, as a footnote is; a later
    # line that opens one cuts the paragraph, each cut at its own line.
    (continued,) = md.parse("[^1]:\n  Autonomy:\n")
    assert continued.plain == "Autonomy:" and continued.line == 0 and continued.footnote
    cut = md.parse("prose\n[^1]: Autonomy: x\nmore\n [^2]: y\n")
    assert [(p.plain, p.line, p.footnote) for p in cut] == [
        ("prose", 0, False),
        ("Autonomy: x more", 1, True),
        ("y", 3, True),
    ]
    # A reference whose label opens with `^` but is no footnote label - a space inside it -
    # is a link definition, filed under a `^` key all the same, and draws nothing (Codex on
    # #462, read of `270e6ae`); it used to trip an assertion.
    for link in ("[^release note]: /url\n", "[^]: /url\n", "[^a\tb]: /url\n", "[^ x]: /url\n"):
        assert md.parse(link) == (), link
    assert (
        md.parse("[^release note]: /url\n\nSee [^release note].\n")[0].plain == "See ^release note."
    )
    # A footnote whose text links through a reference defined elsewhere renders the link.
    (linked,) = md.parse("[^1]: see [the issue][ref]\n\n[ref]: /u\n")
    assert linked.plain == "see the issue" and linked.footnote


def test_a_stripped_elements_text_stays_and_an_empty_strike_draws_nothing():
    """Codex on #462 (read of `f5c37f0`) held, from Selma's source, that the sanitizer removes
    `<svg>`, `<math>` and `<noscript>` with their text, and read 29 encoded it. GitHub's
    markdown endpoint (2026-10-03) renders `Auto<svg>y</svg>nomy: x` as `Autoynomy: x` and
    `<svg>maintainer decision required</svg>` as that paragraph, so the three are stripped
    tags like any other, text kept, and the rule hid a restriction the page shows - reversed
    at read 31, where Codex found the pattern mis-reading a quoted `>` as well. An empty
    `<del>`, `<s>` or `<strike>` strikes nothing and draws nothing, so `Auto<del></del>nomy:`
    is the key - where drawing the marks for both tags read `Auto~~~~nomy` - and its opening
    tag is read by the shared attribute grammar, so a `>` inside a quoted value does not end
    it (Codex on #462, read of `c02ab15`). Inline children are rendered together, so the rule
    holds across them; `<script>` and its kin are literal text; an empty `<q>` still draws its
    quotation marks; and a tag inside a stripped element is a tag, since the page keeps it."""
    for text, plain in (
        (
            "**Auto<svg>x</svg>nomy:** maintainer decision required",
            "Autoxnomy: maintainer decision required",
        ),
        ("Auto<math><mi>x</mi></math>nomy: x", "Autoxnomy: x"),
        ("Auto<noscript>x</noscript>nomy: x", "Autoxnomy: x"),
        ('Auto<svg viewBox="0 0 1 1"><g>t</g></svg>nomy: x', "Autotnomy: x"),
        ("Auto<svg>nomy: maintainer decision required", "Autonomy: maintainer decision required"),
        ('Auto<svg title=">">y</svg>nomy: x', "Autoynomy: x"),
        ("a <svg>b <b>c</b> d</svg> e", "a b c d e"),
        ("Auto<script>x</script>nomy: x", "Autoxnomy: x"),
        ("Auto<style>x</style>nomy: x", "Autoxnomy: x"),
        (
            "**Auto<del></del>nomy:** maintainer decision required",
            "Autonomy: maintainer decision required",
        ),
        ("Auto<s></s>nomy: x", "Autonomy: x"),
        ("Auto<strike></strike>nomy: x", "Autonomy: x"),
        ("Auto<del><s></s></del>nomy: x", "Autonomy: x"),
        ("Auto<del> </del>nomy: x", "Auto nomy: x"),
        ('Auto<del title=">"></del>nomy: x', "Autonomy: x"),
        ("Auto<del title='>' >  </del>nomy: x", "Auto nomy: x"),
        ("<del>agent-can-do-alone</del>", "~~agent-can-do-alone~~"),
        ("<del>maintainer decision required</del>", "~~maintainer decision required~~"),
        ("Auto<q></q>nomy: x", 'Auto""nomy: x'),
        ("n < 5 and `<b>` and <svg>x</svg>&amp;", "n < 5 and <b> and x&"),
    ):
        (para,) = md.parse(text + "\n")
        assert para.plain == plain, (text, para.plain)
    # A self-closing stripped tag holds nothing, so the text after it is whole (Codex on #462,
    # read of `270e6ae`), with or without a quoted `>` in its attributes.
    for void in (
        "<svg/>",
        "<svg />",
        '<math viewBox="0 0 1 1"/>',
        "<noscript/>",
        "<svg/><svg/>",
        '<svg title=">"/>',
    ):
        (closed,) = md.parse(f"**Auto{void}nomy:** maintainer decision required\n")
        assert closed.plain == "Autonomy: maintainer decision required", (void, closed.plain)
    (raw_void,) = md.parse("<p>Auto<svg/>nomy: maintainer decision required</p>\n")
    assert raw_void.shown == (md.Shown("Autonomy: maintainer decision required", "p"),)
    # The same in a raw block, and for the tags a caller counts: a `</details>` inside an
    # `<svg>` closes the widget on the page, which drops the `<svg>` and keeps what it held.
    (raw,) = md.parse("<p>Auto<svg>x</svg>nomy: maintainer decision required</p>\n")
    assert raw.shown == (md.Shown("Autoxnomy: maintainer decision required", "p"),)
    (block,) = md.parse("<svg>\nmaintainer decision required\n</svg>\n")
    assert block.shown == (md.Shown("maintainer decision required", ""),)
    (inline,) = md.parse("<svg>maintainer decision required</svg>\n")
    assert inline.plain == "maintainer decision required"
    assert md.has_tag("<svg>x</svg>") and md.has_tag("<del></del>")
    closes = [("svg", False), ("details", True), ("svg", True)]
    assert list(md.tags("<svg></details></svg>")) == closes
    assert list(md.inline_tags("a <svg></details></svg> b")) == closes
    assert list(md.tags("<svg/><details>")) == [("svg", False), ("details", False)]


def test_a_footnote_continuation_is_more_footnote_paragraphs():
    """Codex on #462 (read of `e259a99`): cmark-gfm reads the lines indented four spaces after
    a footnote definition as the rest of it and draws them inside the footnote, while
    CommonMark reads them as an indented code block, so `[^1]: first line` over `    Autonomy:
    maintainer decision required` was a footnote and a `Code` nobody read. GitHub's markdown
    endpoint (2026-10-03) draws the continuation's paragraphs, lists and headings inside the
    footnote, and nothing indented two spaces. The continuation comes back as footnote
    paragraphs, one per block the foot draws text for, at their source lines."""
    doc = md.parse(
        "See[^1].\n\n[^1]: first line\n    lazy\n\n    - **Autonomy:** maintainer decision "
        "required\n\n    ## Heading inside\n\n    more\n\nnot inside\n"
    )
    assert [(block.plain, block.line, block.footnote) for block in doc] == [
        ("See[^1].", 0, False),
        ("first line lazy", 2, True),
        ("Autonomy: maintainer decision required", 5, True),
        ("Heading inside", 7, True),
        ("more", 9, True),
        ("not inside", 11, False),
    ]
    # After the swallowed one-word form, and after a label with nothing behind it.
    doc = md.parse("See[^1].\n\n[^1]: /url\n\n    Autonomy: maintainer decision required\n")
    assert [(block.plain, block.footnote) for block in doc[1:]] == [
        ("/url", True),
        ("Autonomy: maintainer decision required", True),
    ]
    doc = md.parse("[^1]:\n\n    Autonomy: maintainer decision required\n")
    assert [(block.plain, block.footnote) for block in doc] == [
        ("", True),
        ("Autonomy: maintainer decision required", True),
    ]
    # Two spaces continue nothing: that line is a paragraph of the body, as on the page.
    doc = md.parse("[^1]: first line\n\n  Autonomy: maintainer decision required\n")
    assert [(block.plain, block.footnote) for block in doc] == [
        ("first line", True),
        ("Autonomy: maintainer decision required", False),
    ]
    # An indented block after anything but a footnote is the code block it always was, and a
    # fence inside a continuation is literal.
    (para, code) = md.parse("text\n\n    Autonomy: maintainer decision required\n")
    assert isinstance(code, md.Code) and not code.fenced
    doc = md.parse("[^1]: note\n\n    ```\n    Autonomy: human action\n    ```\n\n    after\n")
    assert [block.plain for block in doc] == ["note", "", "after"]
    # A table in a continuation is a footnote paragraph per row; a nested definition is one too.
    doc = md.parse("[^1]: note\n\n    | Autonomy | human action |\n    | --- | --- |\n")
    assert [(block.plain, block.footnote) for block in doc] == [
        ("note", True),
        ("Autonomy human action", True),
    ]
    # Codex on #462 (read of `f488e46`): raw HTML in a continuation is a paragraph per block
    # the page draws of it, not one joined; and every paragraph of one definition carries the
    # line it opens on as `note`, a nested definition its own, the next definition another.
    doc = md.parse(
        "[^1]: note\n\n    <div><p>Notes</p><p>Autonomy: human review required</p></div>\n\n"
        "    [^2]: nested\n\n[^3]: next\n"
    )
    # The `<div>` opens a block of its own that shows no text, a piece as it is in the body.
    assert [(block.plain, block.note) for block in doc] == [
        ("note", 0),
        ("", 0),
        ("Notes", 0),
        ("Autonomy: human review required", 0),
        ("nested", 4),
        ("next", 6),
    ]
    # Codex on #462 (read of `22b148b`): a block the foot draws with no text - a rule, a code
    # block, a raw piece showing nothing - keeps its place as an empty footnote paragraph, so
    # a bare key above it heads the rule and not the value below, as it would in the body.
    for empty in ("<hr>", "---", "    code", "<div></div>", "-", "> <!-- c -->", "- <!-- c -->"):
        doc = md.parse(f"[^1]: Autonomy\n\n    {empty}\n\n    agent-can-do-alone\n")
        assert [(block.plain, block.note) for block in doc] == [
            ("Autonomy", 0),
            ("", 0),
            ("agent-can-do-alone", 0),
        ], empty
    # An item or a quote that draws nothing stands in for itself, as it does in the body; one
    # that draws does not, and a list draws a bullet per item.
    doc = md.parse("[^1]: Autonomy\n\n    - <!-- c -->\n    - agent-can-do-alone\n")
    assert [(block.plain, block.line) for block in doc] == [
        ("Autonomy", 0),
        ("", 2),
        ("agent-can-do-alone", 3),
    ]
    doc = md.parse("[^1]: Autonomy\n\n    > agent-can-do-alone\n")
    assert [block.plain for block in doc] == ["Autonomy", "agent-can-do-alone"]
    assert md.draws(md.Paragraph("<!-- c -->", "", 0)) is False
    assert md.draws(md.Paragraph("![](x)", "", 0, pictured=True)) is True
    assert md.draws(md.Rule(0)) is True


def test_an_images_alternative_text_is_its_label_rendered():
    """Codex on #462 (read of `e259a99`): the parser keeps an image's raw label as the token's
    content, so `![**Autonomy:** human review required](x)` rendered `**Autonomy:**` and no
    key matched it, while GitHub's `alt` is the label rendered - `alt="Autonomy: human review
    required"` from the markdown endpoint (2026-10-03), and `alt="b c d &amp;"` for a label
    holding code, a nested image and a character reference. The parser's own renderer is not
    the oracle: it drops those three. And a raw `<img>` keeps its `alt` through the sanitizer,
    so it leaves that text between its spaces, as a Markdown image's `plain` shows its
    alternative text (Codex on #462, the same read)."""
    for text, plain in (
        ("![**Autonomy:** human review required](x)", "Autonomy: human review required"),
        ("a ![b `c` ![d](e) &amp; \\* x](f) g", "a b c d & * x g"),
        ("![a <b>b</b> c](x)", "a b c"),
        ("![a\nb](x)", "a b"),
        ("![](x) y", "y"),
    ):
        (para,) = md.parse(text + "\n")
        assert para.plain == plain and para.pictured, (text, para)
    (item,) = md.parse("- ![**Autonomy:** human review required](x)\n")
    assert item.items[0].blocks[0].plain == "Autonomy: human review required"
    for text, plain in (
        (
            '<img src="/missing" alt="Autonomy: human review required"> after',
            "Autonomy: human review required after",
        ),
        ("<img alt='x &amp; y'>z", "x & y z"),
        ("a<img alt=bare>b", "a bare b"),
        ("a<img src=x>b", "a b"),
        ('a<img alt="">b', "a b"),
        ('<img data-alt="no" alt="yes">', "yes"),
        # Codex on #462 (read of `be3164c`): an `alt=` inside another attribute's quoted value
        # is that value's text; the attributes are read in order, and a name written twice
        # keeps its first value, as the page keeps it.
        (
            '<img title=" alt=\'note\'" alt="Autonomy: human review required">',
            "Autonomy: human review required",
        ),
        ("<img title='x=\"alt=z\"' alt=y>", "y"),
        ('<img alt="a" alt="b">', "a"),
        ('<img ALT="upper" src=x>', "upper"),
        ("<img alt>", ""),
    ):
        (para,) = md.parse(text + "\n")
        assert para.plain == plain, (text, para.plain)
    (raw,) = md.parse('<p><img src="/m" alt="Autonomy: human review required"></p>\n')
    assert raw.shown == (md.Shown("Autonomy: human review required", "p"),)


def test_attributes_are_read_in_order_by_the_grammar():
    assert md._attributes('<img title=" alt=\'note\'" alt="A: b" src=x>') == {
        "title": " alt='note'",
        "alt": "A: b",
        "src": "x",
    }
    assert md._attributes("<img alt='a' alt=\"b\" hidden>") == {"alt": "a", "hidden": ""}
    assert md._attributes("<img>") == {} and md._attributes("<img />") == {}


def test_a_table_rows_plain_cells_are_rendered_in_order():
    (table,) = md.parse("| **Field** | [Value](u) |\n|---|---|\n| `Autonomy` | *x* &amp; y |\n")
    assert [row.plain for row in table.rows] == [("Field", "Value"), ("Autonomy", "x & y")]
    # A one-column table has one cell per row, and no second cell at all.
    (table,) = md.parse("| Autonomy |\n| --- |\n")
    assert [row.plain for row in table.rows] == [("Autonomy",)]


def test_a_block_quote_contains_its_blocks_and_a_quoted_item_is_not_at_column_zero():
    (quote,) = md.parse("> - Autonomy: human action\n>\n> prose\n")
    assert isinstance(quote, md.BlockQuote) and quote.line == 0
    lst, para = quote.blocks
    assert lst.items[0].column == 2
    assert para == md.Paragraph(text="prose", plain="prose", line=2)


def test_a_thematic_break_is_a_rule_and_not_a_setext_underline():
    doc = md.parse("para\n\n---\n")
    assert doc == (md.Paragraph(text="para", plain="para", line=0), md.Rule(line=2))


def test_crlf_input_yields_the_same_tree_and_lines_as_lf_input():
    lf = "## Autonomy\n\nagent-can-do-alone\n- a\n  b\n"
    assert md.parse(lf.replace("\n", "\r\n")) == md.parse(lf)
    assert md.parse(lf.replace("\n", "\r")) == md.parse(lf)


def test_walk_visits_every_block_at_every_depth_in_document_order():
    doc = md.parse("- a\n  - b\n\n    > c\n\n# h\n")
    assert kinds(md.walk(doc)) == [
        "ListBlock",
        "Paragraph",
        "ListBlock",
        "Paragraph",
        "BlockQuote",
        "Paragraph",
        "Heading",
    ]


def test_an_empty_document_has_no_blocks():
    assert md.parse("") == ()
    assert md.parse("\n\n") == ()


def test_a_block_the_module_does_not_model_raises_rather_than_vanishing(monkeypatch):
    # The parser is configured once; a rule it did not have when this module was written would
    # emit a token kind the tree cannot hold, and a gate must stop on that rather than read past
    # it. Simulated by renaming one token, since the real configuration emits nothing unknown.
    real = md._PARSER.parse

    def renamed(text: str, *args, **kwargs):
        tokens = real(text, *args, **kwargs)
        tokens[0].type = "footnote_block_open"
        return tokens

    monkeypatch.setattr(md._PARSER, "parse", renamed)
    with pytest.raises(md.MarkdownStructureError, match="footnote_block_open"):
        md.parse("para\n")


def test_a_block_opening_on_its_parent_items_line_has_its_own_column():
    # Codex on #470: `- # Notes` reported the heading at column 0, and `- - value` both items at
    # column 0, because the prefix scan stopped at the parent's marker. A same-line child is
    # scanned from the parent's content column, so compact nesting is distinguishable from the
    # top level - the distinction the gate's column-zero rule turns on.
    (lst,) = md.parse("- # Notes\n")
    assert lst.items[0].blocks[0] == md.Heading(
        level=1, text="Notes", plain="Notes", line=0, column=2, markup="#"
    )

    def inner(src: str) -> md.ListItem:
        (outer,) = md.parse(src)
        (item,) = outer.items
        (child,) = [b for b in item.blocks if isinstance(b, md.ListBlock)]
        return child.items[0]

    assert inner("- - value\n").column == 2
    assert inner("1. - x\n").column == 3
    assert inner("10)   - x\n").column == 6
    # A tab after the marker stops at column 4, so the child marker sits there.
    assert inner("-\t- x\n").column == 4
    # Inside a quote the `>` counts too, on the same line and on later ones alike.
    (quote,) = md.parse("> - # h\n")
    assert quote.blocks[0].items[0].column == 2
    assert quote.blocks[0].items[0].blocks[0].column == 4
    (lst,) = md.parse("- > # h\n")
    assert lst.items[0].blocks[0].blocks[0].column == 4
    # Five or more columns of padding open indented code, so there is no same-line child at all.
    (lst,) = md.parse("-     - x\n")
    assert lst.items[0].blocks == (md.Code(text="- x\n", line=0, fenced=False, info=""),)


def test_a_table_token_the_module_does_not_model_raises_rather_than_vanishing(monkeypatch):
    # Codex on #470: the row reader stepped over anything it did not expect. The table
    # scaffolding is named in full, so an extension's extra token stops the read like an
    # unmodelled block does.
    real = md._PARSER.parse

    def renamed(text: str, *args, **kwargs):
        tokens = real(text, *args, **kwargs)
        tokens[[t.type for t in tokens].index("thead_open")].type = "tfoot_open"
        return tokens

    monkeypatch.setattr(md._PARSER, "parse", renamed)
    with pytest.raises(md.MarkdownStructureError, match="tfoot_open"):
        md.parse("| a |\n|---|\n")


def test_a_quoted_table_ending_in_a_bare_quote_marker_is_read_rather_than_crashed():
    # markdown-it-py 4.2.0, the pinned version, indexes past the end of this exact body
    # (upstream #415) when it lacks a final newline. The missing newline is supplied before
    # parsing, which changes no block, and the body reads as GitHub renders it.
    doc = md.parse("> | a | b |\n> |---|---|\n>")
    assert doc == (
        md.BlockQuote(
            line=0,
            blocks=(
                md.Table(
                    line=0,
                    rows=(md.TableRow(cells=("a", "b"), plain=("a", "b"), line=0, header=True),),
                ),
            ),
        ),
    )


def test_a_failure_inside_the_parser_is_reported_as_unreadable(monkeypatch):
    # Whatever the parser raises, the caller sees one error type and never a traceback from
    # inside a third-party rule: an unreadable body is a result the gate can report.
    def broken(text: str, *args, **kwargs):
        raise IndexError("string index out of range")

    monkeypatch.setattr(md._PARSER, "parse", broken)
    with pytest.raises(md.MarkdownStructureError, match="could not read"):
        md.parse("anything\n")

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
    assert doc[0] == md.Html(text="<!-- tether-grooming-v1 -->", plain="", line=0)
    assert doc[2] == md.Html(text="<!-- aside\nspans -->", plain="", line=2)


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

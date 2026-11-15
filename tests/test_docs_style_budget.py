# SPDX-FileCopyrightText: 2026 The Tether Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""A ratchet on the countable writing-style rules (#592).

The style guide (``docs/maintainers/writing-style.md``, #582) sets rules that a machine can
count. ``docs/style-budget.toml`` holds a budget for each nav page and for ``README.md``:

* ``long_share`` -- the percentage of sentences over the length limit of rule 2 (20 words on a
  ``procedure`` page, 25 on a ``descriptive`` one);
* ``semicolons`` -- the semicolons in prose (rule 9);
* ``long_paragraphs`` -- the paragraphs of more than six sentences (rule 2);
* ``retired`` -- the uses of a phrase on the ``[retired]`` list: the terms #589 retires, the
  idioms the sweep issues of #588 remove, and the British spellings of rule 4. The allowance
  is 0 when a page names none, and the list exists only so the test can land before the sweeps.

A page over a budget fails. A page *well under* a budget fails too, until the budget is lowered
to the measured value: that is the ratchet, so a budget goes down with each sweep and cannot
creep back up without a reviewed edit to the table. ``long_share`` is well under when it is more
than ``SHARE_SLACK`` points below its budget, because a share moves when unrelated short
sentences are added; the counts are well under at any value below the budget.

This is not an ASD-STE100 checker. The sentence splitter is approximate, and it does not read
the controlled dictionary. It reads Markdown as text, with the standard library only.

``python tests/test_docs_style_budget.py`` prints the table measured now, and ``--write``
writes it to ``docs/style-budget.toml``.
"""

from __future__ import annotations

import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from _docs_source import split_front_matter

_REPO = Path(__file__).resolve().parents[1]
BUDGET_FILE = _REPO / "docs" / "style-budget.toml"
LIMITS = {"procedure": 20, "descriptive": 25}
MAX_SENTENCES = 6
SHARE_SLACK = 2.0

_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_SKIP_LINE = re.compile(r"^\s*(?:<|\[[^\]]+\]:\s|!!!|\?\?\?|-{3,}\s*$|\*{3,}\s*$)")
_ABBREVIATION = re.compile(r"\b(e\.g|i\.e|etc|vs|cf|Fig|Eq|al|approx|No)\.", re.IGNORECASE)
_SPLIT = re.compile(r"(?<=[.!?])[\"')\]*_]*\s+(?=[\"'(\[*_`]*[A-Z0-9`])")
_QUOTED = ('"', "“", "‘", "'")


def _inline(text: str, code: str) -> str:
    """Markdown inline markup reduced to what the reader sees; a code span becomes ``code``."""
    text = re.sub(r"(`+)(.+?)\1", f" {code} " if code else " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\[[^\]]*\]", r"\1", text)
    text = re.sub(r"<https?://[^>]+>", " LINK ", text)
    text = re.sub(r"</?[A-Za-z][^>]*>", " ", text)
    text = re.sub(r"&#?\w+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def prose_units(markdown: str) -> tuple[list[tuple[str, bool]], list[str]]:
    """The page's prose as ``(text, is_list_item)`` units, and its headings.

    Front matter, fenced code, HTML comments and blocks, tables, link definitions and
    admonition markers are dropped. A list item is its own unit.
    """
    text = re.sub(r"<!--.*?-->", " ", split_front_matter(markdown)[1], flags=re.DOTALL)
    units: list[tuple[str, bool]] = []
    headings: list[str] = []
    current: list[str] = []
    is_item = False
    fence = ""

    def flush() -> None:
        if current:
            units.append((" ".join(current), is_item))
            current.clear()

    for line in text.splitlines():
        marker = _FENCE.match(line)
        if fence:
            if marker and marker.group(1)[0] == fence[0] and len(marker.group(1)) >= len(fence):
                fence = ""
            continue
        if marker:
            flush()
            fence = marker.group(1)
            continue
        stripped = line.strip().lstrip(">").strip()
        if not stripped or stripped.startswith("|") or _SKIP_LINE.match(stripped):
            flush()
            continue
        if stripped.startswith("#"):
            flush()
            headings.append(stripped.lstrip("#").strip())
            continue
        item = _LIST_ITEM.match(stripped)
        if item:
            flush()
            is_item = True
            stripped = stripped[item.end() :]
        elif not current:
            is_item = False
        current.append(stripped)
    flush()
    return units, headings


def sentences(unit: str) -> list[str]:
    """The sentences of one unit with three words or more; a code span counts as one word."""
    text = _ABBREVIATION.sub(r"\1", _inline(unit, "CODE"))
    return [s for s in _SPLIT.split(text) if len(words(s)) >= 3]


def words(sentence: str) -> list[str]:
    return [w for w in sentence.split() if any(c.isalnum() for c in w)]


def retired_uses(text: str, phrases: list[str]) -> list[str]:
    """Each use of a retired phrase in ``text`` (code removed); a quoted mention is not a use."""
    found = []
    for phrase in phrases:
        for match in re.finditer(rf"(?<!\w){re.escape(phrase)}(?!\w)", text, re.IGNORECASE):
            before = text[match.start() - 1] if match.start() else ""
            after = text[match.end()] if match.end() < len(text) else ""
            if not (before in _QUOTED and after in _QUOTED + ("”", "’")):
                found.append(phrase)
    return found


@dataclass
class Measure:
    kind: str
    sentences: int = 0
    long: list[str] = field(default_factory=list)
    semicolons: list[str] = field(default_factory=list)
    long_paragraphs: list[str] = field(default_factory=list)
    retired: list[tuple[str, str]] = field(default_factory=list)

    @property
    def long_share(self) -> float:
        return round(100 * len(self.long) / self.sentences, 1) if self.sentences else 0.0

    def values(self) -> dict[str, float | int]:
        return {
            "long_share": self.long_share,
            "semicolons": len(self.semicolons),
            "long_paragraphs": len(self.long_paragraphs),
            "retired": len(self.retired),
        }


def measure(markdown: str, kind: str, phrases: list[str]) -> Measure:
    result = Measure(kind)
    units, headings = prose_units(markdown)
    for heading in headings:
        result.retired += [(p, heading) for p in retired_uses(_inline(heading, ""), phrases)]
    for unit, is_item in units:
        found = sentences(unit)
        result.sentences += len(found)
        result.long += [s for s in found if len(words(s)) > LIMITS[kind]]
        result.semicolons += [s for s in found for _ in range(s.count(";"))]
        if len(found) > MAX_SENTENCES and not is_item:
            result.long_paragraphs.append(found[0])
        plain = _inline(unit, "")
        result.retired += [(p, plain) for p in retired_uses(plain, phrases)]
    return result


_RULES = {
    "long_share": "rule 2, sentence length",
    "semicolons": "rule 9, semicolons",
    "long_paragraphs": "rule 2, paragraph length",
    "retired": "rule 4/5, retired phrase",
}


def check(page: str, found: Measure, budget: dict) -> list[str]:
    """One message for each budget of ``page`` that ``found`` is over or well under."""
    messages = []
    examples = {
        "long_share": found.long,
        "semicolons": found.semicolons,
        "long_paragraphs": found.long_paragraphs,
        "retired": [f"{phrase!r} in: {text}" for phrase, text in found.retired],
    }
    for key, value in found.values().items():
        allowed = budget.get(key, 0)
        rule = _RULES[key]
        if value > allowed:
            first = examples[key][0]
            messages.append(
                f"{page}: over budget on {rule}: {value} > {allowed}"
                + (f" (limit {LIMITS[found.kind]} words)" if key == "long_share" else "")
                + f". First offender: {first[:300]!r}"
            )
        elif value < allowed - (SHARE_SLACK if key == "long_share" else 0):
            messages.append(
                f"{page}: well under budget on {rule}: {value} < {allowed}. Lower {key} to "
                f"{value} in docs/style-budget.toml (python tests/test_docs_style_budget.py "
                "prints every measured value)."
            )
    return messages


def nav_pages() -> list[str]:
    """Every page in the ``mkdocs.yml`` nav, as a repository path, plus ``README.md``."""
    lines = (_REPO / "mkdocs.yml").read_text(encoding="utf-8").splitlines()
    start = lines.index("nav:") + 1
    pages = ["README.md"]
    for line in lines[start:]:
        if line and not line[0].isspace() and not line.startswith("#"):
            break
        match = re.search(r":\s*\"?([^\s\"]+\.md)\"?\s*$", line)
        if match and not line.lstrip().startswith("#"):
            pages.append(f"docs/{match.group(1)}")
    return pages


def load_budget() -> dict:
    return tomllib.loads(BUDGET_FILE.read_text(encoding="utf-8"))


def measure_page(page: str, table: dict) -> Measure:
    kind = table["pages"].get(page, {}).get("kind", "descriptive")
    text = (_REPO / page).read_text(encoding="utf-8")
    return measure(text, kind, table["retired"]["phrases"])


# --- The repository's pages ------------------------------------------------------------


def test_budget_lists_every_nav_page_and_the_readme() -> None:
    table = load_budget()
    assert sorted(table["pages"]) == sorted(nav_pages()), (
        "docs/style-budget.toml must hold one entry for each nav page and README.md, and no "
        "other; run python tests/test_docs_style_budget.py to print the measured table"
    )
    for page, entry in table["pages"].items():
        assert entry.get("kind") in LIMITS, f"{page}: kind must be one of {sorted(LIMITS)}"


@pytest.mark.parametrize("page", nav_pages())
def test_page_is_within_its_style_budget(page: str) -> None:
    table = load_budget()
    budget = table["pages"].get(page)
    assert budget is not None, f"{page} has no entry in docs/style-budget.toml"
    messages = check(page, measure_page(page, table), budget)
    assert not messages, "\n".join(messages)


# --- The rules themselves, on small pages -----------------------------------------------

_PHRASES = ["project store", "out of the box"]


def _long(n: int) -> str:
    return " ".join(["word"] * (n - 1)) + " end."


def test_code_tables_front_matter_and_headings_are_not_prose() -> None:
    page = (
        '---\ndescription: "x; y"\n---\n# Title; here\n\n| a; b | c |\n|---|---|\n\n'
        "```sh\necho a; echo b\n```\n\nRun `a; b` now please.\n"
    )
    found = measure(page, "descriptive", _PHRASES)
    assert found.sentences == 1 and not found.semicolons


def test_a_code_span_counts_as_one_word() -> None:
    sentence = "Run " + "`" + " ".join(["x"] * 30) + "` " + _long(23)
    assert not measure(sentence, "descriptive", []).long
    assert measure(sentence, "procedure", []).long


def test_sentences_paragraphs_and_list_items_split() -> None:
    paragraph = " ".join(["This is one short sentence."] * 7)
    found = measure(paragraph + "\n\n- " + paragraph + "\n", "descriptive", [])
    assert found.sentences == 14 and len(found.long_paragraphs) == 1
    assert measure("See e.g. the file now. It works well here.", "descriptive", []).sentences == 2


def test_retired_phrase_counts_outside_code_and_quotes_only() -> None:
    page = (
        "Open the project store now.\n\n## Out of the box\n\n"
        'Write "project file", not "project store". Keep `project store` in code.\n'
    )
    found = measure(page, "descriptive", _PHRASES)
    assert [phrase for phrase, _ in found.retired] == ["out of the box", "project store"]


def test_over_budget_names_page_rule_and_first_offender() -> None:
    found = measure(_long(30) + " Then stop; go home now.", "descriptive", _PHRASES)
    budget = {"kind": "descriptive", "long_share": 0.0, "semicolons": 0, "long_paragraphs": 0}
    messages = check("docs/x.md", found, budget)
    assert len(messages) == 2
    assert all(m.startswith("docs/x.md: over budget on rule") for m in messages)
    assert "word word" in messages[0] and "Then stop; go home now." in messages[1]


def test_well_under_budget_fails_until_lowered() -> None:
    found = measure("A short clean sentence here.", "descriptive", _PHRASES)
    budget = {"kind": "descriptive", "long_share": 1.5, "semicolons": 2, "long_paragraphs": 0}
    messages = check("docs/x.md", found, budget)
    assert len(messages) == 1 and "Lower semicolons to 0" in messages[0]
    assert not check("docs/x.md", found, budget | {"semicolons": 0})


# --- Printing the measured table --------------------------------------------------------

_HEADER = """\
# SPDX-FileCopyrightText: 2026 The Tether Authors
# SPDX-License-Identifier: GPL-3.0-or-later
#
# The style budget of each nav page and README.md, checked by tests/test_docs_style_budget.py
# (#592). A budget may go down; raising one is a reviewed decision. The rules and how to read a
# failure: docs/maintainers/writing-style.md, "The style budget".
"""


def render(table: dict) -> str:
    out = [_HEADER, "[retired]", "phrases = ["]
    out += [f'    "{phrase}",' for phrase in table["retired"]["phrases"]]
    out.append("]\n")
    for page in nav_pages():
        found = measure_page(page, table)
        out.append(f'[pages."{page}"]')
        out.append(f'kind = "{found.kind}"')
        out += [f"{key} = {value}" for key, value in found.values().items()]
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    rendered = render(load_budget())
    if "--write" in sys.argv:
        BUDGET_FILE.write_text(rendered, encoding="utf-8", newline="\n")
    else:
        print(rendered, end="")

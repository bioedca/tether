# SPDX-FileCopyrightText: 2026 The Tether Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The AI-use policy for documentation work keeps its rules, its table and its links (#651).

``docs/maintainers/ai-use-policy.md`` gives the rules for a person who uses an AI tool to write
or review Tether documentation. Issue #651 fixed its shape: ten numbered rules, a table of safe
and unsafe practice with at least six pairs, a link from the "AI-assisted contributions" section
of ``CONTRIBUTING.md``, and a link from four rows of the self-assessment table in
``docs/ai-use.md``. These tests pin that shape, so that a later edit cannot drop a rule or a link
without a failing test:

* the page is in the ``Project & maintainers`` nav group;
* it has rules 1 to 10, in order, and rules 1, 3 and 8 point to the rule they do not restate;
* rule 4 says who reads, how much, where the result goes and what happens on a fail;
* its table has the ``Safe`` and ``Unsafe`` columns and at least six filled pairs;
* ``CONTRIBUTING.md`` links it from the AI section, and the four table rows link it.

Stdlib plus PyYAML (a mkdocs dependency in the base lock); it reads the files as text.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[1]
PAGE = "maintainers/ai-use-policy.md"
MAINTAINER_GROUP = "Project & maintainers"
CONTRIBUTING_AI = (
    "https://github.com/bioedca/tether/blob/main/CONTRIBUTING.md#ai-assisted-contributions"
)
_RULE_HEADING = re.compile(r"^### Rule (\d+)\. ", re.MULTILINE)
# The four rows of the self-assessment table in docs/ai-use.md that this policy answers.
_ROWS = (
    "Rules for model validation and quality assurance",
    "Examples of safe practice and unsafe practice",
    "The standard takes priority, and terminology stays under control",
    "An automated check of the style is not proof",
)


def _text() -> str:
    return (_REPO / "docs" / PAGE).read_text(encoding="utf-8")


def _pages(entries: object) -> list[str]:
    found: list[str] = []
    if isinstance(entries, list):
        for entry in entries:
            found += _pages(entry)
    elif isinstance(entries, dict):
        for value in entries.values():
            found += _pages(value)
    elif isinstance(entries, str):
        found.append(entries)
    return found


def _rule(number: int) -> str:
    """The text of one rule, from its heading to the next heading."""
    text = _text()
    start = text.index(f"### Rule {number}. ")
    rest = text[start + 1 :]
    end = min(i for i in (rest.find("\n### "), rest.find("\n## ")) if i != -1)
    return rest[:end]


def test_the_page_is_in_the_maintainer_nav_group() -> None:
    config = yaml.safe_load((_REPO / "mkdocs.yml").read_text(encoding="utf-8"))
    groups = {
        html.unescape(title): _pages(children)
        for entry in config["nav"]
        for title, children in entry.items()
    }
    assert PAGE in groups[MAINTAINER_GROUP]


def test_the_page_has_rules_one_to_ten_in_order() -> None:
    numbers = [int(n) for n in _RULE_HEADING.findall(_text())]
    assert numbers == list(range(1, 11)), numbers


def test_the_pointer_rules_point_to_the_rule_they_do_not_restate() -> None:
    assert CONTRIBUTING_AI in _rule(1)
    assert CONTRIBUTING_AI in _rule(3)
    assert "AGENTS.md#evidence-and-tool-routing" in _rule(3)
    assert "CONTRIBUTING.md#merging-prd-122-126" in _rule(8)
    for number in (1, 3, 8):
        assert "This page does not change" in _rule(number), number


def test_rule_four_says_who_reads_how_much_where_it_is_recorded_and_what_a_fail_does() -> None:
    rule = _rule(4)
    assert "The maintainer reads the full page" in rule
    assert "The pull request description records" in rule
    assert "If the sampled page fails, the pull request does not merge yet." in rule


def test_the_table_has_at_least_six_filled_safe_and_unsafe_pairs() -> None:
    section = _text().split("## Safe practice and unsafe practice", 1)[1]
    rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in section.splitlines()
        if line.startswith("| ")
    ]
    assert rows[0] == ["Safe", "Unsafe"], rows[0]
    pairs = [row for row in rows[1:] if not set("".join(row)) <= set("-: ")]
    assert len(pairs) >= 6, len(pairs)
    for safe, unsafe in pairs:
        assert safe and unsafe, (safe, unsafe)


def test_the_contributor_guide_links_the_policy_from_its_ai_section() -> None:
    guide = (_REPO / "CONTRIBUTING.md").read_text(encoding="utf-8")
    section = guide.split("## AI-assisted contributions", 1)[1].split("\n## ", 1)[0]
    assert "(docs/maintainers/ai-use-policy.md)" in section


def test_the_four_self_assessment_rows_link_the_policy() -> None:
    statement = (_REPO / "docs" / "ai-use.md").read_text(encoding="utf-8")
    lines = {
        line.split("|")[1].strip(): line for line in statement.splitlines() if line.startswith("| ")
    }
    for row in _ROWS:
        assert "(maintainers/ai-use-policy.md#" in lines[row], row

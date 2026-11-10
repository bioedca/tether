# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The AI-use statement stays reachable, and its self-assessment stays honest (#649).

``docs/ai-use.md`` tells a reader that AI tools draft the documentation and the source code. It
also compares the project with the STEMG white paper on ASD-STE100 and AI. The maintainer
decided on #649 where a reader sees the statement: the page, a footer line on every site page,
and one line in the README. These tests pin those three places and the rules the table follows,
so that a later edit cannot remove one without a failing test:

* the page is in the ``Project & maintainers`` nav group;
* the footer partial that links it is the one the theme loads, and its link is relative;
* the README links the page;
* every table row has one of the three statuses, and every row that is not ``Met`` links an
  issue;
* the page cites the white paper and says that Tether is not certified.

Stdlib plus PyYAML (a mkdocs dependency in the base lock); it reads the files as text.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[1]
PAGE = "ai-use.md"
MAINTAINER_GROUP = "Project & maintainers"
WHITE_PAPER = "https://www.asd-ste100.org/assets/files/WhitePaper-ASD-STE100_and_AI.pdf"
STATUSES = {"Met", "Partly", "Not yet"}
_ISSUE_LINK = re.compile(r"\[#\d+\]\(https://github\.com/bioedca/tether/issues/\d+\)")


def _config() -> dict:
    return yaml.safe_load((_REPO / "mkdocs.yml").read_text(encoding="utf-8"))


def _page_text() -> str:
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


def _assessment_rows() -> list[list[str]]:
    section = _page_text().split("## Self-assessment against the STEMG white paper", 1)[1]
    rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in section.splitlines()
        if line.startswith("| ")
    ]
    assert rows[0] == ["White-paper item", "Status", "Evidence or open work"], rows[0]
    return rows[1:]


def test_the_page_is_in_the_maintainer_nav_group() -> None:
    groups = {
        html.unescape(title): _pages(children)
        for entry in _config()["nav"]
        for title, children in entry.items()
    }
    assert PAGE in groups[MAINTAINER_GROUP]


def test_the_footer_of_every_page_links_the_statement() -> None:
    custom_dir = _config()["theme"]["custom_dir"]
    footer = (_REPO / custom_dir / "partials" / "copyright.html").read_text(encoding="utf-8")
    # The `url` filter makes the link relative to the page, so it resolves in every versioned
    # build. An absolute link would point at one published version only.
    assert "<a href=\"{{ 'ai-use/' | url }}\">" in footer
    # The override replaces the theme's partial, so it must keep the copyright line.
    assert "{{ config.copyright }}" in footer


def test_the_readme_links_the_statement() -> None:
    readme = (_REPO / "README.md").read_text(encoding="utf-8")
    assert "(docs/ai-use.md)" in readme


def test_every_row_has_a_known_status_and_open_rows_link_an_issue() -> None:
    rows = _assessment_rows()
    assert len(rows) >= 11, len(rows)
    for item, status, evidence in rows:
        assert status in STATUSES, (item, status)
        if status != "Met":
            assert _ISSUE_LINK.search(evidence), f"{item!r} is {status!r} but links no issue"


def test_the_page_cites_the_white_paper_and_claims_no_certification() -> None:
    text = _page_text()
    assert WHITE_PAPER in text
    assert "Tether is not certified to ASD-STE100." in text
    assert "is not an endorsement of the tool" in text
    assert "they do not endorse Tether" in text

# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""A person reads every user page before a release tag, and the release page says so (#650).

AI tools draft Tether's user pages, and the review lane of a pull request has only AI
reviewers. The maintainer decided on #650 that a named person reads every user page before each
release tag and records the read in the release checklist. These tests pin that step in
``docs/release.md`` so that a later edit cannot remove it without a failing test:

* the step is in "Cutting a release", before the tag step;
* it names a nav group that exists in ``mkdocs.yml``, and ``README.md``;
* it says that an AI tool cannot do it, what the reader checks, and how a changed page is read
  again;
* it names one place for the record, and that table has the record's fields;
* the self-assessment row in ``docs/ai-use.md`` is ``Met`` and links the step.

Stdlib plus PyYAML (a mkdocs dependency in the base lock); it reads the files as text.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[1]
RELEASE = _REPO / "docs" / "release.md"
AI_USE = _REPO / "docs" / "ai-use.md"
USER_GROUP = "Using Tether"
RECORD_HEADING = "### Record of user-page reads"
RECORD_COLUMNS = ["Release", "Date", "Reader", "Commit read", "Pages read"]
STEP_TITLE = "**Read every user page before the tag, and record the read.**"


def _release() -> str:
    return RELEASE.read_text(encoding="utf-8")


def _cutting_section() -> str:
    text = _release()
    start = text.index("## Cutting a release")
    end = text.index("\n### ", start)
    return text[start:end]


def _top_level_steps() -> dict[int, str]:
    """The numbered steps of "Cutting a release", keyed by number, each with its body."""
    section = _cutting_section()
    marks = list(re.finditer(r"^(\d+)\. ", section, flags=re.MULTILINE))
    steps: dict[int, str] = {}
    for index, mark in enumerate(marks):
        stop = marks[index + 1].start() if index + 1 < len(marks) else len(section)
        steps[int(mark.group(1))] = section[mark.start() : stop]
    return steps


def _read_step() -> tuple[int, str]:
    for number, body in _top_level_steps().items():
        if body.startswith(f"{number}. {STEP_TITLE}"):
            return number, body
    raise AssertionError("docs/release.md has no top-level step that reads the user pages")


def _tag_step_number() -> int:
    for number, body in _top_level_steps().items():
        if "signed, annotated** tag" in body:
            return number
    raise AssertionError("docs/release.md has no tag step")


def test_read_step_comes_before_the_tag_step() -> None:
    number, _ = _read_step()
    assert number < _tag_step_number()


def test_read_step_names_pages_that_exist() -> None:
    _, body = _read_step()
    flat = " ".join(body.split())
    assert f"`{USER_GROUP}` group of the `nav:` list in `mkdocs.yml`, and `README.md`" in flat
    config = yaml.safe_load((_REPO / "mkdocs.yml").read_text(encoding="utf-8"))
    groups = [next(iter(entry)) for entry in config["nav"] if isinstance(entry, dict)]
    assert USER_GROUP in groups, f"mkdocs.yml has no {USER_GROUP!r} nav group"
    assert (_REPO / "README.md").is_file()


def test_read_step_states_its_rules() -> None:
    _, body = _read_step()
    flat = " ".join(body.split())
    assert "A named person does this step. An AI tool cannot do this step." in flat
    assert "at the commit that you will tag" in flat
    assert "for the first time for `v1.0.0`" in flat
    assert "Each instruction is true of the application" in flat
    assert "Each warning comes before the step that it protects." in flat
    assert "(maintainers/writing-style.md)" in flat
    assert "Read each changed user page again." in flat
    assert "git diff --name-only <read-commit> HEAD -- README.md docs/ mkdocs.yml" in flat
    assert "Read each page that is new in the group." in flat


def test_record_has_one_place_and_its_fields() -> None:
    _, body = _read_step()
    flat = " ".join(body.split())
    assert "(#record-of-user-page-reads)" in flat
    assert "The table is the only place for the record." in flat
    text = _release()
    assert text.count(RECORD_HEADING) == 1
    record = text.split(RECORD_HEADING, 1)[1].split("\n#", 1)[0]
    header = next(line for line in record.splitlines() if line.startswith("|"))
    assert [cell.strip() for cell in header.strip("|").split("|")] == RECORD_COLUMNS


def test_step_references_follow_the_numbering() -> None:
    number, _ = _read_step()
    tag = _tag_step_number()
    text = _release()
    assert f"The two commands for step {tag}," in text
    flat = " ".join(text.split())
    assert f"before step {tag} of [Cutting a release](#cutting-a-release)" in flat
    assert f"Step {number} of [Cutting a release](#cutting-a-release) writes a row" in flat


def test_ai_use_row_is_met_and_links_the_step() -> None:
    number, _ = _read_step()
    rows = [
        line
        for line in AI_USE.read_text(encoding="utf-8").splitlines()
        if line.startswith("| AI assists, and human oversight stays |")
    ]
    assert len(rows) == 1
    cells = [cell.strip() for cell in rows[0].strip("|").split("|")]
    assert cells[1] == "Met"
    assert f"[step {number} of Cutting a release](release.md#cutting-a-release)" in cells[2]

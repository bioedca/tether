# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The architecture overview names exactly the packages under ``src/tether/`` (#610).

``docs/maintainers/architecture.md`` is the contributor's map of the code. Its first section,
"The parts", has one table row for each package. A map that leaves out a new package, or keeps
a removed one, is wrong in a way ``mkdocs build --strict`` cannot see, so this module checks the
table in both directions against the directories on disk:

* every package under ``src/tether/`` (a directory with an ``__init__.py``) has a row, and
* every row names a package that exists.

It also pins the shape that #610 accepted: the page is in the nav under
``Project & maintainers``, it has the seven sections in their order, the landing page no longer
carries the module table, and the diagram is a text fence that needs no third-party asset.

Stdlib and ``yaml`` only; nothing under ``tether`` is imported, so the default 3-OS matrix runs
it without the scientific stack.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

import pytest
import yaml

from _docs_source import split_front_matter

_REPO = Path(__file__).resolve().parents[1]
DOCS = _REPO / "docs"
PAGE = DOCS / "maintainers" / "architecture.md"
PACKAGE_ROOT = _REPO / "src" / "tether"
NAV_ENTRY = "maintainers/architecture.md"
MAINTAINER_GROUP = "Project & maintainers"

#: The seven sections #610 asked for, in order.
SECTIONS = (
    "The parts",
    "The three environments",
    "The project file",
    "How a movie becomes a result",
    "The invariants",
    "Where decisions are recorded",
    "Where the tests are",
)

#: A row of the parts table: its first cell is a backticked ``tether.<name>``, linked or not.
_ROW = re.compile(r"^\|\s*\[?`tether\.([A-Za-z_][A-Za-z0-9_]*)`")


def packages_on_disk(root: Path) -> set[str]:
    """The names of the packages directly under ``root``: directories with an ``__init__.py``."""
    return {p.name for p in root.iterdir() if p.is_dir() and (p / "__init__.py").is_file()}


def section(text: str, heading: str) -> str:
    """The body of the ``## heading`` section of ``text``, up to the next ``##`` heading."""
    match = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert match is not None, f"no '## {heading}' section"
    return match.group(1)


def packages_on_page(text: str) -> list[str]:
    """The package named by each row of the table in "The parts", in page order."""
    body = section(split_front_matter(text)[1], "The parts")
    return [m.group(1) for line in body.splitlines() if (m := _ROW.match(line))]


def mismatch(on_page: list[str], on_disk: set[str]) -> dict[str, list[str]]:
    """What the page and the disk disagree on: missing rows, stale rows and duplicate rows."""
    return {
        name: sorted(found)
        for name, found in (
            ("not on the page", on_disk - set(on_page)),
            ("not under src/tether", set(on_page) - on_disk),
            ("named twice", {p for p in on_page if on_page.count(p) > 1}),
        )
        if found
    }


def _page_text() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_the_page_names_every_package_and_only_those() -> None:
    on_disk = packages_on_disk(PACKAGE_ROOT)
    assert on_disk, "found no package under src/tether"
    assert mismatch(packages_on_page(_page_text()), on_disk) == {}, (
        "docs/maintainers/architecture.md 'The parts' must have one row for each package "
        f"under src/tether: {mismatch(packages_on_page(_page_text()), on_disk)}"
    )


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ("add", {"not on the page": ["newpkg"]}),
        ("remove", {"not under src/tether": ["io"]}),
    ],
)
def test_the_package_check_fails_when_a_package_changes_without_the_page(
    tmp_path: Path, change: str, expected: dict[str, list[str]]
) -> None:
    # A copy of the real package list, then one package added or removed on disk only.
    for name in packages_on_disk(PACKAGE_ROOT):
        (tmp_path / name).mkdir()
        (tmp_path / name / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "not_a_package").mkdir()  # a directory with no __init__.py is not a package
    if change == "add":
        (tmp_path / "newpkg").mkdir()
        (tmp_path / "newpkg" / "__init__.py").write_text("", encoding="utf-8")
    else:
        (tmp_path / "io" / "__init__.py").unlink()
    assert mismatch(packages_on_page(_page_text()), packages_on_disk(tmp_path)) == expected


def test_a_duplicate_row_is_reported() -> None:
    assert mismatch(["io", "io"], {"io"}) == {"named twice": ["io"]}


def test_the_page_is_in_the_maintainer_group_of_the_nav() -> None:
    nav = yaml.safe_load((_REPO / "mkdocs.yml").read_text(encoding="utf-8"))["nav"]
    groups = {html.unescape(next(iter(entry))): next(iter(entry.values())) for entry in nav}
    entries = [next(iter(item.values())) for item in groups[MAINTAINER_GROUP]]
    assert NAV_ENTRY in entries


def test_the_page_has_the_seven_sections_in_order() -> None:
    headings = re.findall(r"^## (.+)$", split_front_matter(_page_text())[1], re.M)
    assert tuple(headings) == SECTIONS


def test_the_landing_page_no_longer_carries_the_module_table() -> None:
    text = (DOCS / "index.md").read_text(encoding="utf-8")
    assert not re.search(r"^\|\s*\[?`tether\.", text, re.M), "docs/index.md has a module table"
    assert "PRD" not in text, "docs/index.md cites the product specification"
    assert NAV_ENTRY in text, "docs/index.md does not link the architecture overview"


def test_the_contributor_guide_and_the_adr_index_link_the_page() -> None:
    assert "docs/maintainers/architecture.md" in (_REPO / "CONTRIBUTING.md").read_text(
        encoding="utf-8"
    )
    assert "../maintainers/architecture.md" in (DOCS / "adr" / "README.md").read_text(
        encoding="utf-8"
    )


def test_the_diagram_is_a_text_fence_with_a_text_alternative() -> None:
    body = section(split_front_matter(_page_text())[1], "How a movie becomes a result")
    assert "```text" in body, "the data-flow diagram is not a text fence"
    assert not re.search(r"!\[|<img\b|<svg\b|https?://\S+\.(?:png|svg|js)", body)
    # The numbered list after the fence says the same path in words, for a screen reader.
    after = body.split("```text", 1)[1].split("```", 1)[1]
    assert re.search(r"^1\. ", after, re.M), "the diagram has no text alternative"

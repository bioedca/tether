# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The exports reference documents exactly the CSV columns the code writes.

``tether.project.export.MOLECULE_TABLE_COLUMNS`` is annotated in the source as frozen —
"a reader may key on these names" — and ``docs/reference/exports.md`` is the page that
tells that reader what each name means. This module is the drift guard between them: the
page's column table must list exactly the tuple's names, in the tuple's order. Where the
two disagree, **the tuple is right** and the page is stale.

Dependency-free by design (the constraint on issue #160): ``python-markdown`` is not in
the base 3-OS test environment, so the table is parsed structurally — split each ``|``
row, take its first cell — rather than rendered.
"""

from __future__ import annotations

from pathlib import Path

from tether.project.export import MOLECULE_TABLE_COLUMNS

PAGE = Path(__file__).resolve().parents[1] / "docs" / "reference" / "exports.md"

#: The heading the column table lives under. Renaming it in the page means updating it
#: here — deliberately: the guard must never silently stop finding the table.
_HEADING = "### Molecule-table columns"


def _column_table_rows() -> list[str]:
    """First cells of the data rows of the column table, backticks stripped.

    Finds the first Markdown table after :data:`_HEADING`, drops its header and
    ``|---|`` separator rows, and returns one string per remaining row.
    """
    lines = PAGE.read_text(encoding="utf-8").splitlines()
    try:
        start = lines.index(_HEADING)
    except ValueError:  # pragma: no cover - defensive; the assert below reports it
        raise AssertionError(f"heading {_HEADING!r} not found in {PAGE}") from None

    table: list[str] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("|"):
            table.append(stripped)
        elif table:
            break  # the table ended at the first non-row line after it started

    assert len(table) >= 3, f"no column table found under {_HEADING!r} in {PAGE}"

    cells = []
    for row in table[2:]:  # skip the header row and the |---| separator
        first = row.strip("|").split("|")[0]
        cells.append(first.strip().strip("`").strip())
    return cells


def test_page_exists_and_is_in_the_nav() -> None:
    """A page absent from the nav fails ``mkdocs build --strict`` on ``omitted_files``."""
    assert PAGE.is_file(), f"{PAGE} is missing"
    nav = (PAGE.parents[2] / "mkdocs.yml").read_text(encoding="utf-8")
    assert "reference/exports.md" in nav


def test_documented_columns_match_the_frozen_tuple_exactly() -> None:
    """Element-for-element, in order — a rename, addition, removal or reorder fails."""
    assert _column_table_rows() == list(MOLECULE_TABLE_COLUMNS)


def test_column_table_row_count() -> None:
    """The table has one data row per column and no stragglers."""
    assert len(_column_table_rows()) == len(MOLECULE_TABLE_COLUMNS)

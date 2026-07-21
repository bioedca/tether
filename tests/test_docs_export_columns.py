# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The exports reference documents exactly the CSV columns the code writes.

``tether.project.export.MOLECULE_TABLE_COLUMNS`` is annotated in the source as frozen —
"a reader may key on these names" — and ``docs/reference/exports.md`` is the page that
tells that reader what each name means. This module is the drift guard between them: the
page's column table must list exactly the tuple's names, in the tuple's order. Where the
two disagree, **the tuple is right** and the page is stale.

Dependency-free by design (the constraint on issue #160), on *both* sides. Neither
``python-markdown`` (not in the base 3-OS test environment) nor the scientific stack is
imported: the page's table is parsed structurally — split each ``|`` row, take its first
cell — and the tuple is read out of ``src/tether/project/export.py`` with :mod:`ast`
rather than by importing it. Importing ``tether.project.export`` would pull in
``numpy``/``scipy``/``h5py`` through :mod:`tether.imaging.extract`, which is why every
scientific test in this suite opens with ``pytest.importorskip``; a documentation check
should not need that environment at all.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

PAGE = _REPO / "docs" / "reference" / "exports.md"

#: The module the frozen tuple lives in, read as text — never imported (see the module
#: docstring). This is the repo checkout, which is also what an editable install exposes.
EXPORT_SOURCE = _REPO / "src" / "tether" / "project" / "export.py"

#: The heading the column table lives under. Renaming it in the page means updating it
#: here — deliberately: the guard must never silently stop finding the table.
_HEADING = "### Molecule-table columns"

#: The frozen tuple's name in :data:`EXPORT_SOURCE`.
_TUPLE_NAME = "MOLECULE_TABLE_COLUMNS"


def _frozen_columns() -> list[str]:
    """The ``MOLECULE_TABLE_COLUMNS`` string literals, in source order.

    Parses the module instead of importing it, so this docs guard runs in an
    interpreter that has no scientific stack installed.
    """
    tree = ast.parse(EXPORT_SOURCE.read_text(encoding="utf-8"), filename=str(EXPORT_SOURCE))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
        elif isinstance(node, ast.Assign):
            targets = node.targets
        else:
            continue
        named = any(isinstance(t, ast.Name) and t.id == _TUPLE_NAME for t in targets)
        if not named or node.value is None:
            continue
        assert isinstance(node.value, ast.Tuple), (
            f"{_TUPLE_NAME} in {EXPORT_SOURCE} is no longer a tuple literal"
        )
        return [ast.literal_eval(element) for element in node.value.elts]
    raise AssertionError(f"module-level {_TUPLE_NAME} not found in {EXPORT_SOURCE}")


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
    nav = (_REPO / "mkdocs.yml").read_text(encoding="utf-8")
    assert "reference/exports.md" in nav


def test_frozen_tuple_is_parseable() -> None:
    """The ast read is the guard's other half — a silent parse miss must fail loudly."""
    columns = _frozen_columns()
    assert columns, f"{_TUPLE_NAME} parsed as empty from {EXPORT_SOURCE}"
    assert all(isinstance(name, str) for name in columns)
    assert columns[0] == "molecule_id"


def test_documented_columns_match_the_frozen_tuple_exactly() -> None:
    """Element-for-element, in order — a rename, addition, removal or reorder fails."""
    assert _column_table_rows() == _frozen_columns()


def test_column_table_row_count() -> None:
    """The table has one data row per column and no stragglers."""
    assert len(_column_table_rows()) == len(_frozen_columns())

# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The exports reference documents exactly the CSV columns and semantics code writes.

``tether.project.export.MOLECULE_TABLE_COLUMNS`` is annotated in the source as frozen —
"a reader may key on these names" — and ``docs/reference/exports.md`` is the page that
tells that reader what each name means. This module is the drift guard between them:
the page's column table must list exactly the tuple's names, in the tuple's order, and
its CSV types must follow ``MOLECULES_DTYPE`` except for explicitly enumerated
transform/derived fields. Selected load-bearing unit/domain and blankness claims are
also pinned here and exercised behaviorally in ``test_export_tables.py``. Where code
and reference disagree, **the code is right** and the page is stale.

Dependency-free by design (the constraint on issue #160), on *both* sides. Neither
``python-markdown`` (not in the base 3-OS test environment) nor the scientific stack is
imported: all four cells of each Markdown row are parsed structurally, while the tuple
and dtype declarations are read with :mod:`ast` rather than imported. Importing
``tether.project.export`` would pull in ``numpy``/``scipy``/``h5py`` through
:mod:`tether.imaging.extract`, which is why every scientific test in this suite opens
with ``pytest.importorskip``; a documentation check should not need that environment.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

PAGE = _REPO / "docs" / "reference" / "exports.md"

#: The module the frozen tuple lives in, read as text — never imported (see the module
#: docstring). This is the repo checkout, which is also what an editable install exposes.
EXPORT_SOURCE = _REPO / "src" / "tether" / "project" / "export.py"

#: The frozen molecule dtype, parsed as source so this guard stays dependency-free.
SCHEMA_SOURCE = _REPO / "src" / "tether" / "io" / "schema.py"

#: The heading the column table lives under. Renaming it in the page means updating it
#: here — deliberately: the guard must never silently stop finding the table.
_HEADING = "### Molecule-table columns"

#: The frozen tuple's name in :data:`EXPORT_SOURCE`.
_TUPLE_NAME = "MOLECULE_TABLE_COLUMNS"

#: The dtype declaration's name in :data:`SCHEMA_SOURCE`.
_DTYPE_NAME = "MOLECULES_DTYPE"

_TABLE_HEADER = ("Column", "CSV type", "Unit / domain", "Blank when")

# These columns are not direct scalar field copies. Enumerating every exception makes a
# new transform/derived export fail until its CSV representation is reviewed explicitly.
_CSV_TYPE_EXCEPTIONS = {
    "curation_label": ("string", "stored integer transformed to vocabulary text"),
    "donor_bleach_frame": ("integer", "first component of bleach_frames"),
    "acceptor_bleach_frame": ("integer", "second component of bleach_frames"),
    "frame_start": ("integer", "first component of frame_range"),
    "frame_end": ("integer", "second component of frame_range"),
    "window_start": ("integer", "resolved analysis-window lower bound"),
    "window_end": ("integer", "resolved analysis-window upper bound"),
    "n_finite_frames": ("integer", "derived finite apparent-E count"),
    "mean_apparent_e": ("float", "derived apparent-E mean"),
    "median_apparent_e": ("float", "derived apparent-E median"),
}

# These patterns intentionally pin meaning rather than every word of explanatory prose.
# Their matching behavior is covered by a deliberate-mismatch regression below.
_SEMANTIC_CELL_REQUIREMENTS = (
    ("mean_apparent_e", "Unit / domain", r"\*\*Not\*\* \u03b3-corrected"),
    ("median_apparent_e", "Unit / domain", r"\*\*Dimensionless .* apparent E\*\*"),
    ("frame_start", "Unit / domain", r"\*\*Frames\*\*, zero-based, \*\*inclusive\*\*"),
    (
        "frame_end",
        "Unit / domain",
        r"\*\*Frames\*\*, zero-based, \*\*exclusive\*\* \(half-open\)",
    ),
    ("window_start", "Unit / domain", r"\*\*Frames\*\*, zero-based, \*\*inclusive\*\*"),
    ("window_end", "Unit / domain", r"\*\*exclusive\*\*"),
    ("quality_class", "Blank when", r"`NaN`.*blank in every export"),
    ("aperture_id", "Blank when", r"^Never\b.*integer is always written$"),
    ("delta", "Unit / domain", r"`0\.0`.*not blank"),
    ("delta", "Blank when", r"stored value is non-finite"),
)


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


def _molecules_dtype_csv_types(field_names: set[str]) -> dict[str, str]:
    """Scalar CSV types implied by selected ``MOLECULES_DTYPE`` fields."""
    tree = ast.parse(SCHEMA_SOURCE.read_text(encoding="utf-8"), filename=str(SCHEMA_SOURCE))
    declaration: ast.AST | None = None
    for node in tree.body:
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
        elif isinstance(node, ast.Assign):
            targets = node.targets
        else:
            continue
        if any(isinstance(t, ast.Name) and t.id == _DTYPE_NAME for t in targets):
            declaration = node.value
            break

    assert isinstance(declaration, ast.Call), (
        f"module-level {_DTYPE_NAME} dtype call not found in {SCHEMA_SOURCE}"
    )
    assert declaration.args and isinstance(declaration.args[0], ast.List), (
        f"{_DTYPE_NAME} in {SCHEMA_SOURCE} no longer has a literal field list"
    )

    csv_types: dict[str, str] = {}
    for field in declaration.args[0].elts:
        assert isinstance(field, ast.Tuple) and len(field.elts) >= 2
        name = ast.literal_eval(field.elts[0])
        if name not in field_names:
            continue
        dtype = field.elts[1]
        if (
            isinstance(dtype, ast.Call)
            and isinstance(dtype.func, ast.Name)
            and dtype.func.id == "_str"
        ):
            csv_type = "string"
        else:
            code = ast.literal_eval(dtype)
            assert isinstance(code, str) and code
            kind = code.lstrip("<>=|")[0]
            csv_type = {"i": "integer", "u": "integer", "f": "float"}.get(kind)
            assert csv_type is not None, (
                f"unsupported dtype {code!r} for {name!r} in {_DTYPE_NAME}"
            )
        assert name not in csv_types, f"duplicate field {name!r} in {_DTYPE_NAME}"
        csv_types[name] = csv_type
    return csv_types


def _markdown_row_cells(row: str) -> tuple[str, ...]:
    """Split one pipe-table row without treating an escaped ``\\|`` as a delimiter."""
    stripped = row.strip()
    assert stripped.startswith("|") and stripped.endswith("|"), (
        f"not a complete Markdown table row: {row!r}"
    )
    return tuple(cell.strip() for cell in re.split(r"(?<!\\)\|", stripped[1:-1]))


def _column_table_rows(page_text: str | None = None) -> list[tuple[str, str, str, str]]:
    """All four cells of every data row, with column-name backticks stripped.

    Finds the first Markdown table after :data:`_HEADING`, drops its header and
    ``|---|`` separator rows, and returns one four-cell tuple per remaining row.
    """
    text = PAGE.read_text(encoding="utf-8") if page_text is None else page_text
    lines = text.splitlines()
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

    header = _markdown_row_cells(table[0])
    separator = _markdown_row_cells(table[1])
    assert header == _TABLE_HEADER, f"unexpected column-table header: {header!r}"
    assert len(separator) == len(_TABLE_HEADER) and all(
        re.fullmatch(r":?-{3,}:?", cell) for cell in separator
    ), f"unexpected column-table separator: {separator!r}"

    rows: list[tuple[str, str, str, str]] = []
    for row in table[2:]:
        cells = _markdown_row_cells(row)
        assert len(cells) == len(_TABLE_HEADER), (
            f"expected four cells in column-table row, got {len(cells)}: {row!r}"
        )
        normalized = (cells[0].strip("`").strip(), *cells[1:])
        rows.append(normalized)
    return rows


def _assert_load_bearing_semantic_cells(page_text: str | None = None) -> None:
    """Assert selected unit/domain and blankness cells retain their promised meaning."""
    rows = {row[0]: row for row in _column_table_rows(page_text)}
    for column, cell_name, pattern in _SEMANTIC_CELL_REQUIREMENTS:
        cell = rows[column][_TABLE_HEADER.index(cell_name)]
        assert re.search(pattern, cell), (
            f"{column!r} {cell_name!r} no longer matches {pattern!r}: {cell!r}"
        )


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
    assert [row[0] for row in _column_table_rows()] == _frozen_columns()


def test_column_table_row_count() -> None:
    """The table has one data row per column and no stragglers."""
    assert len(_column_table_rows()) == len(_frozen_columns())


def test_column_table_parses_all_four_cells() -> None:
    """Every row has the complete public contract, including escaped Markdown pipes."""
    rows = _column_table_rows()
    assert all(len(row) == len(_TABLE_HEADER) for row in rows)
    assert r"\|" in rows[1][2]  # molecule_key formula contains an escaped pipe


def test_documented_csv_types_match_dtype_or_explicit_exception() -> None:
    """Direct fields follow ``MOLECULES_DTYPE``; transforms are reviewed explicitly."""
    rows = _column_table_rows()
    documented_names = {row[0] for row in rows}
    assert set(_CSV_TYPE_EXCEPTIONS) <= documented_names
    direct_names = documented_names - set(_CSV_TYPE_EXCEPTIONS)
    schema_types = _molecules_dtype_csv_types(direct_names)
    assert set(schema_types) == direct_names, (
        f"direct CSV fields missing from {_DTYPE_NAME}: {sorted(direct_names - set(schema_types))}"
    )

    for name, csv_type, _unit_domain, _blank_when in rows:
        if name in _CSV_TYPE_EXCEPTIONS:
            expected, _reason = _CSV_TYPE_EXCEPTIONS[name]
        else:
            assert name in schema_types, (
                f"{name!r} is not a direct {_DTYPE_NAME} field; enumerate its transform"
            )
            expected = schema_types[name]
        assert csv_type == expected, (
            f"{name!r} documents CSV type {csv_type!r}; expected {expected!r}"
        )


def test_load_bearing_semantic_cells_are_pinned() -> None:
    """Scientific/indexing/blankness promises cannot drift as unchecked prose."""
    _assert_load_bearing_semantic_cells()


def test_deliberate_semantic_cell_mismatch_fails_guard() -> None:
    """Prove the guard rejects a publishably wrong apparent-E semantic cell."""
    original = PAGE.read_text(encoding="utf-8")
    not_gamma_corrected = "**Not** \N{GREEK SMALL LETTER GAMMA}-corrected"
    changed = original.replace(not_gamma_corrected, "gamma-corrected", 1)
    assert changed != original, "test mutation target disappeared from the reference page"

    try:
        _assert_load_bearing_semantic_cells(changed)
    except AssertionError as exc:
        assert "mean_apparent_e" in str(exc)
    else:  # pragma: no cover - this is the failure the regression exists to expose
        raise AssertionError("semantic-cell mismatch unexpectedly passed the guard")

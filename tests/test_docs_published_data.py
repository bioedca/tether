# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The data files the docs site serves for a deposit agree with the code and the pages (#612).

Three files are published so that a reader can keep a machine-readable description of their data
beside it: the project schema (``schema/schema_frozen.json``, served by a build hook as
``reference/tether-schema.json``), the molecule-table column dictionary
(``docs/reference/molecule-table-columns.csv``) and the provenance JSON Schema
(``docs/reference/tether-provenance-schema.json``). This module checks that

* the dictionary lists exactly ``MOLECULE_TABLE_COLUMNS``, in order;
* the dictionary and the column table of ``docs/reference/exports.md`` cannot disagree on a
  column's type, unit or blankness;
* the hook serves the schema unchanged, and the pages link all three files;
* the three files are CC0-1.0 in ``REUSE.toml``, as the maintainer decided on the issue.

Stdlib-only, so it runs in the base 3-OS matrix: the column tuple and the page table are read by
the parsers of ``test_docs_export_columns.py``, which never import the scientific stack. A real
sidecar is validated against the provenance schema in ``test_provenance_schema.py``. The built-site
test runs only where ``TETHER_BUILT_SITE`` names a built site, which the ``docs-build`` job sets.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import os
import sys
import tomllib
import types
from pathlib import Path

import pytest

from test_docs_export_columns import _TABLE_HEADER, _column_table_rows, _frozen_columns

_REPO = Path(__file__).resolve().parents[1]
REFERENCE = _REPO / "docs" / "reference"
DICTIONARY = REFERENCE / "molecule-table-columns.csv"
PROVENANCE_SCHEMA = REFERENCE / "tether-provenance-schema.json"
PROJECT_SCHEMA = _REPO / "schema" / "schema_frozen.json"
HOOKS = _REPO / "scripts" / "mkdocs_hooks.py"

#: The dictionary's header, in order. A reader's tooling may key on these names.
DICTIONARY_HEADER = ("name", "type", "unit", "can_be_blank", "description")

#: The unit vocabulary. ``frames`` and ``dimensionless`` must match the page's bold unit lead;
#: ``none`` marks identifiers and text, ``unspecified`` a number whose range nothing defines.
_UNITS = {"none", "dimensionless", "frames", "unspecified"}

#: The three published files and the license the maintainer chose for them.
_CC0_PATHS = {
    "schema/schema_frozen.json",
    "docs/reference/molecule-table-columns.csv",
    "docs/reference/tether-provenance-schema.json",
}

_spec = importlib.util.spec_from_file_location("mkdocs_hooks_published", HOOKS)
assert _spec is not None and _spec.loader is not None
hooks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hooks)


def _dictionary(text: str | None = None) -> list[dict[str, str]]:
    """The dictionary rows, after checking the header."""
    raw = DICTIONARY.read_text(encoding="utf-8") if text is None else text
    reader = csv.DictReader(raw.splitlines())
    assert tuple(reader.fieldnames or ()) == DICTIONARY_HEADER, reader.fieldnames
    return list(reader)


def _page_unit(cell: str) -> str | None:
    """The unit the page's ``Unit / domain`` cell leads with, if it names one."""
    if cell.startswith("**Frames**"):
        return "frames"
    if cell.startswith(("**Dimensionless", "**Unitless")):
        return "dimensionless"
    return None


def _disagreements(rows: list[dict[str, str]], page_rows: list[tuple[str, ...]]) -> list[str]:
    """Every fact on which the dictionary and the page's column table differ."""
    problems = []
    names = [row["name"] for row in rows]
    page_names = [page_row[0] for page_row in page_rows]
    if names != page_names:
        return [f"column names differ: dictionary {names} != page {page_names}"]
    type_cell = _TABLE_HEADER.index("CSV type")
    unit_cell = _TABLE_HEADER.index("Unit / domain")
    blank_cell = _TABLE_HEADER.index("Blank when")
    for row, page_row in zip(rows, page_rows, strict=True):
        name = row["name"]
        if row["type"] != page_row[type_cell]:
            problems.append(f"{name}: type {row['type']!r} != page {page_row[type_cell]!r}")
        page_unit = _page_unit(page_row[unit_cell])
        if (row["unit"] if row["unit"] in {"frames", "dimensionless"} else None) != page_unit:
            problems.append(f"{name}: unit {row['unit']!r} != page lead {page_unit!r}")
        page_blank = "no" if page_row[blank_cell].startswith("Never") else "yes"
        if row["can_be_blank"] != page_blank:
            problems.append(f"{name}: can_be_blank {row['can_be_blank']!r} != page {page_blank!r}")
    return problems


# --------------------------------------------------------------------------- #
# The column dictionary                                                        #
# --------------------------------------------------------------------------- #


def test_dictionary_lists_the_frozen_columns_in_order() -> None:
    assert [row["name"] for row in _dictionary()] == _frozen_columns()


def test_dictionary_cells_use_the_stated_vocabulary() -> None:
    for row in _dictionary():
        assert row["type"] in {"string", "integer", "float"}, row
        assert row["unit"] in _UNITS, row
        assert row["can_be_blank"] in {"yes", "no"}, row
        assert row["description"].strip(), row


def test_dictionary_and_page_table_agree() -> None:
    assert _disagreements(_dictionary(), _column_table_rows()) == []


def test_a_dictionary_that_disagrees_with_the_page_is_reported() -> None:
    """The comparison is shown firing on each fact it compares, or it proves nothing."""
    page_rows = _column_table_rows()
    for field, wrong in (("type", "float"), ("unit", "frames"), ("can_be_blank", "no")):
        rows = _dictionary()
        target = next(row for row in rows if row["name"] == "molecule_id")
        target[field] = wrong
        problems = _disagreements(rows, page_rows)
        assert any(p.startswith("molecule_id:") for p in problems), (field, problems)
    rows = _dictionary()
    rows[0], rows[1] = rows[1], rows[0]
    assert _disagreements(rows, page_rows), "a reorder must be reported"


# --------------------------------------------------------------------------- #
# The build hook and the links                                                 #
# --------------------------------------------------------------------------- #


class _FakeFile:
    """Stands in for ``mkdocs.structure.files.File``, which the base matrix does not install."""

    def __init__(self, src_uri: str, abs_src_path: str) -> None:
        self.src_uri = src_uri
        self.abs_src_path = abs_src_path

    @classmethod
    def generated(cls, config: object, src_uri: str, *, abs_src_path: str) -> _FakeFile:
        return cls(src_uri, abs_src_path)


@pytest.fixture
def fake_mkdocs(monkeypatch: pytest.MonkeyPatch) -> None:
    names = ("mkdocs", "mkdocs.structure", "mkdocs.structure.files")
    for name in names:
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    sys.modules["mkdocs.structure.files"].File = _FakeFile  # type: ignore[attr-defined]


def test_hook_serves_the_frozen_schema_as_reference_json(fake_mkdocs: None) -> None:
    assert hooks.PUBLISHED_FILES == {"reference/tether-schema.json": PROJECT_SCHEMA}
    files = hooks.on_files([], config=object())
    assert [(f.src_uri, Path(f.abs_src_path)) for f in files] == [
        ("reference/tether-schema.json", PROJECT_SCHEMA)
    ]


def test_hook_stops_the_build_when_the_schema_is_missing(
    fake_mkdocs: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(hooks, "PUBLISHED_FILES", {"reference/x.json": tmp_path / "absent.json"})
    with pytest.raises(FileNotFoundError, match="reference/x.json"):
        hooks.on_files([], config=object())


def test_the_pages_link_the_published_files() -> None:
    fmt = (REFERENCE / "tether-format.md").read_text(encoding="utf-8")
    exports = (REFERENCE / "exports.md").read_text(encoding="utf-8")
    assert "](tether-schema.json)" in fmt
    for name in (
        "tether-schema.json",
        "molecule-table-columns.csv",
        "tether-provenance-schema.json",
    ):
        assert f"]({name})" in exports, name
    assert "## Put these files in your deposit" in exports
    assert "CC0-1.0" in exports


def test_provenance_schema_is_a_draft_2020_12_schema() -> None:
    schema = json.loads(PROVENANCE_SCHEMA.read_text(encoding="utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.Draft202012Validator.check_schema(schema)


def test_built_site_serves_the_three_files() -> None:
    named = os.environ.get("TETHER_BUILT_SITE")
    if not named:
        pytest.skip("TETHER_BUILT_SITE is not set; the docs-build job sets it after the build")
    site = Path(named) / "reference"
    assert (site / "tether-schema.json").read_bytes() == PROJECT_SCHEMA.read_bytes()
    assert (site / "molecule-table-columns.csv").read_bytes() == DICTIONARY.read_bytes()
    assert (site / "tether-provenance-schema.json").read_bytes() == PROVENANCE_SCHEMA.read_bytes()


# --------------------------------------------------------------------------- #
# The license                                                                  #
# --------------------------------------------------------------------------- #


def test_the_three_files_are_cc0_in_reuse() -> None:
    config = tomllib.loads((_REPO / "REUSE.toml").read_text(encoding="utf-8"))
    entries = [
        a
        for a in config["annotations"]
        if _CC0_PATHS & set([a["path"]] if isinstance(a["path"], str) else a["path"])
    ]
    assert len(entries) == 1, entries
    (entry,) = entries
    assert set(entry["path"]) == _CC0_PATHS
    assert entry["SPDX-License-Identifier"] == "CC0-1.0"
    assert entry["precedence"] == "override"
    assert (_REPO / "LICENSES" / "CC0-1.0.txt").is_file()

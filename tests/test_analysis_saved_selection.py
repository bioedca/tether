# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The saved selection: the one-project series of the plot builder (#373, ADR-0124).

A [`SavedSelection`][tether.analysis.saved_selection.SavedSelection] is
``(name, project_path, molecule_keys, filter)``, built from a ``query_molecules`` result,
Qt-free, frozen when it is made and serializable to JSON. The presets split a query by
the result's own ``by_condition`` / ``by_movie`` / ``by_source_file``. A selection is a
[`MoleculeSelection`][tether.analysis.selection.MoleculeSelection], so it drives any
population function, and it names no molecule in another project. No ``QApplication``
exists anywhere in this module.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("h5py")
pytest.importorskip("numpy")

import h5py  # noqa: E402
import numpy as np  # noqa: E402

import tether.analysis as ta  # noqa: E402
from _analysis_store import MEANS, build_store_with_model  # noqa: E402
from tether.analysis.saved_selection import (  # noqa: E402
    PRESET_GROUPINGS,
    SAVED_SELECTION_VERSION,
    SavedSelection,
    SelectionFilter,
    preset_selections,
    select_molecules,
)
from tether.analysis.selection import MoleculeSelection, resolve_selection  # noqa: E402
from tether.io.filename import parse_filename  # noqa: E402
from tether.io.schema import MOLECULES_DTYPE, TABLE, create_project  # noqa: E402
from tether.project import Project  # noqa: E402

_FILE_A10 = "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif"
_FILE_A11 = "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_011.tif"
_FILE_B = "Bla_UCKOPSB_Tbox_35pM_tRNA_600nM_010.tif"
_CID_A = parse_filename(_FILE_A10).condition_id
_CID_B = parse_filename(_FILE_B).condition_id


def _seed(tmp_path: Path, specs: list[dict[str, Any]], *, name: str = "exp.tether") -> Path:
    """A post-extraction ``.tether`` holding only ``/molecules`` rows (as test_analysis_query)."""
    path = create_project(tmp_path / name)
    rows = np.zeros(len(specs), dtype=MOLECULES_DTYPE)
    for field in MOLECULES_DTYPE.names:
        if MOLECULES_DTYPE[field].kind == "O":
            rows[field] = ""
    for i, spec in enumerate(specs):
        rows["molecule_id"][i] = f"mol-{i}"
        rows["molecule_key"][i] = spec["key"]
        rows["source_filename"][i] = spec["src"]
        rows["movie_id"][i] = spec["movie"]
        rows["category"][i] = spec.get("category", "")
        rows["tags"][i] = ",".join(spec.get("tags", ()))
        rows["condition_id"][i] = parse_filename(spec["src"]).condition_id
    with h5py.File(path, "r+") as f:
        table = f["molecules"][TABLE]
        table.resize((len(specs),))
        table[:] = rows
    return path


def _project(tmp_path: Path, *, name: str = "exp.tether") -> Path:
    """Condition A over two files (k0, k1 in m10; k2 in m11) and condition B (k3 in mB)."""
    return _seed(
        tmp_path,
        [
            {"key": "k0", "src": _FILE_A10, "movie": "m10", "tags": ("blink",)},
            {"key": "k1", "src": _FILE_A10, "movie": "m10"},
            {"key": "k2", "src": _FILE_A11, "movie": "m11", "tags": ("blink",)},
            {"key": "k3", "src": _FILE_B, "movie": "mB"},
        ],
        name=name,
    )


# --- construction from a query ------------------------------------------------


def test_select_molecules_freezes_a_query_and_records_its_filter(tmp_path: Path) -> None:
    path = _project(tmp_path)
    sel = select_molecules(path, name="A, blinking", condition_ids=[_CID_A], tags=["blink"])

    assert sel.name == "A, blinking"
    assert sel.molecule_keys == ("k0", "k2")
    assert sel.n_molecules == 2
    assert Path(sel.project_path) == path.resolve()
    assert sel.filter == SelectionFilter.of(condition_ids=[_CID_A], tags=["blink"])
    assert sel.filter.group is None
    # The recorded filter reproduces the membership it produced.
    assert ta.query_molecules(path, **sel.filter.query_kwargs()).molecule_keys == sel.molecule_keys
    assert sel.filter.run(path) == sel.molecule_keys


def test_select_molecules_accepts_an_open_project(tmp_path: Path) -> None:
    path = _project(tmp_path)
    sel = select_molecules(Project.open(path), name="all")
    assert sel.molecule_keys == ("k0", "k1", "k2", "k3")
    assert sel.is_in(path)


def test_membership_is_frozen_against_later_project_edits(tmp_path: Path) -> None:
    path = _project(tmp_path)
    sel = select_molecules(path, name="blinking", tags=["blink"])
    with h5py.File(path, "r+") as f:  # tag k1 after the selection was made
        table = f["molecules"][TABLE]
        rows = table[:]
        rows["tags"][1] = "blink"
        table[:] = rows
    assert ta.query_molecules(path, tags=["blink"]).molecule_keys == ("k0", "k1", "k2")
    assert sel.molecule_keys_in(Project.open(path)) == ("k0", "k2")


def test_selection_filter_is_normalized_and_rejects_what_it_cannot_save() -> None:
    assert SelectionFilter.of(tags=["b", "a", "a"]) == SelectionFilter.of(tags=("a", "b"))
    assert SelectionFilter.of(key={"ligand": "tRNA", "conc": 600}).key == (
        ("conc", 600),
        ("ligand", "tRNA"),
    )
    with pytest.raises(TypeError, match="bare str"):
        SelectionFilter.of(tags="blink")
    with pytest.raises(TypeError, match="key"):
        SelectionFilter.of(key={"ligand": object()})


def test_a_saved_selection_needs_a_name_and_iterable_keys() -> None:
    with pytest.raises(ValueError, match="name"):
        SavedSelection("  ", "/p.tether", ("k0",))
    with pytest.raises(TypeError, match="bare string"):
        SavedSelection("x", "/p.tether", "k0")  # type: ignore[arg-type]


# --- presets: the query result's own grouping ---------------------------------


@pytest.mark.parametrize(
    ("by", "method"),
    [("condition", "by_condition"), ("movie", "by_movie"), ("source_file", "by_source_file")],
)
def test_presets_split_by_the_query_results_own_grouping(
    tmp_path: Path, by: str, method: str
) -> None:
    path = _project(tmp_path)
    presets = preset_selections(path, by)
    expected = getattr(ta.query_molecules(path), method)()

    assert {p.name: p.molecule_keys for p in presets} == expected
    assert [p.name for p in presets] == list(expected)  # first-seen order
    assert all(p.filter.group == (by, p.name) for p in presets)
    # Running a preset's whole filter again gives back that preset, not the base query.
    assert all(p.filter.run(path) == p.molecule_keys for p in presets)


def test_presets_apply_the_filter_before_grouping(tmp_path: Path) -> None:
    path = _project(tmp_path)
    presets = preset_selections(path, "movie", condition_ids=[_CID_A])
    assert {p.name: p.molecule_keys for p in presets} == {"m10": ("k0", "k1"), "m11": ("k2",)}
    assert all(p.filter.condition_ids == (_CID_A,) for p in presets)


def test_presets_name_the_groupings_they_support(tmp_path: Path) -> None:
    assert PRESET_GROUPINGS == ("condition", "movie", "source_file")
    with pytest.raises(ValueError, match="grouping"):
        preset_selections(_project(tmp_path), "tag")


# --- serialization: a composition survives a restart --------------------------


def test_round_trips_through_json(tmp_path: Path) -> None:
    path = _project(tmp_path)
    for sel in (
        select_molecules(
            path, name="A", condition_ids=[_CID_A], key={"ligand": "tRNA"}, match_all_tags=False
        ),
        *preset_selections(path, "source_file"),
    ):
        data = json.loads(json.dumps(sel.to_dict()))
        assert data["version"] == SAVED_SELECTION_VERSION
        assert SavedSelection.from_dict(data) == sel


def test_from_dict_refuses_another_version_a_missing_field_or_an_unknown_group() -> None:
    good = SavedSelection("x", "/p.tether", ("k0",)).to_dict()
    with pytest.raises(ValueError, match="version"):
        SavedSelection.from_dict({**good, "version": SAVED_SELECTION_VERSION + 1})
    with pytest.raises(ValueError, match="molecule_keys"):
        SavedSelection.from_dict({k: v for k, v in good.items() if k != "molecule_keys"})
    with pytest.raises(ValueError, match="grouping"):
        SavedSelection.from_dict({**good, "filter": {**good["filter"], "group": ["tag", "blink"]}})


# --- the MoleculeSelection seam (ADR-0109) ------------------------------------


def test_is_a_molecule_selection_that_names_nothing_in_another_project(tmp_path: Path) -> None:
    one = _project(tmp_path, name="one.tether")
    other = _project(tmp_path, name="other.tether")
    sel = select_molecules(one, name="A", condition_ids=[_CID_A])

    assert isinstance(sel, MoleculeSelection)
    assert resolve_selection(sel, one) == ["k0", "k1", "k2"]
    assert resolve_selection(sel, other) == []  # same keys, different project: no pooling


def test_drives_a_population_function_as_its_keys_do(tmp_path: Path) -> None:
    states = np.array([[0, 1, 2, 1, 0, 1], [2, 2, 1, 0, 0, 1], [1, 0, 0, 2, 2, 2]], dtype="int64")
    project, keys = build_store_with_model(tmp_path, states, MEANS)
    sel = SavedSelection("two", str(project.path), keys[:2])

    by_object = ta.population_apparent_e_histogram(project, molecule_keys=sel)
    by_list = ta.population_apparent_e_histogram(project, molecule_keys=list(keys[:2]))
    np.testing.assert_array_equal(by_object.counts, by_list.counts)
    assert by_object.n_molecules == by_list.n_molecules == 2


# --- Qt-free ------------------------------------------------------------------


def test_importing_the_module_loads_no_gui_toolkit_or_plotting_library() -> None:
    probe = (
        "import sys, tether.analysis.saved_selection\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in "
        "{'PySide6', 'PyQt5', 'PyQt6', 'qtpy', 'matplotlib', 'pyqtgraph'})\n"
        "print(','.join(bad))\n"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""


@pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None, reason="needs the base PySide6 stack"
)
def test_the_renderer_spike_binds_matplotlib_base_to_pyside6() -> None:
    """ADR-0124's spike, kept as a guard: FigureCanvasQTAgg loads on PySide6, never PyQt."""
    probe = (
        "import sys, PySide6\n"
        "from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg\n"
        "from matplotlib.backends import qt_compat\n"
        "print(qt_compat.QT_API, any(m.split('.')[0] in {'PyQt5', 'PyQt6'} for m in sys.modules))\n"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["PySide6", "False"]

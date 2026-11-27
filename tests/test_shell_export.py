# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""File -> Export writes the three project exports from the open project (#621), headless.

Each action opens the dialog of one export function of ``tether.project.export``. The tests
check that a file written from the menu equals the file the function writes for the same
arguments (apart from time stamps), that its provenance file is written, that the project
stays open and writable, that a cancelled dialog or a cancelled task writes nothing, and
that an error of the function reaches the status bar with its message unchanged.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from pathlib import Path

import pytest

pytest.importorskip("h5py")

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
pytestmark = [
    pytest.mark.gui,
    pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed"),
]

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _project(tmp_path: Path, *, rejected: list[bool] | None = None) -> Path:
    import numpy as np

    from _analysis_store import build_store_with_channels  # noqa: PLC0415

    rng = np.random.default_rng(621)
    donor = rng.uniform(400.0, 800.0, size=(3, 12))
    acceptor = rng.uniform(200.0, 600.0, size=(3, 12))
    project, _keys = build_store_with_channels(
        tmp_path, donor, acceptor, name="movie.tether", rejected=rejected or [True, False, False]
    )
    return Path(project.path)


@pytest.fixture
def shell(qapp, qtbot, tmp_path, monkeypatch):
    import tether.gui.shell as shell_module
    from tether.gui.shell import TetherShell

    # No movie panel: the project has no movie, and the panel needs OpenGL.
    monkeypatch.setattr(shell_module, "_movie_panel_unavailable", lambda: "not in this test")
    shell = TetherShell(preferences_file=tmp_path / "user" / "preferences.json")
    qtbot.addWidget(shell.window)
    yield shell
    shell.close()


def _latest(shell) -> str:
    return shell.messages.latest.text


def _export(qtbot, shell, kind, out: Path, **ticks: bool):
    """Open the dialog of *kind*, choose *out* and the options, and press Export."""
    dialog = shell.show_export_dialog(kind)
    assert dialog is not None and dialog.dialog.isVisible()
    dialog.destination.setText(str(out))
    for name, value in ticks.items():
        getattr(dialog, name).setChecked(value)
    dialog.ok_button.click()
    qtbot.waitUntil(lambda: not shell.is_idealizing, timeout=10_000)
    return dialog


def _provenance(path: Path) -> dict:
    payload = json.loads(path.with_name(path.name + ".provenance.json").read_text("utf-8"))
    payload.pop("created_utc")
    return payload


def test_the_menu_holds_the_three_exports_while_a_project_is_open(shell, tmp_path):
    menu = shell.export_menu
    assert menu.title() == "&Export"
    assert menu.menuAction() in shell.file_menu.actions()
    assert [a.text() for a in menu.actions()] == [
        "Molecule &table (CSV)…",
        "T&races (Deep-LASI text)…",
        "&Subset project…",
    ]
    assert not menu.isEnabled()
    assert shell.load_project(_project(tmp_path)) is not None
    assert menu.isEnabled()
    shell.close_project()
    assert not menu.isEnabled()


@pytest.mark.parametrize("include_rejected", [True, False])
def test_the_table_equals_the_functions_file(qtbot, shell, tmp_path, include_rejected):
    from tether.gui.exports import ExportKind
    from tether.project.export import export_molecule_table_csv

    project = _project(tmp_path)
    assert shell.load_project(project) is not None
    out = tmp_path / "out" / "molecules.csv"
    out.parent.mkdir()
    _export(qtbot, shell, ExportKind.TABLE, out, include_rejected=include_rejected)

    n = 3 if include_rejected else 2
    assert _latest(shell) == f"Exported {n} molecule(s) -> molecules.csv"
    reference = tmp_path / "reference.csv"
    export_molecule_table_csv(project, reference, include_rejected=include_rejected)
    assert out.read_bytes() == reference.read_bytes()
    assert _provenance(out) == _provenance(reference)
    # Only the two files: the staged names are gone.
    assert sorted(p.name for p in out.parent.iterdir()) == [
        "molecules.csv",
        "molecules.csv.provenance.json",
    ]
    # The project stays open and writable: no second claim, and no close.
    assert shell.read_only_reason is None


def test_the_table_dialog_defaults_to_the_functions_default(shell, tmp_path):
    from tether.gui.exports import ExportKind

    project = _project(tmp_path)
    assert shell.load_project(project) is not None
    dialog = shell.show_export_dialog(ExportKind.TABLE)
    assert dialog.include_rejected.isChecked()  # the function's include_rejected=True
    assert dialog.destination.text() == str(tmp_path / "movie.csv")
    request = dialog.request()
    assert request.out_path == tmp_path / "movie.csv"
    dialog.destination.setText(str(tmp_path / "bare"))
    assert dialog.request().out_path == tmp_path / "bare.csv"  # the suffix is added
    dialog.destination.setText("")
    assert not dialog.ok_button.isEnabled()
    dialog.dialog.reject()


def test_the_traces_equal_the_functions_file(qtbot, shell, tmp_path):
    from tether.gui.exports import ExportKind
    from tether.project.export import export_deeplasi_txt

    project = _project(tmp_path)
    assert shell.load_project(project) is not None
    out = tmp_path / "movie-donc-accc-w.txt"
    dialog = _export(qtbot, shell, ExportKind.TRACES, out)
    assert not dialog.include_rejected.isChecked()  # the function's include_rejected=False
    assert _latest(shell) == "Exported 2 molecule(s) -> movie-donc-accc-w.txt"
    reference = tmp_path / "reference.txt"
    export_deeplasi_txt(project, reference)
    assert out.read_bytes() == reference.read_bytes()
    assert _provenance(out) == _provenance(reference)


def test_the_traces_of_the_selected_molecules_movie(qtbot, shell, tmp_path):
    from tether.gui.exports import ExportKind
    from tether.project.export import export_deeplasi_txt

    project = _project(tmp_path)
    assert shell.load_project(project) is not None
    keys = [t.molecule_key for t in shell._traces]  # noqa: SLF001 - the molecules shown
    out = tmp_path / "one-movie.txt"
    dialog = _export(qtbot, shell, ExportKind.TRACES, out, movie_only=True, include_rejected=True)
    assert dialog.movie_only.isEnabled()
    assert dialog.request().movie_of == keys[shell.molecule_list.currentRow()]
    reference = tmp_path / "reference.txt"
    export_deeplasi_txt(project, reference, molecule_keys=keys, include_rejected=True)
    assert out.read_bytes() == reference.read_bytes()
    assert _provenance(out) == _provenance(reference)


@pytest.mark.parametrize("include_raw", [False, True])
def test_the_subset_equals_the_functions_file(qtbot, shell, tmp_path, include_raw):
    import h5py
    import numpy as np

    from tether.gui.exports import ExportKind
    from tether.project.export import export_subset_tether

    project = _project(tmp_path)
    assert shell.load_project(project) is not None
    out = tmp_path / "shared.tether"
    _export(qtbot, shell, ExportKind.SUBSET, out, include_raw=include_raw, replace_names=True)
    assert _latest(shell) == "Exported 2 molecule(s) -> shared.tether"
    reference = tmp_path / "reference.tether"
    export_subset_tether(project, reference, include_raw=include_raw, curator_alias="curator")
    assert _provenance(out) == _provenance(reference)

    def contents(path: Path) -> dict[str, object]:
        found: dict[str, object] = {}
        with h5py.File(path, "r") as f:
            f.visititems(
                lambda name, obj: (
                    found.__setitem__(name, obj[()]) if isinstance(obj, h5py.Dataset) else None
                )
            )
        return found

    mine, theirs = contents(out), contents(reference)
    assert mine.keys() == theirs.keys()
    for name in mine:
        a, b = np.asarray(mine[name]), np.asarray(theirs[name])
        for field in a.dtype.names or (None,):
            left, right = (a, b) if field is None else (a[field], b[field])
            np.testing.assert_array_equal(left, right, err_msg=f"{name}:{field}")


def test_a_cancelled_dialog_writes_nothing(shell, tmp_path):
    from tether.gui.exports import ExportKind

    project = _project(tmp_path)
    assert shell.load_project(project) is not None
    before = sorted(tmp_path.iterdir())
    for kind in ExportKind:
        dialog = shell.show_export_dialog(kind)
        dialog.dialog.reject()
        assert shell.export_dialog is None
        assert _latest(shell).endswith(": cancelled, nothing was written")
    assert sorted(tmp_path.iterdir()) == before


def test_a_cancelled_export_writes_nothing(qtbot, shell, tmp_path, monkeypatch):
    import tether.project.export as export_module
    from tether.gui.exports import ExportKind

    project = _project(tmp_path)
    assert shell.load_project(project) is not None
    real = export_module.export_molecule_table_csv
    entered, release = threading.Event(), threading.Event()

    def held(*args, **kwargs):
        result = real(*args, **kwargs)  # the files are written under the staged name
        entered.set()
        assert release.wait(10), "the test never released the export"
        return result

    monkeypatch.setattr(export_module, "export_molecule_table_csv", held)
    before = sorted(tmp_path.iterdir())
    dialog = shell.show_export_dialog(ExportKind.TABLE)
    dialog.destination.setText(str(tmp_path / "molecules.csv"))
    dialog.ok_button.click()
    assert shell.is_idealizing
    qtbot.waitUntil(entered.is_set, timeout=10_000)
    shell._busy_indicator.cancel_button.click()  # noqa: SLF001 - the indicator's Cancel
    release.set()
    qtbot.waitUntil(lambda: not shell.is_idealizing, timeout=10_000)
    assert _latest(shell) == "Export the molecule table: cancelled, nothing was written"
    assert sorted(tmp_path.iterdir()) == before


def test_an_empty_selection_reaches_the_status_bar_unchanged(qtbot, shell, tmp_path):
    from tether.gui.exports import ExportKind
    from tether.project.export import export_deeplasi_txt

    project = _project(tmp_path, rejected=[True, True, True])
    with pytest.raises(ValueError, match="no molecules selected") as raised:
        export_deeplasi_txt(project, tmp_path / "direct.txt")
    assert shell.load_project(project) is not None
    out = tmp_path / "traces.txt"
    _export(qtbot, shell, ExportKind.TRACES, out)
    assert _latest(shell) == f"Export the traces for Deep-LASI failed: {raised.value}"
    assert not out.exists()
    assert not out.with_name(out.name + ".provenance.json").exists()


def test_the_source_as_destination_is_refused_with_the_functions_message(qtbot, shell, tmp_path):
    from tether.gui.exports import ExportKind, ExportRequest
    from tether.project.export import export_subset_tether

    project = _project(tmp_path)
    with pytest.raises(ValueError, match="is the source project") as raised:
        export_subset_tether(project, project)
    assert shell.load_project(project) is not None
    _export(qtbot, shell, ExportKind.SUBSET, project)
    assert _latest(shell) == f"Export a subset project failed: {raised.value}"
    # The dialog adds the table's suffix, so only a request names the project itself. It is
    # refused before anything is written, and the project still opens.
    before = project.read_bytes()
    assert shell.export_project(ExportRequest(ExportKind.TABLE, project, include_rejected=True))
    qtbot.waitUntil(lambda: not shell.is_idealizing, timeout=10_000)
    assert _latest(shell) == (
        f"Export the molecule table failed: export destination {project} is the source "
        "project; refusing to overwrite"
    )
    assert project.read_bytes() == before


def test_a_missing_molecule_key_reads_without_quotes():
    from tether.gui.exports import error_text

    assert error_text(KeyError("no molecule with molecule_key(s) ['x'] in the store")) == (
        "no molecule with molecule_key(s) ['x'] in the store"
    )
    assert error_text(ValueError("plain")) == "plain"


def test_no_export_without_a_project(shell):
    from tether.gui.exports import ExportKind

    assert shell.show_export_dialog(ExportKind.TABLE) is None
    assert _latest(shell) == "Export the molecule table: open a project first"

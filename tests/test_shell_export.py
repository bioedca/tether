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


class _ReplaceFailsOnto:
    """``os`` for the export module: moving a staged file onto one path fails.

    A put-back of the file set aside (``…previous-<n>``) onto that path still works.
    """

    def __init__(self, real, target: Path, message: str) -> None:
        self._real, self._target, self._message = real, target, message

    def replace(self, source, destination):
        if Path(destination) == self._target and ".previous-" not in Path(source).name:
            raise OSError(self._message)
        return self._real.replace(source, destination)

    def __getattr__(self, name):
        return getattr(self._real, name)


def _fail_replace_onto(monkeypatch, exports_module, target: Path, message: str) -> None:
    import os

    monkeypatch.setattr(exports_module, "os", _ReplaceFailsOnto(os, target, message))


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
    # A finished export dialog is deleted later; delete it now, so a hidden dialog keeps
    # neither the active window nor the focus for the next test (offscreen).
    from pyqtgraph.Qt import QtCore

    qapp.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)


def _latest(shell) -> str:
    return shell.messages.latest.text


def _export(qtbot, shell, kind, out: Path, **ticks: bool):
    """Open the dialog of *kind*, choose *out* and the options, and press Export.

    Returns the request, the defaults the dialog showed, and whether each ticked box was
    enabled, all read before Export: the finished dialog is deleted.
    """
    dialog = shell.show_export_dialog(kind)
    assert dialog is not None and dialog.dialog.isVisible()
    dialog.destination.setText(str(out))
    defaults = {"include_rejected": dialog.include_rejected.isChecked()}
    for name, value in ticks.items():
        getattr(dialog, name).setChecked(value)
    enabled = {name: getattr(dialog, name).isEnabled() for name in ticks}
    request = dialog.request()
    dialog.ok_button.click()  # the finished dialog is deleted
    qtbot.waitUntil(lambda: not shell.is_idealizing, timeout=10_000)
    return request, defaults, enabled


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
    from tether.project.export import export_molecule_table_csv
    from tether.services.exports import ExportKind

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
    assert sorted(p.name for p in out.parent.iterdir() if p.suffix != ".guard") == [
        "molecules.csv",
        "molecules.csv.provenance.json",
    ]
    # The project stays open and writable: no second claim, and no close.
    assert shell.read_only_reason is None


def test_the_table_dialog_defaults_to_the_functions_default(shell, tmp_path):
    from tether.services.exports import ExportKind

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
    from tether.project.export import export_deeplasi_txt
    from tether.services.exports import ExportKind

    project = _project(tmp_path)
    assert shell.load_project(project) is not None
    out = tmp_path / "movie-donc-accc-w.txt"
    _request, defaults, _enabled = _export(qtbot, shell, ExportKind.TRACES, out)
    assert not defaults["include_rejected"]  # the function's include_rejected=False
    assert _latest(shell) == "Exported 2 molecule(s) -> movie-donc-accc-w.txt"
    reference = tmp_path / "reference.txt"
    export_deeplasi_txt(project, reference)
    assert out.read_bytes() == reference.read_bytes()
    assert _provenance(out) == _provenance(reference)


def test_the_traces_of_the_selected_molecules_movie(qtbot, shell, tmp_path):
    from tether.project.export import export_deeplasi_txt
    from tether.services.exports import ExportKind

    project = _project(tmp_path)
    assert shell.load_project(project) is not None
    keys = [t.molecule_key for t in shell._traces]  # noqa: SLF001 - the molecules shown
    out = tmp_path / "one-movie.txt"
    request, _defaults, enabled = _export(
        qtbot, shell, ExportKind.TRACES, out, movie_only=True, include_rejected=True
    )
    assert enabled["movie_only"]
    row = shell.molecule_list.currentRow()
    assert request.movie_of == (row, keys[row])
    reference = tmp_path / "reference.txt"
    export_deeplasi_txt(project, reference, molecule_keys=keys, include_rejected=True)
    assert out.read_bytes() == reference.read_bytes()
    assert _provenance(out) == _provenance(reference)


@pytest.mark.parametrize("include_raw", [False, True])
def test_the_subset_equals_the_functions_file(qtbot, shell, tmp_path, include_raw):
    import h5py
    import numpy as np

    from tether.project.export import export_subset_tether
    from tether.services.exports import ExportKind

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
    from tether.services.exports import ExportKind

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
    from tether.services.exports import ExportKind

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
    from tether.project.export import export_deeplasi_txt
    from tether.services.exports import ExportKind

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
    from tether.project.export import export_subset_tether
    from tether.services.exports import ExportKind, ExportRequest

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
    from tether.services.exports import error_text

    assert error_text(KeyError("no molecule with molecule_key(s) ['x'] in the store")) == (
        "no molecule with molecule_key(s) ['x'] in the store"
    )
    assert error_text(ValueError("plain")) == "plain"


def test_no_export_without_a_project(shell):
    from tether.services.exports import ExportKind

    assert shell.show_export_dialog(ExportKind.TABLE) is None
    assert _latest(shell) == "Export the molecule table: open a project first"


def test_a_failed_publish_keeps_the_previous_export_and_its_provenance(tmp_path):
    from tether.services.exports import ExportKind, ExportRequest, run_export

    project = _project(tmp_path)
    out = tmp_path / "molecules.csv"
    out.write_text("previous table\n", encoding="utf-8")
    # A folder where the provenance file goes: the second move fails after the first.
    (tmp_path / "molecules.csv.provenance.json").mkdir()
    before = sorted(p.name for p in tmp_path.iterdir() if p.suffix != ".guard")
    with pytest.raises(OSError):
        run_export(project, ExportRequest(ExportKind.TABLE, out, include_rejected=True))
    assert out.read_text(encoding="utf-8") == "previous table\n"
    assert sorted(p.name for p in tmp_path.iterdir() if p.suffix != ".guard") == before


def test_an_export_replaces_the_previous_pair_and_leaves_nothing_else(tmp_path):
    from tether.services.exports import ExportKind, ExportRequest, run_export

    project = _project(tmp_path)
    folder = tmp_path / "out"
    folder.mkdir()
    out = folder / "molecules.csv"
    out.write_text("previous table\n", encoding="utf-8")
    out.with_name(out.name + ".provenance.json").write_text("{}\n", encoding="utf-8")
    result = run_export(project, ExportRequest(ExportKind.TABLE, out, include_rejected=False))
    assert result.n_molecules == 2
    assert out.read_text(encoding="utf-8").startswith("molecule_id,")
    assert _provenance(out)["parameters"]["include_rejected"] is False
    assert sorted(p.name for p in folder.iterdir() if p.suffix != ".guard") == [
        "molecules.csv",
        "molecules.csv.provenance.json",
    ]


def test_a_movie_whose_key_names_a_molecule_of_another_movie_is_refused(tmp_path, monkeypatch):
    import numpy as np

    import tether.imaging.extract as extract_module
    from tether.services.exports import ExportKind, ExportRequest, run_export

    project = _project(tmp_path)
    # Two movies share the key "k": selecting it in m2 must not bring m1's row in.
    table = np.array(
        [(b"k", b"m1"), (b"j", b"m2"), (b"k", b"m2")],
        dtype=[("molecule_key", "S1"), ("movie_id", "S2")],
    )
    monkeypatch.setattr(extract_module, "read_molecules", lambda _path: table)
    out = tmp_path / "m2.txt"
    request = ExportRequest(ExportKind.TRACES, out, include_rejected=True, movie_of=(2, "k"))
    with pytest.raises(ValueError, match=r"\['k'\] of movie 'm2' also name molecules"):
        run_export(project, request)
    stale = ExportRequest(ExportKind.TRACES, out, include_rejected=True, movie_of=(1, "k"))
    with pytest.raises(ValueError, match="is not row 1 of the project"):
        run_export(project, stale)
    assert not out.exists()


def test_a_subset_is_refused_onto_a_project_another_writer_holds(tmp_path):
    from tether.project.lock import LockedError, acquire, release
    from tether.services.exports import ExportKind, ExportRequest, run_export

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    project = _project(tmp_path / "a")
    other = _project(tmp_path / "b")
    before = other.read_bytes()
    held = acquire(other)  # another writer has project B open
    try:
        request = ExportRequest(ExportKind.SUBSET, other, include_rejected=False)
        with pytest.raises(LockedError, match="is locked"):
            run_export(project, request)
    finally:
        release(other, held)

    def files() -> list[str]:
        # The lock's own guard file stays beside a project once it was locked.
        return sorted(p.name for p in other.parent.iterdir() if p.suffix != ".guard")

    assert other.read_bytes() == before
    assert files() == ["movie.tether"]
    # With no writer holding it, the subset replaces it.
    result = run_export(project, ExportRequest(ExportKind.SUBSET, other, include_rejected=False))
    assert result.n_molecules == 2
    assert files() == ["movie.tether", "movie.tether.provenance.json"]


def test_a_lock_taken_during_a_subset_publish_leaves_the_project_where_it_is(tmp_path, monkeypatch):
    import contextlib

    import tether.project.lock as lock_module
    from tether.project.lock import LockError
    from tether.services.exports import ExportKind, ExportRequest, run_export

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    project = _project(tmp_path / "a")
    other = _project(tmp_path / "b")
    provenance = other.with_name(other.name + ".provenance.json")
    provenance.write_text("{}\n", encoding="utf-8")
    before = other.read_bytes()
    present: list[bool] = []

    @contextlib.contextmanager
    def taken_after_the_first_check(_path):
        def guard() -> None:
            # The project must still be at its name whenever the lock is checked.
            present.append(other.read_bytes() == before)
            if len(present) > 1:  # another writer took the lock after the first check
                raise LockError("lock ownership changed before publish")

        yield guard

    monkeypatch.setattr(lock_module, "publish_lock", taken_after_the_first_check)
    # No real lock is held here, so the lifecycle guard is not modelled: this checks the
    # ownership checks behind it.
    monkeypatch.setattr(lock_module, "read_lock", lambda _path: object())
    monkeypatch.setattr(lock_module, "holding", lambda _path, _held: contextlib.nullcontext())
    with pytest.raises(LockError, match="ownership changed"):
        run_export(project, ExportRequest(ExportKind.SUBSET, other, include_rejected=False))
    # The project file was never moved, so it is where it was at every check, unchanged.
    assert present == [True, True, True]  # the third is the check before any put-back
    assert other.read_bytes() == before
    # The lock is lost, so nothing at the destination is touched: the previous provenance
    # file stays beside it under its temporary name.
    names = sorted(p.name for p in other.parent.iterdir() if p.suffix != ".guard")
    assert any(n.startswith(".movie.tether.") and n.endswith(".previous-0") for n in names)
    aside = next(p for p in other.parent.iterdir() if p.name.endswith(".previous-0"))
    assert aside.read_text(encoding="utf-8") == "{}\n"


def test_a_new_owners_pair_is_kept_when_the_lock_is_taken_mid_publish(tmp_path, monkeypatch):
    import contextlib

    import tether.project.lock as lock_module
    from tether.project.lock import LockError
    from tether.services.exports import ExportKind, ExportRequest, run_export

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    project = _project(tmp_path / "a")
    other = _project(tmp_path / "b")
    provenance = other.with_name(other.name + ".provenance.json")
    provenance.write_text("{}\n", encoding="utf-8")
    checks: list[int] = []

    @contextlib.contextmanager
    def taken_and_published(_path):
        def guard() -> None:
            checks.append(1)
            if len(checks) == 2:
                # Another writer takes the lock and publishes its own project and provenance.
                other.write_bytes(b"successor project")
                provenance.write_text("successor provenance\n", encoding="utf-8")
            if len(checks) > 1:
                raise LockError("lock ownership changed before publish")

        yield guard

    monkeypatch.setattr(lock_module, "publish_lock", taken_and_published)
    # No real lock is held here, so the lifecycle guard is not modelled: this checks the
    # ownership checks behind it.
    monkeypatch.setattr(lock_module, "read_lock", lambda _path: object())
    monkeypatch.setattr(lock_module, "holding", lambda _path, _held: contextlib.nullcontext())
    with pytest.raises(LockError, match="ownership changed"):
        run_export(project, ExportRequest(ExportKind.SUBSET, other, include_rejected=False))
    # The new owner's pair is left as it published it.
    assert other.read_bytes() == b"successor project"
    assert provenance.read_text(encoding="utf-8") == "successor provenance\n"


@pytest.mark.parametrize("failing", [False, True])
def test_no_takeover_can_land_while_a_subset_publish_moves_files(tmp_path, monkeypatch, failing):
    import tether.project.lock as lock_module
    import tether.services.exports as exports_module
    from tether.services.exports import ExportKind, ExportRequest, run_export

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    project = _project(tmp_path / "a")
    other = _project(tmp_path / "b")
    provenance = other.with_name(other.name + ".provenance.json")
    provenance.write_text("{}\n", encoding="utf-8")
    before = other.read_bytes()
    # Every acquire, steal, refresh and release takes the path's lifecycle guard. While the
    # publish places or puts back files, that guard is held, so such a call times out.
    monkeypatch.setattr(lock_module, "_GUARD_ACQUIRE_TIMEOUT_S", 0.2)
    blocked: list[str] = []

    def takeover_is_blocked(step: str) -> None:
        lp = lock_module.lock_path(other)
        try:
            with lock_module._lifecycle_guard(lp):  # noqa: SLF001 - the guard a steal takes
                pass
        except TimeoutError:
            blocked.append(step)

    real_place, real_undo = exports_module._place, exports_module._undo  # noqa: SLF001

    def place(*args, **kwargs):
        takeover_is_blocked("place")
        return real_place(*args, **kwargs)

    def undo(*args, **kwargs):
        takeover_is_blocked("undo")
        return real_undo(*args, **kwargs)

    monkeypatch.setattr(exports_module, "_place", place)
    monkeypatch.setattr(exports_module, "_undo", undo)
    request = ExportRequest(ExportKind.SUBSET, other, include_rejected=False)
    if failing:
        _fail_replace_onto(monkeypatch, exports_module, other, "the project replace failed")
        with pytest.raises(OSError, match="project replace failed"):
            run_export(project, request)
        assert blocked == ["place", "undo"]
        assert other.read_bytes() == before
        assert provenance.read_text(encoding="utf-8") == "{}\n"
    else:
        assert run_export(project, request).n_molecules == 2
        assert blocked == ["place"]
    # After the publish the lock is free again.
    monkeypatch.setattr(lock_module, "_GUARD_ACQUIRE_TIMEOUT_S", 5.0)
    held = lock_module.acquire(other)
    lock_module.release(other, held)


def test_the_identity_record_is_written_outside_the_lifecycle_guard(tmp_path, monkeypatch):
    import contextlib

    import tether.project.lock as lock_module
    from tether.services.exports import ExportKind, ExportRequest, run_export

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    project = _project(tmp_path / "a")
    other = _project(tmp_path / "b")
    # A claim takes the identity guard, then the lifecycle guard. The identity record takes
    # the identity guard on entry and on exit, so the lifecycle guard must be free then:
    # holding it there would take the two guards in the opposite order.
    monkeypatch.setattr(lock_module, "_GUARD_ACQUIRE_TIMEOUT_S", 0.2)
    free: list[str] = []
    real = lock_module.replacement_identity

    def lifecycle_guard_is_free(step: str) -> None:
        with lock_module._lifecycle_guard(lock_module.lock_path(other)):  # noqa: SLF001
            free.append(step)

    @contextlib.contextmanager
    def watched(stage, destination):
        lifecycle_guard_is_free("enter")
        with real(stage, destination):
            yield
        lifecycle_guard_is_free("exit")

    monkeypatch.setattr(lock_module, "replacement_identity", watched)
    run_export(project, ExportRequest(ExportKind.SUBSET, other, include_rejected=False))
    assert free == ["enter", "exit"]


def test_a_failure_after_the_project_replace_keeps_the_new_pair(tmp_path, monkeypatch):
    import contextlib

    import tether.project.lock as lock_module
    from tether.services.exports import ExportKind, ExportRequest, run_export

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    project = _project(tmp_path / "a")
    other = _project(tmp_path / "b")
    provenance = other.with_name(other.name + ".provenance.json")
    provenance.write_text("{}\n", encoding="utf-8")
    before = other.read_bytes()

    @contextlib.contextmanager
    def bookkeeping_fails_after(_stage, _destination):
        yield
        raise TimeoutError("the identity record could not be updated")

    monkeypatch.setattr(lock_module, "replacement_identity", bookkeeping_fails_after)
    with pytest.raises(TimeoutError, match="identity record"):
        run_export(project, ExportRequest(ExportKind.SUBSET, other, include_rejected=False))
    # The project is the new subset and the provenance is its own: a consistent pair.
    assert other.read_bytes() != before
    assert _provenance(other)["tether_export"] == "subset-tether"
    assert sorted(p.name for p in other.parent.iterdir() if p.suffix != ".guard") == [
        "movie.tether",
        "movie.tether.provenance.json",
    ]


def test_a_failed_provenance_move_of_a_subset_leaves_the_project_and_restores_its_pair(
    tmp_path, monkeypatch
):
    import tether.services.exports as exports_module
    from tether.services.exports import ExportKind, ExportRequest, run_export

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    project = _project(tmp_path / "a")
    other = _project(tmp_path / "b")
    provenance = other.with_name(other.name + ".provenance.json")
    provenance.write_text("{}\n", encoding="utf-8")
    before = other.read_bytes()
    _fail_replace_onto(monkeypatch, exports_module, provenance, "the provenance move failed")
    with pytest.raises(OSError, match="provenance move failed"):
        run_export(project, ExportRequest(ExportKind.SUBSET, other, include_rejected=False))
    assert other.read_bytes() == before
    assert provenance.read_text(encoding="utf-8") == "{}\n"
    assert sorted(p.name for p in other.parent.iterdir() if p.suffix != ".guard") == [
        "movie.tether",
        "movie.tether.provenance.json",
    ]


class _ReplaceHoldsAfterTheData:
    """``os`` for the export module: one thread's export stops after its data move.

    The first thread that moves a staged file onto ``target`` sets ``held`` and waits up to
    ``wait_s`` for ``release`` before going on to its provenance move; other threads pass.
    """

    def __init__(self, real, target: Path, wait_s: float) -> None:
        self._real, self._target, self._wait_s = real, target, wait_s
        self.held, self.release = threading.Event(), threading.Event()
        self._holder: int | None = None

    def replace(self, source, destination):
        result = self._real.replace(source, destination)
        moved_data = Path(destination) == self._target and ".previous-" not in Path(source).name
        if moved_data and self._holder is None:
            self._holder = threading.get_ident()
            self.held.set()
            self.release.wait(self._wait_s)
        return result

    def __getattr__(self, name):
        return getattr(self._real, name)


def _pair_parameter(out: Path) -> tuple[int, bool]:
    """Return the data rows of the table ``out`` and its provenance's ``include_rejected``."""
    rows = len(out.read_text(encoding="utf-8").splitlines()) - 1
    return rows, _provenance(out)["parameters"]["include_rejected"]


def test_two_exports_to_one_destination_publish_one_whole_pair(tmp_path, monkeypatch):
    """#1490: a second export cannot land between the two moves of the first."""
    import os

    import tether.services.exports as exports_module
    from tether.services.exports import ExportKind, ExportRequest, run_export

    project = _project(tmp_path)  # three molecules, the first rejected
    folder = tmp_path / "out"
    folder.mkdir()
    out = folder / "molecules.csv"
    fake = _ReplaceHoldsAfterTheData(os, out, wait_s=1.0)
    monkeypatch.setattr(exports_module, "os", fake)
    errors: list[BaseException] = []

    def export(include_rejected: bool) -> None:
        try:
            run_export(project, ExportRequest(ExportKind.TABLE, out, include_rejected))
        except BaseException as exc:  # noqa: BLE001 - reported by the assertion below
            errors.append(exc)

    first = threading.Thread(target=export, args=(True,))
    first.start()
    assert fake.held.wait(10.0)
    second = threading.Thread(target=export, args=(False,))
    second.start()
    second.join(0.5)  # unfixed, the second export publishes its whole pair meanwhile
    fake.release.set()
    first.join(10.0)
    second.join(10.0)
    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    # One export won as a whole: 3 rows with rejected, 2 without (the second, published last).
    assert _pair_parameter(out) == (2, False)
    assert sorted(p.name for p in folder.iterdir() if p.suffix != ".guard") == [
        "molecules.csv",
        "molecules.csv.provenance.json",
    ]


_HOLD_THE_PUBLISH_GUARD = """
import sys
from pathlib import Path

from tether.services.exports import _destination_guard

with _destination_guard(Path(sys.argv[1])):
    print("held", flush=True)
    sys.stdin.readline()
"""


def test_an_export_is_refused_while_another_process_publishes_to_its_destination(
    tmp_path, monkeypatch
):
    """#1490: the per-destination publish guard holds across processes, not only threads."""
    import os
    import subprocess

    import tether.project.lock as lock_module
    from tether.services.exports import ExportKind, ExportRequest, run_export

    project = _project(tmp_path)
    folder = tmp_path / "out"
    folder.mkdir()
    out = folder / "molecules.csv"
    out.write_text("previous table\n", encoding="utf-8")
    provenance = out.with_name(out.name + ".provenance.json")
    provenance.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(lock_module, "_GUARD_ACQUIRE_TIMEOUT_S", 0.5)
    src = Path(__file__).resolve().parent.parent / "src"
    env = {**os.environ, "PYTHONPATH": str(src)}
    holder = subprocess.Popen(  # noqa: S603 - the test's own interpreter and script
        [sys.executable, "-c", _HOLD_THE_PUBLISH_GUARD, str(out)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "held"
        with pytest.raises(TimeoutError):
            run_export(project, ExportRequest(ExportKind.TABLE, out, include_rejected=True))
        assert out.read_text(encoding="utf-8") == "previous table\n"
        assert provenance.read_text(encoding="utf-8") == "{}\n"
    finally:
        assert holder.stdin is not None
        holder.stdin.write("\n")
        holder.stdin.close()
        holder.wait(10.0)
    result = run_export(project, ExportRequest(ExportKind.TABLE, out, include_rejected=True))
    assert result.n_molecules == 3
    assert _pair_parameter(out) == (3, True)
    assert sorted(p.name for p in folder.iterdir() if p.suffix != ".guard") == [
        "molecules.csv",
        "molecules.csv.provenance.json",
    ]

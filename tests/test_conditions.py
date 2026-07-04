# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the condition-validation / confirm-correct / merge dialog (M4 PR-2b, §5.1/§7.6).

All ``@pytest.mark.gui``: they build a real :class:`~tether.gui.conditions.ConditionKeyEditor`
/ :class:`~tether.gui.conditions.ConditionValidationDialog` on the pytest-qt
``QApplication`` over a seeded on-disk ``.tether`` and assert the M4 gates (PLAN §8):
a mis-parsed id re-keys **all** affected molecules transactionally with an audit entry,
and a **merge is human-confirmed** (never silent). The key editor's round-trip and the
shell menu wiring are covered too. Pixel rendering is left to the live smoke.
"""

from __future__ import annotations

import importlib.util
from typing import TYPE_CHECKING

import pytest

pytest.importorskip("h5py")
pytest.importorskip("numpy")

import h5py  # noqa: E402
import numpy as np  # noqa: E402

from tether.io.filename import ConditionKey, parse_filename  # noqa: E402
from tether.io.schema import MOLECULES_DTYPE, TABLE, create_project  # noqa: E402
from tether.project import conditions as C  # noqa: E402
from tether.project.core import Project  # noqa: E402
from tether.project.lock import LockedError, LockIdentity  # noqa: E402

if TYPE_CHECKING:
    from pathlib import Path

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
_needs_qt = pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed")

pytestmark = [pytest.mark.gui, _needs_qt]

# Two acquisitions of one condition (T-box) across two files + a near-miss (Tbox).
_FILE_A10 = "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif"
_FILE_A11 = "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_011.tif"
_FILE_NM = "Bla_UCKOPSB_Tbox_35pM_tRNA_600nM_010.tif"


def _seed(
    tmp_path: Path, specs: list[tuple[str, str, str]], *, name: str = "exp.tether"
) -> Project:
    """Create a ``.tether`` from ``(molecule_key, source_filename, condition_id)`` specs."""
    path = create_project(tmp_path / name)
    rows = np.zeros(len(specs), dtype=MOLECULES_DTYPE)
    for field in MOLECULES_DTYPE.names:
        if MOLECULES_DTYPE[field].kind == "O":
            rows[field] = ""
    rows["molecule_id"] = [f"mol-{i}" for i in range(len(specs))]
    rows["molecule_key"] = [k for k, _, _ in specs]
    rows["source_filename"] = [s for _, s, _ in specs]
    rows["condition_id"] = [c for _, _, c in specs]
    with h5py.File(path, "r+") as f:
        table = f["molecules"][TABLE]
        table.resize((len(specs),))
        table[:] = rows
    return Project(path)


def _faithful(tmp_path: Path, pairs: list[tuple[str, str]]) -> Project:
    """Seed with each molecule a faithful witness of its own condition (id from the parse)."""
    return _seed(tmp_path, [(k, s, parse_filename(s).condition_id) for k, s in pairs])


def _editor(qtbot, key=None):
    from tether.gui.conditions import ConditionKeyEditor

    ed = ConditionKeyEditor(key)
    qtbot.addWidget(ed.dialog)
    return ed


def _dialog(qtbot, project, *, confirm_merge=None):
    from tether.gui.conditions import ConditionValidationDialog

    dlg = ConditionValidationDialog(project, confirm_merge=confirm_merge)
    qtbot.addWidget(dlg.dialog)
    return dlg


# --- the key editor ----------------------------------------------------------


def test_key_editor_roundtrips(qtbot) -> None:
    key = ConditionKey(
        construct_variant="Bla UCKOPSB T-box",
        dye="Cy3",
        ligand="tRNA",
        ligand_concentration=600.0,
        ligand_concentration_unit="nM",
        buffer="T50",
        temperature_c=25.0,
        laser_power=2.0,
    )
    ed = _editor(qtbot, key)
    assert ed.key() == key


def test_key_editor_blank_numeric_is_none_and_bad_numeric_raises(qtbot) -> None:
    ed = _editor(qtbot, ConditionKey(construct_variant="X", temperature_c=25.0, laser_power=2.0))
    ed.field("temperature_c").setText("")  # blank -> absent
    assert ed.key().temperature_c is None
    ed.field("laser_power").setText("not-a-number")
    with pytest.raises(ValueError, match="number or blank"):
        ed.key()


def test_key_editor_edits_reach_the_key(qtbot) -> None:
    ed = _editor(qtbot)
    ed.field("construct_variant").setText("  Edited construct  ")
    ed.field("ligand_concentration").setText("35")
    key = ed.key()
    assert key.construct_variant == "Edited construct"  # stripped
    assert key.ligand_concentration == 35.0


def test_key_editor_preserves_high_precision_numeric(qtbot) -> None:
    # A high-precision numeric must round-trip exactly through the editor, or pressing
    # OK unchanged would rebuild a DIFFERENT condition_id and silently re-key molecules.
    key = ConditionKey(
        construct_variant="X",
        ligand="tRNA",
        ligand_concentration=1234567.0,
        temperature_c=3.1415926,
    )
    ed = _editor(qtbot, key)
    assert ed.key() == key
    assert ed.key().condition_id() == key.condition_id()  # id stable through the editor


# --- the validation table + materialize (confirm provisional ids) ------------


def test_materialize_confirms_provisional_ids(qtbot, tmp_path: Path) -> None:
    project = _faithful(tmp_path, [("k0", _FILE_A10), ("k1", _FILE_A11), ("k2", _FILE_NM)])
    cid_a = parse_filename(_FILE_A10).condition_id
    cid_nm = parse_filename(_FILE_NM).condition_id
    dlg = _dialog(qtbot, project)

    # Unsynced: every referenced condition is dangling (no /conditions row yet).
    assert set(dlg.row_condition_ids()) == {cid_a, cid_nm}
    assert dlg.status_of(cid_a) == "dangling"
    assert dlg.table.rowCount() == 2

    dlg.materialize()  # "Confirm provisional" -> sync_conditions materializes the rows

    assert dlg.status_of(cid_a) == "ok"
    assert dlg.status_of(cid_nm) == "ok"


def test_select_condition_raises_keyerror_when_absent(qtbot, tmp_path: Path) -> None:
    project = _faithful(tmp_path, [("k0", _FILE_A10)])
    dlg = _dialog(qtbot, project)
    cid = parse_filename(_FILE_A10).condition_id
    dlg.select_condition(cid)
    assert dlg.selected_condition_id() == cid
    with pytest.raises(KeyError):
        dlg.select_condition("cond-absent")


# --- the §9 M4 gate: mis-parsed id re-keys ALL affected molecules + audit ----


def test_misparsed_id_rekeys_all_affected_with_audit(qtbot, tmp_path: Path) -> None:
    # Two molecules share one WRONG (drifted) condition_id — a mis-parse.
    project = _seed(tmp_path, [("k0", _FILE_A10, "cond-wrong"), ("k1", _FILE_A11, "cond-wrong")])
    corrected = ConditionKey(construct_variant="Bla UCKOPSB T-box", ligand="tRNA")
    dlg = _dialog(qtbot, project)

    result = dlg.apply_correction("cond-wrong", corrected)

    assert result is not None
    assert result.n_molecules == 2
    assert result.event == "rekey"  # empty destination -> a plain correction, not a merge
    # ALL affected molecules moved together to the corrected id.
    members = project.molecules_by_condition()
    assert set(members[corrected.condition_id()]) == {"k0", "k1"}
    assert "cond-wrong" not in members
    # A single audit entry recorded the transactional re-key.
    audit = project.read_condition_audit()
    assert audit.shape[0] == 1
    assert C._to_str(audit["event"][0]) == "rekey"
    assert int(audit["n_molecules"][0]) == 2
    assert C._to_str(audit["to_condition_id"][0]) == corrected.condition_id()


def test_noop_correction_writes_nothing(qtbot, tmp_path: Path) -> None:
    key = ConditionKey(construct_variant="A")
    project = _seed(tmp_path, [("k0", _FILE_A10, key.condition_id())])
    dlg = _dialog(qtbot, project)
    # "Correcting" to the same key is a no-op — nothing to re-key, no audit.
    assert dlg.apply_correction(key.condition_id(), key) is None
    assert project.read_condition_audit().shape[0] == 0


# --- the §9 M4 gate: merge is human-confirmed (never silent) -----------------


def test_merge_is_human_confirmed(qtbot, tmp_path: Path) -> None:
    key_a = ConditionKey(construct_variant="A")
    key_b = ConditionKey(construct_variant="B")
    project = _seed(
        tmp_path,
        [("k0", _FILE_A10, key_a.condition_id()), ("k1", _FILE_A11, key_b.condition_id())],
    )

    # Correcting A's id into B (already populated) is a MERGE. Declined -> nothing written.
    declined = _dialog(qtbot, project, confirm_merge=lambda _preview: False)
    assert declined.apply_correction(key_a.condition_id(), key_b) is None
    assert project.read_condition_audit().shape[0] == 0
    assert set(project.molecules_by_condition()[key_a.condition_id()]) == {"k0"}

    # Confirmed -> the two conditions collapse into one, logged as a merge.
    confirmed = _dialog(qtbot, project, confirm_merge=lambda _preview: True)
    result = confirmed.apply_correction(key_a.condition_id(), key_b)
    assert result is not None
    assert result.is_merge
    assert result.event == "merge"
    assert set(project.molecules_by_condition()[key_b.condition_id()]) == {"k0", "k1"}
    audit = project.read_condition_audit()
    assert audit.shape[0] == 1
    assert C._to_str(audit["event"][0]) == "merge"


def test_confirm_merge_callback_receives_preview(qtbot, tmp_path: Path) -> None:
    key_a = ConditionKey(construct_variant="A")
    key_b = ConditionKey(construct_variant="B")
    project = _seed(
        tmp_path,
        [("k0", _FILE_A10, key_a.condition_id()), ("k1", _FILE_A11, key_b.condition_id())],
    )
    seen = {}

    def _confirm(preview):
        seen["is_merge"] = preview.is_merge
        seen["dest"] = tuple(preview.destination_molecule_keys)
        seen["moved"] = preview.molecule_keys
        return True

    dlg = _dialog(qtbot, project, confirm_merge=_confirm)
    dlg.apply_correction(key_a.condition_id(), key_b)
    assert seen["is_merge"] is True
    assert seen["dest"] == ("k1",)
    assert seen["moved"] == ("k0",)


# --- a refused write reports, never escapes the Qt slot (§5.4) ---------------


def test_locked_project_write_reports_error_not_escape(qtbot, tmp_path: Path, monkeypatch) -> None:
    path = _faithful(tmp_path, [("k0", _FILE_A10)]).path
    # A foreign writer holds the single-writer lock.
    holder = Project(path, identity=LockIdentity(host="H", user="u", pid=999))
    holder.acquire_lock()
    try:
        # The dialog acts as a different identity, so its write is refused (§5.4).
        dlg = _dialog(qtbot, Project(path, identity=LockIdentity(host="L", user="l", pid=1)))
        # The core honors the lock (raises)...
        with pytest.raises(LockedError):
            dlg.materialize()
        # ...and the guarded slot surfaces it instead of letting it escape the slot.
        captured: list[Exception] = []
        monkeypatch.setattr(dlg, "_report_error", captured.append)
        dlg._on_materialize()
        assert len(captured) == 1
        assert isinstance(captured[0], LockedError)
    finally:
        holder.release_lock()


# --- shell wiring ------------------------------------------------------------


def test_shell_conditions_menu_wired_and_reports_no_project(qtbot) -> None:
    from tether.gui.shell import TetherShell

    shell = TetherShell()  # no conditions seam
    qtbot.addWidget(shell.window)
    assert shell.conditions_menu is not None
    assert shell._act_validate_conditions is not None
    # With no project the menu handler reports it (and opens no modal).
    shell._validate_conditions_dialog()
    assert "load a project" in shell.window.statusBar().currentMessage().lower()

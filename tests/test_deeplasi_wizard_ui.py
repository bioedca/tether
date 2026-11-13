# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the Deep-LASI re-analysis wizard UI (M7 PR #5, PRD §7.8, FR-LEGACY).

All ``@pytest.mark.gui``: they construct the real :class:`DeepLasiWizardDialog` on
the pytest-qt ``QApplication`` and assert it renders the headless controller's plan,
routes every edit through the controller's validated mutators (reverting an
unsupported edit and surfacing the reason), gates *Run* on readiness + a
destination, and — with an injected fake executor — advances to and renders the
:class:`~tether.gui.deeplasi_executor.ExecutionReport`. Pixel rendering is left to
the live computer-use smoke; these assert wiring/behaviour only.

The plan is built from hand-made :class:`~tether.io.intake.AcquisitionFileSet`s
(discovery reads no file contents, so the role paths need not exist) and the
executor is faked (its own suite covers the real importers), except in one test:
it runs the dialog with **no** injected runner over a committed fixture, so the
join between the dialog and the real
:func:`~tether.gui.deeplasi_executor.execute_plan` (its signature, and the report
attributes the dialog reads) is executed and a real ``.tether`` is written to
``tmp_path`` (#746 GUI-F14).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
_needs_qt = pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed")

pytestmark = [pytest.mark.gui, _needs_qt]


# --------------------------------------------------------------------------- #
# fixtures — hand-built discoveries covering every planned mode
# --------------------------------------------------------------------------- #


def _fileset(key, *, movie=False, mat=False, tdat=False, txt=False, smd=False, warnings=()):
    from tether.io.intake import AcquisitionFileSet

    root = Path("fixtures") / key
    return AcquisitionFileSet(
        key=key,
        condition_id="cond",
        video_index="001",
        movie=root.with_suffix(".tif") if movie else None,
        tdat=root.with_suffix(".tdat") if tdat else None,
        mat=root.with_suffix(".mat") if mat else None,
        txt=root.with_suffix(".txt") if txt else None,
        smd=root.with_suffix(".hdf5") if smd else None,
        warnings=tuple(warnings),
    )


def _discovery(*filesets, unpaired=(), ignored=()):
    from tether.io.intake import DiscoveryResult

    return DiscoveryResult(
        acquisitions=tuple(filesets), unpaired=tuple(unpaired), ignored=tuple(ignored)
    )


def _mixed_wizard():
    """A controller with one reconstruct, one analysis-only, and one blocked/skip set."""
    from tether.gui.deeplasi_wizard import DeepLasiWizard

    return DeepLasiWizard(
        _discovery(
            _fileset("recon_010", movie=True, mat=True, tdat=True),  # → reconstruct (tdat coords)
            _fileset("smd_020", txt=True),  # → analysis-only
            _fileset("bare_030", movie=True),  # → skip (movie only: no .mat, no txt/smd)
        )
    )


def _dialog(qtbot, **kwargs):
    from tether.gui.deeplasi_wizard_ui import DeepLasiWizardDialog

    dlg = DeepLasiWizardDialog(**kwargs)
    qtbot.addWidget(dlg.dialog)
    return dlg


# --------------------------------------------------------------------------- #
# reading the dialog as the user sees it — widgets found by their visible text
# (button/checkbox labels, table headers, placeholders) through Qt's public child
# lookup, not through the dialog's private attributes (#746 GUI-U3)
# --------------------------------------------------------------------------- #

_CONFIRM_HEADERS = ["Acquisition", "Mode", "Coordinates", "Output name", "Notes"]
_REPORT_HEADERS = ["Acquisition", "Mode", "Result", "Notes"]


def _only(widgets):
    (widget,) = widgets
    return widget


def _button(dlg, *texts):
    """The one push button whose label is one of ``texts``."""
    from pyqtgraph.Qt import QtWidgets

    return _only(b for b in dlg.dialog.findChildren(QtWidgets.QPushButton) if b.text() in texts)


def _next_button(dlg):
    """The navigation button, labelled *Next* on intake and *Run* on confirm."""
    return _button(dlg, "Next", "Run")


def _checkbox(dlg, text):
    from pyqtgraph.Qt import QtWidgets

    return _only(c for c in dlg.dialog.findChildren(QtWidgets.QCheckBox) if c.text() == text)


def _bleach_box(dlg):
    return _checkbox(dlg, "Detect photobleaching on import")


def _recursive_box(dlg):
    return _checkbox(dlg, "Search sub-folders")


def _folder_edit(dlg):
    from pyqtgraph.Qt import QtWidgets

    return _only(
        e
        for e in dlg.dialog.findChildren(QtWidgets.QLineEdit)
        if e.placeholderText() == "Acquisition folder…"
    )


def _table(dlg, headers):
    from pyqtgraph.Qt import QtWidgets

    def _headers(table):
        return [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]

    return _only(
        t for t in dlg.dialog.findChildren(QtWidgets.QTableWidget) if _headers(t) == headers
    )


def _confirm_table(dlg):
    return _table(dlg, _CONFIRM_HEADERS)


def _report_table(dlg):
    return _table(dlg, _REPORT_HEADERS)


def _cells(table, row):
    """The text of each cell of ``row`` (``None`` where the cell holds a widget)."""
    items = [table.item(row, c) for c in range(table.columnCount())]
    return [None if item is None else item.text() for item in items]


def _shown_text(dlg):
    """Every label text the dialog holds, one per line (status, heading, summary)."""
    from pyqtgraph.Qt import QtWidgets

    return "\n".join(label.text() for label in dlg.dialog.findChildren(QtWidgets.QLabel))


# --------------------------------------------------------------------------- #
# rendering the plan
# --------------------------------------------------------------------------- #


def test_prebuilt_wizard_opens_on_confirm_with_a_row_per_acquisition(qapp, qtbot) -> None:
    from tether.gui.deeplasi_wizard import WizardMode

    dlg = _dialog(qtbot, wizard=_mixed_wizard())

    assert dlg.step == 1  # confirm step
    assert dlg.mode_combo("recon_010").currentData() == WizardMode.RECONSTRUCT.value
    assert dlg.mode_combo("smd_020").currentData() == WizardMode.ANALYSIS_ONLY.value
    assert dlg.mode_combo("bare_030").currentData() == WizardMode.SKIP.value
    # The reconstruct row offers both coordinate sources, tdat preferred + selected.
    coords = dlg.coordinate_combo("recon_010")
    assert coords.isEnabled()
    assert coords.currentData() == "tdat"
    assert dlg.output_edit("recon_010").text() == "recon_010.tether"


def test_summary_line_reflects_mode_counts(qapp, qtbot) -> None:
    dlg = _dialog(qtbot, wizard=_mixed_wizard())
    text = dlg.summary_text()
    assert "Reconstruct 1" in text
    assert "analysis-only 1" in text
    assert "skip 1" in text


def test_notes_column_shows_the_rationale_and_the_first_warning(qapp, qtbot) -> None:
    from tether.gui.deeplasi_wizard import DeepLasiWizard

    wizard = DeepLasiWizard(
        _discovery(
            _fileset(
                "recon_010", movie=True, mat=True, tdat=True, warnings=("pairing is ambiguous",)
            ),
            _fileset("smd_020", txt=True),
        )
    )
    dlg = _dialog(qtbot, wizard=wizard)
    plans = {p.key: p for p in wizard.plans}
    table = _confirm_table(dlg)
    notes = {_cells(table, r)[0]: _cells(table, r)[4] for r in range(table.rowCount())}

    # A row with a warning shows its rationale and the warning; a row without shows
    # its rationale. Both rationales are real text, so an empty cell fails.
    assert plans["recon_010"].warnings[0] == "pairing is ambiguous"
    assert plans["recon_010"].rationale
    assert notes["recon_010"] == f"{plans['recon_010'].rationale} — pairing is ambiguous"
    assert plans["smd_020"].rationale
    assert notes["smd_020"] == plans["smd_020"].rationale


def test_summary_line_shows_the_discovery_advisories(qapp, qtbot) -> None:
    from tether.gui.deeplasi_wizard import DeepLasiWizard

    wizard = DeepLasiWizard(
        _discovery(
            _fileset("smd_020", txt=True),
            unpaired=(Path("fixtures/orphan.hdf5"),),
            ignored=(Path("fixtures/readme.pdf"),),
        )
    )
    dlg = _dialog(qtbot, wizard=wizard)
    text = dlg.summary_text()
    assert "1 SMD file(s) matched no acquisition and will not be imported" in text
    assert "1 unrecognized file(s) ignored" in text


# --------------------------------------------------------------------------- #
# intake step
# --------------------------------------------------------------------------- #


def test_intake_scan_builds_controller_and_enables_next(qapp, qtbot, monkeypatch) -> None:
    from tether.gui import deeplasi_wizard_ui as ui

    dlg = _dialog(qtbot)  # no wizard → opens on intake
    assert dlg.step == 0
    assert not _next_button(dlg).isEnabled()

    built = _mixed_wizard()
    captured: dict = {}

    class _Stub:
        @staticmethod
        def from_directory(directory, *, recursive=False):
            captured["directory"] = directory
            captured["recursive"] = recursive
            return built

    monkeypatch.setattr(ui, "DeepLasiWizard", _Stub)
    dlg.set_directory("/some/folder")
    _recursive_box(dlg).setChecked(True)
    dlg.scan()

    assert captured == {"directory": "/some/folder", "recursive": True}
    assert dlg.wizard is built
    assert _next_button(dlg).isEnabled()
    # Advancing lands on the populated confirm table.
    _next_button(dlg).click()
    assert dlg.step == 1
    assert _confirm_table(dlg).rowCount() == 3


def test_intake_scan_of_empty_folder_keeps_next_disabled(qapp, qtbot, monkeypatch) -> None:
    from tether.gui import deeplasi_wizard_ui as ui
    from tether.gui.deeplasi_wizard import DeepLasiWizard

    dlg = _dialog(qtbot)
    empty = DeepLasiWizard(_discovery())

    monkeypatch.setattr(
        ui, "DeepLasiWizard", type("S", (), {"from_directory": staticmethod(lambda d, **k: empty)})
    )
    dlg.set_directory("/empty")
    dlg.scan()

    assert dlg.wizard is empty
    assert not _next_button(dlg).isEnabled()
    assert "No Deep-LASI acquisitions" in _shown_text(dlg)


def test_intake_scan_surfaces_an_unreadable_folder(qapp, qtbot, monkeypatch) -> None:
    from tether.gui import deeplasi_wizard_ui as ui

    dlg = _dialog(qtbot)

    def _boom(directory, *, recursive=False):
        raise OSError("no such directory")

    monkeypatch.setattr(
        ui, "DeepLasiWizard", type("S", (), {"from_directory": staticmethod(_boom)})
    )
    dlg.set_directory("/missing")
    dlg.scan()

    assert dlg.wizard is None
    assert not _next_button(dlg).isEnabled()
    assert "Could not scan" in _shown_text(dlg)


def test_scan_with_no_folder_prompts_and_does_not_build(qapp, qtbot) -> None:
    dlg = _dialog(qtbot)
    dlg.scan()
    assert dlg.wizard is None
    assert "Choose a folder" in _shown_text(dlg)


def _scan_stub(dlg, monkeypatch, wizard, directory="/some/folder"):
    from tether.gui import deeplasi_wizard_ui as ui

    monkeypatch.setattr(
        ui, "DeepLasiWizard", type("S", (), {"from_directory": staticmethod(lambda d, **k: wizard)})
    )
    dlg.set_directory(directory)
    dlg.scan()


def test_editing_the_folder_after_a_scan_invalidates_the_plan(qapp, qtbot, monkeypatch) -> None:
    dlg = _dialog(qtbot)
    _scan_stub(dlg, monkeypatch, _mixed_wizard())
    assert dlg.wizard is not None
    assert _next_button(dlg).isEnabled()

    # Changing the folder text drops the stale plan so Next cannot re-open the old one.
    _folder_edit(dlg).setText("/some/other/folder")
    assert dlg.wizard is None
    assert not _next_button(dlg).isEnabled()
    assert _confirm_table(dlg).rowCount() == 0
    assert "scan again" in _shown_text(dlg).lower()


def test_toggling_recursive_after_a_scan_invalidates_the_plan(qapp, qtbot, monkeypatch) -> None:
    dlg = _dialog(qtbot)
    _scan_stub(dlg, monkeypatch, _mixed_wizard())
    assert _next_button(dlg).isEnabled()

    _recursive_box(dlg).setChecked(True)
    assert dlg.wizard is None
    assert not _next_button(dlg).isEnabled()


# --------------------------------------------------------------------------- #
# editing the plan — every edit routes through the controller
# --------------------------------------------------------------------------- #


def test_mode_edit_routes_to_the_controller(qapp, qtbot) -> None:
    from tether.gui.deeplasi_wizard import WizardMode

    wizard = _mixed_wizard()
    dlg = _dialog(qtbot, wizard=wizard)

    combo = dlg.mode_combo("smd_020")
    combo.setCurrentIndex(combo.findData(WizardMode.SKIP.value))

    _, plan = next((i, p) for i, p in enumerate(wizard.plans) if p.key == "smd_020")
    assert plan.mode is WizardMode.SKIP
    assert "skip 2" in dlg.summary_text()


def test_unsupported_mode_reverts_and_reports_the_reason(qapp, qtbot) -> None:
    from tether.gui.deeplasi_wizard import WizardMode

    wizard = _mixed_wizard()
    dlg = _dialog(qtbot, wizard=wizard)

    # The bare movie-only set cannot reconstruct (no .mat) — the controller rejects it.
    combo = dlg.mode_combo("bare_030")
    combo.setCurrentIndex(combo.findData(WizardMode.RECONSTRUCT.value))

    # The combo reverts to skip and the controller's plan is unchanged.
    assert combo.currentData() == WizardMode.SKIP.value
    plan = next(p for p in wizard.plans if p.key == "bare_030")
    assert plan.mode is WizardMode.SKIP
    assert "cannot reconstruct" in _shown_text(dlg)


def test_switching_off_reconstruct_disables_the_coordinate_combo(qapp, qtbot) -> None:
    from tether.gui.deeplasi_wizard import WizardMode

    wizard = _mixed_wizard()
    dlg = _dialog(qtbot, wizard=wizard)
    coords = dlg.coordinate_combo("recon_010")
    assert coords.isEnabled()

    mode = dlg.mode_combo("recon_010")
    mode.setCurrentIndex(mode.findData(WizardMode.SKIP.value))
    assert not coords.isEnabled()


def test_coordinate_source_edit_routes_to_the_controller(qapp, qtbot) -> None:
    wizard = _mixed_wizard()
    dlg = _dialog(qtbot, wizard=wizard)

    coords = dlg.coordinate_combo("recon_010")
    coords.setCurrentIndex(coords.findData("mat"))

    plan = next(p for p in wizard.plans if p.key == "recon_010")
    assert plan.coordinate_source == "mat"


def test_output_name_edit_is_normalized_by_the_controller(qapp, qtbot) -> None:
    wizard = _mixed_wizard()
    dlg = _dialog(qtbot, wizard=wizard)

    edit = dlg.output_edit("recon_010")
    edit.setText("renamed")
    edit.editingFinished.emit()

    plan = next(p for p in wizard.plans if p.key == "recon_010")
    assert plan.output_name == "renamed.tether"
    assert edit.text() == "renamed.tether"  # the dialog reflects the normalization


def test_empty_output_name_reverts_to_the_current_name(qapp, qtbot) -> None:
    wizard = _mixed_wizard()
    dlg = _dialog(qtbot, wizard=wizard)

    edit = dlg.output_edit("recon_010")
    edit.setText("   ")
    edit.editingFinished.emit()

    plan = next(p for p in wizard.plans if p.key == "recon_010")
    assert plan.output_name == "recon_010.tether"
    assert edit.text() == "recon_010.tether"
    assert "cannot be empty" in _shown_text(dlg)


def test_path_like_output_name_is_rejected_and_reverted(qapp, qtbot) -> None:
    wizard = _mixed_wizard()
    dlg = _dialog(qtbot, wizard=wizard)

    edit = dlg.output_edit("recon_010")
    edit.setText("../escape")  # a traversal attempt must not leave the destination
    edit.editingFinished.emit()

    plan = next(p for p in wizard.plans if p.key == "recon_010")
    assert plan.output_name == "recon_010.tether"  # unchanged
    assert edit.text() == "recon_010.tether"  # reverted
    assert "bare filename" in _shown_text(dlg)


# --------------------------------------------------------------------------- #
# run gating + executor wiring
# --------------------------------------------------------------------------- #


def test_run_is_gated_on_readiness_and_a_destination(qapp, qtbot, tmp_path) -> None:
    dlg = _dialog(qtbot, wizard=_mixed_wizard())
    # Ready (one reconstruct + one analysis-only) but no destination yet → Run disabled.
    assert not _next_button(dlg).isEnabled()
    dlg.set_output_dir(tmp_path)
    assert _next_button(dlg).isEnabled()
    assert _next_button(dlg).text() == "Run"


def test_run_disabled_and_blocking_shown_when_nothing_runnable(qapp, qtbot, tmp_path) -> None:
    from tether.gui.deeplasi_wizard import DeepLasiWizard

    # A single blocked set → no runnable acquisition → not ready.
    wizard = DeepLasiWizard(_discovery(_fileset("bare_030", movie=True)))
    dlg = _dialog(qtbot, wizard=wizard)
    dlg.set_output_dir(tmp_path)
    assert not _next_button(dlg).isEnabled()
    assert "Cannot run yet" in dlg.summary_text()


def _fake_report(output_dir):
    """A duck-typed stand-in for a real :class:`ExecutionReport`.

    The dialog reads the report purely by attribute (``output_dir`` / ``executed`` /
    ``succeeded`` / ``n_ok`` / ``n_failed``, and each entry's ``key`` / ``mode`` /
    ``output_path`` / ``ok`` / ``coordinate_source`` / ``error`` / ``warnings``), so a
    :class:`~types.SimpleNamespace` keeps this GUI test off the heavy importer stack
    the real executor drags in (its own suite covers the concrete dataclasses).
    """
    from types import SimpleNamespace

    from tether.gui.deeplasi_wizard import WizardMode

    ok = SimpleNamespace(
        key="recon_010",
        mode=WizardMode.RECONSTRUCT,
        output_path=Path(output_dir) / "recon_010.tether",
        ok=True,
        coordinate_source="tdat",
        error="",
        warnings=("coordinate fallback applied",),
    )
    bad = SimpleNamespace(
        key="smd_020",
        mode=WizardMode.ANALYSIS_ONLY,
        output_path=Path(output_dir) / "smd_020.tether",
        ok=False,
        coordinate_source="",
        error="ValueError: boom",
        warnings=(),
    )
    executed = (ok, bad)
    succeeded = tuple(e for e in executed if e.ok)
    return SimpleNamespace(
        output_dir=Path(output_dir),
        executed=executed,
        succeeded=succeeded,
        n_ok=len(succeeded),
        n_failed=sum(1 for e in executed if not e.ok),
    )


def test_run_invokes_the_runner_and_renders_the_report(qapp, qtbot, tmp_path) -> None:
    calls: dict = {}

    def _runner(plan, output_dir, *, detect_photobleach):
        calls["plan"] = plan
        calls["output_dir"] = output_dir
        calls["detect_photobleach"] = detect_photobleach
        return _fake_report(output_dir)

    wizard = _mixed_wizard()
    dlg = _dialog(qtbot, wizard=wizard, runner=_runner)
    dlg.set_output_dir(tmp_path)
    _bleach_box(dlg).setChecked(False)

    report = dlg.run()

    # The finalized plan (runnable-only) reached the executor with the chosen options.
    assert calls["output_dir"] == str(tmp_path)
    assert calls["detect_photobleach"] is False
    assert tuple(p.key for p in calls["plan"].acquisitions) == ("recon_010", "smd_020")
    # The report step renders one row per executed acquisition + exposes the produced project.
    assert dlg.step == 2
    assert report is not None
    table = _report_table(dlg)
    assert table.rowCount() == 2
    # Each row reads Acquisition, Mode, Result, Notes; a failed import says so.
    assert _cells(table, 0) == [
        "recon_010",
        "reconstruct",
        "Imported · coords=tdat",
        "coordinate fallback applied",
    ]
    assert _cells(table, 1) == ["smd_020", "analysis_only", "Failed · ValueError: boom", ""]
    assert dlg.produced_projects() == (Path(tmp_path) / "recon_010.tether",)
    assert "Imported 1 of 2" in _shown_text(dlg)
    assert "1 failed" in _shown_text(dlg)


def test_run_with_the_default_checked_box_asks_for_photobleach_detection(
    qapp, qtbot, tmp_path
) -> None:
    calls: dict = {}

    def _runner(plan, output_dir, *, detect_photobleach):
        calls["detect_photobleach"] = detect_photobleach
        return _fake_report(output_dir)

    dlg = _dialog(qtbot, wizard=_mixed_wizard(), runner=_runner)
    dlg.set_output_dir(tmp_path)
    assert _bleach_box(dlg).isChecked()  # the default the user sees

    assert dlg.run() is not None
    assert calls["detect_photobleach"] is True


def test_run_before_ready_returns_none_without_calling_the_runner(qapp, qtbot, tmp_path) -> None:
    from tether.gui.deeplasi_wizard import DeepLasiWizard

    called = False

    def _runner(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("runner must not be called")

    wizard = DeepLasiWizard(_discovery(_fileset("bare_030", movie=True)))  # nothing runnable
    dlg = _dialog(qtbot, wizard=wizard, runner=_runner)
    dlg.set_output_dir(tmp_path)

    assert dlg.run() is None
    assert called is False
    assert dlg.step == 1  # stayed on confirm


def test_run_without_destination_returns_none(qapp, qtbot) -> None:
    def _runner(*args, **kwargs):
        raise AssertionError("runner must not be called without a destination")

    dlg = _dialog(qtbot, wizard=_mixed_wizard(), runner=_runner)
    assert dlg.run() is None
    assert "destination" in _shown_text(dlg).lower()


def test_back_from_confirm_returns_to_intake(qapp, qtbot) -> None:
    dlg = _dialog(qtbot, wizard=_mixed_wizard())
    assert dlg.step == 1
    _button(dlg, "Back").click()
    assert dlg.step == 0


# --------------------------------------------------------------------------- #
# the real executor, not a fake (#746 GUI-F14)
# --------------------------------------------------------------------------- #


def test_run_without_an_injected_runner_uses_the_real_executor(qapp, qtbot, tmp_path) -> None:
    pytest.importorskip("h5py")
    from tether.gui.deeplasi_executor import ExecutionReport
    from tether.gui.deeplasi_wizard import DeepLasiWizard
    from tether.io.intake import AcquisitionFileSet
    from tether.project.analysis_import import read_analysis_only_marker

    txt = Path(__file__).parent / "fixtures" / "deeplasi_traces_slice.txt"
    fileset = AcquisitionFileSet(key="ao_txt", condition_id="cond", video_index="001", txt=txt)
    wizard = DeepLasiWizard(_discovery(fileset))
    dlg = _dialog(qtbot, wizard=wizard)  # no runner: the dialog imports execute_plan
    out = tmp_path / "out"
    dlg.set_output_dir(out)

    report = dlg.run()

    assert isinstance(report, ExecutionReport)
    assert report.n_ok == 1
    written = out / "ao_txt.tether"
    assert dlg.produced_projects() == (written,)
    assert written.is_file()
    marker = read_analysis_only_marker(written)
    assert marker is not None
    assert marker.round_trip_available is False
    table = _report_table(dlg)
    assert table.rowCount() == 1
    assert _cells(table, 0)[:3] == ["ao_txt", "analysis_only", "Imported"]

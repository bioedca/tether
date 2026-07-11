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
executor is faked (its own suite covers the real importers), so no ``.tether`` is
written and the tests stay in the default-plus-Qt matrix.
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


def _fileset(key, *, movie=False, mat=False, tdat=False, txt=False, smd=False):
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


# --------------------------------------------------------------------------- #
# intake step
# --------------------------------------------------------------------------- #


def test_intake_scan_builds_controller_and_enables_next(qapp, qtbot, monkeypatch) -> None:
    from tether.gui import deeplasi_wizard_ui as ui

    dlg = _dialog(qtbot)  # no wizard → opens on intake
    assert dlg.step == 0
    assert not dlg._next_btn.isEnabled()

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
    dlg._recursive_box.setChecked(True)
    dlg.scan()

    assert captured == {"directory": "/some/folder", "recursive": True}
    assert dlg.wizard is built
    assert dlg._next_btn.isEnabled()
    # Advancing lands on the populated confirm table.
    dlg._advance()
    assert dlg.step == 1
    assert dlg._table.rowCount() == 3


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
    assert not dlg._next_btn.isEnabled()
    assert "No Deep-LASI acquisitions" in dlg._intake_status.text()


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
    assert not dlg._next_btn.isEnabled()
    assert "Could not scan" in dlg._intake_status.text()


def test_scan_with_no_folder_prompts_and_does_not_build(qapp, qtbot) -> None:
    dlg = _dialog(qtbot)
    dlg.scan()
    assert dlg.wizard is None
    assert "Choose a folder" in dlg._intake_status.text()


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
    assert "cannot reconstruct" in dlg._status.text()


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
    assert "cannot be empty" in dlg._status.text()


# --------------------------------------------------------------------------- #
# run gating + executor wiring
# --------------------------------------------------------------------------- #


def test_run_is_gated_on_readiness_and_a_destination(qapp, qtbot, tmp_path) -> None:
    dlg = _dialog(qtbot, wizard=_mixed_wizard())
    # Ready (one reconstruct + one analysis-only) but no destination yet → Run disabled.
    assert not dlg._next_btn.isEnabled()
    dlg.set_output_dir(tmp_path)
    assert dlg._next_btn.isEnabled()
    assert dlg._next_btn.text() == "Run"


def test_run_disabled_and_blocking_shown_when_nothing_runnable(qapp, qtbot, tmp_path) -> None:
    from tether.gui.deeplasi_wizard import DeepLasiWizard

    # A single blocked set → no runnable acquisition → not ready.
    wizard = DeepLasiWizard(_discovery(_fileset("bare_030", movie=True)))
    dlg = _dialog(qtbot, wizard=wizard)
    dlg.set_output_dir(tmp_path)
    assert not dlg._next_btn.isEnabled()
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
    dlg._detect_bleach_box.setChecked(False)

    report = dlg.run()

    # The finalized plan (runnable-only) reached the executor with the chosen options.
    assert calls["output_dir"] == str(tmp_path)
    assert calls["detect_photobleach"] is False
    assert tuple(p.key for p in calls["plan"].acquisitions) == ("recon_010", "smd_020")
    # The report step renders one row per executed acquisition + exposes the produced project.
    assert dlg.step == 2
    assert report is not None
    assert dlg._report_table.rowCount() == 2
    assert dlg.produced_projects() == (Path(tmp_path) / "recon_010.tether",)
    assert "Imported 1 of 2" in dlg._report_heading.text()
    assert "1 failed" in dlg._report_heading.text()


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
    assert "destination" in dlg._status.text().lower()


def test_back_from_confirm_returns_to_intake(qapp, qtbot) -> None:
    dlg = _dialog(qtbot, wizard=_mixed_wizard())
    assert dlg.step == 1
    dlg._go_back()
    assert dlg.step == 0

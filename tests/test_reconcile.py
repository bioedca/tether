# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the tMAVEN return-leg reconcile dialog (M2 S7 PR-B, PRD §7.4/§5.3).

All ``@pytest.mark.gui``: they build a real :class:`ReconcileDialog` on the
pytest-qt ``QApplication`` from a synthetic
:class:`~tether.project.handoff.ReconcileReport` (the headless PR-A result type,
constructed directly so the dialog tests need no on-disk ``.tether``) and assert
the per-facet accept toggles, the M4-deferred-class disabling, the idealization
import gate, unmatched surfacing, and that :meth:`ReconcileDialog.decision` maps
to the ``apply_reconcile`` accept-spec. Pixel rendering is left to the live smoke.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
_needs_qt = pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed")

pytestmark = [pytest.mark.gui, _needs_qt]


def _trace(idx, key, *, window=None, cls=None):
    from tether.project.handoff import TraceReconcile

    return TraceReconcile(
        returned_index=idx,
        store_row=idx,
        molecule_key=key,
        molecule_id=f"id-{key}",
        window_change=window,
        class_change=cls,
    )


def _report(traces, *, unmatched=(), has_model=False, model_name="tmaven-import"):
    from tether.project.handoff import ReconcileReport

    return ReconcileReport(
        matched=list(traces),
        unmatched_returned=list(unmatched),
        intensity_quantity="corrected",
        smd_path=Path("ret.hdf5"),
        model_path=Path("model.hdf5") if has_model else None,
        model_name=model_name,
        imported_model_type="vb" if has_model else None,
        imported_nstates=2 if has_model else None,
    )


def _window():
    from tether.project.handoff import WindowChange

    return WindowChange(old=(0, 100), new=(0, 50))


def _applicable_class():
    from tether.project.handoff import ClassChange

    return ClassChange(0, "good", proposed_category="", applicable=True)


def _deferred_class():
    from tether.project.handoff import ClassChange

    return ClassChange(3, "good", proposed_category=None, applicable=False)


def _dialog(qtbot, report):
    from tether.gui.reconcile import ReconcileDialog

    dlg = ReconcileDialog(report)
    qtbot.addWidget(dlg.dialog)
    return dlg


def test_dialog_lists_only_changed_rows(qtbot) -> None:
    # An unchanged matched trace has nothing to reconcile, so it is not shown.
    traces = [
        _trace(0, "a", window=_window()),
        _trace(1, "b", cls=_applicable_class()),
        _trace(2, "c"),  # unchanged
    ]
    dlg = _dialog(qtbot, _report(traces))
    assert [t.molecule_key for t in dlg.change_rows()] == ["a", "b"]


def test_accept_boxes_enabled_per_facet(qtbot) -> None:
    dlg = _dialog(
        qtbot,
        _report([_trace(0, "a", window=_window()), _trace(1, "b", cls=_applicable_class())]),
    )
    # The window box is live only for the trace whose window changed; likewise class.
    assert dlg.window_checkbox("id-a").isEnabled() is True
    assert dlg.class_checkbox("id-a").isEnabled() is False
    assert dlg.window_checkbox("id-b").isEnabled() is False
    assert dlg.class_checkbox("id-b").isEnabled() is True


def test_deferred_class_is_surfaced_but_not_committable(qtbot) -> None:
    dlg = _dialog(qtbot, _report([_trace(0, "a", cls=_deferred_class())]))
    # The non-zero class is shown (transparency) …
    assert [t.molecule_key for t in dlg.change_rows()] == ["a"]
    # … but its Accept box is disabled (needs the M4 category map, §7.6).
    assert dlg.class_checkbox("id-a").isEnabled() is False


def test_decision_collects_checked_ids(qtbot) -> None:
    dlg = _dialog(
        qtbot,
        _report([_trace(0, "a", window=_window()), _trace(1, "b", cls=_applicable_class())]),
    )
    dlg.window_checkbox("id-a").setChecked(True)
    dlg.class_checkbox("id-b").setChecked(True)
    decision = dlg.decision()
    assert decision.accept_windows == ("id-a",)
    assert decision.accept_classes == ("id-b",)
    assert decision.import_idealization is False
    assert decision.is_empty is False


def test_decision_excludes_force_checked_disabled_box(qtbot) -> None:
    # A disabled box can be checked programmatically but must never be committed.
    dlg = _dialog(qtbot, _report([_trace(0, "a", cls=_deferred_class())]))
    dlg.class_checkbox("id-a").setChecked(True)
    assert dlg.decision().accept_classes == ()
    assert dlg.decision().is_empty is True


def test_import_checkbox_reflects_model_presence(qtbot) -> None:
    with_model = _dialog(qtbot, _report([_trace(0, "a", window=_window())], has_model=True))
    assert with_model.import_checkbox.isEnabled() is True
    with_model.import_checkbox.setChecked(True)
    assert with_model.decision().import_idealization is True

    no_model = _dialog(qtbot, _report([_trace(0, "a", window=_window())], has_model=False))
    assert no_model.import_checkbox.isEnabled() is False
    no_model.import_checkbox.setChecked(True)  # force-check a disabled box
    assert no_model.decision().import_idealization is False


def test_select_all_checks_only_committable(qtbot) -> None:
    traces = [
        _trace(0, "a", window=_window()),
        _trace(1, "b", cls=_applicable_class()),
        _trace(2, "c", cls=_deferred_class()),
    ]
    dlg = _dialog(qtbot, _report(traces, has_model=True))
    dlg.select_all()
    decision = dlg.decision()
    assert decision.accept_windows == ("id-a",)
    assert decision.accept_classes == ("id-b",)
    assert decision.import_idealization is True
    # The deferred class row stayed unchecked (its box is disabled).
    assert dlg.class_checkbox("id-c").isChecked() is False


def test_unmatched_traces_are_surfaced(qtbot) -> None:
    from pyqtgraph.Qt import QtWidgets

    dlg = _dialog(qtbot, _report([_trace(0, "a", window=_window())], unmatched=[2, 5]))
    texts = " ".join(lbl.text() for lbl in dlg.dialog.findChildren(QtWidgets.QLabel))
    assert "2, 5" in texts
    assert "matched no store molecule" in texts


def test_empty_report_builds_and_decides_empty(qtbot) -> None:
    dlg = _dialog(qtbot, _report([]))
    assert dlg.change_rows() == []
    assert dlg.decision().is_empty is True


def test_exec_returns_decision_on_accept(qtbot, monkeypatch) -> None:
    from pyqtgraph.Qt import QtWidgets

    dlg = _dialog(qtbot, _report([_trace(0, "a", window=_window())]))
    dlg.window_checkbox("id-a").setChecked(True)
    monkeypatch.setattr(dlg.dialog, "exec", lambda: QtWidgets.QDialog.DialogCode.Accepted)
    decision = dlg.exec()
    assert decision is not None
    assert decision.accept_windows == ("id-a",)


def test_exec_returns_none_on_cancel(qtbot, monkeypatch) -> None:
    from pyqtgraph.Qt import QtWidgets

    dlg = _dialog(qtbot, _report([_trace(0, "a", window=_window())]))
    dlg.window_checkbox("id-a").setChecked(True)
    monkeypatch.setattr(dlg.dialog, "exec", lambda: QtWidgets.QDialog.DialogCode.Rejected)
    assert dlg.exec() is None


def test_dual_change_row_collects_both_facets(qtbot) -> None:
    # A single returning trace can carry BOTH a window edit and an applicable class
    # change; the two per-facet boxes are independent and each is collected under the
    # same molecule_id — so the id lands in both accept lists.
    dlg = _dialog(qtbot, _report([_trace(0, "a", window=_window(), cls=_applicable_class())]))
    assert dlg.window_checkbox("id-a").isEnabled() is True
    assert dlg.class_checkbox("id-a").isEnabled() is True
    dlg.window_checkbox("id-a").setChecked(True)
    dlg.class_checkbox("id-a").setChecked(True)
    decision = dlg.decision()
    assert decision.accept_windows == ("id-a",)
    assert decision.accept_classes == ("id-a",)


def test_decision_excludes_enabled_but_unchecked_box(qtbot) -> None:
    # Both traces have a live window box; only one is checked. decision() must honor
    # isChecked(), not merely isEnabled() — the enabled-but-unchecked box is excluded.
    dlg = _dialog(
        qtbot,
        _report([_trace(0, "a", window=_window()), _trace(1, "b", window=_window())]),
    )
    dlg.window_checkbox("id-a").setChecked(True)  # id-b stays enabled but unchecked
    assert dlg.decision().accept_windows == ("id-a",)

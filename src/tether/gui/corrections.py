# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The summary of a correction pass, as the Corrections menu shows it (#360, PRD §7.2).

**Corrections → Run correction pass…** in the Curate and correct section runs
[`compute_corrected_fret`][tether.project.correct.compute_corrected_fret] over the open
project through [`tether.services.corrections`][], on the window's background-task service
(#343). This module holds what the window shows after the pass:

* [`summary_fields`][] — each field of the
  [`CorrectionSummary`][tether.project.correct.CorrectionSummary], one row each, with the
  correction state after the pass;
* [`summary_banner`][] — the banner, if one is due. ``total_failure`` is ``True`` both when
  no molecule could be corrected and when *Apparent E only* was chosen, so the banner reads
  ``apparent_e_only`` first: the choice gets a note and never the failure banner;
* [`CorrectionSummaryDialog`][] — the non-modal dialog that shows both.

Like the other ``tether.gui`` dialogs, the dialog *holds* a ``QDialog`` and imports Qt in
``__init__``, so the two functions above need no Qt.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyqtgraph.Qt import QtWidgets

    from tether.project.correct import CorrectionSummary
    from tether.services.corrections import CorrectionPassResult

__all__ = [
    "BANNER_CHOICE",
    "BANNER_EMPTY",
    "BANNER_FAILURE",
    "CorrectionSummaryDialog",
    "summary_banner",
    "summary_fields",
]

#: The banner kinds of [`summary_banner`][].
BANNER_FAILURE = "failure"
BANNER_CHOICE = "choice"
BANNER_EMPTY = "empty"


def _yes(value: bool) -> str:
    return "yes" if value else "no"


def summary_fields(result: CorrectionPassResult) -> tuple[tuple[str, str], ...]:
    """Return each field of the pass's summary as a ``(label, value)`` row, in order."""
    s = result.summary
    return (
        ("Molecules examined", str(s.n_molecules)),
        ("Corrected (estimated α and γ)", str(s.n_corrected)),
        ("Corrected (manual α or γ)", str(s.n_manual)),
        ("Apparent E", str(s.n_apparent)),
        ("Total correction failure", _yes(s.total_failure)),
        ("Apparent E only (by choice)", _yes(s.apparent_e_only)),
        ("Source", s.source),
        ("Correction state", result.state),
    )


def summary_banner(summary: CorrectionSummary) -> tuple[str, str] | None:
    """Return the ``(kind, text)`` of the banner a pass's summary needs, or ``None``.

    ``apparent_e_only`` is read before ``total_failure``, as the summary's docstring asks:
    a pass that kept apparent E by choice gives a [`BANNER_CHOICE`][] note, never
    [`BANNER_FAILURE`][]. A project with no molecule to examine gives [`BANNER_EMPTY`][].
    """
    if summary.apparent_e_only:
        return (
            BANNER_CHOICE,
            "Apparent E by choice: Apparent E only was on, so every molecule stays in "
            "apparent E. This is not a correction failure.",
        )
    if summary.n_molecules == 0:
        return (BANNER_EMPTY, "No molecule has frames to correct.")
    if summary.total_failure:
        return (
            BANNER_FAILURE,
            "Corrections unavailable: no molecule has a valid leakage factor α and γ, so "
            "every molecule stays in apparent E. No factor was applied.",
        )
    return None


class CorrectionSummaryDialog:
    """A non-modal dialog that shows one correction pass's summary, field by field.

    ``fields`` maps each label of [`summary_fields`][] to the text shown, and
    ``banner_kind`` is the kind of [`summary_banner`][], or ``None`` when no banner shows.
    """

    def __init__(self, result: CorrectionPassResult, parent: QtWidgets.QWidget | None = None):
        from pyqtgraph.Qt import QtWidgets

        self.result = result
        self.dialog = QtWidgets.QDialog(parent)
        self.dialog.setWindowTitle("Correction pass")
        self.dialog.setObjectName("correction-summary")
        layout = QtWidgets.QVBoxLayout(self.dialog)

        banner = summary_banner(result.summary)
        self.banner_kind: str | None = None if banner is None else banner[0]
        self.banner = QtWidgets.QLabel(self.dialog)
        self.banner.setObjectName("correction-banner")
        self.banner.setWordWrap(True)
        if banner is None:
            self.banner.hide()
        else:
            self.banner.setText(banner[1])
            self.banner.setAccessibleName(
                "Correction failure" if banner[0] == BANNER_FAILURE else "Correction note"
            )
        layout.addWidget(self.banner)

        form = QtWidgets.QFormLayout()
        self.fields: dict[str, str] = {}
        for label, value in summary_fields(result):
            shown = QtWidgets.QLabel(value, self.dialog)
            shown.setAccessibleName(label)
            form.addRow(label, shown)
            self.fields[label] = value
        layout.addLayout(form)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Close, parent=self.dialog
        )
        buttons.rejected.connect(self.dialog.reject)
        layout.addWidget(buttons)

    def show(self) -> None:
        """Show the dialog without blocking the window."""
        self.dialog.show()

    def close(self) -> None:
        """Close the dialog."""
        self.dialog.close()

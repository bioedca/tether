# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Tether PySide6 shell host + curation help/overflow dialogs (PRD §7.3).

:class:`TetherShell` is the minimal ``QMainWindow`` that wires the M2 S2 focus
contract: it hosts the pyqtgraph trace dock (:mod:`tether.gui.trace_dock`) plus
the focus surfaces the curation keys must reach past — a molecule list, a movie
switcher, and the **editable category field** (a text-entry that is exempt from
the keymap, §7.6) — and installs the application-level
:class:`~tether.gui.curation.CurationEventFilter`. The embedded napari movie panel
and the real round-trip navigation land at M2 S3/S4; this shell is deliberately
small so those sessions extend it.

Two companion dialogs complete the §7.3 contract:

* :class:`OverflowCategoryPicker` assigns a category **beyond the first nine**
  (the ``1``–``9`` hotkeys cover only the first nine of the per-condition list),
  returning the chosen integer class.
* :class:`CheatSheetOverlay` renders the shipped, always-current keyboard
  cheat-sheet from the live :class:`~tether.gui.curation.Keymap`.

All three are **composition** wrappers (they *hold* a Qt widget rather than
subclass one) so importing this module costs no Qt, matching the rest of
``tether.gui``. Qt is imported lazily inside ``__init__``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tether.gui.curation import (
    CATEGORY_SLOTS,
    Command,
    CurationAction,
    CurationController,
    CurationEventFilter,
    CurationHandlers,
    Keymap,
)

if TYPE_CHECKING:
    from pyqtgraph.Qt import QtWidgets

    from tether.gui.trace_dock import TraceView

__all__ = ["CheatSheetOverlay", "OverflowCategoryPicker", "TetherShell", "launch"]

_APP_NAME = "Tether"


class OverflowCategoryPicker:
    """Modal picker for assigning a category beyond the ``1``–``9`` hotkeys.

    Given the full ordered per-condition category list, it lists only the entries
    whose integer class exceeds :data:`~tether.gui.curation.CATEGORY_SLOTS`
    (index ``>= 9``) and returns the chosen **integer class** (1-based) — the
    correct integer↔category mapping the ``/labels`` writer stores (§7.3). The
    full ``> 9`` path is re-exercised once the M4 editable list lands.
    """

    def __init__(self, categories: list[str], *, parent: QtWidgets.QWidget | None = None) -> None:
        from pyqtgraph.Qt import QtWidgets

        self._categories = list(categories)
        self._overflow_classes: list[int] = [
            cls for cls in range(1, len(self._categories) + 1) if cls > CATEGORY_SLOTS
        ]

        self._dialog = QtWidgets.QDialog(parent)
        self._dialog.setWindowTitle("Assign category")
        layout = QtWidgets.QVBoxLayout(self._dialog)
        layout.addWidget(QtWidgets.QLabel("Categories beyond the 1–9 hotkeys:"))
        self._list = QtWidgets.QListWidget()
        for cls in self._overflow_classes:
            self._list.addItem(f"{cls}: {self._categories[cls - 1]}")
        layout.addWidget(self._list)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._dialog.accept)
        buttons.rejected.connect(self._dialog.reject)
        self._list.itemDoubleClicked.connect(lambda *_: self._dialog.accept())
        layout.addWidget(buttons)

    @property
    def dialog(self) -> QtWidgets.QDialog:
        return self._dialog

    @property
    def overflow_classes(self) -> list[int]:
        """The integer classes offered (those beyond the first nine)."""
        return list(self._overflow_classes)

    def choose(self, integer_class: int) -> None:
        """Select the row for ``integer_class`` (raises if not an overflow class)."""
        self._list.setCurrentRow(self._overflow_classes.index(integer_class))

    def selected_class(self) -> int | None:
        """The chosen integer class, or ``None`` if nothing is selected."""
        row = self._list.currentRow()
        return self._overflow_classes[row] if 0 <= row < len(self._overflow_classes) else None

    def command(self) -> Command | None:
        """The :class:`Command` for the selection (``ASSIGN_CATEGORY``), or ``None``."""
        cls = self.selected_class()
        return Command(CurationAction.ASSIGN_CATEGORY, cls) if cls is not None else None

    def exec(self) -> int | None:
        """Show modally; return the chosen integer class, or ``None`` if cancelled."""
        from pyqtgraph.Qt import QtWidgets

        accepted = self._dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted
        return self.selected_class() if accepted else None


class CheatSheetOverlay:
    """The shipped keyboard cheat-sheet, rendered from the live keymap (§7.3).

    A two-column ``Key`` / ``Action`` table populated from
    :meth:`~tether.gui.curation.Keymap.cheatsheet`; :meth:`refresh` re-reads the
    keymap so a rebinding is reflected the next time it is shown.
    """

    def __init__(self, keymap: Keymap, *, parent: QtWidgets.QWidget | None = None) -> None:
        from pyqtgraph.Qt import QtWidgets

        self._keymap = keymap
        self._rows: list[tuple[str, str]] = []
        self._dialog = QtWidgets.QDialog(parent)
        self._dialog.setWindowTitle("Keyboard shortcuts")
        layout = QtWidgets.QVBoxLayout(self._dialog)
        self._table = QtWidgets.QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Key", "Action"])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self._table)
        self.refresh()

    @property
    def dialog(self) -> QtWidgets.QDialog:
        return self._dialog

    @property
    def rows(self) -> list[tuple[str, str]]:
        """The ``(key_label, action)`` rows currently displayed."""
        return list(self._rows)

    def refresh(self) -> None:
        """Re-read the keymap and repopulate the table (reflects rebindings)."""
        from pyqtgraph.Qt import QtWidgets

        self._rows = self._keymap.cheatsheet()
        self._table.setRowCount(len(self._rows))
        for row, (key_label, description) in enumerate(self._rows):
            self._table.setItem(row, 0, QtWidgets.QTableWidgetItem(key_label))
            self._table.setItem(row, 1, QtWidgets.QTableWidgetItem(description))
        self._table.resizeColumnsToContents()

    def show(self) -> None:
        self.refresh()
        self._dialog.show()


class TetherShell:
    """The minimal PySide6 shell hosting the trace dock + the focus contract.

    Constructs headlessly (needs a ``QApplication``; ``pyqtgraph.mkQApp`` /
    ``qtbot`` provide one). Installs the curation event filter on the application
    so the four bare keys reach the controller from any of the child surfaces,
    routes each dispatched command to a status-bar message (so the live smoke
    shows keys firing), and exposes the dialogs. Use as a context manager or call
    :meth:`close`.
    """

    def __init__(self, *, categories: list[str] | None = None) -> None:
        from pyqtgraph.Qt import QtCore, QtWidgets

        from tether.gui.trace_dock import TraceDock

        self._categories = (
            list(categories) if categories else [f"category {i}" for i in range(1, 13)]
        )
        self._traces: list[TraceView] = []
        self._current_index = -1

        self._window = QtWidgets.QMainWindow()
        self._window.setWindowTitle(_APP_NAME)

        # Central surface: the pyqtgraph trace dock.
        self._trace_dock = TraceDock()
        self._window.setCentralWidget(self._trace_dock.widget)

        # Left browser panel: movie switcher + molecule list + editable category
        # field. The category field is the text-entry exempt from the keymap.
        panel = QtWidgets.QWidget()
        vbox = QtWidgets.QVBoxLayout(panel)
        self._movie_switcher = QtWidgets.QComboBox()
        self._movie_switcher.addItems(["movie-0"])
        self._molecule_list = QtWidgets.QListWidget()
        self._molecule_list.currentRowChanged.connect(self._on_list_row_changed)
        self._category_field = QtWidgets.QLineEdit()
        self._category_field.setPlaceholderText("category name (text-entry — keymap exempt)")
        self._category_field.setProperty("tetherTextEntry", True)
        vbox.addWidget(QtWidgets.QLabel("Movie"))
        vbox.addWidget(self._movie_switcher)
        vbox.addWidget(QtWidgets.QLabel("Molecules"))
        vbox.addWidget(self._molecule_list, stretch=1)
        vbox.addWidget(QtWidgets.QLabel("Category name"))
        vbox.addWidget(self._category_field)
        browser = QtWidgets.QDockWidget("Browser", self._window)
        browser.setWidget(panel)
        self._window.addDockWidget(QtCore.Qt.DockWidgetArea.LeftDockWidgetArea, browser)

        # Curation controller + keymap + the application-level event filter.
        self._controller = CurationController(self._build_handlers())
        self._keymap = Keymap.default()
        self._event_filter = CurationEventFilter(
            self._controller, self._keymap, focus_dock=self._trace_dock.widget
        )
        self._event_filter.install()

        # Help / overflow actions (menu + the shipped cheat-sheet).
        self._cheatsheet = CheatSheetOverlay(self._keymap, parent=self._window)
        menu = self._window.menuBar().addMenu("&Curation")
        act_help = menu.addAction("Keyboard &shortcuts…")
        act_help.triggered.connect(self.show_cheatsheet)
        act_pick = menu.addAction("Assign category (&overflow)…")
        act_pick.triggered.connect(self.show_overflow_picker)

        self._window.statusBar().showMessage("Ready — Space accept · Backspace reject · Enter jump")

    # --- accessors -----------------------------------------------------------

    @property
    def window(self) -> QtWidgets.QMainWindow:
        return self._window

    @property
    def trace_dock(self) -> Any:
        return self._trace_dock

    @property
    def molecule_list(self) -> QtWidgets.QListWidget:
        return self._molecule_list

    @property
    def movie_switcher(self) -> QtWidgets.QComboBox:
        return self._movie_switcher

    @property
    def category_field(self) -> QtWidgets.QLineEdit:
        return self._category_field

    @property
    def controller(self) -> CurationController:
        return self._controller

    @property
    def keymap(self) -> Keymap:
        return self._keymap

    @property
    def event_filter(self) -> CurationEventFilter:
        return self._event_filter

    @property
    def cheatsheet(self) -> CheatSheetOverlay:
        return self._cheatsheet

    @property
    def status_message(self) -> str:
        return self._window.statusBar().currentMessage()

    # --- molecules -----------------------------------------------------------

    def set_molecules(self, traces: list[TraceView]) -> None:
        """Populate the molecule list + dock from ``traces`` (select the first)."""
        self._traces = list(traces)
        self._molecule_list.clear()
        for i, trace in enumerate(self._traces):
            self._molecule_list.addItem(trace.name or f"mol-{i}")
        if self._traces:
            self._molecule_list.setCurrentRow(0)

    def _on_list_row_changed(self, row: int) -> None:
        if 0 <= row < len(self._traces):
            self._current_index = row
            trace = self._traces[row]
            self._trace_dock.set_trace(trace)
            # Navigation (via the ←/→/↑/↓ keys or a click) shows which molecule is
            # active — so the live smoke sees NEXT/PREV firing, not just accept.
            self._status(f"Molecule {trace.name or f'mol-{row}'} ({row + 1}/{len(self._traces)})")

    # --- dialogs -------------------------------------------------------------

    def show_cheatsheet(self) -> CheatSheetOverlay:
        """Show the shipped keyboard cheat-sheet (refreshed from the live keymap)."""
        self._cheatsheet.show()
        return self._cheatsheet

    def show_overflow_picker(self) -> OverflowCategoryPicker:
        """Open the overflow category picker; on accept, dispatch the assignment."""
        picker = OverflowCategoryPicker(self._categories, parent=self._window)
        chosen = picker.exec()
        if chosen is not None:
            self._controller.dispatch(Command(CurationAction.ASSIGN_CATEGORY, chosen))
        return picker

    # --- handlers (route each command to a visible status message) -----------

    def _build_handlers(self) -> CurationHandlers:
        return CurationHandlers(
            accept=lambda: self._status("Accepted (uncategorized)"),
            reject=lambda: self._status("Rejected"),
            jump=lambda: self._status("Jump to movie spot"),
            idealize=lambda: self._status("Idealize (one-click vbFRET — M2 S6)"),
            next=lambda: self._step(+1),
            prev=lambda: self._step(-1),
            assign_category=self._assign_category,
            clear_category=lambda: self._status("Category cleared (uncategorized)"),
            window_start=lambda d: self._status(f"Analysis-window start {d:+d}"),
            window_end=lambda d: self._status(f"Analysis-window end {d:+d}"),
            reset_window=lambda: self._status("Analysis window reset"),
            photobleach=lambda: self._status("Photobleach detect"),
            grid=lambda: self._status("Grid toggled"),
        )

    def _assign_category(self, integer_class: int) -> None:
        name = (
            self._categories[integer_class - 1]
            if 1 <= integer_class <= len(self._categories)
            else "?"
        )
        self._status(f"Category {integer_class} ({name})")

    def _step(self, delta: int) -> None:
        if not self._traces:
            self._status("Next trace" if delta > 0 else "Previous trace")
            return
        new_row = min(max(self._molecule_list.currentRow() + delta, 0), len(self._traces) - 1)
        self._molecule_list.setCurrentRow(new_row)

    def _status(self, message: str) -> None:
        self._window.statusBar().showMessage(message)

    # --- lifecycle -----------------------------------------------------------

    def show(self) -> None:
        self._window.show()

    def close(self) -> None:
        """Remove the app-level filter and close the window."""
        self._event_filter.remove()
        self._trace_dock.close()
        self._window.close()

    def __enter__(self) -> TetherShell:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()


def launch() -> None:  # pragma: no cover - interactive smoke entry point
    """Launch the shell with synthetic traces (computer-use live-smoke entry).

    Not part of the headless test surface; run via ``python -m tether.gui.shell``
    to exercise the focus contract by hand / with the computer-use MCP.
    """
    import numpy as np
    from pyqtgraph.Qt import QtWidgets

    from tether.gui.trace_dock import TraceView

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setApplicationName(_APP_NAME)
    app.setOrganizationName("MondragonLab")

    rng = np.random.default_rng(0)
    traces = []
    for i in range(6):
        n = 200
        donor = 500 + 200 * np.sin(np.linspace(0, 6, n)) + rng.normal(0, 20, n)
        acceptor = 500 - 200 * np.sin(np.linspace(0, 6, n)) + rng.normal(0, 20, n)
        traces.append(TraceView(donor=donor, acceptor=acceptor, frame_time=0.1, name=f"mol-{i}"))

    shell = TetherShell()
    shell.set_molecules(traces)
    shell.show()
    app.exec()


if __name__ == "__main__":  # pragma: no cover
    launch()

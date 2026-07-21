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

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
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
    from collections.abc import Callable
    from os import PathLike
    from typing import Protocol

    import numpy as np
    from pyqtgraph.Qt import QtWidgets

    from tether.analysis.histogram import Histogram1D
    from tether.analysis.overlap import OverlapInfo
    from tether.gui.reconcile import ReconcileDecision
    from tether.gui.trace_dock import TraceView
    from tether.project.core import Project
    from tether.project.handoff import AppliedReconcile, HandoffManifest, ReconcileReport

    #: Maps a molecule_key to that molecule's per-frame idealized FRET path (NaN
    #: outside its analysis window), or ``None`` when nothing was produced. The
    #: shell's ``I`` handler draws the result on the dock; :func:`make_store_idealizer`
    #: is the store-backed default that runs the real one-click vbFRET pipeline.
    Idealizer = Callable[[str], np.ndarray | None]

    #: Recompute + return the pooled population apparent-E :class:`Histogram1D` the
    #: shell's ``&Analysis`` menu draws, or ``None`` when there is nothing to show.
    #: :func:`make_store_histogram` is the store-backed default that runs the real
    #: :func:`tether.analysis.histogram.population_apparent_e_histogram` each call,
    #: so the histogram reflects the current curation state (§7.5/§7.7).
    HistogramSeam = Callable[[], Histogram1D | None]

    #: Resolve a molecule_key to its static-overlap-view payload — the cached patch
    #: + nearest-neighbour distance + apertures-overlap flag — or ``None`` for an
    #: unknown key. :func:`make_store_overlap` is the store-backed default that
    #: computes it once from ``/molecules`` coordinates + ``/patches`` (§7.3/§5.1).
    OverlapSeam = Callable[[str], OverlapInfo | None]

    class HandoffSeam(Protocol):
        """The store hand-off operations the shell's ``&Hand-off`` menu drives.

        :func:`make_store_handoff` is the store-backed default; a test can inject a
        fake so the menu wiring is exercised without an on-disk ``.tether``.
        """

        def hand_off(
            self, molecule_keys: list[str] | None, out_path: str | PathLike[str]
        ) -> HandoffManifest: ...

        def preview(
            self, smd_path: str | PathLike[str], *, model_path: str | PathLike[str] | None = None
        ) -> ReconcileReport: ...

        def apply(
            self,
            smd_path: str | PathLike[str],
            decision: ReconcileDecision,
            *,
            model_path: str | PathLike[str] | None = None,
        ) -> AppliedReconcile: ...


__all__ = [
    "CheatSheetOverlay",
    "OverflowCategoryPicker",
    "TetherShell",
    "launch",
    "make_store_handoff",
    "make_store_histogram",
    "make_store_idealizer",
    "make_store_overlap",
]

_APP_NAME = "Tether"

#: How often (ms) the main thread polls the background idealize fit for completion.
_IDEALIZE_POLL_MS = 25

#: File filter for the hand-off SMD / model open+save dialogs (tMAVEN uses HDF5).
_SMD_FILTER = "tMAVEN SMD (*.hdf5 *.smd);;All files (*)"

#: File filter for the ``&File → Open project…`` dialog (a ``.tether`` HDF5 store).
_TETHER_FILTER = "Tether project (*.tether);;All files (*)"


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
        """The wrapped modal ``QDialog`` listing the overflow categories."""
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
        """The wrapped ``QDialog`` holding the ``Key``/``Action`` cheat-sheet table."""
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
    shows keys firing), and exposes the dialogs. The ``I`` key runs one-click
    idealize on the selected molecule through the injected ``idealizer`` seam
    (:func:`make_store_idealizer` wires the real store-backed vbFRET pipeline) and
    draws the returned Viterbi path on the dock. When an ``overlap`` seam is wired
    (:func:`make_store_overlap`), a right-hand static overlap dock shows the selected
    molecule's cached patch + nearest-neighbour distance, refreshed on selection
    (§7.3). Use as a context manager or call :meth:`close`.
    """

    def __init__(
        self,
        *,
        categories: list[str] | None = None,
        idealizer: Idealizer | None = None,
        handoff: HandoffSeam | None = None,
        histogram: HistogramSeam | None = None,
        overlap: OverlapSeam | None = None,
        conditions: Project | None = None,
    ) -> None:
        from pyqtgraph.Qt import QtCore, QtWidgets

        from tether.gui.trace_dock import TraceDock

        self._categories = (
            list(categories) if categories else [f"category {i}" for i in range(1, 13)]
        )
        self._traces: list[TraceView] = []
        self._current_index = -1
        # The one-click-idealize seam: a molecule_key -> idealized-path callable.
        # None (the synthetic/no-project default) makes ``I`` report that a project
        # must be loaded; make_store_idealizer(project) wires the real pipeline.
        self._idealizer = idealizer
        # The standalone-tMAVEN hand-off seam (export + return-leg import). None (no
        # project) makes the &Hand-off menu report that a project must be loaded;
        # make_store_handoff(project) wires the real tether.project.handoff pipeline.
        self._handoff = handoff
        # The population-histogram seam. None (no project) makes the &Analysis menu
        # report that a project must be loaded; make_store_histogram(project) wires
        # the real population_apparent_e_histogram. The dock is built lazily on the
        # first show_histogram so the default shell stays light.
        self._histogram_seam = histogram
        self._histogram_dock: Any | None = None
        self._histogram_dock_widget: Any | None = None
        # The static overlap-view seam: molecule_key -> patch + NN distance. None (no
        # project) leaves the dock unbuilt; make_store_overlap(project) wires the real
        # per-molecule neighbour report. The dock is built lazily on the first
        # selection with an overlap seam so the default shell stays light, and it is
        # refreshed from _on_list_row_changed as the curator moves between molecules.
        self._overlap_seam = overlap
        self._overlap_dock: Any | None = None
        self._overlap_dock_widget: Any | None = None
        # The condition-validation seam: the .tether Project whose provisional
        # per-molecule condition_ids the &Conditions menu confirms/corrects (§7.6).
        # None (no project) makes the menu report that a project must be loaded; a real
        # Project wires ConditionValidationDialog over the headless re-key core (§5.1).
        self._conditions = conditions
        # The fit runs on a background worker (the sidecar can block for a cold-JIT
        # first run); a main-thread QTimer polls the future so the GUI stays live and
        # the overlay draw + status update always happen on the main thread.
        self._idealize_executor = ThreadPoolExecutor(max_workers=1)
        self._idealize_future: Any | None = None
        self._idealize_timer: Any | None = None
        self._idealize_key: str | None = None

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

        # File menu: open a produced/extracted .tether live in this shell for
        # curation + one-click idealize — the store<->shell hookup (§7.8, FR-LEGACY;
        # the general capability the M7 Deep-LASI wizard hands off into).
        self._file_menu = self._window.menuBar().addMenu("&File")
        self._act_open_project = self._file_menu.addAction("&Open project…")
        self._act_open_project.triggered.connect(self._open_project_dialog)

        # Help / overflow actions (menu + the shipped cheat-sheet).
        self._cheatsheet = CheatSheetOverlay(self._keymap, parent=self._window)
        menu = self._window.menuBar().addMenu("&Curation")
        act_help = menu.addAction("Keyboard &shortcuts…")
        act_help.triggered.connect(self.show_cheatsheet)
        act_pick = menu.addAction("Assign category (&overflow)…")
        act_pick.triggered.connect(self.show_overflow_picker)

        # Hand-off menu: the standalone-tMAVEN round trip (§7.4) — export the
        # selection as an SMD, and re-import a returning SMD (+ optional model)
        # through the per-trace reconcile prompt.
        self._handoff_menu = self._window.menuBar().addMenu("&Hand-off")
        self._act_hand_off = self._handoff_menu.addAction("Hand to &tMAVEN…")
        self._act_hand_off.triggered.connect(self._hand_off_dialog)
        self._act_import = self._handoff_menu.addAction("&Import return leg…")
        self._act_import.triggered.connect(self._import_dialog)

        # Analysis menu: the population apparent-E histogram (§7.7, Appendix C A1).
        self._analysis_menu = self._window.menuBar().addMenu("&Analysis")
        self._act_histogram = self._analysis_menu.addAction("Population &histogram…")
        self._act_histogram.triggered.connect(self.show_histogram)

        # Conditions menu: the M4 annotation step — confirm/correct the provisional
        # per-molecule condition_id, with the transactional re-key + human-confirmed
        # merge (§5.1/§7.6).
        self._conditions_menu = self._window.menuBar().addMenu("&Conditions")
        self._act_validate_conditions = self._conditions_menu.addAction("Validate &conditions…")
        self._act_validate_conditions.triggered.connect(self._validate_conditions_dialog)

        # Legacy menu: the M7 Deep-LASI re-analysis entry point — import a folder of
        # Deep-LASI acquisition files (movie + .tdat/.mat/.txt/SMD) into round-trip
        # or analysis-only .tether projects through the wizard (§7.8, FR-LEGACY).
        self._legacy_menu = self._window.menuBar().addMenu("&Legacy")
        self._act_import_deeplasi = self._legacy_menu.addAction("&Import Deep-LASI bundle…")
        self._act_import_deeplasi.triggered.connect(self._import_deeplasi_dialog)

        self._window.statusBar().showMessage("Ready — Space accept · Backspace reject · Enter jump")

    # --- accessors -----------------------------------------------------------

    @property
    def window(self) -> QtWidgets.QMainWindow:
        """The wrapped ``QMainWindow`` hosting the docks, menu bar, and status bar."""
        return self._window

    @property
    def trace_dock(self) -> Any:
        """The :class:`~tether.gui.trace_dock.TraceDock` occupying the window's central widget."""
        return self._trace_dock

    @property
    def molecule_list(self) -> QtWidgets.QListWidget:
        """The browser-panel ``QListWidget`` whose current row is the selected molecule."""
        return self._molecule_list

    @property
    def movie_switcher(self) -> QtWidgets.QComboBox:
        """The browser-panel ``QComboBox`` of movie ids (a keymap-reachable focus surface)."""
        return self._movie_switcher

    @property
    def category_field(self) -> QtWidgets.QLineEdit:
        """The browser-panel category-name ``QLineEdit`` — the text-entry exempt from the keymap."""
        return self._category_field

    @property
    def controller(self) -> CurationController:
        """The :class:`~tether.gui.curation.CurationController` each command is routed to."""
        return self._controller

    @property
    def keymap(self) -> Keymap:
        """The live :class:`~tether.gui.curation.Keymap` resolving key chords to commands."""
        return self._keymap

    @property
    def event_filter(self) -> CurationEventFilter:
        """The application-level :class:`CurationEventFilter` this shell installs (§7.3)."""
        return self._event_filter

    @property
    def cheatsheet(self) -> CheatSheetOverlay:
        """The :class:`CheatSheetOverlay` this shell shows for ``Keyboard shortcuts…``."""
        return self._cheatsheet

    @property
    def status_message(self) -> str:
        """The message currently displayed in the window's status bar."""
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
            # Refresh the overlap view last, so a seam failure surfaces (overwriting
            # the navigation status) rather than being clobbered by it; a successful
            # refresh sets no status, leaving the molecule message in place.
            self._refresh_overlap(trace)

    @property
    def overlap_dock(self) -> Any | None:
        """The static overlap-view dock, or ``None`` until first shown."""
        return self._overlap_dock

    def _refresh_overlap(self, trace: TraceView) -> None:
        """Update the overlap view for the selected molecule (§7.3, no-op when unwired).

        Runs the injected ``overlap`` seam (:func:`make_store_overlap` computes the
        per-molecule neighbour report + patch once), building the dock lazily on the
        first selection that resolves. A molecule with no ``molecule_key`` (the
        synthetic/no-project shell) or a seam failure surfaces as a status message
        and leaves the last view in place — never a crash out of a selection change.
        """
        if self._overlap_seam is None or trace.molecule_key is None:
            return
        try:
            info = self._overlap_seam(trace.molecule_key)
        except Exception as exc:  # noqa: BLE001 - keep the GUI alive, report the cause
            self._status(f"Overlap view failed: {exc}")
            return
        dock = self._overlap_dock
        newly_built = dock is None
        if newly_built:
            from tether.gui.overlap_dock import OverlapDock

            dock = OverlapDock()
        try:
            dock.set_molecule(info)
        except Exception as exc:  # noqa: BLE001 - a malformed payload must not crash
            # Mirror show_histogram: a seam that resolves but returns a shape-invalid
            # patch surfaces as a status message, never an uncaught exception out of
            # a selection change. A dock that fails its very first draw is discarded
            # so no blank dock is ever attached; an already-shown dock is kept.
            self._status(f"Overlap view failed: {exc}")
            if newly_built:
                dock.close()
            return
        if newly_built:
            self._overlap_dock = dock
            self._attach_overlap_dock()

    def _attach_overlap_dock(self) -> None:
        """Dock the (lazily-built) overlap view into the right of the window."""
        from pyqtgraph.Qt import QtCore, QtWidgets

        dock_widget = QtWidgets.QDockWidget("Overlap", self._window)
        dock_widget.setWidget(self._overlap_dock.widget)
        self._window.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, dock_widget)
        self._overlap_dock_widget = dock_widget

    # --- project (open a produced / extracted .tether live, §7.8) ------------

    @property
    def file_menu(self) -> QtWidgets.QMenu:
        """The ``&File`` menu (open a ``.tether`` project live in the shell)."""
        return self._file_menu

    def load_project(
        self, path: str | PathLike[str], *, intensity_quantity: str = "corrected"
    ) -> Project | None:
        """Open a produced / extracted ``.tether`` live in this shell (§7.8, FR-LEGACY).

        Re-wires the running shell's store-backed seams onto ``path`` — the one-click
        idealize (``I``), the population histogram (``&Analysis``), the standalone-tMAVEN
        hand-off (``&Hand-off``), the per-molecule overlap view, and the ``&Conditions``
        validation target — then loads the store's molecules into the browser + trace
        dock (:meth:`set_molecules`). This is the general store↔shell hookup (the
        M2-deferred capability, reusable beyond legacy): a wizard-produced project
        (:meth:`import_deeplasi_bundle`) or any extracted ``.tether`` opens live without
        relaunching.

        Every :class:`~tether.gui.trace_dock.TraceView` carries its ``molecule_key`` so
        the idealize / overlap seams resolve the store row. An **analysis-only** import
        (coordinate-less, ``read_analysis_only_marker`` non-``None``) leaves the overlap
        seam unwired — the spot/overlap view is meaningless without coordinates/patches —
        and surfaces the one-time marker banner. All reads happen before any state is
        mutated, so a failed open (missing / incompatible / unreadable ``.tether``)
        leaves the prior project in place and only reports a status message — the shell
        never crashes on an open. Returns the opened :class:`~tether.project.core.Project`
        (``None`` on failure).

        The curation category vocabulary, the movie switcher, and the napari movie panel
        / round-trip navigation are **not** re-wired here (the movie panel + round-trip
        land at M2 S3/S4, and the per-condition category list is edited through the
        ``&Conditions`` dialog); this loader wires the trace-level curate + analyze
        surfaces the produced project supports today.
        """
        from tether.project.analysis_import import read_analysis_only_marker
        from tether.project.core import Project

        # Do every fallible read BEFORE touching shell state, so a bad open is atomic
        # (the prior project stays wired) — mirroring the catch-and-report contract of
        # the hand-off / import menu methods.
        try:
            project = Project.open(path)
            proj_path = project.path
            marker = read_analysis_only_marker(proj_path)
            analysis_only = marker is not None
            traces = traces_from_store(project, intensity_quantity=intensity_quantity)
            idealizer = make_store_idealizer(project)
            histogram = make_store_histogram(project)
            handoff = make_store_handoff(project)
            overlap = None if analysis_only else make_store_overlap(project)
        except Exception as exc:  # noqa: BLE001 - keep the GUI alive, report the cause
            self._status(f"Open project failed: {exc}")
            return None

        self._idealizer = idealizer
        self._histogram_seam = histogram
        self._handoff = handoff
        self._overlap_seam = overlap
        self._conditions = project
        # Drop any docks built against a prior project so they rebuild fresh against the
        # new store (no stale histogram / overlap leaking across a re-open).
        self._reset_store_docks()
        # set_molecules selects row 0 → _on_list_row_changed refreshes the overlap dock
        # and sets a per-molecule status; overwrite it with the open outcome afterwards.
        self.set_molecules(traces)

        name = Path(proj_path).name
        if analysis_only:
            self._status(f"Opened {name} (analysis-only) — {marker.banner}")
        else:
            self._status(f"Opened {name} — {len(traces)} molecule(s)")
        return project

    def _reset_store_docks(self) -> None:
        """Discard the lazily-built histogram / overlap docks so a re-open rebuilds them."""
        for dock_attr, widget_attr in (
            ("_histogram_dock", "_histogram_dock_widget"),
            ("_overlap_dock", "_overlap_dock_widget"),
        ):
            widget = getattr(self, widget_attr)
            if widget is not None:
                self._window.removeDockWidget(widget)
            dock = getattr(self, dock_attr)
            if dock is not None:
                dock.close()
            setattr(self, dock_attr, None)
            setattr(self, widget_attr, None)

    def _open_project_dialog(self) -> None:  # pragma: no cover - interactive file dialog
        """Menu entry: pick a ``.tether`` project, then open it live (§7.8)."""
        from pyqtgraph.Qt import QtWidgets

        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self._window, "Open .tether project", "", _TETHER_FILTER
        )
        if not path:
            self._status("Open project cancelled")
            return
        self.load_project(path)

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

    # --- hand-off (standalone-tMAVEN round trip, §7.4/§5.3) ------------------

    @property
    def handoff_menu(self) -> QtWidgets.QMenu:
        """The ``&Hand-off`` menu (export + return-leg import)."""
        return self._handoff_menu

    def hand_off_to_tmaven(
        self, out_path: str | PathLike[str], *, molecule_keys: list[str] | None = None
    ) -> HandoffManifest | None:
        """Export molecules to an SMD the standalone tMAVEN GUI opens (``None`` = all).

        Returns the manifest, or ``None`` (with a status message) when no project is
        loaded or the export fails — the shell must never crash on a hand-off.
        """
        if self._handoff is None:
            self._status("Hand-off: load a project with extracted molecules first")
            return None
        try:
            manifest = self._handoff.hand_off(molecule_keys, out_path)
        except Exception as exc:  # noqa: BLE001 - keep the GUI alive, report the cause
            self._status(f"Hand-off failed: {exc}")
            return None
        self._status(f"Handed off {manifest.n_molecules} molecule(s) to {Path(out_path).name}")
        return manifest

    def import_return_leg(
        self, smd_path: str | PathLike[str], *, model_path: str | PathLike[str] | None = None
    ) -> AppliedReconcile | None:
        """Preview a returning SMD, show the reconcile prompt, apply the accepted subset.

        Returns what was committed, or ``None`` when no project is loaded, the preview
        fails, the user cancels, or the commit fails (each surfaced as a status message,
        never a crash). ``model_path`` (a returning tMAVEN model) enables the dialog's
        idealization-import option; the same path threads through preview and apply.
        """
        from tether.gui.reconcile import ReconcileDialog

        if self._handoff is None:
            self._status("Import: load a project with extracted molecules first")
            return None
        try:
            report = self._handoff.preview(smd_path, model_path=model_path)
        except Exception as exc:  # noqa: BLE001 - keep the GUI alive, report the cause
            self._status(f"Return-leg preview failed: {exc}")
            return None
        decision = ReconcileDialog(report, parent=self._window).exec()
        if decision is None:
            self._status("Return-leg import cancelled")
            return None
        try:
            applied = self._handoff.apply(smd_path, decision, model_path=model_path)
        except Exception as exc:  # noqa: BLE001 - keep the GUI alive, report the cause
            self._status(f"Return-leg import failed: {exc}")
            return None
        self._status(_applied_summary(applied))
        return applied

    def _hand_off_dialog(self) -> None:  # pragma: no cover - interactive file dialog
        """Menu entry: pick a destination SMD, then hand off every extracted molecule."""
        from pyqtgraph.Qt import QtWidgets

        if self._handoff is None:
            self._status("Hand-off: load a project with extracted molecules first")
            return
        out_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self._window, "Hand to tMAVEN (SMD)", "", _SMD_FILTER
        )
        if out_path:
            self.hand_off_to_tmaven(out_path)
        else:
            self._status("Hand-off cancelled")

    def _import_dialog(self) -> None:  # pragma: no cover - interactive file dialog
        """Menu entry: pick the returning SMD (+ optional model), then reconcile."""
        from pyqtgraph.Qt import QtWidgets

        if self._handoff is None:
            self._status("Import: load a project with extracted molecules first")
            return
        smd_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self._window, "Import returning SMD", "", _SMD_FILTER
        )
        if not smd_path:
            self._status("Return-leg import cancelled")
            return
        model_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self._window, "tMAVEN model to import (optional — Cancel to skip)", "", _SMD_FILTER
        )
        self.import_return_leg(smd_path, model_path=model_path or None)

    # --- conditions (M4 annotation: confirm/correct provisional ids, §7.6) ---

    @property
    def conditions_menu(self) -> QtWidgets.QMenu:
        """The ``&Conditions`` menu (condition validation / confirm-correct / merge)."""
        return self._conditions_menu

    def _validate_conditions_dialog(self) -> None:  # pragma: no cover - interactive dialog
        """Menu entry: open the condition validation / confirm-correct / merge dialog (§7.6)."""
        from tether.gui.conditions import ConditionValidationDialog

        if self._conditions is None:
            self._status("Conditions: load a project first")
            return
        try:
            ConditionValidationDialog(self._conditions, parent=self._window).exec()
        except Exception as exc:  # noqa: BLE001 - keep the GUI alive, report the cause
            self._status(f"Conditions validation failed: {exc}")
            return
        self._status("Conditions validation closed")

    # --- legacy (M7 Deep-LASI re-analysis bundle import, §7.8) ---------------

    @property
    def legacy_menu(self) -> QtWidgets.QMenu:
        """The ``&Legacy`` menu (Deep-LASI bundle re-analysis import, §7.8)."""
        return self._legacy_menu

    def _new_deeplasi_dialog(self) -> Any:  # pragma: no cover - builds the real dialog
        """Construct the real Deep-LASI wizard dialog parented to the shell window."""
        from tether.gui.deeplasi_wizard_ui import DeepLasiWizardDialog

        return DeepLasiWizardDialog(parent=self._window)

    def import_deeplasi_bundle(
        self, *, dialog_factory: Callable[[], Any] | None = None
    ) -> tuple[Path, ...]:
        """Run the Deep-LASI re-analysis wizard, then report + return the written projects.

        Opens the :class:`~tether.gui.deeplasi_wizard_ui.DeepLasiWizardDialog` — which
        owns the whole flow (folder intake → per-acquisition plan confirmation →
        reconstruct / analysis-only import) — then reports the written ``.tether``
        project paths in the status bar and returns them (§7.8, FR-LEGACY). The
        ``dialog_factory`` seam defaults to :meth:`_new_deeplasi_dialog` (the real
        modal dialog parented to the shell); a test injects a stand-in to drive the
        report path headlessly without a modal event loop. A dialog that raises is
        caught and surfaced as a status message so the shell stays alive; the return
        is the produced paths — empty when the user cancels, writes nothing, or the
        dialog fails.
        """
        factory = dialog_factory or self._new_deeplasi_dialog
        try:
            produced = tuple(factory().exec())
        except Exception as exc:  # noqa: BLE001 - keep the GUI alive, report the cause
            self._status(f"Deep-LASI import failed: {exc}")
            return ()
        if produced:
            names = ", ".join(Path(p).name for p in produced)
            self._status(f"Deep-LASI import: wrote {len(produced)} project(s) — {names}")
            # Close the round-trip loop: when the wizard wrote exactly one project, open
            # it live for curate/idealize (the §7.8 "browse/curate/idealize round-trip
            # live" clause). With several written, which to open is the curator's call —
            # report them and leave &File → Open project… as the picker. load_project is
            # fail-soft (a status message, never a crash), so the produced paths are
            # always returned regardless of the live-open outcome.
            if len(produced) == 1:
                self.load_project(produced[0])
        else:
            self._status("Deep-LASI import: no projects written")
        return produced

    def _import_deeplasi_dialog(self) -> None:  # pragma: no cover - interactive dialog
        """Menu entry: run the Deep-LASI re-analysis wizard (intake → import), §7.8."""
        self.import_deeplasi_bundle()

    # --- analysis (population apparent-E histogram, §7.7) --------------------

    @property
    def analysis_menu(self) -> QtWidgets.QMenu:
        """The ``&Analysis`` menu (population histogram)."""
        return self._analysis_menu

    @property
    def histogram_dock(self) -> Any | None:
        """The population-histogram dock, or ``None`` until first shown."""
        return self._histogram_dock

    def show_histogram(self) -> Any | None:
        """Compute + display the population apparent-E histogram (§7.7, §9 M2).

        Runs the injected ``histogram`` seam (:func:`make_store_histogram` wires the
        real :func:`~tether.analysis.histogram.population_apparent_e_histogram` over
        the bound project, recomputed each call so it tracks curation), draws the
        result in a bottom :class:`~tether.gui.histogram_dock.HistogramDock` (built
        lazily on first use), and returns the dock. No wired seam, a failing
        computation, or a ``None`` result each surface as a status message and
        return ``None`` — the shell never crashes on analysis. An *empty* histogram
        (zero accepted molecules) is still drawn: a flat baseline is the honest "no
        data" answer, not an error.
        """
        if self._histogram_seam is None:
            self._status("Histogram: load a project with extracted molecules first")
            return None
        try:
            hist = self._histogram_seam()
        except Exception as exc:  # noqa: BLE001 - keep the GUI alive, report the cause
            self._status(f"Histogram failed: {exc}")
            return None
        if hist is None:
            self._status("Histogram: nothing to pool")
            return None
        dock = self._histogram_dock
        newly_built = dock is None
        if newly_built:
            from tether.gui.histogram_dock import HistogramDock

            dock = HistogramDock()
        try:
            dock.set_histogram(hist)
        except Exception as exc:  # noqa: BLE001 - a malformed result must not crash
            # Mirrors _poll_idealize: an injected seam returning a shape-invalid
            # Histogram1D surfaces as a status message, never an uncaught exception
            # out of the &Analysis menu action. A dock that fails its very first draw
            # is discarded, so no blank dock is ever adopted/attached to the window
            # (consistent with the no-seam / seam-raises / None-result paths); an
            # already-shown dock is kept, so a later failure leaves the last good view.
            self._status(f"Histogram failed: {exc}")
            if newly_built:
                dock.close()
            return None
        if newly_built:
            self._histogram_dock = dock
            self._attach_histogram_dock()
        self._histogram_dock_widget.show()
        self._histogram_dock_widget.raise_()
        n_mol = hist.n_molecules if hist.n_molecules is not None else 0
        self._status(f"Population histogram — {n_mol} molecule(s), {hist.n_samples} frame(s)")
        return dock

    def _attach_histogram_dock(self) -> None:
        """Dock the (already-built, successfully-drawn) histogram into the window."""
        from pyqtgraph.Qt import QtCore, QtWidgets

        dock_widget = QtWidgets.QDockWidget("Population histogram", self._window)
        dock_widget.setWidget(self._histogram_dock.widget)
        self._window.addDockWidget(QtCore.Qt.DockWidgetArea.BottomDockWidgetArea, dock_widget)
        self._histogram_dock_widget = dock_widget

    # --- handlers (route each command to a visible status message) -----------

    def _build_handlers(self) -> CurationHandlers:
        return CurationHandlers(
            accept=lambda: self._status("Accepted (uncategorized)"),
            reject=lambda: self._status("Rejected"),
            jump=lambda: self._status("Jump to movie spot"),
            idealize=self._idealize_current,
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

    @property
    def is_idealizing(self) -> bool:
        """Whether a background one-click-idealize fit is currently running."""
        return self._idealize_future is not None

    def _idealize_current(self) -> None:
        """One-click idealize the selected molecule and draw its step overlay (``I``, §7.4).

        The thin GUI layer over :func:`tether.project.idealize.idealize_molecules`
        (M2 S6 PR-A): resolve the selected list row to its ``molecule_key`` and run
        the injected :data:`Idealizer` (the store-backed one built by
        :func:`make_store_idealizer` runs the real SMD→vbFRET→write pipeline). The
        fit runs on a **background worker** and a main-thread timer polls it, so the
        GUI stays responsive during a long (cold-JIT) fit; when it finishes the
        Viterbi path is drawn on the dock — always on the main thread. A missing
        selection, no wired idealizer, an already-running fit, an empty result, or a
        failing/length-mismatched fit each surface as a status message, never a crash.
        """
        from pyqtgraph.Qt import QtCore, QtWidgets

        if self._idealize_future is not None:
            self._status("Idealize: a fit is already running")
            return
        row = self._molecule_list.currentRow()
        trace = self._traces[row] if 0 <= row < len(self._traces) else None
        if trace is None:
            self._status("Idealize: select a molecule first")
            return
        if trace.molecule_key is None or self._idealizer is None:
            self._status("Idealize: load a project with extracted molecules first")
            return
        key = trace.molecule_key
        self._idealize_key = key
        self._status(f"Idealizing {key} (one-click vbFRET)…")
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
        self._idealize_future = self._idealize_executor.submit(self._idealizer, key)
        self._idealize_timer = QtCore.QTimer()
        self._idealize_timer.setInterval(_IDEALIZE_POLL_MS)
        self._idealize_timer.timeout.connect(self._poll_idealize)
        self._idealize_timer.start()

    def _poll_idealize(self) -> None:
        """Main-thread poll: when the background fit is done, draw it or report why."""
        future = self._idealize_future
        if future is None or not future.done():
            return
        key = self._idealize_key
        self._finish_idealize()  # stop the timer, clear state, restore the cursor
        try:
            idealized = future.result()
        except Exception as exc:  # noqa: BLE001 - keep the GUI alive, report the cause
            self._status(f"Idealize failed for {key}: {exc}")
            return
        if idealized is None:
            self._status(f"Idealize: no idealization produced for {key}")
            return
        row = self._molecule_list.currentRow()
        current = self._traces[row].molecule_key if 0 <= row < len(self._traces) else None
        if current != key:
            # The curator navigated away while the fit ran; the result is for a
            # different molecule, so don't draw it on the one now on screen.
            self._status(f"Idealization for {key} ready (selection moved on)")
            return
        try:
            self._trace_dock.set_idealization(idealized)
        except Exception as exc:  # noqa: BLE001 - a mismatched result must not crash
            self._status(f"Idealize failed for {key}: {exc}")
            return
        self._status(f"Idealized {key} (one-click vbFRET)")

    def _finish_idealize(self) -> None:
        """Tear down the poll timer + fit state and restore the wait cursor."""
        from pyqtgraph.Qt import QtWidgets

        if self._idealize_timer is not None:
            self._idealize_timer.stop()
            self._idealize_timer = None
        self._idealize_future = None
        self._idealize_key = None
        QtWidgets.QApplication.restoreOverrideCursor()

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
        """Remove the app-level filter, abandon any running fit, and close the window."""
        from pyqtgraph.Qt import QtWidgets

        self._event_filter.remove()
        if self._idealize_timer is not None:
            self._idealize_timer.stop()
            self._idealize_timer = None
        if self._idealize_future is not None:
            # A running fit is abandoned (its result is discarded), and the wait
            # cursor it set is restored so it never leaks past close.
            self._idealize_future = None
            QtWidgets.QApplication.restoreOverrideCursor()
        self._idealize_executor.shutdown(wait=False)
        if self._histogram_dock is not None:
            self._histogram_dock.close()
        if self._overlap_dock is not None:
            self._overlap_dock.close()
        self._trace_dock.close()
        self._window.close()

    def __enter__(self) -> TetherShell:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()


def make_store_idealizer(
    project: Project | str | PathLike[str],
    *,
    model_name: str = "vbfret",
    overwrite: bool = True,
    **kwargs: Any,
) -> Idealizer:
    """Build the store-backed one-click-idealize callable for a :class:`TetherShell`.

    Returns a ``molecule_key -> idealized-path`` function that runs the real
    :func:`tether.project.idealize.idealize_molecules` pipeline for that molecule
    (build an SMD → vbFRET via the sidecar → write ``/idealization/{model_name}`` as
    additive data with the per-molecule input-provenance hash) and hands back its
    per-frame Viterbi path for the dock overlay. ``overwrite=True`` so re-pressing
    ``I`` re-idealizes into the same model. Extra keyword arguments
    (``nstates`` / ``nstates_grid`` / ``intensity_quantity`` / ``sidecar_python`` /
    ``timeout`` …) pass straight through to :func:`idealize_molecules`.

    **MVP scope.** Idealizes the single selected molecule into a shared model,
    overwriting that model's prior contents; batch / accumulating idealization and
    model management are a later session (the deferred store↔shell hookup).
    """
    from tether.project.idealize import idealize_molecules

    def _idealize(molecule_key: str) -> np.ndarray | None:
        stored = idealize_molecules(
            project, [molecule_key], model_name=model_name, overwrite=overwrite, **kwargs
        )
        if stored.idealized is None:
            return None
        try:
            row = stored.molecule_keys.index(molecule_key)
        except ValueError:  # the requested key was not fitted (should not happen)
            return None
        return stored.idealized[row]

    return _idealize


def make_store_histogram(project: Project | str | PathLike[str], **kwargs: Any) -> HistogramSeam:
    """Build the store-backed population-histogram seam for a :class:`TetherShell`.

    Returns a ``() -> Histogram1D`` that runs
    :func:`tether.analysis.histogram.population_apparent_e_histogram` over
    ``project`` **each time it is called**, so re-opening the histogram reflects the
    current curation state (rejected molecules excluded by default, §7.5). Keyword
    arguments (``molecule_keys`` / ``intensity_quantity`` / ``bins`` /
    ``value_range`` / ``density`` / ``per_molecule_equal_weight`` /
    ``include_rejected``) pass straight through to that function.
    """
    from tether.analysis.histogram import population_apparent_e_histogram

    def _histogram() -> Histogram1D:
        return population_apparent_e_histogram(project, **kwargs)

    return _histogram


def _stored_disk_radius(path: str | PathLike[str]) -> float | None:
    """The experiment's stored aperture ``disk_radius`` (``/settings/extraction``).

    Returns ``None`` when the project carries no extraction settings (e.g. an
    analysis-only import), so the caller can fall back to the documented default
    rather than a fabricated radius.
    """
    import h5py

    try:
        with h5py.File(path, "r") as handle:
            group = handle.get("/settings/extraction")
            if group is not None and "disk_radius" in group.attrs:
                return float(group.attrs["disk_radius"])
    except OSError:
        return None
    return None


def make_store_overlap(
    project: Project | str | PathLike[str],
    *,
    aperture_radius: float | None = None,
) -> OverlapSeam:
    """Build the store-backed static-overlap-view seam for a :class:`TetherShell`.

    Reads ``/molecules`` (donor coordinates + ``movie_id``) and the cached
    ``/patches`` **once**, computes each molecule's nearest-neighbour distance +
    apertures-overlap flag via :func:`tether.analysis.overlap.neighbor_report`
    (confined per movie, §5.2), and returns a ``molecule_key -> OverlapInfo`` lookup
    the shell refreshes on selection. The geometry is static — spot coordinates
    never change with curation — so it is computed once, not per call (unlike the
    histogram seam). ``aperture_radius`` defaults to the experiment's stored
    extraction ``disk_radius``, falling back to
    :data:`~tether.analysis.overlap.DEFAULT_APERTURE_RADIUS` when absent, so the
    overlap threshold is never a hardcoded literal. An analysis-only project with no
    ``/patches`` still yields the NN readouts, with a ``None`` patch (§7.4).
    """
    import numpy as np

    from tether.analysis.overlap import DEFAULT_APERTURE_RADIUS, OverlapInfo, neighbor_report
    from tether.imaging.extract import read_molecules, read_patches

    path = getattr(project, "path", project)

    def _decode(value: object) -> str:
        return value.decode() if isinstance(value, bytes) else str(value)

    mols = read_molecules(path)
    keys = [_decode(k) for k in mols["molecule_key"]]
    coords = np.asarray(mols["donor_xy"], dtype=np.float64)
    movie_ids = np.array([_decode(m) for m in mols["movie_id"]])

    if aperture_radius is None:
        stored = _stored_disk_radius(path)
        radius = stored if stored is not None else DEFAULT_APERTURE_RADIUS
    else:
        radius = float(aperture_radius)

    report = neighbor_report(coords, aperture_radius=radius, groups=movie_ids)

    try:
        patches = read_patches(path).get("donor")
    except (KeyError, OSError):
        patches = None  # analysis-only project — no cached patch to show

    index_of = {key: i for i, key in enumerate(keys)}

    def _overlap(molecule_key: str) -> OverlapInfo | None:
        i = index_of.get(molecule_key)
        if i is None:
            return None
        neighbor = report.neighbor_of(i)
        return OverlapInfo(
            nn_distance=report.distance_of(i),
            overlaps=report.overlaps_at(i),
            aperture_radius=radius,
            patch=(np.asarray(patches[i]) if patches is not None else None),
            name=molecule_key,
            nn_molecule_key=(keys[neighbor] if neighbor is not None else None),
        )

    return _overlap


def _applied_summary(applied: AppliedReconcile) -> str:
    """A one-line status summary of what an :meth:`TetherShell.import_return_leg` committed."""
    parts: list[str] = []
    if applied.idealization_written is not None:
        parts.append(f"imported model /idealization/{applied.idealization_written}")
    if applied.windows_applied:
        parts.append(f"{len(applied.windows_applied)} window(s)")
    if applied.classes_applied:
        parts.append(f"{len(applied.classes_applied)} class(es)")
    if applied.classes_deferred:
        parts.append(f"{len(applied.classes_deferred)} class(es) deferred to M4")
    if applied.import_unfit_dropped:
        parts.append(f"{len(applied.import_unfit_dropped)} unfit trace(s) dropped")
    if applied.stale_after:
        parts.append(f"{len(applied.stale_after)} idealization(s) re-staled")
    if not parts:
        return "Return-leg import: nothing to apply"
    return "Return-leg import applied — " + ", ".join(parts)


class _StoreHandoff:
    """Store-backed :class:`HandoffSeam` over :mod:`tether.project.handoff`.

    Binds one project + intensity quantity so all three legs use the same ``/traces``
    layer; see :func:`make_store_handoff`. Each op imports its ``handoff`` function
    lazily so constructing the seam (and importing this module) stays light.
    """

    def __init__(
        self,
        project: Project | str | PathLike[str],
        *,
        intensity_quantity: str,
        model_name: str | None,
        overwrite: bool,
    ) -> None:
        self._project = project
        self._intensity_quantity = intensity_quantity
        self._model_name = model_name
        self._overwrite = overwrite

    def hand_off(
        self, molecule_keys: list[str] | None, out_path: str | PathLike[str]
    ) -> HandoffManifest:
        from tether.project.handoff import hand_off_to_tmaven

        return hand_off_to_tmaven(
            self._project,
            molecule_keys,
            out_path=out_path,
            intensity_quantity=self._intensity_quantity,
        )

    def preview(
        self, smd_path: str | PathLike[str], *, model_path: str | PathLike[str] | None = None
    ) -> ReconcileReport:
        from tether.project.handoff import read_return_leg

        return read_return_leg(
            self._project,
            smd_path,
            model_path=model_path,
            intensity_quantity=self._intensity_quantity,
            model_name=self._model_name,
        )

    def apply(
        self,
        smd_path: str | PathLike[str],
        decision: ReconcileDecision,
        *,
        model_path: str | PathLike[str] | None = None,
    ) -> AppliedReconcile:
        from tether.project.handoff import apply_reconcile

        return apply_reconcile(
            self._project,
            smd_path,
            model_path=model_path,
            intensity_quantity=self._intensity_quantity,
            model_name=self._model_name,
            accept_windows=decision.accept_windows,
            accept_classes=decision.accept_classes,
            import_idealization=decision.import_idealization,
            overwrite=self._overwrite,
        )


def make_store_handoff(
    project: Project | str | PathLike[str],
    *,
    intensity_quantity: str = "corrected",
    model_name: str | None = None,
    overwrite: bool = False,
) -> HandoffSeam:
    """Build the store-backed :class:`HandoffSeam` for a :class:`TetherShell`.

    Binds one ``.tether`` project + intensity quantity across all three legs so the
    outbound SMD and the return-leg intensity match use the **same** ``/traces`` layer
    (§5.3). ``model_name`` is the ``/idealization`` entry an accepted idealization
    import writes (``None`` → the ``tmaven-import`` default); ``overwrite`` guards
    clobbering it — the default ``False`` keeps the re-import non-destructive (a repeat
    import into the same name is refused, not silently overwritten, §7.4).
    """
    return _StoreHandoff(
        project,
        intensity_quantity=intensity_quantity,
        model_name=model_name,
        overwrite=overwrite,
    )


def _movie_frame_times(path: str | PathLike[str]) -> dict[str, float | None]:
    """Map each ``/movies`` ``movie_id`` to its per-frame time (seconds), ``None`` unknown.

    Reads ``/movies/table`` directly (there is no ``read_movies`` helper) to fill each
    :class:`~tether.gui.trace_dock.TraceView`'s ``frame_time``. A stored ``0.0`` (the
    extractor's "TIFF interval unknown" sentinel) maps to ``None`` — a frame-index-only
    axis — because :class:`TraceView` rejects a non-positive ``frame_time``. An
    analysis-only import (movie-less, no ``/movies`` group) yields an empty map, so every
    trace falls back to ``None``.
    """
    import h5py

    from tether.io.schema import TABLE

    out: dict[str, float | None] = {}
    try:
        with h5py.File(Path(path), "r") as handle:
            group = handle.get("movies")
            if group is None or TABLE not in group:
                return out
            table = group[TABLE][:]
    except OSError:
        return out
    for row in table:
        movie_id = row["movie_id"]
        movie_id = movie_id.decode() if isinstance(movie_id, bytes) else str(movie_id)
        frame_time = float(row["frame_time"])
        out[movie_id] = frame_time if frame_time > 0.0 else None
    return out


def traces_from_store(
    project: Project | str | PathLike[str],
    *,
    intensity_quantity: str = "corrected",
) -> list[TraceView]:
    """Read a ``.tether`` store's molecules into the shell's ``list[TraceView]``.

    The missing member of the ``make_store_*`` family: turns a produced / extracted
    ``.tether`` into the value objects :meth:`TetherShell.set_molecules` consumes, so a
    project can be opened live in the shell (:meth:`TetherShell.load_project`). Reads
    ``/molecules`` + ``/traces`` positionally (row *i* of each is the same molecule,
    §5.1) and, for every molecule, slices the chosen intensity layer to its **full native
    ``frame_range``** — the whole trace including post-bleach frames, the curation view
    (unlike analysis, which narrows to the ``analysis_window``). ``frame_range`` unset
    ``[0, 0]`` falls back to the full row extent. Each :class:`TraceView` carries its
    decoded ``molecule_key`` (so the idealize / overlap seams resolve the store row) and
    its ``frame_time`` from the linked ``/movies`` row (``0.0``/movie-less → ``None``).

    Rejected molecules are **included** (a curator revisits rejects); the histogram /
    analysis seams still exclude them by their own default. ``intensity_quantity`` selects
    the ``/traces`` layer via the frozen
    :data:`~tether.project.trace_layers.INTENSITY_QUANTITY_LAYERS` map — ``"corrected"``
    (the apparent-E substrate present in both round-trip and analysis-only stores) or
    ``"raw"``.

    Raises
    ------
    ValueError
        ``intensity_quantity`` is unknown, or the store lacks the requested trace layer
        (e.g. ``"raw"`` on an analysis-only store, which writes only corrected layers).
    """
    import numpy as np

    from tether.gui.trace_dock import TraceView
    from tether.imaging.extract import read_molecules, read_traces
    from tether.project.trace_layers import INTENSITY_QUANTITY_LAYERS

    if intensity_quantity not in INTENSITY_QUANTITY_LAYERS:
        raise ValueError(
            f"intensity_quantity must be one of {sorted(INTENSITY_QUANTITY_LAYERS)}, "
            f"got {intensity_quantity!r}"
        )
    donor_key, acceptor_key = INTENSITY_QUANTITY_LAYERS[intensity_quantity]

    path = getattr(project, "path", project)
    molecules = read_molecules(path)
    if molecules.shape[0] == 0:
        return []
    traces = read_traces(path)
    for key in (donor_key, acceptor_key):
        if key not in traces:
            raise ValueError(
                f"{Path(path).name}/traces has no {key!r} layer "
                f"(intensity_quantity={intensity_quantity!r})"
            )
    donor_all = traces[donor_key]
    acceptor_all = traces[acceptor_key]
    max_t = int(donor_all.shape[1]) if donor_all.ndim == 2 else 0
    if max_t == 0:  # a store with molecules but zero-width traces — nothing to view
        return []

    frame_times = _movie_frame_times(path)
    key_col = molecules["molecule_key"]
    movie_col = molecules["movie_id"]
    frame_range = molecules["frame_range"]

    def _decode(value: object) -> str:
        return value.decode() if isinstance(value, bytes) else str(value)

    views: list[TraceView] = []
    for i in range(molecules.shape[0]):
        lo, hi = int(frame_range[i][0]), int(frame_range[i][1])
        if hi <= lo:  # unset [0, 0] -> the full native row extent
            lo, hi = 0, max_t
        donor = np.asarray(donor_all[i, lo:hi], dtype=np.float64)
        acceptor = np.asarray(acceptor_all[i, lo:hi], dtype=np.float64)
        views.append(
            TraceView(
                donor=donor,
                acceptor=acceptor,
                frame_time=frame_times.get(_decode(movie_col[i])),
                molecule_key=_decode(key_col[i]),
            )
        )
    return views


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
        traces.append(
            TraceView(
                donor=donor,
                acceptor=acceptor,
                frame_time=0.1,
                name=f"mol-{i}",
                molecule_key=f"mol-{i}",
            )
        )

    keyed = {t.molecule_key: t for t in traces}

    def _demo_idealizer(molecule_key: str) -> np.ndarray | None:
        # A SYNTHETIC two-level step for the interactive/computer-use smoke ONLY —
        # not a real vbFRET fit. The real one-click uses make_store_idealizer(project).
        trace = keyed.get(molecule_key)
        if trace is None:
            return None
        path = np.full(trace.n_frames, 0.3)
        path[trace.n_frames // 2 :] = 0.7
        return path

    def _demo_histogram() -> Any:
        # Pool the synthetic traces' apparent E for the &Analysis smoke ONLY — the
        # real menu uses make_store_histogram(project) over population_apparent_e_histogram.
        from dataclasses import replace

        from tether.analysis.histogram import apparent_e_histogram

        pooled = np.concatenate([t.apparent_e for t in traces])
        return replace(apparent_e_histogram(pooled), n_molecules=len(traces))

    # A tiny on-disk demo project so the &Conditions menu opens a populated dialog for
    # the live smoke: two acquisitions of one condition (T-box) across two files + a
    # near-miss (Tbox), left unsynced so the rows show as «dangling» until the curator
    # clicks "Confirm provisional (materialize)" or corrects one via "Correct…".
    import tempfile
    from pathlib import Path

    import h5py

    from tether.io.filename import parse_filename
    from tether.io.schema import MOLECULES_DTYPE, TABLE, create_project
    from tether.project.core import Project

    demo_dir = Path(tempfile.mkdtemp(prefix="tether-demo-"))
    demo_path = create_project(demo_dir / "demo.tether")
    specs = [
        ("k0", "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif", "movie-A10"),
        ("k1", "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_011.tif", "movie-A11"),
        ("k2", "Bla_UCKOPSB_Tbox_35pM_tRNA_600nM_010.tif", "movie-NM"),
    ]
    demo_rows = np.zeros(len(specs), dtype=MOLECULES_DTYPE)
    for name in MOLECULES_DTYPE.names:
        if MOLECULES_DTYPE[name].kind == "O":
            demo_rows[name] = ""
    demo_rows["molecule_id"] = [f"mol-{i}" for i in range(len(specs))]
    demo_rows["molecule_key"] = [k for k, _, _ in specs]
    demo_rows["movie_id"] = [m for _, _, m in specs]
    demo_rows["source_filename"] = [s for _, s, _ in specs]
    demo_rows["condition_id"] = [parse_filename(s).condition_id for _, s, _ in specs]
    with h5py.File(demo_path, "r+") as f:
        demo_table = f["molecules"][TABLE]
        demo_table.resize((len(specs),))
        demo_table[:] = demo_rows

    shell = TetherShell(
        idealizer=_demo_idealizer, histogram=_demo_histogram, conditions=Project(demo_path)
    )
    shell.set_molecules(traces)
    shell.show()
    app.exec()


if __name__ == "__main__":  # pragma: no cover
    launch()

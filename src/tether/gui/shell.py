# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Tether PySide6 shell host + curation help/overflow dialogs (PRD §7.3).

[`TetherShell`][] is the minimal ``QMainWindow`` that wires the M2 S2 focus
contract: it hosts the pyqtgraph trace dock ([`tether.gui.trace_dock`][]) plus
the focus surfaces the curation keys must reach past — a molecule list, a movie
switcher, and the **Categories…** button that opens the
[`CategoryEditor`][tether.gui.category_editor.CategoryEditor] of the selected molecule's
condition (§7.6, #633; its name field is a text entry exempt from the keymap) — and
installs the application-level
[`CurationEventFilter`][tether.gui.curation.CurationEventFilter]. An opened project's movies fill
the movie switcher from ``/movies``, and the embedded napari movie panel
([`NapariMoviePanel`][tether.gui.movie_panel.NapariMoviePanel]) shows the chosen one with its
donor and acceptor spots as a dock of Curate and correct (#356). The round-trip between a
trace and its spot ([`tether.gui.roundtrip`][], #357) links the two: selecting a molecule,
or pressing ``Enter``, shows its own movie centred on its donor spot, and clicking a spot
selects its molecule. The trace dock's frame cursor and the movie's displayed frame move
together ([`frame_link`][tether.gui.shell.TetherShell.frame_link], #366) unless the user turns
that off in the Curation menu.

Two companion dialogs complete the §7.3 contract:

* [`OverflowCategoryPicker`][] assigns a category **beyond the first nine**
  (the ``1``–``9`` hotkeys cover only the first nine of the per-condition list),
  returning the chosen integer class.
* [`CheatSheetOverlay`][] renders the shipped, always-current keyboard
  cheat-sheet from the live [`Keymap`][tether.gui.curation.Keymap], with the name of
  the category each number key gives the selected molecule (#633).

The window hosts its **sections** (#341, ADR-0116): the central widget is a
``QStackedWidget`` with one page per section of the
[`SectionRegistry`][tether.gui.sections.SectionRegistry] — Prepare, Curate and
correct, Analyze (Deposit is registered but hidden until #956) — and each dock belongs to
one section and shows only while that section is current.
[`TetherShell.set_section`][tether.gui.shell.TetherShell.set_section] switches; a
switch never touches the session lock of ADR-0055.

The user moves between the sections in two ways (#897). **Direct navigation**: the
section bar under the menu bar, **View → Section**, and ``Ctrl+1``, ``Ctrl+2``, ...
(``Cmd`` on macOS) open any visible section at any time. The **guided mode**: the same bar
shows the next step that ``tether status`` gives for the open project, and **Go to** opens
its section. A project made from imported traces opens in Curate and correct. A control
that needs an earlier stage declares it with
[`TetherShell.require`][tether.gui.shell.TetherShell.require] (#898): it is disabled, it
names the missing step, and it offers **Go to this step** ([`tether.gui.requirements`][]).

The menu bar and the curation keymap follow the section too (#342). The menus of each
section are built from its [`MenuSpec`][tether.gui.sections.MenuSpec]s and show only while
the section is current, between the window's own File and Edit menus and its View and Help
menus.
The curation keys act only in a section whose ``keymap_scope`` is
[`CURATION_SCOPE`][tether.gui.curation.CURATION_SCOPE] (Curate and correct): elsewhere the
event filter lets them through to the native widgets, and the cheat-sheet lists no key.

The keymap is the user's (#635): the shell starts with the keymap saved at
``keymap_file`` (``tether-gui`` passes
[`default_keymap_path`][tether.gui.curation.default_keymap_path]), falling back to the
defaults with one status message when that file cannot be used, and
**Curation → Edit keyboard shortcuts…** opens the
[`KeymapEditor`][tether.gui.keymap_editor.KeymapEditor], whose Save puts the new keys in
effect at once and writes the file.

The shell also carries the desktop basics (#629): File → Close project and Quit
with the platform's standard keys, a Help menu (documentation, the cheat-sheet, the
log folder, and the About box of [`tether.gui.about`][]), opening a dropped
``.tether`` file, and the [`tether.gui.start_page`][] shown while nothing is loaded.

All three are **composition** wrappers (they *hold* a Qt widget rather than
subclass one) so importing this module costs no Qt, matching the rest of
``tether.gui``. Qt is imported lazily inside ``__init__``.
"""

from __future__ import annotations

import logging
import os
from contextlib import suppress
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any

from tether.gui import preferences
from tether.gui.curation import (
    CATEGORY_SLOTS,
    CURATION_SCOPE,
    Command,
    CurationAction,
    CurationController,
    CurationEventFilter,
    CurationHandlers,
    Keymap,
    KeymapError,
)
from tether.gui.requirements import RequirementGate
from tether.gui.sections import (
    DockArea,
    DockSpec,
    MenuSpec,
    SectionRegistry,
    SectionSpec,
    entry_section,
    guided_step,
)
from tether.gui.task_service import TaskService
from tether.gui.undo import CATEGORY, CURATION, WINDOW, UndoEntry, UndoStack, WindowState
from tether.services.curation import (
    ACCEPTED,
    REJECTED,
    UNDECIDED,
    calibration_windows,
    curation_snapshot,
    curation_state,
    molecule_states,
    resolved_frame_range,
    resolved_window,
    stored_molecule,
    stored_rows,
    summed_bleach_frame,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence
    from os import PathLike
    from typing import Protocol

    from pyqtgraph.Qt import QtWidgets

    from tether.analysis.overlap import OverlapInfo
    from tether.gui.category_editor import CategoryEdit, CategoryEditor
    from tether.gui.generation_reconcile import GenerationReconcilePage
    from tether.gui.keymap_editor import KeymapEditor
    from tether.gui.reconcile import ReconcileDecision
    from tether.gui.tasks import TaskOutcome
    from tether.gui.trace_dock import TraceView
    from tether.project.core import Project
    from tether.project.generation_reconcile import ConfirmResult
    from tether.project.handoff import AppliedReconcile, HandoffManifest, ReconcileReport
    from tether.services.corrections import CorrectionPassResult
    from tether.services.curation import CurationSnapshot, MoleculeState, StoredMolecule

    #: Runs one whole-project fit, ``idealizer(fit, cancel=token, progress=report)``, on
    #: the worker thread and returns the command's
    #: [`IdealizeResult`][tether.services.idealize.IdealizeResult] (#625). The shell's
    #: "Idealize project…" starts it; `make_store_idealizer` is the store-backed default.
    Idealizer = Callable[..., Any]

    #: Resolve a molecule_key to its static-overlap-view payload — the cached patch
    #: + nearest-neighbour distance + apertures-overlap flag — or ``None`` for an
    #: unknown key. `make_store_overlap` is the store-backed default that
    #: computes it once from ``/molecules`` coordinates + ``/patches`` (§7.3/§5.1).
    OverlapSeam = Callable[[str], OverlapInfo | None]

    class HandoffSeam(Protocol):
        """The store hand-off operations the shell's ``&Hand-off`` menu drives.

        [`make_store_handoff`][] is the store-backed default; a test can inject a
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
    "ANALYZE_SECTION",
    "CURATE_SECTION",
    "DEFERRED_ACTIONS",
    "PREPARE_SECTION",
    "CheatSheetOverlay",
    "OverflowCategoryPicker",
    "TetherShell",
    "launch",
    "make_store_handoff",
    "make_store_idealizer",
    "make_store_overlap",
]

_APP_NAME = "Tether"

#: The curation commands the shell does not implement yet (#345). Their keys stay
#: bound, so a press reports ``… unavailable: …`` instead of a success it did not
#: earn, and the cheat sheet marks them ``(unavailable)``. The grid becomes real in
#: #364, which removes its entry here. Jump is real since #357 (the trace↔movie
#: round-trip), and the category, analysis-window and photobleach keys write the
#: project (#359).
DEFERRED_ACTIONS: frozenset[CurationAction] = frozenset({CurationAction.GRID})

#: How often (ms) the main thread polls the background idealize fit for completion.
_IDEALIZE_POLL_MS = 25

#: Retry a failed nonce-safe session-lock release without waiting for staleness.
_LOCK_RELEASE_RETRY_MS = 1000

#: Upper bound (ms) on one wait before the shell judges a refused lock again (#626).
#: A foreign holder can refresh while the shell waits, so the wait is re-armed. A running
#: holder on this computer has no due time and is judged again at this interval (#1123).
_LOCK_RECOVERY_RECHECK_MAX_MS = 60_000

#: Coalesce rapid curation keystrokes before the Analyze section draws its plot again (#374).
_HISTOGRAM_REFRESH_DEBOUNCE_MS = 250

#: After a write that can change a stage (a curation decision, a fit, a return leg), the
#: guided next step is read again once the writes stop for this long (#897). The read runs
#: on the main thread, between writes: a read-only handle open on a worker thread would make
#: HDF5 refuse the next write's ``r+`` open. It took 0.37 s on 2000 molecules of 2000 frames
#: with one fit, so it waits for a pause in the curation rather than following each key.
_READINESS_REFRESH_DEBOUNCE_MS = 1000

#: File filter for the hand-off SMD / model open+save dialogs (tMAVEN uses HDF5).
_SMD_FILTER = "tMAVEN SMD (*.hdf5 *.smd);;All files (*)"

#: Suffixes ``_SMD_FILTER`` lists; a hand-off path carrying neither gets the first.
_SMD_SUFFIXES = (".hdf5", ".smd")

#: File filter for the ``&File → Open project…`` dialog (a ``.tether`` HDF5 store).
_TETHER_FILTER = "Tether project (*.tether);;All files (*)"

#: The ids of the sections the shell registers (#341, ADR-0116), in the order of page 1
#: of the design (#897).
PREPARE_SECTION = "prepare"
CURATE_SECTION = "curate"
ANALYZE_SECTION = "analyze"
DEPOSIT_SECTION = "deposit"

#: Whether the Deposit section shows. It stays hidden until its content lands (#956);
#: this one registry flag controls it (#897).
DEPOSIT_VISIBLE = False

#: The section that shows when the shell opens: its page holds the start page (#629).
_DEFAULT_SECTION = CURATE_SECTION

#: Why a project writer cannot start while the Import dock links a source again (#913).
_RELINK_RUNNING = "a source of the project is being linked again; wait for it to finish"

# Initial acquisition treats the same host/user/PID as one identity. Separate shells
# in one QApplication therefore need an additional process-local ownership boundary
# or they can both become writable before retained-session refresh takes over.
_ProjectOwnerKey = tuple[str, int, int] | tuple[str, str]
_GUI_PROJECT_OWNERS: dict[_ProjectOwnerKey, object] = {}
_GUI_PROJECT_OWNERS_LOCK = Lock()
# A shell whose pass publishes a replacement of its project file holds the canonical path
# here until its claim has moved to the new file (`_rebind_gui_project`). Between the
# replace and that move the new file has no owner key, so a claim of the path must wait.
_GUI_PUBLISHING_PATHS: dict[str, object] = {}


def _canonical_project_path(path: str | Path) -> str:
    return os.path.normcase(os.path.realpath(os.fspath(path)))


def _project_owner_key(path: str | Path) -> _ProjectOwnerKey:
    """Return one process-local identity for paths to the same existing file.

    The identity is the canonical [`file_identity`][tether.project.lock.file_identity]
    (ADR-0112), so the GUI registry and the lock lifecycle agree on what one file is.
    """
    from tether.project.lock import file_identity

    resolved = _canonical_project_path(path)
    identity = file_identity(resolved)
    if identity is None:
        # Construction/failure paths may not exist yet. Keep the former normalized
        # path behavior as a safe, deterministic fallback without inventing an inode.
        return ("path", resolved)
    return ("file", *identity)


def _claim_gui_project(path: str | Path, token: object) -> _ProjectOwnerKey | None:
    """Claim ``path`` for one shell token, returning its key or ``None`` if busy."""
    canonical = _canonical_project_path(path)
    with _GUI_PROJECT_OWNERS_LOCK:
        # Read the identity under the lock: a pass that replaces the file and moves its
        # claim between this read and the check would leave the old identity free.
        key = _project_owner_key(path)
        owner = _GUI_PROJECT_OWNERS.get(key)
        if owner is not None and owner is not token:
            return None
        publisher = _GUI_PUBLISHING_PATHS.get(canonical)
        if publisher is not None and publisher is not token:
            return None
        _GUI_PROJECT_OWNERS[key] = token
    return key


def _hold_publishing_path(path: str | Path, token: object) -> str:
    """Mark ``path`` as being replaced by ``token``'s shell; return the canonical path."""
    canonical = _canonical_project_path(path)
    with _GUI_PROJECT_OWNERS_LOCK:
        _GUI_PUBLISHING_PATHS[canonical] = token
    return canonical


def _drop_publishing_path(canonical: str, token: object) -> None:
    """End the mark of `_hold_publishing_path` when ``token`` still holds it."""
    with _GUI_PROJECT_OWNERS_LOCK:
        if _GUI_PUBLISHING_PATHS.get(canonical) is token:
            del _GUI_PUBLISHING_PATHS[canonical]


def _release_gui_project(key: _ProjectOwnerKey | None, token: object) -> None:
    """Release a process-local claim only when ``token`` still owns it."""
    if key is None:
        return
    with _GUI_PROJECT_OWNERS_LOCK:
        if _GUI_PROJECT_OWNERS.get(key) is token:
            del _GUI_PROJECT_OWNERS[key]


def _rebind_gui_project(
    key: _ProjectOwnerKey | None, path: str | Path, token: object
) -> _ProjectOwnerKey | None:
    """Move ``token``'s claim from ``key`` to the file now at ``path``; return the key it holds.

    A guarded publish replaces the project file with one atomic ``os.replace``, so the
    file at ``path`` has a new identity and ``key`` names a file that is gone. The lock
    moves its identity record across that replace (ADR-0112); this moves the
    process-local claim the same way. If ``token`` does not hold ``key``, or another
    token holds the new file, the claim stays as it was.
    """
    if key is None:
        return None
    new = _project_owner_key(path)
    with _GUI_PROJECT_OWNERS_LOCK:
        if new == key or _GUI_PROJECT_OWNERS.get(key) is not token:
            return key
        owner = _GUI_PROJECT_OWNERS.get(new)
        if owner is not None and owner is not token:
            return key
        del _GUI_PROJECT_OWNERS[key]
        _GUI_PROJECT_OWNERS[new] = token
    return new


def _probe_project_writable(path: str | Path) -> None:
    """Require the project HDF5 itself to open for update before enabling writes."""
    import h5py

    with h5py.File(path, "r+"):
        pass


def _set_standard_shortcuts(action: Any, standard_key: Any, fallback: str) -> None:
    """Give ``action`` the platform's keys for ``standard_key`` plus ``fallback``.

    Qt's per-platform table leaves gaps: Quit has no key on Windows, and on a generic
    Linux desktop only the dedicated Exit key. ``fallback`` (the common ``Ctrl``
    chord, ``Cmd`` on macOS) is added when the table does not already hold it, so
    every action stays reachable from an ordinary keyboard.
    """
    from pyqtgraph.Qt import QtGui

    keys = list(QtGui.QKeySequence.keyBindings(standard_key))
    extra = QtGui.QKeySequence(fallback)
    if all(key.toString() != extra.toString() for key in keys):
        keys.append(extra)
    action.setShortcuts(keys)


def _redo_keys(platform: str | None = None) -> tuple[str, ...]:
    """The chords of **Edit → Redo** (#632): ``Ctrl+Shift+Z``, and ``Ctrl+Y`` on Windows.

    ``Ctrl`` is ``Cmd`` on macOS, as Qt maps it. ``platform`` defaults to ``sys.platform``.
    """
    import sys

    platform = sys.platform if platform is None else platform
    return ("Ctrl+Shift+Z", "Ctrl+Y") if platform == "win32" else ("Ctrl+Shift+Z",)


def _read_or_none(read: Callable[[], Any]) -> Any:
    """Return ``read()``, or ``None`` when it fails (#632).

    An unreadable state before an action only means the action cannot be undone, never
    that it is refused.
    """
    try:
        return read()
    except Exception:  # noqa: BLE001 - the action itself still runs and reports
        return None


def _window_state(rows: Sequence[StoredMolecule], row: int) -> WindowState:
    """The [`WindowState`][tether.gui.undo.WindowState] of a molecule's stored rows (#632).

    ``window`` is that of row ``row`` when it is among ``rows``, else of the first row;
    ``whole`` says whether every row has its whole ``frame_range``; ``rows`` holds each
    row's own window. Raises ``KeyError`` when ``rows`` is empty.
    """
    if not rows:
        raise KeyError("no stored row of the molecule")
    selected = next((m for m in rows if m.row == row), rows[0])
    return WindowState(
        window=selected.window,
        whole=all(m.window == m.frame_range for m in rows),
        rows=tuple((m.molecule_id, m.window) for m in rows),
    )


def _category_state(rows: Sequence[StoredMolecule]) -> tuple[tuple[str, str], ...]:
    """Each stored row's ``(molecule_id, category)``, the category state an undo writes back.

    Two rows can share a molecule key with different categories, so the state is kept
    row by row (#632). Raises ``KeyError`` when ``rows`` is empty.
    """
    if not rows:
        raise KeyError("no stored row of the molecule")
    return tuple((m.molecule_id, m.category) for m in rows)


def _movie_panel_unavailable() -> str | None:
    """Why no napari movie panel can be built in this process, or ``None`` (#356).

    A napari ``Viewer`` needs an OpenGL context for its canvas, and the offscreen Qt
    platform on macOS has none: building one there ends the process, which no ``except``
    can catch. That one combination is refused up front; elsewhere the panel is built
    and a failure is reported by the caller.
    """
    import importlib.util
    import sys

    from pyqtgraph.Qt import QtGui

    if importlib.util.find_spec("napari") is None:
        return "napari is not installed"
    if sys.platform == "darwin" and QtGui.QGuiApplication.platformName() == "offscreen":
        return "the offscreen display platform on macOS has no OpenGL"
    return None


def _new_movie_panel() -> Any:
    """A new headless [`NapariMoviePanel`][tether.gui.movie_panel.NapariMoviePanel] to dock."""
    from tether.gui.movie_panel import NapariMoviePanel

    return NapariMoviePanel(title="Tether — movie", show=False)


def _local_paths(mime: Any) -> list[Path]:
    """The local files a drag carries (``[]`` for text, remote URLs, or no URLs)."""
    if mime is None or not mime.hasUrls():
        return []
    return [Path(url.toLocalFile()) for url in mime.urls() if url.isLocalFile()]


def _guide_page(title: str, text: str) -> Any:
    """A plain page with a heading and one paragraph that says where the tools are.

    The Analyze and Deposit sections show one until their own content lands (#897 and the
    issues it lists); the page never offers an action the shell cannot do.
    """
    from pyqtgraph.Qt import QtCore, QtWidgets

    from tether.gui import theme

    page = QtWidgets.QWidget()
    page.setAccessibleName(f"{title} section")
    layout = QtWidgets.QVBoxLayout(page)
    heading = QtWidgets.QLabel(title)
    heading.setFont(theme.heading_font())
    body = QtWidgets.QLabel(text)
    body.setWordWrap(True)
    layout.addWidget(heading)
    layout.addWidget(body)
    layout.addStretch(1)
    layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
    return page


def _quit_application() -> None:  # pragma: no cover - ends the process's event loop
    """Quit the running ``QApplication`` (a seam, so tests can observe File → Quit)."""
    from pyqtgraph.Qt import QtWidgets

    QtWidgets.QApplication.quit()


class OverflowCategoryPicker:
    """Modal picker for assigning a category beyond the ``1``–``9`` hotkeys.

    Given the full ordered per-condition category list, it lists only the entries
    whose integer class exceeds [`CATEGORY_SLOTS`][tether.gui.curation.CATEGORY_SLOTS]
    (index ``>= 9``) and returns the chosen **integer class** (1-based) — the
    correct integer↔category mapping the ``/labels`` writer stores (§7.3). The
    list is the selected molecule's condition's own, as the category editor keeps it
    (#633).
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
        """The [`Command`][] for the selection (``ASSIGN_CATEGORY``), or ``None``."""
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
    [`cheatsheet`][tether.gui.curation.Keymap.cheatsheet]; [`refresh`][] re-reads the
    keymap so a rebinding is reflected the next time it is shown. Each action in
    ``unavailable`` (the shell passes [`DEFERRED_ACTIONS`][tether.gui.shell.DEFERRED_ACTIONS]) is
    marked ``(unavailable)``, so the sheet never advertises a key the shell cannot honour.

    ``live`` scopes the sheet to a section (#342): while it returns ``False`` the
    keymap is not active, so the table has no rows and ``inactive_text`` shows in their
    place. The shell passes the same predicate as its event filter, so the sheet lists
    exactly the keys that act in the current section. ``None`` keeps every row.

    ``category_names`` is asked on each refresh for the category each number key
    assigns (#633): the shell passes the list of the selected molecule's condition, so
    the sheet shows ``Assign category 2 (bound)``. ``None``, or a call that returns
    ``None``, keeps the bare class numbers.
    """

    def __init__(
        self,
        keymap: Keymap,
        *,
        parent: QtWidgets.QWidget | None = None,
        unavailable: frozenset[CurationAction] = frozenset(),
        note: str | None = None,
        live: Callable[[], bool] | None = None,
        inactive_text: str = "No keyboard shortcuts act in this section.",
        category_names: Callable[[], Mapping[int, str] | None] | None = None,
    ) -> None:
        from pyqtgraph.Qt import QtWidgets

        self._keymap = keymap
        self._category_names = category_names
        self._unavailable = frozenset(unavailable)
        self._rows: list[tuple[str, str]] = []
        self._note = note
        self._live = live
        self._note_label: Any | None = None
        self._dialog = QtWidgets.QDialog(parent)
        self._dialog.setWindowTitle("Keyboard shortcuts")
        layout = QtWidgets.QVBoxLayout(self._dialog)
        self._table = QtWidgets.QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Key", "Action"])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self._table)
        # Shown in place of the rows while the keymap is not active (#342).
        self._inactive_label = QtWidgets.QLabel(inactive_text)
        self._inactive_label.setWordWrap(True)
        layout.addWidget(self._inactive_label)
        if note:
            # A behavior the key rows cannot carry, such as the advance-after-curation
            # preference (#631): shown under the table, never as a key row.
            self._note_label = QtWidgets.QLabel(note)
            self._note_label.setWordWrap(True)
            layout.addWidget(self._note_label)
        self.refresh()

    @property
    def dialog(self) -> QtWidgets.QDialog:
        """The wrapped ``QDialog`` holding the ``Key``/``Action`` cheat-sheet table."""
        return self._dialog

    @property
    def rows(self) -> list[tuple[str, str]]:
        """The ``(key_label, action)`` rows currently displayed."""
        return list(self._rows)

    @property
    def note(self) -> str | None:
        """The text shown under the key table, or ``None`` when there is none."""
        return self._note

    @property
    def live(self) -> bool:
        """Whether the keymap is active now, so the table lists its bindings (#342)."""
        return self._live is None or bool(self._live())

    @property
    def inactive_label(self) -> QtWidgets.QLabel:
        """The text shown in place of the rows while the keymap is not active."""
        return self._inactive_label

    def refresh(self) -> None:
        """Re-read the keymap and repopulate the table (reflects rebindings and the section)."""
        from pyqtgraph.Qt import QtWidgets

        live = self.live
        names = self._category_names() if live and self._category_names is not None else None
        self._rows = (
            self._keymap.cheatsheet(unavailable=self._unavailable, category_names=names)
            if live
            else []
        )
        # The advance note (#631) is about curation, so it shows only with the rows.
        self._inactive_label.setHidden(live)
        if self._note_label is not None:
            self._note_label.setHidden(not live)
        self._table.setRowCount(len(self._rows))
        for row, (key_label, description) in enumerate(self._rows):
            self._table.setItem(row, 0, QtWidgets.QTableWidgetItem(key_label))
            self._table.setItem(row, 1, QtWidgets.QTableWidgetItem(description))
        self._table.resizeColumnsToContents()

    def show(self) -> None:
        self.refresh()
        self._dialog.show()


def _load_keymap(path: Path | None) -> tuple[Keymap, str | None]:
    """The keymap saved at ``path`` and ``None``, or the defaults and why the file failed.

    No path or no file gives the defaults silently. A file that cannot be used gives the
    defaults and one message; the file is never rewritten here, only by a Save (#635).
    """
    defaults = Keymap.default()
    if path is None:
        return defaults, None
    try:
        path.stat()
        return Keymap.load(path, defaults=defaults), None
    except (FileNotFoundError, NotADirectoryError):
        return defaults, None
    except (OSError, KeymapError) as exc:
        logging.getLogger("tether.gui").warning("keymap file %s not used: %s", path, exc)
        return defaults, (
            f"Keyboard shortcuts: {path} could not be used: {exc}. The default keys are in "
            "effect, and the file was left as it is."
        )


def _read_lock_recovery(path: str | PathLike[str]) -> Any | None:
    """[`lock_recovery`][tether.project.lock.lock_recovery], or ``None`` if unreadable."""
    from tether.project.lock import lock_recovery

    try:
        return lock_recovery(path)
    except OSError:
        return None


def _lock_refusal_text(path: str | PathLike[str], recovery: Any) -> str:
    """The refusal text for ``recovery``'s current holder, as an open would report it."""
    from tether.project import lock

    owner = recovery.owner
    stale = owner is not None and owner.is_stale(timeout_s=lock.DEFAULT_STALENESS_TIMEOUT_S)
    exc = lock.LockedError(owner, stale=stale, corrupt=recovery.corrupt, path=lock.lock_path(path))
    return f"write lock unavailable: {exc}"


def _lock_reason(refusal: str, recovery: Any | None) -> str:
    """The read-only banner text for a refused lock and its recovery verdict (#626)."""
    from tether.project.lock import RecoveryAction

    if recovery is None:
        return refusal
    owner = recovery.owner
    if recovery.action is RecoveryAction.RECOVER:
        hint = "a Tether session of yours on this computer stopped; Recover reopens it writable"
    elif recovery.action is RecoveryAction.TAKE_OVER:
        hint = "the holder is idle past the staleness timeout; Take over… is available"
    elif recovery.holder_running:
        assert owner is not None  # a running holder is a parsed record
        hint = (
            f"a running Tether process on this computer holds it (pid {owner.pid}); "
            "close that window to write here"
        )
    elif recovery.available_at is not None:
        when = recovery.available_at.astimezone().strftime("%Y-%m-%d %H:%M %Z")
        hint = f"a takeover becomes possible at {when} if the holder stays idle"
    else:  # pragma: no cover - every NONE verdict carries one of the two facts above
        hint = "no takeover is available"
    return f"{refusal}; {hint}"


class TetherShell:
    """The minimal PySide6 shell hosting the trace dock + the focus contract.

    Constructs headlessly (needs a ``QApplication``; ``pyqtgraph.mkQApp`` /
    ``qtbot`` provide one). Installs the curation event filter on the application
    so the four bare keys reach the controller from any of the child surfaces,
    holds the loaded project's single-writer lock for the GUI session, persists
    accept/reject/un-reject, the category and the analysis window (``0``–``9``,
    ``-``/``=``/``[``/``]``, ``R``, ``P``, #359) for the selected molecule when that
    lock is available,
    falls back to read-only browsing otherwise, reports deferred commands explicitly
    unavailable, and exposes the dialogs. The ``I`` key and **Idealize → Idealize
    project…** open the whole-project fit ([`show_idealize_dialog`][], #625); the fit
    runs through the injected ``idealizer`` seam ([`make_store_idealizer`][] wires the
    store-backed ``idealize`` command) with progress and cancel, and the step line of the
    selected molecule is drawn on the dock when it ends. When an ``overlap`` seam is wired
    ([`make_store_overlap`][]), a right-hand static overlap dock shows the selected
    molecule's cached patch + nearest-neighbour distance, refreshed on selection
    (§7.3). The trace dock and the Browser belong to the Curate and correct section,
    one of the sections [`set_section`][] switches between (#341); the curation keys and
    the Curation, Hand-off and Conditions menus act only there (#342). After a stored
    accept or reject the next listed molecule is selected, so one key both decides
    and moves on; ``advance_after_curation=False`` keeps the selection in place
    instead. ``keymap_file`` is where the curation keymap is loaded from at start and
    saved to by the shortcut editor (#635); ``None`` starts with the defaults and saves
    nothing. Use as a context manager or call [`close`][].
    """

    def __init__(
        self,
        *,
        categories: list[str] | None = None,
        idealizer: Idealizer | None = None,
        handoff: HandoffSeam | None = None,
        overlap: OverlapSeam | None = None,
        conditions: Project | None = None,
        advance_after_curation: bool = True,
        preferences_file: Path | None = None,
        keymap_file: Path | None = None,
    ) -> None:
        from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

        from tether.gui import theme
        from tether.gui.trace_dock import TraceDock

        # Fusion style, palette, style sheet and heading font for the whole
        # application, following the OS light/dark scheme (#675). Idempotent.
        theme.install()

        # The overflow picker's list while no project molecule is selected. There is no
        # placeholder list: an open project's own list is read for the molecule (#633).
        self._categories = list(categories) if categories else []
        # The last category editor opened (#633), kept for tests and inspection.
        self._category_editor: CategoryEditor | None = None
        self._traces: list[TraceView] = []
        self._current_index = -1
        # "Advance after accept or reject" (#631): a stored accept or reject selects
        # the next listed molecule. On by default; the settings store (#330) will
        # carry the preference, until then the caller passes it here.
        self._advance_after_curation = bool(advance_after_curation)
        # The writable project behind Space/Backspace/Delete. It is set only by a
        # successful load_project call: synthetic/demo traces and a bare shell must
        # never acquire an accidental persistence target.
        self._curation_project: Project | None = None
        # Lifecycle handle for the acquired sidecar nonce. It remains populated
        # after a refresh I/O failure even though write seams fail closed, so retry
        # and close can still release exactly the nonce this shell acquired.
        self._session_project: Project | None = None
        # A newly acquired project can also need teardown if switching fails while
        # releasing the prior session. Retain that second nonce and process-local
        # claim independently until rollback release succeeds.
        self._rollback_session_project: Project | None = None
        self._rollback_claimed_project_key: _ProjectOwnerKey | None = None
        self._session_release_lock = Lock()
        # The sidecar lock identifies a process, so two shells in this process would
        # otherwise refresh the same identity and both become writable. This opaque
        # token owns at most one existing project file identity (including hard-link
        # aliases) in the local registry.
        self._project_owner_token = object()
        self._claimed_project_key: _ProjectOwnerKey | None = None
        # Why the loaded project is browse-only. Kept separately from
        # _curation_project so a blocked writer gets an actionable status instead
        # of the bare-shell "load a writable project" message.
        self._curation_read_only_reason: str | None = None
        # The undo and redo lists of this session's curation actions (#632). They belong
        # to the open project and are emptied when it closes, another opens, or it
        # becomes read-only; they are never written to the project file.
        self._undo = UndoStack()
        # What the read-only banner may offer for a lock the open could not take
        # (#626): one-click Recover, typed Take over, or nothing yet.
        self._lock_recovery: Any | None = None
        # The open's refusal text, kept so a timed re-check can rebuild the banner.
        self._lock_refusal: str | None = None
        # A lock a recovery took whose reopen then failed and whose release has not
        # yet succeeded: ``(project path, taken LockInfo)``. The release-retry timer
        # keeps trying its exact nonce, as it does for a rollback session.
        self._recovered_lock: tuple[Path, Any] | None = None
        # The whole-project fit seam (#625): idealizer(fit, cancel=, progress=) -> result.
        # None (the synthetic/no-project default) makes "Idealize project…" report that
        # a project must be loaded; make_store_idealizer(project) wires the real command.
        self._idealizer = idealizer
        # The open "Idealize project…" dialog, retained while it shows, or None.
        self._idealize_dialog: Any | None = None
        # The standalone-tMAVEN hand-off seam (export + return-leg import). None (no
        # project) makes the &Hand-off menu report that a project must be loaded;
        # make_store_handoff(project) wires the real tether.project.handoff pipeline.
        self._handoff = handoff
        # The page of the Analyze section (#374): the plot registry, the layer list and
        # the Matplotlib preview. load_project points it at the open project; it imports
        # Matplotlib only when it first draws a figure, so the default shell stays light.
        from tether.gui.analysis_section import AnalysisSection

        self._analysis_section = AnalysisSection(status=lambda text: self._status(text))
        # The static overlap-view seam: molecule_key -> patch + NN distance. None (no
        # project) leaves the dock unbuilt; make_store_overlap(project) wires the real
        # per-molecule neighbour report. The dock is built lazily on the first
        # selection with an overlap seam so the default shell stays light, and it is
        # refreshed from _on_list_row_changed as the curator moves between molecules.
        self._overlap_seam = overlap
        self._overlap_dock: Any | None = None
        self._overlap_dock_widget: Any | None = None
        # The embedded napari movie panel (#356, PRD §7.3). It is built when an opened
        # project has a movie whose file can be opened, docked in Curate and correct, and
        # torn down with the project, so a bare shell never builds a napari Viewer. The
        # shell's movie switcher, not the panel's own, chooses the movie: the shell owns
        # the switcher (it is a focus surface of the curation keys), and the panel's
        # ``switcher`` property is never built. ``_movie_rows`` maps a switcher row to the
        # panel's index of that movie; a movie that cannot be shown has no entry.
        self._movie_panel: Any | None = None
        self._movie_dock_widget: Any | None = None
        self._movie_readers: list[Any] = []
        self._project_movies: list[Any] = []
        self._movie_rows: dict[int, int] = {}
        self._movie_aperture_radius: float | None = None
        # Movies found again on this computer (#638), by checksum, for this session; with a
        # preferences file they are remembered there too.
        self._movie_locations: dict[str, Any] = {}
        # The trace↔movie round-trip (#357, PRD §7.3): a RoundTripNavigator over the
        # panel, rebuilt with the molecule list. ``_roundtrip_rows`` maps a navigator
        # molecule index to its list row and ``_roundtrip_site_of_row`` the reverse; a
        # molecule with no spot in a shown movie has no entry. ``_roundtrip_error`` says
        # why there is no navigator when building one failed. ``_selecting_from_movie``
        # is set while a canvas click selects a row, so that selection does not move
        # the camera the user just clicked in.
        self._roundtrip: Any | None = None
        self._roundtrip_rows: list[int] = []
        self._roundtrip_site_of_row: dict[int, int] = {}
        self._roundtrip_error: str | None = None
        self._selecting_from_movie = False
        # Synchronized scrubbing (#366): a FrameLink between the trace dock's frame
        # cursor and the movie panel's frame, built with the round-trip. Whether it is
        # on is Curate and correct's own state (its Curation menu toggles it), kept for
        # this window only: it is not a preference and does not outlive the shell.
        self._frame_link: Any | None = None
        self._frame_link_enabled = True
        # The condition-validation seam: the .tether Project whose provisional
        # per-molecule condition_ids the &Conditions menu confirms/corrects (§7.6).
        # None (no project) makes the menu report that a project must be loaded; a real
        # Project wires ConditionValidationDialog over the headless re-key core (§5.1).
        self._conditions = conditions
        # The currently open modal wrapper, retained so timer-detected lock loss can
        # reject it before its stale Project handle reaches another write action.
        self._active_conditions_dialog: Any | None = None
        # The summary of the last correction pass (#360), shown non-modal, or None.
        self._correction_summary: Any | None = None
        # A fit abandoned by shell close remains a writer until its Future settles.
        # Keep that Future separate from the visible idealization state so the
        # nonce-safe release timer waits for the writer and retries teardown errors.
        self._session_release_future: Any | None = None
        # What this computer remembers between launches (#628, ADR-0122): recent
        # projects, the molecule selected at close, the layout and the curator name.
        # None (a bare or test shell) remembers nothing; tether-gui passes the per-user
        # file of tether.gui.preferences. Nothing of it is written into a project.
        self._preferences_file = preferences_file
        # The curator name set for new labels, or None: then the project writes the
        # operating-system user, exactly as before #628.
        self._curator_name: str | None = (
            None if preferences_file is None else preferences.curator_name(preferences_file)
        )

        self._window = QtWidgets.QMainWindow()
        self._window.setWindowTitle(_APP_NAME)
        # Refresh a writable GUI session well before the canonical 30-minute
        # staleness boundary. Retained refresh preserves the acquisition nonce and
        # refuses a vanished/replaced sidecar, keeping long fits bound to one epoch.
        from tether.project.lock import DEFAULT_STALENESS_TIMEOUT_S

        self._lock_refresh_timer = QtCore.QTimer()
        self._lock_refresh_timer.setInterval(max(1000, int(DEFAULT_STALENESS_TIMEOUT_S * 1000 / 3)))
        self._lock_refresh_timer.timeout.connect(self._refresh_project_lock)
        self._lock_release_timer = QtCore.QTimer()
        self._lock_release_timer.setInterval(_LOCK_RELEASE_RETRY_MS)
        self._lock_release_timer.timeout.connect(self._retry_session_release)
        # Judge a refused lock again once it can become stale (#626), so the banner
        # offers Take over without a reopen.
        self._lock_recovery_timer = QtCore.QTimer()
        self._lock_recovery_timer.setSingleShot(True)
        self._lock_recovery_timer.timeout.connect(self._recheck_lock_recovery)
        self._analysis_refresh_timer = QtCore.QTimer()
        self._analysis_refresh_timer.setSingleShot(True)
        self._analysis_refresh_timer.setInterval(_HISTOGRAM_REFRESH_DEBOUNCE_MS)
        self._analysis_refresh_timer.timeout.connect(self._run_queued_analysis_refresh)
        self._readiness_refresh_timer = QtCore.QTimer()
        self._readiness_refresh_timer.setSingleShot(True)
        self._readiness_refresh_timer.setInterval(_READINESS_REFRESH_DEBOUNCE_MS)
        self._readiness_refresh_timer.timeout.connect(self.refresh_readiness)
        self._readiness_refresh_timer.timeout.connect(self._refresh_states_if_due)
        self._states_refresh_due = False

        # Central surface: the pyqtgraph trace dock, behind the start page that shows
        # while no project is open and no molecules are loaded (#629).
        from tether.gui.start_page import StartPage

        self._trace_dock = TraceDock()
        # The path of the project load_project opened, or None (no project open).
        self._project_path: Path | None = None
        # The readiness of each stage of the open project (#896), read on each open.
        self._readiness: Any | None = None
        # The controls that declare a stage as their requirement (#898), in the order made.
        self._requirement_gates: list[RequirementGate] = []
        # The intensity_quantity that open used, so a lock recovery reopens the same
        # trace layer rather than load_project's default (#1097).
        self._intensity_quantity = "corrected"
        self._start_page = StartPage(
            on_open=lambda: self._open_project_dialog(),
            on_tutorial=self.open_tutorial,
        )
        # The page of the Curate and correct section: the start page while nothing is
        # loaded, else the trace dock. The section stack itself is built further down.
        self._curate_stack = QtWidgets.QStackedWidget()
        self._curate_stack.addWidget(self._start_page.widget)
        self._curate_stack.addWidget(self._trace_dock.widget)
        # A .tether file dropped anywhere on the window opens it (#629).
        self._window.setAcceptDrops(True)

        # Left browser panel: movie switcher + molecule list + the entry point of the
        # category editor (#633), whose name field is the text entry exempt from the keymap.
        panel = QtWidgets.QWidget()
        vbox = QtWidgets.QVBoxLayout(panel)
        # No invented movie id: the box lists the open project's /movies rows (#356),
        # stays empty, and is disabled while no project is open (#1031).
        self._movie_switcher = QtWidgets.QComboBox()
        self._movie_switcher.setAccessibleName("Movie")
        self._movie_switcher.setEnabled(False)
        self._movie_switcher.currentIndexChanged.connect(self._on_movie_switched)
        self._molecule_list = QtWidgets.QListWidget()
        self._molecule_list.currentRowChanged.connect(self._on_list_row_changed)
        # Each row's curation state, the progress counts and the filter (#630).
        from tether.gui.molecule_list import MoleculeListView

        self._molecule_view = MoleculeListView(
            self._molecule_list,
            movie_name=self._movie_name,
            on_filter_changed=self._on_molecule_filter_changed,
        )
        self._categories_button = QtWidgets.QPushButton("Categories…")
        self._categories_button.setAccessibleName("Edit the categories")
        self._categories_button.setToolTip(
            "Add, rename, remove or reorder the categories of the selected molecule's "
            "condition. Keys 1 to 9 give its first nine categories."
        )
        self._categories_button.clicked.connect(lambda: self.show_category_editor())
        self._unreject_button = QtWidgets.QPushButton("Un-reject selected")
        self._unreject_button.setToolTip(
            "Reverse a rejected trace to uncurated and append the reversal to /labels"
        )
        self._read_only_banner = QtWidgets.QLabel()
        self._read_only_banner.setAccessibleName("Read-only project warning")
        self._read_only_banner.setWordWrap(True)
        # The theme's style sheet draws it in the "caution" state token.
        self._read_only_banner.setProperty("tetherRole", "caution")
        self._read_only_banner.hide()
        # OneDrive/SharePoint sync-conflict siblings of the open project (PRD §5.4,
        # detect-and-surface): a non-blocking warning, never a dialog or a refusal.
        self._conflict_copy_banner = QtWidgets.QLabel()
        self._conflict_copy_banner.setAccessibleName("Sync-conflict copy warning")
        self._conflict_copy_banner.setWordWrap(True)
        # File names are shown as written: a name holding "<b>" must not become markup.
        self._conflict_copy_banner.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self._conflict_copy_banner.setProperty("tetherRole", "caution")
        self._conflict_copy_banner.hide()
        # The banner's lock actions (#626), shown only when the lock allows one.
        self._recover_button = QtWidgets.QPushButton("Recover")
        self._recover_button.setAccessibleName("Recover the project lock")
        self._recover_button.setToolTip(
            "A Tether session of yours on this computer stopped without releasing the "
            "lock. Take the lock and reopen the project writable."
        )
        self._recover_button.clicked.connect(lambda: self.recover_project_lock())
        self._recover_button.hide()
        self._take_over_button = QtWidgets.QPushButton("Take over…")
        self._take_over_button.setAccessibleName("Take over the project lock")
        self._take_over_button.setToolTip(
            "The lock holder has been idle past the staleness timeout. Take the lock "
            "after a typed confirmation; the holder's unsaved work can be lost."
        )
        self._take_over_button.clicked.connect(lambda: self.take_over_project_lock())
        self._take_over_button.hide()
        # Section titles use the heading font (Red Hat Display, else the system font).
        self._section_titles = [QtWidgets.QLabel(t) for t in ("Movie", "Molecules", "Categories")]
        for title in self._section_titles:
            title.setFont(theme.heading_font())
        movie_title, molecules_title, category_title = self._section_titles
        vbox.addWidget(movie_title)
        vbox.addWidget(self._movie_switcher)
        vbox.addWidget(self._read_only_banner)
        lock_actions = QtWidgets.QHBoxLayout()
        lock_actions.addWidget(self._recover_button)
        lock_actions.addWidget(self._take_over_button)
        lock_actions.addStretch(1)
        vbox.addLayout(lock_actions)
        vbox.addWidget(self._conflict_copy_banner)
        vbox.addWidget(molecules_title)
        vbox.addWidget(self._molecule_view.header)
        vbox.addWidget(self._molecule_view.filter_box)
        self.require(self._molecule_view.filter_box, "traces")
        vbox.addWidget(self._molecule_list, stretch=1)
        vbox.addWidget(category_title)
        vbox.addWidget(self._categories_button)
        self.require(self._categories_button, "traces")
        vbox.addWidget(self._unreject_button)
        # The list comes before the filter box in the focus chain, so a window that
        # takes focus (the browser floated, the app activated) never gives it to the box
        # first: the box keeps its own keys, and the curation keys must act there at once.
        # The box is one Tab after the list.
        QtWidgets.QWidget.setTabOrder(self._molecule_list, self._molecule_view.filter_box)
        self._browser_panel = panel

        # The sections (#341, ADR-0116): the central widget of the one QMainWindow is a
        # QStackedWidget with one page per registered section, and each dock belongs to
        # one section and shows only while that section is current. A section switch
        # changes only which page and which docks show; it never touches the session lock.
        # The reconciliation page of a new trace generation (#923), while it is open.
        self._generation_page: GenerationReconcilePage | None = None
        self._sections = self._build_sections()
        self._section_stack = QtWidgets.QStackedWidget()
        self._section_pages: dict[str, Any] = {}
        self._shown_sections = self._sections.visible_sections()
        self._section_docks: dict[str, list[Any]] = {s.id: [] for s in self._shown_sections}
        # Whether a dock of a section that is not current shows again when its section does;
        # kept so a dock the user closed stays closed across a switch.
        self._dock_restore: dict[Any, bool] = {}
        # Each dock's default area, so View -> Reset layout can return a dock built after
        # the default layout was captured to its default place too (#628).
        self._dock_default_area: dict[Any, Any] = {}
        # The docks the stored layout names as hidden only by their section; a dock built
        # after the restore reads it too (#628).
        self._stored_section_hidden: set[str] = set()
        self._section: str = _DEFAULT_SECTION
        # The menus of each section (#342), filled when the menu bar is built below.
        self._section_menus: dict[str, list[Any]] = {s.id: [] for s in self._shown_sections}
        dock_widgets: dict[tuple[str, str], Any] = {}
        for spec in self._shown_sections:
            page = spec.central(self)
            self._section_stack.addWidget(page)
            self._section_pages[spec.id] = page
            for dock in spec.docks:
                dock_widgets[spec.id, dock.id] = self._attach_section_dock(
                    spec.id, dock.title, dock.factory(self), dock.area
                )
        self._window.setCentralWidget(self._section_stack)
        # The section bar (#897): the sections for the mouse, then the guided next step.
        # It is built here so the default layout kept below holds it; the actions are
        # added with the View menu.
        self._section_bar = self._window.addToolBar("Sections")
        self._section_bar.setObjectName("sections-toolbar")
        self._section_bar.setMovable(False)
        # The bar is the way to the sections, so the window's dock menu cannot hide it.
        self._section_bar.toggleViewAction().setVisible(False)
        self._browser_dock = dock_widgets[_DEFAULT_SECTION, "browser"]
        self._show_section(_DEFAULT_SECTION)
        # Build the window-wide Messages dock before saving the default layout (#627).
        from tether.gui.messages import Level, MessageCenter, MessagesPanel

        self._messages = MessageCenter(self._window)
        self._messages_panel = MessagesPanel(self._messages)
        self._messages_dock = QtWidgets.QDockWidget("Messages", self._window)
        self._messages_dock.setObjectName("messages")
        self._messages_dock.setWidget(self._messages_panel.widget)
        self._messages_dock.toggleViewAction().setText("&Messages")
        self._window.addDockWidget(
            getattr(QtCore.Qt.DockWidgetArea, DockArea.BOTTOM.qt_name), self._messages_dock
        )
        self._messages_dock.hide()
        # The default layout, kept before a stored one is restored, for View -> Reset
        # layout (#628). A window never shown reports Qt's placeholder size and takes its
        # real one from its size hint on the first show; adjustSize applies that sizing now,
        # so the size kept is the one a fresh launch shows.
        self._window.adjustSize()
        self._default_size = self._window.size()
        self._default_state = self._window.saveState()
        self._default_docks = set(self._dock_default_area)

        # Curation controller + keymap + the application-level event filter.
        self._controller = CurationController(self._build_handlers())
        self._unreject_button.clicked.connect(
            lambda: self._controller.dispatch(Command(CurationAction.UNREJECT))
        )
        # The saved keymap (#635): tether-gui passes default_keymap_path(); a bare or test
        # shell passes nothing and starts with the defaults. A file that cannot be used
        # gives the defaults and one message, and stays as it is on disk.
        self._keymap_file = keymap_file
        self._keymap, self._keymap_error = _load_keymap(keymap_file)
        self._keymap_editor: Any | None = None
        self._event_filter = CurationEventFilter(
            self._controller,
            self._keymap,
            focus_dock=self._trace_dock.widget,
            scope_window=self._window,
            scope_widgets=(self._browser_dock,),
            # The curation keys act only in a section with the curation scope (#342).
            scope_active=self._curation_keys_active,
        )
        self._event_filter.install()
        self._closed = False

        # A host may reuse QApplication or disable quit-on-last-window. Observe the
        # exposed QMainWindow's Close event directly so its session lock and the
        # application-wide shortcut filter never outlive the visible shell.
        outer = self

        drag_events = (QtCore.QEvent.Type.DragEnter, QtCore.QEvent.Type.DragMove)

        class _WindowCloseFilter(QtCore.QObject):
            def eventFilter(self, watched: Any, event: Any) -> bool:  # noqa: N802
                event_type = event.type()
                if event_type == QtCore.QEvent.Type.Close:
                    outer._close(close_window=False)
                elif event_type in drag_events and _local_paths(event.mimeData()):
                    # Accept any local file so the drop arrives and a wrong type gets
                    # its one refusal message (rather than a silent no-entry cursor).
                    event.acceptProposedAction()
                    return True
                elif event_type == QtCore.QEvent.Type.Drop:
                    paths = _local_paths(event.mimeData())
                    if paths:
                        event.acceptProposedAction()
                        outer.open_dropped(paths)
                        return True
                return False

        self._window_close_filter = _WindowCloseFilter(self._window)
        self._window.installEventFilter(self._window_close_filter)

        # QApplication teardown is the second lifecycle route (for hosts that
        # quit without first sending the main window a Close event).
        self._app = QtWidgets.QApplication.instance()
        self._app.aboutToQuit.connect(self.close)

        # File menu: open a produced/extracted .tether live in this shell for
        # curation + the project fit — the store<->shell hookup (§7.8, FR-LEGACY;
        # the general capability the M7 Deep-LASI wizard hands off into).
        # Open / Close project / Quit carry the platform's standard keys (#629); every
        # one has a modifier, so none shadows a bare curation key (§7.3).
        std = QtGui.QKeySequence.StandardKey
        self._file_menu = self._window.menuBar().addMenu("&File")
        self._act_open_project = self._file_menu.addAction("&Open project…")
        _set_standard_shortcuts(self._act_open_project, std.Open, "Ctrl+O")
        self._act_open_project.triggered.connect(self._open_project_dialog)
        self._act_close_project = self._file_menu.addAction("&Close project")
        _set_standard_shortcuts(self._act_close_project, std.Close, "Ctrl+W")
        self._act_close_project.triggered.connect(self.close_project)
        self._act_close_project.setEnabled(False)
        # File -> Open recent (#628): the last projects opened on this computer, newest
        # first; one whose file is missing shows disabled, with its path.
        self._recent_menu = QtWidgets.QMenu("Open &recent", self._window)
        self._file_menu.insertMenu(self._act_close_project, self._recent_menu)
        self._recent_menu.aboutToShow.connect(self._sync_recent_menu)
        self._sync_recent_menu()
        # Save as recipe (#899): the requests of the open project's completed jobs, as a
        # recipe file that `tether run` runs again on a new copy of the inputs.
        self._act_save_recipe = self._file_menu.addAction("Save as &recipe…")
        self._act_save_recipe.triggered.connect(self._save_recipe_dialog)
        self._act_save_recipe.setEnabled(False)
        # File -> Export (#621): the three project exports, from the open project.
        self._build_export_menu()
        self._file_menu.addSeparator()
        # The name written into new labels (#628). Shown in the status bar too, so a
        # user on a shared computer sees whose labels they are writing.
        self._act_curator_name = self._file_menu.addAction("Curator &name…")
        self._act_curator_name.triggered.connect(self._curator_name_dialog)
        # Whether a launch reopens the last project, in the section of its next step
        # (#1159). The answer lives in the preferences file, never in a project.
        self._act_reopen_last = self._file_menu.addAction("Reopen &last project at start")
        self._act_reopen_last.setCheckable(True)
        self._act_reopen_last.setChecked(
            preferences_file is not None and preferences.reopen_last_project(preferences_file)
        )
        self._act_reopen_last.setEnabled(preferences_file is not None)
        self._act_reopen_last.toggled.connect(self.set_reopen_last_project)
        self._file_menu.addSeparator()
        self._act_quit = self._file_menu.addAction("&Quit")
        _set_standard_shortcuts(self._act_quit, std.Quit, "Ctrl+Q")
        self._act_quit.setMenuRole(QtGui.QAction.MenuRole.QuitRole)
        self._act_quit.triggered.connect(self.quit)

        # Edit menu (#632): undo and redo of the curation actions of this session. Each
        # chord has a modifier, so neither shadows a bare curation key (§7.3). A focused
        # text field keeps Ctrl+Z for its own text, as Qt gives it the shortcut first.
        self._edit_menu = self._window.menuBar().addMenu("&Edit")
        self._act_undo = self._edit_menu.addAction("&Undo")
        self._act_undo.setShortcuts([QtGui.QKeySequence("Ctrl+Z")])
        self._act_undo.triggered.connect(self.undo)
        self._act_redo = self._edit_menu.addAction("&Redo")
        self._act_redo.setShortcuts([QtGui.QKeySequence(key) for key in _redo_keys()])
        self._act_redo.triggered.connect(self.redo)
        self._sync_undo_actions()

        # The shipped cheat-sheet: it lists the keys that act in the current section (#342).
        self._cheatsheet = CheatSheetOverlay(
            self._keymap,
            parent=self._window,
            unavailable=DEFERRED_ACTIONS,
            note=self._advance_note(),
            live=self._curation_keys_active,
            category_names=self.category_names,
            inactive_text=(
                "The curation keys act only in the "
                f"{self._sections.get(CURATE_SECTION).title} section. To use them, choose "
                f"View → Section → {self._sections.get(CURATE_SECTION).title}."
            ),
        )

        # The menus of the sections (#342): built from each section's MenuSpec, in the
        # order of the registry, between the window's own File and Edit menus and its View and Help
        # menus. A section's menus show only while that section is current.
        menu_bar = self._window.menuBar()
        for spec in self._shown_sections:
            for menu_spec in spec.menus:
                section_menu = menu_bar.addMenu(menu_spec.title)
                section_menu.setObjectName(f"{spec.id}-{menu_spec.id}-menu")
                if menu_spec.populate is not None:
                    menu_spec.populate(self, section_menu)
                self._section_menus[spec.id].append(section_menu)
        self._sync_section_menus()

        # View menu: View -> Theme -> System / Light / Dark (#675). The choice lasts
        # for the session until the settings store (#330) can keep it.
        self._view_menu = self._window.menuBar().addMenu("&View")
        self._theme_menu = self._view_menu.addMenu("&Theme")
        self._theme_group = QtGui.QActionGroup(self._window)
        self._theme_actions: dict[str, Any] = {}
        for value, label in (("system", "&System"), ("light", "&Light"), ("dark", "&Dark")):
            action = self._theme_menu.addAction(label)
            action.setCheckable(True)
            action.setData(value)
            self._theme_group.addAction(action)
            action.triggered.connect(lambda _checked=False, v=value: theme.set_mode(v))
            self._theme_actions[value] = action
        self._theme_menu.aboutToShow.connect(self._sync_theme_menu)
        self._sync_theme_menu()
        # View -> Section: one checkable item per visible section (#341), with Ctrl+1,
        # Ctrl+2, ... in the order of the registry. The same actions are the buttons of
        # the section bar, so the mouse and the keyboard open each section the same way
        # (direct navigation, #897). Then the guided next step.
        self._section_menu = self._view_menu.addMenu("&Section")
        self._section_group = QtGui.QActionGroup(self._window)
        self._section_actions: dict[str, Any] = {}
        for number, spec in enumerate(self._shown_sections, start=1):
            action = self._section_menu.addAction(spec.title)
            action.setCheckable(True)
            action.setData(spec.id)
            if number <= 9:
                action.setShortcut(QtGui.QKeySequence(f"Ctrl+{number}"))
            self._section_group.addAction(action)
            action.triggered.connect(lambda _checked=False, s=spec.id: self.set_section(s))
            self._section_actions[spec.id] = action
            self._section_bar.addAction(action)
        self._section_actions[self._section].setChecked(True)
        self._section_menu.addSeparator()
        self._act_next_step = self._section_menu.addAction("Go to the next step")
        self._act_next_step.setObjectName("next-step")
        self._act_next_step.triggered.connect(self.go_to_next_step)
        self._section_bar.addSeparator()
        self._next_step_label = QtWidgets.QLabel()
        self._next_step_label.setObjectName("next-step-label")
        self._next_step_label.setWordWrap(False)
        self._section_bar.addWidget(self._next_step_label)
        self._section_bar.addAction(self._act_next_step)
        self._sync_guided()
        self._view_menu.addAction(self._messages_dock.toggleViewAction())
        # View -> Reset layout (#628): the default window size and dock positions, and
        # the stored layout forgotten, so the next launch starts from the default too.
        self._view_menu.addSeparator()
        self._act_reset_layout = self._view_menu.addAction("&Reset layout")
        self._act_reset_layout.triggered.connect(self.reset_layout)

        # View -> Trace colors (#636): the colour-blind-safe set is the default; the
        # classic green and red set is the option. The choice is kept in the settings
        # store (ADR-0123) and create_shell applies it at the next start.
        self._trace_colors_menu = self._view_menu.addMenu("Trace &colors")
        self._trace_colors_group = QtGui.QActionGroup(self._window)
        self._trace_colors_actions: dict[str, Any] = {}
        for value, label in (
            ("safe", "Color-blind &safe (default)"),
            ("classic", "&Classic green and red"),
        ):
            action = self._trace_colors_menu.addAction(label)
            action.setCheckable(True)
            action.setData(value)
            self._trace_colors_group.addAction(action)
            action.triggered.connect(lambda _checked=False, v=value: self.set_trace_colors(v))
            self._trace_colors_actions[value] = action
        self._trace_colors_menu.aboutToShow.connect(self._sync_trace_colors_menu)
        self._sync_trace_colors_menu()

        # Help menu (#629): the installed version's documentation, the shortcut
        # cheat-sheet (the same action as Curation's), the log folder once a log
        # exists, and the About box with the build a bug report needs.
        self._help_menu = self._window.menuBar().addMenu("&Help")
        self._act_documentation = self._help_menu.addAction("&Documentation")
        _set_standard_shortcuts(self._act_documentation, std.HelpContents, "F1")
        self._act_documentation.triggered.connect(self.open_documentation)
        self._help_menu.addAction(self._act_shortcuts)
        self._act_log_folder = self._help_menu.addAction("Open &log folder")
        self._act_log_folder.triggered.connect(self.open_log_folder)
        self._help_menu.aboutToShow.connect(self._sync_help_menu)
        self._help_menu.addSeparator()
        self._act_about = self._help_menu.addAction("&About Tether")
        self._act_about.setMenuRole(QtGui.QAction.MenuRole.AboutRole)
        self._act_about.triggered.connect(self.show_about)
        self._about: Any | None = None
        self._sync_help_menu()

        # Interface icons (#677): each menu item that has an icon in the brand kit
        # shows it beside its text (never instead of it), in the active theme.
        # Every other menu item keeps text only; later shell work (#341) adds more.
        self._menu_icons: list[tuple[Any, str]] = [
            (self._act_open_project, "project"),
            (self._act_assign_category, "curate"),
            (self._act_hand_off, "hand-off"),
            (self._act_import, "import"),
            (self._act_import_deeplasi, "import"),
            (self._act_validate_conditions, "conditions"),
        ]
        self._apply_menu_icons(theme.current())
        theme.subscribe(self._apply_menu_icons)

        # Busy indicator (#677): the wavefront mark and the task name, at the right
        # of the status bar, while a background task runs (the task service, #343);
        # a task that reports progress, such as the project fit, shows its percentage.
        from tether.gui.indicators import BusyIndicator

        self._busy_indicator = BusyIndicator()
        self._window.statusBar().addPermanentWidget(self._busy_indicator)
        # The fit runs on the background-task service (#343): one worker thread (the
        # sidecar can block for a cold-JIT first run), polled by a main-thread QTimer so
        # the GUI stays live and the overlay draw + status update happen on the main
        # thread. The service sets the wait cursor and drives the busy indicator.
        self._tasks = TaskService(poll_ms=_IDEALIZE_POLL_MS, indicator=self._busy_indicator)
        # Locate / download a movie (#638) reads files only, never the project's lock, so it
        # has a worker of its own: a long checksum read never holds up a curation write.
        self._movie_tasks = TaskService(poll_ms=_IDEALIZE_POLL_MS, indicator=self._busy_indicator)
        self._curator_label = QtWidgets.QLabel()
        self._curator_label.setAccessibleName("Curator name")
        self._window.statusBar().addPermanentWidget(self._curator_label)
        self._sync_curator_label()

        # The stored layout, last: every dock built at startup exists by now (#628).
        self._restore_layout()

        # The one message of an unusable keymap file (#635), else the ready line, which
        # names the current accept and reject keys rather than the defaults.
        startup_level = Level.WARNING if self._keymap_error is not None else Level.INFO
        self._messages.post(self._keymap_error or self._ready_message(), startup_level)
        self._sync_start_page()

    # --- accessors -----------------------------------------------------------

    @property
    def window(self) -> QtWidgets.QMainWindow:
        """The wrapped ``QMainWindow`` hosting the docks, menu bar, and status bar."""
        return self._window

    @property
    def channel_map(self) -> Any:
        """The Calibration menu's actions.

        A [`ChannelMapActions`][tether.gui.calibration.ChannelMapActions] (#353).
        """
        return self._channel_map

    @property
    def trace_dock(self) -> Any:
        """The `TraceDock` on the page of the Curate and correct section."""
        return self._trace_dock

    # --- sections (#341, ADR-0116) -----------------------------------------

    @property
    def sections(self) -> SectionRegistry:
        """The [`SectionRegistry`][tether.gui.sections.SectionRegistry] the shell hosts."""
        return self._sections

    @property
    def section_stack(self) -> QtWidgets.QStackedWidget:
        """The central widget of the window: one page per registered section, in order."""
        return self._section_stack

    @property
    def section(self) -> str:
        """The id of the section that shows now."""
        return self._section

    @property
    def section_actions(self) -> dict[str, Any]:
        """The checkable **View → Section** actions, by section id."""
        return dict(self._section_actions)

    def section_page(self, section_id: str) -> QtWidgets.QWidget:
        """The page of ``section_id`` in [`section_stack`][]; raise `KeyError` if unknown."""
        self._visible_section(section_id)
        return self._section_pages[section_id]

    def section_docks(self, section_id: str) -> list[QtWidgets.QDockWidget]:
        """The docks attached to ``section_id`` now, lazily built ones included."""
        self._visible_section(section_id)
        return list(self._section_docks[section_id])

    def _visible_section(self, section_id: str) -> None:
        """Raise `KeyError` unless ``section_id`` is a section the shell shows (#897)."""
        if not self._sections.get(section_id).visible:
            raise KeyError(f"the section {section_id!r} is not shown in this version")

    @property
    def section_bar(self) -> QtWidgets.QToolBar:
        """The section bar: a button per visible section, then the guided next step (#897)."""
        return self._section_bar

    @property
    def next_step_action(self) -> Any:
        """The **Go to** action of the guided mode: it opens the section of the next step."""
        return self._act_next_step

    @property
    def next_step_text(self) -> str:
        """The next step the section bar shows now: the text of ``tether status``."""
        return self._next_step_label.text()

    def go_to_next_step(self) -> str | None:
        """Read the readiness again and open the section of its next step (the guided mode).

        Return the id of the section that shows after, or ``None`` when the next step has no
        section the shell shows (no project, every stage ready, or a hidden section).
        """
        self.refresh_readiness()
        step = guided_step(self._readiness, self._sections)
        if step is None or step.section is None:
            return None
        self.set_section(step.section)
        return step.section

    def require(self, control: Any, stage: str) -> RequirementGate:
        """Disable ``control`` until the stage ``stage`` of the readiness record is done (#898).

        ``control`` is a ``QWidget`` or a ``QAction`` of a section; ``stage`` is a key of
        [`REQUIREMENTS`][tether.gui.requirements.REQUIREMENTS]. The returned
        [`RequirementGate`][tether.gui.requirements.RequirementGate] owns the enabled state,
        the tooltip and the accessible description of the control, and its **Go to this
        step** opens the section of the stage. It follows each read of the readiness.
        """
        gate = RequirementGate(
            control,
            stage,
            go_to=self.set_section,
            section_title=self._shown_section_title,
        )
        self._requirement_gates.append(gate)
        gate.update(self._readiness)
        return gate

    @property
    def requirement_gates(self) -> tuple[RequirementGate, ...]:
        """The gates that [`require`][] made, in the order made (#898)."""
        return tuple(self._requirement_gates)

    def _shown_section_title(self, section_id: str) -> str | None:
        """The title of a section the shell shows, or ``None`` for a hidden or unknown one."""
        if section_id not in self._sections or not self._sections.get(section_id).visible:
            return None
        return self._sections.get(section_id).title

    def _sync_guided(self) -> None:
        """Show the next step of the readiness record on the section bar (#897).

        The gated controls (#898) follow the same record.
        """
        for gate in self._requirement_gates:
            gate.update(self._readiness)
        step = guided_step(self._readiness, self._sections)
        if step is None:
            self._next_step_label.setText("Next step: open or import a project")
            self._act_next_step.setText("Go to the next step")
            self._act_next_step.setEnabled(False)
            return
        self._next_step_label.setText(f"Next step: {step.action}")
        self._next_step_label.setToolTip(step.action)
        if step.section is None:
            self._act_next_step.setText("Go to the next step")
            self._act_next_step.setEnabled(False)
        else:
            self._act_next_step.setText(f"Go to {step.title}")
            self._act_next_step.setEnabled(True)

    def set_section(self, section_id: str) -> None:
        """Show the section ``section_id``: its page, its docks, and no other section's docks.

        A switch changes only what shows. It does not acquire, refresh or release the session
        lock, and it does not change which write controls are enabled (ADR-0055 governs both,
        unchanged). An unknown or hidden id raises `KeyError` and changes nothing.
        """
        self._visible_section(section_id)
        if section_id == self._section:
            return
        self._show_section(section_id)
        self._section_actions[section_id].setChecked(True)
        # An open cheat-sheet follows the section, so it never lists keys that do not act.
        self._cheatsheet.refresh()

    def section_menus(self, section_id: str) -> list[QtWidgets.QMenu]:
        """The menu-bar menus of ``section_id``, in the order of its `MenuSpec`s (#342).

        They show on the menu bar only while ``section_id`` is current; File, View and Help
        are the window's own menus and are in no section's list. Raise `KeyError` if unknown.
        """
        self._visible_section(section_id)
        return list(self._section_menus[section_id])

    @property
    def keymap_active(self) -> bool:
        """Whether the curation keys act now: the current section has the curation scope (#342).

        While it is ``False`` a curation key keeps its native meaning and never reaches the
        [`controller`][], and the cheat-sheet lists no key.
        """
        return self._curation_keys_active()

    def _curation_keys_active(self) -> bool:
        """The section predicate of the event filter and the cheat-sheet (#342)."""
        return self._sections.get(self._section).keymap_scope == CURATION_SCOPE

    def _sync_section_menus(self) -> None:
        """Show the menus of the current section on the menu bar and hide every other one."""
        for section_id, menus in self._section_menus.items():
            for menu in menus:
                menu.menuAction().setVisible(section_id == self._section)

    def _show_section(self, section_id: str) -> None:
        """Make ``section_id`` current: its page in the stack, its docks shown, others hidden.

        The show/hide item of each other section's dock is hidden too, so the window's dock
        menu (a right click on the menu bar) cannot show a dock outside its section.
        """
        for other, docks in self._section_docks.items():
            if other == section_id:
                continue
            for dock_widget in docks:
                if dock_widget not in self._dock_restore:
                    self._dock_restore[dock_widget] = not dock_widget.isHidden()
                dock_widget.hide()
                dock_widget.toggleViewAction().setVisible(False)
        for dock_widget in self._section_docks[section_id]:
            dock_widget.toggleViewAction().setVisible(True)
            if self._dock_restore.pop(dock_widget, True):
                dock_widget.show()
        self._section_stack.setCurrentWidget(self._section_pages[section_id])
        self._section = section_id
        self._sync_section_menus()
        if section_id == "analyze":
            # A fit made in another section may have added a model: read the choices again.
            self._analysis_section.refresh()

    def _attach_section_dock(
        self, section_id: str, title: str, widget: Any, area: DockArea
    ) -> QtWidgets.QDockWidget:
        """Dock ``widget`` into the window as a dock of ``section_id``.

        The dock shows at once when ``section_id`` is current; otherwise it stays hidden
        until that section shows.
        """
        from pyqtgraph.Qt import QtCore, QtWidgets

        dock_widget = QtWidgets.QDockWidget(title, self._window)
        dock_widget.setObjectName(f"{section_id}-{title.lower().replace(' ', '-')}")
        dock_widget.setWidget(widget)
        default_area = getattr(QtCore.Qt.DockWidgetArea, area.qt_name)
        self._dock_default_area[dock_widget] = default_area
        # A dock built after the stored layout was restored takes its stored place (#628);
        # a dock the layout does not know goes to its default area. Restore first:
        # addDockWidget drops the stored placeholder of a dock with the same name.
        # A restored dock keeps its stored visibility, so one the user closed stays closed,
        # unless the layout names it as hidden only by its section.
        restored = self._window.restoreDockWidget(dock_widget)
        if not restored:
            self._window.addDockWidget(default_area, dock_widget)
        shown = (
            not restored
            or dock_widget.objectName() in self._stored_section_hidden
            or not dock_widget.isHidden()
        )
        self._section_docks[section_id].append(dock_widget)
        if section_id == self._section:
            dock_widget.setVisible(shown)
        else:
            self._dock_restore[dock_widget] = shown
            dock_widget.hide()
            dock_widget.toggleViewAction().setVisible(False)
        return dock_widget

    def _detach_section_dock(self, dock_widget: Any) -> None:
        """Remove a lazily-built dock from the window and from its section."""
        self._window.removeDockWidget(dock_widget)
        self._dock_restore.pop(dock_widget, None)
        self._dock_default_area.pop(dock_widget, None)
        for docks in self._section_docks.values():
            if dock_widget in docks:
                docks.remove(dock_widget)

    def _build_sections(self) -> SectionRegistry:
        """The sections this shell hosts, in the order of page 1 of the design (#897).

        Each factory returns a widget the shell already owns or a plain guide page, so the
        registry adds no second owner of any widget. Prepare and Analyze write to the open
        project in later work (#352 publishes an extraction into it; analysis results are
        stamped into it), so each declares ``writes_project=True``, as Curate and correct does.

        Each section also declares its menus (#342): Import and Calibration (#353) in
        Prepare; Curation, Hand-off and Conditions in Curate and correct. Analyze has no
        menu: its page is the plot builder (#374), which replaced the one-action Analysis
        menu. Only Curate and correct declares the curation keymap scope, so the curation
        keys act only there.
        """
        browser_panel = self._browser_panel
        return SectionRegistry(
            [
                SectionSpec(
                    id=PREPARE_SECTION,
                    title="Prepare",
                    order=1,
                    central=lambda _host: self._build_extraction_page(),
                    writes_project=True,
                    docks=(
                        DockSpec(
                            id="import",
                            title="Import",
                            area=DockArea.LEFT,
                            factory=lambda _host: self._build_import_page(),
                        ),
                    ),
                    menus=(
                        MenuSpec(
                            id="import",
                            title="&Import",
                            populate=lambda _host, menu: self._populate_import_menu(menu),
                        ),
                        MenuSpec(
                            id="calibration",
                            title="Ca&libration",
                            populate=lambda _host, menu: self._populate_calibration_menu(menu),
                        ),
                    ),
                ),
                SectionSpec(
                    id=CURATE_SECTION,
                    title="Curate and correct",
                    order=2,
                    central=lambda _host: self._curate_stack,
                    writes_project=True,
                    docks=(
                        DockSpec(
                            id="browser",
                            title="Browser",
                            area=DockArea.LEFT,
                            factory=lambda _host: browser_panel,
                        ),
                    ),
                    menus=(
                        MenuSpec(
                            id="curation",
                            title="&Curation",
                            populate=lambda _host, menu: self._populate_curation_menu(menu),
                        ),
                        MenuSpec(
                            id="hand-off",
                            title="&Hand-off",
                            populate=lambda _host, menu: self._populate_handoff_menu(menu),
                        ),
                        MenuSpec(
                            id="conditions",
                            title="&Conditions",
                            populate=lambda _host, menu: self._populate_conditions_menu(menu),
                        ),
                        MenuSpec(
                            id="corrections",
                            title="Co&rrections",
                            populate=lambda _host, menu: self._populate_corrections_menu(menu),
                        ),
                        MenuSpec(
                            id="idealize",
                            title="I&dealize",
                            populate=lambda _host, menu: self._populate_idealize_menu(menu),
                        ),
                    ),
                    keymap_scope=CURATION_SCOPE,
                ),
                SectionSpec(
                    id=ANALYZE_SECTION,
                    title="Analyze",
                    order=3,
                    central=lambda _host: self._analysis_section.widget,
                    writes_project=True,
                ),
                SectionSpec(
                    id=DEPOSIT_SECTION,
                    title="Deposit",
                    order=4,
                    central=lambda _host: _guide_page("Deposit", "Deposit is not available yet."),
                    writes_project=True,
                    visible=DEPOSIT_VISIBLE,
                ),
            ]
        )

    def _build_extraction_page(self) -> Any:
        """Prepare's page: one movie, its options, the stage preview and a run (#350)."""
        from tether.gui.extraction import ExtractionSection

        self._extraction = ExtractionSection(
            status=self._status, open_project=self.load_project, window=self._window
        )
        return self._extraction.widget

    def _build_import_page(self) -> Any:
        """Prepare's Import dock (#913): add movies, pair them, set the layout, repair."""
        from tether.gui.import_page import ImportPage

        def use_movie(path: Path, layout_texts: dict[str, str]) -> None:
            self._extraction.set_movie(path)
            for name, text in layout_texts.items():
                self._extraction.set_field(name, text)

        self._import_page = ImportPage(
            status=self._status,
            window=self._window,
            use_movie=use_movie,
            write_blocked=self._idealization_blocks_write,
        )
        return self._import_page.widget

    @property
    def import_page(self) -> Any:
        """Prepare's Import dock: an [`ImportPage`][tether.gui.import_page.ImportPage] (#913)."""
        return self._import_page

    @property
    def extraction(self) -> Any:
        """Prepare's extraction page.

        An [`ExtractionSection`][tether.gui.extraction.ExtractionSection] (#350).
        """
        return self._extraction

    # --- the menus of the sections (#342) --------------------------------------

    def _populate_import_menu(self, menu: Any) -> None:
        """Prepare's Import menu (FR-LEGACY): the M7 Deep-LASI re-analysis entry point.

        It imports a folder of Deep-LASI acquisition files (movie + .tdat/.mat/.txt/SMD)
        into round-trip or analysis-only .tether projects through the wizard (§7.8). It is
        named for what it does (#629).
        """
        self._legacy_menu = menu
        self._act_import_deeplasi = menu.addAction("&Deep-LASI bundle…")
        self._act_import_deeplasi.triggered.connect(self._import_deeplasi_dialog)

    def _populate_calibration_menu(self, menu: Any) -> None:
        """Prepare's Calibration menu (#353): make, choose and apply a channel map file.

        Make a channel map from a bead video or image, or choose a saved one, and extract
        a later movie with it. Each action is a service command (map, extract), run on a
        background worker of its own so the window stays live; the mapping page itself is
        #917.
        """
        from tether.gui.calibration import ChannelMapActions
        from tether.gui.messages import Level

        self._channel_map = ChannelMapActions(
            status=self._status,
            open_project=self.load_project,
            window=self._window,
            warning=lambda message: self._status(message, Level.WARNING),
        )
        self._calibration_menu = menu
        self._act_make_map = menu.addAction("&Make channel map…")
        self._act_make_map.triggered.connect(self._channel_map.make_map_dialog)
        self._act_choose_map = menu.addAction("&Choose channel map…")
        self._act_choose_map.triggered.connect(self._channel_map.choose_map_dialog)
        self._act_extract_with_map = menu.addAction("&Extract a movie with the channel map…")
        self._act_extract_with_map.triggered.connect(self._channel_map.extract_with_map_dialog)

    def _populate_curation_menu(self, menu: Any) -> None:
        """Curate and correct's Curation menu: the cheat-sheet and the overflow picker."""
        self._act_shortcuts = menu.addAction("Keyboard &shortcuts…")
        self._act_shortcuts.triggered.connect(self.show_cheatsheet)
        self._act_edit_shortcuts = menu.addAction("&Edit keyboard shortcuts…")
        self._act_edit_shortcuts.triggered.connect(self.show_keymap_editor)
        self._act_assign_category = menu.addAction("Assign category (&overflow)…")
        self._act_assign_category.triggered.connect(self.show_overflow_picker)
        self._act_edit_categories = menu.addAction("&Categories…")
        self._act_edit_categories.triggered.connect(lambda: self.show_category_editor())
        self.require(self._act_edit_categories, "traces")
        self._act_reconcile_generation = menu.addAction("Reconcile trace &generations…")
        self._act_reconcile_generation.triggered.connect(self.show_generation_reconcile)
        # A navigation action (#630): it selects a row and never reorders the list (§7.5).
        # Its key is the curation key U (the keymap's, so it acts only in this section);
        # a section menu action carries no shortcut of its own.
        self._act_next_undecided = menu.addAction("Next &undecided molecule")
        self._act_next_undecided.triggered.connect(self.select_next_undecided)
        self.require(self._act_next_undecided, "traces")
        menu.addSeparator()
        # Synchronized scrubbing (#366): on unless the user turns it off here.
        self._act_link_frames = menu.addAction("&Link trace and movie frames")
        self._act_link_frames.setCheckable(True)
        self._act_link_frames.setChecked(self._frame_link_enabled)
        self._act_link_frames.setStatusTip(
            "Move the movie with the trace's frame cursor, and the cursor with the movie"
        )
        self._act_link_frames.toggled.connect(self.set_frame_link_enabled)
        # A movie that is missing or not on this disk (#638): find it, or download it.
        self._act_locate_movie = menu.addAction("Locate &movie…")
        self._act_locate_movie.triggered.connect(self._locate_movie_dialog)
        self._act_locate_movie_folder = menu.addAction("Locate movie &folder…")
        self._act_locate_movie_folder.triggered.connect(self._locate_movie_folder_dialog)
        self._act_download_movie = menu.addAction("&Download and open movie…")
        self._act_download_movie.triggered.connect(self._download_movie_dialog)

    def _populate_handoff_menu(self, menu: Any) -> None:
        """Curate and correct's Hand-off menu: the standalone-tMAVEN round trip (§7.4).

        It exports the selection as an SMD, and re-imports a returning SMD (+ optional
        model) through the per-trace reconcile prompt.
        """
        self._handoff_menu = menu
        self._act_hand_off = menu.addAction("Hand to &tMAVEN…")
        self._act_hand_off.triggered.connect(self._hand_off_dialog)
        self._act_import = menu.addAction("&Import return leg…")
        self._act_import.triggered.connect(self._import_dialog)

    def _populate_conditions_menu(self, menu: Any) -> None:
        """Curate and correct's Conditions menu: the M4 annotation step (§5.1/§7.6).

        It confirms or corrects the provisional per-molecule condition_id, with the
        transactional re-key and the human-confirmed merge.
        """
        self._conditions_menu = menu
        self._act_validate_conditions = menu.addAction("Validate &conditions…")
        self._act_validate_conditions.triggered.connect(self._validate_conditions_dialog)

    def _populate_corrections_menu(self, menu: Any) -> None:
        """Curate and correct's Corrections menu: run the correction pass (#360, §7.2).

        The action is enabled from the project's correction state (G03), read each time
        the menu opens; a disabled action says why in its tooltip.
        """
        # A QMenu shows no action tooltip unless asked to, and the tooltip holds the reason.
        menu.setToolTipsVisible(True)
        self._corrections_menu = menu
        self._act_correction_pass = menu.addAction("Run &correction pass…")
        self._act_correction_pass.triggered.connect(self._correction_pass_dialog)
        menu.aboutToShow.connect(self.sync_correction_controls)

    def _populate_idealize_menu(self, menu: Any) -> None:
        """Curate and correct's Idealize menu: the whole-project fit (#625, §7.4).

        Its one action opens the "Idealize project…" dialog, as the ``I`` key does; the
        key is the curation keymap's, so the action carries no shortcut of its own.
        """
        self._idealize_menu = menu
        self._act_idealize_project = menu.addAction("Idealize &project…")
        self._act_idealize_project.triggered.connect(self.show_idealize_dialog)
        # A fit needs extracted traces; a busy or read-only window says why on the status
        # line when the action is chosen, as the I key does.
        self.require(self._act_idealize_project, "traces")

    @property
    def idealize_project_action(self) -> Any:
        """The **Idealize → Idealize project…** action."""
        return self._act_idealize_project

    @property
    def molecule_list(self) -> QtWidgets.QListWidget:
        """The browser-panel ``QListWidget`` whose current row is the selected molecule."""
        return self._molecule_list

    @property
    def movie_switcher(self) -> QtWidgets.QComboBox:
        """The browser-panel ``QComboBox`` of the open project's movies (#356).

        One item per ``/movies`` row, in table order, labelled with the movie's file name;
        the item data is the ``movie_id``. A movie that cannot be shown is listed disabled,
        with the reason. Choosing a movie shows it in the [`movie_panel`][]. It is a
        keymap-reachable focus surface of the curation keys.
        """
        return self._movie_switcher

    @property
    def movie_panel(self) -> Any | None:
        """The embedded [`NapariMoviePanel`][tether.gui.movie_panel.NapariMoviePanel], or ``None``.

        ``None`` while no project is open, for an analysis-only project, and for a project
        none of whose movies can be opened here (#356).
        """
        return self._movie_panel

    @property
    def movie_dock(self) -> QtWidgets.QDockWidget | None:
        """The Curate and correct dock holding the movie panel's window, or ``None``."""
        return self._movie_dock_widget

    @property
    def browser_dock(self) -> QtWidgets.QDockWidget:
        """The Browser dock, including when the curator floats it."""
        return self._browser_dock

    @property
    def unreject_button(self) -> QtWidgets.QPushButton:
        """The visible one-click control that reverses the selected sticky reject."""
        return self._unreject_button

    @property
    def view_menu(self) -> QtWidgets.QMenu:
        """The ``&View`` menu: ``&Theme`` (System, Light, Dark) and ``Trace &colors``."""
        return self._view_menu

    @property
    def theme_actions(self) -> dict[str, Any]:
        """The checkable **View → Theme** actions, by mode (``system``/``light``/``dark``)."""
        return dict(self._theme_actions)

    @property
    def trace_colors_actions(self) -> dict[str, Any]:
        """The checkable **View → Trace colors** actions, by palette (``safe``/``classic``)."""
        return dict(self._trace_colors_actions)

    def set_trace_colors(self, name: str) -> None:
        """Draw the traces in the palette ``name`` now and keep the choice for later starts.

        The plots change at once. If the settings store cannot be written, the choice
        still holds for this session and the status bar says it was not kept.
        """
        from tether.gui import app_settings, theme

        theme.set_trace_palette(name)
        self._sync_trace_colors_menu()
        try:
            app_settings.record_trace_colors(name)
        except OSError as exc:
            self._status(f"Trace colors changed for this session only; not saved: {exc}")

    @property
    def section_titles(self) -> list[QtWidgets.QLabel]:
        """The browser panel's section-title labels (drawn in the heading font)."""
        return list(self._section_titles)

    @property
    def menu_icons(self) -> list[tuple[Any, str]]:
        """The menu actions that show an interface icon, each with its icon name."""
        return list(self._menu_icons)

    @property
    def busy_indicator(self) -> Any:
        """The status-bar `BusyIndicator`."""
        return self._busy_indicator

    def _apply_menu_icons(self, active: Any) -> None:
        """Set each menu icon for ``active``; called again after each change of theme."""
        from tether.gui import theme

        for action, name in self._menu_icons:
            action.setIcon(theme.icon(name, active))
            # Qt hides menu icons where the platform asks (the Cocoa theme does on every
            # macOS before Tahoe); these icons are meant to show on every OS.
            action.setIconVisibleInMenu(True)

    def _sync_theme_menu(self) -> None:
        from tether.gui import theme

        self._theme_actions[theme.mode()].setChecked(True)

    def _sync_trace_colors_menu(self) -> None:
        from tether.gui import theme

        self._trace_colors_actions[theme.trace_palette()].setChecked(True)

    @property
    def read_only_banner(self) -> QtWidgets.QLabel:
        """The persistent Browser warning shown while the loaded project is browse-only."""
        return self._read_only_banner

    @property
    def conflict_copy_banner(self) -> QtWidgets.QLabel:
        """The Browser warning naming possible sync-conflict copies of the open project.

        [`load_project`][] fills it from
        [`conflict_copy_warning`][tether.services.notices.conflict_copy_warning] and hides
        it when the project's directory holds none; closing the project clears it. It only
        reports: Tether never reconciles, deletes or merges a conflict copy (§5.4).
        """
        return self._conflict_copy_banner

    @property
    def recover_button(self) -> QtWidgets.QPushButton:
        """The banner's one-click **Recover** action, shown only after a crash (#626)."""
        return self._recover_button

    @property
    def take_over_button(self) -> QtWidgets.QPushButton:
        """The banner's **Take over…** action, shown only for a stale foreign lock (#626)."""
        return self._take_over_button

    @property
    def lock_recovery(self) -> Any | None:
        """The [`LockRecovery`][tether.project.lock.LockRecovery] verdict the banner shows.

        ``None`` when the project is writable, nothing is open, or browse-only for a
        reason other than a lock this shell could not take.
        """
        return self._lock_recovery

    @property
    def controller(self) -> CurationController:
        """The `CurationController` each command is routed to."""
        return self._controller

    @property
    def keymap(self) -> Keymap:
        """The live [`Keymap`][tether.gui.curation.Keymap] resolving key chords to commands."""
        return self._keymap

    @property
    def keymap_file(self) -> Path | None:
        """The file the keymap is loaded from and saved to, or ``None`` (#635)."""
        return self._keymap_file

    @property
    def keymap_error(self) -> str | None:
        """Why the keymap file could not be used at start, or ``None`` (#635)."""
        return self._keymap_error

    @property
    def event_filter(self) -> CurationEventFilter:
        """The application-level [`CurationEventFilter`][] this shell installs (§7.3)."""
        return self._event_filter

    @property
    def cheatsheet(self) -> CheatSheetOverlay:
        """The [`CheatSheetOverlay`][] this shell shows for ``Keyboard shortcuts…``."""
        return self._cheatsheet

    @property
    def status_message(self) -> str:
        """The latest status message (#627).

        The status bar shows it, unless the bar holds an earlier warning or error until the
        user dismisses it; [`messages`][tether.gui.shell.TetherShell.messages] has both.
        """
        latest = self._messages.latest
        return "" if latest is None else latest.text

    @property
    def messages(self) -> Any:
        """The [`MessageCenter`][tether.gui.messages.MessageCenter] of the window (#627)."""
        return self._messages

    @property
    def messages_panel(self) -> Any:
        """The [`MessagesPanel`][tether.gui.messages.MessagesPanel] in the Messages dock."""
        return self._messages_panel

    @property
    def messages_dock(self) -> QtWidgets.QDockWidget:
        """The Messages dock (**View → Messages**), hidden by default."""
        return self._messages_dock

    # --- molecules -----------------------------------------------------------

    def set_molecules(
        self, traces: list[TraceView], states: Sequence[MoleculeState] | None = None
    ) -> None:
        """Populate the molecule list + dock from ``traces`` (select the first).

        ``states`` are the rows' curation states as
        [`molecule_states`][tether.services.curation.molecule_states] reads them, in the
        same order; each row shows its own (#630). A state whose ``molecule_key`` is not the
        trace's is not shown. The filter goes back to all molecules.

        With a [`movie_panel`][] shown, this also rebuilds the trace↔movie round-trip over
        the new list (#357), so the first selection already moves the movie to its spot.
        """
        from tether.gui.molecule_list import FILTER_ALL

        self._traces = list(traces)
        given = list(states or ())
        matched = [
            given[i] if i < len(given) and given[i].molecule_key == trace.molecule_key else None
            for i, trace in enumerate(self._traces)
        ]
        self._molecule_view.set_filter(FILTER_ALL)
        self._molecule_view.populate(
            [self._row_name(i, trace) for i, trace in enumerate(self._traces)], matched
        )
        self._molecule_view.sync_header(-1)
        self._wire_roundtrip()
        if self._traces:
            self._molecule_list.setCurrentRow(0)
        else:
            self._current_index = -1
            self._trace_dock.clear()
        self._sync_start_page()

    @staticmethod
    def _row_name(row: int, trace: TraceView) -> str:
        """The name a list row shows for ``trace``: its own, else ``mol-<row>``."""
        return trace.name or f"mol-{row}"

    @property
    def molecule_view(self) -> Any:
        """The [`MoleculeListView`][tether.gui.molecule_list.MoleculeListView] (#630)."""
        return self._molecule_view

    def _movie_name(self, movie_id: str) -> str:
        """The name of the project movie ``movie_id``; the id when the project has no such row."""
        names = {movie.movie_id: movie.name for movie in self._project_movies}
        return str(names.get(movie_id, movie_id))

    def _read_molecule_states(self, path: str | Path) -> tuple[MoleculeState, ...]:
        """Read the list state of every molecule; a failed read shows the rows without one."""
        try:
            return molecule_states(path)
        except Exception as exc:  # noqa: BLE001 - the list still opens, without its marks
            from tether.gui.messages import Level

            self._status(f"Molecule states unavailable: {exc}", Level.WARNING)
            return ()

    def _queue_states_refresh(self) -> None:
        """Read every row's state again once a burst of writes ends (#630).

        For the writes whose effect on a row's marks only a fresh read can tell: an
        analysis-window edit can make a fit stale, and a fit or a return leg changes
        many rows. It rides the readiness timer, so it runs between writes, not per key.
        """
        if self._project_path is not None:
            self._states_refresh_due = True
            self._readiness_refresh_timer.start()

    def _refresh_states_if_due(self) -> None:
        if not self._states_refresh_due or self._project_path is None:
            return
        self._states_refresh_due = False
        fresh = self._read_molecule_states(self._project_path)
        if fresh:
            self._molecule_view.replace_states(
                [
                    fresh[i] if i < len(fresh) and fresh[i].molecule_key == t.molecule_key else None
                    for i, t in enumerate(self._traces)
                ]
            )
            self._after_states_changed()

    def _after_states_changed(self) -> None:
        """Count again and move the selection off a row that the filter now hides."""
        self._ensure_current_shown()
        self._molecule_view.sync_header(self._molecule_list.currentRow())

    def _on_molecule_filter_changed(self) -> None:
        """After a new filter: keep a shown selection, and say how many rows it shows."""
        self._ensure_current_shown()
        shown = sum(1 for row in range(len(self._traces)) if self._molecule_view.shown(row))
        self._molecule_view.sync_header(self._molecule_list.currentRow())
        self._status(f"Filter {self._molecule_view.filter_box.currentText()}: {shown} shown")

    def _ensure_current_shown(self) -> None:
        """Move the selection to the next shown row, else the previous, when it is hidden.

        When the filter shows no row, nothing stays selected, so no key acts on a
        molecule the list does not show.
        """
        row = self._molecule_list.currentRow()
        if row < 0 or self._molecule_view.shown(row):
            return
        target = self._molecule_view.shown_from(row, 1)
        if target is None:
            target = self._molecule_view.shown_from(row, -1)
        if target is None:
            self._molecule_list.setCurrentRow(-1)
            self._current_index = -1
            self._trace_dock.clear()
            return
        self._molecule_list.setCurrentRow(target)

    def select_next_undecided(self) -> int | None:
        """Select the next undecided molecule in list order; return its row (#630).

        A navigation action: the list order never changes (§7.5). After the last row the
        search goes on from the top. The status says when no undecided molecule is left,
        or when the filter hides the ones that are.
        """
        row, wrapped = self._molecule_view.next_undecided(self._molecule_list.currentRow())
        if row is None:
            hidden = sum(
                1
                for i, state in enumerate(self._molecule_view.states)
                if state is not None
                and state.curation == UNDECIDED
                and not self._molecule_view.shown(i)
            )
            if hidden:
                self._status(f"No undecided molecule is shown; the filter hides {hidden}")
            else:
                self._status("No undecided molecule is left")
            return None
        self._molecule_list.setCurrentRow(row)
        if wrapped:
            self._status(f"{self.status_message} (from the top of the list)")
        return row

    def _on_list_row_changed(self, row: int) -> None:
        self._molecule_view.sync_header(row)
        # The frame link still names the previous molecule: drop it before the dock shows
        # the new trace and the jump below switches movies, so a frame the panel tells on
        # the way is not mapped onto the new trace with the old one's length. With no row
        # selected (a filter that shows none, #630) it stays dropped: the dock is empty.
        if self._frame_link is not None:
            self._frame_link.clear_target()
        if 0 <= row < len(self._traces):
            self._current_index = row
            trace = self._traces[row]
            self._trace_dock.set_trace(trace)
            # Navigation (via the ←/→/↑/↓ keys or a click) shows which molecule is
            # active — so the live smoke sees NEXT/PREV firing, not just accept.
            self._status(f"Molecule {self._row_name(row, trace)} ({row + 1}/{len(self._traces)})")
            # Refresh the overlap view last, so a seam failure surfaces (overwriting
            # the navigation status) rather than being clobbered by it; a successful
            # refresh sets no status, leaving the molecule message in place.
            self._refresh_overlap(trace)
            # An open cheat-sheet names the categories of this molecule's condition (#633).
            if self._cheatsheet.dialog.isVisible():
                self._cheatsheet.refresh()
            # Trace → movie (#357): a list selection shows the molecule's own movie,
            # centred on its donor spot. A selection made by clicking that spot leaves
            # the camera where the user clicked. A failure is reported; success sets no
            # status, so the molecule message stays.
            if not self._selecting_from_movie:
                self._focus_movie(row)
            # Synchronized scrubbing (#366): link the cursor to this molecule's movie. A
            # list selection shows the cursor frame in the movie; a click on a spot keeps
            # the frame the user was looking at, and moves the cursor there.
            self._retarget_frame_link(row, lead="movie" if self._selecting_from_movie else "trace")

    @property
    def overlap_dock(self) -> Any | None:
        """The static overlap-view dock, or ``None`` until first shown."""
        return self._overlap_dock

    def _refresh_overlap(self, trace: TraceView) -> None:
        """Update the overlap view for the selected molecule (§7.3, no-op when unwired).

        Runs the injected ``overlap`` seam ([`make_store_overlap`][] computes the
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
            # A seam that resolves but returns a shape-invalid
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
        """Dock the (lazily-built) overlap view at the right, in Curate and correct."""
        self._overlap_dock_widget = self._attach_section_dock(
            CURATE_SECTION, "Overlap", self._overlap_dock.widget, DockArea.RIGHT
        )

    # --- the embedded napari movie panel (#356, PRD §7.3) -----------------------

    def _wire_movies(self, movies: Any) -> str | None:
        """Fill the movie switcher from ``movies`` and show the openable ones in a movie panel.

        ``movies`` is the project's [`ProjectMovies`][tether.services.movies.ProjectMovies]:
        its rows, in ``/movies`` order, and the aperture radius. An analysis-only project
        passes none, which leaves the switcher empty and disabled and builds no panel. Each
        movie whose file opens is added to one
        [`NapariMoviePanel`][tether.gui.movie_panel.NapariMoviePanel] with a
        [`MovieOverlay`][tether.gui.movie_panel.MovieOverlay] of its own molecules at that
        radius (the stored extraction ``disk_radius``, or the documented default when the
        project stores none). The panel's Qt window becomes a dock of Curate and correct.

        Returns a note for the open's status message when a recorded movie file cannot be
        shown, or the panel cannot be built here, else ``None``. A movie whose ``uri`` is
        empty records no file, so it is listed (disabled) without a note. A movie whose
        spots cannot be placed (no channel geometry recorded) is shown without spots, and
        its switcher entry says so.
        """
        from pyqtgraph.Qt import QtCore

        self._project_movies = list(movies.movies)
        self._movie_aperture_radius = float(movies.aperture_radius)
        switcher = self._movie_switcher
        switcher.blockSignals(True)
        try:
            switcher.clear()
            for row, movie in enumerate(self._project_movies):
                why = movie.unavailable or movie.no_spots
                label = f"{movie.name} ({why})" if why else movie.name
                switcher.addItem(label, movie.movie_id)
                tooltip = str(movie.path) if movie.path is not None else movie.unavailable
                switcher.setItemData(row, tooltip, QtCore.Qt.ItemDataRole.ToolTipRole)
                if not movie.available:
                    self._disable_movie_row(row)
        finally:
            switcher.blockSignals(False)
        switcher.setEnabled(bool(self._project_movies))

        recorded = [m for m in self._project_movies if m.path is not None]
        not_shown = [m for m in recorded if not m.available]
        openable = [row for row, m in enumerate(self._project_movies) if m.available]
        notes: list[str] = []
        if openable:
            reason = _movie_panel_unavailable()
            panel = None
            if reason is None:
                try:
                    panel = _new_movie_panel()
                except Exception as exc:  # noqa: BLE001 - no movie panel is not a failed open
                    reason = str(exc) or type(exc).__name__
            if panel is None:
                note = f"movie panel unavailable: {reason}"
                notes.append(note)
                # A row that cannot show its movie is not offered as if it could.
                for row in openable:
                    switcher.setItemData(row, note, QtCore.Qt.ItemDataRole.ToolTipRole)
                    self._disable_movie_row(row)
            else:
                not_shown += self._add_movies(panel, openable, movies.aperture_radius)
                self._attach_movie_panel(panel)
        if not_shown:
            notes.insert(0, f"{len(not_shown)} of {len(recorded)} movie(s) not shown")
        return "; ".join(notes) or None

    def _add_movies(self, panel: Any, rows: list[int], radius: float) -> list[Any]:
        """Open the movie of each switcher row in ``rows`` into ``panel``; return the failures.

        A movie that does not open (a file the reader refuses) or that the panel cannot
        prepare (its first frame cannot be displayed) is disabled in the switcher with the
        reason, its reader is closed, and the others are still shown.
        """
        from tether.gui.movie_panel import MovieOverlay
        from tether.services.movies import open_project_movie

        failed: list[Any] = []
        for row in rows:
            movie = self._project_movies[row]
            reader = None
            try:
                reader = open_project_movie(movie)
                # A movie whose spots cannot be placed on the frame is shown without any.
                overlay = (
                    None
                    if movie.no_spots
                    else MovieOverlay(
                        donor_xy=movie.donor_xy,
                        acceptor_xy=movie.acceptor_xy,
                        aperture_radius=radius,
                    )
                )
                index = panel.add_movie(reader, overlay=overlay, name=movie.movie_id)
            except Exception:  # noqa: BLE001 - one unshowable movie must not fail the open
                if reader is not None:
                    with suppress(Exception):
                        reader.close()
                failed.append(movie)
                self._movie_switcher.setItemText(row, f"{movie.name} (cannot be opened)")
                self._disable_movie_row(row)
                continue
            self._movie_readers.append(reader)
            self._movie_rows[row] = index
        return failed

    def _attach_movie_panel(self, panel: Any) -> None:
        """Dock ``panel``'s Qt window in Curate and correct and select its active movie.

        napari's own menu bar is hidden: inside the Tether window its actions would carry
        shortcuts (``Ctrl+W``, ``Ctrl+Q``) that collide with the File menu's, and a hidden
        menu bar's actions do not fire. The viewer itself (canvas, layer list, layer
        controls) shows as it does in napari.
        """
        if not self._movie_rows:
            panel.close()
            return
        self._movie_panel = panel
        window = panel.qt_window
        window.menuBar().hide()
        self._movie_dock_widget = self._attach_section_dock(
            CURATE_SECTION, "Movie", window, DockArea.RIGHT
        )
        # Floated, the dock is its own window; the curation keys still act there.
        self._event_filter.add_scope_widget(self._movie_dock_widget)
        # Select the row of the movie the panel shows, so the switcher names that movie.
        active = [row for row, index in self._movie_rows.items() if index == panel.active_index]
        first_row = active[0] if active else min(self._movie_rows, key=self._movie_rows.__getitem__)
        self._movie_switcher.blockSignals(True)
        self._movie_switcher.setCurrentIndex(first_row)
        self._movie_switcher.blockSignals(False)

    def _disable_movie_row(self, row: int) -> None:
        """Make switcher row ``row`` unselectable (its movie cannot be shown)."""
        item = self._movie_switcher.model().item(row)
        if item is not None:
            item.setEnabled(False)

    def _on_movie_switched(self, row: int) -> None:
        """Show the movie of switcher row ``row`` in the movie panel (#356)."""
        panel = self._movie_panel
        index = self._movie_rows.get(row)
        if panel is None or index is None:
            return
        try:
            panel.set_active_movie(index)
        except Exception as exc:  # noqa: BLE001 - keep the GUI alive, report the cause
            self._status(f"Movie switch failed: {exc}")
            return
        self._status(f"Movie {self._project_movies[row].name}")

    # --- a movie that is missing or not on this disk (#638, PRD §5.4) -----------

    def _with_movie_locations(self, project_path: Path, movies: Any) -> Any:
        """Return ``movies`` with each one that cannot be shown moved to where it was found here.

        A movie is looked up by its recorded checksum: in this session's locations, then in
        the preferences file. Reads the project only; a lookup that fails keeps the movie as
        it is, so a bad lookup never fails an open.
        """
        from tether.services.movie_locations import (
            MovieLocation,
            apply_locations,
            expected_movie_sha256,
            guard_placeholders,
            locatable,
        )

        # A .vsi movie whose frame files are not on this disk is not opened (PRD §5.4). It is
        # marked before the lookup, so a verified local copy can take its place, and the
        # result is guarded again, since a remembered copy can be a placeholder too.
        movies = guard_placeholders(movies)
        found: dict[str, Any] = {}
        for movie in movies.movies:
            if not locatable(movie):
                continue
            try:
                sha256 = expected_movie_sha256(project_path, movie.movie_id)
            except Exception:  # noqa: BLE001 - an unreadable checksum finds nothing
                continue
            if not sha256:
                continue
            where = self._movie_locations.get(sha256)
            if where is None and self._preferences_file is not None:
                stored = preferences.movie_location(sha256, self._preferences_file)
                if stored is not None:
                    where = MovieLocation(*stored)
            if where is not None:
                found[movie.movie_id] = where
        return guard_placeholders(apply_locations(movies, found))

    def unavailable_movies(self) -> list[Any]:
        """The open project's movies whose file cannot be read here, in switcher order (#638).

        A movie held back for another reason (a movie pair, #1405) is not listed: locating
        its file would not make it showable.
        """
        from tether.services.movie_locations import locatable

        return [movie for movie in self._project_movies if locatable(movie)]

    def _movie_by_id(self, feature: str, movie_id: str) -> Any | None:
        for movie in self._project_movies:
            if movie.movie_id == movie_id:
                return movie
        self._unavailable(feature, f"the project has no movie {movie_id!r}")
        return None

    def locate_movie(self, movie_id: str, path: str | os.PathLike[str]) -> bool:
        """Show the project movie ``movie_id`` from the file at ``path``, if it is that movie.

        **Locate movie…** (#638). The file's checksum is read on a background worker and
        must be the one the project records
        ([`check_relocated`][tether.services.movie_locations.check_relocated]); another file
        is refused and nothing changes. The location is remembered for this
        computer, and the project file is not written. Returns whether the check started.
        """
        movie = self._movie_by_id("Locate movie", movie_id)
        if movie is None:
            return False
        from tether.gui.movie_locate import locate_one

        assert self._project_path is not None  # a movie implies an open project
        return self._start_movie_job(
            "Locate movie",
            f"Checking {Path(path).name}",
            locate_one,
            self._project_path,
            movie_id,
            Path(path),
        )

    def locate_movie_folder(self, folder: str | os.PathLike[str]) -> bool:
        """Find every movie that cannot be shown under ``folder``, in one step (#638).

        **Locate movie folder…**. Each movie is looked for by its file name and
        accepted only by its checksum; a cloud placeholder is never read. Returns whether
        the search started.
        """
        feature = "Locate movie folder"
        missing = self.unavailable_movies()
        if not missing:
            self._unavailable(feature, "every movie of this project is shown")
            return False
        from tether.gui.movie_locate import locate_folder

        assert self._project_path is not None
        return self._start_movie_job(
            feature,
            f"Looking for movies in {Path(folder).name}",
            locate_folder,
            self._project_path,
            missing,
            Path(folder),
        )

    def download_movie(self, movie_id: str) -> bool:
        """Download the movie ``movie_id``, which is not on this disk, then show it (#638).

        The explicit action of PRD §5.4: nothing else reads a cloud placeholder. The menu
        entry states the file size and asks first. Returns whether the download started.
        """
        feature = "Download and open movie"
        movie = self._movie_by_id(feature, movie_id)
        if movie is None:
            return False
        if movie.unavailable != "file not on this disk" or movie.path is None:
            self._unavailable(feature, f"{movie.name} is not a file waiting to be downloaded")
            return False
        from tether.gui.movie_locate import download

        return self._start_movie_job(feature, f"Downloading {movie.name}", download, movie.path)

    def _start_movie_job(self, feature: str, label: str, fn: Any, *args: Any) -> bool:
        if self._project_path is None:
            self._unavailable(feature, "no project is open")
            return False
        if self._movie_tasks.busy:
            self._unavailable(feature, "another movie is being located")
            return False
        self._status(f"{label}…")
        started = self._movie_tasks.start(
            fn,
            *args,
            context=(feature, self._project_path),
            label=label,
            on_finished=self._finish_movie_job,
            cancellable=True,
            reports_progress=True,
        )
        return started is not None

    def _stop_movie_job(self) -> None:
        """Stop a running locate or download: its reads end and its outcome is not applied."""
        self._movie_tasks.cancel()
        self._movie_tasks.abandon()

    def _finish_movie_job(self, outcome: Any) -> None:
        """Remember what a locate found, show the movies again, and say what happened."""
        from tether.gui.movie_locate import LocateResult
        from tether.gui.tasks import TaskState
        from tether.services.movies import read_project_movies

        feature, project_path = outcome.context
        if project_path != self._project_path:
            return  # another project is open now
        if outcome.state is TaskState.CANCELLED:
            self._status(f"{feature} cancelled")
            return
        if outcome.state is not TaskState.DONE:
            self._status(f"{feature} refused: {outcome.error}")
            return
        result = outcome.value
        found = result.found if isinstance(result, LocateResult) else {}
        for sha256, where in found.values():
            self._movie_locations[sha256] = where
            self._remember(
                lambda s=sha256, w=where: preferences.record_movie_location(
                    s, w.path, w.file_size, w.mtime, self._preferences_file
                )
            )
        try:
            movies = self._with_movie_locations(project_path, read_project_movies(project_path))
        except Exception as exc:  # noqa: BLE001 - keep the GUI alive, report the cause
            self._status(f"{feature}: the project's movies cannot be read again: {exc}")
            return
        row = self._molecule_list.currentRow()
        self._reset_movie_panel()
        note = self._wire_movies(movies)
        self._wire_roundtrip()
        if 0 <= row < len(self._traces):
            self._focus_movie(row)
            self._retarget_frame_link(row, lead="trace")
        if isinstance(result, LocateResult):
            parts = [f"{len(found)} movie(s) found"] + list(result.problems)
        else:
            parts = ["downloaded"]
        if note:
            parts.append(note)
        self._status(f"{feature}: " + "; ".join(parts))

    def _pick_unavailable_movie(self, feature: str, movies: list[Any]) -> Any | None:
        """Ask which of ``movies`` to act on; the one movie when there is only one."""
        from pyqtgraph.Qt import QtWidgets

        if not movies:
            self._unavailable(feature, "every movie of this project is shown")
            return None
        if len(movies) == 1:
            return movies[0]
        # The movie id keeps two movies of one file name and one reason apart.
        labels = [f"{movie.name} ({movie.unavailable}) [{movie.movie_id}]" for movie in movies]
        choice, ok = QtWidgets.QInputDialog.getItem(
            self._window, feature, "Movie:", labels, 0, False
        )
        return movies[labels.index(choice)] if ok and choice in labels else None

    def _confirm_download(self, feature: str, path: Path) -> bool:
        """Ask before a file that is not on this disk is read, stating its size (PRD §5.4)."""
        from pyqtgraph.Qt import QtWidgets

        from tether.gui.movie_locate import format_size
        from tether.services.movie_locations import download_size

        # A .vsi movie's frame files are read (and downloaded) with it, so they count too.
        size = format_size(download_size(path))
        buttons = QtWidgets.QMessageBox.StandardButton
        answer = QtWidgets.QMessageBox.question(
            self._window,
            feature,
            f"{path.name} is not on this disk. Download it ({size}) and open it?",
            buttons.Yes | buttons.No,
            buttons.No,
        )
        return answer == buttons.Yes

    def _locate_movie_dialog(self) -> None:
        """Menu entry: choose a movie that cannot be shown, then the file that is it."""
        from pyqtgraph.Qt import QtWidgets

        from tether.services.movie_locations import is_placeholder
        from tether.services.movies import movie_file_suffixes

        feature = "Locate movie"
        movie = self._pick_unavailable_movie(feature, self.unavailable_movies())
        if movie is None:
            return
        patterns = " ".join(f"*{suffix}" for suffix in movie_file_suffixes())
        start = str(movie.path.parent) if movie.path is not None else ""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self._window, f"Locate {movie.name}", start, f"Movies ({patterns});;All files (*)"
        )
        if not path:
            self._status(f"{feature} cancelled")
            return
        if is_placeholder(path) and not self._confirm_download(feature, Path(path)):
            self._status(f"{feature} cancelled: nothing was downloaded")
            return
        self.locate_movie(movie.movie_id, path)

    def _locate_movie_folder_dialog(self) -> None:  # pragma: no cover - interactive dialog
        """Menu entry: choose a folder, then find every movie that cannot be shown under it."""
        from pyqtgraph.Qt import QtWidgets

        feature = "Locate movie folder"
        if not self.unavailable_movies():
            self._unavailable(feature, "every movie of this project is shown")
            return
        folder = QtWidgets.QFileDialog.getExistingDirectory(self._window, feature)
        if not folder:
            self._status(f"{feature} cancelled")
            return
        self.locate_movie_folder(folder)

    def _download_movie_dialog(self) -> None:
        """Menu entry: choose a movie that is not on this disk, confirm its size, download it."""
        feature = "Download and open movie"
        waiting = [m for m in self.unavailable_movies() if m.unavailable == "file not on this disk"]
        if not waiting:
            self._unavailable(feature, "no movie of this project is waiting to be downloaded")
            return
        movie = self._pick_unavailable_movie(feature, waiting)
        if movie is None:
            return
        if not self._confirm_download(feature, movie.path):
            self._status(f"{feature} cancelled: nothing was downloaded")
            return
        self.download_movie(movie.movie_id)

    # --- the trace↔movie round-trip (#357, PRD §7.3, §5.2) ----------------------

    @property
    def roundtrip(self) -> Any | None:
        """The [`RoundTripNavigator`][tether.gui.roundtrip.RoundTripNavigator], or ``None``.

        Built over the [`movie_panel`][] and the molecule list whenever both exist, so
        ``None`` exactly when no movie panel is shown (or building it failed; ``Enter``
        then says why). Selecting a molecule in the list, or pressing ``Enter``, shows
        the molecule's own movie centred on its donor spot; clicking a donor or acceptor
        spot in the panel selects that molecule's list row.
        """
        return self._roundtrip

    def _wire_roundtrip(self) -> None:
        """Build the round-trip navigator over the shown movies and the molecule list.

        Each listed molecule whose ``molecule_key`` has a spot in a movie the panel shows
        becomes a [`MoleculeSite`][tether.gui.roundtrip.MoleculeSite]
        ([`sites_for_molecules`][tether.gui.roundtrip.sites_for_molecules]). The panel's
        movies are passed in its registration order, so a molecule's ``movie_id`` maps to
        the panel index to switch to. A click counts only inside the integration aperture
        drawn round a spot (the stored radius), so a click on the background selects
        nothing. A failure leaves no navigator and keeps the reason for ``Enter``.
        """
        self._roundtrip = None
        self._roundtrip_rows = []
        self._roundtrip_site_of_row = {}
        self._roundtrip_error = None
        panel = self._movie_panel
        if panel is None:
            return
        from tether.gui.roundtrip import RoundTripIndex, RoundTripNavigator, sites_for_molecules

        disconnect = getattr(panel, "disconnect_spot_clicks", None)
        if disconnect is not None:
            disconnect()  # a re-listed project must not fire the old navigator too
        self._unwire_frame_link()
        shown = [
            self._project_movies[row]
            for row, _ in sorted(self._movie_rows.items(), key=lambda item: item[1])
        ]
        try:
            sites, rows = sites_for_molecules([t.molecule_key for t in self._traces], shown)
            navigator = RoundTripNavigator(
                RoundTripIndex(sites),
                panel,
                [movie.movie_id for movie in shown],
                on_select=self._on_movie_spot_clicked,
                on_focus=self._on_movie_focused,
                max_click_distance=self._movie_aperture_radius,
            )
            navigator.connect()
        except Exception as exc:  # noqa: BLE001 - no round-trip is not a failed open
            self._roundtrip_error = str(exc) or type(exc).__name__
            self._status(f"Movie round-trip unavailable: {self._roundtrip_error}")
            return
        self._roundtrip = navigator
        self._roundtrip_rows = rows
        self._roundtrip_site_of_row = {row: site for site, row in enumerate(rows)}
        self._wire_frame_link(panel)

    def _focus_movie(self, row: int) -> bool:
        """Show list row ``row``'s molecule in the movie panel; ``False`` if it has no spot."""
        navigator = self._roundtrip
        site = self._roundtrip_site_of_row.get(row)
        if navigator is None or site is None:
            return False
        try:
            navigator.focus_molecule(site)
        except Exception as exc:  # noqa: BLE001 - keep the GUI alive, report the cause
            self._status(f"Movie jump failed: {exc}")
            return False
        return True

    def _jump_to_movie(self) -> None:
        """``Enter``: show the selected molecule's movie, centred on its donor spot (#357)."""
        feature = "Jump to movie spot"
        row = self._molecule_list.currentRow()
        if not 0 <= row < len(self._traces):
            self._unavailable(feature, "no molecule is selected")
            return
        label = self._row_name(row, self._traces[row])
        if self._roundtrip is None:
            self._unavailable(feature, self._roundtrip_error or "no movie is shown")
            return
        if row not in self._roundtrip_site_of_row:
            self._unavailable(feature, f"{label} has no spot in a movie shown here")
            return
        if not self._focus_movie(row):
            return
        if self._movie_dock_widget is not None:
            # A dock the user closed opens again: a jump to a hidden panel shows nothing.
            self._movie_dock_widget.show()
            self._movie_dock_widget.raise_()
        movie_id = self._roundtrip.index.site(self._roundtrip_site_of_row[row]).movie_id
        names = {m.movie_id: m.name for m in self._project_movies}
        self._status(f"Jumped to {label} in movie {names.get(movie_id, movie_id)}")

    def _on_movie_focused(self, _site: int) -> None:
        """After a jump, make the switcher name the movie the panel now shows."""
        panel = self._movie_panel
        if panel is None:
            return
        rows = [row for row, index in self._movie_rows.items() if index == panel.active_index]
        if rows and self._movie_switcher.currentIndex() != rows[0]:
            self._movie_switcher.blockSignals(True)
            self._movie_switcher.setCurrentIndex(rows[0])
            self._movie_switcher.blockSignals(False)

    def _on_movie_spot_clicked(self, site: int) -> None:
        """Movie → trace: select the list row of the molecule whose spot was clicked.

        A spot of a molecule the filter hides shows every molecule first (#630), so the
        selected molecule is always one the list shows and no key acts on a hidden row.
        """
        self._selecting_from_movie = True
        try:
            self._select_shown(self._roundtrip_rows[site])
        finally:
            self._selecting_from_movie = False

    # --- synchronized scrubbing (#366, PRD §7.3) -------------------------------

    @property
    def frame_link(self) -> Any | None:
        """The [`FrameLink`][tether.gui.roundtrip.FrameLink] of the trace dock and the movie panel.

        Built with the [`roundtrip`][], so ``None`` exactly when that is ``None``. While it
        is on and the panel shows the selected molecule's own movie, dragging the trace
        dock's frame cursor moves the movie to that frame, and moving the movie's time
        slider moves the cursor.
        """
        return self._frame_link

    @property
    def frame_link_enabled(self) -> bool:
        """Whether synchronized scrubbing is on in Curate and correct (on by default)."""
        return self._frame_link_enabled

    def set_frame_link_enabled(self, enabled: bool) -> None:
        """Turn synchronized scrubbing on or off (Curation → Link trace and movie frames).

        The state belongs to Curate and correct and to this window: it is kept across
        projects opened here, and it is not stored as a preference.
        """
        self._frame_link_enabled = bool(enabled)
        action = getattr(self, "_act_link_frames", None)
        if action is not None and action.isChecked() != self._frame_link_enabled:
            action.blockSignals(True)
            action.setChecked(self._frame_link_enabled)
            action.blockSignals(False)
        if self._frame_link is not None:
            self._frame_link.set_enabled(self._frame_link_enabled)
        self._status(
            "Trace and movie frames linked"
            if self._frame_link_enabled
            else "Trace and movie frames not linked"
        )

    def _wire_frame_link(self, panel: Any) -> None:
        """Build the frame link over ``panel`` and the trace dock (a failure is reported)."""
        from tether.gui.roundtrip import FrameLink

        try:
            link = FrameLink(panel, self._trace_dock, enabled=self._frame_link_enabled)
            link.connect()
        except Exception as exc:  # noqa: BLE001 - no frame link is not a failed open
            self._unwire_frame_link()
            self._status(f"Frame link unavailable: {exc}")
            return
        self._frame_link = link

    def _unwire_frame_link(self) -> None:
        """Drop the frame link and the listeners it added to the panel and the dock."""
        self._frame_link = None
        panel = self._movie_panel
        disconnect = getattr(panel, "disconnect_frame_changed", None)
        if disconnect is not None:
            disconnect()
        self._trace_dock.disconnect_frame_cursor()

    def _retarget_frame_link(self, row: int, *, lead: str) -> None:
        """Link the cursor of list row ``row``'s trace to that molecule's own movie."""
        link, navigator = self._frame_link, self._roundtrip
        if link is None:
            return
        site = self._roundtrip_site_of_row.get(row)
        if navigator is None or site is None or not 0 <= row < len(self._traces):
            link.clear_target()
            return
        trace = self._traces[row]
        try:
            movie_index = navigator.movie_ids.index(navigator.index.site(site).movie_id)
            link.set_target(
                movie_index, frame_offset=trace.frame_offset, n_frames=trace.n_frames, lead=lead
            )
        except Exception as exc:  # noqa: BLE001 - keep the GUI alive, report the cause
            link.clear_target()
            self._status(f"Frame link failed: {exc}")

    def _select_shown(self, row: int) -> None:
        """Select list row ``row``; when the filter hides it, show all molecules first (#630).

        For a selection made outside the list (a movie spot, a reconcile row), so the
        selected molecule is always one the list shows and no key acts on a hidden row.
        """
        from tether.gui.molecule_list import FILTER_ALL

        revealed = not self._molecule_view.shown(row)
        if revealed:
            self._molecule_view.set_filter(FILTER_ALL)
        self._molecule_list.setCurrentRow(row)
        if revealed:
            self._status(f"{self.status_message}; the filter now shows all molecules")

    def _reset_movie_panel(self) -> None:
        """Close the movie panel and its readers, remove its dock, and empty the switcher."""
        self._unwire_frame_link()
        panel, dock_widget = self._movie_panel, self._movie_dock_widget
        self._movie_panel = None
        self._movie_dock_widget = None
        self._roundtrip = None
        self._roundtrip_rows = []
        self._roundtrip_site_of_row = {}
        self._roundtrip_error = None
        self._movie_aperture_radius = None
        if dock_widget is not None:
            self._event_filter.remove_scope_widget(dock_widget)
            self._detach_section_dock(dock_widget)
        if panel is not None:
            # Take napari's window out of the dock first, so the viewer closes its own
            # top-level window, as it does outside Tether.
            panel.qt_window.setParent(None)
            panel.close()
        if dock_widget is not None:
            dock_widget.deleteLater()
        for reader in self._movie_readers:
            with suppress(Exception):
                reader.close()
        self._movie_readers.clear()
        self._movie_rows.clear()
        self._project_movies = []
        self._movie_switcher.blockSignals(True)
        self._movie_switcher.clear()
        self._movie_switcher.blockSignals(False)
        self._movie_switcher.setEnabled(False)

    # --- what this computer remembers between launches (#628, ADR-0122) -------

    @property
    def preferences_file(self) -> Path | None:
        """The per-user preferences file, or ``None`` when this shell remembers nothing."""
        return self._preferences_file

    def _remember(self, write: Callable[[], object]) -> bool:
        """Run one preferences write; a failure is a status message, never an exception.

        Remembering is a convenience: a full disk or a read-only home folder must not stop
        an open, a close or a curation decision.
        """
        if self._preferences_file is None:
            return False
        try:
            write()
        except (OSError, ValueError) as exc:
            from tether.gui.messages import Level

            # An error stays in the status bar until dismissed, so a success message of the
            # same action does not hide it (#628) and nothing needs to carry it along.
            self._messages.post(f"Could not save preferences: {exc}", Level.ERROR)
            return False
        return True

    @property
    def curator_name(self) -> str:
        """The name written into new labels: the preference, else the operating-system user."""
        return self._curator_name if self._curator_name is not None else preferences.login_name()

    def _labeler_kwargs(self) -> dict[str, str]:
        """``labeler=`` for a project write when a curator name is set; else the project's own."""
        return {} if self._curator_name is None else {"labeler": self._curator_name}

    @property
    def curator_label(self) -> QtWidgets.QLabel:
        """The status-bar label that names the curator new labels are written for."""
        return self._curator_label

    @property
    def curator_name_action(self) -> Any:
        """The **File → Curator name…** action."""
        return self._act_curator_name

    def set_curator_name(self, name: str | None) -> bool:
        """Write new labels as ``name``; ``None`` or blank returns to the operating-system user.

        Labels already written keep the name they were written with. A name
        [`validate_curator_name`][tether.gui.preferences.validate_curator_name] refuses
        changes nothing and is reported on the status bar; the return value says whether
        the name changed.
        """
        value = None if name is None or not name.strip() else name
        try:
            checked = None if value is None else preferences.validate_curator_name(value)
        except ValueError as exc:
            self._status(f"Curator name not changed: {exc}")
            return False
        self._remember(lambda: preferences.set_curator_name(checked, self._preferences_file))
        self._curator_name = checked
        self._sync_curator_label()
        self._status(f"New labels are written as {self.curator_name}")
        return True

    def _sync_curator_label(self) -> None:
        self._curator_label.setText(f"Curator: {self.curator_name}")
        self._curator_label.setToolTip(
            "New labels are written with this name. Change it with File → Curator name…"
        )

    def _curator_name_dialog(self) -> None:  # pragma: no cover - interactive dialog
        from pyqtgraph.Qt import QtWidgets

        text, ok = QtWidgets.QInputDialog.getText(
            self._window,
            "Curator name",
            "Name written into new labels (leave empty for your login name):",
            text=self.curator_name,
        )
        if ok:
            self.set_curator_name(text)

    @property
    def reopen_last_action(self) -> Any:
        """The checkable **File → Reopen last project at start** action (#1159)."""
        return self._act_reopen_last

    def set_reopen_last_project(self, on: bool) -> bool:
        """Turn on or off the reopening of the last project at launch; return if it was saved.

        When the preferences file cannot be written, the action shows the stored answer
        again, so the menu never shows a setting the next launch will not use.
        """
        on = bool(on)
        # The menu click has checked the action already; a call from code checks it here,
        # without the toggled signal calling this method a second time.
        self._check_reopen_last(on)
        saved = self._remember(
            lambda: preferences.set_reopen_last_project(on, self._preferences_file)
        )
        if not saved:
            self._check_reopen_last(
                self._preferences_file is not None
                and preferences.reopen_last_project(self._preferences_file)
            )
        else:
            self._status(
                "The last project opens again at the next start"
                if on
                else "The next start shows the start page"
            )
        return saved

    def _check_reopen_last(self, on: bool) -> None:
        """Check or clear **Reopen last project at start** without its toggled signal."""
        blocked = self._act_reopen_last.blockSignals(True)
        try:
            self._act_reopen_last.setChecked(on)
        finally:
            self._act_reopen_last.blockSignals(blocked)

    def reopen_last_project(self) -> str | None:
        """At launch, open the newest recent project in the section of its next step (#1159).

        It does nothing when this shell remembers nothing, when **File → Reopen last project
        at start** is off, or when **File → Open recent** is empty. The section comes from
        the readiness record of the project ([`refresh_readiness`][]), the next step that
        ``tether status`` prints, and never from the preferences file. A step with no section
        the shell shows keeps the section the open chose.

        When the file is missing, the open fails, or the readiness cannot be read, the start
        page stays (or shows again) and the status bar says why. Return the id of the section
        that shows after a reopen, or ``None`` when nothing was opened.
        """
        if self._preferences_file is None or not preferences.reopen_last_project(
            self._preferences_file
        ):
            return None
        recent = preferences.recent_projects(self._preferences_file)
        if not recent:
            return None
        from tether.gui.messages import Level

        path = recent[0]
        if not path.is_file():
            self._status(f"Last project not reopened: {path} was not found", Level.WARNING)
            return None
        if self.load_project(path) is None:
            # load_project said why on the status bar, and the start page still shows.
            return None
        if self._readiness is None:
            self.close_project()
            self._status(
                f"Last project not reopened: the next step of {path.name} could not be read",
                Level.WARNING,
            )
            return None
        step = guided_step(self._readiness, self._sections)
        if step is not None and step.section is not None:
            self.set_section(step.section)
        return self._section

    @property
    def recent_menu(self) -> QtWidgets.QMenu:
        """The **File → Open recent** submenu."""
        return self._recent_menu

    def recent_projects(self) -> list[Path]:
        """The projects **File → Open recent** lists, newest first (empty if nothing is kept)."""
        if self._preferences_file is None:
            return []
        return preferences.recent_projects(self._preferences_file)

    def _sync_recent_menu(self) -> None:
        """Rebuild **File → Open recent** from the preferences file."""
        self._recent_menu.clear()
        recent = self.recent_projects()
        for path in recent:
            exists = path.is_file()
            label = path.name if exists else f"{path} (missing)"
            action = self._recent_menu.addAction(label.replace("&", "&&"))
            action.setToolTip(str(path))
            action.setStatusTip(str(path))
            action.setData(str(path))
            action.setEnabled(exists)
            action.triggered.connect(lambda _checked=False, p=path: self.load_project(p))
        if not recent:
            empty = self._recent_menu.addAction("No recent projects")
            empty.setEnabled(False)
        self._recent_menu.setEnabled(self._preferences_file is not None)

    def _remember_position(self) -> None:
        """Store the selected molecule as the resume position of the open project."""
        if self._project_path is None:
            return
        row = self._molecule_list.currentRow()
        trace = self._traces[row] if 0 <= row < len(self._traces) else None
        key = None if trace is None else trace.molecule_key
        path = self._project_path
        self._remember(lambda: preferences.record_resume(path, key, self._preferences_file))

    def _resume_position(self) -> None:
        """Select the molecule this project showed when it was last closed here, if any."""
        if self._preferences_file is None or self._project_path is None:
            return
        key = preferences.resume_molecule(self._project_path, self._preferences_file)
        if key is None:
            return
        for row, trace in enumerate(self._traces):
            if trace.molecule_key == key:
                self._molecule_list.setCurrentRow(row)
                return

    def _section_hidden_docks(self) -> list[str]:
        """The docks hidden only because their section is not current (by object name)."""
        names = {dock.objectName() for dock, shown in self._dock_restore.items() if shown}
        # A dock not built in this session (Overlap, Movie) keeps the stored
        # answer, since Qt keeps its stored place and visibility for the next launch too.
        built = {dock.objectName() for docks in self._section_docks.values() for dock in docks}
        return sorted(names | (self._stored_section_hidden - built))

    def _save_layout(self) -> None:
        """Store the window geometry and dock positions for the next launch."""
        geometry = bytes(self._window.saveGeometry().data())
        state = bytes(self._window.saveState().data())
        hidden = self._section_hidden_docks()
        self._remember(
            lambda: preferences.record_layout(
                geometry, state, self._preferences_file, section_hidden=hidden
            )
        )

    def _restore_layout(self) -> None:
        """Restore the stored layout, then let the section rules decide what shows."""
        if self._preferences_file is None:
            return
        stored = preferences.stored_layout(self._preferences_file)
        if stored is None:
            return
        from pyqtgraph.Qt import QtCore

        if not self._window.restoreGeometry(QtCore.QByteArray(stored.geometry)):
            return
        if not self._window.restoreState(QtCore.QByteArray(stored.state)):
            return
        self._stored_section_hidden = set(stored.section_hidden)
        self._enforce_sections(shown_by_section=self._stored_section_hidden)

    def _enforce_sections(self, *, shown_by_section: set[str]) -> None:
        """Apply the section rule after Qt restored a state that knows nothing of sections.

        A dock named in ``shown_by_section`` was hidden at save time only because its
        section was not current, so it counts as shown. The current section's docks then
        show or stay closed as restored, and every other section's docks are hidden.
        """
        self._dock_restore.clear()
        for section_id, docks in self._section_docks.items():
            for dock in docks:
                visible = dock.objectName() in shown_by_section or not dock.isHidden()
                if section_id == self._section:
                    dock.setVisible(visible)
                    dock.toggleViewAction().setVisible(True)
                else:
                    self._dock_restore[dock] = visible
                    dock.hide()
                    dock.toggleViewAction().setVisible(False)

    def reset_layout(self) -> None:
        """Return the window to its default size and dock positions (**View → Reset layout**).

        Every dock shows again in its default place, the section rule still applies, and the
        stored layout is forgotten so the next launch uses the default too.
        """
        self._window.restoreState(self._default_state)
        self._stored_section_hidden = set()
        # The default state does not know a dock built after it was captured (Overlap,
        # Movie), so restoring it leaves such a dock where the user put it.
        for dock, area in self._dock_default_area.items():
            if dock not in self._default_docks:
                dock.setFloating(False)
                self._window.addDockWidget(area, dock)
        self._window.resize(self._default_size)
        every_dock = {dock.objectName() for docks in self._section_docks.values() for dock in docks}
        self._enforce_sections(shown_by_section=every_dock)
        self._remember(lambda: preferences.clear_layout(self._preferences_file))
        self._status("Layout reset to the default")

    @property
    def reset_layout_action(self) -> Any:
        """The **View → Reset layout** action."""
        return self._act_reset_layout

    # --- project (open a produced / extracted .tether live, §7.8) ------------

    @property
    def file_menu(self) -> QtWidgets.QMenu:
        """The ``&File`` menu (open or close a ``.tether`` project, and quit)."""
        return self._file_menu

    def _set_read_only_reason(self, reason: str | None) -> None:
        """Keep browse-only state visible and disable the exposed source-write controls.

        It is called when a project opens, when it closes and when the session lock is
        lost, so it also empties the undo and redo lists (#632): they belong to one
        open, writable project.
        """
        self._curation_read_only_reason = reason
        self._clear_undo()
        if reason is None:
            self._read_only_banner.clear()
            self._read_only_banner.hide()
            self._set_lock_recovery(None)
        else:
            self._read_only_banner.setText(f"Read-only project: {reason}")
            self._read_only_banner.show()
        self._sync_project_write_controls()

    def _set_conflict_copy_warning(self, warning: str | None) -> None:
        """Show ``warning`` in the sync-conflict banner, or clear and hide it for ``None``."""
        if warning is None:
            self._conflict_copy_banner.clear()
            self._conflict_copy_banner.hide()
        else:
            self._conflict_copy_banner.setText(f"Warning: {warning}.")
            self._conflict_copy_banner.show()

    def _sync_project_write_controls(self) -> None:
        """Enable source-project writers only for an idle writable GUI session.

        The Deep-LASI import publishes whole projects, possibly over the loaded one, so
        it is frozen while idealization owns HDF5 as well (#346, ADR-0112). It needs no
        writable session: it writes its own files under each one's writer lock.
        """
        enabled = self._curation_read_only_reason is None and self._idealize_future is None
        self._unreject_button.setEnabled(enabled)
        self._act_import.setEnabled(enabled)
        self._act_validate_conditions.setEnabled(enabled)
        self._act_import_deeplasi.setEnabled(self._idealize_future is None)

    def _idealization_blocks_write(self, feature: str) -> bool:
        """Report and reject a source-project mutation while idealization owns HDF5."""
        if self._idealize_future is None:
            return False
        self._status(f"{feature} unavailable: Idealize in progress; wait for it to finish")
        return True

    def load_project(
        self, path: str | PathLike[str], *, intensity_quantity: str = "corrected"
    ) -> Project | None:
        """Open a produced / extracted ``.tether`` live in this shell (§7.8, FR-LEGACY).

        Re-wires the running shell's store-backed seams onto ``path`` — the whole-project
        fit (``I``), the Analyze section's plots (#374), the standalone-tMAVEN
        hand-off (``&Hand-off``), the per-molecule overlap view, and the ``&Conditions``
        validation target — then loads the store's molecules into the browser + trace
        dock ([`set_molecules`][]). This is the general store↔shell hookup (the
        M2-deferred capability, reusable beyond legacy): a wizard-produced project
        ([`import_deeplasi_bundle`][]) or any extracted ``.tether`` opens live without
        relaunching.

        The GUI acquires and retains the project's single-writer sidecar lock before
        enabling any write seam. A foreign/corrupt/uncreatable lock does not prevent
        browsing: the project opens read-only, write seams stay disabled, and the
        status banner records why. Replacing a project releases the prior session
        lock; [`close`][] releases the current one (after any in-flight idealizer
        write settles). Replacing a project also closes the summary of the last
        correction pass, so [`correction_summary`][] is ``None`` until the next pass.

        Every [`TraceView`][tether.gui.trace_dock.TraceView] carries its ``molecule_key`` so
        the idealize / overlap seams resolve the store row. An **analysis-only** import
        (coordinate-less, ``read_analysis_only_marker`` non-``None``) leaves the overlap
        seam unwired — the spot/overlap view is meaningless without coordinates/patches —
        and surfaces the one-time marker banner. All reads happen before any state is
        mutated, so a failed open (missing / incompatible / unreadable ``.tether``)
        leaves the prior project in place and only reports a status message — the shell
        never crashes on an open. Returns the opened [`Project`][tether.project.core.Project]
        (``None`` on failure).

        A successful open also looks for OneDrive/SharePoint sync-conflict siblings of
        the file ([`conflict_copy_warning`][tether.services.notices.conflict_copy_warning],
        PRD §5.4 detect-and-surface) and names any in the non-blocking
        [`conflict_copy_banner`][]; with none, the banner is hidden. The open never
        waits on, refuses over, or changes a conflict copy.

        The movie switcher lists the project's ``/movies`` rows, and the movies whose files
        can be opened are shown in the embedded napari movie panel, a dock of Curate and
        correct, with each movie's donor and acceptor spots at the stored aperture radius
        (#356; [`movie_panel`][]). An analysis-only import leaves the panel unbuilt and the
        switcher empty. Selecting a molecule then shows its own movie centred on its donor
        spot, and clicking a spot selects its molecule ([`roundtrip`][], #357). The curation
        category vocabulary is **not** re-wired here (the per-condition category list is
        edited through the ``&Conditions`` dialog).
        """
        from tether.project.analysis_import import read_analysis_only_marker
        from tether.project.core import Project
        from tether.project.lock import LockedError
        from tether.services.movies import ProjectMovies, read_project_movies
        from tether.services.notices import conflict_copy_warning

        if self._idealize_future is not None:
            self._status("Open project unavailable: wait for idealization to finish")
            return None
        if self._session_project is not None and self._curation_project is None:
            self._status("Open project unavailable: waiting to release the prior session lock")
            return None
        if self._rollback_session_project is not None:
            self._status("Open project unavailable: waiting to release a rolled-back session lock")
            return None
        if self._recovered_lock is not None:
            self._status("Open project unavailable: waiting to release a recovered project lock")
            return None

        # Do every fallible read BEFORE touching shell state, so a bad open is atomic
        # (the prior project stays wired) — mirroring the catch-and-report contract of
        # the hand-off / import menu methods.
        try:
            opened_project = Project.open(path)
            proj_path = opened_project.path
            reusing_session = (
                self._session_project is not None
                and self._curation_project is self._session_project
                and self._claimed_project_key is not None
                and _project_owner_key(proj_path) == self._claimed_project_key
            )
            if reusing_session:
                # Reopening this shell's existing file (including a hard-link alias)
                # must not replace its retained nonce. Rebuild read/seam state against
                # the lifecycle-bearing handle, then refresh that exact epoch below.
                project = self._session_project
                proj_path = project.path
            else:
                project = opened_project
            marker = read_analysis_only_marker(proj_path)
            analysis_only = marker is not None
            traces = traces_from_store(project, intensity_quantity=intensity_quantity)
            overlap = None if analysis_only else make_store_overlap(project)
            # The movie panel's data (#356): the /movies rows with their spots, and the
            # stored aperture radius the overlap view uses too. An analysis-only project
            # leaves the panel unwired (PRD §7.8), whatever /movies holds.
            movies = (
                ProjectMovies(movies=(), aperture_radius=0.0)
                if analysis_only
                else read_project_movies(proj_path)
            )
            idealizer_candidate = make_store_idealizer(project)
            # Export is read-only with respect to the source project, so retain this
            # seam even when the canonical store must open browse-only.
            handoff = make_store_handoff(project)
        except Exception as exc:  # noqa: BLE001 - keep the GUI alive, report the cause
            self._status(f"Open project failed: {exc}")
            return None
        movies = self._with_movie_locations(Path(proj_path), movies)

        recovery = None
        refusal = None
        if reusing_session:
            self._refresh_project_lock()
            if self._curation_project is None or self._session_project is None:
                return None
            claimed_key = self._claimed_project_key
            session_project = project
            writable = True
            read_only_reason = None
            idealizer = idealizer_candidate
        else:
            claimed_key = _claim_gui_project(proj_path, self._project_owner_token)
            session_project = None
            if claimed_key is None:
                writable = False
                read_only_reason = "already open writable in another Tether window"
                idealizer = None
            else:
                try:
                    project.acquire_lock()
                except (LockedError, OSError) as exc:
                    _release_gui_project(claimed_key, self._project_owner_token)
                    claimed_key = None
                    writable = False
                    refusal = f"write lock unavailable: {exc}"
                    if isinstance(exc, LockedError):
                        recovery = _read_lock_recovery(proj_path)
                    read_only_reason = _lock_reason(refusal, recovery)
                    idealizer = None
                else:
                    session_project = project
                    try:
                        _probe_project_writable(proj_path)
                    except Exception as exc:  # noqa: BLE001 - fall back to browse-only
                        # Deliberately wider than OSError: this runs after the lock is
                        # held and outside the fallible-reads try above, so anything the
                        # HDF5 layer raises (RuntimeError, ValueError) would otherwise
                        # escape load_project -- breaking the "never crash on open"
                        # contract AND leaking both the sidecar nonce and the
                        # process-local claim, with no handle recorded to release them.
                        try:
                            released = project.release_lock()
                        except OSError:
                            # Retain both handle and process-local claim; the release
                            # timer retries this exact nonce after state goes read-only.
                            pass
                        else:
                            if released:
                                session_project = None
                                _release_gui_project(claimed_key, self._project_owner_token)
                                claimed_key = None
                        writable = False
                        read_only_reason = f"project file is not writable: {exc}"
                        idealizer = None
                    else:
                        writable = True
                        read_only_reason = None
                        idealizer = idealizer_candidate

        prior_session_project = self._session_project
        prior_claimed_key = self._claimed_project_key
        if prior_session_project is not None and prior_session_project is not session_project:
            prior_release_error = None
            prior_epoch_lost = False
            try:
                prior_released = prior_session_project.release_lock()
            except OSError as exc:
                prior_release_error = exc
            else:
                if not prior_released:
                    if prior_session_project._held_lock is None:
                        prior_epoch_lost = True
                    else:
                        prior_release_error = OSError(
                            "prior session lock release was indeterminate"
                        )
            if prior_release_error is not None or prior_epoch_lost:
                if session_project is not None:
                    try:
                        new_released = session_project.release_lock()
                    except OSError:
                        new_released = False
                    if not new_released and session_project._held_lock is not None:
                        self._rollback_session_project = session_project
                        self._rollback_claimed_project_key = claimed_key
                        self._lock_release_timer.start()
                    elif claimed_key != prior_claimed_key:
                        _release_gui_project(claimed_key, self._project_owner_token)
                elif claimed_key != prior_claimed_key:
                    _release_gui_project(claimed_key, self._project_owner_token)
                if prior_epoch_lost:
                    self._lock_refresh_timer.stop()
                    self._reject_active_conditions_dialog()
                    self._curation_project = None
                    self._session_project = None
                    self._claimed_project_key = None
                    self._set_read_only_reason("session lock lost while opening another project")
                    self._idealizer = None
                    self._conditions = None
                    _release_gui_project(prior_claimed_key, self._project_owner_token)
                    self._status(
                        "Open project unavailable: prior session lock was lost; "
                        "current project is read-only"
                    )
                else:
                    self._status(
                        "Open project unavailable: prior session release failed: "
                        f"{prior_release_error}"
                    )
                return None

        self._curation_project = project if writable else None
        self._session_project = session_project
        self._claimed_project_key = claimed_key if session_project is not None else None
        self._set_read_only_reason(read_only_reason)
        self._lock_refusal = refusal
        self._set_lock_recovery(recovery)
        self._idealizer = idealizer
        self._handoff = handoff
        self._overlap_seam = overlap
        self._conditions = project if writable else None
        if writable:
            self._lock_release_timer.stop()
            self._lock_refresh_timer.start()
        else:
            self._lock_refresh_timer.stop()
            if session_project is not None:
                self._lock_release_timer.start()
        # Drop any docks built against a prior project so they rebuild fresh against the
        # new store (no stale overlap view leaking across a re-open).
        self._reset_store_docks()
        self._close_generation_page()
        self._close_correction_summary()
        # The project being replaced keeps the molecule it shows now (#628).
        self._remember_position()
        self._project_path = Path(proj_path)
        self._analysis_section.set_project(self._project_path)
        self._import_page.set_project(self._project_path, self._curation_project)
        self.refresh_readiness()
        # A project made from imported traces starts in Curate and correct (#897).
        entry = entry_section(self._readiness)
        if entry is not None:
            self.set_section(entry)
        self._intensity_quantity = intensity_quantity
        self._act_close_project.setEnabled(True)
        self._act_save_recipe.setEnabled(True)
        self._export_menu.setEnabled(True)
        movie_note = self._wire_movies(movies)
        # set_molecules selects row 0 → _on_list_row_changed refreshes the overlap dock
        # and sets a per-molecule status; overwrite it with the open outcome afterwards.
        # The rows' states are read once here; actions then update their own rows (#630).
        self.set_molecules(traces, self._read_molecule_states(proj_path))
        # Then the molecule selected when this project was last closed here, and the
        # project goes to the top of File -> Open recent (#628).
        self._resume_position()
        self._remember(lambda: preferences.add_recent_project(proj_path, self._preferences_file))
        if prior_claimed_key != self._claimed_project_key:
            _release_gui_project(prior_claimed_key, self._project_owner_token)

        name = Path(proj_path).name
        modes = [
            mode
            for enabled, mode in (
                (analysis_only, "analysis-only"),
                (not writable, "read-only"),
            )
            if enabled
        ]
        mode_suffix = f" ({', '.join(modes)})" if modes else ""
        detail = marker.banner if analysis_only else f"{len(traces)} molecule(s)"
        if movie_note is not None:
            detail = f"{detail}; {movie_note}"
        if read_only_reason is not None:
            detail = f"{detail}; {read_only_reason}"
        # Detect-and-surface (§5.4): look for OneDrive/SharePoint conflict siblings on
        # every successful open, writable or browse-only. Advisory only — the helper
        # swallows a directory-listing failure, so this can never fail the open.
        self._set_conflict_copy_warning(conflict_copy_warning(proj_path))
        self._status(f"Opened {name}{mode_suffix} — {detail}")
        return project

    # --- lock recovery from the read-only banner (#626) -----------------------

    def _set_lock_recovery(self, recovery: Any | None) -> None:
        """Show the banner action ``recovery`` allows and arm its timed re-check."""
        from tether.project.lock import RecoveryAction

        self._lock_recovery = recovery
        self._lock_recovery_timer.stop()
        action = None if recovery is None else recovery.action
        self._recover_button.setVisible(action is RecoveryAction.RECOVER)
        self._take_over_button.setVisible(action is RecoveryAction.TAKE_OVER)
        if recovery is None:
            return
        if recovery.available_at is not None:
            from datetime import UTC, datetime

            wait_s = (recovery.available_at - datetime.now(UTC)).total_seconds()
            wait_ms = int(max(0.0, wait_s) * 1000) + 1000
            self._lock_recovery_timer.start(min(wait_ms, _LOCK_RECOVERY_RECHECK_MAX_MS))
        elif recovery.holder_running:
            # A running holder has no due time, but it can stop (or crash) at any
            # moment; judge it again at the cap so Recover appears once it has (#1123).
            # recover_lock() probes the process again before it takes anything.
            self._lock_recovery_timer.start(_LOCK_RECOVERY_RECHECK_MAX_MS)

    def _recheck_lock_recovery(self) -> None:
        """Judge the refused lock again and refresh the banner and its actions."""
        if (
            self._closed
            or self._project_path is None
            or self._lock_recovery is None
            or self._curation_project is not None
        ):
            return
        recovery = _read_lock_recovery(self._project_path)
        if recovery is None:
            reason = "the lock was released; open the project again to write"
        else:
            # Describe the holder from this verdict, not the refusal at open time:
            # the lock can have been refreshed or taken by another writer since.
            refusal = _lock_refusal_text(self._project_path, recovery)
            self._lock_refusal = refusal
            reason = _lock_reason(refusal, recovery)
        self._set_read_only_reason(reason)
        self._set_lock_recovery(recovery)

    def recover_project_lock(self) -> Project | None:
        """Take a lock left by a stopped session of this user here, and reopen writable.

        The banner's one-click **Recover** (#626). It is available only when
        [`lock_recovery`][] reports
        [`RecoveryAction.RECOVER`][tether.project.lock.RecoveryAction]: the lock names
        this computer and this user, and its process no longer runs. The reopen uses
        the ``intensity_quantity`` of the open that was refused, so the traces under
        curation keep their layer. Returns the reopened writable project, or ``None``
        with a status message.
        """
        from tether.project.lock import RecoveryAction

        return self._take_project_lock(RecoveryAction.RECOVER, confirmation=None)

    def take_over_project_lock(self, confirmation: str | None = None) -> Project | None:
        """Take a stale foreign lock after a typed confirmation, and reopen writable.

        The banner's **Take over…** (#626). It is available only when
        [`lock_recovery`][] reports
        [`RecoveryAction.TAKE_OVER`][tether.project.lock.RecoveryAction]: another
        computer, another user or a corrupt lock, idle past the staleness timeout
        (PRD §11.2). ``confirmation`` is the typed text; ``None`` asks for it in a
        dialog that warns that the holder's unsaved work can be lost. The text must
        equal the project file name. The reopen uses the ``intensity_quantity`` of
        the open that was refused, as [`recover_project_lock`][] does. Returns the
        reopened writable project, or ``None`` with a status message.
        """
        from tether.project.lock import RecoveryAction

        return self._take_project_lock(RecoveryAction.TAKE_OVER, confirmation=confirmation)

    def _take_project_lock(self, action: Any, *, confirmation: str | None) -> Project | None:
        """Shared body of [`recover_project_lock`][] and [`take_over_project_lock`][]."""
        from tether.project import lock
        from tether.project.lock import LockedError, RecoveryAction

        label = "Recover" if action is RecoveryAction.RECOVER else "Take over"
        if self._idealize_future is not None:
            self._status(f"{label} unavailable: wait for idealization to finish")
            return None
        recovery = self._lock_recovery
        path = self._project_path
        if path is None or recovery is None or recovery.action is not action:
            self._status(f"{label} unavailable: the project lock does not allow it")
            return None
        if action is RecoveryAction.TAKE_OVER:
            if confirmation is None:
                confirmation = self._ask_take_over_confirmation(path, recovery)
                if confirmation is None:
                    self._status("Take over cancelled")
                    return None
            if confirmation.strip() != path.name:
                self._status(f"Take over cancelled: the typed text does not match {path.name}")
                return None
        try:
            taken, ousted = lock.recover_lock(path, recovery)
        except LockedError as exc:
            self._status(f"{label} refused: the lock changed ({exc})")
            self._recheck_lock_recovery()
            return None
        except OSError as exc:
            self._status(f"{label} failed: {exc}")
            return None
        # Re-run the open path so every write seam binds to the new session, over the
        # same trace layer the refused open showed (#1097).
        opened = self.load_project(path, intensity_quantity=self._intensity_quantity)
        if opened is None or self._curation_project is None:
            # The reopen is not writable; give back the lock just taken. The release
            # is nonce-checked, so a session lock the reopen took and still holds is
            # not touched. A failed release keeps the record for the retry timer.
            self._recovered_lock = (path, taken)
            if not self._release_recovered_lock_once():
                self._lock_release_timer.start()
            self._status(f"{label} failed: {self._curation_read_only_reason or 'reopen failed'}")
            return None
        holder = (
            "an unknown holder (corrupt lock)"
            if ousted is None
            else f"{ousted.user}@{ousted.host} (pid {ousted.pid})"
        )
        warning = "" if action is RecoveryAction.RECOVER else "; their unsaved work is not merged"
        # The durable record goes in under the new lock, through the writable reopen,
        # so a refused, failed or read-only takeover never writes one (#1099, ADR-0121).
        try:
            self._curation_project.record_lock_takeover(action, taken, ousted)
        except Exception as exc:  # noqa: BLE001 - the takeover stands; say the record failed
            logging.getLogger("tether.project.lock").warning(
                "lock %s on %s: the takeover record was not written: %s", action.value, path, exc
            )
            warning += f"; the takeover record was not written ({exc})"
        self._status(f"{label}: took the lock from {holder}; {path.name} is writable{warning}")
        return opened

    def _ask_take_over_confirmation(self, path: Path, recovery: Any) -> str | None:
        """Ask the user to type the project file name; ``None`` when cancelled."""
        from pyqtgraph.Qt import QtWidgets

        holder = (
            "The lock file is corrupt, so its holder is unknown."
            if recovery.owner is None
            else (
                f"{recovery.owner.user}@{recovery.owner.host} holds this lock "
                f"(pid {recovery.owner.pid}, last refreshed {recovery.owner.timestamp})."
            )
        )
        text, ok = QtWidgets.QInputDialog.getText(
            self._window,
            "Take over the project lock",
            f"{holder}\n\nIf you take over, this window becomes the only writer. Work "
            "that the holder did not save is lost. It is not merged.\n\n"
            f"Type the project file name, {path.name}, to confirm.",
        )
        return str(text) if ok else None

    # --- session-lock state (read-only) ---------------------------------------
    #
    # Public, read-only views of the single-writer session state that
    # `load_project`, the lock-refresh timer and the release-retry timer
    # maintain. They expose state for inspection only: none has a setter, and none
    # changes the lock or the session.

    @property
    def curation_project(self) -> Project | None:
        """The writable project this shell curates, or ``None`` when no write is allowed.

        ``None`` before any project opens, while the loaded project is browse-only
        (see [`read_only_reason`][]), after the session lock is lost, and after
        [`close`][]. Only this project receives curation, idealization, conditions
        and return-leg writes.
        """
        return self._curation_project

    @property
    def session_project(self) -> Project | None:
        """The project whose session lock this shell holds or has yet to release.

        Usually the same object as [`curation_project`][]. It stays set after the
        project becomes browse-only or the shell closes while a release of its lock
        is still pending ([`lock_release_pending`][]), and is ``None`` once that
        release succeeds or no lock was taken.
        """
        return self._session_project

    @property
    def holds_project_claim(self) -> bool:
        """``True`` while this shell owns the in-process claim on its session project.

        The claim stops a second Tether window in the same process from opening the
        same file (or a hard-link alias of it) writable. It is dropped together
        with the session lock.
        """
        return self._claimed_project_key is not None

    @property
    def rollback_session_project(self) -> Project | None:
        """A project whose lock a failed project switch left held, or ``None``.

        When [`load_project`][] cannot release the prior session's lock, it gives
        up the switch and keeps the prior project; if it then also cannot release
        the lock just taken on the new project, that project is kept here until the
        release-retry timer frees it.
        """
        return self._rollback_session_project

    @property
    def holds_rollback_claim(self) -> bool:
        """``True`` while the in-process claim of [`rollback_session_project`][] is held."""
        return self._rollback_claimed_project_key is not None

    @property
    def read_only_reason(self) -> str | None:
        """Why the loaded project is browse-only, or ``None`` when writes are not blocked.

        The same text the Browser's read-only banner shows ([`read_only_banner`][]),
        for example a lock held by another session or a lost session lock.
        """
        return self._curation_read_only_reason

    @property
    def lock_refresh_active(self) -> bool:
        """``True`` while the timer that refreshes the writable session lock runs."""
        return bool(self._lock_refresh_timer.isActive())

    @property
    def lock_refresh_interval_ms(self) -> int:
        """The session-lock refresh period in milliseconds (well inside the stale timeout)."""
        return int(self._lock_refresh_timer.interval())

    @property
    def lock_release_pending(self) -> bool:
        """``True`` while a session or rollback lock still waits on the release-retry timer.

        Not only a failure signal: the timer also runs, for example, after
        [`close`][] deferred the release because an idealization still writes to
        the project, as well as after a release attempt failed.
        """
        return bool(self._lock_release_timer.isActive())

    def _refresh_project_lock(self) -> None:
        """Refresh the writable session lock or fail closed to read-only browsing."""
        from tether.project.lock import LockedError

        project = self._curation_project
        if self._closed or project is None:
            self._lock_refresh_timer.stop()
            return
        try:
            project.refresh_lock()
        except (LockedError, OSError) as exc:
            self._lock_refresh_timer.stop()
            self._reject_active_conditions_dialog()
            self._curation_project = None
            self._set_read_only_reason(f"session lock lost: {exc}")
            self._import_page.set_project(self._project_path, None)
            self._idealizer = None
            self._conditions = None
            if self._idealize_future is None:
                self._retry_session_release()
            self._status(f"Project became read-only: {self._curation_read_only_reason}")

    def _retry_session_release(self) -> None:
        """Release a disabled session's held nonce, retrying transient I/O failures."""
        rollback_released = self._release_rollback_session_once()
        if self._curation_project is not None or self._idealize_future is not None:
            if rollback_released:
                self._lock_release_timer.stop()
            else:
                self._lock_release_timer.start()
            return
        release_future = self._session_release_future
        if release_future is not None:
            if not release_future.done():
                self._lock_release_timer.start()
                return
            self._session_release_future = None
        if not self._release_session_once() or not rollback_released:
            self._lock_release_timer.start()
            return
        self._lock_release_timer.stop()

    def _release_recovered_lock_once(self) -> bool:
        """Release a lock a failed recovery reopen left held; ``False`` to retry."""
        from tether.project import lock

        with self._session_release_lock:
            recovered = self._recovered_lock
            if recovered is None:
                return True
            path, taken = recovered
            try:
                # Nonce-checked: a no-op once a later open re-took the lock.
                released = lock.release(path, taken)
                if not released:
                    # As Project.release_lock: forget the record only when a
                    # readable sidecar proves the lock is gone or no longer ours.
                    current = lock.read_lock(path)
                    if current is not None and current.nonce == taken.nonce:
                        return False
            except (OSError, lock.CorruptLockError):
                return False
            self._recovered_lock = None
            return True

    def _release_rollback_session_once(self) -> bool:
        """Release a failed project-switch rollback without dropping its lifecycle."""
        if not self._release_recovered_lock_once():
            return False
        with self._session_release_lock:
            project = self._rollback_session_project
            if project is not None:
                try:
                    released = project.release_lock()
                except OSError:
                    return False
                if not released and project._held_lock is not None:
                    return False
                self._rollback_session_project = None
            _release_gui_project(
                self._rollback_claimed_project_key,
                self._project_owner_token,
            )
            self._rollback_claimed_project_key = None
            return True

    def _release_session_once(self) -> bool:
        """Attempt one thread-safe nonce release, retaining lifecycle state on failure."""
        with self._session_release_lock:
            project = self._session_project
            if project is not None:
                try:
                    released = project.release_lock()
                except OSError:
                    return False
                if not released and project._held_lock is not None:
                    return False
                self._session_project = None
            _release_gui_project(self._claimed_project_key, self._project_owner_token)
            self._claimed_project_key = None
            return True

    def _reset_store_docks(self) -> None:
        """Discard the lazily-built overlap / movie docks so a re-open rebuilds them."""
        self._analysis_refresh_timer.stop()
        self._readiness_refresh_timer.stop()
        self._stop_movie_job()
        self._reset_movie_panel()
        for dock_attr, widget_attr in (("_overlap_dock", "_overlap_dock_widget"),):
            widget = getattr(self, widget_attr)
            if widget is not None:
                self._detach_section_dock(widget)
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

    def open_dropped(self, paths: list[Path]) -> Project | None:
        """Open one dropped ``.tether`` file through [`load_project`][] (#629).

        Anything else — another file type, or more than one file — is refused with
        one status message and opens nothing.
        """
        if len(paths) != 1:
            self._status("Drop refused: drop one .tether project file at a time")
            return None
        path = paths[0]
        if path.suffix.lower() != ".tether":
            self._status(f"Drop refused: {path.name} is not a .tether project file")
            return None
        return self.load_project(path)

    @property
    def project_path(self) -> Path | None:
        """The path of the open project, or ``None`` when no project is open."""
        return self._project_path

    @property
    def readiness(self) -> Any | None:
        """The readiness record of the open project, or ``None`` (#896).

        It is [`project_status`][tether.services.status.project_status], the function that
        ``tether status`` prints, read when the project opened or at the last
        [`refresh_readiness`][]. ``None`` when no project is open or the read failed.
        """
        return self._readiness

    def refresh_readiness(self) -> Any | None:
        """Read the readiness of the open project again and return it (#896)."""
        from tether.services.status import project_status

        if self._project_path is None:
            self._readiness = None
        else:
            try:
                self._readiness = project_status(self._project_path)
            except Exception:  # noqa: BLE001 - an open never fails on this read
                self._readiness = None
        self._sync_guided()
        self._reload_generation_page()
        return self._readiness

    def _reload_generation_page(self) -> None:
        """Read the lists of the open reconciliation page again after a write (#923).

        An accept or reject in the trace view also confirms a carried molecule, so the page
        must not keep showing it as waiting.
        """
        page = self._generation_page
        if page is None or self._project_path is None:
            return
        from tether.services.reconcile import read_reconciliations

        try:
            page.set_reconciliations(read_reconciliations(self._project_path))
        except Exception as exc:  # noqa: BLE001 - keep the GUI alive, report the cause
            self._status(f"Reconcile trace generations: the lists could not be read: {exc}")

    def close_project(self) -> bool:
        """Close the open project and release its session lock; show the start page.

        Refused (returns ``False`` with a status message) while idealization owns the
        store, as [`load_project`][] is. The lock release takes the same nonce-safe
        route as a lock-loss fallback: a transient failure keeps the handle and the
        release timer retries it. It also closes the summary of the last correction
        pass, so [`correction_summary`][] is ``None`` afterwards (#1393).
        """
        if self._project_path is None:
            self._status("Close project: no project is open")
            return False
        if self._idealize_future is not None:
            self._status("Close project unavailable: wait for idealization to finish")
            return False
        name = self._project_path.name
        self._remember_position()
        self._reject_active_conditions_dialog()
        self._close_generation_page()
        self._lock_refresh_timer.stop()
        self._curation_project = None
        self._set_read_only_reason(None)
        self._set_conflict_copy_warning(None)
        self._idealizer = None
        self._handoff = None
        self._overlap_seam = None
        self._conditions = None
        self._reset_store_docks()
        self._close_correction_summary()
        self._project_path = None
        self._analysis_section.set_project(None)
        self._import_page.set_project(None, None)
        self._readiness = None
        self._sync_guided()
        self._act_close_project.setEnabled(False)
        self._act_save_recipe.setEnabled(False)
        self._export_menu.setEnabled(False)
        self.set_molecules([])
        # The start page is the page of Curate and correct, so show that section.
        self.set_section(CURATE_SECTION)
        self._retry_session_release()
        self._status(f"Closed {name}")
        return True

    def quit(self) -> None:
        """Close the shell — releasing the session lock as a window close does — and quit."""
        self.close()
        _quit_application()

    # --- start page + Help menu (#629) -------------------------------------

    @property
    def start_page(self) -> Any:
        """The [`StartPage`][tether.gui.start_page.StartPage] shown while nothing is loaded."""
        return self._start_page

    @property
    def start_page_visible(self) -> bool:
        """Whether the start page fills the window: Curate and correct shows, nothing loaded."""
        return (
            self._section == CURATE_SECTION
            and self._curate_stack.currentWidget() is self._start_page.widget
        )

    def _sync_start_page(self) -> None:
        empty = self._project_path is None and not self._traces
        self._curate_stack.setCurrentWidget(
            self._start_page.widget if empty else self._trace_dock.widget
        )

    @property
    def help_menu(self) -> QtWidgets.QMenu:
        """The ``&Help`` menu (documentation, shortcuts, log folder, About)."""
        return self._help_menu

    def _sync_help_menu(self) -> None:
        from tether.gui import about

        self._act_log_folder.setVisible(about.log_folder() is not None)

    def open_documentation(self) -> bool:
        """Open the installed version's documentation in the web browser."""
        from tether.gui import about

        url = about.docs_url()
        opened = about.open_external(url)
        self._status(f"Opened {url}" if opened else f"Could not open {url}")
        return opened

    def open_tutorial(self) -> bool:
        """Open the tutorial of the installed version's documentation."""
        from tether.gui import about

        url = about.docs_url(page="tutorial/")
        opened = about.open_external(url)
        self._status(f"Opened {url}" if opened else f"Could not open {url}")
        return opened

    def open_log_folder(self) -> bool:
        """Open the log folder in the file manager, when it exists."""
        from pyqtgraph.Qt import QtCore

        from tether.gui import about

        folder = about.log_folder()
        if folder is None:
            self._status("No log folder yet")
            return False
        opened = about.open_external(QtCore.QUrl.fromLocalFile(str(folder)).toString())
        self._status("Opened the log folder" if opened else "Could not open the log folder")
        return opened

    def show_about(self) -> Any:
        """Show the About box (version, commit, the three stacks, and **Copy**)."""
        from tether.gui.about import AboutDialog

        # A fresh box each time, so the sidecar line follows a changed environment.
        if self._about is not None:
            self._about.dialog.close()
            self._about.dialog.deleteLater()
        self._about = AboutDialog(parent=self._window)
        self._about.show()
        return self._about

    # --- dialogs -------------------------------------------------------------

    def show_cheatsheet(self) -> CheatSheetOverlay:
        """Show the shipped keyboard cheat-sheet (refreshed from the live keymap)."""
        self._cheatsheet.show()
        return self._cheatsheet

    def show_keymap_editor(self) -> KeymapEditor:
        """Open the keyboard-shortcut editor over the live keymap (#635).

        **Save** puts the edited keymap in effect at once and writes the keymap file.
        """
        from tether.gui.keymap_editor import KeymapEditor

        if self._keymap_editor is not None:
            self._keymap_editor.dialog.close()
            self._keymap_editor.dialog.deleteLater()
        self._keymap_editor = KeymapEditor(
            self._keymap,
            parent=self._window,
            on_save=self.apply_keymap,
            taken=self._menu_chords(),
            unavailable=DEFERRED_ACTIONS,
            file_path=self._keymap_file,
        )
        self._keymap_editor.show()
        return self._keymap_editor

    def apply_keymap(self, keymap: Keymap) -> bool:
        """Put ``keymap``'s bindings in effect now and save them; ``True`` if saved.

        The live keymap object is updated in place, so the event filter and the
        cheat-sheet read the new keys at once. A failed write keeps the new keys for this
        session and says so in the status bar; it never raises.
        """
        self._keymap.bindings = dict(keymap.bindings)
        self._cheatsheet.refresh()
        if self._keymap_file is None:
            self._status("Keyboard shortcuts changed for this session (no keymap file to save to)")
            return False
        try:
            self._keymap.save(self._keymap_file)
        except OSError as exc:
            self._status(f"Keyboard shortcuts changed for this session, but not saved: {exc}")
            return False
        self._status(f"Keyboard shortcuts saved to {self._keymap_file}")
        return True

    def _menu_chords(self) -> dict[tuple[int, int], str]:
        """Every single-key shortcut the window's menus own, with the command's name.

        That is each menu command's shortcut and each menu-bar title's mnemonic (Alt+F for
        File on Windows and Linux; macOS has none), since Qt hands both to the menu before
        the curation filter sees the key.
        """
        from pyqtgraph.Qt import QtGui

        from tether.gui.curation import _as_int

        chords: dict[tuple[int, int], str] = {}

        def add(sequence: Any, name: str) -> None:
            if sequence.count() != 1:
                return
            combined = sequence[0]
            chord = (_as_int(combined.key()), _as_int(combined.keyboardModifiers()))
            chords.setdefault(chord, name)

        for action in self._window.findChildren(QtGui.QAction):
            name = action.text().replace("&", "").rstrip("…") or "a menu command"
            for sequence in action.shortcuts():
                add(sequence, name)
        for action in self._window.menuBar().actions():
            title = action.text().replace("&", "")
            add(QtGui.QKeySequence.mnemonic(action.text()), f"{title} menu")
        return chords

    def _ready_message(self) -> str:
        """The start-up status line, naming the accept, reject and jump keys (#635, #357)."""
        from tether.gui.keymap_editor import chord_label

        parts = []
        for action, verb in (
            (CurationAction.ACCEPT, "accept"),
            (CurationAction.REJECT, "reject"),
            (CurationAction.JUMP, "jump"),
        ):
            chords = sorted(self._keymap.chords_for(Command(action)), key=lambda c: (c[1], c[0]))
            if chords:
                parts.append(f"{chord_label(*chords[0])} {verb}")
        return "Ready — " + " · ".join(parts) if parts else "Ready"

    def _picker_categories(self) -> list[str]:
        """The category list the overflow picker offers.

        A writable project supplies the selected molecule's persisted class lookup.
        Without one, use the shell's ``categories`` argument.
        """
        project = self._curation_project
        row = self._molecule_list.currentRow()
        trace = self._traces[row] if 0 <= row < len(self._traces) else None
        if project is None or trace is None or trace.molecule_key is None:
            return list(self._categories)
        try:
            stored = stored_molecule(project.path, trace.molecule_key, row=row)
            classes = project.class_lookup(stored.condition_id).classes
            return [classes[index] for index in sorted(classes)]
        except Exception as exc:  # noqa: BLE001 - show the lookup failure without guessing
            self._status(f"Category lookup failed: {exc}")
            return []

    def show_overflow_picker(self) -> OverflowCategoryPicker:
        """Open the overflow category picker; on accept, dispatch the assignment.

        It lists the selected molecule's condition's categories beyond the ``1``–``9``
        keys, and the chosen class is stored like a number key's (#359).
        """
        picker = OverflowCategoryPicker(self._picker_categories(), parent=self._window)
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

        if self._idealization_blocks_write("Import"):
            return None
        if self._curation_read_only_reason is not None:
            self._status(
                f"Import unavailable: project is read-only: {self._curation_read_only_reason}"
            )
            return None
        if self._handoff is None:
            self._status("Import: load a project with extracted molecules first")
            return None
        store_session = self._session_project is not None
        try:
            report = self._handoff.preview(smd_path, model_path=model_path)
        except Exception as exc:  # noqa: BLE001 - keep the GUI alive, report the cause
            self._status(f"Return-leg preview failed: {exc}")
            return None
        decision = ReconcileDialog(report, parent=self._window).exec()
        if decision is None:
            self._status("Return-leg import cancelled")
            return None
        # A modal event loop keeps the lock-refresh timer alive. Another writer can
        # steal ownership while the curator is deciding, so revalidate immediately
        # after the dialog and before the return leg opens the project for mutation.
        if self._idealization_blocks_write("Import"):
            return None
        if self._curation_read_only_reason is not None:
            self._status(
                f"Import unavailable: project is read-only: {self._curation_read_only_reason}"
            )
            return None
        if store_session:
            if self._curation_project is not None:
                self._refresh_project_lock()
            if self._curation_project is None:
                reason = self._curation_read_only_reason or "session writer ownership was lost"
                self._status(f"Import unavailable: project is read-only: {reason}")
                return None
        try:
            applied = self._handoff.apply(smd_path, decision, model_path=model_path)
        except Exception as exc:  # noqa: BLE001 - keep the GUI alive, report the cause
            self._status(f"Return-leg import failed: {exc}")
            return None
        summary = _applied_summary(applied)
        if store_session and applied.windows_applied:
            assert self._session_project is not None  # store_session
            try:
                keys_by_id = {trace.molecule_id: trace.molecule_key for trace in report.matched}
                keys = (keys_by_id[molecule_id] for molecule_id in applied.windows_applied)
                self._show_stored_windows(self._session_project.path, keys)
            except Exception as exc:  # noqa: BLE001 - the import stands; say what is not shown
                summary = f"{summary}; the trace view could not reread the windows: {exc}"
        self._queue_readiness_refresh()
        self._queue_states_refresh()  # a return leg can change labels, windows and fits
        self._status(summary)
        return applied

    def _hand_off_dialog(self) -> None:
        """Menu entry: pick a destination SMD, then hand off every extracted molecule.

        The native Windows dialog appends the filter's first extension to a bare name;
        the Qt dialog used on Linux returns the name as typed (#568). So the shell does
        it itself: a path without an ``.hdf5`` or ``.smd`` suffix gets ``.hdf5``, and
        the SMD filter can then find the file again on every OS.

        The dialog's own replace prompt checked the name as typed, not the suffixed
        one, so when the suffix changes the path and that file already exists the
        shell asks before replacing it; the export would otherwise overwrite it.
        """
        from pyqtgraph.Qt import QtWidgets

        if self._handoff is None:
            self._status("Hand-off: load a project with extracted molecules first")
            return
        out_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self._window, "Hand to tMAVEN (SMD)", "", _SMD_FILTER
        )
        if not out_path:
            self._status("Hand-off cancelled")
            return
        smd_path = _with_smd_suffix(out_path)
        if smd_path != out_path and Path(smd_path).exists():
            buttons = QtWidgets.QMessageBox.StandardButton
            answer = QtWidgets.QMessageBox.question(
                self._window,
                "Hand to tMAVEN (SMD)",
                f"{Path(smd_path).name} already exists. Do you want to replace it?",
                buttons.Yes | buttons.No,
                buttons.No,
            )
            if answer != buttons.Yes:
                self._status("Hand-off cancelled")
                return
        self.hand_off_to_tmaven(smd_path)

    def save_recipe(self, path: str | Path) -> Path | None:
        """Write the open project's actions as the recipe file ``path`` (#899).

        The recipe holds the requests of the project's completed jobs, in order, with
        each path relative to the recipe's folder where that is possible
        ([`tether.services.recipe.recipe_from_project`][]). Returns the path written, or
        ``None`` (with a status message) when there is nothing to save or the write failed.
        """
        from tether.services.recipe import RecipeError, recipe_from_project, write_recipe

        if self._project_path is None:
            self._status("Save as recipe: open a project first")
            return None
        try:
            requests = recipe_from_project(self._project_path)
            written = write_recipe(path, requests)
        except (RecipeError, OSError) as exc:
            self._status(f"Save as recipe: {exc}")
            return None
        self._status(f"Saved the recipe {written.name} ({len(requests)} step(s))")
        return written

    # --- File -> Export (#621): the project exports, on the background-task service ---

    def _build_export_menu(self) -> None:
        from tether.gui.exports import MENU_TEXT
        from tether.services.exports import ExportKind

        self._export_menu = self._file_menu.addMenu("&Export")
        self._export_dialog: Any | None = None
        for kind in ExportKind:
            action = self._export_menu.addAction(MENU_TEXT[kind])
            action.triggered.connect(lambda _checked=False, k=kind: self.show_export_dialog(k))
        self._export_menu.setEnabled(False)

    @property
    def export_menu(self) -> QtWidgets.QMenu:
        """The **File → Export** menu (#621)."""
        return self._export_menu

    @property
    def export_dialog(self) -> Any | None:
        """The open export dialog ([`show_export_dialog`][]), or ``None``."""
        return self._export_dialog

    def show_export_dialog(self, kind: Any) -> Any | None:
        """Open the dialog of the export *kind* ([`tether.services.exports.ExportKind`][]).

        Its **Export** starts [`export_project`][] with the choice; **Cancel** writes
        nothing. ``None``, with a status message, when no project is open.
        """
        from tether.gui.exports import TITLES, ExportDialog

        if self._project_path is None:
            self._status(f"{TITLES[kind]}: open a project first")
            return None
        if self._export_dialog is not None:
            self._export_dialog.dialog.close()
        # The list holds one row for each /molecules row, in store order.
        row = self._molecule_list.currentRow()
        key = self._traces[row].molecule_key if 0 <= row < len(self._traces) else None
        molecule = None if key is None else (row, key)
        dialog = ExportDialog(kind, self._project_path, molecule=molecule, parent=self._window)
        self._export_dialog = dialog

        # A finished dialog is deleted: a hidden one can stay the active window (offscreen).
        def accepted() -> None:
            self._export_dialog = None
            dialog.dialog.deleteLater()
            self.export_project(dialog.request())

        def rejected() -> None:
            self._export_dialog = None
            dialog.dialog.deleteLater()
            self._status(f"{TITLES[kind]}: cancelled, nothing was written")

        dialog.dialog.accepted.connect(accepted)
        dialog.dialog.rejected.connect(rejected)
        dialog.dialog.open()
        return dialog

    def export_project(self, request: Any) -> bool:
        """Start the export *request* ([`tether.services.exports.ExportRequest`][]).

        It runs [`run_export`][tether.services.exports.run_export] on the background-task service
        with **Cancel**. The export only reads the project, so it takes no lock and the
        project stays open; every project write waits for it, as for the project fit.
        Returns whether it started; a refusal is a status message.
        """
        from tether.gui.exports import TITLES
        from tether.services.exports import run_export

        title = TITLES[request.kind]
        if self._project_path is None:
            self._status(f"{title}: open a project first")
            return False
        if self._tasks.busy:
            self._status(f"{title} unavailable: a background task is running; wait for it")
            return False
        self._status(f"{title}…")
        started = self._tasks.start(
            run_export,
            self._project_path,
            request,
            context=request,
            label=title,
            on_finished=self._finish_export,
            cancellable=True,
        )
        self._sync_project_write_controls()
        return started is not None

    def _finish_export(self, outcome: TaskOutcome) -> None:
        """Report a finished export on the main thread."""
        from tether.gui.exports import TITLES
        from tether.gui.messages import Level
        from tether.gui.tasks import TaskState
        from tether.services.exports import error_text, export_message

        self._sync_project_write_controls()
        if self._curation_project is None:
            self._retry_session_release()
        title = TITLES[outcome.context.kind]
        if outcome.state is TaskState.CANCELLED:
            self._status(f"{title}: cancelled, nothing was written")
        elif outcome.state is not TaskState.DONE:
            self._status(f"{title} failed: {error_text(outcome.error)}", Level.ERROR)
        else:
            self._status(export_message(outcome.value))

    def _save_recipe_dialog(self) -> None:  # pragma: no cover - interactive file dialog
        """Menu entry: choose where the recipe goes, then [`save_recipe`][]."""
        from pyqtgraph.Qt import QtWidgets

        out_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self._window, "Save as recipe", "", "Tether recipe (*.json)"
        )
        if not out_path:
            self._status("Save as recipe cancelled")
            return
        add_suffix = not out_path.lower().endswith(".json")
        recipe_path = Path(out_path + ".json" if add_suffix else out_path)
        if add_suffix and recipe_path.exists():
            buttons = QtWidgets.QMessageBox.StandardButton
            answer = QtWidgets.QMessageBox.question(
                self._window,
                "Save as recipe",
                f"{recipe_path.name} already exists. Do you want to replace it?",
                buttons.Yes | buttons.No,
                buttons.No,
            )
            if answer != buttons.Yes:
                self._status("Save as recipe cancelled")
                return
        self.save_recipe(recipe_path)

    def _import_dialog(self) -> None:  # pragma: no cover - interactive file dialog
        """Menu entry: pick the returning SMD (+ optional model), then reconcile."""
        from pyqtgraph.Qt import QtWidgets

        if self._idealization_blocks_write("Import"):
            return
        if self._curation_read_only_reason is not None:
            self._status(
                f"Import unavailable: project is read-only: {self._curation_read_only_reason}"
            )
            return
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

    def _reject_active_conditions_dialog(self) -> None:
        """Close a modal conditions editor before a lost session can write unlocked."""
        active = self._active_conditions_dialog
        if active is not None:
            with suppress(RuntimeError):
                active.dialog.reject()

    def _guard_conditions_write(self) -> None:
        """Require the retained session nonce immediately before a dialog write."""
        from tether.project.lock import LockedError

        project = self._curation_project
        if project is None:
            raise RuntimeError("condition editor is no longer writable")
        try:
            project._assert_held_lock()
        except (LockedError, OSError):
            # The loss may occur entirely between heartbeat ticks. Route it through
            # the normal fail-closed state transition before refusing this write.
            self._refresh_project_lock()
            raise

    def _validate_conditions_dialog(self) -> None:  # pragma: no cover - interactive dialog
        """Menu entry: open the condition validation / confirm-correct / merge dialog (§7.6)."""
        from tether.gui.conditions import ConditionValidationDialog

        if self._idealization_blocks_write("Conditions validation"):
            return
        if self._conditions is None:
            self._status("Conditions: load a project first")
            return
        dialog = None
        try:
            dialog = ConditionValidationDialog(
                self._conditions,
                writer_guard=self._guard_conditions_write,
                parent=self._window,
                **self._labeler_kwargs(),
            )
            self._active_conditions_dialog = dialog
            dialog.exec()
        except Exception as exc:  # noqa: BLE001 - keep the GUI alive, report the cause
            self._status(f"Conditions validation failed: {exc}")
            return
        finally:
            if self._active_conditions_dialog is dialog:
                self._active_conditions_dialog = None
        if self._curation_read_only_reason is not None:
            return
        self._status("Conditions validation closed")

    # --- corrections (the correction pass, §7.2, #360) -----------------------

    @property
    def corrections_menu(self) -> QtWidgets.QMenu:
        """The ``Co&rrections`` menu of the Curate and correct section (#360)."""
        return self._corrections_menu

    @property
    def correction_pass_action(self) -> Any:
        """The **Run correction pass…** action of the Corrections menu (#360)."""
        return self._act_correction_pass

    @property
    def correction_summary(self) -> Any | None:
        """The [`CorrectionSummaryDialog`][tether.gui.corrections.CorrectionSummaryDialog].

        It is the summary of the last correction pass, or ``None`` before the first one.
        Closing the project or opening another one closes the summary and resets this to
        ``None`` until the next pass (#1393).
        """
        return self._correction_summary

    def _close_correction_summary(self) -> None:
        """Close the summary of the last pass when the project it describes goes (#1393).

        The summary shows no project name or path, so a summary left open after the
        project is closed, or after another one is opened, reads as the new project's.
        """
        summary = self._correction_summary
        self._correction_summary = None
        if summary is not None:
            with suppress(RuntimeError):  # the window already deleted the dialog
                summary.close()

    def _correction_pass_unavailable(self) -> str | None:
        """Why the correction pass cannot start now, or ``None`` when it can.

        It needs an idle, writable session, a correction state of
        [`PASS_STATES`][tether.services.corrections.PASS_STATES] and no manual factor
        ([`pass_refusal`][tether.services.corrections.pass_refusal], read now, with no lock).
        """
        from tether.services.corrections import pass_refusal

        if self._idealize_future is not None:
            return "a background task is running; wait for it to finish"
        if self._import_page.writing:
            return _RELINK_RUNNING
        project = self._curation_project
        if project is None:
            reason = self._curation_read_only_reason or "load a writable project first"
            prefix = "project is read-only: " if self._curation_read_only_reason else ""
            return f"{prefix}{reason}"
        try:
            return pass_refusal(project.path)
        except Exception as exc:  # noqa: BLE001 - a state that cannot be read refuses the pass
            return f"the correction state could not be read: {exc}"

    def sync_correction_controls(self) -> None:
        """Enable **Run correction pass…** from the correction state, with the reason if not.

        The Corrections menu calls this each time it opens.
        """
        reason = self._correction_pass_unavailable()
        self._act_correction_pass.setEnabled(reason is None)
        tip = "" if reason is None else f"Unavailable: {reason}"
        self._act_correction_pass.setToolTip(tip)
        self._act_correction_pass.setStatusTip(tip)

    def run_correction_pass(self, *, apparent_e_only: bool = False) -> bool:
        """Start the correction pass over the open project; return whether it started.

        The pass is [`compute_corrected_fret`][tether.project.correct.compute_corrected_fret]
        over every molecule, through
        [`run_correction_pass`][tether.services.corrections.run_correction_pass]. It runs on
        the background-task service (#343), so idealization and every other project write
        are frozen until it ends. Its writes are bound to the retained session lock: the
        write guard asserts the exact nonce held when the pass started, so a lock stolen
        while it runs refuses the publish. When it ends, the summary shows field by field
        ([`correction_summary`][]). A refusal is a status message and starts nothing.
        """
        from tether.services.corrections import run_correction_pass

        verb = "Correction pass"
        if self._curation_project is not None:
            self._refresh_project_lock()
        reason = self._correction_pass_unavailable()
        project = self._curation_project
        if reason is None and project is not None:
            try:
                nonce = project._assert_held_lock()
            except Exception:  # noqa: BLE001 - fail closed: any doubt about ownership refuses
                self._refresh_project_lock()
                reason = self._curation_read_only_reason or "the session lock is not held"
        if reason is not None or project is None:
            self._status(f"{verb} unavailable: {reason}")
            return False

        def guard() -> None:
            project._assert_held_lock(expected_nonce=nonce)

        claimed = self._claimed_project_key
        token = self._project_owner_token

        # The publish replaces the file, and the new file has no owner key until the claim
        # moves to it after the pass. The path is held from now until then, so another
        # window in this process cannot claim the new file in between.
        publishing = _hold_publishing_path(project.path, token)

        def task() -> CorrectionPassResult:
            # The window's key moves here too, before the task settles: a window closed
            # while the pass ran abandons the task, and its release after the task then
            # frees the new key. Open and close project are refused while a task runs, so
            # nothing else changes the key meanwhile.
            try:
                return run_correction_pass(
                    project.path, apparent_e_only=apparent_e_only, write_guard=guard
                )
            finally:
                rebound = _rebind_gui_project(claimed, project.path, token)
                if rebound != claimed:
                    self._claimed_project_key = rebound
                _drop_publishing_path(publishing, token)

        self._status("Running the correction pass…")
        started = self._tasks.start(
            task,
            context=project.path,
            label="Correction pass",
            on_finished=self._finish_correction_pass,
        )
        if started is None:
            _drop_publishing_path(publishing, token)
        self._sync_project_write_controls()
        return started is not None

    def _finish_correction_pass(self, outcome: TaskOutcome) -> None:
        """Apply a finished correction pass on the main thread: show it, or say why not."""
        from tether.gui.corrections import BANNER_FAILURE, CorrectionSummaryDialog
        from tether.gui.messages import Level
        from tether.gui.tasks import TaskState

        self._sync_project_write_controls()
        if self._curation_project is None:
            self._retry_session_release()
        if outcome.state is not TaskState.DONE:
            # A lost lock is one way to fail: the refresh turns the window read-only then,
            # and does nothing while the lock is still held.
            self._refresh_project_lock()
            self._status(f"Correction pass failed: {outcome.error}", Level.ERROR)
            return
        self._queue_readiness_refresh()
        self._queue_states_refresh()  # a changed correction method makes a fit stale (#1423)
        result = outcome.value
        if self._correction_summary is not None:
            self._correction_summary.close()
        summary = CorrectionSummaryDialog(result, parent=self._window)
        self._correction_summary = summary
        summary.show()
        s = result.summary
        line = (
            f"Correction pass: {s.n_corrected + s.n_manual} of {s.n_molecules} molecules "
            f"corrected, {s.n_apparent} in apparent E (state {result.state})"
        )
        if summary.banner_kind == BANNER_FAILURE:
            self._status(f"{line}. {summary.banner.text()}", Level.WARNING)
        else:
            self._status(line)

    def _correction_pass_dialog(self) -> None:  # pragma: no cover - interactive dialog
        """Menu entry: ask whether to keep apparent E by choice, then run the pass."""
        from pyqtgraph.Qt import QtWidgets

        box = QtWidgets.QMessageBox(self._window)
        box.setWindowTitle("Run correction pass")
        box.setText(
            "Stamp each molecule's correction method from its leakage factor α and γ. "
            "A molecule without a valid factor stays in apparent E."
        )
        only = QtWidgets.QCheckBox("Apparent E only (keep every molecule in apparent E)")
        box.setCheckBox(only)
        box.setStandardButtons(
            QtWidgets.QMessageBox.StandardButton.Ok | QtWidgets.QMessageBox.StandardButton.Cancel
        )
        if box.exec() != QtWidgets.QMessageBox.StandardButton.Ok:
            self._status("Correction pass cancelled")
            return
        self.run_correction_pass(apparent_e_only=only.isChecked())

    # --- legacy (M7 Deep-LASI re-analysis bundle import, §7.8) ---------------

    @property
    def legacy_menu(self) -> QtWidgets.QMenu:
        """The ``&Import`` menu (FR-LEGACY: Deep-LASI bundle re-analysis import, §7.8)."""
        return self._legacy_menu

    def _new_deeplasi_dialog(self) -> Any:  # pragma: no cover - builds the real dialog
        """Construct the real Deep-LASI wizard dialog parented to the shell window."""
        from tether.gui.deeplasi_wizard_ui import DeepLasiWizardDialog

        return DeepLasiWizardDialog(parent=self._window)

    def import_deeplasi_bundle(
        self, *, dialog_factory: Callable[[], Any] | None = None
    ) -> tuple[Path, ...]:
        """Run the Deep-LASI re-analysis wizard, then report + return the written projects.

        Opens the [`DeepLasiWizardDialog`][tether.gui.deeplasi_wizard_ui.DeepLasiWizardDialog] —
        which
        owns the whole flow (folder intake → per-acquisition plan confirmation →
        reconstruct / analysis-only import) — then reports the written ``.tether``
        project paths in the status bar and returns them (§7.8, FR-LEGACY). The
        ``dialog_factory`` seam defaults to `_new_deeplasi_dialog` (the real
        modal dialog parented to the shell); a test injects a stand-in to drive the
        report path headlessly without a modal event loop. A dialog that raises is
        caught and surfaced as a status message so the shell stays alive; the return
        is the produced paths — empty when the user cancels, writes nothing, or the
        dialog fails.
        """
        if self._idealization_blocks_write("Deep-LASI import"):
            return ()
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

    # --- analysis (the Analyze section's plot builder, §7.7, #374) -----------

    @property
    def analysis_section(self) -> Any:
        """The page of the Analyze section (#374).

        An [`AnalysisSection`][tether.gui.analysis_section.AnalysisSection]: the plot
        registry, the layer list and the preview.
        """
        return self._analysis_section

    def _queue_analysis_refresh(self) -> None:
        """Draw the Analyze plot again once a burst of curation writes ends.

        The single-shot timer of ``_HISTOGRAM_REFRESH_DEBOUNCE_MS`` coalesces the writes, so
        a run of curation keystrokes draws the plot once, not once per key. A layer list
        with no series has nothing to draw again.
        """
        section = self._analysis_section
        if section.project is not None and section.series:
            self._analysis_refresh_timer.start()

    def _queue_readiness_refresh(self) -> None:
        """Read the guided next step again once a burst of writes ends (#897)."""
        if self._project_path is not None:
            self._readiness_refresh_timer.start()

    def _run_queued_analysis_refresh(self) -> None:
        """Apply one coalesced refresh of the Analyze plot; a failure shows in its preview.

        The redraw posts its summary only while Analyze shows, so it never replaces the
        curation result that queued it in the status bar (as the old histogram refresh).
        """
        self._analysis_section.refresh(announce=self._section == ANALYZE_SECTION)

    # --- handlers ------------------------------------------------------------

    def _build_handlers(self) -> CurationHandlers:
        return CurationHandlers(
            accept=lambda: self._curate_current(CurationAction.ACCEPT),
            reject=lambda: self._curate_current(CurationAction.REJECT),
            unreject=lambda: self._curate_current(CurationAction.UNREJECT),
            jump=self._jump_to_movie,
            idealize=self.show_idealize_dialog,
            next=lambda: self._step(+1),
            prev=lambda: self._step(-1),
            next_undecided=self.select_next_undecided,
            assign_category=self._assign_category,
            clear_category=self._clear_category,
            window_start=lambda delta: self._nudge_window(0, delta),
            window_end=lambda delta: self._nudge_window(1, delta),
            reset_window=self._reset_window,
            photobleach=self._photobleach,
            grid=lambda: self._unavailable("Grid toggle", "the grid display is not implemented"),
        )

    def _curate_current(self, action: CurationAction) -> None:
        """Persist accept/reject/un-reject for the selected molecule, then report success."""
        if action not in {
            CurationAction.ACCEPT,
            CurationAction.REJECT,
            CurationAction.UNREJECT,
        }:
            raise ValueError(f"unsupported curation action {action}")

        labels = {
            CurationAction.ACCEPT: ("Accept", "Accepted"),
            CurationAction.REJECT: ("Reject", "Rejected"),
            CurationAction.UNREJECT: ("Un-reject", "Un-rejected"),
        }
        verb, completed = labels[action]
        selection = self._writable_selection(verb)
        if selection is None:
            return
        project, row, trace, nonce = selection

        key = trace.molecule_key
        assert key is not None  # _writable_selection requires a project molecule
        writers = {
            CurationAction.ACCEPT: project.accept,
            CurationAction.REJECT: project.reject,
            CurationAction.UNREJECT: project.unreject,
        }
        before = _read_or_none(lambda: curation_snapshot(project.path, key))
        try:
            self._guard_session_write(project, nonce)
            result = writers[action](key, **self._labeler_kwargs())
        except Exception as exc:  # noqa: BLE001 - keep the GUI alive and surface the write failure
            self._keep_history_if_unchanged(
                key, before, lambda: curation_snapshot(project.path, key)
            )
            self._status(f"{verb} failed: {exc}")
            return
        if action is CurationAction.UNREJECT and result is None:
            self._status("Un-reject unavailable: selected molecule is not rejected")
            return

        target = trace.name or trace.molecule_key
        self._record_undo(CURATION, key, row, f"{verb.lower()} {target}", before, project=project)
        self._queue_analysis_refresh()
        self._queue_readiness_refresh()
        # The rows of this key show their new state now, with no read of the store (#630);
        # the writer set every row that carries the key.
        state = {
            CurationAction.ACCEPT: ACCEPTED,
            CurationAction.REJECT: REJECTED,
            CurationAction.UNREJECT: UNDECIDED,
        }[action]
        self._molecule_view.update_rows(
            self._molecule_view.rows_of_key(trace.molecule_key), curation=state
        )
        stays = action is CurationAction.UNREJECT or not self._advance_after_curation
        if stays and self._molecule_view.shown(row):
            self._status(f"{completed} {target}")
        else:
            # Advance on, or the filter now hides the row: show the next shown one.
            self._advance_from(row, f"{completed} {target}")
        self._after_states_changed()

    def _advance_from(self, row: int, done: str) -> None:
        """Select the next listed molecule after ``row`` once a decision is stored.

        "Next" is the next row in list order that the list shows, so a hidden
        (filtered-out) row is skipped and the list is never reordered (§7.5). At the
        last shown row the selection stays and the status says the end was reached.
        The navigation status the move produces is kept after ``done``, so an overlap
        failure on the new molecule is still reported.
        """
        nxt = self._molecule_view.shown_from(row, 1)
        if nxt is None:
            self._status(f"{done}; end of the list reached")
            return
        self._molecule_list.setCurrentRow(nxt)
        self._status(f"{done}; {self.status_message}")

    @property
    def advance_after_curation(self) -> bool:
        """Whether a stored accept or reject selects the next listed molecule.

        Set once by the ``advance_after_curation`` constructor argument, on by default.
        """
        return self._advance_after_curation

    def _advance_note(self) -> str:
        """The cheat-sheet line that states the advance-after-curation preference."""
        state = "on" if self._advance_after_curation else "off"
        return (
            f"Advance after accept or reject: {state}. When on, Accept and Reject show "
            "the next trace; Un-reject and the category keys keep the same trace."
        )

    def _writable_project(self, verb: str) -> tuple[Project, str] | None:
        """The writable project and lock nonce a project write acts on, or ``None``.

        Refuses writes during background idealization and in a read-only project, with
        a status message. Pass the returned lock nonce to [`_guard_session_write`][]
        immediately before writing.
        """
        if self._idealization_blocks_write(verb):
            return None
        if self._curation_project is not None:
            self._refresh_project_lock()
        project = self._curation_project
        nonce = None
        if project is not None:
            try:
                nonce = project._assert_held_lock()
            except Exception:  # noqa: BLE001 - fail closed: any doubt about ownership refuses
                self._refresh_project_lock()
                project = self._curation_project
        if project is None or nonce is None:
            reason = self._curation_read_only_reason or "load a writable project first"
            prefix = "project is read-only: " if self._curation_read_only_reason else ""
            self._status(f"{verb} unavailable: {prefix}{reason}")
            return None
        return project, nonce

    def _writable_selection(self, verb: str) -> tuple[Project, int, TraceView, str] | None:
        """The writable project, list row, trace and lock nonce a curation write acts on.

        Refuses writes during background idealization, in a read-only project,
        or without a selected project molecule. Pass the returned lock nonce to
        [`_guard_session_write`][] immediately before writing.
        """
        writable = self._writable_project(verb)
        if writable is None:
            return None
        project, nonce = writable
        row = self._molecule_list.currentRow()
        trace = self._traces[row] if 0 <= row < len(self._traces) else None
        if trace is None or trace.molecule_key is None:
            self._status(f"{verb} failed: select a project molecule first")
            return None
        return project, row, trace, nonce

    # --- the reconciliation of a new trace generation (#923) ------------------

    def show_generation_reconcile(self) -> GenerationReconcilePage | None:
        """Show the carried, new and lost molecules of each movie extracted again.

        Curation → Reconcile trace generations…. Returns the page, or ``None`` when no
        project is open or its lists cannot be read (each said on the status bar). The
        page is not modal: a molecule it opens is selected in the trace view.
        """
        from tether.gui.generation_reconcile import GenerationReconcilePage
        from tether.services.reconcile import read_reconciliations

        path = self._project_path
        if path is None:
            self._status("Reconcile trace generations: open a project first")
            return None
        try:
            reconciliations = read_reconciliations(path)
        except Exception as exc:  # noqa: BLE001 - keep the GUI alive, report the cause
            self._status(f"Reconcile trace generations failed: {exc}")
            return None
        self._close_generation_page()
        page = GenerationReconcilePage(
            reconciliations,
            confirm=self._confirm_carried,
            open_molecule=self.select_molecule,
            reload=lambda: read_reconciliations(path),
            parent=self._window,
        )
        self._generation_page = page
        page.show()
        return page

    @property
    def generation_page(self) -> GenerationReconcilePage | None:
        """The reconciliation page while it is open, else ``None``."""
        return self._generation_page

    def _close_generation_page(self) -> None:
        """Close the reconciliation page: its lists belong to the project it was opened on."""
        if self._generation_page is not None:
            self._generation_page.dialog.close()
            self._generation_page = None

    def _confirm_carried(self, keys: list[str]) -> ConfirmResult | None:
        """Confirm carried molecules under the session's writer lock; ``None`` if refused."""
        writable = self._writable_project("Confirm")
        if writable is None:
            return None
        project, nonce = writable
        try:
            self._guard_session_write(project, nonce)
            result = project.confirm_carried(keys, **self._labeler_kwargs())
        except Exception as exc:  # noqa: BLE001 - keep the GUI alive and surface the write failure
            self._status(f"Confirm failed: {exc}")
            return None
        self._queue_readiness_refresh()
        self._status(f"Confirmed {len(result.confirmed)} carried molecule(s)")
        return result

    def select_molecule(self, molecule_key: str) -> bool:
        """Select the molecule ``molecule_key`` in the trace view; return whether it is there.

        The Curate and correct section is shown first, so the trace view is in sight.
        """
        for row, trace in enumerate(self._traces):
            if trace.molecule_key == molecule_key:
                if CURATE_SECTION in self._sections:
                    self.set_section(CURATE_SECTION)
                self._select_shown(row)
                return True
        return False

    def _guard_session_write(self, project: Project, nonce: str) -> None:
        """Require the lock epoch ``nonce`` [`_writable_selection`][] read, just before a write.

        Another writer can take the lock while the bleach detector runs. The nonce
        guards against that even if the other writer has since released the lock.
        A lost lock turns the shell read-only and raises.
        """
        try:
            project._assert_held_lock(expected_nonce=nonce)
        except Exception:
            self._refresh_project_lock()
            raise

    # --- undo and redo of the curation actions (#632) ------------------------------

    @property
    def undo_stack(self) -> UndoStack:
        """The undo and redo lists of this session's curation actions (#632)."""
        return self._undo

    @property
    def edit_menu(self) -> QtWidgets.QMenu:
        """The ``&Edit`` menu: **Undo** and **Redo** of the curation actions (#632)."""
        return self._edit_menu

    @property
    def undo_action(self) -> Any:
        """The **Edit → Undo** action (``Ctrl+Z``)."""
        return self._act_undo

    @property
    def redo_action(self) -> Any:
        """The **Edit → Redo** action (``Ctrl+Shift+Z``; also ``Ctrl+Y`` on Windows)."""
        return self._act_redo

    def _record_undo(
        self,
        kind: str,
        key: str,
        row: int,
        description: str,
        before: object,
        *,
        after: object = None,
        project: Project | None = None,
    ) -> None:
        """Put a stored curation action on the undo list; an action that changed nothing is not.

        ``before`` (or the state after) is ``None`` when it could not be read: the action
        is then stored but cannot be undone, and the molecule's entries leave both lists.

        For an accept, reject or un-reject (``project`` given) the state after the write
        is read back from the store, so a redo writes what the action stored.
        """
        if kind == CURATION:
            assert project is not None
            try:
                after = curation_snapshot(project.path, key)
            except Exception:  # noqa: BLE001 - the write stands; it only cannot be undone
                after = None
        if before is None or after is None:
            # A stored action that cannot be undone drops the molecule's entries: an undo
            # or a redo of an earlier action written over it would take it back unseen.
            # A redo of another molecule's action must not follow a new action either.
            self._forget_undo(key)
            self._undo.clear_redo()
            self._sync_undo_actions()
            return
        if after == before:
            return
        self._undo.push(UndoEntry(kind, key, row, description, before, after))  # type: ignore[arg-type]
        self._sync_undo_actions()

    def _clear_undo(self) -> None:
        """Empty the undo and redo lists: they belong to one open, writable project."""
        self._undo.clear()
        self._sync_undo_actions()

    def _keep_history_if_unchanged(
        self, key: str, before: object, read: Callable[[], object]
    ) -> None:
        """Keep molecule ``key``'s entries after a failed write only if its state is unchanged.

        A writer can fail part way, after it changed the stored state: a reject can store
        its label and then fail on its reject-log row. An undo or a redo written over such
        a state would take it back unseen, so the molecule's entries leave both lists
        unless its state can be read and still equals ``before``.
        """
        if before is not None and _read_or_none(read) == before:
            return
        self._forget_undo(key)

    def _forget_undo(self, key: str) -> None:
        """Drop molecule ``key``'s entries from both lists (see [`UndoStack.forget`][])."""
        self._undo.forget(key)
        self._sync_undo_actions()

    def _sync_undo_actions(self) -> None:
        """Name the action each Edit entry acts on, and enable it only when there is one."""
        undo, redo = self._undo.undo_entry, self._undo.redo_entry
        self._act_undo.setText(f"&Undo {undo.description}" if undo else "&Undo")
        self._act_undo.setEnabled(undo is not None)
        self._act_redo.setText(f"&Redo {redo.description}" if redo else "&Redo")
        self._act_redo.setEnabled(redo is not None)

    def undo(self) -> bool:
        """Take back the last curation action of this session (**Edit → Undo**, ``Ctrl+Z``).

        The molecule gets its stored state from before the action again, by new writes:
        the curation log and the molecule edit log gain rows and lose none (PRD §5.1).
        The list then selects that molecule. Refused, with a status message, while the
        project is read-only, while idealization runs, and when there is nothing to undo.
        Returns whether a state was written back.
        """
        return self._step_history(redo=False)

    def redo(self) -> bool:
        """Do the last undone action again (**Edit → Redo**).

        The keys are ``Ctrl+Shift+Z``, and also ``Ctrl+Y`` on Windows. It writes the state
        the action stored, by new writes, as [`undo`][] does, and is refused in the same
        cases. A new curation action empties the redo list.
        """
        return self._step_history(redo=True)

    def _step_history(self, *, redo: bool) -> bool:
        verb = "Redo" if redo else "Undo"
        writable = self._writable_project(verb)
        if writable is None:
            return False
        entry = self._undo.redo_entry if redo else self._undo.undo_entry
        if entry is None:
            self._status(f"{verb} unavailable: no curation action to {verb.lower()}")
            return False
        project, nonce = writable
        state = entry.after if redo else entry.before
        expected = entry.before if redo else entry.after
        if _read_or_none(lambda: self._read_entry_state(project, entry)) != expected:
            # Something the lists do not hold changed the molecule since (a return-leg
            # import, a bulk reject, a step the store refused): writing this step would
            # take that back unseen, so the molecule's entries leave both lists.
            self._forget_undo(entry.molecule_key)
            self._status(
                f"{verb} unavailable: {entry.description} was changed since by an edit "
                "that cannot be undone, so its steps are no longer on the lists"
            )
            return False
        try:
            self._guard_session_write(project, nonce)
            self._write_undo_state(project, entry, state, verb.lower())
        except Exception as exc:  # noqa: BLE001 - keep the GUI alive and surface the failure
            # The store refused this step, so it stays refused: drop it, so the steps
            # before it can still be taken. A lost lock has emptied both lists already,
            # and a write that failed part way drops the molecule's entries here.
            self._keep_history_if_unchanged(
                entry.molecule_key,
                entry.before if redo else entry.after,
                lambda: self._read_entry_state(project, entry),
            )
            still_there = self._undo.redo_entry if redo else self._undo.undo_entry
            if still_there is entry:
                self._undo.discard(redo=redo)
            self._sync_undo_actions()
            self._refresh_undo_rows(project, entry)
            self._status(
                f"{verb} {entry.description} failed: {exc}; it is no longer on the {verb} list"
            )
            return False
        if redo:
            self._undo.redone()
        else:
            self._undo.undone()
        self._sync_undo_actions()
        self._refresh_undo_rows(project, entry)
        self._select_undo_row(entry)
        self._after_states_changed()
        self._status(f"{verb} {entry.description}")
        return True

    def _read_entry_state(self, project: Project, entry: UndoEntry) -> object:
        """Read the stored state of ``entry``'s molecule, in the form its ``kind`` names."""
        key = entry.molecule_key
        if entry.kind == CURATION:
            return curation_snapshot(project.path, key)
        if entry.kind == CATEGORY:
            return _category_state(stored_rows(project.path, (key,)))
        return _window_state(stored_rows(project.path, (key,)), entry.row)

    def _write_undo_state(
        self, project: Project, entry: UndoEntry, state: Any, reason: str
    ) -> None:
        """Write ``state`` back for ``entry``'s molecule by the action's own setters.

        Each write appends to its log: ``/labels`` (and ``/labels/reject_log``) for an
        accept or reject, ``/settings/molecule_audit`` for a category or a window, whose
        ``reason`` column then reads ``undo`` or ``redo``.
        """
        key = entry.molecule_key
        labeler = self._labeler_kwargs()
        if entry.kind == CURATION:
            snap: CurationSnapshot = state
            project.restore_curation(
                key,
                snap.label,
                pending=snap.pending,
                reason=snap.reason,
                note=snap.note,
                value=snap.value,
                restore_note=reason,
                **labeler,
            )
        elif entry.kind == CATEGORY:
            project.restore_categories(key, dict(state), reason=reason, **labeler)
        else:
            window: WindowState = state
            if window.whole:
                project.reset_analysis_window(key, reason=reason, **labeler)
            else:
                project.restore_analysis_windows(key, dict(window.rows), reason=reason, **labeler)

    def _refresh_undo_rows(self, project: Project, entry: UndoEntry) -> None:
        """Show the stored state of ``entry``'s molecule on its list rows and trace view."""
        key = entry.molecule_key
        rows = self._molecule_view.rows_of_key(key)
        try:
            if entry.kind == CURATION:
                label = curation_snapshot(project.path, key).label
                self._molecule_view.update_rows(rows, curation=curation_state(label))
                self._queue_analysis_refresh()
                self._queue_readiness_refresh()
            elif entry.kind == CATEGORY:
                # Each list row shows its own stored category; a listed row the store
                # does not hold shows its key's first.
                by_row = {m.row: m for m in stored_rows(project.path, (key,))}
                first = next(iter(by_row.values()))
                for i in rows:
                    self._molecule_view.update_rows([i], category=by_row.get(i, first).category)
            else:
                self._queue_analysis_refresh()
                self._queue_readiness_refresh()
                self._queue_states_refresh()
                self._show_stored_windows(project.path, (key,))
        except Exception:  # noqa: BLE001 - the next full read shows it; the write stands
            self._queue_states_refresh()

    def _select_undo_row(self, entry: UndoEntry) -> None:
        """Select the molecule an undo or redo changed, so the curator sees it (#632).

        The row the action was made on when it still carries the molecule, else the
        first row that does. A row the filter hides is shown first.
        """
        rows = [i for i, t in enumerate(self._traces) if t.molecule_key == entry.molecule_key]
        if not rows:
            return
        row = entry.row if entry.row in rows else rows[0]
        if CURATE_SECTION in self._sections and self._section != CURATE_SECTION:
            self.set_section(CURATE_SECTION)
        if row != self._molecule_list.currentRow() or not self._molecule_view.shown(row):
            self._select_shown(row)

    def _assign_category(self, integer_class: int) -> None:
        """Store class ``integer_class`` of the molecule's condition list as its category.

        The condition's persisted [`ClassLookup`][tether.project.conditions.ClassLookup]
        maps class ``N`` to its ``N``-th category. An unmapped class leaves the store and
        the selection unchanged.
        """
        verb = f"Category {integer_class} assignment"
        selection = self._writable_selection(verb)
        if selection is None:
            return
        project, row, trace, nonce = selection
        key = trace.molecule_key
        assert key is not None  # _writable_selection requires a project molecule
        try:
            stored = stored_molecule(project.path, key, row=row)
            classes = project.class_lookup(stored.condition_id).classes
        except Exception as exc:  # noqa: BLE001 - keep the GUI alive and surface the failure
            self._status(f"{verb} failed: {exc}")
            return
        name = classes.get(integer_class)
        if name is None:
            count = f"{len(classes)} " + ("category" if len(classes) == 1 else "categories")
            self._status(f"{verb} unavailable: its condition has {count}")
            return

        def read() -> tuple[tuple[str, str], ...]:
            return _category_state(stored_rows(project.path, (key,)))

        before = _read_or_none(read)
        try:
            self._guard_session_write(project, nonce)
            project.set_category(key, name, **self._labeler_kwargs())
        except Exception as exc:  # noqa: BLE001 - keep the GUI alive and surface the failure
            self._keep_history_if_unchanged(key, before, read)
            self._status(f"{verb} failed: {exc}")
            return
        target = trace.name or key
        self._record_undo(
            CATEGORY,
            key,
            row,
            f"category {integer_class} ({name}) for {target}",
            before,
            after=_read_or_none(read),
        )
        self._molecule_view.update_rows(self._molecule_view.rows_of_key(key), category=name)
        self._after_states_changed()
        self._status(f"Category {integer_class} ({name}) set for {target}")

    def _clear_category(self) -> None:
        """Clear the selected molecule's category to *uncategorized* (``0``, §7.3)."""
        verb = "Category clearing"
        selection = self._writable_selection(verb)
        if selection is None:
            return
        project, row, trace, nonce = selection
        key = trace.molecule_key
        assert key is not None  # _writable_selection requires a project molecule

        def read() -> tuple[tuple[str, str], ...]:
            return _category_state(stored_rows(project.path, (key,)))

        before = _read_or_none(read)
        try:
            self._guard_session_write(project, nonce)
            project.clear_category(key, **self._labeler_kwargs())
        except Exception as exc:  # noqa: BLE001 - keep the GUI alive and surface the failure
            self._keep_history_if_unchanged(key, before, read)
            self._status(f"{verb} failed: {exc}")
            return
        self._record_undo(
            CATEGORY,
            key,
            row,
            f"category clearing for {trace.name or key}",
            before,
            after=_read_or_none(read),
        )
        self._molecule_view.update_rows(self._molecule_view.rows_of_key(key), category="")
        self._after_states_changed()
        self._status(f"Category cleared for {trace.name or key}")

    # --- the category list of the selected molecule's condition (#633) ----------

    def _selected_condition_id(self) -> str | None:
        """The ``condition_id`` of the selected project molecule, or ``None``.

        ``None`` while no project is open, nothing is selected, the molecule has no
        condition, or its row cannot be read.
        """
        path = self._project_path
        row = self._molecule_list.currentRow()
        trace = self._traces[row] if 0 <= row < len(self._traces) else None
        if path is None or trace is None or trace.molecule_key is None:
            return None
        try:
            return stored_molecule(path, trace.molecule_key, row=row).condition_id or None
        except Exception:  # noqa: BLE001 - an unreadable row names no condition
            return None

    def category_names(self) -> dict[int, str] | None:
        """The category each number key assigns to the selected molecule, by class (#633).

        Class ``k`` is the ``k``-th name of the list of the selected molecule's condition,
        as the number keys and the overflow picker use it. ``None`` while no project
        molecule with a condition is selected, or when the list cannot be read.
        """
        from tether.services.curation import category_classes

        condition_id = self._selected_condition_id()
        if condition_id is None or self._project_path is None:
            return None
        try:
            return category_classes(self._project_path, condition_id)
        except Exception:  # noqa: BLE001 - the keys then show only their numbers
            return None

    def _category_members(self, condition_id: str, name: str) -> tuple[str, ...]:
        """The keys of the molecules of ``condition_id`` whose stored category is ``name``."""
        from tether.services.curation import category_members

        assert self._project_path is not None
        return category_members(self._project_path, condition_id, name)

    def category_usage(self, name: str, *, condition_id: str | None = None) -> int:
        """How many molecules of a condition have category ``name``.

        ``condition_id`` names the condition; ``None`` takes the selected molecule's.
        """
        if condition_id is None:
            condition_id = self._selected_condition_id()
        if condition_id is None:
            return 0
        try:
            return len(self._category_members(condition_id, name))
        except Exception:  # noqa: BLE001 - nothing read, so nothing to count
            return 0

    def _category_target(
        self, verb: str, condition_id: str | None
    ) -> tuple[Project, str, str] | None:
        """The writable project, the condition and the lock nonce of a list change.

        A given ``condition_id`` is used as it is: the editor binds the condition it was
        opened on, so a later change of the selection cannot redirect an edit (#633).
        ``None`` takes the selected molecule's condition. Says why on the status bar and
        returns ``None`` when the project is read-only, or with no ``condition_id`` when
        nothing is selected or the selected molecule has no condition.
        """
        if condition_id is not None:
            writable = self._writable_project(verb)
            if writable is None:
                return None
            project, nonce = writable
            return project, condition_id, nonce
        selection = self._writable_selection(verb)
        if selection is None:
            return None
        project, _row, _trace, nonce = selection
        condition_id = self._selected_condition_id()
        if condition_id is None:
            self._status(f"{verb} unavailable: the selected molecule has no condition")
            return None
        return project, condition_id, nonce

    def _current_category_names(self, condition_id: str | None = None) -> tuple[str, ...]:
        """The list of ``condition_id`` (default: the selected molecule's), or ``()``."""
        if condition_id is None:
            condition_id = self._selected_condition_id()
        if condition_id is None or self._project_path is None:
            return ()
        from tether.services.curation import category_names

        try:
            return category_names(self._project_path, condition_id)
        except Exception:  # noqa: BLE001 - show an empty list rather than guess
            return ()

    def _category_failed(
        self, message: str, condition_id: str | None, *, post: bool = True
    ) -> CategoryEdit:
        """Report ``message`` and return a failed edit that shows the list as stored.

        ``post=False`` when the status bar already says it.
        """
        from tether.gui.category_editor import CategoryEdit

        if post:
            self._status(message)
        names = self._current_category_names(condition_id)
        return CategoryEdit(ok=False, names=names, message=message)

    def _category_done(self, names: Sequence[str], message: str) -> CategoryEdit:
        """Report ``message``, show the new names on the keys, and return the edit."""
        from tether.gui.category_editor import CategoryEdit

        self._status(message)
        self._cheatsheet.refresh()
        return CategoryEdit(ok=True, names=tuple(names), message=message)

    def add_category(self, name: str, *, condition_id: str | None = None) -> CategoryEdit:
        """Add ``name`` at the end of the list of a condition.

        ``condition_id`` names the condition; ``None`` takes the selected molecule's, as
        it does for each of the list changes below. It calls
        [`Project.add_category`][tether.project.core.Project.add_category] under the
        session's lock. The new name takes the next number key.
        """
        verb = "Add category"
        target = self._category_target(verb, condition_id)
        if target is None:
            return self._category_failed(self.status_message, condition_id, post=False)
        project, condition_id, nonce = target
        try:
            self._guard_session_write(project, nonce)
            result = project.add_category(condition_id, name)
        except Exception as exc:  # noqa: BLE001 - keep the GUI alive and surface the failure
            return self._category_failed(f"{verb} failed: {exc}", condition_id)
        names = result.categories
        return self._category_done(names, f"Category {len(names)} ({names[-1]}) added")

    def rename_category(
        self, old: str, new: str, *, condition_id: str | None = None
    ) -> CategoryEdit:
        """Rename ``old`` to ``new`` and keep the category of every molecule that has it.

        The list keeps the position, so the number key stays the same
        ([`Project.rename_category`][tether.project.core.Project.rename_category]).
        Molecules store their category by name, so each molecule of the condition that
        has ``old`` is then given ``new``
        ([`Project.set_category`][tether.project.core.Project.set_category]), with one
        molecule edit log row each that names the rename. All under the session's lock.
        """
        verb = "Rename category"
        target = self._category_target(verb, condition_id)
        if target is None:
            return self._category_failed(self.status_message, condition_id, post=False)
        project, condition_id, nonce = target
        try:
            members = self._category_members(condition_id, old)
            self._guard_session_write(project, nonce)
            result = project.rename_category(condition_id, old, new)
        except Exception as exc:  # noqa: BLE001 - keep the GUI alive and surface the failure
            return self._category_failed(f"{verb} failed: {exc}", condition_id)
        renamed = result.categories[result.code_of(new.strip())]
        reason = f"category renamed from {old!r} to {renamed!r}"
        moved = 0
        for key in members:
            try:
                self._guard_session_write(project, nonce)
                project.set_category(key, renamed, reason=reason, **self._labeler_kwargs())
            except Exception as exc:  # noqa: BLE001 - say what is left, never hide it
                left = len(members) - moved
                self._after_states_changed()
                return self._category_failed(
                    f"{verb}: “{old}” is now “{renamed}” in the list, but {left} of "
                    f"{len(members)} molecules still have “{old}”: {exc}",
                    condition_id,
                )
            self._molecule_view.update_rows(self._molecule_view.rows_of_key(key), category=renamed)
            moved += 1
        self._after_states_changed()
        noun = "molecule keeps" if moved == 1 else "molecules keep"
        return self._category_done(
            result.categories, f"Category “{old}” renamed to “{renamed}”; {moved} {noun} it"
        )

    def remove_category(self, name: str, *, condition_id: str | None = None) -> CategoryEdit:
        """Remove ``name`` from the list of a condition.

        It calls [`Project.remove_category`][tether.project.core.Project.remove_category]
        under the session's lock. The library's rule applies: a molecule that has
        ``name`` keeps it as its stored category, and the categories after it move up
        one number key. The editor asks before it calls this for a used name.
        """
        verb = "Remove category"
        target = self._category_target(verb, condition_id)
        if target is None:
            return self._category_failed(self.status_message, condition_id, post=False)
        project, condition_id, nonce = target
        try:
            used = len(self._category_members(condition_id, name))
            self._guard_session_write(project, nonce)
            result = project.remove_category(condition_id, name)
        except Exception as exc:  # noqa: BLE001 - keep the GUI alive and surface the failure
            return self._category_failed(f"{verb} failed: {exc}", condition_id)
        message = f"Category “{name}” removed"
        if used:
            noun = "molecule keeps" if used == 1 else "molecules keep"
            message += f"; {used} {noun} it as a name that is not in the list"
        return self._category_done(result.categories, message)

    def move_category(
        self, name: str, offset: int, *, condition_id: str | None = None
    ) -> CategoryEdit:
        """Move ``name`` ``offset`` places in the list (``-1`` up, ``+1`` down).

        The new order is written with
        [`Project.set_category_list`][tether.project.core.Project.set_category_list]
        under the session's lock. Molecules keep their categories; the number keys follow
        the new order.
        """
        verb = "Move category"
        target = self._category_target(verb, condition_id)
        if target is None:
            return self._category_failed(self.status_message, condition_id, post=False)
        project, condition_id, nonce = target
        from tether.services.curation import category_names

        try:
            names = list(category_names(project.path, condition_id))
            index = names.index(name)
        except ValueError:
            return self._category_failed(
                f"{verb} failed: “{name}” is not in the list", condition_id
            )
        except Exception as exc:  # noqa: BLE001 - keep the GUI alive and surface the failure
            return self._category_failed(f"{verb} failed: {exc}", condition_id)
        target_index = index + offset
        if not 0 <= target_index < len(names):
            return self._category_failed(
                f"{verb} unavailable: “{name}” is at the end of the list", condition_id
            )
        names.insert(target_index, names.pop(index))
        try:
            self._guard_session_write(project, nonce)
            result = project.set_category_list(condition_id, names)
        except Exception as exc:  # noqa: BLE001 - keep the GUI alive and surface the failure
            return self._category_failed(f"{verb} failed: {exc}", condition_id)
        return self._category_done(
            result.categories, f"Category “{name}” is now category {target_index + 1}"
        )

    def show_category_editor(self) -> CategoryEditor | None:
        """Open the editor of the list of the selected molecule's condition (#633).

        **Curation → Categories…** and the **Categories…** button of the browser panel.
        Returns the editor after it closes, or ``None`` (with a status message) when no
        project molecule with a condition is selected. A read-only project shows the list,
        and each change then says why it is unavailable.
        """
        from tether.gui.category_editor import CategoryEditor

        condition_id = self._selected_condition_id()
        if condition_id is None:
            self._status("Categories: select a molecule of a condition in an open project first")
            return None
        # Every read and write of the editor is bound to the condition it opens on: a
        # rename the filter hides, or a click, can move the selection to a molecule of
        # another condition while the editor is open (#633).
        cid = condition_id
        editor = CategoryEditor(
            self._condition_text(cid),
            self._current_category_names(cid),
            usage=lambda name: self.category_usage(name, condition_id=cid),
            add=lambda name: self.add_category(name, condition_id=cid),
            rename=lambda old, new: self.rename_category(old, new, condition_id=cid),
            remove=lambda name: self.remove_category(name, condition_id=cid),
            move=lambda name, offset: self.move_category(name, offset, condition_id=cid),
            parent=self._window,
        )
        self._category_editor = editor
        editor.exec()
        return editor

    def _condition_text(self, condition_id: str) -> str:
        """The condition's identity fields in one line, else its ``condition_id``."""
        from tether.gui.conditions import _key_summary
        from tether.services.curation import condition_key

        assert self._project_path is not None
        try:
            key = condition_key(self._project_path, condition_id)
        except Exception:  # noqa: BLE001 - the id alone still names it
            key = None
        return f"{_key_summary(key)} ({condition_id})" if key is not None else condition_id

    @property
    def category_editor(self) -> CategoryEditor | None:
        """The last category editor opened, or ``None`` before the first."""
        return self._category_editor

    @property
    def categories_button(self) -> QtWidgets.QPushButton:
        """The browser panel's **Categories…** button, which opens the editor."""
        return self._categories_button

    def _nudge_window(self, bound: int, delta: int) -> None:
        """Move the analysis window's start (``bound`` 0) or end (``bound`` 1) by ``delta``.

        The window is ``[start, stop)`` in absolute frames. A nudge that would leave the
        molecule's ``frame_range`` or empty the window is refused and stores nothing; a
        stored one redraws the dock's window and idealization overlay (§7.3).
        """
        verb = "Analysis-window editing"
        selection = self._writable_selection(verb)
        if selection is None:
            return
        project, row, trace, nonce = selection
        key = trace.molecule_key
        assert key is not None  # _writable_selection requires a project molecule
        try:
            stored = stored_molecule(project.path, key, row=row)
        except Exception as exc:  # noqa: BLE001 - keep the GUI alive and surface the failure
            self._status(f"{verb} failed: {exc}")
            return
        start, stop = stored.window
        wanted = [start, stop]
        wanted[bound] += delta
        lo, hi = stored.frame_range
        name = "start" if bound == 0 else "end"
        if wanted[0] >= wanted[1]:
            reason = "the window must keep at least one frame"
        elif wanted[0] < lo or wanted[1] > hi:
            reason = f"the window {name} is at the trace {name}"
        else:
            reason = None
        if reason is not None:
            self._status(f"Analysis window unchanged at [{start}, {stop}): {reason}")
            return
        self._write_window(
            project,
            nonce,
            row,
            trace,
            lambda: project.set_analysis_window(key, *wanted, **self._labeler_kwargs()),
            verb,
            "Analysis window",
            f"analysis-window {name}",
        )

    def _reset_window(self) -> None:
        """Restore the analysis window to the molecule's whole ``frame_range`` (``R``)."""
        verb = "Analysis-window reset"
        selection = self._writable_selection(verb)
        if selection is None:
            return
        project, row, trace, nonce = selection
        key = trace.molecule_key
        assert key is not None  # _writable_selection requires a project molecule
        self._write_window(
            project,
            nonce,
            row,
            trace,
            lambda: project.reset_analysis_window(key, **self._labeler_kwargs()),
            verb,
            "Analysis window reset to",
            "window reset",
        )

    def _photobleach(self) -> None:
        """Detect the summed-intensity photobleach and end the analysis window there (``P``).

        Runs the native single-step detector
        ([`detect_photobleach`][tether.fret.photobleach.detect_photobleach]) on the
        molecule's background-subtracted (``corrected``) donor and acceptor over its
        ``frame_range``, as [`compute_photobleach`][tether.project.photobleach.compute_photobleach]
        does, and stores ``[start, first bleach)`` with the window's start kept — tMAVEN's
        ``P`` moves only the end. With no bleach the end is the trace end. A bleach at or
        before the window start is refused. It is a manual window, so a later project
        photobleach pass leaves it alone; the per-channel ``bleach_frames`` the
        corrections read are not changed here.
        """
        verb = "Photobleach detection"
        selection = self._writable_selection(verb)
        if selection is None:
            return
        project, row, trace, nonce = selection
        key = trace.molecule_key
        assert key is not None  # _writable_selection requires a project molecule
        try:
            stored = stored_molecule(project.path, key, row=row)
            bleach = summed_bleach_frame(project.path, stored)
        except Exception as exc:  # noqa: BLE001 - keep the GUI alive and surface the failure
            self._status(f"{verb} failed: {exc}")
            return
        start = stored.window[0]
        if bleach <= start:
            self._status(
                f"{verb} unavailable: the summed intensity bleaches at frame {bleach}, "
                f"not after the window start {start}"
            )
            return
        found = (
            f"Photobleach at frame {bleach}:"
            if bleach < stored.frame_range[1]
            else "No photobleach step found:"
        )
        self._write_window(
            project,
            nonce,
            row,
            trace,
            lambda: project.set_analysis_window(key, start, bleach, **self._labeler_kwargs()),
            verb,
            f"{found} analysis window",
            "photobleach edit",
        )

    def _write_window(
        self,
        project: Project,
        nonce: str,
        row: int,
        trace: TraceView,
        write: Callable[[], object],
        verb: str,
        done: str,
        undo_text: str,
    ) -> None:
        """Run one analysis-window write, then show the window the project now stores.

        The window is read back from the project rather than assumed from the request,
        so the dock's bounds and overlay always agree with ``/molecules.analysis_window``.
        The windows before and after go on the undo list as ``undo_text`` (#632).
        """
        key = trace.molecule_key
        assert key is not None  # every caller passed _writable_selection

        def read() -> WindowState:
            return _window_state(stored_rows(project.path, (key,)), row)

        before = _read_or_none(read)
        try:
            self._guard_session_write(project, nonce)
            write()
        except Exception as exc:  # noqa: BLE001 - keep the GUI alive and surface the failure
            self._keep_history_if_unchanged(key, before, read)
            self._status(f"{verb} failed: {exc}")
            return
        self._queue_analysis_refresh()
        self._queue_readiness_refresh()
        self._queue_states_refresh()  # a window edit can make the row's fit stale
        try:
            by_row = self._show_stored_windows(project.path, (key,))
        except Exception as exc:  # noqa: BLE001 - the write stands; say what is not shown
            self._record_undo(WINDOW, key, row, undo_text, None)  # cannot be undone
            self._status(
                f"Analysis window saved for {trace.name or key}, "
                f"but the trace view could not reread it: {exc}"
            )
            return
        after = _window_state(tuple(by_row.values()), row)
        description = f"{undo_text} for {trace.name or key}"
        self._record_undo(WINDOW, key, row, description, before, after=after)
        selected = by_row.get(row) or next(iter(by_row.values()))
        start, stop = selected.window
        self._status(f"{done} [{start}, {stop}) for {trace.name or key}")

    def _show_stored_windows(
        self, path: str | Path, keys: Iterable[str]
    ) -> dict[int, StoredMolecule]:
        """Give each listed trace of ``keys`` its stored analysis window; return them by row.

        A window setter writes every row that carries a key, each clamped to its own
        ``frame_range``, so each listed row takes its own stored window back, and a
        listed row the store does not hold takes its key's first. The current trace's
        dock bounds and overlay are redrawn. Raises ``KeyError`` when no row carries
        any of ``keys``.
        """
        from dataclasses import replace

        stored = stored_rows(path, keys)
        if not stored:
            raise KeyError(f"no molecule with any of the molecule_keys in {Path(path).name}")
        by_row = {m.row: m for m in stored}
        first: dict[str, StoredMolecule] = {}
        for m in stored:
            first.setdefault(m.molecule_key, m)
        current = self._molecule_list.currentRow()
        for i, listed in enumerate(self._traces):
            mine = by_row.get(i)
            if mine is None or mine.molecule_key != listed.molecule_key:
                mine = first.get(listed.molecule_key or "")
            if mine is None:
                continue
            window = _relative_window(mine.frame_range[0], listed.n_frames, mine.window)
            self._traces[i] = replace(listed, analysis_window=window)
            if i == current and self._trace_dock.trace is not None:
                self._trace_dock.set_analysis_window(window)
        return by_row

    def _unavailable(self, feature: str, reason: str) -> None:
        """Report a deferred GUI command without claiming it changed project data."""
        self._status(f"{feature} unavailable: {reason}")

    @property
    def is_idealizing(self) -> bool:
        """Whether a background task (the project fit, the correction pass) is running."""
        return self._tasks.busy

    @property
    def _idealize_future(self) -> Any | None:
        # The Future of the in-flight fit, held by the background-task service. Every
        # source-project writer gate reads it: None means idealization owns no HDF5.
        return self._tasks.future

    @property
    def idealize_dialog(self) -> Any | None:
        """The open "Idealize project…" dialog ([`show_idealize_dialog`][]), or ``None``."""
        return self._idealize_dialog

    def _idealize_unavailable(self) -> str | None:
        """Why "Idealize project…" cannot run now, or ``None`` when it can."""
        if self._idealize_future is not None:
            return "a background task is running; wait for it to finish"
        if self._import_page.writing:
            return _RELINK_RUNNING
        if self._curation_project is not None:
            self._refresh_project_lock()
        if self._idealizer is None or self._curation_project is None:
            return self._curation_read_only_reason or "load a project with extracted molecules"
        return None

    def show_idealize_dialog(self) -> Any | None:
        """Open "Idealize project…" (``I``, **Idealize** menu, #625); return the dialog.

        The dialog ([`IdealizeProjectDialog`][tether.gui.idealize_project.IdealizeProjectDialog])
        shows non-modal over the window, with the facts of the open project read now; its
        **Idealize** starts [`idealize_project`][] with the choice. ``None``, with a status
        message, when no fit can run: no writable project, or a background task running.
        """
        from tether.gui.idealize_project import IdealizeProjectDialog, read_fit_facts

        reason = self._idealize_unavailable()
        project = self._curation_project
        if reason is not None or project is None:
            self._status(f"Idealize project unavailable: {reason}")
            return None
        try:
            facts = read_fit_facts(project)
        except Exception as exc:  # noqa: BLE001 - keep the GUI alive, report the read failure
            self._status(f"Idealize project unavailable: {exc}")
            return None
        if self._idealize_dialog is not None:
            self._idealize_dialog.dialog.close()
        dialog = IdealizeProjectDialog(facts, parent=self._window)
        self._idealize_dialog = dialog

        def accepted() -> None:
            self._idealize_dialog = None
            self.idealize_project(dialog.choice())

        def rejected() -> None:
            self._idealize_dialog = None

        dialog.dialog.accepted.connect(accepted)
        dialog.dialog.rejected.connect(rejected)
        dialog.dialog.open()
        return dialog

    def idealize_project(self, fit: Any) -> bool:
        """Start the whole-project fit *fit* (#625).

        *fit* is a [`ProjectFit`][tether.gui.idealize_project.ProjectFit].

        It runs on the background-task service (#343) with progress and **Cancel**; the
        project stays open for browsing while every project write waits for it. The fit
        is the ``idealize`` command on the session's own project handle, bound to the
        exact lock held when it starts. A cancel stops it before its model is written,
        so the project is unchanged. Returns whether it started; a refusal is a status
        message and starts nothing.
        """
        from tether.gui.idealize_project import METHOD_LABELS

        reason = self._idealize_unavailable()
        if reason is not None:
            self._status(f"Idealize project unavailable: {reason}")
            return False
        label = METHOD_LABELS.get(fit.method, fit.method)
        what = f"Refitting the stale {fit.method} model" if fit.stale_only else "Idealizing project"
        self._status(f"{what} ({label})…")
        started = self._tasks.start(
            self._idealizer,
            fit,
            context=fit,
            label=what,
            on_finished=self._finish_idealize,
            cancellable=True,
            reports_progress=True,
        )
        self._sync_project_write_controls()
        return started is not None

    def _finish_idealize(self, outcome: TaskOutcome) -> None:
        """Apply a finished project fit on the main thread: report it and draw the step line."""
        from tether.gui.idealize_project import SIDECAR_REMEDY, outcome_message, stored_model
        from tether.gui.messages import Level
        from tether.gui.tasks import TaskState
        from tether.services.idealize import SidecarUnavailable

        # The service has already stopped the timer, restored the cursor and stopped the
        # indicator, and holds no task: the write gates below see an idle shell.
        fit = outcome.context
        self._sync_project_write_controls()
        if self._curation_project is None:
            self._retry_session_release()
        if outcome.state is TaskState.CANCELLED:
            self._status("Idealize project cancelled: the project is unchanged")
            return
        if outcome.state is not TaskState.DONE:
            self._refresh_project_lock()
            detail = f"Idealize project failed: {outcome.error}"
            if isinstance(outcome.error, SidecarUnavailable):
                detail = f"{detail}. {SIDECAR_REMEDY}"
            self._status(detail, Level.ERROR)
            return
        result = outcome.value
        self._queue_readiness_refresh()
        self._queue_states_refresh()  # the rows now have a fit
        self._queue_analysis_refresh()  # a model-derived plot may read the new fit
        self._status(outcome_message(fit, result))
        stored = stored_model(result)
        row = self._molecule_list.currentRow()
        key = self._traces[row].molecule_key if 0 <= row < len(self._traces) else None
        if stored is None or stored.idealized is None or key not in stored.molecule_keys:
            return
        try:
            self._trace_dock.set_idealization(stored.idealized[stored.molecule_keys.index(key)])
        except Exception as exc:  # noqa: BLE001 - a mismatched result must not crash
            self._status(f"Idealize project: the step line of {key} cannot be drawn: {exc}")

    def _step(self, delta: int) -> None:
        if not self._traces:
            self._status("Next trace" if delta > 0 else "Previous trace")
            return
        # The next row the filter shows; at either end the selection stays (#630).
        current = self._molecule_list.currentRow()
        new_row = self._molecule_view.shown_from(current, 1 if delta > 0 else -1)
        if new_row is None:
            if current < 0 or not self._molecule_view.shown(current):
                self._ensure_current_shown()
            return
        self._molecule_list.setCurrentRow(new_row)

    def _status(self, message: str, level: Any = None) -> None:
        self._messages.post(message, level)

    # --- lifecycle -----------------------------------------------------------

    def show(self) -> None:
        self._window.show()

    def close(self) -> None:
        """Remove the filter, settle writer ownership, and close the window."""
        self._close(close_window=True)

    def _close(self, *, close_window: bool) -> None:
        """Shared teardown for API/app shutdown and an in-progress window Close event.

        A running idealizer cannot be force-cancelled safely while its sidecar may
        still commit HDF5 data. Its result is abandoned visually, but the session
        lock is retained until that future completes and releases it from its done
        callback. With no active writer, the lock is released immediately.
        """
        from tether.gui import theme

        if self._closed:
            return
        self._closed = True
        # What the next launch restores (#628), saved while every widget still exists.
        self._remember_position()
        self._save_layout()
        self._reject_active_conditions_dialog()
        self._window.removeEventFilter(self._window_close_filter)
        self._window_close_filter = None
        if self._app is not None:
            with suppress(RuntimeError, TypeError):
                self._app.aboutToQuit.disconnect(self.close)
            self._app = None
        self._event_filter.remove()
        self._lock_refresh_timer.stop()
        self._lock_release_timer.stop()
        self._analysis_refresh_timer.stop()
        self._readiness_refresh_timer.stop()
        future = self._idealize_future
        writable_project = self._curation_project
        session_project = self._session_project
        if writable_project is not None and future is not None and not future.done():
            # Give an abandoned in-flight writer a fresh full staleness window.
            # The write path re-checks ownership before committing, so a later
            # explicit steal still fails closed rather than racing HDF5.
            from tether.project.lock import LockedError

            with suppress(LockedError, OSError):
                writable_project.refresh_lock()
        self._curation_project = None
        self._set_read_only_reason(None)
        self._set_conflict_copy_warning(None)
        # A running fit is abandoned (its result is discarded): the service stops its
        # poll timer and restores the wait cursor it set, so it never leaks past close.
        self._tasks.abandon()
        # The same for a running channel-map command (#353): its outcome is not applied
        # to a closed shell, and its worker stops once the command ends.
        self._channel_map.close()
        # The same for a running preview or extraction of the Prepare page (#350).
        self._extraction.close()
        self._import_page.close()
        self._busy_indicator.stop()
        self._messages.close()
        theme.unsubscribe(self._apply_menu_icons)
        self._molecule_view.close()
        self._start_page.close()
        # The "Idealize project…" dialog is non-modal: close it with the window.
        if self._idealize_dialog is not None:
            self._idealize_dialog.dialog.close()
            self._idealize_dialog = None
        # The correction summary is its own top-level window: it would outlive the shell.
        if self._correction_summary is not None:
            self._correction_summary.close()
            self._correction_summary = None
        if session_project is not None and future is not None and not future.done():
            # Keep both lifecycle ownership records until the abandoned writer
            # settles. The repeating timer then attempts release and keeps retrying
            # any transient OSError. The completion callback also makes one safe
            # attempt for QApplication shutdown, but never drops state on failure.
            self._session_release_future = future
            self._lock_release_timer.start()

            def release_after_fit(_done: Any) -> None:
                self._session_release_future = None
                self._release_session_once()

            future.add_done_callback(release_after_fit)
        else:
            # Keep the lifecycle handle and process-local claim until the exact held
            # nonce is released. A transient unlink failure is retried by the timer
            # even though the visible shell has already closed.
            self._retry_session_release()
        self._tasks.shutdown()
        self._stop_movie_job()
        self._movie_tasks.shutdown()
        self._analysis_section.close()
        if self._overlap_dock is not None:
            self._overlap_dock.close()
        self._reset_movie_panel()
        self._trace_dock.close()
        if close_window:
            self._window.close()

    def __enter__(self) -> TetherShell:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()


def make_store_idealizer(project: Project, *, _runner: Any = None) -> Idealizer:
    """Build the store-backed whole-project fit for a [`TetherShell`][] (#625).

    Returns ``idealizer(fit, cancel=None, progress=None)``, which runs
    [`fit_project`][tether.gui.idealize_project.fit_project] on *project*: the
    ``idealize`` command ([`run_held`][tether.services.idealize.run_held]) on this handle,
    whose session lock it requires and does not release. ``_runner`` is the private
    sidecar test seam of the command.
    """
    from tether.gui.idealize_project import fit_project

    def _idealize(fit: Any, *, cancel: Any = None, progress: Any = None) -> Any:
        return fit_project(project, fit, cancel=cancel, progress=progress, _runner=_runner)

    return _idealize


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
    """Build the store-backed static-overlap-view seam for a [`TetherShell`][].

    Reads ``/molecules`` (donor coordinates + ``movie_id``) and the cached
    ``/patches`` **once**, computes each molecule's nearest-neighbour distance +
    apertures-overlap flag via [`tether.analysis.overlap.neighbor_report`][]
    (confined per movie, §5.2), and returns a ``molecule_key -> OverlapInfo`` lookup
    the shell refreshes on selection. The geometry is static — spot coordinates
    never change with curation — so it is computed once, not per call (unlike the
    Analyze section's plots). ``aperture_radius`` defaults to the experiment's stored
    extraction ``disk_radius``, falling back to
    [`DEFAULT_APERTURE_RADIUS`][tether.analysis.overlap.DEFAULT_APERTURE_RADIUS] when absent, so the
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


def _with_smd_suffix(path: str) -> str:
    """``path`` with ``.hdf5`` appended unless it already ends in an SMD suffix (#568).

    The comparison ignores case, so ``HANDOFF.HDF5`` is left as typed.
    """
    if Path(path).suffix.lower() in _SMD_SUFFIXES:
        return path
    return path + _SMD_SUFFIXES[0]


def _applied_summary(applied: AppliedReconcile) -> str:
    """A one-line status summary of what an [`TetherShell.import_return_leg`][] committed."""
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
    """Store-backed `HandoffSeam` over [`tether.project.handoff`][].

    Binds one project + intensity quantity so all three legs use the same ``/traces``
    layer; see [`make_store_handoff`][]. Each op imports its ``handoff`` function
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
        from tether.project.core import Project as _Project
        from tether.project.handoff import apply_reconcile

        kwargs = {
            "model_path": model_path,
            "intensity_quantity": self._intensity_quantity,
            "model_name": self._model_name,
            "accept_windows": decision.accept_windows,
            "accept_classes": decision.accept_classes,
            "import_idealization": decision.import_idealization,
            "overwrite": self._overwrite,
        }
        if isinstance(self._project, _Project):
            # Use the Project facade for a live GUI store so its point-of-write
            # ownership assertions back up the post-dialog refresh. `require_held_lock`
            # binds each write to the retained session's exact nonce, so a lock stolen
            # while `_reconcile()` re-resolves the match — and released again before the
            # return leg persists — is still refused. Matches the retained-session
            # contract the whole-project fit of `make_store_idealizer` uses.
            return self._project.apply_reconcile(smd_path, require_held_lock=True, **kwargs)
        return apply_reconcile(
            self._project,
            smd_path,
            **kwargs,
        )


def make_store_handoff(
    project: Project | str | PathLike[str],
    *,
    intensity_quantity: str = "corrected",
    model_name: str | None = None,
    overwrite: bool = False,
) -> HandoffSeam:
    """Build the store-backed `HandoffSeam` for a [`TetherShell`][].

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
    [`TraceView`][tether.gui.trace_dock.TraceView]'s ``frame_time``. A stored ``0.0`` (the
    extractor's "TIFF interval unknown" sentinel) maps to ``None`` — a frame-index-only
    axis — because [`TraceView`][] rejects a non-positive ``frame_time``. An
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
    ``.tether`` into the value objects [`TetherShell.set_molecules`][] consumes, so a
    project can be opened live in the shell ([`TetherShell.load_project`][]). Reads
    ``/molecules`` + ``/traces`` positionally (row *i* of each is the same molecule,
    §5.1) and, for every molecule, slices the chosen intensity layer to its **full native
    ``frame_range``** — the whole trace including post-bleach frames, the curation view
    (unlike analysis, which narrows to the ``analysis_window``). ``frame_range`` unset
    ``[0, 0]`` falls back to the full row extent. Each [`TraceView`][] carries its
    decoded ``molecule_key`` (so the idealize / overlap seams resolve the store row) and
    its ``frame_time`` from the linked ``/movies`` row (``0.0``/movie-less → ``None``).

    Rejected molecules are **included** (a curator revisits rejects); the Analyze
    section's plots still exclude them by their own default. ``intensity_quantity`` selects
    the ``/traces`` layer via the frozen
    [`INTENSITY_QUANTITY_LAYERS`][tether.project.trace_layers.INTENSITY_QUANTITY_LAYERS] map —
    ``"corrected"``
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
    analysis_window = molecules["analysis_window"]
    calibration_window = calibration_windows(path)  # positional, as /molecules (#928)

    def _decode(value: object) -> str:
        return value.decode() if isinstance(value, bytes) else str(value)

    views: list[TraceView] = []
    for i in range(molecules.shape[0]):
        lo, hi = resolved_frame_range(frame_range[i], max_t)
        donor = np.asarray(donor_all[i, lo:hi], dtype=np.float64)
        acceptor = np.asarray(acceptor_all[i, lo:hi], dtype=np.float64)
        window = resolved_window(analysis_window[i], (lo, hi))
        views.append(
            TraceView(
                donor=donor,
                acceptor=acceptor,
                frame_time=frame_times.get(_decode(movie_col[i])),
                molecule_key=_decode(key_col[i]),
                analysis_window=_relative_window(lo, int(donor.size), window),
                frame_offset=lo,
                calibration_window=_relative_window(
                    lo,
                    int(donor.size),
                    (int(calibration_window[i][0]), int(calibration_window[i][1])),
                ),
            )
        )
    return views


def _relative_window(offset: int, n_frames: int, window: tuple[int, int]) -> tuple[int, int] | None:
    """An absolute window as frames into a trace that starts at ``offset``, or ``None``.

    Clipped to the trace's ``n_frames``; ``None`` when nothing of it lies inside.
    """
    start = min(max(window[0] - offset, 0), n_frames)
    stop = min(max(window[1] - offset, 0), n_frames)
    return (start, stop) if start < stop else None


def launch() -> None:  # pragma: no cover - interactive smoke entry point
    """Launch the shell with synthetic traces (computer-use live-smoke entry).

    Not part of the headless test surface; run via ``python -m tether.gui.shell``
    to exercise the focus contract by hand / with the computer-use MCP.
    """
    import numpy as np
    from pyqtgraph.Qt import QtWidgets

    from tether.gui.identity import apply_application_identity
    from tether.gui.trace_dock import TraceView

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setApplicationName(_APP_NAME)
    app.setOrganizationName("MondragonLab")
    apply_application_identity(app)

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

    shell = TetherShell(conditions=Project(demo_path))
    shell.set_molecules(traces)
    shell.show()
    app.exec()


if __name__ == "__main__":  # pragma: no cover
    launch()

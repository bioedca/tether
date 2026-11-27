# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The import page of the Prepare section: add movies, pair, set the layout, repair (#913).

[`ImportPage`][] is the **Import** dock of the Prepare section (FR-WORKFLOW, FR-EXTRACT). The
user:

* **adds movies** with **Add movies…** or by a drop of files on the page. Each movie is read
  once on the background worker, and the page shows its reader, its shape, its pixel type
  and each metadata value with its origin (from the file, from you, or unknown);
* **pairs two movies** as the donor and the acceptor files of one acquisition (#909). The
  pair is refused when the frame count, the frame time, the pixel type or the frame size
  differ;
* **sets the channel layout** (#908) and the **frame steps** (#1435), and exchanges the
  donor and the acceptor. Each change shows at once on the frame view, as two outlined
  rectangles on the first frame, and is kept for the movie only on **Apply**;
* **saves a recipe** (#899) with one ``extract`` step for each applied movie, or **uses a
  movie in the extraction page** beside it, which receives the movie and its layout;
* on the open project, **links a missing source again** (#912) and **gives a missing frame
  time** (#911). A frame time from you shows as *from you, not verified* until **Confirm**.

Every step is a function of [`tether.services.movie_import`][] (ADR-0100), so this page adds
no direct call into the core. The module imports no Qt at import time; the class imports it
when it is built.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tether.services import movie_import as mi
from tether.services.base import RequestRefused, ServiceError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    import numpy as np

    from tether.gui.tasks import TaskOutcome
    from tether.project.core import Project

__all__ = ["ImportEntry", "ImportPage"]

#: The interval of the main-thread poll of a running read or link, in ms.
_POLL_MS = 50

_RECIPE_FILTER = "Tether recipe (*.json);;All files (*)"


@dataclasses.dataclass(frozen=True)
class ImportEntry:
    """One acquisition on the page: one movie, or the two files of a movie pair.

    ``layout`` and ``frame_steps`` are the applied choices; ``applied`` says whether the
    user applied them, so a recipe holds only movies the user saw on the frame view.
    """

    donor: mi.MovieDescription
    acceptor: mi.MovieDescription | None = None
    layout: mi.LayoutChoice = mi.LayoutChoice()
    frame_steps: str = ""
    applied: bool = False

    @property
    def is_pair(self) -> bool:
        """Whether the entry is a movie pair (two files)."""
        return self.acceptor is not None

    @property
    def name(self) -> str:
        """The label of the entry: the movie, or ``donor + acceptor`` for a pair."""
        if self.acceptor is None:
            return self.donor.name
        return f"{self.donor.name} + {self.acceptor.name}"


def _read_movies(paths: Sequence[Path]) -> tuple[list[tuple[Any, Any]], list[str]]:
    """Describe each movie and read its first frame (worker thread)."""
    found: list[tuple[Any, Any]] = []
    refused: list[str] = []
    for path in paths:
        try:
            found.append((mi.describe_movie(path), mi.read_first_frame(path)))
        except RequestRefused as exc:
            refused.append(str(exc))
    return found, refused


def _rect_text(rect: tuple[int, ...] | None) -> str:
    return "" if rect is None else ", ".join(str(v) for v in rect)


def _parse_rect(text: str, name: str) -> tuple[int, int, int, int]:
    parts = [p for p in text.replace(",", " ").split() if p]
    try:
        values = tuple(int(p) for p in parts)
    except ValueError:
        values = ()
    if len(values) != 4:
        raise RequestRefused(f"{name} needs four whole numbers: y1, x1, y2, x2")
    return values  # type: ignore[return-value]


class ImportPage:
    """The Import dock of the Prepare section.

    ``status`` shows one line in the window's status bar; ``window`` parents the file
    dialogs; ``use_movie`` receives ``(movie path, layout form texts)`` for **Use in
    extraction**, or is ``None`` to hide that button. ``write_blocked(feature)`` answers
    whether another background writer owns the project now (the shell's fit or correction
    pass), and reports it; the page asks it before each write, because such a writer
    replaces the whole file when it finishes and would drop the change. Create this on the
    main thread, after the ``QApplication``. The page is [`widget`][].
    """

    def __init__(
        self,
        *,
        status: Callable[[str], None],
        window: Any = None,
        use_movie: Callable[[Path, dict[str, str]], None] | None = None,
        write_blocked: Callable[[str], bool] | None = None,
    ) -> None:
        from tether.gui.task_service import TaskService

        self._status = status
        self._window = window
        self._use_movie = use_movie
        self._write_blocked = write_blocked
        self._relinking = False
        self._tasks = TaskService(poll_ms=_POLL_MS)
        self._entries: list[ImportEntry] = []
        self._frames: dict[Path, np.ndarray] = {}
        self._view: mi.FrameView | None = None
        self._project_path: Path | None = None
        self._project: Project | None = None
        self._sources: tuple[mi.ProjectSource, ...] = ()
        self._frame_times: tuple[mi.FrameTime, ...] = ()
        self._loading = False
        self._build()

    # --- widgets ------------------------------------------------------------------

    def _build(self) -> None:
        import pyqtgraph as pg
        from pyqtgraph.Qt import QtCore, QtWidgets

        from tether.gui import theme

        outer = self._widget = _DropWidget(self.add_movies)
        outer.setAccessibleName("Import")
        outer_layout = QtWidgets.QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        outer_layout.addWidget(scroll)
        content = QtWidgets.QWidget()
        scroll.setWidget(content)
        layout = QtWidgets.QVBoxLayout(content)
        intro = QtWidgets.QLabel(
            "Add movies, or drop movie files here. Set the channel layout and the frame "
            "steps of each movie, and check them on the frame view before you apply them."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        add_row = QtWidgets.QHBoxLayout()
        self._add_button = QtWidgets.QPushButton("&Add movies…")
        self._add_button.clicked.connect(self._add_dialog)
        self._pair_button = QtWidgets.QPushButton("&Pair two movies")
        self._pair_button.setToolTip("Select two movies: the first one is the donor file")
        self._pair_button.clicked.connect(self.pair_selected)
        self._unpair_button = QtWidgets.QPushButton("&Unpair")
        self._unpair_button.clicked.connect(self.unpair_selected)
        for button in (self._add_button, self._pair_button, self._unpair_button):
            add_row.addWidget(button)
        layout.addLayout(add_row)

        self._movie_list = QtWidgets.QListWidget()
        self._movie_list.setAccessibleName("Movies")
        self._movie_list.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self._movie_list.currentRowChanged.connect(self._show_entry)
        layout.addWidget(self._movie_list)

        self._metadata_table = QtWidgets.QTableWidget(0, 3)
        self._metadata_table.setAccessibleName("Movie details")
        self._metadata_table.setHorizontalHeaderLabels(["Value", "Setting", "Origin"])
        self._metadata_table.horizontalHeader().setStretchLastSection(True)
        self._metadata_table.verticalHeader().setVisible(False)
        self._metadata_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self._metadata_table)

        geometry = QtWidgets.QGroupBox("Channels")
        form = self._layout_form = QtWidgets.QFormLayout(geometry)
        self._kind_combo = QtWidgets.QComboBox()
        self._kind_combo.setAccessibleName("Channel layout")
        for kind in mi.LAYOUT_KINDS:
            self._kind_combo.addItem(kind, kind)
        form.addRow("Layout", self._kind_combo)
        self._donor_combo = QtWidgets.QComboBox()
        self._donor_combo.setAccessibleName("Donor channel")
        form.addRow("Donor", self._donor_combo)
        self._donor_rect = QtWidgets.QLineEdit()
        self._donor_rect.setAccessibleName("Donor rectangle")
        self._donor_rect.setPlaceholderText("y1, x1, y2, x2")
        form.addRow("Donor rectangle", self._donor_rect)
        self._acceptor_rect = QtWidgets.QLineEdit()
        self._acceptor_rect.setAccessibleName("Acceptor rectangle")
        self._acceptor_rect.setPlaceholderText("y1, x1, y2, x2")
        form.addRow("Acceptor rectangle", self._acceptor_rect)
        self._steps_field = QtWidgets.QLineEdit()
        self._steps_field.setAccessibleName("Frame steps")
        self._steps_field.setPlaceholderText("none, or for example: rotate 90, flip h")
        form.addRow("Frame steps", self._steps_field)
        buttons = QtWidgets.QHBoxLayout()
        self._swap_button = QtWidgets.QPushButton("&Swap donor and acceptor")
        self._swap_button.clicked.connect(self.swap_roles)
        self._apply_button = QtWidgets.QPushButton("A&pply")
        self._apply_button.clicked.connect(self.apply)
        buttons.addWidget(self._swap_button)
        buttons.addWidget(self._apply_button)
        form.addRow(buttons)
        layout.addWidget(geometry)
        self._kind_combo.currentIndexChanged.connect(self._kind_changed)
        self._donor_combo.currentIndexChanged.connect(self._draft_changed)
        for field in (self._donor_rect, self._acceptor_rect, self._steps_field):
            field.textChanged.connect(self._draft_changed)

        self._view_note = QtWidgets.QLabel("Add a movie to see its first frame.")
        self._view_note.setWordWrap(True)
        self._view_note.setAccessibleName("Frame view note")
        layout.addWidget(self._view_note)
        self._plot = pg.PlotWidget()
        self._plot.setMinimumHeight(180)
        self._plot.setAspectLocked(True)
        self._plot.invertY(True)
        self._plot.hideAxis("left")
        self._plot.hideAxis("bottom")
        self._image = pg.ImageItem()
        self._plot.addItem(self._image)
        self._rect_items = {}
        colors = theme.current()
        for role in ("donor", "acceptor"):
            item = QtWidgets.QGraphicsRectItem()
            item.setPen(pg.mkPen(colors.trace(role), width=2))
            item.setVisible(False)
            self._plot.addItem(item)
            self._rect_items[role] = item
        layout.addWidget(self._plot)

        out_row = QtWidgets.QHBoxLayout()
        self._recipe_button = QtWidgets.QPushButton("Save &recipe…")
        self._recipe_button.clicked.connect(self._recipe_dialog)
        out_row.addWidget(self._recipe_button)
        self._use_button = QtWidgets.QPushButton("Use in &extraction")
        self._use_button.clicked.connect(self.use_in_extraction)
        self._use_button.setHidden(self._use_movie is None)
        out_row.addWidget(self._use_button)
        layout.addLayout(out_row)

        project_box = QtWidgets.QGroupBox("Open project")
        project_layout = QtWidgets.QVBoxLayout(project_box)
        self._project_note = QtWidgets.QLabel("Open a project to repair its sources.")
        self._project_note.setWordWrap(True)
        project_layout.addWidget(self._project_note)
        self._source_table = _table(("Source", "Role", "State"), "Project sources")
        project_layout.addWidget(self._source_table)
        self._relink_button = QtWidgets.QPushButton("&Link again…")
        self._relink_button.clicked.connect(self._relink_dialog)
        project_layout.addWidget(self._relink_button)
        self._time_table = _table(("Movie", "Frame time (s)", "Origin"), "Frame times")
        project_layout.addWidget(self._time_table)
        time_row = QtWidgets.QHBoxLayout()
        self._time_field = QtWidgets.QDoubleSpinBox()
        self._time_field.setAccessibleName("Frame time in seconds")
        self._time_field.setDecimals(6)
        self._time_field.setRange(0.0, 3600.0)
        self._time_field.setSingleStep(0.01)
        self._give_button = QtWidgets.QPushButton("&Give frame time")
        self._give_button.clicked.connect(self._give_clicked)
        self._confirm_button = QtWidgets.QPushButton("&Confirm frame time")
        self._confirm_button.clicked.connect(self._confirm_clicked)
        for widget in (self._time_field, self._give_button, self._confirm_button):
            time_row.addWidget(widget)
        project_layout.addLayout(time_row)
        layout.addWidget(project_box)

        self._message = QtWidgets.QLabel()
        self._message.setWordWrap(True)
        self._message.setAccessibleName("Import message")
        self._message.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._message)
        layout.addStretch(1)
        self._kind_changed()
        self._show_project()

    # --- the state a test reads ---------------------------------------------------

    @property
    def widget(self) -> Any:
        """The page: the ``QWidget`` the shell places in the Import dock."""
        return self._widget

    @property
    def entries(self) -> tuple[ImportEntry, ...]:
        """The acquisitions on the page, in list order."""
        return tuple(self._entries)

    @property
    def view(self) -> mi.FrameView | None:
        """What the frame view shows now: the stepped frame and the two rectangles."""
        return self._view

    @property
    def message(self) -> str:
        """The page's last message: a refusal, a warning or what an action did."""
        return str(self._message.text())

    @property
    def details(self) -> list[tuple[str, str, str]]:
        """The rows of the details table: ``(setting, value, origin)``."""
        table = self._metadata_table
        return [
            (table.item(r, 1).text(), table.item(r, 0).text(), table.item(r, 2).text())
            for r in range(table.rowCount())
        ]

    @property
    def sources(self) -> tuple[mi.ProjectSource, ...]:
        """The source records of the open project."""
        return self._sources

    @property
    def frame_times(self) -> tuple[mi.FrameTime, ...]:
        """The frame time of each movie of the open project."""
        return self._frame_times

    @property
    def busy(self) -> bool:
        """Whether a read or a link runs, its outcome not yet applied."""
        return self._tasks.busy

    @property
    def writing(self) -> bool:
        """Whether a link again runs: it writes the project, so no other writer may start."""
        return self._relinking and self._tasks.busy

    @property
    def movie_list(self) -> Any:
        """The list of acquisitions (a ``QListWidget``)."""
        return self._movie_list

    @property
    def source_table(self) -> Any:
        """The table of the open project's sources."""
        return self._source_table

    @property
    def time_table(self) -> Any:
        """The table of the open project's frame times."""
        return self._time_table

    # --- add movies --------------------------------------------------------------

    def add_movies(self, paths: Iterable[str | Path]) -> bool:
        """Read the movies at ``paths`` on the worker and add each one that opens.

        Returns whether the read started. A file that no reader opens is named in the
        message with the reason, and the others are still added.
        """
        files = [Path(p) for p in paths]
        if not files:
            return False
        if self._refuse_while_busy("Add movies"):
            return False
        self._say(f"Reading {len(files)} movie(s)…")
        task = self._tasks.start(
            _read_movies, files, label="Reading movies", on_finished=self._movies_read
        )
        return task is not None

    def _movies_read(self, outcome: TaskOutcome) -> None:
        from tether.gui.tasks import TaskState

        if outcome.state is not TaskState.DONE:
            self._say(f"Movies not added: {outcome.error}")
            return
        found, refused = outcome.value
        for description, frame in found:
            self._frames[description.path] = frame
            self._entries.append(ImportEntry(description))
        self._refresh_list(len(self._entries) - 1 if found else None)
        added = f"Added {len(found)} movie(s)."
        self._say(" ".join([added, *(f"Not added: {r}" for r in refused)]))

    # --- pair -----------------------------------------------------------------------

    def _selected_rows(self) -> list[int]:
        return sorted(self._movie_list.row(item) for item in self._movie_list.selectedItems())

    def pair_selected(self) -> bool:
        """Pair the two selected movies, the upper one as the donor file (#909)."""
        rows = self._selected_rows()
        if len(rows) != 2 or any(self._entries[r].is_pair for r in rows):
            self._say("Not paired: select two movies that are not paired")
            return False
        donor, acceptor = (self._entries[r].donor for r in rows)
        try:
            mi.check_movie_pair(donor, acceptor)
        except RequestRefused as exc:
            self._say(f"Not paired: {exc}")
            return False
        first, second = rows
        del self._entries[second]
        self._entries[first] = ImportEntry(donor, acceptor)
        self._refresh_list(first)
        self._say(f"Paired: {donor.name} is the donor file, {acceptor.name} the acceptor file")
        return True

    def unpair_selected(self) -> bool:
        """Split the selected movie pair back into two movies."""
        row = self._movie_list.currentRow()
        if row < 0 or not self._entries[row].is_pair:
            self._say("Not unpaired: select a movie pair")
            return False
        entry = self._entries[row]
        assert entry.acceptor is not None
        self._entries[row : row + 1] = [ImportEntry(entry.donor), ImportEntry(entry.acceptor)]
        self._refresh_list(row)
        self._say(f"Unpaired {entry.name}")
        return True

    # --- the layout and the frame view ---------------------------------------------

    def _current(self) -> ImportEntry | None:
        row = self._movie_list.currentRow()
        return self._entries[row] if 0 <= row < len(self._entries) else None

    def set_layout(self, layout: mi.LayoutChoice, frame_steps: str = "") -> None:
        """Set the draft layout and frame steps in the form; the frame view shows them."""
        self._loading = True
        try:
            self._kind_combo.setCurrentIndex(max(self._kind_combo.findData(layout.kind), 0))
            self._fill_donor_positions()
            if layout.donor is not None:
                self._donor_combo.setCurrentIndex(max(self._donor_combo.findData(layout.donor), 0))
            self._donor_rect.setText(_rect_text(layout.donor_rect))
            self._acceptor_rect.setText(_rect_text(layout.acceptor_rect))
            self._steps_field.setText(frame_steps)
        finally:
            self._loading = False
        self._enable_layout_fields()
        self._draft_changed()

    def draft(self) -> tuple[mi.LayoutChoice, str]:
        """Return the layout and the frame steps the form holds now (not yet applied)."""
        kind = str(self._kind_combo.currentData())
        steps = self._steps_field.text().strip()
        if kind == "rectangles":
            return (
                mi.LayoutChoice(
                    kind=kind,
                    donor=None,
                    donor_rect=_parse_rect(self._donor_rect.text(), "the donor rectangle"),
                    acceptor_rect=_parse_rect(self._acceptor_rect.text(), "the acceptor rectangle"),
                ),
                steps,
            )
        return mi.LayoutChoice(kind=kind, donor=str(self._donor_combo.currentData())), steps

    def swap_roles(self) -> bool:
        """Exchange the donor and the acceptor in the draft (for a pair, the two files)."""
        entry = self._current()
        if entry is None:
            self._say("Not swapped: select a movie first")
            return False
        if entry.acceptor is not None:
            row = self._movie_list.currentRow()
            self._entries[row] = dataclasses.replace(
                entry, donor=entry.acceptor, acceptor=entry.donor, applied=False
            )
            self._refresh_list(row)
            self._say(f"Swapped: {entry.acceptor.name} is now the donor file")
            return True
        try:
            layout, steps = self.draft()
        except RequestRefused as exc:
            self._say(f"Not swapped: {exc}")
            return False
        self.set_layout(layout.swapped(), steps)
        self._say("Swapped the donor and the acceptor; check the frame view, then apply")
        return True

    def apply(self) -> bool:
        """Keep the draft layout and frame steps for the selected movie, once they fit it."""
        entry = self._current()
        if entry is None:
            self._say("Not applied: select a movie first")
            return False
        try:
            layout, steps = self.draft()
            view = mi.frame_view(self._frames[entry.donor.path], layout, steps, pair=entry.is_pair)
        except RequestRefused as exc:
            self._say(f"Not applied: {exc}")
            return False
        row = self._movie_list.currentRow()
        self._entries[row] = dataclasses.replace(
            entry, layout=layout, frame_steps=steps, applied=True
        )
        self._refresh_list(row)
        self._say(
            f"Applied to {entry.name}" + (f". Warning: {view.warning}" if view.warning else "")
        )
        return True

    def _fill_donor_positions(self) -> None:
        kind = str(self._kind_combo.currentData())
        self._donor_combo.blockSignals(True)
        self._donor_combo.clear()
        for position in mi.donor_positions(kind):
            self._donor_combo.addItem(position, position)
        self._donor_combo.blockSignals(False)

    def _enable_layout_fields(self) -> None:
        """Show the rows the layout uses: the donor half, or the two rectangles.

        A movie pair uses neither, since each file is one whole channel. Rows are hidden
        rather than disabled, so no control waits on a step it cannot name.
        """
        entry = self._current()
        pair = entry is not None and entry.is_pair
        rects = str(self._kind_combo.currentData()) == "rectangles"
        form = self._layout_form
        form.setRowVisible(self._kind_combo, not pair)
        form.setRowVisible(self._donor_combo, not pair and not rects)
        form.setRowVisible(self._donor_rect, not pair and rects)
        form.setRowVisible(self._acceptor_rect, not pair and rects)

    def _kind_changed(self, *_args: object) -> None:
        if self._loading:
            return
        self._fill_donor_positions()
        self._enable_layout_fields()
        self._draft_changed()

    def _draft_changed(self, *_args: object) -> None:
        """Show the draft on the frame view: the stepped frame and the two rectangles."""
        if self._loading:
            return
        entry = self._current()
        self._view = None
        if entry is None:
            self._draw(None)
            self._view_note.setText("Add a movie to see its first frame.")
            return
        try:
            layout, steps = self.draft()
            self._view = mi.frame_view(
                self._frames[entry.donor.path], layout, steps, pair=entry.is_pair
            )
        except RequestRefused as exc:
            self._draw(None)
            self._view_note.setText(f"Cannot show this layout: {exc}")
            return
        self._draw(self._view)
        pending = (layout, steps) != (entry.layout, entry.frame_steps) or not entry.applied
        state = "Preview, not applied yet." if pending else "Applied."
        what = (
            "Each file is one whole channel."
            if entry.is_pair
            else "The donor and the acceptor trace colors outline the two channels."
        )
        warning = f" Warning: {self._view.warning}" if self._view.warning else ""
        self._view_note.setText(f"{state} {what}{warning}")

    def _draw(self, view: mi.FrameView | None) -> None:
        from pyqtgraph.Qt import QtCore

        if view is None:
            self._image.clear()
            for item in self._rect_items.values():
                item.setVisible(False)
            return
        # pyqtgraph reads an image as [column, row]; the frame is [row, column].
        self._image.setImage(view.frame.T, autoLevels=True)
        for role, rect in (("donor", view.donor_rect), ("acceptor", view.acceptor_rect)):
            y1, x1, y2, x2 = rect
            item = self._rect_items[role]
            item.setRect(QtCore.QRectF(x1 - 1, y1 - 1, x2 - x1 + 1, y2 - y1 + 1))
            item.setVisible(True)

    def _show_entry(self, *_args: object) -> None:
        entry = self._current()
        self._show_details(entry)
        if entry is None:
            self._draft_changed()
            return
        self.set_layout(entry.layout, entry.frame_steps)

    def _show_details(self, entry: ImportEntry | None) -> None:
        from pyqtgraph.Qt import QtWidgets

        rows: list[tuple[str, str, str]] = []
        if entry is not None:
            files = (
                [("", entry.donor)]
                if entry.acceptor is None
                else [
                    ("Donor file: ", entry.donor),
                    ("Acceptor file: ", entry.acceptor),
                ]
            )
            for prefix, movie in files:
                frames, height, width = movie.shape
                rows += [
                    (f"{prefix}Reader", movie.reader, "file"),
                    (f"{prefix}Shape", f"{frames} frames of {height} x {width} px", "file"),
                    (f"{prefix}Pixel type", movie.pixel_type, "file"),
                ]
                rows += [(f"{prefix}{m.name}", m.value, m.origin) for m in movie.metadata]
        table = self._metadata_table
        table.setRowCount(len(rows))
        for r, (setting, value, origin) in enumerate(rows):
            for c, text in enumerate((value, setting, mi.ORIGIN_TEXT.get(origin, origin))):
                table.setItem(r, c, QtWidgets.QTableWidgetItem(text))

    def _refresh_list(self, current: int | None) -> None:
        self._movie_list.blockSignals(True)
        self._movie_list.clear()
        for entry in self._entries:
            mark = "" if entry.applied else " (not applied)"
            self._movie_list.addItem(f"{entry.name}{mark}")
        self._movie_list.blockSignals(False)
        if current is not None and 0 <= current < len(self._entries):
            self._movie_list.setCurrentRow(current)
        self._show_entry()

    # --- the outputs -----------------------------------------------------------------

    def recipe_requests(self) -> list[Any]:
        """Return one ``extract`` request for each applied single movie, in list order.

        Each project is ``<movie name>.tether`` beside the movie. Raises
        [`RequestRefused`][tether.services.base.RequestRefused] naming a movie pair, which an
        extract step cannot hold yet.
        """
        requests = []
        for entry in self._entries:
            if not entry.applied:
                continue
            path = entry.donor.path
            requests.append(
                mi.extract_request(
                    path,
                    path.with_suffix(".tether"),
                    entry.layout,
                    entry.frame_steps,
                    acceptor_movie=None if entry.acceptor is None else entry.acceptor.path,
                )
            )
        return requests

    def save_recipe(self, path: str | Path) -> Path | None:
        """Write a recipe with one ``extract`` step for each applied movie; return its path."""
        try:
            written = mi.save_recipe(path, self.recipe_requests())
        except ServiceError as exc:
            self._say(f"Recipe not saved: {exc}")
            return None
        self._say(f"Recipe saved: {written.name}")
        return written

    def use_in_extraction(self) -> bool:
        """Give the selected movie and its applied layout to the extraction page."""
        entry = self._current()
        if self._use_movie is None or entry is None or not entry.applied:
            self._say("Not used: select a movie and apply its layout first")
            return False
        if entry.is_pair or entry.frame_steps:
            self._say(
                "Not used: the extraction page cannot take a movie pair or frame steps yet. "
                "Save a recipe instead"
            )
            return False
        self._use_movie(entry.donor.path, entry.layout.profile_texts())
        self._say(f"{entry.name} is in the extraction page")
        return True

    # --- the open project ----------------------------------------------------------

    def set_project(self, path: str | Path | None, project: Project | None) -> None:
        """Show the project at ``path``; ``project`` is its writable session, or ``None``.

        Without a writable session the lists show, and the repair and frame-time actions
        are refused.
        """
        self._project_path = None if path is None else Path(path)
        self._project = project
        self._show_project()

    def _show_project(self) -> None:
        self._sources, self._frame_times = (), ()
        if self._project_path is not None:
            try:
                self._sources = mi.read_project_sources(self._project_path)
                self._frame_times = mi.read_frame_times(self._project_path)
            except ServiceError as exc:
                self._say(f"Project sources not read: {exc}")
        if self._project_path is None:
            note = "Open a project to repair its sources and give a missing frame time."
        elif self._project is None:
            note = (
                f"{self._project_path.name} is read-only here: the lists show, no change is made."
            )
        else:
            missing = sum(1 for s in self._sources if s.missing)
            note = f"{self._project_path.name}: {missing} missing source(s)."
        self._project_note.setText(note)
        _fill(self._source_table, [(s.name, s.role, s.missing or "found") for s in self._sources])
        _fill(
            self._time_table,
            [
                (t.movie_id, f"{t.seconds:g}" if t.seconds > 0 else "unknown", t.state)
                for t in self._frame_times
            ],
        )

    def relink(self, source_id: str, new_path: str | Path) -> bool:
        """Link the source ``source_id`` to ``new_path`` on the worker; return if it started."""
        if self._project is None:
            self._say("Not linked: open the project writable first")
            return False
        if self._refuse_while_busy("Link again"):
            return False
        if self._blocked("Link again"):
            self._say("Not linked: a background task writes the project; wait for it to finish")
            return False
        self._say(f"Reading {Path(new_path).name} to compare its checksum…")
        task = self._tasks.start(
            self._relink_work,
            self._project,
            source_id,
            Path(new_path),
            label="Linking a source",
            on_finished=self._relinked,
            reports_progress=True,
        )
        self._relinking = task is not None
        return task is not None

    @staticmethod
    def _relink_work(project: Project, source_id: str, path: Path, *, progress: Any) -> Any:
        return mi.relink_source(project, source_id, path, progress=progress.report)

    def _relinked(self, outcome: TaskOutcome) -> None:
        from tether.gui.tasks import TaskState

        self._relinking = False
        if outcome.state is TaskState.DONE:
            self._say(f"Linked {outcome.value.name} again: {outcome.value.path.name}")
        else:
            self._say(f"Not linked: {outcome.error}")
        self._show_project()

    def give_frame_time(self, movie_id: str, seconds: float) -> bool:
        """Record ``seconds`` as the frame time of ``movie_id``: from you, not verified."""
        return self._frame_time_action(lambda p: mi.give_frame_time(p, movie_id, seconds))

    def confirm_frame_time(self, movie_id: str) -> bool:
        """Mark the frame time of ``movie_id`` as verified: the separate confirm."""
        return self._frame_time_action(lambda p: mi.confirm_frame_time(p, movie_id))

    def _frame_time_action(self, action: Callable[[Project], mi.FrameTime]) -> bool:
        if self._project is None:
            self._say("Frame time not changed: open the project writable first")
            return False
        if self._blocked("Frame time") or self.writing:
            self._say(
                "Frame time not changed: a background task writes the project; wait for it "
                "to finish"
            )
            return False
        try:
            record = action(self._project)
        except ServiceError as exc:
            self._say(f"Frame time not changed: {exc}")
            return False
        self._show_project()
        self._say(f"Frame time of {record.movie_id}: {record.seconds:g} s, {record.state}")
        return True

    def _selected_time(self) -> str | None:
        row = self._time_table.currentRow()
        return self._frame_times[row].movie_id if 0 <= row < len(self._frame_times) else None

    def _give_clicked(self) -> None:
        movie_id = self._selected_time()
        if movie_id is None:
            self._say("Frame time not changed: select a movie of the project")
            return
        self.give_frame_time(movie_id, float(self._time_field.value()))

    def _confirm_clicked(self) -> None:
        movie_id = self._selected_time()
        if movie_id is None:
            self._say("Frame time not changed: select a movie of the project")
            return
        self.confirm_frame_time(movie_id)

    # --- dialogs ---------------------------------------------------------------------

    def _add_dialog(self) -> None:  # pragma: no cover - interactive file dialog
        from pyqtgraph.Qt import QtWidgets

        from tether.services.movies import movie_file_suffixes

        suffixes = " ".join(f"*{s}" for s in movie_file_suffixes())
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self._window, "Add movies", "", f"Movies ({suffixes});;All files (*)"
        )
        self.add_movies(paths)

    def _recipe_dialog(self) -> None:  # pragma: no cover - interactive file dialog
        from pyqtgraph.Qt import QtWidgets

        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self._window, "Save recipe", "import-recipe.json", _RECIPE_FILTER
        )
        if path:
            self.save_recipe(path)

    def _relink_dialog(self) -> None:  # pragma: no cover - interactive file dialog
        from pyqtgraph.Qt import QtWidgets

        row = self._source_table.currentRow()
        if not 0 <= row < len(self._sources):
            self._say("Not linked: select a source of the project")
            return
        source = self._sources[row]
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self._window, f"Find {source.name}", "", "All files (*)"
        )
        if path:
            self.relink(source.source_id, path)

    # --- helpers ---------------------------------------------------------------------

    def _blocked(self, feature: str) -> bool:
        return self._write_blocked is not None and self._write_blocked(feature)

    def _refuse_while_busy(self, feature: str) -> bool:
        if not self._tasks.busy:
            return False
        self._say(f"{feature} unavailable: wait for the running read to finish")
        return True

    def _say(self, text: str) -> None:
        self._message.setText(text)
        self._status(text)

    def close(self) -> None:
        """Forget a running read or link without applying it, and stop the worker."""
        self._tasks.abandon()
        self._tasks.shutdown()


def _table(headers: Sequence[str], name: str) -> Any:
    from pyqtgraph.Qt import QtWidgets

    table = QtWidgets.QTableWidget(0, len(headers))
    table.setAccessibleName(name)
    table.setHorizontalHeaderLabels(list(headers))
    table.horizontalHeader().setStretchLastSection(True)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
    return table


def _fill(table: Any, rows: Sequence[tuple[str, ...]]) -> None:
    from pyqtgraph.Qt import QtWidgets

    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            table.setItem(r, c, QtWidgets.QTableWidgetItem(text))


def _DropWidget(on_drop: Callable[[list[str]], object]) -> Any:  # noqa: N802 - a widget class
    """Return a ``QWidget`` that takes a drop of local files and passes their paths on."""
    from pyqtgraph.Qt import QtWidgets

    class DropWidget(QtWidgets.QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.setAcceptDrops(True)

        @staticmethod
        def _paths(event: Any) -> list[str]:
            mime = event.mimeData()
            if mime is None or not mime.hasUrls():
                return []
            return [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]

        def dragEnterEvent(self, event: Any) -> None:  # noqa: N802
            if self._paths(event):
                event.acceptProposedAction()

        def dragMoveEvent(self, event: Any) -> None:  # noqa: N802
            if self._paths(event):
                event.acceptProposedAction()

        def dropEvent(self, event: Any) -> None:  # noqa: N802
            paths = self._paths(event)
            if paths:
                event.acceptProposedAction()
                on_drop(paths)

    return DropWidget()

# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The molecule list shows each molecule's state, the progress counts and a filter (#630).

PRD §7.3 and §7.5. Each row of the list in the browser panel shows, after the molecule's
name, its curation state (accepted, rejected or undecided) as a mark and as words, its
category when it has one, and whether an idealization exists and is stale. The mark of
each state has its own shape as well as its own colour, so a state is never shown by
colour alone: in a greyscale picture of the list the shape and the words still differ.

A header above the list counts the accepted, rejected and undecided molecules, for the
movie of the selected molecule and for the project. A filter selects which molecules the
list shows: all, undecided, accepted, rejected, or the molecules of one category. The
filter only hides rows; it never changes the stored order, which PRD §7.5 fixes.

The list is the shell's ``QListWidget``: Qt keeps its rows in one item model and paints
them through the view, so no row is a widget of its own and a list of thousands of rows
stays responsive. [`MoleculeListView`][] is a composition wrapper round it, as the rest of
``tether.gui`` is, so importing this module costs no Qt. The states come from
[`molecule_states`][tether.services.curation.molecule_states], read once when a project
opens; after that the shell updates only the rows that an action changes.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from tether.gui import theme
from tether.services.curation import (
    ACCEPTED,
    CURATION_STATES,
    REJECTED,
    UNDECIDED,
    MoleculeState,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

__all__ = [
    "CATEGORY_FILTER_PREFIX",
    "FILTER_ALL",
    "STATE_SHAPES",
    "STATE_TEXT",
    "STATE_TOKENS",
    "MoleculeListView",
    "StateCounts",
    "count_states",
    "matches_filter",
    "row_text",
    "state_icon",
]

#: The filter that shows every molecule.
FILTER_ALL = "all"
#: The prefix of a category filter's key: ``"category:<name>"``.
CATEGORY_FILTER_PREFIX = "category:"

#: The words of each curation state, as the row and the header show them.
STATE_TEXT = {ACCEPTED: "Accepted", REJECTED: "Rejected", UNDECIDED: "Undecided"}
#: The theme state token that colours the mark of each curation state.
STATE_TOKENS = {ACCEPTED: "accept", REJECTED: "reject", UNDECIDED: "info"}
#: The shape of the mark of each state, so that colour is never the only sign.
STATE_SHAPES = {ACCEPTED: "disc", REJECTED: "square", UNDECIDED: "ring"}

#: The edge of a state mark in pixels; the icon is drawn at twice this for high-DPI screens.
_MARK_PX = 16


@dataclass(frozen=True)
class StateCounts:
    """How many molecules are in each curation state."""

    accepted: int = 0
    rejected: int = 0
    undecided: int = 0

    @property
    def total(self) -> int:
        """All the molecules counted."""
        return self.accepted + self.rejected + self.undecided

    def text(self) -> str:
        """``"2 accepted, 1 rejected, 7 undecided of 10"``."""
        return (
            f"{self.accepted} accepted, {self.rejected} rejected, "
            f"{self.undecided} undecided of {self.total}"
        )


def count_states(states: Iterable[MoleculeState | None]) -> StateCounts:
    """Count the curation states of ``states``; a row with no state is not counted."""
    found = dict.fromkeys(CURATION_STATES, 0)
    for state in states:
        if state is not None:
            found[state.curation] += 1
    return StateCounts(found[ACCEPTED], found[REJECTED], found[UNDECIDED])


def matches_filter(state: MoleculeState | None, key: str) -> bool:
    """Whether a row with ``state`` is shown under the filter ``key``.

    [`FILTER_ALL`][] shows every row. A row with no state (a trace with no project
    store behind it) is shown only by [`FILTER_ALL`][].
    """
    if key == FILTER_ALL:
        return True
    if state is None:
        return False
    if key.startswith(CATEGORY_FILTER_PREFIX):
        return state.category == key[len(CATEGORY_FILTER_PREFIX) :]
    return state.curation == key


def row_text(name: str, state: MoleculeState | None) -> str:
    """Return the text of one row: the name, then the state, the category and the fit mark.

    For example ``"mol-3 · Accepted · category bound · idealized, stale"``. A row with
    no state shows its name only.
    """
    if state is None:
        return name
    parts = [name, STATE_TEXT[state.curation]]
    if state.category:
        parts.append(f"category {state.category}")
    if state.idealized:
        parts.append("idealized, stale" if state.stale else "idealized")
    return " · ".join(parts)


def state_icon(curation: str, active: theme.Theme | None = None) -> Any:
    """Draw the mark of the curation state ``curation`` in the theme ``active`` (a ``QIcon``).

    Accepted is a filled disc, rejected a filled square and undecided a ring, each in the
    colour of its state token ([`STATE_TOKENS`][]).
    """
    from pyqtgraph.Qt import QtCore, QtGui

    active = active or theme.current()
    size = 2 * _MARK_PX
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.GlobalColor.transparent)
    colour = QtGui.QColor(active.state(STATE_TOKENS[curation]))
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    box = QtCore.QRectF(6, 6, size - 12, size - 12)
    shape = STATE_SHAPES[curation]
    if shape == "ring":
        painter.setPen(QtGui.QPen(colour, 4))
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.drawEllipse(box)
    else:
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(colour)
        if shape == "disc":
            painter.drawEllipse(box)
        else:
            painter.drawRect(box)
    painter.end()
    return QtGui.QIcon(pixmap)


class MoleculeListView:
    """The state marks, the progress header and the filter of the shell's molecule list.

    ``list_widget`` is the shell's ``QListWidget``. [`header`][] and [`filter_box`][] are
    made here; the shell puts them above the list. ``movie_name`` maps a ``movie_id`` to
    the name the header shows. ``on_filter_changed`` is called after the user picks a
    filter, so the shell can move the selection off a row the filter hid.
    """

    def __init__(
        self,
        list_widget: Any,
        *,
        movie_name: Callable[[str], str] = str,
        on_filter_changed: Callable[[], None] | None = None,
    ) -> None:
        from pyqtgraph.Qt import QtCore, QtWidgets

        self._list = list_widget
        # One row height for all rows: the view then lays out thousands of rows without
        # measuring each one.
        list_widget.setUniformItemSizes(True)
        self._movie_name = movie_name
        self._on_filter_changed = on_filter_changed
        self._names: list[str] = []
        self._states: list[MoleculeState | None] = []
        self._filter = FILTER_ALL
        self._icons: dict[str, Any] = {}
        self._header = QtWidgets.QLabel()
        self._header.setAccessibleName("Curation progress")
        self._header.setWordWrap(True)
        self._header.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self._filter_box = QtWidgets.QComboBox()
        self._filter_box.setAccessibleName("Show molecules")
        self._filter_box.setToolTip("Show only the molecules in one state or one category")
        self._filter_box.currentIndexChanged.connect(self._on_filter_index)
        self._sync_filter_options()
        theme.subscribe(self._on_theme)

    def close(self) -> None:
        """Stop following the theme."""
        theme.unsubscribe(self._on_theme)

    @property
    def header(self) -> Any:
        """The ``QLabel`` with the counts for the current movie and for the project."""
        return self._header

    @property
    def filter_box(self) -> Any:
        """The ``QComboBox`` that selects which molecules the list shows."""
        return self._filter_box

    @property
    def filter_key(self) -> str:
        """The key of the current filter: [`FILTER_ALL`][], a state, or a category filter."""
        return self._filter

    def filter_keys(self) -> list[str]:
        """Return the keys the filter box offers, in its order."""
        return [self._filter_box.itemData(i) for i in range(self._filter_box.count())]

    def state(self, row: int) -> MoleculeState | None:
        """Return the state of list row ``row``; ``None`` for a row with no project store."""
        return self._states[row] if 0 <= row < len(self._states) else None

    @property
    def states(self) -> tuple[MoleculeState | None, ...]:
        """The state of every row, in list order."""
        return tuple(self._states)

    # --- filling and updating ------------------------------------------------

    def populate(self, names: Sequence[str], states: Sequence[MoleculeState | None]) -> None:
        """Fill the list with one row for each of ``names``, marked with ``states``.

        ``states`` is in the same order as ``names``; a shorter sequence leaves the
        remaining rows without a state.
        """
        from pyqtgraph.Qt import QtWidgets

        self._names = list(names)
        self._states = [states[i] if i < len(states) else None for i in range(len(names))]
        self._list.clear()
        for name, state in zip(self._names, self._states, strict=True):
            item = QtWidgets.QListWidgetItem(row_text(name, state))
            self._mark(item, state)
            self._list.addItem(item)
        self._sync_filter_options()
        self._apply_filter()

    def update_rows(self, rows: Iterable[int], **changes: Any) -> None:
        """Change the fields ``changes`` of the states of ``rows`` and show them.

        A row with no state is left as it is. Only those rows are written, and the
        filter is applied to them again.
        """
        changed = False
        for row in rows:
            state = self.state(row)
            if state is None:
                continue
            self._states[row] = replace(state, **changes)
            self._show_row(row)
            changed = True
        if changed and "category" in changes:
            self._sync_filter_options()

    def replace_states(self, states: Sequence[MoleculeState | None]) -> None:
        """Take a fresh read of every row's state; only rows that changed are written."""
        changed_category = False
        for row in range(len(self._states)):
            new = states[row] if row < len(states) else None
            old = self._states[row]
            if new == old:
                continue
            changed_category |= (old and old.category) != (new and new.category)
            self._states[row] = new
            self._show_row(row)
        if changed_category:
            self._sync_filter_options()

    def rows_of_key(self, molecule_key: str) -> list[int]:
        """Every list row whose state carries ``molecule_key`` (a key can name several rows)."""
        return [
            row
            for row, state in enumerate(self._states)
            if state is not None and state.molecule_key == molecule_key
        ]

    def _show_row(self, row: int) -> None:
        item = self._list.item(row)
        state = self._states[row]
        item.setText(row_text(self._names[row], state))
        self._mark(item, state)
        self._list.setRowHidden(row, not matches_filter(state, self._filter))

    def _mark(self, item: Any, state: MoleculeState | None) -> None:
        from pyqtgraph.Qt import QtGui

        if state is None:
            item.setIcon(QtGui.QIcon())
            return
        icon = self._icons.get(state.curation)
        if icon is None:
            icon = self._icons[state.curation] = state_icon(state.curation)
        item.setIcon(icon)

    def _on_theme(self, active: theme.Theme) -> None:
        self._icons = {curation: state_icon(curation, active) for curation in CURATION_STATES}
        for row, state in enumerate(self._states):
            if state is not None:
                self._list.item(row).setIcon(self._icons[state.curation])

    # --- the filter ----------------------------------------------------------

    def set_filter(self, key: str) -> None:
        """Show only the rows that the filter ``key`` matches (one of [`filter_keys`][])."""
        index = self._filter_box.findData(key)
        if index < 0:
            raise ValueError(f"unknown molecule filter {key!r}; offered: {self.filter_keys()}")
        self._filter_box.setCurrentIndex(index)

    def _sync_filter_options(self) -> None:
        """Offer the state filters and one filter for each category the rows carry."""
        categories = sorted({s.category for s in self._states if s is not None and s.category})
        options = [(FILTER_ALL, "All molecules")] + [
            (curation, STATE_TEXT[curation]) for curation in (UNDECIDED, ACCEPTED, REJECTED)
        ]
        options += [(f"{CATEGORY_FILTER_PREFIX}{c}", f"Category: {c}") for c in categories]
        box = self._filter_box
        box.blockSignals(True)
        box.clear()
        for key, text in options:
            box.addItem(text, key)
        index = box.findData(self._filter)
        box.setCurrentIndex(max(index, 0))
        box.blockSignals(False)
        if index < 0:
            # The category of the filter is gone (its last molecule changed): show all.
            self._filter = FILTER_ALL
            self._apply_filter()

    def _on_filter_index(self, index: int) -> None:
        self._filter = self._filter_box.itemData(index) or FILTER_ALL
        self._apply_filter()
        if self._on_filter_changed is not None:
            self._on_filter_changed()

    def _apply_filter(self) -> None:
        for row, state in enumerate(self._states):
            self._list.setRowHidden(row, not matches_filter(state, self._filter))

    # --- navigation ----------------------------------------------------------

    def shown(self, row: int) -> bool:
        """Whether list row ``row`` exists and the filter shows it."""
        return 0 <= row < len(self._states) and not self._list.isRowHidden(row)

    def shown_from(self, row: int, step: int) -> int | None:
        """Return the first shown row from ``row + step`` on, by ``step``; ``None`` if none."""
        nxt = row + step
        while 0 <= nxt < len(self._states):
            if not self._list.isRowHidden(nxt):
                return nxt
            nxt += step
        return None

    def next_undecided(self, row: int) -> tuple[int | None, bool]:
        """Find the next shown undecided row after ``row`` in list order, and if it wrapped.

        The search goes to the end of the list, then from the top up to ``row``. It
        returns ``(None, False)`` when no shown row is undecided.
        """
        count = len(self._states)
        for offset in range(1, count + 1):
            candidate = (row + offset) % count if row >= 0 else offset - 1
            state = self._states[candidate]
            if (
                state is not None
                and state.curation == UNDECIDED
                and not self._list.isRowHidden(candidate)
            ):
                return candidate, row >= 0 and candidate <= row
        return None, False

    # --- the header ----------------------------------------------------------

    def header_text(self, current_row: int) -> str:
        """Return the counts for the movie of ``current_row`` and for the project.

        Empty when no row has a state (no project store is open).
        """
        if all(state is None for state in self._states):
            return ""
        lines = []
        current = self.state(current_row)
        if current is not None:
            movie = count_states(s for s in self._states if s and s.movie_id == current.movie_id)
            lines.append(f"Movie {self._movie_name(current.movie_id)}: {movie.text()}")
        lines.append(f"Project: {count_states(self._states).text()}")
        return "\n".join(lines)

    def sync_header(self, current_row: int) -> None:
        """Show [`header_text`][] for ``current_row`` in the header."""
        text = self.header_text(current_row)
        self._header.setText(text)
        self._header.setVisible(bool(text))

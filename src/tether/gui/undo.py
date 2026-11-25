# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The undo and redo stack of the curation actions (#632, PRD §7.3).

**Edit → Undo** (``Ctrl+Z``) takes back the last curation action of this session and
**Edit → Redo** (``Ctrl+Shift+Z``, also ``Ctrl+Y`` on Windows) does it again. The actions
are accept, reject, un-reject, category assign and clear, the analysis-window start and
end, the window reset and the photobleach edit.

Each [`UndoEntry`][] holds the molecule an action changed and its stored state before and
after the action. An undo writes the *before* state back and a redo the *after* state,
each as a new write: the ``/labels`` log and the molecule edit log are append-only
(PRD §5.1), so an undo adds the rows that restore the earlier state and never deletes
one. The shell does the writes; this module only keeps the order.

The stack belongs to the session and to the open project. The shell empties it when the
project closes, when another project opens and when the project becomes read-only. It
is never stored in the project file. Like [`tether.gui.curation`][], it needs no Qt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

__all__ = [
    "CATEGORY",
    "CURATION",
    "WINDOW",
    "UndoEntry",
    "UndoStack",
    "WindowState",
]

#: An accept, reject or un-reject: the state is a
#: [`CurationSnapshot`][tether.services.curation.CurationSnapshot].
CURATION = "curation"
#: A category assign or clear: the state is each row's ``(molecule_id, category)``
#: (``""`` for none), since rows that share a molecule can differ.
CATEGORY = "category"
#: An analysis-window edit: the state is a [`WindowState`][].
WINDOW = "window"

EntryKind = Literal["curation", "category", "window"]


@dataclass(frozen=True)
class WindowState:
    """The analysis window of a molecule, as an undo writes it back.

    Attributes
    ----------
    window
        The absolute ``(start, stop)`` window of the row the action started from.
    whole
        Whether every row of the molecule had its whole ``frame_range`` as its window.
        Such a state is written back with a reset, so each row takes its own range and
        a later photobleach pass treats the window as not narrowed again.
    rows
        Each row's ``(molecule_id, (start, stop))``. Two rows can share a molecule with
        different frame ranges and windows, so any other state is written back row by
        row.
    """

    window: tuple[int, int]
    whole: bool
    rows: tuple[tuple[str, tuple[int, int]], ...] = ()


@dataclass(frozen=True)
class UndoEntry:
    """One curation action the stack can take back and do again.

    Attributes
    ----------
    kind
        [`CURATION`][], [`CATEGORY`][] or [`WINDOW`][]: which field the action wrote.
    molecule_key
        The molecule the action changed.
    row
        The list row the action was made on. An undo selects it again.
    description
        What the action did, for the Edit menu: ``"reject mol-0412"`` gives
        **Undo reject mol-0412**.
    before, after
        The stored state before and after the action, in the form ``kind`` names.
    """

    kind: EntryKind
    molecule_key: str
    row: int
    description: str
    before: Any
    after: Any


class UndoStack:
    """The undo and redo lists of one session's curation actions (newest last).

    [`push`][] records a new action and empties the redo list, as every editor does.
    [`undo_entry`][] and [`redo_entry`][] name what an undo or redo would act on; the
    shell writes the state and then calls [`undone`][] or [`redone`][] to move the
    entry across. [`discard`][] drops an entry whose write failed, so one broken step
    cannot block the steps before it, and [`forget`][] drops a molecule's entries once
    its stored state no longer follows from them.
    """

    def __init__(self) -> None:
        self._undo: list[UndoEntry] = []
        self._redo: list[UndoEntry] = []

    @property
    def undo_entry(self) -> UndoEntry | None:
        """The entry an undo writes back now, or ``None`` when there is none."""
        return self._undo[-1] if self._undo else None

    @property
    def redo_entry(self) -> UndoEntry | None:
        """The entry a redo writes again now, or ``None`` when there is none."""
        return self._redo[-1] if self._redo else None

    @property
    def undo_count(self) -> int:
        """How many actions an undo can take back, one at a time."""
        return len(self._undo)

    @property
    def redo_count(self) -> int:
        """How many undone actions a redo can do again, one at a time."""
        return len(self._redo)

    def push(self, entry: UndoEntry) -> None:
        """Record a new action; the actions undone before it can no longer be redone."""
        self._undo.append(entry)
        self._redo.clear()

    def undone(self) -> UndoEntry:
        """Move the [`undo_entry`][] to the redo list once its *before* state is written."""
        entry = self._undo.pop()
        self._redo.append(entry)
        return entry

    def redone(self) -> UndoEntry:
        """Move the [`redo_entry`][] to the undo list once its *after* state is written."""
        entry = self._redo.pop()
        self._undo.append(entry)
        return entry

    def discard(self, *, redo: bool = False) -> UndoEntry:
        """Drop the [`undo_entry`][] (or the [`redo_entry`][]) after its write failed."""
        return (self._redo if redo else self._undo).pop()

    def clear_redo(self) -> None:
        """Empty the redo list: a stored action that cannot be undone came after it."""
        self._redo.clear()

    def forget(self, molecule_key: str) -> int:
        """Drop every entry of ``molecule_key`` from both lists; return how many.

        The molecule's stored state no longer follows from its entries (a write failed
        part way, a state could not be read, or an edit the lists do not hold changed
        it), so replaying one would take back a change unseen. Entries of other
        molecules stay: an action writes the rows of one molecule only.
        """
        undo = [e for e in self._undo if e.molecule_key != molecule_key]
        redo = [e for e in self._redo if e.molecule_key != molecule_key]
        dropped = len(self._undo) + len(self._redo) - len(undo) - len(redo)
        self._undo[:], self._redo[:] = undo, redo
        return dropped

    def clear(self) -> None:
        """Empty both lists: the project closed, another opened, or it became read-only."""
        self._undo.clear()
        self._redo.clear()

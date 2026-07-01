# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Curation keymap + controller + the application-level focus contract (PRD §7.3).

The trace dock (:mod:`tether.gui.trace_dock`) is a keyboard-driven surface stepped
at ≈1–2 s/trace. Its four **bare** curation keys — ``Space`` (accept),
``Backspace``/``Delete`` (reject), ``Enter`` (jump to the movie spot) — collide
with the default Qt bindings of whatever child widget holds focus (a molecule
list's ``Space`` toggles a checkbox, ``Enter`` edits/activates a row, ``Delete``
removes; the napari canvas has its own map). PRD §7.3 therefore mandates an
**application-level event filter** that delivers the curation keys to the
controller **regardless of which child widget holds focus**, suppressing the
conflicting native bindings on those non-text surfaces — **except** a focused
text-entry widget (notably the editable category field, §7.6) is exempted so the
keys keep their text semantics there.

This module is split so the pure decision logic stays importable and testable
without Qt (mirroring :mod:`tether.gui.trace_dock`):

* :class:`CurationAction` / :class:`Command` — the vocabulary of curation actions.
* :class:`Keymap` — the key-chord → :class:`Command` table: the tMAVEN-inherited
  bindings (``←``/``→`` prev/next with ``↑``/``↓`` aliases, ``1``–``9`` categories,
  ``0`` clears to *uncategorized*, ``-``/``=`` window start, ``[``/``]`` window
  end, ``R``/``P``/``G``) plus the Tether-only ``Space``/``Backspace``/``Delete``/
  ``Enter``/``I`` and the reserved ``C``/``V`` no-ops. Rebindable and
  JSON-persistable; renders a cheat-sheet.
* :class:`CurationController` — routes a :class:`Command` to injected handler
  callbacks (later sessions wire real ``/labels`` writes, idealize, camera jumps;
  M2 S2 records the dispatch for tests) and keeps the integer↔category contract
  (tMAVEN class ``0`` ↔ Tether *uncategorized*, ``≥ 1`` ↔ named categories).
* :class:`CurationEventFilter` — the ``QObject`` installed on the ``QApplication``
  that implements the focus contract. Qt is imported lazily in ``__init__`` so
  importing this module costs no Qt.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pyqtgraph.Qt import QtCore, QtWidgets

__all__ = [
    "CATEGORY_SLOTS",
    "UNCATEGORIZED_CLASS",
    "Command",
    "CurationAction",
    "CurationController",
    "CurationEventFilter",
    "CurationHandlers",
    "Keymap",
    "action_description",
    "default_keymap_path",
    "is_text_entry",
]

# --- vocabulary --------------------------------------------------------------

#: tMAVEN class 0 is pinned to Tether's *uncategorized* null state (PRD §7.3 /
#: §7.4): ``Space`` alone yields an accepted-but-uncategorized trace, and ``0``
#: clears any named category back to it. Named categories are classes ``>= 1``.
UNCATEGORIZED_CLASS = 0

#: The number of categories addressable by the ``1``–``9`` hotkeys; categories
#: beyond this are assigned via the overflow picker (exercised fully once the M4
#: editable per-condition list lands).
CATEGORY_SLOTS = 9


class CurationAction(StrEnum):
    """A single curation action the keymap can dispatch to the controller.

    A :class:`~enum.StrEnum` so an action round-trips through JSON as its value
    (the keymap is user-persistable) and reads cleanly in logs/tests.
    """

    ACCEPT = "accept"  # Tether-only: Space
    REJECT = "reject"  # Tether-only: Backspace / Delete
    JUMP = "jump"  # Tether-only: Enter — round-trip to the movie spot
    IDEALIZE = "idealize"  # Tether-only: I — one-click vbFRET (wired at M2 S6)
    NEXT = "next"  # inherited: Right / Down
    PREV = "prev"  # inherited: Left / Up
    ASSIGN_CATEGORY = "assign_category"  # inherited: 1-9 (arg = integer class)
    CLEAR_CATEGORY = "clear_category"  # inherited: 0 -> uncategorized
    WINDOW_START_DEC = "window_start_dec"  # inherited: - (pre_list)
    WINDOW_START_INC = "window_start_inc"  # inherited: =
    WINDOW_END_DEC = "window_end_dec"  # inherited: [ (post_list)
    WINDOW_END_INC = "window_end_inc"  # inherited: ]
    RESET_WINDOW = "reset_window"  # inherited: R
    PHOTOBLEACH = "photobleach"  # inherited: P
    GRID = "grid"  # inherited: G
    RESERVED = "reserved"  # C / V — tMAVEN split/collect, inert no-ops in v1


@dataclass(frozen=True)
class Command:
    """A resolved curation command: an :class:`CurationAction` and optional arg.

    ``arg`` carries the integer class for :attr:`CurationAction.ASSIGN_CATEGORY`
    (slot key ``n`` → class ``n``; the overflow picker → the chosen class, which
    may exceed :data:`CATEGORY_SLOTS`); it is ``None`` for every other action.

    The contract is enforced at construction (and therefore on JSON load): an
    ``ASSIGN_CATEGORY`` **must** carry an integer class ``>= 1`` — class ``0`` is
    reserved for the *uncategorized* null state (:attr:`CurationAction.CLEAR_CATEGORY`)
    — and every other action must carry no arg. This keeps an out-of-range or
    ``0`` class from ever reaching a handler as a named-category assignment.
    """

    action: CurationAction
    arg: int | None = None

    def __post_init__(self) -> None:
        if self.action is CurationAction.ASSIGN_CATEGORY:
            # bool is an int subclass; reject it explicitly so True/False can't pose
            # as a class. Class 0 is CLEAR_CATEGORY/uncategorized, never ASSIGN.
            if type(self.arg) is not int or self.arg < 1:
                raise ValueError(
                    "ASSIGN_CATEGORY requires an integer class >= 1 "
                    f"(0 is CLEAR_CATEGORY/uncategorized), got {self.arg!r}"
                )
        elif self.arg is not None:
            raise ValueError(f"{self.action.value} takes no arg, got {self.arg!r}")


def action_description(command: Command) -> str:
    """A human-readable description of ``command`` for the cheat-sheet."""
    a = command.action
    if a is CurationAction.ASSIGN_CATEGORY:
        return f"Assign category {command.arg}"
    return _ACTION_TEXT[a]


_ACTION_TEXT: dict[CurationAction, str] = {
    CurationAction.ACCEPT: "Accept trace",
    CurationAction.REJECT: "Reject trace",
    CurationAction.JUMP: "Jump to movie spot",
    CurationAction.IDEALIZE: "One-click idealize",
    CurationAction.NEXT: "Next trace",
    CurationAction.PREV: "Previous trace",
    CurationAction.CLEAR_CATEGORY: "Clear category (uncategorized)",
    CurationAction.WINDOW_START_DEC: "Analysis-window start −1",
    CurationAction.WINDOW_START_INC: "Analysis-window start +1",
    CurationAction.WINDOW_END_DEC: "Analysis-window end −1",
    CurationAction.WINDOW_END_INC: "Analysis-window end +1",
    CurationAction.RESET_WINDOW: "Reset analysis window",
    CurationAction.PHOTOBLEACH: "Detect photobleach",
    CurationAction.GRID: "Toggle grid",
    CurationAction.RESERVED: "Reserved (no-op)",
}


# --- handlers + controller (Qt-free) -----------------------------------------


@dataclass
class CurationHandlers:
    """Injected callbacks the controller invokes for each dispatched command.

    Every hook is optional; an unset hook makes its action a silent no-op at M2
    S2 (the curation/labels writer, one-click idealize, and the camera jump wire
    their real backends at M2 S5/S6/S4). ``assign_category`` receives the integer
    class; ``window_start``/``window_end`` receive the ``±1`` nudge delta.
    """

    accept: Callable[[], Any] | None = None
    reject: Callable[[], Any] | None = None
    jump: Callable[[], Any] | None = None
    idealize: Callable[[], Any] | None = None
    next: Callable[[], Any] | None = None
    prev: Callable[[], Any] | None = None
    assign_category: Callable[[int], Any] | None = None
    clear_category: Callable[[], Any] | None = None
    window_start: Callable[[int], Any] | None = None
    window_end: Callable[[int], Any] | None = None
    reset_window: Callable[[], Any] | None = None
    photobleach: Callable[[], Any] | None = None
    grid: Callable[[], Any] | None = None


class CurationController:
    """Routes a :class:`Command` to its :class:`CurationHandlers` callback.

    Keeps a :attr:`history` of every dispatched command (and :attr:`last`) so the
    focus contract can be asserted at the controller level without pixels. The
    routing is pure Python — no Qt — so it is exercised in the default matrix.
    """

    def __init__(self, handlers: CurationHandlers | None = None) -> None:
        self._handlers = handlers or CurationHandlers()
        self._history: list[Command] = []

    @property
    def handlers(self) -> CurationHandlers:
        return self._handlers

    @property
    def history(self) -> list[Command]:
        """Every command dispatched so far, in order (newest last)."""
        return self._history

    @property
    def last(self) -> Command | None:
        """The most recently dispatched command, or ``None``."""
        return self._history[-1] if self._history else None

    def dispatch(self, command: Command) -> None:
        """Record ``command`` and invoke its handler (if any)."""
        self._history.append(command)
        h = self._handlers
        a = command.action
        if a is CurationAction.ACCEPT:
            _call(h.accept)
        elif a is CurationAction.REJECT:
            _call(h.reject)
        elif a is CurationAction.JUMP:
            _call(h.jump)
        elif a is CurationAction.IDEALIZE:
            _call(h.idealize)
        elif a is CurationAction.NEXT:
            _call(h.next)
        elif a is CurationAction.PREV:
            _call(h.prev)
        elif a is CurationAction.ASSIGN_CATEGORY:
            _call(h.assign_category, command.arg)
        elif a is CurationAction.CLEAR_CATEGORY:
            _call(h.clear_category)
        elif a is CurationAction.WINDOW_START_DEC:
            _call(h.window_start, -1)
        elif a is CurationAction.WINDOW_START_INC:
            _call(h.window_start, +1)
        elif a is CurationAction.WINDOW_END_DEC:
            _call(h.window_end, -1)
        elif a is CurationAction.WINDOW_END_INC:
            _call(h.window_end, +1)
        elif a is CurationAction.RESET_WINDOW:
            _call(h.reset_window)
        elif a is CurationAction.PHOTOBLEACH:
            _call(h.photobleach)
        elif a is CurationAction.GRID:
            _call(h.grid)
        # RESERVED (C / V): recorded above, intentionally inert (PRD §7.3).


def _call(fn: Callable[..., Any] | None, *args: Any) -> None:
    if fn is not None:
        fn(*args)


# --- keymap ------------------------------------------------------------------

# A key-chord is (key_code, significant_modifiers) as plain ints, so the table is
# Qt-free once built and serializes to JSON directly.
KeyChord = tuple[int, int]


def _as_int(value: Any) -> int:
    """Coerce a PySide6 enum/flag (or a plain int) to its integer value.

    ``QKeyEvent.key()`` already returns an ``int``; ``Qt.Key`` members are
    int-convertible. But PySide6 6.x ``Qt.KeyboardModifier`` **flags** are *not*
    directly ``int()``-convertible (that raises ``TypeError``) — their integer is
    on ``.value``. Prefer ``.value`` when present so the keymap works uniformly
    for keys, modifiers, and raw ints without depending on enum identity.
    """
    if isinstance(value, int):
        return value
    inner = getattr(value, "value", None)
    return int(inner) if inner is not None else int(value)


@dataclass
class Keymap:
    """The key-chord → :class:`Command` table (rebindable, JSON-persistable).

    Construct empty (``Keymap()`` — Qt-free) or via :meth:`default` (needs Qt for
    the ``Qt.Key`` constants). :meth:`command_for` resolves an event's
    ``(key, modifiers)``; :meth:`rebind` moves an action to a new chord (leaving
    the old chord unbound, i.e. a controller-level no-op); :meth:`cheatsheet`
    renders the current bindings for the shipped help overlay.
    """

    bindings: dict[KeyChord, Command] = field(default_factory=dict)

    # --- lookup / mutation ---------------------------------------------------

    def command_for(self, key: int, modifiers: int = 0) -> Command | None:
        """The :class:`Command` bound to ``(key, modifiers)``, or ``None``."""
        return self.bindings.get((_as_int(key), _as_int(modifiers)))

    def chords_for(self, command: Command) -> list[KeyChord]:
        """Every chord currently bound to ``command`` (order unspecified)."""
        return [chord for chord, cmd in self.bindings.items() if cmd == command]

    def bind(self, key: int, command: Command, modifiers: int = 0) -> None:
        """Bind ``(key, modifiers)`` → ``command`` (overwriting any prior chord)."""
        self.bindings[(_as_int(key), _as_int(modifiers))] = command

    def rebind(self, command: Command, key: int, modifiers: int = 0) -> None:
        """Move ``command`` to ``(key, modifiers)``, unbinding its former chord(s).

        The former default chord becomes unbound, so it no longer dispatches the
        action (a controller-level no-op) while the new chord fires it.
        """
        self.bindings = {chord: cmd for chord, cmd in self.bindings.items() if cmd != command}
        self.bind(key, command, modifiers)

    def restore_defaults(self) -> None:
        """Reset every binding to the :meth:`default` table."""
        self.bindings = Keymap.default().bindings

    # --- construction --------------------------------------------------------

    @classmethod
    def default(cls) -> Keymap:
        """The default keymap: tMAVEN-inherited bindings + the Tether-only keys.

        Mirrors tMAVEN's ``main_window`` key handler for muscle-memory continuity
        (PRD §7.3): ``←``/``→`` (``↑``/``↓`` alias) prev/next; ``1``–``9`` assign
        the first nine categories and ``0`` clears to *uncategorized*; ``-``/``=``
        nudge the analysis-window start, ``[``/``]`` the end; ``R`` resets the
        window, ``P`` detects photobleach, ``G`` toggles the grid. The Tether-only
        ``Space``/``Backspace``/``Delete``/``Enter``/``I`` and the reserved
        ``C``/``V`` no-ops complete the map. All defaults are bare (no modifier).
        """
        from pyqtgraph.Qt import QtCore

        k = QtCore.Qt.Key
        no_mod = 0
        km = cls()

        def bind(key: Any, command: Command) -> None:
            km.bindings[(_as_int(key), no_mod)] = command

        # Tether-only (the four bare curation keys + idealize).
        bind(k.Key_Space, Command(CurationAction.ACCEPT))
        bind(k.Key_Backspace, Command(CurationAction.REJECT))
        bind(k.Key_Delete, Command(CurationAction.REJECT))
        bind(k.Key_Return, Command(CurationAction.JUMP))  # main Enter
        bind(k.Key_Enter, Command(CurationAction.JUMP))  # numpad Enter
        bind(k.Key_I, Command(CurationAction.IDEALIZE))

        # Inherited navigation (arrows + up/down aliases).
        bind(k.Key_Left, Command(CurationAction.PREV))
        bind(k.Key_Up, Command(CurationAction.PREV))
        bind(k.Key_Right, Command(CurationAction.NEXT))
        bind(k.Key_Down, Command(CurationAction.NEXT))

        # Inherited categories: 1-9 assign classes 1-9; 0 clears to uncategorized.
        for slot, key in enumerate(
            (k.Key_1, k.Key_2, k.Key_3, k.Key_4, k.Key_5, k.Key_6, k.Key_7, k.Key_8, k.Key_9),
            start=1,
        ):
            bind(key, Command(CurationAction.ASSIGN_CATEGORY, slot))
        bind(k.Key_0, Command(CurationAction.CLEAR_CATEGORY))

        # Inherited analysis-window nudges (distinct start/end bounds).
        bind(k.Key_Minus, Command(CurationAction.WINDOW_START_DEC))
        bind(k.Key_Equal, Command(CurationAction.WINDOW_START_INC))
        bind(k.Key_BracketLeft, Command(CurationAction.WINDOW_END_DEC))
        bind(k.Key_BracketRight, Command(CurationAction.WINDOW_END_INC))

        # Inherited single-key actions.
        bind(k.Key_R, Command(CurationAction.RESET_WINDOW))
        bind(k.Key_P, Command(CurationAction.PHOTOBLEACH))
        bind(k.Key_G, Command(CurationAction.GRID))

        # Reserved no-ops (tMAVEN split/collect have no Tether analog in v1).
        bind(k.Key_C, Command(CurationAction.RESERVED))
        bind(k.Key_V, Command(CurationAction.RESERVED))

        return km

    # --- persistence ---------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serialisable representation of the current bindings."""
        return {
            "bindings": [
                {"key": key, "modifiers": mods, "action": cmd.action.value, "arg": cmd.arg}
                for (key, mods), cmd in sorted(self.bindings.items())
            ]
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Keymap:
        """Rebuild a keymap from :meth:`to_dict` output."""
        km = cls()
        for row in data.get("bindings", []):
            cmd = Command(CurationAction(row["action"]), row.get("arg"))
            km.bindings[(int(row["key"]), int(row["modifiers"]))] = cmd
        return km

    def save(self, path: str | Path) -> None:
        """Persist the keymap to ``path`` as JSON (creating parent dirs)."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> Keymap:
        """Load a keymap saved by :meth:`save`."""
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    # --- cheat-sheet ---------------------------------------------------------

    def cheatsheet(self) -> list[tuple[str, str]]:
        """Ordered ``(key_label, description)`` rows describing the current map.

        The shipped help overlay renders these; a test asserts a rebound key is
        reflected here. Key labels come from ``QKeySequence`` (native text).
        """
        from pyqtgraph.Qt import QtGui

        rows: list[tuple[int, int, int, str, str]] = []
        native = QtGui.QKeySequence.SequenceFormat.NativeText
        for (key, mods), cmd in self.bindings.items():
            label = QtGui.QKeySequence(key | mods).toString(native)
            priority = _ACTION_ORDER.get(cmd.action, len(_ACTION_ORDER))
            rows.append((priority, mods, key, label, action_description(cmd)))
        rows.sort()
        return [(label, desc) for _, _, _, label, desc in rows]


_ACTION_ORDER: dict[CurationAction, int] = {
    action: i
    for i, action in enumerate(
        (
            CurationAction.ACCEPT,
            CurationAction.REJECT,
            CurationAction.JUMP,
            CurationAction.IDEALIZE,
            CurationAction.PREV,
            CurationAction.NEXT,
            CurationAction.ASSIGN_CATEGORY,
            CurationAction.CLEAR_CATEGORY,
            CurationAction.WINDOW_START_DEC,
            CurationAction.WINDOW_START_INC,
            CurationAction.WINDOW_END_DEC,
            CurationAction.WINDOW_END_INC,
            CurationAction.RESET_WINDOW,
            CurationAction.PHOTOBLEACH,
            CurationAction.GRID,
            CurationAction.RESERVED,
        )
    )
}


# --- the application-level focus contract (lazy Qt) --------------------------

#: Widget classes whose focus exempts them from the curation keymap: text goes
#: to them verbatim (PRD §7.3 — the editable category field, §7.6, keeps
#: ``Space``/``Backspace``/``Delete`` as text). A widget may also opt in with the
#: dynamic property ``tetherTextEntry = True`` (for custom editors).
_TEXT_ENTRY_PROPERTY = "tetherTextEntry"


def is_text_entry(widget: Any) -> bool:
    """Whether ``widget`` is a focused text editor exempt from the keymap."""
    if widget is None:
        return False
    try:
        if bool(widget.property(_TEXT_ENTRY_PROPERTY)):
            return True
    except (RuntimeError, AttributeError):
        # A deleted C++ object (RuntimeError) or a non-QObject watched target.
        return False
    from pyqtgraph.Qt import QtWidgets

    return isinstance(
        widget,
        (
            QtWidgets.QLineEdit,
            QtWidgets.QTextEdit,
            QtWidgets.QPlainTextEdit,
            QtWidgets.QAbstractSpinBox,
        ),
    )


_UNSET = object()  # sentinel: "look up the focused widget" vs. an injected one


class CurationEventFilter:
    """The ``QApplication`` event filter implementing the §7.3 focus contract.

    Composes a lazily-created ``QObject`` (so importing this module needs no Qt,
    matching :mod:`tether.gui.trace_dock`). On a ``KeyPress`` for a chord in the
    keymap, and only when the **focused** widget is not a text editor, it
    dispatches the :class:`Command` to the controller and **consumes** the event
    (returning ``True``) so the native list/canvas binding never fires; the
    matching ``KeyRelease`` is consumed too. After a ``JUMP`` (camera round-trip)
    focus is returned to the registered dock widget, mirroring tMAVEN.

    The exemption is keyed on the **focused** widget, not merely the event's
    target: a focused ``QLineEdit`` that *ignores* a key (e.g. ``Enter``, which it
    leaves for a default button) lets Qt propagate the event up to a non-text
    parent, and an app-level filter is re-invoked for each parent. Checking the
    focus widget (which stays the text field throughout propagation) keeps the
    category field fully exempt, so editing text never fires a curation action.

    Install with :meth:`install` (defaults to the running ``QApplication``) and
    tear down with :meth:`remove` — important under pytest-qt, whose
    ``QApplication`` is shared across the session.
    """

    def __init__(
        self,
        controller: CurationController,
        keymap: Keymap | None = None,
        *,
        focus_dock: QtWidgets.QWidget | None = None,
    ) -> None:
        from pyqtgraph.Qt import QtCore, QtWidgets

        self._controller = controller
        self._keymap = keymap if keymap is not None else Keymap.default()
        self._focus_dock = focus_dock
        self._app: QtCore.QCoreApplication | None = None
        self._qtwidgets = QtWidgets  # cached for the per-event focus lookup
        # Key codes whose KeyPress this filter consumed, so the paired KeyRelease
        # is consumed iff its press was — keeping press/release symmetric even if
        # focus/exemption changes mid-chord (rather than re-deriving on release).
        self._consumed_press_keys: set[int] = set()

        mod = QtCore.Qt.KeyboardModifier
        self._significant_mask = (
            _as_int(mod.ShiftModifier)
            | _as_int(mod.ControlModifier)
            | _as_int(mod.AltModifier)
            | _as_int(mod.MetaModifier)
        )
        self._key_press = QtCore.QEvent.Type.KeyPress
        self._key_release = QtCore.QEvent.Type.KeyRelease

        # Lazily define the QObject subclass so the module stays Qt-free to import.
        outer = self

        class _Filter(QtCore.QObject):
            def eventFilter(self, watched: Any, event: Any) -> bool:  # noqa: N802 (Qt override)
                return outer.filter_event(watched, event)

        self._qobject = _Filter()

    # --- accessors -----------------------------------------------------------

    @property
    def controller(self) -> CurationController:
        return self._controller

    @property
    def keymap(self) -> Keymap:
        return self._keymap

    @property
    def qobject(self) -> QtCore.QObject:
        """The underlying ``QObject`` (what ``installEventFilter`` receives)."""
        return self._qobject

    def set_focus_dock(self, widget: QtWidgets.QWidget | None) -> None:
        """Register the dock to refocus after a ``JUMP`` (camera round-trip)."""
        self._focus_dock = widget

    # --- install / remove ----------------------------------------------------

    def install(self, app: QtCore.QCoreApplication | None = None) -> None:
        """Install on ``app`` (default: the running ``QApplication``)."""
        from pyqtgraph.Qt import QtWidgets

        target = app if app is not None else QtWidgets.QApplication.instance()
        if target is None:  # pragma: no cover - defensive; a shell always has one
            raise RuntimeError("no QApplication to install the curation event filter on")
        target.installEventFilter(self._qobject)
        self._app = target

    def remove(self) -> None:
        """Remove the filter from the app it was installed on (idempotent)."""
        if self._app is not None:
            self._app.removeEventFilter(self._qobject)
            self._app = None

    # --- the filter ----------------------------------------------------------

    def filter_event(self, watched: Any, event: Any, focus_widget: Any = _UNSET) -> bool:
        """The focus-contract decision: dispatch + consume, or pass through.

        Returns ``True`` to consume (a mapped chord on a non-text surface) or
        ``False`` to let the native widget handle the event. Dispatches to the
        controller only on ``KeyPress``. This is the ``QObject.eventFilter`` body,
        exposed as a plain method so it is testable without event-loop plumbing.

        ``focus_widget`` overrides the focused-widget lookup (default: the live
        ``QApplication.focusWidget()``) — a testing seam for exercising the
        exemption deterministically without a shown, activated window.
        """
        event_type = event.type()
        if event_type == self._key_release:
            # Consume the release iff this filter consumed the matching press, so
            # the native widget never sees a half-chord regardless of any
            # focus/exemption change between the two events.
            key = _as_int(event.key())
            if key in self._consumed_press_keys:
                self._consumed_press_keys.discard(key)
                return True
            return False
        if event_type != self._key_press:
            return False
        # A focused text editor keeps native text semantics (PRD §7.3 exemption).
        # Check both the event's target and the focused widget: on key-event
        # propagation the target climbs to non-text parents while focus stays on
        # the text field, so the focus check is what keeps the category field
        # exempt for keys it ignores (e.g. Enter).
        focus = self._current_focus() if focus_widget is _UNSET else focus_widget
        if is_text_entry(watched) or is_text_entry(focus):
            return False
        modifiers = _as_int(event.modifiers()) & self._significant_mask
        command = self._keymap.command_for(_as_int(event.key()), modifiers)
        if command is None:
            return False  # unmapped: let the native widget handle it
        # Dispatch on press (auto-repeat included, mirroring tMAVEN) and record the
        # key so its release is consumed too — the native binding sees neither.
        self._controller.dispatch(command)
        if command.action is CurationAction.JUMP and self._focus_dock is not None:
            self._focus_dock.setFocus()
        self._consumed_press_keys.add(_as_int(event.key()))
        return True

    def _current_focus(self) -> Any:
        """The application's focused widget (``None`` when nothing has focus)."""
        return self._qtwidgets.QApplication.focusWidget()


def default_keymap_path() -> Path:
    """The per-user path where the rebindable keymap persists.

    Uses Qt's ``AppConfigLocation`` so it lands in the platform-native config dir
    (``%LOCALAPPDATA%`` on Windows, ``~/.config`` on Linux, ``~/Library`` on
    macOS). The file is written on demand by :meth:`Keymap.save`.
    """
    from pyqtgraph.Qt import QtCore

    base = QtCore.QStandardPaths.writableLocation(
        QtCore.QStandardPaths.StandardLocation.AppConfigLocation
    )
    return Path(base) / "tether" / "keymap.json"

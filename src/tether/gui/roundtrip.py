# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Trace↔movie round-trip navigation — the per-movie KDTree resolver + navigator.

This is the core of the FR-ROUNDTRIP browser (PRD §7.3, §5.2; #357 wires it into
the shell): the two-way link between a molecule's [`trace_dock`][tether.gui.trace_dock]
trace and its spot in the [`movie_panel`][tether.gui.movie_panel] napari movie.

* **trace → movie** (``Enter`` / list selection): resolve the molecule to *its own*
  movie and centre the camera on its spot. PRD §5.2 makes this ``O(1)`` — the
  molecule already carries its ``movie_id`` and coordinates.
* **movie → trace** (click a spot): a **per-movie KDTree over the molecule spots**
  (PRD §5.2) maps a canvas click to the nearest molecule. An experiment holds many
  movies, so each movie gets its own tree and a click only ever resolves within the
  movie on screen.

The module is split like the rest of ``tether.gui``: the resolver and the
navigator are **Qt-free** (they touch only NumPy + SciPy and a duck-typed panel),
so the round-trip logic runs in the default test matrix without a display. The
only Qt-bound pieces — centring the camera and receiving canvas clicks — live on
[`NapariMoviePanel`][tether.gui.movie_panel.NapariMoviePanel] (``center_on`` /
``connect_spot_click``); this navigator drives them through that thin seam.

**Coordinate conventions (the one place the two frames meet).** Molecule
coordinates are stored ``[x, y] = [column, row]`` (the ``detect_spots`` /
``/molecules`` ``donor_xy`` convention, §5.1), matching
[`MovieOverlay`][tether.gui.movie_panel.MovieOverlay]. napari, however, works in
``[row, col] = [y, x]`` world coordinates — both ``camera.center`` and a mouse
event's ``position``. The resolver therefore builds its KDTree and returns its
camera targets in **napari ``[row, col]``**, converting the stored ``[x, y]`` at
the boundary (`_xy_to_rowcol`), so a click ``position`` and a camera
``center`` flow through without any per-call transposing at the call sites.

**Which spot.** A molecule is one emitter imaged in two channels, so it owns a
**donor** and an **acceptor** spot in the displayed dual-channel frame. The
camera jump anchors on the **donor** spot (Tether's detection anchor since M1);
the click KDTree indexes **both** spots (each mapped back to its molecule), so a
click in either channel half resolves to the right molecule.

**Synchronized scrubbing (#366).** [`FrameLink`][] is the third link between the
two surfaces: the trace dock's frame cursor and the movie panel's displayed frame
move together, for the active molecule's own movie. It is Qt-free like the
navigator, and drives the panel through the frame half of [`MoviePanelLike`][].
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

__all__ = [
    "FrameLink",
    "MoleculeSite",
    "MoviePanelLike",
    "MovieSpots",
    "RoundTripIndex",
    "RoundTripNavigator",
    "TraceCursorLike",
    "sites_for_molecules",
]


def _validate_spot(xy: object, name: str) -> np.ndarray:
    """Validate + freeze a single ``(2,)`` ``[x, y]`` spot coordinate.

    Returns a read-only ``float64`` copy so a [`MoleculeSite`][] never aliases
    the caller's buffer. Rejects a non-``(2,)`` shape and any non-finite value —
    a NaN spot would silently poison the KDTree (every query would return it or
    skip it, depending on SciPy's handling) rather than failing loudly here.
    """
    arr = np.asarray(xy, dtype=np.float64)
    if arr.shape != (2,):
        raise ValueError(f"{name} must be a (2,) [x, y] coordinate, got shape {arr.shape}")
    if not np.isfinite(arr).all():
        raise ValueError(f"{name} must be finite, got {arr!r}")
    out = arr.copy()
    out.setflags(write=False)
    return out


def _xy_to_rowcol(xy: np.ndarray) -> tuple[float, float]:
    """Convert a stored ``[x, y] = [col, row]`` spot to napari ``(row, col)``."""
    return float(xy[1]), float(xy[0])


@dataclass(frozen=True)
class MoleculeSite:
    """One molecule's movie + spot coordinates for round-trip navigation.

    A thin, immutable value object decoupling the resolver from the ``/molecules``
    store: a caller reads each molecule's ``movie_id`` and per-channel coordinates
    (§5.1) and hands over a [`MoleculeSite`][]. Coordinates are ``[x, y] =
    [column, row]`` in the displayed movie frame, the same convention as
    [`MovieOverlay`][tether.gui.movie_panel.MovieOverlay].

    Parameters
    ----------
    movie_id
        The molecule's own movie (§5.2: each molecule resolves to its own movie).
    donor_xy, acceptor_xy
        ``(2,)`` ``[x, y]`` donor / acceptor spot centres in the displayed frame.
    """

    movie_id: str
    donor_xy: np.ndarray
    acceptor_xy: np.ndarray

    def __post_init__(self) -> None:
        if not isinstance(self.movie_id, str) or not self.movie_id:
            raise ValueError(f"movie_id must be a non-empty str, got {self.movie_id!r}")
        object.__setattr__(self, "donor_xy", _validate_spot(self.donor_xy, "donor_xy"))
        object.__setattr__(self, "acceptor_xy", _validate_spot(self.acceptor_xy, "acceptor_xy"))


class MovieSpots(Protocol):
    """One shown movie's spots, keyed by molecule — what [`sites_for_molecules`][] reads.

    [`ProjectMovie`][tether.services.movies.ProjectMovie] satisfies it: ``donor_xy`` and
    ``acceptor_xy`` are ``(N, 2)`` ``[x, y]`` spots in the displayed frame, and
    ``molecule_keys`` names the molecule of each row.
    """

    @property
    def movie_id(self) -> str:
        """The movie's ``movie_id``, as the panel's movie list names it."""
        ...

    @property
    def molecule_keys(self) -> Sequence[str]:
        """The ``molecule_key`` of each spot row of ``donor_xy`` / ``acceptor_xy``."""
        ...

    @property
    def donor_xy(self) -> np.ndarray:
        """``(N, 2)`` ``[x, y]`` donor spots in the displayed frame."""
        ...

    @property
    def acceptor_xy(self) -> np.ndarray:
        """``(N, 2)`` ``[x, y]`` acceptor spots in the displayed frame."""
        ...


def sites_for_molecules(
    molecule_keys: Sequence[str | None], movies: Iterable[MovieSpots]
) -> tuple[list[MoleculeSite], list[int]]:
    """The [`MoleculeSite`][] of each listed molecule that has a spot in ``movies`` (#357).

    ``molecule_keys`` is the molecule list in its display order (``None`` for a molecule
    with no store row). ``movies`` are the movies the panel shows. Returns the sites, and
    for each site the position in ``molecule_keys`` it belongs to, so a resolved site maps
    back to its list row. A molecule with no spot in a shown movie (its movie file is not
    shown, or its movie records no spot positions) gets no site and so cannot be jumped
    to or clicked. A key that appears in more than one movie keeps its first spot.
    """
    spots: dict[str, tuple[str, np.ndarray, np.ndarray]] = {}
    for movie in movies:
        donor = np.asarray(movie.donor_xy, dtype=np.float64).reshape(-1, 2)
        acceptor = np.asarray(movie.acceptor_xy, dtype=np.float64).reshape(-1, 2)
        keys = list(movie.molecule_keys)
        if not len(keys) == len(donor) == len(acceptor):
            raise ValueError(
                f"movie {movie.movie_id!r} has {len(keys)} molecule key(s) for "
                f"{len(donor)} donor and {len(acceptor)} acceptor spot(s)"
            )
        for key, d, a in zip(keys, donor, acceptor, strict=True):
            spots.setdefault(key, (movie.movie_id, d, a))
    sites: list[MoleculeSite] = []
    rows: list[int] = []
    for row, key in enumerate(molecule_keys):
        hit = spots.get(key) if key is not None else None
        if hit is None:
            continue
        movie_id, donor_xy, acceptor_xy = hit
        sites.append(MoleculeSite(movie_id=movie_id, donor_xy=donor_xy, acceptor_xy=acceptor_xy))
        rows.append(row)
    return sites, rows


class RoundTripIndex:
    """Per-movie KDTree over molecule spots — the round-trip resolver (§5.2, Qt-free).

    Built once from the experiment's [`MoleculeSite`][] list; it answers the two
    legs of the round-trip:

    * [`camera_target`][] — ``molecule index → (movie_id, (row, col))``: the
      donor-spot napari camera centre for the **trace → movie** jump (``O(1)``).
    * [`nearest_molecule`][] — ``(movie_id, (row, col)) → molecule index`` via the
      movie's KDTree over **both** channels' spots, for the **movie → trace** click.
      A click only ever resolves within the passed ``movie_id`` (per-movie trees),
      and an optional ``max_distance`` ignores clicks that land far from any spot.

    Coordinates in and out are napari ``[row, col]`` (see the module docstring);
    the stored ``[x, y]`` is converted at construction.
    """

    def __init__(self, sites: Sequence[MoleculeSite]) -> None:
        from scipy.spatial import cKDTree

        self._sites: list[MoleculeSite] = list(sites)
        for i, site in enumerate(self._sites):
            if not isinstance(site, MoleculeSite):
                raise TypeError(f"sites[{i}] must be a MoleculeSite, got {type(site).__name__}")

        # Group every spot (donor + acceptor) by movie, remembering which molecule
        # each spot belongs to, then build one KDTree per movie in [row, col].
        by_movie: dict[str, list[tuple[int, tuple[float, float]]]] = defaultdict(list)
        for mol_index, site in enumerate(self._sites):
            by_movie[site.movie_id].append((mol_index, _xy_to_rowcol(site.donor_xy)))
            by_movie[site.movie_id].append((mol_index, _xy_to_rowcol(site.acceptor_xy)))

        self._trees: dict[str, tuple[object, np.ndarray]] = {}
        for movie_id, entries in by_movie.items():
            mol_indices = np.array([e[0] for e in entries], dtype=np.intp)
            points = np.array([e[1] for e in entries], dtype=np.float64)
            self._trees[movie_id] = (cKDTree(points), mol_indices)

    # --- accessors -----------------------------------------------------------

    @property
    def n_molecules(self) -> int:
        """Number of molecules indexed."""
        return len(self._sites)

    @property
    def movie_ids(self) -> list[str]:
        """The distinct ``movie_id``s that carry at least one molecule."""
        return list(self._trees)

    def site(self, mol_index: int) -> MoleculeSite:
        """The [`MoleculeSite`][] at ``mol_index``."""
        return self._sites[mol_index]

    # --- the two round-trip legs ---------------------------------------------

    def camera_target(self, mol_index: int) -> tuple[str, tuple[float, float]]:
        """``molecule → (movie_id, (row, col))`` — the trace→movie camera centre.

        Returns the molecule's movie and its **donor** spot as a napari
        ``(row, col)`` world coordinate for
        [`NapariMoviePanel.center_on`][tether.gui.movie_panel.NapariMoviePanel.center_on].
        Raises ``IndexError`` for an out-of-range molecule.
        """
        site = self._sites[mol_index]
        return site.movie_id, _xy_to_rowcol(site.donor_xy)

    def nearest_molecule(
        self,
        movie_id: str,
        position: tuple[float, float] | Sequence[float],
        *,
        max_distance: float | None = None,
    ) -> int | None:
        """``(movie_id, click) → molecule index`` — the movie→trace resolution.

        ``position`` is a napari ``(row, col)`` world coordinate (a mouse event's
        ``position``). Returns the index of the molecule whose nearest donor/acceptor
        spot is closest to the click **within** ``movie_id``, or ``None`` when the
        movie carries no molecules or (with ``max_distance`` set) the nearest spot is
        farther than ``max_distance``.
        """
        point = np.asarray(position, dtype=np.float64)
        if point.shape != (2,) or not np.isfinite(point).all():
            # The panel reduces a napari nD event.position to (row, col) at the
            # boundary (connect_spot_click), so the resolver requires exactly a
            # 2-D click — never silently truncate an (depth, row, col) to
            # (depth, row) and query the wrong axes.
            raise ValueError(f"position must be a finite (row, col) coordinate, got {position!r}")
        entry = self._trees.get(movie_id)
        if entry is None:
            return None
        tree, mol_indices = entry
        upper = float(max_distance) if max_distance is not None else np.inf
        distance, tree_index = tree.query(point, distance_upper_bound=upper)
        # SciPy signals "nothing within distance_upper_bound" with an infinite
        # distance and an out-of-range index (== tree size).
        if not np.isfinite(distance) or tree_index >= len(mol_indices):
            return None
        return int(mol_indices[tree_index])


class MoviePanelLike(Protocol):
    """The slice of `NapariMoviePanel` the navigator drives.

    Declared as a `Protocol` so [`RoundTripNavigator`][] stays
    Qt-free and testable with a lightweight fake, while the real napari panel
    satisfies it structurally.
    """

    @property
    def active_index(self) -> int:
        """The registration index of the movie currently displayed (``-1`` when none)."""
        ...

    def set_active_movie(self, index: int) -> object: ...

    def center_on(self, row: float, col: float, *, zoom: float | None = ...) -> None: ...

    def connect_spot_click(self, callback: Callable[[tuple[float, float]], None]) -> None: ...

    # The frame half, which [`FrameLink`][] drives (#366).

    @property
    def n_frames(self) -> int:
        """Number of frames of the movie shown (``0`` when none)."""
        ...

    @property
    def current_frame(self) -> int:
        """The movie frame shown (``-1`` when none)."""
        ...

    def set_frame(self, frame: int) -> None: ...

    def connect_frame_changed(self, callback: Callable[[int], object]) -> None:
        """Call ``callback(frame)`` on each change of the frame shown.

        A switch to another movie is a change even when it keeps the frame number, so the
        link gets back in step when the target movie shows again.
        """
        ...


class TraceCursorLike(Protocol):
    """The slice of [`TraceDock`][tether.gui.trace_dock.TraceDock] that [`FrameLink`][] drives.

    The frame cursor of the trace shown, in frames **into the trace**. The callbacks of
    ``connect_frame_cursor`` hear a drag only, never a ``set_frame_cursor`` call.
    """

    @property
    def frame_cursor(self) -> int | None:
        """The cursor frame (``None`` while no trace is shown)."""
        ...

    def set_frame_cursor(self, frame: int) -> None: ...

    def connect_frame_cursor(self, callback: Callable[[int], object]) -> None: ...


class RoundTripNavigator:
    """Binds a [`RoundTripIndex`][] to a movie panel + list/dock callbacks (§7.3).

    The coordinator that turns the resolver into live navigation. It is Qt-free —
    the panel is duck-typed ([`MoviePanelLike`][]) and the list/dock updates are
    injected callbacks — so the full round-trip is exercised headlessly.

    * **trace → movie:** [`focus_molecule`][] switches the panel to the molecule's
      own movie, centres the camera on its donor spot, and fires ``on_focus`` (the
      shell refocuses the trace dock after the jump, mirroring tMAVEN, §7.3).
    * **movie → trace:** [`connect`][] wires the panel's spot-click to
      [`handle_spot_click`][], which resolves the click to the nearest molecule
      and fires ``on_select`` (the shell selects that molecule's list row + trace).

    Parameters
    ----------
    index
        The [`RoundTripIndex`][] resolver.
    panel
        The movie panel to drive ([`MoviePanelLike`][]).
    movie_ids
        The ``movie_id`` of each registered movie, in the panel's registration
        order, so a molecule's ``movie_id`` maps to the panel index to switch to.
    on_select
        Called with a molecule index when a click resolves to one (movie → trace).
    on_focus
        Called with a molecule index after a trace → movie jump (dock refocus).
    max_click_distance
        Ignore clicks whose nearest spot is farther than this (napari px); ``None``
        always resolves to the nearest molecule.
    """

    def __init__(
        self,
        index: RoundTripIndex,
        panel: MoviePanelLike,
        movie_ids: Sequence[str],
        *,
        on_select: Callable[[int], object] | None = None,
        on_focus: Callable[[int], object] | None = None,
        max_click_distance: float | None = None,
    ) -> None:
        self._index = index
        self._panel = panel
        self._movie_ids = list(movie_ids)
        # The one-to-one movie_id → panel-index mapping is the navigator's contract;
        # a duplicate id would silently misroute focus/clicks, so reject it loudly.
        if len(set(self._movie_ids)) != len(self._movie_ids):
            raise ValueError(f"movie_ids must be unique, got {self._movie_ids!r}")
        self._panel_index: dict[str, int] = {
            movie_id: panel_index for panel_index, movie_id in enumerate(self._movie_ids)
        }
        self._on_select = on_select
        self._on_focus = on_focus
        self._max_click_distance = max_click_distance
        self._connected = False

    # --- accessors -----------------------------------------------------------

    @property
    def index(self) -> RoundTripIndex:
        """The [`RoundTripIndex`][] resolver driving both round-trip legs."""
        return self._index

    @property
    def movie_ids(self) -> list[str]:
        """The panel-index-aligned ``movie_id`` list."""
        return list(self._movie_ids)

    # --- trace → movie -------------------------------------------------------

    def focus_molecule(self, mol_index: int) -> None:
        """Jump the movie panel to ``mol_index``'s spot (switch movie + centre).

        Resolves the molecule to its own movie (§5.2), switches the panel to it if
        it is not already active, centres the camera on the donor spot, and fires
        ``on_focus``. Raises ``KeyError`` if the molecule's ``movie_id`` is not among
        the registered movies (a misconfigured navigator, surfaced rather than
        silently no-oped).
        """
        movie_id, (row, col) = self._index.camera_target(mol_index)
        if movie_id not in self._panel_index:
            raise KeyError(
                f"molecule {mol_index} is in movie {movie_id!r}, which is not registered "
                f"with the panel (have {self._movie_ids})"
            )
        panel_index = self._panel_index[movie_id]
        if panel_index != self._panel.active_index:
            self._panel.set_active_movie(panel_index)
        self._panel.center_on(row, col)
        if self._on_focus is not None:
            self._on_focus(mol_index)

    # --- movie → trace -------------------------------------------------------

    def connect(self) -> None:
        """Wire the panel's spot-click to [`handle_spot_click`][] (idempotent).

        A second call is a no-op, so re-binding the navigator never registers a
        duplicate callback that would fire ``on_select`` twice per click.
        """
        if self._connected:
            return
        self._panel.connect_spot_click(self.handle_spot_click)
        self._connected = True

    def handle_spot_click(self, position: tuple[float, float]) -> int | None:
        """Resolve a click on the active movie to a molecule and fire ``on_select``.

        ``position`` is the napari ``(row, col)`` click. Uses the **active** movie's
        KDTree, so a click resolves only within the movie on screen. Returns the
        resolved molecule index (or ``None`` if the click matched nothing), and
        fires ``on_select`` only on a match.
        """
        active = self._panel.active_index
        if not 0 <= active < len(self._movie_ids):
            return None
        movie_id = self._movie_ids[active]
        mol_index = self._index.nearest_molecule(
            movie_id, position, max_distance=self._max_click_distance
        )
        if mol_index is not None and self._on_select is not None:
            self._on_select(mol_index)
        return mol_index


class FrameLink:
    """Synchronized scrubbing between the trace dock and the movie panel (#366, Qt-free).

    Moving the trace dock's frame cursor shows that frame in the movie panel, and moving
    the panel's time slider moves the cursor, for **the active molecule's own movie**.
    Both sides are duck-typed ([`TraceCursorLike`][], [`MoviePanelLike`][]), so the
    coupling runs headless with fakes.

    The link acts only while it is **enabled** (the user can turn it off) and while the
    panel shows the target movie that [`set_target`][] named. When the user switches the
    panel to another movie, neither side moves the other until the target movie shows
    again; the panel then tells its frame, and the cursor moves to it.

    **Frames.** Trace frame ``i`` is movie frame ``frame_offset + i`` (the start of the
    molecule's ``frame_range``, §5.1). A frame outside the other side's range moves that
    side to its nearest end: a movie frame before or after the trace puts the cursor on
    the trace's first or last frame, and a trace frame past the movie's end shows the
    movie's last frame.

    **No loop.** A frame the link itself set is not passed back: while the link moves one
    side, a change it hears from the other side is ignored.

    Parameters
    ----------
    panel
        The movie panel ([`MoviePanelLike`][]).
    cursor
        The trace dock's frame cursor ([`TraceCursorLike`][]).
    enabled
        Whether the link starts on (it is opt-out, so ``True``).
    """

    def __init__(
        self, panel: MoviePanelLike, cursor: TraceCursorLike, *, enabled: bool = True
    ) -> None:
        self._panel = panel
        self._cursor = cursor
        self._enabled = bool(enabled)
        # (panel movie index, frame_offset, trace n_frames), or None: nothing to link.
        self._target: tuple[int, int, int] | None = None
        self._moving = False
        self._connected = False

    # --- state ---------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """Whether the link is on."""
        return self._enabled

    @property
    def target(self) -> tuple[int, int, int] | None:
        """``(movie index, frame_offset, n_frames)`` of the linked trace, or ``None``."""
        return self._target

    @property
    def linked(self) -> bool:
        """Whether the link acts now: enabled, with a target, and the target movie shown."""
        return (
            self._enabled
            and self._target is not None
            and self._panel.active_index == self._target[0]
        )

    def set_enabled(self, enabled: bool) -> None:
        """Turn the link on or off. Turning it on shows the cursor frame in the movie."""
        was = self._enabled
        self._enabled = bool(enabled)
        if self._enabled and not was:
            self.sync(lead="trace")

    # --- wiring --------------------------------------------------------------

    def connect(self) -> None:
        """Listen to both sides (idempotent: a second call adds no second listener)."""
        if self._connected:
            return
        self._panel.connect_frame_changed(self.on_movie_frame)
        self._cursor.connect_frame_cursor(self.on_trace_frame)
        self._connected = True

    def set_target(
        self,
        movie_index: int,
        *,
        frame_offset: int,
        n_frames: int,
        lead: Literal["trace", "movie"] = "trace",
    ) -> None:
        """Link the trace shown to panel movie ``movie_index``, then bring the two in step.

        ``frame_offset`` and ``n_frames`` describe the trace (its first movie frame and
        its length). ``lead`` names the side the other one follows now: ``"trace"`` (a
        molecule chosen in the list: the movie shows the cursor frame) or ``"movie"`` (a
        molecule chosen by a click on its spot: the cursor moves to the frame the user
        was looking at).
        """
        if isinstance(frame_offset, bool) or int(frame_offset) < 0:
            raise ValueError(f"frame_offset must be a non-negative integer, got {frame_offset!r}")
        if isinstance(n_frames, bool) or int(n_frames) < 1:
            raise ValueError(f"n_frames must be a positive integer, got {n_frames!r}")
        self._target = (int(movie_index), int(frame_offset), int(n_frames))
        self.sync(lead=lead)

    def clear_target(self) -> None:
        """Link nothing (the trace shown has no spot in a movie the panel shows)."""
        self._target = None

    def sync(self, *, lead: Literal["trace", "movie"] = "trace") -> None:
        """Bring the side that is not ``lead`` to ``lead``'s frame (no-op unless linked)."""
        if lead not in ("trace", "movie"):
            raise ValueError(f"lead must be 'trace' or 'movie', got {lead!r}")
        if lead == "trace":
            cursor = self._cursor.frame_cursor
            if cursor is not None:
                self.on_trace_frame(cursor)
        else:
            self.on_movie_frame(self._panel.current_frame)

    # --- the two directions --------------------------------------------------

    def on_trace_frame(self, frame: int) -> None:
        """The cursor moved to trace frame ``frame``: show its movie frame."""
        target = self._target
        if self._moving or target is None or not self.linked:
            return
        n_movie = self._panel.n_frames
        if n_movie < 1:
            return
        movie_frame = min(max(target[1] + int(frame), 0), n_movie - 1)
        if movie_frame == self._panel.current_frame:
            return
        self._moving = True
        try:
            self._panel.set_frame(movie_frame)
        finally:
            self._moving = False

    def on_movie_frame(self, frame: int) -> None:
        """The movie shows frame ``frame``: move the cursor to its trace frame."""
        target = self._target
        if self._moving or target is None or not self.linked or frame < 0:
            return
        _, offset, n_frames = target
        trace_frame = min(max(int(frame) - offset, 0), n_frames - 1)
        if trace_frame == self._cursor.frame_cursor:
            return
        self._moving = True
        try:
            self._cursor.set_frame_cursor(trace_frame)
        finally:
            self._moving = False

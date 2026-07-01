# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Trace↔movie round-trip navigation — the per-movie KDTree resolver + navigator.

This is the M2 S4 core of the FR-ROUNDTRIP browser (PRD §7.3, §5.2): the two-way
link between a molecule's :mod:`~tether.gui.trace_dock` trace and its spot in the
:mod:`~tether.gui.movie_panel` napari movie.

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
:class:`~tether.gui.movie_panel.NapariMoviePanel` (``center_on`` /
``connect_spot_click``); this navigator drives them through that thin seam.

**Coordinate conventions (the one place the two frames meet).** Molecule
coordinates are stored ``[x, y] = [column, row]`` (the ``detect_spots`` /
``/molecules`` ``donor_xy`` convention, §5.1), matching
:class:`~tether.gui.movie_panel.MovieOverlay`. napari, however, works in
``[row, col] = [y, x]`` world coordinates — both ``camera.center`` and a mouse
event's ``position``. The resolver therefore builds its KDTree and returns its
camera targets in **napari ``[row, col]``**, converting the stored ``[x, y]`` at
the boundary (:func:`_xy_to_rowcol`), so a click ``position`` and a camera
``center`` flow through without any per-call transposing at the call sites.

**Which spot.** A molecule is one emitter imaged in two channels, so it owns a
**donor** and an **acceptor** spot in the displayed dual-channel frame. The
camera jump anchors on the **donor** spot (Tether's detection anchor since M1);
the click KDTree indexes **both** spots (each mapped back to its molecule), so a
click in either channel half resolves to the right molecule.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

__all__ = ["MoleculeSite", "MoviePanelLike", "RoundTripIndex", "RoundTripNavigator"]


def _validate_spot(xy: object, name: str) -> np.ndarray:
    """Validate + freeze a single ``(2,)`` ``[x, y]`` spot coordinate.

    Returns a read-only ``float64`` copy so a :class:`MoleculeSite` never aliases
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
    (§5.1) and hands over a :class:`MoleculeSite`. Coordinates are ``[x, y] =
    [column, row]`` in the displayed movie frame, the same convention as
    :class:`~tether.gui.movie_panel.MovieOverlay`.

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


class RoundTripIndex:
    """Per-movie KDTree over molecule spots — the round-trip resolver (§5.2, Qt-free).

    Built once from the experiment's :class:`MoleculeSite` list; it answers the two
    legs of the round-trip:

    * :meth:`camera_target` — ``molecule index → (movie_id, (row, col))``: the
      donor-spot napari camera centre for the **trace → movie** jump (``O(1)``).
    * :meth:`nearest_molecule` — ``(movie_id, (row, col)) → molecule index`` via the
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
        """The :class:`MoleculeSite` at ``mol_index``."""
        return self._sites[mol_index]

    # --- the two round-trip legs ---------------------------------------------

    def camera_target(self, mol_index: int) -> tuple[str, tuple[float, float]]:
        """``molecule → (movie_id, (row, col))`` — the trace→movie camera centre.

        Returns the molecule's movie and its **donor** spot as a napari
        ``(row, col)`` world coordinate for :meth:`NapariMoviePanel.center_on`.
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
        entry = self._trees.get(movie_id)
        if entry is None:
            return None
        tree, mol_indices = entry
        upper = float(max_distance) if max_distance is not None else np.inf
        point = np.asarray(position, dtype=np.float64)[:2]
        distance, tree_index = tree.query(point, distance_upper_bound=upper)
        # SciPy signals "nothing within distance_upper_bound" with an infinite
        # distance and an out-of-range index (== tree size).
        if not np.isfinite(distance) or tree_index >= len(mol_indices):
            return None
        return int(mol_indices[tree_index])


class MoviePanelLike(Protocol):
    """The slice of :class:`~tether.gui.movie_panel.NapariMoviePanel` the navigator drives.

    Declared as a :class:`~typing.Protocol` so :class:`RoundTripNavigator` stays
    Qt-free and testable with a lightweight fake, while the real napari panel
    satisfies it structurally.
    """

    @property
    def active_index(self) -> int: ...

    def set_active_movie(self, index: int) -> object: ...

    def center_on(self, row: float, col: float, *, zoom: float | None = ...) -> None: ...

    def connect_spot_click(self, callback: Callable[[tuple[float, float]], None]) -> None: ...


class RoundTripNavigator:
    """Binds a :class:`RoundTripIndex` to a movie panel + list/dock callbacks (§7.3).

    The coordinator that turns the resolver into live navigation. It is Qt-free —
    the panel is duck-typed (:class:`MoviePanelLike`) and the list/dock updates are
    injected callbacks — so the full round-trip is exercised headlessly.

    * **trace → movie:** :meth:`focus_molecule` switches the panel to the molecule's
      own movie, centres the camera on its donor spot, and fires ``on_focus`` (the
      shell refocuses the trace dock after the jump, mirroring tMAVEN, §7.3).
    * **movie → trace:** :meth:`connect` wires the panel's spot-click to
      :meth:`handle_spot_click`, which resolves the click to the nearest molecule
      and fires ``on_select`` (the shell selects that molecule's list row + trace).

    Parameters
    ----------
    index
        The :class:`RoundTripIndex` resolver.
    panel
        The movie panel to drive (:class:`MoviePanelLike`).
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
        # movie_id → panel registration index (the first registration wins if a
        # movie were ever registered twice; ids are expected unique).
        self._panel_index: dict[str, int] = {}
        for panel_index, movie_id in enumerate(self._movie_ids):
            self._panel_index.setdefault(movie_id, panel_index)
        self._on_select = on_select
        self._on_focus = on_focus
        self._max_click_distance = max_click_distance

    # --- accessors -----------------------------------------------------------

    @property
    def index(self) -> RoundTripIndex:
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
        """Wire the panel's spot-click to :meth:`handle_spot_click`."""
        self._panel.connect_spot_click(self.handle_spot_click)

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

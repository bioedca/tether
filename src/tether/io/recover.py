# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-molecule coordinate recovery for Deep-LASI legacy import (PRD §7.8).

The M7 "New project from Deep-LASI data" re-analysis workflow reconstructs a
round-trip-ready project *without re-extraction* (PRD §7.8, goal G8). After
:mod:`tether.io.intake` groups a folder into acquisitions and pairs each to its
raw movie, this module answers the next question: **where is each molecule?**

Two Deep-LASI artifacts carry per-molecule pixel coordinates (PRD §7.8
"Coordinate sources", Appendix A):

* the ``.tdat`` ``ParticlesColocalized`` table (:func:`tether.io.read_tdat` →
  :class:`~tether.io.tdat.TdatColocalization`), which stores one ``(x, y)`` per
  channel keyed by channel index; and
* the ``.mat`` ``fret_pairs`` (:func:`tether.io.read_deeplasi_mat` →
  :class:`~tether.io.deeplasi.DeepLasiExport`), already split into donor / acceptor
  ``(N, 2)`` arrays.

:func:`recover_coordinates` **unifies** these two into one donor/acceptor
representation, so a downstream reconstruction sees a single coordinate model
regardless of which artifact survived. The channel→role mapping for the ``.tdat``
is faithful, not assumed: the mapping/trace **reference channel**
(:attr:`~tether.io.tdat.Tdat.reference_channel`) is the donor and the single
remaining colocalized channel is the acceptor — the same donor/acceptor split the
``.mat`` records, locked equal on real UCKOPSB data by ``tests/test_recover.py``.

The ``.txt`` and the tMAVEN SMD carry **intensities only, no coordinates**
(Appendix A), and tMAVEN may subset / reorder molecules by the GUI selection mask
(Appendix D.1), so a returning SMD's row order is *not trusted*. To attach recovered
coordinates to an SMD's traces, :func:`match_smd_to_coordinates` re-resolves each
SMD trace to its acquisition molecule by **exact intensity-trace matching** against
the acquisition's index-aligned reference traces — the same identity test the
tMAVEN return leg uses (:func:`tether.idealize.match_return_leg`, PRD §5.3/§7.4),
reused here rather than reimplemented. The resulting per-SMD-row acquisition index
is the "index-pairing key" PRD §7.8 calls for; SMD molecules absent from the
reference are reported unmatched, never guessed.

Layering: the intensity matcher lives in the sibling :mod:`tether.idealize` package
and is imported lazily inside :func:`match_smd_to_coordinates` — ``tether.idealize``
never imports ``tether.io``, so there is no import cycle, and ``tether.io`` stays
free of the idealize (sidecar) import graph at load time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import numpy as np

if TYPE_CHECKING:
    from tether.io.deeplasi import DeepLasiExport
    from tether.io.tdat import Tdat

__all__ = [
    "RecoveredCoordinates",
    "SmdCoordinateMatch",
    "match_smd_to_coordinates",
    "recover_coordinates",
]

#: Which artifact the unified coordinates were recovered from.
CoordinateSource = Literal["tdat", "mat"]


@dataclass(frozen=True, eq=False)
class RecoveredCoordinates:
    """Unified per-molecule donor / acceptor pixel coordinates (PRD §7.8).

    ``donor_xy`` / ``acceptor_xy`` are 0-based ``[x = col, y = row]`` ``float64``
    ``(n_molecules, 2)`` arrays in the **acquisition molecule order** (row ``i`` is
    detected molecule ``i``), the index-pairing key downstream import keys on.
    ``source`` records which artifact supplied them (``"tdat"`` or ``"mat"``).
    ``eq=False`` because the ndarray fields make a generated ``__eq__`` ambiguous
    (cf. :class:`~tether.io.deeplasi.DeepLasiExport`).
    """

    donor_xy: np.ndarray
    acceptor_xy: np.ndarray
    source: CoordinateSource

    @property
    def n_molecules(self) -> int:
        """Number of molecules carrying recovered donor/acceptor coordinates."""
        return int(self.donor_xy.shape[0])


@dataclass(eq=False)
class SmdCoordinateMatch:
    """Recovered coordinates attached to an SMD's traces (PRD §7.8, §5.3).

    ``mapping[i]`` is the acquisition molecule index matched to SMD row ``i`` (the
    index-pairing key), or ``-1`` when SMD row ``i`` matched no reference molecule.
    ``donor_xy`` / ``acceptor_xy`` are ``(n_smd, 2)`` ``float64`` — the recovered
    coordinates of each SMD row's matched molecule, or ``[nan, nan]`` when
    unmatched. The match is **one-to-one** (each acquisition molecule is claimed by
    at most one SMD row) and unmatched rows are reported, never guessed. ``eq=False``
    because the ndarray fields make a generated ``__eq__`` ambiguous (identity
    equality is the sensible default here, as for :class:`RecoveredCoordinates`).
    """

    mapping: np.ndarray
    donor_xy: np.ndarray
    acceptor_xy: np.ndarray
    matched: list[tuple[int, int]]
    unmatched: list[int]

    @property
    def n_smd(self) -> int:
        """Number of SMD trace rows covered by this match, matched or not."""
        return int(self.mapping.shape[0])

    @property
    def n_matched(self) -> int:
        """Number of SMD rows resolved to an acquisition molecule."""
        return len(self.matched)

    @property
    def n_unmatched(self) -> int:
        """Number of SMD rows that matched no reference molecule."""
        return len(self.unmatched)

    @property
    def all_matched(self) -> bool:
        """True when every SMD row resolved to an acquisition molecule."""
        return not self.unmatched


def _acceptor_channel(tdat: Tdat) -> int:
    """The single colocalized channel that is not the donor (reference) channel.

    Deep-LASI two-colour data colocalizes exactly two channels — the donor (the
    mapping/trace reference) and the acceptor. Anything else (one channel, or a
    three-plus-colour set) is outside the M7 two-colour re-analysis scope and is
    refused rather than silently mis-assigned.
    """
    others = [ch for ch in tdat.channels_with_data if ch != tdat.reference_channel]
    if tdat.reference_channel not in tdat.channels_with_data or len(others) != 1:
        raise ValueError(
            "recover_coordinates supports two-colour donor/acceptor .tdat only; got "
            f"channels_with_data={tdat.channels_with_data!r} with reference channel "
            f"{tdat.reference_channel} (need exactly the reference + one other)."
        )
    return others[0]


def recover_coordinates(
    *,
    tdat: Tdat | None = None,
    mat: DeepLasiExport | None = None,
    prefer: CoordinateSource = "tdat",
) -> RecoveredCoordinates:
    """Unify per-molecule donor / acceptor coordinates from a ``.tdat`` or ``.mat``.

    Recovers a single donor/acceptor coordinate model from whichever Deep-LASI
    artifact carries coordinates (PRD §7.8). When only one is given it is used;
    when both are given ``prefer`` selects the authoritative source (default the
    ``.tdat`` ``TIRFdata`` colocalization). The two are **not** cross-validated here
    — they may cover a different molecule count (the ``.mat`` export is often a
    curated slice of the full ``.tdat`` detection); their agreement on a shared
    slice is locked by ``tests/test_recover.py`` instead.

    Parameters
    ----------
    tdat:
        A decoded :class:`~tether.io.tdat.Tdat` (its ``colocalization`` supplies the
        per-channel coordinates). Donor = the reference channel, acceptor = the one
        other colocalized channel.
    mat:
        A parsed :class:`~tether.io.deeplasi.DeepLasiExport` (its ``donor_xy`` /
        ``acceptor_xy`` are used directly).
    prefer:
        Which source to use when **both** are provided (``"tdat"`` or ``"mat"``).

    Returns
    -------
    RecoveredCoordinates
        Donor / acceptor ``(N, 2)`` 0-based ``[x, y]`` coordinates and their source.

    Raises
    ------
    ValueError
        If neither ``tdat`` nor ``mat`` is given, if ``prefer`` is not one of the
        provided sources, or if a given ``.tdat`` is not two-colour donor/acceptor.
    """
    if prefer not in ("tdat", "mat"):
        raise ValueError(f"prefer must be 'tdat' or 'mat'; got {prefer!r}")
    if tdat is None and mat is None:
        raise ValueError("recover_coordinates needs a .tdat or a .mat coordinate source")

    # Honour ``prefer`` only when that source is present; otherwise use the one given.
    use_tdat = tdat is not None if (prefer == "tdat" or mat is None) else False

    if use_tdat:
        assert tdat is not None  # narrowed by use_tdat; for the type checker
        donor_ch = tdat.reference_channel
        acceptor_ch = _acceptor_channel(tdat)
        coords = tdat.colocalization.coords
        donor_xy = np.ascontiguousarray(coords[donor_ch], dtype=np.float64)
        acceptor_xy = np.ascontiguousarray(coords[acceptor_ch], dtype=np.float64)
        return RecoveredCoordinates(donor_xy=donor_xy, acceptor_xy=acceptor_xy, source="tdat")

    assert mat is not None  # the only remaining source
    donor_xy = np.ascontiguousarray(mat.donor_xy, dtype=np.float64)
    acceptor_xy = np.ascontiguousarray(mat.acceptor_xy, dtype=np.float64)
    return RecoveredCoordinates(donor_xy=donor_xy, acceptor_xy=acceptor_xy, source="mat")


def match_smd_to_coordinates(
    smd_raw,
    reference_traces,
    recovered: RecoveredCoordinates,
    *,
    atol: float = 1e-6,
    rtol: float = 0.0,
    id_hint=None,
) -> SmdCoordinateMatch:
    """Attach recovered coordinates to an SMD's traces by intensity matching.

    The tMAVEN SMD carries intensities but no trusted coordinates or molecule slot
    (Appendix A/D.1), so each SMD trace is re-resolved to its acquisition molecule
    by exact intensity-trace matching against ``reference_traces`` — the
    acquisition's traces in the same order as ``recovered`` (row ``i`` is molecule
    ``i``). The match delegates to :func:`tether.idealize.match_return_leg` (the
    tMAVEN return-leg matcher, PRD §5.3/§7.4); the returned per-SMD-row acquisition
    index is the index-pairing key, and each row's recovered coordinates are taken
    from that index (``nan`` when unmatched).

    The trace kind must be **consistent** between the SMD and the reference (both
    raw, or both corrected): a Deep-LASI ``video*.hdf5`` SMD stores the corrected
    ``-donc-accc-w`` series, so it cross-checks against the ``.mat`` ``donc``/``accc``
    (or the ``.txt``), not the raw ``don``/``acc``.

    Parameters
    ----------
    smd_raw:
        ``(M, T_s, 2)`` SMD trace array (donor, acceptor), e.g.
        :attr:`tether.idealize.SMDData.raw`.
    reference_traces:
        ``(N, T_r, 2)`` acquisition traces (donor, acceptor) index-aligned with
        ``recovered`` — ``N`` must equal ``recovered.n_molecules``.
    recovered:
        The :class:`RecoveredCoordinates` whose molecule order the reference traces
        follow.
    atol, rtol:
        Absolute / relative intensity-match tolerance passed through to
        :func:`~tether.idealize.match_return_leg` (default a tight absolute match,
        since a Deep-LASI SMD reproduces the exported trace exactly).
    id_hint:
        Optional length-``M`` candidate acquisition indices (e.g. an SMD source /
        row-order hint), honoured only when it also matches on intensity.

    Returns
    -------
    SmdCoordinateMatch
        The index-pairing key, per-SMD-row recovered coordinates, and the
        matched / unmatched split.

    Raises
    ------
    ValueError
        If ``reference_traces`` row count does not equal ``recovered.n_molecules``.
    """
    from tether.idealize import match_return_leg  # lazy: keep io load graph idealize-free

    reference = np.asarray(reference_traces, dtype=np.float64)
    if reference.ndim != 3 or reference.shape[0] != recovered.n_molecules:
        raise ValueError(
            f"reference_traces must be (N={recovered.n_molecules}, T, 2) aligned with "
            f"the recovered coordinates; got shape {reference.shape}"
        )

    result = match_return_leg(smd_raw, reference, atol=atol, rtol=rtol, id_hint=id_hint)
    mapping = result.mapping
    m = int(mapping.shape[0])

    donor_xy = np.full((m, 2), np.nan, dtype=np.float64)
    acceptor_xy = np.full((m, 2), np.nan, dtype=np.float64)
    matched_rows = mapping >= 0
    donor_xy[matched_rows] = recovered.donor_xy[mapping[matched_rows]]
    acceptor_xy[matched_rows] = recovered.acceptor_xy[mapping[matched_rows]]

    return SmdCoordinateMatch(
        mapping=mapping,
        donor_xy=donor_xy,
        acceptor_xy=acceptor_xy,
        matched=result.matched,
        unmatched=result.unmatched,
    )

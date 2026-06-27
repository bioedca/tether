# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Derive the small committed acceptor-intensity oracle fixture (M0.5 S5/S6, #16).

This completes the M0.5(b) "aperture integration ... comparison to Deep-LASI"
deliverable for the **acceptor** channel, mirroring the donor path in
``make_aperture_fixture.py``. Where the donor sits directly at its
``fret_pairs`` coordinate in the raw frame, the acceptor must be reached through
the dual-view registration:

1. Decode the ``.tmap`` (:func:`tether.imaging.register.read_tmap`).
2. Warp each donor (reference) coordinate into the **acceptor channel's
   full-frame position** with :meth:`TmapChannel.reference_to_channel_image`,
   which folds in the acceptor crop origin (the channels are registered in
   channel-local coordinates; ``tools/processImage.m`` crops ``I(y1:y2, x1:x2)``).
3. Sum-integrate a 21x21 aperture there
   (:func:`tether.imaging.aperture.integrate_traces`) and compare to the
   Deep-LASI raw acceptor trace ``acc``.

**Acceptor signal is sparse in this field.** The acceptor only emits under FRET,
so most molecules carry weak/absent acceptor signal and a broad high-correlation
gate (as for the always-bright donor) is not achievable here — this is a *loose*
M0.5 preview; the strict broad gate is M1 (PRD §4 M1, §9 M1). We therefore commit
the molecules with the **strongest acceptor signal** (top pre-bleach ``acc`` std),
which demonstrate that the warp + aperture recovers the Deep-LASI acceptor
intensity, and validate over the **pre-acceptor-bleach window** (frames up to the
``pacc`` bleach frame) where the acceptor signal actually exists. The full-field
distribution is printed below for honesty.

Regenerate with::

    uv run --no-project --with scipy --with numpy --with tifffile \
        python scripts/make_acceptor_fixture.py
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import numpy as np
import scipy.io as sio
import tifffile

# Use the in-tree package without installing (no local base env).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tether.imaging.aperture import integrate_traces  # noqa: E402
from tether.imaging.register import read_tmap  # noqa: E402


def _find_example_data() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "example-data"
        if candidate.is_dir():
            return candidate
    raise SystemExit("could not locate the external 'example-data' sibling directory")


SRC = _find_example_data() / "bla-uckopsb-tbox-video10"
MOVIE = SRC / "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif"
MAT = SRC / "DeepLASI_MAT_export_Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.mat"
TMAP = SRC / "DeepLASI_MAP_Bla_UCKOPSB_T-box_35pM_tRNA_600nM_20250718_2025-07-18_13-40.tmap"

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "acceptor_oracle.npz"

HALF = 10  # 21x21 aperture window half-width
N_FRAMES = 70  # committed frames per crop (covers the pre-bleach window + margin)
N_MOL = 6  # molecules kept (strongest acceptor signal)
MIN_STD = 80.0  # min pre-bleach acc std to count as "has acceptor signal"
MIN_CORR = 0.7  # floor a committed molecule's pre-bleach correlation must clear


def _round_away(v: np.ndarray) -> np.ndarray:
    return np.sign(v) * np.floor(np.abs(v) + 0.5)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _prebleach(pacc: int) -> slice:
    """Pre-acceptor-bleach correlation window within the committed crop."""
    hi = min(pacc, N_FRAMES) if pacc > 0 else N_FRAMES
    return slice(HALF, hi)


def main() -> None:
    mat = sio.loadmat(MAT)
    fret_pairs = mat["fret_pairs"]
    acc = mat["acc"]
    pacc = mat["pacc"].ravel().astype(int)  # acceptor first-bleach frame (1-based)

    channels = read_tmap(TMAP)
    reference_id = min(channels)
    ref_origin = channels[reference_id].origin
    acceptor_id = max(channels)
    acc_ch = channels[acceptor_id]

    movie = tifffile.memmap(MOVIE)[:N_FRAMES]  # (T, 512, 512) big-endian uint16
    height, width = movie.shape[1:]

    donor0 = fret_pairs[:, 0:2] - 1.0  # 0-based [x, y] full-frame reference coords
    warped = acc_ch.reference_to_channel_image(donor0, reference_origin=ref_origin)
    rows = _round_away(warped[:, 1]).astype(int)
    cols = _round_away(warped[:, 0]).astype(int)

    # Score every in-bounds molecule with enough pre-bleach frames.
    scored: list[tuple[float, float, int]] = []  # (std, corr, molecule)
    all_corr: list[float] = []
    for mol in range(fret_pairs.shape[0]):
        r, c = rows[mol], cols[mol]
        if not (HALF <= r < height - HALF and HALF <= c < width - HALF):
            continue
        sl = _prebleach(pacc[mol])
        if sl.stop - sl.start < 20:  # need a usable pre-bleach window
            continue
        crop = movie[:, r - HALF : r + HALF + 1, c - HALF : c + HALF + 1].astype(np.float64)
        traced = integrate_traces(crop, np.array([[HALF, HALF]])).intensity[0]
        ref = acc[mol, :N_FRAMES]
        std = float(ref[sl].std())
        if traced[sl].std() == 0 or ref[sl].std() == 0:
            continue
        corr = float(np.corrcoef(traced[sl], ref[sl])[0, 1])
        if not np.isfinite(corr):
            continue
        all_corr.append(corr)
        if std >= MIN_STD:
            scored.append((std, corr, mol))

    all_corr_arr = np.array(all_corr)
    print(f"in-bounds molecules with a pre-bleach window: {len(all_corr)}")
    print(
        f"full-field acceptor-correlation distribution: median={np.median(all_corr_arr):.3f} "
        f">=0.7: {(all_corr_arr >= 0.7).sum()}  >=0.8: {(all_corr_arr >= 0.8).sum()}  "
        f">=0.9: {(all_corr_arr >= 0.9).sum()}  (weak/absent acceptor signal dominates the field)"
    )

    # Keep the strongest-signal molecules that clear the correlation floor.
    scored.sort(reverse=True)  # strongest acceptor signal first
    chosen = [(s, cc, mol) for s, cc, mol in scored if cc >= MIN_CORR][:N_MOL]
    if len(chosen) < N_MOL:
        raise SystemExit(
            f"only {len(chosen)} strong-signal molecules cleared corr>={MIN_CORR}; "
            f"loosen MIN_STD/MIN_CORR or raise N_FRAMES"
        )

    crops = np.empty((N_MOL, N_FRAMES, 2 * HALF + 1, 2 * HALF + 1), dtype=movie.dtype)
    acc_ref = np.empty((N_MOL, N_FRAMES), dtype=np.float64)
    mol_idx = np.empty(N_MOL, dtype=np.int32)
    pacc_idx = np.empty(N_MOL, dtype=np.int32)
    warped_xy = np.empty((N_MOL, 2), dtype=np.float64)  # full-frame [x, y] (sub-pixel)
    snapped_xy = np.empty((N_MOL, 2), dtype=np.int32)  # crop-centre pixel [col, row]
    for k, (_std, _cc, mol) in enumerate(chosen):
        r, c = rows[mol], cols[mol]
        crops[k] = movie[:, r - HALF : r + HALF + 1, c - HALF : c + HALF + 1]
        acc_ref[k] = acc[mol, :N_FRAMES]
        mol_idx[k] = mol
        pacc_idx[k] = pacc[mol]
        warped_xy[k] = warped[mol]
        snapped_xy[k] = (c, r)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        OUT,
        crops=crops,
        acc_ref=acc_ref,
        molecule_index=mol_idx,
        pacc=pacc_idx,
        warped_xy=warped_xy,
        snapped_xy=snapped_xy,
        local_center=np.array([HALF, HALF], dtype=np.int32),  # (row, col) of the spot in each crop
    )

    print(f"\nwrote {OUT} ({OUT.stat().st_size} B)")
    print(f"source movie sha256: {_sha256(MOVIE)}")
    print(f"source .mat sha256:  {_sha256(MAT)}")
    print(f"source .tmap sha256: {_sha256(TMAP)}")
    print(
        f"acceptor channel {acceptor_id} origin (0-based [x,y]) = {acc_ch.origin.tolist()}; "
        f"reference channel {reference_id} origin = {ref_origin.tolist()}"
    )
    for (std, cc, mol), k in zip(chosen, range(N_MOL), strict=True):
        print(
            f"  fixture[{k}] = molecule {mol} @ warped (x={warped_xy[k, 0]:.2f}, "
            f"y={warped_xy[k, 1]:.2f}) pacc={pacc_idx[k]} acc-std={std:.0f} corr={cc:.3f}"
        )


if __name__ == "__main__":
    main()

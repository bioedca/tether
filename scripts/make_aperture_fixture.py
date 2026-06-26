# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Derive the small committed aperture-integration oracle fixture (M0.5 S5 PR-2).

The aperture + Sum-integration validation (PRD Appendix E Stages 5, 11-15; §11.2)
needs a *real-data* oracle: the Deep-LASI ``.mat`` export carries the raw
integrated donor trace ``don`` for every molecule, so we crop the donor
neighbourhood of a handful of molecules out of the ~0.9 GB UCKOPSB movie and
pair each crop with its oracle ``don`` slice. The crops are tiny (21x21 = the
aperture window) and committed; the full movie + ``.mat`` stay external
(PLAN §2.1/§2.2).

Coordinate convention (verified against the oracle): ``fret_pairs`` columns are
``[x_donor, y_donor, x_acc, y_acc]`` with ``x`` = column, ``y`` = row, 0-based
in MATLAB's 1-based frame -> we subtract 1. The donor signal sits at
``(row=y_donor, col=x_donor)`` directly in the raw frame; the acceptor channel
is *registration-mapped* (a ``.tmap`` apply) and is intentionally out of scope
here (it rides the M0.5 S6 ``.tdat``/``.tmap`` decode).

Regenerate with::

    uv run --no-project --with scipy --with numpy --with tifffile \
        python scripts/make_aperture_fixture.py
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import scipy.io as sio
import tifffile
from scipy.ndimage import uniform_filter1d


def _find_example_data() -> Path:
    """Locate the read-only ``example-data`` sibling by walking up from here.

    Robust to running from either the main checkout (``Tether/scripts``) or a
    linked worktree (``Tether/.claude/worktrees/<branch>/scripts``).
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "example-data"
        if candidate.is_dir():
            return candidate
    raise SystemExit("could not locate the external 'example-data' sibling directory")


# External read-only source (never committed).
SRC = _find_example_data() / "bla-uckopsb-tbox-video10"
MOVIE = SRC / "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif"
MAT = SRC / "DeepLASI_MAT_export_Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.mat"

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "aperture_oracle.npz"

HALF = 10  # 21x21 aperture window half-width
N_FRAMES = 120  # committed frames per crop
N_MOL = 6  # molecules kept
MIN_CORR = 0.9  # donor-trace correlation a molecule must clear to be kept


def _aperture_masks() -> tuple[np.ndarray, np.ndarray]:
    yy, xx = np.mgrid[0 : 2 * HALF + 1, 0 : 2 * HALF + 1]
    dist = np.hypot(yy - HALF, xx - HALF)
    return dist <= 3, (dist > 6) & (dist <= 8)


def _integrate(crop: np.ndarray, disk: np.ndarray, ring: np.ndarray) -> np.ndarray:
    """Reference Sum integration used only to *score* candidate molecules."""
    bg = uniform_filter1d(crop, size=10, axis=0, mode="nearest", origin=0)
    tot = (crop * disk).sum(axis=(1, 2))
    ringvals = bg[:, ring]
    bgmean = np.array([rv[rv > 0].mean() if np.any(rv > 0) else 0.0 for rv in ringvals])
    return tot - bgmean * disk.sum()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    mat = sio.loadmat(MAT)
    fret_pairs = mat["fret_pairs"]
    don = mat["don"]
    disk, ring = _aperture_masks()

    movie = tifffile.memmap(MOVIE)[:N_FRAMES]  # (T, 512, 512) big-endian uint16
    height, width = movie.shape[1:]

    interior = slice(HALF, N_FRAMES - HALF)  # frames whose 10-frame bg window is in-crop
    scored: list[tuple[float, int, int, int]] = []  # (corr, mol, row, col)
    for mol in range(fret_pairs.shape[0]):
        col = int(round(fret_pairs[mol, 0])) - 1  # MATLAB 1-based -> 0-based
        row = int(round(fret_pairs[mol, 1])) - 1
        if not (HALF <= row < height - HALF and HALF <= col < width - HALF):
            continue
        crop = movie[:, row - HALF : row + HALF + 1, col - HALF : col + HALF + 1].astype(np.float64)
        traced = _integrate(crop, disk, ring)
        ref = don[mol, :N_FRAMES]
        corr = float(np.corrcoef(traced[interior], ref[interior])[0, 1])
        if corr >= MIN_CORR:
            scored.append((corr, mol, row, col))

    scored.sort(reverse=True)  # brightest correlation first
    chosen = scored[:N_MOL]
    if len(chosen) < N_MOL:
        raise SystemExit(f"only {len(chosen)} molecules cleared corr>={MIN_CORR}; loosen criteria")

    crops = np.empty((N_MOL, N_FRAMES, 2 * HALF + 1, 2 * HALF + 1), dtype=np.uint16)
    don_ref = np.empty((N_MOL, N_FRAMES), dtype=np.float64)
    mol_idx = np.empty(N_MOL, dtype=np.int32)
    full_xy = np.empty((N_MOL, 2), dtype=np.int32)  # (col, row) = (x, y), 0-based
    for k, (_corr, mol, row, col) in enumerate(chosen):
        crops[k] = movie[:, row - HALF : row + HALF + 1, col - HALF : col + HALF + 1]
        don_ref[k] = don[mol, :N_FRAMES]
        mol_idx[k] = mol
        full_xy[k] = (col, row)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        OUT,
        crops=crops,
        don_ref=don_ref,
        molecule_index=mol_idx,
        full_xy=full_xy,
        local_center=np.array([HALF, HALF], dtype=np.int32),  # (row, col) of the spot in each crop
    )

    print(f"wrote {OUT} ({OUT.stat().st_size} B)")
    print(f"source movie sha256: {_sha256(MOVIE)}")
    print(f"source .mat sha256:  {_sha256(MAT)}")
    for (corr, mol, row, col), k in zip(chosen, range(N_MOL), strict=True):
        print(f"  fixture[{k}] = molecule {mol} @ (row={row}, col={col})  donor-corr={corr:.3f}")


if __name__ == "__main__":
    main()

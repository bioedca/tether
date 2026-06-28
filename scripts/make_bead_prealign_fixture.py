# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Derive the committed 4-DOF prealign oracle fixture (M1 S5b, #35-followup).

Closes the bead-image-pair data-gap that ADR-0012 flagged for the S5b
Fourier-Mellin similarity prealign (now staged by the maintainer at
``example-data/2bla-uckopsb-bead-calibration-20250721/``).

The fixture pairs the two **real** bead-calibration channel images with the
**ground-truth** donor<->acceptor similarity *derived from the paired ``.tmap``*:

* ``donor`` / ``acceptor`` -- 256x256 centred crops of the left (donor, cols
  0-255) and right (acceptor, cols 256-511) halves of ``map.tif`` (a single
  512x512 bead field split L/R, exactly like the UCKOPSB movie). The estimator
  under test, :func:`tether.imaging.register.estimate_similarity_prealign`, runs
  on these and must report the true (near-identity) channel similarity.
* ``gt_scale`` / ``gt_rotation_deg`` / ``gt_translation`` -- the **acceptor ->
  donor** similarity (the direction the estimator returns: reference=donor,
  moving=acceptor) in the crop's local ``[x, y]`` frame, obtained by sampling the
  decoded ``.tmap`` polynomial on a grid and least-squares (Umeyama) fitting a
  similarity. This is the real geometric oracle.

**Why the image only validates the near-identity regime.** ``map.tif`` is a
contrast-stretched, partly-saturated uint8 *display* export, so its FFT magnitude
is DC-dominated and cannot validate large-rotation/scale *recovery* (verified:
saturating an otherwise-recoverable synthetic bead field to uint8 breaks
recovery). The real channel relationship is itself near-identity (rotation
~0.085 deg, scale ~0.15% off unity), so there is no large warp to recover here
anyway. Large-warp *recovery* of the estimator is therefore unit-tested on a
deterministic non-saturated synthetic bead field (``test_register_prealign.py``);
this fixture validates that the estimator reports the **true similarity on real
bead images** (a no-op estimator returning translation [0,0] fails the ~7.5 px
real translation check).

Regenerate with::

    uv run --no-project --with scipy --with numpy --with scikit-image --with tifffile \
        python scripts/make_bead_prealign_fixture.py
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import numpy as np
import tifffile
from skimage.transform import SimilarityTransform

# Use the in-tree package without installing (no local base env).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tether.imaging.register import (  # noqa: E402
    estimate_similarity_prealign,
    read_tmap,
)

CROP = (slice(128, 384), slice(0, 256))  # 256x256 centred rows; donor cols
DONOR_OFFSET = np.array([0, 128], dtype=np.int32)  # [col, row] of donor crop in full frame
ACCEPTOR_OFFSET = np.array([256, 128], dtype=np.int32)  # [col, row] of acceptor crop


def _find_example_data() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "example-data"
        if candidate.is_dir():
            return candidate
    raise SystemExit("could not locate the external 'example-data' sibling directory")


SRC = _find_example_data() / "2bla-uckopsb-bead-calibration-20250721"
TIF = SRC / "map.tif"
TMAP = SRC / "DeepLASI_MAP_2bla_UCKOPSB_35pM_tRNA_600nM_20250721_2025-07-21_15-36.tmap"
OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "bead_prealign_oracle.npz"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    img = tifffile.imread(TIF)
    if img.ndim == 3:  # pseudo-RGB save artefact: 3 byte-identical planes
        if img.shape[0] == 3:  # sample-first (C, H, W)
            img = img[0]
        elif img.shape[-1] == 3:  # sample-last (H, W, C)
            img = img[..., 0]
        else:
            raise SystemExit(f"unexpected 3-D map.tif shape {img.shape}")
    donor = np.ascontiguousarray(img[CROP[0], 0:256])
    acceptor = np.ascontiguousarray(img[CROP[0], 256:512])
    assert donor.shape == (256, 256) and acceptor.shape == (256, 256)  # noqa: S101
    assert donor.dtype == np.uint8  # noqa: S101

    channels = read_tmap(TMAP)
    reference_id, acceptor_id = min(channels), max(channels)
    ref_origin = channels[reference_id].origin
    acc_ch = channels[acceptor_id]

    # Ground-truth crop-frame similarity from the .tmap polynomial: sample a grid of
    # donor crop-local points, map donor full-frame -> acceptor full-frame, express in
    # the acceptor crop-local frame, then Umeyama-fit acceptor -> donor (estimator dir).
    g = np.linspace(20, 235, 16)
    gx, gy = np.meshgrid(g, g)
    donor_local = np.column_stack([gx.ravel(), gy.ravel()])  # [x, y] in donor crop
    donor_full = donor_local + DONOR_OFFSET
    acc_full = acc_ch.reference_to_channel_image(donor_full, reference_origin=ref_origin)
    acc_local = acc_full - ACCEPTOR_OFFSET

    tf = SimilarityTransform.from_estimate(acc_local, donor_local)  # acceptor-local -> donor-local
    if not tf:
        raise SystemExit("Umeyama similarity fit of the .tmap grid failed")
    residual = float(np.sqrt(((tf(acc_local) - donor_local) ** 2).sum(1)).mean())
    gt_scale = float(tf.scale)
    gt_rotation_deg = float(np.rad2deg(tf.rotation))
    gt_translation = np.asarray(tf.translation, dtype=np.float64)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        OUT,
        donor=donor,
        acceptor=acceptor,
        gt_scale=np.float64(gt_scale),
        gt_rotation_deg=np.float64(gt_rotation_deg),
        gt_translation=gt_translation,
        donor_crop_offset=DONOR_OFFSET,
        acceptor_crop_offset=ACCEPTOR_OFFSET,
    )

    # Self-proving check: the estimator under test recovers the ground truth.
    est = estimate_similarity_prealign(donor.astype(np.float64), acceptor.astype(np.float64))
    est_rot = float(np.rad2deg(est.rotation))

    print(f"wrote {OUT} ({OUT.stat().st_size} B)")
    print(f"source map.tif sha256: {_sha256(TIF)}")
    print(f"source .tmap sha256:   {_sha256(TMAP)}")
    print(f"source sizes: map.tif={TIF.stat().st_size} B  .tmap={TMAP.stat().st_size} B")
    print(
        f"ground truth (acceptor->donor, crop frame): scale={gt_scale:.5f} "
        f"rot={gt_rotation_deg:+.4f} deg t=({gt_translation[0]:+.3f}, {gt_translation[1]:+.3f}) "
        f"[.tmap grid similarity-fit residual {residual:.3f} px]"
    )
    print(
        f"estimator on the real pair:                 scale={est.scale:.5f} "
        f"rot={est_rot:+.4f} deg t=({est.translation[0]:+.3f}, {est.translation[1]:+.3f})"
    )
    print(
        f"agreement: dscale={abs(est.scale - gt_scale):.5f} "
        f"drot={abs(est_rot - gt_rotation_deg):.4f} deg "
        f"dt={np.hypot(*(est.translation - gt_translation)):.3f} px"
    )


if __name__ == "__main__":
    main()

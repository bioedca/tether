# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Acceptor-channel intensity via the ``.tmap`` apply (PRD App. E Stages 11-15; M0.5 S5/S6, #16).

Completes the M0.5(b) "aperture integration ... comparison to Deep-LASI"
deliverable for the **acceptor** channel (the donor half landed in
``test_aperture.py``). The acceptor is reached through the dual-view
registration, so this also locks the channel-local -> full-frame mapping:

* :attr:`TmapChannel.origin` recovers the channel crop origin and
  :meth:`TmapChannel.reference_to_channel_image` warps a full-frame reference
  (donor) coordinate into the acceptor channel's full-frame position (folding in
  the crop origin; Deep-LASI registers channel-local sub-images,
  ``tools/processImage.m``);
* the extracted acceptor traces correlate with the Deep-LASI raw ``acc`` oracle
  on a committed crop of the real UCKOPSB movie.

The acceptor only emits under FRET, so most molecules in this field carry weak or
absent acceptor signal — a broad high-correlation gate (as for the always-bright
donor) is not achievable here. The committed fixture holds the strongest-signal
molecules, validated over the **pre-acceptor-bleach window** where the signal
exists; this is the *loose* M0.5 preview (PRD §4 M1 / §9 M1 set the strict bar).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")

import numpy as np  # noqa: E402

from tether.imaging.aperture import integrate_traces  # noqa: E402
from tether.imaging.register import PolyTransform2D, TmapChannel  # noqa: E402

ORACLE = Path(__file__).resolve().parent / "fixtures" / "acceptor_oracle.npz"


def _identity_transform() -> PolyTransform2D:
    """A degree-2 transform that is the identity (out_x = x, out_y = y)."""
    return PolyTransform2D(
        a=np.array([0.0, 1.0, 0.0, 0.0, 0.0, 0.0]),  # x_out = x
        b=np.array([0.0, 0.0, 1.0, 0.0, 0.0, 0.0]),  # y_out = y
        norm_xy=np.eye(3),
        norm_uv=np.eye(3),
    )


def _channel(crop: np.ndarray) -> TmapChannel:
    ident = _identity_transform()
    return TmapChannel(
        channel_id=2,
        crop=crop,
        map_particles=np.zeros((0, 2)),
        ref_to_channel=ident,
        channel_to_ref=ident,
    )


# --- channel-local -> full-frame mapping ------------------------------------


def test_channel_origin_from_crop() -> None:
    # Deep-LASI Crop = [[y1, x1], [y2, x2]] (1-based, inclusive); origin is 0-based [x1-1, y1-1].
    ch = _channel(np.array([[1, 257], [512, 512]]))  # the real acceptor (right) half
    np.testing.assert_array_equal(ch.origin, [256.0, 0.0])
    ch2 = _channel(np.array([[5, 17], [260, 400]]))
    np.testing.assert_array_equal(ch2.origin, [16.0, 4.0])


def test_channel_origin_rejects_malformed_crop() -> None:
    with pytest.raises(ValueError, match="4 elements"):
        _ = _channel(np.array([1, 257, 512])).origin


def test_reference_to_channel_image_folds_in_origin() -> None:
    ch = _channel(np.array([[1, 257], [512, 512]]))  # origin (256, 0)
    pts = np.array([[10.0, 20.0], [100.5, 200.25]])
    # Identity warp + reference origin (0, 0): full-frame acceptor = pt + channel origin.
    got = ch.reference_to_channel_image(pts)
    np.testing.assert_allclose(got, pts + np.array([256.0, 0.0]))
    # A non-zero reference origin is subtracted before the warp.
    got2 = ch.reference_to_channel_image(pts, reference_origin=(3.0, 7.0))
    np.testing.assert_allclose(got2, pts - np.array([3.0, 7.0]) + np.array([256.0, 0.0]))


def test_reference_to_channel_image_accepts_single_point() -> None:
    ch = _channel(np.array([[1, 257], [512, 512]]))
    got = ch.reference_to_channel_image([10.0, 20.0])
    np.testing.assert_allclose(got, [[266.0, 20.0]])


# --- real-data oracle: acceptor traces vs Deep-LASI raw `acc` ---------------


def test_acceptor_intensity_matches_deeplasi_acc_oracle() -> None:
    data = np.load(ORACLE)
    crops = data["crops"]  # (N, T, 21, 21) uint16, big-endian movie pixels
    assert crops.dtype == np.dtype(">u2")  # source byte order preserved (not byte-swapped)
    acc_ref = data["acc_ref"]  # (N, T) Deep-LASI raw acceptor intensity
    pacc = data["pacc"]  # (N,) acceptor first-bleach frame (1-based)
    n_mol, n_frames = acc_ref.shape
    centre = tuple(int(v) for v in data["local_center"])  # (row, col) of the spot
    assert centre == (10, 10)
    # Every committed molecule is warped into the acceptor (right) half (x > 256).
    assert np.all(data["warped_xy"][:, 0] > 256.0)

    corrs = []
    for m in range(n_mol):
        res = integrate_traces(crops[m], [[centre[1], centre[0]]])  # coord [x=col, y=row]
        assert res.valid[0]
        # Correlate over the pre-bleach window (>=10 so the 10-frame temporal-MA
        # background is in-crop; up to the acceptor bleach frame, where signal exists).
        hi = min(int(pacc[m]), n_frames) if pacc[m] > 0 else n_frames
        sl = slice(10, hi)
        assert sl.stop - sl.start >= 20
        corrs.append(float(np.corrcoef(res.intensity[0, sl], acc_ref[m, sl])[0, 1]))

    corrs = np.array(corrs)
    # Strongest-acceptor-signal molecules: the .tmap apply + aperture recovers the
    # Deep-LASI acceptor intensity. Loose M0.5 preview (per §9 M0.5(b)); the broad
    # M1 gate is stricter. The best molecule tracks the oracle tightly.
    assert np.all(corrs >= 0.70), f"per-molecule acceptor corr below 0.70: {corrs}"
    assert np.median(corrs) >= 0.80, f"median acceptor corr {np.median(corrs):.3f} < 0.80"
    assert corrs.max() >= 0.95, f"best acceptor corr {corrs.max():.3f} < 0.95"

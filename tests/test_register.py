# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Dual-view registration (PRD Appendix E Stages 6-10; §9 M0.5(b); M0.5 S6).

Locks the registration half of the M0.5(b) acceptance:

* the :class:`PolyTransform2D` degree-2 warp math (apply / round-trip / fit
  recovery);
* the §9 M0.5(b) gate on real data: a *native* degree-2 fit from the committed
  ``.tdat`` colocalized molecule pairs reproduces Deep-LASI's imported ``.tmap``
  registration to RMS <= 0.5 px (and within 1 px for >= 95% of molecules), using
  the small committed decoded-coefficient fixture; and
* (data-present only) the ``.tmap`` MCOS decoder itself, checked against that
  fixture when the external ``.tmap`` is available.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")  # tether.imaging.register imports scipy.io

import numpy as np  # noqa: E402

from tether.imaging.register import (  # noqa: E402
    PairedControlPoints,
    PolyTransform2D,
    SimilarityTransform2D,
    TmapChannel,
    estimate_translation_prealign,
    fit_polynomial_transform,
    pair_control_points,
    point_rms,
    read_tmap,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "tmap_coeffs.npz"
TDAT_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "tdat_coloc_slice.tdat"

_IDENTITY_A = np.array([0.0, 1.0, 0.0, 0.0, 0.0, 0.0])
_IDENTITY_B = np.array([0.0, 0.0, 1.0, 0.0, 0.0, 0.0])


def _identity_transform() -> PolyTransform2D:
    return PolyTransform2D(a=_IDENTITY_A, b=_IDENTITY_B, norm_xy=np.eye(3), norm_uv=np.eye(3))


def _load_tmap_fixture() -> tuple[int, dict[int, TmapChannel]]:
    data = np.load(FIXTURE)
    reference = int(data["reference_channel"])
    channels: dict[int, TmapChannel] = {}
    for cid in data["channel_ids"].tolist():

        def _poly(name: str, cid: int = cid) -> PolyTransform2D:
            return PolyTransform2D(
                a=data[f"c{cid}_{name}_a"],
                b=data[f"c{cid}_{name}_b"],
                norm_xy=data[f"c{cid}_{name}_norm_xy"],
                norm_uv=data[f"c{cid}_{name}_norm_uv"],
            )

        channels[cid] = TmapChannel(
            channel_id=cid,
            crop=data[f"c{cid}_crop"],
            map_particles=data[f"c{cid}_map_particles"],
            ref_to_channel=_poly("ref_to_channel"),
            channel_to_ref=_poly("channel_to_ref"),
        )
    return reference, channels


# --- PolyTransform2D math ----------------------------------------------------


def test_identity_transform_is_a_no_op() -> None:
    pts = np.array([[0.0, 0.0], [10.5, 200.0], [255.0, 511.0]])
    np.testing.assert_allclose(_identity_transform().apply(pts), pts, atol=1e-12)


def test_apply_rejects_bad_shape() -> None:
    with pytest.raises(ValueError, match=r"\(N, 2\)"):
        _identity_transform().apply(np.zeros((3, 3)))


@pytest.mark.parametrize("bad", ["a", "b", "norm_xy", "norm_uv"])
def test_transform_validates_field_shapes(bad: str) -> None:
    kwargs = {"a": _IDENTITY_A, "b": _IDENTITY_B, "norm_xy": np.eye(3), "norm_uv": np.eye(3)}
    kwargs[bad] = np.zeros(7) if bad in ("a", "b") else np.zeros((2, 2))
    with pytest.raises(ValueError, match=bad):
        PolyTransform2D(**kwargs)


def test_fit_recovers_a_known_degree2_map() -> None:
    # A genuine degree-2 map (affine + small quadratic terms) sampled exactly.
    known = PolyTransform2D(
        a=np.array([1.0, 1.01, 0.002, 1e-4, -3e-5, 2e-5]),
        b=np.array([-2.0, 0.003, 0.99, 5e-5, 1e-4, -2e-5]),
        norm_xy=np.eye(3),
        norm_uv=np.eye(3),
    )
    rng = np.random.RandomState(1)
    src = rng.uniform([0, 0], [200, 400], (60, 2))
    dst = known.apply(src)
    fit = fit_polynomial_transform(src, dst)
    np.testing.assert_allclose(fit.apply(src), dst, atol=1e-6)  # fits its own points
    held_out = rng.uniform([0, 0], [200, 400], (40, 2))
    np.testing.assert_allclose(fit.apply(held_out), known.apply(held_out), atol=1e-4)


def test_fit_rejects_too_few_points() -> None:
    with pytest.raises(ValueError, match="6 control points"):
        fit_polynomial_transform(np.zeros((5, 2)), np.zeros((5, 2)))


def test_fit_rejects_mismatched_shapes() -> None:
    with pytest.raises(ValueError, match="matching"):
        fit_polynomial_transform(np.zeros((6, 2)), np.zeros((7, 2)))


def test_fit_rejects_degenerate_control_points() -> None:
    # Enough points but collinear (x == y) -> rank-deficient degree-2 design matrix.
    line = np.arange(8, dtype=float)
    src = np.column_stack([line, line])
    dst = np.column_stack([line * 1.1, line - 2.0])
    with pytest.raises(ValueError, match="non-degenerate"):
        fit_polynomial_transform(src, dst)


def test_point_rms_known_value() -> None:
    a = np.array([[0.0, 0.0], [3.0, 4.0]])
    b = np.zeros((2, 2))
    assert point_rms(a, b) == pytest.approx(np.sqrt(12.5))


def test_point_rms_rejects_empty() -> None:
    with pytest.raises(ValueError, match="zero points"):
        point_rms(np.empty((0, 2)), np.empty((0, 2)))


# --- decoded .tmap fixture ---------------------------------------------------


def test_reference_channel_map_is_identity() -> None:
    reference, channels = _load_tmap_fixture()
    rng = np.random.RandomState(0)
    grid = rng.uniform([10, 10], [240, 500], (200, 2))
    ref = channels[reference]
    # Exercise the public 0-based API (not the raw 1-based transform).
    assert point_rms(ref.reference_to_channel(grid), grid) < 1e-6


def test_decoded_forward_inverse_round_trip() -> None:
    reference, channels = _load_tmap_fixture()
    rng = np.random.RandomState(2)
    grid = rng.uniform([10, 10], [240, 500], (200, 2))
    for cid, ch in channels.items():
        if cid == reference:
            continue
        back = ch.channel_to_reference(ch.reference_to_channel(grid))
        assert point_rms(back, grid) < 0.1  # independently fitted fwd/inv


def test_native_fit_reproduces_tmap_within_tolerance() -> None:
    """§9 M0.5(b) registration gate: a native degree-2 fit reproduces the imported
    .tmap to RMS <= 0.5 px.

    This is registration *faithfulness* (native fit vs imported map agree), not
    colocalization recall: the ">=95% of molecules matched within 1 px" recall
    criterion is the M1 detection+colocalization deliverable (the .tmap's own
    residual to the actual acceptor molecules has median > 1 px). Here ">=95%
    within 1 px" means the native fit and the imported map agree to within 1 px at
    >=95% of the Deep-LASI molecule positions.
    """
    h5py = pytest.importorskip("h5py")
    assert h5py  # used transitively by read_tdat
    from tether.io import read_tdat

    reference, channels = _load_tmap_fixture()
    tdat = read_tdat(TDAT_FIXTURE)
    coords = tdat.colocalization.coords
    donor = coords[tdat.reference_channel]  # reference-frame 0-based [x, y]
    checked = 0
    for cid, ch in channels.items():
        if cid == reference:
            continue
        acceptor = coords[cid - 1]  # ChannelID -> 0-based coord key
        native = fit_polynomial_transform(donor, acceptor)  # reference -> channel
        native_pred = native.apply(donor)
        tmap_pred = ch.reference_to_channel(donor)
        agreement = np.linalg.norm(native_pred - tmap_pred, axis=1)
        assert point_rms(native_pred, tmap_pred) <= 0.5  # native-vs-.tmap RMS gate
        assert np.mean(agreement <= 1.0) >= 0.95  # native agrees with .tmap within 1 px
        checked += 1
    assert checked >= 1


# --- the MCOS decoder (data-present only) -------------------------------------


def _find_example_tmap() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        candidate = (
            parent
            / "example-data"
            / "bla-uckopsb-tbox-video10"
            / "DeepLASI_MAP_Bla_UCKOPSB_T-box_35pM_tRNA_600nM_20250718_2025-07-18_13-40.tmap"
        )
        if candidate.is_file():
            return candidate
    return None


_EXAMPLE_TMAP = _find_example_tmap()


@pytest.mark.skipif(_EXAMPLE_TMAP is None, reason="external .tmap not present (default checkout)")
def test_read_tmap_matches_committed_fixture() -> None:
    decoded = read_tmap(_EXAMPLE_TMAP)
    _reference, fixture = _load_tmap_fixture()
    assert sorted(decoded) == sorted(fixture)
    for cid, expected in fixture.items():
        got = decoded[cid]
        np.testing.assert_array_equal(got.crop, expected.crop)
        np.testing.assert_allclose(got.map_particles, expected.map_particles, atol=1e-9)
        for name in ("ref_to_channel", "channel_to_ref"):
            exp_t = getattr(expected, name)
            got_t = getattr(got, name)
            np.testing.assert_allclose(got_t.a, exp_t.a, atol=1e-9)
            np.testing.assert_allclose(got_t.b, exp_t.b, atol=1e-9)
            np.testing.assert_allclose(got_t.norm_xy, exp_t.norm_xy, atol=1e-9)
            np.testing.assert_allclose(got_t.norm_uv, exp_t.norm_uv, atol=1e-9)


# --- prealign + NN pairing (M1 S5; Appendix E Stages 7-8) --------------------


def _bead_image(
    points: np.ndarray, shape: tuple[int, int] = (128, 128), sigma: float = 1.6
) -> np.ndarray:
    """A normalized [0, 1] image with a Gaussian blob at each ``[x, y]`` point."""
    yy, xx = np.mgrid[0 : shape[0], 0 : shape[1]]
    img = np.zeros(shape, dtype=float)
    for x, y in np.atleast_2d(points):
        img += np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma**2))
    peak = img.max()
    return img / peak if peak > 0 else img


# SimilarityTransform2D math


def test_similarity_identity_is_a_no_op() -> None:
    pts = np.array([[0.0, 0.0], [10.5, 200.0], [255.0, 511.0]])
    tf = SimilarityTransform2D(scale=1.0, rotation=0.0, translation=np.zeros(2))
    np.testing.assert_allclose(tf.apply(pts), pts, atol=1e-12)


def test_similarity_translation_only() -> None:
    pts = np.array([[1.0, 2.0], [3.0, 4.0]])
    tf = SimilarityTransform2D(scale=1.0, rotation=0.0, translation=np.array([5.0, -3.0]))
    np.testing.assert_allclose(tf.apply(pts), pts + [5.0, -3.0])


def test_similarity_rotation_and_scale() -> None:
    # 90deg CCW, scale 2: [1, 0] -> 2*[0, 1] = [0, 2]; then translate by [10, 20].
    tf = SimilarityTransform2D(scale=2.0, rotation=np.pi / 2, translation=np.array([10.0, 20.0]))
    np.testing.assert_allclose(tf.apply(np.array([[1.0, 0.0]])), [[10.0, 22.0]], atol=1e-12)


def test_similarity_validates_translation_shape() -> None:
    with pytest.raises(ValueError, match="translation"):
        SimilarityTransform2D(scale=1.0, rotation=0.0, translation=np.zeros(3))


def test_similarity_apply_rejects_bad_shape() -> None:
    tf = SimilarityTransform2D(scale=1.0, rotation=0.0, translation=np.zeros(2))
    with pytest.raises(ValueError, match=r"\(N, 2\)"):
        tf.apply(np.zeros((3, 3)))


# estimate_translation_prealign (phase correlation; needs scikit-image)


def test_translation_prealign_recovers_known_shift() -> None:
    pytest.importorskip("skimage")
    rng = np.random.RandomState(0)
    pts = rng.uniform([30, 30], [98, 98], (25, 2))
    dx, dy = 7.0, -4.0
    reference = _bead_image(pts)
    moving = _bead_image(pts + [dx, dy])  # content displaced by [dx, dy]
    prealign = estimate_translation_prealign(reference, moving)
    assert prealign.scale == 1.0
    assert prealign.rotation == 0.0
    # The prealign maps moving -> reference, i.e. translation ~ -[dx, dy] ...
    np.testing.assert_allclose(prealign.translation, [-dx, -dy], atol=0.6)
    # ... and it actually re-aligns the displaced points onto the reference points.
    assert point_rms(prealign.apply(pts + [dx, dy]), pts) < 0.6


def test_translation_prealign_validates_inputs() -> None:
    pytest.importorskip("skimage")
    img = np.zeros((16, 16))
    with pytest.raises(ValueError, match="same shape"):
        estimate_translation_prealign(img, np.zeros((16, 8)))
    with pytest.raises(ValueError, match="2-D"):
        estimate_translation_prealign(np.zeros(16), img)
    with pytest.raises(ValueError, match="upsample_factor"):
        estimate_translation_prealign(img, img, upsample_factor=0)


# pair_control_points


def test_pairing_recovers_correspondence_on_real_tdat() -> None:
    h5py = pytest.importorskip("h5py")
    assert h5py  # used transitively by read_tdat
    from tether.io import read_tdat

    tdat = read_tdat(TDAT_FIXTURE)
    reference = tdat.colocalization.coords[tdat.reference_channel]  # (250, 2) [x, y]
    shift = np.array([5.0, -3.0])
    rng = np.random.RandomState(0)
    perm = rng.permutation(len(reference))
    moving = reference[perm] + shift  # shuffled + displaced "moving" cloud
    prealign = SimilarityTransform2D(scale=1.0, rotation=0.0, translation=-shift)

    paired = pair_control_points(reference, moving, tol=2.0, prealign=prealign)

    assert isinstance(paired, PairedControlPoints)
    assert len(paired.reference) == len(reference)  # every molecule matched
    # Unique one-to-one: no reference and no moving point assigned twice.
    assert len(set(paired.reference_index.tolist())) == len(reference)
    assert len(set(paired.moving_index.tolist())) == len(reference)
    # Each kept pair is the planted correspondence.
    np.testing.assert_array_equal(perm[paired.moving_index], paired.reference_index)
    # Fit-on-original: returned moving are the ORIGINAL (displaced) coords, not the
    # prealigned ones -> reference + shift == returned moving for every pair.
    np.testing.assert_allclose(paired.reference + shift, paired.moving, atol=1e-9)


def test_pairing_is_mutual_no_double_assignment() -> None:
    # Two moving points near one reference: a greedy gate maps both; mutual keeps
    # only the closer one (no double-assignment).
    reference = np.array([[0.0, 0.0], [100.0, 100.0]])
    moving = np.array([[0.5, 0.0], [0.6, 0.0]])
    paired = pair_control_points(reference, moving, tol=2.0)
    assert paired.reference_index.tolist() == [0]
    assert paired.moving_index.tolist() == [0]


def test_pairing_drops_unmatched_and_respects_tol() -> None:
    reference = np.array([[0.0, 0.0], [50.0, 50.0]])
    moving = np.array([[0.1, 0.0], [10.0, 10.0]])  # mov0 pairs ref0; mov1 too far
    paired = pair_control_points(reference, moving, tol=2.0)
    assert paired.reference_index.tolist() == [0]
    assert paired.moving_index.tolist() == [0]
    # Gate is inclusive at exactly tol (faithful to findPairs.m `D <= tol`).
    ref = np.array([[0.0, 0.0]])
    assert len(pair_control_points(ref, np.array([[2.5, 0.0]]), tol=2.0).reference) == 0
    assert len(pair_control_points(ref, np.array([[2.0, 0.0]]), tol=2.0).reference) == 1
    assert len(pair_control_points(ref, np.array([[1.5, 0.0]]), tol=2.0).reference) == 1


def test_pairing_empty_inputs() -> None:
    ref = np.array([[1.0, 2.0]])
    for paired in (
        pair_control_points(np.empty((0, 2)), ref),
        pair_control_points(ref, np.empty((0, 2))),
    ):
        assert paired.reference.shape == (0, 2)
        assert paired.moving.shape == (0, 2)
        assert paired.reference_index.shape == (0,)


def test_pairing_validates_inputs() -> None:
    with pytest.raises(ValueError, match=r"\(N, 2\)"):
        pair_control_points(np.zeros((3, 3)), np.zeros((3, 2)))
    with pytest.raises(ValueError, match="tol"):
        pair_control_points(np.zeros((3, 2)), np.zeros((3, 2)), tol=0.0)


def test_prealign_pair_fit_chain_on_synthetic_beads() -> None:
    pytest.importorskip("skimage")
    from tether.imaging.detect import detect_spots

    rng = np.random.RandomState(3)
    pts = rng.uniform([20, 20], [108, 108], (20, 2))
    dx, dy = 6.0, 5.0
    reference = _bead_image(pts)
    moving = _bead_image(pts + [dx, dy])
    ref_spots = detect_spots(reference)
    mov_spots = detect_spots(moving)
    prealign = estimate_translation_prealign(reference, moving)
    paired = pair_control_points(ref_spots, mov_spots, tol=2.0, prealign=prealign)
    # Most beads pair, and a degree-2 fit on the ORIGINAL moving coords recovers
    # the moving -> reference map to sub-pixel RMS.
    assert len(paired.reference) >= 15
    fit = fit_polynomial_transform(paired.moving, paired.reference)
    assert point_rms(fit.apply(paired.moving), paired.reference) < 0.5

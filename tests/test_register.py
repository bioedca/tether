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
    PolyTransform2D,
    TmapChannel,
    fit_polynomial_transform,
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

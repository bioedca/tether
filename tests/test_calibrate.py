# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Registration calibration: map + RMS gate + persistence (PRD App E Stages 9-10; M1 S6).

Locks the S6 registration deliverables on top of the M0.5 fit/decode primitives:

* the :class:`RegistrationMap` object + over-gate verdict (§7.1);
* :func:`fit_registration_map` -- degree-2 native fit, similarity fallback, the
  numeric RMS residual, and the over-gate branch (warn-and-flag vs fail-movie,
  never-drop);
* :func:`save_map` / :func:`load_map` map-file round-trip (no pickled objects);
* :func:`write_calibration` / :func:`read_calibration` additive ``/calibration``
  persistence (the M0 schema freeze stays intact);
* the imported-``.tmap`` path and the **apply-both parity** (§7.1; §9 M1): an
  imported map warps to the same coordinates as a native fit on real data.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")  # tether.imaging.register imports scipy.io

import numpy as np  # noqa: E402

from tether.imaging.calibrate import (
    DEFAULT_RMS_GATE_PX,
    LOW_CONFIDENCE_TAG,
    OverGateRegistrationWarning,
    RegistrationMap,
    RegistrationOverGateError,
    fit_registration_map,
    load_map,
    read_calibration,
    registration_map_from_tmap,
    save_map,
    write_calibration,
)
from tether.imaging.register import (
    PolyTransform2D,
    TmapChannel,
    point_rms,
)  # noqa: E402
from tether.imaging.split import ChannelGeometry  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "tmap_coeffs.npz"
TDAT_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "tdat_coloc_slice.tdat"

_IDENTITY_A = np.array([0.0, 1.0, 0.0, 0.0, 0.0, 0.0])
_IDENTITY_B = np.array([0.0, 0.0, 1.0, 0.0, 0.0, 0.0])


def _identity_poly() -> PolyTransform2D:
    return PolyTransform2D(a=_IDENTITY_A, b=_IDENTITY_B, norm_xy=np.eye(3), norm_uv=np.eye(3))


def _identity_map(**overrides: object) -> RegistrationMap:
    kwargs: dict[str, object] = {
        "reference_channel": 1,
        "moving_channel": 2,
        "ref_to_moving": _identity_poly(),
        "moving_to_ref": _identity_poly(),
        "rms_residual": 0.1,
        "n_control_points": 100,
    }
    kwargs.update(overrides)
    return RegistrationMap(**kwargs)  # type: ignore[arg-type]


def _known_degree2() -> PolyTransform2D:
    # A genuine degree-2 map (affine + small quadratic terms).
    return PolyTransform2D(
        a=np.array([1.0, 1.01, 0.002, 1e-4, -3e-5, 2e-5]),
        b=np.array([-2.0, 0.003, 0.99, 5e-5, 1e-4, -2e-5]),
        norm_xy=np.eye(3),
        norm_uv=np.eye(3),
    )


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


# --- RegistrationMap object --------------------------------------------------


def test_apply_directions_use_the_right_transform() -> None:
    fwd = PolyTransform2D(
        a=np.array([10.0, 1.0, 0.0, 0.0, 0.0, 0.0]),  # x + 10
        b=np.array([0.0, 0.0, 1.0, 0.0, 0.0, 0.0]),  # y
        norm_xy=np.eye(3),
        norm_uv=np.eye(3),
    )
    inv = PolyTransform2D(
        a=np.array([-10.0, 1.0, 0.0, 0.0, 0.0, 0.0]),  # x - 10
        b=np.array([0.0, 0.0, 1.0, 0.0, 0.0, 0.0]),
        norm_xy=np.eye(3),
        norm_uv=np.eye(3),
    )
    reg = _identity_map(ref_to_moving=fwd, moving_to_ref=inv)
    pts = np.array([[1.0, 2.0], [3.0, 4.0]])
    np.testing.assert_allclose(reg.apply_reference_to_moving(pts), pts + [10.0, 0.0])
    np.testing.assert_allclose(reg.apply_moving_to_reference(pts), pts + [-10.0, 0.0])


def test_low_confidence_and_tags_track_the_gate() -> None:
    ok = _identity_map(rms_residual=0.4, gate_px=0.5)
    assert ok.low_confidence is False
    assert ok.molecule_tags == ()
    over = _identity_map(rms_residual=0.6, gate_px=0.5)
    assert over.low_confidence is True
    assert over.molecule_tags == (LOW_CONFIDENCE_TAG,)


def test_non_finite_residual_is_not_low_confidence() -> None:
    # An imported map with no control points carries an unknown (NaN) residual; the
    # gate must not fire on it (NaN > gate is False, but make the intent explicit).
    reg = _identity_map(rms_residual=float("nan"))
    assert reg.low_confidence is False
    assert reg.molecule_tags == ()


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"moving_channel": 1}, "must differ"),
        ({"gate_px": 0.0}, "gate_px"),
        ({"source": "bogus"}, "source"),
        ({"n_control_points": -1}, "n_control_points"),
        ({"ref_to_moving": object()}, "PolyTransform2D"),
    ],
)
def test_registration_map_validates(overrides: dict[str, object], match: str) -> None:
    with pytest.raises((ValueError, TypeError), match=match):
        _identity_map(**overrides)


# --- fit_registration_map (native, Stage 9) ----------------------------------


def test_native_fit_recovers_degree2_within_gate() -> None:
    known = _known_degree2()
    rng = np.random.RandomState(1)
    ref = rng.uniform([0, 0], [200, 400], (40, 2))
    moving = known.apply(ref)
    reg = fit_registration_map(ref, moving, reference_channel=1, moving_channel=2)
    assert reg.source == "native"
    assert reg.degree == 2
    assert reg.n_control_points == 40
    assert reg.rms_residual < 1e-6
    assert reg.low_confidence is False
    np.testing.assert_allclose(reg.apply_reference_to_moving(ref), moving, atol=1e-6)
    # The inverse direction is fitted independently (a degree-2 map's exact inverse
    # is not degree-2), so a forward->back round-trip is sub-pixel, not exact.
    assert point_rms(reg.apply_moving_to_reference(moving), ref) < 0.1


def test_over_gate_warns_flags_and_never_drops() -> None:
    known = _known_degree2()
    rng = np.random.RandomState(2)
    ref = rng.uniform([0, 0], [200, 400], (50, 2))
    moving = known.apply(ref) + rng.normal(scale=2.0, size=(50, 2))  # residual >> 0.5 px
    with pytest.warns(OverGateRegistrationWarning, match="exceeds"):
        reg = fit_registration_map(ref, moving, reference_channel=1, moving_channel=2)
    assert reg.rms_residual > DEFAULT_RMS_GATE_PX
    assert reg.low_confidence is True
    assert reg.molecule_tags == (LOW_CONFIDENCE_TAG,)
    # Never silently dropped: every control point is still part of the fit.
    assert reg.n_control_points == 50


def test_over_gate_fail_policy_raises() -> None:
    known = _known_degree2()
    rng = np.random.RandomState(3)
    ref = rng.uniform([0, 0], [200, 400], (50, 2))
    moving = known.apply(ref) + rng.normal(scale=2.0, size=(50, 2))
    with pytest.raises(RegistrationOverGateError, match="exceeds"):
        fit_registration_map(
            ref, moving, reference_channel=1, moving_channel=2, on_over_gate="fail"
        )


def test_within_gate_fit_is_silent_under_both_policies() -> None:
    known = _known_degree2()
    rng = np.random.RandomState(4)
    ref = rng.uniform([0, 0], [200, 400], (30, 2))
    moving = known.apply(ref)
    for policy in ("warn", "fail"):
        with warnings.catch_warnings():
            warnings.simplefilter("error")  # any OverGate warning would fail the test
            reg = fit_registration_map(
                ref,
                moving,
                reference_channel=1,
                moving_channel=2,
                on_over_gate=policy,  # type: ignore[arg-type]
            )
        assert reg.low_confidence is False


def test_similarity_fallback_for_few_points() -> None:
    # A pure 4-DOF similarity: scale 1.2, 15deg rotation, translation [3, -4].
    theta = np.deg2rad(15.0)
    rot = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    linear = 1.2 * rot
    offset = np.array([3.0, -4.0])
    ref = np.array([[0.0, 0.0], [10.0, 0.0], [0.0, 10.0], [10.0, 10.0]])  # 4 < 6 points
    moving = ref @ linear.T + offset
    reg = fit_registration_map(ref, moving, reference_channel=1, moving_channel=2)
    assert reg.degree == 1
    assert reg.rms_residual < 1e-9
    np.testing.assert_allclose(reg.apply_reference_to_moving(ref), moving, atol=1e-9)
    # And a held-out point is mapped by the recovered similarity.
    held = np.array([[5.0, 7.0]])
    np.testing.assert_allclose(
        reg.apply_reference_to_moving(held), held @ linear.T + offset, atol=1e-9
    )


def test_fit_rejects_too_few_points_for_any_fit() -> None:
    with pytest.raises(ValueError, match="control points"):
        fit_registration_map(
            np.array([[1.0, 2.0]]), np.array([[3.0, 4.0]]), reference_channel=1, moving_channel=2
        )


def test_fit_validates_inputs() -> None:
    ref = np.zeros((6, 2))
    with pytest.raises(ValueError, match="matching"):
        fit_registration_map(ref, np.zeros((7, 2)), reference_channel=1, moving_channel=2)
    with pytest.raises(ValueError, match="on_over_gate"):
        fit_registration_map(ref, ref, reference_channel=1, moving_channel=2, on_over_gate="nope")  # type: ignore[arg-type]


# --- map-file persistence (Stage 10) -----------------------------------------


def _assert_maps_equal(a: RegistrationMap, b: RegistrationMap) -> None:
    assert a.reference_channel == b.reference_channel
    assert a.moving_channel == b.moving_channel
    assert a.n_control_points == b.n_control_points
    assert a.gate_px == b.gate_px
    assert a.degree == b.degree
    assert a.source == b.source
    assert a.provenance == b.provenance
    if np.isnan(a.rms_residual):
        assert np.isnan(b.rms_residual)
    else:
        assert a.rms_residual == pytest.approx(b.rms_residual)
    for name in ("ref_to_moving", "moving_to_ref"):
        pa, pb = getattr(a, name), getattr(b, name)
        np.testing.assert_array_equal(pa.a, pb.a)
        np.testing.assert_array_equal(pa.b, pb.b)
        np.testing.assert_array_equal(pa.norm_xy, pb.norm_xy)
        np.testing.assert_array_equal(pa.norm_uv, pb.norm_uv)
    assert a.reference_geometry == b.reference_geometry
    assert a.moving_geometry == b.moving_geometry


def test_save_load_map_round_trip(tmp_path: Path) -> None:
    known = _known_degree2()
    rng = np.random.RandomState(5)
    ref = rng.uniform([0, 0], [200, 400], (20, 2))
    reg = fit_registration_map(
        ref,
        known.apply(ref),
        reference_channel=1,
        moving_channel=2,
        reference_geometry=ChannelGeometry(crop=(1, 1, 256, 256), rotation_deg=90, flip=(1, 0)),
        moving_geometry=ChannelGeometry(crop=(1, 257, 256, 512)),
        app_version="9.9.9",
        bead_file="beads.tif",
    )
    out = save_map(reg, tmp_path / "map.npz")
    assert out.exists()
    _assert_maps_equal(load_map(out), reg)


def test_save_load_map_handles_missing_geometry(tmp_path: Path) -> None:
    reg = _identity_map(rms_residual=float("nan"), reference_geometry=None, moving_geometry=None)
    out = save_map(reg, tmp_path / "m2.npz")
    _assert_maps_equal(load_map(out), reg)


def test_map_file_has_no_pickled_objects(tmp_path: Path) -> None:
    reg = _identity_map()
    out = save_map(reg, tmp_path / "m3.npz")
    # The whole point of Stage 10: explicit coeffs, no pickled transforms. np.load
    # with the default allow_pickle=False must succeed on every stored array.
    with np.load(out, allow_pickle=False) as data:
        assert {"ref_to_moving_a", "moving_to_ref_b", "provenance_json"} <= set(data.files)


@pytest.mark.parametrize("name", ["m4", "m4.dat", "m4.npz"])
def test_save_map_returns_the_written_path(tmp_path: Path, name: str) -> None:
    # np.savez appends ".npz" unless the suffix is exactly ".npz"; the returned path
    # must be the file actually on disk (so load_map(save_map(...)) always works),
    # whatever suffix the caller passed.
    out = save_map(_identity_map(), tmp_path / name)
    assert out.exists()
    _assert_maps_equal(load_map(out), _identity_map())


# --- /calibration persistence (additive data, M0 freeze) ---------------------


def test_write_read_calibration_round_trip(tmp_path: Path) -> None:
    h5py = pytest.importorskip("h5py")
    assert h5py
    from tether.io.schema import assert_is_compatible_project, create_project

    project = tmp_path / "p.tether"
    create_project(project)
    reg = _identity_map(
        rms_residual=0.42,
        moving_geometry=ChannelGeometry(crop=(1, 257, 256, 512)),
        provenance={"app_version": "1.2.3", "fit": "polynomial-deg2"},
    )
    path = write_calibration(project, reg, calibration_id="cal0")
    assert path == "/calibration/cal0"
    # Additive data must not break the frozen-skeleton contract.
    assert_is_compatible_project(project)
    _assert_maps_equal(read_calibration(project, "cal0"), reg)


def test_write_calibration_persists_low_confidence(tmp_path: Path) -> None:
    pytest.importorskip("h5py")
    from tether.io.schema import create_project

    project = tmp_path / "p2.tether"
    create_project(project)
    reg = _identity_map(rms_residual=0.9, gate_px=0.5)  # over gate
    write_calibration(project, reg, calibration_id="lowconf")
    back = read_calibration(project, "lowconf")
    assert back.low_confidence is True
    assert back.molecule_tags == (LOW_CONFIDENCE_TAG,)


def test_write_calibration_is_write_once(tmp_path: Path) -> None:
    pytest.importorskip("h5py")
    from tether.io.schema import create_project

    project = tmp_path / "p3.tether"
    create_project(project)
    reg = _identity_map()
    write_calibration(project, reg, calibration_id="dup")
    with pytest.raises(ValueError, match="already exists"):
        write_calibration(project, reg, calibration_id="dup")


def test_read_calibration_missing_id_raises(tmp_path: Path) -> None:
    pytest.importorskip("h5py")
    from tether.io.schema import create_project

    project = tmp_path / "p4.tether"
    create_project(project)
    with pytest.raises(KeyError, match="no calibration"):
        read_calibration(project, "ghost")


def test_writing_calibration_keeps_schema_manifest_frozen(tmp_path: Path) -> None:
    # Writing calibration data into a real project must not change the *structural*
    # manifest the code declares (build_manifest builds a fresh project): the M0
    # schema-guard golden is unaffected by additive /calibration data.
    pytest.importorskip("h5py")
    from tether.io.schema import build_manifest, create_project

    before = build_manifest()
    project = tmp_path / "p5.tether"
    create_project(project)
    write_calibration(project, _identity_map(), calibration_id="c")
    assert build_manifest() == before  # the declared schema is independent of written data


def test_write_read_calibration_nan_residual(tmp_path: Path) -> None:
    # An imported map with no control points carries an unknown (NaN) residual; the
    # /calibration round-trip (HDF5 float attr) must preserve it, and a NaN residual
    # must not read back as low-confidence (mirrors the npz NaN test).
    pytest.importorskip("h5py")
    _reference, channels = _load_tmap_fixture()
    from tether.io.schema import create_project

    project = tmp_path / "p6.tether"
    create_project(project)
    reg = registration_map_from_tmap(channels)  # no control points -> NaN residual
    assert np.isnan(reg.rms_residual)
    write_calibration(project, reg, calibration_id="imported")
    back = read_calibration(project, "imported")
    assert np.isnan(back.rms_residual)
    assert back.low_confidence is False
    assert back.source == "imported"


# --- imported .tmap path + apply-both parity (§7.1; §9 M1) -------------------


def test_imported_map_matches_tmap_channel_helper() -> None:
    """The imported 0-based transform equals the TmapChannel 0-based helper exactly.

    This validates the coefficient-folding (`_shift_poly`): the RegistrationMap's
    `apply_reference_to_moving` must reproduce `TmapChannel.reference_to_channel`
    to machine precision, so the imported map is a faithful, self-contained 0-based
    transform (not relying on the helper's run-time +-1 shift).
    """
    reference, channels = _load_tmap_fixture()
    reg = registration_map_from_tmap(channels)
    assert reg.source == "imported"
    moving_id = reg.moving_channel
    rng = np.random.RandomState(0)
    grid = rng.uniform([10, 10], [240, 500], (200, 2))
    np.testing.assert_allclose(
        reg.apply_reference_to_moving(grid),
        channels[moving_id].reference_to_channel(grid),
        atol=1e-9,
    )
    np.testing.assert_allclose(
        reg.apply_moving_to_reference(grid),
        channels[moving_id].channel_to_reference(grid),
        atol=1e-9,
    )


def test_apply_both_parity_native_vs_imported() -> None:
    """§7.1 / §9 M1: an imported .tmap warps to the same coordinates as a native fit.

    A native degree-2 fit from the committed .tdat colocalized pairs and the
    imported .tmap map agree to <= 0.5 px RMS at the molecule positions -- the
    conjunctive "native calibration AND apply imported .tmap" deliverable.

    Parity is a property of the two *transforms*, not of either fit's residual to
    the control points. The .tdat colocalized FRET-molecule pairs carry ~1.6 px of
    biological/colocalization scatter (the .tmap's own residual to the actual
    acceptor molecules has median > 1 px; bead control points, not FRET molecules,
    are the precise registration inputs), so both fits legitimately exceed the
    0.5 px registration gate *at the molecules* -- yet their predicted acceptor
    coordinates still agree to <= 0.5 px. ``gate_px`` is relaxed here so that
    expected molecule-scatter does not flag these transform-agreement fits; the
    0.5 px gate itself is exercised in the dedicated over-gate tests above.
    """
    h5py = pytest.importorskip("h5py")
    assert h5py  # used transitively by read_tdat
    from tether.io import read_tdat

    reference, channels = _load_tmap_fixture()
    tdat = read_tdat(TDAT_FIXTURE)
    coords = tdat.colocalization.coords
    donor = coords[tdat.reference_channel]  # reference-frame 0-based [x, y]

    moving_id = next(cid for cid in channels if cid != reference)
    acceptor = coords[moving_id - 1]

    native = fit_registration_map(
        donor, acceptor, reference_channel=reference, moving_channel=moving_id, gate_px=5.0
    )
    imported = registration_map_from_tmap(
        channels, reference_points=donor, moving_points=acceptor, gate_px=5.0
    )
    assert imported.source == "imported"
    assert np.isfinite(imported.rms_residual)  # measured at the supplied control points

    native_pred = native.apply_reference_to_moving(donor)
    imported_pred = imported.apply_reference_to_moving(donor)
    assert point_rms(native_pred, imported_pred) <= 0.5  # apply-both parity
    assert np.mean(np.linalg.norm(native_pred - imported_pred, axis=1) <= 1.0) >= 0.95


def test_imported_map_without_points_has_unknown_residual() -> None:
    _reference, channels = _load_tmap_fixture()
    reg = registration_map_from_tmap(channels)
    assert np.isnan(reg.rms_residual)
    assert reg.n_control_points == 0
    assert reg.low_confidence is False  # unknown residual never trips the gate


def _tdat_pairs() -> tuple[int, dict[int, TmapChannel], np.ndarray, np.ndarray]:
    from tether.io import read_tdat

    reference, channels = _load_tmap_fixture()
    tdat = read_tdat(TDAT_FIXTURE)
    coords = tdat.colocalization.coords
    donor = coords[tdat.reference_channel]
    moving_id = next(cid for cid in channels if cid != reference)
    return moving_id, channels, donor, coords[moving_id - 1]


def test_imported_over_gate_warns_flags_and_never_drops() -> None:
    # The imported map's residual at the real colocalized pairs is ~1.6 px (> the
    # 0.5 px gate), so the imported path must apply the SAME over-gate policy as the
    # native path: warn-and-flag, tag, never drop -- not silently flag.
    pytest.importorskip("h5py")
    _moving_id, channels, donor, acceptor = _tdat_pairs()
    with pytest.warns(OverGateRegistrationWarning, match="exceeds"):
        reg = registration_map_from_tmap(channels, reference_points=donor, moving_points=acceptor)
    assert reg.rms_residual > DEFAULT_RMS_GATE_PX
    assert reg.low_confidence is True
    assert reg.molecule_tags == (LOW_CONFIDENCE_TAG,)
    assert reg.n_control_points == len(donor)  # never dropped


def test_imported_over_gate_fail_policy_raises() -> None:
    pytest.importorskip("h5py")
    _moving_id, channels, donor, acceptor = _tdat_pairs()
    with pytest.raises(RegistrationOverGateError, match="exceeds"):
        registration_map_from_tmap(
            channels, reference_points=donor, moving_points=acceptor, on_over_gate="fail"
        )


def test_registration_map_from_tmap_validates() -> None:
    _reference, channels = _load_tmap_fixture()
    with pytest.raises(ValueError, match="empty"):
        registration_map_from_tmap({})
    with pytest.raises(ValueError, match="not in decoded"):
        registration_map_from_tmap(channels, moving_channel=999)
    with pytest.raises(ValueError, match="on_over_gate"):
        registration_map_from_tmap(channels, on_over_gate="nope")  # type: ignore[arg-type]


# --- public surface ----------------------------------------------------------


def test_public_imaging_surface_reexports_calibration() -> None:
    import tether.imaging as imaging

    assert imaging.RegistrationMap is RegistrationMap
    assert imaging.fit_registration_map is fit_registration_map
    assert imaging.registration_map_from_tmap is registration_map_from_tmap
    assert imaging.save_map is save_map
    assert imaging.write_calibration is write_calibration
    assert imaging.DEFAULT_RMS_GATE_PX == DEFAULT_RMS_GATE_PX

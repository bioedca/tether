# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Store-integrated engineered features -> ``/features`` (M5, FR-ML; PRD §7.5).

Locks the ``/features/table`` writer/reader: the round-trip matrix matches the
pure core, features are computed for rejected molecules by default (they are ML
labels), the derived cache recomputes/replaces, provenance is stamped, and the
write is additive under the M0 schema freeze (``schema-guard`` stays green).
Headless -> base CI matrix.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("h5py")

import h5py  # noqa: E402
import numpy as np  # noqa: E402

from tether.imaging.aperture import IntegratedTraces  # noqa: E402
from tether.imaging.calibrate import RegistrationMap  # noqa: E402
from tether.imaging.coloc import ColocalizedMolecules  # noqa: E402
from tether.imaging.extract import (  # noqa: E402
    MoleculeTraces,
    MovieMetadata,
    read_molecules,
    write_extraction,
)
from tether.imaging.register import PolyTransform2D  # noqa: E402
from tether.imaging.split import ChannelGeometry  # noqa: E402
from tether.io import schema  # noqa: E402
from tether.io.filename import parse_filename  # noqa: E402
from tether.io.schema import assert_is_compatible_project, create_project  # noqa: E402
from tether.ml.features import (  # noqa: E402
    FEATURE_NAMES,
    SPATIAL_FEATURE_NAMES,
    TRACE_FEATURE_NAMES,
    compute_spatial_features,
    compute_trace_features,
)
from tether.project.core import Project  # noqa: E402
from tether.project.features import (  # noqa: E402
    FEATURES_GROUP,
    compute_features,
    feature_matrix,
    read_features,
)

_N_TRACE = len(TRACE_FEATURE_NAMES)  # trace columns precede the spatial block in the matrix

_PARSED = parse_filename("Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif")
_WINDOW = 21


def _reg_map() -> RegistrationMap:
    poly = PolyTransform2D(
        a=np.array([0.0, 1.0, 0.0, 0.0, 0.0, 0.0]),
        b=np.array([0.0, 0.0, 1.0, 0.0, 0.0, 0.0]),
        norm_xy=np.eye(3),
        norm_uv=np.eye(3),
    )
    return RegistrationMap(
        reference_channel=1,
        moving_channel=2,
        ref_to_moving=poly,
        moving_to_ref=poly,
        rms_residual=0.1,
        n_control_points=100,
    )


def _integrated(intensity: np.ndarray) -> IntegratedTraces:
    intensity = np.asarray(intensity, dtype="float64")
    n = intensity.shape[0]
    background = np.full_like(intensity, 100.0)
    return IntegratedTraces(
        intensity=intensity,
        total=intensity + background,
        background=background,
        valid=np.ones(n, dtype=bool),
    )


def _build_store(
    path: Path, donor: np.ndarray, acceptor: np.ndarray, *, coords: np.ndarray | None = None
) -> tuple[Project, list[str]]:
    """A ``.tether`` whose ``corrected`` traces are exactly ``donor``/``acceptor``.

    ``coords`` overrides the per-molecule donor ``[x, y]`` (default: all distinct);
    pass repeated coordinates to force a shared ``molecule_key`` (the §7.10 case).
    """
    donor = np.asarray(donor, dtype="float64")
    acceptor = np.asarray(acceptor, dtype="float64")
    n, t = donor.shape
    if coords is None:
        coords = np.array(
            [[12.0 + 1.7 * i, 14.0 + 2.3 * (i % 7)] for i in range(n)], dtype="float64"
        )
    else:
        coords = np.asarray(coords, dtype="float64")
    mols = ColocalizedMolecules(
        donor_xy=coords,
        acceptor_xy=coords,
        acceptor_detected=np.zeros(n, dtype=bool),
        donor_index=np.arange(n, dtype=np.intp),
        acceptor_index=np.full(n, -1, dtype=np.intp),
    )
    traces = MoleculeTraces(
        donor=_integrated(donor),
        acceptor=_integrated(acceptor),
        donor_patches=np.zeros((n, _WINDOW, _WINDOW), dtype="float32"),
        acceptor_patches=np.zeros((n, _WINDOW, _WINDOW), dtype="float32"),
        window=_WINDOW,
        disk_radius=3.0,
        ring_inner=6.0,
        ring_outer=8.0,
        bg_window=10,
    )
    movie = MovieMetadata(
        movie_id="mov-1",
        sha256="a" * 64,
        n_frames=t,
        height=64,
        width=64,
        donor_geometry=ChannelGeometry(crop=(1, 1, 64, 64)),
        acceptor_geometry=ChannelGeometry(crop=(1, 65, 64, 128)),
    )
    create_project(path, overwrite=True)
    write_extraction(
        path,
        movie=movie,
        molecules=mols,
        traces=traces,
        parsed=_PARSED,
        registration_map=_reg_map(),
    )
    proj = Project.open(path)
    keys = [
        k.decode() if isinstance(k, bytes) else str(k) for k in read_molecules(path)["molecule_key"]
    ]
    return proj, keys


def _anticorrelated(n: int, t: int) -> tuple[np.ndarray, np.ndarray]:
    """(n, t) donor/acceptor where each molecule's total intensity genuinely varies."""
    rng = np.random.default_rng(5)
    donor = rng.normal(600.0, 90.0, size=(n, t))
    acceptor = rng.normal(500.0, 70.0, size=(n, t))
    return donor, acceptor


def test_compute_and_read_roundtrip_matches_pure_core(tmp_path) -> None:
    donor, acceptor = _anticorrelated(4, 30)
    proj, _ = _build_store(tmp_path / "x.tether", donor, acceptor)

    stored = compute_features(proj)
    assert stored.n_molecules == 4
    assert stored.feature_names == FEATURE_NAMES
    assert stored.matrix.shape == (4, len(FEATURE_NAMES))
    assert len(set(stored.molecule_ids)) == 4  # unique join key

    # Every stored row equals the pure core over that molecule's corrected trace.
    # /traces is float32 on disk, so compare against the same float32-round-tripped
    # input the store reads back — not the original float64 fixture (a ~1e-6 gap).
    for i in range(4):
        d32 = donor[i].astype(np.float32).astype(np.float64)
        a32 = acceptor[i].astype(np.float32).astype(np.float64)
        expected = compute_trace_features(d32, a32).as_vector()
        # The trace block is the leading _N_TRACE columns; the spatial block follows.
        np.testing.assert_allclose(stored.matrix[i, :_N_TRACE], expected, rtol=1e-9, atol=1e-9)

    # feature_matrix reads back an identical matrix + names.
    reread = feature_matrix(proj)
    assert reread.feature_names == FEATURE_NAMES
    np.testing.assert_array_equal(reread.matrix, stored.matrix)
    assert reread.molecule_ids == stored.molecule_ids


def test_read_features_structured_columns(tmp_path) -> None:
    donor, acceptor = _anticorrelated(3, 24)
    proj, _ = _build_store(tmp_path / "x.tether", donor, acceptor)
    compute_features(proj)

    table = read_features(proj)
    assert table.shape == (3,)
    for name in ("molecule_id", "molecule_key", *FEATURE_NAMES):
        assert name in table.dtype.names
    # n_frames is stored as an integer column, the rest as float.
    assert np.issubdtype(table["n_frames"].dtype, np.integer)
    assert np.issubdtype(table["snr"].dtype, np.floating)
    np.testing.assert_array_equal(table["n_frames"], np.full(3, 24))  # window = full 24-frame trace


def test_features_computed_for_rejected_by_default(tmp_path) -> None:
    donor, acceptor = _anticorrelated(3, 20)
    proj, keys = _build_store(tmp_path / "x.tether", donor, acceptor)

    proj.reject(keys[0], labeler="tester")
    stored = compute_features(proj)  # include_rejected defaults True
    assert stored.n_molecules == 3  # the rejected molecule keeps a feature row (ML label)

    excluded = compute_features(proj, include_rejected=False)
    assert excluded.n_molecules == 2  # opt out -> drop the rejected molecule


def test_recompute_replaces_and_overwrite_guard(tmp_path) -> None:
    donor, acceptor = _anticorrelated(3, 18)
    proj, _ = _build_store(tmp_path / "x.tether", donor, acceptor)

    first = compute_features(proj)
    assert first.n_molecules == 3
    # Default overwrite=True recomputes/replaces the derived cache in place.
    again = compute_features(proj)
    np.testing.assert_array_equal(read_features(proj).shape, (3,))
    np.testing.assert_allclose(again.matrix, first.matrix)

    with pytest.raises(FileExistsError, match="features"):
        compute_features(proj, overwrite=False)


def test_provenance_attrs_stamped(tmp_path) -> None:
    donor, acceptor = _anticorrelated(2, 16)
    path = tmp_path / "x.tether"
    proj, _ = _build_store(path, donor, acceptor)
    compute_features(proj, intensity_quantity="corrected")

    with h5py.File(path, "r") as f:
        attrs = f[FEATURES_GROUP]["table"].attrs
        assert attrs["intensity_quantity"] == "corrected"
        assert int(attrs["n_molecules"]) == 2
        assert int(attrs["feature_schema_version"]) >= 1
        assert str(attrs["app_version"])  # non-empty version stamp
        assert str(attrs["created_utc"])
        assert tuple(json.loads(attrs["feature_names"])) == FEATURE_NAMES


def test_write_is_additive_under_schema_freeze(tmp_path) -> None:
    donor, acceptor = _anticorrelated(3, 20)
    path = tmp_path / "x.tether"
    proj, _ = _build_store(path, donor, acceptor)
    compute_features(proj)

    # The store is still a valid, complete .tether after writing features...
    assert assert_is_compatible_project(path) == schema.SCHEMA_VERSION
    # ...and every difference from the frozen skeleton is an ADDITION (no removal,
    # rename, or dtype change), so schema-guard stays green (ADR-0005).
    golden = schema.build_manifest()
    with h5py.File(path, "r") as f:
        current = schema.introspect(f)
    assert schema.diff_manifest(golden, current) == []
    assert "/features/table" in current["datasets"]


def test_raw_quantity_records_provenance(tmp_path) -> None:
    donor, acceptor = _anticorrelated(2, 16)
    path = tmp_path / "x.tether"
    proj, _ = _build_store(path, donor, acceptor)
    compute_features(proj, intensity_quantity="raw")
    with h5py.File(path, "r") as f:
        assert f[FEATURES_GROUP]["table"].attrs["intensity_quantity"] == "raw"


def test_read_before_compute_raises(tmp_path) -> None:
    donor, acceptor = _anticorrelated(2, 12)
    proj, _ = _build_store(tmp_path / "x.tether", donor, acceptor)
    with pytest.raises(KeyError, match="features"):
        read_features(proj)
    with pytest.raises(KeyError, match="features"):
        feature_matrix(proj)


def test_no_molecules_raises(tmp_path) -> None:
    path = tmp_path / "empty.tether"
    create_project(path)
    with pytest.raises(ValueError, match="no molecules"):
        compute_features(path)


def _set_analysis_window(path: Path, row: int, lo: int, hi: int) -> None:
    """Directly set one molecule's analysis_window (extraction writes [0, n_frames])."""
    with h5py.File(path, "r+") as f:
        table = f["molecules"]["table"]
        record = table[row]
        record["analysis_window"] = [lo, hi]
        table[row] = record


def test_analysis_window_slice_and_fallback(tmp_path) -> None:
    # Extraction writes analysis_window = [0, n_frames]; narrow one molecule's window
    # and leave another's as the [0, 0] sentinel to exercise both _windowed_rows paths
    # (the narrowed slice and the frame_range fallback) — otherwise a window-ignoring
    # or off-by-one drift from windowed_channels would go undetected.
    donor, acceptor = _anticorrelated(2, 30)
    path = tmp_path / "x.tether"
    proj, _ = _build_store(path, donor, acceptor)
    _set_analysis_window(path, 0, 5, 15)  # narrowed sub-window
    _set_analysis_window(path, 1, 0, 0)  # sentinel -> falls back to frame_range (full 30)

    stored = compute_features(proj)
    # Row 0's trace features == the pure core over exactly the [5, 15) float32 slice.
    d0 = donor[0, 5:15].astype(np.float32).astype(np.float64)
    a0 = acceptor[0, 5:15].astype(np.float32).astype(np.float64)
    np.testing.assert_allclose(
        stored.matrix[0, :_N_TRACE],
        compute_trace_features(d0, a0).as_vector(),
        rtol=1e-9,
        atol=1e-9,
    )
    assert stored.matrix[0, 0] == pytest.approx(10.0)  # n_frames = 15 - 5
    # Row 1's [0, 0] window falls back to the full 30-frame native extent.
    assert stored.matrix[1, 0] == pytest.approx(30.0)


def test_duplicate_molecule_key_kept_as_distinct_rows(tmp_path) -> None:
    # §7.10: a molecule_key is NOT unique (quantized donor_xy can collide), which is
    # why molecule_id is the join key. Two molecules at the SAME donor_xy share a
    # molecule_key; features must stay two distinct rows, each aligned to its own
    # trace by position/molecule_id — never collapsed or deduped by molecule_key.
    donor = np.array([[10.0, 30.0, 20.0, 40.0], [500.0, 100.0, 300.0, 200.0]])
    acceptor = np.array([[40.0, 20.0, 30.0, 10.0], [100.0, 500.0, 200.0, 300.0]])
    coords = np.array([[12.0, 14.0], [12.0, 14.0]])  # identical -> shared molecule_key
    path = tmp_path / "x.tether"
    proj, _ = _build_store(path, donor, acceptor, coords=coords)

    stored = compute_features(proj)
    assert stored.n_molecules == 2  # not collapsed to one
    assert len(set(stored.molecule_ids)) == 2  # distinct per-row identity
    assert len(set(stored.molecule_keys)) == 1  # ...that genuinely share a molecule_key
    for i in range(2):
        d = donor[i].astype(np.float32).astype(np.float64)
        a = acceptor[i].astype(np.float32).astype(np.float64)
        np.testing.assert_allclose(
            stored.matrix[i, :_N_TRACE],
            compute_trace_features(d, a).as_vector(),
            rtol=1e-9,
            atol=1e-9,
        )


def test_nan_feature_survives_store_roundtrip(tmp_path) -> None:
    # A conserved constant total (acceptor = C - donor) has an undefined SNR (NaN).
    # The "never a fabricated 0" contract must hold across the HDF5 <f8 round-trip,
    # not just in the pure core. Small integer values are float32-exact, so the total
    # stays exactly constant on disk.
    donor = np.array([[1.0, 3.0, 2.0, 5.0, 4.0, 6.0]])
    acceptor = 10.0 - donor  # constant total -> snr NaN; still anticorrelated
    path = tmp_path / "x.tether"
    proj, _ = _build_store(path, donor, acceptor)
    compute_features(proj)

    snr_idx = FEATURE_NAMES.index("snr")
    assert np.isnan(read_features(proj)["snr"][0])  # not coerced to 0 on write/read
    assert np.isnan(feature_matrix(proj).matrix[0, snr_idx])


def test_raw_layer_is_actually_read(tmp_path) -> None:
    # The raw layer (total = intensity + 100/channel background) differs from corrected
    # by a known +200/frame offset in mean(D + A); assert the selection reads the raw
    # *data*, not merely that the provenance attr says "raw".
    donor, acceptor = _anticorrelated(3, 20)
    path = tmp_path / "x.tether"
    proj, _ = _build_store(path, donor, acceptor)

    corrected = compute_features(proj, intensity_quantity="corrected")
    raw = compute_features(proj, intensity_quantity="raw", overwrite=True)
    ti = FEATURE_NAMES.index("total_intensity")
    np.testing.assert_allclose(raw.matrix[:, ti], corrected.matrix[:, ti] + 200.0, atol=0.05)


# --- spatial (crowding) features -----------------------------------------------


def _set_disk_radius(path: Path, radius: float) -> None:
    """Override the stored aperture (PSF-disk) radius the overlap test reads."""
    with h5py.File(path, "r+") as f:
        f["settings"]["extraction"].attrs["disk_radius"] = float(radius)


def test_spatial_columns_written_and_match_pure_core(tmp_path) -> None:
    # The stored spatial block equals a direct compute_spatial_features over the
    # store's own donor_xy / movie_id / aperture radius — the store passes the right
    # inputs and merges them into /features/table in FEATURE_NAMES order.
    donor, acceptor = _anticorrelated(5, 20)
    path = tmp_path / "x.tether"
    proj, _ = _build_store(path, donor, acceptor)
    stored = compute_features(proj)

    mols = read_molecules(path)
    coords = np.asarray(mols["donor_xy"], dtype=np.float64)
    movie_ids = np.array([m.decode() if isinstance(m, bytes) else str(m) for m in mols["movie_id"]])
    expected = compute_spatial_features(coords, movie_ids=movie_ids, aperture_radius=3.0)

    nd = FEATURE_NAMES.index("neighbor_distance")
    ov = FEATURE_NAMES.index("aperture_overlap")
    for row, sf in enumerate(expected):
        np.testing.assert_allclose(stored.matrix[row, nd], sf.neighbor_distance, equal_nan=True)
        assert stored.matrix[row, ov] == sf.aperture_overlap
    # The columns also exist on the structured table (float dtype, never coerced int).
    table = read_features(proj)
    for name in SPATIAL_FEATURE_NAMES:
        assert name in table.dtype.names
        assert np.issubdtype(table[name].dtype, np.floating)


def test_spatial_neighbour_context_is_full_population_not_selection(tmp_path) -> None:
    # A molecule's crowding is measured against EVERY molecule in its movie, including
    # rejected / unselected ones — a rejected neighbour still contaminates the aperture.
    # A (10,10) & B (11,10) are 1 px apart; C is far. Reject B, feature only the kept
    # set: A's neighbour_distance must still be 1.0 (B), not the distance to far C.
    donor, acceptor = _anticorrelated(3, 18)
    coords = np.array([[10.0, 10.0], [11.0, 10.0], [50.0, 50.0]])
    path = tmp_path / "x.tether"
    proj, keys = _build_store(path, donor, acceptor, coords=coords)
    proj.reject(keys[1], labeler="tester")  # B is the nearest neighbour of A

    stored = compute_features(proj, include_rejected=False)
    assert stored.n_molecules == 2  # A and C only (B dropped from the featured set)
    nd = FEATURE_NAMES.index("neighbor_distance")
    row_of = {k: i for i, k in enumerate(stored.molecule_keys)}
    a_row = row_of[keys[0]]
    assert stored.matrix[a_row, nd] == pytest.approx(1.0)  # sees rejected B, not far C


def test_spatial_overlap_reads_stored_aperture_radius(tmp_path) -> None:
    # Two molecules 10 px apart: no aperture overlap at the stored radius 3 (2·3 = 6),
    # but flipping the stored disk_radius to 6 (2·6 = 12 > 10) flips the flag — proving
    # the overlap test reads the store's own aperture radius, not a hardcoded constant.
    donor, acceptor = _anticorrelated(2, 16)
    coords = np.array([[10.0, 10.0], [20.0, 10.0]])
    path = tmp_path / "x.tether"
    proj, _ = _build_store(path, donor, acceptor, coords=coords)

    ov = FEATURE_NAMES.index("aperture_overlap")
    at_r3 = compute_features(proj)
    assert at_r3.matrix[0, ov] == 0.0 and at_r3.matrix[1, ov] == 0.0

    _set_disk_radius(path, 6.0)
    at_r6 = compute_features(proj, overwrite=True)
    assert at_r6.matrix[0, ov] == 1.0 and at_r6.matrix[1, ov] == 1.0
    # The raw neighbour distance is radius-independent (10 px) in both.
    nd = FEATURE_NAMES.index("neighbor_distance")
    assert at_r3.matrix[0, nd] == pytest.approx(10.0)
    assert at_r6.matrix[0, nd] == pytest.approx(10.0)

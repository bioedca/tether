# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Store-integrated ranking dataset + baseline precision@k (M5, FR-ML; PRD §7.5).

Locks :mod:`tether.project.ranking`: the ``/features`` -> ``/labels`` join produces the
supervised (feature-matrix, accept/reject truth) view; uncurated molecules stay in the
candidate set but out of the ground truth (never dropped); and the file-order baseline
precision@k matches the pure-core computation over the same labels. Headless -> base CI matrix.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("h5py")

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
from tether.io.filename import parse_filename  # noqa: E402
from tether.io.schema import create_project  # noqa: E402
from tether.ml.ranking import file_order_ranking, precision_at_k  # noqa: E402
from tether.project.core import Project  # noqa: E402
from tether.project.features import compute_features  # noqa: E402
from tether.project.labels import CurationLabel, accept, reject  # noqa: E402
from tether.project.ranking import baseline_precision_at_k, ranking_dataset  # noqa: E402

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
    path: Path,
    donor: np.ndarray,
    acceptor: np.ndarray,
    *,
    coords: np.ndarray | None = None,
) -> tuple[Project, list[str]]:
    """A ``.tether`` whose ``corrected`` traces are exactly ``donor``/``acceptor``.

    ``coords`` overrides the per-molecule ``donor_xy`` (default: distinct positions);
    pass colliding coordinates to exercise the §7.10 multi-id-per-key case. Returns the
    project and its molecule_keys in store order.
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


def _six_valid_traces() -> tuple[np.ndarray, np.ndarray]:
    """6 molecules with distinct, all-finite features (no undefined/NaN feature rows)."""
    rng = np.random.default_rng(11)
    t = 24
    donor = np.empty((6, t), dtype=np.float64)
    acceptor = np.empty((6, t), dtype=np.float64)
    for i in range(6):
        donor[i] = rng.normal(400.0 + 120.0 * i, 40.0 + 5.0 * i, size=t)
        acceptor[i] = rng.normal(900.0 - 110.0 * i, 30.0 + 4.0 * i, size=t)
    return donor, acceptor


def _labeled_store(path: Path) -> tuple[Project, list[str]]:
    """Store with keys[0],[2] accepted; keys[1],[3] rejected; keys[4],[5] left uncurated."""
    donor, acceptor = _six_valid_traces()
    proj, keys = _build_store(path, donor, acceptor)
    compute_features(proj)  # a feature row per molecule (include_rejected default)
    accept(path, keys[0])
    reject(path, keys[1])
    accept(path, keys[2])
    reject(path, keys[3])
    return proj, keys


def test_ranking_dataset_joins_features_and_labels(tmp_path) -> None:
    proj, keys = _labeled_store(tmp_path / "x.tether")
    data = ranking_dataset(proj)

    assert data.n_molecules == 6
    assert data.X.shape == (6, len(data.feature_names))
    assert np.isfinite(data.X).all()  # the six chosen traces all have defined features
    # curation_label aligned to the store order the features were written in.
    expected = {
        keys[0]: int(CurationLabel.ACCEPT),
        keys[1]: int(CurationLabel.REJECT),
        keys[2]: int(CurationLabel.ACCEPT),
        keys[3]: int(CurationLabel.REJECT),
        keys[4]: int(CurationLabel.UNCURATED),
        keys[5]: int(CurationLabel.UNCURATED),
    }
    got = dict(zip(data.molecule_keys, data.curation_label.tolist(), strict=True))
    assert got == expected
    assert data.n_labeled == 4


def test_uncurated_molecules_are_candidates_not_ground_truth(tmp_path) -> None:
    proj, keys = _labeled_store(tmp_path / "x.tether")
    data = ranking_dataset(proj)

    # All six molecules remain ranking candidates (never dropped)...
    assert len(data.molecule_ids) == 6
    # ...but only the four human-labeled ones are ground truth for evaluation.
    good = data.is_good
    assert len(good) == 4
    key_to_id = dict(zip(data.molecule_keys, data.molecule_ids, strict=True))
    assert good[key_to_id[keys[0]]] is True
    assert good[key_to_id[keys[1]]] is False
    assert key_to_id[keys[4]] not in good and key_to_id[keys[5]] not in good


def test_baseline_precision_at_k_matches_pure_core(tmp_path) -> None:
    proj, _ = _labeled_store(tmp_path / "x.tether")
    data = ranking_dataset(proj)

    # Labeled molecules in store order carry relevance [accept, reject, accept, reject].
    ranking = file_order_ranking(data.molecule_ids)
    rel = ranking.ranked_relevance(data.is_good)
    assert rel.tolist() == [True, False, True, False]

    for k, expected in [(1, 1.0), (2, 0.5), (4, 0.5)]:
        assert baseline_precision_at_k(proj, k) == pytest.approx(expected)
        assert baseline_precision_at_k(proj, k) == pytest.approx(precision_at_k(rel, k))


def test_baseline_precision_at_k_requires_labels(tmp_path) -> None:
    # Features computed but nothing curated -> precision@k is undefined, surfaced loudly.
    donor, acceptor = _six_valid_traces()
    proj, _ = _build_store(tmp_path / "x.tether", donor, acceptor)
    compute_features(proj)
    with pytest.raises(ValueError, match="no human-labeled molecules"):
        baseline_precision_at_k(proj, 3)


def test_baseline_precision_at_k_validates_k(tmp_path) -> None:
    proj, _ = _labeled_store(tmp_path / "x.tether")
    with pytest.raises(ValueError, match="positive integer"):
        baseline_precision_at_k(proj, 0)


def test_multi_id_per_key_join_both_rows_inherit_shared_label(tmp_path) -> None:
    # §7.10 collision: two molecules whose quantized donor_xy coincide share ONE
    # molecule_key but keep distinct (UUID) molecule_ids. Curating that key must label
    # BOTH feature rows, while both molecule_ids stay distinct ranking candidates — the
    # load-bearing per-row `labels_by_key.get(key)` broadcast the join is built on.
    rng = np.random.default_rng(3)
    t = 24
    donor = np.array([rng.normal(500.0 + 100.0 * i, 40.0, size=t) for i in range(3)])
    acceptor = np.array([rng.normal(800.0 - 90.0 * i, 30.0, size=t) for i in range(3)])
    # rows 0 & 1: identical donor_xy -> identical molecule_key; row 2 distinct.
    coords = np.array([[20.0, 30.0], [20.0, 30.0], [45.0, 50.0]], dtype="float64")
    proj, keys = _build_store(tmp_path / "x.tether", donor, acceptor, coords=coords)
    assert keys[0] == keys[1] and keys[0] != keys[2]  # the collision genuinely exists

    compute_features(proj)
    accept(proj.path, keys[0])  # curating the shared key touches BOTH colliding molecules

    data = ranking_dataset(proj)
    assert data.n_molecules == 3  # both colliding molecules kept as distinct rows
    shared_rows = [i for i, k in enumerate(data.molecule_keys) if k == keys[0]]
    assert len(shared_rows) == 2  # the two rows sharing the key...
    assert all(
        data.curation_label[i] == int(CurationLabel.ACCEPT) for i in shared_rows
    )  # ...both labeled
    shared_ids = [data.molecule_ids[i] for i in shared_rows]
    assert len(set(shared_ids)) == 2  # ...yet keep distinct molecule_ids
    good = data.is_good
    assert len(good) == 2  # one is_good entry per labeled molecule_id (row 2 uncurated)
    assert all(good[mid] is True for mid in shared_ids)


def test_ranking_dataset_before_compute_features_raises(tmp_path) -> None:
    donor, acceptor = _six_valid_traces()
    proj, _ = _build_store(tmp_path / "x.tether", donor, acceptor)
    with pytest.raises(KeyError, match="features"):
        ranking_dataset(proj)
    with pytest.raises(KeyError, match="features"):
        baseline_precision_at_k(proj, 2)

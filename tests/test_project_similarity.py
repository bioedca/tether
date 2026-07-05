# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Store-integrated feature similarity search (M5, FR-ML; PRD §4.2, §7.5).

Locks the ``/features`` -> "find traces like these" entry points
(:func:`~tether.project.features.similar_molecules` /
:func:`~tether.project.features.similar_to_molecules` /
:func:`~tether.project.features.build_project_similarity_index`): the store convenience
matches the pure core over the stored matrix, a known reference returns its
near-neighbours, a molecule with an undefined feature is reported (not dropped), and the
population is never reduced. Headless -> base CI matrix.
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
from tether.ml.similarity import build_similarity_index  # noqa: E402
from tether.project.core import Project  # noqa: E402
from tether.project.features import (  # noqa: E402
    build_project_similarity_index,
    compute_features,
    feature_matrix,
    similar_molecules,
    similar_to_molecules,
)

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


def _build_store(path: Path, donor: np.ndarray, acceptor: np.ndarray) -> tuple[Project, list[str]]:
    """A ``.tether`` whose ``corrected`` traces are exactly ``donor``/``acceptor``."""
    donor = np.asarray(donor, dtype="float64")
    acceptor = np.asarray(acceptor, dtype="float64")
    n, t = donor.shape
    coords = np.array([[12.0 + 1.7 * i, 14.0 + 2.3 * (i % 7)] for i in range(n)], dtype="float64")
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
    ids = [
        m.decode() if isinstance(m, bytes) else str(m) for m in read_molecules(path)["molecule_id"]
    ]
    return proj, ids


def _mixed_traces() -> tuple[np.ndarray, np.ndarray]:
    """5 molecules: rows 0 & 1 identical (mutual nearest), 2 & 3 distinct, 4 undefined-SNR.

    Row 4 has a conserved constant total (acceptor = C - donor) with float32-exact small
    integers, so its ``snr`` is ``NaN`` on disk -> it is unrankable (reported, not dropped).
    """
    rng = np.random.default_rng(7)
    t = 24
    donor = np.empty((5, t), dtype=np.float64)
    acceptor = np.empty((5, t), dtype=np.float64)
    donor[0] = rng.normal(600.0, 90.0, size=t)
    acceptor[0] = rng.normal(500.0, 70.0, size=t)
    donor[1] = donor[0]  # identical to row 0 -> mutual nearest neighbour
    acceptor[1] = acceptor[0]
    donor[2] = rng.normal(1200.0, 30.0, size=t)  # much brighter, tighter
    acceptor[2] = rng.normal(200.0, 15.0, size=t)
    donor[3] = rng.normal(300.0, 120.0, size=t)  # dim, noisy
    acceptor[3] = rng.normal(900.0, 110.0, size=t)
    base = np.tile(np.array([1.0, 3.0, 2.0, 5.0], dtype=np.float64), t // 4)
    donor[4] = base
    acceptor[4] = 10.0 - base  # constant total -> snr NaN
    return donor, acceptor


def test_similar_molecules_matches_pure_core(tmp_path) -> None:
    donor, acceptor = _mixed_traces()
    proj, ids = _build_store(tmp_path / "x.tether", donor, acceptor)
    compute_features(proj)

    core = build_similarity_index(feature_matrix(proj)).query(ids[0])
    conv = similar_molecules(proj, ids[0])
    assert [(n.molecule_id, n.rank) for n in conv] == [(n.molecule_id, n.rank) for n in core]
    np.testing.assert_allclose(
        [n.distance for n in conv], [n.distance for n in core], rtol=1e-12, atol=0
    )


def test_identical_traces_are_mutual_nearest(tmp_path) -> None:
    # Rows 0 and 1 have identical traces -> identical features -> each is the other's
    # nearest neighbour at distance ~0 (a "known fixture trace returns its neighbour").
    donor, acceptor = _mixed_traces()
    proj, ids = _build_store(tmp_path / "x.tether", donor, acceptor)
    compute_features(proj)

    top0 = similar_molecules(proj, ids[0])[0]
    top1 = similar_molecules(proj, ids[1])[0]
    assert top0.molecule_id == ids[1]
    assert top1.molecule_id == ids[0]
    assert top0.distance == pytest.approx(0.0, abs=1e-9)


def test_undefined_feature_molecule_reported_not_dropped(tmp_path) -> None:
    donor, acceptor = _mixed_traces()
    proj, ids = _build_store(tmp_path / "x.tether", donor, acceptor)
    compute_features(proj)  # include_rejected default -> all 5 molecules have a feature row

    index = build_project_similarity_index(proj)
    assert ids[4] in index.unindexed_ids  # the conserved-total molecule is unrankable
    assert index.n_indexed == 4
    # It is not silently gone: 4 indexed + 1 unindexed == the 5 stored molecules.
    assert index.n_indexed + index.n_unindexed == 5
    # A ranking over the other molecules never surfaces the unrankable one...
    assert ids[4] not in {n.molecule_id for n in similar_molecules(proj, ids[0])}
    # ...and every rankable molecule (bar the seed) is accounted for (never-auto-drop).
    assert {n.molecule_id for n in similar_molecules(proj, ids[0])} == set(ids[:4]) - {ids[0]}
    # Using the undefined molecule as the reference fails loudly.
    with pytest.raises(ValueError, match="non-finite"):
        similar_molecules(proj, ids[4])


def test_similar_to_molecules_multi_reference(tmp_path) -> None:
    donor, acceptor = _mixed_traces()
    proj, ids = _build_store(tmp_path / "x.tether", donor, acceptor)
    compute_features(proj)

    result = similar_to_molecules(proj, [ids[0], ids[2]])
    got = {n.molecule_id for n in result}
    assert ids[0] not in got and ids[2] not in got  # both references excluded
    assert ids[4] not in got  # unrankable never appears
    assert got == {ids[1], ids[3]}  # the remaining rankable molecules


def test_similar_before_compute_raises(tmp_path) -> None:
    donor, acceptor = _mixed_traces()
    proj, ids = _build_store(tmp_path / "x.tether", donor, acceptor)
    with pytest.raises(KeyError, match="features"):
        similar_molecules(proj, ids[0])
    with pytest.raises(KeyError, match="features"):
        build_project_similarity_index(proj)


def test_unknown_molecule_id_raises(tmp_path) -> None:
    donor, acceptor = _mixed_traces()
    proj, _ = _build_store(tmp_path / "x.tether", donor, acceptor)
    compute_features(proj)
    with pytest.raises(KeyError, match="not-a-real-id"):
        similar_molecules(proj, "not-a-real-id")

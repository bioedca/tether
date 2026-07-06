# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Store-integrated gradient-boosting quality ranker (M5, FR-ML; PRD §7.5).

Locks :mod:`tether.project.gbranking`: training on a ``.tether``'s ``/features`` + ``/labels``
produces a fitted ranker; ranking scores **every** molecule (labeled and uncurated) into a
never-auto-drop permutation of the full candidate set; apparent precision@k is a valid
fraction; and the degenerate cases (one label class, no labels, no features) are refused
loudly. Needs scikit-learn (base lock, #92) + h5py -> base CI matrix.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("h5py")
pytest.importorskip("sklearn")

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
from tether.ml.features import FEATURE_NAMES  # noqa: E402
from tether.project.core import Project  # noqa: E402
from tether.project.features import compute_features  # noqa: E402
from tether.project.gbranking import (  # noqa: E402
    ranker_precision_at_k,
    ranker_ranking,
    train_ranker,
)
from tether.project.labels import accept, reject  # noqa: E402
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
    keys = [
        k.decode() if isinstance(k, bytes) else str(k) for k in read_molecules(path)["molecule_key"]
    ]
    return proj, keys


def _six_valid_traces() -> tuple[np.ndarray, np.ndarray]:
    """6 molecules with distinct, all-finite features (no NaN feature rows)."""
    rng = np.random.default_rng(11)
    t = 24
    donor = np.empty((6, t), dtype=np.float64)
    acceptor = np.empty((6, t), dtype=np.float64)
    for i in range(6):
        donor[i] = rng.normal(400.0 + 120.0 * i, 40.0 + 5.0 * i, size=t)
        acceptor[i] = rng.normal(900.0 - 110.0 * i, 30.0 + 4.0 * i, size=t)
    return donor, acceptor


def _labeled_store(path: Path) -> tuple[Project, list[str]]:
    """keys[0],[2] accepted; keys[1],[3] rejected; keys[4],[5] left uncurated."""
    donor, acceptor = _six_valid_traces()
    proj, keys = _build_store(path, donor, acceptor)
    compute_features(proj)
    accept(path, keys[0])
    reject(path, keys[1])
    accept(path, keys[2])
    reject(path, keys[3])
    return proj, keys


def _discriminable_store(path: Path) -> tuple[Project, list[str], np.ndarray]:
    """A 24-molecule store whose features separate by label, interleaved good/bad.

    Even rows are clean high-SNR, strongly anticorrelated 'good' traces (accepted); odd rows
    are noisy low-SNR uncorrelated 'bad' traces (rejected). The classes are separable in
    feature space (SNR ~127 vs ~3, total intensity ~1400 vs ~500, anticorrelation ~-1 vs ~0)
    with enough labels per class (12) to exceed ``min_samples_leaf`` — so a *working* ranker
    must discriminate them, and the interleaving makes plain file order a poor baseline the
    ranker can beat. Returns the project, its molecule_keys, and the per-row good mask.
    """
    rng = np.random.default_rng(7)
    t = 40
    n_per = 12
    n = 2 * n_per
    donor = np.empty((n, t), dtype=np.float64)
    acceptor = np.empty((n, t), dtype=np.float64)
    good = np.zeros(n, dtype=bool)
    for j in range(n_per):
        gi, bi = 2 * j, 2 * j + 1  # even = good, odd = bad
        good[gi] = True
        d = rng.normal(600.0, 8.0, size=t)  # clean, low-noise donor
        donor[gi] = d
        acceptor[gi] = 1400.0 - d + rng.normal(0.0, 8.0, size=t)  # strongly anticorrelated
        donor[bi] = rng.normal(250.0, 130.0, size=t)  # noisy, uncorrelated
        acceptor[bi] = rng.normal(250.0, 130.0, size=t)
    proj, keys = _build_store(path, donor, acceptor)
    compute_features(proj)
    for i, is_good in enumerate(good.tolist()):
        (accept if is_good else reject)(proj.path, keys[i])
    return proj, keys, good


def test_train_ranker_fits_on_the_projects_labels(tmp_path) -> None:
    proj, _ = _labeled_store(tmp_path / "x.tether")
    ranker = train_ranker(proj)
    assert ranker.feature_names == FEATURE_NAMES
    assert ranker.n_train == 4  # four human-labeled molecules
    assert ranker.n_good == 2  # two accepted


def test_ranker_ranking_ranks_all_molecules_never_dropping(tmp_path) -> None:
    proj, _ = _labeled_store(tmp_path / "x.tether")
    data = ranking_dataset(proj)
    ranked = ranker_ranking(proj)

    # Every molecule — the 4 labeled AND the 2 uncurated — is ranked exactly once.
    assert ranked.n == 6
    assert set(ranked.molecule_ids) == set(data.molecule_ids)
    assert len(set(ranked.molecule_ids)) == 6


def test_ranker_precision_at_k_is_a_valid_fraction(tmp_path) -> None:
    proj, _ = _labeled_store(tmp_path / "x.tether")
    for k in (1, 2, 4):
        p = ranker_precision_at_k(proj, k)
        assert 0.0 <= p <= 1.0


def test_ranker_discriminates_good_from_bad_and_beats_file_order(tmp_path) -> None:
    # A working store integration must actually separate good from bad — not just wire up an
    # inert model. On separable, per-class-plentiful labels the trained ranker ranks every
    # good trace ahead of every bad one (apparent precision@k = 1) and beats the interleaved
    # file-order baseline, exercising the whole /features -> train -> score -> rank path.
    proj, _, good = _discriminable_store(tmp_path / "x.tether")
    n_good = int(good.sum())  # 12

    ranked = ranker_ranking(proj)
    assert ranked.n == 2 * n_good  # all 24 molecules ranked, none dropped

    p_ranker = ranker_precision_at_k(proj, n_good)
    p_baseline = baseline_precision_at_k(proj, n_good)
    assert p_ranker == pytest.approx(1.0)  # separable -> all good ranked first
    assert p_ranker > p_baseline  # beats the deliberately-interleaved file order


def test_single_class_store_is_refused(tmp_path) -> None:
    # A project where every labeled molecule is accepted cannot train a discriminative ranker.
    donor, acceptor = _six_valid_traces()
    proj, keys = _build_store(tmp_path / "x.tether", donor, acceptor)
    compute_features(proj)
    accept(proj.path, keys[0])
    accept(proj.path, keys[1])
    with pytest.raises(ValueError, match="both accepted and rejected"):
        train_ranker(proj)


def test_no_labels_is_refused(tmp_path) -> None:
    donor, acceptor = _six_valid_traces()
    proj, _ = _build_store(tmp_path / "x.tether", donor, acceptor)
    compute_features(proj)
    with pytest.raises(ValueError, match="no human-labeled molecules"):
        train_ranker(proj)
    with pytest.raises(ValueError, match="no human-labeled molecules"):
        ranker_ranking(proj)


def test_before_compute_features_raises(tmp_path) -> None:
    donor, acceptor = _six_valid_traces()
    proj, _ = _build_store(tmp_path / "x.tether", donor, acceptor)
    with pytest.raises(KeyError, match="features"):
        train_ranker(proj)
    with pytest.raises(KeyError, match="features"):
        ranker_precision_at_k(proj, 2)

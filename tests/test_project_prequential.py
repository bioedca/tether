# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Store-integrated prequential precision@k uplift gate (M5, FR-ML; PRD §7.5, §9 M5).

Locks :mod:`tether.project.prequential`: a project's human-labeled molecules group into
per-video folds by ``movie_id`` in store order; the prequential (interleaved test-then-train)
gate trains the gradient-boosting ranker on the accumulated prior at each video boundary and,
on separable multi-video data, the held-out ranker beats file order and clears the ship-bar;
degenerate cases (one video, no labels, no features, bad k) are refused loudly. Needs
scikit-learn (base lock, #92) + h5py -> base CI matrix.
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
from tether.project.core import Project  # noqa: E402
from tether.project.features import compute_features  # noqa: E402
from tether.project.labels import accept, reject  # noqa: E402
from tether.project.prequential import prequential_folds, ranker_prequential_uplift  # noqa: E402
from tether.project.ranking import ranking_dataset  # noqa: E402

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


def _write_movie(
    path: Path,
    movie_id: str,
    sha256: str,
    donor: np.ndarray,
    acceptor: np.ndarray,
    *,
    create: bool,
) -> None:
    """Append one movie's molecules/traces to ``path`` (creating the store on the first call).

    Coordinates repeat across movies, which is safe: ``molecule_key`` hashes the movie
    ``sha256`` *and* the quantized ``donor_xy`` (``imaging.extract.molecule_key``), so a
    distinct per-movie ``sha256`` keeps every molecule's key globally unique.
    """
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
        movie_id=movie_id,
        sha256=sha256,
        n_frames=t,
        height=64,
        width=64,
        donor_geometry=ChannelGeometry(crop=(1, 1, 64, 64)),
        acceptor_geometry=ChannelGeometry(crop=(1, 65, 64, 128)),
    )
    if create:
        create_project(path, overwrite=True)
    write_extraction(
        path,
        movie=movie,
        molecules=mols,
        traces=traces,
        parsed=_PARSED,
        registration_map=_reg_map(),
    )


def _discriminable_traces(
    n_per_class: int, *, seed: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``2*n_per_class`` interleaved traces: even rows clean/anticorrelated 'good', odd 'bad'.

    Good and bad separate strongly in feature space (SNR ~high vs ~low, anticorrelation ~-1 vs
    ~0), and the interleaving makes plain file order a baseline a working ranker can beat.
    """
    rng = np.random.default_rng(seed)
    t = 40
    n = 2 * n_per_class
    donor = np.empty((n, t), dtype=np.float64)
    acceptor = np.empty((n, t), dtype=np.float64)
    good = np.zeros(n, dtype=bool)
    for j in range(n_per_class):
        gi, bi = 2 * j, 2 * j + 1
        good[gi] = True
        d = rng.normal(600.0, 8.0, size=t)
        donor[gi] = d
        acceptor[gi] = 1400.0 - d + rng.normal(0.0, 8.0, size=t)
        donor[bi] = rng.normal(250.0, 130.0, size=t)
        acceptor[bi] = rng.normal(250.0, 130.0, size=t)
    return donor, acceptor, good


def _multi_movie_store(
    path: Path, *, n_movies: int, n_per_class: int, label: bool = True
) -> tuple[Project, np.ndarray]:
    """A ``.tether`` of ``n_movies`` separable videos; returns the project + store-order good mask.

    Each movie contributes ``2*n_per_class`` interleaved good/bad molecules under its own
    ``movie_id``. Features are computed over the full store, then (``label=True``) every molecule
    is accepted/rejected by its good/bad status in store order.
    """
    goods: list[np.ndarray] = []
    for m in range(n_movies):
        donor, acceptor, good = _discriminable_traces(n_per_class, seed=17 + m)
        _write_movie(path, f"mov-{m}", f"{m + 1:064x}", donor, acceptor, create=(m == 0))
        goods.append(good)
    good_store_order = np.concatenate(goods)

    proj = Project.open(path)
    compute_features(proj)
    if label:
        keys = [
            k.decode() if isinstance(k, bytes) else str(k)
            for k in read_molecules(path)["molecule_key"]
        ]
        for key, is_good in zip(keys, good_store_order.tolist(), strict=True):
            (accept if is_good else reject)(path, key)
    return proj, good_store_order


# --- folds -------------------------------------------------------------------


def test_folds_group_labeled_molecules_by_movie_in_store_order(tmp_path) -> None:
    proj, _ = _multi_movie_store(tmp_path / "x.tether", n_movies=3, n_per_class=8)
    folds = prequential_folds(proj)

    assert [f.movie_id for f in folds] == ["mov-0", "mov-1", "mov-2"]  # store / curation order
    assert all(f.n == 16 for f in folds)  # 8 good + 8 bad labeled per movie
    for f in folds:
        assert len(set(f.molecule_ids)) == f.n  # unique ids, none dropped
        assert f.X.shape[0] == f.n
        assert sum(f.is_good) == 8


def test_folds_preserve_within_movie_store_order(tmp_path) -> None:
    # The uplift's baseline is file_order_ranking(fold.molecule_ids), so each fold must hold its
    # movie's labeled molecules in feature-table (store) order. Re-derive that order independently
    # of the module under test and lock it as an ordered subsequence: a within-movie reorder (e.g. a
    # sort-by-id regression in _folds_and_feature_names) would corrupt the baseline and fail here.
    proj, _ = _multi_movie_store(tmp_path / "x.tether", n_movies=3, n_per_class=8)
    data = ranking_dataset(proj)
    is_good = data.is_good
    mols = read_molecules(proj.path)

    def _s(v: object) -> str:
        return v.decode() if isinstance(v, bytes) else str(v)

    movie_by_id = {
        _s(mid): _s(mv) for mid, mv in zip(mols["molecule_id"], mols["movie_id"], strict=True)
    }
    for f in prequential_folds(proj):
        expected = [
            mid for mid in data.molecule_ids if mid in is_good and movie_by_id[mid] == f.movie_id
        ]
        assert list(f.molecule_ids) == expected  # ordered subsequence, not just membership
    # The ordered check only has teeth if store order isn't already id-sorted (ids are UUIDs).
    assert list(data.molecule_ids) != sorted(data.molecule_ids)


def test_folds_exclude_uncurated_molecules(tmp_path) -> None:
    # Feature every molecule but curate only the first movie -> only it yields a fold.
    proj, good = _multi_movie_store(tmp_path / "x.tether", n_movies=2, n_per_class=8, label=False)
    keys = [
        k.decode() if isinstance(k, bytes) else str(k)
        for k in read_molecules(proj.path)["molecule_key"]
    ]
    for i in range(16):  # label only movie 0's 16 molecules
        (accept if good[i] else reject)(proj.path, keys[i])

    folds = prequential_folds(proj)
    assert [f.movie_id for f in folds] == ["mov-0"]  # movie 1 uncurated -> no fold
    assert folds[0].n == 16


# --- the prequential gate ----------------------------------------------------


def test_gate_ships_on_separable_multivideo(tmp_path) -> None:
    # Held out on each later video, the ranker trained on the prior video(s) surfaces the good
    # traces far earlier than the interleaved file order -> clears the 10-pt ship-bar.
    proj, _ = _multi_movie_store(tmp_path / "x.tether", n_movies=3, n_per_class=8)
    result = ranker_prequential_uplift(proj, k=8)

    assert result.skipped_movie_ids == ("mov-0",)  # the first video has no prior
    assert [v.movie_id for v in result.per_video] == ["mov-1", "mov-2"]
    for v in result.per_video:
        assert v.n_labeled == 16
        assert v.ranker > v.baseline  # held-out ranker beats file order
        assert v.uplift > 0.0
    assert result.median_uplift_pts >= 10.0
    assert result.shipped is True


def test_gate_uplift_is_held_out_not_apparent(tmp_path) -> None:
    # Sanity: the evaluated videos are scored by a model that never trained on them (the prior
    # only ever grows), so the number is a genuine generalization measurement.
    proj, _ = _multi_movie_store(tmp_path / "x.tether", n_movies=3, n_per_class=8)
    result = ranker_prequential_uplift(proj, k=8)
    # mov-0 folds into training but is never itself an evaluation unit.
    assert "mov-0" not in {v.movie_id for v in result.per_video}


def test_custom_ship_bar_can_withhold_a_ship(tmp_path) -> None:
    proj, _ = _multi_movie_store(tmp_path / "x.tether", n_movies=3, n_per_class=8)
    strict = ranker_prequential_uplift(proj, k=8, ship_bar_pts=200.0)
    assert strict.median_uplift_pts < 200.0
    assert strict.shipped is False  # a positive uplift still fails an impossibly strict bar


def test_single_movie_project_has_no_held_out_video(tmp_path) -> None:
    proj, _ = _multi_movie_store(tmp_path / "x.tether", n_movies=1, n_per_class=8)
    with pytest.raises(ValueError, match="no held-out video had a trainable prior"):
        ranker_prequential_uplift(proj, k=8)


def test_no_labels_raises(tmp_path) -> None:
    proj, _ = _multi_movie_store(tmp_path / "x.tether", n_movies=2, n_per_class=8, label=False)
    with pytest.raises(ValueError, match="no video folds"):
        ranker_prequential_uplift(proj, k=8)


def test_k_must_be_positive_int(tmp_path) -> None:
    proj, _ = _multi_movie_store(tmp_path / "x.tether", n_movies=3, n_per_class=8)
    with pytest.raises(ValueError, match="positive integer"):
        ranker_prequential_uplift(proj, 0)


def test_broken_store_labeled_molecule_without_molecules_row_raises(tmp_path, monkeypatch) -> None:
    # Defensive guard in _folds_and_feature_names: a labeled+featured molecule whose /molecules
    # row is missing (a corrupt store) is surfaced loudly, never silently mis-grouped. Simulate
    # the inconsistency by making read_molecules omit one molecule while its /features + /labels
    # rows remain (curation_labels reads /molecules directly, so is_good still includes it).
    import tether.imaging.extract as _extract  # noqa: PLC0415

    proj, _ = _multi_movie_store(tmp_path / "x.tether", n_movies=2, n_per_class=8)
    real_read = _extract.read_molecules
    monkeypatch.setattr(_extract, "read_molecules", lambda path: real_read(path)[:-1])
    with pytest.raises(ValueError, match="no /molecules movie_id"):
        prequential_folds(proj)


def test_before_compute_features_raises(tmp_path) -> None:
    path = tmp_path / "x.tether"
    donor, acceptor, _ = _discriminable_traces(8, seed=1)
    _write_movie(path, "mov-0", f"{1:064x}", donor, acceptor, create=True)
    proj = Project.open(path)
    with pytest.raises(KeyError, match="features"):
        prequential_folds(proj)
    with pytest.raises(KeyError, match="features"):
        ranker_prequential_uplift(proj, 4)

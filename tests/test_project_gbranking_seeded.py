# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Weighted provisional-prior training fold for the quality ranker (M5, FR-ML; PRD §7.5).

Locks the seeding-seam half of :mod:`tether.project.gbranking`: the ranker now trains on a
project's human accept/reject labels **plus** the provisional ``/labels`` priors (Deep-LASI /
cross-condition seeds), each folded in at its §7.5 cold-start-decayed ``sample_weight``
(``w = w₀/(1 + n_human)``). The load-bearing separations this file pins:

* a **human** label supersedes a provisional prior on the same molecule (full weight, once);
* a provisional prior is trained at its decayed weight and that weight **shrinks** as human labels
  accrue (the cold-start decay, shared with ``recompute_label_weights``);
* **evaluation stays human-only** — apparent precision@k / ``is_good`` never count a provisional
  seed as ground truth, so seeds train the model but are never scored;
* seeds can bootstrap a condition a human has not curated at all (both classes among the priors),
  yet apparent precision@k there is *undefined*, surfaced loudly (never a fabricated ``0``).

The methodology — training on down-weighted pseudo-labeled/seed priors alongside ground-truth
labels while evaluating only against ground truth — is standard semi-supervised practice
[Wang2022; Liu2024]. Needs scikit-learn (base lock, #92) + h5py -> the base CI matrix.

References
----------
[Wang2022] Wang et al. "Debiased Learning from Naturally Imbalanced Pseudo-Labels." CVPR (2022) —
    pseudo-labels (seed predictions) are used to adapt a model but are distinguished from
    ground-truth training labels, which remain the evaluation basis.
[Liu2024] Liu et al. "Enhanced Semi-Supervised Medical Image Classification Based on Dynamic Sample
    Reweighting..." Mathematics (2024) — reweighting less-reliable (pseudo-labeled) samples below
    trusted labels during training.
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
from tether.ml.weighting import DEFAULT_SEED_WEIGHT, seed_weight  # noqa: E402
from tether.project.core import Project  # noqa: E402
from tether.project.features import compute_features  # noqa: E402
from tether.project.gbranking import (  # noqa: E402
    ranker_precision_at_k,
    ranker_ranking,
    train_ranker,
    weighted_training_set,
)
from tether.project.labels import (  # noqa: E402
    LABEL_SOURCE_CROSS_CONDITION,
    LABEL_SOURCE_DEEPLASI,
    CurationLabel,
    accept,
    reject,
    set_curation_label,
)
from tether.project.ranking import ranking_dataset  # noqa: E402
from tether.project.weighting import human_counts_by_condition  # noqa: E402

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
    """A single-condition ``.tether`` with corrected traces exactly ``donor``/``acceptor``."""
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


def _separable_store(path: Path, n_per: int = 8) -> tuple[Project, list[str], np.ndarray]:
    """A ``2*n_per``-molecule featured (unlabeled) store separable by label in feature space.

    Even rows are clean, high-SNR, strongly anticorrelated 'good' traces; odd rows are noisy,
    uncorrelated 'bad' traces (the :mod:`tests.test_project_gbranking` discriminable pattern). The
    caller supplies the labels/seeds; returns the project, its ``molecule_key`` list, and the
    per-row good mask (``good[i]`` is ``True`` for the good molecules).
    """
    rng = np.random.default_rng(7)
    t = 40
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
    return proj, keys, good


def _id_by_key(path: Path) -> dict[str, str]:
    mols = read_molecules(path)

    def _s(x: object) -> str:
        return x.decode() if isinstance(x, bytes) else str(x)

    return {_s(k): _s(i) for k, i in zip(mols["molecule_key"], mols["molecule_id"], strict=True)}


def _seed(path: Path, key: str, label: CurationLabel) -> None:
    """Write a provisional cross-condition seed to ``/labels`` (never touches curation_label)."""
    set_curation_label(path, key, label, source=LABEL_SOURCE_CROSS_CONDITION)


# --- the human-only baseline is unchanged --------------------------------------------------------


def test_no_provisional_rows_trains_human_only_at_unit_weight(tmp_path) -> None:
    # With no provisional priors the training set is exactly the human labels, all at weight 1.0 —
    # the fit is identical to the pre-fold unweighted model (backward compatibility).
    proj, keys, _ = _separable_store(tmp_path / "p.tether")
    accept(proj.path, keys[0])
    reject(proj.path, keys[1])
    accept(proj.path, keys[2])
    reject(proj.path, keys[3])

    ts = weighted_training_set(proj)
    ids = _id_by_key(proj.path)
    assert ts.n_train == 4
    assert set(ts.molecule_ids) == {ids[keys[i]] for i in (0, 1, 2, 3)}
    assert np.all(ts.sample_weight == 1.0)


# --- provisional priors fold in, weighted --------------------------------------------------------


def test_provisional_seed_enters_training_with_decayed_weight(tmp_path) -> None:
    proj, keys, _ = _separable_store(tmp_path / "p.tether")
    accept(proj.path, keys[0])
    reject(proj.path, keys[1])
    accept(proj.path, keys[2])
    reject(proj.path, keys[3])  # n_human = 4 (one condition)
    _seed(proj.path, keys[4], CurationLabel.ACCEPT)  # a provisional prior on an uncurated molecule

    ts = weighted_training_set(proj)
    ids = _id_by_key(proj.path)
    w_by_id = dict(zip(ts.molecule_ids, ts.sample_weight.tolist(), strict=True))
    y_by_id = dict(zip(ts.molecule_ids, ts.y.tolist(), strict=True))

    assert ts.n_train == 5  # four human + one seed
    seed_id = ids[keys[4]]
    assert w_by_id[seed_id] == pytest.approx(DEFAULT_SEED_WEIGHT / 5.0)  # w₀/(1+4)
    assert y_by_id[seed_id] is True  # the seed was an ACCEPT
    for i in (0, 1, 2, 3):  # human rows keep full weight
        assert w_by_id[ids[keys[i]]] == pytest.approx(1.0)


def test_human_label_supersedes_a_provisional_prior_on_the_same_molecule(tmp_path) -> None:
    proj, keys, _ = _separable_store(tmp_path / "p.tether")
    reject(proj.path, keys[0])  # human REJECT
    accept(proj.path, keys[2])  # a second class so the set is non-degenerate
    _seed(proj.path, keys[0], CurationLabel.ACCEPT)  # a conflicting provisional ACCEPT on keys[0]

    ts = weighted_training_set(proj)
    ids = _id_by_key(proj.path)
    y_by_id = dict(zip(ts.molecule_ids, ts.y.tolist(), strict=True))
    w_by_id = dict(zip(ts.molecule_ids, ts.sample_weight.tolist(), strict=True))

    assert ts.n_train == 2  # keys[0] counted ONCE (human), plus keys[2]; the seed adds no row
    assert y_by_id[ids[keys[0]]] is False  # human reject wins over the provisional accept
    assert w_by_id[ids[keys[0]]] == pytest.approx(1.0)  # at full human weight


def test_evaluation_ground_truth_stays_human_only(tmp_path) -> None:
    # A provisional seed trains the model but is never counted as evaluation ground truth: is_good
    # (and thus apparent precision@k) spans the human labels only.
    proj, keys, _ = _separable_store(tmp_path / "p.tether")
    accept(proj.path, keys[0])
    reject(proj.path, keys[1])  # two human labels
    _seed(proj.path, keys[2], CurationLabel.ACCEPT)  # provisional-only molecule

    is_good = ranking_dataset(proj).is_good
    ids = _id_by_key(proj.path)
    assert len(is_good) == 2  # the seed is NOT in the human ground truth
    assert ids[keys[2]] not in is_good
    assert 0.0 <= ranker_precision_at_k(proj, 2) <= 1.0


# --- seeds can bootstrap a humanly-uncurated condition -------------------------------------------


def test_seeds_alone_enable_training_and_ranking_without_human_labels(tmp_path) -> None:
    # A condition with zero human labels but both provisional classes now trains + ranks — before
    # the fold this refused ("no human-labeled molecules").
    proj, keys, good = _separable_store(tmp_path / "p.tether", n_per=8)
    for i, is_good in enumerate(good.tolist()):
        _seed(proj.path, keys[i], CurationLabel.ACCEPT if is_good else CurationLabel.REJECT)

    ranker = train_ranker(proj)
    assert ranker.n_train == 16  # all molecules seeded

    ranked = ranker_ranking(proj)
    assert ranked.n == 16  # every molecule ranked, none dropped


def test_apparent_precision_at_k_undefined_without_human_labels(tmp_path) -> None:
    proj, keys, good = _separable_store(tmp_path / "p.tether", n_per=6)
    for i, is_good in enumerate(good.tolist()):
        _seed(proj.path, keys[i], CurationLabel.ACCEPT if is_good else CurationLabel.REJECT)
    with pytest.raises(ValueError, match="no human-labeled molecules"):
        ranker_precision_at_k(proj, 3)


def test_single_class_provisional_only_is_refused(tmp_path) -> None:
    proj, keys, _ = _separable_store(tmp_path / "p.tether")
    _seed(proj.path, keys[0], CurationLabel.ACCEPT)
    _seed(proj.path, keys[2], CurationLabel.ACCEPT)  # both seeds accept -> one class
    with pytest.raises(ValueError, match="both accepted and rejected"):
        train_ranker(proj)


def test_seed_priors_train_a_model_that_separates_good_from_bad(tmp_path) -> None:
    # The fold is not bookkeeping: provisional priors alone must teach a working ranker. On
    # separable data seeded good=accept / bad=reject, every good trace ranks ahead of every bad.
    proj, keys, good = _separable_store(tmp_path / "p.tether", n_per=8)
    for i, is_good in enumerate(good.tolist()):
        _seed(proj.path, keys[i], CurationLabel.ACCEPT if is_good else CurationLabel.REJECT)
    ids = _id_by_key(proj.path)
    good_ids = {ids[keys[i]] for i in range(16) if good[i]}

    ranked = ranker_ranking(proj)
    assert set(ranked.molecule_ids[:8]) == good_ids  # the 8 goods take the top 8 ranks


# --- the decay tracks human evidence -------------------------------------------------------------


def test_seed_training_weight_decays_as_human_labels_accrue(tmp_path) -> None:
    proj, keys, _ = _separable_store(tmp_path / "p.tether")
    _seed(proj.path, keys[10], CurationLabel.ACCEPT)  # a seed on an uncurated molecule
    accept(proj.path, keys[0])  # n_human = 1

    ids = _id_by_key(proj.path)
    seed_id = ids[keys[10]]
    w_one = dict(zip(*_ts_weight_arrays(weighted_training_set(proj)), strict=True))[seed_id]
    assert w_one == pytest.approx(DEFAULT_SEED_WEIGHT / 2.0)  # w₀/(1+1)

    reject(proj.path, keys[1])
    accept(proj.path, keys[2])
    reject(proj.path, keys[3])  # n_human = 4 now
    w_four = dict(zip(*_ts_weight_arrays(weighted_training_set(proj)), strict=True))[seed_id]
    assert w_four == pytest.approx(DEFAULT_SEED_WEIGHT / 5.0)  # w₀/(1+4)
    assert w_four < w_one  # more human evidence -> a more decayed seed


def test_custom_w0_flows_into_the_seed_weight(tmp_path) -> None:
    proj, keys, _ = _separable_store(tmp_path / "p.tether")
    accept(proj.path, keys[0])
    reject(proj.path, keys[1])  # n_human = 2
    _seed(proj.path, keys[4], CurationLabel.ACCEPT)

    ts = weighted_training_set(proj, w0=0.6)
    ids = _id_by_key(proj.path)
    w_by_id = dict(zip(ts.molecule_ids, ts.sample_weight.tolist(), strict=True))
    assert w_by_id[ids[keys[4]]] == pytest.approx(seed_weight(2, w0=0.6))  # 0.6/(1+2)


# --- the /labels event stream: latest provisional wins, a clear removes the prior ----------------


def test_latest_provisional_event_wins(tmp_path) -> None:
    proj, keys, _ = _separable_store(tmp_path / "p.tether")
    _seed(proj.path, keys[4], CurationLabel.ACCEPT)
    _seed(proj.path, keys[4], CurationLabel.REJECT)  # a re-seed supersedes the earlier accept

    ts = weighted_training_set(proj)
    ids = _id_by_key(proj.path)
    y_by_id = dict(zip(ts.molecule_ids, ts.y.tolist(), strict=True))
    assert y_by_id[ids[keys[4]]] is False  # the latest event (reject) is the training label


def test_provisional_clear_drops_the_seed_from_training(tmp_path) -> None:
    proj, keys, _ = _separable_store(tmp_path / "p.tether")
    _seed(proj.path, keys[4], CurationLabel.ACCEPT)
    _seed(proj.path, keys[4], CurationLabel.UNCURATED)  # a provisional clear
    _seed(proj.path, keys[6], CurationLabel.ACCEPT)  # an unrelated live seed

    ts = weighted_training_set(proj)
    ids = _id_by_key(proj.path)
    assert ids[keys[4]] not in ts.molecule_ids  # cleared seed is no longer a training row
    assert ids[keys[6]] in ts.molecule_ids  # the other seed still trains


def test_weighted_training_set_is_deterministic(tmp_path) -> None:
    proj, keys, _ = _separable_store(tmp_path / "p.tether")
    accept(proj.path, keys[0])
    reject(proj.path, keys[1])
    _seed(proj.path, keys[4], CurationLabel.ACCEPT)

    a = weighted_training_set(proj)
    b = weighted_training_set(proj)
    assert a.molecule_ids == b.molecule_ids
    np.testing.assert_array_equal(a.y, b.y)
    np.testing.assert_array_equal(a.sample_weight, b.sample_weight)
    np.testing.assert_array_equal(a.X, b.X)


# --- the extracted per-condition human-count helper ----------------------------------------------


def test_human_counts_by_condition_groups_and_counts(tmp_path) -> None:
    counts = human_counts_by_condition(
        ["cA", "cA", "cB", "cB", "cB"],
        [
            int(CurationLabel.ACCEPT),
            int(CurationLabel.UNCURATED),
            int(CurationLabel.REJECT),
            int(CurationLabel.ACCEPT),
            int(CurationLabel.UNCURATED),
        ],
    )
    assert counts == {"cA": 1, "cB": 2}


def test_human_counts_by_condition_empty_and_mismatch() -> None:
    assert human_counts_by_condition([], []) == {}
    with pytest.raises(ValueError, match="equal length"):
        human_counts_by_condition(["cA"], [1, 0])


def _ts_weight_arrays(ts) -> tuple[list[str], list[float]]:
    return ts.molecule_ids, ts.sample_weight.tolist()


def _append_raw_label(path: Path, key: str, source: str, label_value: int) -> None:
    """Append a ``/labels`` row with an arbitrary ``source``, bypassing ``set_curation_label``.

    Lets a test drive the fold's source allow-list with a source **outside** the ``LABEL_SOURCES``
    vocabulary (which the public writer rejects) — the defensive check that only the two seed
    sources ever enter training.
    """
    import h5py  # noqa: PLC0415

    from tether.io.schema import LABELS_DTYPE, TABLE  # noqa: PLC0415

    with h5py.File(path, "r+") as f:
        table = f["labels"][TABLE]
        row = np.zeros(1, dtype=LABELS_DTYPE)
        row["molecule_key"] = key
        row["labeler"] = "test"
        row["timestamp"] = "2026-01-01T00:00:00+00:00"
        row["source_file"] = "test.tether"
        row["source"] = source
        row["weight"] = 1.0
        row["label_value"] = int(label_value)
        row["condition_id"] = ""
        n = table.shape[0]
        table.resize((n + 1,))
        table[n:] = row


def test_deeplasi_provisional_source_also_folds_in(tmp_path) -> None:
    # Both seed sources train: a deeplasi-provisional prior folds in exactly like a cross-condition
    # one, at the same decayed weight.
    proj, keys, _ = _separable_store(tmp_path / "p.tether")
    accept(proj.path, keys[0])
    reject(proj.path, keys[1])  # n_human = 2
    set_curation_label(proj.path, keys[4], CurationLabel.ACCEPT, source=LABEL_SOURCE_DEEPLASI)

    ts = weighted_training_set(proj)
    ids = _id_by_key(proj.path)
    w_by_id = dict(zip(ts.molecule_ids, ts.sample_weight.tolist(), strict=True))
    assert ids[keys[4]] in ts.molecule_ids
    assert w_by_id[ids[keys[4]]] == pytest.approx(DEFAULT_SEED_WEIGHT / 3.0)  # w₀/(1+2)


def test_unknown_label_source_is_ignored_by_the_fold(tmp_path) -> None:
    # Only PROVISIONAL_LABEL_SOURCES train; any other non-human source is ignored, so a future
    # annotation source can never silently enter the fit.
    proj, keys, _ = _separable_store(tmp_path / "p.tether")
    accept(proj.path, keys[0])
    reject(proj.path, keys[1])  # a valid two-class human set
    _append_raw_label(proj.path, keys[4], "future-annotation-source", int(CurationLabel.ACCEPT))

    ts = weighted_training_set(proj)
    ids = _id_by_key(proj.path)
    assert ids[keys[4]] not in ts.molecule_ids  # the unknown-source row did not enter training
    assert ts.n_train == 2  # only the two human labels


def test_precision_at_k_reports_no_human_over_single_class_provisional(tmp_path) -> None:
    # A provisional-only, single-class project: apparent precision@k is undefined (no human ground
    # truth), and that must be surfaced before — and instead of — the model's "both classes" error.
    proj, keys, _ = _separable_store(tmp_path / "p.tether")
    _seed(proj.path, keys[0], CurationLabel.ACCEPT)
    _seed(proj.path, keys[2], CurationLabel.ACCEPT)  # provisional-only, one class
    with pytest.raises(ValueError, match="no human-labeled molecules"):
        ranker_precision_at_k(proj, 2)

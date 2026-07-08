# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Store-integrated active-learning "recommended next" badge (M5, FR-ML; PRD §7.5, §9 M5).

Locks :mod:`tether.project.active`: over a ``.tether`` the active-learning badge names the
single **uncurated** molecule of maximal predictive uncertainty **without reordering the fixed
within-video sweep** (the §9 M5 acceptance — the sweep is byte-identical to
:func:`~tether.project.gbranking.ranker_ranking`); the badge matches the pure uncertainty-sampling
pick over the same scores; an all-curated project has a full sweep but no badge (never a
fabricated pick); and an untrainable project is refused loudly. Needs scikit-learn (base lock,
#92) + h5py -> base CI matrix.
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
from tether.ml.active import informativeness  # noqa: E402
from tether.project.active import ActiveRecommendation, next_recommendation  # noqa: E402
from tether.project.core import Project  # noqa: E402
from tether.project.features import compute_features  # noqa: E402
from tether.project.gbranking import ranker_ranking, score_molecules  # noqa: E402
from tether.project.labels import CurationLabel, accept, reject  # noqa: E402

_PARSED = parse_filename("Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif")
_WINDOW = 21
_UNCURATED = int(CurationLabel.UNCURATED)


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


def _discriminable_traces(n_good: int, n_bad: int, n_extra: int) -> np.ndarray:
    """Interleaved good/bad separable traces + ``n_extra`` unlabeled tail molecules.

    Even of the first ``2*min`` rows are clean high-SNR anticorrelated 'good', odd are noisy
    'bad'; the ``n_extra`` tail rows span the range so their scores differ. Returns donor,
    acceptor, and the boolean good mask over just the first ``n_good + n_bad`` labeled rows.
    """
    rng = np.random.default_rng(7)
    t = 40
    n = n_good + n_bad + n_extra
    donor = np.empty((n, t), dtype=np.float64)
    acceptor = np.empty((n, t), dtype=np.float64)
    good = np.zeros(n_good + n_bad, dtype=bool)
    for j in range(n_good + n_bad):
        if j % 2 == 0:  # even = good
            good[j] = True
            d = rng.normal(600.0, 8.0, size=t)
            donor[j] = d
            acceptor[j] = 1400.0 - d + rng.normal(0.0, 8.0, size=t)
        else:  # odd = bad
            donor[j] = rng.normal(250.0, 130.0, size=t)
            acceptor[j] = rng.normal(250.0, 130.0, size=t)
    # Tail (uncurated) molecules: a spread of clean-ish -> noisy so their P(good) differs.
    for e in range(n_extra):
        j = n_good + n_bad + e
        noise = 8.0 + 40.0 * e
        d = rng.normal(600.0 - 60.0 * e, noise, size=t)
        donor[j] = d
        acceptor[j] = 1400.0 - d + rng.normal(0.0, noise, size=t)
    return donor, acceptor, good


def _partial_store(path: Path) -> tuple[Project, list[str], np.ndarray]:
    """A store with 24 labeled (12 good / 12 bad) molecules + 4 uncurated tail molecules."""
    donor, acceptor, good = _discriminable_traces(n_good=12, n_bad=12, n_extra=4)
    proj, keys = _build_store(path, donor, acceptor)
    compute_features(proj)
    for i, is_good in enumerate(good.tolist()):  # labels only the first 24; keys[24:] uncurated
        (accept if is_good else reject)(proj.path, keys[i])
    return proj, keys, good


def test_next_recommendation_does_not_reorder_the_fixed_sweep(tmp_path) -> None:
    # The §9 M5 acceptance: active learning surfaces its cue WITHOUT reordering the fixed
    # within-video sweep -> the returned sweep is byte-identical to ranker_ranking's.
    proj, _, _ = _partial_store(tmp_path / "x.tether")
    rec = next_recommendation(proj)
    assert isinstance(rec, ActiveRecommendation)

    sweep_order = ranker_ranking(proj).molecule_ids
    assert rec.sweep.molecule_ids == sweep_order  # not re-ranked by the badge
    assert rec.sweep.n == 28  # every molecule kept (never auto-drop)
    assert len(set(rec.sweep.molecule_ids)) == 28


def test_next_recommendation_badge_is_the_most_uncertain_uncurated(tmp_path) -> None:
    proj, _, _ = _partial_store(tmp_path / "x.tether")
    rec = next_recommendation(proj)
    assert rec.badge is not None

    # Independently recompute the expected pick from the same trained scores: the uncurated
    # molecule with the highest uncertainty-sampling informativeness, ties on ascending id.
    scored = score_molecules(proj)
    data = scored.dataset
    u = informativeness(scored.scores)
    candidates = [
        i
        for i in range(len(data.molecule_ids))
        if data.curation_label[i] == _UNCURATED and np.isfinite(u[i])
    ]
    assert len(candidates) == 4  # the 4 tail molecules are the candidate pool
    expected = min(candidates, key=lambda i: (-float(u[i]), data.molecule_ids[i]))

    assert rec.badge.molecule_id == data.molecule_ids[expected]
    assert rec.recommended_id == data.molecule_ids[expected]
    # The recommendation is genuinely uncurated (never a molecule a human already decided).
    picked = data.molecule_ids.index(rec.badge.molecule_id)
    assert data.curation_label[picked] == _UNCURATED
    assert rec.badge.informativeness == pytest.approx(float(u[expected]))


def test_next_recommendation_is_deterministic(tmp_path) -> None:
    proj, _, _ = _partial_store(tmp_path / "x.tether")
    a = next_recommendation(proj)
    b = next_recommendation(proj)
    assert a.recommended_id == b.recommended_id
    assert a.sweep.molecule_ids == b.sweep.molecule_ids


def test_next_recommendation_no_badge_when_all_curated_but_sweep_is_full(tmp_path) -> None:
    # Every molecule labeled (both classes present) -> nothing left to recommend: badge is None,
    # never a fabricated pick, and the sweep still ranks every molecule.
    donor, acceptor, good = _discriminable_traces(n_good=12, n_bad=12, n_extra=0)
    proj, keys = _build_store(tmp_path / "x.tether", donor, acceptor)
    compute_features(proj)
    for i, is_good in enumerate(good.tolist()):
        (accept if is_good else reject)(proj.path, keys[i])

    rec = next_recommendation(proj)
    assert rec.badge is None
    assert rec.recommended_id is None
    assert rec.sweep.n == 24  # full sweep, nothing dropped


def test_next_recommendation_refuses_an_untrainable_project(tmp_path) -> None:
    # A single-class project cannot train a discriminative ranker -> raise, never fabricate a
    # recommendation over an untrainable project.
    donor, acceptor, _ = _discriminable_traces(n_good=12, n_bad=12, n_extra=4)
    proj, keys = _build_store(tmp_path / "x.tether", donor, acceptor)
    compute_features(proj)
    accept(proj.path, keys[0])
    accept(proj.path, keys[2])  # both accepted, no rejects
    with pytest.raises(ValueError, match="both accepted and rejected"):
        next_recommendation(proj)

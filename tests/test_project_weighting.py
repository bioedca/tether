# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Store-integrated cold-start label-weight recompute (M5, FR-ML; PRD §7.5).

Locks :mod:`tether.project.weighting`: recomputing a ``.tether``'s ``/labels`` weights writes the
§7.5 cold-start decay back into the frozen ``weight`` field — human rows at full weight, a
provisional/seed prior down-weighted by its condition's human-label count. Contains the §9 M5
weight-decay acceptance: a provisional label's effective weight drops below ``0.2·w₀`` after a
handful of human labels. Needs h5py (base lock) for the ``.tether`` store.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("numpy")
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
from tether.ml.weighting import DEFAULT_SEED_WEIGHT  # noqa: E402
from tether.project.core import Project  # noqa: E402
from tether.project.labels import (  # noqa: E402
    LABEL_SOURCE_DEEPLASI,
    LABEL_SOURCE_HUMAN,
    CurationLabel,
    accept,
    read_labels,
    reject,
    set_curation_label,
)
from tether.project.weighting import recompute_label_weights  # noqa: E402

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


def _traces(n: int, t: int = 12) -> MoleculeTraces:
    intensity = np.full((n, t), 500.0, dtype="float64")
    return MoleculeTraces(
        donor=_integrated(intensity),
        acceptor=_integrated(intensity),
        donor_patches=np.zeros((n, _WINDOW, _WINDOW), dtype="float32"),
        acceptor_patches=np.zeros((n, _WINDOW, _WINDOW), dtype="float32"),
        window=_WINDOW,
        disk_radius=3.0,
        ring_inner=6.0,
        ring_outer=8.0,
        bg_window=10,
    )


def _molecules(n: int) -> ColocalizedMolecules:
    coords = np.array([[12.0 + 1.7 * i, 14.0 + 2.3 * (i % 7)] for i in range(n)], dtype="float64")
    return ColocalizedMolecules(
        donor_xy=coords,
        acceptor_xy=coords,
        acceptor_detected=np.zeros(n, dtype=bool),
        donor_index=np.arange(n, dtype=np.intp),
        acceptor_index=np.full(n, -1, dtype=np.intp),
    )


def _movie(movie_id: str, sha_char: str, t: int) -> MovieMetadata:
    return MovieMetadata(
        movie_id=movie_id,
        sha256=sha_char * 64,
        n_frames=t,
        height=64,
        width=64,
        donor_geometry=ChannelGeometry(crop=(1, 1, 64, 64)),
        acceptor_geometry=ChannelGeometry(crop=(1, 65, 64, 128)),
    )


def _keys(path: Path) -> list[str]:
    return [
        k.decode() if isinstance(k, bytes) else str(k) for k in read_molecules(path)["molecule_key"]
    ]


def _one_condition_store(path: Path, n: int) -> tuple[Project, list[str]]:
    """A ``.tether`` with ``n`` molecules in a single condition (one movie)."""
    t = 12
    create_project(path, overwrite=True)
    write_extraction(
        path,
        movie=_movie("mov-1", "a", t),
        molecules=_molecules(n),
        traces=_traces(n, t),
        parsed=parse_filename("Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif"),
        registration_map=_reg_map(),
    )
    return Project.open(path), _keys(path)


def _seed_weight_of(path: Path, source: str = LABEL_SOURCE_DEEPLASI) -> float:
    """The (single) provisional-source ``/labels`` row's weight."""
    rows = read_labels(path)
    src = np.array([s.decode() if isinstance(s, bytes) else str(s) for s in rows["source"]])
    seeds = rows["weight"][src == source]
    assert seeds.shape[0] == 1, f"expected exactly one {source} row, got {seeds.shape[0]}"
    return float(seeds[0])


def test_recompute_decays_provisional_below_one_fifth_after_five_human_labels(tmp_path) -> None:
    # PLAN §9 M5 weight-decay acceptance. One deeplasi-provisional seed on keys[0]; five *other*
    # molecules get a human accept/reject (n_human = 5). After recompute the seed's effective
    # weight is w₀/(1+5) = w₀/6 < 0.2·w₀.
    proj, keys = _one_condition_store(tmp_path / "p.tether", n=6)
    set_curation_label(
        proj.path,
        keys[0],
        CurationLabel.ACCEPT,
        source=LABEL_SOURCE_DEEPLASI,
        weight=DEFAULT_SEED_WEIGHT,
    )
    accept(proj.path, keys[1])
    reject(proj.path, keys[2])
    accept(proj.path, keys[3])
    reject(proj.path, keys[4])
    accept(proj.path, keys[5])  # five human-labeled molecules

    n_rewritten = recompute_label_weights(proj)

    assert n_rewritten == 6  # one seed + five human rows
    seed = _seed_weight_of(proj.path)
    assert seed == pytest.approx(DEFAULT_SEED_WEIGHT / 6.0)
    assert seed < 0.2 * DEFAULT_SEED_WEIGHT


def test_recompute_full_seed_weight_when_no_human_labels(tmp_path) -> None:
    # Cold start: a lone seed in an unlabeled condition keeps the full seed weight w₀.
    proj, keys = _one_condition_store(tmp_path / "p.tether", n=3)
    set_curation_label(proj.path, keys[0], CurationLabel.ACCEPT, source=LABEL_SOURCE_DEEPLASI)
    recompute_label_weights(proj)
    assert _seed_weight_of(proj.path) == pytest.approx(DEFAULT_SEED_WEIGHT)


def test_recompute_resets_human_rows_to_full_weight(tmp_path) -> None:
    # A human row written with a stale (non-1.0) weight is reset to full weight on recompute.
    proj, keys = _one_condition_store(tmp_path / "p.tether", n=2)
    set_curation_label(
        proj.path, keys[0], CurationLabel.ACCEPT, source=LABEL_SOURCE_HUMAN, weight=0.5
    )
    recompute_label_weights(proj)
    rows = read_labels(proj.path)
    assert float(rows["weight"][0]) == pytest.approx(1.0)


def test_recompute_honours_a_custom_w0(tmp_path) -> None:
    proj, keys = _one_condition_store(tmp_path / "p.tether", n=4)
    set_curation_label(proj.path, keys[0], CurationLabel.ACCEPT, source=LABEL_SOURCE_DEEPLASI)
    accept(proj.path, keys[1])
    reject(proj.path, keys[2])  # n_human = 2
    recompute_label_weights(proj, w0=0.6)
    assert _seed_weight_of(proj.path) == pytest.approx(0.6 / 3.0)


def test_recompute_is_idempotent(tmp_path) -> None:
    proj, keys = _one_condition_store(tmp_path / "p.tether", n=4)
    set_curation_label(proj.path, keys[0], CurationLabel.ACCEPT, source=LABEL_SOURCE_DEEPLASI)
    accept(proj.path, keys[1])
    reject(proj.path, keys[2])
    recompute_label_weights(proj)
    first = read_labels(proj.path)["weight"].copy()
    recompute_label_weights(proj)
    second = read_labels(proj.path)["weight"]
    np.testing.assert_array_equal(first, second)


def test_recompute_preserves_every_other_labels_field(tmp_path) -> None:
    # The read-all/modify-only-weight/write-all round-trip must leave every OTHER /labels field
    # byte-for-byte unchanged — the M0 schema freeze + never-fabricate invariant. A regression that
    # rebuilt the row array or corrupted a vlen-string/int32 column would blank provenance while the
    # weight-only tests stayed green; this locks it. Includes a near-zero decayed weight (n_human=6)
    # so the kept-row invariant is checked simultaneously.
    proj, keys = _one_condition_store(tmp_path / "p.tether", n=8)
    set_curation_label(proj.path, keys[0], CurationLabel.ACCEPT, source=LABEL_SOURCE_DEEPLASI)
    for i in range(1, 7):  # six human-labeled molecules -> seed decays to w₀/7
        (accept if i % 2 else reject)(proj.path, keys[i])

    before = read_labels(proj.path)
    frozen_fields = (
        "molecule_key",
        "labeler",
        "timestamp",
        "source_file",
        "source",
        "label_value",
        "condition_id",
    )
    snapshot = {name: before[name].copy() for name in frozen_fields}
    n_rows_before = before.shape[0]

    recompute_label_weights(proj)

    after = read_labels(proj.path)
    assert after.shape[0] == n_rows_before  # no row dropped or duplicated
    for name in frozen_fields:
        np.testing.assert_array_equal(snapshot[name], after[name])  # provenance intact
    # weight is the only field that changed, and the fully-decayed seed row is still present.
    assert _seed_weight_of(proj.path) == pytest.approx(DEFAULT_SEED_WEIGHT / 7.0)


def test_recompute_empty_labels_log_returns_zero(tmp_path) -> None:
    proj, _ = _one_condition_store(tmp_path / "p.tether", n=2)  # no labels written
    assert recompute_label_weights(proj) == 0


def test_recompute_rejects_bad_w0(tmp_path) -> None:
    proj, keys = _one_condition_store(tmp_path / "p.tether", n=2)
    set_curation_label(proj.path, keys[0], CurationLabel.ACCEPT, source=LABEL_SOURCE_DEEPLASI)
    with pytest.raises(ValueError, match="w0"):
        recompute_label_weights(proj, w0=0.0)


def test_recompute_missing_groups_raises(tmp_path) -> None:
    import h5py  # noqa: PLC0415

    bare = tmp_path / "bare.h5"
    with h5py.File(bare, "w") as f:
        f.create_group("nothing")
    with pytest.raises(KeyError, match="/labels or /molecules"):
        recompute_label_weights(bare)


def test_recompute_decays_each_condition_independently(tmp_path) -> None:
    # Two conditions, each with one deeplasi seed; condition B has more human labels, so its seed
    # is decayed further. Verifies the per-condition n_human grouping, not a global count.
    path = tmp_path / "two.tether"
    t = 12
    create_project(path, overwrite=True)
    write_extraction(
        path,
        movie=_movie("mov-A", "a", t),
        molecules=_molecules(3),
        traces=_traces(3, t),
        parsed=parse_filename("Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif"),
        registration_map=_reg_map(),
    )
    write_extraction(
        path,
        movie=_movie("mov-B", "b", t),
        molecules=_molecules(5),
        traces=_traces(5, t),
        # Differs in the *ligand* concentration (300 vs 600 nM), a condition-key field — the
        # sample concentration (35pM) is provenance, not part of condition_id (tether.io.filename).
        parsed=parse_filename("Bla_UCKOPSB_T-box_35pM_tRNA_300nM_011.tif"),
        registration_map=_reg_map(),
    )
    proj = Project.open(path)
    keys = _keys(path)
    mols = read_molecules(path)
    conditions = np.array(
        [c.decode() if isinstance(c, bytes) else str(c) for c in mols["condition_id"]]
    )
    cond_a, cond_b = conditions[0], conditions[3]
    assert cond_a != cond_b  # two distinct conditions

    a_idx = np.flatnonzero(conditions == cond_a)
    b_idx = np.flatnonzero(conditions == cond_b)
    # Seed the first molecule of each condition; give A one human label, B three.
    dl = LABEL_SOURCE_DEEPLASI
    set_curation_label(proj.path, keys[a_idx[0]], CurationLabel.ACCEPT, source=dl)
    set_curation_label(proj.path, keys[b_idx[0]], CurationLabel.ACCEPT, source=dl)
    accept(proj.path, keys[a_idx[1]])  # condition A: n_human = 1
    accept(proj.path, keys[b_idx[1]])
    reject(proj.path, keys[b_idx[2]])
    accept(proj.path, keys[b_idx[3]])  # condition B: n_human = 3

    recompute_label_weights(proj)

    rows = read_labels(proj.path)
    src = np.array([s.decode() if isinstance(s, bytes) else str(s) for s in rows["source"]])
    rcond = np.array([c.decode() if isinstance(c, bytes) else str(c) for c in rows["condition_id"]])
    seed_a = float(rows["weight"][(src == LABEL_SOURCE_DEEPLASI) & (rcond == cond_a)][0])
    seed_b = float(rows["weight"][(src == LABEL_SOURCE_DEEPLASI) & (rcond == cond_b)][0])
    assert seed_a == pytest.approx(DEFAULT_SEED_WEIGHT / 2.0)  # w₀/(1+1)
    assert seed_b == pytest.approx(DEFAULT_SEED_WEIGHT / 4.0)  # w₀/(1+3)
    assert seed_b < seed_a  # more human evidence -> more decayed

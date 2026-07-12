# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure (torch-free) deep-dataset substrate tests (PRD §9 M8; FR-ML; ADR-0047)."""

from __future__ import annotations

import numpy as np
import pytest

from tether.ml.deep.dataset import (
    DEFAULT_DEEP_CHANNELS,
    DEFAULT_NORMALIZATION,
    DEFAULT_SPLIT_SEED,
    DEFAULT_VAL_FRACTION,
    DEFAULT_WINDOW_LENGTH,
    DeepTraceDataset,
    assemble_dataset,
    normalize_pair,
    train_val_split,
)

pytest.importorskip("numpy")


# --- normalize_pair -------------------------------------------------------------------------


def test_per_trace_total_preserves_fret_ratio():
    donor = np.array([10.0, 6.0, 0.0])
    acceptor = np.array([0.0, 4.0, 10.0])
    donor_n, acceptor_n = normalize_pair(donor, acceptor, "per_trace_total")
    # One shared scale = max total intensity (10 + 0, 6 + 4, 0 + 10) = 10.
    assert np.allclose(donor_n, donor / 10.0)
    assert np.allclose(acceptor_n, acceptor / 10.0)
    # Apparent FRET E = A / (D + A) is invariant under a shared per-trace scale (the load-bearing
    # property — an independent per-channel scale would destroy this ratio).
    raw_e = acceptor / (donor + acceptor)
    norm_e = acceptor_n / (donor_n + acceptor_n)
    assert np.allclose(raw_e, norm_e)


def test_normalization_none_is_identity():
    donor = np.array([3.0, 7.0])
    acceptor = np.array([1.0, 2.0])
    donor_n, acceptor_n = normalize_pair(donor, acceptor, "none")
    assert np.array_equal(donor_n, donor)
    assert np.array_equal(acceptor_n, acceptor)
    # A copy, not the same object (never mutate the caller's array).
    assert donor_n is not donor


def test_normalize_degenerate_all_zero_trace_never_divides_by_zero():
    donor = np.zeros(4)
    acceptor = np.zeros(4)
    donor_n, acceptor_n = normalize_pair(donor, acceptor, "per_trace_total")
    assert np.all(np.isfinite(donor_n)) and np.all(donor_n == 0.0)
    assert np.all(np.isfinite(acceptor_n)) and np.all(acceptor_n == 0.0)


def test_normalize_empty_trace_returns_empty():
    donor_n, acceptor_n = normalize_pair(np.array([]), np.array([]), "per_trace_total")
    assert donor_n.shape == (0,)
    assert acceptor_n.shape == (0,)


def test_normalize_non_finite_total_falls_back_to_unit_scale():
    donor = np.array([np.nan, 2.0])
    acceptor = np.array([np.inf, 1.0])
    donor_n, acceptor_n = normalize_pair(donor, acceptor, "per_trace_total")
    # Finite total is only frame 1 (= 3.0) -> scale 3.0; the non-finite frame is passed through.
    assert acceptor_n[1] == pytest.approx(1.0 / 3.0)


def test_normalize_pair_rejects_unknown_method():
    with pytest.raises(ValueError, match="unknown normalization"):
        normalize_pair(np.array([1.0]), np.array([1.0]), "zscore")


# --- assemble_dataset -----------------------------------------------------------------------


def _simple_lists():
    ids = ["m0", "m1", "m2"]
    donors = [np.array([10.0, 8.0, 6.0]), np.array([5.0, 5.0]), np.array([1.0, 2.0, 3.0, 4.0])]
    acceptors = [np.array([0.0, 2.0, 4.0]), np.array([5.0, 5.0]), np.array([4.0, 3.0, 2.0, 1.0])]
    y = np.array([True, False, True])
    weights = np.array([1.0, 1.0, 0.3])
    return ids, donors, acceptors, y, weights


def test_assemble_shapes_dtypes_and_labels():
    ids, donors, acceptors, y, weights = _simple_lists()
    ds = assemble_dataset(ids, donors, acceptors, y, weights, window_length=5)
    assert isinstance(ds, DeepTraceDataset)
    assert ds.X.shape == (3, 2, 5)
    assert ds.X.dtype == np.float32
    assert ds.mask.shape == (3, 5)
    assert ds.mask.dtype == np.bool_
    assert ds.lengths.tolist() == [3, 2, 4]
    assert ds.y.dtype == np.int8
    assert ds.y.tolist() == [1, 0, 1]  # accept -> 1, reject -> 0
    assert np.array_equal(ds.sample_weight, weights)
    assert ds.channels == DEFAULT_DEEP_CHANNELS
    assert ds.n_samples == 3
    assert ds.n_good == 2
    assert ds.n_bad == 1
    assert ds.window_length == 5
    assert ds.normalization == DEFAULT_NORMALIZATION


def test_assemble_mask_marks_only_real_frames():
    ids, donors, acceptors, y, weights = _simple_lists()
    ds = assemble_dataset(ids, donors, acceptors, y, weights, window_length=5)
    assert ds.mask[0].tolist() == [True, True, True, False, False]
    assert ds.mask[1].tolist() == [True, True, False, False, False]
    # Padded frames are exactly zero on every channel (masked placeholder).
    assert np.all(ds.X[1, :, 2:] == 0.0)


def test_assemble_crops_long_trace_to_leading_window():
    donor = [np.arange(7, dtype=float)]
    acceptor = [np.arange(7, dtype=float)[::-1].copy()]
    ds = assemble_dataset(["m"], donor, acceptor, np.array([1]), np.array([1.0]), window_length=3)
    assert ds.lengths.tolist() == [3]
    assert np.all(ds.mask[0])  # every window frame is real (trace longer than the window)


def test_assemble_channel_order_and_subset_under_no_normalization():
    ids, donors, acceptors, y, weights = _simple_lists()
    ds = assemble_dataset(ids, donors, acceptors, y, weights, window_length=4, normalization="none")
    # Row 0 = donor channel, row 1 = acceptor channel.
    assert ds.X[0, 0, :3].tolist() == [10.0, 8.0, 6.0]
    assert ds.X[0, 1, :3].tolist() == [0.0, 2.0, 4.0]
    donor_only = assemble_dataset(
        ids,
        donors,
        acceptors,
        y,
        weights,
        window_length=4,
        channels=("donor",),
        normalization="none",
    )
    assert donor_only.channels == ("donor",)
    assert donor_only.X.shape == (3, 1, 4)
    assert donor_only.X[0, 0, :3].tolist() == [10.0, 8.0, 6.0]


def test_single_channel_per_trace_total_uses_shared_donor_plus_acceptor_scale():
    # A donor-only dataset still normalizes by the shared donor+acceptor total scale (not the
    # donor's own max), so the apparent-FRET relationship survives — pin that contract.
    donor = [np.array([6.0, 4.0])]
    acceptor = [np.array([0.0, 10.0])]  # total = [6, 14] -> shared scale 14, not donor-max 6
    ds = assemble_dataset(
        ["m"],
        donor,
        acceptor,
        np.array([1]),
        np.array([1.0]),
        window_length=2,
        channels=("donor",),  # default per_trace_total
    )
    assert ds.channels == ("donor",)
    assert np.allclose(ds.X[0, 0].tolist(), [6.0 / 14.0, 4.0 / 14.0])  # NOT divided by 6


def test_assemble_maps_signed_curation_labels():
    # +1 accept -> 1, -1 reject -> 0 (CurationLabel codes).
    ds = assemble_dataset(
        ["a", "b"],
        [np.array([1.0])] * 2,
        [np.array([1.0])] * 2,
        np.array([1, -1]),
        np.array([1.0, 1.0]),
        window_length=2,
    )
    assert ds.y.tolist() == [1, 0]


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"channels": ()}, "non-empty"),
        ({"channels": ("fret",)}, "unsupported channel"),
        ({"channels": ("donor", "donor")}, "duplicate channel"),
        ({"normalization": "zscore"}, "unknown normalization"),
        ({"window_length": 0}, "window_length must be positive"),
    ],
)
def test_assemble_validation_errors(kwargs, match):
    ids, donors, acceptors, y, weights = _simple_lists()
    with pytest.raises(ValueError, match=match):
        assemble_dataset(ids, donors, acceptors, y, weights, **kwargs)


def test_assemble_rejects_misaligned_inputs():
    with pytest.raises(ValueError, match="same length"):
        assemble_dataset(
            ["a", "b"], [np.array([1.0])], [np.array([1.0])], np.array([1]), np.array([1.0])
        )


def test_assemble_rejects_empty_set():
    with pytest.raises(ValueError, match="zero molecules"):
        assemble_dataset([], [], [], np.array([]), np.array([]))


def test_assemble_rejects_per_molecule_channel_length_mismatch():
    with pytest.raises(ValueError, match="donor length"):
        assemble_dataset(
            ["m"], [np.array([1.0, 2.0])], [np.array([1.0])], np.array([1]), np.array([1.0])
        )


# --- train_val_split ------------------------------------------------------------------------


def test_split_is_reproducible_for_a_given_seed():
    y = np.array([1, 0, 1, 0, 1, 0, 1, 0])
    a_train, a_val = train_val_split(y, val_fraction=0.25, seed=7)
    b_train, b_val = train_val_split(y, val_fraction=0.25, seed=7)
    assert np.array_equal(a_train, b_train)
    assert np.array_equal(a_val, b_val)


def test_split_partitions_all_rows_disjointly():
    y = np.array([1, 1, 0, 0, 1, 0, 1, 0, 1, 0])
    train, val = train_val_split(y, val_fraction=0.3, seed=0)
    assert set(train.tolist()) | set(val.tolist()) == set(range(y.shape[0]))
    assert set(train.tolist()).isdisjoint(val.tolist())
    assert np.array_equal(val, np.sort(val))


def test_split_is_stratified_by_label():
    y = np.array([1] * 8 + [0] * 4)
    _train, val = train_val_split(y, val_fraction=0.25, seed=3, stratify=True)
    val_labels = y[val]
    assert int(np.count_nonzero(val_labels == 1)) == 2  # round(8 * 0.25)
    assert int(np.count_nonzero(val_labels == 0)) == 1  # round(4 * 0.25)


def test_split_keeps_a_singleton_class_in_train():
    y = np.array([1, 1, 1, 0])  # one reject
    train, val = train_val_split(y, val_fraction=0.25, seed=0, stratify=True)
    reject_idx = 3
    assert reject_idx in train.tolist()
    assert reject_idx not in val.tolist()


def test_split_one_class_dataset_still_holds_out_one():
    y = np.array([1, 1, 1, 1, 1])
    train, val = train_val_split(y, val_fraction=0.2, seed=0)
    assert val.shape[0] == 1
    assert train.shape[0] == 4


def test_split_rejects_bad_fraction_and_empty():
    with pytest.raises(ValueError, match=r"val_fraction must be in \(0, 1\)"):
        train_val_split(np.array([1, 0]), val_fraction=1.0)
    with pytest.raises(ValueError, match="empty label array"):
        train_val_split(np.array([]))


def test_dataset_split_convenience_matches_function():
    ids = [f"m{i}" for i in range(6)]
    donors = [np.array([1.0, 2.0])] * 6
    acceptors = [np.array([2.0, 1.0])] * 6
    y = np.array([1, 0, 1, 0, 1, 0])
    ds = assemble_dataset(ids, donors, acceptors, y, np.ones(6), window_length=2)
    a = ds.split(seed=1)
    b = train_val_split(ds.y, seed=1)
    assert np.array_equal(a[0], b[0])
    assert np.array_equal(a[1], b[1])


def test_defaults_match_prd_11_2():
    # Pin the documented PRD §11.2 "Deep-dataset preprocessing" defaults to their literals so the
    # code constants and the §11.2 single-source-of-truth row can never silently drift apart (a
    # PR-1b retune must touch both) — mirrors test_analysis_transition_prob's default-pinning test.
    assert DEFAULT_WINDOW_LENGTH == 500
    assert DEFAULT_NORMALIZATION == "per_trace_total"
    assert DEFAULT_DEEP_CHANNELS == ("donor", "acceptor")
    assert DEFAULT_VAL_FRACTION == 0.2
    assert DEFAULT_SPLIT_SEED == 0

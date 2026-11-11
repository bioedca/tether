# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Store-integrated deep-dataset builder tests (PRD §9 M8; FR-ML; ADR-0047)."""

from __future__ import annotations

import h5py
import numpy as np
import pytest

pytest.importorskip("numpy")
pytest.importorskip("h5py")
pytest.importorskip("scipy")
pytest.importorskip("skimage")
pytest.importorskip("tifffile")

from _analysis_store import build_store_with_channels  # noqa: E402
from tether.imaging.extract import read_molecules  # noqa: E402
from tether.ml.deep.dataset import DeepTraceDataset  # noqa: E402
from tether.project.deep_dataset import build_deep_dataset  # noqa: E402
from tether.project.features import compute_features  # noqa: E402
from tether.project.gbranking import weighted_training_set  # noqa: E402
from tether.project.labels import accept, reject  # noqa: E402


def _channels(n: int = 6, t: int = 20, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Anticorrelated donor/acceptor intensity traces with a per-molecule FRET level."""
    rng = np.random.default_rng(seed)
    donor = np.zeros((n, t))
    acceptor = np.zeros((n, t))
    for i in range(n):
        e = 0.2 + 0.1 * i
        donor[i] = 1000.0 * (1.0 - e) + rng.normal(0.0, 20.0, t)
        acceptor[i] = 1000.0 * e + rng.normal(0.0, 20.0, t)
    return donor, acceptor


def _labeled_store(tmp_path, *, name="deep.tether", windows=None):
    """A 6-molecule store: mols 0,2 accepted; 1,3 rejected; 4,5 uncurated. Features computed."""
    donor, acceptor = _channels()
    proj, keys = build_store_with_channels(tmp_path, donor, acceptor, windows=windows, name=name)
    accept(proj.path, keys[0])
    reject(proj.path, keys[1])
    accept(proj.path, keys[2])
    reject(proj.path, keys[3])
    compute_features(proj)
    return proj, keys


def test_build_matches_the_ranker_labeled_set_exactly(tmp_path):
    proj, _keys = _labeled_store(tmp_path)
    wts = weighted_training_set(proj)
    ds = build_deep_dataset(proj)
    assert isinstance(ds, DeepTraceDataset)
    # The deep dataset's labeled set / labels / weights are the ranker's, molecule-for-molecule.
    assert ds.molecule_ids == wts.molecule_ids
    assert np.array_equal(ds.y, wts.y.astype(np.int8))
    assert np.array_equal(ds.sample_weight, wts.sample_weight)
    assert ds.n_samples == 4
    assert ds.n_good == 2  # two accepts
    assert ds.n_bad == 2  # two rejects (rejects are training labels, y=0)


def test_build_accepts_a_path_or_string_projectref(tmp_path):
    # The documented ProjectRef = Project | path | str: a path/string input must work identically.
    proj, _keys = _labeled_store(tmp_path)
    from_project = build_deep_dataset(proj)
    from_path = build_deep_dataset(proj.path)  # pathlib.Path input
    from_str = build_deep_dataset(str(proj.path))  # str input
    assert from_path.molecule_ids == from_project.molecule_ids
    assert from_str.molecule_ids == from_project.molecule_ids
    assert np.array_equal(from_path.X, from_project.X)
    assert np.array_equal(from_str.sample_weight, from_project.sample_weight)


def test_build_tensor_shapes_and_provenance(tmp_path):
    proj, _keys = _labeled_store(tmp_path)
    ds = build_deep_dataset(proj)
    assert ds.X.shape == (4, 2, ds.window_length)
    assert ds.X.dtype == np.float32
    assert ds.mask.shape == (4, ds.window_length)
    assert ds.channels == ("donor", "acceptor")
    assert ds.intensity_quantity == "corrected"
    assert ds.normalization == "per_trace_total"


def _store_rows(proj) -> dict[str, int]:
    """``molecule_id -> store row``, read from ``/molecules`` (the row the input traces sit at)."""
    ids = read_molecules(proj.path)["molecule_id"]
    return {(m.decode() if isinstance(m, bytes) else str(m)): i for i, m in enumerate(ids)}


def test_tensor_values_are_each_molecules_own_donor_then_acceptor_trace(tmp_path):
    # ML-F5: the tensor must hold, molecule for molecule, the donor trace in channel 0 and the
    # acceptor trace in channel 1 — compared with the very arrays written to the store, so a
    # swapped channel order (or a trace joined to the wrong molecule) cannot pass. mol 0 has a
    # narrowed analysis window, so the window slice is checked by value too.
    windows = [(2, 12), (0, 20), (0, 20), (0, 20), (0, 20), (0, 20)]
    proj, _keys = _labeled_store(tmp_path, windows=windows)
    donor, acceptor = _channels()  # the same seed _labeled_store wrote
    rows = _store_rows(proj)
    ds = build_deep_dataset(proj, normalization="none")
    assert ds.channels == ("donor", "acceptor")
    for i, mol_id in enumerate(ds.molecule_ids):
        row = rows[mol_id]
        start, stop = windows[row]
        n = int(ds.lengths[i])
        assert n == stop - start
        np.testing.assert_allclose(ds.X[i, 0, :n], donor[row, start:stop], rtol=1e-6)
        np.testing.assert_allclose(ds.X[i, 1, :n], acceptor[row, start:stop], rtol=1e-6)
        assert np.all(ds.X[i, :, n:] == 0.0)  # padded tail stays a zero placeholder


def test_labels_map_accept_to_one_reject_to_zero(tmp_path):
    proj, _keys = _labeled_store(tmp_path)
    ds = build_deep_dataset(proj)
    # Labeled set is store order [mol0 accept, mol1 reject, mol2 accept, mol3 reject].
    assert ds.y.tolist() == [1, 0, 1, 0]


def test_window_lengths_follow_the_analysis_window(tmp_path):
    # mol 0 windowed to [2, 12) (10 frames); the rest full [0, 20).
    windows = [(2, 12), (0, 20), (0, 20), (0, 20), (0, 20), (0, 20)]
    proj, _keys = _labeled_store(tmp_path, windows=windows)
    ds = build_deep_dataset(proj)  # default window_length >> 20, so no crop
    assert ds.lengths.tolist() == [10, 20, 20, 20]
    assert ds.mask[0].tolist()[:10] == [True] * 10
    assert not ds.mask[0][10:].any()


def test_window_length_crops_long_traces(tmp_path):
    proj, _keys = _labeled_store(tmp_path)
    ds = build_deep_dataset(proj, window_length=5)
    assert ds.X.shape == (4, 2, 5)
    assert ds.lengths.tolist() == [5, 5, 5, 5]  # native 20 frames cropped to the window
    assert ds.mask.all()
    # ML-F6: the crop keeps the LEADING frames (PRD §11.2 "crop leading"), compared by value.
    donor, acceptor = _channels()
    rows = _store_rows(proj)
    raw = build_deep_dataset(proj, window_length=5, normalization="none")
    for i, mol_id in enumerate(raw.molecule_ids):
        row = rows[mol_id]
        np.testing.assert_allclose(raw.X[i, 0], donor[row, :5], rtol=1e-6)
        np.testing.assert_allclose(raw.X[i, 1], acceptor[row, :5], rtol=1e-6)


def test_build_reads_the_raw_trace_layer(tmp_path):
    proj, _keys = _labeled_store(tmp_path)
    corrected = build_deep_dataset(proj)
    raw = build_deep_dataset(proj, intensity_quantity="raw")
    assert raw.intensity_quantity == "raw"
    assert raw.molecule_ids == corrected.molecule_ids  # same labeled set, different trace layer
    # raw = corrected + the per-frame background, so the tensors genuinely differ.
    assert not np.allclose(raw.X, corrected.X)


def test_requires_a_feature_table(tmp_path):
    donor, acceptor = _channels()
    proj, keys = build_store_with_channels(tmp_path, donor, acceptor, name="nofeat.tether")
    accept(proj.path, keys[0])
    reject(proj.path, keys[1])
    with pytest.raises(KeyError, match="features"):  # no compute_features run
        build_deep_dataset(proj)


def test_raises_when_no_molecule_is_labeled(tmp_path):
    donor, acceptor = _channels()
    proj, _keys = build_store_with_channels(tmp_path, donor, acceptor, name="nolabel.tether")
    compute_features(proj)
    with pytest.raises(ValueError, match="no labeled molecules"):
        build_deep_dataset(proj)


def test_raises_on_an_unknown_intensity_quantity(tmp_path):
    proj, _keys = _labeled_store(tmp_path)
    with pytest.raises(ValueError, match="intensity_quantity must be one of"):
        build_deep_dataset(proj, intensity_quantity="does-not-exist")


@pytest.mark.parametrize("layer", ["donor_raw", "acceptor_raw"])
def test_raises_on_an_absent_trace_layer(tmp_path, layer):
    # ML-F14: a valid quantity whose /traces layer is missing from the store (an older or damaged
    # file) is refused by name — the branch an unknown quantity never reaches.
    proj, _keys = _labeled_store(tmp_path)
    with h5py.File(proj.path, "r+") as f:
        del f["traces"][layer]
    with pytest.raises(ValueError, match=rf"has no '{layer}' layer"):
        build_deep_dataset(proj, intensity_quantity="raw")

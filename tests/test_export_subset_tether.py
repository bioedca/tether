# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Movie-less subset ``.tether`` export (FR-EXPORT, PRD §7.9/§5.3/§5.4).

Headless (no Qt): builds a source ``.tether`` via the shared ``_analysis_store``
builder (molecules + patches + all six trace layers + a persisted idealization
model), exports a subset to ``tmp_path``, and reads it back. Runs in the default
3-OS ``test`` matrix (gated on ``h5py``).

The load-bearing invariants under test:

* **movie-less** — the subset carries no ``/movies`` rows;
* **raw optional & non-reconstructable** — ``include_raw`` embeds the raw *and*
  background layers together (since ``corrected = raw − background`` exactly, keeping
  background alone would let raw be reconstructed), so omitting raw leaves *only*
  corrected traces (§5.4 "raw is not reconstructable there");
* **idealization travels fresh** — the copied per-molecule input-provenance hashes
  still match the copied traces/window/factors, so the subset's idealizations read
  back **live**, not silently stale;
* **provenance + additive-only** — sidecar + root attrs are stamped and the subset is
  a valid ``.tether`` built purely from additive data.
"""

from __future__ import annotations

import json
import os

import pytest

pytest.importorskip("numpy")
h5py = pytest.importorskip("h5py")

import numpy as np  # noqa: E402

from _analysis_store import MEANS, build_store_with_model, fresh_input_hashes, to_str  # noqa: E402
from tether.imaging.extract import read_molecules, read_patches, read_traces  # noqa: E402
from tether.io.schema import TABLE, assert_is_compatible_project  # noqa: E402
from tether.project import Project  # noqa: E402
from tether.project.export import ExportResult, export_subset_tether  # noqa: E402
from tether.project.idealize import write_idealization_model  # noqa: E402
from tether.project.labels import CurationLabel  # noqa: E402

_ALL_LAYERS = frozenset(
    {
        "donor_raw",
        "acceptor_raw",
        "donor_corrected",
        "acceptor_corrected",
        "donor_background",
        "acceptor_background",
    }
)
_CORRECTED_ONLY = frozenset({"donor_corrected", "acceptor_corrected"})


def _state_matrix() -> np.ndarray:
    """A 4-molecule × 12-frame Viterbi state matrix with transitions (3 states)."""
    return np.array(
        [
            [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],  # mol 0: one state
            [0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1],  # mol 1: one transition
            [1, 1, 1, 2, 2, 2, 2, 1, 1, 1, 0, 0],  # mol 2: several
            [2, 2, 1, 1, 0, 0, 0, 1, 1, 2, 2, 2],  # mol 3: several
        ],
        dtype="int64",
    )


def _source(tmp_path, **kwargs) -> tuple[Project, list[str]]:
    """A source ``.tether`` with 4 molecules + a persisted ``vbconhmm`` model."""
    return build_store_with_model(tmp_path, _state_matrix(), MEANS, **kwargs)


def _movies_rowcount(path) -> int:
    with h5py.File(path, "r") as f:
        return int(f["movies"][TABLE].shape[0])


def _root_attr(path, key):
    with h5py.File(path, "r") as f:
        val = f.attrs[key]
    return val.decode() if isinstance(val, bytes) else val


# --------------------------------------------------------------------------- #
# Validity / return contract
# --------------------------------------------------------------------------- #


def test_subset_is_a_valid_openable_tether(tmp_path):
    project, _ = _source(tmp_path)
    out = tmp_path / "subset.tether"

    result = export_subset_tether(project, out)

    assert isinstance(result, ExportResult)
    assert result.path == out
    assert result.n_molecules == 4
    assert out.exists()
    # A complete, compatible .tether that Project.open accepts.
    assert assert_is_compatible_project(out) == Project.open(out).schema_version


def test_subset_carries_the_selected_molecules_in_store_order(tmp_path):
    project, keys = _source(tmp_path)
    out = tmp_path / "subset.tether"

    export_subset_tether(project, out)

    sub_keys = [to_str(k) for k in read_molecules(out)["molecule_key"]]
    assert sub_keys == keys  # all four, same order


# --------------------------------------------------------------------------- #
# Movie-less (§5.4)
# --------------------------------------------------------------------------- #


def test_source_has_a_movie_row_but_subset_is_movie_less(tmp_path):
    project, _ = _source(tmp_path)
    out = tmp_path / "subset.tether"

    assert _movies_rowcount(project.path) == 1  # the source carries its movie provenance

    export_subset_tether(project, out)

    assert _movies_rowcount(out) == 0  # the subset is definitionally movie-less


# --------------------------------------------------------------------------- #
# Raw optional & non-reconstructable (§5.4 / §7.9 "raw optional")
# --------------------------------------------------------------------------- #


def test_source_store_has_all_six_trace_layers(tmp_path):
    project, _ = _source(tmp_path)
    assert set(read_traces(project.path)) == set(_ALL_LAYERS)


def test_include_raw_true_embeds_all_six_layers(tmp_path):
    project, _ = _source(tmp_path)
    out = tmp_path / "with_raw.tether"

    export_subset_tether(project, out, include_raw=True)

    assert set(read_traces(out)) == set(_ALL_LAYERS)


def test_include_raw_false_embeds_only_corrected_and_drops_background(tmp_path):
    """The §5.4 invariant: without raw, neither raw nor background survives, so raw
    is not reconstructable (``raw = corrected + background`` would otherwise recover it)."""
    project, _ = _source(tmp_path)
    out = tmp_path / "no_raw.tether"

    export_subset_tether(project, out, include_raw=False)

    layers = set(read_traces(out))
    assert layers == set(_CORRECTED_ONLY)
    # explicitly: neither the raw layer nor the background that reconstructs it is present
    assert "donor_raw" not in layers
    assert "acceptor_raw" not in layers
    assert "donor_background" not in layers
    assert "acceptor_background" not in layers


def test_default_omits_raw(tmp_path):
    project, _ = _source(tmp_path)
    out = tmp_path / "default.tether"

    export_subset_tether(project, out)

    assert set(read_traces(out)) == set(_CORRECTED_ONLY)
    assert _root_attr(out, "tether_subset_include_raw") == 0


def test_corrected_traces_are_copied_value_exact(tmp_path):
    project, _ = _source(tmp_path)
    out = tmp_path / "subset.tether"

    export_subset_tether(project, out, include_raw=True)

    src = read_traces(project.path)
    sub = read_traces(out)
    for layer in _ALL_LAYERS:
        np.testing.assert_array_equal(sub[layer], src[layer])


# --------------------------------------------------------------------------- #
# Coordinates + patches (movie-less curation substrate)
# --------------------------------------------------------------------------- #


def test_coordinates_and_patches_travel(tmp_path):
    project, _ = _source(tmp_path)
    out = tmp_path / "subset.tether"

    export_subset_tether(project, out)

    src_mol = read_molecules(project.path)
    sub_mol = read_molecules(out)
    np.testing.assert_array_equal(sub_mol["donor_xy"], src_mol["donor_xy"])
    np.testing.assert_array_equal(sub_mol["acceptor_xy"], src_mol["acceptor_xy"])

    src_patch = read_patches(project.path)
    sub_patch = read_patches(out)
    assert set(sub_patch) == set(src_patch)
    for channel in src_patch:
        np.testing.assert_array_equal(sub_patch[channel], src_patch[channel])


# --------------------------------------------------------------------------- #
# Idealization: copied, filtered to the subset, and still FRESH
# --------------------------------------------------------------------------- #


def test_idealization_model_copied_with_global_arrays(tmp_path):
    project, keys = _source(tmp_path)
    out = tmp_path / "subset.tether"

    export_subset_tether(project, out)

    sub = Project.open(out)
    assert sub.list_idealizations() == ["vbconhmm"]
    stored = sub.read_idealization("vbconhmm")
    assert stored.molecule_keys == keys  # all four molecules' rows
    np.testing.assert_array_equal(stored.means, MEANS)  # global levels preserved
    np.testing.assert_array_equal(stored.state_paths, _state_matrix())


def test_subset_idealizations_read_back_live_not_stale(tmp_path):
    """The copied input-provenance hashes still match the copied traces/window/factors,
    so the subset's idealizations are LIVE (TDP/dwell keep them), never silently stale.

    Uses a **non-trivial analysis_window** ``(2, 11) != frame_range (0, 12)`` so the
    freshness recompute actually exercises the *window* provenance: a subset that lost
    or zeroed ``analysis_window`` would fall back to ``frame_range``, hash over a wider
    slice, and surface as stale — this assertion would then fail."""
    n_frames = _state_matrix().shape[1]
    windows = [(2, n_frames - 1)] * 4
    project, keys = build_store_with_model(tmp_path, _state_matrix(), MEANS, windows=windows)
    out = tmp_path / "subset.tether"

    export_subset_tether(project, out)

    sub = Project.open(out)
    assert sub.stale_idealization_keys("vbconhmm") == []
    assert set(sub.live_idealization_keys("vbconhmm")) == set(keys)


def test_idealization_rows_filtered_to_the_exported_molecules(tmp_path):
    project, keys = _source(tmp_path)
    out = tmp_path / "subset.tether"

    # export only molecules 0 and 2
    picked = [keys[0], keys[2]]
    export_subset_tether(project, out, molecule_keys=picked)

    sub = Project.open(out)
    stored = sub.read_idealization("vbconhmm")
    assert stored.molecule_keys == picked
    assert stored.state_paths.shape[0] == 2
    # pin the per-row VALUES to the picked source rows (0 and 2), not just the count —
    # catches a per-member misalignment that would leave molecule_keys right but the
    # state paths wrong. The fixture's four state rows are distinct, so this discriminates.
    np.testing.assert_array_equal(stored.state_paths[0], _state_matrix()[0])
    np.testing.assert_array_equal(stored.state_paths[1], _state_matrix()[2])
    with h5py.File(out, "r") as f:
        assert int(f["idealization"]["vbconhmm"].attrs["n_molecules"]) == 2
    # and its molecules are LIVE
    assert set(sub.live_idealization_keys("vbconhmm")) == set(picked)


def test_duplicate_molecule_key_idealization_filters_by_molecule_id(tmp_path):
    """§7.10 duplicate-molecule_key regression: two rows share a key but have distinct
    molecule_ids; when the curation filter splits them (one REJECT, dropped by default),
    the un-exported namesake's idealization row must NOT leak into the subset, and the
    exported molecule's idealization must read back LIVE.

    Filtering by the non-unique molecule_key (the earlier bug) would copy the dropped
    row's idealization as an orphan whose molecule_id has no /molecules row — which the
    staleness recompute reads as stale, silently dropping the exported (live) molecule's
    idealization from TDP/dwell. Filtering by molecule_id (the fix) avoids this."""
    project, _ = _source(tmp_path)  # 4 molecules, one vbconhmm model over all
    with h5py.File(project.path, "r+") as f:
        mol = f["molecules"][TABLE][:]
        shared_key = mol["molecule_key"][0]
        id0 = to_str(mol["molecule_id"][0])
        id1 = to_str(mol["molecule_id"][1])
        # collide row 1's key onto row 0's (coherent: same quantized donor_xy) + REJECT it
        mol["molecule_key"][1] = shared_key
        mol["donor_xy"][1] = mol["donor_xy"][0]
        mol["curation_label"][1] = int(CurationLabel.REJECT)
        f["molecules"][TABLE][:] = mol
        model = f["idealization"]["vbconhmm"]
        model_keys = model["molecule_key"][:]
        model_keys[1] = shared_key  # the model's row 1 now carries the shared key too
        model["molecule_key"][:] = model_keys

    out = tmp_path / "subset.tether"
    export_subset_tether(project, out)  # default include_rejected=False drops row 1

    sub = Project.open(out)
    stored = sub.read_idealization("vbconhmm")
    assert id1 not in stored.molecule_ids  # the dropped REJECT namesake does NOT leak in
    assert stored.state_paths.shape[0] == 3  # molecules 0, 2, 3 (row 1 dropped)
    assert id0 in stored.molecule_ids
    # the exported molecule reads LIVE — not silently staled by an orphan row
    assert sub.stale_idealization_keys("vbconhmm") == []
    assert to_str(shared_key) in set(sub.live_idealization_keys("vbconhmm"))


def _add_partial_model(project, keys, rows, model_name="partial", intensity_quantity="corrected"):
    """Write a second /idealization model that fit only the given molecule ``rows``."""
    n_frames = read_traces(project.path)["donor_corrected"].shape[1]
    hashes = fresh_input_hashes(project.path)
    ids = [to_str(x) for x in read_molecules(project.path)["molecule_id"]]
    write_idealization_model(
        project.path,
        model_name=model_name,
        model_type="vbconhmm",
        nstates=int(MEANS.size),
        dtype="FRET",
        means=MEANS,
        variances=np.full(MEANS.size, 0.01),
        tmatrix=None,
        norm_tmatrix=None,
        elbo=1.0,
        idealized=np.zeros((len(rows), n_frames)),
        state_paths=np.zeros((len(rows), n_frames), dtype="int64"),
        molecule_keys=[keys[r] for r in rows],
        molecule_ids=[ids[r] for r in rows],
        input_hashes=[hashes[r] for r in rows],
        intensity_quantity=intensity_quantity,
        selected_by="fixed",
        elbo_by_nstates=None,
        app_version="test",
        created_utc="2026-01-01T00:00:00Z",
        overwrite=True,
        frac=np.full(MEANS.size, 1.0 / MEANS.size),
    )


def test_model_with_no_selected_molecule_is_skipped(tmp_path):
    """A model none of whose molecules are exported is dropped entirely (not written
    as an empty, meaningless model); a model that *does* cover the subset survives."""
    project, keys = _source(tmp_path)
    _add_partial_model(project, keys, rows=[2, 3])  # "partial" fit only molecules 2 and 3
    out = tmp_path / "subset.tether"

    export_subset_tether(project, out, molecule_keys=[keys[0]])  # export molecule 0 only

    sub = Project.open(out)
    # vbconhmm covered molecule 0 -> survives (filtered to that one row); partial did
    # not cover molecule 0 -> skipped, absent from the subset.
    assert sub.list_idealizations() == ["vbconhmm"]
    assert sub.read_idealization("vbconhmm").molecule_keys == [keys[0]]


def test_second_model_survives_filtered_when_it_covers_the_subset(tmp_path):
    project, keys = _source(tmp_path)
    _add_partial_model(project, keys, rows=[2, 3])
    out = tmp_path / "subset.tether"

    export_subset_tether(project, out, molecule_keys=[keys[2]])  # covered by both models

    sub = Project.open(out)
    assert set(sub.list_idealizations()) == {"vbconhmm", "partial"}
    assert sub.read_idealization("partial").molecule_keys == [keys[2]]
    assert sub.read_idealization("vbconhmm").molecule_keys == [keys[2]]


def test_raw_non_reconstructability_propagates_through_a_subset_of_a_subset(tmp_path):
    """Once raw is dropped it cannot be resurrected: re-exporting a raw-less subset with
    include_raw=False stays corrected-only, and include_raw=True fails loudly (the source
    has no raw to embed) rather than silently producing a raw-less 'with raw' subset —
    so the §5.4 invariant is not silently escapable."""
    project, keys = _source(tmp_path)
    first = tmp_path / "first.tether"
    export_subset_tether(project, first, include_raw=False)
    assert set(read_traces(first)) == set(_CORRECTED_ONLY)

    # a further raw-less re-export stays corrected-only
    second = tmp_path / "second.tether"
    export_subset_tether(first, second, include_raw=False, molecule_keys=[keys[0]])
    assert set(read_traces(second)) == set(_CORRECTED_ONLY)

    # asking for raw from a raw-less source is rejected, not silently downgraded
    with pytest.raises(ValueError, match="source /traces is missing"):
        export_subset_tether(first, tmp_path / "third.tether", include_raw=True)


# --------------------------------------------------------------------------- #
# Selection: molecule_keys + curation filter
# --------------------------------------------------------------------------- #


def test_molecule_keys_subselect(tmp_path):
    project, keys = _source(tmp_path)
    out = tmp_path / "subset.tether"

    result = export_subset_tether(project, out, molecule_keys=[keys[1], keys[3]])

    assert result.n_molecules == 2
    sub_keys = [to_str(k) for k in read_molecules(out)["molecule_key"]]
    assert sub_keys == [keys[1], keys[3]]
    # trace rows track the molecule rows positionally
    assert read_traces(out)["donor_corrected"].shape[0] == 2
    assert read_patches(out)["donor"].shape[0] == 2


def test_rejected_dropped_by_default_kept_with_include_rejected(tmp_path):
    project, keys = _source(tmp_path, rejected=[False, True, False, False])
    default_out = tmp_path / "curated.tether"
    all_out = tmp_path / "all.tether"

    default_result = export_subset_tether(project, default_out)
    all_result = export_subset_tether(project, all_out, include_rejected=True)

    assert default_result.n_molecules == 3  # the REJECT molecule dropped
    assert all_result.n_molecules == 4
    default_keys = [to_str(k) for k in read_molecules(default_out)["molecule_key"]]
    assert keys[1] not in default_keys


def test_empty_selection_raises(tmp_path):
    project, _ = _source(tmp_path)
    with pytest.raises(ValueError, match="no molecules selected"):
        export_subset_tether(project, tmp_path / "e.tether", molecule_keys=[])


def test_all_rejected_selection_raises(tmp_path):
    project, keys = _source(tmp_path, rejected=[True, True, True, True])
    with pytest.raises(ValueError, match="no molecules selected"):
        export_subset_tether(project, tmp_path / "e.tether")


def test_unknown_molecule_key_raises_keyerror(tmp_path):
    project, _ = _source(tmp_path)
    with pytest.raises(KeyError, match="no molecule with molecule_key"):
        export_subset_tether(project, tmp_path / "e.tether", molecule_keys=["nope"])


# --------------------------------------------------------------------------- #
# Labels + conditions travel (provenance + referential integrity)
# --------------------------------------------------------------------------- #


def test_labels_travel_only_for_exported_molecules(tmp_path):
    project, keys = _source(tmp_path)
    project.accept(keys[0], labeler="tester")
    project.accept(keys[2], labeler="tester")
    out = tmp_path / "subset.tether"

    export_subset_tether(project, out, molecule_keys=[keys[0]])

    sub = Project.open(out)
    label_keys = {to_str(k) for k in sub.read_labels()["molecule_key"]}
    assert keys[0] in label_keys  # exported molecule's label row travels
    assert keys[2] not in label_keys  # a non-exported molecule's label is not carried


def test_conditions_copied_verbatim(tmp_path):
    project, _ = _source(tmp_path)
    summary = project.sync_conditions()
    assert summary.created_ids  # the fixture's filename parsed to a provisional condition
    out = tmp_path / "subset.tether"

    export_subset_tether(project, out)

    sub = Project.open(out)
    src_conditions = {to_str(c) for c in project.read_conditions()["condition_id"]}
    sub_conditions = {to_str(c) for c in sub.read_conditions()["condition_id"]}
    assert sub_conditions == src_conditions
    # referential integrity holds: every subset molecule resolves to a condition row
    assert sub.validate_conditions().ok


def test_settings_extraction_provenance_travels(tmp_path):
    project, _ = _source(tmp_path)
    out = tmp_path / "subset.tether"

    export_subset_tether(project, out)

    with h5py.File(out, "r") as f:
        assert "extraction" in f["settings"]
        assert "window" in f["settings"]["extraction"].attrs


def test_calibration_and_models_groups_travel_verbatim(tmp_path):
    """The other two verbatim metadata groups (/calibration, /models) survive the copy.

    The fixture leaves them empty, so seed a sentinel dataset in each before export and
    assert it lands in the subset — this pins that both are in the verbatim-copy set (a
    dropped tuple entry would leave the subset's group empty)."""
    project, _ = _source(tmp_path)
    with h5py.File(project.path, "r+") as f:
        f["calibration"].create_dataset("sentinel", data=np.array([1, 2, 3]))
        f["models"].create_dataset("sentinel", data=np.array([4, 5, 6]))
    out = tmp_path / "subset.tether"

    export_subset_tether(project, out)

    with h5py.File(out, "r") as f:
        np.testing.assert_array_equal(f["calibration"]["sentinel"][()], [1, 2, 3])
        np.testing.assert_array_equal(f["models"]["sentinel"][()], [4, 5, 6])


# --------------------------------------------------------------------------- #
# Provenance stamp (§7.9 / NFR-REPRO)
# --------------------------------------------------------------------------- #


def test_provenance_sidecar_and_root_attrs(tmp_path):
    project, _ = _source(tmp_path)
    out = tmp_path / "subset.tether"

    result = export_subset_tether(project, out, include_raw=True)

    # sidecar
    assert result.provenance_path == out.with_name(out.name + ".provenance.json")
    payload = json.loads(result.provenance_path.read_text(encoding="utf-8"))
    assert payload["tether_export"] == "subset-tether"
    assert payload["source_project"] == project.path.name
    assert payload["parameters"]["include_raw"] is True
    assert payload["parameters"]["n_molecules"] == 4
    assert payload["parameters"]["n_idealization_models"] == 1
    assert "created_utc" in payload
    assert "app_version" in payload

    # embedded root-attr provenance
    assert _root_attr(out, "tether_subset_of") == project.path.name
    assert _root_attr(out, "tether_subset_include_raw") == 1
    assert _root_attr(out, "tether_subset_n_molecules") == 4
    assert _root_attr(out, "tether_subset_created_utc") == payload["created_utc"]


# --------------------------------------------------------------------------- #
# Raw-fit idealization models when raw is omitted (the live/stale contract)
# --------------------------------------------------------------------------- #


def test_raw_fit_model_skipped_when_raw_omitted(tmp_path):
    """A model fit over the raw traces can't be re-staled once raw is dropped, so it is
    skipped (and recorded) rather than embedded to read back permanently stale."""
    project, keys = _source(tmp_path)
    _add_partial_model(
        project, keys, rows=[0, 1, 2, 3], model_name="rawmodel", intensity_quantity="raw"
    )
    out = tmp_path / "no_raw.tether"

    result = export_subset_tether(project, out, include_raw=False)

    sub = Project.open(out)
    assert "rawmodel" not in sub.list_idealizations()  # the raw-fit model is dropped
    assert "vbconhmm" in sub.list_idealizations()  # the corrected-fit model survives
    payload = json.loads(result.provenance_path.read_text(encoding="utf-8"))
    assert payload["parameters"]["skipped_idealization_models"] == ["rawmodel"]


def test_raw_fit_model_kept_when_raw_included(tmp_path):
    project, keys = _source(tmp_path)
    _add_partial_model(
        project, keys, rows=[0, 1, 2, 3], model_name="rawmodel", intensity_quantity="raw"
    )
    out = tmp_path / "with_raw.tether"

    export_subset_tether(project, out, include_raw=True)

    sub = Project.open(out)
    assert set(sub.list_idealizations()) == {"vbconhmm", "rawmodel"}  # raw layers present


# --------------------------------------------------------------------------- #
# Preflight: a malformed source fails loudly before any output is written
# --------------------------------------------------------------------------- #


def test_preflight_rejects_source_missing_corrected_layer(tmp_path):
    project, _ = _source(tmp_path)
    with h5py.File(project.path, "r+") as f:
        del f["traces"]["donor_corrected"]
    with pytest.raises(ValueError, match="missing the required corrected layer"):
        export_subset_tether(project, tmp_path / "s.tether")


def test_preflight_rejects_source_missing_patch_channel(tmp_path):
    project, _ = _source(tmp_path)
    with h5py.File(project.path, "r+") as f:
        del f["patches"]["donor"]
    with pytest.raises(ValueError, match="missing the 'donor' channel"):
        export_subset_tether(project, tmp_path / "s.tether")


# --------------------------------------------------------------------------- #
# Guardrails: don't clobber the source, overwrite semantics
# --------------------------------------------------------------------------- #


def test_hard_linked_out_path_refused(tmp_path):
    """A hard-linked out_path is the same inode as the source; overwrite would truncate
    it, so the export must refuse (resolve() alone would miss this)."""
    project, _ = _source(tmp_path)
    out = tmp_path / "hardlink.tether"
    try:
        os.link(project.path, out)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - platform/FS dependent
        pytest.skip(f"hard links unavailable here: {exc}")
    with pytest.raises(ValueError, match="is the source project"):
        export_subset_tether(project, out, overwrite=True)


def test_existing_subset_survives_a_failed_export(tmp_path, monkeypatch):
    """Staging + atomic replace: a mid-copy failure leaves a pre-existing subset intact
    and leaves no leftover temp file."""
    project, _ = _source(tmp_path)
    out = tmp_path / "subset.tether"
    export_subset_tether(project, out, molecule_keys=None)  # a good, complete subset (4 mols)
    assert read_molecules(out).shape[0] == 4

    # force a failure late in the copy (after the temp store is created)
    def _boom(*_a, **_k):
        raise RuntimeError("simulated mid-copy failure")

    monkeypatch.setattr("tether.project.export._copy_verbatim_groups", _boom)
    with pytest.raises(RuntimeError, match="simulated mid-copy failure"):
        export_subset_tether(project, out, overwrite=True)

    # the pre-existing subset is untouched, and no .tmp debris was left behind
    assert read_molecules(out).shape[0] == 4
    assert Project.open(out).schema_version == project.schema_version
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def test_out_path_equal_to_source_raises(tmp_path):
    project, _ = _source(tmp_path)
    with pytest.raises(ValueError, match="is the source project"):
        export_subset_tether(project, project.path)


def test_refuses_to_clobber_without_overwrite(tmp_path):
    project, _ = _source(tmp_path)
    out = tmp_path / "subset.tether"
    export_subset_tether(project, out)

    with pytest.raises((FileExistsError, OSError)):
        export_subset_tether(project, out)  # overwrite defaults False


def test_overwrite_replaces_an_existing_subset(tmp_path):
    project, keys = _source(tmp_path)
    out = tmp_path / "subset.tether"
    export_subset_tether(project, out, molecule_keys=[keys[0]])
    assert read_molecules(out).shape[0] == 1

    export_subset_tether(project, out, overwrite=True)  # all four now
    assert read_molecules(out).shape[0] == 4


def test_subset_accepts_a_path_or_project(tmp_path):
    project, _ = _source(tmp_path)
    by_path = tmp_path / "a.tether"
    by_project = tmp_path / "b.tether"

    r1 = export_subset_tether(project.path, by_path)
    r2 = export_subset_tether(project, by_project)

    assert r1.n_molecules == r2.n_molecules == 4


# --------------------------------------------------------------------------- #
# Schema freeze neutrality (the subset adds no structure)
# --------------------------------------------------------------------------- #


def test_subset_declares_the_frozen_schema_version(tmp_path):
    project, _ = _source(tmp_path)
    out = tmp_path / "subset.tether"

    export_subset_tether(project, out)

    # same on-disk schema_version as the source — additive data only, no bump
    assert Project.open(out).schema_version == project.schema_version

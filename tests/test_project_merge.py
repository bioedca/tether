# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Multi-curator split-file label merge-back (M5, FR-ML; PRD §5.1, §7.5, §7.10).

Locks :mod:`tether.project.merge`: the condition-owner's append-only owner-pull of a contributor's
split ``.tether`` — the labeled rows merge back joined on the stable ``molecule_key``, the owner's
weights recompute centrally, and human-vs-human disagreement on the same molecule surfaces as a
:class:`~tether.project.merge.MergeConflict` rather than silently overwriting the owner's decision.
Contains the §9 M5 acceptance (a **two-curator split-and-merge** confirming those three properties).
Needs h5py (base lock) for the ``.tether`` store.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("numpy")
h5py = pytest.importorskip("h5py")

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
from tether.project.labels import (  # noqa: E402
    LABEL_SOURCE_DEEPLASI,
    REJECT_LOG,
    CurationLabel,
    accept,
    curation_labels,
    read_labels,
    read_reject_log,
    reject,
    reject_record_of,
    set_curation_label,
    unreject,
)
from tether.project.merge import MergeConflict, merge_labels  # noqa: E402

_WINDOW = 21
_ACCEPT = int(CurationLabel.ACCEPT)
_REJECT = int(CurationLabel.REJECT)
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
    # Deterministic coords: the first ``m`` of ``_molecules(n)`` match ``_molecules(m)`` for m <= n,
    # so two stores built from the same movie sha share molecule_keys on their common prefix.
    coords = np.array([[12.0 + 1.7 * i, 14.0 + 2.3 * (i % 7)] for i in range(n)], dtype="float64")
    return ColocalizedMolecules(
        donor_xy=coords,
        acceptor_xy=coords,
        acceptor_detected=np.zeros(n, dtype=bool),
        donor_index=np.arange(n, dtype=np.intp),
        acceptor_index=np.full(n, -1, dtype=np.intp),
    )


def _movie(t: int, sha_char: str = "a") -> MovieMetadata:
    return MovieMetadata(
        movie_id="mov-1",
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


_FILENAME = "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif"
#: Same movie stem with a different tRNA concentration, so a different provisional condition_id.
_OTHER_CONDITION_FILENAME = "Bla_UCKOPSB_T-box_35pM_tRNA_300nM_010.tif"


def _store(path: Path, n: int, *, sha_char: str = "a", filename: str = _FILENAME) -> list[str]:
    """A single-condition ``.tether`` with ``n`` molecules (one movie); returns its molecule_keys.

    Two stores built with the same ``sha_char`` share molecule_keys on their common molecule prefix
    (same movie sha256 + same deterministic coords) — the split-file merge join. ``filename`` sets
    the provisional ``condition_id``; it is not part of the molecule_key.
    """
    t = 12
    create_project(path, overwrite=True)
    write_extraction(
        path,
        movie=_movie(t, sha_char=sha_char),
        molecules=_molecules(n),
        traces=_traces(n, t),
        parsed=parse_filename(filename),
        registration_map=_reg_map(),
    )
    return _keys(path)


def _condition_of(path: Path) -> str:
    (condition,) = {_to_text(c) for c in read_molecules(path)["condition_id"]}
    return condition


def _to_text(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _labels_of(path: Path) -> np.ndarray:
    return read_labels(path)


def _n_rows(path: Path) -> int:
    return int(read_labels(path).shape[0])


def _source_files(rows: np.ndarray) -> list[str]:
    return [s.decode() if isinstance(s, bytes) else str(s) for s in rows["source_file"]]


def test_two_curator_split_and_merge(tmp_path) -> None:
    # PRD §7.5 acceptance: split-file labels merge back on molecule_key, weights recompute
    # centrally, and human-vs-human conflicts surface (§7.10). Owner "alice", contributor "bob".
    owner = tmp_path / "owner.tether"
    split = tmp_path / "split.tether"
    okeys = _store(owner, n=5)
    skeys = _store(split, n=5)
    assert okeys == skeys  # same movie sha + coords => shared molecule_keys (the join)

    # Owner's prior decisions: keys[0] ACCEPT, keys[4] REJECT; keys[1..3] uncurated.
    accept(owner, okeys[0], labeler="alice")
    reject(owner, okeys[4], labeler="alice")

    # Contributor bob curates his split: conflict on [0], new on [1]/[2], agree on [4]; [3] left.
    reject(split, skeys[0], labeler="bob")  # conflicts with alice's ACCEPT
    accept(split, skeys[1], labeler="bob")  # new -> adopt
    reject(split, skeys[2], labeler="bob")  # new -> adopt
    reject(split, skeys[4], labeler="bob")  # agrees with alice's REJECT

    n_before = _n_rows(owner)  # 2 (alice's two rows)
    report = merge_labels(owner, split)

    # append-only, joined on molecule_key: bob's 4 human rows on matched keys are appended.
    assert report.appended == 4
    assert _n_rows(owner) == n_before + 4
    assert _source_files(_labels_of(owner)).count("split.tether") == 4  # bob's provenance survived

    # non-conflicting decisions adopted into the owner's authoritative curation_label (report tuples
    # are sorted by molecule_key hash, so compare as sets)...
    assert set(report.adopted) == {okeys[1], okeys[2]}
    assert report.agreements == (okeys[4],)
    labels = curation_labels(owner)
    assert labels[okeys[1]] == _ACCEPT
    assert labels[okeys[2]] == _REJECT

    # ...but the human-vs-human disagreement surfaces and never overwrites the owner's decision.
    assert report.n_conflicts == 1
    conflict = report.conflicts[0]
    assert conflict == MergeConflict(
        molecule_key=okeys[0],
        owner_label=_ACCEPT,
        contributor_label=_REJECT,
        contributor_labeler="bob",
        contributor_source_file="split.tether",
    )
    assert labels[okeys[0]] == _ACCEPT  # untouched pending reconcile
    assert labels[okeys[3]] == _UNCURATED  # bob never labeled it

    # weights recomputed centrally over the merged set (all 6 rows).
    assert report.weights_recomputed == 6
    assert report.unmatched == ()
    assert report.skipped_non_human == 0


def test_pulled_rows_take_the_owner_condition_id(tmp_path) -> None:
    # The physical molecule's condition lives on the owner's /molecules (§5.1): a contributor row
    # that carries another condition_id is appended under the owner's id, so the central weight
    # recompute groups it with the owner's condition (#729 PRJ-F10).
    owner = tmp_path / "owner.tether"
    split = tmp_path / "split.tether"
    okeys = _store(owner, n=3)
    skeys = _store(split, n=3, filename=_OTHER_CONDITION_FILENAME)
    assert okeys == skeys  # the join key does not depend on the condition
    owner_condition = _condition_of(owner)
    split_condition = _condition_of(split)
    assert owner_condition != split_condition

    accept(split, skeys[1], labeler="bob")
    reject(split, skeys[2], labeler="bob")
    assert {_to_text(c) for c in read_labels(split)["condition_id"]} == {split_condition}

    report = merge_labels(owner, split)

    assert report.appended == 2
    rows = read_labels(owner)
    pulled = [i for i, src in enumerate(_source_files(rows)) if src == "split.tether"]
    assert len(pulled) == 2
    assert [_to_text(rows["condition_id"][i]) for i in pulled] == [owner_condition] * 2
    # The contributor's file is read-only to the merge: its own rows keep its condition_id.
    assert {_to_text(c) for c in read_labels(split)["condition_id"]} == {split_condition}


def test_latest_contributor_decision_wins(tmp_path) -> None:
    # A contributor who changes their mind leaves two rows for one molecule; the merge reconciles
    # the LATEST decision (append order), never the first one (#729 PRJ-F10).
    owner = tmp_path / "owner.tether"
    split = tmp_path / "split.tether"
    okeys = _store(owner, n=3)
    _store(split, n=3)
    reject(split, okeys[1], labeler="bob")
    accept(split, okeys[1], labeler="bob")  # latest for key 1: ACCEPT
    accept(split, okeys[2], labeler="bob")
    reject(split, okeys[2], labeler="bob")  # latest for key 2: REJECT

    report = merge_labels(owner, split)

    assert report.appended == 4  # both rows of each key stay in the audit log
    assert set(report.adopted) == {okeys[1], okeys[2]}
    assert report.n_conflicts == 0
    labels = curation_labels(owner)
    assert labels[okeys[1]] == _ACCEPT
    assert labels[okeys[2]] == _REJECT
    assert labels[okeys[0]] == _UNCURATED


def test_withdrawn_contributor_decision_is_not_adopted(tmp_path) -> None:
    # Reject then un-reject: the contributor's latest decision is "no decision", so nothing is
    # adopted and no conflict is raised against the owner's own label (#729 PRJ-F10).
    owner = tmp_path / "owner.tether"
    split = tmp_path / "split.tether"
    okeys = _store(owner, n=3)
    _store(split, n=3)
    accept(owner, okeys[2], labeler="alice")
    for key in (okeys[1], okeys[2]):
        reject(split, key, labeler="bob")
        assert unreject(split, key, labeler="bob") is not None

    report = merge_labels(owner, split)

    assert report.appended == 4  # the reject and its reversal are both recorded
    assert report.adopted == ()
    assert report.agreements == ()
    assert report.n_conflicts == 0
    labels = curation_labels(owner)
    assert labels[okeys[1]] == _UNCURATED  # not adopted as REJECT
    assert labels[okeys[2]] == _ACCEPT  # the owner's label is not challenged


def test_unmatched_molecule_key_is_surfaced_not_dropped(tmp_path) -> None:
    # A split labels a molecule the owner does not have (its key absent from /molecules): surfaced
    # in report.unmatched, never appended (§7.5 never-silently-drop).
    owner = tmp_path / "owner.tether"
    split = tmp_path / "split.tether"
    _store(owner, n=3)
    skeys = _store(split, n=5)  # keys[3], keys[4] are extra molecules the owner lacks

    accept(split, skeys[0], labeler="bob")  # matched -> appended + adopted
    accept(split, skeys[4], labeler="bob")  # unmatched -> surfaced, not appended

    report = merge_labels(owner, split)

    assert report.unmatched == (skeys[4],)
    assert report.appended == 1  # only the matched key[0] row
    assert report.adopted == (skeys[0],)
    # the foreign key never entered the owner's log
    merged_keys = {
        k.decode() if isinstance(k, bytes) else str(k) for k in read_labels(owner)["molecule_key"]
    }
    assert skeys[4] not in merged_keys


def test_append_only_and_idempotent_re_pull(tmp_path) -> None:
    # Re-pulling the same split file appends nothing new (same-provenance rows deduplicated) and the
    # adopted state is stable — the second pull sees agreement, not a fresh adopt.
    owner = tmp_path / "owner.tether"
    split = tmp_path / "split.tether"
    okeys = _store(owner, n=3)
    _store(split, n=3)
    accept(split, okeys[1], labeler="bob")

    first = merge_labels(owner, split)
    assert first.appended == 1
    assert first.adopted == (okeys[1],)
    rows_after_first = _n_rows(owner)

    second = merge_labels(owner, split)
    assert second.appended == 0
    assert second.skipped_duplicate == 1
    assert _n_rows(owner) == rows_after_first  # append-only, but no duplicate event re-appended
    assert second.adopted == ()  # already owner-ACCEPT
    assert second.agreements == (okeys[1],)
    assert curation_labels(owner)[okeys[1]] == _ACCEPT


def test_reject_reasons_follow_the_appended_label_rows(tmp_path) -> None:
    # #1051: the split file's /labels/reject_log rows that join an appended /labels row on
    # molecule_key + labeler + timestamp are appended to the owner, under the owner's condition_id,
    # so reject_record_of reads the contributor's reason in the owner.
    owner = tmp_path / "owner.tether"
    split = tmp_path / "split.tether"
    okeys = _store(owner, n=4)
    skeys = _store(split, n=4, filename=_OTHER_CONDITION_FILENAME)
    reject(split, skeys[0], labeler="bob", reason="other", note="two spots")
    unreject(split, skeys[0], labeler="bob", note="one spot after all")
    reject(split, skeys[0], labeler="bob", reason="threshold", value=0.4)
    reject(split, skeys[1], labeler="bob", reason="multiple-molecules")
    # Not appended: a reason row whose molecule the owner does not hold (no label row joins it).
    foreign = _store(tmp_path / "foreign.tether", n=1, sha_char="b")[0]
    with h5py.File(split, "r+") as f:
        log = f["labels"][REJECT_LOG]
        extra = log[:1].copy()
        extra["molecule_key"] = foreign
        log.resize((log.shape[0] + 1,))
        log[-1:] = extra

    report = merge_labels(owner, split)

    assert report.appended == 4
    assert report.reject_log_appended == 4
    log = read_reject_log(owner)
    assert [_to_text(k) for k in log["molecule_key"]] == [okeys[0]] * 3 + [okeys[1]]
    assert [_to_text(e) for e in log["event"]] == ["reject", "restore", "reject", "reject"]
    assert {_to_text(c) for c in log["condition_id"]} == {_condition_of(owner)}
    record = reject_record_of(owner, okeys[1])
    assert record is not None
    assert _to_text(record["reason"]) == "multiple-molecules"
    assert _to_text(record["labeler"]) == "bob"
    # okeys[0] went to the owner as a fresh adopt: its current reject carries the threshold reason.
    record = reject_record_of(owner, okeys[0])
    assert record is not None
    assert _to_text(record["reason"]) == "threshold"
    assert float(record["value"]) == 0.4

    second = merge_labels(owner, split)
    assert second.appended == 0
    assert second.reject_log_appended == 0
    assert read_reject_log(owner).shape == (4,)


def test_reject_reasons_append_after_the_owner_s_own(tmp_path) -> None:
    # The owner's own reasons stay where they are; the contributor's are appended after them.
    owner = tmp_path / "owner.tether"
    split = tmp_path / "split.tether"
    okeys = _store(owner, n=3)
    _store(split, n=3)
    reject(owner, okeys[0], labeler="alice", reason="no-anticorrelation")
    reject(split, okeys[1], labeler="bob", reason="multiple-molecules")

    report = merge_labels(owner, split)

    assert report.reject_log_appended == 1
    log = read_reject_log(owner)
    assert [_to_text(r) for r in log["reason"]] == ["no-anticorrelation", "multiple-molecules"]
    assert _to_text(reject_record_of(owner, okeys[0])["labeler"]) == "alice"
    assert _to_text(reject_record_of(owner, okeys[1])["labeler"]) == "bob"


def test_a_pull_interrupted_before_the_reasons_completes_on_the_next(tmp_path, monkeypatch) -> None:
    # The label rows and the reason rows are two writes. A pull that stops between them leaves the
    # label rows in the owner; the next pull skips them as duplicates but still copies their
    # reasons, once.
    import tether.project.merge as merge_mod  # noqa: PLC0415

    owner = tmp_path / "owner.tether"
    split = tmp_path / "split.tether"
    okeys = _store(owner, n=2)
    _store(split, n=2)
    reject(split, okeys[1], labeler="bob", reason="multiple-molecules")
    real = merge_mod._append_reject_log_rows

    def _crash(*_args: object) -> int:
        raise RuntimeError("interrupted")

    monkeypatch.setattr(merge_mod, "_append_reject_log_rows", _crash)
    with pytest.raises(RuntimeError, match="interrupted"):
        merge_labels(owner, split)
    assert _n_rows(owner) == 1  # the label row landed
    assert read_reject_log(owner).shape == (0,)  # its reason did not
    monkeypatch.setattr(merge_mod, "_append_reject_log_rows", real)

    retry = merge_labels(owner, split)

    assert retry.appended == 0
    assert retry.skipped_duplicate == 1
    assert retry.reject_log_appended == 1
    assert _to_text(reject_record_of(owner, okeys[1])["reason"]) == "multiple-molecules"
    assert merge_labels(owner, split).reject_log_appended == 0


def test_a_join_that_names_two_rejects_copies_no_reason(tmp_path) -> None:
    # Two rejects with the same labeler and timestamp cannot be told apart (the split file reads no
    # reason for them). Their label rows collapse into one in the owner, so no reason is attached.
    owner = tmp_path / "owner.tether"
    split = tmp_path / "split.tether"
    okeys = _store(owner, n=2)
    _store(split, n=2)
    when = "2026-11-19T12:00:00+00:00"
    reject(split, okeys[1], labeler="bob", timestamp=when, reason="multiple-molecules")
    reject(split, okeys[1], labeler="bob", timestamp=when, reason="no-anticorrelation")
    assert reject_record_of(split, okeys[1]) is None

    report = merge_labels(owner, split)

    assert report.appended == 1  # the two identical events collapse into one label row
    assert report.reject_log_appended == 0
    assert curation_labels(owner)[okeys[1]] == _REJECT
    assert reject_record_of(owner, okeys[1]) is None


def test_an_accept_sharing_the_join_keeps_the_reject_reason(tmp_path) -> None:
    # An accept and a reject with the same labeler and timestamp are two decisions; the reject's
    # reason is still the one reject_record_of reads in the split file, so it travels.
    owner = tmp_path / "owner.tether"
    split = tmp_path / "split.tether"
    okeys = _store(owner, n=2)
    _store(split, n=2)
    when = "2026-11-19T12:00:00+00:00"
    accept(split, okeys[1], labeler="bob", timestamp=when)
    reject(split, okeys[1], labeler="bob", timestamp=when, reason="multiple-molecules")
    assert _to_text(reject_record_of(split, okeys[1])["reason"]) == "multiple-molecules"

    report = merge_labels(owner, split)

    assert report.appended == 2
    assert report.reject_log_appended == 1
    assert _to_text(reject_record_of(owner, okeys[1])["reason"]) == "multiple-molecules"


def test_split_file_without_a_reject_log_merges_as_before(tmp_path) -> None:
    # A split file with no /labels/reject_log (a label written straight through set_curation_label,
    # or a store from before #932) merges its label rows and leaves the owner without a log.
    owner = tmp_path / "owner.tether"
    split = tmp_path / "split.tether"
    okeys = _store(owner, n=2)
    _store(split, n=2)
    set_curation_label(split, okeys[1], _REJECT, labeler="bob")
    with h5py.File(split, "r") as f:
        assert REJECT_LOG not in f["labels"]

    report = merge_labels(owner, split)

    assert report.appended == 1
    assert report.reject_log_appended == 0
    assert curation_labels(owner)[okeys[1]] == _REJECT
    with h5py.File(owner, "r") as f:
        assert REJECT_LOG not in f["labels"]
    assert reject_record_of(owner, okeys[1]) is None


def test_within_batch_duplicate_events_deduped(tmp_path) -> None:
    # A single source can carry two identical-provenance human rows (a chained/replayed split file):
    # they must dedupe against each other, not just against the owner's pre-merge log — only one is
    # appended and `appended` stays exact.
    owner = tmp_path / "owner.tether"
    split = tmp_path / "split.tether"
    okeys = _store(owner, n=3)
    _store(split, n=3)
    ts = "2026-07-07T00:00:00+00:00"
    set_curation_label(split, okeys[1], CurationLabel.ACCEPT, labeler="bob", timestamp=ts)
    set_curation_label(split, okeys[1], CurationLabel.ACCEPT, labeler="bob", timestamp=ts)
    assert _n_rows(split) == 2  # two byte-identical event rows in the source

    n_before = _n_rows(owner)
    report = merge_labels(owner, split)

    assert report.appended == 1  # not 2 — the within-batch duplicate is skipped
    assert report.skipped_duplicate == 1
    assert _n_rows(owner) == n_before + 1


def test_provisional_source_rows_are_not_pulled(tmp_path) -> None:
    # A contributor's provisional seed rows are the owner's own machine priors (ADR-0038), not human
    # curation: they are counted (skipped_non_human), never merged.
    owner = tmp_path / "owner.tether"
    split = tmp_path / "split.tether"
    okeys = _store(owner, n=3)
    _store(split, n=3)
    set_curation_label(split, okeys[0], CurationLabel.ACCEPT, source=LABEL_SOURCE_DEEPLASI)
    accept(split, okeys[1], labeler="bob")

    report = merge_labels(owner, split)

    assert report.skipped_non_human == 1
    assert report.appended == 1  # only the human row
    sources = {s.decode() if isinstance(s, bytes) else str(s) for s in read_labels(owner)["source"]}
    assert LABEL_SOURCE_DEEPLASI not in sources


def test_conflict_appends_audit_row_but_leaves_training_signal_untouched(tmp_path) -> None:
    # A conflicting contributor row is recorded in the append-only /labels audit (provenance) yet
    # the owner's curation_label — the ranker's human training signal — is not overwritten.
    owner = tmp_path / "owner.tether"
    split = tmp_path / "split.tether"
    okeys = _store(owner, n=2)
    _store(split, n=2)
    accept(owner, okeys[0], labeler="alice")
    reject(split, okeys[0], labeler="bob")

    report = merge_labels(owner, split)

    assert report.appended == 1  # bob's conflicting row IS recorded (audit)
    assert report.n_conflicts == 1
    assert curation_labels(owner)[okeys[0]] == _ACCEPT  # training signal unchanged


def test_recompute_weights_false_defers_central_recompute(tmp_path) -> None:
    owner = tmp_path / "owner.tether"
    split = tmp_path / "split.tether"
    okeys = _store(owner, n=3)
    _store(split, n=3)
    accept(split, okeys[1], labeler="bob")

    report = merge_labels(owner, split, recompute_weights=False)

    assert report.appended == 1
    assert report.weights_recomputed == 0
    assert curation_labels(owner)[okeys[1]] == _ACCEPT  # adoption still applied


def test_merge_is_deterministic(tmp_path) -> None:
    def _run(root: Path) -> object:
        root.mkdir(parents=True, exist_ok=True)
        owner = root / "owner.tether"
        split = root / "split.tether"
        okeys = _store(owner, n=4)
        _store(split, n=4)
        accept(owner, okeys[0], labeler="alice")
        reject(split, okeys[0], labeler="bob")  # conflict
        accept(split, okeys[1], labeler="bob")  # adopt
        reject(split, okeys[2], labeler="bob")  # adopt
        return merge_labels(owner, split)

    a = _run(tmp_path / "a")
    b = _run(tmp_path / "b")
    assert a == b


def test_ambiguous_cross_condition_key_is_surfaced_not_adopted(tmp_path) -> None:
    # A molecule_key that maps to >1 owner condition_id (a same-sha cross-condition coordinate
    # collision, §7.10) has an ambiguous label scope: refuse it like set_curation_label does —
    # surface it, never append or adopt (which would inflate the other condition's n_human).
    import h5py

    owner = tmp_path / "owner.tether"
    split = tmp_path / "split.tether"
    okeys = _store(owner, n=3)
    _store(split, n=3)

    # White-box: duplicate molecule row 0 under a second condition_id, so okeys[0] now spans two
    # conditions in the owner's /molecules (the collision the normal intake would surface at M4).
    with h5py.File(owner, "r+") as f:
        table = f["molecules"]["table"]
        dup = table[0].copy()
        dup["condition_id"] = "second-condition"
        n0 = table.shape[0]
        table.resize((n0 + 1,))
        table[n0] = dup

    accept(split, okeys[0], labeler="bob")  # labels the ambiguous key
    accept(split, okeys[1], labeler="bob")  # an unambiguous control -> adopted

    report = merge_labels(owner, split)

    assert report.ambiguous == (okeys[0],)
    assert okeys[0] not in report.adopted
    assert report.appended == 1  # only the unambiguous key[1] row
    assert report.adopted == (okeys[1],)
    # neither /molecules row for the ambiguous key was flipped
    labels = read_molecules(owner)["curation_label"]
    keys = _keys(owner)
    ambiguous_labels = {int(labels[i]) for i, k in enumerate(keys) if k == okeys[0]}
    assert ambiguous_labels == {_UNCURATED}


def test_missing_labels_group_raises(tmp_path) -> None:
    import h5py

    owner = tmp_path / "owner.tether"
    split = tmp_path / "split.tether"
    _store(owner, n=2)
    _store(split, n=2)
    with h5py.File(split, "r+") as f:
        del f["labels"]

    with pytest.raises(KeyError, match="no /labels group"):
        merge_labels(owner, split)

# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the reason of each reject and the recorded restore (#932, PRD §7.2/§7.5).

A human reject appends a ``/labels/reject_log`` row (reason, note, threshold value, who,
when); an un-reject appends a ``"restore"`` row. The log is additive data: a fresh store
is unchanged and the curation filter reads ``curation_label``, never the reason.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

pytest.importorskip("h5py")
pytest.importorskip("numpy")

import h5py  # noqa: E402
import numpy as np  # noqa: E402

from tether.io.schema import MOLECULES_DTYPE, TABLE, create_project  # noqa: E402
from tether.project import Project  # noqa: E402
from tether.project import labels as L  # noqa: E402

_TS = "2026-11-14T12:00:00+00:00"


def _seed(tmp_path: Path, keys: list[str], *, name: str = "exp.tether") -> Path:
    """A ``.tether`` whose ``/molecules`` holds ``keys`` (uncurated, one condition)."""
    path = create_project(tmp_path / name)
    rows = np.zeros(len(keys), dtype=MOLECULES_DTYPE)
    for field in MOLECULES_DTYPE.names:
        if MOLECULES_DTYPE[field].kind == "O":
            rows[field] = ""
    rows["molecule_key"] = keys
    rows["condition_id"] = "cond"
    with h5py.File(path, "r+") as f:
        table = f["molecules"][TABLE]
        table.resize((len(keys),))
        table[:] = rows
    return path


def test_reject_records_reason_note_person_and_time(tmp_path: Path) -> None:
    path = _seed(tmp_path, ["k1"])
    label_row = L.reject(
        path,
        "k1",
        reason="multiple-molecules",
        note="two acceptor steps",
        labeler="ana",
        timestamp=_TS,
    )
    log = L.read_reject_log(path)
    assert log.shape == (1,)
    rec = log[0]
    assert L._to_str(rec["event"]) == L.REJECT_EVENT
    assert L._to_str(rec["reason"]) == "multiple-molecules"
    assert L._to_str(rec["note"]) == "two acceptor steps"
    assert math.isnan(float(rec["value"]))
    assert L._to_str(rec["labeler"]) == "ana"
    assert L._to_str(rec["timestamp"]) == _TS
    assert L._to_str(rec["condition_id"]) == "cond"
    # The reason row joins its /labels row exactly on key + who + when.
    for field in ("molecule_key", "labeler", "timestamp"):
        assert L._to_str(rec[field]) == L._to_str(label_row[field])
    record = L.reject_record_of(path, "k1")
    assert record is not None
    assert L._to_str(record["reason"]) == "multiple-molecules"


def test_default_reason_is_unspecified(tmp_path: Path) -> None:
    path = _seed(tmp_path, ["k1"])
    L.reject(path, "k1")
    record = L.reject_record_of(path, "k1")
    assert record is not None
    assert L._to_str(record["reason"]) == L.REJECT_REASON_UNSPECIFIED


def test_threshold_reject_records_reason_and_value(tmp_path: Path) -> None:
    path = _seed(tmp_path, ["k1"])
    L.reject(path, "k1", reason=L.REJECT_REASON_THRESHOLD, value=0.25)
    record = L.reject_record_of(path, "k1")
    assert record is not None
    assert L._to_str(record["reason"]) == "threshold"
    assert float(record["value"]) == pytest.approx(0.25)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"reason": "bored"}, "reason must be one of"),
        ({"reason": "threshold"}, "finite threshold value"),
        ({"reason": "threshold", "value": float("nan")}, "finite threshold value"),
        ({"reason": "artifact", "value": 0.5}, "only a 'threshold' reject"),
        ({"reason": "other"}, "must carry a note"),
        ({"reason": "other", "note": "   "}, "must carry a note"),
        ({"note": 3}, "note must be a string"),
    ],
)
def test_bad_reason_refused_before_any_write(tmp_path: Path, kwargs: dict, match: str) -> None:
    path = _seed(tmp_path, ["k1"])
    with pytest.raises(ValueError, match=match):
        L.reject(path, "k1", **kwargs)
    assert L.curation_label_of(path, "k1") == int(L.CurationLabel.UNCURATED)
    assert L.read_labels(path).shape == (0,)
    assert L.read_reject_log(path).shape == (0,)


def test_restore_is_recorded_and_trace_stays_in_store(tmp_path: Path) -> None:
    path = _seed(tmp_path, ["k1", "k2"])
    L.reject(path, "k1", reason="artifact")
    assert L.rejected_molecule_keys(path) == {"k1"}
    assert set(L.curation_labels(path)) == {"k1", "k2"}  # a reject deletes nothing
    assert L.unreject(path, "k1", note="the spike was a cosmic ray", labeler="ben") is not None
    log = L.read_reject_log(path)
    assert [L._to_str(e) for e in log["event"]] == [L.REJECT_EVENT, L.RESTORE_EVENT]
    assert L._to_str(log[1]["note"]) == "the spike was a cosmic ray"
    assert L._to_str(log[1]["labeler"]) == "ben"
    assert L.reject_record_of(path, "k1") is None
    # A no-op un-reject (k2 was never rejected) records nothing.
    assert L.unreject(path, "k2") is None
    assert L.read_reject_log(path).shape == (2,)


def test_record_is_the_latest_reject(tmp_path: Path) -> None:
    path = _seed(tmp_path, ["k1"])
    L.reject(path, "k1", reason="low-signal")
    L.unreject(path, "k1")
    L.reject(path, "k1", reason="no-anticorrelation")
    record = L.reject_record_of(path, "k1")
    assert record is not None
    assert L._to_str(record["reason"]) == "no-anticorrelation"


def test_reject_without_reason_row_reads_none(tmp_path: Path) -> None:
    # A store written before #932 (or a crash between the two writes): a REJECT with no log row.
    path = _seed(tmp_path, ["k1"])
    L.set_curation_label(path, "k1", L.CurationLabel.REJECT)
    assert L.read_reject_log(path).shape == (0,)
    assert L.reject_record_of(path, "k1") is None


def test_an_earlier_reason_is_never_given_to_a_later_reject(tmp_path: Path) -> None:
    # Codex finding on PR #1050: reject with a reason, accept, then a REJECT with no reason
    # row (a crash between the two writes, or a direct set_curation_label) must read None.
    path = _seed(tmp_path, ["k1"])
    L.reject(path, "k1", reason="artifact", timestamp="2026-11-14T12:00:00+00:00")
    L.accept(path, "k1", timestamp="2026-11-14T12:01:00+00:00")
    L.set_curation_label(path, "k1", L.CurationLabel.REJECT, timestamp="2026-11-14T12:02:00+00:00")
    assert L.reject_record_of(path, "k1") is None


def test_a_later_provisional_label_does_not_hide_the_human_reason(tmp_path: Path) -> None:
    path = _seed(tmp_path, ["k1"])
    L.reject(path, "k1", reason="low-signal")
    L.accept(path, "k1", source=L.LABEL_SOURCE_CROSS_CONDITION, weight=0.5)
    record = L.reject_record_of(path, "k1")
    assert record is not None
    assert L._to_str(record["reason"]) == "low-signal"


def test_provisional_reject_writes_no_reason_row(tmp_path: Path) -> None:
    path = _seed(tmp_path, ["k1"])
    L.reject(path, "k1", source=L.LABEL_SOURCE_DEEPLASI, weight=0.5)
    assert L.read_reject_log(path).shape == (0,)


def test_fresh_store_has_no_reject_log(tmp_path: Path) -> None:
    path = create_project(tmp_path / "fresh.tether")
    with h5py.File(path, "r") as f:
        assert list(f["labels"]) == [TABLE]
    assert L.read_reject_log(path).shape == (0,)


def test_filter_reads_the_label_not_the_reason(tmp_path: Path) -> None:
    with_reason = _seed(tmp_path, ["k1", "k2", "k3"], name="a.tether")
    without_reason = _seed(tmp_path, ["k1", "k2", "k3"], name="b.tether")
    L.reject(with_reason, "k2", reason=L.REJECT_REASON_THRESHOLD, value=0.1)
    L.set_curation_label(without_reason, "k2", L.CurationLabel.REJECT)
    for include in (False, True):
        masks = []
        for path in (with_reason, without_reason):
            with h5py.File(path, "r") as f:
                masks.append(
                    L.curation_filter_mask(f["molecules"][TABLE][:], include_rejected=include)
                )
        np.testing.assert_array_equal(masks[0], masks[1])
    with h5py.File(with_reason, "r") as f:
        assert L.curation_filter_mask(f["molecules"][TABLE][:]).tolist() == [True, False, True]


def test_reasons_are_one_documented_list() -> None:
    assert L.REJECT_REASON_UNSPECIFIED in L.REJECT_REASONS
    assert L.REJECT_REASON_THRESHOLD in L.REJECT_REASONS
    assert L.REJECT_REASON_OTHER in L.REJECT_REASONS
    assert all(isinstance(text, str) and text for text in L.REJECT_REASONS.values())
    with pytest.raises(TypeError):
        L.REJECT_REASONS["new"] = "x"  # type: ignore[index]
    doc = (Path(__file__).resolve().parents[1] / "docs/reference/tether-format.md").read_text(
        encoding="utf-8"
    )
    for code in L.REJECT_REASONS:
        assert f"`{code}`" in doc, f"reject reason {code!r} is not documented"


def test_project_methods_pass_reason_through(tmp_path: Path) -> None:
    path = _seed(tmp_path, ["k1"])
    proj = Project.open(path)
    proj.reject("k1", reason="artifact", note="drift")
    record = proj.reject_record("k1")
    assert record is not None
    assert L._to_str(record["reason"]) == "artifact"
    proj.unreject("k1", note="fine after all")
    assert proj.reject_record("k1") is None
    assert L._to_str(L.read_reject_log(path)[-1]["event"]) == L.RESTORE_EVENT


def test_a_reused_join_key_never_borrows_an_earlier_reason(tmp_path: Path) -> None:
    # CodeRabbit finding on PR #1050: a later reject that reuses the same labeler and
    # timestamp as an earlier reasoned reject must not read that earlier reason.
    path = _seed(tmp_path, ["k1"])
    L.reject(path, "k1", reason="artifact", labeler="ana", timestamp=_TS)
    L.accept(path, "k1", labeler="ana", timestamp="2026-11-14T12:01:00+00:00")
    L.set_curation_label(path, "k1", L.CurationLabel.REJECT, labeler="ana", timestamp=_TS)
    assert L.reject_record_of(path, "k1") is None


@pytest.mark.parametrize("note", ["artifact\x00detail", "bad \udcff surrogate"])
def test_a_note_hdf5_cannot_store_is_refused_before_any_write(tmp_path: Path, note: str) -> None:
    # CodeRabbit finding on PR #1050: the note is checked before the label changes, for a
    # reject and for a restore, so a failed reason write never leaves a half-recorded event.
    path = _seed(tmp_path, ["k1"])
    with pytest.raises(ValueError, match="note"):
        L.reject(path, "k1", note=note)
    assert L.read_labels(path).shape == (0,)
    L.reject(path, "k1", reason="artifact")
    with pytest.raises(ValueError, match="note"):
        L.unreject(path, "k1", note=note)
    assert L.curation_label_of(path, "k1") == int(L.CurationLabel.REJECT)
    assert L.read_labels(path).shape == (1,)
    assert L.read_reject_log(path).shape == (1,)


def test_an_ordinary_unicode_note_is_kept(tmp_path: Path) -> None:
    path = _seed(tmp_path, ["k1"])
    L.reject(path, "k1", reason="other", note="señal débil, ΔFRET ≈ 0")
    record = L.reject_record_of(path, "k1")
    assert record is not None
    assert L._to_str(record["note"]) == "señal débil, ΔFRET ≈ 0"

# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for curation accept/reject logging to ``/labels`` (M2 S5, PRD §5.1/§7.5).

Covers the S5 gate (PLAN §6 S5): every accept/reject writes a fully-provenanced
``/labels`` row and sets the molecule's ``curation_label``; reject is a reversible
sticky tag carrying the toggleable exclusion filter (§7.5). All headless (no Qt) —
plus one Qt-free :class:`CurationController` wiring smoke showing the GUI seam.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

pytest.importorskip("h5py")
pytest.importorskip("numpy")

import h5py  # noqa: E402
import numpy as np  # noqa: E402

from tether.io.schema import LABELS_DTYPE, MOLECULES_DTYPE, TABLE, create_project  # noqa: E402
from tether.project import Project  # noqa: E402
from tether.project import labels as L  # noqa: E402


def _seed(tmp_path: Path, specs: list[tuple[str, str]], *, name: str = "exp.tether") -> Path:
    """Create a ``.tether`` with molecule rows ``(molecule_key, condition_id)``.

    Seeds only the fields curation touches (``molecule_key``, ``condition_id``,
    ``curation_label`` = uncurated); everything else stays at the dtype zero-fill.
    Avoids the heavy extraction pipeline while producing a schema-faithful store.
    Duplicate ``molecule_key`` entries are allowed (to exercise the §7.10 case).
    """
    path = create_project(tmp_path / name)
    rows = np.zeros(len(specs), dtype=MOLECULES_DTYPE)
    # np.zeros leaves the variable-length-string fields as int 0, which h5py
    # refuses to write; initialize every string field to "" (as the real
    # extraction writer does) before overriding the ones curation resolves on.
    for field in MOLECULES_DTYPE.names:
        if MOLECULES_DTYPE[field].kind == "O":
            rows[field] = ""
    rows["molecule_id"] = [f"mol-{i}" for i in range(len(specs))]
    rows["molecule_key"] = [key for key, _ in specs]
    rows["condition_id"] = [cond for _, cond in specs]
    rows["curation_label"] = int(L.CurationLabel.UNCURATED)
    with h5py.File(path, "r+") as f:
        table = f["molecules"][TABLE]
        table.resize((len(specs),))
        table[:] = rows
    return path


def _assert_full_provenance(
    row: np.ndarray,
    *,
    molecule_key: str,
    label_value: int,
    condition_id: str,
    source_file: str,
    source: str = L.LABEL_SOURCE_HUMAN,
    weight: float = L.HUMAN_WEIGHT,
) -> None:
    """Assert a ``/labels`` row carries the full §5.1 provenance field-set."""
    assert L._to_str(row["molecule_key"]) == molecule_key
    assert L._to_str(row["labeler"])  # non-empty labeler identity
    datetime.fromisoformat(L._to_str(row["timestamp"]))  # a parseable ISO-8601 instant
    assert L._to_str(row["source_file"]) == source_file
    assert L._to_str(row["source"]) == source
    assert float(row["weight"]) == pytest.approx(weight)
    assert int(row["label_value"]) == label_value
    assert L._to_str(row["condition_id"]) == condition_id


# --- provenance + state (accept / reject / unreject all fully provenanced) ----


def test_accept_writes_full_provenance_row(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("key-A", "cond-1")])
    returned = L.accept(path, "key-A", labeler="alice", timestamp="2026-07-01T12:00:00+00:00")

    labels = L.read_labels(path)
    assert labels.shape[0] == 1
    _assert_full_provenance(
        labels[0],
        molecule_key="key-A",
        label_value=int(L.CurationLabel.ACCEPT),
        condition_id="cond-1",  # inherited from the molecule
        source_file=path.name,
    )
    assert L._to_str(labels["labeler"][0]) == "alice"
    assert L._to_str(labels["timestamp"][0]) == "2026-07-01T12:00:00+00:00"
    # ...the returned row is a usable copy matching disk...
    assert int(returned["label_value"]) == int(L.CurationLabel.ACCEPT)
    # ...and the molecule's authoritative human state is updated.
    assert L.curation_label_of(path, "key-A") == int(L.CurationLabel.ACCEPT)


def test_reject_full_provenance_and_state(tmp_path: Path) -> None:
    # A distinct condition_id catches an idx-0/hardcode bug in the inheritance.
    path = _seed(tmp_path, [("key-A", "cond-X")])
    L.reject(path, "key-A", labeler="bob")

    assert L.curation_label_of(path, "key-A") == int(L.CurationLabel.REJECT) == -1
    _assert_full_provenance(
        L.read_labels(path)[0],
        molecule_key="key-A",
        label_value=int(L.CurationLabel.REJECT),
        condition_id="cond-X",
        source_file=path.name,
    )
    assert L.rejected_molecule_keys(path) == {"key-A"}


def test_unreject_full_provenance_and_reversal(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("key-A", "cond-X")])
    L.reject(path, "key-A")
    L.unreject(path, "key-A")

    # State cleared back to uncurated; no longer in the rejected bin.
    assert L.curation_label_of(path, "key-A") == int(L.CurationLabel.UNCURATED) == 0
    assert L.rejected_molecule_keys(path) == set()
    # ...both events persist (append order preserved), each fully provenanced.
    labels = L.read_labels(path)
    assert [int(v) for v in labels["label_value"]] == [
        int(L.CurationLabel.REJECT),
        int(L.CurationLabel.UNCURATED),
    ]
    _assert_full_provenance(
        labels[1],
        molecule_key="key-A",
        label_value=int(L.CurationLabel.UNCURATED),
        condition_id="cond-X",
        source_file=path.name,
    )


def test_default_labeler_timestamp_and_source_file(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("key-A", "cond-1")])
    L.accept(path, "key-A")  # no labeler/timestamp/source_file supplied

    row = L.read_labels(path)[0]
    assert L._to_str(row["labeler"])  # non-empty best-effort identity
    datetime.fromisoformat(L._to_str(row["timestamp"]))
    assert L._to_str(row["source_file"]) == path.name


# --- provisional sources are /labels-only priors (never human state) ----------


def test_provisional_source_does_not_touch_curation_label(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("key-A", "cond-1")])
    L.set_curation_label(
        path,
        "key-A",
        L.CurationLabel.ACCEPT,
        source=L.LABEL_SOURCE_DEEPLASI,
        weight=0.3,
    )
    # The provisional prior is logged...
    row = L.read_labels(path)[0]
    assert L._to_str(row["source"]) == L.LABEL_SOURCE_DEEPLASI
    assert float(row["weight"]) == pytest.approx(0.3)
    assert int(row["label_value"]) == int(L.CurationLabel.ACCEPT)
    # ...but the molecule's human state stays uncurated (§5.1 independence).
    assert L.curation_label_of(path, "key-A") == int(L.CurationLabel.UNCURATED)


def test_weight_zero_is_legal(tmp_path: Path) -> None:
    # A fully-decayed seed weight (limit of w = w0/(1+n_human), §7.5) is valid.
    path = _seed(tmp_path, [("key-A", "cond-1")])
    L.set_curation_label(
        path, "key-A", L.CurationLabel.ACCEPT, source=L.LABEL_SOURCE_CROSS_CONDITION, weight=0.0
    )
    assert float(L.read_labels(path)[0]["weight"]) == 0.0


# --- reversible sticky reject + exclusion filter (§7.5) ----------------------


def test_reject_is_sticky_until_reversed(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("key-A", "c")])
    L.reject(path, "key-A")
    # A fresh open still sees the reject (persisted per-molecule, not in-memory).
    assert L.rejected_molecule_keys(path) == {"key-A"}
    L.accept(path, "key-A")  # accepting also clears the reject bin
    assert L.rejected_molecule_keys(path) == set()
    assert L.curation_label_of(path, "key-A") == int(L.CurationLabel.ACCEPT)


def test_unreject_is_noop_off_reject(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("acc", "c"), ("unc", "c")])
    L.accept(path, "acc")
    n_before = L.read_labels(path).shape[0]

    # Un-rejecting an ACCEPTED molecule must not clobber it, and logs nothing.
    assert L.unreject(path, "acc") is None
    assert L.curation_label_of(path, "acc") == int(L.CurationLabel.ACCEPT)
    # Un-rejecting a never-curated molecule is a no-op too (no spurious clear row).
    assert L.unreject(path, "unc") is None
    assert L.curation_label_of(path, "unc") == int(L.CurationLabel.UNCURATED)
    assert L.read_labels(path).shape[0] == n_before


def test_exclusion_filter_default_and_toggle(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("key-A", "c"), ("key-B", "c"), ("key-C", "c")])
    L.accept(path, "key-A")  # accepted must be KEPT (not just uncurated)
    L.reject(path, "key-B")  # rejected excluded by default
    # key-C left uncurated
    with h5py.File(path, "r") as f:
        molecules = f["molecules"][TABLE][:]

    assert L.curation_filter_mask(molecules).tolist() == [True, False, True]
    assert L.curation_filter_mask(molecules, include_rejected=True).tolist() == [True, True, True]
    assert L.rejected_molecule_keys(path) == {"key-B"}


def test_exclusion_filter_edges() -> None:
    # All-uncurated -> all kept; an empty table -> an empty bool mask.
    all_unc = np.zeros(3, dtype=MOLECULES_DTYPE)
    all_unc["curation_label"] = int(L.CurationLabel.UNCURATED)
    assert L.curation_filter_mask(all_unc).tolist() == [True, True, True]

    empty = np.zeros(0, dtype=MOLECULES_DTYPE)
    mask = L.curation_filter_mask(empty)
    assert mask.dtype == bool
    assert mask.tolist() == []


# --- duplicate molecule_key contract (§7.10) ---------------------------------


def test_duplicate_key_same_condition_updates_all_rows(tmp_path: Path) -> None:
    # Two /molecules rows share a molecule_key (same physical molecule, §7.10).
    path = _seed(tmp_path, [("dup", "cond-1"), ("dup", "cond-1"), ("other", "cond-1")])
    L.reject(path, "dup")

    # One label event -> one /labels row; BOTH matched rows get the state.
    assert L.read_labels(path).shape[0] == 1
    with h5py.File(path, "r") as f:
        labels_col = f["molecules"][TABLE]["curation_label"][:]
    assert labels_col.tolist() == [
        int(L.CurationLabel.REJECT),
        int(L.CurationLabel.REJECT),
        int(L.CurationLabel.UNCURATED),
    ]


def test_duplicate_key_divergent_condition_raises(tmp_path: Path) -> None:
    # A corrupt/mis-merged file where one key maps to two conditions is refused,
    # not silently mis-attributed to condition idx[0].
    path = _seed(tmp_path, [("dup", "cond-1"), ("dup", "cond-2")])
    with pytest.raises(ValueError, match="condition_id"):
        L.accept(path, "dup")
    assert L.read_labels(path).shape[0] == 0  # nothing logged


# --- validation / error paths (no partial write) -----------------------------


def test_unknown_key_raises_not_silent(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("key-A", "c")])
    with pytest.raises(KeyError):
        L.accept(path, "no-such-key")
    with pytest.raises(KeyError):
        L.curation_label_of(path, "no-such-key")
    with pytest.raises(KeyError):
        L.unreject(path, "no-such-key")
    assert L.read_labels(path).shape[0] == 0  # nothing logged for the failed accept


def test_invalid_source_weight_label_raise_no_partial_write(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("key-A", "c")])
    for bad in (
        lambda: L.set_curation_label(path, "key-A", L.CurationLabel.ACCEPT, source="bogus"),
        lambda: L.accept(path, "key-A", weight=-1.0),
        lambda: L.accept(path, "key-A", weight=float("nan")),
        lambda: L.set_curation_label(path, "key-A", 7),  # not a CurationLabel
    ):
        with pytest.raises(ValueError):
            bad()
    # Every guard runs before the file is opened: no row logged, state untouched.
    assert L.read_labels(path).shape[0] == 0
    assert L.curation_label_of(path, "key-A") == int(L.CurationLabel.UNCURATED)


# --- schema freeze: the frozen fields stay signed <i4 and round-trip ---------


def test_frozen_label_fields_are_signed_i4(tmp_path: Path) -> None:
    # Guard what THIS PR owns (signed round-trip), not the whole-dtype structure
    # (schema-guard's diff_manifest owns that, via additive-safe prefix comparison).
    assert LABELS_DTYPE["label_value"] == np.dtype("<i4")
    assert MOLECULES_DTYPE["curation_label"] == np.dtype("<i4")

    path = _seed(tmp_path, [("key-A", "c")])
    L.reject(path, "key-A")
    stored = L.read_labels(path)["label_value"][0]
    assert stored.dtype == np.dtype("<i4")
    assert int(stored) == -1  # REJECT reads back signed, not wrapped to 4294967295


# --- Project delegation ------------------------------------------------------


def test_project_methods_delegate(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("key-A", "c"), ("key-B", "c")])
    proj = Project.open(path)
    proj.accept("key-A")
    proj.reject("key-B")
    assert proj.curation_label("key-A") == int(L.CurationLabel.ACCEPT)
    assert proj.curation_label("key-B") == int(L.CurationLabel.REJECT)
    assert proj.rejected_molecule_keys() == {"key-B"}
    assert proj.read_labels().shape[0] == 2
    proj.unreject("key-B")
    assert proj.rejected_molecule_keys() == set()


# --- the GUI seam: a Qt-free CurationController wired to the store ------------


def test_controller_wiring_writes_only_for_curation_actions(tmp_path: Path) -> None:
    # `tether.gui.curation` imports Qt lazily; the controller/handlers are Qt-free,
    # so this end-to-end path runs in the default matrix (mirrors test_curation).
    from tether.gui.curation import (
        Command,
        CurationAction,
        CurationController,
        CurationHandlers,
    )

    path = _seed(tmp_path, [("key-A", "c"), ("key-B", "c")])
    selected = {"key": "key-A"}  # stand-in for the browser's current selection

    handlers = CurationHandlers(
        accept=lambda: L.accept(path, selected["key"], labeler="curator"),
        reject=lambda: L.reject(path, selected["key"], labeler="curator"),
    )
    controller = CurationController(handlers)

    controller.dispatch(Command(CurationAction.ACCEPT))  # accept key-A
    selected["key"] = "key-B"
    controller.dispatch(Command(CurationAction.REJECT))  # reject key-B
    # Non-curation actions must write NO /labels row (the dual of the gate).
    controller.dispatch(Command(CurationAction.JUMP))
    controller.dispatch(Command(CurationAction.NEXT))

    labels = L.read_labels(path)
    assert labels.shape[0] == 2  # JUMP/NEXT logged nothing
    pairs = {
        (L._to_str(k), int(v))
        for k, v in zip(labels["molecule_key"], labels["label_value"], strict=True)
    }
    assert pairs == {
        ("key-A", int(L.CurationLabel.ACCEPT)),
        ("key-B", int(L.CurationLabel.REJECT)),
    }
    assert {L._to_str(k) for k in labels["labeler"]} == {"curator"}

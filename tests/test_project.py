# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the headless project core (PRD §7.11, §5.4)."""

from __future__ import annotations

import h5py
import pytest

from tether.io.schema import FORMAT_TAG, SCHEMA_VERSION
from tether.project import Project


def test_create_writes_frozen_skeleton(tmp_path) -> None:
    path = tmp_path / "demo.tether"
    proj = Project.create(path)
    assert proj.path == path
    assert path.exists()
    assert proj.schema_version == SCHEMA_VERSION
    assert proj.app_schema_version == SCHEMA_VERSION


def test_open_round_trips(tmp_path) -> None:
    path = tmp_path / "demo.tether"
    Project.create(path)
    reopened = Project.open(path)
    assert reopened.schema_version == SCHEMA_VERSION


def test_create_refuses_to_clobber(tmp_path) -> None:
    path = tmp_path / "demo.tether"
    Project.create(path)
    with pytest.raises((FileExistsError, OSError)):
        Project.create(path)
    # ...unless explicitly told to overwrite.
    assert Project.create(path, overwrite=True).schema_version == SCHEMA_VERSION


def test_open_missing_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        Project.open(tmp_path / "nope.tether")


def test_open_refuses_future_schema(tmp_path) -> None:
    # A file written by a newer Tether must be refused (PRD §5.4 forward guard).
    path = tmp_path / "demo.tether"
    Project.create(path)
    with h5py.File(path, "r+") as f:
        f.attrs["schema_version"] = SCHEMA_VERSION + 1
    with pytest.raises(ValueError):
        Project.open(path)


def test_open_rejects_foreign_hdf5(tmp_path) -> None:
    # A valid HDF5 that is not a Tether store (no `format` marker) is refused,
    # not silently accepted (PRD §5.1 on-disk contract).
    foreign = tmp_path / "foreign.h5"
    with h5py.File(foreign, "w") as f:
        f.attrs["schema_version"] = SCHEMA_VERSION  # present, but no `format`
        f.create_dataset("payload", data=[1, 2, 3])
    with pytest.raises(ValueError):
        Project.open(foreign)


def test_open_rejects_non_hdf5(tmp_path) -> None:
    junk = tmp_path / "not.tether"
    junk.write_bytes(b"this is not an HDF5 file")
    with pytest.raises(ValueError):
        Project.open(junk)


def test_open_rejects_truncated_project(tmp_path) -> None:
    # The root markers are present but the frozen §5.1 skeleton is missing, e.g.
    # an interrupted write — must be refused, not silently accepted.
    trunc = tmp_path / "trunc.tether"
    with h5py.File(trunc, "w") as f:
        f.attrs["format"] = FORMAT_TAG
        f.attrs["schema_version"] = SCHEMA_VERSION
        # deliberately no /movies, /molecules, /conditions, /labels, ... groups
    with pytest.raises(ValueError):
        Project.open(trunc)


def test_parse_condition_is_provisional() -> None:
    parsed = Project.parse_condition("Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif")
    assert parsed.key.ligand == "tRNA"
    assert parsed.condition_id.startswith("cond-")

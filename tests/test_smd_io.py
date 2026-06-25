# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""SMD-HDF5 interchange round-trip and tMAVEN-openable structure (PRD §7.4)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from tether.idealize import read_smd, write_smd

FIXTURES = Path(__file__).parent / "fixtures"
FOUR_MOL = FIXTURES / "smd_4mol.hdf5"


def test_read_4mol_fixture() -> None:
    """The committed 4-molecule tMAVEN SMD reads with its known geometry."""
    pytest.importorskip("h5py")
    smd = read_smd(FOUR_MOL)
    assert smd.raw.shape == (4, 1700, 2)
    assert smd.raw.dtype == np.float64
    assert smd.n_molecules == 4
    assert smd.n_channels == 2
    assert len(smd.source_names) >= 1
    # The fixture carries a tMAVEN group (classes + analysis windows).
    assert smd.has_tmaven
    assert smd.classes.shape == (4,)
    assert smd.pre_list.shape == (4,)
    assert smd.post_list.shape == (4,)
    # Reference fixture is a Deep-LASI export: no native coordinates.
    assert not smd.has_superset


def test_roundtrip_fixture_reexports(tmp_path: Path) -> None:
    """Read the fixture, re-export it, and confirm it re-opens identically.

    This is the M0.5 S1 "exported SMD is valid / re-opens" acceptance.
    """
    pytest.importorskip("h5py")
    src = read_smd(FOUR_MOL)
    out = write_smd(
        tmp_path / "reexport.hdf5",
        src.raw,
        source_names=src.source_names,
        source_index=src.source_index,
        classes=src.classes,
        pre_list=src.pre_list,
        post_list=src.post_list,
    )
    back = read_smd(out)
    np.testing.assert_array_equal(back.raw, src.raw)
    np.testing.assert_array_equal(back.source_index, src.source_index)
    np.testing.assert_array_equal(back.classes, src.classes)
    np.testing.assert_array_equal(back.pre_list, src.pre_list)
    np.testing.assert_array_equal(back.post_list, src.post_list)
    assert back.source_names == src.source_names


def test_written_smd_has_tmaven_openable_structure(tmp_path: Path) -> None:
    """A written SMD mirrors exactly the groups tMAVEN's loader requires."""
    h5py = pytest.importorskip("h5py")
    raw = np.random.default_rng(0).random((3, 20, 2))
    out = write_smd(
        tmp_path / "synthetic.hdf5",
        raw,
        source_names=["movieA.tif", "movieB.tif"],
        source_index=[0, 1, 1],
        classes=[0, 1, 2],
        donor_xy=[[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]],
        acceptor_xy=[[7.0, 8.0], [9.0, 10.0], [11.0, 12.0]],
        molecule_keys=["k0", "k1", "k2"],
    )
    with h5py.File(out, "r") as f:
        g = f["dataset"]
        assert g.attrs["format"] == "SMD"
        assert "date_created" in g.attrs and "date_modified" in g.attrs
        assert g["data/raw"].shape == (3, 20, 2)
        assert g["data/source_index"].dtype == np.int64
        # tMAVEN reads sources by integer-keyed subgroups + a source_list attr.
        assert g["sources"].attrs["source_list"] == str(["movieA.tif", "movieB.tif"])
        assert g["sources/0"].attrs["source_name"] == "movieA.tif"
        assert g["sources/1"].attrs["source_name"] == "movieB.tif"
        assert g["tMAVEN"].attrs["format"] == "tMAVEN"
        assert g["tMAVEN/classes"].shape == (3,)
        # Tether superset rides alongside in a group tMAVEN ignores.
        assert g["tether"].attrs["format"] == "tether-smd-superset"
        assert g["tether/donor_xy"].shape == (3, 2)


def test_superset_coordinates_roundtrip(tmp_path: Path) -> None:
    """Tether→Tether: coordinates and keys survive the superset group."""
    pytest.importorskip("h5py")
    raw = np.random.default_rng(1).random((2, 15, 2))
    donor = np.array([[10.5, 20.5], [30.5, 40.5]])
    acceptor = np.array([[50.5, 60.5], [70.5, 80.5]])
    out = write_smd(
        tmp_path / "superset.hdf5",
        raw,
        donor_xy=donor,
        acceptor_xy=acceptor,
        molecule_keys=["mk-a", "mk-b"],
        molecule_ids=["id-a", "id-b"],
    )
    back = read_smd(out)
    assert back.has_superset
    np.testing.assert_array_equal(back.donor_xy, donor)
    np.testing.assert_array_equal(back.acceptor_xy, acceptor)
    assert back.molecule_keys == ["mk-a", "mk-b"]
    assert back.molecule_ids == ["id-a", "id-b"]


def test_plain_smd_has_no_superset(tmp_path: Path) -> None:
    """Without coordinates the file is a plain SMD (no tether group)."""
    h5py = pytest.importorskip("h5py")
    raw = np.random.default_rng(2).random((2, 10, 2))
    out = write_smd(tmp_path / "plain.hdf5", raw)
    with h5py.File(out, "r") as f:
        assert "tether" not in f["dataset"]
    back = read_smd(out)
    assert not back.has_superset
    assert back.donor_xy is None


def test_write_rejects_bad_raw_shape(tmp_path: Path) -> None:
    pytest.importorskip("h5py")
    with pytest.raises(ValueError, match="n_frames"):
        write_smd(tmp_path / "bad.hdf5", np.zeros((3, 10, 3)))


def test_write_rejects_dangling_source_index(tmp_path: Path) -> None:
    pytest.importorskip("h5py")
    with pytest.raises(ValueError, match="source"):
        write_smd(
            tmp_path / "bad.hdf5",
            np.zeros((2, 5, 2)),
            source_names=["only-one"],
            source_index=[0, 1],
        )


def test_write_rejects_negative_source_index(tmp_path: Path) -> None:
    pytest.importorskip("h5py")
    with pytest.raises(ValueError, match="non-negative"):
        write_smd(
            tmp_path / "bad.hdf5",
            np.zeros((2, 5, 2)),
            source_names=["a", "b"],
            source_index=[0, -1],
        )


def test_read_rejects_non_smd_group(tmp_path: Path) -> None:
    h5py = pytest.importorskip("h5py")
    path = tmp_path / "notsmd.hdf5"
    with h5py.File(path, "w") as f:
        f.create_group("dataset").attrs["format"] = "something-else"
    with pytest.raises(ValueError, match="not SMD"):
        read_smd(path)


def test_read_rejects_malformed_raw(tmp_path: Path) -> None:
    """A 3-channel (non donor/acceptor) raw is rejected at read time."""
    h5py = pytest.importorskip("h5py")
    path = tmp_path / "malformed.hdf5"
    with h5py.File(path, "w") as f:
        g = f.create_group("dataset")
        g.attrs["format"] = "SMD"
        g.create_group("data").create_dataset("raw", data=np.zeros((2, 5, 3)))
    with pytest.raises(ValueError, match="raw must be"):
        read_smd(path)


def test_read_rejects_half_superset(tmp_path: Path) -> None:
    """A tether superset with only donor_xy (no acceptor_xy) is rejected."""
    h5py = pytest.importorskip("h5py")
    raw = np.random.default_rng(4).random((2, 8, 2))
    path = write_smd(tmp_path / "half.hdf5", raw)
    with h5py.File(path, "a") as f:
        gx = f["dataset"].create_group("tether")
        gx.attrs["format"] = "tether-smd-superset"
        gx.create_dataset("donor_xy", data=np.zeros((2, 2)))
    with pytest.raises(ValueError, match="only one of donor_xy"):
        read_smd(path)


def test_smd_module_does_not_import_h5py_eagerly() -> None:
    """match_return_leg (pure NumPy) must not transitively require h5py."""
    import tether.idealize.smd as smd_module

    assert not hasattr(smd_module, "h5py")

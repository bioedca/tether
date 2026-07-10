# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the MCOS string/cell decode primitive (M7 S2, PRD §7.8).

Locks :func:`tether.io.mcos._decode_matlab_value` and ``_decode_matlab_chars`` — the
recovery path for the ``TIRFdata`` ``Channel.FilePath = {dir, {movie}}`` embedded
movie reference — against a synthetic HDF5 file (no MCOS ``FileWrapper__`` metadata
needed: the decoder is pure over ``(file, node)``), plus :meth:`McosDecoder.property_value`
on the committed real-data fixture.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("h5py")
pytest.importorskip("numpy")

import h5py  # noqa: E402  (guarded by importorskip above)
import numpy as np  # noqa: E402

from tether.io.mcos import (  # noqa: E402
    McosDecoder,
    _decode_matlab_chars,
    _decode_matlab_value,
    object_reference_id,
)

FIXTURE = Path(__file__).parent / "fixtures" / "tdat_coloc_slice.tdat"


def _char(group: h5py.Group, name: str, text: str, *, pad: int = 0) -> h5py.Reference:
    """Write ``text`` as a MATLAB ``uint16`` char column vector; return its reference."""
    codes = [ord(c) for c in text] + [0] * pad
    ds = group.create_dataset(name, data=np.array(codes, dtype=np.uint16).reshape(-1, 1))
    return ds.ref


def _cell(group: h5py.Group, name: str, refs: list) -> h5py.Reference:
    """Write a MATLAB cell as an object-reference dataset over ``refs``; return its reference."""
    ds = group.create_dataset(name, shape=(len(refs), 1), dtype=h5py.ref_dtype)
    for i, ref in enumerate(refs):
        ds[i, 0] = ref if ref is not None else h5py.Reference()
    return ds.ref


@pytest.fixture
def h5(tmp_path: Path):
    """An open, writable HDF5 file with a ``#refs#`` staging group."""
    path = tmp_path / "synthetic.mat"
    with h5py.File(path, "w") as f:
        f.create_group("refs")
        yield f


# --- _decode_matlab_chars ----------------------------------------------------


def test_decode_chars_uint16(h5: h5py.File) -> None:
    codes = np.array([ord(c) for c in "movie.tif"], dtype=np.uint16)
    ds = h5["refs"].create_dataset("a", data=codes)
    assert _decode_matlab_chars(ds) == "movie.tif"


def test_decode_chars_uint8(h5: h5py.File) -> None:
    ds = h5["refs"].create_dataset("a", data=np.array([104, 105], dtype=np.uint8))  # "hi"
    assert _decode_matlab_chars(ds) == "hi"


def test_decode_chars_strips_nul_padding(h5: h5py.File) -> None:
    ds = h5["refs"].create_dataset("a", data=np.array([65, 66, 0, 0], dtype=np.uint16))  # "AB\0\0"
    assert _decode_matlab_chars(ds) == "AB"


def test_decode_chars_empty_matlab_string_is_none(h5: h5py.File) -> None:
    # MATLAB '' is stored as a small non-character dims marker (all zeros here).
    ds = h5["refs"].create_dataset("a", data=np.array([0, 0], dtype=np.uint64))
    assert _decode_matlab_chars(ds) is None


def test_decode_chars_numeric_leaf_is_none(h5: h5py.File) -> None:
    ds = h5["refs"].create_dataset("a", data=np.array([[1.0]], dtype=np.float64))
    assert _decode_matlab_chars(ds) is None


def test_decode_chars_object_ref_marker_is_none(h5: h5py.File) -> None:
    # An object-reference marker vector's first word (0xDD000000) exceeds the Unicode
    # range, so it must never be mistaken for text.
    ds = h5["refs"].create_dataset("a", data=np.array([0xDD000000, 2, 1, 1], dtype=np.uint32))
    assert _decode_matlab_chars(ds) is None


def test_decode_chars_negative_signed_is_none(h5: h5py.File) -> None:
    # A signed-int leaf with a negative code unit degrades to None -- chr(-1) would
    # otherwise raise ValueError; the range guard must bound the low end too.
    ds = h5["refs"].create_dataset("a", data=np.array([-1, 65], dtype=np.int16))
    assert _decode_matlab_chars(ds) is None


# --- _decode_matlab_value ----------------------------------------------------


def test_decode_value_char_dataset_to_str(h5: h5py.File) -> None:
    _char(h5["refs"], "s", "D:\\rig\\")
    assert _decode_matlab_value(h5, h5["refs"]["s"]) == "D:\\rig\\"


def test_decode_value_null_reference_is_none(h5: h5py.File) -> None:
    assert _decode_matlab_value(h5, h5py.Reference()) is None


def test_decode_value_group_is_none(h5: h5py.File) -> None:
    assert _decode_matlab_value(h5, h5["refs"]) is None


def test_decode_value_numeric_leaf_is_none(h5: h5py.File) -> None:
    ds = h5["refs"].create_dataset("n", data=np.array([[3.0]], dtype=np.float64))
    assert _decode_matlab_value(h5, ds) is None


def test_decode_value_flat_cell_of_strings(h5: h5py.File) -> None:
    a, b = _char(h5["refs"], "a", "one"), _char(h5["refs"], "b", "two")
    cell = h5["refs"].create_dataset("cell", shape=(2, 1), dtype=h5py.ref_dtype)
    cell[0, 0], cell[1, 0] = a, b
    assert _decode_matlab_value(h5, cell) == ["one", "two"]


def test_decode_value_nested_filepath_cell(h5: h5py.File) -> None:
    # The real FilePath shape: {dir, {movie}} -> ['dir', ['movie']].
    refs = h5["refs"]
    dir_ref = _char(refs, "dir", "D:\\data\\")
    file_ref = _char(refs, "file", "movie_010.tif")
    inner = _cell(refs, "inner", [file_ref])  # the MultiSelect 1-cell {movie}
    outer = _cell(refs, "outer", [dir_ref, inner])
    assert _decode_matlab_value(h5, refs["outer"]) == ["D:\\data\\", ["movie_010.tif"]]
    assert h5py.check_dtype(ref=refs["outer"].dtype) is not None  # sanity: it is a ref dataset
    del outer  # ref value unused beyond construction


def test_decode_value_cell_with_null_element(h5: h5py.File) -> None:
    a = _char(h5["refs"], "a", "kept")
    cell = h5["refs"].create_dataset("cell", shape=(2, 1), dtype=h5py.ref_dtype)
    cell[0, 0] = a  # cell[1] left as the default null reference
    assert _decode_matlab_value(h5, cell) == ["kept", None]


# --- McosDecoder.property_value (committed real-data fixture) -----------------


@pytest.fixture(scope="module")
def fixture_channels():
    """(decoder, [object_id, ...]) for the committed .tdat's MCOS Channel objects."""
    with h5py.File(FIXTURE, "r") as f:
        decoder = McosDecoder.from_file(f)
        assert decoder is not None
        oids = [
            object_reference_id(np.asarray(f[ref][()]).reshape(-1))
            for ref in np.asarray(f["temp"]["Channel"][()]).reshape(-1)
            if ref
        ]
        yield decoder, oids


def test_property_value_decodes_char_property(fixture_channels) -> None:
    decoder, oids = fixture_channels
    # The two channels are the donor 'G' and acceptor 'R' detection colours.
    colors = {decoder.property_value(oid, "ChannelColor") for oid in oids}
    assert colors == {"G", "R"}


def test_property_value_numeric_property_is_none(fixture_channels) -> None:
    decoder, oids = fixture_channels
    # ChannelID is a numeric scalar (use property_scalar for it), not a string.
    assert all(decoder.property_value(oid, "ChannelID") is None for oid in oids)


def test_property_value_absent_property_is_none(fixture_channels) -> None:
    decoder, oids = fixture_channels
    # FilePath is dropped when the fixture is slimmed, and a bogus name is unknown.
    assert all(decoder.property_value(oid, "FilePath") is None for oid in oids)
    assert all(decoder.property_value(oid, "NoSuchField") is None for oid in oids)

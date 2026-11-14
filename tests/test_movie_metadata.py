# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Movie metadata with its origin: frame time, pixel size, channel roles (#911).

Behavioural coverage of :mod:`tether.project.movie_metadata`, the extraction writer
that records origins, the :class:`~tether.io.movie.MovieReader` pixel-size read, and
the ``tether metadata`` command that gives the same values and confirmation.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("h5py")
pytest.importorskip("tifffile")

import h5py  # noqa: E402
import tifffile  # noqa: E402

from test_extract_cli import (  # noqa: E402, PLC2701 - the synthetic movie and stub map
    _N_FRAMES,
    _WINDOW,
    _make_movie,
    _stub_tmap,
    _synthetic_tmap_channels,
    _write_movie,
)
from tether.cli import main  # noqa: E402
from tether.io.movie import open_movie  # noqa: E402
from tether.project import movie_metadata as mm  # noqa: E402
from tether.project.core import Project  # noqa: E402
from tether.project.extract import ExtractOptions, extract_movie  # noqa: E402

_OPTIONS = ExtractOptions(window=_WINDOW)


def _imagej_movie(tmp_path, *, finterval=0.1, pixel_um=0.16, unit="um"):
    """The synthetic movie rewritten as an ImageJ TIFF with a frame time and pixel size."""
    plain = tmp_path / "plain.tif"
    _write_movie(plain)
    data = tifffile.imread(plain).astype(">u2")
    path = tmp_path / "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_011.tif"
    metadata = {"axes": "TYX", "unit": unit}
    if finterval is not None:
        metadata["finterval"] = finterval
    tifffile.imwrite(
        path, data, imagej=True, resolution=(1 / pixel_um, 1 / pixel_um), metadata=metadata
    )
    return path


def _only(path) -> mm.MovieMetadataRecord:
    (record,) = mm.read_movie_metadata(path).values()
    return record


def test_reader_reads_imagej_pixel_size(tmp_path) -> None:
    with open_movie(_imagej_movie(tmp_path)) as reader:
        assert reader.pixel_size_um == pytest.approx(0.16)
        assert reader.frame_time == pytest.approx(0.1)
        assert reader.shape[0] == _N_FRAMES


def test_reader_ignores_a_resolution_without_a_micrometre_unit(tmp_path) -> None:
    with open_movie(_imagej_movie(tmp_path, unit="pixel")) as reader:
        assert reader.pixel_size_um is None
    with open_movie(_make_movie(tmp_path)) as reader:  # plain TIFF: default 1/1 inch
        assert reader.pixel_size_um is None


def test_extraction_records_file_values_as_file_and_verified(tmp_path) -> None:
    out = tmp_path / "a.tether"
    extract_movie(_imagej_movie(tmp_path), out, options=_OPTIONS)

    record = _only(out)
    assert record.frame_time == pytest.approx(0.1)
    assert (record.frame_time_origin, record.frame_time_verified) == (mm.ORIGIN_FILE, True)
    assert (record.pixel_size_um, record.pixel_size_origin) == (pytest.approx(0.16), "file")
    assert record.channel_roles_origin == mm.ORIGIN_USER  # native: the donor_side option
    with h5py.File(out, "r") as f:  # additive data beside the frozen table
        assert record.movie_id in f["movies/metadata"]
        assert float(f["movies/table"][0]["frame_time"]) == pytest.approx(0.1)


def test_extraction_without_metadata_records_unknown(tmp_path) -> None:
    out = tmp_path / "b.tether"
    extract_movie(_make_movie(tmp_path), out, options=_OPTIONS)

    record = _only(out)
    assert record == mm.MovieMetadataRecord(record.movie_id, channel_roles_origin="user")


def test_an_imported_tmap_gives_channel_roles_from_the_file(tmp_path, monkeypatch) -> None:
    import tether.project.extract as ext

    monkeypatch.setattr(ext, "read_tmap", lambda _path: _synthetic_tmap_channels())
    out = tmp_path / "c.tether"
    extract_movie(_make_movie(tmp_path), out, tmap=_stub_tmap(tmp_path), options=_OPTIONS)

    assert _only(out).channel_roles_origin == mm.ORIGIN_FILE


def _legacy_store(tmp_path, frame_time: float):
    """A store as written before #911: a /movies row and no /movies/metadata group."""
    out = tmp_path / f"legacy-{frame_time}.tether"
    extract_movie(_make_movie(tmp_path), out, options=_OPTIONS)
    with h5py.File(out, "r+") as f:
        del f["movies/metadata"]
        table = f["movies/table"]
        row = table[0]
        row["frame_time"] = frame_time
        table[0] = row
    return out


def test_an_old_store_reads_unknown_unless_the_file_gave_the_frame_time(tmp_path) -> None:
    unknown = _only(_legacy_store(tmp_path, 0.0))
    assert (unknown.frame_time_origin, unknown.frame_time_verified) == ("unknown", False)
    assert (unknown.pixel_size_origin, unknown.channel_roles_origin) == ("unknown", "unknown")

    from_file = _only(_legacy_store(tmp_path, 0.05))
    assert from_file.frame_time == pytest.approx(0.05)
    assert (from_file.frame_time_origin, from_file.frame_time_verified) == ("file", True)


def test_a_given_frame_time_is_verified_only_by_the_separate_confirmation(tmp_path) -> None:
    out = tmp_path / "d.tether"
    extract_movie(_make_movie(tmp_path), out, options=_OPTIONS)
    project = Project.open(out)
    movie_id = _only(out).movie_id

    with pytest.raises(ValueError, match="no frame time to confirm"):
        project.confirm_frame_time(movie_id)
    given = project.set_frame_time(movie_id, 0.03)
    assert (given.frame_time, given.frame_time_origin, given.frame_time_verified) == (
        pytest.approx(0.03),
        "user",
        False,
    )
    assert project.confirm_frame_time(movie_id).frame_time_verified is True
    # A new value clears the confirmation; the frozen field keeps the file's 0.0.
    assert project.set_frame_time(movie_id, 0.04).frame_time_verified is False
    with h5py.File(out, "r") as f:
        assert float(f["movies/table"][0]["frame_time"]) == 0.0


def test_a_user_value_replaces_a_file_value_and_is_not_verified(tmp_path) -> None:
    out = tmp_path / "e.tether"
    extract_movie(_imagej_movie(tmp_path), out, options=_OPTIONS)
    movie_id = _only(out).movie_id

    record = mm.set_frame_time(out, movie_id, 0.2)
    assert (record.frame_time_origin, record.frame_time_verified) == ("user", False)
    record = mm.set_pixel_size(out, movie_id, 0.108)
    assert (record.pixel_size_um, record.pixel_size_origin) == (pytest.approx(0.108), "user")
    assert _only(out) == record


@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf")])
def test_a_given_value_must_be_finite_and_positive(tmp_path, bad) -> None:
    out = tmp_path / "f.tether"
    extract_movie(_make_movie(tmp_path), out, options=_OPTIONS)
    movie_id = _only(out).movie_id
    with pytest.raises(ValueError, match="finite positive"):
        mm.set_frame_time(out, movie_id, bad)
    with pytest.raises(ValueError, match="finite positive"):
        mm.set_pixel_size(out, movie_id, bad)
    with pytest.raises(KeyError):
        mm.set_frame_time(out, "mov-absent", 0.1)


def test_the_record_refuses_a_bad_origin_or_an_unknown_verified_time() -> None:
    with pytest.raises(ValueError, match="frame_time_origin"):
        mm.MovieMetadataRecord("m", frame_time_origin="guess")
    with pytest.raises(ValueError, match="cannot be verified"):
        mm.MovieMetadataRecord("m", frame_time_verified=True)


def test_cli_gives_the_same_values_and_the_same_confirmation(tmp_path, capsys) -> None:
    out = tmp_path / "g.tether"
    extract_movie(_make_movie(tmp_path), out, options=_OPTIONS)
    movie_id = _only(out).movie_id

    assert main(["metadata", str(out), "--frame-time", "0.05", "--pixel-size", "0.16"]) == 0
    assert "0.05 s (user, not verified)" in capsys.readouterr().out
    assert main(["metadata", str(out), "--movie", movie_id, "--confirm-frame-time"]) == 0
    capsys.readouterr()
    assert main(["metadata", str(out), "--json"]) == 0
    (printed,) = json.loads(capsys.readouterr().out)
    assert printed == _only(out).to_dict()
    assert printed["frame_time_verified"] is True
    assert printed["pixel_size_origin"] == "user"


def test_cli_refuses_a_given_and_confirmed_frame_time_in_one_call(tmp_path, capsys) -> None:
    out = tmp_path / "h.tether"
    extract_movie(_make_movie(tmp_path), out, options=_OPTIONS)
    args = ["metadata", str(out), "--frame-time", "0.05", "--confirm-frame-time"]
    assert main(args) == 2
    assert "separate action" in capsys.readouterr().err
    assert _only(out).frame_time_origin == "unknown"


def test_cli_reports_a_refused_change_with_code_1(tmp_path, capsys) -> None:
    out = tmp_path / "i.tether"
    extract_movie(_make_movie(tmp_path), out, options=_OPTIONS)
    assert main(["metadata", str(out), "--confirm-frame-time"]) == 1
    assert "no frame time to confirm" in capsys.readouterr().err
    assert main(["metadata", str(out), "--movie", "mov-absent", "--pixel-size", "1"]) == 1
    assert main(["metadata", str(tmp_path / "absent.tether")]) == 1
    assert _only(out).pixel_size_um == 0.0  # nothing was written


def test_cli_writes_nothing_when_any_requested_change_is_refused(tmp_path, capsys) -> None:
    out = tmp_path / "j.tether"
    extract_movie(_imagej_movie(tmp_path), out, options=_OPTIONS)
    before = _only(out)

    assert main(["metadata", str(out), "--frame-time", "0.2", "--pixel-size", "-1"]) == 1
    assert "finite positive" in capsys.readouterr().err
    assert _only(out) == before  # the valid frame time was not written either


def test_one_update_applies_every_change_or_none(tmp_path) -> None:
    out = tmp_path / "k.tether"
    extract_movie(_make_movie(tmp_path), out, options=_OPTIONS)
    movie_id = _only(out).movie_id

    with pytest.raises(ValueError, match="separate action"):
        mm.update_movie_metadata(out, movie_id, frame_time=0.1, confirm_frame_time=True)
    with pytest.raises(ValueError, match="no frame time to confirm"):
        mm.update_movie_metadata(out, movie_id, pixel_size_um=0.16, confirm_frame_time=True)
    assert _only(out).pixel_size_origin == "unknown"  # the refused call wrote nothing

    record = Project.open(out).update_movie_metadata(movie_id, frame_time=0.1, pixel_size_um=0.16)
    assert (record.frame_time_origin, record.pixel_size_origin) == ("user", "user")
    assert _only(out) == record

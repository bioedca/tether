# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The map file as a separate asset and its reuse check (#916, ADR-0146).

Acceptance criteria of #916, in order: the map file holds the asset record (format
version 2); one map file serves more than one project, each recording the map id and the
checksum; the reuse check refuses a different layout, geometry or pixel size and names the
difference, and a different optics text needs a confirmation; the date is never a reason;
a ``.tmap`` still reads and becomes an asset of origin ``tmap`` with no quality record; an
existing project opens unchanged and its map can be saved as a separate asset.
"""

from __future__ import annotations

import dataclasses
import hashlib
from dataclasses import replace

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("skimage")
pytest.importorskip("h5py")
tifffile = pytest.importorskip("tifffile")

import numpy as np  # noqa: E402

from tether.cli import main  # noqa: E402
from tether.imaging import calibrate  # noqa: E402
from tether.imaging import register as register_module  # noqa: E402
from tether.imaging.calibrate import MapFileError, read_calibration, save_map  # noqa: E402
from tether.imaging.layout import ChannelLayout  # noqa: E402
from tether.imaging.map_asset import (  # noqa: E402
    MAP_ORIGIN_BEADS,
    MAP_ORIGIN_NATIVE_FIT,
    MAP_ORIGIN_TMAP,
    MapAsset,
    MapFacts,
    MapReuseError,
    OpticsConfirmationNeeded,
    check_map_reuse,
    load_map_asset,
    map_asset_from_tmap,
    project_map_links,
    require_map_reuse,
    save_map_asset,
    save_project_map,
)
from tether.imaging.recipe import ChannelRecipe  # noqa: E402
from tether.imaging.split import ChannelGeometry  # noqa: E402
from tether.project.extract import ExtractionError, ExtractOptions, extract_movie  # noqa: E402

_BG = 80.0
_AMP = 400.0
_SIGMA = 1.5
_SHAPE = (64, 96)  # two 48-px halves
_WINDOW = 21
_DONOR = np.array([[12.0, 12.0], [24.0, 40.0], [16.0, 52.0]])
_ACCEPTOR = _DONOR + np.array([49.0, 0.0])
_SIDE_BY_SIDE = ChannelLayout()


def _frame() -> np.ndarray:
    frame = np.full(_SHAPE, _BG)
    rows, cols = np.mgrid[0 : _SHAPE[0], 0 : _SHAPE[1]]
    for x, y in np.vstack([_DONOR, _ACCEPTOR]):
        frame += _AMP * np.exp(-((rows - y) ** 2 + (cols - x) ** 2) / (2.0 * _SIGMA**2))
    return frame


def _bead_frame() -> np.ndarray:
    """A bead image: a 3 x 3 grid of beads in each half, the acceptor 1 px to the right."""
    frame = np.full(_SHAPE, _BG)
    rows, cols = np.mgrid[0 : _SHAPE[0], 0 : _SHAPE[1]]
    donor = [(x, y) for x in (10.0, 24.0, 38.0) for y in (12.0, 32.0, 52.0)]
    for x, y in donor + [(x + 49.0, y) for x, y in donor]:
        frame += _AMP * np.exp(-((rows - y) ** 2 + (cols - x) ** 2) / (2.0 * _SIGMA**2))
    return frame


def _write_movie(path, *, pixel_um: float | None = None, n_frames: int = 12):
    stack = np.ascontiguousarray(np.broadcast_to(_frame(), (n_frames, *_SHAPE)), dtype=">u2")
    if pixel_um is None:
        tifffile.imwrite(path, stack, photometric="minisblack", byteorder=">")
    else:
        tifffile.imwrite(
            path,
            stack,
            imagej=True,
            resolution=(1 / pixel_um, 1 / pixel_um),
            metadata={"axes": "TYX", "unit": "um"},
        )
    return path


@pytest.fixture
def fitted(tmp_path):
    """A map fitted on a calibration movie, as the project holds it."""
    out = tmp_path / "calibration.tether"
    summary = extract_movie(
        _write_movie(tmp_path / "calibration.tif"), out, options=ExtractOptions(window=_WINDOW)
    )
    return out, summary.calibration_id, read_calibration(out, summary.calibration_id)


def _asset(reg_map, **kwargs) -> MapAsset:
    kwargs.setdefault("channel_layout", _SIDE_BY_SIDE)
    return MapAsset.from_registration_map(reg_map, **kwargs)


def _bytes_sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- AC 1: the file holds the record (format version 2) ----------------------------


def test_the_map_file_holds_the_asset_record(tmp_path, fitted) -> None:
    _, _, reg_map = fitted
    layout = ChannelLayout(
        donor_recipe=ChannelRecipe((("flip", (0, 1)),)),
        acceptor_recipe=ChannelRecipe((("flip", (0, 1)),)),
    )
    quality = {"rms_residual": 0.1, "n_pairs": 3, "pair_residuals": [0.1, 0.1, 0.1]}
    asset = replace(
        MapAsset.from_registration_map(
            reg_map, channel_layout=layout, optics="60x TIRF, EMCCD 1x1", pixel_size_um=0.267
        ),
        source_ids=("sha256:" + "ab" * 32,),
        quality=quality,
    )
    path = save_map_asset(asset, tmp_path / "beads.npz")
    with np.load(path, allow_pickle=False) as data:
        assert int(data["format_version"]) == calibrate.MAP_FORMAT_VERSION == 2
    back = load_map_asset(path)
    assert back.map_id == asset.map_id and back.map_id.startswith("map:")
    assert back.origin == MAP_ORIGIN_NATIVE_FIT
    assert back.source_ids == asset.source_ids
    assert back.channel_layout == layout
    assert back.quality == quality
    assert back.optics == "60x TIRF, EMCCD 1x1"
    assert back.pixel_size_um == 0.267
    assert back.created_utc == asset.created_utc and back.created_utc.endswith("Z")
    assert back.app_version == asset.app_version
    assert back.geometry == (reg_map.reference_geometry, reg_map.moving_geometry)
    assert np.array_equal(back.registration_map.ref_to_moving.a, reg_map.ref_to_moving.a)


def test_a_bead_map_records_its_origin_source_and_quality(tmp_path) -> None:
    beads = tmp_path / "beads.tif"
    tifffile.imwrite(beads, _bead_frame().astype(np.uint16))
    out = tmp_path / "beads.npz"
    assert (
        main(
            [
                "map",
                "create",
                str(beads),
                "-o",
                str(out),
                "--optics",
                "60x",
                "--pixel-size",
                "0.267",
            ]
        )
        == 0
    )
    asset = load_map_asset(out)
    assert asset.origin == MAP_ORIGIN_BEADS
    assert asset.source_ids == (f"sha256:{_bytes_sha(beads)}",)
    assert asset.quality is not None and asset.quality["n_pairs"] >= 2
    assert asset.optics == "60x" and asset.pixel_size_um == 0.267
    assert asset.channel_layout == ChannelLayout(donor="left")
    # The recorded layout gives the geometry the map was fitted with.
    movie = MapFacts.for_movie(channel_layout=ChannelLayout(), frame_shape=_SHAPE)
    assert check_map_reuse(asset.facts(), movie).refusals == ()


def test_a_version_2_file_without_a_record_array_is_refused(tmp_path, fitted) -> None:
    _, _, reg_map = fitted
    path = save_map_asset(_asset(reg_map), tmp_path / "m.npz")
    with np.load(path, allow_pickle=False) as data:
        arrays = {k: data[k] for k in data.files if k != "optics"}
    np.savez(tmp_path / "no-optics.npz", **arrays)
    with pytest.raises(MapFileError, match=r"no-optics.npz is a version-2 map file that lacks"):
        load_map_asset(tmp_path / "no-optics.npz")


@pytest.mark.parametrize("version", [None, 1])
def test_an_older_map_file_reads_with_a_derived_record(tmp_path, fitted, version) -> None:
    _, _, reg_map = fitted
    arrays = calibrate._map_arrays(reg_map)
    if version is None:
        del arrays["format_version"]
    else:
        arrays["format_version"] = np.array(version, dtype=np.int64)
    path = tmp_path / "old.npz"
    np.savez(path, **arrays)
    asset = load_map_asset(path)
    assert asset.map_id == f"sha256:{_bytes_sha(path)}"
    assert asset.format_version == (0 if version is None else 1)
    assert asset.channel_layout is None and asset.optics == "" and asset.pixel_size_um is None
    assert asset.created_utc == ""
    # It still registers a movie, and the project names it by that id.
    out = tmp_path / "old-map.tether"
    summary = extract_movie(
        _write_movie(tmp_path / "movie.tif"),
        out,
        options=ExtractOptions(window=_WINDOW),
        map_path=path,
    )
    (link,) = project_map_links(out)
    assert link.calibration_id == summary.calibration_id
    assert (link.map_id, link.sha256) == (asset.map_id, _bytes_sha(path))


# --- AC 2: one map file, more than one project ---------------------------------------


def test_two_projects_record_the_same_map_id_and_checksum(tmp_path, fitted) -> None:
    _, _, reg_map = fitted
    map_file = save_map_asset(_asset(reg_map), tmp_path / "shared.npz")
    asset = load_map_asset(map_file)
    links = []
    for name in ("a", "b"):
        out = tmp_path / f"{name}.tether"
        extract_movie(
            _write_movie(tmp_path / f"{name}.tif"),
            out,
            options=ExtractOptions(window=_WINDOW),
            map_path=map_file,
        )
        links.extend(project_map_links(out))
    assert [(link.map_id, link.sha256, link.name) for link in links] == [
        (asset.map_id, _bytes_sha(map_file), "shared.npz")
    ] * 2


def test_a_native_fit_has_no_map_link(fitted) -> None:
    project, _, _ = fitted
    assert project_map_links(project) == []


# --- AC 3: the reuse check -----------------------------------------------------------


def _facts(**kwargs) -> MapFacts:
    base = {
        "channel_layout": _SIDE_BY_SIDE,
        "geometry": _SIDE_BY_SIDE.geometries(*_SHAPE),
        "pixel_size_um": 0.267,
        "optics": "60x TIRF",
    }
    base.update(kwargs)
    return MapFacts(**base)


def test_the_same_facts_pass_the_check() -> None:
    check = require_map_reuse(_facts(), _facts())
    assert check.refusals == check.warnings == check.not_compared == ()


def test_a_different_layout_is_refused_and_named() -> None:
    movie = _facts(
        channel_layout=ChannelLayout(kind="top-bottom"),
        geometry=_SIDE_BY_SIDE.geometries(*_SHAPE),
    )
    with pytest.raises(
        MapReuseError, match=r"channel layout: the map has side-by-side, donor left"
    ):
        require_map_reuse(_facts(), movie)


def test_a_different_geometry_is_refused_and_named() -> None:
    # The same layout on a wider frame gives other crops: a map of one camera size does
    # not fit a movie of another.
    movie = MapFacts.for_movie(
        channel_layout=_SIDE_BY_SIDE, frame_shape=(64, 128), pixel_size_um=0.267, optics="60x TIRF"
    )
    with pytest.raises(MapReuseError, match=r"channel geometry: the map has donor .*crop"):
        require_map_reuse(_facts(), movie)


def test_a_different_pixel_size_is_refused_and_both_values_are_named() -> None:
    with pytest.raises(
        MapReuseError, match=r"pixel size: the map has 0.267 um; the movie has 0.16"
    ):
        require_map_reuse(_facts(), _facts(pixel_size_um=0.16))


def test_every_difference_is_named_at_once() -> None:
    movie = MapFacts.for_movie(
        channel_layout=ChannelLayout(donor="right"), frame_shape=_SHAPE, pixel_size_um=0.16
    )
    check = check_map_reuse(_facts(), movie)
    assert [r.split(":")[0] for r in check.refusals] == [
        "channel layout",
        "channel geometry",
        "pixel size",
    ]


def test_a_different_optics_text_needs_a_confirmation() -> None:
    movie = _facts(optics="100x TIRF")
    check = check_map_reuse(_facts(), movie)
    assert not check.refused and check.warnings
    with pytest.raises(OpticsConfirmationNeeded, match=r"the map says '60x TIRF'.*'100x TIRF'"):
        require_map_reuse(_facts(), movie)
    assert require_map_reuse(_facts(), movie, confirm_optics=True).warnings == check.warnings
    # White space at either end is not a difference.
    assert check_map_reuse(_facts(), _facts(optics="  60x TIRF \n")).warnings == ()


def test_a_value_one_side_does_not_know_is_not_compared() -> None:
    check = check_map_reuse(_facts(), MapFacts())
    assert check.refusals == check.warnings == ()
    assert check.not_compared == ("channel layout", "channel geometry", "pixel size", "optics")


# --- AC 4: the date is never a reason ------------------------------------------------


def test_the_date_of_a_map_is_never_a_reason(fitted) -> None:
    _, _, reg_map = fitted
    old = _asset(
        reg_map, optics="60x TIRF", pixel_size_um=0.267, created_utc="1999-01-01T00:00:00Z"
    )
    new = replace(old, created_utc="2099-12-31T23:59:59Z")
    unknown = replace(old, created_utc="")
    for movie in (
        MapFacts.for_movie(channel_layout=_SIDE_BY_SIDE, frame_shape=_SHAPE, pixel_size_um=0.267),
        MapFacts.for_movie(pixel_size_um=0.16, optics="100x"),
    ):
        results = {check_map_reuse(a.facts(), movie) for a in (old, new, unknown)}
        assert len(results) == 1
    # The facts the check reads hold no date at all.
    assert {f.name for f in dataclasses.fields(MapFacts)} == {
        "channel_layout",
        "geometry",
        "pixel_size_um",
        "optics",
    }


# --- the check at extraction ---------------------------------------------------------


def test_extraction_refuses_a_map_of_another_pixel_size(tmp_path, fitted) -> None:
    _, _, reg_map = fitted
    map_file = save_map_asset(_asset(reg_map, pixel_size_um=0.267), tmp_path / "m.npz")
    out = tmp_path / "refused.tether"
    with pytest.raises(
        ExtractionError, match=r"pixel size: the map has 0.267 um; the movie has 0.16"
    ):
        extract_movie(
            _write_movie(tmp_path / "px.tif", pixel_um=0.16),
            out,
            options=ExtractOptions(window=_WINDOW),
            map_path=map_file,
        )
    assert not out.exists()


def test_extraction_with_other_optics_needs_confirm_optics(tmp_path, fitted, capsys) -> None:
    _, _, reg_map = fitted
    map_file = save_map_asset(_asset(reg_map, optics="60x TIRF"), tmp_path / "m.npz")
    movie = _write_movie(tmp_path / "movie.tif")
    out = tmp_path / "optics.tether"
    argv = ["extract", str(movie), "-o", str(out), "--map", str(map_file), "--window", "21"]
    assert main([*argv, "--optics", "100x TIRF"]) == 1
    assert "--confirm-optics" in capsys.readouterr().err
    assert not out.exists()
    assert main([*argv, "--optics", "100x TIRF", "--confirm-optics"]) == 0
    (link,) = project_map_links(out)
    record = read_calibration(out, link.calibration_id).provenance["map_file"]
    assert record["movie_optics"] == "100x TIRF"
    assert record["reuse"]["optics_confirmed"] is True
    assert "pixel size" in record["reuse"]["not_compared"]


def test_extraction_records_a_passed_check(tmp_path, fitted) -> None:
    _, _, reg_map = fitted
    map_file = save_map_asset(
        _asset(reg_map, optics="60x TIRF", pixel_size_um=0.16), tmp_path / "m.npz"
    )
    out = tmp_path / "ok.tether"
    summary = extract_movie(
        _write_movie(tmp_path / "movie.tif", pixel_um=0.16),
        out,
        options=ExtractOptions(window=_WINDOW),
        map_path=map_file,
        map_optics="60x TIRF",
    )
    record = read_calibration(out, summary.calibration_id).provenance["map_file"]
    assert record["reuse"] == {
        "refusals": [],
        "warnings": [],
        "not_compared": ["channel layout", "channel geometry"],
        "optics_confirmed": False,
    }


# --- AC 5: a .tmap still reads -------------------------------------------------------


def test_a_tmap_becomes_an_asset_of_origin_tmap_with_no_quality(monkeypatch, tmp_path) -> None:
    from test_extract_preview import _tmap_channels

    tmap = tmp_path / "run.tmap"
    tmap.write_bytes(b"a stand-in for the .tmap bytes")
    monkeypatch.setattr(register_module, "read_tmap", lambda path: _tmap_channels())
    asset = map_asset_from_tmap(tmap, optics="60x")
    assert asset.origin == MAP_ORIGIN_TMAP
    assert asset.quality is None
    assert asset.source_ids == (f"sha256:{_bytes_sha(tmap)}",)
    back = load_map_asset(save_map_asset(asset, tmp_path / "from-tmap.npz"))
    assert (back.origin, back.quality, back.optics) == (MAP_ORIGIN_TMAP, None, "60x")
    assert back.geometry == (
        ChannelGeometry(crop=(1, 1, 64, 48)),
        ChannelGeometry(crop=(1, 49, 64, 96)),
    )


# --- AC 6: an existing project opens unchanged, and its map can be saved -------------


def test_a_projects_map_is_saved_as_a_separate_asset_and_the_project_is_unchanged(
    tmp_path, fitted
) -> None:
    project, calibration_id, reg_map = fitted
    before = _bytes_sha(project)
    out = tmp_path / "from-project.npz"
    asset = save_project_map(project, calibration_id, out, optics="60x", pixel_size_um=0.267)
    assert _bytes_sha(project) == before
    back = load_map_asset(out)
    assert back.map_id == asset.map_id and back.origin == MAP_ORIGIN_NATIVE_FIT
    assert np.array_equal(back.registration_map.moving_to_ref.b, reg_map.moving_to_ref.b)
    with pytest.raises(FileExistsError):
        save_project_map(project, calibration_id, out)
    # A name np.savez would extend is refused, so no other file is replaced (Codex P2).
    written = _bytes_sha(out)
    with pytest.raises(ValueError, match="must end in .npz"):
        save_project_map(project, calibration_id, tmp_path / "from-project")
    assert _bytes_sha(out) == written
    # A later movie extracts with it.
    later = tmp_path / "later.tether"
    extract_movie(
        _write_movie(tmp_path / "later.tif"),
        later,
        options=ExtractOptions(window=_WINDOW),
        map_path=out,
    )
    assert project_map_links(later)[0].map_id == asset.map_id
    # A map the project took from a map file keeps that file's id, layout, optics and
    # pixel size when saved again, so the copy is checked as the original was (Codex P1).
    later_id = project_map_links(later)[0].calibration_id
    again = save_project_map(later, later_id, tmp_path / "x.npz")
    assert again.map_id == asset.map_id
    assert (again.optics, again.pixel_size_um) == ("60x", 0.267)
    copy = load_map_asset(tmp_path / "x.npz")
    assert (copy.optics, copy.pixel_size_um) == ("60x", 0.267)
    # An explicit value still overrides the recorded one.
    other = save_project_map(later, later_id, tmp_path / "y.npz", optics="100x")
    assert (other.optics, other.pixel_size_um) == ("100x", 0.267)


def test_save_map_still_writes_a_full_asset(tmp_path, fitted) -> None:
    _, _, reg_map = fitted
    asset = load_map_asset(save_map(reg_map, tmp_path / "plain.npz"))
    assert asset.map_id.startswith("map:") and asset.origin == MAP_ORIGIN_NATIVE_FIT
    assert asset.channel_layout is None and asset.created_utc.endswith("Z")

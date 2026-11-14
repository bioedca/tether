# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Channel layouts: side by side, top and bottom, free rectangles (#908).

The acceptance criteria of #908, one block each:

* the layout record supports the three kinds and names the donor and the acceptor;
* a layout whose rectangles differ in size, or lie outside the frame, is refused with
  a message, and overlapping rectangles give a warning;
* the default layout is the split Tether used before #908 (the existing extraction
  tests and frozen numbers are untouched; here the crops are pinned directly);
* a top-and-bottom movie made by stacking the two halves of the public tutorial movie
  (``docs/tutorial/data/tutorial_movie.tif``, side by side) gives traces equal to the
  side-by-side extraction;
* the layout is stored for each movie as additive data, and the frozen manifest does
  not change.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("skimage")
pytest.importorskip("h5py")
pytest.importorskip("tifffile")

import h5py  # noqa: E402
import numpy as np  # noqa: E402
import tifffile  # noqa: E402

from tether.imaging.extract import MovieMetadata, read_molecules, read_traces  # noqa: E402
from tether.imaging.layout import (  # noqa: E402
    DEFAULT_CHANNEL_LAYOUT,
    LAYOUT_KINDS,
    LAYOUTS_GROUP,
    ChannelLayout,
    ChannelLayoutError,
    ChannelOverlapWarning,
    check_layout_slot,
    read_layout_record,
    write_layout_record,
)
from tether.io.schema import build_manifest, create_project  # noqa: E402
from tether.project.extract import (  # noqa: E402
    ExtractionError,
    ExtractOptions,
    extract_movie,
    resolve_channel_layout,
)

_REPO = Path(__file__).resolve().parents[1]
#: The public side-by-side movie: first-party synthetic output of
#: ``scripts/make_tutorial_movie.py``, served on the docs site (PROVENANCE.md).
_TUTORIAL_MOVIE = _REPO / "docs" / "tutorial" / "data" / "tutorial_movie.tif"
_GOLDEN = _REPO / "schema" / "schema_frozen.json"


# --- the record ---------------------------------------------------------------


def test_the_three_kinds_name_the_donor_and_the_acceptor() -> None:
    assert LAYOUT_KINDS == ("side-by-side", "top-bottom", "rectangles")

    side = ChannelLayout()
    assert (side.kind, side.donor, side.acceptor) == ("side-by-side", "left", "right")
    assert ChannelLayout(donor="right").acceptor == "left"

    top = ChannelLayout(kind="top-bottom")
    assert (top.donor, top.acceptor) == ("top", "bottom")
    assert ChannelLayout(kind="top-bottom", donor="bottom").acceptor == "top"

    rects = ChannelLayout(kind="rectangles", donor_rect=[1, 1, 8, 8], acceptor_rect=(1, 9, 8, 16))
    assert rects.donor_rect == (1, 1, 8, 8)
    assert rects.acceptor_rect == (1, 9, 8, 16)
    assert rects.donor is None and rects.acceptor is None


@pytest.mark.parametrize(
    "layout",
    [
        ChannelLayout(),
        ChannelLayout(donor="right"),
        ChannelLayout(kind="top-bottom", donor="bottom"),
        ChannelLayout(kind="rectangles", donor_rect=(2, 3, 9, 10), acceptor_rect=(20, 3, 27, 10)),
    ],
    ids=["side-left", "side-right", "top-bottom", "rectangles"],
)
def test_the_record_round_trips_through_its_dict(layout: ChannelLayout) -> None:
    data = layout.to_dict()
    assert data["acceptor"] == layout.acceptor  # stated, so a reader needs no rule
    assert ChannelLayout.from_dict(data) == layout


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"kind": "diagonal"}, "kind must be one of"),
        ({"donor": "top"}, "must be one of ('left', 'right')"),
        ({"kind": "top-bottom", "donor": "left"}, "must be one of ('top', 'bottom')"),
        ({"donor_rect": (1, 1, 4, 4), "acceptor_rect": (1, 5, 4, 8)}, "only to a 'rectangles'"),
        ({"kind": "rectangles", "donor_rect": (1, 1, 4, 4)}, "needs both"),
        (
            {"kind": "rectangles", "donor": "left", "donor_rect": (1, 1, 4, 4)},
            "donor must be None",
        ),
        (
            {"kind": "rectangles", "donor_rect": (1, 1, 4), "acceptor_rect": (1, 5, 4, 8)},
            "4 values",
        ),
        (
            {"kind": "rectangles", "donor_rect": (1, 1, 4.5, 4), "acceptor_rect": (1, 5, 4, 8)},
            "must be integers",
        ),
        (
            {"kind": "rectangles", "donor_rect": (0, 1, 4, 4), "acceptor_rect": (1, 5, 4, 8)},
            "1-based",
        ),
        (
            {"kind": "rectangles", "donor_rect": (4, 1, 1, 4), "acceptor_rect": (1, 5, 4, 8)},
            "y2 >= y1",
        ),
    ],
)
def test_a_malformed_layout_is_refused_with_a_message(kwargs, message) -> None:
    with pytest.raises(ChannelLayoutError, match=message.replace("(", r"\(").replace(")", r"\)")):
        ChannelLayout(**kwargs)


def test_rectangles_of_different_size_are_refused_with_both_sizes() -> None:
    with pytest.raises(ChannelLayoutError, match="same size") as info:
        ChannelLayout(kind="rectangles", donor_rect=(1, 1, 10, 10), acceptor_rect=(1, 11, 10, 21))
    assert "10x10" in str(info.value) and "10x11" in str(info.value)


@pytest.mark.parametrize(
    ("donor_rect", "acceptor_rect", "name"),
    [
        ((1, 1, 32, 40), (1, 41, 32, 80), "donor_rect"),  # 32 rows > the 31-row frame
        ((1, 1, 31, 40), (1, 42, 31, 81), "acceptor_rect"),  # column 81 > 80
    ],
)
def test_a_rectangle_outside_the_frame_is_refused(donor_rect, acceptor_rect, name) -> None:
    layout = ChannelLayout(kind="rectangles", donor_rect=donor_rect, acceptor_rect=acceptor_rect)
    with pytest.raises(ChannelLayoutError, match=f"{name} .* is outside the 31x80 frame"):
        layout.geometries(31, 80)


def test_a_frame_too_small_to_cut_in_two_is_refused() -> None:
    with pytest.raises(ChannelLayoutError, match="too narrow"):
        ChannelLayout().geometries(10, 1)
    with pytest.raises(ChannelLayoutError, match="too short"):
        ChannelLayout(kind="top-bottom").geometries(1, 10)


def test_overlapping_rectangles_warn_and_still_split() -> None:
    layout = ChannelLayout(
        kind="rectangles", donor_rect=(1, 1, 10, 10), acceptor_rect=(5, 5, 14, 14)
    )
    with pytest.warns(ChannelOverlapWarning, match="overlap"):
        donor, acceptor = layout.geometries(20, 20)
    assert donor.crop == (1, 1, 10, 10)
    assert acceptor.crop == (5, 5, 14, 14)


def test_rectangles_that_only_touch_do_not_warn() -> None:
    layout = ChannelLayout(
        kind="rectangles", donor_rect=(1, 1, 10, 10), acceptor_rect=(1, 11, 10, 20)
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error", ChannelOverlapWarning)
        layout.geometries(10, 20)


# --- the default is the pre-#908 split ------------------------------------------


@pytest.mark.parametrize(("height", "width"), [(40, 80), (33, 81)])
def test_the_default_layout_is_the_split_before_908(height: int, width: int) -> None:
    """The exact crops ``_half_split_geometry`` built before #908, odd column dropped."""
    half = width // 2
    left, right = (1, 1, height, half), (1, half + 1, height, 2 * half)

    assert ExtractOptions().channel_layout is None
    assert resolve_channel_layout(ExtractOptions()) == DEFAULT_CHANNEL_LAYOUT
    donor, acceptor = DEFAULT_CHANNEL_LAYOUT.geometries(height, width)
    assert (donor.crop, acceptor.crop) == (left, right)
    assert (donor.rotation_deg, donor.flip) == (0, (0, 0))

    swapped = resolve_channel_layout(ExtractOptions(donor_side="right"))
    donor, acceptor = swapped.geometries(height, width)
    assert (donor.crop, acceptor.crop) == (right, left)


def test_top_bottom_halves_drop_an_odd_trailing_row() -> None:
    donor, acceptor = ChannelLayout(kind="top-bottom").geometries(41, 30)
    assert (donor.crop, acceptor.crop) == ((1, 1, 20, 30), (21, 1, 40, 30))
    donor, acceptor = ChannelLayout(kind="top-bottom", donor="bottom").geometries(41, 30)
    assert (donor.crop, acceptor.crop) == ((21, 1, 40, 30), (1, 1, 20, 30))


def test_options_refuse_a_layout_beside_a_non_default_donor_side() -> None:
    with pytest.raises(ExtractionError, match="donor_side applies only"):
        ExtractOptions(donor_side="right", channel_layout=ChannelLayout(kind="top-bottom"))
    with pytest.raises(ExtractionError, match="must be a tether.imaging.layout.ChannelLayout"):
        ExtractOptions(channel_layout="top-bottom")  # type: ignore[arg-type]


# --- extraction with each layout -------------------------------------------------


def _write_be(path: Path, stack: np.ndarray) -> Path:
    tifffile.imwrite(
        path, np.ascontiguousarray(stack, dtype=">u2"), photometric="minisblack", byteorder=">"
    )
    return path


def _tutorial_halves() -> tuple[np.ndarray, np.ndarray]:
    movie = tifffile.imread(_TUTORIAL_MOVIE)
    half = movie.shape[2] // 2
    return movie[:, :, :half], movie[:, :, half : 2 * half]


@pytest.fixture(scope="module")
def side_by_side(tmp_path_factory) -> dict[str, np.ndarray]:
    out = tmp_path_factory.mktemp("sbs") / "side_by_side.tether"
    summary = extract_movie(_TUTORIAL_MOVIE, out)
    assert summary.n_molecules > 0  # a vacuous equality would prove nothing
    return read_traces(out)


def _assert_same_traces(expected: dict[str, np.ndarray], actual: dict[str, np.ndarray]) -> None:
    assert set(actual) == set(expected)
    for name in expected:
        np.testing.assert_array_equal(actual[name], expected[name], err_msg=name)


def test_a_stacked_top_bottom_movie_gives_the_side_by_side_traces(tmp_path, side_by_side) -> None:
    """Acceptance: the halves stacked (not rotated) give equal traces (#908)."""
    donor, acceptor = _tutorial_halves()
    movie = _write_be(tmp_path / "stacked.tif", np.concatenate([donor, acceptor], axis=1))
    out = tmp_path / "stacked.tether"
    layout = ChannelLayout(kind="top-bottom")

    extract_movie(movie, out, options=ExtractOptions(channel_layout=layout))

    _assert_same_traces(side_by_side, read_traces(out))
    movie_id = read_molecules(out)["movie_id"][0].decode("utf-8")
    assert read_layout_record(out, movie_id) == layout


def test_a_donor_on_the_bottom_reads_the_bottom_half(tmp_path, side_by_side) -> None:
    donor, acceptor = _tutorial_halves()
    movie = _write_be(tmp_path / "flipped.tif", np.concatenate([acceptor, donor], axis=1))
    out = tmp_path / "flipped.tether"

    extract_movie(
        movie,
        out,
        options=ExtractOptions(channel_layout=ChannelLayout(kind="top-bottom", donor="bottom")),
    )

    _assert_same_traces(side_by_side, read_traces(out))


def test_free_rectangles_inside_a_padded_frame_give_the_same_traces(tmp_path, side_by_side) -> None:
    """Two rectangles at arbitrary places in a larger frame read the same two channels."""
    donor, acceptor = _tutorial_halves()
    n_frames, height, width = donor.shape
    frame = np.full((n_frames, height + 13, 2 * width + 9), 7, dtype=np.uint16)
    frame[:, 5 : 5 + height, 2 : 2 + width] = acceptor  # acceptor first, on purpose
    frame[:, 11 : 11 + height, width + 8 : 2 * width + 8] = donor
    movie = _write_be(tmp_path / "padded.tif", frame)
    out = tmp_path / "padded.tether"
    layout = ChannelLayout(
        kind="rectangles",
        donor_rect=(12, width + 9, 11 + height, 2 * width + 8),
        acceptor_rect=(6, 3, 5 + height, 2 + width),
    )

    extract_movie(movie, out, options=ExtractOptions(channel_layout=layout))

    _assert_same_traces(side_by_side, read_traces(out))
    with h5py.File(out, "r") as f:
        row = f["movies/table"][0]
    assert tuple(row["donor_crop"]) == layout.donor_rect
    assert tuple(row["acceptor_crop"]) == layout.acceptor_rect


def test_a_rectangle_outside_the_movie_is_a_clean_extraction_error(tmp_path) -> None:
    out = tmp_path / "never.tether"
    layout = ChannelLayout(
        kind="rectangles", donor_rect=(1, 1, 40, 40), acceptor_rect=(1, 60, 40, 99)
    )
    with pytest.raises(ExtractionError, match="acceptor_rect .* is outside the 40x80 frame"):
        extract_movie(_TUTORIAL_MOVIE, out, options=ExtractOptions(channel_layout=layout))
    assert not out.exists()


def test_a_layout_beside_a_tmap_is_refused(tmp_path) -> None:
    tmap = tmp_path / "map.tmap"
    tmap.write_bytes(b"")  # never read: the refusal comes first
    with pytest.raises(ExtractionError, match="channel_layout and tmap"):
        extract_movie(
            _TUTORIAL_MOVIE,
            tmp_path / "never.tether",
            options=ExtractOptions(channel_layout=ChannelLayout(kind="top-bottom")),
            tmap=tmap,
        )


# --- storage -----------------------------------------------------------------------


def test_a_default_extraction_stores_the_default_layout(tmp_path) -> None:
    out = tmp_path / "default.tether"
    extract_movie(_TUTORIAL_MOVIE, out)
    movie_id = read_molecules(out)["movie_id"][0].decode("utf-8")

    assert read_layout_record(out, movie_id) == DEFAULT_CHANNEL_LAYOUT
    with h5py.File(out, "r") as f:
        attrs = dict(f[f"movies/{LAYOUTS_GROUP}/{movie_id}"].attrs)
    assert attrs["version"] == 1
    assert {k: v for k, v in attrs.items() if k != "version"} == {
        "kind": "side-by-side",
        "donor": "left",
        "acceptor": "right",
    }


def test_the_rectangles_record_stores_both_rectangles(tmp_path) -> None:
    path = create_project(tmp_path / "p.tether")
    layout = ChannelLayout(kind="rectangles", donor_rect=(1, 2, 3, 4), acceptor_rect=(5, 6, 7, 8))
    with h5py.File(path, "r+") as f:
        write_layout_record(f["movies"], "mov-1", layout)
    with h5py.File(path, "r") as f:
        attrs = f[f"movies/{LAYOUTS_GROUP}/mov-1"].attrs
        assert attrs["donor"] == "" and attrs["acceptor"] == ""
        assert attrs["donor_rect"].tolist() == [1, 2, 3, 4]
        assert attrs["acceptor_rect"].tolist() == [5, 6, 7, 8]
    assert read_layout_record(path, "mov-1") == layout
    assert read_layout_record(path, "mov-absent") is None


def test_a_store_without_records_reads_none(tmp_path) -> None:
    path = create_project(tmp_path / "old.tether")
    assert read_layout_record(path, "mov-1") is None


def test_a_record_is_write_once_and_needs_a_safe_name(tmp_path) -> None:
    path = create_project(tmp_path / "p.tether")
    with h5py.File(path, "r+") as f:
        movies = f["movies"]
        write_layout_record(movies, "mov-1", ChannelLayout())
        with pytest.raises(ValueError, match="already has a layout record"):
            write_layout_record(movies, "mov-1", ChannelLayout(kind="top-bottom"))
        for bad in ("", "a/b", ".", ".."):
            with pytest.raises(ValueError, match="cannot name a layout record"):
                check_layout_slot(movies, bad)
    assert read_layout_record(path, "mov-1") == ChannelLayout()


def test_a_newer_record_version_is_refused(tmp_path) -> None:
    path = create_project(tmp_path / "p.tether")
    with h5py.File(path, "r+") as f:
        write_layout_record(f["movies"], "mov-1", ChannelLayout())
        f[f"movies/{LAYOUTS_GROUP}/mov-1"].attrs["version"] = 2
    with pytest.raises(ValueError, match="version 2"):
        read_layout_record(path, "mov-1")


def test_the_frozen_manifest_does_not_carry_the_layout_group() -> None:
    """Additive data: a fresh store has no ``/movies/layouts``, so the golden is unchanged."""
    import json  # noqa: PLC0415

    manifest = build_manifest()
    assert "/movies/layouts" not in manifest["groups"]
    assert manifest == json.loads(_GOLDEN.read_text(encoding="utf-8"))


def test_movie_metadata_refuses_a_layout_that_is_not_a_record() -> None:
    """A wrong type fails before ``write_extraction`` can append the ``/movies`` row."""
    with pytest.raises(TypeError, match="channel_layout must be a ChannelLayout or None"):
        MovieMetadata(
            movie_id="mov-1",
            sha256="0" * 64,
            n_frames=1,
            height=2,
            width=2,
            channel_layout={"kind": "top-bottom"},  # type: ignore[arg-type]
        )

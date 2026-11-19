# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Channel geometry recipes: rotate, flip, translate and crop for each channel (#910).

The acceptance criteria of #910, one block each:

* a recipe is an ordered list of rotate (90-degree steps), flip, translate (whole
  pixels) and crop for each channel, and it is stored with the movie;
* the identity recipe changes nothing (the existing extraction tests and frozen numbers
  are untouched; here the geometry and the bytes are pinned directly);
* each recipe can be inverted: a coordinate that goes to the view and back is the same
  coordinate (a seeded property test over random recipes, frames and points);
* a ``.tmap`` per-channel rotation or flip is a recipe and is applied, not refused (the
  #559 acceptance tests in ``tests/test_extract_cli.py`` and ``tests/test_split.py``
  still pass; here the recipe is pinned against ``processImage``);
* a movie whose frames do not agree with its map is refused, naming both geometries;
* no operation resamples a pixel value (the set of values before and after).

The movies are the public tutorial movie (``docs/tutorial/data/tutorial_movie.tif``,
first-party synthetic output of ``scripts/make_tutorial_movie.py``) and synthetic frames.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("skimage")
pytest.importorskip("h5py")
pytest.importorskip("tifffile")

import numpy as np  # noqa: E402
import tifffile  # noqa: E402

from tether.imaging.calibrate import fit_registration_map, save_map  # noqa: E402
from tether.imaging.extract import read_molecules, read_traces  # noqa: E402
from tether.imaging.layout import (  # noqa: E402
    ChannelLayout,
    ChannelLayoutError,
    read_layout_record,
)
from tether.imaging.recipe import (  # noqa: E402
    ChannelRecipe,
    ChannelRecipeError,
    check_frame_fits,
)
from tether.imaging.split import ChannelGeometry  # noqa: E402
from tether.project.extract import ExtractionError, ExtractOptions, extract_movie  # noqa: E402
from tether.services.profile import ExtractionProfile  # noqa: E402

_REPO = Path(__file__).resolve().parents[1]
_TUTORIAL_MOVIE = _REPO / "docs" / "tutorial" / "data" / "tutorial_movie.tif"


def _random_recipe(rng: np.random.Generator, frame: tuple[int, int]) -> ChannelRecipe:
    """A random valid recipe: rotations, flips, and moves each followed by a crop that fits."""
    steps: list = []
    for _ in range(int(rng.integers(1, 6))):
        kind = rng.choice(["rotate", "flip", "translate", "crop"])
        if kind == "rotate":
            steps.append(("rotate", int(rng.choice([0, 90, 180, 270]))))
        elif kind == "flip":
            steps.append(("flip", (int(rng.integers(2)), int(rng.integers(2)))))
        else:
            recipe = ChannelRecipe(tuple(steps))
            h, w = recipe.view_shape(frame)
            dy = int(rng.integers(-(h // 3), h // 3 + 1)) if kind == "translate" else 0
            dx = int(rng.integers(-(w // 3), w // 3 + 1)) if kind == "translate" else 0
            # The part the move keeps, then a random crop inside it.
            top, left = max(0, dy), max(0, dx)
            bottom, right = h - max(0, -dy), w - max(0, -dx)
            y1 = int(rng.integers(top, bottom)) + 1
            x1 = int(rng.integers(left, right)) + 1
            y2 = int(rng.integers(y1, bottom + 1))
            x2 = int(rng.integers(x1, right + 1))
            if kind == "translate":
                steps.append(("translate", (dy, dx)))
            steps.append(("crop", (y1, x1, y2, x2)))
    return ChannelRecipe(tuple(steps))


# --- the recipe record ------------------------------------------------------------


def test_a_recipe_is_an_ordered_list_of_the_four_operations() -> None:
    recipe = ChannelRecipe(
        (("translate", [2, -1]), ("crop", (3, 1, 7, 10)), ("rotate", 90), ("flip", (0, 1)))
    )
    assert [name for name, _ in recipe.steps] == ["translate", "crop", "rotate", "flip"]
    assert str(recipe) == "translate 2 -1, crop 3 1 7 10, rotate 90, flip h"
    assert ChannelRecipe.parse(str(recipe)) == recipe
    assert ChannelRecipe.parse("none") == ChannelRecipe() == ChannelRecipe.parse("")
    assert str(ChannelRecipe()) == "none"
    # The order matters: the same steps in another order are another recipe.
    image = np.arange(7 * 11).reshape(7, 11)
    a = ChannelRecipe((("rotate", 90), ("flip", (1, 0))))
    b = ChannelRecipe((("flip", (1, 0)), ("rotate", 90)))
    assert not np.array_equal(a.apply(image), b.apply(image))


@pytest.mark.parametrize(
    ("steps", "message"),
    [
        ((("spin", 90),), "unknown recipe operation"),
        ((("rotate", 45),), "rotate must be one of"),
        ((("rotate", 90.5),), "whole numbers"),
        ((("flip", (2, 0)),), "each be 0 or 1"),
        ((("translate", (1.5, 0)),), "whole numbers"),
        ((("translate", (True, 0)),), "whole numbers"),
        ((("crop", (0, 1, 4, 4)),), "1-based"),
        ((("crop", (1, 1, 4)),), "needs 4 whole numbers"),
        (("rotate",), r"\(name, value\) pair"),
    ],
)
def test_a_malformed_recipe_is_refused_with_a_message(steps, message) -> None:
    with pytest.raises(ChannelRecipeError, match=message):
        ChannelRecipe(steps)


@pytest.mark.parametrize(
    "steps",
    [
        (("flip", (0, 0)),),
        (("rotate", 0),),
        (("translate", (0, 0)), ("flip", (1, 1)), ("rotate", 270)),
    ],
)
def test_identity_steps_survive_the_text_form_and_the_record(steps, tmp_path) -> None:
    """Every accepted recipe reads back, identity steps included (Codex on PR #1280)."""
    import h5py  # noqa: PLC0415

    from tether.imaging.layout import write_layout_record  # noqa: PLC0415
    from tether.io.schema import create_project  # noqa: PLC0415

    recipe = ChannelRecipe(steps)
    assert ChannelRecipe.parse(str(recipe)) == recipe
    layout = ChannelLayout(acceptor_recipe=recipe)
    assert ChannelLayout.from_dict(layout.to_dict()) == layout
    path = create_project(tmp_path / "p.tether")
    with h5py.File(path, "r+") as f:
        write_layout_record(f["movies"], "mov-1", layout)
    assert read_layout_record(path, "mov-1") == layout


def test_the_text_form_refuses_a_bad_flip_or_number() -> None:
    with pytest.raises(ChannelRecipeError, match="v, h, vh or none"):
        ChannelRecipe.parse("flip x")
    with pytest.raises(ChannelRecipeError, match="whole numbers"):
        ChannelRecipe.parse("translate 1 a")


@pytest.mark.parametrize(
    ("steps", "message"),
    [
        ((("crop", (1, 1, 8, 3)),), "outside the 7 x 11 px frame"),
        ((("translate", (1, 0)),), "no source pixel"),
        ((("translate", (0, -2)), ("crop", (1, 1, 7, 10))), "no source pixel"),
        ((("translate", (20, 0)),), "out of the frame"),
    ],
)
def test_a_recipe_that_would_invent_pixels_is_refused(steps, message) -> None:
    image = np.arange(7 * 11).reshape(7, 11)
    with pytest.raises(ChannelRecipeError, match=message):
        ChannelRecipe(steps).apply(image)
    with pytest.raises(ChannelRecipeError, match=message):
        ChannelRecipe(steps).view_shape(image.shape)


def test_a_move_then_a_crop_inside_the_moved_image_is_the_shifted_window() -> None:
    image = np.arange(7 * 11).reshape(7, 11)
    recipe = ChannelRecipe((("translate", (2, -1)), ("crop", (3, 1, 7, 10))))
    np.testing.assert_array_equal(recipe.apply(image), image[0:5, 1:11])


# --- the identity recipe -----------------------------------------------------------


@pytest.mark.parametrize("shape", [(7, 11), (3, 7, 11)])
def test_the_identity_recipe_gives_the_same_bytes(shape) -> None:
    image = np.arange(int(np.prod(shape)), dtype=">u2").reshape(shape)
    out = ChannelRecipe().apply(image)
    assert out.dtype == image.dtype
    assert out.tobytes() == image.tobytes()


def test_a_layout_with_identity_recipes_gives_the_geometry_before_910() -> None:
    plain = ChannelLayout().geometries(64, 96)
    identity = ChannelLayout(donor_recipe=ChannelRecipe(), acceptor_recipe=ChannelRecipe())
    assert identity.geometries(64, 96) == plain
    assert plain == (ChannelGeometry(crop=(1, 1, 64, 48)), ChannelGeometry(crop=(1, 49, 64, 96)))


# --- the property test: invertible, and the mapping agrees with the pixels ----------


def test_every_recipe_can_be_inverted_and_maps_each_pixel_to_its_value() -> None:
    """Seeded property test over 400 random recipes, frames and sub-pixel points."""
    rng = np.random.default_rng(910)
    for _ in range(400):
        frame = (int(rng.integers(2, 20)), int(rng.integers(2, 20)))
        recipe = _random_recipe(rng, frame)
        image = rng.permutation(frame[0] * frame[1]).reshape(frame)
        view = recipe.apply(image)
        assert view.shape == recipe.view_shape(frame)

        # A coordinate that goes to the view and back is the same coordinate.
        points = rng.uniform(-5.0, 25.0, size=(16, 2))
        back = recipe.to_original(recipe.to_view(points, frame), frame)
        np.testing.assert_allclose(back, points, atol=1e-9, err_msg=str(recipe))
        view_points = rng.uniform(-5.0, 25.0, size=(16, 2))
        again = recipe.to_view(recipe.to_original(view_points, frame), frame)
        np.testing.assert_allclose(again, view_points, atol=1e-9, err_msg=str(recipe))

        # Each view pixel is the original pixel its coordinate maps back to.
        rows, cols = np.mgrid[0 : view.shape[0], 0 : view.shape[1]]
        xy = np.column_stack([cols.ravel(), rows.ravel()]).astype(float)
        src = np.rint(recipe.to_original(xy, frame)).astype(int)
        np.testing.assert_array_equal(image[src[:, 1], src[:, 0]], view.ravel(), str(recipe))

        # The stored rotate -> flip -> crop geometry gives the same pixels.
        np.testing.assert_array_equal(recipe.canonical(frame).apply(image), view, str(recipe))


# --- no resampling -----------------------------------------------------------------


def test_no_operation_resamples_a_pixel_value() -> None:
    rng = np.random.default_rng(1910)
    image = rng.integers(0, 2**16, size=(4, 17, 23), dtype=np.uint16)
    values = set(np.unique(image).tolist())
    for _ in range(200):
        view = _random_recipe(rng, image.shape[1:]).apply(image)
        assert view.dtype == image.dtype
        assert set(np.unique(view).tolist()) <= values
    # Rotations and flips alone keep every pixel: the same multiset of values.
    for rotation, flip in itertools.product((0, 90, 180, 270), ((0, 0), (1, 0), (0, 1), (1, 1))):
        view = ChannelRecipe((("rotate", rotation), ("flip", flip))).apply(image)
        np.testing.assert_array_equal(np.sort(view, axis=None), np.sort(image, axis=None))


# --- the .tmap geometry is a recipe --------------------------------------------------


@pytest.mark.parametrize(
    ("rotation", "flip"), list(itertools.product((0, 90, 180, 270), ((0, 0), (1, 0), (0, 1))))
)
def test_a_tmap_rotation_and_flip_become_a_recipe_with_the_same_pixels(rotation, flip) -> None:
    """The ``.tmap`` geometry (#559, ``processImage`` order) maps to a recipe and back."""
    image = np.arange(3 * 9 * 14).reshape(3, 9, 14)
    geometry = ChannelGeometry(crop=(2, 3, 6, 7), rotation_deg=rotation, flip=flip)
    recipe = ChannelRecipe.from_geometry(geometry)
    np.testing.assert_array_equal(recipe.apply(image), geometry.apply(image))
    canonical = recipe.canonical(image.shape[1:])
    np.testing.assert_array_equal(canonical.apply(image), geometry.apply(image))
    if rotation in (0, 90):  # a pair has two spellings; these come back as stored
        assert canonical == geometry


# --- the geometry check of a map --------------------------------------------------------


def test_a_movie_rotated_against_its_map_is_refused_naming_both_geometries() -> None:
    donor = ChannelGeometry(crop=(1, 1, 64, 48))
    acceptor = ChannelGeometry(crop=(1, 49, 64, 96), rotation_deg=0, flip=(0, 1))
    check_frame_fits(64, 96, donor, acceptor)  # the map's own orientation fits
    with pytest.raises(ChannelRecipeError) as info:
        check_frame_fits(96, 64, donor, acceptor)  # the same movie turned by 90 degrees
    message = str(info.value)
    assert "96 x 64 px" in message
    assert "donor: crop 1 1 64 48" in message
    assert "acceptor: flip h, crop 1 49 64 96" in message
    assert "rotated against its map" in message


def test_extraction_refuses_a_movie_rotated_against_its_map_file(tmp_path) -> None:
    rng = np.random.default_rng(887)
    points = rng.uniform(5.0, 40.0, size=(8, 2))
    reg_map = fit_registration_map(
        points,
        points + 0.25,
        reference_channel=1,
        moving_channel=2,
        reference_geometry=ChannelGeometry(crop=(1, 1, 64, 48)),
        moving_geometry=ChannelGeometry(crop=(1, 49, 64, 96)),
    )
    map_file = save_map(reg_map, tmp_path / "map.npz")
    turned = np.ascontiguousarray(rng.integers(50, 500, size=(4, 96, 64)), dtype=">u2")
    movie = tmp_path / "turned.tif"
    tifffile.imwrite(movie, turned, photometric="minisblack", byteorder=">")
    with pytest.raises(ExtractionError, match=r"turned\.tif: the movie's frames \(96 x 64 px"):
        extract_movie(movie, tmp_path / "turned.tether", map_path=map_file)
    assert not (tmp_path / "turned.tether").exists()


# --- a layout carries a recipe for each channel -------------------------------------------


def test_a_layout_folds_its_cut_and_recipe_into_one_geometry() -> None:
    layout = ChannelLayout(acceptor_recipe=ChannelRecipe((("rotate", 180),)))
    donor, acceptor = layout.geometries(64, 96)
    assert donor == ChannelGeometry(crop=(1, 1, 64, 48))
    assert acceptor == ChannelGeometry(crop=(1, 1, 64, 48), rotation_deg=180)
    frame = np.arange(64 * 96).reshape(64, 96)
    np.testing.assert_array_equal(acceptor.apply(frame), np.rot90(frame[:, 48:], 2))


def test_a_layout_whose_recipes_give_different_sizes_is_refused() -> None:
    layout = ChannelLayout(acceptor_recipe=ChannelRecipe((("rotate", 90),)))
    with pytest.raises(ChannelLayoutError, match="same size") as info:
        layout.geometries(64, 96)
    assert "donor 64x48" in str(info.value) and "acceptor 48x64 (rotate 90)" in str(info.value)
    with pytest.raises(ChannelLayoutError, match="acceptor recipe does not fit"):
        ChannelLayout(acceptor_recipe=ChannelRecipe((("crop", (1, 1, 65, 48)),))).geometries(64, 96)
    with pytest.raises(ChannelLayoutError, match="must be a ChannelRecipe"):
        ChannelLayout(donor_recipe="rotate 90")  # type: ignore[arg-type]


def test_the_layout_dict_round_trips_its_recipes() -> None:
    layout = ChannelLayout(
        kind="rectangles",
        donor_rect=(1, 1, 10, 10),
        acceptor_rect=(1, 11, 10, 23),
        acceptor_recipe=ChannelRecipe((("translate", (0, -3)), ("crop", (1, 1, 10, 10)))),
    )
    data = layout.to_dict()
    assert data["donor_recipe"] is None
    assert data["acceptor_recipe"] == "translate 0 -3, crop 1 1 10 10"
    assert ChannelLayout.from_dict(data) == layout


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


def test_a_rotated_acceptor_half_turned_back_gives_the_side_by_side_traces(
    tmp_path, side_by_side
) -> None:
    """A channel stored rotated by 180 degrees reads the same traces with its recipe."""
    donor, acceptor = _tutorial_halves()
    turned = np.concatenate([donor, np.rot90(acceptor, 2, axes=(1, 2))], axis=2)
    movie = _write_be(tmp_path / "turned.tif", turned)
    out = tmp_path / "turned.tether"
    layout = ChannelLayout(acceptor_recipe=ChannelRecipe((("rotate", 180),)))

    extract_movie(movie, out, options=ExtractOptions(channel_layout=layout))

    _assert_same_traces(side_by_side, read_traces(out))
    movie_id = read_molecules(out)["movie_id"][0].decode("utf-8")
    assert read_layout_record(out, movie_id) == layout  # stored with the movie


def test_a_moved_and_flipped_acceptor_with_its_recipe_gives_the_same_traces(
    tmp_path, side_by_side
) -> None:
    """Translate + crop + flip: the acceptor sits 3 px further right and mirrored."""
    donor, acceptor = _tutorial_halves()
    n_frames, height, width = donor.shape
    frame = np.full((n_frames, height, 2 * width + 3), 7, dtype=np.uint16)
    frame[:, :, :width] = donor
    frame[:, :, width + 3 :] = acceptor[:, :, ::-1]
    movie = _write_be(tmp_path / "moved.tif", frame)
    out = tmp_path / "moved.tether"
    layout = ChannelLayout(
        kind="rectangles",
        donor_rect=(1, 1, height, width),
        acceptor_rect=(1, width + 1, height, 2 * width + 3),
        acceptor_recipe=ChannelRecipe(
            (("translate", (0, -3)), ("crop", (1, 1, height, width)), ("flip", (0, 1)))
        ),
    )

    extract_movie(movie, out, options=ExtractOptions(channel_layout=layout))

    _assert_same_traces(side_by_side, read_traces(out))


def test_a_layout_without_recipes_keeps_record_version_1(tmp_path) -> None:
    import h5py  # noqa: PLC0415

    out = tmp_path / "plain.tether"
    extract_movie(_TUTORIAL_MOVIE, out, options=ExtractOptions(channel_layout=ChannelLayout()))
    movie_id = read_molecules(out)["movie_id"][0].decode("utf-8")
    with h5py.File(out, "r") as f:
        attrs = f[f"movies/layouts/{movie_id}"].attrs
        assert int(attrs["version"]) == 1
        assert "donor_recipe" not in attrs


def test_a_profile_refuses_a_layout_with_a_recipe() -> None:
    layout = ChannelLayout(acceptor_recipe=ChannelRecipe((("rotate", 180),)))
    with pytest.raises(ExtractionError, match="cannot hold a channel recipe"):
        ExtractionProfile.from_options(ExtractOptions(channel_layout=layout))

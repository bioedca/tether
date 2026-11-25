# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The SIF movie adapter (#904, ADR-0150): values, row order, metadata, refusals.

The adapter conformance module runs every interface test on a synthetic 64-frame movie of
``tests/_sif_synth.py``. This module adds what is specific to SIF:

* two committed files that Andor Solis wrote (Zenodo 18293266, CC-BY-4.0): a spectrum of raw
  counts, compared bit for bit with Solis's own ASCII export of it and with its reference
  TIFF export by sif_parser 0.3.6, and a background-corrected spectrum, which is refused;
* the synthetic writer's variants: each refusal, the binning, the older layouts;
* a public Andor Zyla image (Zenodo 4904806, CC0): its frame equals, bit for bit, the
  reference digest of sif_parser 0.3.6, and its row order is that of the TIFF that Solis
  exported from it. These tests download the files, so they run in the weekly
  reference-data job (``reference_download``) and skip elsewhere.
"""

from __future__ import annotations

import hashlib
import json
import threading
import tracemalloc
from pathlib import Path

import pytest

pytest.importorskip("numpy")

import numpy as np  # noqa: E402

from _sif_synth import sif_bytes, write_sif  # noqa: E402
from reference_data.fetch import path_for  # noqa: E402
from tether.io.adapters import (  # noqa: E402
    UnsupportedContainerError,
    open_movie_source,
    select_adapter,
)
from tether.io.sif import SifMovie  # noqa: E402

SIF = Path(__file__).resolve().parent / "fixtures" / "sif"


def _frames(n: int = 4, h: int = 6, w: int = 10, seed: int = 904) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 16000, size=(n, h, w)).astype("<f4")


# --- the committed Solis files ------------------------------------------------------------


def _solis_ascii(path: Path) -> np.ndarray:
    """The counts column of a Solis ASCII export (tab-separated, decimal comma)."""
    rows = [line.split("\t") for line in path.read_text("latin-1").splitlines() if "\t" in line]
    return np.array([float(r[1].replace(",", ".")) for r in rows], dtype="<f4")


def test_a_solis_spectrum_equals_the_solis_ascii_export_bit_for_bit() -> None:
    with open_movie_source(SIF / "ZP2270-1.sif") as source:
        assert source.pixel_type == "<f4"
        assert source.shape == (1, 1, 1024)
        got = source.read_frames(0, 1)
    assert got.reshape(-1).tobytes() == _solis_ascii(SIF / "ZP2270-1.asc").tobytes()


def test_a_solis_spectrum_equals_its_reference_tiff_bit_for_bit() -> None:
    import tifffile

    reference = tifffile.imread(SIF / "ZP2270-1_reference.tif").reshape(1, 1, 1024)
    with open_movie_source(SIF / "ZP2270-1.sif") as source:
        got = source.read_frames(0, source.n_frames)
    assert got.dtype == reference.dtype
    assert got.tobytes() == reference.tobytes()


def test_a_one_frame_file_has_no_frame_time_and_no_pixel_size() -> None:
    with open_movie_source(SIF / "ZP2270-1.sif") as source:
        meta = source.metadata
    assert {meta.frame_time.origin, meta.pixel_size.origin, meta.channels.origin} == {"unknown"}


def test_a_background_corrected_file_is_refused() -> None:
    with pytest.raises(UnsupportedContainerError, match=r"'Counts \(Bg Corrected\)'"):
        open_movie_source(SIF / "ZP1-1.sif")


def test_a_solis_file_cut_short_by_its_flag_line_is_refused_not_read_early(
    tmp_path: Path,
) -> None:
    """A copy short by two bytes, the length of the ``0`` flag line, is refused (#904 review).

    Without the version rule, a start before the flag line made the frames end at the end of
    the file, and the frame was read two bytes early.
    """
    data = (SIF / "ZP2270-1.sif").read_bytes()
    path = tmp_path / "short.sif"
    path.write_bytes(data[: 2836 + 1024 * 4])
    assert len(data) - path.stat().st_size > 2, "the cut removes the trailing block flags too"
    with pytest.raises(UnsupportedContainerError, match="shorter than the 1 frames"):
        SifMovie(path)


# --- values, row order and metadata -------------------------------------------------------


def test_frames_are_returned_top_row_first(tmp_path: Path) -> None:
    frames = _frames()
    path = write_sif(tmp_path / "m.sif", frames)
    with SifMovie(path) as movie:
        np.testing.assert_array_equal(movie.read_frames(0, 4), frames)
    # The file itself stores the bottom row of each frame first.
    data = path.read_bytes()
    stored = np.ascontiguousarray(frames[:, ::-1, :]).tobytes()
    assert stored in data
    assert np.ascontiguousarray(frames).tobytes() not in data


def test_the_kinetic_cycle_time_is_the_frame_time(tmp_path: Path) -> None:
    path = write_sif(tmp_path / "m.sif", _frames(), kinetic_cycle=0.03035)
    with SifMovie(path) as movie:
        meta = movie.metadata
    assert (meta.frame_time.value, meta.frame_time.origin) == (0.03035, "file")
    assert meta.pixel_size.origin == "unknown"
    assert meta.channels.origin == "unknown"


@pytest.mark.parametrize("cycle", [0.0, -1.0, float("nan"), float("inf")])
def test_a_kinetic_cycle_time_that_is_not_finite_and_positive_is_unknown(
    tmp_path: Path, cycle: float
) -> None:
    path = write_sif(tmp_path / "m.sif", _frames(), kinetic_cycle=cycle)
    with SifMovie(path) as movie:
        assert movie.metadata.frame_time.origin == "unknown"


def test_binned_frames_have_the_binned_size(tmp_path: Path) -> None:
    frames = _frames(3, 4, 6)
    path = write_sif(tmp_path / "m.sif", frames, xbin=2, ybin=4)
    with SifMovie(path) as movie:
        assert movie.shape == (3, 4, 6)
        np.testing.assert_array_equal(movie.read_frames(0, 3), frames)


@pytest.mark.parametrize(
    "variant",
    [
        {"stamp_flag": b"0"},  # a flag line of 0, as in the public spectra
        {"version": 65559, "stamp_flag": b""},  # no flag line, as in format version 65559
        {"structure_version": 65538},  # the older image-structure record
        {"after": b""},  # the frames end the file
        {"after": b"0\n1\n65567 0 0 1"},  # a background block follows
    ],
)
def test_the_layouts_of_the_solis_versions_read_the_same_frames(
    tmp_path: Path, variant: dict
) -> None:
    frames = _frames()
    path = write_sif(tmp_path / "m.sif", frames, **variant)
    with SifMovie(path) as movie:
        np.testing.assert_array_equal(movie.read_frames(0, 4), frames)


def test_a_record_lookalike_in_the_user_text_is_not_the_layout(tmp_path: Path) -> None:
    frames = _frames()
    fake = b"65541 1 2 2 1 9 1 36 4\n65538 1 2 2 1 1 1 0\n"
    path = write_sif(tmp_path / "m.sif", frames, user_text=fake)
    with SifMovie(path) as movie:
        assert movie.shape == (4, 6, 10)
        np.testing.assert_array_equal(movie.read_frames(0, 4), frames)


def test_concurrent_reads_return_their_own_frames(tmp_path: Path) -> None:
    frames = _frames(32, 8, 8)
    path = write_sif(tmp_path / "m.sif", frames)
    errors: list[str] = []
    with SifMovie(path) as movie:

        def read(i: int) -> None:
            for _ in range(20):
                if not np.array_equal(movie.read_frames(i, i + 1), frames[i : i + 1]):
                    errors.append(f"frame {i}")

        threads = [threading.Thread(target=read, args=(i,)) for i in range(0, 32, 4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert not errors


def test_the_repr_names_the_file_and_its_shape(tmp_path: Path) -> None:
    path = write_sif(tmp_path / "m.sif", _frames())
    with SifMovie(path) as movie:
        assert repr(movie) == "SifMovie('m.sif', frames=4, height=6, width=10, dtype='<f4')"


def test_extraction_does_not_read_sif_yet(tmp_path: Path) -> None:
    from tether.io.frames import open_extraction_movie
    from tether.io.movie import EXTRACTED_CONTAINERS

    assert "SIF" not in EXTRACTED_CONTAINERS
    path = write_sif(tmp_path / "m.sif", _frames())
    with (
        pytest.raises(UnsupportedContainerError, match="extraction does not read SIF yet"),
        open_extraction_movie(path),
    ):
        pass


def test_the_registry_chooses_sif_by_its_header(tmp_path: Path) -> None:
    path = tmp_path / "movie.dat"
    path.write_bytes(sif_bytes(_frames()))
    assert select_adapter(path).name == "SIF"
    named = tmp_path / "movie.sif"
    named.write_bytes(b"not a SIF file at all")
    with pytest.raises(UnsupportedContainerError, match="the name says SIF"):
        select_adapter(named)


# --- refusals ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("variant", "message"),
    [
        ({"signal": 0}, "holds no signal frames"),
        ({"sub_images": 2}, "holds 2 image areas in each frame"),
        ({"data_type": b"Counts per second"}, "'Counts per second'"),
        ({"truncate": 5}, "shorter than the 4 frames"),
        ({"after": b"XX"}, "do not end where its header says"),
        ({"stamp_flag": b"7"}, "no flag line after its frame time stamps"),
        ({"stamp_flag": b""}, r"no flag line .* format version \(65567\)"),
        ({"version": 65559}, "do not end where its header says"),
        ({"version": 65563, "stamp_flag": b""}, "format version 65563, whose layout"),
        ({"stamp_flag": b"0", "truncate": 2}, "shorter than the 4 frames"),
    ],
)
def test_a_file_that_is_not_one_whole_movie_of_counts_is_refused(
    tmp_path: Path, variant: dict, message: str
) -> None:
    path = write_sif(tmp_path / "m.sif", _frames(), **variant)
    with pytest.raises(UnsupportedContainerError, match=message):
        SifMovie(path)


def test_an_image_area_that_does_not_divide_by_its_binning_is_refused(tmp_path: Path) -> None:
    data = sif_bytes(_frames(), xbin=2)
    # Make the area one pixel wider, so that its width (21) does not divide by 2.
    data = data.replace(b"65538 1 6 20 1 1 2 0\n", b"65538 1 6 21 1 1 2 0\n")
    path = tmp_path / "m.sif"
    path.write_bytes(data)
    with pytest.raises(UnsupportedContainerError, match="does not divide by its binning"):
        SifMovie(path)


def test_frame_lengths_that_do_not_match_the_area_are_refused(tmp_path: Path) -> None:
    data = sif_bytes(_frames()).replace(b" 4 1 240 60\n", b" 4 1 240 61\n")
    path = tmp_path / "m.sif"
    path.write_bytes(data)
    with pytest.raises(UnsupportedContainerError, match="frame lengths that do not match"):
        SifMovie(path)


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        (lambda b: b[:60], "damaged header"),
        (lambda b: b.replace(b"65538 1\n", b"12345 1\n", 1), "damaged header"),
        (lambda b: b.replace(b"65567 0 0 1 1700000000", b"65567 0 0 1 when", 1), "damaged header"),
        (lambda b: b.replace(b"12\nPixel number65541", b"12\nPixel number99999", 1), "cannot find"),
        (lambda b: b.replace(b"       0\n", b"     abc\n", 1), "time stamps"),
    ],
)
def test_a_damaged_header_is_refused(tmp_path: Path, damage, message: str) -> None:
    path = tmp_path / "m.sif"
    path.write_bytes(damage(sif_bytes(_frames())))
    with pytest.raises(UnsupportedContainerError, match=message):
        SifMovie(path)


def test_a_header_that_declares_too_many_frames_is_refused_without_a_large_read(
    tmp_path: Path,
) -> None:
    """A count of ten million frames in a 32 MiB file is refused at once (#904 review).

    The open once read up to 64 bytes for each declared frame, here the whole file, before
    it refused the file.
    """
    data = sif_bytes(_frames())
    record = b"65541 1 6 10 1 4 1 240 60\n"
    assert data.count(record) == 1
    path = tmp_path / "m.sif"
    path.write_bytes(data.replace(record, b"65541 1 6 10 1 10000000 1 600000000 60\n"))
    with path.open("r+b") as fh:
        fh.truncate(32 << 20)  # a large file, so that the size of the file does not bound a read
    tracemalloc.start()
    try:
        with pytest.raises(UnsupportedContainerError) as err:
            SifMovie(path)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 4 << 20, f"the refusal allocated {peak} B"
    assert "shorter than the 10000000 frames" in str(err.value)


def test_a_file_that_shrinks_after_it_opened_is_refused_not_read_in_part(tmp_path: Path) -> None:
    frames = _frames()
    path = write_sif(tmp_path / "m.sif", frames, after=b"")
    with SifMovie(path) as movie:
        path.write_bytes(path.read_bytes()[:-200])
        with pytest.raises(UnsupportedContainerError, match="ended inside frame 3"):
            movie.read_frames(0, 4)


def test_a_failed_open_closes_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    opened = []
    real_open = Path.open

    def spy(self: Path, *args, **kwargs):
        fh = real_open(self, *args, **kwargs)
        opened.append(fh)
        return fh

    monkeypatch.setattr(Path, "open", spy)
    path = write_sif(tmp_path / "m.sif", _frames(), signal=0)
    with pytest.raises(UnsupportedContainerError):
        SifMovie(path)
    assert opened and all(fh.closed for fh in opened)


# --- the public Andor Zyla image (weekly reference-data job) ---------------------------------


def _reference() -> dict:
    return json.loads((SIF / "reference.json").read_text("utf-8"))


@pytest.mark.reference_download
def test_the_public_zyla_image_equals_its_reference_bit_for_bit() -> None:
    ref = _reference()["c739xMagFRET_1.sif"]
    with open_movie_source(path_for("wu-magfret-c739-sif")) as source:
        got = source.read_frames(0, source.n_frames)
        meta = source.metadata
    assert list(got.shape) == ref["shape"]
    assert hashlib.sha256(got.tobytes()).hexdigest() == ref["pixel_sha256"]
    assert {meta.frame_time.origin, meta.pixel_size.origin, meta.channels.origin} == {"unknown"}


@pytest.mark.reference_download
def test_the_row_order_is_that_of_the_tiff_that_solis_exported() -> None:
    """Solis exported the image as an 8-bit TIFF: a linear scale of the counts, rounded.

    Tether's frame maps onto it within one grey level; the stored row order does not.
    """
    import tifffile

    exported = tifffile.imread(path_for("wu-magfret-c739-solis-tiff")).astype(np.float64)
    with open_movie_source(path_for("wu-magfret-c739-sif")) as source:
        frame = source.read_frames(0, 1)[0].astype(np.float64)

    def worst(counts: np.ndarray) -> float:
        design = np.column_stack([counts.ravel(), np.ones(counts.size)])
        coef, *_ = np.linalg.lstsq(design, exported.ravel(), rcond=None)
        predicted = np.clip(np.round(design @ coef), 0, 255)
        return float(np.abs(predicted - exported.ravel()).max())

    assert worst(frame) <= 1.0
    assert worst(frame[::-1]) > 50.0

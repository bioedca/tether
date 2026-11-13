# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""``extract_movie`` options tested by their effect on the result (#734).

The structural tests in ``test_extract_cli.py`` use three spots 26 px or more
apart, so every minimum separation gives the same molecules there, and an option
that the pipeline drops still passes. The movie here has one pair of donor spots
**7 px apart**: a 3 px minimum separation keeps both, an 8 px one keeps only the
brighter. That makes ``ExtractOptions.min_separation`` (and each detection mode's
own default for it, ADR-0022) visible in the molecule count.

The same file tests the guard that refuses to publish when the movie file changes
during the extraction (``extract_movie``: "movie changed during extraction").

Findings IMG-F6 (pipeline half), IMG-F8 and IMG-F12 of the 2026-11-11 test audit
(#695). The detectors themselves are tested in ``test_detect.py``.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

# numpy, h5py and tifffile are locked base dependencies: import them directly, so
# that a broken environment fails here instead of skipping the file (#726).
import h5py
import numpy as np
import pytest
import tifffile

import tether.project.extract as extract_module
from tether.imaging.extract import read_molecules
from tether.project.extract import ExtractionError, ExtractOptions, extract_movie

_SHAPE = (64, 96)  # two 48-px-wide halves: donor | acceptor
_HALF_WIDTH = 48
_BG = 80.0
_AMP = 400.0
_SIGMA = 0.8  # compact spots, so the 7 px pair stays two spots at 3 px
_N_FRAMES = 12

# Donor spots as (x, y, relative amplitude). The first two are the 7 px pair; the
# last two are isolated and keep the registration well posed in every case.
_CLOSE_BRIGHT = (14.0, 14.0, 1.0)
_CLOSE_DIM = (21.0, 14.0, 0.9)
_ISOLATED = ((30.0, 40.0, 1.0), (16.0, 50.0, 1.0))
_DONOR_SPOTS = (_CLOSE_BRIGHT, _CLOSE_DIM, *_ISOLATED)
_ACCEPTOR_DX = 1.0  # the acceptor copy sits +1 px in x inside its own half


def _write_movie(path: Path) -> Path:
    """Write a static dual-channel big-endian ``uint16`` movie (donor | acceptor)."""
    rows, cols = np.mgrid[0 : _SHAPE[0], 0 : _SHAPE[1]]
    frame = np.full(_SHAPE, _BG, dtype=np.float64)
    for x, y, rel in _DONOR_SPOTS:
        for cx in (x, x + _HALF_WIDTH + _ACCEPTOR_DX):
            frame += rel * _AMP * np.exp(-((rows - y) ** 2 + (cols - cx) ** 2) / (2 * _SIGMA**2))
    stack = np.broadcast_to(frame, (_N_FRAMES, *_SHAPE))
    tifffile.imwrite(
        path, np.ascontiguousarray(stack, dtype=">u2"), photometric="minisblack", byteorder=">"
    )
    return path


@pytest.fixture
def movie(tmp_path: Path) -> Path:
    return _write_movie(tmp_path / "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif")


def _donor_positions(project: Path) -> set[tuple[int, int]]:
    return {tuple(int(v) for v in np.round(xy)) for xy in read_molecules(project)["donor_xy"]}


def _recorded_min_separation(project: Path) -> float | None:
    with h5py.File(project, "r") as f:
        raw = f["/settings/extraction"].attrs["profile_json"]
    text = raw.decode() if isinstance(raw, bytes | bytearray) else str(raw)
    return json.loads(text)["min_separation"]


_ALL_FOUR = {(14, 14), (21, 14), (30, 40), (16, 50)}
_WITHOUT_DIM = _ALL_FOUR - {(21, 14)}


# --- IMG-F6: each mode's own default reaches the pipeline ---------------------


@pytest.mark.parametrize(
    ("mode", "threshold", "expected"),
    [
        # ADR-0022: intensity/bandpass default to 3 px, so the 7 px pair survives.
        ("intensity", None, _ALL_FOUR),
        # band-pass needs an explicit threshold: its 0.98 default zeroes the dim spot
        # before any separation applies (a threshold effect, not the one under test).
        ("bandpass", 0.5, _ALL_FOUR),
        # wavelet keeps its own 8 px default and suppresses the dimmer spot of the pair.
        ("wavelet", None, _WITHOUT_DIM),
    ],
)
def test_default_min_separation_is_each_modes_own(
    tmp_path: Path, movie: Path, mode: str, threshold: float | None, expected: set
) -> None:
    out = tmp_path / f"{mode}.tether"
    summary = extract_movie(
        movie, out, options=ExtractOptions(detection_mode=mode, detection_threshold=threshold)
    )
    assert summary.n_molecules == len(expected)
    assert _donor_positions(out) == expected
    assert _recorded_min_separation(out) is None  # unset -> recorded as JSON null


# --- IMG-F8: ExtractOptions.min_separation reaches both detectors --------------


@pytest.mark.parametrize(
    ("mode", "threshold", "min_separation", "expected"),
    [
        # An explicit value overrides the mode's default in both directions.
        ("intensity", None, 8.0, _WITHOUT_DIM),  # default 3 -> 8 drops the dim spot
        ("intensity", None, 3.0, _ALL_FOUR),
        ("bandpass", 0.5, 8.0, _WITHOUT_DIM),
        ("wavelet", None, 3.0, _ALL_FOUR),  # default 8 -> 3 keeps the pair
        ("wavelet", None, 8.0, _WITHOUT_DIM),
    ],
)
def test_explicit_min_separation_changes_the_molecules(
    tmp_path: Path,
    movie: Path,
    mode: str,
    threshold: float | None,
    min_separation: float,
    expected: set,
) -> None:
    out = tmp_path / f"{mode}-{min_separation}.tether"
    options = ExtractOptions(
        detection_mode=mode, detection_threshold=threshold, min_separation=min_separation
    )
    summary = extract_movie(movie, out, options=options)
    assert summary.n_molecules == len(expected)
    assert _donor_positions(out) == expected
    assert _recorded_min_separation(out) == pytest.approx(min_separation)


@pytest.mark.parametrize("bad", [0.0, -1.0])
def test_min_separation_must_be_positive(bad: float) -> None:
    with pytest.raises(ExtractionError, match="min_separation must be > 0"):
        ExtractOptions(min_separation=bad)


# --- IMG-F12: a movie that changes during the extraction is refused ------------


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _touch_during_extraction(monkeypatch: pytest.MonkeyPatch, movie: Path) -> None:
    """Move the movie's mtime forward while the traces are integrated.

    The guard re-reads the size and mtime after the extraction; a later mtime is a
    change. Only the timestamp moves (no bytes are written to the open memmap), so
    the step is the same on every operating system.
    """
    real = extract_module.extract_molecules

    def touch_then_extract(*args, **kwargs):
        stat = movie.stat()
        os.utime(movie, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
        return real(*args, **kwargs)

    monkeypatch.setattr(extract_module, "extract_molecules", touch_then_extract)


def test_movie_changed_during_extraction_is_refused(
    tmp_path: Path, movie: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "changed.tether"
    _touch_during_extraction(monkeypatch, movie)
    with pytest.raises(ExtractionError, match="movie changed during extraction"):
        extract_movie(movie, out)
    assert not out.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == [movie.name]  # no partial file


def test_movie_changed_during_extraction_keeps_an_existing_project(
    tmp_path: Path, movie: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "existing.tether"
    extract_movie(movie, out)
    before = _file_sha256(out)
    _touch_during_extraction(monkeypatch, movie)
    with pytest.raises(ExtractionError, match="movie changed during extraction"):
        extract_movie(movie, out, overwrite=True)
    assert _file_sha256(out) == before  # the earlier project is not replaced

# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Write the AVI test files of the AVI adapter (#906, ADR-0154) and their FFmpeg reference TIFFs.

The AVI adapter's bit-for-bit test compares Tether's frames with the frames that **FFmpeg**
decodes from the same file. FFmpeg writes two of the movies and decodes all three, so the
reference is a second, independent AVI reader:

* ``gray_y800_37x23x8.avi`` — written by FFmpeg (``rawvideo``, ``gray``): 8 frames of 37 x 23,
  FourCC ``Y800``, rows top-down and unpadded.
* ``gray_dib_top_down_37x23x8.avi`` — written by FFmpeg (``rawvideo``, ``pal8``) from frames that
  carry the gray ramp as their color table: an 8-bit ``BI_RGB`` DIB, negative height (rows
  top-down), each row padded to 40 bytes.
* ``gray_dib_bottom_up_37x23x8.avi`` — written by ``tests/_avi_synth.py``: an 8-bit ``BI_RGB``
  DIB with a positive height, so its rows are stored bottom-up, as ImageJ and Windows writers
  store them. FFmpeg writes no bottom-up DIB, so it only decodes this one.

Each has ``<name>_reference.tif``: the frames as ``ffmpeg -f rawvideo`` decodes them (``gray``,
or ``pal8`` with the color table that FFmpeg appends to each frame checked to be the gray ramp
and dropped), saved by tifffile as an uncompressed ``uint8`` TYX TIFF. The refusal files, 2
frames of 16 x 8 written by FFmpeg: ``ffv1.avi`` (FFV1, lossless), ``mjpeg.avi`` (Motion JPEG,
lossy), ``pal8_rgb332.avi`` (an 8-bit DIB whose color table is FFmpeg's RGB 3-3-2 palette, not the
gray ramp) and ``bgr24.avi`` (24-bit color).

This is a developer-only tool and never runs in CI. It needs ``ffmpeg`` on ``PATH`` (the committed
files: FFmpeg 6.1.1, Ubuntu ``6.1.1-3ubuntu5``), NumPy and tifffile::

    python scripts/make_avi_fixtures.py tests/fixtures/avi

The frames are synthetic and seeded, so the files are Tether's own work under the repository
licence. ``tests/fixtures/PROVENANCE.md`` records the SHA-256 of the committed files.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

_N, _H, _W = 8, 23, 37
_RAMP = np.repeat(np.arange(256, dtype="u1"), 4).reshape(256, 4)
_RAMP[:, 3] = 255  # FFmpeg's PAL8 table is native-endian ARGB: B, G, R, A on disk


def frames(n: int = _N, h: int = _H, w: int = _W, seed: int = 906) -> np.ndarray:
    """The seeded 8-bit frames of the readable movies."""
    return np.random.default_rng(seed).integers(0, 256, size=(n, h, w), dtype="u1")


def _ffmpeg(*args: str | Path) -> None:
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *map(str, args)], check=True)


def _raw_input(path: Path, data: np.ndarray, pix_fmt: str, *, size: tuple[int, int]) -> list[str]:
    path.write_bytes(data.tobytes())
    return ["-f", "rawvideo", "-pix_fmt", pix_fmt, "-s", f"{size[1]}x{size[0]}", "-r", "10"]


def decode(avi: Path, scratch: Path) -> np.ndarray:
    """Return the frames that FFmpeg decodes from ``avi``, as ``(n, h, w)`` ``uint8``."""
    probe = (
        subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,pix_fmt",
                "-of",
                "csv=p=0",
                str(avi),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        .stdout.strip()
        .split(",")
    )
    w, h, pix_fmt = int(probe[0]), int(probe[1]), probe[2]
    out = scratch / (avi.stem + ".raw")
    if pix_fmt == "pal8":
        _ffmpeg("-i", avi, "-f", "rawvideo", "-pix_fmt", "pal8", out)
        raw = np.fromfile(out, dtype="u1").reshape(-1, h * w + 1024)
        tables = raw[:, h * w :].reshape(-1, 256, 4)
        if not (tables == _RAMP).all():
            sys.exit(f"{avi}: FFmpeg reports a color table that is not the gray ramp")
        return np.ascontiguousarray(raw[:, : h * w].reshape(-1, h, w))
    if pix_fmt != "gray":
        sys.exit(f"{avi}: FFmpeg decodes {pix_fmt}, not gray or pal8")
    _ffmpeg("-i", avi, "-f", "rawvideo", "-pix_fmt", "gray", out)
    return np.fromfile(out, dtype="u1").reshape(-1, h, w)


def main(argv: list[str] | None = None) -> int:
    """Write every file into the named folder."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("out", type=Path, help="the folder, normally tests/fixtures/avi")
    args = parser.parse_args(argv)
    import tifffile

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
    from _avi_synth import write_avi

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    movie = frames()
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp)
        raw = scratch / "in.raw"
        gray = _raw_input(raw, movie, "gray", size=(_H, _W))
        y800 = out / "gray_y800_37x23x8.avi"
        _ffmpeg(*gray, "-i", raw, "-c:v", "rawvideo", "-pix_fmt", "gray", y800)
        pal = np.concatenate(
            [movie.reshape(_N, -1), np.broadcast_to(_RAMP.reshape(1, -1), (_N, 1024))], axis=1
        )
        pal_in = _raw_input(raw, pal, "pal8", size=(_H, _W))
        _ffmpeg(
            *pal_in,
            "-i",
            raw,
            "-c:v",
            "rawvideo",
            "-pix_fmt",
            "pal8",
            out / "gray_dib_top_down_37x23x8.avi",
        )
        write_avi(out / "gray_dib_bottom_up_37x23x8.avi", movie)
        readable = ("gray_y800_37x23x8", "gray_dib_top_down_37x23x8", "gray_dib_bottom_up_37x23x8")
        for name in readable:
            got = decode(out / f"{name}.avi", scratch)
            if not np.array_equal(got, movie):
                sys.exit(f"{name}: FFmpeg decodes other frames than were written")
            tifffile.imwrite(out / f"{name}_reference.tif", got, photometric="minisblack")
        small = frames(2, 8, 16, seed=9061)
        tiny = _raw_input(raw, small, "gray", size=(8, 16))
        for name, codec in (
            ("ffv1", ["-c:v", "ffv1"]),
            ("mjpeg", ["-c:v", "mjpeg", "-pix_fmt", "yuvj420p"]),
            ("pal8_rgb332", ["-c:v", "rawvideo", "-pix_fmt", "pal8"]),
            ("bgr24", ["-c:v", "rawvideo", "-pix_fmt", "bgr24"]),
        ):
            _ffmpeg(*tiny, "-i", raw, *codec, out / f"{name}.avi")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

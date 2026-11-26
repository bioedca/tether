# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Make the SIRA test extract, and the reference export of a SIRA file by MASH-FRET (#905).

The SIRA adapter's bit-for-bit tests compare Tether's frames with the frames that
**MASH-FRET's own reader**, ``readSira.m``, reads from the same file. This script runs
``readSira.m`` under GNU Octave (``octave-cli``), from a MASH-FRET checkout that you name,
headless in MASH-FRET's own ``mute_actions`` mode on an invisible figure. Octave 8 has no
``contains``, which MASH-FRET's ``getSiraDat.m`` calls on the header line, so the script
gives Octave a MATLAB-equivalent one. Octave writes the frames as raw little-endian float32,
and tifffile saves them as an uncompressed float32 TYX TIFF.

    python scripts/make_sira_reference.py reference IN.sira OUT.tif --mash DIR
    python scripts/make_sira_reference.py reference IN.sira --mash DIR --digest
    python scripts/make_sira_reference.py extract IN.sira OUT.sira --frames 16
    python scripts/make_sira_reference.py offsets OUT.sira --mash DIR

``DIR`` is ``MASH-FRET/source`` of the checkout. ``extract`` writes the first frames of a
file, with its header line unchanged and the frame count of the header set to the frames
kept; the frames' bytes are copied unchanged. It needs no package. ``offsets`` has
MASH-FRET's own writer, ``writeSiraFile.m``, write the small movie of [`offset_frames`][],
whose frames hold negative values, so that the file stores an offset for them. ``reference`` needs
``octave-cli``, gnuplot (the graphics toolkit of the invisible figure), NumPy and tifffile.
The versions that made the committed files are in ``tests/fixtures/PROVENANCE.md``.
"""

from __future__ import annotations

import argparse
import hashlib
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

_DRIVER = """\
function sira_reference(mash_source, in, out)
addpath(fullfile(mash_source, 'mod-video-processing', 'graphic-files'));
addpath(genpath(fullfile(mash_source, 'divers')));
graphics_toolkit('gnuplot');
h_fig = figure('visible', 'off');
guidata(h_fig, struct('mute_actions', true));
[data, ok] = readSira(in, 'all', [], h_fig, false);
if ~ok, error('readSira failed'); end
f = fopen(out, 'w', 'ieee-le');
fwrite(f, permute(data.movie, [2 1 3]), 'single');
fclose(f);
printf('%d %d %d\\n', size(data.movie, 3), size(data.movie, 1), size(data.movie, 2));
end
"""

_WRITER = """\
function sira_write(mash_source, raw, shape, out)
addpath(fullfile(mash_source, 'mod-video-processing', 'graphic-files'));
f = fopen(raw, 'r', 'ieee-le');
frames = permute(reshape(fread(f, Inf, 'single=>single'), shape(3), shape(2), shape(1)), [2 1 3]);
fclose(f);
g = writeSiraFile('init', out, 'tether-905', [0.05, shape(3), shape(2), shape(1)]);
for k = 1:shape(1)
  writeSiraFile('append', g, frames(:, :, k), shape(2) * shape(3));
end
fclose(g);
end
"""

_CONTAINS = """\
function tf = contains(str, pat)
if ischar(pat), pat = {pat}; end
hit = @(s) any(cellfun(@(p) ~isempty(strfind(s, p)), pat));
if ischar(str), tf = hit(str); else, tf = cellfun(hit, str); end
end
"""


def _octave_str(path: Path) -> str:
    """Return ``path`` as an Octave single-quoted string literal (#1425).

    Octave ends a single-quoted string at the first apostrophe, and its escape for one inside is a
    doubled apostrophe, so ``/home/o'brien`` becomes ``'/home/o''brien'``.
    """
    return "'" + path.as_posix().replace("'", "''") + "'"


def _octave(work: Path, call: str) -> str:
    (work / "sira_reference.m").write_text(_DRIVER)
    (work / "sira_write.m").write_text(_WRITER)
    (work / "contains.m").write_text(_CONTAINS)
    done = subprocess.run(
        ["octave-cli", "--no-gui", "--quiet", "--eval", f"addpath({_octave_str(work)}); {call}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout


def offset_frames():  # noqa: ANN201 - numpy is imported lazily.
    """Three 4 x 5 frames in halves: two with negative values, the last with none."""
    import numpy as np

    base = np.arange(20, dtype="<f4").reshape(4, 5)
    return np.stack([(base - 7) / 2, (base - 9) / 2, (base + 10) / 2]).astype("<f4")


def write_offsets(out: Path, mash: Path) -> None:
    """Have MASH-FRET's writeSiraFile.m write [`offset_frames`][] to ``out``."""
    frames = offset_frames()
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        frames.tofile(work / "frames.raw")
        shape = "[" + " ".join(str(v) for v in frames.shape) + "]"
        _octave(
            work,
            f"sira_write({_octave_str(mash.resolve())}, {_octave_str(work / 'frames.raw')}, "
            f"{shape}, {_octave_str(out.resolve())})",
        )


def reference_frames(path: Path, mash: Path):  # noqa: ANN201 - numpy is imported lazily.
    """Return the frames of ``path`` as MASH-FRET's readSira.m reads them, as ``<f4``."""
    import numpy as np

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        raw = work / "frames.raw"
        call = (
            f"sira_reference({_octave_str(mash.resolve())}, {_octave_str(path.resolve())}, "
            f"{_octave_str(raw)})"
        )
        n, h, w = (int(v) for v in _octave(work, call).split()[-3:])
        return np.fromfile(raw, dtype="<f4").reshape(n, h, w)


def extract(path: Path, out: Path, frames: int) -> None:
    """Write the first ``frames`` frames of ``path`` to ``out`` as a SIRA file."""
    data = path.read_bytes()
    line = min(i for i in (data.find(b"\r"), data.find(b"\n")) if i >= 0) + 1
    frame_time, width, height, total = struct.unpack_from("<dfff", data, line)
    if not 1 <= frames <= total:
        raise SystemExit(f"{path} has {total:g} frames; cannot keep {frames}")
    start = line + 20
    frame_bytes = (int(width) * int(height) + 1) * 4
    header = struct.pack("<dfff", frame_time, width, height, float(frames))
    out.write_bytes(data[:line] + header + data[start : start + frames * frame_bytes])


def main(argv: list[str] | None = None) -> int:
    """Run the ``reference`` or the ``extract`` command."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    ref = sub.add_parser("reference", help="the reference TIFF of MASH-FRET's readSira.m")
    ref.add_argument("sira", type=Path)
    ref.add_argument("out", type=Path, nargs="?")
    ref.add_argument("--mash", type=Path, required=True, help="MASH-FRET/source of a checkout")
    ref.add_argument("--digest", action="store_true", help="print shape and pixel SHA-256")
    ext = sub.add_parser("extract", help="the first frames of a SIRA file")
    ext.add_argument("sira", type=Path)
    ext.add_argument("out", type=Path)
    ext.add_argument("--frames", type=int, required=True)
    off = sub.add_parser("offsets", help="a movie with offsets, by MASH-FRET's writer")
    off.add_argument("out", type=Path)
    off.add_argument("--mash", type=Path, required=True, help="MASH-FRET/source of a checkout")
    args = parser.parse_args(argv)
    if args.command == "extract":
        extract(args.sira, args.out, args.frames)
        return 0
    if args.command == "offsets":
        write_offsets(args.out, args.mash)
        return 0
    frames = reference_frames(args.sira, args.mash)
    if args.digest:
        print(list(frames.shape), hashlib.sha256(frames.tobytes()).hexdigest())
        return 0
    if args.out is None:
        parser.error("give OUT.tif or --digest")
    import tifffile

    tifffile.imwrite(args.out, frames, photometric="minisblack")
    return 0


if __name__ == "__main__":
    sys.exit(main())

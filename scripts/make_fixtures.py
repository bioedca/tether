# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Derive Tether's small committed test fixtures from the read-only example-data.

The ``example-data/`` sibling holds the real lab acquisitions cited in the PRD
(big-endian movie, tMAVEN SMDs, the 281-molecule population + its vbHMM model).
The large originals are **never** committed; this tool derives the small,
defensible fixtures that live in the repo (PRD §8 NFR-FIXTURES) and records their
provenance.

This is a **developer-only** regeneration tool — it never runs in CI. The
scientific stack (h5py / tifffile / numpy) is not in the dev shell, so run it
through ``uv``::

    uv run --no-project --with h5py --with tifffile --with numpy \\
        python scripts/make_fixtures.py

Outputs (relative to the repo root):

* ``tests/fixtures/movie_be_64x64x50.tif`` — a 50-frame 64x64 big-endian uint16
  crop of the 0.9 GB UCKOPSB movie, kept big-endian to exercise the M0 S7 reader
  and the napari open path; the crop window is the brightest 64x64 region of the
  first frames so it contains real molecules (M1/M2 extraction smoke).
* ``tests/fixtures/smd_4mol.hdf5`` / ``smd_2mol.hdf5`` — the curated 4- and
  2-molecule tMAVEN SMDs, copied verbatim (already small; structure preserved
  exactly for tMAVEN-interop round-trip tests).
* ``tests/fixtures/large/smd_281mol.hdf5`` + ``model_281mol.hdf5`` — the
  redistributable >=50-molecule population SMD and its paired 4-state consensus
  vbHMM model, routed to the Git-LFS gated tier (M0.5/M6 idealization-parity).

It also updates ``tests/fixtures/PROVENANCE.md`` deterministically: its own five rows
(source name, sizes and full source SHA-256) and the movie-crop paragraph between its
markers. Every other row and section of that inventory is kept as it is (#552).
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import tifffile

# example-data was gathered into the sibling folder on this date (see its
# README). Fixtures are *derived* from it; we record the upstream accessed date.
SOURCE_ACCESSED = "2026-06-22"

# Crop geometry for the movie fixture.
CROP_FRAMES = 50
CROP_SIZE = 64

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"
LARGE = FIXTURES / "large"


def default_source_root() -> Path:
    """Locate the ``example-data`` sibling of the main checkout.

    Resolved via the git common dir so it is correct from a linked worktree
    (where simple ``..`` walks land inside ``.claude/worktrees/``).
    """
    try:
        common = subprocess.check_output(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=REPO_ROOT,
            text=True,
        ).strip()
        main_checkout = Path(common).parent
        candidate = main_checkout.parent / "example-data"
        if candidate.is_dir():
            return candidate
    except (subprocess.CalledProcessError, OSError):
        pass
    # Fallback: a plain sibling of the repo root.
    return REPO_ROOT.parent / "example-data"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def brightest_window(frames: np.ndarray, size: int) -> tuple[int, int]:
    """Top-left (row, col) of the brightest ``size``x``size`` window.

    Uses a summed-area table over the max-intensity projection so every window
    is considered in O(H*W) rather than a strided guess.
    """
    proj = frames.max(axis=0).astype(np.float64)
    sat = np.zeros((proj.shape[0] + 1, proj.shape[1] + 1), dtype=np.float64)
    sat[1:, 1:] = proj.cumsum(axis=0).cumsum(axis=1)
    h, w = proj.shape
    box = (
        sat[size:, size:] - sat[:-size, size:] - sat[size:, :-size] + sat[:-size, :-size]
    )  # shape (h-size+1, w-size+1): sum over each window
    flat = int(box.argmax())
    return divmod(flat, w - size + 1)


def crop_movie(src: Path, dst: Path) -> dict[str, object]:
    """Write a big-endian uint16 crop of the first frames of ``src``."""
    frames = tifffile.imread(src, key=range(CROP_FRAMES))  # (CROP_FRAMES, H, W)
    if frames.ndim != 3:
        raise ValueError(f"expected a 3-D movie, got shape {frames.shape}")
    row, col = brightest_window(frames, CROP_SIZE)
    crop = frames[:, row : row + CROP_SIZE, col : col + CROP_SIZE]
    # Force big-endian on disk so the M0 S7 reader's byte-order handling is
    # genuinely exercised (the source movie is big-endian).
    crop_be = np.ascontiguousarray(crop, dtype=">u2")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(dst, crop_be, photometric="minisblack", byteorder=">")
    with tifffile.TiffFile(dst) as tif:
        if tif.byteorder != ">":
            raise RuntimeError(f"crop is not big-endian (byteorder={tif.byteorder!r})")
        shape = tuple(int(x) for x in tif.series[0].shape)
    return {"crop_origin": (row, col), "shape": shape}


def copy_verbatim(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def format_row(
    fixture: str,
    label: str,
    src_name: str,
    src_size: int,
    dst_size: int,
    src_sha256: str,
    note: str,
) -> str:
    """One inventory row, with the **full** 64-hex source SHA-256 (#552)."""
    return (
        f"| `{fixture}` | {label} | `{src_name}` | {src_size:,} B | {dst_size:,} B | "
        f"`{src_sha256}` | {note} |"
    )


def md_row(label: str, src: Path, dst: Path, note: str) -> str:
    return format_row(
        dst.relative_to(REPO_ROOT).as_posix(),
        label,
        src.name,
        src.stat().st_size,
        dst.stat().st_size,
        sha256(src),
        note,
    )


def row_key(line: str) -> str | None:
    """The fixture path in the first cell of an inventory row, or ``None``."""
    if not line.startswith("| `"):
        return None
    end = line.find("`", 3)
    return line[3:end] if end > 3 else None


# The movie-crop paragraph is the only prose this script owns. These markers bound it,
# and nothing outside them (or outside its own table rows) is ever rewritten.
CROP_BEGIN = "<!-- make_fixtures.py: movie crop, begin -->"
CROP_END = "<!-- make_fixtures.py: movie crop, end -->"


def movie_crop_text(crop_meta: dict[str, object]) -> str:
    origin = crop_meta["crop_origin"]
    shape = crop_meta["shape"]
    return f"""`movie_be_64x64x50.tif` is the **brightest {CROP_SIZE}×{CROP_SIZE} window**
(top-left pixel `(row={origin[0]}, col={origin[1]})`) of the first
{CROP_FRAMES} frames of the source movie, kept **big-endian uint16**
(shape `{shape}`, axes `TYX`) so it exercises the M0 S7 big-endian reader and
the napari open path, and contains real molecules for M1/M2 extraction smoke."""


def update_provenance(text: str, rows: list[str], crop_meta: dict[str, object]) -> str:
    """Return ``text`` with only this script's rows and its movie-crop paragraph replaced.

    ``PROVENANCE.md`` is the inventory of every committed fixture, and most of its rows come
    from other scripts or by hand. This script used to rewrite the whole file from its own five
    rows and so erased every later entry (#552). Now it replaces, in place, each line whose first
    cell names one of its fixtures, and the text between ``CROP_BEGIN`` and ``CROP_END``. Every
    other byte is kept. A missing row or marker raises rather than guessing where to write.
    """
    wanted = {row_key(row): row for row in rows}
    lines = text.split("\n")
    seen: set[str] = set()
    for i, line in enumerate(lines):
        key = row_key(line)
        if key in wanted:
            if key in seen:
                raise ValueError(f"PROVENANCE.md has more than one row for {key}")
            lines[i] = wanted[key]
            seen.add(key)
    missing = sorted(k for k in wanted if k not in seen)
    if missing:
        raise ValueError(f"PROVENANCE.md has no row for {missing}; add the row by hand first")
    out = "\n".join(lines)
    begin = out.find(CROP_BEGIN)
    end = out.find(CROP_END)
    if begin < 0 or end < begin:
        raise ValueError("PROVENANCE.md lacks the movie-crop markers of make_fixtures.py")
    head = out[: begin + len(CROP_BEGIN)]
    return f"{head}\n{movie_crop_text(crop_meta)}\n{out[end:]}"


def write_provenance(rows: list[str], crop_meta: dict[str, object]) -> None:
    path = FIXTURES / "PROVENANCE.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(update_provenance(text, rows, crop_meta), encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-root",
        type=Path,
        default=default_source_root(),
        help="directory holding the example-data subfolders",
    )
    args = parser.parse_args(argv)
    root: Path = args.source_root
    if not root.is_dir():
        parser.error(f"source root not found: {root} (pass --source-root)")

    movie = root / "bla-uckopsb-tbox-video10" / "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif"
    smd_4 = root / "bla-uckopsb-tbox-video10" / "video10.hdf5"
    smd_2 = root / "uckopsb-01ab-smd-video25-28" / "video 25,26,27,28.hdf5"
    smd_281 = root / "tmaven-model" / "model-source-smd-281mol.hdf5"
    model = root / "tmaven-model" / "model.hdf5"
    for p in (movie, smd_4, smd_2, smd_281, model):
        if not p.is_file():
            parser.error(f"missing source: {p}")

    FIXTURES.mkdir(parents=True, exist_ok=True)
    LARGE.mkdir(parents=True, exist_ok=True)

    out_movie = FIXTURES / "movie_be_64x64x50.tif"
    out_4 = FIXTURES / "smd_4mol.hdf5"
    out_2 = FIXTURES / "smd_2mol.hdf5"
    out_281 = LARGE / "smd_281mol.hdf5"
    out_model = LARGE / "model_281mol.hdf5"

    crop_meta = crop_movie(movie, out_movie)
    copy_verbatim(smd_4, out_4)
    copy_verbatim(smd_2, out_2)
    copy_verbatim(smd_281, out_281)
    copy_verbatim(model, out_model)

    rows = [
        md_row("big-endian movie crop (M0 open; M1/M2 smoke)", movie, out_movie, "cropped"),
        md_row("4-molecule tMAVEN SMD (M0.5/M2 parity)", smd_4, out_4, "verbatim copy"),
        md_row("2-molecule tMAVEN SMD (import round-trip)", smd_2, out_2, "verbatim copy"),
        md_row("281-molecule population SMD (parity gate)", smd_281, out_281, "verbatim · **LFS**"),
        md_row("4-state vbHMM model (parity gate)", model, out_model, "verbatim · **LFS**"),
    ]
    write_provenance(rows, crop_meta)

    print("Wrote fixtures:")
    for p in (out_movie, out_4, out_2, out_281, out_model):
        print(f"  {p.relative_to(REPO_ROOT).as_posix()}  ({p.stat().st_size:,} B)")
    print(f"  crop window top-left = {crop_meta['crop_origin']}, shape = {crop_meta['shape']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

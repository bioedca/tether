# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stage the kinSoftChallenge *simulated* datasets into one gated-tier HDF5 fixture.

The kinSoftChallenge [Götz2022] is a blind community benchmark of smFRET
kinetic-rate analysis tools. Its three simulated datasets (``sim_dataset_Fig2``
= level 1, ``Fig3`` = level 2, ``Fig4`` = level 3; increasing difficulty) each
hold per-trace ALEX text files with columns ``t (s) / Idd / Ida / Iaa / FRET E``.
They back the M8 kinetics-validation oracle (PRD §8 NFR-VALID(c), §9 M8): fitted
rates on a level must fall within that dataset's reported inter-tool spread
(advisory).

The source data is **CC-BY-4.0** (Götz & Schmid, Zenodo 10.5281/zenodo.5701310),
NOT the repo's GPL-3.0 — the packed fixture is annotated CC-BY-4.0 in REUSE.toml
and attributed in NOTICE + PROVENANCE. This script is dev-only (it is GPL like the
rest of ``scripts/``); it never runs in CI.

Usage — download the three simulated zips from the record first (they are not
committed), then::

    uv run --no-project --with h5py --with numpy python scripts/make_kinsoft_fixture.py \
        --source /path/to/dir/with/the/three/zips \
        --out tests/fixtures/large/kinsoft_sim.hdf5

``--source`` is the directory holding the pristine
``sim_dataset_Fig{2,3,4}.zip`` (hashed for provenance and read directly, so the
extracted ``__MACOSX`` shadow files never matter).
"""

from __future__ import annotations

import argparse
import hashlib
import zipfile
from pathlib import Path

import h5py
import numpy as np

# Challenge level -> (source zip, inner dataset dir, HDF5 group, figure label).
LEVELS = [
    ("sim_dataset_Fig2.zip", "sim_level1_final_publish", "level1", "Fig2"),
    ("sim_dataset_Fig3.zip", "sim_level2_final_publish", "level2", "Fig3"),
    ("sim_dataset_Fig4.zip", "sim_level3_final_publish", "level3", "Fig4"),
]
# Column order in every trace file: %t (s)  Idd  Ida  Iaa  FRET E.
COLUMNS = ("idd", "ida", "iaa", "fret_e")

DOI = "10.5281/zenodo.5701310"
URL = "https://zenodo.org/records/5701310"
LICENSE = "CC-BY-4.0"
CITATION = (
    "Götz M, Barth A, Bohr S S-R, et al. Nat Commun 13:5402 (2022). doi:10.1038/s41467-022-33023-3"
)
ACCESSED = "2026-07-13"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_trace(raw: bytes) -> np.ndarray:
    """Parse one trace text file -> (n_frames, 5) float array [t, Idd, Ida, Iaa, E]."""
    rows = []
    for line in raw.decode("ascii").splitlines():
        line = line.strip()
        if not line or line.startswith("%"):  # header row: "%t (s)\tIdd ..."
            continue
        parts = line.split("\t")
        if len(parts) != 5:
            raise ValueError(f"expected 5 tab-separated columns, got {len(parts)}: {line!r}")
        rows.append([float(p) for p in parts])
    if not rows:
        raise ValueError("no data rows")
    return np.asarray(rows, dtype=np.float64)


def _read_level(zip_path: Path, inner_dir: str) -> tuple[list[np.ndarray], float]:
    """Read all trace_*.txt from *inner_dir* inside *zip_path*, sorted numerically."""
    with zipfile.ZipFile(zip_path) as zf:
        names = [
            n
            for n in zf.namelist()
            if n.startswith(f"{inner_dir}/")
            and n.rsplit("/", 1)[-1].startswith("trace_")
            and n.endswith(".txt")
            and "__MACOSX" not in n
            and not n.rsplit("/", 1)[-1].startswith("._")
        ]
        names.sort(key=lambda n: int(n.rsplit("trace_", 1)[1].split(".")[0]))
        traces = [_parse_trace(zf.read(n)) for n in names]

    dts = {round(float(tr[1, 0] - tr[0, 0]), 6) for tr in traces}
    if len(dts) != 1:
        raise ValueError(f"inconsistent frame time within {inner_dir}: {sorted(dts)}")
    return traces, dts.pop()


def build(source: Path, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(out, "w") as h5:
        h5.attrs["format"] = "kinsoft-sim"
        h5.attrs["source"] = "kinSoftChallenge simulated datasets (Götz & Schmid)"
        h5.attrs["doi"] = DOI
        h5.attrs["url"] = URL
        h5.attrs["license"] = LICENSE
        h5.attrs["citation"] = CITATION
        h5.attrs["accessed"] = ACCESSED
        h5.attrs["columns"] = ",".join(COLUMNS)
        h5.attrs["description"] = (
            "Simulated ALEX smFRET traces from the kinSoftChallenge blind benchmark. "
            "Per level: idd/ida/iaa/fret_e are (n_traces, max_frames) float32 arrays "
            "zero-padded past each trace's `length`; frame_time_s is the level's dt."
        )

        for zip_name, inner_dir, group, figure in LEVELS:
            zip_path = source / zip_name
            traces, dt = _read_level(zip_path, inner_dir)
            n = len(traces)
            max_len = max(tr.shape[0] for tr in traces)
            lengths = np.array([tr.shape[0] for tr in traces], dtype=np.int32)

            packed = {c: np.zeros((n, max_len), dtype=np.float32) for c in COLUMNS}
            for i, tr in enumerate(traces):
                L = tr.shape[0]
                for j, c in enumerate(COLUMNS):  # skip col 0 (time) -> tr[:, 1:]
                    packed[c][i, :L] = tr[:, j + 1]

            g = h5.create_group(group)
            g.attrs["figure"] = figure
            g.attrs["source_zip"] = zip_name
            g.attrs["source_sha256"] = _sha256(zip_path)
            g.attrs["frame_time_s"] = float(dt)
            g.attrs["n_traces"] = n
            g.create_dataset("length", data=lengths, compression="gzip")
            for c in COLUMNS:
                g.create_dataset(c, data=packed[c], compression="gzip", shuffle=True)
            sha = str(g.attrs["source_sha256"])[:12]
            print(f"{group} ({figure}): n={n} max_len={max_len} dt={dt}s sha256={sha}…")

    print(f"wrote {out} ({out.stat().st_size:,} bytes)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, required=True, help="dir with the 3 sim zips")
    ap.add_argument("--out", type=Path, default=Path("tests/fixtures/large/kinsoft_sim.hdf5"))
    args = ap.parse_args()
    build(args.source, args.out)


if __name__ == "__main__":
    main()

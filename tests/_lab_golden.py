# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Self-drift golden numbers for the full laboratory acquisitions on the workstation (#703).

The self-drift suite of #702 (``_golden``, ADR-0080) runs small inputs on every CI runner. A
real acquisition has thousands of spots, uneven background, drift and bleaching. The data
acquisitions that the maintainer keeps on the reference workstation are private, so this
module runs them only through the ``private_data`` fixture of the workstation runner
(ADR-0071), and the repository keeps **summary numbers only**: one file
``tests/golden/workstation/<id>.json`` for each neutral id, with no traces, no images and no
file names (maintainer decisions R12: B and R13: A).

Each acquisition runs the full pipeline from the movie to the FRET histogram with the default
settings and the map file of its acquisition series: ``tether batch --no-idealize --tmap``
(detection, extraction and correction), then the native two-state Gaussian HMM of #702 for the
state means. :func:`lab_quantities` returns the numbers; :func:`validate_document` is the format
rule that ``tests/test_lab_golden_format.py`` applies to each committed file in the required
matrix. The comparison is the tolerance policy of #702 (:func:`_golden.compare`).

Like ``_golden``, this is a plain helper module imported as the top-level ``_lab_golden``.
"""

from __future__ import annotations

import contextlib
import io
import json
import math
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import numpy as np

import _golden

__all__ = [
    "EXPORT_ROUTES",
    "LAB_ACQUISITIONS",
    "LAB_GOLDEN_DIR",
    "MAP_ROLE",
    "MOVIE_ROLE",
    "QUANTITIES",
    "EXPORT_ROUTE_ROTATION",
    "compare_export_routes",
    "dump_lab_golden",
    "extract_project",
    "lab_quantities",
    "load_lab_golden",
    "movie_role",
    "rotate_movie",
    "validate_document",
]

LAB_GOLDEN_DIR = _golden.GOLDEN_DIR / "workstation"
#: Version of the layout of a laboratory golden file.
LAB_FORMAT = 1
#: Same pattern as the runner's ``NEUTRAL_ID`` (``scripts/workstation/run_private_checks.py``).
NEUTRAL_ID = re.compile(r"[a-z]+-[0-9]{2,}")

#: The ids of the two export routes of one movie (#703 item 4), as ``{vendor: direct}``: the TIFF
#: exported from the vendor format, and the direct TIFF of the same movie. The name table gives
#: each route its own id; the two are one acquisition (D3: A), so only the vendor route has a
#: golden file (item 1), and the direct route is read only by the export-route check.
EXPORT_ROUTES: dict[str, str] = {"lab-12": "lab-11"}
#: The vendor route is the direct TIFF turned a quarter turn clockwise: each vendor frame is
#: ``numpy.rot90(direct_frame, k=EXPORT_ROUTE_ROTATION)`` (measured on all 1700 frames, #887).
#: The export-route check turns the vendor frames back before it compares the two routes.
EXPORT_ROUTE_ROTATION = 3
#: The data acquisitions of the name table that have a golden file: every movie id except the
#: direct route of :data:`EXPORT_ROUTES` (maintainer decisions D1-D3: the bead movie has no
#: neutral id). The issue counted ten; the name table holds twelve (Q20: C, "ten or more").
LAB_ACQUISITIONS: tuple[str, ...] = tuple(
    f"lab-{i:02d}" for i in range(1, 14) if f"lab-{i:02d}" not in EXPORT_ROUTES.values()
)
#: Role of the movie that the pipeline reads. An id whose mapping value is one file name has only
#: the runner's default role ``file``, which then names the movie.
MOVIE_ROLE = "movie"
DEFAULT_ROLE = "file"
#: Role of the Deep-LASI map file of the acquisition's series (D2: A).
MAP_ROLE = "tmap"
#: States of the native HMM whose means are frozen.
NSTATES = 2

#: Every quantity a laboratory golden file holds, and what kind of value it is.
QUANTITIES: dict[str, str] = {
    "cli.batch.exit_code": "int",
    "molecules.count": "int",
    "molecules.accepted": "int",
    "intensity.median_donor": "float",
    "intensity.median_acceptor": "float",
    "intensity.median_total": "float",
    "histogram.counts": "int-list",
    "histogram.n_samples": "int",
    "histogram.n_molecules": "int",
    "hmm.means": "float-list",
    "hmm.n_iter": "int",
}


# --- the pipeline ------------------------------------------------------------------------


def movie_role(roles: Iterable[str]) -> str:
    """The role that names the movie of an id: ``movie``, else the default role ``file``."""
    roles = set(roles)
    return MOVIE_ROLE if MOVIE_ROLE in roles or DEFAULT_ROLE not in roles else DEFAULT_ROLE


def extract_project(movie: Path, tmap: Path | None, workdir: Path) -> tuple[int, Path | None]:
    """``tether batch --no-idealize`` on one movie (with its map file); exit code and project.

    ``tmap=None`` uses the default registration, which only the synthetic check of the
    required matrix needs; every laboratory acquisition passes its map file.
    """
    from tether.cli import main  # noqa: PLC0415

    out_dir = workdir / "batch"
    argv = ["batch", str(movie), "-d", str(out_dir), "--no-idealize"]
    if tmap is not None:
        argv += ["--tmap", str(tmap)]
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        code = int(main(argv))
    projects = sorted(out_dir.glob("*.tether"))
    return code, (projects[0] if len(projects) == 1 else None)


def rotate_movie(src: Path, dst: Path, k: int) -> Path:
    """Write the frames of ``src`` turned ``k`` quarter turns (``numpy.rot90``) to ``dst``.

    The copy is the uncompressed big-endian TIFF that the extractor reads; only the pixel
    layout changes. ``dst`` keeps the name of ``src`` when it is a folder, so a name-derived
    tag of the movie stays the same.
    """
    import tifffile  # noqa: PLC0415

    dst = dst / src.name if dst.is_dir() else dst
    frames = np.asarray(tifffile.memmap(src, mode="r"))
    turned = np.ascontiguousarray(np.rot90(frames, k=k, axes=(1, 2)), dtype=">u2")
    tifffile.imwrite(dst, turned, photometric="minisblack", byteorder=">")
    return dst


def _median(values: list[float]) -> float:
    return float(np.median(values)) if values else 0.0


def lab_quantities(movie: Path, tmap: Path | None, workdir: Path) -> dict[str, Any]:
    """The summary numbers of one acquisition, keyed by :data:`QUANTITIES`.

    * ``molecules.count``: molecules extracted; ``molecules.accepted``: those the default
      curation filter keeps (§7.5), which every population view reads.
    * ``intensity.median_*``: the median over accepted molecules of each molecule's mean
      corrected intensity in its analysis window (donor, acceptor, and their sum).
    * ``histogram.*``: the default population apparent-E histogram (counts, not density).
    * ``hmm.*``: the native two-state Gaussian HMM of #702 on the windowed apparent E.
    """
    from tether.analysis.histogram import population_apparent_e_histogram  # noqa: PLC0415
    from tether.imaging.extract import read_molecules, read_traces  # noqa: PLC0415
    from tether.project.labels import curation_filter_mask  # noqa: PLC0415

    workdir.mkdir(parents=True, exist_ok=True)
    code, path = extract_project(movie, tmap, workdir)
    if code != 0:
        # A failed stage must never become a golden number, even when a project was written.
        raise RuntimeError(f"tether batch exited {code}; no golden quantities from a failed run")
    if path is None:
        raise RuntimeError("tether batch wrote no single project")
    q: dict[str, Any] = {"cli.batch.exit_code": code}
    mols = read_molecules(path)
    traces = read_traces(path)
    donor = np.asarray(traces["donor_corrected"], dtype="float64")
    acceptor = np.asarray(traces["acceptor_corrected"], dtype="float64")
    keep = curation_filter_mask(mols)
    q["molecules.count"] = int(mols.shape[0])
    q["molecules.accepted"] = int(np.count_nonzero(keep))
    mean_d: list[float] = []
    mean_a: list[float] = []
    for i in np.flatnonzero(keep):
        lo, hi = (int(v) for v in mols["analysis_window"][i])
        if hi <= lo:
            continue
        mean_d.append(float(np.mean(donor[i, lo:hi])))
        mean_a.append(float(np.mean(acceptor[i, lo:hi])))
    q["intensity.median_donor"] = _median(mean_d)
    q["intensity.median_acceptor"] = _median(mean_a)
    q["intensity.median_total"] = _median([d + a for d, a in zip(mean_d, mean_a, strict=True)])
    h = population_apparent_e_histogram(path, density=False)
    q["histogram.counts"] = np.asarray(h.counts, dtype="int64").tolist()
    q["histogram.n_samples"] = int(h.n_samples)
    q["histogram.n_molecules"] = int(h.n_molecules or 0)
    hmm = _golden._idealize_natively(path, "golden", NSTATES)
    q["hmm.means"] = hmm["hmm.means"]
    q["hmm.n_iter"] = hmm["hmm.n_iter"]
    return {k: _golden._plain(q[k]) for k in sorted(q)}


def compare_export_routes(project_a: Path, project_b: Path) -> dict[str, float]:
    """How far the projects of the two export routes of one movie differ (#703 item 4).

    Returns summary numbers only: the molecule counts, how many molecules have the same
    donor and acceptor positions, the frame counts, and the largest absolute difference of a raw
    trace frame over those matched molecules and the frames both have. ``n_nonfinite_diff``
    counts the frames where only one route has a non-finite value, so a NaN cannot hide a
    difference. The routes agree when ``n_matched`` is above 0, ``n_unmatched`` and
    ``n_nonfinite_diff`` are 0, the frame counts are equal and ``max_trace_diff`` is 0.
    """
    from tether.imaging.extract import read_molecules, read_traces  # noqa: PLC0415

    ma, mb = read_molecules(project_a), read_molecules(project_b)
    ta, tb = read_traces(project_a), read_traces(project_b)

    def keyed(m: np.ndarray) -> dict[tuple[float, ...], list[int]]:
        # Every molecule at a position, so a duplicated position counts once for each copy.
        xy = np.hstack([np.asarray(m["donor_xy"]), np.asarray(m["acceptor_xy"])])
        out: dict[tuple[float, ...], list[int]] = {}
        for i, row in enumerate(xy):
            out.setdefault(tuple(np.round(row, 6).tolist()), []).append(i)
        return out

    ka, kb = keyed(ma), keyed(mb)
    common = sorted(set(ka) & set(kb))
    pairs = [(i, j) for key in common for i, j in zip(ka[key], kb[key], strict=False)]
    unmatched = sum(abs(len(ka.get(k, [])) - len(kb.get(k, []))) for k in set(ka) | set(kb))
    worst = 0.0
    nonfinite = 0
    frames = (int(np.asarray(ta["donor_raw"]).shape[1]), int(np.asarray(tb["donor_raw"]).shape[1]))
    width = min(frames)
    for channel in ("donor_raw", "acceptor_raw"):
        a = np.asarray(ta[channel], dtype="float64")[:, :width]
        b = np.asarray(tb[channel], dtype="float64")[:, :width]
        for i, j in pairs:
            ra, rb = a[i], b[j]
            fa, fb = np.isfinite(ra), np.isfinite(rb)
            nonfinite += int(np.count_nonzero(fa != fb))
            both = fa & fb
            worst = max(worst, float(np.max(np.abs(ra[both] - rb[both]), initial=0.0)))
    return {
        "n_molecules_a": int(ma.shape[0]),
        "n_molecules_b": int(mb.shape[0]),
        "n_frames_a": frames[0],
        "n_frames_b": frames[1],
        "n_matched": len(pairs),
        "n_unmatched": unmatched,
        "n_nonfinite_diff": nonfinite,
        "max_trace_diff": worst,
    }


# --- golden files ------------------------------------------------------------------------


def golden_path(neutral_id: str, directory: Path = LAB_GOLDEN_DIR) -> Path:
    return directory / f"{neutral_id}.json"


def load_lab_golden(neutral_id: str, directory: Path = LAB_GOLDEN_DIR) -> dict[str, Any] | None:
    """The quantities of a laboratory golden file, or ``None`` when there is none yet."""
    path = golden_path(neutral_id, directory)
    if not path.is_file():
        return None
    doc = json.loads(path.read_text(encoding="utf-8"))
    problems = validate_document(doc, expected_id=neutral_id)
    if problems:
        raise ValueError(f"{neutral_id}: " + "; ".join(problems))
    return dict(doc["quantities"])


def document(neutral_id: str, quantities: Mapping[str, Any]) -> dict[str, Any]:
    """The JSON document of a laboratory golden file. It holds no free text at all."""
    return {
        "format": LAB_FORMAT,
        "id": neutral_id,
        "quantities": {k: quantities[k] for k in sorted(quantities)},
    }


def dump_lab_golden(
    neutral_id: str, quantities: Mapping[str, Any], directory: Path = LAB_GOLDEN_DIR
) -> Path:
    """Write one laboratory golden file after :func:`validate_document` accepts it."""
    doc = document(neutral_id, quantities)
    problems = validate_document(doc, expected_id=neutral_id)
    if problems:
        raise ValueError(f"{neutral_id}: refusing to write: " + "; ".join(problems))
    path = golden_path(neutral_id, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(doc, indent=1, allow_nan=False)
    path.write_bytes((body + "\n").encode("utf-8"))
    return path


def _strings(value: Any) -> Iterable[str]:
    """Every string in a JSON value: the keys and the values of objects, at any depth."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield str(k)
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def _kind_ok(kind: str, value: Any) -> bool:
    def is_int(v: Any) -> bool:
        return isinstance(v, int) and not isinstance(v, bool)

    def is_float(v: Any) -> bool:
        return (is_int(v) or isinstance(v, float)) and not isinstance(v, bool) and math.isfinite(v)

    if kind == "int":
        return is_int(value)
    if kind == "float":
        return is_float(value)
    if kind == "int-list":
        return isinstance(value, list) and bool(value) and all(is_int(v) for v in value)
    if kind == "float-list":
        return isinstance(value, list) and bool(value) and all(is_float(v) for v in value)
    return False


#: The only strings a laboratory golden file may hold that are not a neutral id: its own keys.
_STRUCTURE = frozenset({"format", "id", "quantities", *QUANTITIES})


def validate_document(doc: Any, *, expected_id: str | None = None) -> list[str]:
    """Every way ``doc`` breaks the laboratory golden-file format; an empty list when it holds.

    The rule that keeps private names out of the repository: apart from the fixed keys of the
    layout and of :data:`QUANTITIES`, the only string a file may hold is a neutral id. A file
    name, a folder name or any free text therefore fails, wherever it sits.
    """
    if not isinstance(doc, dict):
        return ["the document is not a JSON object"]
    problems: list[str] = []
    for text in _strings(doc):
        if text not in _STRUCTURE and not NEUTRAL_ID.fullmatch(text):
            problems.append(f"holds a string that is not a neutral id ({len(text)} characters)")
    if set(doc) != {"format", "id", "quantities"}:
        problems.append("top-level keys are not exactly format, id, quantities")
    if doc.get("format") != LAB_FORMAT:
        problems.append(f"format is not {LAB_FORMAT}")
    nid = doc.get("id")
    if not (isinstance(nid, str) and NEUTRAL_ID.fullmatch(nid)):
        problems.append("id is not a neutral id")
    elif expected_id is not None and nid != expected_id:
        problems.append(f"id {nid} does not match the file name {expected_id}")
    quantities = doc.get("quantities")
    if not isinstance(quantities, dict):
        problems.append("quantities is not an object")
        return problems
    if list(quantities) != sorted(quantities):
        problems.append("quantities are not sorted")
    missing = sorted(set(QUANTITIES) - set(quantities))
    extra = sorted(set(quantities) - set(QUANTITIES))
    if missing:
        problems.append("missing quantities: " + ", ".join(missing))
    if extra:
        problems.append(f"{len(extra)} unknown quantities")
    for key, kind in QUANTITIES.items():
        if key in quantities and not _kind_ok(kind, quantities[key]):
            problems.append(f"{key} is not a finite {kind}")
    return problems

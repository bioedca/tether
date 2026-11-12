# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The self-drift suite's pipelines, golden files and comparison (#702, ADR-0078).

Each entry of :data:`INPUTS` runs one input through the public API from end to end and
returns a flat ``{quantity: value}`` mapping. ``tests/test_golden.py`` compares it with the
committed golden file ``tests/golden/<name>.json``; ``scripts/update_golden.py`` rewrites
the golden files and prints the old/new table a pull request must carry.

**This is a regression suite, not an oracle** (maintainer decision Q36). A golden value is
what Tether computed when the file was written. It makes no claim that the value is
scientifically right; it only makes a change of that value visible and deliberate.

The tolerance policy (ADR-0078): integers, strings, booleans, list lengths and the keys of a
golden file compare exactly; a float compares with :data:`FLOAT_RTOL` relative to the larger
magnitude. The exported tables are compared by the SHA-256 of their text after
:func:`normalize_table` (fixed ids masked, floats rounded to :data:`TABLE_SIGNIFICANT`
significant digits).

Like ``_synth`` and ``_analysis_store``, this is a plain helper module imported as the
top-level ``_golden`` (pytest's ``prepend`` import mode, no ``tests/__init__.py``).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

__all__ = [
    "FLOAT_RTOL",
    "GOLDEN_DIR",
    "INPUTS",
    "TABLE_SIGNIFICANT",
    "Difference",
    "compare",
    "diff_table",
    "dump_golden",
    "load_golden",
    "normalize_table",
    "run_input",
    "synthetic_traces_store",
]

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

#: Relative limit for a float quantity. Measured, not chosen (ADR-0078): against golden files
#: written on Linux, macOS and Windows differ by at most 2.7e-13 in closed-form results and by
#: at most 5.9e-10 in the outputs of an iterative fit (the dwell-time rates and their
#: confidence intervals); this limit leaves a factor of about 17 above the largest.
FLOAT_RTOL = 1e-8
#: Significant digits a float cell of an exported table keeps before the table is hashed.
TABLE_SIGNIFICANT = 10
#: Version of the golden-file layout (bumped only if the JSON layout itself changes).
GOLDEN_FORMAT = 1

#: The movie name every synthetic movie and store carries (a parseable UCKOPSB name).
MOVIE_NAME = "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif"
#: Table cells that are random per run (uuid4 ids) and are masked before hashing.
_RANDOM_ID = re.compile(r"^(mol|mov|cal|ali|apt)-[0-9a-f-]{8,}$")
#: A table cell that is a float (has a decimal point or an exponent).
_FLOAT_CELL = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$|^[+-]?(nan|inf)$")


# --- values ------------------------------------------------------------------------------


def _plain(value: Any) -> Any:
    """Convert a NumPy scalar/array (or nested list) to JSON-ready Python values.

    A non-finite float becomes the string ``"nan"``, ``"inf"`` or ``"-inf"`` so the golden
    file stays strict JSON.
    """
    if isinstance(value, np.ndarray):
        return [_plain(v) for v in value.tolist()]
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        f = float(value)
        if math.isnan(f):
            return "nan"
        if math.isinf(f):
            return "inf" if f > 0 else "-inf"
        return f
    return value


def _sha256_ints(array: np.ndarray) -> str:
    """SHA-256 of an integer array as little-endian int64 (the same bytes on every OS)."""
    return hashlib.sha256(np.ascontiguousarray(array, dtype="<i8").tobytes()).hexdigest()


def normalize_table(text: str) -> str:
    """Mask random ids and round float cells, so an exported table hashes the same on every OS.

    Splits on commas and runs of whitespace (the CSV and the Deep-LASI text both qualify);
    a float cell keeps :data:`TABLE_SIGNIFICANT` significant digits. Line structure and the
    order of cells are kept, so a moved column or a lost row still changes the hash.
    """
    out: list[str] = []
    for line in text.splitlines():
        cells = re.split(r"(,|\s+)", line)
        norm: list[str] = []
        for cell in cells:
            if _RANDOM_ID.match(cell):
                norm.append("<id>")
            elif _FLOAT_CELL.match(cell) and ("." in cell or "e" in cell.lower() or "n" in cell):
                norm.append(format(float(cell), f".{TABLE_SIGNIFICANT}g"))
            else:
                norm.append(cell)
        out.append("".join(norm))
    return "\n".join(out) + "\n"


def _table_sha(path: Path) -> str:
    return hashlib.sha256(normalize_table(path.read_text(encoding="utf-8")).encode()).hexdigest()


# --- inputs ------------------------------------------------------------------------------


def correction_traces(seed: int = 702) -> tuple[np.ndarray, np.ndarray]:
    """``(36, 300)`` donor/acceptor traces that exercise every correction stage.

    A two-state :func:`_synth.fret_traces` chain (E = 0.3 / 0.7, sigma = 40), then three
    groups of twelve: the acceptor bleaches first (a donor-only tail of 22 to 59 frames with
    leakage 0.08 and a donor level of 1100, then the donor bleaches), the donor bleaches
    (both channels drop to noise), or nothing bleaches. Only elementwise arithmetic on the
    generator's own noise, so the bytes are the same on every OS.
    """
    from _synth import fret_traces  # noqa: PLC0415

    n, t = 36, 300
    tr = fret_traces(n, t, seed=seed, efficiency=(0.3, 0.7), noise_sigma=40.0)
    level = tr.efficiency[tr.states]
    noise_d = tr.donor - 1000.0 * (1.0 - level)
    noise_a = tr.acceptor - 1000.0 * level
    donor = tr.donor.copy()
    acceptor = tr.acceptor.copy()
    rng = np.random.default_rng(seed + 1)
    for m in range(n):
        if m % 3 == 0:
            acc_bleach = int(rng.integers(120, 200))
            don_bleach = int(rng.integers(acc_bleach + 22, acc_bleach + 60))
            donor[m, acc_bleach:] = 1100.0 + noise_d[m, acc_bleach:]
            acceptor[m, acc_bleach:] = 0.08 * 1100.0 + noise_a[m, acc_bleach:]
        elif m % 3 == 1:
            don_bleach = int(rng.integers(150, 280))
        else:
            continue
        donor[m, don_bleach:] = noise_d[m, don_bleach:]
        acceptor[m, don_bleach:] = noise_a[m, don_bleach:]
    return donor, acceptor


def _molecule_quantities(path: Path, q: dict[str, Any]) -> None:
    """Per-molecule summary statistics of an extracted and corrected store."""
    from tether.fret import apparent_fret, corrected_fret  # noqa: PLC0415
    from tether.imaging.extract import read_molecules, read_traces  # noqa: PLC0415

    mols = read_molecules(path)
    traces = read_traces(path)
    donor = np.asarray(traces["donor_corrected"], dtype="float64")
    acceptor = np.asarray(traces["acceptor_corrected"], dtype="float64")
    q["molecules.count"] = int(mols.shape[0])
    q["molecules.donor_xy"] = _plain(np.asarray(mols["donor_xy"], dtype="float64"))
    q["molecules.acceptor_xy"] = _plain(np.asarray(mols["acceptor_xy"], dtype="float64"))
    q["molecules.bleach_frames"] = _plain(np.asarray(mols["bleach_frames"], dtype="int64"))
    q["molecules.analysis_window"] = _plain(np.asarray(mols["analysis_window"], dtype="int64"))
    q["molecules.correction_method"] = [
        v.decode() if isinstance(v, bytes) else str(v) for v in mols["correction_method"]
    ]
    alpha = np.asarray(mols["alpha"], dtype="float64")
    gamma = np.asarray(mols["gamma"], dtype="float64")
    q["molecules.alpha"] = _plain(alpha)
    q["molecules.gamma"] = _plain(gamma)
    mean_d, mean_a, mean_e, mean_ce = [], [], [], []
    for i in range(mols.shape[0]):
        lo, hi = (int(v) for v in mols["analysis_window"][i])
        if hi <= lo:
            lo, hi = (int(v) for v in mols["frame_range"][i])
        d, a = donor[i, lo:hi], acceptor[i, lo:hi]
        mean_d.append(float(np.mean(d)))
        mean_a.append(float(np.mean(a)))
        mean_e.append(float(np.nanmean(apparent_fret(d, a))))
        if np.isfinite(alpha[i]) and np.isfinite(gamma[i]):
            mean_ce.append(float(np.nanmean(corrected_fret(d, a, alpha=alpha[i], gamma=gamma[i]))))
        else:
            mean_ce.append(math.nan)
    q["molecules.mean_donor"] = _plain(mean_d)
    q["molecules.mean_acceptor"] = _plain(mean_a)
    q["molecules.mean_apparent_e"] = _plain(mean_e)
    q["molecules.mean_corrected_e"] = _plain(mean_ce)


def _analysis_quantities(path: Path, q: dict[str, Any]) -> None:
    """The pre-idealization population views: histogram, cross-correlation, FRET cloud."""
    from tether.analysis.cloud import population_raw_fret_cloud  # noqa: PLC0415
    from tether.analysis.crosscorr import population_cross_correlation  # noqa: PLC0415
    from tether.analysis.histogram import population_apparent_e_histogram  # noqa: PLC0415

    h = population_apparent_e_histogram(path, density=False)
    q["histogram.counts"] = _plain(np.asarray(h.counts, dtype="int64"))
    q["histogram.n_samples"] = int(h.n_samples)
    q["histogram.n_molecules"] = int(h.n_molecules or 0)
    c = population_cross_correlation(path)
    q["crosscorr.lag0"] = _plain(c.lag0)
    q["crosscorr.lag1_magnitude"] = _plain(c.lag1_magnitude)
    q["crosscorr.values"] = _plain(c.values)
    cl = population_raw_fret_cloud(path)
    q["cloud.n_samples"] = int(cl.n_samples)
    q["cloud.n_out_of_range"] = int(cl.n_out_of_range)
    q["cloud.hdr_levels"] = _plain(cl.hdr_levels)
    q["cloud.bandwidth"] = _plain(cl.bandwidth)
    dens = np.asarray(cl.density, dtype="float64")
    q["cloud.density_sum"] = _plain(float(np.sum(dens)))
    q["cloud.density_max"] = _plain(float(np.max(dens)))


def _idealize_natively(path: Path, model_name: str, nstates: int) -> dict[str, Any]:
    """Fit the native Gaussian HMM to each molecule's windowed apparent E and store the paths.

    The sidecar fit is out of scope (its random starts need parity limits, not goldens), so
    the state paths come from :func:`tether.analysis.kinetics.fit_gaussian_hmm`, which has a
    deterministic start. The stored model carries the real provenance hashes, so every
    molecule reads back fresh.
    """
    from _analysis_store import fresh_input_hashes  # noqa: PLC0415
    from tether.analysis.kinetics import fit_gaussian_hmm, viterbi_paths  # noqa: PLC0415
    from tether.fret import apparent_fret  # noqa: PLC0415
    from tether.idealize import NO_STATE  # noqa: PLC0415
    from tether.imaging.extract import read_molecules, read_traces  # noqa: PLC0415
    from tether.project.idealize import write_idealization_model  # noqa: PLC0415

    mols = read_molecules(path)
    traces = read_traces(path)
    donor = np.asarray(traces["donor_corrected"], dtype="float64")
    acceptor = np.asarray(traces["acceptor_corrected"], dtype="float64")
    n, t = donor.shape
    windows = []
    series = []
    for i in range(n):
        lo, hi = (int(v) for v in mols["analysis_window"][i])
        windows.append((lo, hi))
        series.append(apparent_fret(donor[i, lo:hi], acceptor[i, lo:hi]))
    hmm = fit_gaussian_hmm(series, nstates)
    paths = viterbi_paths(series, hmm)
    state_paths = np.full((n, t), NO_STATE, dtype="int64")
    idealized = np.full((n, t), np.nan)
    for i, ((lo, hi), p) in enumerate(zip(windows, paths, strict=True)):
        state_paths[i, lo:hi] = p
        idealized[i, lo:hi] = hmm.means[p]

    def text(v: object) -> str:
        return v.decode() if isinstance(v, bytes) else str(v)

    write_idealization_model(
        path,
        model_name=model_name,
        model_type="native-gaussian-hmm",
        nstates=nstates,
        dtype="FRET",
        means=np.asarray(hmm.means),
        variances=np.asarray(hmm.sigmas) ** 2,
        tmatrix=np.asarray(hmm.tmatrix),
        norm_tmatrix=None,
        elbo=None,
        idealized=idealized,
        state_paths=state_paths,
        molecule_keys=[text(k) for k in mols["molecule_key"]],
        molecule_ids=[text(k) for k in mols["molecule_id"]],
        input_hashes=fresh_input_hashes(path),
        intensity_quantity="corrected",
        selected_by="fixed",
        elbo_by_nstates=None,
        app_version="golden",
        created_utc="2026-01-01T00:00:00Z",
        overwrite=True,
    )
    return {
        "hmm.means": _plain(hmm.means),
        "hmm.sigmas": _plain(hmm.sigmas),
        "hmm.tmatrix": _plain(hmm.tmatrix),
        "hmm.log_likelihood": _plain(hmm.log_likelihood),
        "hmm.n_iter": int(hmm.n_iter),
        "hmm.converged": bool(hmm.converged),
        "hmm.state_paths_sha256": _sha256_ints(state_paths),
    }


def _idealized_quantities(path: Path, model: str, q: dict[str, Any]) -> None:
    """The views of a stored idealization: TDP, dwell times, transition probability, states."""
    from tether.analysis.dwell import population_dwell_times  # noqa: PLC0415
    from tether.analysis.state_number import population_state_number  # noqa: PLC0415
    from tether.analysis.tdp import population_transition_density  # noqa: PLC0415
    from tether.analysis.transition_prob import (  # noqa: PLC0415
        population_transition_prob_histogram,
    )

    tdp = population_transition_density(path, model)
    q["tdp.n_transitions"] = int(tdp.n_transitions)
    q["tdp.n_molecules"] = int(tdp.n_molecules)
    q["tdp.counts_sha256"] = _sha256_ints(np.rint(np.asarray(tdp.counts)).astype("int64"))
    dwells = population_dwell_times(path, model)
    for state in sorted(dwells):
        a = dwells[state]
        key = f"dwell.state{state}"
        q[f"{key}.n_dwells"] = int(a.n_dwells)
        q[f"{key}.n_molecules"] = int(a.n_molecules)
        q[f"{key}.lengths_sha256"] = _sha256_ints(np.asarray(a.dwell_lengths))
        q[f"{key}.level"] = _plain(a.level)
        if a.fit is not None:
            q[f"{key}.fit_success"] = bool(a.fit.success)
            q[f"{key}.rate"] = _plain(a.fit.rates)
            q[f"{key}.rate_ci"] = _plain(a.fit.rate_ci)
    tp = population_transition_prob_histogram(path, model, 0, 1, density=False, kde=False)
    q["transition_prob.0to1.counts"] = _plain(np.rint(tp.counts).astype("int64"))
    q["transition_prob.0to1.probabilities"] = _plain(tp.probabilities)
    sn = population_state_number(path, model)
    q["state_number.counts"] = _plain(np.asarray(sn.counts, dtype="int64"))
    q["state_number.n_in_range"] = int(sn.n_in_range)


def _export_quantities(path: Path, workdir: Path, q: dict[str, Any]) -> None:
    """Checksums of the two exported tables, after :func:`normalize_table`."""
    from tether.project.export import (  # noqa: PLC0415
        export_deeplasi_txt,
        export_molecule_table_csv,
    )

    csv = export_molecule_table_csv(path, workdir / "molecules.csv")
    q["export.molecule_table.rows"] = int(csv.n_molecules)
    q["export.molecule_table.sha256"] = _table_sha(csv.path)
    txt = export_deeplasi_txt(path, workdir / "deeplasi.txt")
    q["export.deeplasi_txt.rows"] = int(txt.n_molecules)
    q["export.deeplasi_txt.sha256"] = _table_sha(txt.path)


def _correct(path: Path, q: dict[str, Any]) -> None:
    from tether.project.batch import run_correct_stage  # noqa: PLC0415

    q["correct.detail"] = run_correct_stage(path)


def synthetic_traces_store(workdir: Path, q: dict[str, Any] | None = None) -> Path:
    """A ``.tether`` of :func:`correction_traces`, corrected; the base of ``synthetic_traces``.

    Shared with the GUI check (``tests/test_golden_gui.py``), which draws this store's
    histogram and compares the drawn values with the golden file.
    """
    from _analysis_store import build_store_with_channels  # noqa: PLC0415

    donor, acceptor = correction_traces()
    build_store_with_channels(workdir, donor, acceptor, name="traces.tether")
    path = workdir / "traces.tether"
    _correct(path, {} if q is None else q)
    return path


def run_synthetic_traces(workdir: Path) -> dict[str, Any]:
    """Synthetic traces (:func:`correction_traces`) through correct, analyse, idealize, export."""
    q: dict[str, Any] = {}
    path = synthetic_traces_store(workdir, q)
    _molecule_quantities(path, q)
    _analysis_quantities(path, q)
    q.update(_idealize_natively(path, "golden", 2))
    _idealized_quantities(path, "golden", q)
    _export_quantities(path, workdir, q)
    return q


def run_synthetic_movie(workdir: Path) -> dict[str, Any]:
    """The #701 synthetic movie through the CLI (``tether batch --no-idealize``), then analyse.

    The CLI runs extract and correct; the population views and the exports then read the
    project it wrote.
    """
    import contextlib  # noqa: PLC0415
    import io  # noqa: PLC0415

    from _synth import two_channel_movie, write_movie_tiff  # noqa: PLC0415
    from tether.cli import main  # noqa: PLC0415

    q: dict[str, Any] = {}
    movie = write_movie_tiff(
        two_channel_movie(seed=702, noise_sigma=20.0, n_frames=60), workdir / MOVIE_NAME
    )
    out_dir = workdir / "batch"
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        q["cli.batch.exit_code"] = main(["batch", str(movie), "-d", str(out_dir), "--no-idealize"])
    projects = sorted(out_dir.glob("*.tether"))
    q["cli.batch.projects"] = len(projects)
    path = projects[0]
    _molecule_quantities(path, q)
    _analysis_quantities(path, q)
    _export_quantities(path, workdir, q)
    return q


def run_fixture_smd(workdir: Path) -> dict[str, Any]:
    """The committed 4-molecule tMAVEN SMD (``smd_4mol.hdf5``) imported, then the full chain.

    An analysis-only import (no movie, no coordinates) of real traces: correct, analyse,
    idealize natively, export.
    """
    from tether.idealize import read_smd  # noqa: PLC0415
    from tether.project.analysis_import import import_analysis_only_project  # noqa: PLC0415

    q: dict[str, Any] = {}
    path = workdir / "smd.tether"
    summary = import_analysis_only_project(
        path, source=read_smd(FIXTURES / "smd_4mol.hdf5"), source_name="smd_4mol.hdf5"
    )
    q["import.n_molecules"] = int(summary.n_molecules)
    q["import.n_frames"] = int(summary.n_frames)
    _correct(path, q)
    _molecule_quantities(path, q)
    _analysis_quantities(path, q)
    q.update(_idealize_natively(path, "golden", 2))
    _idealized_quantities(path, "golden", q)
    _export_quantities(path, workdir, q)
    return q


#: Golden-file name -> the pipeline that produces it.
INPUTS: dict[str, Callable[[Path], dict[str, Any]]] = {
    "fixture_smd": run_fixture_smd,
    "synthetic_movie": run_synthetic_movie,
    "synthetic_traces": run_synthetic_traces,
}


def run_input(name: str, workdir: Path) -> dict[str, Any]:
    """Run one input in ``workdir`` (an empty directory) and return its JSON-ready quantities."""
    workdir.mkdir(parents=True, exist_ok=True)
    return {k: _plain(v) for k, v in INPUTS[name](workdir).items()}


# --- golden files ------------------------------------------------------------------------


def golden_path(name: str, directory: Path = GOLDEN_DIR) -> Path:
    return directory / f"{name}.json"


def load_golden(name: str, directory: Path = GOLDEN_DIR) -> dict[str, Any] | None:
    """The quantities of a golden file, or ``None`` when there is none yet."""
    path = golden_path(name, directory)
    if not path.is_file():
        return None
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("format") != GOLDEN_FORMAT:
        raise ValueError(f"{path.name}: golden format {doc.get('format')!r} != {GOLDEN_FORMAT}")
    return dict(doc["quantities"])


def dump_golden(name: str, quantities: Mapping[str, Any], directory: Path = GOLDEN_DIR) -> Path:
    """Write a golden file: sorted keys, one quantity per line, ``\\n`` line ends."""
    doc = {
        "format": GOLDEN_FORMAT,
        "input": name,
        "note": "Regenerate with `python scripts/update_golden.py`; never edit by hand (#702).",
        "quantities": {k: quantities[k] for k in sorted(quantities)},
    }
    path = golden_path(name, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(doc, indent=1, sort_keys=False, allow_nan=False)
    path.write_bytes((body + "\n").encode("utf-8"))
    return path


@dataclass(frozen=True)
class Difference:
    """One quantity whose value moved: ``old`` / ``new`` (``None`` = absent) and the change."""

    quantity: str
    old: Any
    new: Any
    relative: float | None  # largest relative change of its floats; None when not numeric


def _flatten(value: Any) -> list[Any]:
    if isinstance(value, list):
        return [x for v in value for x in _flatten(v)]
    return [value]


def _shape(value: Any) -> Any:
    if isinstance(value, list):
        return [_shape(v) for v in value]
    return type(value).__name__ if not isinstance(value, bool | int | float) else "num"


def _relative(old: Any, new: Any) -> float | None:
    """Largest elementwise relative change between two equally shaped numeric values."""
    a, b = _flatten(old), _flatten(new)
    if len(a) != len(b):
        return None
    worst = 0.0
    for x, y in zip(a, b, strict=True):
        if isinstance(x, bool) or isinstance(y, bool) or isinstance(x, str) or isinstance(y, str):
            if x != y:
                return None
            continue
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            return None
        if x == y:
            continue
        scale = max(abs(x), abs(y))
        worst = max(worst, abs(x - y) / scale)
    return worst


def _equal(old: Any, new: Any, rtol: float) -> bool:
    a, b = _flatten(old), _flatten(new)
    if len(a) != len(b) or _shape(old) != _shape(new):
        return False
    for x, y in zip(a, b, strict=True):
        if isinstance(x, float) or isinstance(y, float):
            if isinstance(x, bool) or isinstance(y, bool):
                return False
            if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                return False
            if isinstance(x, int) != isinstance(y, int):
                return False
            if x != y and abs(x - y) > rtol * max(abs(x), abs(y)):
                return False
        elif x != y or type(x) is not type(y):
            return False
    return True


def compare(
    golden: Mapping[str, Any], actual: Mapping[str, Any], rtol: float = FLOAT_RTOL
) -> list[Difference]:
    """Every quantity that differs: added, removed, or moved beyond the policy."""
    diffs: list[Difference] = []
    for key in sorted(set(golden) | set(actual)):
        old, new = golden.get(key), actual.get(key)
        if key not in golden or key not in actual or not _equal(old, new, rtol):
            rel = _relative(old, new) if key in golden and key in actual else None
            diffs.append(Difference(key, old if key in golden else None, new, rel))
    return diffs


def _short(value: Any, width: int = 48) -> str:
    text = "(absent)" if value is None else json.dumps(value)
    return text if len(text) <= width else text[: width - 1] + "…"


#: Header of the table :func:`diff_table` prints and the golden-table check looks for.
TABLE_HEADER = "| golden file | quantity | old | new | relative change |"


def diff_table(changes: Mapping[str, list[Difference]]) -> str:
    """The Markdown old/new table a pull request that changes a golden file must carry."""
    lines = [TABLE_HEADER, "|---|---|---|---|---|"]
    for name in sorted(changes):
        for d in changes[name]:
            rel = "n/a" if d.relative is None else f"{d.relative:.3e}"
            row = [f"`{name}.json`", f"`{d.quantity}`", _short(d.old), _short(d.new), rel]
            lines.append("| " + " | ".join(c.replace("|", "\\|") for c in row) + " |")
    return "\n".join(lines)

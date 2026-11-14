# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run PySMACKS once on its own tutorial traces and write the result as a reference file (#720).

The PySMACKS tutorial record (Zenodo 10.5281/zenodo.21793247, CC0-1.0) holds a traces file in
OpenFRET JSON with trace labels, but no FRET values and no hidden-Markov results: no trace has
the ``HMM_param`` entry that PySMACKS writes after a kinetic analysis. This script makes that
missing result once, offline, with the published PySMACKS, and writes it to
``tests/fixtures/pysmacks_tutorial_reference.json``. PySMACKS never runs in CI (maintainer
decision of 2026-11-13 on #720; R7: A of #695 keeps it out of the test jobs).

What it runs, all PySMACKS 1.0.0b4 code except the one line marked below:

1. ``openfret.read_data`` reads the JSON, and ``PySMACKS.GUI.GUI_SEhist.extract_labelled_traces``
   keeps the 52 traces labelled ``FRET``, cut to their labelled frames and background-subtracted
   (the traces say ``"Median"``, which that function subtracts frame by frame).
2. The apparent FRET efficiency of each frame, ``DA / (DD + DA)``. This is the expression of
   ``GUI_calculate_FRET_eff`` at its identity factors (alpha = delta = 0, gamma = 1), written out
   here because that function is a method of the PySMACKS window. Non-finite frames are dropped,
   as it drops them. Stoichiometry is not computed: PRD §2.2 rule 1 lets a test use the
   donor-excitation channels only.
3. ``TbT_HMM_pipeline`` (two states, a 2-D Gaussian HMM on DD and DA per trace, 10 fixed seeds)
   and ``Ens_HMM`` (one shared transition matrix over every valid trace), as the Kinetics tab
   runs them.

It writes the per-frame histogram of step 2 in the bins Tether's test reads with, the ensemble
transition matrix of step 3 with its state order, and the versions of everything that ran.
Run it in an environment of its own, never Tether's (PySMACKS needs PyQt5 and NumPy >= 2)::

    python -m venv .venv-pysmacks
    .venv-pysmacks/bin/python -m pip install "PySMACKS==1.0.0b4"
    QT_QPA_PLATFORM=offscreen MPLBACKEND=Agg .venv-pysmacks/bin/python \\
        scripts/make_pysmacks_reference.py --source <dir with the .json.zip>
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import sys
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

ZIP_NAME = "PySMACKS_tutorial_traces_dataset.json.zip"
ZIP_SHA256 = "2c19091c55326b1eadda42b0e99c8a8c6123f92f9ecccafae176db7fbcb3da59"
DOI = "10.5281/zenodo.21793247"
OUT = Path("tests/fixtures/pysmacks_tutorial_reference.json")
PYSMACKS_VERSION = "1.0.0b4"
#: The histogram bins: Tether's default count over a range wider than Tether's default. A few
#: frames with a near-zero total intensity lie outside it; the file counts them.
BINS = 151
VALUE_RANGE = (-0.5, 1.5)
NSTATES = 2
PACKAGES = ("PySMACKS", "openfret", "hmmlearn", "numpy", "scipy", "scikit-learn")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(source: Path, out: Path = OUT) -> dict:
    from openfret import read_data
    from PySMACKS.components.HMM_utils import Ens_HMM, TbT_HMM_pipeline
    from PySMACKS.GUI.GUI_SEhist import extract_labelled_traces

    versions = {name: importlib.metadata.version(name) for name in PACKAGES}
    if versions["PySMACKS"] != PYSMACKS_VERSION:
        raise SystemExit(f"needs PySMACKS {PYSMACKS_VERSION}, found {versions['PySMACKS']}")
    zip_path = source / ZIP_NAME
    if _sha256(zip_path) != ZIP_SHA256:
        raise SystemExit(f"{zip_path}: SHA-256 is not the one of Zenodo record {DOI}")

    with zipfile.ZipFile(zip_path) as archive, tempfile.TemporaryDirectory() as tmp:
        (member,) = archive.namelist()
        raw = read_data(archive.extract(member, tmp))
    fret = extract_labelled_traces(raw, "FRET")

    per_frame = []
    traces = []
    for trace in fret.traces:
        dd = np.asarray(trace.channels[0].data, dtype=np.float64)
        da = np.asarray(trace.channels[1].data, dtype=np.float64)
        e = da / (dd + da)  # GUI_calculate_FRET_eff at alpha = delta = 0, gamma = 1
        per_frame.append(e[np.isfinite(e)])
        traces.append({"uuid": trace.metadata["UUID_v7"], "frames": int(dd.size)})
    values = np.concatenate(per_frame)
    counts, edges = np.histogram(values, bins=BINS, range=VALUE_RANGE)

    fitted, _, _, valid = TbT_HMM_pipeline(fret, nb_hidden_states=NSTATES)
    valid_index = [i for i, ok in enumerate(valid) if ok == 1]
    fitted, ensemble = Ens_HMM(fitted, NSTATES, valid_index)
    # TbT_init orders the states from low to high FRET (state 0 starts at the highest donor
    # and the lowest acceptor mean); check it on the fitted means rather than assume it.
    means = np.median([fitted.traces[k].metadata["HMM_param"]["means"] for k in valid_index], 0)
    state_e = (means[:, 1] / (means[:, 0] + means[:, 1])).tolist()
    if not state_e[0] < state_e[1]:
        raise SystemExit(f"state 0 is not the low-FRET state: {state_e}")

    reference = {
        "format": "pysmacks-reference",
        "version": 1,
        "source": {
            "doi": DOI,
            "file": ZIP_NAME,
            "member": member,
            "sha256": ZIP_SHA256,
            "license": "CC0-1.0",
        },
        "generator": {
            "script": "scripts/make_pysmacks_reference.py",
            "run_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "packages": versions,
        },
        "label": "FRET",
        "traces": traces,
        "histogram": {
            "quantity": "apparent FRET DA / (DD + DA), background-subtracted, per frame",
            "bins": BINS,
            "range": list(VALUE_RANGE),
            "n_frames": int(values.size),
            "n_outside_range": int(values.size - counts.sum()),
            "counts": counts.astype(int).tolist(),
            "edges_check": [float(edges[0]), float(edges[-1])],
        },
        "hmm": {
            "nstates": NSTATES,
            "method": "TbT_HMM_pipeline (10 seeds per trace) then Ens_HMM",
            "n_valid_traces": len(valid_index),
            "state_apparent_e": state_e,
            "transition_matrix_per_frame": np.asarray(ensemble).tolist(),
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(reference, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {out}: {len(traces)} traces, {values.size} frames, {len(valid_index)} valid fits")
    return reference


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, required=True, help=f"directory with {ZIP_NAME}")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    build(args.source, args.out)
    sys.exit(0)


if __name__ == "__main__":
    main()

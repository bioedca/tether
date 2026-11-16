# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Agreement with Deep-LASI on more acquisitions than video 010, on the workstation (#715).

Three checks, all on private laboratory data, so every test here is a ``workstation`` test
(ADR-0071) that only the workstation runner starts. A result file keeps summary numbers under
the neutral ids; an assertion message names ids and quantities, never a file.

* **Spots and traces** (:data:`_deeplasi_agreement.EXTRACTION_IDS`): Tether extracts the movie
  with the series map file and the detection settings of the maintainer's Deep-LASI state, and
  the oracle (a) scorer compares it with the maintainer's Deep-LASI export, with the limits of
  ADR-0022. ADR-0115 makes these acquisitions part of the oracle: a failure on one of them is a
  finding to report, never a reason to change a limit.
* **Corrected FRET** (:data:`_deeplasi_agreement.FRET_IDS`): Tether's correction chain on the
  traces of each Deep-LASI export, against Deep-LASI's corrected FRET with its own factors in
  the ``(1 + α)`` convention of ADR-0028. The numbers are recorded and only the convention
  parity is asserted: no agreement limit exists, and setting one is a maintainer decision.
* **The scripted rerun of video 010**: the frozen output of
  ``scripts/reference/matlab/deep_lasi.py`` (ADR-0074) is checked against its provenance record,
  and compared with the old export, so that the old export has a repeatable origin.
"""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pytest

import _deeplasi_agreement as agree
from _oracle_a import FILES as ORACLE_A_FILES
from _oracle_a import oracle_a_file

pytestmark = pytest.mark.workstation

_HARNESS = Path(__file__).resolve().parents[1] / "scripts" / "reference" / "matlab" / "harness.py"


def _harness():
    spec = importlib.util.spec_from_file_location("tether_ref_harness", _HARNESS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _finite(numbers: dict) -> dict:
    return {k: v for k, v in numbers.items() if isinstance(v, int) or math.isfinite(v)}


def _mat(private_data, neutral_id: str) -> Path:
    if neutral_id == agree.RERUN_ID:
        return oracle_a_file(private_data, "mat")
    return agree.agreement_file(private_data, neutral_id, "mat")


@pytest.mark.parametrize("neutral_id", agree.EXTRACTION_IDS)
def test_spots_and_traces_meet_the_oracle_a_limits(private_data, neutral_id, tmp_path) -> None:
    pytest.importorskip("scipy")
    pytest.importorskip("skimage")
    pytest.importorskip("h5py")
    pytest.importorskip("tifffile")
    from tether.project.extract import extract_movie  # noqa: PLC0415
    from tether.project.oracle import evaluate_project  # noqa: PLC0415

    movie = private_data.path(neutral_id, "movie")
    tmap, tdat, mat = (
        agree.agreement_file(private_data, neutral_id, r) for r in ("tmap", "tdat", "mat")
    )
    out = tmp_path / f"{neutral_id}.tether"
    summary = extract_movie(movie, out, tmap=tmap, tdat=tdat)
    assert out.is_file(), f"{neutral_id}: extraction wrote no project"
    assert summary.registration_source == "imported", neutral_id

    res = evaluate_project(out, mat, intensity="raw")
    private_data.record(
        neutral_id,
        **_finite(
            {
                "matched_fraction": res.recall,
                "position_rms_px": res.coord_rms_px,
                "donor_pearson_median": res.donor_pearson_median,
                "acceptor_pearson_median": res.acceptor_pearson_median,
                "precision": res.precision,
                "n_ground_truth": res.n_ground_truth,
                "n_extracted": res.n_extracted,
                "n_matched": res.n_matched,
            }
        ),
    )
    # The ADR-0022 gate, unchanged (ADR-0115): recall at 2 px and donor Pearson >= 0.95.
    assert res.meets_recall, f"{neutral_id}: recall {res.recall:.3f} < 0.95 ({res.summary()})"
    assert res.meets_pearson, (
        f"{neutral_id}: donor Pearson {res.donor_pearson_median:.3f} < 0.95 ({res.summary()})"
    )


@pytest.mark.parametrize("neutral_id", agree.FRET_IDS)
def test_corrected_fret_against_the_deeplasi_export(private_data, neutral_id) -> None:
    pytest.importorskip("scipy")
    from tether.io.deeplasi import read_deeplasi_mat  # noqa: PLC0415

    mat = _mat(private_data, neutral_id)
    export = read_deeplasi_mat(mat)
    b, g = agree.read_factors(mat)
    numbers = agree.fret_agreement(export, b, g)
    private_data.record(neutral_id, **_finite(numbers))
    assert numbers["n_compared"] > 0, f"{neutral_id}: no molecule could be compared ({numbers})"
    # ADR-0028: Tether's formula with alpha = b and gamma = g * (1 + b) is Deep-LASI's formula.
    assert numbers["parity_max_abs"] < agree.PARITY_ATOL, (
        f"{neutral_id}: the ADR-0028 convention does not map Deep-LASI's factors onto Tether's "
        f"formula (largest difference {numbers['parity_max_abs']:.3g})"
    )


def test_the_scripted_rerun_of_video_010_repeats_the_old_export(private_data) -> None:
    pytest.importorskip("scipy")
    from tether.io.deeplasi import read_deeplasi_mat  # noqa: PLC0415

    harness = _harness()
    folder = private_data.path(agree.RERUN_ID, "rerun")
    record = harness.verify(folder)
    pin = harness.load_pins()["deep-lasi"]
    assert record["method"] == "matlab-batch" and record["task"] == "extract", agree.RERUN_ID
    assert record["tool"]["commit"] == pin["commit"], f"{agree.RERUN_ID}: rerun not at the pin"
    for role in ("movie", "tmap", "tdat"):
        assert list(record["inputs"][role].values()) == [ORACLE_A_FILES[role][0]], (
            f"{agree.RERUN_ID}: the rerun did not read the {role} of oracle (a)"
        )
    [rerun_mat] = [folder / name for name in record["outputs"] if name.endswith(".mat")]

    import numpy as np  # noqa: PLC0415

    old = read_deeplasi_mat(oracle_a_file(private_data, "mat"))
    new = read_deeplasi_mat(rerun_mat)
    numbers = agree.compare_exports(old, new)
    _, g_new = agree.read_factors(rerun_mat)
    numbers["n_rerun_factors"] = int(np.isfinite(g_new).sum())
    private_data.record(agree.RERUN_ID, **_finite(numbers))
    # Measured 2026-11-16: the same 250 molecules, coordinates and traces as the old export.
    assert numbers["n_matched"] == numbers["n_reference"] == numbers["n_other"], numbers
    assert numbers["position_rms_px"] == 0.0 and numbers["trace_max_rel_diff"] == 0.0, numbers

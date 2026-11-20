# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Agreement with Deep-LASI on more acquisitions than video 010, on the workstation (#715).

Three checks, all on private laboratory data, so every test here is a ``workstation`` test
(ADR-0071) that only the workstation runner starts. A result file keeps summary numbers under
the neutral ids; an assertion message names ids and quantities, never a file. Each Deep-LASI
export is read through its ``window`` role, a frozen copy beside a ``provenance.json`` that says
Deep-LASI's window saved it and on which date (``harness.py freeze``, ADR-0074).

* **Spots and traces** (:data:`_deeplasi_agreement.EXTRACTION_IDS`): Tether extracts the movie
  with the series map file and the detection settings of the maintainer's Deep-LASI state, and
  the oracle (a) scorer compares it with the maintainer's Deep-LASI export, with the limits of
  ADR-0022. ADR-0115 makes these acquisitions part of the oracle: a failure on one of them is a
  finding to report, never a reason to change a limit.
* **Corrected FRET** (:data:`_deeplasi_agreement.FRET_IDS`): Tether's correction chain on the
  traces of each Deep-LASI export, against Deep-LASI's corrected FRET with its own factors in
  the ``(1 + α)`` convention of ADR-0028. The numbers are recorded and only the convention
  parity is asserted: no agreement limit exists, and setting one is a maintainer decision.
  The numbers include the shape of the acceptor-bleach step behind each of Tether's γ (#1233,
  :func:`_deeplasi_agreement.gamma_step_numbers`), as evidence for #546 and never a limit.
* **The scripted rerun of video 010**: the frozen output of
  ``scripts/reference/matlab/deep_lasi.py`` (ADR-0074) is checked against its provenance record,
  and compared with the old export, so that the old export has a repeatable origin.
"""

from __future__ import annotations

import math

import pytest

import _deeplasi_agreement as agree
from _oracle_a import FILES as ORACLE_A_FILES

pytestmark = pytest.mark.workstation


def _finite(numbers: dict) -> dict:
    return {k: v for k, v in numbers.items() if isinstance(v, int) or math.isfinite(v)}


@pytest.mark.parametrize("neutral_id", agree.EXTRACTION_IDS)
def test_spots_and_traces_meet_the_oracle_a_limits(private_data, neutral_id, tmp_path) -> None:
    pytest.importorskip("scipy")
    pytest.importorskip("skimage")
    pytest.importorskip("h5py")
    pytest.importorskip("tifffile")
    from tether.project.extract import extract_movie  # noqa: PLC0415
    from tether.project.oracle import evaluate_project  # noqa: PLC0415

    movie = private_data.path(neutral_id, "movie")
    tmap, tdat = (agree.agreement_file(private_data, neutral_id, r) for r in ("tmap", agree.STATE))
    mat = agree.window_export(private_data, neutral_id)
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

    mat = agree.window_export(private_data, neutral_id)
    export = read_deeplasi_mat(mat)
    b, g = agree.read_factors(mat)
    numbers = agree.fret_agreement(export, b, g)
    private_data.record(neutral_id, **_finite(numbers))
    # No limit applies to the comparison (ADR-0115): an empty one is recorded with its cause
    # (no factors in the export, or a factor Tether withholds by its own rule), not failed.
    # The identity is asserted wherever the export has factors, and never vacuously.
    if numbers["n_deeplasi_factors"] == 0:
        return
    assert numbers["n_parity_frames"] > 0, f"{neutral_id}: no frame to check the identity on"
    # ADR-0028: Tether's formula with alpha = b and gamma = g * (1 + b) is Deep-LASI's formula.
    assert numbers["parity_max_abs"] < agree.PARITY_ATOL, (
        f"{neutral_id}: the ADR-0028 convention does not map Deep-LASI's factors onto Tether's "
        f"formula (largest difference {numbers['parity_max_abs']:.3g})"
    )


def test_the_scripted_rerun_of_video_010_repeats_the_old_export(private_data) -> None:
    pytest.importorskip("scipy")
    from tether.io.deeplasi import read_deeplasi_mat  # noqa: PLC0415

    harness = agree.harness()
    rerun_mat = private_data.path(agree.RERUN_ID, "rerun")
    record = harness.verify(rerun_mat.parent)
    pin = harness.load_pins()["deep-lasi"]
    assert record["method"] == "matlab-batch" and record["task"] == "extract", agree.RERUN_ID
    assert record["tool"]["commit"] == pin["commit"], f"{agree.RERUN_ID}: rerun not at the pin"
    for role in ("movie", "tmap", "tdat"):
        assert list(record["inputs"][role].values()) == [ORACLE_A_FILES[role][0]], (
            f"{agree.RERUN_ID}: the rerun did not read the {role} of oracle (a)"
        )
    names = [name for name in record["outputs"] if name.endswith(".mat")]
    assert names == [rerun_mat.name], f"{agree.RERUN_ID}: the rerun role is not the run's export"

    import numpy as np  # noqa: PLC0415

    old = read_deeplasi_mat(agree.window_export(private_data, agree.RERUN_ID))
    new = read_deeplasi_mat(rerun_mat)
    numbers = agree.compare_exports(old, new)
    _, g_new = agree.read_factors(rerun_mat)
    numbers["n_rerun_factors"] = int(np.isfinite(g_new).sum())
    private_data.record(agree.RERUN_ID, **_finite(numbers))
    # Measured 2026-11-16: the same 250 molecules, coordinates and traces as the old export.
    assert numbers["n_matched"] == numbers["n_reference"] == numbers["n_other"], numbers
    # The traces are compared on the shared frames only, so a shorter rerun must fail here.
    assert numbers["n_frames_reference"] == numbers["n_frames_other"], numbers
    assert numbers["position_rms_px"] == 0.0 and numbers["trace_max_rel_diff"] == 0.0, numbers
    assert numbers["acceptor_position_max_px"] == 0.0, numbers

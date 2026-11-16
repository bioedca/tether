# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tether reproduces the published numbers of Campos-Chavez et al. 2024 (#756, ADR-0097).

Nat Commun 15:6592 (2024), doi:10.1038/s41467-024-50885-x. The Source Data archive is the
``campos2024-source-data`` entry of the reference-data manifest (CC BY-NC-ND 4.0, ADR-0083).

* Required matrix: the method of ``_campos_chavez_2024`` on data built here — the rounding bound,
  the window search and the survival-curve inversion.
* Weekly ``reference-data`` job (``reference_download``): the archive is downloaded and verified,
  and each published efficiency, histogram, survival curve, exponential fit and transition density
  count is compared with Tether's. The limits in :data:`LIMITS` were measured on Linux, macOS and
  Windows (ADR-0097 has the numbers) and are frozen.

These are advisory references. The HMM fits of the paper are #757 and its rate bounds are #758.
"""

from __future__ import annotations

import functools
import hashlib
import zipfile

import numpy as np
import pytest

from _campos_chavez_2024 import (
    ARCHIVE_ID,
    DOUBLE_PARAMS,
    HISTOGRAMS,
    INTRAFRET,
    SURVIVAL,
    TDPS,
    TRACE_SHEETS,
    column,
    compare_histogram,
    compare_intrafret,
    compare_survival,
    compare_tdp,
    compare_trace_sheet,
    dwells_from_survival,
    find_windows,
    locate_blocks,
    read_book,
    read_double_params,
    read_run,
    read_traces,
    rounding_bound,
)
from reference_data import fetch
from tether.analysis.dwell import survival_curve
from tether.fret import apparent_fret

#: Frozen limits (ADR-0097). A ``*_bound_ratio`` is the deviation divided by the change that the
#: 8-digit rounding of the archive's intensities can cause, so 1.0 is that bound itself. The other
#: limits are relative to the published value (``model``: to the curve's first point).
LIMITS = {
    "trace_rel": 2e-14,
    "bound_ratio": 1.0,
    "survival_abs": 1e-14,
    "time_rel": 1e-15,
    "model_rel": 2e-5,
    "params_rel": 5e-5,
    "errors_rel": 1e-2,
}


@functools.cache
def _archive():
    return fetch.path_for(ARCHIVE_ID)


@functools.cache
def _book():
    return read_book(_archive())


@functools.cache
def _traces(name: str):
    return read_traces(_archive(), name)


# --- required matrix: the method, on data built here --------------------------------------------


def _rounded(x: np.ndarray) -> np.ndarray:
    return np.array([float(f"{v:.7e}") for v in x.ravel()]).reshape(x.shape)


def test_the_rounding_bound_covers_intensities_written_with_eight_digits() -> None:
    rng = np.random.default_rng(756)
    donor = rng.normal(80.0, 60.0, 20_000)
    acceptor = rng.normal(40.0, 60.0, 20_000)
    d8, a8 = _rounded(donor), _rounded(acceptor)
    exact = apparent_fret(donor, acceptor)
    shown = apparent_fret(d8, a8)
    ratio = np.abs(shown - exact) / rounding_bound(d8, a8)
    assert np.max(ratio) <= 1.0
    assert np.max(ratio) > 0.1  # the bound is not loose by orders of magnitude


def test_the_rounding_bound_holds_near_cancellation_and_is_infinite_at_zero() -> None:
    # Two intensity pairs for which a first-order bound is too small (#1032 review).
    donor = np.array([19.99999950000001, 1.999999951])
    acceptor = np.array([-9.000000049999999, -1.999999949])
    d8, a8 = _rounded(donor), _rounded(acceptor)
    bound = rounding_bound(d8, a8)
    change = np.abs(apparent_fret(d8, a8) - apparent_fret(donor, acceptor))
    assert change[0] <= bound[0] < np.inf
    # 2.0000000 + (-1.9999999): the interval of D + A contains zero, so nothing bounds E.
    assert np.isinf(bound[1])


def test_find_windows_finds_each_window_and_a_value_the_matrix_lost() -> None:
    rng = np.random.default_rng(1)
    donor = rng.uniform(20, 200, (300, 5))
    acceptor = rng.uniform(20, 200, (300, 5))
    e = apparent_fret(donor, acceptor)
    lost = 0.123456  # a frame the published matrix does not hold
    values = np.concatenate([e[10:60, 3], e[0:300, 1], [lost], e[5:9, 0]])
    windows = find_windows(e, rounding_bound(donor, acceptor), values)
    assert windows == [(3, 10, 50), (1, 0, 300), (-1, -1, 1), (0, 5, 4)]


def test_dwells_from_survival_give_back_the_survival_curve() -> None:
    lengths = np.array([1, 1, 2, 3, 3, 3, 7, 12, 12, 40])
    _, surv = survival_curve(lengths)
    got = dwells_from_survival(surv)
    np.testing.assert_array_equal(np.sort(got), np.sort(lengths))


def test_column_reads_down_to_the_first_gap() -> None:
    rows = [(None, "Fig"), ("Time (s)", "x"), (0.0, 1.0), (0.1, 2.0), (None, None), (0.2, 3.0)]
    np.testing.assert_array_equal(column(rows, "Time (s)", 0), [0.0, 0.1])


def test_locate_blocks_joins_runs_in_order_and_searches_every_start(tmp_path) -> None:
    rng = np.random.default_rng(1128)
    runs = [(rng.uniform(50, 150, (1500, n)), rng.uniform(10, 90, (1500, n))) for n in (3, 2)]
    e = [apparent_fret(d, a) for d, a in runs]
    blocks = [e[0][:400, 0], e[0][:400, 1], e[0][:400, 2], e[1][:400, 0], e[1][:400, 1]]
    blocks += [e[1][700:1100, 0], np.full(400, 0.5)]  # found at frame 700; found nowhere
    got = locate_blocks(np.concatenate(blocks), runs)
    assert got["run_molecules"] == [3, 2] and got["column_molecules"] == 7
    assert got["leading_equal"] == 5 and got["max_abs_deviation"] == 0.0
    assert got["unmatched"] == [6]
    # Swapping the order of the runs breaks the join at the first block.
    assert locate_blocks(np.concatenate(blocks), runs[::-1])["leading_equal"] == 0
    # A run file is read as the notebook reads it: the first line (the molecule ids) is skipped.
    path = tmp_path / "run.txt"
    np.savetxt(path, np.vstack([[7, 7], np.column_stack([runs[1][0][:, 0], runs[1][1][:, 0]])]))
    donor, acceptor = read_run(path)
    np.testing.assert_allclose(donor[:, 0], runs[1][0][:, 0], rtol=1e-15)
    assert acceptor.shape == (1500, 1)


def test_every_double_exponential_panel_has_a_committed_parameter_file() -> None:
    manifest = fetch.load_manifest()
    files = {d.extract_member for d in manifest.values() if d.extract_member}
    for name in DOUBLE_PARAMS.values():
        member = f"ErrorCalculations_CodeAndSourceData/ErrorsDoubleExponential/{name}.txt"
        assert member in files, name
        assert SURVIVAL[next(k for k, v in DOUBLE_PARAMS.items() if v == name)] == "double"


# --- weekly: Tether beside the published numbers ------------------------------------------------


@pytest.mark.reference_download
@pytest.mark.parametrize("sheet", TRACE_SHEETS)
def test_representative_traces_have_tethers_efficiency(sheet: str) -> None:
    got = compare_trace_sheet(_book()[sheet])
    assert got["values"] > 0
    assert got["max_rel"] <= LIMITS["trace_rel"], got


@pytest.mark.reference_download
@pytest.mark.parametrize("sheet", sorted(HISTOGRAMS))
def test_each_histogram_value_is_tethers_efficiency_of_one_frame(sheet: str) -> None:
    values = column(_book()[sheet], "FRET Efficiency", 0)
    got = compare_histogram(values, *_traces(HISTOGRAMS[sheet]))
    # Only WT+NCCA_tRNA.txt (1499 frames, the others 1500) loses values, each the last frame.
    assert got["not_found"] == got["not_found_after_last_frame"], got
    if HISTOGRAMS[sheet] != "WT+NCCA_tRNA":
        assert got["not_found"] == 0, got
    assert got["max_bound_ratio"] <= LIMITS["bound_ratio"], got
    assert got["unbounded_values"] == 0, got  # every value is held to a finite bound
    assert got["bins_differing"] == 0, got


@pytest.mark.reference_download
@pytest.mark.parametrize("col", sorted(INTRAFRET))
def test_intrafret_histogram_values_are_tethers_first_400_frames(col: int) -> None:
    values = column(_book()["Fig5b"], "FRET Efficiency", col)
    got = compare_intrafret(values, *_traces(INTRAFRET[col]))
    assert got["molecules"] == got["matrix_molecules"], got
    # Every molecule matches; the files of the notebook start at frame 0, then at frame 1.
    assert [s for s, _ in got["start_groups"]] == [0, 1], got
    assert got["max_bound_ratio"] <= LIMITS["bound_ratio"], got
    assert got["unbounded_values"] == 0, got  # every value is held to a finite bound


@pytest.mark.reference_download
def test_the_archive_has_no_trace_matrix_for_5um_wt_trna() -> None:
    # Fig. 5b's 5 uM WT tRNA column has 163 molecules; the archive's 5 uM file is the 13 uM file.
    with zipfile.ZipFile(_archive()) as zf:
        five, thirteen = (
            hashlib.sha256(zf.read(f"smFRET_SourceData/intraFRET_WT+WT_{c}.txt")).hexdigest()
            for c in ("5uM", "13uM")
        )
    assert five == thirteen
    assert column(_book()["Fig5b"], "FRET Efficiency", 2).size == 163 * 400


@pytest.mark.reference_download
@pytest.mark.parametrize("panel", sorted(SURVIVAL), ids=lambda p: f"{p[0]}-{p[1]}")
def test_survival_curve_and_exponential_fit_reproduce_the_panel(panel: tuple) -> None:
    got = compare_survival(_book()[panel[0]], panel[1], SURVIVAL[panel], None)
    assert got["time_max_rel"] <= LIMITS["time_rel"], got
    assert got["survival_max_abs"] <= LIMITS["survival_abs"], got
    assert got["model_max_rel"] <= LIMITS["model_rel"], got
    assert got["residual_max_rel"] <= LIMITS["model_rel"], got


@pytest.mark.reference_download
@pytest.mark.parametrize("panel", sorted(DOUBLE_PARAMS), ids=lambda p: f"{p[0]}-{p[1]}")
def test_double_exponential_parameters_and_errors(panel: tuple) -> None:
    params = read_double_params(_archive(), DOUBLE_PARAMS[panel])
    got = compare_survival(_book()[panel[0]], panel[1], "double", params)
    assert got["params_max_rel"] <= LIMITS["params_rel"], got
    # The published errors are tMAVEN's, which stay in the optimizer's order after its rates are
    # sorted: Tether's error of each parameter is the published error of the other one.
    assert got["errors_swapped_max_rel"] <= LIMITS["errors_rel"], got
    assert got["errors_as_labelled_max_rel"] > 1.0, got


@pytest.mark.reference_download
@pytest.mark.parametrize("sheet", TDPS)
def test_transition_density_counts_equal_checktdp(sheet: str) -> None:
    got = compare_tdp(_book()[sheet])
    assert got["edges_equal"], got
    assert got["tether_transitions"] == got["transitions"], got
    assert got["counts_differing"] == 0, got

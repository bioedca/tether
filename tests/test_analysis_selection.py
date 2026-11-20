# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""One selection argument and one return shape on every population function (#369).

ADR-0109: the fifteen population entry points of :mod:`tether.analysis` take their
selection through ``molecule_keys``, which also accepts a
:class:`~tether.analysis.selection.MoleculeSelection` (the protocol a frozen
analysis-set version, #934, implements). The six that read ``/idealization`` take
``include_stale``; the nine that read none must not. A selection object selects exactly
what the same keys as a list select — bit-identical results, asserted here for all
fifteen.
"""

from __future__ import annotations

import dataclasses
import inspect
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("h5py")
pytest.importorskip("scipy")

import numpy as np  # noqa: E402

import tether.analysis as ta  # noqa: E402
from _analysis_store import MEANS, build_store_with_model  # noqa: E402
from tether.analysis.selection import MoleculeSelection, resolve_selection  # noqa: E402

REPO = Path(__file__).resolve().parents[1]

#: The population entry points: the fifteen of #369, and the successive-frame cloud (#710).
ENTRY_POINTS = (
    "population_apparent_e_histogram",
    "population_apparent_e_histogram_ci",
    "per_condition_apparent_e_histograms",
    "population_time_signal_histogram2d",
    "population_raw_fret_cloud",
    "population_fret_cloud_alpha_shape",
    "population_fret_cloud_state_number_elbow",
    "population_cross_correlation",
    "population_anticorrelation_events",
    "population_dwell_times",
    "population_state_number",
    "population_transition_density",
    "population_transition_prob_histogram",
    "population_transition_sync_histogram2d",
    "population_model_gaussian_overlay",
    "population_successive_frame_cloud",
)

#: The six that read ``/idealization`` and so take ``include_stale``.
READS_IDEALIZATION = frozenset(
    {
        "population_dwell_times",
        "population_state_number",
        "population_transition_density",
        "population_transition_prob_histogram",
        "population_transition_sync_histogram2d",
        "population_model_gaussian_overlay",
    }
)

#: Why the other nine have no ``include_stale`` — the reason the parameter page gives.
NO_IDEALIZATION_REASON = (
    "the 1-D histogram, the raw time-versus-signal 2-D heatmap and the cloud views "
    "have no idealization to go stale"
)


class _Keys:
    """A minimal :class:`MoleculeSelection`: names fixed keys in any project."""

    def __init__(self, keys: list[str]) -> None:
        self.keys = list(keys)
        self.calls = 0

    def molecule_keys_in(self, project):  # noqa: ANN001 - protocol signature
        self.calls += 1
        return list(self.keys)


def _states() -> np.ndarray:
    return np.array(
        [
            [0, 0, 1, 1, 1, 2, 2, 0, 0, 0, 1, 1, 2, 2, 2, 0, 0, 1, 1, 1],
            [1, 1, 1, 2, 2, 0, 0, 0, 1, 1, 2, 2, 2, 2, 0, 0, 1, 1, 2, 2],
            [2, 2, 0, 0, 1, 1, 1, 2, 2, 2, 0, 0, 0, 1, 1, 2, 2, 0, 0, 1],
        ],
        dtype="int64",
    )


def _call(name: str, project, **kwargs):  # noqa: ANN001, ANN202
    fn = getattr(ta, name)
    if name in {
        "population_dwell_times",
        "population_state_number",
        "population_transition_density",
        "population_transition_sync_histogram2d",
        "population_model_gaussian_overlay",
    }:
        return fn(project, "vbconhmm", **kwargs)
    if name == "population_transition_prob_histogram":
        return fn(project, "vbconhmm", 0, 1, **kwargs)
    if name == "population_anticorrelation_events":
        return fn(project, window=4, **kwargs)
    if name == "population_apparent_e_histogram_ci":
        return fn(project, n_resamples=20, **kwargs)
    if name == "population_fret_cloud_state_number_elbow":
        return fn(project, k_max=3, restarts=2, **kwargs)
    return fn(project, **kwargs)


def _same(a, b) -> bool:  # noqa: ANN001
    """Deep, bit-level equality of two results (NaN equal to NaN)."""
    if dataclasses.is_dataclass(a) and not isinstance(a, type):
        if type(a) is not type(b):
            return False
        return all(_same(getattr(a, f.name), getattr(b, f.name)) for f in dataclasses.fields(a))
    if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
        a, b = np.asarray(a), np.asarray(b)
        return a.shape == b.shape and a.dtype == b.dtype and np.array_equal(a, b, equal_nan=True)
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list | tuple):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, float) and isinstance(b, float) and np.isnan(a) and np.isnan(b):
        return True
    return a == b


def test_the_fifteen_are_every_population_entry_point() -> None:
    # A new population function joins this list (and so the contract) or fails here.
    exported = {
        n for n in ta.__all__ if n.startswith("population_") or n.startswith("per_condition_")
    }
    assert exported == set(ENTRY_POINTS)
    assert len(ENTRY_POINTS) == 16
    assert set(ENTRY_POINTS) >= READS_IDEALIZATION and len(READS_IDEALIZATION) == 6


@pytest.mark.parametrize("name", ENTRY_POINTS)
def test_every_entry_point_takes_molecule_keys(name: str) -> None:
    params = inspect.signature(getattr(ta, name)).parameters
    assert "molecule_keys" in params
    assert params["molecule_keys"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["molecule_keys"].default is None
    assert "include_rejected" in params


@pytest.mark.parametrize("name", ENTRY_POINTS)
def test_include_stale_only_where_idealization_is_read(name: str) -> None:
    params = inspect.signature(getattr(ta, name)).parameters
    if name in READS_IDEALIZATION:
        assert "include_stale" in params
        assert params["include_stale"].default is False
    else:
        assert "include_stale" not in params, f"{name}: {NO_IDEALIZATION_REASON}"


def test_the_absence_reason_is_the_parameter_page() -> None:
    text = (REPO / "docs" / "reference" / "parameters.md").read_text(encoding="utf-8")
    assert NO_IDEALIZATION_REASON in text


@pytest.fixture
def store(tmp_path):  # noqa: ANN001, ANN201
    project, keys = build_store_with_model(tmp_path, _states(), MEANS)
    return project, keys


@pytest.mark.parametrize("name", ENTRY_POINTS)
def test_a_selection_object_selects_what_its_keys_select(store, name: str) -> None:
    project, keys = store
    picked = keys[:2]
    selection = _Keys(picked)
    as_list = _call(name, project, molecule_keys=list(picked))
    as_object = _call(name, project, molecule_keys=selection)
    assert _same(as_list, as_object)
    assert selection.calls >= 1  # the protocol was asked, not bypassed


@pytest.mark.parametrize("name", ENTRY_POINTS)
def test_all_keys_select_what_no_selection_selects(store, name: str) -> None:
    project, keys = store
    assert _same(_call(name, project), _call(name, project, molecule_keys=_Keys(keys)))


def test_selection_is_a_protocol() -> None:
    assert isinstance(_Keys([]), MoleculeSelection)
    assert not isinstance(["a"], MoleculeSelection)


def test_resolve_selection(tmp_path) -> None:
    path = tmp_path / "unused.tether"  # never opened: no protocol object is passed
    assert resolve_selection(None, path) is None
    assert resolve_selection(iter(["a", "b"]), path) == ["a", "b"]
    assert resolve_selection(("a",), path) == ["a"]
    with pytest.raises(TypeError, match="bare str"):
        resolve_selection("m0", path)
    with pytest.raises(TypeError, match="iterable"):
        resolve_selection(3, path)  # type: ignore[arg-type]


def test_a_selection_that_answers_none_is_refused(store) -> None:
    project, _keys = store

    class _Bad:
        def molecule_keys_in(self, project):  # noqa: ANN001, ANN202
            return None

    with pytest.raises(TypeError, match="must return an iterable"):
        ta.population_apparent_e_histogram(project, molecule_keys=_Bad())


def test_an_empty_selection_selects_nothing(store) -> None:
    project, _keys = store
    h = ta.population_apparent_e_histogram(project, molecule_keys=_Keys([]))
    assert h.n_molecules == 0


# --- one return shape ---------------------------------------------------------


def test_dwell_times_return_the_mapping_with_or_without_state(store) -> None:
    project, _keys = store
    every = ta.population_dwell_times(project, "vbconhmm")
    one = ta.population_dwell_times(project, "vbconhmm", state=1)
    assert isinstance(every, dict) and isinstance(one, dict)
    assert list(one) == [1]
    assert _same(one[1], every[1])
    missing = ta.population_dwell_times(project, "vbconhmm", state=9)
    assert list(missing) == [9] and missing[9].n_dwells == 0 and missing[9].fit is None


def test_alpha_shape_is_always_an_alpha_shape(store) -> None:
    project, keys = store
    shape = ta.population_fret_cloud_alpha_shape(project)
    assert isinstance(shape, ta.AlphaShape) and shape.spans_region and not shape.is_empty
    none = ta.population_fret_cloud_alpha_shape(project, molecule_keys=[])
    assert isinstance(none, ta.AlphaShape)
    assert not none.spans_region and none.is_empty and none.n_points == 0
    # "No support at this α" stays what it was: triangles, none kept, no boundary.
    tight = ta.population_fret_cloud_alpha_shape(project, alpha=1e-9)
    assert tight.spans_region and tight.n_kept == 0 and tight.is_empty


# --- the new arguments ----------------------------------------------------------


def test_per_condition_histograms_narrow_by_selection(store) -> None:
    project, keys = store
    every = ta.per_condition_apparent_e_histograms(project)
    assert every.n_conditions == 1 and every.total_molecules == len(keys)
    some = ta.per_condition_apparent_e_histograms(project, molecule_keys=keys[:1])
    assert some.condition_ids == every.condition_ids
    assert some.total_molecules == 1
    ref = ta.population_apparent_e_histogram(project, molecule_keys=keys[:1])
    assert _same(some.histograms[0].histogram, ref)
    nothing = ta.per_condition_apparent_e_histograms(project, molecule_keys=[])
    assert nothing.n_conditions == 0  # no selected molecule -> not overlaid
    np.testing.assert_array_equal(nothing.bin_edges, every.bin_edges)


def test_overlay_numbers_never_depend_on_the_selection(store) -> None:
    project, keys = store
    every = ta.population_model_gaussian_overlay(project, "vbconhmm")
    one = ta.population_model_gaussian_overlay(project, "vbconhmm", molecule_keys=keys[:1])
    assert _same(every, one)


def test_overlay_is_withheld_when_the_model_describes_no_selected_molecule(tmp_path) -> None:
    project, keys = build_store_with_model(
        tmp_path, _states(), MEANS, stale=[True, False, False], rejected=[False, True, False]
    )
    with pytest.raises(ValueError, match="describes no molecule"):
        ta.population_model_gaussian_overlay(project, "vbconhmm", molecule_keys=[])
    # stale: withheld by default, drawn with include_stale
    with pytest.raises(ValueError, match="describes no molecule"):
        ta.population_model_gaussian_overlay(project, "vbconhmm", molecule_keys=keys[:1])
    ta.population_model_gaussian_overlay(
        project, "vbconhmm", molecule_keys=keys[:1], include_stale=True
    )
    # rejected: withheld by default, drawn with include_rejected
    with pytest.raises(ValueError, match="describes no molecule"):
        ta.population_model_gaussian_overlay(project, "vbconhmm", molecule_keys=keys[1:2])
    ta.population_model_gaussian_overlay(
        project, "vbconhmm", molecule_keys=keys[1:2], include_rejected=True
    )


def test_sync_heatmap_excludes_stale_fits_by_default(tmp_path) -> None:
    project, keys = build_store_with_model(tmp_path, _states(), MEANS, stale=[True, False, False])
    fresh = ta.population_transition_sync_histogram2d(project, "vbconhmm")
    with_stale = ta.population_transition_sync_histogram2d(project, "vbconhmm", include_stale=True)
    only_fresh = ta.population_transition_sync_histogram2d(
        project, "vbconhmm", molecule_keys=keys[1:], include_stale=True
    )
    assert fresh.n_molecules == 2 and with_stale.n_molecules == 3
    assert _same(fresh, only_fresh)

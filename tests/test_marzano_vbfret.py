# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Measured traces with a stored vbFRET path as a dwell and transition reference (#797, ADR-0090).

Two condition folders of measured two-colour traces by Marzano et al. (CC-BY-4.0) carry the
per-trace vbFRET_nov12 idealization the authors analysed: PPR-RNA ``RNAanticognate`` (114 traces,
Nucleic Acids Res. 2024) and Hsp70/Hsp90 ``KJG_231027`` (68 traces, Sci. Adv. 2026). The authors'
own analysis code, ``nmarzano/smFRET_module`` v1.0.3 (MIT), was run once outside the product on
the same bytes (``scripts/make_marzano_fixture.py tables``); its dwell and transition tables are
committed under ``tests/fixtures/reference/marzano/``.

These tests hold Tether's dwell extraction (:func:`tether.analysis.dwell.state_dwells` and the
store entry point :func:`~tether.analysis.dwell.population_dwell_times`) and its transition
density (:func:`~tether.analysis.tdp.population_transition_density`) to those tables: the same
state values, the same durations in frames, in the same order. Where the two programs define a
thing differently the test names the difference and goes through it:

* **The last dwell.** smFRET_module's dwell table keeps each molecule's last run; Tether drops
  it (it is right-censored). Its transition table has one row per run *except* the last, which
  is exactly Tether's ``include_first=True`` set.
* **The first dwell.** Tether drops it by default (its start was not observed); so does
  smFRET_module's own ``cleanup_dwell(first_dwell="delete")`` default before its dwell fits.
* **Out-of-range frames.** smFRET_module drops each frame whose raw FRET is not inside
  (-0.5, 1.5) and joins what is left. Here those frames are ``NO_STATE``, which
  ``state_dwells`` also drops and joins, so dwells agree. The TDP differs: Tether reads a
  ``NO_STATE`` gap as an unobserved transition and counts none across it, smFRET_module counts
  the change. Two PPR transitions cross such a gap; none of the chaperone set do.
* **A molecule with one run** gives no dwell and no transition in either program.

The required matrix runs this module on all three operating systems: the fixtures are plain git
files (``tests/reference_data/manifest.toml``), and only the standard library, NumPy and h5py
are needed.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("h5py")

from _analysis_store import build_store_with_model  # noqa: E402
from _marzano_dat import SETS, DatTrace, global_levels, read_set, state_path  # noqa: E402
from reference_data.fetch import path_for  # noqa: E402
from tether.analysis.dwell import population_dwell_times, state_dwells  # noqa: E402
from tether.analysis.tdp import population_transition_density  # noqa: E402
from tether.idealize import NO_STATE  # noqa: E402

TABLES = Path(__file__).parent / "fixtures" / "reference" / "marzano"
#: The traces and transitions of each set, as smFRET_module counted them (smfret_module_run.json).
COUNTS = {"ppr": (114, 640), "chaperone": (68, 465)}
#: The PPR transitions whose two runs a dropped (out-of-range) frame separates.
GAP_TRANSITIONS = {"ppr": 2, "chaperone": 0}
#: The TDP grid of the comparison: wide enough for every level, 0.01 wide bins.
TDP_RANGE, TDP_BINS = (-0.5, 1.5), 200


def _molecule_id(set_id: str, trace: DatTrace) -> str:
    """smFRET_module's ``unique_id``: molecule number, treatment name and repeat 1."""
    return f"{trace.molecule}_{SETS[set_id][2]}_1"


def _table(set_id: str, kind: str) -> dict[str, list[dict[str, str]]]:
    rows = defaultdict(list)
    with (TABLES / f"{set_id}_{kind}.csv").open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rows[row["Molecule"]].append(row)
    return rows


def _runs(path: np.ndarray) -> list[np.ndarray]:
    """The runs of constant state after the ``NO_STATE`` frames are dropped (the shared rule)."""
    s = path[path != NO_STATE]
    return np.split(s, np.flatnonzero(np.diff(s) != 0) + 1) if s.size else []


@pytest.fixture(scope="module", params=sorted(SETS))
def ref(request) -> dict:
    set_id = request.param
    traces = read_set(path_for(SETS[set_id][1]))
    levels = global_levels(traces)
    paths = [state_path(t, levels, no_state=NO_STATE) for t in traces]
    return {
        "id": set_id,
        "traces": traces,
        "levels": levels,
        "paths": paths,
        "dwells": _table(set_id, "dwells"),
        "transitions": _table(set_id, "transitions"),
    }


@pytest.fixture(scope="module")
def store(ref, tmp_path_factory) -> dict:
    """A ``.tether`` whose ``/idealization`` holds every trace's stored vbFRET path."""
    n_frames = max(p.size for p in ref["paths"])
    matrix = np.full((len(ref["paths"]), n_frames), NO_STATE, dtype=np.int64)
    for i, p in enumerate(ref["paths"]):
        matrix[i, : p.size] = p
    windows = [(0, p.size) for p in ref["paths"]]
    tmp = tmp_path_factory.mktemp(f"marzano_{ref['id']}")
    project, _ = build_store_with_model(
        tmp, matrix, ref["levels"], windows=windows, model_name="vbfret_nov12"
    )
    return {"project": project, "model": "vbfret_nov12"}


# --- the fixtures and the reader ----------------------------------------------------------------


def test_the_fixtures_hold_the_named_folders_verbatim(ref) -> None:
    n_traces, _ = COUNTS[ref["id"]]
    assert len(ref["traces"]) == n_traces
    names = [t.name for t in ref["traces"]]
    assert names == sorted(set(names)) and all(n.endswith(".dat") for n in names)
    provenance = json.loads((TABLES / "smfret_module_run.json").read_text(encoding="utf-8"))
    fixture = path_for(SETS[ref["id"]][1])
    assert provenance["inputs"][fixture.name] == hashlib.sha256(fixture.read_bytes()).hexdigest()


def test_the_reader_reads_the_columns_vbfret_writes(ref) -> None:
    for t in ref["traces"]:
        total = t.donor + t.acceptor
        ok = np.isfinite(t.fret) & (total != 0)
        # FRET = A / (D + A), computed before the intensities were rounded to 3 decimals.
        bound = 1e-3 / np.abs(total[ok]) + 5e-7
        assert np.all(np.abs(t.fret[ok] - t.acceptor[ok] / total[ok]) <= bound), t.name
        # vbFRET_nov12 was run with at most 5 states (Sci. Adv. 2026 methods).
        assert 1 <= np.unique(t.idealized).size <= 5, t.name


def test_the_expected_tables_are_the_recorded_smfret_module_output() -> None:
    provenance = json.loads((TABLES / "smfret_module_run.json").read_text(encoding="utf-8"))
    assert provenance["tool"]["name"] == "nmarzano/smFRET_module"
    assert provenance["tool"]["version"] == "1.0.3"
    for name, digest in provenance["outputs"].items():
        assert hashlib.sha256((TABLES / name).read_bytes()).hexdigest() == digest, name


# --- dwells: the pure core, molecule by molecule ------------------------------------------------


def test_each_molecule_has_the_transitions_of_smfret_module_in_order(ref) -> None:
    """``include_first=True`` keeps every run but the last: smFRET_module's transition table."""
    seen = 0
    for trace, path in zip(ref["traces"], ref["paths"], strict=True):
        rows = ref["transitions"].get(_molecule_id(ref["id"], trace), [])
        expected = [(float(r["FRET_before"]), int(r["Time"])) for r in rows]
        runs = _runs(path)
        got = [(float(ref["levels"][r[0]]), int(r.size)) for r in runs[:-1]]
        assert got == expected, trace.name
        # state_dwells pools by state but keeps the order inside each state.
        pooled = state_dwells([path], no_state=NO_STATE, include_first=True)
        for state, lengths in pooled.lengths.items():
            level = float(ref["levels"][state])
            assert lengths.tolist() == [n for lv, n in expected if lv == level], trace.name
        seen += len(expected)
    assert seen == COUNTS[ref["id"]][1]


def test_the_last_run_is_the_one_dwell_tether_drops(ref) -> None:
    """smFRET_module's dwell table is every run; Tether's is every run but the last."""
    for trace, path in zip(ref["traces"], ref["paths"], strict=True):
        rows = ref["dwells"].get(_molecule_id(ref["id"], trace), [])
        runs = _runs(path)
        if len(runs) <= 1:
            assert rows == [], f"{trace.name}: one run, so neither program has a dwell"
            continue
        assert [(float(r["FRET_state"]), int(r["Time"])) for r in rows] == [
            (float(ref["levels"][r[0]]), int(r.size)) for r in runs
        ], trace.name
        tether = state_dwells([path], no_state=NO_STATE, include_first=True)
        assert sum(v.size for v in tether.lengths.values()) == len(rows) - 1


# --- dwells and transitions from the store ------------------------------------------------------


def _expected_by_state(ref, *, include_first: bool) -> dict[int, list[int]]:
    index = {float(v): i for i, v in enumerate(ref["levels"])}
    by_state: dict[int, list[int]] = defaultdict(list)
    for trace in ref["traces"]:
        rows = ref["transitions"].get(_molecule_id(ref["id"], trace), [])
        # smFRET_module's cleanup_dwell(first_dwell="delete") drops each molecule's first row.
        for r in rows if include_first else rows[1:]:
            by_state[index[float(r["FRET_before"])]].append(int(r["Time"]))
    return by_state


@pytest.mark.parametrize("include_first", [True, False], ids=["first-kept", "first-dropped"])
def test_the_store_gives_the_dwells_of_smfret_module(ref, store, include_first) -> None:
    analyses = population_dwell_times(store["project"], store["model"], include_first=include_first)
    expected = _expected_by_state(ref, include_first=include_first)
    got = {st: a.dwell_lengths.tolist() for st, a in analyses.items() if a.n_dwells}
    assert got == dict(expected)
    for st, a in analyses.items():
        assert a.level == ref["levels"][st]


def _gap_crossings(ref) -> set[tuple[str, int]]:
    """(molecule, row) of each transition whose two runs a dropped frame separates."""
    crossings = set()
    for trace, path in zip(ref["traces"], ref["paths"], strict=True):
        frames = np.flatnonzero(path != NO_STATE)
        states = path[frames]
        change = np.flatnonzero(np.diff(states) != 0)
        for row, k in enumerate(change):
            if frames[k + 1] - frames[k] > 1:
                crossings.add((_molecule_id(ref["id"], trace), row))
    return crossings


def test_the_store_tdp_has_the_transitions_of_smfret_module_except_across_a_gap(ref, store):
    tdp = population_transition_density(
        store["project"],
        store["model"],
        nskip=1,
        signal_range=TDP_RANGE,
        signal_bins=TDP_BINS,
    )
    crossings = _gap_crossings(ref)
    assert len(crossings) == GAP_TRANSITIONS[ref["id"]]
    before, after = [], []
    for molecule, rows in ref["transitions"].items():
        for row, r in enumerate(rows):
            if (molecule, row) not in crossings:
                before.append(float(r["FRET_before"]))
                after.append(float(r["FRET_after"]))
    expected, _, _ = np.histogram2d(
        before, after, bins=[TDP_BINS, TDP_BINS], range=[TDP_RANGE, TDP_RANGE]
    )
    assert tdp.n_transitions == COUNTS[ref["id"]][1] - len(crossings)
    np.testing.assert_array_equal(tdp.counts, expected)

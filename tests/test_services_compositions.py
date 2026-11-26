# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""A plot composition saves to a file and opens again to the same layer list (#1438).

``tether.services.compositions`` is Qt-free, so these run with no ``QApplication``. They
check the acceptance criteria of #1438 on the record and its two service commands:

* two series save and open again to the same plot, the same series in the same order with
  the same names and molecule keys, and the same parameters;
* the file holds the plot, the project, the series, the parameters and the format version,
  and no trace data;
* a series whose project or molecules are missing is named, with what is missing, and is
  not among the series that can be drawn;
* a file of another format version is refused;
* both commands have command-line routes (``tether save-composition``, ``tether
  open-composition``).
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from tether.services import (
    OpenCompositionRequest,
    RequestRefused,
    SaveCompositionRequest,
    run,
)
from tether.services.compositions import (
    COMPOSITION_VERSION,
    PlotComposition,
    series_text,
)
from tether.services.plots import ALL_MOLECULES, PlotSeries

pytest.importorskip("h5py")

MODEL = "vbconhmm"
PLOT = "population_transition_prob_histogram"


@pytest.fixture
def modelled(tmp_path):
    """A six-molecule store with a two-state model, and its molecule keys."""
    from _analysis_store import build_store_with_model

    n, t = 6, 60
    states = np.array([[(j // (5 + i)) % 2 for j in range(t)] for i in range(n)])
    project, keys = build_store_with_model(tmp_path, states, np.array([0.2, 0.8]), model_name=MODEL)
    return project.path, list(keys)


def _two(path, keys) -> tuple[PlotSeries, PlotSeries]:
    from tether.analysis.saved_selection import SavedSelection

    return (
        PlotSeries("late", SavedSelection("late", path, keys[3:])),
        PlotSeries("early", SavedSelection("early", path, keys[:3])),
    )


def _save(path, project, series, **fields):
    request = SaveCompositionRequest(
        path=str(path),
        project=str(project),
        plot=fields.pop("plot", PLOT),
        series=tuple(s if isinstance(s, str) else series_text(s) for s in series),
        **fields,
    )
    return run(request)


def test_two_series_save_and_open_again_to_the_same_layer_list(modelled, tmp_path) -> None:
    project, keys = modelled
    late, early = _two(project, keys)
    out = tmp_path / "two.json"
    saved = _save(
        out,
        project,
        [late, early],
        model=MODEL,
        states=("init_state=1", "final_state=0"),
    )
    opened = run(OpenCompositionRequest(path=str(out)))
    assert opened.composition == saved.composition
    composition = opened.composition
    assert composition.plot == PLOT
    assert [s.label for s in composition.series] == ["late", "early"]
    assert [s.selection.molecule_keys for s in composition.series] == [
        tuple(keys[3:]),
        tuple(keys[:3]),
    ]
    assert composition.model_name == MODEL
    assert dict(composition.states) == {"init_state": 1, "final_state": 0}
    assert opened.missing == ()
    assert opened.found == composition.series


def test_the_record_round_trips_without_a_file() -> None:
    from tether.analysis.saved_selection import SavedSelection

    record = PlotComposition(
        plot="population_dwell_times",
        project_path="p.tether",
        series=(
            PlotSeries(ALL_MOLECULES, None),
            PlotSeries("a", SavedSelection("a", "p.tether", ("m2", "m1"))),
        ),
        model_name=MODEL,
        states=(("state", 1),),
    )
    assert PlotComposition.from_text(record.to_text()) == record


def test_the_file_holds_no_trace_data(modelled, tmp_path) -> None:
    project, keys = modelled
    out = tmp_path / "c.json"
    _save(out, project, [*_two(project, keys), "all"], model=MODEL)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert set(data) == {"format", "version", "plot", "project_path", "series", "parameters"}
    assert data["version"] == COMPOSITION_VERSION
    assert set(data["parameters"]) == {"model_name", "states"}
    for entry in data["series"]:
        assert set(entry) == {"label", "selection"}
    assert data["series"][2] == {"label": ALL_MOLECULES, "selection": None}
    # Names of molecules, never a number of a trace: every leaf is short text or a flag.
    assert out.stat().st_size < 4096


def test_a_preset_series_is_frozen_against_the_project(modelled, tmp_path) -> None:
    from tether.analysis.saved_selection import preset_selections

    project, _keys = modelled
    movie = preset_selections(project, "movie")[0]
    value = movie.filter.group[1]
    saved = _save(
        tmp_path / "p.json", project, [f"movie={value}", "all"], plot="population_raw_fret_cloud"
    )
    first, everything = saved.composition.series
    assert first.label == movie.name
    assert first.selection.molecule_keys == movie.molecule_keys
    assert everything == PlotSeries(ALL_MOLECULES, None)
    with pytest.raises(RequestRefused, match="has no movie 'nope'"):
        _save(tmp_path / "q.json", project, ["movie=nope"])
    with pytest.raises(RequestRefused, match="is not a preset"):
        _save(tmp_path / "q.json", project, ["tags=x"])
    assert not (tmp_path / "q.json").exists()


def test_missing_molecules_are_named_and_not_drawn(modelled, tmp_path) -> None:
    from tether.analysis.saved_selection import SavedSelection

    project, keys = modelled
    ghost = PlotSeries("ghost", SavedSelection("ghost", project, [keys[0], "no-such-key"]))
    late, _early = _two(project, keys)
    out = tmp_path / "c.json"
    _save(out, project, [late, ghost])
    opened = run(OpenCompositionRequest(path=str(out)))
    assert [label for label, _ in opened.missing] == ["ghost"]
    assert "1 of its 2 molecules are not in" in opened.missing[0][1]
    assert "no-such-key" in opened.missing[0][1]
    assert [s.label for s in opened.found] == ["late"]


def test_a_missing_project_is_named_for_every_series(modelled, tmp_path) -> None:
    project, keys = modelled
    out = tmp_path / "c.json"
    _save(out, project, [*_two(project, keys)])
    data = json.loads(out.read_text(encoding="utf-8"))
    gone = str(tmp_path / "moved.tether")
    data["project_path"] = gone
    out.write_text(json.dumps(data), encoding="utf-8")
    opened = run(OpenCompositionRequest(path=str(out)))
    assert [label for label, _ in opened.missing] == ["late", "early"]
    assert all("moved.tether cannot be found" in reason for _, reason in opened.missing)
    assert opened.found == ()


@pytest.mark.parametrize("version", [0, 2, "1", None, True])
def test_an_unknown_format_version_is_refused(modelled, tmp_path, version) -> None:
    project, keys = modelled
    out = tmp_path / "c.json"
    _save(out, project, [*_two(project, keys)])
    data = json.loads(out.read_text(encoding="utf-8"))
    data["version"] = version
    out.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(RequestRefused, match="composition version .* is not supported"):
        run(OpenCompositionRequest(path=str(out)))


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("{", "not valid JSON"),
        ('{"format": "other"}', "not a Tether plot composition"),
        (None, "has no key \\['traces'\\]"),
    ],
)
def test_a_file_that_is_not_a_composition_is_refused(modelled, tmp_path, text, message) -> None:
    out = tmp_path / "c.json"
    if text is None:
        project, keys = modelled
        _save(out, project, [*_two(project, keys)])
        data = json.loads(out.read_text(encoding="utf-8"))
        data["traces"] = [[0.1, 0.2]]
        text = json.dumps(data)
    out.write_text(text, encoding="utf-8")
    with pytest.raises(RequestRefused, match=message):
        run(OpenCompositionRequest(path=str(out)))


def test_save_refuses_before_any_write(modelled, tmp_path) -> None:
    project, keys = modelled
    out = tmp_path / "c.json"
    with pytest.raises(RequestRefused, match="no plot 'nope'"):
        _save(out, project, [*_two(project, keys)], plot="nope")
    with pytest.raises(RequestRefused, match="takes no state option"):
        _save(out, project, [*_two(project, keys)], states=("state=1",))
    with pytest.raises(RequestRefused, match="names each series once"):
        _save(out, project, ["all", "all"])
    with pytest.raises(RequestRefused, match="cannot be found"):
        _save(out, tmp_path / "none.tether", ["all"])
    assert not out.exists()
    _save(out, project, ["all"])
    with pytest.raises(RequestRefused, match="exists; give --force"):
        _save(out, project, ["all"])
    _save(out, project, ["all"], overwrite=True)


def test_the_command_line_saves_and_opens_a_composition(modelled, tmp_path, capsys) -> None:
    from tether.cli import main

    project, _keys = modelled
    out = tmp_path / "cli.json"
    argv = ["save-composition", str(out), "--project", str(project), "--plot", PLOT]
    argv += ["--series", "all", "--model", MODEL, "--state", "final_state=0"]
    assert main(argv) == 0
    assert main(["open-composition", str(out)]) == 0
    printed = capsys.readouterr().out
    assert f"{PLOT} of" in printed and f"model: {MODEL}" in printed
    assert "final_state: 0" in printed and f"series {ALL_MOLECULES}: all molecules" in printed
    assert main(argv) == 2
    data = json.loads(out.read_text(encoding="utf-8"))
    data["series"].append(
        {
            "label": "ghost",
            "selection": {
                "version": 1,
                "name": "ghost",
                "project_path": str(project),
                "molecule_keys": ["no-such-key"],
                "filter": {},
            },
        }
    )
    out.write_text(json.dumps(data), encoding="utf-8")
    assert main(["open-composition", str(out)]) == 1
    assert "ghost: 1 of its 1 molecules are not in" in capsys.readouterr().err
    data["version"] = 99
    out.write_text(json.dumps(data), encoding="utf-8")
    assert main(["open-composition", str(out)]) == 2

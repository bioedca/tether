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


def _saved_data(modelled, tmp_path) -> dict:
    project, keys = modelled
    out = tmp_path / "base.json"
    _save(out, project, [*_two(project, keys)], model=MODEL)
    return json.loads(out.read_text(encoding="utf-8"))


def _set(data: dict, path: tuple, value) -> dict:
    node = data
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return data


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("parameters",), [], "'parameters' must be an object"),
        (("series",), {}, "'series' a list"),
        (("parameters", "states"), {"init_state": "1"}, "map each option to an integer"),
        (("parameters", "model_name"), 3, "'model_name' must be a string or null"),
        (("series", 0), ["late"], "with 'label' and 'selection' only"),
        (("series", 0, "selection"), "keys", "'selection' must be an object or null"),
        (("plot",), "population_nope", "bad.json: no plot 'population_nope' in the plot"),
    ],
)
def test_a_malformed_composition_is_refused(modelled, tmp_path, path, value, message) -> None:
    data = _set(_saved_data(modelled, tmp_path), path, value)
    out = tmp_path / "bad.json"
    out.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(RequestRefused, match=message):
        run(OpenCompositionRequest(path=str(out)))


def test_a_composition_missing_a_key_is_refused(modelled, tmp_path) -> None:
    data = _saved_data(modelled, tmp_path)
    del data["plot"]
    out = tmp_path / "bad.json"
    out.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(RequestRefused, match="the composition is missing 'plot'"):
        run(OpenCompositionRequest(path=str(out)))
    with pytest.raises(RequestRefused, match="cannot read"):
        run(OpenCompositionRequest(path=str(tmp_path / "none.json")))


def test_many_missing_keys_are_counted_and_another_project_is_named(modelled, tmp_path) -> None:
    from tether.analysis.saved_selection import SavedSelection

    project, keys = modelled
    many = PlotSeries("many", SavedSelection("many", project, [f"k{i}" for i in range(7)]))
    elsewhere = tmp_path / "elsewhere.tether"
    other = PlotSeries("other", SavedSelection("other", elsewhere, keys[:1]))
    out = tmp_path / "c.json"
    _save(out, project, [many, other])
    opened = run(OpenCompositionRequest(path=str(out)))
    reasons = dict(opened.missing)
    assert "7 of its 7 molecules are not in" in reasons["many"]
    assert reasons["many"].endswith("and 2 more")
    assert "its molecules are of the project" in reasons["other"]
    assert "many: " in opened.message() and "other: " in opened.message()


def test_an_unreadable_project_is_named(modelled, tmp_path) -> None:
    project, keys = modelled
    out = tmp_path / "c.json"
    _save(out, project, ["all"])
    data = json.loads(out.read_text(encoding="utf-8"))
    broken = tmp_path / "broken.tether"
    broken.write_text("not a project", encoding="utf-8")
    data["project_path"] = str(broken)
    out.write_text(json.dumps(data), encoding="utf-8")
    opened = run(OpenCompositionRequest(path=str(out)))
    assert opened.missing[0][0] == ALL_MOLECULES
    assert "cannot be read" in opened.missing[0][1]


def test_save_refuses_a_bad_state_and_fails_an_unwritable_path(modelled, tmp_path) -> None:
    from tether.services import CommandFailed

    project, _keys = modelled
    with pytest.raises(RequestRefused, match="is not NAME=INTEGER"):
        _save(tmp_path / "c.json", project, ["all"], states=("state=one",))
    with pytest.raises(CommandFailed, match="cannot write"):
        _save(tmp_path / "no-such-folder" / "c.json", project, ["all"])


def test_a_selection_without_a_saved_form_cannot_be_saved() -> None:
    with pytest.raises(TypeError, match="its selection cannot be saved"):
        series_text(PlotSeries("odd", object()))


# --- review findings on PR #1465 ---------------------------------------------------


def test_a_save_never_replaces_the_project_or_any_other_file(modelled, tmp_path) -> None:
    project, _keys = modelled
    before = project.read_bytes()
    with pytest.raises(RequestRefused, match="is the project the composition reads"):
        _save(project, project, ["all"], overwrite=True)
    assert project.read_bytes() == before
    other = tmp_path / "notes.json"
    other.write_text('{"kept": true}', encoding="utf-8")
    with pytest.raises(RequestRefused, match="is not a plot composition"):
        _save(other, project, ["all"], overwrite=True)
    assert other.read_text(encoding="utf-8") == '{"kept": true}'
    binary = tmp_path / "other.tether"
    binary.write_bytes(before)
    with pytest.raises(RequestRefused, match="is not a plot composition"):
        _save(binary, project, ["all"], overwrite=True)
    assert binary.read_bytes() == before


def test_a_binary_file_is_refused_not_raised(modelled, tmp_path, capsys) -> None:
    from tether.cli import main

    project, _keys = modelled
    with pytest.raises(RequestRefused, match="cannot read"):
        run(OpenCompositionRequest(path=str(project)))
    assert main(["open-composition", str(project)]) == 2
    assert "tether open-composition: cannot read" in capsys.readouterr().err


def test_an_unconditioned_molecule_is_not_missing(modelled, tmp_path) -> None:
    import h5py

    from tether.analysis.saved_selection import SavedSelection
    from tether.io.schema import TABLE

    project, keys = modelled
    with h5py.File(project, "r+") as f:
        table = f["molecules"][TABLE]
        rows = table[:]
        rows["condition_id"][0] = b""
        table[...] = rows
    series = PlotSeries("first", SavedSelection("first", project, keys[:1]))
    out = tmp_path / "c.json"
    _save(out, project, [series])
    opened = run(OpenCompositionRequest(path=str(out)))
    assert opened.missing == ()
    assert [s.label for s in opened.found] == ["first"]


def test_a_negative_state_and_a_malformed_filter_are_refused(modelled, tmp_path) -> None:
    project, _keys = modelled
    with pytest.raises(RequestRefused, match="a state is 0 or more"):
        _save(
            tmp_path / "c.json",
            project,
            ["all"],
            states=("state=-1",),
            plot="population_dwell_times",
        )
    data = _saved_data(modelled, tmp_path)
    data["series"][0]["selection"]["filter"] = [1]
    out = tmp_path / "bad.json"
    out.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(RequestRefused, match="its selection cannot be read"):
        run(OpenCompositionRequest(path=str(out)))


# --- review findings of the CodeRabbit review on PR #1465 --------------------------


def test_an_unknown_parameter_and_a_repeated_state_are_refused(modelled, tmp_path) -> None:
    data = _saved_data(modelled, tmp_path)
    data["parameters"]["include_stale"] = True
    out = tmp_path / "bad.json"
    out.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(RequestRefused, match=r"has no parameter \['include_stale'\]"):
        run(OpenCompositionRequest(path=str(out)))
    project, _keys = modelled
    with pytest.raises(RequestRefused, match="each state option is given once"):
        _save(
            tmp_path / "c.json",
            project,
            ["all"],
            plot="population_dwell_times",
            states=("state=1", "state=0"),
        )


def test_a_save_never_writes_through_a_link_at_a_temporary_name(modelled, tmp_path) -> None:
    import os

    project, _keys = modelled
    before = project.read_bytes()
    out = tmp_path / "c.json"
    trap = tmp_path / ".c.json.tmp"
    try:
        trap.symlink_to(project)
    except OSError:  # no symlink privilege (Windows without developer mode)
        pytest.skip("symlinks are not available here")
    _save(out, project, ["all"])
    assert project.read_bytes() == before
    assert trap.is_symlink()
    assert json.loads(out.read_text(encoding="utf-8"))["format"] == "tether-plot-composition"
    leftovers = [p for p in os.listdir(tmp_path) if p.endswith(".tmp") and p != trap.name]
    assert leftovers == []


def test_a_failed_write_leaves_no_temporary_file(modelled, tmp_path, monkeypatch) -> None:
    import os

    from tether.services import CommandFailed

    project, _keys = modelled

    def refuse(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(CommandFailed, match="disk full"):
        _save(tmp_path / "c.json", project, ["all"], overwrite=True)
    assert [p for p in os.listdir(tmp_path) if p.endswith(".tmp")] == []
    # Without overwrite and without hard links, a POSIX save reserves the new name and
    # replaces the reservation; when that fails, no temporary file stays, and the empty
    # reservation is left rather than removed by name (#1523).
    from tether.services import compositions

    monkeypatch.setattr(os, "link", refuse)
    monkeypatch.setattr(compositions, "_RENAME_REFUSES_EXISTING", False)
    with pytest.raises(CommandFailed, match="disk full"):
        _save(tmp_path / "c.json", project, ["all"])
    assert [p for p in os.listdir(tmp_path) if p.endswith(".tmp")] == []
    assert (tmp_path / "c.json").read_bytes() == b""
    monkeypatch.undo()
    # A write that fails before the publish leaves no temporary file either.
    monkeypatch.setattr(os, "fstat", refuse)
    with pytest.raises(CommandFailed, match="disk full"):
        _save(tmp_path / "d.json", project, ["all"])
    assert [p for p in os.listdir(tmp_path) if p.endswith(".tmp")] == []


def test_a_saved_composition_is_readable_like_any_new_file(modelled, tmp_path) -> None:
    import os
    import stat

    project, _keys = modelled
    out = tmp_path / "c.json"
    _save(out, project, ["all"])
    mode = stat.S_IMODE(out.stat().st_mode)
    if hasattr(os, "fchmod"):
        assert mode == 0o644  # not mkstemp's private 0600
    else:  # Windows: no fchmod, and the file keeps its ordinary read-write default
        assert mode & stat.S_IREAD and mode & stat.S_IWRITE


# --- #1470: publish exclusively, and only the file the save wrote ------------------


def _record_temporary(monkeypatch) -> list:
    """Make ``tempfile.mkstemp`` record each name it makes, and return that list."""
    import tempfile

    names: list = []
    real = tempfile.mkstemp

    def mkstemp(*args, **kwargs):
        fd, name = real(*args, **kwargs)
        names.append(name)
        return fd, name

    monkeypatch.setattr(tempfile, "mkstemp", mkstemp)
    return names


def test_a_destination_created_after_the_check_is_never_replaced(
    modelled, tmp_path, monkeypatch
) -> None:
    """Gap 1: another process creates the destination between the check and the publish."""
    import os

    project, _keys = modelled
    out = tmp_path / "c.json"
    names = _record_temporary(monkeypatch)
    real_fdopen = os.fdopen

    def fdopen(fd, *args, **kwargs):  # runs after the existence check, before the publish
        out.write_text('{"theirs": true}', encoding="utf-8")
        return real_fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(os, "fdopen", fdopen)
    with pytest.raises(RequestRefused, match="exists; give --force to replace it"):
        _save(out, project, ["all"])
    assert out.read_text(encoding="utf-8") == '{"theirs": true}'
    assert names and not any(os.path.lexists(n) for n in names)


@pytest.mark.parametrize("overwrite", [False, True])
def test_a_temporary_entry_replaced_before_the_publish_is_never_published(
    modelled, tmp_path, monkeypatch, overwrite
) -> None:
    """Gap 2: the temporary entry is swapped for another file after the write."""
    import os

    from tether.services import CommandFailed

    project, _keys = modelled
    out = tmp_path / "c.json"
    if overwrite:
        _save(out, project, ["all"])
    before = out.read_bytes() if overwrite else None
    names = _record_temporary(monkeypatch)
    real_fdopen = os.fdopen

    class Swapped:
        """The written file; on close, its name is given to another file."""

        def __init__(self, f) -> None:
            self.f = f

        def __enter__(self):
            return self.f.__enter__()

        def __exit__(self, *exc):
            result = self.f.__exit__(*exc)
            other = tmp_path / "other"
            other.write_text('{"swapped": true}', encoding="utf-8")
            os.replace(other, names[-1])
            return result

    monkeypatch.setattr(os, "fdopen", lambda fd, *a, **k: Swapped(real_fdopen(fd, *a, **k)))
    with pytest.raises(CommandFailed, match="was replaced before the save published it"):
        _save(out, project, ["all"], overwrite=overwrite)
    if overwrite:
        assert out.read_bytes() == before
    else:
        assert not os.path.lexists(out)
    # The swapped entry is not the save's, so the save leaves it where it found it.
    assert (tmp_path / os.path.basename(names[-1])).read_text(encoding="utf-8") == (
        '{"swapped": true}'
    )


def test_without_hard_links_a_save_still_publishes_exclusively(
    modelled, tmp_path, monkeypatch
) -> None:
    """On POSIX without hard links, the new name is reserved exclusively, then replaced."""
    import os

    project, _keys = modelled

    from tether.services import compositions

    def no_links(*_args, **_kwargs):
        raise PermissionError("hard links are not supported")

    monkeypatch.setattr(os, "link", no_links)
    monkeypatch.setattr(compositions, "_RENAME_REFUSES_EXISTING", False)
    out = tmp_path / "c.json"
    _save(out, project, ["all"])
    assert json.loads(out.read_text(encoding="utf-8"))["format"] == "tether-plot-composition"
    later = tmp_path / "d.json"
    names = _record_temporary(monkeypatch)
    real_fdopen = os.fdopen

    def fdopen(fd, *args, **kwargs):
        later.write_text('{"theirs": true}', encoding="utf-8")
        return real_fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(os, "fdopen", fdopen)
    with pytest.raises(RequestRefused, match="exists; give --force to replace it"):
        _save(later, project, ["all"])
    assert later.read_text(encoding="utf-8") == '{"theirs": true}'
    assert not any(os.path.lexists(n) for n in names)


def test_a_temporary_entry_removed_before_the_publish_publishes_nothing(
    modelled, tmp_path, monkeypatch
) -> None:
    """Gap 2, its other form: the temporary name is gone by the time of the publish."""
    import os

    from tether.services import CommandFailed

    project, _keys = modelled
    out = tmp_path / "c.json"
    names = _record_temporary(monkeypatch)
    real_fdopen = os.fdopen

    class Removed:
        def __init__(self, f) -> None:
            self.f = f

        def __enter__(self):
            return self.f.__enter__()

        def __exit__(self, *exc):
            result = self.f.__exit__(*exc)
            os.unlink(names[-1])
            return result

    monkeypatch.setattr(os, "fdopen", lambda fd, *a, **k: Removed(real_fdopen(fd, *a, **k)))
    with pytest.raises(CommandFailed, match="was replaced before the save published it"):
        _save(out, project, ["all"])
    assert not os.path.lexists(out)


# --- review findings of the CodeRabbit review on PR #1508 --------------------------


def test_without_hard_links_a_file_another_writer_puts_there_is_never_replaced(
    modelled, tmp_path, monkeypatch
) -> None:
    """The reserving route never replaces a file that another writer puts at the name."""
    import os

    from tether.services import CommandFailed, compositions

    project, _keys = modelled
    out = tmp_path / "c.json"

    def no_links(*_args, **_kwargs):
        raise PermissionError("hard links are not supported")

    monkeypatch.setattr(os, "link", no_links)
    monkeypatch.setattr(compositions, "_RENAME_REFUSES_EXISTING", False)
    real_lstat = os.lstat
    swapped = []

    def overwritten_before_the_check(name, *args, **kwargs):
        # An overwriting save lands after the in-place write and before its check. The
        # write's descriptor is closed by then, so Windows lets the replace happen too.
        if os.fspath(name) == os.fspath(out) and not swapped:
            swapped.append(name)
            other = tmp_path / "theirs"
            other.write_text('{"theirs": true}', encoding="utf-8")
            os.replace(other, out)
        return real_lstat(name, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", overwritten_before_the_check)
    with pytest.raises(CommandFailed, match="was replaced while the save reserved it"):
        _save(out, project, ["all"])
    assert out.read_text(encoding="utf-8") == '{"theirs": true}'
    assert [p for p in os.listdir(tmp_path) if p.endswith(".tmp")] == []


def test_where_rename_never_replaces_a_save_publishes_by_rename(
    modelled, tmp_path, monkeypatch
) -> None:
    """Without hard links on Windows, a rename publishes, and it never replaces a file."""
    import os

    from tether.services import compositions

    project, _keys = modelled

    def no_links(*_args, **_kwargs):
        raise PermissionError("hard links are not supported")

    real_rename = os.rename

    def windows_rename(src, dst, *args, **kwargs):  # what os.rename does on Windows
        if os.path.lexists(dst):
            raise FileExistsError(dst)
        real_rename(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "link", no_links)
    monkeypatch.setattr(os, "rename", windows_rename)
    monkeypatch.setattr(compositions, "_RENAME_REFUSES_EXISTING", True)
    out = tmp_path / "c.json"
    _save(out, project, ["all"])
    assert json.loads(out.read_text(encoding="utf-8"))["format"] == "tether-plot-composition"
    later = tmp_path / "d.json"
    real_fdopen = os.fdopen

    def fdopen(fd, *args, **kwargs):
        later.write_text('{"theirs": true}', encoding="utf-8")
        return real_fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(os, "fdopen", fdopen)
    with pytest.raises(RequestRefused, match="exists; give --force to replace it"):
        _save(later, project, ["all"])
    assert later.read_text(encoding="utf-8") == '{"theirs": true}'
    assert [p for p in os.listdir(tmp_path) if p.endswith(".tmp")] == []


# --- #1523: composition save leftovers -------------------------------------------


class _Killed(BaseException):
    """Stands in for a kill: nothing the save does after it runs in a killed process."""


def _no_hard_links(monkeypatch) -> None:
    """Take the POSIX route of a file system without hard links."""
    import os

    from tether.services import compositions

    def no_links(*_args, **_kwargs):
        raise PermissionError("hard links are not supported")

    monkeypatch.setattr(os, "link", no_links)
    monkeypatch.setattr(compositions, "_RENAME_REFUSES_EXISTING", False)


def test_without_hard_links_a_killed_save_leaves_no_half_file(
    modelled, tmp_path, monkeypatch
) -> None:
    """Leftover 1: the text is never written through the destination's name."""
    import os

    project, _keys = modelled
    out = tmp_path / "c.json"
    _no_hard_links(monkeypatch)
    seen = []

    def killed_at_the_publish(src, dst, *args, **kwargs):
        seen.append(out.read_bytes())  # the destination as the kill would leave it
        raise _Killed

    monkeypatch.setattr(os, "replace", killed_at_the_publish)
    with pytest.raises(_Killed):
        _save(out, project, ["all"])
    assert seen == [b""]  # an empty reservation, never part of the composition
    monkeypatch.undo()
    # Without --force the leftover is refused like any existing file ...
    with pytest.raises(RequestRefused, match="exists; give --force to replace it"):
        _save(out, project, ["all"])
    # ... and with --force it is replaced, since an empty file holds nothing to lose.
    _save(out, project, ["all"], overwrite=True)
    assert json.loads(out.read_text(encoding="utf-8"))["format"] == "tether-plot-composition"


def test_force_still_refuses_a_file_that_is_not_empty_nor_a_composition(modelled, tmp_path) -> None:
    project, _keys = modelled
    out = tmp_path / "c.json"
    out.write_text("{", encoding="utf-8")  # a half file of any other writer
    with pytest.raises(RequestRefused, match="is not a plot composition"):
        _save(out, project, ["all"], overwrite=True)
    assert out.read_text(encoding="utf-8") == "{"


@pytest.mark.parametrize("hard_links", [True, False])
@pytest.mark.parametrize("failures", [1, 99])
def test_a_failed_cleanup_after_the_publish_is_not_a_failed_save(
    modelled, tmp_path, monkeypatch, hard_links, failures
) -> None:
    """Leftover 2: the composition is published, so the save succeeds."""
    import os
    from pathlib import Path

    project, _keys = modelled
    out = tmp_path / "c.json"
    names = _record_temporary(monkeypatch)
    if not hard_links:
        _no_hard_links(monkeypatch)
    real_unlink = Path.unlink
    refused = []

    def unlink(self, *args, **kwargs):
        if names and os.fspath(self) == names[-1] and len(refused) < failures:
            refused.append(self)
            raise PermissionError("the temporary file is busy")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    saved = _save(out, project, ["all"])
    assert saved.path == out
    assert json.loads(out.read_text(encoding="utf-8"))["format"] == "tether-plot-composition"
    if hard_links:
        assert refused
        # Where a later removal succeeds, no temporary file stays behind.
        assert os.path.lexists(names[-1]) == (failures > 1)
    else:  # the reservation route moves the temporary name, so nothing is left to remove
        assert not os.path.lexists(names[-1])


def test_without_hard_links_a_failed_save_never_removes_a_file_swapped_in(
    modelled, tmp_path, monkeypatch
) -> None:
    """Leftover 4: a file put at the name after the reservation is left where it is."""
    import os

    from tether.services import CommandFailed

    project, _keys = modelled
    out = tmp_path / "c.json"
    _no_hard_links(monkeypatch)

    def swapped_then_failed(src, dst, *args, **kwargs):
        os.unlink(out)  # another writer removes the reservation ...
        out.write_text('{"theirs": true}', encoding="utf-8")  # ... and creates its own
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", swapped_then_failed)
    with pytest.raises(CommandFailed, match="disk full"):
        _save(out, project, ["all"])
    assert out.read_text(encoding="utf-8") == '{"theirs": true}'
    assert [p for p in os.listdir(tmp_path) if p.endswith(".tmp")] == []


def test_only_an_empty_regular_file_counts_as_empty(tmp_path) -> None:
    from tether.services.compositions import _is_empty_file

    empty = tmp_path / "empty.json"
    empty.write_bytes(b"")
    assert _is_empty_file(empty)
    assert not _is_empty_file(tmp_path / "gone.json")  # vanished since the caller's check
    assert not _is_empty_file(tmp_path)  # a directory is not a file to replace
    (tmp_path / "full.json").write_bytes(b"{")
    assert not _is_empty_file(tmp_path / "full.json")


@pytest.mark.parametrize("route", ["overwrite", "rename", "reservation"])
def test_a_route_that_moved_the_temporary_name_never_removes_it_afterwards(
    modelled, tmp_path, monkeypatch, route
) -> None:
    """CodeRabbit on PR #1527: a name reused after the move belongs to another writer."""
    import os

    from tether.services import compositions

    project, _keys = modelled
    out = tmp_path / "c.json"
    if route == "overwrite":
        _save(out, project, ["all"])
    names = _record_temporary(monkeypatch)
    _no_hard_links(monkeypatch)
    real_rename, real_replace = os.rename, os.replace

    def then_reused(real):
        def move(src, dst, *args, **kwargs):
            real(src, dst, *args, **kwargs)
            if os.fspath(src) in names:  # another writer takes the freed name at once
                with open(src, "w", encoding="utf-8") as f:
                    f.write('{"theirs": true}')

        return move

    if route == "rename":
        monkeypatch.setattr(compositions, "_RENAME_REFUSES_EXISTING", True)
        monkeypatch.setattr(os, "rename", then_reused(real_rename))
    else:
        monkeypatch.setattr(os, "replace", then_reused(real_replace))
    _save(out, project, ["all"], overwrite=route == "overwrite")
    assert json.loads(out.read_text(encoding="utf-8"))["format"] == "tether-plot-composition"
    with open(names[-1], encoding="utf-8") as f:
        assert f.read() == '{"theirs": true}'

# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Analyze section's page (#374): plot list, layer list, one Matplotlib preview.

Headless ``pytest-qt`` tests (``QT_QPA_PLATFORM=offscreen``). The section builds no napari
``Viewer``, so they run on all three operating systems, macOS included. They check:

* every entry of the plot registry is reachable from the section's plot list;
* adding two selections gives two named, removable series in the layer list, and removing
  one draws the plot again without it;
* the preview canvas holds the very ``Figure`` the registry's renderer drew (one renderer);
* a model-derived plot on a project with no model shows what is missing, never a figure;
* the model choice and the state boxes show only for a plot that reads them;
* *Save composition…* and *Open composition…* give back the same layer list, name a series
  whose molecules are missing and leave it out, and refuse a file of another version (#1438).
"""

from __future__ import annotations

import importlib.util

import numpy as np
import pytest

from tether.services.plots import ALL_MOLECULES, PLOTS, PlotSeries

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
pytestmark = [
    pytest.mark.gui,
    pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed"),
]

MODEL = "vbconhmm"


@pytest.fixture
def modelled(tmp_path):
    """A four-molecule store with a two-state model, and its keys."""
    from _analysis_store import build_store_with_model

    n, t = 4, 40
    states = np.array([[(j // (4 + i)) % 2 for j in range(t)] for i in range(n)])
    project, keys = build_store_with_model(
        tmp_path, states, np.array([0.25, 0.75]), model_name=MODEL
    )
    return project.path, keys


@pytest.fixture
def section(qtbot):
    from tether.gui.analysis_section import AnalysisSection

    page = AnalysisSection()
    qtbot.addWidget(page.widget)
    yield page
    page.close()


def _saved(path, name, keys):
    from tether.analysis.saved_selection import SavedSelection

    return PlotSeries(name, SavedSelection(name, path, keys))


def _all_texts(figure) -> str:
    from matplotlib.text import Text

    return " ".join(t.get_text() for t in figure.findobj(Text))


def test_every_registry_plot_is_reachable_from_the_plot_list(section) -> None:
    combo = section.plot_combo
    listed = [combo.itemData(i) for i in range(combo.count())]
    assert listed == [spec.id for spec in PLOTS]
    assert len(listed) == 16
    for spec in PLOTS:
        section.set_plot(spec.id)
        assert section.plot_id == spec.id
        assert combo.currentText() == spec.title


def test_two_selections_are_two_named_removable_series(section, modelled) -> None:
    path, keys = modelled
    section.set_project(path)
    section.add_series(_saved(path, "early", keys[:2]))
    section.add_series(_saved(path, "late", keys[2:]))
    layers = section.layers
    assert [layers.item(i).text() for i in range(layers.count())] == ["early", "late"]
    assert section.series_labels == ["early", "late"]
    assert [label for label, _ in section.outcome.results] == ["early", "late"]
    texts = _all_texts(section.figure)
    assert "early" in texts and "late" in texts

    # Remove "early" with the button: the plot is drawn again over "late" alone.
    layers.setCurrentRow(0)
    assert section.remove_button.isEnabled()
    first_figure = section.figure
    section.remove_button.click()
    assert section.series_labels == ["late"]
    assert layers.count() == 1 and layers.item(0).text() == "late"
    assert section.figure is not first_figure
    assert [label for label, _ in section.outcome.results] == ["late"]
    assert "early" not in _all_texts(section.figure)
    assert section.outcome.results[0][1].n_molecules == 2


def test_a_repeated_name_gets_a_distinct_label(section, modelled) -> None:
    path, _keys = modelled
    section.set_project(path)
    section.add_series(PlotSeries(ALL_MOLECULES, None))
    section.add_series(PlotSeries(ALL_MOLECULES, None))
    section.add_series(PlotSeries(ALL_MOLECULES, None))
    assert section.series_labels == [ALL_MOLECULES, "All molecules (2)", "All molecules (3)"]
    assert section.outcome.drawn


def test_the_preview_canvas_holds_the_figure_the_renderer_drew(section, modelled) -> None:
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

    path, _keys = modelled
    section.set_project(path)
    section.set_plot("population_transition_density")
    section.add_series(PlotSeries(ALL_MOLECULES, None))
    canvas = section.canvas
    assert isinstance(canvas, FigureCanvasQTAgg)
    assert canvas.figure is section.figure is section.outcome.figure
    assert section.figure.canvas is canvas
    assert section.empty_text is None


def test_a_model_plot_without_a_model_shows_what_is_missing(section, tmp_path) -> None:
    from _analysis_store import build_store_with_channels

    rng = np.random.default_rng(1)
    project, _keys = build_store_with_channels(
        tmp_path, rng.uniform(1, 2, (2, 20)), rng.uniform(1, 2, (2, 20))
    )
    section.set_project(project.path)
    section.add_series(PlotSeries(ALL_MOLECULES, None))
    assert section.figure is not None  # the A1 histogram needs no model
    section.set_plot("population_dwell_times")
    assert section.figure is None and section.canvas is None
    assert section.empty_text == (
        "Dwell-time survival (B2) needs an idealization model, and this project has none. "
        "Idealize the molecules in Curate and correct first."
    )
    assert section.model_combo.count() == 0


def test_the_model_and_states_show_only_for_a_plot_that_reads_them(section, modelled) -> None:
    path, _keys = modelled
    section.set_project(path)
    assert section.model_name == MODEL
    boxes = section.state_boxes
    section.set_plot("population_apparent_e_histogram")
    assert section.model_combo.isHidden()
    assert all(box.isHidden() for box in boxes.values())
    section.set_plot("population_transition_prob_histogram")
    assert not section.model_combo.isHidden()
    assert not boxes["init_state"].isHidden() and not boxes["final_state"].isHidden()
    assert boxes["state"].isHidden()
    section.set_plot("population_dwell_times")
    assert not boxes["state"].isHidden() and boxes["init_state"].isHidden()


def test_a_state_box_draws_the_chosen_state(section, modelled) -> None:
    path, _keys = modelled
    section.set_project(path)
    section.set_plot("population_dwell_times")
    section.add_series(PlotSeries(ALL_MOLECULES, None))
    assert section.outcome.results[0][1].state == 0
    section.state_boxes["state"].setValue(1)
    assert section.outcome.results[0][1].state == 1
    section.state_boxes["state"].setValue(7)
    assert section.figure is None
    assert "the state 7 is not one" in section.empty_text


def test_the_add_menu_offers_all_molecules_and_the_presets(section, modelled) -> None:
    path, keys = modelled
    assert not section.add_button.isEnabled()
    section.set_project(path)
    assert section.add_button.isEnabled()
    menu = section.add_menu
    menu.aboutToShow.emit()
    actions = menu.actions()
    assert [a.text() for a in actions] == [
        ALL_MOLECULES,
        "By condition",
        "By movie",
        "By source file",
    ]
    by_movie = actions[2].menu().actions()
    assert len(by_movie) == 1 and by_movie[0].text().endswith(f"({len(keys)})")
    by_movie[0].trigger()
    assert len(section.series) == 1
    assert section.outcome.results[0][1].n_molecules == len(keys)


def test_a_new_project_empties_the_layer_list(section, modelled, tmp_path) -> None:
    path, _keys = modelled
    section.set_project(path)
    section.add_series(PlotSeries(ALL_MOLECULES, None))
    assert section.layers.count() == 1
    section.set_project(None)
    assert section.series == () and section.layers.count() == 0
    assert section.empty_text == "Open a project to draw a plot."


def test_an_unexpected_failure_is_shown_not_raised(section, modelled, monkeypatch) -> None:
    from tether.gui import analysis_section

    path, _keys = modelled
    section.set_project(path)

    def broken(*_args, **_kwargs):
        raise RuntimeError("disk went away")

    monkeypatch.setattr(analysis_section, "compose_plot", broken)
    section.add_series(PlotSeries(ALL_MOLECULES, None))
    assert section.figure is None
    assert section.empty_text == "FRET-efficiency histogram (A1) cannot be drawn: disk went away"


def test_a_model_written_after_the_project_opened_can_be_chosen(
    section, modelled, monkeypatch
) -> None:
    # Codex review on #1377: a fit made in Curate and correct after the project opened must
    # reach the model choices, or the empty state would keep saying the project has none.
    from tether.gui import analysis_section

    path, _keys = modelled
    real = analysis_section.idealization_models
    monkeypatch.setattr(analysis_section, "idealization_models", lambda _project: [])
    section.set_project(path)
    section.set_plot("population_dwell_times")
    section.add_series(PlotSeries(ALL_MOLECULES, None))
    assert section.model_combo.count() == 0
    assert not section.outcome.drawn  # no model chosen, so nothing is drawn

    monkeypatch.setattr(analysis_section, "idealization_models", real)  # the fit lands
    section.refresh()
    assert section.model_name == MODEL
    assert section.outcome.drawn and section.empty_text is None


def test_showing_the_analyze_section_reads_the_model_choices_again(
    qtbot, modelled, monkeypatch
) -> None:
    from tether.gui import analysis_section
    from tether.gui.shell import TetherShell

    path, _keys = modelled
    real = analysis_section.idealization_models
    monkeypatch.setattr(analysis_section, "idealization_models", lambda _project: [])
    shell = TetherShell()
    qtbot.addWidget(shell.window)
    try:
        assert shell.load_project(path) is not None, shell.status_message
        page = shell.analysis_section
        page.set_plot("population_dwell_times")
        page.add_series(PlotSeries(ALL_MOLECULES, None))
        assert page.model_combo.count() == 0
        if shell.section == "analyze":
            shell.set_section("curate")
        monkeypatch.setattr(analysis_section, "idealization_models", real)  # the fit lands
        shell.set_section("analyze")
        assert page.model_name == MODEL
        assert page.outcome.drawn
    finally:
        shell.close()


def test_the_successive_frame_cloud_is_drawn_and_exported_with_its_numbers(
    section, modelled, tmp_path
) -> None:
    """#1085: the cloud of E(i) against E(i+1), under its own name, and its export."""
    import json

    from tether.analysis.plot_export import export_figure
    from tether.services.plots import plot_spec

    path, keys = modelled
    successive = plot_spec("population_successive_frame_cloud")
    assert successive.title != plot_spec("population_raw_fret_cloud").title
    assert "raw" not in successive.title.lower()
    section.set_project(path)
    section.set_plot(successive.id)
    assert section.plot_combo.currentText() == successive.title
    section.add_series(PlotSeries(ALL_MOLECULES, None))
    assert section.outcome.drawn, section.outcome.missing
    cloud = section.outcome.results[0][1]
    assert cloud.n_molecules == len(keys) and cloud.n_pairs > 0
    texts = _all_texts(section.figure)
    assert "Successive-frame FRET cloud" in texts and "Raw FRET cloud" not in texts
    assert section.canvas.figure is section.figure

    out = export_figure(
        section.figure,
        tmp_path / "succ",
        tether_export="plot:successive-frame-cloud",
        source=path.name,
        parameters={},
        data_csv=True,
    )
    assert set(out.data_paths) == {"data", "density", "levels", "centroids"}
    assert out.data_paths["levels"].read_text(encoding="utf-8").count("\n") > len(
        cloud.hdr_coverages
    )
    sidecar = json.loads(out.provenance_path.read_text(encoding="utf-8"))
    assert sidecar["tether_export"] == "plot:successive-frame-cloud"
    (record,) = sidecar["parameters"]["series"]
    assert record["label"] == ALL_MOLECULES
    assert record["result"] == "SuccessiveFrameCloud"
    analysis = record["analysis"]
    assert analysis["analysis"] == "successive_frame_cloud"
    assert analysis["parameters"]["molecule_keys"] == [str(k) for k in keys]
    assert analysis["n_pairs"] == cloud.n_pairs


# --- Save composition… and Open composition… (#1438) ----------------------------------


def test_a_saved_composition_opens_again_to_the_same_layer_list(
    section, modelled, tmp_path, qtbot, monkeypatch
) -> None:
    from pyqtgraph.Qt import QtCore, QtWidgets

    path, keys = modelled
    assert section.save_composition(tmp_path / "none.json") is None
    assert section.notice_text == "Open a project to save a composition."
    section.set_project(path)
    assert section.save_composition(tmp_path / "none.json") is None
    assert "Add a selection" in section.notice_text
    assert not (tmp_path / "none.json").exists()
    section.add_series(_saved(path, "late", keys[2:]))
    section.add_series(_saved(path, "early", keys[:2]))
    section.set_plot("population_transition_prob_histogram")
    section.set_model(MODEL)
    section.state_boxes["init_state"].setValue(1)
    section.state_boxes["final_state"].setValue(0)
    out = tmp_path / "composition.json"
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), ""))
    qtbot.mouseClick(section.save_button, QtCore.Qt.MouseButton.LeftButton)
    assert out.is_file(), section.notice_text
    assert "Saved the composition" in section.notice_text
    saved = [(s.label, s.selection.molecule_keys) for s in section.series]

    # Change everything, then open the file again.
    section.set_project(path)
    section.set_plot("population_apparent_e_histogram")
    section.add_series(PlotSeries(ALL_MOLECULES, None))
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", lambda *a, **k: (str(out), ""))
    qtbot.mouseClick(section.open_button, QtCore.Qt.MouseButton.LeftButton)
    assert section.notice_text.startswith("Opened composition.json"), section.notice_text
    assert section.plot_id == "population_transition_prob_histogram"
    assert section.model_name == MODEL
    assert section.state_boxes["init_state"].value() == 1
    assert section.state_boxes["final_state"].value() == 0
    layers = section.layers
    assert [layers.item(i).text() for i in range(layers.count())] == ["late", "early"]
    assert [(s.label, s.selection.molecule_keys) for s in section.series] == saved
    assert section.outcome.drawn
    assert [label for label, _ in section.outcome.results] == ["late", "early"]


def test_opening_names_missing_molecules_and_draws_nothing_for_them(
    section, modelled, tmp_path
) -> None:
    path, keys = modelled
    section.set_project(path)
    section.add_series(_saved(path, "kept", keys[:2]))
    section.add_series(_saved(path, "ghost", [keys[2], "no-such-key"]))
    out = tmp_path / "c.json"
    assert section.save_composition(out) is not None
    section.set_project(path)
    result = section.open_composition(out)
    assert result is not None
    assert "Left out ghost: 1 of its 2 molecules are not in" in section.notice_text
    assert "no-such-key" in section.notice_text
    assert section.series_labels == ["kept"]
    assert [label for label, _ in section.outcome.results] == ["kept"]


def test_a_composition_of_another_project_changes_nothing(section, modelled, tmp_path) -> None:
    from _analysis_store import build_store_with_model

    path, keys = modelled
    section.set_project(path)
    section.add_series(_saved(path, "early", keys[:2]))
    out = tmp_path / "c.json"
    section.save_composition(out)
    states = np.array([[0, 1] * 10 for _ in range(2)])
    (tmp_path / "other").mkdir()
    other, other_keys = build_store_with_model(
        tmp_path / "other", states, np.array([0.25, 0.75]), model_name=MODEL
    )
    section.set_project(other.path)
    section.add_series(_saved(other.path, "theirs", other_keys))
    section.open_composition(out)
    assert "open that project first" in section.notice_text
    assert section.series_labels == ["theirs"]


def test_a_file_of_an_unknown_version_is_refused_with_a_message(
    section, modelled, tmp_path
) -> None:
    import json

    path, keys = modelled
    section.set_project(path)
    section.add_series(_saved(path, "early", keys[:2]))
    out = tmp_path / "c.json"
    section.save_composition(out)
    data = json.loads(out.read_text(encoding="utf-8"))
    data["version"] = 7
    out.write_text(json.dumps(data), encoding="utf-8")
    assert section.open_composition(out) is None
    assert "The composition was not opened" in section.notice_text
    assert "version 7 is not supported" in section.notice_text
    assert section.series_labels == ["early"]


def test_a_composition_with_no_model_draws_with_no_model(section, modelled, tmp_path) -> None:
    from tether.services import SaveCompositionRequest, run
    from tether.services.compositions import series_text

    path, keys = modelled
    out = tmp_path / "c.json"
    series = series_text(_saved(path, "early", keys[:2]))
    run(
        SaveCompositionRequest(
            path=str(out), project=str(path), plot="population_state_number", series=(series,)
        )
    )
    section.set_project(path)
    section.set_model(MODEL)
    section.open_composition(out)
    assert section.plot_id == "population_state_number"
    assert section.model_name is None
    assert not section.outcome.drawn
    assert "needs an idealization model" in section.empty_text


def test_a_state_the_section_cannot_show_is_refused_not_clamped(
    section, modelled, tmp_path
) -> None:
    from tether.services import SaveCompositionRequest, run
    from tether.services.compositions import series_text

    path, keys = modelled
    out = tmp_path / "c.json"
    run(
        SaveCompositionRequest(
            path=str(out),
            project=str(path),
            plot="population_dwell_times",
            series=(series_text(_saved(path, "early", keys[:2])),),
            model=MODEL,
            states=("state=150",),
        )
    )
    section.set_project(path)
    section.add_series(_saved(path, "kept", keys))
    assert section.open_composition(out) is None
    assert "cannot show state 150" in section.notice_text
    assert section.series_labels == ["kept"]


def test_no_model_stays_no_model_when_the_model_list_changes(
    section, modelled, tmp_path, monkeypatch
) -> None:
    import tether.gui.analysis_section as page
    from tether.services import SaveCompositionRequest, run
    from tether.services.compositions import series_text

    path, keys = modelled
    out = tmp_path / "c.json"
    series = series_text(_saved(path, "early", keys[:2]))
    run(
        SaveCompositionRequest(
            path=str(out), project=str(path), plot="population_state_number", series=(series,)
        )
    )
    section.set_project(path)
    section.open_composition(out)
    assert section.model_name is None
    # A fit writes a second model: the list is rebuilt, and the choice stays empty.
    monkeypatch.setattr(page, "idealization_models", lambda _p: ["another", MODEL])
    section.refresh()
    assert section.model_combo.count() == 2
    assert section.model_name is None
    assert not section.outcome.drawn

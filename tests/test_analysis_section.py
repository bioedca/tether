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
* the model choice and the state boxes show only for a plot that reads them.
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
    assert len(listed) == 15
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

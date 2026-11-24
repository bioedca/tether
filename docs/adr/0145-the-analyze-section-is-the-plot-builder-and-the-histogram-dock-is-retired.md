<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0145 — The Analyze section's page is the plot builder, and the population-histogram dock is retired

- **Status:** accepted; changes one consequence of
  [ADR-0124](0124-the-plot-builder-s-composition-unit-renderer-and-staleness-and-frame-time-policies.md)
  (the histogram dock no longer keeps pyqtgraph) and the Analyze part of
  [ADR-0116](0116-the-shell-hosts-its-sections-in-one-stack.md) (Analyze has no dock and no
  menu); their other decisions govern unchanged
- **Date:** 2026-11-24
- **Deciders:** bioedca (the acceptance criteria of #374, approved 2026-11-13 and again with the
  plan change of 2026-11-14)
- **PRD anchor:** §7.7 FR-PLOT (composed plots), §7.11 (the application calls the service layer)
- **Milestone:** M17
- **Issue:** [#374](https://github.com/bioedca/tether/issues/374)

## Context and problem statement

Before #374 the Analyze section was a guide page and a one-action **Analysis** menu. The menu
opened a pyqtgraph `HistogramDock` with the A1 histogram, the one population plot the application
drew; the other fourteen population functions of `tether.analysis` ran only from Python. ADR-0124
chose Matplotlib, through `FigureCanvasQTAgg`, as the one renderer of the plot builder, so that the
figure previewed is the figure exported, and it left the histogram dock on pyqtgraph. #374 builds
the plot builder and says the dock "either becomes a registry entry or is retired in the same PR".
Three things have to be fixed:

1. where the plot registry lives, given that the application may not grow its direct calls into
   the core (`tests/test_gui_core_ratchet.py`, ADR-0100);
2. what becomes of the histogram dock and its menu;
3. what a plot shows when it cannot be drawn.

## Decision drivers

- **What is exported is what was seen** (ADR-0124). Two drawings of the A1 histogram — pyqtgraph
  in a dock and Matplotlib in the builder — would be two figures of one result.
- **The application calls the service layer** (§7.11, ADR-0100): a new direct import from
  `src/tether/gui` into a core package fails the ratchet.
- **Never fabricate** (§7.7): a plot whose model, state or selection is missing must say so, not
  draw a placeholder.

## Considered options

**The histogram dock.**

- **A. Retire it.** The A1 histogram is the first entry of the registry and is drawn like the
  other fourteen. The dock's tests move to the same promises of the new path.
- **B. Keep it beside the builder.** A second renderer of one plot, and a menu that duplicates the
  section's plot list.

**The registry.**

- **(i) A Qt-free module of `tether.services`** (`tether.services.plots`), like the status read
  (`tether.services.status`): drawing a plot writes nothing, so it is a plain function and not a
  registered command.
- **(ii) Inside `tether.gui`.** Fifteen new direct calls into `tether.analysis`, which the
  ratchet refuses.

## Decision outcome

Chosen: **A** and **(i)**.

- `tether.services.plots.PLOTS` holds one `PlotSpec` per population plot function — the fifteen
  #374 names, keyed by the function's own name — with its title, whether it reads an
  `/idealization` model, the state options it takes, the call that computes one series and the
  `tether.analysis.plot_export` renderer that draws the series as one figure (ADR-0132).
  `compose_plot` runs the call once per series, with the series' selection as `molecule_keys`
  (ADR-0109) and the function's own defaults otherwise, and returns either the `Figure` or the
  reason there is none.
- `tether.gui.analysis_section.AnalysisSection` is the Analyze section's page: the plot list, the
  idealization model and the states of a plot that reads them, the layer list (*Add selection*:
  every molecule, or a one-click preset of `tether.analysis.saved_selection`; *Remove*), and a
  `FigureCanvasQTAgg` holding the `Figure` that `compose_plot` returned. Analyze declares no menu
  and no dock.
- `tether.gui.histogram_dock`, the **Analysis** menu, `TetherShell.show_histogram` and
  `make_store_histogram` are removed. `tests/test_histogram_dock.py` and the golden GUI check
  (`tests/test_golden_gui.py`) now check the A1 entry on the section's canvas.
- **When a plot cannot be drawn**, the preview shows the reason in words: no project, no series,
  no idealization model (or not the one chosen), a state the model does not have, or the
  `ValueError` or `KeyError` that the population function or the renderer refused with. An
  unexpected failure shows the same way, so the application never stops on a plot.
- A curation write draws the plot again through the shell's existing single-shot timer of
  `_HISTOGRAM_REFRESH_DEBOUNCE_MS` (250 ms), so a run of keystrokes draws it once; a change of the
  plot, the model, a state or the layer list draws it at once.

### Consequences

- Good: every population plot is reachable from the application, and each is drawn by the
  renderer its export uses.
- Good: the ratchet shrinks by two entries of `shell.py` and one of `histogram_dock.py`.
- Trade-off: the preview keeps the export's white background in the dark theme, because it *is*
  the export figure; the pyqtgraph docks follow the theme as before.
- Trade-off: a Matplotlib redraw is slower than a pyqtgraph one. The section draws on a change of
  its controls or after the debounce, never per mouse event, as ADR-0124 accepted.
- Follow-ups: #375 (stale badge and the opt-out), #376 (frame time), #377 (export from the
  section), #934 and #935 (analysis sets in the layer list), #938 (capability routing), #1085 (the
  successive-frame cloud, a sixteenth entry).

## More information

- Code: `src/tether/services/plots.py`, `src/tether/gui/analysis_section.py`; tests:
  `tests/test_services_plots.py`, `tests/test_analysis_section.py`,
  `tests/test_histogram_dock.py`, `tests/test_golden_gui.py`.
- Number: `claim.py reserve-adr` cannot create its reservation ref from the cloud container that
  wrote this record (HTTP 403), so the number was taken as the next one free after checking
  `main` (0140), every open pull request and the threads' shared number log on 2026-11-24;
  0141 to 0144 were taken by other work the same day.

<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0124 — The plot builder's composition unit, its one renderer, and its staleness and frame-time policies

- **Status:** accepted
- **Date:** 2026-11-16
- **Deciders:** bioedca (the acceptance criteria of #373, approved 2026-11-13; the plan change
  of 2026-11-14 that makes a version of an analysis set the composition unit, cards C4 and C5,
  approved again on 2026-11-14)
- **PRD anchor:** §7.7 FR-PLOT (composed plots), §7.9 FR-EXPORT, §4.1 (no PyQt in the base stack)
- **Milestone:** M17
- **Issue:** [#373](https://github.com/bioedca/tether/issues/373)

## Context and problem statement

FR-PLOT (PRD §7.7) has the application compose one plot from parts of one or more projects,
save the composition, open it again and export it. Before the builder (#374, #375, #376, #377)
can be written, four things have to be fixed:

1. **The composition unit.** What one entry of the layer list is, what it stores, and how it
   names its molecules.
2. **The renderer.** The preview and the export must be the same figure (FR-PLOT: "the exported
   figure is the figure that the user saw"). The export already renders with Matplotlib
   (`tether.analysis.plot_export`, [ADR-0044](0044-matplotlib-base-plot-export-backend.md)); the
   application's histogram dock draws with pyqtgraph.
3. **The staleness default.** A series can hold molecules whose idealization is stale
   ([ADR-0103](0103-the-project-dependency-graph-and-the-stale-rules.md)).
4. **The frame time.** Every time axis and every rate is in frames unless a `dt` is passed, and
   the default `dt = 1.0` silently mis-units a published number
   ([parameters](../reference/parameters.md#param-dt)).

The plan change of 2026-11-14 (#890) answered the first in outline: the composition unit is a
version of an analysis set (#934). [ADR-0109](0109-one-selection-argument-and-one-return-shape-on-the-population-functions.md)
then fixed the seam that version plugs into — the `MoleculeSelection` protocol that every
population function accepts as `molecule_keys` — and left the set itself to #934, which is not
built yet.

## Decision drivers

- **What is exported is what was seen.** One figure object, one renderer.
- **No PyQt in the base stack** (§4.1). `environment.yml` pins `matplotlib-base`, not
  `matplotlib`, on purpose: *"it ships the headless file backends (Agg/PDF/SVG) with NO Qt/Tk
  GUI toolkit, so it does NOT pull PyQt into the PySide6 base stack (keeping the §4.1 no-PyQt5
  invariant intact)"*. **Adding full `matplotlib` is therefore not an available fallback**: the
  pin is a deliberate lock decision ([ADR-0044](0044-matplotlib-base-plot-export-backend.md)),
  and the builder may not reverse it.
- **Never silent.** A molecule that is not plotted is counted on the series; molecules of two
  projects never pool unless an analysis set says so; a series in frames says it is in frames.
- **Reuse, don't re-implement.** The condition, movie and source-file grouping already exists on
  `ConditionQueryResult` (`by_condition`, `by_movie`, `by_source_file`).

## Considered options

**Composition unit.**

- **A. A series is a named `MoleculeSelection`.** A version of an analysis set (#934) is the
  series that spans projects; a `SavedSelection` — `(name, project_path, molecule_keys,
  filter)` — is the one-project series the builder can make now. Both are passed to the
  population functions through the one `molecule_keys` argument.
- **B. A condition is the unit.** Too coarse: a series of one movie or one source file, or of a
  tagged subset, could not be made; and it does not span projects.
- **C. A bare list of `molecule_key`.** No name, no N, no record of what produced it, and it
  would select the same keys in any project it is handed.

**Renderer.**

- **(i) Matplotlib in the preview through `FigureCanvasQTAgg`**, the same `Figure` that
  `plot_export` writes to PDF, SVG and PNG.
- **(ii) pyqtgraph in the preview, Matplotlib for the export.** Two renderers; the exported
  figure is a second drawing of the same data and can differ (ticks, bin edges drawn, fonts).
- **(iii) Full `matplotlib`.** Ruled out by the driver above. One finding is recorded here
  because it dates the reason the pin gives, not the pin: on 2026-11-16,
  `micromamba repoquery depends -c conda-forge "matplotlib=3.11.0=py312*"` lists `pyside6`, not
  a PyQt package, as the Qt binding conda-forge's `matplotlib` adds to `matplotlib-base` (also
  `tornado`). Full `matplotlib` would therefore no longer pull PyQt5 into the base stack. The pin
  stays all the same: the spike below shows `matplotlib-base` is enough, so the builder needs no
  lock change, and changing a lock is a decision of its own.

## Decision outcome

Chosen: **A** and **(i)**.

### The composition unit

- A **series** is one entry of the layer list. Its molecules are a `MoleculeSelection`
  (ADR-0109): a version of an analysis set (#934), one condition group inside one, or a
  `SavedSelection` of one project.
- `tether.analysis.saved_selection.SavedSelection` is Qt-free and holds `name`,
  `project_path` (resolved), `molecule_keys` and the `SelectionFilter` that produced it
  (the `query_molecules` arguments, plus the `(grouping, value)` of a preset). It serializes to
  a versioned JSON form (`SAVED_SELECTION_VERSION = 1`), so a composition survives a restart.
- **Its membership is frozen when it is made**, as an analysis-set version's is (#934: "a
  change of the membership makes a new version"). Editing a tag later never changes what a
  saved selection plots. The filter is kept beside the keys so the builder can show where the
  molecules came from and run it again as a *new* selection: `SelectionFilter.run` runs the
  query and then reapplies a preset's group, so a preset's filter on an unchanged project gives
  back exactly that preset.
- In any project other than its own, a `SavedSelection` names **no** molecule
  (`molecule_keys_in` returns an empty tuple), so the same keys in a copy of the project are
  never pooled by accident. Pooling across projects, re-linking a moved project and the
  checksum that decides it is the same project are #934's.
- The one-click presets (`preset_selections(project, by)`, `by` one of `condition`, `movie`,
  `source_file`) run `query_molecules` once and split it with the result's own `by_condition`,
  `by_movie` or `by_source_file`, in first-seen order; each selection is named by its group
  value and records it. An empty group value (the movie of a movie-less molecule from an
  analysis-only import) is named `(no movie)` and keeps the empty value in its group.

### The renderer: one, Matplotlib

The preview is a `FigureCanvasQTAgg` holding the `Figure` a `plot_export.render_*` function
returns, and the export writes that same `Figure`. pyqtgraph stays where it is (the trace and
histogram docks) and is not the plot builder's renderer.

**Spike, run 2026-11-16 in the locked base environment** (Linux x86-64, conda-forge, versions as
`conda-lock.yml` pins them: Python 3.12.13, `pyside6` 6.11.1, `qt6-main` 6.11.1,
`matplotlib-base` 3.11.0, `qtpy` 2.4.3), with `QT_QPA_PLATFORM=offscreen`:

```python
import PySide6
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
```

Result: **it passes.** The import succeeds; `matplotlib.backends.qt_compat.QT_API` is
`PySide6`; a `FigureCanvasQTAgg` holding a histogram `Figure` draws at 300×200 under a
`QApplication`; the same `Figure` saves to PDF and SVG; and `PyQt5`, `PyQt6` and `PySide2` are
neither installed nor loaded. The Qt backends are pure-Python modules that `matplotlib-base`
ships; what `matplotlib` adds over it is the dependency on a Qt binding. `qt_compat` (3.11.0)
binds to a Qt module already imported, else to `QT_API`, else to the first binding it finds,
and in the base stack PySide6 is the only one. The spike is kept as a test,
`tests/test_analysis_saved_selection.py::test_the_renderer_spike_binds_matplotlib_base_to_pyside6`,
so the three-OS matrix re-runs it on every change.

**If it ever fails** (a lock bump that drops the Qt backends, or a platform where the canvas
does not draw), **the builder's export is blocked** on the build or platform where it fails,
and stays blocked until a new ADR picks a renderer that keeps FR-PLOT's guarantee that the
exported figure is the figure the user saw. Option (ii) is **not** a fallback: a pyqtgraph
preview beside a Matplotlib export is two drawings of the same data, and labelling the preview
as a preview does not make the export the figure that was seen, so shipping it would ship a
builder that breaks the requirement. Blocked means the builder says why it cannot export and
writes no figure, rather than writing one that was never on screen. On the three CI platforms,
the spike test imports `FigureCanvasQTAgg` and checks that it selects PySide6. A missing Qt
backend fails the test when PySide6 is present. Without PySide6, the test skips. A canvas that
imports but cannot draw can still pass this test. (Amended 2026-11-17 by
[#1192](https://github.com/bioedca/tether/issues/1192): as first accepted, this paragraph
named (ii) as the fallback, with the preview labelled, while also saying (ii) breaks "what is
exported is what was seen".)

### The staleness default: exclude, badge, stamped opt-out

What "stale" means is [ADR-0103](0103-the-project-dependency-graph-and-the-stale-rules.md)'s:
a fit is left out of the TDP, dwell and plot results unless the user asks to include stale fits.
For the builder (#375 implements it):

- **Exclude** by default: every model-derived series is drawn with `include_stale=False`, the
  default of the six population functions that read `/idealization` (ADR-0109).
- **Badge**: a series that excluded stale molecules shows how many it excluded beside its N, so
  a smaller N is never unexplained.
- **Stamped opt-out**: the user may turn `include_stale` on for one series; the export's
  provenance records the value for that series, so a figure drawn over stale fits says so.

**The model-Gaussian overlay.** Before #369, `population_model_gaussian_overlay` had **no**
staleness lever, **no** `molecule_keys` and **no** `include_rejected`: it drew the model's
Gaussians whatever the cohort, the hazard [parameters](../reference/parameters.md) describes in
its `include_stale` row. The other entry point with a missing lever was
`per_condition_apparent_e_histograms`, which selected by condition, key, category and tag but
not by molecule. #369 gave both the one `molecule_keys` argument, and gave the overlay
`include_rejected` and `include_stale`. The overlay's curves are still the model's own fitted
means, variances and populations, which no selection changes (ADR-0042, ADR-0109): the levers
decide only *whether* it is drawn. So **the builder warns before it draws the overlay over a
cohort with stale members** — the curves include the fit of molecules the series excluded or,
with the opt-out, of molecules whose traces have changed since.

### The frame-time policy

The builder takes `dt` from `/movies.frame_time` of the movies its series read, never from the
`1.0` default (#376 implements it):

- A series whose movies share one frame time is drawn in seconds with that `dt`.
- A series whose movies have **different** frame times is **refused** with the reason, never
  resampled or averaged — the FR-PLOT rule for series whose grids differ.
- A series with **no** frame time (`frame_time` is zero when the source movie declared none) is
  drawn on a **frame-index axis whose label says so**, and its rates are per frame.
- The `dt` used is recorded in the export's provenance for each series.

### The composition file (amended 2026-11-26, #1438)

The builder's save and open (#1438) need a file form of a whole composition, which the record
above does not fix. It is this:

- A composition file is one JSON object with exactly six keys: `format`
  (`"tether-plot-composition"`), `version` (an integer, `1` today), `plot` (the `id` of a registry
  entry), `project_path` (the resolved project the plot reads), `series` and `parameters`. It
  holds no trace data. `series` is the layer list in drawing order; each entry is `label` and
  `selection`: the `SavedSelection` form above, or `null` for every molecule of the project.
  `parameters` is `model_name` and `states` (each state option the plot takes, by name).
- A reader refuses another `version`, an unknown key and a repeated label, with a message. A
  later field (#375's staleness choice, #376's frame time) is added with a new version, not
  read past.
- Opening checks each series against the project: a series whose project cannot be found, or
  that names a molecule the project does not have, is named with what is missing and is left
  out of the layer list. It is never drawn from a guess. The builder reads one project, so a
  composition of a project other than the open one changes nothing.
- Saving and opening are the `save-composition` and `open-composition` service commands
  (`tether.services.compositions`), each with a command-line route (ADR-0100).

### Consequences

- Good: the builder has one type to put in a layer list, today, and #934's version drops into the
  same slot without a change to the population functions or to the builder's loop.
- Good: one renderer; the export path that already writes stamped PDF/SVG/PNG is the preview.
- Good: the base lock is unchanged; no new dependency.
- Trade-off: a `SavedSelection` does not follow its project when the file moves (it is matched by
  resolved path). Re-linking by checksum belongs to the analysis set (#934); a one-project
  selection is re-made with its recorded filter.
- Trade-off: the Matplotlib canvas is slower than pyqtgraph to redraw. The builder redraws on a
  change of the layer list, not per mouse event, so this is accepted; the trace and histogram
  docks keep pyqtgraph.
- Follow-ups: #374 (builder and save), #375 (levers and the staleness badge), #376 (frame time),
  #377 (export), #934 (analysis sets).

## More information

- Code: `src/tether/analysis/saved_selection.py`; tests:
  `tests/test_analysis_saved_selection.py` (run without a `QApplication`). The composition file:
  `src/tether/services/compositions.py`; tests: `tests/test_services_compositions.py` and
  `tests/test_analysis_section.py`.
- Related: [ADR-0044](0044-matplotlib-base-plot-export-backend.md) (the Matplotlib export and
  `matplotlib-base`), [ADR-0042](0042-a1-model-gaussian-overlay.md) (the overlay is the model's
  own Gaussians), [ADR-0103](0103-the-project-dependency-graph-and-the-stale-rules.md) (stale),
  [ADR-0109](0109-one-selection-argument-and-one-return-shape-on-the-population-functions.md)
  (the selection seam), #369, #890, #934.
- Number: `claim.py reserve-adr` cannot create its reservation ref from the cloud container that
  wrote this record (HTTP 403), so the number was taken as the next one free after checking
  `main` and every open pull request on 2026-11-16.

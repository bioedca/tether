<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0127 — Several series on one figure: one color cycle, small multiples where an overlay has no meaning, and one grid

- **Status:** accepted
- **Date:** 2026-11-17
- **Deciders:** bioedca (the acceptance criteria of #371, approved 2026-11-13 and again with the plan
  change of 2026-11-14; moved to ready on 2026-11-17)
- **PRD anchor:** §7.7 FR-PLOT (a plot is a layer list), §7.9 FR-EXPORT (per-series provenance)
- **Milestone:** M17
- **Issue:** [#371](https://github.com/bioedca/tether/issues/371)

## Context and problem statement

FR-PLOT (PRD §7.7) composes one figure from several series, each a named selection of molecules
([ADR-0124](0124-the-plot-builder-s-composition-unit-renderer-and-staleness-and-frame-time-policies.md)
of [#373](https://github.com/bioedca/tether/issues/373) fixes what a series is and that the renderer is `tether.analysis.plot_export`). Each of the
thirteen `render_*` functions drew exactly one analysis result. Before the builder (#374) can show
a layer list, every renderer has to take several results, and five things have to be fixed:

1. how a series is passed, and what a figure of one series looks like;
2. which colors the series take;
3. which plots overlay their series and which draw one panel per series;
4. what happens when two series are not on one grid;
5. how the parameters of each series reach the provenance file.

The PRD already states the rules for 3 and 4 in outline: "Where an overlay has no meaning (the 2-D
densities, the TDP and the raw FRET cloud), the series are drawn as small multiples. Two series whose
grids differ (bin edges, value range or frame time) are refused with the reason and never
resampled". This record fixes them for each renderer.

## Decision drivers

- **Nothing changes for a caller of one result.** The thirteen figures are checked artist by artist
  in `tests/test_plot_export.py`, and the parity gallery draws them.
- **Re-rendering one composition gives the same bytes.** The module already pins `svg.hashsalt` and
  the creation date for this reason.
- **A color is a claim about a series.** Two series that read as one, to a reader with a color-vision
  deficiency or on a printed page, misreport the figure. The colors are a scientific-communication
  choice as much as a cosmetic one.
- **Never resample silently** (FR-PLOT). A bin moved to another grid is a number the user did not ask
  for.
- **The export records what was drawn** (§7.9, invariant §1.3(4)).

## Considered options

**How a series is passed.**

- **A. Each renderer's first argument also takes an ordered sequence of `(label, result)`.** A bare
  result is drawn as before.
- **B. A second function per renderer** (`render_histogram1d_series`, …). Twenty-six public names
  for one plot each, and the builder has to pick between two.
- **C. A series object** (label, result, style). The style is the cycle's to give, not the caller's,
  and the label is all the caller adds.

**The colors.**

- **(i) `tab10`**, which the per-condition overlay uses. Under simulated protanopia its orange and
  green differ by 1.3 CIEDE2000 units, and four of its ten colors reach less than 3 : 1 on white.
- **(ii) The whole Okabe–Ito set.** Its yellow, orange and sky blue reach 1.3 : 1, 2.2 : 1 and 2.3 : 1
  on the white export background, below the 3 : 1 WCAG 2.x floor for graphical objects that
  `tests/test_gui_theme.py` applies to the trace palette; its black is the color of the reference
  lines and the model curves.
- **(iii) Four colors of the Okabe–Ito set** (blue `#0072b2`, vermilion `#d55e00`, bluish green
  `#009e73`, reddish purple `#cc79a7`), then the line style. Each reaches 3 : 1 on white, and every
  two differ by at least 12.2 CIEDE2000 units under each of the three simulated deficiencies (the
  model and floor of `tests/test_gui_theme.py`: Machado et al. 2009 at severity 1.0, CIEDE2000 ≥ 10).
- **(iv) Paul Tol's "muted" set, six colors that pass both floors.** Its closest pair under normal
  vision differs by 15.0 units, against 37.0 for (iii), so on a normal display two of its series
  are much closer than any two of (iii).

**Several 2-D results.**

- **Overlay** them with transparency: one density hides the other, and the color bar no longer
  says which.
- **Small multiples**, one panel per series, sharing the axes and one color scale.

## Decision outcome

Chosen: **A**, **(iii)**, **small multiples** for the 2-D results, and the rest as below.

### A series

Every renderer takes either a bare result, as before, or an ordered sequence of `(label, result)`.
The labels must be distinct non-empty strings; a malformed sequence is refused with a `TypeError` or
`ValueError` that names the series. **A one-series sequence draws the bare result's figure, byte for
byte** in PDF, SVG and PNG: the label is not drawn, and it is recorded in the provenance. A test
checks the bytes for all thirteen renderers.

### The color cycle

`SERIES_COLOR_CYCLE = "okabe-ito-4"`: series *k* takes `SERIES_COLORS[k mod 4]`, and the line style
(`-`, `--`, `-.`, `:`) changes every four series; a bar chart uses a hatch per line style instead.
`series_style(k)` gives both, so the builder can color its layer list with the same cycle. The cycle
is deterministic: the same position always takes the same style. After 16 series it repeats, and the
legend label is the only thing that tells series 17 from series 1. The colors are those of Wong
(2011), *Points of view: Color blindness*, Nature Methods 8, 441,
[doi:10.1038/nmeth.1618](https://doi.org/10.1038/nmeth.1618), the source `tether.gui.theme`
already cites for its dark `safe` palette; no new source is introduced. The four values agree with
`palettes.Colorblind8` of the `bokeh` package, version 3.6.3 (the wheel
`bokeh-3.6.3-py3-none-any.whl`, SHA-256 `1c219e2afe1405e6ada212071ac3bee91c95acfd1aa6d620eb6f61a751407747`,
read on 2026-11-17), the check the theme records for its colors.
`tests/test_plot_export_series.py` checks the 3 : 1 floor and the CIEDE2000 floor on every pair.

A one-series figure keeps the color it has always had, which is not in the cycle (`#4c72b0` for the
A1 histogram, for example). That follows from "nothing changes for a caller of one result".

### Overlay or small multiples, per renderer

| Renderer | Several series | The grid the series must share |
|---|---|---|
| `render_histogram1d` | overlay: unfilled outlines; a bootstrap band and a model total in the series color (the per-state components are left out) | bin edges, value range, density |
| `render_dwell_survival` | overlay: markers, fit and residuals in the series color | frame time `dt` |
| `render_transition_prob` | overlay: unfilled outlines, KDE in the series color | bin edges, probability range, density, the `init → final` pair |
| `render_state_number` | grouped bars | the state numbers |
| `render_cross_correlation` | overlay | lags, normalization |
| `render_alpha_shape` | overlay of the boundaries, each cloud faint in its color | frame time; the time and signal range of the clouds given |
| `render_state_number_elbow` | overlay; the chords are left out | none: `k` is a count, and the axis carries every `k` probed |
| `render_histogram2d` | small multiples | time and signal bin edges, frame time, signal range, density |
| `render_transition_density` | small multiples | signal bin edges, signal range, density |
| `render_raw_fret_cloud` | small multiples | time and signal bin edges, time and signal range, frame time |
| `render_transition_sync_histogram2d` | small multiples | time and signal bin edges, frame time, signal range, density |
| `render_per_condition_histograms` | small multiples; a condition keeps its color in every panel | bin edges, value range, density |
| `render_population_anticorrelation` | small multiples, stacked | none: the axis is frames of each molecule's window |

The four 2-D results the issue names are small multiples. Two more are: the per-condition overlay,
whose curves already use color for the condition, so a second overlay would need a second channel
to tell the selection; and the event raster, whose rows are molecules of one series. Small
multiples share their axes and **one color scale**, computed over every panel (for the TDP, the
log or linear scale of the B1 plot over the bins of all panels), so a color means one value in
every panel; one color bar serves them all.

### One grid, or a refusal

Every series is compared with the first, key by key, with no tolerance. The first key that differs
is refused with a `ValueError` that names the series, the key and both values, and says that Tether
does not resample. A key that is a lever of the series rather than its grid (per-molecule equal
weight, `nskip`, the HDR coverages, the scan window) may differ; it is recorded per series.

### The provenance of each series

A renderer given a sequence records, on the figure, one entry per series: its `label`, its `result`
type, the `parameters` that result carries (every scalar and range, and the arrays that are its grid
or its probed values: bin edges, state numbers, lags, `k`, HDR coverages; and the parameters of a
model drawn with it: a Gaussian overlay's means, variances and weights, a dwell fit's rates and
amplitudes with their errors; never its data, a model's sampled curve included), and its
`display` (color and line style, or its panel). A model overlay or a cloud passed with a series is
recorded with it. `export_figure` writes the list to `parameters.series`, and the figure layout to
`parameters.display` (`series_layout`, `series_cycle`, `series_color_scale`). The caller may add its
own keys per series (the selection and its version, the levers, the frame time it chose) as
`parameters["series"]`, one dict per series; a key that contradicts the figure's record is refused,
as a contradicting `display` entry already is.

### Consequences

- Good: one public name per plot, and every existing call draws the same bytes.
- Good: the builder (#374) and `tether plot` (#659) pass their layer list straight through, and the
  export records each series without the caller restating it.
- Good: every pair of series colors stays apart for the three common color-vision deficiencies.
- Trade-off: four colors is few; series 5 to 16 differ from 1 to 4 by line style, which a bar chart
  shows as a hatch and a marker-only curve (the dwell survival markers) does not show at all — there
  the fit line and the legend tell them apart.
- Trade-off: a one-series figure does not take the first cycle color, so adding a second series
  recolors the first. Changing that would change every existing figure.
- Follow-ups: #374 (the builder draws its layer list through these renderers), #377 (the composed
  export), #934 (the analysis set whose versions are the series), #946 (the figure footer).

## More information

- Code: `src/tether/analysis/plot_export.py`; tests: `tests/test_plot_export_series.py` (and
  `tests/test_plot_export.py` unchanged for the one-result figures).
- The color measurements above were made with the helpers of `tests/test_gui_theme.py` on
  2026-11-17 (contrast against `#ffffff`; the smallest CIEDE2000 difference per vision type for the
  four chosen colors: normal 37.0, protanopia 12.2, deuteranopia 15.9, tritanopia 12.4).
- Related: [ADR-0124](0124-the-plot-builder-s-composition-unit-renderer-and-staleness-and-frame-time-policies.md) of #373 (the composition unit and the renderer),
  [ADR-0109](0109-one-selection-argument-and-one-return-shape-on-the-population-functions.md)
  (the selection seam), [ADR-0044](0044-matplotlib-base-plot-export-backend.md) (the Matplotlib
  export), the trace palette of [`tether.gui.theme`](../reference/gui-theme.md).
- Number: `claim.py reserve-adr` cannot create its reservation ref from the cloud container that
  wrote this record (HTTP 403), so the number was taken as the next one free after checking `main`
  and every open pull request on 2026-11-17.

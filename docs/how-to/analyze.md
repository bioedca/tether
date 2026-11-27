---
description: "Draw a FRET-efficiency histogram, a transition-density plot and dwell-time survival curves from an idealized project, and save each as a figure."
---

# Draw a FRET histogram, a TDP and dwell-time survival plots

## Who this page is for

You have a `.tether` project whose molecules have been curated and idealized, as in
[Idealize traces into FRET states](idealize.md). You want the three population plots that most
smFRET papers report, each saved as a figure:

- A FRET-efficiency histogram.
- A transition-density plot (TDP).
- A dwell-time survival curve for each state.

The [Seven-plot parity gallery](../analysis/parity-gallery.md) shows what each plot shows, and
how it matches its tMAVEN counterpart. This page tells you how to make them.

## Before you start

- The project file, here called `movie.tether`, closed in the desktop application.
- The name of the fitted model. A whole-project fit or a `tether batch` run stores it as
  `vbconhmm`, and an ebFRET fit as `ebhmm`. The histogram needs no model.
- The molecules of one experimental condition. Each plot pools every molecule that the
  analysis admits, whatever its condition. So a plot of one condition needs a project or a
  `molecule_keys` selection with only the molecules of that condition.
- A project can hold more than one condition. Every molecule gets the provisional condition
  parsed from the filename of its movie. So make sure that each movie holds molecules of one
  condition only.
- One folder for each project. Run the script for each project in its own folder. Then
  the figures of one project do not overwrite those of another.
- Your movie's frame time in seconds. Dwell times and rates come out **per frame** unless
  you supply it.
- The Python that came with Tether. At the default install locations, it is
  `~/tether/envs/tether/bin/python` on Linux and `~/Tether/envs/tether/bin/python` on macOS.
  On Windows, it is `$env:USERPROFILE\Tether\envs\tether\python.exe`. The application
  window shows the three plots. You save them as figures from Python.

## 1. Look at the plots in the application window

The **Analyze** section draws each population plot of Tether. It has three parts:

- **Plot** — the list of plots. A plot that reads a fit, such as the TDP, also shows the
  **Idealization model** to read. The dwell times and the transition probabilities also show
  the states.
- **Layers** — the series of the plot. **Add selection** adds **All molecules**, or one
  series for each condition, movie or source file of the project. **Remove** takes out the
  selected series. Each series has its own color on the figure.
- The figure. It is the same figure that an export writes.

1. Open the project with `tether-gui movie.tether`.
2. Choose **View → Section → Analyze**.
3. In **Plot**, choose **FRET-efficiency histogram (A1)**.
4. Click **Add selection**, then **All molecules**.

The histogram shows. The status bar names the plot and how many molecules went into each
series, for example:

```text
FRET-efficiency histogram (A1) — All molecules: 398 molecule(s)
```

Rejected molecules are left out. Choose **Transition-density plot (B1)** or **Dwell-time
survival (B2)** in **Plot** to see the other two plots over the same series. If the project has
no fit yet, the section says so in place of the figure. To save a plot as a figure, use the
script below. **View → Section → Curate and correct** takes you back to the traces.

### Stale fits

A fit is *stale* for a molecule when the molecule's inputs changed after the fit. A new α or γ
does that, and so does a new extraction. A plot that reads a fit leaves the stale molecules of
each series out. Each series that left molecules out says how many beside its name in
**Layers**, for example:

```text
All molecules — 3 stale excluded (inputs changed since the fit)
```

The status bar says it beside the series' count too. You can draw the stale molecules of one
series anyway. Select the series and check **Include stale fits in the selected series**. The
row then says `includes 3 stale`.

The choice is per series. An export of the figure records it for each series in its provenance
file. The keys are `include_stale`, `stale_excluded` and `stale_included`, so a figure drawn
over stale fits says so. A composition file does not keep the choice: an opened composition
leaves the stale molecules out again.

The **FRET-efficiency histogram with model Gaussians (A1)** draws the model's own curves. They
come from the fit of every molecule the model was fitted on, the stale ones too. So when a
series has stale molecules, a warning above the figure says so. The histogram under the curves
leaves the stale molecules out unless you check the box.

The better fix is to [fit the stale molecules again](idealize.md#after-you-change-the-project).

### Save the composition and open it again

A *composition* is the plot, its idealization model and states, and its layer list. Save it to
come back to the same plot later.

- **Save composition…** writes the composition to a `.json` file. The file names the project
  and the plot. It names each series with its molecules, and the model and the states. It
  holds no trace data. A later change of the project does not change it.
- **Open composition…** reads a file back. The plot, the model and the states are as you saved
  them. The layer list has the same series in the same order. Each series keeps its name. A
  selection keeps the molecules it held when you saved it. **All molecules** has no fixed
  list. It is every molecule of the project when you open the file, also one added later.

Open the project of the composition first. When the file reads another project, the section
says so and changes nothing. A series can name molecules that are no longer in the project.
The section then names the series and what is missing under the buttons. It leaves that
series out and never draws it from a guess. A file of an unknown format version is refused
with a message.

The command line has the same two actions:
[`tether save-composition`](../cli.md#tether-save-composition) and
[`tether open-composition`](../cli.md#tether-open-composition).

## 2. Make and save all three plots

1. Save the script below as `plots.py` beside the project.
2. Set `dt` to your frame time.
3. If your fit is an ebFRET fit, set `model_name` to `ebhmm`.
4. Run the script with Tether's Python.

Each analysis takes its settings from one dictionary. The same dictionary goes into the
provenance file of the figure. So the record names every setting that made the plot.
`molecule_keys=None` means every molecule that the analysis admits. To analyze only some
molecules, put their keys there. Then the record names them too:

```python
from tether.analysis.dwell import population_dwell_times
from tether.analysis.histogram import population_apparent_e_histogram_ci
from tether.analysis.plot_export import (
    export_figure,
    render_dwell_survival,
    render_histogram1d,
    render_transition_density,
)
from tether.analysis.tdp import population_transition_density

project = "movie.tether"
model_name = "vbconhmm"
dt = 0.1  # seconds per frame: replace with your movie's frame time

hist_settings = dict(
    molecule_keys=None,
    intensity_quantity="corrected",
    bins=151,
    value_range=(-0.25, 1.25),
    density=True,
    per_molecule_equal_weight=False,
    include_rejected=False,
    n_resamples=1000,
    ci_level=0.95,
    seed=0,
)
tdp_settings = dict(
    molecule_keys=None,
    nskip=2,
    signal_bins=101,
    signal_range=(-0.25, 1.25),
    density=False,
    include_rejected=False,
    include_stale=False,
)
dwell_settings = dict(
    molecule_keys=None,
    dt=dt,
    include_first=False,
    model="single",
    ci_level=0.95,
    include_rejected=False,
    include_stale=False,
)

hist = population_apparent_e_histogram_ci(project, **hist_settings)
export_figure(
    render_histogram1d(hist),
    "histogram",
    tether_export="plot:a1",
    source=project,
    parameters=hist_settings,
)
print(f"histogram: {hist.histogram.n_molecules} molecules, {hist.histogram.n_samples} frames")

tdp = population_transition_density(project, model_name, **tdp_settings)
export_figure(
    render_transition_density(tdp),
    "tdp",
    tether_export="plot:b1",
    source=project,
    parameters={"model_name": model_name, **tdp_settings},
)
print(f"TDP: {tdp.n_transitions} transitions from {tdp.n_molecules} molecules")

dwells = population_dwell_times(project, model_name, **dwell_settings)
for state, analysis in dwells.items():
    export_figure(
        render_dwell_survival(analysis),
        f"dwell-state-{state}",
        tether_export="plot:b2",
        source=project,
        parameters={"model_name": model_name, "state": state, **dwell_settings},
    )
    if analysis.fit is not None and analysis.fit.success:
        rates = ", ".join(
            f"{rate:.3g} ± {ci:.2g}" for rate, ci in zip(analysis.fit.rates, analysis.fit.rate_ci)
        )
        print(f"state {state} (E = {analysis.level:.2f}): k = {rates} per second")
```

Each plot is written three times, as `histogram.pdf`, `histogram.svg` and `histogram.png`.
Beside them, `histogram.provenance.json` records what made the plot. The same is true of
`tdp.*` and of one `dwell-state-N.*` set per state.

The TDP uses a log color scale, so a rare transition stays visible next to a frequent one.
The `tdp.provenance.json` file records the scale. To get a linear scale, call
`render_transition_density(tdp, color_scale="linear")`.
The histogram carries a shaded 95% confidence band, drawn from 1000 bootstrap resamples of
the molecules. The resampling is seeded, so the band is the same every time the script runs
([`n_resamples`](../reference/parameters.md#param-n-resamples),
[`ci_level`](../reference/parameters.md#param-bootstrap-ci-level),
[`seed`](../reference/parameters.md#param-bootstrap-seed)).

[Plot export](../reference/exports.md#4-plot-export-pdf-svg-png) describes the files.
To also save the numbers each figure shows, add `data_csv=True` to an `export_figure` call.
`histogram.csv` then holds the bins, the counts and the confidence band. A dwell plot adds
`dwell-state-N.model.csv` for its fitted curve and `dwell-state-N.fit.csv` for its rates.
[The numbers behind a figure](../reference/exports.md#the-numbers-behind-a-figure) lists every
column and its unit.

## 3. Check the result

1. The molecule count printed for the TDP can be **smaller** than that of the histogram. The
   reason is that the TDP counts only molecules that change state at least once. The TDP and
   the dwell times also leave out stale molecules. Those are molecules whose inputs changed
   after they were fitted, such as a new α or γ. If you corrected the project after you
   idealized it, [fit them again](idealize.md#after-you-change-the-project).
2. A state can be missing from the dwell-time results. A state gets a curve only if at least
   one of its dwells both started and ended inside a trace. So a state that molecules stay in
   until the trace ends has no curve.
3. The dwell-time axis reads seconds when `dt` is anything other than 1, and frames at
    `dt = 1` ([`dt`](../reference/parameters.md#param-dt)). With a frame time of exactly
    1 second, the axis still says frames, but the numbers are the same. The printed rates are
    per second either way.

    One `dt` applies to every molecule. So it is right only when all the molecules come from
    movies recorded at that frame time. If your project holds movies recorded at different
    frame times, do these steps once for each frame time:

    1. Set `dt` to that frame time.
    2. Put the molecules of the movies with that frame time as `molecule_keys` in
       `dwell_settings`.
    3. Give the dwell files of this run names of their own. For example, use
       `f"dwell-100ms-state-{state}"` in place of `f"dwell-state-{state}"`. A run that
       reuses a name overwrites the files that the earlier run wrote.
    4. Run the script.

4. The script prints one rate for each state whose fit succeeded, from the
   single-exponential fit. After the `±` comes the half-width of its 95% confidence interval
   ([dwell-fit `ci_level`](../reference/parameters.md#param-dwell-model)). A state whose fit
   fails still gets its plot, but no rate. Too few dwells is one cause. For a two-exponential
   fit, set `model="double"` in `dwell_settings`. The script then prints both rates, slower
   first ([dwell-fit `model`](../reference/parameters.md#param-dwell-model)).
   `analysis.fit.mean_dwell_time()` gives one number for such a fit. It is the mean dwell
   time `(A1/k1 + A2/k2) / (A1 + A2)` and its propagated error. For rates fitted by another
   program, call `tether.analysis.mean_dwell_time`. First put each error beside its own
   parameter. tMAVEN sorts the rates and amplitudes, but not their errors.

The histogram here is of **apparent** efficiency, from the background-subtracted traces. To
report a γ-corrected efficiency instead, see
[Where the corrected efficiency appears](apply-corrections.md#where-the-corrected-efficiency-appears).

## 4. Fit Gaussians to the histogram

To report the population of each FRET state from the histogram, fit one Gaussian for each
state. Give one starting mean for each state. Add these lines to the end of `plots.py`:

```python
from tether.analysis.gaussian_fit import fit_gaussians

fit = fit_gaussians(hist.histogram, (0.0, 0.33))
if fit.success:
    for mean, fraction, error in zip(fit.means, fit.fractions, fit.fraction_stderr):
        print(f"E = {mean:.3f}: fraction {fraction:.4f} ± {error:.4f}")
```

The Gaussians come back in order of mean, whatever the order of the starting means. Each
fraction is the area of its Gaussian divided by the sum of the areas. The `±` is one standard
error of the fit. The area is `A·√(2π)·σ`, the area under the whole Gaussian.

To use the area inside the histogram range instead, pass `area="bin_sum"`. That area is the sum
of the Gaussian over the bin centres. The analysis of Fig. 5c in Campos-Chavez et al. 2024 uses
it. The two areas differ when a Gaussian goes past an end of the range.

The error is the precision of the least-squares fit to the bins. It does not include the
differences between molecules, because the frames of one molecule are not independent of each
other. [ADR-0120](../adr/0120-a-sum-of-gaussians-fit-of-a-fret-histogram-and-its-two-area-definitions.md)
has the method and how it reproduces the published numbers.

## Next

[Export the results for a figure or another tool](export-results.md).

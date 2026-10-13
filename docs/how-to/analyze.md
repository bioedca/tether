# Draw a FRET histogram, a TDP and dwell-time survival plots

## Who this page is for

You have a `.tether` project whose molecules have been curated and idealized, as in
[Idealize traces into FRET states](idealize.md), and you want the three population plots
most smFRET papers report: a FRET-efficiency histogram, a transition-density plot (TDP) and a
dwell-time survival curve for each state, each saved as a figure. What each plot shows, and how
it matches its tMAVEN counterpart, is on the [Seven-plot parity
gallery](../analysis/parity-gallery.md); this page is how to make them.

## Before you start

- The project file, here called `movie.tether`, closed in the desktop application.
- The name of the fitted model. A whole-project fit or a `tether batch` run stores it as
  `vbconhmm`, and an ebFRET fit as `ebhmm`. The histogram needs no model.
- Your movie's frame time in seconds. Dwell times and rates come out **per frame** unless
  you supply it.
- The Python that came with Tether: `~/tether/envs/tether/bin/python` on Linux,
  `~/Tether/envs/tether/bin/python` on macOS, or
  `$env:USERPROFILE\Tether\envs\tether\python.exe` on Windows, at the default install
  locations. Only the histogram has a menu item; the other two plots are made from Python.

## 1. Look at the histogram in the window

Open the project with `tether-gui movie.tether` and choose **Analysis → Population
histogram…**. A panel opens with the histogram, and the status bar names how many molecules
and frames went into it, for example:

```text
Population histogram — 398 molecule(s), 71640 frame(s)
```

Rejected molecules are left out. The panel is for looking; to save the plot as a figure, use
the script below.

## 2. Make and save all three plots

Save this as `plots.py` beside the project, set `dt` to your frame time, and run it with
Tether's Python. Each analysis takes its settings from one dictionary, and the same
dictionary goes into the figure's provenance file, so the record names every setting that
made the plot. `molecule_keys=None` means every molecule the analysis admits; to analyze only
some, put their keys there, so the record names them too:

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
        rates = ", ".join(f"{rate:.3g}" for rate in analysis.fit.rates)
        print(f"state {state} (E = {analysis.level:.2f}): k = {rates} per second")
```

Each plot is written three times, as `histogram.pdf`, `histogram.svg` and `histogram.png`,
with a `histogram.provenance.json` beside them recording what made it; likewise `tdp.*` and
one `dwell-state-N.*` set per state.
The histogram carries a shaded 95% confidence band, drawn from 1000 bootstrap resamples of
the molecules. The resampling is seeded, so the band is the same every time the script runs
([`n_resamples`](../reference/parameters.md#param-n-resamples),
[`ci_level`](../reference/parameters.md#param-bootstrap-ci-level),
[`seed`](../reference/parameters.md#param-bootstrap-seed)).
[Plot export](../reference/exports.md#4-plot-export-pdf-svg-png) describes the files.

## 3. Check the result

1. The molecule count printed for the TDP can be **smaller** than the histogram's, because
   the TDP counts only molecules that change state at least once. The TDP and the dwell times
   also leave out stale molecules, whose inputs changed after they were fitted, such as a new α or γ; [fit them
   again](idealize.md#after-you-change-the-project) if you corrected the project after
   idealizing.
2. A state can be missing from the dwell-time results. A state gets a curve only if at least
   one of its dwells both started and ended inside a trace, so a state molecules sit in until
   the trace ends has none.
3. The dwell-time axis reads seconds when `dt` is anything other than 1, and frames at
   `dt = 1` ([`dt`](../reference/parameters.md#param-dt)). With a frame time of exactly
   1 second the axis still says frames, but the numbers are the same, and the printed rates
   are per second either way. One `dt` applies to every molecule, so it
   is right only when they all come from movies recorded at that frame time. A project from
   one `tether extract` run holds one movie; if yours combines movies recorded at different
   frame times, run the script once per frame time, with that frame time as `dt` and those
   molecules as `molecule_keys` in `dwell_settings`. Give each run's dwell files names of
   their own, for example `f"dwell-100ms-state-{state}"` in place of `f"dwell-state-{state}"`,
   because a run that reuses a name overwrites the files the earlier run wrote.
4. The script prints one rate for each state whose fit succeeded, from the
   single-exponential fit. A state whose fit fails, for example because it has too few
   dwells, still gets its plot, but no rate. For a two-exponential fit, set `model="double"` in `dwell_settings`; it
   then prints both rates, slower first
   ([dwell-fit `model`](../reference/parameters.md#param-dwell-model)).

The histogram here is of **apparent** efficiency, from the background-subtracted traces. To
report a γ-corrected efficiency instead, see [Where the corrected efficiency
appears](apply-corrections.md#where-the-corrected-efficiency-appears).

## Next

[Export the results for a figure or another tool](export-results.md).

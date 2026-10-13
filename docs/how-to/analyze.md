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
Tether's Python:

```python
from tether.analysis.dwell import population_dwell_times
from tether.analysis.histogram import population_apparent_e_histogram
from tether.analysis.plot_export import (
    export_figure,
    render_dwell_survival,
    render_histogram1d,
    render_transition_density,
)
from tether.analysis.tdp import population_transition_density

project = "movie.tether"
model = "vbconhmm"
dt = 0.1  # seconds per frame: replace with your movie's frame time

hist = population_apparent_e_histogram(project)
export_figure(
    render_histogram1d(hist),
    "histogram",
    tether_export="plot:a1",
    source=project,
    parameters={},
)
print(f"histogram: {hist.n_molecules} molecules, {hist.n_samples} frames")

tdp = population_transition_density(project, model)
export_figure(
    render_transition_density(tdp),
    "tdp",
    tether_export="plot:b1",
    source=project,
    parameters={"model": model},
)
print(f"TDP: {tdp.n_transitions} transitions from {tdp.n_molecules} molecules")

dwells = population_dwell_times(project, model, dt=dt)
for state, analysis in dwells.items():
    export_figure(
        render_dwell_survival(analysis),
        f"dwell-state-{state}",
        tether_export="plot:b2",
        source=project,
        parameters={"model": model, "state": state, "dt": dt},
    )
    if analysis.fit is not None and analysis.fit.success:
        rate = analysis.fit.rates[0]
        print(f"state {state} (E = {analysis.level:.2f}): k = {rate:.3g} per second")
```

Each plot is written three times, as `histogram.pdf`, `histogram.svg` and `histogram.png`,
with a `histogram.provenance.json` beside them recording what made it; likewise `tdp.*` and
one `dwell-state-N.*` set per state.
[Plot export](../reference/exports.md#4-plot-export-pdf-svg-png) describes the files.

## 3. Check the result

1. The molecule counts printed for the TDP and the dwell times can be **smaller** than the
   histogram's. The TDP counts only molecules that change state at least once. Both leave out
   stale molecules, whose inputs changed after they were fitted, such as a new α or γ; [fit them
   again](idealize.md#after-you-change-the-project) if you corrected the project after
   idealizing.
2. A state can be missing from the dwell-time results. A state gets a curve only if at least
   one of its dwells both started and ended inside a trace, so a state molecules sit in until
   the trace ends has none.
3. The dwell-time axis reads seconds only if you set `dt`
   ([`dt`](../reference/parameters.md#param-dt)). The rate printed is the single-exponential
   fit; for a two-exponential fit pass `model="double"` to `population_dwell_times`
   ([dwell-fit `model`](../reference/parameters.md#param-dwell-model)).

The histogram here is of **apparent** efficiency, from the background-subtracted traces. To
report a γ-corrected efficiency instead, see [Where the corrected efficiency
appears](apply-corrections.md#where-the-corrected-efficiency-appears).

## Next

[Export the results for a figure or another tool](export-results.md).

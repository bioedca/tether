# Correct a dataset for leakage (α) and gamma (γ)

## Who this page is for

You have a `.tether` project of extracted traces, curated or not, and you want Tether to
estimate the leakage factor α and the detection-correction factor γ from those traces and
record them on every molecule. A project made by `tether extract` has no factors yet, and
this page adds them.

If you made the project with `tether batch`, this has already run with the default
settings. Use this page to check the result, or to run it again with settings of your own.
Running photobleach detection a second time updates the bleach frames but keeps each
molecule's analysis window from the first pass, because Tether sets a window only while it
still spans the whole trace; new α and γ settings take full effect.

## Before you start

- The project file, here called `movie.tether`, closed in the desktop application.
- The Python that came with Tether: `~/tether/envs/tether/bin/python` on Linux,
  `~/Tether/envs/tether/bin/python` on macOS, or
  `$env:USERPROFILE\Tether\envs\tether\python.exe` on Windows, at the default install
  locations. There is no menu item or `tether` command for this step.
- Molecules whose **acceptor bleaches before their donor**, with a good stretch of signal on
  each side of that moment. α is measured on the frames after the acceptor bleaches and before
  the donor does, and γ on the step where the acceptor bleaches. With the default settings each
  factor needs at least 10 such molecules. For α the stretch between the two bleaches must be
  at least 20 frames long; for γ the stretch before the acceptor bleaches and the stretch after
  it must each be longer than 20 frames.
- A project of one sample on one optical setup. The steps pool every molecule into one
  dataset α, and molecules without a γ of their own take one dataset γ, so a project that
  mixes samples or setups gets one value for all of them. `tether extract` and
  `tether batch` make one project per movie; keep movies of different samples or setups
  in separate projects.
- The donor half was right at extraction. Swapped channels make both factors meaningless; see
  step 5 of [Extract traces from a dual-view movie](extract-traces.md#5-check-that-it-worked).
- Run this **before** you idealize. Changing α or γ after a fit makes that fit stale, and stale
  molecules are left out of the transition and dwell-time analyses until you fit again.

## 1. Run the four steps in order

Save this as `correct.py` in the folder that holds the project and run it with Tether's
Python:

```python
from tether.project.correct import compute_corrected_fret
from tether.project.gamma import compute_gamma
from tether.project.leakage import compute_leakage_alpha
from tether.project.photobleach import compute_photobleach

project = "movie.tether"

bleach = compute_photobleach(project)
print(f"{bleach.n_acceptor_bleached} of {bleach.n_molecules} acceptors bleach")

alpha = compute_leakage_alpha(project)
print(f"alpha = {alpha.alpha} from {alpha.n_qualifying} molecules")

if alpha.applied:
    gamma = compute_gamma(project)
    print(f"gamma = {gamma.gamma} from {gamma.n_qualifying} molecules")

result = compute_corrected_fret(project)
print(f"{result.n_corrected} corrected, {result.n_apparent} left at apparent E")
```

The order matters. Photobleach detection finds the frames the other steps read, and γ is
measured on leakage-corrected traces, so it runs only when α was estimated. The last step
decides, molecule by molecule, whether both factors are there and records the outcome.

## 2. Check that the correction can be trusted

Read the printed lines:

1. `alpha = None` means α was **withheld**: fewer than 10 molecules gave a usable estimate. γ
   is then skipped, and every molecule stays at apparent E. Tether does this on purpose rather
   than guess a factor from too few traces.
2. `gamma = None` means γ was withheld for the same reason.
3. The molecule counts after `from` are the molecules each factor rests on. A factor built on
   the bare minimum of 10 is a weaker basis than one built on hundreds.
4. `0 corrected` means no molecule carries a correction.

Then look at the molecules themselves. Export the molecule table, as in
[Export results for a figure or another tool](export-results.md), and read its `alpha`,
`gamma` and `correction_method` columns
([molecule-table columns](../reference/exports.md#molecule-table-columns)).
`correction_method` reads `corrected` where both factors were applied and
`apparent-E (corrections unavailable)` where one was missing. Each molecule shares the one
dataset α. A molecule whose own bleach step qualified keeps its own γ, and every other one
takes the dataset median.

## 3. If a factor was withheld, or you measured it elsewhere

If you have α and γ from your own calibration of the same setup, give Tether those values
instead
([`alpha_override` / `gamma_override`](../reference/parameters.md#param-alpha-gamma-override)).
A molecule is corrected only when it has both factors, and a withheld α means γ was never
estimated, so in that case supply both. Replace the two numbers with your own measurements:

```python
from tether.project.correct import compute_corrected_fret

result = compute_corrected_fret("movie.tether", alpha_override=0.1, gamma_override=1.2)
print(f"{result.n_manual} molecules corrected with the values you supplied")
```

Molecules corrected this way are marked `manual` in `correction_method`, so a reader of the
project can tell a supplied factor from an estimated one.

## Where the corrected efficiency appears

Tether stores α and γ on each molecule; it does not rewrite your traces. The histograms, the
idealization and the `mean_apparent_e` column of the molecule table all stay on apparent E.
To report a corrected efficiency, compute it from the exported factors, as
[Reading the exports elsewhere](../reference/exports.md#reading-the-exports-elsewhere) shows.

The settings the α, γ and correction steps used are recorded in the project; photobleach
detection records none of its own. If you run the steps again and α is withheld this time,
γ is skipped, and the γ settings from the earlier run stay in the project although no
molecule carries that γ any more. The molecules' own `alpha` and `gamma` values are the
current result. [Analysis
parameters](../reference/parameters.md#correction-factors) lists them, with what each one
changes.

## Next

[Idealize the traces](idealize.md).

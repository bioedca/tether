---
description: "Estimate the leakage factor alpha and the gamma factor from your traces and record them on every molecule of a project."
---

# Correct a dataset for leakage (α) and gamma (γ)

## Who this page is for

You have a `.tether` project of extracted traces, curated or not. You want Tether to estimate
two factors from those traces: the leakage factor α and the detection-correction factor γ. You
want Tether to record them on every molecule. A project made by `tether extract` has no factors
yet, and this page adds them.

If you made the project with `tether batch`, this has already run with the default
settings. Use this page to check the result, or to run it again with settings of your own.

Running photobleach detection a second time updates the bleach frames. It sets the analysis
window only of a molecule whose analysis window still spans the whole trace. An example is a
molecule with no bleach found the first time. So an analysis window that the first pass
shortened stays as it was. New α and γ settings take full effect.

## Before you start

- The project file, here called `movie.tether`, closed in the desktop application.
- The Python that came with Tether. At the default install locations, it is
  `~/tether/envs/tether/bin/python` on Linux and `~/Tether/envs/tether/bin/python` on macOS.
  On Windows, it is `$env:USERPROFILE\Tether\envs\tether\python.exe`. There is no menu
  item or `tether` command for the first three steps. The last step is also in the desktop
  application ([step 4](#4-run-the-last-step-in-the-desktop-application)).
- Molecules whose **acceptor bleaches before their donor**. Each needs a good stretch of
  signal on each side of that moment. Tether measures α on the frames after the acceptor
  bleaches and before the donor does. It measures γ on the step where the acceptor bleaches.
  With the default settings, each factor needs at least 10 such molecules. For α, the stretch
  between the two bleaches must be at least 20 frames long. For γ, the stretch before the
  acceptor bleaches must be longer than 20 frames. So must the stretch after it.
- A project whose molecules all have one [condition](../glossary.md#condition). One sample
  on one optical setup can still give two conditions, for example at two laser powers. The
  steps pool every molecule into one dataset α. Molecules without a γ of their own take one
  dataset γ. The steps cannot select a condition. So a project that holds more than one
  condition gets one value for all of them. Tether gives every molecule the provisional
  condition parsed from the filename of its movie. So a project of two movies can hold two
  conditions. Tether does not check the sample. Before you follow these steps, make sure that
  each movie holds molecules of one condition only. One construct is an example.
- No rejected molecules you distrust. The α and γ steps read every molecule in the project,
  whatever its curation label. So a trace you rejected still enters both estimates when its
  bleach steps pass the checks above. If you rejected traces as contaminants or artifacts,
  [leave them out first](#leave-out-the-molecules-you-rejected).
- The donor half was right at extraction. Swapped channels make both factors meaningless. See
  step 5 of [Extract traces from a dual-view movie](extract-traces.md#5-check-that-it-worked).
- Run this **before** you idealize. Changing α or γ after a fit makes that fit stale. The
  transition and dwell-time analyses leave stale molecules out until you fit again.

### Leave out the molecules you rejected

Make a project of every molecule you did not reject, and correct that one instead:

```python
from tether.project.export import export_subset_tether

export_subset_tether("movie.tether", "accepted.tether")
```

The new project carries the background-subtracted traces that these steps read
([Export a subset project](export-results.md#export-a-subset-project)). Use
`accepted.tether` in place of `movie.tether` on the rest of this page. Also use it in the
steps after this page. A project imported for analysis only has no image crops and cannot be exported
this way.

## 1. Run the four steps in order

1. Save this script as `correct.py` in the folder that holds the project.
2. Run it with Tether's Python.

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
    print(f"{gamma.n_step_clean} of {gamma.n_step_evaluated} bleach steps clean")
    if gamma.few_clean_steps:
        print("warning: gamma rests on few clean bleach steps")

result = compute_corrected_fret(project)
print(f"{result.n_corrected} corrected, {result.n_apparent} left at apparent E")
```

The order matters. Photobleach detection finds the frames that the other steps read. Tether
measures γ on leakage-corrected traces, so γ runs only when α was estimated. The last step
decides, molecule by molecule, whether both factors are there. It records the outcome.

## 2. Check that the correction can be trusted

Read the printed lines:

1. `alpha = None` means that α was **withheld**. Fewer than 10 molecules gave a usable
   estimate. Then γ is skipped, and every molecule stays at apparent E. Tether does this on
   purpose. It does not guess a factor from too few traces.
2. `gamma = None` means γ was withheld for the same reason.
3. The molecule counts after `from` are the molecules each factor rests on. A factor built on
   the bare minimum of 10 is a weaker basis than one built on hundreds.
4. `warning: gamma rests on few clean bleach steps` is a reason to check γ. On a clean step the
   donor rises as the acceptor drops. Here fewer than 65 % of the measured steps did. Noise
   alone can make many steps read clean by chance, so this γ may rest largely on noise. It may
   also be sound, because a step whose donor did not rise never enters γ. The γ is still
   stored. Check it, report it with that caveat, or use a γ measured another way (step 3). `tether batch`
   writes the same warning in its log. The project records it as `few_clean_steps` in
   `/settings/gamma`
   ([`min_clean_step_fraction`](../reference/parameters.md#correction-factors),
   [ADR-0134](../adr/0134-a-gamma-from-few-clean-acceptor-bleach-steps-is-reported-with-a-warning.md)).
5. `0 corrected` means no molecule carries a correction.

Then look at the molecules themselves:

1. Export the molecule table, as in
   [Export results for a figure or another tool](export-results.md).
2. Read its `alpha`, `gamma` and `correction_method` columns
   ([molecule-table columns](../reference/exports.md#molecule-table-columns)).

`correction_method` reads `corrected` where both factors were applied. It reads
`apparent-E (corrections unavailable)` where one was missing. Each molecule shares the one
dataset α. When the dataset γ was computed, a molecule whose own bleach step qualified keeps
its own γ. Every other molecule takes the dataset median. When γ was withheld, no molecule
carries one.

## 3. If a factor was withheld, or you measured it elsewhere

You may have α and γ from your own calibration of the same setup. Then give Tether those
values instead
([`alpha_override` / `gamma_override`](../reference/parameters.md#param-alpha-gamma-override)).
A molecule is corrected only when it has both factors. A withheld α means that γ was never
estimated. So in that case, supply both. Replace the two numbers with your own measurements:

```python
from tether.project.correct import compute_corrected_fret

result = compute_corrected_fret("movie.tether", alpha_override=0.1, gamma_override=1.2)
print(f"{result.n_manual} molecules corrected with the values you supplied")
```

Molecules corrected this way are marked `manual` in `correction_method`. So a reader of the
project can tell a supplied factor from an estimated one.

## 4. Run the last step in the desktop application

The last step of step 1 records the correction method of each molecule from the α and γ
that the molecules already carry. You can run it again in the desktop application, for
example after you change those factors:

1. Open the project in the desktop application.
2. Go to the **Curate and correct** section.
3. Choose **Corrections → Run correction pass…**.
4. To keep every molecule at apparent E on purpose, select **Apparent E only**.
5. Select **OK**.

The step runs in the background. While it runs, Tether does not let you idealize or edit
the project. When it ends, a window shows each count of the result and the
correction state of the project. This menu item does not estimate α or γ
and does not take a value of your own. For those, use steps 1 and 3.

Two results look alike and are different:

- **Corrections unavailable** shows when no molecule has both factors. Every molecule stays
  at apparent E, and no factor is applied. Step 2 tells you what to check.
- **Apparent E by choice** shows when you selected **Apparent E only**. This is not a
  failure.

The menu item is available only in some correction states. Before the first α or γ is
stored, the state is APPARENT_REVIEW and the item is off. It is also off after an approval
(CALIBRATION_APPROVED, CORRECTED_REVIEW and ANALYSIS_READY). Then a new pass would change the
corrected traces behind the approval or the review. Hold the pointer over the item to see
the reason.

## Where the corrected efficiency appears

Tether stores α and γ on each molecule. It does not rewrite your traces. The histograms, the
idealization and the `mean_apparent_e` column of the molecule table all stay on apparent E.
To report a corrected efficiency, compute it from the exported factors, as
[Reading the exports elsewhere](../reference/exports.md#reading-the-exports-elsewhere) shows.

The project records the settings that the α, γ and correction steps used. Photobleach
detection records none of its own. You may run the steps again, and α may be withheld this
time. Then γ is skipped, and the α step removes the γ settings of the earlier run. The reason is
that no molecule carries that γ any more.
[Analysis parameters](../reference/parameters.md#correction-factors) lists the settings, with
what each one changes.

## Next

[Idealize the traces](idealize.md).

# Idealize traces into FRET states

## Who this page is for

You have a `.tether` project of extracted traces, ideally curated and corrected already, and
you want Tether to fit a hidden Markov model to them: a small set of FRET states and, for each
molecule, the state it is in at every frame. That fit is what the transition-density and
dwell-time analyses read.

## Before you start

- The project file, here called `movie.tether`.
- The tMAVEN sidecar, the separate environment every fit runs in. An installer sets it up
  for you and nothing more is needed. From a source checkout, build it with
  [Guided sidecar setup](../idealize/standalone-tmaven-handoff.md#guided-sidecar-setup) and set
  `TETHER_SIDECAR_PYTHON` to the interpreter it prints. If a fit says it cannot find the
  sidecar, see [Idealization cannot find the sidecar
  interpreter](../troubleshooting.md#idealization-cannot-find-the-sidecar-interpreter).
- For a whole-project fit, the Python that came with Tether:
  `~/tether/envs/tether/bin/python` on Linux, `~/Tether/envs/tether/bin/python` on macOS, or
  `$env:USERPROFILE\Tether\envs\tether\python.exe` on Windows, at the default install
  locations. Close the project in the desktop application before running a script on it.
- Your curation done. Rejected molecules are left out of a fit, so
  [curate the traces](curate-traces.md) first.
- Your corrections done. Changing α or γ after a fit makes it stale, so
  [correct the dataset](apply-corrections.md) first.

## Choose a method

- **To look at one trace:** press **I** in the desktop application. This is the one-click
  vbFRET fit. It fits only the selected molecule and is for checking a trace by eye, not for
  analysis.
- **For a population analysis:** fit the whole project with the **consensus VB-HMM**, one
  shared model across all molecules. This is Tether's default and what `tether batch` runs.
  Use it unless you have a reason not to.
- **For per-molecule state selection under a shared prior:** fit the whole project with
  **ebFRET** instead. It learns a shared prior across the molecules while choosing states
  molecule by molecule, and it varies more from run to run than the consensus model.
- **To fit by hand in tMAVEN's own window:** use **Hand-off → Hand to tMAVEN…**, fit and edit
  there, then bring the result back with **Hand-off → Import return leg…**.
  [Standalone-tMAVEN hand-off](../idealize/standalone-tmaven-handoff.md) covers it.

## Look at one trace

1. Open the project with `tether-gui movie.tether` and select a molecule that is not
   rejected.
2. Press **I**. The status bar reads `Idealizing` followed by the molecule's ID.
3. Wait. The first fit after Tether starts is much slower than later ones, and the window stays
   usable meanwhile.
4. When the status bar reads `Idealized` and the ID, a black stepped line is drawn over the
   blue efficiency trace.

Each press stores its fit as the project's `vbfret` model and replaces whatever that model held
before, so it never builds up a population fit.

## Fit the whole project

Save this as `idealize.py` beside the project and run it with Tether's Python:

```python
from tether.project.idealize import idealize_molecules

stored = idealize_molecules("movie.tether")
print(f"{stored.model_name}: {stored.nstates} states over {len(stored.molecule_keys)} molecules")
print("state levels:", stored.means)
```

It fits every molecule that is not rejected and stores the result as the `vbconhmm` model.
Tether tries one to four states and keeps the count the data support best. To fix the count
yourself, pass `nstates=`
([`nstates`](../reference/parameters.md#param-idealize-nstates)):

```python
stored = idealize_molecules("movie.tether", nstates=2)
```

For ebFRET, pass `model_type="ebhmm"`
([`model_type`](../reference/parameters.md#param-model-type)). It is stored as the `ebhmm`
model, beside any consensus fit:

```python
stored = idealize_molecules("movie.tether", model_type="ebhmm")
```

A fit refuses to replace a model of the same name that is already in the project. To fit
again, add `overwrite=True`.

A large project can take a long time, and the first fit of a session is slow. If a fit is
stopped for running too long, see [Idealization times out, or is restarted
repeatedly](../troubleshooting.md#idealization-times-out-or-is-restarted-repeatedly)
([`timeout`](../reference/parameters.md#param-sidecar-timeout)).

## Check that it worked

1. The script prints the model name, its state count and how many molecules it covers. The
   molecule count should match the molecules you did not reject.
2. The state levels are the FRET efficiencies of the states. They should sit where the plateaus
   in your traces sit.
3. With `tether batch`, read the end-of-run summary: an `idealize` stage reported `deferred`
   means it never ran, because no sidecar was found. See [The batch finished "ok" but nothing
   was idealized](../troubleshooting.md#the-batch-finished-ok-but-nothing-was-idealized).

## After you change the project

If you changed α or γ since the fit, for example by running the corrections again, the
affected molecules are now *stale*, and the transition and dwell-time analyses leave them out. Fit the same molecules
again with `reidealize`:

```python
from tether.project.idealize import reidealize

reidealize("movie.tether", "vbconhmm")
```

If you rejected or un-rejected molecules since the fit, `reidealize` is not enough, because it
keeps the molecules the model was first fitted on. Fit again with
`idealize_molecules("movie.tether", overwrite=True)` instead.

## Next

[Draw a FRET histogram, a TDP and dwell-time survival plots](analyze.md).

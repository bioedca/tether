---
description: "Fit a hidden Markov model to the traces of a project to get the FRET states and the state of each molecule at each frame."
---

# Idealize traces into FRET states

## Who this page is for

You have a `.tether` project of extracted traces, ideally curated and corrected already. You
want Tether to fit a hidden Markov model to them. The model is a small set of FRET states. For
each molecule, it also gives the state the molecule is in at every frame. The
transition-density and dwell-time analyses read that fit.

## Before you start

- The project file, here called `movie.tether`.
- The tMAVEN sidecar, the separate environment every fit runs in. An installer sets it up
  for you, and nothing more is needed. From a source checkout, build it with
  [Guided sidecar setup](../idealize/standalone-tmaven-handoff.md#guided-sidecar-setup). Then
  set `TETHER_SIDECAR_PYTHON` to the interpreter it prints. If a fit says that it cannot find
  the sidecar, see [Idealization cannot find the sidecar
  interpreter](../troubleshooting.md#idealization-cannot-find-the-sidecar-interpreter).
- For a fit from the command line instead of the application, a terminal where the `tether`
  command runs ([Command line](../cli.md#launching-tether-after-an-installer-install)).
- For a Python script instead, the Python that came with Tether. At the default install
  locations, it is `~/tether/envs/tether/bin/python` on Linux and
  `~/Tether/envs/tether/bin/python` on macOS. On Windows, it is
  `$env:USERPROFILE\Tether\envs\tether\python.exe`. Close the project in the desktop
  application before you run a script on it.
- Your curation done. The application fits the molecules you accepted. The command line fits
  every molecule you did not reject. So [curate the traces](curate-traces.md) first.
- Your corrections done. Changing α or γ after a fit makes it stale, so
  [correct the dataset](apply-corrections.md) first.

## Choose a method

- **For a population analysis:** fit the whole project with the **consensus VB-HMM**. That is
  one shared model across all molecules. This is Tether's default and what `tether batch` runs.
  The application offers it first.
  Use it unless you have a reason not to.
- **For per-molecule state selection under a shared prior:** fit the whole project with
  **ebFRET** instead. It learns a shared prior across the molecules while it chooses states
  molecule by molecule. It varies more from run to run than the consensus model.
- **To fit by hand in tMAVEN's own application window:** use **Hand-off → Hand to tMAVEN…**.
  Fit and edit there. Then bring the result back with **Hand-off → Import return leg…**.
  [Standalone-tMAVEN hand-off](../idealize/standalone-tmaven-handoff.md) covers it.

## Fit the whole project in the application

1. Open the project with `tether-gui movie.tether`.
2. In the **Curate and correct** section, press **I**, or choose **Idealize → Idealize
   project…**. The **Idealize project** dialog opens.
3. Choose the **Method**: **consensus VB-HMM (vbconhmm)** or **ebFRET (ebhmm)**.
4. Choose the **State count**: **Automatic** tries one to four states and keeps the count with
   the largest ELBO. **Fixed** uses the count you type.
5. Choose the **Molecules**: **Accepted molecules** by default. When the project holds several
   conditions, you can choose the accepted molecules of one condition.
6. Click **Idealize**. The status bar reads `Idealizing project` and the method.

The fit runs in the background. You can still browse the molecules while it runs, but every
change to the project waits for it. The right end of the status bar shows its progress, one
step for each state count it fits. **Cancel** there stops the fit before its model is written,
so the project is unchanged.

When the fit is done, the status bar gives the method, the state count and the molecule count.
For example:

```text
Idealized the project with consensus VB-HMM: 2 states, 2 molecules fitted (model vbconhmm)
```

The model is stored under the name of its method, `vbconhmm` or `ebhmm`. A fit replaces a
model of the same name, and the dialog says so before you start. A stepped line is drawn over
the blue efficiency trace of the selected molecule, if the fit included it. The line is black in
the light theme and white in the dark theme
([Application window themes and trace colors](../reference/gui-theme.md#trace-colors)).

If the dialog says that the sidecar is not available, **Idealize** stays off. See
[Idealization cannot find the sidecar interpreter](../troubleshooting.md#idealization-cannot-find-the-sidecar-interpreter).

The fit is the same as `tether idealize` with the same choices. A fit from the dialog stores
the same model as `idealize_molecules` with the same arguments.

## Fit the whole project from the command line

1. Close the project in the desktop application.
2. Run this command:

```console
tether idealize movie.tether
```

It fits every molecule that is not rejected and stores the result as the `vbconhmm` model.
Tether tries one to four states and keeps the count the data support best. The command
prints the method and the state count it kept. It also prints the ELBO of each state count
it tried and the number of molecules it fitted. To fix the count yourself, add `--nstates`
([`nstates`](../reference/parameters.md#param-idealize-nstates)):

```console
tether idealize movie.tether --nstates 2
```

For ebFRET, add `--method ebhmm`
([`model_type`](../reference/parameters.md#param-model-type)). It is stored as the `ebhmm`
model, beside any consensus fit:

```console
tether idealize movie.tether --method ebhmm
```

A fit refuses to replace a model of the same name that is already in the project. To fit
again, add `--overwrite`. Add `--seed` and a number to make the fit repeatable. The same
project, options and seed then give the same model.

[`tether idealize`](../cli.md#tether-idealize)
lists every option and the exit codes. Code `3` means the sidecar could not start. The
project is then not changed.

The command is the same fit as `idealize_molecules` in Python. To fit from a script, save
this as `idealize.py` beside the project. Run it with Tether's Python:

```python
from tether.project.idealize import idealize_molecules

stored = idealize_molecules("movie.tether")
print(f"{stored.model_name}: {stored.nstates} states over {len(stored.molecule_keys)} molecules")
print("state levels:", stored.means)
```

The options are arguments there: `nstates=2`, `model_type="ebhmm"`, `overwrite=True` and
`seed=1`.

A large project can take a long time, and the first fit of a session is slow. A fit can be
stopped for running too long. Then see
[Idealization times out, or is restarted repeatedly](../troubleshooting.md#idealization-times-out-or-is-restarted-repeatedly)
([`timeout`](../reference/parameters.md#param-sidecar-timeout)).

## Check that it worked

1. The status bar, the command or the script gives the model name and its state count. It also gives how
   many molecules the model covers. The molecule count should match the molecules you accepted
   (in the application) or did not reject (from the command line).
2. The state levels are the FRET efficiencies of the states. They should sit where the plateaus
   in your traces sit.
3. With `tether batch`, read the end-of-run summary. An `idealize` stage reported `deferred`
   means that it never ran, because no sidecar was found. See
   [The batch finished "ok" but nothing was idealized](../troubleshooting.md#the-batch-finished-ok-but-nothing-was-idealized).

## After you change the project

You may have changed α or γ since the fit, for example when you ran the corrections again.
Then the affected molecules are now *stale*. The transition and dwell-time analyses leave them
out. Fit the same molecules again.

In the application, open **Idealize project…** and choose the method of the model. Select
**Refit stale only** and click **Idealize**. The dialog says when no molecule of that model is
stale. From the command line:

```console
tether idealize movie.tether --stale-only
```

For each model, the command prints how many molecules were stale and how many current. It
refits each model that has a stale molecule, with the settings stored in the model. It
refits all the molecules of that model. A consensus or ebFRET model is one fit over all of
them. A model with no stale molecule is left as it is. From Python, the same refit of one
model is `reidealize`:

```python
from tether.project.idealize import reidealize

reidealize("movie.tether", "vbconhmm")
```

This works for a model fitted in Tether. A model you imported through the
[standalone-tMAVEN hand-off](../idealize/standalone-tmaven-handoff.md) cannot be refitted this
way. For such a model, hand the molecules to tMAVEN again and import the new fit.

You may have rejected or un-rejected molecules since the fit. Then `--stale-only` and
`reidealize` are not enough, because they keep the molecules the model was first fitted on.
Run the fit you made in [Fit the whole project in the application](#fit-the-whole-project-in-the-application) or [from the command line](#fit-the-whole-project-from-the-command-line) again. Use the same
settings, and add `--overwrite` (or `overwrite=True`).

A call without the same settings falls
back to the defaults. So it fits a different model. Or it replaces the `vbconhmm` model and
leaves the one you fitted untouched:

```python
stored = idealize_molecules("movie.tether", overwrite=True)  # the default consensus fit
stored = idealize_molecules("movie.tether", nstates=2, overwrite=True)  # if you fixed the count
stored = idealize_molecules("movie.tether", model_type="ebhmm", overwrite=True)  # an ebFRET fit
```

## Next

[Draw a FRET histogram, a TDP and dwell-time survival plots](analyze.md).

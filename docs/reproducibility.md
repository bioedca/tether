# Reproducibility and reporting

**Who this page is for.** Anyone who has to write the Methods paragraph for an analysis done
in Tether, or answer a reviewer who asks which version and which settings produced a figure.
It lists what a `.tether` project and its exports record about how they were made, shows how
to read that record back, and explains how to rebuild the software environment that produced
it.

Tether's design rule is that every datum carries its origin
([ADR-0001](adr/0001-provenance-first-data-model.md)). In practice that means three things,
covered in turn below: a version stamp and the stage's settings on extraction, the leakage,
γ and correction passes and each idealization inside the project, the same stamp in a sidecar file
beside every publication export, and committed dependency locks that pin the environment the
version ran in. The record is not complete: some steps and some settings are never stamped,
and [What is not recorded](#what-is-not-recorded) lists them. Two pages go further: the
[project store reference](reference/tether-format.md) documents every group and dataset in a
`.tether` file, and the [exports reference](reference/exports.md) documents every column of
every export. What to cite, and the caveats on which steps leave no stamp, are on
[Citing Tether](citing.md#reporting-a-tether-analysis).

## What is recorded

### The project root

Every project is created with these attributes on its root group
(`create_project`, `src/tether/io/schema.py`):

| Attribute | Meaning |
|---|---|
| `format` | Always `tether-project`; marks the file as a Tether store. |
| `schema_version` | The store layout version, an integer. |
| `app_version` | The Tether version that **created** the project. It is optional: a writer can leave it out, so a valid project may lack it. |

The root `app_version` says nothing about later steps. A project created with one release
can be corrected or idealized with another, and each of those steps stamps its own version
below.

### Each pipeline stage, under `/settings`

Each stage writes one group under `/settings` holding its parameters and its own
`app_version`. Every attribute named here is written by the module listed, so you can search
that file for the name and land on the line that writes it.

**`/settings/extraction`** — `src/tether/imaging/extract.py`. Written the **first** time a
movie is extracted into the project and never rewritten. A later movie must use the same
`window`, `disk_radius`, `ring_inner`, `ring_outer` and `bg_window` or the write is
refused; nothing else is compared, so `profile_json` and `app_version` describe the first
movie only, and a later movie's detection or registration settings are not recorded. In a
project rebuilt from a Deep-LASI acquisition it holds Tether's
standard aperture values, recording where the traces came from rather than an integration
Tether performed.

| Attribute | Meaning |
|---|---|
| `window` | Aperture and patch window edge, pixels. |
| `disk_radius` | Signal disk radius, pixels. |
| `ring_inner`, `ring_outer` | Background annulus radii, pixels. |
| `bg_window` | Background moving-average length, frames. |
| `n_psf` | Number of pixels in the signal disk. |
| `molecule_key_quantum_px` | The coordinate quantum behind each molecule's key, pixels. |
| `app_version` | The Tether version that extracted the first movie. In a project rebuilt from a Deep-LASI acquisition it is the version that did the rebuild, not the software that extracted the traces. |
| `profile_json` | Present only when a settings profile was supplied, as JSON. From the native pipeline behind `tether extract` and `tether batch` it holds the detection, registration and colocalization settings plus the registration source and its RMS error. From a Deep-LASI reconstruction it holds only where the traces came from: `source`, `coordinate_source`, `movie_name` and `exported_by`. |

**`/settings/leakage`** — `src/tether/project/leakage.py`. Rewritten on every leakage pass.
`alpha` is the donor-to-acceptor leakage factor α, estimated from the signal left in the
acceptor channel after the acceptor bleaches (`source` = `post-acceptor-bleach-tail`); `withheld` is `True`, and
`alpha` is `NaN`, when too few traces qualified. The other attributes record the inputs to
that decision: `n_qualifying`, `n_molecules`, `min_window_frames`, `ceiling`,
`min_qualifying_traces` and `intensity_quantity`, plus `app_version` and `created_utc`.

**`/settings/gamma`** — `src/tether/project/gamma.py`. Rewritten on every γ pass. The
detection-efficiency and quantum-yield ratio γ is estimated **per molecule** at its acceptor
bleach step (`source` = `acceptor-bleach-step`), and each molecule that passes the gates
stores its own value in the `gamma` column of `/molecules`. Storing is all the pass does;
nothing in Tether applies γ to a displayed or exported value (see the note under the sample
Methods paragraph). The `gamma`
attribute here is the population median of those values, which the `n_fallback` molecules
that failed the gates receive instead; it follows the same `withheld` rule. The rest are
`n_qualifying`, `n_fallback`, `n_molecules`, `half_window`, `min_window_frames`, `ceiling`,
`min_qualifying_traces`, `intensity_quantity`, `app_version` and `created_utc`.

**`/settings/correction`** — `src/tether/project/correct.py`. Rewritten on every correction
pass (`source` = `accurate-fret`). The pass decides, per molecule, whether its α and γ are
usable and records the outcome in `/molecules` as `correction_method` and
`correction_confidence`, with any manual factor written into `alpha` and `gamma`. It does
not compute a corrected E itself.

| Attribute | Meaning |
|---|---|
| `apparent_e_only` | `True` when the pass was told to keep every molecule on apparent E on purpose, even where factors exist. |
| `alpha_override`, `gamma_override` | A factor requested by hand, or `NaN` when none was. It is recorded whether or not it took effect (`apparent_e_only`, for one, overrides it), so read `n_manual` for how many molecules it was actually applied to. |
| `n_molecules` | Molecules in the pass. |
| `n_corrected`, `n_manual`, `n_apparent` | How many molecules this pass classified `corrected`, `manual` or apparent E. `corrected` means the molecule had a finite stored α, a positive stored γ and no override was requested in this pass; it does not prove the factors were estimated. A factor stored earlier by an override or by a Deep-LASI reconstruction counts as `corrected` on a later pass with no override, and the later pass replaces this group, so check how the factors were produced if that matters. |
| `total_failure` | `True` when no molecule carries a correction. It is also `True` under `apparent_e_only`, where nothing was corrected by choice, so check that attribute first. |
| `app_version`, `created_utc` | The version and time of the pass. |

Direct excitation (δ) has no attribute here. Tether's two-color, single-laser scheme carries
δ as an inert `0` ([ADR-0008](adr/0008-correction-factor-remap.md)), stored per molecule in
the `delta` column of `/molecules` rather than under `/settings`.

**`/settings/batch`** — `src/tether/project/batch.py`. Written by `tether batch`, rewritten on
every run: `source` (`batch-runner`), `policy`, `idealize_requested`, one `<stage>_status`
per stage (`extract_status`, `correct_status`, `idealize_status`), `app_version` and
`created_utc`.

**`/settings/analysis_only`** — `src/tether/project/analysis_import.py`. Present only in a
store imported from someone else's traces, where there is no movie to go back to:
`round_trip_available` (`False`), `reason`, `n_molecules`, `n_frames`, `banner`, `source`,
`source_kind`, `app_version` and `created_utc`. Such a store has no `/settings/extraction`
at all.

### Each idealization, under `/idealization`

Every fitted model is its own group, `/idealization/<model>`, stamped by
`write_idealization_model` in `src/tether/project/idealize.py`:

| Attribute | Meaning |
|---|---|
| `type` | The backend, for example `vbconhmm` (Tether's default) or `ebhmm`. A model imported from standalone tMAVEN keeps tMAVEN's own label. |
| `nstates` | Number of states in the chosen model. |
| `nstates_selected_by` | How `nstates` was chosen: `max-elbo` (the best evidence lower bound over the states tried), `fixed`, or `imported`. |
| `dtype`, `intensity_quantity` | What was fitted. For Tether's own fits `dtype` is `FRET` and `intensity_quantity` names the trace layer: `corrected` means the background-subtracted layer, not α- or γ-corrected, and the fit runs on apparent E computed from it ([ADR-0029](adr/0029-idealization-correction-provenance-hash-and-per-factor-staleness.md)), so the state means are apparent efficiencies. A model imported from standalone tMAVEN keeps whatever `dtype` tMAVEN gave it, unchecked, so read `dtype` to know what quantity its state means are in. |
| `n_molecules` | Molecules this fit covered. |
| `app_version`, `created_utc` | The Tether version and time of the fit. For a model imported from standalone tMAVEN both describe the import instead: the version that imported it and the time it was written (`apply_reconcile`, `src/tether/project/handoff.py`), not the software or time of the fit, so record those yourself if they matter. |
| `elbo`, `elbo_by_nstates` | The evidence lower bound of the chosen model, and of every state count tried (JSON), when the backend reports them. |

The backends and the papers behind each are listed on [Citing Tether](citing.md#idealization).

### Two more stamps

Two other records carry a version, and the read-back below does not print them. The
engineered feature table, the dataset `/features/table`, carries `app_version`,
`created_utc`, `intensity_quantity`, `feature_schema_version`, `feature_names` and
`n_molecules` as attributes (`src/tether/project/features.py`). Each row of the
`/settings/condition_audit` log of condition re-keys and merges carries its own
`app_version` and `timestamp` (`src/tether/project/conditions.py`).

### What is not recorded

Some steps leave no version or parameter stamp in the project: photobleach detection, and
ranking and classification runs. The SMD file handed off to standalone tMAVEN has no
sidecar, and a fit run there is stamped only with the Tether version that imported it. For
those, keep your own note of the version and settings; [Citing Tether](citing.md#reporting-a-tether-analysis)
lists each case.

Nor is every setting of the stamped stages stored. The idealization fitter's restart count
(`nrestarts`), for one, is passed to tMAVEN but never written to the model group. The
*Recorded in `.tether`* column of the [parameters reference](reference/parameters.md) says,
for each tunable, whether and where it is stamped; anything marked **no** that you changed
belongs in your own notes.

### Every export

Each publication export writes a sidecar beside its output with the same stamp
(`write_provenance_sidecar`, `src/tether/project/export.py`). The name is the full output
filename with `.provenance.json` appended, so the original extension survives:
`molecules.csv.provenance.json`, `traces.txt.provenance.json`,
`subset.tether.provenance.json`. A figure exported with `export_figure`
(`src/tether/analysis/plot_export.py`) is named by its stem, so its sidecar is
`<stem>.provenance.json`.

| Key | Meaning |
|---|---|
| `tether_export` | Which export wrote it, for example `molecule-table-csv`. |
| `app_version` | The Tether version that wrote the export. |
| `created_utc` | When. |
| `source_project` | The project it came from, as a file name. For a figure it is whatever the code that exported it passed in. |
| `parameters` | For the store exports, the export's own settings, such as the intensity quantity and molecule count. For a figure, whatever the code that exported it passed in, plus `formats`, `dpi` and `outputs`; nothing records a plotting choice such as the bin count unless that code passes it, so pass every setting the plot was drawn with. |

Figures are stamped twice more: the version is printed in the figure footer and embedded in
the PDF, SVG and PNG metadata. The printed footer is there only with `export_figure`'s
default `stamp=True`; a caller that passes `stamp=False` gets no footer, while the sidecar
and the embedded metadata are still written. A figure re-rendered and exported again at the same pinned
time gives byte-identical files in all three formats. Each export's parameters are listed in
the [exports reference](reference/exports.md#provenance-sidecar).

## How to read it back

You do not need Tether to read any of this. Any HDF5 viewer shows the attributes; with `h5py`,
this prints the root and every stage and model:

```python
import h5py


def show(name, attrs):
    print(f"[{name}]")
    for key in sorted(attrs):
        value = attrs[key]
        if isinstance(value, bytes):
            value = value.decode()
        print(f"  {key} = {value}")


with h5py.File("experiment.tether", "r") as f:
    show("/", f.attrs)
    for name, group in f.get("settings", {}).items():
        if isinstance(group, h5py.Group):
            show(f"/settings/{name}", group.attrs)
    for name, group in f.get("idealization", {}).items():
        if not name.endswith(".__writing__"):
            show(f"/idealization/{name}", group.attrs)
```

The `isinstance` test skips `/settings/condition_audit`, which is a dataset (a log of
condition edits) rather than a group. The `.__writing__` test skips the staging group a fit
leaves behind if it is interrupted while being saved; that group is an incomplete model, and
`list_idealizations` in `src/tether/project/idealize.py` skips it the same way.

Here is that script's output from one run, shown as an illustration rather than a reference
value: the project was made by `tether batch` from a synthetic 25-molecule, 400-frame movie
generated by a one-off script that is not part of the repository, so the numbers cannot be
regenerated from anything committed. The run used the default extraction settings and the
default idealization, on a development build. Numbers will differ for your data, and so can the
set of groups and keys, because some are written only when a step ran or produced a result:

- `/settings/gamma` is absent when leakage withheld α (too few qualifying molecules), since
  `tether batch` then skips γ; the correction pass still runs and records the apparent-E
  fallback.
- `/idealization/<model>` is absent when no fit completed, and `elbo` and `elbo_by_nstates` appear
  only when the backend reports them.
- `profile_json` appears only when a settings profile was supplied.
- `/settings/batch` appears only for a `tether batch` run, and `/settings/analysis_only` only
  for an imported analysis-only store.

In an intact project, a missing group means the step left no completed record: it did not
run, withheld its result, or ran and failed. For a `tether batch` run, each `<stage>_status`
in `/settings/batch` records how that stage ended (`done`, `skipped`, `failed`, `blocked`,
`deferred`, `not-requested` or `warning`), and the batch log has the detail. The groups alone
cannot tell any of these apart from a record that was deleted or damaged. One loss does leave
a trace: a save interrupted between removing an old model and renaming its replacement can
leave only the `<model>.__writing__` staging group, which the script skips. If
`/idealization` holds one with no matching model, re-run that fit.

```text
[/]
  app_version = 1.0.0rc3.dev28+gdfadf1f74
  format = tether-project
  schema_version = 1
[/settings/extraction]
  app_version = 1.0.0rc3.dev28+gdfadf1f74
  bg_window = 10
  disk_radius = 3.0
  molecule_key_quantum_px = 0.1
  n_psf = 29
  profile_json = {"app_version": "1.0.0rc3.dev28+gdfadf1f74", "bg_window": 10, "coloc_distance": 3.0, "detection_block": 50, "detection_mode": "wavelet", "detection_threshold": null, "disk_radius": 3.0, "donor_side": "left", "min_separation": null, "n_control_points": 25, "pair_tol": 2.0, "pipeline": "native", "prealign": "translation", "prealign_high_sigma": 20.0, "prealign_low_sigma": 3.0, "prealign_upsample": 10, "registration_rms_px": 0.1877906926396776, "registration_source": "native", "ring_inner": 6.0, "ring_outer": 8.0, "rms_gate": 0.5, "tdat_source": null, "tmap_source": null, "window": 21}
  ring_inner = 6.0
  ring_outer = 8.0
  window = 21
[/settings/leakage]
  alpha = 0.0790027613469851
  app_version = 1.0.0rc3.dev28+gdfadf1f74
  ceiling = 0.3
  created_utc = 2026-10-12T23:57:52.230988+00:00
  intensity_quantity = corrected
  min_qualifying_traces = 10
  min_window_frames = 20
  n_molecules = 25
  n_qualifying = 25
  source = post-acceptor-bleach-tail
  withheld = False
[/settings/gamma]
  app_version = 1.0.0rc3.dev28+gdfadf1f74
  ceiling = 5.0
  created_utc = 2026-10-12T23:57:52.237885+00:00
  gamma = 0.9911339755625846
  half_window = 3
  intensity_quantity = corrected
  min_qualifying_traces = 10
  min_window_frames = 20
  n_fallback = 0
  n_molecules = 25
  n_qualifying = 25
  source = acceptor-bleach-step
  withheld = False
[/settings/correction]
  alpha_override = nan
  app_version = 1.0.0rc3.dev28+gdfadf1f74
  apparent_e_only = False
  created_utc = 2026-10-12T23:57:52.241450+00:00
  gamma_override = nan
  n_apparent = 0
  n_corrected = 25
  n_manual = 0
  n_molecules = 25
  source = accurate-fret
  total_failure = False
[/settings/batch]
  app_version = 1.0.0rc3.dev28+gdfadf1f74
  correct_status = done
  created_utc = 2026-10-12T23:58:19.823358+00:00
  extract_status = done
  idealize_requested = True
  idealize_status = done
  policy = warn
  source = batch-runner
[/idealization/vbconhmm]
  app_version = 1.0.0rc3.dev28+gdfadf1f74
  created_utc = 2026-10-12T23:58:19.815625+00:00
  dtype = FRET
  elbo = 12193.51114453067
  elbo_by_nstates = {"1": -919.471515411359, "2": 5629.248918599241, "3": 12193.51114453067, "4": 11680.489664497498}
  intensity_quantity = corrected
  n_molecules = 25
  nstates = 3
  nstates_selected_by = max-elbo
  type = vbconhmm
```

An export's sidecar is plain JSON. This prints the stamp and then each parameter:

```python
import json

with open("molecules.csv.provenance.json", encoding="utf-8") as fh:
    stamp = json.load(fh)

for key in ("tether_export", "app_version", "created_utc", "source_project"):
    print(f"{key}: {stamp[key]}")
for key, value in sorted(stamp["parameters"].items()):
    print(f"parameters.{key}: {value}")
```

Its actual output for a molecule-table CSV exported from the same project:

```text
tether_export: molecule-table-csv
app_version: 1.0.0rc3.dev28+gdfadf1f74
created_utc: 2026-10-12T23:58:41.116615+00:00
source_project: Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tether
parameters.include_rejected: True
parameters.intensity_quantity: corrected
parameters.n_molecules: 25
```

## What a version string means

Tether's version comes from git when it is built, so the string tells you which build you
have:

- **A release**, such as `1.0.0`, or a release candidate such as `1.0.0rc2`, is what an
  installer from the [releases page](https://github.com/bioedca/tether/releases) reports.
- **A development build** looks like `1.0.0rc3.dev28+gdfadf1f74`: 28 commits after the last
  release tag, at commit `dfadf1f74`, which is what the output above shows. That commit
  identifies the exact source only for a build made from a clean checkout; a dirty or edited
  one runs code the commit does not name ([Reproducing the environment](#reproducing-the-environment)).
- **`0.0.0`** means one of two things: a build that had no git history to read, for example
  one made from a downloaded source archive, or a build at the project's first milestone tag,
  `v0.0.0`. The string alone cannot tell them apart, so check it against your own record of
  what you installed.
- **`0.0.0+unknown`** means the version could not be determined at all, for example code run
  straight from a source tree that was never installed.

To see the version you have installed, run `tether --version` once Tether's launcher folder
is on your PATH ([Install Tether, step 4](install.md#4-put-tethers-launcher-folder-on-your-path)),
or run the launcher by its full path as that page shows. For results already on disk, read
the stamps above instead: they record the version that actually ran each step, which may not
be the one installed today.

## Reproducing the environment

A release, or a development build installed from a clean checkout, pins Tether's own code
through its version string. Three cases do not. `0.0.0+unknown` names no commit at all, and
`0.0.0` names one only when you know the build was made at the `v0.0.0` tag rather than
from a source tree with no git history; the string alone cannot tell you which, as the
section above explains. An editable install (`pip install -e`, used below) runs
whatever is in the checkout, so uncommitted changes, or edits made after installing, are not
in the version string, which names only the commit it was built from. In those cases keep
the commit plus a patch, a source archive or a checksum of the source you ran as a separate
record.

The libraries Tether ran on are pinned by three committed lock files, and CI checks on every change that each lock still matches the
specification it was solved from ([ADR-0004](adr/0004-pin-and-hold-dual-lock-isolation.md)):

| Lock | Pins | In the installer |
|---|---|---|
| `conda-lock.yml` | The application: Python, NumPy, Numba, h5py, PySide6, napari and the rest. | Yes, as `envs/tether`. |
| `sidecar/conda-lock.yml` | The isolated tMAVEN idealization environment (PyQt5, `numpy<2`). | Yes, as `envs/sidecar`. |
| `deep/conda-lock.yml` | The optional deep-classifier add-on (PyTorch), CPU build only. | **No.** It is a separate opt-in install; see [Deep trace classifier](ml/deep-classifier.md). |

The deep lock covers only the CPU build. The GPU path adds a CUDA PyTorch wheel from the
PyTorch index on top, with whatever pip resolves alongside it, and that install is deliberately unpinned
([ADR-0047](adr/0047-deep-model-optional-stack-and-dataset.md)). If you trained or scored with the GPU
build, record the PyTorch wheel version, its CUDA channel and your NVIDIA driver version
yourself (`pip freeze` in that environment lists the wheels); the lock does not.

Two parts of the sidecar sit outside its lock: tMAVEN itself, installed from a pinned git
commit by `scripts/setup_sidecar.py`, and a setuptools compatibility wheel pinned by version
and hash in `packaging/setuptools-compatibility.txt`, which replaces the lock's own
setuptools. From `v1.0.0-rc2` on, the installer bundles both, and a release's record of its
environment is those three locks plus `scripts/setup_sidecar.py` and
`packaging/setuptools-compatibility.txt` at the release tag. `v1.0.0-rc1` predates the
compatibility pin: the file is not at that tag, its sidecar has the lock's own setuptools,
and its one-click idealization fails with a `pkg_resources` error
([Install Tether](install.md) says so). Each release
also publishes a software bill of materials, `tether-sbom.cyclonedx.json`, beside the locks.

**With an installer**, the environment comes with the version. Each installer is built from
per-platform locks rendered from the first two files, with no fresh solve, so two installs of
the same release get the same packages. Each release publishes the source locks as assets
on its [release page](https://github.com/bioedca/tether/releases), so you can see exactly
what a release pinned without installing it. `v1.0.0-rc1` predates the deep lock's addition
to the release assets and carries only the base and sidecar locks
([its asset record](release.md#what-a-published-release-contains-verified-v100-rc1)); for that release, read `deep/conda-lock.yml` from the
`v1.0.0-rc1` tag in the source tree.

**From source**, check out the release tag you are reproducing, create the environment from
the committed lock, and install Tether on top of it without letting pip install any
runtime dependency:

```console
micromamba create -n tether -f conda-lock.yml   # or: conda-lock install -n tether
micromamba activate tether
pip install -e . --no-deps
```

Never create the environment from `environment.yml`: that is the specification the lock was
solved from, and solving it again today gives newer packages than the release used. One
step is not pinned by the lock: `pip install` still downloads Tether's build backend
(`hatchling` and `hatch-vcs`) into a temporary build environment, so it needs network
access, and the backend version can differ from the one the release was built with. The
sidecar has its own guided setup, `python scripts/setup_sidecar.py` from the same tag,
which installs the pinned tMAVEN and setuptools; see
[`CONTRIBUTING.md`](https://github.com/bioedca/tether/blob/main/CONTRIBUTING.md).

## A sample Methods paragraph

This paragraph is built from the read-back above: the version, the extraction settings, the
correction stamps and the idealization stamp. The spread of the per-molecule γ values comes
from the `gamma` column of the molecule-table CSV exported from the same project. Swap in
your own values and drop what you did
not run. The citations are the ones [Citing Tether](citing.md) gives for each step.

> Single-molecule FRET movies were processed with Tether version 1.0.0rc3.dev28+gdfadf1f74
> (https://doi.org/10.5281/zenodo.23306397). Donor and acceptor intensities were integrated
> over a 3.0-pixel-radius disk inside a 21-pixel window, with the local background taken from
> an annulus between 6.0 and 8.0 pixels and averaged over 10 frames (Wanninger et al., 2023).
> Correction factors were estimated as described by McCann et al. (2010) and Hellenkamp et
> al. (2018): donor-to-acceptor leakage α = 0.079, from 25 of 25 molecules after acceptor
> photobleaching, and the detection-efficiency and quantum-yield ratio γ, estimated per
> molecule at the acceptor photobleaching step (median 0.99, interquartile range 0.89 to
> 1.13; all 25 molecules had their own estimate, so none took the median). Direct acceptor
> excitation was taken as zero under single-laser excitation. FRET trajectories were
> idealized on the apparent FRET efficiency of the background-subtracted intensities,
> without the α and γ corrections, using the consensus variational-Bayes hidden Markov model
> (Bronson et al., 2009) run through tMAVEN (Verma et al., 2024), with the number of states
> (three) chosen by the maximum evidence lower bound over one to four states; the state
> means are therefore apparent efficiencies.

The idealization clause matters most. Tether stores α and γ with each molecule but does not
apply them to any value it shows or exports: the trace views, the analysis functions, the
molecule-table CSV's E summary and the hidden Markov model all use apparent E, so none of
them is a corrected efficiency. If you report corrected E, compute it yourself from the
stored factors (the [exports reference](reference/exports.md#at-a-glance) gives the formula)
and say so.

Report the version that actually produced the results, which here is a development build;
never substitute a release that did not run. For a paper, rerun the final analysis on a
release if you can, and then cite that release. If one exact release matters, cite that
version's own DOI from its Zenodo record instead of the concept DOI. If steps ran under
different versions, name each one.

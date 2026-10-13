# Reproducibility and reporting

**Who this page is for.** Anyone who has to write the Methods paragraph for an analysis done
in Tether, or answer a reviewer who asks which version and which settings produced a figure.
It lists what a `.tether` project and its exports record about how they were made, shows how
to read that record back, and explains how to rebuild the software environment that produced
it.

Tether's design rule is that every datum carries its origin
([ADR-0001](adr/0001-provenance-first-data-model.md)). In practice that means three things,
covered in turn below: a version stamp and a parameter set on each pipeline stage inside the
project, the same stamp in a sidecar file beside every publication export, and committed
dependency locks that pin the environment the version ran in. Two pages go further: the
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
movie is extracted into the project and never rewritten; later movies must match it or the
write is refused. In a project rebuilt from a Deep-LASI acquisition it holds Tether's
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
| `app_version` | The Tether version that extracted the first movie. |
| `profile_json` | Present only when a settings profile was supplied; the native pipeline behind `tether extract` and `tether batch` always supplies one. It holds the detection, registration and colocalization settings as JSON, plus the registration source and its RMS error. |

**`/settings/leakage`** — `src/tether/project/leakage.py`. Rewritten on every leakage pass.
`alpha` is the donor-to-acceptor leakage factor α, estimated from the signal left in the
acceptor channel after the acceptor bleaches (`source` = `post-acceptor-bleach-tail`); `withheld` is `True`, and
`alpha` is `NaN`, when too few traces qualified. The other attributes record the inputs to
that decision: `n_qualifying`, `n_molecules`, `min_window_frames`, `ceiling`,
`min_qualifying_traces` and `intensity_quantity`, plus `app_version` and `created_utc`.

**`/settings/gamma`** — `src/tether/project/gamma.py`. Rewritten on every γ pass. `gamma` is
the detection-efficiency and quantum-yield ratio γ, estimated at the acceptor bleach step
(`source` = `acceptor-bleach-step`), with the same `withheld` rule. The rest are
`n_qualifying`, `n_fallback`, `n_molecules`, `half_window`, `min_window_frames`, `ceiling`,
`min_qualifying_traces`, `intensity_quantity`, `app_version` and `created_utc`.

**`/settings/correction`** — `src/tether/project/correct.py`. Rewritten on every correction
pass, and the stage that turns α and γ into corrected FRET efficiency (`source` =
`accurate-fret`).

| Attribute | Meaning |
|---|---|
| `apparent_e_only` | `True` when the pass was told to keep every molecule on apparent E on purpose, even where factors exist. |
| `alpha_override`, `gamma_override` | A factor set by hand and applied to every molecule, or `NaN` when none was set. |
| `n_molecules` | Molecules in the pass. |
| `n_corrected`, `n_manual`, `n_apparent` | How many got estimated factors, manual factors, or fell back to apparent E. |
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
| `dtype`, `intensity_quantity` | What was fitted. |
| `n_molecules` | Molecules this fit covered. |
| `app_version`, `created_utc` | The Tether version and time of the fit. For an imported model this is the version that *imported* it, not the software that ran the fit. |
| `elbo`, `elbo_by_nstates` | The evidence lower bound of the chosen model, and of every state count tried (JSON), when the backend reports them. |

The backends and the papers behind each are listed on [Citing Tether](citing.md#idealization).

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
| `parameters` | The export's own settings, such as the intensity quantity and molecule count. |

Figures are stamped twice more: the version is printed in the figure footer and embedded in
the PDF, SVG and PNG metadata. A figure re-rendered and exported again at the same pinned
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
        show(f"/idealization/{name}", group.attrs)
```

The `isinstance` test skips `/settings/condition_audit`, which is a dataset (a log of
condition edits) rather than a group.

Here is that script's actual output for a project made by `tether batch` from a synthetic
25-molecule, 400-frame movie, with the default extraction settings and the default
idealization, on a development build. Numbers will differ for your data; the key names will
not.

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
  release tag, at commit `dfadf1f74`. That commit identifies the exact source, and it is what
  the output above shows.
- **`0.0.0`** means the build had no git history to read, for example one made from a
  downloaded source archive.
- **`0.0.0+unknown`** means the version could not be determined at all, for example code run
  straight from a source tree that was never installed.

To see the version you have installed, run `tether --version` once Tether's launcher folder
is on your PATH ([Install Tether, step 4](install.md#4-put-tethers-launcher-folder-on-your-path)),
or run the launcher by its full path as that page shows. For results already on disk, read
the stamps above instead: they record the version that actually ran each step, which may not
be the one installed today.

## Reproducing the environment

The version string pins Tether's own code. The libraries it ran on are pinned by three
committed lock files. CI checks on every change that each lock still matches the
specification it was solved from ([ADR-0004](adr/0004-pin-and-hold-dual-lock-isolation.md)):

| Lock | Pins | In the installer |
|---|---|---|
| `conda-lock.yml` | The application: Python, NumPy, Numba, h5py, PySide6, napari and the rest. | Yes, as `envs/tether`. |
| `sidecar/conda-lock.yml` | The isolated tMAVEN idealization environment (PyQt5, `numpy<2`). | Yes, as `envs/sidecar`. |
| `deep/conda-lock.yml` | The optional deep-classifier add-on (PyTorch). | **No.** It is a separate opt-in install; see [Deep trace classifier](ml/deep-classifier.md). |

**With an installer**, the environment comes with the version. Each installer is built from
per-platform locks rendered from the first two files, with no fresh solve, so two installs of
the same release get the same packages. Each release also publishes the three source locks
as assets on its [release page](https://github.com/bioedca/tether/releases), so you can see
exactly what a release pinned without installing it.

**From source**, check out the release tag you are reproducing, create the environment from
the committed lock, and install Tether on top of it without letting pip resolve anything:

```console
micromamba create -n tether -f conda-lock.yml   # or: conda-lock install -n tether
micromamba activate tether
pip install -e . --no-deps
```

Never create the environment from `environment.yml`: that is the specification the lock was
solved from, and solving it again today gives newer packages than the release used. The
sidecar has its own guided setup, `python scripts/setup_sidecar.py`, described in
[`CONTRIBUTING.md`](https://github.com/bioedca/tether/blob/main/CONTRIBUTING.md).

## A sample Methods paragraph

This paragraph is built from the read-back above: the version, the extraction settings, the
correction stamps and the idealization stamp. Swap in your own values and drop what you did
not run. The citations are the ones [Citing Tether](citing.md) gives for each step.

> Single-molecule FRET movies were processed with Tether version 1.0.0rc3.dev28+gdfadf1f74
> (https://doi.org/10.5281/zenodo.23306397). Donor and acceptor intensities were integrated
> over a 3.0-pixel-radius disk inside a 21-pixel window, with the local background taken from
> an annulus between 6.0 and 8.0 pixels and averaged over 10 frames (Wanninger et al., 2023).
> Intensities were corrected for donor-to-acceptor leakage (α = 0.079, estimated from 25 of
> 25 molecules after acceptor photobleaching) and for the detection-efficiency and
> quantum-yield ratio (γ = 0.99, estimated from 25 of 25 molecules at the acceptor
> photobleaching step) as described by McCann et al. (2010) and Hellenkamp et al. (2018);
> direct acceptor excitation was taken as zero under single-laser excitation. FRET
> trajectories were idealized with the consensus variational-Bayes hidden Markov model
> (Bronson et al., 2009) run through tMAVEN (Verma et al., 2024), with the number of states
> (three) chosen by the maximum evidence lower bound over one to four states.

A paper names a release rather than a development build. If one exact release matters, cite
that version's own DOI from its Zenodo record instead of the concept DOI. If steps ran under
different versions, name each one.

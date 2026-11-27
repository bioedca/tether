---
description: "What a project and its exports record about how they were made, how to read that record back, and how to write a Methods paragraph."
---

# Reproducibility and reporting

**Who this page is for.** Anyone who writes the Methods paragraph for an analysis done in Tether.
It is also for anyone who must tell a reviewer which version and which settings made a figure.
The page lists what a `.tether` project and its exports record about how they were made. It shows
how to read that record back, and how to rebuild the software environment that made it.

Tether's design rule is that every datum carries its origin
([ADR-0001](adr/0001-provenance-first-data-model.md)). In practice that means three records, and
the sections below cover them in turn:

- Inside the project, a version stamp and the settings of each stage: extraction, the leakage, γ
  and correction passes, and each idealization.
- Beside every publication export, a provenance file with the same stamp.
- In the source tree, committed dependency locks that pin the environment that the version ran in.

The record is not complete. Some steps and some settings are never stamped, and
[What is not recorded](#what-is-not-recorded) lists them. Other pages go further:

- The [project file reference](reference/tether-format.md) documents every group and dataset in a
  `.tether` file.
- The [exports reference](reference/exports.md) documents every column of every export.
- [Citing Tether](citing.md#reporting-a-tether-analysis) says what to cite.
- [Share and deposit a dataset](how-to/share-a-dataset.md) says which files to deposit with a
  paper.

## How to read this page

Each table of recorded attributes has the same five columns, in this order:

| Column | What it holds |
|---|---|
| **Attribute** | The name of the attribute or key, as its writer spells it. |
| **Type** | `int`, `float`, `bool` or `str` for an HDF5 attribute. A key of a provenance file is `str` or `object`. |
| **Unit** | The unit of a number: frames, pixels, count or dimensionless. A dash means that the value is text, a flag or a code. |
| **Empty value** | The value when there is no result, such as `NaN`. **absent** means that the attribute can be missing, and a dash means that the current writer always writes it. |
| **Meaning** | What the value is, in at most 25 words. A longer note is in the numbered notes below the table. |

A paragraph above each table names the module that writes the attributes. Search that file for an
attribute's name to find the line that writes it.

A warning before a table gives a fact that can change a number that you report. The
[Background](#background) section at the end gives the reasons behind some records.

## What is recorded

### The project root

`create_project` in `src/tether/io/schema.py` writes these attributes on the root group of every
project.

> **Do not report the root `app_version` as the version of a later step.** A project created with
> one release can be corrected or idealized with another. Each of those steps stamps its own
> version, as the sections below show.

| Attribute | Type | Unit | Empty value | Meaning |
|---|---|---|---|---|
| `format` | `str` | — | — | Always `tether-project`. It marks the file as a Tether project file. |
| `schema_version` | `int` | — | — | The version of the project file layout. |
| `app_version` | `str` | — | absent | The Tether version that **created** the project. A writer can leave it out, so a valid project can lack it. |

### Each pipeline stage, under `/settings`

Each stage writes one group under `/settings`. The group holds the stage's parameters and its own
`app_version`.

**`/settings/extraction`**, written by `src/tether/imaging/extract.py`. Tether writes it the
**first** time that a movie is extracted into the project, and it never rewrites it. A later movie
must use the same `window`, `disk_radius`, `ring_inner`, `ring_outer` and `bg_window`, or Tether
refuses the write.

> **Read `profile_json` and `app_version` as the record of the first movie only.** Tether compares
> nothing else for a later movie. So a later movie's detection or registration settings are not
> recorded.

> **Do not read the `/settings/extraction` of a Deep-LASI reconstruction as the settings of an
> integration.** In a project rebuilt from a Deep-LASI acquisition, it holds Tether's standard
> aperture values. They record where the traces came from, not an integration that Tether
> performed.

| Attribute | Type | Unit | Empty value | Meaning |
|---|---|---|---|---|
| `window` | `int` | pixels | — | The edge of the aperture window, which is also the edge of the patch. |
| `disk_radius` | `float` | pixels | — | The radius of the signal disk. |
| `ring_inner`, `ring_outer` | `float` | pixels | — | The radii of the background annulus. |
| `bg_window` | `int` | frames | — | The length of the background moving average. |
| `n_psf` | `int` | pixels | — | The number of pixels in the signal disk. |
| `molecule_key_quantum_px` | `float` | pixels | — | The coordinate quantum behind the key of each molecule. |
| `app_version` | `str` | — | — | The Tether version that extracted the first movie (note 1). |
| `profile_json` | `str` | — | absent | The settings profile as JSON. It is present only when a settings profile was supplied (note 2). |

**Notes**

1. In a project rebuilt from a Deep-LASI acquisition, it is the version that did the rebuild. It
   is not the software that extracted the traces.
2. From the native pipeline behind `tether extract` and `tether batch`, it holds the detection,
   registration and colocalization settings. It also holds the registration source and its RMS
   error. From a Deep-LASI reconstruction, it holds only where the traces came from: `source`,
   `coordinate_source`, `movie_name` and `exported_by`.

### Which molecules each factor was estimated on

Tether estimates α and γ **per condition inside the project**
([ADR-0158](adr/0158-leakage-and-gamma-are-estimated-per-condition-inside-the-project.md)). A
condition is the `condition_id` of a molecule in `/molecules`. Each pass groups the molecules by
that value and runs its estimator once for each group. So the α of a condition rests only on that
condition's molecules in this project file. It is the α that the same code gives when that
condition is alone in a project.

The same holds for the γ median and its fallback. Molecules with no `condition_id` form one
group.

Two consequences matter when you report a factor:

- **A condition that spans several project files has one estimate in each file.** A project is one
  experiment, one day or one session of acquisition
  ([ADR-0068](adr/0068-a-project-holds-one-experiment.md)). Tether does not pool a condition's
  molecules across files for α or γ. Report the factor of each file, or say which one you used.
- **The condition of a molecule is the key that Tether holds for it.** A key parsed from a
  filename is provisional until you confirm it. It carries no buffer, temperature or laser power,
  so two conditions that differ only in those fields share one group, and one α.

The `conditions` attribute of each group below records the factors and the molecule counts of
each condition. It is a JSON list with one object for each condition, in the order the condition
first appears in `/molecules`. With one condition, the top-level `alpha` and `gamma` attributes
equal that condition's factor, as before. With several, they are `NaN`: read `conditions`.

**`/settings/leakage`**, written by `src/tether/project/leakage.py`. Tether rewrites it on every
leakage pass. The pass estimates the donor-to-acceptor leakage factor α from the signal that is
left in the acceptor channel after the acceptor bleaches. The other attributes record the inputs to
the decision to withhold α.

| Attribute | Type | Unit | Empty value | Meaning |
|---|---|---|---|---|
| `alpha` | `float` | dimensionless | `NaN` (withheld, or several conditions) | The leakage factor α of the project's one condition. |
| `withheld` | `bool` | — | — | `True` when too few traces qualified in every condition. With one condition, `alpha` is then `NaN`. |
| `source` | `str` | — | — | The estimator, `post-acceptor-bleach-tail`. |
| `n_qualifying` | `int` | count | — | The number of traces that gave an α. |
| `n_molecules` | `int` | count | — | The number of molecules in the pass. |
| `min_window_frames` | `int` | frames | — | The shortest tail after the acceptor bleach from which α can be measured. |
| `ceiling` | `float` | dimensionless | — | The largest α of one trace that the pass accepts. |
| `min_qualifying_traces` | `int` | count | — | How many traces had to qualify before the pass gave an α. |
| `intensity_quantity` | `str` | — | — | The trace layer that the pass read. |
| `conditions` | `str` (JSON) | — | absent (note 1) | For each condition: `condition_id`, `alpha` (`null` when withheld), `withheld`, `n_molecules`, `n_qualifying` and `source`. |
| `n_conditions` | `int` | count | absent (note 1) | The number of conditions in `conditions`. |
| `app_version`, `created_utc` | `str` | — | — | The version and the time of the pass. |

**Notes**

1. A project from before
   [ADR-0158](adr/0158-leakage-and-gamma-are-estimated-per-condition-inside-the-project.md) has
   no `conditions` or `n_conditions` here, under `/settings/gamma` or under
   `/settings/correction`. Its passes pooled every molecule, which is the same as one condition.

A condition whose α is withheld has its processed `alpha` and `gamma` set to `NaN`. The other
conditions keep theirs.

**`/settings/gamma`**, written by `src/tether/project/gamma.py`. Tether rewrites it on every γ
pass. A leakage pass that withholds α removes it, because that pass clears the processed `gamma`
rows. The pass estimates the detection-efficiency and quantum-yield ratio γ **per molecule**, at
the acceptor bleach step of the molecule.

Each molecule that passes the gates stores its own value in the `gamma` column of `/molecules`.
The median is taken for each condition. The `n_fallback` molecules that failed the gates receive
their condition's median instead.

When a condition's median is withheld, Tether clears the
`gamma` of every processed molecule of that condition to `NaN`. This includes the molecules that
had an estimate of their own. A condition whose α was withheld is not examined, and its γ is
withheld with it. The counts below are the sums over the conditions.

> **Do not report the stored γ as applied to a value.** Storing γ is all that the pass does.
> Nothing in Tether applies γ to a value that it shows or exports (see the warning under
> [A sample Methods paragraph](#a-sample-methods-paragraph)).

> **Check a γ whose `few_clean_steps` is `True`, report it with that caveat, or enter a factor
> measured another way.** The flag does not change the stored γ. It is not proof that the γ is
> wrong
> ([Why the few-clean-steps flag is only a reason to check](#why-the-few-clean-steps-flag-is-only-a-reason-to-check)).

| Attribute | Type | Unit | Empty value | Meaning |
|---|---|---|---|---|
| `gamma` | `float` | dimensionless | `NaN` (withheld, or several conditions) | The population median of the per-molecule γ values of the project's one condition. |
| `withheld` | `bool` | — | — | `True` when too few molecules qualified in every condition. With one condition, `gamma` is then `NaN`. |
| `source` | `str` | — | — | The estimator, `acceptor-bleach-step`. |
| `n_qualifying` | `int` | count | — | The number of molecules that passed the gates and have their own γ. |
| `n_fallback` | `int` | count | — | The number of molecules that failed the gates and receive the median. |
| `n_molecules` | `int` | count | — | The number of molecules in the pass. |
| `level_window` | `str` | — | absent (note 1) | The frames that each γ level was averaged over: `half-window` or `whole-segment` (note 2). |
| `half_window` | `int` | frames | — | The frames on each side of the step, 20 by default. It is `0` for `whole-segment`. |
| `min_window_frames` | `int` | frames | — | The length that both segments around the step had to be longer than. |
| `ceiling` | `float` | dimensionless | — | The largest γ of one molecule that the pass accepts. |
| `min_qualifying_traces` | `int` | count | — | How many molecules had to qualify before the pass gave a γ. |
| `intensity_quantity` | `str` | — | — | The trace layer that the pass read. |
| `n_step_evaluated` | `int` | count | absent (note 3) | The number of molecules whose acceptor-bleach step was measured, because both of its segments were long enough. |
| `n_step_clean` | `int` | count | absent (note 3) | The number of measured steps with the shape of an acceptor bleach (note 4). |
| `min_clean_step_fraction` | `float` | dimensionless | absent (note 3) | The share of clean steps below which the pass sets `few_clean_steps`. |
| `few_clean_steps` | `bool` | — | absent (note 3) | `True` when γ was applied to a condition and fewer than `min_clean_step_fraction` of that condition's measured steps were clean. |
| `n_excluded` | `int` | count | — | The number of molecules that a person or a recipe excluded from the median in the γ evidence table. |
| `conditions` | `str` (JSON) | — | absent (note 5) | For each condition: `condition_id`, `gamma` (`null` when withheld), `withheld`, `leakage_withheld`, `n_molecules`, `n_qualifying`, `n_fallback`, `n_excluded`, `n_step_evaluated`, `n_step_clean`, `few_clean_steps` and `source`. |
| `n_conditions` | `int` | count | absent (note 5) | The number of conditions in `conditions`. |
| `app_version`, `created_utc` | `str` | — | — | The version and the time of the pass. |

**Notes**

1. A project from before
   [ADR-0130](adr/0130-the-gamma-levels-are-averaged-over-20-frames-each-side.md) has no
   `level_window`.
2. `half-window` means that `half_window` frames on each side of the step were used.
   `whole-segment` means that the whole segments were used.
3. A project from before
   [ADR-0134](adr/0134-a-gamma-from-few-clean-acceptor-bleach-steps-is-reported-with-a-warning.md)
   has none of these four attributes.
4. A clean step is one where the donor rose and the leakage-corrected acceptor dropped across the
   step.
5. As note 1 under `/settings/leakage`.

`n_excluded` is `0` until a person or a recipe excludes a molecule.

**`/settings/correction`**, written by `src/tether/project/correct.py`. Tether rewrites it on every
correction pass. The pass decides, for each molecule, whether its α and γ are usable. It records
the outcome in `/molecules` as `correction_method` and `correction_confidence`, and it writes any
manual factor into `alpha` and `gamma`. It does not compute a corrected E itself.

A pass limited to one movie keeps the `conditions` records of the conditions it did not examine.
The record of a condition it did examine still counts the molecules of that condition's other
movies.

> **Do not read `corrected` as proof that the factors were estimated.** `corrected` means that
> the molecule had a finite stored α and a positive stored γ, and that this pass requested no
> override. A factor stored earlier by an override or by a Deep-LASI reconstruction counts as
> `corrected` on a later pass with no override. The later pass replaces this group, so check how
> the factors were produced if that matters.

| Attribute | Type | Unit | Empty value | Meaning |
|---|---|---|---|---|
| `source` | `str` | — | — | The method, `accurate-fret`. |
| `apparent_e_only` | `bool` | — | — | `True` when the pass was told to keep every molecule on apparent E on purpose, even where factors exist. |
| `alpha_override`, `gamma_override` | `float` | dimensionless | `NaN` (none requested) | A factor requested by hand (note 1). |
| `n_molecules` | `int` | count | — | The number of molecules in the pass. |
| `n_corrected`, `n_manual`, `n_apparent` | `int` | count | — | How many molecules this pass classified `corrected`, `manual` or apparent E. |
| `total_failure` | `bool` | — | — | `True` when no molecule carries a correction (note 2). |
| `override_condition_id` | `str` | — | absent (no override, or note 3) | The condition that `alpha_override` and `gamma_override` were applied to. |
| `conditions` | `str` (JSON) | — | absent (note 3) | For each condition: `condition_id`, `alpha` and `gamma` with their `alpha_source` and `gamma_source` (`estimated`, `manual` or `null`), `n_molecules`, `n_corrected`, `n_manual`, `n_apparent` and `total_failure`. Also `alpha_manual` or `gamma_manual` when it carries several manual values (note 4). |
| `n_conditions` | `int` | count | absent (note 3) | The number of conditions in `conditions`. |
| `app_version`, `created_utc` | `str` | — | — | The version and the time of the pass. |

**Notes**

1. It is recorded whether or not it took effect. `apparent_e_only`, for one, overrides it. So read
   `n_manual` for how many molecules it was actually applied to.
2. It is also `True` under `apparent_e_only`, where nothing was corrected by choice. So check that
   attribute first.
3. As note 1 under `/settings/leakage`.
4. `total_failure` of a condition is `True` when no molecule of that condition carries a
   correction. That condition is then on apparent E, while the others may be corrected. `gamma`
   is the condition's median from `/settings/gamma`, or the manual factor. A molecule whose own
   step qualified keeps its own γ in `/molecules`. Two movies of one condition can take
   different manual factors. Then `gamma` is `null`, and `gamma_manual` lists the values. The
   same holds for α.

Direct excitation (δ) has no attribute here. Tether's two-color, single-laser scheme carries δ as
an inert `0` ([ADR-0008](adr/0008-correction-factor-remap.md)). Tether stores it for each molecule
in the `delta` column of `/molecules`, not under `/settings`.

**`/settings/batch`**, written by `src/tether/project/batch.py`. `tether batch` writes it and
rewrites it on every run.

| Attribute | Type | Unit | Empty value | Meaning |
|---|---|---|---|---|
| `source` | `str` | — | — | The writer, `batch-runner`. |
| `policy` | `str` | — | — | The failure policy of the run, such as `warn`. |
| `idealize_requested` | `bool` | — | — | `True` when the run asked for the idealization stage. |
| `extract_status`, `correct_status`, `idealize_status` | `str` | — | — | How each stage ended, one `<stage>_status` for each stage. |
| `app_version`, `created_utc` | `str` | — | — | The version and the time of the run. |

**`/settings/analysis_only`**, written by `src/tether/project/analysis_import.py`. It is present
only in a project file imported from someone else's traces, where there is no movie to go back to.
Such a project file has no `/settings/extraction` at all.

| Attribute | Type | Unit | Empty value | Meaning |
|---|---|---|---|---|
| `round_trip_available` | `bool` | — | — | Always `False`: there is no movie to go back to. |
| `reason` | `str` | — | — | Why the round trip is not available. |
| `n_molecules` | `int` | count | — | The number of molecules imported. |
| `n_frames` | `int` | frames | — | The shared frame extent of the imported traces. |
| `banner` | `str` | — | — | The notice that the project file has no coordinates or patches. |
| `source` | `str` | — | — | The name of the imported file. |
| `source_kind` | `str` | — | — | The reader that read it: `smd` or `txt`. |
| `app_version`, `created_utc` | `str` | — | — | The version and the time of the import. |

### Each idealization, under `/idealization`

Every fitted model is its own group, `/idealization/<model>`. `write_idealization_model` in
`src/tether/project/idealize.py` stamps it.

> **Report the state means of a Tether fit as apparent efficiencies.** For Tether's own fits,
> `intensity_quantity` is `corrected`. That means the background-subtracted layer, not α- or
> γ-corrected. The fit runs on apparent E computed from it
> ([ADR-0029](adr/0029-idealization-correction-provenance-hash-and-per-factor-staleness.md)).

> **Record the software and the time of the fit yourself for a model imported from standalone
> tMAVEN.** Its `app_version` and `created_utc` describe the import instead. They give the version
> that imported it and the time it was written (`apply_reconcile`,
> `src/tether/project/handoff.py`).

| Attribute | Type | Unit | Empty value | Meaning |
|---|---|---|---|---|
| `type` | `str` | — | — | The backend, for example `vbconhmm` (Tether's default) or `ebhmm`. A model imported from standalone tMAVEN keeps tMAVEN's own label. |
| `nstates` | `int` | count | — | The number of states in the chosen model. |
| `nstates_selected_by` | `str` | — | — | How `nstates` was chosen: `max-elbo`, `fixed` or `imported` (note 1). |
| `dtype` | `str` | — | — | The quantity that was fitted. For Tether's own fits, it is `FRET` (note 2). |
| `intensity_quantity` | `str` | — | — | The trace layer that the fit read. |
| `n_molecules` | `int` | count | — | The number of molecules that this fit covered. |
| `app_version`, `created_utc` | `str` | — | — | The Tether version and the time of the fit. |
| `elbo` | `float` | dimensionless | absent | The evidence lower bound of the chosen model, when the backend reports it. |
| `elbo_by_nstates` | `str` | dimensionless | absent | The evidence lower bound of every state count tried, as JSON, when the backend reports them. |

**Notes**

1. `max-elbo` is the best evidence lower bound over the states tried.
2. A model imported from standalone tMAVEN keeps whatever `dtype` tMAVEN gave it, unchecked. So
   read `dtype` to know what quantity its state means are in.

The backends and the papers behind each are listed on [Citing Tether](citing.md#idealization).

### Two more stamps

Two other records carry a version, and the read-back below does not print them:

- The engineered feature table, the dataset `/features/table`, carries `app_version`,
  `created_utc`, `intensity_quantity`, `feature_schema_version`, `feature_names` and `n_molecules`
  as attributes (`src/tether/project/features.py`).
- Each row of the `/settings/condition_audit` log of condition re-keys and merges carries its own
  `app_version` and `timestamp` (`src/tether/project/conditions.py`).

### What is not recorded

Some steps leave no version or parameter stamp in the project, and one leaves only part of one:

- Photobleach detection. Rerunning it overwrites the bleach frames and analysis windows and
  records no version.
- Ranking and classification runs. A saved classical ranker records the version it was trained
  under (`tether_version` in its model manifest). The deep classifier records none.
- The SMD file handed off to standalone tMAVEN has no provenance file. A fit run there is stamped
  only with the Tether version that imported it, so report the tMAVEN version you ran.
- Extraction is stamped once, for the first movie (see `/settings/extraction` above). Tether
  records no per-movie version, so the release that extracted a later movie is not recorded.

For those, keep your own note of the version and settings. See
[Citing Tether](citing.md#reporting-a-tether-analysis) for what to quote.

Nor is every setting of the stamped stages stored. For example, Tether passes the restart count of
the idealization fitter (`nrestarts`) to tMAVEN, but it never writes it to the model group. The
*Recorded in `.tether`* column of the [parameters reference](reference/parameters.md) says, for
each tunable, whether and where it is stamped. Keep your own note of any tunable marked **no**
that you changed.

### Every export

Each publication export writes a provenance file beside its output with the same stamp
(`write_provenance_sidecar`, `src/tether/project/export.py`). Its name is the full output filename
with `.provenance.json` added, so the original extension stays: `molecules.csv.provenance.json`,
`traces.txt.provenance.json` and `subset.tether.provenance.json`. A figure exported with
`export_figure` (`src/tether/analysis/plot_export.py`) is named by its stem. So its provenance file
is `<stem>.provenance.json`.

> **Pass every setting that a plot was drawn with when you export a figure.** Nothing records a
> plotting choice, such as the bin count, unless the code that exports the figure passes it.

| Attribute | Type | Unit | Empty value | Meaning |
|---|---|---|---|---|
| `tether_export` | `str` | — | — | The export that wrote the file, for example `molecule-table-csv`. |
| `app_version` | `str` | — | — | The Tether version that wrote the export. |
| `created_utc` | `str` | — | — | When the export was written. |
| `source_project` | `str` | — | — | The project that the export came from, as a file name (note 1). |
| `parameters` | `object` | — | — | The export's own settings, such as the intensity quantity and the molecule count (note 2). |

**Notes**

1. For a figure, it is whatever the code that exported it passed in.
2. For a figure, it holds whatever the code that exported it passed in, plus `formats`, `dpi` and
   `outputs`.

Figures carry the version in two more places. Tether prints it in the figure footer, and it embeds
it in the PDF, SVG and PNG metadata. The printed footer is there only with the default
`stamp=True` of `export_figure`. A caller that passes `stamp=False` gets no footer, but Tether
still writes the provenance file and the embedded metadata. A figure that is rendered and exported
again at the same pinned time gives byte-identical files in all three formats. The
[exports reference](reference/exports.md#provenance-file) lists the parameters of each export.

## How to read it back

You do not need Tether to read any of this. Any HDF5 viewer shows the attributes. With `h5py`,
this script prints the root and every stage and model. For your own project, put its file name in
place of `reproducibility_movie.tether`:

```python
import h5py


def show(name, attrs):
    print(f"[{name}]")
    for key in sorted(attrs):
        value = attrs[key]
        if isinstance(value, bytes):
            value = value.decode()
        print(f"  {key} = {value}")


with h5py.File("reproducibility_movie.tether", "r") as f:
    show("/", f.attrs)
    for name, group in f.get("settings", {}).items():
        if isinstance(group, h5py.Group):
            show(f"/settings/{name}", group.attrs)
    for name, group in f.get("idealization", {}).items():
        if not name.endswith(".__writing__"):
            show(f"/idealization/{name}", group.attrs)
```

The script skips two kinds of entry:

- The `isinstance` test skips `/settings/condition_audit`, which is a dataset (a log of condition
  edits) and not a group.
- The `.__writing__` test skips the staging group that a fit leaves behind if it is interrupted
  while it is saved. That group is an incomplete model. `list_idealizations` in
  `src/tether/project/idealize.py` skips it in the same way.

Here is that script's output for a project that you can make yourself. It is an illustration, not
a reference value.

The project comes from a synthetic movie of 16 molecules and 150 frames. The
movie is written by `scripts/make_tutorial_movie.py`, the same script that makes the movie of the
[tutorial](tutorial.md). The tutorial's three molecules and 24 frames are too few for the leakage
and γ estimates, so this movie is larger. Its leakage α is 0.08 and its γ is 1 by construction. So
you can compare those with what Tether estimates.

To make the project, do these steps:

1. Download
   [`make_tutorial_movie.py`](https://github.com/bioedca/tether/blob/main/scripts/make_tutorial_movie.py)
   into an empty folder.
2. Move into that folder.
3. Run the two commands below. Use the Python that came with Tether in place of `python`
   ([Your first analysis, step 9](tutorial.md#9-export-the-molecule-table) gives its path).

```console
python make_tutorial_movie.py --reproducibility-movie reproducibility_movie.tif
tether batch reproducibility_movie.tif -d .
```

The first command writes the movie, about 3.6 MB. The second extracts it into
`reproducibility_movie.tether`. It runs the leakage, γ and correction passes and the default
idealization, and it writes `batch-log.jsonl`. Then run the script above in the same folder.

Some values differ between runs and machines:

- The output shows `<your version>` for the Tether version and `<time of the run>` for each time
  stamp. Yours shows the real ones.
- The `elbo` values come from the tMAVEN fit. They can differ in their last digits from one run to
  the next.
- Other numbers can differ in their last digits between operating systems.

Everything else should match. With your own data, the numbers will differ. The set of groups and
keys can differ too, because Tether writes some of them only when a step ran or gave a result:

- `/settings/gamma` is absent when leakage withheld α (too few qualifying molecules), because
  `tether batch` then skips γ. The correction pass still runs and records the apparent-E fallback.
- `/idealization/<model>` is absent when no fit completed. `elbo` and `elbo_by_nstates` appear only
  when the backend reports them.
- `profile_json` appears only when a settings profile was supplied.
- `/settings/batch` appears only for a `tether batch` run. `/settings/analysis_only` appears only
  for an imported analysis-only project file.

In an intact project, a missing group means that the step left no completed record. The step did
not run, withheld its result, or ran and failed. For a `tether batch` run, each `<stage>_status` in
`/settings/batch` records how that stage ended. The value is `done`, `skipped`, `failed`,
`blocked`, `deferred` or `not-requested`, and the batch log has the detail.

> **Read the batch log for warnings, also when a stage reads `done`.** A warning, such as a
> provenance stamp that failed, goes only to the batch log.

The groups alone cannot tell any of these cases apart from a record that was deleted or damaged.
One loss does leave a trace. A save interrupted between removing an old model and renaming its
replacement can leave only the `<model>.__writing__` staging group, which the script skips. If
`/idealization` holds one with no matching model, run that fit again.

```text
[/]
  app_version = <your version>
  format = tether-project
  schema_version = 4
[/settings/extraction]
  app_version = <your version>
  bg_window = 10
  disk_radius = 3.0
  molecule_key_quantum_px = 0.1
  n_psf = 29
  profile_json = {"app_version": "<your version>", "bg_window": 10, "channel_layout": null, "coloc_distance": 3.0, "colocalization": "partner-in-both", "detection_block": 50, "detection_mode": "wavelet", "detection_threshold": null, "disk_radius": 3.0, "donor_side": "left", "donor_side_check": {"level_frames": 10, "majority": 0.8, "min_events": 15, "min_frames": 5, "n_consistent": 10, "n_events": 10, "n_molecules": 16, "n_swapped": 0, "n_together": 1, "reason": "", "step_z": 3.0, "verdict": "insufficient"}, "map_source": null, "min_separation": null, "n_control_points": 16, "pair_tol": 2.0, "pipeline": "native", "prealign": "translation", "prealign_high_sigma": 20.0, "prealign_low_sigma": 3.0, "prealign_upsample": 10, "registration_rms_px": 3.901419122093834e-14, "registration_source": "native", "ring_inner": 6.0, "ring_outer": 8.0, "rms_gate": 0.5, "tdat_source": null, "tmap_source": null, "window": 21}
  ring_inner = 6.0
  ring_outer = 8.0
  window = 21
[/settings/leakage]
  alpha = 0.07964339582206567
  app_version = <your version>
  ceiling = 0.3
  conditions = [{"condition_id": "cond-09baefb9a15b", "alpha": 0.07964339582206567, "withheld": false, "n_molecules": 16, "n_qualifying": 15, "source": "post-acceptor-bleach-tail"}]
  created_utc = <time of the run>
  intensity_quantity = corrected
  min_qualifying_traces = 10
  min_window_frames = 20
  n_conditions = 1
  n_molecules = 16
  n_qualifying = 15
  source = post-acceptor-bleach-tail
  withheld = False
[/settings/gamma]
  app_version = <your version>
  ceiling = 5.0
  conditions = [{"condition_id": "cond-09baefb9a15b", "gamma": 1.002771186368295, "withheld": false, "leakage_withheld": false, "n_molecules": 16, "n_qualifying": 15, "n_fallback": 1, "n_excluded": 0, "n_step_evaluated": 15, "n_step_clean": 15, "few_clean_steps": false, "source": "acceptor-bleach-step"}]
  created_utc = <time of the run>
  few_clean_steps = False
  gamma = 1.002771186368295
  half_window = 20
  intensity_quantity = corrected
  level_window = half-window
  min_clean_step_fraction = 0.65
  min_qualifying_traces = 10
  min_window_frames = 20
  n_conditions = 1
  n_excluded = 0
  n_fallback = 1
  n_molecules = 16
  n_qualifying = 15
  n_step_clean = 15
  n_step_evaluated = 15
  source = acceptor-bleach-step
  withheld = False
[/settings/correction]
  alpha_override = nan
  app_version = <your version>
  apparent_e_only = False
  conditions = [{"condition_id": "cond-09baefb9a15b", "alpha": 0.07964339582206567, "alpha_source": "estimated", "gamma": 1.002771186368295, "gamma_source": "estimated", "n_molecules": 16, "n_corrected": 16, "n_manual": 0, "n_apparent": 0, "total_failure": false}]
  created_utc = <time of the run>
  gamma_override = nan
  n_apparent = 0
  n_conditions = 1
  n_corrected = 16
  n_manual = 0
  n_molecules = 16
  source = accurate-fret
  total_failure = False
[/settings/batch]
  app_version = <your version>
  correct_status = done
  created_utc = <time of the run>
  extract_status = done
  idealize_requested = True
  idealize_status = done
  policy = warn
  source = batch-runner
[/idealization/vbconhmm]
  app_version = <your version>
  created_utc = <time of the run>
  dtype = FRET
  elbo = 988.0485063731418
  elbo_by_nstates = {"1": -289.3029186673166, "2": 988.0485063731418, "3": 777.3874486897873, "4": -346.60709299751613}
  intensity_quantity = corrected
  n_molecules = 16
  nstates = 2
  nstates_selected_by = max-elbo
  type = vbconhmm
```

An export's provenance file is plain JSON. To make one from the same project, start the Python that
came with Tether in the same folder, and export the molecule table:

```pycon
>>> from tether.project.export import export_molecule_table_csv
>>> result = export_molecule_table_csv("reproducibility_movie.tether", "molecules.csv")
>>> result.n_molecules
16
```

That writes `molecules.csv` and its provenance file, `molecules.csv.provenance.json`. This script
prints the stamp and then each parameter:

```python
import json

with open("molecules.csv.provenance.json", encoding="utf-8") as fh:
    stamp = json.load(fh)

for key in ("tether_export", "app_version", "created_utc", "source_project"):
    print(f"{key}: {stamp[key]}")
for key, value in sorted(stamp["parameters"].items()):
    print(f"parameters.{key}: {value}")
```

Its output, with the same two placeholders:

```text
tether_export: molecule-table-csv
app_version: <your version>
created_utc: <time of the run>
source_project: reproducibility_movie.tether
parameters.include_rejected: True
parameters.intensity_quantity: corrected
parameters.n_molecules: 16
```

## What a version string means

Tether's version comes from git when it is built. So the string tells you which build you have:

- **A release**, such as `1.0.0`, or a release candidate, such as `1.0.0rc2`, is what an installer
  from the [releases page](https://github.com/bioedca/tether/releases) reports.
- **A development build** looks like `1.0.0rc3.dev28+gdfadf1f74`: 28 commits after the last
  release tag, at commit `dfadf1f74`. That commit identifies the exact source only for a build
  made from a clean checkout. A dirty or edited checkout runs code that the commit does not name
  ([Reproducing the environment](#reproducing-the-environment)).
- **`0.0.0`** means one of two things. It is a build that had no git history to read, for example
  one made from a downloaded source archive. Or it is a build at the project's first milestone
  tag, `v0.0.0`. The string alone cannot tell them apart, so check it against your own record of
  what you installed.
- **`0.0.0+unknown`** means that the version could not be found at all. An example is code run
  straight from a source tree that was never installed.

To see the version that you have installed, run `tether --version`. This works once Tether's
launcher folder is on your PATH
([Install Tether, step 4](install.md#optional-put-the-launcher-folder-on-your-path)). Or run the
launcher by its full path, as that page shows.

> **Read the stamps above, not the installed version, for results already on disk.** The stamps
> record the version that actually ran each step. That may not be the version installed today.

## Reproducing the environment

A release, or a development build installed from a clean checkout, pins Tether's own code through
its version string. Three cases do not:

- `0.0.0+unknown` names no commit at all.
- `0.0.0` names a commit only when you know that the build was made at the `v0.0.0` tag. The
  string alone cannot tell you that, as the section above explains.
- An editable install (`pip install -e`, used below) runs whatever is in the checkout. The version
  string names only the commit that it was built from. So it does not show uncommitted changes, or
  edits made after the install.

> **Keep a separate record of the source for these three cases.** Keep the commit plus a patch, a
> source archive or a checksum of the source that you ran.

Three committed lock files pin the libraries that Tether ran on. CI checks on every change that
each lock still matches the specification that it was solved from
([ADR-0004](adr/0004-pin-and-hold-dual-lock-isolation.md)):

| Lock | Pins | In the installer |
|---|---|---|
| `conda-lock.yml` | The application: Python, NumPy, Numba, h5py, PySide6, napari and the rest. | Yes, as `envs/tether`. |
| `sidecar/conda-lock.yml` | The isolated tMAVEN idealization environment (PyQt5, `numpy<2`). | Yes, as `envs/sidecar`. |
| `deep/conda-lock.yml` | The optional deep-classifier add-on (PyTorch), CPU build only. | **No.** It is a separate opt-in install ([Deep trace classifier](ml/deep-classifier.md)). |

> **Record the PyTorch wheel version, its CUDA channel and your NVIDIA driver version yourself if
> you trained or scored with the GPU build.** The deep lock covers only the CPU build. The GPU path
> adds a CUDA PyTorch wheel from the PyTorch index, with whatever pip resolves beside it. That
> install is deliberately not pinned
> ([ADR-0047](adr/0047-deep-model-optional-stack-and-dataset.md)). `pip freeze` in that
> environment lists the wheels.

Two parts of the sidecar are outside its lock:

- tMAVEN itself, which `scripts/setup_sidecar.py` installs from a pinned git commit.
- A setuptools compatibility wheel, pinned by version and hash in
  `packaging/setuptools-compatibility.txt`. It replaces the lock's own setuptools.

From `v1.0.0-rc2` on, the installer bundles both. A release's record of its environment is then
those three locks, plus `scripts/setup_sidecar.py` and `packaging/setuptools-compatibility.txt` at
the release tag. `v1.0.0-rc1` predates the compatibility pin. The file is not at that tag, and its
sidecar has the lock's own setuptools. So its one-click idealization fails with a `pkg_resources`
error ([Install Tether](install.md) says so). Each release also publishes a software bill of
materials, `tether-sbom.cyclonedx.json`, beside the locks.

**With an installer**, the environment comes with the version. Each installer is built from
per-platform locks rendered from the first two files, with no fresh solve. So two installs of the
same release on the same platform get the same packages. Linux, Intel macOS, Apple silicon macOS
and Windows each have their own solution, so the packages differ between platforms.

Each release
publishes the source locks as assets on its
[release page](https://github.com/bioedca/tether/releases). So you can see exactly what a release
pinned without installing it. `v1.0.0-rc1` predates the addition of the deep lock to the release
assets. It carries only the base and sidecar locks
([its asset record](release.md#what-a-published-release-contains-verified-v100-rc1)). For that
release, read `deep/conda-lock.yml` from the `v1.0.0-rc1` tag in the source tree.

**From source**, check out the release tag that you are reproducing. Then create the environment
from the committed lock, and install Tether on top of it without letting pip install any runtime
dependency.

> **Never create the environment from `environment.yml`.** That is the specification that the lock
> was solved from. Solving it again today gives newer packages than the release used.

```console
micromamba create -n tether -f conda-lock.yml   # or: conda-lock install -n tether
micromamba activate tether
pip install -e . --no-deps
```

The lock does not pin one step. `pip install` still downloads Tether's build backend (`hatchling`
and `hatch-vcs`) into a temporary build environment. So it needs network access, and the version
of the backend can differ from the one that the release was built with.

The sidecar has its own guided setup, `python scripts/setup_sidecar.py` from the same tag. It
installs the pinned tMAVEN and setuptools
([`CONTRIBUTING.md`](https://github.com/bioedca/tether/blob/main/CONTRIBUTING.md)).

## A sample Methods paragraph

This paragraph is built from the read-back above: the version, the extraction settings, the
correction stamps and the idealization stamp. The spread of the per-molecule γ values comes from
the `gamma` column of the molecule-table CSV exported from the same project. Put in your own
values, and remove what you did not run. The citations are the ones that
[Citing Tether](citing.md) gives for each step.

> **Report every E value from Tether as apparent E, unless you computed corrected E yourself.**
> Tether stores α and γ with each molecule, but it does not apply them to any value that it shows
> or exports. The trace views, the analysis functions, the E summary of the molecule-table CSV and
> the hidden Markov model all use apparent E. So none of them is a corrected efficiency. If you
> report corrected E, compute it yourself from the stored factors, and say so. The
> [exports reference](reference/exports.md#at-a-glance) gives the formula.

Here is the sample, for the project made above:

> Single-molecule FRET movies were processed with Tether version `<your version>`
> (<https://doi.org/10.5281/zenodo.23306397>). Donor and acceptor intensities were integrated
> over a 3.0-pixel-radius disk inside a 21-pixel aperture window, with the local background taken from
> an annulus between 6.0 and 8.0 pixels and averaged over 10 frames (Wanninger et al., 2023).
> Correction factors were estimated as described by McCann et al. (2010) and Hellenkamp et
> al. (2018): donor-to-acceptor leakage α = 0.080, from 15 of 16 molecules after acceptor
> photobleaching, and the detection-efficiency and quantum-yield ratio γ, estimated per
> molecule at the acceptor photobleaching step (median 1.00, interquartile range 0.99 to
> 1.02; 15 of the 16 molecules had their own estimate and the other took the median).
> Direct acceptor excitation was taken as zero under single-laser excitation. FRET trajectories were
> idealized on the apparent FRET efficiency of the background-subtracted intensities,
> without the α and γ corrections, using the consensus variational-Bayes hidden Markov model
> (Bronson et al., 2009) run through tMAVEN (Verma et al., 2024), with the number of states
> (two) chosen by the maximum evidence lower bound over one to four states; the state
> means are therefore apparent efficiencies.

Report the version that actually produced the results, also when it is a development build. Never
put in a release that did not run. For a paper, run the final analysis again on a release if you
can, and then cite that release. If one exact release matters, cite the DOI of that version from
its Zenodo record, not the concept DOI. If steps ran under different versions, name each one.

## Background

### Why the few-clean-steps flag is only a reason to check

The flag is a reason to check the γ, not proof that it is wrong. Noise alone can make many steps
read clean by chance, and ADR-0134 states the noise model. So the γ may rest largely on noise
([ADR-0134](adr/0134-a-gamma-from-few-clean-acceptor-bleach-steps-is-reported-with-a-warning.md)).

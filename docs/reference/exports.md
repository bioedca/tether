---
description: "The files that Tether writes out: each column, its order, its unit and what a blank cell means, for tables, trace files, subsets and figures."
---

# Exports — what Tether writes out

**Who this page is for.** Anyone who has a file that Tether wrote and needs to know exactly what
is in it. Examples are a molecule-table CSV for a figure, a `…-donc-accc-w.txt` for a
collaborator, a subset `.tether` for a reviewer and a PDF for a manuscript. The page assumes that
you can read a CSV or an HDF5 file in your own tool. It is a reference, not a tutorial. Each claim
names the module, function or constant behind it, so you can search the source for it.

## How to read this page

Each table on this page uses one of these entry forms:

| Kind of entry | Columns, in this order |
|---|---|
| A column of an exported table | **Column**, **Type**, **Unit**, **Blank when** and **Meaning**. The molecule-table CSV puts the unit and the meaning in one **Unit / domain** cell, unit first. |
| An option of an export function | **Option**, **Unit**, **Default**, **Allowed values** and **Meaning**. |
| A property of a file format | **Property** and **Value**. |
| A field of a provenance file or an attribute of a subset | **Field** or **Attribute**, and its **Value**. |

A meaning has at most 25 words. A cell has at most two sentences. A longer note is numbered below
its table, and the cell names it, for example "(note 2)". A dash means that the entry does not
apply.

Units have the same names on every page: frames, seconds, pixels, counts, fraction and
dimensionless. **Trace intensity** has no physical unit (see [Intensity units](#intensity-units)).

A warning before a table gives a fact that can change a number that you publish. The
[Background](#background) section at the end gives the reasons behind some of the behavior.

> **Convert frames to seconds yourself.** Every temporal number in the three *project* exports
> (CSV, `.txt` and subset `.tether`) is in *frames*, zero-based, never seconds. None of the three
> carries the frame duration, and [Frames to seconds](#frames-to-seconds) shows the conversion.

The project file keeps the frame duration (`/movies.frame_time`, `tether.io.schema.MOVIES_DTYPE`).
But the molecule-table CSV has no such column, and the Deep-LASI `.txt` has no metadata at all. A
subset `.tether` carries **zero** `/movies` rows by construction. The fourth export is the
exception. A plot renderer that gets `dt` or `time_dt` ≠ `1.0` draws a *seconds* axis (see
[Plot export](#4-plot-export-pdf-svg-png)).

## At a glance

> **Report the E summary of the CSV as apparent E, not as γ-corrected E.**
> `export_molecule_table_csv` computes it with `tether.fret.efficiency.apparent_fret`, that is,
> `A / (D + A)`. This is `corrected_fret` at the identity factors `alpha=0, gamma=1`. The stored
> α, γ and δ are **not** applied. They are separate columns, so that you can recompute the
> corrected value: `E = (A − α·D) / ((A − α·D) + γ·D)`. The
> [Background](#why-the-e-summary-is-apparent-e) gives the reason.

Four export entry points are public. `tether.project.export` holds the first three. Its `__all__`
also lists `ExportResult` and `write_provenance_sidecar`. `tether.analysis.plot_export` holds the
fourth.

| Entry point | Writes | Use it for |
|---|---|---|
| `tether.project.export.export_molecule_table_csv` | `<name>.csv` and `<name>.csv.provenance.json` | One row for each molecule, for a spreadsheet, R, pandas, Prism or Origin. It gives the inventory of molecules and their apparent-E summary. |
| `tether.project.export.export_deeplasi_txt` | `<name>.txt` and `<name>.txt.provenance.json` | The intensity traces of each frame, in the Deep-LASI `…-donc-accc-w.txt` matrix that a Deep-LASI-compatible tool reads. |
| `tether.project.export.export_subset_tether` | `<name>.tether` (HDF5) and `<name>.tether.provenance.json` | A portable, self-contained re-analysis of a *selection*, without the movie. It holds coordinates, patches, background-subtracted traces and idealization models. |
| `tether.analysis.plot_export.export_figure` | `<stem>.pdf`, `<stem>.svg`, `<stem>.png` and `<stem>.provenance.json`. With `data_csv=True`, also `<stem>.csv` and `<stem>.<name>.csv`. | A publication figure of an analysis plot, as vector PDF, vector SVG and raster PNG, all stamped. The [numbers behind it](#the-numbers-behind-a-figure) are optional. |

The first three also have a command. `tether export table`, `tether export traces` and
`tether export subset` call `export_molecule_table_csv`, `export_deeplasi_txt` and
`export_subset_tether`, with one flag for each parameter ([`tether export`](../cli.md#tether-export)).
The file is the same.

`export_figure` has no command. No module under `src/tether/gui/` references
any of the four functions above. Each snippet on this page is the Python call. The
[how-to page](../how-to/export-results.md) gives the commands first.

The tMAVEN hand-off (`tether.project.handoff.hand_off_to_tmaven`, which the GUI can start) also
writes a file, an SMD-HDF5. It is an *interchange* step with a return path, not an export. It does
**not** write a provenance file.
[Standalone-tMAVEN hand-off](../idealize/standalone-tmaven-handoff.md) documents it.

## What all three project exports share

### Selection

All three take their rows from
[`_selected_rows`](https://github.com/bioedca/tether/blob/main/src/tether/project/export.py) in
`tether.project.export`. It has these behaviors:

| Behavior | Detail |
|---|---|
| Order | **Project order** (ascending row index). A `molecule_keys` request does *not* reorder the output. |
| Curation filter | Rows whose `curation_label` is `CurationLabel.REJECT` (`-1`, `tether.project.labels`) are dropped. `include_rejected=True` keeps them. |
| `molecule_keys` | A membership subselect, not a lookup. `molecule_key` is **not unique**, so one requested key matches *every* project row that carries it. |
| Unknown key | A requested key that matches no project row raises `KeyError`. So a typo fails, and the export never leaves rows out without a message. |
| Empty selection | `export_deeplasi_txt` and `export_subset_tether` raise `ValueError`. |

The `include_rejected` default is **not** the same across the three. The difference is deliberate
(see [Background](#why-the-include_rejected-defaults-differ)).

| Function | `include_rejected` default |
|---|---|
| `export_molecule_table_csv` | `True` |
| `export_deeplasi_txt` | `False` |
| `export_subset_tether` | `False` |

Only `export_deeplasi_txt` and `export_subset_tether` accept `molecule_keys`.
`export_molecule_table_csv` has no such parameter. It always passes `molecule_keys=None`.

### `intensity_quantity`

The two table exports read one `/traces` layer pair. `intensity_quantity` chooses it, through
`tether.project.trace_layers.INTENSITY_QUANTITY_LAYERS`. Exactly two values are legal:

| Value | Layers read |
|---|---|
| `"corrected"` (default) | `donor_corrected` and `acceptor_corrected`, the background-subtracted layers |
| `"raw"` | `donor_raw` and `acceptor_raw` |

Any other value raises `ValueError`.
[`_layers`](https://github.com/bioedca/tether/blob/main/src/tether/project/export.py) builds the
message. For the value `'total'`, it reads:

```text
ValueError: unknown intensity_quantity 'total'; expected one of ['corrected', 'raw']
```

### Return value

All three return a frozen `ExportResult`. It has three fields, in this order:

| Field | Value |
|---|---|
| `path` | The path of the written file. |
| `provenance_path` | The path of its provenance file. |
| `n_molecules` | The count of exported molecules, **after** the curation and selection filters. |

### Intensity units

> **Do not read an exported intensity as photons.** Wherever an export carries an intensity (the
> `.txt` matrix, the `/traces` of a subset), the number is **uncalibrated**. No module in
> `tether.imaging` converts gain, offset or quantum efficiency. What the number *is* depends on how
> the project file was built, and no export records that.

| Project file built by | What an exported intensity is |
|---|---|
| **Native extraction**: `tether.project.extract.extract_movie` → `tether.imaging.extract.write_extraction` | An **integrated camera value in the movie's own pixel units**, in counts or ADU. `tether.imaging.aperture.integrate_traces` integrates it over the aperture below, with Tether's own extraction settings. |
| **Deep-LASI reconstruction**: `tether.project.reconstruct.reconstruct_project` | The **pre-integrated Deep-LASI `.mat` series, carried through without re-integration**. Its units are those of Deep-LASI's own integration (note 1). |
| **Analysis-only import**: `tether.project.analysis_import.import_analysis_only_project` | The **SMD or `.txt` intensity series, carried through without re-integration**, as the background-subtracted (`corrected`) pair (note 2). |

**Notes**

1. [`_traces_from_export`](https://github.com/bioedca/tether/blob/main/src/tether/project/reconstruct.py)
   maps `donc`/`accc` to the background-subtracted (`corrected`) layer, `don`/`acc` to raw and
   `bdon`/`bacc` to background. Its docstring states that "the aperture integration
   `extract_molecules` would run is skipped".
2. [`_write_corrected_traces`](https://github.com/bioedca/tether/blob/main/src/tether/project/analysis_import.py)
   writes the pair. The module calls it the "apparent-E analysis substrate". It synthesizes no `raw`
   or `background` layers, because there is "no movie to decompose the intensities against".

Only the first row is a number that Tether itself measured. For the other two, the units are those
of the upstream tool. The aperture parameters below then describe the *provenance* of the imported
layers, not an integration that Tether performed.

Carried through does not mean bit-preserved. Each reader parses the upstream numbers as `float64`:
`tether.io.deeplasi` for the `.mat` and the `.txt`, and `tether.idealize.read_smd` for the SMD.
Each `/traces` layer is stored as `float32`
([`_TRACE_DTYPE = "<f4"`](https://github.com/bioedca/tether/blob/main/src/tether/imaging/extract.py),
in both
[`tether.imaging.extract._append_padded_2d`](https://github.com/bioedca/tether/blob/main/src/tether/imaging/extract.py)
and
[`tether.project.analysis_import._write_corrected_traces`](https://github.com/bioedca/tether/blob/main/src/tether/project/analysis_import.py)).
So an export differs from a double-precision `.mat` or SMD input by single-precision rounding.

The three layers are not the same number:

| Layer | Value | Sign |
|---|---|---|
| `*_corrected` (`intensity`) | The top-hat: the disk sum minus the scaled ring background, `intensity = tot - bg`. | **Can be negative**, because a background is subtracted. |
| `*_raw` (`total`) | The bare disk sum `tot` over the PSF disk, with nothing subtracted. | Non-negative for a non-negative movie. |
| `*_background` (`background`) | The subtracted term `bg` alone. Only a subset with `include_raw=True` carries it. | Non-negative for a non-negative movie. |

The mapping is the frozen
[`_QUANTITIES`](https://github.com/bioedca/tether/blob/main/src/tether/imaging/extract.py) tuple in
`tether.imaging.extract`. Its annotation there reads *"`raw` = uncorrected `total`, `corrected` =
`intensity`"*. So an `intensity_quantity="raw"` export is **not** the top-hat, and the "can be
negative" caveat does not apply to it.

`bg` itself is not a bare ring mean for each frame. `integrate_traces` computes it in three steps:

1. It runs a uniform temporal moving average over the crop (`bg_window`, default `10` frames,
   `mode="nearest"`).
2. It averages the **positive** in-ring pixels of the smoothed crop (`ring_vals > 0`), as
   Deep-LASI's `mean(bg(bg>0))` does.
3. It scales that mean by the pixel count of the disk, `n_psf = int(disk.sum())`.

> **Read the aperture geometry from the `/settings/extraction` of the project file, not from an
> export.** The geometry is an extraction option, not a property of the export. A subset `.tether`
> carries `/settings/extraction`. **Neither** the `.txt` nor its provenance file carries it.

The aperture options are in `tether.project.extract.ExtractOptions`.
`tether.imaging.aperture.integrate_traces` has the same defaults.

| Option | Unit | Default | Allowed values | Meaning |
|---|---|---|---|---|
| `window` | pixels | `21` | Odd, and `2 * ring_outer <= window` | The side length of the crop box. `tether extract --window` sets it, and it does not change `n_psf`. |
| `disk_radius` | pixels | `3.0` | — | The radius of the PSF disk. It alone sets `n_psf`, and so the scale of each intensity (note 1). |
| `ring_inner` | pixels | `6.0` | — | The inner radius of the background ring. |
| `ring_outer` | pixels | `8.0` | — | The outer radius of the background ring. |
| `bg_window` | frames | `10` | — | The length of the temporal moving average over the crop. |

**Notes**

1. `aperture_masks` builds the disk as `dist <= disk_radius` about the center of the aperture
   window. So at the default the disk is **29 px** for *every* legal `window`. `3.0` gives 29 px,
   `4.0` gives 49 px and `5.0` gives 81 px.

Only `window` has a command-line flag. `disk_radius`, the ring radii and `bg_window` are reachable
only through the Python API: `ExtractOptions`, or `integrate_traces(disk_radius=…)`.
[`tether.imaging.extract._write_settings_once`](https://github.com/bioedca/tether/blob/main/src/tether/imaging/extract.py)
stamps the effective geometry into `/settings/extraction`: `window`, `disk_radius`, `ring_inner`,
`ring_outer`, `bg_window` and `n_psf`.

> **Do not read the `/settings/extraction` of an imported project file as a measurement.** It is
> provenance. `reconstruct_project` gives `write_extraction` the standard constants
> ([`_APERTURE_WINDOW = 21`](https://github.com/bioedca/tether/blob/main/src/tether/project/reconstruct.py),
> [`_APERTURE_DISK_RADIUS = 3.0`](https://github.com/bioedca/tether/blob/main/src/tether/project/reconstruct.py),
> [`_APERTURE_RING_INNER = 6.0`](https://github.com/bioedca/tether/blob/main/src/tether/project/reconstruct.py),
> [`_APERTURE_RING_OUTER = 8.0`](https://github.com/bioedca/tether/blob/main/src/tether/project/reconstruct.py)
> and [`_APERTURE_BG_WINDOW = 10`](https://github.com/bioedca/tether/blob/main/src/tether/project/reconstruct.py)).
> The source says that they describe "the *provenance* of the layers, not a fresh integration
> this module performed".

An analysis-only import writes **no** `/settings/extraction` at all. Its `/settings` children are
`analysis_only` and `correction`. It carries neither `raw` or `background` layers nor `/patches`.
So it can leave only as the CSV or as the background-subtracted `.txt`:

- `intensity_quantity="raw"` raises `KeyError: 'donor_raw'`.
- `export_subset_tether` refuses with `ValueError: source /patches is missing the 'donor' channel;
  cannot export a curatable movie-less subset`.

## 1. Molecule-table CSV

```python
from tether.project.export import export_molecule_table_csv

result = export_molecule_table_csv(
    "experiment.tether",
    "molecules.csv",
    intensity_quantity="corrected",  # or "raw"
    include_rejected=True,           # default: the CSV is a full inventory
)
print(result.path, result.provenance_path, result.n_molecules)
```

**File format.** Tether writes the file with the standard-library `csv.writer`, on a handle opened
as `open("w", newline="", encoding="utf-8")`:

| Property | Value |
|---|---|
| Encoding | UTF-8, no BOM. |
| Delimiter | `,` |
| Quoting | `csv.QUOTE_MINIMAL` with `"`. A cell that contains a comma is quoted, for example a `tags` cell with several tags or a `category` such as `dynamic, folded`. |
| Line terminator | `\r\n` on **every** platform. It is the default of the `csv` module, not the line ending of the OS. |
| Header | Exactly one row, the 25 names below. |
| Rows | One for each exported molecule, in project order. A row is one detected donor spot in one movie, or one imported SMD or `.txt` trace in an analysis-only import. |

**Numbers.** Integer columns go through `int()` and print with no decimal point. Float columns go
through [`_fmt_float`](https://github.com/bioedca/tether/blob/main/src/tether/project/export.py). It
returns `""` for `None` or for any non-finite value. Otherwise it returns `repr(float)`, the
shortest text that reads back to the same number, with no fixed decimal count.

So a real exported cell carries all its significant digits. For example, row 0 of the three-molecule test project of
the `.txt` sample below
([`tests/test_export_tables.py`](https://github.com/bioedca/tether/blob/main/tests/test_export_tables.py))
has a `mean_apparent_e` of `0.3715803781168891`, not `0.372`.

### Molecule-table columns

> **Keep blank cells blank when you read the CSV.** A blank cell means "not finite or not set",
> never zero. [`_fmt_float`](https://github.com/bioedca/tether/blob/main/src/tether/project/export.py)
> maps both `None` and `NaN` to the empty string. A tool that reads blanks as `0` silently turns
> "no γ was ever estimated" into "γ = 0".

> **Do not read `window_start`/`window_end` equal to `frame_start`/`frame_end` as a sign of
> curation.** [`_window`](https://github.com/bioedca/tether/blob/main/src/tether/project/export.py)
> substitutes the frame range whenever `analysis_window` is unset (`hi <= lo`). Extraction also
> *initializes* `analysis_window` to `[0, n_frames]`. So "analysis window equals frame range" tells
> you nothing about whether a curator ever looked at the molecule.

`tether.project.export.MOLECULE_TABLE_COLUMNS` freezes the order. The source annotates the order as
frozen, and it says that a reader may key on these names. The frozen
`tether.io.schema.MOLECULES_DTYPE` gives the types of the underlying fields.
[`tests/test_docs_export_columns.py`](https://github.com/bioedca/tether/blob/main/tests/test_docs_export_columns.py)
fails if this table and that tuple ever disagree.

The same columns are in a file that you can download,
[`molecule-table-columns.csv`](molecule-table-columns.csv). It has one row for each column. The
row gives the name, CSV type and unit, whether it can be blank and a one-line meaning (see
[Put these files in your deposit](#put-these-files-in-your-deposit)).
[`tests/test_docs_published_data.py`](https://github.com/bioedca/tether/blob/main/tests/test_docs_published_data.py)
fails if that file and the tuple differ. It also fails if its type, unit or blank entry for a
column disagrees with this table.

| Column | CSV type | Unit / domain | Blank when |
|---|---|---|---|
| `molecule_id` | string | Unitless identity: a stable UUID for each row, `mol-` and 32 hex characters. Unique within a project file. | The stored string is empty |
| `molecule_key` | string | Unitless identity: the 64-character lowercase hex SHA-256 of `"{movie_sha256}\|{qx}\|{qy}"`, or of other inputs in an analysis-only import (note 1). The cross-file join key, **not unique**. | The stored string is empty |
| `movie_id` | string | Unitless identity of the source `/movies` row, for example `mov-1`. `""` in an analysis-only project, which has no linked movie (note 2). | No movie is linked |
| `source_filename` | string | The filename that `tether.io.filename.parse_filename` parsed the provenance of the row from (note 2). It is the source **movie**, or the imported **SMD or `.txt`** file. | The stored string is empty |
| `condition_id` | string | Unitless condition key, for example `cond-353fd5a76531`. | The stored string is empty |
| `condition_id_provisional` | string | Unitless: the provisional key that extraction parsed from the filename. It stays verbatim across any later re-key. | The stored string is empty |
| `curation_label` | string | **Text, not the stored integer**: `accept`, `uncurated` or `reject` (note 3). An unrecognized integer falls through to its decimal string. | Never: one of the values above is always written |
| `category` | string | Free text, editable for each condition. Tether writes it verbatim, so a comma inside it is CSV-quoted. | The stored string is empty (the extraction default `""`) |
| `quality_class` | float | The read-only ML quality score. The export and the schema do **not** define its numeric range (note 4). | The value is `NaN`, so this column is blank in every export the current build can produce (note 4) |
| `aperture_id` | integer | Unitless index into the aperture registry. Extraction always writes `0`, a **placeholder** that does **not** encode the aperture geometry (note 5). | Never: an integer is always written |
| `alpha` | float | **Dimensionless.** The donor→acceptor leakage α, applied as `I_A,corr = I_A − α·I_D` (`tether.fret.efficiency`). | α is unset. Extraction writes `NaN` until the correction step estimates it. |
| `gamma` | float | **Dimensionless.** The detection-correction factor γ, the denominator weight in `E = I_A,corr / (I_A,corr + γ·I_D,corr)`. | γ is unset (`NaN` at extraction) |
| `delta` | float | **Dimensionless.** Direct-excitation δ, initialized to `0.0` and inert in two-color data without ALEX (ADR-0008), so it exports as `0.0`, not blank. | The stored value is non-finite |
| `correction_method` | string | Vocabulary from `tether.project.correct`: `""` (the extraction default, nothing applied), `corrected`, `manual`, `apparent-E (corrections unavailable)` or `apparent-E (user toggle)`. | Nothing has been applied yet (the `""` default) |
| `correction_confidence` | float | **Unitless provenance flag, not a statistical CI**: `1.0` after a real photophysical correction, `0.0` when the molecule fell back to apparent E (note 6). | No correction pass has run (`NaN` at extraction) |
| `donor_bleach_frame` | integer | **Frames**, zero-based, absolute: `bleach_frames[0]`. `-1` means not detected, and a value equal to `frame_end` means no bleach within the trace (note 7). | Never: an integer is always written |
| `acceptor_bleach_frame` | integer | **Frames**, zero-based, absolute: `bleach_frames[1]`, with the same `-1` and `== frame_end` conventions. | Never |
| `frame_start` | integer | **Frames**, zero-based, **inclusive**: `frame_range[0]`, the start of the valid native extent of the molecule inside the zero-pad of the project file (ADR-0016). Extraction writes `0`. | Never |
| `frame_end` | integer | **Frames**, zero-based, **exclusive** (half-open): `frame_range[1]`. Extraction writes `n_frames`. | Never |
| `window_start` | integer | **Frames**, zero-based, **inclusive**: the lower bound of the *resolved* analysis window that the three summary columns use (note 8). | Never |
| `window_end` | integer | **Frames**, zero-based, **exclusive**: the upper bound of the resolved analysis window, with the same fallback (note 8). | Never |
| `tags` | string | Free text, **comma-joined** in the project file (`tether.analysis.query` splits on `,`). Tether writes it verbatim, so a cell with several tags is CSV-quoted (note 2). | The molecule has no tags |
| `n_finite_frames` | integer | **Frames**: how many frames in the resolved analysis window have a finite apparent E. `0` when every frame in the analysis window has `D + A == 0` exactly. | Never |
| `mean_apparent_e` | float | **Dimensionless — apparent E** (the proximity ratio `A/(D+A)`), the arithmetic mean over the finite frames of the resolved analysis window. **Not** γ-corrected and **not** clipped to `[0, 1]` (note 9). | `n_finite_frames == 0` |
| `median_apparent_e` | float | **Dimensionless — apparent E**: the `numpy.median` of the same finite values. The same no-clipping rule applies. | `n_finite_frames == 0` |

**Notes**

1. [Identity columns by kind of project file](#identity-columns-by-kind-of-project-file) gives
   both derivations. For a project file built from a movie, `tether.imaging.extract.molecule_key`
   computes the key with `donor_xy` quantized.
2. The value depends on how the project file was built. See
   [Identity columns by kind of project file](#identity-columns-by-kind-of-project-file).
3. [`_CURATION_TEXT`](https://github.com/bioedca/tether/blob/main/src/tether/project/export.py) maps
   the `tether.project.labels.CurationLabel` values `1`, `0` and `-1` to the three words.
4. The schema calls the score only a "read-only ML output". Extraction and analysis-only import
   both initialize it to `NaN` (`tether.imaging.extract`, `tether.project.analysis_import`). No
   shipped code path writes anything else.
5. There is no registry for each aperture yet. `tether extract --window` is settable and still
   writes `0`. The effective geometry is in `/settings/extraction`, which the CSV does not carry
   (see [Intensity units](#intensity-units)).
6. [`_CONFIDENCE_CORRECTED`](https://github.com/bioedca/tether/blob/main/src/tether/project/correct.py)
   and [`_CONFIDENCE_APPARENT`](https://github.com/bioedca/tether/blob/main/src/tether/project/correct.py)
   in `tether.project.correct` hold the two values.
7. `-1` is [`_UNDETECTED_FRAME`](https://github.com/bioedca/tether/blob/main/src/tether/imaging/extract.py)
   in `tether.imaging.extract`. A value equal to `frame_end` means that the channel does not bleach
   within the trace (`tether.project.photobleach`).
8. The resolved analysis window is `analysis_window`, or `frame_range` when the analysis window is
   unset (`hi <= lo`), per
   [`_window`](https://github.com/bioedca/tether/blob/main/src/tether/project/export.py). It is the
   window of `n_finite_frames`, `mean_apparent_e` and `median_apparent_e`. These two columns exist
   so that the reported analysis window always matches the range that those three columns cover.
9. `apparent_fret` deliberately leaves a noisy out-of-range value alone.

**How the E summary is computed.** For each row, Tether calls `apparent_fret(donor[i, lo:hi],
acceptor[i, lo:hi])` over the resolved analysis window `[lo, hi)`, on the layer that
`intensity_quantity` chooses. Frames where `D + A == 0` exactly are `NaN`. The mean, the median and
`n_finite_frames` exclude them.

### Identity columns by kind of project file

> **Do not expect the `molecule_key` of an analysis-only import to match a movie-linked project
> file.** Nothing in it is derived from a movie. You cannot re-derive it from a movie hash or from
> coordinates. It does not match the key that the same molecule carries in a movie-linked project
> file.

Three kinds of project file write the columns above. A native extraction and a Deep-LASI
reconstruction (`tether.project.reconstruct`, which calls the same
`tether.imaging.extract.write_extraction`) both link a `/movies` row.

| Project file built by | `movie_id` | `molecule_key` | `source_filename` |
|---|---|---|---|
| Native extraction | The id of the linked `/movies` row | The SHA-256 of the movie `sha256` and the quantized `donor_xy` | The name of the movie |
| Deep-LASI reconstruction | The id of the linked `/movies` row | The SHA-256 of the movie `sha256` and the quantized `donor_xy` | The name of the movie |
| Analysis-only import | `""` | The SHA-256 of other inputs (note 1) | The name of the SMD or `.txt` file |

**Notes**

1. [`_analysis_only_molecule_key`](https://github.com/bioedca/tether/blob/main/src/tether/project/analysis_import.py)
   hashes these inputs:
    - the constant `"tether-analysis-only"`,
    - the source id,
    - the **row index** of the molecule,
    - the raw donor and acceptor **trace bytes** of that molecule (little-endian `float64`).

    The source calls it "an identity, not fabricated coordinate data". It is unique for each row
    and stable across a re-import of the same source.

The analysis-only import is
`tether.project.analysis_import.import_analysis_only_project`, the SMD and `.txt` branch without
coordinates. It also writes `donor_xy` and `acceptor_xy` as `NaN`, and `tags` as
`round-trip-unavailable` (`ANALYSIS_ONLY_TAG`). A blank `movie_id` is the flag that marks such a
row. `tether.project.analysis_import.read_analysis_only_marker` is the O(1) check on the project
file itself.

## 2. Deep-LASI trace matrix (`.txt`)

```python
from tether.project.export import export_deeplasi_txt

result = export_deeplasi_txt(
    "experiment.tether",
    "traces-donc-accc-w.txt",
    molecule_keys=None,              # or a list of molecule_key strings
    intensity_quantity="corrected",
    include_rejected=False,
)
```

`tether.io.deeplasi.write_deeplasi_txt` writes the file. Its read-side mirror,
`read_deeplasi_txt`, splits the interleaved columns of the same file back into two `(N, T)` arrays.

> **Read the first frame of the matrix from the provenance file.** Row 0 of the file is absolute
> frame **`lo`**, which need not be 0. Only the provenance file records `lo`, in `frame_range`.
> This is the one place where a row index is not an absolute frame.

| Property | Value |
|---|---|
| Header | **None**: no header line, no index column and no molecule ids. The file must stay faithful to Deep-LASI. |
| Shape | `T` rows × `2N` columns: one row for each **frame**, and one column *pair* for each **molecule**. |
| Column order | **Interleaved, donor first**: `donor₀ acceptor₀ donor₁ acceptor₁ …` (`interleaved[:, 0::2] = donor.T`). |
| Number format | `numpy.savetxt(..., fmt="%.5f")`: fixed-point, exactly 5 decimals ([`_TXT_DECIMALS = 5`](https://github.com/bioedca/tether/blob/main/src/tether/io/deeplasi.py)). A round trip therefore loses what that rounding removes. |
| Delimiter | A single space. |
| Line ending | Translated by the OS: CRLF on Windows and LF elsewhere (note 1). Unlike the CSV, it is *not* fixed. |
| Values | Uncalibrated intensities, never photons and **not necessarily movie pixel units** (note 2). They are signed, so background-subtracted values can be negative. |

**Notes**

1. `savetxt` opens the file in text mode, and Tether passes no `newline=`.
2. What the number is depends on how the *source project file* was built. It is integrated camera
   counts for a native extraction. It is the pre-integrated series of the upstream tool, not
   re-integrated, for a Deep-LASI reconstruction or an analysis-only import. See
   [Intensity units](#intensity-units).

A real first line, from a three-molecule project file:

```text
100.00000 53.00000 200.00000 56.00000 300.00000 59.00000
```

**Frame trimming.** The `/traces` arrays of a project file are zero-padded to the largest frame
count of the experiment, as movies of different length are added (ADR-0016). So
`export_deeplasi_txt` trims to the shared `frame_range` of the selection. The written matrix is
`donor_all[rows][:, lo:hi]`. A selection that spans more than one `frame_range` has no honest
common frame axis. It raises `ValueError`, and Tether does not pad or truncate it.

The `parameters` of the provenance file for this export:

| Parameter | Value |
|---|---|
| `intensity_quantity` | The layer pair that was read. |
| `include_rejected` | The value passed. |
| `n_molecules` | The exported count. |
| `n_frames` | `hi - lo`. |
| `frame_range` | `[lo, hi]`, half-open. |
| `molecule_keys` | `null`, or a list. |

## 3. Subset `.tether`

```python
from tether.project.export import export_subset_tether

result = export_subset_tether(
    "experiment.tether",
    "subset.tether",
    molecule_keys=None,        # or a list of molecule_key strings
    include_rejected=False,
    include_raw=False,
    overwrite=False,
    curator_alias=None,        # or a prefix such as "curator"; see below
)
```

The output is a new, self-contained HDF5 project. `tether.io.schema.create_project` builds it, and
Tether then adds additive data only. It adds no structure to the frozen skeleton (ADR-0005).

Tether writes it **atomically**. It stages the file to a `tempfile.mkstemp` sibling in the
destination folder, and it moves the file into place with `os.replace`. On any failure it deletes
the staged file.
[`_preflight_source`](https://github.com/bioedca/tether/blob/main/src/tether/project/export.py)
validates the source **before** anything is written. The layers must be present, the row counts
must agree and both patch channels must be present.

**What travels:**

| Group | Content |
|---|---|
| `/molecules` | The selected rows only. |
| `/traces` | Always `donor_corrected` and `acceptor_corrected`. `donor_raw`, `acceptor_raw`, `donor_background` and `acceptor_background` only when `include_raw=True`. |
| `/patches` | Every channel (`donor`, `acceptor`), the selected rows only, with the aperture window unchanged. |
| `/idealization/{model}` | Each model, filtered on **`molecule_id`**, the unique UUID, not the non-unique `molecule_key` (note 1). The `n_molecules` attribute of the group is rewritten to the kept count. |
| `/labels` | Rows whose `molecule_key` is in the selection. |
| `/labels/reject_log` | Rows whose `molecule_key` is in the selection, so each reject keeps its reason. Absent when the source has no log or no selected molecule has a row. |
| `/conditions`, `/settings`, `/calibration`, `/models` | Copied verbatim. |

**Notes**

1. Tether takes the selected rows of the row-aligned members `idealized`, `state_path`,
   `molecule_key`, `molecule_id` and `input_hash`. Every other member is a global consensus array,
   and Tether copies it verbatim: `mean`, `var`, `frac`, `tmatrix`, `norm_tmatrix`, `rates`, `pi`
   and the `priors` group.

**What does not travel:**

| Dropped | Note |
|---|---|
| `/movies` **rows** | The subset is movie-less by definition. The group exists, because the skeleton is frozen, but it has **zero rows** (note 1). |
| `/features` | Present but empty. [`_SUBSET_VERBATIM_GROUPS`](https://github.com/bioedca/tether/blob/main/src/tether/project/export.py) deliberately leaves out the ML features of each molecule. |
| Raw **and** background traces, when `include_raw=False` | The four layers travel together or not at all (note 2). |
| Idealization models whose input layers are absent, or none of whose molecules were selected | Listed in `skipped_idealization_models` of the provenance file. A model fitted on `raw` is skipped when `include_raw=False`. |

**Notes**

1. Nothing that the movie row held comes with it: `uri`, `sha256`, `file_size`, `mtime`,
   `n_frames`, `height`, `width`, `pixel_dtype`, `frame_time` and `head_tail_hash`. The crop,
   rotation and flip of each channel and the `calibration_id` cell of the row stay behind too
   (`tether.io.schema.MOVIES_DTYPE`). **The calibration itself does travel.** `/calibration` is in
   [`_SUBSET_VERBATIM_GROUPS`](https://github.com/bioedca/tether/blob/main/src/tether/project/export.py),
   and Tether copies it whole. So `/calibration/<calibration_id>` keeps the id, which *is* the group
   name. It also keeps the two polynomial transforms and the registration geometry attributes.
2. `corrected = raw − background` exactly. So background alone would make raw reconstructable.
   A subset that leaves out raw must leave out background too.

### User names (`curator_alias`)

> **Read `source_filename` and `tether_subset_of` before you share the copy.** `curator_alias`
> does **not** remove them. They are file names, not paths, and they are the origin record of each
> molecule. A file name can identify a sample.

A project records a user name in these fields. By default each user name is the login of the
operating system.

| Row | Field that holds the name |
|---|---|
| `/labels/table` | `labeler` |
| `/labels/reject_log` | `labeler` |
| `/settings/condition_audit` (a condition re-key or merge) | `labeler` |
| `/settings/lock_takeovers` (a lock that was recovered or taken over) | `holder_user` and `ousted_user` |
| `/settings/jobs` | The `user` key of its `owner` object: the name of the last writer. |
| `/settings/correction_transitions`, `/settings/gamma_contributors` and `/settings/gamma_contributor_decisions` | `actor`, when `by` is `person`. The `actor` of a `policy` row contains policy text and keeps its value. |

`tether.project.export.USER_NAME_FIELDS` lists the plain fields.
`tether.project.export.JSON_USER_KEYS` lists the JSON fields that hold a name under one key. Only
that key changes, so the record keeps its shape.

| `curator_alias` | Result |
|---|---|
| `None` (default) | The names are copied as they are. The output is the same as before the option existed. |
| A prefix, for example `"curator"` | Each distinct name becomes `curator-1`, `curator-2`, … (note 1). The source project does not change. |

**Notes**

1. The numbers follow the order in which the copy first writes each name. That is the selected
   `/labels/table` and `/labels/reject_log` rows in the order of the project file, followed by the
   `/settings` logs.
    - One person keeps one alias in every table, so a multi-curator analysis still works on the
      copy.
    - An alias that contains a name in the source is skipped. So a copy whose names are already
      `curator-1`, … never gets `curator-10`.
    - A prefix that contains a name in the source raises `ValueError`.
    - An empty name stays empty.

A path can name the user too, for example a model file in a home folder. With the option, each path
in `tether.project.export.PATH_ATTRS` keeps only its file name. Today that is the `artifact_path` of
each `/models/<condition_id>` reference. So the reference in the copy names the model file but not
its folder.

Tether writes each table that holds names directly from its aliased rows, so those fields store
only their new values. The count covers the names in the records selected for export. It excludes a
name whose only labels stay behind, for example on a rejected molecule. A blank prefix raises
`ValueError`.

The option copies these as they are:

- free text, such as an audit `reason`, the `detail` of a correction transition and the text of a
  correction policy,
- the computer names `holder_host` and `ousted_host` of a lock takeover row, and the `host` of a job
  owner,
- the `request` and `error` text of a job row, which can hold a full file path.

### Embedded provenance

Besides the provenance file, the subset stamps four root attributes, which `h5py` can read. It
stamps two more with `curator_alias`.

| Attribute | Value |
|---|---|
| `tether_subset_of` | The **source filename only** (`path.name`), never a full path. |
| `tether_subset_created_utc` | Offset-aware ISO-8601 UTC. It is byte-identical to the `created_utc` of the provenance file. |
| `tether_subset_include_raw` | `0` or `1`, an integer and not a bool. |
| `tether_subset_n_molecules` | The exported count. |
| `tether_subset_names_aliased` | `1`, only with `curator_alias`. |
| `tether_subset_n_aliased_names` | The count of distinct names replaced, only with `curator_alias`. No name is recorded. |

The root also carries the `format="tether-project"`, `schema_version` and `app_version` of
`create_project`. `app_version` is the version of the build that performed the *export*, not that
of the source.

### Refusals

| Error | When |
|---|---|
| `FileExistsError` | `out_path` exists and `overwrite=False`. |
| `ValueError` | The output resolves to the source, or it is a hard link of the source. |
| `ValueError` | The selection is empty. |
| `ValueError` | The preflight fails. |
| `ValueError` | `curator_alias` is blank or contains a user name stored in the source. |
| `ValueError` | `curator_alias` leaves no alias that is free of every source user name. |

The `parameters` of the provenance file for this export are `include_raw`, `include_rejected`,
`molecule_keys`, `n_molecules`, `trace_layers`, `n_trace_layers`, `idealization_models`,
`n_idealization_models`, `skipped_idealization_models`, `n_label_rows` and `n_reject_log_rows`.
With `curator_alias` they also include `curator_alias` (the prefix), `names_aliased` (`true`) and
`n_aliased_names`. They never record the names.

## 4. Plot export (PDF + SVG + PNG)

`tether.analysis.plot_export` keeps rendering apart from writing. Each `render_*` helper returns a
bare `matplotlib.figure.Figure` and writes nothing. `export_figure` is the only function that writes
to disk. The figures use the object-oriented `Figure` API, not `pyplot`. So no GUI backend is
selected, and the base environment stays `matplotlib-base` (ADR-0044).

`tether.analysis` re-exports the module and its public names **lazily**. A module-level
`__getattr__` ([PEP 562](https://peps.python.org/pep-0562/)) imports `plot_export` on the first
access to one of them. So `from tether.analysis import export_figure` works, and
`import tether.analysis` alone still does not import Matplotlib. The names are not in
`tether.analysis.__all__`, so a star import does not import it either.

```python
from tether.analysis.histogram import population_apparent_e_histogram
from tether.analysis.plot_export import export_figure, render_histogram1d

hist = population_apparent_e_histogram("experiment.tether", bins=151)
figure = render_histogram1d(hist)
result = export_figure(
    figure,
    "figures/a1-histogram",      # a stem: no extension
    tether_export="plot:a1",     # free-form kind string, recorded in the stamp
    source="experiment.tether",
    parameters={"bins": 151},    # whatever you want stamped
)
print(result.paths)              # {'pdf': ..., 'svg': ..., 'png': ...}
```

> **Render the figure again before you export it again.** `export_figure` mutates the figure that
> you pass: it draws the footer onto the `Figure` object. So two exports of the same figure with
> `stamp=True` leave two overlapping footers with two different timestamps.

| Property | Value |
|---|---|
| Formats | `DEFAULT_PLOT_FORMATS = ("pdf", "svg", "png")`, all three by default. `formats=` can select a subset (note 1). |
| Write order | Always the canonical `pdf, svg, png`, whatever order you pass. The loop iterates `DEFAULT_PLOT_FORMATS` and skips. |
| Filenames | The extension is **appended** to `out_stem`, never substituted: a stem `my.plot.v2` gives `my.plot.v2.pdf`. Nothing comes from the plot kind, the title or the timestamp. |
| Figure size | `DEFAULT_FIGSIZE = (6.4, 4.8)` **inches**. |
| Raster resolution | `DEFAULT_EXPORT_DPI = 200`, PNG only: a 1280 × 960 px PNG at the default figure size. The vector formats are resolution-independent and ignore it. |
| Background | The opaque white of Matplotlib. Tether never passes `transparent=`. |
| Determinism | A **freshly re-rendered** figure, exported again at the same pinned `created_utc`, is byte-identical in all three formats (note 2). The *same* `Figure` object is **not**, because `stamp=True` draws another footer onto it. |
| Return value | `PlotExportResult(stem, paths, provenance_path, formats, data_paths)`. `data_paths` is empty unless `data_csv=True`. |

**Notes**

1. Any other value raises `ValueError`. An empty tuple raises
   `ValueError("at least one export format is required")`.
2. `savefig` runs inside `rc_context({"svg.hashsalt": _SVG_HASHSALT})`, so the SVG element ids are
   stable across processes.

Tether stamps a plot export in **three** ways:

1. **The provenance file** `<stem>.provenance.json`, written by the same
   `write_provenance_sidecar`. Tether writes it even when `stamp=False`. `export_figure` merges
   these keys of its own into your `parameters`:
    - `formats`, in canonical order,
    - `dpi`,
    - `outputs`, as `{format: basename}` with base names and not paths,
    - `data_tables`, with `data_csv=True` ([below](#the-numbers-behind-a-figure)),
    - `series`, for a figure drawn from several series (below),
    - `display`, for a figure that records how it was drawn (below).
2. **A visible footer on the figure**, when `stamp=True` (the default). It is two 6-point texts at
   55 % opacity at the bottom of the figure. `Tether <version> · <kind>` is on the left, and the ISO
   timestamp is on the right.
3. **Embedded document metadata**, for each backend:
    - PDF: `Title`, `Author`, `Subject`, `Creator` and `CreationDate`.
    - SVG: the Dublin Core `Title`, `Description` and `Date`.
    - PNG: the `tEXt` chunks `Software`, `Title`, `Description` and `Creation Time`.

    The `title=` argument fills only the document `Title`, and it defaults to the `tether_export`
    kind. It does not change the title on the figure or the filename.

> **Pass a frame duration if you want a time axis in seconds.** `dt` and `time_dt` default to
> `1.0`, and at that default the renderer labels the axis in frames. The same applies to the rates
> of a B2 survival fit. `tether.analysis.dwell` reports them in `1 / (frames · dt)`, so they are
> **per frame** at the default.

The fourteen renderers follow, with the plot that each produces. The plot ids are those of the
[seven-plot parity gallery](../analysis/parity-gallery.md).

| Renderer | Input dataclass | Plot | x-axis unit |
|---|---|---|---|
| `render_histogram1d` | `Histogram1D` or `HistogramBootstrapCI` (+ optional `ModelGaussianOverlay`) | A1 population histogram | Apparent FRET efficiency *E* (dimensionless) |
| `render_histogram2d` | `Histogram2D` | A2 time-vs-signal heatmap | `Time (s)` when `time_dt != 1.0`, otherwise `Frame` |
| `render_transition_density` | `TransitionDensityPlot` or `SmoothedTransitionDensity` | B1 transition-density plot | Initial *E* (dimensionless) |
| `render_dwell_survival` | `DwellTimeAnalysis` | B2 survival curve (+ residual panel when a fit succeeded) | `Dwell time (s)` when `dt != 1.0`, otherwise `Dwell time (frames)` |
| `render_transition_prob` | `TransitionProbHistogram` | B3 transition-probability histogram | Probability per molecule (dimensionless) |
| `render_state_number` | `StateNumberCounts` | C1 state-number distribution | Number of occupied states (a count) |
| `render_raw_fret_cloud` | `RawFretCloud` | QC raw FRET cloud | `Time (s)` when `time_dt != 1.0`, otherwise `Frame` |
| `render_cross_correlation` | `CrossCorrelation` | Donor–acceptor cross-correlation | `Lag (frames)`, always frames. There is no seconds variant. |
| `render_per_condition_histograms` | `PerConditionHistograms` | Per-condition overlay: the A1 histogram of each condition on one shared axis | Apparent FRET efficiency *E* (dimensionless) |
| `render_transition_sync_histogram2d` | `TransitionSyncHistogram2D` | A2b post-synchronized heatmap | `Time from transition (s)` when `time_dt != 1.0`, otherwise `Frames from transition` |
| `render_alpha_shape` | `AlphaShape` (+ optional `RawFretCloud`) | Alpha-shape support boundary of the raw FRET cloud | `Time (s)` when the frame duration is not `1.0`, otherwise `Frame` |
| `render_state_number_elbow` | `StateNumberElbow` | k-vs-RMSE elbow state-count hint | Number of clusters *k* (a count) |
| `render_population_anticorrelation` | `PopulationAnticorrelation` | Anticorrelation events, one row per molecule | `Frame (analysis window)`, always frames |
| `render_successive_frame_cloud` | `SuccessiveFrameCloud` | Successive-frame FRET cloud: *E* at frame *i* against *E* at frame *i* + 1 | *E* at frame *i* (dimensionless). The y-axis is *E* at frame *i* + 1. |

### What each renderer draws

`render_transition_density` draws the B1 color scale as `color_scale="log"` by default. This is the
log-normalized TDP that the product specification names. Use `color_scale="linear"` for a linear
scale. The log scale is zero-safe: an empty bin gets the floor color of the color map. The floor is
one decade below the smallest non-empty bin, so a rare transition does not disappear next to a
frequent one. The scale is display-only: the drawn array is the stored histogram.

`export_figure` writes the scale to the provenance file as `parameters.display.color_scale`. You do
not have to pass it. If you pass a `display` entry that does not agree with the figure,
`export_figure` raises `ValueError`.

To draw the TDP as published figures show it, pass the smoothed view instead of the counts:
`render_transition_density(smooth_transition_density(tdp))`. The view is the counts
Gaussian-filtered, interpolated onto an 800 × 800 grid and normalized to their maximum. It uses the
settings of the `checkTDP.py` published by Campos-Chavez et al. 2024 (see the
[parameter reference](parameters.md)). It is drawn as an image on that grid.

On the log scale the floor of the smoothed view is 10⁻⁴ of the peak, the color floor of
`checkTDP.py`. `export_figure` writes the smoothing settings to the provenance file as
`parameters.display.smoothing`. The counts are not changed.

`render_per_condition_histograms` draws every condition as an unfilled outline on the one shared bin
grid of the overlay, in the order of the overlay. It labels each curve with its condition id and
molecule count. A condition binned on another grid raises `ValueError`, so it is never drawn with
its bins in the wrong place.

`render_transition_sync_histogram2d` draws time relative to the selected transition. The axis runs
negative before it, and a dotted line marks relative time zero. The note names the selected states,
with `any` for `-1`.

`render_alpha_shape` draws the boundary segments and nothing else of the shape. When there is no
boundary, it draws no line, and the note tells you why. The cloud spans no 2-D region, or the alpha
was too small to keep a triangle. Pass `cloud=` to draw the points underneath and to take the axis
limits and the time unit from the cloud. Without a cloud, pass `time_dt=` for a time axis in
seconds.

`render_state_number_elbow` draws the RMSE curve, the chord from its first to its last point, and
the elbow when there is one. The elbow is a hint, and the figure says so: the state count of record
is the model's.

`render_population_anticorrelation` gives each scanned molecule one row, in the order of the project
file. An event is a bar over its frames, colored by its peak `|lag-0|`, with a tick at its peak
frame. A molecule with no event keeps its empty row. The rows carry the `molecule_key` up to 30
molecules and an index above that.

`render_successive_frame_cloud` draws five things. They are the density surface, the
highest-density-region contours and the pairs as points. They are also the k-means centroids as
crosses and the diagonal *E*(*i* + 1) = *E*(*i*) as a dashed line. It is not the raw FRET cloud, which draws *E*
against time.

It thins its points to `max_points` (default 20 000), as the raw cloud does. The density and the
centroids come from every pair. Each series record in the provenance file holds the cloud's own
analysis provenance under `analysis`. That record gives every parameter and the `molecule_key` of
each molecule in the cloud. A bare cloud writes it under `parameters.analysis`. Its `data` rows lead
back to their molecules too.

`render_raw_fret_cloud` thins its scatter overlay when the population exceeds `max_points` (default
20 000), to keep the vector file manageable. The thinning does not touch the density surface
underneath. But the density is not fit on every point either. `raw_fret_cloud` keeps all finite
samples in `points`. It fits the KDE, and the HDR levels derived from it, only on the samples inside
`time_range` × `signal_range`. So apparent-E outliers that are not clipped cannot inflate the
bandwidth.

`n_out_of_range` is the excluded count. The annotation on the figure reports only `n_samples` and
`n_molecules`, so read `n_out_of_range` from the dataclass.

### Several series on one figure

Every renderer also takes an ordered sequence of `(label, result)` pairs. These are the series of
one composed plot. Each label must be a distinct, non-empty string. A sequence of one series draws
exactly the figure of the bare result, byte for byte. Its label goes only to the provenance file.

```python
from tether.analysis.histogram import population_apparent_e_histogram
from tether.analysis.plot_export import export_figure, render_histogram1d

wild_type = population_apparent_e_histogram("wt.tether", bins=151)
mutant = population_apparent_e_histogram("mutant.tether", bins=151)
figure = render_histogram1d([("WT", wild_type), ("mutant", mutant)])
export_figure(
    figure,
    "figures/a1-wt-vs-mutant",
    tether_export="plot:a1",
    source="wt.tether, mutant.tether",
    parameters={"series": [{"selection": "WT v1"}, {"selection": "mutant v1"}]},
)
```

- **Overlay.** Seven renderers draw all series on one axis, with a legend entry for each. They are
  the 1-D histogram, the dwell survival, the transition probability, the state-number chart (as
  grouped bars), the cross-correlation, the alpha shape and the elbow.
    - The series take their colors from one cycle, `SERIES_COLOR_CYCLE = "okabe-ito-4"`. It has four
      colors of the Okabe–Ito set: blue, vermilion, bluish green and reddish purple.
    - After them come the same four with the next line style (`--`, `-.`, `:`), so 16 series
      differ.
    - A series that can be drawn as points alone has a marker (`o`, `s`, `^`, `D`). The marker
      steps with the line style, as the dwell survival markers do. `series_style(k)` returns the style of series
      `k`.
    - Each color reaches 3 : 1 against the white background. Every two colors also stay apart under
      simulated protanopia, deuteranopia and tritanopia, the check that the
      [trace colors](gui-theme.md) pass.
- **Small multiples.** Where one series would hide another, each series gets its own panel, titled
  with its label. This applies to the time-vs-signal heatmap, the transition-density plot, the raw
  FRET cloud, the transition-synchronized heatmap, the per-condition overlay and the anticorrelation
  raster.
    - The panels share their axes. The anticorrelation panels share only the frame axis. Each has
      its own molecule rows, so one row position is not one molecule across panels.
    - The heatmaps and the raster share one color scale and one color bar, so a color means the same
      value in every panel.
    - In the per-condition panels, a condition keeps its color in every panel.
- **One grid.** Two series that are not on one grid are refused with a `ValueError`. It names the
  series, what differs and both values. Tether never resamples one series onto another.
    - What must agree depends on the plot. Examples are the bin edges, value range and density
      normalization of a histogram, and the frame time of anything with a time axis. Others are the
      `init → final` pair of a transition-probability histogram and the lags of a cross-correlation.
    - A lever of the series, such as per-molecule equal weight, can differ.

`export_figure` writes one record for each series to `parameters.series`, in figure order. A record
holds the `label`, the `result` type, the `parameters` that the result carries and its `display`.
The parameters are the scalars, ranges and grid of the result, never its data. The display is the
color and line style, or the panel. A one-series figure is the figure of the bare result, so its
record says `{"as_bare_result": true}`. The layout goes to `parameters.display` (`series_layout`,
and `series_cycle` or `series_color_scale`).

A model drawn with a series keeps its own parameters in the record. Examples are the Gaussian means,
variances and weights, or the fitted rates. Its sampled curve is data and is left out. A series drawn
without its model overlay or cloud records `null` for it.

A parameter that is NaN or infinite is recorded
as `null`, so the file stays strict JSON. An example is the `alpha` of an empty alpha shape. So is
one in a key that you add.

You can add your own keys to a series, such as the selection and its version. Pass
`parameters["series"]` as a list of one dict for each series. A key that contradicts what was drawn raises `ValueError`.
ADR-0132 records these choices.

### The numbers behind a figure

Pass `data_csv=True` to `export_figure` to write the numbers that the figure shows beside it. Use
them to replot in your own style, to compare in another program, or as the source data that a
journal asks for. The figure files are the same bytes with or without the option.

```python
result = export_figure(
    figure,
    "figures/a1-histogram",
    tether_export="plot:a1",
    source="experiment.tether",
    parameters={"bins": 151, "selection": "accepted v2"},
    data_csv=True,
)
print(result.data_paths)         # {'data': PosixPath('figures/a1-histogram.csv')}
```

Each `render_*` helper records on the figure the result that it drew. The tables come from that
result, so they are the values plotted and not a second computation. The record is a copy made when
the figure is drawn. A result that you change later does not change the tables. A figure that you
drew yourself carries no such record, and `data_csv=True` raises `ValueError` before it writes any
file. `tether.analysis.plot_data` builds and writes the tables.

**Files.** The marks of the figure (its bars, cells, points or segments) are the main table,
`<stem>.csv`. Other parts of a figure have a grid of their own. Examples are a curve drawn over the
marks, the parameters of a fit, and a density surface with its contour levels. Each of them is a table of its own,
`<stem>.<name>.csv`.

`result.data_paths` maps the name of each table to its file. The provenance
file lists them under `parameters.data_tables` (`{name: basename}`).

**Layout.** Every table is in long form: one row for each bin, cell, point or segment. A 2-D result
gives one row for each cell, `x_low, x_high, y_low, y_high, value`, with `x` varying slowest.

A figure of several series gives one table, in figure order. Its first column, `series`, holds the
label of the series of each row. A figure of a bare result has no `series` column. Sometimes one
series has a column that another lacks, such as the bootstrap interval of one histogram among plain
ones. That column is blank in the rows of the series without it.

**Header.** Every file starts with `#` comment lines, so that a file found alone can be traced:

```text
# tether_version: <the Tether version>
# plot: plot:a1
# provenance: a1-histogram.provenance.json
# created_utc: 2026-11-19T17:23:42.050896+00:00
# source: experiment.tether
# selection: "accepted v2"
# table: data
# units: bin_left = dimensionless (apparent FRET efficiency E); bin_right = …; value = density
bin_left,bin_right,value
```

`selection` is the `selection` key of your `parameters`, as JSON (`null` when you gave none). A
figure of several series adds one `# series N:` line for each series. The line is a JSON object with
the series `label` and the `selection` that you gave in `parameters["series"]`.

`units` names the unit of each column. A time axis is in seconds when the result has a frame time
other than `1.0`, and in frames otherwise, as on the figure. Read a table with
`pandas.read_csv(path, comment="#")`. Add `float_precision="round_trip"` to get back the exact
values written.

**Cells.** A float is written by Python `repr`, the shortest text that reads back to the same
number. So a table read back equals the result exactly. NaN and infinity are blank cells, as in the
molecule-table CSV. Every text cell is quoted, so a `#` in a series label is never read as the start
of a comment.

The values are the stored values. The figure clips the survival markers at 10⁻⁶ for its log axis,
and it can thin the cloud points to `max_points`. The tables hold them in full.

The tables of each plot, and what one row of each holds:

| Renderer | Table | Written when | One row for each |
|---|---|---|---|
| `render_histogram1d` | `data` | Always | Bin |
| | `model` | With a model overlay | Point of each curve |
| `render_histogram2d` | `data` | Always | Cell |
| `render_transition_density` | `data` | Always | Cell of the counts, or point of the grid of the smoothed view (800 × 800 by default) |
| `render_dwell_survival` | `data` | Always | Point of the survival curve |
| | `model` | With a successful fit | Point of each curve |
| | `fit` | With a successful fit | Parameter of the fit |
| `render_transition_prob` | `data` | Always | Bin |
| | `model` | With a KDE | Point of the curve |
| `render_state_number` | `data` | Always | Number of occupied states |
| `render_raw_fret_cloud` | `data` | Always | Point of the cloud |
| | `density` | With a density surface | Cell |
| | `levels` | With HDR contours | Contour |
| `render_cross_correlation` | `data` | Always | Lag |
| `render_per_condition_histograms` | `data` | Always | Bin of each condition |
| `render_transition_sync_histogram2d` | `data` | Always | Cell |
| `render_alpha_shape` | `data` | Always | Boundary segment. An empty shape has no rows. |
| | `points` | With `cloud=` | Point of the cloud, as the `data` table of `render_raw_fret_cloud` |
| `render_state_number_elbow` | `data` | Always | Value of *k* |
| `render_successive_frame_cloud` | `data` | Always | Pair |
| | `density` | With a density surface | Grid node |
| | `levels` | With HDR contours | Contour |
| | `centroids` | With centroids | Centroid |
| `render_population_anticorrelation` | `data` | Always | Event. A molecule with no event has no rows. |

The columns of each table. *E* is the apparent FRET efficiency. "Seconds or frames" follows the
frame time of the result, as the `units` line says.

| Renderer | Table | Column | Unit | Meaning |
|---|---|---|---|---|
| `render_histogram1d` | `data` | `bin_left`, `bin_right` | dimensionless | The bin edges, in *E*. |
| | | `value` | counts or density | Counts, or density when the histogram is normalized. |
| | | `ci_low`, `ci_high` | the unit of `value` | The bootstrap interval, for a `HistogramBootstrapCI` only. |
| | `model` | `curve` | — | `total`, or `state 1`, `state 2`, … for the Gaussian of each state. A figure of several series draws, and writes, the total only. |
| | | `x` | dimensionless | *E*. |
| | | `value` | the unit of the histogram's `value` | The height of the curve. |
| `render_histogram2d` | `data` | `x_low`, `x_high` | seconds or frames | The time edges of the cell. |
| | | `y_low`, `y_high` | dimensionless | The *E* edges of the cell. |
| | | `value` | counts or density | The counts or density of the cell. |
| `render_transition_density` | `data` (counts) | `x_low`, `x_high` | dimensionless | The initial *E* edges of the cell. |
| | | `y_low`, `y_high` | dimensionless | The final *E* edges of the cell. |
| | | `value` | counts or density | The counts or density of the cell. |
| | `data` (smoothed view) | `x`, `y` | dimensionless | The initial *E* and the final *E* of the grid point. |
| | | `value` | density or counts | The normalized density with peak 1, or the smoothed counts or density. |
| `render_dwell_survival` | `data` | `time` | seconds or frames | The dwell time. |
| | | `survival` | fraction | The fraction of dwells longer than `time`. |
| | `model` | `curve` | — | `model_survival` or `residual`. |
| | | `x` | seconds or frames | The dwell time. |
| | | `value` | fraction | A fraction of dwells. |
| | `fit` | `model` | — | The fit model. |
| | | `parameter` | — | `rate_1`, `rate_2`, …, `amplitude_1`, …, `beta` (the stretched model) and `r_squared`. |
| | | `unit` | — | The unit of the parameter: `1/s` or `1/frame` for a rate, and `dimensionless` otherwise. |
| | | `value`, `stderr`, `ci_halfwidth` | the unit in `unit` | The estimate, its standard error (1 σ) and the half-width of its confidence interval. |
| | | `ci_level` | fraction | The confidence level of `ci_halfwidth`. |
| `render_transition_prob` | `data` | `bin_left`, `bin_right` | dimensionless | The bin edges of the transition probability per molecule. |
| | | `value` | counts or density | Counts, or density. |
| | `model` | `curve` | — | `kde`. |
| | | `x` | dimensionless | The probability. |
| | | `value` | density | The KDE density. |
| `render_state_number` | `data` | `n_states` | count | The number of occupied states. |
| | | `molecules` | count | How many molecules have that many. |
| `render_raw_fret_cloud` | `data` | `time` | seconds or frames | The time of the point. |
| | | `e` | dimensionless | *E*. |
| | `density` | `x_low`, `x_high` | seconds or frames | The time edges of the cell. |
| | | `y_low`, `y_high` | dimensionless | The *E* edges of the cell. |
| | | `value` | density | The KDE density. |
| | `levels` | `coverage` | fraction | The fraction of the density inside the contour. |
| | | `level` | density | The KDE density of the contour. |
| `render_cross_correlation` | `data` | `lag` | frames | The lag. |
| | | `value` | dimensionless | The correlation, under the normalization that the `units` line names. |
| `render_per_condition_histograms` | `data` | `condition` | — | The condition id. |
| | | `bin_left`, `bin_right` | dimensionless | The bin edges, in *E*. |
| | | `value` | counts or density | Counts, or density. |
| `render_transition_sync_histogram2d` | `data` | `x_low`, `x_high` | seconds or frames | The edges of the time from the transition. |
| | | `y_low`, `y_high` | dimensionless | The *E* edges of the cell. |
| | | `value` | counts or density | The counts or density of the cell. |
| `render_alpha_shape` | `data` | `edge` | count | The number of the segment, from 1. |
| | | `time_start`, `time_end` | seconds or frames | The times of the two ends. |
| | | `e_start`, `e_end` | dimensionless | The *E* of the two ends. |
| | `points` | `time`, `e` | as in `render_raw_fret_cloud` | The points of the cloud. |
| `render_state_number_elbow` | `data` | `k` | count | The number of clusters. |
| | | `rmse` | dimensionless | The within-cluster RMSE of *E*. |
| | | `elbow` | — | `1` for the suggested *k*, otherwise `0`. |
| `render_successive_frame_cloud` | `data` | `molecule` | — | The position of the molecule, from 0, in the `molecule_keys` of the provenance of the cloud. |
| | | `frame` | frames | The frame *i* of the pair, counted from the start of the analysis window of the molecule. |
| | | `e_i`, `e_next` | dimensionless | *E* at frame *i* and at frame *i* + 1. |
| | `density` | `x`, `y` | dimensionless | *E* at frame *i* and at frame *i* + 1. |
| | | `value` | density | The KDE density. |
| | `levels` | `coverage` | fraction | The fraction of the density inside the contour. |
| | | `level` | density | The KDE density of the contour. |
| | `centroids` | `centroid` | count | The number of the centroid, from 1. |
| | | `e_i`, `e_next` | dimensionless | The *E* of the centroid at frame *i* and at frame *i* + 1. |
| `render_population_anticorrelation` | `data` | `row` | — | The row of the molecule on the figure, from 0. |
| | | `molecule_key` | — | The `molecule_key` of the molecule. |
| | | `start`, `stop` | frames | The first frame of the event and the frame after its last, in the analysis window. |
| | | `peak_frame` | frames | The frame of the peak. |
| | | `peak_magnitude` | dimensionless | The peak \|lag-0\| Pearson correlation. |

Three later additions are not in these files yet: the result snapshot, the figure footer and the
interval method. Each adds its part when it lands.

<a id="provenance-sidecar"></a>

## Provenance file

> **Keep the provenance file with the data file when you move or share the export.** For the CSV
> and the `.txt`, it is the **only** record of which build, which project and which parameters
> produced those numbers.

Each of the four exports writes `<file>.provenance.json` beside its output, through
`tether.project.export.write_provenance_sidecar`. The name *appends* to the full filename, so the
original extension survives: `molecules.csv.provenance.json`, `traces.txt.provenance.json` and
`subset.tether.provenance.json`. Plot exports pass the stem without an extension, so theirs is
`<stem>.provenance.json`.

[`tether-provenance-schema.json`](tether-provenance-schema.json) states the keys below as a JSON
Schema, with the `parameters` of each export.

The payload is `json.dumps(payload, indent=2, sort_keys=True) + "\n"`, in UTF-8. That is why the
keys are in alphabetical order.

> **Pass a base name, not a path, as the `source=` of `export_figure`.** Tether writes the value
> unchanged into `source_project` (note 1).

| Field | Value |
|---|---|
| `app_version` | The git-derived `tether.__version__`, with `"0.0.0+unknown"` as the fallback. A provenance stamp never raises. |
| `created_utc` | Offset-aware ISO-8601 UTC with microseconds (`datetime.now(UTC).isoformat()`), for example `2026-07-20T22:14:26.314669+00:00`. A caller can pin it, which is how the attribute of a subset and its provenance file agree exactly. |
| `parameters` | The parameters of the export, different for each export. The sections above list them. |
| `source_project` | For the three project exports, the source `.tether` **base name only**. For `export_figure`, the `source=` argument, **unnormalized** (note 1). |
| `tether_export` | The export-kind discriminator (note 2). |

**Notes**

1. The three project exports call `write_provenance_sidecar(source=path.name)`. `export_figure`
   forwards its `source=` argument verbatim, and `write_provenance_sidecar` does no `.name`
   reduction of its own. So an absolute path that you pass is an absolute path in the JSON.
2. The project exports use exactly three fixed values: `"deeplasi-txt"`, `"molecule-table-csv"` and
   `"subset-tether"`. For plots the caller supplies it. Nothing in `src/` defines a vocabulary of
   plot kinds. But `export_figure` refuses the three project kinds with `ValueError`. The
   [provenance schema](tether-provenance-schema.json) reads them as project exports, so a figure
   provenance file under one would fail it. Use a `plot:` kind, such as `"plot:tdp"`.

A real provenance file, verbatim:

```json
{
  "app_version": "0.0.0+unknown",
  "created_utc": "2026-07-20T22:14:26.314669+00:00",
  "parameters": {
    "include_rejected": true,
    "intensity_quantity": "corrected",
    "n_molecules": 3
  },
  "source_project": "chan.tether",
  "tether_export": "molecule-table-csv"
}
```

A subset `.tether` and a plot export also carry a stamp *inside* the file. For a subset it is the
`tether_subset_*` root attributes and the `app_version` of `create_project`. For a plot it is the
footer on the figure and the embedded document metadata (both above). Only the provenance file
carries the full parameter set. The [Background](#why-the-provenance-file-is-beside-the-export)
gives the reason for a separate file.

To read a provenance file back next to the stamps inside the project, and to turn both into a
Methods paragraph, see [Reproducibility and reporting](../reproducibility.md).

## Put these files in your deposit

A CSV or `.txt` export is flat text. To let another person read it correctly, put these files
beside your data when you deposit it. Download them from the documentation of the Tether version
that wrote your exports, so that they describe that version:

| File | What it describes |
|---|---|
| [`tether-schema.json`](tether-schema.json) | The frozen skeleton of a `.tether` file: its groups, its four compound tables and its root attributes. It is `schema/schema_frozen.json` from the repository, unchanged ([format reference](tether-format.md)). |
| [`molecule-table-columns.csv`](molecule-table-columns.csv) | The data dictionary of the molecule-table CSV: one row for each column, with `name`, `type`, `unit`, `can_be_blank` and `description` ([columns](#molecule-table-columns)). |
| [`tether-provenance-schema.json`](tether-provenance-schema.json) | A JSON Schema (draft 2020-12) of the `<file>.provenance.json` provenance file ([above](#provenance-file)). Any JSON Schema validator can check a provenance file against it. |

These three files are licensed **CC0-1.0**, so you can copy them into a deposit with no conditions.
The program code keeps its GPL-3.0-or-later license.

## Reading the exports elsewhere

### pandas

```python
import pandas as pd

table = pd.read_csv("molecules.csv")  # UTF-8, one header row, comma-delimited

# Blank cells are "not finite", not zero — keep them as NaN, do not fillna(0).
uncorrected = table["gamma"].isna().sum()

# Recompute corrected E from the shipped factors where they exist:
#   E = (A - alpha*D) / ((A - alpha*D) + gamma*D)
# The CSV carries only the per-molecule apparent-E summary, so the per-frame D and A
# must come from the .txt export or the store itself.
```

Eight columns are integers and never blank: `aperture_id`, `donor_bleach_frame`,
`acceptor_bleach_frame`, `frame_start`, `frame_end`, `window_start`, `window_end` and
`n_finite_frames`. Seven columns are floats that can be blank: `quality_class`, `alpha`, `gamma`,
`delta`, `correction_confidence`, `mean_apparent_e` and `median_apparent_e`. Every other column is a
string, `curation_label` included. `tags` arrives as one comma-joined cell that you split yourself.

### MATLAB, Prism, Origin, Excel

The CSV is plain UTF-8 with exactly one header row, a comma delimiter and standard `"` quoting. So
`readtable` and every spreadsheet importer read it directly. Set two things on import:

- Keep blank cells as missing, not `0`.
- Read `molecule_id` and `molecule_key` as text. `molecule_key` is a 64-character hex digest, and a
  numeric or scientific-notation guess changes it.

The Deep-LASI `.txt` is whitespace-delimited with **no header**. So `readmatrix` and
`numpy.loadtxt` read it as a plain `T × 2N` numeric matrix. Pass `ndmin=2` to `numpy.loadtxt`. A
single-frame export is a one-row file, and plain `loadtxt` collapses it to a 1-D array. For that
reason Tether's own `read_deeplasi_txt` reads it as `np.loadtxt(path, dtype=np.float64, ndmin=2)`.
`readmatrix` needs no such flag.

Count molecules from zero. Then the donor of molecule *k* is 0-based column `2k`, and its acceptor
is column `2k + 1`. In the 1-based indexing of MATLAB, they are columns `2k + 1` and `2k + 2`.

### Frames to seconds

No *project* export carries the frame duration. Only a plot export can, and only as a rendered
seconds axis that you cannot read a number back from. The source project holds the duration **for
each movie** (`/movies.frame_time`, `tether.io.schema.MOVIES_DTYPE`). Every CSV row names its movie
in `movie_id`. So the conversion is a join on each row, not one global constant:

```python
import csv

import h5py


def frame_times_by_movie(project_path):
    """Map each ``movie_id`` to its own seconds per frame, from the source project."""
    with h5py.File(project_path, "r") as store:
        movies = store["movies"]["table"][:]
    times = {}
    for movie in movies:
        movie_id = movie["movie_id"]
        if isinstance(movie_id, bytes):  # h5py hands vlen UTF-8 back as bytes
            movie_id = movie_id.decode("utf-8")
        times[movie_id] = float(movie["frame_time"])
    return times


def frames_to_seconds(frames, movie_id, frame_times):
    """Convert one exported frame number. A stored 0.0 means *unknown*, never zero."""
    frame_time = frame_times.get(movie_id)
    if frame_time is None:
        raise KeyError(f"no /movies row for movie_id {movie_id!r}")
    if frame_time <= 0.0:
        raise ValueError(f"movie {movie_id!r} carries no frame duration")
    return frames * frame_time


frame_times = frame_times_by_movie("experiment.tether")

with open("molecules.csv", newline="", encoding="utf-8") as handle:
    for row in csv.DictReader(handle):
        seconds = frames_to_seconds(int(row["window_end"]), row["movie_id"], frame_times)
```

Three caveats follow. The two functions raise an error on each of them, and they do not return a
plausible wrong number.

- **A project can hold more than one movie, each with its own `frame_time`.** Extraction adds one
  `/movies` row for each movie. So `movies["frame_time"][0]` applied to every exported row silently
  scales wrong each molecule that came from a later movie. Key by `movie_id`, as above. On a
  two-movie project file at `0.1` and `0.25` s/frame, frame 8 is `0.8 s` and `2.0 s`. The first-row
  shortcut reports `0.8 s` for both.
- **A stored `0.0` means *unknown*, not "instantaneous".** The GUI treats a non-positive
  `frame_time` as absent and falls back to a frame axis (`tether.gui.shell`). `frames_to_seconds`
  above refuses, and it does not hand back a column of zeros labeled seconds.
- **Some rows have no movie to key on at all.** A **subset `.tether` has zero `/movies` rows** by
  construction. An **analysis-only import** has none either, and it writes `movie_id = ""` (see
  [Identity columns by kind of project file](#identity-columns-by-kind-of-project-file)). Both reach
  the `KeyError`. The factor has to come from the parent project or from the acquisition metadata,
  because Tether never stored one.

### Idealization state paths in a subset

A subset carries whole `/idealization/{model}` groups. In `state_path`, the sentinel **`-1`**
(`tether.idealize.NO_STATE`) marks a frame with **no assigned state**. Such a frame is outside the
analysis window, or it is an interior gap, and the matching `idealized` cell is `NaN`. It is a
sentinel, not state number −1. Exclude those frames, and do not treat them as an extra state.
Tether's own analyses do exactly that (`tether.analysis.tdp`, `tether.analysis.state_number`,
`tether.analysis.transition_prob`).

### How a `NO_STATE` gap changes the dwells

A `NO_STATE` frame is **never itself a dwell and never a transition**. What it does to the run
around it depends on which function reads the path:

- `tether.analysis.dwell.state_dwells` does **not split a run** at it. It *strips* the `NO_STATE`
  frames before it splits on a state change. This is the NaN strip of tMAVEN, kept for parity
  ([`tests/test_analysis_dwell.py`](https://github.com/bioedca/tether/blob/main/tests/test_analysis_dwell.py)).
  So two same-state segments on either side of an interior gap are stitched into **one run**.
- `tether.idealize.dwells_from_states` does the opposite. A `NO_STATE` frame **ends** the run that
  it interrupts. Its docstring says: "they break a run but are not dwells".

**What a merged dwell's length means.** `state_dwells` reports the length of a run as the number of
frames that survived the strip. So the length of a merged dwell is the **sum of the observed
in-state frames on both sides of the gap**. The gap frames are discarded:

```python
import numpy as np
from tether.analysis.dwell import state_dwells
from tether.idealize import NO_STATE

path = np.array([0, 0, 0, NO_STATE, NO_STATE, NO_STATE, NO_STATE, 0, 0, 0, 1, 1, 1, 0])
state_dwells([path], no_state=NO_STATE, include_first=True).lengths
# {0: array([6]), 1: array([3])}
```

The state-0 frames of the merged run are indices 0–2 and 7–9. That is a dwell length of **6
frames**, across an elapsed span of **10 frames** from the first of them to the last. So a merged
dwell is longer than either segment alone. It is shorter than the time that elapsed between entering
the state and the last frame in it. The gap makes it impossible to observe which of those the
molecule did. It is not a residence time in either direction.

**A merge is reported only when the merged run is retained.** `state_dwells` always drops the last
run of a molecule. It drops the first run unless `include_first` is set (see
[dwell time](../glossary.md#dwell-time-dwell-time-survival)). It also skips a molecule left with a
single run. So a merged run is reported only when it is neither the sole run of the molecule nor its
last. When it is the first, it is reported only at `include_first=True`.

The bare trace `0,0,NO_STATE,0,0` yields **no dwell at all**, at either `include_first` setting,
because its merged run is the only run of the molecule. A later transition makes the merged run the
first of several. At `include_first=True` it is then reported as one 4-frame dwell of state 0,
against an elapsed span of 5 frames, not as two 2-frame dwells. At the default
`include_first=False` it is dropped as a first run:

```python
state_dwells([np.array([0, 0, NO_STATE, 0, 0])], no_state=NO_STATE, include_first=True).lengths
# {}   (and {} with include_first=False)
state_dwells([np.array([0, 0, NO_STATE, 0, 0, 1, 1, 0])], no_state=NO_STATE, include_first=True).lengths
# {0: array([4]), 1: array([2])}
state_dwells([np.array([0, 0, NO_STATE, 0, 0, 1, 1, 0])], no_state=NO_STATE, include_first=False).lengths
# {1: array([2])}
```

**Direction of the effect.** The comparison is with the same extraction where the gap *splits*
runs instead of stitching them, with identical first and last handling. Tether does not implement
that mode.

- The sample never gains a dwell, and the pooled frame total never rises. Each merge removes one of
  the retained dwells of its molecule, until the molecule has none left to lose. At the default
  `include_first=False`, the bare `0,0,NO_STATE,0,0` above retains none either way. Each of its
  segments is a first or a last run, so its merge removes nothing.
- The pooled mean dwell `⟨τ⟩` **rises** when both joined runs would have been retained anyway,
  because the same frames now sit in one fewer dwell. In the first path above, state 0 goes from two
  3-frame dwells (mean 3 frames) to one 6-frame dwell (mean 6 frames). The `k = 1/⟨τ⟩` of
  `pooled_exit_rates` **falls** with it, from 0.333333 to 0.166667 per frame at `dt = 1`.
- The merged run can land in the last slot, which is always dropped. It can also land in the first
  slot at the default `include_first=False`. Then the segments that it absorbed leave the sample,
  frames and all, and `⟨τ⟩` has **no fixed direction**.
    - `⟨τ⟩` rises if what was removed was shorter on average than the pooled dwells of the state. It
      falls if longer, and it is unchanged if equal. `k = 1/⟨τ⟩` moves the other way.
    - At the extreme the molecule is left with one run and contributes nothing. For example,
      `0,0,NO_STATE,NO_STATE,0,0` returns `{}` at `include_first=True`, where splitting would yield
      one 2-frame dwell of state 0. A state can drop out of the results altogether.
- Over a population both kinds of merge can occur at once. So the merge alone does not fix the net
  direction of `⟨τ⟩`, or of `k`.
- No direction is claimed for the fitted survival lifetime. `population_dwell_times` hands this pool
  to `survival_curve` and `fit_survival`, so the fit moves with the pool. But a least-squares
  survival fit is not the reciprocal of a mean.

**Which dwell surfaces see a gap is decided by their callers**, not by the functions. Both
`population_dwell_times` and `tether.analysis.kinetics.pooled_exit_rates` call `state_dwells` with
the sentinel `-1`, which *is* `NO_STATE`.

- `population_dwell_times` reads its paths through
  [`tether.analysis._store.windowed_states`](https://github.com/bioedca/tether/blob/main/src/tether/analysis/_store.py).
  That function cuts the analysis window of each molecule between its first and last non-sentinel
  frame, but it keeps any interior gap. So it **merges**.
- The only caller of `pooled_exit_rates` in the source of Tether is `two_state_rate_constants`. Its
  paths come from `viterbi_paths`, an `argmax` over the states of the model that never emits the
  sentinel. So nothing merges there. But `pooled_exit_rates` is public API: give it a stored
  analysis window, and it merges in the same way.

A gap between two *different* states is not merged. The strip makes the runs on either side of it
adjacent, so the transition is kept. But the transition happened somewhere inside the gap, and its
timing was not observed. Each of those two dwells counts only its observed frames.

On the [TDP](../glossary.md#tdp-transition-density-plot) side, the gap becomes `NaN` and the
non-finite pair is dropped. So a `NO_STATE` border never emits a transition point.

**Why the first and last dwells are dropped.** The two are dropped for different reasons. The
**last** dwell is right-censored: the trace ends before the molecule is seen to leave that state.

The **first** is a different case. Its start was not observed, because the molecule was already in
that state when the analysis window opened. Its end is a recorded transition. That exit is observed
only when no interior `NO_STATE` gap separates the first dwell from the next state. Across such a
gap the exit happened somewhere inside it.

## Background

### Why the E summary is apparent E

The docstring of `tether.project.export.export_molecule_table_csv` states the reason: apparent E is
always well-defined. The stored α, γ and δ travel as their own columns precisely so that a consumer
can recompute the corrected value.

### Why the `include_rejected` defaults differ

- The CSV is a full inventory. Its `curation_label` column tells you which rows were rejected.
- A trace matrix has no label slot for each column, so a rejected trace would be
  indistinguishable from an accepted one.
- A subset is a curated selection by intent.

### Why the provenance file is beside the export

Flat text has no metadata slot, and the Deep-LASI `.txt` must stay without a header. So the stamp
travels beside the file, not inside it (ADR-0001).

### Why a subset `.tether` is readable at all

The `.tether` project file is a **superset of the tMAVEN SMD layout**, not a private format
(ADR-0002, [The `.tether` store is an SMD superset](../adr/0002-smd-superset-round-trip.md)). It is
HDF5: `h5py`, MATLAB `h5read` and `rhdf5` all open it without Tether installed. The same property
makes the tMAVEN round trip a consequence of the data model, not a conversion step.

## References

- The [product specification](https://github.com/bioedca/tether/blob/main/docs/PRD.md) in the
  repository. It gives the export surface and the requirement that "all exports are stamped with
  provenance and parameters". It also gives the subset invariant, the curation filter and the
  non-uniqueness of `molecule_key`.
- **ADR-0001**: the provenance-first data model.
- **ADR-0002**: the `.tether` store is an SMD superset, and the round trip is a property of the
  data model.
- **ADR-0005**: the schema freeze (additive-only), which the subset export honors.
- **ADR-0008**: the Deep-LASI → Tether correction-factor remap (β→α, α→δ, γ→γ).
- **ADR-0016**: the extraction trace layout, with traces zero-padded to the largest `T` and the
  `molecule_key` content hash.
- **ADR-0024**: the `/idealization/{model}` layout that a subset carries.
- **ADR-0044**: `matplotlib-base` as the static vector plot-export backend.
- [Legacy Deep-LASI import](../io/legacy-import.md): the input side of the same story.
- [Standalone-tMAVEN hand-off](../idealize/standalone-tmaven-handoff.md): the SMD interchange step,
  which is not an export.
- [Seven-plot parity gallery](../analysis/parity-gallery.md): what each renderer plots and the test
  that pins its parity.

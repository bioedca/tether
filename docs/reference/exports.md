---
description: "The files that Tether writes out: each column, its order, its unit and what a blank cell means, for tables, trace files, subsets and figures."
---

# Exports — what Tether writes out

**Who this page is for.** Anyone who has a file Tether produced — a molecule-table CSV
about to become a figure, a `…-donc-accc-w.txt` handed to a collaborator, a subset
`.tether` mailed to a reviewer, a PDF pasted into a manuscript — and needs to know
exactly what is in it: which columns, in which order, in which units, and what a blank
cell means. It is a reference, not a tutorial. Every claim below names the module,
function or constant that makes it true, so you can grep the source rather than trust
the prose.

Two facts frame everything else:

- **Every temporal number in the three *project* exports (CSV, `.txt`, subset `.tether`) is
  in *frames*, zero-based, never seconds**, and none of the three carries the frame
  duration. The project file keeps it (`/movies.frame_time`, `tether.io.schema.MOVIES_DTYPE`),
  but the molecule-table CSV has no such column, the Deep-LASI `.txt` has no metadata at
  all, and a subset `.tether` carries **zero** `/movies` rows by construction. Converting
  to seconds is the reader's own job — see
  [Reading the exports elsewhere](#reading-the-exports-elsewhere). The fourth export is
  the exception: a plot renderer handed `dt` / `time_dt` ≠ `1.0` draws a *seconds* axis
  (`tether.analysis.plot_export`; see [Plot export](#4-plot-export-pdf-svg-png)).
- **The E summary in the CSV is *apparent* E, not γ-corrected E.** `export_molecule_table_csv`
  computes it with `tether.fret.efficiency.apparent_fret`, i.e. `A / (D + A)`
  (`corrected_fret` at the identity factors `alpha=0, gamma=1`). The stored α/γ/δ are
  **not** applied; they ride as their own columns precisely so a consumer can recompute
  the corrected value. The docstring at `tether.project.export.export_molecule_table_csv`
  states the reason: apparent E is always well-defined.

## At a glance

Four export entry points are public. The first three live in `tether.project.export`
(`__all__` also lists `ExportResult` and `write_provenance_sidecar`); the fourth lives in
`tether.analysis.plot_export`.

| Entry point | Writes | Choose it when |
|---|---|---|
| `tether.project.export.export_molecule_table_csv` | `<name>.csv` + `<name>.csv.provenance.json` | You want one row per molecule for a spreadsheet, R/pandas, Prism or Origin — the per-molecule inventory and its apparent-E summary |
| `tether.project.export.export_deeplasi_txt` | `<name>.txt` + `<name>.txt.provenance.json` | You want the per-frame intensity traces themselves, in the Deep-LASI `…-donc-accc-w.txt` matrix a downstream Deep-LASI-compatible tool reads |
| `tether.project.export.export_subset_tether` | `<name>.tether` (HDF5) + `<name>.tether.provenance.json` | You want a portable, self-contained re-analysis of a *selection* — coordinates, patches, background-subtracted traces, idealization models — without shipping the movie |
| `tether.analysis.plot_export.export_figure` | `<stem>.pdf`, `<stem>.svg`, `<stem>.png` + `<stem>.provenance.json`, and with `data_csv=True` the numbers as `<stem>.csv` (+ `<stem>.<name>.csv`) | You want a publication figure of an analysis plot: vector PDF **and** vector SVG **and** raster PNG, all stamped, optionally with the [numbers behind it](#the-numbers-behind-a-figure) |

> **The first three also have a command.** `tether export table`, `tether export traces`
> and `tether export subset` call `export_molecule_table_csv`, `export_deeplasi_txt` and
> `export_subset_tether`, with one flag for each parameter
> ([`tether export`](../cli.md#tether-export)), and the file is the same. `export_figure` has no
> command, and no module under `src/tether/gui/` references any of the four functions above.
> Every snippet on this page is the Python call. The [how-to
> page](../how-to/export-results.md) gives the commands first.

The tMAVEN hand-off (`tether.project.handoff.hand_off_to_tmaven`, reachable from the GUI)
also writes a file — an SMD-HDF5 — but it is an *interchange* leg with a return path, not
an export, and it does **not** write a provenance file. It is documented on
[Standalone-tMAVEN hand-off](../idealize/standalone-tmaven-handoff.md).

## What all three project exports share

### Selection

All three take their rows from [`_selected_rows`](https://github.com/bioedca/tether/blob/main/src/tether/project/export.py) in `tether.project.export`, which means:

| Behavior | Detail |
|---|---|
| Order | **Project order** (ascending row index). A `molecule_keys` request does *not* reorder the output |
| Curation filter | Rows whose `curation_label` is `CurationLabel.REJECT` (`-1`, `tether.project.labels`) are dropped unless `include_rejected=True` |
| `molecule_keys` | A membership subselect, not a lookup. `molecule_key` is **not unique**, so one requested key matches *every* project row carrying it |
| Unknown key | A requested key matching no project row raises `KeyError` — a typo fails loudly rather than silently under-exporting |
| Empty selection | `export_deeplasi_txt` and `export_subset_tether` raise `ValueError` |

The `include_rejected` default is **not** the same across the three, and the difference is
deliberate:

| Function | `include_rejected` default | Why |
|---|---|---|
| `export_molecule_table_csv` | `True` | The CSV is a full inventory; the `curation_label` column tells you which rows were rejected |
| `export_deeplasi_txt` | `False` | A trace matrix has no per-column label slot, so a rejected trace would be indistinguishable |
| `export_subset_tether` | `False` | A subset is a curated selection by intent |

Only `export_deeplasi_txt` and `export_subset_tether` accept `molecule_keys`.
`export_molecule_table_csv` has no such parameter — it always passes `molecule_keys=None`.

### `intensity_quantity`

The two table exports read one `/traces` layer pair, chosen by `intensity_quantity` through
`tether.project.trace_layers.INTENSITY_QUANTITY_LAYERS`. Exactly two values are legal:

| Value | Layers read |
|---|---|
| `"corrected"` (default) | `donor_corrected`, `acceptor_corrected` — background-subtracted |
| `"raw"` | `donor_raw`, `acceptor_raw` |

Anything else raises `ValueError: unknown intensity_quantity 'total'; expected one of
['corrected', 'raw']` (the message is built in `_layers`).

### Return value

All three return a frozen `ExportResult` with three fields in this order: `path`,
`provenance_path`, `n_molecules` — the count **after** the curation and selection filters.

### Intensity units

Wherever an export carries an intensity (the `.txt` matrix, a subset's `/traces`), the
number is **uncalibrated**: there is no gain, offset or quantum-efficiency conversion
anywhere in `tether.imaging`, so nothing any export writes is in photons. What the number
*is*, though, depends on how the project file was built — and no export records that:

| Project file built by | What an exported intensity is |
|---|---|
| **Native extraction** — `tether.project.extract.extract_movie` → `tether.imaging.extract.write_extraction` | An **integrated camera value in the movie's own pixel units**, produced here by `tether.imaging.aperture.integrate_traces` over the aperture below: counts/ADU, tied to Tether's own extraction settings |
| **Deep-LASI reconstruction** — `tether.project.reconstruct.reconstruct_project` | The **pre-integrated Deep-LASI `.mat` series, carried through without re-integration**. [`_traces_from_export`](https://github.com/bioedca/tether/blob/main/src/tether/project/reconstruct.py) maps `donc`/`accc` → the background-subtracted (`corrected`) layer, `don`/`acc` → raw, `bdon`/`bacc` → background; its docstring states that "the aperture integration `extract_molecules` would run is skipped". The units are whatever Deep-LASI's own integration produced |
| **Analysis-only import** — `tether.project.analysis_import.import_analysis_only_project` | The **SMD / `.txt` intensity series, carried through without re-integration** as the background-subtracted (`corrected`) pair ([`_write_corrected_traces`](https://github.com/bioedca/tether/blob/main/src/tether/project/analysis_import.py)), the module's "apparent-E analysis substrate". No `raw` or `background` layers are synthesized — there is "no movie to decompose the intensities against" |

Only the first row is a number Tether itself measured. For the other two the units are the
upstream tool's, and the aperture parameters below describe the *provenance* of the
imported layers rather than an integration Tether performed. Carried through is not
bit-preserved, though: every reader parses the upstream numbers as `float64`
(`tether.io.deeplasi` for the `.mat` and the `.txt`, `tether.idealize.read_smd` for the
SMD), while every `/traces` layer is stored as `float32`
([`_TRACE_DTYPE = "<f4"`](https://github.com/bioedca/tether/blob/main/src/tether/imaging/extract.py), in both [`tether.imaging.extract._append_padded_2d`](https://github.com/bioedca/tether/blob/main/src/tether/imaging/extract.py) and
[`tether.project.analysis_import._write_corrected_traces`](https://github.com/bioedca/tether/blob/main/src/tether/project/analysis_import.py)). An export compared back
against a double-precision `.mat` or SMD input therefore differs by single-precision
rounding.

Which of the three layers you get is not the same number:

| Layer | What the value is | Sign |
|---|---|---|
| `*_corrected` (`intensity`) | The top-hat: the disk sum minus the scaled ring background, `intensity = tot - bg` | **May be negative** — a background is subtracted |
| `*_raw` (`total`) | The bare disk sum `tot` over the PSF disk, nothing subtracted | Non-negative for a non-negative movie |
| `*_background` (`background`) | The subtracted term `bg` alone — carried only by a subset with `include_raw=True` | Non-negative for a non-negative movie |

The mapping is the frozen [`_QUANTITIES`](https://github.com/bioedca/tether/blob/main/src/tether/imaging/extract.py) tuple in `tether.imaging.extract`, annotated
there as *"`raw` = uncorrected `total`, `corrected` = `intensity`"*. So an
`intensity_quantity="raw"` export is **not** the top-hat, and the "may be negative"
caveat does not apply to it.

`bg` itself is not a bare per-frame ring mean: `integrate_traces` first runs a uniform
temporal moving average over the crop (`bg_window`, default `10` frames,
`mode="nearest"`), then averages the **positive** in-ring pixels of the smoothed crop
(`ring_vals > 0`, faithful to Deep-LASI's `mean(bg(bg>0))`), then scales that mean by the
disk's pixel count `n_psf = int(disk.sum())`.

> **The aperture geometry is an extraction option, not a property of the export.** At the
> defaults (`window=21`, `disk_radius=3.0`, `ring_inner=6.0`, `ring_outer=8.0`,
> `bg_window=10` — `tether.project.extract.ExtractOptions`, mirrored by
> `tether.imaging.aperture.integrate_traces`) the disk is **29 px**, and that count is set
> by **`disk_radius` alone**. `aperture_masks` builds the disk as `dist <= disk_radius`
> about the aperture-window center, so `n_psf` is 29 for *every* legal `window` — the aperture window only
> has to be odd and wide enough for the ring (`2 * ring_outer <= window`). Changing
> `disk_radius` is what re-scales an intensity: `3.0 → 29 px`, `4.0 → 49 px`, `5.0 → 81 px`.
> The `tether extract --window` flag changes the crop-box side length and leaves `n_psf`
> at 29; `disk_radius`, the ring radii and `bg_window` have **no CLI flag** and are
> reachable only through the Python API (`ExtractOptions`,
> `integrate_traces(disk_radius=…)`). The effective geometry is stamped into
> `/settings/extraction` (`window`, `disk_radius`, `ring_inner`, `ring_outer`,
> `bg_window`, `n_psf` — [`tether.imaging.extract._write_settings_once`](https://github.com/bioedca/tether/blob/main/src/tether/imaging/extract.py)), which travels in
> a subset `.tether` but is carried by **neither** the `.txt` nor its provenance file.

> **An imported project file's `/settings/extraction` is provenance, not a measurement.**
> `reconstruct_project` hands `write_extraction` the standard constants
> ([`_APERTURE_WINDOW = 21`](https://github.com/bioedca/tether/blob/main/src/tether/project/reconstruct.py), [`_APERTURE_DISK_RADIUS = 3.0`](https://github.com/bioedca/tether/blob/main/src/tether/project/reconstruct.py), [`_APERTURE_RING_INNER = 6.0`](https://github.com/bioedca/tether/blob/main/src/tether/project/reconstruct.py),
> [`_APERTURE_RING_OUTER = 8.0`](https://github.com/bioedca/tether/blob/main/src/tether/project/reconstruct.py), [`_APERTURE_BG_WINDOW = 10`](https://github.com/bioedca/tether/blob/main/src/tether/project/reconstruct.py)), annotated in the source as
> describing "the *provenance* of the layers, not a fresh integration this module
> performed". An analysis-only import writes **no** `/settings/extraction` at all — its
> `/settings` children are `analysis_only` and `correction` — and, carrying neither
> `raw`/`background` layers nor `/patches`, it can leave only as the CSV or the background-subtracted
> `.txt`: `intensity_quantity="raw"` raises `KeyError: 'donor_raw'`, and `export_subset_tether`
> refuses with `ValueError: source /patches is missing the 'donor' channel; cannot export
> a curatable movie-less subset`.

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

**File format.** Written with the stdlib `csv.writer` on a handle opened
`open("w", newline="", encoding="utf-8")`, so:

| Property | Value |
|---|---|
| Encoding | UTF-8, no BOM |
| Delimiter | `,` |
| Quoting | `csv.QUOTE_MINIMAL` with `"` — a cell containing a comma (a multi-tag `tags`, a `category` like `dynamic, folded`) is quoted |
| Line terminator | `\r\n` on **every** platform (the `csv` module default, not the OS) |
| Header | Exactly one row, the 25 names below |
| Rows | One per exported molecule, in project order — one `/molecules` row = one detected donor spot in one movie, or, in an analysis-only import, one imported SMD / `.txt` trace |

**Numbers.** Integer columns go through `int()` and print with no decimal point. Float
columns go through [`_fmt_float`](https://github.com/bioedca/tether/blob/main/src/tether/project/export.py), which returns `""` for `None` or any non-finite value and
otherwise `repr(float)` — Python's shortest round-tripping representation, full precision
and no fixed decimal count. That is why a real exported cell carries all its significant
digits: the row-0 `mean_apparent_e` of the three-molecule project file used for the `.txt`
sample below ([`tests/_analysis_store.build_store_with_channels`](https://github.com/bioedca/tether/blob/main/tests/_analysis_store.py) over the [`_asym_channels`](https://github.com/bioedca/tether/blob/main/tests/test_export_tables.py)
values of [`tests/test_export_tables.py`](https://github.com/bioedca/tether/blob/main/tests/test_export_tables.py)) reads `0.3715803781168891`, not `0.372`.

> **A blank cell means "not finite / not set", never zero.** [`_fmt_float`](https://github.com/bioedca/tether/blob/main/src/tether/project/export.py) maps both `None`
> and `NaN` to the empty string. Reading the CSV with a tool that coerces blanks to `0`
> will silently turn "no γ was ever estimated" into "γ = 0".

### Molecule-table columns

The order is frozen in `tether.project.export.MOLECULE_TABLE_COLUMNS`, annotated in the
source as *"the per-molecule CSV column order (frozen; a reader may key on these names)"*.
[`tests/test_docs_export_columns.py`](https://github.com/bioedca/tether/blob/main/tests/test_docs_export_columns.py) fails if this table and that tuple ever disagree. The
underlying field types are the frozen `tether.io.schema.MOLECULES_DTYPE`.
The same columns are in a file you can download,
[`molecule-table-columns.csv`](molecule-table-columns.csv): one row per column, with its
name, CSV type, unit, whether it can be blank, and a one-line meaning (see
[Put these files in your deposit](#put-these-files-in-your-deposit)).
[`tests/test_docs_published_data.py`](https://github.com/bioedca/tether/blob/main/tests/test_docs_published_data.py)
fails if that file and the tuple differ, or if its type, unit or blank entry for a column
disagrees with this table.

| Column | CSV type | Unit / domain | Blank when |
|---|---|---|---|
| `molecule_id` | string | Unitless identity — a stable per-row UUID, `mol-` + 32 hex chars. Unique within a project file | The stored string is empty |
| `molecule_key` | string | Unitless identity — 64-char lowercase hex SHA-256. For a project file built from a movie: of `"{movie_sha256}\|{qx}\|{qy}"` with `donor_xy` quantized (`tether.imaging.extract.molecule_key`). For an analysis-only import there is no movie and no coordinate, so [`tether.project.analysis_import._analysis_only_molecule_key`](https://github.com/bioedca/tether/blob/main/src/tether/project/analysis_import.py) hashes the source id, the row index and the trace bytes instead — same shape, different inputs (callout below). The cross-file join key; **not unique** | The stored string is empty |
| `movie_id` | string | Unitless identity of the source `/movies` row (e.g. `mov-1`). `""` in an analysis-only project, which has no linked movie — `tether.project.analysis_import` writes the empty string explicitly | No movie is linked |
| `source_filename` | string | The filename the row's provenance was parsed from (`tether.io.filename.parse_filename`): the source **movie** for a native extraction or a Deep-LASI reconstruction, the imported **SMD / `.txt`** for an analysis-only import | The stored string is empty |
| `condition_id` | string | Unitless condition key (e.g. `cond-353fd5a76531`) | The stored string is empty |
| `condition_id_provisional` | string | Unitless — the provisional key parsed from the filename at extraction, retained verbatim across any later re-key | The stored string is empty |
| `curation_label` | string | **Text, not the stored integer**: `accept`, `uncurated`, `reject` ([`_CURATION_TEXT`](https://github.com/bioedca/tether/blob/main/src/tether/project/export.py) over `tether.project.labels.CurationLabel` = `1 / 0 / -1`). An unrecognized integer falls through to its decimal string | Never — one of the values above is always written |
| `category` | string | Free text, editable per condition. Emitted verbatim, so a comma inside it is CSV-quoted | The stored string is empty (the extraction default `""`) |
| `quality_class` | float | The read-only ML quality score. Its numeric range is **not** defined by the export or the schema, which call it only a "read-only ML output" | The value is `NaN`. Extraction and analysis-only import both initialize it to `NaN` (`tether.imaging.extract`, `tether.project.analysis_import`) and no shipped code path writes anything else, so this column is blank in every export the current build can produce |
| `aperture_id` | integer | Unitless index into the aperture registry. Extraction always writes `0` (`tether.imaging.extract`) — a **placeholder**, because there is no per-aperture registry yet. It does **not** encode the aperture geometry: `tether extract --window` is settable and still writes `0`; the effective geometry lives in `/settings/extraction`, which the CSV does not carry (see [Intensity units](#intensity-units)) | Never — an integer is always written |
| `alpha` | float | **Dimensionless.** Donor→acceptor leakage α, applied as `I_A,corr = I_A − α·I_D` (`tether.fret.efficiency`) | α is unset. Extraction writes `NaN` until the correction step estimates it |
| `gamma` | float | **Dimensionless.** Detection-correction factor γ, the denominator weight in `E = I_A,corr / (I_A,corr + γ·I_D,corr)` | γ is unset (`NaN` at extraction) |
| `delta` | float | **Dimensionless.** Direct-excitation δ. Initialized to `0.0` and inert in 2-color (no ALEX; ADR-0008), so it exports as `0.0`, not blank | The stored value is non-finite |
| `correction_method` | string | Vocabulary from `tether.project.correct`: `""` (extraction default, nothing applied), `corrected`, `manual`, `apparent-E (corrections unavailable)`, `apparent-E (user toggle)` | Nothing has been applied yet (the `""` default) |
| `correction_confidence` | float | **Unitless provenance flag, not a statistical CI**: `1.0` when a real photophysical correction was applied, `0.0` when the molecule fell back to apparent E ([`_CONFIDENCE_CORRECTED`](https://github.com/bioedca/tether/blob/main/src/tether/project/correct.py) / [`_CONFIDENCE_APPARENT`](https://github.com/bioedca/tether/blob/main/src/tether/project/correct.py) in `tether.project.correct`) | No correction pass has run (`NaN` at extraction) |
| `donor_bleach_frame` | integer | **Frames**, zero-based, absolute. `bleach_frames[0]`. Sentinel `-1` = not detected ([`_UNDETECTED_FRAME`](https://github.com/bioedca/tether/blob/main/src/tether/imaging/extract.py), `tether.imaging.extract`); a value equal to `frame_end` means the channel does not bleach within the trace (`tether.project.photobleach`) | Never — an integer is always written |
| `acceptor_bleach_frame` | integer | **Frames**, zero-based, absolute. `bleach_frames[1]`; same `-1` and `== frame_end` conventions | Never |
| `frame_start` | integer | **Frames**, zero-based, **inclusive** — `frame_range[0]`, the start of the molecule's valid native extent inside the project file's zero-pad (ADR-0016). Extraction writes `0` | Never |
| `frame_end` | integer | **Frames**, zero-based, **exclusive** (half-open) — `frame_range[1]`. Extraction writes `n_frames` | Never |
| `window_start` | integer | **Frames**, zero-based, **inclusive** — the *resolved* analysis window actually used for `n_finite_frames`, `mean_apparent_e` and `median_apparent_e`: `analysis_window[0]`, falling back to `frame_range[0]` when the window is unset (`hi <= lo`), per [`_window`](https://github.com/bioedca/tether/blob/main/src/tether/project/export.py) | Never |
| `window_end` | integer | **Frames**, zero-based, **exclusive** — the resolved upper bound, with the same fallback | Never |
| `tags` | string | Free text, **comma-joined** in the project file (`tether.analysis.query` splits on `,`). Emitted verbatim, so a multi-tag cell is CSV-quoted. Analysis-only imports carry `round-trip-unavailable` | The molecule has no tags |
| `n_finite_frames` | integer | **Frames** — how many frames inside the resolved analysis window have a finite apparent E. `0` when every frame in the analysis window has `D + A == 0` exactly | Never |
| `mean_apparent_e` | float | **Dimensionless — apparent E** (the proximity ratio `A/(D+A)`), arithmetic mean over the finite frames of the resolved analysis window. **Not** γ-corrected. **Not** clipped to `[0, 1]`: `apparent_fret` deliberately leaves a noisy out-of-range value alone | `n_finite_frames == 0` |
| `median_apparent_e` | float | **Dimensionless — apparent E**, `numpy.median` of the same finite values. Same no-clipping rule | `n_finite_frames == 0` |

> **Three kinds of project file write these columns, and only one of them came from a movie.**
> A native extraction and a Deep-LASI reconstruction (`tether.project.reconstruct`, which
> calls the same `tether.imaging.extract.write_extraction`) both link a `/movies` row:
> `movie_id` is that row's id, `molecule_key` is the movie `sha256` + quantized
> `donor_xy`, and `source_filename` is the movie's name. An **analysis-only import**
> (`tether.project.analysis_import.import_analysis_only_project`, the coordinate-less
> SMD / `.txt` branch) writes `movie_id = ""`, `donor_xy`/`acceptor_xy` = `NaN`,
> `source_filename` = the SMD/`.txt` name, `tags = "round-trip-unavailable"`
> (`ANALYSIS_ONLY_TAG`), and a `molecule_key` from [`_analysis_only_molecule_key`](https://github.com/bioedca/tether/blob/main/src/tether/project/analysis_import.py) — the
> SHA-256 of the constant `"tether-analysis-only"`, the source id, the molecule's **row
> index**, and that molecule's raw donor and acceptor **trace bytes** (little-endian
> `float64`). The source calls it "an identity, not fabricated coordinate data". It is
> unique per row and stable across a re-import of the same source, but **nothing in it is
> movie-derived**: it cannot be re-derived from a movie hash or from coordinates, and it
> will not match the key the same molecule would carry in a movie-linked project file. A blank
> `movie_id` is the flag that says so; `tether.project.analysis_import.read_analysis_only_marker`
> is the O(1) check on the project file itself.

> **`window_start`/`window_end` may equal `frame_start`/`frame_end` by *fallback*, not by
> curation.** [`_window`](https://github.com/bioedca/tether/blob/main/src/tether/project/export.py) substitutes the frame range whenever `analysis_window` is unset
> (`hi <= lo`), and extraction also *initializes* `analysis_window` to `[0, n_frames]`. So
> "analysis window == frame range" tells you nothing about whether a curator ever looked at the
> molecule. The columns exist so the reported analysis window always matches the range the
> three trailing summary columns actually cover.

**How the E summary is computed.** For each row, `apparent_fret(donor[i, lo:hi],
acceptor[i, lo:hi])` over the resolved window `[lo, hi)`, on the layer chosen by
`intensity_quantity`; frames where `D + A == 0` exactly are `NaN` and are excluded from
the mean, the median and `n_finite_frames`.

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

The serializer is `tether.io.deeplasi.write_deeplasi_txt`; its read-side mirror is
`read_deeplasi_txt`, which de-interleaves the same file back into two `(N, T)` arrays.

| Property | Value |
|---|---|
| Header | **None.** No header line, no index column, no molecule ids — the file must stay Deep-LASI-faithful |
| Shape | `T` rows × `2N` columns: one row per **frame**, one column *pair* per **molecule** |
| Column order | **Interleaved, donor first**: `donor₀ acceptor₀ donor₁ acceptor₁ …` (`interleaved[:, 0::2] = donor.T`) |
| Number format | `numpy.savetxt(..., fmt="%.5f")` — fixed-point, exactly 5 decimals ([`_TXT_DECIMALS = 5`](https://github.com/bioedca/tether/blob/main/src/tether/io/deeplasi.py)). A round trip is therefore lossy to that rounding |
| Delimiter | A single space |
| Line ending | OS-translated (`savetxt` opens in text mode; no `newline=` is passed) — CRLF on Windows, LF elsewhere. Unlike the CSV, this is *not* fixed |
| Values | Uncalibrated intensities — never photons, and **not necessarily movie pixel units**: what the number is depends on how the *source project file* was built (integrated camera counts for a native extraction; the upstream tool's pre-integrated series, not re-integrated, for a Deep-LASI reconstruction or an analysis-only import). See [Intensity units](#intensity-units). Signed either way, so background-subtracted values may be negative |

A real first line, from a three-molecule project file:

```text
100.00000 53.00000 200.00000 56.00000 300.00000 59.00000
```

**Frame trimming — the one place a row index is not an absolute frame.** The project file's
`/traces` arrays are zero-padded to the experiment-max frame count as movies of differing
length are appended (ADR-0016), so `export_deeplasi_txt` trims to the selection's shared
`frame_range`: the written matrix is `donor_all[rows][:, lo:hi]`. Row 0 of the file is
therefore absolute frame **`lo`**, which need not be 0, and `lo` is recorded **only** in
the provenance file (`frame_range`). A selection spanning more than one `frame_range`
has no honest common axis and raises `ValueError` rather than padding or truncating.

Provenance-file `parameters` for this export: `intensity_quantity`, `include_rejected`,
`n_molecules`, `n_frames` (`= hi - lo`), `frame_range` (`[lo, hi]`, half-open),
`molecule_keys` (`null` or a list).

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

A new, self-contained HDF5 project built by `tether.io.schema.create_project` and then
populated with additive data only — it adds no structure to the frozen skeleton
(ADR-0005). It is written **atomically**: staged to a `tempfile.mkstemp` sibling in the
destination directory and moved into place with `os.replace`, and the staged file is
unlinked on any failure. [`_preflight_source`](https://github.com/bioedca/tether/blob/main/src/tether/project/export.py) validates the source (layers present,
row counts consistent, both patch channels present) **before** anything is written.

**What travels:**

| Group | Content |
|---|---|
| `/molecules` | The selected rows only |
| `/traces` | `donor_corrected`, `acceptor_corrected` always; `donor_raw`, `acceptor_raw`, `donor_background`, `acceptor_background` only when `include_raw=True` |
| `/patches` | Every channel (`donor`, `acceptor`), row-subset, aperture window unchanged |
| `/idealization/{model}` | Per-model, filtered on **`molecule_id`** (the unique UUID, not the non-unique `molecule_key`). The row-aligned members `idealized`, `state_path`, `molecule_key`, `molecule_id`, `input_hash` are subset; every other member (`mean`, `var`, `frac`, `tmatrix`, `norm_tmatrix`, `rates`, `pi`, the `priors` group) is a global consensus array and is copied verbatim. The group's `n_molecules` attribute is rewritten to the kept count |
| `/labels` | Rows whose `molecule_key` is in the selection |
| `/labels/reject_log` | Rows whose `molecule_key` is in the selection, so each reject keeps its reason. Absent when the source has no log or no selected molecule has a row |
| `/conditions`, `/settings`, `/calibration`, `/models` | Copied verbatim |

**What does not travel:**

| Dropped | Why |
|---|---|
| `/movies` **rows** | The subset is definitionally movie-less. The group exists (the skeleton is frozen) but has **zero rows**, so nothing the row itself held comes with it: `uri`, `sha256`, `file_size`, `mtime`, `n_frames` / `height` / `width`, `pixel_dtype`, `frame_time`, `head_tail_hash`, the per-channel crop/rotation/flip, and the row's `calibration_id` cell (`tether.io.schema.MOVIES_DTYPE`). **The calibration itself does travel** — `/calibration` is in [`_SUBSET_VERBATIM_GROUPS`](https://github.com/bioedca/tether/blob/main/src/tether/project/export.py) and is copied whole, so `/calibration/<calibration_id>` keeps the id (it *is* the group name), the two polynomial transforms and the registration geometry attrs; only the movie row is gone |
| `/features` | Present but empty — per-molecule ML features are outside the subset embed set ([`_SUBSET_VERBATIM_GROUPS`](https://github.com/bioedca/tether/blob/main/src/tether/project/export.py) deliberately omits it) |
| Raw **and** background traces, when `include_raw=False` | They ride as a set of four. Because `corrected = raw − background` exactly, keeping background alone would make raw reconstructable — omitting raw must omit background too |
| Idealization models whose input layers are absent, or none of whose molecules were selected | Listed in the provenance file's `skipped_idealization_models`. A `raw`-fitted model is skipped when `include_raw=False` |

**User names (`curator_alias`).** A project records a person's user name in
the `labeler` of each `/labels/table` and `/labels/reject_log` row, the `labeler` of each
`/settings/condition_audit` row (a condition re-key or merge), and the `holder_user` and `ousted_user` of each
`/settings/lock_takeovers` row (a lock that was recovered or taken over). Each
`/settings/jobs` row stores the last writer's name in the `user` key of its `owner` object.
A `/settings/correction_transitions` row, a `/settings/gamma_contributors` row and a
`/settings/gamma_contributor_decisions` row store a user name in `actor` when `by` is
`person`. The `actor` of a `policy` row contains policy text and keeps its value.
By default each user name is the operating-system login.

The plain fields are listed in `tether.project.export.USER_NAME_FIELDS`. The JSON fields
that hold a name under one key are listed in `tether.project.export.JSON_USER_KEYS`.
Only that key changes, so the record keeps its shape.

| `curator_alias` | Result |
|---|---|
| `None` (default) | The names are copied as they are. The output is the same as before the option existed |
| A prefix, for example `"curator"` | Each distinct name becomes `curator-1`, `curator-2`, … in the order the copy first writes it: the selected `/labels/table` and `/labels/reject_log` rows in store order, followed by the `/settings` logs. One person keeps one alias in every table, so a multi-curator analysis still works on the copy. An alias that contains a name in the source is skipped (so a copy whose names are already `curator-1`, … never gets `curator-10`), and a prefix that contains a name in the source raises `ValueError`. An empty name stays empty. The source project does not change |

A path can name the user too, for example a model file in a home folder. With the option,
each path in `tether.project.export.PATH_ATTRS` keeps only its file name. Today that is the
`artifact_path` of each `/models/<condition_id>` reference, so the copy's reference names
the model file but not its folder.

A name-bearing table is written directly from its aliased rows so those fields store only
their new values. The count covers the names in the records selected for export. A name
whose only labels stay behind, for example on a rejected molecule, is excluded from the count.
A blank prefix raises `ValueError`.

The option does **not** remove `source_filename` or `tether_subset_of`. They are file
names, not paths, and they are the origin record of each molecule. A file name can
identify a sample, so read them before you share the copy. Free text such as an audit
`reason`, a correction transition's `detail`, and the text of a correction policy is
copied as it is. So are the computer names (`holder_host`,
`ousted_host`) of a lock takeover row and the `host` of a job owner. The `request` and
`error` text of a job row are copied as they are too, and they can hold a full file path.

**Embedded provenance.** Besides the provenance file, the subset stamps four root attributes
(readable with `h5py`), and two more with `curator_alias`:

| Attribute | Value |
|---|---|
| `tether_subset_of` | The **source filename only** (`path.name`), never a full path |
| `tether_subset_created_utc` | Offset-aware ISO-8601 UTC — byte-identical to the provenance file's `created_utc` |
| `tether_subset_include_raw` | `0` or `1` — an integer, not a bool |
| `tether_subset_n_molecules` | The exported count |
| `tether_subset_names_aliased` | `1`, only with `curator_alias` |
| `tether_subset_n_aliased_names` | The count of distinct names replaced, only with `curator_alias`. No name is recorded |

The root also carries `create_project`'s own `format="tether-project"`, `schema_version`
and `app_version`; the last is the version of the build that performed the *export*, not
the source's.

**Refusals.** `FileExistsError` when `out_path` exists and `overwrite=False`; `ValueError`
when the output resolves to (or is a hard link of) the source, when the selection is
empty, when the preflight fails, or when `curator_alias` is blank, contains a user name
stored in the source, or leaves no alias that is free of every source user name.

Provenance-file `parameters` for this export: `include_raw`, `include_rejected`, `molecule_keys`,
`n_molecules`, `trace_layers` and `n_trace_layers`, `idealization_models` and
`n_idealization_models`, `skipped_idealization_models`, `n_label_rows`, `n_reject_log_rows`.
With `curator_alias` also `curator_alias` (the prefix), `names_aliased` (`true`) and
`n_aliased_names`. It never records the names.

## 4. Plot export (PDF + SVG + PNG)

`tether.analysis.plot_export` splits rendering from writing: each `render_*` helper returns
a bare `matplotlib.figure.Figure` and writes nothing; `export_figure` is the only function
that touches disk. Figures are built with the object-oriented `Figure` API rather than
`pyplot`, so no GUI backend is selected and the base environment stays `matplotlib-base`
(ADR-0044). `tether.analysis` re-exports the module and its public names **lazily**: a
module-level `__getattr__` ([PEP 562](https://peps.python.org/pep-0562/)) imports
`plot_export` on the first access to one of them. `from tether.analysis import export_figure`
therefore works, and `import tether.analysis` alone still does not pull in Matplotlib. The
names are not in `tether.analysis.__all__`, so a star import does not pull it in either.

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

| Property | Value |
|---|---|
| Formats | `DEFAULT_PLOT_FORMATS = ("pdf", "svg", "png")` — all three by default. `formats=` may select a subset; any other value raises `ValueError`, and an empty tuple raises `ValueError("at least one export format is required")` |
| Write order | Always canonical `pdf, svg, png`, regardless of the order passed — the loop iterates `DEFAULT_PLOT_FORMATS` and skips |
| Filenames | The extension is **appended** to `out_stem`, never substituted: a stem `my.plot.v2` yields `my.plot.v2.pdf`. Nothing is derived from the plot kind, the title or the timestamp |
| Figure size | `DEFAULT_FIGSIZE = (6.4, 4.8)` **inches** |
| Raster resolution | `DEFAULT_EXPORT_DPI = 200`, PNG only — at the default figure size that is a 1280 × 960 px PNG. The vector formats are resolution-independent and ignore it |
| Background | Matplotlib's opaque white; `transparent=` is never passed |
| Determinism | `savefig` runs inside `rc_context({"svg.hashsalt": _SVG_HASHSALT})`, so SVG element ids are stable across processes and re-exporting a **freshly re-rendered** figure at the same pinned `created_utc` is byte-identical in all three formats. Re-exporting the *same* `Figure` object is **not**, because `stamp=True` draws another footer onto it — see the mutation callout below |
| Return value | `PlotExportResult(stem, paths, provenance_path, formats, data_paths)`; `data_paths` is empty unless `data_csv=True` |

A plot export is stamped **three** ways:

1. **The provenance file** `<stem>.provenance.json`, written by the same
   `write_provenance_sidecar`. `export_figure` merges three keys of its own into your
   `parameters`: `formats` (canonical order), `dpi`, and `outputs` (`{format: basename}` —
   basenames, not paths). With `data_csv=True` it also adds `data_tables`
   ([below](#the-numbers-behind-a-figure)). A figure drawn from several series also adds `series`, and a
   figure that records how it was drawn adds `display` (both below). The provenance file is
   written even when `stamp=False`.
2. **A visible on-figure footer**, when `stamp=True` (the default): two 6-point,
   55 %-opacity texts at the bottom of the figure — `Tether <version> · <kind>` on the
   left and the ISO timestamp on the right.
3. **Embedded document metadata**, per backend: PDF `Title`/`Author`/`Subject`/`Creator`/
   `CreationDate`; SVG Dublin-Core `Title`/`Description`/`Date`; PNG `tEXt` chunks
   `Software`/`Title`/`Description`/`Creation Time`. The `title=` argument fills only the
   document `Title` (defaulting to the `tether_export` kind) — it does not change the
   on-figure title or the filename.

> **`export_figure` mutates the figure you pass.** The footer is drawn onto the `Figure`
> object, so exporting the *same* figure twice with `stamp=True` leaves two overlapping
> footers with two different timestamps. Re-render before re-exporting.

The fourteen renderers, and the plot each produces (Appendix-C ids as used in the
[seven-plot parity gallery](../analysis/parity-gallery.md)):

| Renderer | Input dataclass | Plot | x-axis unit |
|---|---|---|---|
| `render_histogram1d` | `Histogram1D` or `HistogramBootstrapCI` (+ optional `ModelGaussianOverlay`) | A1 population histogram | Apparent FRET efficiency *E* (dimensionless) |
| `render_histogram2d` | `Histogram2D` | A2 time-vs-signal heatmap | `Time (s)` when `time_dt != 1.0`, otherwise `Frame` |
| `render_transition_density` | `TransitionDensityPlot` or `SmoothedTransitionDensity` | B1 transition-density plot | Initial *E* (dimensionless) |
| `render_dwell_survival` | `DwellTimeAnalysis` | B2 survival curve (+ residual panel when a fit succeeded) | `Dwell time (s)` when `dt != 1.0`, otherwise `Dwell time (frames)` |
| `render_transition_prob` | `TransitionProbHistogram` | B3 transition-probability histogram | Probability per molecule (dimensionless) |
| `render_state_number` | `StateNumberCounts` | C1 state-number distribution | Number of occupied states (a count) |
| `render_raw_fret_cloud` | `RawFretCloud` | QC raw FRET cloud | `Time (s)` when `time_dt != 1.0`, otherwise `Frame` |
| `render_cross_correlation` | `CrossCorrelation` | Donor–acceptor cross-correlation | `Lag (frames)` — always frames; there is no seconds variant |
| `render_per_condition_histograms` | `PerConditionHistograms` | Per-condition overlay: each condition's A1 histogram on one shared axis | Apparent FRET efficiency *E* (dimensionless) |
| `render_transition_sync_histogram2d` | `TransitionSyncHistogram2D` | A2b post-synchronized heatmap | `Time from transition (s)` when `time_dt != 1.0`, otherwise `Frames from transition` |
| `render_alpha_shape` | `AlphaShape` (+ optional `RawFretCloud`) | Alpha-shape support boundary of the raw FRET cloud | `Time (s)` when the frame duration is not `1.0`, otherwise `Frame` |
| `render_state_number_elbow` | `StateNumberElbow` | k-vs-RMSE elbow state-count hint | Number of clusters *k* (a count) |
| `render_population_anticorrelation` | `PopulationAnticorrelation` | Anticorrelation events, one row per molecule | `Frame (analysis window)` — always frames |
| `render_successive_frame_cloud` | `SuccessiveFrameCloud` | Successive-frame FRET cloud: *E* at frame *i* against *E* at frame *i* + 1 | *E* at frame *i* (dimensionless). The y-axis is *E* at frame *i* + 1 |

`render_transition_density` draws the B1 color scale as `color_scale="log"` by default, the
log-normalized TDP that the product specification names. Use `color_scale="linear"` for a
linear scale.
The log scale is zero-safe: an empty bin gets the floor color of the color map. The floor
is one decade below the smallest non-empty bin, so a rare transition does not disappear next
to a frequent one. The scale is display-only: the drawn array is the stored histogram.
`export_figure` writes the scale to the provenance file as `parameters.display.color_scale`. You do
not have to pass it. If you pass a `display` entry that does not agree with the figure,
`export_figure` raises `ValueError`.

To draw the TDP as published figures show it, pass the smoothed view instead of the counts:
`render_transition_density(smooth_transition_density(tdp))`. The view is the counts
Gaussian-filtered, interpolated onto an 800 × 800 grid and normalized to their maximum, with the
settings of the `checkTDP.py` published by Campos-Chavez et al. 2024 (see the
[parameter reference](parameters.md)). It is drawn as an image on that grid. On the log scale its
floor is 10⁻⁴ of the peak, the color floor of `checkTDP.py`. `export_figure` writes the smoothing
settings to the provenance file as `parameters.display.smoothing`. The counts are not changed.

> **A time axis says seconds only if you supplied a frame duration.** `dt` / `time_dt`
> default to `1.0`, and at that default the renderer labels the axis in frames. The same
> applies to rates read off a B2 survival fit: `tether.analysis.dwell` reports them in
> `1 / (frames · dt)`, i.e. **per frame** at the default.

`render_per_condition_histograms` draws every condition as an unfilled outline on the
overlay's one shared bin grid, in the overlay's own order, and labels each curve with its
condition id and molecule count. A condition binned on another grid raises `ValueError`
rather than being drawn with its bins in the wrong place.

`render_transition_sync_histogram2d` draws time relative to the selected transition: the
axis runs negative before it, and a dotted line marks relative time zero. The note names
the selected states, with `any` for `-1`.

`render_alpha_shape` draws the boundary segments and nothing else of the shape. When there
is no boundary, it draws no line and the note tells you why: the cloud spans no 2-D region,
or the alpha was too small to keep a triangle. Pass `cloud=` to draw the points underneath
and take the axis limits and time unit from the cloud. Without a cloud, pass `time_dt=` for
a time axis in seconds.

`render_state_number_elbow` draws the RMSE curve, the chord from its first to its last
point, and the elbow when there is one. The elbow is a hint and the figure says so: the
state count of record is the model's.

`render_population_anticorrelation` gives each scanned molecule one row, in store order. An
event is a bar over its frames, colored by its peak `|lag-0|`, with a tick at its peak
frame. A molecule with no event keeps its empty row. The rows carry the `molecule_key` up
to 30 molecules and an index above that.

`render_successive_frame_cloud` draws five things. They are the density surface, the
highest-density-region contours, the pairs as points, the k-means centroids as crosses and the
diagonal *E*(*i* + 1) = *E*(*i*) as a dashed line. It is not the raw FRET cloud, which draws *E*
against time.

It thins its points to `max_points` (default 20 000), as the raw cloud does. The density and the
centroids come from every pair. Each series record in the provenance file holds
the cloud's own analysis provenance under `analysis`. That record gives every parameter and the
`molecule_key` of each molecule in the cloud. A bare cloud, drawn without a series label, writes
the same record under `parameters.analysis`, so the `molecule` positions of its `data` table
still lead back to their molecules (#1418).

`render_raw_fret_cloud` decimates its scatter overlay when the population exceeds
`max_points` (default 20 000) to keep the vector file manageable; the density surface
underneath is untouched by that decimation. It is not, however, fit on every point:
`raw_fret_cloud` keeps all finite samples in `points`, but fits the KDE — and the HDR
levels derived from it — on the samples inside `time_range` × `signal_range` only, so
un-clipped apparent-E outliers cannot inflate the bandwidth. The excluded count is
`n_out_of_range`; the on-figure annotation reports only `n_samples` and `n_molecules`,
so read it off the dataclass.

### Several series on one figure

Every renderer also takes an ordered sequence of `(label, result)` pairs. These are the series of
one composed plot (FR-PLOT). Each label must be a distinct, non-empty string. A sequence of one
series draws exactly the figure of the bare result, byte for byte. Its label goes only to the
provenance file.

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

- **Overlay.** Seven renderers draw all series on one axis, with a legend entry for each: the 1-D
  histogram, the dwell survival, the transition probability, the state-number chart (as grouped
  bars), the cross-correlation, the alpha shape and the elbow. The series take their colors from
  one cycle, `SERIES_COLOR_CYCLE = "okabe-ito-4"`. It has four colors of the Okabe–Ito set: blue,
  vermilion, bluish green and reddish purple. After them come the same four with the next line
  style (`--`, `-.`, `:`), so 16 series differ. Where a series can be drawn as points alone, its
  marker (`o`, `s`, `^`, `D`) steps with the line style, as the dwell survival markers do. `series_style(k)` returns the style of series `k`.
  Each color reaches 3 : 1 against the white background. Every two colors also stay apart under
  simulated protanopia, deuteranopia and tritanopia, the check the [trace colors](gui-theme.md)
  pass.
- **Small multiples.** Where one series would hide another, each series gets its own panel, titled
  with its label. This applies to the time-vs-signal heatmap, the transition-density plot, the raw
  FRET cloud, the transition-synchronized heatmap, the per-condition overlay and the
  anticorrelation raster. The panels share their axes. The anticorrelation panels share only the
  frame axis: each has its own molecule rows, so one row position is not one molecule across
  panels. The heatmaps and the raster share one color scale and one color bar, so a color means
  the same value in every panel. In the per-condition
  panels, a condition keeps its color in every panel.
- **One grid.** Two series that are not on one grid are refused with a `ValueError`. It names the
  series, what differs and both values. Tether never resamples one series onto another. What must
  agree depends on the plot. Examples are the bin edges, value range and density normalization of
  a histogram, the frame time of anything with a time axis, the `init → final` pair of a
  transition-probability histogram and the lags of a cross-correlation. A lever of the series, such
  as per-molecule equal weight, may differ.

`export_figure` writes one record per series to `parameters.series`, in figure order. A record
holds the `label`, the `result` type, the `parameters` that result carries and its `display`. The
parameters are the scalars, ranges and grid of the result, never its data. The display is the color
and line style, or the panel. A one-series figure is the bare result's figure, so its record says
`{"as_bare_result": true}`. The layout goes to `parameters.display` (`series_layout`, and
`series_cycle` or `series_color_scale`).

A model drawn with a series keeps its own parameters in the record. Examples are the Gaussian
means, variances and weights, or the fitted rates. Its sampled curve is data and is left out. A
series drawn without its model overlay or cloud records `null` for it.
A parameter that is NaN or infinite, such as the `alpha` of an empty alpha shape, is recorded as
`null`, so the file stays strict JSON. So is one in a key you add.

To add your own keys to a series, such as the selection
and its version, pass `parameters["series"]` as a list of one dict per series. A key that
contradicts what was drawn raises `ValueError`. ADR-0132 records these choices.

### The numbers behind a figure

Pass `data_csv=True` to `export_figure` to write the numbers the figure shows beside it. Use
them to replot in your own style, to compare in another program, or as a journal's source data.
The figure files are the same bytes with or without the option.

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

Each `render_*` helper records on the figure the result it drew. The tables come from that
result, so they are the values plotted and not a second computation. The record is a copy made
when the figure is drawn. A result you change later does not change the tables. A figure you drew yourself carries no
such record, and `data_csv=True` raises `ValueError` before it writes any file.
`tether.analysis.plot_data` builds and writes the tables.

**Files.** The marks of the figure (its bars, cells, points or segments) are the main table,
`<stem>.csv`. Other parts of a figure have a grid of their own: a curve drawn over the marks, the
parameters of a fit, a density surface and its contour levels. Each of them is a table of its
own, `<stem>.<name>.csv`.
`result.data_paths` maps each table's name to its file, and the provenance file lists them under
`parameters.data_tables` (`{name: basename}`).

**Layout.** Every table is in long form: one row per bin, cell, point or segment. A 2-D result
gives one row per cell, `x_low, x_high, y_low, y_high, value`, with `x` varying slowest.

A figure of several series gives one table, in figure order. Its first column, `series`, holds
each row's series label. A figure of a bare result has no `series` column. Sometimes one series
has a column that another lacks, such as the bootstrap interval of one histogram among plain
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
figure of several series adds one `# series N:` line per series. The line is a JSON object with
the series `label` and the `selection` you gave in `parameters["series"]`.

`units` names the unit of each
column. A time axis is in seconds when the result has a frame time other than `1.0` and in frames
otherwise, as on the figure. Read a table with `pandas.read_csv(path, comment="#")`. Add
`float_precision="round_trip"` to get back the exact values written.

**Cells.** A float is written by Python `repr`, the shortest text that reads back to the same
number. So a table read back equals the result exactly. NaN and infinity are blank cells, as in
the molecule-table CSV. Every text cell is quoted, so a `#` in a series label is never read as
the start of a comment.

The values are the stored values. The figure clips the survival markers at 10⁻⁶ for its log
axis, and it may thin the cloud points to `max_points`. The tables hold them in full.

The tables of each plot:

| Renderer | Table | Columns and units |
|---|---|---|
| `render_histogram1d` | `data` | `bin_left`, `bin_right`: the bin edges, *E* (dimensionless). `value`: counts, or density when the histogram is normalized. `ci_low`, `ci_high`: the bootstrap interval, in the unit of `value`, for a `HistogramBootstrapCI` |
| | `model` | With a model overlay. `curve`: `total`, or `state 1`, `state 2`, … for each state's Gaussian. A figure of several series draws, and writes, the total only. `x`: *E* (dimensionless). `value`: in the unit of the histogram's `value` |
| `render_histogram2d` | `data` | `x_low`, `x_high`: time, in s or frames. `y_low`, `y_high`: *E* (dimensionless). `value`: counts or density |
| `render_transition_density` | `data` | Counts: `x_low`, `x_high` (initial *E*), `y_low`, `y_high` (final *E*), `value` (counts or density). The smoothed view: `x` (initial *E*), `y` (final *E*), `value` (normalized density with peak 1, or smoothed counts or density), one row per point of its grid, which is 800 × 800 by default |
| `render_dwell_survival` | `data` | `time`: the dwell time, in s or frames. `survival`: the fraction of dwells longer than `time` |
| | `model` | With a successful fit. `curve`: `model_survival` or `residual`. `x`: the dwell time, in s or frames. `value`: a fraction of dwells |
| | `fit` | With a successful fit, one row per parameter. `model`: the fit model. `parameter`: `rate_1`, `rate_2`, …, `amplitude_1`, …, `beta` (the stretched model) and `r_squared`. `unit`: the parameter's unit, `1/s` or `1/frame` for a rate and `dimensionless` otherwise. `value`, `stderr` (1 σ) and `ci_halfwidth`: in that unit. `ci_level`: the confidence level of `ci_halfwidth`, a fraction |
| `render_transition_prob` | `data` | `bin_left`, `bin_right`: the transition probability per molecule (dimensionless). `value`: counts or density |
| | `model` | With a KDE. `curve`: `kde`. `x`: the probability (dimensionless). `value`: density |
| `render_state_number` | `data` | `n_states`: the number of occupied states. `molecules`: how many molecules have that many |
| `render_raw_fret_cloud` | `data` | `time`: in s or frames. `e`: *E* (dimensionless). One row per point of the cloud |
| | `density` | With a density surface. `x_low`, `x_high`: time, in s or frames. `y_low`, `y_high`: *E*. `value`: the KDE density |
| | `levels` | With HDR contours. `coverage`: the fraction of the density inside the contour. `level`: the KDE density of the contour |
| `render_cross_correlation` | `data` | `lag`: in frames. `value`: the correlation, under the normalization the units line names |
| `render_per_condition_histograms` | `data` | `condition`: the condition id. `bin_left`, `bin_right`: *E* (dimensionless). `value`: counts or density |
| `render_transition_sync_histogram2d` | `data` | `x_low`, `x_high`: time from the transition, in s or frames. `y_low`, `y_high`: *E*. `value`: counts or density |
| `render_alpha_shape` | `data` | One row per boundary segment. `edge`: its number, from 1. `time_start`, `time_end`: in s or frames. `e_start`, `e_end`: *E* (dimensionless). An empty shape has no rows |
| | `points` | With `cloud=`: the cloud's points, as the `data` table of `render_raw_fret_cloud` |
| `render_state_number_elbow` | `data` | `k`: the number of clusters. `rmse`: the within-cluster RMSE of *E*. `elbow`: `1` for the suggested *k*, otherwise `0` |
| `render_successive_frame_cloud` | `data` | One row per pair. `molecule`: the molecule's position, from 0, in the `molecule_keys` of the cloud's provenance. `frame`: the frame *i* of the pair, counted from the start of the molecule's analysis window. `e_i`, `e_next`: *E* at frame *i* and at frame *i* + 1 (dimensionless) |
| | `density` | With a density surface, one row per grid node. `x`: *E* at frame *i*. `y`: *E* at frame *i* + 1. `value`: the KDE density |
| | `levels` | With HDR contours. `coverage`: the fraction of the density inside the contour. `level`: the KDE density of the contour |
| | `centroids` | With centroids, one row per centroid. `centroid`: its number, from 1. `e_i`, `e_next`: its *E* at frame *i* and at frame *i* + 1 |
| `render_population_anticorrelation` | `data` | One row per event. `row`: the molecule's row on the figure, from 0. `molecule_key`. `start`, `stop`: the event's first frame and the frame after its last, in the analysis window. `peak_frame`: in frames. `peak_magnitude`: the peak \|lag-0\| Pearson correlation (dimensionless). A molecule with no event has no rows |

Three later additions are not in these files yet: the result snapshot of
[#943](https://github.com/bioedca/tether/issues/943), the figure footer of
[#946](https://github.com/bioedca/tether/issues/946) and the interval method of
[#947](https://github.com/bioedca/tether/issues/947). Each of those issues adds its part when it lands.

<a id="provenance-sidecar"></a>

## Provenance file

Every one of the four exports writes `<file>.provenance.json` beside its output, via
`tether.project.export.write_provenance_sidecar`. The name is formed by *appending* to the
full filename, so the original extension survives: `molecules.csv.provenance.json`,
`traces.txt.provenance.json`, `subset.tether.provenance.json`. Plot exports pass the
extension-less stem, so theirs is `<stem>.provenance.json`.

[`tether-provenance-schema.json`](tether-provenance-schema.json) states the keys below as a
JSON Schema, with the `parameters` of each export.

The payload is `json.dumps(payload, indent=2, sort_keys=True) + "\n"`, UTF-8 — which is
why the keys read alphabetically:

| Field | Content |
|---|---|
| `app_version` | The git-derived `tether.__version__`, falling back to `"0.0.0+unknown"`. A provenance stamp never raises |
| `created_utc` | Offset-aware ISO-8601 UTC with microseconds (`datetime.now(UTC).isoformat()`), e.g. `2026-07-20T22:14:26.314669+00:00`. A caller may pin it, which is how a subset's in-file attribute and its provenance file agree exactly |
| `parameters` | The export's own parameters — different per export; the per-export lists are given above |
| `source_project` | For the three project exports, the source `.tether` **basename only** — they call `write_provenance_sidecar(source=path.name)`. For `export_figure` it is **caller-supplied and unnormalized**: the `source=` argument is forwarded verbatim, and `write_provenance_sidecar` does no `.name` reduction of its own, so an absolute path you pass is an absolute path in the JSON. Pass a basename yourself |
| `tether_export` | The export-kind discriminator |

`tether_export` takes exactly three fixed values from the project exports —
`"deeplasi-txt"`, `"molecule-table-csv"`, `"subset-tether"`. For plots it is **caller-supplied**.
Nothing in `src/` defines a plot kind vocabulary. But `export_figure` refuses the three project
kinds with `ValueError`. The [provenance schema](tether-provenance-schema.json) reads them as
project exports, so a figure sidecar under one would fail it. Use a `plot:` kind, such as
`"plot:tdp"`.

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

The provenance file exists because flat text has no metadata slot and the Deep-LASI `.txt` must
stay header-free — so the stamp travels beside the file rather than inside it (ADR-0001). Keep it with the data file when you move or share the export. For
the CSV and the `.txt` it is the **only** record of which build, which project and which
parameters produced those numbers. A subset `.tether` and a plot export also carry a stamp
*inside* the file — the subset's `tether_subset_*` root attributes plus
`create_project`'s `app_version`, and a plot's on-figure footer plus its embedded document
metadata (both above) — but only the provenance file carries the full parameter set.

To read a provenance file back next to the stamps inside the project, and to turn both into a
Methods paragraph, see [Reproducibility and reporting](../reproducibility.md).

## Put these files in your deposit

A CSV or `.txt` export is flat text. To let another person read it correctly, put these
files beside your data when you deposit it. Download them from the documentation of the
Tether version that wrote your exports, so that they describe that version:

| File | What it describes |
|---|---|
| [`tether-schema.json`](tether-schema.json) | The frozen skeleton of a `.tether` file: its groups, its four compound tables and its root attributes. It is `schema/schema_frozen.json` from the repository, unchanged ([format reference](tether-format.md)). |
| [`molecule-table-columns.csv`](molecule-table-columns.csv) | The data dictionary of the molecule-table CSV: one row per column, with `name`, `type`, `unit`, `can_be_blank` and `description` ([columns](#molecule-table-columns)). |
| [`tether-provenance-schema.json`](tether-provenance-schema.json) | A JSON Schema (draft 2020-12) of the `<file>.provenance.json` provenance file ([above](#provenance-file)). Any JSON Schema validator can check a provenance file against it. |

These three files are licensed **CC0-1.0**, so you can copy them into a deposit with no
conditions. The program code keeps its GPL-3.0-or-later license.

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
`acceptor_bleach_frame`, `frame_start`, `frame_end`, `window_start`, `window_end`,
`n_finite_frames`. The other five numeric columns — `quality_class`, `alpha`, `gamma`,
`delta`, `correction_confidence` — plus `mean_apparent_e` and `median_apparent_e` are
floats that may be blank. Everything else is a string, including `curation_label`, and
`tags` arrives as a single comma-joined cell you must split yourself.

### MATLAB, Prism, Origin, Excel

The CSV is plain UTF-8 with exactly one header row, a comma delimiter and standard `"`
quoting, so `readtable` and every spreadsheet importer handle it directly. Two things to
set on import: keep blank cells as missing rather than `0`, and treat `molecule_id` /
`molecule_key` as text — `molecule_key` is a 64-character hex digest and will be mangled
by a numeric or scientific-notation guess.

The Deep-LASI `.txt` is whitespace-delimited with **no header**, so `readmatrix` /
`numpy.loadtxt` read it as a plain `T × 2N` numeric matrix. Pass `ndmin=2` to
`numpy.loadtxt` — a single-frame export is a one-row file, which plain `loadtxt`
collapses to a 1-D array; Tether's own `read_deeplasi_txt` reads it as
`np.loadtxt(path, dtype=np.float64, ndmin=2)` for exactly that reason. `readmatrix`
needs no equivalent flag. Counting molecules from zero, molecule *k*'s donor is
0-based column `2k` and its acceptor `2k + 1` — in MATLAB's 1-based indexing, columns
`2k + 1` and `2k + 2`.

### Frames to seconds

No *project* export carries the frame duration — only a plot export can, and only as a
rendered seconds axis you cannot read a number back out of. The duration lives in the
source project, **per movie** (`/movies.frame_time`, `tether.io.schema.MOVIES_DTYPE`), and
every CSV row names its movie in `movie_id`. So the conversion is a per-row join, not one
global constant:

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

Three caveats — the two functions raise on each of them rather than return a plausible
wrong number:

- **A project may hold more than one movie, each with its own `frame_time`.** Extraction
  appends one `/movies` row per movie, so reading `movies["frame_time"][0]` and applying
  it to every exported row silently mis-scales every molecule that came from a later
  movie. Key by `movie_id`, as above. On a two-movie project file at `0.1` and `0.25` s/frame,
  frame 8 is `0.8 s` and `2.0 s` respectively; the first-row shortcut reports `0.8 s` for
  both.
- **A stored `0.0` means *unknown*, not "instantaneous".** The GUI treats a non-positive
  `frame_time` as absent and falls back to a frame axis (`tether.gui.shell`), and
  `frames_to_seconds` above refuses rather than handing back a column of zeros labeled
  seconds.
- **Some rows have no movie to key on at all.** A **subset `.tether` has zero `/movies`
  rows** by construction, and an **analysis-only import** has none either and writes
  `movie_id = ""` (see the callout under
  [Molecule-table columns](#molecule-table-columns)). Both land in the `KeyError`: the
  factor has to come from the parent project or from the acquisition metadata, because
  Tether never stored one.

### Idealization state paths in a subset

A subset carries whole `/idealization/{model}` groups. In `state_path`, the sentinel
**`-1`** (`tether.idealize.NO_STATE`) marks a frame with **no assigned state** — outside
the analysis window, or an interior gap — and the matching `idealized` cell is `NaN`. It
is a sentinel, not state number −1: exclude those frames rather than treating them as an
extra state. Tether's own analyses do exactly that (`tether.analysis.tdp`,
`tether.analysis.state_number`, `tether.analysis.transition_prob`).

### How a `NO_STATE` gap changes the dwells

A `NO_STATE` frame is **never itself a dwell and never a transition**. What it does to the run
around it depends on which function reads the path:

- `tether.analysis.dwell.state_dwells` does **not split a run** at it. It *strips* the
  `NO_STATE` frames before splitting on state change. This is tMAVEN's NaN strip, kept for parity
  ([`test_state_dwells_strips_no_state_like_tmaven`](https://github.com/bioedca/tether/blob/main/tests/test_analysis_dwell.py)).
  So two same-state segments either side of an interior gap are stitched into **one run**.
- `tether.idealize.dwells_from_states` does the opposite: a `NO_STATE` frame **ends** the run
  it interrupts (its docstring: "they break a run but are not dwells").

**What a merged dwell's length means.** `state_dwells` reports a run's length as the number
of frames that survived the strip. So a merged dwell's length is the **sum of the observed
in-state frames on both sides of the gap**. The gap frames are discarded:

```python
import numpy as np
from tether.analysis.dwell import state_dwells
from tether.idealize import NO_STATE

path = np.array([0, 0, 0, NO_STATE, NO_STATE, NO_STATE, NO_STATE, 0, 0, 0, 1, 1, 1, 0])
state_dwells([path], no_state=NO_STATE, include_first=True).lengths
# {0: array([6]), 1: array([3])}
```

The state-0 frames of the merged run are indices 0–2 and 7–9. That is a dwell length of
**6 frames**, across an elapsed span of **10 frames** from the first of them to the last. A
merged dwell is therefore longer than either segment alone. It is shorter than the time elapsed
between entering the state and last being seen in it. Which of those the molecule actually did
is what the gap makes unobservable. It is not a residence time in either direction.

**A merge is reported only when the merged run is retained.** `state_dwells` always drops a
molecule's last run, and drops its first unless `include_first` (see
[dwell time](../glossary.md#dwell-time-dwell-time-survival)). It also skips a molecule left with
a single run. So a merged run is reported only when it is neither the molecule's sole run nor
its last. When it is the first, it is reported only at `include_first=True`.

The bare trace `0,0,NO_STATE,0,0`, whose merged run is the molecule's only run, yields **no
dwell at all**, at either `include_first` setting. A later transition makes the merged run the
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
runs instead of stitching them, with identical first and last handling. Tether does not
implement that mode.

- The sample never gains a dwell, and the pooled frame total never rises. Each merge removes
  one of its molecule's retained dwells until the molecule has none left to lose. At the
  default `include_first=False`, the bare `0,0,NO_STATE,0,0` above retains none either way.
  Each of its segments is a first or a last run, so its merge removes nothing.
- The pooled mean dwell `⟨τ⟩` **rises** when both joined runs would have been retained anyway,
  because the same frames now sit in one fewer dwell. In the first path above, state 0 goes from
  two 3-frame dwells (mean 3 frames) to one 6-frame dwell (mean 6 frames).
  `pooled_exit_rates`' `k = 1/⟨τ⟩` **falls** with it, from 0.333333 to 0.166667 per frame at
  `dt = 1`.
- The merged run can land in the always-dropped last slot, or in the first slot at the default
  `include_first=False`. Then the segments it absorbed leave the sample, frames and all, and
  `⟨τ⟩` has **no fixed direction**. It rises if what was removed was shorter on average than the
  state's pooled dwells. It falls if longer, and is unchanged if equal. `k = 1/⟨τ⟩` moves the
  other way. At the extreme the molecule is left with one run and contributes nothing.
  For example, `0,0,NO_STATE,NO_STATE,0,0` returns `{}` at `include_first=True`, where splitting would yield
  one 2-frame dwell of state 0. A state can drop out of the results altogether.
- Over a population both kinds of merge can occur at once. So the merge alone does not fix the
  net direction of `⟨τ⟩`, or of `k`.
- No direction is claimed for the fitted survival lifetime. `population_dwell_times` hands this
  pool to `survival_curve` and `fit_survival`, so the fit moves with the pool. But a
  least-squares survival fit is not the reciprocal of a mean.

**Which dwell surfaces see a gap is decided by their callers**, not by the functions. Both
`population_dwell_times` and `tether.analysis.kinetics.pooled_exit_rates` call `state_dwells`
with the sentinel `-1`, which *is* `NO_STATE`.

- `population_dwell_times` reads its paths through [`tether.analysis._store.windowed_states`](https://github.com/bioedca/tether/blob/main/src/tether/analysis/_store.py).
  That function cuts each molecule's analysis window between its first and last non-sentinel
  frame, but keeps any interior gap. So it **merges**.
- `pooled_exit_rates`' only caller in Tether's source is `two_state_rate_constants`. Its paths
  come from `viterbi_paths`, an `argmax` over the model's states that never emits the sentinel,
  so nothing merges there. But `pooled_exit_rates` is public API: hand it a stored analysis
  window and it merges identically.

A gap between two *different* states is not merged. The strip makes the runs either side of it
adjacent, so the transition is kept. But the transition happened somewhere inside the gap, and
its timing was not observed. Each of those two dwells counts only its observed frames.

On the [TDP](../glossary.md#tdp-transition-density-plot) side the gap becomes `NaN` and the non-finite pair
is dropped, so a `NO_STATE` border never emits a transition point.

**Why the first and last dwells are dropped.** The two are dropped for different reasons. The
**last** dwell is right-censored: the trace ends before the molecule is seen to leave that state.

The **first** is a different case. Its start was not observed, because the molecule was already
in that state when the analysis window opened. Its end is a recorded transition. That exit is
observed only when no interior `NO_STATE` gap separates the first dwell from the next state.
Across such a gap the exit happened somewhere inside it.

### Why a subset `.tether` is readable at all

The `.tether` project file is a **superset of the tMAVEN SMD layout**, not a private format
(ADR-0002, [The `.tether` store is an SMD superset](../adr/0002-smd-superset-round-trip.md)).
It is HDF5: `h5py`, MATLAB `h5read` and `rhdf5` all open it without Tether installed. The
same property is what makes the tMAVEN round trip a data-model consequence rather than a
conversion step.

## References

- The [product specification](https://github.com/bioedca/tether/blob/main/docs/PRD.md) in the
  repository — the export surface; the requirement that "all exports are stamped with
  provenance and parameters"; the subset invariant; the curation filter; `molecule_key`
  non-uniqueness.
- **ADR-0001** — provenance-first project store.
- **ADR-0002** — the `.tether` store is an SMD superset; round-trip is a data-model property.
- **ADR-0005** — the schema freeze (additive-only), which the subset export honors.
- **ADR-0008** — the Deep-LASI → Tether correction-factor remap (β→α, α→δ, γ→γ).
- **ADR-0016** — the extraction trace-store layout: zero-pad-to-max-`T` traces and the
  `molecule_key` content hash.
- **ADR-0024** — the `/idealization/{model}` store layout a subset carries.
- **ADR-0044** — `matplotlib-base` as the static vector plot-export backend.
- [Legacy Deep-LASI import](../io/legacy-import.md) — the input side of the same story.
- [Standalone-tMAVEN hand-off](../idealize/standalone-tmaven-handoff.md) — the SMD
  interchange leg, which is not an export.
- [Seven-plot parity gallery](../analysis/parity-gallery.md) — what each renderer plots and
  the test that pins its parity.

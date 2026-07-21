# Exports — what Tether writes out

**Who this page is for.** Anyone who has a file Tether produced — a molecule-table CSV
about to become a figure, a `…-donc-accc-w.txt` handed to a collaborator, a subset
`.tether` mailed to a reviewer, a PDF pasted into a manuscript — and needs to know
exactly what is in it: which columns, in which order, in which units, and what a blank
cell means. It is a reference, not a tutorial. Every claim below names the module,
function or constant that makes it true, so you can grep the source rather than trust
the prose.

Two facts frame everything else:

- **Every temporal number in the three *store* exports (CSV, `.txt`, subset `.tether`) is
  in *frames*, zero-based, never seconds**, and none of the three carries the frame
  duration. The store keeps it (`/movies.frame_time`, `tether.io.schema.MOVIES_DTYPE`),
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
| `tether.project.export.export_subset_tether` | `<name>.tether` (HDF5) + `<name>.tether.provenance.json` | You want a portable, self-contained re-analysis of a *selection* — coordinates, patches, corrected traces, idealization models — without shipping the movie |
| `tether.analysis.plot_export.export_figure` | `<stem>.pdf`, `<stem>.svg`, `<stem>.png` + `<stem>.provenance.json` | You want a publication figure of an analysis plot: vector PDF **and** vector SVG **and** raster PNG, all stamped |

> **There is no CLI or GUI export command, and this page will not invent one.**
> `tether.cli` registers exactly two subcommands, `extract` and `batch`, and no module
> under `src/tether/gui/` references any of the four functions above. They are a Python
> API today. Every snippet on this page is therefore the call you actually run.

The tMAVEN hand-off (`tether.project.handoff.hand_off_to_tmaven`, reachable from the GUI)
also writes a file — an SMD-HDF5 — but it is an *interchange* leg with a return path, not
an export, and it does **not** write a provenance sidecar. It is documented on
[Standalone-tMAVEN hand-off](../idealize/standalone-tmaven-handoff.md).

## What all three store exports share

### Selection

All three take their rows from `_selected_rows` in `tether.project.export`, which means:

| Behavior | Detail |
|---|---|
| Order | **Store order** (ascending row index). A `molecule_keys` request does *not* reorder the output |
| Curation filter | Rows whose `curation_label` is `CurationLabel.REJECT` (`-1`, `tether.project.labels`) are dropped unless `include_rejected=True` |
| `molecule_keys` | A membership subselect, not a lookup. `molecule_key` is **not unique**, so one requested key matches *every* store row carrying it |
| Unknown key | A requested key matching no store row raises `KeyError` — a typo fails loudly rather than silently under-exporting |
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
*is*, though, depends on how the store was built — and no export records that:

| Store built by | What an exported intensity is |
|---|---|
| **Native extraction** — `tether.project.extract.extract_movie` → `tether.imaging.extract.write_extraction` | An **integrated camera value in the movie's own pixel units**, produced here by `tether.imaging.aperture.integrate_traces` over the aperture below: counts/ADU, tied to Tether's own extraction settings |
| **Deep-LASI reconstruction** — `tether.project.reconstruct.reconstruct_project` | The **pre-integrated Deep-LASI `.mat` series, stored verbatim**. `_traces_from_export` maps `donc`/`accc` → the corrected layer, `don`/`acc` → raw, `bdon`/`bacc` → background; its docstring states that "the aperture integration `extract_molecules` would run is skipped". The units are whatever Deep-LASI's own integration produced |
| **Analysis-only import** — `tether.project.analysis_import.import_analysis_only_project` | The **SMD / `.txt` intensity series, stored verbatim** as the corrected pair (`_write_corrected_traces`), the module's "apparent-E analysis substrate". No `raw` or `background` layers are synthesized — there is "no movie to decompose the intensities against" |

Only the first row is a number Tether itself measured. For the other two the units are the
upstream tool's, and the aperture parameters below describe the *provenance* of the
imported layers rather than an integration Tether performed.

Which of the three layers you get is not the same number:

| Layer | What the value is | Sign |
|---|---|---|
| `*_corrected` (`intensity`) | The top-hat: the disk sum minus the scaled ring background, `intensity = tot - bg` | **May be negative** — a background is subtracted |
| `*_raw` (`total`) | The bare disk sum `tot` over the PSF disk, nothing subtracted | Non-negative for a non-negative movie |
| `*_background` (`background`) | The subtracted term `bg` alone — carried only by a subset with `include_raw=True` | Non-negative for a non-negative movie |

The mapping is the frozen `_QUANTITIES` tuple in `tether.imaging.extract`, annotated
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
> about the window centre, so `n_psf` is 29 for *every* legal `window` — the window only
> has to be odd and wide enough for the ring (`2 * ring_outer <= window`). Changing
> `disk_radius` is what re-scales an intensity: `3.0 → 29 px`, `4.0 → 49 px`, `5.0 → 81 px`.
> The `tether extract --window` flag changes the crop-box side length and leaves `n_psf`
> at 29; `disk_radius`, the ring radii and `bg_window` have **no CLI flag** and are
> reachable only through the Python API (`ExtractOptions`,
> `integrate_traces(disk_radius=…)`). The effective geometry is stamped into
> `/settings/extraction` (`window`, `disk_radius`, `ring_inner`, `ring_outer`,
> `bg_window`, `n_psf` — `tether.imaging.extract._write_settings_once`), which travels in
> a subset `.tether` but is carried by **neither** the `.txt` nor its sidecar.

> **An imported store's `/settings/extraction` is provenance, not a measurement.**
> `reconstruct_project` hands `write_extraction` the standard constants
> (`_APERTURE_WINDOW = 21`, `_APERTURE_DISK_RADIUS = 3.0`, `_APERTURE_RING_INNER = 6.0`,
> `_APERTURE_RING_OUTER = 8.0`, `_APERTURE_BG_WINDOW = 10`), annotated in the source as
> describing "the *provenance* of the layers, not a fresh integration this module
> performed". An analysis-only import writes **no** `/settings/extraction` at all — its
> `/settings` children are `analysis_only` and `correction` — and, carrying neither
> `raw`/`background` layers nor `/patches`, it can leave only as the CSV or the corrected
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
| Rows | One per exported molecule, in store order — one `/molecules` row = one detected donor spot in one movie, or, in an analysis-only import, one imported SMD / `.txt` trace |

**Numbers.** Integer columns go through `int()` and print with no decimal point. Float
columns go through `_fmt_float`, which returns `""` for `None` or any non-finite value and
otherwise `repr(float)` — Python's shortest round-tripping representation, full precision
and no fixed decimal count. That is why a real exported cell carries all its significant
digits: the row-0 `mean_apparent_e` of the three-molecule store used for the `.txt`
sample below (`tests/_analysis_store.build_store_with_channels` over the `_asym_channels`
values of `tests/test_export_tables.py`) reads `0.3715803781168891`, not `0.372`.

> **A blank cell means "not finite / not set", never zero.** `_fmt_float` maps both `None`
> and `NaN` to the empty string. Reading the CSV with a tool that coerces blanks to `0`
> will silently turn "no γ was ever estimated" into "γ = 0".

### Molecule-table columns

The order is frozen in `tether.project.export.MOLECULE_TABLE_COLUMNS`, annotated in the
source as *"the per-molecule CSV column order (frozen; a reader may key on these names)"*.
`tests/test_docs_export_columns.py` fails if this table and that tuple ever disagree. The
underlying field types are the frozen `tether.io.schema.MOLECULES_DTYPE`.

| Column | CSV type | Unit / domain | Blank when |
|---|---|---|---|
| `molecule_id` | string | Unitless identity — a stable per-row UUID, `mol-` + 32 hex chars. Unique within a store | The stored string is empty |
| `molecule_key` | string | Unitless identity — 64-char lowercase hex SHA-256. For a store built from a movie: of `"{movie_sha256}\|{qx}\|{qy}"` with `donor_xy` quantized (`tether.imaging.extract.molecule_key`). For an analysis-only import there is no movie and no coordinate, so `tether.project.analysis_import._analysis_only_molecule_key` hashes the source id, the row index and the trace bytes instead — same shape, different inputs (callout below). The cross-file join key; **not unique** (§7.10) | The stored string is empty |
| `movie_id` | string | Unitless identity of the source `/movies` row (e.g. `mov-1`). `""` in an analysis-only project, which has no linked movie — `tether.project.analysis_import` writes the empty string explicitly | No movie is linked |
| `source_filename` | string | The filename the row's provenance was parsed from (`tether.io.filename.parse_filename`): the source **movie** for a native extraction or a Deep-LASI reconstruction, the imported **SMD / `.txt`** for an analysis-only import | The stored string is empty |
| `condition_id` | string | Unitless condition key (e.g. `cond-353fd5a76531`) | The stored string is empty |
| `condition_id_provisional` | string | Unitless — the provisional key parsed from the filename at extraction, retained verbatim across any later re-key | The stored string is empty |
| `curation_label` | string | **Text, not the stored integer**: `accept`, `uncurated`, `reject` (`_CURATION_TEXT` over `tether.project.labels.CurationLabel` = `1 / 0 / -1`). An unrecognized integer falls through to its decimal string | Never — one of the values above is always written |
| `category` | string | Free text, editable per condition. Emitted verbatim, so a comma inside it is CSV-quoted | The stored string is empty (the extraction default `""`) |
| `quality_class` | float | The read-only ML quality score. Its numeric range is **not** defined by the export or the schema, which call it only a "read-only ML output" | The value is `NaN`. Extraction and analysis-only import both initialize it to `NaN` (`tether.imaging.extract`, `tether.project.analysis_import`) and no shipped code path writes anything else, so this column is blank in every export the current build can produce |
| `aperture_id` | integer | Unitless index into the aperture registry. Extraction always writes `0` (`tether.imaging.extract`) — a **placeholder**, because there is no per-aperture registry yet. It does **not** encode the aperture geometry: `tether extract --window` is settable and still writes `0`; the effective geometry lives in `/settings/extraction`, which the CSV does not carry (see [Intensity units](#intensity-units)) | Never — an integer is always written |
| `alpha` | float | **Dimensionless.** Donor→acceptor leakage α, applied as `I_A,corr = I_A − α·I_D` (`tether.fret.efficiency`) | α is unset. Extraction writes `NaN` until M3 estimates it |
| `gamma` | float | **Dimensionless.** Detection-correction factor γ, the denominator weight in `E = I_A,corr / (I_A,corr + γ·I_D,corr)` | γ is unset (`NaN` at extraction) |
| `delta` | float | **Dimensionless.** Direct-excitation δ. Initialized to `0.0` and inert in 2-colour (no ALEX; ADR-0008), so it exports as `0.0`, not blank | The stored value is non-finite |
| `correction_method` | string | Vocabulary from `tether.project.correct`: `""` (extraction default, nothing applied), `corrected`, `manual`, `apparent-E (corrections unavailable)`, `apparent-E (user toggle)` | Nothing has been applied yet (the `""` default) |
| `correction_confidence` | float | **Unitless provenance flag, not a statistical CI**: `1.0` when a real photophysical correction was applied, `0.0` when the molecule fell back to apparent E (`_CONFIDENCE_CORRECTED` / `_CONFIDENCE_APPARENT` in `tether.project.correct`) | No correction pass has run (`NaN` at extraction) |
| `donor_bleach_frame` | integer | **Frames**, zero-based, absolute. `bleach_frames[0]`. Sentinel `-1` = not detected (`_UNDETECTED_FRAME`, `tether.imaging.extract`); a value equal to `frame_end` means the channel does not bleach within the trace (`tether.project.photobleach`) | Never — an integer is always written |
| `acceptor_bleach_frame` | integer | **Frames**, zero-based, absolute. `bleach_frames[1]`; same `-1` and `== frame_end` conventions | Never |
| `frame_start` | integer | **Frames**, zero-based, **inclusive** — `frame_range[0]`, the start of the molecule's valid native extent inside the store's zero-pad (ADR-0016). Extraction writes `0` | Never |
| `frame_end` | integer | **Frames**, zero-based, **exclusive** (half-open) — `frame_range[1]`. Extraction writes `n_frames` | Never |
| `window_start` | integer | **Frames**, zero-based, **inclusive** — the *resolved* analysis window actually used for `n_finite_frames`, `mean_apparent_e` and `median_apparent_e`: `analysis_window[0]`, falling back to `frame_range[0]` when the window is unset (`hi <= lo`), per `_window` | Never |
| `window_end` | integer | **Frames**, zero-based, **exclusive** — the resolved upper bound, with the same fallback | Never |
| `tags` | string | Free text, **comma-joined** in the store (`tether.analysis.query` splits on `,`). Emitted verbatim, so a multi-tag cell is CSV-quoted. Analysis-only imports carry `round-trip-unavailable` | The molecule has no tags |
| `n_finite_frames` | integer | **Frames** — how many frames inside the resolved window have a finite apparent E. `0` when every frame in the window has `D + A == 0` exactly | Never |
| `mean_apparent_e` | float | **Dimensionless — apparent E** (the proximity ratio `A/(D+A)`), arithmetic mean over the finite frames of the resolved window. **Not** γ-corrected. **Not** clipped to `[0, 1]`: `apparent_fret` deliberately leaves a noisy out-of-range value alone | `n_finite_frames == 0` |
| `median_apparent_e` | float | **Dimensionless — apparent E**, `numpy.median` of the same finite values. Same no-clipping rule | `n_finite_frames == 0` |

> **Three kinds of store write these columns, and only one of them came from a movie.**
> A native extraction and a Deep-LASI reconstruction (`tether.project.reconstruct`, which
> calls the same `tether.imaging.extract.write_extraction`) both link a `/movies` row:
> `movie_id` is that row's id, `molecule_key` is the movie `sha256` + quantized
> `donor_xy`, and `source_filename` is the movie's name. An **analysis-only import**
> (`tether.project.analysis_import.import_analysis_only_project`, the coordinate-less
> SMD / `.txt` branch) writes `movie_id = ""`, `donor_xy`/`acceptor_xy` = `NaN`,
> `source_filename` = the SMD/`.txt` name, `tags = "round-trip-unavailable"`
> (`ANALYSIS_ONLY_TAG`), and a `molecule_key` from `_analysis_only_molecule_key` — the
> SHA-256 of the constant `"tether-analysis-only"`, the source id, the molecule's **row
> index**, and that molecule's raw donor and acceptor **trace bytes** (little-endian
> `float64`). The source calls it "an identity, not fabricated coordinate data". It is
> unique per row and stable across a re-import of the same source, but **nothing in it is
> movie-derived**: it cannot be re-derived from a movie hash or from coordinates, and it
> will not match the key the same molecule would carry in a movie-linked store. A blank
> `movie_id` is the flag that says so; `tether.project.analysis_import.read_analysis_only_marker`
> is the O(1) check on the store itself.

> **`window_start`/`window_end` may equal `frame_start`/`frame_end` by *fallback*, not by
> curation.** `_window` substitutes the frame range whenever `analysis_window` is unset
> (`hi <= lo`), and extraction also *initializes* `analysis_window` to `[0, n_frames]`. So
> "window == frame range" tells you nothing about whether a curator ever looked at the
> molecule. The columns exist so the reported window always matches the range the
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
| Number format | `numpy.savetxt(..., fmt="%.5f")` — fixed-point, exactly 5 decimals (`_TXT_DECIMALS = 5`). A round trip is therefore lossy to that rounding |
| Delimiter | A single space |
| Line ending | OS-translated (`savetxt` opens in text mode; no `newline=` is passed) — CRLF on Windows, LF elsewhere. Unlike the CSV, this is *not* fixed |
| Values | Uncalibrated intensities — never photons, and **not necessarily movie pixel units**: what the number is depends on how the *source store* was built (integrated camera counts for a native extraction; the upstream tool's pre-integrated series, verbatim, for a Deep-LASI reconstruction or an analysis-only import). See [Intensity units](#intensity-units). Signed either way, so background-subtracted values may be negative |

A real first line, from a three-molecule store:

```text
100.00000 53.00000 200.00000 56.00000 300.00000 59.00000
```

**Frame trimming — the one place a row index is not an absolute frame.** The store's
`/traces` arrays are zero-padded to the experiment-max frame count as movies of differing
length are appended (ADR-0016), so `export_deeplasi_txt` trims to the selection's shared
`frame_range`: the written matrix is `donor_all[rows][:, lo:hi]`. Row 0 of the file is
therefore absolute frame **`lo`**, which need not be 0, and `lo` is recorded **only** in
the provenance sidecar (`frame_range`). A selection spanning more than one `frame_range`
has no honest common axis and raises `ValueError` rather than padding or truncating.

Sidecar `parameters` for this export: `intensity_quantity`, `include_rejected`,
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
)
```

A new, self-contained HDF5 project built by `tether.io.schema.create_project` and then
populated with additive data only — it adds no structure to the M0-frozen skeleton
(ADR-0005). It is written **atomically**: staged to a `tempfile.mkstemp` sibling in the
destination directory and moved into place with `os.replace`, and the staged file is
unlinked on any failure. `_preflight_source` validates the source (layers present,
row counts consistent, both patch channels present) **before** anything is written.

**What travels:**

| Group | Content |
|---|---|
| `/molecules` | The selected rows only |
| `/traces` | `donor_corrected`, `acceptor_corrected` always; `donor_raw`, `acceptor_raw`, `donor_background`, `acceptor_background` only when `include_raw=True` |
| `/patches` | Every channel (`donor`, `acceptor`), row-subset, patch window unchanged |
| `/idealization/{model}` | Per-model, filtered on **`molecule_id`** (the unique UUID, not the non-unique `molecule_key`). The row-aligned members `idealized`, `state_path`, `molecule_key`, `molecule_id`, `input_hash` are subset; every other member (`mean`, `var`, `frac`, `tmatrix`, `norm_tmatrix`, `rates`, `pi`, the `priors` group) is a global consensus array and is copied verbatim. The group's `n_molecules` attribute is rewritten to the kept count |
| `/labels` | Rows whose `molecule_key` is in the selection |
| `/conditions`, `/settings`, `/calibration`, `/models` | Copied verbatim |

**What does not travel:**

| Dropped | Why |
|---|---|
| `/movies` **rows** | The subset is definitionally movie-less. The group exists (the skeleton is frozen) but has **zero rows**, so nothing the row itself held comes with it: `uri`, `sha256`, `file_size`, `mtime`, `n_frames` / `height` / `width`, `pixel_dtype`, `frame_time`, `head_tail_hash`, the per-channel crop/rotation/flip, and the row's `calibration_id` cell (`tether.io.schema.MOVIES_DTYPE`). **The calibration itself does travel** — `/calibration` is in `_SUBSET_VERBATIM_GROUPS` and is copied whole, so `/calibration/<calibration_id>` keeps the id (it *is* the group name), the two polynomial transforms and the registration geometry attrs; only the movie row is gone |
| `/features` | Present but empty — per-molecule ML features are outside the subset embed set (`_SUBSET_VERBATIM_GROUPS` deliberately omits it) |
| Raw **and** background traces, when `include_raw=False` | They ride as a set of four. Because `corrected = raw − background` exactly, keeping background alone would make raw reconstructable — omitting raw must omit background too |
| Idealization models whose input layers are absent, or none of whose molecules were selected | Listed in the sidecar's `skipped_idealization_models`. A `raw`-fitted model is skipped when `include_raw=False` |

**Embedded provenance.** Besides the sidecar, the subset stamps four root attributes
(readable with `h5py`):

| Attribute | Value |
|---|---|
| `tether_subset_of` | The **source filename only** (`path.name`), never a full path |
| `tether_subset_created_utc` | Offset-aware ISO-8601 UTC — byte-identical to the sidecar's `created_utc` |
| `tether_subset_include_raw` | `0` or `1` — an integer, not a bool |
| `tether_subset_n_molecules` | The exported count |

The root also carries `create_project`'s own `format="tether-project"`, `schema_version`
and `app_version`; the last is the version of the build that performed the *export*, not
the source's.

**Refusals.** `FileExistsError` when `out_path` exists and `overwrite=False`; `ValueError`
when the output resolves to (or is a hard link of) the source, when the selection is
empty, or when the preflight fails.

Sidecar `parameters` for this export: `include_raw`, `include_rejected`, `molecule_keys`,
`n_molecules`, `trace_layers` and `n_trace_layers`, `idealization_models` and
`n_idealization_models`, `skipped_idealization_models`, `n_label_rows`.

## 4. Plot export (PDF + SVG + PNG)

`tether.analysis.plot_export` splits rendering from writing: each `render_*` helper returns
a bare `matplotlib.figure.Figure` and writes nothing; `export_figure` is the only function
that touches disk. Figures are built with the object-oriented `Figure` API rather than
`pyplot`, so no GUI backend is selected and the base environment stays `matplotlib-base`
(ADR-0044). The module is deliberately **not** re-exported from `tether.analysis`, so
importing the package does not pull in Matplotlib.

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
| Return value | `PlotExportResult(stem, paths, provenance_path, formats)` |

A plot export is stamped **three** ways:

1. **The sidecar** `<stem>.provenance.json`, written by the same
   `write_provenance_sidecar`. `export_figure` merges three keys of its own into your
   `parameters`: `formats` (canonical order), `dpi`, and `outputs` (`{format: basename}` —
   basenames, not paths). The sidecar is written even when `stamp=False`.
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

The eight renderers, and the plot each produces (Appendix-C ids as used in the
[seven-plot parity gallery](../analysis/parity-gallery.md)):

| Renderer | Input dataclass | Plot | x-axis unit |
|---|---|---|---|
| `render_histogram1d` | `Histogram1D` or `HistogramBootstrapCI` (+ optional `ModelGaussianOverlay`) | A1 population histogram | Apparent FRET efficiency *E* (dimensionless) |
| `render_histogram2d` | `Histogram2D` | A2 time-vs-signal heatmap | `Time (s)` when `time_dt != 1.0`, otherwise `Frame` |
| `render_transition_density` | `TransitionDensityPlot` | B1 transition-density plot | Initial *E* (dimensionless) |
| `render_dwell_survival` | `DwellTimeAnalysis` | B2 survival curve (+ residual panel when a fit succeeded) | `Dwell time (s)` when `dt != 1.0`, otherwise `Dwell time (frames)` |
| `render_transition_prob` | `TransitionProbHistogram` | B3 transition-probability histogram | Probability per molecule (dimensionless) |
| `render_state_number` | `StateNumberCounts` | C1 state-number distribution | Number of occupied states (a count) |
| `render_raw_fret_cloud` | `RawFretCloud` | QC raw FRET cloud | `Time (s)` when `time_dt != 1.0`, otherwise `Frame` |
| `render_cross_correlation` | `CrossCorrelation` | Donor–acceptor cross-correlation | `Lag (frames)` — always frames; there is no seconds variant |

> **A time axis says seconds only if you supplied a frame duration.** `dt` / `time_dt`
> default to `1.0`, and at that default the renderer labels the axis in frames. The same
> applies to rates read off a B2 survival fit: `tether.analysis.dwell` reports them in
> `1 / (frames · dt)`, i.e. **per frame** at the default.

`render_raw_fret_cloud` decimates its scatter overlay when the population exceeds
`max_points` (default 20 000) to keep the vector file manageable; the density surface
underneath still covers every point.

## Provenance sidecar

Every one of the four exports writes `<file>.provenance.json` beside its output, via
`tether.project.export.write_provenance_sidecar`. The name is formed by *appending* to the
full filename, so the original extension survives: `molecules.csv.provenance.json`,
`traces.txt.provenance.json`, `subset.tether.provenance.json`. Plot exports pass the
extension-less stem, so theirs is `<stem>.provenance.json`.

The payload is `json.dumps(payload, indent=2, sort_keys=True) + "\n"`, UTF-8 — which is
why the keys read alphabetically:

| Field | Content |
|---|---|
| `app_version` | The git-derived `tether.__version__`, falling back to `"0.0.0+unknown"`. A provenance stamp never raises |
| `created_utc` | Offset-aware ISO-8601 UTC with microseconds (`datetime.now(UTC).isoformat()`), e.g. `2026-07-20T22:14:26.314669+00:00`. A caller may pin it, which is how a subset's in-file attribute and its sidecar agree exactly |
| `parameters` | The export's own parameters — different per export; the per-export lists are given above |
| `source_project` | For the three store exports, the source `.tether` **basename only** — they call `write_provenance_sidecar(source=path.name)`. For `export_figure` it is **caller-supplied and unnormalized**: the `source=` argument is forwarded verbatim, and `write_provenance_sidecar` does no `.name` reduction of its own, so an absolute path you pass is an absolute path in the JSON. Pass a basename yourself |
| `tether_export` | The export-kind discriminator |

`tether_export` takes exactly three fixed values from the store exports —
`"deeplasi-txt"`, `"molecule-table-csv"`, `"subset-tether"`. For plots it is **caller-supplied
and unvalidated**: nothing in `src/` defines or checks a plot kind vocabulary.

A real sidecar, verbatim:

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

The sidecar exists because flat text has no metadata slot and the Deep-LASI `.txt` must
stay header-free — so the stamp travels beside the file rather than inside it (PRD §8
NFR-REPRO; ADR-0001). Keep it with the data file when you move or share the export. For
the CSV and the `.txt` it is the **only** record of which build, which project and which
parameters produced those numbers. A subset `.tether` and a plot export also carry a stamp
*inside* the file — the subset's four `tether_subset_*` root attributes plus
`create_project`'s `app_version`, and a plot's on-figure footer plus its embedded document
metadata (both above) — but only the sidecar carries the full parameter set.

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
`numpy.loadtxt` read it as a plain `T × 2N` numeric matrix. Counting molecules from zero,
molecule *k*'s donor is 0-based column `2k` and its acceptor `2k + 1` — in MATLAB's
1-based indexing, columns `2k + 1` and `2k + 2`.

### Frames to seconds

No *store* export carries the frame duration — only a plot export can, and only as a
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
  movie. Key by `movie_id`, as above. On a two-movie store at `0.1` and `0.25` s/frame,
  frame 8 is `0.8 s` and `2.0 s` respectively; the first-row shortcut reports `0.8 s` for
  both.
- **A stored `0.0` means *unknown*, not "instantaneous".** The GUI treats a non-positive
  `frame_time` as absent and falls back to a frame axis (`tether.gui.shell`), and
  `frames_to_seconds` above refuses rather than handing back a column of zeros labelled
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

### Why a subset `.tether` is readable at all

The `.tether` store is a **superset of the tMAVEN SMD layout**, not a private format
(ADR-0002, [The `.tether` store is an SMD superset](../adr/0002-smd-superset-round-trip.md)).
It is HDF5: `h5py`, MATLAB `h5read` and `rhdf5` all open it without Tether installed. The
same property is what makes the tMAVEN round trip a data-model consequence rather than a
conversion step.

## References

- **PRD §7.9 FR-EXPORT** — the export surface; **§8 NFR-REPRO** — "all exports are stamped
  with provenance and parameters"; **§5.4** — the subset invariant; **§7.5** — the curation
  filter; **§7.10** — `molecule_key` non-uniqueness.
- **ADR-0001** — provenance-first project store.
- **ADR-0002** — the `.tether` store is an SMD superset; round-trip is a data-model property.
- **ADR-0005** — the M0 schema freeze (additive-only), which the subset export honours.
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

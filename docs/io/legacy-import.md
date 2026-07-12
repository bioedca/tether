# Legacy Deep-LASI import

Tether re-analyses an existing **Deep-LASI** acquisition *without re-extracting the movie*
(PRD §7.8). The import path reads the legacy file set — the raw movie, the `TIRFdata`
`.tdat`, the `DeepLASI_MAT_export_*.mat`, the `…-donc-accc-w.txt`, and the tMAVEN SMD —
recovers the per-molecule coordinates and pre-integrated traces, applies the Deep-LASI →
Tether correction-factor remap, and writes a `.tether` project store.

Whether that project is **round-trip-ready** (linked to a movie, browsable/curatable with
spot & overlap views) or **analysis-only** (idealization, histograms, TDP, kinetics — but
no movie round-trip) is decided by *which files are present*, and above all by whether any
of them carries pixel coordinates. This page is the single reference that ties that decision
to the on-disk formats (the **coordinate-availability matrix**, PRD Appendix A), the
**correction-factor remap** (PRD Appendix B.1), and the shipped module behind each stage.

> **Where the behavior actually lives.** Every claim below is asserted by a committed test
> (the "Evidence" column), which the 3-OS CI `test` matrix runs headlessly. This page
> **consolidates** those behaviors into one reference; it does not replace the tests. Import
> is deliberately layered: `tether.io.*` readers never write a store, `tether.project.*`
> writers never touch HDF5 directly (they drive the M0-frozen schema writers), and the
> `tether.gui.*` wizard only orchestrates — so `schema-guard` stays green across the whole
> path.

## Coordinate-availability matrix

Of the five legacy artifacts, **only the `.tdat` (`ParticlesColocalized`) and the `.mat`
(`fret_pairs`) carry pixel coordinates**; the `.txt` and the tMAVEN SMD carry intensities
only (PRD Appendix A). Full round-trip re-analysis therefore requires the `.tdat` *or* the
`.mat` for coordinates — plus, in the shipped pipeline, the `.mat` as the trace source and
the movie to link molecules to pixels.

| Legacy file | Format | Pixel coordinates | Intensity traces | Role in reconstruction |
|---|---|---|---|---|
| Movie `*.tif` | big-endian multi-page TIFF | — (pixels only) | — (raw movie) | Links molecules to pixels; SHA-256 seeds every `molecule_key`; makes crops re-cacheable |
| `*.tdat` | MATLAB v7.3 (HDF5, MCOS) | **✓** `ParticlesColocalized` (donor = reference channel, acceptor = the one other colocalized channel) | not read by the shipped decode | Preferred *native* coordinates + remapped α/γ + detection mode + embedded movie reference |
| `DeepLASI_MAT_export_*.mat` | MATLAB v5 | **✓** `fret_pairs` → `donor_xy` / `acceptor_xy` | **✓** raw / corrected / background, donor + acceptor | **Mandatory trace source**; baseline coordinates when no `.tdat` |
| `…-donc-accc-w.txt` | whitespace text | — | **✓** corrected only | Analysis-only trace source |
| tMAVEN SMD `*.hdf5` | SMD-HDF5 (Appendix D) | — (Tether's coordinate superset is dropped on a standalone-tMAVEN round-trip) | **✓** corrected + curated subset | Analysis-only source; intensity-matched back to coordinates when a coordinate source is present |

Coordinates are always **recovered, never re-detected** — but the two sources store them
differently. The `.mat` `fret_pairs` is MATLAB 1-based `[x = col, y = row]`, so
`read_deeplasi_mat` simply subtracts 1 to reach Tether's 0-based `[x, y]`. The `.tdat`
`ParticlesColocalized` (findColoc) is MATLAB 1-based `[row, col] = [y, x]` (inherited from
`findPart.m`, `XY = [ty, tx]`), so `read_tdat` *flips* the axes to `[x = col, y = row]` and
*then* subtracts 1 — that axis flip was a real correctness fix caught by the M0.5
registration validation (the earlier no-flip assumption put row into x). Forgetting the `-1`,
or the flip, misplaces every molecule.

## Correction-factor remap

Deep-LASI stores the leakage and direct-excitation factors under the *opposite* Greek
letters from Tether's field-standard convention. The importer remaps them on the way in
(`tether.io.tdat.remap_correction_factors`), applying the accurate-FRET correction order
**background → α → δ (= 0) → γ** (PRD Appendix B).

| Physical factor | Tether | Deep-LASI field | On import |
|---|---|---|---|
| Donor→acceptor **leakage** (additive) | **α** | `Beta` | applied — `I_A,corr = I_A − α·I_D` |
| **Direct excitation** of acceptor (additive) | **δ** | `Alpha` | forced inert **0** (its estimator needs the ALEX acceptor-under-acceptor channel) |
| **Detection / QY ratio** (multiplicative) | **γ** | `Gamma` | applied |

> **The naming is inverted — this is the load-bearing trap.** Deep-LASI's stored `Beta` is
> the leakage that Tether calls **α**, and Deep-LASI's stored `Alpha` is direct excitation,
> which Tether calls **δ** and forces to 0 in the single-laser 2-color scheme. The remap must
> never pass Deep-LASI `Alpha` through as Tether α, and never fold `Beta` into γ — either
> mistake silently drops a real leakage correction and shifts every imported FRET efficiency
> (PRD Appendix B.1; ADR-0008). The raw Deep-LASI values survive on
> `TdatCorrections.deeplasi_alpha` / `deeplasi_beta` / `deeplasi_gamma` so the remap stays
> auditable. When γ is unavailable or ≤ 0 (e.g. the Cy3-only calibration sample, whose
> `DefaultGamma = 0`), the project falls back to the **apparent-E substrate** rather than a
> fabricated factor or a NaN efficiency (ADR-0003).

## The import pipeline at a glance

| Stage | Tether module / entry point | Produces | Evidence |
|---|---|---|---|
| **Intake + pairing** | `tether.io.intake.discover_acquisitions` | `DiscoveryResult` of grouped `AcquisitionFileSet`s | `tests/test_intake.py`, `tests/test_filename.py` |
| **TIRFdata decode** | `tether.io.tdat.read_tdat` (+ `tether.io.mcos`) | `Tdat`: coordinates + remapped α/γ + detection mode + movie reference | `tests/test_tdat.py`, `tests/test_mcos.py` |
| **Coordinate recovery + SMD cross-check** | `tether.io.recover.recover_coordinates` / `match_smd_to_coordinates` (+ `tether.io.deeplasi`) | `RecoveredCoordinates`, `SmdCoordinateMatch` | `tests/test_recover.py`, `tests/test_deeplasi.py` |
| **Round-trip reconstruction** | `tether.project.reconstruct.reconstruct_project` | round-trip-ready `.tether` | `tests/test_reconstruct.py` |
| **Analysis-only import** | `tether.project.analysis_import.import_analysis_only_project` | movie-less analysis-only `.tether` | `tests/test_analysis_import.py` |
| **Wizard (controller / executor / UI)** | `tether.gui.deeplasi_wizard`, `.deeplasi_executor`, `.deeplasi_wizard_ui` | `WizardPlan` → `ExecutionReport` | `tests/test_deeplasi_wizard.py`, `tests/test_deeplasi_executor.py`, `tests/test_deeplasi_wizard_ui.py` |
| **Open live in the shell** | `tether.gui.shell.TetherShell.import_deeplasi_bundle` / `load_project` | live round-trip or analysis-only project | `tests/test_shell_load_project.py` |

### Intake + pairing

`discover_acquisitions(directory)` scans a folder **by filename only** (no contents are
read), classifies each file by its *final* suffix, and groups the `.tif` / `.tdat` / `.mat`
/ `.txt` of one acquisition under their shared `parse_filename(...).stem` — the parser
strips the Deep-LASI prefixes, the glued mid-name source `.tif<timestamp>`, and the
`-donc-accc-w` suffix so all four canonicalize to the same key. `.tmap` maps attach as
`shared_maps` to every acquisition of the matching condition; an SMD `.hdf5` attaches by
exact stem, else by an *unambiguous* video index, otherwise it lands in
`DiscoveryResult.unpaired`. Each `AcquisitionFileSet` exposes `has_coordinate_source`
(`.tdat` or `.mat`), `round_trip_available` (movie **and** a coordinate source), and
`analysis_only`.

The `.mat` `movie_name` and the `.tdat` `Channel.FilePath` supply an *embedded* movie
reference; `verify_movie_reference` cross-checks it against the grouped movie **by basename,
case-insensitively** (a Windows path recorded in a `.mat` still matches on a POSIX CI
runner). The reference readers are best-effort — a missing or unreadable export degrades to
"no reference" and pairing rests on the stem alone, so intake never fails the whole scan on
one bad file.

### TIRFdata decode

A `.tdat` is a MATLAB v7.3 (HDF5) file whose `TIRFdata` class instances live in the opaque
`#subsystem#/MCOS` blob. `read_tdat` recovers exactly what import needs: the
`ParticlesColocalized` coordinates (rows kept only where a molecule is colocalized in every
participating channel, so donor and acceptor rows stay index-aligned), the
`DefaultAlpha/Beta/Gamma` scalars **remapped** to Tether naming, the particle-detection mode
(`wavelet` / `intensity` / `bandpass`; local-variance and ZMW modes are *refused* with a
`ValueError` rather than silently mis-detected), and the embedded movie reference. Coordinate
and correction decoding are MCOS-independent; only the detection threshold and movie
reference need the MCOS graph and are decoded best-effort (a `None` there is normal, not an
error). The shipped decode deliberately does **not** read the `.tdat`'s per-molecule series,
image patches, masks, `FrameTime`, HMM states, or categories.

### Coordinate recovery + SMD cross-check

`recover_coordinates(tdat=…, mat=…, prefer=…)` unifies the two coordinate sources into one
`RecoveredCoordinates` model (donor/acceptor `(N, 2)` in acquisition-molecule order), tagged
with the source actually used. From a `.tdat` it takes donor = the reference channel and
acceptor = the single other colocalized channel (two-colour only — one or three-plus
channels raise). From a `.mat` it uses `donor_xy` / `acceptor_xy` directly. The two sources
are **not** cross-validated (the `.mat` is often a curated slice with a different molecule
count).

Because tMAVEN may subset or reorder molecules by its selection mask, an SMD's row order is
not trusted: `match_smd_to_coordinates` re-resolves each SMD trace to its acquisition
molecule by **exact intensity matching** against index-aligned reference traces, then attaches
the recovered coordinates; unmatched rows are reported (`mapping = -1`, `[nan, nan]`), never
guessed. The reference traces and the SMD must be the *same kind* of series — a Deep-LASI SMD
stores the **corrected** `-donc-accc-w` intensities, so the cross-check uses the corrected
`.mat` `donc`/`accc` (or the `.txt`), never the raw `don`/`acc`.

### Round-trip reconstruction

`reconstruct_project(output_path, *, export, movie, coordinates=…, corrections=…, …)` builds
a round-trip-ready store by **reusing the existing extraction writers** — it never writes
HDF5 directly, so the M0 schema freeze holds (ADR-0045). It maps the legacy pre-integrated
series into layers without re-integrating (corrected → `intensity`, raw → `total`,
background → `background`), writes the linked `/movies` row, the `/molecules` rows keyed by
the stable `molecule_key = SHA-256(movie.sha256 + quantized donor_xy)` (§7.10), the six
`/traces` arrays, `/patches`, the remapped α/γ and corrected FRET (`METHOD_MANUAL` when
γ > 0, else the apparent-E substrate), per-channel photobleach frames and the auto analysis
window, and the seeded condition category vocabulary. The whole build is atomic (written at
a temp sibling, `os.replace` only on success); when `overwrite` replaces an existing project
it re-asserts the single-writer lock (a fresh output writes to a temp sibling and needs no
lock re-assertion).

The Deep-LASI accept/reject selection is written as a **provisional curation prior**:
`/labels` events with `source = "deeplasi-provisional"` at a decaying weight `w₀/(1 + n_human)`
(§7.5) — it lives only in `/labels` and never sets `/molecules.curation_label`, so a human's
own curation always supersedes it. Two things are honest **documented data gaps** (deferred,
never fabricated, ADR-0045): the Deep-LASI per-molecule NN/HMM category *assignments* (only
the category vocabulary is seeded) and the real 21×21 image patches (patch arrays are
zero-filled unless supplied; because the movie is linked, spot/overlap crops are re-cached
from it instead).

### Analysis-only import

`import_analysis_only_project(output_path, *, source, …)` handles the degraded branch — a
bare `.txt` or a coordinate-less SMD with no `.tdat`, `.mat`, or movie (ADR-0046). It writes
a **movie-less** store: only the corrected trace layer, `donor_xy` / `acceptor_xy` as NaN
sentinels (genuinely absent, never a fake `[0, 0]`), a synthesized per-molecule
`molecule_key` (a content hash over source id + row index + trace bytes, so keys stay unique
and stable without coordinates), and every molecule tagged
`tags = "round-trip-unavailable"`. An additive `/settings/analysis_only` group records a
one-time banner. Idealization, FRET histograms, TDP, and kinetics are fully usable; the
movie round-trip browser and patch/overlap views are structurally impossible and disabled.

`read_analysis_only_marker(path)` is the O(1) gate the GUI reads. **Mind the polarity:** it
returns the marker for an analysis-only project and **`None` for a normal, round-trip-capable
one**; a missing/partial marker defaults `round_trip_available` to `False`, so a truncated
store never re-enables round-trip over coordinate-less data.

### The wizard, and opening a project live

The Deep-LASI re-analysis wizard is a Qt-free controller (`DeepLasiWizard`) + executor
(`execute_plan`) with a thin `QDialog` renderer. `plan_discovery` proposes a default per
acquisition — **reconstruct** (needs a movie **and** a `.mat` trace source), else
**analysis-only** (needs an SMD or `.txt`), else **skip** — and every edit routes through
validated mutators that raise `WizardError` on an unsupported change. `finalize()` freezes a
`WizardPlan` once at least one acquisition is runnable with no output-name collision, and
`execute_plan` writes one `.tether` per acquisition (fail-soft by default, continuing past a
bad file). The `&Legacy → Import Deep-LASI bundle…` shell action runs the dialog, reports the
written paths, and — when exactly one project was produced — opens it live via
`TetherShell.load_project` for browse / curate / idealize; `load_project` does every fallible
read before mutating shell state, so a bad open leaves the prior project in place.

> **Reconstruct capability is stricter than coordinate availability.** A movie plus a
> `.tdat`-only set *has* coordinates but no trace source, so it cannot reconstruct — the
> `.tdat` only upgrades the coordinate source (to native `ParticlesColocalized`) and supplies
> correction factors. When a requested `.tdat` coordinate set does not align 1-to-1 with the
> `.mat` export's molecules, the executor falls back to the export-aligned `.mat` coordinates
> and surfaces a warning rather than fabricating a join.

## Round-trip vs analysis-only

| | Round-trip (`reconstruct_project`) | Analysis-only (`import_analysis_only_project`) |
|---|---|---|
| Requires | movie + `.mat` (+ optional `.tdat`) | `.txt` or coordinate-less SMD |
| Coordinates | recovered from `.tdat`/`.mat` | NaN (genuinely absent) |
| Movie link | yes (`/movies` row, `molecule_key` from movie hash) | none (`movie_id = ""`) |
| Trace layers | corrected + raw + background | corrected only |
| Patches | written (real if supplied, else zero-filled; crops re-cache from movie) | none |
| Curation prior | Deep-LASI mask → `deeplasi-provisional` `/labels` | none (SMD *is* the curated subset; all import uncurated) |
| Per-molecule marker | — | `tags = "round-trip-unavailable"` |
| Project marker | — | `/settings/analysis_only` + one-time banner |
| Enabled | idealization, histograms, TDP, kinetics, **movie round-trip, spot/overlap curation** | idealization, histograms, TDP, kinetics |
| Disabled | — | movie round-trip, patch/overlap views |

## References

- **PRD Appendix A** — input formats + the coordinate-availability matrix.
- **PRD Appendix B (B.1)** — the correction-factor scheme and the Deep-LASI naming map.
- **PRD §7.8** — the polished Deep-LASI re-analysis workflow; **§5.3** — analysis-only projects.
- **ADR-0008** — Deep-LASI → Tether correction-factor naming remap (β→α, α→δ, γ→γ).
- **ADR-0045** — reconstruct a round-trip-ready `.tether` from Deep-LASI legacy data.
- **ADR-0046** — analysis-only import of a coordinate-less SMD / `.txt` source.
- **ADR-0002** — the `.tether` store is an SMD superset; round-trip is a data-model property.
- **ADR-0017** — the minimal Deep-LASI `.mat` / `.txt` validation reader and coordinate convention.

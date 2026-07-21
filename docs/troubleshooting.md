# Troubleshooting

**Who this page is for.** Anyone whose Tether run did something other than what they
expected — including the runs that *looked* fine. Entries are keyed by **what you saw**, not
by which module owns the problem, so scan the headings or paste your error text into your
browser's find bar: every message quoted here is copied verbatim from `src/` and each entry
names the module that raises it. Any term that reads as jargon is defined in the
[Glossary](glossary.md).

The **silent** failures come first, because nothing will tell you about them. They are marked
with a *Signature* block: something you can check on your own store to confirm or rule the
diagnosis out.

---

## Data and analysis failures

### Every efficiency is mirrored — `--donor-side` was backwards

**Symptom.** The run succeeded. Molecule counts look right. But the population histogram sits
where you expected its mirror image — a low-FRET construct reads high, a high-FRET construct
reads low — and every trace's donor and acceptor look swapped.

**Cause.** `--donor-side` says which horizontal half of the dual-view frame is the donor
channel. It defaults to `left`, and **nothing in Tether detects the true orientation.** The
CLI takes it as free text (`tether.cli`, `metavar="{left,right}"` rather than `choices=`), so
a *typo* is rejected downstream by `ExtractOptions.__post_init__`:

```text
tether extract: donor_side must be 'left' or 'right', got 'middle'
```

…but a *valid-but-wrong* value is accepted in silence. All `donor_side` does is choose which
crop tuple is returned by `_half_split_geometry` in `tether.project.extract`, and detection
is [donor-anchored](glossary.md#donor-anchored-colocalization) — so the swapped run still
colocalizes and still registers. Since apparent E is `A / (D + A)`, swapping the two channels
gives exactly `1 − E`.

The molecule count is *not* a control here. Donor-anchoring makes the molecule set exactly
the detections in the half you **labelled** donor, so swapping the label swaps which half is
counted. The two counts agree only when both halves detect the same number of spots — true of
a symmetric synthetic movie, and not something to rely on with real data, where the
acceptor-dark and low-FRET population ADR-0015 exists to preserve is precisely the set present
in one half and not the other.

Two related traps:

- With `--tmap`, `--donor-side` is **ignored**: the split comes from the `.tmap`'s own crop
  geometry. No warning is emitted when both flags are passed.
- The `tether batch` **CLI cannot set it at all.** `run_batch()` *does* take an
  `extract_options=` argument and forwards it, but `tether.cli._run_batch` never builds one —
  so every batch run takes the `ExtractOptions` defaults. Dumped from a real batch run's
  `/settings/extraction.attrs["profile_json"]`: `"donor_side": "left"`,
  `"detection_mode": "wavelet"`, `"pair_tol": 2.0`, `"rms_gate": 0.5`.

> **Signature.** Run the two extractions side by side on one movie and compare. The relevant
> store fields, dumped from a real pair of runs on the same input — a **symmetric** 3-spot
> synthetic movie, which is why the two counts match:
>
> | Field | `--donor-side left` | `--donor-side right` |
> |---|---|---|
> | `/settings/extraction.attrs["profile_json"]` → `donor_side` | `"left"` | `"right"` |
> | `/movies/table` → `donor_crop` (1-based `[y1, x1, y2, x2]`) | `[1, 1, 64, 48]` | `[1, 49, 64, 96]` |
> | `/movies/table` → `acceptor_crop` | `[1, 49, 64, 96]` | `[1, 1, 64, 48]` |
> | mean apparent E | `0.1986` | `0.8014` |
>
> Both runs printed the same `Extracted 3 molecule(s) -> …` line and both exited `0`.

**Remedy.** Confirm the orientation *before* you trust a dataset, two ways:

- **Against the instrument.** `donor_crop` in `/movies/table` is a 1-based
  `[y1, x1, y2, x2]` box; check that its half of the frame is the one the splitter sends
  through the donor emission filter.
- **Against a trace.** Open a molecule that photobleaches. At the acceptor-bleach step the
  acceptor channel drops to background and the donor channel *rises* — the dequenching step
  Tether's own γ estimator measures as `ΔI_A / ΔI_D` (`tether.fret.gamma`). If in your traces
  the channel labelled *acceptor* is the one that rises, the sides are swapped.

If it was wrong, **re-extract**. There is no in-place fix and nothing to flip after the fact:
the crops, the registration map, the molecule keys and the tags are all written from the
chosen split.

### The batch finished "ok" but nothing was idealized

**Symptom.**

```text
Batch run: 1 movie(s), 1 ok, 0 failed (policy=warn, idealize=on)
  ok    warp_010  extract=done correct=done idealize=deferred
```

Exit code `0`. `/idealization` is empty.

**Cause.** `tether batch` probes the sidecar once at startup. When no interpreter resolves,
and `defer_if_unavailable` is on (the default), the idealize stage is recorded as
`deferred` rather than `failed` — and `deferred` is not a failure, so `n_failed` stays `0`
and the CLI returns `0` (`tether.project.batch`). The *reason* is logged at `INFO`, below
Python's last-resort handler threshold, so it never reaches your console; the word
`deferred` in the stage list is the only tell.

> **Signature.** `/settings/batch.attrs["idealize_status"] == "deferred"`, `/idealization`
> empty, and the JSONL log carries the explanation:
>
> ```text
> {"movie": "(batch)", "stage": "idealize", "status": "deferred", "detail": "sidecar unavailable \u2014 idealization deferred: no sidecar interpreter: pass sidecar_python= or set TETHER_SIDECAR_PYTHON to a Python in an env built from sidecar/conda-lock.yml with tMAVEN installed"}
> ```
>
> That is the byte-for-byte line as it lands on disk. `BatchLog.event` writes
> `json.dumps(record)` at the default `ensure_ascii=True`, so every non-ASCII character in a
> detail string is **escaped**: the em dash the code emits is stored as `\u2014`. Grep for an
> ASCII-only substring — `grep '"status": "deferred"' <out-dir>/batch-log.jsonl`, or
> `grep 'idealization deferred' …` — never for the rendered `\u2014`.

**Remedy.** Fix the sidecar (see
[Idealization cannot find the sidecar interpreter](#idealization-cannot-find-the-sidecar-interpreter))
and re-run the same batch — extract and correct report `skipped` from their checkpoints and
only idealization runs. To make the condition loud in the first place, pass `--no-defer`,
which fails each movie's idealize stage in isolation and exits `1`:

```text
warp_010 · idealize: failed — no sidecar interpreter: pass sidecar_python= or set TETHER_SIDECAR_PYTHON to a Python in an env built from sidecar/conda-lock.yml with tMAVEN installed
Batch run: 1 movie(s), 0 ok, 1 failed (policy=warn, idealize=on)
  FAIL  warp_010  extract=done correct=done idealize=failed(no sidecar interpreter: pass sidecar_python= or set TETHER_SIDECAR_PYTHON to a Python in an env built from sidecar/conda-lock.yml with tMAVEN installed)
```

The first line is `BatchLog.event`'s console record — it joins movie and stage with a middle
dot (` · `) and the error with an **em dash** (` — `). The last two lines are
`BatchSummary.format_report`, which appends the error text only because the stage is `failed`.

### The batch says `correct=done` but no corrections were applied

**Symptom.** The run reports `correct=done` and exit `0`, but α and γ are NaN on every
molecule and your efficiencies are uncorrected.

**Cause.** This is the **documented, expected** outcome for an acquisition with no clean
acceptor-bleach step. `compute_leakage_alpha` withholds α below
`DEFAULT_MIN_QUALIFYING_TRACES = 10` qualifying donor-only tails rather than fabricating one;
`run_correct_stage` then skips γ entirely (it cannot run without α) and stamps every molecule
`apparent-E (corrections unavailable)`. Nothing is wrong with the code — but the batch
console never says so, because the explanation lives in the stage *detail* string, which the
end-of-run report does not print.

> **Signature.** Three store reads, dumped from a real run:
>
> ```text
> /settings/correction : {'apparent_e_only': False, 'n_molecules': 7, 'n_corrected': 0, 'n_manual': 0, 'n_apparent': 7, 'total_failure': True, ...}
> /settings/leakage    : {'alpha': nan, 'withheld': True, 'n_qualifying': 0, 'min_qualifying_traces': 10, ...}
> /settings/gamma      : ABSENT
> ```
>
> `total_failure=True` **with** `apparent_e_only=False` is the unambiguous "correction
> failed" signature; `total_failure=True` with `apparent_e_only=True` is the deliberate
> user toggle. The absence of `/settings/gamma` is itself diagnostic — γ never ran.
> `/molecules.correction_method` reads `apparent-E (corrections unavailable)` with
> `correction_confidence = 0.0`. The stage detail string spells it out. The line below is
> copied byte-for-byte from a real `batch-log.jsonl`, so the `α` that `run_correct_stage`
> builds appears in its escaped on-disk form (`ensure_ascii` again — see the deferral
> entry above):
>
> ```text
> {"movie": "warp16b", "stage": "correct", "status": "done", "detail": "pb 0D/0A; \u03b1 withheld; apparent-E fallback (7 mol)"}
> ```

**Remedy.** There is no flag that makes the estimators succeed on data that lacks the step
they measure. Realistically there are two options: accept apparent E (which is what every
population plot shows anyway — see [apparent E](glossary.md#apparent-e)), or acquire traces
long enough to capture acceptor photobleaching in ≥ 10 molecules.

The correction overrides are a **provenance** tool, not a third option. `compute_corrected_fret`
accepts `alpha_override` / `gamma_override` (`tether.project.correct`), and they must be
supplied **together**: the gate is
`np.isfinite(eff_alpha) and np.isfinite(eff_gamma) and eff_gamma > 0.0`, and the *other*
factor is still the stored NaN, so passing only one leaves every molecule stamped
`apparent-E (corrections unavailable)` exactly as before. Even when both are supplied, the
visible effect is provenance only: the applied factors are persisted to `/molecules.alpha` /
`.gamma`, `/settings/correction` records the overrides, and `correction_method` becomes
`manual` — but plots and CSV exports still show apparent E, because
`tether.fret.efficiency.corrected_fret` has **no call sites** outside the `tether.fret`
re-export (`tether.analysis.histogram`, `.cloud`, `tether.gui.trace_dock`,
`tether.project.export` and `tether.ml.features` all call `apparent_fret`).

> Note the related non-silence: a molecule that fails γ's own gates silently receives the
> **dataset median** γ and is still stamped `corrected` with confidence `1.0`. Only the
> population count `/settings/gamma.attrs["n_fallback"]` reveals how many; there is no
> per-row marker.

### `tether batch --overwrite` changed nothing

**Symptom.** You fixed a parameter, re-ran the batch with `--overwrite`, and got:

```text
  ok    warp_010  extract=skipped correct=skipped idealize=deferred
```

The results are the old ones, reported as `ok`, exit `0`.

**Cause.** `--overwrite` means "re-extract a movie whose output exists but is **not** a
completed extraction" — exactly as its own `--help` text says. `_do_extract` in
`tether.project.batch` checks the checkpoint (`/settings/extraction` present) and returns
`skipped` *before* `overwrite` is ever forwarded to `extract_movie`; `_do_correct` does the
same on `/settings/correction`. A completed stage is always skipped.

This also defeats `--policy fail` on a second run: the failed movie's `.tether` was already
written on the first pass, so the re-run skips extraction and never re-evaluates the gate.

> **Signature.** `extract=skipped` / `correct=skipped` in the report, and
> `/settings/extraction.attrs["profile_json"]` still holding the old parameters.

**Remedy.** Delete the affected `.tether` files (or the whole out-dir) and re-run. For a
single movie, `tether extract --overwrite` *does* re-extract — the checkpoint is a batch-runner
concept only.

Deleting is not enough if the parameter you fixed is an **extract** tunable
(`--donor-side`, `--detection-mode`, `--detection-threshold`, `--min-separation`, `--window`,
`--pair-tol`, `--rms-gate`). `tether batch` exposes none of them and never builds an
`ExtractOptions`, so a clean re-run silently re-applies the same defaults
(see [every efficiency is mirrored](#every-efficiency-is-mirrored-donor-side-was-backwards)).
Re-run those movies through `tether extract --overwrite` one at a time, or drive
`run_batch(..., extract_options=ExtractOptions(...))` from the library, which *does* forward
them.

### Registration passed the gate with a suspiciously perfect residual

**Symptom.** `/calibration` reports an RMS residual at machine epsilon (`1e-14` or smaller)
and `low_confidence: False`, on a movie you know is not perfectly registered.

**Cause.** The gate is **vacuous whenever the control points exactly determine the fit** — the
residual of an exactly-determined least-squares fit is zero by construction, so it measures
nothing about the mapping. `_fit_both` in `tether.imaging.calibrate` uses a degree-2
polynomial at `_MIN_POLY_POINTS = 6` pairs or more and a 4-DOF similarity below that, which
gives the condition two arms:

- **2 pairs, `degree: 1`** — 4 unknowns, 4 equations.
- **6 pairs, `degree: 2`** — the polynomial basis `[1, x, y, xy, x², y²]` has 6 coefficients
  per axis (`poly_basis_deg2` in `tether.imaging.register`), so a 6×6 system.

At **3–5 pairs the similarity is over-determined and the residual is real** — the gate works
normally there, and a `low_confidence: True` on a 4-point fit is a genuine warning, not an
artefact. (What it cannot tell you is whether the map needs degree 2: a 3–5-point residual
only measures how well a *similarity* fits.) Separately, `pair_control_points` silently
**drops** outlier pairs beyond `--pair-tol` (default 2 px), which is how a movie with plenty
of spots lands on 6 surviving pairs; only fewer than 2 is a hard error.

> **Signature.** In `/calibration/<cal-id>.attrs`: an `rms_residual` at machine epsilon
> (`~1e-14`) with `n_control_points` **equal to the fit's degrees of freedom** — 2 at
> `degree: 1`, or 6 at `degree: 2`. Both are verified below on synthetic movies whose true
> donor→acceptor mapping is a **cubic** warp, i.e. deliberately not representable by either
> fit, at the default `--rms-gate`:
>
> ```text
> vacuous : {'rms_residual': 4.24e-14, 'n_control_points': 6, 'gate_px': 0.5, 'degree': 2, 'low_confidence': False, 'source': 'native'}
> genuine : {'rms_residual': 0.502,    'n_control_points': 15, 'gate_px': 0.5, 'degree': 2, 'low_confidence': True,  'source': 'native'}
> ```
>
> The first row is the trap: a badly mis-mapped movie reporting a *perfect* fit, and
> `degree: 2` alone does **not** clear it.

**Remedy.** Treat the RMS number as meaningful only when `n_control_points` is comfortably
above the fit's DOF — in practice 7 or more, since that is where the degree-2 fit starts being
over-determined. Get more pairs: raise `--pair-tol` so genuine pairs are not discarded, lower
`--min-separation`, or try `--detection-mode intensity` with a lower `--detection-threshold`.

Importing a Deep-LASI map with `--tmap` does **not** help here: the imported path skips pairing
entirely, so the residual is left NaN and the gate is not merely vacuous but absent (see
[registration was flagged low-confidence](#registration-was-flagged-low-confidence)). Tether
also has no `.tmap` **writer** — `tether.imaging.register.read_tmap` is the only entry point —
so there is no way to extract a bead movie in Tether and export a calibration map from it.

### Extraction found no molecules

**Symptom.**

```text
tether extract: registration failed: only 0 control-point pair(s) matched (need >= 2). Detected 0 donor / 0 acceptor spots; check the channel split, detection sensitivity (--min-separation) or pairing tolerance (--pair-tol).
```

Exit code `1`. Raised by `tether.project.extract.extract_movie` when fewer than 2 control
points survive pairing (the native path only — an imported `--tmap` skips pairing entirely).

**Cause and remedy, in the order worth trying.** Read the two counts in the message first:
they tell you which half of the problem you have.

1. **`Detected 0 donor / 0 acceptor`** — the detector found nothing. Check that the movie
   really is a side-by-side dual-channel frame and that `--donor-side` matches, then loosen
   detection: `--detection-mode intensity` with a lower `--detection-threshold` (e.g. `0.2`;
   it is a **fraction of the detection-image maximum**, not an absolute count), or a smaller
   `--min-separation`.
2. **Spots were detected but pairs did not match** — the two halves are misaligned by more
   than `--pair-tol` (default 2 px). Raise `--pair-tol`, switch to `--prealign similarity`,
   or import the real calibration with `--tmap`.

Defaults worth knowing: `--detection-threshold` is ignored by the `wavelet` mode, and
defaults to `0.5` for `intensity` and `0.98` for `bandpass`. Omitting `--min-separation`
gives each mode its own faithful default (wavelet 8 px, intensity/bandpass 3 px;
[ADR-0022](adr/0022-m1-acceptance-reframe-and-close.md)).

### Extraction found far fewer, or far more, molecules than expected

**Symptom.** `Extracted N molecule(s)` where *N* is wildly off — a handful on a crowded
field, or thousands on a sparse one. **No error is raised**; molecule count is not gated.

**Cause.** Detection sensitivity. There is no absolute-count check anywhere in
`tether.project.extract`, and remember that colocalization is
[donor-anchored](glossary.md#donor-anchored-colocalization) — *every* in-frame donor spot
becomes a molecule, whether or not an acceptor was independently detected, so the count
tracks donor detections alone. Molecules whose full `21 × 21` aperture would fall outside
the frame in either channel are dropped rather than zero-filled, which also trims edge spots.

> **Signature.** `/settings/extraction.attrs["profile_json"]` records every tunable verbatim
> (`detection_mode`, `detection_threshold`, `min_separation`, `window`, `pair_tol`,
> `coloc_distance`, `rms_gate`, `donor_side`). Compare it against a run you trust; the
> parameters, not the code, will differ.

**Remedy.** Adjust `--detection-mode` / `--detection-threshold` / `--min-separation` and
re-extract. There is no post-hoc re-detection: detection happens once, at extraction.

### The movie will not load

All three of these arrive wrapped by `extract_movie` as
`could not extract from <name>: …`, exit `1`. The inner message comes from
`tether.io.movie`.

| Message | What it means | Remedy |
|---|---|---|
| `… is not memory-mappable; the lazy reader needs an uncompressed, contiguous TIFF (the reference movie format, PRD Appendix A).` | A compressed or tiled TIFF. This is the most common real-world case. | Re-save uncompressed — in ImageJ, *Save As → Tiff* with no compression. |
| `…: expected a 3-D movie (frames, height, width), got shape (64, 64) (axes 'YX').` | A single-page TIFF — often a max-projection saved instead of the stack. | Point Tether at the stack. |
| `not a TIFF file: header=b'this'` | The file is not a TIFF at all: renamed, corrupt, or a copy that never finished. | Re-copy from the acquisition machine. |

Two more from the same family, raised by `tether.project.extract`:

- `movie changed during extraction: <path>` — the file's size or mtime moved mid-run
  (cloud sync, or the acquisition is still writing). Copy the movie locally, or wait for the
  acquisition to finish.
- `movie too narrow (1 px) to split into two channels` — the frame cannot be halved.
  Note that a movie of odd width silently drops its trailing column so both halves match.

### `tether extract` rejects an option value

Every operator-actionable tunable is validated in `ExtractOptions.__post_init__`
(`tether.project.extract`) so bad input fails with a clean one-line message on stderr and
exit `1` — never a traceback. The ones you are most likely to meet:

| Message | Fix |
|---|---|
| `donor_side must be 'left' or 'right', got 'middle'` | use `left` or `right` |
| `detection_mode must be one of ('wavelet', 'intensity', 'bandpass'), got 'gaussian'` | pick one of the three |
| `detection_threshold must be in [0, 1) (a fraction of the detection-image max), got 1.5` | it is a fraction, not a count |
| `min_separation must be > 0, got -1.0` | omit it to get each mode's faithful default |
| `window must be a positive odd integer, got 20` | the aperture side must be odd (default 21) |
| `ring (2*8.0) does not fit in a 11px window` | enlarge `--window` |
| `pair_tol must be > 0, got 0.0` | also applies to `coloc_distance` and `rms_gate` |
| `output exists: <path> (use overwrite=True / --overwrite)` | pass `--overwrite` (the message names the library keyword first) |
| `--detection-mode/--detection-threshold cannot be combined with --tdat (the .tdat supplies the detection mode); pass one or the other` | drop one side |

> **Ordering gotcha.** Option validation runs **before** any file-existence check, and
> `movie not found` fires before `tmap not found` / `tdat not found`. So
> `tether extract nope.tif -o x --donor-side middle` reports the donor-side error, not the
> missing movie.

### Registration was flagged low-confidence

**Symptom.** On stderr, with the run still succeeding and **exit code `0`**:

```text
  warning: registration RMS 0.368 px exceeds the 0.2 px gate (7 control points); molecules tagged 'low-confidence-registration'.
Extracted 5 molecule(s) -> …\warp2.tether
```

The library-level form of the same verdict (`tether.imaging.calibrate`) reads:

```text
registration RMS residual 0.368 px exceeds the 0.200 px gate (channels 0->1, 7 control points, source native): calibration flagged low-confidence; molecules will be tagged 'low-confidence-registration'.
```

**Cause.** The fit's RMS residual exceeded `--rms-gate` (default
`DEFAULT_RMS_GATE_PX = 0.5` px). Under the default `warn` policy this is **accept-with-flag**:
molecules are tagged, never dropped.

> **Signature.** `/calibration/<cal-id>.attrs` carries
> `low_confidence: True` alongside `rms_residual`, `n_control_points`, `gate_px`, `degree`
> and `source`; every row of `/molecules.tags` carries `low-confidence-registration`.
>
> **In `tether batch` the warning disappears entirely** — the end-of-run report prints the
> stage *status* only, and the flag survives only in the JSONL `detail`
> (`"…; low-confidence registration (flagged)"`). Run with `--policy fail` to make an
> over-gate movie fail loudly and exit `1`.

**Remedy.** Nothing downstream currently filters on the tag — no GUI banner, no analysis
exclusion — so treat it as a prompt to look at the registration, not as a handled condition.
Improve the fit (more control points, `--prealign similarity`, a looser `--pair-tol` so real
pairs are not discarded), or import a dedicated calibration with `--tmap`. Note that an
imported `.tmap` leaves the residual NaN and therefore **can never trip the gate** — a clean
gate on an imported map means nothing was measured, not that the map is good.

### A correction step refuses: run something else first

**Symptom.** One of these `ValueError`s from `tether.project.gamma` or
`tether.project.leakage`:

```text
/molecules row 0 has no photobleach frames (bleach_frames=(-1, -1), the undetected sentinel); run compute_photobleach() before compute_leakage_alpha()
/molecules row 0 has no photobleach frames (bleach_frames=(-1, -1), the undetected sentinel); run compute_photobleach() before compute_gamma()
/molecules row 0 has no leakage factor (alpha is NaN); run compute_leakage_alpha() before compute_gamma()
```

**Cause and remedy.** These messages *are* the fix — they encode the canonical order
**`compute_photobleach` → `compute_leakage_alpha` → `compute_gamma`**. `(-1, -1)` is the
documented "photobleaching not yet computed" sentinel; `alpha` being NaN is "leakage not yet
computed". `tether batch` runs the chain for you; you only see these when driving the
library directly.

Two siblings from the same family:

- `project has no /traces/<layer>; run extraction first` (a `KeyError`, identical text in
  `tether.project.photobleach`, `.leakage` and `.gamma`) — the store has no traces yet.
- `intensity_quantity must be one of ['corrected', 'raw'], got 'bogus'` — only those two
  layers exist. `tether.project.export` phrases the same condition differently:
  `unknown intensity_quantity 'bogus'; expected one of ['corrected', 'raw']`.

### A condition query or per-condition overlay returns nothing

**Symptom.** `query_molecules(project, key={"ligand": "tRNA"})` matches zero molecules, or a
per-condition histogram overlay **called with that same `key=` filter** comes back with no
curves — while the plain population histogram over the same store is fine.

The `key` filter is the whole story: an overlay called *without* `key` groups on the raw
`condition_id` and is unaffected by everything below, so if your unfiltered overlay is empty,
this is not your entry.

**Cause.** Two rules in `tether.analysis.query`, both deliberate:

1. A **`key` filter only sees materialized `/conditions` rows.** Until
   `tether.project.conditions.sync_conditions()` has run, a molecule's provisional
   `condition_id` cannot be resolved back to a key, so it is not key-matchable and the filter
   excludes it. `per_condition_apparent_e_histograms` forwards `key` to `query_molecules`, so
   it inherits this rule and nothing else.
2. **A condition query never returns a molecule with an empty `condition_id`** — it is a
   *condition* query by definition. This one is a defensive invariant rather than a likely
   cause: every Tether writer stamps the id from `ConditionKey.condition_id()`, which always
   returns a non-empty `cond-<12 hex>`, so no natively-extracted or imported store should
   contain one. If you do meet an empty id, note that `sync_conditions` cannot repair it — it
   skips empty ids outright (`if not cid: … continue`) and excludes them from `referenced`, so
   it reports `n_unresolved=0` while fixing nothing. `rekey_condition` is not the escape hatch
   either; it rejects an empty `from_condition_id`.

> **Signature.** Verified end to end on a freshly batch-extracted 7-molecule project
> (`Tbox_Cy3Cy5_tRNA_100nM_25C.tether`), running each probe before and after the sync:
>
> ```text
>                                before sync   after sync
> plain population histogram N :      7             7
> query (no filter) matches    :      7             7
> query key={'ligand':'tRNA'}  :      0             7
> per-condition overlay curves :      1             1
> overlay + key={'ligand':...} :      0             1
> sync_conditions -> created_ids=('cond-6115d12da682',), n_unresolved=0
> ```
>
> Only the two `key`-filtered rows moved. The unfiltered query, the plain histogram and the
> plain per-condition overlay were never affected — if one of those is what came back empty,
> `sync_conditions` will not change it.

**Remedy.** Call `sync_conditions(path)` once per store before using `key` filters, then
`validate_conditions(path)` to see anything still dangling. `sync_conditions` reports
`n_unresolved` for ids with neither an existing row nor a filename witness — but read that
number narrowly: `n_unresolved=0` means every **non-empty** id either already had a row or got
one from a filename witness. It is not a claim that every molecule is now key-matchable.

### A plot shows fewer molecules than I curated

**Cause.** Two on-by-default invariants on every store-level group-B/C entry point, both
Tether additions with no tMAVEN analogue: **stale** molecules are excluded, and **rejected**
molecules are excluded. A molecule goes stale when its input trace, its analysis window, or
its effective applied α/γ changed after the model was fitted — so re-running the corrections
can silently empty a TDP that worked yesterday.

**Remedy.** Pass `include_stale=True` / `include_rejected=True` to see them, or re-idealize
to make the model fresh again. Mind the blast radius: the α estimator is **store-wide** —
`compute_leakage_alpha` fits one dataset median over every analysable molecule and writes it
to every one of their `/molecules.alpha` cells, with no partition by condition — so
re-estimating α re-stales **every** molecule in the project, not one condition's cohort. A
γ-median shift re-stales only the molecules that took the fallback. See
[staleness](glossary.md#staleness-stale-fresh-live).

### The project will not open

Raised by `tether.project.core.Project.open` / `tether.io.schema`. In the GUI these appear on
the status bar as `Open project failed: <msg>` — the shell never crashes, and the previously
loaded project stays in place.

| Message | Cause | Remedy |
|---|---|---|
| `no such .tether project: <path>` | path typo, or the file moved | — |
| `file schema_version 99 is newer than this app's 1; refusing to open (PRD section 5.4).` | written by a newer Tether | **upgrade Tether.** Do not attempt to edit the file |
| `<path> is not a .tether project (format marker=None)` | valid HDF5, but not ours — an SMD, a model, or a `.mat` opened by mistake | open the right file |
| `<path> is not a readable .tether HDF5 project` | not HDF5 at all, or truncated beyond reading | restore from backup; check the copy completed |
| `<path> is not a complete .tether project; missing: /movies, /molecules, …` | a half-written store; every missing group is named | re-extract. `tether extract` itself writes atomically (temp file + replace), so this comes from an external interruption |

> A batch **resume** additionally refuses a newer-schema output even under `--overwrite`:
> `--overwrite` means "redo my extraction", not "discard whatever a newer Tether wrote". A
> foreign or half-written HDF5, by contrast, falls through to the normal re-attempt path.

---

## Setup and environment failures

### Idealization cannot find the sidecar interpreter

**Symptom.**

```text
SidecarError: no sidecar interpreter: pass sidecar_python= or set TETHER_SIDECAR_PYTHON to a Python in an env built from sidecar/conda-lock.yml with tMAVEN installed
```

or, when the path is set but wrong:

```text
SidecarError: sidecar interpreter does not exist: C:\nope\python.exe
```

Both are raised by `resolve_sidecar_python` in `tether.idealize.driver`, both are
**deterministic** (`transient=False`), so the supervisor fails fast instead of burning its
restart budget.

**Cause.** The interpreter is resolved in this order: the explicit `sidecar_python=` argument
→ `$TETHER_SIDECAR_PYTHON` → the installer's sibling `envs/sidecar` derived from
`sys.prefix`. None of the three resolved.

On a proper install the third step should cover you even when the environment was never
activated — that fallback exists precisely because a menu shortcut, a `PATH` shim or a
`.desktop` entry never runs the conda `activate.d` hook that sets the variable
([ADR-0051](adr/0051-installed-app-launch-surface.md)). If you see this error from an
installed app, the `envs/sidecar` environment is missing or was moved.

**Remedy.** From a source checkout, build the sidecar with `python scripts/setup_sidecar.py`,
which creates the env from `sidecar/conda-lock.yml`, installs the pinned tMAVEN, probes
liveness, and prints the line that points Tether at the interpreter. See
[Guided sidecar setup](idealize/standalone-tmaven-handoff.md#guided-sidecar-setup). Then set
`TETHER_SIDECAR_PYTHON`, or pass `--sidecar-python` to `tether batch`.

### The sidecar starts but tMAVEN cannot import `pkg_resources`

**Symptom.** Idealization fails with a sidecar-reported error; the stderr tail carried in
Tether's wrapper message names `pkg_resources`. The Tether-side wrapper
(`tether.idealize.driver`) looks like:

```text
sidecar idealization failed (exit 1): <the sidecar's own error>
--- stderr (tail) ---
…
```

**Cause.** tMAVEN calls `import pkg_resources` at runtime while setting up its version log
(`tmaven/maven.py`), without declaring the dependency. setuptools **removed `pkg_resources`
in v81**, so a sidecar environment built with a newer setuptools cannot construct
`maven_class` at all.

**Remedy.** Get `setuptools<81` into the sidecar environment. Which side you are on decides
whether that has already happened.

*Sidecar built from a source checkout.* `scripts/setup_sidecar.py` applies the pin for you
(`SETUPTOOLS_PIN = "setuptools<81"`), and that script is the **only** place the pin lives:
`sidecar/conda-lock.yml` deliberately resolves setuptools `82.0.1`, and the script's own pip
step downgrades it after the env is created. Do not go looking for the pin in the lock file or
the workflow — `.github/workflows/sidecar.yml` just calls the script.

*Sidecar that came with the installer.* The pin is applied for you. `envs/sidecar` is
materialised from the rendered `sidecar/conda-lock.yml`, which resolves setuptools `82.0.1`,
so the installer bundles `setuptools<81` as a third offline wheel and the `post_install`
script lays it over that env before installing tMAVEN. You should not see this error on an
installed app; if you do, it is a bug worth reporting.

*Environment you built by hand.* Nothing applies the pin, so downgrade it yourself with the
sidecar's own interpreter (needs network):

```text
# Linux / macOS
<install-prefix>/envs/sidecar/bin/python -m pip install "setuptools<81"

# Windows
<install-prefix>\envs\sidecar\python.exe -m pip install "setuptools<81"
```

Then re-run the liveness probe or the idealization. The same command is the recovery step if
you have upgraded setuptools inside `envs/sidecar` yourself. This affects the **sidecar**
environment only — Tether's base environment does not import `pkg_resources`.

### Idealization times out, or is restarted repeatedly

**Symptom.** From `tether.idealize.driver`:

```text
sidecar idealization timed out after 1800.0s
--- stderr (tail) ---
```

or, after the restart budget is spent (`tether.idealize.supervisor`):

```text
sidecar idealization failed after 3 restart(s); last error: …
```

**Cause.** The default per-call timeout is 1800 s and the default restart budget is 3. A cold
tMAVEN `vbconhmm` fit is dominated by JIT compilation, so the *first* fit of a session is far
slower than the rest. A timeout or crash is classed **transient** and is retried; an error the
sidecar itself reports is **deterministic** and is never retried — re-running it will only
repeat the same failure, so look at the data or the model choice instead.

**Remedy.** Raise `--sidecar-timeout`, or `--max-restarts` for a flaky machine. `0` disables
restarts.

### A Deep-LASI `.tdat` uses a particle-detection mode Tether does not implement

**Symptom.**

```text
tether extract: could not use --tdat mode4.tdat: Deep-LASI ParticleDetectionMode 4 is not supported by Tether (only [1, 2, 3] = wavelet/intensity/bandpass; modes 4 'local-variance' and 5 'ZMW intensity' are not ported)
```

Raised by `tether.io.tdat`, wrapped by `tether.project.extract`. A related message tells you
the file is not a TIRFdata container at all, and prints its actual root keys so you can see
what you handed it:

```text
tether extract: could not use --tdat notdat.tdat: 'notdat.tdat' is not a Deep-LASI TIRFdata .tdat (no 'temp' struct; root keys: ['junk'])
```

**Remedy.** Re-run **without** `--tdat` and choose a supported `--detection-mode` yourself.
Tether refuses rather than silently substituting a mode, so an import can never mis-detect
under the wrong method ([ADR-0021](adr/0021-particle-detection-modes.md)).

> A related non-error: the threshold decode is deliberately best-effort. If the `.tdat`'s
> MCOS threshold blob cannot be decoded, `tether.io.tdat` returns `None` and the detector
> keeps its own default — this never raises, so a slimmed `.tdat` silently leaves your
> threshold at the CLI/library value.

### The project is locked by another user or machine

**Symptom.** One of these from `tether.project.lock`:

```text
LockedError: <path>.tether.lock is locked: held by alice@OTHERPC (pid 424242, acquired 2026-07-21T02:33:13.661070+00:00)
LockedError: <path>.tether.lock is locked: held by alice@OTHERPC (pid 424242, acquired 2020-01-01T00:00:00+00:00; stale)
LockedError: <path>.tether.lock is locked: the lock file is corrupt (unknown owner)
CorruptLockError: unparseable lock file: <path>.tether.lock
```

**Cause and remedy — the three cases differ, and the message tells you which one you have.**

- **No `; stale` suffix** — a live writer. Close the other Tether window, or ask the named
  host's user to. Do not steal.
- **`; stale`** — the holder has been idle longer than the ≈30-minute staleness timeout.
  Liveness is judged by wall clock, not by probing the PID, because a remote PID cannot be
  probed and cloud storage is eventually consistent. A stale lock is reclaimable only by an
  **explicit steal**; it is never auto-acquired, and the ousted owner's unsaved work is not
  merged.
- **`the lock file is corrupt (unknown owner)`** — the `.lock` JSON is unreadable, so no
  owner can be identified and writes are refused until an explicit steal overwrites it.
  Deleting the stray `.tether.lock` file is the manual fix.

While locked out you can still open the project read-only and curate into your own
split/subset `.tether`, merged back later on `molecule_key`.

### The spot and overlap views are unavailable

**Symptom.** A project opens and curates fine, histograms and idealization work, but the
movie panel and the per-molecule spot/overlap view are not wired, and the shell shows:

```text
coordinates and patches absent; movie round-trip and spot/overlap views unavailable
```

**Cause.** This is an **analysis-only** import — a coordinate-less project built from an SMD
or a `…-donc-accc-w.txt` with no `.tdat` or `.mat` to supply pixel coordinates. It is not a
failure: `tether.gui.shell` deliberately leaves the overlap seam unwired because the view is
meaningless without coordinates, and surfaces the marker banner once
([ADR-0046](adr/0046-analysis-only-smd-import.md)). Every molecule also carries the
`round-trip-unavailable` tag.

**Remedy.** To get the round-trip views you must re-import with a coordinate source present —
the movie **plus** the `.mat` (the shipped reconstruction path needs both). The import wizard
refuses up front rather than producing a half-wired project, with reasons such as
`'<key>' cannot reconstruct: no movie and no .mat trace source`. See the
[coordinate-availability matrix](io/legacy-import.md#coordinate-availability-matrix).

### The installer is flagged as unsigned

**Symptom.** Windows SmartScreen or macOS Gatekeeper warns that the installer is from an
unidentified developer.

**Cause.** **Every Tether installer published so far is unsigned** — release builds included.
Code-signing is wired into the release pipeline but **gated on repository variables** that are
not yet set: `.github/workflows/release.yml` runs the SignPath submission only
`if: runner.os == 'Windows' && vars.SIGNPATH_ORGANIZATION_ID != ''`, and the macOS
`codesign`/`notarytool` leg only `if: … vars.APPLE_SIGNING_ENABLED == 'true'`. With the gates
off, each build emits a `::warning::` saying the installer ships UNSIGNED, and that is the
current state of the published assets. Re-downloading from the release page will reproduce the
same warning.

**Remedy.** Verify the download instead of trusting the OS prompt, then click through it:

- Check the file against the release's `SHA256SUMS-<platform>.txt` (`sha256sum -c` on
  Linux, `shasum -a 256 -c` on macOS, `Get-FileHash` on Windows).
- Check the **build-provenance attestation** the pipeline publishes for the same assets
  (`gh attestation verify <file> -R bioedca/tether`).

Authenticode and Apple-notarized installers arrive once SignPath enrollment and the Apple gate
are completed — the maintainer-side steps are in
[Releasing (signed installers)](release.md), and macOS additionally needs a hardened-runtime
pass over the conda payload before `APPLE_SIGNING_ENABLED` can be flipped.

### The deep classifier does not see the GPU

**Symptom.** `torch.cuda.is_available()` is `False`, or the CUDA runtime fails to initialize,
in the optional `deep/` environment.

**Cause.** The committed `deep/conda-lock.yml` is **CPU-only on purpose**, so that CI stays
reproducible; a CUDA build is selected at install time and is deliberately outside
pin-and-hold ([ADR-0047](adr/0047-deep-model-optional-stack-and-dataset.md)). The usual
failure is a wheel whose CUDA channel (`cu124` / `cu126` / `cu128`) does not match the
installed NVIDIA driver.

**Remedy.** Install the wheel whose channel matches your driver, per the PyTorch
*Start Locally* selector. The CUDA wheel bundles the CUDA runtime, so only an NVIDIA
**driver** is needed — no system CUDA toolkit. Full instructions and the reference GPU floor
are on the [Deep trace classifier](ml/deep-classifier.md#gpu-cuda-setup) page.

---

## Exit codes, and where the details go

| Command | `0` | `1` | `2` |
|---|---|---|---|
| `tether extract` | success — **including a low-confidence-registration warning** | the run failed; the message is on stderr as `tether extract: <msg>` | argparse rejected the command line |
| `tether batch` | every movie finished without a `failed` stage — **including `deferred`**, `not-requested` and `warning` | at least one movie had a failed stage | argparse rejected the command line, **or** the run refused to start (colliding output basenames, a bad supervision argument) |
| `tether` (no subcommand) | prints help | — | an unknown subcommand or top-level flag |

Two notes on that table:

- **`2` is argparse, not Tether.** A missing or unknown option never reaches Tether's own
  validation: `tether extract movie.tif` without the required `-o/--output` prints a usage
  block and exits `2`, as does any unrecognized flag or unknown subcommand. A *bad value* for
  a known option is different — that is Tether's own `ExtractOptions.__post_init__` check, one
  line on stderr and exit `1`.
- **`blocked` never appears on an exit-`0` run.** A stage is recorded `blocked` only when its
  upstream stage was not `done`/`skipped`, and the only way upstream lands there is `failed`
  (or `blocked`, which itself implies a `failed` further up). Any `blocked` therefore travels
  with a `failed` in the same movie, which flips `MovieResult.ok` and exits `1`.

Two things the console deliberately does not print, and where to find them instead:

- **Stage details.** `tether batch`'s end-of-run report prints `stage=status` and appends the
  error text only for a `failed` stage. Everything else — the over-gate flag, the α-withheld
  fallback, the deferral reason — lives in the **JSONL structured log**, whose path is echoed
  to stderr on every run (`<out-dir>/batch-log.jsonl` by default, or `--log`).
- **Basename collisions.** Two input movies with the same stem are refused **up front**, exit
  `2`, before any work: `movies '…/flat.tif' and '…/sub2/flat.tif' both map to
  '…/bout/flat.tether'; rename one or use a separate --out-dir`. This is the classic
  `movie_010.tif` in two condition folders; the checkpoint would otherwise treat the second
  movie as already done using the first's data.

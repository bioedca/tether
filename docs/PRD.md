# Tether — Product Requirements Document

**Tether** is a cross-platform, open-source (GPL-3.0) single-molecule FRET (smFRET) analysis suite for the
Mondragón Lab (Northwestern). It owns the full **movie → trace → corrected FRET → idealized states** pipeline,
embeds **tMAVEN** [Verma2024] for HMM idealization, and is built around a **provenance-first** data model so that
any trace can be resolved back to its exact location in the source movie, and any location in the movie back to
its trace.

| | |
|---|---|
| **Document type** | Product Requirements Document |
| **Version** | 1.2 |
| **Repository** | `github.com/bioedca/tether` (public, GPL-3.0; governance §12) |
| **Primary platform** | Windows + NVIDIA GPU (base app CPU-only and cross-platform; GPU optional) |
| **License rationale** | GPL-3.0 permits embedding tMAVEN (GPL-3.0) |

---

## Table of contents

1. [Overview & vision](#1-overview--vision)
2. [Goals & non-goals](#2-goals--non-goals)
3. [Target users & use cases](#3-target-users--use-cases)
4. [System architecture](#4-system-architecture)
5. [Data model — the provenance project store](#5-data-model--the-provenance-project-store)
6. [Processing pipeline](#6-processing-pipeline-movie--states)
7. [Functional requirements](#7-functional-requirements)
8. [Non-functional requirements](#8-non-functional-requirements)
9. [Milestones & acceptance criteria](#9-milestones--acceptance-criteria)
10. [Risks & mitigations](#10-risks--mitigations)
11. [Conventions & glossary](#11-conventions--glossary)
12. [Development & version-control protocol (GitHub)](#12-development--version-control-protocol-github)
- [Appendix A — Input formats](#appendix-a--input-formats)
- [Appendix B — Correction-factor scheme](#appendix-b--correction-factor-scheme-single-laser-2-color)
- [Appendix C — tMAVEN plot inventory](#appendix-c--tmaven-plot-inventory-m6-native-reproduction-scope)
- [Appendix D — tMAVEN SMD + model HDF5 schema](#appendix-d--tmaven-smd--model-hdf5-schema)
- [Appendix E — Native extraction specification](#appendix-e--native-extraction-specification)
- [References](#references)

### Source-citation conventions

All source citations are relative to the configurable **reference root** `$SMFRET_REFERENCE_ROOT`, which is set
outside the repository and holds read-only local clones of the reference implementations and a fixture set:

- **Deep-LASI** (MATLAB) [Wanninger2023] — paths are relative to `deeplasi/functions/` (e.g.
  `deeplasi/functions/traces/extractTracesC.m:20-33`).
- **tMAVEN** (Python) [Verma2024] — paths are relative to `tmaven/tmaven/` (e.g.
  `tmaven/tmaven/controllers/analysis_plots/data_hist1d.py`); pinned at commit
  `10f4230b6d13c6d2ad67b05d801696b4a40eff4a`.
- **MASH-FRET** (MATLAB + docs) [Börner2018] — e.g. `MASH-FRET/docs/trace-processing/workflow.md`.
- **Reference fixtures** — real lab files under `example-data/` (Appendix A).

Bracketed keys such as [Roy2008] index the [References](#references) section. The reference clones are used for
algorithm reference only; they are never vendored into the Tether repository.

---

## 1. Overview & vision

Tether unifies, in one Python codebase, the steps a single-molecule FRET (smFRET) [Förster1948][Ha1996] lab
currently splits across several tools: native extraction of donor/acceptor intensity traces from dual-view
total-internal-reflection-fluorescence (TIRF) movies [Axelrod2003] (today done in Deep-LASI [Wanninger2023] or
MASH-FRET [Börner2018]), photophysical corrections and FRET computation, hidden-Markov idealization (today done in
tMAVEN [Verma2024]), and population-level histogram/kinetics analysis. Three properties distinguish it:

1. **Provenance is the product.** Every trace permanently records its source movie (relative path + content hash),
   sub-pixel donor/acceptor coordinates, integration aperture, frame range, corrections applied, idealization, and
   annotations. The trace↔movie round-trip is a property of the data model, not a bolted-on feature. This matters
   because the link is lost in current workflows: a tMAVEN SMD built from Deep-LASI `…-donc-accc-w.txt` exports
   carries no pixel coordinates at all (Appendix D), so today a curated trace cannot be traced back to its spot in
   the movie. Tether stores coordinates natively; a Tether-authored SMD carries them as superset metadata, and the
   trace↔movie link survives a standalone-tMAVEN round-trip because Tether re-resolves each returned trace to its
   molecule from its own retained store by exact intensity-trace matching — tMAVEN's container has no per-molecule
   metadata slot and its exporter applies the GUI selection mask, so coordinates in a *tMAVEN-written* SMD are not
   trusted or required (§5.3, §7.4).

2. **Model-free lenses are first-class.** Donor–acceptor cross-correlation and the raw successive-frame FRET
   "cloud" are surfaced prominently. They reveal dynamics *without* committing to an HMM — a capability neither
   tMAVEN nor Deep-LASI offers.

3. **Statistics derive from provenance.** Because every molecule's contribution is retained, error bars, bootstrap
   confidence intervals [König2013], per-condition splits, and the click-bin → molecules → movie drill-down are
   available by construction.

Tether reuses rather than reinvents: tMAVEN for HMM idealization (embedded for one-click use *and* reachable as the
standalone GUI), Deep-LASI and MASH-FRET as algorithm references (corrections and extraction cross-checked against
both — Appendices B and E), HDF5 for self-describing files, and the SMD format [Greenfeld2015] for interchange.

---

## 2. Goals & non-goals

### 2.1 Goals

- **G1 — Native, faithful extraction.** Reproduce Deep-LASI's movie→trace logic for the two-color single-laser
  case to a defined numerical tolerance (Appendix E; §9 acceptance criteria), from day one ("extraction-first").
- **G2 — Provenant data model.** A single self-describing project file per experiment in which every trace
  round-trips to its movie spot and back (§5).
- **G3 — One-click idealization with verified tMAVEN parity.** In-app idealization (vbFRET, consensus VB-HMM,
  ebFRET) via an embedded tMAVEN sidecar, plus bidirectional hand-off to the standalone tMAVEN GUI (§7.4, §9
  idealization). *Parity* means statistical agreement within a stated tolerance (state count, state means,
  Viterbi-path agreement, ELBO), **not** bit-identical reproduction — the pinned tMAVEN self-reseeds its RNG, so
  bit-exactness is unachievable without patching its GPL core (§7.4, §10).
- **G4 — Scientifically correct FRET.** Background, leakage (α), and γ corrections following the accepted
  accurate-FRET framework [Hellenkamp2018][Roy2008][Lee2005][McCann2010] (Appendix B).
- **G5 — Per-condition, persistent, incrementally-improving curation.** A sort/rank ML model that travels with a
  *condition* (≈100 videos across many days/files), warm-start-retrains video-by-video, and never auto-drops (§7.5).
- **G6 — Lab-friendly & cross-platform.** One Python codebase on Windows/Mac/Linux; non-technical lab members never
  touch a terminal; signed installers eventually.
- **G7 — Headless-first core.** A GUI-independent, scriptable core and an overnight, error-isolated, checkpointed
  batch runner (§7.11, §8).
- **G8 — First-class Deep-LASI re-analysis.** Re-open existing Deep-LASI acquisitions, recover coordinates and
  curated subsets, and reconstruct a round-trip-ready project without re-extraction (§7.8).

### 2.2 Non-goals (explicit scope boundaries)

- **N1 — No ALEX/PIE, no stoichiometry, no three-color.** Two-color, single-laser only. Consequently the direct-
  excitation correction δ is structurally inert (= 0): its estimator requires the acceptor-under-acceptor-excitation
  channel that only ALEX provides [Lee2005][Hohlbein2014] (Appendix B).
- **N2 — No data simulator in v1.** Validation uses real labeled traces and the kinSoftChallenge benchmark
  [Götz2022]; a simulator is deferred.
- **N3 — Bounded plot parity.** The native analysis surface reproduces exactly the seven tMAVEN plot types
  (Appendix C); any other tMAVEN plot is reachable via the standalone hand-off, not reimplemented.
- **N4 — Out-of-scope legacy formats.** Supported legacy inputs are Deep-LASI (`.tdat` + `.tmap` + `.txt` + `.mat`)
  and tMAVEN SMD (`.hdf5`). Older `.dat` and vbFRET `.mat` formats are out of scope.
- **N5 — No central server.** Each lab member runs on their own machine; data lives on OneDrive / a network share.

---

## 3. Target users & use cases

**Primary users.** Bench scientists in the Mondragón Lab who acquire dual-view TIRF smFRET movies and currently
process them through Deep-LASI + tMAVEN. They range from expert (comfortable scripting) to non-technical (GUI only).
Data lives on OneDrive or a network share; there is no shared server.

### 3.1 Core use cases

- **UC1 — Extract & browse a fresh acquisition.** Load a dual-view movie, extract coordinate-tagged
  donor/acceptor traces, and browse them in a keyboard-driven round-trip viewer where selecting a trace jumps the
  camera to its movie spot and clicking a spot opens its trace.
- **UC2 — Curate at scale with logged labels.** Accept/reject/categorize hundreds of traces per sitting (≈1–2 s
  per trace), with every action logged into the project so it trains the per-condition model.
- **UC3 — Per-condition curation loop (the central workflow).** A *condition* spans ≈100 videos across multiple
  days and files. The loop is: load the condition's persistent model → curate one video → the model warm-start-
  retrains on the new labels → save → open the next video and reload the model so it surfaces good traces faster
  each time (§7.5).
- **UC4 — Correct & idealize.** Apply background/leakage/γ corrections, then one-click idealize kept traces with
  verified tMAVEN parity (statistical tolerance, §7.4), review the step overlays, and optionally hand off to the
  standalone tMAVEN GUI.
- **UC5 — Produce a publication figure.** Export a per-condition FRET histogram with bootstrap CI, a TDP, and
  dwell/rate fits, each as vector PDF/SVG + PNG, stamped with provenance and parameters.
- **UC6 — Re-analyze existing Deep-LASI work.** Hand Tether a Deep-LASI acquisition's file set; it auto-pairs the
  files to the movie, recovers coordinates and the curated subset, and builds a round-trip-ready project without
  re-extraction (§7.8).
- **UC7 — Overnight batch.** Queue many movies for unattended extraction + correction + idealization, with per-
  movie error isolation, checkpoint/resume, and an end-of-run summary (§7.11).

### 3.2 North-star adoption test

A scientist can: **open a real dataset → browse/curate with logged accept/reject → one-click vbFRET (verified
tMAVEN parity) → export a per-condition FRET histogram with bootstrap CI ready for a figure.** This is the
capability delivered by the **M2–M3** milestone set (§9); it is a non-binding adoption aspiration, deliberately not
operationalized as a calendar gate (§9 is capability-sequenced, with no schedule commitment anywhere).

---

## 4. System architecture

### 4.1 Technology stack

- **Language:** Python ≥ 3.11 for the base app, pinned to one concrete version inside Numba's supported window
  [Lam2015] (Appendix A), with an explicit numpy upper bound set to a tested Numba-supported numpy ceiling. This pin
  is distinct from the tMAVEN sidecar's `numpy<2` pin. **Version policy = pin-and-hold:** the exact pins for the
  whole base stack (Python, numpy, Numba, **and the GUI stack — napari / PySide6 / pyqtgraph / scikit-image**) live
  in a committed `conda-lock` that is the single source of truth, frozen per Tether release and bumped deliberately,
  with a "tested-against" 3-OS CI matrix on top (not track-latest). The PRD intentionally does not hardcode version
  numbers (they would go stale); the lock file does.
- **GUI:** PySide6 (custom shell) + **napari** embedded as a clean movie panel + pyqtgraph for fast trace/plot
  docks. Traces and curation are first-class surfaces.
- **Compute:** NumPy, SciPy, scikit-image, **Numba** for hot kernels [Lam2015], pandas.
- **Idealization:** **tMAVEN** (GPL-3.0) [Verma2024] in an isolated **sidecar** with its own conda environment
  (PyQt5 + `numpy<2`), pinning the **subset of tMAVEN's `install_requires` needed for vbFRET / consensus VB-HMM /
  ebFRET** rather than the full set: `biasd @ git+main` is omitted (lazy-imported, not on conda-forge, unused by the
  three in-scope HMM methods) and the unbounded `numba>=0.51.0` is given an explicit upper bound, so the sidecar can
  ship inside an offline signed installer (§4.3, §9 M9). Data is exchanged as SMD-HDF5; the same export is the
  standalone-GUI hand-off.
- **ML:** scikit-learn / XGBoost [Chen2016] for the classical, warm-start/incremental per-condition model →
  PyTorch (deep, GPU) for DeepFRET-style trace classifiers [Thomsen2020], hosted in a **third isolated `deep/`
  conda stack** (mirroring the sidecar; the CPU build is locked for CI via the `pytorch-cpu` metapackage, the CUDA
  build documented for the GPU floor) and consumed behind a lazy import so the CPU base app is unaffected
  (M8; ADR-0047, `deep/environment.yml` + `deep/conda-lock.yml`).
- **Storage:** immutable source TIFF via `tifffile.memmap`; per-experiment project = a single self-describing
  **HDF5** file; an optional cached Zarr movie pyramid in local scratch (never synced).
- **Packaging:** conda-forge + **constructor** installers; a guided sidecar-environment setup script for v1, full
  bundling at M9.
- **Repo/CI:** public from M0, GitHub Actions (pytest + ruff + 3-OS build), mkdocs documentation; full source-code governance (GitHub Flow + SemVer, signed commits, branch protection, CI-as-merge-gate, supply-chain scanning) is specified in §12.

### 4.2 Module breakdown

- **`tether.io`** — readers (lazy TIFF; Deep-LASI `.tdat`/`.tmap`/`.txt`/`.mat`; tMAVEN SMD), the HDF5 project
  store, the filename→metadata parser, and exporters (CSV, Deep-LASI-style `.txt`, subset `.tether`, SMD-HDF5).
  Applies the Deep-LASI correction-triplet remap on import (Appendix B).
- **`tether.imaging`** — native extraction mirroring Deep-LASI (Appendix E): per-channel split → moving-average
  max-projection detection image → à trous wavelet spot detection [Olivo-Marin2002] → 21×21 aperture (PSF disk
  r = 3) + annular background → Sum integration; and registration: native bead control-points →
  phase-correlation prealign → nearest-neighbour pairing → degree-2 polynomial map (forward + inverse, with a
  numeric RMS-residual gate), or apply an imported `.tmap`; donor-anchored colocalization.
- **`tether.fret`** — photobleaching-step detection (a native, headless reimplementation of tMAVEN's Bayesian
  single-step model [Verma2024], run independently per channel — Appendix E Stage 16); correction factors (Appendix B); corrected FRET
  over the per-trace analysis window; donor–acceptor cross-correlation (vectorized FFT).
- **`tether.idealize`** — tMAVEN sidecar driver (export SMD → run vbFRET/consensus/ebFRET headless via
  `tmaven.maven.maven_class` → import); one-click hand-off to the standalone tMAVEN GUI with non-destructive
  re-import; idealization staleness tracking; dwell/rate analysis.
- **`tether.ml`** — per-condition, persistent, incrementally-retrained feature extraction + classifier (sort/rank
  quality, never auto-drop) [Chen2016]; similarity search; active-learning loop; model load / warm-start-retrain /
  save as a portable artifact; deep models later [Thomsen2020].
- **`tether.analysis`** — histograms with CI [König2013], transition density plots [McKinney2006], the raw FRET
  cloud, the anticorrelation-event finder, per-condition population statistics, and the seven tMAVEN plot types
  (Appendix C).
- **`tether.gui`** — the PySide6 shell + embedded napari panel, the multi-movie round-trip browser,
  curation/labeling, annotation, and plot docks.
- **`tether.project`** — the experiment/session model plus the batch runner and headless API; the GUI is a thin
  layer over this core.

### 4.3 tMAVEN sidecar pattern

tMAVEN is a PyQt5 application pinned to `numpy<2` (`tmaven/setup.py`: `numpy>=1.21.0,<2.0.0`, `PyQt5>=5.15.0`),
whereas Tether's own GUI is PySide6/napari on current numpy. To avoid a dependency conflict, tMAVEN runs in its own
environment/subprocess. Its computational core is GUI-free: `tmaven.maven.maven_class` (`tmaven/tmaven/maven.py:15`)
imports no Qt and exposes `io` (SMD read/write) plus a `modeler` whose `run_vbhmm` (vbFRET), `run_vbconhmm`
(consensus VB-HMM), and `run_ebhmm` (ebFRET) methods are pure NumPy/Numba and therefore callable headlessly. The
SMD container the standalone GUI reads/writes is the same `dataset/{data,sources,tMAVEN}` structure Tether exports
(Appendix D), so a hand-off SMD opens directly in standalone tMAVEN. If the headless `maven_class` cannot be driven
reproducibly across OSes, the **pre-committed escalation** is a prebuilt **bundled sidecar invoked over a stable
IPC** (preserving the numpy isolation the sidecar exists for) — not an in-process embed and not a hand-off-only MVP
(§9 M0.5, §10).

---

## 5. Data model — the provenance project store

A single `.tether` (HDF5) file per experiment. The full group skeleton is forward-declared and version-stamped at
M0 so that later milestones add *data*, not *structure*.

### 5.1 Group skeleton

- **`/movies/{id}`** — source URI (relative), full `sha256`, a **metadata-only fast signature** (file size + mtime
  + offline-availability flag, e.g. `FILE_ATTRIBUTE_OFFLINE`) for routine no-hydration checks — it performs **zero
  byte reads**, so it never forces a OneDrive Files-On-Demand placeholder to hydrate; dims, dtype + endianness,
  frame_time, dual-view geometry, calibration reference. An optional head/tail content hash is computed only at
  extraction (file guaranteed local) and consulted only inside the already-hydrated relink/verify path. Integrity-
  check timing is defined in §5.4 (cheap metadata-only signature on open; head/tail hash + full `sha256` only on
  relink/explicit verify; a dehydrated OneDrive placeholder is never auto-hydrated).
- **`/calibration/{id}`** — registration transform (polynomial coefficients) + source bead/grid image reference,
  from an imported `.tmap` or a native bead/grid fit (Appendix E, Stages 6–10).
- **`/molecules`** (table) — `molecule_id` (a **globally stable UUID**, inherited unchanged by any split/subset
  file at branch time — §7.10), `molecule_key` (the **cross-file content identity** = the molecule's `movie_id`
  `sha256` + quantized sub-pixel `donor_xy`; this is the join key for split-file merge-back — §7.10 — and is
  persisted into movie-less subset exports so a labeled subset row can always be resolved to its canonical
  molecule), `movie_id, donor_xy, acceptor_xy, aperture, frame_range,
  analysis_window(pre, post), bleach_frames(D, A), corrections(α, γ + δ = 0 inert + method + confidence),
  curation_label, category, quality_class, condition_id, condition_id_provisional, source_filename, tags`. The three
  per-trace label fields are **independent**: `curation_label` is the explicit, separately-logged accept/reject
  state (§7.5); `category` is an optional value from the editable per-condition list (§7.6) and assigning it does
  **not** imply accept (a trace may be accepted-but-uncategorized); `quality_class` is the **read-only ML ranker
  output** (§7.5), never a user input. `condition_id` is provisional-from-filename at extraction and validated at M4
  (§5.1 `/conditions`); the original provisional value (`condition_id_provisional`) and raw `source_filename` are
  retained for provenance across any re-key.
- **`/traces`** — `(n_molecules × n_frames × {donor, acceptor})` raw **and** corrected; FRET derived; per-frame
  background. Chunked + compressed; raw reconstructable on demand. Because one experiment spans **many movies of
  differing frame count and `frame_time`**, this is a single rectangular array **zero-padded to the experiment-max
  `n_frames`** (mirroring tMAVEN's `concatenate_smds` pad-to-`maxt`, and consistent with Appendix D.1's single
  `raw` + `source_index`); each molecule's `frame_range` delimits its valid native extent and its time axis is
  resolved through its `movie_id`'s `frame_time`. **Pad regions carry no FRET and are never fed to the analysis
  window, corrections, or idealization.**
- **`/patches`** — per-molecule local image patch (e.g. 21×21) cached at extraction/import, enabling movie-less
  curation and the static overlap view.
- **`/idealization/{model}`** — state path, levels (means), transition matrix, dwell table, and model evidence
  (ELBO) per molecule, stamped with a **per-molecule provenance hash of the inputs the corrected-FRET was computed
  from** — the molecule's *effective applied* α and γ, the apparent-E toggle, the analysis-window bounds, and the
  input-trace identity (raw + background). This is deliberately **not** a hash of the final E array alone (which
  would miss a window-only edit that rounds to the same E) and **not** the global factor set (which would falsely
  STALE the whole cohort whenever any *unrelated* global median shifts). The re-flag scope is therefore
  **per-factor**: γ carries a per-molecule value with a population-median fallback, so a γ-median shift re-stales
  only the molecules running on that fallback; **applied α is purely global** (the donor-only-sample median applied
  identically to every FRET molecule — §7.2), so an α-median shift correctly re-stales **every** FRET molecule under
  that α. An α recalibration is thus a deliberate **condition-wide re-idealization event**, not a cheap edit (the
  per-molecule-α language in Appendix E Stage 18 documents Deep-LASI's *storage*; Tether's *applied* α is global).
  When inputs change, dependent idealizations are flagged STALE, excluded from TDP/dwell analysis, and offered
  one-click re-idealization. Layout mirrors the tMAVEN model schema (Appendix D).
- **`/conditions`** — structured metadata (construct/variant, dye, ligand + concentration, buffer, temperature,
  laser power, date, replicate) + free tags; auto-parsed from filename (validation mandatory). **Condition identity
  key** = the chemistry/optics tuple **(construct/variant, dye, ligand + concentration, buffer, temperature, laser
  power)**; `date`, `replicate`, and source file deliberately **vary within** a condition. Laser power is part of
  the key because it scales the intensities that feed α and γ. α is scoped **per-condition**; there is no separate finer "session" scope. **Validation is referential:** a
  molecule's `condition_id` is valid only when it resolves to a `/conditions` row built from that key, so two movies
  share a condition iff their key fields match. When two movies meant to be one condition parse to slightly
  different strings, the default is **keep-separate**, with an explicit human-confirmed **merge at M4** (re-keying
  all affected molecules transactionally with a logged audit entry); silent merges of ~100-video conditions are
  never performed. A condition spans many movies across many days/files. The per-condition leakage α and its
  donor-only-sample provenance are stored here. The **editable per-trace category list** (§7.6) and the
  **integer↔category lookup table** (§7.4, Appendix D) also live here as per-condition **data** — the category list
  as a variable-length string dataset (the same affordance as free tags), the lookup as a map attribute on the
  condition row — so both travel with the condition across its many files. They are additive *data* under the
  already-declared `/conditions` group, not new structure, so they do not require a schema-freeze exception.
- **`/settings`** — effective extraction/detection/aperture/registration parameters per experiment, written at
  extraction. A global default config seeds new experiments (per-experiment overrides global); the batch runner
  reads a settings profile.
- **`/features`, `/labels`** — ML feature vectors + labels, scoped per condition. Every `/labels` row carries
  **provenance: the `molecule_key` (§5.1 `/molecules` — the stable cross-file join key), labeler identity,
  timestamp, source experiment file, `source ∈ {human, deeplasi-provisional, cross-condition-seed}`, and a
  `weight`** — all frozen into the M0 schema because adding label-provenance structure later is forbidden by the
  schema freeze (§9 M0). `weight` is the row's **effective training weight, recomputed and rewritten on each
  retrain** (§7.5): human labels are full weight; `deeplasi-provisional` and `cross-condition-seed` labels are
  down-weighted cold-start priors whose weight **decays toward zero as human labels in the condition accrue**
  (§7.5). On split-file merge-back the **owner-curator's retrain recomputes every row's `weight` from the merged
  label set**, so per-split-file stored weights are advisory and superseded centrally — no weight reconciliation is
  needed (§7.10). `/labels` is fed continuously from M2 curation onward; the `molecule_key` + labeler identity
  enable multi-curator reconciliation (§7.5, §7.10).
- **`/models`** — a reference to the per-condition model artifact (a standalone portable file — §7.5 — that
  persists across experiment files and is reloaded/retrained video-by-video), guarded by its **own single-writer
  lock**, plus the active-learning queue. A designated **condition-owner curator** retrains and saves the canonical
  model; other members contribute labels (with labeler provenance) via their own split/subset files that merge back
  on the stable `molecule_key` (§7.5, §7.10).
- **`<file>.lock`** — single-writer marker (host/user/PID/timestamp).

### 5.2 Round-trip mechanics

- **trace → movie:** `memmap(source)` seek + slice — O(1).
- **movie → trace:** a per-movie KDTree over molecule centroids (an experiment may hold many movies; each molecule
  resolves to its own `movie_id`).

### 5.3 Interoperability

The store is an **SMD superset** [Greenfeld2015]. On a **Tether→Tether** SMD round-trip, coordinates travel as
superset metadata that the standalone tMAVEN GUI ignores but Tether re-reads. The trace↔movie link does **not**,
however, depend on coordinates surviving a *standalone-tMAVEN* save: tMAVEN's container has only per-source and
file-level metadata (no per-molecule slot) and its exporter applies the GUI selection mask, so any per-molecule
coordinate array would be silently dropped or reordered on a tMAVEN save (Appendix D.1). Tether therefore treats
its **own retained store as authoritative** and recovers the link on the return leg by **exact intensity-trace
matching** of the SMD `raw` series against its retained traces (with molecule-id/order only as a hint), the same
robust mechanism used for Deep-LASI re-analysis (§7.8). A raw `.txt`-sourced SMD that never carried coordinates is
imported as a degraded, round-trip-disabled **analysis-only** project (§7.8).

### 5.4 Concurrency & lifecycle

- **Single-writer** enforced by `<file>.lock` (host/user/PID/timestamp) + a steal-lock override + **stale-lock
  recovery**. Because OneDrive is eventually-consistent and a remote PID cannot be probed across machines, liveness
  is judged by a **wall-clock staleness timeout** (default ≈ 30 min, configurable) followed by a steal confirmation
  — not by cross-machine PID liveness. The intended posture is **one owner at a time** (sync = backup / sequential
  hand-off, not simultaneous multi-machine editing); Tether additionally **detects and surfaces OneDrive
  conflict-copies** rather than trying to prevent them. Concurrent curation is served by the split-file path (§7.10),
  not by concurrent writes to one file.
- Last-write-wins with parameter + version stamping (a steal warns the stealer; the prior owner's unsaved work is
  not silently merged back).
- Movie relink on a broken path. **Integrity-check timing:** a routine open verifies only the **metadata-only fast
  signature** (size + mtime + offline-availability flag — **zero byte reads**) and **never auto-hydrates** a
  dehydrated placeholder — it warns instead; the head/tail content hash and full `sha256` run only on relink or
  explicit verify. A mismatch is flagged and never silently trusted.
- Schema migration backs up and migrates in place and refuses files newer than the app.
- **Movie-less mode** is first-class (patches + coordinates); a subset-export `.tether` always opens movie-less
  (raw is not reconstructable there).

---

## 6. Processing pipeline (movie → states)

Each stage mirrors Deep-LASI (full specification in Appendix E):

```
load movie (lazy)
  → calibration (apply .tmap OR native bead/grid fit)
  → split + register
  → detect spots + colocalize (donor-anchored)
  → place apertures
  → per-molecule per-channel background subtraction + integrate per frame (Sum)
  → detect photobleaching steps
  → set analysis window (auto = both-dyes-active → first bleach; manual override)
  → estimate α (global, donor-only sample, median) then γ per trace (median; δ = 0)
  → corrected FRET (Appendix B order)
  → write molecules + traces + patches + provenance + settings
  → (curate / ML pre-sort)
  → idealize via tMAVEN
  → dwell/rate + population analysis
```

**Performance.** memmap I/O; vectorized/Numba detection + integration; parallel across molecules; HMM parallel
across traces.

**Batch.** The same pipeline runs headless; each movie is isolated (continue-on-error), checkpointed (resumable),
with a structured log and an end-of-run summary.

---

## 7. Functional requirements

Requirement IDs are referenced by the milestone acceptance criteria in §9.

### 7.1 FR-EXTRACT — Native movie→trace extraction

Tether **shall** extract coordinate-tagged donor/acceptor intensity traces from a dual-view single-laser TIRF
movie, faithfully reproducing Deep-LASI's logic (Appendix E), comprising: per-channel split geometry; a
moving-average max-projection detection image; à trous wavelet spot detection [Olivo-Marin2002]; sub-pixel
localization by centroid + 3 px max-pixel snap [Izeddin2012] (optional radial-symmetry upgrade [Parthasarathy2012]);
a 21×21 aperture with a PSF disk (radius 3, 29 px) and a concentric background annulus (inner 6, outer 8, with the
deliberate dead-zone gap); per-frame local background subtraction; and Sum integration. Registration **shall**
support both a native bead/grid fit (phase-correlation prealign → NN pairing → degree-2 polynomial map with a
numeric RMS-residual gate) and an imported `.tmap`. Colocalization **shall** be donor-anchored (acceptor intensity
read at the mapped position regardless of independent acceptor detection) so low-FRET acceptors are not lost.
Advanced options (disk/ring radii, detection mode, tolerances) **shall** be configurable, with defaults that
reproduce Deep-LASI.

**Over-gate registration (numeric fit succeeds but residual exceeds the gate).** This is a distinct branch from the
fit-*failure* ladder (degree-2 → retry degree-3 → similarity fallback). The RMS residual **shall** always be stored
in `/calibration` provenance; a fit **≤** the gate (default ≤ 0.5 px, configurable, §11.2) proceeds normally; a fit
**>** the gate marks the calibration **low-confidence** and tags every molecule it produces `low-confidence-
registration` (**never silently dropped**). The action is mode-aware: in the **GUI**, a blocking confirm-dialog
offers { accept-with-flag | import a `.tmap` | abort this movie } (default focus = import `.tmap`); in the
**headless batch**, the default is **accept-with-flag + a structured warning** in the per-movie log and end-of-run
summary (do not abort), with the batch policy (warn-and-flag vs. fail-movie) configurable in the settings profile.

### 7.2 FR-CORRECT — Corrections & FRET computation

Tether **shall** compute corrected FRET following the order **background → leakage α → direct-excitation δ (= 0)
→ γ** and the formula **E = I_A,corr / (I_A,corr + γ·I_D,corr)**, with an apparent-E toggle (α = δ = 0, γ = 1),
exactly as specified in Appendix B and cross-checked against MASH-FRET and Deep-LASI. The leakage factor α **shall**
be obtained primarily from a dedicated donor-only sample (global α = median over donor-only molecules of
I_DA/I_DD), supplemented by the per-trace post-acceptor-bleach tail where a clean acceptor-bleach step exists (both
estimators are computed whenever the data allow, and their **agreement** is the M3 leakage-α validation oracle —
§9 M3 — since Deep-LASI has no donor-only route for a direct comparison). The agreement test is **conjunctive**:
it passes iff the relative difference of the two population medians |α_donor-only − α_tail| / mean(α_donor-only,
α_tail) ≤ 20% **and** both medians lie in the physical band 0.05–0.2 — the band is a plausibility check only and is
never a standalone pass path (an OR would let two badly-disagreeing estimates both pass on plausibility alone,
defeating the oracle; §9 M3, §11.2). γ
**shall** be obtained trace-wise across the acceptor-bleach step (acceptor drop / donor rise) over a tolerance
window (half-width pinned to 3 frames each side, configurable; §11.2), aggregated by population median [McCann2010].
Manual override of every factor **shall** be available.
Corrections **shall** not be required to *view* traces: apparent-E analysis and histograms work without any
photobleaching; only the α/γ corrections require bleach steps.

**Total-correction-failure path.** The min-qualifying-traces gate (§11.2) is applied **before** the population
median, so an empty qualifying set can never emit a NaN factor or NaN corrected-E. When **no donor-only sample is
loaded and fewer than `min_qualifying_traces` molecules yield a valid factor** — the *expected* case for the lab's
typical pure-FRET acquisitions lacking a clean acceptor-bleach step — Tether **shall** retain/display **apparent
E** (α = δ = 0, γ = 1), stamp provenance `method = "apparent-E (corrections unavailable)"`, show a **non-blocking
banner**, and offer two recovery actions: load a donor-only sample, or enter manual per-condition α/γ (which stamps
`method = "manual"`). If the user declines, the project stays in apparent-E. A NaN factor or NaN corrected-E is
never written (§9 M3, §10).

### 7.3 FR-ROUNDTRIP — Provenance & round-trip browser (the MVP centerpiece)

Tether **shall** present a multi-movie round-trip browser:

- An embedded **napari movie panel** showing the lazy movie + donor/acceptor points + aperture overlays, with a
  movie switcher for multi-movie experiments.
- A **trace dock** (pyqtgraph) as the primary, keyboard-driven surface (≈1–2 s/trace, hundreds per sitting):
  donor/acceptor/total + FRET + idealization step overlay; cross-correlation; histogram. This is tMAVEN's per-trace
  viewer (Appendix C, D1) reimagined as Tether's curation surface. At the MVP the FRET axis reads "apparent E"
  (corrections land at M3).
- **Round-trip navigation:** select a trace → the camera jumps to its spot (resolving each molecule to its own
  movie), with synchronized scrubbing and a neighbor/overlap view (static patch + nearest-neighbour distance; movie
  scrub optional); click a spot → its trace.
- **Familiar conventions:** donor green / acceptor red / FRET blue; FRET y-axis 0–1; x in seconds (from FrameTime,
  with a frame-index toggle); idealized path drawn as a step overlay.
- **Keyboard map:** the trace dock inherits tMAVEN's per-trace bindings for overlapping actions (← / → prev/next,
  with `↑`/`↓` as aliases; `1`–`9` assign the first nine editable per-condition categories and **`0` clears the
  category back to the *uncategorized* null state** — distinct from any named category, so `Space` alone yields an
  *accepted-but-uncategorized* trace, §5.1/§7.6; an overflow picker handles >9 categories; `-`/`=` nudge the
  analysis-window **start** (`pre_list`) and `[`/`]` nudge the **end** (`post_list`) — distinct bounds, not one
  action; `R` reset, `P` photobleach, `G` grid) for muscle-memory continuity, and adds the Tether-only actions
  **`Space` = accept, `Backspace`/`Delete` = reject, `Enter` = jump to the movie spot (round-trip focus), `I` =
  one-click idealize**. tMAVEN's `C` (split) / `V` (collect) have no Tether analog and are **reserved as no-ops in
  v1** so no Tether-only binding shadows them. The integer↔category lookup pins **tMAVEN class 0 ↔ Tether
  "uncategorized"** and named categories ↔ tMAVEN classes ≥ 1, so a tMAVEN round-trip never silently turns an
  uncategorized trace into a named category (§7.4, Appendix D).
  - **Focus contract.** Because the four bare curation keys (`Space`/`Backspace`/`Delete`/`Enter`) collide with
    default Qt list/table and napari-canvas bindings (`Space` toggles a checkbox, `Enter` activates/edits a row,
    `Delete` removes), an **application-level event filter** delivers them to the trace-dock curation controller
    **regardless of which child widget (napari panel / molecule list / movie switcher) holds focus**, suppressing
    the conflicting native bindings on those non-text surfaces — **except** a focused text-entry widget (notably the
    editable category field, §7.6) is exempted so `Space`/`Backspace`/`Delete` keep text semantics there. Focus is
    also returned to the trace dock after a camera jump (mirroring tMAVEN). This removes the silent-no-op / stray-
    toggle hazard at the 1–2 s/trace cadence. A cheat-sheet ships with the app and all bindings are rebindable.

### 7.4 FR-IDEALIZE — Idealization (tMAVEN integration)

In-app one-click idealization **shall** be available from the MVP, with **verified tMAVEN parity**, via the embedded
sidecar: export selected molecules to SMD → run vbFRET (per-trace), consensus VB-HMM, or ebFRET headless through
`tmaven.maven.maven_class` → import states/dwells. Auto state-count selection **shall** use max ELBO with a manual
per-trace override. **In-app idealization is a hard requirement; a hand-off-only MVP is not acceptable** (see §10).

*Parity definition.* Because the pinned tMAVEN self-reseeds its RNG (`initialize_gmm` calls `np.random.seed()` then
random-resamples a KDE; `clip_traces` reseeds from wall-clock), bit-identical reproduction is impossible without
patching tMAVEN's GPL core. Parity is therefore defined as **statistical agreement within a stated tolerance** on
state count, state means, Viterbi-path agreement, and ELBO — mirroring the extraction-tolerance approach (§9). The
four tolerance **numbers** live in one place — the **§11.2 "Idealization parity tolerance" row** — seeded with
provisional defaults (state-count exact on ≥ 90% of traces; per-state mean |ΔE| ≤ 0.02; Viterbi per-frame agreement
≥ 95%; |ΔELBO| / |ELBO| ≤ 0.01) and **ratified at M0.5**, whose deliverable measures the cross-seed spread by
running standalone tMAVEN ≥ 20× on the committed fixtures and freezes the row. M2 and M6 inherit it by reference and
**may not be signed off until it is frozen** (§9 M0.5 / M2 / M6).

Integration **shall** be **bidirectional**: a one-click "Hand to tMAVEN" exports an SMD the standalone GUI opens
directly (Tether-authored coordinates ride along as superset metadata). On the **return leg** Tether re-imports the
tMAVEN session as a **new** `/idealization/{model}` entry (non-destructive). Because tMAVEN's writer has no
per-molecule slot and its exporter may subset/reorder by the GUI selection mask (Appendix D.1), the returned SMD's
coordinates are **not trusted or required**; instead Tether matches each returning trace to its molecule by **exact
intensity-trace matching** of the SMD `raw` series against its retained store (molecule-id / order as a hint only)
and reports unmatched molecules (§5.3). The returning SMD may also carry **edited analysis windows**
(`pre_list`/`post_list`) and integer classes; those windows are edited in tMAVEN by **manual trace-plot
adjustments, photobleach re-detection, or a leading-frame trim — not as a side effect of leakage/γ correction**. The
return leg **shall present a per-trace reconcile prompt** showing the diff (idealization, analysis-window, class)
and let the user accept or reject each change rather than silently overwriting; an accepted analysis-window change
re-stales that molecule's dependent corrections/idealizations (§5.1). tMAVEN's integer classes map to Tether's
free-text per-condition categories through the stored **integer↔category lookup table** (class 0 ↔ uncategorized;
otherwise lossy — §7.3).

### 7.5 FR-ML — Curation & per-condition ML

Tether **shall** provide a classical, GPU-free quality model (engineered features: SNR, anticorrelation/XC
magnitude, bleach-step count, FRET mean/variance, dwell statistics, total intensity, edge/overlap, an explicit
single-anticorrelated-acceptor-then-donor-bleach detector, and a second-molecule-in-aperture flag) feeding a
gradient-boosting ranker [Chen2016] for quality ranking and "find traces like these." The model **shall only
re-order / pre-sort — never auto-drop**; threshold-reject is an opt-in, logged, manual action. Feature values
**shall** be shown next to each trace.

**Ranking objective & success metric.** The ranker optimizes **precision@k** — the fraction of good traces among
the first k reviewed (k ≈ the 20–50 traces in a curation sitting at the ~1–2 s/trace budget) — minimizing wasted
clicks. The M5 gate (§9) is a precision@k **uplift over the file-/extraction-order baseline**, evaluated
**prequentially** (each new video's traces are scored by the reloaded model *before* their labels fold into the
next warm-start retrain) and required to hold on the **median across the condition's videos** (not every video);
the default ship-bar is a ≥ 10-percentage-point precision@k uplift (tunable, §11.2).

**Curation order.** Within a single video's pass, trace order is **fixed once the model pre-sorts on load**; retrain
+ re-sort happen only at the **video boundary**, preserving a predictable sweep. The active-learning loop surfaces
its "most informative next" suggestion as a **non-reordering badge** (a "recommended next" cue), not a live
re-queue; live unseen-tail re-ranking is a deferred opt-in.

**Reject semantics.** A reject (single or opt-in threshold-reject) is a **reversible tag**, never a deletion: the
molecule is excluded from default histograms/idealization through a **toggleable filter**, kept in a visible
"rejected" bin, one-click un-rejectable, with **undo + confirmation** on bulk threshold-reject, and the reject
**carries across files** as a sticky exclusion (and as an ML training label). This honors "never silently drop."

The model **shall** be **per-condition, persistent, and incrementally retrained**: a standalone, portable artifact
(not trapped in one experiment file) following the loop in UC3 (load → curate one video → warm-start-retrain →
save → reload on the next video). Each label trains the model **weighted by its `source`** (§5.1 `/labels`). The
per-row `weight` is **mutable — recomputed and rewritten on each retrain**: human labels are full weight (1.0);
Deep-LASI-provisional and cross-condition-seed labels are down-weighted **cold-start priors** whose effective weight
follows the decay law **w = w₀ / (1 + n_human)** — `w₀` the seed weight (default ≈ 0.3) and `n_human` the count of
human labels in the condition at retrain time (tunable, §11.2) — so the weight **decays toward zero as human labels
accrue** and the model learns the lab's preferences rather than Deep-LASI's classifier. A condition's model **may**
be seeded from another condition; cross-condition use raises an **advisory (overridable) flag** driven by a simple
feature-distribution / FRET-range / SNR drift signal between the source and target conditions (gated at M5, §9). The
model trains on `/labels` accumulated from first curation (M2). **Multi-curator reconciliation:** a designated
**condition-owner curator** retrains and saves the canonical model (its artifact has its own single-writer lock,
§5.1 `/models`); other members curate into their own split/subset files whose labeled rows — tagged with labeler
identity and the stable `molecule_key` (§5.1) — **merge back as an append-only owner-pull at the video boundary**,
joined on `molecule_key`. The owner's retrain then recomputes every row's `weight` from the merged set, and
human-vs-human disagreement on the same molecule surfaces through a §7.4-style per-trace reconcile prompt. An **active-learning** loop **shall** propose the most
informative next traces (surfaced as the non-reordering badge above). A later deep phase (1-D CNN/LSTM on raw
traces, DeepFRET/Deep-LASI-style [Thomsen2020]) **shall** reuse the same label store on the GPU (RTX 4060 floor,
§8).

### 7.6 FR-ANNOTATE — Annotation & conditions

Tether **shall** support structured condition fields auto-filled from the filename (validation mandatory), free
tags, and a **fully user-editable per-trace category list (no presets)** scoped per condition (the list travels
with the condition, which spans many files). Assigning a category is **independent of accept/reject** (§5.1, §7.5):
it does not imply acceptance, and accept/reject is a separate logged keystroke. A condition **shall** be
queryable/filterable across its many movies.

### 7.7 FR-ANALYZE — Analysis & visualization

Tether **shall** provide:

- FRET histograms with error bars / bootstrap CI [König2013] + per-condition overlays, a per-molecule equal-weight
  toggle, computed over the analysis window (rejected traces excluded by default via the toggleable filter, §7.5).
- Donor–acceptor **cross-correlation**: vectorized FFT with principled Pearson normalization, a population curve,
  and a lag-1 magnitude feeding the anticorrelation-event finder.
- The **raw FRET cloud**: a consolidated pre-idealization QC view (KDE + highest-density-region percentile contours
  [Hyndman1996] + alpha-shape + k-vs-RMSE elbow).
- **Real TDP**: a 2-D before/after idealized-state density (fresh idealizations only) [McKinney2006][Hadzic2018];
  dwell distributions with exponential/rate fits and CIs.
- Native reproduction of the **seven tMAVEN plot types** (Appendix C); any other tMAVEN plot stays reachable via
  the hand-off.

### 7.8 FR-LEGACY — Legacy import & Deep-LASI re-analysis

Tether **shall** import Deep-LASI projects and tMAVEN SMD. A minimal read path lands at M0.5/M1 (for validation and
bootstrap); a polished importer lands at M7.

A **raw `.txt`-sourced tMAVEN SMD imported standalone** (no `.tdat`, no `.mat`, possibly no movie — e.g. the M6
281-molecule parity fixture) carries neither coordinates nor patches, so it **shall** be accepted as an explicit
**analysis-only project**: idealization, histograms, TDP, and kinetics are fully usable (exactly what M6 parity
needs), but the trace↔movie round-trip browser (§7.3) and patch-dependent movie-less curation are **disabled**, a
one-time banner announces *"coordinates and patches absent; movie round-trip and spot/overlap views unavailable,"*
and every molecule is tagged `round-trip-unavailable` in provenance. This degraded branch is distinct from the
Deep-LASI-bundle re-analysis path below, which re-imports with coordinates intact.

The **Deep-LASI re-analysis** workflow **shall** let a user re-analyze existing Deep-LASI work inside Tether by
handing over an acquisition's files and pairing them to the movie, recovering coordinates and the curated subset
**without re-extraction**:

- **Intake.** One acquisition = { raw movie `.tif`, Deep-LASI `.tdat` } plus any of { `.mat` export, `.txt`,
  tMAVEN SMD `.hdf5` }. A "New project from Deep-LASI data" wizard auto-detects and pairs the set by filename stem
  and the movie references embedded in the `.tdat` (`LastPath`/source) and `.mat` (`movie_path`/`movie_name`); the
  user confirms the proposed pairing.
- **Coordinate sources.** Two files carry per-molecule pixel coordinates: the `.tdat` (`ParticlesColocalized`,
  via the `TIRFdata` OOP decode) and the `.mat` export (the `fret_pairs` field, N×4 donor/acceptor pixel pairs).
  The `.txt` and the tMAVEN SMD carry intensities/metadata only. **Full round-trip re-analysis therefore requires
  the `.tdat` *or* the `.mat`** (native re-extraction, seeded by either's coordinates, remains optional).
- **Pairing key.** All Deep-LASI exports preserve molecule order, so the primary key is the molecule index across
  `.tdat ↔ .mat ↔ .txt ↔ SMD`. Because the SMD/`.txt` carry no coordinates, a curated SMD trace is mapped back to
  its movie pixel by exact intensity-trace matching (the SMD `raw` series equals the `.txt`/`.mat` corrected
  columns) as a robust cross-check on the index. The selected set comes from the SMD (tMAVEN curation) and/or the
  `.mat` `select` flags (Deep-LASI curation); the raw extracted set is the full `.tdat`/`.mat` molecule list.
- **Reconstructed project.** Tether writes a provenance store with coordinates + patches, raw + corrected +
  background traces, correction factors remapped per Appendix B (`b` → α, `g` → γ, δ = 0), bleach frames + analysis
  window, Deep-LASI categories/NN/HMM states (written to `/labels` with `source = deeplasi-provisional` and a
  decaying weight per §7.5, plus seeds for the editable category list), and the
  curated selection — every molecule linked to the movie (`movie_id` + sha256 + sub-pixel xy). The user can
  immediately browse / curate / idealize with the round-trip live.

The importer **shall** apply the correction-factor remap of Appendix B: Deep-LASI `β` (donor leakage) → Tether α
(applied additively); Deep-LASI `α` (direct excitation) → Tether δ (inert/0); Deep-LASI `γ` → Tether γ.
Misattributing β would silently drop a real leakage correction and shift every imported E.

### 7.9 FR-EXPORT — Interoperability & exports

Tether **shall** export: CSV and Deep-LASI-style `.txt` per-molecule/per-condition tables; a **subset `.tether`**
(movie-less; embeds patches/coordinates/corrected traces/idealization/provenance, raw optional); SMD-HDF5 for
tMAVEN hand-off [Greenfeld2015]; and every plot as vector PDF/SVG + PNG. The seven tMAVEN plot types are reproduced
natively (Appendix C); all exports are stamped with provenance and parameters.

### 7.10 FR-CONCURRENCY — File lifecycle on shared storage

Tether **shall** enforce single-writer access to an experiment file via `<file>.lock` (host/user/PID/timestamp), a
read-only banner for non-owners, a steal-lock override (typed confirmation), and stale-lock recovery via a
**wall-clock staleness timeout** (default ≈ 30 min) rather than cross-machine PID-liveness, so multiple lab members
on OneDrive/network shares do not corrupt a project; the intended posture is **one owner at a time** and Tether
**detects and surfaces OneDrive conflict-copies** (§5.4). A **read-only (non-owner) member shall still be able to
browse and curate into their own split/subset `.tether`** (their own provenance-tagged `/labels`, each row keyed by
the stable `molecule_key`, §5.1) that **merges back as an append-only owner-pull joined on `molecule_key`** (the
owner's retrain recomputes weights from the merged set; conflicting human labels on the same molecule surface via a
§7.4-style reconcile prompt — §7.5) — curation is the central daily workflow and must not be blocked while a file
is locked. Curation **may** therefore be split across multiple files. The portable per-condition model artifact has its own single-writer lock and a
designated owner-curator (§5.1 `/models`, §7.5). The cached Zarr pyramid is local-only and never synced.

### 7.11 FR-BATCH — Headless core & batch runner

Every module **shall** be usable without the GUI, proven per milestone (a CLI movie→`.tether` extract at M1; a
headless reproduction of the MVP histogram at M2). The overnight **batch runner shall** isolate each movie
(continue-on-error), checkpoint **per stage** (extract / correct / idealize — so a resume re-runs only the failed
stage), and emit a structured log + end-of-run summary that enumerates every movie's status and names any failures.

**Sidecar supervision.** Because idealization runs through the separate long-lived tMAVEN sidecar over IPC (§4.3),
the batch **shall** supervise it: a per-IPC-call **wall-clock timeout + liveness check**; on a hang or crash,
**auto-restart the sidecar up to N times** (default 3); on persistent failure, mark **only that movie's
idealization stage** failed under continue-on-error and proceed with the queue (its extraction + correction remain
checkpointed). If the sidecar environment is **absent or corrupt at startup**, the batch **shall** proceed in an
**idealization-deferred** mode — all movies extracted + corrected, idealization queued for a later run — rather
than aborting. The batch policy (warn-and-flag vs. fail-movie) is configurable in the settings profile (§10).

---

## 8. Non-functional requirements

- **NFR-PERF — Performance.** memmap/lazy I/O, Numba kernels [Lam2015], parallelism across molecules and traces; a
  Rust (PyO3) escape hatch only if a single kernel dominates profiling. <!-- NFR-PERF gate is M3 per §12.10; see Targets note below --> **Reference hardware floor:** a laptop with
  16 GB RAM, a spinning **HDD** (not SSD), ~100 GB free, and an NVIDIA RTX 4060 Laptop GPU (8 GB). Two consequences
  are load-bearing: (1) the HDD makes random memmap access and the local Zarr scratch cache performance-critical, so
  extraction and trace I/O **favor sequential/block access**; (2) a ~100-video condition is ≈ 90 GB of raw movies,
  which nearly fills the disk, so movies are expected to live on OneDrive (Files-On-Demand) rather than all hydrated
  locally, and the `.tether` + scratch footprint must stay modest. **Targets (verified from M3 — the trace dock
  whose render+navigate latency is budgeted lands at M2, and the overnight extraction+correction+idealization
  envelope is only end-to-end at M3, so M1 has nothing to measure; a light §9 gate, not
  an SLA matrix):** per-trace render+navigate latency budget ≈ 100 ms (to sustain the 1–2 s/trace cadence); a
  ~100-movie condition completes extraction + correction + idealization overnight; a bounded `.tether` size envelope
  per condition.
- **NFR-XPLAT — Cross-platform.** One Python codebase on Windows/Mac/Linux; the base app is CPU-only; GPU is an
  optional add-on for the later deep models.
- **NFR-REPRO — Reproducibility.** Every analysis writes parameters + app version + provenance into the project
  file; the app version is derived from git (release tag / `git describe`) for end-to-end traceability (§12).
- **NFR-VALID — Validation oracles.** (a) Extraction vs Deep-LASI on the UCKOPSB `.tdat`/`.tmap` + movie pair to
  the §9 tolerance; (b) idealization vs a real tMAVEN SMD session — per-trace vbFRET on the small fixtures **and**
  consensus VB-HMM / ebFRET on the committed **≥ 50-molecule** SMD — both to the **§11.2 idealization-parity
  tolerance** (state count / means / Viterbi / ELBO), ratified at M0.5 (§9 M6, NFR-FIXTURES); (c) kinetics vs
  kinSoftChallenge [Götz2022] to the **§11.2 within-spread band** (advisory until the gated-CI slice is acquired,
  §9 M8); (d) the per-condition ranker via **held-out cross-validation** (prequential /
  leave-one-video-out, the precision@k protocol of §7.5); (e) the α/γ estimators via **edge-case unit tests**
  (missing/sparse bleach steps, median-fallback paths, and the **total-failure → apparent-E** path that must never
  emit a NaN factor) derived from Appendix E Stages 16–18 and §10, plus the **conjunctive two-Tether-estimator α
  agreement** (§7.2, §11.2); (f) round-trip integrity and schema-migration tests under existing conventions;
  (g) the photobleaching detector's **per-channel first-bleach frames vs the `.mat` `pacc`/`pdon` ground truth** to the **§11.2 bleach-frame tolerance**
  (§9 M3 — validating the Bayesian single-step detector).
  **No synthetic-data simulator is introduced** — validation stays on real labeled fixtures (N2 holds).
- **NFR-FIXTURES — Test fixtures.** Because the lab holds redistribution rights, a small cropped UCKOPSB movie
  slice + a few molecules are committed for unit/CI; a **redistributable ≥ 50-molecule curated SMD**
  (`example-data/tmaven-model/model-source-smd-281mol.hdf5` — the 281-molecule population that
  `tmaven-model/model.hdf5` was idealized from) is committed as the population-model (consensus VB-HMM / ebFRET)
  parity fixture (§9 M6, Appendix D); the full ≈0.9 GB movie + kinSoftChallenge live in a large local/LFS/gated-CI
  tier.
- **NFR-PKG — Packaging/CI.** 3-OS GitHub Actions build + test; guided sidecar setup for v1 → constructor signed
  installers at M9; mkdocs documentation. CI design (small committed fixtures in default CI; the large LFS/gated tier in a manual/gated workflow — NFR-FIXTURES) and the release pipeline are governed by §12.
- **NFR-HEADLESS — Headless-first.** The GUI is a thin layer over a fully scriptable core (FR-BATCH).
- **NFR-GOVERNANCE — Version control & supply chain.** Solo-developer GitHub Flow with CI-as-merge-gate: `main` is
  always releasable and branch-protected (green required CI + a self-review checklist on every PR; squash-merge,
  linear history, delete-branch-on-merge), scaling later to required human reviews/CODEOWNERS if contributors join.
  Conventional Commits; SSH-signed, verified commits authored as `bioedca@u.northwestern.edu`; 2FA enforced.
  Supply-chain hardening = CodeQL + secret scanning + push protection + Dependabot (pip + github-actions;
  Dependabot's conda ecosystem covers `environment.yml` version updates but **not** `conda-lock.yml` lock files);
  the `conda-lock` base, sidecar, and deep stacks (§4.1/§4.3) follow the pin-and-hold deliberate-bump policy plus a scheduled
  `pip-audit`/`safety` vulnerability job. A CI **schema-guard** gate enforces the additive-only HDF5 schema freeze
  (§5, §9 M0). Full protocol in §12.

---

## 9. Milestones & acceptance criteria

Each milestone ships a runnable, tested app + a scripted pass/fail checklist + sample data. Development is
incremental and milestone-by-milestone with reviewer sign-off. The roadmap is extraction-first, with de-risking
validation front-loaded at M0.5.

| Milestone | Deliverable | Acceptance criteria |
|---|---|---|
| **M0 — Foundation** | Public repo, environment (pinned Python/numpy for Numba), CI; full HDF5 schema forward-declared (all groups incl. empty `/conditions`, `/features`, `/labels`, `/models`, `/idealization`, `/settings`, version-stamped; **`/molecules` carries the stable-UUID `molecule_id` + the `molecule_key` (movie-`sha256` + quantized `donor_xy`) + condition-key + provisional fields; `/labels` carries the `molecule_key` + labeler / timestamp / source / weight provenance fields; `/movies` carries the metadata-only fast signature (size + mtime + offline flag) — all frozen now**); lazy big-endian TIFF reader; embedded napari viewer; filename parser; headless-core scaffolding; governance setup (§12) — repo created with branch protection + signed commits, the GitHub Actions workflows (pytest + ruff + 3-OS), CodeQL + secret scanning + push protection + Dependabot enabled, issue/PR templates + labels + Milestones + Project board, pre-commit, and a CI schema-guard gate. | CI green on 3 OSes; a big-endian 512×512×1700 TIFF opens and displays in napari; `.tether` skeleton created and re-opened with version stamp; filename parser round-trips a known condition string; the frozen schema includes the `molecule_key` (on `/molecules` and `/labels`), the label-provenance, condition-key, and metadata-only movie-signature fields; branch protection rejects a PR with red CI, a signed/verified commit lands on `main`, and the CI schema-guard fails a deliberately structure-breaking schema change (§12). |
| **M0.5 — De-risking validation** | (a) A headless vbFRET sidecar round-trip producing an SMD the standalone tMAVEN GUI opens with coordinate metadata intact, run via the guided sidecar setup on Windows + one of Mac/Linux; **and ratification of the §11.2 idealization-parity tolerance** — measure the cross-seed spread over ≥ 20 standalone-tMAVEN runs on the committed fixtures and freeze the four tolerance numbers (state count / means / Viterbi-path / ELBO). (b) Spot-detection + aperture integration on a `.tmap` + movie pair, the `TIRFdata` `.tdat` decode (incl. the β/α/γ remap, Appendix B), comparison to Deep-LASI, and validation of native bead/grid residuals against the `.tmap`. | (a) A headless vbFRET idealization reproduces tMAVEN's states on a known SMD **within the §11.2 idealization-parity tolerance (state count / means / Viterbi-path / ELBO)**, and that §11.2 row is **frozen with the measured numbers** (M2/M6 may not sign off until this is done); the exported SMD opens in standalone tMAVEN; setup script runs clean on two OSes. (b) ≥ 95% of Deep-LASI molecules matched within 1 px; the `TIRFdata` decode recovers coordinates + α/β/γ; native registration RMS residual ≤ 0.5 px vs the `.tmap`. *If headless `maven_class` cannot be driven reproducibly across OSes, the pre-committed escalation is a prebuilt bundled sidecar over a stable IPC — not an in-process embed, not a hand-off-only MVP (§4.3, §10).* |
| **M1 — Extraction core** | Native calibration creation (bead/grid → transform) **and** apply imported `.tmap`; registration; spot detection + colocalization; aperture integration (configurable advanced options) + background → coordinate-tagged traces + cached patches + provisional `condition_id` per movie from filename + `/settings`. Minimal Deep-LASI reader for validation. Headless CLI: extract one movie → `.tether`. | Extraction-vs-Deep-LASI on the UCKOPSB pair meets tolerance: matched-molecule recall ≥ 95% within 1 px; per-frame integrated-intensity Pearson r ≥ 0.99 on matched molecules; registration RMS ≤ 0.5 px. CLI produces a valid `.tether` headlessly. |
| **M2 — MVP** | Multi-movie round-trip browser (per-movie KDTree, movie switcher, static overlap) + curation logging (→ `/labels`) + tMAVEN sidecar (vbFRET, statistical parity) one-click + hand-off to standalone tMAVEN + non-destructive re-import + FRET histogram + cross-correlation + single-writer `.lock` + read-only banner + steal-lock. Runs on apparent E (UI-labeled). | Select-trace→camera-jump and click-spot→trace both work across ≥ 2 movies; every accept/reject writes a `/labels` row; one-click vbFRET matches standalone tMAVEN within the **§11.2 idealization-parity tolerance**; the single-writer lock (wall-clock stale-timeout) prevents a second writer, steal-lock recovers, and a cross-machine lock / stale / steal case is exercised. Headless: reproduce the MVP histogram from the API. |
| **M3 — Corrections** | Photobleaching detection (native Bayesian single-step model, run per channel); load a donor-only sample → global leakage α; then γ auto (Appendix B order; δ = 0); corrected FRET with the **total-failure → apparent-E** fallback; stale-idealization flag + re-idealize; histograms with CI. Batch runner (error-isolated, **per-stage** checkpointed, sidecar-supervised, logged) usable by end of M3. | Tether's donor-only α agrees with its own post-acceptor-bleach-tail α (matched gates, same FRET dataset) under the **conjunctive §11.2 leakage-α validation band** (relative-median difference ≤ 20% **and** both medians ∈ 0.05–0.2; §7.2); γ agreement with the Deep-LASI median is within the **§11.2 γ-agreement tolerance** (default ±10%) **on a shared frame set derived from Deep-LASI's own per-frame classification** (estimator-isolated; 3-frame half-width, §11.2) plus a looser end-to-end CI-overlap check; the **per-channel first-bleach frames match the `.mat` `pacc`/`pdon` ground truth within the §11.2 bleach-frame tolerance (default ±2 frames)**; a pure-FRET dataset with no donor-only sample and < `min_qualifying_traces` valid factors **falls to apparent-E with a banner and never writes a NaN factor**; changing a correction flags only the affected molecules' dependent idealizations STALE and excludes them from TDP (a γ-median shift re-stales fallback molecules only, an α-median shift the whole cohort, §5.1); and the batch runner **(i)** isolates a deliberately corrupt movie (continue-on-error) while the rest complete, **(ii)** resumes after a killed movie via per-stage checkpoint, and **(iii)** emits an end-of-run summary naming the failed movie — exercising sidecar timeout/restart. |
| **M4 — Annotation** | Structured conditions (spanning many movies/days/files) + filename auto-parse + user-editable per-condition category list + condition query/filter. Validates the provisional `condition_id`. | A condition aggregates molecules across ≥ 2 files; the category list edits persist per condition; provisional `condition_id` values are confirmed or corrected via the validation UI; a mis-parsed `condition_id` re-keys all affected molecules transactionally with an audit entry, and merges are keep-separate-by-default + human-confirmed (§5.1). |
| **M5 — Curation + ML v1** | Per-condition, persistent, incrementally-retrained model (load → curate a video → warm-start retrain → save → reload next video); sort/rank only; active learning; seed from Deep-LASI categories / other conditions. Reads `/labels` since M2. | Reloading the saved model on a held-out (prequential) new video improves **precision@k** (k ≈ 20–50) over the file-order baseline by ≥ the agreed ship-bar (default ≥ 10 pts), holding on the median across the condition's videos; never-auto-drop is verified as a permutation invariant; active learning surfaces informative traces; the model file is portable across experiment files; a **weight-decay test** confirms a provisional label's effective weight drops below a stated fraction (e.g. < 0.2·w₀) after K human labels (decay law §7.5/§11.2); a **drift-flag test** confirms a deliberately mismatched source/target condition raises the advisory while a matched pair does not; and a **two-curator split-and-merge test** confirms split-file labels merge back on `molecule_key` with weights recomputed centrally and human-vs-human conflicts surfaced (§7.10). |
| **M6 — Analysis suite** | Consensus + ebFRET; the seven tMAVEN plot types (Appendix C); real TDP; dwell/rate fits; raw FRET cloud; anticorrelation finder; CSV/`.txt`/subset-`.tether`/SMD exports. | Each of the seven plot types renders from real data and visually matches its tMAVEN counterpart; TDP uses only fresh idealizations; all exports carry provenance stamps; consensus VB-HMM and ebFRET reproduce standalone tMAVEN on the ≥ 50-molecule SMD within the **§11.2 idealization-parity tolerance** (state levels + transition matrix + ELBO). |
| **M7 — Legacy importers** | The polished Deep-LASI re-analysis workflow (§7.8): multi-file intake + movie pairing → a full round-trip-ready project (coords / raw + corrected traces / factors / bleach / categories / selected subset) without re-extraction; robust `TIRFdata` OOP decode, error handling + wizard UI; tMAVEN SMD. | A full Deep-LASI acquisition reconstructs into a round-trip-ready project from `.tdat` or `.mat` coordinates; curated subset and categories survive; intensity-match cross-check passes on the SMD subset. |
| **M8 — ML v2** | Deep models (GPU, optional add-on) [Thomsen2020], fine-tuning; kinSoftChallenge validation [Götz2022]. | A deep classifier trains on the shared label store and is optional (CPU base app unaffected); kinetics on the **named kinSoftChallenge dataset (§11.2)** fall within that dataset's reported inter-tool spread — an **advisory** check until the gated-CI slice is acquired (M8 is the terminal optional GPU add-on). |
| **M9 — Packaging & docs** | Fully-bundled constructor signed installers; mkdocs; validation suite; the release pipeline (§12.7) — annotated + signed git tag → 3-OS signed installers + frozen per-release `conda-lock` + SBOM + auto-generated changelog (Conventional Commits) + docs deploy; SemVer 1.0.0 cut here. | Signed installers install clean on Windows + Mac + Linux with the **trimmed/pinned** sidecar bundled (no install-time git/network; `biasd` omitted); docs build in CI; the validation suite runs end-to-end; a signed `v1.0.0` tag drives the §12.7 release pipeline (3-OS signed installers + frozen `conda-lock` + SBOM + changelog + docs deploy) reproducibly. |

---

## 10. Risks & mitigations

- **Two riskiest pieces are early (extraction-first + tMAVEN-from-MVP).** Mitigation: front-loaded M0.5 validation,
  sidecar validation first.
- **tMAVEN sidecar cannot be driven headlessly / bundled.** Mitigation: the **pre-committed escalation** is a
  prebuilt **bundled sidecar invoked over a stable IPC** (not an in-process embed, which would reintroduce the
  `numpy<2` conflict). The sidecar ships a **trimmed/pinned** dependency subset (omit `biasd @ git+main`, bound
  `numba`) so it fits an offline signed installer (§4.1/§4.3, §9 M9). In-app idealization stays in the MVP;
  hand-off-only is not an acceptable fallback (FR-IDEALIZE). The standalone hand-off remains a *feature*, not the
  mechanism.
- **tMAVEN is not bit-reproducible — it self-reseeds its RNG.** Mitigation: parity is defined as **statistical
  agreement within a stated tolerance** (state count / means / Viterbi-path / ELBO), not bit-exactness — the same
  stance as the extraction tolerance (§7.4, §9).
- **Deep-LASI correction-factor naming inversion.** Mitigation: the explicit β → α / α → δ remap (Appendix B),
  validated at M0.5; otherwise every imported E silently shifts.
- **Extraction never matches Deep-LASI bit-for-bit.** Mitigation: a defined numerical tolerance (§9), not
  bit-exactness.
- **Dual-view registration.** Mitigation: a native bead/grid fit *and* an imported `.tmap`; validate residuals
  (≤ 0.5 px target, configurable); flag drift. An **over-gate fit** (numeric success but residual > gate) is a
  distinct branch from the fit-failure ladder: always store the residual, mark the calibration low-confidence + tag
  molecules `low-confidence-registration` (never drop), GUI confirm-dialog vs. headless accept-with-flag-and-warn
  (§7.1).
- **Correction factors on sparse bleaching.** Mitigation: trace-wise α/γ with a global-median fallback +
  confidence; a donor-only-sample fallback for α; manual override; do not require photobleaching to *view* traces.
  **Total failure** (no donor-only sample AND fewer than `min_qualifying_traces` valid factors — the *expected*
  pure-FRET case) applies the gate *before* the median, so no NaN is ever emitted: the project falls to
  **apparent-E with a banner + recovery actions** (load donor-only / manual entry), never a NaN factor (§7.2).
- **OneDrive + a single HDF5.** Mitigation: a **one-owner-at-a-time** posture with single-writer `.lock` + a
  **wall-clock stale-timeout** (≈ 30 min) + steal-lock; **detect-and-surface OneDrive conflict-copies**; concurrent
  curation via per-user split files (§7.10), never concurrent writes to one file; a routine open never auto-hydrates
  a dehydrated movie placeholder (a **metadata-only signature** — size + mtime + offline flag, **zero byte reads** —
  on open; head/tail hash + full `sha256` only on relink); the cached Zarr is local-only.
- **Overnight batch robustness.** Mitigation: per-movie isolation (continue-on-error), **per-stage**
  checkpoint/resume, log + summary, stale-lock recovery. The shared tMAVEN **sidecar is supervised** (per-call
  timeout + liveness, auto-restart ≤ N, fail-only-that-movie's-idealization on persistent failure, an
  **idealization-deferred** mode if the sidecar is absent/corrupt at startup) so a sidecar hang or crash cannot
  stall the whole run (§7.11).
- **Scope creep.** Mitigation: MVP-first; "all tMAVEN plots" bounded to seven (Appendix C) + hand-off; simulator
  deferred.

---

## 11. Conventions & glossary

### 11.1 Units & indexing conventions

- **Coordinates** are stored as sub-pixel `[x, y]` in source-movie pixels. Deep-LASI internally uses `[row, col]`
  and stores map particles `fliplr`'d to `[x, y]`; importers convert explicitly. Image geometry (e.g. 512×512 vs a
  512×256 split view) and channel identity are read from the source/`.tdat` (`ChannelsWithDataColor`), never
  hardcoded.
- **Indexing.** MATLAB sources are 1-based inclusive; Tether is 0-based half-open. Conversions are explicit at
  every importer/extractor boundary.
- **Time.** `FrameTime` (≈103 ms for the reference data) is always read from the file; trace x-axes default to
  seconds with a frame-index toggle.
- **FRET efficiency** E ∈ [0, 1]; intensities are in camera counts (ADU).
- **Aggregation** of global correction factors defaults to the population **median**.

### 11.2 Default parameter values

| Parameter | Default | Source / rationale |
|---|---|---|
| Detection block size (moving-average window) | 50 frames | `deeplasi/functions/classes/TRACERdata.m:42` (`MovingAverageWindowSize = 50` defined here; the projection mechanism is `TRACEdata.m` `CalcCumulated`) |
| à trous scales / threshold | J = 6, σ = 2·MAD hard-threshold, AND of scales 1 & 4 | `deeplasi/functions/external/Wave_Partfind.m`; [Olivo-Marin2002] |
| Sub-pixel localization | centroid + ≤ 3 px max-pixel snap (Gaussian σ = 1), mode 1 | `deeplasi/functions/mapping/findPart.m:88-101`; [Izeddin2012] |
| Particle detection mode | `wavelet` (à trous mode 1; class default) — selectable `{wavelet, intensity, bandpass}` (mode 3 = `find_part_bpass_sort.m`) | `deeplasi/functions/mapping/findPart.m:1,18-62` (`method` dispatch); Tether multi-mode detector (ADR-0021) |
| Detection threshold (intensity mode) | t = 0.5 (fraction of detection-image max); band-pass fine-threshold 3 % of bpass max; bpass `lnoise` = 1 / `lobject` = 7 | `deeplasi/functions/mapping/findPart.m:21-28,107-115`, `external/bpass.m`; [Crocker1996] |
| Detection threshold (bandpass mode) | t = 0.98 — **dual-use**: floor pixels below t·max **and** keep only the top 1−t fraction of band-pass values (percentile sort); bpass `lnoise` = 1 / `lobject` = 9; `imregionalmax` → centroid. (t==0 → 0.01.) In production Deep-LASI's `findPart` supplies the GUI `DetectionThreshold` instead | `deeplasi/functions/mapping/find_part_bpass_sort.m`, `external/bpass.m`; [Crocker1996] |
| Aperture (PSF disk / BG ring) | 21×21 grid; disk r = 3 (29 px); ring inner 6 / outer 8 (84 px); dead-zone 3 < d ≤ 6 | `deeplasi/functions/filtering/circ.m:5-32`, `classes/TRACERdata.m:92-100` |
| Per-frame background | 10-frame uniform temporal moving average, ring mean | `deeplasi/functions/traces/extractTracesC.m:13-22` |
| Integration | Sum (top-hat): I = TOT − bg·N_psf | `deeplasi/functions/traces/extractTracesC.m:20-33` |
| Molecule-key quantization | 0.1 px (`donor_xy` quantized before the cross-file content hash `molecule_key`) | Tether (§5.1/§7.10; below the 8 px detection min-separation so no collision; ADR-0016) |
| Registration prealign | phase-correlation, 4-DOF similarity (translation, S5a; rotation+scale via Fourier-Mellin log-polar, S5b, ADR-0012/ADR-0013); sub-pixel `upsample_factor` = 10; band-pass `low_sigma` = 3 / `high_sigma` = 20 px (S5b) | `deeplasi/functions/mapping/createMapPhaseCorr.m:6-16`; [Crocker1996] (bandpass alt.); `upsample_factor` / `low_sigma` / `high_sigma` = `skimage.registration.phase_cross_correlation` + `skimage.filters.difference_of_gaussians` (ADR-0013) |
| NN pairing tolerance | 2 px (fit on original, un-prealigned coords); 4 px legacy | `deeplasi/functions/mapping/findPairs.m:15-24`, `createMap.m:53` |
| Polynomial map degree | 2 (retry 3; similarity fallback if < ~6 points); 4 legacy | `deeplasi/functions/mapping/createMapPhaseCorr.m:20-47`, `createMap.m:57-58` |
| Registration RMS-residual gate | ≤ 0.5 px (Tether addition; Deep-LASI uses visual QA only) | Tether improvement over `createMapPhaseCorr.m` |
| Over-gate batch policy (headless) | `warn` = accept-with-flag + structured warning (default; never abort, never drop); `fail` = fail-the-movie | Tether (§7.1; ADR-0014) |
| Batch sidecar supervision (auto-restart / liveness) | auto-restart up to **N = 3** per movie's idealization on a *transient* sidecar failure (crash/timeout; a sidecar-reported fit error is **not** retried); per-idealization-call timeout = **1800 s** (inherited from `run_vbfret`); startup liveness probe (probe timeout **120 s**) → sidecar absent/corrupt ⇒ idealization **deferred** for the run (extract + correct still run and checkpoint; a later run resumes only the deferred stage) | Tether (§7.11 FR-BATCH; ADR-0031) |
| Colocalization distance | 3 px, donor-anchored (ADR-0015) | `deeplasi/functions/mapping/findColoc.m`, `traces/batchExtraction.m:182` |
| Bleach detection | native reimplementation of **tMAVEN's Bayesian single-step model** (signal→N(0)), run per channel; priors a = b = β = 1, μ = 1000; per-channel first-bleach validated vs `.mat` `pacc`/`pdon` (§9 M3) | [Verma2024]; `tmaven/tmaven/controllers/photobleaching/photobleaching.py`. (Kalafut2008 is a parameter-free *multi-step* method — classical alternative only; the `penalty ≈ 5` is Deep-LASI's `stepFinder` L1 mode, `TRACEdata.m:110`, not a Kalafut parameter) |
| Correction tolerance window (γ half-width) | 3 frames each side of the bleach step (configurable) | `MASH-FRET/docs/.../panel-factor-corrections.md`; [McCann2010] (§7.2, §9 M3) |
| γ-agreement tolerance (M3) | γ within ±10% of the Deep-LASI median on the shared-frame, estimator-isolated comparison | Tether (§7.2, §9 M3) |
| Bleach-frame tolerance (M3) | per-channel first-bleach frame within ±2 frames of the `.mat` `pacc`/`pdon` ground truth | Tether (§8 NFR-VALID (g), §9 M3) |
| Leakage acceptance ceiling | ≈ 0.3 (Cy3→Cy5 leakage typically 0.05–0.2; empirical median ≈ 0.09) | Tether tightening of Deep-LASI's loose `ct_lim = 1` |
| γ acceptance ceiling | γ ≤ 5 | `deeplasi/functions/gui/TracesTab/createTracesPlotLayout.m:172` (in-scope 2-color single-row table; `:157` is the out-of-scope 3-color variant, same values) |
| `min_window_frames` (per-trace bleach-window minimum) | 20 frames | `createTracesPlotLayout.m:172` (2-color single-row table default) |
| `min_qualifying_traces` (per-dataset minimum before manual entry) | ≈ 10 molecules | Tether default |
| Analysis window | auto = both-dyes-active (start → first bleach on summed intensity); manual per-trace override | Appendix B step 6 |
| FRET-histogram bootstrap | 1000 resamples; 95% percentile CI (2.5/97.5); resample unit = **molecule**; default seed = 0 | [König2013] BOBA-FRET (also in MASH-FRET [Börner2018]); cross-sample variability ⇒ molecule is the resampled unit (§7.7, §9 M3) |
| Lock staleness timeout | ≈ 30 min (wall-clock), then steal-confirm | Tether OneDrive policy (§5.4, §7.10) |
| Ranker success target (M5) | precision@k uplift ≥ 10 pts vs file order, prequential, median across videos | Tether default (§7.5, §9 M5) |
| Quality-ranker model (M5) | scikit-learn `HistGradientBoostingClassifier` on `P(good)`; curation-tuned defaults `learning_rate=0.1`, `max_iter=100`, `max_leaf_nodes=15`, `min_samples_leaf=5`, `l2_regularization=1.0`, `early_stopping=off`, `random_state=0` | Tether (§7.5, §9 M5; ADR-0034); [Chen2016] gradient boosting, ranking by `P(good)` per the probability-ranking principle [Robertson1977]; leaf/regularization defaults smaller than sklearn stock for small curation label sets |
| Per-trace UI latency budget | ≈ 100 ms render + navigate | Tether perf floor (§8 NFR-PERF) |
| Leakage-α validation band | **conjunctive**: relative-median difference ≤ 20% **and** both medians ∈ 0.05–0.2 (the band is plausibility-only, never a standalone pass) | Tether (§7.2, §9 M3) |
| **Idealization parity tolerance** (**frozen at M0.5**, 2026-06-26) | state count exact on ≥ 90% of traces; per-state mean ΔE ≤ 0.02 (absolute, FRET units); Viterbi per-frame agreement ≥ 95%; relative ELBO change ≤ 0.01 | Tether (§7.4, §9 M0.5/M2/M6); frozen from the measured cross-seed spread (20 self-reseeded `vbconhmm` fits × 2 committed fixtures; measured spread ≤ 1e-8 on all four metrics — the provisional defaults are confirmed). Evidence `schema/parity_tolerance.json`; rationale ADR-0009 |
| Idealization state-count selection (M2) | auto = max-ELBO over `nstates ∈ {1, 2, 3, 4}` (default model `vbconhmm`); explicit `nstates` = manual per-trace override | Tether (§7.4, §9 M2; ADR-0024); max-evidence (ELBO) VB-HMM model selection [Bronson2009] |
| Cold-start seed weight w₀ / decay law | w₀ ≈ 0.3 (human = 1.0); effective weight w = w₀ / (1 + n_human), recomputed each retrain | Tether (§5.1 `/labels`, §7.5, §9 M5) |
| Cross-condition drift advisory (M5) | two-sample Kolmogorov–Smirnov per engineered feature; overall (family-wise) α = 0.05, **Bonferroni-corrected** across the tested features (per-feature threshold α / n_tested); **advisory & overridable** | Tether (§7.5, §9 M5; ADR-0037); KS as a label-free distribution-drift detector [Porwik2022]; Bonferroni family-wise correction |
| kinSoftChallenge parity band (M8) | 2-state (Fig. 2) fitted rates within the reported inter-tool spread of ground truth — **≤ 12 % relative deviation per rate constant** (the 14-tool benchmark maximum; 5 % average, ≥ 3 % 1σ finite-size floor). GT k₁₂ = 0.15 / k₂₁ = 0.22 s⁻¹; Tether's base-env 2-state HMM + dwell-MLE lands at 2.3 % / 3.1 %. **Advisory** (gated `large` tier); 3-/4-state levels deferred (GT recorded). Frozen in `schema/kinsoft_reference.json` | [Götz2022] (§8 NFR-VALID(c), §9 M8; ADR-0048) |
| Deep-dataset preprocessing (M8) | window length = 500 frames (crop leading / zero-pad + `mask`); per-trace normalization = `per_trace_total` (one shared donor+acceptor scale = max total intensity, preserves the apparent-FRET ratio E=A/(D+A)); channels = (donor, acceptor) measured; train/val split = 0.2 stratified, seed 0 | Tether (§7.5, §9 M8; ADR-0047); DeepFRET non-ALEX DD+DA input [Thomsen2020], Deep-LASI [Wanninger2023]; retuned to the trained model in PR-1b |
| Deep-classifier model + training (M8) | 1-D CNN (`num_conv_layers` = 2 × Conv1d, `conv_channels` = 32, odd `kernel_size` = 5, ReLU, "same" length) → bidirectional LSTM (`lstm_hidden` = 32, sequence **packed** so padded frames are excluded — never treated as data) → linear accept/reject logit; **per-sample-weighted** `BCEWithLogitsLoss` (carries the M5 cold-start weights w₀/(1+n_human), §7.5); Adam `learning_rate` = 1e-3; `batch_size` = 32; `epochs` = 20; `dropout` = 0; `seed` = 0; `device` = cpu (base) | Tether (§7.5, §9 M8; ADR-0047); DeepFRET/Deep-LASI-style CNN+LSTM trace classifier [Thomsen2020][Wanninger2023]; consumes the Deep-dataset-preprocessing tensors above (`tether.ml.deep.model`, PR-1b-ii) |
| Deep-classifier fine-tuning + transfer (M8) | warm-start from a trained model (deep-copied — the base is never mutated) and continue training on newly accumulated labels; reduced Adam `learning_rate` = 1e-4 (⅒ the fresh-train LR); `epochs` = 10; `batch_size` = 32; `freeze_conv` = false (true freezes the CNN feature extractor, adapting only the LSTM summary + head); `seed` = 0. The fine-tuned model's `P(accept)` plugs into the same never-auto-drop `rank_by_score` / `precision_at_k` substrate as the M5 classical ranker (`rank_by_deep_model`), so the two rankers' outputs reconcile on the shared objective | Tether (§7.5, §9 M8; ADR-0047); transfer learning for smFRET trace selection [Li2020], pretrain-then-apply [Zhang2025]; `tether.ml.deep.model.fine_tune` / `rank_by_deep_model` (PR-4) |

`min_window_frames` (per-trace) and `min_qualifying_traces` (per-dataset) are distinct quantities and must not be
conflated. Deep-LASI's `ct_lim`, `γ_lim`, and `min_frames` are GUI-table defaults
(`createTracesPlotLayout.m:172`, the in-scope 2-color single-row `Data = [1, 1, 5, 20]` = `[de_lim, ct_lim, γ_lim,
min_frames]`; `:157` holds the identical values in the out-of-scope 3-color branch), not hardcoded source constants.

### 11.3 Glossary

- **ALEX / PIE** — alternating-laser excitation / pulsed interleaved excitation; provides the acceptor-under-
  acceptor-excitation channel needed for direct-excitation and stoichiometry correction. Out of scope (N1).
- **SMD** — Single-Molecule Dataset, a generalized HDF5 storage format for single-molecule data [Greenfeld2015];
  tMAVEN's interchange container.
- **TDP** — Transition Density Plot: a 2-D histogram of initial vs final idealized FRET state [McKinney2006].
- **ELBO** — Evidence Lower BOund; the variational objective used for model selection in vbFRET/ebFRET.
- **vbFRET / ebFRET / consensus VB-HMM** — variational-Bayes per-trace HMM [Bronson2009], empirical-Bayes
  population HMM [vandeMeent2014], and consensus variational-Bayes HMM idealization models (the VB-HMM basis is
  [Beal2003]/[Bishop2006]); all available via tMAVEN.
- **à trous / starlet wavelet** — an undecimated wavelet transform; its multiscale product yields a robust spot
  detector [Olivo-Marin2002].
- **MAD** — median absolute deviation; the per-scale noise estimate in the à trous detector.
- **`TIRFdata` / MCOS / `#refs#`** — Deep-LASI's custom MATLAB class stored as MATLAB-Class-Object-System objects
  in a v7.3 (HDF5) `.tdat`, requiring `#refs#`/`#subsystem#` resolution to decode.
- **`.tmap` / `.tdat`** — Deep-LASI's registration-map file vs full-session project file.
- **dual-view** — donor and acceptor imaged on one chip via a splitter; the two halves are registered to each
  other.
- **donor-anchored colocalization** — reading acceptor intensity at the mapped donor position regardless of
  independent acceptor detection, so dark/low-FRET acceptors are retained.
- **kinSoftChallenge** — a blind community benchmark of single-molecule kinetics analysis tools [Götz2022].

---

## 12. Development & version-control protocol (GitHub)

This section governs **distributed (git/GitHub) source-code version control and software supply-chain security**
for the public GPL-3.0 repository `github.com/bioedca/tether` (§4.1). Its scope is **source governance only** —
large-dataset versioning is already handled by the LFS / gated-CI fixture tiers (§8 NFR-FIXTURES) and is not
re-litigated here, and **no external data-versioning tool is introduced**. The governing posture is **solo
developer (bioedca) with CI and risk-classified review gates**: branch protection on `main` requires green CI plus
the §12.4 review path. There is no universal human-approval count, but material high-risk work requires the
qualified human/domain judgment defined there. The rules scale to required reviews + `CODEOWNERS` (§12.3). Unless flagged
otherwise, every GitHub capability below is **free for this public repo**.

### 12.1 Repository, account & identity

- **Repository.** `github.com/bioedca/tether` — public, GPL-3.0, account `bioedca`, public from M0 (§4.1, §9 M0).
- **Canonical identity.** The single authoritative commit-author/committer identity for this repo is
  `bioedca@u.northwestern.edu` — a convention introduced here in §12 (the PRD does not otherwise specify a
  commit-author email). The account's other address `bioedca@gmail.com` is **not** used for repo commits.
- **Signed commits — SSH signing.** Commits *and* tags are signed with an SSH key registered to the account as a
  *signing* key, so GitHub renders the **Verified** badge. Local config: `git config user.email
  bioedca@u.northwestern.edu`, `gpg.format ssh`, `user.signingkey <ssh-pubkey>`, `commit.gpgsign true`,
  `tag.gpgsign true`; the committer email is on the account's verified-emails list so the badge resolves.
  Signature verification — not a DCO `Signed-off-by` trailer — is the trust mechanism in the solo model (a DCO can
  be layered in at scale-up, §12.3).
- **2FA required** on the `bioedca` account (TOTP/passkey) — the primary account-takeover control for a
  solo-maintained public repo.
- **app version ← git.** The app version stamped into every project file (§8 NFR-REPRO) is derived from the signed
  annotated tag via `git describe --tags` (`setuptools-scm` / `hatch-vcs`), so a `.tether` provenance stamp resolves
  to a specific verified commit and a frozen `conda-lock` (§4.1, §12.7).

**GPL-3.0 compliance & attribution.** GPL-3.0 is required to embed tMAVEN [Verma2024] (§1 license rationale). Even
though **tMAVEN is never vendored** — reference clones are algorithm-reference only (§ Source-citation conventions,
§4.3) — compliance and good scientific practice require crediting it:

- **`LICENSE`** carries the verbatim GPLv3 license text; the "or later" grant is expressed via
  <!-- REUSE-IgnoreStart -->`SPDX-License-Identifier: GPL-3.0-or-later`<!-- REUSE-IgnoreEnd --> headers and the standard recommended notice (not a separate license
  body).
- **SPDX / REUSE.** Every source file carries <!-- REUSE-IgnoreStart -->`SPDX-License-Identifier: GPL-3.0-or-later`<!-- REUSE-IgnoreEnd --> + an
  `SPDX-FileCopyrightText` header; the **REUSE** spec (`REUSE.toml` / `LICENSES/`) makes licensing file-level
  machine-checkable, enforced by a `reuse lint` hook in pre-commit and CI (§12.6, §12.9).
- **`NOTICE`** records that Tether **interoperates with and runs an isolated tMAVEN sidecar** (GPL-3.0,
  [Verma2024], pinned commit `10f4230…`) shipping under its own license in its own environment (§4.3), and credits
  Deep-LASI [Wanninger2023] and MASH-FRET [Börner2018] as algorithm references. The M9 signed installer that
  *bundles* the sidecar (§9 M9) must ship tMAVEN's license text alongside Tether's; the SBOM (§12.8) lists the
  sidecar as a distinct, attributed component.

**Repository metadata files** (repo-root / `.github/`):

| File | Purpose |
|---|---|
| `README.md` | What Tether is (§1), install/quickstart, the provenance-first pitch, links to docs + CONTRIBUTING + license + a `CITATION.cff` pointer. |
| `CONTRIBUTING.md` | §12.2–§12.9 in contributor prose: branch naming, Conventional Commits, running `pre-commit` + the small-fixture suite, regenerating `conda-lock`, the schema-freeze rule (§5/§9 M0), the PR self-review checklist; states the solo+CI model and the scale-up path; notes that the `bioedca@u.northwestern.edu` commit-author identity is a §12-introduced convention. |
| `CODE_OF_CONDUCT.md` | Contributor Covenant; contact `bioedca@u.northwestern.edu`. |
| `SECURITY.md` | Supported version(s); private disclosure via GitHub **private vulnerability reporting** (not public issues); notes that the scheduled dependency audit backstops the gap that Dependabot does **not** re-solve conda **lock files** (§12.8). |
| `CITATION.cff` | Machine-readable academic citation (the "Cite this repository" button): authors, title, GPL-3.0, repo URL, and `references:` linking the PRD's `[BracketKey]` upstream tools ([Verma2024], [Wanninger2023], [Börner2018], [Greenfeld2015]). Version + DOI filled at release tags (optionally Zenodo-archived at M9). |
| `.gitattributes` | Git-LFS patterns for the large-fixture tier (§8 NFR-FIXTURES) — the ≈0.9 GB movie + kinSoftChallenge assets + large `*.hdf5` benchmarks — while the **small committed fixtures stay in plain git**; `* text=auto` line-ending normalization for the 3-OS matrix on Windows-primary development. |
| `.gitignore` | Python/build artifacts, the local-only Zarr scratch pyramid (never synced/committed — §4.1/§5.1), `*.lock` experiment markers (§5.1), local working `.tether` files, env dirs, mkdocs `site/`. |
| `.github/` | PR template (§12.4), issue forms (§12.5), `dependabot.yml` (§12.8), `CODEOWNERS` placeholder (§12.3), workflow YAMLs (§12.6). |

### 12.2 Branching & merge model (GitHub Flow)

- **Model — GitHub Flow.** `main` is **always releasable and protected** (§12.3). All work happens on short-lived
  branches off `main`, opened as a PR, merged via **squash-merge**, branch **deleted on merge**. No long-lived
  `develop`/`release` branches — milestones M0–M9 (§9) are tracked as GitHub Milestones (§12.5), not git branches.
- **Branch naming.** `type/issue-N-short-slug`, optionally scoped to a milestone or FR-ID: `feat/`, `fix/`, `docs/`,
  `chore/`, `refactor/`, `test/`, `ci/`, `build/`, `perf/`, `revert/`. Examples:
  `feat/issue-123-m1-atrous-detector`, `fix/issue-124-correct-nan-guard`, `docs/issue-125-mkdocs-deploy`. The slug is
  kebab-case, ≤ ~5 words; the branch name is not load-bearing (the PR title + linked issue carry authoritative
  metadata) — it exists for at-a-glance `git branch` scanning.
- **Conventional Commits** [ConventionalCommits] govern **both commit messages and PR titles**: `type(scope):
  summary`. The **scope is a §4.2 module** without the `tether.` prefix — `io | imaging | fret | idealize | ml |
  analysis | gui | project` — plus cross-cutting scopes `schema | ci | deps | docs | release`. Examples:
  - `feat(imaging): à trous wavelet spot detector (FR-EXTRACT)`
  - `fix(fret): never emit NaN factor on total-correction-failure (§7.2)`
  - `feat(io)!: freeze HDF5 schema skeleton at M0` — the `!` (or a `BREAKING CHANGE:` footer) marks an
    incompatible change.
  - Footers carry traceability: `Refs: #123`, `Closes: #123`, `Milestone: M3`, `FR: FR-CORRECT`.
- **Squash-merge + linear history + delete-on-merge.** One clean Conventional-Commit per PR on `main` keeps the
  generated changelog and `git bisect` legible and ties cleanly into the version stamp (§12.7). The squash commit
  message defaults to the PR title, which is itself lint-gated (§12.6).

### 12.3 Branch protection (solo + CI-as-gate)

`main` is governed by a GitHub **repository ruleset** (preferred over the legacy branch-protection UI — rulesets
are exportable as JSON, version-history-tracked, and layer cleanly):

- **No direct pushes** — every change via PR (the `push` event to `main` is blocked for everyone, the maintainer
  included; this is what makes CI the gate).
- **Require a pull request before merging.** Required approvals = **0** in the solo model; CI, the §12.4
  self-review checklist, and its risk-based review path are the agent merge gates. **Dismiss stale approvals on new
  commits** is pre-enabled for scale-up.
- **Require status checks to pass** + **require branches up to date** before merging (required checks listed in
  §12.6).
- **Require signed commits** — enforces the SSH-verified identity (§12.1) on everything landing on `main`.
- **Require linear history** — pairs with squash-merge (§12.2).
- **Require conversation resolution before merging** — even solo, this forces resolving every self-review thread
  and every actionable finding from the required risk-classified review path before merge.
- **Block force-pushes** and **block branch deletion** on `main`.

**How the solo dev merges.** With 0 ruleset approvals, bioedca squash-merges only after the §12.4 risk path is
complete on the exact head SHA and required checks are green on the current base. An agent may use auto-merge only
when a server rule or merge queue enforces that same head/base binding; otherwise the coordinator performs a
guarded merge. **No standing "include administrators / bypass" exemption** — a rare genuine emergency uses a
deliberate, logged temporary bypass, not a permanent admin exception.

**Scale-up path (documented, not active).** If contributors join: set **required approvals ≥ 1**, uncomment a
`CODEOWNERS` mapping §4.2 modules to owners (e.g. `/src/tether/idealize/ @bioedca`), enable **require review from
Code Owners**, keep **dismiss-stale-approvals** on, and optionally add a **DCO** check. None of this changes branch
names, commit convention, or CI jobs — only the approval count and the CODEOWNERS file flip.

### 12.4 Pull requests

Small, **milestone-scoped** PRs are the unit of work (ideally one issue ↔ one PR ↔ one squash commit); WIP opens
as a **draft PR** and cannot merge. The PR title is a Conventional-Commits string (§12.2) — it
becomes the squash commit and feeds the changelog. CodeQL remains enforced through code-scanning **default
setup** and the ruleset's `code_scanning` rule, not as a named status check. Agent-authored PRs also follow the
`AGENTS.md` risk path: Copilot first; Codex for standard/high changes; `@coderabbitai review` plus qualified
human/domain judgment for high/load-bearing changes. Reviews and checks bind to the final head SHA.

`.github/pull_request_template.md` carries the **self-review checklist** — the human-judgment gate in the solo model:

- [ ] Tests added/updated for the change; they run on the **small committed fixtures** (§8 NFR-FIXTURES) and pass
      on the 3-OS matrix.
- [ ] **Data policy respected** — no raw/private/unlicensed data or large data in ordinary Git; issue-authorized,
      redistributable fixtures use named small or LFS/gated paths with license and provenance (§8, §12.9).
- [ ] **conda-lock updated** if dependencies changed — base, **isolated tMAVEN sidecar**, and/or
      **isolated deep** lock, kept distinct (§4.1/§4.3); `conda-lock-verify` is green.
- [ ] **Schema freeze respected** — no structural change to the §5 HDF5 skeleton frozen at §9 M0; only additive
      *data* (`schema-guard` green; a legitimate structural change carries an ADR + an explicit schema-version
      bump, §12.6/§12.7).
- [ ] **Provenance / NFR-REPRO** — any new analysis stamps parameters + app version + provenance into the project
      file (§8 NFR-REPRO); app version resolves from `git describe` (§12.7).
- [ ] **Default parameters** — any new tunable is registered in the **§11.2** table (single source of truth), not
      hardcoded inline.
- [ ] **No secrets** committed (`secret-scan` green; mirrors push protection — §12.8/§12.9).
- [ ] **SPDX header present** on every new source file (`GPL-3.0-or-later`); `reuse lint` green (§12.1/§12.9).
- [ ] **Docs updated** (mkdocs / docstrings); if a resolved decision changed, the PRD and/or an ADR is updated in
      the same PR (§12.7).
- [ ] **Conventional-Commits** PR title; breaking changes carry `!` / `BREAKING CHANGE:` (§12.2).
- [ ] CodeQL clean; the low/standard/high review path is recorded and complete for the final head SHA (§12.3).

### 12.5 Issue tracking & project planning

**All work is tracked as GitHub Issues**, linked by the `Closes #N` footer (§12.2) so the issue ↔ PR ↔ commit ↔
FR/milestone chain is queryable.

**Label taxonomy** (prefixed namespaces, so labels group and filter cleanly):

| Namespace | Values |
|---|---|
| `type:` | `bug`, `feature`, `refactor`, `docs`, `test`, `chore`, `ci`, `perf`, `question`, `validation-oracle-failure` (a dedicated type for a §8 NFR-VALID oracle regressing) |
| `area:` | one per §4.2 module — `io`, `imaging`, `fret`, `idealize`, `ml`, `analysis`, `gui`, `project` — plus `schema`, `sidecar`, `packaging`, `docs` |
| `milestone:` | `M0`, `M0.5`, `M1` … `M9` (one per §9 milestone **including the fractional de-risking gate M0.5**, mirroring its GitHub Milestone for cross-filtering; redundant by design so a closed-milestone search still works) |
| `priority:` | `P0` (blocker) … `P3` (nice-to-have) |
| `status:` | `backlog`, `ready`, `in-progress`, `in-review`, `blocked`, `done` (mirror the board columns) |
| standalone | `good-first-issue`, `security`, `help-wanted` (the last two latent until contributors join) |

**§9 milestones → GitHub Milestones.** Each of M0, M0.5, M1 … M9 is a GitHub Milestone whose description **embeds
the §9 acceptance criteria verbatim as a markdown checklist**; an issue is filed per criterion (or coherent group)
and assigned to that Milestone, so milestone progress *is* the §9 sign-off checklist. M0's "schema freeze" and
M0.5's "freeze the §11.2 idealization-parity tolerance" become explicit checklist items, since later milestones
gate on them (§9 M0.5/M2/M6).

**Project board — GitHub Projects (v2)**, a single board with columns **Backlog → Ready → In progress → In review
→ Done** (the `status:` labels mirror the columns). Custom fields: `Milestone` (M0–M9), `Area` (§4.2 module),
`Priority`, `FR-ID`. The board is filtered by milestone to drive each §9 increment.

**Issue templates** (`.github/ISSUE_TEMPLATE/`, YAML issue forms): `bug.yml` (repro, expected vs actual, OS,
Tether version via `git describe`, fixture/`.tether` involved, traceback; auto-labels `type:bug`); `feature.yml`
(motivation, FR-ID/§-ref, milestone, acceptance criteria; auto-labels `type:feature`);
**`validation-oracle-failure.yml`** — *project-specific*: which §8 NFR-VALID oracle (a–g) and §9 milestone gate
failed, the fixture used (small committed vs gated large tier), the measured-vs-§11.2-tolerance numbers, and the
suspected §4.2 module; auto-labels `type:validation-oracle-failure` + `priority:P0`, making a parity/tolerance
regression a first-class triagable event; `config.yml` routes security reports to the SECURITY.md private-advisory
flow (§12.8), not public issues.

### 12.6 Continuous integration (GitHub Actions)

CI is the merge gate. Workflows live in `.github/workflows/`; a composite action
`.github/actions/setup-env/` is the single source of truth for env setup, reused by `ci.yml`, `schema-guard`,
`docs`, and `release.yml`.

**Reproducible env from the committed lock (pin-and-hold, not track-latest).** CI **restores** the committed,
multi-platform `conda-lock` (`linux-64`, `osx-64`/`osx-arm64`, `win-64`) — it **never solves the environment
fresh**. The lock is the single source of truth (§4.1); CI does `pip install -e . --no-deps` so the lock — not
pip's resolver — owns every dependency. A re-lock is a **deliberate** developer action committed as its own PR
(validated by `conda-lock-verify`). The **sidecar** has its own `sidecar/conda-lock.yml` (PyQt5 + `numpy<2`, the
trimmed tMAVEN `install_requires` subset — `biasd` omitted, `numba` upper-bounded, §4.1/§4.3); it is **never**
merged into the base lock and is exercised in a **separate job** so the base stack's modern numpy and the sidecar's
`numpy<2` never share a process — exactly the isolation §4.3 mandates.

**3-OS matrix.** `os: [ubuntu-latest, macos-latest, windows-latest]` × **one pinned Python** (the lock's version,
inside Numba's supported window — §4.1). Note `macos-latest` runners are now Apple Silicon (**arm64**), so
`osx-arm64` is the CI-exercised mac platform; `osx-64` (Intel mac) is **locked-but-not-CI-tested** unless a
`macos-13` leg is added. Under pin-and-hold the matrix is "tested-against" on a single Python, not
a range; a "next-Python readiness" canary, if ever wanted, is added as a **non-required** `allow-failure` leg, not
a second required pin.

**Headless Qt / napari.** GUI tests run with `QT_QPA_PLATFORM=offscreen` everywhere and, on Linux, wrapped in
**xvfb** (napari/OpenGL paths still want an X server even offscreen); `NAPARI_ASYNC=0` + a headless-safe GL. The
embedded napari panel (§4.1; M0 acceptance "TIFF opens and displays in napari") is smoke-tested by opening the
**small committed** big-endian TIFF slice, instantiating the viewer, asserting layer/dtype/shape, and tearing
down; the keyboard **focus contract** (§7.3 FR-ROUNDTRIP) is tested at the controller/event-filter level
headlessly (no pixel assertions). These are marked `@pytest.mark.gui` so they select/skip per leg.

**Sidecar parity job (`sidecar.yml`).** Separate env from `sidecar/conda-lock.yml`; runs the **M0.5 vbFRET
round-trip** (drive `tmaven.maven.maven_class` headless → export SMD → assert it opens) and asserts vbFRET on the
small fixtures + consensus VB-HMM / ebFRET on the committed ≥ 50-molecule SMD (`example-data/tmaven-model/
model-source-smd-281mol.hdf5`) meet the **§11.2 idealization-parity tolerance** (state count ≥ 90% exact, per-state
|ΔE| ≤ 0.02, Viterbi ≥ 95%, relative ΔELBO ≤ 0.01). The tolerance is a **frozen input ratified once at M0.5
(§9)** — CI asserts *against* the frozen numbers, never recomputes them; because tMAVEN self-reseeds, CI uses
statistical tolerance (never bit-exactness) over seed-averaged replicates. If headless `maven_class` proves
non-reproducible cross-OS, the same job exercises the pre-committed IPC-bundled-sidecar fallback instead (§4.3,
§10).

**schema-guard — the M0 freeze gate (strongest governance fit).** M0 freezes the full HDF5 group skeleton + the
specific fields (§5/§9 M0): `molecule_id` UUID + `molecule_key`, the `/labels` provenance fields, the metadata-only
`/movies` signature, condition-key + provisional fields. `schema-guard.yml` dumps the schema the code declares
(`scripts/dump_schema.py`, the same builder that writes a fresh `.tether`) and **diffs it against a committed golden
manifest** `schema/schema_frozen.json`:

- **Additions** (new group / dataset / attribute; the editable category list + integer↔category lookup are additive
  *data* under the already-declared `/conditions`, §5.1) → **pass**.
- **Removals, renames, dtype/shape/identity changes** to a frozen field → **fail**, naming the offending field —
  protecting `molecule_id`/`molecule_key`/`/labels` provenance/movie-signature from silent drift.
- The **schema version stamp** must be present and **monotonic** (the guard refuses a decrement, mirroring §5.4
  "refuses files newer than the app"). A deliberate structural change updates the golden manifest in the same PR
  with an explicit `schema-change:` footer + an ADR (§12.7), making structural change loud and auditable.

A cheap bonus check folded into `ci.yml` (high-value for §8 NFR-REPRO): write a `.tether`, re-open it, assert the
version stamp + frozen fields survive the round-trip.

**Default tier vs gated large-fixture tier (§8 NFR-FIXTURES).** Default CI runs **only the small committed
fixtures** — the cropped UCKOPSB slice + a few molecules + the ≥ 50-molecule curated SMD — covering M0 napari open,
M2 round-trip/histogram smoke, and M6 consensus/ebFRET parity. The **gated tier** (`large-fixtures.yml`,
`workflow_dispatch` + weekly `schedule` + a `large-fixtures` PR label only) LFS-pulls the ≈0.9 GB UCKOPSB movie +
kinSoftChallenge slice and runs the **M1 extraction-vs-Deep-LASI** acceptance and the **M8 kinSoftChallenge**
kinetics check (advisory until the slice is acquired, §9 M8). It is **never a required check**, so a contributor
without the big blobs is never blocked; default jobs use `lfs: false` / sparse checkout to avoid the 0.9 GB pull.
The deep/GPU M8 validation (RTX 4060 floor) cannot run on hosted runners — it is `deep-gpu.yml`, a
`workflow_dispatch` job targeting a self-hosted GPU runner (or stays local-only, advisory), outside the required set,
consistent with "GPU optional add-on" (§8 NFR-XPLAT). It installs the documented, unpinned CUDA torch wheel at run
time (the committed `deep/conda-lock.yml` stays CPU-only, ADR-0047) and runs the same `-m deep` train-smoke as the
CPU `deep.yml` leg, exercising the `device="cuda"` path.

**Required status checks (branch protection, §12.3):**

| Check | Active from | Covers |
|---|---|---|
| `lint` (ruff lint + format) | M0 | §4.1 ruff |
| `test (ubuntu-latest / macos-latest / windows-latest)` | M0 | 3-OS matrix on small committed fixtures |
| `pre-commit` | M0 | §12.9 hooks mirrored in CI |
| `commitlint` (PR-title / Conventional-Commits) | M0 | §12.2 |
| `secret-scan` (gitleaks; mirrors push protection) | M0 | §12.8/§12.9 |
| `conda-lock-verify` (locks ↔ sources, base + sidecar + deep) | M0 | pin-and-hold integrity §4.1/§4.3 (deep stack from M8) |
| `schema-guard` | M0 | additive-only HDF5 freeze §5/§9 M0 |
| *(CodeQL — **not** a required status check; enforced by the ruleset's separate `code_scanning` rule, §12.8)* | M0 | static analysis §12.8 |
| `docs-build` (mkdocs `--strict`) | M0 | §4.1 / §8 NFR-PKG |
| `sidecar / parity` | M0.5 | §11.2 idealization-parity tolerance (advisory before M0.5) |

`large-fixtures.yml`, `deps-audit.yml`, and `scorecard.yml` (§12.8) are **scheduled/manual, not required**.

**Hardening (every workflow).** Each `uses:` is pinned to a full **40-char commit SHA** with a `# vX.Y.Z` comment
(never a moving tag); top-level `permissions: { contents: read }`, elevated per-job only where needed
(`security-events: write` for CodeQL; `pages: write` + `id-token: write` for docs deploy; `contents: write` +
`id-token: write` + `attestations: write` for the release; `issues: write` for the audit). `concurrency: { group:
${{ github.workflow }}-${{ github.ref }}, cancel-in-progress: true }` cancels superseded runs — **except**
`release.yml` (`cancel-in-progress: false`; never cancel a half-built signed installer).
`setup-micromamba`'s `cache-environment` is keyed on the lock hash + OS + Python, with separate base/sidecar cache
namespaces; pin-and-hold means the lock rarely changes, so hit-rate is high and a lock bump cleanly invalidates it.

### 12.7 Releases & versioning (SemVer)

- **SemVer** [SemVer] on the §9 track: `0.x.y` through M0–M8, **1.0.0 at M9**. The Conventional-Commit → bump map
  feeds the automated changelog/release tooling (`git-cliff` / `release-please`):

  | Commit type | Pre-1.0 (0.x.y) | Post-1.0 | Changelog section |
  |---|---|---|---|
  | `fix:` | patch | patch | Bug Fixes |
  | `feat:` | minor | minor | Features |
  | `feat!:` / `BREAKING CHANGE:` | minor (documented as breaking) | **major** | ⚠ Breaking Changes |
  | `perf:` | patch | patch | Performance |
  | `docs/test/ci/build/chore/refactor:` | no release bump (changelog "Internal") | same | — |

- **Milestone → tag.** Each milestone/release is cut as an **annotated, SSH-signed** tag `vMAJOR.MINOR.PATCH`
  (e.g. `v0.3.0` at M3, `v1.0.0` at M9), and the **base, sidecar, and deep locks are frozen at that tag** per
  pin-and-hold (§4.1) — the tag is the single point where all three are snapshotted for the release.
- **`CHANGELOG.md`** is **generated from commit history**, never hand-edited (so squash titles must be clean
  Conventional Commits — §12.2/§12.6).
- **Release pipeline (`release.yml`, triggered on a signed `v*.*.*` tag):**
  1. **verify-tag** — assert the tag is signed + annotated, on `main`, and all three locks are committed and clean;
     re-run the full required suite as a gate.
  2. **build-installers** (3-OS matrix) — **constructor** signed installers (§4.1, §8 NFR-PKG, §9 M9) bundling the
     **trimmed/pinned sidecar** (no install-time git/network; `biasd` omitted; numba bounded — M9 acceptance);
     OS code-signing (Windows Authenticode, macOS notarization), Linux installer + checksum. Stamp the app version
     from the git tag (`git describe`) so it flows into the project file (§8 NFR-REPRO).
  3. **provenance** — generate an **SBOM** (CycloneDX, via Syft) over all released env stacks; SHA-256 checksums for every
     installer; publish the **base, sidecar, and deep locks** as release assets (any release is
     exactly re-creatable); attach **build-provenance + SBOM attestations** (`actions/attest-build-provenance`,
     `actions/attest-sbom`; `id-token: write` + `attestations: write`; free for public, verifiable with
     `gh attestation verify`).
  4. **changelog** — auto-generate from Conventional Commits since the previous tag into `CHANGELOG.md` + the
     Release body.
  5. **publish** — create the GitHub Release; upload installers + checksums + SBOM + all three lock files. (A
     conda-forge feedstock is an optional later follow-on, not required at first 1.0.0.)
- **Docs deploy.** mkdocs (Material) is built in CI on every PR (`docs-build`, required) and **deployed on
  release** to GitHub Pages (versioned via `mike`), satisfying §4.1 / §8 NFR-PKG.
- **ADRs.** Architecture Decision Records under `docs/adr/` (MADR, `NNNN-title.md`) home the ~50 resolved PRD
  decisions (the v1.1/v1.2 audit resolutions) so rationale survives prose harmonization; any **schema-structure
  change** (which `schema-guard` blocks without one — §12.6) **requires an ADR**. The PRD lives in-repo under
  `docs/` and changes only via PR under the full §12.3 ruleset, so the spec is versioned, reviewed, and signed
  exactly like code.

### 12.8 Security & supply chain

Everything below is **free for this public repo** (the listed capabilities are paid only for private/internal
repos, which does not apply).

- **CodeQL code scanning.** **Default setup** is recommended for the solo dev (GitHub auto-detects Python — pure
  Python, `build-mode: none`, no compiled step — manages the analysis YAML, auto-updates query packs; runs on PR,
  push to `main`, and a weekly schedule). Switch to **advanced setup** (committed `codeql.yml`) only to add the
  `security-and-quality` suite or align triggers. CodeQL scans Tether's own `tether.*` packages (§4.2) — the
  `numpy<2` tMAVEN sidecar is not vendored (§4.3), so CodeQL never scans tMAVEN internals. The ruleset's separate
  code-scanning rule blocks new high/critical alerts (§12.3). CodeQL is a scanner, not a substitute for any
  required author, AI, human, or domain review.
- **Secret scanning + push protection.** Both **enabled**. Push protection blocks a recognized secret **at `git
  push`** before it reaches the public remote (the critical control for a public repo — a leaked-then-deleted
  secret on a public repo must be treated as compromised). Repo-level **custom patterns** can be added for any
  lab-/Northwestern-specific token; default to none until a concrete pattern exists. **Bypass policy:** the default
  is to **remediate** (remove + rotate), bypassing **only** for a verified false positive (e.g. a fixture string
  that merely matches a pattern), with the reason recorded; every bypass is logged and reviewed.
- **Dependabot — what actually applies (`.github/dependabot.yml`):** watches **`pip`** (any pip-installable deps —
  `pyproject.toml`/`requirements*`, mkdocs deps, CI test extras) and **`github-actions`** (keeps SHA-pinned actions
  current via grouped weekly PRs). All three Dependabot capabilities are enabled: **alerts** (GHSA/CVE vs the
  dependency graph), **security updates** (auto-PR a vulnerable dep to a fix), and **version updates** (grouped,
  weekly, Conventional-Commit prefixes so squash titles stay compliant).
- **CRITICAL — Dependabot does *not* re-solve the conda lock files.** As of its GA on **2025-12-16** Dependabot
  supports a **`conda` ecosystem** for **`environment.yml` version updates** — but it does **not** update
  **conda *lock files*** (`conda-lock.yml`), and does not handle private registries or vendoring. So it cannot
  re-solve or bump the committed base `conda-lock.yml`, isolated `sidecar/conda-lock.yml`, or isolated
  `deep/conda-lock.yml` (§4.1/§4.3). Each has a tracked `environment.yml` input, but Dependabot is intentionally not
  configured to mutate it because it cannot regenerate the paired lock. All three stacks remain deliberate human
  re-locks, not Dependabot targets. Do not
  represent the lock files as Dependabot-monitored. The stacks are governed by **(a)** the pin-and-hold
  deliberate-bump policy with a human-authored re-lock PR validated by `conda-lock-verify`, and **(b)** the
  scheduled advisory `deps-audit.yml` job over locked PyPI packages; it opens a tracking issue on a CVE rather
  than auto-PRing into a frozen lock.
- **SBOM + artifact attestations** on the M9 release path — see §12.7 (CycloneDX SBOM + build-provenance/SBOM
  attestations binding each artifact to the exact workflow + commit; the SBOM is also the natural place to record
  the bundled-but-unvendored tMAVEN sidecar for GPL attribution, §12.1). GitHub's repo **SBOM export**
  (dependency-graph) is kept available for the dependency-level bill of materials.
- **OpenSSF Scorecard** (`scorecard.yml`, optional, free for public) audits the repo's own supply-chain posture
  (token permissions, pinned dependencies, branch protection, signed releases) and uploads results to the
  code-scanning tab; SHA-pinned like every action. Note some checks (e.g. **Signed-Releases**, which looks for
  detached artifact signatures) may not score perfectly even with attestation-based provenance, so Scorecard stays
  **advisory, not required**.
- **Private vulnerability reporting (PVR)** — **enabled** (Settings → Code security): researchers file privately
  via "Report a vulnerability", opening a draft repository security advisory; `SECURITY.md` points at this flow with
  `bioedca@u.northwestern.edu` as fallback. The dependency graph (free, on) underpins both alerts and SBOM export.

**Free-vs-paid summary (this public repo):**

| Capability | Status |
|---|---|
| CodeQL (default or advanced) | **Free** (paid for private/internal) |
| Secret scanning + push protection (+ custom patterns) | **Free** (paid "Secret Protection" for private/internal) |
| Dependabot alerts / security updates / version updates | **Free** |
| Dependency graph + SBOM export | **Free** |
| Private vulnerability reporting + security advisories | **Free** |
| Artifact attestations (build provenance + SBOM, CycloneDX/SPDX) | **Free** (needs `id-token`/`attestations: write`) |
| OpenSSF Scorecard action + badge | **Free** |
| Branch protection / rulesets, required checks, required signed commits | **Free** |
| **conda / conda-lock** Dependabot coverage | **conda `environment.yml` supported (GA 2025-12); `conda-lock.yml` NOT covered** — locks follow pin-and-hold (§4.1) + scheduled `pip-audit`/`safety` (§12.8) |

### 12.9 Pre-commit & local hooks

The **pre-commit framework** runs the same checks locally (on commit) and in CI (the `pre-commit` required job,
§12.6), so what blocks a push also blocks a PR:

- **ruff** — lint *and* format (replaces black/flake8/isort).
- **trailing-whitespace**, **end-of-file-fixer**, **check-yaml**, **check-merge-conflict**.
- **check-added-large-files** — blocks an accidental big-movie/benchmark commit into plain git (it must go to the
  LFS / gated tier — §8 NFR-FIXTURES); threshold tuned to sit **above** the small committed fixtures and **below**
  the large tier.
- **secret scan** — **gitleaks** (or `detect-secrets`), mirroring GitHub push protection so a secret is caught
  before it reaches the remote (§12.6/§12.8).
- **conda-lock up-to-date** — verifies the committed base lock, the separate sidecar lock, *and* the isolated
  `deep/` torch lock (§4.1/§4.3; deep from M8) are in sync with their `environment.yml` sources (no drift between
  intent and lock).
- **SPDX / REUSE** — `reuse lint` + a fast SPDX-header presence hook (§12.1).
- **commitlint / Conventional-Commits** on the commit message (and the PR title in CI — §12.2).

### 12.10 Bootstrap at M0 & threading through milestones

Governance is **established whole at M0** and then enforced continuously — no calendar dates, only capability gates
(mirrors §9).

**M0 bootstrap checklist** (all part of the §9 M0 "Foundation" deliverable):

- Repo `github.com/bioedca/tether` created public + GPL-3.0; SSH signing key + 2FA + verified-email configured
  (§12.1); the `main` ruleset enabled (no direct push, required checks, signed commits, linear history,
  conversation resolution, no force-push/delete — §12.3).
- `ci.yml` (lint + 3-OS test + pre-commit + commitlint + secret-scan + conda-lock-verify + docs-build), `codeql.yml`
  (or default setup), `schema-guard.yml` (**freeze goes live** against the M0 golden manifest), and `dependabot.yml`
  (pip + github-actions) all green on 3 OSes.
- Secret scanning + push protection + private vulnerability reporting enabled; `deps-audit.yml` + (optional)
  `scorecard.yml` scheduled.
- Repo metadata + `.github/` scaffolding landed (§12.1): LICENSE, README, CONTRIBUTING, CODE_OF_CONDUCT,
  SECURITY.md, CITATION.cff, NOTICE, `.gitattributes` LFS, `.gitignore`, PR template + self-review checklist, issue
  forms, labels, GitHub Milestones M0–M9 with §9 acceptance criteria, Projects-v2 board, pre-commit config.
- **M0 acceptance additions:** branch protection rejects a PR with red CI; a signed/verified commit lands on
  `main`; `schema-guard` fails a deliberately structure-breaking schema change (§9 M0).

**Per-milestone governance touchpoints:**

| Milestone | Governance touchpoint |
|---|---|
| **M0** | All of the above established; schema-guard freeze live; CI green on 3 OSes. |
| **M0.5** | `sidecar / parity` becomes **required** (vbFRET round-trip + the §11.2 parity row frozen with measured numbers); M2/M6 parity checks inherit the frozen row by reference. |
| **M1/M2/M3/M6** | §9 acceptance criteria become `@pytest.mark`-tagged tests in the **default (small-fixture) tier**; M1 full-movie extraction + M8 kinetics live in `large-fixtures.yml`. **M3** adds the batch-runner + perf-budget (§8 NFR-PERF / §11.2) and the bleach-frame parity (§8 NFR-VALID (g)) checks. |
| **M9** | `release.yml` produces the 3-OS signed bundled installers + frozen `conda-lock`s + SBOM + attestations + auto changelog; `docs.yml` deploy + the validation suite run end-to-end; SemVer **1.0.0** is cut from a signed `v1.0.0` tag. |

The **schema-guard** gate persists from M0 through every later milestone, mechanically enforcing the §5/§9-M0
invariant that later milestones add **DATA, not STRUCTURE**.

---

## Appendix A — Input formats

Real lab files used as reference fixtures live under `example-data/`. Properties below are the on-disk values for
the reference acquisitions.

| File | Format | Key contents |
|---|---|---|
| Movie `*.tif` | multi-page TIFF, 512×512, 16-bit **big-endian** (byteorder `>`), uint16, 1700 frames, ≈0.9 GB (512·512·1700·2 = 891 MB, uncompressed), photometric min-is-black | raw dual-view TIRF movie; `tifffile.memmap` → O(1) frame access |
| `*.tmap` | MATLAB v5 `.mat` (≈4 MB), variable `m` = 1×2 | dual-view channel mapping / registration transform (Appendix E, Stages 6–10) |
| `*.tdat` | MATLAB v7.3 (HDF5, ≈37 MB), struct `temp` with `TIRFdata` channel objects (`MATLAB_object_decode=3`, `#refs#`/`#subsystem#`/MCOS) | full Deep-LASI project: **coordinates** (`ParticlesColocalized`), 21×21 patches, masks (`MaskPSF`/`MaskBG`), ~10 series/molecule, **α/β/γ** (remap per Appendix B), `FrameTime`, source-movie ref, NN + 2/3/4-state HMM states, categories |
| `…-donc-accc-w.txt` | whitespace text, 1700×500 (rows = frames; 250 molecules × interleaved donor/acceptor columns) | per-frame corrected intensities only; **no coordinates** |
| `DeepLASI_MAT_export_*.mat` | MATLAB v5 `.mat` (≈9 MB) | per-video, 250 molecules × 1700: raw/corrected/background donor + acceptor, FRET, per-molecule `b` (= Deep-LASI β = leakage → Tether α) and `g` (= γ), `pacc`/`pdon` photobleach frames, `range`, `select` (250×18) + `tags` (18 named categories), movie path/name, **and `fret_pairs` (250×4) = per-molecule donor/acceptor pixel coordinates** |
| tMAVEN `*.hdf5` | HDF5 SMD (Appendix D) | idealization interchange (primary) + standalone-GUI hand-off; curated subset; **no coordinates** when sourced from `.txt` |

**Coordinate availability across the file set.** Of `.tif`, `.tdat`, `.mat`, `.txt`, and SMD, the **`.tdat`
(`ParticlesColocalized`) and the `.mat` (`fret_pairs`) carry pixel coordinates**; the `.txt` and the tMAVEN SMD do
not. Full round-trip re-analysis therefore requires the `.tdat` *or* the `.mat` (or native re-extraction seeded
from either's coordinates).

**Environment note.** The base environment pins one concrete Python ≥ 3.11 inside Numba's supported window plus a
numpy upper bound (exact pins live in a committed `conda-lock`, pin-and-hold per release — §4.1); the sidecar
separately pins `numpy<2` + PyQt5 and the **subset of `tmaven/setup.py` needed for vbFRET / consensus / ebFRET**
(`biasd @ git+main` omitted, `numba` bounded — §4.1/§4.3). `FrameTime` always
comes from the file. Native extraction mirrors Deep-LASI's aperture model (21×21 box, summed PSF mask + annular
background) as its reference (all radii configurable). A **donor-only calibration sample** (e.g. a Cy3-only
`.tdat`, geometry 512×256, channels G/R) is a recognized input used to set the per-condition global leakage α
(Appendix B).

---

## Appendix B — Correction-factor scheme (single-laser 2-color)

This appendix is the **single source of truth** for the correction scheme; §2, §6, §7.2, and Appendix E reference
it. The scheme is cross-checked against MASH-FRET [Börner2018] and Deep-LASI [Wanninger2023] and grounded in the
accurate-FRET literature [Hellenkamp2018][Roy2008][Lee2005][McCann2010].

### B.1 Naming map (the references use opposite Greek letters)

| Physical factor | **Tether** | MASH-FRET | Deep-LASI |
|---|---|---|---|
| Donor→acceptor **leakage** (additive) | **α** | bt (bleedthrough) | **β** |
| **Direct excitation** of acceptor (additive) | **δ** (= 0, dropped) | dE | **α** |
| **Detection/QY ratio** (multiplicative) | **γ** | γ | **γ** |
| Stoichiometry-only excitation factor | n/a | β | n/a |

Tether's α/δ/γ convention is the field standard [Hellenkamp2018][Lee2005]. Deep-LASI's internal MATLAB naming is
inverted relative to this convention: in `deeplasi/functions/deeplearning/deep_autocorrect_2color.m` the stored
field `Beta` holds crosstalk/leakage `ct` (`= mean(I_DA)/mean(I_DD)` over donor-only frames) and the stored field
`Alpha` holds direct excitation `de` (`= mean(I_DA)/mean(I_AA)`, which requires the ALEX `aa` channel); the same
field assignment appears in `deeplasi/functions/traces/manualCorrectionFactors.m`. MASH-FRET independently uses `bt` = bleedthrough/leakage,
`dE` = direct excitation, and `β` = stoichiometry-only excitation (which appears only in the stoichiometry formula,
never in the FRET formula) — `MASH-FRET/docs/trace-processing/workflow.md`,
`MASH-FRET/docs/output-files/bet-beta-factors.md`.

> **On Deep-LASI import:** β → Tether α (apply, additive); α → Tether δ (inert/0, ALEX-only); γ → Tether γ.
> Never fold β into γ; never treat Deep-LASI α as Tether α. Misattributing β silently drops a real leakage
> correction and shifts every imported E.

### B.2 Correction order and formulas (both references agree)

Order (load-bearing): **background → leakage (α) → direct-excitation (δ = 0) → gamma (γ)**.

1. **Background** — per-molecule, per-channel local subtraction at extraction (Deep-LASI's simplicity); later
   expose MASH's selectable estimators + a static/time-varying toggle. Yields I_D*, I_A*.
2. **Leakage α** — *primary route:* a dedicated donor-only sample (the lab's typical FRET traces often lack a clean
   acceptor-bleach step). Load a donor-only acquisition (Deep-LASI `.tdat` or movie), read the two channels (donor
   in the donor channel + donor leakage in the acceptor channel), and take **global α = median over donor-only
   molecules of I_DA/I_DD** (background-subtracted). *Supplement/cross-check:* the per-trace post-acceptor-
   photobleach tail (α = I_DA/I_DD) on FRET traces that show a clean acceptor-bleach step. Apply additively:
   **I_A,corr = I_A* − α·I_D***. α is a **per-condition** calibration, stored with provenance to its donor-only
   source. The leakage coefficient multiplies the *donor*, consistent with the standard leakage subtraction
   [Lee2005][Roy2008]. Tether computes **both** the donor-only-sample α and the post-bleach-tail α whenever the data
   allow; their agreement (under matched gates, on the same FRET dataset) is the M3 validation oracle (§9 M3), since
   Deep-LASI offers no donor-only route for a direct comparison. The test is **conjunctive**: it passes iff the
   relative difference of the two population medians ≤ 20% **and** both medians lie in 0.05–0.2 (the band is a
   plausibility check only, never a standalone pass — §7.2, §11.2).
3. **Direct excitation δ** — **dropped, default 0, inert.** Its estimator needs the acceptor-under-acceptor-
   excitation signal I_YY, which requires ALEX [Lee2005][Hohlbein2014]; pre-FRET acceptor signal is treated as
   background (already removed). This is the correct single-laser simplification, not an omission.
4. **Gamma γ** — trace-wise across the **acceptor-bleach step** (acceptor drop / donor rise) on leakage-corrected
   intensities, averaged over a tolerance window (3 frames each side, configurable; §11.2), on traces where the
   acceptor bleaches before the donor; global = **median** [McCann2010]. Formally
   γ = (I_A,spFRET − I_A,after) / (I_D,after − I_D,spFRET) = ΔI_A/ΔI_D
   (`deeplasi/functions/deeplearning/deep_autocorrect_2color.m:118-130`), with the ALEX `de·(da+dd)` term dropped
   for δ = 0. Manual override available.
5. **E = I_A,corr / (I_A,corr + γ·I_D,corr)**; **apparent E** = same with α = δ = 0, γ = 1. This reduces from the
   general gamma-corrected expression E = (1 + γ·I_D/I_A)⁻¹ [Hellenkamp2018][McCann2010] and matches tMAVEN's
   intensity-ratio computation — tMAVEN forms the ratio in `tmaven/tmaven/maven.py:83-86` (`calc_relative`) after
   applying γ in `tmaven/tmaven/controllers/corrections/corrections.py` (`gamma()` :212).
6. **Analysis window** — auto-default = both-dyes-active (trace start → first photobleach on the summed-intensity
   trace); manual per-trace override.

Implementable with donor-excitation frames + a photobleaching detector only; all steps reuse the same bleach-step
detection (Tether's native reimplementation of tMAVEN's Bayesian single-step model [Verma2024], run per channel —
Appendix E Stage 16). Apparent-E analysis and histograms require no photobleaching; only the α/γ corrections do.
Deep-LASI's acceptance gates (`< 1` for leakage, `γ ≤ 5`, window `> 20` frames) are GUI-table defaults
(`createTracesPlotLayout.m:172`, the in-scope 2-color branch); Tether tightens the leakage gate to a configurable
physical ceiling (≈ 0.3 default) so outliers do not skew the median (§11.2). Stored Deep-LASI factors come from the median branch
(`deep_autocorrect_2color.m:95-148`), with a population-median substitution when a molecule's own factor is
invalid — Tether matches this (per-molecule value retained when valid, median fallback otherwise).

The exact Deep-LASI-grounded estimator forms (the donor-only leakage window, the non-ALEX `γ = ΔI_A/ΔI_D` step
formula, the gates, and median aggregation) are detailed in Appendix E, Stages 16–18.

---

## Appendix C — tMAVEN plot inventory (M6 native-reproduction scope)

Enumerated from tMAVEN source at the pinned commit. There are **exactly seven distinct plot types**: six
`controller_base_analysisplot` subclasses (registered in
`tmaven/tmaven/controllers/analysis_plots/analysisplots.py`) plus the per-trace viewer in
`tmaven/tmaven/trace_plot/`. The smFRET / ND-Normalized / ND-Raw modes and post-synchronization are *variants*, not
separate plots. Groups B/C are idealization-gated; A/D work without a model.

- **A1 — 1D Population Histogram** (E_FRET or normalized/raw intensity; `signal_nbins = 151`; density, optional
  log) with a fitted Gaussian/GMM overlay (dashed components + solid combined) + N annotation.
  `tmaven/tmaven/controllers/analysis_plots/data_hist1d.py`.
- **A2 — 2D Time-vs-Signal Histogram** (synchronized FRET heatmap): x = time (s), y = E_FRET, colour = frame
  density; raw OR post-sync to HMM transitions, with smoothing.
  `tmaven/tmaven/controllers/analysis_plots/data_hist2d.py`.
- **B1 — Transition Density Plot (TDP)** [McKinney2006]: x = initial E, y = final E from neighbour pairs
  (`nskip = 2`), restricted to state-change frames when idealized; log-normalized.
  `tmaven/tmaven/controllers/analysis_plots/data_tdp.py`.
- **B2 — Survival / Dwell-Time Distribution** (with a residuals subplot): histogram OR survival curve;
  single/double/triple-exponential + stretched + transition-matrix-derived fits with parameter annotations.
  `tmaven/tmaven/controllers/analysis_plots/survival_dwell.py`.
- **B3 — Transition-Probability Histogram**: a 1D histogram of HMM transition-matrix probabilities for a chosen
  state pair, pooled across trace-level VB models; optional KDE overlay.
  `tmaven/tmaven/controllers/analysis_plots/tm_hist.py`.
- **C1 — vbFRET State-Number Distribution** [Bronson2009]: a bar chart, x = N states (1–10), y = N trajectories
  (requires an active vbFRET model). `tmaven/tmaven/controllers/analysis_plots/model_vbstates.py`.
- **D1 — Per-Trace Viewer**: a 2×2 grid — donor/acceptor intensity + relative-intensity/E_FRET time-series (left)
  and marginal probability histograms (right); three alpha-graded segments split at pre-truncation + photobleach;
  the Viterbi/idealized path overlaid on the FRET panel; mode-switchable. This *is* Tether's curation trace dock
  (FR-ROUNDTRIP). `tmaven/tmaven/trace_plot/multi_plot.py`.

---

## Appendix D — tMAVEN SMD + model HDF5 schema

Schemas below are the on-disk structures of the reference fixtures, introspected with h5py.

### D.1 SMD container (curated-traces file, `@format='SMD'`) — root group `dataset`

- `dataset/data/raw` — `(n_molecules, n_frames, 2)` float64 (donor, acceptor); `dataset/data/source_index` —
  per-trace source id.
- `dataset/sources/{i}` — `@source_name` (+ root `@source_list`); in the reference fixtures these are Deep-LASI
  `…-donc-accc-w.txt` exports, so **no pixel coordinates are present** — the round-trip gap Tether closes by
  re-injecting coordinates as superset metadata.
- `dataset/tMAVEN/` — `@format='tMAVEN'`; `classes` (per-trace integer class), `pre_list` / `post_list` (per-trace
  analysis-window start/end). `pre_list`/`post_list` map onto Tether's `analysis_window`; the integer `classes` map
  onto Tether's free-text per-condition `category` through a stored **integer↔category lookup table** (lossy
  otherwise), and are distinct from Tether's `curation_label` (accept/reject) and `quality_class` (ML output) — see
  §5.1 and the §7.4 reconcile prompt.

The shipped SMD fixtures include small **curated subsets** (`example-data/bla-uckopsb-tbox-video10/video10.hdf5`
holds 4 molecules; `example-data/uckopsb-01ab-smd-video25-28/…hdf5` holds 2) **and a full-population SMD**
(`example-data/tmaven-model/model-source-smd-281mol.hdf5` — **281 molecules**, `dataset/data/raw` = (281, 1700, 2),
with a `dataset/tMAVEN/` `classes` + `pre_list`/`post_list` group). The full-population SMD is the ≥ 50-molecule
consensus / ebFRET parity fixture (NFR-FIXTURES, §9 M6) and is the exact SMD that `tmaven-model/model.hdf5`
(Appendix D.2) was idealized from.

### D.2 Standalone model file — root group `model`

`@type` (e.g. `'vb Consensus HMM'`), `@rate_type='Transition Matrix'`:

- `mean` (state levels; e.g. 4 states `[0.110, 0.428, 0.755, 0.952]`), `var`, `norm_tmatrix` (N×N), `tmatrix`,
  `rates`, `frac` (state populations), `pi`, `nstates`, `dtype` (`b'FRET'`).
- `chain` (`n_mol × n_frames` int state path), `idealized` (`n_mol × n_frames` float FRET), `r`
  (`n_mol × n_frames × n_states`), `ran` (`n_mol`).
- `likelihood` (iter × 5 — ELBO trace), `iteration`; variational params `a`, `b`, `beta`, `E_lnlam`, `E_lnpi`,
  `E_lntm`; and a `priors/` subgroup (`a_prior`, `b_prior`, `beta_prior`, `mu_prior`, `pi_prior`, `tm_prior`).

A model is a standalone, portable artifact spanning many molecules (e.g. 281 in the reference fixture
`example-data/tmaven-model/model.hdf5`). The raw traces this model was idealized from are the **paired** fixture
`example-data/tmaven-model/model-source-smd-281mol.hdf5` (Appendix D.1), enabling an end-to-end SMD → idealize →
compare parity check at M6. It maps onto Tether's `/idealization/{model}` and confirms the load/save-across-files
pattern that the per-condition ML model (§7.5) also follows. Tether's `/idealization/{model}`
layout mirrors the full member set above (including `var`, `tmatrix`, `rates`, `pi`, and `priors/`).

---

## Appendix E — Native extraction specification

This appendix specifies Tether's movie→trace extraction so it faithfully reproduces Deep-LASI's logic
[Wanninger2023], adapted for the two-color single-laser (no ALEX) case. **All Deep-LASI paths are relative to
`deeplasi/functions/`.** Deep-LASI is MATLAB; every stage cites the source `file:lines` it mirrors.

**Input.** Multi-page 16-bit big-endian TIFF, 512×512×1700, dual-view on one chip (G left / R right by default).
Downstream corrections are fixed by Appendix B: leakage α (= Deep-LASI *Beta*) + γ from photobleaching;
δ/direct-excitation (= Deep-LASI *Alpha*) dropped; `E = I_A,corr/(I_A,corr + γ·I_D,corr)`.

**Naming caveat (carried throughout).** Tether leakage **α = Deep-LASI Beta** (donor→acceptor crosstalk); Tether
dropped **δ = Deep-LASI Alpha** (direct acceptor excitation, ALEX-only); Deep-LASI **Gamma = Tether γ**. Factors
below are named by physical meaning.

### Pipeline (ordered)

1. Split geometry · 2. Detection image · 3. Spot detection · 4. Sub-pixel localization + guardrails · 5. PSF +
background aperture masks · 6. Registration: bead control-point detection · 7. Registration: phase-correlation
prealign · 8. Registration: NN pairing within tolerance · 9. Registration: polynomial fit + residual QA ·
10. Map persistence · 11. Cross-view colocalization · 12. Apply map at extraction · 13. Crop box · 14. Per-frame
background · 15. Signal integration (Sum) · 16. Bleach-step detection → active windows · 17. Correction factors
(leakage α, γ) · 18. Trace-wise → global aggregation (median).

### Stage 1 — Split geometry
**Deep-LASI:** Each channel is a `TIRFdata` object with a chip region (Left/Right/Lower/Upper/Full),
Rotation ∈ {0,90,180,270}, and Flip. `processImage` applies the fixed order `imrotate(I,−rot) → flipud/fliplr →
crop`, the same transform on the calibration image and every movie frame.
**Tether:** Per-channel `{crop_rect, rotation_deg, flip_v, flip_h}` applied rotate→flip→crop to both calibration
and movie frames. Default donor = Left `[1,1;512,256]`, acceptor = Right `[1,257;512,512]`. Convert MATLAB 1-based
inclusive bounds to 0-based half-open slices carefully; image geometry and channel identity are file-driven, never
hardcoded.
**Refs:** `gui/GUIchannels.m:164-177`, `tools/processImage.m:1-32`, `classes/TIRFdata.m:118-120,175`.

### Stage 2 — Detection image (moving-average max projection, "Cumulated")
**Deep-LASI:** `cumIMG` reshapes frames into non-overlapping blocks of `MovingAverageWindowSize = 50`, takes each
block's mean, then the per-pixel MAX across blocks, normalized by the global max. Empty ALEX sequence ⇒ continuous-
wave ⇒ uses all frames; falls back to a sum projection if too few frames.
**Tether:** Per half, detection image = max over block-means (block = 50), normalized to [0,1]. Use the same
projection for detection and registration. This suppresses single-frame noise/blinking while keeping spots bright
in ≥ 1 window.
**Refs:** `tools/cumIMG.m:16-65`, `classes/TRACEdata.m:70-74`, `traces/batchExtraction.m:122,147`.

### Stage 3 — Spot detection (à trous wavelet, mode 1 = class default)
**Deep-LASI:** `Wave_Partfind(I, J=6, t, vicinity=true)` — an undecimated starlet transform, separable B3-spline
kernel `[1/16,1/4,3/8,1/4,1/16]` dilated by `2^(i-1)` zeros; per-scale noise `σ = 2·MAD`, hard-threshold;
significance via cumulative product across scales; detection mask = AND of scales 1 and 4 (`bwmorph(...,'clean')`);
`regionprops Centroid`; border removal; vicinity filter (< 8 px ⇒ keep the brightest by 3×3 sum).
**Tether:** A Python à trous/starlet detector: B3-spline kernel with `2^(i-1)` hole dilation, J = 6 scales, per-scale
`σ = 2·MAD` hard threshold, AND of significant pixels at scales 1 & 4, `scipy.ndimage.label` + `center_of_mass`,
8 px min-separation keeping the brightest. Run per half (G, R). (Bandpass mode 3 — `find_part_bpass_sort.m`:
threshold → `bpass(lnoise=1, lobject=9)` → top-(1−t) percentile sort → `imregionalmax` → centroid [Crocker1996] —
is a selectable alternative; ADR-0021.) The multiscale-product wavelet detector follows [Olivo-Marin2002].
**Refs:** `external/Wave_Partfind.m:1-100`, `mapping/findPart.m:18-30`, `classes/TRACERdata.m:62`. Alt:
`mapping/find_part_bpass_sort.m`, `external/bpass.m`.

### Stage 4 — Sub-pixel localization + guardrails
**Deep-LASI:** Mode 1 (default) does NOT use `radialcenter`; it uses the connected-component centroid + a snap:
per spot crop half-width 5, `imgaussfilt(σ=1)`, find the max; if the offset from center is < 3 px, snap to
`round(coord + offset)`. Remove spots closer than `z = ceil(len(MaskBG)/2)` apart and within `z` of the border.
(Modes 3/4 use Parthasarathy radial-symmetry `radialcenter` [Parthasarathy2012] instead.)
**Tether:** Faithful default = centroid + ≤ 3 px max-pixel snap (Gaussian σ = 1), NOT radialcenter; guardrails:
drop spots closer than ~½-mask apart, border margin = mask radius, cap the snap at 3 px. Wavelet-segmentation +
centroid localization follows [Izeddin2012]. Optional accuracy upgrade: implement `radialcenter`
[Parthasarathy2012] in numpy — an opt-in that improves localization accuracy but diverges slightly from
Deep-LASI's numbers.
**Refs:** `mapping/findPart.m:14-16,88-101` (snap block: σ=1 at :92, < 3 px at :96, round at :97; separation/border
filters at :67-77), `external/radialcenter.m:50-167` (alt only).

### Stage 5 — PSF + background aperture masks
**Deep-LASI:** Default `MaskType = 1` (manual `circ` on a 21×21 grid, `MaskOuterSize = 9`). PSF disk =
`circ(19, 0.6)` → radius 3 (29 px). BG ring = `circ(19, 0.35, 0.15)` → inner 6, outer 8 (`6 < dist ≤ 8`, 84 px). A
dead-zone gap `3 < dist ≤ 6` is deliberate so the ring samples true background, not PSF tails. (An autocorrelation-
derived PSF path exists; the fixed disk is the class default.)
**Tether:** A binary circular aperture (≤ 3 px) in a 21×21 grid + a concentric BG annulus inner 6 / outer 8,
keeping the dead-zone gap. Expose disk/ring radii (defaults reproduce Deep-LASI). Same geometry for G and R.
**Refs:** `gui/DataTab/genMask.m:4-5`, `filtering/circ.m:5-32`, `classes/TRACERdata.m:92-100`.

### Stage 6 — Registration: bead control-point detection
**Deep-LASI:** A separate calibration movie (multi-labeled beads / multi-dye origami); `RawMap = mean over stack`;
per channel `findPart(RawMap, …, method=1, refine=true)` (the same wavelet detector as Stage 3); store `fliplr()`
as `MapParticles`.
**Tether:** A bead-centroid detector per half = the Stage 3/4 detector on the temporal mean of the bead stack;
enforce min-separation + border exclusion; be explicit about `[row,col]` vs `[x,y]` (Deep-LASI stores `fliplr` →
`[x,y]`).
**Refs:** `classes/TIRFdata.m:117`, `gui/createMapTab.m:122-124`, `mapping/findPart.m:18-30,67-101`.

### Stage 7 — Registration: phase-correlation prealign (4-DOF similarity)
**Deep-LASI:** `PreMap = imregcorr(RawMap_moving, RawMap_ref, 'similarity')` (translation + rotation + isotropic
scale); reference = `MappingReferenceChannel` (default donor, idx 1); prealign moving control points by
`transformPointsForward`.
**Tether:** A coarse 4-DOF isotropic-similarity prealign via phase correlation
(`skimage.registration.phase_cross_correlation`, or log-polar for rotation + scale) to seed pairing. Default
reference = donor.
**Refs:** `gui/createMapTab.m:100-108`, `mapping/createMapPhaseCorr.m:6-16`.

### Stage 8 — Registration: NN pairing within tolerance
**Deep-LASI:** `findPairs(ref, [prealigned, original], tol)` — per moving point, the NN reference via
`pdist2 'Smallest',1`, keep `dist ≤ tol`; **fit on the ORIGINAL (un-prealigned) moving coords** (prealign only aids
matching). `tol = 2 px` active / 4 px legacy.
**Tether:** Prealign, NN-match with a 2 px gate (up to ~4), but FIT on the original moving coords. Use `cKDTree`;
enforce mutual/unique matches to avoid greedy double-assignment.
**Refs:** `mapping/createMapPhaseCorr.m:16-22` (fit on original at :21), `mapping/findPairs.m:15-24`.

### Stage 9 — Registration: polynomial fit + residual QA
**Deep-LASI:** Stored map = `fitgeotrans(moving, ref, 'polynomial', 2)` forward + inverse; retries degree 3 on
failure; a similarity fallback if that fails. Legacy uses degree 4 and `tol = 4`. Residuals are not computed
numerically — visual overlay only. (The transform is polynomial, despite a tutorial calling it "affine.")
**Tether:** A 2-D polynomial warp via `skimage.transform.PolynomialTransform` (order 2 default, fall back to 3); a
similarity fallback when points < ~6. Store both directions. **Improve on the source: compute a numeric per-point
RMS residual and reject/flag above ~0.5 px** (Deep-LASI only eyeballs it). Avoid degree 4 unless many beads.
**Refs:** `mapping/createMapPhaseCorr.m:20-47`, `mapping/createMap.m:53,57-101` (legacy degree 4 at :57-58, legacy
tol = 4 at :53).

### Stage 10 — Map persistence (`.tmap` analog)
**Deep-LASI:** `.tmap` = a MATLAB `-mat` cell `m{i}` of per-channel structs (MapToReference, MapFromReference,
Crop, Rotation, Flip, WarpImref2D, MapParticles, ChannelColor) with raw images stripped; `.tdat` is the full
session file (distinct).
**Tether:** A map file (JSON/HDF5/npz) per channel: crop rect, rotation, flip, explicit polynomial coefficients
(both directions), output size/reference frame, and provenance (bead file, n control points, RMS residual, app
version). No raw images, no pickled transform objects. Keep map files separate from session files (mirror `.tmap`
vs `.tdat`).
**Refs:** `gui/createMainGui.m:380-449`, `gui/MainGUI/save_load_State.m:33`.

### Stage 11 — Cross-view colocalization / donor↔acceptor pairing
**Deep-LASI:** `findColoc(T, dist)` — warp all spots into the reference frame, compute pairwise distances, per
reference spot take the first other-channel spot with `dist < dist`; keep a molecule only if it has a partner in
EVERY channel. `batchExtraction` calls `findColoc(T, 3)` → 3 px.
**Tether:** (1) Register G↔R once (Stages 6–9). (2) Warp R spots into G coords, match by NN within 3 px (`cKDTree`).
**For single-laser FRET, prefer donor-anchored extraction** (read acceptor intensity at the mapped position
regardless of independent acceptor detection) so dark/low-FRET acceptors are not lost — a deliberate relaxation of
Deep-LASI's "partner in every channel" rule.
**Refs:** `mapping/findColoc.m:4-112` (the "partner in every channel" gate at :110),
`traces/batchExtraction.m:150-154,163-164,182`.

### Stage 12 — Apply map at extraction (coordinate domain, no movie rewarp)
**Deep-LASI:** The map is applied in the coordinate domain — the movie is NOT rewarped for extraction (warp is
display/QA only). Reference (donor) spot positions are transformed into each channel's native coords; apertures are
placed there: `xy_mapped = MapToReference.transformPointsInverse(fliplr(D.Particles))`.
**Tether:** Detect spots once in the donor half, `map.inverse(donor_xy)` → acceptor-half centers, extract I_D and
I_A at paired sub-pixel centers with the same aperture + ring. **Transform coordinates; do NOT resample the movie**
(avoids interpolation bias). Keep `[x,y]` vs `[row,col]` explicit. Reserve warping for QA overlays.
**Refs:** `traces/batchExtraction.m:128,160-164,415-431` (the transform at :421), `classes/TRACERdata.m:65`.

### Stage 13 — Crop box
**Deep-LASI:** Per spot, round the coord and crop a square sub-stack across all frames
`cr = S(x−z:x+z, y−z:y+z, :)`, `z = floor(size(B,1)/2)`; with `MaskOuterSize = 9` ⇒ 21×21 ⇒ `z = 10` ⇒ crop
21×21 × Nframes. Out-of-bounds ⇒ zeros (border spots already removed).
**Tether:** Per spot at integer (row, col), crop 21×21 across all frames; skip spots whose window leaves the frame.
**Refs:** `traces/extractTraces.m:9-25`.

### Stage 14 — Per-frame background
**Deep-LASI:** `Filter = ones(1,1,10)/10` — a 10-frame uniform moving average along time only, replicate-padded;
per frame `bg = bg_avg(:,:,i).*B` (ring), then `bg = mean(bg(bg>0))` — one scalar/pixel per frame.
**Tether:** Per frame, background = the mean of ring pixels after a 10-frame uniform temporal moving-average
(replicate-padded) of the cropped stack → one scalar/frame. Exact mirror.
**Refs:** `traces/extractTracesC.m:13-22`, `traces/extractTraces_Cpp.m:24-29`.

### Stage 15 — Signal integration (Sum / top-hat — the default)
**Deep-LASI:** `ExtractionMethod = 'Sum'` ("Always use Sum!"). Per frame: `psf = RAW_frame .* P` (not
time-smoothed); `TOT = sum(psf)`; `BG = bg_per_pixel · sum(P)`; `I = TOT − BG`. Summed-aperture, not
Gaussian-weighted. Returns `I` (corrected), `BG`, `TOT`.
**Tether:** `I_uncorr = Σ raw pixels in the PSF disk`; `I_corr = I_uncorr − bg_per_pixel · N_psf` (`N_psf` = 29).
Store both corrected and uncorrected (uncorrected feeds bleach detection). Do NOT Gaussian-weight. Same in both
halves at paired coords.
**Refs:** `traces/extractTracesC.m:20-33`, `classes/TRACERdata.m:38`.

### Stage 16 — Bleach-step detection → active windows
**Deep-LASI:** Primary = a DNN (`predict_trace_categories`, model 2 = 2-color non-ALEX) classifying each frame;
per-channel first-bleach = argmax of the bleach-prob channel; per-frame donor/acceptor active booleans in
`TraceSelection`. A classical alternative `stepFinder(s,'L1',5,1)` (single-step) feeds `autoCategorization`. The
`stepFinder`/`autoCategorization` internals are an external toolbox not present in the reference clone (only
compiled `.mexw64` binaries exist).
**Tether:** Replicate the output contract, not the DNN: per molecule, donor & acceptor first-bleach frames +
per-frame active masks. Tether ships a **native reimplementation of tMAVEN's Bayesian single-step photobleaching
model** [Verma2024] (`tmaven/tmaven/controllers/photobleaching/photobleaching.py` — a signal→N(0) changepoint with
conjugate Normal-inverse-Gamma priors and marginal-likelihood model selection; `get_point_pbtime` for one trace,
`pb_ensemble` the empirical-Bayes population variant sharing a bleaching-rate constant), run **independently on the
donor and acceptor channel**: each channel's drop-to-zero is its first-bleach frame, and the acceptor-before-donor
ordering yields both the acceptor-bleaches-first window γ needs and the donor-only window for leakage α. Priors
default to a = b = β = 1, μ = 1000 (tMAVEN's documented defaults); the per-channel frames are validated against the
`.mat` `pacc`/`pdon` ground truth (§9 M3). (Kalafut2008 — a parameter-free *multi-step* BIC detector — is a
classical alternative only; Deep-LASI's `stepFinder(s,'L1',5,1)` penalty of 5 belongs to that L1 method,
`TRACEdata.m:110`, not to Kalafut.)
**Refs:** `deeplearning/predict_trace_categories.m:75-213`, `classes/TRACEdata.m:78-142`;
`tmaven/tmaven/controllers/photobleaching/photobleaching.py`, `photobleaching_controller.py`.

### Stage 17 — Correction factors from bleach steps
**Deep-LASI:**
- **Leakage (= Tether α):** donor-only frames (acceptor bleached); `ct = mean(I_DA)/mean(I_DD)` over that window;
  accept `0 < ct < ct_lim`.
- **γ:** at the acceptor-bleach step (donor rises, acceptor falls). Non-ALEX branch:
  `da_spFRET = mean(da − dd·ct)`; `γ = (da_spFRET − da_accbleached)/(dd_accbleached − dd_spFRET) = ΔI_A/ΔI_D`.
  Accept `0 < γ ≤ γ_lim`, both windows `> min_frames`.
- **Direct excitation (= Tether δ):** both paths need the AA channel ⇒ ALEX-only; set NaN single-laser. Correctly
  dropped.
- **Default gates (from the correction-limit table):** `ct_lim = 1`, `γ_lim = 5`, `min_frames = 20`.
**Tether:** Leakage α: window `[acc_bleach+1 : donor_bleach]`, ≥ ~20 frames, `α = mean(I_A)/mean(I_D)`
(bg-subtracted). γ: leakage-correct both windows, `γ = ΔI_A/ΔI_D` across the acceptor-bleach step; drop the
`de·(da+dd)` term (δ = 0). Reject γ ≤ 0 or > 5. Tighten the leakage ceiling to a configurable physical value
(≈ 0.3 default; Cy3→Cy5 leakage ~0.05–0.2) so outliers do not skew the median [McCann2010] (§11.2).
**Refs:** `deeplearning/deep_autocorrect_2color.m:38-150` (esp. 118-130), `traces/manualCorrectionFactors.m:5-20`
(header), `:46-90`, `:256-323`; gate defaults `gui/TracesTab/createTracesPlotLayout.m:172` (in-scope 2-color branch).

### Stage 18 — Trace-wise → global aggregation (population median)
**Deep-LASI:** Factors per molecule, aggregated globally; stored factors come from the median branch (`g==2`), with
a population-median substitution when a molecule's own factor is NaN. Gates: leakage `0 < · < 1`, γ `0 < · ≤ 5`,
windows `> 20` frames.
**Tether:** Per-molecule α and γ from valid bleach-step windows, gates applied, **population MEDIAN** as the dataset
factor; the per-molecule value retained when valid, the median substituted otherwise. Show mean/median/mode but
default to median. **Note (applied-α scope):** this per-molecule-α retention mirrors Deep-LASI's *storage*; Tether's
*applied* leakage α is the **global** donor-only-sample median, used identically for every FRET molecule (§7.2), so
a single global α — not a per-molecule one — feeds corrected-E and the staleness hash (§5.1 `/idealization`). γ
keeps its per-molecule-with-median-fallback form. Final pass `E = I_A,corr/(I_A,corr + γ·I_D,corr)` with I_A
leakage-corrected.
**Refs:** `deeplearning/deep_autocorrect_2color.m:95-148,243-247`, `traces/manualCorrectionFactors.m:271-272`.

### Notes on faithfulness
- `stepFinder`/`autoCategorization` (classical changepoint math) are an external toolbox not in the reference
  clone — Tether instead ships a native reimplementation of **tMAVEN's Bayesian single-step model** [Verma2024]
  (Stage 16), run per channel: a defensible, parameter-light method (conjugate priors, no ad-hoc penalty).
  Kalafut2008 (parameter-free multi-step BIC) remains a classical alternative reference only.
- The Deep-LASI registration transform is polynomial (degree 2 active / 4 legacy), despite a tutorial calling it
  "affine"; trust the code.
- The DNN bleach classifier is mirrored only at the output-contract level (by design — Tether uses a classical
  detector).
- Wavelet (default) detection uses centroid + 3 px snap, not `radialcenter`. If Tether changes detector, it must
  match that detector's native refinement.

---

## References

Software reference implementations (read-only clones under the reference root; never vendored):

- **Deep-LASI** — `deeplasi/` [Wanninger2023].
- **tMAVEN** — `tmaven/`, pinned at commit `10f4230b6d13c6d2ad67b05d801696b4a40eff4a` [Verma2024].
- **MASH-FRET** — `MASH-FRET/` [Börner2018].

Published literature:

- **[Förster1948]** Förster Th. "Zwischenmolekulare Energiewanderung und Fluoreszenz." *Annalen der Physik*
  437(1–2):55–75 (1948). doi:10.1002/andp.19484370105.
- **[Ha1996]** Ha T, Enderle Th, Ogletree DF, Chemla DS, Selvin PR, Weiss S. "Probing the interaction between two
  single molecules: fluorescence resonance energy transfer between a single donor and a single acceptor." *PNAS*
  93(13):6264–6268 (1996). doi:10.1073/pnas.93.13.6264.
- **[Robertson1977]** Robertson SE. "The probability ranking principle in IR." *Journal of Documentation*
  33(4):294–304 (1977). doi:10.1108/eb026647. (The information-retrieval basis for the FR-ML quality ranker's
  precision@k objective: ranking by probability of relevance is optimal for precision-based retrieval; §7.5, §11.2.)
- **[Axelrod2003]** Axelrod D. "Total internal reflection fluorescence microscopy in cell biology." *Methods in
  Enzymology* 361:1–33 (2003). doi:10.1016/S0076-6879(03)61003-7.
- **[Lee2005]** Lee NK, Kapanidis AN, Wang Y, Michalet X, Mukhopadhyay J, Ebright RH, Weiss S. "Accurate FRET
  measurements within single diffusing biomolecules using alternating-laser excitation." *Biophysical Journal*
  88(4):2939–2953 (2005). doi:10.1529/biophysj.104.054114.
- **[McKinney2006]** McKinney SA, Joo C, Ha T. "Analysis of single-molecule FRET trajectories using hidden Markov
  modeling." *Biophysical Journal* 91(5):1941–1951 (2006). doi:10.1529/biophysj.106.082487.
- **[Roy2008]** Roy R, Hohng S, Ha T. "A practical guide to single-molecule FRET." *Nature Methods* 5(6):507–516
  (2008). doi:10.1038/nmeth.1208.
- **[Bronson2009]** Bronson JE, Fei J, Hofman JM, Gonzalez RL Jr, Wiggins CH. "Learning rates and states from
  biophysical time series: a Bayesian approach to model selection and single-molecule FRET data." *Biophysical
  Journal* 97(12):3196–3205 (2009). doi:10.1016/j.bpj.2009.09.031.
- **[McCann2010]** McCann JJ, Choi UB, Zheng L, Weninger K, Bowen ME. "Optimizing methods to recover absolute FRET
  efficiency from immobilized single molecules." *Biophysical Journal* 99(3):961–970 (2010).
  doi:10.1016/j.bpj.2010.04.063.
- **[vandeMeent2014]** van de Meent J-W, Bronson JE, Wiggins CH, Gonzalez RL Jr. "Empirical Bayes methods enable
  advanced population-level analyses of single-molecule FRET experiments." *Biophysical Journal* 106(6):1327–1337
  (2014). doi:10.1016/j.bpj.2013.12.055.
- **[Hellenkamp2018]** Hellenkamp B, Schmid S, Doroshenko O, et al. "Precision and accuracy of single-molecule FRET
  measurements—a multi-laboratory benchmark study." *Nature Methods* 15(9):669–676 (2018).
  doi:10.1038/s41592-018-0085-0.
- **[Greenfeld2015]** Greenfeld M, van de Meent J-W, Pavlichin DS, Mabuchi H, Wiggins CH, Gonzalez RL Jr, Herschlag
  D. "Single-molecule dataset (SMD): a generalized storage format for raw and processed single-molecule data."
  *BMC Bioinformatics* 16:3 (2015). doi:10.1186/s12859-014-0429-4.
- **[Verma2024]** Verma AR, Ray KK, Bodick M, Kinz-Thompson CD, Gonzalez RL Jr. "Increasing the accuracy of
  single-molecule data analysis using tMAVEN." *Biophysical Journal* 123(17):2765–2780 (2024).
  doi:10.1016/j.bpj.2024.01.022.
- **[Wanninger2023]** Wanninger S, Asadiatouei P, Bohlen J, Salem CB, Tinnefeld P, Ploetz E, Lamb DC. "Deep-LASI:
  deep-learning assisted, single-molecule imaging analysis of multi-color DNA origami structures." *Nature
  Communications* 14:6564 (2023). doi:10.1038/s41467-023-42272-9.
- **[Börner2018]** Börner R, Kowerko D, Hadzic MCAS, König SLB, Ritter M, Sigel RKO. "Simulations of camera-based
  single-molecule fluorescence experiments." *PLoS ONE* 13(4):e0195277 (2018). doi:10.1371/journal.pone.0195277.
- **[Hadzic2018]** Hadzic MCAS, Börner R, König SLB, Kowerko D, Sigel RKO. "Reliable state identification and
  state transition detection in fluorescence intensity-based single-molecule FRET data." *J. Phys. Chem. B*
  122(23):6134–6147 (2018). doi:10.1021/acs.jpcb.7b12483.
- **[Thomsen2020]** Thomsen J, Sletfjerding MB, Jensen SB, et al. "DeepFRET, a software for rapid and automated
  single-molecule FRET data classification using deep learning." *eLife* 9:e60404 (2020). doi:10.7554/eLife.60404.
- **[Götz2022]** Götz M, Barth A, Bohr SS-R, et al. "A blind benchmark of analysis tools to infer kinetic rate
  constants from single-molecule FRET trajectories." *Nature Communications* 13:5402 (2022).
  doi:10.1038/s41467-022-33023-3.
- **[Olivo-Marin2002]** Olivo-Marin J-C. "Extraction of spots in biological images using multiscale products."
  *Pattern Recognition* 35(9):1989–1996 (2002). doi:10.1016/S0031-3203(01)00127-3.
- **[Izeddin2012]** Izeddin I, Boulanger J, Racine V, et al. "Wavelet analysis for single molecule localization
  microscopy." *Optics Express* 20(3):2081–2095 (2012). doi:10.1364/OE.20.002081.
- **[Parthasarathy2012]** Parthasarathy R. "Rapid, accurate particle tracking by calculation of radial symmetry
  centers." *Nature Methods* 9(7):724–726 (2012). doi:10.1038/nmeth.2071.
- **[Crocker1996]** Crocker JC, Grier DG. "Methods of digital video microscopy for colloidal studies." *Journal of
  Colloid and Interface Science* 179(1):298–310 (1996). doi:10.1006/jcis.1996.0217.
- **[Kalafut2008]** Kalafut B, Visscher K. "An objective, model-independent method for detection of non-uniform
  steps in noisy signals." *Computer Physics Communications* 179(10):716–723 (2008). doi:10.1016/j.cpc.2008.06.008.
- **[König2013]** König SLB, Hadzic MCAS, Fiorini E, Börner R, Kowerko D, Blanckenhorn WU, Sigel RKO. "BOBA FRET:
  bootstrap-based analysis of single-molecule FRET data." *PLoS ONE* 8(12):e84157 (2013).
  doi:10.1371/journal.pone.0084157.
- **[Hyndman1996]** Hyndman RJ. "Computing and graphing highest density regions." *The American Statistician*
  50(2):120–126 (1996). doi:10.1080/00031305.1996.10474359.
- **[Hohlbein2014]** Hohlbein J, Craggs TD, Cordes T. "Alternating-laser excitation: single-molecule FRET and
  beyond." *Chemical Society Reviews* 43:1156–1171 (2014). doi:10.1039/C3CS60233H.
- **[Beal2003]** Beal MJ. *Variational Algorithms for Approximate Bayesian Inference.* PhD thesis, Gatsby
  Computational Neuroscience Unit, University College London (2003).
- **[Bishop2006]** Bishop CM. *Pattern Recognition and Machine Learning.* Springer (2006). ISBN 978-0-387-31073-2.
- **[Chen2016]** Chen T, Guestrin C. "XGBoost: A Scalable Tree Boosting System." *Proc. 22nd ACM SIGKDD* 785–794
  (2016). doi:10.1145/2939672.2939785.
- **[Lam2015]** Lam SK, Pitrou A, Seibert S. "Numba: A LLVM-based Python JIT compiler." *Proc. Second Workshop on
  the LLVM Compiler Infrastructure in HPC (LLVM '15)* Article 7 (2015). doi:10.1145/2833157.2833162.

Development standards & conventions (§12):

- **[ConventionalCommits]** *Conventional Commits 1.0.0* — a lightweight convention for commit-message structure
  that maps to SemVer. https://www.conventionalcommits.org/en/v1.0.0/
- **[SemVer]** Preston-Werner T. *Semantic Versioning 2.0.0.* https://semver.org/spec/v2.0.0.html

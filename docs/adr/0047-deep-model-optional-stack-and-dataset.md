# 0047 — Deep-model optional stack (Option A) + torch-free training-dataset substrate

- **Status:** accepted
- **Date:** 2026-07-12
- **Deciders:** bioedca
- **PRD anchor:** §4.1 (technology stack), §7.5 (FR-ML), §9 M8
- **Milestone:** M8

## Context and problem statement

M8 adds a deep trace classifier (1-D CNN/LSTM, DeepFRET/Deep-LASI-style [Thomsen2020],
[Wanninger2023]) trained on the shared `/labels` store. The PRD calls it a **terminal optional
GPU add-on**: "a deep classifier trains on the shared label store and **is optional (CPU base
app unaffected)**" (§9 M8), with the stack noted only as "→ PyTorch (deep, GPU) **later**"
(§4.1). Two things are undecided and load-bearing:

1. **Where does PyTorch live?** It is absent from the whole repo — not in the base `conda-lock`,
   not in the isolated tMAVEN sidecar lock, no pyproject extra. Putting a heavy DL framework in
   the **base** lock would bloat every install and break "optional / CPU base unaffected"; and a
   base-lock bump needs explicit maintainer approval anyway (ADR-0004, [[conda-lock-relock-drift]]).
2. **What does the classifier consume?** It cannot reuse the M5 engineered 9-feature vector; it
   needs the *raw windowed traces* as fixed-length tensors — a preprocessing contract (channels,
   normalization, length, label encoding) that must respect Tether's never-fabricate discipline.

## Decision drivers

- Keep the CPU base app **completely unaffected** by the optional GPU add-on (§9 M8).
- Preserve the load-bearing "**two conda-lock stacks stay isolated**" invariant (PLAN §1.3).
- Never casually bump the base lock (a deliberate, maintainer-approved event — ADR-0004).
- Reuse the M5 label/weight machinery rather than re-deriving it.
- Never fabricate data: undefined values are masked/withheld, not zero-filled-as-real.

## Considered options

- **A. A third isolated `deep/` conda stack** (mirroring the tMAVEN sidecar): `deep/environment.yml`
  + `deep/conda-lock.yml` pinning `pytorch` (CPU build for CI, CUDA build documented for the
  RTX-4060 GPU floor), consumed via a **lazy/guarded** import in a new `tether.ml.deep` subpackage
  so the base app never imports torch; deep tests carry a `@pytest.mark.deep` marker run on a
  **non-required** CI leg (the roadmap PR-2 GPU `workflow_dispatch`).
- **B. An unlocked pip extra** `pip install tether[deep]`. Lighter to introduce, but breaks the
  pin-and-hold invariant (an unlocked extra tracks latest) and torch-CUDA via pip is fragile
  cross-OS.
- **C. Add `pytorch-cpu` to the BASE lock.** Simplest mechanically but wrong: bloats every base
  install with a heavy DL framework and contradicts "optional / CPU base unaffected".

## Decision outcome

**Chosen: Option A** — the third, isolated, optional `deep/` stack (maintainer-approved
2026-07-12). It is the only option honoring both "optional / CPU base unaffected" and "two
conda-lock stacks stay isolated". The `deep/` lock, the torch `Dataset`/`DataLoader`, the model,
and the training loop are the follow-up **PR-1b**; the base app and its `conda-lock` are untouched.

**This PR (PR-1a) is the torch-free substrate only** — `tether.ml.deep.dataset` (pure NumPy) +
its store wrapper `tether.project.deep_dataset.build_deep_dataset`, so it ships in the base env,
runs on the default 3-OS matrix, and de-risks the model PR. Substrate decisions:

- **Channels** default to the *measured* `(donor, acceptor)` background-corrected intensities —
  the DeepFRET non-ALEX input (DD + DA) [Thomsen2020]. A derived FRET-efficiency channel is a
  deliberate PR-1b extension: E = A/(D+A) is undefined where D + A ≈ 0, so baking it into the
  substrate would emit a fabricated value; measured intensities are always defined.
- **Normalization** default `per_trace_total` divides donor **and** acceptor by one per-trace
  scale (the max total intensity D + A), preserving their relative magnitude and hence the
  apparent-FRET ratio E = A/(D + A) — the very signal the classifier learns from. An independent
  per-channel standardization would rescale the two channels by different factors and destroy that
  ratio (the Pearson donor–acceptor correlation is scale-free and survives either scheme, so it is
  not the distinguishing property). `none` leaves raw intensities.
- **Fixed length** `window_length`: a longer trace crops to its leading (pre-bleach,
  information-rich) frames, a shorter one zero-pads, and a boolean **`mask`** marks the real
  observed frames — padding is masked, never zero-filled-as-real (the never-fabricate rule).
- **Labels** are the shared-store **binary** accept(1)/reject(0) curation labels (`CurationLabel`).
  The six-way DeepFRET taxonomy [Thomsen2020] needs the M4 category codec, which does not exist
  yet (ADR-0023 defers category → `/labels`), so the substrate is binary now and extensible later.
- **Reuse, not re-derivation:** the store wrapper takes the ranker's exact labeled set + cold-start
  weights (`weighted_training_set`, ADR-0038: human labels full-weight + provisional priors at
  `w₀/(1 + n_human)`) and joins each row by unique `molecule_id` to the same
  analysis-window-sliced trace the engineered features use (`_windowed_rows`), so a deep window
  equals that molecule's feature window.

### Consequences

- **Good:** the base app / base lock are untouched (schema-guard + conda-lock-verify green — this
  PR is read-only, additive, torch-free); the substrate is unit-testable without any GPU/torch;
  the deep dataset is provably consistent with the M5 ranker (a committed test asserts identical
  ids/labels/weights); the risky torch lock is isolated to PR-1b.
- **Bad / trade-off:** the substrate currently requires `/features/table` to exist (it reads the
  labeled set through the ranker's feature/label join) — a coupling a future refactor can remove
  by extracting the feature-independent label/weight join; binary labels only until M4's category
  codec; `window_length`/`normalization` defaults are best-effort and retuned to the trained model
  in PR-1b.
- **Follow-up:** PR-1b was split in two. **PR-1b-i (this landed the lock):** the isolated `deep/`
  stack — `deep/environment.yml` + `deep/conda-lock.yml` pinning the CPU `pytorch-cpu` metapackage
  (build strings `cpu_mkl*`/`cpu_generic*`, zero CUDA artifacts) across all four platforms, plus
  numpy (bounded to the base `<2.2` window) + scipy + h5py (the empirically verified import footprint
  of `tether.ml.deep.dataset`), wired into the required `conda-lock-verify` check (base + sidecar +
  deep). The deep env was instantiated from the lock and `import torch` + `import tether.ml.deep.dataset`
  verified to co-import. **PR-1b-ii (landed):** the torch `Dataset`/`DataLoader` over `DeepTraceDataset`,
  the 1-D CNN/LSTM, and a headless CPU train-smoke `@pytest.mark.deep` on the new **non-required**
  `deep.yml` CI leg. **PR-2 (landed): the non-required GPU `workflow_dispatch` leg** —
  `.github/workflows/deep-gpu.yml`, dispatched manually onto a self-hosted CUDA runner (`self-hosted` +
  a GPU label), running the same `pytest -m deep tests/test_*_deep.py` as `deep.yml` but exercising the
  `device="cuda"` path (`tests/test_deep_gpu_deep.py`, which self-skips off-GPU). It installs the
  **documented, unpinned** CUDA torch wheel (`pytorch.org/whl/cuXXX`) at run time — deliberately OUTSIDE
  pin-and-hold, acceptable because the leg never gates a merge and the committed `deep/conda-lock.yml`
  stays CPU-only. `tests/test_marker_contract.py` locks the leg's advisory shape (dispatch-only, no
  PR/push trigger; self-hosted; same deep glob). kinSoftChallenge kinetics validation and fine-tuning
  are their own M8 PRs.

## More information

- **New §11.2 tunable:** "Deep-dataset preprocessing (M8)" (window length, normalization, channels,
  train/val split) — registered in PRD §11.2, defaults defined in `tether.ml.deep.dataset`.
- **Packaging (PR-1b-i):** `deep/environment.yml` + `deep/conda-lock.yml` (the third isolated stack),
  verified by the required `conda-lock-verify` CI check (base + sidecar + deep) and `.gitattributes`
  (kept diffable as text). PRD §4.1 names the stack.
- Code: `src/tether/ml/deep/__init__.py`, `src/tether/ml/deep/dataset.py`,
  `src/tether/project/deep_dataset.py`.
- Tests: `tests/test_ml_deep_dataset.py`, `tests/test_project_deep_dataset.py`.
- Related: [ADR-0004](0004-pin-and-hold-dual-lock-isolation.md) (dual-lock isolation),
  [ADR-0023](0023-curation-label-codec-and-labels-log.md) (label codec; category deferred),
  [ADR-0034](0034-gradient-boosting-quality-ranker.md) /
  [ADR-0038](0038-provisional-prior-training-fold.md) (the reused labeled set + weights).

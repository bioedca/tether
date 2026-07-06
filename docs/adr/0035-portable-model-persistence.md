<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0035 — Portable per-condition model artifact: zip + manifest + pickle, format-versioned, warm-start = refit on accumulated labels

- **Status:** accepted
- **Date:** 2026-07-06
- **Deciders:** bioedca (maintainer)
- **PRD anchor:** §7.5 (FR-ML: persistent, portable, incrementally-retrained model), §5.1 (`/models`), UC3, G5, §9 M5
- **Milestone:** M5

## Context and problem statement

PRD §7.5 / UC3 require the quality ranker (ADR-0034) to be **per-condition, persistent, and
portable**: a standalone file that travels with a *condition* (≈100 videos across many days and
experiment files), reloaded and warm-start-retrained video-by-video, **not** trapped inside one
`.tether`. ADR-0034 built the trained scorer and explicitly deferred "persistence as a portable
load/warm-start-retrain/save artifact" to its own PR — this one. What was unhomed: **the on-disk
format**, **what "warm-start-retrain" means for a gradient-boosting model**, and **how a loaded
model stays trustworthy** given that unpickling executes arbitrary code.

Three tensions:

1. **"Warm-start" is ambiguous.** scikit-learn's `warm_start=True` only appends trees to an
   *unchanged* training set; it is *not* an online update on new data. A histogram
   gradient-boosting model has no exact incremental fit on newly arrived labels.
2. **Deserialization is a code-execution surface.** pickle/joblib run arbitrary code on load
   ([sklearn-persistence]); a portable file that moves between machines must not be loadable into
   a misread state, and its provenance must be inspectable before the pickle is executed.
3. **Cross-version portability.** The artifact outlives a single environment; an in-range
   scikit-learn bump must not brick a saved model.

## Decision

**"Warm-start-retrain" = refit on the accumulated label set at the video boundary.** Each boundary
honestly refits the ranker on *every* human label the condition has seen so far (the caller
supplies the accumulated `(X, y)` from `/features` ⋈ `/labels`), not an online tree-append. This is
the field-standard incremental-curation behaviour — a feature-based smFRET trace selector sharpens
and adapts to a lab's preferences as labels accrue [Li2020] — and it keeps the ranker
deterministic and the ADR-0034 never-fabricate / never-auto-drop disciplines intact. The
`warm_start_retrain` result is a **new** immutable model that carries `condition_id` and
`created_utc` forward, appends the `video_id` to a deduplicated `videos_seen`, and increments
`n_retrains`, so the artifact records *what it learned from*.

**On-disk format = a zip container with a plaintext `manifest.json` + a pickled `model.pkl`**
(`MODEL_FORMAT_VERSION = 1`). The manifest holds the format version, the tether/scikit-learn
version stamps, `condition_id`, the feature-name order, the hyperparameters, and the video/retrain
provenance — all as JSON. `load_model` **validates the manifest before unpickling**: an
unsupported `format_version` raises `UnsupportedModelFormatError` without ever touching the pickle,
and after unpickling, the manifest's feature-name schema is cross-checked against the loaded ranker
(a mismatch is a `CorruptModelError`). The pickle uses protocol 5 (efficient for the model's NumPy
arrays). Writes are **atomic** (temp file in the destination dir → `os.replace`), so a reader never
sees a half-written artifact and a crash leaves the previous model intact.

**scikit-learn version mismatch warns, never fails.** A model saved under a different scikit-learn
version still loads; scikit-learn's own `InconsistentVersionWarning` is caught and re-emitted as a
Tether-scoped warning, so the mismatch is visible but an in-range dependency bump does not brick a
saved model.

**Security posture: Tether-owned artifacts only.** Because unpickling runs arbitrary code,
`load_model` is documented as loading trusted Tether-produced files only, never an untrusted file.
The manifest-before-unpickle order limits how far a malformed/incompatible artifact gets, but is
not a sandbox.

## Scope and consequences

- **Store-free and Qt-free; no schema change, no conda-lock change.** The artifact lives in pure
  `tether.ml.persistence` operating on file paths + a `QualityRanker`; it does not touch a
  `.tether` (`schema-guard` green — no HDF5 change at all) and adds no dependency (stdlib
  `pickle`/`zipfile`/`json`; scikit-learn already locked in #92). `import tether.ml` stays
  scikit-learn-free — the sklearn import remains lazy (only `_sklearn_version` / unpickle touch it).
- **`MODEL_FORMAT_VERSION` is a format constant, not a §11.2 tunable** (a schema-style version, like
  the HDF5 schema version — not a user-facing knob), so no §11.2 row is added. The ranker
  hyperparameters remain the single §11.2 "Quality-ranker model" row from ADR-0034.
- **Deferred to later PRs (explicitly out of scope here).** The store-integration layer — the
  `.tether` `/models` **reference** to the external artifact and its **own single-writer
  owner-curator lock** (PRD §5.1 `/models`, §7.10) — is the next M5 PR (the lock machinery already
  exists in `tether.project.lock`). Also still deferred: per-label `source` weighting + cold-start
  decay, the prequential median-across-videos precision@k **uplift ship gate**, cross-condition
  seeding + drift flag + multi-curator merge, and the active-learning badge.

## Alternatives considered

- **scikit-learn `warm_start=True` (append trees).** Rejected: it only adds estimators to the
  *same* training data — it is not an update on the new video's labels, so it would not implement
  UC3's "retrain as labels accumulate." Refit-on-accumulated is the honest semantics.
- **A single joblib/pickle blob (no manifest).** Rejected: reading the provenance (format version,
  feature schema, condition) would require unpickling first — exactly the code-execution surface we
  want to gate. The plaintext manifest is inspectable and validatable before any pickle runs.
- **`skops.io` (safe, no-arbitrary-code loading).** Attractive for the security posture, but not in
  the base lock and it requires manual per-file type approval; deferred rather than adding a
  dependency now. The manifest-before-unpickle discipline + "trusted artifacts only" is the interim
  stance; `skops` remains a future option if models are ever shared across trust boundaries.
- **Store the model inside the `.tether` (`/models` as the artifact, not a reference).** Rejected by
  PRD §7.5/§5.1: the model must be *portable across* experiment files (one condition spans ≈100
  videos in many files), so it is a standalone file the `.tether` only *references*.

## References

- [Li2020] Li, Zhang, Johnson-Buck & Walter. "Automatic classification and segmentation of
  single-molecule fluorescence time traces with deep learning." *Nature Communications* (2020).
- [sklearn-persistence] scikit-learn, "Model persistence."
  https://scikit-learn.org/stable/model_persistence.html

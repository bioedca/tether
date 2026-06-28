# 0014 — Registration map: numeric RMS gate, over-gate flag-don't-drop, and a unified native/imported calibration

- **Status:** accepted
- **Date:** 2026-06-28
- **Deciders:** bioedca
- **PRD anchor:** §7.1 (FR-EXTRACT registration + the over-gate branch), Appendix E Stages 9–10, §9 M1, §11.2 (RMS-residual gate row)
- **Milestone:** M1 (S6 — registration polynomial fit + residual QA + map persistence)

## Context and problem statement

M0.5 S6 landed the registration *primitives* in `tether.imaging.register`: the
degree-2 polynomial transform (`PolyTransform2D`), a normalised least-squares fit
(`fit_polynomial_transform`), the RMS-residual metric (`point_rms`), and the
`.tmap` MCOS decoder (`read_tmap` → `TmapChannel`). Those are pieces, not a
calibration. M1 S6 must turn them into the FR-EXTRACT §7.1 registration contract:

1. **a numeric RMS-residual gate** (PRD §11.2 default ≤ 0.5 px) — Deep-LASI only
   eyeballs the overlay (`createMapPhaseCorr.m`), Tether stores a number;
2. **the over-gate branch** (§7.1) — a *distinct* branch from the fit-*failure*
   ladder: a fit that **succeeds numerically but exceeds the gate** must mark the
   calibration low-confidence and tag every molecule it produces
   `low-confidence-registration`, **never silently drop** them; the action is
   mode-aware (GUI blocking dialog vs headless batch policy);
3. **map persistence** (Stage 10) — coefficients both directions + geometry +
   provenance, no raw images, no pickled transform objects, in a file kept
   separate from the session `.tether`;
4. **support both a native fit *and* an imported `.tmap`** (§7.1, the conjunctive
   §9 M1 deliverable) such that the two are interchangeable at extraction.

The open question this ADR settles: *what object represents a calibration, where
does the RMS gate live, how is the over-gate verdict represented headlessly (with
no GUI yet), and how do the native and imported paths converge?*

## Decision drivers

- **Faithful to §7.1 / Stages 9–10** — numeric RMS, flag-don't-drop, separate map
  file, native-and-imported.
- **`apparent-E never NaN` discipline, applied to registration** — a low-confidence
  registration degrades to *flagged* output, never a dropped molecule or a refused
  movie (the §7.1 "never silently dropped" rule mirrors §7.2's total-correction
  fallback).
- **Headless-first (FR-BATCH §7.11)** — the verdict and its policy must work with
  no GUI; the M2 confirm-dialog is a thin layer over the same verdict.
- **One type for native and imported** — so the §7.1 apply-both parity is a literal
  comparison of two `RegistrationMap`s, not two code paths.
- **Schema freeze intact** — persistence into `/calibration` is additive *data*
  only; the frozen empty container group is untouched (`schema-guard` green).
- **Additive over the M0.5 primitives** — reuse `PolyTransform2D` / `point_rms` /
  `read_tmap`; add an orchestration layer, change none of them.

## Considered options

**Where the RMS gate / over-gate verdict lives.**
- **A. A `RegistrationMap` object that owns the verdict** (`low_confidence` is a
  derived property `rms_residual > gate_px`; `molecule_tags` derives from it).
  Chosen — the verdict travels with the calibration into persistence and into
  extraction, so S8 can tag molecules without re-deriving.
- **B. A bare `(transform, rms)` tuple + ad-hoc gate checks at call sites.**
  Rejected — scatters the §7.1 rule and loses it across persistence.

**Headless over-gate action (§7.1 batch policy).**
- **C. A `warn`/`fail` policy parameter, default `warn` = accept-with-flag + a
  structured `OverGateRegistrationWarning`.** Chosen — exactly the PRD's headless
  default (warn-and-flag, do not abort), with `fail` for the configurable
  fail-the-movie profile. The GUI's `{accept | import .tmap | abort}` dialog (M2)
  is a later layer over the same `low_confidence` verdict.
- **D. Always raise on over-gate.** Rejected — violates "never abort" / "never
  dropped" and is unusable in batch.

**Fewer than six control points (Stage 9 "similarity fallback").**
- **E. Fit a 4-DOF similarity (Umeyama, reflection excluded) and store it as a
  degree-2 polynomial with zero quadratic terms.** Chosen — keeps one uniform
  persisted type; settled linear algebra (no Consensus gate).
- **F. Refuse < 6 points.** Rejected — Stage 9 mandates the fallback.

**Native vs imported convergence.**
- **G. Both paths build the same `RegistrationMap`** (native via
  `fit_registration_map`; imported via `registration_map_from_tmap`, which folds
  the `.tmap`'s 0-based ±1 boundary into the polynomial's normalisation affines so
  the imported transform is a self-contained 0-based map identical in form to a
  native fit). Chosen.

## Decision outcome

Chosen: **A + C + E + G**, landed in a new `tether.imaging.calibrate` module.

- `RegistrationMap` (frozen): both-direction `PolyTransform2D`s, `rms_residual`,
  `n_control_points`, `gate_px`, `degree`, `source ∈ {native, imported}`, optional
  per-channel `ChannelGeometry`, and a `provenance` dict. `low_confidence` =
  *finite* residual strictly above the gate (a non-finite "unknown" residual — an
  imported map with no control points — never trips it). `molecule_tags` =
  `("low-confidence-registration",)` iff low-confidence.
- `fit_registration_map(...)` — degree-2 both directions (similarity fallback for
  2 ≤ N < 6), forward RMS residual at the control points, and the over-gate branch
  (`on_over_gate="warn"` default / `"fail"`).
- `registration_map_from_tmap(...)` — the imported path; `source="imported"`,
  optionally measuring the imported map's residual at supplied control points.
- `save_map` / `load_map` — `.npz` map file (explicit arrays + JSON provenance;
  reloads with `allow_pickle=False`).
- `write_calibration` / `read_calibration` — additive persistence into
  `/calibration/<id>` of a `.tether` (per-direction coefficient sub-groups + scalar
  attrs incl. the `low_confidence` verdict); write-once per id.

**RMS residual is a property of the control points, not a colocalization metric.**
The apply-both parity test fits a native map from the committed `.tdat` colocalized
*FRET-molecule* pairs and compares it to the imported `.tmap`: the two *transforms*
agree to ≤ 0.5 px RMS at the molecule positions, yet *both* fits exceed the 0.5 px
gate **at the molecules** (the molecule pairs carry ~1.6 px biological/colocalization
scatter; bead control points, not FRET molecules, are the precise registration
inputs). This is the gate behaving correctly — a fit to scattered control points is
genuinely low-confidence. Parity is asserted on transform agreement (with the gate
relaxed for that specific transform-agreement check); the 0.5 px gate itself is
exercised on planted-residual fits.

### Deferred (recorded, not silently dropped)

- **The degree-3 polynomial *retry* rung** of the fit-failure ladder (Stage 9:
  "retries degree 3 on failure"). It needs `PolyTransform2D` to become
  degree-aware (a change to a type shared with the `.tmap` decoder), which is a
  separable concern from this PR's gate/persistence/import deliverable. Degree-2 +
  the similarity fallback covers the real-data acceptance (the §9 M1 oracle is
  degree-2). The retry is a small follow-up.
- **Decoding the imported `.tmap`'s per-channel rotation/flip.** The transform
  coefficients + crop suffice for the S6 map and the apply-both parity; rotation/
  flip matter at S7 (apply-map-at-extraction) and are decoded there.

### Consequences

- Good: §7.1 registration is faithfully homed — numeric RMS, flag-don't-drop,
  separate map file, native-and-imported as one comparable type; headless-clean
  with a pluggable batch policy and a clear seam for the M2 GUI dialog; schema
  freeze untouched (verified: writing calibration data leaves `build_manifest`
  identical).
- Trade-off: two deferrals (degree-3 retry, imported rotation/flip), both recorded
  here and scoped to later sessions; neither blocks the §9 M1 acceptance.
- New tunable: the over-gate **batch policy** (warn-and-flag vs fail-movie) is
  registered in PRD §11.2 alongside the existing RMS-gate row.

## More information

PRD §7.1 (over-gate branch), Appendix E Stages 9–10, §9 M1, §11.2 (RMS-residual
gate + over-gate batch policy rows); `deeplasi/functions/mapping/createMapPhaseCorr.m:20-47`,
`createMap.m:53,57-101`; ADR-0012 (registration pairing), ADR-0013 (prealign),
ADR-0003 (apparent-E-never-NaN, the flag-don't-fail sibling); `src/tether/imaging/calibrate.py`,
`tests/test_calibrate.py`.

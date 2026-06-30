# 0019 — `tether extract --tmap`: the imported registration path; trust the bead map (residual unknown), refuse a non-identity rotation/flip, defer the apply + the oracle

- **Status:** accepted
- **Date:** 2026-06-29
- **Deciders:** bioedca
- **PRD anchor:** §7.1 (a native bead/grid fit **and** an imported `.tmap`), §7.11 (headless CLI), FR-EXTRACT; §9 M1; Appendix E Stages 6–10; §11.2
- **Milestone:** M1 (S9 — PR-C1 of the PR-C split: the imported `--tmap` path; the Deep-LASI oracle + M1 close follow in PR-C2)

## Context and problem statement

PRD §7.1 requires extraction to support **both** a native bead/grid fit **and** an
imported Deep-LASI `.tmap`. ADR-0018 (the `tether extract` CLI, PR-B) landed only
the native path and explicitly deferred the imported `--tmap` branch + the
recall/Pearson/RMS acceptance oracle to the S9 follow-up. That follow-up (PR-C)
bundles three distinct concerns — the `--tmap` path, the oracle + `large-fixtures.yml`,
and the M1 close + `v0.1.0` tag — which exceeds one atomic, single-session PR, so
PR-C is split. This ADR settles **PR-C1: wiring the imported `.tmap` path into the
CLI**, which raises three questions:

1. **Residual / over-gate for an imported map.** The native over-gate branch
   (ADR-0014) flags a fit whose RMS residual exceeds the §11.2 gate. A `.tmap` is
   fitted on *bead* control points, which a sample movie does not carry; the only
   residual measurable at extraction time is at colocalized FRET molecules, which
   (the S6 finding) is ~1.6 px of **colocalization scatter**, not registration
   error. Measuring it would wrongly flag every trustworthy imported map.
2. **Channel geometry.** The native path splits the movie into naive L/R halves
   (`--donor-side`). A `.tmap` carries its own per-channel crop geometry, and its
   polynomial is only valid in that frame.
3. **Testability without a committed `.tmap`.** A `.tmap` is an MCOS MAT-file
   impractical to author; there is no committed one (the committed `tmap_coeffs.npz`
   is the *decoded* form). `read_tmap`'s real decode is covered data-present in
   `test_register.py`.

## Decision drivers

- The §7.1 **conjunctive** deliverable: native *and* imported, both yielding the one
  comparable `RegistrationMap` type (ADR-0014 already unified them).
- One concern per PR; `main` stays green (split PR-C; the oracle's real-data leg
  cannot be a committed-data default-CI test — ADR-0018, never-fabricate).
- Honest registration confidence: do not manufacture a low-confidence flag from a
  non-registration signal.
- No raw traceback for operator-actionable failures (the CLI contract, ADR-0018).

## Considered options and decision

- **Trust the imported bead map; leave its residual unknown (NaN).** *Chosen.* With
  no sample control points, `registration_map_from_tmap` returns `rms=NaN` →
  `low_confidence=False` → no flag, no molecule tags (`RegistrationMap` already
  defines a non-finite residual as *not* low-confidence). The bead map *is* the
  precise registration; measuring a molecule-domain residual and gating on it would
  be a category error. (Measure-and-flag was rejected — it would flag every good
  map.)
- **Split + detect at the `.tmap`'s own crop geometry; ignore `--donor-side`;
  refuse a non-identity rotation/flip.** *Chosen.* `registration_map_from_tmap`
  carries each channel's crop into `RegistrationMap.reference_geometry` /
  `moving_geometry`; the imported branch splits with those (donor = reference half,
  acceptor = moving half). `processImage` rotates and flips *before* cropping, but
  the imported path applies only the crop so far. To avoid a silent wrong-frame
  split, `read_tmap` now **decodes** each channel's `Rotation`/`Flip` and the
  imported path **refuses** (a clean `ExtractionError`) any `.tmap` whose channels
  carry a non-identity rotation/flip (`TmapChannel.has_simple_geometry`). The real
  UCKOPSB `.tmap` stores *empty* `Rotation`/`Flip` (a plain L/R split), so it is
  fully supported; the rotation/flip **apply** is deferred to PR-C2 and validated
  against the real movie (ADR-0014).
- **`tmap` is a path parameter on `extract_movie` (`--tmap PATH` on the CLI),
  decoded inside via `read_tmap`.** *Chosen* — symmetric with `movie_path`, and it
  keeps the "never a raw traceback" wrapping in one place (`_imported_registration_map`
  translates any decode/build failure into a `.tmap`-centric `ExtractionError`).
  `tmap` is an input *source*, not a numeric tunable, so it is not an
  `ExtractOptions` field and needs no §11.2 ratification; its provenance
  (`registration_source="imported"`, the `.tmap` filename) is stamped into
  `/settings/extraction`, and the imported map persists to `/calibration` exactly
  like a native fit (ADR-0016).
- **Exercise the imported branch by reconstructing `TmapChannel`s in memory +
  monkeypatching `read_tmap`.** *Chosen* — the MCOS `.tmap` is impractical to author
  and its decode is covered data-present elsewhere; the in-memory channels (the
  exact shape `read_tmap` returns) let the default-CI test assert the full imported
  extraction **and** apply-both parity with the native fit on the same synthetic
  movie.

## Decision outcome

`tether extract <movie> --tmap <map.tmap> -o <out.tether>` decodes the `.tmap` up
front (before any movie IO — a bad map touches nothing), builds an imported
`RegistrationMap`, splits + detects at its channel geometry, then runs the shared
donor-anchored `colocalize` → `extract_molecules` → `write_extraction`.
`registration_source="imported"` and the `.tmap` filename land in
`/settings/extraction`; the imported map persists to `/calibration` like a native
fit. Exit codes follow ADR-0018 (`0` success; `1` for any operator-actionable
`ExtractionError`, including a missing or undecodable `.tmap`). Tested in
`tests/test_extract_cli.py`: a valid imported `.tether`, the trusted-NaN-residual
contract, apply-both parity with the native fit on the same synthetic movie, the
CLI success line, and the missing / undecodable-`.tmap` error paths.

### Consequences

- Good: the §7.1 native-**and**-imported deliverable is complete at the CLI; both
  paths produce the one comparable `RegistrationMap`.
- Good: no schema change (additive data only; `schema-guard` green); no new §11.2
  tunable; `tether --version` stays dependency-light.
- Good: an imported bead map is never spuriously flagged low-confidence.
- Deferred to **PR-C2** (`feat/m1-oracle`): the recall/Pearson/RMS
  extraction-vs-Deep-LASI oracle, `large-fixtures.yml`, the imported `.tmap`
  rotation/flip **apply** (decode + the refuse-non-identity guard land here; only
  the apply, validated on the real movie, is deferred), and the M1 close + `v0.1.0`
  tag.

## More information

PRD §7.1, §7.11, §9 M1, Appendix E Stages 6–10, §11.2; ADR-0014 (RMS gate /
over-gate / unified native-vs-imported map; `registration_map_from_tmap`), ADR-0015
(donor-anchored colocalization), ADR-0016 (the trace store written here), ADR-0018
(the native CLI this extends). `src/tether/project/extract.py`, `src/tether/cli.py`,
`tests/test_extract_cli.py`.

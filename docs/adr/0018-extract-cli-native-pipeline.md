# 0018 — `tether extract` CLI: a native auto-registration pipeline; imported `.tmap` + the Deep-LASI oracle deferred to the S9 follow-up

- **Status:** accepted
- **Date:** 2026-06-29
- **Deciders:** bioedca
- **PRD anchor:** §7.11 (headless CLI), FR-EXTRACT, FR-BATCH; §9 M1; Appendix E (extraction stages); §11.2 (tunables)
- **Milestone:** M1 (S9 — part 2: the `tether extract` CLI; the recall/Pearson/RMS acceptance oracle + M1 close follow)

## Context and problem statement

M1 S9 turns the extraction primitives (split → detect → register → colocalize →
integrate → write) into a headless front door (`tether extract <movie> → .tether`,
PRD §7.11) and validates it against Deep-LASI (recall ≥ 95 % @ 1 px, intensity
Pearson r ≥ 0.99, RMS ≤ 0.5 px; §9 M1, §8 NFR-VALID (a)). S9 was already split:
PR-A landed the Deep-LASI *reader* (ADR-0017). This PR settles the **CLI** itself.
Two questions block a clean, atomic PR:

1. **Where does the CLI get its channel registration?** A sample movie carries no
   bead control points, so a `.tmap` (imported, Deep-LASI's production path) and a
   self-paired native fit are *different* registration sources with different
   inputs.
2. **Can the real acceptance oracle live in this PR?** The recall/Pearson/RMS gate
   needs the **gated full dual-channel UCKOPSB movie + the full ~250-molecule
   Deep-LASI export**; the committed fixtures are a *single-channel* small movie
   (`movie_be_64x64x50.tif`) + a *4-molecule* Deep-LASI slice — too little for a
   95 % recall statistic, and there is no committed `.tmap` for `read_tmap`.

## Decision drivers

- **One concern per PR; `main` stays green** (CLAUDE.md / PLAN §0.2). The oracle's
  real-data leg cannot be a default-CI green test on committed data — bundling it
  would make this PR's CI unable to actually exercise it.
- **Never fabricate reference data** (CLAUDE.md §Data-gaps). A 95 %-recall claim
  must rest on the real movie + real Deep-LASI output, not a synthetic stand-in.
- **A usable, testable command now.** The native path composes existing M1
  primitives end-to-end and is fully exercisable on a synthetic dual-channel movie
  in the default matrix.
- **Honest registration on sample data.** Self-paired native fits on a sample
  movie have larger residuals than bead calibration; the over-gate branch
  (ADR-0014) must flag, not reject.

## Considered options

**Registration source for PR-B.**
- **A. Native auto-registration: detect both halves → phase-correlation prealign
  (`estimate_translation_prealign`, ADR-0012) → mutual-NN `pair_control_points` →
  `fit_registration_map`, `on_over_gate="warn"`.** Chosen. Composes shipped
  primitives; needs no external fixture; honestly flags sample-movie residuals via
  `low-confidence-registration` (ADR-0014) instead of dropping molecules. The
  `--prealign similarity` flag exposes the 4-DOF Fourier-Mellin prealign (ADR-0013)
  for larger warps.
- **B. Require an imported `.tmap` (`registration_map_from_tmap`, ADR-0014).**
  Deferred — it is Deep-LASI's production path and the right one for the oracle, but
  there is **no committed `.tmap`** to exercise `read_tmap` (the committed
  `tmap_coeffs.npz` is the *decoded* form, and a synthetic MCOS `.tmap` is
  impractical to author). It belongs with PR-C, where the real `.tmap`, the gated
  full movie, and the oracle that validates that exact path all come together.

**Acceptance oracle.**
- **C. Defer the recall/Pearson/RMS oracle + `large-fixtures.yml` to PR-C.** Chosen
  — see the data-gap above; PR-C wires the gated full-movie leg (never a required
  check) + a small default-CI leg and closes M1 (tag `v0.1.0`). This PR asserts
  **structure/round-trip** on a synthetic movie, explicitly *not* scientific
  accuracy.

**Code placement.**
- **D. Orchestration in `tether.project.extract.extract_movie`; argparse glue in
  `tether.cli`; heavy imports lazy under the `extract` handler.** Chosen — scope is
  `project` (PR title), the layering `project → imaging → io` stays a DAG, and
  `tether --version` keeps loading only `tether.cli` (no imaging/HDF5 stack). A
  typed `ExtractOptions` records every effective tunable into `/settings/extraction`
  (NFR-REPRO) and leaves room for PR-C's `--tmap` branch without reshaping the call
  site.

## Decision outcome

Chosen **A + C + D**. `tether extract <movie> -o <out.tether>` runs the native
pipeline and writes a fresh project: the registration map is persisted to
`/calibration` (`write_calibration`) and linked from the `/movies` row
(`calibration_id`); molecules, the six `/traces` arrays, `/patches`, and the
`/settings/extraction` provenance are written additively (ADR-0016). The movie's
full SHA-256 (content identity feeding `molecule_key`, §5.1/§7.10) plus
`file_size`/`mtime` populate `MovieMetadata`. Exit codes: `0` success; `1` for any
operator-actionable `ExtractionError` — invalid tunables (validated upfront in
`ExtractOptions`), a missing/unreadable/compressed movie, a pre-existing output
without `--overwrite`, an un-splittable frame, or `< 2` matched control points;
primitive `ValueError`s are translated, so the operator never sees a raw
traceback. The project is built at a sibling temp path and atomically
`os.replace`d into place only on full success, so a failed run leaves no partial
`.tether`. Tested in `tests/test_extract_cli.py` on a synthetic dual-channel
big-endian TIFF (donor/acceptor Gaussians at a known offset): a valid
schema-compatible `.tether`, correct molecule count, donor-coordinate round-trip,
trace/patch shapes, the calibration↔movie link, the apparent-E substrate, the
over-gate flag-don't-drop path, and the error/argparse paths.

### Consequences

- Good: a working, fully-CI-tested `tether extract` lands now; the native path
  exercises every M1 imaging primitive end-to-end.
- Good: no fabricated data — the scientific oracle waits for the real fixtures.
- Good: no schema change (additive data only; `schema-guard` green); no new §11.2
  tunable (the CLI surfaces existing ones and pins their library defaults);
  `tether --version` stays dependency-light.
- Deferred to **PR-C**: the imported `--tmap` registration path, the
  recall/Pearson/RMS extraction-vs-Deep-LASI oracle, `large-fixtures.yml`, and the
  M1 close + `v0.1.0` tag. `donor_side` is a CLI ergonomics flag (which half is the
  donor), not a numeric tunable requiring ratification.

## More information

PRD §7.11, §9 M1, §8 NFR-VALID (a), Appendix E (stages), §11.2; ADR-0012/0013
(prealign + pairing), ADR-0014 (RMS gate / over-gate / native-vs-imported map),
ADR-0015 (donor-anchored colocalization), ADR-0016 (the trace store written here),
ADR-0017 (the Deep-LASI reader the PR-C oracle consumes).
`src/tether/project/extract.py`, `src/tether/cli.py`, `tests/test_extract_cli.py`.

<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0115 — Deep-LASI acquisitions in oracle (a), and how a failure on one is handled

- **Status:** accepted
- **Date:** 2026-11-16
- **Deciders:** bioedca (the acceptance criteria of #715; "Start #715 now", 2026-11-16)
- **PRD anchor:** §9.5 (the validation suite, oracle (a)), §8 NFR-VALID (a), §8 NFR-FIXTURES
- **Milestone:** M19
- **Issue:** [#715](https://github.com/bioedca/tether/issues/715)
- **Builds on:** [ADR-0022](0022-m1-acceptance-reframe-and-close.md) (the oracle (a) limits),
  [ADR-0028](0028-gamma-acceptor-bleach-step-estimator.md) (the `(1 + α)` convention),
  [ADR-0071](0071-private-data-checks-run-from-a-workstation-task.md) (the workstation runner),
  [ADR-0074](0074-matlab-reference-outputs-come-from-pinned-runs.md) (MATLAB reference runs)

## Context and problem statement

Oracle (a) compared Tether's extraction with Deep-LASI on one acquisition, video 010 (`lab-01`),
and its Deep-LASI export came from clicks in Deep-LASI's window, with no record of the settings
or the tool version. The workstation holds Deep-LASI results for more acquisitions of the same
laboratory. Which of them become part of the oracle, where does each reference come from, and
what happens when one of them fails a limit that video 010 meets?

## Decision drivers

- **No limit moves.** The ADR-0022 limits (recall at 2 px ≥ 0.95, donor Pearson ≥ 0.95) stay
  as they are. A new acquisition must not become a reason to change them (AGENTS.md: never
  weaken a frozen oracle to fit an implementation).
- **A reference names its origin.** Each Deep-LASI result needs a record: the tool commit or
  version, the MATLAB release, the settings, the checksums, and, for a result made in the
  window, the date of the file.
- **Nothing private reaches the repository.** Only neutral ids, checksums and numbers that
  Tether computes are committed. Deep-LASI has no licence file, so none of its code is
  committed either (ADR-0074).
- **One bad acquisition must be visible.** A pooled number over ten acquisitions can hide one
  acquisition that fails.

## Considered options

1. **Keep video 010 as the only gated acquisition; record the others as numbers.** Rejected:
   #715 asks for the agreement on more acquisitions as part of the oracle, and a number nothing
   checks does not catch a regression.
2. **Gate the pooled result of all acquisitions.** Rejected: one failing acquisition can pass
   inside a pooled mean (the last driver).
3. **Give each acquisition its own limits, measured on it.** Rejected: that sets limits from the
   implementation under test, which is the weakening the first driver forbids.
4. **Gate each acquisition alone with the unchanged ADR-0022 limits, and report a failure as a
   finding.** Chosen.

## Decision outcome

Chosen option 4.

### The acquisitions

| Check | Neutral ids | Why these |
|---|---|---|
| Spots and traces (oracle (a) scorer, ADR-0022 limits) | `lab-01` (as before, `tests/test_oracle.py`), `lab-02` to `lab-08`, `lab-10`, `lab-12` | movie, map file, saved Deep-LASI state (`.tdat`) and `.mat` export are all on the workstation |
| Corrected FRET (numbers, no limit) | the ids above, `lab-13`, `dlexp-01`, `dlexp-02` | each has a `.mat` export with per-molecule factors `b` and `g` (`lab-07` has `b` only) |
| Scripted rerun of video 010 | `lab-01` | the feasibility case of #715 |

Not used: `lab-09` and `lab-11` have no Deep-LASI export. `lab-13`'s export comes from a movie
of the other export route that is not on the workstation, so its spots cannot be compared. Two
more exports have only the `-donc-accc-w.txt` traces, with no factors and no movie, so no check
can use them. `dlexp-*` ids name exports whose movie is not on the workstation.

`tests/_deeplasi_agreement.py` pins the SHA-256 and size of every Deep-LASI file a check reads.
A changed file fails the check with its id and role, never its name.

### How a failure is handled

- Each acquisition passes or fails alone, at the ADR-0022 limits. The scheduled run reports a
  failure, and the agent session of the runner opens or updates one issue for it with the
  *Validation-oracle failure* form (the procedure of `docs/maintainers/workstation-runner.md`).
- A failure is a finding about Tether, the reference or the acquisition. It is investigated in
  its own issue. It is never a reason to change a limit, to remove an acquisition, or to mark
  the check as an expected failure. A change to the limits or to this list of acquisitions needs
  a separate maintainer decision in its own record.

### Measured on 2026-11-16

The first run on the workstation, from a clone of #1109 at `761c2a8`:

| id | recall at 2 px | donor Pearson | position RMS (px) | result |
|---|---|---|---|---|
| `lab-02` | 0.650 | 1.000 | 0.515 | fails; [#1114](https://github.com/bioedca/tether/issues/1114) |
| `lab-03` | 0.992 | 0.980 | 0.140 | passes |
| `lab-04` | 1.000 | 0.978 | 0.122 | passes |
| `lab-05` | 0.996 | 0.976 | 0.000 | passes |
| `lab-06` | 1.000 | 0.984 | 0.038 | passes |
| `lab-07` | 0.993 | 0.983 | 0.000 | passes |
| `lab-08` | 1.000 | 0.986 | 0.056 | passes |
| `lab-10` | 0.000 | — | — | fails; the movie is turned against its map ([#887](https://github.com/bioedca/tether/issues/887)) |
| `lab-12` | 0.041 | 0.298 | 1.581 | fails; the movie is turned against its map ([#887](https://github.com/bioedca/tether/issues/887)) |

These failures are what the rule above is for: each has its issue, and no limit or id changed.

The corrected-FRET numbers of the comparable exports are on
[#546](https://github.com/bioedca/tether/issues/546), the deferred γ-against-Deep-LASI leg. Seven
exports give no comparison, and the numbers record why: `lab-07` has no finite `g`; for
`lab-08`, `dlexp-01` and `dlexp-02` Tether withholds γ, and for `lab-10`, `lab-12` and `lab-13`
(19 to 49 molecules) it withholds α, by its own rule of too few qualifying traces. An empty
comparison is recorded, not failed. The ADR-0028 identity is asserted wherever the export has
factors.

No file records the Deep-LASI commit that made a window export: at the pinned commit the version
that an export states (`exportedby`) is a constant, so each `window` record says "commit not
recorded".

### Where each reference comes from

- **Window results are frozen with their date.** Each `.mat` export is copied with
  `harness.py freeze` into a `window` folder on the workstation. Its `provenance.json` states
  the method `saved-files`, the checksums, the MATLAB release, and the date of the export file
  itself (`saved`, added to `freeze` by #715). The checks read the copy, verify its record and
  compare its checksum with the pin.
- **Deep-LASI can run without clicks.** `scripts/reference/matlab/deep_lasi.py` (task `extract`)
  runs Deep-LASI at the pinned commit `d2d348cb` of `gitlab.com/simon71/deeplasi` with
  `matlab -batch`. The call sequence is: build the main object (its window is made and hidden at
  once, because Deep-LASI has no entry without one); restore the saved state as Deep-LASI's own
  state loader does; copy the map from the `.tmap`; read the movie and process each channel;
  set the frame range; run Deep-LASI's batch trace extraction, which saves a `.tdat`; and write
  the `.mat` export with Deep-LASI's own exporter. The settings that the run used go into
  `deeplasi_settings.json` beside the outputs. The pinned commit has no writer for the `.txt`
  traces, so a run makes no `.txt`. The window is deleted without its close callback, which
  would write a settings file into the clone; the harness checks the clone again after each run
  and refuses to write a record for a run that changed it.
- **The rerun of video 010 repeats the old export.** Measured 2026-11-16 on the workstation with
  MATLAB R2023b: the same 250 molecules, with a position difference of 0 px and the same raw
  traces (largest relative difference 0). The workstation check asserts this.
- **The step that needs the window is the correction factors.** The old export of video 010
  carries `b` and `g` for its molecules; the rerun carries none (all `NaN`). The old export file
  is dated 96 s after its `.tdat`, so the factors came from a correction step in the window
  after the state was saved. That step probably depends on Deep-LASI's per-frame
  classification, which #715 puts out of scope. The factors of every check therefore come from
  the window exports.

### Corrected FRET: numbers, and one asserted identity

The check starts from the traces of each export and runs Tether's chain: photobleach
detection, the tail leakage α, and the acceptor-bleach γ. It compares Tether's corrected FRET
with Deep-LASI's formula on the export's own factors:
`E = (A − bD) / ((A − bD) + g(1 + b)D)`. Tether's formula with α = `b` and γ = `g(1 + b)` is
the same expression (ADR-0028), and the check asserts that, frame by frame, to rounding
(`PARITY_ATOL` = 1e-9). The agreement numbers compare **molecule means**, not frames: for each
molecule, each tool's corrected FRET is averaged over the same frames (up to Tether's first
bleach of the summed signal), and the two means are compared. A difference that changes sign
within a trace can cancel in a mean. The check records the number of molecules with Deep-LASI
factors, with a Tether γ, and compared, α and the median `b`, the medians of γ and of
`g(1 + b)`, the median and 90th percentile of the absolute difference between the molecule
means, and the Pearson correlation of the molecule means. **No agreement limit is set**: a
limit is a scientific claim, and it waits for a
maintainer decision on the recorded numbers.

On video 010 the two chains differ: α 0.066 against a median `b` of 0.090, γ 2.22 against a
median `g(1 + b)` of 0.62, a median difference of the molecule means of 0.084 (90th percentile
0.25) and a Pearson correlation of the molecule means of 0.10. This is a finding, recorded and
not gated.

**Deep-LASI's own corrected FRET for one-laser data does not use γ.** In the pinned commit,
the two-colour, no-ALEX branch of `calculateTracesFullyCorrected` multiplies the acceptor by
`g` before it forms the ratio, so `g` cancels:
`E = g(A − bD) / (g(A − bD) + g(1 + b)D) = (A − bD) / (A + D)`. On video 010 the `.tdat`'s
corrected FRET equals that expression to 2.2e-16 and differs from the γ-corrected formula by
up to 0.14. The check therefore compares with the γ-corrected formula on Deep-LASI's factors,
which is the quantity ADR-0028 relates to Tether's, and not with the value in Deep-LASI's
window.

### Consequences

- Good: oracle (a) covers ten acquisitions of the laboratory instead of one, each with its own
  pass or fail.
- Good: every Deep-LASI reference has a record, and the window results say so and give their
  date.
- Good: a scripted Deep-LASI run exists and reproduces the old export of video 010 exactly, so
  the spots and traces of that reference have a repeatable origin.
- Bad: the correction factors still come from the window, with no repeatable origin; a
  repeatable origin waits for the classification step that #715 excludes.
- Bad: a failure on a new acquisition shows as a failing weekly check until its cause is found.
  That is the intent: a known failure is tracked in its issue, not hidden.
- Follow-up: #1114 (`lab-02`), #887 (`lab-10`, `lab-12`), and #546 for the corrected-FRET
  disagreement.

## More information

- `tests/test_deeplasi_agreement.py` (the three workstation checks),
  `tests/test_deeplasi_agreement_math.py` (their arithmetic on synthetic data, on every CI
  runner), `tests/_deeplasi_agreement.py` (pins and helpers).
- `scripts/reference/matlab/deep_lasi.py`,
  `scripts/reference/matlab/m/deep_lasi/tether_deeplasi_extract.m`, the `deep-lasi` entry of
  `scripts/reference/matlab/pins.toml`.
- `docs/maintainers/workstation-runner.md` (the roles each id needs in the name table).

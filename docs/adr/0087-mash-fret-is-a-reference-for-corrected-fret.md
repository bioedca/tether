<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0087 — MASH-FRET's published project files are a reference for leakage and corrected FRET

- **Status:** accepted
- **Date:** 2026-11-13
- **Deciders:** bioedca (MASH-FRET as the reference, Q15 A; corrected intensities and FRET with the
  same factors, R3 A; ALEX sets at trace level, R1 A; a committed extract and a weekly full
  check, R9 A — interview of 2026-11-11; the weaker leakage check with a follow-up, decision of
  2026-11-13)
- **PRD anchor:** §7.2 (corrections), §7.4 (FRET efficiency), §2.2 (what the tests can use), §8 NFR-VALID
- **Milestone:** M19
- **Issue:** [#711](https://github.com/bioedca/tether/issues/711)

## Context and problem statement

No Tether test compared a result with MASH-FRET, although Tether cites MASH-FRET's bootstrap
method. Authors of MASH-FRET published nine MASH-FRET project files (`.mash`) with
their group IIB intron study: Zenodo record 24104528, doi:10.5281/zenodo.24104528, CC-BY-4.0,
`02_smFRET.zip`, 236,278,098 B. A project file is a MATLAB v5 file, so `scipy.io.loadmat` reads it
and no MATLAB run is needed. #711 asks for two checks on it: Tether's leakage correction gives
MASH-FRET's cross-talk-corrected intensities, and Tether's γ-corrected FRET gives MASH-FRET's FRET.

Reading all nine files (2026-11-13) showed three facts that decide what such a test can prove:

1. **The leakage coefficient is 0 in every file.** MASH-FRET keeps the bleedthrough coefficients
   in `fix{4}{1}` and the direct-excitation coefficients in `fix{4}{2}`; both are 0 in all nine
   files, and `intensities_crossCorr` equals `intensities_bgCorr` exactly. A comparison at
   coefficient 0 shows that Tether reads the field and leaves the intensities as MASH-FRET does,
   but it cannot catch a sign error.
2. **No FRET value for each frame is stored.** `FRET_DTA` holds the state value of each frame.
   With "recalculate" on (`prmTT{m}{4}{1}(3) = 1`, in every molecule of the record) MASH-FRET sets
   each state value to the mean of its γ-corrected FRET over the frames of that state (`getDiscr.m`,
   `aveStates`). So the strongest FRET reference in the files is that mean, one value for each state.
3. **The files come from several MASH-FRET builds.** The version text of the smallest file is
   "1.1.2 (prev. commit: 1a1f465)". Commit `1a1f465983c59cb513459b10c19474fded317af5` is 393
   commits after the `v1.2.1` tag, so the text "1.1.2" names no release; the commit does. Other
   files name `89deb70`, `e0bc2229`, `5ab2aff0` and `240b0d99`; the last is a MASH-FRET 1.3.4
   file that keeps its settings in a `TP` structure.

The archive is also larger than the 200 MB limit of the `download` tier of
[ADR-0073](0073-one-manifest-for-reference-data-sets.md).

## Decision drivers

- **A reference value comes from the reference tool**, never from a re-implementation of it in the
  test. No value in these tests is computed by Tether-side code that copies MASH-FRET's formula.
- **The convention is the claim.** Tether's γ is defined on the bare donor (ADR-0028); a reference
  is only useful if the mapping between the two conventions is exact and written down.
- **The required matrix makes no network call**, and a committed file is at most 1 MB
  (ADR-0073) and 512 KiB for a plain fixture (`tests/test_fixtures.py`).

## Considered options

1. **Leakage at MASH-FRET's stored coefficient and FRET for each state; a follow-up for a non-zero
   leakage reference (chosen).** The tests prove what the files hold, and the gap is tracked.
2. **Wait for a non-zero reference.** Run MASH-FRET on the workstation (the MATLAB harness of
   [ADR-0074](0074-matlab-reference-outputs-come-from-pinned-runs.md)) with a non-zero coefficient
   first. Rejected for this item: it needs a person at the workstation, and the FRET check, which
   the files do support, would wait for it.
3. **Drop the leakage part.** Rejected: the check at coefficient 0 still pins the reading of
   `fix{4}{1}` and that Tether leaves both channels as MASH-FRET does, at no cost.

## Decision outcome

Chosen option 1. MASH-FRET at commit `1a1f465983c59cb513459b10c19474fded317af5` is a reference for
Tether's leakage correction and γ-corrected FRET, through `tests/test_mashfret_reference.py`:

- **The convention mapping.** For a donor in channel 0 and an acceptor in channel 1:
  - leakage: MASH-FRET subtracts `fix{4}{1}(1,1) · I_D` (bgCorr) from the acceptor on every laser
    (`crossCorr.m`) and leaves the donor unchanged when `fix{4}{1}(2,1)` (acceptor into donor) is 0.
    That is Tether's `I_A,corr = I_A − α·I_D` with `α = fix{4}{1}(1,1)` and the bare donor;
  - γ: MASH-FRET's two-emitter FRET is `1 / (1 + γ·I_D/I_A)` (`buildFretExpr.m`), with γ from
    `prmTT{m}{6}{1}(1,p)`. That is Tether's `I_A / (I_A + γ·I_D)` with the same γ, **no
    conversion** — unlike Deep-LASI's `(1 + α)` donor (ADR-0028). MASH-FRET sets an undefined
    FRET to 0 where Tether gives NaN; no included frame of the record is undefined.
- **The ALEX rule.** The set alternates 532 nm and 638 nm lasers. The tests use only the donor and
  acceptor channels under the donor's laser (PRD §2.2), and they require `fix{4}{2}` (direct
  excitation of the acceptor by the donor laser, an ALEX-only correction that Tether does not
  make) to be 0.
- **What is compared.** From `intensities_bgCorr`, at MASH-FRET's α, Tether's corrected acceptor
  must equal `intensities_crossCorr` (relative limit 1e-15; measured exactly 0). With MASH-FRET's γ,
  the mean of Tether's corrected FRET over the frames of each state must equal that state's value
  in `FRET_DTA` (relative limit 1e-13; measured worst 1.4e-15 on the 34 states of the extract and
  6.3e-14 on the 4442 states of the eight readable files). Background subtraction is not compared:
  the files hold no video, so it cannot be repeated.
- **The data.** The required matrix reads a committed extract of the smallest project file
  (`scripts/make_mashfret_fixture.py`: its first 18 molecules, values unchanged, 498,472 B). The
  archive is a `workstation` entry of the manifest (`$TETHER_PRIVATE_DATA`): the weekly workstation
  run verifies its checksum, makes the extract again and compares it byte for byte, and runs both
  checks on every file this reader can read (eight; the 1.3.4 file is reported, not read).
- **The reader** (`tests/_mashfret.py`) reads only the layout of the generation that wrote these
  files (top-level `fix` and `prmTT`) and raises on another layout, so a later layout is never read
  from the wrong fields.

### Consequences

- Good: Tether's γ convention now has a second, exact, external reference besides Deep-LASI, with
  no conversion factor between the two tools.
- Good: a γ applied to the acceptor, or no γ at all, fails the required matrix (the pull request
  records both demonstrations).
- Bad: a sign error in the leakage correction is **not** caught by this reference, because every
  file of the record has α = 0. The pull request records that demonstration too, and a follow-up
  issue asks for a MASH-FRET reference with a non-zero α.
- Bad: the check is on the mean of each state, not on each frame. A per-frame error that averages
  to zero within every state would pass. The γ-convention errors this guards against do not average
  out.
- Neutral: the full-archive check runs only where the workstation has the archive in
  `$TETHER_PRIVATE_DATA`; it is public data, placed there because it is too large for the
  `download` tier.

## References

- Zelger-Paulus S, Hadzic MCAS, Lindner L, Börner R, Steffen F, Fazliji B, Johannsen S, Sigel R.
  Dataset: "Cis-exons shape the conformational landscape and Mg2+ response of a group IIB intron."
  Zenodo (2026), doi:10.5281/zenodo.24104528, CC-BY-4.0. `02_smFRET.zip` SHA-256
  `0a30d78ab7c67a3bc716afc760fa5f5b7bf54bc60cce5bde8ccac5a41c4605c7`, MD5 (Zenodo)
  `48096e03a7fbef53614f7148da328281`; retrieved 2026-11-13.
- MASH-FRET, <https://github.com/RNA-FRETools/MASH-FRET>, GPL-3.0, commit
  `1a1f465983c59cb513459b10c19474fded317af5` (2020-06-20): `source/mod-trace-processing/
  cross-talk/crossCorr.m`, `FRET/calcFRET.m`, `FRET/buildFretExpr.m`, `dwell-time analysis/
  discrTraces.m` and `getDiscr.m`, `source/project/setDefPrm_traces.m`, and
  `docs/output-files/mash-mash-project.md`. Read, not vendored.
- [ADR-0028](0028-gamma-acceptor-bleach-step-estimator.md) (Tether's bare-donor γ),
  [ADR-0073](0073-one-manifest-for-reference-data-sets.md) (the manifest and its tiers),
  [ADR-0074](0074-matlab-reference-outputs-come-from-pinned-runs.md) (MATLAB runs).

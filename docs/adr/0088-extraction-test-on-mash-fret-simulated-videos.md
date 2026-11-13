<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0088 — An extraction test on videos with known truth from the MASH-FRET simulator

- **Status:** accepted
- **Date:** 2026-11-13
- **Deciders:** bioedca
- **PRD anchor:** §9.5 (the validation suite), §8 NFR-VALID, §8 NFR-FIXTURES
- **Milestone:** M19

## Context and problem statement

Oracle (a) compares Tether's extraction with Deep-LASI on one real movie. That movie has no
truth: it can show that two tools agree, not that either one found the true molecules or the true
intensities. Its headline assertion also runs on no CI runner (§Scope and known limitations of
`docs/validation.md`). Issue [#714](https://github.com/bioedca/tether/issues/714) asks for an
end-to-end extraction test on simulated two-channel videos with known truth, made by an
independent simulator through the MATLAB harness of #700
([ADR-0074](0074-matlab-reference-outputs-come-from-pinned-runs.md)), with limits frozen from the
first measurement, and two fault injections that must each break a limit.

## Decision drivers

- The truth must come from a tool that is not Tether, so that a shared error cannot cancel out.
- Every video, every truth file and every parameter must be frozen with a checksum, and the test
  must run on the required matrix of three operating systems.
- Each file must fit the repository's large-file hook (512 KB for each added file), which is
  stricter than the 1 MB git tier of [ADR-0073](0073-one-manifest-for-reference-data-sets.md).
- The test must be able to fail: a moved aperture and a moved channel mapping must each break a
  frozen limit.

## Considered options

1. **Tether's own synthetic generator (`tests/_synth.py`, #701).** Rejected: it shares Tether's
   conventions for coordinates, PSF and noise, so it cannot find an error in them.
2. **A real movie with a manual truth.** Rejected: a manual truth is a third opinion, not a truth,
   and the movies of oracle (a) are too large for the git tier.
3. **The MASH-FRET simulator at the pin of #700, run once on the maintainer's workstation, with
   the outputs frozen in git.** Chosen.

## Decision outcome

Chosen option 3.

- **The run.** `scripts/reference/matlab/mash_fret_extraction_set.py` holds every parameter and
  seed. The `simulate` task (`m/mash_fret/tether_mash_simulate.m`) drives MASH-FRET v1.3.6
  (`83fcadf3b7274f2eeb22591429202dda0328c05b`) in the order of MASH-FRET's own test routine
  `routinetest_S`: a new simulation project, `getDefault_S` with the set's changes,
  `setDefault_S`, `rng(seed, 'twister')`, generation and export. The maintainer ran it on
  2026-11-13 with MATLAB R2023b Update 10 on Windows 11, at the Tether commit `72ace90`. The
  outputs are commit `7ec756a`.
- **Four scenarios**, each a 160 × 80 px video (two 80 × 80 px channels side by side), 40 frames
  at 10 frames/s, one excitation, two FRET states (0.3 and 0.7, rates of 2 s⁻¹), PSF widths of
  1.28 and 1.40 px, MASH-FRET's "P" camera model (Poisson photoelectrons, offset 113, η = 0.95,
  K = 1, so every pixel is a whole image count), no photobleaching, no bleedthrough, no direct
  excitation and γ = 1:

  | Scenario | Seed | Molecules | Total intensity (photons/frame) | Background (photons/px) |
  |---|---|---|---|---|
  | `easy` | 714001 | 15 | 400 | 3, flat |
  | `low-snr` | 714002 | 15 | 80 | 8, flat |
  | `dense` | 714003 | 60 | 400 | 3, flat |
  | `uneven` | 714004 | 15 | 400 | 12 at the centre, a Gaussian of width 30 px (TIRF profile) |

- **What is frozen.** For each scenario, `tests/fixtures/reference/mash-fret/<scenario>/` holds
  the video (`.sira`, compressed losslessly with gzip, 339–402 KB), the true coordinates
  (`coordinates/<name>.coord`), one trace file for each molecule with its noise-free intensities
  (`traces_ASCII/`), MASH-FRET's parameter log, a note file (below) and `provenance.json`, whose
  SHA-256 values `tests/test_matlab_reference.py` checks. These files are outputs of a pinned run,
  not a downloaded data set, so they have no entry in `tests/reference_data/manifest.toml`: the
  provenance record of ADR-0074 is their manifest, and it holds the checksums, the tool commit
  and the MATLAB release.
- **The conventions.** MASH-FRET's pixel `i` (1-based) spans `(i − 1, i]`, so a MASH-FRET
  coordinate `c` is `c − 0.5` in Tether, and an acceptor `x` is also reduced by the channel width.
  The truth in image counts is η × the noise-free photon count. The test converts the video to the
  uncompressed big-endian `uint16` TIFF that Tether reads, and refuses any pixel that is not a
  whole number in range, so the conversion cannot round.
- **What the test measures.** `tests/test_mash_fret_extraction.py` runs `extract_movie` with every
  default and compares it with the truth. A Tether molecule is a true one when its donor position
  is within 1.5 px. *Eligible* molecules are those whose 21-px aperture fits in the channel. For
  intensity, only molecules with no other true molecule within 10 px count, except in `dense`,
  where crowding is the point: the ratio of Tether's corrected intensity, summed over the video, to
  the true image counts, and the Pearson correlation of the two traces, in each channel.
- **The first measurement**, at `7ec756a`:

  | Scenario | Found / eligible | False | Largest position error donor / acceptor (px) | Median intensity ratio donor / acceptor | Lowest r donor / acceptor |
  |---|---|---|---|---|---|
  | `easy` | 4 / 6 | 0 | 0.477 / 0.416 | 0.917 / 0.913 | 0.895 / 0.827 |
  | `low-snr` | 6 / 8 | 0 | 0.969 / 3.075 | 0.875 / 0.934 | 0.348 / 0.381 |
  | `dense` | 7 / 35 | 1 | 0.984 / 2.067 | 1.078 / 0.731 | 0.432 / 0.467 |
  | `uneven` | 7 / 10 | 0 | 0.645 / 0.645 | 0.965 / 0.892 | 0.831 / 0.701 |

  Every eligible molecule that Tether missed in `easy` and `uneven` has another true molecule
  within 7.4 px, and of each such pair Tether keeps at most one. The ratios are below 1 for an
  isolated molecule because the 3-px disk does not hold the whole PSF; in `dense` the donor ratio
  is above 1 because the disk also holds light from neighbours.
- **The frozen limits** are the measurement with fixed margins, so that a change of the
  floating-point result between platforms cannot fail the test, but a real change does: the found
  count at least, and the false count at most, the measured one; each largest position error the
  measured one + 0.1 px, rounded up to 0.05 px; each median intensity ratio the measured one
  ± 0.03; each lowest correlation the measured one − 0.05, rounded down to 0.05. The limits are in
  `LIMITS` in the test module, and only a new ADR may change them.
- **The demonstrations (#714, third criterion).** Two tests apply a fault to `easy` and require a
  broken limit. With the aperture moved by 2 px in `x`, the donor ratio fell to 0.689 and the
  acceptor ratio to 0.667 (limits 0.887 and 0.883). With the acceptor position of each molecule
  moved by 2 px in `y` after the channel mapping, the largest acceptor error became 2.390 px
  (limit 0.55), and the acceptor ratio fell to 0.660.
- **MATLAB R2023b and MASH-FRET v1.3.6.** MASH-FRET draws its trace plot with the legend property
  `IconColumnWidth`, which MATLAB R2023b does not have. The error comes after the export has
  written every file. The maintainer chose (2026-11-13) to accept exactly that error, only when
  every expected file exists, and to write `<name>_tether_note.txt`, which the record lists like
  any other output. Every other error ends the run.
- **The Windows launcher.** `bin\matlab.exe` returns at once unless it gets `-wait`, and on the
  workstation it sometimes started a second copy of itself and returned even with `-wait`. The
  harness now passes `-wait` on Windows, and `tether_run_job.m` reports the MATLAB release only
  after the task ends without an error, so a run that comes back early writes no record. The
  outputs at `7ec756a` were made before that change of order, through a wrapper that waited for
  the MATLAB process itself; the record names the driver commit `72ace90`.

### Consequences

- Good: the extraction has a test against a truth from an independent simulator that runs on the
  required matrix of every platform, in about a second, and the test fails on a known fault.
- Good: the measurement shows that Tether keeps at most one molecule of a close pair; that is now
  a frozen fact rather than an unknown.
- Bad: the sets are small (40 frames, few isolated molecules), so the intensity limits rest on two
  or three molecules in `easy`, `low-snr` and `uneven`. A larger set would need the LFS tier.
- Bad: a change to Tether's detection, aperture or mapping that improves the result also fails the
  test, and needs a new ADR with a new measurement. That is the intended cost of a frozen oracle.
- Neutral: regenerating the set needs MATLAB and the maintainer's approval of a workstation run.

## References

- Börner, R., Kowerko, D., Hadzic, M. C. A. S., König, S. L. B., Ritter, M. & Sigel, R. K. O.
  (2018). Simulations of camera-based single-molecule fluorescence experiments. *PLoS ONE* 13(4):
  e0195277. doi:10.1371/journal.pone.0195277 (PubMed 29652886, metadata checked 2026-11-13).
- MASH-FRET, `RNA-FRETools/MASH-FRET`, tag v1.3.6, commit
  `83fcadf3b7274f2eeb22591429202dda0328c05b` (GPL-3.0); the camera model and the defaults are
  from its `getDefault_S.m` at that commit.

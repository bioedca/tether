<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0086 — The Ha-laboratory SAM/SAH snapshot histogram is a reference, compared by its peaks

- **Status:** accepted
- **Date:** 2026-11-13
- **Deciders:** bioedca (issue #796 approved on 2026-11-11; the peak tolerance is his to accept on
  the pull request)
- **PRD anchor:** §1 (the apparent-E histogram), §2.2 (N4), §8
  (NFR-FIXTURES, NFR-VALID)
- **Milestone:** M19

## Context and problem statement

The survey of [#721](https://github.com/bioedca/tether/issues/721) found a public set that holds
both raw traces and the output that the laboratory made from them: the ligand-free SAM/SAH
riboswitch snapshots of Fig. 1 of Liao et al., Nucleic Acids Res. 51:8957 (2023),
doi:10.1093/nar/gkad633, deposited as Zenodo 10.5281/zenodo.8088172 (CC-BY-4.0, version 1.1,
2023-06-27). The folder `20200129 RNA only condition/RNA only/` of the Fig. 1 archive holds 53
binary `helN.traces` snapshots of 20 frames, three long movies, the laboratory's 46,950 FRET values
(`frets.dat`), their normalised histogram (`test3.dat`, bin width 0.015, centres -0.4 to 1.4) and
`counts2.txt`, the record that the laboratory's script wrote of its parameters and of its counts
for each movie.

Can Tether's FRET histogram be tested against this output, and how strictly?

## What was found

- **The format.** An int32 frame count, an int16 channel count, then int16 intensities frame by
  frame (`p_nxgn1_ap.pro`, Zenodo 10.5281/zenodo.4925617). The size of all 56 files equals the
  size that the header names. The channel count over two equals the molecule count that
  `counts2.txt` gives for each file.
- **The channel order is donor first.** Two independent evidences. In the IDL source,
  `p_nxgn1_ffp.pro` stores each donor peak of the left half of the frame, then its mapped acceptor
  peak of the right half. In the data, the movies alternate excitation (green frames 1 to 10, red
  frames 11 to 20, as `counts2.txt` states); under red excitation the first channel of each pair
  falls to at most 25 % of its green level in every file, and the second keeps at least 52 %
  (direct excitation of the acceptor). The `.pks` peak lists of the folder (not committed) agree:
  in 23,816 of their 23,836 peak sets the first peak lies in the left half and the second in the
  right half.
- **`test3.dat` is the histogram of `frets.dat`.** MATLAB's `hist(frets, centres) / 46950`
  reproduces every bin to 5.0e-10 (the file writes 8 significant digits).
- **`counts2.txt` accounts for every value.** Its "both" counts over the 56 movies sum to 46,950,
  the length of `frets.dat`, and 2,854 of them come from the three long movies, which are not
  committed.
- **The rule from the traces to `frets.dat` was not found.** The script records leakage 0.12,
  intensity cutoffs 100 and 1000 and a FRET split at 0.5. It counts about four times as many
  "good spots" as there are molecules in each file (1,663 for 437 molecules in `hel1.traces`), and
  `frets.dat` holds runs of about four close values whose means follow the per-molecule FRET of the
  traces. No combination of frames, background and leakage that was tried (contiguous frame
  windows, pairs of frames, background from the red-excitation frames, the three leakage
  conventions) gave the values to the file's precision. The `.smt` files of the folder hold float
  intensities and their own background, which suggests that the script read data that the
  `.traces` files do not carry.

## Considered options

1. **Exact agreement of Tether's values from the traces with `frets.dat`.** Not possible: the rule
   was not found (above).
2. **No test until the rule is found.** Rejected: the histogram and its peaks are the result the
   paper reports, and a peak check on the laboratory's own output is a real reference now.
3. **Two checks (chosen):** an exact check of Tether's histogram on the laboratory's values, and a
   peak check of Tether's histogram on the traces, against a measured tolerance.

## Decision outcome

Chosen option: **option 3**, as #796 item 3 provides for the case where the rule is not found.

- **The data.** 56 files are committed verbatim under `tests/fixtures/ha_sam_sah/`: the 53 short
  snapshots (1,811,598 B), `frets.dat` (845,100 B), `test3.dat` (4,114 B) and `counts2.txt`
  (11,219 B), each a `git`-tier entry of the manifest of ADR-0073 with the archive as its
  `extract_of` and its member path, so the weekly `reference-data` job compares each copy with
  the archive. They keep their CRLF bytes (`-text`), and `frets.dat` is excluded from the 512 KiB
  pre-commit size hook: it is inside the 1 MB limit of the git tier. CC-BY-4.0 through REUSE,
  NOTICE and the fixtures' PROVENANCE. `counts2.txt` is outside the extract that #796 names; it is
  committed because the tests read the parameters from the laboratory's own record instead of
  copying numbers into the code.
- **The reader** lives under `tests/` only (`_ha_traces.py`, PRD N4 and the test-data rules of
  #705). The product gets no `.traces` importer.
- **Exact check.** Tether's `apparent_e_histogram` of `frets.dat` on the laboratory's bins equals
  `test3.dat` to 5e-9, after one exact conversion: MATLAB's `hist` puts the values outside the
  centres' range in the end bins (two below and two above here), and Tether's fixed range drops
  them. No value of `frets.dat` lies on an inner bin edge, so the two edge-closure conventions
  give the same bins; the test requires that.
- **Peak check.** For each molecule, Tether's `corrected_fret` (α = the file's leakage, γ = 1) on
  the mean donor and acceptor over the green frames; a molecule is kept when its green total lies
  between the two cutoffs and its acceptor under red excitation is above the lower one. This is
  Tether's reading of the recorded thresholds, not a reconstruction of the script. The peaks are
  the vertex of a parabola through the largest bin of the 3-bin moving average and its neighbours,
  on each side of the file's FRET split. Measured on 2026-11-13 with 11,316 molecules: Tether 0.3950
  and 0.8663, the laboratory 0.3956 and 0.8669, a difference of 0.0006 at each peak.
  **The tolerance is 0.015, one bin of the laboratory's histogram.** #796 quotes the paper's open
  and closed states as E = 0.4 and 0.84; the paper fits its states, and the test finds histogram
  modes, so those numbers are context, not the gate.
- **The channel order is tested both ways.** A test requires the donor-first behaviour under red
  excitation in all 53 files. With the reader changed to acceptor first, that test and the peak
  test fail (the low peak moves to 0.128 from the laboratory's), and a further test swaps the
  channels inside the comparison and requires a miss of more than five tolerances.

### Consequences

- Good: a public, laboratory-made FRET histogram is compared in the required matrix on three
  operating systems, with no network call.
- Good: the exact check pins Tether's binning against MATLAB's `hist` on 46,950 real values.
- Bad: the peak check is not an exact agreement. A change to Tether's FRET or histogram code that
  moves a peak by less than one bin passes it; the exact check of the binning and the unit tests
  of `corrected_fret` cover that ground.
- Neutral: the three long movies, the `.pma` raw movies of Figs. 2 to 6 and the dwell times are out
  of scope (#796). The second `.traces` variant, with a frame-index row, is #798; it can reuse the
  reader after one change to the header.

## More information

- Issue #796, tracking issue #695, survey #721, manifest ADR-0073, test-data rules #705.
- Liao T-W, Huang L, Wilson TJ, Ganser LR, Lilley DMJ, Ha T. Nucleic Acids Res. 51(17):8957-8969
  (2023), doi:10.1093/nar/gkad633, PMID 37522343, PMC10516623 (metadata checked in PubMed on
  2026-11-13). Data: Zenodo 10.5281/zenodo.8088172 (archive
  SHA-256 `a8c0e41a9c080b39048bcee22503d86bb58d99824aa82c564d08535dbf5422f3`). IDL extraction
  scripts: Zenodo 10.5281/zenodo.4925617 (v1.0.0, archive SHA-256
  `3305db8d459b6f964cd0617c5ed6456585e362e327b364a054d3e2ff7c3471aa`).

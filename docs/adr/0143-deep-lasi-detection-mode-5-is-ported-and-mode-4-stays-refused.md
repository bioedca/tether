<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0143 — Deep-LASI detection mode 5 is ported, and mode 4 stays refused because Deep-LASI cannot run it

- **Status:** accepted
- **Date:** 2026-11-24
- **Deciders:** bioedca (issue #924 approved 2026-11-14, grooming card; the choice between the two
  modes, decision card "Port 5, keep refusing 4", 2026-11-24)
- **PRD anchor:** §7.1 FR-EXTRACT, §11.2 (the particle-detection rows), Appendix E stage 3
- **Milestone:** M10
- **Issue:** [#924](https://github.com/bioedca/tether/issues/924)
- **Amends:** [ADR-0021](0021-particle-detection-modes.md) on the modes a `.tdat` may name; its
  other decisions govern unchanged

## Context and problem statement

Deep-LASI's `findPart` (`functions/mapping/findPart.m`) has five particle-detection modes.
[ADR-0021](0021-particle-detection-modes.md) ported modes 1 (wavelet), 2 (intensity) and 3
(bandpass) and refused a `.tdat` saved with mode 4 (local-variance) or 5 (ZMW intensity). Issue
[#924](https://github.com/bioedca/tether/issues/924) asks for those two modes as options, each with its source in Deep-LASI, a validation against
Deep-LASI on a public movie, the mode recorded with each generation, and no change to the
default.

Before a port can match Deep-LASI, Deep-LASI must produce a result to match. At the pinned commit
`d2d348cb44804f91555aec9c79413c26bf6de045` of `gitlab.com/simon71/deeplasi` (the repository's only
commit, "Reinitialize", 2025-05-10):

- **The window offers modes 1 to 3 only.** The `Rbg_Detection` button group
  (`functions/gui/createDataTab.m:241-279`) has three buttons: "Wavelet (Default)",
  "Intensity Threshold" and "Regional Maxima". Its callback (`:568-569`) maps the selected button
  to 1, 2 or 3. Modes 4 and 5 are reached only when a script sets
  `TRACERdata.ParticleDetectionMode`.
- **Mode 4 cannot run.** Its branch (`findPart.m:32-49`) reads `fI(ty(i)-r:ty(i)+r, …)` before
  `r` has a value: `r` is set only later, inside the `refine` loop (`:89`), and the line that once
  set it before the loop is commented out (`% r = z-1;`, `:85`). MATLAB, which treats `r` as a
  variable of the function because it is assigned there, stops with an error at the first local
  maximum. The branch also multiplies the window by the masks as matrices (`cI*B`, `cI*P`) where
  an element-wise product is evidently meant, and compares a mean-to-variance ratio with a
  constant (`9.3341e-09*2`, "bc of uint16 to double conv") that has no stated source.
- **Mode 5 runs.** Its branch (`findPart.m:50-58`) is the intensity mode with a 3 × 3 disc average
  in place of the Crocker-Grier band-pass: zero the pixels below `t·max`, `imfilter(I,
  fspecial('disk', 1))`, binarize above 3 % of the filtered maximum, `bwmorph(…, 'erode')`,
  `regionprops 'Centroid'`, and then the tail every mode shares.

## Decision drivers

- **Faithfulness** (ADR-0021): a mode is ported only against what Deep-LASI does, and it is
  validated against Deep-LASI's own output.
- **Never fabricate a reference** (AGENTS.md): a result that no run of Deep-LASI can give is not a
  reference.
- **The default and its frozen numbers do not change** (#924).
- **An import never detects silently with a wrong method** (ADR-0021).

## Considered options

- **A. Port mode 5; keep refusing mode 4, and say why.**
- **B. Port mode 4 by its evident intent**: `r = z-1` (the commented-out line), so the window
  matches the masks. It runs, but nothing in Deep-LASI can be compared with it; it is a new
  algorithm under an old name.
- **C. Port neither, and re-scope #924 first.**

## Decision outcome

Chosen option: **A** (the maintainer's choice on the decision card, 2026-11-24).

### Mode 5

`ParticleDetectionMode.ZMW` (`"zmw"`) is the fourth selectable detector, and
`tether.imaging.detect.detect_spots_zmw` is the port. It follows the five steps above in order,
with three details that decide whether it matches:

1. `fspecial('disk', 1)` is the 3 × 3 kernel with corner 0.02508, edge 0.14534 and centre `1/π`,
   the values MATLAB documents to four decimals, here to full precision as Octave's image package
   computes them by the same algorithm.
2. `imfilter`'s defaults are a correlation with zero padding and an output of the input's size;
   the disc is symmetric, so `scipy.ndimage.correlate(…, mode="constant", cval=0)` is the same.
3. MATLAB's binary erosion pads with 1 outside the image, so a spot on the edge is eroded only from
   inside. `scipy.ndimage.binary_erosion` pads with 0 unless `border_value=1` is passed. Mode 2 is
   not affected, because its band-pass leaves a 7-pixel zero border; mode 5 leaves none.

The threshold `t` is the shared `DetectionThreshold` knob. Deep-LASI gives mode 5 no default of its
own, so Tether's default is the intensity mode's 0.5; the minimum separation default is the
intensity mode's 3 px (ADR-0022). The shared Stage-4 tail is unchanged. A `.tdat` saved with mode 5
now decodes to `"zmw"`, and a re-extraction with `--tdat` uses it. The mode is recorded in
`/settings/extraction` and with each trace generation like every other mode, and it can be set
with `tether extract --detection-mode zmw` and from an extraction profile.

### Validation against Deep-LASI

`scripts/reference/matlab/deep_lasi.py` gains a task, `findpart`, that calls Deep-LASI's own
`findPart` in MATLAB with no window (`m/deep_lasi/tether_deeplasi_findpart.m`), and the record of
that run is frozen in `tests/fixtures/reference/deep-lasi/findpart-zmw/` with its provenance
(ADR-0074). Its inputs are committed in `tests/fixtures/deep_lasi_findpart_images/`:

- `public.csv`, a 128 × 128 crop of a detection image that Deep-LASI itself made and saved: the
  first `Cumulated` image of channel 1 in `Figure_2.zip` of Zenodo record 7561162 (Wanninger
  *et al.*, CC-BY-4.0; [Wanninger2023]), made by `scripts/make_deeplasi_findpart_images.py`;
- `synthetic.csv`, seven Gaussian spots, three of them on or next to the image edge.

The run uses thresholds 0.1, 0.3 and 0.5, a background mask of length 1 (so the shared tail's
border width is 1) and no refinement, so what is compared is the mode itself. Tether's own tail
(snap, border margin, minimum separation) is the separate choice of ADR-0022, so
`tests/test_deeplasi_findpart_reference.py` takes Tether's spots before that tail, applies
Deep-LASI's tail with the same border width, and requires every spot to agree within 1e-9 px. A
fault injection, an erosion padded with 0, fails that comparison on the synthetic image.

This is the same kind of evidence the present modes have: Deep-LASI's own output on Deep-LASI's own
data, frozen with its provenance, against Tether's detector (§(a) of the validation page). It is
narrower in one way. The comparison is of detection positions on detection images; it is not a
full extraction of a movie, because Deep-LASI's window cannot select mode 5, so no saved Deep-LASI
state with mode 5 exists to repeat.

Before the MATLAB run, the same task was run on the same inputs in GNU Octave 8.4 with the image
package 2.14 and the statistics package 1.6.3, as an author-side check: all six cases agree with
Tether to 1e-9 px, and an erosion padded with 0 disagrees on four of them. That run is not the
reference.

### Mode 4

Mode 4 stays refused. The refusal now says why: `read_tdat` and `read_detection_settings` raise
`ValueError` naming mode 4, that Deep-LASI's own mode 4 reads a variable that is never set and
stops with an error, and this record. The same frozen run calls mode 4 once and keeps MATLAB's
error message (`mode4_error.txt`), so the reason is itself on record. An out-of-range code is
refused as before. Because the window cannot select mode 4 and the code cannot detect with it, a
`.tdat` naming mode 4 holds no detection made with it.

### Consequences

- Good: a `.tdat` saved with mode 5 opens and re-extracts with its own method, matched to
  Deep-LASI's output on a public detection image.
- Good: the default (`wavelet`) and every frozen number of modes 1 to 3 are unchanged; the CLI,
  the profile and the generation record take the new value through the one tuple
  `tether.extract_choices.DETECTION_MODES`.
- Bad: mode 4 is still not available. If Deep-LASI fixes its mode 4, a new issue can port the fixed
  version against its output.
- Neutral: #924's criterion "a `.tdat` saved with mode 4 or mode 5 decodes" holds for mode 5 only.
  The maintainer edits that criterion on the issue.

## More information

- Deep-LASI source: `gitlab.com/simon71/deeplasi`, commit
  `d2d348cb44804f91555aec9c79413c26bf6de045`, `functions/mapping/findPart.m` (modes 4 and 5:
  lines 32-58; the shared tail: 63-103; `fourierImage`: 107-115) and
  `functions/gui/createDataTab.m:241-279,568-569`. The repository has no licence file, so nothing
  of it is committed; only the numbers its run computed are.
- Data: Zenodo record 7561162, `Figure_2.zip` (SHA-256
  `fccd230c630272503bcf7d3dbe1c7953c10adb9a6d87ba9570d7d1153ec570ee`), CC-BY-4.0.
- Citations:
  - [Wanninger2023] S. Wanninger, P. Asadiatouei, J. Bohlen, C.-B. Salem, P. Tinnefeld,
    E. Ploetz & D. C. Lamb (2023), *Deep-LASI: deep-learning assisted, single-molecule imaging
    analysis of multi-color DNA origami structures*, Nat. Commun. 14:6564,
    doi:10.1038/s41467-023-42272-9.
- Related: [ADR-0021](0021-particle-detection-modes.md) (the selector and modes 1 to 3),
  [ADR-0022](0022-m1-acceptance-reframe-and-close.md) (the per-mode minimum separation),
  [ADR-0074](0074-matlab-reference-outputs-come-from-pinned-runs.md) (the MATLAB harness).

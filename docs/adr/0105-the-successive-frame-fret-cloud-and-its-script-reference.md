<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0105 — The successive-frame FRET cloud, and the laboratory script as its reference

- **Status:** accepted
- **Date:** 2026-11-15
- **Deciders:** bioedca (issue #710 approved; interview of 2026-11-11: Q12 C, Q14 A; batch 4 B4 A;
  batch 5 B2 A; the one MATLAB session approved on the #710 thread 2026-11-15)
- **PRD anchor:** §1 (item 2), §7.7 (analysis plots), §11.2 (tunables), §8 (NFR-VALID)
- **Milestone:** M19

## Context and problem statement

PRD §1 named a "raw successive-frame FRET cloud" as a model-free lens, but the only cloud in
Tether, `tether.analysis.cloud`, plots apparent E against **time**. The laboratory's FRET_cloud
scripts (FRET_cloud scripts, Eduardo Campos Chavez, 2025, unpublished) plot apparent E at frame
*i* against apparent E at frame *i* + 1, with a density, its contours and k-means centroids. A user
of the scripts did not find that plot in Tether, and Tether had no result to compare with them.

The scripts are not public, so no commit can pin them, and the maintainer chose that they stay on
the workstation (B4: A). How is the plot defined in Tether, which of the scripts' choices become
Tether's defaults, and what does the reference test compare when one of the outputs (k-means) is
random?

## Decision drivers

- **One name for each plot** (PRD §1, §7.7): the two clouds are different plots and both stay
  (Q14: A).
- **Agreement with the scripts' cloud** (Q12: C): the defaults are the scripts' values, and the
  test shows the agreement.
- **The scripts stay private** (B4: A): only frozen output enters the repository, with the
  citation and the SHA-256 of each script.
- **A reference is never re-derived by the code it checks**, and no tolerance is set before it is
  measured.
- **Registered tunables and stamped provenance** (AGENTS.md invariants).

## Decision

1. **A new analysis, `tether.analysis.successive`.** `successive_frame_pairs` forms
   `(E[t], E[t + 1])` for each molecule's array, only where both frames are finite: a pair never
   crosses a masked (non-finite) frame and never joins two molecules.
   `population_successive_frame_cloud` cuts each molecule to its analysis window first and leaves
   rejected molecules out by default, as the other population analyses do (PRD §7.5). It is a
   sixteenth population entry point under ADR-0109: `molecule_keys` takes the one selection
   argument, and a test holds it to that contract with the fifteen others. The
   existing time cloud is unchanged, and the PRD calls it the raw FRET cloud "(apparent E against
   time)".
2. **The density, the contours and the centroids are the scripts' method.** A Gaussian product
   kernel with one fixed bandwidth on both axes [Silverman1986], evaluated at the nodes of a square
   grid and normalized to mass 1 on the grid (`f / sum(f) / (dx dy)`); the highest-density-region
   level of each coverage by the grid density-quantile rule that `tether.analysis.cloud` already
   uses [Hyndman1996], [Haselsteiner2017]; k-means with k-means++ starts [Arthur2007] and Lloyd
   iterations [Lloyd1982], the run of least within-cluster sum of squares kept. Every pair counts
   in every estimate, also one outside the grid range; `n_out_of_range` reports them.
3. **The defaults are the scripts' values** and are PRD §11.2 science tunables: a 200 × 200 grid on
   [0, 1], bandwidth 0.05, coverages 10 % to 90 %, 4 centroids, 5 starts. Two are Tether's,
   because the scripts leave them to MATLAB: the seed (0) and the iteration cap (100, MATLAB's
   `MaxIter` default). An empty cluster takes the point farthest from its centre, as MATLAB's
   `EmptyAction` `singleton`. The result carries every value in its `provenance`.
4. **The reference.** `scripts/reference/matlab/fret_cloud.py` runs
   `FRET_cloud_contours_centroids.m` unchanged, one time, with the MATLAB harness of ADR-0074, on
   the 76 smfret-analysis2 test traces of ADR-0084. `pins.toml` pins the script by its SHA-256
   (`kind = "files"`, a new pin kind for a tool with no public commit) and holds the citation;
   the harness refuses another file and writes both into the record. The script reads its files
   through a dialog and column 4 as E, which is zero in these traces, so the task gives it the
   files through a shadow `uigetfile` and a copy of each trace whose column 4 is
   `acceptor / (acceptor + donor)`. The frozen output is
   `tests/fixtures/reference/fret-cloud-scripts/sa2-traces/` (pairs, density, levels, centroids,
   toolbox version, `provenance.json`).
5. **What the test asks** (`tests/test_fret_cloud_reference.py`). Tether is given what the script
   used: the first 51 frames of each trace (the script's `numFramesToUse = 50`, a choice of the
   script and not a Tether default). Then:
   - the 3800 pairs are equal, bit for bit;
   - with MATLAB's kernel cut applied in the test (item 6), the density agrees to 1e-14 of its peak
     (measured 2.5e-15) and Tether's level rule gives the nine levels to 1e-14 relative (2.2e-15);
   - Tether's own density, without the cut, agrees to 1e-3 of the peak (measured 1.1e-4) and its
     levels to 1e-3 relative (3.2e-4);
   - MATLAB's centroids are a fixed point of Tether's Lloyd step (measured difference 0); and
     Tether's best run has MATLAB's within-cluster sum of squares to 1e-6 relative (4.5e-7) and its
     centroids to 1e-3 in E (8.0e-4).

   The centroid limits are not round-off: the two programs draw their k-means++ starts from
   different generators, MATLAB's run ended in a slightly lower local minimum than Tether's with
   seed 0, and k-means has such nearby minima (on these pairs, Tether seeds 4, 6 and 8 of 0 to 9
   end up to 0.003 in E from the other seven). Each limit is the measured difference rounded up
   to the next power of ten.
6. **Tether does not copy MATLAB's kernel cut.** The frozen output shows that `mvksdensity` leaves
   out the part of each pair's kernel that is more than 4 bandwidths from the pair on either
   axis: with that cut the densities agree to round-off, and the 5216 grid nodes where MATLAB's
   density is exactly 0 are exactly the nodes that no cut kernel reaches. The cut is a property of
   MATLAB's implementation, not a choice of the script, and it moves the density by at most 1.1e-4
   of its peak here. Tether keeps the whole Gaussian, and the test applies the cut on its side to
   show that it is the whole difference.
7. **Out of scope, to a follow-up issue:** the GUI entry and the export of the plot (#1085).
   The scripts' heat map, their concatenated FRET-against-time plot and their R² of the centroids
   to the diagonal are not part of this decision.

## Consequences

- A user of the scripts gets their plot from Tether with the same numbers, and the PRD names each
  cloud once.
- A new pin kind lets the harness freeze a private script's output; the record states the citation
  and the script hash, and no line of the script is in the repository.
- The reference proves agreement on one data set and one seed of MATLAB's generator. It does not
  prove that the defaults suit every experiment; they stay tunables.
- The frozen inputs and outputs are GPL-3.0-only, the licence of the smfret-analysis2 traces they
  derive from.

## Alternatives considered

- **Rename the time cloud and give its name to the new plot.** Rejected: it changes an existing
  API and the maintainer kept the present cloud as it is (Q14: A).
- **Silverman's or Scott's bandwidth.** Rejected as the default: the scripts use a fixed 0.05,
  and agreement with them is the requirement (Q12: C). The bandwidth stays a tunable.
- **Copy MATLAB's 4-bandwidth kernel cut.** Rejected: it would make the two densities equal to
  round-off, but it is an artefact of one implementation, it puts exact zeros into the density,
  and the difference it removes (1.1e-4 of the peak here) is below anything the plot shows.
- **Require MATLAB's exact k-means run.** Rejected: it would mean reimplementing MATLAB's random
  generator and its k-means++ draw order, which is not Tether's behaviour and gives nothing a user
  needs. The fixed-point and best-run checks test what the centroids mean.
- **Commit the script.** Rejected by the maintainer (B4: A).

## References

- [Silverman1986], [Hyndman1996], [Haselsteiner2017], [Lloyd1982], [Arthur2007] — see
  `tether.analysis.successive`.
- FRET_cloud scripts, Eduardo Campos Chavez, 2025, unpublished (`FRET_cloud_contours_centroids.m`,
  SHA-256 in `scripts/reference/matlab/pins.toml`).
- ADR-0074 (the MATLAB harness), ADR-0084 (the smfret-analysis2 test traces).

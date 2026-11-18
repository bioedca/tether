<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0127 — The colocalization policy: a partner in both channels by default, or donor-anchored

- **Status:** accepted (card C1, option A, 2026-11-14)
- **Date:** 2026-11-17
- **Deciders:** bioedca (the decision on card C1; the science sign-off on the implementation pull
  request)
- **Supersedes:** the molecule-anchor choice (options A and B) of
  [ADR-0015](0015-donor-anchored-colocalization.md). Its other choices (the coordinate-domain
  map, the crop box shared with the integrator and the strict 3 px gate) stand.
- **PRD anchor:** §7.1 FR-EXTRACT, Appendix E Stage 11, §11.2 (colocalization rows), §5 (schema)
- **Milestone:** M15
- **Issue:** [#920](https://github.com/bioedca/tether/issues/920), plan item E1 of
  [#890](https://github.com/bioedca/tether/issues/890)

## Context and problem statement

ADR-0015 made every in-frame donor spot a molecule (donor-anchored) and turned Deep-LASI's
"partner in every channel" rule (`findColoc.m:110`) into an informational `acceptor_detected`
flag that never dropped a molecule and was never stored. The reason was the dark-acceptor and
low-FRET population: a molecule whose acceptor is too weak to be detected as a spot of its own
is dropped by the partner rule, and that biases a FRET-efficiency histogram toward high values
(Vogel 2012).

The user-facing workflow design (G02 note 03) asks for the opposite default: "The Deep-LASI
partner-in-both rule is the requested default. Donor-anchored extraction remains available for
weak-acceptor / low-FRET candidates. Show candidate-count differences." The maintainer chose that
on 2026-11-14 (card C1, option A).

Which rule does a new extraction use, how is the choice made and stored, what does an existing
project read as, and where are the two counts shown?

## Decision drivers

- Agree with Deep-LASI, the tool the laboratory already uses and the reference Tether is
  validated against, for the molecule set a new user gets without choosing.
- Keep the dark-acceptor population reachable: ADR-0015's reason is still true, so the old rule
  must stay one option away, and the user must see what the default costs before the run.
- PRD §5 and ADR-0005: the schema is additive-only. A project extracted before this change must
  read exactly as it did, with no rewrite.
- AGENTS.md: a frozen scientific reference keeps its numbers, and no tolerance is widened.
- No change of detection: the acceptor spots, the 3 px strict gate and the read position stay.

## Considered options

**The default for a new extraction.**

- **A. Partner in both: keep a donor only when an acceptor spot was detected within
  `coloc_distance` of its mapped position. Donor-anchored is the other value.** Chosen, the
  maintainer's decision.
- **B. Donor-anchored by default (ADR-0015), partner in both as the option.** Rejected by the
  maintainer: a new user would get a different molecule set from Deep-LASI without asking for it.

**Where the acceptor is read under the partner rule.**

- **C. At the mapped donor position, as under donor-anchored.** Chosen. Only the keep/drop
  decision changes, so a molecule kept by both rules has identical traces under both, and the
  partner rule is exactly a subset of the donor-anchored set.
- **D. At the detected acceptor spot.** Rejected: the traces would depend on the rule, and the
  per-donor nearest neighbour is already the partner `findColoc` keeps.

**How the choice is made.** One value, `colocalization`, with the two strings `partner-in-both`
and `donor-anchored`, on `ExtractOptions` (so it is part of the stored extraction profile),
`ExtractRequest`, `BatchRequest`, `tether extract --colocalization` and
`tether batch --colocalization`. An unknown value is refused.

**What is stored.**

- **E. `/molecules/acceptor_detected`, an `int8` column row-aligned with `/molecules/table`
  (`1` detected, `0` not detected, `-1` not recorded), and the rule in each movie's stored
  extraction profile under the key `colocalization`.** Chosen. The column is a new dataset beside
  the table, so the table's dtype does not change, and it is created on the first write that needs
  it with `-1` as its fill value. The rule is a new key in a JSON record that already holds the
  options.
- **F. A new field of the molecule table.** Rejected: a dtype change is not additive (ADR-0005).
- **G. A file-level rule attribute.** Rejected: one project can hold movies extracted under
  different rules (ADR-0117 appends movies), so the rule belongs to each movie.

**What an existing project reads as.**

- **H. A movie record with no `colocalization` key reads as `donor-anchored`; a project with no
  flag column reads every molecule as `-1` (not recorded). Nothing is recomputed or rewritten.**
  Chosen: that is the rule those projects were made with, and recomputing the flag would need the
  movie, which a project need not have.
- **I. Recompute the flag on open.** Rejected: it writes to a project the user only read, and
  the movie may be gone.

**Where the two counts are shown before the run.** The colocalize stage computes both counts
from one detection (the partner count is the number of kept donors with a detected acceptor), so
they cost nothing extra.

- The stage preview (#348) reports `rule_counts` in the colocalize stage outputs and takes the
  rule as a parameter.
- `count_molecules_by_rule()` runs the stages up to colocalize and writes nothing.
- `tether extract --count-only` prints both counts and writes no project.
- Every extraction summary carries both counts, and `tether extract` prints the count of the
  other rule beside the one it kept.
- The extraction page is [#922](https://github.com/bioedca/tether/issues/922) and shows the counts
  from these outputs when it lands.

## Decision outcome

Chosen: **A + C + E + H**, and the counts as above. Every frozen reference made with the
donor-anchored rule names that rule in its test or script and keeps its numbers: the M1 oracle
(`tests/test_oracle.py`, `scripts/run_m1_oracle.py`), the self-drift goldens (ADR-0080,
`tests/_golden.py`), the laboratory goldens (#703, `tests/_lab_golden.py`) and the MASH-FRET
extraction reference (`tests/test_mash_fret_extraction.py`). No reference value and no tolerance
changes.

### The literature for the partner rule

Deep-LASI documents the choice this ADR makes the default: its trace extraction runs "for all
detected molecules, for colocalized molecules only, or for molecules detected in a given detection
channel" (Asadiatouei et al. 2024, the Deep-LASI tutorial in *Biophys. J.*; the software is
described in Wanninger et al. 2023, *Nat. Commun.*). The second source is the Deep-LASI code
itself: `findColoc.m` keeps a molecule only if it has a partner within the gate in every channel
(`deeplasi/functions/mapping/findColoc.m:110`), and `traces/batchExtraction.m:182` extracts those
molecules. Both references were checked with Scite on 2026-11-17: both resolve, and neither
carries an editorial notice.

### Consequences

- Good: a new extraction gives the molecule set a Deep-LASI user expects, and the cost is visible
  before the run in the two counts.
- Cost, as the decision card said: the new default changes the molecule set of every new
  extraction. Molecules with a dark or very weak acceptor, which include the low-FRET population,
  are dropped unless the user selects `donor-anchored` (Vogel 2012). A FRET histogram built from a
  partner-in-both extraction is therefore biased toward high efficiency, and the guide says so.
- The cost has been measured once on real data. On the M1 oracle movie, ADR-0022 found that
  requiring an independently detected acceptor cut the recall of Deep-LASI's curated molecules to
  about 0.66, where the donor-anchored extraction reaches the frozen M1 recall. That oracle keeps
  donor-anchored, and its test says so; it does not measure the new default.
- Old projects open unchanged and say their rule (donor-anchored) and that their flag was not
  recorded. A project that mixes movies extracted under both rules records the rule per movie.
- The flag is now stored, so a downstream filter can use it on a donor-anchored project, and a subset export carries it.
- One new tunable, registered in PRD §11.2 and `docs/reference/parameters.md`.

## More information

PRD §7.1, Appendix E Stage 11, §11.2; ADR-0015 (the record this supersedes in part), ADR-0005
(additive schema), ADR-0117 (appended movies keep their own settings), ADR-0022 and ADR-0080 (the
frozen references). `src/tether/imaging/coloc.py`, `src/tether/extract_choices.py`,
`src/tether/imaging/extract.py` (`read_acceptor_detected`, `read_colocalization_policies`),
`src/tether/project/extract.py` (`count_molecules_by_rule`), `src/tether/project/preview.py`,
`tests/test_coloc.py`, `tests/test_colocalization_policy.py`, `tests/test_extract_preview.py`.

- Asadiatouei, P., Salem, C.-B., Wanninger, S., et al. (2024). Deep-LASI, single-molecule data
  analysis software. *Biophysical Journal*, 123(17), 2682–2695.
  [doi:10.1016/j.bpj.2024.02.013](https://doi.org/10.1016/j.bpj.2024.02.013)
- Wanninger, S., Asadiatouei, P., Bohlen, J., et al. (2023). Deep-LASI: deep-learning assisted,
  single-molecule imaging analysis of multi-color DNA origami structures. *Nature Communications*,
  14. [doi:10.1038/s41467-023-42272-9](https://doi.org/10.1038/s41467-023-42272-9)
- Vogel, S. S., van der Meer, B. W., & Blank, P. S. (2012). The impact of heterogeneity and dark
  acceptor states on FRET: implications for using fluorescent protein donors and acceptors.
  *PLoS ONE*, 7(11), e49593.
  [doi:10.1371/journal.pone.0049593](https://doi.org/10.1371/journal.pone.0049593) (a correction
  was published on 2013-01-14,
  [doi:10.1371/annotation/ea42b98e-12b7-4940-8c4e-6142dedbb997](https://doi.org/10.1371/annotation/ea42b98e-12b7-4940-8c4e-6142dedbb997))

<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# NNNN — Leakage and gamma are estimated per condition inside the project

- **Status:** accepted
- **Date:** 2026-11-27
- **Deciders:** bioedca (usability audit question D2, option A, 2026-11-10; issue #624 approved 2026-11-14)
- **PRD anchor:** §7.2 FR-CORRECT, §5.1 (conditions), §11.2
- **Milestone:** M16
- **Issue:** [#624](https://github.com/bioedca/tether/issues/624)

## Context and problem statement

The leakage pass and the γ pass pooled every examined molecule of a project into one α and one
γ median (`src/tether/project/leakage.py`, `src/tether/project/gamma.py` on `main` at
`68147e5`). That was right while a project held one movie of one condition. Since #347 a project
holds several movies, and by ADR-0068 a project is one experiment that may hold several
conditions. Pooled, a condition's factors were estimated partly on another sample's molecules.

ADR-0068 left one point open for this record: the maintainer's D2 answer put the factors "per
condition inside the project", while PRD §5.1 said that α has "no separate finer 'session'
scope" than the condition. With one project for each day, the first gives a condition that spans
three project files three estimates. Which scope holds, and what does a condition with too few
bleach events get?

## Decision drivers

- **The factors belong to the sample and the optics.** γ depends on the quantum yields of the
  two dyes, their detection efficiencies, the optical alignment and filters, and the solvent
  conditions [Michalet2006]; gamma factors "vary depending on dye labeling scheme and optical
  design" and "should be determined case by case" [Lee2010]. Without the correction, FRET is
  consistent across experiments "only if the photophysical and instrument properties remain
  unchanged" [McCann2010]. A condition is the chemistry/optics tuple of PRD §5.1, laser power
  included, so it is the smallest unit whose molecules share those properties by definition.
- **The estimators are frozen** (ADR-0027, ADR-0028, ADR-0130, ADR-0134). The scope may change
  which molecules reach an estimator, not what the estimator computes or its gates.
- **A one-condition project must not move.** Its stored values stay those of `main`, bit for bit.
- **The project file is the unit of reading and writing** (ADR-0068): a pass holds one file's
  writer lock and reads one file.
- **The schema stays as it is.** The provenance is additive attributes only; no group, dataset or
  `schema_version` change.
- **#926 and #929 come next.** Each condition's factor becomes a record with a source and a
  version; the provenance written here must take those two keys without a new shape.

## Considered options

- **A. Per condition inside the project.** Each pass groups the molecules of the file by
  `condition_id` and runs its estimator once for each group.
- **B. Per condition across project files.** A pass reads every project that holds the
  condition's confirmed key and pools their molecules.
- **C. Per day (per `date` of the condition row) inside the project.**
- **D. Per movie.**
- **E. Pooled over the project, as on `main`.**

## Decision outcome

Chosen option: **A**, the maintainer's D2 decision. It is implemented in
`src/tether/project/_condition_scope.py` (the grouping and the provenance shape),
`src/tether/project/leakage.py`, `src/tether/project/gamma.py` and
`src/tether/project/correct.py`.

E gives one condition a factor measured partly on another sample, which the drivers above rule
out. C and D are finer than the condition. The argument for C is day-to-day alignment drift: γ
depends on the optical alignment [Michalet2006]. It is not adopted as a key, because a project is
one experiment, one session of acquisition (ADR-0068), so its movies of a condition share a
session, and a finer key
divides the bleach events of a condition that already often falls short of
`min_qualifying_traces` (PRD §7.2). D divides them further still, and the per-molecule γ of
ADR-0028 already covers the variation from one molecule to the next. B is the scope PRD §5.1
described, but it needs a pass that reads several files under one writer, finds every file of a
key, and names the files it cannot read (ADR-0068, PRD §5.4); none of that exists, and a factor
that changed whenever another day's file was added would make every stored factor depend on the
files present when it was computed.

### What this closes from ADR-0068

Inside one project file there is **no scope finer than the condition**: no per-movie, per-day or
per-session factor. **Across files, each file keeps its own estimate** for the condition, from the
condition's molecules in that file. Because a file is one experiment (ADR-0068), a condition that
spans three daily files has three estimates, one in each, as the open point foresaw. This record
reads the PRD §5.1 sentence as forbidding a separate session key, not as requiring a pooled
estimate across files, and the sentence is amended to say so. Pooling a condition across files
(option B) is not decided against for ever; it needs its own issue, built on the cross-file
features of ADR-0068.

### The grouping

The group key is `/molecules.condition_id` as stored, and an empty `condition_id` is a group of
its own. A key parsed from a filename is provisional (ADR-0068) and carries no buffer,
temperature or laser power, so two conditions that differ only there share one group until a
person confirms or corrects the key; the how-to says so. The groups are formed from the rows each
pass already examined (a valid `frame_range`; the γ pass's calibration windows of ADR-0156 are
unchanged), and each estimator runs on its group with the same gates. With one group the call is
the one `main` made, so every stored value is the same.

### Too few bleach events in one condition

The §7.2 total-correction-failure rule is evaluated **for each condition**. A condition with fewer
than `min_qualifying_traces` qualifying molecules has its factor withheld: its rows hold the `NaN`
sentinel, its molecules are stamped `apparent-E (corrections unavailable)`, and it borrows no
factor from another condition or from the project. A condition whose α was withheld is not
examined by the γ pass and its γ is withheld with it, while the other conditions are estimated;
the γ pass still refuses when no condition has an α, or when a molecule lacks an α its condition
was given. The recovery is a manual factor for that condition: `compute_corrected_fret(...,
condition_id=...)` applies `alpha_override`/`gamma_override` to the named condition only, and an
override that names no condition is refused when the pass examines more than one.

### The provenance

`/settings/leakage`, `/settings/gamma` and `/settings/correction` each gain two additive
attributes: `conditions`, a JSON list with one object for each condition, and `n_conditions`;
`/settings/correction` also gains `override_condition_id`. Each object names its `condition_id`
and carries that condition's factor (or `null`), its source tag and its molecule counts, so #926
adds `source` details and `version` as keys of the same objects. The existing attributes keep
their meaning for one condition. With several conditions the top-level `alpha` and `gamma` are
`NaN` and `withheld` means withheld in every condition; the counts are sums. A store written
before this record has no `conditions` attribute and reads as one pooled condition.

### Consequences

- Good: a condition's α and γ rest only on its own molecules, and equal what the same code gives
  when the condition is alone in a project (`tests/test_project_condition_factors.py`).
- Good: a one-condition project stores what it stored before; the pooled-estimate test checks it
  exactly, and the existing leakage, γ and correction tests pass unchanged.
- Bad / trade-off: a condition that spans several project files has several estimates, one in each
  file, which can disagree. Each is recorded with its file; a report must say which it used.
- Bad / trade-off: dividing a project's molecules among conditions leaves fewer bleach events
  behind each factor, so more conditions fall to apparent E than a pooled estimate would.
- Bad / trade-off: with several conditions the single project-wide factor that
  `tether.project.correction_states` reads as the proposal of GAMMA_REVIEW is `NaN`, and the
  stored correction state is still one state for the project. A correction state for each
  condition is the "#925 for each condition" of the plan change on #624, not this record.
- Follow-up: `tether batch` prints `α=`/`γ=` from the project-wide summary and runs the γ pass only
  when that summary has one α (`src/tether/project/batch.py`), so a batch project holding several
  conditions skips γ; it writes one project for each movie today, so no batch output is affected
  yet. The γ contributor decision of #927 predicts a withheld γ from the whole table rather than
  from the decided molecule's condition (`src/tether/project/gamma_contributors.py`).
- Gates: `tests/test_project_condition_factors.py`; `tests/test_project_leakage.py`,
  `tests/test_project_gamma.py` and `tests/test_project_correct.py` unchanged.

## More information

- PRD §7.2 (FR-CORRECT and the total-correction-failure path), §5.1 (the condition key), §11.2.
- [ADR-0068](0068-a-project-holds-one-experiment.md) (the project is one experiment; the open point
  this record closes), [ADR-0027](0027-leakage-alpha-tail-estimator.md),
  [ADR-0028](0028-gamma-acceptor-bleach-step-estimator.md),
  [ADR-0156](0156-a-trace-s-calibration-window-is-stored-apart-from-its-analysis-window.md).
- References (retrieved 2026-11-27; Consensus `search` "smFRET correction factors leakage gamma
  depend on dye pair and setup must be determined for each sample measurement", then Scite
  `check_claim` and `verify_papers`, all three verified with no editorial notice):
  - [Lee2010] S. Lee, J. Lee, S. Hohng (2010), *Single-molecule three-color FRET with both
    negligible spectral overlap and long observation time*, PLoS ONE 5(8): e12270.
    [doi:10.1371/journal.pone.0012270](https://doi.org/10.1371/journal.pone.0012270) (CC BY;
    quoted from the methods).
  - [Michalet2006] X. Michalet, S. Weiss, M. Jäger (2006), *Single-molecule fluorescence studies
    of protein folding and conformational dynamics*, Chem. Rev. 106(5): 1785–1813.
    [doi:10.1021/cr0404343](https://doi.org/10.1021/cr0404343) (publisher licence; cited, not
    reproduced).
  - [McCann2010] J. J. McCann et al. (2010), *Optimizing methods to recover absolute FRET
    efficiency from immobilized single molecules*, Biophys. J. 99(3): 961–970.
    [doi:10.1016/j.bpj.2010.04.063](https://doi.org/10.1016/j.bpj.2010.04.063) (already cited by
    PRD §7.2 and ADR-0028; quoted from the abstract).

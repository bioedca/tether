<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0109 — One selection argument and one return shape on the population functions

- **Status:** accepted
- **Date:** 2026-11-15
- **Deciders:** bioedca (the acceptance criteria of #369, approved 2026-11-13; the plan change
  of 2026-11-14 that makes a version of an analysis set the selection, approved again on
  2026-11-14)
- **PRD anchor:** §7.7 FR-ANALYZE (population views), §5.1 (stale idealizations), §7.5 (the
  curation filter)
- **Milestone:** M17
- **Issue:** [#369](https://github.com/bioedca/tether/issues/369)

## Context and problem statement

`tether.analysis` has fifteen population functions. The plot builder (#373) must drive all of
them with one loop: one selection in, one result type out for each function. Four things
stopped that:

1. The selection was not one argument. `per_condition_apparent_e_histograms` selected by
   condition, key, category and tag but not by molecule; `population_model_gaussian_overlay`
   took no selection, no curation filter and no freshness filter.
2. The freshness rule was not one rule. Four of the six functions that read `/idealization`
   cut stale fits; `population_transition_sync_histogram2d` and the overlay did not.
3. `population_dwell_times` returned a mapping, or one analysis when `state` was given.
4. `population_fret_cloud_alpha_shape` returned `None` for a cloud with no 2-D region, so each
   caller needed an `isinstance` check.

The plan change of 2026-11-14 (#890) adds a fifth need: the composition unit of the plot builder
is a frozen version of an analysis set (#934), which spans several projects. The one selection
argument must accept that version. #934 is not built yet, so this record fixes the seam it
plugs into, not the set itself.

## Decision drivers

- **Every frozen oracle stays bit-identical.** A selection may change which molecules enter a
  number, never how the number is computed.
- **Never fabricate.** An empty state is reported as empty, never as a boundary or a curve.
- **One rule, written once.** The freshness rule of the model-derived views lives in one helper.
- **No new public names the builder must special-case.** Fifteen entry points stay fifteen.

## Considered options

- **A. `molecule_keys` takes the selection on all fifteen, and also accepts a protocol object**
  that names its molecules in one project (`MoleculeSelection.molecule_keys_in(project)`).
- **B. A new `selection=` argument beside `molecule_keys`.** Two ways to say one thing; every
  caller must learn which wins.
- **C. Make the analysis-set version a concrete class now**, and type the argument with it. It
  would decide the stored form of #934 before its ADR, in the wrong issue.

For the dwell return: **(i)** always the mapping, `state` narrows it to one key; **(ii)** a
sibling `population_dwell_time(..., state)` returning one analysis — a sixteenth entry point.

For the α-shape: **(a)** return an `AlphaShape` that says "no region"; **(b)** keep `None` and
document it.

## Decision outcome

Chosen: **A**, **(i)** and **(a)**.

- **The selection.** `molecule_keys` on every population function accepts `None`, an iterable of
  `molecule_key` strings, or a `tether.analysis.selection.MoleculeSelection`. `resolve_selection`
  turns all three into `list[str] | None` before the store is read. A bare `str` is refused (it
  would select its characters). The protocol is per project on purpose: the population functions
  read one project, and an analysis set spans several, so a version names, for the project at
  hand, the keys of its members from that project. Pooling across projects stays in #934.
- **One freshness rule.** `tether.analysis._store.fresh_selection` resolves the selection and,
  unless `include_stale`, cuts it to the model's live keys. All six functions that read
  `/idealization` use it, so `population_transition_sync_histogram2d` now excludes stale fits by
  default. The nine that read no idealization take no `include_stale`; a test asserts the
  absence, with the reason `docs/reference/parameters.md` gives.
- **The overlay.** `population_model_gaussian_overlay` gains `molecule_keys`, `include_rejected`
  and `include_stale`. Its curves stay the model's own fitted means, variances and populations
  ([ADR-0042](0042-a1-model-gaussian-overlay.md)); the arguments decide only **whether** it is
  drawn. It is withheld with a `ValueError` — the same way ADR-0042 withholds a model with no
  spread — when no selected, accepted and fresh molecule has an idealized window in the model.
  Re-weighting `frac` by the selection was not chosen: that is a new estimate, a scientific
  choice outside this issue.
- **Per-condition histograms.** `molecule_keys` is ANDed with the condition query; a condition
  with no selected molecule is not overlaid, the rule for a condition the query does not match.
- **Dwell times.** `population_dwell_times` always returns `dict[int, DwellTimeAnalysis]`;
  `state=k` gives the one key `k` (an empty analysis if `k` never dwells).
- **α-shape.** `population_fret_cloud_alpha_shape` always returns an `AlphaShape`.
  `AlphaShape.no_region(n_points)` has `n_triangles == 0`, `alpha` NaN and no edge;
  `spans_region` and `is_empty` tell the three states apart. "No support at this α"
  (`n_triangles > 0`, `n_kept == 0`) is unchanged. The pure `alpha_shape` keeps `None`.

### Consequences

- Good: the plot builder (#373) calls fifteen functions with one selection and reads one type
  from each. An analysis-set version (#934) only has to implement one method.
- Good: every existing oracle test passes unchanged. `tests/test_analysis_selection.py` asserts,
  for all fifteen, that a selection object gives a result bit-identical to its keys as a list,
  and that all keys give what no selection gives.
- Trade-off (breaking, pre-1.0): a caller of `population_dwell_times(..., state=k)` now indexes
  `[k]`; a caller of `population_fret_cloud_alpha_shape` tests `spans_region` instead of
  `is None`. The sync heatmap and the overlay now exclude stale fits by default; a store whose
  every fit is stale gets an empty heatmap and a withheld overlay until `reidealize`.
- Trade-off: two test fixtures wrote fake provenance hashes, so every molecule read as stale.
  They now write the real current hashes, as `tests/_analysis_store.py` already does; their
  assertions are unchanged.
- Follow-up: #934 implements `MoleculeSelection` on a frozen version.

## More information

- Code: `src/tether/analysis/selection.py`, `src/tether/analysis/_store.py`
  (`fresh_selection`), the fifteen entry points in `histogram.py`, `cloud.py`, `crosscorr.py`,
  `anticorrelation.py`, `dwell.py`, `state_number.py`, `tdp.py`, `transition_prob.py`.
- Related: [ADR-0042](0042-a1-model-gaussian-overlay.md) (the overlay is the model's own
  Gaussians), [ADR-0024](0024-idealization-store-layout-staleness-and-nstates.md) (stale fits),
  [ADR-0068](0068-a-project-holds-one-experiment.md) (a project is one experiment), #373,
  #890, #934.

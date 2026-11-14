<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0103 — The dependency graph of a project and the rules that make a result stale

- **Status:** accepted
- **Date:** 2026-11-14
- **Deciders:** bioedca (issue #893 approved 2026-11-14, track A of the workflow plan #890)
- **PRD anchor:** §7.13 (FR-WORKFLOW-6), §5 (data model), §9.1 (load-bearing invariants)
- **Milestone:** M14

## Context and problem statement

The workflow design of 2026-11-14 (`docs/design/`, tracking issue #890) gives the stale rules in
notes. G05 note 03: *“Map/geometry edits invalidate extraction and descendants.
Factor/calibration-window edits invalidate affected corrected data and descendants. Analysis-window
edits invalidate dependent fits/plots. Membership edits invalidate only affected cohort results.”*
G04 note 04 adds that *“stale fits never silently supply current results”*. G02 note 04, the
transitions of G03 and G03 notes 03 and 04 add more events: a new trace generation, a change of the
calibration evidence, an edit of the analysis windows or the curation, a change of the membership
of an analysis set.

Today the store has one stale rule, for idealizations (ADR-0024, ADR-0029). The other rules are not
written. About twenty issues of #890 make or read the records that these rules connect, so two of
them could implement two different rules for the same edge.

Which records depend on which, what event makes each dependent record stale, what a stale record
still permits, and how is the identity of each record calculated?

## Decision drivers

- **One rule for each edge.** The job and dependency coordinator (#895), the stage readiness of
  `tether status` (#896), the correction states (#925) and the analysis sets (#934) must read one
  table, not each carry their own copy.
- **Exactly the dependent results.** FR-WORKFLOW-6: an edit makes stale exactly the results that
  depend on it. A rule that stales too much (a global project version) contradicts G03 note 04,
  which keeps the calibration when only the membership changes; a rule that stales too little lets
  a stale fit supply a current result.
- **No change to the idealization rule.** ADR-0024 and ADR-0029 are the tested stale rule of today,
  with its per-factor scope. The graph must contain it as it is.
- **Additive only.** §9.1 and ADR-0005: after M0 the store changes by additive data only.
- **Qt-free.** The application, the command line and a recipe see the same rules (§7.11).

## Considered options

- **A. One graph of record kinds, with the event, the guard and the diagram block of each edge; a
  record is stale when an identity that it recorded at creation differs from the current one.**
- **B. A rule in each feature**, written where the feature is made (the state of today).
- **C. One project version counter**: any edit makes every result made before it stale.
- **D. Stored marks only**: an edit writes a mark on each dependent record, and nothing computes
  the marks again.

## Decision outcome

Chosen option: **A**.

B is the problem that this record exists to remove. C is simple but wrong: a membership edit would
stale the calibration, against G03 note 04 and FR-WORKFLOW-6, and an unrelated γ-median shift would
stale every idealization, which ADR-0029 rejected for the same reason. D loses a mark when a write
stops between the edit and the mark, and it disagrees with the idealization rule of today, which
computes staleness again from the store each time.

### 1. The graph is a table of record kinds

A node is a *kind* of record, an *instance* is one record, and an edge says that a downstream record
is made from an upstream one. The graph has fifteen nodes and twenty-nine edges, below. It is a
directed graph with no cycle. The Qt-free copy of the same table is
`src/tether/project/dependency_graph.py` (`NODES`, `EDGES`), and `descendants()` gives the transitive
closure. `tests/test_dependency_graph.py` asserts that the two tables of this record and the module
agree cell by cell; a change to one is a change to the other, in the same pull request.

The issue lists fourteen nodes. This record adds **analysis windows** as a node of its own, because
G03 note 03 makes it a separate thing from the calibration windows and G05 note 03 gives its edit a
rule of its own; without the node, the rule “an analysis-window edit stales γ only if the estimator's
inputs changed” has no edge to sit on.

### 2. A record is stale when a recorded input identity differs

Each downstream record records, when it is made, the identity of each direct upstream instance that
it was made from. The record is **stale** when one of those identities differs from the current
identity of that upstream instance, and the descendants of a stale record are stale too.

- The **event** of an edge is the edit that changes the upstream identity. A **guarded** edge applies
  only when its guard holds; the owner of the downstream record evaluates the guard (for example,
  the γ estimator of #928 says whether its inputs changed). `descendants(key, follow_guarded=False)`
  gives what an edit always stales, and the default gives what it can stale.
- The **scope** of a mark is the instances that recorded the changed instance, never every instance
  of the node kind. An α change stales the corrections of its condition only; a membership edit
  stales the fits of that one set only; a γ-median shift stales only the molecules that use the
  fallback (ADR-0029).
- The comparison is the authority. A stored mark (#895) is a cache that can be calculated again from
  the recorded identities; when the two differ, the comparison wins.

### 3. What a stale record permits

For every node: a stale record **stays readable and keeps its mark**; it **never supplies a current
result**; **nothing is deleted**. The last column of the node table adds what a stale record of
each kind still permits. A historical result keeps the inputs that it was made from (G03 note 04),
and the mark says why it is no longer current.

### 4. The identity of a record

| Kind | Rule |
|---|---|
| content hash | SHA-256 over a canonical encoding of the record: a domain prefix with a version (`tether-<node key>-v1`, as `tether-idealization-provenance-v2` does today), JSON objects with sorted keys, floats as `float.hex` with explicit `nan` and `inf` tokens (`_canonical_float`). Two equal records get one identity on all three operating systems. |
| version counter | A counter that starts at 1 in its scope (a project, a condition, an analysis set), goes only up and is never reused. A frozen version is never edited: a change makes a new version. A version also records the content hash of what it froze, so that two copies of a project can be compared. |
| external id | The id that an external system assigns (Zenodo), stored as it is given and bound to the content hash of the manifest. |

**The stamps that exist stay as they are.** A new identity uses an existing stamp as the identity
(`/movies.sha256` for a source movie, `calibration_id` for a map) or folds it in as an input
(`input_trace_hash`, `input_provenance_hash`). No existing stamp gets a new meaning, and no existing
hash is calculated in a new way. The column *Existing stamp* of the node table names each one.

### 5. The idealization rule of today is three edges of the graph

`stale_molecule_keys` (`src/tether/project/idealize.py`) calculates the `input_provenance_hash` of
each fitted molecule again from the current `/traces` and `/molecules` and compares it with the hash
in `/idealization/{model}`. The three inputs of that hash are the upstream identities of three edges
into **Fit**:

- the input-trace identity (`input_trace_hash`) is the **trace generation**, edge 10;
- the window bounds `(pre, post)` are the **analysis windows**, edge 18;
- the effective applied α and γ, with apparent E folded in as α = 0 and γ = 1, are the **correction
  version**, edge 21.

The graph has no edge from **Curation labels** into **Fit**, so a label edit does not make a
project-level idealization stale, as today. The per-factor scope of ADR-0029 is the instance scope
of decision 2. The analysis functions that leave stale molecules out unless `include_stale` is set
(`tether.analysis.dwell`, `state_number`, `transition_prob`) are decision 3. **This record changes no
code of that rule**, and the test asserts the three edges and the missing fourth.

### 6. The store data is additive, and the schema version does not change

This record adds no store structure. The records that the graph needs are written by the issues
that make them, each as additive data in its own pull request: the source registry (#912), trace
generations (#921), the α record, the γ evidence, calibration windows and factor versions
(#926 to #929), the correction states (#925), analysis-set versions (#934), result snapshots
(#943), the package manifest (#951), the deposit record (#957), and the recorded input identities
and stale marks (#895).

**Schema version: no change.** `SCHEMA_VERSION` stays **1**. ADR-0005 and the `schema-guard` gate
accept an additive change with no bump; a pull request that would remove, rename or retype a frozen
field writes its own ADR and bumps the version, as §12.6 requires. This pull request does not change
`schema/schema_frozen.json`, and `scripts/dump_schema.py --check` passes on it.

### 7. Where each rule of the design is

| Rule of the design | Edges |
|---|---|
| G05 note 03, *Map/geometry edits invalidate extraction and descendants* | 3, 4, then the descendants of **Trace generation** |
| G05 note 03, *Factor/calibration-window edits invalidate affected corrected data and descendants* | 14, 19, then the descendants of **Correction version** |
| G05 note 03, *Analysis-window edits invalidate dependent fits/plots* | 16, 17, 18 (and 15, guarded) |
| G05 note 03, *Membership edits invalidate only affected cohort results* | 23, 24; **Analysis-set version** has no edge back to the calibration |
| G03 *calibration evidence changes* | 7, 14, 20 |
| G03 *factor / contributor evidence changes* | 11, 19 |
| G03 *edit analysis windows / curation* | 12, 16 |
| G04 *Membership changed* | 23 |
| G02 note 04, a new generation, label and fit reconciliation | 5, 6, 8, 10 |
| G04 note 04, *stale fits never silently supply current results* | 25 and decision 3 |

### The nodes

<!-- graph:nodes start -->
| Key | Node | Diagram block | Identity kind | Identity | Existing stamp | When stale, it still permits |
|---|---|---|---|---|---|---|
| `source_movie` | Source movie | G02 “Immutable source registry”; G05 “Raw / prepared movies” | content hash | SHA-256 of the file bytes | `/movies.sha256`; `molecule_key` is built on it | Root: no upstream event makes it stale. A file whose bytes change is a different source: the registry shows the registered one as missing. |
| `geometry_recipe` | Geometry recipe | G02 “Pair channels; assign D/A” | content hash | SHA-256 of the canonical recipe: channel roles, the per-channel convert, crop, rotate, flip and translate steps, and the adapter with its version | the crop, rotation and flip fields of `/movies` | Root: no upstream event makes it stale. An edit makes a new recipe, and the old recipe stays in the registry. |
| `map` | Map | G02 “Separate reusable map file” | content hash | SHA-256 of the map file: source ids, geometry, transform, projection settings and quality record | `/movies.calibration_id` and `/calibration/<calibration_id>` | Readable with its quality record. It is not applied to a new extraction until the reuse check passes again. |
| `trace_generation` | Trace generation | G02 “New trace generation” | version counter | A counter per project; the generation records the identities of the sources, the recipe and the map that made it | `input_trace_hash` of each molecule (ADR-0024) | The old generation stays readable. Its labels and analysis windows are carried to the new generation; no new fit or correction uses it as current. |
| `curation_labels` | Curation labels | G03 “APPARENT_REVIEW” | content hash | SHA-256 of the curation label and the category of each molecule | `/molecules.curation_label` and the `/labels` log (ADR-0023) | Readable and editable with the mark “carried” on the new generation, until the user reconciles them. |
| `analysis_windows` | Analysis windows | G03 note 03; G03 “CORRECTED_REVIEW” | content hash | The (pre, post) frame bounds of each molecule | `/molecules.analysis_window`, folded into `input_provenance_hash` (ADR-0029) | Readable and editable with the mark “carried” on the new generation, until the user reconciles them. |
| `calibration_windows` | Calibration windows | G03 note 03 | content hash | SHA-256 of the step window of each γ contributor | none; `/molecules.bleach_frames` is the detected boundary, not a window | Root: no upstream event makes it stale. |
| `factor_version` | Bleedthrough factor and gamma version | G03 “CALIBRATION_APPROVED”; G05 “Factor versions + contributor evidence” | version counter | A counter per condition; the version also records the SHA-256 of the factors and of the contributor evidence frozen at approval | the effective applied `/molecules.alpha`, `/molecules.gamma` and `/molecules.correction_method` (ADR-0029) | Readable with its evidence (CALIBRATION_STALE). It is not applied as a new correction version. |
| `correction_version` | Correction version | G03 “apply correction version” | version counter | A counter per project; the version records its trace generation and its factor version | the effective α and γ folded into `input_provenance_hash` (ADR-0029) | Corrected data stays readable with the mark. Corrected analysis on it is disabled; apparent E stays available. |
| `review_snapshot` | Review snapshot | G03 “ANALYSIS_READY” | content hash | SHA-256 of the correction version and of the label, analysis window and bleach frames of each molecule | none | Readable. The project leaves ANALYSIS_READY; no corrected analysis set is frozen on it. |
| `analysis_set_version` | Analysis-set version | G04 “Frozen analysis-set version” | version counter | A counter per analysis set; the version records the SHA-256 of its frozen definition | none | Readable and frozen. A new version is frozen for new fits. |
| `fit` | Fit | G04 “Run model jobs in background” | content hash | SHA-256 of its input, family, K and effective settings, plus the `input_provenance_hash` of each molecule | the per-molecule `input_hashes` of `/idealization/{model}` (ADR-0024, ADR-0029) | Viewable with the mark. It is left out of the TDP, dwell and plot results unless the user asks to include stale fits; it is never the current result of an active fit. |
| `result_snapshot` | Result snapshot | G04 “Versioned result snapshot” | version counter | A counter per project; the snapshot records the SHA-256 of its lineage: the analysis-set version, the active fit and the plot recipe | the export provenance sidecar: `app_version`, `created_utc`, `parameters` | A historical result: readable with its original inputs, never shown as current. |
| `package_manifest` | Package manifest | G06 “Portable package manifest” | content hash | SHA-256 of the manifest: relative paths, the SHA-256 of each file, and the lineage | none | Readable. It is not uploaded until it is built again. |
| `deposit_record` | Deposit record | G06 “Save DOI + record version” | external id | The Zenodo deposit id, record id, record version and DOI, bound to the manifest hash | none | A published record stays a valid record of its version. A draft is bound to the new manifest before upload; a new publication is a new record version. |
<!-- graph:nodes end -->

### The edges

A dash in the *Guard* column means that the edge always applies.

<!-- graph:edges start -->
| # | Upstream | Downstream | Event that makes the downstream stale | Guard | Diagram block |
|--:|---|---|---|---|---|
| 1 | `source_movie` | `trace_generation` | Source change: the bytes no longer match the registered SHA-256 | — | G02 “Immutable source registry”; G05 “Read source pixels” |
| 2 | `geometry_recipe` | `map` | Geometry edit | only when the map no longer matches the channel geometry | G02 “Saved map matches channel geometry?”; G02 note 02 |
| 3 | `geometry_recipe` | `trace_generation` | Geometry edit | — | G05 note 03; G02 note 04 |
| 4 | `map` | `trace_generation` | Map edit | — | G05 note 03; G02 note 04 |
| 5 | `trace_generation` | `curation_labels` | New trace generation: labels are carried by `molecule_key` | — | G02 note 04 |
| 6 | `trace_generation` | `analysis_windows` | New trace generation: windows are carried by `molecule_key` | — | G02 note 04 |
| 7 | `trace_generation` | `factor_version` | New trace generation: calibration evidence changes | only when the intensities of an included γ contributor change | G03 “calibration evidence changes” |
| 8 | `trace_generation` | `correction_version` | New trace generation | — | G02 note 04 |
| 9 | `trace_generation` | `analysis_set_version` | New trace generation of a member movie | — | G04 “Frozen analysis-set version” |
| 10 | `trace_generation` | `fit` | New trace generation: the input-trace identity changes | — | G02 note 04 |
| 11 | `curation_labels` | `factor_version` | Curation edit: factor / contributor evidence changes | only when the label of a γ contributor changes | G03 “factor / contributor evidence changes” |
| 12 | `curation_labels` | `review_snapshot` | Curation edit: edit analysis windows / curation | — | G03 “edit analysis windows / curation” |
| 13 | `curation_labels` | `analysis_set_version` | Curation edit of a member molecule | only when it changes the result of the molecule filter of the set | G04 “Corrections + review current for selection?” |
| 14 | `calibration_windows` | `factor_version` | Calibration-window edit: calibration evidence changes | — | G05 note 03; G03 note 03 |
| 15 | `analysis_windows` | `factor_version` | Analysis-window edit | only when the inputs of the γ estimator change | G03 note 03 |
| 16 | `analysis_windows` | `review_snapshot` | Analysis-window edit: edit analysis windows / curation | — | G03 “edit analysis windows / curation” |
| 17 | `analysis_windows` | `analysis_set_version` | Analysis-window edit of a member molecule | — | G05 note 03 |
| 18 | `analysis_windows` | `fit` | Analysis-window edit | — | G05 note 03; G03 note 03 |
| 19 | `factor_version` | `correction_version` | Factor edit: factor / contributor evidence changes | — | G05 note 03; G03 “factor / contributor evidence changes” |
| 20 | `correction_version` | `review_snapshot` | New correction version: calibration evidence changes | — | G03 “calibration evidence changes”; G03 “finish review [all dependencies current]” |
| 21 | `correction_version` | `fit` | New correction version: the effective α or γ of a molecule changes | — | G05 note 03 |
| 22 | `review_snapshot` | `analysis_set_version` | New review snapshot of a member project | only for an analysis set in corrected E | G04 “Corrections + review current for selection?” |
| 23 | `analysis_set_version` | `fit` | Membership edit: Membership changed | — | G04 “Membership changed”; G05 note 03; G03 note 04 |
| 24 | `analysis_set_version` | `result_snapshot` | New analysis-set version | — | G04 “Lineage to exact data and active fit”; G03 note 04 |
| 25 | `fit` | `result_snapshot` | The active fit changes or goes stale | — | G04 note 04 |
| 26 | `source_movie` | `package_manifest` | Source change or a missing source | — | G06 “All sources available and verified?”; G06 note 01 |
| 27 | `map` | `package_manifest` | Map edit | — | G06 note 01 |
| 28 | `result_snapshot` | `package_manifest` | New result snapshot | — | G06 note 04 |
| 29 | `package_manifest` | `deposit_record` | Manifest change | — | G06 “Bind deposit ID to manifest”; G06 note 04 |
<!-- graph:edges end -->

### Consequences

- Good: one rule for each edge, in one table that the code reads. #895 sets marks, #896 reports
  readiness and #925 moves the correction states from the same edges.
- Good: the rule of today for idealizations is part of the graph with no change, so its tests stay
  the proof of that part.
- Good: a membership edit never touches the calibration, and an analysis-window edit stales γ only
  through a guarded edge, as G03 notes 03 and 04 require.
- Trade-off: each downstream record must record the identities of its direct inputs. That is a small
  additive field in each new record, and each implementing issue carries it.
- Trade-off: the guards are judgements of the owner of the downstream record. The table names them,
  and the code that evaluates each one belongs to that owner's issue.
- Follow-up: `tests/test_dependency_graph.py` keeps this record and the module equal, checks that
  the graph has no cycle, and checks each rule of decision 7 on the closure.

## More information

- Workflow design: [`docs/design/`](https://github.com/bioedca/tether/tree/main/docs/design)
  (diagrams G02 to G06; the notes are in the maintainer's PDF, and the PRD §7.13 FR-WORKFLOW-6 carries
  the rules). Plan: [#890](https://github.com/bioedca/tether/issues/890).
- PRD §7.1 (trace generations, card C5), §7.2 (correction states), §7.4 (active fit), §7.7
  (analysis sets, result snapshots), §7.13 FR-WORKFLOW-6, §7.14 FR-DEPOSIT.
- The stale rule of today: [ADR-0024](0024-idealization-store-layout-staleness-and-nstates.md),
  [ADR-0029](0029-idealization-correction-provenance-hash-and-per-factor-staleness.md). The schema
  freeze: [ADR-0005](0005-m0-schema-freeze.md). The label log:
  [ADR-0023](0023-curation-label-codec-and-labels-log.md). One experiment is one project:
  [ADR-0068](0068-a-project-holds-one-experiment.md).
- Users of the table: #895 (coordinator and marks), #896 (`tether status`), #925 (correction
  states), #934 (analysis sets), #373 and #375 (the plot builder).

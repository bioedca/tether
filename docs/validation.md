# Validation & benchmarks

**Who this page is for:** anyone deciding whether an analysis produced with Tether is
defensible enough to publish — and what, specifically, has and has not been measured.

Tether's acceptance criteria are seven validation oracles, (a)–(g), specified in PRD §8
(NFR-VALID) with their numeric bars in PRD §11.2. This page transcribes each one: what is
compared, against which reference data, where the frozen tolerance lives, what was
measured, and which committed test enforces it. Every number below names the file,
constant or test it comes from, so any claim can be spot-checked with `grep`.

Two things this page is *not*. It is not a claim that every oracle runs on every pull
request — several are gated to tiers that do not gate `main`, and the table below says
which. And it is not the [seven-plot parity gallery](analysis/parity-gallery.md), which
documents *plot inventory* parity with tMAVEN, not numerical accuracy.

## How to read the tiers

Test markers are declared in `pyproject.toml` under `[tool.pytest.ini_options] markers`,
with `--strict-markers`. Where a test runs follows from its marker:

| Tier | Marker | Where it runs | Gates `main`? |
| --- | --- | --- | --- |
| Required matrix | *(unmarked)* | `.github/workflows/ci.yml`, `pytest -m "not large and not sidecar and not deep and not workstation and not reference_download"` on Ubuntu, macOS and Windows | Yes — `test (ubuntu-latest)`, `test (macos-latest)`, `test (windows-latest)` |
| Live sidecar | `@pytest.mark.sidecar` | `.github/workflows/sidecar.yml` — nightly cron, manual dispatch, and pull requests, in the isolated tMAVEN env | Yes — `sidecar / parity`, but **path-filtered**: a pull request that touches none of `src/tether/idealize/`, `src/tether/fret/photobleach.py`, `src/tether/fret/efficiency.py`, `src/tether/analysis/`, `sidecar/`, `scripts/setup_sidecar.py`, `schema/parity_tolerance.json`, `tests/test_*sidecar*.py`, `tests/test_fret_photobleach.py`, the shared gate `_sidecar_gate.py` and the RF2-ArfA child `_rf2_arfa_child.py` in `tests/`, the fixtures those tests read (`tests/fixtures/smd_4mol.hdf5`, `tests/fixtures/rf2_core_wtprensec_smd.hdf5`, `tests/fixtures/rf2_core_wtprensec_model.hdf5`, `tests/fixtures/large/smd_281mol.hdf5`, `tests/fixtures/large/model_281mol.hdf5`), the workflow or the setup action reports a fast "not applicable" pass **without running the fits**. Where it does run them, the job sets `TETHER_REQUIRE_SIDECAR`, so a missing interpreter fails the tests instead of skipping them |
| Upstream tMAVEN | `@pytest.mark.sidecar` | `.github/workflows/sidecar-upstream.yml` — weekly cron and manual dispatch, the same tests with tMAVEN at the newest commit of its default branch instead of the pin (#704), also with `TETHER_REQUIRE_SIDECAR` | No — it only reports. It has no pull-request or push trigger, so it cannot be a required check |
| Large fixtures | `@pytest.mark.large` | `.github/workflows/large-fixtures.yml` — manual dispatch + weekly cron, Git-LFS checkout; the job fails first if any file under `tests/fixtures/large/` is still an LFS pointer | No — deliberately not a required check, because its headline leg can skip when the gated movie is absent |
| Deep add-on | `@pytest.mark.deep` | `.github/workflows/deep.yml` (CPU) and `deep-gpu.yml` (manual dispatch, self-hosted CUDA) | No |
| Private data | `@pytest.mark.workstation` | no CI job: a scheduled task on the maintainer's workstation runs `scripts/workstation/run_private_checks.py` ([Running the private-data checks](maintainers/workstation-runner.md)) | No |
| Reference downloads | `@pytest.mark.reference_download` | `.github/workflows/reference-data.yml` — weekly cron + manual dispatch; downloads the `download` tier of `tests/reference_data/manifest.toml` into a cache and verifies each SHA-256 | No — the required matrix makes no network call |

**When a test skips.** pytest exits 0 when a test skips, so a job stays green when a fixture,
a tool or a package goes absent. The three `test` legs, `sidecar / parity`, `deep / smoke` and
`large-fixtures` therefore write a JUnit report, and `scripts/check_skips.py` compares it with
the job's part of `tests/skip_budget.toml`. The job fails when a test skips that the file does
not list for that job with the same reason, when more tests skip than the job's
`max_skipped`, or when fewer tests ran than its `min_collected` (a path filter or a marker
error that takes a file out of the run). Each listed skip says why it is correct on its jobs.
A pull request that adds a skip, or removes tests on purpose, changes the file in the same
pull request (#726).

The required set is the `main-baseline` ruleset: `lint`, `test` on the three platforms,
`pre-commit`, `commitlint`, `secret-scan`, `conda-lock-verify`, `docs-build`,
`schema-guard` and `sidecar / parity`.

**When a scheduled run fails.** `sidecar.yml` (nightly, and after each merge to `main`),
`sidecar-upstream.yml` (weekly), `deep.yml` (nightly), `large-fixtures.yml` (weekly),
`reference-data.yml` (weekly) and `docs-liveness.yml` (daily) call
`.github/workflows/scheduled-failure.yml` after their job. When the job fails on `main`, that
workflow opens one issue with the title `ci(scheduled): <job> failed on main`. The issue gives
the run, the commit, the failed step and the last 50 log lines up to the last error. The two
oracle jobs, `sidecar / parity` and `large-fixtures`, use the fields and labels of the
validation-oracle-failure form. The other jobs use `type:ci`. A later failure adds one comment,
and the next passing run adds one comment. Each day there is at most one new comment of each
kind. After that, a change from failed to passed (or back) edits the latest comment, so the issue
always shows the latest state, failed or passed. A repeated failure on the same day adds nothing:
the Actions page lists every run. The issue does not close itself: close it with the cause. A
pull request never opens an issue. To test the report, run `docs-liveness` by hand on a test
branch with `drill` set to `fail`, then to `pass`. A drill issue has the title prefix
`ci(drill)`, so it never touches the real issue.

**Reference data sets.** Each reference file of the regression suite has one entry in
`tests/reference_data/manifest.toml`: its DOI or accession, record URL, SPDX licence, size,
SHA-256, tier and citation. A test gets the file with `path_for(id)` from the `fetch` module
beside that manifest, which calculates the SHA-256 before the first use and refuses a
file whose checksum is not the manifest's. The tiers are `git` (at most 1 MB for each file), `lfs`
(at most 50 MB for all LFS files together), `download` (at most 200 MB for each file; downloaded
only when the environment variable `TETHER_REFERENCE_DOWNLOAD` is `1`) and `workstation` (a
directory that an environment variable names). A test skips, with the reason, when its tier is not
available. [ADR-0073](adr/0073-one-manifest-for-reference-data-sets.md) records the tiers, the
limits and the cache rule.

Which tier each oracle's *headline* assertion lives in:

| Oracle | Headline assertion runs in | Also covered on the required matrix |
| --- | --- | --- |
| (a) Extraction vs Deep-LASI | `large` — but it **skips even there**, because the ≈0.9 GB movie is on no runner; the numbers come from a local run | scorer unit tests, the committed 4-molecule slice, the registration RMS gate |
| (b) Idealization parity vs tMAVEN | `sidecar` — required, but only actually fits when a pull request touches an idealization path | the frozen artifact is checked against its own recorded evidence |
| (c) kinSoftChallenge kinetics | `large` — and **advisory**, never a gate | reference loader + synthetic rate recovery; the level-2 and level-3 truth against the simulator settings files |
| (d) Ranker held-out cross-validation | required matrix | yes (synthetic stores only) |
| (e) α / γ estimators | required matrix | yes |
| (f) Round-trip and schema integrity | required matrix (plus the required `schema-guard` job) | yes; the live tMAVEN-opens leg is `sidecar` |
| (g) Photobleach detector | required matrix | yes; the live tMAVEN leg is `sidecar` |
| (h) Histogram and cross-correlation vs smfret-analysis2 | required matrix | yes (committed verbatim reference files) |
| RF2-ArfA data set of the tMAVEN authors | required matrix (import, kinetics) and `sidecar` (fit, 2-D histogram), on a committed extract | the whole archive and Table 1 run weekly under `reference_download` |

## (a) Extraction vs Deep-LASI

**What is compared.** Tether's native extraction — donor spot coordinates and the
per-molecule integrated donor/acceptor traces — against the Deep-LASI `.mat` export for
the same movie. The scorers are in `src/tether/project/oracle.py`: `match_coordinates`
(greedy unique nearest-neighbour), `coordinate_rms`, `pooled_pearson`,
`evaluate_extraction`.

**Reference data.** The UCKOPSB movie plus its `.tdat`, `.tmap` and Deep-LASI `.mat`
export. The ≈0.9 GB movie is **not in the repository** — the reason is size, not
licensing: `.github/workflows/large-fixtures.yml` records that it "is NOT committed (not
even to LFS; it is too large to host on the repo's LFS budget)", while PRD §8
(NFR-FIXTURES) states the lab *does* hold redistribution rights, which is why the cropped
slice below could be committed at all. The gated test locates the full movie via
`TETHER_UCKOPSB_DIR` and skips when it is absent. A committed 4-molecule slice
(`tests/fixtures/deeplasi_export_slice.mat`, `tests/fixtures/deeplasi_traces_slice.txt`)
covers the readers on every matrix run.

**Frozen tolerance.** Module constants in `src/tether/project/oracle.py`, not a JSON file:

| Constant | Value | Role |
| --- | --- | --- |
| `RECALL_THRESHOLD` | 0.95 | gated |
| `MATCH_TOL_PX` | 2.0 | gated (match tolerance for recall) |
| `PEARSON_THRESHOLD` | 0.95 | gated — **donor channel only** |
| `RMS_THRESHOLD_PX` | 0.5 | gated when registration is fit natively |

These are the M1 bars as reframed by [ADR-0022](adr/0022-m1-acceptance-reframe-and-close.md);
the original PRD §9 M1 gate asked for 1 px recall and r ≥ 0.99 on both channels.

**Measured result** (ADR-0022, from a **local** run on the maintainer's workstation — see
"Enforcing tests" below — on the full UCKOPSB pair with imported `.tmap` registration and
`.tdat` intensity-mode detection):

| Metric | Value | Status |
| --- | --- | --- |
| Recall @ 1 px | 0.928 | below the original 1 px bar — the reason for the reframe |
| Recall @ 2 px | 0.984 | **gated**, passes |
| Donor per-molecule Pearson (median) | 0.982 | **gated**, passes |
| Acceptor per-molecule Pearson (median) | 0.854 | reported, **never gated** |
| Donor precision | 0.34 | reported, never gated |
| Registration RMS | not applicable | the map was imported from the `.tmap`, not fit |

ADR-0022 records the reasons each reframed bar is science rather than a lowered
standard: 1 px recall is a localizer-*identity* test between two independent sub-pixel
localizers on human-curated picks; Tether is donor-anchored
([ADR-0015](adr/0015-donor-anchored-colocalization.md)), so the acceptor is read at the
mapped donor position and its Pearson carries the map's molecule-domain scatter; and a
bidirectional colocalization filter was measured to collapse recall to ≈0.66.

**Enforcing tests.** `tests/test_oracle.py::test_extraction_meets_m1_acceptance_on_uckopsb`
— `@pytest.mark.large`, so it is never on the required matrix. It *is* collected by the
weekly `large-fixtures` job, but it **skips there every time**: the movie is absent from
every GitHub-hosted runner, so this assertion has never executed in CI. (The weekly run of
2026-07-20 reports `test_extraction_meets_m1_acceptance_on_uckopsb SKIPPED`, summary
`7 passed, 4 skipped`.) The numbers in the table above therefore come from a local run of
this enforcing test with `TETHER_UCKOPSB_DIR` pointing at a staged copy of the movie, which
supplies the `.tdat` detector settings and the 2 px gate; that run is recorded in ADR-0022.
`scripts/run_m1_oracle.py --data-dir <staged copy>` reproduces the same configuration
as a one-shot report ([#555](https://github.com/bioedca/tether/issues/555)): it imports
the `.tmap`, passes the unique `.tdat` to extraction, scores at the frozen `MATCH_TOL_PX`,
and gates registration RMS only when the map is fit natively (an imported `.tmap` reports
it as not applicable). Its own behaviour is pinned without the movie by
`tests/test_run_m1_oracle.py`.

On the required matrix: `tests/test_oracle.py::test_real_slice_txt_correlates_with_mat` —
which is **not an extraction check**. It reads the committed 4-molecule slice's `.txt` and
`.mat` exports through Tether's two Deep-LASI readers and asserts they agree (Pearson
medians ≥ 0.99 on both channels), passing the `.mat`'s own coordinates in as the
"extracted" ones, so recall is 1.0 by construction and `tether.imaging.extract` is never
called. It exercises the readers and the scorers, not Tether's extraction — read its
≥ 0.99 as reader-to-reader agreement, never as extraction fidelity (which was measured at
donor 0.982 / acceptor 0.854 above). Also
`test_acceptor_pearson_is_diagnostic_not_gated`, and the registration leg
`tests/test_register.py::test_native_fit_reproduces_tmap_within_tolerance` (native
degree-2 fit vs the imported `.tmap`: RMS ≤ 0.5 px and ≥ 95 % of molecule positions
agreeing within 1 px, on committed fixtures).

## (b) Idealization parity vs tMAVEN

**What is compared.** Four metrics from `src/tether/idealize/parity.py`, after canonical
mean-sorted state relabelling: `state_count_fraction`, `state_mean_abs_delta`,
`viterbi_agreement`, `relative_elbo`. Parity is statistical, never bit-exact — tMAVEN
self-reseeds ([ADR-0007](adr/0007-parity-is-statistical.md)).

**Reference data.** `tests/fixtures/smd_4mol.hdf5` (4 molecules, 2 states, cross-seed
anchored on its own first run) and the Git-LFS `tests/fixtures/large/smd_281mol.hdf5`
(281 molecules, 4 states) against the committed reference model
`tests/fixtures/large/model_281mol.hdf5`.

**Frozen tolerance.** `schema/parity_tolerance.json` — `schema_version` 1,
`frozen_at_milestone` `"M0.5"`, `measured_utc` `"2026-06-26"`. The default row at
`$.tolerance` applies to consensus VB-HMM and per-trace vbFRET; ebFRET is frozen
separately at `$.tolerance_by_method.ebhmm`
([ADR-0043](adr/0043-per-method-parity-tolerance.md)):

| Bound | Direction | `$.tolerance` (default) | `$.tolerance_by_method.ebhmm` |
| --- | --- | --- | --- |
| `state_count_min_fraction` | floor | 0.9 | 0.5249 |
| `state_mean_abs_delta_max` | ceiling | 0.02 | 0.0518 |
| `viterbi_min_agreement` | floor | 0.95 | 0.9034 |
| `relative_elbo_max` | ceiling | 0.01 | 0.01 |

The freeze rule is recorded in the file at `$.freeze_policy`: the frozen bound is the more
permissive of the provisional PRD §11.2 default and the measured worst case with a 0.5
margin. `$.provisional` holds the same four numbers as `$.tolerance` — the measured spread
confirmed the defaults rather than widening them
([ADR-0009](adr/0009-parity-metrics-and-freeze.md)).

**Measured result.** 20 self-reseeded fits per fixture; 39 recorded comparisons pooled
across the two fixtures. `$.pooled_worst` for vbconhmm:

| Metric | Pooled worst case |
| --- | --- |
| `state_count_fraction` | 1.0 |
| `state_mean_abs_delta` | 8.34719832143449e-09 |
| `viterbi_agreement` | 1.0 |
| `relative_elbo` | 1.0024658100238614e-09 |

ebFRET was measured separately (`$.measured_by_method.ebhmm`, `measured_utc`
`"2026-07-08"`, 19 cross-seed comparisons on `smd_281mol`): pooled-worst
`state_count_fraction` 0.6832740213523132, `state_mean_abs_delta` 0.034534406067662116,
`viterbi_agreement` 0.9355949176941853, `relative_elbo` 0.0006627875119730492. ebFRET
reproduces its kinetic model across seeds, but its empirical-Bayes per-trace state
*selection* is genuinely more seed-variable — which is why it gets its own row rather than
loosening the shared one. Its ELBO is not commensurable with the vbconhmm reference
model's, so it is anchored cross-seed only (ADR-0043).

**The live ebFRET gate runs on two fixed seeds.** Unseeded, tMAVEN's ebFRET draws its starting
points from two streams nothing seeds: Numba's own generator (the restart means in
`hmm_eb.eb_outer_loop`) and NumPy's global generator, which `initialize_gmm` re-seeds from
operating-system entropy once per molecule. The gate therefore compared two random fits, and one
observed draw scored Viterbi 0.7568 against the frozen 0.9034 floor
([#311](https://github.com/bioedca/tether/issues/311)). `run_ebhmm(..., seed=N)` now seeds both
streams in the sidecar, and the gate fits seeds 1 and 2, so it gives one answer per commit. The
band is unchanged. `test_4mol_ebfret_same_seed_gives_the_same_model` fails if a seeded fit stops
being reproducible ([ADR-0075](adr/0075-seeded-ebfret-parity-gate.md)). Unseeded fits, including
every fit run from the GUI, keep tMAVEN's own behaviour.

**Which build each tolerance was measured on — and the gap that opens.** Both `method`
blocks name a build (`sidecar_python_version`, `tmaven_commit`) rather than an interpreter
path, and both carry a `build_provenance` string. The values in the committed artifact were
not captured automatically: `scripts/measure_parity.py` only grew a build probe on
2026-07-20, after both runs, so both pairs of values are post-hoc attributions. The script
now emits a non-empty `build_provenance` description with its measurement-time CPython and
PEP 610 commit probes, so a future measurement preserves how those two fields were obtained.
The historical attributions differ in how well-founded they are and — more importantly — in
*which* build they name:

| | `$.method` (M0.5, vbconhmm) | `$.measured_by_method.ebhmm.method` |
| --- | --- | --- |
| `measured_utc` | 2026-06-26 | 2026-07-08 |
| `sidecar_python_version` | 3.9.23 | 3.12.13 |
| `tmaven_commit` | `71cfa1af…` (2025-05-06) | `10f4230b…` (2025-10-05) |
| How the values were obtained | reconstructed on 2026-07-20 from the interpreter the old workstation path still resolves to; the artifact says "the environment is not guaranteed unchanged since 2026-06-26" | re-derived from the run's own inputs — `.github/workflows/sidecar-measure.yml` (dispatch run 28963324581, whose log records `python 3.12.13` and the resolved `@10f4230`), `sidecar/conda-lock.yml` and the workflow's pinned `TMAVEN_SPEC` |
| Same tMAVEN commit and CPython as the live gate? | **No** | Yes |

The pin every live assertion uses is `10f4230b6d13c6d2ad67b05d801696b4a40eff4a` on CPython
3.12.13 — `TMAVEN_SPEC` in `.github/workflows/sidecar.yml`, `python=3.12.*` in
`sidecar/environment.yml` resolving to 3.12.13 on all four platforms in
`sidecar/conda-lock.yml`, and the same commit recorded in `NOTICE` and
`scripts/setup_sidecar.py`. Those two fields are all either `method` block records about its
build: neither records the rest of the resolved sidecar lock, so "the same build" on this page
means the same tMAVEN commit on the same CPython, not an identical dependency set.

> Gap, stated plainly. The **default** frozen tolerance — the `$.tolerance` row that gates
> the required `sidecar / parity` vbconhmm fits and is `applied_to` per-trace vbFRET — was
> measured against tMAVEN `71cfa1af` (2025-05-06) on CPython 3.9.23. That is 13 upstream
> commits *behind* the pinned `10f4230b` (2025-10-05) and a different interpreter from the
> shipped sidecar lock. The intervening commits are not cosmetic: they touch the idealizer
> path (`tmaven/controllers/modeler/modeler.py`, 256 changed lines;
> `.../modeler/fxns/hmm.py`, 42). So the frozen bound and the fits it gates were **not
> produced by the same tMAVEN build**. The ebFRET block has no such gap. The mitigating
> facts, also on the record: the M0.5 worst cases (`$.pooled_worst`, table above) are 1.0 on
> both floors and 8.35e-09 / 1.00e-09 against ceilings of 0.02 and 0.01, and the freeze
> *confirmed* the provisional PRD §11.2 defaults rather than widening them — `$.tolerance`
> holds exactly `$.provisional` — so no bound was derived from the older build's numbers.
> Closing the gap means re-measuring in the pinned sidecar build, which is a deliberate
> re-freeze and needs an ADR (PRD §11.2). `sidecar-measure.yml` cannot do it as written: its
> measurement step always passes `--cross-seed`, which forces `reference=None` for every
> fixture in `scripts/measure_parity.py`, so it would anchor `smd_281mol` on its own first run
> rather than on the committed `model_281mol.hdf5` that the default row was measured against
> and that the live gate asserts. The faithful invocation is the one in that script's own
> docstring — `scripts/measure_parity.py --n-runs 20` with **no** `--cross-seed`, which the
> script permits for `--model-type vbconhmm` — run with `TETHER_SIDECAR_PYTHON` pointed at the
> pinned sidecar build. (The script itself runs under the base interpreter. Two things then
> execute in the sidecar: the fits, which `tether.idealize.driver` spawns from that variable,
> and the script's own short build-provenance probe, which `scripts/measure_parity.py` spawns
> directly.) Two caveats on that command. `--out` defaults to `schema/parity_tolerance.json`, so
> running it as written writes over the committed artifact in place — and the write is a full
> replacement, not a merge. The script assembles one literal dict (the `out = {...}` at the end of
> `main()` in `scripts/measure_parity.py` — that dict, not this paragraph, is the authority) with
> exactly ten top-level keys: `schema_version`, `frozen_at_milestone`, `measured_utc`, `method`,
> `coverage`, `freeze_policy`, `provisional`, `tolerance`, `pooled_worst`, `spread_by_fixture`. It
> then dumps that over the target. So point `--out` at a scratch file, treat the result as *only*
> the re-measured numbers, and carry back by hand everything in the committed artifact that the
> fresh dict does not reproduce. Against today's artifact that is two things.
> `$.tolerance_by_method` and `$.measured_by_method` fall outside the ten keys entirely, so the
> ebFRET freeze (ADR-0043) vanishes and both
> `test_per_method_tolerances_cover_their_own_measured_evidence` and
> `test_load_frozen_tolerance_selects_per_method` fail. The other is that all three sub-keys
> of `$.coverage` are hand-curated in the artifact but rebuilt from the script's own
> pre-ADR-0043 literals: `measured_methods` becomes `["vbconhmm"]` rather than the committed
> `"vbconhmm (vb Consensus HMM)"` quoted below, `applied_to` gains `"ebFRET (M6)"`, and `note`
> reverts to text asserting one shared tolerance for every
> idealization method — which contradicts ADR-0043 and this page, and which no test reads.

**When upstream tMAVEN changes.** The parity tests use the pinned commit.
`.github/workflows/sidecar-upstream.yml` runs the same tests each week with tMAVEN at the newest
commit of its default branch, and its log names that commit. It never changes the pin, the
sidecar lock or a limit. When a weekly run fails, the report step opens one `type:ci` issue
whose log excerpt ends with the upstream commit and the first failing comparison (or a line
saying the tests did not run, when the install or import failed). A manual run with `drill`
set to `none` opens no issue: read its result on the Actions page. The weekly issue is the
signal to decide: move the pin, or record why not. To test it, run the workflow by hand with
`drill` set to `drift`. The drill changes one prior of the fetched tMAVEN (the consensus
VB-HMM `b`, 0.01 to 1.0). The 281-molecule fit then fails on its ELBO, and the report uses the
`ci(drill)` title prefix.

**Enforcing tests.** Live fits are `tests/test_parity_sidecar.py`
(`pytestmark = pytest.mark.sidecar`, so deselected from the 3-OS matrix and run instead by
the required `sidecar / parity` job — which executes them on every nightly run and on any
pull request touching an idealization path, and reports a "not applicable" pass otherwise):
`test_281mol_fresh_fit_matches_reference_within_frozen_tolerance`,
`test_4mol_cross_seed_matches_within_frozen_tolerance`,
`test_281mol_ebfret_cross_seed_matches_within_frozen_tolerance`. On the required matrix,
`tests/test_parity.py` checks the artifact against itself:
`test_frozen_artifact_covers_its_own_measured_evidence` requires *every* recorded per-run
value to satisfy `$.tolerance` and requires `$.provisional` to still equal the `PROVISIONAL`
constant in `src/tether/idealize/parity.py` — a *drift* check between the two, not a pin to
the literal §11.2 numbers, and
`test_per_method_tolerances_cover_their_own_measured_evidence` requires every per-method
tolerance to carry the four bounds and to be satisfied by its own recorded evidence.

Be precise about what that does and does not catch, because it is a validation claim.
The core assertion in `_assert_spread_within` is `recorded value ≥ floor` /
`recorded value ≤ ceiling`, which on its own would let a pull request satisfy a bound by
*deleting* the runs that stressed it, so the same helper additionally pins the evidence
itself: the measured fixture set, and for each fixture the SMD path, the reference anchor,
the fitted state count and the comparison count, are pinned to `_EXPECTED_EVIDENCE` (the
run00-anchored `smd_4mol` spread at 2 states and 19 of 20 runs; `smd_281mol` at 4 states and
20 runs against `tests/fixtures/large/model_281mol.hdf5`) and, for the per-method blocks, to
`_EXPECTED_EVIDENCE_BY_METHOD` (ebFRET on `smd_281mol` at 4 states, run00-anchored, 19).
Each count is cross-checked against that block's `$.method.n_runs_per_fixture`, with the
anchored-or-not mode taken from the pin, not read back from the artifact. Every fixture must
carry all four metrics in their declared direction, and each summary's
`n`/`min`/`max`/`mean`/`worst` is recomputed
from its own `values` list with the production `SpreadSummary`. So on every pull request
these two tests fail on a bound **tightened** below its own evidence, on a deleted fixture,
on a dropped or truncated run — including one removed cleanly with every summary statistic
recomputed — on a re-labelled reference anchor, SMD path or state count, on a
`$.provisional` that has drifted from `PROVISIONAL`, and on a per-method
tolerance with no `measured_by_method` entry. The anchor pin is a check on the *strings the
artifact records*: it asserts the frozen block still names the anchor it was measured
against, not that the named model file exists or is unchanged — that is Git-LFS plus the
live sidecar fit. They do **not** fail on a **loosened** bound:
widening a ceiling or lowering a floor leaves every recorded value comfortably inside it. Nor
do they fail on a pull request that edits `$.provisional` and `PROVISIONAL` *in lockstep* —
only one of them alone. The live `sidecar / parity` arm cannot catch a loosened bound
either — a looser tolerance only makes its assertions easier to pass — and nothing pins
`$.tolerance` or `$.provisional` to their committed values.

What the suite does still enforce against loosening is the *shape* of a tolerance block rather
than the magnitude of any bound inside it, plus one ordering between two blocks:

* Every block must carry exactly the four bound keys — checked by
  `test_load_frozen_tolerance_returns_the_four_bounds` for `$.tolerance` and by
  `test_per_method_tolerances_cover_their_own_measured_evidence` for each
  `$.tolerance_by_method` entry — so a bound cannot be loosened by deleting it. Deleting any
  one of the eight fails at least two tests.
* The four `$.tolerance` values must additionally be finite, so an infinite ceiling there also
  fails `test_load_frozen_tolerance_returns_the_four_bounds`. The per-method bounds carry no
  finiteness check: `$.tolerance_by_method.ebhmm.relative_elbo_max` set to infinity passes.
* `test_load_frozen_tolerance_selects_per_method` asserts ebFRET's state-count floor is strictly
  below the default one, so it fails once `$.tolerance.state_count_min_fraction` is dropped to
  ebFRET's frozen `0.5249` or below (0.5249 and 0.40 fail; 0.5250 and 0.60 pass).

Widening any of the eight bounds to some *other* finite value is caught by nothing — including
all eight widened at once, as long as the default state-count floor stays above `0.5249`. In
that region the freeze is protected in the loosening direction by review plus the deliberate
re-freeze rule (`$.freeze_policy`, PRD §11.2, ADR-0009), not by a test.

**Drift limit of the pinned build.** The two vbconhmm tests in `tests/test_parity_sidecar.py`
also assert a second bound, much tighter than the frozen default row: a state-mean difference of
at most 1e-6 FRET, a relative ELBO change of at most 1e-6, and exact (1.0) state-count and
Viterbi agreement. It is a regression limit of the pinned tMAVEN build, not a scientific
tolerance: the frozen row above stays the oracle and does not change, and the drift limit only
catches a change of tMAVEN or of a sidecar dependency that moves the fit but stays inside the
frozen row — every state mean moved by 0.015 FRET, for example. It was set from 10 runs of the
`sidecar / parity` job on GitHub runners (#737): the worst state-mean difference was 9.5e-09
FRET and the worst relative ELBO change 1.1e-09, with state-count and Viterbi agreement 1.0 in
all 20 comparisons. Each ceiling is at least 10 times its worst value. Each live parity test
also writes the agreement it measured to the job log as a `[parity]` line, so every nightly run
records it. A deliberate tMAVEN or sidecar-lock bump re-measures the drift limit in its own pull
request, which is not a re-freeze. ebFRET has no drift limit, because its per-trace state
selection changes from seed to seed (ADR-0043). The live tests also check that each fit is the
requested model type with the requested state count, which a cross-seed comparison of two
equally wrong fits cannot see.

> Gap, stated plainly. PRD §8 asks for per-trace **vbFRET** parity on the small fixtures as
> well as the consensus fit. `$.coverage.measured_methods` records only
> `"vbconhmm (vb Consensus HMM)"`, while `$.coverage.applied_to` lists per-trace vbFRET. The
> vbconhmm-measured tolerance is therefore *applied* to per-trace vbFRET without a per-trace
> measurement, and no committed test fits `model_type="vbfret"` against it.

### ebFRET against stored ebFRET sessions (report only)

The gate above checks Tether's ebFRET path only against itself. A **report** compares it with
the MATLAB ebFRET program ([#719](https://github.com/bioedca/tether/issues/719)). The data are
the 11 ebFRET session files of the MreC-MreD record, Zenodo
[10.5281/zenodo.13910538](https://doi.org/10.5281/zenodo.13910538) (CC-BY-4.0), packed into
`tests/fixtures/large/ebfret_mrec_sessions.hdf5` (manifest id `ebfret-mrec-sessions`). Each
session holds the traces that ebFRET fitted and the result of the fit. For each condition,
`test_ebfret_path_reports_its_difference_from_stored_ebfret_sessions` fits the same traces with
`run_ebhmm` (the session's crop, number of states and 2 restarts, seed 1) and prints one table
to the `sidecar / parity` job log. It compares:

- the state means with the session's empirical-Bayes prior means (`analysis(K).prior.mu`), and
- the state populations with the session's expected occupancy (`analysis(K).expect.z`, summed
  over the series), which is the quantity tMAVEN's `frac` is.

The number of states is the one the session shows (`controls.ensemble.value`): 2 for ten
conditions and 3 for `MreC-N53E-MreD`. The session files do not state the ebFRET version. Their
settings block has the fields and the defaults of the ebfret-gui `MainWindow`.

**It cannot fail on a number.** Two implementations of one method, with different starting
points and stopping rules, give no agreement band to hold them to (maintainer decision R6: A,
[#695](https://github.com/bioedca/tether/issues/695)). The test fails only when the fixture
cannot be read or a fit does not give a `K`-state `eb HMM`.

Measured on 2026-11-12 (tMAVEN 10f4230, the sidecar lock, seed 1), sorted by state mean:

| Condition | K | Largest \|mean difference\| | Largest \|population difference\| |
|---|---|---|---|
| Figure 2 / A29C-T201C | 2 | 0.039 | 0.008 |
| Figure 2 / A29C-T201C+MreD | 2 | 0.030 | 0.371 |
| Figure 2 / A33C-T201C | 2 | 0.035 | 0.239 |
| Figure 2 / A33C-T201C+MreD | 2 | 0.011 | 0.108 |
| Figure 3 / MreC-F36R-MreD | 2 | 0.008 | 0.096 |
| Figure 3 / MreC-L39E-F36R-MreD | 2 | 0.120 | 0.100 |
| Figure 3 / MreC-L39E-MreD | 2 | 0.018 | 0.082 |
| Figure 3 / MreC-MreD-L59E | 2 | 0.053 | 0.019 |
| Figure 3 / MreC-MreD-WT | 2 | 0.125 | 0.010 |
| Figure 3 / MreC-N53E-MreD | 3 | 0.073 | 0.041 |
| Figure 3 / MreC-N53E-MreD-L59E | 2 | 0.042 | 0.001 |

Two patterns show. Where the two states are close (less than 0.1 FRET apart, as in the four
Figure 2 conditions and two of Figure 3), the means agree to 0.04 or better but the populations
can differ by up to 0.37: the split of the frames between two close states is not well
determined. Where a high-FRET state holds less than 15 % of the frames (the other five
conditions), the mean of that state differs by 0.04 to 0.13 FRET. This report is evidence about the size
of the difference. It is not a tolerance, and nothing in `schema/parity_tolerance.json` comes
from it.

## (c) Kinetics vs kinSoftChallenge

This oracle is **advisory**. Only the **2-state level 1 dataset** is held to a band; levels 2
and 3 are fitted and their errors reported, never band-checked. See
[External benchmark](#external-benchmark) below for the full statement with its
qualifiers, the frozen band, the ground truth and the two reported levels.

**Enforcing test.** `tests/test_kinsoft_kinetics.py::test_kinsoft_level1_within_inter_tool_spread`
— `@pytest.mark.large`, and it skips when `tests/fixtures/large/kinsoft_sim.hdf5` is an
unmaterialized Git-LFS pointer. On the required matrix,
`test_load_kinsoft_reference` pins, by literal, the band, the ground-truth rates
(`rates_s_inv`) of the three levels and Tether's frozen level-1 rates and deviations, and
requires each deviation to be the one its rate gives;
`test_evaluate_kinsoft_level_scores_a_synthetic_level` runs the scoring on a level built in
the test; and `test_evaluate_rejects_the_reported_levels` proves levels 2 and 3 raise in the
band check rather than silently score. The reported levels run in
`tests/test_kinsoft_levels.py`: `test_kinsoft_level2_six_rates_against_the_settings_truth` and
`test_kinsoft_level3_dwell_distributions_against_the_settings_truth` are `@pytest.mark.large`
beside level 1, and on the required matrix `test_level2_reference_is_the_settings_file` and
`test_level3_reference_is_the_settings_file_renumbered` require each ground-truth value of
the two levels to equal what the parser reads from the simulator settings file. The primitives are covered by
`test_fit_gaussian_hmm_recovers_synthetic_states` and
`test_two_state_rate_constants_recovers_synthetic_rates` on synthetic traces, and the EM
stop rule by `test_fit_gaussian_hmm_stops_on_the_first_iteration_that_meets_tol`.

### (c) Measured data sets, beside the published results

The kinSoft record also holds five *measured* sets: the two of the paper's Fig. 2 (ACTR binding
NCBD, confocal, 19 traces, binned at 10 ms and at 1 ms) and the three of its Fig. 5 (SSB binding a
DNA hairpin, TIRF, 100 ms frames, 134, 163 and 118 traces). Measured data has no ground truth, so
this check is **advisory**: it shows where Tether's rates lie among the rates that the analyses of
the challenge reported, and it never gives a pass or a fail on that position.
[ADR-0082](adr/0082-measured-kinsoft-sets-are-placed-not-scored.md) records the choices.

- **Tether.** The tMAVEN sidecar fits a 2-state consensus VB-HMM with seed 0. The rate of each
  FRET class is `1/⟨τ⟩` of the pooled Viterbi dwells.
- **Published.** The kinetic model of each analysis, copied unchanged from Supplementary Data 6,
  7, 10, 11 and 12 of the paper into `tests/reference_data/kinsoft_published_results.json`, with
  the sheet name of each model. For Fig. 5 the file holds only the two-FRET-state models, which the
  paper's Fig. 5 compares. A model with hidden states gives the exit rate of its aggregated class,
  `1 / mean residence time`. Two models are not compared: one reported no FRET efficiencies, and
  one reported no rate matrix.

Exit rates in s⁻¹, measured 2026-11-12:

| Set | FRET class | Tether | Published range | Published median | Published below Tether |
| --- | --- | --- | --- | --- | --- |
| fig2-10ms | low | 2.743 | 2.340–3.520 | 2.724 | 9 of 14 |
| fig2-10ms | high | 3.310 | 1.910–3.732 | 3.150 | 12 of 14 |
| fig2-1ms | low | 2.973 | 2.140–6.110 | 2.900 | 5 of 9 |
| fig2-1ms | high | 3.594 | 1.500–7.470 | 3.500 | 5 of 9 |
| fig5-set1 | low | 0.484 | 0.207–0.522 | 0.362 | 5 of 6 |
| fig5-set1 | high | 0.454 | 0.071–0.669 | 0.331 | 5 of 6 |
| fig5-set2 | low | 0.658 | 0.339–0.660 | 0.480 | 6 of 7 |
| fig5-set2 | high | 0.621 | 0.345–0.588 | 0.480 | 7 of 7 |
| fig5-set3 | low | 0.435 | 0.030–0.443 | 0.366 | 4 of 6 |
| fig5-set3 | high | 0.323 | 0.022–0.444 | 0.234 | 5 of 6 |

Tether is inside the published range for nine of the ten rates. The high-FRET class of Fig. 5
set 2 is above it: 0.621 s⁻¹ against a published maximum of 0.588 s⁻¹. The published results
themselves differ: the paper gives a coefficient of variation of 12 % and 16 % for the two rates
at 10 ms, and of 33 % and 45 % at 1 ms.

**Enforcing tests.**
`tests/test_kinsoft_measured.py::test_tether_on_a_measured_set_beside_the_published_results` runs
in the weekly `reference-data` job, with the sidecar. It prints the position and fails only when
Tether's fit moves from its frozen `tether_measured` result. In the same job,
`test_every_copied_number_equals_its_cell_in_the_publisher_spreadsheet` downloads the five
spreadsheets, checks their SHA-256 and compares each copied number with its cell. On the required
matrix,
`test_the_page_shows_tether_beside_the_published_range` keeps the table above equal to the file,
`test_every_analysis_has_the_shape_of_one_sheet` and
`test_every_set_names_its_data_and_the_paper_file_its_numbers_come_from` check the copy and its
sources, and `test_a_class_of_two_hidden_states_exits_at_its_mean_residence_time` checks the
derivation on a model whose answer is known.

## (d) Ranker held-out cross-validation

**What is compared.** Prequential (interleaved test-then-train) precision@k of the trained
per-condition ranker against the file-order baseline, per video, aggregated as the
**median across videos** and reported as an uplift in percentage points.

**Frozen tolerance.** `DEFAULT_SHIP_BAR_PTS = 10.0` in `src/tether/ml/prequential.py` — a
Python constant, not a JSON artifact. Asserted exactly by
`tests/test_ml_prequential.py::test_default_ship_bar_is_ten_points`, which pins
`DEFAULT_SHIP_BAR_PTS == 10.0` as the PRD §11.2 value. The rationale that apparent
precision@k is *not* the gate is [ADR-0034](adr/0034-gradient-boosting-quality-ranker.md).

**Measured result.** None on real data. Both enforcing tests build synthetic or
programmatic stores; no committed artifact records a precision@k uplift measured on a real
labelled video set. What is proven is that the *protocol* behaves: the first video is
skipped rather than scored against nothing, a ranker equal to file order yields zero uplift
and does not ship, a non-finite score raises instead of being ranked last, and a
single-movie project raises rather than inventing a held-out video.

**Enforcing tests** (all unmarked — required 3-OS matrix): `tests/test_ml_prequential.py`
(pure core with fake scorers, including `test_perfect_ranker_beats_file_order`,
`test_ranker_equal_to_file_order_is_zero_uplift_not_shipped`,
`test_no_trainable_prior_raises_never_fabricates`) and `tests/test_project_prequential.py`
(store-integrated: `test_gate_ships_on_separable_multivideo`,
`test_gate_uplift_is_held_out_not_apparent`, `test_custom_ship_bar_can_withhold_a_ship`,
`test_single_movie_project_has_no_held_out_video`).

> Gap, stated plainly. PRD §8 says "prequential / leave-one-video-out". Only the
> **prequential** protocol exists — a repository-wide search for `leave_one_video_out`
> returns nothing in `src/` or `tests/`.

## (e) α / γ estimator edge cases

**Frozen constants** (PRD §11.2 rows), all in code:

| Constant | Value | Module |
| --- | --- | --- |
| `LEAKAGE_CEILING` | 0.3 | `src/tether/fret/leakage.py` |
| `DEFAULT_MIN_WINDOW_FRAMES` | 20 | `src/tether/fret/leakage.py` |
| `DEFAULT_MIN_QUALIFYING_TRACES` | 10 | `src/tether/fret/leakage.py` |
| `GAMMA_CEILING` | 5.0 | `src/tether/fret/gamma.py` |
| `DEFAULT_GAMMA_HALF_WINDOW` | 3 | `src/tether/fret/gamma.py` |

The first three are locked by `tests/test_fret_leakage.py::test_defaults_match_prd_11_2`.

**What is compared.** α (leakage) against **synthetic known-α ground truth**, and γ against
**synthetic known-γ recovery** (`test_recovers_known_gamma`, parametrized over γ ∈ {0.5,
1.0, 2.0, 4.0}) plus a **reference-formula parity check** (`test_reference_formula_parity`)
against an independent in-test transcription (`_reference_gamma`, deliberately computed a
different way — explicit slices, no shared helper). That transcription is of the δ = 0,
**bare-`I_D`** simplification of Deep-LASI's `deep_autocorrect_2color.m:118-130`, which is
*Tether's own* convention, so the check proves self-consistency, not agreement with
Deep-LASI: ADR-0028 deliberately chose bare `I_D` over Deep-LASI's `(1+α)`-scaled donor and
records that "Tether's γ is systematically ≈ `(1 + α)` times Deep-LASI's on the same step"
— ≈ 9 % at α ≈ 0.09. The actual Deep-LASI-median comparison is deferred (below). The edge
cases the PRD names are
each a test: too-short tail, no tail when the acceptor outlives the donor, above-ceiling
and negative rejection, degenerate donor, median over qualifying traces only, and
withholding below the minimum qualifying count rather than emitting a factor from too
little data. `tests/test_fret_leakage.py::test_estimate_empty_qualifying_never_medians_empty`
turns any `RuntimeWarning` into a failure so an empty median can never leak through.

**Total failure → apparent E, never NaN.** `tests/test_project_correct.py` covers the path
that PRD §8 singles out: `test_total_failure_falls_to_apparent_never_nan`,
`test_fresh_store_nan_factors_is_total_failure`, `test_zero_gamma_boundary_is_total_failure`,
`test_negative_gamma_is_total_failure`, `test_manual_override_rescues_total_failure`,
`test_partial_alpha_override_without_gamma_stays_apparent`, and
`test_gamma_override_must_be_positive_finite` (parametrized over `0.0`, `-1.0`, `nan`,
`inf`). The design rule is [ADR-0003](adr/0003-apparent-e-never-nan.md). All of these run on
the required matrix.

> Not implemented — the two deferred legs of this oracle.
>
> 1. The **conjunctive two-estimator α agreement** band of PRD §11.2 (relative-median
>    difference ≤ 20 % *and* both medians within 0.05–0.2) has no code and no test. The
>    donor-only-sample estimator it needs cannot be built from the committed
>    `cy3-donor-only-calibration` `.tdat`, whose traces sit in an undecoded MCOS
>    `FileWrapper__` blob; deferred in
>    [ADR-0027](adr/0027-leakage-alpha-tail-estimator.md) gated on an MCOS trace decoder.
>    Until then the tail α is the applied factor and the band is not gated against a
>    fabricated value.
> 2. The **γ within ±10 % of the Deep-LASI median** oracle of PRD §11.2 is deferred: the
>    vendored export carries no per-frame classification, so the shared-frame,
>    estimator-isolated comparison cannot be constructed
>    ([ADR-0028](adr/0028-gamma-acceptor-bleach-step-estimator.md)).

## (f) Round-trip integrity and schema migration

**What is compared.** Three things: the `.tether` HDF5 structure declared by the code
against the committed golden manifest; a fresh project reopened with every M0-frozen field
intact; and the SMD interchange write → read superset round-trip.

**Frozen artifact.** `schema/schema_frozen.json`, the golden manifest read by
`tests/test_schema.py` as `GOLDEN_PATH`.

**Tolerance.** Structural, not numeric: **additive-only**. Additions pass; removals,
renames, dtype changes, field reordering, mid-group insertions and a decremented
`schema_version` all fail. In full, a violation is any of: a frozen field removed or
renamed, or its dtype or shape changed; a field reordered or inserted before the end; a
frozen group or dataset removed; a dataset's dtype or `maxshape` changed; a frozen
attribute removed or its dtype changed; the root `format` changed; and `schema_version`
missing, decreased, or raised without a regenerated golden. This is the M0 freeze,
[ADR-0005](adr/0005-m0-schema-freeze.md).

**Enforcing tests** (all unmarked — required matrix): `tests/test_schema.py`
`test_round_trip_preserves_version_and_frozen_fields`, `test_committed_golden_matches_code`,
`test_golden_carries_required_frozen_fields`, `test_assert_compatible_refuses_newer_file`,
and one negative guard for each violation kind: `test_guard_flags_removed_field`,
`test_guard_flags_dtype_change`, `test_guard_flags_field_shape_change`,
`test_guard_flags_reordered_field`, `test_guard_flags_mid_insertion`,
`test_guard_flags_removed_group`, `test_guard_flags_removed_dataset`,
`test_guard_flags_non_compound_dataset_dtype_change`,
`test_guard_flags_compound_dataset_becoming_plain`, `test_guard_flags_maxshape_change`,
`test_guard_flags_removed_attribute`, `test_guard_flags_attribute_dtype_change`,
`test_guard_flags_format_change`, `test_guard_flags_missing_version`,
`test_guard_flags_version_decrement`, `test_guard_flags_version_raised_without_new_golden`,
plus `test_guard_allows_additions`. Separately, `.github/workflows/schema-guard.yml` runs
`python scripts/dump_schema.py --check` on every pull request and push to `main` — a
required status check independent of the test matrix.

The SMD interchange leg is `tests/test_smd_io.py` (unmarked; `test_read_4mol_fixture`,
`test_written_smd_has_tmaven_openable_structure`, `test_superset_coordinates_roundtrip`,
`test_plain_smd_has_no_superset`), with the live proof that a Tether-authored SMD opens in
tMAVEN's own loader in `tests/test_handoff_sidecar.py::test_tether_smd_opens_in_tmaven` and
`test_coordinate_superset_intact_after_tmaven_open` — `sidecar`-marked, so they run in the
required `sidecar / parity` job under its path filter rather than on the 3-OS matrix.

> Gap, stated plainly. There are **no schema-migration tests**, because there is no
> migration machinery: the freeze is additive-only by construction (ADR-0005), so there is
> no version step to migrate across. A search for `migrate`/`migration` in `tests/`,
> `src/tether/io/` and the workflows returns nothing.

## (g) Photobleaching detector

**What PRD §8 asks for, and why it changed.** The PRD specifies comparing per-channel
first-bleach frames against the `.mat` `pacc`/`pdon` fields within the PRD §11.2 tolerance
of ±2 frames. [ADR-0026](adr/0026-photobleach-detection-and-window-default.md) found this
field is not a per-molecule oracle: in `DeepLASI_MAT_export_…010.mat`, `pacc` and `pdon`
are `uint8` arrays of shape 250×1 whose value is the **constant 60 for all 250 molecules**,
while the corrected traces bleach at ≈97, ≈644 and ≈1693. Gating on it would require the
detector to return 60 for a molecule that demonstrably bleaches at 97. It is an
acquisition-wide marker, not ground truth.

**What is validated instead** (option C in ADR-0026, maintainer-approved 2026-07-02):
**reference-formula parity** against a line-for-line transcription of tMAVEN's
`photobleaching.py` formulas (`normal_ln_evidence`, `normal_mu_ln_evidence`,
`ln_likelihood`, `get_point_pbtime`, `pb_ensemble`), kept in the test file itself as
`_ref_*` helpers — plus recovery of a **synthesized known step** to the same ±2-frame
tolerance. Read the parity leg for what it is: these tests are unmarked, so they run on the
required matrix, where tMAVEN is not installed and is never imported. What is proven is
that the O(T) prefix-sum rewrite is faithful to the O(T²) reference *formulation*; a
transcription error in the `_ref_*` helpers would not be caught, because tMAVEN itself does
not execute here. The live leg below closes that gap.

**Live leg against tMAVEN** (#706). `tests/test_fret_photobleach_sidecar.py` runs in the
required `sidecar / parity` job. On a pull request that job runs it when the diff touches
`src/tether/fret/photobleach.py`, `tests/test_fret_photobleach.py` or any other path in
its relevance filter. A child process in the sidecar environment calls tMAVEN's own
`normal_ln_evidence`, `normal_mu_ln_evidence`, `ln_likelihood`, `get_point_pbtime` and
`pb_ensemble`, with the priors from tMAVEN's own photobleach preferences. The inputs are
the synthetic traces of the unmarked tests (same seeds, same draws) and the donor, acceptor
and summed channels of the committed 4-molecule `smd_4mol.hdf5`. Tether and the `_ref_*`
helpers are both compared with that output. The algorithm is the same, so bleach frames and
the ensemble rate must be equal. Log evidence must agree to a relative 1e-14 for the
helpers (measured worst 7.1e-16, the same O(T²) sums) and 1e-12 for Tether (measured worst
6.6e-14, from the O(T) prefix sums on a trace that never bleaches); measured 2026-11-11
against tMAVEN `10f4230`. The same one-character error put into both a `_ref_*` helper and
`tether.fret.photobleach` passes every unmarked photobleach test and fails this leg.

**Frozen constants.** The ±2-frame tolerance is asserted inline; the priors are named in
`src/tether/fret/photobleach.py` — `PB_PRIOR_A` 1.0, `PB_PRIOR_B` 1.0, `PB_PRIOR_BETA` 1.0,
`PB_PRIOR_MU` 1000.0 — and locked by
`tests/test_fret_photobleach.py::test_priors_match_registered_defaults`.

**Measured result.** Pass/fail against injected truth rather than a headline scalar. The
step-recovery test sweeps step positions 30, 60, 90, 120 and 160 across three
signal/noise settings and asserts `abs(pbt - k) <= 2` for each.

**Enforcing tests** (all unmarked — required 3-OS matrix):
`tests/test_fret_photobleach.py::test_ln_likelihood_matches_reference_formulas`,
`test_point_pbtime_matches_reference`, `test_ensemble_matches_reference`,
`test_point_pbtime_recovers_known_step_within_two_frames`,
`test_detect_photobleach_window_and_masks`, `test_never_bleaches_returns_length_and_all_zero_returns_zero`,
`test_short_traces_do_not_raise`; and at the store level
`tests/test_project_photobleach.py::test_compute_photobleach_populates_frames_and_auto_window`
and `test_manual_window_is_not_overwritten`. Live leg (`sidecar`):
`tests/test_fret_photobleach_sidecar.py::test_ref_helpers_match_live_tmaven`,
`test_tether_detector_matches_live_tmaven` and
`test_tmaven_default_priors_are_the_ones_tether_and_the_helpers_use`.

## (h) Histogram and cross-correlation vs smfret-analysis2

**What is compared.** PRD §1 makes two model-free lenses first-class: the apparent-E histogram
and the donor–acceptor cross-correlation. Both come from the laboratory's MATLAB scripts,
smfret-analysis2. The public repository `vafabakhsh-lab/smfret-analysis2` at the commit that
`scripts/reference/matlab/pins.toml` pins (`121634e9`, 2022-05-20, GPL-3.0) carries 76 trace
files and the two outputs that its `create_histogram.m` wrote from them, `total_FRET.csv`
and `total_XC.csv`. [ADR-0084](adr/0084-smfret-analysis2-is-a-reference-at-a-pinned-commit.md)
records this as a reference.

**Reference data.** The 78 files are committed verbatim under `tests/fixtures/smfret_analysis2/`
(tier `git`, GPL-3.0-only, one manifest entry each with its SHA-256; the PROVENANCE table of the
fixtures lists each file with its upstream path). The commit's own scripts reproduce both
outputs from the traces, so no MATLAB run is needed. A reader that lives only in the tests
reads the trace files; the product has no importer for them (PRD N4).

**Two exact conversions, no tolerance.** Tether and the script use different conventions in
two places, and the tests convert each one exactly before they compare:

- *Bin edges.* `hist(fret, -0.2:0.01:1.2)` puts the edges at the midpoints of the centres and
  closes each bin on the right; `apparent_e_histogram` closes each bin on the left. Only a
  sample exactly on an edge is binned differently. In these traces there is one sample, at
  0.375. The test moves each such sample one bin down and requires that there is exactly one.
- *Cross-correlation normalization.* The script divides the sum at lag k by N − k and by
  MATLAB's `std`, which divides by N − 1. Tether divides every lag by N and uses the population
  σ (§7.7, kept by the maintainer). The exact factor from Tether's value to the script's is
  (N − 1) / (N − k), at Tether's lag −k (the script correlates donor(t) with acceptor(t + k)).

With the conversions, Tether's histogram equals the `raw` column of `total_FRET.csv` and
Tether's cross-correlation equals `total_XC.csv`, each to 1e-14 absolute: the files write 15
significant digits, and the rest is float64 round-off of the FFT and of the sums. Measured
2026-11-12: largest difference 4.4e-16 for the histogram and 3.1e-16 for the cross-correlation. Without the edge rule the histogram misses by 7.5e-6 in the
0.37 and 0.38 bins; with N / (N − k) as the only factor the cross-correlation misses by more
than 1e-4.

**Out of scope.** The successive-frame cloud (#710), the smoothed and filtered histogram
columns, the heat map, trace selection and background subtraction. The files under
`test-data/ground-truth/` of the same commit are not used: the commit's scripts do not
reproduce them.

**Enforcing tests** (all unmarked — required 3-OS matrix):
`tests/test_smfret_analysis2_reference.py::test_tether_histogram_equals_total_fret_raw_column`,
`test_tether_cross_correlation_equals_total_xc`,
`test_the_lag_factor_alone_does_not_reach_the_file` and
`test_the_fixtures_are_the_pinned_commit_of_smfret_analysis2`.

## RF2-ArfA data set of the tMAVEN authors

Every other tMAVEN check compares Tether with fits that Tether's own sidecar made. This one uses an
analysis the tMAVEN authors made themselves: the "RF2-ArfA smFRET data and analysis" record of
Michael, Huang, Ray, Kinz-Thompson and Gonzalez (Zenodo `10.5281/zenodo.17246323`, CC-BY-4.0), the
tMAVEN SMD files and "vb Consensus HMM" models behind their paper (*Proc. Natl. Acad. Sci. USA*
**122**(46):e2509408122, 2025, doi `10.1073/pnas.2509408122`).
[ADR-0079](adr/0079-rf2-arfa-authors-data-oracle.md) records the decisions.

**What is committed.** One model/SMD pair, the RF2 core domain on ArfA-pre-incubated non-stop
elongation complexes: the model file verbatim and a windowed extract of the SMD file (frames
outside each molecule's analysis window set to `0.0`, so it fits under the plain-git gate; a
tMAVEN fit of the extract equals the fit of the original). `tests/fixtures/PROVENANCE.md` gives
the archive and member checksums. The whole archive (127.8 MB, 11 SMD files, 9 models) is a
`download` entry of the manifest.

**Measured results** (2026-11-12; tMAVEN `10f4230`, CPython 3.12.13, NumPy 1.26.4; the authors'
files name no tMAVEN build and are dated 2022-11-29):

| Check | Result |
| --- | --- |
| 1. Import | Every SMD file and model reads, writes back and stores with no loss. The one loss is the float32 `/traces` layers: in 3 of the 11 SMD files some intensities round, by at most 2^-24 relative |
| 2. Idealization | A fresh two-state fit with the authors' priors and bleed-through 0.035, seed 1: state-count agreement 1.0, per-state \|ΔE\| 7.2e-5, Viterbi agreement 0.99954, all inside the frozen row. **Relative ΔELBO 0.0194 is outside the frozen 0.01.** The whole gap is the Gaussian-Wishart term, which tMAVEN adds once per trace; the other three ELBO terms agree to 2.0e-5 relative and the rates to 4.3e-4. The frozen row does not change; the test pins the measured difference |
| 3. Kinetics | The stored rates are the authors' rule `-ln(1 - p) / τ` on the stored transition matrix. With the paper's photobleaching correction, 16 of the 21 numbers of Table 1 fall inside the stated SD |
| 4. 2-D histogram | Start-synchronized: Tether's counts equal tMAVEN's cell for cell. Post-synchronized on the window frames: the same 38 transitions in 29 molecules and the same 1608 counts, cell for cell (since #806; before it Tether lacked 2, the last frame of a dwell that runs to the window end). tMAVEN's app also counts 2 jumps at a window border (#707) |

**Table 1 numbers that do not agree** (1/s; paper value ± SD against the value from the archived
model). They are measured differences, pinned in `TABLE_1_DIFFERENCES`, not tolerances:

| Complex, rate | Paper | Archive |
| --- | --- | --- |
| wt-pre-nsEC, proximal → distal | 0.32 ± 0.02 | 0.3705 |
| wt-pre-nsEC, distal → proximal | 0.25 ± 0.01 | 0.2755 |
| wt-pre-nsEC, dissociation from proximal | 0.08 ± 0.02 | 0.0593 |
| wt-pre-nsEC, dissociation from distal | 0.18 ± 0.01 | 0.1983 |
| mut-nsEC, dissociation from distal | 1.11 ± 0.03 | 1.1710 |

All four (Cy5)RF2-dIII numbers of the wt-pre-nsEC complex differ, so the archived wt-pre-nsEC
dIII model is probably not the one behind the table. The mut-nsEC model has four states, two of
them at zero FRET, which the authors merge.

**Enforcing tests.** Required matrix: `tests/test_rf2_arfa.py::test_the_extract_imports_with_no_loss`
and `test_the_stored_rates_are_the_authors_rule_on_the_stored_transition_matrix`. Weekly
(`reference_download`): `test_every_smd_file_of_the_archive_imports_with_no_loss`,
`test_every_model_file_of_the_archive_imports_with_no_loss`,
`test_the_smd_extract_is_what_its_script_says` and
`test_the_archive_reproduces_table_1_of_the_paper`. Live leg (`sidecar`):
`tests/test_rf2_arfa_sidecar.py::test_a_fresh_fit_with_the_authors_priors_reproduces_their_model`,
`test_the_settings_the_child_used_are_the_authors`,
`test_the_start_synchronized_histogram_equals_tmavens` and
`test_the_post_synchronized_histogram_equals_tmavens_on_the_window_frames`.

## External benchmark

Tether's rate constants were compared against the ground truth and the reported inter-tool
spread of the **kinSoftChallenge** of Götz *et al.*, *Nature Communications* **13**:5402
(2022) — a blind benchmark for the 14 analyses published in it, though not for Tether's own
retrospective fit (see the qualifiers below). Paper doi
`10.1038/s41467-022-33023-3`, data doi `10.5281/zenodo.5701310` (CC-BY-4.0). Everything
below is recorded in `schema/kinsoft_reference.json` (`schema_version` 2,
`frozen_at_milestone` `"M8"`, `measured_utc` `"2026-07-13"`) and rationalized in
[ADR-0048](adr/0048-kinsoft-kinetics-oracle.md); levels 2 and 3 in
[ADR-0081](adr/0081-kinsoft-levels-two-and-three-are-reported.md).

Read the qualifiers first, because the result means nothing without them:

- The check covers **level 1 only** — the 2-state equilibrium dataset of the paper's
  Fig. 2 (`$.levels.level1`: 75 traces, `dt_s` 0.2, `sampling_rate_hz` 5, `snr_estimate` 4).
- It is **advisory**. `$.description` says so verbatim: it "never gates main". A failure
  here does not block a release.
- It is **gated** to the `large` fixture tier — `@pytest.mark.large` on
  `tests/test_kinsoft_kinetics.py::test_kinsoft_level1_within_inter_tool_spread`, which runs
  weekly or on manual dispatch, needs the Git-LFS `tests/fixtures/large/kinsoft_sim.hdf5`,
  and skips if that file is still an LFS pointer.
- Levels 2 and 3 are **reported, not band-checked**: the fit runs and its errors are
  printed and frozen (see "The reported levels" below). No band applies to them, so a
  large error passes; a change of a frozen value fails its test, which is how drift shows.
- **Tether's own fit was not blind.** The kinSoftChallenge was a blind benchmark for its 14
  published analyses; that is a property of the paper, not of this check. Tether's fit is
  retrospective: the ground-truth rates are committed at
  `$.levels.level1.ground_truth.rates_s_inv`, the resulting rates are frozen in the same
  file, and the enforcing test pins the fit to those frozen values (each rate rounds to its
  frozen four-decimal value). What is
  inherited from the benchmark is the *band*, not the blindness.

**Band.** `$.band.rate_rel_deviation_max = 0.12` — the maximum relative deviation from
ground truth across the 14 published benchmark analyses of the 2-state dataset (5 % average),
against a finite-dataset MLE uncertainty floor of ≥ 3 % (1σ) recorded at
`$.levels.level1.reported_inter_tool_spread`.

**Result** — for the advisory 2-state level 1 oracle only, using a base-environment 2-state
Gaussian HMM (Baum–Welch + Viterbi) with pooled dwell-time MLE `k = 1/⟨τ⟩`:

| Rate | Ground truth (s⁻¹) | Tether (s⁻¹) | Relative deviation | Advisory band |
| --- | --- | --- | --- | --- |
| `k12_low_high` | 0.15 | 0.1465 | 0.023 | ≤ 0.12 |
| `k21_high_low` | 0.22 | 0.2131 | 0.031 | ≤ 0.12 |

Both deviations sit inside the advisory band; the artifact characterises both as
"~4x inside the band" (`$.band.rationale`) and "~4x inside the 12% band"
(`$.levels.level1.tether_measured.note`) for this one 2-state dataset. That is the whole claim: it is not a general accuracy
guarantee, and the file records no uncertainty on Tether's own two fitted rates. The
enforcing test additionally requires each rate to round, at four decimals, to its frozen
value, to reproduce the rate measured on this fixture for #740 to a relative `1e-6` (one
Viterbi frame moved between the states changes a rate by more than that), and each state to
contribute more than 500 pooled dwells.

**The reported levels, with ground truth from the simulator settings.** Since #717
([ADR-0081](adr/0081-kinsoft-levels-two-and-three-are-reported.md)) the truth of levels 2
and 3 is read, not transcribed: the record's `MATLAB_simulator.zip` holds the settings file
the simulator read for each level, the three files are committed verbatim under
`tests/fixtures/kinsoft_simulator/`, and a parser in the test suite reads them (the simulator
itself is not vendored, PRD N2). Both levels raise `ValueError` in the band check
(`test_evaluate_rejects_the_reported_levels`). Their errors are printed by the `large`-tier
tests and frozen in the file to catch drift. No band applies to them, so a large error passes,
but a change of a frozen value fails its test:

| Level | System | States | Traces | Leg (`$.levels.*.report_reason`) |
| --- | --- | --- | --- | --- |
| level2 (Fig. 3) | 3-state non-equilibrium steady state | 3 | 150 | a 3-state fit, then the six rates `k_ij = n_ij / T_i`, each against the truth |
| level3 (Fig. 4) | 4-state kinetic heterogeneity with blinking | 4 | 250 | a 2-state fit, then the dwell-time distribution of each FRET level against the one the true 4-state matrix predicts |

**Level 2.** The 3-state fit finds the three FRET levels of the settings file (0.10, 0.53,
0.90). `n_ij` is the number of counted dwells in state `i` that end in `j` and `T_i` their
total time, over the dwells `pooled_exit_rates` counts, so each row sums to that state's
exit rate:

| Rate | Ground truth (s⁻¹) | Tether (s⁻¹) | Relative deviation, no band |
| --- | --- | --- | --- |
| `k12_low_mid` | 0.30 | 0.4073 | 0.358 |
| `k13_low_high` | 0.38 | 0.2338 | 0.385 |
| `k21_mid_low` | 0.05 | 0.2373 | 3.746 |
| `k23_mid_high` | 0.37 | 0.4733 | 0.279 |
| `k31_high_low` | 0.45 | 0.2956 | 0.343 |
| `k32_high_mid` | 0.10 | 0.2287 | 1.287 |

The present method cannot recover these rates, and the reason is measured. A low↔high
jump inside one 0.1 s frame averages to a mid-FRET frame, and Viterbi reads that frame as a
one-frame dwell in the mid state: 689 of the 1638 counted mid-FRET dwells last one frame. So
`k21` and `k32` come out high and `k13` and `k31` low — the mis-allocation of 1↔3
transitions that the benchmark reports for most tools. What is missing is a correction for
a transition inside a frame (a missed-event or mixed-frame model); the fit still agrees with
the truth on which direction of each pair is faster. The earlier record of this level had
read `k12` as 0.05 and `k21` as 0.30 s⁻¹ from a diagram of the paper's supplement; the
settings file gives the reverse.

**Level 3.** States 1 and 2 share the low-FRET level and 3 and 4 the high-FRET level, so a
FRET-only 2-state fit sees two states, as the benchmark found for most tools. The benchmark
compares this level through cumulative dwell-time distributions, which it simulates from
each submitted model (Götz *et al.* 2022, above). Tether compares the pooled Viterbi dwells
of each FRET level (first and last dwell of each molecule dropped) with the distribution
the true matrix gives in closed form: a dwell in class *A* starts in its states with the
probabilities `φ_A ∝ π_B Q_BA` of an entry at steady state and lasts longer than `t` with
probability `φ_A exp(Q_AA t) 1` — the dwell-time distribution of an aggregated Markov process
(Colquhoun & Hawkes, *Proc. R. Soc. Lond. B* **211**:205 (1981), doi
`10.1098/rspb.1981.0003`; *Phil. Trans. R. Soc. Lond. B* **300**:1 (1982), doi
`10.1098/rstb.1982.0156`). The distance is the Kolmogorov–Smirnov statistic:

| FRET level | Dwells | Mean dwell (s) | Predicted mean dwell (s) | Relative deviation | KS distance |
| --- | --- | --- | --- | --- | --- |
| `low_fret` | 600 | 6.4653 | 9.0538 | 0.286 | 0.0781 |
| `high_fret` | 667 | 3.0459 | 4.3897 | 0.306 | 0.1471 |

The observed dwells are shorter than predicted. Most of that is the window: the traces are
short against the dwells (median 35.7 s against a predicted mean low-FRET dwell of 9.1 s),
and a dwell that a trace end cuts is never counted, so long dwells are under-sampled.
Blinking and noise also split dwells. The comparison therefore measures Tether's fit and
this dataset's windowing together, not the fit alone.

Level 3's full ground-truth rate matrix is recorded at
`$.levels.level3.ground_truth.rates_s_inv` in the state numbers of the paper's supplement
(k12 0.053, k14 0.018, k21 0.080, k23 0.250,
k32 0.680, k41 0.032 s⁻¹, all other off-diagonal rates zero), with blinking rates
`kbright` 7 s⁻¹ and `kdark` 0.007 s⁻¹. The
settings file numbers the same states differently; `settings_state_of` maps each one to its
row there, and the required-matrix test checks every rate, every zero and both blinking
rates of both dyes against the file.

## Scope and known limitations

**Validation coverage.**

- Oracles (a) and (c) have their headline assertions on the `large` tier, which is manual
  or weekly and is deliberately **not** a required check. Oracle (b)'s live fits are in the
  required `sidecar / parity` job, but that job only actually fits when the pull request
  touches an idealization path. A green required 3-OS matrix run on its own therefore proves
  the scorers, the frozen artifacts and the pure primitives — not a fresh fit against real
  reference data.
- Oracle (a) is stronger than that: its headline assertion depends on the ≈0.9 GB UCKOPSB
  movie, which exceeds the repo's Git-LFS budget and so is on **no** runner. The weekly
  `large-fixtures` job collects it and skips it, every time — the assertion has never run in
  CI, and its numbers come from a local run recorded in ADR-0022.
- The frozen **default** parity tolerance for (b) was measured against tMAVEN `71cfa1af`
  (2025-05-06) on CPython 3.9.23 — 13 upstream commits behind the pinned `10f4230b`
  (2025-10-05) on CPython 3.12.13 that CI, the installers and every live sidecar assertion
  actually run, with idealizer-path files changed in between. The frozen bound and the gate
  that asserts it were not produced by the same tMAVEN build; the ebFRET block has no such
  gap. Nothing was widened as a result (the measured spread was ≈1e-9 and only confirmed the
  PRD §11.2 provisional defaults), and closing it needs a re-measure plus a re-freeze ADR.
- Nothing in CI fails on a **loosened** `$.tolerance`: the artifact self-checks assert
  recorded values *within* the bounds, so widening a bound keeps them passing. Only
  tightening below the evidence, or removing the evidence, is caught (see §(b)).
- The ebFRET gate tests **one fixed pair of seeds** (1 and 2,
  [ADR-0075](adr/0075-seeded-ebfret-parity-gate.md)), not a fresh pair per run. A regression
  that shows only on some seeds can pass it. Unseeded ebFRET keeps a rare poor-optimum tail
  (one observed draw scored Viterbi 0.7568 against the frozen 0.9034 floor,
  [#311](https://github.com/bioedca/tether/issues/311)) that the frozen band does not cover.
- The kinSoftChallenge check is advisory, band-checked on the 2-state level 1 only, gated
  (see above), and **not blind** — the ground truth is committed and the fit is
  retrospective. Levels 2 and 3 are reported, not band-checked, and the present method does
  not recover the level-2 rates.
- (b) has no per-trace vbFRET measurement, (d) has no leave-one-video-out protocol and no
  real-data uplift number, (e) is missing the conjunctive α-agreement band and the γ
  Deep-LASI-median oracle, and (f) has no migration tests. Each is stated in its own section
  above with the ADR that deferred it.
- PRD §8 states that no synthetic-data simulator is introduced. In practice (e) and (g) are
  validated against **synthesized known-α / known-γ / known-step traces**, and (d) against a
  synthetic separable multi-video store, because the real fixtures do not carry the required
  ground-truth field. Two of those substitutions were ratified in an ADR and two were not:
  [ADR-0026](adr/0026-photobleach-detection-and-window-default.md) option C ratifies the
  synthetic known-step ground truth for (g), and
  [ADR-0028](adr/0028-gamma-acceptor-bleach-step-estimator.md) option D ships γ "validated
  against synthetic known-γ recovery". The synthetic known-α fixtures and (d)'s synthetic
  multi-video store carry no such ratification —
  [ADR-0027](adr/0027-leakage-alpha-tail-estimator.md) defers the donor-only α cross-check
  but never mentions synthetic data, and neither ranker ADR
  ([ADR-0034](adr/0034-gradient-boosting-quality-ranker.md),
  [ADR-0038](adr/0038-provisional-prior-training-fold.md)) discusses validation data at all. The
  real-data comparisons that do exist (oracles (a), (b), (c)) stay on real fixtures.
- A reference that comes from an ALEX data set compares **apparent FRET from the
  donor-excitation channels only** — the donor and acceptor intensities under donor
  excitation. It does not check direct excitation (δ), stoichiometry or any other quantity
  that needs the acceptor-excitation frames, and so it does not validate them. Tether is
  single-laser only (PRD N1); the PRD's "What the tests can use" rules (§2.2) allow the
  ALEX sets at trace level and add no ALEX feature.

**Accepted diagnostics that are not gates.**

- M1 acceptor per-molecule Pearson median **0.854** and donor precision **0.34** are
  reported, never gated (ADR-0022). Acceptor intensity fidelity and the ≈1.3 px acceptor
  read-position scatter were carried forward as M3 diagnostics rather than re-opening M1.
- `pacc`/`pdon` is a constant acquisition marker, not a per-molecule bleach oracle
  (ADR-0026).

**Input formats refused rather than approximated.**

- Deep-LASI particle-detection modes **4 (local-variance)** and **5 (ZMW intensity)** are
  not ported. `src/tether/io/tdat.py` raises `ValueError` on them rather than silently
  mis-detecting; only modes 1 (wavelet), 2 (intensity) and 3 (bandpass) are supported
  ([ADR-0021](adr/0021-particle-detection-modes.md)). A `.tdat` with a non-integer or
  out-of-range mode code also raises. Covered by
  `tests/test_tdat.py::test_unsupported_detection_mode_raises` and, at the CLI, by
  `tests/test_extract_cli.py::test_extract_tdat_unsupported_mode_errors`, which asserts a
  non-zero exit and that no output file is written.

**Legacy-import data gaps** ([ADR-0045](adr/0045-deeplasi-round-trip-reconstruction.md),
documented gaps rather than fabricated values):

- Deep-LASI per-molecule NN/HMM **category assignments are not written** — they sit in an
  undecoded MCOS blob. Only the category vocabulary is seeded, so a future decode attaches
  assignments additively.
- **Image patches are not populated** from Deep-LASI: the writer takes caller-supplied
  patches from the import wizard (which opens the movie), and otherwise zero-fills; the
  movie link keeps crops re-cacheable.
- **γ may be absent.** Remapped α/γ are injected only when γ is finite and positive;
  otherwise the project degrades to the explicit apparent-E substrate — never a NaN E
  (ADR-0003). The committed Cy3-only fixture exercises that path.
- δ (direct excitation) is inert 0 for single-laser Deep-LASI data, which has no ALEX
  channel to estimate it from.
- A raw-`.txt`-sourced SMD has no coordinates and no patches, so it is imported as an
  explicit **analysis-only** project with the movie round-trip browser and patch-dependent
  views disabled ([ADR-0046](adr/0046-analysis-only-smd-import.md)).

**The deep-learning add-on is not part of the validated core.** The optional torch trace
classifier lives in its own isolated lock stack
([ADR-0047](adr/0047-deep-model-optional-stack-and-dataset.md)) and is deselected from the
base 3-OS matrix by `-m "not ... and not deep"`. Its GPU leg,
`tests/test_deep_gpu_deep.py`, imports torch through `pytest.importorskip` and self-skips
without a CUDA device, and `.github/workflows/deep-gpu.yml` is `workflow_dispatch`-only —
by construction it never reports a pull-request status and can never become a required
check. Treat deep-classifier output as an assistive ranking signal, not a validated
measurement.

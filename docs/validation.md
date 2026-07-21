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
| Required matrix | *(unmarked)* | `.github/workflows/ci.yml`, `pytest -m "not large and not sidecar and not deep"` on Ubuntu, macOS and Windows | Yes — `test (ubuntu-latest)`, `test (macos-latest)`, `test (windows-latest)` |
| Live sidecar | `@pytest.mark.sidecar` | `.github/workflows/sidecar.yml` — nightly cron, manual dispatch, and pull requests, in the isolated tMAVEN env | Yes — `sidecar / parity`, but **path-filtered**: a pull request that touches none of `src/tether/idealize/`, `sidecar/`, `scripts/setup_sidecar.py`, `schema/parity_tolerance.json`, `tests/test_*sidecar*.py`, the workflow or the setup action reports a fast "not applicable" pass **without running the fits** |
| Large fixtures | `@pytest.mark.large` | `.github/workflows/large-fixtures.yml` — manual dispatch + weekly cron, Git-LFS checkout | No — deliberately not a required check, because its headline leg can skip when the gated movie is absent |
| Deep add-on | `@pytest.mark.deep` | `.github/workflows/deep.yml` (CPU) and `deep-gpu.yml` (manual dispatch, self-hosted CUDA) | No |

The required set is the `main-baseline` ruleset: `lint`, `test` on the three platforms,
`pre-commit`, `commitlint`, `secret-scan`, `conda-lock-verify`, `docs-build`,
`schema-guard` and `sidecar / parity`.

Which tier each oracle's *headline* assertion lives in:

| Oracle | Headline assertion runs in | Also covered on the required matrix |
| --- | --- | --- |
| (a) Extraction vs Deep-LASI | `large` — but it **skips even there**, because the ≈0.9 GB movie is on no runner; the numbers come from a local run | scorer unit tests, the committed 4-molecule slice, the registration RMS gate |
| (b) Idealization parity vs tMAVEN | `sidecar` — required, but only actually fits when a pull request touches an idealization path | the frozen artifact is checked against its own recorded evidence |
| (c) kinSoftChallenge kinetics | `large` — and **advisory**, never a gate | reference loader + synthetic rate recovery |
| (d) Ranker held-out cross-validation | required matrix | yes (synthetic stores only) |
| (e) α / γ estimators | required matrix | yes |
| (f) Round-trip and schema integrity | required matrix (plus the required `schema-guard` job) | yes; the live tMAVEN-opens leg is `sidecar` |
| (g) Photobleach detector | required matrix | yes |

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
`7 passed, 4 skipped`.) The numbers in the table above therefore come from a local run,
recorded in ADR-0022 and reproducible with `scripts/run_m1_oracle.py` against a staged
copy of the movie.

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

**Which build each tolerance was measured on — and the gap that opens.** Both `method`
blocks name a build (`sidecar_python_version`, `tmaven_commit`) rather than an interpreter
path, and both carry a `build_provenance` string. Neither was captured automatically:
`scripts/measure_parity.py` only grew a build probe on 2026-07-20, after both runs, so both
pairs of values are post-hoc attributions. They differ in how well-founded the attribution
is, and — more importantly — in *which* build they name:

| | `$.method` (M0.5, vbconhmm) | `$.measured_by_method.ebhmm.method` |
| --- | --- | --- |
| `measured_utc` | 2026-06-26 | 2026-07-08 |
| `sidecar_python_version` | 3.9.23 | 3.12.13 |
| `tmaven_commit` | `71cfa1af…` (2025-05-06) | `10f4230b…` (2025-10-05) |
| How the values were obtained | reconstructed on 2026-07-20 from the interpreter the old workstation path still resolves to; the artifact says "the environment is not guaranteed unchanged since 2026-06-26" | re-derived from the run's own inputs — `.github/workflows/sidecar-measure.yml` (dispatch run 28963324581, whose log records `python 3.12.13` and the resolved `@10f4230`), `sidecar/conda-lock.yml` and the workflow's pinned `TMAVEN_SPEC` |
| Matches the build the live gate runs? | **No** | Yes |

The pin every live assertion uses is `10f4230b6d13c6d2ad67b05d801696b4a40eff4a` on CPython
3.12.13 — `TMAVEN_SPEC` in `.github/workflows/sidecar.yml`, `python=3.12.*` in
`sidecar/environment.yml` resolving to 3.12.13 on all four platforms in
`sidecar/conda-lock.yml`, and the same commit recorded in `NOTICE` and
`scripts/setup_sidecar.py`.

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
> fresh dict does not reproduce. Against today's artifact that is three things.
> `$.tolerance_by_method` and `$.measured_by_method` fall outside the ten keys entirely, so the
> ebFRET freeze (ADR-0043) vanishes and both
> `test_per_method_tolerances_cover_their_own_measured_evidence` and
> `test_load_frozen_tolerance_selects_per_method` fail. `$.method.build_provenance` is a
> hand-written string inside a block the script *does* rewrite, so it is dropped with no test
> noticing. And all three sub-keys of `$.coverage` are hand-curated in the artifact but rebuilt
> from the script's own pre-ADR-0043 literals: `measured_methods` becomes `["vbconhmm"]` rather
> than the committed `"vbconhmm (vb Consensus HMM)"` quoted below, `applied_to` gains
> `"ebFRET (M6)"`, and `note` reverts to text asserting one shared tolerance for every
> idealization method — which contradicts ADR-0043 and this page, and which no test reads.

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
itself: the measured fixture set and each fixture's comparison count are pinned to
`_EXPECTED_COMPARISONS` (19 of 20 runs for the run00-anchored `smd_4mol` spread, 20 for the
reference-anchored `smd_281mol` one; 19 for ebFRET), each count is cross-checked against
that block's `$.method.n_runs_per_fixture`, every fixture must carry all four metrics in
their declared direction, and each summary's `n`/`min`/`max`/`mean`/`worst` is recomputed
from its own `values` list with the production `SpreadSummary`. So on every pull request
these two tests fail on a bound **tightened** below its own evidence, on a deleted fixture,
on a dropped or truncated run — including one removed cleanly with every summary statistic
recomputed — on a `$.provisional` that has drifted from `PROVISIONAL`, and on a per-method
tolerance with no `measured_by_method` entry. They do **not** fail on a **loosened** bound:
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

> Gap, stated plainly. PRD §8 asks for per-trace **vbFRET** parity on the small fixtures as
> well as the consensus fit. `$.coverage.measured_methods` records only
> `"vbconhmm (vb Consensus HMM)"`, while `$.coverage.applied_to` lists per-trace vbFRET. The
> vbconhmm-measured tolerance is therefore *applied* to per-trace vbFRET without a per-trace
> measurement, and no committed test fits `model_type="vbfret"` against it.

## (c) Kinetics vs kinSoftChallenge

This oracle is **advisory** and covers the **2-state level 1 dataset only**; see
[External benchmark](#external-benchmark) below for the full statement with its
qualifiers, the frozen band, the ground truth and the deferred levels.

**Enforcing test.** `tests/test_kinsoft_kinetics.py::test_kinsoft_level1_within_inter_tool_spread`
— `@pytest.mark.large`, and it skips when `tests/fixtures/large/kinsoft_sim.hdf5` is an
unmaterialized Git-LFS pointer. On the required matrix,
`test_load_kinsoft_reference` locks the reference file's contents and
`test_evaluate_rejects_deferred_levels` proves levels 2 and 3 raise rather than silently
score; the primitives are covered by `test_fit_gaussian_hmm_recovers_synthetic_states` and
`test_two_state_rate_constants_recovers_synthetic_rates` on synthetic traces.

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
`schema_version` all fail. This is the M0 freeze,
[ADR-0005](adr/0005-m0-schema-freeze.md).

**Enforcing tests** (all unmarked — required matrix): `tests/test_schema.py`
`test_round_trip_preserves_version_and_frozen_fields`, `test_committed_golden_matches_code`,
`test_golden_carries_required_frozen_fields`, `test_assert_compatible_refuses_newer_file`,
and the six negative guards `test_guard_flags_removed_field`, `test_guard_flags_dtype_change`,
`test_guard_flags_reordered_field`, `test_guard_flags_mid_insertion`,
`test_guard_flags_removed_group`, `test_guard_flags_version_decrement`, plus
`test_guard_allows_additions`. Separately, `.github/workflows/schema-guard.yml` runs
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
not execute here (that is what the `sidecar` tier does elsewhere on this page).

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
and `test_manual_window_is_not_overwritten`.

## External benchmark

Tether's rate constants were compared against the ground truth and the reported inter-tool
spread of the **kinSoftChallenge** of Götz *et al.*, *Nature Communications* **13**:5402
(2022) — a blind benchmark for the 14 analyses published in it, though not for Tether's own
retrospective fit (see the qualifiers below). Paper doi
`10.1038/s41467-022-33023-3`, data doi `10.5281/zenodo.5701310` (CC-BY-4.0). Everything
below is recorded in `schema/kinsoft_reference.json` (`schema_version` 1,
`frozen_at_milestone` `"M8"`, `measured_utc` `"2026-07-13"`) and rationalized in
[ADR-0048](adr/0048-kinsoft-kinetics-oracle.md).

Read the qualifiers first, because the result means nothing without them:

- The check covers **level 1 only** — the 2-state equilibrium dataset of the paper's
  Fig. 2 (`$.levels.level1`: 75 traces, `dt_s` 0.2, `sampling_rate_hz` 5, `snr_estimate` 4).
- It is **advisory**. `$.description` says so verbatim: it "never gates main". A failure
  here does not block a release.
- It is **gated** to the `large` fixture tier — `@pytest.mark.large` on
  `tests/test_kinsoft_kinetics.py::test_kinsoft_level1_within_inter_tool_spread`, which runs
  weekly or on manual dispatch, needs the Git-LFS `tests/fixtures/large/kinsoft_sim.hdf5`,
  and skips if that file is still an LFS pointer.
- Levels 2 and 3 are **deferred**, not passed and not attempted.
- **Tether's own fit was not blind.** The kinSoftChallenge was a blind benchmark for its 14
  published analyses; that is a property of the paper, not of this check. Tether's fit is
  retrospective: the ground-truth rates are committed at
  `$.levels.level1.ground_truth.rates_s_inv`, the resulting rates are frozen in the same
  file, and the enforcing test pins the fit to those frozen values (`abs=0.01`). What is
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
"~4x inside the band" (`$.band.rationale`, `$.levels.level1.tether_measured.note`) for this
one 2-state dataset. That is the whole claim: it is not a general accuracy
guarantee, and the file records no uncertainty on Tether's own two fitted rates. The
enforcing test additionally requires both rates to reproduce the frozen values to `abs=0.01`
and each state to contribute more than 500 pooled dwells.

**Deferred levels, with ground truth recorded.** Both are stored so a future oracle needs no
new data sourcing, and both raise `ValueError` today rather than scoring
(`test_evaluate_rejects_deferred_levels`):

| Level | System | States | Traces | Why deferred (`$.levels.*.deferred_reason`) |
| --- | --- | --- | --- | --- |
| level2 (Fig. 3) | 3-state non-equilibrium steady state | 3 | 150 | the best benchmark tools reach only 9–14 % average deviation and most mis-allocate 1↔3 transitions; a faithful oracle needs a 3-state idealizer plus net-flow handling |
| level3 (Fig. 4) | 4-state kinetic heterogeneity with blinking | 4 | 250 | states 1 and 2 share the low-FRET level and 3 and 4 the high-FRET level, so a FRET-only idealizer sees two states |

Level 3's full ground-truth rate matrix is recorded at
`$.levels.level3.ground_truth.rates_s_inv` (k12 0.053, k14 0.018, k21 0.080, k23 0.250,
k32 0.680, k41 0.032 s⁻¹, all other off-diagonal rates zero), with blinking rates
`kbright` 7 s⁻¹ and `kdark` 0.007 s⁻¹.

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
- The kinSoftChallenge check is advisory, 2-state level 1 only, gated (see above), and
  **not blind** — the ground truth is committed and the fit is retrospective. Levels 2 and 3
  are deferred.
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

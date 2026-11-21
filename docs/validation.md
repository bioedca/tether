---
description: "The seven validation oracles of Tether: what each compares, the reference data, the frozen tolerance, the measured result and the test that enforces it."
---

# Validation & benchmarks

**Who this page is for:** anyone deciding whether an analysis produced with Tether is
defensible enough to publish — and what, specifically, has and has not been measured.

Tether's acceptance criteria are seven validation oracles, (a)–(g), specified in Tether's
[product specification](https://github.com/bioedca/tether/blob/main/docs/PRD.md) (the
requirements document kept in the repository; below, "the specification") with their numeric
bars in its table of tunables. This page transcribes each one: what is
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
| Live sidecar | `@pytest.mark.sidecar` | `.github/workflows/sidecar.yml` — nightly cron, manual dispatch, and pull requests, in the isolated tMAVEN env | Yes — `sidecar / parity`, but **path-filtered**: a pull request that touches none of `src/tether/idealize/`, `src/tether/fret/photobleach.py`, `src/tether/fret/efficiency.py`, `src/tether/analysis/`, `sidecar/`, `scripts/setup_sidecar.py`, `pyproject.toml`, `schema/parity_tolerance.json`, `tests/test_*sidecar*.py`, `tests/test_fret_photobleach.py`, the shared gate [`_sidecar_gate.py`](https://github.com/bioedca/tether/blob/main/tests/_sidecar_gate.py) and the RF2-ArfA child [`_rf2_arfa_child.py`](https://github.com/bioedca/tether/blob/main/tests/_rf2_arfa_child.py) in `tests/`, the fixtures those tests read (`tests/fixtures/smd_4mol.hdf5`, `tests/fixtures/rf2_core_wtprensec_smd.hdf5`, `tests/fixtures/rf2_core_wtprensec_model.hdf5`, `tests/fixtures/large/smd_281mol.hdf5`, `tests/fixtures/large/model_281mol.hdf5`), the workflow or the setup action reports a fast "not applicable" pass **without running the fits**. Where it does run them, the job sets `TETHER_REQUIRE_SIDECAR`, so a missing interpreter fails the tests instead of skipping them |
| Upstream tMAVEN | `@pytest.mark.sidecar` | `.github/workflows/sidecar-upstream.yml` — weekly cron and manual dispatch, the same tests with tMAVEN at the newest commit of its default branch instead of the pin (#704), also with `TETHER_REQUIRE_SIDECAR` | No — it only reports. It has no pull-request or push trigger, so it cannot be a required check |
| Large fixtures | `@pytest.mark.large` | `.github/workflows/large-fixtures.yml` — manual dispatch + weekly cron, Git-LFS checkout; the job fails first if any file under `tests/fixtures/large/` is still an LFS pointer | No — deliberately not a required check: it is manual and weekly, and an LFS outage must never block `main` |
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
validation-oracle-failure form. The other jobs use `type:ci`. A later failure adds one comment
with the same facts and log excerpt for its own run, because a repeat failure can have a new
cause. The next passing run adds one comment. Each day there is at most one new comment of each
kind. After that, a change from failed to passed (or back) edits the latest comment, so the issue
always shows the latest state, failed or passed. A repeated failure on the same day adds nothing:
the Actions page lists every run. The issue does not close itself: close it with the cause. A
pull request never opens an issue. To test the report, run `docs-liveness` by hand on a test
branch with `drill` set to `fail`, then to `pass`. A drill issue has the title prefix
`ci(drill)`, so it never touches the real issue.

**Reference data sets.** Each reference file of the regression suite has one entry in
`tests/reference_data/manifest.toml`: its DOI or accession, record URL, SPDX license, size,
SHA-256, tier and citation. A test gets the file with `path_for(id)` from the `fetch` module
beside that manifest, which calculates the SHA-256 before the first use and refuses a
file whose checksum is not the manifest's. The tiers are `git` (at most 1 MB for each file), `lfs`
(at most 50 MB for all LFS files together), `download` (at most 200 MB for each file; downloaded
only when the environment variable `TETHER_REFERENCE_DOWNLOAD` is `1`) and `workstation` (a
directory that an environment variable names). A test skips, with the reason, when its tier is not
available. [ADR-0073](adr/0073-one-manifest-for-reference-data-sets.md) records the tiers, the
limits and the cache rule.

**The Source Data of the 2024 T-box paper.** The study of Campos-Chavez, Paul, Zhou, Alonso, Verma,
Fei and Mondragón, *Nature Communications* **15**:6592 (2024),
[doi:10.1038/s41467-024-50885-x](https://doi.org/10.1038/s41467-024-50885-x), publishes its Source
Data as one archive (95 MB). The weekly job downloads it and verifies its SHA-256. The repository
holds 26 of its members, unchanged: the folder `ErrorCalculations_CodeAndSourceData/` (without
its one notebook, whose saved cells hold private paths) and `checkTDP.py` in plain git, and one trace matrix (6.7 MB) in Git LFS. These files are **not under
GPL-3.0**. They are under the license of the publication, CC BY-NC-ND 4.0: you can use and share
them unchanged, for non-commercial purposes only, and you must cite the article. A test that needs
another format converts a file when it runs; the repository keeps no changed copy.
[ADR-0083](adr/0083-unchanged-copies-of-cc-by-nc-nd-reference-data.md) records these rules, and
`tests/fixtures/PROVENANCE.md` the checksum of each file.

Which tier each oracle's *headline* assertion lives in:

| Oracle | Headline assertion runs in | Also covered on the required matrix |
| --- | --- | --- |
| (a) Extraction vs Deep-LASI | `workstation` — the ≈0.9 GB movie is private data on no CI runner, so the scheduled task on the maintainer's workstation runs it and records its numbers | scorer unit tests, the committed 4-molecule slice, the registration RMS gate |
| (b) Idealization parity vs tMAVEN | `sidecar` — required, but only actually fits when a pull request touches an idealization path | the frozen artifact is checked against its own recorded evidence |
| (c) kinSoftChallenge kinetics | `large` — and **advisory**, never a gate | reference loader + synthetic rate recovery; the level-2 and level-3 truth against the simulator settings files |
| (d) Ranker held-out cross-validation | required matrix | yes (synthetic project files only) |
| (e) α / γ estimators | required matrix; the α band on the lab's donor-only sample is `workstation` | yes (the band's arithmetic and limits) |
| (f) Round-trip and schema integrity | required matrix (plus the required `schema-guard` job) | yes; the live tMAVEN-opens leg is `sidecar` |
| (g) Photobleach detector | required matrix | yes; the live tMAVEN leg is `sidecar` |
| (h) Histogram and cross-correlation vs smfret-analysis2 | required matrix | yes (committed verbatim reference files) |
| Successive-frame FRET cloud vs the FRET_cloud script | required matrix | yes (committed frozen MATLAB output) |
| (i) Corrected FRET vs MASH-FRET | required matrix (a committed extract) | yes; the full archive is checked by the workstation run |
| MASH-FRET analyses: histogram, states, rates | required matrix (exact histogram) and `sidecar` (state limits), on a committed extract | the 37 stored histograms of the archive are checked by the workstation run |
| Histogram bootstrap vs MASH-FRET | required matrix, on a committed extract | yes |
| RF2-ArfA data set of the tMAVEN authors | required matrix (import, kinetics) and `sidecar` (fit, 2-D histogram), on a committed extract | the whole archive and Table 1 run weekly under `reference_download` |
| Campos-Chavez et al. 2024: the tMAVEN fits | `reference_download` — weekly, with the sidecar env; **advisory** | the settings, the windows, the comparison and the limit rule; the stored models are checked by the workstation run |
| Ha-laboratory SAM/SAH snapshot histogram | required matrix | yes (committed verbatim reference files) |
| Published analysis of the 2024 T-box paper | `reference_download` — and **advisory**, never a gate | the method (rounding bound, window search, dwell inversion) on data built in the test |
| Extraction vs MASH-FRET simulated truth | required matrix | yes |

## (a) Extraction vs Deep-LASI

**What is compared.** Tether's native extraction — donor spot coordinates and the
per-molecule integrated donor/acceptor traces — against the Deep-LASI `.mat` export for
the same movie. The scorers are in `src/tether/project/oracle.py`: `match_coordinates`
(greedy unique nearest-neighbor), `coordinate_rms`, `pooled_pearson`,
`evaluate_extraction`.

**Reference data.** The UCKOPSB movie plus its `.tdat`, `.tmap` and Deep-LASI `.mat`
export. The ≈0.9 GB movie is **not in the repository** — the reason is size, not
licensing: `.github/workflows/large-fixtures.yml` records that it "is NOT committed (not
even to LFS; it is too large to host on the repo's LFS budget)", while the specification
states the lab *does* hold redistribution rights, which is why the cropped
slice below could be committed at all. The enforcing test is a `workstation` test: it finds
the movie and its files through the name table of the workstation runner, under the neutral id
`lab-01`, and no CI job runs it. A committed 4-molecule slice
(`tests/fixtures/deeplasi_export_slice.mat`, `tests/fixtures/deeplasi_traces_slice.txt`)
covers the readers on every matrix run.

**More acquisitions.** Nine more acquisitions of the same laboratory (`lab-02` to `lab-08`,
`lab-10`, `lab-12`) have the same bundle. The workstation run scores each one alone with the
limits below (`tests/test_deeplasi_agreement.py`,
[ADR-0115](adr/0115-deep-lasi-acquisitions-in-oracle-a-and-how-a-failure-is-handled.md)). A
failure on one of them is a finding to investigate, never a reason to change a limit. Each
Deep-LASI export is read from a frozen copy whose provenance record gives the file's date,
because the exports were made in Deep-LASI's window.

A scripted Deep-LASI run (`scripts/reference/matlab/deep_lasi.py`) at a pinned commit repeats
the export of video 010: the same 250 molecules, positions and raw traces. It cannot make the
correction factors, which come from a later step in the window. The workstation run also
compares Tether's corrected FRET on each export, with no limit, with the γ-corrected formula
on Deep-LASI's exported factors. On video 010 the median absolute difference of the molecule
means is 0.084. The formulas agree: the gap came from the 3-frame γ level windows Tether
used then and from few molecules with their own γ (ADR-0115,
[#1206](https://github.com/bioedca/tether/issues/1206)).
The corrected FRET saved in Deep-LASI's state does not depend on γ, so it is not the reference.

**Frozen tolerance.** Module constants in `src/tether/project/oracle.py`, not a JSON file:

| Constant | Value | Role |
| --- | --- | --- |
| `RECALL_THRESHOLD` | 0.95 | gated |
| `MATCH_TOL_PX` | 2.0 | gated (match tolerance for recall) |
| `PEARSON_THRESHOLD` | 0.95 | gated — **donor channel only** |
| `RMS_THRESHOLD_PX` | 0.5 | gated when registration is fit natively |

These are the extraction bars as reframed by [ADR-0022](adr/0022-m1-acceptance-reframe-and-close.md);
the specification's original extraction gate asked for 1 px recall and r ≥ 0.99 on both channels.

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
localizers on human-curated picks; the oracle extraction is donor-anchored
([ADR-0015](adr/0015-donor-anchored-colocalization.md)), so the acceptor is read at the
mapped donor position and its Pearson carries the map's molecule-domain scatter; and a
bidirectional colocalization filter was measured to collapse recall to ≈0.66. A new
extraction defaults to that filter, the partner-in-both rule
([ADR-0127](adr/0127-colocalization-policy-partner-in-both-by-default-or-donor-anchored.md)),
so the oracle test and `scripts/run_m1_oracle.py` select `donor-anchored` by name and keep
the frozen numbers.

**Enforcing tests.** `tests/test_oracle.py::test_extraction_meets_m1_acceptance_on_uckopsb`
— `@pytest.mark.workstation`, so no CI job collects it
([ADR-0071](adr/0071-private-data-checks-run-from-a-workstation-task.md)). Until
[#699](https://github.com/bioedca/tether/issues/699) it was
a `large` test that **skipped every time** in the weekly `large-fixtures` job, because the
movie is absent from every GitHub-hosted runner (the weekly run of 2026-07-20 reports
`test_extraction_meets_m1_acceptance_on_uckopsb SKIPPED`, summary `7 passed, 4 skipped`), and
the numbers in the table above come from the local run recorded in ADR-0022. Now the weekly
scheduled task on the maintainer's workstation runs it
(`scripts/workstation/run_private_checks.py`, [Running the private-data
checks](maintainers/workstation-runner.md)), with the `.tdat` detector settings and the 2 px
gate, and writes its outcome and summary numbers (matched fraction, position RMS, both Pearson
medians, precision and the molecule counts) to the run's result file. The same run executes
the two intake tests and the five reader tests on the same bundle
(`tests/test_intake.py`, `tests/test_deeplasi.py`, `tests/test_register.py`,
`tests/test_tdat.py`), which are `workstation` tests too.
`scripts/run_m1_oracle.py --data-dir <staged copy>` reproduces the same configuration
as a one-shot report ([#555](https://github.com/bioedca/tether/issues/555)): it imports
the `.tmap`, passes the unique `.tdat` to extraction, scores at the frozen `MATCH_TOL_PX`,
and gates registration RMS only when the map is fit natively (an imported `.tmap` reports
it as not applicable). Its own behavior is pinned without the movie by
`tests/test_run_m1_oracle.py`.

**Newest recorded run.** On the maintainer's workstation on 2026-11-14, at commit `19796a9`
on `main` (a scheduled run of the workstation task): **passed**, matched fraction 0.984 (246 of
250 curated molecules), donor Pearson median 0.982, acceptor Pearson median 0.854. The first
scheduled run that held this check, earlier that day, and the run by hand of the pull request of
[#699](https://github.com/bioedca/tether/issues/699) on 2026-11-13 gave the same numbers. The
numbers agree with ADR-0022. The record is
[`docs/validation/oracle-a-last-run.json`](validation/oracle-a-last-run.json), one check entry of
a workstation result, and `tests/test_oracle_a_record.py` keeps this paragraph and the record in
step.

Each of the five files of video 010 is pinned by its SHA-256 and size, and a check that
reads one stops when the file is not the pinned one; the pins were measured on the workstation
on 2026-11-13, and they are the full digests the fixture provenance table
records. A deliberate regression (the donor traces reversed in time) made the same check fail at the
donor Pearson gate; the demonstration is recorded on
[#699](https://github.com/bioedca/tether/issues/699).

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
mean-sorted state relabeling: `state_count_fraction`, `state_mean_abs_delta`,
`viterbi_agreement`, `relative_elbo`. Parity is statistical, never bit-exact — tMAVEN
self-reseeds ([ADR-0007](adr/0007-parity-is-statistical.md)).

**Reference data.** `tests/fixtures/smd_4mol.hdf5` (4 molecules, 2 states, cross-seed
anchored on its own first run) and the Git-LFS `tests/fixtures/large/smd_281mol.hdf5`
(281 molecules, 4 states) against the committed reference model
`tests/fixtures/large/model_281mol.hdf5`.

**Frozen tolerance.** `schema/parity_tolerance.json` — `schema_version` 1,
`frozen_at_milestone` `"M0.5"`, `measured_utc` `"2026-11-15"` (the re-measure on the pinned
build, below). The default row at
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
permissive of the specification's provisional default and the measured worst case with a 0.5
margin. `$.provisional` holds the same four numbers as `$.tolerance` — the measured spread
confirmed the defaults rather than widening them
([ADR-0009](adr/0009-parity-metrics-and-freeze.md)).

**Measured result.** 20 self-reseeded fits per fixture; 39 recorded comparisons pooled
across the two fixtures. `$.pooled_worst` for vbconhmm:

| Metric | Pooled worst case |
| --- | --- |
| `state_count_fraction` | 1.0 |
| `state_mean_abs_delta` | 6.807253372098643e-09 |
| `viterbi_agreement` | 1.0 |
| `relative_elbo` | 5.878506758245325e-10 |

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
every fit run from the GUI, keep tMAVEN's own behavior.

**Which build each tolerance was measured on.** Both `method` blocks name a build
(`sidecar_python_version`, `tmaven_commit`) rather than an interpreter path, and both carry a
`build_provenance` string. `scripts/measure_parity.py` grew a build probe on 2026-07-20, so the
default row's re-measure of 2026-11-15 recorded its two values at measure time. The ebFRET
values predate the probe and are a post-hoc attribution:

| | `$.method` (M0.5, vbconhmm) | `$.measured_by_method.ebhmm.method` |
| --- | --- | --- |
| `measured_utc` | 2026-11-15 | 2026-07-08 |
| `sidecar_python_version` | 3.12.13 | 3.12.13 |
| `tmaven_commit` | `10f4230b…` (2025-10-05) | `10f4230b…` (2025-10-05) |
| How the values were obtained | captured at measure time by the build probe of `scripts/measure_parity.py`, in the re-freeze of [ADR-0110](adr/0110-idealization-parity-gaps-per-trace-transition-matrix-refreeze-and-bound-lock.md) | re-derived from the run's own inputs — `.github/workflows/sidecar-measure.yml` (dispatch run 28963324581, whose log records `python 3.12.13` and the resolved `@10f4230`), `sidecar/conda-lock.yml` and the workflow's pinned `TMAVEN_SPEC` |
| Same tMAVEN commit and CPython as the live gate? | Yes | Yes |

The pin every live assertion uses is `10f4230b6d13c6d2ad67b05d801696b4a40eff4a` on CPython
3.12.13 — `TMAVEN_SPEC` in `.github/workflows/sidecar.yml`, `python=3.12.*` in
`sidecar/environment.yml` resolving to 3.12.13 on all four platforms in
`sidecar/conda-lock.yml`, and the same commit recorded in `NOTICE` and
`scripts/setup_sidecar.py`. Those two fields are all either `method` block records about its
build: neither records the rest of the resolved sidecar lock, so "the same build" on this page
means the same tMAVEN commit on the same CPython, not an identical dependency set.

> Re-freeze on the pinned build. The **default** frozen tolerance — the `$.tolerance` row
> that gates the required `sidecar / parity` vbconhmm fits — was first measured against tMAVEN
> `71cfa1af` (2025-05-06) on CPython 3.9.23, 13 upstream commits behind the pinned `10f4230b`.
> On 2026-11-15 it was re-measured on tMAVEN `10f4230b` on CPython 3.12.13, the build the
> sidecar, CI and the installers run (#545, ADR-0110), with
> `scripts/measure_parity.py --n-runs 20` and **no** `--cross-seed`, so `smd_281mol` is anchored
> on the committed `model_281mol.hdf5` as the live gate asserts. The worst cases are 1.0 on both
> floors and 6.81e-09 / 5.88e-10 against ceilings of 0.02 and 0.01, and the freeze again
> confirmed the specification's provisional defaults: every bound stayed. The earlier build,
> date and pooled worst cases are kept under `$.refreeze.previous`. The ebFRET block has no
> such gap: it was measured on this build from the start. The script writes a full replacement of its `--out` file, so it was pointed at a
> scratch file and only the re-measured `method`, `spread_by_fixture` and `pooled_worst` were
> carried into the artifact; everything else in it — the per-method blocks, `$.coverage` with
> its committed `"vbconhmm (vb Consensus HMM)"` quoted below, and `$.transition_matrix` — was
> kept by hand.

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
`test_281mol_ebfret_cross_seed_matches_within_frozen_tolerance`, which also assert the
transition-matrix term (below). On the required matrix,
`tests/test_parity.py` checks the artifact against itself:
`test_frozen_artifact_covers_its_own_measured_evidence` requires *every* recorded per-run
value to satisfy `$.tolerance` and requires `$.provisional` to still equal the `PROVISIONAL`
constant in `src/tether/idealize/parity.py` — a *drift* check between the two, not a pin to
the literal numbers of the specification, and
`test_per_method_tolerances_cover_their_own_measured_evidence` requires every per-method
tolerance to carry the four bounds and to be satisfied by its own recorded evidence.

Be precise about what that does and does not catch, because it is a validation claim.
The core assertion in [`_assert_spread_within`](https://github.com/bioedca/tether/blob/main/tests/test_parity.py) is `recorded value ≥ floor` /
`recorded value ≤ ceiling`, which on its own would let a pull request satisfy a bound by
*deleting* the runs that stressed it, so the same helper additionally pins the evidence
itself: the measured fixture set, and for each fixture the SMD path, the reference anchor,
the fitted state count and the comparison count, are pinned to [`_EXPECTED_EVIDENCE`](https://github.com/bioedca/tether/blob/main/tests/test_parity.py) (the
run00-anchored `smd_4mol` spread at 2 states and 19 of 20 runs; `smd_281mol` at 4 states and
20 runs against `tests/fixtures/large/model_281mol.hdf5`) and, for the per-method blocks, to
[`_EXPECTED_EVIDENCE_BY_METHOD`](https://github.com/bioedca/tether/blob/main/tests/test_parity.py) (ebFRET on `smd_281mol` at 4 states, run00-anchored, 19).
Each count is cross-checked against that block's `$.method.n_runs_per_fixture`, with the
anchored-or-not mode taken from the pin, not read back from the artifact. Every fixture must
carry all four metrics in their declared direction, and each summary's
`n`/`min`/`max`/`mean`/`worst` is recomputed
from its own `values` list with the production `SpreadSummary`. So on every pull request
these two tests fail on a bound **tightened** below its own evidence, on a deleted fixture,
on a dropped or truncated run — including one removed cleanly with every summary statistic
recomputed — on a re-labeled reference anchor, SMD path or state count, on a
`$.provisional` that has drifted from `PROVISIONAL`, and on a per-method
tolerance with no `measured_by_method` entry. The anchor pin is a check on the *strings the
artifact records*: it asserts the frozen block still names the anchor it was measured
against, not that the named model file exists or is unchanged — that is Git-LFS plus the
live sidecar fit. They do **not** fail on a **loosened** bound:
widening a ceiling or lowering a floor leaves every recorded value comfortably inside it. Nor
do they fail on a pull request that edits `$.provisional` and `PROVISIONAL` *in lockstep* —
only one of them alone. The live `sidecar / parity` arm cannot catch a loosened bound
either — a looser tolerance only makes its assertions easier to pass. A separate test pins the
bounds (below); nothing pins `$.provisional` to its committed values.

What the suite does still enforce against loosening is the *shape* of a tolerance block rather
than the magnitude of any bound inside it, plus one ordering between two blocks:

- Every block must carry exactly the four bound keys — checked by
  `test_load_frozen_tolerance_returns_the_four_bounds` for `$.tolerance` and by
  `test_per_method_tolerances_cover_their_own_measured_evidence` for each
  `$.tolerance_by_method` entry — so a bound cannot be loosened by deleting it. Deleting any
  one of the eight fails at least two tests.
- The four `$.tolerance` values must additionally be finite, so an infinite ceiling there also
  fails `test_load_frozen_tolerance_returns_the_four_bounds`. The per-method bounds carry no
  finiteness check: `$.tolerance_by_method.ebhmm.relative_elbo_max` set to infinity passes.
- `test_load_frozen_tolerance_selects_per_method` asserts ebFRET's state-count floor is strictly
  below the default one, so it fails once `$.tolerance.state_count_min_fraction` is dropped to
  ebFRET's frozen `0.5249` or below (0.5249 and 0.40 fail; 0.5250 and 0.60 pass).

Widening any of the eight bounds to some *other* finite value was caught by nothing until #545,
as long as the default state-count floor stayed above `0.5249`. Now
`test_frozen_bounds_match_the_accepted_copy` holds a literal copy of every frozen bound — the
eight above and the two transition-matrix ceilings — in [`_ACCEPTED_BOUNDS`](https://github.com/bioedca/tether/blob/main/tests/test_parity.py) in
`tests/test_parity.py`, outside the file, and fails on any difference; it names a widened bound
`WIDENED`. `test_widening_any_one_frozen_bound_fails` widens each bound alone by 1e-4 and checks
that it is reported. A deliberate re-freeze changes both copies in one pull request, under an ADR
([ADR-0110](adr/0110-idealization-parity-gaps-per-trace-transition-matrix-refreeze-and-bound-lock.md)).

**Transition matrix.** Consensus VB-HMM and ebFRET are also compared on their transition
matrix: `transition_matrix_abs_delta` is the largest `|Δ|` between the two row-normalized
matrices (`norm_tmatrix`, the per-frame transition probabilities), both in canonical
mean-sorted order, and `inf` when the state counts differ or a matrix is missing. It is frozen
apart from the four-bound rows, under `$.transition_matrix`, because the specification gives no
provisional default for it: the measured worst case × 1.5, rounded up to the four decimal places
of the other bounds and never below `0.0001` (`freeze_transition_matrix`).

| Method | Evidence | Pooled worst | Frozen `transition_matrix_abs_delta_max` |
| --- | --- | --- | --- |
| `vbconhmm` | the re-measure above: 19 cross-seed comparisons on `smd_4mol`, 20 against `model_281mol.hdf5` | 3.915569757317883e-09 | 0.0001 |
| `ebhmm` | 20 unseeded fits of `smd_281mol`, 19 cross-seed comparisons (2026-11-15) | 0.029381270392564246 | 0.0441 |

The ebFRET four-bound row is not re-measured by this. Its 2026-07-08 evidence is unchanged.
The three population arms of `tests/test_parity_sidecar.py` assert the term against these
ceilings, and `test_transition_matrix_block_covers_its_own_evidence` checks the block against its
own recorded runs on the required matrix.

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

> Gap, stated plainly. The specification asks for per-trace **vbFRET** parity on the small fixtures as
> well as the consensus fit. `$.coverage.measured_methods` records only
> `"vbconhmm (vb Consensus HMM)"`, while `$.coverage.applied_to` lists per-trace vbFRET. The
> vbconhmm-measured tolerance is therefore *applied* to per-trace vbFRET without a per-trace
> measurement, and no committed test fits `model_type="vbfret"` against it.

### Measured traces with a stored vbFRET path

This part compares with the **original vbFRET_nov12** output on measured data, not with
standalone tMAVEN, so it adds evidence and replaces none of the parity above
([#797](https://github.com/bioedca/tether/issues/797),
[ADR-0090](adr/0090-measured-vbfret-paths-are-a-dwell-reference.md)).

**Measured data.** Two whole condition folders of measured two-color traces by Marzano et
al., CC-BY-4.0, each trace with the per-trace vbFRET_nov12 idealization the authors analyzed:
PPR-RNA `Validation_data/RNAanticognate` (114 traces, Zenodo 10.5281/zenodo.11323228;
Nucleic Acids Res. 52:14154, 2024) and Hsp70/Hsp90 `KJG/KJG_231027` (68 traces, Zenodo
10.5281/zenodo.17118152; Sci. Adv. 12:eaeg5464, 2026). The fixtures hold the original `.dat` bytes; a reader under
`tests/` reads them (the specification's "What the tests can use" rules). Their manifest ids start with
`marzano-`.

**Deterministic part (required matrix, three operating systems).** The authors' own analysis
code, `nmarzano/smFRET_module` v1.0.3 (MIT), ran once outside the product on the fixtures. Its
dwell and transition tables are committed, and
`tests/test_marzano_vbfret.py` asserts that Tether's dwell code and transition density, read
from a `.tether` project file that holds the stored paths, give the same state values, the same
durations in frames and the same order: 640 PPR and 465 chaperone transitions. Four
definitions differ, and the test goes through each one:

- smFRET_module's dwell table keeps the last run of a molecule; Tether drops it, because it is
  right-censored. smFRET_module's transition table is every run except the last, which is
  Tether's `include_first=True` set exactly.
- Tether drops the first run by default. So does smFRET_module's own
  `cleanup_dwell(first_dwell="delete")` before its dwell fits.
- smFRET_module drops each frame whose raw FRET is not inside (-0.5, 1.5) and joins what is
  left. As `NO_STATE` frames, Tether's dwells agree. Tether's transition density counts no
  transition across such a gap, smFRET_module does: 2 PPR transitions, no chaperone one.
- A molecule with only one run gives no dwell and no transition in either program.

A change of one frame in the dwell rule fails this test: each dwell one frame longer, or the
run boundary one frame later, fails 6 of its 15 cases, and the transition density's initial
point one frame earlier fails 2.

**Live part (advisory, sidecar).** `tests/test_marzano_vbfret_sidecar.py` runs tMAVEN's own
per-trace variational-Bayes HMM (`tracelevel_vbhmm_modelselection`: 1 to 5 states for each
trace, the highest evidence kept) in the pinned sidecar, with the paper settings that tMAVEN
has (at most 5 states, prior β = 0.5; vbFRET's μ = 1.5 has no tMAVEN counterpart), and
compares each fitted path with the stored one. No tolerance is frozen; the maintainer accepts
one first. Measured on tMAVEN `10f4230` (CPython 3.12.13), 2026-11-13, seed 797, with the
range over seeds 1, 2 and 797 after it:

| Measure | PPR (114 traces) | Chaperone (68 traces) |
| --- | --- | --- |
| Traces with the same number of occupied states | 9.6 % (9.6 to 14.0 %) | 19.1 % (16.2 to 20.6 %) |
| Median state-mean difference, same-count traces | 0.0053 (0.0048 to 0.0098) | 0.0115 (0.0057 to 0.0115) |
| Largest state-mean difference, same-count traces | 0.0141 (0.0141 to 0.2147) | 0.6243 (0.0586 to 0.6243) |
| Frames at the nearest stored level (Viterbi agreement) | 0.9394 (0.9348 to 0.9394) | 0.9414 (0.9042 to 0.9414) |

tMAVEN's per-trace fit selects more states than vbFRET_nov12 did. vbFRET kept 1 state in 86 of
the 114 PPR traces; tMAVEN kept 1 state in 8. Where the two programs use the same number of
states, the levels agree to about 0.01 FRET. Most frames sit at the nearest stored level, but
that measure maps each fitted level onto a stored one, so it does not count the extra states.
The difference in state number is the open finding of this comparison; a frozen tolerance needs
the maintainer's decision first.

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

**What is compared.** The held-out precision@k of the trained per-condition ranker against
the file-order baseline, per video, aggregated as the **median across videos** and reported as
an uplift in percentage points. Two protocols make that measurement
([ADR-0114](adr/0114-leave-one-video-out-is-the-second-held-out-ranker-protocol.md)):

- **Prequential** (interleaved test-then-train): each video is scored by a model trained on the
  videos curated before it. The first video is skipped. This is the ship gate.
- **Leave-one-video-out**: each video is scored by a model trained on every other video of the
  condition, never on itself. The first video is scored too, and which videos train each
  scorer does not depend on the curation order. It is reported beside the gate, at the same
  bar. The store entry point evaluates one condition at a time.

**Frozen tolerance.** `DEFAULT_SHIP_BAR_PTS = 10.0` in `src/tether/ml/prequential.py` — a
Python constant, not a JSON artifact. Asserted exactly by
`tests/test_ml_prequential.py::test_default_ship_bar_is_ten_points`, which pins
`DEFAULT_SHIP_BAR_PTS == 10.0` as the specification's value, and both protocols default to it
(`test_lovo_keeps_the_ship_bar_and_k`). The rationale that apparent precision@k is *not* the
gate is [ADR-0034](adr/0034-gradient-boosting-quality-ranker.md).

**Measured result.** None on real data. Every enforcing test builds synthetic or
programmatic project files; no committed artifact records a precision@k uplift measured on a real
labeled video set (withheld, see the gap below). What is
proven is that both *protocols* behave: the prequential walk skips the first video rather than
scoring it against nothing, leave-one-video-out trains each video's scorer on exactly the other
videos' rows and labels of one condition, a ranker equal to file order yields zero uplift and does not ship, a
non-finite score raises instead of being ranked last, a video split over two folds raises, and
a single-movie project raises rather than inventing a held-out video.

**Enforcing tests** (all unmarked — required 3-OS matrix): `tests/test_ml_prequential.py`
(pure core with fake scorers, including `test_perfect_ranker_beats_file_order`,
`test_ranker_equal_to_file_order_is_zero_uplift_not_shipped`,
`test_no_trainable_prior_raises_never_fabricates`,
`test_lovo_prior_is_every_other_video_never_itself`,
`test_lovo_priors_hold_the_same_rows_whatever_the_fold_order` and
`test_lovo_duplicate_movie_id_raises`) and `tests/test_project_prequential.py`
(project-integrated: `test_gate_ships_on_separable_multivideo`,
`test_gate_uplift_is_held_out_not_apparent`, `test_custom_ship_bar_can_withhold_a_ship`,
`test_single_movie_project_has_no_held_out_video`,
`test_lovo_gate_ships_on_separable_multivideo`,
`test_lovo_trains_on_every_other_video_never_itself` and
`test_lovo_refuses_a_project_whose_labels_span_conditions`).

> Gap, stated plainly. Both protocols exist, but neither has been measured on a real labeled
> multi-video set. No curated multi-video project with accept/reject labels exists yet, so the
> real-data leg is withheld
> ([ADR-0114](adr/0114-leave-one-video-out-is-the-second-held-out-ranker-protocol.md)), and it is
> tracked in [#1100](https://github.com/bioedca/tether/issues/1100).

## (e) α / γ estimator edge cases

**Frozen constants** (rows of the specification's table of tunables), all in code:

| Constant | Value | Module |
| --- | --- | --- |
| `LEAKAGE_CEILING` | 0.3 | `src/tether/fret/leakage.py` |
| `DEFAULT_MIN_WINDOW_FRAMES` | 20 | `src/tether/fret/leakage.py` |
| `DEFAULT_MIN_QUALIFYING_TRACES` | 10 | `src/tether/fret/leakage.py` |
| `GAMMA_CEILING` | 5.0 | `src/tether/fret/gamma.py` |
| `DEFAULT_GAMMA_HALF_WINDOW` | 20 | `src/tether/fret/gamma.py` |

The first three are locked by `tests/test_fret_leakage.py::test_defaults_match_prd_11_2`.

**What is compared.** α (leakage) against **synthetic known-α ground truth**, and γ against
**synthetic known-γ recovery** (`test_recovers_known_gamma`, parametrized over γ ∈ {0.5,
1.0, 2.0, 4.0}) plus a **reference-formula parity check** (`test_reference_formula_parity`)
against an independent in-test transcription ([`_reference_gamma`](https://github.com/bioedca/tether/blob/main/tests/test_fret_gamma.py), deliberately computed a
different way — explicit slices, no shared helper). That transcription is of the δ = 0,
**bare-`I_D`** simplification of Deep-LASI's `deep_autocorrect_2color.m:118-130`, which is
*Tether's own* convention, so the check proves self-consistency, not agreement with
Deep-LASI: ADR-0028 deliberately chose bare `I_D` over Deep-LASI's `(1+α)`-scaled donor and
records that "Tether's γ is systematically ≈ `(1 + α)` times Deep-LASI's on the same step"
— ≈ 9 % at α ≈ 0.09. The actual Deep-LASI-median comparison is deferred (below). The edge
cases the specification names are
each a test: too-short tail, no tail when the acceptor outlives the donor, above-ceiling
and negative rejection, degenerate donor, median over qualifying traces only, and
withholding below the minimum qualifying count rather than emitting a factor from too
little data. `tests/test_fret_leakage.py::test_estimate_empty_qualifying_never_medians_empty`
turns any `RuntimeWarning` into a failure so an empty median can never leak through.

**The γ level windows.** Since [ADR-0130](adr/0130-the-gamma-levels-are-averaged-over-20-frames-each-side.md)
each level averages 20 frames each side of the step. The parity check runs on that default,
on the whole segments and on 3 frames. `test_wider_windows_scatter_less_than_three_frames`
shows that over 200 noise draws the default's spread is under half the 3-frame estimate's.

**Total failure → apparent E, so a missing factor never gives a NaN E.** A missing or
non-finite α or γ routes the molecule to apparent E. A frame with zero total intensity is
still NaN in apparent E, because the ratio is not defined there. That is not a correction
failure.

`tests/test_project_correct.py` covers the path that the specification singles out:
`test_total_failure_falls_to_apparent_never_nan`,
`test_fresh_store_nan_factors_is_total_failure`, `test_zero_gamma_boundary_is_total_failure`,
`test_negative_gamma_is_total_failure`, `test_manual_override_rescues_total_failure`,
`test_partial_alpha_override_without_gamma_stays_apparent`, and
`test_gamma_override_must_be_positive_finite` (parametrized over `0.0`, `-1.0`, `nan`,
`inf`). The design rule is [ADR-0003](adr/0003-apparent-e-never-nan.md). All of these run on
the required matrix.

**The conjunctive α band on real data (workstation).** The band of the specification
(relative-median difference ≤ 20 % *and* both medians within 0.05–0.2, never the band alone) is
`tests/test_leakage_alpha_band.py`, a `workstation` test
([ADR-0133](adr/0133-the-leakage-band-runs-on-the-donor-only-sample-and-the-gamma-leg-stays-withheld.md)).
The donor-only α comes from the lab's Cy3-only calibration acquisition (`donly-01`), read with the
test-only trace reader of the public Deep-LASI check (#716): the tail estimator with every frame before the donor
bleach as its window, so the gates are the tail estimator's own. The tail α is the median over the
pooled molecules of the FRET series that sample calibrates (`lab-03` to `lab-08`), with Tether's
bleach frames. The band's arithmetic and its two limits are checked on the required matrix in the
same file, against the specification's row.

**The first run passed** (2026-11-19). The donor-only α was 0.1200 (89 of 91 molecules
qualifying) and the pooled tail α of the series 0.1012 (260 of 971), a relative difference of
17.0 % against the 20 % limit, with both medians inside 0.05–0.2.

> Not implemented — the γ leg of this oracle.
>
> The **γ within ±10 % of the Deep-LASI median** oracle of the specification, and its looser
> CI-overlap check, stay deferred. They need Deep-LASI's per-molecule γ and its per-frame
> classification, and no available file holds them: not the vendored export
> ([ADR-0028](adr/0028-gamma-acceptor-bleach-step-estimator.md)), not the lab's saved Deep-LASI
> states, and not the public Figure 3 state
> ([ADR-0133](adr/0133-the-leakage-band-runs-on-the-donor-only-sample-and-the-gamma-leg-stays-withheld.md)).
> Deep-LASI's γ uses its per-frame classes, not its `BleachFrame`, so the shared frame set is
> those classes once a state is saved after Deep-LASI's classification and automatic correction
> ([#1244](https://github.com/bioedca/tether/issues/1244)).

## (f) Round-trip integrity and schema migration

**What is compared.** Three things: the `.tether` HDF5 structure declared by the code
against the committed golden manifest; a fresh project reopened with every frozen field
intact; and the SMD interchange write → read superset round-trip.

**Frozen artifact.** `schema/schema_frozen.json`, the golden manifest read by
`tests/test_schema.py` as `GOLDEN_PATH`.

**Tolerance.** Structural, not numeric: **additive-only**. Additions pass; removals,
renames, dtype changes, field reordering, mid-group insertions and a decremented
`schema_version` all fail. In full, a violation is any of: a frozen field removed or
renamed, or its dtype or shape changed; a field reordered or inserted before the end; a
frozen group or dataset removed; a dataset's dtype or `maxshape` changed; a frozen
attribute removed or its dtype changed; the root `format` changed; and `schema_version`
missing, decreased, or raised without a regenerated golden. This is the schema freeze,
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

**Schema migration is not part of (f) yet.** There is no migration machinery and no
migration test, and none is needed: the freeze is additive-only by construction (ADR-0005)
and `SCHEMA_VERSION` has never been raised, so there is no version step to migrate across.
A search for `migrate`/`migration` in `tests/`, `src/tether/io/` and the workflows returns
nothing. The specification's project-lifecycle rules name the condition that changes this:
the **first schema-version step**, a structural change that raises `SCHEMA_VERSION` with an
ADR, carries the backup-and-migrate path and a test that exercises it, and that test joins
(f) in the same pull request. The other half of those rules, that a file newer than the app
is refused, is tested now (`test_assert_compatible_refuses_newer_file`). Maintainer decision
of 2026-11-13 on [#547](https://github.com/bioedca/tether/issues/547).

## (g) Photobleaching detector

**What the specification asks for, and why it changed.** The specification asks for a
comparison of per-channel first-bleach frames against the `.mat` `pacc`/`pdon` fields within
its tolerance of ±2 frames. [ADR-0026](adr/0026-photobleach-detection-and-window-default.md) found this
field is not a per-molecule oracle: in `DeepLASI_MAT_export_…010.mat`, `pacc` and `pdon`
are `uint8` arrays of shape 250×1 whose value is the **constant 60 for all 250 molecules**,
while the background-subtracted traces bleach at ≈97, ≈644 and ≈1693. Gating on it would require the
detector to return 60 for a molecule that demonstrably bleaches at 97. It is an
acquisition-wide marker, not ground truth.

**What is validated instead** (option C in ADR-0026, maintainer-approved 2026-07-02):
**reference-formula parity** against a line-for-line transcription of tMAVEN's
`photobleaching.py` formulas (`normal_ln_evidence`, `normal_mu_ln_evidence`,
`ln_likelihood`, `get_point_pbtime`, `pb_ensemble`), kept in the test file itself as
[`_ref_*`](https://github.com/bioedca/tether/blob/main/tests/test_fret_photobleach.py) helpers — plus recovery of a **synthesized known step** to the same ±2-frame
tolerance. Read the parity leg for what it is: these tests are unmarked, so they run on the
required matrix, where tMAVEN is not installed and is never imported. What is proven is
that the O(T) prefix-sum rewrite is faithful to the O(T²) reference *formulation*; a
transcription error in the [`_ref_*`](https://github.com/bioedca/tether/blob/main/tests/test_fret_photobleach.py) helpers would not be caught, because tMAVEN itself does
not execute here. The live leg below closes that gap.

**Live leg against tMAVEN** (#706). `tests/test_fret_photobleach_sidecar.py` runs in the
required `sidecar / parity` job. On a pull request that job runs it when the diff touches
`src/tether/fret/photobleach.py`, `tests/test_fret_photobleach.py` or any other path in
its relevance filter. A child process in the sidecar environment calls tMAVEN's own
`normal_ln_evidence`, `normal_mu_ln_evidence`, `ln_likelihood`, `get_point_pbtime` and
`pb_ensemble`, with the priors from tMAVEN's own photobleach preferences. The inputs are
the synthetic traces of the unmarked tests (same seeds, same draws) and the donor, acceptor
and summed channels of the committed 4-molecule `smd_4mol.hdf5`. Tether and the [`_ref_*`](https://github.com/bioedca/tether/blob/main/tests/test_fret_photobleach.py)
helpers are both compared with that output. The algorithm is the same, so bleach frames and
the ensemble rate must be equal. Log evidence must agree to a relative 1e-14 for the
helpers (measured worst 7.1e-16, the same O(T²) sums) and 1e-12 for Tether (measured worst
6.6e-14, from the O(T) prefix sums on a trace that never bleaches); measured 2026-11-11
against tMAVEN `10f4230`. The same one-character error put into both a [`_ref_*`](https://github.com/bioedca/tether/blob/main/tests/test_fret_photobleach.py) helper and
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
`test_short_traces_do_not_raise`; and at the project level
`tests/test_project_photobleach.py::test_compute_photobleach_populates_frames_and_auto_window`
and `test_manual_window_is_not_overwritten`. Live leg (`sidecar`):
`tests/test_fret_photobleach_sidecar.py::test_ref_helpers_match_live_tmaven`,
`test_tether_detector_matches_live_tmaven` and
`test_tmaven_default_priors_are_the_ones_tether_and_the_helpers_use`.

## (h) Histogram and cross-correlation vs smfret-analysis2

**What is compared.** The specification makes two model-free lenses first-class: the apparent-E histogram
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
reads the trace files; the product has no importer for them (the specification leaves this
legacy format out of scope).

**Two exact conversions, no tolerance.** Tether and the script use different conventions in
two places, and the tests convert each one exactly before they compare:

- *Bin edges.* `hist(fret, -0.2:0.01:1.2)` puts the edges at the midpoints of the centers and
  closes each bin on the right; `apparent_e_histogram` closes each bin on the left. Only a
  sample exactly on an edge is binned differently. In these traces there is one sample, at
  0.375. The test moves each such sample one bin down and requires that there is exactly one.
- *Cross-correlation normalization.* The script divides the sum at lag k by N − k and by
  MATLAB's `std`, which divides by N − 1. Tether divides every lag by N and uses the population
  σ (kept by the maintainer). The exact factor from Tether's value to the script's is
  (N − 1) / (N − k), at Tether's lag −k (the script correlates donor(t) with acceptor(t + k)).

With the conversions, Tether's histogram equals the `raw` column of `total_FRET.csv` and
Tether's cross-correlation equals `total_XC.csv`, each to 1e-14 absolute: the files write 15
significant digits, and the rest is float64 round-off of the FFT and of the sums. Measured
2026-11-12: largest difference 4.4e-16 for the histogram and 3.1e-16 for the cross-correlation. Without the edge rule the histogram misses by 7.5e-6 in the
0.37 and 0.38 bins; with N / (N − k) as the only factor the cross-correlation misses by more
than 1e-4.

**Out of scope.** The successive-frame cloud (the next section), the smoothed and filtered histogram
columns, the heat map, trace selection and background subtraction. The files under
`test-data/ground-truth/` of the same commit are not used: the commit's scripts do not
reproduce them.

**Enforcing tests** (all unmarked — required 3-OS matrix):
`tests/test_smfret_analysis2_reference.py::test_tether_histogram_equals_total_fret_raw_column`,
`test_tether_cross_correlation_equals_total_xc`,
`test_the_lag_factor_alone_does_not_reach_the_file` and
`test_the_fixtures_are_the_pinned_commit_of_smfret_analysis2`.

### Successive-frame FRET cloud vs the FRET_cloud script

**What is compared.** The successive-frame FRET cloud plots apparent E at frame *i* against
apparent E at frame *i* + 1 ([#710](https://github.com/bioedca/tether/issues/710)). It is the plot of
the laboratory's FRET_cloud scripts (FRET_cloud scripts, Eduardo Campos Chavez, 2025, unpublished).
[ADR-0105](adr/0105-the-successive-frame-fret-cloud-and-its-script-reference.md) records the
analysis and the reference.

**Reference data.** The scripts are not public and are not in the repository.
`scripts/reference/matlab/pins.toml` pins `FRET_cloud_contours_centroids.m` by its SHA-256, and
`scripts/reference/matlab/fret_cloud.py` ran it unchanged, one time, in MATLAB R2023b (Statistics
and Machine Learning Toolbox 23.2) on the 76 trace files of section (h), on 2026-11-15. The frozen
output is under `tests/fixtures/reference/fret-cloud-scripts/sa2-traces/`: the 3800 pairs, the
200 × 200 density, the nine contour levels, the four k-means centroids and the toolbox version,
with a `provenance.json` that holds the citation, the script hash and the input hashes.

The script reads column 4 of each file as E. Column 4 of these files is zero, so the run gave the script
copies whose column 4 is `acceptor / (acceptor + donor)`, Tether's apparent E. The script uses the
first 50 pairs of each file, so the test gives Tether the first 51 frames of each trace.

**Results** (measured 2026-11-15. Each limit is the measurement rounded up to the next power of
ten):

| Quantity | Measured | Limit |
|---|---|---|
| Pairs | equal, bit for bit | equal |
| Density, MATLAB's kernel cut applied, relative to the peak | 2.5e-15 | 1e-14 |
| Contour levels from that density, relative | 2.2e-15 | 1e-14 |
| Tether's density (no cut), relative to the peak | 1.1e-4 | 1e-3 |
| Tether's contour levels (no cut), relative | 3.2e-4 | 1e-3 |
| MATLAB's centroids after one Lloyd step of Tether | 0 | 1e-12 |
| Within-cluster sum of squares, Tether's best run against MATLAB's, relative | 4.5e-7 | 1e-6 |
| Centroids, Tether against MATLAB, in E | 8.0e-4 | 1e-3 |

MATLAB's `mvksdensity` leaves out the part of each kernel that is more than 4 bandwidths from the
pair on either axis. Tether keeps the whole Gaussian. With that cut applied in the test, the two
densities agree to round-off, and the 5216 nodes where MATLAB's density is 0 are the nodes that no
cut kernel reaches. So the cut is the whole difference. The k-means starts are random in both
programs and come from different generators. MATLAB's run ended in a local minimum with a slightly
smaller sum of squares than Tether's run with seed 0, so the centroid limits are not round-off.

**Out of scope.** The scripts' heat map, their concatenated FRET-against-time plot and their R² of
the centroids to the diagonal. The GUI entry and the export of the plot are a follow-up issue.

**Enforcing tests** (all unmarked — required 3-OS matrix): `tests/test_fret_cloud_reference.py`
and the record check `tests/test_matlab_reference.py::test_every_committed_output_matches_its_record`.

## (i) Corrected FRET vs MASH-FRET

**What it checks.** Tether's leakage correction and γ-corrected FRET against MASH-FRET's own
results, in the MASH-FRET project files that authors of MASH-FRET published with their group IIB
intron study (Zenodo doi `10.5281/zenodo.24104528`, CC-BY-4.0). MASH-FRET at commit `1a1f465`
wrote the smallest file, which is the source of the committed extract; the other files name commits
`89deb70`, `e0bc2229` and `5ab2aff0`, and one file in the 1.3.4 layout (`240b0d99`) is not read.
[ADR-0087](adr/0087-mash-fret-is-a-reference-for-corrected-fret.md) records the
reference, the convention mapping and the ALEX rule; `tests/test_mashfret_reference.py` holds the
tests.

- **Leakage.** From MASH-FRET's background-subtracted intensities and its leakage coefficient,
  Tether's corrected acceptor equals MASH-FRET's cross-talk-corrected acceptor on both lasers, and
  the donor is unchanged (relative limit 1e-15; the difference is exactly 0).
- **Leakage at a non-zero coefficient** ([#870](https://github.com/bioedca/tether/issues/870)).
  A second reference is a test project of the MASH-FRET repository (`2chan2exc.mash` at commit
  `b577928`, GPL-3.0), which MASH-FRET "1.3.2 (prev. commit: 3471fdf3)" wrote with a leakage
  coefficient of 0.0775. That project also has a direct-excitation coefficient of 0.0176, and
  MASH-FRET subtracts that term after the leakage term. So under the acceptor's laser the test
  compares directly, and under the donor's laser it adds MASH-FRET's own direct-excitation term
  back first. Tether's corrected acceptor equals MASH-FRET's on 10 molecules and both lasers
  (relative limit 1e-15; worst 1.1e-16, and 1.7e-16 on all 30 molecules of the project). The
  traces of this project are MASH-FRET test data, not a published measurement.
- **Corrected FRET.** The files hold no FRET value for each frame, only the value of each frame's
  state, and MASH-FRET sets that value to the mean of its γ-corrected FRET over the frames of the
  state. With MASH-FRET's γ, the mean of Tether's corrected FRET over the same frames equals each
  state value (relative limit 1e-13; worst 1.4e-15 on the 34 states of the committed extract and
  6.3e-14 on the 4442 states of the eight readable files of the archive). MASH-FRET's
  `1 / (1 + γ·I_D/I_A)` is Tether's `I_A / (I_A + γ·I_D)` with the same γ; no conversion factor is
  needed.
- **Teeth.** γ applied to the acceptor, or no γ, makes the FRET test fail. A sign error in the
  leakage correction (`I_A + α·I_D`) makes the non-zero leakage test fail: its worst difference
  is then 0.25.

**What it does not check.** Every file of the group IIB intron record has a leakage coefficient of
0, so the sign of the correction is checked only on the MASH-FRET test project. The comparison is
per state, not per frame. Background
subtraction is not compared, because the files hold no video. The leakage comparison uses both
lasers; the FRET comparison uses only the donor and acceptor channels under the donor's laser.
MASH-FRET's direct-excitation coefficient (an ALEX-only correction that Tether does not make) must
be 0 in the record, and in the test project, where it is not 0, the test removes MASH-FRET's own
term instead of comparing it.

**Where it runs.** The required matrix reads a committed extract of the smallest project file (18
molecules, values unchanged). The archive (236 MB) is larger than the `download` limit, so it is a
`workstation` entry of the manifest: the nightly workstation run verifies it, makes the extract again
and compares it byte for byte, and runs both checks on every file of the archive that has the
layout of that MASH-FRET generation.

## MASH-FRET analyses: histogram, states and rates

The MASH-FRET project files of oracle (i) also keep MASH-FRET's own analyses of each molecule tag
([#712](https://github.com/bioedca/tether/issues/712)): the FRET histogram of its Histogram
analysis and the Gaussian fit of it, the state sequence of each trace from its per-trace vbFRET
(1 to 5 states), and the dwell table of its Transition analysis.
[ADR-0101](adr/0101-mash-fret-analyses-are-a-histogram-and-state-reference.md) records what each
comparison can prove. A second committed extract holds the 17 molecules of one tag, "Path A 0.2 +-
0.3", of the smallest project file, with that tag's analyses (values unchanged).

- **Histogram, exact (a gate).**
  `tests/test_mashfret_analysis.py::test_tether_gives_mashfret_s_fret_histogram_exactly` bins
  Tether's γ-corrected FRET of the included frames on MASH-FRET's bins and requires every count to
  equal MASH-FRET's. It does, in the required matrix on the extract (49 bins, 10,086 frames) and on
  the workstation for all 37 FRET histograms that the eight readable files store. Without γ the
  test fails.
- **States, measured limits (a gate in the `sidecar` job).**
  `tests/test_mashfret_analysis_sidecar.py::test_tether_s_states_agree_with_mashfret_s_within_the_frozen_limits`
  runs Tether's idealization (`vbconhmm_modelselection`, seed 712) on the same traces. The limits
  are in `schema/mashfret_analysis_limits.json` with their measurements:

  | Measure | Measured | Limit |
  |---|---|---|
  | Frames whose Tether level, matched to the nearest MASH-FRET state of the molecule, is MASH-FRET's state | 0.848 | at least 0.84 |
  | Mean absolute difference of the number of states in a trace (11 of 17 equal, 5 one fewer, 1 one more) | 0.353 | at most 0.36 |

- **Populations, report only.** Tether's state fractions are 0.333, 0.384 and 0.283 (means 0.074,
  0.201, 0.374). MASH-FRET's Gaussian populations are 0.415, 0.246 and 0.340 (means 0.081, 0.200,
  0.351), each with a bootstrap SD of about 0.11. The two are different models, so no limit applies.
- **Rates, report only.** No file of the record stores a fitted MASH-FRET rate. The test prints
  Tether's exit rate of each state beside `1 / mean` of the dwell times that MASH-FRET's dwell
  table holds for the matched transition cluster: the ratio is 1.04 to 1.77, Tether faster.

**What it does not check.** The state limits hold for the pinned sidecar build and seed; a new
measurement and the maintainer's acceptance are needed to move them. No MASH-FRET rate is compared,
because none is stored.

## Histogram bootstrap vs MASH-FRET

**What it checks.** Tether's bootstrap of the FRET histogram, which resamples molecules as
BOBA-FRET does (König et al., *PLoS ONE* 8:e84157, 2013, doi `10.1371/journal.pone.0084157`),
against the BOBA-FRET bootstrap that MASH-FRET itself ran on the same traces ([#713](https://github.com/bioedca/tether/issues/713),
[ADR-0108](adr/0108-mash-fret-s-stored-bootstrap-is-the-reference-for-tether-s-bootstrap.md)). The
Path A extract of the previous section keeps that bootstrap: 100 samples of 17 molecules drawn from
the tag's 17, each molecule weighted to sum 1, with each sample's histogram and its fit of three
Gaussians. These stored values are the reference, so no MATLAB run is needed.

**How.** `tests/test_mashfret_bootstrap.py` runs Tether's bootstrap on the same FRET values, with
the same weights and 100 samples, for ten fixed seeds. Each compared value is a z-score: the
difference from MASH-FRET divided by the seed-to-seed SD of the ten seeds, times
`sqrt(1 + 1/10)`. The limit, in `schema/mashfret_bootstrap_limits.json`, is |z| ≤ 3 for every
value.

| Compared value | Largest measured \|z\| | Limit |
|---|---|---|
| SD of each histogram bin over the samples (26 bins) | 1.11 | 3 |
| Mean of each Gaussian population (0.410, 0.237, 0.352 against 0.415, 0.246, 0.340) | 1.17 | 3 |
| SD of each Gaussian population (0.111, 0.110, 0.098 against 0.106, 0.111, 0.107) | 2.20 | 3 |

Six other sets of ten seeds gave a largest |z| of at most 2.69. A bootstrap that resamples frames in
place of molecules gives |z| = 130 on the bin SD, and a test keeps that case.

**What it does not check.** One tag of one project file. The population fit is test code with
SciPy. On MASH-FRET's own samples it moves the population means by up to 0.010 from MASH-FRET's
MATLAB fit, and that difference is inside each population z-score.

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
| 4. 2-D histogram | Start-synchronized: Tether's counts equal tMAVEN's cell for cell. Post-synchronized on the analysis-window frames: the same 38 transitions in 29 molecules and the same 1608 counts, cell for cell (since #806; before it Tether lacked 2, the last frame of a dwell that runs to the analysis-window end). tMAVEN's app also counts 2 jumps at a window border (#707) |

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

## Published analysis of the 2024 T-box paper

**What is compared.** Tether's analysis functions against the published numbers of Campos-Chavez
*et al.*, *Nature Communications* **15**:6592 (2024),
[doi:10.1038/s41467-024-50885-x](https://doi.org/10.1038/s41467-024-50885-x), Source Data
CC BY-NC-ND 4.0 (see "The Source Data of the 2024 T-box paper" above). The paper made them with
tMAVEN 0.2.3 (commit 62422f9) and the authors' scripts.
[ADR-0097](adr/0097-the-2024-t-box-source-data-is-an-advisory-analysis-reference.md) records the
method, the limits and the measurements.

| Published | Sheet or file | Tether function | Result (Linux, macOS, Windows) |
| --- | --- | --- | --- |
| Representative traces, Figs. 2b, 3g, 4a, 4e | `Fig2b`, `Fig3g`, `Fig4a`, `Fig4e` | `apparent_fret` | 12,000 values, 9.8e-15 relative |
| FRET histograms, Figs. 2c, 3b, 3h, 4b, 4f, 6b, 7b | the seven sheets and their trace matrix | `apparent_fret`, `apparent_e_histogram` | 1,016,781 values in 1,440 recovered windows, each within the rounding bound (largest ratio 0.91); every bin count equal |
| intraFRET histograms, Fig. 5b (4 of 5 columns) | `Fig5b` and the `intraFRET_*` matrices | `apparent_fret` | 282,800 values, first 400 frames, within the rounding bound (0.91) |
| Survival curves, 13 panels of Figs. 2e-7f | `Fig2e` ... `Fig7f` | `survival_curve` | equal to 1.0e-15 |
| Exponential fits of the same panels | `Model`, `Residual` columns | `fit_survival` (single or double) | within 5.9e-6 of the curve's first point |
| Double-exponential parameters, Figs. 2e, 3e, 7d | `Double-Wt+Wt-4`, `Double-Wt+NCCA-5`, `Double-RAG-4` | `fit_survival` | parameters 1.3e-5 relative; errors 3.2e-3 relative after re-pairing (below) |
| TDP counts, Figs. 2d, 3c, 3i, 4c, 4g, 6c, 7c | the seven sheets with `checkTDP.py`'s grid | `transition_density` | every count equal |

The trace matrices of the archive keep 8 significant digits of each intensity, so an efficiency
calculated from them can differ from a published one by the rounding of the two intensities. The
bound is the largest change of the efficiency over the rounding interval of both intensities, which
takes in both the rounded and the unrounded denominator. Where the interval of `D + A` contains zero
there is no finite bound, and that frame is not checked. No published value falls on such a frame.
That bound, not a chosen number, is the limit. The workbook does not say which frames a histogram pools,
so each test finds the windows in the trace matrix when it runs.

**Differences the archive shows.**

1. tMAVEN's `optimize_double_surv` sorts the two rates and amplitudes of a double-exponential fit
   but not their errors: in the three panels that can be checked, each published error belongs to
   the other parameter. Tether's errors are paired correctly.
2. The archive's 5 uM WT tRNA intraFRET matrix is a copy of the 13 uM one, so that column of
   Fig. 5b has no trace data.
3. `WT+NCCA_tRNA.txt` has 1499 frames, one fewer than the windows of Figs. 3b, 4b and 4f need.
4. The intraFRET notebook skips the first line of each file, so the first 400 frames of a molecule
   start at frame 0 or at frame 1.

**Not compared.** The HMM population fits (#757), the rates and their bounds (#758), the mean dwell
time of a double exponential with its error, the TDP smoothing and spline, and the two-Gaussian
fit of Fig. 5c. Tether has no function for the last three: #1028, #1029 and #1030.

**Enforcing tests.** Required matrix: `tests/test_campos_chavez_2024.py::test_the_rounding_bound_covers_intensities_written_with_eight_digits`,
`test_find_windows_finds_each_window_and_a_value_the_matrix_lost` and
`test_dwells_from_survival_give_back_the_survival_curve`. Weekly (`reference_download`):
`test_representative_traces_have_tethers_efficiency`,
`test_each_histogram_value_is_tethers_efficiency_of_one_frame`,
`test_intrafret_histogram_values_are_tethers_first_400_frames`,
`test_survival_curve_and_exponential_fit_reproduce_the_panel`,
`test_double_exponential_parameters_and_errors` and
`test_transition_density_counts_equal_checktdp`.

## Campos-Chavez et al. 2024: the tMAVEN fits

This check fits the measured traces of Campos-Chavez et al. (*Nat Commun* **15**:6592, 2024,
doi `10.1038/s41467-024-50885-x`) again in Tether's sidecar and compares each fit with the fit the
authors published. The Source Data archive is the manifest entry `campos2024-source-data`
(CC BY-NC-ND 4.0). The test reads the published values from the archive each time and never
commits them ([ADR-0083](adr/0083-unchanged-copies-of-cc-by-nc-nd-reference-data.md)).
[ADR-0106](adr/0106-campos-chavez-tmaven-fits-are-an-advisory-reference.md) records the decisions.

**What is compared.** Seven fits: the consensus vbHMM of five samples, and the two
sub-populations of the ΔNCCA sample (Fig. 4: the composite HMM with K-means, and vbGMM). For each
fit, the published state means are the levels of its transition density sheet, and the published
populations and transitions come from its count matrix (`Kmatrix/*.txt`, global fits only). The
frames that each fit read are the frames whose FRET values its histogram sheet pools. The priors
are those of the Methods (α 1.0, β 0.25, π 1.0, a 2.5 and b 0.01, with b 0.4 for the Linker and
0.2 for the RAG mutant), and the tMAVEN 0.2.3 defaults where the Methods give none.
`tests/reference_data/campos_chavez_2024_hmm.json` gives each setting with its source.

**Limits** (`schema/campos_chavez_hmm_limits.json`): the largest deviation over seeds 0 to 9 and
757 at the pin (tMAVEN `10f4230`, the sidecar lock, 2026-11-15), rounded up at the second
significant digit. "—" marks a metric that has no published value.

| Fit | Sample | Key | States | Molecules | Means | Populations | Counts off the diagonal | Transitions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `wt` | WT T-box, WT tRNA (Fig. 2) | `vbconhmm` | 3 | 348 | 3.8e-06 | 0.00092 | 0.015 | 0.023 |
| `dncca` | WT T-box, tRNA-ΔNCCA, all traces (Fig. 3) | `vbconhmm` | 2 | 264 | 0.00024 | 1.3e-05 | 0.0011 | 0.033 |
| `ddiscriminator` | Δ-Discriminator T-box, WT tRNA (Fig. 3) | `vbconhmm` | 2 | 164 | 6.6e-05 | 5.2e-05 | 0.0047 | 0.0063 |
| `dncca_one_state` | tRNA-ΔNCCA One-state population (Fig. 4b-d) | `kmeans_vbhmm` | 2 | 192 | 0.0028 | — | — | 0.075 |
| `dncca_two_state` | tRNA-ΔNCCA Two-state population (Fig. 4f-h) | `vbgmm` | 3 | 72 | 3.7e-06 | — | — | 0.00015 |
| `linker` | Linker mutant, WT tRNA (Fig. 6) | `vbconhmm` | 3 | 197 | 0.4 | 0.48 | 0.79 | 0.56 |
| `rag` | RAG mutant, WT tRNA (Fig. 7) | `vbconhmm` | 4 | 197 | 0.37 | 0.25 | 0.59 | 0.49 |

**This is an advisory reference.** A limit measures how far the pinned tMAVEN is from the published
fit, so a failure means that the sidecar fit moved, not that the article is wrong. Five fits
reproduce the article: wild type, ΔNCCA, ΔDiscriminator and the two sub-populations. For the
Linker and RAG mutants, the pin finds a different optimum. Each limit is an upper bound: a change
that moves a fit further from the article fails it, and a change that moves a fit nearer to the
article passes it and shows only in the printed numbers.

**Seed and version.** The consensus vbHMM of `10f4230` and of 0.2.3 starts from percentiles of the
data. At the pin, the 11 seeds change the means of the wild-type, ΔNCCA and ΔDiscriminator fits by
less than 2e-8, and those of the Linker and RAG fits by less than 1.2e-6. In 0.2.3 the three
reproduced fits are the same, but the seeds move the Linker and RAG means by up to 0.020 and
0.017. The K-means start of the One-state
population depends on the seed (means 1.0e-3 to 2.7e-3 from the published ones). The same fits
with older tMAVEN, in a separate environment with the same numerical stack (the largest mean
difference, over the seeds):

| Fit | `10f4230` (pin) | 0.2.3 (`62422f9`, 2024-10-24) | 0.2.0 (`8beaeb1`, 2023-08-14) |
| --- | --- | --- | --- |
| `wt` | 3.8e-06 | 3.8e-06 | 1.6e-07 |
| `dncca` | 0.00024 | 0.00024 | 0.00024 |
| `ddiscriminator` | 6.6e-05 | 6.6e-05 | 5.2e-10 |
| `dncca_one_state` | 0.00098 to 0.0027 | 0.0003 to 0.0019 | 0.0003 to 0.0019 |
| `dncca_two_state` | 3.6e-06 | 3.6e-06 | 3.6e-06 |
| `linker` | 0.39 | 0.41 to 0.42 | 0.0036 to 0.42 |
| `rag` | 0.36 | 0.34 | 0.00011 to 0.39 |

Three results follow.

1. tMAVEN 0.2.0 reproduces the published fits most closely: the ΔDiscriminator count matrix to
   8e-10, the wild-type means to 1.6e-7 with the same transitions, and, for 3 of the 11 seeds, the
   Linker count matrix to 2e-4 (for 2 seeds, the RAG means to 1.1e-4). Its consensus fit starts
   from K-means, so the seed decides which optimum it reaches. Commit `62422f9` (0.2.3) is dated
   after the article, so the published fits were probably made with 0.2.0 or a build near it.
2. For the wild-type, ΔNCCA, ΔDiscriminator and Two-state fits, the version changes the means by
   less than 7e-5 (the One-state population depends on the seed in each version, above), and
   `10f4230` changes the transitions of the idealized paths (2.2 % for the wild type, where 0.2.0
   and 0.2.3 give exactly the published ones).
3. For the Linker and RAG mutants, the percentile start of 0.2.3 and `10f4230` reaches another
   optimum on every seed. The wild-type count matrix differs by 1.5 % off the diagonal in all three
   versions.

**The two sub-populations.** Each of their windows lies inside the window of the same molecule in
the whole ΔNCCA fit, but the two do not split that fit: 26 molecules are in both and 26 in
neither, though 192 + 72 = 264. The Two-state population is tMAVEN's plain vbGMM, the sidecar
key `vbgmm`. The sidecar key `vbgmm_vbhmm` selects one state on these traces. The weekly job
prints its result and does not gate it.

**Stored models** (`workstation`): each private tMAVEN trace file is matched to a sample by its
donor traces (a correlation of 0.99 or more with a column of the sample's matrix, which does not
see the background subtraction or one missing frame), and each stored model and model summary is
compared with the published values and with the fresh fit of seed 757. Every model is read with
`read_model`, which also opens older tMAVEN models that store `rates` as an empty group. The
result file holds numbers under neutral ids only.

The first run (2026-11-15, 24 files: 17 trace files, 4 models, 3 summaries) found the traces of
each of the five samples. Three trace files of one further sample match no matrix of the Source
Data.

The stored ΔNCCA model is the published one (count matrix to 1e-11). One of the three stored
wild-type models also agrees (means to 5.8e-4, counts to 0.04 %). The other two do not (means to
0.042 or 0.0085). The Linker summary has the published count matrix exactly and means to 3.6e-3,
0.39 from the fresh fit: the published Linker fit is the one the authors' tMAVEN found, and the pin
does not reach it. The Two-state and One-state summaries agree with the published means (to 4.7e-9
and 1.5e-3).

**Enforcing tests.** Required matrix: `tests/test_campos_chavez_hmm.py` (the settings and their
sources, the windows, the comparison on built data, the rounding rule and this table). Weekly
(`reference_download`, with the sidecar env of the `reference-data` job):
`test_each_window_is_the_published_histogram_values`,
`test_the_sidecar_fit_is_within_its_frozen_limits` and
`test_the_vbgmm_vbhmm_key_on_the_two_state_population_is_reported`. Workstation:
`tests/test_campos_chavez_hmm_stored.py`.

## Ha-laboratory SAM/SAH snapshot histogram

**What is compared.** The ligand-free SAM/SAH riboswitch snapshots of Fig. 1 of Liao *et al.*,
*Nucleic Acids Research* **51**:8957 (2023), doi:10.1093/nar/gkad633. The authors deposited the
data as Zenodo 10.5281/zenodo.8088172 (CC-BY-4.0): 53 binary `.traces` snapshots of 20 frames
each, with alternating excitation (green frames 1 to 10, red frames 11 to 20), and the
laboratory's own output from them: 46,950 FRET values (`frets.dat`), their normalized histogram
(`test3.dat`, bins of 0.015) and the record of the script's parameters (`counts2.txt`).
[ADR-0086](adr/0086-ha-laboratory-snapshot-histogram-is-a-reference.md) records this set as a
reference.

**Reference data.** The 56 files are committed verbatim under `tests/fixtures/ha_sam_sah/` (tier
`git`, CC-BY-4.0, one manifest entry each with its SHA-256 and its member of the Fig. 1 archive).
A reader that lives only in the tests reads the `.traces` files; the product has no importer for
them (the specification leaves this legacy format out of scope). The donor is the first channel of each molecule: the IDL extraction script stores
it first, and in every file that channel falls to its background under red excitation while the
second keeps the acceptor's direct-excitation signal.

**Two checks.** The rule that turns the traces into `frets.dat` was not found (ADR-0086 says what
was tried), so the traces are compared by their peaks only:

- *Exact.* Tether's histogram of `frets.dat` on the laboratory's bins equals `test3.dat` to 5e-9
  (the file writes 8 significant digits; measured largest difference 5.0e-10). One conversion is
  exact: MATLAB's `hist` puts the values outside the range in the end bins, and Tether's fixed
  range drops them (two below and two above here).
- *Peaks.* Tether's leakage-corrected FRET (α = 0.12 from `counts2.txt`, γ = 1) of each molecule,
  from its mean intensities over the green frames, for the 11,316 molecules whose intensities pass
  the file's cutoffs. The two peaks of its histogram must lie within **0.015** (one bin) of the
  two peaks of `test3.dat`. Measured 2026-11-13: Tether 0.3950 and 0.8663, the laboratory 0.3956
  and 0.8669. With the two channels swapped, the low peak moves to 0.128 from the laboratory's.

**Out of scope.** The three long movies of the folder (their 2,854 values are in `frets.dat` but
their files are not committed), extraction from the raw `.pma` movies, the dwell times, and the
other figures (#796). The second `.traces` variant, with a frame-index row, is the next section
(#798).

**Enforcing tests** (all unmarked — required 3-OS matrix):
`tests/test_ha_sam_sah_reference.py::test_tether_histogram_of_frets_dat_equals_test3_dat`,
`test_tether_histogram_of_the_traces_has_the_peaks_of_test3_dat`,
`test_the_donor_is_the_first_channel_of_each_molecule`,
`test_swapped_channels_miss_the_peaks_of_test3_dat` and
`test_the_reader_opens_all_53_files_with_the_molecule_counts_of_the_lab`.

## Weninger-laboratory hairpin `.traces` variant

**What is read.** One movie of the trinucleotide-repeat hairpin data of Xu *et al.*, *Journal of
Molecular Biology* **435**:168086 (2023), doi:10.1016/j.jmb.2023.168086: the CTG 14-repeat movie
`cascade110.traces` at 15 ms (872 frames, 13 molecules) from Zenodo 10.5281/zenodo.7853531, a copy
of Dryad doi:10.5061/dryad.ht76hdrkc (CC0-1.0). It is committed verbatim under
`tests/fixtures/weninger_hairpin/` (tier `git`, one manifest entry with its SHA-256 and its member
of the archive). The second movie that #798 names, `Evo31.traces`, is not committed: at
1,146,478 B it is larger than the git tier allows.

**How this variant differs.** It is the second variant of the binary `.traces` format that the
tests read; the Ha-laboratory files of the previous section are the first. Each frame starts
with a frame index (1 to 872) that the header does not count, so the file holds 27 values per
frame for 26 channels. Each molecule's pair is acceptor first: the record's README says
"acceptor and donor pairs in series", and the laboratory's MATLAB reader takes row 1 as time,
then the acceptor and the donor of each molecule. The test reader finds the variant from the
file size and takes the channel order as a stated parameter; the product has no importer for
`.traces` files (the specification leaves this legacy format out of scope).

**Checks** (all in the required 3-OS matrix):

- The reader returns the frame index 1 to 872, and the header and the file size agree
  (6 + 2 × 872 × 27 = 47,094 B). A Ha-laboratory file reads with no frame index.
- The reader agrees value for value with a line-by-line copy of the laboratory's MATLAB reader,
  and Tether's leakage-corrected FRET (α = 0.06, the reader's leakage; γ = 1) equals the reader's
  FRET on every frame where its donor + corrected acceptor is positive. The MATLAB reader reads
  only 26 × 872 values, so it fills the first 839 frames; the comparison uses those frames.
- Two demonstrations: a reader that ignores the frame index, and a reader that takes the donor
  first, each no longer follow the MATLAB reader (donor first gives E → 1 − E exactly when
  α = 0).

**Advisory peak check.** The paper reports that the CTG hairpin switches between E 0.74 and
0.53, from its own methods (a Chung-Kennedy filter with edge detection, K-means and SPARTAN) on
many movies. The test reports Tether's histogram of this one movie beside those two values and
never fails on them. Measured 2026-11-14: of the 4,895 frames whose donor + corrected acceptor is
above 300 counts, the highest bin above E = 0.3 is at 0.7125, 28.2 % of the frames lie within
0.05 of 0.74 and 3.2 % within 0.05 of 0.53. One movie of 13 molecules shows the high state but
no separate peak near 0.53.

**Out of scope.** The kinetic schemes and rates of the paper, the 50 ms data, the rest of the
archive and any request to the authors (#798).

**Enforcing tests** (all unmarked — required 3-OS matrix):
`tests/test_weninger_hairpin_traces.py::test_the_reader_returns_the_frame_index_and_the_header_counts`,
`test_the_reader_finds_each_variant_from_the_file_size`,
`test_the_reader_and_tether_fret_follow_the_laboratory_matlab_reader`,
`test_reading_the_file_as_the_variant_without_a_frame_index_fails` and
`test_the_swapped_channel_order_fails`. The advisory report is
`test_advisory_tether_histogram_beside_the_papers_ctg_values`.

## Extraction vs MASH-FRET simulated truth

Oracle (a) compares Tether with another tool on a real movie, which has no truth. This check
compares Tether's extraction with the truth of four simulated two-channel videos that the
MASH-FRET simulator (v1.3.6, the pin of the MATLAB harness) made on the maintainer's
workstation (#714, [ADR-0088](adr/0088-extraction-test-on-mash-fret-simulated-videos.md)). The
scenarios are `easy` (bright, isolated molecules), `low-snr` (dim molecules on a brighter
background), `dense` (60 molecules, so apertures overlap) and `uneven` (a TIRF-like Gaussian
background), each 160 × 80 px, 40 frames, one excitation. The videos, the true coordinates and
the noise-free intensities are frozen under `tests/fixtures/reference/mash-fret/` with a
provenance record, and `tests/test_matlab_reference.py` checks their SHA-256 values.

`tests/test_mash_fret_extraction.py` runs `extract_movie` with every default on each video and
measures: how many of the molecules whose aperture fits in the channel Tether found (a donor
position within 1.5 px of the truth), how many Tether molecules match no true one, the largest
donor and acceptor position errors, and, in each channel, the median ratio of Tether's background-subtracted
intensity to the true image counts and the lowest Pearson correlation of the traces. The first
measurement:

| Scenario | Found / eligible | False | Largest error donor / acceptor (px) | Median ratio donor / acceptor | Lowest r donor / acceptor |
| --- | --- | --- | --- | --- | --- |
| `easy` | 4 / 6 | 0 | 0.477 / 0.416 | 0.917 / 0.913 | 0.895 / 0.827 |
| `low-snr` | 6 / 8 | 0 | 0.969 / 3.075 | 0.875 / 0.934 | 0.348 / 0.381 |
| `dense` | 7 / 35 | 1 | 0.984 / 2.067 | 1.078 / 0.731 | 0.432 / 0.467 |
| `uneven` | 7 / 10 | 0 | 0.645 / 0.645 | 0.965 / 0.892 | 0.831 / 0.701 |

The limits are these values with fixed margins (ADR-0088 gives them) and are frozen. Every
molecule Tether missed in `easy` and `uneven` has another true molecule within 7.4 px. Two
fault injections each break a limit: an aperture moved by 2 px lowers the donor ratio of `easy`
to 0.689, and an acceptor position moved by 2 px after the channel mapping raises its largest
acceptor error to 2.390 px.

The test also counts duplicates: Tether molecules that match a true molecule which an earlier
Tether molecule already matched (#1006,
[ADR-0107](adr/0107-the-mash-fret-extraction-test-counts-duplicate-molecules.md)). The count is 0
in each scenario, and the limit is that count, so a molecule written twice breaks it. A test
repeats each molecule and trace row of `easy` and requires the duplicate limit to break.

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

Both deviations sit inside the advisory band; the artifact characterizes both as
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
itself is not vendored: the specification introduces no data simulator). Both levels raise `ValueError` in the band check
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

The observed dwells are shorter than predicted. Most of that is the observation time: the traces are
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

## PySMACKS tutorial traces (exact histogram, rate report)

A second data point from outside Tether, tMAVEN and kinSoft
([#720](https://github.com/bioedca/tether/issues/720)): the traces file of the PySMACKS tutorial,
Zenodo [10.5281/zenodo.21793247](https://doi.org/10.5281/zenodo.21793247) (Molcrette and Schmid,
2026, CC0-1.0), manifest id `pysmacks-tutorial-traces`. It holds 1,136 ALEX traces of a
two-state Holliday junction in OpenFRET JSON; 52 of them carry the label "FRET", a frame range.
PySMACKS is at a beta version, so it is a second data point and not a reference tool.

**The PySMACKS side was made once, offline.** The record's README says the file holds the HMM
analysis, but it does not: no trace has the `HMM_param` entry that PySMACKS writes, no trace has
FRET values, and the file names no PySMACKS version. So `scripts/make_pysmacks_reference.py` ran
PySMACKS 1.0.0b4 on the 52 labeled traces on 2026-11-13 and wrote
`tests/fixtures/pysmacks_tutorial_reference.json`, which records the versions that ran. PySMACKS
never runs in CI (maintainer decision of 2026-11-13). The tests read the original downloaded zip
with a test-only reader and use only the donor-excitation channels (rules 1 and 2 of the specification's rules for test
data).
Both tests are in the weekly `reference-data` job.

- **Histogram, exact.**
  `tests/test_pysmacks_tutorial_download.py::test_tethers_apparent_e_histogram_equals_the_pysmacks_histogram`
  bins Tether's apparent FRET of each labeled, background-subtracted frame (56,442 frames) in
  151 bins over −0.5 to 1.5, and requires the count of every bin to equal the PySMACKS count.
  It does.
- **Rates, report only.**
  `tests/test_pysmacks_tutorial_download.py::test_tethers_two_state_rates_report_their_difference_from_pysmacks`
  fits the same frames with the base-environment two-state HMM of oracle (c) and prints its
  numbers beside the PySMACKS ensemble HMM (a 2-D Gaussian HMM on the donor and acceptor
  intensities, `TbT_HMM_pipeline` then `Ens_HMM`). It fails only when the fit does not converge.
  No frozen limit exists; one needs the maintainer's sign-off. The record gives no frame time,
  so the rates are per frame, and no published rates for this set were found (the PySMACKS
  documentation site could not be reached from the build environment).

Measured on 2026-11-13:

| Quantity | PySMACKS 1.0.0b4 | Tether | Difference |
|---|---|---|---|
| State 1 apparent E | 0.2374 | 0.2140 | −0.0234 |
| State 2 apparent E | 0.6734 | 0.5944 | −0.0790 |
| p(1 → 2) per frame | 0.2711 | 0.2649 | −0.0062 |
| p(2 → 1) per frame | 0.2861 | 0.1679 | −0.1182 |
| k(1 → 2) per frame | 0.2711 | 0.2054 | −0.0657 |
| k(2 → 1) per frame | 0.2861 | 0.1422 | −0.1439 |

PySMACKS's state E is the E of its median state means in intensity, and its rate is the
transition probability per frame, which its Kinetics tab divides by the frame time. Tether's rate
is `1/⟨τ⟩` of the interior dwells of its Viterbi paths (4,687 and 4,664 dwells). The two fit
different models (two intensity channels against one apparent-E signal), so this table is
evidence about the size of the difference and not a tolerance.

## Scope and known limitations

**Validation coverage.**

- Oracle (a) has its headline assertion on the `workstation` tier and oracle (c) on the
  `large` tier; neither is a required check. Oracle (b)'s live fits are in the
  required `sidecar / parity` job, but that job only actually fits when the pull request
  touches an idealization path. A green required 3-OS matrix run on its own therefore proves
  the scorers, the frozen artifacts and the pure primitives — not a fresh fit against real
  reference data.
- Oracle (a) is stronger than that: its headline assertion depends on the ≈0.9 GB UCKOPSB
  movie, which exceeds the repo's Git-LFS budget and so is on **no** CI runner. No CI job
  collects it; the nightly scheduled task on the maintainer's workstation runs it
  ([#699](https://github.com/bioedca/tether/issues/699)), and a CI run says nothing about it.
- The frozen **default** parity tolerance for (b) was first measured against tMAVEN `71cfa1af`
  (2025-05-06) on CPython 3.9.23 — 13 upstream commits behind the pinned `10f4230b`
  (2025-10-05) on CPython 3.12.13 that CI, the installers and every live sidecar assertion
  actually run. The re-measure on the pinned build of 2026-11-15 closed that gap without moving
  a bound ([ADR-0110](adr/0110-idealization-parity-gaps-per-trace-transition-matrix-refreeze-and-bound-lock.md));
  the ebFRET block has no such gap.
- A frozen bound — in `$.tolerance`, a per-method row or a transition-matrix ceiling — can now
  be loosened only by a pull request that edits both `schema/parity_tolerance.json` and the
  literal copy [`_ACCEPTED_BOUNDS`](https://github.com/bioedca/tether/blob/main/tests/test_parity.py) in
  `tests/test_parity.py`; `test_frozen_bounds_match_the_accepted_copy` fails on either edit
  alone (see §(b)). Nothing pins `$.provisional` to its committed values.
- The ebFRET gate tests **one fixed pair of seeds** (1 and 2,
  [ADR-0075](adr/0075-seeded-ebfret-parity-gate.md)), not a fresh pair per run. A regression
  that shows only on some seeds can pass it. Unseeded ebFRET keeps a rare poor-optimum tail
  (one observed draw scored Viterbi 0.7568 against the frozen 0.9034 floor,
  [#311](https://github.com/bioedca/tether/issues/311)) that the frozen band does not cover.
- The kinSoftChallenge check is advisory, band-checked on the 2-state level 1 only, gated
  (see above), and **not blind** — the ground truth is committed and the fit is
  retrospective. Levels 2 and 3 are reported, not band-checked, and the present method does
  not recover the level-2 rates.
- (b) has no per-trace vbFRET *parity* measurement against standalone tMAVEN, and its
  comparison with the original vbFRET_nov12 on measured traces is advisory, with no frozen
  tolerance (see "Measured traces with a stored vbFRET path"). (d) has no
  real-data uplift number, (e) is missing the γ Deep-LASI-median oracle, and its α band runs on
  the workstation only. Each is stated in its own section above with the ADR that
  deferred it. (f) has no migration tests, which is not a gap: the specification makes them
  required only at the first schema-version step (see (f) above).
- The specification states that Tether ships no synthetic-data simulator. In practice (e) and (g) are
  validated against **synthesized known-α / known-γ / known-step traces**, and (d) against a
  synthetic separable multi-video project file, because the real fixtures do not carry the required
  ground-truth field. Two of those substitutions were ratified in an ADR and two were not:
  [ADR-0026](adr/0026-photobleach-detection-and-window-default.md) option C ratifies the
  synthetic known-step ground truth for (g), and
  [ADR-0028](adr/0028-gamma-acceptor-bleach-step-estimator.md) option D ships γ "validated
  against synthetic known-γ recovery". The synthetic known-α fixtures and (d)'s synthetic
  multi-video project file carry no such ratification —
  [ADR-0027](adr/0027-leakage-alpha-tail-estimator.md) defers the donor-only α cross-check
  but never mentions synthetic data, and neither ranker ADR
  ([ADR-0034](adr/0034-gradient-boosting-quality-ranker.md),
  [ADR-0038](adr/0038-provisional-prior-training-fold.md)) discusses validation data at all.
  The specification allows synthesized known-truth fixtures in tests. (e)'s α is now also checked
  on real data, by the workstation band above
  ([ADR-0133](adr/0133-the-leakage-band-runs-on-the-donor-only-sample-and-the-gamma-leg-stays-withheld.md));
  (d)'s synthetic project file is listed among the criteria not yet met, as not yet validated on
  real data ([#548](https://github.com/bioedca/tether/issues/548)). The real-data comparisons that do
  exist (oracles (a), (b), (c)) stay on real fixtures.
- A reference that comes from an ALEX data set compares **apparent FRET from the
  donor-excitation channels only** — the donor and acceptor intensities under donor
  excitation. It does not check direct excitation (δ), stoichiometry or any other quantity
  that needs the acceptor-excitation frames, and so it does not validate them. Tether is
  single-laser only (the specification excludes ALEX); the specification's "What the tests
  can use" rules allow the
  ALEX sets at trace level and add no ALEX feature.

**Accepted diagnostics that are not gates.**

- The extraction oracle's acceptor per-molecule Pearson median **0.854** and donor precision
  **0.34** are reported, never gated (ADR-0022). Acceptor intensity fidelity and the ≈1.3 px
  acceptor read-position scatter were carried forward as diagnostics rather than re-opening
  the extraction gate.
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
  otherwise the project degrades to the explicit apparent-E substrate, so a missing γ never
  gives a NaN E (ADR-0003). The committed Cy3-only fixture exercises that path.
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

The nightly CPU job also trains the default model on the public PySMACKS tutorial labels and
checks frozen training numbers and a floor on the area under the ROC curve of a held-out part
([ADR-0098](adr/0098-the-pysmacks-labels-are-the-deep-classifier-reference.md)). That floor is a
regression check on one labeled set. It does not make the add-on part of the validated core.

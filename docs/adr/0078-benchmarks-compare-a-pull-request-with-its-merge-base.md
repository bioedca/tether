<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0078 — Benchmarks compare a pull request with its merge base on one runner

- **Status:** accepted
- **Date:** 2026-11-12
- **Deciders:** bioedca
- **PRD anchor:** §8 (NFR-PERF), §12.6 (continuous integration)
- **Milestone:** M19
- **Extends:** [ADR-0032](0032-nfr-perf-budget-verification.md)

## Context and problem statement

ADR-0032 checks three NFR-PERF budgets against fixed limits with generous headroom. The test
audit of 2026-11-11 measured that headroom at 20 to 130 times the measured cost, so a kernel
that becomes two times slower still passes. No test measured memory, and no number was kept
from one run to the next (#722). The maintainer decided that the tests must find when Tether
becomes slower or uses more memory, with a start limit of 1.5 times main, after the noise is
measured (interview of 2026-11-11, Q2, Q26, Q27).

A fixed time limit cannot do this on shared GitHub runners: two runs of the same commit on two
runners can differ by more than 1.5. A stored history of numbers has the same problem unless
every number comes from the same machine, which is the workstation history of #723 and not
this decision.

## Decision

1. **Same runner, same job, interleaved.** `benchmarks/bench.py run` measures the merge base and
   the pull request head in one job. For each case and size it runs base then head (the order
   turns round each round, three rounds), each measurement in its own child process. Machine
   drift during the job therefore touches both sides alike, and nothing is compared across
   runners.
2. **Minimum of repeats.** Time is the minimum over repeats, as
   `tether.project.perf.min_runtime` does; a repeat calls the operation enough times to last
   at least 50 ms. The round result is the minimum over the three rounds.
3. **Memory.** The `tracemalloc` peak of one extra call (Python and NumPy allocations) is the
   gated memory value; the process peak RSS is reported. A peak below 1 MiB only reports,
   because a ratio of a few kilobytes means nothing.
4. **Warm-up.** The first call is timed apart (`first_call_s`) and never enters the timed part,
   so a one-time cost such as a Numba compilation is reported and not compared.
5. **Limits.** A gated case fails when head is above 1.5 times base in time or in peak
   memory, and the job names the case. A case that fails on head fails the job; a case that
   fails only on base (an API that the pull request changed) is reported as not compared.
6. **Noise first.** `workflow_dispatch` runs the suite ten times on one commit and reports
   max/min for each case. A case keeps the 1.5 gate only when its spread is at most 1.2;
   otherwise it gets a larger input or reports only. The pull request for #722 holds the noise
   report that set the gates in `benchmarks/cases.py`.
7. **Inputs.** Every case reads data from the test-only generator `tests/_synth.py` (#701) at two
   sizes, with one seed, so base and head read the same bytes. The `.tether` size of the
   extraction case is one more reported value.
8. **Scope of the job.** It runs only on pull requests that touch `src/tether/**`; it is not a
   required context until the maintainer adds it; it uses no third-party service and sends no
   data outside GitHub. Idealization runs in its own job in the isolated sidecar env.

`tests/test_project_perf.py` keeps its budget checks unchanged: they still hold the absolute
envelope, and this job holds the relative one.

## Consequences

- A regression that stays inside the NFR-PERF budget now fails a pull request, when the case
  is gated, at 1.5 times its merge base.
- The job costs about ten to fifteen runner minutes on a pull request that touches the source,
  and nothing on any other pull request.
- The benchmark code comes from the head; the source under test comes from each side. A pull
  request that changes an API changes its benchmark too, and the base side then reports "not
  compared" for that case instead of failing.
- Native allocations that bypass the Python allocator (for example inside HDF5) are not in the
  `tracemalloc` peak; the RSS column shows them, and it only reports.

## Alternatives considered

- **Stored numbers from earlier runs on GitHub runners.** Rejected: the runner hardware
  changes between runs, so the spread would exceed the limit. The workstation history (#723)
  is the place for a stored series.
- **pytest-benchmark or asv.** Rejected for now: a new dependency in the locked base stack for
  what one small script does, and neither interleaves two source trees in one job by default.
- **A fixed time limit per case.** Rejected: that is ADR-0032's budget, which by design
  cannot see a regression inside it.

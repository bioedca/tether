<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0071 — Private-data checks run from a scheduled task on the maintainer's workstation

- **Status:** accepted
- **Date:** 2026-11-11
- **Deciders:** bioedca
- **PRD anchor:** §9.5 (the validation suite), §12.6 (continuous integration)
- **Milestone:** M18

## Context and problem statement

Some checks need laboratory data that is not public: the ≈0.9 GB movie of oracle (a), and more
acquisitions that the regression plan (#695) adds. No CI runner has this data, and it is too
large to download for each run. Oracle (a) therefore has run only by hand, and
`docs/validation.md` says that it "skips even there". How does a check on private data run on a
schedule, from reviewed code, without the data or its file names leaving the computer that has
them?

## Decision drivers

- The laboratory data stays private (interview of 2026-11-11, Q19: B).
- The repository is public. A pull request must not be able to run its code on the computer
  that has the data.
- The repository may hold summary numbers from these acquisitions, but no traces, no images
  (R13: A) and no file names: neutral ids only (R12: B).
- The workstation is the reference computer for speed (Q28: A), so a result also records the
  tool versions and the hardware.
- The runner itself must be tested in the required CI matrix, which has no private data.

## Considered options

- **A GitHub self-hosted runner on the workstation.** Rejected (R14: A). On a public repository a
  workflow from a pull request can be sent to a self-hosted runner, and the workflow would run on
  the computer that has the data.
- **Private data in a private store that CI downloads.** Rejected: the data stays on the
  workstation, and a secret that opens it would be one more thing to protect.
- **Runs by hand (the old state).** Rejected: the check never runs when nobody remembers it.
- **A committed runner that a scheduled task on the workstation starts.** Chosen.

## Decision outcome

Chosen option: **a committed runner that a scheduled task on the workstation starts**, because
it keeps the data and the trigger on the workstation and keeps the code reviewed.

- `scripts/workstation/run_private_checks.py` fetches only `refs/heads/main` into its own clone,
  refuses a commit that is not reachable from `origin/main` and a clone with local changes,
  builds the locked environment, and runs `pytest -m workstation`.
- The tests find the data through `$TETHER_PRIVATE_DATA`: a folder with a mapping file,
  `tether-private-data.json`, from neutral ids (`lab-01`) to file names. The mapping file and the
  names stay on the workstation.
- A result file (format `tether-workstation-result/1`) holds the commit, the times, the tool
  versions, a hardware summary without a host name or a user name, and for each check its outcome,
  its neutral ids and its summary numbers. It holds no trace, no image and no file name. The
  runner searches each result for every mapped name and withholds the checks when it finds one.
  The output of pytest, which can name files, goes to a local log only.
- The `workstation` marker is deselected in every CI job, and `tests/test_marker_contract.py`
  enforces that. `tests/conftest.py` stops collection when a `workstation` test also carries
  `large`, `sidecar` or `deep`, because those jobs select on their marker alone.
- `run --dry-run` runs the self-check with the current interpreter against a synthetic folder. The
  required matrix runs it, and it can never claim a verified commit of `main`.
- An agent session reads the newest result with `latest` and opens or updates one issue for each
  check that did not pass, the rule of #697.

### Consequences

- Good: oracle (a) and the other private-data checks (#699, #703, #715, #723) get a scheduled
  run from reviewed code, and each run leaves a record with numbers.
- Good: the refusal, the result format and the privacy guard are tested on every pull request.
- Bad / trade-off: the scheduled run itself and the environment build are not tested in CI; the
  first real run is recorded on #699. A refusal or a failed build writes a result with no checks,
  but a dead schedule shows only as an old result, which the agent procedure turns into an issue
  after 8 days.
- Bad / trade-off: the guard does not search names shorter than 5 characters, and a skip on the
  workstation counts as a failure.
- Follow-up: the maintainer installs the scheduled task one time
  (`docs/maintainers/workstation-runner.md`).

## More information

- Work item #698; plan and decisions on #695 (Q19, R12, R13, R14, Q28, E1).
- `scripts/workstation/run_private_checks.py`, `tests/conftest.py`,
  `tests/test_workstation_runner.py`, `tests/test_workstation_selfcheck.py`,
  `tests/test_marker_contract.py`.
- [Running the private-data checks](../maintainers/workstation-runner.md).

<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0074 — MATLAB reference outputs come from pinned runs with a provenance record

- **Status:** accepted
- **Date:** 2026-11-11
- **Deciders:** bioedca
- **PRD anchor:** §1 (reference tools), §8 NFR-VALID, §8 NFR-FIXTURES
- **Milestone:** M19

## Context and problem statement

Three reference tools are MATLAB programs: smfret-analysis2, MASH-FRET and Deep-LASI. CI has no
MATLAB. Where a Tether test claims agreement with one of them, the reference today is one old
export or a formula copied into the test file, and a copied formula cannot show that the copy is
wrong. Issue [#700](https://github.com/bioedca/tether/issues/700) asks for a harness that makes
every MATLAB reference output come from the real tool, at a known commit, with a record that
lets anyone check the frozen numbers later. The runs themselves happen on the maintainer's
workstation, in sessions the maintainer approves (interview of 2026-11-11, Q8: A, R15: A, E1: A).

## Decision drivers

- A reference number must be traceable to one tool commit, one MATLAB release and the exact
  input bytes.
- A frozen output must not change without a failing test.
- CI cannot run MATLAB, so the harness must be testable without it.
- The inputs of a real run often live on the workstation; no private path may reach the
  repository.
- No code of a reference tool may enter the repository. Deep-LASI's repository has no licence
  file.

## Considered options

1. **Copy the tool's formula into the test** (the state before #700). Rejected: it tests the copy.
2. **Run MATLAB in CI.** Rejected: no licence or runner exists, and #700 puts a live MATLAB job
   out of scope.
3. **Committed drivers, real runs on the workstation, frozen outputs with a provenance record and
   a checksum test in the required matrix.** Chosen.

## Decision outcome

Chosen option 3, implemented in `scripts/reference/matlab/`:

- **One pin file.** `pins.toml` holds every pin. Changing a pin is a reviewed change, and the same
  pull request regenerates every output that the old pin made. The pins at this decision:

  | Tool | Repository | Commit | Note |
  |---|---|---|---|
  | smfret-analysis2 | `vafabakhsh-lab/smfret-analysis2` (GPL-3.0) | `121634e9b533eb3a922fbfe9bc1e7715a9a6a6b4` | default branch head, 2022-05-20 |
  | MASH-FRET | `RNA-FRETools/MASH-FRET` (GPL-3.0) | `83fcadf3b7274f2eeb22591429202dda0328c05b` | tag v1.3.6, 2025-11-14, the latest release |

  Deep-LASI gets its pin with its driver in #715, after the feasibility step of that issue.
- **A driver refuses a clone that is not the pinned one**: `HEAD` must equal the pinned commit,
  and `git status --porcelain --untracked-files=all` must be empty (an untracked `.m` file could
  shadow a tool function). Files that the tool's own `.gitignore` ignores (MASH-FRET's dump
  folders and settings file) do not count.
- **The run.** `matlab -batch` calls `m/tether_run_job.m`, which reports `version` and
  `version('-release')`, puts the clone and the task folder on the path, and calls a task
  function. A task function calls the tool's own functions on named inputs and writes plain files
  (CSV, or HDF5) into an output directory that was empty before the run.
- **The provenance record.** `provenance.json` beside the outputs states: the method, the tool
  name, URL and commit, the MATLAB release and version, the operating system, the driver's Tether
  commit and whether its folder was clean, the task and its parameters, the SHA-256 of every input
  file (by file name only, never by path) and of every output file, the manual steps, and the date
  in UTC. **Every record states its MATLAB release** in the form `version('-release')` gives
  (`2023b`); a record without one, or with a value such as `unknown`, is refused when it is
  written and fails verification. No absolute path may appear in a parameter or a manual step,
  and an input whose bytes change while MATLAB runs is refused, so the hashes name the bytes the
  tool read.
- **The checksum test.** `tests/test_matlab_reference.py` verifies every committed record under
  `tests/fixtures/reference/`: each recorded output must exist with its SHA-256, and a file that
  the record does not list fails too. A stand-in record (`stand-in/selftest`) is always committed,
  so the scan cannot pass on an empty set. Outputs larger than the git tier go to the data
  manifest of #696, which carries their checksums. `.gitattributes` and the whitespace hooks leave
  the bytes of these fixtures unchanged on every platform.
- **Rule of saved files first (#700 item 5).** When a tool cannot run without its window, the
  files that the tool already saved (exports, project files) are the reference. `freeze` copies
  them and writes a record with the method `saved-files`, the tool version that saved them and the
  MATLAB release as the maintainer states it, and, where known, the SHA-256 of the inputs the
  tool read to make them. Manual steps in the tool are the last alternative,
  for a result that is in no saved file, and the record then lists the steps.
- **Rule of scripts with no public commit (#700 item 7).** A pin of kind `files` (the laboratory
  cloud scripts of #710) records the SHA-256 of each script file in place of a commit. The driver
  refuses a script whose hash differs. The scripts stay on the workstation.
- **No tool code in the repository.** The task functions call the tool; they contain no copy of
  it. Only numbers that a tool computes from our inputs are committed, with REUSE coverage, and a
  data file of a tool's repository only where its licence permits.
- **CI uses a stand-in.** `tests/_matlab_standin.py` takes the same `-batch` command, reports the
  release `0000x` and writes a small known output, so the required matrix tests the pin check, the
  record and the checksum check on three operating systems. The MATLAB task functions themselves
  run first in the issue that needs their output (#709, #713, #714); a defect that the first real
  run finds is corrected in that issue's pull request.

### Consequences

- Good: a frozen reference number names the tool commit, the MATLAB release and the input bytes
  that made it, and a changed byte fails the required matrix.
- Good: a reviewer can see from the record whether a reference came from a run, from saved files,
  or from manual steps.
- Bad: the MATLAB task functions are not exercised until the first real run. The MASH-FRET
  `simulate` task drives MASH-FRET's window through its own routine helpers and is the most
  likely to need a correction then.
- Bad: a record states what the run reported; it cannot prove that the workstation's MATLAB had
  no local change. The maintainer's approval of each session is the control for that.

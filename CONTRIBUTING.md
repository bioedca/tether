# Contributing to Tether

**Who this page is for.** Anyone who wants to improve Tether: correct the documentation, report
a problem or change the code. It assumes that you can use git and Python in a terminal, and that
you have a GitHub account. It does not assume that you know the rules of the project. The first
sections follow the tasks of a first day. The rules of the project come after them.

This page is the contributor summary of [`docs/PRD.md` §12](docs/PRD.md). `AGENTS.md` governs
agent operations and safety. `docs/PRD.md` governs the product and the science. Where this page
shortens either file, that file governs.

## Correct the documentation: the quick path

Use this path to correct a sentence, a link or a command on a documentation page. The pages of
the site are Markdown files under `docs/`. `README.md`, `CONTRIBUTING.md` and `SECURITY.md` are
at the top of the repository.

1. Fork `bioedca/tether` on GitHub.
2. Clone your fork. In the command, `YOUR_GITHUB_USERNAME` is your GitHub username.

   ```bash
   git clone https://github.com/YOUR_GITHUB_USERNAME/tether.git
   cd tether
   ```

3. Make a branch for the change.

   ```bash
   git switch -c docs/issue-N-short-slug
   ```

   [Name the branch and the commit](#name-the-branch-and-the-commit) gives the form of the name.

4. Install the pinned documentation tools and pre-commit.

   ```bash
   pip install -r requirements-docs.txt pre-commit
   ```

5. Edit the page to the [writing style guide](docs/maintainers/writing-style.md).
6. Build the site, with each warning treated as an error.

   ```bash
   mkdocs build --strict
   ```

   The last line reads `INFO    -  Documentation built in 3.20 seconds`, with your own time. A
   warning ends the build with `Aborted with 1 warnings in strict mode!`. Correct each warning,
   and build again.

7. Run every hook on every file.

   ```bash
   pre-commit run --all-files
   ```

   Each hook prints `Passed` or `Skipped`. A hook that corrects a file prints `Failed`. Run the
   command again after a hook corrects a file.

8. Commit the change with a Conventional Commit message.

   ```bash
   git commit -a -m "docs(<scope>): <summary>"
   ```

9. Push the branch to your fork.

   ```bash
   git push -u origin docs/issue-N-short-slug
   ```

10. Open a draft pull request against `main`.

    GitHub fills the body with the pull request template. Complete each field of it. The required
    checks start, and the `docs-build` check uploads the rendered site as the `docs-site`
    artifact. [What review to expect](#what-review-to-expect) tells you what comes next.

## Report a problem

Open a new issue, and pick one of the forms that GitHub offers. Blank issues are disabled. The
form routes the report and applies the right labels for you.

Do not report a security vulnerability in a public issue. Follow [`SECURITY.md`](SECURITY.md),
which uses GitHub Private Vulnerability Reporting. This is the one route where the wrong choice
causes harm.

- **Something wrong in the docs.** This is an inaccurate, missing, unclear or stale page, or a
  dead link. Give the **page URL** and the entry from the **version selector** of the docs site.
  The site is versioned with `mike`, so both are necessary to reproduce what you saw.
- **An open-ended question**, such as "how should I approach…?". Ask it in
  [Discussions Q&A](https://github.com/bioedca/tether/discussions/categories/q-a), not in the
  issue tracker. A question whose answer is missing from the docs becomes a separate `type:docs`
  issue. A question that had to be asked is itself a documentation signal.
- **The Question form.** This form is non-worker intake. Its issue may stay in `status:backlog`.
  It cannot become `status:ready` or enter the swarm until someone converts it to a separate
  work issue.

The bug, documentation, feature, validation-oracle and maintenance forms are work-producing
intake. The maintenance form covers chore, CI, test, refactor and governance work while blank
issues stay disabled. It starts as `type:chore`, and a maintainer may correct that type during
grooming.

Every form requires the same attestations. They cover the duplicate search, private
vulnerability reporting, secrets, and private, raw, unlicensed, user or lab data.
[How an issue becomes ready for work](#how-an-issue-becomes-ready-for-work) tells you what happens
after you open an issue.

## Set up a development environment

Restore the base environment from the lock. Do not solve a fresh environment. The `conda-lock`
files are pin-and-hold.

1. Create the pinned base environment from the committed `conda-lock.yml`.

   ```bash
   micromamba create -n tether -f conda-lock.yml   # or: conda-lock install -n tether
   ```

2. Activate the environment.

   ```bash
   micromamba activate tether
   ```

3. Install Tether without its dependencies. The dependencies come from the lock, not from pip.

   ```bash
   pip install -e . --no-deps                      # deps come from the lock, not pip
   ```

4. Install the hooks.

   ```bash
   pre-commit install
   ```

5. Install the test tools, as [Run the tests](#run-the-tests) tells you.

The tMAVEN idealization sidecar is a **separate** PyQt5 and `numpy<2` environment. It is built
from `sidecar/conda-lock.yml`. Only the `sidecar`-marked tests need it, and everything else runs
without it.

Two things that the sidecar needs are outside that lock. One is tMAVEN itself, pinned by commit.
The other is `setuptools<81`, for the `pkg_resources` API that tMAVEN imports without declaring
it. So use the guided script, and do not build the sidecar by hand:

```bash
python scripts/setup_sidecar.py     # writes the $TETHER_SIDECAR_PYTHON you need
```

Source: `docs/PRD.md` §12.

## Run the checks

### Run pre-commit

Run pre-commit before every commit:

```bash
pre-commit run --all-files
```

The hooks include these checks:

- `ruff`, for lint and format,
- `reuse lint`, for SPDX/REUSE licensing,
- the secret and large-file guards (`docs/PRD.md` §12.6 and §12.9),
- `markdownlint-cli2`, for the Markdown structure, with its settings in `.markdownlint-cli2.yaml`,
- `codespell`, for misspellings in prose and code comments, with its settings in `.codespellrc`.

When `codespell` flags a word that is correct, add the word to `.codespell-ignore`. Such a word
is a name, an abbreviation or an identifier, for example. Write it in lowercase on its own line,
and keep the list sorted. Do not rename an identifier for it. Do not add a per-line ignore
comment.

### Run the tests

`pytest` is **not** in the base lock. CI installs it explicitly, so a freshly restored
environment needs the same step before it can run the tests. The install line below also names
`markdown-it-py`. That is the dev-only CommonMark parser with which the agent scripts under
`.agents/bin/` read issue bodies (ADR-0066). The lock already carries that exact version, so on a
restored environment the install changes nothing.

Run the **small-fixture** test suite locally. The GUI tests run headless with
`QT_QPA_PLATFORM=offscreen`.

```bash
python -m pip install "pytest==9.1.1" "pytest-qt==4.5.0" "markdown-it-py==4.2.0"

# exactly what the required `test` matrix runs (see Test tiers below)
QT_QPA_PLATFORM=offscreen pytest -m "not large and not sidecar and not deep and not workstation and not reference_download"
```

The large and gated fixtures run only in the scheduled `large-fixtures.yml` tier. The required
matrix never runs them.

#### Test tiers and markers

A bare `pytest` runs **everything**, marked tests included. The `-m` option is a filter, not a
default.

So the required matrix is an explicit *exclusion*. It runs the unmarked tests **and**
the `gui` tests. It leaves out only the five tiers that need something that CI does not have.
Four of those tiers have their own workflow. The fifth runs in no CI job at all.

| marker | needs | where it runs |
|---|---|---|
| *(unmarked)* | nothing beyond the base env | `ci.yml` — required, 3 OS |
| `gui` | PySide6/napari/pyqtgraph; headless via `QT_QPA_PLATFORM=offscreen` | `ci.yml` — required, 3 OS |
| `sidecar` | a live tMAVEN env (`$TETHER_SIDECAR_PYTHON`) | `sidecar.yml` — never the required matrix |
| `deep` | the isolated torch stack (`deep/conda-lock.yml`) | `deep.yml` / `deep-gpu.yml` — advisory |
| `large` | the gated large-fixture tier | `large-fixtures.yml` — scheduled |
| `reference_download` | network access to download the `download` tier of `tests/reference_data/manifest.toml` | `reference-data.yml` — weekly, the required matrix makes no network call ([ADR-0073](docs/adr/0073-one-manifest-for-reference-data-sets.md)) |
| `workstation` | private laboratory data (`$TETHER_PRIVATE_DATA`) | no CI job — `scripts/workstation/run_private_checks.py` on the maintainer's workstation ([ADR-0071](docs/adr/0071-private-data-checks-run-from-a-workstation-task.md)) |

```bash
# The required matrix, verbatim — ci.yml runs this on all three OSes
# (Linux wraps it in `xvfb-run -a`; QT_QPA_PLATFORM=offscreen is the local equivalent).
QT_QPA_PLATFORM=offscreen pytest -m "not large and not sidecar and not deep and not workstation and not reference_download"

QT_QPA_PLATFORM=offscreen pytest -m gui    # just the GUI tier

# Skip Qt. The optional tiers must be excluded here too — `not gui` on its own still
# selects deep/sidecar/large/reference_download, and the deep tests import torch, which
# the base lock does not carry.
pytest -m "not gui and not large and not sidecar and not deep and not workstation and not reference_download"
```

The keyboard-shortcut tests of the shell in `tests/test_shell_basics.py` need a window that the
platform plugin activates. Where the plugin does not activate it, the tests skip. If
`TETHER_REQUIRE_WINDOW_ACTIVATION=1` is set, they fail instead. The required `test` job sets it on
all three operating systems, so that gate cannot go green without the key path (#855).

A GUI test that needs a shell asks for the `section_shell` fixture. The fixture is in
`tests/conftest.py`, and it is built on `tests/_gui_harness.py`. `section_shell("analyze")`
returns a shell that shows that section over a writable temporary `.tether`. `project=False`
gives a bare shell.

When the test ends, the fixture closes every shell that it built. The teardown fails if a session
lock, the curation event filter or a `_GUI_PROJECT_OWNERS` entry survived. A new section joins
`SECTION_IDS` there, so that `tests/test_gui_section_contract.py` runs on it. If the section builds
a napari `Viewer`, it also joins `VIEWER_SECTIONS`, because macOS offscreen cannot draw a `Viewer`
(#344).

`--strict-markers` rejects an *unregistered* marker only where a test applies it. It does
**not** check the names in a `-m` expression, so a typo there is not an error. `-m deepp`
deselects every test, and pytest exits 5 with no message about the unknown name.

`-m "not largge"` is a valid filter that excludes nothing. So that typo silently pulls the
optional tiers back into the run. Again, pytest does not report the unknown name, so nothing tells
you that the filter is wrong. Read the collected count, not only the exit code.

`tests/test_marker_contract.py` enforces two naming rules:

- A live sidecar test must have a name of the form `test_*sidecar*.py`.
- A deep test uses the `test_*_deep.py` suffix.

The isolated workflows select their tests with those globs. The rule works in both directions:
every test in a file that matches one of those globs carries the marker too. The one exception is
`test_setup_sidecar.py`. The reason is that the `-m` option of the job deselects each test that
does not carry the marker.

A live sidecar test uses `requires_sidecar` from `tests/_sidecar_gate.py`. Where the
`sidecar / parity` job sets `TETHER_REQUIRE_SIDECAR=1`, that gate fails the test instead of
skipping it. The same module keeps the pytest command of every workflow from selecting a
`workstation` test. `tests/conftest.py` stops the collection when a `workstation` test also
carries `large`, `sidecar` or `deep`. Without the runner a `workstation` test skips. So a local
run that leaves out `and not workstation` still passes.

#### Golden numbers (the self-drift suite)

`tests/test_golden.py` runs three inputs from end to end:

- the committed 4-molecule SMD fixture,
- the synthetic movie of `tests/_synth.py`, through `tether batch`,
- synthetic traces that exercise every correction.

It compares each result with its golden file in `tests/golden/`. A golden file holds the molecule
counts, the per-molecule statistics and the histogram counts. It also holds the native HMM, the
TDP, the dwell rates and the SHA-256 of each exported table. `tests/test_golden_gui.py` checks that
the histogram dock draws the same histogram. The suite is a **regression suite, not an oracle**.
It fails when a number changes, not when a number is wrong.

Integers, strings and table checksums must match exactly. Floats must match to the relative limit
`FLOAT_RTOL` in the helper module of the suite.
[ADR-0080](docs/adr/0080-self-drift-golden-numbers.md) measured that limit across the three
operating systems of the matrix. Do not widen it to make a change pass.

When a change moves a golden number on purpose, use these two commands:

```bash
python scripts/update_golden.py --check   # list what moved; write nothing
python scripts/update_golden.py           # rewrite tests/golden/ and print the table
```

Paste the printed table into the pull request body. The table starts with
`| golden file | quantity | old | new | relative change |`. Add a line that starts with `Cause:`
and says why the numbers moved. An array that kept its shape gets one row for each changed
element, at most 20, and then one row that counts the rest.

The `golden-table` workflow fails a pull request that changes a file in `tests/golden/` without
both the table and the cause. It runs again when you edit the body. Never edit a golden file by
hand. A dependency update that moves a number fails here on purpose. The table and the cause are
then the record of what the update changed.

The full laboratory acquisitions of the maintainer have golden files too, in
`tests/golden/workstation/`. These files hold summary numbers only (#703). They are private, so
they run only on the reference workstation. Only the maintainer or an agent session there can
rewrite them, with `scripts/workstation/update_lab_golden.py`
([Running the private-data checks](docs/maintainers/workstation-runner.md)). The same table and
`Cause:` line apply. A change that moves one of these numbers fails only the weekly run on the
workstation, not a pull request.

#### Coverage floors

The Linux leg of the required matrix runs the same tests under `coverage`, with branch
measurement. It puts a table of line and branch coverage in the job summary, for the total and
for each package of `src/tether` (ADR-0072). `.github/coverage-floors.toml` holds one floor of line
coverage for the total and one for each package.

The job fails when a value is below its floor, and when a new package has no floor. A floor can
only go up: `tests/test_coverage_floors.py` fails when a floor is lower than on `main`. When the
job prints a notice that a floor can go up, raise it in the same pull request or a later one. To
see the numbers locally, run these commands:

```bash
python -m pip install "coverage==7.17.2"
QT_QPA_PLATFORM=offscreen coverage run -m pytest -m "not large and not sidecar and not deep and not workstation and not reference_download"
coverage json -o coverage.json && python scripts/check_coverage_floors.py coverage.json
```

## Write documentation

Write each documentation page to the
[writing style guide](docs/maintainers/writing-style.md). It gives the rules for sentences,
words, terms, warnings and page shape, and the files that the rules apply to. A reviewer can
cite a rule by its number. To build the site and run its checks, see
[Build the docs locally](#build-the-docs-locally).

Write each docstring in `src/tether` to the
[docstring standard](docs/maintainers/docstring-style.md). It asks for the NumPy style, a one-line
imperative summary and the unit of each physical quantity. It also asks for Markdown
cross-references in the form of mkdocstrings. Ruff checks the shape in the `lint` job (ADR-0093).

### Build the docs locally

The required `docs-build` gate is `mkdocs build --strict`, where **warnings are errors**. Register
a new page in the `nav` of `mkdocs.yml`, or the build fails. A new page must not link to
`docs/PRD.md`, because the site deliberately does not serve that file.

**ADR records are the exception.** `mkdocs.yml` matches them with `not_in_nav: adr/0*.md`. That
keeps each record *in the build*, so `--strict` still validates its links. It also keeps the record
out of the navigation tree. Only the index (`adr/README.md`) is in the `nav`. Do **not** add a new
ADR to the `nav`.

```bash
pip install -r requirements-docs.txt
mkdocs build --strict          # the gate
mkdocs serve                   # live preview at http://127.0.0.1:8000
```

`requirements-docs.txt` is the **only** pin of the docs toolchain. `pyproject.toml` has no `docs`
extra, so do not reach for `pip install -e .[docs]`.

`--strict` validates only the links that it can *resolve*. Two ordinary tests cover what it cannot
see, on all three operating systems:

- `tests/test_docs_links.py` checks the links of each page.
  - It fails on a reference-style `[text][key]` with no `[key]: …` definition in the same page.
    Python-Markdown renders such a link as literal text.
  - It fails on a relative link or `#anchor` that does not resolve. An anchor is a heading slug
    or an inline `<a id="…"></a>`.
  - Write a bare citation key as `[Key2018], [Key2005]`. Never write `[Key2018][Key2005]`, which is
    a reference link to a missing definition.
  - Its parse models the Markdown forms that its module docstring lists, and it promises nothing
    beyond them.
  - An unlisted form that it can detect, and that could change its verdict, fails. The message is
    `unsupported Markdown form, the checker cannot vouch for it`, with the file and the line.
    Rewrite the page in a listed form.
  - Any other unlisted form is not guaranteed. #1013 tracks the known gaps.
- `tests/test_docs_examples.py` checks each `from tether…` or `import tether…` line in a `python`
  fence.
  - The line must name a real module and object. The test reads the source and never imports it,
    so a deep-stack example resolves without torch.
  - The fence must open and close at column zero, as `fenced_code` requires.
  - A tether import line anywhere else on a page fails the test.

Every `docs-build` run uploads the rendered site as the `docs-site` artifact, kept for seven days.
A reviewer can then open the changed page, instead of inferring it from the Markdown. The
`external-links` job in `deps-audit.yml` checks the external links weekly, as advice only.

## Open a pull request

### Name the branch and the commit

- **Model: GitHub Flow.** All work happens on short-lived branches off `main`. Each branch opens
  as a PR, merges by **squash-merge** and is **deleted on merge**. There are no long-lived
  `develop` or `release` branches. Milestones are GitHub Milestones, not git branches.
- **Branch name:** `type/issue-N-short-slug`, optionally scoped to a milestone or an FR. Examples
  are `feat/issue-123-m1-atrous-detector` and `fix/issue-124-correct-nan-guard`. The slug is
  kebab-case, at most about 5 words. The branch name is not load-bearing: the PR title and the
  linked issue carry the authoritative metadata.
- **One exception, and it is load-bearing:** an automated agent claims an issue by atomically
  creating `agent/issue-N`, with **no slug**. That ref *is* the mutex: `POST /git/refs` answers
  `201` once and `422` after that. A slug derived from the title is not deterministic across
  agents. Two agents could then create two different refs for one issue, both could succeed, and
  that would silently void the mutex. See
  [ADR-0057](docs/adr/0057-github-native-swarm-coordination.md) and `AGENTS.md`.
- **Conventional Commits** govern **both the commit messages and the PR titles**. The form is
  `type(scope): summary (FR-ID when applicable)`.
  - The types are `feat fix docs chore refactor test ci build perf revert`.
  - The **scope is a §4.2 module** without the `tether.` prefix:
    `io | imaging | fret | idealize | ml | analysis | gui | project`. The cross-cutting scopes
    `schema | ci | deps | docs | release` are also allowed.
  - Examples are `feat(imaging): à trous wavelet spot detector (FR-EXTRACT)` and
    `fix(fret): never emit NaN factor on total-correction-failure (§7.2)`.
  - Mark a breaking change with `!` or a `BREAKING CHANGE:` footer. It is reserved almost
    exclusively for the deliberate, ADR-backed bump of the HDF5 schema version.
- **One concern per PR.** Keep each PR atomic and reviewable. Link the issue with a `Closes: #N`
  footer.

Source: `docs/PRD.md` §12.2.

### Add the license header

Tether is `GPL-3.0-or-later`. **Every source file carries** an
<!-- REUSE-IgnoreStart -->`SPDX-License-Identifier: GPL-3.0-or-later`<!-- REUSE-IgnoreEnd --> and
an `SPDX-FileCopyrightText` header. `REUSE.toml` covers the files that are not code. `reuse lint`
must be green, and pre-commit and CI enforce it. Add a header to every new source file.

Source: `docs/PRD.md` §12.1.

### Check the pull request before review

Before you ask for a review or merge, confirm each item:

- [ ] Tests are added or updated, and green on the 3-OS small-fixture matrix. New GUI behavior
      has a `pytest-qt` test.
- [ ] **The schema freeze is respected.** `schema-guard` is green. A structural change needs an
      ADR and a version bump.
- [ ] **conda-lock** is regenerated if the dependencies changed: base, sidecar or deep, each
      isolated. `conda-lock-verify` is green.
- [ ] Each new tunable is registered in `docs/PRD.md` §11.2, the single source of truth. It is
      not hardcoded.
- [ ] Each new analysis stamps its provenance, its parameters and the app version into the
      `.tether` (NFR-REPRO).
- [ ] Each new source file has the SPDX `GPL-3.0-or-later` header. `reuse lint` is green.
- [ ] **The docs are updated.** This covers the `mkdocs` pages under `docs/` *and* the public
      docstrings, for each user-facing thing that this PR changes. `docs-build` is green.
- [ ] **The data policy is respected.** No raw, private, unlicensed or large data is in ordinary
      Git. A redistributable fixture that the issue authorizes is in a named small or LFS/gated
      path, with its license and provenance.
- [ ] **No secrets are committed.** No token, key, credential or private path is in the code, the
      tests, the logs or the fixtures. `secret-scan` is green.
- [ ] Code scanning is clean: CodeQL reports no new alerts. The PR title is a Conventional Commit.
- [ ] **The risk is recorded, and the review lane is complete.**
  - Record the risk: `low`, `standard` or `high`. The risk routes nothing. The changed files
    decide the gate (ADR-0142).
  - Record the round.
  - Record a result from every provider that the lane reached. The result is **either** a
    substantive review **or** the quoted "nothing to review" of that provider for the head it
    read. A Codex 👍 counts as such a result.
  - **A code PR needs one metered review with no actionable comments.** That review is Greptile,
    or CodeRabbit when Greptile has no credit (ADR-0142). Its one-review cap can be spent with
    every finding disposed of. Then the Codex review of the final head that closed the gate in its
    place is required instead.
  - **A docs-only or test-only PR needs no metered review.** A clean posted Codex review of the
    final head is its gate.
  - That is a verdict that a completed review reached, not the absence of a review. Record the
    review itself: its permalink, and the commit it read. **That commit must be the final head
    for the review that closes the gate.**
  - On the cap-spent path, record CodeRabbit's review at the head that it read. The posted Codex
    review then closes the gate and names the final head. It names that head in either shape that
    `AGENTS.md` §Review describes.
  - Ask for that closing review with an `@codex review` comment, because the closer has to be
    posted and has to name the head it read. A local CLI run posts nothing.
  - A `PENDING` review has no `submitted_at`, so it is not a submitted review. A `DISMISSED`
    review is a verdict *withdrawn*, and it proves nothing.
  - **The `Actionable comments posted:` line is a convention of CodeRabbit.** CodeRabbit writes
    its clean verdict by leaving that line **out**, not by writing `0`. A Codex review has no such
    line. In its place, quote the verdict that Codex posted, against the commit that it names.
  - **A review of an earlier head does not close the gate.** That is true whether a material push
    or a non-material push moved the head. The review still stands as the completed review, and
    the cap-spent path relies on exactly that. It is recorded at the head it read, and the Codex
    closing review covers the final head.
  - The non-material case looks like an exception, but it is not one. A clean review followed by
    a formatting commit keeps its evidence. So the cap is spent with nothing outstanding, and the
    Codex close names the commit that `--match-head-commit` binds.
  - That is the ordinary close doing ordinary work, not a special path. It is strictly more work
    than the clean review that it follows.
  - Silence is not the gate, and a green `CodeRabbit` status check is not the gate. A request
    that reviewed **nothing** also leaves both of them behind. See the full-review command in
    [What review to expect](#what-review-to-expect).
  - Record the Greptile review of a code PR as this item asks for CodeRabbit's review. The
    `Last reviewed commit` of its summary comment is the head that it read. After its findings,
    record the Codex close.
  - With no Greptile credit, record the budget reading. Then record the CodeRabbit review. Nothing
    stands in for a CodeRabbit that refused: a refusal is a wait.
  - Fix the blocking findings. Defer the non-blocking findings to a follow-up issue, as
    `AGENTS.md` says.
- [ ] A resolved design decision that changed is updated in the PRD, an ADR or both, in the
      **same** PR.

Source: `docs/PRD.md` §12.4.

## AI-assisted contributions

AI assistance is allowed. Two rules apply, and they are not negotiable, because the failure modes
are silent:

- **Verify before you submit.** You are the author of everything that you open a PR with. Check
  generated code, docstrings and prose against what the code actually does. A plausible docstring
  that misstates behavior is worse than none, because people believe it. If you cannot verify a
  claim, do not ship it.
- **Check what you send.** You may want to paste unpublished code, unreleased data or anything
  under embargo into a third-party service. Before you do, confirm the policy of your group *and*
  the data-retention terms of that service. This repository is public, but not everything in your
  working tree is.

Fill the `AI assistance:` line of the pull-request template. Write `none`, or the tool that drafted
the change and its model where the tool reports it.

The cloud reviewers process the PR diffs according to the review lane in `AGENTS.md`. This is
third-party processing. Do not open a PR until its contents are safe to send.

If you use an AI tool to write or review documentation, also follow the
[AI-use policy for documentation work](docs/maintainers/ai-use-policy.md). It adds the rules for
the style guide, the validation by a person, the record of the tool and the safe and unsafe
practices.

## What review to expect

This section tells you how a pull request (PR) reaches `main`. The full review and merge rules
are in [`AGENTS.md`, section "Review"](AGENTS.md#review). Where the two differ, `AGENTS.md`
governs.

1. Open the PR as a draft.
2. Wait until every required check is green. Do not merge over a red or pending check.
3. Ask Codex for a review with an `@codex review` comment.
4. Fix each blocking Codex finding and ask Codex again. Repeat until Codex finds nothing blocking.
5. Find the kind of the PR with `python .agents/bin/greptile_usage.py --kind <PR>` (ADR-0142).
   A docs-only or test-only PR stops the review here. Its gate is the clean Codex review of the
   final head. Go to step 8.
6. A code PR is marked ready for review. It asks one standard Greptile review with an
   `@greptileai review` comment, while
   `python .agents/bin/greptile_usage.py --fail-over-share` exits 0. A clean Greptile review is the
   gate. A Greptile review with findings needs more. Treat the findings as step 7 says. Then ask
   Codex for a closing review of the final head. With no Greptile credit, the PR asks CodeRabbit
   instead. It asks once, in its turn in the queue, with an `@coderabbitai full review` comment.
   The incremental command reviews nothing here. A refusal is not that one review.
7. Fix each serious finding. Defer or answer the others as `AGENTS.md` says.
8. Resolve each review thread. An unresolved thread blocks the merge, even with green checks.

No provider reviews a PR automatically here, so a review that was not asked for does not come.
The one exception is Greptile on a branch cut before `.greptile/config.json` landed. A review
usually posts within an hour of the request. A provider can refuse for a quota or a rate limit,
and that can hold a PR for hours. A refusal is a wait, not a pass.

The `main-baseline` ruleset requires these **11** status checks: `lint` · `test (ubuntu-latest)` ·
`test (macos-latest)` · `test (windows-latest)` · `pre-commit` · `commitlint` · `secret-scan` ·
`conda-lock-verify` · `schema-guard` · `docs-build` · `sidecar / parity`. CodeQL is also
enforced, but not as a status check. It runs through code-scanning **default setup** and a
separate `code_scanning` rule, so `.github/workflows/` has no `codeql.yml`.

The maintainer merges, **squash-only**, with a linear history and the branch deleted on merge. An
agent merges only with explicit merge authority for that one PR. A green lane does not give that
authority. Arm auto-merge only after the review lane is complete. The lane is not a required
check, so GitHub can merge as soon as the checks are green. Arm it with this command:

```bash
gh pr merge <PR> --auto --squash --match-head-commit <SHA>
```

`<SHA>` is the 40-hex head that the clean review read. Take it from that review, not from the PR.
Releases, tags, signing and each new scientific claim or citation need the maintainer's sign-off.

Source: `docs/PRD.md` §12.2 and §12.6.

## Where the rules are

- [`AGENTS.md`](AGENTS.md): the operations and safety of agents, and the full review lane.
- [`docs/PRD.md`](docs/PRD.md): the product and the science. Section 12 governs the contribution process.
- [Writing style for documentation](docs/maintainers/writing-style.md): the rules for each page.
- [Docstring standard](docs/maintainers/docstring-style.md): the rules for each docstring.
- [The ADR index](docs/adr/README.md): why each decision was made.
- [`SECURITY.md`](SECURITY.md): how to report a vulnerability.
- The sections below: how the project is run, and the rules that protect it.

## How the project is run

### The development model: solo, with CI and review gates

`bioedca` maintains Tether alone. CI and a fixed review lane gate merges. Branch protection on
`main` enforces the required CI checks. Complete the self-review checklist on every PR. The
merger checks it during review. See the [review rules in `AGENTS.md`](AGENTS.md#review).

The ruleset requires zero approvals. Load-bearing changes need the qualified human or domain
judgment that `AGENTS.md` names. The model scales to required human reviews and `CODEOWNERS` as contributors join (PRD §12.3).

`main` is **always releasable and protected**. Never push to `main` directly. Never merge, release
or declare a PR ready while the required checks are red or pending.

### Identity and signed commits

- The single authoritative commit-author and committer identity for this repository is
  **`bioedca@u.northwestern.edu`**. This convention comes from §12. The other address of the
  account, `bioedca@gmail.com`, is **not** used for the commits of the repository.
- **Commits and tags are SSH-signed**, so that GitHub shows the *Verified* badge:

  ```bash
  git config user.email bioedca@u.northwestern.edu
  git config gpg.format ssh
  git config user.signingkey <path-to-ssh-public-key>
  git config commit.gpgsign true
  git config tag.gpgsign true
  ```

  The signing key must be registered to the account as a **Signing key**. The committer email
  must be on the list of verified emails of the account.
- **2FA is required** on the account.

Source: `docs/PRD.md` §12.1.

### Two load-bearing invariants: do not break them

1. **The HDF5 schema is additive-only after M0.** The §5 `.tether` group skeleton is
   **frozen at M0**. Only additive *data* is allowed. The CI **`schema-guard`** gate enforces
   it. A legitimate structural change carries an **ADR and an explicit schema-version bump**.
   Never make a silent structural edit.
2. **`conda-lock` is pin-and-hold.** Never casually bump a dependency. Regenerate the affected
   locks: base, isolated sidecar, isolated deep, or more than one. Keep all three separate. The
   `numpy<2` and PyQt5 stack of the sidecar and the PyTorch stack of deep must never merge into
   the PySide6 base. Confirm that `conda-lock-verify` is green.

### Propose an ADR

Architecture Decision Records under [`docs/adr/`](docs/adr/README.md) are where the *rationale*
survives. They are load-bearing here. The PRD records what was decided. The ADR records **why**,
and which options were rejected.

Write an ADR when a change settles a question that a future reader could reasonably decide
differently. Examples are a schema-affecting change, a dependency or isolation boundary, an
algorithm choice with a scientific trade-off, or anything that supersedes an earlier ADR. Routine
bug fixes and refactors do not need one.

1. Reserve the number first, as [Reserving a number](docs/agents/adr.md#reserving-a-number)
   tells you. Do not pick the next number by reading `docs/adr/`. Then copy
   [`docs/adr/0000-template.md`](docs/adr/0000-template.md) to `NNNN-kebab-title.md`, where `NNNN`
   is the reserved number. Gaps in the numbers are allowed. Never reuse a number.
2. Fill all five frontmatter fields: **Status**, **Date**, **Deciders**, **PRD anchor** and
   **Milestone**. Keep the MADR headings from the template.
3. Set `Status` to `proposed`, `accepted`, `deprecated` or `superseded by ADR-NNNN`. When a record
   supersedes another, say so in both. Link them with a real Markdown link, such as
   `[ADR-0004](0004-pin-and-hold-dual-lock-isolation.md)`. A bare `[ADR-0004]` renders as literal
   brackets.
4. **Add the row to [`docs/adr/README.md`](docs/adr/README.md) in the same PR.** Use the H1 of
   the record as the Title cell. `tests/test_adr_index.py` enforces that the index is complete,
   that every link resolves and that the titles match their headings.
5. **Land the ADR in the PR that implements the decision.** This is the §0.4 DoD rule. An ADR
   merged apart from its implementation drifts immediately. **There is one exception.** An
   accepted issue whose whole scope is the contract for named implementation issues may land its
   ADR alone, before them. The ADR must then name those issues, on its *Follow-up* line or under
   *More information*. The contract issues #892, #893 and #894 of plan #890 are of this kind.

Source: `docs/PRD.md` §12.7.

### How an issue becomes ready for work

Every public form starts at `status:backlog`, and no form can apply `status:ready`. Work moves
through **new → `status:backlog` → groomed → exact-body maintainer approval → `status:ready`**.
Grooming confirms these things:

- testable acceptance criteria,
- the dependencies,
- the execution autonomy,
- the related work and the file overlap,
- a scope of one PR.

After grooming, a maintainer approves the exact snapshot of the title and the body.

## License of your contribution

By contributing, you agree that your contributions are licensed under `GPL-3.0-or-later`.

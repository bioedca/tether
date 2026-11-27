---
description: "Which Tether surfaces keep a compatibility promise in the 1.x line, which do not, and how a name is retired."
---

# Stability policy

**Who this page is for.** Anyone who is about to depend on Tether not changing under them. One example
is a lab that standardizes on one version. Others are someone who scripts `tether batch` into a
pipeline, and another group that writes a tool to read `.tether` files. No repository access is needed to read
this page or to rely on it.

The page says exactly which surfaces carry a compatibility promise in the `1.x` line, and which
deliberately do not. It also says what happens when two people in the same lab run different
versions, and how a name is retired.

Tether follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html). The version comes from
the git tag at build time (`hatch-vcs`, `[tool.hatch.version]` in `pyproject.toml`). At runtime,
read it as `tether.__version__` or with `tether --version`.

## How to read this page

Each table names one item on each row: a release, a covered surface, a name or a rule. The last
column gives its promise or its meaning in at most 25 words. A longer note is in the numbered notes
below the table.

A warning before a table gives a fact that can break a script or a reader with no error. The
[Background](#background) section at the end gives the reasons behind the policy.

## What each release may change

| Release | May do | May not do |
|---|---|---|
| Patch (`1.2.0` → `1.2.1`) | Fix bugs. Change anything in the [Not covered](#not-covered) list. | Change a covered surface. |
| Minor (`1.2.1` → `1.3.0`) | Add a CLI subcommand, an optional flag, a name, a group, a dataset or a struct field. Widen a promise, or deprecate a covered name. | Remove or rename a covered name, change a documented default, or break a `.tether` reader. |
| Major (`1.x` → `2.0.0`) | Remove deprecated names, and make breaking changes to any covered surface. | — |

Widening a promise (adding something to the stable list below) is a minor release. Retracting one
is a major release ([Why the stable list is short](#why-the-stable-list-is-short)).

## What SemVer covers in Tether 1.x

Three surfaces carry the promise, and nothing else does. Every other user-facing surface, also the
environment variables, is in [Not covered](#not-covered) below.

### 1. The command line

This surface is the `tether` console script (`[project.scripts]` → `tether.cli:main`) and the
`tether-gui` launcher (`[project.gui-scripts]` → `tether.gui.app:main`).

| Covered | Promise |
|---|---|
| Subcommand names | `tether extract`, `tether batch`, and `tether export` with its three forms `table`, `traces` and `subset` (`tether.cli`). |
| `tether --version` | Prints the version and exits `0`. |
| `tether-gui` | Exists as a launcher and starts the application. Nothing about the window layout or its behavior is covered. |
| Flag names and meanings | Every option that `tether extract --help`, `tether batch --help` and `tether export <form> --help` list keeps its name and meaning for all of `1.x` (note 1). |
| Documented defaults | The default values printed in `--help` do not change within `1.x` (note 2). |
| Exit status | `0` on success, and not `0` otherwise. The exit codes are part of this contract (note 3). |

**Notes**

1. The four sidecar options are an exception, in the next subsection.
2. Examples are `--donor-side left`, `--window 21`, `--coloc-distance 3` and `--rms-gate 0.5`.
   Tether treats a changed default as a breaking change, not a small adjustment
   ([Why a changed default is a breaking change](#why-a-changed-default-is-a-breaking-change)).
3. `1` means that the work failed: an `ExtractionError`, at least one movie that failed in a batch,
   or at least one export that failed. `2` means a usage or configuration error. `3`, for
   `tether batch` only, means that no movie failed but at least one requested stage was deferred.
   `1` takes precedence over `3`. See [Exit codes](cli.md#exit-codes).

**Sidecar carve-out.** Four `tether batch` options describe the
[sidecar interface](#not-covered), which is explicitly *not* covered: `--sidecar-python`,
`--sidecar-timeout`, `--max-restarts` and `--no-defer`. Their **names** are covered like any other
flag. Their semantics and their defaults follow the sidecar and can change.

> **Do not treat the `3` and the `1800` seconds that `--help` prints as frozen.** `--max-restarts`
> and `--sidecar-timeout` are both `default=None` in `tether.cli`. The effective defaults are
> `DEFAULT_MAX_RESTARTS` and `DEFAULT_SIDECAR_TIMEOUT` in `tether.idealize.supervisor`, an
> uncovered module. The two numbers in `--help` are literals in the help strings, and a committed
> test keeps them equal to those constants.

These parts of the command line are not covered:

- The wording and layout of anything printed to stdout or stderr. This includes the exit-code line
  at the end of the `tether batch` report.
- The field set of the `batch` JSONL structured log.

The four exit codes above are covered, so a script can branch on them. `3` means "run this batch
again later", not "these projects are complete".

New optional flags and new subcommands can appear in a minor release. Nothing that exists today is
removed before `2.0.0`.

### 2. The `.tether` on-disk format

The project file is HDF5. These parts are covered for `1.x`:

- the root `format` attribute value `tether-project` (`FORMAT_TAG`),
- the `schema_version` attribute and its rules,
- the top-level group skeleton,
- the field sets of the compound dtypes of the seven entity tables.

| Covered | Promise |
|---|---|
| Format marker | The root attribute `format == "tether-project"`. |
| Entity groups | `/molecules`, `/movies`, `/labels`, `/conditions`, `/generations` (from version 2), `/sources` (from version 3) and `/calibration_windows` (from version 4). Each holds a compound dataset named `table`. |
| Container groups | `/calibration`, `/traces`, `/patches`, `/idealization`, `/settings`, `/features` and `/models`. |
| Existing fields | A field in a frozen compound dtype is never removed, renamed, retyped or reordered. New fields go *after* the existing ones (note 1). |
| `schema_version` | It only increases, and it is never decremented. |

**Notes**

1. So the on-disk byte layout of the existing fields is stable. A binary reader can index the old
   fields by position.

The `schema-guard` CI job enforces the rules mechanically, not by review. It runs
`scripts/dump_schema.py --check`. That script dumps the structure that `tether.io.schema` declares,
and it compares it with the committed golden manifest `schema/schema_frozen.json`:

- Adding a group, a dataset, or a field *appended after the existing fields* passes.
- Removing, renaming or retyping a frozen field fails, and the message names the field.
- Inserting a field in the middle of the list, or reordering one, also fails. The layout of a
  compound dtype is positional, and `_diff_compound` requires the golden field sequence to stay an
  exact prefix of the current one.
- A decremented `schema_version` fails.

The freeze itself is [ADR-0005](adr/README.md).

Tether stamps the root `app_version` attribute for provenance. Its **value** is deliberately not
frozen. Only `format` and `schema_version` have their values pinned (`_VALUE_ATTRS` in
`tether.io.schema`). So the stamp changes on every release without failing `schema-guard`. The
attribute itself is in the manifest as `{"dtype": "str"}`. Removing it fails with
`frozen attribute removed: /@app_version`, and retyping it fails with a message that names the dtype
change.

> **Read `app_version` as optional, and as a string when it is present
> (`f.attrs.get("app_version")`).** Do not reject a file because it lacks the attribute. That would
> reject output that the stable API is documented to make.

That rule freezes the **writer**, not every file. `create_project(path, stamp_app_version=False)` is
on the covered list, and it deliberately leaves the attribute out. `assert_is_compatible_project`
does not look for it, so a project written that way is a complete, valid `.tether`. What is frozen
is this: a build that stamps the attribute writes a string under that name, and the name is never
removed or retyped. The version that it carries is not a covered value.

See [Project file compatibility](#project-file-compatibility) below for what happens across
versions.

### 3. The Python API

**The rule is the table. If a name is not in it, it is not covered.** There is no blanket promise
that "the public API is stable". At the time of writing, the `__all__` lists under `src/tether/`
name 862 objects across 92 modules. Freezing all of them until `2.0.0` is not a promise that this
project could keep.

| Covered name | Kind | Promise |
|---|---|---|
| `tether.__version__` | `str` | Present and importable. |
| `tether.io.schema.SCHEMA_VERSION` | `int` | Present. It only increases. |
| `tether.io.schema.FORMAT_TAG` | `str` | Present. The value stays `"tether-project"`. |
| `tether.io.schema.TABLE` | `str` | Present. The value stays `"table"`, the name of the dataset inside each entity group. |
| `tether.io.schema.MOLECULES_DTYPE` | `numpy.dtype` | Present. Fields are additive-only. |
| `tether.io.schema.MOVIES_DTYPE` | `numpy.dtype` | Present. Fields are additive-only. |
| `tether.io.schema.LABELS_DTYPE` | `numpy.dtype` | Present. Fields are additive-only. |
| `tether.io.schema.CONDITIONS_DTYPE` | `numpy.dtype` | Present. Fields are additive-only. |
| `tether.io.schema.create_project(path, *, overwrite=..., stamp_app_version=...)` | function | Signature and behavior. |
| `tether.io.schema.read_schema_version(path)` | function | Signature and behavior. |
| `tether.io.schema.assert_compatible(file_version)` | function | Signature. It raises `ValueError` on a newer file. |
| `tether.io.schema.assert_is_compatible_project(path)` | function | Signature. It raises `ValueError` on a non-project, an incomplete skeleton or a newer file, and returns the on-disk `schema_version`. |
| `tether.project.Project` | class | The class exists and can be imported from `tether.project`. |
| `Project.create(path, *, overwrite=..., identity=...)` | classmethod | Signature and behavior. |
| `Project.open(path, *, identity=...)` | classmethod | Signature and behavior. |
| `Project.path` | attribute | A `pathlib.Path`. |
| `Project.schema_version` | property | The on-disk version. |
| `Project.app_schema_version` | property | The version that this build writes. |

That is the whole list. `Project` is in it as a **class with five named members**. The other
methods of `Project` are not covered, and neither is the identity and locking model that they use.

The exception classes that those functions raise are covered only as the table states:
`assert_compatible` and `assert_is_compatible_project` raise `ValueError`. The *text* of an
exception message is not covered. The one exception is the schema refusal quoted below, because
users paste it into bug reports.

## Not covered

Everything below can change in any release, also in a patch release, without notice.

| Surface | What is not covered |
|---|---|
| Any name not in the table above | This is the default (note 1). |
| Everything under `tether.gui.*` | Every module, class, dock, dialog and function. `tether-gui` starting is covered, and nothing inside it is (note 2). |
| Underscore names | Any module, attribute, function or method whose name starts with `_` (note 3). A leading underscore always means internal. |
| The sidecar interface | The isolated tMAVEN idealization backend and everything that talks to it (note 4). |
| Environment variables | `TETHER_SIDECAR_PYTHON` (`SIDECAR_ENV_VAR` in `tether.idealize.driver`) is the only Tether-namespaced environment variable that the application reads (note 5). |
| Console output and log formats | Progress text, warning wording, report layout, and the schema of the `batch` JSONL log. |
| Exception message text | All of it, except the schema refusal quoted below. |
| Everything else in the repository | Test helpers, `scripts/`, `packaging/`, fixtures, CI workflow names, and the serialized form of the golden manifest. |

**Notes**

1. `tether.analysis`, `tether.imaging`, `tether.fret`, `tether.ml` and `tether.idealize` are
   **not covered**. Neither is any module of `tether.project`, other than the five `Project`
   members named above.
2. The GUI is a layer over the headless core. It is meant to be used, not imported.
3. Examples are `tether.analysis._store`, `tether.project.extract._layout_geometry` and
   `tether.io.schema._RICH_TABLES`.
4. That is `tether.idealize._sidecar_runner` and its argv contract, the `TETHER_SIDECAR_STATUS`
   stdout protocol, and the pinned `sidecar/conda-lock.yml` environment
   ([Why the sidecar interface is not covered](#why-the-sidecar-interface-is-not-covered)). See the
   [standalone-tMAVEN hand-off page](idealize/standalone-tmaven-handoff.md).
5. The [standalone-tMAVEN hand-off page](idealize/standalone-tmaven-handoff.md) documents how to
   export it to point at a sidecar interpreter, which is supported. It will not be removed casually.
   But it belongs to the uncovered sidecar interface, so it is not frozen for `1.x`.
   `TETHER_SIDECAR_STATUS` is not a variable at all. It is the stdout line prefix of the sidecar
   protocol, listed above.

> **Worked example.** Is `tether.analysis.histogram.Histogram1D` covered by the `1.x` promise?
> **No.** It is not in the stable table, so it can change in any release. It is a good thing to
> use, but pin your Tether version if you do. Read the [release notes](release.md#change-record)
> and the [deprecation policy](#deprecation-policy) before you upgrade.

If you depend on something in the "not covered" list and want it promoted, open an issue. Name the
exact symbol and what you use it for. Widening the list is a minor release. So it is cheap to say
yes to a concrete request, and expensive to say yes to a blanket one.

## Project file compatibility

The on-disk contract in one line: **`schema_version` only increases, and changes are additive-only.
Older files open in newer apps. A file stamped with a newer `schema_version` never opens in an
older app.**

| Rule | What it means |
|---|---|
| Monotonic | `schema_version` only increases. `SCHEMA_VERSION` is currently **4** (`tether.io.schema`). |
| Additive-only | A release can add a group, a dataset, or a field *appended to the end of* a compound dtype. It cannot remove, rename, retype or reorder one (note 1). |
| Structural changes are deliberate | A change that is not purely additive needs an ADR, a `schema_version` bump and a regenerated `schema/schema_frozen.json` in the same pull request. `schema-guard` fails the PR otherwise. |
| Forward-only | Tether refuses, loudly, a file stamped with a `schema_version` newer than the running app's. There is no downgrade path and no partial read. |

**Notes**

1. Whatever is added must be **optional on read**. A project written by an earlier `1.x` does not
   contain it. So a newer app must treat it as absent or defaulted, and must not assume it.

> **Read "forward-only" as a promise about the stamp, not about the bytes.** The check is
> `schema_version`, and only `schema_version`. `assert_compatible` compares the file's stamp with
> the running app's `SCHEMA_VERSION`, and it never reads `app_version`, whose value is not frozen.
> So the promise is "a file stamped newer never opens in an older app". It is not "an older app
> never sees bytes that a newer app wrote".

An additive release can add a group, a dataset or an appended field *without* bumping the stamp.
The older app deliberately does not refuse that file. That is the point of additive-only. The
older app still finds the whole skeleton that it declares, and it ignores the structure that it
does not know. The new data stays out of its way and is not misread.

Anything that an older app
could **not** safely ignore is, by definition, not additive. A non-additive change carries a
`schema_version` bump under the rule above, and that bump puts it back behind the refusal.

### Older files in newer apps

A project written by an earlier `1.x` opens in a later `1.x`. This is a commitment about how Tether
is allowed to add. It constrains the additive rule, and it does not follow from that rule for free.
`assert_is_compatible_project` checks that the file carries the whole top-level skeleton *that the
running app declares* (`_RICH_TABLES` and `_CONTAINER_GROUPS` in `tether.io.schema`). So a release
that added a new top-level group would make every older file fail that check. Any such bump must
therefore also make the new structure optional for files stamped at the older version.

That has happened twice:

- Version 2 added the `/generations` table (ADR-0136). `_missing_skeleton` asks for it only of a
  file stamped 2 or newer (`_RICH_TABLES_SINCE`). So a file stamped 1 stays a complete project, and
  it reads as generation 1 of each movie. The first writer that records a generation into such a
  file adds the empty table and stamps it 2 (`upgrade_to_generations`). Nothing else in the file is
  rewritten. The publish that raises the stamp first keeps the file as it was beside it, as
  `<stem>.v1-backup.tether`.
- Version 3 added the `/sources` table, the source registry (ADR-0140), in the same way. A file
  stamped 1 or 2 reads as one source record for each of its movies. The first registry write adds
  every missing table (`upgrade_skeleton`), stamps the file 3 and keeps
  `<stem>.v<n>-backup.tether`.

Every other group in the skeleton was declared empty in advance at M0, so that later milestones add
*data*, not structure.

> **Read data added in a later `1.x` defensively.** Use `group.get(name)` or
> `name in table.dtype.names`, backfill a default, or bump `schema_version` and migrate. No guard
> checks this for you.

The same constraint applies one level down, where no guard can see it.
`assert_is_compatible_project` checks the *top-level* skeleton only (`_missing_skeleton`). So it
cannot see a dataset added inside `/traces` or `/models`, or a field appended to a frozen compound
dtype. An older file passes the check cleanly, and then a reader that assumed the new thing fails
on real data.

Additive **data** therefore carries the same obligation as additive structure. Making
newly added data *required* breaks the older-files promise and is not an addition. It is not
permitted inside `1.x` without the bump.

### Newer files in older apps

This is the failure that two people in the same lab actually hit. One of them upgrades and saves a
project, and the other cannot open it. `assert_compatible` raises `ValueError`, and the guarded
entry points reach it through `assert_is_compatible_project`. With `SCHEMA_VERSION = 4`, a file
stamped version 5 gives exactly this:

```text
file schema_version 5 is newer than this app's 4; refusing to open (PRD section 5.4).
```

Five call sites in `src/tether/` call that guard, and they are the ones that a person goes
through:

| Guarded entry point | Reached by |
|---|---|
| `Project.open` (`tether.project.core`) | The **Open project** dialog of the GUI (`tether.gui.shell`), and any script that opens a project the covered way. |
| `extract_movie` (`tether.imaging.extract`) | `tether extract`, and the extract stage of `tether batch`. |
| `write_calibration` (`tether.imaging.calibrate`) | Writing a registration map into a project. |
| `add_movie_to_project` (`tether.project.extract`) | Appending a movie into an existing project. |
| `export_subset_tether` (`tether.project.export`) | Subset export. |

A `tether batch` **resume** reaches none of those five. Its checkpoint probes read the output
project directly to decide what to skip. So, with extraction already complete, the guard inside
`write_extraction` never runs. That path has its own check (`tether.project.batch`), which is
deliberately narrower than `assert_is_compatible_project`. It refuses only a file that carries the
`format` marker *and* declares a newer `schema_version`. A missing, unreadable, foreign or
half-written project still goes through
([Why the resume check is narrower](#why-the-resume-check-is-narrower)).

One route is **not** guarded:

> **Use `Project.open`, not `Project(path)`, whenever the file came from elsewhere.** Constructing
> `Project(path)` directly does no validation. `Project(path).schema_version` on a version-4 file
> returns `4` and does not raise. `Project.open` *is* on the covered list.

The constructor deliberately owns the path, not an open HDF5 handle, and each read method opens the
file for its own scope. That gap does not let you rely on reading a future file. The forward-only
rule is the policy. A route that skips the check is a bug to report under
[If a covered surface breaks anyway](#if-a-covered-surface-breaks-anyway). It is not an
alternative supported path.

**The remedy is to upgrade Tether** to at least the version that wrote the file (see the
[installers page](packaging.md)). There is no downgrade path. The older application cannot read the
file in part or strip the newer fields. It does not guess
([Why an older app refuses a newer file](#why-an-older-app-refuses-a-newer-file)).

> **Do not look for the "PRD section 5.4" that the message names.** It is an internal document that
> is deliberately not published on this site. This page states the part of it that the message
> points at: newer files never open in older apps, and the fix is to upgrade. If you file a bug
> report, paste the message as it is, because its version numbers are the useful part.

§5.4 is a broader section. It also covers single-writer locking, stale-lock recovery and movie
relink.

To find out what wrote a file before you try to open it, read the stamp directly:

```python
from tether.io.schema import SCHEMA_VERSION, read_schema_version

print(read_schema_version("experiment.tether"), "vs", SCHEMA_VERSION)
```

Both names are on the stable list, so that snippet keeps working for the whole `1.x` line.

## Deprecation policy

This is a policy that the project adopts for the future of `1.x`. It does not describe machinery
that exists. **Nothing has been deprecated yet.** `grep -rn deprecat src/` returns no matches,
because no name on the stable list has needed to be retired. The policy exists so that the first
one is handled predictably.

It applies only to the covered surfaces above. Names in the "not covered" list can change at any
time, and they get no deprecation period at all. That is what "not covered" means.

| Question | Answer |
|---|---|
| How long does a deprecated name survive? | **At least two further minor releases, and in practice the rest of the `1.x` line** (note 1). Removing a covered name inside `1.x` is not permitted, whatever notice was given. |
| Is a `DeprecationWarning` emitted? | **Yes, and at call time, not at import time** (note 2). None of this machinery exists yet. |
| Where is it announced? | **The GitHub release notes** for the release that introduces the deprecation, at [github.com/bioedca/tether/releases](https://github.com/bioedca/tether/releases). There is no `CHANGELOG.md` in this repository (note 3). |
| Does the `2.0.0` release note list the removals? | **Yes**, under **⚠ Breaking Changes**, a heading that sets them apart (note 4). A deprecation itself is not listed there. |

**Notes**

1. A covered name deprecated in `1.4.0` still works in `1.5.0`, `1.6.0` and every later `1.x`. It
   is removed only in `2.0.0`. `2.0.0` will not be tagged sooner than two minor releases after the
   deprecation is announced.
2. A deprecated function or method **emits** a `DeprecationWarning` through `warnings.warn`, and
   then does what it always did. A deprecated constant or module attribute emits one on attribute
   access, through a module-level `__getattr__` ([PEP 562](https://peps.python.org/pep-0562/)), and
   still returns its value.
    - The warning is emitted, never raised. A deprecated name keeps working for the rest of `1.x`,
      so a caller who ignores the warning is not broken by it.
    - Importing an unrelated part of `tether` never emits one.
    - The message names the release that deprecated the name and the replacement to use. Or it
      says plainly that there is no replacement.
    - There is no deprecation helper, decorator or `__getattr__` in `src/` today. It will be written
      with the first deprecation.
3. So do not look for one. The Releases page is the record of every published Release. The
   [change record](release.md#change-record) says how the notes are made and where the history
   before 1.0 is. The [release pipeline](release.md) describes how a Release is cut.
4. The notes are generated mechanically from Conventional-Commit subjects (`release.yml`), and the
   [change record](release.md#change-record) lists their sections.
    - A `feat!:` subject, or any commit with a `BREAKING CHANGE:` footer, is listed first, under
      **⚠ Breaking Changes**.
    - `feat!:` marks the break itself: the `2.0.0` commit that actually removes a covered name. The
      [pull-request template](https://github.com/bioedca/tether/blob/main/.github/pull_request_template.md)
      also keeps `!` and `BREAKING CHANGE:` for a deliberate breaking change. A deprecated name that
      still works is not one.
    - A deprecation ships as an ordinary `feat:` or `docs:` commit whose subject names the
      deprecated symbol. So search the notes for the symbol, not for a `!`.

> **Run with `python -W default::DeprecationWarning` to see the warnings in a script or a batch
> job.** Python hides `DeprecationWarning` by default outside `__main__`. `pytest` shows them by
> default.

A deprecated CLI flag follows the same clock. It keeps working and keeps its meaning, and it prints
a one-line notice to stderr that names its replacement.

## If a covered surface breaks anyway

That is a bug, not a change of policy. Open an issue at
[github.com/bioedca/tether/issues](https://github.com/bioedca/tether/issues). Name the covered
surface, the version that changed it, and the version that you upgraded from. If the break has a
security side, follow [SECURITY.md](https://github.com/bioedca/tether/blob/main/SECURITY.md) and
report it privately instead.

## Background

### Why the stable list is short

Widening a promise is a minor release, and retracting one is a major release. Because of that
asymmetry, the stable list is kept short.

### Why a changed default is a breaking change

A default that changes silently changes a scientific result. So Tether treats it as a breaking
change, not as a small adjustment.

### Why the sidecar interface is not covered

The sidecar is an implementation detail of idealization. It is pinned to an old numeric stack that
the project does not control.

### Why the resume check is narrower

A run that crashed must stay resumable. An incomplete store is a stage to do again, not a movie to
fail.

### Why an older app refuses a newer file

Refusing is the point. Quietly reading a file whose structure you do not fully understand is how an
analysis silently loses a correction factor.

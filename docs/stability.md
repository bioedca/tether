# Stability policy

**Who this page is for.** Anyone who is about to depend on Tether not changing under them:
a lab standardising on one version, someone scripting `tether batch` into a pipeline, or
another group writing a tool that reads `.tether` files. It says exactly which surfaces
carry a compatibility promise in the `1.x` line, which deliberately do not, what happens
when two people in the same lab run different versions, and how a name is retired.

Tether follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html). The version is
derived from the git tag at build time (`hatch-vcs`, `[tool.hatch.version]` in
`pyproject.toml`) and is readable at runtime as `tether.__version__` or `tether --version`.

## What each release may change

| Release | May do | May not do |
|---|---|---|
| Patch (`1.2.0` → `1.2.1`) | fix bugs; change anything in the [Not covered](#not-covered) list | change a covered surface |
| Minor (`1.2.1` → `1.3.0`) | add a CLI subcommand or optional flag; add a name, a group, a dataset or a struct field; widen a promise; deprecate a covered name | remove or rename a covered name; change a documented default; break a `.tether` reader |
| Major (`1.x` → `2.0.0`) | remove deprecated names; make breaking changes to any covered surface | — |

Widening a promise (adding something to the stable list below) is a minor release.
Retracting one is a major release. That asymmetry is why the stable list is short.

## What SemVer covers in Tether 1.x

Three surfaces carry the promise, and nothing else does. Every other user-facing surface —
including environment variables — falls under [Not covered](#not-covered) below.

### 1. The command line

The `tether` console script (`[project.scripts]` → `tether.cli:main`) and the `tether-gui`
launcher (`[project.gui-scripts]` → `tether.gui.app:main`).

| Covered | Detail |
|---|---|
| Subcommand names | `tether extract` and `tether batch` — the only two (`tether.cli`). No others exist to promise. |
| `tether --version` | Prints the version and exits `0`. |
| `tether-gui` | Exists as a launcher and starts the application. Nothing about the window layout or its behaviour is covered. |
| Flag names and meanings | Every option listed by `tether extract --help` and `tether batch --help` keeps its name and its meaning for the whole `1.x` line. See the sidecar carve-out below. |
| Documented defaults | The default values printed in `--help` — e.g. `--donor-side left`, `--window 21`, `--coloc-distance 3`, `--rms-gate 0.5` — do not change within `1.x`. A default that changes silently changes a scientific result, so it is treated as a breaking change, not a tweak. |
| Exit status | `0` on success, non-zero on failure. |

**Sidecar carve-out.** Four `tether batch` options — `--sidecar-python`, `--sidecar-timeout`,
`--max-restarts` and `--no-defer` — describe the [sidecar interface](#not-covered), which is
explicitly *not* covered. Their **names** are covered like any other flag; their semantics
and their defaults follow the sidecar and may change. Concretely, `--max-restarts` and
`--sidecar-timeout` are both `default=None` in `tether.cli`; the values `3` and `1800`
printed in `--help` come from `DEFAULT_MAX_RESTARTS` and `DEFAULT_SIDECAR_TIMEOUT` in
`tether.idealize.supervisor`, an uncovered module. Do not treat those two numbers as frozen.

Not covered on this surface: the wording and layout of anything printed to stdout or
stderr, the field set of the `batch` JSONL structured log, and the *specific* non-zero exit
values. For the record, `tether.cli` currently returns `1` when the work itself failed
(an `ExtractionError`, or at least one movie failed in a batch) and `2` for a usage or
configuration error — but only "non-zero" is promised. Do not branch on `1` vs `2`.

New optional flags and new subcommands may appear in a minor release. Nothing that exists
today disappears before `2.0.0`.

### 2. The `.tether` on-disk format

The project store is HDF5. Covered for `1.x`: the root `format` attribute value
`tether-project` (`FORMAT_TAG`), the `schema_version` attribute and its rules, the
top-level group skeleton, and the compound-dtype field sets of the four entity tables.

| Covered | Detail |
|---|---|
| Format marker | Root attribute `format == "tether-project"`. |
| Entity groups | `/molecules`, `/movies`, `/labels`, `/conditions` — each a group holding a compound dataset named `table`. |
| Container groups | `/calibration`, `/traces`, `/patches`, `/idealization`, `/settings`, `/features`, `/models`. |
| Existing fields | A field in a frozen compound dtype is never removed, renamed, retyped, or reordered. New fields are appended *after* the existing ones, so the on-disk byte layout of the existing prefix is stable — a binary reader can index the old fields positionally. |
| `schema_version` | Monotonic; never decremented. |

The rules are enforced mechanically, not by review: the `schema-guard` CI job runs
`scripts/dump_schema.py --check`, which dumps the structure `tether.io.schema` declares and
diffs it against the committed golden manifest `schema/schema_frozen.json`. Adding a group,
a dataset, or a field *appended after the existing fields* passes. Removing, renaming or
retyping a frozen field fails, naming the field; so does inserting a field mid-list or
reordering one, because a compound dtype's layout is positional (`_diff_compound` requires
the golden field sequence to stay an exact prefix of the current one). A decremented
`schema_version` fails. The freeze itself is [ADR-0005](adr/README.md).

The root `app_version` attribute is stamped for provenance. Its **value** is deliberately
excluded from the freeze — only `format` and `schema_version` have their values pinned
(`_VALUE_ATTRS` in `tether.io.schema`), so the stamp changes every release without failing
`schema-guard`. The attribute itself is in the manifest as `{"dtype": "str"}`: removing it
fails with `frozen attribute removed: /@app_version`, and retyping it fails naming the
dtype change.

What that freezes is the **writer**, not every file. `create_project(path,
stamp_app_version=False)` is on the covered list and deliberately omits the attribute,
and `assert_is_compatible_project` does not look for it — a project written that way is
a complete, valid `.tether`. So read the stamp as *optional, string-typed when present*
(`f.attrs.get("app_version")`), not as guaranteed; rejecting a file for its absence
would reject output the stable API is documented to produce. What is frozen is that a
build which stamps it writes a string under that name, and that the name is never
removed or retyped. The version it carries is not a covered value.

See [Project file compatibility](#project-file-compatibility) below for what happens across
versions.

### 3. The Python API

**The rule is the table. If a name is not in it, it is not covered.** There is no blanket
"the public API is stable" promise — at the time of writing the `__all__` lists under
`src/tether/` name 862 objects across 92 modules, and freezing all of them until `2.0.0` is
not a promise this project could keep.

| Covered name | Kind | Promise |
|---|---|---|
| `tether.__version__` | `str` | Present and importable. |
| `tether.io.schema.SCHEMA_VERSION` | `int` | Present; monotonic. |
| `tether.io.schema.FORMAT_TAG` | `str` | Present; value stays `"tether-project"`. |
| `tether.io.schema.TABLE` | `str` | Present; value stays `"table"` (the dataset name inside each entity group). |
| `tether.io.schema.MOLECULES_DTYPE` | `numpy.dtype` | Present; fields additive-only. |
| `tether.io.schema.MOVIES_DTYPE` | `numpy.dtype` | Present; fields additive-only. |
| `tether.io.schema.LABELS_DTYPE` | `numpy.dtype` | Present; fields additive-only. |
| `tether.io.schema.CONDITIONS_DTYPE` | `numpy.dtype` | Present; fields additive-only. |
| `tether.io.schema.create_project(path, *, overwrite=..., stamp_app_version=...)` | function | Signature and behaviour. |
| `tether.io.schema.read_schema_version(path)` | function | Signature and behaviour. |
| `tether.io.schema.assert_compatible(file_version)` | function | Signature; raises `ValueError` on a newer file. |
| `tether.io.schema.assert_is_compatible_project(path)` | function | Signature; raises `ValueError` on a non-project, an incomplete skeleton, or a newer file; returns the on-disk `schema_version`. |
| `tether.project.Project` | class | The class exists and is importable from `tether.project`. |
| `Project.create(path, *, overwrite=..., identity=...)` | classmethod | Signature and behaviour. |
| `Project.open(path, *, identity=...)` | classmethod | Signature and behaviour. |
| `Project.path` | attribute | A `pathlib.Path`. |
| `Project.schema_version` | property | The on-disk version. |
| `Project.app_schema_version` | property | The version this build writes. |

That is the whole list. `Project` appears in it as a **class with five named members** —
the other methods on `Project` are not covered, and neither is the identity/locking model
they use.

The exception classes those functions raise are covered to the extent stated above:
`assert_compatible` and `assert_is_compatible_project` raise `ValueError`. The *text* of
any exception message is not covered, with one exception — the schema refusal quoted
verbatim below, which is quoted because users paste it into bug reports.

## Not covered

Everything below may change in any release, including a patch release, without notice.

| Surface | Why |
|---|---|
| Any name not in the table above | The default. `tether.analysis`, `tether.imaging`, `tether.fret`, `tether.ml`, `tether.idealize`, and every module of `tether.project` other than the five `Project` members named above are **not covered**. |
| Everything under `tether.gui.*` | Every module, class, dock, dialog and function. The GUI is a layer over the headless core; it is meant to be used, not imported. `tether-gui` starting is covered; nothing inside it is. |
| Underscore names | Any module, attribute, function or method whose name begins with `_` — for example `tether.analysis._store`, `tether.project.extract._half_split_geometry`, `tether.io.schema._RICH_TABLES`. A leading underscore means internal, in every case, without further qualification. |
| The sidecar interface | The isolated tMAVEN idealization backend and everything that talks to it: `tether.idealize._sidecar_runner` and its argv contract, the `TETHER_SIDECAR_STATUS` stdout protocol, and the pinned `sidecar/conda-lock.yml` environment. It is an implementation detail of idealization, pinned to an old numeric stack we do not control. See the [standalone-tMAVEN hand-off page](idealize/standalone-tmaven-handoff.md). |
| Environment variables | `TETHER_SIDECAR_PYTHON` (`SIDECAR_ENV_VAR` in `tether.idealize.driver`) is the only Tether-namespaced environment variable the application reads. The [standalone-tMAVEN hand-off page](idealize/standalone-tmaven-handoff.md) documents exporting it as the supported way to point at a sidecar interpreter, and it will not be removed casually — but it belongs to the uncovered sidecar interface, so it is not frozen for `1.x`. (`TETHER_SIDECAR_STATUS` is not a variable at all: it is the stdout line prefix of the sidecar protocol, listed above.) |
| Console output and log formats | Progress text, warning wording, report layout, and the `batch` JSONL log schema. |
| Exception message text | Except the schema refusal quoted below. |
| Everything else in the repository | Test helpers, `scripts/`, `packaging/`, fixtures, CI workflow names, and the golden manifest's serialised form. |

> **Worked example.** Is `tether.analysis.histogram.Histogram1D` covered by the `1.x`
> promise? **No.** It is not in the stable table, so it may change in any release. It is a
> perfectly good thing to use — just pin your Tether version if you do, and read the
> release notes (once there are any — see the [deprecation policy](#deprecation-policy))
> before upgrading.

If you depend on something in the "not covered" column and want it promoted, open an issue
naming the exact symbol and what you use it for. Widening the list is a minor release, so
it is cheap to say yes to a concrete request and expensive to say yes to a blanket one.

## Project file compatibility

The on-disk contract in one line: **`schema_version` is monotonic and additive-only; older
files open in newer apps; a file stamped with a newer `schema_version` never opens in an
older app.**

| Rule | What it means |
|---|---|
| Monotonic | `schema_version` only ever increases. `SCHEMA_VERSION` is currently **1** (`tether.io.schema`). |
| Additive-only | A new release may add a group, a dataset, or a field *appended to the end of* a compound dtype. It may not remove, rename, retype or reorder one. Whatever is added has to be **optional on read**: a project written by an earlier `1.x` will not contain it, so a newer app must treat it as absent-or-defaulted rather than assume it. |
| Structural changes are deliberate | A change that is not purely additive requires an ADR, a `schema_version` bump, and a regenerated `schema/schema_frozen.json` in the same pull request. `schema-guard` fails the PR otherwise. |
| Forward-only | A file stamped with a `schema_version` newer than the running app's is refused, loudly. There is no downgrade path and no partial read. |

> **What "forward-only" does and does not promise.** The check is `schema_version`, and
> only `schema_version`: `assert_compatible` compares the file's stamp against the running
> app's `SCHEMA_VERSION` and never reads `app_version` (whose value is deliberately
> unfrozen). So the promise is "a file stamped newer never opens in an older app" — not
> "an older app never sees bytes a newer app wrote". A purely additive release that adds a
> group, a dataset or an appended field *without* bumping the stamp is deliberately not
> refused, and that is the point of additive-only: the older app still finds the whole
> skeleton it declares, ignores the structure it does not know about, and the new data sits
> out of its way rather than being misread. Anything an older app could **not** safely
> ignore is by definition not additive, and a non-additive change carries a `schema_version`
> bump under the rule above — which is what puts it back behind the refusal.

### Older files in newer apps

A project written by an earlier `1.x` opens in a later `1.x`. This is a commitment about
how we are allowed to add, and it constrains the additive rule rather than following from
it for free: `assert_is_compatible_project` validates that the file carries the whole
top-level skeleton *the running app declares* (`_RICH_TABLES` and `_CONTAINER_GROUPS` in
`tether.io.schema`), so a release that introduced a new top-level group would make every
older file fail that presence check. Any such bump must therefore also make the new
structure optional for files stamped at the older version. It has not come up —
`SCHEMA_VERSION` is still 1 and every group in the skeleton was forward-declared empty at
M0 precisely so that later milestones add *data*, not structure.

The same constraint applies one level down, where no guard can see it.
`assert_is_compatible_project` validates the *top-level* skeleton only (`_missing_skeleton`),
so a dataset added inside `/traces` or `/models`, or a field appended to a frozen compound
dtype, is invisible to it: an older file passes validation cleanly and then a reader that
assumed the new thing is there fails on real data. Additive **data** therefore carries the
same obligation as additive structure — read it defensively (`group.get(name)`,
`name in table.dtype.names`), or backfill a default, or bump `schema_version` and migrate.
Making newly added data *required* is a break of the older-files promise, not an addition,
and is not permitted inside `1.x` without the bump.

### Newer files in older apps

This is the failure two people in the same lab actually hit: one of them upgrades, saves a
project, and the other cannot open it. `assert_compatible` raises `ValueError`, and the
guarded entry points reach it via `assert_is_compatible_project`. With `SCHEMA_VERSION = 1`,
a file stamped version 2 produces exactly this:

```text
file schema_version 2 is newer than this app's 1; refusing to open (PRD section 5.4).
```

Four call sites in `src/tether/` invoke that guard, and they are the ones a person goes
through:

| Guarded entry point | Reached by |
|---|---|
| `Project.open` (`tether.project.core`) | the GUI's **Open project** dialog (`tether.gui.shell`) and any script that opens a project the covered way |
| `extract_movie` (`tether.imaging.extract`) | `tether extract`, and the extract stage of `tether batch` |
| `write_calibration` (`tether.imaging.calibrate`) | writing a registration map into a project |
| `export_subset_tether` (`tether.project.export`) | subset export |

A `tether batch` **resume** reaches none of those four: the checkpoint probes read the output
project directly to decide what to skip, so with extraction already complete the guard inside
`write_extraction` never runs. That path has its own check (`tether.project.batch`), added
deliberately narrower than `assert_is_compatible_project` — it refuses only a file that
carries the `format` marker *and* declares a newer `schema_version`. A missing, unreadable,
foreign or half-written project still falls through, because a crashed run must stay
resumable and an incomplete store is a stage to redo rather than a movie to fail.

One route is **not** guarded, and you should know about it rather than be surprised:

- Constructing `Project(path)` directly does no validation — the constructor deliberately
  owns the path, not an open HDF5 handle, and each read method opens the file for its own
  scope. `Project(path).schema_version` on a version-2 file returns `2` instead of raising.
  Use `Project.open` (which *is* on the covered list) whenever the file came from elsewhere.

That gap is not a licence to rely on reading a future file: the forward-only rule is the
policy, and a route that skips the check is a bug to report under
[If a covered surface breaks anyway](#if-a-covered-surface-breaks-anyway), not an
alternative supported path.

**The remedy is to upgrade Tether** to at least the version that wrote the file — see the
[installers page](packaging.md). There is no downgrade path: the older application cannot
read the file partially, cannot strip the newer fields, and will not guess. Refusing is the
point. Quietly reading a file whose structure you do not fully understand is how analysis
silently loses a correction factor.

> **About that "PRD section 5.4" pointer.** The message names an internal document that is
> deliberately not published on this site, so it is not something you can go and read. Do
> not go looking for it. §5.4 is a broader section — it also covers single-writer locking,
> stale-lock recovery, and movie relink — but the part of it this message points at is
> stated on this page: newer files never open in older apps, and the fix is to upgrade. If
> you are filing a bug report, paste the message as-is anyway — the version numbers in it
> are the useful part.

To find out what wrote a file before you try to open it, read the stamp directly:

```python
from tether.io.schema import SCHEMA_VERSION, read_schema_version

print(read_schema_version("experiment.tether"), "vs", SCHEMA_VERSION)
```

Both names are on the stable list, so that snippet keeps working for the whole `1.x` line.

## Deprecation policy

This is a forward-looking policy the project is adopting for `1.x`, not a description of
existing machinery. **Nothing has been deprecated yet**: `grep -rn deprecat src/` returns
no matches, because no name on the stable list has needed retiring. The policy exists so
that the first one is handled predictably.

It applies only to the covered surfaces above. Names in the "not covered" column can change
at any time and are owed no deprecation period at all — that is what "not covered" means.

| Question | Answer |
|---|---|
| How long does a deprecated name survive? | **At least two further minor releases, and in practice the rest of the `1.x` line.** A covered name deprecated in `1.4.0` still works in `1.5.0`, `1.6.0` and every later `1.x`. It is removed only in `2.0.0`, and `2.0.0` will not be tagged sooner than two minor releases after the deprecation is announced. Removing a covered name inside `1.x` is not permitted, whatever notice was given. |
| Is a `DeprecationWarning` emitted? | **Yes, and at call time rather than import time** — but note that none of this machinery exists yet. A deprecated function or method **emits** a `DeprecationWarning` through `warnings.warn` and then does what it always did; a deprecated constant or module attribute emits one on attribute access, via a module-level `__getattr__` ([PEP 562](https://peps.python.org/pep-0562/)), and still returns its value. The warning is emitted, never raised: a deprecated name keeps working for the rest of `1.x`, so a caller who ignores the warning is not broken by it. Importing an unrelated part of `tether` will never emit one. The message will name the release that deprecated the name and the replacement to use, or say plainly that there is no replacement. There is no deprecation helper, decorator or `__getattr__` in `src/` today; it will be written with the first deprecation. |
| Where is it announced? | **The GitHub release notes** for the release that introduces the deprecation, at [github.com/bioedca/tether/releases](https://github.com/bioedca/tether/releases). There is no `CHANGELOG.md` in this repository, so do not go looking for one. Note the current state: the repository has nine tags and **no published Releases** — release notes begin with the first tag cut through the signed [release pipeline](release.md). |
| Does the `2.0.0` release note list the removals? | The notes are generated mechanically from Conventional-Commit subjects (`release.yml`), and the generator emits seven fixed groups — Features, Bug fixes, Performance, Documentation, Refactoring, Build & packaging, CI. There is no breaking-changes group, and a `feat!:` subject is folded into **Features**. So a removal appears in the notes, but not under a heading that sets it apart. `feat!:` marks the break itself — the `2.0.0` commit that actually removes a covered name; the [pull-request template](https://github.com/bioedca/tether/blob/main/.github/pull_request_template.md) likewise reserves `!` / `BREAKING CHANGE:` for a deliberate breaking change, and a deprecated name that still works is not one. A deprecation ships as an ordinary `feat:` or `docs:` commit whose subject names the deprecated symbol, so search the notes for the symbol rather than for a `!`. |

Python hides `DeprecationWarning` by default outside `__main__`. To see them in a script or
a batch job, run with `python -W default::DeprecationWarning`; `pytest` shows them by
default.

A deprecated CLI flag follows the same clock: it keeps working, keeps its meaning, and
prints a one-line notice to stderr naming its replacement.

## If a covered surface breaks anyway

That is a bug, not a policy change. Open an issue at
[github.com/bioedca/tether/issues](https://github.com/bioedca/tether/issues) naming the
covered surface, the version that changed it, and the version you upgraded from. If the
break has a security dimension, follow
[SECURITY.md](https://github.com/bioedca/tether/blob/main/SECURITY.md) and report it
privately instead.

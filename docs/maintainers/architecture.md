---
description: "How Tether is built: its packages, its three environments, the project file, the data flow, the invariants, the decision records and the tests."
---

# Architecture overview

**Who this page is for.** A person who will change the code of Tether and wants a map of the
program before they open it. It assumes that you know Python and git. It does not assume that you
know the science. It needs no write access to `bioedca/tether`, because a change starts as a pull
request from a fork.

This page is an explanation. It records no decision of its own. Each statement comes from the code
or from an accepted architecture decision record (ADR), and it names that source. Where this page
and the code disagree, the code is correct and this page has a bug.

## The parts

The code is in `src/tether/`. It has nine packages, and each package owns one responsibility. The
API reference has a page for each package.

| Package | Responsibility | Read first |
| --- | --- | --- |
| [`tether.io`](../api/io.md) | The movie readers and their registry, the HDF5 skeleton of the project file, the filename parser and the legacy Deep-LASI readers. | [ADR-0113](../adr/0113-one-movie-reader-interface-an-adapter-registry-and-a-rule-for-reader-packages.md), [ADR-0005](../adr/0005-m0-schema-freeze.md) |
| [`tether.imaging`](../api/imaging.md) | Native extraction: split, spot detection, registration, colocalization and integration of the traces. | [Extract traces](../how-to/extract-traces.md) |
| [`tether.fret`](../api/fret.md) | Photobleaching detection, the leakage (α) and gamma (γ) factors, and FRET efficiency. | [ADR-0026](../adr/0026-photobleach-detection-and-window-default.md), [ADR-0003](../adr/0003-apparent-e-never-nan.md) |
| [`tether.idealize`](../api/idealize.md) | The driver of the tMAVEN sidecar, the SMD writer, the standalone-tMAVEN hand-off, and dwell analysis. | [ADR-0006](../adr/0006-sidecar-ipc-escalation.md), [Standalone-tMAVEN hand-off](../idealize/standalone-tmaven-handoff.md) |
| [`tether.ml`](../api/ml.md) | The per-condition quality ranker, and the optional deep classifier in `tether.ml.deep`. | [ADR-0034](../adr/0034-gradient-boosting-quality-ranker.md), [ADR-0047](../adr/0047-deep-model-optional-stack-and-dataset.md) |
| [`tether.analysis`](../api/analysis.md) | Histograms, transition-density plots, FRET clouds, cross-correlation, population statistics and the figure export. | [Seven-plot parity gallery](../analysis/parity-gallery.md) |
| [`tether.project`](../api/project.md) | The headless core: the `Project` object, each stage that reads and writes a project file, the batch runner and the exports. | [ADR-0030](../adr/0030-headless-batch-runner-isolation-and-checkpoint.md) |
| [`tether.services`](../api/services.md) | One Qt-free command for each action, which the application, the command line and a recipe file all call. | [ADR-0100](../adr/0100-service-commands-are-the-one-way-to-run-an-action.md) |
| [`tether.gui`](../api/gui.md) | The PySide6 shell: its sections, the trace and plot docks, and the napari movie panel. | [ADR-0116](../adr/0116-the-shell-hosts-its-sections-in-one-stack.md), [ADR-0055](../adr/0055-gui-session-writer-ownership.md) |

Some modules sit at the top of `src/tether/`, outside the packages. `cli.py` and `cli_export.py`
are the `tether` command line ([Command line](../cli.md)). `extract_choices.py` and
`idealize_choices.py` hold the choices that the command line and the application share.

### Which package can import which

Nothing in the code fixes a strict order among `tether.io`, `tether.imaging`, `tether.fret`,
`tether.idealize`, `tether.ml`, `tether.analysis` and `tether.project`. They import one another
where one step needs the output of another. These rules hold, and a test checks most of them:

1. Only `tether.gui` imports Qt, and no other package imports `tether.gui`. The `tether.project`
   docstring states it: the application is a thin layer over the headless core.
   `tests/test_services.py` checks it for `tether.services`.
2. Inside `tether.gui`, a module imports Qt through `pyqtgraph.Qt`. A short list of modules is the
   exception, and `tests/test_gui_qt_imports.py` holds that list.
3. The application runs an action through a command of `tether.services`
   ([ADR-0100](../adr/0100-service-commands-are-the-one-way-to-run-an-action.md)). Older code
   calls the core directly. `tests/test_gui_core_ratchet.py` lists each of those calls, and the
   list can only get shorter.
4. No module of the base environment imports tMAVEN. Only `tether/idealize/_sidecar_runner.py`
   imports it, and that file runs in the sidecar interpreter
   ([ADR-0006](../adr/0006-sidecar-ipc-escalation.md)).
5. The base application never imports PyTorch. Only functions inside `tether.ml.deep` import it,
   and `tests/test_torch_optional_import.py` checks it
   ([ADR-0047](../adr/0047-deep-model-optional-stack-and-dataset.md)).

## The three environments

Tether runs in three conda environments. Each has its own `environment.yml` and its own committed
lock file. They are separate because their dependencies conflict: tMAVEN needs PyQt5 and NumPy
below 2, and the application needs PySide6 and a current NumPy
([ADR-0004](../adr/0004-pin-and-hold-dual-lock-isolation.md)).

| Environment | Lock file | What it carries | How the application uses it |
| --- | --- | --- | --- |
| Application (base) | `conda-lock.yml` | Python, NumPy, Numba, h5py, PySide6, pyqtgraph and napari. | It is the environment that Tether runs in. |
| tMAVEN sidecar | `sidecar/conda-lock.yml` | PyQt5, NumPy below 2, and the subset of the tMAVEN dependencies that the three fitting methods need. | `tether.idealize.driver` starts its interpreter as a subprocess. It writes an SMD file in, reads a model file out, and reads one JSON status line. |
| Deep classifier (optional) | `deep/conda-lock.yml` | The CPU build of PyTorch, with the NumPy, SciPy and h5py that the dataset code needs. The [Deep trace classifier](../ml/deep-classifier.md) page tells you how to install a GPU build. | You run Tether in this environment to train or apply the deep classifier. The base application does not need it. |

The driver finds the sidecar interpreter in three places, in this order. It takes the
`sidecar_python` argument, then the `TETHER_SIDECAR_PYTHON` environment variable, then the
`envs/sidecar` folder of an installed Tether
([`driver.py`](https://github.com/bioedca/tether/blob/main/src/tether/idealize/driver.py)). In a
source checkout, `scripts/setup_sidecar.py` builds that environment.

Each lock is pin-and-hold. A pull request changes a lock on purpose, and never as a side effect.
The required `conda-lock-verify` check fails when a lock does not match its `environment.yml`.

## The project file

A project is one HDF5 file with the `.tether` extension. Inside it, the *store* is the HDF5 layout
of groups and datasets. `tether.io.schema` writes the whole top-level skeleton when it creates the
file, and it stamps a `schema_version`. The [format reference](../reference/tether-format.md)
documents each group.

After milestone M0, the skeleton is additive-only. A change can add data, but it cannot remove, rename or
retype a frozen field ([ADR-0005](../adr/0005-m0-schema-freeze.md)). The required `schema-guard`
check runs `scripts/dump_schema.py --check`. It compares the structure that the code writes with
`schema/schema_frozen.json`. A structural change needs an ADR, a `schema_version` bump and a new
golden file in the same pull request.

A project refers to its movies and does not copy their pixels
([ADR-0001](../adr/0001-provenance-first-data-model.md)). A project is also a superset of the
tMAVEN SMD format. So the round trip between a trace and its movie is a property of the data
([ADR-0002](../adr/0002-smd-superset-round-trip.md)).

## How a movie becomes a result

The diagram shows the path of one movie. Each line names the stage and the package that owns its
work. `tether.project` runs each stage over a project file, and `tether.services` exposes it as a
command.

```text
movie file (TIFF, CZI, ND2, ...)
  │  tether.io        opens the movie through its reader adapter
  ▼
split → detect → register → colocalize → extract
  │  tether.imaging   finds the molecules and integrates their traces
  ▼
.tether project file  (tether.io writes the skeleton, tether.project the data)
  │
  ├─► curate          tether.ml ranks the traces, tether.project records the labels
  ├─► photobleach, α / γ corrections
  │                   tether.fret computes the factors and FRET efficiency
  ├─► idealize        tether.idealize runs tMAVEN in the sidecar
  ├─► analyze         tether.analysis computes the plots and statistics
  └─► export          tether.project writes the tables, tether.analysis the figures
```

The same path in words:

1. `tether.io` opens the movie through the reader adapter for its container.
2. `tether.imaging` splits the two channels and detects the spots. It registers the channels,
   colocalizes the molecules and integrates their traces.
3. `tether.project.extract` writes the traces into a new `.tether` project file, in the skeleton
   that `tether.io.schema` declares.
4. After extraction, the path branches. You can curate, correct, idealize, analyze or export in
   the order that the steps permit.
5. Curation records labels through `tether.project.labels`, and `tether.ml` ranks the traces for
   the curator.
6. `tether.fret` detects the photobleaching steps and computes the α and γ factors and FRET
   efficiency. `tether.project.correct` stamps the method of each molecule.
7. `tether.idealize` fits states in the sidecar. `tether.project.idealize` writes the fit back.
8. `tether.analysis` computes the histograms, the transition-density plots and the other plots.
9. `tether.project.export` writes the tables and the subset project files.
   `tether.analysis.plot_export` writes the figures.

The batch runner in `tether.project.batch` runs three of these stages with no display:
`extract → correct → idealize`
([ADR-0030](../adr/0030-headless-batch-runner-isolation-and-checkpoint.md)). The user page
[How Tether works](../pipeline.md) describes each stage for a person who runs an experiment.

## The invariants

A change must not break these rules. Each rule has a check that fails when the rule breaks.

| Rule | Source | What enforces it |
| --- | --- | --- |
| The HDF5 skeleton is additive-only. | [ADR-0005](../adr/0005-m0-schema-freeze.md) | The `schema-guard` check, and `tests/test_schema.py` |
| The three lock files stay separate, and each one is pin-and-hold. | [ADR-0004](../adr/0004-pin-and-hold-dual-lock-isolation.md), [ADR-0047](../adr/0047-deep-model-optional-stack-and-dataset.md) | The `conda-lock-verify` check, and `tests/test_torch_optional_import.py` |
| A missing α or γ routes the molecule to apparent E. A NaN factor is never applied. | [ADR-0003](../adr/0003-apparent-e-never-nan.md) | `tests/test_project_correct.py` |
| Provenance travels with each result: the application version, the parameters and the source of each value. | [ADR-0001](../adr/0001-provenance-first-data-model.md) | `tests/test_provenance_schema.py` for the provenance file beside each export |
| Each analysis default has one registered value, and the reference page prints it. | [Analysis parameters](../reference/parameters.md) | `tests/test_docs_parameter_defaults.py` |
| Idealization parity with tMAVEN is statistical, against a frozen tolerance. | [ADR-0007](../adr/0007-parity-is-statistical.md) | The `sidecar / parity` check, and `schema/parity_tolerance.json` |
| The application runs an action through a service command. | [ADR-0100](../adr/0100-service-commands-are-the-one-way-to-run-an-action.md) | `tests/test_services.py` and `tests/test_gui_core_ratchet.py` |

Two rules protect the science and have no single test. Do not widen a frozen tolerance to fit an
implementation. Do not commit a reference value that does not come from a real, cited source.
[`AGENTS.md`](https://github.com/bioedca/tether/blob/main/AGENTS.md) states both.

## Where decisions are recorded

The [ADR index](../adr/README.md) lists each architecture decision record in number order. Each
record gives the context, the options and the reason for the choice. A later record can amend or
supersede an earlier one, and the Status column of the index says so. Read the status before you
rely on a record.

Read these records first. Together they explain most of the structure on this page.

1. [ADR-0001](../adr/0001-provenance-first-data-model.md): provenance comes first in the data model.
2. [ADR-0002](../adr/0002-smd-superset-round-trip.md): a project is a superset of the SMD format.
3. [ADR-0003](../adr/0003-apparent-e-never-nan.md): a molecule with a missing factor falls back to
   apparent E.
4. [ADR-0004](../adr/0004-pin-and-hold-dual-lock-isolation.md): the locks are pin-and-hold and
   separate.
5. [ADR-0005](../adr/0005-m0-schema-freeze.md): the HDF5 skeleton is frozen and additive-only.
6. [ADR-0006](../adr/0006-sidecar-ipc-escalation.md): tMAVEN runs headless in the sidecar.
7. [ADR-0007](../adr/0007-parity-is-statistical.md): idealization parity is statistical.
8. [ADR-0030](../adr/0030-headless-batch-runner-isolation-and-checkpoint.md): the headless batch
   runner.
9. [ADR-0047](../adr/0047-deep-model-optional-stack-and-dataset.md): the optional deep stack.
10. [ADR-0100](../adr/0100-service-commands-are-the-one-way-to-run-an-action.md): service
    commands run each action.
11. [ADR-0113](../adr/0113-one-movie-reader-interface-an-adapter-registry-and-a-rule-for-reader-packages.md):
    one movie reader interface and its registry.
12. [ADR-0116](../adr/0116-the-shell-hosts-its-sections-in-one-stack.md): the sections of the
    application window.

## Where the tests are

The tests are in `tests/`. A pytest marker puts a test in a tier, and each tier runs where its
needs are met. The
[contributor guide](https://github.com/bioedca/tether/blob/main/CONTRIBUTING.md#test-tiers-and-markers)
gives the commands and the rules of each marker.

| Marker | What the test needs | Where it runs |
| --- | --- | --- |
| *(none)* | The base environment only. | `ci.yml`, required, on three operating systems |
| `gui` | Qt, headless with `QT_QPA_PLATFORM=offscreen`. | `ci.yml`, required, on three operating systems |
| `sidecar` | A live tMAVEN environment. | `sidecar.yml` |
| `deep` | The deep environment with PyTorch. | `deep.yml` and `deep-gpu.yml`, advisory |
| `large` | The gated large fixtures. | `large-fixtures.yml`, on a schedule |
| `reference_download` | Network access to the reference data sets. | `reference-data.yml`, weekly |
| `workstation` | Private laboratory data. | The maintainer's workstation only |

Two suites guard the numbers from end to end. `tests/test_golden.py` compares results with the
golden files in `tests/golden/` ([ADR-0080](../adr/0080-self-drift-golden-numbers.md)).
The coverage job compares coverage with the floors in `.github/coverage-floors.toml`, and a floor
can only go up ([ADR-0072](../adr/0072-coverage-floors-that-only-go-up.md)).

`tests/test_docs_architecture.py` checks this page. It fails when a package is added to
`src/tether/` or removed from it, and the table in [The parts](#the-parts) does not change with
it.

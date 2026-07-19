# Standalone-tMAVEN hand-off

Tether idealizes traces by driving [tMAVEN](https://github.com/GonzalezBiophysicsLab/tmaven)
(vbFRET / consensus VB-HMM / ebFRET). Because tMAVEN pins `numpy<2` + PyQt5, it runs in
an **isolated sidecar environment** — a separate interpreter that never shares a process
with Tether's own PySide6 / current-numpy GUI (ADR-0004, ADR-0006).

Two ways to reach tMAVEN share that sidecar:

- the **headless driver** (`tether.idealize.run_vbfret`), used by one-click idealize and
  batch runs; and
- the **standalone-GUI hand-off** — Tether exports an
  [SMD](https://github.com/GonzalezBiophysicsLab/tmaven) file the user opens directly in
  the standalone tMAVEN GUI, edits by hand, and re-imports (PRD §7.4).

This page covers the **guided setup** for the sidecar and the **hand-off verification**
that a Tether-exported SMD opens in standalone tMAVEN with its coordinate metadata intact
— the M9 acceptance folded in from the M0.5 deferral (ADR-0010, issue #13).

## Guided sidecar setup

`scripts/setup_sidecar.py` turns a checkout into a working sidecar interpreter in one
command. It encodes the two things that live **outside** the committed
`sidecar/conda-lock.yml` and are easy to get wrong by hand:

1. **tMAVEN itself** — the GPL reference app, driven over IPC and installed from a pinned
   git commit (it is not a conda-lock dependency); and
2. **`setuptools<81`** — tMAVEN imports the legacy `pkg_resources` API at runtime, which
   setuptools removed in v81, so it must be pinned back into the sidecar env.

The script runs three phases — **create** the env from the lock, **install** tMAVEN +
`setuptools<81`, then **probe** liveness (import and instantiate `maven_class`, no fit) —
and prints the line that points Tether at the interpreter.

From a fresh checkout, with a conda front-end on `PATH`
([`micromamba`](https://mamba.readthedocs.io/), `mamba`, or `conda` +
[`conda-lock`](https://conda.github.io/conda-lock/)):

```bash
python scripts/setup_sidecar.py
```

On success it prints, for example:

```text
Sidecar env is ready. Point Tether at it with:
  export TETHER_SIDECAR_PYTHON="/path/to/envs/tether-sidecar/bin/python"
```

Set `TETHER_SIDECAR_PYTHON` to that path (the app and the driver both read it). On
Windows the script prints the PowerShell form (`$env:TETHER_SIDECAR_PYTHON = "..."`).

Useful options:

| Option | Effect |
|---|---|
| `--python PATH` | Use an existing interpreter as the sidecar; skip env creation. |
| `--conda-exe EXE` | Force a specific conda front-end (default: first of micromamba/mamba/conda). |
| `--env-name NAME` | Name of the created env (default `tether-sidecar`). |
| `--lock-file PATH` | conda-lock file to build the env from (default `sidecar/conda-lock.yml`). |
| `--tmaven-spec SPEC` | pip spec for tMAVEN (default `$TMAVEN_SPEC` or the pinned commit). |
| `--with-pytest` | Also install `pytest` (needed to run the live sidecar test suite). |
| `--skip-install` | Assume tMAVEN is already installed; only create the env / probe. |
| `--no-probe` | Skip the liveness probe. |
| `--dry-run` | Print every command without running it. |

Users who install Tether from a **packaged installer** do not need this script — the
installer bundles the sidecar env and wires `TETHER_SIDECAR_PYTHON` automatically (see
[Packaging & installers](../packaging.md)). The guided script is for developers and for
building the sidecar from source.

## Verifying the hand-off

An exported SMD "opens in standalone tMAVEN with coordinate metadata intact" is checked
two ways: a **scripted** assertion that runs in CI and locally, and a **manual** GUI leg
a human performs once per OS.

### Scripted open-check

`tether.idealize.check_smd_opens(path)` launches the sidecar interpreter and loads the
SMD with **tMAVEN's own loader** — `maven.io.load_smdtmaven_hdf5`, the exact code path
behind the standalone GUI's *File → Load SMD* menu — then reports the molecule/frame
counts, the analysis windows tMAVEN read back, and a raw-intensity checksum. If tMAVEN
loads it, the GUI opens it.

Tether's own coordinates (donor/acceptor pixel positions, molecule identities) ride along
in a `tether/` **superset group** that tMAVEN ignores on load and drops on save — the
documented gap (ADR-0002) that the return-leg intensity matcher closes. `check_smd_opens`
confirms the standard SMD the GUI reads is complete; the superset's survival in the same
file is asserted separately with `tether.idealize.read_smd`.

The live assertion is the `@pytest.mark.sidecar` suite `tests/test_handoff_sidecar.py`.
It is deselected from the ordinary CI matrix (which has no sidecar env) and runs in the
[`sidecar`](https://github.com/bioedca/tether/actions/workflows/sidecar.yml) workflow,
which provisions the isolated env **via `scripts/setup_sidecar.py`** — so the guided
setup script is itself exercised on every live run. To run it locally after setting up the
sidecar:

```bash
export TETHER_SIDECAR_PYTHON="/path/to/sidecar/python"
pytest -m sidecar tests/test_handoff_sidecar.py
```

### Manual GUI leg

The scripted check drives tMAVEN's loader but not its windowed UI. Once per operating
system, a human confirms the file opens in the actual GUI:

1. Set up the sidecar with `scripts/setup_sidecar.py` (above) and note the interpreter.
2. Produce a Tether-exported SMD — either from the app's **Hand to tMAVEN** action, or
   with `tether.idealize.write_smd(...)` (the same writer the app uses).
3. Launch the standalone tMAVEN GUI from the sidecar env:
   `TETHER_SIDECAR_PYTHON` → `python -m tmaven` (or the bundled launcher).
4. **File → Load → SMD**, choose the exported `.hdf5`, and confirm the traces load with
   the expected molecule count and per-trace analysis windows.
5. Record the result in the table below.

## Cross-OS result record

The M9 acceptance (ADR-0010) is: the exported SMD opens in standalone tMAVEN and the setup
script runs clean on **≥2 OSes** (Windows + macOS/Linux). The scripted half is verified on
Windows (developer) and Linux (CI); the manual GUI leg is recorded here as it is completed.

| OS | tMAVEN commit | Setup script | Scripted open-check | Manual GUI open | Date | Notes |
|---|---|---|---|---|---|---|
| Linux (CI) | `10f4230` | clean | pass | n/a (headless CI) | ongoing | `sidecar` workflow: setup script + `test_handoff_sidecar.py` on every live run. |
| Windows 11 | `10f4230` | clean | pass | pending | 2026-07-19 | Developer box; setup-script probe + `test_handoff_sidecar.py` (3/3) green. GUI leg pending. |
| macOS / Linux (desktop) | — | — | — | pending | — | Second desktop OS for the manual GUI leg. |

The scripted open-check and the guided setup script are green on two operating systems
(Windows developer + Linux CI). The manual GUI legs are tracked here for a maintainer to
complete on a physical desktop of each OS, which is the human-in-the-loop step ADR-0010
deferred to packaging time.

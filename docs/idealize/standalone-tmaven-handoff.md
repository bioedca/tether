---
description: "Open Tether's traces in the standalone tMAVEN application window, edit the fit by hand and bring it back. It also covers the tMAVEN sidecar setup."
---

# Standalone-tMAVEN hand-off

**Who this page is for.** Anyone who wants to open Tether's traces in the standalone tMAVEN
application window and edit the fit by hand. They then bring the result back. It is also for anyone who
must set up the tMAVEN sidecar. It assumes you have used tMAVEN. The setup section also assumes
you can run a Python script from a source checkout.

Tether [idealizes](../pipeline.md#idealize) traces by driving
[tMAVEN](https://github.com/GonzalezBiophysicsLab/tmaven)
([vbFRET / consensus VB-HMM / ebFRET](../glossary.md#vbfret-ebfret-consensus-vb-hmm)).
The tMAVEN app pins `numpy<2` + PyQt5. So it runs in an **isolated sidecar environment**. That is a
separate interpreter that never shares a process with Tether's own PySide6 / current-numpy GUI.

Two ways to reach tMAVEN share that sidecar:

- The **headless driver** (`tether.idealize.run_vbfret`). The application's project fit, the
  `tether idealize` command and batch runs use it.
- The **standalone-GUI hand-off**. Tether exports an
  [SMD](https://github.com/GonzalezBiophysicsLab/tmaven) file. The user opens it directly in
  the standalone tMAVEN GUI, edits it by hand, and imports it again.

This page covers the **guided setup** for the sidecar. It also covers the **hand-off
verification**. That check confirms that a Tether-exported SMD opens in standalone tMAVEN with
its coordinate metadata intact. ADR-0010 records that manual check. ADR-0004 and ADR-0006 record
why the sidecar is a separate environment.

## Categories and tMAVEN classes

tMAVEN keeps one whole-number class for each trace. Tether keeps a category name. The
hand-off translates between them through the category list of each molecule's condition:

| tMAVEN class | Tether category |
|---|---|
| `0` | no category |
| `1` | the first category in the condition's list (hotkey **1**) |
| `2` | the second category (hotkey **2**), and so on |

Each molecule's category goes out as its class. An SMD you bring back unchanged shows no
class change.

If you change a class in tMAVEN, **Import return leg…** shows the category it names. Nothing
is written until you accept it.

A class that no category has is shown but cannot be accepted, and Tether never guesses a name
for it. Add the category to the list first.

The translation follows the list's current order. Reordering or removing categories while an
SMD is out changes what its classes mean. Check the proposed categories before you accept.
A category that left the list goes out as class `0`. A `0` coming back leaves it alone.

## Guided sidecar setup

`scripts/setup_sidecar.py` turns a checkout into a working sidecar interpreter in one
command. It encodes two things that are easy to get wrong by hand. Both live **outside** the
committed `sidecar/conda-lock.yml`:

1. **tMAVEN itself.** It is the GPL reference app, driven over IPC. The script installs it from
   a pinned git commit. It is not a conda-lock dependency.
2. **The setuptools compatibility wheel.** The tMAVEN app imports the legacy `pkg_resources`
   API at runtime without declaring it. The setuptools project deprecated that API by 80.9.0.
   It still shipped the API through 81.0.0. It **removed the API in 82.0.0**. But `sidecar/conda-lock.yml` resolves
   82.0.1 on every platform, past the removal. Without the pin, an installed app builds its
   sidecar env with no error. It then fails at the *first* idealization.

   The exact version and its SHA-256 live in **`packaging/setuptools-compatibility.txt`**,
   which is the single source. The file itself names its consumers rather than counting them.
   They are `packaging.yml`, `release.yml`, `scripts/setup_sidecar.py` and the local build
   recipe in `packaging/README.md`. A contract test enforces that list, so nothing restates the
   version anywhere else.

   The download is hash-enforced (`pip --require-hashes`). So the tagged
   commit alone determines what shipped. Before this, three OS runners resolved
   `setuptools<81` independently. A rebuild of the same tag could then bundle a different
   build.

   **This is a runtime-only compatibility exception, not a dependency.** It goes into the
   *isolated* sidecar env and never into the base app env. Its only purpose is to resolve the
   undeclared import of an unmaintained upstream. A release older than the ecosystem default
   keeps a deprecated API alive in one interpreter. That is a knowing trade. It is recorded
   rather than hidden, and it is not waived from dependency auditing.

   **Removal trigger:** when tMAVEN no longer imports `pkg_resources`, delete the file and
   every consumer named in it. Nothing else changes. The sidecar lock already resolves a
   current setuptools on its own.

The script runs three phases:

1. **Create** the env from the lock.
2. **Install** the hash-checked setuptools wheel, and then tMAVEN.
3. **Probe** liveness. The probe imports and instantiates `maven_class`, with no fit.

It then prints the line that points Tether at the interpreter. The install is two `pip`
commands rather than one. The reason is that the hash-checking mode of pip is all-or-nothing.
The tMAVEN spec is a git URL with no hash to give.

You need a fresh checkout and a conda front-end on `PATH`. The front-end can be
[`micromamba`](https://mamba.readthedocs.io/), `mamba`, or `conda` +
[`conda-lock`](https://conda.github.io/conda-lock/). Run:

```bash
python scripts/setup_sidecar.py
```

On success it prints, for example:

```text
Sidecar env is ready. Point Tether at it with:
  export TETHER_SIDECAR_PYTHON="/path/to/envs/tether-sidecar/bin/python"
```

Set `TETHER_SIDECAR_PYTHON` to that path. The app and the driver both read it. On Windows, the
script prints the PowerShell form (`$env:TETHER_SIDECAR_PYTHON = "..."`).

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

Users who install Tether from a **packaged installer** do not need this script. The installer
bundles the sidecar env and sets `TETHER_SIDECAR_PYTHON` automatically (see
[Packaging & installers](../packaging.md)). The guided script is for developers and for
building the sidecar from source.

## Verifying the hand-off

Two checks confirm that an exported SMD "opens in standalone tMAVEN with coordinate metadata
intact". A **scripted** assertion runs in CI and locally. A human does a **manual** GUI leg
once per OS.

### Scripted open-check

`tether.idealize.check_smd_opens(path)` launches the sidecar interpreter. It loads the SMD
with **tMAVEN's own loader**, `maven.io.load_smdtmaven_hdf5`. That is the exact code path
behind the *File → Load SMD* menu of the standalone GUI. The check then reports three things:
the molecule/frame counts, the analysis windows that tMAVEN read back, and a raw-intensity
checksum. If tMAVEN loads the SMD, the GUI opens it.

Tether's own coordinates go along in a `tether/` **superset group**. They are the
donor/acceptor pixel positions and the molecule identities. The tMAVEN loader ignores the
group on load and drops it on save. That is the documented gap (ADR-0002) that the return-leg intensity
matcher closes. `check_smd_opens` confirms that the standard SMD the GUI reads is complete. A
separate assertion with `tether.idealize.read_smd` confirms that the superset survives in the
same file.

The live assertion is the `@pytest.mark.sidecar` suite [`tests/test_handoff_sidecar.py`](https://github.com/bioedca/tether/blob/main/tests/test_handoff_sidecar.py).

The ordinary CI matrix has no sidecar env, so it deselects this suite. The suite runs in the
[`sidecar`](https://github.com/bioedca/tether/actions/workflows/sidecar.yml) workflow. That
workflow provisions the isolated env **via `scripts/setup_sidecar.py`**. So every live run also
exercises the guided setup script. To run the suite locally, first set up the sidecar. Then
run:

```bash
export TETHER_SIDECAR_PYTHON="/path/to/sidecar/python"
pytest -m sidecar tests/test_handoff_sidecar.py
```

A second sidecar test is
[`tests/test_tmaven_gui_sidecar.py`](https://github.com/bioedca/tether/blob/main/tests/test_tmaven_gui_sidecar.py).
It builds tMAVEN's main window on the offscreen Qt platform, the way `python -m tmaven` does.
It then runs the first plot draw. The test exists because headless tests never draw.

The sidecar lock once resolved `matplotlib-base` 3.11, which breaks the canvas that tMAVEN draws
on. The main window then crashed at startup, while every other check stayed green (#566). The
lock now bounds `matplotlib-base` below 3.11. This test fails if a GUI-breaking dependency
comes back.

### Manual GUI leg

The scripted checks drive tMAVEN's loader and build its main window offscreen. But nobody
looks at the result. Once per operating system, a human confirms that the file opens in the
actual GUI:

1. Set up the sidecar with `scripts/setup_sidecar.py` (above).
2. Note the interpreter that it prints.
3. Make a Tether-exported SMD. Use the **Hand to tMAVEN** action of the app, or
   `tether.idealize.write_smd(...)`, which is the same writer that the app uses.
4. Launch the standalone tMAVEN GUI from the sidecar env:
   `TETHER_SIDECAR_PYTHON` → `python -m tmaven` (or the bundled launcher).
5. Choose **File → Load → SMD**.
6. Choose the exported `.hdf5`.
7. Confirm that the traces load with the expected molecule count and per-trace analysis
   windows.
8. Record the result in the
   [cross-OS result record](../maintainers/standalone-tmaven-cross-os-record.md), a maintainer
   page that keeps one row per operating system.

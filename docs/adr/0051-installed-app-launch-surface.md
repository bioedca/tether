# 0051 — The installed app's launch surface: a real GUI entry point, prefix shims, and a menu shortcut

- **Status:** accepted
- **Date:** 2026-07-20
- **Deciders:** bioedca
- **PRD anchor:** §7.8 (shell / project open), §4.1 (installers), §9 M9 (packaging & docs)
- **Milestone:** M9

## Context and problem statement

[ADR-0049](0049-m9-packaging-constructor-architecture.md) deliberately makes the installer leave the user's machine alone: `initialize_conda: false`,
`initialize_by_default: false`, `register_python: false` and `menu_packages: []` (globally *and* under
`extra_envs.tether`). That is correct — Tether is a desktop application, not a conda distribution, and
the bootstrap conda in `base` must never initialise someone's shell.

The unintended consequence is that **an installed Tether cannot be started at all**:

1. `pyproject.toml` declared exactly one entry point, `tether = "tether.cli:main"` under
   `[project.scripts]`. The console script is installed into `<prefix>/envs/tether`, which is never
   added to `PATH`, so `tether --version` — the verification step an install guide must give — fails in
   a fresh terminal.
2. There was **no GUI entry point at all**. The only callable that opens a window is
   `tether.gui.shell.launch`, whose own docstring describes it as the *"computer-use live-smoke entry"*:
   it fabricates six synthetic traces and a demo project. Wiring a shortcut to it would ship a demo, not
   the application.
3. `menu_packages: []` means no shortcut is created, so there is no icon either.

Every user-facing M9 deliverable is blocked on this: the install guide, the CLI reference, the README
quickstart and the GUI tour all describe invocations that do not work for someone who ran the installer.

## Decision drivers

- A scientist who double-clicks a signed installer must end up with something clickable.
- The documented verification step (`tether --version`) must actually succeed.
- [ADR-0049](0049-m9-packaging-constructor-architecture.md)'s isolation invariants must survive: no re-solve, no merging the two envs, no shell init.
- A launcher must never open the synthetic-data smoke helper.
- Whatever is written must be recoverable if it goes wrong on a user's machine.

## Considered options

1. **Document absolute per-OS paths and change nothing.** Zero install-time risk, but the README
   quickstart, install verification and every CLI example stay false for installer users, and the
   product ships with no icon.
2. **`menuinst` shortcut via `menu_packages`.** The constructor-native route — but `menuinst` attaches
   shortcuts to a **conda package**, and `tether` is an offline-pip-installed wheel ([ADR-0049](0049-m9-packaging-constructor-architecture.md)), so it
   can own no menuinst entry. Repackaging the wheel as a conda package to gain a shortcut would mean a
   new build/publish path for every release.
3. **Have the installer edit `PATH`.** Makes `tether` work in a fresh shell with no user action — but on
   Windows `setx` silently **truncates** `PATH` beyond 1024 characters, and on Unix it means rewriting a
   user's shell rc. Both damage state the installer does not own.
4. **A real GUI entry point + prefix-local shims + a directly-created shortcut** (chosen).

## Decision outcome

**Option 4.** Three parts:

- **A real GUI entry point.** `tether.gui.app:main` builds `TetherShell` against an actual store: the
  `.tether` named on the command line, or none, in which case the shell opens empty and the curator uses
  the existing `&File -> &Open project…`. It is registered as `tether-gui` under **`[project.gui-scripts]`**
  — not `scripts` — so Windows produces a console-less launcher and a shortcut does not flash a terminal.
  `create_shell` is split from `main` so the startup path is testable without entering the Qt event loop.
  `python -m tether.gui` is the equivalent module spelling.
- **Prefix-local shims.** `post_install` writes `<prefix>/bin/tether` and `<prefix>/bin/tether-gui`
  (`.bat` on Windows) forwarding into `envs/tether`. One stable, documented directory means the install
  guide can name a **single** path to add to `PATH`.
- **Direct menu integration.** A Start Menu `.lnk` on Windows (created with `WScript.Shell`, since
  `menuinst` is unavailable per option 2) and a `~/.local/share/applications/tether.desktop` entry on
  Linux. macOS `.pkg` installs get the shims only; a proper `.app` bundle is separate work.

A fourth change falls out of the first three and is not optional. [ADR-0049](0049-m9-packaging-constructor-architecture.md)
wires `TETHER_SIDECAR_PYTHON` through a conda `activate.d` hook, which runs **only on
activation** — so every launch path added here (shortcut, shim, `.desktop`) would start an app
whose idealization is broken, on an otherwise correct install. `resolve_sidecar_python` therefore
gains a third and final step after the argument and the environment variable: the installer's
sibling `envs/sidecar`, derived from `sys.prefix`. This is the *"prefix-relative app-side default"*
[ADR-0049](0049-m9-packaging-constructor-architecture.md) already named as the more robust
follow-up. It resolves relative to the running interpreter rather than hard-coding a path, and a
development checkout — where no sibling exists — falls through to the same actionable error as before.

**The installer still does not edit `PATH`.** Adding `<prefix>/bin` stays a documented one-line step.
This is the deliberate trade in option 3: a user who skips the step has a working icon and a documented
absolute path, whereas a truncated `PATH` is not recoverable by documentation.

### Consequences

- **Good.** The application is launchable from a menu on Windows and Linux; `tether --version` works
  once the documented directory is on `PATH`; the install guide, CLI reference and README quickstart
  become writable against something real; no launch path reaches the synthetic-data helper.
- **Good.** No change to either conda lock, so pin-and-hold and the two-stack isolation are untouched.
  `menu_packages: []` stays empty, as does the no-shell-init posture.
- **Bad / accepted.** `tether` in a fresh shell needs one documented user action. macOS has no icon
  yet. Both shell/batch launcher paths execute only on a real install, so they are verified by the
  packaging install-smoke and by a maintainer running the actual installers — not by the unit matrix.
- **Bad / accepted.** The Windows shortcut is created imperatively rather than declaratively, so it is
  not removed by an uninstall that only unlinks conda packages.

## More information

- Supersedes the "a GUI menu shortcut is a deferred follow-up" note in [ADR-0049](0049-m9-packaging-constructor-architecture.md); the stale comments
  in `packaging/construct.yaml` are corrected in the same change.
- `tests/test_gui_app.py` asserts the entry point is declared under `gui-scripts`, that it is **not**
  `tether.gui.shell:launch`, that a missing project path leaves a usable empty shell, and that
  `--version` answers without importing PySide6.

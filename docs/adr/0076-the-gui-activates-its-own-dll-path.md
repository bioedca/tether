<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0076 — The GUI puts its env's DLL folders first on PATH, so the Windows shortcut needs no console

- **Status:** accepted
- **Date:** 2026-11-12
- **Deciders:** bioedca
- **PRD anchor:** §12.7 (installers), §7.8 (the desktop entry point)
- **Milestone:** M9

## Context and problem statement

On Windows, MKL (the env's BLAS) loads its dependent DLLs through the ordinary Windows search
order, and that order reads `PATH`. On a PC with other scientific software, an incompatible
copy earlier on `PATH` won, and the first BLAS call crashed with `0xC06D007F` (issue #553).
The fix in PR #557 copied what `conda activate` does: the installer's `bin\tether.bat` and
`bin\tether-gui.bat` put the env's folders at the front of `PATH` for their own process. A
shortcut cannot set an environment variable, so the Start Menu entry ran `tether-gui.bat`,
through `cmd.exe` since issue #578. The result was a console window that flashed before the
GUI opened, on every start (issue #579). How does the GUI start with the env's folders first on
`PATH` and no console window?

## Decision drivers

- The #553 crash must not come back on any launch route.
- No console window on the Start Menu route.
- Start must keep listing the shortcut (issue #578).
- The installer still never edits the user's persistent `PATH` (ADR-0051).
- Nothing new to install, sign or keep in step across releases.

## Considered options

1. **A hidden-window wrapper.** The shortcut runs a script host (for example `wscript.exe`
   with a VBScript) that sets `PATH` and starts `tether-gui.exe` with no window. It works
   today, but VBScript is deprecated: since Windows 11 24H2 it is an optional feature, it is
   to be off by default from about 2027, and it is then to be removed
   ([Microsoft, "VBScript deprecation: Timelines and next steps"](https://techcommunity.microsoft.com/blog/Windows-ITPro-blog/vbscript-deprecation-timelines-and-next-steps/4148301),
   read 2026-11-12). It also adds a third launcher file to keep in step with the other two.
2. **PowerShell with `-WindowStyle Hidden`.** PowerShell is a console program, so its console
   still appears for a moment before it hides itself. This does not remove the flash.
3. **The GUI takes the activation step itself.** `tether.gui.app.main` puts the env's folders
   first on its own `PATH` before PySide6 or NumPy loads, and the shortcut targets the
   console-less `tether-gui.exe` directly.

## Decision outcome

**Option 3.** `tether.gui.env_path.put_env_folders_first` runs first in `tether.gui.app.main`.
On Windows it puts the folders that exist under `sys.prefix` at the front of `PATH`, in
`conda activate`'s order: the prefix, `Library\mingw-w64\bin`, `Library\usr\bin`,
`Library\bin`, `Scripts`, `bin`. An entry already on `PATH` moves to the front and is not
repeated, so a second call (or a launcher that already took the step) changes nothing. On
Linux and macOS it does nothing: there the env's libraries find each other through their run
paths. The change applies to the GUI process and the processes it starts. The persistent
`PATH` is not edited.

The Start Menu shortcut targets `envs\tether\Scripts\tether-gui.exe` with no arguments and
`WindowStyle` 1 (a normal window). Style 7, which minimized the `.bat`'s console, would now
minimize the splash: Windows applies the shortcut's style to the first window the process
shows. The icon (issue #673) and the AppUserModelID are unchanged.

`bin\tether-gui.bat` stays for terminal use, with the same activation `PATH`; from a terminal
it opens no extra window. `bin\tether.bat` is unchanged: the CLI is a console program, so a
console is expected there.

### Consequences

- **Good.** No console window on the Start Menu route. One activation list in Python code that
  a unit test keeps equal to the list `post_install.bat` writes. Nothing new to install.
- **Good.** Every route into the GUI takes the step, including `python -m tether.gui` and a
  double-click on `tether-gui.exe`, because the step is in `main`.
- **Bad / accepted.** The step works only if nothing loads MKL before `main` runs. A test
  asserts that importing `tether.gui.app` loads no NumPy, SciPy or PySide6.
- **Bad / accepted.** The CI runner has no competing MKL, so it cannot reproduce the #553
  crash. The packaging smoke proves the shortcut's target and style and that the step puts the
  prefix first in the installed env; the crash itself is checked by a maintainer on a real
  install (issue #579).

## More information

- Changes the Windows shortcut part of the launch surface in
  [ADR-0051](0051-installed-app-launch-surface.md); its other decisions are unchanged.
- Tests: `tests/test_gui_env_path.py`, `tests/test_gui_app.py`
  (`test_main_puts_the_env_folders_on_path_before_anything_loads`) and
  `tests/test_windows_launchers.py`.

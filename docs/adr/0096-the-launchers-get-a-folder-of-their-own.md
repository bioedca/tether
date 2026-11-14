<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0096 — The two launchers get a folder of their own: `<prefix>/launchers`

- **Status:** accepted; amends [ADR-0051](0051-installed-app-launch-surface.md) on the folder that holds the launchers
- **Date:** 2026-11-14
- **Deciders:** bioedca
- **PRD anchor:** §4.1 (installers), §9 M9 (packaging & docs)
- **Milestone:** M9

## Context and problem statement

[ADR-0051](0051-installed-app-launch-surface.md) has `post_install` write the `tether` and
`tether-gui` launchers into `<prefix>/bin`, so that the install guide can name one folder to add
to `PATH`. On Linux and macOS that folder is not Tether's. It is the `bin` of constructor's
**base** env, the python + conda bootstrap of
[ADR-0049](0049-m9-packaging-constructor-architecture.md), and on an rc1 Linux install it held
121 entries: `python`, `python3`, `pip`, `conda`, `activate`, `openssl` and the rest (issue
#492). A user who put the folder first on `PATH`, the usual way to write that line, got the
bootstrap's `python`, `pip` and `conda` in place of their own. A user who put it last and had
none of their own got the bootstrap's copies under those names. The install guide told users to
append the folder and said why; that was a mitigation, not a fix.

Which folder should hold the launchers, so that adding it to `PATH`, in either position, changes
nothing else that a shell finds?

## Decision drivers

- The folder that the install guide names holds `tether` and `tether-gui` (`tether.bat` and
  `tether-gui.bat` on Windows) and nothing else.
- One folder name on all three systems, so the guide, the README and the command-line page give
  one instruction.
- Nothing else that ADR-0049 and ADR-0051 decided changes: no `PATH` edit by the installer, no
  shell initialisation, no change to either env or to the base env's contents.
- The menu entries keep working. The Linux `.desktop` entry and the Windows Start Menu shortcut
  target `envs/tether` directly, not the launchers.

## Considered options

1. **Keep `<prefix>/bin` and tell users to append it.** No installer change. The bootstrap's
   programs still reach the shell of every user who follows the guide, and a user with no Python
   of their own gets the bootstrap's.
2. **Keep `<prefix>/bin` and delete everything else in it.** The bootstrap env is constructor's
   and runs the install; removing its programs breaks constructor's own update and uninstall
   paths. The base env's contents are out of scope for issue #492.
3. **`<prefix>/app/bin`.** A folder of its own, but two levels deep for one purpose, and `app`
   reads as if the application lived there; it lives in `envs/tether`.
4. **`<prefix>/launchers` on all three systems** (chosen).

## Decision outcome

**Option 4.** `post_install.sh` and `post_install.bat` write the two launchers into
`<prefix>/launchers`, a folder that the installer creates and in which nothing else is written.
The name says what the folder holds, it is one level below the install folder, and it is the same
on Windows, macOS and Linux.

Windows moves too, although its `bin` is probably not affected: there the base env's programs are
in the prefix root, `Scripts\` and `Library\bin\`. That was inferred and never observed, and one
folder name for all three systems keeps the documentation to one instruction.

`packaging/scripts/install_smoke.sh` lists the folder after the install and fails unless it holds
exactly the two launchers, on all three systems, so the check runs in both `packaging.yml` and
`release.yml`. Its `PATH="<folder>:$PATH" tether --version` and `tether-gui --version` checks, and
the Windows `tether.bat` checks, run through the new folder.

### Consequences

- **Good.** Adding the launcher folder to `PATH`, first or last, adds two names and nothing else.
  Users who already have Python or conda keep them; users who do not get no stray copies.
- **Good.** No env, lock or menu entry changes. The `.desktop` entry, the Start Menu shortcut and
  the macOS application bundle that issue #663 adds all start
  `envs/tether` directly.
- **Bad / accepted.** A user of rc3 or earlier who added `<prefix>/bin` to `PATH` must change that
  line to `<prefix>/launchers` after installing the next release into a new folder. The install
  guide names both folders until the next release is the one it describes.
- **Bad / accepted.** An install over an existing rc3 folder can keep rc3's two launchers in
  `<prefix>/bin`. They forward to the same `envs/tether` and stay harmless; the guide's
  uninstall step deletes the whole folder.

## More information

- Issue #492; found while the install guide (#167) was drafted.
- `tests/test_install_launchers.py` runs `post_install.sh` against a stub prefix and checks that
  the launcher folder holds exactly the two launchers, and that both post-install scripts and
  the smoke name `launchers` and not `bin`.

<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0141 — The update helper moves the install aside, installs unattended, and puts it back on failure

- **Status:** accepted
- **Date:** 2026-11-24
- **Deciders:** bioedca (issue #331 approved 2026-11-13 in backlog triage; decision card in the
  issue's thread on 2026-11-24, "Unattended run, then restart")
- **PRD anchor:** §7.12 (FR-UPDATE), §4.1 (application shell), §11.2 (tunables)
- **Milestone:** M13 - In-app updater
- **Issue:** [#331](https://github.com/bioedca/tether/issues/331)

## Context and problem statement

[ADR-0060](0060-in-app-update-mechanism-and-integrity-model.md) decision 1 hands a verified
installer to the OS installer, and decision 6 leaves the per-OS mechanics to this issue with four
constraints: on Windows the hand-off must exit before the installer touches the install folder; on
macOS there is no application bundle to start again from (since ADR-0091 there is a `Tether.app`,
but it is a launcher only); on Linux there is the `.desktop` entry; and on all three the ADR-0051
launch surface must survive. The issue adds two more: a failed or cancelled installer must leave
the existing install working, and the file handed to the installer must be the file that was
verified.

[ADR-0095](0095-application-settings-live-outside-the-install-folder.md) measured what the
installers do when they are run over an existing install, on the installers CI builds
(`packaging.yml` run 43555544588, 2026-11-14):

| Installer | Over an existing install folder |
| --- | --- |
| Windows NSIS `.exe /S` | exit **2**, nothing changed |
| macOS `.pkg` (`installer -pkg`) | exit **1**, *"An error occurred while running scripts from the package"*, nothing changed |
| Linux `.sh -b -p` | exit **1**, `ERROR: File or directory already exists`; with `-u` it writes over the folder |

The Windows refusal is constructor's own NSIS template: `OnDirectoryLeave` aborts on any
non-empty `$INSTDIR` (*"Directory '$INSTDIR' is not empty, please choose a different location"*;
`constructor/nsis/main.nsi.tmpl` at conda/constructor `5214673`, read 2026-11-24; the build env
takes `constructor>=3.16` unpinned, `packaging.yml`). So "launch the new installer over the
running copy's folder" does not work on two systems of three, and on the third it writes over the
only working copy with no way back if it stops half way.

How does an installed Tether replace itself so that a failure at any step leaves the previous copy
working and every shortcut still starting it?

## Decision drivers

- An interrupted update must never leave a lab machine without a working Tether (issue #331,
  acceptance criterion 4).
- The launch surface is checked after the update, not assumed (criterion 2): the `launchers`
  folder, the Start Menu shortcuts, the Linux `.desktop` entry, the macOS `Tether.app`.
- The verified-to-executed window is closed, or what is left of it is stated (criterion 3;
  ADR-0060's threat model gives this row to #331).
- Nothing runs without consent and a successful verification (criterion 5).
- No new dependency, nothing new in either lock, and no code of the old install may be needed
  while its folder is being replaced.

## Considered options

1. **Run the installer over the existing folder.** Refused by two installers of three (measured
   above); `-u` on Linux has no restore.
2. **Install the new version into a second, versioned folder and repoint the shortcuts.** The old
   copy stays as a fallback. But the macOS package installs into one fixed folder, the
   `launchers` folder a user put on `PATH` would keep starting the old version, and each update
   leaves an 800 MB folder behind for someone to delete.
3. **Show the installer's own windows** and let the user drive it. The user can then choose
   another folder or another installation type, which neither the launch-surface check nor the
   restore can follow; Linux has no installer window at all.
4. **A helper moves the folder aside, runs the installer unattended into the same folder, has the
   new copy check itself, and restores the old copy on any failure** (chosen).

## Decision outcome

**Option 4.** `tether.gui.update_handoff.hand_off` accepts only a `VerifiedInstaller` and only
while the update check is *allowed*. It copies the verified file into a private folder
(`tether-update-*` in the user's temporary folder, mode `0700` on macOS and Linux), hashing the
bytes as it copies them, and refuses unless they hash to the verified SHA-256. It writes the helper
script of the platform beside the copy and starts it detached: `update_handoff.sh` with
`/bin/sh`, or `update_handoff.ps1` with Windows PowerShell by its absolute path and
`-ExecutionPolicy Bypass` for that one process. When the helper reports that it runs, and only
then, Tether quits. The helper:

0. takes the lock of the install, `<prefix>.tether-update-lock` (a folder made with `mkdir` on
   macOS and Linux, a file opened with `CreateNew` on Windows: one step that fails when it
   exists), and holds it until it ends; a second helper for the same install stops before it
   reports that it runs, so its Tether stays open and nothing changes;
1. waits for Tether's process to end (at most `EXIT_WAIT_TIMEOUT_S`, else it stops with nothing
   changed) — the Windows ordering, *launch, then exit*, used on every platform;
2. hashes its copy again and stops if it is not the verified file;
3. renames `<prefix>` to `<prefix>.tether-update-backup` (instant, no extra disk space; it stops
   with nothing changed if that name is taken or if Windows refuses because another program still
   has a file in the folder open);
4. runs the installer unattended into `<prefix>`: `sh <installer> -b -p <prefix>`,
   `/usr/sbin/installer -pkg <installer> -target CurrentUserHomeDirectory`, or
   `<installer> /InstallationType=JustMe /S /D=<prefix>`; the installer runs in a process group
   of its own and is stopped, with every process it started, after `INSTALL_TIMEOUT_S`;
5. runs `<prefix>/envs/tether/…/python -m tether.gui.update_handoff check`, so the **new** copy
   checks the launch surface it wrote: each launcher forwards into `<prefix>/envs/tether` and
   reports the new version, the GUI entry point reports it too (macOS and Linux), the two Start
   Menu shortcuts target this install (Windows), the `.desktop` entry starts this install (Linux),
   and `Tether.app` passes `macos_app.check_bundle` (macOS);
6. on success deletes the backup; on any failure deletes the partial install (or renames it to
   `<prefix>.tether-update-failed` if it cannot) and renames the backup back;
7. writes one line, `updated|restored|unchanged|failed VERSION DETAIL`, to `update-result.txt`
   beside the settings file (ADR-0095), and starts Tether again.

Hand-off is offered only where the helper can do all of this without new rights: a *Just Me*
install on Windows (constructor's `.nonadmin` marker), the `.pkg` install into `~/Tether` on macOS,
and on every system an install whose parent folder the user can write. Anywhere else, and in a
development checkout, `hand_off` answers *unavailable* and changes nothing.

The four timeouts are PRD §11.2 tunables: `HELPER_START_TIMEOUT_S` 15 s, `EXIT_WAIT_TIMEOUT_S`
120 s, `INSTALL_TIMEOUT_S` 3600 s, `CHECK_TIMEOUT_S` 120 s.

**"Never silent" (ADR-0060 decision 1, PRD §7.12).** The installer runs without windows of its
own, which the maintainer chose on 2026-11-24 for the reason given in option 3. That is not a
silent update: the hand-off starts only from the user's explicit choice to install, Tether says
before it closes that it will install and start again, and the result line is there for the next
start to show.

### The verified-to-executed window, stated

The verifier proves a property of bytes at one instant (ADR-0060, threat model). What reaches the
installer is the private copy whose bytes were hashed while they were copied and hashed again by
the helper immediately before the installer starts. Left open: the time between the helper's last
hash and the installer opening the file, inside a folder only this user can write. A process
running as this user can already change the installed program in place, which ADR-0060's last row
puts out of scope; this residual is the same capability, not a new one.

### Consequences

- **Good.** No path through the helper leaves the user without a working copy short of the helper
  itself dying between steps 3 and 6. Every refusal before step 3 changes nothing at all.
- **Good.** The launch surface is checked by the code that wrote it, in the copy being installed;
  the checks run in the unit matrix on fake installs and, in `packaging/scripts/upgrade_smoke.sh`,
  on the real installers of all three systems: one update that must land, and one whose installer
  is stopped after 5 s and must be put back.
- **Bad / accepted.** A power cut or a killed helper between steps 3 and 6 leaves
  `<prefix>.tether-update-backup` and no `<prefix>`. Renaming it back restores the previous
  install; the user documentation for updating (#602) must say so. A killed helper also leaves
  its lock, and Tether then says an update is already running and names the lock to remove.
- **Bad / accepted.** After a restore, what the new installer's `post_install` wrote outside the
  folder stays: the Start Menu shortcuts, the `.desktop` entry, the file type, and `Tether.app`
  point at the same folder and so start the restored copy, but the Windows *Installed apps* entry
  and the `Tether.app` version string name the new version until the next install.
- **Bad / accepted.** A site whose Group Policy forbids `-ExecutionPolicy Bypass` cannot run the
  Windows helper. Tether then sees no report within `HELPER_START_TIMEOUT_S`, stays open, and says
  to install by hand.
- **Bad / accepted.** Nothing calls `hand_off` yet: the download and the install button are still
  to come. Until then this code runs only in tests and in the upgrade smoke.

## More information

- [ADR-0060](0060-in-app-update-mechanism-and-integrity-model.md) — decisions 1 and 6, and the
  threat model row this record closes.
- [ADR-0051](0051-installed-app-launch-surface.md),
  [ADR-0094](0094-the-launchers-get-a-folder-of-their-own.md),
  [ADR-0091](0091-macos-launcher-bundle.md) — the launch surface checked.
- [ADR-0095](0095-application-settings-live-outside-the-install-folder.md) — the over-install
  measurements this design starts from, and the settings folder the result file lives in.

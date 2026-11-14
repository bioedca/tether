<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0095 — Application settings live outside the install folder

- **Status:** accepted (merging the pull request that lands this record is the maintainer's
  sign-off)
- **Date:** 2026-11-14
- **Deciders:** bioedca
- **PRD anchor:** §7.12 FR-UPDATE (consent, and the requirement that the answer survives an
  update); §4.1 (application shell)
- **Milestone:** M13 - In-app updater
- **Issue:** [#330](https://github.com/bioedca/tether/issues/330)

## Context and problem statement

[ADR-0060](0060-in-app-update-mechanism-and-integrity-model.md) decision 3 requires a first-run
consent answer for the update check that is remembered and **survives an update**: an answer
that resets on upgrade would re-prompt forever and would silently re-enable network checking for
a user who had declined. ADR-0060 left open *where* the answer is stored, because Tether had no
application-level settings store and the per-OS re-install behaviour that decides the location
had not been measured. Where does the answer live, what reads it and when, and how does a site
administrator turn the mechanism off for a whole machine?

## Decision drivers

- The answer must be readable at startup, before any project is open and before any network
  call is possible, with no measurable startup cost.
- What a `constructor` installer does to an existing prefix is installer behaviour Tether does
  not control, and it differs per OS (measured below).
- Every failure must read as *off*. A damaged file must never read as consent.
- ADR-0060 §6 requires a documented, machine-wide way for an administrator to disable the
  mechanism, at a precedence level above the user's own answer.
- It must not grow into a preferences framework, a second home for analysis parameters (those
  belong in the project store, PRD §5), or something the batch runner depends on.

## Considered options

- **A. A file in the install prefix.** Simple, and found next to the app it configures.
- **B. Qt's `QSettings` / `QStandardPaths`.** Already used for the rebindable keymap.
- **C. A small JSON file in the user's own per-user folder, read with the standard library.**

## Decision outcome

Chosen option: **C**, because it is the only one whose survival does not depend on what an
installer does to its prefix, and it can be read before Qt is imported.

**The per-user file.** `tether.gui.app_settings` stores the answer at:

| OS | Settings file |
|---|---|
| Windows | `%LOCALAPPDATA%\MondragonLab\Tether\settings.json` |
| macOS | `~/Library/Application Support/MondragonLab/Tether/settings.json` |
| Linux | `$XDG_CONFIG_HOME/tether/settings.json`, default `~/.config/tether/settings.json` |

`MondragonLab` and `Tether` are the organization and application names the GUI already gives
Qt, and they make the AppUserModelID `MondragonLab.Tether`. On Windows and macOS the
organization level also keeps the folder apart from a `Tether` install folder a user might
choose under the same parent (`%LOCALAPPDATA%\Tether` is a plausible install location; a store
at `%LOCALAPPDATA%\Tether\settings.json` would then sit inside the prefix, which is option A by
accident). Linux follows the XDG Base Directory specification, which also says a relative
`XDG_CONFIG_HOME` is ignored.

**Three precedence levels**, highest present one deciding:

1. *Unset* — no file, or anything that is not a file this version wrote (empty, torn, oversized,
   not UTF-8, not a JSON object, another `schema`, an unknown value). No network request; the
   prompt is pending.
2. *The user's answer* — `allowed` or `declined`.
3. *The machine-wide administrator override* — the presence of a file named
   `disable-update-check` in `%PROGRAMDATA%\MondragonLab\Tether\`,
   `/Library/Application Support/MondragonLab/Tether/` or `/etc/tether/`. Its content is not
   read. If the file cannot be examined (anything but a clear *no such file*), it counts as
   present. It can only disable; nothing lets an administrator turn checking on for a user who
   has not agreed, because that would bypass the consent decision 3 exists for.

**What it stores.** Exactly `{"schema": 1, "update_check": "allowed" | "declined"}`. No time,
no identifier, no user name, no path, no project data. `record_update_consent` is the only
writer, and it writes atomically (a temporary file in the same folder, `fsync`, `os.replace`),
so a crash leaves the old answer or the new one, never a torn file — and a torn file would read
as unset anyway.

**What reads it, and when.** `update_check_state()` is one small file read and one `stat`, with
no Qt, NumPy or HDF5 import (a test runs it in a fresh interpreter and checks `sys.modules`). It
never creates a file or a folder. The consumer is the update notice of
[#248](https://github.com/bioedca/tether/issues/248), which calls it at startup; until #248 lands
nothing in the application reads it and nothing asks the question. No module outside
`tether.gui` imports it, and a test enforces that, so the batch runner cannot come to depend
on it.

**Why not B.** `QStandardPaths.AppConfigLocation` is resolved from the organization and
application names on the `QCoreApplication`, so the same call answers differently at different
moments of startup. Measured with the base lock's PySide6 6.11.1 on Linux (2026-11-14): before a
`QCoreApplication` exists it returns `~/.config`; with one and no names, `~/.config/PySideApp`;
only after the GUI sets its names, `~/.config/MondragonLab/Tether`. A read made before the
application object — which is exactly when the consent must be read — would look in the wrong
folder and report *unset*. `QSettings` in its native format is the registry on Windows and a
`cfprefsd`-managed property list on macOS (Qt 6 `QSettings` documentation, *Locations Where
Application Settings Are Stored*), which a site administrator and the upgrade smoke would each
have to handle per OS. And both import Qt, which the startup read must not need. The keymap's use
of `AppConfigLocation` is unaffected by this record: it is read after the names are set.

### Measured per-OS re-install behaviour

`packaging/scripts/upgrade_smoke.sh` runs in `packaging.yml` after `install_smoke.sh`, on the
installer that job just built. It installs, writes `declined` with the **installed** app's own
module, puts a marker file in the install folder and in the app env, and then measures the two
upgrade paths a user has: running the installer again over the existing install
(*over-install*) and removing the install as `docs/install.md` says and installing again
(*remove-then-install*). After each it asserts that the settings file is byte for byte the one
the app wrote and that the installed app reads `declined`. A fresh install must first read
`pending` with no settings file present: the default is off on a real install, not only in a
unit test.

Measured on GitHub-hosted runners by `packaging.yml` run
[43555544588](https://github.com/bioedca/tether/actions/runs/43555544588) on `86a91ee`
(2026-11-14), each leg on the installer it had just built:

| Platform | Over-install: installer exit | File in the install folder | File in the app env | Remove (as `docs/install.md` says) | Answer after remove-then-install |
|---|---|---|---|---|---|
| Linux `linux-64` (`.sh`) | plain `-b -p`: **1** (`ERROR: File or directory already exists`); with `-u`: **0** | kept | kept | `rm -rf`: folder gone | `declined` |
| Windows `win-64` (NSIS `.exe /S`) | **2**, nothing changed | kept | kept | uninstaller exit 0; `Uninstall-Tether.exe` left in the folder | `declined` |
| macOS Intel `osx-64` (`.pkg`) | **1** (*"An error occurred while running scripts from the package"*), nothing changed | kept | kept | `rm -rf ~/Tether`: folder gone | `declined` |
| macOS Apple silicon `osx-arm64` (`.pkg`) | **1** (the same script error), nothing changed | kept | kept | `rm -rf ~/Tether`: folder gone | `declined` |

On every leg a fresh install read `pending` with no settings file, and after every path the
settings file was byte for byte the one the app wrote. After the Linux `-u` over-install the
installed app also read `declined`.

What this measured, stated plainly: **no installer offers an over-install except Linux's with
`-u`**. The Windows and macOS installers refuse an existing install folder and leave it as it
was; the only upgrade path there is remove-then-install, which deletes the install folder.
Where an over-install does run, it keeps files it did not lay down. So a store inside the prefix
would have been lost on two of three systems on the one upgrade path they have, and kept on the
third — exactly the *appears to work until the one upgrade path that clears it* that ADR-0060
warned of. The store outside the prefix survives all of them.

This also bears on ADR-0060 decision 1 (*launch the OS installer and exit*): on Windows and
macOS, launching the new installer over the running copy's folder fails as measured here. That
is the hand-off's problem ([#331](https://github.com/bioedca/tether/issues/331)), not this
record's, and it is recorded here so that issue, and the manual update procedure of
[#602](https://github.com/bioedca/tether/issues/602), start from a measurement.

### Consequences

- Good: the answer survives every upgrade path the installers offer, on every platform, and it
  would survive one that deletes the prefix outright, because it is not there.
- Good: a damaged or foreign file, an unreadable override, or a future format all read as off.
- Bad / trade-off: an uninstall leaves the settings file behind. That is the point — a
  remove-then-install keeps the answer — and `docs/install.md` says how to delete it.
- Bad / trade-off: the override binds the application, not a user. Each user owns their own
  install folder and could edit the code in it; the override is a site setting, not a lock, and
  the user page says so.
- Bad / trade-off: the upgrade smoke installs the *same* build twice, not an older release and
  then a newer one. The installer mechanics it measures are the same for any two versions, and
  the store's location does not depend on the version; a cross-version run would need a
  published release with this module, which does not exist yet.
- Follow-up: the smoke runs in the advisory `packaging.yml` only. Three more installs per
  platform would lengthen the release pipeline's critical path, and the release builds the same
  recipe. Moving it into `release.yml` is a one-line change if a release ever needs it gated.
- Follow-up: [#248](https://github.com/bioedca/tether/issues/248) reads the state at startup
  and asks the question; `docs/privacy.md` changes in that pull request (ADR-0060 decision 5).

## More information

- Implementation: `src/tether/gui/app_settings.py`; tests: `tests/test_app_settings.py`;
  installer leg: `packaging/scripts/upgrade_smoke.sh` and its step in
  `.github/workflows/packaging.yml`.
- User page: `docs/install.md`, *Settings Tether keeps outside the install folder*, with the
  administrator override.
- XDG Base Directory Specification, version 0.8, `$XDG_CONFIG_HOME`:
  <https://specifications.freedesktop.org/basedir-spec/0.8/>.

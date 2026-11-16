<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0117 — The installer registers the .tether file type on each system

- **Status:** accepted
- **Date:** 2026-11-16
- **Deciders:** bioedca
- **PRD anchor:** §4.1 NFR-PKG, goal G6
- **Milestone:** M18
- **Issue:** [#664](https://github.com/bioedca/tether/issues/664)

## Context and problem statement

A double-click on a `.tether` project did nothing useful on any system, because no system knew
the file type. `tether-gui [PROJECT.tether]` already opens a project named on its command line,
so only the registration was missing. The maintainer decided on 2026-11-10 (usability audit
question D15, option A) that a double-click opens the project. Each system registers a file type
in a different place, and macOS does not give the file to the program as an argument at all.

## Decision drivers

- A double-click starts Tether with that project open on Windows, Linux and macOS, also for a
  path with spaces or non-ASCII letters.
- A project opened this way goes through the same open path as **File → Open project…**, so the
  lock and the read-only banner behave the same.
- The registration never takes over an extension that another program owns, and it never changes
  `PATH` ([ADR-0051](0051-installed-app-launch-surface.md)).
- The uninstall removes the registration and nothing else.
- The install smoke checks the registration on each leg, with networking blocked.
- The installer stays offline, and no lock changes.

## Considered options

1. **Registry keys written by `reg.exe` lines in `post_install.bat`.** No Python, but the rules
   "leave an extension another program owns" and "remove a key only if nothing else is in it"
   are conditionals that cmd expresses badly, and they could not be tested off Windows.
2. **menuinst file associations.** menuinst attaches them to a conda package, and `tether` is an
   offline-pip-installed wheel ([ADR-0051](0051-installed-app-launch-surface.md), option 2).
3. **A standard-library module in the wheel, run by the install scripts, plus a MIME package on
   Linux and document-type keys in `Tether.app` on macOS** (chosen).

## Decision outcome

**Option 3.** `src/tether/gui/file_type.py` holds the names and the Windows logic; it imports
only the standard library, so `pre_uninstall.bat` can run it and the tests run it on every
system against an in-memory registry.

- **Windows.** `post_install.bat` runs `python -m tether.gui.file_type install --prefix
  "%PREFIX%"` from the installed environment. Under `Software\Classes` it writes the program
  identifier `Tether.Project` (default value `Tether project`, `DefaultIcon` the document icon
  `tether-document.ico` from the installed wheel, and `shell\open\command`
  `"<prefix>\envs\tether\Scripts\tether-gui.exe" "%1"`, the console-less launcher of
  [ADR-0076](0076-the-gui-activates-its-own-dll-path.md)), adds `Tether.Project` to `.tether\OpenWithProgids`, and sets the default
  value of `.tether` to `Tether.Project` **only when it is absent or empty**. When another program
  already owns `.tether`, Tether is only in its **Open with** list. The keys go under
  `HKEY_CURRENT_USER` for a "Just Me" install and under `HKEY_LOCAL_MACHINE` for an all-users
  install: constructor writes `.nonadmin` into the install folder for a "Just Me" install before
  `post_install` runs, and its own uninstaller reads the same file (constructor 3.17.3,
  `constructor/nsis/main.nsi.tmpl`). After a write the module calls `SHChangeNotify` so that
  Explorer shows the icon at once. A failure is a warning and never fails the install.
- **Windows uninstall.** `pre_uninstall.bat` runs the same module with `remove` while the
  environment is still in place. It removes the keys only when the open command starts **this**
  install's `tether-gui.exe` (compared without case), so the uninstall of one Tether install
  leaves another's registration. It then removes Tether's `OpenWithProgids` value, the default of
  `.tether` only when it names `Tether.Project`, and each of the two extension keys only when
  nothing else is left in it, so another program's values stay. It never fails the uninstall.
- **Linux.** `tether.desktop` already lists `MimeType=application/x-tether;` and takes the file
  as `%f`. `post_install.sh` now also writes the shared-mime-info package
  `~/.local/share/mime/packages/tether.xml` (type `application/x-tether`, glob `*.tether`, a
  subclass of `application/x-hdf5`, because a project is an HDF5 file), with the text from the
  installed module, and runs `update-mime-database` on that folder where the tool exists. No
  `mimeapps.list` is written: with no other program for the type the entry is its default, and a
  choice the user made stays. The install guide names the file for the uninstall, because a Linux
  install has no uninstaller.
- **macOS.** `Tether.app`'s `Info.plist` ([ADR-0091](0091-macos-launcher-bundle.md)) exports the
  type `io.github.bioedca.tether.project` (conforms to `public.data`, extension `tether`) and
  declares it in `CFBundleDocumentTypes` with the role `Editor` and the rank `Owner`. No public
  type is claimed. The existing `lsregister -f` registers it, and `macos_app remove` with
  `lsregister -u` takes it away with the bundle. macOS gives the file to the application as a
  file-open event, not as an argument: `tether.gui.app` installs an event filter on the
  `QApplication` as soon as it exists, keeps a path that arrives before the shell is built, and
  then gives each path to `TetherShell.load_project`.
- **A second open (issue item 4).** On Windows and Linux each double-click starts a new
  `tether-gui` process with its own window. If another window holds the project's lock, the new
  window opens it read-only and shows the banner, which is what the lock already does. On macOS
  the system sends the file to the Tether that is running, and that window opens it the way
  **File → Open project…** does: the open project is closed and its lock released first. No
  single-instance service is added (out of scope in #664).
- **The smoke.** Windows: the module's `check`, then cmd's `assoc .tether` and
  `ftype Tether.Project`, which read the merged `HKEY_CLASSES_ROOT` view that Explorer uses; after
  the uninstall, both must answer that nothing is registered. Linux: the entry's `MimeType=` and
  `%f`, the package byte for byte, and where the runner has the tools, the database
  `update-mime-database` built, the content type `gio` reads for a `.tether` file, and the
  `mimeinfo.cache` entry `update-desktop-database` wrote. macOS: the bundle `check` (now with the
  document keys), then the module's `check`, which asks LaunchServices for the type of the
  extension and the bundle identifier of its default program.

### Consequences

- **Good.** A project opens with a double-click on all three systems, through the one open path,
  so the lock rules and the read-only banner need nothing new.
- **Good.** The Windows rules are unit-tested on every system with an in-memory registry:
  install, the foreign owner, the exact removal, a second install, and the case of the path.
- **Bad / accepted.** The double-click itself, Explorer's icon and Finder's behaviour are not
  visible to CI; the smoke reads the same databases the desktop reads, and a real check on a
  Windows PC and a Mac before merge covers the rest.
- **Bad / accepted.** On macOS the file-open event reaches the running process only because the
  bundle's launcher `exec`s `tether-gui`, so the process macOS started is the application
  ([ADR-0091](0091-macos-launcher-bundle.md)). A launcher that starts a child process instead
  would break it.
- **Bad / accepted.** The macOS LaunchServices check uses two calls that are deprecated since
  macOS 12 and are reached through `ctypes`. They still answer; if Apple removes them, the smoke
  check must move to their replacements.
- **Bad / accepted.** An all-users Windows install registers for all users, but the Start Menu
  shortcut stays per user ([ADR-0051](0051-installed-app-launch-surface.md)); this ADR does not
  change the shortcut.

## More information

- `src/tether/gui/file_type.py` — the names, the Windows registration, its check and removal,
  the Linux MIME package and the macOS document keys.
- `src/tether/gui/app.py` — `install_file_open_handler` and its use in `create_shell`.
- `tests/test_file_type.py` and `tests/test_gui_app.py` — the registry rules, the scripts and
  the smoke, the MIME package against a stub prefix, the plist keys, and headless `pytest-qt`
  tests for the file-open event, a path with non-ASCII letters and the read-only banner.
- Qt: `QFileOpenEvent` is sent to `QApplication::instance()` for a file the application
  declares in `CFBundleDocumentTypes` (Qt 6 documentation, `qfileopenevent.html`; PySide6 6.11.1
  in the base lock).

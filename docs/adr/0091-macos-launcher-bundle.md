<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0091 — The macOS installer adds a Tether.app launcher bundle

- **Status:** accepted
- **Date:** 2026-11-14
- **Deciders:** bioedca
- **PRD anchor:** §4.1 NFR-PKG, goal G6, §9.6 (M18 clean-machine acceptance)
- **Milestone:** M18
- **Issue:** [#663](https://github.com/bioedca/tether/issues/663)

## Context and problem statement

[ADR-0051](0051-installed-app-launch-surface.md) gives the installed application a launch surface:
prefix shims in `<prefix>/bin`, a Start Menu shortcut on Windows and a `.desktop` entry on Linux.
On macOS it stopped at the shims ("a proper `.app` bundle is separate work"), so the application
starts only from a Terminal command. Goal G6 says that non-technical lab members never touch a
terminal, and the M18 clean-machine checklist (#197) asks the tester to start Tether "from the OS
launch surface", which macOS did not have.

The maintainer decided on 2026-11-10 (usability audit question D14, option A) that macOS gets a
`Tether.app` in Applications before 1.0.

## Decision drivers

- Launchpad, Spotlight and the Dock must find "Tether" after the install, with no Terminal step.
- The installer stays offline and keeps its size: [ADR-0049](0049-m9-packaging-constructor-architecture.md)'s
  two pinned environments are not copied, merged or re-solved.
- The installers stay unsigned ([ADR-0059](0059-ship-v1-unsigned-with-provenance-as-the-integrity-anchor.md)).
- The install smoke must check the bundle on both macOS legs, with networking blocked.
- The step must never damage a file that Tether did not write.

## Considered options

1. **Keep the shims only.** No install-time risk, but G6 and #197 stay unmet on macOS.
2. **A self-contained frozen application** (py2app or PyInstaller) shipped in a disk image. This
   is the usual Mac form, but it is a second build of the whole stack outside the pinned locks,
   it doubles the size, and it is out of scope for #663.
3. **A menuinst shortcut.** menuinst can make a macOS `.app`, but it attaches shortcuts to a conda
   package, and `tether` is an offline-pip-installed wheel ([ADR-0051](0051-installed-app-launch-surface.md), option 2).
4. **A launcher bundle written by `post_install`** (chosen).

## Decision outcome

**Option 4.** On macOS, `packaging/scripts/post_install.sh` runs
`python -m tether.gui.macos_app install --prefix "$PREFIX"` from the installed environment. The
module writes:

```text
Tether.app/Contents/Info.plist             name, bundle identifier, version, icon
Tether.app/Contents/PkgInfo                APPL????
Tether.app/Contents/MacOS/Tether           sh script: exec <prefix>/envs/tether/bin/tether-gui "$@"
Tether.app/Contents/Resources/tether.icns  the brand icon from the installed wheel (#671)
```

- **Where.** In `~/Applications` when the install folder is inside the home folder (the "install
  for me only" choice, and the documented `installer -target CurrentUserHomeDirectory` command),
  else in `/Applications`. When `/Applications` cannot be written, `~/Applications` again, so the
  launcher never makes an install fail for want of a permission. constructor's `.pkg` offers no
  separate "all users" domain by default, so the folder is chosen from the install folder rather
  than from an installer setting.
- **What it is.** A launcher only. The executable `exec`s the installed `tether-gui` entry point
  ([ADR-0051](0051-installed-app-launch-surface.md)), so the process that macOS starts for the
  bundle is the application, and `resolve_sidecar_python` finds the sibling sidecar from
  `sys.prefix` as before. It drops the `-psn_…` argument older macOS versions add. It holds no
  copy of either environment.
- **The plist.** Written with `plistlib`, so it is valid by construction. The bundle identifier is
  `io.github.bioedca.tether`, the reverse DNS of the documentation site; it must not change
  between releases, because macOS keys its records of the application on it.
  `CFBundleShortVersionString` is the full Tether version (`1.0.0rc3`), which Finder shows;
  `CFBundleVersion` is its numeric release part (`1.0.0`), because macOS compares that key as
  dot-separated integers.
  `LSArchitecturePriority` names the architecture of the installed interpreter (`arm64` for
  the `osx-arm64` installer, `x86_64` for `osx-64`). The executable is a shell script, so macOS
  cannot read an architecture from it: on a real Apple silicon Mac without Rosetta 2
  (macOS 26.6.2), LaunchServices chose `x86_64` for a bundle without the key and refused the
  start with error -10669 and no message. With the key, the same bundle started natively.
- **Reinstall.** A `Tether.app` whose `Info.plist` carries Tether's identifier is replaced, so a
  reinstall updates the version and the launcher target. Any other `Tether.app`, or a folder of
  that name without a readable plist, is left as it is with a warning.
- **Registration.** `lsregister -f` and `mdimport` are run best-effort, so Launchpad and
  Spotlight need not wait for the next scan of the folder; on the real Mac, Spotlight had not
  listed a bundle registered with `lsregister` alone three minutes after the install. When the script runs as root and the bundle is in the user's
  home folder, the bundle is given back to the user, as constructor does for the prefix.
- **Uninstall.** constructor's `pre_uninstall` is Windows-only (constructor 3.17.3,
  `docs/source/construct-yaml.md`), and the macOS uninstall is the documented deletion of the
  install folder. `docs/install.md` puts `python -m tether.gui.macos_app remove --prefix …`
  before it. `remove` deletes a `Tether.app` only when its plist has Tether's identifier and its
  launcher starts that install's `tether-gui`, so an unconditional `rm -rf` never deletes a
  `Tether.app` that the install left alone.
- **Signing.** None, as for the installer ([ADR-0059](0059-ship-v1-unsigned-with-provenance-as-the-integrity-anchor.md)).
  The bundle's only executable is a shell script, and the interpreter it starts is the conda one
  the shims already start. The installer writes the bundle on the Mac, so the bundle carries no
  quarantine attribute; the install guide gives the Open Anyway route in case macOS blocks the
  first start all the same, and the PR records what a real Mac showed.
- **The smoke.** On both macOS legs `install_smoke.sh` checks that `~/Applications/Tether.app`
  exists, runs `plutil -lint` on its `Info.plist`, runs the module's `check` (keys, the
  architecture key, icon, and the launcher's target is the installed `tether-gui`), and runs the bundle's executable with
  `--version`, which proves that it resolves to the installed entry point.

This amends [ADR-0051](0051-installed-app-launch-surface.md)'s macOS clause: macOS `.pkg` installs
get the shims **and** this bundle.

### Consequences

- **Good.** Tether starts from Launchpad, Spotlight and the Dock, with the brand icon, and the
  Terminal command stays the documented fallback. Neither lock changes, the installer stays
  offline, and its size changes by one small script and one icon file.
- **Good.** The bundle writer is standard-library Python inside the wheel, so its layout, its
  plist and its launcher are unit-tested on every system, and `post_install.sh` is run against a
  stub prefix with a stand-in `uname`.
- **Bad / accepted.** Launchpad, Spotlight and the Gatekeeper behaviour of a downloaded install
  are not visible to CI, and the hosted runners may have Rosetta 2, so they cannot show the
  architecture failure either; a real Mac check before merge covers them, and the install
  guide is corrected from what it shows.
- **Bad / accepted.** The bundle is removed by the documented commands, not by an
  uninstaller; macOS `.pkg` installs have none.
- **Bad / accepted.** The bundle names the install folder in its launcher. Moving the install
  folder breaks the bundle; reinstalling writes it again.

## More information

- `src/tether/gui/macos_app.py` — the bundle writer and its `check`.
- `tests/test_macos_app.py` — layout, plist keys, the launcher forwarding its arguments, the
  reinstall and foreign-bundle rules, `remove`, the `post_install.sh` macOS block, the smoke and
  docs contracts, and a headless `pytest-qt` test that the running application has the name and
  icon the bundle declares.
- Out of scope, as #663 states: code signing and notarization (#244), a drag-to-install disk
  image, and a self-contained frozen application. #331 (the in-app updater) must keep this
  bundle pointing at the installed entry point.

# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The macOS ``Tether.app`` launcher bundle (issue #663, ADR-0091).

The macOS installer is a constructor ``.pkg``. It puts the application in
``<prefix>/envs/tether`` and two shims in ``<prefix>/bin`` (ADR-0051), but those give
no icon in Launchpad, Spotlight or the Dock. This module writes a small application
bundle that does:

.. code-block:: text

    Tether.app/
      Contents/
        Info.plist             the name, the bundle identifier, the version, the icon,
                               the architecture to start the launcher as
        MacOS/Tether           a sh script: exec <prefix>/envs/tether/bin/tether-gui
        Resources/tether.icns  the application icon from the installed wheel

The bundle is a launcher only. It holds no copy of the environment, so the size of the
installer and its offline property do not change. ``packaging/scripts/post_install.sh``
runs :func:`main` with ``install`` from the installed environment, the install smoke
runs it with ``check``, and the uninstall in ``docs/install.md`` runs it with ``remove``.

The bundle goes in ``~/Applications`` when the install folder is in the user's home
folder (the "install for me only" choice), and in ``/Applications`` for any other
install folder. If ``/Applications`` cannot be written, it goes in ``~/Applications``.

A ``Tether.app`` that this module did not write (its ``Info.plist`` names another
bundle identifier, or there is none) is never replaced or removed, and ``remove`` deletes
only the bundle whose launcher starts the install it is given.

Only the standard library is imported, so the step costs no Qt and no NumPy, and the
tests run on every system.
"""

from __future__ import annotations

import argparse
import os
import platform
import plistlib
import re
import shlex
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any
from xml.parsers.expat import ExpatError

from tether.gui.identity import APPLICATION_NAME, app_icon_dir

__all__ = [
    "BUNDLE_ID",
    "BUNDLE_NAME",
    "EXECUTABLE_NAME",
    "ICON_FILE",
    "applications_dir",
    "bundle_version",
    "check_bundle",
    "info_plist",
    "is_tether_bundle",
    "launcher_script",
    "launcher_target",
    "main",
    "native_architecture",
    "remove_bundles",
    "write_bundle",
]

#: The folder name of the bundle, as Finder, Launchpad and Spotlight show it.
BUNDLE_NAME = f"{APPLICATION_NAME}.app"

#: The bundle identifier: reverse DNS of the documentation site, bioedca.github.io.
#: It must not change between releases, because macOS keys the bundle's records on it.
BUNDLE_ID = "io.github.bioedca.tether"

#: The file name of the launcher in ``Contents/MacOS``.
EXECUTABLE_NAME = APPLICATION_NAME

#: The file name of the icon in ``Contents/Resources``.
ICON_FILE = "tether.icns"

_RELEASE = re.compile(r"\d+(?:\.\d+)*")


def applications_dir(prefix: Path, home: Path) -> Path:
    """The ``Applications`` folder for an install into *prefix*.

    ``~/Applications`` when *prefix* is inside *home*, else ``/Applications``. When
    ``/Applications`` is not writable (an install without administrator rights into a
    folder outside the home folder), ``~/Applications`` again.
    """
    prefix = Path(os.path.abspath(prefix))
    home = Path(os.path.abspath(home))
    user_dir = home / "Applications"
    if prefix == home or home in prefix.parents:
        return user_dir
    system_dir = Path("/Applications")
    if system_dir.is_dir() and os.access(system_dir, os.W_OK):
        return system_dir
    return user_dir


def bundle_version(version: str) -> str:
    """The numeric release part of *version*, for ``CFBundleVersion``.

    ``1.0.0rc3`` gives ``1.0.0``. macOS compares bundle versions as integers separated
    by dots, so the pre-release text stays out of this key. A version without numbers
    gives ``0``.
    """
    match = _RELEASE.match(version.strip())
    return match.group(0) if match else "0"


def native_architecture(machine: str | None = None) -> str | None:
    """The macOS architecture name of *machine* (default: this interpreter's), or None.

    The bundle executable is a shell script, so macOS cannot read an architecture from
    it. Without ``LSArchitecturePriority``, LaunchServices on Apple silicon may choose
    ``x86_64`` for it and, when Rosetta is not installed, refuse to start it with error
    -10669 and no message (seen on macOS 26.6.2, ADR-0091). The installed interpreter
    runs as the architecture of the build (``osx-arm64`` or ``osx-64``), so its
    ``platform.machine()`` names the architecture the bundle must ask for.
    """
    name = (machine if machine is not None else platform.machine()).lower()
    if name in ("arm64", "aarch64"):
        return "arm64"
    if name in ("x86_64", "amd64"):
        return "x86_64"
    return None


def info_plist(version: str, architecture: str | None = None) -> dict[str, Any]:
    """The ``Info.plist`` keys of the bundle for Tether *version*.

    *architecture* (``arm64`` or ``x86_64``) is written as ``LSArchitecturePriority``,
    so that macOS starts the launcher natively; see :func:`native_architecture`.
    """
    keys: dict[str, Any] = {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleDisplayName": APPLICATION_NAME,
        "CFBundleExecutable": EXECUTABLE_NAME,
        "CFBundleIconFile": ICON_FILE,
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": APPLICATION_NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": bundle_version(version),
        "LSApplicationCategoryType": "public.app-category.education",
        "NSHighResolutionCapable": True,
    }
    if architecture is not None:
        keys["LSArchitecturePriority"] = [architecture]
    return keys


def _gui_entry_point(prefix: Path) -> Path:
    return Path(os.path.abspath(prefix)) / "envs" / "tether" / "bin" / "tether-gui"


def launcher_script(prefix: Path) -> str:
    """The text of ``Contents/MacOS/Tether`` for an install into *prefix*.

    It replaces itself with the installed ``tether-gui`` entry point (``exec``), so the
    process that macOS started for the bundle is the application. Older macOS versions
    add a ``-psn_…`` process-serial-number argument; the script drops it, so that
    ``tether-gui`` does not read it as a project path.
    """
    target = shlex.quote(str(_gui_entry_point(prefix)))
    return (
        "#!/bin/sh\n"
        "# Generated by the Tether installer (ADR-0091). Starts the installed application.\n"
        'case "${1:-}" in -psn_*) shift ;; esac\n'
        f'exec {target} "$@"\n'
    )


def launcher_target(script: str) -> Path | None:
    """The program that a launcher written by :func:`launcher_script` ``exec``s, or None."""
    for line in script.splitlines():
        if line.startswith("exec "):
            try:
                words = shlex.split(line)
            except ValueError:
                return None
            return Path(words[1]) if len(words) == 3 and words[2] == "$@" else None
    return None


def _same_file(a: Path, b: Path) -> bool:
    """True when *a* and *b* name one file.

    A path comparison is not enough on macOS: constructor installs into a lower-case
    folder (``~/tether``) on a file system that ignores case, so ``~/Tether`` names the
    same folder with a different string.
    """
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.abspath(a) == os.path.abspath(b)


def _read_plist(bundle: Path) -> dict[str, Any] | None:
    try:
        with open(bundle / "Contents" / "Info.plist", "rb") as handle:
            data = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException, ValueError, ExpatError):
        # ExpatError: a file that starts as XML and then breaks is not a ValueError.
        return None
    return data if isinstance(data, dict) else None


def is_tether_bundle(bundle: Path) -> bool:
    """True when *bundle* has an ``Info.plist`` with Tether's bundle identifier."""
    data = _read_plist(bundle)
    return data is not None and data.get("CFBundleIdentifier") == BUNDLE_ID


def write_bundle(
    prefix: Path,
    applications: Path,
    version: str,
    icon: Path | None = None,
) -> Path | None:
    """Write ``Tether.app`` into *applications* for an install into *prefix*.

    A bundle that this function wrote before is replaced, so a reinstall over an older
    install updates the version and the launcher. Any other ``Tether.app`` is left as
    it is and the function returns ``None``. The new bundle is built in a temporary
    folder next to the target and then moved into place.
    """
    applications = Path(applications)
    bundle = applications / BUNDLE_NAME
    if bundle.is_symlink() or (bundle.exists() and not is_tether_bundle(bundle)):
        return None
    applications.mkdir(parents=True, exist_ok=True)
    icon = app_icon_dir() / ICON_FILE if icon is None else Path(icon)

    staging = Path(tempfile.mkdtemp(prefix=".tether-app-", dir=applications))
    try:
        new = staging / BUNDLE_NAME
        macos_d = new / "Contents" / "MacOS"
        resources_d = new / "Contents" / "Resources"
        macos_d.mkdir(parents=True)
        resources_d.mkdir()
        with open(new / "Contents" / "Info.plist", "wb") as handle:
            plistlib.dump(info_plist(version, native_architecture()), handle, fmt=plistlib.FMT_XML)
        (new / "Contents" / "PkgInfo").write_text("APPL????", encoding="ascii")
        exe = macos_d / EXECUTABLE_NAME
        exe.write_text(launcher_script(prefix), encoding="utf-8")
        exe.chmod(0o755)
        shutil.copyfile(icon, resources_d / ICON_FILE)
        if bundle.exists():
            shutil.rmtree(bundle)
        os.replace(new, bundle)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return bundle


def check_bundle(bundle: Path, prefix: Path) -> list[str]:
    """The problems of *bundle* for an install into *prefix*; empty when it is correct.

    The checks: the ``Info.plist`` parses and carries the keys of :func:`info_plist`
    (the version excepted, and ``LSArchitecturePriority`` naming the architecture of
    the interpreter that runs the check), the executable it names exists and can run, that
    executable starts the installed ``tether-gui`` of *prefix*, and the icon it names
    is a non-empty file.
    """
    bundle = Path(bundle)
    data = _read_plist(bundle)
    if data is None:
        return [f"no readable Contents/Info.plist in {bundle}"]
    problems = []
    expected = info_plist("0", native_architecture())
    for key in sorted(expected):
        if key in ("CFBundleShortVersionString", "CFBundleVersion"):
            if not isinstance(data.get(key), str) or not data[key]:
                problems.append(f"Info.plist has no {key}")
        elif data.get(key) != expected[key]:
            problems.append(f"Info.plist {key} is {data.get(key)!r}, not {expected[key]!r}")
    exe = bundle / "Contents" / "MacOS" / str(data.get("CFBundleExecutable", ""))
    if not exe.is_file():
        problems.append(f"the bundle executable is missing: {exe}")
    else:
        if os.name == "posix" and not exe.stat().st_mode & stat.S_IXUSR:
            problems.append(f"the bundle executable cannot run: {exe}")
        started = launcher_target(exe.read_text(encoding="utf-8"))
        if started is None or not _same_file(started, _gui_entry_point(prefix)):
            problems.append(f"the bundle executable does not start {_gui_entry_point(prefix)}")
    target = _gui_entry_point(prefix)
    if not target.is_file():
        problems.append(f"the entry point the bundle starts is missing: {target}")
    icon = bundle / "Contents" / "Resources" / str(data.get("CFBundleIconFile", ""))
    if not icon.is_file() or icon.stat().st_size == 0:
        problems.append(f"the bundle icon is missing: {icon}")
    return problems


def remove_bundles(prefix: Path, home: Path) -> tuple[list[Path], list[Path]]:
    """Remove the ``Tether.app`` of the install in *prefix*; return (removed, failed).

    Both ``~/Applications`` and ``/Applications`` are looked at. A bundle is removed only
    when its ``Info.plist`` has Tether's identifier **and** its launcher starts the
    ``tether-gui`` of *prefix*, so the uninstall never deletes another program's
    ``Tether.app`` or the launcher of another Tether install. A bundle that cannot be
    removed (``/Applications`` needs an administrator) is returned in *failed*.
    """
    removed: list[Path] = []
    failed: list[Path] = []
    for folder in (Path(home) / "Applications", Path("/Applications")):
        bundle = folder / BUNDLE_NAME
        if bundle.is_symlink() or not is_tether_bundle(bundle):
            continue
        data = _read_plist(bundle) or {}
        exe = bundle / "Contents" / "MacOS" / str(data.get("CFBundleExecutable", ""))
        try:
            started = launcher_target(exe.read_text(encoding="utf-8"))
        except OSError:
            continue
        if started is None or not _same_file(started, _gui_entry_point(prefix)):
            continue
        try:
            shutil.rmtree(bundle)
        except OSError:
            failed.append(bundle)
        else:
            removed.append(bundle)
    return removed, failed


def main(argv: list[str] | None = None) -> int:
    """``python -m tether.gui.macos_app install|check|remove`` (installer, smoke, uninstall)."""
    parser = argparse.ArgumentParser(prog="python -m tether.gui.macos_app")
    commands = parser.add_subparsers(dest="command", required=True)
    install = commands.add_parser("install", help="write Tether.app for an install")
    install.add_argument("--prefix", type=Path, required=True)
    install.add_argument("--home", type=Path, default=Path.home())
    check = commands.add_parser("check", help="check a Tether.app against an install")
    check.add_argument("bundle", type=Path)
    check.add_argument("--prefix", type=Path, required=True)
    remove = commands.add_parser("remove", help="remove the Tether.app of an install")
    remove.add_argument("--prefix", type=Path, required=True)
    remove.add_argument("--home", type=Path, default=Path.home())
    args = parser.parse_args(argv)

    if args.command == "remove":
        removed, failed = remove_bundles(args.prefix, args.home)
        for bundle in removed:
            print(f"removed {bundle}")
        for bundle in failed:
            print(f"could not remove {bundle}; run this command with sudo", file=sys.stderr)
        if not removed and not failed:
            print("no Tether.app of this install was found")
        return 1 if failed else 0

    if args.command == "install":
        from tether import __version__

        target = applications_dir(args.prefix, args.home)
        bundle = write_bundle(args.prefix, target, __version__)
        if bundle is None:
            print(
                f"warning: {target / BUNDLE_NAME} is not Tether's launcher; left it as it is",
                file=sys.stderr,
            )
            return 0
        print(bundle)
        return 0

    problems = check_bundle(args.bundle, args.prefix)
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        return 1
    print(f"{args.bundle}: OK")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the CLI tests
    raise SystemExit(main())

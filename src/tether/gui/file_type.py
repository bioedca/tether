# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
r"""The ``.tether`` file type on the desktop (issue #664, ADR-0118).

A double-click on a ``.tether`` project starts Tether with that project open. The
installer registers the file type on each system:

* **Windows.** ``packaging/scripts/post_install.bat`` runs [`main`][] with ``install``. It
  writes the program identifier [`PROG_ID`][] under ``Software\Classes``, with the name
  [`TYPE_NAME`][], the document icon from the installed wheel and the open command
  ``"<prefix>\envs\tether\Scripts\tether-gui.exe" "%1"``, and it names that identifier
  for the ``.tether`` extension. ``pre_uninstall.bat`` runs ``remove``, and the install
  smoke runs ``check``.
* **Linux.** ``post_install.sh`` adds the MIME type [`MIME_TYPE`][] to the user's MIME
  database (the text is [`mime_package_xml`][]) and lists it in ``tether.desktop``.
* **macOS.** ``Tether.app`` declares the document type in its ``Info.plist``
  ([`macos_document_keys`][], used by [`tether.gui.macos_app`][]). macOS gives the file to
  the application as a file-open event, not as an argument; [`tether.gui.app`][] passes it
  to the shell.

The registration never takes over an extension that another program owns: when the
``.tether`` key of the registry already names another program, Tether only adds itself to
the "Open with" list of the extension. ``remove`` deletes only what ``install`` wrote, and
only when the open command starts the install it is given, so the uninstall of one Tether
install never removes the registration of another. Nothing here changes ``PATH``
(ADR-0051).

Only the standard library is imported, and the registry is reached through the small
[`Registry`][] interface, so the tests run on every system with an in-memory registry.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
from collections.abc import Iterator
from pathlib import Path, PureWindowsPath
from typing import Any, Protocol

__all__ = [
    "EXTENSION",
    "HDF_MIME_TYPE",
    "MACOS_TYPE_ID",
    "MIME_TYPE",
    "PROG_ID",
    "TYPE_NAME",
    "MemoryRegistry",
    "Registry",
    "WindowsRegistry",
    "check_macos",
    "check_windows",
    "document_icon_path",
    "install_scope",
    "macos_document_keys",
    "macos_registration",
    "main",
    "mime_package_xml",
    "open_command",
    "register_windows",
    "unregister_windows",
]

#: The file name extension of a Tether project.
EXTENSION = ".tether"

#: The name the desktop shows for the file type.
TYPE_NAME = "Tether project"

#: The Windows program identifier of the file type. It must not change between releases,
#: because an earlier install's keys are found by it.
PROG_ID = "Tether.Project"

#: The MIME type on Linux. ``x-`` because the type is not registered with IANA.
MIME_TYPE = "application/x-tether"

#: The shared-mime-info type of HDF files (HDF4 and HDF5), the parent of [`MIME_TYPE`][].
HDF_MIME_TYPE = "application/x-hdf"

#: The uniform type identifier on macOS, under the bundle identifier of ``Tether.app``.
MACOS_TYPE_ID = "io.github.bioedca.tether.project"

_CLASSES = "Software\\Classes"
_EXTENSION_KEY = f"{_CLASSES}\\{EXTENSION}"
_OPEN_WITH_KEY = f"{_EXTENSION_KEY}\\OpenWithProgids"
_PROG_ID_KEY = f"{_CLASSES}\\{PROG_ID}"
_ICON_KEY = f"{_PROG_ID_KEY}\\DefaultIcon"
_COMMAND_KEY = f"{_PROG_ID_KEY}\\shell\\open\\command"

#: The registry roots: the current user's, and the computer's (an all-users install).
USER = "HKCU"
MACHINE = "HKLM"


def document_icon_path() -> Path:
    """Return the Windows document icon in the installed package."""
    return Path(__file__).resolve().parent / "assets" / "brand" / "document" / "tether-document.ico"


def _gui_exe(prefix: str | os.PathLike[str]) -> str:
    env = PureWindowsPath(os.fspath(prefix)) / "envs" / "tether"
    return str(env / "Scripts" / "tether-gui.exe")


def open_command(prefix: str | os.PathLike[str]) -> str:
    """Return the open command of the file type for an install into *prefix*.

    Both the program and ``%1`` are in quotes, so a path with spaces reaches
    ``tether-gui`` as one argument. ``tether-gui.exe`` is the console-less launcher, so
    no console window opens (issue #579).
    """
    return f'"{_gui_exe(prefix)}" "%1"'


def install_scope(prefix: str | os.PathLike[str]) -> str:
    """Return the registry root for an install into *prefix*: [`USER`][] or [`MACHINE`][].

    The constructor installer writes ``.nonadmin`` into the install folder for a "Just
    Me" install, before ``post_install`` runs (constructor 3.17.3,
    ``nsis/main.nsi.tmpl``); its uninstaller reads the same file to choose between the
    user's and all users' settings. An install without it is an all-users install.
    """
    return USER if (Path(prefix) / ".nonadmin").exists() else MACHINE


class Registry(Protocol):
    """The part of the Windows registry this module reads and writes.

    *root* is [`USER`][] or [`MACHINE`][], *key* a path under it, and *name* a value
    name, where ``""`` is the default value of the key.
    """

    def get(self, root: str, key: str, name: str) -> str | None:
        """Return the string value *name* of *key*, or None when either is absent."""

    def set(self, root: str, key: str, name: str, value: str) -> None:
        """Write the string value *name* of *key*, and make *key* if it is absent."""

    def delete_value(self, root: str, key: str, name: str) -> None:
        """Delete the value *name* of *key*, if it exists."""

    def delete_tree(self, root: str, key: str) -> None:
        """Delete *key* with all its values and subkeys, if it exists."""

    def is_empty(self, root: str, key: str) -> bool:
        """Tell whether *key* exists and has no values and no subkeys."""

    def delete_key(self, root: str, key: str) -> None:
        """Delete *key*, which has no subkeys, if it exists."""


class MemoryRegistry:
    """A [`Registry`][] in memory, for the tests and for a dry run.

    ``keys`` maps ``(root, key)`` to the values of that key; a key is in it when it
    exists. Key names are compared without case, as in the Windows registry.
    """

    def __init__(self) -> None:
        self.keys: dict[tuple[str, str], dict[str, str]] = {}

    @staticmethod
    def _id(root: str, key: str) -> tuple[str, str]:
        return root, key.lower()

    def get(self, root: str, key: str, name: str) -> str | None:
        """Return the string value *name* of *key*, or None."""
        return self.keys.get(self._id(root, key), {}).get(name)

    def set(self, root: str, key: str, name: str, value: str) -> None:
        """Write the string value *name* of *key*, making the key."""
        parts = key.split("\\")
        for depth in range(1, len(parts) + 1):
            self.keys.setdefault(self._id(root, "\\".join(parts[:depth])), {})
        self.keys[self._id(root, key)][name] = value

    def delete_value(self, root: str, key: str, name: str) -> None:
        """Delete the value *name* of *key*, if it exists."""
        self.keys.get(self._id(root, key), {}).pop(name, None)

    def delete_tree(self, root: str, key: str) -> None:
        """Delete *key* and everything under it, if it exists."""
        top = self._id(root, key)
        for item in list(self.keys):
            if item == top or (item[0] == root and item[1].startswith(top[1] + "\\")):
                del self.keys[item]

    def _has_subkeys(self, root: str, key: str) -> bool:
        prefix = self._id(root, key)[1] + "\\"
        return any(r == root and k.startswith(prefix) for r, k in self.keys)

    def is_empty(self, root: str, key: str) -> bool:
        """Tell whether *key* exists with no values and no subkeys."""
        values = self.keys.get(self._id(root, key))
        return values is not None and not values and not self._has_subkeys(root, key)

    def delete_key(self, root: str, key: str) -> None:
        """Delete *key* if it has no subkeys."""
        if not self._has_subkeys(root, key):
            self.keys.pop(self._id(root, key), None)


class WindowsRegistry:  # pragma: no cover - needs Windows; the install smoke runs it
    """The real Windows registry, through the standard-library ``winreg``."""

    def __init__(self) -> None:
        import winreg

        self._winreg: Any = winreg
        self._roots = {USER: winreg.HKEY_CURRENT_USER, MACHINE: winreg.HKEY_LOCAL_MACHINE}

    @contextlib.contextmanager
    def _open(self, root: str, key: str, access: int) -> Iterator[Any]:
        handle = self._winreg.OpenKey(self._roots[root], key, 0, access)
        try:
            yield handle
        finally:
            self._winreg.CloseKey(handle)

    def get(self, root: str, key: str, name: str) -> str | None:
        """Return the string value *name* of *key*, or None."""
        try:
            with self._open(root, key, self._winreg.KEY_READ) as handle:
                value, _kind = self._winreg.QueryValueEx(handle, name)
        except OSError:
            return None
        return value if isinstance(value, str) else None

    def set(self, root: str, key: str, name: str, value: str) -> None:
        """Write the string value *name* of *key*, making the key."""
        handle = self._winreg.CreateKeyEx(self._roots[root], key, 0, self._winreg.KEY_WRITE)
        try:
            self._winreg.SetValueEx(handle, name, 0, self._winreg.REG_SZ, value)
        finally:
            self._winreg.CloseKey(handle)

    def delete_value(self, root: str, key: str, name: str) -> None:
        """Delete the value *name* of *key*, if it exists."""
        with (
            contextlib.suppress(OSError),
            self._open(root, key, self._winreg.KEY_SET_VALUE) as handle,
        ):
            self._winreg.DeleteValue(handle, name)

    def delete_tree(self, root: str, key: str) -> None:
        """Delete *key* and everything under it, if it exists."""
        try:
            with self._open(root, key, self._winreg.KEY_READ) as handle:
                subkeys = []
                with contextlib.suppress(OSError):
                    index = 0
                    while True:
                        subkeys.append(self._winreg.EnumKey(handle, index))
                        index += 1
        except OSError:
            return
        for subkey in subkeys:
            self.delete_tree(root, f"{key}\\{subkey}")
        self.delete_key(root, key)

    def is_empty(self, root: str, key: str) -> bool:
        """Tell whether *key* exists with no values and no subkeys."""
        try:
            with self._open(root, key, self._winreg.KEY_READ) as handle:
                subkeys, values, _modified = self._winreg.QueryInfoKey(handle)
        except OSError:
            return False
        return subkeys == 0 and values == 0

    def delete_key(self, root: str, key: str) -> None:
        """Delete *key* if it has no subkeys."""
        with contextlib.suppress(OSError):
            self._winreg.DeleteKey(self._roots[root], key)


def _ours(registry: Registry, root: str, prefix: str | os.PathLike[str]) -> bool:
    """Tell whether the program identifier in *root* opens files with this install."""
    command = registry.get(root, _COMMAND_KEY, "")
    return command is not None and command.lower() == open_command(prefix).lower()


def register_windows(
    registry: Registry, root: str, prefix: str | os.PathLike[str], icon: Path | None = None
) -> bool:
    """Register ``.tether`` in *root* for an install into *prefix*.

    Returns True when Tether is now the program that opens the extension, and False when
    another program already owned it: then only Tether's entry in the extension's
    ``OpenWithProgids`` list is added, so Tether shows under "Open with" and the
    double-click keeps opening the other program. The program identifier is written in
    either case; an identifier left by an earlier install is replaced, so the newest
    install opens the files.

    The owner is the one Windows uses, from its merged view of the two roots: the current
    user's registration when it names a program, else the computer's. So a per-user
    install does not take the extension from a program registered for all users, which
    a per-user default would override.
    """
    icon = document_icon_path() if icon is None else icon
    registry.set(root, _PROG_ID_KEY, "", TYPE_NAME)
    registry.set(root, _ICON_KEY, "", f'"{icon}",0')
    registry.set(root, _COMMAND_KEY, "", open_command(prefix))
    registry.set(root, _OPEN_WITH_KEY, PROG_ID, "")
    owner = _effective_owner(registry)
    if owner and owner.lower() != PROG_ID.lower():
        return False
    if not registry.get(root, _EXTENSION_KEY, ""):
        registry.set(root, _EXTENSION_KEY, "", PROG_ID)
    return True


def _effective_owner(registry: Registry) -> str | None:
    """Return the program identifier Windows opens ``.tether`` with, or None if there is none.

    In the merged ``HKEY_CLASSES_ROOT`` view the current user's value wins over the
    computer's, and an empty value names no program.
    """
    for root in (USER, MACHINE):
        owner = registry.get(root, _EXTENSION_KEY, "")
        if owner:
            return owner
    return None


def unregister_windows(registry: Registry, root: str, prefix: str | os.PathLike[str]) -> bool:
    """Remove the registration of the install in *prefix* from *root*; return True if removed.

    Nothing is removed when the program identifier opens files with another install (or
    does not exist): a second Tether install keeps its registration. Otherwise the
    identifier goes, then Tether's entry in ``OpenWithProgids`` and the extension's default
    value if it names Tether, and then each of the two extension keys only if nothing else
    is left in it, so another program's values under ``.tether`` stay.
    """
    if not _ours(registry, root, prefix):
        return False
    registry.delete_tree(root, _PROG_ID_KEY)
    registry.delete_value(root, _OPEN_WITH_KEY, PROG_ID)
    owner = registry.get(root, _EXTENSION_KEY, "")
    if owner is not None and owner.lower() == PROG_ID.lower():
        registry.delete_value(root, _EXTENSION_KEY, "")
    for key in (_OPEN_WITH_KEY, _EXTENSION_KEY):
        if registry.is_empty(root, key):
            registry.delete_key(root, key)
    return True


def check_windows(
    registry: Registry, root: str, prefix: str | os.PathLike[str], *, owner: bool = True
) -> list[str]:
    """Return the problems of the registration in *root* for *prefix*; empty if correct.

    With *owner* (the install smoke, on a runner where nothing else claims ``.tether``)
    the extension must also name Tether's program identifier.
    """
    problems = []
    if registry.get(root, _PROG_ID_KEY, "") != TYPE_NAME:
        problems.append(f"{root}\\{_PROG_ID_KEY} does not name the file type {TYPE_NAME!r}")
    if not _ours(registry, root, prefix):
        problems.append(
            f"{root}\\{_COMMAND_KEY} is {registry.get(root, _COMMAND_KEY, '')!r},"
            f" not {open_command(prefix)!r}"
        )
    icon = registry.get(root, _ICON_KEY, "") or ""
    icon_file = icon.rsplit(",", 1)[0].strip('"')
    if not icon_file or not Path(icon_file).is_file():
        problems.append(f"the file type icon is missing: {icon!r}")
    if registry.get(root, _OPEN_WITH_KEY, PROG_ID) is None:
        problems.append(f"{root}\\{_OPEN_WITH_KEY} does not list {PROG_ID}")
    if owner and (registry.get(root, _EXTENSION_KEY, "") or "").lower() != PROG_ID.lower():
        problems.append(f"{root}\\{_EXTENSION_KEY} does not name {PROG_ID}")
    return problems


def _notify_shell() -> None:  # pragma: no cover - needs Windows
    """Best-effort: tell Explorer that file associations changed, so icons update now."""
    with contextlib.suppress(Exception):
        import ctypes

        shcne_assocchanged, shcnf_idlist = 0x08000000, 0x0000
        ctypes.windll.shell32.SHChangeNotify(shcne_assocchanged, shcnf_idlist, None, None)


def mime_package_xml() -> str:
    """Return the shared-mime-info package that defines [`MIME_TYPE`][] on Linux.

    A ``.tether`` project is an HDF5 file, so the type is a subclass of
    ``application/x-hdf``, the type that shared-mime-info gives HDF4 and HDF5 files
    (``*.h5`` among them): a program that reads HDF5 is still offered for it.
    """
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!-- Generated by the Tether installer (issue #664, ADR-0118). -->\n"
        '<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">\n'
        f'  <mime-type type="{MIME_TYPE}">\n'
        f"    <comment>{TYPE_NAME}</comment>\n"
        f'    <sub-class-of type="{HDF_MIME_TYPE}"/>\n'
        f'    <glob pattern="*{EXTENSION}"/>\n'
        "  </mime-type>\n"
        "</mime-info>\n"
    )


def macos_document_keys() -> dict[str, Any]:
    """Return the ``Info.plist`` keys that make ``Tether.app`` open ``.tether`` files.

    ``UTExportedTypeDeclarations`` declares the type, because Tether defines the format;
    ``CFBundleDocumentTypes`` makes the bundle its owner and editor. No public type and no
    extension that another type declares is claimed.
    """
    return {
        "CFBundleDocumentTypes": [
            {
                "CFBundleTypeName": TYPE_NAME,
                "CFBundleTypeRole": "Editor",
                "LSHandlerRank": "Owner",
                "LSItemContentTypes": [MACOS_TYPE_ID],
            }
        ],
        "UTExportedTypeDeclarations": [
            {
                "UTTypeIdentifier": MACOS_TYPE_ID,
                "UTTypeDescription": TYPE_NAME,
                "UTTypeConformsTo": ["public.data"],
                "UTTypeTagSpecification": {"public.filename-extension": [EXTENSION[1:]]},
            }
        ],
    }


_CORE_FOUNDATION = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
_CORE_SERVICES = "/System/Library/Frameworks/CoreServices.framework/CoreServices"


def macos_registration(  # pragma: no cover - needs macOS
    extension: str = EXTENSION,
) -> tuple[str | None, str | None]:
    """Return (type identifier, bundle identifier of its default program) for *extension*.

    The answer comes from LaunchServices, the database Finder reads for a double-click,
    so the install smoke can tell that ``Tether.app`` is registered as the program that
    opens ``.tether`` files and not only that its ``Info.plist`` says so. The two calls
    (``UTTypeCreatePreferredIdentifierForTag`` and
    ``LSCopyDefaultRoleHandlerForContentType``) are deprecated since macOS 12 but still
    answer; they are reached through ``ctypes`` because the standard library has no
    binding, and the install adds no package for one check. An extension with no
    declared type gives a ``dyn.`` identifier, and a type with no program gives None.
    """
    import ctypes

    cf = ctypes.cdll.LoadLibrary(_CORE_FOUNDATION)
    cs = ctypes.cdll.LoadLibrary(_CORE_SERVICES)
    utf8 = 0x08000100  # kCFStringEncodingUTF8
    roles_all = 0xFFFFFFFF  # kLSRolesAll
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFStringGetCString.restype = ctypes.c_bool
    cf.CFStringGetCString.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_long,
        ctypes.c_uint32,
    ]
    cf.CFRelease.restype = None
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    cs.UTTypeCreatePreferredIdentifierForTag.restype = ctypes.c_void_p
    cs.UTTypeCreatePreferredIdentifierForTag.argtypes = [ctypes.c_void_p] * 3
    cs.LSCopyDefaultRoleHandlerForContentType.restype = ctypes.c_void_p
    cs.LSCopyDefaultRoleHandlerForContentType.argtypes = [ctypes.c_void_p, ctypes.c_uint32]

    def text(ref: int | None) -> str | None:
        if not ref:
            return None
        buffer = ctypes.create_string_buffer(1024)
        try:
            return buffer.value.decode() if cf.CFStringGetCString(ref, buffer, 1024, utf8) else None
        finally:
            cf.CFRelease(ref)

    tag_class = cf.CFStringCreateWithCString(None, b"public.filename-extension", utf8)
    tag = cf.CFStringCreateWithCString(None, extension.lstrip(".").encode(), utf8)
    try:
        type_ref = cs.UTTypeCreatePreferredIdentifierForTag(tag_class, tag, None)
    finally:
        cf.CFRelease(tag)
        cf.CFRelease(tag_class)
    if not type_ref:
        return None, None
    handler = text(cs.LSCopyDefaultRoleHandlerForContentType(type_ref, roles_all))
    return text(type_ref), handler


def check_macos(registration: tuple[str | None, str | None], bundle_id: str) -> list[str]:
    """Return the problems of a LaunchServices *registration*; empty if correct.

    *registration* is what [`macos_registration`][] answers, and *bundle_id* the
    identifier of ``Tether.app``.
    """
    type_id, handler = registration
    problems = []
    if type_id != MACOS_TYPE_ID:
        problems.append(f"macOS gives {EXTENSION} the type {type_id!r}, not {MACOS_TYPE_ID!r}")
    elif (handler or "").lower() != bundle_id.lower():
        problems.append(f"macOS opens {MACOS_TYPE_ID} with {handler!r}, not {bundle_id!r}")
    return problems


def main(argv: list[str] | None = None, registry: Registry | None = None) -> int:
    """``python -m tether.gui.file_type install|check|remove --prefix P``.

    On Windows the three commands write, check and remove the registry keys (the
    installer, the install smoke and the uninstaller run them). ``install`` never fails
    the installer and ``remove`` never fails the uninstaller: a problem is a warning on
    stderr and the exit status is 0. ``check`` exits 1 and names each problem.

    On macOS ``check`` asks LaunchServices which program opens ``.tether`` files
    ([`macos_registration`][]); ``install`` and ``remove`` have nothing to do there,
    because ``Tether.app`` declares the type and is written and removed by
    [`tether.gui.macos_app`][]. On Linux, ``post_install.sh`` writes the MIME type.
    """
    parser = argparse.ArgumentParser(prog="python -m tether.gui.file_type")
    parser.add_argument("command", choices=("install", "check", "remove"))
    parser.add_argument("--prefix", type=Path, required=True)
    args = parser.parse_args(argv)
    if registry is None:
        if sys.platform == "darwin" and args.command == "check":  # pragma: no cover - macOS
            from tether.gui.macos_app import BUNDLE_ID

            problems = check_macos(macos_registration(), BUNDLE_ID)
            for problem in problems:
                print(problem, file=sys.stderr)
            if not problems:
                print(f"{EXTENSION} is {MACOS_TYPE_ID} and opens with {BUNDLE_ID}")
            return 1 if problems else 0
        if sys.platform != "win32":
            print(f"nothing to {args.command} on this system", file=sys.stderr)
            return 0 if args.command != "check" else 1
        registry = WindowsRegistry()  # pragma: no cover - needs Windows
    root = install_scope(args.prefix)

    if args.command == "check":
        problems = check_windows(registry, root, args.prefix)
        for problem in problems:
            print(problem, file=sys.stderr)
        if problems:
            return 1
        print(f"{EXTENSION} opens with {open_command(args.prefix)} ({root})")
        return 0

    try:
        if args.command == "install":
            if register_windows(registry, root, args.prefix):
                print(f"{EXTENSION} opens with Tether ({root})")
            else:
                print(
                    f"warning: another program opens {EXTENSION} files; Tether is added to"
                    " 'Open with' only",
                    file=sys.stderr,
                )
        else:
            # Both roots. Each removal first checks that the registration opens files
            # with this install, so looking in the other root as well can remove nothing
            # but this install's own keys.
            other = USER if root == MACHINE else MACHINE
            removed = [r for r in (root, other) if unregister_windows(registry, r, args.prefix)]
            print(f"removed the {EXTENSION} registration from {', '.join(removed) or 'nowhere'}")
    except OSError as exc:
        print(f"warning: could not {args.command} {EXTENSION}: {exc}", file=sys.stderr)
        return 0
    if isinstance(registry, WindowsRegistry):  # pragma: no cover - needs Windows
        _notify_shell()
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the CLI tests
    raise SystemExit(main())

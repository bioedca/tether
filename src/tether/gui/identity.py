# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""How the operating system knows that a window is Tether (issue #673).

Three things tell a desktop which program a window belongs to and which picture to show
for it. This module sets all three, once, before the first window opens:

* **The application icon.** :func:`application_icon` builds one ``QIcon`` from the
  PNG sizes of the brand kit's dark tile (#671). Set on the ``QApplication``, it is the
  default icon of every window and dialog, the taskbar button on Windows and the Dock
  icon on macOS while Tether runs. Each size is its own drawing: the tiny mark at 16 px,
  the small mark at 24 and 32 px, the mark without the pixel grid at 48 px and the mark
  with the grid from 64 px (docs/brand.md, "Small sizes"). Qt picks the nearest size, so
  a 16 px title bar gets the drawing made for 16 px instead of a shrunken large one. The
  sizes stop at 256 px, except on macOS (:data:`MACOS_EXTRA_PNG_SIZES`).
* **The Windows application identity.** A Python program's windows belong to
  ``pythonw.exe``, so without an identity of its own the taskbar groups Tether with every
  other Python program and shows Python's icon. :func:`set_windows_app_id` gives the
  process an explicit AppUserModelID. The Start Menu shortcut must carry the same ID,
  or a shortcut pinned to the taskbar and the window it opens show as two buttons:
  ``post_install.bat`` writes it with :func:`set_shortcut_app_id`, and the install
  smoke reads it back with :func:`shortcut_app_id`.
* **The Linux desktop entry.** ``setDesktopFileName`` names ``tether.desktop`` (written
  by ``packaging/scripts/post_install.sh``), so a Wayland compositor matches the window
  to the menu entry and its ``Icon=tether``. On X11 the entry's ``StartupWMClass`` does
  the same job against the window class, which Qt takes from the application name.

Like the rest of :mod:`tether.gui`, importing this module costs no Qt: the Qt imports
are inside the functions. ``post_install.sh`` imports it to find the icon files, and the
install smoke reads the same paths. The Qt import is PySide6 itself, not
``pyqtgraph.Qt``: ``create_shell`` applies the identity before the startup splash
(#676), which must be on the screen before pyqtgraph and NumPy load.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

__all__ = [
    "APPLICATION_NAME",
    "DESKTOP_FILE_NAME",
    "ICON_NAME",
    "ICON_PNG_SIZES",
    "MACOS_EXTRA_PNG_SIZES",
    "WINDOWS_APP_ID",
    "app_icon_dir",
    "application_icon",
    "apply_application_identity",
    "hicolor_dir",
    "icon_png_sizes",
    "set_shortcut_app_id",
    "set_windows_app_id",
    "shortcut_app_id",
    "windows_icon_path",
]

#: The application name. Qt uses it as the X11 window class, which is why the desktop
#: entry's ``StartupWMClass`` must be this exact string.
APPLICATION_NAME = "Tether"

#: The base name of the Linux desktop entry: ``tether.desktop``, without the suffix, as
#: ``QGuiApplication.desktopFileName`` takes it.
DESKTOP_FILE_NAME = "tether"

#: The icon-theme name the desktop entry's ``Icon=`` line looks up, and the file name of
#: every icon in the hicolor tree.
ICON_NAME = "tether"

#: The explicit Windows AppUserModelID. Company and product, dot-separated, no spaces
#: and no version: a version in the ID would split the taskbar group at every upgrade.
WINDOWS_APP_ID = "MondragonLab.Tether"

#: The square PNG sizes of the application icon on every system, in pixels
#: (``tether-<size>.png``).
ICON_PNG_SIZES = (16, 24, 32, 48, 64, 128, 256)

#: The larger PNG sizes that only macOS gets: the Dock draws the icon at up to 512 px on
#: a Retina display. Elsewhere they only cost memory, because X11 copies every size of
#: the icon into each window's ``_NET_WM_ICON`` property, and the 1024 px one alone is
#: 4 MB there.
MACOS_EXTRA_PNG_SIZES = (512, 1024)


def app_icon_dir() -> Path:
    """The folder of the application icon files inside the installed package."""
    return Path(__file__).resolve().parent / "assets" / "brand" / "app-icon"


def hicolor_dir() -> Path:
    """The freedesktop ``hicolor`` tree of the icon, as ``post_install.sh`` copies it."""
    return app_icon_dir() / "hicolor"


def windows_icon_path() -> Path:
    """The ``.ico`` file that the Windows Start Menu shortcut shows."""
    return app_icon_dir() / "tether.ico"


def icon_png_sizes(platform: str | None = None) -> tuple[int, ...]:
    """The PNG sizes :func:`application_icon` uses on *platform* (default: this one)."""
    platform = sys.platform if platform is None else platform
    if platform == "darwin":
        return ICON_PNG_SIZES + MACOS_EXTRA_PNG_SIZES
    return ICON_PNG_SIZES


def application_icon(platform: str | None = None) -> Any:
    """Build the application ``QIcon`` from the PNG sizes of the dark tile.

    Each file is added without a size, so Qt reads the true size from the image: a file
    that is missing or does not decode adds no size at all, and
    ``QIcon.availableSizes()`` then shows the gap instead of hiding it.
    """
    # PySide6 directly, not pyqtgraph.Qt, which loads NumPy (see the module docstring).
    from PySide6 import QtGui

    icon = QtGui.QIcon()
    folder = app_icon_dir()
    for size in icon_png_sizes(platform):
        icon.addFile(str(folder / f"tether-{size}.png"))
    return icon


def set_windows_app_id(app_id: str = WINDOWS_APP_ID) -> bool:
    """Give this process an explicit Windows AppUserModelID; ``True`` if it took.

    Microsoft requires the call during startup, before the process shows any window, so
    :func:`apply_application_identity` makes it before it touches the application. It
    does nothing on other systems. A failure returns ``False`` and never raises: the
    identity only changes how the taskbar groups and draws the windows, and starting
    Tether must not depend on it.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        setter = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        setter.argtypes = [ctypes.c_wchar_p]
        setter.restype = ctypes.c_long
        return bool(setter(app_id) == 0)  # S_OK
    except Exception:  # noqa: BLE001 - cosmetic; a failure must not stop the start
        return False


def set_shortcut_app_id(shortcut: str | os.PathLike[str], app_id: str = WINDOWS_APP_ID) -> bool:
    """Write *app_id* into a Windows shortcut (``.lnk``); ``True`` if it reads back.

    The property is ``System.AppUserModel.ID``. When a shortcut and a window have the
    same ID, the taskbar shows one button for both, so a pinned Tether and a running
    Tether do not become two buttons. ``WScript.Shell``, which creates the shortcut in
    ``post_install.bat``, cannot write this property, so the installer calls this
    function with the environment's own Python. It does nothing on other systems and,
    like :func:`set_windows_app_id`, returns ``False`` instead of raising.
    """
    if sys.platform != "win32":
        return False
    try:
        _shortcut_property(os.fspath(shortcut), app_id)
    except Exception:  # noqa: BLE001 - the shortcut still works without the ID
        return False
    return shortcut_app_id(shortcut) == app_id


def shortcut_app_id(shortcut: str | os.PathLike[str]) -> str | None:
    """The AppUserModelID a Windows shortcut carries, or ``None`` if it has none.

    Also ``None`` on other systems and on any error, so a check that compares the
    result with :data:`WINDOWS_APP_ID` fails instead of crashing.
    """
    if sys.platform != "win32":
        return None
    try:
        return _shortcut_property(os.fspath(shortcut), None)
    except Exception:  # noqa: BLE001 - a missing or unreadable shortcut has no ID
        return None


def _shortcut_property(path: str, new_value: str | None) -> str | None:
    """Read (``new_value=None``) or write ``System.AppUserModel.ID`` on *path*.

    The shell's property store of the file does the work
    (``SHGetPropertyStoreFromParsingName``, then ``IPropertyStore`` ``GetValue``, or
    ``SetValue`` and ``Commit``). The property key comes from Windows by its canonical
    name (``PSGetPropertyKeyFromName``), so no key is copied into this file. Raises
    ``OSError`` when a call fails; the two public functions above turn that into their
    "no" answer.
    """
    import ctypes
    import uuid
    from ctypes import wintypes

    class _Guid(ctypes.Structure):
        _fields_ = [
            ("data1", wintypes.DWORD),
            ("data2", wintypes.WORD),
            ("data3", wintypes.WORD),
            ("data4", ctypes.c_ubyte * 8),
        ]

    class _PropertyKey(ctypes.Structure):
        _fields_ = [("fmtid", _Guid), ("pid", wintypes.DWORD)]

    class _PropVariant(ctypes.Structure):
        # vt, three reserved WORDs, then the value union (two pointers wide).
        _fields_ = [
            ("vt", wintypes.USHORT),
            ("reserved1", wintypes.WORD),
            ("reserved2", wintypes.WORD),
            ("reserved3", wintypes.WORD),
            ("value", ctypes.c_void_p),
            ("value_high", ctypes.c_void_p),
        ]

    vt_lpwstr = 31
    gps_default, gps_readwrite = 0, 2
    iid_property_store = _Guid.from_buffer_copy(
        uuid.UUID("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99").bytes_le
    )

    def check(hresult: int, what: str) -> None:
        if hresult < 0:
            raise OSError(f"{what} failed: HRESULT 0x{hresult & 0xFFFFFFFF:08X}")

    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = ctypes.c_long
    hresult = ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
    initialised = hresult in (0, 1)  # S_OK, S_FALSE: pair with CoUninitialize
    if not initialised and (hresult & 0xFFFFFFFF) != 0x80010106:  # RPC_E_CHANGED_MODE
        check(hresult, "CoInitializeEx")
    try:
        key = _PropertyKey()
        get_key = ctypes.windll.propsys.PSGetPropertyKeyFromName
        get_key.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(_PropertyKey)]
        get_key.restype = ctypes.c_long
        check(get_key("System.AppUserModel.ID", ctypes.byref(key)), "PSGetPropertyKeyFromName")

        open_store = ctypes.windll.shell32.SHGetPropertyStoreFromParsingName
        open_store.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.POINTER(_Guid),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        open_store.restype = ctypes.c_long
        store = ctypes.c_void_p()
        flags = gps_default if new_value is None else gps_readwrite
        check(
            open_store(path, None, flags, ctypes.byref(iid_property_store), ctypes.byref(store)),
            "SHGetPropertyStoreFromParsingName",
        )
        vtable = ctypes.cast(store, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents

        def method(index: int, *argtypes: Any) -> Any:
            # IPropertyStore after IUnknown's three: GetCount 3, GetAt 4, GetValue 5,
            # SetValue 6, Commit 7.
            prototype = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
            return prototype(vtable[index])

        key_p, variant_p = ctypes.POINTER(_PropertyKey), ctypes.POINTER(_PropVariant)
        try:
            if new_value is not None:
                text = ctypes.create_unicode_buffer(new_value)
                variant = _PropVariant(vt=vt_lpwstr, value=ctypes.addressof(text))
                check(method(6, key_p, variant_p)(store, key, variant), "IPropertyStore.SetValue")
                check(method(7)(store), "IPropertyStore.Commit")
                return new_value
            variant = _PropVariant()
            check(method(5, key_p, variant_p)(store, key, variant), "IPropertyStore.GetValue")
            try:
                if variant.vt != vt_lpwstr or not variant.value:
                    return None
                return ctypes.wstring_at(variant.value)
            finally:
                ole32.PropVariantClear.argtypes = [variant_p]
                ole32.PropVariantClear(variant)
        finally:
            method(2)(store)  # IUnknown.Release
    finally:
        if initialised:
            ole32.CoUninitialize()


def apply_application_identity(app: Any) -> None:
    """Set the Windows identity, the application icon and the Linux desktop file name.

    Call it on the ``QApplication`` before the first window is created. The Windows call
    applies to the whole process and the two Qt calls to the whole application, so a
    second call (a test that reuses one ``QApplication``) changes nothing.
    """
    set_windows_app_id()
    app.setWindowIcon(application_icon())
    app.setDesktopFileName(DESKTOP_FILE_NAME)

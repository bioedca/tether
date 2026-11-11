# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the Tether application icon and identity (issue #673).

Three groups:

* **The running application** (``gui`` marker, headless): ``create_shell`` sets the
  Tether icon with the 16, 32, 48 and 256 px sizes on the ``QApplication`` and so on the
  window, picks the drawing made for each small size, and names ``tether.desktop``.
* **The Windows identity**: the AppUserModelID calls do nothing off Windows, never
  raise, and on the Windows leg of the matrix really set the ID on the process and
  write it into a shortcut, where Windows' own shell reads it back.
* **The installer files**: ``post_install.sh`` run against a stub prefix copies the
  icon files and writes the ``Icon=`` and ``StartupWMClass`` lines; the paths in
  ``post_install.bat``, ``install_smoke.sh`` and the uninstall section of
  ``docs/install.md`` agree with the files the package ships.

The installer scripts run for real only in the packaging install smoke, which checks the
Windows shortcut's icon and the Linux icon files on real installs.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from tether.gui import identity

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
_needs_qt = pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed")

_REPO = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO / "packaging" / "scripts"
POST_INSTALL_SH = _SCRIPTS / "post_install.sh"
POST_INSTALL_BAT = _SCRIPTS / "post_install.bat"
INSTALL_SMOKE = _SCRIPTS / "install_smoke.sh"
INSTALL_DOC = _REPO / "docs" / "install.md"

#: The sizes the issue names for the application icon.
_REQUIRED_SIZES = (16, 32, 48, 256)

#: The hicolor size folders the brand kit ships (#671), one PNG each.
_HICOLOR_SIZES = (16, 24, 32, 48, 64, 128, 256, 512)


def _hicolor_files() -> set[str]:
    """The icon files of the package's hicolor tree, as paths relative to the tree."""
    root = identity.hicolor_dir()
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def _expected_hicolor_files() -> set[str]:
    return {f"{n}x{n}/apps/{identity.ICON_NAME}.png" for n in _HICOLOR_SIZES} | {
        f"scalable/apps/{identity.ICON_NAME}.svg"
    }


def _png_size(path: Path) -> tuple[int, int]:
    """Width and height from a PNG's IHDR chunk (no image library needed)."""
    head = path.read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"
    return struct.unpack(">II", head[16:24])


def _ico_sizes(path: Path) -> set[int]:
    """The square sizes in a Windows ``.ico`` directory (a width byte of 0 means 256)."""
    data = path.read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind) == (0, 1), f"{path} is not an .ico file"
    sizes = set()
    for i in range(count):
        width, height = data[6 + 16 * i], data[7 + 16 * i]
        assert width == height, f"{path} entry {i} is not square"
        sizes.add(width or 256)
    return sizes


# --- the shipped files (no Qt) ------------------------------------------------


def test_every_icon_png_is_present_at_its_named_size() -> None:
    """Each ``tether-<n>.png`` that :func:`application_icon` loads is an n x n PNG."""
    for size in identity.ICON_PNG_SIZES + identity.MACOS_EXTRA_PNG_SIZES:
        path = identity.app_icon_dir() / f"tether-{size}.png"
        assert path.is_file(), f"missing {path}"
        assert _png_size(path) == (size, size), path


def test_icon_sizes_stop_at_256_px_except_on_macos() -> None:
    """X11 copies every size into each window, so only macOS gets 512 and 1024 px."""
    assert set(_REQUIRED_SIZES) <= set(identity.icon_png_sizes("linux"))
    assert max(identity.icon_png_sizes("linux")) == 256
    assert max(identity.icon_png_sizes("win32")) == 256
    assert {512, 1024} <= set(identity.icon_png_sizes("darwin"))


def test_windows_icon_file_has_the_shortcut_sizes() -> None:
    """``tether.ico`` holds the sizes Start and Explorer draw a shortcut at."""
    assert {16, 32, 48, 256} <= _ico_sizes(identity.windows_icon_path())


def test_hicolor_tree_is_eight_pngs_and_one_svg() -> None:
    """The tree ``post_install.sh`` copies is the set the docs and the smoke name."""
    assert _hicolor_files() == _expected_hicolor_files()
    for n in _HICOLOR_SIZES:
        png = identity.hicolor_dir() / f"{n}x{n}" / "apps" / f"{identity.ICON_NAME}.png"
        assert _png_size(png) == (n, n), png


def test_windows_app_id_has_the_microsoft_form() -> None:
    """Dot-separated company and product, no spaces, no version, within 128 characters."""
    app_id = identity.WINDOWS_APP_ID
    assert len(app_id) <= 128
    assert " " not in app_id
    assert re.fullmatch(r"[A-Za-z]+(\.[A-Za-z]+)+", app_id), app_id


# --- the Windows identity -----------------------------------------------------


def test_windows_app_id_does_nothing_on_other_systems(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert identity.set_windows_app_id() is False


def test_windows_app_id_failure_never_raises(monkeypatch) -> None:
    """A Windows without the shell call (or any error in it) must not stop the start."""
    import ctypes

    class _NoShell32:
        def __getattr__(self, name: str) -> object:
            raise OSError(f"no {name} here")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "windll", _NoShell32(), raising=False)
    assert identity.set_windows_app_id() is False


@pytest.mark.skipif(sys.platform != "win32", reason="the AppUserModelID is a Windows call")
def test_windows_app_id_is_set_on_the_process() -> None:
    """On the Windows leg the ID really takes, read back with the matching getter."""
    import ctypes

    assert identity.set_windows_app_id() is True
    value = ctypes.c_wchar_p()
    getter = ctypes.windll.shell32.GetCurrentProcessExplicitAppUserModelID
    getter.argtypes = [ctypes.POINTER(ctypes.c_wchar_p)]
    getter.restype = ctypes.c_long
    assert getter(ctypes.byref(value)) == 0
    try:
        assert value.value == identity.WINDOWS_APP_ID
    finally:
        ctypes.windll.ole32.CoTaskMemFree(value)


def test_shortcut_app_id_does_nothing_on_other_systems(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert identity.set_shortcut_app_id(tmp_path / "Tether.lnk") is False
    assert identity.shortcut_app_id(tmp_path / "Tether.lnk") is None


def test_shortcut_app_id_failure_never_raises(monkeypatch, tmp_path) -> None:
    """Any error in the shell calls is a "no", so the installer only warns."""
    import ctypes

    class _NoDlls:
        def __getattr__(self, name: str) -> object:
            raise OSError(f"no {name} here")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "windll", _NoDlls(), raising=False)
    assert identity.set_shortcut_app_id(tmp_path / "Tether.lnk") is False
    assert identity.shortcut_app_id(tmp_path / "Tether.lnk") is None


def _powershell(script: str, **env: str) -> str:
    """Run *script* in Windows PowerShell; values reach it through the environment."""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


@pytest.fixture
def windows_shortcut(tmp_path) -> Path:
    """A shortcut made the way ``post_install.bat`` makes it, in a folder whose name has
    a space and an apostrophe."""
    lnk = tmp_path / "O'Brien folder" / "Tether.lnk"
    lnk.parent.mkdir()
    _powershell(
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:TEST_LNK);"
        "$s.TargetPath=$env:ComSpec; $s.Save()",
        TEST_LNK=str(lnk),
    )
    assert lnk.is_file()
    return lnk


@pytest.mark.skipif(sys.platform != "win32", reason="shortcuts are a Windows feature")
def test_shortcut_carries_the_app_id(windows_shortcut) -> None:
    """A new shortcut has no ID; after the call it has Tether's, read back."""
    assert identity.shortcut_app_id(windows_shortcut) is None
    assert identity.set_shortcut_app_id(windows_shortcut) is True
    assert identity.shortcut_app_id(windows_shortcut) == identity.WINDOWS_APP_ID


@pytest.mark.skipif(sys.platform != "win32", reason="shortcuts are a Windows feature")
def test_windows_shell_reads_the_shortcut_app_id(windows_shortcut) -> None:
    """Windows' own shell, not this module, finds the ID under ``System.AppUserModel.ID``.

    The module and this check name the property the same way, but they reach it by
    different routes (the property store against ``Shell.Application``), so a write
    under any other key fails here.
    """
    assert identity.set_shortcut_app_id(windows_shortcut) is True
    value = _powershell(
        "$f=(New-Object -ComObject Shell.Application).NameSpace("
        "(Split-Path -Parent $env:TEST_LNK)).ParseName((Split-Path -Leaf $env:TEST_LNK));"
        "$f.ExtendedProperty('System.AppUserModel.ID')",
        TEST_LNK=str(windows_shortcut),
    )
    assert value == identity.WINDOWS_APP_ID


@pytest.mark.skipif(sys.platform != "win32", reason="shortcuts are a Windows feature")
def test_shortcut_app_id_on_a_missing_file_is_a_no(tmp_path) -> None:
    missing = tmp_path / "missing.lnk"
    assert identity.set_shortcut_app_id(missing) is False
    assert identity.shortcut_app_id(missing) is None


# --- the running application (Qt) ---------------------------------------------


@pytest.mark.gui
@_needs_qt
def test_application_icon_has_the_required_sizes(qtbot) -> None:
    """``create_shell`` sets an icon with real 16, 32, 48 and 256 px images on the app.

    ``QIcon.isNull()`` alone proves nothing: a file name makes an icon non-null even when
    the file is missing. The sizes come from the decoded images, so they are the check.
    """
    from tether.gui.app import create_shell

    app, shell = create_shell([])
    qtbot.addWidget(shell._window)
    icon = app.windowIcon()
    assert not icon.isNull()
    sizes = {(s.width(), s.height()) for s in icon.availableSizes()}
    for n in _REQUIRED_SIZES:
        assert (n, n) in sizes, f"no {n} px image in the application icon: {sorted(sizes)}"
        pixmap = icon.pixmap(n, n)
        assert not pixmap.isNull() and (pixmap.width(), pixmap.height()) == (n, n)
    shell.close()


@pytest.mark.gui
@_needs_qt
def test_window_shows_the_application_icon(qtbot) -> None:
    """The shell's window carries the icon, so its title bar and task switcher show it."""
    from tether.gui.app import create_shell

    app, shell = create_shell([])
    qtbot.addWidget(shell._window)
    window_sizes = {s.width() for s in shell._window.windowIcon().availableSizes()}
    app_sizes = {s.width() for s in app.windowIcon().availableSizes()}
    assert window_sizes == app_sizes
    assert set(_REQUIRED_SIZES) <= window_sizes
    shell.close()


@pytest.mark.gui
@_needs_qt
def test_small_sizes_use_the_drawing_made_for_them(qtbot) -> None:
    """At 16 and 32 px the icon is the tiny and the small mark, not a shrunken tile.

    Both sides go through the same ``QPixmap`` load and are compared premultiplied. A
    pixmap stores premultiplied alpha, and converting it back to straight alpha is lossy
    and depends on the CPU (Qt's SSE4 path uses an approximate reciprocal): the first
    version compared against the PNG in straight alpha and failed at 32 px on the Ubuntu
    runner while it passed here. The shrunken 256 px tile is the negative control, so
    the check can still tell the two drawings apart.
    """
    from pyqtgraph.Qt import QtCore, QtGui

    premultiplied = QtGui.QImage.Format.Format_ARGB32_Premultiplied

    def _image(pixmap: QtGui.QPixmap) -> QtGui.QImage:
        return pixmap.toImage().convertToFormat(premultiplied)

    icon = identity.application_icon()
    large = QtGui.QPixmap(str(identity.app_icon_dir() / "tether-256.png"))
    for n in (16, 32):
        drawn = _image(icon.pixmap(n, n))
        source = _image(QtGui.QPixmap(str(identity.app_icon_dir() / f"tether-{n}.png")))
        shrunk = _image(
            large.scaled(
                n,
                n,
                QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            )
        )
        assert drawn == source, f"the {n} px icon is not tether-{n}.png"
        assert drawn != shrunk, f"tether-{n}.png cannot be told from the shrunken tile"


@pytest.mark.gui
@_needs_qt
def test_desktop_identity_matches_the_menu_entry(qtbot) -> None:
    """The window names ``tether.desktop``, and its class is the entry's StartupWMClass."""
    from tether.gui.app import create_shell

    app, shell = create_shell([])
    qtbot.addWidget(shell._window)
    assert app.desktopFileName() == identity.DESKTOP_FILE_NAME
    assert app.applicationName() == identity.APPLICATION_NAME
    shell.close()


# --- the installer files ------------------------------------------------------


def _stub_prefix(tmp_path: Path) -> Path:
    """A prefix whose two env interpreters skip pip and run the test's own Python.

    ``post_install.sh`` installs the three wheels first; the stub turns those ``-m pip``
    calls into no-ops, so the script reaches its menu-entry block on any machine. The
    ``-c`` call that asks the package for its hicolor tree runs the real interpreter, so
    the copy reads the same files an install would.
    """
    prefix = tmp_path / "prefix with space"
    for env in ("tether", "sidecar"):
        bin_d = prefix / "envs" / env / "bin"
        bin_d.mkdir(parents=True)
        stub = bin_d / "python"
        stub.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then exit 0; fi\n'
            f'exec "{sys.executable}" "$@"\n',
            encoding="utf-8",
        )
        stub.chmod(0o755)
    (prefix / "wheelhouse").mkdir()
    return prefix


def _run_post_install(prefix: Path, home: Path) -> None:
    env = {**os.environ, "PREFIX": str(prefix), "HOME": str(home)}
    subprocess.run(["sh", str(POST_INSTALL_SH)], check=True, env=env, capture_output=True)


@pytest.mark.skipif(
    not sys.platform.startswith("linux") or shutil.which("sh") is None,
    reason="the icon step of post_install.sh runs on Linux only",
)
def test_linux_post_install_installs_the_icon_and_names_it(tmp_path) -> None:
    """The Linux step copies the nine icon files and writes the two lines that use them.

    Run twice, the way a reinstall over an old install runs it: the second run must
    succeed and leave the same files.
    """
    prefix = _stub_prefix(tmp_path)
    home = tmp_path / "home"
    icons_d = home / ".local" / "share" / "icons" / "hicolor"
    for _ in range(2):
        _run_post_install(prefix, home)
        installed = {p.relative_to(icons_d).as_posix() for p in icons_d.rglob("*") if p.is_file()}
        assert installed == _expected_hicolor_files()
        for rel in installed:
            assert (icons_d / rel).read_bytes() == (identity.hicolor_dir() / rel).read_bytes()

    entry = (home / ".local" / "share" / "applications" / "tether.desktop").read_text()
    lines = entry.splitlines()
    assert f"Icon={identity.ICON_NAME}" in lines
    assert f"StartupWMClass={identity.APPLICATION_NAME}" in lines
    assert f"{identity.DESKTOP_FILE_NAME}.desktop" == "tether.desktop"


@pytest.mark.skipif(
    not sys.platform.startswith("linux") or shutil.which("sh") is None,
    reason="the icon step of post_install.sh runs on Linux only",
)
def test_linux_post_install_makes_a_stale_icon_cache_invalid(tmp_path) -> None:
    """The theme folder ends up newer than an old cache, so GTK and Qt stop using it."""
    prefix = _stub_prefix(tmp_path)
    home = tmp_path / "home"
    icons_d = home / ".local" / "share" / "icons" / "hicolor"
    (icons_d / "48x48" / "apps").mkdir(parents=True)
    cache = icons_d / "icon-theme.cache"
    cache.write_bytes(b"")
    old = 1_000_000_000
    os.utime(cache, (old, old))
    os.utime(icons_d, (old, old))
    _run_post_install(prefix, home)
    assert icons_d.stat().st_mtime > old


def test_windows_shortcut_icon_path_is_the_shipped_ico() -> None:
    """``post_install.bat`` and the smoke name the ``.ico`` the wheel really installs.

    The batch script cannot ask Python for the path, so it spells it out. This binds that
    spelling to the file: the part after ``site-packages`` is the package's own path to
    ``tether.ico``, and the install smoke expects the same tail.
    """
    import tether

    package_rel = identity.windows_icon_path().relative_to(
        Path(tether.__file__).resolve().parents[1]
    )
    tail = "\\".join(("envs", "tether", "Lib", "site-packages", *package_rel.parts))
    bat = POST_INSTALL_BAT.read_text(encoding="utf-8")
    assert re.search(r'(?m)^set "TETHER_ICO=%PREFIX%\\' + re.escape(tail) + '"$', bat), (
        f"post_install.bat must set TETHER_ICO to %PREFIX%\\{tail}"
    )
    assert "\"$s.IconLocation=$env:TETHER_ICO + ',0';\"" in bat
    smoke = INSTALL_SMOKE.read_text(encoding="utf-8")
    assert f"*'\\{tail},0')" in smoke, "install_smoke.sh must expect the same .ico path"


def test_installer_writes_and_smoke_reads_the_shortcut_app_id() -> None:
    """``post_install.bat`` writes the ID after it saves the shortcut; the smoke reads it.

    The shortcut's path goes to Python as an argument, never inside the source, and the
    smoke compares with the same ID the running window sets.
    """
    bat = POST_INSTALL_BAT.read_text(encoding="utf-8")
    write = (
        '"%TETHER_PY%" -c "import sys; from tether.gui.identity import set_shortcut_app_id; '
        'sys.exit(0 if set_shortcut_app_id(sys.argv[1]) else 1)" "%TETHER_LNK%"'
    )
    assert write in bat
    assert bat.index('"$s.Save()"') < bat.index(write), "write the ID after the shortcut"
    assert bat.index(write) < bat.index('del "%WHEELHOUSE%'), "and before the script ends"
    smoke = INSTALL_SMOKE.read_text(encoding="utf-8")
    assert "from tether.gui.identity import shortcut_app_id" in smoke
    assert f'test "$lnk_app_id" = "{identity.WINDOWS_APP_ID}"' in smoke


def test_install_smoke_and_uninstall_name_every_linux_icon_file() -> None:
    """The smoke checks, and the uninstall removes, exactly the files the step adds."""
    sizes = ",".join(f"{n}x{n}" for n in _HICOLOR_SIZES)
    doc = INSTALL_DOC.read_text(encoding="utf-8")
    uninstall = doc.split("## Uninstall", 1)[1]
    assert (
        f"rm -f ~/.local/share/icons/hicolor/{{{sizes}}}/apps/{identity.ICON_NAME}.png" in uninstall
    )
    assert f"rm -f ~/.local/share/icons/hicolor/scalable/apps/{identity.ICON_NAME}.svg" in uninstall

    smoke = INSTALL_SMOKE.read_text(encoding="utf-8")
    loop = re.search(r"(?m)^\s*for rel in ([^;]+); do$", smoke)
    assert loop, "install_smoke.sh must loop over the hicolor size folders"
    assert loop.group(1).split() == [f"{n}x{n}" for n in _HICOLOR_SIZES]
    assert "scalable/apps/tether.svg" in smoke
    assert "grep -qx 'Icon=tether'" in smoke
    assert "grep -qx 'StartupWMClass=Tether'" in smoke

# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The ``.tether`` file type on each desktop (issue #664, ADR-0118).

A double-click on a project must start Tether with that project open. These tests cover:

* the Windows registration through an in-memory registry, so they run on every system:
  the keys ``install`` writes, that an extension another program owns is left to it, and
  that ``remove`` takes away exactly what ``install`` wrote and only for its own install;
* the Linux MIME package that ``post_install.sh`` writes, run against a stub prefix, and,
  where the machine has ``update-mime-database`` and ``gio``, what the desktop then
  reads for a ``.tether`` file;
* the macOS document type in ``Tether.app``'s ``Info.plist``;
* the install scripts and the install smoke, which run the same module.

The GUI half (the file-open event on macOS, the read-only banner) is in
``tests/test_gui_app.py``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from tether.gui import file_type, macos_app
from tether.gui.file_type import (
    EXTENSION,
    MACHINE,
    MIME_TYPE,
    PROG_ID,
    TYPE_NAME,
    USER,
    MemoryRegistry,
)

_SCRIPTS = Path(__file__).resolve().parents[1] / "packaging" / "scripts"
POST_INSTALL_SH = _SCRIPTS / "post_install.sh"
POST_INSTALL_BAT = _SCRIPTS / "post_install.bat"
PRE_UNINSTALL_BAT = _SCRIPTS / "pre_uninstall.bat"
INSTALL_SMOKE = _SCRIPTS / "install_smoke.sh"

PREFIX = r"C:\Users\Zoë Ana\Tether"
OTHER_PREFIX = r"D:\Apps\Tether"
CLASSES = "Software\\Classes"


def _snapshot(registry: MemoryRegistry) -> dict[tuple[str, str], dict[str, str]]:
    return {key: dict(values) for key, values in registry.keys.items()}


def _icon(tmp_path: Path) -> Path:
    icon = tmp_path / "tether-document.ico"
    icon.write_bytes(b"ico")
    return icon


# --- Windows -----------------------------------------------------------------


def test_document_icon_ships_in_the_package() -> None:
    """The icon the registry names is in the installed package, not only in the repository."""
    icon = file_type.document_icon_path()
    assert icon.is_file()
    assert icon.read_bytes()[:4] == b"\x00\x00\x01\x00", "an .ico file starts 00 00 01 00"


def test_open_command_quotes_the_program_and_the_file() -> None:
    """A path with spaces or non-ASCII letters reaches ``tether-gui`` as one argument."""
    command = file_type.open_command(PREFIX)
    assert command == r'"C:\Users\Zoë Ana\Tether\envs\tether\Scripts\tether-gui.exe" "%1"'


@pytest.mark.parametrize(("just_me", "expected"), [(True, USER), (False, MACHINE)])
def test_scope_follows_the_constructor_install_mode(tmp_path, just_me, expected) -> None:
    """A "Just Me" install has ``.nonadmin`` in its folder and registers for the user."""
    if just_me:
        (tmp_path / ".nonadmin").write_bytes(b"")
    assert file_type.install_scope(tmp_path) == expected


@pytest.mark.parametrize("root", [USER, MACHINE])
def test_install_writes_the_program_identifier_and_owns_a_free_extension(tmp_path, root) -> None:
    """A fresh registry ends with the type name, icon, open command and the extension."""
    registry = MemoryRegistry()
    icon = _icon(tmp_path)
    assert file_type.register_windows(registry, root, PREFIX, icon=icon) is True
    prog = f"{CLASSES}\\{PROG_ID}"
    assert registry.get(root, prog, "") == TYPE_NAME
    assert registry.get(root, f"{prog}\\DefaultIcon", "") == f'"{icon}",0'
    assert registry.get(root, f"{prog}\\shell\\open\\command", "") == file_type.open_command(PREFIX)
    assert registry.get(root, f"{CLASSES}\\{EXTENSION}", "") == PROG_ID
    assert registry.get(root, f"{CLASSES}\\{EXTENSION}\\OpenWithProgids", PROG_ID) == ""
    assert file_type.check_windows(registry, root, PREFIX) == []
    other_root = MACHINE if root == USER else USER
    assert not any(key[0] == other_root for key in registry.keys)


def test_an_extension_another_program_owns_is_left_to_it(tmp_path) -> None:
    """Tether joins the "Open with" list and does not change the double-click."""
    registry = MemoryRegistry()
    ext = f"{CLASSES}\\{EXTENSION}"
    registry.set(USER, ext, "", "Other.Document")
    registry.set(USER, f"{ext}\\OpenWithProgids", "Other.Document", "")
    assert file_type.register_windows(registry, USER, PREFIX, icon=_icon(tmp_path)) is False
    assert registry.get(USER, ext, "") == "Other.Document"
    assert registry.get(USER, f"{ext}\\OpenWithProgids", "Other.Document") == ""
    assert registry.get(USER, f"{ext}\\OpenWithProgids", PROG_ID) == ""
    assert file_type.check_windows(registry, USER, PREFIX, owner=False) == []
    assert file_type.check_windows(registry, USER, PREFIX) == [
        f"{USER}\\{ext} does not name {PROG_ID}"
    ]


def test_a_per_user_install_leaves_an_all_users_owner(tmp_path) -> None:
    """Windows prefers the current user's default, so a per-user one would take it over."""
    registry = MemoryRegistry()
    ext = f"{CLASSES}\\{EXTENSION}"
    registry.set(MACHINE, ext, "", "Other.Document")
    registry.set(USER, f"{CLASSES}\\.txt", "", "txtfile")  # the user's Classes key exists
    before = _snapshot(registry)
    assert file_type.register_windows(registry, USER, PREFIX, icon=_icon(tmp_path)) is False
    assert registry.get(USER, ext, "") is None
    assert registry.get(USER, f"{ext}\\OpenWithProgids", PROG_ID) == ""
    assert registry.get(MACHINE, ext, "") == "Other.Document"
    assert file_type.unregister_windows(registry, USER, PREFIX) is True
    assert _snapshot(registry) == before


def test_an_all_users_install_serves_other_accounts_and_leaves_a_per_user_owner(
    tmp_path,
) -> None:
    """The computer's default is set for every account; the installer's own choice stays.

    Another account, with no per-user ``.tether`` key, reads only the computer's default,
    so it opens projects with Tether; the installing account keeps its other program.
    """
    registry = MemoryRegistry()
    ext = f"{CLASSES}\\{EXTENSION}"
    registry.set(USER, ext, "", "Other.Document")
    registry.set(MACHINE, f"{CLASSES}\\.txt", "", "txtfile")  # the computer's Classes key exists
    before = _snapshot(registry)
    assert file_type.register_windows(registry, MACHINE, PREFIX, icon=_icon(tmp_path)) is False
    assert registry.get(MACHINE, ext, "") == PROG_ID
    assert registry.get(USER, ext, "") == "Other.Document"
    assert file_type.check_windows(registry, MACHINE, PREFIX) == []
    assert file_type.unregister_windows(registry, MACHINE, PREFIX) is True
    assert _snapshot(registry) == before


def test_an_empty_extension_default_is_taken(tmp_path) -> None:
    """A ``.tether`` key with an empty default names no program, so Tether may own it."""
    registry = MemoryRegistry()
    registry.set(USER, f"{CLASSES}\\{EXTENSION}", "", "")
    assert file_type.register_windows(registry, USER, PREFIX, icon=_icon(tmp_path)) is True
    assert registry.get(USER, f"{CLASSES}\\{EXTENSION}", "") == PROG_ID


@pytest.mark.parametrize("owned_before", [False, True])
def test_remove_takes_away_exactly_what_install_wrote(tmp_path, owned_before) -> None:
    """After install and remove the registry is as it was, another program's keys included."""
    registry = MemoryRegistry()
    registry.set(USER, f"{CLASSES}\\.txt", "", "txtfile")
    if owned_before:
        registry.set(USER, f"{CLASSES}\\{EXTENSION}", "", "Other.Document")
        registry.set(USER, f"{CLASSES}\\{EXTENSION}", "Content Type", "application/x-other")
        registry.set(USER, f"{CLASSES}\\{EXTENSION}\\OpenWithProgids", "Other.Document", "")
    before = _snapshot(registry)
    file_type.register_windows(registry, USER, PREFIX, icon=_icon(tmp_path))
    assert _snapshot(registry) != before
    assert file_type.unregister_windows(registry, USER, PREFIX) is True
    assert _snapshot(registry) == before


def test_remove_leaves_the_registration_of_another_install(tmp_path) -> None:
    """The uninstall of one Tether install never removes the keys that open another."""
    registry = MemoryRegistry()
    file_type.register_windows(registry, USER, OTHER_PREFIX, icon=_icon(tmp_path))
    before = _snapshot(registry)
    assert file_type.unregister_windows(registry, USER, PREFIX) is False
    assert _snapshot(registry) == before


def test_the_newest_install_opens_the_files(tmp_path) -> None:
    """A second install replaces the open command; removing the first one changes nothing."""
    registry = MemoryRegistry()
    file_type.register_windows(registry, USER, OTHER_PREFIX, icon=_icon(tmp_path))
    file_type.register_windows(registry, USER, PREFIX, icon=_icon(tmp_path))
    assert file_type.check_windows(registry, USER, PREFIX) == []
    assert file_type.unregister_windows(registry, USER, OTHER_PREFIX) is False
    assert file_type.check_windows(registry, USER, PREFIX) == []


def test_command_comparison_ignores_case(tmp_path) -> None:
    """Windows paths ignore case, so another spelling of the same folder is the same install."""
    registry = MemoryRegistry()
    file_type.register_windows(registry, USER, PREFIX, icon=_icon(tmp_path))
    assert file_type.check_windows(registry, USER, PREFIX.upper()) == []
    assert file_type.unregister_windows(registry, USER, PREFIX.lower()) is True


def test_check_names_each_problem(tmp_path) -> None:
    """An empty registry fails every check, each with its own message."""
    problems = file_type.check_windows(MemoryRegistry(), USER, PREFIX)
    assert len(problems) == 5
    assert any("does not name the file type" in p for p in problems)
    assert any("shell\\open\\command" in p for p in problems)
    assert any("icon is missing" in p for p in problems)
    assert any("OpenWithProgids" in p for p in problems)


def test_check_finds_a_missing_icon_file(tmp_path) -> None:
    """The icon value must name a file that exists."""
    registry = MemoryRegistry()
    missing = tmp_path / "gone.ico"
    file_type.register_windows(registry, USER, PREFIX, icon=missing)
    assert file_type.check_windows(registry, USER, PREFIX) == [
        f"the file type icon is missing: {f'{chr(34)}{missing}{chr(34)},0'!r}"
    ]


def test_cli_installs_checks_and_removes(tmp_path, capsys) -> None:
    """The three commands the installer, the smoke and the uninstaller run, in order."""
    registry = MemoryRegistry()
    prefix = tmp_path / "Tether"
    prefix.mkdir()
    (prefix / ".nonadmin").write_bytes(b"")
    assert file_type.main(["install", "--prefix", str(prefix)], registry=registry) == 0
    assert "opens with Tether (HKCU)" in capsys.readouterr().out
    assert file_type.main(["check", "--prefix", str(prefix)], registry=registry) == 0
    assert file_type.main(["remove", "--prefix", str(prefix)], registry=registry) == 0
    assert "removed the .tether registration from HKCU" in capsys.readouterr().out
    assert [key for key in registry.keys if "tether" in key[1]] == []
    assert file_type.main(["check", "--prefix", str(prefix)], registry=registry) == 1
    assert "does not name the file type" in capsys.readouterr().err


def test_cli_install_warns_when_another_program_owns_the_extension(tmp_path, capsys) -> None:
    """The install still succeeds, and the warning says what Tether did instead."""
    registry = MemoryRegistry()
    registry.set(MACHINE, f"{CLASSES}\\{EXTENSION}", "", "Other.Document")
    assert file_type.main(["install", "--prefix", str(tmp_path)], registry=registry) == 0
    assert "added to 'Open with' only" in capsys.readouterr().err


def test_cli_never_fails_the_installer_or_the_uninstaller(tmp_path, capsys) -> None:
    """A registry that refuses a write is a warning and exit 0, for install and remove."""

    class Refusing(MemoryRegistry):
        def set(self, root: str, key: str, name: str, value: str) -> None:
            raise PermissionError("access is denied")

        def get(self, root: str, key: str, name: str) -> str | None:
            raise PermissionError("access is denied")

    for command in ("install", "remove"):
        assert file_type.main([command, "--prefix", str(tmp_path)], registry=Refusing()) == 0
        assert "access is denied" in capsys.readouterr().err


def test_cli_without_a_registry_does_nothing_off_windows(tmp_path, monkeypatch, capsys) -> None:
    """On Linux, install and remove are no-ops; ``check`` cannot pass."""
    monkeypatch.setattr(sys, "platform", "linux")
    assert file_type.main(["install", "--prefix", str(tmp_path)]) == 0
    assert file_type.main(["remove", "--prefix", str(tmp_path)]) == 0
    assert file_type.main(["check", "--prefix", str(tmp_path)]) == 1
    assert "nothing to check" in capsys.readouterr().err


def test_post_install_bat_registers_and_pre_uninstall_bat_removes() -> None:
    """Both Windows scripts run the module from the installed env, the prefix as an argument."""
    install = POST_INSTALL_BAT.read_text(encoding="utf-8")
    assert '"%TETHER_PY%" -m tether.gui.file_type install --prefix "%PREFIX%"' in install
    assert install.index("tether.gui.file_type install") < install.index('del "%WHEELHOUSE%')
    remove = PRE_UNINSTALL_BAT.read_text(encoding="utf-8")
    assert 'set "TETHER_PY=%PREFIX%\\envs\\tether\\python.exe"' in remove
    assert (
        'if exist "%TETHER_PY%" "%TETHER_PY%" -m tether.gui.file_type remove --prefix "%PREFIX%"'
        in remove
    )
    assert remove.rstrip().endswith("exit /b 0"), "the uninstall step may never fail"


def test_windows_install_smoke_checks_the_registration_and_its_removal() -> None:
    """The smoke reads the keys with the module and with cmd, then after the uninstall."""
    smoke = INSTALL_SMOKE.read_text(encoding="utf-8")
    windows = smoke.split('if [ "$RUNNER_OS" = "Windows" ]; then', 1)[1].split("\nelse\n", 1)[0]
    assert '-m tether.gui.file_type check --prefix "$prefix"' in windows
    assert 'test "$ext_assoc" = ".tether=Tether.Project"' in windows
    assert "'Tether.Project=\"'*'\\envs\\tether\\Scripts\\tether-gui.exe\" \"%1\"'" in windows
    uninstall = windows.index("Uninstall-Tether.exe")
    assert windows.index("tether.gui.file_type check") < uninstall
    assert windows.index("uninstall left the .tether extension behind") > uninstall
    assert windows.index("uninstall left Tether.Project behind") > uninstall


def test_nothing_changes_path() -> None:
    """The registration reads and writes no environment variable, ``PATH`` included (ADR-0051).

    ``PATH`` is the per-user ``Environment`` key of the registry or the process
    environment; the module touches neither.
    """
    source = Path(file_type.__file__).read_text(encoding="utf-8")
    assert "environ" not in source
    assert "Environment" not in source
    assert "setx" not in source


# --- Linux -------------------------------------------------------------------


def test_mime_package_defines_the_type_the_desktop_entry_names() -> None:
    """One type, glob ``*.tether``, an HDF5 subclass, and the entry lists that type."""
    ns = {"m": "http://www.freedesktop.org/standards/shared-mime-info"}
    root = ET.fromstring(file_type.mime_package_xml())
    types = root.findall("m:mime-type", ns)
    assert [t.get("type") for t in types] == [MIME_TYPE]
    (mime,) = types
    assert [g.get("pattern") for g in mime.findall("m:glob", ns)] == [f"*{EXTENSION}"]
    assert [s.get("type") for s in mime.findall("m:sub-class-of", ns)] == ["application/x-hdf"]
    assert mime.findtext("m:comment", namespaces=ns) == TYPE_NAME
    entry = POST_INSTALL_SH.read_text(encoding="utf-8")
    assert f"MimeType={MIME_TYPE};\n" in entry
    assert 'Exec="$PREFIX/envs/tether/bin/tether-gui" %f\n' in entry


def _stub_prefix(tmp_path: Path) -> Path:
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
    # A desktop hides an entry whose program does not exist, so the entry point is there.
    gui = prefix / "envs" / "tether" / "bin" / "tether-gui"
    gui.write_text("#!/bin/sh\n", encoding="utf-8")
    gui.chmod(0o755)
    (prefix / "wheelhouse").mkdir()
    gh = prefix / "verifier" / "gh" / "gh"
    gh.parent.mkdir(parents=True)
    gh.write_bytes(b"")
    return prefix


@pytest.mark.skipif(
    not sys.platform.startswith("linux") or shutil.which("sh") is None,
    reason="the MIME step of post_install.sh runs on Linux only",
)
def test_linux_post_install_adds_the_mime_type(tmp_path) -> None:
    """The step writes the package into the user's MIME folder, also on a reinstall.

    Where the machine has ``update-mime-database``, the database it builds holds the type,
    and where it also has ``gio``, a ``.tether`` file with a space and a non-ASCII letter in
    its name has that type, which is what a file manager reads for a double-click.
    """
    prefix = _stub_prefix(tmp_path)
    home = tmp_path / "home"
    env = {**os.environ, "PREFIX": str(prefix), "HOME": str(home)}
    env.pop("XDG_DATA_HOME", None)
    for _ in range(2):
        subprocess.run(["sh", str(POST_INSTALL_SH)], check=True, env=env, capture_output=True)
    mime_d = home / ".local" / "share" / "mime"
    assert (mime_d / "packages" / "tether.xml").read_text(encoding="utf-8") == (
        file_type.mime_package_xml()
    )
    if shutil.which("update-mime-database") is None:
        pytest.skip("no update-mime-database here; the install smoke checks the database")
    assert (mime_d / "application" / "x-tether.xml").is_file()
    # The type inherits from the HDF type of the system database, so the programs that
    # handle HDF files are offered for a project too.
    subclasses = (mime_d / "subclasses").read_text(encoding="utf-8").splitlines()
    assert f"{MIME_TYPE} {file_type.HDF_MIME_TYPE}" in subclasses
    system_hdf = (
        Path("/usr/share/mime/application") / f"{file_type.HDF_MIME_TYPE.split('/')[1]}.xml"
    )
    if Path("/usr/share/mime/packages/freedesktop.org.xml").is_file():
        assert system_hdf.is_file(), f"shared-mime-info defines no {file_type.HDF_MIME_TYPE}"
    if shutil.which("gio") is None:
        pytest.skip("no gio here; the install smoke checks the content type")
    doc = tmp_path / "my project é.tether"
    doc.write_bytes(b"\x89HDF\r\n\x1a\n")
    gio_env = {**env, "XDG_DATA_DIRS": "/usr/share"}
    info = subprocess.run(
        ["gio", "info", "-a", "standard::content-type", str(doc)],
        check=True,
        env=gio_env,
        capture_output=True,
        text=True,
    ).stdout
    assert f"standard::content-type: {MIME_TYPE}" in info


def test_linux_install_smoke_checks_the_mime_type() -> None:
    """The Linux leg of the smoke checks the entry, the package and the desktop lookup."""
    smoke = INSTALL_SMOKE.read_text(encoding="utf-8")
    linux = smoke.split('if [ "$RUNNER_OS" = "Linux" ]; then', 1)[1]
    assert "grep -qx 'MimeType=application/x-tether;' \"$desktop\"" in linux
    assert 'mime_pkg="$HOME/.local/share/mime/packages/tether.xml"' in linux
    assert "mime_package_xml()" in linux
    assert 'test "$doc_type" = "application/x-tether"' in linux


def test_install_guide_names_the_mime_file_for_the_uninstall() -> None:
    """The Linux uninstall removes the package and rebuilds the user's MIME database."""
    guide = (Path(__file__).resolve().parents[1] / "docs" / "install.md").read_text("utf-8")
    assert "~/.local/share/mime/packages/tether.xml" in guide
    assert "update-mime-database ~/.local/share/mime" in guide


# --- macOS -------------------------------------------------------------------


def test_bundle_declares_and_owns_the_document_type() -> None:
    """``Tether.app`` exports the type for ``tether`` files and is its owner and editor."""
    keys = macos_app.info_plist("1.0.0", "arm64")
    (exported,) = keys["UTExportedTypeDeclarations"]
    assert exported["UTTypeIdentifier"] == file_type.MACOS_TYPE_ID
    assert exported["UTTypeIdentifier"].startswith(macos_app.BUNDLE_ID + ".")
    assert exported["UTTypeTagSpecification"] == {"public.filename-extension": ["tether"]}
    assert exported["UTTypeConformsTo"] == ["public.data"]
    (document,) = keys["CFBundleDocumentTypes"]
    assert document["LSItemContentTypes"] == [file_type.MACOS_TYPE_ID]
    assert document["LSHandlerRank"] == "Owner"
    assert document["CFBundleTypeRole"] == "Editor"
    assert not any(t.startswith("public.") for t in document["LSItemContentTypes"])


def test_bundle_check_rejects_a_bundle_without_the_document_type(tmp_path) -> None:
    """A bundle that an older installer wrote, with no document type, fails ``check``."""
    import plistlib

    prefix = tmp_path / "Tether"
    (prefix / "envs" / "tether" / "bin").mkdir(parents=True)
    (prefix / "envs" / "tether" / "bin" / "tether-gui").write_text("#!/bin/sh\n")
    icon = tmp_path / "tether.icns"
    icon.write_bytes(b"icns")
    bundle = macos_app.write_bundle(prefix, tmp_path / "Applications", "1.0.0", icon=icon)
    assert bundle is not None
    assert macos_app.check_bundle(bundle, prefix) == []
    plist = bundle / "Contents" / "Info.plist"
    data = plistlib.loads(plist.read_bytes())
    del data["CFBundleDocumentTypes"]
    plist.write_bytes(plistlib.dumps(data))
    assert any("CFBundleDocumentTypes" in p for p in macos_app.check_bundle(bundle, prefix))


@pytest.mark.parametrize(
    ("registration", "message"),
    [
        ((file_type.MACOS_TYPE_ID, macos_app.BUNDLE_ID), None),
        ((file_type.MACOS_TYPE_ID, macos_app.BUNDLE_ID.upper()), None),
        (("dyn.ah62d4rv4ge81e7dwqz2g22p3", None), "gives .tether the type"),
        ((file_type.MACOS_TYPE_ID, "com.example.other"), "opens io.github.bioedca.tether.project"),
        ((file_type.MACOS_TYPE_ID, None), "with None"),
    ],
)
def test_macos_check_reads_the_launchservices_answer(registration, message) -> None:
    """The smoke's macOS check passes only for the Tether type opened by ``Tether.app``."""
    problems = file_type.check_macos(registration, macos_app.BUNDLE_ID)
    if message is None:
        assert problems == []
    else:
        assert len(problems) == 1 and message in problems[0]


def test_macos_install_smoke_asks_launchservices() -> None:
    """The macOS leg runs the module's check after the bundle check."""
    smoke = INSTALL_SMOKE.read_text(encoding="utf-8")
    mac = smoke.split('if [ "$RUNNER_OS" = "macOS" ]; then\n    # The launcher bundle', 1)[1]
    mac = mac.split("\n  fi\n", 1)[0]
    assert (
        '"$prefix/envs/tether/bin/python" -m tether.gui.file_type check --prefix "$prefix"' in mac
    )

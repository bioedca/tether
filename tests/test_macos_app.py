# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The macOS ``Tether.app`` launcher bundle (issue #663, ADR-0091).

:mod:`tether.gui.macos_app` imports only the standard library, so these tests run on
every system. The bundle's executable is a ``sh`` script; the tests that run it need a
POSIX shell. The one end-to-end test runs ``post_install.sh`` with a ``uname`` that
answers ``Darwin``, so the macOS block runs here against a stub prefix. What only a real
Mac shows (Launchpad, Spotlight, Gatekeeper) is the manual check the PR records.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tether.gui import identity, macos_app

ROOT = Path(__file__).resolve().parents[1]
POST_INSTALL_SH = ROOT / "packaging" / "scripts" / "post_install.sh"
INSTALL_SMOKE = ROOT / "packaging" / "scripts" / "install_smoke.sh"
INSTALL_DOC = ROOT / "docs" / "install.md"

_POSIX_SH = os.name == "posix" and shutil.which("sh") is not None
needs_sh = pytest.mark.skipif(
    not _POSIX_SH, reason="the bundle launcher and post_install.sh are sh scripts"
)


def _prefix(tmp_path: Path, name: str = "Tether with space") -> Path:
    """An install prefix whose ``tether-gui`` prints the arguments it was given."""
    prefix = tmp_path / name
    bin_d = prefix / "envs" / "tether" / "bin"
    bin_d.mkdir(parents=True)
    gui = bin_d / "tether-gui"
    gui.write_text('#!/bin/sh\nprintf "%s\\n" "tether-gui" "$@"\n', encoding="utf-8")
    gui.chmod(0o755)
    return prefix


def _plist(bundle: Path) -> dict:
    with open(bundle / "Contents" / "Info.plist", "rb") as handle:
        return plistlib.load(handle)


def test_bundle_goes_to_the_user_folder_for_an_install_in_home(tmp_path) -> None:
    home = tmp_path / "home"
    assert macos_app.applications_dir(home / "Tether", home) == home / "Applications"


def test_bundle_goes_to_the_system_folder_for_any_other_install(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "is_dir", lambda self: True)
    monkeypatch.setattr(os, "access", lambda path, mode: True)
    assert macos_app.applications_dir(tmp_path / "opt" / "Tether", home) == Path("/Applications")


def test_unwritable_system_folder_falls_back_to_the_user_folder(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(os, "access", lambda path, mode: False)
    assert macos_app.applications_dir(tmp_path / "opt" / "Tether", home) == home / "Applications"


def test_a_folder_that_only_starts_like_home_is_not_in_home(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "is_dir", lambda self: True)
    monkeypatch.setattr(os, "access", lambda path, mode: True)
    home = tmp_path / "home"
    assert macos_app.applications_dir(tmp_path / "home2" / "Tether", home) == Path("/Applications")


@pytest.mark.parametrize(
    ("version", "expected"),
    [("1.0.0", "1.0.0"), ("1.0.0rc3", "1.0.0"), ("1.2.dev4+g1234", "1.2"), ("unknown", "0")],
)
def test_bundle_version_is_the_numeric_release(version, expected) -> None:
    assert macos_app.bundle_version(version) == expected


def test_written_bundle_has_the_launcher_layout(tmp_path) -> None:
    prefix = _prefix(tmp_path)
    bundle = macos_app.write_bundle(prefix, tmp_path / "Applications", "1.0.0rc3")
    assert bundle == tmp_path / "Applications" / "Tether.app"

    data = _plist(bundle)
    assert data["CFBundleIdentifier"] == macos_app.BUNDLE_ID
    assert data["CFBundleName"] == data["CFBundleDisplayName"] == identity.APPLICATION_NAME
    assert data["CFBundlePackageType"] == "APPL"
    assert data["CFBundleShortVersionString"] == "1.0.0rc3"
    assert data["CFBundleVersion"] == "1.0.0"
    assert data["CFBundleExecutable"] == macos_app.EXECUTABLE_NAME
    assert data["CFBundleIconFile"] == macos_app.ICON_FILE

    icon = bundle / "Contents" / "Resources" / macos_app.ICON_FILE
    assert icon.read_bytes() == (identity.app_icon_dir() / "tether.icns").read_bytes()
    assert (bundle / "Contents" / "PkgInfo").read_text(encoding="ascii") == "APPL????"
    # A launcher only: no copy of the environment goes into the bundle.
    files = sorted(p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file())
    assert files == [
        "Contents/Info.plist",
        "Contents/MacOS/Tether",
        "Contents/PkgInfo",
        "Contents/Resources/tether.icns",
    ]
    assert not list((tmp_path / "Applications").glob(".tether-app-*")), "staging left behind"
    assert macos_app.check_bundle(bundle, prefix) == []


@needs_sh
def test_bundle_executable_starts_the_installed_entry_point(tmp_path) -> None:
    """The bundle's executable forwards its arguments to ``tether-gui`` of the prefix.

    The prefix has a space in it, as a user's folder can. The ``-psn_`` argument that
    older macOS versions add is dropped; every other argument arrives unchanged.
    """
    prefix = _prefix(tmp_path)
    bundle = macos_app.write_bundle(prefix, tmp_path / "Applications", "1.0.0")
    exe = bundle / "Contents" / "MacOS" / macos_app.EXECUTABLE_NAME
    assert os.access(exe, os.X_OK)
    out = subprocess.run(
        [str(exe), "-psn_0_12345", "my project.tether"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert out.splitlines() == ["tether-gui", "my project.tether"]


def test_reinstall_replaces_tethers_own_bundle(tmp_path) -> None:
    prefix = _prefix(tmp_path)
    apps = tmp_path / "Applications"
    macos_app.write_bundle(prefix, apps, "1.0.0rc3")
    (apps / "Tether.app" / "Contents" / "Resources" / "stale").write_text("x")
    bundle = macos_app.write_bundle(prefix, apps, "1.0.0")
    assert _plist(bundle)["CFBundleShortVersionString"] == "1.0.0"
    assert not (bundle / "Contents" / "Resources" / "stale").exists()


def test_another_programs_bundle_is_left_alone(tmp_path) -> None:
    apps = tmp_path / "Applications"
    other = apps / "Tether.app" / "Contents"
    other.mkdir(parents=True)
    with open(other / "Info.plist", "wb") as handle:
        plistlib.dump({"CFBundleIdentifier": "org.example.tether"}, handle)
    assert macos_app.write_bundle(_prefix(tmp_path), apps, "1.0.0") is None
    assert _plist(apps / "Tether.app")["CFBundleIdentifier"] == "org.example.tether"
    assert not (other / "MacOS").exists()


def test_a_folder_without_info_plist_is_left_alone(tmp_path) -> None:
    apps = tmp_path / "Applications"
    (apps / "Tether.app" / "notes").mkdir(parents=True)
    assert macos_app.write_bundle(_prefix(tmp_path), apps, "1.0.0") is None
    assert (apps / "Tether.app" / "notes").is_dir()


def test_check_names_each_problem(tmp_path) -> None:
    prefix = _prefix(tmp_path)
    bundle = macos_app.write_bundle(prefix, tmp_path / "Applications", "1.0.0")
    other_prefix = _prefix(tmp_path, "Other")
    assert any("does not start" in p for p in macos_app.check_bundle(bundle, other_prefix))

    (bundle / "Contents" / "Resources" / macos_app.ICON_FILE).write_bytes(b"")
    assert any("icon is missing" in p for p in macos_app.check_bundle(bundle, prefix))

    data = _plist(bundle)
    data["CFBundleIdentifier"] = "org.example.other"
    with open(bundle / "Contents" / "Info.plist", "wb") as handle:
        plistlib.dump(data, handle)
    assert any("CFBundleIdentifier" in p for p in macos_app.check_bundle(bundle, prefix))

    (bundle / "Contents" / "Info.plist").write_bytes(b"not a plist")
    assert macos_app.check_bundle(bundle, prefix) == [
        f"no readable Contents/Info.plist in {bundle}"
    ]


def test_cli_installs_and_checks(tmp_path, capsys) -> None:
    home = tmp_path / "home"
    prefix = _prefix(home, "Tether")
    assert macos_app.main(["install", "--prefix", str(prefix), "--home", str(home)]) == 0
    bundle = home / "Applications" / "Tether.app"
    assert capsys.readouterr().out.strip() == str(bundle)
    assert macos_app.main(["check", str(bundle), "--prefix", str(prefix)]) == 0
    assert macos_app.main(["check", str(bundle), "--prefix", str(tmp_path / "x")]) == 1


@needs_sh
def test_post_install_writes_the_bundle_on_macos(tmp_path) -> None:
    """``post_install.sh`` on "macOS" writes ``~/Applications/Tether.app``, run twice.

    A ``uname`` that answers ``Darwin`` comes first on ``PATH``; the env interpreters are
    stubs that skip pip and run this test's own Python, as in ``test_gui_identity``.
    """
    home = tmp_path / "home"
    prefix = home / "Tether"
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
    (prefix / "envs" / "tether" / "bin" / "tether-gui").write_text("#!/bin/sh\n")
    (prefix / "wheelhouse").mkdir()
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    uname = fake_bin / "uname"
    uname.write_text("#!/bin/sh\necho Darwin\n", encoding="utf-8")
    uname.chmod(0o755)
    env = {
        **os.environ,
        "PREFIX": str(prefix),
        "HOME": str(home),
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
    }
    for _ in range(2):
        subprocess.run(["sh", str(POST_INSTALL_SH)], check=True, env=env, capture_output=True)
    bundle = home / "Applications" / "Tether.app"
    assert macos_app.check_bundle(bundle, prefix) == []
    assert not (home / ".local" / "share" / "applications").exists(), "no Linux entry on macOS"


def test_install_smoke_checks_the_bundle_on_macos() -> None:
    smoke = INSTALL_SMOKE.read_text(encoding="utf-8")
    block = smoke.split('if [ "$RUNNER_OS" = "macOS" ]; then', 2)[2].split("\n  fi\n", 1)[0]
    assert 'app="$HOME/Applications/Tether.app"' in block
    assert 'plutil -lint "$app/Contents/Info.plist"' in block
    assert "-m tether.gui.macos_app check" in block
    assert '"$app/Contents/MacOS/$app_exe" --version' in block


def test_install_guide_names_the_bundle_for_launch_and_uninstall() -> None:
    doc = INSTALL_DOC.read_text(encoding="utf-8")
    uninstall = doc.split("## Uninstall", 1)[1].split("**Windows.**", 1)[0]
    macos = uninstall.split("**macOS**", 1)[1]
    assert "rm -rf ~/Tether ~/Applications/Tether.app" in macos
    assert "Launchpad" in doc.split("## 5. Check that Tether runs", 1)[1].split("\n## ", 1)[0]


def test_check_accepts_another_spelling_of_the_same_install_folder(tmp_path) -> None:
    """constructor's ``.pkg`` installs into ``~/tether``; the smoke names it ``~/Tether``.

    The two strings name one folder on macOS and Windows, whose file systems ignore case.
    On Linux a link stands in for that: ``check`` compares the files, not the strings.
    """
    prefix = _prefix(tmp_path, "tether")
    bundle = macos_app.write_bundle(prefix, tmp_path / "Applications", "1.0.0")
    if sys.platform.startswith("linux"):
        alias = tmp_path / "Tether-alias"
        alias.symlink_to(prefix, target_is_directory=True)
    else:
        alias = tmp_path / "TETHER"
    assert macos_app.check_bundle(bundle, alias) == []


def test_launcher_target_reads_the_exec_line(tmp_path) -> None:
    prefix = tmp_path / "with space" / "Tether"
    expected = Path(os.path.abspath(prefix)) / "envs" / "tether" / "bin" / "tether-gui"
    assert macos_app.launcher_target(macos_app.launcher_script(prefix)) == expected
    assert macos_app.launcher_target("#!/bin/sh\necho hi\n") is None
    assert macos_app.launcher_target("exec 'unterminated\n") is None

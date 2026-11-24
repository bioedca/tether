# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The installer hand-off (#331, ADR-0141): ordering, gating, the window, and the helpers.

Three layers, cheapest first:

* the Python side with a recorded ``spawn``: nothing starts without consent and a
  [`VerifiedInstaller`][tether.gui.update_verify.VerifiedInstaller], the helper is started
  before Tether is asked to quit, and a file changed after verification never reaches it;
* [`check_launch_surface`][tether.gui.update_handoff.check_launch_surface] against a fake
  install laid out the way ``packaging/scripts/post_install.sh`` lays one out;
* the real helper script of this platform (``update_handoff.sh`` on macOS and Linux,
  ``update_handoff.ps1`` on Windows) run against a fake installer: success, a failing
  installer, an installer that installs the wrong version, a killed installer, and a file
  replaced after verification. Each failure must leave the previous install byte for byte
  where it was.

The real installers run through the same helper in ``packaging/scripts/upgrade_smoke.sh``.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path

import pytest

from tether.gui import app_settings, update_handoff, update_verify
from tether.gui.update_check import parse_version
from tether.gui.update_handoff import (
    BACKUP_SUFFIX,
    CHECK_TIMEOUT_S,
    EXIT_WAIT_TIMEOUT_S,
    HELPER_START_TIMEOUT_S,
    INSTALL_TIMEOUT_S,
    HandoffOutcome,
    HandoffRefused,
    HandoffUnavailable,
    check_install,
    check_launch_surface,
    copy_verified,
    hand_off,
    helper_command,
    installer_command,
    read_update_result,
    relaunch_command,
)

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
_PRD = _ROOT / "docs" / "PRD.md"
_ASSETS = _SRC / "tether" / "gui" / "assets" / "update"
_POSIX = os.name != "nt"
_SUFFIX = {"linux": ".sh", "macos": ".pkg", "windows": ".exe"}
_HERE = update_handoff.handoff_platform()


def _mint(path: Path, version: str = "1.0.1") -> update_verify.VerifiedInstaller:
    """A VerifiedInstaller for ``path`` as it is now, made the way the verifier makes one."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return update_verify.VerifiedInstaller(
        update_verify._MINT, path, parse_version(version), digest, "0" * 40
    )


def _fake_install(prefix: Path, platform: str, *, nonadmin: bool = True) -> Path:
    (prefix / "envs" / "tether" / "conda-meta").mkdir(parents=True)
    (prefix / "launchers").mkdir()
    if platform == "windows" and nonadmin:
        (prefix / ".nonadmin").write_text("", encoding="utf-8")
    return prefix


class FakeProcess:
    """A stand-in for the spawned helper: reports ``started`` unless told not to."""

    def __init__(self, argv: Sequence[str], *, starts: bool, exits: bool) -> None:
        self.argv = list(argv)
        self.killed = False
        self._exits = exits
        if starts:
            started = (
                Path(self.argv[self.argv.index("-Started") + 1])
                if "-Started" in argv
                else (Path(self.argv[8]))
            )
            started.write_text("", encoding="utf-8")

    def poll(self) -> int | None:
        return 1 if self._exits or self.killed else None

    def kill(self) -> None:
        self.killed = True

    def wait(self) -> int:
        return 1


class Rig:
    """An installed copy, a verified installer, allowed consent and a recording spawn."""

    def __init__(self, tmp_path: Path, platform: str = "linux") -> None:
        self.platform = platform
        self.home = tmp_path / "home"
        self.home.mkdir()
        prefix = self.home / "Tether" if platform == "macos" else tmp_path / "apps" / "Tether"
        self.prefix = _fake_install(prefix, platform)
        self.download = tmp_path / "download" / f"Tether-1.0.1-X{_SUFFIX[platform]}"
        self.download.parent.mkdir()
        self.download.write_bytes(b"the verified installer bytes")
        self.installer = _mint(self.download)
        self.settings = tmp_path / "settings" / "settings.json"
        self.override = tmp_path / "no-override"
        app_settings.record_update_consent(True, self.settings)
        self.work = tmp_path / "work"
        self.work.mkdir()
        self.events: list[str] = []
        self.spawned: list[FakeProcess] = []
        self.starts = True
        self.exits = False

    def spawn(self, argv: Sequence[str], cwd: Path) -> FakeProcess:
        self.events.append("spawn")
        process = FakeProcess(argv, starts=self.starts, exits=self.exits)
        self.spawned.append(process)
        return process

    def quit(self) -> None:
        self.events.append("quit")

    def run(self, installer: object | None = None, **kw: object):
        options: dict[str, object] = {
            "prefix": self.prefix,
            "quit_app": self.quit,
            "settings_file": self.settings,
            "override_file": self.override,
            "platform": self.platform,
            "home": self.home,
            "workroot": self.work,
            "spawn": self.spawn,
            "tether_pid": 4242,
            "start_timeout": 0.5,
        }
        options.update(kw)
        return hand_off(installer or self.installer, **options)  # type: ignore[arg-type]


# --- the Python side --------------------------------------------------------------------


@pytest.mark.parametrize("platform", ["linux", "macos", "windows"])
def test_the_helper_is_started_before_tether_is_asked_to_quit(tmp_path: Path, platform: str):
    """Launch, then exit (ADR-0060 decision 6): the Windows ordering, on every platform."""
    rig = Rig(tmp_path, platform)
    result = rig.run()
    assert result.outcome is HandoffOutcome.HANDED_OFF, result.reason
    assert rig.events == ["spawn", "quit"]
    argv = rig.spawned[0].argv
    if platform == "windows":
        assert argv[0].lower().endswith(r"system32\windowspowershell\v1.0\powershell.exe")
        assert argv[argv.index("-TetherPid") + 1] == "4242"
        assert argv[argv.index("-Prefix") + 1] == str(rig.prefix)
    else:
        assert argv[:2] == ["/bin/sh", argv[1]] and argv[1].endswith("update_handoff.sh")
        assert argv[2] == "4242" and argv[3] == str(rig.prefix) and argv[11] == "--"


@pytest.mark.parametrize("answer", [None, False])
def test_nothing_starts_without_consent(tmp_path: Path, answer: bool | None):
    """Acceptance criterion 5: no consent, no helper and no quit."""
    rig = Rig(tmp_path)
    rig.settings.unlink()
    if answer is not None:
        app_settings.record_update_consent(answer, rig.settings)
    result = rig.run()
    assert result.outcome is HandoffOutcome.UNAVAILABLE
    assert rig.events == []
    assert list(rig.work.iterdir()) == []


def test_the_administrator_override_stops_it(tmp_path: Path):
    rig = Rig(tmp_path)
    rig.override.write_text("", encoding="utf-8")
    assert rig.run().outcome is HandoffOutcome.UNAVAILABLE
    assert rig.events == []


class _LooksVerified:
    def __init__(self, real: update_verify.VerifiedInstaller) -> None:
        self.path, self.version, self.sha256 = real.path, real.version, real.sha256
        self.source_digest = real.source_digest


def test_only_a_verified_installer_is_accepted(tmp_path: Path):
    """Acceptance criterion 5: an object that only looks verified is refused."""
    rig = Rig(tmp_path)

    class Sub(update_verify.VerifiedInstaller):
        __slots__ = ()

    forged = object.__new__(Sub)
    for name in ("_path", "_version", "_sha256", "_source_digest"):
        object.__setattr__(forged, name, getattr(rig.installer, name))
    for impostor in (_LooksVerified(rig.installer), forged, rig.download):
        result = rig.run(impostor)
        assert result.outcome is HandoffOutcome.REFUSED, impostor
    assert rig.events == []


def test_a_refused_verification_carries_nothing_to_hand_off():
    """The verifier's refused and unavailable results hold no installer (#333)."""
    for outcome in (update_verify.Outcome.REFUSED, update_verify.Outcome.UNAVAILABLE):
        assert update_verify.VerificationResult(outcome, "no").installer is None


def test_a_file_changed_after_verification_is_refused(tmp_path: Path):
    """Acceptance criterion 3: the bytes the helper gets are the bytes that were verified."""
    rig = Rig(tmp_path)
    rig.download.write_bytes(b"something else, written after verification")
    result = rig.run()
    assert result.outcome is HandoffOutcome.REFUSED
    assert "changed after it was verified" in result.reason
    assert rig.events == []
    assert list(rig.work.iterdir()) == []


def test_the_copy_is_the_verified_bytes_and_private(tmp_path: Path):
    rig = Rig(tmp_path)
    target = tmp_path / "private"
    target.mkdir()
    copy = copy_verified(rig.installer, target)
    assert copy.name == rig.download.name
    assert copy.read_bytes() == rig.download.read_bytes()
    if _POSIX:
        assert copy.stat().st_mode & 0o777 == 0o600
    with pytest.raises(HandoffRefused):
        copy_verified(rig.installer, target)  # created new, never written over


@pytest.mark.skipif(not _POSIX, reason="symbolic links need a privilege on Windows")
def test_a_symbolic_link_put_in_place_of_the_file_is_refused(tmp_path: Path):
    rig = Rig(tmp_path)
    real = tmp_path / "elsewhere.sh"
    real.write_bytes(rig.download.read_bytes())
    rig.download.unlink()
    rig.download.symlink_to(real)
    assert rig.run().outcome is HandoffOutcome.REFUSED
    assert rig.events == []


def test_the_private_folder_is_only_this_users(tmp_path: Path):
    rig = Rig(tmp_path)
    assert rig.run().outcome is HandoffOutcome.HANDED_OFF
    (workdir,) = rig.work.iterdir()
    if _POSIX:
        assert workdir.stat().st_mode & 0o777 == 0o700
    assert {p.name for p in workdir.iterdir()} == {
        rig.download.name,
        "update_handoff.sh",
        "helper-started",
    }


@pytest.mark.parametrize("starts, exits", [(False, False), (False, True)])
def test_a_helper_that_does_not_report_leaves_tether_open(tmp_path: Path, starts, exits):
    """A helper that cannot start (Group Policy, a missing shell) changes nothing."""
    rig = Rig(tmp_path)
    rig.starts, rig.exits = starts, exits
    result = rig.run()
    assert result.outcome is HandoffOutcome.REFUSED
    assert rig.events == ["spawn"]
    assert rig.spawned[0].killed is not exits
    assert list(rig.work.iterdir()) == []


def test_an_installer_of_another_platform_is_refused(tmp_path: Path):
    rig = Rig(tmp_path, "windows")
    other = tmp_path / "download" / "Tether-1.0.1-Linux-x86_64.sh"
    other.write_bytes(b"x")
    assert rig.run(_mint(other)).outcome is HandoffOutcome.REFUSED
    assert rig.events == []


def test_a_prerelease_is_refused_here_too(tmp_path: Path):
    rig = Rig(tmp_path)
    assert rig.run(_mint(rig.download, "1.0.1rc1")).outcome is HandoffOutcome.REFUSED


def test_check_install(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    linux = _fake_install(tmp_path / "linux" / "Tether", "linux")
    check_install(linux, "linux", home=home)
    with pytest.raises(HandoffUnavailable, match="not installed by a Tether installer"):
        check_install(tmp_path, "linux", home=home)  # a development checkout
    with pytest.raises(HandoffUnavailable, match="folder of its own"):
        check_install(home, "linux", home=home)
    with pytest.raises(HandoffUnavailable):
        check_install(Path("Tether"), "linux", home=home)
    windows_admin = _fake_install(tmp_path / "admin" / "Tether", "windows", nonadmin=False)
    with pytest.raises(HandoffUnavailable, match="all users"):
        check_install(windows_admin, "windows", home=home)
    with pytest.raises(HandoffUnavailable, match="home folder"):
        check_install(linux, "macos", home=home)
    check_install(_fake_install(home / "Tether", "macos"), "macos", home=home)
    leftover = linux.with_name(linux.name + BACKUP_SUFFIX)
    leftover.mkdir()
    with pytest.raises(HandoffUnavailable, match="earlier update"):
        check_install(linux, "linux", home=home)


def test_the_installer_commands():
    prefix = Path("/home/u/My Tether")
    exe = Path("/w/Tether-1.0.1-Linux-x86_64.sh")
    assert installer_command(exe, prefix, "linux") == ["/bin/sh", str(exe), "-b", "-p", str(prefix)]
    assert installer_command(exe, prefix, "macos") == [
        "/usr/sbin/installer",
        "-pkg",
        str(exe),
        "-target",
        "CurrentUserHomeDirectory",
    ]
    program, arguments = installer_command(exe, prefix, "windows")
    assert program == str(exe)
    # NSIS takes /D= last and unquoted, spaces and all.
    assert arguments == f"/InstallationType=JustMe /S /D={prefix}"
    assert arguments.endswith(f"/D={prefix}")
    assert relaunch_command(prefix, "linux") == [str(prefix / "launchers" / "tether-gui")]
    assert relaunch_command(prefix, "windows")[0].endswith("tether-gui.exe")


def test_the_windows_helper_runs_under_windows_powershell_by_absolute_path():
    argv = helper_command(
        Path(r"C:\t\update_handoff.ps1"),
        platform="windows",
        tether_pid=1,
        prefix=Path(r"C:\Users\o'brien\Tether"),
        installer=Path(r"C:\t\Tether-1.0.1-Windows-x86_64.exe"),
        sha256="a" * 64,
        version="1.0.1",
        result=Path(r"C:\r\update-result.txt"),
        started=Path(r"C:\t\helper-started"),
        installer_argv=["C:/t/x.exe", "/S /D=C:/x"],
        environ={"SystemRoot": r"C:\Windows"},
    )
    assert argv[0] == str(Path(r"C:\Windows") / r"System32\WindowsPowerShell\v1.0\powershell.exe")
    assert argv[argv.index("-ExecutionPolicy") + 1] == "Bypass"
    assert argv[argv.index("-File") + 1] == r"C:\t\update_handoff.ps1"
    assert argv[argv.index("-InstallerArguments") + 1] == "/S /D=C:/x"
    assert argv[argv.index("-ExitTimeout") + 1] == str(EXIT_WAIT_TIMEOUT_S)
    assert argv[argv.index("-InstallTimeout") + 1] == str(INSTALL_TIMEOUT_S)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("updated 1.0.1 \n", ("updated", "1.0.1", "")),
        (
            "restored 1.0.1 the installer exited 2\n",
            ("restored", "1.0.1", "the installer exited 2"),
        ),
        ("unchanged 1.0.1 Tether did not close", ("unchanged", "1.0.1", "Tether did not close")),
        (
            "failed 1.0.1 the previous copy is in /x",
            ("failed", "1.0.1", "the previous copy is in /x"),
        ),
        ("installed 1.0.1", None),
        ("", None),
    ],
)
def test_read_update_result(tmp_path: Path, text: str, expected):
    path = tmp_path / "update-result.txt"
    path.write_text(text, encoding="utf-8")
    report = read_update_result(path)
    got = None if report is None else (report.status, report.version, report.detail)
    assert got == expected
    assert read_update_result(tmp_path / "absent.txt") is None


def test_the_tunables_are_registered_in_the_prd():
    """PRD §11.2 states each timeout with the value the module uses."""
    text = _PRD.read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if "HELPER_START_TIMEOUT_S" in line]
    assert len(rows) == 1, "one §11.2 row names the hand-off timeouts"
    row = rows[0]
    for name, value in (
        ("HELPER_START_TIMEOUT_S", HELPER_START_TIMEOUT_S),
        ("EXIT_WAIT_TIMEOUT_S", EXIT_WAIT_TIMEOUT_S),
        ("INSTALL_TIMEOUT_S", INSTALL_TIMEOUT_S),
        ("CHECK_TIMEOUT_S", CHECK_TIMEOUT_S),
    ):
        assert name in row
        assert f"**{value:g} s**" in row, name


def test_the_helpers_ship_in_the_package():
    for name in ("update_handoff.sh", "update_handoff.ps1"):
        text = (_ASSETS / name).read_text(encoding="utf-8")
        assert "SPDX-" + "License-Identifier: GPL-3.0-or-later" in text
        assert ".tether-update-backup" in text
        assert "tether.gui.update_handoff check" in text
    assert update_handoff._helper_source("linux") == (_ASSETS / "update_handoff.sh").read_bytes()
    assert BACKUP_SUFFIX == ".tether-update-backup"


def test_the_module_imports_no_qt():
    code = (
        "import sys, tether.gui.update_handoff; "
        "sys.exit(any(m.split('.')[0] in ('PySide6', 'pyqtgraph', 'numpy') for m in sys.modules))"
    )
    env = dict(os.environ, PYTHONPATH=str(_SRC))
    assert subprocess.run([sys.executable, "-c", code], env=env, check=False).returncode == 0


# --- a fake install, laid out as post_install.sh lays one out --------------------------------


def _posix_layout(prefix: Path, home: Path, version: str, *, marker: str) -> None:
    """What the .sh installer and post_install.sh leave, with stand-in programs."""
    bin_dir = prefix / "envs" / "tether" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    (prefix / "envs" / "tether" / "conda-meta").mkdir(exist_ok=True)
    (prefix / "marker.txt").write_text(marker, encoding="utf-8")
    for name in ("tether", "tether-gui"):
        program = bin_dir / name
        program.write_text(f'#!/bin/sh\necho "tether {version}"\n', encoding="utf-8")
        program.chmod(0o755)
    python = bin_dir / "python"
    python.write_text(
        f'#!/bin/sh\nPYTHONPATH="{_SRC}" exec "{sys.executable}" "$@"\n', encoding="utf-8"
    )
    python.chmod(0o755)
    launchers = prefix / "launchers"
    launchers.mkdir(exist_ok=True)
    for name in ("tether", "tether-gui"):
        shim = launchers / name
        shim.write_text(
            "#!/bin/sh\n# Generated by the Tether installer (ADR-0051).\n"
            f'exec "{bin_dir / name}" "$@"\n',
            encoding="utf-8",
        )
        shim.chmod(0o755)
    apps = home / ".local" / "share" / "applications"
    apps.mkdir(parents=True, exist_ok=True)
    (apps / "tether.desktop").write_text(
        f'[Desktop Entry]\nType=Application\nName=Tether\nExec="{bin_dir / "tether-gui"}" %f\n',
        encoding="utf-8",
    )


@pytest.mark.skipif(not _POSIX, reason="the shims of macOS and Linux are sh scripts")
def test_check_launch_surface_on_a_linux_layout(tmp_path: Path):
    """Acceptance criterion 2: each element is checked, not assumed."""
    home = tmp_path / "home"
    prefix = tmp_path / "Tether"
    _posix_layout(prefix, home, "1.0.1", marker="new")
    assert check_launch_surface(prefix, "1.0.1", platform="linux", home=home) == []

    problems = check_launch_surface(prefix, "1.0.2", platform="linux", home=home)
    assert len(problems) == 2 and all("does not report 1.0.2" in p for p in problems)

    entry = home / ".local" / "share" / "applications" / "tether.desktop"
    entry.write_text(
        entry.read_text().replace(str(prefix), str(tmp_path / "Old")), encoding="utf-8"
    )
    (problem,) = check_launch_surface(prefix, "1.0.1", platform="linux", home=home)
    assert "tether.desktop does not start" in problem

    entry.unlink()
    (prefix / "launchers" / "tether-gui").write_text("#!/bin/sh\nexec /elsewhere\n")
    problems = check_launch_surface(prefix, "1.0.1", platform="linux", home=home)
    assert any("launchers/tether-gui does not start" in p for p in problems)
    assert any("tether.desktop" in p for p in problems)


@pytest.mark.skipif(not _POSIX, reason="symbolic links need a privilege on Windows")
def test_another_name_for_the_same_folder_is_the_same_install(tmp_path: Path):
    """The macOS .pkg installs into ``~/tether`` on a file system that ignores case.

    The launchers it writes then name ``~/tether`` while Tether is asked about ``~/Tether``;
    the packaging run on macOS failed the new copy's check on exactly that (#331). A
    symbolic link stands in for the second spelling, on any file system.
    """
    home = tmp_path / "home"
    real = tmp_path / "tether"
    _posix_layout(real, home, "1.0.1", marker="new")
    home.mkdir(exist_ok=True)
    (home / "Tether").symlink_to(real, target_is_directory=True)
    alias = home / "Tether"

    assert check_launch_surface(alias, "1.0.1", platform="linux", home=home) == []
    check_install(real, "macos", home=home)


# --- the real helper, against a fake installer ---------------------------------------------------


_FAKE_POSIX_INSTALLER = """\
#!/bin/sh
# A stand-in for the .sh installer: $1 is -p, $2 the prefix. MODE picks what it does.
prefix="$2"
[ -e "$prefix" ] && {{ echo "ERROR: File or directory already exists: $prefix"; exit 1; }}
case "{mode}" in
  fail) mkdir -p "$prefix/envs/half-written"; exit 3 ;;
  hang)
    mkdir -p "$prefix/envs/half-written"
    sleep 60 &
    echo $! > "$HOME/child.pid"
    sleep 60
    exit 0 ;;
esac
"{python}" -c 'import sys; sys.path.insert(0, sys.argv[1]); import test_update_handoff as t; \
t._posix_layout(t.Path(sys.argv[2]), t.Path(sys.argv[3]), sys.argv[4], marker="new")' \
  "{tests}" "$prefix" "$HOME" "{version}"
"""


class HelperRig:
    """The real helper of this platform, a fake installer, and an old install to protect."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.prefix = tmp_path / "apps" / "Tether"
        # Named as hand_off names its private folder, so the helper removes it at the end.
        self.work = tmp_path / "tether-update-test"
        self.work.mkdir()
        self.result = tmp_path / "settings" / "update-result.txt"
        self.result.parent.mkdir()
        self.started = self.work / "helper-started"

    def old_install(self) -> None:
        _posix_layout(self.prefix, self.home, "1.0.0", marker="old")

    def installer(self, mode: str, version: str = "1.0.1") -> Path:
        path = self.work / "Tether-1.0.1-Linux-x86_64.sh"
        path.write_text(
            _FAKE_POSIX_INSTALLER.format(
                mode=mode, python=sys.executable, tests=_ROOT / "tests", version=version
            ),
            encoding="utf-8",
        )
        return path

    def run(
        self,
        installer: Path,
        *,
        sha256: str | None = None,
        tether: subprocess.Popen[bytes] | None = None,
        exit_timeout: int = 20,
        install_timeout: int = 60,
    ) -> subprocess.CompletedProcess[bytes]:
        helper = self.work / "update_handoff.sh"
        helper.write_bytes((_ASSETS / "update_handoff.sh").read_bytes())
        pid = tether.pid if tether is not None else _dead_pid()
        argv = helper_command(
            helper,
            platform="linux",
            tether_pid=pid,
            prefix=self.prefix,
            installer=installer,
            sha256=sha256 or hashlib.sha256(installer.read_bytes()).hexdigest(),
            version="1.0.1",
            result=self.result,
            started=self.started,
            installer_argv=["/bin/sh", str(installer), "-p", str(self.prefix)],
            exit_timeout=exit_timeout,
            install_timeout=install_timeout,
        )
        env = dict(os.environ, HOME=str(self.home))
        return subprocess.run(argv, env=env, capture_output=True, timeout=180, check=False)

    def report(self) -> tuple[str, str, str] | None:
        report = read_update_result(self.result)
        return None if report is None else (report.status, report.version, report.detail)

    def marker(self) -> str:
        return (self.prefix / "marker.txt").read_text(encoding="utf-8")

    def leftovers(self) -> list[str]:
        return sorted(p.name for p in self.prefix.parent.iterdir() if p.name != "Tether")


def _dead_pid() -> int:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


_posix_only = pytest.mark.skipif(
    not _POSIX, reason="update_handoff.sh is the macOS and Linux helper"
)


@_posix_only
def test_the_helper_installs_checks_and_deletes_the_backup(tmp_path: Path):
    rig = HelperRig(tmp_path)
    rig.old_install()
    tether = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2)"])
    # Reaped as soon as it ends, as the real Tether is by whatever started it: an unreaped
    # child stays a zombie, and `kill -0` answers for a zombie.
    reaper = threading.Thread(target=tether.wait, daemon=True)
    reaper.start()
    run = rig.run(rig.installer("ok"), tether=tether)
    assert run.returncode == 0, run.stderr
    assert tether.poll() is not None, "the helper went on while Tether still ran"
    assert rig.report() == ("updated", "1.0.1", "")
    assert rig.marker() == "new"
    assert rig.leftovers() == []
    assert not rig.work.exists(), "the private folder, installer included, is removed"


@_posix_only
@pytest.mark.parametrize(
    "mode, version, why",
    [
        ("fail", "1.0.1", "the installer exited 3"),
        ("ok", "1.0.2", "the new install failed its launch check"),
    ],
)
def test_a_failed_install_puts_the_previous_copy_back(tmp_path: Path, mode, version, why):
    """Acceptance criterion 4: an interrupted update does not brick the machine."""
    rig = HelperRig(tmp_path)
    rig.old_install()
    before = _tree(rig.prefix)
    run = rig.run(rig.installer(mode, version))
    assert run.returncode == 1, run.stderr
    assert rig.report() == ("restored", "1.0.1", why)
    assert _tree(rig.prefix) == before
    assert rig.leftovers() == []
    assert check_launch_surface(rig.prefix, "1.0.0", platform="linux", home=rig.home) == []


@_posix_only
def test_a_killed_installer_puts_the_previous_copy_back(tmp_path: Path):
    rig = HelperRig(tmp_path)
    rig.old_install()
    before = _tree(rig.prefix)
    started = time.monotonic()
    run = rig.run(rig.installer("hang"), install_timeout=2)
    assert time.monotonic() - started < 50, "the installer was not stopped"
    assert run.returncode == 1, run.stderr
    status, _, detail = rig.report()
    assert status == "restored" and detail.startswith("the installer exited ")
    assert _tree(rig.prefix) == before
    # Every process the installer started was stopped, not only the first one.
    child = int((rig.home / "child.pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)


@_posix_only
def test_a_file_replaced_after_verification_never_runs(tmp_path: Path):
    rig = HelperRig(tmp_path)
    rig.old_install()
    before = _tree(rig.prefix)
    installer = rig.installer("ok")
    run = rig.run(installer, sha256="0" * 64)
    assert run.returncode == 1
    assert rig.report() == ("unchanged", "1.0.1", "the update file is not the verified file")
    assert _tree(rig.prefix) == before


@_posix_only
def test_the_helper_waits_for_tether_and_gives_up_without_change(tmp_path: Path):
    rig = HelperRig(tmp_path)
    rig.old_install()
    before = _tree(rig.prefix)
    tether = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        run = rig.run(rig.installer("ok"), tether=tether, exit_timeout=2)
    finally:
        tether.kill()
        tether.wait()
    assert run.returncode == 1
    assert rig.report() == ("unchanged", "1.0.1", "Tether did not close")
    assert _tree(rig.prefix) == before


@_posix_only
def test_a_leftover_backup_is_never_overwritten(tmp_path: Path):
    rig = HelperRig(tmp_path)
    rig.old_install()
    leftover = rig.prefix.with_name("Tether" + BACKUP_SUFFIX)
    leftover.mkdir()
    (leftover / "keep.txt").write_text("keep", encoding="utf-8")
    run = rig.run(rig.installer("ok"))
    assert run.returncode == 1
    assert rig.report()[0] == "unchanged"
    assert (leftover / "keep.txt").read_text() == "keep"
    assert rig.marker() == "old"


def _tree(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): (p.read_bytes() if p.is_file() else b"<dir>")
        for p in sorted(root.rglob("*"))
    }


# --- the Windows helper ------------------------------------------------------------------

_FAKE_WINDOWS_INSTALLER = r"""@echo off
rem A stand-in for the NSIS installer. The last argument is /D=<prefix>.
setlocal EnableDelayedExpansion
set "ARGS=%*"
rem Everything up to /D goes; "=" cannot be part of the text replaced, so it goes next.
set "PREFIX=!ARGS:*/D=!"
set "PREFIX=!PREFIX:~1!"
if exist "!PREFIX!" exit /b 2
mkdir "!PREFIX!\envs\half-written"
if "{mode}"=="fail" exit /b 3
"{python}" "{driver}" "{tests}" "!PREFIX!" "{version}"
exit /b !errorlevel!
"""


_FAKE_WINDOWS_LAYOUT = """\
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
import test_update_handoff as t

t._windows_layout(Path(sys.argv[2]), sys.argv[3], marker="new")
"""


def _windows_layout(prefix: Path, version: str, *, marker: str) -> None:
    """What the NSIS installer and post_install.bat leave, with stand-in programs."""
    import venv

    env_dir = prefix / "envs" / "tether"
    if not (env_dir / "python.exe").exists():
        venv.EnvBuilder(system_site_packages=True, with_pip=False).create(env_dir)
    (env_dir / "conda-meta").mkdir(exist_ok=True)
    (prefix / ".nonadmin").write_text("", encoding="utf-8")
    (prefix / "marker.txt").write_text(marker, encoding="utf-8")
    scripts = env_dir / "Scripts"
    (scripts / "tether-gui.exe").write_bytes(b"MZ stand-in")
    launchers = prefix / "launchers"
    launchers.mkdir(exist_ok=True)
    for name in ("tether", "tether-gui"):
        (launchers / f"{name}.bat").write_text(
            f'@echo off\r\nrem forwards to "{scripts / (name + ".exe")}"\r\n'
            f"echo tether {version}\r\n",
            encoding="utf-8",
        )
    (prefix / "tether-prompt.bat").write_text("@echo off\r\n", encoding="utf-8")
    menu = Path(os.environ["APPDATA"]) / update_handoff._START_MENU
    menu.mkdir(parents=True, exist_ok=True)
    for link, target, arguments in (
        ("Tether.lnk", str(scripts / "tether-gui.exe"), ""),
        (
            "Tether Command Prompt.lnk",
            r"C:\Windows\System32\cmd.exe",
            f'/k ""{prefix}\\tether-prompt.bat""',
        ),
    ):
        env = dict(os.environ, L=str(menu / link), T=target, A=arguments)
        subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:L);"
                "$s.TargetPath=$env:T;$s.Arguments=$env:A;$s.Save()",
            ],
            env=env,
            check=True,
        )


_windows_only = pytest.mark.skipif(
    os.name != "nt", reason="update_handoff.ps1 is the Windows helper"
)


class WindowsRig(HelperRig):
    def old_install(self) -> None:
        _windows_layout(self.prefix, "1.0.0", marker="old")

    def installer(self, mode: str, version: str = "1.0.1") -> Path:
        path = self.work / "Tether-1.0.1-Windows-x86_64.cmd"
        driver = self.tmp / "fake_layout.py"
        driver.write_text(_FAKE_WINDOWS_LAYOUT, encoding="utf-8")
        path.write_text(
            _FAKE_WINDOWS_INSTALLER.format(
                mode=mode,
                python=sys.executable,
                driver=driver,
                tests=_ROOT / "tests",
                version=version,
            ),
            encoding="utf-8",
        )
        return path

    def run(self, installer: Path, *, sha256: str | None = None, **_: object):
        helper = self.work / "update_handoff.ps1"
        helper.write_bytes((_ASSETS / "update_handoff.ps1").read_bytes())
        program, arguments = installer_command(installer, self.prefix, "windows")
        argv = helper_command(
            helper,
            platform="windows",
            tether_pid=_dead_pid(),
            prefix=self.prefix,
            installer=installer,
            sha256=sha256 or hashlib.sha256(installer.read_bytes()).hexdigest(),
            version="1.0.1",
            result=self.result,
            started=self.started,
            installer_argv=[program, arguments],
            exit_timeout=20,
            install_timeout=120,
        )
        env = dict(os.environ, PYTHONPATH=str(_SRC))
        return subprocess.run(argv, env=env, capture_output=True, timeout=300, check=False)


@pytest.fixture
def windows_rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WindowsRig:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    return WindowsRig(tmp_path)


@_windows_only
def test_the_windows_helper_installs_checks_and_deletes_the_backup(windows_rig: WindowsRig):
    rig = windows_rig
    rig.old_install()
    run = rig.run(rig.installer("ok"))
    log = rig.result.with_suffix(".log")
    assert run.returncode == 0, (run.stderr, log.read_text() if log.exists() else "")
    assert rig.report() == ("updated", "1.0.1", "")
    assert rig.marker() == "new"
    assert rig.leftovers() == []


@_windows_only
@pytest.mark.parametrize(
    "mode, version, why",
    [
        ("fail", "1.0.1", "the installer exited 3"),
        ("ok", "1.0.2", "the new install failed its launch check"),
    ],
)
def test_the_windows_helper_puts_the_previous_copy_back(
    windows_rig: WindowsRig, mode, version, why
):
    rig = windows_rig
    rig.old_install()
    before = _tree(rig.prefix)
    run = rig.run(rig.installer(mode, version))
    assert run.returncode == 1, run.stderr
    assert rig.report() == ("restored", "1.0.1", why)
    assert _tree(rig.prefix) == before
    assert rig.leftovers() == []


@_windows_only
def test_the_windows_helper_never_runs_a_replaced_file(windows_rig: WindowsRig):
    rig = windows_rig
    rig.old_install()
    before = _tree(rig.prefix)
    run = rig.run(rig.installer("ok"), sha256="0" * 64)
    assert run.returncode == 1
    assert rig.report() == ("unchanged", "1.0.1", "the update file is not the verified file")
    assert _tree(rig.prefix) == before


def test_the_helpers_share_one_report_vocabulary():
    words = set()
    for name in ("update_handoff.sh", "update_handoff.ps1"):
        text = (_ASSETS / name).read_text(encoding="utf-8")
        words |= set(
            re.findall(r"(?:report|Write-Report) '?(updated|restored|unchanged|failed)", text)
        )
    assert words == {"updated", "restored", "unchanged", "failed"}

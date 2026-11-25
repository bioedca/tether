# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
r"""Hand a verified installer to the OS installer without orphaning the launch surface (#331).

This is the apply step of FR-UPDATE (PRD §7.12, ADR-0060 decision 1 and 6, ADR-0141). Its
only input is a [`tether.gui.update_verify.VerifiedInstaller`][], which only
[`tether.gui.update_verify.verify_update`][] can make, and only after consent was given and
every verification step passed. Nothing here downloads, and nothing here verifies provenance.

**Why a helper, and why the old copy is moved aside.** The installers refuse an existing
install folder on Windows and macOS (measured, ADR-0095: NSIS exits 2, the ``.pkg`` exits 1,
and nothing is changed), and the ``.sh`` installer accepts one only with ``-u``, which writes
over the running copy. So an update cannot simply run the new installer over the old folder.
[`hand_off`][] instead starts a small helper script that runs with the operating system's
own shell (``/bin/sh``, or Windows PowerShell), from a private folder outside the install, and
Tether then quits. The helper:

1. waits until Tether has exited;
2. hashes its private copy of the installer again and stops if it is not the verified file;
3. moves the install folder aside, to ``<prefix>.tether-update-backup`` (a rename, so it is
   instant and needs no extra disk space);
4. runs the installer, unattended, into the same folder;
5. asks the **new** copy to check its own launch surface: ``python -m
   tether.gui.update_handoff check`` (see [`check_launch_surface`][]);
6. on success deletes the backup; on any failure (the installer exits non-zero, is
   cancelled or killed, or the check finds a problem) deletes the partial install and moves
   the backup back, so the existing installation keeps working;
7. writes one line to the result file ([`read_update_result`][]) and starts Tether again.

**The Windows ordering is load-bearing.** A running Tether holds ``python.exe`` and its DLLs
in the install folder open, and Windows refuses to rename or replace an open file. So the
sequence is *launch the helper, then exit*: the helper is started first and waits for this
process to be gone before it touches the folder. The same order is used on every platform.

**The verified-to-executed window.** The verifier hashed the file at one instant
(ADR-0060's threat model, last but one row). [`hand_off`][] copies the file into a folder it
creates for this update (mode ``0700`` on macOS and Linux; on Windows inside the user's own
temporary folder) and hashes the bytes as it copies them; the copy is used only if that hash
equals [`VerifiedInstaller.sha256`][tether.gui.update_verify.VerifiedInstaller.sha256].
The helper hashes the copy once more immediately before it runs it. What is left is the time
between that last hash and the installer opening the file, inside a folder only this user can
write. A process that runs as this user can already change the installed program itself, so
this is the residual risk ADR-0141 states, not a gap this module could close.

**What can still break an install.** A power cut or a killed helper between step 3 and step 6
leaves the install folder missing and the backup in place. The user then renames
``<prefix>.tether-update-backup`` back to ``<prefix>``; ``docs/install.md`` says so.

**Where hand-off is offered.** Only an install the helper can replace without new rights:

============  ===============================================================================
Windows       a *Just Me* install (constructor writes ``.nonadmin`` into the folder)
macOS         the ``.pkg`` install into ``~/Tether``, the folder the package installs into
Linux         any folder whose parent this user can write
============  ===============================================================================

Elsewhere, and in a development checkout, [`hand_off`][] returns *unavailable* and changes
nothing; the user installs the update by hand.

The module imports only the standard library and Qt-free Tether modules, so the ``check``
command costs no Qt import in the newly installed copy.
"""

from __future__ import annotations

import argparse
import contextlib
import enum
import hashlib
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from tether.gui import app_settings
from tether.gui.update_check import Version, parse_version
from tether.gui.update_verify import VerifiedInstaller, channel_version

__all__ = [
    "BACKUP_SUFFIX",
    "CHECK_TIMEOUT_S",
    "EXIT_WAIT_TIMEOUT_S",
    "FAILED_SUFFIX",
    "HELPER_START_TIMEOUT_S",
    "INSTALL_TIMEOUT_S",
    "LOCK_SUFFIX",
    "RESULT_FILENAME",
    "HandoffOutcome",
    "HandoffRefused",
    "HandoffResult",
    "HandoffUnavailable",
    "UpdateReport",
    "Spawn",
    "check_install",
    "check_launch_surface",
    "copy_verified",
    "hand_off",
    "handoff_platform",
    "helper_command",
    "installer_command",
    "main",
    "read_update_result",
    "relaunch_command",
    "result_path",
]

HELPER_START_TIMEOUT_S = 15.0
"""Seconds Tether waits for the helper to report that it runs. If it does not, Tether stays
open and nothing is changed (PRD §11.2)."""

EXIT_WAIT_TIMEOUT_S = 120
"""Seconds the helper waits for Tether to exit before it gives up without changing anything
(PRD §11.2)."""

INSTALL_TIMEOUT_S = 3600
"""Seconds the installer may run before the helper stops it and restores the previous copy
(PRD §11.2)."""

CHECK_TIMEOUT_S = 120.0
"""Seconds each command of the launch-surface check may take (PRD §11.2)."""

BACKUP_SUFFIX = ".tether-update-backup"
"""Appended to the install folder's name while the update runs: the previous copy."""

FAILED_SUFFIX = ".tether-update-failed"
"""Appended to a partial install the helper could not delete before it restored the backup."""

LOCK_SUFFIX = ".tether-update-lock"
"""Appended to the install folder's name for the lock a helper holds while it runs.

A folder on macOS and Linux, a file on Windows; either is made in one step that fails when
it exists, so two helpers never change the same install at the same time.
"""

RESULT_FILENAME = "update-result.txt"
"""The helper's one-line report, beside the settings file (ADR-0095)."""

_HELPER_POSIX = "update_handoff.sh"
_HELPER_WINDOWS = "update_handoff.ps1"
_STARTED = "helper-started"
_HASH_CHUNK = 1024 * 1024
_SUFFIX = {"linux": ".sh", "macos": ".pkg", "windows": ".exe"}
_POWERSHELL = r"System32\WindowsPowerShell\v1.0\powershell.exe"
_START_MENU = Path("Microsoft") / "Windows" / "Start Menu" / "Programs" / "Tether"
_REPORT_LINE = re.compile(r"(updated|restored|unchanged|failed) (\S+)(?: (.*))?")


class HandoffOutcome(enum.Enum):
    """How [`hand_off`][] ended."""

    HANDED_OFF = "handed-off"
    """The helper runs and Tether was asked to quit; the result is read at the next start."""

    REFUSED = "refused"
    """The hand-off was refused; nothing was changed. Tell the user ``reason``."""

    UNAVAILABLE = "unavailable"
    """This copy cannot update itself (no consent, a development checkout, an install the
    helper cannot replace). Nothing was changed; the user installs the update by hand."""


class HandoffRefused(Exception):
    """A hand-off step failed. ``str()`` is a sentence the user can be shown."""


class HandoffUnavailable(Exception):
    """No hand-off is possible for this copy of Tether. ``str()`` says why, for the user."""


@dataclass(frozen=True)
class HandoffResult:
    """The outcome of [`hand_off`][]."""

    outcome: HandoffOutcome
    reason: str
    """A sentence for the user; for *handed-off*, what happens next."""


@dataclass(frozen=True)
class UpdateReport:
    """The helper's report of the last update, read by [`read_update_result`][]."""

    status: str
    """``updated``, ``restored`` (the installer failed and the previous copy was put back),
    ``unchanged`` (the helper stopped before it changed anything) or ``failed`` (the
    previous copy could not be put back; ``detail`` says where it is)."""

    version: str
    """The version the update was for."""

    detail: str
    """What happened, in a few words; empty for ``updated``."""


Spawn = Callable[[Sequence[str], Path], "subprocess.Popen[bytes]"]
"""Start ``argv`` detached from Tether, with the working folder given; return the process."""


# --- where hand-off is offered ----------------------------------------------------------


def handoff_platform(system: str | None = None) -> str | None:
    """Name the platform of ``system``: ``windows``, ``macos``, ``linux``, or ``None``.

    ``system`` defaults to `sys.platform`.
    """
    system = sys.platform if system is None else system
    if system.startswith("win"):
        return "windows"
    if system == "darwin":
        return "macos"
    if system.startswith("linux"):
        return "linux"
    return None


def check_install(prefix: Path, platform: str, *, home: Path | None = None) -> None:
    """Raise [`HandoffUnavailable`][] unless the helper can replace the install in ``prefix``.

    ``prefix`` is the install folder (`tether._verifier.install_prefix` for the
    running copy). It must be an absolute folder that holds an installed Tether
    (``envs/tether`` with ``conda-meta``, and the ``launchers`` folder), must not be the
    home folder or a drive root, and must have no backup beside it from an earlier update.
    Then per platform: a *Just Me* install on Windows, ``~/Tether`` on macOS, and on every
    platform a parent folder this user can write, since the helper renames the folder.
    """
    home = Path.home() if home is None else home
    if not prefix.is_absolute():
        raise HandoffUnavailable("Tether cannot find its own install folder.")
    resolved = Path(os.path.abspath(prefix))
    if resolved == Path(os.path.abspath(home)) or resolved.parent == resolved:
        raise HandoffUnavailable("Tether is not installed in a folder of its own.")
    if (
        not (prefix / "envs" / "tether" / "conda-meta").is_dir()
        or not (prefix / "launchers").is_dir()
    ):
        raise HandoffUnavailable("This copy of Tether was not installed by a Tether installer.")
    if platform == "windows" and not (prefix / ".nonadmin").is_file():
        raise HandoffUnavailable(
            "Tether was installed for all users, so an update needs an administrator."
        )
    if platform == "macos" and not _same_file(resolved, Path(os.path.abspath(home)) / "Tether"):
        raise HandoffUnavailable(
            "Tether can update itself only when it is installed in the Tether folder of your "
            "home folder."
        )
    if platform not in _SUFFIX:
        raise HandoffUnavailable("Tether cannot update itself on this operating system.")
    if not os.access(prefix.parent, os.W_OK):
        raise HandoffUnavailable("Tether cannot write to the folder that holds its install.")
    lock = prefix.with_name(prefix.name + LOCK_SUFFIX)
    if os.path.lexists(lock):
        raise HandoffUnavailable(
            f"An update of this copy of Tether is already running ({lock}). If none is, "
            "remove that and try again."
        )
    for suffix in (BACKUP_SUFFIX, FAILED_SUFFIX):
        leftover = prefix.with_name(prefix.name + suffix)
        if os.path.lexists(leftover):
            raise HandoffUnavailable(
                f"An earlier update left {leftover} behind. Remove it or rename it, then try again."
            )


# --- what the helper runs ---------------------------------------------------------------


def installer_command(installer: Path, prefix: Path, platform: str) -> list[str]:
    """Return the installer invocation for an unattended install into ``prefix``.

    Linux: ``/bin/sh <installer> -b -p <prefix>`` (batch mode; the folder no longer exists
    when it runs, so ``-u`` is not needed). macOS: ``/usr/sbin/installer -pkg <installer>
    -target CurrentUserHomeDirectory``, which installs into ``~/Tether``. Windows: the
    installer and its arguments ``/InstallationType=JustMe /S /D=<prefix>``; NSIS requires
    ``/D=`` last and unquoted, so the helper passes the arguments as one string.
    """
    if platform == "linux":
        return ["/bin/sh", str(installer), "-b", "-p", str(prefix)]
    if platform == "macos":
        return [
            "/usr/sbin/installer",
            "-pkg",
            str(installer),
            "-target",
            "CurrentUserHomeDirectory",
        ]
    if platform == "windows":
        return [str(installer), f"/InstallationType=JustMe /S /D={prefix}"]
    raise ValueError(f"no installer command for {platform!r}")


def relaunch_command(prefix: Path, platform: str) -> list[str]:
    """Return what starts Tether's GUI from ``prefix``: the entry point of the launch surface."""
    if platform == "windows":
        return [str(prefix / "envs" / "tether" / "Scripts" / "tether-gui.exe")]
    return [str(prefix / "launchers" / "tether-gui")]


def result_path(settings_file: Path | None = None) -> Path:
    """Return the helper's result file: [`RESULT_FILENAME`][] beside the settings file."""
    settings = app_settings.settings_path() if settings_file is None else settings_file
    return settings.parent / RESULT_FILENAME


def helper_command(
    helper: Path,
    *,
    platform: str,
    tether_pid: int,
    prefix: Path,
    installer: Path,
    sha256: str,
    version: str,
    result: Path,
    started: Path,
    installer_argv: Sequence[str],
    exit_timeout: int = EXIT_WAIT_TIMEOUT_S,
    install_timeout: int = INSTALL_TIMEOUT_S,
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    """Return the command that runs the helper script ``helper``.

    POSIX: ``/bin/sh helper`` with nine positional arguments, ``--``, and then the
    installer's argv. Windows: Windows PowerShell by its absolute path (a bare name is not
    on the ``PATH`` of every process, issue #551), ``-ExecutionPolicy Bypass`` for this one
    process, and named parameters. A site whose Group Policy forbids that keeps the helper
    from starting, and [`hand_off`][] then refuses with nothing changed.
    """
    if platform == "windows":
        env = os.environ if environ is None else environ
        system_root = env.get("SystemRoot") or env.get("SYSTEMROOT") or r"C:\Windows"
        program, arguments = installer_argv
        return [
            str(Path(system_root) / _POWERSHELL),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            str(helper),
            "-TetherPid",
            str(tether_pid),
            "-Prefix",
            str(prefix),
            "-Installer",
            program,
            "-InstallerArguments",
            arguments,
            "-Sha256",
            sha256,
            "-Version",
            version,
            "-Result",
            str(result),
            "-Started",
            str(started),
            "-ExitTimeout",
            str(int(exit_timeout)),
            "-InstallTimeout",
            str(int(install_timeout)),
        ]
    return [
        "/bin/sh",
        str(helper),
        str(tether_pid),
        str(prefix),
        str(installer),
        sha256,
        version,
        str(result),
        str(started),
        str(int(exit_timeout)),
        str(int(install_timeout)),
        "--",
        *installer_argv,
    ]


def _helper_source(platform: str) -> bytes:
    name = _HELPER_WINDOWS if platform == "windows" else _HELPER_POSIX
    return resources.files("tether.gui").joinpath("assets", "update", name).read_bytes()


# --- closing the verified-to-executed window ------------------------------------------------


def copy_verified(installer: VerifiedInstaller, workdir: Path) -> Path:
    """Copy the verified file into ``workdir``, hashing the bytes as they are copied.

    Returns the copy, which has the original's name. Raises [`HandoffRefused`][] when the
    bytes read do not hash to the verified SHA-256 (the file was changed after it was
    verified), or when the file cannot be read; the copy
    is then removed. The source is opened without following a symbolic link where the
    platform can do that, and the copy is created new, readable and writable by this user
    only.
    """
    source = installer.path
    target = workdir / source.name
    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(source, flags)
    except OSError as error:
        raise HandoffRefused("The downloaded update could not be read.") from error
    try:
        with (
            os.fdopen(fd, "rb") as reader,
            open(  # noqa: PTH123 - opener sets the mode of a file created new
                target,
                "xb",
                opener=lambda path, f: os.open(path, f | getattr(os, "O_BINARY", 0), 0o600),
            ) as writer,
        ):
            while chunk := reader.read(_HASH_CHUNK):
                digest.update(chunk)
                writer.write(chunk)
    except OSError as error:
        target.unlink(missing_ok=True)
        raise HandoffRefused("The downloaded update could not be copied.") from error
    if digest.hexdigest() != installer.sha256:
        target.unlink(missing_ok=True)
        raise HandoffRefused(
            "The downloaded update changed after it was verified, so it was not installed."
        )
    return target


# --- the hand-off -------------------------------------------------------------------------


def _spawn(argv: Sequence[str], cwd: Path) -> subprocess.Popen[bytes]:
    """Start the helper detached from Tether: the default [`Spawn`][]."""
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess, "CREATE_NO_WINDOW", 0
        )
        return subprocess.Popen(  # noqa: S603 - argv is built here, never from input
            list(argv),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            close_fds=True,
        )
    return subprocess.Popen(  # noqa: S603 - argv is built here, never from input
        list(argv),
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )


def hand_off(
    installer: VerifiedInstaller,
    *,
    prefix: Path,
    quit_app: Callable[[], None],
    settings_file: Path | None = None,
    override_file: Path | None = None,
    platform: str | None = None,
    home: Path | None = None,
    workroot: Path | None = None,
    spawn: Spawn = _spawn,
    tether_pid: int | None = None,
    start_timeout: float = HELPER_START_TIMEOUT_S,
) -> HandoffResult:
    """Hand ``installer`` to the OS installer and ask Tether to quit; never raises.

    ``installer`` must be a [`VerifiedInstaller`][tether.gui.update_verify.VerifiedInstaller]
    (anything else is refused, so nothing unverified reaches here), and the update check
    must still be *allowed* in the settings store (``settings_file`` and ``override_file``
    go to [`tether.gui.app_settings.update_check_state`][]). ``prefix`` is the install
    folder; the caller passes `tether._verifier.install_prefix` because this GUI module
    does not import the core. ``platform`` defaults to [`handoff_platform`][] and ``home`` to
    the home folder; ``workroot`` is the parent of the private folder (the system's temporary
    folder by default). ``spawn`` and ``tether_pid`` are for tests.

    In order: the checks above and [`check_install`][]; a private folder and
    [`copy_verified`][]; the helper script written beside the copy; **the helper started**;
    then a wait of at most ``start_timeout`` seconds for the helper to report that it runs;
    then, and only then, ``quit_app()``. If the helper does not report in time it is stopped,
    the private folder is removed, Tether stays open and the result is *refused*.

    Blocks for at most ``start_timeout`` seconds plus the time to copy the installer.
    ``quit_app`` is called on the calling thread.
    """
    try:
        return _hand_off(
            installer,
            prefix=prefix,
            quit_app=quit_app,
            settings_file=settings_file,
            override_file=override_file,
            platform=platform,
            home=home,
            workroot=workroot,
            spawn=spawn,
            tether_pid=tether_pid,
            start_timeout=start_timeout,
        )
    except HandoffUnavailable as error:
        return HandoffResult(HandoffOutcome.UNAVAILABLE, str(error))
    except HandoffRefused as error:
        return HandoffResult(HandoffOutcome.REFUSED, str(error))
    except Exception as error:  # noqa: BLE001 - unclassifiable: refuse, change nothing
        return HandoffResult(
            HandoffOutcome.REFUSED, f"The update could not be started ({type(error).__name__})."
        )


def _hand_off(
    installer: VerifiedInstaller,
    *,
    prefix: Path,
    quit_app: Callable[[], None],
    settings_file: Path | None,
    override_file: Path | None,
    platform: str | None,
    home: Path | None,
    workroot: Path | None,
    spawn: Spawn,
    tether_pid: int | None,
    start_timeout: float,
) -> HandoffResult:
    # Only the verifier makes a VerifiedInstaller, and only after consent and every check
    # passed; type() and not isinstance(), so no subclass stands in for one.
    if type(installer) is not VerifiedInstaller:
        raise HandoffRefused("The update was not verified, so it was not installed.")
    state = app_settings.update_check_state(settings_file, override_file)
    if not state.network_allowed:
        raise HandoffUnavailable("Updates are turned off on this computer.")
    platform = handoff_platform() if platform is None else platform
    if platform is None:
        raise HandoffUnavailable("Tether cannot update itself on this operating system.")
    triple = channel_version(installer.version)
    if triple is None or not installer.path.name.endswith(_SUFFIX.get(platform, "\0")):
        raise HandoffRefused("The update is not this platform's installer.")
    check_install(prefix, platform, home=home)

    workdir = Path(tempfile.mkdtemp(prefix="tether-update-", dir=workroot))
    try:
        if os.name == "posix":
            workdir.chmod(0o700)
        copy = copy_verified(installer, workdir)
        helper = workdir / (_HELPER_WINDOWS if platform == "windows" else _HELPER_POSIX)
        helper.write_bytes(_helper_source(platform))
        started = workdir / _STARTED
        result = result_path(settings_file)
        result.parent.mkdir(parents=True, exist_ok=True)
        result.unlink(missing_ok=True)
        version = "{}.{}.{}".format(*triple)
        argv = helper_command(
            helper,
            platform=platform,
            tether_pid=os.getpid() if tether_pid is None else tether_pid,
            prefix=prefix,
            installer=copy,
            sha256=installer.sha256,
            version=version,
            result=result,
            started=started,
            installer_argv=installer_command(copy, prefix, platform),
        )
        # Launch, THEN exit. The helper must be running before Tether quits, and it
        # touches the install folder only after this process is gone: on Windows a running
        # Tether keeps python.exe and its DLLs in the folder open, and an open file can be
        # neither renamed nor replaced.
        process = spawn(argv, workdir)
    except BaseException:
        shutil.rmtree(workdir, ignore_errors=True)
        raise

    deadline = time.monotonic() + start_timeout
    while not started.exists():
        if process.poll() is not None or time.monotonic() >= deadline:
            if process.poll() is None:
                process.kill()
                process.wait()
            shutil.rmtree(workdir, ignore_errors=True)
            raise HandoffRefused(
                "Tether could not start the update, so nothing was changed. Install the "
                "update by hand."
            )
        time.sleep(0.05)

    quit_app()
    return HandoffResult(
        HandoffOutcome.HANDED_OFF,
        f"Tether will close, install {version} and start again. This can take some minutes.",
    )


# --- after the installer: the launch surface ---------------------------------------------------


def _run_bounded(
    argv: Sequence[str] | str, env: Mapping[str, str] | None, timeout: float = CHECK_TIMEOUT_S
) -> tuple[int, str] | None:
    """Run *argv* for at most *timeout* seconds; ``(exit status, output)``, or None.

    A string is a Windows command line, passed on as it is written.

    A launcher is a ``cmd /c`` or ``sh`` script, so the program that might hang is a child
    of the process started here. On a timeout the whole tree is stopped, and the output goes
    to a file rather than a pipe, so that nothing it started can hold this check open.
    """
    windows = os.name == "nt"
    with tempfile.TemporaryFile() as out:
        try:
            process = subprocess.Popen(  # noqa: S603 - argv is built here, never from input
                argv if isinstance(argv, str) else list(argv),
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.DEVNULL,
                env=None if env is None else dict(env),
                start_new_session=not windows,
            )
        except OSError:
            return None
        try:
            status = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _stop_tree(process, windows=windows, env=env)
            return None
        out.seek(0)
        return status, out.read().decode("utf-8", errors="replace")


def _stop_tree(
    process: subprocess.Popen[bytes], *, windows: bool, env: Mapping[str, str] | None
) -> None:
    """Stop *process* and everything it started, then reap it."""
    if windows:
        environ = os.environ if env is None else env
        system_root = environ.get("SystemRoot") or environ.get("SYSTEMROOT") or r"C:\Windows"
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(  # noqa: S603 - argv is built here, never from input
                [
                    str(Path(system_root) / "System32" / "taskkill.exe"),
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
            )
    else:
        with contextlib.suppress(OSError):
            os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(OSError):
        process.kill()
    with contextlib.suppress(subprocess.SubprocessError):
        process.wait(timeout=30)


def _run_version(argv: Sequence[str] | str, env: Mapping[str, str] | None) -> Version | None:
    ran = _run_bounded(argv, env)
    if ran is None:
        return None
    status, output = ran
    words = output.split()
    if status != 0 or len(words) < 2 or words[-2] != "tether":
        return None
    return parse_version(words[-1])


def _same_path(a: str | Path, b: str | Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _same_file(a: str | Path, b: str | Path) -> bool:
    """Tell whether *a* and *b* name one file, or one folder.

    The strings alone are not enough on macOS: the ``.pkg`` installs into ``~/tether`` on
    a file system that ignores case, so ``~/Tether`` names the same folder.
    """
    try:
        return os.path.samefile(a, b)
    except OSError:
        return _same_path(a, b)


def _shim_target(shim: Path) -> str | None:
    """Return the program a POSIX launcher ``exec``s (``exec "<program>" "$@"``), or None."""
    try:
        text = shim.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("exec "):
            try:
                words = shlex.split(line)
            except ValueError:
                return None
            return words[1] if len(words) == 3 and words[2] == "$@" else None
    return None


def _windows_shortcut(link: Path, env: Mapping[str, str]) -> tuple[str, str] | None:
    """``(target, arguments)`` of a ``.lnk``, read back through ``WScript.Shell``."""
    system_root = env.get("SystemRoot") or env.get("SYSTEMROOT") or r"C:\Windows"
    child = dict(env)
    child["TETHER_LNK"] = str(link)
    ran = _run_bounded(
        [
            str(Path(system_root) / _POWERSHELL),
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:TETHER_LNK);"
            '"$($s.TargetPath)|$($s.Arguments)"',
        ],
        child,
    )
    if ran is None or ran[0] != 0:
        return None
    target, _, arguments = ran[1].strip().partition("|")
    return target, arguments


def check_launch_surface(
    prefix: Path,
    version: str,
    *,
    platform: str | None = None,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    """Return what is wrong with the launch surface of the install in ``prefix``; empty if right.

    Every element of the ADR-0051 launch surface must exist, must start ``prefix``'s own
    environment, and must start version ``version``:

    * both launchers in ``<prefix>/launchers``, each forwarding into ``<prefix>/envs/tether``;
      ``tether --version`` run through the launcher reports ``version``;
    * macOS and Linux: ``envs/tether/bin/tether-gui --version`` reports ``version``
      (it answers before any Qt import, ADR-0051);
    * Windows: ``envs/tether/Scripts/tether-gui.exe`` exists, and the Start Menu
      ``Tether.lnk`` targets it; ``Tether Command Prompt.lnk`` opens ``<prefix>/tether-prompt.bat``;
    * Linux: ``~/.local/share/applications/tether.desktop`` starts
      ``<prefix>/envs/tether/bin/tether-gui``;
    * macOS: the ``Tether.app`` of ADR-0091 passes
      [`tether.gui.macos_app.check_bundle`][] for ``prefix``.

    The helper runs this in the **newly installed** copy, so it checks what that copy wrote.
    """
    platform = handoff_platform() if platform is None else platform
    home = Path.home() if home is None else home
    env = os.environ if environ is None else environ
    want = parse_version(version)
    if want is None:
        return [f"{version!r} is not a version"]
    problems: list[str] = []
    launchers = prefix / "launchers"
    env_dir = prefix / "envs" / "tether"
    if platform == "windows":
        scripts = env_dir / "Scripts"
        # cmd.exe writes the launchers in a code page of its own, so a non-ASCII install
        # folder cannot be matched as text; running tether.bat below is the real check, and
        # a launcher whose path no longer exists cannot pass it.
        for name in ("tether", "tether-gui"):
            bat = launchers / f"{name}.bat"
            text = (
                bat.read_bytes().decode("ascii", errors="replace").lower() if bat.is_file() else ""
            )
            if f"\\scripts\\{name}.exe".lower() not in text:
                problems.append(f"{bat} does not start {scripts / (name + '.exe')}")
        system_root = env.get("SystemRoot") or env.get("SYSTEMROOT") or r"C:\Windows"
        cmd = str(Path(system_root) / "System32" / "cmd.exe")
        # cmd /c drops the first and the last quote of what follows it, so a quoted path
        # with arguments needs one more pair around it all; /s makes that rule the only one.
        # Without it an install folder with a space in its name fails this check (#331).
        line = f'"{cmd}" /d /s /c ""{launchers / "tether.bat"}" --version"'
        got = _run_version(line, env)
        if got != want:
            problems.append(f"{launchers / 'tether.bat'} --version does not report {version}")
        gui = scripts / "tether-gui.exe"
        if not gui.is_file():
            problems.append(f"the GUI entry point is missing: {gui}")
        appdata = env.get("APPDATA")
        if not appdata:
            problems.append("APPDATA is not set, so the Start Menu cannot be checked")
        else:
            menu = Path(appdata) / _START_MENU
            link = _windows_shortcut(menu / "Tether.lnk", env)
            if link is None or not _same_path(link[0], gui):
                problems.append(f"{menu / 'Tether.lnk'} does not start {gui}")
            prompt = _windows_shortcut(menu / "Tether Command Prompt.lnk", env)
            if prompt is None or str(prefix / "tether-prompt.bat").lower() not in prompt[1].lower():
                problems.append(f"{menu / 'Tether Command Prompt.lnk'} does not open this install")
        return problems

    for name in ("tether", "tether-gui"):
        shim = launchers / name
        target = env_dir / "bin" / name
        started = _shim_target(shim) if shim.is_file() else None
        if not os.access(shim, os.X_OK) or started is None or not _same_file(started, target):
            problems.append(f"{shim} does not start {target}")
    if _run_version([str(launchers / "tether"), "--version"], env) != want:
        problems.append(f"{launchers / 'tether'} --version does not report {version}")
    if _run_version([str(env_dir / "bin" / "tether-gui"), "--version"], env) != want:
        problems.append(f"{env_dir / 'bin' / 'tether-gui'} --version does not report {version}")
    if platform == "linux":
        entry = home / ".local" / "share" / "applications" / "tether.desktop"
        gui = env_dir / "bin" / "tether-gui"
        text = entry.read_text(encoding="utf-8", errors="replace") if entry.is_file() else ""
        execs = [
            line[len('Exec="') : -len('" %f')]
            for line in text.splitlines()
            if line.startswith('Exec="') and line.endswith('" %f')
        ]
        if not any(_same_file(program, gui) for program in execs):
            problems.append(f"{entry} does not start {gui}")
    if platform == "macos":
        from tether.gui import macos_app

        bundle = macos_app.applications_dir(prefix, home) / macos_app.BUNDLE_NAME
        problems.extend(macos_app.check_bundle(bundle, prefix))
    return problems


# --- the result, at the next start --------------------------------------------------------------


def read_update_result(path: Path | None = None) -> UpdateReport | None:
    """Read the helper's report of the last update, or ``None`` when there is none.

    ``path`` defaults to [`result_path`][]. A missing, unreadable or malformed file is
    ``None``. Reading never removes the file; the caller does, once the user was told.
    """
    path = result_path() if path is None else path
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    match = _REPORT_LINE.fullmatch(text.strip())
    if match is None:
        return None
    return UpdateReport(match[1], match[2], match[3] or "")


# --- the command line the helper calls -----------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m tether.gui.update_handoff check --prefix P --version V``; 0 when all is right.

    The helper runs it with the newly installed interpreter after the installer exited 0.
    Each problem is printed on its own line, and the exit status is 1 when there is one.
    """
    parser = argparse.ArgumentParser(prog="python -m tether.gui.update_handoff")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="check an install's launch surface")
    check.add_argument("--prefix", type=Path, required=True)
    check.add_argument("--version", required=True)
    args = parser.parse_args(argv)
    problems = check_launch_surface(args.prefix, args.version)
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
r"""The application settings store: two answers that survive an update (ADR-0095, ADR-0123).

Tether keeps almost every setting inside a ``.tether`` project. The update-check consent
of ADR-0060 decision 3 cannot live there: it must be readable at startup, before any
project is open and before any network call is possible, and it must survive a
re-install. This module is the store for that answer and for one display preference,
the trace colors of #636 (ADR-0123), and deliberately nothing more. It is not a
preferences framework, it holds no analysis parameter (those belong in the project
store, PRD §5), and the batch runner does not read it.

**Where it lives.** Outside the install folder, in the user's own per-user folder, so
that what an installer does to its prefix cannot matter (ADR-0095 records the measured
per-OS behaviour that this choice makes irrelevant):

============  ==========================================================================
Windows       ``%LOCALAPPDATA%\MondragonLab\Tether\settings.json``
macOS         ``~/Library/Application Support/MondragonLab/Tether/settings.json``
Linux         ``$XDG_CONFIG_HOME/tether/settings.json`` (``~/.config/tether/`` by default)
============  ==========================================================================

**Three precedence levels**, lowest first, with the highest present one deciding:

1. *Unset* — no file, an unreadable file, or a value this version does not know. No
   network request is allowed and the first-run prompt is pending.
2. *The user's answer* — ``allowed`` or ``declined``, written by
   [`record_update_consent`][].
3. *The machine-wide administrator override* — a file named ``disable-update-check``
   in the machine folder below. Its presence alone disables the update check for every
   user of the machine, whatever they answered. It can only disable; nothing here lets
   an administrator turn checking on for a user who has not agreed.

============  ==========================================================================
Windows       ``%PROGRAMDATA%\MondragonLab\Tether\disable-update-check``
macOS         ``/Library/Application Support/MondragonLab/Tether/disable-update-check``
Linux         ``/etc/tether/disable-update-check``
============  ==========================================================================

**What it stores.** The file holds ``schema`` (the integer 1) and at most two answers:
``update_check`` (``"allowed"`` or ``"declined"``) and ``trace_colors`` (``"safe"`` or
``"classic"``, ADR-0123). No time, no identifier, no user name, no path and no project
data is written. [`record_update_consent`][] and [`record_trace_colors`][] are the only
writers; each keeps the other's answer when it is one this version wrote, and drops
anything else.

**Writes take turns** (#1254). A write reads the file, sets its answer and replaces the
file while it holds an operating-system lock on an empty file beside it,
``settings.json.lock``: the lock of ``preferences.json`` (#1171, ADR-0122), the same code.
So two Tether instances that write at the same moment cannot drop each other's answer. The
lock belongs to the open handle, so it ends when an instance exits or crashes, and the file
it leaves behind blocks nothing. A write that cannot take it within a few seconds fails with
`OSError`. A write also fails with `OSError`, and leaves the file as it was, when the file
exists but cannot be read: carrying on would replace it without the answer it could not
read. Reading takes no lock.

**Every failure is off.** A missing, empty, truncated or foreign file reads as *unset*,
never as an error and never as consent. So does a path that is not a regular file (a
folder, a FIFO or a device): it is opened without waiting and never read. An override
file that exists but cannot be examined counts as present. Reading never creates a file
or a folder, and never raises. An unset trace color answer means the default, ``safe``.
Writing the trace colors never writes a consent answer that was not already stored.

The module imports only the standard library, so a read at startup costs one small
file read and no Qt, NumPy or HDF5 import.
"""

from __future__ import annotations

import contextlib
import enum
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

__all__ = [
    "OVERRIDE_FILENAME",
    "SETTINGS_FILENAME",
    "SETTINGS_SCHEMA",
    "TRACE_COLORS",
    "UpdateCheckState",
    "machine_override_path",
    "record_trace_colors",
    "record_update_consent",
    "settings_path",
    "stored_trace_colors",
    "update_check_state",
]

SETTINGS_FILENAME = "settings.json"
"""The name of the per-user settings file."""

OVERRIDE_FILENAME = "disable-update-check"
"""The name of the machine-wide administrator override file."""

SETTINGS_SCHEMA = 1
"""The ``schema`` value this version writes and reads."""

_KEY = "update_check"
_ALLOWED = "allowed"
_DECLINED = "declined"

_TRACE_KEY = "trace_colors"
TRACE_COLORS = ("safe", "classic")
"""The trace-color answers ``trace_colors`` may hold: the names of
``tether.gui.theme.TRACE_PALETTES`` (a test keeps the two equal). Named here so this
module imports nothing from the GUI."""

# The organization and application names the GUI gives Qt (tether.gui.app), so the
# folder name matches the AppUserModelID ``MondragonLab.Tether``. On Windows and macOS
# the organization level also keeps the folder apart from a ``Tether`` install folder a
# user might choose under the same parent.
_ORG = "MondragonLab"
_APP = "Tether"

# Files larger than this are not a settings file this module wrote; they read as unset
# rather than being parsed.
_MAX_BYTES = 4096

# Read-only, never waiting on a FIFO or a device and never taking a terminal as the
# controlling one. O_NONBLOCK and O_NOCTTY do not exist on Windows, where no FIFO can
# sit at a file path; O_BINARY exists only there.
_READ_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_NOCTTY", 0)
    | getattr(os, "O_BINARY", 0)
)


class UpdateCheckState(enum.Enum):
    """The effective update-check consent, after every precedence level is applied."""

    PENDING = "pending"
    """No answer is stored. No network request; the first-run prompt is pending."""

    ALLOWED = "allowed"
    """The user agreed to the update check."""

    DECLINED = "declined"
    """The user declined the update check."""

    DISABLED_BY_ADMINISTRATOR = "disabled-by-administrator"
    """The machine-wide override is present. No network request and no prompt."""

    @property
    def network_allowed(self) -> bool:
        """True only for [`ALLOWED`][]; every other state forbids a request."""
        return self is UpdateCheckState.ALLOWED

    @property
    def prompt_pending(self) -> bool:
        """True only for [`PENDING`][]; the override suppresses the prompt too."""
        return self is UpdateCheckState.PENDING


def _platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _env_dir(name: str) -> Path | None:
    """Return an environment folder, or ``None`` when it is unset, empty or relative."""
    value = os.environ.get(name, "")
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else None


def settings_path() -> Path:
    """Return the per-user settings file. The file and its folder may not exist."""
    platform = _platform()
    if platform == "windows":
        base = _env_dir("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        return base / _ORG / _APP / SETTINGS_FILENAME
    if platform == "macos":
        return Path.home() / "Library" / "Application Support" / _ORG / _APP / SETTINGS_FILENAME
    # The XDG Base Directory specification says a relative XDG_CONFIG_HOME is invalid
    # and must be ignored, which _env_dir does.
    base = _env_dir("XDG_CONFIG_HOME") or Path.home() / ".config"
    return base / "tether" / SETTINGS_FILENAME


def machine_override_path() -> Path:
    """Return the machine-wide administrator override file. It may not exist."""
    platform = _platform()
    if platform == "windows":
        base = _env_dir("PROGRAMDATA") or Path("C:/ProgramData")
        return base / _ORG / _APP / OVERRIDE_FILENAME
    if platform == "macos":
        return Path("/Library/Application Support") / _ORG / _APP / OVERRIDE_FILENAME
    return Path("/etc/tether") / OVERRIDE_FILENAME


def _override_present(path: Path) -> bool:
    """Whether the override is in force. Only a clear *no such file* means absent."""
    try:
        path.stat()
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        # The file may be there and unreadable to this user (a locked-down folder).
        # The override can only disable, so an uncertain answer is the safe one.
        return True
    return True


def _stored(path: Path, *, strict: bool = False) -> dict[str, str]:
    """Return the answers of a file this version wrote, each one valid; else ``{}``.

    A reader passes ``strict=False`` and never sees an error. A writer passes ``strict=True``
    (#1254): then only a missing file reads as ``{}``, and a file that is there but cannot be
    opened, examined or read raises its `OSError`, so the write stops before it can replace
    the file and drop the answer it did not read. A file that reads but is not one this version
    wrote is still ``{}`` either way.
    """
    try:
        # A non-blocking open, then a check on the open descriptor: a FIFO opens at once
        # instead of waiting for a writer (#1116), and the file tested is the file read,
        # so nothing swapped in after the check can be read.
        fd = os.open(path, _READ_FLAGS)
    except FileNotFoundError:
        return {}
    except OSError:
        if strict:
            raise
        return {}
    try:
        handle = os.fdopen(fd, "rb") if stat.S_ISREG(os.fstat(fd).st_mode) else None
    except OSError:
        # The descriptor could not be examined or wrapped; it is still ours to close
        # (os.fdopen does not close it on failure).
        with contextlib.suppress(OSError):
            os.close(fd)
        if strict:
            raise
        return {}
    if handle is None:
        # Not a regular file: nothing this version wrote, and nothing to read.
        with contextlib.suppress(OSError):
            os.close(fd)
        return {}
    try:
        with handle:
            raw = handle.read(_MAX_BYTES + 1)
    except OSError:
        if strict:
            raise
        return {}
    if len(raw) > _MAX_BYTES:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        # RecursionError: deeply nested JSON that fits in _MAX_BYTES still exceeds the
        # decoder's nesting limit on Python 3.11.
        return {}
    if not isinstance(data, dict):
        return {}
    schema = data.get("schema")
    # An exact int: JSON true and 1.0 compare equal to 1 in Python but are not the format
    # this module writes.
    if type(schema) is not int or schema != SETTINGS_SCHEMA:
        return {}
    # Each answer is judged on its own, so a bad value of one key never spoils the other.
    answers: dict[str, str] = {}
    if data.get(_KEY) in (_ALLOWED, _DECLINED):
        answers[_KEY] = data[_KEY]
    if data.get(_TRACE_KEY) in TRACE_COLORS:
        answers[_TRACE_KEY] = data[_TRACE_KEY]
    return answers


def _stored_answer(path: Path) -> str | None:
    """Return the stored ``update_check`` value, or ``None`` for anything that is not one."""
    return _stored(path).get(_KEY)


def update_check_state(
    settings_file: Path | None = None, override_file: Path | None = None
) -> UpdateCheckState:
    """Read the effective update-check consent. Never raises for a bad or missing file.

    ``settings_file`` and ``override_file`` default to [`settings_path`][] and
    [`machine_override_path`][]; tests pass their own.
    """
    override = override_file if override_file is not None else machine_override_path()
    if _override_present(override):
        return UpdateCheckState.DISABLED_BY_ADMINISTRATOR
    answer = _stored_answer(settings_file if settings_file is not None else settings_path())
    if answer == _ALLOWED:
        return UpdateCheckState.ALLOWED
    if answer == _DECLINED:
        return UpdateCheckState.DECLINED
    return UpdateCheckState.PENDING


def record_update_consent(allowed: bool, settings_file: Path | None = None) -> Path:
    """Store the user's answer to the first-run prompt and return the file written.

    The write is atomic: a temporary file in the same folder replaces the old one, so a
    crash mid-write leaves either the old answer or the new one, never a torn file. The
    answer is stored even when the administrator override is present; the override
    still decides the effective state. Writes from two instances take turns, and a file
    that exists but cannot be read, or a lock another instance holds for more than a few
    seconds, fails the write with `OSError` and leaves the file as it was (#1254).
    """
    if not isinstance(allowed, bool):
        raise TypeError(f"allowed must be a bool, not {type(allowed).__name__}")
    path = settings_file if settings_file is not None else settings_path()
    return _write(path, _KEY, _ALLOWED if allowed else _DECLINED)


def stored_trace_colors(settings_file: Path | None = None) -> str | None:
    """Return the stored trace-color answer ([`TRACE_COLORS`][]), or ``None`` when unset.

    ``None`` means the default palette. Like [`update_check_state`][] it never raises for
    a bad or missing file and never creates one.
    """
    return _stored(settings_file if settings_file is not None else settings_path()).get(_TRACE_KEY)


def record_trace_colors(name: str, settings_file: Path | None = None) -> Path:
    """Store the **View → Trace colors** choice (``"safe"`` or ``"classic"``); return the file.

    The write is atomic, as for [`record_update_consent`][], and keeps a stored consent
    answer as it is. It never writes one that was not there: a file without a valid
    answer still reads as *pending* afterwards.
    """
    if name not in TRACE_COLORS:
        raise ValueError(f"trace colors must be one of {TRACE_COLORS}, got {name!r}")
    path = settings_file if settings_file is not None else settings_path()
    return _write(path, _TRACE_KEY, name)


def _write(path: Path, key: str, value: str) -> Path:
    """Set ``key`` to ``value`` atomically, keeping the file's other valid answers.

    The read and the replace hold the cross-process lock of
    [`tether.gui.preferences`][tether.gui.preferences] on ``settings.json.lock`` (#1254, the
    mechanism of #1171), so a write from another instance is never overwritten by one that
    read the file before it landed. A file that exists but cannot be read fails the write
    with `OSError` and is left as it was.
    """
    # Deferred, so that a read at startup imports nothing beyond this module; the
    # preferences module is standard-library only as well.
    from tether.gui.preferences import _locked  # noqa: PLC0415

    path.parent.mkdir(parents=True, exist_ok=True)
    with _locked(path):
        _write_locked(path, key, value)
    return path


def _write_locked(path: Path, key: str, value: str) -> None:
    answers = _stored(path, strict=True)
    answers[key] = value
    # A fixed key order, so the same answers always give the same bytes.
    payload = json.dumps(
        {"schema": SETTINGS_SCHEMA} | {k: answers[k] for k in (_KEY, _TRACE_KEY) if k in answers},
        indent=2,
    )
    fd, tmp_name = tempfile.mkstemp(prefix=".settings-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise

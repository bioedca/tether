# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The application settings store: one consent answer that survives an update (ADR-0094).

Tether keeps almost every setting inside a ``.tether`` project. The update-check consent
of ADR-0060 decision 3 cannot live there: it must be readable at startup, before any
project is open and before any network call is possible, and it must survive a
re-install. This module is the store for that one answer, and deliberately nothing
more. It is not a preferences framework, it holds no analysis parameter (those belong
in the project store, PRD §5), and the batch runner does not read it.

**Where it lives.** Outside the install folder, in the user's own per-user folder, so
that what an installer does to its prefix cannot matter (ADR-0094 records the measured
per-OS behaviour that this choice makes irrelevant):

============  ==========================================================================
Windows       ``%LOCALAPPDATA%\\MondragonLab\\Tether\\settings.json``
macOS         ``~/Library/Application Support/MondragonLab/Tether/settings.json``
Linux         ``$XDG_CONFIG_HOME/tether/settings.json`` (``~/.config/tether/`` by default)
============  ==========================================================================

**Three precedence levels**, lowest first, with the highest present one deciding:

1. *Unset* — no file, an unreadable file, or a value this version does not know. No
   network request is allowed and the first-run prompt is pending.
2. *The user's answer* — ``allowed`` or ``declined``, written by
   :func:`record_update_consent`.
3. *The machine-wide administrator override* — a file named ``disable-update-check``
   in the machine folder below. Its presence alone disables the update check for every
   user of the machine, whatever they answered. It can only disable; nothing here lets
   an administrator turn checking on for a user who has not agreed.

============  ==========================================================================
Windows       ``%PROGRAMDATA%\\MondragonLab\\Tether\\disable-update-check``
macOS         ``/Library/Application Support/MondragonLab/Tether/disable-update-check``
Linux         ``/etc/tether/disable-update-check``
============  ==========================================================================

**What it stores.** The file holds exactly two keys, ``schema`` (the integer 1) and
``update_check`` (``"allowed"`` or ``"declined"``). No time, no identifier, no user
name, no path and no project data is written, and :func:`record_update_consent` is the
only writer.

**Every failure is off.** A missing, empty, truncated or foreign file reads as *unset*,
never as an error and never as consent. An override file that exists but cannot be
examined counts as present. Reading never creates a file or a folder.

The module imports only the standard library, so a read at startup costs one small
file read and no Qt, NumPy or HDF5 import.
"""

from __future__ import annotations

import enum
import json
import os
import sys
import tempfile
from pathlib import Path

__all__ = [
    "OVERRIDE_FILENAME",
    "SETTINGS_FILENAME",
    "SETTINGS_SCHEMA",
    "UpdateCheckState",
    "machine_override_path",
    "record_update_consent",
    "settings_path",
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

# The organization and application names the GUI gives Qt (tether.gui.app), so the
# folder name matches the AppUserModelID ``MondragonLab.Tether``. On Windows and macOS
# the organization level also keeps the folder apart from a ``Tether`` install folder a
# user might choose under the same parent.
_ORG = "MondragonLab"
_APP = "Tether"

# Files larger than this are not a settings file this module wrote; they read as unset
# rather than being parsed.
_MAX_BYTES = 4096


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
        """True only for :attr:`ALLOWED`; every other state forbids a request."""
        return self is UpdateCheckState.ALLOWED

    @property
    def prompt_pending(self) -> bool:
        """True only for :attr:`PENDING`; the override suppresses the prompt too."""
        return self is UpdateCheckState.PENDING


def _platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _env_dir(name: str) -> Path | None:
    """An environment folder, or ``None`` when it is unset, empty or relative."""
    value = os.environ.get(name, "")
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else None


def settings_path() -> Path:
    """The per-user settings file. The file and its folder may not exist."""
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
    """The machine-wide administrator override file. It may not exist."""
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


def _stored_answer(path: Path) -> str | None:
    """The stored ``update_check`` value, or ``None`` for anything that is not one."""
    try:
        with path.open("rb") as handle:
            raw = handle.read(_MAX_BYTES + 1)
    except OSError:
        return None
    if len(raw) > _MAX_BYTES:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != SETTINGS_SCHEMA:
        return None
    value = data.get(_KEY)
    return value if value in (_ALLOWED, _DECLINED) else None


def update_check_state(
    settings_file: Path | None = None, override_file: Path | None = None
) -> UpdateCheckState:
    """Read the effective update-check consent. Never raises for a bad or missing file.

    ``settings_file`` and ``override_file`` default to :func:`settings_path` and
    :func:`machine_override_path`; tests pass their own.
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
    still decides the effective state.
    """
    if not isinstance(allowed, bool):
        raise TypeError(f"allowed must be a bool, not {type(allowed).__name__}")
    path = settings_file if settings_file is not None else settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"schema": SETTINGS_SCHEMA, _KEY: _ALLOWED if allowed else _DECLINED}, indent=2
    )
    fd, tmp_name = tempfile.mkstemp(prefix=".settings-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return path

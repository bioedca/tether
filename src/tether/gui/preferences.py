# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
r"""What the application remembers between launches on this computer (#628, ADR-0122).

The desktop application keeps four things from one session to the next:

* **Recent projects** — the last ten projects opened, newest first, for
  **File → Open recent**.
* **Resume** — per project, the ``molecule_key`` that was selected when the project was last
  closed, so that reopening it selects that molecule again.
* **Layout** — the window geometry and the dock positions, as Qt's own opaque
  ``saveGeometry``/``saveState`` bytes.
* **Curator name** — the name written into new labels. Unset means the operating-system
  user name, which [`default_labeler`][tether.project.labels.default_labeler] writes, as
  before.

**Not in the project file.** None of it is written into a ``.tether``: it describes this
computer and this user, not the data. A project shared with a collaborator carries none of it.

**Not in ``settings.json``.** ADR-0095 keeps the update-check consent file to its one
answer, with no path and no user name in it, so that it can never grow into a preferences
framework. This module writes a second file, ``preferences.json``, in the **same per-user
folder** as [`settings_path`][tether.gui.app_settings.settings_path]:

============  ==========================================================================
Windows       ``%LOCALAPPDATA%\MondragonLab\Tether\preferences.json``
macOS         ``~/Library/Application Support/MondragonLab/Tether/preferences.json``
Linux         ``$XDG_CONFIG_HOME/tether/preferences.json`` (``~/.config/tether/`` by default)
============  ==========================================================================

The environment variable ``TETHER_PREFERENCES_FILE``, when it holds an absolute path, names
another file instead. The test suite uses it so that no test touches the real folder; a lab
that runs Tether from a shared account can use it to give each person their own file.

**The format.** One JSON object: ``schema`` (the integer 1), ``recent_projects`` (a list of
absolute paths), ``resume`` (an object from a project identity to a ``molecule_key``),
``layout`` (an object with base64 ``geometry`` and ``state``, and the
``section_hidden`` dock names) and ``curator_name`` (a
string). Each key is checked on its own when read: a key that is missing or not in that
form reads as its default, and the other keys still read. A file that is not a JSON object
of this ``schema`` reads as empty. Reading never raises, never creates a file and never
creates a folder.

**One writer.** Every change goes through ``_update``: read the file, change one key, and
replace the file atomically (a temporary file in the same folder, ``fsync``,
``os.replace``), as [`record_update_consent`][tether.gui.app_settings.record_update_consent]
does. A key this version does not know is kept, so a newer version's keys survive a session
of this one. A JSON object of another ``schema`` is never replaced: a write refuses with
`OSError` and leaves it as it was.

**A project's identity** is its resolved absolute path, case-folded on Windows
([`project_identity`][]). The ``.tether`` format has no identifier of its own, and the
inode changes when a project is rewritten, so a path is the stable key that exists. A
project that is moved or renamed opens at its first molecule.

The module imports only the standard library: no Qt, NumPy or HDF5.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import getpass
import json
import os
import tempfile
import unicodedata
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, NamedTuple

__all__ = [
    "CURATOR_NAME_MAX_LENGTH",
    "PREFERENCES_ENV",
    "PREFERENCES_FILENAME",
    "PREFERENCES_SCHEMA",
    "RECENT_PROJECTS_LIMIT",
    "RESUME_LIMIT",
    "StoredLayout",
    "add_recent_project",
    "clear_layout",
    "curator_name",
    "effective_curator_name",
    "login_name",
    "preferences_path",
    "project_identity",
    "recent_projects",
    "record_layout",
    "record_resume",
    "remove_recent_project",
    "resume_molecule",
    "set_curator_name",
    "stored_layout",
    "validate_curator_name",
]

PREFERENCES_FILENAME = "preferences.json"
"""The name of the per-user preferences file, beside ``settings.json``."""

PREFERENCES_ENV = "TETHER_PREFERENCES_FILE"
"""An environment variable that, holding an absolute path, names another preferences file."""

PREFERENCES_SCHEMA = 1
"""The ``schema`` value this version writes and reads."""

RECENT_PROJECTS_LIMIT = 10
"""How many projects **File → Open recent** lists."""

RESUME_LIMIT = 50
"""How many projects keep a resume position; the least recently closed one is dropped first."""

CURATOR_NAME_MAX_LENGTH = 100
"""The longest curator name accepted, in characters."""

_RECENT = "recent_projects"
_RESUME = "resume"
_LAYOUT = "layout"
_CURATOR = "curator_name"

# The file holds up to RESUME_LIMIT paths plus Qt's layout bytes; anything far larger is
# not a file this module wrote, and reads as empty rather than being parsed.
_MAX_BYTES = 256 * 1024


def preferences_path() -> Path:
    """Return the preferences file. The file and its folder may not exist."""
    override = os.environ.get(PREFERENCES_ENV, "")
    if override and Path(override).is_absolute():
        return Path(override)
    from tether.gui.app_settings import settings_path  # noqa: PLC0415 - stdlib-only module

    return settings_path().with_name(PREFERENCES_FILENAME)


def project_identity(project: str | os.PathLike[str]) -> str:
    """Return the key a project's resume position is stored under: its resolved path.

    Symbolic links are resolved, and on Windows the path is case-folded, so two spellings
    of one file give one key. The file need not exist.
    """
    return os.path.normcase(os.path.realpath(os.fspath(project)))


def validate_curator_name(name: object) -> str:
    """Return ``name`` stripped, or raise `ValueError` if it cannot be a curator name.

    A curator name is written into every new label (``labeler``), so it must be non-empty
    after stripping, at most [`CURATOR_NAME_MAX_LENGTH`][] characters, and free of control
    characters and line breaks, U+2028 and U+2029 included (a NUL or a line break in a label
    column is unreadable later).
    """
    if not isinstance(name, str):
        raise ValueError(f"a curator name must be text, not {type(name).__name__}")
    stripped = name.strip()
    if not stripped:
        raise ValueError("a curator name must not be empty")
    if len(stripped) > CURATOR_NAME_MAX_LENGTH:
        raise ValueError(f"a curator name must be at most {CURATOR_NAME_MAX_LENGTH} characters")
    # U+2028 and U+2029 are line breaks too, in categories Zl and Zp rather than C.
    if any(unicodedata.category(ch)[0] == "C" or ch in "\u2028\u2029" for ch in stripped):
        raise ValueError("a curator name must not contain control characters")
    return stripped


def _resolve(preferences_file: Path | None) -> Path:
    return preferences_file if preferences_file is not None else preferences_path()


def _load(path: Path) -> dict[str, Any] | None:
    """Return the file's JSON object whatever its ``schema``, or ``None`` if it has none."""
    try:
        with path.open("rb") as handle:
            raw = handle.read(_MAX_BYTES + 1)
    except OSError:
        return None
    if len(raw) > _MAX_BYTES:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def _ours(data: dict[str, Any]) -> bool:
    schema = data.get("schema")
    # An exact int: JSON true and 1.0 compare equal to 1 but are not what this module writes.
    return type(schema) is int and schema == PREFERENCES_SCHEMA


def _read(path: Path) -> dict[str, Any]:
    """Return the stored object, or ``{}`` for anything this module did not write."""
    data = _load(path)
    return data if data is not None and _ours(data) else {}


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # No sort_keys: the order of ``resume`` is its recency, oldest first, and is what
    # record_resume trims by (#628).
    payload = (json.dumps({**data, "schema": PREFERENCES_SCHEMA}, indent=2) + "\n").encode("utf-8")
    # A file the reader would discard as oversized must never replace a good one.
    if len(payload) > _MAX_BYTES:
        raise OSError(f"preferences would exceed {_MAX_BYTES} bytes; the file was left as it was")
    fd, tmp_name = tempfile.mkstemp(prefix=".preferences-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def _update(preferences_file: Path | None, mutate: Callable[[dict[str, Any]], None]) -> Path:
    """Apply ``mutate`` to the stored object and write it back atomically; return the file."""
    path = _resolve(preferences_file)
    data = _load(path)
    if data is None:
        # Missing, unreadable or not a JSON object: nothing in it can be kept.
        data = {}
    elif not _ours(data):
        # Another version's format: replacing it would drop every key that version wrote.
        raise OSError(
            f"{path.name} was written in another format (schema {data.get('schema')!r}); "
            "it was left as it was"
        )
    mutate(data)
    _write(path, data)
    return path


# --- recent projects -----------------------------------------------------------------


def _recent(data: dict[str, Any]) -> list[str]:
    value = data.get(_RECENT)
    if not isinstance(value, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and Path(item).is_absolute() and item not in seen:
            seen.add(item)
            out.append(item)
    return out[:RECENT_PROJECTS_LIMIT]


def recent_projects(preferences_file: Path | None = None) -> list[Path]:
    """Return the recent projects, newest first. A listed file may no longer exist."""
    return [Path(item) for item in _recent(_read(_resolve(preferences_file)))]


def add_recent_project(
    project: str | os.PathLike[str], preferences_file: Path | None = None
) -> list[Path]:
    """Put ``project`` at the top of the recent projects and return the new list."""
    entry = os.path.abspath(os.fspath(project))
    key = project_identity(entry)
    result: list[str] = []

    def mutate(data: dict[str, Any]) -> None:
        kept = [item for item in _recent(data) if project_identity(item) != key]
        result[:] = [entry, *kept][:RECENT_PROJECTS_LIMIT]
        data[_RECENT] = result

    _update(preferences_file, mutate)
    return [Path(item) for item in result]


def remove_recent_project(
    project: str | os.PathLike[str], preferences_file: Path | None = None
) -> list[Path]:
    """Take ``project`` off the recent projects and return the new list."""
    key = project_identity(project)
    result: list[str] = []

    def mutate(data: dict[str, Any]) -> None:
        result[:] = [item for item in _recent(data) if project_identity(item) != key]
        data[_RECENT] = result

    _update(preferences_file, mutate)
    return [Path(item) for item in result]


# --- resume position ------------------------------------------------------------------


def _resume(data: dict[str, Any]) -> dict[str, str]:
    value = data.get(_RESUME)
    if not isinstance(value, dict):
        return {}
    return {k: v for k, v in value.items() if isinstance(k, str) and isinstance(v, str) and v}


def resume_molecule(
    project: str | os.PathLike[str], preferences_file: Path | None = None
) -> str | None:
    """Return the ``molecule_key`` selected when ``project`` was last closed here."""
    return _resume(_read(_resolve(preferences_file))).get(project_identity(project))


def record_resume(
    project: str | os.PathLike[str],
    molecule_key: str | None,
    preferences_file: Path | None = None,
) -> Path:
    """Store ``molecule_key`` as the resume position of ``project``; ``None`` forgets it.

    The most recently recorded project is kept last, and only the last [`RESUME_LIMIT`][]
    projects keep a position.
    """
    key = project_identity(project)

    def mutate(data: dict[str, Any]) -> None:
        positions = _resume(data)
        positions.pop(key, None)
        if molecule_key:
            positions[key] = molecule_key
        data[_RESUME] = dict(list(positions.items())[-RESUME_LIMIT:])

    return _update(preferences_file, mutate)


# --- layout ------------------------------------------------------------------------------


def _decode(value: object) -> bytes | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError, ValueError):
        return None


class StoredLayout(NamedTuple):
    """The main window's layout as the last session left it."""

    geometry: bytes
    """``QMainWindow.saveGeometry()``: the window's size, position and screen."""
    state: bytes
    """``QMainWindow.saveState()``: where each dock is, and whether it shows."""
    section_hidden: tuple[str, ...]
    """The object names of docks hidden only because their section was not current.

    Qt's state cannot tell such a dock from one the user closed, and the shell (#341) hides
    every dock of a section that is not showing; these docks count as shown on restore.
    """


def stored_layout(preferences_file: Path | None = None) -> StoredLayout | None:
    """Return the stored layout of the main window, or ``None`` when none is stored."""
    value = _read(_resolve(preferences_file)).get(_LAYOUT)
    if not isinstance(value, dict):
        return None
    geometry, state = _decode(value.get("geometry")), _decode(value.get("state"))
    if geometry is None or state is None:
        return None
    hidden = value.get("section_hidden", [])
    names = tuple(n for n in hidden if isinstance(n, str)) if isinstance(hidden, list) else ()
    return StoredLayout(geometry, state, names)


def record_layout(
    geometry: bytes,
    state: bytes,
    preferences_file: Path | None = None,
    *,
    section_hidden: Iterable[str] = (),
) -> Path:
    """Store the main window's ``saveGeometry`` and ``saveState`` bytes.

    ``section_hidden`` names the docks hidden only because their section was not current
    ([`StoredLayout.section_hidden`][]).
    """
    encoded = {
        "geometry": base64.b64encode(bytes(geometry)).decode("ascii"),
        "state": base64.b64encode(bytes(state)).decode("ascii"),
        "section_hidden": sorted({str(name) for name in section_hidden}),
    }

    def mutate(data: dict[str, Any]) -> None:
        data[_LAYOUT] = encoded

    return _update(preferences_file, mutate)


def clear_layout(preferences_file: Path | None = None) -> Path:
    """Forget the stored layout, so the next launch uses the default one."""

    def mutate(data: dict[str, Any]) -> None:
        data.pop(_LAYOUT, None)

    return _update(preferences_file, mutate)


# --- curator name ----------------------------------------------------------------------


def curator_name(preferences_file: Path | None = None) -> str | None:
    """Return the stored curator name, or ``None`` when none (or no valid one) is set."""
    value = _read(_resolve(preferences_file)).get(_CURATOR)
    try:
        return validate_curator_name(value)
    except ValueError:
        return None


def login_name() -> str:
    """Return the operating-system login name, or ``"unknown"``, as the project defaults it.

    This is the name a label carries when no curator name is set: the project's own
    [`default_labeler`][tether.project.labels.default_labeler] writes it. It is read here
    only to *show* it, so the GUI does not import the project core for a display string.
    """
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 - an environment without a resolvable login
        return "unknown"
    return user or "unknown"


def effective_curator_name(preferences_file: Path | None = None) -> str:
    """Return the name new labels carry: the stored one, else the OS login name."""
    stored = curator_name(preferences_file)
    return stored if stored is not None else login_name()


def set_curator_name(name: str | None, preferences_file: Path | None = None) -> Path:
    """Store ``name`` as the curator name; ``None`` returns to the operating-system user name.

    Raises `ValueError` (and writes nothing) for a name [`validate_curator_name`][] refuses.
    """
    value = None if name is None else validate_curator_name(name)

    def mutate(data: dict[str, Any]) -> None:
        if value is None:
            data.pop(_CURATOR, None)
        else:
            data[_CURATOR] = value

    return _update(preferences_file, mutate)

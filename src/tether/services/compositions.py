# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Save a plot composition to a file and open it again (#1438, FR-PLOT, PRD §7.7).

A *composition* is what the Analyze section draws: one plot of the plot registry
([`tether.services.plots.PLOTS`][]), the project it reads, its layer list and the
parameters of the plot. [`PlotComposition`][] is that record, and it holds no data: each
series is its label and its frozen
[`SavedSelection`][tether.analysis.saved_selection.SavedSelection] in the versioned JSON
form of ADR-0124 (or ``null`` for every molecule of the project), so the file names
molecules and never holds a trace.

Two service commands use it, each with a command-line route (the parity rule, ADR-0100):

* ``save-composition`` ([`SaveCompositionRequest`][]) writes a composition file. A series
  of the request is either the JSON text of one saved series (what the application sends)
  or a preset — ``all``, ``condition=VALUE``, ``movie=VALUE`` or ``source_file=VALUE`` —
  that the command freezes against the project when it runs (what the command line sends).
* ``open-composition`` ([`OpenCompositionRequest`][]) reads one back and checks each series
  against its project. A series whose project or molecules cannot be found is named in
  [`OpenCompositionResult.missing`][] with what is missing, and is never drawn from a guess.

A file of another format version is refused. This module imports no Qt module, and the
core loads only when a command runs.
"""

from __future__ import annotations

import dataclasses
import errno
import json
import os
import secrets
import stat
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, ClassVar

from tether.services.base import CommandFailed, Request, RequestRefused
from tether.services.plots import ALL_MOLECULES, PlotSeries, plot_spec

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows: no advisory locks, no reservation route
    fcntl = None  # type: ignore[assignment]

__all__ = [
    "COMPOSITION_FORMAT",
    "COMPOSITION_VERSION",
    "OpenCompositionRequest",
    "OpenCompositionResult",
    "PlotComposition",
    "SaveCompositionRequest",
    "SaveCompositionResult",
    "run_open",
    "run_save",
    "series_text",
]

#: The ``format`` key of a composition file: what kind of file it is.
COMPOSITION_FORMAT = "tether-plot-composition"
#: The format version of a composition file this Tether writes and reads.
COMPOSITION_VERSION = 1

_TOP_KEYS = frozenset({"format", "version", "plot", "project_path", "series", "parameters"})
_PARAMETER_KEYS = frozenset({"model_name", "states"})
_PRESET_BYS = ("condition", "movie", "source_file")
#: How many missing molecule keys a message lists before it says how many more there are.
_KEYS_SHOWN = 5


def _resolved(path: str) -> str:
    return os.fspath(Path(path).resolve())


@dataclasses.dataclass(frozen=True)
class PlotComposition:
    """One plot of the Analyze section, as a file holds it: no data, only what it draws.

    ``plot`` is the ``id`` of a registry entry, ``project_path`` the project it reads,
    ``series`` the layer list in drawing order, ``model_name`` the idealization model a
    model-derived plot reads (or ``None``) and ``states`` the state options of the plot as
    sorted ``(name, value)`` pairs. Series labels are distinct, as the section keeps them.
    """

    plot: str
    project_path: str
    series: tuple[PlotSeries, ...]
    model_name: str | None = None
    states: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        """Refuse an unknown plot or a repeated label; resolve the path; sort the states."""
        spec = plot_spec(self.plot)  # KeyError for a plot that is not in the registry
        labels = [s.label for s in self.series]
        repeated = sorted({label for label in labels if labels.count(label) > 1})
        if repeated:
            raise ValueError(f"a composition names each series once; repeated: {repeated}")
        unknown = sorted(name for name, _ in self.states if name not in spec.states)
        if unknown:
            raise ValueError(f"{spec.title} takes no state option {unknown}")
        names = [name for name, _ in self.states]
        twice = sorted({name for name in names if names.count(name) > 1})
        if twice:
            raise ValueError(f"each state option is given once; repeated: {twice}")
        negative = sorted(name for name, value in self.states if int(value) < 0)
        if negative:
            raise ValueError(f"a state is 0 or more; {negative} is not")
        object.__setattr__(self, "project_path", _resolved(self.project_path))
        object.__setattr__(self, "series", tuple(self.series))
        object.__setattr__(self, "states", tuple(sorted((str(k), int(v)) for k, v in self.states)))

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-ready form; [`from_dict`][] reads it back to an equal record."""
        return {
            "format": COMPOSITION_FORMAT,
            "version": COMPOSITION_VERSION,
            "plot": self.plot,
            "project_path": self.project_path,
            "series": [_series_dict(s) for s in self.series],
            "parameters": {"model_name": self.model_name, "states": dict(self.states)},
        }

    def to_text(self) -> str:
        """Return the text of a composition file (indented JSON, sorted keys)."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"

    @classmethod
    def from_dict(cls, data: Any) -> PlotComposition:
        """Read the form [`to_dict`][] wrote.

        Raises
        ------
        ValueError
            The form is not a composition, is of another format version, has a key this
            version does not know, or a field is missing or of the wrong type.
        """
        if not isinstance(data, Mapping) or data.get("format") != COMPOSITION_FORMAT:
            raise ValueError("the file is not a Tether plot composition")
        version = data.get("version")
        if type(version) is not int or version != COMPOSITION_VERSION:
            raise ValueError(
                f"plot composition version {version!r} is not supported "
                f"(this Tether reads version {COMPOSITION_VERSION})"
            )
        unknown = sorted(set(data) - _TOP_KEYS)
        if unknown:
            raise ValueError(f"a version {version} composition has no key {unknown}")
        try:
            parameters = data["parameters"]
            series = data["series"]
            if not isinstance(parameters, Mapping) or not isinstance(series, list):
                raise ValueError("'parameters' must be an object and 'series' a list")
            extra = sorted(set(parameters) - _PARAMETER_KEYS)
            if extra:
                raise ValueError(f"a version {version} composition has no parameter {extra}")
            states = parameters.get("states") or {}
            if not isinstance(states, Mapping) or not all(type(v) is int for v in states.values()):
                raise ValueError("the parameter 'states' must map each option to an integer")
            model = parameters.get("model_name")
            if model is not None and not isinstance(model, str):
                raise ValueError("the parameter 'model_name' must be a string or null")
            plot = str(data["plot"])
            project_path = str(data["project_path"])
        except KeyError as exc:
            raise ValueError(f"the composition is missing {exc.args[0]!r}") from None
        try:
            return cls(
                plot=plot,
                project_path=project_path,
                series=tuple(_series_from(entry) for entry in series),
                model_name=model,
                states=tuple(states.items()),
            )
        except KeyError as exc:  # an unknown plot id
            raise ValueError(exc.args[0]) from None

    @classmethod
    def from_text(cls, text: str) -> PlotComposition:
        """Read the text of a composition file; raise `ValueError` if it is not one."""
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"the file is not valid JSON: {exc}") from None
        return cls.from_dict(data)


def _series_dict(series: PlotSeries) -> dict[str, Any]:
    selection = series.selection
    if selection is not None and not hasattr(selection, "to_dict"):
        raise TypeError(f"series {series.label!r}: its selection cannot be saved")
    return {"label": series.label, "selection": None if selection is None else selection.to_dict()}


def _series_from(entry: Any) -> PlotSeries:
    from tether.analysis.saved_selection import SavedSelection  # noqa: PLC0415

    if not isinstance(entry, Mapping) or set(entry) != {"label", "selection"}:
        raise ValueError("each series must be an object with 'label' and 'selection' only")
    selection = entry["selection"]
    if selection is not None and not isinstance(selection, Mapping):
        raise ValueError(f"series {entry['label']!r}: 'selection' must be an object or null")
    try:
        saved = None if selection is None else SavedSelection.from_dict(selection)
    except (TypeError, AttributeError) as exc:  # a nested field of the wrong shape
        raise ValueError(
            f"series {entry['label']!r}: its selection cannot be read: {exc}"
        ) from None
    return PlotSeries(entry["label"], saved)


def series_text(series: PlotSeries) -> str:
    """Return the JSON text of one series: an item of [`SaveCompositionRequest.series`][]."""
    return json.dumps(_series_dict(series), sort_keys=True, separators=(",", ":"))


# --- save-composition -----------------------------------------------------------------


@dataclasses.dataclass(frozen=True, kw_only=True)
class SaveCompositionRequest(Request):
    """Write the composition of ``plot`` over ``series`` of ``project`` to ``path``.

    Each item of ``series`` is the JSON text of one series ([`series_text`][]) or a preset:
    ``all`` (every molecule) or ``condition=VALUE``, ``movie=VALUE`` or
    ``source_file=VALUE``, frozen against ``project`` when the command runs. ``states``
    holds ``NAME=VALUE`` items. An existing ``path`` is replaced only with ``overwrite``,
    including one that appears while the command runs (#1470).
    """

    COMMAND: ClassVar[str] = "save-composition"
    VERSION: ClassVar[int] = 1
    PATH_FIELDS: ClassVar[tuple[str, ...]] = ("path", "project")

    path: str
    project: str
    plot: str
    series: tuple[str, ...]
    model: str | None = None
    states: tuple[str, ...] = ()
    overwrite: bool = False


@dataclasses.dataclass(frozen=True)
class SaveCompositionResult:
    """The outcome of ``save-composition``: the file written and the record it holds."""

    path: Path
    composition: PlotComposition


def _preset(project: str, spec: str) -> PlotSeries:
    from tether.analysis.saved_selection import preset_selections  # noqa: PLC0415

    if spec == "all":
        return PlotSeries(ALL_MOLECULES, None)
    by, sep, value = spec.partition("=")
    if not sep or by not in _PRESET_BYS:
        raise RequestRefused(
            f"series {spec!r} is not a preset: give all, or condition=, movie= or "
            "source_file= and a value"
        )
    for selection in preset_selections(project, by):
        if selection.filter.group == (by, value):
            return PlotSeries(selection.name, selection)
    raise RequestRefused(f"series {spec!r}: the project has no {by.replace('_', ' ')} {value!r}")


def _states(items: Sequence[str]) -> tuple[tuple[str, int], ...]:
    states = []
    for item in items:
        name, _, value = item.partition("=")
        if not name or not value.strip().lstrip("-").isdigit():
            raise RequestRefused(f"state {item!r} is not NAME=INTEGER")
        states.append((name, int(value)))
    return tuple(states)


def _is_composition_file(path: Path) -> bool:
    """Say whether ``path`` holds a composition, so a save never replaces another file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # a binary file, such as a project, is a UnicodeDecodeError
        return False
    return isinstance(data, Mapping) and data.get("format") == COMPOSITION_FORMAT


def _is_empty_file(path: Path) -> bool:
    """Say whether ``path`` is an empty regular file, such as a reservation `_create_new` left.

    It holds nothing to lose, so ``--force`` may replace it like a composition.
    """
    try:
        entry = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISREG(entry.st_mode) and entry.st_size == 0


class _Exists(Exception):
    """The destination appeared after the check, so a save without ``overwrite`` stops."""


class _Busy(Exception):
    """The destination is an empty reservation that a running save still holds (#1543)."""


def _lock(fd: int) -> bool:
    """Take an exclusive advisory lock on ``fd`` without waiting; say whether no save holds it.

    The lock is what tells a live reservation from an abandoned one: the save that made
    it holds the lock until its replace is done, and the kernel releases it when that
    process ends, however it ends. Where there are no such locks (Windows, or a file
    system that refuses them) no lock is taken and the answer is ``True``, as before #1543.
    """
    if fcntl is None:
        return True
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:  # another descriptor holds it
        return False
    except OSError:  # no lock support on this file system
        return True
    return True


def _temporary(path: Path) -> tuple[int, Path]:
    """Create a unique temporary file beside ``path``, exclusively, and return it open.

    Like `tempfile.mkstemp`, but with the mode an ordinary new file gets: it asks for
    0666 and the kernel applies the process umask (and any default ACL), where
    ``mkstemp`` asks for 0600. Every route publishes this inode, so this is the mode
    of the saved composition (#1543).
    """
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
    for name in ("O_NOFOLLOW", "O_BINARY", "O_NOINHERIT"):
        flags |= getattr(os, name, 0)
    for _ in range(100):
        tmp = path.parent / f".{path.name}.{secrets.token_hex(6)}.tmp"
        try:
            return os.open(tmp, flags, 0o666), tmp
        except FileExistsError:
            continue
    raise FileExistsError(errno.EEXIST, "no unused temporary name", os.fspath(path.parent))


# Windows ``os.rename`` never replaces an existing name (it raises FileExistsError);
# on POSIX it does, so there a file system without hard links reserves the name first.
_RENAME_REFUSES_EXISTING = os.name == "nt"


def _discard(tmp: Path) -> None:
    """Remove the temporary name after a publish; a failure here never fails the save.

    The composition is at its destination already, so an error is not reported: one
    retry runs, and a name both attempts leave is a stray dot file, never a half file.
    """
    for _ in range(2):
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            continue
        return


def _publish_new(tmp: Path, path: Path) -> bool:
    """Give the written file the name ``path``, failing if that name already exists.

    Returns once ``path`` holds the composition, and says whether ``tmp`` is still a
    name of it: only a hard link leaves one. Removing it is the caller's separate
    step, so a failure there is not a failed publish (#1523); a route that moved the
    name leaves nothing to remove, and a name another writer reuses is never touched.
    """
    try:
        os.link(tmp, path)  # exclusive: a hard link never replaces an entry
    except FileExistsError:
        raise _Exists from None
    except OSError:  # a file system without hard links
        if _RENAME_REFUSES_EXISTING:
            try:
                os.rename(tmp, path)  # atomic, and never replaces an entry
            except FileExistsError:
                raise _Exists from None
            return False
        _create_new(tmp, path)
        return False
    return True


def _create_new(tmp: Path, path: Path) -> None:
    """Reserve ``path`` with an exclusive, empty create, then replace it with ``tmp``.

    Nothing that another writer created is replaced: the exclusive create fails on an
    existing name (`_Exists`), and the replace runs only while ``path`` is still this
    call's own reservation. The text is never written in place, so a save killed at
    any point leaves either the whole composition or an empty file at ``path``, never
    a half file; ``--force`` replaces an empty file (`_is_empty_file`).

    The reservation is locked (`_lock`) from just after its create until the replace
    is done, and a forced save replaces an empty file only once it holds that lock
    itself (`_replace_unless_reserved`), so a forced save never replaces a reservation
    a running save still holds (#1543). If a forced save takes the lock first, in the
    moment between the create and the lock, this save finds its reservation gone and
    fails instead: either way one of the two saves reports the conflict. Where there
    are no advisory locks the guard is absent, and a forced save landing between the
    check and the replace below is lost to this save's replace.

    A save that fails after the reservation leaves it in place rather than remove it:
    no removal by name can rule out a file swapped in after its check, and losing
    another writer's file is worse than an empty one (#1523). A swap between the
    reservation check and the replace is the window `_write` states.
    """
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        raise _Exists from None
    try:
        if not _lock(fd):  # a forced save has it, and is replacing it now
            raise OSError(f"{path.name} is being replaced by another save")
        reserved = os.fstat(fd)
        if fcntl is None:  # no lock to hold, and Windows never replaces a file held open
            os.close(fd)
            fd = -1
        if not os.path.samestat(reserved, os.lstat(path)):
            raise OSError(f"{path.name} was replaced while the save reserved it")
        os.replace(tmp, path)  # atomic, and the entry it replaces is this reservation
    finally:
        if fd >= 0:
            os.close(fd)  # releases the lock, after the replace


def _replace_unless_reserved(tmp: Path, path: Path) -> None:
    """Replace ``path`` with ``tmp``, unless it is a reservation a running save holds.

    An empty regular file at ``path`` is replaced only while this call holds its lock
    (`_lock`); a lock another save holds is `_Busy`, and ``path`` is left as it is.
    Anything else is replaced as before. Windows has no such locks, and keeps no
    descriptor open on ``path`` (that would make the replace fail there).
    """
    if fcntl is None:
        os.replace(tmp, path)
        return
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    except OSError:  # no entry, a link, or an entry this process cannot open: none to guard
        os.replace(tmp, path)
        return
    try:
        held = os.fstat(fd)
        if stat.S_ISREG(held.st_mode) and held.st_size == 0 and not _lock(fd):
            raise _Busy
        os.replace(tmp, path)
    finally:
        os.close(fd)


def _write(path: Path, text: str, *, overwrite: bool) -> None:
    """Write ``text`` to ``path`` through a sibling temporary file, so no half file stays.

    Without ``overwrite`` the publish itself is exclusive, so a file that appears at
    ``path`` after the caller's check is never replaced (`_Exists`). Before the publish,
    the temporary name is checked to still be the file this call wrote, so a swapped
    entry is never published. A failed save leaves no temporary file, and no route
    writes ``path`` in place, so not even a killed save leaves a half file there; on a
    POSIX file system without hard links it may leave an empty file (`_create_new`).
    Once ``path`` holds the composition the save succeeds, whatever happens when the
    temporary name is then removed (`_discard`); only a hard link leaves that name, and
    a route that moved it removes nothing afterwards.

    Known limit: the publish resolves the temporary name again after its check, so an
    entry swapped in that window is still published. Closing it needs a publish bound
    to the descriptor (``linkat`` with ``AT_EMPTY_PATH`` on Linux), which is
    platform-specific and which the standard library does not expose (#1523).

    The saved file has the mode an ordinary new file gets under the caller's umask
    (`_temporary`). With ``overwrite``, an empty file a running save still reserves is
    never replaced (`_Busy`, `_replace_unless_reserved`).
    """
    # A unique file, created exclusively: never a fixed name that a link could point
    # elsewhere, or that a second save to the same path would share.
    fd, tmp = _temporary(path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            written = os.fstat(f.fileno())
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    try:
        entry = os.lstat(tmp)
    except FileNotFoundError:
        entry = None
    if entry is None or not os.path.samestat(written, entry):
        # Not the file this call wrote: publish nothing, and leave that entry alone.
        raise OSError(f"its temporary file {tmp.name} was replaced before the save published it")
    try:
        if overwrite:
            _replace_unless_reserved(tmp, path)
            linked = False  # the replace moved the name
        else:
            linked = _publish_new(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    if linked:
        _discard(tmp)


def run_save(request: SaveCompositionRequest) -> SaveCompositionResult:
    """Freeze the series, check the record and write the file; refuse before any write.

    The file gets the mode an ordinary new file gets under the process umask. With
    ``overwrite``, an empty file is replaced like a composition (it may be what a killed
    save left), but not while a running save still reserves it: that save is writing
    the name now, so this one is refused and the destination left to it (#1543).
    """
    path = Path(request.path)
    if not Path(request.project).exists():
        raise RequestRefused(f"the project {request.project} cannot be found")
    if path.exists():
        if os.path.samefile(path, request.project):  # both exist
            raise RequestRefused(f"{request.path} is the project the composition reads")
        if not request.overwrite:
            raise RequestRefused(f"{request.path} exists; give --force to replace it")
        if not (_is_composition_file(path) or _is_empty_file(path)):
            raise RequestRefused(
                f"{request.path} is not a plot composition; only a composition file is replaced"
            )
    series = []
    try:
        for item in request.series:
            if item.lstrip().startswith("{"):
                series.append(_series_from(json.loads(item)))
            else:
                series.append(_preset(request.project, item))
        composition = PlotComposition(
            plot=request.plot,
            project_path=request.project,
            series=tuple(series),
            model_name=request.model,
            states=_states(request.states),
        )
    except RequestRefused:
        raise
    except KeyError as exc:  # an unknown plot id
        raise RequestRefused(exc.args[0]) from None
    except (ValueError, TypeError, OSError) as exc:
        raise RequestRefused(str(exc)) from exc
    try:
        _write(path, composition.to_text(), overwrite=request.overwrite)
    except _Exists:
        raise RequestRefused(f"{request.path} exists; give --force to replace it") from None
    except _Busy:
        raise RequestRefused(
            f"{request.path} is being written by another save; try again when it is done"
        ) from None
    except OSError as exc:
        raise CommandFailed(f"cannot write {request.path}: {exc}") from exc
    return SaveCompositionResult(path=path, composition=composition)


# --- open-composition -----------------------------------------------------------------


@dataclasses.dataclass(frozen=True, kw_only=True)
class OpenCompositionRequest(Request):
    """Read the composition file ``path`` and check each series against its project."""

    COMMAND: ClassVar[str] = "open-composition"
    VERSION: ClassVar[int] = 1
    PATH_FIELDS: ClassVar[tuple[str, ...]] = ("path",)

    path: str


@dataclasses.dataclass(frozen=True)
class OpenCompositionResult:
    """The outcome of ``open-composition``: the record, and each series that cannot be drawn.

    ``missing`` holds ``(label, reason)`` for each series whose project or molecules cannot
    be found, in layer-list order; [`found`][] is the rest, the series that can be drawn.
    """

    path: Path
    composition: PlotComposition
    missing: tuple[tuple[str, str], ...] = ()

    @property
    def found(self) -> tuple[PlotSeries, ...]:
        """The series that can be drawn, in layer-list order."""
        gone = {label for label, _ in self.missing}
        return tuple(s for s in self.composition.series if s.label not in gone)

    def message(self) -> str:
        """One line per series that cannot be drawn, naming what is missing."""
        return "\n".join(f"{label}: {reason}" for label, reason in self.missing)


def _project_keys(project: str) -> frozenset[str] | str:
    """Return the molecule keys of ``project``, or the reason it cannot be read."""
    from tether.imaging.extract import read_molecules  # noqa: PLC0415

    if not Path(project).exists():
        return f"the project {project} cannot be found"
    try:
        # Every row of /molecules, with no condition filter: a saved selection can name an
        # unconditioned molecule, which a condition query leaves out.
        column = read_molecules(project)["molecule_key"]
        return frozenset(k.decode() if isinstance(k, bytes) else str(k) for k in column)
    except (OSError, ValueError, KeyError) as exc:
        return f"the project {project} cannot be read: {exc}"


def _check(composition: PlotComposition) -> tuple[tuple[str, str], ...]:
    keys = _project_keys(composition.project_path)
    missing: list[tuple[str, str]] = []
    for series in composition.series:
        if isinstance(keys, str):
            missing.append((series.label, keys))
            continue
        selection = series.selection
        if selection is None:
            continue
        if not selection.is_in(composition.project_path):
            missing.append(
                (series.label, f"its molecules are of the project {selection.project_path}")
            )
            continue
        absent = [k for k in selection.molecule_keys if k not in keys]
        if absent:
            shown = ", ".join(absent[:_KEYS_SHOWN])
            more = f" and {len(absent) - _KEYS_SHOWN} more" if len(absent) > _KEYS_SHOWN else ""
            missing.append(
                (
                    series.label,
                    f"{len(absent)} of its {selection.n_molecules} molecules are not in "
                    f"{composition.project_path}: {shown}{more}",
                )
            )
    return tuple(missing)


def run_open(request: OpenCompositionRequest) -> OpenCompositionResult:
    """Read the file and name each series that cannot be drawn; refuse a file it cannot read."""
    path = Path(request.path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RequestRefused(f"cannot read {request.path}: {exc}") from exc
    try:
        composition = PlotComposition.from_text(text)
    except (ValueError, TypeError) as exc:
        raise RequestRefused(f"{request.path}: {exc}") from exc
    return OpenCompositionResult(path=path, composition=composition, missing=_check(composition))

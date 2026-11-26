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
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, ClassVar

from tether.services.base import CommandFailed, Request, RequestRefused
from tether.services.plots import ALL_MOLECULES, PlotSeries, plot_spec

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
    saved = None if selection is None else SavedSelection.from_dict(selection)
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
    holds ``NAME=VALUE`` items. An existing ``path`` is replaced only with ``overwrite``.
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


def _write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` through a sibling temporary file, so no half file stays."""
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def run_save(request: SaveCompositionRequest) -> SaveCompositionResult:
    """Freeze the series, check the record and write the file; refuse before any write."""
    path = Path(request.path)
    if not Path(request.project).exists():
        raise RequestRefused(f"the project {request.project} cannot be found")
    if path.exists():
        if os.path.samefile(path, request.project):  # both exist
            raise RequestRefused(f"{request.path} is the project the composition reads")
        if not request.overwrite:
            raise RequestRefused(f"{request.path} exists; give --force to replace it")
        if not _is_composition_file(path):
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
        _write(path, composition.to_text())
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

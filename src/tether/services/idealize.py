# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The ``idealize`` service command: fit a whole project, or refit its stale models.

[`IdealizeRequest`][] holds what ``tether idealize`` takes on its command line, with the
same defaults; [`run`][] calls [`tether.project.idealize.idealize_molecules`][] once, or,
with ``stale_only``, [`tether.project.idealize.reidealize`][] for each model that has a
stale molecule (issue #657). The application's "Idealize project…" (#625) is the other
front end of the same command.

Before any project write the command checks that the sidecar can start (the startup
liveness probe of [`tether.idealize.supervisor.probe_sidecar`][]). When it cannot, the
command raises [`SidecarUnavailable`][] and writes nothing: no model and no lock file.
Otherwise it holds the project's single-writer lock for the whole fit, and each fit runs
under [`tether.idealize.supervisor.supervise_idealize`][], which restarts the sidecar on a
crash or a timeout.

The command stores the fit. It selects no active fit: that is the rule of #939, which the
command line reaches through #944. The core is imported when the command runs, not when
this module is imported (ADR-0100).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from tether.services.base import CommandFailed, Request, RequestRefused, ServiceError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from tether.idealize.supervisor import ProbeResult, SidecarSupervision
    from tether.project.idealize import StoredIdealization

__all__ = [
    "IdealizeRequest",
    "IdealizeResult",
    "ModelRefit",
    "SidecarUnavailable",
    "parse_nstates_grid",
    "run",
]


class SidecarUnavailable(ServiceError):
    """The sidecar could not start, so nothing was fitted and nothing was written."""


@dataclasses.dataclass(frozen=True, kw_only=True)
class IdealizeRequest(Request):
    """Fit the molecules of one project, or refit the project's stale models.

    The fields are the options of ``tether idealize`` (``docs/cli.md``) with the same
    defaults. ``method=None`` is the library default
    ([`MODEL_TYPE_DEFAULT`][tether.project.idealize.MODEL_TYPE_DEFAULT]); ``nstates=None``
    selects the state count by maximum ELBO over ``nstates_grid``, a comma-separated list
    of state counts (``None`` is
    [`NSTATES_GRID_DEFAULT`][tether.project.idealize.NSTATES_GRID_DEFAULT]).
    ``conditions`` limits the fit to the molecules of those condition IDs (empty = every
    molecule). ``None`` in a supervision field means the
    [`SidecarSupervision`][tether.idealize.supervisor.SidecarSupervision] default (§11.2).

    With ``stale_only`` the command refits each model that has a stale molecule, with the
    configuration the model stored, and the fit options must keep their defaults;
    ``model_name`` then limits the refit to that one model.
    """

    COMMAND: ClassVar[str] = "idealize"
    VERSION: ClassVar[int] = 1
    PATH_FIELDS: ClassVar[tuple[str, ...]] = ("project",)
    PROJECT_FIELD: ClassVar[str | None] = "project"

    project: str
    method: str | None = None
    nstates: int | None = None
    nstates_grid: str | None = None
    model_name: str | None = None
    conditions: tuple[str, ...] = ()
    include_rejected: bool = False
    overwrite: bool = False
    seed: int | None = None
    stale_only: bool = False
    max_restarts: int | None = None
    sidecar_timeout: float | None = None
    sidecar_python: str | None = None


@dataclasses.dataclass(frozen=True)
class ModelRefit:
    """What ``stale_only`` did with one model.

    ``n_rows`` is the number of molecule rows the model holds and ``n_stale`` how many of
    them were stale, both counted by row (``molecule_id``), so a ``molecule_key`` shared by
    two rows (§7.10) counts twice. ``refit`` is the new model, or ``None`` when the model was
    left as it was: no row was stale, or the model was ``imported`` from standalone tMAVEN.
    An imported model stores no fit configuration Tether can repeat, so ``stale_only``
    never refits one, stale or not.
    """

    model_name: str
    n_rows: int
    n_stale: int
    refit: StoredIdealization | None
    imported: bool = False

    @property
    def n_current(self) -> int:
        """Rows whose inputs had not changed since the fit."""
        return self.n_rows - self.n_stale


@dataclasses.dataclass(frozen=True)
class IdealizeResult:
    """The outcome of an ``idealize`` command.

    ``fitted`` is the stored model of an ordinary run (``None`` with ``stale_only``);
    ``refits`` holds one [`ModelRefit`][] for each model a ``stale_only`` run checked.
    """

    project: Path
    fitted: StoredIdealization | None = None
    refits: tuple[ModelRefit, ...] = ()


def parse_nstates_grid(text: str) -> tuple[int, ...]:
    """Read ``"1,2,3,4"`` as ``(1, 2, 3, 4)``; raise [`ValueError`][] if it is not one.

    Every entry must be a positive integer, and an entry may appear only once.
    """
    parts = [p.strip() for p in text.split(",")]
    try:
        grid = tuple(int(p) for p in parts)
    except ValueError:
        raise ValueError(
            f"nstates grid must be comma-separated whole numbers, got {text!r}"
        ) from None
    if not grid or any(k < 1 for k in grid):
        raise ValueError(f"every state count in the grid must be 1 or more, got {text!r}")
    if len(set(grid)) != len(grid):
        raise ValueError(f"the nstates grid names a state count twice: {text!r}")
    return grid


#: The options a ``stale_only`` run must leave at their defaults: a refit reuses the
#: configuration the model stored (``reidealize``), so a value here would be ignored.
_FIT_OPTIONS = ("method", "nstates", "nstates_grid", "seed")


def _refuse_stale_only_options(request: IdealizeRequest) -> None:
    named = [name for name in _FIT_OPTIONS if getattr(request, name) is not None]
    if request.conditions:
        named.append("conditions")
    if request.include_rejected:
        named.append("include_rejected")
    if request.overwrite:
        named.append("overwrite")
    if named:
        raise RequestRefused(
            "stale_only refits each model with the configuration it stored, so it takes "
            f"none of: {', '.join(named)}"
        )


def _supervision(request: IdealizeRequest) -> SidecarSupervision:
    from tether.idealize.supervisor import SidecarSupervision

    kwargs: dict[str, Any] = {}
    if request.max_restarts is not None:
        kwargs["max_restarts"] = request.max_restarts
    if request.sidecar_timeout is not None:
        kwargs["timeout"] = request.sidecar_timeout
    if request.sidecar_python is not None:
        kwargs["sidecar_python"] = request.sidecar_python
    try:
        return SidecarSupervision(**kwargs)
    except ValueError as exc:
        raise RequestRefused(str(exc)) from exc


def _probe(supervision: SidecarSupervision) -> ProbeResult:
    """Run the startup liveness probe (a module attribute, so a test can replace it)."""
    from tether.idealize.supervisor import probe_sidecar

    return probe_sidecar(supervision.sidecar_python, timeout=supervision.probe_timeout)


#: The sidecar runner both fits use; ``None`` is the library's own (``run_vbfret``). A
#: private test seam, as ``_runner`` is on the library functions.
_runner: Any = None


def _selected_keys(project: Any, conditions: tuple[str, ...]) -> list[str] | None:
    if not conditions:
        return None
    by_condition = project.molecules_by_condition()
    missing = [c for c in conditions if c not in by_condition]
    if missing:
        known = ", ".join(by_condition) or "none"
        raise RequestRefused(f"no condition {', '.join(missing)} in the project (known: {known})")
    keys: list[str] = []
    for condition in dict.fromkeys(conditions):
        keys.extend(by_condition[condition])
    return list(dict.fromkeys(keys))


def _refuse_bad_model_name(name: str, core: Any) -> None:
    """Refuse a model name that is not one plain entry of ``/idealization``.

    The name becomes an HDF5 path under ``/idealization``. A ``/`` in it reaches another
    group (``/traces`` is absolute, so ``--overwrite`` would replace the traces), and a name
    ending in the writer's staging suffix is hidden by ``list_idealizations`` and cleaned
    up by the next write of the shorter name.
    """
    if not name or "/" in name or name in {".", ".."} or name.endswith(core._WRITING_SUFFIX):
        raise RequestRefused(
            f"model name {name!r} is not allowed: give one name with no '/', not '.' or "
            f"'..', and not ending in {core._WRITING_SUFFIX!r}"
        )


# ``nstates_selected_by`` of a model written by the standalone-tMAVEN return leg.
_IMPORTED = "imported"


def _unchanged(checked: list[tuple[str, int, int, bool]]) -> tuple[ModelRefit, ...]:
    """Report a ``stale_only`` run that refitted nothing."""
    return tuple(ModelRefit(name, n, s, None, imported) for name, n, s, imported in checked)


def _check_models(project: Any, names: list[str]) -> list[tuple[str, int, int, bool]]:
    """Count each model's rows and stale rows, and mark the imported ones (read-only)."""
    from tether.project import idealize as core

    checked = []
    for name in names:
        stored = core.read_idealization(project, name)
        imported = stored.nstates_selected_by == _IMPORTED
        checked.append(
            (name, stored.n_molecules, len(core.stale_row_ids(project, stored)), imported)
        )
    return checked


def run(request: IdealizeRequest) -> IdealizeResult:
    """Run *request*.

    Raises [`RequestRefused`][tether.services.base.RequestRefused] before the sidecar is
    started and before any write when the request is not valid: an unknown method or
    condition, a bad state count or grid, a model that exists without ``overwrite``, a
    project that cannot be opened, or fit options combined with ``stale_only``. Raises
    [`SidecarUnavailable`][] when the sidecar cannot start, and
    [`CommandFailed`][tether.services.base.CommandFailed] when the lock cannot be taken or
    a fit fails.
    """
    from tether.idealize.driver import SidecarError
    from tether.idealize.supervisor import supervise_idealize
    from tether.project import idealize as core
    from tether.project.core import Project
    from tether.project.lock import LockError

    if request.stale_only:
        _refuse_stale_only_options(request)
    method = request.method if request.method is not None else core.MODEL_TYPE_DEFAULT
    if method not in core.IDEALIZE_METHODS:
        raise RequestRefused(
            f"method must be one of {', '.join(core.IDEALIZE_METHODS)}, got {method!r}"
        )
    if request.nstates is not None and request.nstates < 1:
        raise RequestRefused(f"nstates must be 1 or more, got {request.nstates}")
    if request.nstates is not None and request.nstates_grid is not None:
        raise RequestRefused("give a fixed nstates or an nstates grid, not both")
    try:
        grid = (
            core.NSTATES_GRID_DEFAULT
            if request.nstates_grid is None
            else parse_nstates_grid(request.nstates_grid)
        )
    except ValueError as exc:
        raise RequestRefused(str(exc)) from exc
    if request.model_name is not None:
        _refuse_bad_model_name(request.model_name, core)
    supervision = _supervision(request)

    try:
        project = Project.open(request.project)
        existing = project.list_idealizations()
    except (OSError, ValueError) as exc:
        raise RequestRefused(f"cannot open {request.project}: {exc}") from exc
    # The default model name is the method (idealize_molecules), so the stored name
    # matches the method that ran unless the caller names it.
    model_name = request.model_name or method
    if request.stale_only:
        if request.model_name is not None and request.model_name not in existing:
            raise RequestRefused(f"{request.project} has no model {request.model_name!r}")
        names = [request.model_name] if request.model_name is not None else existing
        # Read-only: count each model's stale rows before the sidecar or the lock is
        # touched, so a project with nothing to refit needs neither. The count is taken
        # again under the lock below, since another writer may change the project first.
        checked = _check_models(project, names)
        if request.model_name is not None and checked[0][3]:
            raise RequestRefused(
                f"the model {request.model_name!r} was imported from standalone tMAVEN, "
                "so stale_only cannot refit it; hand its molecules to tMAVEN again and "
                "import the new fit"
            )
        if not any(n_stale and not imported for _, _, n_stale, imported in checked):
            return IdealizeResult(project=project.path, refits=_unchanged(checked))
        keys = None
    else:
        if model_name in existing and not request.overwrite:
            raise RequestRefused(
                f"{request.project} already has the model {model_name!r}; pass overwrite "
                "to replace it or name another model"
            )
        keys = _selected_keys(project, request.conditions)
        checked = []  # an ordinary fit checks no model

    # Nothing is written before this point, and nothing at all when the sidecar is absent.
    probe = _probe(supervision)
    if not probe.available:
        raise SidecarUnavailable(f"the sidecar is not available: {probe.detail}")

    runner = {} if _runner is None else {"_runner": _runner}
    try:
        project.acquire_lock()
    except LockError as exc:
        raise CommandFailed(f"cannot take the lock of {request.project}: {exc}") from exc
    try:
        if not request.stale_only:
            # Under the lock, so the fit takes the molecules in the conditions now; the
            # selection above only refused an unknown condition before the probe.
            keys = _selected_keys(project, request.conditions)
            fitted = supervise_idealize(
                core.idealize_molecules,
                project,
                keys,
                supervision=supervision,
                model_type=method,
                nstates=request.nstates,
                nstates_grid=grid,
                model_name=model_name,
                sidecar_python=supervision.sidecar_python,
                timeout=supervision.timeout,
                overwrite=request.overwrite,
                include_rejected=request.include_rejected,
                require_held_lock=True,
                seed=request.seed,
                **runner,
            )
            return IdealizeResult(project=project.path, fitted=fitted)
        # Under the lock, so the models refitted are the ones stale now.
        if request.model_name is None:
            names = project.list_idealizations()
        checked = _check_models(project, names)
        refits: list[ModelRefit] = []
        for name, n_rows, n_stale, imported in checked:
            refit = None
            if n_stale and not imported:
                refit = supervise_idealize(
                    core.reidealize,
                    project,
                    name,
                    supervision=supervision,
                    sidecar_python=supervision.sidecar_python,
                    timeout=supervision.timeout,
                    require_held_lock=True,
                    **runner,
                )
            refits.append(ModelRefit(name, n_rows, n_stale, refit, imported))
        return IdealizeResult(project=project.path, refits=tuple(refits))
    except (SidecarError, LockError) as exc:
        raise CommandFailed(str(exc)) from exc
    except (KeyError, ValueError, FileExistsError) as exc:
        # The selection or the store refused the fit (for example: no molecule left
        # after the rejected ones are dropped). The message is the library's own.
        message = exc.args[0] if isinstance(exc, KeyError) and exc.args else exc
        raise CommandFailed(str(message)) from exc
    finally:
        project.release_lock()

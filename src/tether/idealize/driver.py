# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless tMAVEN sidecar driver — vbFRET round-trip (PRD §4.3, §7.4, Appendix D.2).

This is the base-environment half of the idealization hand-off. It writes the
selected molecules to an SMD (:func:`tether.idealize.write_smd`), launches the
**isolated sidecar interpreter** on :mod:`tether.idealize._sidecar_runner` to
drive ``tmaven.maven.maven_class`` headlessly, then reads the resulting
Appendix-D.2 ``model`` file back into a :class:`StateModel` and derives
per-molecule state paths and dwell segments.

Why a subprocess and not an in-process import: tMAVEN pins ``numpy<2`` + PyQt5
(the sidecar lock), which is deliberately *isolated* from Tether's base stack
(PySide6 / current numpy) — they cannot share a process (PRD §4.1, §4.3,
ADR-0004/0006). The driver therefore communicates over the filesystem (the SMD
in, the model out) and a one-line JSON status on stdout. The de-risk recon
(M0.5 S1) confirmed ``maven_class.__init__`` builds plain objects and does not
spawn a Qt app, so it runs headless under ``QT_QPA_PLATFORM=offscreen``.

The sidecar interpreter is located via the ``sidecar_python`` argument, then the
``TETHER_SIDECAR_PYTHON`` environment variable, then the installer's sibling
``envs/sidecar`` derived from :data:`sys.prefix` (ADR-0049/0051 — a shortcut or
``PATH``-shim launch never runs the conda ``activate.d`` hook that exports the
variable). There is still no hard-coded path: the last step resolves relative to the
running interpreter, so any env built from ``sidecar/conda-lock.yml`` (with tMAVEN
installed) works, and a development checkout simply falls through to the error.

The live round-trip is exercised by ``@pytest.mark.sidecar`` tests, which are
**deselected from the CI matrix** (CI has no sidecar env); the pure model-reader
and dwell logic are covered by ordinary unit tests.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from tether.idealize._sidecar_runner import STATUS_PREFIX
from tether.idealize.smd import DEFAULT_GROUP, read_smd

if TYPE_CHECKING:
    from collections.abc import Callable
    from os import PathLike

#: Environment variable naming the sidecar interpreter (overridden by the arg).
SIDECAR_ENV_VAR = "TETHER_SIDECAR_PYTHON"
#: Root group of a tMAVEN model file (Appendix D.2).
MODEL_GROUP = "model"
#: State index assigned to frames outside the analysis window / not idealized.
NO_STATE = -1
#: Default timeout for the tMAVEN SMD open-check (:func:`check_smd_opens`). No fit
#: runs, but a cold ``tmaven``/Numba import is not instant, so the window is generous
#: (mirrors the startup liveness probe, :data:`supervisor.DEFAULT_PROBE_TIMEOUT`).
DEFAULT_OPEN_CHECK_TIMEOUT = 120.0

_RUNNER = Path(__file__).with_name("_sidecar_runner.py")


class SidecarError(RuntimeError):
    """The sidecar process failed (non-zero exit, crash, or reported error).

    ``transient`` distinguishes a **process-level** failure a fresh process may
    recover (a crash or timeout — the default) from a **deterministic** one the
    sidecar cleanly reported (a fit error on this data), which a restart cannot fix.
    :func:`tether.idealize.supervisor.supervise_idealize` retries only transient
    failures.
    """

    def __init__(self, *args: object, transient: bool = True) -> None:
        super().__init__(*args)
        self.transient = transient


@dataclass
class StateModel:
    """An ensemble idealization model (the Appendix D.2 ``model`` group).

    ``means``/``variances``/``tmatrix`` describe the states; ``idealized`` is the
    ``(n_molecules, n_frames)`` float FRET level per frame (NaN outside each
    molecule's analysis window), and ``ran`` lists the SMD-order indices of the
    molecules the model was fit on.

    ``rates``/``pi``/``frac``/``priors`` are the **population-model** members of the
    Appendix-D.2 ``model`` group (PRD §10): ``rates`` is the N×N transition-rate
    matrix (``@rate_type='Transition Matrix'``); ``pi`` is the **unnormalized**
    variational Dirichlet posterior over the initial state (concentration parameters
    — tMAVEN's ``pik = pi0 + Σ responsibilities``; divide by ``pi.sum()`` for the
    initial-state *probability* vector, not the stored array directly); ``frac`` is
    the **normalized** state-population vector (sums to 1); and ``priors`` are the
    variational prior hyperparameters (the ``priors/`` subgroup — ``a_prior``,
    ``b_prior``, ``beta_prior``, ``mu_prior``, ``pi_prior``, ``tm_prior``). All are
    optional: :func:`read_model` reads each when the model file carries it and leaves
    it ``None`` otherwise (a threshold/k-means model has no rate matrix or priors).
    """

    model_type: str
    nstates: int
    means: np.ndarray
    variances: np.ndarray | None = None
    tmatrix: np.ndarray | None = None
    norm_tmatrix: np.ndarray | None = None
    elbo: float | None = None
    dtype: str = "FRET"
    likelihood: np.ndarray | None = None
    ran: np.ndarray = field(default_factory=lambda: np.empty(0, dtype="int64"))
    idealized: np.ndarray | None = None
    rates: np.ndarray | None = None
    pi: np.ndarray | None = None
    frac: np.ndarray | None = None
    priors: dict[str, np.ndarray] | None = None


@dataclass(frozen=True)
class Dwell:
    """A single contiguous dwell in one molecule's idealized state path."""

    molecule_index: int  # index into the SMD molecule order
    state: int  # state index into StateModel.means
    start: int  # first frame of the dwell
    length: int  # number of frames


@dataclass
class IdealizationResult:
    """Outcome of a headless vbFRET round-trip.

    ``state_paths`` maps each idealized SMD molecule index to its integer state
    path (``NO_STATE`` outside the window). ``molecule_keys`` carries the
    Tether ``molecule_key`` of each SMD molecule (from the superset group) when
    present, so a result row maps back to its store molecule without relying on
    order; the general scrambled-order return leg is handled separately by
    :func:`tether.idealize.match_return_leg`.
    """

    model: StateModel
    state_paths: dict[int, np.ndarray]
    dwells: list[Dwell]
    model_path: Path
    status: dict
    molecule_keys: list[str] | None = None


@dataclass(frozen=True)
class SMDOpenCheck:
    """What tMAVEN's own loader parsed from an SMD (the standalone-GUI load path).

    Produced by :func:`check_smd_opens`, which drives the exact loader the standalone
    tMAVEN GUI's *File → Load SMD* uses. ``n_molecules``/``n_frames``/``n_channels`` are
    the trace dimensions tMAVEN read; ``raw_shape``/``raw_sum`` fingerprint the loaded
    intensities so a caller can assert the trace data survived the hand-off; and
    ``pre_list``/``post_list`` are the per-trace analysis windows tMAVEN read back from
    the ``tMAVEN`` group (the windows Tether rides along, PRD §7.4).
    """

    n_molecules: int
    n_frames: int
    n_channels: int
    raw_shape: tuple[int, ...]
    raw_sum: float
    pre_list: np.ndarray
    post_list: np.ndarray
    classes: np.ndarray


def _decode(value) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def read_model(path: str | PathLike[str], group: str = MODEL_GROUP) -> StateModel:
    """Read a tMAVEN/Appendix-D.2 ``model`` HDF5 file into a :class:`StateModel`.

    Tolerant of optional members: only ``mean`` (state levels) is required;
    ``var``/``tmatrix``/``norm_tmatrix``/``idealized``/``likelihood``/``ran`` are
    read when present. ``elbo`` is the final ELBO (``likelihood[-1, 0]``).
    """
    import h5py

    path = Path(path)
    with h5py.File(path, "r") as f:
        if group not in f:
            raise KeyError(f"group {group!r} not found in {path}")
        g = f[group]

        if "mean" not in g:
            raise ValueError(f"{path}: model group has no 'mean' (state levels)")
        means = np.asarray(g["mean"][()], dtype="float64").reshape(-1)
        nstates = int(g["nstates"][()]) if "nstates" in g else int(means.shape[0])

        def _arr(name: str) -> np.ndarray | None:
            return np.asarray(g[name][()], dtype="float64") if name in g else None

        likelihood = _arr("likelihood")
        elbo = None
        if likelihood is not None and likelihood.size:
            flat = likelihood.reshape(likelihood.shape[0], -1)
            elbo = float(flat[-1, 0])

        ran = (
            np.asarray(g["ran"][()], dtype="int64").reshape(-1)
            if "ran" in g
            else np.arange(nstates, dtype="int64")[:0]
        )

        model_type = _decode(g.attrs.get("type", "")) or "unknown"
        dtype_val = _decode(g["dtype"][()]) if "dtype" in g else "FRET"

        # The population-model members (Appendix D.2). ``priors`` is a subgroup of
        # named hyperparameter arrays; read every dataset in it (a model without a
        # priors group -- e.g. threshold/k-means -- yields None).
        priors: dict[str, np.ndarray] | None = None
        if "priors" in g and isinstance(g["priors"], h5py.Group):
            pg = g["priors"]
            priors = {
                name: np.asarray(pg[name][()], dtype="float64")
                for name in pg
                if isinstance(pg[name], h5py.Dataset)
            } or None

        return StateModel(
            model_type=model_type,
            nstates=nstates,
            means=means,
            variances=_arr("var"),
            tmatrix=_arr("tmatrix"),
            norm_tmatrix=_arr("norm_tmatrix"),
            elbo=elbo,
            dtype=dtype_val,
            likelihood=likelihood,
            ran=ran,
            idealized=_arr("idealized"),
            rates=_arr("rates"),
            pi=_arr("pi"),
            frac=_arr("frac"),
            priors=priors,
        )


def states_from_idealized(idealized: np.ndarray, means: np.ndarray) -> np.ndarray:
    """Map a float idealized array to integer state indices (nearest mean).

    ``idealized`` is ``(n_molecules, n_frames)``; non-finite entries (outside the
    analysis window) map to :data:`NO_STATE`. Returns an ``int64`` array of the
    same shape.
    """
    idealized = np.asarray(idealized, dtype="float64")
    means = np.asarray(means, dtype="float64").reshape(-1)
    if means.size == 0:
        raise ValueError("means must be non-empty to assign states")
    out = np.full(idealized.shape, NO_STATE, dtype="int64")
    finite = np.isfinite(idealized)
    if finite.any():
        # Nearest state by absolute distance to each state level.
        diffs = np.abs(idealized[finite][:, None] - means[None, :])
        out[finite] = np.argmin(diffs, axis=1).astype("int64")
    return out


def dwells_from_states(state_path: np.ndarray, molecule_index: int) -> list[Dwell]:
    """Run-length-encode one molecule's 1-D integer state path into dwells.

    Contiguous runs of the same state are emitted as :class:`Dwell` segments;
    :data:`NO_STATE` frames are skipped (they break a run but are not dwells).
    """
    path = np.asarray(state_path, dtype="int64").reshape(-1)
    dwells: list[Dwell] = []
    start = 0
    n = path.shape[0]
    while start < n:
        state = int(path[start])
        end = start + 1
        while end < n and path[end] == state:
            end += 1
        if state != NO_STATE:
            dwells.append(
                Dwell(
                    molecule_index=molecule_index,
                    state=state,
                    start=start,
                    length=end - start,
                )
            )
        start = end
    return dwells


def bundled_sidecar_python() -> Path | None:
    """The installer's sibling sidecar interpreter, or ``None`` outside a bundled install.

    The constructor installer lays the two environments down as siblings —
    ``<prefix>/envs/tether`` and ``<prefix>/envs/sidecar`` (ADR-0049) — and wires
    ``TETHER_SIDECAR_PYTHON`` through a conda ``activate.d`` hook. That hook only runs
    when the environment is *activated*, so a GUI started from a menu shortcut, a
    ``PATH`` shim or a ``.desktop`` entry (ADR-0051) never sees it and idealization
    would fail on an otherwise correct install.

    Deriving the sibling from :data:`sys.prefix` covers every launch path without
    relying on the shell. Returns ``None`` when the sibling is absent — a development
    checkout — so the caller still reports the actionable "set the env var" error
    rather than a confusing missing-path one.
    """
    envs = Path(sys.prefix).parent
    candidate = (
        envs / "sidecar" / "python.exe" if os.name == "nt" else envs / "sidecar" / "bin" / "python"
    )
    return candidate if candidate.exists() else None


def resolve_sidecar_python(sidecar_python: str | PathLike[str] | None) -> Path:
    """Resolve the sidecar interpreter from the argument, environment, or install layout.

    Precedence: the explicit argument, then ``TETHER_SIDECAR_PYTHON``, then the
    installer's sibling ``envs/sidecar`` (:func:`bundled_sidecar_python`). The last
    step is what makes idealization work when the app is launched from a shortcut
    rather than an activated environment.

    Raises a clear :class:`SidecarError` (not a bare ``KeyError``) when none resolves,
    so callers get an actionable message. Both failures are **deterministic
    configuration errors** (``transient=False``): no auto-restart can conjure an
    interpreter mid-run, so :func:`tether.idealize.supervisor.supervise_idealize`
    must fail fast rather than burn the restart budget on them.
    """
    candidate = sidecar_python or os.environ.get(SIDECAR_ENV_VAR) or bundled_sidecar_python()
    if not candidate:
        raise SidecarError(
            "no sidecar interpreter: pass sidecar_python= or set "
            f"{SIDECAR_ENV_VAR} to a Python in an env built from "
            "sidecar/conda-lock.yml with tMAVEN installed",
            transient=False,
        )
    path = Path(candidate)
    if not path.exists():
        raise SidecarError(f"sidecar interpreter does not exist: {path}", transient=False)
    return path


def _sidecar_env() -> dict[str, str]:
    """Subprocess environment for launching the sidecar (headless Qt, sync loader).

    Shared by :func:`run_vbfret` and :func:`tether.idealize.supervisor.probe_sidecar`
    so both launch paths stay in sync.
    """
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("NAPARI_ASYNC", "0")
    return env


def run_vbfret(
    smd_path: str | PathLike[str],
    *,
    sidecar_python: str | PathLike[str] | None = None,
    model_type: str = "vbconhmm",
    nstates: int = 2,
    group: str = DEFAULT_GROUP,
    nrestarts: int | None = None,
    model_out: str | PathLike[str] | None = None,
    timeout: float | None = 1800.0,
) -> IdealizationResult:
    """Run a headless vbFRET-family idealization on an existing SMD file.

    Parameters
    ----------
    smd_path:
        An SMD-HDF5 file (e.g. written by :func:`tether.idealize.write_smd`).
    sidecar_python:
        The sidecar interpreter; falls back to ``$TETHER_SIDECAR_PYTHON``.
    model_type:
        One of the keys in :data:`tether.idealize._sidecar_runner._DISPATCH`
        (default ``"vbconhmm"`` — the consensus VB-HMM behind the reference
        model fixture and the M0.5 parity target).
    nstates:
        Number of states (for fixed-``nstates`` model types).
    nrestarts:
        Optional override of tMAVEN's restart count (lower = faster, for tests).
    timeout:
        Seconds before the sidecar is killed (``None`` to wait indefinitely).

    Returns
    -------
    IdealizationResult
        The fitted model plus per-molecule state paths and dwell segments.
    """
    py = resolve_sidecar_python(sidecar_python)
    smd_path = Path(smd_path)
    if not smd_path.exists():
        raise FileNotFoundError(smd_path)

    owns_model_out = model_out is None
    if owns_model_out:
        model_out = smd_path.with_name(smd_path.stem + ".model.hdf5")
    model_out = Path(model_out)

    cmd = [
        str(py),
        str(_RUNNER),
        str(smd_path),
        group,
        model_type,
        str(int(nstates)),
        str(model_out),
    ]
    if nrestarts is not None:
        cmd.append(str(int(nrestarts)))

    env = _sidecar_env()

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        # subprocess.run kills the child on timeout; surface it as a SidecarError
        # (the documented failure mode) with the stderr tail, like the paths below.
        # A hung/killed process is a transient (restart-worthy) failure.
        raise SidecarError(
            f"sidecar idealization timed out after {timeout}s\n"
            f"--- stderr (tail) ---\n{_tail(exc.stderr or '')}",
            transient=True,
        ) from exc
    status = _parse_status(proc.stdout)
    sidecar_ok = status is not None and status.get("ok") is True

    if sidecar_ok and proc.returncode != 0:
        # The sidecar reported success and flushed its status *before* a non-zero exit
        # (a teardown-phase crash of the native PyQt5/Numba stack after the fit). run()
        # writes and closes the model file before that flush, so the on-disk model is
        # complete — salvage it rather than discard a finished (up to 1800 s) fit to a
        # restart. Only a genuinely unreadable model is a transient process failure.
        try:
            model = read_model(model_out)
        except (OSError, KeyError, ValueError) as exc:
            raise SidecarError(
                "sidecar reported success but exited non-zero with no readable model "
                f"(exit {proc.returncode})\n"
                f"--- stderr (tail) ---\n{_tail(proc.stderr)}",
                transient=True,
            ) from exc
    elif not sidecar_ok:
        # A status the sidecar itself emitted with ok=False is a *deterministic* fit
        # failure (bad data, a model that cannot fit these traces) — re-launching the
        # same input only repeats it, so it is not transient. A missing/garbled status
        # (a crash before any output) is a process-level failure — transient and worth a
        # supervised restart (:mod:`tether.idealize.supervisor`).
        reported_error = status is not None and status.get("ok") is False
        detail = status.get("error") if status is not None else None
        if status is None:
            reason = "no status emitted"
        else:
            reason = detail or "sidecar reported failure without detail"
        raise SidecarError(
            f"sidecar idealization failed (exit {proc.returncode}): {reason}\n"
            f"--- stderr (tail) ---\n{_tail(proc.stderr)}",
            transient=not reported_error,
        )
    else:
        model = read_model(model_out)

    smd = read_smd(smd_path, group)
    molecule_keys = smd.molecule_keys

    state_paths: dict[int, np.ndarray] = {}
    dwells: list[Dwell] = []
    if model.idealized is not None and model.means.size:
        all_paths = states_from_idealized(model.idealized, model.means)
        ran = model.ran if model.ran.size else np.arange(all_paths.shape[0], dtype="int64")
        for mol_idx in ran.tolist():
            row = all_paths[mol_idx]
            state_paths[mol_idx] = row
            dwells.extend(dwells_from_states(row, mol_idx))

    return IdealizationResult(
        model=model,
        state_paths=state_paths,
        dwells=dwells,
        model_path=model_out,
        status=status,
        molecule_keys=molecule_keys,
    )


def run_vbconhmm(
    smd_path: str | PathLike[str], *, nstates: int = 2, **kwargs
) -> IdealizationResult:
    """Run a headless **consensus VB-HMM** (``vbconhmm``) idealization.

    The global variational-Bayes HMM fit across the SMD's molecules [Bronson2009];
    the default idealizer behind the M0.5 parity target and the reference model
    fixture. A thin named alias for :func:`run_vbfret` with ``model_type`` fixed, so
    the M6 analysis suite has an explicit population-model entry point. All other
    keywords (``sidecar_python``, ``nrestarts``, ``timeout``, ``model_out``,
    ``group``) forward unchanged.
    """
    return run_vbfret(smd_path, model_type="vbconhmm", nstates=nstates, **kwargs)


def run_ebhmm(smd_path: str | PathLike[str], *, nstates: int = 2, **kwargs) -> IdealizationResult:
    """Run a headless **ebFRET** (empirical-Bayes HMM, ``ebhmm``) idealization.

    ebFRET fits a hierarchical/empirical-Bayes HMM that pools information across the
    population of molecules to infer a consensus kinetic model, sharpening rate and
    state estimates that vary widely trace-by-trace [vandeMeent2014]. A thin named
    alias for :func:`run_vbfret` with ``model_type='ebhmm'``; keywords forward as in
    :func:`run_vbconhmm`.

    References
    ----------
    [vandeMeent2014] van de Meent, Bronson, Wiggins & Gonzalez. "Empirical Bayes
        methods enable advanced population-level analyses of single-molecule FRET
        experiments." Biophysical Journal (2014).
    """
    return run_vbfret(smd_path, model_type="ebhmm", nstates=nstates, **kwargs)


def _default_run(
    cmd: list[str], env: dict[str, str], timeout: float | None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - cmd is a resolved interpreter + our runner
        cmd,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
        check=False,
    )


def check_smd_opens(
    smd_path: str | PathLike[str],
    *,
    sidecar_python: str | PathLike[str] | None = None,
    group: str = DEFAULT_GROUP,
    timeout: float | None = DEFAULT_OPEN_CHECK_TIMEOUT,
    _run: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> SMDOpenCheck:
    """Confirm ``smd_path`` opens in the standalone tMAVEN GUI, via tMAVEN's own loader.

    Launches the sidecar interpreter on :mod:`tether.idealize._sidecar_runner`'s
    ``--load-check`` fast path, which drives ``maven.io.load_smdtmaven_hdf5`` — the
    exact loader tMAVEN's *File → Load SMD* menu uses (``pysmd.load_smd_in_hdf5`` for
    the standard ``data``/``sources`` groups plus the per-trace ``tMAVEN`` window/class
    group). A Tether-authored SMD that loads cleanly here is one the standalone GUI
    opens (PRD §7.4, issue #13). No fit runs — this is the scripted half of the M9
    hand-off verification (the manual GUI leg is documented separately).

    Returns an :class:`SMDOpenCheck` with the trace dimensions, a raw-intensity
    checksum, and the analysis windows tMAVEN read back. Raises :class:`SidecarError`
    if the interpreter is unset/missing, the launch fails, the load times out, or
    tMAVEN could not open the file. ``_run`` is an injectable subprocess launcher
    (test seam), defaulting to :func:`subprocess.run`.
    """
    py = resolve_sidecar_python(sidecar_python)
    smd_path = Path(smd_path)
    if not smd_path.exists():
        raise FileNotFoundError(smd_path)

    cmd = [str(py), str(_RUNNER), "--load-check", str(smd_path), group]
    env = _sidecar_env()

    runner = _run if _run is not None else _default_run
    try:
        proc = runner(cmd, env, timeout)
    except subprocess.TimeoutExpired as exc:
        raise SidecarError(
            f"tMAVEN SMD open-check timed out after {timeout}s\n"
            f"--- stderr (tail) ---\n{_tail(getattr(exc, 'stderr', None))}",
            transient=True,
        ) from exc
    except OSError as exc:  # interpreter vanished / not executable between resolve + launch
        raise SidecarError(
            f"tMAVEN SMD open-check could not launch: {exc}", transient=True
        ) from exc

    status = _parse_status(proc.stdout or "")
    if proc.returncode == 0 and status is not None and status.get("ok"):
        return SMDOpenCheck(
            n_molecules=int(status["nmol"]),
            n_frames=int(status["nt"]),
            n_channels=int(status.get("ncolors", 0)),
            raw_shape=tuple(int(s) for s in status.get("raw_shape", ())),
            raw_sum=float(status.get("raw_sum", float("nan"))),
            pre_list=np.asarray(status.get("pre_list", []), dtype="int64"),
            post_list=np.asarray(status.get("post_list", []), dtype="int64"),
            classes=np.asarray(status.get("classes", []), dtype="int64"),
        )

    detail = status.get("error") if status is not None else None
    message = detail or f"tMAVEN could not open {smd_path.name} (exit {proc.returncode})"
    tail = _tail(proc.stderr or "")
    if tail:
        message += f"\n--- stderr (tail) ---\n{tail}"
    # A status tMAVEN itself emitted with ok=False is a deterministic open failure (a
    # malformed/non-SMD file); a missing status (a crash before any output) is a
    # process-level failure — transient and worth a retry by any supervising caller.
    reported = status is not None and status.get("ok") is False
    raise SidecarError(message, transient=not reported)


def _parse_status(stdout: str) -> dict | None:
    """Recover the runner's JSON status line (last one wins) from stdout.

    Only a JSON **object** counts: a stray ``STATUS_PREFIX`` line carrying a non-object
    payload (a bare number/string/array) is ignored, so every caller can safely
    ``status.get(...)`` — this keeps :func:`tether.idealize.supervisor.probe_sidecar`'s
    "never raises" contract intact.
    """
    status: dict | None = None
    for line in stdout.splitlines():
        if line.startswith(STATUS_PREFIX):
            try:
                parsed = json.loads(line[len(STATUS_PREFIX) :])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                status = parsed
    return status


def _tail(text: str | bytes | None, n: int = 40) -> str:
    # TimeoutExpired.stderr can be bytes even under text=True; normalise first.
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    lines = (text or "").splitlines()
    return "\n".join(lines[-n:])

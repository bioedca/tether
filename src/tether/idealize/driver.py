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

The sidecar interpreter is located via the ``sidecar_python`` argument or the
``TETHER_SIDECAR_PYTHON`` environment variable; there is no hard-coded path, so
any env built from ``sidecar/conda-lock.yml`` (with tMAVEN installed) works.

The live round-trip is exercised by ``@pytest.mark.sidecar`` tests, which are
**deselected from the CI matrix** (CI has no sidecar env); the pure model-reader
and dwell logic are covered by ordinary unit tests.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from tether.idealize._sidecar_runner import STATUS_PREFIX
from tether.idealize.smd import DEFAULT_GROUP, read_smd

if TYPE_CHECKING:
    from os import PathLike

#: Environment variable naming the sidecar interpreter (overridden by the arg).
SIDECAR_ENV_VAR = "TETHER_SIDECAR_PYTHON"
#: Root group of a tMAVEN model file (Appendix D.2).
MODEL_GROUP = "model"
#: State index assigned to frames outside the analysis window / not idealized.
NO_STATE = -1

_RUNNER = Path(__file__).with_name("_sidecar_runner.py")


class SidecarError(RuntimeError):
    """The sidecar process failed (non-zero exit, crash, or reported error)."""


@dataclass
class StateModel:
    """An ensemble idealization model (the Appendix D.2 ``model`` group).

    ``means``/``variances``/``tmatrix`` describe the states; ``idealized`` is the
    ``(n_molecules, n_frames)`` float FRET level per frame (NaN outside each
    molecule's analysis window), and ``ran`` lists the SMD-order indices of the
    molecules the model was fit on.
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


def resolve_sidecar_python(sidecar_python: str | PathLike[str] | None) -> Path:
    """Resolve the sidecar interpreter from the argument or environment.

    Raises a clear :class:`SidecarError` (not a bare ``KeyError``) when neither
    is set, so callers get an actionable message.
    """
    candidate = sidecar_python or os.environ.get(SIDECAR_ENV_VAR)
    if not candidate:
        raise SidecarError(
            "no sidecar interpreter: pass sidecar_python= or set "
            f"{SIDECAR_ENV_VAR} to a Python in an env built from "
            "sidecar/conda-lock.yml with tMAVEN installed"
        )
    path = Path(candidate)
    if not path.exists():
        raise SidecarError(f"sidecar interpreter does not exist: {path}")
    return path


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

    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("NAPARI_ASYNC", "0")

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
        raise SidecarError(
            f"sidecar idealization timed out after {timeout}s\n"
            f"--- stderr (tail) ---\n{_tail(exc.stderr or '')}"
        ) from exc
    status = _parse_status(proc.stdout)
    if proc.returncode != 0 or status is None or not status.get("ok"):
        detail = (status or {}).get("error") if status else None
        raise SidecarError(
            "sidecar idealization failed "
            f"(exit {proc.returncode}): {detail or 'no status emitted'}\n"
            f"--- stderr (tail) ---\n{_tail(proc.stderr)}"
        )

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


def _parse_status(stdout: str) -> dict | None:
    """Recover the runner's JSON status line (last one wins) from stdout."""
    status: dict | None = None
    for line in stdout.splitlines():
        if line.startswith(STATUS_PREFIX):
            try:
                status = json.loads(line[len(STATUS_PREFIX) :])
            except json.JSONDecodeError:
                continue
    return status


def _tail(text: str, n: int = 40) -> str:
    lines = text.splitlines()
    return "\n".join(lines[-n:])

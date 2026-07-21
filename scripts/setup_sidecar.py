# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Guided setup for the isolated tMAVEN idealization sidecar (PRD §4.3, §7.4; issue #13).

Turns a fresh checkout into a working ``$TETHER_SIDECAR_PYTHON`` in one command. The
sidecar is the PyQt5 / ``numpy<2`` environment that drives ``tmaven.maven.maven_class``
headlessly (:mod:`tether.idealize.driver`); it is deliberately isolated from Tether's
base stack (PySide6 / current numpy — ADR-0004/0006) and so is *not* part of the base
``conda-lock.yml``.

Two things live outside the committed ``sidecar/conda-lock.yml`` and are therefore
easy to get wrong by hand — this script encodes them so a user (or CI, or the M9
cross-OS hand-off check) does the same steps every time:

1. **tMAVEN itself** — the GPL reference app driven over IPC, pinned by commit and
   installed from git (never a conda-lock dep).
2. **``setuptools<81``** — tMAVEN imports the legacy ``pkg_resources`` API at runtime
   without declaring it; setuptools deprecated ``pkg_resources`` by 80.9.0 (still shipped
   through 81.0.0) and removed it in 82.0.0, so it must be pinned back into the sidecar
   env alongside tMAVEN (``<81`` is the bound that setuptools' own deprecation warning
   names).

Flow (each phase is skippable):

* **create** the ``tether-sidecar`` env from ``sidecar/conda-lock.yml`` with a detected
  conda front-end (``conda-lock install``, else ``micromamba``/``mamba create -f``).
  Skipped when ``--python`` targets an already-built interpreter (e.g. in CI, where the
  micromamba action restored the env already).
* **install** the pinned tMAVEN + ``setuptools<81`` (+ ``pytest`` with ``--with-pytest``)
  into the sidecar interpreter — byte-for-byte the command ``sidecar.yml`` runs.
* **probe** the result by launching :mod:`tether.idealize._sidecar_runner` ``--probe``
  (import + instantiate ``maven_class``, no fit), the same liveness check the batch
  supervisor uses (:func:`tether.idealize.supervisor.probe_sidecar`).

On success it prints the resolved interpreter and the ``export``/``$env:`` line to set
``TETHER_SIDECAR_PYTHON``. This is a stdlib-only orchestrator (no ``tether`` import
needed) so it runs from any Python on a clean checkout.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

#: Pinned tMAVEN reference (kept in lockstep with ``.github/workflows/sidecar.yml``'s
#: ``TMAVEN_SPEC`` env — ``test_setup_sidecar.py`` binds the two). tMAVEN is the GPL
#: reference app driven over IPC, not a conda-lock dep, so it is git-installed here.
DEFAULT_TMAVEN_SPEC = "git+https://github.com/GonzalezBiophysicsLab/tmaven.git@10f4230"
#: setuptools pin restoring the ``pkg_resources`` API tMAVEN imports at runtime
#: (deprecated by setuptools 80.9.0, still shipped through 81.0.0, removed in 82.0.0;
#: ``<81`` is the bound that setuptools' own deprecation warning names), matching
#: ``sidecar.yml``'s ``"setuptools<81"``.
SETUPTOOLS_PIN = "setuptools<81"
#: Default name of the created sidecar env.
DEFAULT_ENV_NAME = "tether-sidecar"
#: Conda front-ends tried, in order, when ``--conda-exe`` is not given.
CONDA_FRONTENDS = ("micromamba", "mamba", "conda")

_REPO_ROOT = Path(__file__).resolve().parents[1]
#: The committed sidecar lock (isolated numpy<2 / PyQt5 stack).
DEFAULT_LOCK = _REPO_ROOT / "sidecar" / "conda-lock.yml"
#: The headless runner whose ``--probe`` fast-path we launch to verify liveness.
_SIDECAR_RUNNER = _REPO_ROOT / "src" / "tether" / "idealize" / "_sidecar_runner.py"
#: Must match ``tether.idealize._sidecar_runner.STATUS_PREFIX`` (bound by a contract test).
STATUS_PREFIX = "TETHER_SIDECAR_STATUS "


class SetupError(RuntimeError):
    """A guided-setup step failed (front-end missing, env create/install/probe failed)."""


def detect_conda_frontend(explicit: str | None = None) -> str:
    """Resolve the conda front-end: ``explicit`` if given, else the first on PATH.

    Raises :class:`SetupError` naming the candidates when none is found.
    """
    if explicit:
        return explicit
    for name in CONDA_FRONTENDS:
        if shutil.which(name):
            return name
    raise SetupError(
        "no conda front-end found on PATH (looked for "
        f"{', '.join(CONDA_FRONTENDS)}); install one or pass --conda-exe / --python"
    )


def build_env_create_cmd(frontend: str, env_name: str, lock: Path) -> list[str]:
    """Command to create ``env_name`` from the conda-lock ``lock`` with ``frontend``.

    ``conda-lock`` is the canonical, front-end-agnostic installer for a unified
    ``conda-lock.yml`` (it never re-solves — pin-and-hold, PRD §4.1), so it is used
    when available. ``micromamba``/``mamba`` create straight from the lock file with
    ``-f``; plain ``conda`` cannot install a unified lock without ``conda-lock``.
    """
    if shutil.which("conda-lock"):
        return [
            "conda-lock",
            "install",
            "--conda",
            frontend,
            "--name",
            env_name,
            str(lock),
        ]
    base = os.path.basename(frontend).lower()
    if "micromamba" in base or base.startswith("mamba") or "mamba" in base:
        return [frontend, "create", "-y", "-n", env_name, "-f", str(lock)]
    raise SetupError(
        f"{frontend!r} cannot install a unified conda-lock file directly; install "
        "`conda-lock` (pip install conda-lock) or use micromamba/mamba, or pass "
        "--python to target an already-built sidecar interpreter"
    )


def build_pip_cmd(sidecar_python: str, *, tmaven_spec: str, with_pytest: bool) -> list[str]:
    """The offline-safe ``pip install`` of the non-lock deps into the sidecar interpreter.

    Mirrors ``sidecar.yml`` exactly: ``pip install --no-build-isolation [pytest]
    setuptools<81 <tmaven_spec>``. ``--no-build-isolation`` keeps pip from spinning up a
    fresh PEP-517 build env (which would re-pull an unpinned setuptools).
    """
    cmd = [sidecar_python, "-m", "pip", "install", "--no-build-isolation"]
    if with_pytest:
        cmd.append("pytest")
    cmd.append(SETUPTOOLS_PIN)
    cmd.append(tmaven_spec)
    return cmd


def resolve_env_python(frontend: str, env_name: str) -> str:
    """Resolve the interpreter path of ``env_name`` via ``<frontend> run -n``.

    Front-end-agnostic (micromamba/mamba/conda all support ``run -n NAME``), so we do
    not have to guess the platform-specific ``envs/<name>/bin|Scripts`` layout.
    """
    try:
        out = subprocess.run(  # noqa: S603 - frontend is a resolved conda executable
            [frontend, "run", "-n", env_name, "python", "-c", "import sys; print(sys.executable)"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:  # front-end not launchable (e.g. a typo'd --conda-exe)
        raise SetupError(f"could not launch conda front-end {frontend!r}: {exc}") from exc
    path = (out.stdout or "").strip().splitlines()[-1].strip() if out.stdout.strip() else ""
    if out.returncode != 0 or not path:
        raise SetupError(
            f"could not resolve the interpreter for env {env_name!r} via {frontend!r} "
            f"(exit {out.returncode}): {(out.stderr or '').strip()}"
        )
    return path


def run_probe(sidecar_python: str, *, timeout: float | None = 120.0) -> dict:
    """Launch ``_sidecar_runner.py --probe`` in ``sidecar_python`` and return its status.

    Raises :class:`SetupError` on a launch failure, timeout, or a non-``ok`` probe
    status (the same import+instantiate liveness check the batch supervisor runs).
    """
    if not _SIDECAR_RUNNER.exists():  # pragma: no cover - only if the tree is broken
        raise SetupError(f"sidecar runner not found at {_SIDECAR_RUNNER}")
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("NAPARI_ASYNC", "0")
    try:
        proc = subprocess.run(  # noqa: S603 - sidecar_python is a resolved interpreter
            [sidecar_python, str(_SIDECAR_RUNNER), "--probe"],
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SetupError(f"sidecar liveness probe timed out after {timeout}s") from exc
    except OSError as exc:
        raise SetupError(f"sidecar liveness probe could not launch: {exc}") from exc

    status = _parse_status(proc.stdout or "")
    if proc.returncode == 0 and status is not None and status.get("ok"):
        return status
    detail = status.get("error") if status is not None else None
    tail = "\n".join((proc.stderr or "").splitlines()[-20:])
    raise SetupError(
        (detail or f"sidecar liveness probe failed (exit {proc.returncode})")
        + (f"\n--- stderr (tail) ---\n{tail}" if tail else "")
    )


def _parse_status(stdout: str) -> dict | None:
    """Recover the runner's JSON status object from stdout (last one wins)."""
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


def _run(cmd: list[str], *, dry_run: bool) -> None:
    """Echo and run ``cmd`` (or just echo it under ``--dry-run``); raise on failure."""
    print("  $ " + " ".join(cmd))
    if dry_run:
        return
    try:
        result = subprocess.run(cmd, check=False)  # noqa: S603 - callers pass resolved argv
    except OSError as exc:  # executable not launchable (missing / not executable)
        raise SetupError(f"command could not launch: {' '.join(cmd)} ({exc})") from exc
    if result.returncode != 0:
        raise SetupError(f"command failed (exit {result.returncode}): {' '.join(cmd)}")


def _export_line(sidecar_python: str) -> str:
    """The shell line the user runs to point Tether at the sidecar interpreter."""
    if os.name == "nt":
        return f'$env:TETHER_SIDECAR_PYTHON = "{sidecar_python}"'
    return f'export TETHER_SIDECAR_PYTHON="{sidecar_python}"'


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="setup_sidecar",
        description="Build and verify the isolated tMAVEN idealization sidecar env.",
    )
    parser.add_argument(
        "--python",
        metavar="PATH",
        help="use this existing interpreter as the sidecar (skips env creation)",
    )
    parser.add_argument(
        "--conda-exe",
        metavar="EXE",
        help=f"conda front-end for env creation (default: first of {', '.join(CONDA_FRONTENDS)})",
    )
    parser.add_argument("--env-name", default=DEFAULT_ENV_NAME, help="name of the sidecar env")
    parser.add_argument(
        "--lock-file",
        type=Path,
        default=DEFAULT_LOCK,
        help="conda-lock file to create the env from",
    )
    parser.add_argument(
        "--tmaven-spec",
        default=os.environ.get("TMAVEN_SPEC", DEFAULT_TMAVEN_SPEC),
        help="pip spec for tMAVEN (default: $TMAVEN_SPEC or the pinned commit)",
    )
    parser.add_argument(
        "--with-pytest", action="store_true", help="also install pytest (for the live suite)"
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="do not pip-install tMAVEN (assume it is already present)",
    )
    parser.add_argument("--no-probe", action="store_true", help="skip the liveness probe")
    parser.add_argument(
        "--dry-run", action="store_true", help="print the commands without running them"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        # 1) Resolve the sidecar interpreter (create the env unless --python was given).
        if args.python:
            sidecar_python = args.python
            if not args.dry_run and not Path(sidecar_python).exists():
                raise SetupError(f"--python interpreter does not exist: {sidecar_python}")
            print(f"[1/3] Using existing sidecar interpreter: {sidecar_python}")
        else:
            if not args.lock_file.exists() and not args.dry_run:
                raise SetupError(f"conda-lock file not found: {args.lock_file}")
            frontend = detect_conda_frontend(args.conda_exe)
            print(f"[1/3] Creating env {args.env_name!r} from {args.lock_file} (via {frontend})")
            _run(
                build_env_create_cmd(frontend, args.env_name, args.lock_file), dry_run=args.dry_run
            )
            sidecar_python = (
                f"<{args.env_name}>/python"
                if args.dry_run
                else resolve_env_python(frontend, args.env_name)
            )

        # 2) Install the non-lock deps (tMAVEN + setuptools<81 [+ pytest]).
        if args.skip_install:
            print("[2/3] Skipping tMAVEN install (--skip-install)")
        else:
            print(f"[2/3] Installing tMAVEN + {SETUPTOOLS_PIN} into the sidecar env")
            _run(
                build_pip_cmd(
                    sidecar_python, tmaven_spec=args.tmaven_spec, with_pytest=args.with_pytest
                ),
                dry_run=args.dry_run,
            )

        # 3) Verify the env can build the tMAVEN driver (liveness).
        if args.no_probe or args.dry_run:
            print("[3/3] Skipping liveness probe" + (" (--dry-run)" if args.dry_run else ""))
        else:
            print("[3/3] Probing sidecar liveness (import + instantiate maven_class)")
            status = run_probe(sidecar_python)
            print(f"      OK - {status.get('detail', 'sidecar ready')}")
    except SetupError as exc:
        print(f"\nsetup_sidecar: {exc}", file=sys.stderr)
        return 1

    print("\nSidecar env is ready. Point Tether at it with:")
    print("  " + _export_line(sidecar_python))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

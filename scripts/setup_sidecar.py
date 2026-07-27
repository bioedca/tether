# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Guided setup for the isolated tMAVEN idealization sidecar (PRD §4.3, §7.4; issue #13).

Turns a fresh checkout into a working ``$TETHER_SIDECAR_PYTHON`` in one command. The
sidecar is the PyQt5 / ``numpy<2`` environment that drives ``tmaven.maven.maven_class``
headlessly (:mod:`tether.idealize.driver`); it is deliberately isolated from Tether's
base stack (PySide6 / current numpy — ADR-0004/0006) and so is *not* part of the base
``conda-lock.yml``.

Two runtime layers and one optional test-tool layer live outside the committed
``sidecar/conda-lock.yml`` and are therefore easy to get wrong by hand — this script
encodes them so a user (or CI, or the M9 cross-OS hand-off check) does the same steps
every time:

1. **tMAVEN itself** — the GPL reference app driven over IPC, pinned by commit and
   installed from git without dependency resolution (never a conda-lock dep).
2. **pytest test tools** — only with ``--with-pytest``, the exact pytest wheel and the
   dependencies absent from the sidecar lock are installed separately from
   ``sidecar/pytest-requirements.txt`` in binary-only hash-checking mode.
3. **setuptools 80.9.0** — tMAVEN imports the legacy ``pkg_resources`` API at runtime
   without declaring it; setuptools deprecated ``pkg_resources`` by 80.9.0 and removed
   it in 82.0.0.  The exact universal wheel and SHA-256 live in
   ``packaging/setuptools-compatibility.txt`` and are force-reinstalled in pip's
   hash-checking mode after tMAVEN has been built.  Force reinstallation makes reruns
   verify the locked wheel instead of trusting an already-installed same-version package.

Before a no-isolation tMAVEN build, the script reads the target interpreter's actual
setuptools distribution version and compares it with the supplied conda lock. A rerun
whose target already contains the runtime-only 80.9.0 overlay may reuse tMAVEN only when
both its PEP 610 Git commit and its ``WHEEL`` generator prove that exact commit was built
by the locked ordinary setuptools. Commit identity alone is insufficient. Every other
existing-interpreter state fails closed with fresh-environment guidance. A permitted
build disables pip's wheel cache, force-reinstalls the pinned source, and rechecks both
provenance records before the runtime compatibility layer is installed.

Flow (each phase is skippable):

* **create** the ``tether-sidecar`` env from ``sidecar/conda-lock.yml`` with a detected
  conda front-end (``conda-lock install``, else ``micromamba``/``mamba create -f``).
  Skipped when ``--python`` targets an already-built interpreter (e.g. in CI, where the
  micromamba action restored the env already).
* **install** the pinned tMAVEN without dependencies under the lock's setuptools (or
  verify and reuse that exact locked-builder result), optionally install the separately
  hash-locked pytest test tools, then install the exact hash-locked setuptools
  compatibility wheel into the sidecar interpreter — the same split recipe
  ``sidecar.yml`` runs.
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
import re
import shutil
import subprocess
import sys
from pathlib import Path

#: Pinned tMAVEN reference (kept in lockstep with ``.github/workflows/sidecar.yml``'s
#: ``TMAVEN_SPEC`` env — ``test_setup_sidecar.py`` binds the two). tMAVEN is the GPL
#: reference app driven over IPC, not a conda-lock dep, so it is git-installed here.
DEFAULT_TMAVEN_SPEC = "git+https://github.com/GonzalezBiophysicsLab/tmaven.git@10f4230"
#: Full commit resolved by the default short Git revision; reuse must match all 40 chars.
DEFAULT_TMAVEN_COMMIT = "10f4230b6d13c6d2ad67b05d801696b4a40eff4a"
#: Default name of the created sidecar env.
DEFAULT_ENV_NAME = "tether-sidecar"
#: Conda front-ends tried, in order, when ``--conda-exe`` is not given.
CONDA_FRONTENDS = ("micromamba", "mamba", "conda")

_REPO_ROOT = Path(__file__).resolve().parents[1]
#: The committed sidecar lock (isolated numpy<2 / PyQt5 stack).
DEFAULT_LOCK = _REPO_ROOT / "sidecar" / "conda-lock.yml"
#: Exact binary/hash source for optional live-suite tools absent from the sidecar lock.
TEST_TOOLS_REQUIREMENTS = _REPO_ROOT / "sidecar" / "pytest-requirements.txt"
#: Sole version/hash source for the temporary runtime ``pkg_resources`` compatibility wheel.
SETUPTOOLS_REQUIREMENTS = _REPO_ROOT / "packaging" / "setuptools-compatibility.txt"
#: The headless runner whose ``--probe`` fast-path we launch to verify liveness.
_SIDECAR_RUNNER = _REPO_ROOT / "src" / "tether" / "idealize" / "_sidecar_runner.py"
#: Must match ``tether.idealize._sidecar_runner.STATUS_PREFIX`` (bound by a contract test).
STATUS_PREFIX = "TETHER_SIDECAR_STATUS "
#: Prefix for the stdlib-only target-interpreter build-state probe below.
_BUILD_STATE_PREFIX = "TETHER_SIDECAR_BUILD_STATE "
#: Run inside the target interpreter before any no-isolation tMAVEN build. pip 26.1.2
#: makes that interpreter responsible for its build dependencies under
#: ``--no-build-isolation``, so both the active setuptools version and any PEP 610
#: tMAVEN VCS provenance are load-bearing inputs.
_BUILD_STATE_PROBE = f"""
import importlib.metadata as md
import json

state = {{
    "setuptools_version": None,
    "tmaven_direct_url": None,
    "tmaven_wheel_generator": None,
}}
try:
    import setuptools

    state["setuptools_version"] = setuptools.__version__
except (ImportError, AttributeError):
    pass
try:
    distribution = md.distribution("tmaven")
    raw = distribution.read_text("direct_url.json")
    state["tmaven_direct_url"] = json.loads(raw) if raw else None
    wheel = distribution.read_text("WHEEL") or ""
    state["tmaven_wheel_generator"] = next(
        (
            line.removeprefix("Generator: ").strip()
            for line in wheel.splitlines()
            if line.startswith("Generator: ")
        ),
        None,
    )
except (md.PackageNotFoundError, json.JSONDecodeError):
    pass
print({_BUILD_STATE_PREFIX!r} + json.dumps(state, sort_keys=True))
"""


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


def build_tmaven_pip_cmd(sidecar_python: str, *, tmaven_spec: str) -> list[str]:
    """Rebuild/install only git-pinned tMAVEN without resolving the locked dependency set."""
    return [
        sidecar_python,
        "-m",
        "pip",
        "install",
        "--force-reinstall",
        "--no-cache-dir",
        "--no-build-isolation",
        "--no-deps",
        tmaven_spec,
    ]


def load_locked_setuptools_version(lock_file: Path) -> str:
    """Return the one setuptools version pinned for every platform in *lock_file*."""
    try:
        lines = lock_file.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SetupError(f"could not read sidecar lock {lock_file}: {exc}") from exc

    versions: set[str] = set()
    for index, line in enumerate(lines):
        if line != "- name: setuptools":
            continue
        if index + 1 >= len(lines) or not lines[index + 1].startswith("  version:"):
            raise SetupError(f"setuptools entry in {lock_file} has no adjacent version")
        version = lines[index + 1].split(":", 1)[1].strip().strip("'\"")
        if version:
            versions.add(version)
    if len(versions) != 1:
        detail = ", ".join(sorted(versions)) if versions else "none"
        raise SetupError(
            f"{lock_file} must pin one setuptools build version across platforms; found {detail}"
        )
    return versions.pop()


def inspect_sidecar_build_state(sidecar_python: str, *, timeout: float | None = 120.0) -> dict:
    """Read target setuptools and installed tMAVEN PEP 610 provenance."""
    try:
        proc = subprocess.run(  # noqa: S603 - sidecar_python is user-selected/resolved
            [sidecar_python, "-c", _BUILD_STATE_PROBE],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SetupError(
            f"target sidecar build-state inspection timed out after {timeout}s"
        ) from exc
    except OSError as exc:
        raise SetupError(f"could not inspect target sidecar build state: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip()
        raise SetupError(
            f"could not inspect target sidecar build state (exit {proc.returncode})"
            + (f": {detail}" if detail else "")
        )
    for line in reversed((proc.stdout or "").splitlines()):
        if not line.startswith(_BUILD_STATE_PREFIX):
            continue
        try:
            state = json.loads(line[len(_BUILD_STATE_PREFIX) :])
        except json.JSONDecodeError as exc:
            raise SetupError("target sidecar returned malformed build-state JSON") from exc
        if isinstance(state, dict):
            return state
    raise SetupError("target sidecar did not return build-state metadata")


def _expected_tmaven_commit(tmaven_spec: str) -> str | None:
    """Resolve the exact full commit permitted for *tmaven_spec* provenance."""
    source, separator, revision = tmaven_spec.rpartition("@")
    if not separator or not source.startswith("git+"):
        return None
    if tmaven_spec == DEFAULT_TMAVEN_SPEC:
        return DEFAULT_TMAVEN_COMMIT
    if re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", revision):
        return revision.lower()
    return None


def _require_immutable_tmaven_commit(tmaven_spec: str) -> str:
    """Return the exact commit for a supported immutable tMAVEN specification."""
    expected_commit = _expected_tmaven_commit(tmaven_spec)
    if expected_commit is None:
        raise SetupError(
            "unsupported --tmaven-spec: use the repository's default pinned short "
            "specification or a git+ URL ending in a full 40- or 64-hex commit; "
            "tags, branches, abbreviated custom commits, and other mutable references "
            "are rejected before the sidecar environment is changed"
        )
    return expected_commit


def _exact_installed_tmaven_commit(
    state: dict,
    *,
    tmaven_spec: str,
    locked_setuptools_version: str,
) -> str | None:
    """Return the commit only when source and locked build-backend provenance match."""
    source, separator, revision = tmaven_spec.rpartition("@")
    expected_commit = _expected_tmaven_commit(tmaven_spec)
    if not separator or expected_commit is None:
        return None
    direct_url = state.get("tmaven_direct_url")
    if not isinstance(direct_url, dict):
        return None
    vcs_info = direct_url.get("vcs_info")
    if not isinstance(vcs_info, dict):
        return None
    commit = vcs_info.get("commit_id")
    requested = vcs_info.get("requested_revision")
    installed_url = direct_url.get("url")
    expected_url = source.removeprefix("git+")
    expected_generator = f"setuptools ({locked_setuptools_version})"
    if (
        vcs_info.get("vcs") != "git"
        or installed_url != expected_url
        or requested != revision
        or state.get("tmaven_wheel_generator") != expected_generator
        or not isinstance(commit, str)
        or re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", commit) is None
        or commit.lower() != expected_commit
    ):
        return None
    return commit.lower()


def _tmaven_install_action(
    state: dict,
    *,
    locked_setuptools_version: str,
    tmaven_spec: str,
) -> tuple[str, str | None]:
    """Choose a locked-toolchain build or exact-provenance reuse; otherwise fail."""
    _require_immutable_tmaven_commit(tmaven_spec)
    installed_setuptools = state.get("setuptools_version")
    if installed_setuptools == locked_setuptools_version:
        return "install", None
    commit = _exact_installed_tmaven_commit(
        state,
        tmaven_spec=tmaven_spec,
        locked_setuptools_version=locked_setuptools_version,
    )
    if commit is not None:
        return "reuse", commit
    installed = str(installed_setuptools) if installed_setuptools else "missing"
    raise SetupError(
        "refusing to build tMAVEN with setuptools "
        f"{installed}: the locked build version is {locked_setuptools_version}, and "
        "--no-build-isolation makes the target interpreter supply the build toolchain. "
        "The installed tMAVEN provenance also does not prove both the requested pinned Git "
        "commit and the expected "
        f"setuptools ({locked_setuptools_version}) WHEEL generator"
        + (
            f" (found {state.get('tmaven_wheel_generator')})"
            if state.get("tmaven_wheel_generator")
            else ""
        )
        + ". Create a genuinely fresh sidecar environment under a new --env-name, "
        "deterministically restore the target interpreter's setuptools files from the lock, "
        "or target an environment containing the exact requested tMAVEN commit built by the "
        "locked setuptools. Merely rerunning against the same named conda environment is not "
        "a restore because pip overlays can leave stale files behind its conda metadata."
    )


def _build_hash_locked_pip_cmd(
    sidecar_python: str,
    *,
    requirements: Path,
) -> list[str]:
    """Build the shared binary-only, no-dependency, hash-locked pip command."""
    return [
        sidecar_python,
        "-m",
        "pip",
        "install",
        "--force-reinstall",
        "--only-binary=:all:",
        "--no-deps",
        "--require-hashes",
        "-r",
        str(requirements),
    ]


def build_test_tools_pip_cmd(
    sidecar_python: str,
    *,
    requirements: Path = TEST_TOOLS_REQUIREMENTS,
) -> list[str]:
    """Force-install only the hash-locked binary test tools from *requirements*."""
    return _build_hash_locked_pip_cmd(sidecar_python, requirements=requirements)


def build_setuptools_pip_cmd(
    sidecar_python: str,
    *,
    requirements: Path = SETUPTOOLS_REQUIREMENTS,
) -> list[str]:
    """Force-install only the hash-locked binary compatibility wheel from *requirements*."""
    return _build_hash_locked_pip_cmd(sidecar_python, requirements=requirements)


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
        help=(
            "use this existing interpreter as the sidecar (skips env creation); "
            "tMAVEN builds only with the lock's setuptools, otherwise exact installed "
            "Git provenance is required"
        ),
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
        help=(
            "immutable tMAVEN pip spec: the repository's default pinned short spec or "
            "git+URL@<full 40/64-hex commit> only (tags and branches are rejected)"
        ),
    )
    parser.add_argument(
        "--with-pytest",
        action="store_true",
        help="also install the hash-locked pytest test tools (for the live suite)",
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help=(
            "skip the tMAVEN, optional pytest test-tool, and compatibility-wheel installs; "
            "use only with an already-populated sidecar environment "
            "(probe still runs unless --no-probe)"
        ),
    )
    parser.add_argument("--no-probe", action="store_true", help="skip the liveness probe")
    parser.add_argument(
        "--dry-run", action="store_true", help="print the commands without running them"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        # Reject mutable/custom abbreviated VCS references before env creation, target
        # inspection, or any other subprocess can change the sidecar.
        expected_tmaven_commit = _require_immutable_tmaven_commit(args.tmaven_spec)

        # 1) Resolve the sidecar interpreter (create the env unless --python was given).
        if args.python:
            sidecar_python = args.python
            if not args.dry_run and not Path(sidecar_python).exists():
                raise SetupError(f"--python interpreter does not exist: {sidecar_python}")
            print(f"[1/4] Using existing sidecar interpreter: {sidecar_python}")
        else:
            if not args.lock_file.exists() and not args.dry_run:
                raise SetupError(f"conda-lock file not found: {args.lock_file}")
            frontend = detect_conda_frontend(args.conda_exe)
            print(f"[1/4] Creating env {args.env_name!r} from {args.lock_file} (via {frontend})")
            _run(
                build_env_create_cmd(frontend, args.env_name, args.lock_file), dry_run=args.dry_run
            )
            sidecar_python = (
                f"<{args.env_name}>/python"
                if args.dry_run
                else resolve_env_python(frontend, args.env_name)
            )

        # 2–3) Install tMAVEN without dependencies, optional test tools separately,
        # then the runtime-only hashed compatibility wheel.
        if args.skip_install:
            print(
                "[2/4] Skipping tMAVEN + optional pytest test tools + "
                "compatibility-wheel installs (--skip-install)"
            )
        else:
            print("[2/4] Inspecting target build state before the tMAVEN install")
            if args.dry_run:
                print(
                    "  $ "
                    f"{sidecar_python} -c <inspect locked setuptools + tMAVEN PEP 610 provenance>"
                )
                print(
                    "      Dry-run shows the fresh locked-build path; unsafe existing states "
                    "fail instead of building tMAVEN"
                )
                tmaven_action, installed_commit = "install", None
            else:
                locked_setuptools_version = load_locked_setuptools_version(args.lock_file)
                state = inspect_sidecar_build_state(sidecar_python)
                tmaven_action, installed_commit = _tmaven_install_action(
                    state,
                    locked_setuptools_version=locked_setuptools_version,
                    tmaven_spec=args.tmaven_spec,
                )
            if tmaven_action == "install":
                print("      Installing pinned tMAVEN with the locked setuptools build toolchain")
                _run(
                    build_tmaven_pip_cmd(sidecar_python, tmaven_spec=args.tmaven_spec),
                    dry_run=args.dry_run,
                )
                if not args.dry_run:
                    rebuilt_state = inspect_sidecar_build_state(sidecar_python)
                    rebuilt_commit = _exact_installed_tmaven_commit(
                        rebuilt_state,
                        tmaven_spec=args.tmaven_spec,
                        locked_setuptools_version=locked_setuptools_version,
                    )
                    if rebuilt_commit is None:
                        direct_url = rebuilt_state.get("tmaven_direct_url")
                        vcs_info = (
                            direct_url.get("vcs_info") if isinstance(direct_url, dict) else None
                        )
                        found_commit = (
                            vcs_info.get("commit_id") if isinstance(vcs_info, dict) else None
                        )
                        found_generator = rebuilt_state.get("tmaven_wheel_generator")
                        raise SetupError(
                            "rebuilt tMAVEN failed provenance verification: expected "
                            f"commit {expected_tmaven_commit} and "
                            "WHEEL Generator: "
                            f"setuptools ({locked_setuptools_version}); "
                            f"found commit {found_commit or 'missing'} and "
                            f"{found_generator or 'missing generator'}. Refusing to install "
                            "the runtime compatibility wheel."
                        )
                    print(
                        "      Verified rebuilt tMAVEN commit "
                        f"{rebuilt_commit} and locked WHEEL generator"
                    )
            else:
                print(f"      Reusing exact installed tMAVEN commit {installed_commit}")
            if args.with_pytest:
                print("      Installing hash-locked pytest test tools")
                _run(
                    build_test_tools_pip_cmd(sidecar_python),
                    dry_run=args.dry_run,
                )
            print("[3/4] Installing the hash-locked setuptools compatibility wheel")
            _run(
                build_setuptools_pip_cmd(sidecar_python),
                dry_run=args.dry_run,
            )

        # 4) Verify the env can build the tMAVEN driver (liveness).
        if args.no_probe or args.dry_run:
            print("[4/4] Skipping liveness probe" + (" (--dry-run)" if args.dry_run else ""))
        else:
            print("[4/4] Probing sidecar liveness (import + instantiate maven_class)")
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

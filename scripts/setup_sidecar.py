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
   Before liveness, the imported ``pkg_resources`` content and lexical package/spec paths
   must match the installed wheel's raw ``RECORD`` after safe bytecode-cache cleanup.

Before a no-isolation tMAVEN build, the script verifies that the target interpreter's
actual setuptools distribution has the version, one platform artifact SHA-256, and
installed file digests from the supplied lock, and that the imported module is one of
those files. A matching version or shadow import alone cannot authorize a build.
A rerun whose target already contains the runtime-only 80.9.0 overlay may reuse tMAVEN
only when both its PEP 610 Git commit and its ``WHEEL`` generator prove that exact commit
was built by the locked ordinary setuptools, every Python row in the raw wheel ``RECORD``
still exists with matching bytes, and the imported tMAVEN initializer, absolute lexical
package path, and actual ``tmaven.maven`` spec selected by the runner exactly match their
raw ``RECORD`` paths. Before importing tMAVEN, the probe must also safely discard every
matching in-prefix bytecode cache so timestamp-valid unrecorded ``.pyc`` files cannot
override the verified sources. The selected module's resolved bytes must be hash-verified.
Commit identity and build metadata alone are insufficient. Every other existing-interpreter
state fails closed with fresh-environment guidance. A permitted build disables pip's wheel
cache, force-reinstalls the pinned source, and rechecks all provenance before the runtime
compatibility layer is installed.

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
#: Prefix for post-overlay ``pkg_resources`` provenance from the target interpreter.
_PKG_RESOURCES_PREFIX = "TETHER_SIDECAR_PKG_RESOURCES_STATE "
#: Run inside the target interpreter before any no-isolation tMAVEN build. pip 26.1.2
#: makes that interpreter responsible for its build dependencies under
#: ``--no-build-isolation``, so both the active setuptools version and any PEP 610
#: tMAVEN VCS provenance are load-bearing inputs.
_BUILD_STATE_PROBE = f"""
import base64
import csv
import importlib.metadata as md
import importlib.util
import hashlib
import io
import json
import pathlib
import sys

state = {{
    "setuptools_version": None,
    "setuptools_conda_record_sha256": None,
    "setuptools_conda_files_verified": False,
    "setuptools_import_origin_verified": False,
    "tmaven_direct_url": None,
    "tmaven_wheel_generator": None,
    "tmaven_python_files_verified": False,
    "tmaven_import_origin_verified": False,
    "tmaven_package_path_verified": False,
    "tmaven_maven_origin_verified": False,
    "tmaven_bytecode_cache_safe": False,
}}
setuptools_origin = None
try:
    import setuptools

    state["setuptools_version"] = setuptools.__version__
    setuptools_origin = pathlib.Path(setuptools.__file__).resolve(strict=True)
except (ImportError, AttributeError, OSError, TypeError):
    pass
prefix = pathlib.Path(sys.prefix).resolve()
matching_records = []
for record_path in (prefix / "conda-meta").glob("setuptools-*.json"):
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        continue
    if (
        record.get("name") == "setuptools"
        and record.get("version") == state["setuptools_version"]
    ):
        matching_records.append(record)
if len(matching_records) == 1:
    record = matching_records[0]
    state["setuptools_conda_record_sha256"] = record.get("sha256")
    paths = record.get("paths_data", {{}}).get("paths", [])
    verified = isinstance(paths, list) and bool(paths)
    verified_files = set()
    hashed_files = 0
    for entry in paths if isinstance(paths, list) else []:
        expected = entry.get("sha256_in_prefix") or entry.get("sha256")
        if expected is None:
            if entry.get("path_type") == "hardlink":
                verified = False
            continue
        relative_text = entry.get("_path")
        if not isinstance(relative_text, str):
            verified = False
            continue
        relative = pathlib.PurePosixPath(relative_text)
        if relative.is_absolute() or ".." in relative.parts:
            verified = False
            continue
        candidate = prefix.joinpath(*relative.parts)
        try:
            resolved_candidate = candidate.resolve(strict=True)
            resolved_candidate.relative_to(prefix)
            digest = hashlib.sha256(resolved_candidate.read_bytes()).hexdigest()
        except (OSError, ValueError):
            verified = False
            continue
        hashed_files += 1
        if digest != expected:
            verified = False
        else:
            verified_files.add(resolved_candidate)
    state["setuptools_conda_files_verified"] = verified and hashed_files > 0
    state["setuptools_import_origin_verified"] = (
        state["setuptools_conda_files_verified"]
        and setuptools_origin in verified_files
    )
tmaven_bytecode_cache_safe = False
try:
    cache_distribution = md.distribution("tmaven")
    cache_record_text = cache_distribution.read_text("RECORD") or ""
    try:
        cache_python_rows = [
            row
            for row in csv.reader(io.StringIO(cache_record_text))
            if row and pathlib.PurePosixPath(row[0]).suffix == ".py"
        ]
    except csv.Error:
        cache_python_rows = []
    tmaven_bytecode_cache_safe = bool(cache_python_rows) and sys.pycache_prefix is None

    def matching_bytecode(cache_directory, source_stem):
        return [
            candidate
            for candidate in list(cache_directory.iterdir())
            if candidate.suffix == ".pyc"
            and candidate.name.startswith(source_stem + ".")
        ]

    for row in cache_python_rows:
        if not row:
            tmaven_bytecode_cache_safe = False
            continue
        relative = pathlib.PurePosixPath(row[0])
        if relative.is_absolute() or ".." in relative.parts:
            tmaven_bytecode_cache_safe = False
            continue
        try:
            installed_lexical_file = pathlib.Path(
                cache_distribution.locate_file(row[0])
            ).absolute()
            installed_lexical_file.relative_to(prefix)
            installed_lexical_file.resolve(strict=True).relative_to(prefix)
            cache_dir = installed_lexical_file.parent / "__pycache__"
            if cache_dir.is_symlink():
                raise ValueError("tMAVEN bytecode cache directory is a symlink")
            if cache_dir.exists():
                cache_dir.resolve(strict=True).relative_to(prefix)
                cached_files = matching_bytecode(
                    cache_dir, installed_lexical_file.stem
                )
                for cached_file in cached_files:
                    cached_file.absolute().relative_to(prefix)
                    if cached_file.is_symlink():
                        raise ValueError("tMAVEN bytecode cache file is a symlink")
                    cached_file.resolve(strict=True).relative_to(prefix)
                    cached_file.unlink()
            legacy_cache = installed_lexical_file.with_suffix(".pyc")
            if legacy_cache.is_symlink():
                raise ValueError("legacy tMAVEN bytecode cache is a symlink")
            if legacy_cache.exists():
                legacy_cache.absolute().relative_to(prefix)
                legacy_cache.resolve(strict=True).relative_to(prefix)
                legacy_cache.unlink()
            remaining_cached_files = (
                matching_bytecode(cache_dir, installed_lexical_file.stem)
                if cache_dir.exists()
                else []
            )
            if remaining_cached_files or legacy_cache.exists():
                tmaven_bytecode_cache_safe = False
        except (OSError, ValueError):
            tmaven_bytecode_cache_safe = False
    state["tmaven_bytecode_cache_safe"] = tmaven_bytecode_cache_safe
except md.PackageNotFoundError:
    pass
tmaven_file = None
tmaven_spec_origin = None
tmaven_package_paths = ()
tmaven_maven_origin = None
tmaven_maven_resolved_origin = None
tmaven_maven_spec_shape_verified = False
if state["tmaven_bytecode_cache_safe"]:
    try:
        import tmaven

        tmaven_file = pathlib.Path(tmaven.__file__).absolute()
        tmaven_spec_origin = pathlib.Path(tmaven.__spec__.origin).absolute()
        tmaven_package_paths = tuple(pathlib.Path(entry).absolute() for entry in tmaven.__path__)
        tmaven_maven_spec = importlib.util.find_spec("tmaven.maven")
        if tmaven_maven_spec is not None and tmaven_maven_spec.origin is not None:
            tmaven_maven_spec_shape_verified = (
                tmaven_maven_spec.name == "tmaven.maven"
                and tmaven_maven_spec.has_location is True
                and tmaven_maven_spec.submodule_search_locations is None
            )
            tmaven_maven_origin = pathlib.Path(tmaven_maven_spec.origin).absolute()
            tmaven_maven_resolved_origin = tmaven_maven_origin.resolve(strict=True)
    except (ImportError, AttributeError, OSError, TypeError):
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
    record_text = distribution.read_text("RECORD") or ""
    try:
        python_rows = [
            row
            for row in csv.reader(io.StringIO(record_text))
            if row and pathlib.PurePosixPath(row[0]).suffix == ".py"
        ]
    except csv.Error:
        python_rows = []
    python_files_verified = bool(python_rows)
    verified_python_files = set()
    verified_tmaven_initializers = []
    verified_tmaven_mavens = []
    for row in python_rows:
        if len(row) < 2:
            python_files_verified = False
            continue
        relative_text, retained_hash = row[0], row[1]
        algorithm, separator, expected_digest = retained_hash.partition("=")
        if algorithm != "sha256" or not separator or not expected_digest:
            python_files_verified = False
            continue
        relative = pathlib.PurePosixPath(relative_text)
        if relative.is_absolute() or ".." in relative.parts:
            python_files_verified = False
            continue
        try:
            installed_lexical_file = pathlib.Path(
                distribution.locate_file(relative_text)
            ).absolute()
            installed_lexical_file.relative_to(prefix)
            installed_resolved_file = installed_lexical_file.resolve(strict=True)
            installed_resolved_file.relative_to(prefix)
            digest = base64.urlsafe_b64encode(
                hashlib.sha256(installed_resolved_file.read_bytes()).digest()
            ).rstrip(b"=").decode("ascii")
        except (OSError, ValueError):
            python_files_verified = False
            continue
        if digest != expected_digest:
            python_files_verified = False
        else:
            verified_python_files.add(installed_resolved_file)
            if relative == pathlib.PurePosixPath("tmaven/__init__.py"):
                verified_tmaven_initializers.append(installed_lexical_file)
            elif relative == pathlib.PurePosixPath("tmaven/maven.py"):
                verified_tmaven_mavens.append(installed_lexical_file)
    state["tmaven_python_files_verified"] = python_files_verified
    state["tmaven_import_origin_verified"] = (
        python_files_verified
        and len(verified_tmaven_initializers) == 1
        and tmaven_file == verified_tmaven_initializers[0]
        and tmaven_spec_origin == verified_tmaven_initializers[0]
    )
    state["tmaven_package_path_verified"] = (
        state["tmaven_import_origin_verified"]
        and len(tmaven_package_paths) == 1
        and tmaven_package_paths[0] == verified_tmaven_initializers[0].parent
    )
    state["tmaven_maven_origin_verified"] = (
        python_files_verified
        and tmaven_maven_spec_shape_verified
        and len(verified_tmaven_mavens) == 1
        and tmaven_maven_origin == verified_tmaven_mavens[0]
        and tmaven_maven_resolved_origin in verified_python_files
    )
except (md.PackageNotFoundError, json.JSONDecodeError):
    pass
print({_BUILD_STATE_PREFIX!r} + json.dumps(state, sort_keys=True))
"""

_PKG_RESOURCES_PROBE = f"""
import base64
import csv
import hashlib
import importlib.metadata as md
import importlib.util
import io
import json
import pathlib
import sys

state = {{
    "setuptools_distribution_version": None,
    "pkg_resources_python_files_verified": False,
    "pkg_resources_import_origin_verified": False,
    "pkg_resources_package_path_verified": False,
    "pkg_resources_bytecode_cache_safe": False,
}}
prefix = pathlib.Path(sys.prefix).resolve()
try:
    distribution = md.distribution("setuptools")
    state["setuptools_distribution_version"] = distribution.version
    record_text = distribution.read_text("RECORD") or ""
    try:
        python_rows = [
            row
            for row in csv.reader(io.StringIO(record_text))
            if row
            and pathlib.PurePosixPath(row[0]).suffix == ".py"
            and pathlib.PurePosixPath(row[0]).parts
            and pathlib.PurePosixPath(row[0]).parts[0] == "pkg_resources"
        ]
    except csv.Error:
        python_rows = []

    def matching_bytecode(cache_directory, source_stem):
        return [
            candidate
            for candidate in list(cache_directory.iterdir())
            if candidate.suffix == ".pyc"
            and candidate.name.startswith(source_stem + ".")
        ]

    bytecode_cache_safe = bool(python_rows) and sys.pycache_prefix is None
    for row in python_rows:
        relative = pathlib.PurePosixPath(row[0])
        if relative.is_absolute() or ".." in relative.parts:
            bytecode_cache_safe = False
            continue
        try:
            installed_lexical_file = pathlib.Path(
                distribution.locate_file(row[0])
            ).absolute()
            installed_lexical_file.relative_to(prefix)
            installed_lexical_file.resolve(strict=True).relative_to(prefix)
            cache_dir = installed_lexical_file.parent / "__pycache__"
            if cache_dir.is_symlink():
                raise ValueError("pkg_resources bytecode cache directory is a symlink")
            if cache_dir.exists():
                cache_dir.resolve(strict=True).relative_to(prefix)
                for cached_file in matching_bytecode(
                    cache_dir, installed_lexical_file.stem
                ):
                    cached_file.absolute().relative_to(prefix)
                    if cached_file.is_symlink():
                        raise ValueError("pkg_resources bytecode cache file is a symlink")
                    cached_file.resolve(strict=True).relative_to(prefix)
                    cached_file.unlink()
            legacy_cache = installed_lexical_file.with_suffix(".pyc")
            if legacy_cache.is_symlink():
                raise ValueError("legacy pkg_resources bytecode cache is a symlink")
            if legacy_cache.exists():
                legacy_cache.absolute().relative_to(prefix)
                legacy_cache.resolve(strict=True).relative_to(prefix)
                legacy_cache.unlink()
            remaining_cached_files = (
                matching_bytecode(cache_dir, installed_lexical_file.stem)
                if cache_dir.exists()
                else []
            )
            if remaining_cached_files or legacy_cache.exists():
                bytecode_cache_safe = False
        except (OSError, ValueError):
            bytecode_cache_safe = False
    state["pkg_resources_bytecode_cache_safe"] = bytecode_cache_safe

    python_files_verified = bool(python_rows)
    verified_python_files = set()
    verified_initializers = []
    for row in python_rows:
        if len(row) < 2:
            python_files_verified = False
            continue
        relative_text, retained_hash = row[0], row[1]
        algorithm, separator, expected_digest = retained_hash.partition("=")
        if algorithm != "sha256" or not separator or not expected_digest:
            python_files_verified = False
            continue
        relative = pathlib.PurePosixPath(relative_text)
        if relative.is_absolute() or ".." in relative.parts:
            python_files_verified = False
            continue
        try:
            installed_lexical_file = pathlib.Path(
                distribution.locate_file(relative_text)
            ).absolute()
            installed_lexical_file.relative_to(prefix)
            installed_resolved_file = installed_lexical_file.resolve(strict=True)
            installed_resolved_file.relative_to(prefix)
            digest = base64.urlsafe_b64encode(
                hashlib.sha256(installed_resolved_file.read_bytes()).digest()
            ).rstrip(b"=").decode("ascii")
        except (OSError, ValueError):
            python_files_verified = False
            continue
        if digest != expected_digest:
            python_files_verified = False
        else:
            verified_python_files.add(installed_resolved_file)
            if relative == pathlib.PurePosixPath("pkg_resources/__init__.py"):
                verified_initializers.append(installed_lexical_file)
    state["pkg_resources_python_files_verified"] = python_files_verified

    selected_spec_origin = None
    selected_spec_paths = ()
    selected_resolved_origin = None
    selected_spec_shape_verified = False
    if bytecode_cache_safe and python_files_verified:
        try:
            selected_spec = importlib.util.find_spec("pkg_resources")
            selected_spec_shape_verified = (
                selected_spec is not None
                and selected_spec.name == "pkg_resources"
                and selected_spec.has_location is True
                and selected_spec.origin is not None
                and selected_spec.submodule_search_locations is not None
            )
            if selected_spec_shape_verified:
                selected_spec_origin = pathlib.Path(selected_spec.origin).absolute()
                selected_spec_paths = tuple(
                    pathlib.Path(entry).absolute()
                    for entry in selected_spec.submodule_search_locations
                )
                selected_resolved_origin = selected_spec_origin.resolve(strict=True)
        except (AttributeError, OSError, TypeError):
            pass
    selected_spec_verified = (
        python_files_verified
        and selected_spec_shape_verified
        and len(verified_initializers) == 1
        and selected_spec_origin == verified_initializers[0]
        and len(selected_spec_paths) == 1
        and selected_spec_paths[0] == verified_initializers[0].parent
        and selected_resolved_origin in verified_python_files
    )
    if selected_spec_verified:
        try:
            import pkg_resources

            pkg_resources_file = pathlib.Path(pkg_resources.__file__).absolute()
            pkg_resources_spec_origin = pathlib.Path(
                pkg_resources.__spec__.origin
            ).absolute()
            pkg_resources_paths = tuple(
                pathlib.Path(entry).absolute() for entry in pkg_resources.__path__
            )
            state["pkg_resources_import_origin_verified"] = (
                pkg_resources_file == verified_initializers[0]
                and pkg_resources_spec_origin == verified_initializers[0]
                and pkg_resources_file.resolve(strict=True) in verified_python_files
            )
            state["pkg_resources_package_path_verified"] = (
                state["pkg_resources_import_origin_verified"]
                and len(pkg_resources_paths) == 1
                and pkg_resources_paths[0] == verified_initializers[0].parent
            )
        except (ImportError, AttributeError, OSError, TypeError):
            pass
except md.PackageNotFoundError:
    pass
print({_PKG_RESOURCES_PREFIX!r} + json.dumps(state, sort_keys=True))
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


def load_locked_setuptools_artifact_sha256s(lock_file: Path) -> frozenset[str]:
    """Return every platform-specific setuptools artifact SHA-256 in *lock_file*."""
    try:
        lines = lock_file.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SetupError(f"could not read sidecar lock {lock_file}: {exc}") from exc

    hashes: set[str] = set()
    for index, line in enumerate(lines):
        if line != "- name: setuptools":
            continue
        for candidate in lines[index + 1 :]:
            if candidate.startswith("- name: "):
                break
            if candidate.startswith("    sha256:"):
                digest = candidate.split(":", 1)[1].strip().strip("'\"").lower()
                if re.fullmatch(r"[0-9a-f]{64}", digest):
                    hashes.add(digest)
                break
    if not hashes:
        detail = ", ".join(sorted(hashes)) if hashes else "none"
        raise SetupError(
            f"{lock_file} must pin at least one setuptools conda artifact SHA-256; found {detail}"
        )
    return frozenset(hashes)


def load_locked_setuptools_artifact_sha256(lock_file: Path) -> str:
    """Return one shared setuptools artifact hash, for compatibility callers."""
    hashes = load_locked_setuptools_artifact_sha256s(lock_file)
    if len(hashes) != 1:
        raise SetupError(f"{lock_file} pins platform-specific setuptools artifact SHA-256 values")
    return next(iter(hashes))


def load_runtime_setuptools_version(
    requirements: Path = SETUPTOOLS_REQUIREMENTS,
) -> str:
    """Return the sole exact setuptools pin in the compatibility requirements."""
    try:
        lines = requirements.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SetupError(
            f"could not read runtime compatibility lock {requirements}: {exc}"
        ) from exc
    versions = []
    for raw in lines:
        active = raw.split("#", 1)[0].strip()
        match = re.fullmatch(r"setuptools==([A-Za-z0-9_.+-]+)\s*\\?", active)
        if match:
            versions.append(match.group(1))
    if len(versions) != 1:
        raise SetupError(
            f"{requirements} must contain exactly one active exact setuptools pin; "
            f"found {len(versions)}"
        )
    return versions[0]


def inspect_sidecar_build_state(sidecar_python: str, *, timeout: float | None = 120.0) -> dict:
    """Read target setuptools conda provenance and installed tMAVEN provenance."""
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


def inspect_runtime_pkg_resources(
    sidecar_python: str,
    *,
    timeout: float | None = 120.0,
) -> dict:
    """Verify post-overlay ``pkg_resources`` content and actual import provenance."""
    try:
        proc = subprocess.run(  # noqa: S603 - sidecar_python is user-selected/resolved
            [sidecar_python, "-c", _PKG_RESOURCES_PROBE],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SetupError(
            f"target pkg_resources provenance inspection timed out after {timeout}s"
        ) from exc
    except OSError as exc:
        raise SetupError(f"could not inspect target pkg_resources provenance: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip()
        raise SetupError(
            f"could not inspect target pkg_resources provenance (exit {proc.returncode})"
            + (f": {detail}" if detail else "")
        )
    for line in reversed((proc.stdout or "").splitlines()):
        if not line.startswith(_PKG_RESOURCES_PREFIX):
            continue
        try:
            state = json.loads(line[len(_PKG_RESOURCES_PREFIX) :])
        except json.JSONDecodeError as exc:
            raise SetupError("target returned malformed pkg_resources provenance JSON") from exc
        if isinstance(state, dict):
            return state
    raise SetupError("target did not return pkg_resources provenance metadata")


def require_runtime_pkg_resources(state: dict, *, expected_version: str) -> None:
    """Fail unless the imported runtime package is the exact verified wheel content."""
    if (
        state.get("setuptools_distribution_version") == expected_version
        and state.get("pkg_resources_python_files_verified") is True
        and state.get("pkg_resources_import_origin_verified") is True
        and state.get("pkg_resources_package_path_verified") is True
        and state.get("pkg_resources_bytecode_cache_safe") is True
    ):
        return
    raise SetupError(
        "pkg_resources runtime provenance verification failed after the hash-locked "
        f"setuptools overlay: expected setuptools {expected_version}, complete matching "
        "raw RECORD Python content, exact imported initializer/spec/package path, and "
        "safely discarded in-prefix bytecode caches; found version "
        f"{state.get('setuptools_distribution_version') or 'missing'}, Python integrity "
        f"{state.get('pkg_resources_python_files_verified')}, import-origin integrity "
        f"{state.get('pkg_resources_import_origin_verified')}, package-path integrity "
        f"{state.get('pkg_resources_package_path_verified')}, and bytecode-cache safety "
        f"{state.get('pkg_resources_bytecode_cache_safe')}. Refusing to run tMAVEN."
    )


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
    """Return the commit only when source, build, content, and import provenance match."""
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
        or state.get("tmaven_python_files_verified") is not True
        or state.get("tmaven_import_origin_verified") is not True
        or state.get("tmaven_package_path_verified") is not True
        or state.get("tmaven_maven_origin_verified") is not True
        or state.get("tmaven_bytecode_cache_safe") is not True
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
    locked_setuptools_artifact_sha256s: frozenset[str],
    tmaven_spec: str,
) -> tuple[str, str | None]:
    """Choose a locked-toolchain build or exact-provenance reuse; otherwise fail."""
    _require_immutable_tmaven_commit(tmaven_spec)
    installed_setuptools = state.get("setuptools_version")
    if (
        installed_setuptools == locked_setuptools_version
        and state.get("setuptools_conda_record_sha256") in locked_setuptools_artifact_sha256s
        and state.get("setuptools_conda_files_verified") is True
        and state.get("setuptools_import_origin_verified") is True
    ):
        return "install", None
    commit = _exact_installed_tmaven_commit(
        state,
        tmaven_spec=tmaven_spec,
        locked_setuptools_version=locked_setuptools_version,
    )
    if commit is not None:
        return "reuse", commit
    installed = str(installed_setuptools) if installed_setuptools else "missing"
    expected_hashes = ", ".join(sorted(locked_setuptools_artifact_sha256s))
    raise SetupError(
        "refusing to build tMAVEN with setuptools "
        f"{installed}: the locked build version is {locked_setuptools_version}, but "
        "the target does not prove both the exact locked conda artifact files and the "
        "imported setuptools origin "
        f"(expected one of package SHA-256 values {expected_hashes}; found "
        f"{state.get('setuptools_conda_record_sha256') or 'no matching conda record'}). "
        "--no-build-isolation makes the target interpreter supply the build toolchain. "
        "The installed tMAVEN provenance also does not prove the requested pinned Git "
        "commit, the expected "
        f"setuptools ({locked_setuptools_version}) WHEEL generator"
        + (
            f" (found {state.get('tmaven_wheel_generator')})"
            if state.get("tmaven_wheel_generator")
            else ""
        )
        + ", the installed tMAVEN Python files against their complete wheel RECORD, "
        "the imported package path, the selected tmaven.maven origin, and a safely "
        "discarded in-prefix tMAVEN bytecode cache"
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
            "tMAVEN builds only with a matching platform artifact from the lock and "
            "verified setuptools import origin; reuse also requires exact installed Git, "
            "build, complete Python RECORD, and verified tMAVEN package-path/module-origin "
            "provenance after safely discarding in-prefix bytecode caches"
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
            "(pkg_resources provenance verification still runs; liveness probe still "
            "runs unless --no-probe)"
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
                locked_setuptools_artifact_sha256s = load_locked_setuptools_artifact_sha256s(
                    args.lock_file
                )
                state = inspect_sidecar_build_state(sidecar_python)
                tmaven_action, installed_commit = _tmaven_install_action(
                    state,
                    locked_setuptools_version=locked_setuptools_version,
                    locked_setuptools_artifact_sha256s=locked_setuptools_artifact_sha256s,
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
                        found_python_integrity = rebuilt_state.get("tmaven_python_files_verified")
                        found_import_origin = rebuilt_state.get("tmaven_import_origin_verified")
                        found_package_path = rebuilt_state.get("tmaven_package_path_verified")
                        found_maven_origin = rebuilt_state.get("tmaven_maven_origin_verified")
                        found_bytecode_cache = rebuilt_state.get("tmaven_bytecode_cache_safe")
                        raise SetupError(
                            "rebuilt tMAVEN failed provenance verification: expected "
                            f"commit {expected_tmaven_commit} and "
                            "WHEEL Generator: "
                            f"setuptools ({locked_setuptools_version}) with a complete "
                            "verified Python RECORD, package path, tmaven.maven origin, "
                            "and safely discarded bytecode cache; "
                            f"found commit {found_commit or 'missing'} and "
                            f"{found_generator or 'missing generator'} with Python-file "
                            f"integrity {found_python_integrity} and imported-origin "
                            f"integrity {found_import_origin}, package-path integrity "
                            f"{found_package_path}, and tmaven.maven-origin integrity "
                            f"{found_maven_origin}, and bytecode-cache safety "
                            f"{found_bytecode_cache}. Refusing to install "
                            "the runtime compatibility wheel."
                        )
                    print(
                        "      Verified rebuilt tMAVEN commit "
                        f"{rebuilt_commit}, locked WHEEL generator, complete Python RECORD, "
                        "package path, tmaven.maven origin, and discarded bytecode caches"
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

        if args.dry_run:
            print(
                "  $ "
                f"{sidecar_python} -c <verify hash-locked pkg_resources content + import origin>"
            )
        else:
            runtime_setuptools_version = load_runtime_setuptools_version()
            runtime_state = inspect_runtime_pkg_resources(sidecar_python)
            require_runtime_pkg_resources(
                runtime_state,
                expected_version=runtime_setuptools_version,
            )
            print(
                "      Verified pkg_resources from setuptools "
                f"{runtime_setuptools_version}: complete Python RECORD, exact import "
                "origin/package path, and discarded bytecode caches"
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

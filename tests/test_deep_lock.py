# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Guard the isolated ``deep/`` torch conda stack (ADR-0047 "Option A"; PRD §4.1, §9 M8).

``conda-lock-verify`` proves the committed ``deep/conda-lock.yml`` stays in sync with
``deep/environment.yml`` — but it does *not* assert the load-bearing **CPU-only** invariant.
A future edit that let the solver pull a CUDA variant would still pass ``conda-lock-verify``
yet balloon the optional add-on to ~2 GB and pull GPU-only artifacts into the offline bundle
(conda-forge tips-and-tricks: a CUDA build is ~2 GB vs ~200 MB for CPU). These pure,
dependency-free text checks lock that invariant so it cannot silently regress.

The M8 acceptance clause is "a deep classifier trains on the shared label store **and is
optional (CPU base app unaffected)**"; a CPU-only, cross-platform lock is what keeps the add-on
optional and the base install lean.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEEP_DIR = _REPO_ROOT / "deep"
_LOCK = _DEEP_DIR / "conda-lock.yml"
_ENV = _DEEP_DIR / "environment.yml"

# The four platforms the base + sidecar stacks target; the deep stack mirrors them.
_EXPECTED_PLATFORMS = ("linux-64", "osx-64", "osx-arm64", "win-64")

# Tokens that betray a CUDA / GPU build slipping into the CPU-only lock.
_CUDA_TOKENS = ("cudatoolkit", "cudnn", "nccl", "cuda-", "-cuda", "pytorch-gpu")


@pytest.fixture(scope="module")
def lock_text() -> str:
    assert _LOCK.is_file(), f"missing deep lock: {_LOCK}"
    return _LOCK.read_text(encoding="utf-8")


def test_deep_env_and_lock_exist() -> None:
    assert _ENV.is_file(), f"missing deep environment.yml: {_ENV}"
    assert _LOCK.is_file(), f"missing deep conda-lock.yml: {_LOCK}"


def test_env_pins_cpu_pytorch_and_bounds_numpy() -> None:
    env = _ENV.read_text(encoding="utf-8")
    # The CPU metapackage (never the CUDA-capable `pytorch` alone) is the CPU-only pin.
    assert re.search(r"^\s*-\s*pytorch-cpu\b", env, re.MULTILINE), (
        "deep/environment.yml must pin the CPU-only `pytorch-cpu` metapackage"
    )
    # numpy stays inside the base window so the shared substrate behaves identically.
    assert re.search(r"^\s*-\s*numpy>=1\.26,<2\.2\b", env, re.MULTILINE), (
        "deep numpy must be bounded to the base <2.2 window"
    )


def test_lock_covers_all_platforms(lock_text: str) -> None:
    for platform in _EXPECTED_PLATFORMS:
        assert f"platform: {platform}" in lock_text, f"deep lock missing platform {platform}"


def test_lock_ships_pytorch(lock_text: str) -> None:
    assert "name: pytorch-cpu" in lock_text, "deep lock must resolve the pytorch-cpu metapackage"
    assert "name: pytorch\n" in lock_text, "deep lock must resolve the pytorch runtime"


def test_every_pytorch_build_is_cpu(lock_text: str) -> None:
    """Every resolved pytorch/pytorch-cpu artifact must be a CPU build (`cpu_*`), never CUDA."""
    # Match the conda artifact filenames, e.g. pytorch-2.12.1-cpu_mkl_py312_h..._100.conda
    builds = re.findall(r"/(pytorch(?:-cpu)?)-[^/\-]+-([^/]+?)\.conda", lock_text)
    assert builds, "no pytorch artifacts found in the deep lock"
    non_cpu = [(name, build) for name, build in builds if not build.startswith("cpu")]
    assert not non_cpu, f"non-CPU pytorch build(s) leaked into the deep lock: {non_cpu}"


def test_no_cuda_artifacts(lock_text: str) -> None:
    lowered = lock_text.lower()
    hits = [token for token in _CUDA_TOKENS if token in lowered]
    assert not hits, f"CUDA/GPU artifacts leaked into the CPU-only deep lock: {hits}"

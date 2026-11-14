# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The required ``conda-lock-verify`` job brings its own pinned conda backend (#888).

The job used to put the runner image's preinstalled Miniconda (``$CONDA``) on ``PATH`` under
``set -u``. The ``ubuntu-26.04`` image that ``ubuntu-latest`` now resolves to ships no
Miniconda and leaves ``CONDA`` empty (runner-images ``Ubuntu2604-Readme.md``, release
``ubuntu26/20261111.196``), so the step died with ``CONDA: unbound variable`` before it checked
anything. These tests hold the fix: the job installs the backend itself, with the same pinned
``setup-micromamba`` call every other site uses (held equal by
``tests/test_marker_contract.py``), and hands that binary to every ``conda-lock lock`` call, so
the job no longer depends on what an image happens to preinstall and never bootstraps an
unpinned backend over the network.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml  # provided by the base conda-lock (a mkdocs dependency)

CI = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
SETUP_MICROMAMBA = "mamba-org/setup-micromamba@"
#: ``$CONDA`` / ``${CONDA}``, but not ``$CONDA_EXE`` or another longer name.
IMAGE_CONDA = re.compile(r"\$\{?CONDA(?![A-Za-z0-9_])")
VERIFY_STEP = "Verify base + sidecar + deep locks match their environment.yml"


def _steps() -> list[dict]:
    workflow = yaml.safe_load(CI.read_text(encoding="utf-8"))
    return workflow["jobs"]["conda-lock-verify"]["steps"]


def _verify_index(steps: list[dict]) -> int:
    (index,) = [i for i, step in enumerate(steps) if step.get("name") == VERIFY_STEP]
    return index


def test_no_step_reads_the_runner_images_conda_variable() -> None:
    uses_image_conda = [
        step.get("name", "?") for step in _steps() if IMAGE_CONDA.search(step.get("run", ""))
    ]
    assert not uses_image_conda, (
        "conda-lock-verify must not read the runner image's $CONDA: ubuntu-26.04 does not set "
        f"it and ships no Miniconda (#888); found in {uses_image_conda}"
    )


def test_a_binary_only_setup_micromamba_step_runs_before_the_verify_step() -> None:
    steps = _steps()
    verify = _verify_index(steps)
    setups = [
        i
        for i, step in enumerate(steps)
        if isinstance(step.get("uses"), str) and step["uses"].startswith(SETUP_MICROMAMBA)
    ]
    assert len(setups) == 1 and setups[0] < verify, (
        "conda-lock-verify needs exactly one setup-micromamba step, before the verify step"
    )
    inputs = steps[setups[0]].get("with") or {}
    # Binary only: with no environment to create, the action installs micromamba and exports
    # MAMBA_EXE (setup-micromamba v3.2.1 README "Environment creation", src/options.ts).
    assert not {"environment-file", "environment-name", "create-args"} & set(inputs), inputs
    # The pin itself (value, action SHA) is held equal to the other call sites by
    # tests/test_marker_contract.py; this only checks that the job has one.
    assert inputs.get("micromamba-version"), "the micromamba binary must be pinned"


def test_every_conda_lock_call_uses_the_pinned_micromamba() -> None:
    run = _steps()[_verify_index(_steps())]["run"]
    calls = [line.strip() for line in run.splitlines() if line.strip().startswith("conda-lock ")]
    assert calls, "the verify step no longer calls conda-lock"
    for call in calls:
        assert "--micromamba" in call and '--conda "$MAMBA_EXE"' in call, (
            f"each conda-lock call must use the pinned micromamba as its backend: {call!r}"
        )
    # Fail closed with a clear message if the action ever stops exporting the binary path.
    assert ': "${MAMBA_EXE:?' in run

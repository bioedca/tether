# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Contract tests for the hash-locked setuptools runtime compatibility wheel.

Issue #218 replaces three independently resolved ``setuptools<81`` consumers with one
committed requirement whose exact wheel filename and digest are verified before constructor
adopts it.  These tests discover the executable consumers so a future workflow cannot quietly
reintroduce a floating match spec.
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCK = REPO_ROOT / "packaging" / "setuptools-compatibility.txt"
VERIFIER = REPO_ROOT / "scripts" / "verify_setuptools_wheel.py"
PACKAGING_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "packaging.yml"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"
SIDECAR_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "sidecar.yml"
MEASURE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "sidecar-measure.yml"
PACKAGING_DOCS = REPO_ROOT / "packaging" / "README.md"
ADR = REPO_ROOT / "docs" / "adr" / "0054-hash-locked-setuptools-runtime-compatibility-wheel.md"
LOCK_RELPATH = "packaging/setuptools-compatibility.txt"


def _load_verifier():
    assert VERIFIER.exists(), f"missing compatibility-wheel verifier: {VERIFIER}"
    spec = importlib.util.spec_from_file_location("tether_verify_setuptools_wheel", VERIFIER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _execution_strings(path: Path) -> list[str]:
    """Return every GitHub Actions ``run`` and ``with.create-args`` value in *path*."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    found: list[str] = []

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "run":
                    found.append(str(child))
                elif key == "with" and isinstance(child, dict) and "create-args" in child:
                    found.append(str(child["create-args"]))
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(data)
    return found


def test_lock_is_single_exact_hash_locked_wheel_source() -> None:
    verifier = _load_verifier()
    lock = verifier.load_lock(LOCK)

    assert re.fullmatch(r"\d+\.\d+\.\d+", lock.version)
    assert re.fullmatch(r"[0-9a-f]{64}", lock.sha256)
    assert lock.filename == f"setuptools-{lock.version}-py3-none-any.whl"


@pytest.mark.parametrize("workflow", [PACKAGING_WORKFLOW, RELEASE_WORKFLOW])
def test_packaging_drivers_download_and_verify_the_single_lock(workflow: Path) -> None:
    execution_strings = _execution_strings(workflow)
    lock_commands = [command for command in execution_strings if f"-r {LOCK_RELPATH}" in command]
    assert len(lock_commands) == 1
    lock_command = lock_commands[0]
    assert "--require-hashes" in lock_command
    assert "--only-binary=:all:" in lock_command
    assert "--no-deps" in lock_command

    runs = "\n".join(execution_strings)
    assert "scripts/verify_setuptools_wheel.py packaging/staging" in runs
    assert "SETUPTOOLS_WHEEL=staging/$(basename" in runs
    executable = "\n".join(line for line in runs.splitlines() if not line.lstrip().startswith("#"))
    assert not re.search(r"setuptools\s*(?:===|==|~=|!=|<=|>=|<|>)\s*\d", executable), (
        f"{workflow.name} contains an executable setuptools version spec outside {LOCK_RELPATH}"
    )


@pytest.mark.parametrize("workflow", [PACKAGING_WORKFLOW, RELEASE_WORKFLOW])
def test_pkgbuild_provides_ordinary_setuptools_for_no_isolation_build(workflow: Path) -> None:
    execution_strings = _execution_strings(workflow)
    create_args = [value for value in execution_strings if "constructor>=3.16" in value]
    assert len(create_args) == 1
    create_tokens = create_args[0].split()
    assert create_tokens.count("setuptools") == 1
    assert (
        create_tokens.index("pip")
        < create_tokens.index("setuptools")
        < create_tokens.index("wheel")
    )

    tmaven_builds = [
        command
        for command in execution_strings
        if "pip wheel" in command and "TMAVEN_SPEC" in command
    ]
    assert len(tmaven_builds) == 1
    assert "--no-build-isolation" in tmaven_builds[0]


def test_packaging_docs_provision_ordinary_setuptools_for_no_isolation_build() -> None:
    packaging = PACKAGING_DOCS.read_text(encoding="utf-8")
    assert "conda-standalone python-build pip setuptools wheel" in packaging
    assert "--no-build-isolation" in packaging


@pytest.mark.parametrize("workflow", [SIDECAR_WORKFLOW, MEASURE_WORKFLOW])
def test_live_sidecar_consumers_delegate_to_setup_script(workflow: Path) -> None:
    runs = "\n".join(_execution_strings(workflow))
    assert "scripts/setup_sidecar.py" in runs
    executable = "\n".join(line for line in runs.splitlines() if not line.lstrip().startswith("#"))
    assert not re.search(r"setuptools\s*(?:===|==|~=|!=|<=|>=|<|>)\s*\d", executable), (
        f"{workflow.name} bypasses setup_sidecar.py with its own setuptools version spec"
    )


def test_measure_workflow_gives_the_setup_probe_a_writable_numba_cache() -> None:
    data = yaml.safe_load(MEASURE_WORKFLOW.read_text(encoding="utf-8"))
    steps = data["jobs"]["measure"]["steps"]
    setup_step = next(step for step in steps if "scripts/setup_sidecar.py" in step.get("run", ""))
    assert setup_step["env"]["NUMBA_CACHE_DIR"] == "${{ runner.temp }}/numba-cache"


def test_verifier_accepts_only_the_derived_filename_and_digest(tmp_path: Path) -> None:
    verifier = _load_verifier()
    wheel_bytes = b"verified compatibility wheel fixture"
    digest = hashlib.sha256(wheel_bytes).hexdigest()
    requirements = tmp_path / "compatibility.txt"
    requirements.write_text(
        f"setuptools==1.2.3 \\\n    --hash=sha256:{digest}\n",
        encoding="utf-8",
    )
    wheel = tmp_path / "setuptools-1.2.3-py3-none-any.whl"
    wheel.write_bytes(wheel_bytes)

    lock = verifier.load_lock(requirements)
    assert verifier.verify_wheel(tmp_path, lock=lock) == wheel

    wheel.write_bytes(b"tampered compatibility wheel fixture")
    with pytest.raises(verifier.VerificationError, match="SHA-256 mismatch"):
        verifier.verify_wheel(tmp_path, lock=lock)
    wheel.write_bytes(wheel_bytes)

    wheel.rename(tmp_path / "setuptools-1.2.4-py3-none-any.whl")
    with pytest.raises(verifier.VerificationError, match="expected exactly"):
        verifier.verify_wheel(tmp_path, lock=lock)


def test_documented_exception_has_security_scope_and_removal_trigger() -> None:
    packaging = PACKAGING_DOCS.read_text(encoding="utf-8")
    decision = ADR.read_text(encoding="utf-8")
    assert LOCK_RELPATH in packaging
    assert "runtime compatibility" in packaging
    assert "no longer imports" in packaging and "`pkg_resources`" in packaging
    assert "GHSA-h35f-9h28-mq5c" in decision
    assert "PYSEC-2026-3447" in decision
    assert "83.0.0" in decision
    assert "never builds a source distribution" in decision
    assert "no longer imports" in decision and "`pkg_resources`" in decision

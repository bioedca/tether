# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The full pipeline on the maintainer's acquisitions, timed stage by stage (#723).

A ``workstation`` test: only ``scripts/workstation/run_private_checks.py`` runs it, with the
private data (ADR-0071). An acquisition takes part when its neutral id has the role ``speed``
(its movie) in the mapping file; the roles ``tmap`` and ``tdat`` of the same id, when present,
go to the extraction. Each stage runs in its own process (``scripts/workstation/perf_stage.py``)
and the test records its time and peak memory, and the size of the ``.tether``. The runner
copies these numbers into the speed history (``scripts/workstation/perf_history.py``).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

STAGE_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "workstation" / "perf_stage.py"
#: The stages, in order. Each one reads what the one before it wrote.
STAGES = ("extract", "correct", "idealize", "analysis", "save", "export")
#: The issue asks for at least three full acquisitions.
MIN_ACQUISITIONS = 3
SPEED_ROLE = "speed"


def run_stage(stage: str, **paths: Path | None) -> dict:
    """Run one stage in a child process and return its measurement."""
    cmd = [sys.executable, str(STAGE_SCRIPT), stage]
    for name, value in paths.items():
        if value is not None:
            cmd += [f"--{name}", str(value)]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    # The output can name a file, so it goes only to the local log of pytest (ADR-0071).
    assert proc.returncode == 0, f"stage {stage} failed (exit {proc.returncode}): {proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_stage_measurement_selftest() -> None:
    """The measurement works on this operating system: 64 MiB held shows in the peak."""
    from importlib.util import module_from_spec, spec_from_file_location

    spec = spec_from_file_location("perf_stage", STAGE_SCRIPT)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    result = run_stage("selftest")
    assert result["stage"] == "selftest"
    assert 0.1 <= result["time_s"] < 30
    assert result["peak_bytes"] >= module.SELFTEST_BYTES


@pytest.mark.workstation
def test_pipeline_speed_and_memory(private_data, tmp_path: Path) -> None:
    ids = [i for i in private_data.ids if SPEED_ROLE in private_data.roles(i)]
    assert len(ids) >= MIN_ACQUISITIONS, (
        f"{len(ids)} ids have the role {SPEED_ROLE!r}; the speed history needs at least "
        f"{MIN_ACQUISITIONS} (docs/maintainers/workstation-runner.md)"
    )
    for nid in ids:
        roles = private_data.roles(nid)
        extra = {r: private_data.path(nid, r) for r in ("tmap", "tdat") if r in roles}
        store = tmp_path / f"{nid}.tether"
        numbers: dict[str, float] = {}
        for stage in STAGES:
            paths: dict[str, Path | None] = {"store": store}
            if stage == "extract":
                paths.update(movie=private_data.path(nid, SPEED_ROLE), **extra)
            elif stage in ("save", "export"):
                paths["out"] = tmp_path / f"{nid}-{stage}.{'tether' if stage == 'save' else 'csv'}"
            measured = run_stage(stage, **paths)
            numbers[f"{stage}_s"] = round(measured["time_s"], 3)
            numbers[f"{stage}_peak_bytes"] = int(measured["peak_bytes"])
        numbers["tether_bytes"] = store.stat().st_size
        private_data.record(nid, **numbers)
        store.unlink()

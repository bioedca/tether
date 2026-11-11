# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""``scripts/run_m1_oracle.py`` reproduces the ADR-0022 oracle (a) configuration (#555).

The script is the one-shot reproduction command named in ``docs/validation.md``. These
tests pin, without the gated movie, the three behaviours that make it match the
enforcing test ``tests/test_oracle.py::test_extraction_meets_m1_acceptance_on_uckopsb``:
the frozen match tolerance, the ``.tdat`` detector settings, and an RMS gate that
applies only to a natively fit map.
"""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

from tether.project import extract as extract_mod
from tether.project import oracle as oracle_mod
from tether.project.oracle import MATCH_TOL_PX, OracleResult

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_m1_oracle.py"
BASE = "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("tether_run_m1_oracle", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stage(tmp_path: Path, *, n_tmap: int = 1, n_tdat: int = 1) -> Path:
    """Empty stand-ins for the gated files; only their names and count matter here."""
    data = tmp_path / "data"
    data.mkdir()
    (data / f"{BASE}.tif").touch()
    (data / f"DeepLASI_MAT_export_{BASE}.mat").touch()
    for i in range(n_tmap):
        (data / f"DeepLASI_MAP_{i}.tmap").touch()
    for i in range(n_tdat):
        (data / f"settings_{i}.tdat").touch()
    return data


def _result(registration_rms_px: float, tol_px: float, intensity: str) -> OracleResult:
    """A result that passes recall and donor Pearson, so only the RMS leg can fail it."""
    return OracleResult(
        n_ground_truth=10,
        n_extracted=10,
        n_matched=10,
        recall=1.0,
        match_tol_px=tol_px,
        coord_rms_px=0.1,
        intensity=intensity,
        donor_pearson=np.array([0.99]),
        acceptor_pearson=np.array([0.9]),
        donor_pearson_median=0.99,
        acceptor_pearson_median=0.9,
        donor_pearson_pooled=0.99,
        acceptor_pearson_pooled=0.9,
        matches=(),
        registration_rms_px=registration_rms_px,
    )


@pytest.fixture
def fake_run(monkeypatch):
    """Replace extraction and scoring with recorders; returns the recorded calls."""
    calls: dict[str, list] = {"extract": [], "evaluate": []}
    summary = SimpleNamespace(
        n_molecules=10,
        registration_source="imported",
        detection_mode="intensity",
        rms_residual=math.nan,
    )

    def fake_extract(movie, out, **kwargs):
        calls["extract"].append(kwargs)
        return summary

    def fake_evaluate(out, mat, *, tol_px, intensity, registration_rms_px):
        calls["evaluate"].append({"tol_px": tol_px, "registration_rms_px": registration_rms_px})
        return _result(registration_rms_px, tol_px, intensity)

    monkeypatch.setattr(extract_mod, "extract_movie", fake_extract)
    monkeypatch.setattr(oracle_mod, "evaluate_project", fake_evaluate)
    calls["summary"] = summary  # type: ignore[assignment]
    return calls


def test_match_tolerance_defaults_to_frozen_constant(tmp_path, fake_run) -> None:
    script = _load()
    assert script._build_parser().parse_args([]).tol_px == MATCH_TOL_PX

    data = _stage(tmp_path)
    assert script.main(["--data-dir", str(data)]) == 0
    assert [c["tol_px"] for c in fake_run["evaluate"]] == [MATCH_TOL_PX, MATCH_TOL_PX]


def test_locates_the_unique_tdat_and_passes_it_to_extraction(tmp_path, fake_run) -> None:
    script = _load()
    data = _stage(tmp_path)
    found = script._locate(str(data))
    assert found["tdat"] == data / "settings_0.tdat"

    assert script.main(["--data-dir", str(data)]) == 0
    (kwargs,) = fake_run["extract"]
    assert kwargs == {"tmap": found["tmap"], "tdat": found["tdat"]}


@pytest.mark.parametrize(("n_tmap", "n_tdat"), [(1, 0), (1, 2), (2, 1)])
def test_ambiguous_or_missing_tmap_or_tdat_is_refused(tmp_path, monkeypatch, n_tmap, n_tdat):
    monkeypatch.delenv("TETHER_UCKOPSB_DIR", raising=False)
    script = _load()
    data = _stage(tmp_path, n_tmap=n_tmap, n_tdat=n_tdat)
    with pytest.raises(SystemExit, match="exactly one .tdat"):
        script._locate(str(data))


def test_imported_tmap_rms_is_not_gated(tmp_path, fake_run) -> None:
    script = _load()
    data = _stage(tmp_path)
    assert script.main(["--data-dir", str(data)]) == 0
    assert all(math.isnan(c["registration_rms_px"]) for c in fake_run["evaluate"])


@pytest.mark.parametrize(("rms", "expected_exit"), [(0.3, 0), (0.7, 1)])
def test_native_fit_rms_is_gated(tmp_path, fake_run, rms, expected_exit) -> None:
    fake_run["summary"].registration_source = "native"
    fake_run["summary"].rms_residual = rms
    script = _load()
    data = _stage(tmp_path)
    assert script.main(["--data-dir", str(data)]) == expected_exit
    assert [c["registration_rms_px"] for c in fake_run["evaluate"]] == [rms, rms]

# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The arithmetic of the Deep-LASI agreement checks, on synthetic numbers (#715).

The checks themselves read private laboratory data and run only on the workstation
(``tests/test_deeplasi_agreement.py``). These tests keep their arithmetic honest on every CI
runner: Deep-LASI's corrected FRET formula, the ADR-0028 convention that maps its factors onto
Tether's, the comparison of the two correction chains on an acquisition with known factors,
and the export-against-export score of the scripted rerun.
"""

from __future__ import annotations

import pytest

pytest.importorskip("numpy")

import numpy as np  # noqa: E402

import _deeplasi_agreement as agree  # noqa: E402
from tether.io.deeplasi import DeepLasiExport  # noqa: E402

ALPHA = 0.08
GAMMA = 1.25  # Tether's convention: bare donor


def _export(donor: np.ndarray, acceptor: np.ndarray, xy: np.ndarray | None = None):
    n = donor.shape[0]
    xy = np.column_stack([np.arange(n) * 10.0 + 5.0, np.full(n, 20.0)]) if xy is None else xy
    return DeepLasiExport(
        donor_xy=xy,
        acceptor_xy=xy + 100.0,
        donor_raw=donor + 50.0,
        acceptor_raw=acceptor + 40.0,
        donor_corrected=donor,
        acceptor_corrected=acceptor,
        donor_background=np.full_like(donor, 50.0),
        acceptor_background=np.full_like(acceptor, 40.0),
        movie_name="synthetic.tif",
        movie_path="synthetic",
        exported_by="pytest",
    )


def _acquisition(n: int = 40, frames: int = 600, seed: int = 715):
    """Molecules with known E, alpha and gamma, an acceptor bleach, then a donor bleach."""
    rng = np.random.default_rng(seed)
    total = 1000.0
    efficiency = rng.uniform(0.2, 0.8, n)
    donor = np.zeros((n, frames))
    acceptor = np.zeros((n, frames))
    for i in range(n):
        t_acc = int(rng.integers(200, 350))
        t_don = int(rng.integers(t_acc + 120, frames - 40))
        d = np.zeros(frames)
        a_true = np.zeros(frames)
        d[:t_acc] = total * (1.0 - efficiency[i])
        a_true[:t_acc] = GAMMA * total * efficiency[i]
        d[t_acc:t_don] = total
        donor[i] = d + rng.normal(0.0, 15.0, frames)
        acceptor[i] = a_true + ALPHA * d + rng.normal(0.0, 15.0, frames)
    return donor, acceptor, efficiency


def test_deeplasi_formula_by_hand() -> None:
    # A = 600, D = 400, b = 0.1, g = 0.5: (600 - 40) / (560 + 0.5 * 1.1 * 400) = 560 / 780.
    got = agree.deeplasi_corrected_fret(
        np.array([[400.0]]), np.array([[600.0]]), np.array([0.1]), np.array([0.5])
    )
    assert got[0, 0] == pytest.approx(560.0 / 780.0, rel=1e-15)


def test_deeplasi_formula_gives_nan_on_a_zero_denominator() -> None:
    got = agree.deeplasi_corrected_fret(np.zeros((1, 2)), np.zeros((1, 2)), [0.1], [0.5])
    assert np.isnan(got).all()


def test_convention_parity_is_rounding_only() -> None:
    donor, acceptor, _ = _acquisition()
    n = donor.shape[0]
    rng = np.random.default_rng(1)
    b = rng.uniform(0.02, 0.25, n)
    g = rng.uniform(0.2, 4.0, n)
    numbers = agree.fret_agreement(_export(donor, acceptor), b, g)
    assert numbers["parity_max_abs"] < agree.PARITY_ATOL


def test_known_factors_agree_between_the_two_chains() -> None:
    donor, acceptor, efficiency = _acquisition()
    n = donor.shape[0]
    # Deep-LASI's factors for the same truth: its gamma excludes the (1 + b) of the donor.
    b = np.full(n, ALPHA)
    g = np.full(n, GAMMA / (1.0 + ALPHA))
    numbers = agree.fret_agreement(_export(donor, acceptor), b, g)
    assert numbers["n_compared"] == n
    assert numbers["alpha_tether"] == pytest.approx(ALPHA, abs=0.01)
    assert numbers["gamma_tether_median"] == pytest.approx(GAMMA, rel=0.05)
    assert numbers["gamma_deeplasi_median"] == pytest.approx(GAMMA, rel=1e-12)
    assert numbers["fret_abs_diff_median"] < 0.01
    assert numbers["fret_pearson"] > 0.99


def test_a_wrong_gamma_shows_in_the_numbers() -> None:
    donor, acceptor, _ = _acquisition()
    n = donor.shape[0]
    numbers = agree.fret_agreement(
        _export(donor, acceptor), np.full(n, ALPHA), np.full(n, 2.0 * GAMMA)
    )
    assert numbers["fret_abs_diff_median"] > 0.05


def test_factor_count_must_match_the_molecules() -> None:
    donor, acceptor, _ = _acquisition(n=5)
    with pytest.raises(ValueError, match="5 molecules"):
        agree.fret_agreement(_export(donor, acceptor), np.zeros(4), np.ones(4))


def test_an_export_against_itself_is_an_exact_repeat() -> None:
    donor, acceptor, _ = _acquisition(n=12)
    export = _export(donor, acceptor)
    numbers = agree.compare_exports(export, export)
    assert numbers["matched_fraction"] == 1.0
    assert numbers["n_matched"] == 12
    assert numbers["position_rms_px"] == 0.0
    assert numbers["trace_max_rel_diff"] == 0.0
    assert numbers["acceptor_position_max_px"] == 0.0


def test_a_moved_acceptor_spot_shows() -> None:
    donor, acceptor, _ = _acquisition(n=6)
    reference = _export(donor, acceptor)
    other = _export(donor, acceptor)
    other.acceptor_xy[2] += (0.0, 0.5)
    numbers = agree.compare_exports(reference, other)
    assert numbers["position_rms_px"] == 0.0 and numbers["trace_max_rel_diff"] == 0.0
    assert numbers["acceptor_position_max_px"] == pytest.approx(0.5)


def test_a_missing_acceptor_coordinate_is_not_an_exact_repeat() -> None:
    donor, acceptor, _ = _acquisition(n=6)
    reference = _export(donor, acceptor)
    other = _export(donor, acceptor)
    other.acceptor_xy[3, 0] = np.nan
    numbers = agree.compare_exports(reference, other)
    assert numbers["acceptor_position_max_px"] == float("inf")


def test_a_nan_in_one_trace_is_not_an_exact_repeat() -> None:
    donor, acceptor, _ = _acquisition(n=6)
    reference = _export(donor, acceptor)
    changed = donor.copy()
    changed[1, 7] = np.nan
    numbers = agree.compare_exports(reference, _export(changed, acceptor))
    assert numbers["trace_max_rel_diff"] == float("inf")


def test_a_changed_trace_and_a_lost_molecule_show() -> None:
    donor, acceptor, _ = _acquisition(n=12)
    reference = _export(donor, acceptor)
    keep = np.arange(1, 12)
    changed = donor[keep].copy()
    changed[0, 10] += 500.0
    other = _export(changed, acceptor[keep], reference.donor_xy[keep] + 0.3)
    numbers = agree.compare_exports(reference, other)
    assert numbers["n_matched"] == 11
    assert numbers["matched_fraction"] == pytest.approx(11 / 12)
    assert numbers["position_rms_px"] == pytest.approx(0.3 * np.sqrt(2.0))
    assert numbers["trace_max_rel_diff"] > 0.1


def test_read_factors_from_a_mat(tmp_path) -> None:
    sio = pytest.importorskip("scipy.io")
    path = tmp_path / "export.mat"
    sio.savemat(path, {"b": np.array([[0.1], [0.2]]), "g": np.array([[0.5], [0.6]])})
    b, g = agree.read_factors(path)
    assert b.tolist() == [0.1, 0.2] and g.tolist() == [0.5, 0.6]
    sio.savemat(path, {"b": np.array([[0.1]])})
    with pytest.raises(ValueError, match="no g"):
        agree.read_factors(path)


def test_an_export_without_gamma_says_so() -> None:
    donor, acceptor, _ = _acquisition(n=8)
    g = np.full(8, np.nan)
    numbers = agree.fret_agreement(_export(donor, acceptor), np.full(8, ALPHA), g)
    assert numbers["n_deeplasi_factors"] == 0
    assert numbers["n_compared"] == 0 and numbers["n_parity_frames"] == 0
    assert np.isnan(numbers["gamma_deeplasi_median"])
    assert numbers["b_median_deeplasi"] == pytest.approx(ALPHA)


def test_medians_skip_the_molecules_without_factors() -> None:
    donor, acceptor, _ = _acquisition()
    n = donor.shape[0]
    g = np.full(n, GAMMA / (1.0 + ALPHA))
    g[::2] = np.nan
    numbers = agree.fret_agreement(_export(donor, acceptor), np.full(n, ALPHA), g)
    assert numbers["n_deeplasi_factors"] == n - (n + 1) // 2
    assert numbers["gamma_deeplasi_median"] == pytest.approx(GAMMA, rel=1e-12)
    assert numbers["n_compared"] == numbers["n_deeplasi_factors"]
    assert numbers["n_parity_frames"] > 0 and numbers["parity_max_abs"] < agree.PARITY_ATOL


class _Mapping:
    """The two calls of the ``private_data`` fixture that :func:`window_export` makes."""

    def __init__(self, root, roles: dict[str, dict[str, str]]) -> None:
        self.root, self.roles = root, roles

    def path(self, neutral_id: str, role: str):
        return self.root / self.roles[neutral_id][role]


def _frozen_window(tmp_path, monkeypatch):
    export = tmp_path / "saved" / "acquisition-export.mat"
    export.parent.mkdir()
    export.write_bytes(b"not a real export")
    harness = agree.harness()
    harness.freeze(
        "deep-lasi",
        saved=[export],
        out=tmp_path / "data" / "deeplasi-window" / "01",
        tool_version="window, commit not recorded",
        matlab_release="2023b",
    )
    digest = harness.sha256(export)
    monkeypatch.setitem(agree.FILES, "lab-90", {"mat": (digest, export.stat().st_size)})
    return tmp_path / "data"


def test_the_window_role_names_the_frozen_export(tmp_path, monkeypatch) -> None:
    root = _frozen_window(tmp_path, monkeypatch)
    rel = "deeplasi-window/01/acquisition-export.mat"
    got = agree.window_export(_Mapping(root, {"lab-90": {"window": rel}}), "lab-90")
    assert got == root / rel


def test_a_window_role_that_is_not_the_export_fails(tmp_path, monkeypatch) -> None:
    root = _frozen_window(tmp_path, monkeypatch)
    mapping = _Mapping(root, {"lab-90": {"window": "deeplasi-window/01/provenance.json"}})
    with pytest.raises(AssertionError, match="not the export of its record"):
        agree.window_export(mapping, "lab-90")


def test_the_documented_roles_keep_the_neutral_ids_public() -> None:
    """The runner withholds every result that contains a private name (#715, #723)."""
    import importlib.util  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    script = (
        Path(__file__).resolve().parents[1] / "scripts" / "workstation" / "run_private_checks.py"
    )
    spec = importlib.util.spec_from_file_location("tether_run_private_checks_715", script)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    mapping = {
        "lab-02": {
            "window": "deeplasi-window/01/acquisition-export.mat",
            agree.STATE: "series/acquisition.tdat",
        },
        "lab-01": {"rerun": "deeplasi-runs/01/acquisition.mat"},
    }
    assert all(runner.ROLE.fullmatch(r) for roles in mapping.values() for r in roles)
    tokens = runner.private_tokens(mapping)
    assert not any(neutral_id in token for token in tokens for neutral_id in mapping)
    assert agree.STATE != "tdat"

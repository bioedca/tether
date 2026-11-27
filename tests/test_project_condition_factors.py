# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Leakage α and γ are estimated per condition inside the project (#624, ADR-0158).

Locks the scope of the three correction passes when one project holds several conditions:

* each condition's α and γ equal the values the same code gives when that condition is
  alone in a project (acceptance criterion 1);
* on a one-condition project the stored values are those of the pooled estimate the passes
  made before #624, exactly (criterion 2);
* ``/settings/leakage``, ``/settings/gamma`` and ``/settings/correction`` record each
  condition's factors and molecule count (criterion 3);
* a manual α or γ applies to the condition the caller names (issue item 3);
* the total-correction-failure rule is evaluated for each condition (issue item 4).

Headless; runs in the base CI matrix. No frozen oracle or tolerance is touched: every
comparison here is exact equality between two runs of the same estimator.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("h5py")

import h5py  # noqa: E402
import numpy as np  # noqa: E402

from test_project_gamma import _build_store, _step_trace  # noqa: E402, PLC2701
from tether.fret.gamma import estimate_gamma  # noqa: E402
from tether.fret.leakage import estimate_leakage_alpha  # noqa: E402
from tether.imaging.extract import read_molecules  # noqa: E402
from tether.io.schema import TABLE  # noqa: E402
from tether.project.correct import (  # noqa: E402
    METHOD_APPARENT_UNAVAILABLE,
    METHOD_CORRECTED,
    METHOD_MANUAL,
    compute_corrected_fret,
)
from tether.project.gamma import compute_gamma  # noqa: E402
from tether.project.leakage import compute_leakage_alpha  # noqa: E402
from tether.project.trace_layers import INTENSITY_QUANTITY_LAYERS  # noqa: E402

_N = 120
_ACC = 40
_DON = 100

#: Two conditions with clearly different photophysics: (γ, α, first seed).
_COND_A = ("cond-A", 1.2, 0.1, 1)
_COND_B = ("cond-B", 0.8, 0.2, 101)


def _traces(gamma: float, alpha: float, seed0: int, n_mol: int) -> list[tuple]:
    return [
        _step_trace(
            n=_N,
            acceptor_pb=_ACC,
            donor_pb=_DON,
            gamma_true=gamma,
            alpha=alpha,
            seed=seed0 + i,
            noise=3.0,
        )
        for i in range(n_mol)
    ]


def _store(path: Path, groups: list[tuple[str, list[tuple]]]) -> None:
    """Write one project holding ``groups`` of (condition_id, traces), bleach frames set."""
    rows = [(cond, d, a) for cond, traces in groups for d, a in traces]
    _build_store(path, np.stack([d for _, d, _ in rows]), np.stack([a for _, _, a in rows]))
    with h5py.File(path, "r+") as f:
        table = f["molecules"][TABLE][:]
        for i, (cond, _, _) in enumerate(rows):
            table["bleach_frames"][i] = (_DON, _ACC)
            table["condition_id"][i] = cond
        f["molecules"][TABLE][:] = table


def _run(path: Path, **correct_kwargs: object) -> None:
    compute_leakage_alpha(path)
    compute_gamma(path)
    compute_corrected_fret(path, **correct_kwargs)


def _conditions(path: Path, group: str) -> dict[str, dict]:
    with h5py.File(path, "r") as f:
        raw = f[f"settings/{group}"].attrs["conditions"]
    entries = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
    return {e["condition_id"]: e for e in entries}


def _text(values: np.ndarray) -> list[str]:
    return [v.decode() if isinstance(v, bytes) else str(v) for v in values]


def test_each_condition_gets_the_factors_it_gets_alone(tmp_path: Path) -> None:
    a = _traces(*_COND_A[1:], n_mol=12)
    b = _traces(*_COND_B[1:], n_mol=12)
    mixed, alone_a, alone_b = tmp_path / "ab.tether", tmp_path / "a.tether", tmp_path / "b.tether"
    _store(mixed, [("cond-A", a), ("cond-B", b)])
    _store(alone_a, [("cond-A", a)])
    _store(alone_b, [("cond-B", b)])
    for path in (mixed, alone_a, alone_b):
        _run(path)

    m = read_molecules(mixed)
    for alone, rows in ((alone_a, slice(0, 12)), (alone_b, slice(12, 24))):
        solo = read_molecules(alone)
        # The same estimator on the same molecules: equal, not merely close.
        assert np.array_equal(m["alpha"][rows], solo["alpha"])
        assert np.array_equal(m["gamma"][rows], solo["gamma"])
        assert _text(m["correction_method"][rows]) == _text(solo["correction_method"])

    # The two conditions really are estimated apart: a pooled α would sit between them.
    assert m["alpha"][0] == pytest.approx(0.1, abs=5e-3)
    assert m["alpha"][12] == pytest.approx(0.2, abs=5e-3)

    leak = _conditions(mixed, "leakage")
    gamma = _conditions(mixed, "gamma")
    with h5py.File(alone_a, "r") as fa, h5py.File(alone_b, "r") as fb:
        for cond, solo in (("cond-A", fa), ("cond-B", fb)):
            assert leak[cond]["alpha"] == float(solo["settings/leakage"].attrs["alpha"])
            assert gamma[cond]["gamma"] == float(solo["settings/gamma"].attrs["gamma"])
            assert leak[cond]["n_qualifying"] == int(solo["settings/leakage"].attrs["n_qualifying"])
    with h5py.File(mixed, "r") as f:
        # With two conditions no single factor is the project's.
        assert np.isnan(float(f["settings/leakage"].attrs["alpha"]))
        assert np.isnan(float(f["settings/gamma"].attrs["gamma"]))
        assert int(f["settings/leakage"].attrs["n_conditions"]) == 2
        assert int(f["settings/leakage"].attrs["n_molecules"]) == 24


def test_one_condition_stores_the_pooled_estimate_exactly(tmp_path: Path) -> None:
    # The estimate the passes made before #624: every examined molecule in one call.
    traces = _traces(*_COND_A[1:], n_mol=12)
    path = tmp_path / "one.tether"
    _store(path, [("cond-A", traces)])
    leak = compute_leakage_alpha(path)
    gamma = compute_gamma(path)

    donor_layer, acceptor_layer = INTENSITY_QUANTITY_LAYERS["corrected"]
    with h5py.File(path, "r") as f:
        donors = [np.asarray(r, dtype=np.float64) for r in f[f"traces/{donor_layer}"][:]]
        acceptors = [np.asarray(r, dtype=np.float64) for r in f[f"traces/{acceptor_layer}"][:]]
    pooled_alpha = estimate_leakage_alpha(donors, acceptors, [_ACC] * 12, [_DON] * 12)
    assert leak.alpha == pooled_alpha.alpha
    pooled_gamma = estimate_gamma(
        donors, acceptors, [pooled_alpha.alpha] * 12, [_ACC] * 12, [_DON] * 12
    )
    assert gamma.gamma == pooled_gamma.gamma
    table = read_molecules(path)
    assert np.array_equal(table["alpha"], np.full(12, pooled_alpha.alpha))
    assert np.array_equal(
        table["gamma"], np.array([pooled_gamma.effective_gamma(i) for i in range(12)])
    )
    with h5py.File(path, "r") as f:
        assert float(f["settings/leakage"].attrs["alpha"]) == pooled_alpha.alpha
        assert float(f["settings/gamma"].attrs["gamma"]) == pooled_gamma.gamma
    assert [c.condition_id for c in leak.conditions] == ["cond-A"]


def test_correction_records_each_conditions_factors_and_count(tmp_path: Path) -> None:
    path = tmp_path / "ab.tether"
    _store(path, [("cond-A", _traces(*_COND_A[1:], 12)), ("cond-B", _traces(*_COND_B[1:], 14))])
    _run(path)
    table = read_molecules(path)
    gamma = _conditions(path, "gamma")
    correction = _conditions(path, "correction")
    assert list(correction) == ["cond-A", "cond-B"]
    for cond, rows, n in (("cond-A", slice(0, 12), 12), ("cond-B", slice(12, 26), 14)):
        entry = correction[cond]
        assert entry["n_molecules"] == n
        assert entry["n_corrected"] == n
        assert entry["total_failure"] is False
        assert entry["alpha"] == float(table["alpha"][rows][0])
        assert entry["alpha_source"] == "estimated"
        assert entry["gamma"] == gamma[cond]["gamma"]
        assert entry["gamma_source"] == "estimated"


def test_a_manual_factor_applies_to_the_named_condition_only(tmp_path: Path) -> None:
    path = tmp_path / "ab.tether"
    _store(path, [("cond-A", _traces(*_COND_A[1:], 12)), ("cond-B", _traces(*_COND_B[1:], 12))])
    compute_leakage_alpha(path)
    compute_gamma(path)
    before = read_molecules(path)

    summary = compute_corrected_fret(
        path, alpha_override=0.05, gamma_override=2.0, condition_id="cond-B"
    )
    after = read_molecules(path)
    assert summary.override_condition_id == "cond-B"
    assert summary.n_manual == 12 and summary.n_corrected == 12
    # cond-A keeps its estimated factors; cond-B carries the manual ones.
    assert np.array_equal(after["alpha"][:12], before["alpha"][:12])
    assert np.array_equal(after["gamma"][:12], before["gamma"][:12])
    assert np.all(after["alpha"][12:] == 0.05)
    assert np.all(after["gamma"][12:] == 2.0)
    methods = _text(after["correction_method"])
    assert methods[:12] == [METHOD_CORRECTED] * 12
    assert methods[12:] == [METHOD_MANUAL] * 12
    correction = _conditions(path, "correction")
    assert correction["cond-B"]["alpha_source"] == "manual"
    assert correction["cond-B"]["gamma"] == 2.0
    assert correction["cond-A"]["alpha_source"] == "estimated"
    with h5py.File(path, "r") as f:
        assert f["settings/correction"].attrs["override_condition_id"] == "cond-B"


def test_an_override_must_name_its_condition_when_there_are_several(tmp_path: Path) -> None:
    path = tmp_path / "ab.tether"
    _store(path, [("cond-A", _traces(*_COND_A[1:], 12)), ("cond-B", _traces(*_COND_B[1:], 12))])
    _run(path)
    before = read_molecules(path)
    with pytest.raises(ValueError, match="condition_id"):
        compute_corrected_fret(path, gamma_override=2.0)
    with pytest.raises(ValueError, match="no molecule"):
        compute_corrected_fret(path, gamma_override=2.0, condition_id="cond-C")
    # A refused override wrote nothing.
    assert np.array_equal(read_molecules(path)["gamma"], before["gamma"])


def test_a_condition_with_too_few_bleach_events_fails_on_its_own(tmp_path: Path) -> None:
    # cond-B has 4 molecules, under min_qualifying_traces = 10: its α is withheld, its γ
    # is withheld with it, and only its molecules fall to apparent E.
    path = tmp_path / "ab.tether"
    _store(path, [("cond-A", _traces(*_COND_A[1:], 12)), ("cond-B", _traces(*_COND_B[1:], 4))])
    leak = compute_leakage_alpha(path)
    assert leak.applied is True and leak.alpha is None
    assert {c.condition_id: c.applied for c in leak.conditions} == {
        "cond-A": True,
        "cond-B": False,
    }
    gamma = compute_gamma(path)  # does not refuse: cond-A still has an α
    by_cond = {c.condition_id: c for c in gamma.conditions}
    assert by_cond["cond-A"].applied
    assert by_cond["cond-B"].leakage_withheld and not by_cond["cond-B"].applied
    summary = compute_corrected_fret(path)
    assert summary.total_failure is False
    table = read_molecules(path)
    assert np.all(np.isnan(table["alpha"][12:])) and np.all(np.isnan(table["gamma"][12:]))
    methods = _text(table["correction_method"])
    assert methods[:12] == [METHOD_CORRECTED] * 12
    assert methods[12:] == [METHOD_APPARENT_UNAVAILABLE] * 4
    correction = _conditions(path, "correction")
    assert correction["cond-A"]["total_failure"] is False
    assert correction["cond-B"]["total_failure"] is True
    assert correction["cond-B"]["alpha"] is None and correction["cond-B"]["gamma"] is None
    assert _conditions(path, "gamma")["cond-B"]["leakage_withheld"] is True


def test_gamma_still_refuses_when_no_condition_has_alpha(tmp_path: Path) -> None:
    path = tmp_path / "few.tether"
    _store(path, [("cond-A", _traces(*_COND_A[1:], 4)), ("cond-B", _traces(*_COND_B[1:], 4))])
    leak = compute_leakage_alpha(path)
    assert leak.applied is False
    with pytest.raises(ValueError, match="compute_leakage_alpha"):
        compute_gamma(path)


def test_a_missing_alpha_outside_a_withheld_condition_is_still_an_error(tmp_path: Path) -> None:
    path = tmp_path / "ab.tether"
    _store(path, [("cond-A", _traces(*_COND_A[1:], 12)), ("cond-B", _traces(*_COND_B[1:], 12))])
    compute_leakage_alpha(path)
    with h5py.File(path, "r+") as f:
        table = f["molecules"][TABLE][:]
        table["alpha"][3] = np.nan  # cond-A's α was applied; this row lost it
        f["molecules"][TABLE][:] = table
    with pytest.raises(ValueError, match="row 3"):
        compute_gamma(path)


def test_a_second_conditions_manual_factor_keeps_the_first_ones(tmp_path: Path) -> None:
    # Codex P1 on #1486: one condition per call, so a manual factor for cond-B must not
    # relabel cond-A's earlier manual factors as estimated or drop their provenance.
    path = tmp_path / "ab.tether"
    _store(path, [("cond-A", _traces(*_COND_A[1:], 12)), ("cond-B", _traces(*_COND_B[1:], 12))])
    compute_leakage_alpha(path)
    compute_gamma(path)
    compute_corrected_fret(path, alpha_override=0.05, gamma_override=2.0, condition_id="cond-A")
    summary = compute_corrected_fret(path, gamma_override=0.9, condition_id="cond-B")

    table = read_molecules(path)
    methods = _text(table["correction_method"])
    assert methods == [METHOD_MANUAL] * 24
    assert summary.n_manual == 24 and summary.n_corrected == 0
    assert np.all(table["alpha"][:12] == 0.05) and np.all(table["gamma"][:12] == 2.0)
    assert np.all(table["gamma"][12:] == 0.9)
    correction = _conditions(path, "correction")
    assert correction["cond-A"]["alpha"] == 0.05 and correction["cond-A"]["gamma"] == 2.0
    assert correction["cond-A"]["alpha_source"] == "manual"
    assert correction["cond-A"]["gamma_source"] == "manual"
    assert correction["cond-A"]["n_manual"] == 12
    # cond-B: its estimated α stays estimated, its γ is the one supplied now.
    assert correction["cond-B"]["alpha_source"] == "estimated"
    assert correction["cond-B"]["gamma"] == 0.9 and correction["cond-B"]["gamma_source"] == "manual"


def test_a_later_override_of_one_factor_keeps_the_other_manual_one(tmp_path: Path) -> None:
    # Codex P1 on #1486: a manual γ, then a manual α alone for the same condition, must
    # keep recording the γ as manual (and the reverse for α).
    path = tmp_path / "ab.tether"
    _store(path, [("cond-A", _traces(*_COND_A[1:], 12)), ("cond-B", _traces(*_COND_B[1:], 12))])
    compute_leakage_alpha(path)
    compute_gamma(path)
    compute_corrected_fret(path, gamma_override=2.0, condition_id="cond-A")
    compute_corrected_fret(path, alpha_override=0.05, condition_id="cond-A")
    entry = _conditions(path, "correction")["cond-A"]
    assert (entry["alpha"], entry["alpha_source"]) == (0.05, "manual")
    assert (entry["gamma"], entry["gamma_source"]) == (2.0, "manual")

    compute_corrected_fret(path, gamma_override=1.5, condition_id="cond-A")
    entry = _conditions(path, "correction")["cond-A"]
    assert (entry["alpha"], entry["alpha_source"]) == (0.05, "manual")
    assert (entry["gamma"], entry["gamma_source"]) == (1.5, "manual")
    table = read_molecules(path)
    assert np.all(table["alpha"][:12] == 0.05) and np.all(table["gamma"][:12] == 1.5)


def test_a_manual_record_does_not_outlive_a_re_estimated_factor(tmp_path: Path) -> None:
    # Codex P2 on #1486: a manual γ for cond-A, a new γ pass, then an override for cond-B
    # must not restore cond-A's obsolete manual record over its re-estimated factors.
    path = tmp_path / "ab.tether"
    _store(path, [("cond-A", _traces(*_COND_A[1:], 12)), ("cond-B", _traces(*_COND_B[1:], 12))])
    compute_leakage_alpha(path)
    compute_gamma(path)
    compute_corrected_fret(path, gamma_override=2.0, condition_id="cond-A")
    compute_gamma(path)  # replaces cond-A's manual γ with estimated ones
    estimated = read_molecules(path)["gamma"][:12].copy()
    compute_corrected_fret(path, gamma_override=0.9, condition_id="cond-B")

    table = read_molecules(path)
    assert np.array_equal(table["gamma"][:12], estimated)
    assert _text(table["correction_method"][:12]) == [METHOD_CORRECTED] * 12
    entry = _conditions(path, "correction")["cond-A"]
    assert entry["gamma_source"] == "estimated" and entry["gamma"] != 2.0
    assert entry["n_manual"] == 0


def test_the_batch_correct_stage_estimates_gamma_for_each_condition(tmp_path: Path) -> None:
    # Codex P1 on #1486: with several conditions no project-wide α exists, and the batch
    # stage must still run γ on the conditions that have one rather than read "withheld".
    from tether.project.batch import run_correct_stage  # noqa: PLC0415

    # The stage runs the real bleach detector, so cond-B gets a step it detects
    # (α = 0.05, γ = 0.9: the acceptor drops from 390 to 50 counts).
    path = tmp_path / "ab.tether"
    _store(path, [("cond-A", _traces(*_COND_A[1:], 12)), ("cond-B", _traces(0.9, 0.05, 101, 12))])
    detail = run_correct_stage(path)

    assert "α withheld" not in detail
    assert "cond-A: α=" in detail and "cond-B: α=" in detail
    assert detail.count("γ=") == 2
    table = read_molecules(path)
    assert np.all(np.isfinite(table["gamma"]))
    assert _text(table["correction_method"]) == [METHOD_CORRECTED] * 24


def test_an_override_that_was_not_applied_is_not_recorded_as_manual(tmp_path: Path) -> None:
    # Codex P2 on #1486: cond-B's α is withheld, so a γ alone cannot correct it; its
    # provenance must not claim a manual γ its molecules do not carry.
    path = tmp_path / "ab.tether"
    _store(path, [("cond-A", _traces(*_COND_A[1:], 12)), ("cond-B", _traces(*_COND_B[1:], 4))])
    compute_leakage_alpha(path)
    compute_gamma(path)
    summary = compute_corrected_fret(path, gamma_override=2.0, condition_id="cond-B")
    assert summary.n_manual == 0
    table = read_molecules(path)
    assert np.all(np.isnan(table["gamma"][12:]))
    entry = _conditions(path, "correction")["cond-B"]
    assert entry["gamma"] is None and entry["gamma_source"] is None
    assert entry["alpha"] is None and entry["alpha_source"] is None
    assert entry["total_failure"] is True


def test_the_batch_detail_keeps_each_conditions_clean_step_warning(monkeypatch) -> None:
    # Codex P2 on #1486: the multi-condition detail must carry ADR-0134's warning.
    from types import SimpleNamespace  # noqa: PLC0415

    import tether.project.batch as batch  # noqa: PLC0415
    from tether.project import correct, gamma, leakage, photobleach  # noqa: PLC0415

    monkeypatch.setattr(
        photobleach,
        "compute_photobleach",
        lambda p, **kw: SimpleNamespace(n_donor_bleached=1, n_acceptor_bleached=1),
    )
    monkeypatch.setattr(
        leakage,
        "compute_leakage_alpha",
        lambda p, **kw: SimpleNamespace(
            applied=True,
            alpha=None,
            conditions=(
                SimpleNamespace(condition_id="a", alpha=0.1),
                SimpleNamespace(condition_id="b", alpha=0.2),
            ),
        ),
    )
    monkeypatch.setattr(
        gamma,
        "compute_gamma",
        lambda p, **kw: SimpleNamespace(
            conditions=(
                SimpleNamespace(
                    condition_id="a",
                    gamma=1.2,
                    few_clean_steps=True,
                    n_step_clean=3,
                    n_step_evaluated=12,
                ),
                SimpleNamespace(
                    condition_id="b",
                    gamma=0.8,
                    few_clean_steps=False,
                    n_step_clean=12,
                    n_step_evaluated=12,
                ),
            ),
        ),
    )
    monkeypatch.setattr(
        correct,
        "compute_corrected_fret",
        lambda p, **kw: SimpleNamespace(total_failure=False, apparent_e_only=False, n_corrected=3),
    )
    monkeypatch.setattr(batch, "_assert_output_not_newer", lambda p: None)

    class _Stage:
        def __enter__(self):
            return "x.tether"

        def __exit__(self, *exc):
            return False

    import tether.project._guarded_publish as gp  # noqa: PLC0415

    monkeypatch.setattr(gp, "guarded_project_write", lambda *a, **k: _Stage())
    detail = batch.run_correct_stage("x.tether")
    assert "a: α=0.100 γ=1.200 (γ from few clean steps: 3 of 12" in detail
    assert "b: α=0.200 γ=0.800" in detail
    assert detail.count("few clean steps") == 1

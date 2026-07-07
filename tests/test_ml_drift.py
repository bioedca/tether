# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cross-condition feature-distribution drift advisory (M5, FR-ML; PRD §7.5, §9 M5).

Locks the pure :mod:`tether.ml.drift`: a two-sample Kolmogorov–Smirnov sweep with a Bonferroni
family-wise correction flags a deliberately mismatched source/target pair while a matched pair does
not (the §9 M5 drift-flag acceptance, algorithm layer), NaNs are dropped not fabricated, an
all-untestable comparison raises, and the verdict is deterministic. Needs SciPy (base lock) for the
KS test.
"""

from __future__ import annotations

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")

import numpy as np  # noqa: E402

from tether.ml.drift import DEFAULT_DRIFT_ALPHA, DriftReport, condition_drift  # noqa: E402

_NAMES = ("a", "b", "c")


def _cols(
    rng: np.random.Generator, n: int, means: tuple[float, ...], scale: float = 1.0
) -> np.ndarray:
    """An ``(n, len(means))`` matrix whose column ``j`` ~ Normal(``means[j]``, ``scale``)."""
    return np.column_stack([rng.normal(m, scale, size=n) for m in means])


def test_matched_conditions_do_not_flag() -> None:
    # Same distribution in every column (different draws) -> the Bonferroni-corrected sweep raises
    # no advisory. Deterministic (fixed seed), so this is stable across the 3-OS matrix.
    rng = np.random.default_rng(0)
    src = _cols(rng, 200, (0.0, 5.0, 10.0))
    tgt = _cols(rng, 200, (0.0, 5.0, 10.0))
    report = condition_drift(src, tgt, _NAMES)
    assert isinstance(report, DriftReport)
    assert report.drifted is False
    assert report.drifted_features == ()
    assert report.n_tested == 3


def test_shifted_distribution_raises_the_advisory() -> None:
    # A large mean shift in column c only -> c drifts, the untouched columns do not.
    rng = np.random.default_rng(1)
    src = _cols(rng, 200, (0.0, 5.0, 10.0))
    tgt = _cols(rng, 200, (0.0, 5.0, 30.0))
    report = condition_drift(src, tgt, _NAMES)
    assert report.drifted is True
    assert "c" in report.drifted_features
    assert "a" not in report.drifted_features
    assert "b" not in report.drifted_features


def test_multiple_axes_can_drift_together() -> None:
    # Both a and c shifted -> both surface; mirrors the FRET-range + SNR axes drifting at once.
    rng = np.random.default_rng(2)
    src = _cols(rng, 200, (0.0, 5.0, 10.0))
    tgt = _cols(rng, 200, (8.0, 5.0, 30.0))
    report = condition_drift(src, tgt, _NAMES)
    assert set(report.drifted_features) >= {"a", "c"}


def test_conditions_may_have_different_molecule_counts() -> None:
    rng = np.random.default_rng(3)
    src = _cols(rng, 120, (0.0, 5.0, 10.0))
    tgt = _cols(rng, 40, (0.0, 5.0, 10.0))
    report = condition_drift(src, tgt, _NAMES)
    assert report.drifted is False
    by = {f.name: f for f in report.features}
    assert by["a"].n_source == 120
    assert by["a"].n_target == 40


def test_bonferroni_threshold_is_alpha_over_n_tested() -> None:
    # The per-feature verdict is exactly `pvalue < alpha / n_tested` for every tested feature, and
    # corrected_alpha reports that threshold.
    rng = np.random.default_rng(4)
    src = _cols(rng, 150, (0.0, 5.0, 10.0))
    tgt = _cols(rng, 150, (0.6, 5.0, 12.0))
    report = condition_drift(src, tgt, _NAMES, alpha=0.05)
    assert report.corrected_alpha == pytest.approx(0.05 / report.n_tested)
    for f in report.features:
        if f.tested:
            assert f.drifted == (f.pvalue < report.corrected_alpha)
        else:
            assert f.drifted is False


def test_nan_values_are_dropped_not_fabricated() -> None:
    # A column that is all-NaN in one condition is untestable (never a fabricated "no drift"); a
    # column with a few NaNs is still tested on its finite values.
    rng = np.random.default_rng(5)
    src = _cols(rng, 50, (0.0, 5.0, 10.0))
    tgt = _cols(rng, 50, (0.0, 5.0, 10.0))
    tgt[:, 2] = np.nan  # column c untestable in the target
    src[:5, 0] = np.nan  # a few NaNs in a -> still tested on the remaining 45
    report = condition_drift(src, tgt, _NAMES)
    by = {f.name: f for f in report.features}
    assert by["c"].tested is False
    assert np.isnan(by["c"].statistic) and np.isnan(by["c"].pvalue)
    assert by["c"].drifted is False
    assert by["a"].tested is True
    assert by["a"].n_source == 45
    assert report.n_tested == 2
    assert report.corrected_alpha == pytest.approx(DEFAULT_DRIFT_ALPHA / 2)


def test_identical_samples_are_not_drift() -> None:
    x = np.array([[1.0], [2.0], [3.0], [4.0], [5.0]])
    report = condition_drift(x, x, ("a",))
    assert report.drifted is False
    assert report.features[0].statistic == 0.0
    assert report.features[0].pvalue == pytest.approx(1.0)


def test_all_untestable_raises() -> None:
    # One finite value per column per condition -> nothing can be KS-tested -> undefined, raised.
    with pytest.raises(ValueError, match="undefined"):
        condition_drift(np.array([[0.0]]), np.array([[1.0]]), ("a",))


def test_alpha_out_of_range_raises() -> None:
    x = _cols(np.random.default_rng(6), 20, (0.0,))
    for bad in (0.0, 1.0, -0.1, 1.5):
        with pytest.raises(ValueError, match="alpha"):
            condition_drift(x, x, ("a",), alpha=bad)


def test_shape_validation() -> None:
    with pytest.raises(ValueError, match="2-D"):
        condition_drift(np.zeros(3), np.zeros((3, 1)), ("a",))
    with pytest.raises(ValueError, match="column"):
        condition_drift(np.zeros((3, 2)), np.zeros((3, 2)), ("a",))
    with pytest.raises(ValueError, match="column"):
        condition_drift(np.zeros((3, 2)), np.zeros((3, 3)), ("a", "b"))


def test_deterministic() -> None:
    rng = np.random.default_rng(7)
    src = _cols(rng, 100, (0.0, 5.0))
    tgt = _cols(rng, 100, (1.0, 5.0))
    first = condition_drift(src, tgt, ("a", "b"))
    second = condition_drift(src, tgt, ("a", "b"))
    assert [f.pvalue for f in first.features] == [f.pvalue for f in second.features]
    assert [f.statistic for f in first.features] == [f.statistic for f in second.features]
    assert first.drifted == second.drifted

# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cross-condition feature-distribution drift — the advisory seeding flag (PRD §7.5; FR-ML).

A condition's quality ranker **may** be seeded from another condition (or from Deep-LASI
categories), but the two conditions may be too dissimilar for the source's preferences to
transfer. PRD §7.5 requires that cross-condition use ``raises an advisory (overridable) flag
driven by a simple feature-distribution / FRET-range / SNR drift signal between the source and
target conditions``. This module is that signal, kept pure (NumPy + one SciPy test) and
**store-free**: it turns two engineered-feature matrices (the source and the target condition)
into a per-feature drift verdict plus one overall advisory flag, with no knowledge of the
``.tether`` store (the ``condition_id`` grouping lives in :mod:`tether.project.drift`).

The signal (PRD §7.5's three named axes)
----------------------------------------
For each engineered feature (:data:`tether.ml.features.FEATURE_NAMES`) the source- and
target-condition columns are compared with the **two-sample Kolmogorov–Smirnov test** — the
standard non-parametric, label-free test of whether two samples are drawn from the same
distribution, and the field-standard covariate-drift detector [Porwik2022, Cardoso2023]. Because
the monitored columns include ``fret_mean``/``fret_var`` and ``snr``, the single per-feature sweep
covers all three axes PRD §7.5 names at once — the **feature-distribution** drift is the whole
sweep, the **FRET-range** drift is the ``fret_mean``/``fret_var`` columns, and the **SNR** drift is
the ``snr`` column; :attr:`DriftReport.drifted_features` names which axis moved.

Combining the per-feature tests (why Bonferroni)
------------------------------------------------
The overall advisory fires if **any** monitored feature drifts, but testing ``F`` features each at
level ``α`` inflates the chance of a false alarm on genuinely-matched conditions to
``1 − (1 − α)^F`` (≈ 37 % at ``α = 0.05``, ``F = 9``). To keep the *overall* false-alarm rate at
``α``, each feature is judged against the **Bonferroni-corrected** threshold ``α / n_tested`` (the
classic conservative family-wise correction; ``n_tested`` is the number of features that could
actually be tested). Real drift — a shifted distribution — has a vanishing KS p-value and clears
the corrected bar easily; only borderline noise is suppressed, which is the desired behaviour for
an *advisory* flag.

Never fabricate, never over-claim
---------------------------------
* A feature whose column has ``< 2`` finite values in **either** condition is **untestable**
  (:attr:`FeatureDrift.tested` is ``False``, statistic/p-value ``NaN``) — never a fabricated
  "no drift"; it is excluded from the Bonferroni denominator and from the flag (mirrors the
  ``< 2``-samples-undefined convention in :mod:`tether.ml.features`).
* If **no** feature is testable (both conditions too small/empty), drift is undefined and
  :func:`condition_drift` **raises** rather than return a fabricated "not drifted".

The flag is **advisory and overridable** (PRD §7.5): a caller (the seeding path) surfaces it but a
curator may seed anyway. The test uses the sample-size-consistent asymptotic KS distribution
(``method="asymp"``) so the p-value is a deterministic, tie-robust function of the two samples —
cross-platform reproducible for the 3-OS matrix, and sufficient for an advisory Bonferroni gate.

References
----------
[Porwik2022] Porwik, Doroz & Wrobel. "Detection of data drift in a two-dimensional stream using
    the Kolmogorov–Smirnov test." Procedia Computer Science (2022) — the two-sample KS test as a
    label-free distribution-drift detector.
[Cardoso2023] Cardoso et al. "Online evaluation of the Kolmogorov–Smirnov test on arbitrarily
    large samples." Journal of Computational Science (2023) — KS as a non-parametric
    goodness-of-fit / drift test between a sample and a reference.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["DEFAULT_DRIFT_ALPHA", "DriftReport", "FeatureDrift", "condition_drift"]

#: The overall (family-wise) significance level of the advisory drift flag (PRD §11.2
#: "Cross-condition drift advisory"). Each feature is judged against the Bonferroni-corrected
#: threshold ``α / n_tested`` so the combined false-alarm rate on matched conditions stays ≈ ``α``.
DEFAULT_DRIFT_ALPHA = 0.05

#: The definedness floor: a feature needs at least this many finite values in **each** condition to
#: be testable (a KS test on fewer is degenerate). Below it the feature is reported untested, never
#: fabricated as "no drift" — the ``< 2``-samples-undefined convention of :mod:`tether.ml.features`.
_MIN_SAMPLES = 2


@dataclass(frozen=True)
class FeatureDrift:
    """One engineered feature's cross-condition drift verdict (PRD §7.5).

    ``statistic`` is the two-sample Kolmogorov–Smirnov statistic ``D ∈ [0, 1]`` (the maximum gap
    between the two empirical CDFs) and ``pvalue`` its asymptotic two-sided p-value; both are
    ``NaN`` when the feature was **untestable** (``tested`` is ``False`` — fewer than
    :data:`_MIN_SAMPLES` finite values in a condition). ``drifted`` is the Bonferroni-corrected
    verdict (``pvalue < α / n_tested``), always ``False`` for an untested feature.
    """

    name: str
    statistic: float
    pvalue: float
    n_source: int
    n_target: int
    tested: bool
    drifted: bool


@dataclass(frozen=True)
class DriftReport:
    """The per-feature drift verdicts plus the one overall advisory flag (PRD §7.5).

    :attr:`drifted` is the advisory the seeding path surfaces (overridable);
    :attr:`drifted_features` names which axes moved (``fret_mean``/``fret_var`` = FRET-range,
    ``snr`` = SNR, any = the feature-distribution). ``alpha`` is the overall family-wise level;
    :attr:`corrected_alpha` is the per-feature Bonferroni threshold actually applied.
    """

    features: tuple[FeatureDrift, ...]
    alpha: float

    @property
    def n_tested(self) -> int:
        """How many features could be tested (had ≥ :data:`_MIN_SAMPLES` finite values each)."""
        return sum(1 for f in self.features if f.tested)

    @property
    def corrected_alpha(self) -> float:
        """The Bonferroni per-feature threshold ``α / n_tested`` actually applied."""
        n = self.n_tested
        return self.alpha / n if n else float("nan")

    @property
    def drifted(self) -> bool:
        """The overall advisory flag: ``True`` iff any monitored feature drifted."""
        return any(f.drifted for f in self.features)

    @property
    def drifted_features(self) -> tuple[str, ...]:
        """The names of the features that drifted, in feature order."""
        return tuple(f.name for f in self.features if f.drifted)


def condition_drift(
    source: np.ndarray,
    target: np.ndarray,
    feature_names: Sequence[str],
    *,
    alpha: float = DEFAULT_DRIFT_ALPHA,
) -> DriftReport:
    """Advisory feature-distribution drift between a source and a target condition (PRD §7.5).

    Compares each engineered feature's source- vs target-condition distribution with the two-sample
    Kolmogorov–Smirnov test and combines the per-feature verdicts into one **advisory, overridable**
    flag via a Bonferroni family-wise correction (:attr:`DriftReport.drifted`).

    Parameters
    ----------
    source, target:
        The two conditions' engineered-feature matrices, ``(n_molecules, n_features)`` ``float64``
        with the **same** column order (``feature_names``); one row per molecule. Non-finite
        (``NaN``) feature values are dropped **per feature** before its KS test (never fabricated),
        so the two conditions need not have the same number of molecules or the same missing-value
        pattern.
    feature_names:
        The ``n_features`` column names (e.g. :data:`tether.ml.features.FEATURE_NAMES`); labels the
        per-feature verdicts.
    alpha:
        The overall (family-wise) significance level of the advisory (default
        :data:`DEFAULT_DRIFT_ALPHA`, the PRD §11.2 tunable); each feature is judged at
        ``alpha / n_tested``.

    Returns
    -------
    DriftReport
        The per-feature verdicts and the overall advisory flag.

    Raises
    ------
    ValueError
        ``source``/``target`` is not 2-D, their column count differs from ``len(feature_names)``,
        ``alpha`` is not in ``(0, 1)``, or **no** feature had ≥ 2 finite values in both conditions
        (drift is undefined — surfaced loudly, never a fabricated "not drifted").
    """
    src = np.asarray(source, dtype=np.float64)
    tgt = np.asarray(target, dtype=np.float64)
    if src.ndim != 2 or tgt.ndim != 2:
        raise ValueError(
            f"source and target must be 2-D (n_molecules, n_features), "
            f"got shapes {src.shape} and {tgt.shape}"
        )
    names = tuple(str(n) for n in feature_names)
    n_features = len(names)
    if src.shape[1] != n_features or tgt.shape[1] != n_features:
        raise ValueError(
            f"source/target must have one column per feature name ({n_features}); "
            f"got {src.shape[1]} and {tgt.shape[1]} columns"
        )
    alpha = float(alpha)
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be in the open interval (0, 1), got {alpha}")

    from scipy.stats import ks_2samp  # noqa: PLC0415 — lazy: keep `import tether.ml` SciPy-free

    # First pass: raw per-feature KS on the finite values (drop NaN per feature, never fabricate).
    raw: list[tuple[str, float, float, int, int, bool]] = []
    for j, name in enumerate(names):
        s = src[:, j]
        t = tgt[:, j]
        s = s[np.isfinite(s)]
        t = t[np.isfinite(t)]
        n_s, n_t = int(s.size), int(t.size)
        if n_s < _MIN_SAMPLES or n_t < _MIN_SAMPLES:
            raw.append((name, float("nan"), float("nan"), n_s, n_t, False))
            continue
        res = ks_2samp(s, t, method="asymp")
        raw.append((name, float(res.statistic), float(res.pvalue), n_s, n_t, True))

    n_tested = sum(1 for r in raw if r[5])
    if n_tested == 0:
        raise ValueError(
            "no feature had >= 2 finite values in both conditions; drift is undefined "
            "(never fabricated as 'no drift')"
        )

    # Bonferroni: judge each feature at alpha / n_tested so the combined false-alarm stays ~alpha.
    threshold = alpha / n_tested
    features = tuple(
        FeatureDrift(
            name=name,
            statistic=stat,
            pvalue=pval,
            n_source=n_s,
            n_target=n_t,
            tested=tested,
            drifted=bool(tested and pval < threshold),
        )
        for name, stat, pval, n_s, n_t, tested in raw
    )
    return DriftReport(features=features, alpha=alpha)

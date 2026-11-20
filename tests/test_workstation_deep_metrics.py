# SPDX-FileCopyrightText: 2026 The Tether Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Small public cases for the workstation deep-check reporting mechanics."""

import numpy as np
import pytest

from _deep_device import compare_device_scores


def test_device_agreement_compares_each_molecule_at_the_decision_boundary() -> None:
    cpu = np.array([0.25, 0.50, 0.99, 0.50])
    cuda = np.array([0.49, 0.49, 0.51, 0.50])
    comparison = compare_device_scores(cpu, cuda, n_samples=4)

    assert comparison.n_disagreements == 1
    assert comparison.agreement == 0.75
    assert comparison.max_score_difference == pytest.approx(0.48)


@pytest.mark.parametrize(
    ("cuda", "message"),
    [
        (np.array([0.5]), "one score per molecule"),
        (np.array([[0.5], [0.5]]), "one score per molecule"),
        (np.array([np.nan, 0.5]), "finite probabilities"),
        (np.array([np.inf, 0.5]), "finite probabilities"),
        (np.array([0.5, 1.1]), "finite probabilities"),
    ],
)
def test_device_agreement_rejects_invalid_scores(cuda: np.ndarray, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        compare_device_scores(np.array([0.5, 0.5]), cuda, n_samples=2)

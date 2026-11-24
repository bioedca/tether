# SPDX-FileCopyrightText: 2026 The Tether Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compare CPU and CUDA classifier scores for the same ordered molecules."""

from typing import NamedTuple

import numpy as np


class DeviceAgreement(NamedTuple):
    n_disagreements: int
    agreement: float
    max_score_difference: float


def compare_device_scores(cpu: np.ndarray, cuda: np.ndarray, *, n_samples: int) -> DeviceAgreement:
    """Compare each score pair at the GPU smoke test's existing decision boundary."""
    for device, scores in (("CPU", cpu), ("CUDA", cuda)):
        if n_samples < 1 or scores.shape != (n_samples,):
            raise ValueError(f"{device} must return one score per molecule")
        if not np.all(np.isfinite(scores) & (scores >= 0) & (scores <= 1)):
            raise ValueError(f"{device} scores must be finite probabilities")
    disagree = (cpu >= 0.5) != (cuda >= 0.5)
    return DeviceAgreement(
        int(np.count_nonzero(disagree)),
        float(np.mean(~disagree)),
        float(np.max(np.abs(cpu - cuda))),
    )

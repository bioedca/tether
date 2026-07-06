# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The M5 ranker dependencies resolve + import on every OS in the locked stack.

This is the cross-platform guard for the deliberate base-lock bump (ADR-0004)
that added ``scikit-learn`` + ``xgboost`` for the per-condition quality ranker
(PRD §7.5, PLAN §9 M5). Running it in the 3-OS ``test`` matrix proves the two
deps actually install and import from the committed ``conda-lock.yml`` on
ubuntu/macos/windows — not merely that the spec solved.
"""

from __future__ import annotations


def _leading_version(version: str) -> tuple[int, int]:
    """(major, minor) parsed from a version string, non-numeric suffixes dropped."""
    fields = []
    for chunk in version.split(".")[:2]:
        digits = "".join(c for c in chunk if c.isdigit())
        fields.append(int(digits) if digits else 0)
    while len(fields) < 2:
        fields.append(0)
    return fields[0], fields[1]


def test_ranker_dependencies_importable() -> None:
    import sklearn
    import xgboost

    # Import guard: both must resolve + import in the locked stack on every OS.
    assert sklearn.__version__
    assert xgboost.__version__

    # Floor guard: the resolved versions must satisfy the environment.yml
    # constraints (scikit-learn>=1.5, xgboost>=2) the base lock is solved
    # against, so a resolver picking an incompatible older build fails loudly
    # here. Exact patch pins are owned by conda-lock.yml + conda-lock-verify
    # (ADR-0004); this asserts the declared floors, not the frozen versions.
    assert _leading_version(sklearn.__version__) >= (1, 5)
    assert _leading_version(xgboost.__version__) >= (2, 0)

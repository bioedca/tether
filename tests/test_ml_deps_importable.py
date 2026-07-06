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


def test_ranker_dependencies_importable() -> None:
    import sklearn
    import xgboost

    # Both must stay inside the Numba-bounded numpy window (>=1.26,<2.2); a
    # smoke import is enough here — the ranker PR exercises their behaviour.
    assert sklearn.__version__
    assert xgboost.__version__

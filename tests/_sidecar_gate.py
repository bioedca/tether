# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The gate every live ``@pytest.mark.sidecar`` test passes through (#750).

A live sidecar test needs a tMAVEN sidecar interpreter, which ``$TETHER_SIDECAR_PYTHON``
names. On a developer machine without one the test skips. The required ``sidecar / parity``
job must never pass that way: pytest exits 0 when every test is skipped, so a job that lost
the interpreter would report green with zero fits. That job therefore also sets
``TETHER_REQUIRE_SIDECAR=1``, and under it a missing interpreter is a **failure** — the same
rule ``TETHER_REQUIRE_PYTHON_MARKDOWN`` applies to the docs parity tests (#517).
``tests/test_marker_contract.py`` checks both halves: that the job sets the variable, and that
the sidecar tests fail rather than skip under it.

This is a plain helper module (not a ``test_*`` file, so pytest never collects it), importable
as ``from _sidecar_gate import ...`` under pytest's default ``prepend`` import mode, as
``tests/_analysis_store.py`` is. The sidecar job runs in the isolated tMAVEN env, so this file
imports only the standard library and pytest.
"""

from __future__ import annotations

import functools
import os
from collections.abc import Callable
from typing import Any

import pytest

#: Names the sidecar interpreter. Unset on a developer machine without one.
SIDECAR_ENV = "TETHER_SIDECAR_PYTHON"
#: Set to the literal ``1`` by the required ``sidecar / parity`` job.
REQUIRE_ENV = "TETHER_REQUIRE_SIDECAR"

SIDECAR: str | None = os.environ.get(SIDECAR_ENV) or None


def requires_sidecar(test: Callable[..., Any]) -> Callable[..., Any]:
    """Run *test* with a sidecar interpreter; skip it without one, or fail it where required.

    Read once at import, as the ``skipif`` this replaces was, so the verdict cannot change
    between collection and the call.
    """
    if SIDECAR:
        return test
    if os.environ.get(REQUIRE_ENV) != "1":
        skip = pytest.mark.skipif(True, reason=f"set {SIDECAR_ENV} to a tMAVEN sidecar interpreter")
        return skip(test)

    @functools.wraps(test)  # keeps the signature, so pytest still injects the fixtures
    def missing_interpreter(*args: Any, **kwargs: Any) -> None:
        pytest.fail(
            f"{REQUIRE_ENV}=1 but {SIDECAR_ENV} is unset or empty: this job requires the live "
            "sidecar tests to run, and they cannot without the interpreter",
            pytrace=False,
        )

    return missing_interpreter

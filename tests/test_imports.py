# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Smoke test: the package and all eight PRD §4.2 submodules import cleanly."""

from __future__ import annotations

import importlib

import pytest

SUBMODULES = [
    "tether.io",
    "tether.imaging",
    "tether.fret",
    "tether.idealize",
    "tether.ml",
    "tether.analysis",
    "tether.gui",
    "tether.project",
]


def test_package_imports_and_exposes_version() -> None:
    import tether

    assert tether.__doc__, "tether is missing its package docstring"
    assert isinstance(tether.__version__, str)
    assert tether.__version__


@pytest.mark.parametrize("name", SUBMODULES)
def test_submodule_imports_with_docstring(name: str) -> None:
    module = importlib.import_module(name)
    assert module.__doc__, f"{name} is missing its module docstring"

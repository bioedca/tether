# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Smoke test: the package and all eight PRD §4.2 submodules import cleanly.

Also guards that ``tether.analysis`` re-exports :mod:`tether.analysis.plot_export` lazily
(PEP 562, #372): a fresh ``import tether.analysis`` leaves Matplotlib out of ``sys.modules``,
the precedent being ``tests/test_torch_optional_import.py``.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
import textwrap

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


# --- tether.analysis lazily re-exports plot_export (PEP 562, #372) -----------------------------


def _run_fresh(code: str) -> subprocess.CompletedProcess[str]:
    """Run ``code`` in a fresh interpreter, so this process's imports cannot mask the result."""
    return subprocess.run(  # noqa: S603 - fixed argument list, no shell
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,  # a hung import must fail the test, not wedge the CI job
    )


@pytest.mark.parametrize(
    "statement",
    ["import tether.analysis", "from tether.analysis import *"],
)
def test_importing_tether_analysis_does_not_import_matplotlib(statement: str) -> None:
    result = _run_fresh(
        f"""
        import sys
        {statement}  # noqa: F401, F403
        assert "matplotlib" not in sys.modules, "{statement} imported matplotlib"
        assert "tether.analysis.plot_export" not in sys.modules
        """
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_tether_analysis_exposes_export_figure_lazily() -> None:
    result = _run_fresh(
        """
        import sys
        import tether.analysis as analysis
        assert "matplotlib" not in sys.modules
        from tether.analysis import export_figure
        from tether.analysis.plot_export import export_figure as direct
        assert export_figure is direct
        assert "matplotlib" in sys.modules
        assert analysis.plot_export is sys.modules["tether.analysis.plot_export"]
        """
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_lazy_names_match_plot_export_all_and_are_listed_by_dir() -> None:
    import tether.analysis as analysis
    from tether.analysis import plot_export

    lazy = analysis._LAZY_PLOT_EXPORT_NAMES
    assert lazy == {"plot_export", *plot_export.__all__}
    assert lazy <= set(dir(analysis))
    for name in plot_export.__all__:
        assert getattr(analysis, name) is getattr(plot_export, name)
    # Star import stays Matplotlib-free, so none of the lazy names may join __all__.
    assert lazy.isdisjoint(analysis.__all__)


def test_unknown_tether_analysis_attribute_still_raises() -> None:
    import tether.analysis as analysis

    with pytest.raises(AttributeError, match="no_such_name"):
        _ = analysis.no_such_name

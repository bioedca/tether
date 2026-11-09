# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Live guard: the standalone tMAVEN main window opens from the locked sidecar env.

``@pytest.mark.sidecar`` — deselected from the CI matrix, run by ``sidecar / parity`` in
the isolated env built from ``sidecar/conda-lock.yml`` (issue #566, ADR-0010).

Everything else in the sidecar tier drives tMAVEN *headless*, and headless never draws,
so a dependency in the lock that breaks tMAVEN's GUI stays green there. That happened:
the lock resolved ``matplotlib-base`` 3.11.0, whose ``plt.close(fig)`` resets
``fig.canvas`` to a bare ``FigureCanvasBase``. tMAVEN's ``multi_canvas.__init__``
attaches a ``FigureCanvasQTAgg`` and then closes the figure through pyplot, so its first
``update_blits`` raised ``AttributeError: 'FigureCanvasBase' object has no attribute
'copy_from_bbox'`` and the window a user opens for the PRD §7.4 hand-off never appeared.
Only a human run of #543 caught it.

This test builds the window the way ``python -m tmaven`` does (``tmaven.app.setup_maven``
then ``setup_gui``, then the default session) on the offscreen Qt platform, and then
calls the exact draw path that failed. It runs in a child process of
``$TETHER_SIDECAR_PYTHON`` for the same reason the driver does: the sidecar's PyQt5 and
numpy<2 never share a process with anything else, and a Qt crash in the child fails the
test instead of the runner. tMAVEN writes its session and font cache under the user's
config directories, so the child gets throwaway ones.
"""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.sidecar

_SIDECAR = os.environ.get("TETHER_SIDECAR_PYTHON")
requires_sidecar = pytest.mark.skipif(
    not _SIDECAR, reason="set TETHER_SIDECAR_PYTHON to a tMAVEN sidecar interpreter"
)

# Run in the child. Mirrors tmaven.app.run_app minus the event loop, and exits with
# os._exit so the interpreter's Qt teardown (which tMAVEN's own source notes can segfault
# under the Fusion style) cannot turn a pass into a crash or a hang.
_PROBE = textwrap.dedent(
    """
    import json, os, sys
    import matplotlib
    from tmaven.app import setup_maven, setup_gui

    maven = setup_maven([])
    app, gui = setup_gui(maven, [])
    gui.session_default()
    app.processEvents()

    canvas = gui.plot_container.plot
    figure_canvas = canvas.figure.canvas
    report = {
        "matplotlib": matplotlib.__version__,
        "title": gui.windowTitle(),
        "plot_type": type(canvas).__name__,
        "figure_canvas_type": type(figure_canvas).__name__,
        "figure_canvas_is_plot": figure_canvas is canvas,
    }
    # The call that raised under matplotlib 3.11, then the resize path that wraps it.
    canvas.update_blits()
    canvas.resize_figure()
    app.processEvents()
    report["blits"] = sum(len(row) for row in canvas.blit_bgs)
    print("TMAVEN_GUI_REPORT " + json.dumps(report), flush=True)
    sys.stdout.flush()
    os._exit(0)
    """
)


def _child_env(home: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    # tMAVEN's appdirs config/data/cache paths, on every OS, pointed into tmp.
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        env[key] = str(home)
    for key in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        env[key] = str(home / key.lower())
    env.pop("MPLCONFIGDIR", None)  # setup_gui sets its own under the config dir above
    return env


@requires_sidecar
def test_standalone_tmaven_main_window_opens_offscreen(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    proc = subprocess.run(
        [_SIDECAR, "-c", _PROBE],
        cwd=tmp_path,
        env=_child_env(home),
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("TMAVEN_GUI_REPORT ")]
    assert proc.returncode == 0 and lines, (
        f"tMAVEN's main window did not open (exit {proc.returncode}).\n"
        f"stdout:\n{proc.stdout[-4000:]}\nstderr:\n{proc.stderr[-4000:]}"
    )
    report = json.loads(lines[-1].removeprefix("TMAVEN_GUI_REPORT "))

    assert report["title"].startswith("tMAVEN"), report
    assert report["plot_type"] == "multi_canvas", report
    # The #566 regression itself: the figure must still be drawn on tMAVEN's Qt canvas.
    assert report["figure_canvas_is_plot"], report
    assert report["figure_canvas_type"] == "multi_canvas", report
    # multi_canvas is a 2x2 grid, and update_blits saves one background per axis.
    assert report["blits"] == 4, report

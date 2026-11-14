# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Put the environment's own DLL folders first on ``PATH`` in the GUI process (#579).

On Windows, MKL (the env's BLAS) loads its dependent DLLs through the ordinary Windows
search order, which reads ``PATH``. On a PC with other scientific software, an
incompatible copy earlier on ``PATH`` won, and the first BLAS call crashed with
``0xC06D007F`` (issue #553). ``conda activate`` prevents that by putting the env's
folders first, and the installer's ``bin\\tether.bat`` and ``bin\\tether-gui.bat`` do the
same for their own process.

A shortcut cannot set an environment variable, so until #579 the Start Menu entry ran
``tether-gui.bat`` through ``cmd.exe``, and a console window flashed before the GUI
opened. [`put_env_folders_first`][] does the activation step inside the GUI process
instead: [`tether.gui.app.main`][] calls it before PySide6 or NumPy loads, so the
shortcut can start the console-less ``tether-gui.exe`` directly (ADR-0076). The
variable is changed for this process and the processes it starts; the user's
persistent ``PATH`` is never edited (ADR-0051).

This module imports nothing beyond the standard library, so calling it costs nothing
and loads no DLL.
"""

from __future__ import annotations

import ntpath
import os
import sys
from collections.abc import MutableMapping, Sequence
from pathlib import Path

__all__ = ["ACTIVATION_SUBDIRS", "env_folders", "path_with_folders_first", "put_env_folders_first"]

#: The folders ``conda activate`` puts at the front of ``PATH`` on Windows, in its order,
#: relative to the env prefix. ``()`` is the prefix itself. ``post_install.bat`` writes the
#: same list into its launchers; ``tests/test_gui_env_path.py`` keeps the two equal.
ACTIVATION_SUBDIRS: tuple[tuple[str, ...], ...] = (
    (),
    ("Library", "mingw-w64", "bin"),
    ("Library", "usr", "bin"),
    ("Library", "bin"),
    ("Scripts",),
    ("bin",),
)


def env_folders(prefix: str | os.PathLike[str]) -> list[str]:
    """Return the activation folders of the env at ``prefix`` that exist, in order.

    A folder that does not exist is left out, so a pip or venv install, which has no
    ``Library\\bin``, gets only the folders it has.
    """
    root = Path(prefix)
    folders = []
    for parts in ACTIVATION_SUBDIRS:
        folder = root.joinpath(*parts)
        if folder.is_dir():
            folders.append(str(folder))
    return folders


def _key(entry: str) -> str:
    """Compare ``PATH`` entries the way Windows does: case-blind, slashes unified."""
    return ntpath.normcase(ntpath.normpath(entry.strip().strip('"')))


def path_with_folders_first(path: str, folders: Sequence[str], sep: str = ";") -> str:
    """Return ``path`` with ``folders`` at its front, in order, and each entry once.

    An entry of ``path`` that names one of ``folders`` is moved, not repeated, so a
    second call returns the same value as the first. Empty entries are dropped. The
    order of every other entry is kept.
    """
    front = list(dict.fromkeys(folders))
    taken = {_key(folder) for folder in front}
    rest = [entry for entry in path.split(sep) if entry and _key(entry) not in taken]
    return sep.join(front + rest)


def put_env_folders_first(
    environ: MutableMapping[str, str] | None = None,
    *,
    prefix: str | os.PathLike[str] | None = None,
    platform: str | None = None,
) -> bool:
    """Put the running env's activation folders first on ``PATH``, on Windows only.

    ``environ``, ``prefix`` and ``platform`` default to [`os.environ`][],
    [`sys.prefix`][] and [`sys.platform`][]; tests pass their own. Returns ``True``
    when ``PATH`` was changed. On other platforms it does nothing and returns ``False``:
    there the env's libraries find each other through their run paths, not ``PATH``.
    """
    if (sys.platform if platform is None else platform) != "win32":
        return False
    env = os.environ if environ is None else environ
    folders = env_folders(sys.prefix if prefix is None else prefix)
    if not folders:
        return False
    old = env.get("PATH", "")
    new = path_with_folders_first(old, folders)
    if new == old:
        return False
    env["PATH"] = new
    return True

# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the GUI's own PATH step (issue #579, ADR-0076).

The Start Menu shortcut now starts the console-less ``tether-gui.exe`` directly, so no
``.bat`` puts the env's DLL folders first on ``PATH`` any more (issue #553). The GUI does
it itself, in :func:`tether.gui.env_path.put_env_folders_first`, before NumPy loads. These
tests pin what it puts where, that it matches the launchers the installer writes, that it
never touches a non-Windows process, and that importing the entry point loads no NumPy,
so the step really does come first.

The module imports no Qt, so no test here needs ``pytest-qt``.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from tether.gui import env_path
from tether.gui.env_path import (
    ACTIVATION_SUBDIRS,
    env_folders,
    path_with_folders_first,
    put_env_folders_first,
)

_POST_INSTALL_BAT = (
    Path(__file__).resolve().parents[1] / "packaging" / "scripts" / "post_install.bat"
)


def _make_env(root: Path) -> Path:
    """Create every activation folder of a conda-style env under ``root``."""
    for parts in ACTIVATION_SUBDIRS:
        root.joinpath(*parts).mkdir(parents=True, exist_ok=True)
    return root


def _expected(root: Path) -> list[str]:
    return [str(root.joinpath(*parts)) for parts in ACTIVATION_SUBDIRS]


def test_activation_folders_match_the_installer_launchers() -> None:
    """The GUI's list is the list ``post_install.bat`` writes into its launchers (#553).

    Both copies follow ``conda activate``'s order. If one moved without the other, the
    shortcut route and the terminal route would load DLLs from different places.
    """
    text = _POST_INSTALL_BAT.read_text(encoding="utf-8")
    match = re.search(r'(?m)^set "ENV_PATH=([^"]*)"$', text)
    assert match, 'post_install.bat must define `set "ENV_PATH=..."`'
    from_bat = tuple(
        tuple(entry.removeprefix("%ENV_D%").lstrip("\\").split("\\")) if entry != "%ENV_D%" else ()
        for entry in match.group(1).split(";")
    )
    assert from_bat == ACTIVATION_SUBDIRS


def test_env_folders_lists_only_existing_folders_in_order(tmp_path: Path) -> None:
    root = _make_env(tmp_path / "env")
    assert env_folders(root) == _expected(root)
    # A pip or venv install has no Library tree: only what exists is listed.
    bare = tmp_path / "venv"
    (bare / "Scripts").mkdir(parents=True)
    assert env_folders(bare) == [str(bare), str(bare / "Scripts")]
    assert env_folders(tmp_path / "missing") == []


def test_folders_go_first_and_the_users_path_keeps_its_order() -> None:
    folders = [r"C:\T\envs\tether", r"C:\T\envs\tether\Library\bin"]
    path = r"C:\Windows\system32;C:\Other\mkl;C:\Windows"
    assert path_with_folders_first(path, folders) == (
        r"C:\T\envs\tether;C:\T\envs\tether\Library\bin;C:\Windows\system32;C:\Other\mkl;C:\Windows"
    )


def test_a_folder_already_on_path_is_moved_not_repeated() -> None:
    """An env folder later on PATH moves to the front (it must come before other MKLs).

    Windows compares PATH entries without case and with either slash, and a quoted entry
    is the same folder, so those spellings count as the same entry.
    """
    folders = [r"C:\T\envs\tether", r"C:\T\envs\tether\Library\bin"]
    path = r'C:\Other\mkl;c:/t/envs/TETHER/library/bin;;"C:\T\envs\tether";C:\Windows'
    assert path_with_folders_first(path, folders) == (
        r"C:\T\envs\tether;C:\T\envs\tether\Library\bin;C:\Other\mkl;C:\Windows"
    )


def test_a_second_call_changes_nothing() -> None:
    folders = [r"C:\T\envs\tether", r"C:\T\envs\tether\Scripts"]
    once = path_with_folders_first(r"C:\Windows", folders)
    assert path_with_folders_first(once, folders) == once
    assert path_with_folders_first("", folders) == r"C:\T\envs\tether;C:\T\envs\tether\Scripts"


def test_put_env_folders_first_edits_path_on_windows(tmp_path: Path) -> None:
    root = _make_env(tmp_path / "env")
    environ = {"PATH": r"C:\Other\mkl;C:\Windows"}
    assert put_env_folders_first(environ, prefix=root, platform="win32") is True
    assert environ["PATH"].split(";") == [*_expected(root), r"C:\Other\mkl", r"C:\Windows"]
    # Idempotent: a launcher that already did the step leaves nothing to change.
    assert put_env_folders_first(environ, prefix=root, platform="win32") is False


def test_put_env_folders_first_sets_path_when_it_is_unset(tmp_path: Path) -> None:
    root = _make_env(tmp_path / "env")
    environ: dict[str, str] = {}
    assert put_env_folders_first(environ, prefix=root, platform="win32") is True
    assert environ["PATH"].split(";") == _expected(root)


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_put_env_folders_first_does_nothing_off_windows(tmp_path: Path, platform: str) -> None:
    """Linux and macOS libraries find each other through run paths, not PATH."""
    root = _make_env(tmp_path / "env")
    environ = {"PATH": "/usr/bin:/bin"}
    assert put_env_folders_first(environ, prefix=root, platform=platform) is False
    assert environ == {"PATH": "/usr/bin:/bin"}


def test_put_env_folders_first_without_env_folders_changes_nothing(tmp_path: Path) -> None:
    environ = {"PATH": r"C:\Windows"}
    assert put_env_folders_first(environ, prefix=tmp_path / "none", platform="win32") is False
    assert environ == {"PATH": r"C:\Windows"}


def test_defaults_are_the_running_process(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """With no arguments it reads ``os.environ``, ``sys.prefix`` and ``sys.platform``."""
    root = _make_env(tmp_path / "env")
    monkeypatch.setattr(env_path.sys, "prefix", str(root))
    monkeypatch.setattr(env_path.sys, "platform", "win32")
    monkeypatch.setenv("PATH", r"C:\Windows")
    assert put_env_folders_first() is True
    assert env_path.os.environ["PATH"].split(";")[: len(ACTIVATION_SUBDIRS)] == _expected(root)


def test_importing_the_gui_entry_point_loads_no_numpy() -> None:
    """The PATH step runs in ``main``; nothing imported before it may load NumPy.

    NumPy loads MKL, and MKL resolves its DLLs when it loads. If ``tether.gui.app`` (or
    ``tether``, or ``tether.gui``) imported NumPy at module level, MKL would load before
    the step, and the #553 crash would come back on the shortcut route.
    """
    code = (
        "import sys, tether.gui.app, tether.gui.env_path; "
        "bad = sorted(m for m in ('numpy', 'scipy', 'PySide6') if m in sys.modules); "
        "print(','.join(bad))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=120
    )
    assert result.stdout.strip() == "", f"loaded before the PATH step: {result.stdout.strip()}"

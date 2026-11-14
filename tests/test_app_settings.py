# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the application settings store (issue #330, ADR-0094).

The store holds one answer, the update-check consent of ADR-0060 decision 3. These tests
pin the four things the issue asks of it in this tier:

* **Three precedence levels.** Unset (no network, prompt pending), then the user's
  answer, then the machine-wide administrator override, which wins over both.
* **Every failure is off.** A missing, empty, torn or foreign file reads as unset: never
  an error and never consent. Reading creates nothing.
* **What it stores.** Exactly ``schema`` and ``update_check``; no identifier, no time,
  no path. The write is atomic.
* **Where and when.** The per-OS folders, outside any install prefix; readable at
  startup without importing Qt, NumPy or HDF5; and not read by the batch runner.

That the stored answer survives a real re-install is proved on real installers by
``packaging/scripts/upgrade_smoke.sh`` in ``packaging.yml``; the last group here pins
that the script and the workflow keep doing it.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tether.gui import app_settings
from tether.gui.app_settings import (
    OVERRIDE_FILENAME,
    SETTINGS_FILENAME,
    UpdateCheckState,
    machine_override_path,
    record_update_consent,
    settings_path,
    update_check_state,
)

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src" / "tether"
_UPGRADE_SMOKE = _ROOT / "packaging" / "scripts" / "upgrade_smoke.sh"
_PACKAGING_YML = _ROOT / ".github" / "workflows" / "packaging.yml"


@pytest.fixture
def store(tmp_path: Path) -> tuple[Path, Path]:
    """A settings file and an override file, neither of which exists yet."""
    return tmp_path / "user" / SETTINGS_FILENAME, tmp_path / "machine" / OVERRIDE_FILENAME


def _state(store: tuple[Path, Path]) -> UpdateCheckState:
    settings, override = store
    return update_check_state(settings_file=settings, override_file=override)


# --- precedence --------------------------------------------------------------------


def test_unset_is_pending_and_forbids_the_network(store: tuple[Path, Path]) -> None:
    state = _state(store)
    assert state is UpdateCheckState.PENDING
    assert state.network_allowed is False
    assert state.prompt_pending is True


def test_reading_creates_no_file_and_no_folder(store: tuple[Path, Path]) -> None:
    settings, override = store
    _state(store)
    assert not settings.parent.exists()
    assert not override.parent.exists()


@pytest.mark.parametrize(
    ("allowed", "expected"),
    [(True, UpdateCheckState.ALLOWED), (False, UpdateCheckState.DECLINED)],
)
def test_the_user_answer_is_read_back(
    store: tuple[Path, Path], allowed: bool, expected: UpdateCheckState
) -> None:
    record_update_consent(allowed, settings_file=store[0])
    state = _state(store)
    assert state is expected
    assert state.network_allowed is allowed
    assert state.prompt_pending is False


def test_a_new_answer_replaces_the_old_one(store: tuple[Path, Path]) -> None:
    record_update_consent(True, settings_file=store[0])
    record_update_consent(False, settings_file=store[0])
    assert _state(store) is UpdateCheckState.DECLINED


@pytest.mark.parametrize("answer", [None, True, False])
def test_the_administrator_override_wins_over_every_user_level(
    store: tuple[Path, Path], answer: bool | None
) -> None:
    settings, override = store
    if answer is not None:
        record_update_consent(answer, settings_file=settings)
    override.parent.mkdir(parents=True)
    override.write_text("", encoding="utf-8")
    state = _state(store)
    assert state is UpdateCheckState.DISABLED_BY_ADMINISTRATOR
    assert state.network_allowed is False
    assert state.prompt_pending is False


@pytest.mark.parametrize(
    "content", ["", '{"update_check": "allowed"}', "enable", '{"schema": 1, "update_check": "allowed"}']
)
def test_the_override_can_only_disable_whatever_it_contains(
    store: tuple[Path, Path], content: str
) -> None:
    settings, override = store
    record_update_consent(True, settings_file=settings)
    override.parent.mkdir(parents=True)
    override.write_text(content, encoding="utf-8")
    assert _state(store) is UpdateCheckState.DISABLED_BY_ADMINISTRATOR


def test_an_override_that_cannot_be_examined_counts_as_present(
    store: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, override = store
    record_update_consent(True, settings_file=settings)
    real_stat = Path.stat

    def locked(self: Path, *args: object, **kwargs: object) -> os.stat_result:
        if self == override:
            raise PermissionError(13, "Permission denied", str(self))
        return real_stat(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "stat", locked)
    assert _state(store) is UpdateCheckState.DISABLED_BY_ADMINISTRATOR


def test_an_override_under_a_plain_file_is_absent(store: tuple[Path, Path]) -> None:
    # NotADirectoryError: a file where the folder should be is a clear "no such file".
    settings, override = store
    record_update_consent(True, settings_file=settings)
    override.parent.write_text("not a folder", encoding="utf-8")
    assert _state(store) is UpdateCheckState.ALLOWED


# --- every failure is off ----------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"{",
        b'{"schema": 1, "update_check": "allo',
        b"null",
        b"[]",
        b'"allowed"',
        b'{"update_check": "allowed"}',
        b'{"schema": 2, "update_check": "allowed"}',
        b'{"schema": "1", "update_check": "allowed"}',
        b'{"schema": 1, "update_check": true}',
        b'{"schema": 1, "update_check": "yes"}',
        b'{"schema": 1, "update_check": "ALLOWED"}',
        b'{"schema": 1}',
        b"\xff\xfe\x00\x00",
        b'{"schema": 1, "update_check": "allowed", "pad": "' + b"x" * 5000 + b'"}',
    ],
    ids=[
        "empty",
        "torn-brace",
        "torn-value",
        "null",
        "list",
        "bare-string",
        "no-schema",
        "future-schema",
        "string-schema",
        "bool-value",
        "unknown-value",
        "wrong-case",
        "no-key",
        "not-utf8",
        "oversized",
    ],
)
def test_a_file_that_is_not_one_this_version_wrote_reads_as_unset(
    store: tuple[Path, Path], raw: bytes
) -> None:
    settings, _ = store
    settings.parent.mkdir(parents=True)
    settings.write_bytes(raw)
    state = _state(store)
    assert state is UpdateCheckState.PENDING
    assert state.network_allowed is False


def test_a_folder_where_the_file_should_be_reads_as_unset(store: tuple[Path, Path]) -> None:
    settings, _ = store
    settings.mkdir(parents=True)
    assert _state(store) is UpdateCheckState.PENDING


def test_the_default_paths_are_used_when_none_are_passed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = tmp_path / "s.json"
    override = tmp_path / "o"
    monkeypatch.setattr(app_settings, "settings_path", lambda: settings)
    monkeypatch.setattr(app_settings, "machine_override_path", lambda: override)
    assert update_check_state() is UpdateCheckState.PENDING
    assert record_update_consent(False) == settings
    assert update_check_state() is UpdateCheckState.DECLINED
    override.write_text("", encoding="utf-8")
    assert update_check_state() is UpdateCheckState.DISABLED_BY_ADMINISTRATOR


# --- what it stores ----------------------------------------------------------------


@pytest.mark.parametrize("allowed", [True, False])
def test_the_file_holds_exactly_the_schema_and_the_answer(
    store: tuple[Path, Path], allowed: bool
) -> None:
    written = record_update_consent(allowed, settings_file=store[0])
    assert written == store[0]
    data = json.loads(written.read_text(encoding="utf-8"))
    assert data == {"schema": 1, "update_check": "allowed" if allowed else "declined"}


def test_the_write_leaves_no_temporary_file(store: tuple[Path, Path]) -> None:
    settings, _ = store
    record_update_consent(True, settings_file=settings)
    record_update_consent(False, settings_file=settings)
    assert sorted(p.name for p in settings.parent.iterdir()) == [SETTINGS_FILENAME]


def test_a_failed_write_keeps_the_old_answer(
    store: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, _ = store
    record_update_consent(False, settings_file=settings)

    def boom(src: object, dst: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(app_settings.os, "replace", boom)
    with pytest.raises(OSError):
        record_update_consent(True, settings_file=settings)
    assert _state(store) is UpdateCheckState.DECLINED
    assert sorted(p.name for p in settings.parent.iterdir()) == [SETTINGS_FILENAME]


@pytest.mark.parametrize("value", [1, 0, "yes", None])
def test_only_a_bool_is_accepted_as_an_answer(store: tuple[Path, Path], value: object) -> None:
    with pytest.raises(TypeError):
        record_update_consent(value, settings_file=store[0])  # type: ignore[arg-type]
    assert not store[0].exists()


# --- where and when ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("platform", "env", "expected"),
    [
        (
            "win32",
            {"LOCALAPPDATA": "~/lad"},
            "~/lad/MondragonLab/Tether/settings.json",
        ),
        ("darwin", {}, "~/Library/Application Support/MondragonLab/Tether/settings.json"),
        ("linux", {"XDG_CONFIG_HOME": "~/xdg"}, "~/xdg/tether/settings.json"),
        ("linux", {"XDG_CONFIG_HOME": "relative/xdg"}, "~/.config/tether/settings.json"),
        ("linux", {"XDG_CONFIG_HOME": ""}, "~/.config/tether/settings.json"),
    ],
    ids=["windows", "macos", "linux-xdg", "linux-relative-xdg-ignored", "linux-default"],
)
def test_the_settings_file_is_in_the_per_user_folder(
    monkeypatch: pytest.MonkeyPatch, platform: str, env: dict[str, str], expected: str
) -> None:
    monkeypatch.setattr(app_settings.sys, "platform", platform)
    for name in ("LOCALAPPDATA", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(name, raising=False)
    # "~" stands for the home folder, so every value is absolute on the system running
    # the test (a "C:/" path is relative on Linux and would be ignored).
    home = str(Path.home())
    for name, value in env.items():
        monkeypatch.setenv(name, value.replace("~", home, 1))
    want = Path(expected.replace("~", home, 1))
    assert settings_path() == want


def test_windows_without_localappdata_falls_back_to_the_home_folder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_settings.sys, "platform", "win32")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert settings_path() == (
        Path.home() / "AppData" / "Local" / "MondragonLab" / "Tether" / SETTINGS_FILENAME
    )


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ("darwin", "/Library/Application Support/MondragonLab/Tether/disable-update-check"),
        ("linux", "/etc/tether/disable-update-check"),
    ],
)
def test_the_override_is_in_the_machine_folder(
    monkeypatch: pytest.MonkeyPatch, platform: str, expected: str
) -> None:
    monkeypatch.setattr(app_settings.sys, "platform", platform)
    assert machine_override_path() == Path(expected)


def test_the_windows_override_is_under_programdata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_settings.sys, "platform", "win32")
    monkeypatch.delenv("PROGRAMDATA", raising=False)
    assert machine_override_path() == Path("C:/ProgramData/MondragonLab/Tether") / OVERRIDE_FILENAME
    monkeypatch.setenv("PROGRAMDATA", str(Path.home() / "pd"))
    assert machine_override_path() == Path.home() / "pd" / "MondragonLab" / "Tether" / OVERRIDE_FILENAME


def test_the_running_store_is_outside_the_install_prefix() -> None:
    # The whole point of ADR-0094: whatever an installer does to its prefix, the answer
    # is somewhere else. In an installed copy sys.prefix is <install>/envs/tether, so the
    # install folder is two levels up.
    prefix = Path(sys.prefix).resolve()
    install_root = prefix.parent.parent if prefix.parent.name == "envs" else prefix
    for path in (settings_path(), machine_override_path()):
        assert not path.resolve().is_relative_to(install_root), (path, install_root)


def test_the_folder_names_match_the_names_the_gui_gives_qt() -> None:
    from tether.gui import app, identity

    assert app_settings._ORG == app._ORGANIZATION
    assert app_settings._APP == identity.APPLICATION_NAME


def test_a_read_at_startup_imports_no_qt_numpy_or_hdf5(tmp_path: Path) -> None:
    code = (
        "import sys\n"
        "from pathlib import Path\n"
        "from tether.gui.app_settings import update_check_state\n"
        f"state = update_check_state(Path({str(tmp_path / 's.json')!r}), Path({str(tmp_path / 'o')!r}))\n"
        "heavy = sorted({m.split('.')[0] for m in sys.modules} & "
        "{'PySide6', 'PyQt5', 'pyqtgraph', 'napari', 'numpy', 'h5py', 'scipy', 'numba'})\n"
        "print(state.value, heavy)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=120
    ).stdout.strip()
    assert out == "pending []"


def test_nothing_outside_the_gui_reads_the_store() -> None:
    # Not something the batch runner depends on (issue #330): no module outside
    # tether.gui imports it.
    offenders = []
    for path in sorted(_SRC.rglob("*.py")):
        if path.is_relative_to(_SRC / "gui"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            if any(n.endswith("app_settings") for n in names):
                offenders.append(str(path.relative_to(_ROOT)))
    assert offenders == []


# --- the installer leg -------------------------------------------------------------


def test_packaging_runs_the_upgrade_smoke_after_the_install_smoke() -> None:
    text = _PACKAGING_YML.read_text(encoding="utf-8")
    install = text.index("run: bash packaging/scripts/install_smoke.sh")
    upgrade = text.index("run: bash packaging/scripts/upgrade_smoke.sh")
    upload = text.index("Upload the installer artifact")
    assert install < upgrade < upload


def test_the_upgrade_smoke_writes_with_the_installed_app_and_checks_both_paths() -> None:
    code = "\n".join(
        line
        for line in _UPGRADE_SMOKE.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )
    first = next(line for line in code.splitlines() if line.strip() and not line.startswith("#!"))
    assert first.strip() == "set -euo pipefail"
    # The answer is written and read by the INSTALLED app's own module, never the
    # checkout's.
    assert "record_update_consent(False)" in code
    assert "update_check_state()" in code
    # Both upgrade paths: over the existing install, and remove-then-install.
    assert "over-install" in code
    assert "remove-then-install" in code
    # Each path ends by asserting the declined answer is what the app reads.
    assert code.count("expect_declined") >= 3

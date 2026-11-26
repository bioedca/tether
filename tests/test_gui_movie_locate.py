# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""A project whose movie is missing or not downloaded, found again through the shell (#638).

Every test opens a ``.tether`` whose ``/movies`` rows record real TIFF files, then moves,
replaces or "un-downloads" a file. The napari panel is not built (``_movie_panel_unavailable``
is patched), so these run headless on every OS; what they read is the movie switcher, the
shell's movie rows and the files. A cloud placeholder is simulated by giving its path the
Windows offline attribute through ``_file_attributes``, the one place every check reads it.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import numpy as np
import pytest

from _gui_harness import needs_qt

h5py = pytest.importorskip("h5py")
tifffile = pytest.importorskip("tifffile")

pytestmark = [pytest.mark.gui, needs_qt]


def _tiff(path: Path, seed: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.random.default_rng(seed).integers(0, 4000, size=(4, 16, 16), dtype=np.uint16)
    tifffile.imwrite(path, data, photometric="minisblack")
    return path


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(autouse=True)
def _no_panel(monkeypatch) -> None:
    from tether.gui import shell as shell_mod

    monkeypatch.setattr(shell_mod, "_movie_panel_unavailable", lambda: "no OpenGL here")


def _project(tmp_path: Path, n: int = 1) -> tuple[Path, list[Path]]:
    from _movie_locate_store import project_with_movies

    movies = [_tiff(tmp_path / "raw" / f"movie_{i}.tif", i) for i in range(1, n + 1)]
    return project_with_movies(tmp_path, movies), movies


def _open(section_shell, path: Path, **kwargs):
    shell = section_shell(project=False, **kwargs)
    assert shell.load_project(path) is not None, shell.status_message
    return shell


def _wait(qtbot, shell) -> None:
    qtbot.waitUntil(lambda: not shell._movie_tasks.busy, timeout=10_000)


def _labels(shell) -> list[str]:
    return [shell.movie_switcher.itemText(i) for i in range(shell.movie_switcher.count())]


def _offline(monkeypatch, *paths: Path) -> set[Path]:
    """Make ``paths`` read as cloud placeholders: data not on this disk.

    Returns the set of offline paths; clearing it is the sync client fetching their data.
    """
    from tether.project import condition_model

    real = condition_model._file_attributes
    offline = {p.absolute() for p in paths}

    def attributes(path: Path) -> int:
        value = real(path)
        if Path(path).absolute() in offline:
            value |= condition_model._FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
        return value

    monkeypatch.setattr(condition_model, "_file_attributes", attributes)
    return offline


def test_a_missing_movie_is_said_and_curation_still_works(section_shell, qtbot, tmp_path) -> None:
    from pyqtgraph.Qt import QtCore

    from tether.gui.shell import CURATE_SECTION
    from tether.project.core import Project

    path, (movie,) = _project(tmp_path)
    movie.unlink()

    shell = _open(section_shell, path)

    assert _labels(shell) == ["movie_1.tif (file missing)"]
    assert [m.movie_id for m in shell.unavailable_movies()] == ["mov-1"]
    shell.set_section(CURATE_SECTION)
    shell.molecule_list.setCurrentRow(0)
    key = shell._traces[0].molecule_key
    qtbot.keyClick(shell.molecule_list, QtCore.Qt.Key.Key_Space)
    assert Project.open(path).curation_label(key) == 1


def test_a_moved_movie_is_found_remembered_and_the_project_is_unchanged(
    section_shell, qtbot, tmp_path
) -> None:
    from tether.gui import preferences

    prefs = tmp_path / "prefs.json"
    path, (movie,) = _project(tmp_path)
    moved = tmp_path / "another_computer" / "movie_1.tif"
    moved.parent.mkdir()
    shutil.move(movie, moved)
    shell = _open(section_shell, path, preferences_file=prefs)
    before = path.read_bytes()

    assert shell.locate_movie("mov-1", moved)
    _wait(qtbot, shell)

    assert shell.unavailable_movies() == []
    assert _labels(shell) == ["movie_1.tif"]
    assert shell._project_movies[0].path == moved.absolute()
    assert "1 movie(s) found" in shell.status_message
    assert path.read_bytes() == before  # the project file is byte-for-byte unchanged
    stored = preferences.movie_location(_digest(moved), prefs)
    assert stored is not None and stored.path == moved.absolute()

    # The next open on this computer finds it without asking.
    shell.close()
    again = _open(section_shell, path, preferences_file=prefs)
    assert again.unavailable_movies() == []
    assert path.read_bytes() == before


def test_a_wrong_file_is_refused_and_nothing_changes(section_shell, qtbot, tmp_path) -> None:
    prefs = tmp_path / "prefs.json"
    path, (movie,) = _project(tmp_path)
    movie.unlink()
    impostor = _tiff(tmp_path / "elsewhere" / "movie_1.tif", 42)
    shell = _open(section_shell, path, preferences_file=prefs)
    before = path.read_bytes()

    assert shell.locate_movie("mov-1", impostor)
    _wait(qtbot, shell)

    assert "refused" in shell.status_message
    assert "checksum is different" in shell.status_message
    assert _labels(shell) == ["movie_1.tif (file missing)"]
    assert not prefs.exists() or "movie_locations" not in prefs.read_text()
    assert path.read_bytes() == before


def test_one_folder_action_finds_several_movies(section_shell, qtbot, tmp_path) -> None:
    path, movies = _project(tmp_path, n=3)
    folder = tmp_path / "copied"
    (folder / "a").mkdir(parents=True)
    (folder / "b").mkdir()
    shutil.move(movies[0], folder / "a" / movies[0].name)
    shutil.move(movies[1], folder / "b" / movies[1].name)
    movies[2].unlink()  # not copied
    shell = _open(section_shell, path)
    before = path.read_bytes()
    assert len(shell.unavailable_movies()) == 3

    assert shell.locate_movie_folder(folder)
    _wait(qtbot, shell)

    assert [m.movie_id for m in shell.unavailable_movies()] == ["mov-3"]
    assert "2 movie(s) found" in shell.status_message
    assert "movie_3.tif: no file of that name in the folder" in shell.status_message
    assert path.read_bytes() == before


def test_a_placeholder_is_not_downloaded_by_opening_or_selecting(
    section_shell, qtbot, tmp_path, monkeypatch
) -> None:
    from tether.io import adapters
    from tether.services import movie_locations

    path, (movie,) = _project(tmp_path)
    _offline(monkeypatch, movie)
    reads: list[object] = []
    monkeypatch.setattr(adapters, "open_movie_source", lambda p, *a, **k: reads.append(p))
    real_download = movie_locations.download_movie
    monkeypatch.setattr(
        movie_locations,
        "download_movie",
        lambda p, **k: reads.append(p) or real_download(p, **k),
    )

    shell = _open(section_shell, path)
    for row in range(shell.molecule_list.count()):
        shell.molecule_list.setCurrentRow(row)

    assert _labels(shell) == ["movie_1.tif (file not on this disk)"]
    assert reads == []


def test_download_and_open_states_the_size_and_waits_for_a_yes(
    section_shell, qtbot, tmp_path, monkeypatch
) -> None:
    from pyqtgraph.Qt import QtWidgets

    from tether.gui.movie_locate import format_size
    from tether.services import movie_locations

    path, (movie,) = _project(tmp_path)
    offline = _offline(monkeypatch, movie)
    downloaded: list[Path] = []
    monkeypatch.setattr(
        movie_locations, "download_movie", lambda p, **k: downloaded.append(Path(p)) or 0
    )
    asked: list[str] = []
    answer = {"value": QtWidgets.QMessageBox.StandardButton.No}

    def question(_parent, _title, text, *_args):
        asked.append(text)
        return answer["value"]

    monkeypatch.setattr(QtWidgets.QMessageBox, "question", question)
    shell = _open(section_shell, path)

    shell._download_movie_dialog()

    assert asked == [
        f"movie_1.tif is not on this disk. Download it ({format_size(movie.stat().st_size)}) "
        "and open it?"
    ]
    assert downloaded == []
    assert shell.status_message.endswith("nothing was downloaded")

    answer["value"] = QtWidgets.QMessageBox.StandardButton.Yes
    offline.clear()  # the sync client has the data now
    shell._download_movie_dialog()
    _wait(qtbot, shell)

    assert downloaded == [movie.absolute()]
    assert shell.unavailable_movies() == []
    assert shell.status_message.startswith("Download and open movie: downloaded")


def test_locating_a_placeholder_asks_before_reading_it(
    section_shell, qtbot, tmp_path, monkeypatch
) -> None:
    from pyqtgraph.Qt import QtWidgets

    path, (movie,) = _project(tmp_path)
    cloud = tmp_path / "cloud" / "movie_1.tif"
    cloud.parent.mkdir()
    shutil.move(movie, cloud)
    _offline(monkeypatch, cloud)
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName", lambda *_a, **_k: (str(cloud), "")
    )
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "question",
        lambda *_a: QtWidgets.QMessageBox.StandardButton.No,
    )
    started: list[object] = []
    shell = _open(section_shell, path)
    monkeypatch.setattr(shell, "locate_movie", lambda *a: started.append(a))

    shell._locate_movie_dialog()

    assert started == []
    assert shell.status_message == "Locate movie cancelled: nothing was downloaded"


def test_a_locate_finishing_after_the_project_closed_is_not_applied(
    section_shell, qtbot, tmp_path
) -> None:
    path, (movie,) = _project(tmp_path)
    moved = tmp_path / "moved" / "movie_1.tif"
    moved.parent.mkdir()
    shutil.move(movie, moved)
    shell = _open(section_shell, path)

    assert shell.locate_movie("mov-1", moved)
    shell.close_project()
    _wait(qtbot, shell)

    assert shell.movie_switcher.count() == 0
    assert shell._movie_locations == {}

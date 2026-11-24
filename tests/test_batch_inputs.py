# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""``tether batch`` takes folders and patterns, has a dry run and names outputs uniquely (#653)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tether import services
from tether.cli import main
from tether.project.batch_inputs import (
    NAME_SEPARATOR,
    MovieArgumentError,
    OutputNameCollision,
    describe_checkpoint,
    expand_movie_arguments,
    movie_suffixes,
    output_stems,
)


def _touch(path: Path, content: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _tree(root: Path) -> list[str]:
    """Every path under ``root``, to show that a command wrote nothing."""
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


@pytest.fixture
def day_folders(tmp_path: Path) -> Path:
    """Two day folders whose movies are numbered the same way, plus files that are not movies."""
    data = tmp_path / "data"
    for day in ("day1", "day2"):
        _touch(data / day / "movie_010.tif")
        _touch(data / day / "movie_011.TIFF")
        _touch(data / day / "movie_010.tdat")
        _touch(data / day / "._movie_010.tif")  # a macOS resource file
    _touch(data / "beads.tif")
    return data


# --- Folders, patterns and files -----------------------------------------------------


def test_a_folder_gives_the_movies_directly_inside_it(day_folders: Path) -> None:
    movies = expand_movie_arguments([str(day_folders / "day1")])
    assert [p.name for p in movies] == ["movie_010.tif", "movie_011.TIFF"]


def test_recursive_adds_the_movies_of_sub_folders(day_folders: Path) -> None:
    assert [p.name for p in expand_movie_arguments([str(day_folders)])] == ["beads.tif"]
    movies = expand_movie_arguments([str(day_folders)], recursive=True)
    assert [p.relative_to(day_folders).as_posix() for p in movies] == [
        "beads.tif",
        "day1/movie_010.tif",
        "day1/movie_011.TIFF",
        "day2/movie_010.tif",
        "day2/movie_011.TIFF",
    ]


def test_a_quoted_pattern_gives_the_same_movies_as_the_folders(day_folders: Path) -> None:
    # Tether expands the pattern itself, so no shell has to: the list does not depend on
    # whether PowerShell, cmd or bash passed the argument.
    by_pattern = expand_movie_arguments([str(day_folders / "day*" / "*.tif")])
    assert [p.relative_to(day_folders).as_posix() for p in by_pattern] == [
        "day1/movie_010.tif",
        "day2/movie_010.tif",
    ]
    by_folders = expand_movie_arguments([str(day_folders / "day*")])
    assert by_folders == expand_movie_arguments(
        [str(day_folders / "day1"), str(day_folders / "day2")]
    )
    anywhere = expand_movie_arguments([str(day_folders / "**" / "movie_010.tif")])
    assert [p.relative_to(day_folders).as_posix() for p in anywhere] == [
        "day1/movie_010.tif",
        "day2/movie_010.tif",
    ]


def test_a_relative_pattern_is_expanded_from_the_current_folder(
    day_folders: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(day_folders)
    movies = expand_movie_arguments(["day2/*.tif"])
    assert [p.as_posix() for p in movies] == ["day2/movie_010.tif"]


def test_the_movie_suffixes_are_those_of_the_registered_readers(tmp_path, monkeypatch) -> None:
    # Item 1 of #653: "The accepted extensions are the ones the reader supports."
    from tether.io import adapters  # noqa: PLC0415

    assert movie_suffixes() == (".tif", ".tiff", ".czi")
    extra = SimpleNamespace(name="Other", extensions=(".nd2",))
    real = adapters.registered_adapters()
    monkeypatch.setattr(adapters, "registered_adapters", lambda: (*real, extra))
    _touch(tmp_path / "a.nd2")
    _touch(tmp_path / "b.tif")
    assert [p.name for p in expand_movie_arguments([str(tmp_path)])] == ["a.nd2", "b.tif"]


def test_a_file_named_on_its_own_is_kept_whatever_its_name(tmp_path: Path) -> None:
    odd = _touch(tmp_path / "run[1].dat")  # glob characters, and not a movie suffix
    assert expand_movie_arguments([str(odd)]) == (odd,)


def test_the_list_is_sorted_and_names_each_movie_once(day_folders: Path) -> None:
    day1 = day_folders / "day1"
    movies = expand_movie_arguments(
        [str(day1 / "movie_011.TIFF"), str(day1), str(day1 / "*.tif"), str(day1 / "movie_010.tif")]
    )
    assert [p.name for p in movies] == ["movie_010.tif", "movie_011.TIFF"]


def test_a_path_through_a_link_is_not_taken_for_another_movie(tmp_path: Path) -> None:
    # ``link/../movie.tif`` names the movie beside the link's target, not the one beside
    # the link, so both movies must stay in the list (local CodeRabbit check on #1154).
    beside_link = _touch(tmp_path / "work" / "movie.tif")
    beside_target = _touch(tmp_path / "archive" / "movie.tif")
    (tmp_path / "archive" / "sub").mkdir()
    link = tmp_path / "work" / "link"
    try:
        link.symlink_to(tmp_path / "archive" / "sub", target_is_directory=True)
    except OSError:
        pytest.skip("this machine cannot make a symbolic link")
    through_link = os.path.join(str(link), "..", "movie.tif")
    movies = expand_movie_arguments([str(beside_link), through_link, str(beside_target)])
    assert len(movies) == 2
    assert {os.path.realpath(m) for m in movies} == {
        os.path.realpath(beside_link),
        os.path.realpath(beside_target),
    }


def test_a_folder_that_cannot_be_listed_is_refused_by_name(tmp_path: Path, monkeypatch) -> None:
    folder = tmp_path / "locked"
    folder.mkdir()

    def _denied(self):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "iterdir", _denied)
    with pytest.raises(MovieArgumentError, match=r"cannot read movie argument .*locked"):
        expand_movie_arguments([str(folder)])


def test_a_sub_folder_that_cannot_be_listed_is_refused_under_recursive(
    tmp_path: Path, monkeypatch
) -> None:
    """An unreadable sub-folder is a named refusal, not a movie list with a folder missing."""
    folder = tmp_path / "data"
    (folder / "day1").mkdir(parents=True)
    (folder / "day2").mkdir()
    (folder / "day1" / "movie_010.tif").write_bytes(b"")
    (folder / "day2" / "movie_010.tif").write_bytes(b"")
    locked = folder / "day2"
    real_scandir = os.scandir

    def _scandir(path=".", *args, **kwargs):
        if os.path.abspath(os.fspath(path)) == os.path.abspath(locked):
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return real_scandir(path, *args, **kwargs)

    monkeypatch.setattr(os, "scandir", _scandir)
    with pytest.raises(MovieArgumentError, match=r"cannot read movie argument .*data.*day2"):
        expand_movie_arguments([str(folder)], recursive=True)


@pytest.mark.parametrize("pattern", ["data/**/*.tif", "data/day*/*.tif", "data/*/movie_010.tif"])
def test_a_folder_a_pattern_cannot_list_is_refused(
    tmp_path: Path, monkeypatch, pattern: str
) -> None:
    """``glob`` drops a folder it cannot list; the pattern is refused rather than cut short."""
    folder = tmp_path / "data"
    (folder / "day1").mkdir(parents=True)
    (folder / "day2").mkdir()
    (folder / "day1" / "movie_010.tif").write_bytes(b"")
    (folder / "day2" / "movie_010.tif").write_bytes(b"")
    locked = folder / "day2"
    real_scandir = os.scandir

    def _scandir(path=".", *args, **kwargs):
        if os.path.abspath(os.fspath(path)) == os.path.abspath(locked):
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return real_scandir(path, *args, **kwargs)

    real_lstat = os.lstat

    def _lstat(path, *args, **kwargs):
        # A folder without search permission: nothing inside it can be looked up either.
        if os.path.dirname(os.path.abspath(os.fspath(path))) == os.path.abspath(locked):
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return real_lstat(path, *args, **kwargs)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(os, "scandir", _scandir)
    monkeypatch.setattr(os, "lstat", _lstat)
    with pytest.raises(MovieArgumentError, match=r"cannot read movie argument .*day2"):
        expand_movie_arguments([pattern])


def test_a_pattern_refuses_a_linked_folder_it_cannot_list(tmp_path: Path, monkeypatch) -> None:
    """``**`` follows a link to a folder, as ``glob`` does, so an unlistable one is refused."""
    folder = tmp_path / "data"
    (folder / "day1").mkdir(parents=True)
    (tmp_path / "outside").mkdir()
    (folder / "day1" / "movie_010.tif").write_bytes(b"")
    (tmp_path / "outside" / "movie_010.tif").write_bytes(b"")
    link = folder / "alias"
    try:
        link.symlink_to(tmp_path / "outside", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this system cannot make a link to a folder")
    real_scandir = os.scandir

    def _scandir(path=".", *args, **kwargs):
        if os.path.abspath(os.fspath(path)) == os.path.abspath(link):
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return real_scandir(path, *args, **kwargs)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(os, "scandir", _scandir)
    with pytest.raises(MovieArgumentError, match=r"cannot read movie argument .*alias"):
        expand_movie_arguments(["data/**/*.tif"])


def test_a_pattern_skips_a_hidden_folder_it_cannot_list(tmp_path: Path, monkeypatch) -> None:
    """A hidden folder is not one a pattern looks in, so it cannot stop the batch."""
    folder = tmp_path / "data"
    (folder / "day1").mkdir(parents=True)
    (folder / ".trash").mkdir()
    (folder / "day1" / "movie_010.tif").write_bytes(b"")
    locked = folder / ".trash"
    real_scandir = os.scandir

    def _scandir(path=".", *args, **kwargs):
        if os.path.abspath(os.fspath(path)) == os.path.abspath(locked):
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return real_scandir(path, *args, **kwargs)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(os, "scandir", _scandir)
    assert expand_movie_arguments(["data/**/*.tif"]) == (Path("data/day1/movie_010.tif"),)


@pytest.mark.parametrize(
    ("argument", "message"),
    [
        ("missing.tif", "no such movie file or folder: {arg}"),
        ("empty", "folder {arg} holds no movie file (.tif, .tiff, .czi); add --recursive"),
        ("*.nd2", "pattern {arg} matches no movie file (.tif, .tiff, .czi)"),
    ],
)
def test_an_argument_that_gives_no_movie_is_refused_by_name(
    tmp_path: Path, argument: str, message: str
) -> None:
    (tmp_path / "empty").mkdir()
    _touch(tmp_path / "other.nd2")  # a pattern needs a movie suffix as well as a match
    arg = str(tmp_path / argument)
    with pytest.raises(MovieArgumentError) as excinfo:
        expand_movie_arguments([arg])
    assert str(excinfo.value).startswith(message.format(arg=repr(arg)))


# --- Output names ----------------------------------------------------------------------


def test_names_without_a_collision_are_the_file_stems() -> None:
    movies = [Path("/a/movie_010.tif"), Path("/b/movie_011.tif"), Path("/c/beads.tiff")]
    assert output_stems(movies) == ("movie_010", "movie_011", "beads")


def test_equal_file_names_take_their_parent_folder() -> None:
    movies = [Path("/data/day1/movie_010.tif"), Path("/data/day2/movie_010.tif")]
    assert output_stems(movies) == ("day1__movie_010", "day2__movie_010")
    assert NAME_SEPARATOR == "__"


def test_folders_are_added_until_the_names_differ() -> None:
    movies = [
        Path("/data/day1/cond/movie_010.tif"),
        Path("/data/day2/cond/movie_010.tif"),
        Path("/data/day2/cond/movie_011.tif"),
    ]
    assert output_stems(movies) == (
        "day1__cond__movie_010",
        "day2__cond__movie_010",
        "movie_011",
    )


def test_a_name_made_by_the_rule_that_meets_another_name_grows_again() -> None:
    movies = [Path("/x/b__m.tif"), Path("/y/b/m.tif"), Path("/z/c/m.tif")]
    names = output_stems(movies)
    assert len({n.casefold() for n in names}) == 3
    assert names == ("x__b__m", "y__b__m", "c__m")


def test_names_that_differ_only_in_case_are_one_name() -> None:
    assert output_stems([Path("/d1/Movie.tif"), Path("/d2/movie.tif")]) == (
        "d1__Movie",
        "d2__movie",
    )
    with pytest.raises(OutputNameCollision, match="differ only in case"):
        output_stems([Path("/d/A.tif"), Path("/d/a.tif")])


def test_names_do_not_depend_on_the_current_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    absolute = [tmp_path / "day1" / "m.tif", tmp_path / "day2" / "m.tif"]
    monkeypatch.chdir(tmp_path)
    relative = [Path("day1/m.tif"), Path("day2/m.tif")]
    assert output_stems(relative) == output_stems(absolute) == ("day1__m", "day2__m")


def _link_to(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("this machine cannot make a symbolic link")


def test_a_link_followed_by_dotdot_takes_the_real_parent_folders(tmp_path: Path) -> None:
    # ``work/link/../m.tif`` with ``work/link`` -> ``archive/sub`` is ``archive/m.tif``:
    # read as text it would take ``work``'s folders and collide with ``work/m.tif`` (#1215).
    beside_link = _touch(tmp_path / "work" / "m.tif")
    _touch(tmp_path / "archive" / "m.tif")
    (tmp_path / "archive" / "sub").mkdir()
    _link_to(tmp_path / "work" / "link", tmp_path / "archive" / "sub")
    through_link = os.path.join(str(tmp_path / "work" / "link"), "..", "m.tif")
    movies = expand_movie_arguments([str(beside_link), through_link])
    if os.path.samefile(through_link, beside_link):
        # Windows removes ``..`` as text before it follows a link, so there the path is
        # the movie beside the link: one movie, with its own name.
        assert output_stems(movies) == ("m",)
        return
    assert len(movies) == 2
    by_movie = dict(zip((os.path.realpath(m) for m in movies), output_stems(movies), strict=True))
    assert by_movie[os.path.realpath(tmp_path / "archive" / "m.tif")] == "archive__m"
    assert by_movie[os.path.realpath(beside_link)] == "work__m"


def test_a_path_through_a_link_without_dotdot_keeps_the_folders_it_names(
    tmp_path: Path,
) -> None:
    # The path names the movie as typed, so the names stay those of the typed folders.
    _touch(tmp_path / "real" / "day1" / "m.tif")
    _touch(tmp_path / "day2" / "m.tif")
    _link_to(tmp_path / "alias", tmp_path / "real" / "day1")
    movies = [tmp_path / "alias" / "m.tif", tmp_path / "day2" / "m.tif"]
    assert output_stems(movies) == ("alias__m", "day2__m")


# --- The command line --------------------------------------------------------------------


def _capture_run_batch(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    import tether.project.batch as batch_mod  # noqa: PLC0415

    captured: dict[str, object] = {}

    def _fake(jobs, **kwargs):
        captured["jobs"] = list(jobs)
        return batch_mod.BatchSummary(results=[], policy="warn", idealize_requested=False)

    monkeypatch.setattr(batch_mod, "run_batch", _fake)
    return captured


def test_tether_batch_takes_a_folder(day_folders: Path, monkeypatch, tmp_path) -> None:
    captured = _capture_run_batch(monkeypatch)
    out = tmp_path / "out"
    rc = main(["batch", str(day_folders), "--recursive", "-d", str(out), "--no-idealize"])
    assert rc == 0
    jobs = captured["jobs"]
    assert [
        (j.movie_path.relative_to(day_folders).as_posix(), j.output_path.name) for j in jobs
    ] == [
        ("beads.tif", "beads.tether"),
        ("day1/movie_010.tif", "day1__movie_010.tether"),
        ("day1/movie_011.TIFF", "day1__movie_011.tether"),
        ("day2/movie_010.tif", "day2__movie_010.tether"),
        ("day2/movie_011.TIFF", "day2__movie_011.tether"),
    ]
    # The log records each movie and its output before the run starts.
    records = [json.loads(line) for line in (out / "batch-log.jsonl").read_text().splitlines()]
    assert [(r["movie"], r["stage"], r["status"]) for r in records][:2] == [
        ("beads", "input", "mapped"),
        ("day1__movie_010", "input", "mapped"),
    ]
    assert records[1]["detail"] == f"{jobs[1].movie_path} -> {jobs[1].output_path}"


def test_a_refused_argument_exits_2_and_writes_nothing(tmp_path: Path, capsys) -> None:
    out = tmp_path / "out"
    rc = main(["batch", str(tmp_path / "*.tif"), "-d", str(out)])
    assert rc == 2
    assert "matches no movie file" in capsys.readouterr().err
    assert not out.exists()


def test_dry_run_prints_one_line_for_each_movie_and_writes_nothing(
    day_folders: Path, tmp_path: Path, monkeypatch, capsys
) -> None:
    import tether.io.movie as movie_mod  # noqa: PLC0415
    import tether.project.batch as batch_mod  # noqa: PLC0415

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("a dry run read a movie or ran the batch")

    monkeypatch.setattr(batch_mod, "run_batch", _forbidden)
    monkeypatch.setattr(movie_mod, "open_movie", _forbidden)
    monkeypatch.setattr(movie_mod.MovieReader, "__init__", _forbidden)
    out = tmp_path / "out"
    before = _tree(tmp_path)

    rc = main(["batch", str(day_folders / "day*"), "-d", str(out), "--dry-run", "--no-idealize"])

    assert rc == 0
    assert _tree(tmp_path) == before
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("Batch plan (dry run): 4 movie(s) into ")
    movie_lines = lines[1:5]
    assert movie_lines == [
        f"  {day_folders / d / m} -> {out / o}  [new]"
        for d, m, o in (
            ("day1", "movie_010.tif", "day1__movie_010.tether"),
            ("day1", "movie_011.TIFF", "day1__movie_011.tether"),
            ("day2", "movie_010.tif", "day2__movie_010.tether"),
            ("day2", "movie_011.TIFF", "day2__movie_011.tether"),
        )
    ]
    assert lines[5].startswith("Settings: policy=warn, idealize=off, overwrite=off")
    assert lines[6] == f"Structured log: {out / 'batch-log.jsonl'}"
    assert lines[7] == "Sidecar: not needed (idealization not requested)"


def test_dry_run_refuses_what_the_run_refuses(tmp_path: Path, capsys) -> None:
    movie = _touch(tmp_path / "m.tif")
    rc = main(
        ["batch", str(movie), "-d", str(tmp_path / "out"), "--dry-run", "--max-restarts", "-1"]
    )
    assert rc == 2
    assert capsys.readouterr().err.startswith("tether batch: ")
    assert not (tmp_path / "out").exists()


def test_a_profile_layout_beside_tmap_is_refused_before_the_output_folder_exists(
    tmp_path: Path, capsys
) -> None:
    # Extraction refuses this pair for every movie (#1218), so the batch refuses it first.
    from tether.services.profile import ExtractionProfile, write_profile  # noqa: PLC0415

    movie = str(_touch(tmp_path / "m.tif"))
    out = tmp_path / "out"
    request = services.BatchRequest(
        movies=(movie,), out_dir=str(out), tmap="m.tmap", channel_layout="top-bottom"
    )
    for command in (services.batch.plan, services.batch.run):
        with pytest.raises(services.RequestRefused, match="channel_layout and --tmap"):
            command(request)
    assert not out.exists()

    profile = tmp_path / "p.json"
    write_profile(ExtractionProfile(channel_layout="top-bottom"), profile)
    argv = ["batch", movie, "-d", str(out), "--profile", str(profile), "--tmap", "m.tmap"]
    assert main([*argv, "--dry-run"]) == 2
    assert "channel_layout and --tmap" in capsys.readouterr().err
    assert not out.exists()


@pytest.mark.parametrize("extra", [{"tmap": "m.tmap"}, {"channel_layout": "top-bottom"}])
def test_a_layout_alone_or_a_tmap_alone_is_planned(tmp_path: Path, extra: dict) -> None:
    movie = str(_touch(tmp_path / "m.tif"))
    request = services.BatchRequest(
        movies=(movie,), out_dir=str(tmp_path / "out"), idealize=False, **extra
    )
    assert len(services.batch.plan(request).movies) == 1


def test_dry_run_settings_name_the_extraction_options(tmp_path: Path) -> None:
    movie = str(_touch(tmp_path / "m.tif"))

    def settings(**extra: object) -> str:
        request = services.BatchRequest(
            movies=(movie,), out_dir=str(tmp_path / "out"), idealize=False, **extra
        )
        return services.batch.plan(request).settings

    assert settings().endswith(
        ", channel-layout=none, donor-side=left, detection-mode=wavelet, "
        "detection-threshold=mode default"
    )
    assert "donor-side=right" in settings(donor_side="right")
    assert settings(donor_side="right") != settings()
    bandpass = settings(detection_mode="bandpass", detection_threshold=0.9)
    assert "detection-mode=bandpass, detection-threshold=0.9" in bandpass
    layout = settings(channel_layout="top-bottom")
    assert "channel-layout=top-bottom, donor=top," in layout and "donor-side" not in layout
    assert "donor=bottom," in settings(channel_layout="top-bottom", channel_layout_donor="bottom")
    rects = settings(
        channel_layout="rectangles",
        channel_layout_donor_rect=(1, 1, 10, 20),
        channel_layout_acceptor_rect=(11, 1, 20, 20),
    )
    assert "donor-rect=[1 1 10 20], acceptor-rect=[11 1 20 20]," in rects
    tmap = settings(tmap="m.tmap", donor_side="right")
    assert "channel-layout=from .tmap, detection-mode=" in tmap and "donor" not in tmap
    assert settings(tdat="run.tdat").endswith(
        "detection-mode=from .tdat, detection-threshold=from .tdat"
    )


def test_dry_run_says_whether_the_sidecar_was_found(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("TETHER_SIDECAR_PYTHON", raising=False)
    import tether.idealize.driver as driver  # noqa: PLC0415

    monkeypatch.setattr(driver, "bundled_sidecar_python", lambda: None)
    movie = str(_touch(tmp_path / "m.tif"))
    out = str(tmp_path / "out")
    found = _touch(tmp_path / "py")

    def sidecar(**extra: object) -> str:
        request = services.BatchRequest(movies=(movie,), out_dir=out, **extra)
        return services.batch.plan(request).sidecar

    assert sidecar(sidecar_python=str(found)).startswith(f"found at {found}")
    assert sidecar().endswith("; idealization would be deferred")
    assert sidecar(defer_if_unavailable=False).endswith(
        "; each movie's idealize stage would fail (--no-defer)"
    )


def test_dry_run_reads_each_existing_output_checkpoint(tmp_path: Path) -> None:
    h5py = pytest.importorskip("h5py")
    out = tmp_path / "out"
    out.mkdir()
    junk = _touch(out / "junk.tether", b"not hdf5")
    partial, complete = out / "partial.tether", out / "complete.tether"
    with h5py.File(partial, "w") as f:
        f.create_group("settings/extraction")
    with h5py.File(complete, "w") as f:
        f.create_group("settings/extraction")
        f.create_group("settings/correction")
    common = {"policy": "warn", "idealize": False}

    assert describe_checkpoint(out / "absent.tether", overwrite=False, **common) == "new"
    assert describe_checkpoint(junk, overwrite=False, **common) == (
        "exists, incomplete: will fail; pass --overwrite to replace it"
    )
    assert describe_checkpoint(junk, overwrite=True, **common).endswith(
        "will be overwritten (--overwrite)"
    )
    assert (
        describe_checkpoint(partial, overwrite=False, **common) == "exists: will resume at correct"
    )
    assert (
        describe_checkpoint(complete, overwrite=False, **common)
        == "exists, complete: will be skipped"
    )
    assert describe_checkpoint(complete, overwrite=False, policy="warn", idealize=True) == (
        "exists: will resume at idealize"
    )
    # Reading a checkpoint changes nothing in it.
    with h5py.File(complete, "r") as f:
        assert sorted(f["settings"]) == ["correction", "extraction"]


# --- A real run ----------------------------------------------------------------------------


def test_two_day_folders_with_equal_names_run_and_resume(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # The acceptance criteria end to end: one batch over two day folders whose movies share
    # file names gives each movie its own project, and a second run of the same command
    # recognizes every finished movie from its checkpoint.
    for module in ("h5py", "scipy", "skimage", "tifffile"):
        pytest.importorskip(module)
    import test_extract_cli as helpers  # noqa: PLC0415

    data = tmp_path / "data"
    for day in ("day1", "day2"):
        (data / day).mkdir(parents=True)
        helpers._make_movie(data / day, name="movie_010.tif")
    out = tmp_path / "out"
    argv = ["batch", str(data), "--recursive", "-d", str(out), "--no-idealize"]

    assert main(argv) == 0, capsys.readouterr()
    first = capsys.readouterr().out
    assert "day1__movie_010" in first and "day2__movie_010" in first
    assert sorted(p.name for p in out.glob("*.tether")) == [
        "day1__movie_010.tether",
        "day2__movie_010.tether",
    ]

    assert main(argv) == 0
    second = capsys.readouterr().out
    assert second.count("extract=skipped correct=skipped") == 2, second
    assert main([*argv, "--dry-run"]) == 0
    assert capsys.readouterr().out.count("[exists, complete: will be skipped]") == 2

# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""``tether batch`` takes folders and patterns, has a dry run and names outputs uniquely (#653)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tether import services
from tether.cli import main
from tether.project.batch_inputs import (
    NAME_SEPARATOR,
    MovieArgumentError,
    OutputNameCollision,
    describe_checkpoint,
    expand_movie_arguments,
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


def test_a_file_named_on_its_own_is_kept_whatever_its_name(tmp_path: Path) -> None:
    odd = _touch(tmp_path / "run[1].dat")  # glob characters, and not a movie suffix
    assert expand_movie_arguments([str(odd)]) == (odd,)


def test_the_list_is_sorted_and_names_each_movie_once(day_folders: Path) -> None:
    day1 = day_folders / "day1"
    movies = expand_movie_arguments(
        [str(day1 / "movie_011.TIFF"), str(day1), str(day1 / "*.tif"), str(day1 / "movie_010.tif")]
    )
    assert [p.name for p in movies] == ["movie_010.tif", "movie_011.TIFF"]


@pytest.mark.parametrize(
    ("argument", "message"),
    [
        ("missing.tif", "no such movie file or folder: '{arg}'"),
        ("empty", "folder '{arg}' holds no movie file (.tif, .tiff); add --recursive"),
        ("*.nd2", "pattern '{arg}' matches no movie file (.tif, .tiff)"),
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
    assert str(excinfo.value).startswith(message.format(arg=arg))


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

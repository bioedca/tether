# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The aperture and background review before the run (#922).

The review marks spots at an edge and spots that overlap a neighbour with the present guard
rules, its preview trace of a molecule equals the trace the full run gives that molecule, it
writes nothing to a project, and its report is the same file whoever writes it.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("skimage")
pytest.importorskip("h5py")
pytest.importorskip("tifffile")
pytest.importorskip("matplotlib")

import numpy as np  # noqa: E402
import tifffile  # noqa: E402

import tether.project.extract as ext  # noqa: E402
from tether.imaging import read_traces  # noqa: E402
from tether.project.extract import ExtractOptions  # noqa: E402
from tether.services.apertures import (  # noqa: E402
    REPORT_FORMAT,
    preview_trace,
    report_dict,
    review_movie,
    write_report,
)

_BG, _AMP, _SIGMA = 80.0, 400.0, 1.5
_SHAPE = (64, 96)  # two 48-px halves, donor left
_N_FRAMES = 12
#: Spot 0 is at the left edge (its 21-px crop leaves the frame); spots 3 and 4 are 9 px apart.
_DONOR = np.array([[4.0, 30.0], [12.0, 12.0], [24.0, 50.0], [34.0, 20.0], [34.0, 29.0]])
_ACCEPTOR = _DONOR + [49.0, 0.0]  # the acceptor half, +1 px in its half
#: A disk of 5 px makes two disks 9 px apart overlap (2 x 5 > 9); the ring still fits.
_OPTIONS = ExtractOptions(disk_radius=5.0, ring_inner=6.0, ring_outer=8.0)


def _movie(path):
    rows, cols = np.mgrid[0 : _SHAPE[0], 0 : _SHAPE[1]]
    frames = []
    for t in range(_N_FRAMES):
        frame = np.full(_SHAPE, _BG)
        for i, (x, y) in enumerate(np.vstack([_DONOR, _ACCEPTOR])):
            amp = _AMP * (1.0 + 0.05 * ((t + i) % 3))  # traces that change over time
            frame += amp * np.exp(-((rows - y) ** 2 + (cols - x) ** 2) / (2.0 * _SIGMA**2))
        frames.append(frame)
    tifffile.imwrite(path, np.asarray(frames, dtype="<u2"), photometric="minisblack")
    return path


@pytest.fixture
def movie(tmp_path):
    return _movie(tmp_path / "Bla_010.tif")


def test_spots_at_an_edge_and_overlapping_spots_are_marked_and_counted(movie) -> None:
    review, frame = review_movie(movie, _OPTIONS)
    assert frame.shape == _SHAPE
    assert review.n_spots == 5
    by_x = {round(s.donor_xy[0]): s for s in review.spots}
    assert by_x[4].at_edge and by_x[4].molecule is None and by_x[4].status == "at-edge"
    assert by_x[34].overlaps and by_x[34].status == "overlap"
    assert by_x[12].status == "ok" and by_x[24].status == "ok"
    assert (review.n_edge, review.n_overlap, review.n_molecules) == (1, 2, 4)
    assert review.overlap_distance == 10.0
    assert review.summary() == "5 donor spots: 4 molecules, 1 at an edge, 2 overlap a neighbour"
    # The acceptor aperture sits where the registration maps the donor: in the right half.
    assert all(s.acceptor_xy[0] > _SHAPE[1] / 2 for s in review.spots)


def test_default_disk_marks_no_overlap(movie) -> None:
    review, _ = review_movie(movie, ExtractOptions())
    assert review.n_overlap == 0 and review.n_edge == 1


def test_preview_trace_equals_the_trace_of_the_full_run(tmp_path, movie, monkeypatch) -> None:
    seen = {}
    real = ext.extract_molecules

    def spy(*args, **kwargs):
        seen["traces"] = real(*args, **kwargs)
        return seen["traces"]

    monkeypatch.setattr(ext, "extract_molecules", spy)
    out = tmp_path / "run.tether"
    ext.extract_movie(movie, out, options=_OPTIONS)
    monkeypatch.undo()
    review, _ = review_movie(movie, _OPTIONS)
    stored = read_traces(out)
    run = seen["traces"]
    assert run.n_molecules == review.n_molecules
    for molecule in range(review.n_molecules):
        trace = preview_trace(review, molecule)
        for channel in ("donor", "acceptor"):
            want = getattr(run, channel).intensity[molecule]
            assert trace[channel].dtype == want.dtype
            assert trace[channel].tobytes() == want.tobytes()
            np.testing.assert_array_equal(
                stored[f"{channel}_corrected"][molecule], trace[channel].astype("<f4")
            )
    with pytest.raises(IndexError):
        preview_trace(review, review.n_molecules)


def test_the_review_writes_nothing(tmp_path, movie) -> None:
    before = sorted(p.name for p in tmp_path.iterdir())
    review_movie(movie, _OPTIONS)
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_report_carries_the_counts_the_spots_and_the_selected_traces(tmp_path, movie) -> None:
    review, frame = review_movie(movie, _OPTIONS)
    json_path, png_path = tmp_path / "r.json", tmp_path / "r.png"
    written = write_report(review, frame, json_path, png_path, molecules=[1, 0, 1])
    assert written == [json_path, png_path]
    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert report["format"] == REPORT_FORMAT and report["version"] == 1
    assert report["counts"] == {"spots": 5, "molecules": 4, "at_edge": 1, "overlap": 2}
    assert report["aperture"]["disk_radius"] == 5.0
    assert [t["molecule"] for t in report["traces"]] == [0, 1]
    assert report["traces"][0]["donor"] == [float(v) for v in preview_trace(review, 0)["donor"]]
    assert {s["status"] for s in report["spots"]} == {"ok", "overlap", "at-edge"}
    assert report == report_dict(review, [0, 1])
    assert png_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    # The same arguments write the same files.
    again_json, again_png = tmp_path / "s.json", tmp_path / "s.png"
    write_report(review, frame, again_json, again_png, molecules=[0, 1])
    assert again_json.read_bytes() == json_path.read_bytes()
    assert again_png.read_bytes() == png_path.read_bytes()


def test_a_preview_that_stops_early_is_refused_with_its_reason(tmp_path) -> None:
    path = tmp_path / "empty.tif"
    tifffile.imwrite(path, np.full((4, *_SHAPE), 80, dtype="<u2"), photometric="minisblack")
    with pytest.raises(ValueError, match="the preview stopped at"):
        review_movie(path, ExtractOptions())


# --- the command line -------------------------------------------------------------------


def _cli(argv):
    import argparse

    from tether.cli_apertures import add_apertures_parser, run_apertures

    parser = argparse.ArgumentParser(prog="tether")
    add_apertures_parser(parser.add_subparsers(dest="command"))
    return run_apertures(parser.parse_args(["apertures", *argv]))


def test_the_command_line_writes_the_same_report(tmp_path, movie, capsys) -> None:
    review, frame = review_movie(movie, _OPTIONS)
    write_report(review, frame, tmp_path / "core.json", tmp_path / "core.png", molecules=[2])
    argv = [str(movie), "--json", str(tmp_path / "cli.json"), "--png", str(tmp_path / "cli.png")]
    assert _cli([*argv, "--disk-radius", "5", "--molecule", "2"]) == 0
    assert (tmp_path / "cli.json").read_bytes() == (tmp_path / "core.json").read_bytes()
    assert (tmp_path / "cli.png").read_bytes() == (tmp_path / "core.png").read_bytes()
    assert review.summary() in capsys.readouterr().out
    # An existing output is refused without --overwrite; with it, it is replaced.
    assert _cli([*argv, "--disk-radius", "5"]) == 2
    assert _cli([*argv, "--disk-radius", "5", "--overwrite"]) == 0
    assert json.loads((tmp_path / "cli.json").read_text())["traces"] == []


def test_the_command_line_reads_a_profile_and_refuses_bad_options(tmp_path, movie) -> None:
    from tether.services.profile import ExtractionProfile, write_profile

    profile = tmp_path / "profile.json"
    write_profile(ExtractionProfile.from_options(_OPTIONS), profile)
    out = tmp_path / "p.json"
    assert _cli([str(movie), "--json", str(out), "--profile", str(profile)]) == 0
    assert json.loads(out.read_text())["counts"]["overlap"] == 2
    assert _cli([str(movie), "--json", str(tmp_path / "bad.json"), "--window", "20"]) == 2
    assert not (tmp_path / "bad.json").exists()
    assert _cli([str(movie), "--json", str(tmp_path / "m.json"), "--molecule", "99"]) == 1


def test_tether_apertures_is_a_subcommand(tmp_path, movie) -> None:
    from tether.cli import main

    out = tmp_path / "main.json"
    assert main(["apertures", str(movie), "--json", str(out), "--disk-radius", "5"]) == 0
    assert json.loads(out.read_text())["counts"]["at_edge"] == 1


@pytest.mark.parametrize("alias", ["same", "symlink", "hardlink"])
def test_an_output_that_names_an_input_is_refused_even_with_overwrite(
    tmp_path, movie, alias
) -> None:
    import os

    before = movie.read_bytes()
    target = movie
    if alias == "symlink":
        target = tmp_path / "link.tif"
        target.symlink_to(movie)
    elif alias == "hardlink":
        target = tmp_path / "hard.tif"
        os.link(movie, target)
    for flag in ("--json", "--png"):
        other = "--png" if flag == "--json" else "--json"
        argv = [str(movie), flag, str(target), other, str(tmp_path / f"x{flag}")]
        assert _cli([*argv, "--overwrite"]) == 2
    assert movie.read_bytes() == before


def test_an_output_that_names_the_profile_is_refused(tmp_path, movie) -> None:
    from tether.services.profile import ExtractionProfile, write_profile

    profile = tmp_path / "profile.json"
    write_profile(ExtractionProfile.from_options(_OPTIONS), profile)
    before = profile.read_bytes()
    argv = [str(movie), "--json", str(profile), "--profile", str(profile), "--overwrite"]
    assert _cli(argv) == 2
    assert profile.read_bytes() == before


@pytest.mark.parametrize("flag", ["--map", "--tmap"])
def test_an_output_that_names_the_registration_file_is_refused(tmp_path, movie, flag) -> None:
    # The file is refused before it is read, so its content does not matter.
    registration = tmp_path / ("reg.npz" if flag == "--map" else "reg.tmap")
    registration.write_bytes(b"registration")
    for out in ("--json", "--png"):
        other = "--png" if out == "--json" else "--json"
        argv = [str(movie), out, str(registration), other, str(tmp_path / "x.out")]
        assert _cli([*argv, flag, str(registration), "--overwrite"]) == 2
    assert registration.read_bytes() == b"registration"


@pytest.mark.parametrize("alias", ["same", "symlink", "hardlink"])
@pytest.mark.parametrize("overwrite", [False, True])
def test_json_and_png_naming_one_file_is_refused(tmp_path, movie, alias, overwrite, capsys) -> None:
    import os

    out = tmp_path / "report.out"
    out.write_bytes(b"earlier")
    other = out
    if alias == "symlink":
        other = tmp_path / "link.out"
        other.symlink_to(out)
    elif alias == "hardlink":
        other = tmp_path / "hard.out"
        os.link(out, other)
    argv = [str(movie), "--json", str(out), "--png", str(other)]
    assert _cli(argv + (["--overwrite"] if overwrite else [])) == 2
    assert "are the same file" in capsys.readouterr().err
    assert out.read_bytes() == b"earlier"


def test_the_mean_frame_is_reduced_without_a_float64_copy_of_the_movie(tmp_path) -> None:
    import tracemalloc

    from tether.services.apertures import _mean_frame

    shape = (400, 64, 96)  # a float64 copy would be about 19.7 MB
    data = np.lib.format.open_memmap(tmp_path / "m.npy", mode="w+", dtype="<u2", shape=shape)
    data[:] = np.arange(shape[0], dtype="<u2")[:, None, None]
    data.flush()
    tracemalloc.start()
    frame = _mean_frame(data)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert frame.dtype == np.float32 and frame.shape == shape[1:]
    np.testing.assert_allclose(frame, (shape[0] - 1) / 2)
    assert peak < 4_000_000, peak

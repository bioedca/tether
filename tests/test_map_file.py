# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The native ``.npz`` map file applied at extraction (#353).

``save_map`` -> ``load_map`` -> ``extract_movie(map_path=...)`` must give the same
molecules as the in-memory [`RegistrationMap`][] it was saved from; the precedence of
the three registration sources (native fit, ``.tmap``, map file) is one source per run;
``/calibration`` and ``/settings/extraction`` record which file produced the map; and
the map file carries a ``format_version`` and still reads with ``allow_pickle=False``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("skimage")
pytest.importorskip("h5py")
tifffile = pytest.importorskip("tifffile")

import h5py  # noqa: E402
import numpy as np  # noqa: E402

from tether.cli import build_parser, main  # noqa: E402
from tether.imaging import calibrate  # noqa: E402
from tether.imaging.calibrate import (  # noqa: E402
    MAP_FORMAT_VERSION,
    MapFileError,
    RegistrationMap,
    load_map,
    read_calibration,
    save_map,
)
from tether.imaging.extract import read_molecules, read_traces  # noqa: E402
from tether.project import extract as project_extract  # noqa: E402
from tether.project.extract import (  # noqa: E402
    ExtractionError,
    ExtractOptions,
    _run_pipeline,
    add_movie_to_project,
    extract_movie,
)
from tether.project.lock import publish_lock  # noqa: E402

_BG = 80.0
_AMP = 400.0
_SIGMA = 1.5
_SHAPE = (64, 96)  # two 48-px halves
_N_FRAMES = 12
_WINDOW = 21
_DONOR = np.array([[12.0, 12.0], [24.0, 40.0], [16.0, 52.0]])
_ACCEPTOR = _DONOR + np.array([49.0, 0.0])  # +1 px in x inside the right half


def _write_movie(path: Path, *, acceptor_amp: float = _AMP) -> Path:
    centers = np.vstack([_DONOR, _ACCEPTOR])
    amps = np.concatenate([np.full(len(_DONOR), _AMP), np.full(len(_ACCEPTOR), acceptor_amp)])
    frame = np.full(_SHAPE, _BG)
    rows, cols = np.mgrid[0 : _SHAPE[0], 0 : _SHAPE[1]]
    for (x, y), amp in zip(centers, amps, strict=True):
        frame += amp * np.exp(-((rows - y) ** 2 + (cols - x) ** 2) / (2.0 * _SIGMA**2))
    stack = np.ascontiguousarray(np.broadcast_to(frame, (_N_FRAMES, *_SHAPE)), dtype=">u2")
    tifffile.imwrite(path, stack, photometric="minisblack", byteorder=">")
    return path


def _profile(project: Path) -> dict:
    with h5py.File(project, "r") as f:
        raw = f["/settings/extraction"].attrs["profile_json"]
    return json.loads(raw.decode() if isinstance(raw, bytes) else raw)


def _calibration_provenance(project: Path, calibration_id: str) -> dict:
    with h5py.File(project, "r") as f:
        raw = f[f"/calibration/{calibration_id}"].attrs["provenance_json"]
    return json.loads(raw.decode() if isinstance(raw, bytes) else raw)


@pytest.fixture
def fitted(tmp_path) -> tuple[RegistrationMap, Path]:
    """A map fitted natively on a calibration movie, and that map saved to ``.npz``."""
    calibration_movie = _write_movie(tmp_path / "calibration.tif")
    out = tmp_path / "calibration.tether"
    summary = extract_movie(calibration_movie, out, options=ExtractOptions(window=_WINDOW))
    reg_map = read_calibration(out, summary.calibration_id)
    return reg_map, save_map(reg_map, tmp_path / "channel-map.npz")


def _same_molecules(project: Path, extracted) -> None:
    mols = read_molecules(project)
    traces = read_traces(project)
    assert np.array_equal(mols["donor_xy"], extracted.molecules.donor_xy)
    assert np.array_equal(mols["acceptor_xy"], extracted.molecules.acceptor_xy)
    donor, acceptor = extracted.traces.donor.intensity, extracted.traces.acceptor.intensity
    n = donor.shape[1]
    assert np.array_equal(traces["donor_corrected"][:, :n], donor)
    assert np.array_equal(traces["acceptor_corrected"][:, :n], acceptor)


# --- AC 1: save_map -> load_map -> extract_movie == in-memory map ---------------


def test_a_saved_map_extracts_the_same_molecules_as_the_in_memory_map(tmp_path, fitted) -> None:
    reg_map, map_file = fitted
    later = _write_movie(tmp_path / "later_movie_002.tif", acceptor_amp=250.0)
    options = ExtractOptions(window=_WINDOW)

    in_memory = _run_pipeline(later, options, reg_map)
    out = tmp_path / "later.tether"
    summary = extract_movie(later, out, options=options, map_path=map_file)

    assert summary.n_molecules == len(in_memory.molecules.donor_xy) == 3
    _same_molecules(out, in_memory)
    # The loaded map is the saved one: same coefficients, residual and geometry.
    stored = read_calibration(out, summary.calibration_id)
    for name in ("ref_to_moving", "moving_to_ref"):
        for part in ("a", "b", "norm_xy", "norm_uv"):
            assert np.array_equal(
                getattr(getattr(stored, name), part), getattr(getattr(reg_map, name), part)
            )
    assert stored.rms_residual == reg_map.rms_residual
    assert stored.reference_geometry == reg_map.reference_geometry
    assert stored.moving_geometry == reg_map.moving_geometry


def test_a_map_file_appends_a_later_movie_to_a_project(tmp_path, fitted) -> None:
    _reg_map, map_file = fitted
    first = _write_movie(tmp_path / "first_001.tif")
    project = tmp_path / "experiment.tether"
    extract_movie(first, project, options=ExtractOptions(window=_WINDOW), map_path=map_file)
    later = _write_movie(tmp_path / "later_002.tif", acceptor_amp=250.0)
    with publish_lock(project) as guard:
        summary = add_movie_to_project(
            project,
            later,
            options=ExtractOptions(window=_WINDOW),
            map_path=map_file,
            publish_guard=guard,
        )
    assert summary.registration_source == "map-file"
    assert summary.n_molecules == 3
    assert len(read_molecules(project)) == 6


# --- AC 2: precedence among native fit, .tmap and map file ---------------------


def test_a_map_file_replaces_the_native_fit_and_ignores_donor_side(tmp_path, fitted) -> None:
    reg_map, map_file = fitted
    movie = _write_movie(tmp_path / "movie_003.tif")
    in_memory = _run_pipeline(movie, ExtractOptions(window=_WINDOW), reg_map)
    out = tmp_path / "right.tether"
    # donor_side="right" would swap the halves of a native fit; the map's geometry wins.
    extract_movie(
        movie, out, options=ExtractOptions(window=_WINDOW, donor_side="right"), map_path=map_file
    )
    _same_molecules(out, in_memory)


def test_a_tmap_and_a_map_file_together_are_refused_before_any_work(tmp_path) -> None:
    out = tmp_path / "never.tether"
    with pytest.raises(ExtractionError, match="--tmap and --map cannot be combined"):
        # Neither the movie nor either map exists: the conflict is reported first.
        extract_movie(tmp_path / "m.tif", out, tmap="a.tmap", map_path="b.npz")
    assert not out.exists()


def test_the_cli_refuses_tmap_with_map(tmp_path, capsys) -> None:
    rc = main(["extract", "m.tif", "-o", str(tmp_path / "o.tether"), "--tmap", "a", "--map", "b"])
    assert rc == 1
    assert "--tmap and --map cannot be combined" in capsys.readouterr().err


def test_the_cli_help_states_the_precedence() -> None:
    parser = build_parser()
    sub = next(a for a in parser._actions if a.dest == "command")
    text = " ".join(sub.choices["extract"].format_help().split())
    assert "--map PATH" in text
    assert "the native fit runs only when neither --tmap nor --map is given" in text
    assert "--tdat sets detection only" in text


def test_the_docs_state_the_precedence() -> None:
    page = (Path(__file__).resolve().parents[1] / "docs" / "cli.md").read_text(encoding="utf-8")
    assert "| `--map PATH` |" in page
    assert "### Registration: one map source" in page


def test_the_cli_extracts_with_a_map_file(tmp_path, fitted, capsys) -> None:
    _reg_map, map_file = fitted
    movie = _write_movie(tmp_path / "cli_004.tif")
    out = tmp_path / "cli.tether"
    rc = main(["extract", str(movie), "-o", str(out), "--map", str(map_file)])
    assert rc == 0, capsys.readouterr().err
    assert f"registration: map file {map_file}" in capsys.readouterr().out
    assert _profile(out)["map_source"] == map_file.name


def test_a_missing_or_bad_map_file_is_a_clean_error(tmp_path) -> None:
    movie = _write_movie(tmp_path / "movie_005.tif")
    with pytest.raises(ExtractionError, match="map file not found"):
        extract_movie(movie, tmp_path / "a.tether", map_path=tmp_path / "none.npz")
    junk = tmp_path / "junk.npz"
    junk.write_bytes(b"not a zip archive")
    with pytest.raises(ExtractionError, match="could not use --map junk.npz"):
        extract_movie(movie, tmp_path / "b.tether", map_path=junk)


def test_a_map_without_channel_geometry_is_refused(tmp_path, fitted) -> None:
    from dataclasses import replace

    reg_map, _ = fitted
    bare = save_map(replace(reg_map, reference_geometry=None), tmp_path / "bare.npz")
    movie = _write_movie(tmp_path / "movie_006.tif")
    with pytest.raises(ExtractionError, match="no channel geometry"):
        extract_movie(movie, tmp_path / "c.tether", map_path=bare)


# --- AC 3: provenance records which source produced the map -------------------


def test_calibration_and_settings_record_the_map_file(tmp_path, fitted) -> None:
    reg_map, map_file = fitted
    movie = _write_movie(tmp_path / "movie_007.tif")
    out = tmp_path / "prov.tether"
    summary = extract_movie(movie, out, options=ExtractOptions(window=_WINDOW), map_path=map_file)

    assert summary.registration_source == "map-file"
    provenance = _calibration_provenance(out, summary.calibration_id)
    assert provenance["map_file"] == {
        "name": map_file.name,
        "sha256": hashlib.sha256(map_file.read_bytes()).hexdigest(),
    }
    # The transform's own origin is kept: this map was fitted natively.
    assert read_calibration(out, summary.calibration_id).source == reg_map.source == "native"
    profile = _profile(out)
    assert profile["registration_source"] == "map-file"
    assert profile["map_source"] == map_file.name
    assert profile["tmap_source"] is None


def test_the_recorded_digest_names_the_map_actually_applied(monkeypatch, tmp_path, fitted) -> None:
    """A map file replaced while extraction starts cannot pair two files (Codex P2)."""
    reg_map, map_file = fitted
    first_bytes = map_file.read_bytes()
    real_load_map = project_extract.load_map

    def replace_then_load(path, **kwargs):
        # Another session's ``tether map create --overwrite`` lands here, after the read.
        save_map(replace(reg_map, rms_residual=0.123), tmp_path / "other.npz")
        (tmp_path / "other.npz").replace(map_file)
        return real_load_map(path, **kwargs)

    monkeypatch.setattr(project_extract, "load_map", replace_then_load)
    out = tmp_path / "snapshot.tether"
    summary = extract_movie(
        _write_movie(tmp_path / "movie_009.tif"),
        out,
        options=ExtractOptions(window=_WINDOW),
        map_path=map_file,
    )

    assert map_file.read_bytes() != first_bytes  # the file on disk did change
    digest = _calibration_provenance(out, summary.calibration_id)["map_file"]["sha256"]
    assert digest == hashlib.sha256(first_bytes).hexdigest()
    assert read_calibration(out, summary.calibration_id).rms_residual == reg_map.rms_residual


def test_a_native_fit_records_no_map_file(tmp_path) -> None:
    out = tmp_path / "native.tether"
    summary = extract_movie(
        _write_movie(tmp_path / "movie_008.tif"), out, options=ExtractOptions(window=_WINDOW)
    )
    assert summary.registration_source == "native"
    assert "map_file" not in _calibration_provenance(out, summary.calibration_id)
    assert _profile(out)["map_source"] is None


# --- the map file: format version, allow_pickle=False -------------------------


def test_save_map_writes_the_format_version(fitted) -> None:
    _reg_map, map_file = fitted
    with np.load(map_file, allow_pickle=False) as data:
        assert int(data["format_version"]) == MAP_FORMAT_VERSION == 1


def _rewrite(src: Path, dst: Path, **changes) -> Path:
    with np.load(src, allow_pickle=False) as data:
        arrays = {k: data[k] for k in data.files}
    for key, value in changes.items():
        if value is None:
            arrays.pop(key)
        else:
            arrays[key] = value
    np.savez(dst, **arrays)
    return dst


def test_a_map_file_without_a_version_reads_as_version_zero(tmp_path, fitted) -> None:
    reg_map, map_file = fitted
    legacy = _rewrite(map_file, tmp_path / "legacy.npz", format_version=None)
    loaded = load_map(legacy)
    assert loaded.rms_residual == reg_map.rms_residual
    assert loaded.moving_geometry == reg_map.moving_geometry


def test_a_newer_map_file_version_is_refused(tmp_path, fitted) -> None:
    _reg_map, map_file = fitted
    newer = _rewrite(
        map_file, tmp_path / "newer.npz", format_version=np.array(MAP_FORMAT_VERSION + 1)
    )
    with pytest.raises(MapFileError, match="newer than this Tether reads"):
        load_map(newer)


def test_a_map_file_that_lacks_an_array_is_refused(tmp_path, fitted) -> None:
    _reg_map, map_file = fitted
    broken = _rewrite(map_file, tmp_path / "broken.npz", gate_px=None)
    with pytest.raises(MapFileError, match="lacks \\['gate_px'\\]"):
        load_map(broken)


def test_a_pickled_object_never_enters_the_map(tmp_path, fitted) -> None:
    _reg_map, map_file = fitted
    with np.load(map_file, allow_pickle=False) as data:
        arrays = {k: data[k] for k in data.files}
    arrays["source"] = np.array([{"payload": "pickled"}], dtype=object)
    pickled = tmp_path / "pickled.npz"
    np.savez(pickled, **arrays)  # np.savez pickles an object array
    with pytest.raises(MapFileError, match="not a usable map file"):
        load_map(pickled)


def test_load_map_reads_with_allow_pickle_false(monkeypatch, fitted) -> None:
    _reg_map, map_file = fitted
    seen: list[object] = []
    real_load = np.load

    def spy(*args, **kwargs):
        seen.append(kwargs.get("allow_pickle"))
        return real_load(*args, **kwargs)

    monkeypatch.setattr(calibrate.np, "load", spy)
    load_map(map_file)
    load_map(map_file, content=map_file.read_bytes())  # the bytes a caller already read
    assert seen == [False, False]


# --- tether map create: the service command the GUI action runs ----------------

_BEADS = np.array([[x, y] for x in (8.0, 22.0, 36.0) for y in (10.0, 30.0, 50.0)])


def _write_beads(path: Path) -> Path:
    """One bead image: nine beads in the donor half, each +1 px in x in the acceptor half."""
    centers = np.vstack([_BEADS, _BEADS + np.array([49.0, 0.0])])
    frame = np.full(_SHAPE, _BG)
    rows, cols = np.mgrid[0 : _SHAPE[0], 0 : _SHAPE[1]]
    for x, y in centers:
        frame += _AMP * np.exp(-((rows - y) ** 2 + (cols - x) ** 2) / (2.0 * _SIGMA**2))
    tifffile.imwrite(path, np.ascontiguousarray(frame, dtype=">u2"), byteorder=">")
    return path


def test_map_create_writes_a_map_file_a_later_movie_extracts_with(tmp_path, capsys) -> None:
    beads = _write_beads(tmp_path / "beads.tif")
    map_file = tmp_path / "beads.npz"
    assert main(["map", "create", str(beads), "-o", str(map_file)]) == 0
    printed = capsys.readouterr().out
    assert f"Map -> {map_file}" in printed and "9 bead pair(s)" in printed

    reg_map = load_map(map_file)
    assert reg_map.n_control_points == 9
    assert reg_map.provenance["bead_file"] == "beads.tif"
    movie = _write_movie(tmp_path / "later_009.tif")
    out = tmp_path / "later.tether"
    summary = extract_movie(movie, out, options=ExtractOptions(window=_WINDOW), map_path=map_file)
    assert summary.n_molecules == 3
    assert summary.registration_source == "map-file"


def test_map_create_refuses_an_existing_output_and_a_bad_name(tmp_path, capsys) -> None:
    beads = _write_beads(tmp_path / "beads.tif")
    existing = tmp_path / "taken.npz"
    existing.write_bytes(b"keep")
    assert main(["map", "create", str(beads), "-o", str(existing)]) == 1
    assert "output exists" in capsys.readouterr().err
    assert existing.read_bytes() == b"keep"
    assert main(["map", "create", str(beads), "-o", str(tmp_path / "m.dat")]) == 1
    assert "must end in .npz" in capsys.readouterr().err
    assert main(["map", "create", str(beads), "-o", str(existing), "--overwrite"]) == 0
    assert load_map(existing).n_control_points == 9


def test_a_failed_map_leaves_no_file(tmp_path, capsys) -> None:
    blank = tmp_path / "blank.tif"
    tifffile.imwrite(blank, np.full(_SHAPE, 80, dtype=">u2"), byteorder=">")
    out = tmp_path / "blank.npz"
    assert main(["map", "create", str(blank), "-o", str(out)]) == 1
    assert "bead" in capsys.readouterr().err
    assert list(tmp_path.glob("*.npz")) == []


def test_map_create_takes_a_frame_range_of_a_video(tmp_path) -> None:
    from tether.services import MapCreateRequest, run

    still = tifffile.imread(_write_beads(tmp_path / "still.tif"))
    video = tmp_path / "beads-video.tif"
    tifffile.imwrite(
        video, np.ascontiguousarray(np.stack([still] * 4), dtype=">u2"), photometric="minisblack"
    )
    result = run(
        MapCreateRequest(
            source=str(video), output=str(tmp_path / "v.npz"), frame_start=1, frame_stop=3
        )
    )
    source = load_map(result.path).provenance["bead_source"]
    assert (source["frame_start"], source["frame_stop"], source["projection"]) == (1, 3, "max")
    with pytest.raises(Exception, match="pass both or neither"):
        run(MapCreateRequest(source=str(video), output=str(tmp_path / "w.npz"), frame_start=1))


def test_a_channel_layout_beside_a_map_file_is_refused(tmp_path, fitted) -> None:
    from tether.imaging.layout import ChannelLayout

    _reg_map, map_file = fitted
    movie = _write_movie(tmp_path / "movie_010.tif")
    layout = ChannelLayout(kind="top-bottom")
    with pytest.raises(ExtractionError, match="channel_layout and map_path"):
        extract_movie(
            movie,
            tmp_path / "d.tether",
            options=ExtractOptions(window=_WINDOW, channel_layout=layout),
            map_path=map_file,
        )

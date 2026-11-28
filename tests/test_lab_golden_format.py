# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The laboratory golden files hold summary numbers under neutral ids, and nothing else (#703).

The acquisitions behind ``tests/golden/workstation/`` are private and run only on the
workstation (``tests/test_lab_golden.py``). This module runs on the required matrix and keeps
the committed files safe and usable: one file for each data acquisition, each in the layout of
``_lab_golden.validate_document``, and no string in any of them that is not a neutral id. It
also runs the summary pipeline once on the synthetic movie of #701, so a change that breaks
the workstation check fails here first.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

import _lab_golden

_REPO = Path(__file__).resolve().parents[1]
_UPDATE = _REPO / "scripts" / "workstation" / "update_lab_golden.py"


def _refuse_constant(name: str) -> None:
    pytest.fail(f"non-standard JSON constant {name} in a laboratory golden file")


def _good_quantities() -> dict:
    values = {"int": 3, "float": 1.5, "int-list": [0, 2, 1], "float-list": [0.25, 0.75]}
    return {k: values[kind] for k, kind in sorted(_lab_golden.QUANTITIES.items())}


# --- the committed files ------------------------------------------------------------------


def test_there_is_one_golden_file_for_each_data_acquisition() -> None:
    files = sorted(p.name for p in _lab_golden.LAB_GOLDEN_DIR.iterdir())
    assert files == sorted(f"{nid}.json" for nid in _lab_golden.GOLDEN_IDS)


@pytest.mark.parametrize("neutral_id", _lab_golden.GOLDEN_IDS)
def test_each_golden_file_has_the_format_and_only_neutral_ids(neutral_id: str) -> None:
    path = _lab_golden.golden_path(neutral_id)
    raw = path.read_bytes()
    assert b"\r\n" not in raw, f"{path.name} has CRLF line ends"
    doc = json.loads(raw, parse_constant=_refuse_constant)
    assert _lab_golden.validate_document(doc, expected_id=neutral_id) == []
    # The file is exactly what dump_lab_golden writes, so a rewrite of an unchanged file
    # leaves it unchanged.
    expected = json.dumps(_lab_golden.document(neutral_id, doc["quantities"]), indent=1)
    assert raw.decode("utf-8") == expected + "\n"


# --- the format rule ----------------------------------------------------------------------


def test_the_format_rule_accepts_a_document_of_summary_numbers() -> None:
    doc = _lab_golden.document("lab-04", _good_quantities())
    assert _lab_golden.validate_document(doc, expected_id="lab-04") == []


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda d: d.__setitem__("note", "lab-04"), id="extra-top-level-key"),
        pytest.param(lambda d: d.__setitem__("id", "series-a/acquisition-1.tif"), id="file-as-id"),
        pytest.param(
            lambda d: d["quantities"].__setitem__("hmm.means", ["acquisition-1.tif"]),
            id="file-in-a-value",
        ),
        pytest.param(
            lambda d: d["quantities"].__setitem__("acquisition-1.tif", 1), id="file-as-a-key"
        ),
        pytest.param(
            lambda d: d["quantities"].__setitem__("histogram.n_samples", "lab-04 movie"),
            id="free-text-value",
        ),
    ],
)
def test_the_format_rule_rejects_a_string_that_is_not_a_neutral_id(mutate) -> None:
    doc = _lab_golden.document("lab-04", _good_quantities())
    mutate(doc)
    problems = _lab_golden.validate_document(doc, expected_id="lab-04")
    assert any("not a neutral id" in p for p in problems), problems
    # The messages never repeat the offending text.
    assert not any("acquisition" in p or "series" in p for p in problems)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("molecules.count", 3.5),
        ("molecules.count", True),
        ("intensity.median_donor", float("nan")),
        ("histogram.counts", []),
        ("hmm.means", [0.1, float("inf")]),
    ],
)
def test_the_format_rule_rejects_a_value_of_the_wrong_kind(key: str, value) -> None:
    q = _good_quantities()
    q[key] = value
    problems = _lab_golden.validate_document(_lab_golden.document("lab-04", q))
    assert problems == [f"{key} is not a finite {_lab_golden.QUANTITIES[key]}"]


def test_the_format_rule_rejects_a_missing_quantity_and_a_wrong_id() -> None:
    q = _good_quantities()
    del q["hmm.n_iter"]
    problems = _lab_golden.validate_document(
        _lab_golden.document("lab-05", q), expected_id="lab-04"
    )
    assert "missing quantities: hmm.n_iter" in problems
    assert "id lab-05 does not match the file name lab-04" in problems


def test_dump_refuses_a_document_that_breaks_the_rule(tmp_path: Path) -> None:
    q = _good_quantities()
    q["hmm.means"] = ["acquisition-1.tif"]
    with pytest.raises(ValueError, match="refusing to write"):
        _lab_golden.dump_lab_golden("lab-04", q, tmp_path)
    assert not list(tmp_path.iterdir())
    path = _lab_golden.dump_lab_golden("lab-04", _good_quantities(), tmp_path)
    assert _lab_golden.load_lab_golden("lab-04", tmp_path) == _good_quantities()
    assert path.read_bytes().endswith(b"}\n")


def test_the_two_export_routes_count_as_one_acquisition() -> None:
    """Item 1 reads the vendor route; the direct route has no golden file of its own (D3: A)."""
    ((vendor, direct),) = _lab_golden.EXPORT_ROUTES.items()
    assert vendor in _lab_golden.LAB_ACQUISITIONS
    assert direct not in _lab_golden.LAB_ACQUISITIONS
    ids = list(_lab_golden.LAB_ACQUISITIONS)
    assert ids == sorted(set(ids)) and len(ids) >= 10  # Q20: C, ten or more
    assert all(_lab_golden.NEUTRAL_ID.fullmatch(nid) for nid in [*ids, direct])


def test_the_vendor_route_acquisitions_are_turned_back_to_their_map() -> None:
    """#887: the vendor-route TIFFs of one series are turned against the map of that series."""
    turned = _lab_golden.TURNED_AGAINST_MAP
    assert turned == {"lab-10", "lab-12"}
    assert turned <= set(_lab_golden.LAB_ACQUISITIONS)
    assert set(_lab_golden.EXPORT_ROUTES) <= turned  # the route whose turn was measured
    assert not turned & set(_lab_golden.EXPORT_ROUTES.values())  # a direct route is not turned
    frame = np.arange(12).reshape(3, 4)
    vendor = np.rot90(frame, k=_lab_golden.EXPORT_ROUTE_ROTATION)
    for nid in _lab_golden.LAB_ACQUISITIONS:
        k = _lab_golden.map_turns(nid)
        if nid in turned:
            assert np.array_equal(np.rot90(vendor, k=k), frame), nid
        else:
            assert k == 0, nid
    assert _lab_golden.map_turns("lab-11") == 0


def test_each_vsi_movie_has_a_recorded_turn_to_its_map() -> None:
    """#1433: the map of the series of ``vsi-01``/``vsi-02`` fits TIFFs turned 90° right."""
    assert _lab_golden.VSI_MAP_TURNS == {"vsi-01": 3, "vsi-02": 3}
    assert _lab_golden.VSI_ACQUISITIONS == ("vsi-01", "vsi-02")
    assert not set(_lab_golden.VSI_ACQUISITIONS) & set(_lab_golden.LAB_ACQUISITIONS)
    expected = (*_lab_golden.LAB_ACQUISITIONS, *_lab_golden.VSI_ACQUISITIONS)
    assert expected == _lab_golden.GOLDEN_IDS
    frame = np.arange(12).reshape(3, 4)
    clockwise = np.array([[8, 4, 0], [9, 5, 1], [10, 6, 2], [11, 7, 3]])
    for nid in _lab_golden.VSI_ACQUISITIONS:
        assert _lab_golden.NEUTRAL_ID.fullmatch(nid)
        assert _lab_golden.map_turns(nid) == 3, nid
        assert np.array_equal(np.rot90(frame, k=_lab_golden.map_turns(nid)), clockwise), nid


def test_the_movie_role_falls_back_to_the_default_role() -> None:
    assert _lab_golden.movie_role(["movie", "tmap"]) == "movie"
    assert _lab_golden.movie_role(["file", "tmap"]) == "file"
    assert _lab_golden.movie_role(["file", "movie", "tmap"]) == "movie"
    assert _lab_golden.movie_role(["tmap"]) == "movie"  # then the lookup names the missing role
    assert _lab_golden.movie_role(["vsi", "tiff_export", "tmap"]) == "vsi"


def test_a_vsi_skips_with_a_reason_without_the_bioformats_stack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TETHER_BIOFORMATS_PYTHON", raising=False)
    reason = _lab_golden.vsi_skip_reason()
    assert reason is not None and "TETHER_BIOFORMATS_PYTHON" in reason
    monkeypatch.setenv("TETHER_BIOFORMATS_PYTHON", "bioformats-python")
    assert _lab_golden.vsi_skip_reason() is None


# --- the pipeline, on the synthetic movie of #701 -----------------------------------------


@pytest.fixture(scope="module")
def synthetic(tmp_path_factory) -> tuple[dict, Path]:
    from _golden import MOVIE_NAME  # noqa: PLC0415
    from _synth import two_channel_movie, write_movie_tiff  # noqa: PLC0415

    root = tmp_path_factory.mktemp("lab-golden")
    movie = write_movie_tiff(
        two_channel_movie(seed=703, noise_sigma=20.0, n_frames=60), root / MOVIE_NAME
    )
    quantities = _lab_golden.lab_quantities(movie, None, root / "run")
    (project,) = sorted((root / "run" / "batch").glob("*.tether"))
    return quantities, project


def test_the_pipeline_yields_a_valid_golden_document(synthetic) -> None:
    quantities, _ = synthetic
    assert list(quantities) == sorted(_lab_golden.QUANTITIES)
    doc = _lab_golden.document("lab-01", quantities)
    assert _lab_golden.validate_document(doc, expected_id="lab-01") == []
    assert quantities["cli.batch.exit_code"] == 0
    assert quantities["molecules.count"] > 0
    assert quantities["molecules.accepted"] == quantities["molecules.count"]
    assert sum(quantities["histogram.counts"]) == quantities["histogram.n_samples"] > 0
    assert len(quantities["hmm.means"]) == _lab_golden.NSTATES


def test_a_movie_turned_and_turned_back_gives_the_same_molecules_and_traces(
    synthetic, tmp_path: Path
) -> None:
    """The export-route check's own step: undo a quarter turn, then compare the extractions."""
    _, project = synthetic
    movie = project.parents[2] / _golden_movie_name()
    k = _lab_golden.EXPORT_ROUTE_ROTATION
    vendor = _lab_golden.rotate_movie(movie, tmp_path / "vendor.tif", k)
    (tmp_path / "back").mkdir()
    back = _lab_golden.rotate_movie(vendor, tmp_path / "back", -k)
    assert back.name == vendor.name
    import tifffile  # noqa: PLC0415

    assert np.array_equal(tifffile.imread(back), tifffile.imread(movie))
    assert not np.array_equal(tifffile.imread(vendor), tifffile.imread(movie))
    code, turned = _lab_golden.extract_project(back, None, tmp_path / "run")
    assert code == 0 and turned is not None
    diff = _lab_golden.compare_export_routes(turned, project)
    assert diff["n_unmatched"] == 0 and diff["max_trace_diff"] == 0.0


def test_a_movie_turned_against_its_map_is_turned_back_before_extraction(
    synthetic, tmp_path: Path
) -> None:
    """#887: with ``map_turns``, a vendor-route movie gives the numbers of the direct movie."""
    quantities, project = synthetic
    movie = project.parents[2] / _golden_movie_name()
    (tmp_path / "vendor").mkdir()
    vendor = _lab_golden.rotate_movie(movie, tmp_path / "vendor", _lab_golden.EXPORT_ROUTE_ROTATION)
    nid = next(iter(_lab_golden.EXPORT_ROUTES))
    turned = _lab_golden.lab_quantities(
        vendor, None, tmp_path / "run", turns=_lab_golden.map_turns(nid)
    )
    assert turned == quantities
    assert vendor.is_file()  # the input stays; only the turned copy is deleted
    assert not list((tmp_path / "run" / "turned").iterdir())


class _FakeVsi:
    """A movie the adapters would open, standing in for a ``.vsi`` read by Bio-Formats."""

    def __init__(self, frames: np.ndarray) -> None:
        self.frames = frames
        self.n_frames = frames.shape[0]
        self.pixel_type = frames.dtype.str
        self.reads: list[tuple[int, int]] = []

    def __enter__(self) -> _FakeVsi:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read_frames(self, start: int, stop: int) -> np.ndarray:
        self.reads.append((start, stop))
        return self.frames[start:stop]


def test_a_vsi_is_turned_through_the_adapters_to_its_map(
    synthetic, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1433: a ``.vsi`` stored a quarter turn against its map gives the numbers of the map's
    orientation once :func:`map_turns` turns it, read a block at a time through the adapters."""
    import tifffile  # noqa: PLC0415

    import tether.io.adapters as adapters  # noqa: PLC0415

    quantities, project = synthetic
    movie = project.parents[2] / _golden_movie_name()
    nid = _lab_golden.VSI_ACQUISITIONS[0]
    # The .vsi holds the frames turned back by its recorded turn: turning them gives the movie.
    stored = np.rot90(tifffile.imread(movie), k=-_lab_golden.map_turns(nid), axes=(1, 2))
    fake = _FakeVsi(np.ascontiguousarray(stored, dtype="<u2"))
    opened: list[Path] = []

    def open_fake(path: Path) -> _FakeVsi:
        opened.append(Path(path))
        return fake

    monkeypatch.setattr(adapters, "open_movie_source", open_fake)
    monkeypatch.setattr(_lab_golden, "_CHUNK", 16)  # the synthetic movie is 60 frames
    vsi = tmp_path / f"{Path(_golden_movie_name()).stem}.vsi"
    (tmp_path / "turned").mkdir()
    out = _lab_golden.rotate_movie(vsi, tmp_path / "turned", _lab_golden.map_turns(nid))
    assert out.name == f"{vsi.stem}.tif"
    assert np.array_equal(tifffile.imread(out), tifffile.imread(movie))
    assert len(fake.reads) > 1  # read in blocks, never whole
    assert opened == [vsi]

    turned = _lab_golden.lab_quantities(
        vsi, None, tmp_path / "run", turns=_lab_golden.map_turns(nid)
    )
    assert turned == quantities
    assert not list((tmp_path / "run" / "turned").iterdir())


def test_a_vsi_of_a_wider_pixel_type_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tether.io.adapters as adapters  # noqa: PLC0415

    fake = _FakeVsi(np.zeros((2, 3, 4), dtype="<u4"))
    monkeypatch.setattr(adapters, "open_movie_source", lambda path: fake)
    with pytest.raises(ValueError, match="16-bit"):
        _lab_golden.rotate_movie(tmp_path / "x.vsi", tmp_path / "x.tif", 3)


def _golden_movie_name() -> str:
    from _golden import MOVIE_NAME  # noqa: PLC0415

    return MOVIE_NAME


def test_a_project_agrees_with_itself_across_export_routes(synthetic) -> None:
    _, project = synthetic
    diff = _lab_golden.compare_export_routes(project, project)
    assert diff["n_unmatched"] == 0 and diff["max_trace_diff"] == 0.0
    assert diff["n_matched"] == diff["n_molecules_a"] == diff["n_molecules_b"] > 0
    assert diff["n_frames_a"] == diff["n_frames_b"] == 60


def test_a_nan_in_one_route_counts_as_a_trace_difference(
    synthetic, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, project = synthetic
    import tether.imaging.extract as extract  # noqa: PLC0415

    real = extract.read_traces
    calls: list[Path] = []

    def with_a_nan(path: Path):
        traces = dict(real(path))
        calls.append(path)
        if len(calls) == 2:  # the second route only
            raw = np.array(traces["donor_raw"], dtype="float64")
            raw[0, 5] = np.nan
            traces["donor_raw"] = raw
        return traces

    monkeypatch.setattr(extract, "read_traces", with_a_nan)
    diff = _lab_golden.compare_export_routes(project, project)
    assert diff["n_nonfinite_diff"] == 1
    assert diff["max_trace_diff"] == 0.0  # the finite frames agree


def test_an_extra_molecule_at_a_matched_position_is_unmatched(
    synthetic, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, project = synthetic
    import tether.imaging.extract as extract  # noqa: PLC0415

    real_m, real_t = extract.read_molecules, extract.read_traces
    seen: list[str] = []

    def duplicated(read, kind: str):
        def wrapped(path: Path):
            data = read(path)
            seen.append(kind)
            if seen.count(kind) != 2:  # the second route only
                return data
            if kind == "m":
                return np.concatenate([data, data[-1:]])
            return {k: np.concatenate([np.asarray(v), np.asarray(v)[-1:]]) for k, v in data.items()}

        return wrapped

    monkeypatch.setattr(extract, "read_molecules", duplicated(real_m, "m"))
    monkeypatch.setattr(extract, "read_traces", duplicated(real_t, "t"))
    diff = _lab_golden.compare_export_routes(project, project)
    assert diff["n_molecules_b"] == diff["n_molecules_a"] + 1
    assert diff["n_unmatched"] == 1
    assert diff["max_trace_diff"] == 0.0


def test_a_failed_batch_gives_no_golden_numbers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_project = tmp_path / "batch" / "x.tether"
    monkeypatch.setattr(_lab_golden, "extract_project", lambda *a: (1, fake_project))
    with pytest.raises(RuntimeError, match="exited 1"):
        _lab_golden.lab_quantities(tmp_path / "movie.tif", None, tmp_path / "run")


# --- the update script --------------------------------------------------------------------


def _update_module():
    import importlib.util  # noqa: PLC0415

    spec = importlib.util.spec_from_file_location("tether_update_lab_golden_test", _UPDATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _data_dir(root: Path) -> Path:
    data = root / "data"
    mapping = {}
    for nid in _lab_golden.GOLDEN_IDS:
        movie = "file" if nid == "lab-03" else "movie"
        names = {movie: f"SECRET-series/{nid}-ZETA.tif", "tmap": "SECRET-series/OMEGA.tmap"}
        if nid in _lab_golden.VSI_ACQUISITIONS:
            names = {
                "vsi": f"SECRET-series/{nid}-ZETA.vsi",
                "tiff_export": f"SECRET-series/{nid}-ZETA.tif",
                "tmap": "SECRET-series/OMEGA.tmap",
            }
        for rel in names.values():
            (data / rel).parent.mkdir(parents=True, exist_ok=True)
            (data / rel).write_bytes(b"synthetic")
        mapping[nid] = names
    (data / "tether-private-data.json").write_text(json.dumps(mapping), encoding="utf-8")
    return data


def test_the_update_script_writes_after_every_id_ran_and_prints_no_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    update = _update_module()
    data = _data_dir(tmp_path)
    golden_dir = tmp_path / "golden"
    seen: list[str] = []
    turns: dict[str, int] = {}
    monkeypatch.setenv("TETHER_BIOFORMATS_PYTHON", "bioformats-python")

    def fake(movie: Path, tmap: Path, workdir: Path, **kw) -> dict:
        assert movie.is_file() and tmap.suffix == ".tmap"
        seen.append(movie.name)
        nid = movie.name.split("-ZETA")[0]
        # A .vsi id reads the .vsi itself, never the TIFF made from it.
        assert movie.suffix == (".vsi" if nid in _lab_golden.VSI_ACQUISITIONS else ".tif")
        turns[nid] = kw["turns"]
        if len(seen) == len(_lab_golden.GOLDEN_IDS) and fail[0]:
            raise RuntimeError(f"cannot read {movie}")
        return _good_quantities()

    fail = [True]
    monkeypatch.setattr(update._lab_golden, "lab_quantities", fake)
    args = ["--data", str(data), "--golden-dir", str(golden_dir)]
    assert update.main(args) == 2  # the last id failed: nothing is written
    assert not golden_dir.exists()

    fail[0] = False
    assert update.main(args) == 0
    assert sorted(p.stem for p in golden_dir.iterdir()) == list(_lab_golden.GOLDEN_IDS)
    assert turns == {nid: _lab_golden.map_turns(nid) for nid in _lab_golden.GOLDEN_IDS}
    assert {turns[nid] for nid in _lab_golden.VSI_ACQUISITIONS} == {3}
    assert update.main([*args, "--check"]) == 0
    q = _good_quantities()
    q["molecules.count"] = 4
    monkeypatch.setattr(update._lab_golden, "lab_quantities", lambda *a, **k: q)
    assert update.main([*args, "--check", "--only", "lab-02"]) == 1
    out = capsys.readouterr()
    text = out.out + out.err
    assert "| `lab-02.json` | `molecules.count` | 3 | 4 |" in text
    assert "SECRET" not in text and "ZETA" not in text and "OMEGA" not in text


def test_the_update_script_skips_the_vsi_ids_without_the_bioformats_stack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    update = _update_module()
    data = _data_dir(tmp_path)
    golden_dir = tmp_path / "golden"
    monkeypatch.delenv("TETHER_BIOFORMATS_PYTHON", raising=False)
    monkeypatch.setattr(update._lab_golden, "lab_quantities", lambda *a, **k: _good_quantities())
    args = ["--data", str(data), "--golden-dir", str(golden_dir)]
    assert update.main(args) == 0
    assert sorted(p.stem for p in golden_dir.iterdir()) == list(_lab_golden.LAB_ACQUISITIONS)
    out = capsys.readouterr().out
    for nid in _lab_golden.VSI_ACQUISITIONS:
        assert f"{nid}: skipped: the Bio-Formats stack is not installed" in out
    # An id the user names is never skipped quietly.
    assert update.main([*args, "--only", _lab_golden.VSI_ACQUISITIONS[0]]) == 2
    assert "nothing was written" in capsys.readouterr().err

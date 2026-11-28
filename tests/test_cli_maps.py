# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""``tether map create``, ``check`` and ``show`` (#918).

``map create`` writes the map file and its report (JSON and PNG); ``--accept`` and a
recipe's ``map.accept`` policy record the acceptance in the same ``/settings/approvals``
fields as ``tether approve`` (ADR-0102); ``map check`` gives the reuse check of #916 as an
exit code; ``map show`` prints the record as text or JSON.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("skimage")
pytest.importorskip("h5py")
pytest.importorskip("matplotlib")
tifffile = pytest.importorskip("tifffile")

import numpy as np  # noqa: E402

from tether.cli import main  # noqa: E402
from tether.imaging.map_asset import load_map_asset  # noqa: E402
from tether.project.approvals import read_approvals  # noqa: E402
from tether.project.core import Project  # noqa: E402
from tether.services import maps  # noqa: E402
from tether.services import recipe as rc  # noqa: E402

_SHAPE = (64, 96)  # two 48-px halves
_BEADS = np.array([[x, y] for x in (8.0, 22.0, 36.0) for y in (10.0, 30.0, 50.0)])


def _frame(shift: float) -> np.ndarray:
    centers = np.vstack([_BEADS, _BEADS + np.array([48.0 + shift, 0.0])])
    frame = np.full(_SHAPE, 80.0)
    rows, cols = np.mgrid[0 : _SHAPE[0], 0 : _SHAPE[1]]
    for x, y in centers:
        frame += 400.0 * np.exp(-((rows - y) ** 2 + (cols - x) ** 2) / (2.0 * 1.5**2))
    return frame


def _beads(path: Path) -> Path:
    """One bead image: nine beads per half, the acceptor ones +1 px in x."""
    tifffile.imwrite(path, np.ascontiguousarray(_frame(1.0), dtype=">u2"), byteorder=">")
    return path


def _movie(path: Path) -> Path:
    stack = np.broadcast_to(_frame(1.0), (4, *_SHAPE))
    tifffile.imwrite(path, np.ascontiguousarray(stack, dtype=">u2"), photometric="minisblack")
    return path


def _create(tmp_path: Path, *extra: str) -> Path:
    output = tmp_path / "beads.npz"
    assert (
        main(["map", "create", str(_beads(tmp_path / "beads.tif")), "-o", str(output), *extra]) == 0
    )
    return output


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- map create: the report ---------------------------------------------------------


def test_map_create_writes_the_map_and_its_report(tmp_path, capsys) -> None:
    output = _create(tmp_path, "--pixel-size", "0.16", "--optics", "60x TIRF")
    printed = capsys.readouterr().out
    report, png = tmp_path / "beads.json", tmp_path / "beads.png"
    assert f"report: {report} and {png}" in printed
    assert png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    data = json.loads(report.read_text(encoding="utf-8"))
    asset = load_map_asset(output)
    assert data["report"] == "tether-map" and data["version"] == 1
    assert data["map_file"] == "beads.npz" and data["map_sha256"] == _sha256(output)
    assert data["map_id"] == asset.map_id
    assert data["n_control_points"] == 9 and data["quality"]["n_pairs"] == 9
    assert data["pixel_size_um"] == 0.16 and data["optics"] == "60x TIRF"
    assert data["low_confidence"] is False and data["gate_px"] == 0.5


def test_map_create_refuses_an_existing_report_without_overwrite(tmp_path, capsys) -> None:
    beads = _beads(tmp_path / "beads.tif")
    (tmp_path / "m.png").write_bytes(b"keep")
    assert main(["map", "create", str(beads), "-o", str(tmp_path / "m.npz")]) == 1
    assert "the map's report exists" in capsys.readouterr().err
    assert sorted(p.name for p in tmp_path.iterdir()) == ["beads.tif", "m.png"]
    assert main(["map", "create", str(beads), "-o", str(tmp_path / "m.npz"), "--overwrite"]) == 0
    assert (tmp_path / "m.png").read_bytes() != b"keep"


# --- map create --accept --------------------------------------------------------------


def test_accept_records_a_person_approval_on_the_report(tmp_path, capsys) -> None:
    project = tmp_path / "exp.tether"
    Project.create(project)
    output = _create(tmp_path, "--accept", str(project), "--by", "Ana")
    assert "accepted in" in capsys.readouterr().out
    (approval,) = read_approvals(project)
    assert approval.step == "map.accept"
    assert approval.subject == load_map_asset(output).map_id
    assert (approval.by, approval.actor) == ("person", "Ana")
    assert (approval.recipe_sha256, approval.recipe_version) == ("", 0)
    assert dict(approval.report) == {
        "beads.json": _sha256(tmp_path / "beads.json"),
        "beads.png": _sha256(tmp_path / "beads.png"),
    }


def test_accept_into_a_missing_project_is_refused_before_any_work(tmp_path, capsys) -> None:
    beads = _beads(tmp_path / "beads.tif")
    argv = ["map", "create", str(beads), "-o", str(tmp_path / "m.npz")]
    assert main([*argv, "--accept", str(tmp_path / "none.tether")]) == 2
    assert "not a file" in capsys.readouterr().err
    assert main([*argv, "--by", "Ana"]) == 2
    assert "--by is the person of --accept" in capsys.readouterr().err
    assert sorted(p.name for p in tmp_path.iterdir()) == ["beads.tif"]


# --- map create in a recipe: the map.accept policy ----------------------------------


def _run(tmp_path: Path, policy: dict | None) -> rc.RecipeRun:
    if not (tmp_path / "exp.tether").exists():
        _beads(tmp_path / "beads.tif")
        Project.create(tmp_path / "exp.tether")
    fields = {"source": "beads.tif", "output": "beads.npz", "project": "exp.tether"}
    step: dict = {"command": "map", "version": 1, "fields": fields}
    if policy is not None:
        step["policy"] = policy
    text = json.dumps({"format": "tether-recipe", "version": 1, "steps": [step]})
    path = tmp_path / "recipe.json"
    path.write_text(text, encoding="utf-8")
    return rc.run_recipe(rc.read_recipe(path), resume=path.with_suffix(".done").exists())


_GATE = {"step": "map.accept", "rule": "rms-gate", "gate_px": 0.5, "no_residual": "stop"}


def test_a_policy_records_its_acceptance_in_the_same_fields(tmp_path) -> None:
    run = _run(tmp_path, {**_GATE, "otherwise": "stop"})
    assert [s.state for s in run.steps] == ["done"]
    (approval,) = read_approvals(tmp_path / "exp.tether")
    assert approval.step == "map.accept" and approval.by == "policy"
    assert approval.subject == load_map_asset(tmp_path / "beads.npz").map_id
    assert json.loads(approval.actor) == {**_GATE, "otherwise": "stop"}
    assert approval.recipe_sha256 and approval.recipe_version == 1
    assert set(approval.report) == {"beads.json", "beads.png"}


@pytest.mark.parametrize(("otherwise", "state"), [("stop", "stopped"), ("fail", "failed")])
def test_a_map_over_the_policys_gate_is_not_accepted(tmp_path, otherwise, state) -> None:
    run = _run(tmp_path, {**_GATE, "gate_px": 1e-30, "otherwise": otherwise})
    assert [s.state for s in run.steps] == [state]
    assert "over the policy's" in run.steps[0].message
    assert read_approvals(tmp_path / "exp.tether") == []


def test_flag_accepts_a_map_over_the_policys_gate(tmp_path) -> None:
    run = _run(tmp_path, {**_GATE, "gate_px": 1e-30, "otherwise": "flag"})
    assert [s.state for s in run.steps] == ["done"]
    assert [a.by for a in read_approvals(tmp_path / "exp.tether")] == ["policy"]


def test_no_policy_stops_after_the_report_and_resumes_once_a_person_accepts(tmp_path) -> None:
    run = _run(tmp_path, None)
    assert [s.state for s in run.steps] == ["stopped"]
    assert run.stop is not None and run.stop.report == str(tmp_path / "beads.json")
    map_id = load_map_asset(tmp_path / "beads.npz").map_id
    assert run.stop.subject == map_id
    project = str(tmp_path / "exp.tether")
    report = str(tmp_path / "beads.json")
    assert main(["approve", project, "map.accept", "--subject", map_id, "--report", report]) == 0
    (tmp_path / "recipe.done").touch()  # _run resumes
    resumed = _run(tmp_path, None)
    assert [s.state for s in resumed.steps] == ["done"]
    assert load_map_asset(tmp_path / "beads.npz").map_id == map_id  # not fitted again


def test_a_recipe_cannot_accept_for_a_person(tmp_path) -> None:
    gate = SimpleNamespace(decide=None, record=None)
    request = maps.MapCreateRequest(
        source="b.tif", output=str(tmp_path / "b.npz"), project="p.tether", accept=True
    )
    with pytest.raises(maps.RequestRefused, match="only with a map.accept policy"):
        maps.run(request, approvals=gate)


# --- map check --------------------------------------------------------------------


def _fake_movie(monkeypatch, pixel_size_um: float | None) -> None:
    from tether.io import frames

    @contextlib.contextmanager
    def opened(_path):
        yield SimpleNamespace(pixel_size_um=pixel_size_um)

    monkeypatch.setattr(frames, "open_extraction_movie", opened)


def test_map_check_exits_0_for_a_map_that_can_be_used(tmp_path, capsys) -> None:
    output = _create(tmp_path, "--optics", "60x")
    capsys.readouterr()
    movie = _movie(tmp_path / "movie.tif")
    assert main(["map", "check", str(output), str(movie), "--optics", " 60x "]) == 0
    out = capsys.readouterr().out
    assert "can register" in out and "not compared: channel layout" in out


def test_map_check_exits_1_with_the_reason_for_another_pixel_size(
    tmp_path, capsys, monkeypatch
) -> None:
    output = _create(tmp_path, "--pixel-size", "0.16")
    capsys.readouterr()
    _fake_movie(monkeypatch, 0.32)
    assert main(["map", "check", str(output), "movie.tif"]) == 1
    assert "pixel size: the map has 0.16 um; the movie has 0.32 um" in capsys.readouterr().err
    _fake_movie(monkeypatch, 0.16)
    assert main(["map", "check", str(output), "movie.tif"]) == 0


def test_map_check_exits_1_for_other_optics_unless_confirmed(tmp_path, capsys) -> None:
    output = _create(tmp_path, "--optics", "60x")
    movie = _movie(tmp_path / "movie.tif")
    capsys.readouterr()
    argv = ["map", "check", str(output), str(movie), "--optics", "100x"]
    assert main(argv) == 1
    err = capsys.readouterr().err
    assert "not confirmed" in err and "cannot register" in err
    assert main([*argv, "--confirm-optics"]) == 0


def test_map_check_exits_2_for_a_map_or_movie_that_cannot_be_read(tmp_path, capsys) -> None:
    bad = tmp_path / "bad.npz"
    bad.write_bytes(b"not a map")
    assert main(["map", "check", str(bad), "movie.tif"]) == 2
    output = _create(tmp_path)
    capsys.readouterr()
    assert main(["map", "check", str(output), str(tmp_path / "beads.tif")]) == 2
    assert "cannot read the movie" in capsys.readouterr().err


# --- map show ---------------------------------------------------------------------


def test_map_show_prints_the_record_as_text_and_json(tmp_path, capsys) -> None:
    output = _create(tmp_path, "--pixel-size", "0.16")
    capsys.readouterr()
    assert main(["map", "show", str(output), "--json"]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record == maps.map_summary(load_map_asset(output))
    assert record["geometry"] == {"donor": "crop 1 1 64 48", "acceptor": "crop 1 49 64 96"}
    assert main(["map", "show", str(output)]) == 0
    text = capsys.readouterr().out
    assert f"map_id: {record['map_id']}" in text and "pixel_size_um: 0.16" in text
    assert "optics: not known" in text and "pair_residuals" not in text


def test_map_show_exits_2_for_a_file_that_is_not_a_map(tmp_path, capsys) -> None:
    bad = tmp_path / "bad.npz"
    bad.write_bytes(b"not a map")
    assert main(["map", "show", str(bad)]) == 2
    assert "tether map show:" in capsys.readouterr().err


# --- refusals and failures ----------------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"accept": True}, "needs the project"),
        ({"project": "PROJECT"}, "only with 'accept'"),
        ({"project": "PROJECT", "accept": True, "by": " "}, "name of the person"),
    ],
)
def test_an_acceptance_that_cannot_be_recorded_is_refused(tmp_path, fields, message) -> None:
    project = tmp_path / "exp.tether"
    Project.create(project)
    fields = {k: str(project) if v == "PROJECT" else v for k, v in fields.items()}
    request = maps.MapCreateRequest(source="b.tif", output=str(tmp_path / "b.npz"), **fields)
    with pytest.raises(maps.RequestRefused, match=message):
        maps.run(request)


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--pixel-size", "0"], "--pixel-size must be > 0"),
        (["--frame-steps", "spin 7"], "--frame-steps 'spin 7'"),
    ],
)
def test_bad_values_fail_before_any_file_is_written(tmp_path, capsys, extra, message) -> None:
    beads = _beads(tmp_path / "beads.tif")
    assert main(["map", "create", str(beads), "-o", str(tmp_path / "m.npz"), *extra]) == 1
    assert message in capsys.readouterr().err
    assert sorted(p.name for p in tmp_path.iterdir()) == ["beads.tif"]


def test_a_report_made_meanwhile_is_kept_and_named(tmp_path, capsys, monkeypatch) -> None:
    beads = _beads(tmp_path / "beads.tif")
    real = maps._write_report

    def another_session_writes_the_report(*args, **kwargs):
        real(*args, **kwargs)
        (tmp_path / "m.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(maps, "_write_report", another_session_writes_the_report)
    assert main(["map", "create", str(beads), "-o", str(tmp_path / "m.npz")]) == 1
    assert "the map is written to" in capsys.readouterr().err
    assert (tmp_path / "m.json").read_text(encoding="utf-8") == "{}"


def test_a_project_that_cannot_record_fails_the_command(tmp_path, capsys) -> None:
    project = tmp_path / "exp.tether"
    project.write_bytes(b"not a project")
    beads = _beads(tmp_path / "beads.tif")
    argv = ["map", "create", str(beads), "-o", str(tmp_path / "m.npz"), "--accept", str(project)]
    assert main(argv) == 1
    assert "cannot record the acceptance" in capsys.readouterr().err


def test_a_resumed_run_with_an_unreadable_map_fails(tmp_path) -> None:
    _run(tmp_path, None)
    (tmp_path / "beads.npz").write_bytes(b"damaged")
    (tmp_path / "recipe.done").touch()
    resumed = _run(tmp_path, None)
    assert [s.state for s in resumed.steps] == ["failed"]
    assert "output exists" in resumed.steps[0].message


def test_a_resumed_run_without_its_report_cannot_record(tmp_path) -> None:
    _run(tmp_path, None)
    (tmp_path / "beads.json").unlink()
    (tmp_path / "recipe.done").touch()
    resumed = _run(tmp_path, {**_GATE, "otherwise": "stop"})
    assert [s.state for s in resumed.steps] == ["failed"]
    assert "cannot record the acceptance" in resumed.steps[0].message


@pytest.mark.parametrize(("no_residual", "accepted"), [("approve", "policy"), ("stop", None)])
def test_no_residual_decides_a_map_without_a_residual(tmp_path, no_residual, accepted) -> None:
    project = tmp_path / "exp.tether"
    Project.create(project)
    _create(tmp_path)
    asset = load_map_asset(tmp_path / "beads.npz")
    reg_map = SimpleNamespace(rms_residual=float("nan"))
    nan_asset = SimpleNamespace(map_id=asset.map_id, registration_map=reg_map)
    policy = rc.check_policy({**_GATE, "no_residual": no_residual, "otherwise": "stop"})
    recorded = []
    gate = SimpleNamespace(
        decide=lambda *a, **k: policy, record=lambda *a, **k: recorded.append(k["subject"])
    )
    request = maps.MapCreateRequest(source="b.tif", output="b.npz", project=str(project))
    report = tmp_path / "beads.json"
    if accepted is None:
        with pytest.raises(rc.ApprovalStop, match="no measured RMS residual"):
            maps._accept(request, nan_asset, report, gate=gate, actor="")
        assert recorded == []
    else:
        assert maps._accept(request, nan_asset, report, gate=gate, actor="") == accepted
        assert recorded == [asset.map_id]


def test_a_map_over_its_gate_is_written_with_a_warning(tmp_path, capsys) -> None:
    _create(tmp_path, "--rms-gate", "1e-30")
    assert "flagged low-confidence" in capsys.readouterr().err
    assert json.loads((tmp_path / "beads.json").read_text(encoding="utf-8"))["low_confidence"]

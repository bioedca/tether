# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The self-drift suite: end-to-end numbers compared with the committed golden files (#702).

Each input of ``_golden.INPUTS`` runs through the public API (one of them through the CLI)
and every quantity it yields is compared with ``tests/golden/<input>.json`` under the policy
of ADR-0078. A failure lists each moved quantity with its old value, its new value and the
relative change. When the change is intended, run ``python scripts/update_golden.py`` and
paste the table it prints into the pull request with a ``Cause:`` line.

This suite protects what Tether does now; it makes no claim that the numbers are right
(maintainer decision Q36). The rest of the file tests the comparison, the update command
and the pull-request check themselves, so a broken harness cannot pass silently.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("h5py")
pytest.importorskip("skimage")
pytest.importorskip("tifffile")

import _golden  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(
        f"_script_{name}", REPO / "scripts" / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


update_golden = _load_script("update_golden")
check_golden_table = _load_script("check_golden_table")


# --- the suite ----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def actual(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict]:
    """Every input run once per module (about 3 s together)."""
    root = tmp_path_factory.mktemp("golden")
    return {name: _golden.run_input(name, root / name) for name in _golden.INPUTS}


@pytest.mark.parametrize("name", sorted(_golden.INPUTS))
def test_end_to_end_numbers_match_the_golden_file(name: str, actual: dict[str, dict]) -> None:
    golden = _golden.load_golden(name)
    assert golden is not None, f"tests/golden/{name}.json is missing; run scripts/update_golden.py"
    diffs = _golden.compare(golden, actual[name])
    assert not diffs, (
        f"{len(diffs)} golden quantities of {name} moved (rtol {_golden.FLOAT_RTOL:g}). "
        "If intended, run `python scripts/update_golden.py` and paste its table in the PR:\n"
        + _golden.diff_table({name: diffs})
    )


def test_every_golden_file_has_an_input() -> None:
    files = {p.stem for p in _golden.GOLDEN_DIR.glob("*.json")}
    assert files == set(_golden.INPUTS)


def test_the_suite_covers_every_stage(actual: dict[str, dict]) -> None:
    """A stage that drops out of a pipeline would leave its golden numbers untested."""
    prefixes = {key.split(".")[0] for q in actual.values() for key in q}
    assert {
        "cli",
        "correct",
        "molecules",
        "histogram",
        "crosscorr",
        "cloud",
        "hmm",
        "tdp",
        "dwell",
        "transition_prob",
        "state_number",
        "export",
    } <= prefixes
    traces = actual["synthetic_traces"]
    # The correction stages ran and produced factors, not the apparent-E fallback.
    assert all(m == "corrected" for m in traces["molecules.correction_method"])
    assert 0.0 < traces["molecules.alpha"][0] < 0.3
    assert traces["tdp.n_transitions"] > 0


def _refuse_constant(name: str) -> None:
    pytest.fail(f"non-standard JSON constant {name} in a golden file")


def test_the_golden_files_are_strict_json_with_sorted_keys() -> None:
    for path in sorted(_golden.GOLDEN_DIR.glob("*.json")):
        raw = path.read_bytes()
        assert b"\r\n" not in raw, path.name
        doc = json.loads(raw, parse_constant=_refuse_constant)
        keys = list(doc["quantities"])
        assert keys == sorted(keys), path.name


# --- the comparison -----------------------------------------------------------------------


def test_compare_is_exact_for_integers_strings_and_shape() -> None:
    golden = {"n": 3, "s": "a", "v": [1.0, 2.0], "b": True}
    assert _golden.compare(golden, dict(golden)) == []
    assert [d.quantity for d in _golden.compare(golden, {**golden, "n": 4})] == ["n"]
    assert [d.quantity for d in _golden.compare(golden, {**golden, "s": "b"})] == ["s"]
    assert [d.quantity for d in _golden.compare(golden, {**golden, "v": [1.0]})] == ["v"]
    assert [d.quantity for d in _golden.compare(golden, {**golden, "b": False})] == ["b"]
    # An int that became a float is a change of type, whatever its value.
    assert [d.quantity for d in _golden.compare(golden, {**golden, "n": 3.0})] == ["n"]


def test_compare_uses_the_relative_limit_for_floats() -> None:
    rtol = _golden.FLOAT_RTOL
    golden = {"x": 1000.0, "nan": "nan"}
    assert _golden.compare(golden, {"x": 1000.0 * (1 + rtol / 2), "nan": "nan"}) == []
    moved = _golden.compare(golden, {"x": 1000.0 * (1 + 4 * rtol), "nan": "nan"})
    assert [d.quantity for d in moved] == ["x"]
    assert moved[0].relative == pytest.approx(4 * rtol, rel=1e-3)
    assert [d.quantity for d in _golden.compare(golden, {"x": 1000.0, "nan": 1.0})] == ["nan"]


def test_compare_reports_added_and_removed_quantities() -> None:
    diffs = _golden.compare({"old": 1}, {"new": 1})
    assert [(d.quantity, d.old, d.new) for d in diffs] == [("new", None, 1), ("old", 1, None)]


def test_normalize_table_masks_ids_and_rounds_floats() -> None:
    a = "molecule_id,x\nmol-0123456789abcdef,0.12345678901234567\n"
    b = "molecule_id,x\nmol-fedcba9876543210,0.12345678901234599\n"
    assert _golden.normalize_table(a) == _golden.normalize_table(b)
    assert "0.123456789" in _golden.normalize_table(a)
    # A value that moves in a kept digit, or a lost row, changes the text.
    assert _golden.normalize_table(a) != _golden.normalize_table(a.replace("0.1234", "0.1235"))
    assert _golden.normalize_table(a) != _golden.normalize_table(a.splitlines()[0])


def test_the_table_names_each_changed_array_element() -> None:
    """Codex finding on #838: a change late in a long array must show in the table."""
    old = [0.5] * 36
    new = [*old[:-1], 0.6]
    table = _golden.diff_table({"t": _golden.compare({"g": old}, {"g": new})})
    assert "| `t.json` | `g[35]` | 0.5 | 0.6 | 1.667e-01 |" in table
    assert "`g[0]`" not in table
    nested = _golden.compare({"m": [[1, 2], [3, 4]]}, {"m": [[1, 2], [3, 5]]})
    assert "| `t.json` | `m[1][1]` | 4 | 5 | 2.000e-01 |" in _golden.diff_table({"t": nested})
    # Many changed elements: the first rows, then one row that counts the rest.
    many = _golden.diff_table({"t": _golden.compare({"g": old}, {"g": [0.4] * 36})})
    assert many.count("`g[") == _golden.MAX_ELEMENT_ROWS
    assert f"… {36 - _golden.MAX_ELEMENT_ROWS} more changed elements" in many
    # A changed shape keeps one whole-value row, and every row still names its file.
    reshaped = _golden.diff_table({"t": _golden.compare({"g": old}, {"g": old[:-1]})})
    assert reshaped.count("| `t.json` | `g` |") == 1
    assert check_golden_table.table_files(many) == {"t.json"}
    assert all(r.startswith("| `t.json` |") for r in many.splitlines()[2:])


def test_the_two_table_headers_agree() -> None:
    assert check_golden_table.TABLE_HEADER == _golden.TABLE_HEADER


# --- the update command -------------------------------------------------------------------


def test_update_golden_prints_the_table_and_rewrites(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    name = "synthetic_movie"
    real = _golden.load_golden(name)
    assert real is not None
    stale = dict(real)
    stale["molecules.count"] = 8
    _golden.dump_golden(name, stale, tmp_path)

    assert update_golden.main(["--only", name, "--golden-dir", str(tmp_path), "--check"]) == 1
    out = capsys.readouterr().out
    assert _golden.TABLE_HEADER in out
    assert "| `synthetic_movie.json` | `molecules.count` | 8 | 9 | 1.111e-01 |" in out
    assert _golden.load_golden(name, tmp_path) == stale  # --check writes nothing

    assert update_golden.main(["--only", name, "--golden-dir", str(tmp_path)]) == 0
    assert "Paste the table" in capsys.readouterr().out
    assert _golden.compare(real, _golden.load_golden(name, tmp_path) or {}) == []

    assert update_golden.main(["--only", name, "--golden-dir", str(tmp_path), "--check"]) == 0
    assert "No golden quantity moved." in capsys.readouterr().out


def test_update_golden_writes_nothing_when_an_input_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure part way must leave the earlier golden files as they were."""
    for name in ("fixture_smd", "synthetic_movie"):
        _golden.dump_golden(name, {"stale": 1}, tmp_path)
    real = _golden.run_input

    def run_input(name: str, workdir: Path) -> dict:
        if name == "synthetic_movie":
            raise RuntimeError("input failed")
        return real(name, workdir)

    monkeypatch.setattr(_golden, "run_input", run_input)
    argv = ["--only", "fixture_smd", "--only", "synthetic_movie", "--golden-dir", str(tmp_path)]
    with pytest.raises(RuntimeError, match="input failed"):
        update_golden.main(argv)
    assert _golden.load_golden("fixture_smd", tmp_path) == {"stale": 1}


# --- the pull-request check ---------------------------------------------------------------

_TABLE = (
    f"{_golden.TABLE_HEADER}\n|---|---|---|---|---|\n"
    "| `synthetic_movie.json` | `molecules.count` | 8 | 9 | 1.111e-01 |\n"
)


def test_check_passes_when_no_golden_file_changed() -> None:
    assert check_golden_table.problems("", ["src/tether/cli.py"]) == []


def test_check_passes_with_the_table_and_a_cause() -> None:
    body = f"## Golden numbers\n\n{_TABLE}\nCause: the detector default moved (#999).\n"
    assert check_golden_table.problems(body, ["tests/golden/synthetic_movie.json"]) == []


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("Cause: something", "no golden table"),
        (_TABLE, "no 'Cause:' line"),
        (f"{_TABLE}\nCause:\n", "no 'Cause:' line"),
        (f"<!-- {_TABLE}\nCause: x -->", "no golden table"),
    ],
)
def test_check_fails_without_the_table_or_the_cause(body: str, expected: str) -> None:
    found = check_golden_table.problems(body, ["tests/golden/synthetic_movie.json"])
    assert any(expected in p for p in found), found


def test_check_names_each_changed_golden_file() -> None:
    body = f"{_TABLE}\nCause: x\n"
    found = check_golden_table.problems(
        body, ["tests/golden/synthetic_movie.json", "tests/golden/fixture_smd.json"]
    )
    assert found == ["the golden table names no row for fixture_smd.json"]


@pytest.mark.parametrize(
    "body",
    [
        # Codex finding on #838: the name only in prose under an empty table.
        f"{_golden.TABLE_HEADER}\n|---|---|---|---|---|\n\n"
        "Cause: regenerated `synthetic_movie.json`",
        # A row outside the golden table does not count either.
        "| `synthetic_movie.json` | a | b | c | d |\n\n"
        f"{_golden.TABLE_HEADER}\n|---|---|---|---|---|\n\nCause: x",
    ],
)
def test_check_needs_a_populated_row_in_the_table(body: str) -> None:
    found = check_golden_table.problems(body, ["tests/golden/synthetic_movie.json"])
    assert found == ["the golden table names no row for synthetic_movie.json"]


def test_check_reads_the_pull_request_event(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    event = tmp_path / "event.json"
    changed = ["--changed", "tests/golden/synthetic_movie.json"]
    event.write_text(json.dumps({"pull_request": {"body": "no table"}}), encoding="utf-8")
    assert check_golden_table.main(["--event", str(event), *changed]) == 1
    assert "no golden table" in capsys.readouterr().err
    event.write_text(json.dumps({"pull_request": {"body": f"{_TABLE}\nCause: x"}}), "utf-8")
    assert check_golden_table.main(["--event", str(event), *changed]) == 0
    event.write_text(json.dumps({"push": {}}), encoding="utf-8")
    assert check_golden_table.main(["--event", str(event)]) == 0

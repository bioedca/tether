# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The reproducibility page's sample output still matches what Tether writes (#596).

``docs/reproducibility.md`` teaches a reader to read back the provenance stamps of a
project and of an export, and shows the output of its two scripts. Those samples once came
from a movie made by a script that was never committed, and nothing checked them, so the
page drifted: the extraction profile gained ``donor_side_check`` and the sample never
showed it. This module makes the samples reproducible and keeps them honest:

* it runs the page's own commands -- the ``make_tutorial_movie.py --reproducibility-movie``
  line and the ``tether batch`` line, parsed by Tether's own argument parser -- in a
  temporary folder, then the page's ``h5py`` read-back script and its export-sidecar
  script, both copied from the page;
* it compares the **set of groups and keys** each script prints with the set the page's
  sample shows, including the keys inside ``profile_json`` and under ``parameters``. It
  does not compare values, which differ between runs and machines;
* it checks that the sample shows the page's placeholders for every version and time stamp,
  and that ``source_project`` names the project the page's commands make, not a file the
  reader cannot obtain.

The idealization stage needs the tMAVEN sidecar, which the default tier does not have, so
the batch runs with Tether's own idealize writer and a stand-in for the fit itself: the
``/idealization/<model>`` keys checked are the ones Tether stamps, while the values the page
shows came from a real tMAVEN fit. Default tier only: no ``large``, ``sidecar`` or ``deep``
marker.
"""

from __future__ import annotations

import functools
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("skimage")
pytest.importorskip("h5py")
pytest.importorskip("tifffile")

import numpy as np  # noqa: E402

from tether.cli import build_parser  # noqa: E402
from tether.idealize import IdealizationResult, StateModel, read_smd  # noqa: E402
from tether.project.batch import MovieJob, run_batch  # noqa: E402
from tether.project.idealize import idealize_molecules  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]
PAGE = _ROOT / "docs" / "reproducibility.md"
SCRIPT = _ROOT / "scripts" / "make_tutorial_movie.py"
TUTORIAL_MOVIE = _ROOT / "docs" / "tutorial" / "data" / "tutorial_movie.tif"

VERSION_PLACEHOLDER = "<your version>"
TIME_PLACEHOLDER = "<time of the run>"

_FENCE = re.compile(r"^```(\w+)\n(.*?)^```$", re.M | re.S)


def _blocks(kind: str) -> list[str]:
    return [body for lang, body in _FENCE.findall(PAGE.read_text(encoding="utf-8")) if lang == kind]


def _one(kind: str, marker: str) -> str:
    found = [body for body in _blocks(kind) if marker in body]
    assert len(found) == 1, (
        f"expected one ```{kind} block containing {marker!r}, found {len(found)}"
    )
    return found[0]


def _readback_script() -> str:
    return _one("python", "h5py.File(")


def _sidecar_script() -> str:
    return _one("python", ".provenance.json")


def _readback_sample() -> str:
    return _one("text", "[/settings/extraction]")


def _sidecar_sample() -> str:
    return _one("text", "tether_export:")


def _commands() -> list[list[str]]:
    return [shlex.split(line) for line in _one("console", "--reproducibility-movie").splitlines()]


# --- parsing the two outputs ------------------------------------------------------------


def _readback_values(text: str) -> dict[str, dict[str, str]]:
    """``{group: {key: value}}`` from the read-back script's output."""
    groups: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for line in text.splitlines():
        if line.startswith("[") and line.endswith("]"):
            current = groups.setdefault(line[1:-1], {})
        elif line.startswith("  ") and " = " in line:
            assert current is not None, f"key before any group: {line!r}"
            key, value = line.strip().split(" = ", 1)
            current[key] = value
        elif line.strip():
            raise AssertionError(f"unexpected read-back line: {line!r}")
    return groups


def _readback_keys(text: str) -> dict[str, set[str]]:
    """Group -> key set, with ``profile_json``'s own top-level keys as ``profile_json.<key>``."""
    keys: dict[str, set[str]] = {}
    for group, values in _readback_values(text).items():
        found = set(values)
        if "profile_json" in values:
            found |= {f"profile_json.{k}" for k in json.loads(values["profile_json"])}
        keys[group] = found
    return keys


def _sidecar_values(text: str) -> dict[str, str]:
    values = {}
    for line in text.splitlines():
        if line.strip():
            key, value = line.split(": ", 1)
            values[key] = value
    return values


# --- running the page in a temporary folder ---------------------------------------------


def _fake_fit(smd_path, *, nstates, model_type="vbconhmm", **_kw) -> IdealizationResult:
    """A stand-in for the tMAVEN fit: a flat model whose evidence peaks at two states."""
    smd = read_smd(smd_path)
    n, t = smd.n_molecules, smd.n_frames
    pre = smd.pre_list if smd.pre_list is not None else np.zeros(n, dtype=int)
    post = smd.post_list if smd.post_list is not None else np.full(n, t, dtype=int)
    means = np.linspace(0.3, 0.7, nstates) if nstates > 1 else np.array([0.5])
    idealized = np.full((n, t), np.nan)
    for i in range(n):
        idealized[i, int(pre[i]) : int(post[i])] = means[0]
    model = StateModel(
        model_type=model_type,
        nstates=nstates,
        means=means,
        variances=np.full(nstates, 0.01),
        tmatrix=np.eye(nstates),
        norm_tmatrix=np.eye(nstates) * 0.9,
        elbo={1: -3.0, 2: -1.0, 3: -2.0, 4: -4.0}[int(nstates)],
        dtype="FRET",
        idealized=idealized,
        ran=np.arange(n, dtype="int64"),
    )
    return IdealizationResult(
        model=model,
        state_paths={},
        dwells=[],
        model_path=Path(smd_path),
        status={"ok": True},
        molecule_keys=smd.molecule_keys,
    )


@pytest.fixture(scope="module")
def ran(tmp_path_factory) -> dict[str, object]:
    """Run the page's commands and scripts in an empty folder; return what they printed."""
    folder = tmp_path_factory.mktemp("reproducibility")
    make_movie, batch = _commands()

    assert make_movie[:2] == ["python", "make_tutorial_movie.py"], make_movie
    subprocess.run([sys.executable, str(SCRIPT), *make_movie[2:]], cwd=folder, check=True)

    assert batch[0] == "tether", batch
    args = build_parser().parse_args(batch[1:])
    assert args.command == "batch" and not args.no_idealize, batch
    out_dir = folder / args.out_dir
    jobs = [
        MovieJob(movie_path=folder / movie, output_path=out_dir / f"{Path(movie).stem}.tether")
        for movie in args.movies
    ]
    summary = run_batch(
        jobs,
        policy=args.policy,
        idealize=True,
        _idealize=functools.partial(idealize_molecules, _runner=_fake_fit),
    )
    assert summary.n_failed == 0, summary.format_report()

    def page_script(code: str) -> str:
        done = subprocess.run(
            [sys.executable, "-c", code], cwd=folder, check=True, capture_output=True, text=True
        )
        return done.stdout

    readback = page_script(_readback_script())

    from tether.project.export import export_molecule_table_csv  # noqa: PLC0415

    export = export_molecule_table_csv(jobs[0].output_path, folder / "molecules.csv")
    sidecar = page_script(_sidecar_script())
    return {
        "readback": readback,
        "sidecar": sidecar,
        "project": jobs[0].output_path.name,
        "n_exported": export.n_molecules,
    }


def test_the_tutorial_movie_is_untouched(ran) -> None:
    """The second movie must leave the committed tutorial movie byte for byte as it was."""
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
    assert TUTORIAL_MOVIE.is_file()


def test_readback_groups_and_keys_match_the_page(ran) -> None:
    page = _readback_keys(_readback_sample())
    actual = _readback_keys(ran["readback"])
    assert sorted(page) == sorted(actual), "the groups on the page differ from what Tether writes"
    for group in page:
        assert page[group] == actual[group], (
            f"{group}: only on the page {sorted(page[group] - actual[group])}, "
            f"only in the project {sorted(actual[group] - page[group])}"
        )


def test_sidecar_keys_match_the_page(ran) -> None:
    assert set(_sidecar_values(_sidecar_sample())) == set(_sidecar_values(ran["sidecar"]))


def test_the_samples_name_only_what_the_page_makes(ran) -> None:
    """``source_project`` and the script's file name are the project the commands make."""
    assert _sidecar_values(_sidecar_sample())["source_project"] == ran["project"]
    assert f'h5py.File("{ran["project"]}"' in _readback_script()
    assert '"molecules.csv.provenance.json"' in _sidecar_script()
    pycon = _one("pycon", "export_molecule_table_csv")
    assert f'export_molecule_table_csv("{ran["project"]}", "molecules.csv")' in pycon
    assert pycon.rstrip().splitlines()[-1] == str(ran["n_exported"])


def test_volatile_values_are_placeholders() -> None:
    """Every version and time stamp on the page is a placeholder, never one run's value."""
    for group, values in _readback_values(_readback_sample()).items():
        for key, value in values.items():
            if key == "app_version":
                assert value == VERSION_PLACEHOLDER, (group, value)
            elif key == "created_utc":
                assert value == TIME_PLACEHOLDER, (group, value)
            elif key == "profile_json":
                assert json.loads(value)["app_version"] == VERSION_PLACEHOLDER, group
    sidecar = _sidecar_values(_sidecar_sample())
    assert sidecar["app_version"] == VERSION_PLACEHOLDER
    assert sidecar["created_utc"] == TIME_PLACEHOLDER
    text = PAGE.read_text(encoding="utf-8")
    assert VERSION_PLACEHOLDER in text and TIME_PLACEHOLDER in text

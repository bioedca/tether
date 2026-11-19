# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real provenance sidecars validate against the published JSON Schema (#612).

``docs/reference/tether-provenance-schema.json`` is served so that a reader can check the
``<file>.provenance.json`` beside an export without Tether. It is written from the keys the
exporters write; this module is what keeps it true. Each of the four exports writes a sidecar into
``tmp_path`` and that sidecar must validate. The negative cases show the schema is not vacuous: a
missing or unknown top-level key, an unknown store-export parameter, a non-UTC store timestamp and
a figure sidecar without its three merged keys are each rejected.

``jsonschema`` (Draft 2020-12) is in the locked base stack as a transitive dependency; the module
skips where it is absent rather than pretend to validate.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("h5py")
jsonschema = pytest.importorskip("jsonschema")

import numpy as np  # noqa: E402

from _analysis_store import MEANS, build_store_with_model  # noqa: E402
from tether.analysis import plot_export  # noqa: E402
from tether.analysis.histogram import apparent_e_histogram  # noqa: E402
from tether.project.export import (  # noqa: E402
    export_deeplasi_txt,
    export_molecule_table_csv,
    export_subset_tether,
)

SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "docs" / "reference" / "tether-provenance-schema.json"
)

_STATES = np.array(
    [
        [0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1],
        [1, 1, 1, 2, 2, 2, 2, 1, 1, 1, 0, 0],
        [2, 2, 1, 1, 0, 0, 0, 1, 1, 2, 2, 2],
    ],
    dtype="int64",
)


@pytest.fixture(scope="module")
def validator() -> jsonschema.Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


@pytest.fixture
def sidecars(tmp_path: Path) -> dict[str, dict]:
    """One freshly written sidecar per export kind, read back as JSON."""
    project, _keys = build_store_with_model(tmp_path, _STATES, MEANS)
    out = tmp_path / "out"
    out.mkdir()
    rng = np.random.default_rng(0)
    figure = plot_export.render_histogram1d(apparent_e_histogram(rng.uniform(0.0, 1.0, 200)))
    results = {
        "molecule-table-csv": export_molecule_table_csv(project, out / "molecules.csv"),
        "deeplasi-txt": export_deeplasi_txt(project, out / "traces.txt"),
        "subset-tether": export_subset_tether(project, out / "subset.tether"),
        "plot:a1": plot_export.export_figure(
            figure,
            out / "a1",
            tether_export="plot:a1",
            source="exp.tether",
            parameters={"bins": 151},
        ),
    }
    return {
        kind: json.loads(result.provenance_path.read_text(encoding="utf-8"))
        for kind, result in results.items()
    }


def _errors(validator: jsonschema.Draft202012Validator, payload: dict) -> list[str]:
    return [error.message for error in validator.iter_errors(payload)]


def test_every_export_writes_a_sidecar_that_validates(validator, sidecars) -> None:
    assert set(sidecars) == {"molecule-table-csv", "deeplasi-txt", "subset-tether", "plot:a1"}
    for kind, payload in sidecars.items():
        assert payload["tether_export"] == kind
        assert _errors(validator, payload) == [], kind


def test_a_missing_or_unknown_top_level_key_is_rejected(validator, sidecars) -> None:
    missing = copy.deepcopy(sidecars["molecule-table-csv"])
    del missing["app_version"]
    assert _errors(validator, missing)

    extra = copy.deepcopy(sidecars["molecule-table-csv"])
    extra["unexpected"] = 1
    assert _errors(validator, extra)


def test_store_export_parameters_are_closed(validator, sidecars) -> None:
    for kind in ("molecule-table-csv", "deeplasi-txt", "subset-tether"):
        changed = copy.deepcopy(sidecars[kind])
        changed["parameters"]["unexpected"] = True
        assert _errors(validator, changed), kind

        dropped = copy.deepcopy(sidecars[kind])
        dropped["parameters"].pop("n_molecules")
        assert _errors(validator, dropped), kind


def test_a_store_timestamp_must_be_utc(validator, sidecars) -> None:
    changed = copy.deepcopy(sidecars["deeplasi-txt"])
    changed["created_utc"] = changed["created_utc"].replace("+00:00", "+02:00")
    assert _errors(validator, changed)


def test_a_figure_sidecar_needs_its_merged_keys(validator, sidecars) -> None:
    for key in ("formats", "dpi", "outputs"):
        changed = copy.deepcopy(sidecars["plot:a1"])
        changed["parameters"].pop(key)
        assert _errors(validator, changed), key

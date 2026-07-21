# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Every default printed on the parameter reference page matches the code.

``docs/reference/parameters.md`` re-homes the user-facing analysis parameters out of
the unpublished spec, and CONTRIBUTING's accuracy bar makes a *wrong* documented
default worse than an absent one: a scientist reads the page, believes the number,
and reports it in a methods section. This module is the durable guard against that —
if a constant moves in ``src/`` and the page is not updated, the test fails.

The defaults are read straight out of the source with :mod:`ast` rather than by
importing ``tether``, so the guard runs on the plain 3-OS ``test`` matrix without
pulling SciPy/h5py/matplotlib in and without executing module-import side effects.

The page side is parsed from its Markdown tables: for each registered entry, *every*
row whose **Parameter** cell carries the entry's backticked tokens must state the live
value in its **Default** cell. A parameter documented twice (``min_window_frames``
gates both the leakage and the gamma estimator) therefore has to agree with itself as
well as with the code.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "docs" / "reference" / "parameters.md"
SRC = ROOT / "src"

# A Markdown table body row: at least the six documented columns. Header/separator
# rows are filtered out by the separator pattern below.
_SEPARATOR_RE = re.compile(r"^\|[\s:|-]+\|$")
_BACKTICKED_RE = re.compile(r"`([^`]+)`")


def _module_source(dotted: str) -> ast.Module:
    """Parse ``tether.x.y`` from disk (no import, so no third-party dependency)."""
    path = SRC / Path(*dotted.split(".")).with_suffix(".py")
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _literal(node: ast.AST) -> object:
    return ast.literal_eval(node)


def module_constant(dotted: str, name: str) -> object:
    """The module-level value of ``name`` in module ``dotted``."""
    for stmt in _module_source(dotted).body:
        if isinstance(stmt, ast.AnnAssign):
            if isinstance(stmt.target, ast.Name) and stmt.target.id == name:
                assert stmt.value is not None
                return _literal(stmt.value)
        elif isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return _literal(stmt.value)
    raise AssertionError(f"{dotted} has no module-level constant {name!r}")


def dataclass_field(dotted: str, class_name: str, field: str) -> object:
    """The default of ``field`` on the dataclass ``class_name`` in module ``dotted``."""
    for stmt in _module_source(dotted).body:
        if isinstance(stmt, ast.ClassDef) and stmt.name == class_name:
            for item in stmt.body:
                if (
                    isinstance(item, ast.AnnAssign)
                    and isinstance(item.target, ast.Name)
                    and item.target.id == field
                    and item.value is not None
                ):
                    return _literal(item.value)
            raise AssertionError(f"{dotted}.{class_name} has no field {field!r}")
    raise AssertionError(f"{dotted} has no class {class_name!r}")


def _fmt(value: object) -> str:
    """Render a live default the way the page writes it in a Default cell."""
    if isinstance(value, str):
        return value
    return repr(value)


# --- The registry -------------------------------------------------------------
# (backticked tokens the Parameter cell must carry, live value). Some names are
# documented on more than one row (``learning_rate`` is both a ranker and a deep
# hyper-parameter), so an entry may pin a second token to identify its row. The
# value is read from the source, so a code change the page does not follow fails
# here.


def _registry() -> list[tuple[tuple[str, ...], object]]:
    opt = ("tether.project.extract", "ExtractOptions")
    entries: list[tuple[tuple[str, ...], object]] = [
        # Detection and extraction (ExtractOptions is the pinned CLI contract).
        ((name,), dataclass_field(*opt, name))
        for name in (
            "donor_side",
            "detection_mode",
            "detection_block",
            "prealign",
            "prealign_upsample",
            "prealign_low_sigma",
            "prealign_high_sigma",
            "pair_tol",
            "rms_gate",
            "window",
            "coloc_distance",
            "disk_radius",
            "ring_inner",
            "ring_outer",
            "bg_window",
        )
    ]
    entries += [
        # Photobleaching priors.
        (("PB_PRIOR_A",), module_constant("tether.fret.photobleach", "PB_PRIOR_A")),
        (("PB_PRIOR_B",), module_constant("tether.fret.photobleach", "PB_PRIOR_B")),
        (("PB_PRIOR_BETA",), module_constant("tether.fret.photobleach", "PB_PRIOR_BETA")),
        (("PB_PRIOR_MU",), module_constant("tether.fret.photobleach", "PB_PRIOR_MU")),
        # Corrections.
        (("LEAKAGE_CEILING",), module_constant("tether.fret.leakage", "LEAKAGE_CEILING")),
        (
            ("DEFAULT_MIN_WINDOW_FRAMES",),
            module_constant("tether.fret.leakage", "DEFAULT_MIN_WINDOW_FRAMES"),
        ),
        (
            ("DEFAULT_MIN_QUALIFYING_TRACES",),
            module_constant("tether.fret.leakage", "DEFAULT_MIN_QUALIFYING_TRACES"),
        ),
        (("GAMMA_CEILING",), module_constant("tether.fret.gamma", "GAMMA_CEILING")),
        (
            ("DEFAULT_GAMMA_HALF_WINDOW",),
            module_constant("tether.fret.gamma", "DEFAULT_GAMMA_HALF_WINDOW"),
        ),
        # Idealization.
        (("MODEL_TYPE_DEFAULT",), module_constant("tether.project.idealize", "MODEL_TYPE_DEFAULT")),
        (
            ("NSTATES_GRID_DEFAULT",),
            module_constant("tether.project.idealize", "NSTATES_GRID_DEFAULT"),
        ),
        (
            ("DEFAULT_SIDECAR_TIMEOUT",),
            module_constant("tether.idealize.supervisor", "DEFAULT_SIDECAR_TIMEOUT"),
        ),
        (
            ("DEFAULT_MAX_RESTARTS",),
            module_constant("tether.idealize.supervisor", "DEFAULT_MAX_RESTARTS"),
        ),
        (
            ("DEFAULT_PROBE_TIMEOUT",),
            module_constant("tether.idealize.supervisor", "DEFAULT_PROBE_TIMEOUT"),
        ),
        # Analysis — science tunables.
        (
            ("DEFAULT_BOOTSTRAP_RESAMPLES",),
            module_constant("tether.analysis.histogram", "DEFAULT_BOOTSTRAP_RESAMPLES"),
        ),
        (("DEFAULT_CI_LEVEL",), module_constant("tether.analysis.histogram", "DEFAULT_CI_LEVEL")),
        (("DEFAULT_SEED",), module_constant("tether.analysis.histogram", "DEFAULT_SEED")),
        (
            ("DEFAULT_HMM_MAX_ITER",),
            module_constant("tether.analysis.kinetics", "DEFAULT_HMM_MAX_ITER"),
        ),
        # Analysis — rendering defaults.
        (("DEFAULT_NBINS",), module_constant("tether.analysis.histogram", "DEFAULT_NBINS")),
        (("DEFAULT_RANGE",), module_constant("tether.analysis.histogram", "DEFAULT_RANGE")),
        (
            ("DEFAULT_SYNC_PREFRAME",),
            module_constant("tether.analysis.histogram", "DEFAULT_SYNC_PREFRAME"),
        ),
        (("DEFAULT_TDP_NSKIP",), module_constant("tether.analysis.tdp", "DEFAULT_TDP_NSKIP")),
        (("DEFAULT_DWELL_NBINS",), module_constant("tether.analysis.dwell", "DEFAULT_DWELL_NBINS")),
        (
            ("DEFAULT_TPROB_KDE_BANDWIDTH",),
            module_constant("tether.analysis.transition_prob", "DEFAULT_TPROB_KDE_BANDWIDTH"),
        ),
        (
            ("DEFAULT_CLOUD_HDR_COVERAGES",),
            module_constant("tether.analysis.cloud", "DEFAULT_CLOUD_HDR_COVERAGES"),
        ),
        (("DEFAULT_ELBOW_K_MAX",), module_constant("tether.analysis.cloud", "DEFAULT_ELBOW_K_MAX")),
        (
            ("DEFAULT_ELBOW_RESTARTS",),
            module_constant("tether.analysis.cloud", "DEFAULT_ELBOW_RESTARTS"),
        ),
        (("DEFAULT_ELBOW_SEED",), module_constant("tether.analysis.cloud", "DEFAULT_ELBOW_SEED")),
        (
            ("DEFAULT_ANTICORR_MIN_MAGNITUDE",),
            module_constant("tether.analysis.anticorrelation", "DEFAULT_ANTICORR_MIN_MAGNITUDE"),
        ),
        (
            ("DEFAULT_EXPORT_DPI",),
            module_constant("tether.analysis.plot_export", "DEFAULT_EXPORT_DPI"),
        ),
        # Curation ranker.
        (("DEFAULT_SEED_WEIGHT",), module_constant("tether.ml.weighting", "DEFAULT_SEED_WEIGHT")),
        (("DEFAULT_DRIFT_ALPHA",), module_constant("tether.ml.drift", "DEFAULT_DRIFT_ALPHA")),
        (
            ("DEFAULT_SHIP_BAR_PTS",),
            module_constant("tether.ml.prequential", "DEFAULT_SHIP_BAR_PTS"),
        ),
        (
            ("learning_rate", "RankerHyperparams"),
            dataclass_field("tether.ml.gbranker", "RankerHyperparams", "learning_rate"),
        ),
        (
            ("max_iter", "RankerHyperparams"),
            dataclass_field("tether.ml.gbranker", "RankerHyperparams", "max_iter"),
        ),
        (
            ("max_leaf_nodes", "RankerHyperparams"),
            dataclass_field("tether.ml.gbranker", "RankerHyperparams", "max_leaf_nodes"),
        ),
        (
            ("min_samples_leaf", "RankerHyperparams"),
            dataclass_field("tether.ml.gbranker", "RankerHyperparams", "min_samples_leaf"),
        ),
        (
            ("l2_regularization", "RankerHyperparams"),
            dataclass_field("tether.ml.gbranker", "RankerHyperparams", "l2_regularization"),
        ),
        (
            ("early_stopping", "RankerHyperparams"),
            dataclass_field("tether.ml.gbranker", "RankerHyperparams", "early_stopping"),
        ),
        (
            ("random_state", "RankerHyperparams"),
            dataclass_field("tether.ml.gbranker", "RankerHyperparams", "random_state"),
        ),
        # Optional deep add-on.
        (
            ("DEFAULT_WINDOW_LENGTH",),
            module_constant("tether.ml.deep.dataset", "DEFAULT_WINDOW_LENGTH"),
        ),
        (
            ("DEFAULT_NORMALIZATION",),
            module_constant("tether.ml.deep.dataset", "DEFAULT_NORMALIZATION"),
        ),
        (
            ("DEFAULT_VAL_FRACTION",),
            module_constant("tether.ml.deep.dataset", "DEFAULT_VAL_FRACTION"),
        ),
        (("DEFAULT_EPOCHS",), module_constant("tether.ml.deep.model", "DEFAULT_EPOCHS")),
        (("DEFAULT_BATCH_SIZE",), module_constant("tether.ml.deep.model", "DEFAULT_BATCH_SIZE")),
        (
            ("DEFAULT_LEARNING_RATE",),
            module_constant("tether.ml.deep.model", "DEFAULT_LEARNING_RATE"),
        ),
        (
            ("DEFAULT_FINE_TUNE_EPOCHS",),
            module_constant("tether.ml.deep.model", "DEFAULT_FINE_TUNE_EPOCHS"),
        ),
        (("DEFAULT_FREEZE_CONV",), module_constant("tether.ml.deep.model", "DEFAULT_FREEZE_CONV")),
    ]
    return entries


REGISTRY = _registry()


def _rows() -> list[list[str]]:
    """Every Markdown table body row of the page, as a list of stripped cells."""
    out: list[list[str]] = []
    for line in PAGE.read_text(encoding="utf-8").split("\n"):
        stripped = line.strip()
        if not stripped.startswith("|") or _SEPARATOR_RE.match(stripped):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) >= 2:
            out.append(cells)
    return out


ROWS = _rows()


def test_page_exists_and_has_tables() -> None:
    assert PAGE.is_file(), f"{PAGE} is missing"
    assert len(ROWS) > 50, "the parameter page lost its tables"


def test_page_is_listed_in_the_site_nav() -> None:
    """`mkdocs build --strict` fails on a page that is in docs/ but not in nav."""
    mkdocs = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    assert "reference/parameters.md" in mkdocs


def test_page_does_not_link_the_unpublished_spec() -> None:
    """The whole point of the page is that PRD.md is excluded from the site."""
    text = PAGE.read_text(encoding="utf-8")
    assert "PRD.md" not in text


@pytest.mark.parametrize(("tokens", "value"), REGISTRY, ids=[tokens[0] for tokens, _ in REGISTRY])
def test_documented_default_matches_the_code(tokens: tuple[str, ...], value: object) -> None:
    label = tokens[0]
    matches = [row for row in ROWS if set(tokens) <= set(_BACKTICKED_RE.findall(row[0]))]
    assert matches, f"no Parameter cell on the page mentions `{label}`"
    expected = _fmt(value)
    for row in matches:
        literals = _BACKTICKED_RE.findall(row[1])
        assert literals, f"the Default cell for `{label}` has no backticked literal"
        assert expected in literals, (
            f"docs/reference/parameters.md documents {literals} for `{label}`, "
            f"but the code says {expected!r}"
        )


def test_every_row_states_whether_it_is_recorded_in_the_project() -> None:
    """No parameter row may leave the 'Recorded in `.tether`' cell empty."""
    for row in ROWS:
        if len(row) != 6:
            continue  # the two 3-column explanatory tables
        assert row[5], f"empty 'Recorded in .tether' cell in row: {row[0]}"

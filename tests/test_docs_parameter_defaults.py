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

A registry entry reads its live value from a module constant
(:func:`module_constant`), a dataclass field (:func:`dataclass_field`) or a
function-signature default (:func:`function_default`) — the last because much of what
the page prints (the per-mode detector thresholds, ``intensity_quantity``, the
population functions' boolean switches) is a plain keyword default with no constant
behind it.

The page side is parsed from its Markdown tables: for each registered entry, *every*
row whose **Parameter** cell carries the entry's backticked tokens must state the live
value in its **Default** cell. A parameter documented twice (``min_window_frames``
gates both the leakage and the gamma estimator) therefore has to agree with itself as
well as with the code. A second pass walks the rows rather than the entries, so on a
row that prints several values no drifted default can hide behind a sibling's.
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
# A module-level constant as the page spells one: SCREAMING_CASE. Distinguishes the
# constants a Parameter cell names from the keyword arguments beside them.
_CONSTANT_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


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


def function_default(dotted: str, func: str, param: str) -> object:
    """The default of the parameter ``param`` on ``func`` in module ``dotted``.

    Function-signature defaults are the other half of what the page prints. The
    per-mode detector thresholds, ``intensity_quantity``, the population
    functions' boolean switches and the cross-correlation lag arguments are plain
    keyword defaults rather than module constants, so :func:`module_constant`
    cannot reach them and they would otherwise drift unguarded. A default that is
    itself a name (``bins=DEFAULT_NBINS``) is rejected: that value belongs to a
    :func:`module_constant` entry, so no registry line can silently pin a name
    instead of a value.
    """
    for stmt in _module_source(dotted).body:
        if not (isinstance(stmt, ast.FunctionDef) and stmt.name == func):
            continue
        args = stmt.args
        positional = args.posonlyargs + args.args
        defaulted = positional[len(positional) - len(args.defaults) :]
        pairs: list[tuple[str, ast.expr | None]] = [
            (arg.arg, default) for arg, default in zip(defaulted, args.defaults, strict=True)
        ]
        pairs += [
            (arg.arg, default)
            for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True)
        ]
        for name, node in pairs:
            if name != param:
                continue
            if node is None:
                raise AssertionError(f"{dotted}.{func} parameter {param!r} has no default")
            if isinstance(node, ast.Name):
                raise AssertionError(
                    f"{dotted}.{func} defaults {param!r} to the constant {node.id} — "
                    f"pin it with module_constant() instead"
                )
            return _literal(node)
        raise AssertionError(f"{dotted}.{func} has no defaulted parameter {param!r}")
    raise AssertionError(f"{dotted} has no module-level function {func!r}")


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


def _states(text: str, value: object) -> bool:
    """Does the backticked Default-cell literal ``text`` state ``value``?

    Compared by *value* and type, not by spelling, because prose is allowed to
    write a number the way a reader would: the page says ``1e-6`` and
    ``("pdf", "svg", "png")`` where ``repr`` would give ``1e-06`` and
    ``('pdf', 'svg', 'png')``. Requiring the parsed type to match exactly keeps the
    guard honest anyway — ``1`` does not satisfy ``True``, and ``0`` does not
    satisfy ``0.0``. Bare enum words (``wavelet``, ``cpu``, ``scott``) are not
    Python literals, so a live ``str`` is compared as text.
    """
    if isinstance(value, str):
        return text == value
    try:
        parsed = ast.literal_eval(text)
    except (SyntaxError, ValueError):
        return False
    return type(parsed) is type(value) and bool(parsed == value)


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
    detect = "tether.imaging.detect"
    entries += [
        # Per-mode detector defaults. ``ExtractOptions`` stores ``None`` for both and
        # the mode's own function supplies the number the page prints, so these are
        # reachable only as function-signature defaults.
        (("detection_threshold",), function_default(detect, "detect_spots_intensity", "threshold")),
        (("detection_threshold",), function_default(detect, "detect_spots_bandpass", "threshold")),
        (("min_separation",), function_default(detect, "detect_spots", "min_separation")),
        (
            ("min_separation",),
            function_default(detect, "detect_spots_intensity", "min_separation"),
        ),
        (("min_separation",), function_default(detect, "detect_spots_bandpass", "min_separation")),
        # Batch over-gate policy (the row names no constant, only its module).
        (("tether.project.batch",), module_constant("tether.project.batch", "POLICY_WARN")),
        # Photobleaching priors.
        (("PB_PRIOR_A",), module_constant("tether.fret.photobleach", "PB_PRIOR_A")),
        (("PB_PRIOR_B",), module_constant("tether.fret.photobleach", "PB_PRIOR_B")),
        (("PB_PRIOR_BETA",), module_constant("tether.fret.photobleach", "PB_PRIOR_BETA")),
        (("PB_PRIOR_MU",), module_constant("tether.fret.photobleach", "PB_PRIOR_MU")),
        (
            ("intensity_quantity", "tether.project.photobleach"),
            function_default(
                "tether.project.photobleach", "compute_photobleach", "intensity_quantity"
            ),
        ),
        # Corrections.
        (
            ("alpha_override",),
            function_default("tether.project.correct", "compute_corrected_fret", "alpha_override"),
        ),
        (
            ("gamma_override",),
            function_default("tether.project.correct", "compute_corrected_fret", "gamma_override"),
        ),
        (
            ("apparent_e_only",),
            function_default("tether.project.correct", "compute_corrected_fret", "apparent_e_only"),
        ),
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
        (
            ("nstates", "tether.project.idealize"),
            function_default("tether.project.idealize", "idealize_molecules", "nstates"),
        ),
        (
            ("nrestarts",),
            function_default("tether.project.idealize", "idealize_molecules", "nrestarts"),
        ),
        (
            ("intensity_quantity", "tether.project.idealize"),
            function_default("tether.project.idealize", "idealize_molecules", "intensity_quantity"),
        ),
        (
            ("defer_if_unavailable",),
            dataclass_field(
                "tether.idealize.supervisor", "SidecarSupervision", "defer_if_unavailable"
            ),
        ),
        # Analysis — science tunables.
        (
            ("intensity_quantity", "tether.analysis"),
            function_default(
                "tether.analysis.histogram",
                "population_apparent_e_histogram",
                "intensity_quantity",
            ),
        ),
        (
            ("include_first",),
            function_default("tether.analysis.dwell", "population_dwell_times", "include_first"),
        ),
        (
            ("per_molecule_equal_weight",),
            function_default(
                "tether.analysis.histogram",
                "population_apparent_e_histogram",
                "per_molecule_equal_weight",
            ),
        ),
        (
            ("include_rejected",),
            function_default(
                "tether.analysis.histogram",
                "population_apparent_e_histogram",
                "include_rejected",
            ),
        ),
        (
            ("include_stale",),
            function_default("tether.analysis.dwell", "population_dwell_times", "include_stale"),
        ),
        (
            ("model", "tether.analysis.dwell"),
            function_default("tether.analysis.dwell", "population_dwell_times", "model"),
        ),
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
        (("DEFAULT_HMM_TOL",), module_constant("tether.analysis.kinetics", "DEFAULT_HMM_TOL")),
        (
            ("DEFAULT_HMM_VAR_FLOOR",),
            module_constant("tether.analysis.kinetics", "DEFAULT_HMM_VAR_FLOOR"),
        ),
        (("DEFAULT_DWELL_DT",), module_constant("tether.analysis.dwell", "DEFAULT_DWELL_DT")),
        (("DEFAULT_TIME_DT",), module_constant("tether.analysis.histogram", "DEFAULT_TIME_DT")),
        (
            ("DEFAULT_CLOUD_TIME_DT",),
            module_constant("tether.analysis.cloud", "DEFAULT_CLOUD_TIME_DT"),
        ),
        (
            ("DEFAULT_DWELL_CI_LEVEL",),
            module_constant("tether.analysis.dwell", "DEFAULT_DWELL_CI_LEVEL"),
        ),
        # Analysis — rendering defaults.
        (("DEFAULT_NBINS",), module_constant("tether.analysis.histogram", "DEFAULT_NBINS")),
        (("DEFAULT_RANGE",), module_constant("tether.analysis.histogram", "DEFAULT_RANGE")),
        (("DEFAULT_TIME_BINS",), module_constant("tether.analysis.histogram", "DEFAULT_TIME_BINS")),
        (
            ("DEFAULT_SIGNAL_BINS",),
            module_constant("tether.analysis.histogram", "DEFAULT_SIGNAL_BINS"),
        ),
        (
            ("DEFAULT_SIGNAL_RANGE",),
            module_constant("tether.analysis.histogram", "DEFAULT_SIGNAL_RANGE"),
        ),
        (
            ("DEFAULT_SYNC_PREFRAME",),
            module_constant("tether.analysis.histogram", "DEFAULT_SYNC_PREFRAME"),
        ),
        (("DEFAULT_TDP_NSKIP",), module_constant("tether.analysis.tdp", "DEFAULT_TDP_NSKIP")),
        (
            ("DEFAULT_TDP_SIGNAL_BINS",),
            module_constant("tether.analysis.tdp", "DEFAULT_TDP_SIGNAL_BINS"),
        ),
        (
            ("DEFAULT_TDP_SIGNAL_RANGE",),
            module_constant("tether.analysis.tdp", "DEFAULT_TDP_SIGNAL_RANGE"),
        ),
        (("DEFAULT_DWELL_NBINS",), module_constant("tether.analysis.dwell", "DEFAULT_DWELL_NBINS")),
        (
            ("DEFAULT_TPROB_NBINS",),
            module_constant("tether.analysis.transition_prob", "DEFAULT_TPROB_NBINS"),
        ),
        (
            ("DEFAULT_TPROB_RANGE",),
            module_constant("tether.analysis.transition_prob", "DEFAULT_TPROB_RANGE"),
        ),
        (
            ("DEFAULT_TPROB_KDE_BANDWIDTH",),
            module_constant("tether.analysis.transition_prob", "DEFAULT_TPROB_KDE_BANDWIDTH"),
        ),
        (
            ("DEFAULT_TPROB_KDE_POINTS",),
            module_constant("tether.analysis.transition_prob", "DEFAULT_TPROB_KDE_POINTS"),
        ),
        (
            ("DEFAULT_STATE_NUMBER_LOW",),
            module_constant("tether.analysis.state_number", "DEFAULT_STATE_NUMBER_LOW"),
        ),
        (
            ("states_high",),
            function_default(
                "tether.analysis.state_number", "population_state_number", "states_high"
            ),
        ),
        (
            ("max_lag",),
            function_default("tether.analysis.crosscorr", "cross_correlation", "max_lag"),
        ),
        (
            ("normalize",),
            function_default("tether.analysis.crosscorr", "cross_correlation", "normalize"),
        ),
        (
            ("DEFAULT_CLOUD_SIGNAL_BINS",),
            module_constant("tether.analysis.cloud", "DEFAULT_CLOUD_SIGNAL_BINS"),
        ),
        (
            ("DEFAULT_CLOUD_TIME_BINS",),
            module_constant("tether.analysis.cloud", "DEFAULT_CLOUD_TIME_BINS"),
        ),
        (
            ("DEFAULT_CLOUD_SIGNAL_RANGE",),
            module_constant("tether.analysis.cloud", "DEFAULT_CLOUD_SIGNAL_RANGE"),
        ),
        (
            ("DEFAULT_CLOUD_HDR_COVERAGES",),
            module_constant("tether.analysis.cloud", "DEFAULT_CLOUD_HDR_COVERAGES"),
        ),
        (
            ("DEFAULT_CLOUD_BW_METHOD",),
            module_constant("tether.analysis.cloud", "DEFAULT_CLOUD_BW_METHOD"),
        ),
        (("DEFAULT_ELBOW_K_MAX",), module_constant("tether.analysis.cloud", "DEFAULT_ELBOW_K_MAX")),
        (
            ("DEFAULT_ELBOW_RESTARTS",),
            module_constant("tether.analysis.cloud", "DEFAULT_ELBOW_RESTARTS"),
        ),
        (("DEFAULT_ELBOW_SEED",), module_constant("tether.analysis.cloud", "DEFAULT_ELBOW_SEED")),
        (
            ("DEFAULT_ANTICORR_WINDOW",),
            module_constant("tether.analysis.anticorrelation", "DEFAULT_ANTICORR_WINDOW"),
        ),
        (
            ("DEFAULT_ANTICORR_STEP",),
            module_constant("tether.analysis.anticorrelation", "DEFAULT_ANTICORR_STEP"),
        ),
        (
            ("DEFAULT_ANTICORR_MIN_MAGNITUDE",),
            module_constant("tether.analysis.anticorrelation", "DEFAULT_ANTICORR_MIN_MAGNITUDE"),
        ),
        (
            ("DEFAULT_ANTICORR_MIN_WINDOWS",),
            module_constant("tether.analysis.anticorrelation", "DEFAULT_ANTICORR_MIN_WINDOWS"),
        ),
        (
            ("DEFAULT_EXPORT_DPI",),
            module_constant("tether.analysis.plot_export", "DEFAULT_EXPORT_DPI"),
        ),
        (("DEFAULT_FIGSIZE",), module_constant("tether.analysis.plot_export", "DEFAULT_FIGSIZE")),
        (
            ("DEFAULT_PLOT_FORMATS",),
            module_constant("tether.analysis.plot_export", "DEFAULT_PLOT_FORMATS"),
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
            ("DEFAULT_DEEP_CHANNELS",),
            module_constant("tether.ml.deep.dataset", "DEFAULT_DEEP_CHANNELS"),
        ),
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
        (("DEFAULT_SPLIT_SEED",), module_constant("tether.ml.deep.dataset", "DEFAULT_SPLIT_SEED")),
        (("DEFAULT_EPOCHS",), module_constant("tether.ml.deep.model", "DEFAULT_EPOCHS")),
        (("DEFAULT_BATCH_SIZE",), module_constant("tether.ml.deep.model", "DEFAULT_BATCH_SIZE")),
        (
            ("DEFAULT_LEARNING_RATE",),
            module_constant("tether.ml.deep.model", "DEFAULT_LEARNING_RATE"),
        ),
        (
            ("DEFAULT_NUM_CONV_LAYERS",),
            module_constant("tether.ml.deep.model", "DEFAULT_NUM_CONV_LAYERS"),
        ),
        (
            ("DEFAULT_CONV_CHANNELS",),
            module_constant("tether.ml.deep.model", "DEFAULT_CONV_CHANNELS"),
        ),
        (("DEFAULT_KERNEL_SIZE",), module_constant("tether.ml.deep.model", "DEFAULT_KERNEL_SIZE")),
        (("DEFAULT_LSTM_HIDDEN",), module_constant("tether.ml.deep.model", "DEFAULT_LSTM_HIDDEN")),
        (
            ("DEFAULT_BIDIRECTIONAL",),
            module_constant("tether.ml.deep.model", "DEFAULT_BIDIRECTIONAL"),
        ),
        (("DEFAULT_DROPOUT",), module_constant("tether.ml.deep.model", "DEFAULT_DROPOUT")),
        (
            ("DEFAULT_FINE_TUNE_EPOCHS",),
            module_constant("tether.ml.deep.model", "DEFAULT_FINE_TUNE_EPOCHS"),
        ),
        (
            ("DEFAULT_FINE_TUNE_LEARNING_RATE",),
            module_constant("tether.ml.deep.model", "DEFAULT_FINE_TUNE_LEARNING_RATE"),
        ),
        (("DEFAULT_FREEZE_CONV",), module_constant("tether.ml.deep.model", "DEFAULT_FREEZE_CONV")),
        (("DEFAULT_DEVICE",), module_constant("tether.ml.deep.model", "DEFAULT_DEVICE")),
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
    for row in matches:
        literals = _BACKTICKED_RE.findall(row[1])
        assert literals, f"the Default cell for `{label}` has no backticked literal"
        assert any(_states(text, value) for text in literals), (
            f"docs/reference/parameters.md documents {literals} for `{label}`, "
            f"but the code says {_fmt(value)!r}"
        )


def test_a_multi_value_row_states_each_registered_default_separately() -> None:
    """A changed default may not hide behind a sibling's value on the same row.

    ``test_documented_default_matches_the_code`` accepts *any* backticked literal
    in the Default cell, so on a row that prints several values a drifted constant
    passes as soon as its new value collides with a neighbour's: flip
    ``DEFAULT_CONV_CHANNELS`` from ``32`` to ``5`` and the ``5`` already printed
    for ``kernel_size`` absorbs it. This walks the row instead of the entry and
    makes every registered default claim its **own** literal.

    Two registry lines that carry the same tokens *and* the same value are one
    documented claim, not two — ``min_separation`` prints a single ``3.0`` for both
    the intensity and the bandpass detector — so they collapse before the walk. A
    row that still prints fewer literals than it has entries has collapsed values
    the registry cannot tell apart (``dt`` / ``time_dt`` print one ``1.0`` for
    three separately-named constants); it keeps the per-entry check only.
    """
    for row in ROWS:
        parameter_tokens = set(_BACKTICKED_RE.findall(row[0]))
        seen: set[tuple[tuple[str, ...], str, str]] = set()
        entries: list[tuple[tuple[str, ...], object]] = []
        for tokens, value in REGISTRY:
            key = (tokens, type(value).__name__, repr(value))
            if set(tokens) <= parameter_tokens and key not in seen:
                seen.add(key)
                entries.append((tokens, value))
        literals = _BACKTICKED_RE.findall(row[1]) if len(row) > 1 else []
        if len(entries) < 2 or len(literals) < len(entries):
            continue
        unclaimed = list(literals)
        for tokens, value in entries:
            for i, text in enumerate(unclaimed):
                if _states(text, value):
                    del unclaimed[i]
                    break
            else:
                raise AssertionError(
                    f"the row for `{tokens[0]}` prints {literals} but has no literal left "
                    f"for its live value {_fmt(value)!r} — a default on this row has "
                    f"drifted and is hiding behind a sibling's value"
                )


def test_every_constant_the_page_prints_a_value_for_is_registered() -> None:
    """The registry may not lag the page.

    ``test_documented_default_matches_the_code`` only checks what ``REGISTRY``
    names, so a row added to the page with an unregistered constant would be
    unguarded and could drift away from the code unnoticed. Every SCREAMING_CASE
    constant named in a **Parameter** cell of a six-column parameter table is
    therefore required to have an entry. The three-column "Tolerances and gates"
    table is exempt on purpose: it deliberately does not restate values, so it has
    no Default cell to check.
    """
    registered = {token for tokens, _ in REGISTRY for token in tokens}
    documented: set[str] = set()
    for row in ROWS:
        if len(row) != 6:
            continue
        documented |= {t for t in _BACKTICKED_RE.findall(row[0]) if _CONSTANT_RE.match(t)}
    missing = sorted(documented - registered)
    assert not missing, (
        "docs/reference/parameters.md prints a default for these constants but "
        f"_registry() does not pin them to the code: {missing}"
    )


def test_every_row_states_whether_it_is_recorded_in_the_project() -> None:
    """No parameter row may leave the 'Recorded in `.tether`' cell empty."""
    for row in ROWS:
        if len(row) != 6:
            continue  # the two 3-column explanatory tables
        assert row[5], f"empty 'Recorded in .tether' cell in row: {row[0]}"

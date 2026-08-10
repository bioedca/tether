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

Both passes originally looked only at the *set* of literals in a Default cell, which is
blind on the two rows that key their values to a **mode name** rather than to a
position (``detection_threshold``, ``min_separation``). Swapping those labels leaves
every literal present and distinct, so the guard stayed green while the page printed
the wrong per-mode number. A registry entry may therefore carry mode labels, and a
labelled value must appear next to *its own* label — see :class:`Claim`.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import NamedTuple

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


# --- Labelled Default cells ---------------------------------------------------
# Two rows print several values keyed by a *mode name* rather than by position:
#
#   detection_threshold | `None` (per-mode: `0.5` intensity, `0.98` bandpass)
#   min_separation      | `None` (per-mode: `8.0` wavelet, `3.0` intensity and bandpass)
#
# Every check below this point used to look only at the *set* of literals in the
# cell, so swapping the two labels -- writing `0.98` intensity and `0.5` bandpass --
# left both literals present and distinct and every assertion green, while the page
# told a reader the wrong per-mode number. A label is not decoration on these rows;
# it is half of the claim.

#: A backticked literal and the run of prose up to the next literal. That span is the
#: literal's label text: the cell is read left to right, and a label written after a
#: value belongs to it until the next value starts. Bounded by construction, so a
#: label cannot be satisfied by a word sitting next to some other number.
_LITERAL_SPAN_RE = re.compile(r"`([^`]+)`([^`]*)")

#: Word characters only -- `intensity,` and `bandpass)` must match the bare labels.
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")


def _labelled_literals(cell: str) -> list[tuple[str, frozenset[str]]]:
    """Each backticked literal in ``cell`` with the label words that follow it."""
    return [
        (literal, frozenset(_WORD_RE.findall(span)))
        for literal, span in _LITERAL_SPAN_RE.findall(cell)
    ]


class Claim(NamedTuple):
    """One documented default: the row it lives on, its live value, and its labels.

    ``labels`` is empty for the ordinary single-value row, where the value alone is
    the whole claim and any literal in the cell may satisfy it -- the behaviour every
    row had before mode labels existed. When labels are present the literal must
    *also* carry all of them, which is what makes a swap fail.
    """

    tokens: tuple[str, ...]
    value: object
    labels: frozenset[str] = frozenset()

    def satisfied_by(self, cell: str) -> bool:
        """Does ``cell`` state this claim's value under *all* of its labels?"""
        return any(
            _states(literal, self.value) and self.labels <= words
            for literal, words in _labelled_literals(cell)
        )


# --- The registry -------------------------------------------------------------
# (backticked tokens the Parameter cell must carry, live value[, mode labels]). Some
# names are documented on more than one row (``learning_rate`` is both a ranker and a
# deep hyper-parameter), so an entry may pin a second token to identify its row. The
# value is read from the source, so a code change the page does not follow fails
# here. The optional third element is the mode label(s) the value must be printed
# under; omit it on a row that prints one value.

_Entry = tuple[tuple[str, ...], object] | tuple[tuple[str, ...], object, tuple[str, ...]]


def _registry() -> list[_Entry]:
    opt = ("tether.project.extract", "ExtractOptions")
    entries: list[_Entry] = [
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
        # reachable only as function-signature defaults. Each is labelled with the mode
        # it belongs to, because on these two rows the label carries as much of the
        # claim as the number does.
        #
        # ``min_separation`` is read from all three functions even though intensity and
        # bandpass currently agree: the two entries MERGE into one labelled claim only
        # while their live values are equal, so changing one of them alone splits them
        # again and the page is asked for a literal it does not print.
        (
            ("detection_threshold",),
            function_default(detect, "detect_spots_intensity", "threshold"),
            ("intensity",),
        ),
        (
            ("detection_threshold",),
            function_default(detect, "detect_spots_bandpass", "threshold"),
            ("bandpass",),
        ),
        (
            ("min_separation",),
            function_default(detect, "detect_spots", "min_separation"),
            ("wavelet",),
        ),
        (
            ("min_separation",),
            function_default(detect, "detect_spots_intensity", "min_separation"),
            ("intensity",),
        ),
        (
            ("min_separation",),
            function_default(detect, "detect_spots_bandpass", "min_separation"),
            ("bandpass",),
        ),
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
            ("intensity_quantity", "tether.project.leakage"),
            function_default(
                "tether.project.leakage", "compute_leakage_alpha", "intensity_quantity"
            ),
        ),
        (
            ("intensity_quantity", "tether.project.gamma"),
            function_default("tether.project.gamma", "compute_gamma", "intensity_quantity"),
        ),
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
            # Canonical in ``driver`` and re-exported by ``supervisor`` (#222), so the
            # page's ``tether.idealize.supervisor`` path stays true while the value has
            # one home. ``module_constant`` reads the *source*, so it has to be pointed
            # at the module that assigns the literal, not one that imports the name.
            ("DEFAULT_SIDECAR_TIMEOUT",),
            module_constant("tether.idealize.driver", "DEFAULT_SIDECAR_TIMEOUT"),
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
            # ``include_first`` is one printed default governing two surfaces: the dwell
            # survival fit and the kinetics rate estimator. Same tokens and same live
            # value, so ``_claims`` merges them into the single ``False`` the page prints
            # -- and the merge is by value, so the day one of them changes they split
            # again and the row is asked for a literal it does not carry.
            ("include_first",),
            function_default("tether.analysis.dwell", "population_dwell_times", "include_first"),
        ),
        (
            ("include_first",),
            function_default("tether.analysis.kinetics", "pooled_exit_rates", "include_first"),
        ),
        (
            # Disambiguated on its module: ``state`` is a short, reusable word and a bare
            # entry would bind any future row that happens to backtick it.
            ("state", "tether.analysis.dwell"),
            function_default("tether.analysis.dwell", "population_dwell_times", "state"),
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
            ("density",),
            function_default(
                "tether.analysis.histogram",
                "population_apparent_e_histogram",
                "density",
            ),
        ),
        (
            ("density",),
            function_default(
                "tether.analysis.histogram",
                "population_time_signal_histogram2d",
                "density",
            ),
        ),
        (
            ("include_rejected", "tether.analysis"),
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
            ("nstates", "tether.analysis.kinetics"),
            function_default("tether.analysis.kinetics", "fit_gaussian_hmm", "nstates"),
        ),
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
        (
            # The A1 overlay grid. Both entry points default it to the constant itself,
            # which ``function_default`` refuses on purpose, so the constant is the only
            # honest way to pin it -- and pinning it once covers both.
            ("DEFAULT_OVERLAY_POINTS",),
            module_constant("tether.analysis.histogram", "DEFAULT_OVERLAY_POINTS"),
        ),
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
        (
            ("from_state",),
            function_default(
                "tether.analysis.histogram", "transition_sync_histogram2d", "from_state"
            ),
        ),
        (
            ("to_state",),
            function_default(
                "tether.analysis.histogram", "transition_sync_histogram2d", "to_state"
            ),
        ),
        (
            ("single_dwell",),
            function_default(
                "tether.analysis.histogram", "transition_sync_histogram2d", "single_dwell"
            ),
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
            # The transition-probability KDE switch, bound on BOTH signatures. Two
            # entries that merge while the pair agrees, exactly as ``min_separation``
            # does -- the single/population defaults drifting apart is precisely what
            # this is here to catch. Disambiguated on its module because the raw-FRET
            # cloud has a ``kde`` of its own further down the same table: a bare
            # ``("kde",)`` entry binds whichever row it lands on, and the two rows
            # agreeing on ``True`` today is what would make that silent.
            ("kde", "tether.analysis.transition_prob"),
            function_default("tether.analysis.transition_prob", "transition_prob_histogram", "kde"),
        ),
        (
            ("kde", "tether.analysis.transition_prob"),
            function_default(
                "tether.analysis.transition_prob", "population_transition_prob_histogram", "kde"
            ),
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
        (
            ("time_range",),
            function_default("tether.analysis.cloud", "raw_fret_cloud", "time_range"),
        ),
        (
            # Disambiguated for the same reason as the transition-probability pair above:
            # each ``kde`` must bind its own row and only its own.
            ("kde", "tether.analysis.cloud"),
            function_default("tether.analysis.cloud", "raw_fret_cloud", "kde"),
        ),
        (("DEFAULT_ELBOW_K_MAX",), module_constant("tether.analysis.cloud", "DEFAULT_ELBOW_K_MAX")),
        (
            ("DEFAULT_ELBOW_RESTARTS",),
            module_constant("tether.analysis.cloud", "DEFAULT_ELBOW_RESTARTS"),
        ),
        (("DEFAULT_ELBOW_SEED",), module_constant("tether.analysis.cloud", "DEFAULT_ELBOW_SEED")),
        (("k_min",), function_default("tether.analysis.cloud", "k_rmse_elbow", "k_min")),
        (
            ("in_grid_only",),
            function_default(
                "tether.analysis.cloud",
                "population_fret_cloud_state_number_elbow",
                "in_grid_only",
            ),
        ),
        (
            ("DEFAULT_ALPHA_FACTOR",),
            module_constant("tether.analysis.cloud", "DEFAULT_ALPHA_FACTOR"),
        ),
        (
            ("in_grid_only",),
            function_default(
                "tether.analysis.cloud", "population_fret_cloud_alpha_shape", "in_grid_only"
            ),
        ),
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
        (
            ("intensity_quantity", "tether.project.features"),
            function_default("tether.project.features", "compute_features", "intensity_quantity"),
        ),
        (
            ("include_rejected", "tether.project.features"),
            function_default("tether.project.features", "compute_features", "include_rejected"),
        ),
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
            ("intensity_quantity", "tether.project.deep_dataset"),
            function_default(
                "tether.project.deep_dataset", "build_deep_dataset", "intensity_quantity"
            ),
        ),
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
        (
            ("stratify",),
            function_default("tether.ml.deep.dataset", "train_val_split", "stratify"),
        ),
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


def _claims(entries: list[_Entry]) -> list[Claim]:
    """Registry entries as :class:`Claim`s, merging the ones the page prints once.

    Two entries with the same tokens and the same live value are **one** documented
    claim, not two -- ``min_separation`` reads its default from both the intensity and
    the bandpass detector and the page prints a single ``3.0`` for the pair. Merging
    unions their labels, so that one literal is required to carry *both* mode names,
    which is the acceptance criterion for a value shared by several labels.

    The merge is by live value, so it is not a way of excusing drift: if the two
    functions stop agreeing, the entries no longer merge and the page is asked for a
    literal it does not print. This is the same collapse the row-walking pass already
    performed inline, lifted here so both passes see one consistent claim list.
    """
    merged: dict[tuple[tuple[str, ...], str, str], Claim] = {}
    for entry in entries:
        tokens, value = entry[0], entry[1]
        labels = frozenset(entry[2]) if len(entry) > 2 else frozenset()
        key = (tokens, type(value).__name__, repr(value))
        previous = merged.get(key)
        merged[key] = Claim(tokens, value, labels | (previous.labels if previous else frozenset()))
    return list(merged.values())


REGISTRY = _claims(_registry())


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


@pytest.mark.parametrize(
    "claim",
    REGISTRY,
    ids=[
        claim.tokens[0] + ("-" + "-".join(sorted(claim.labels)) if claim.labels else "")
        for claim in REGISTRY
    ],
)
def test_documented_default_matches_the_code(claim: Claim) -> None:
    name = claim.tokens[0]
    matches = [row for row in ROWS if set(claim.tokens) <= set(_BACKTICKED_RE.findall(row[0]))]
    assert matches, f"no Parameter cell on the page mentions `{name}`"
    for row in matches:
        literals = _BACKTICKED_RE.findall(row[1])
        assert literals, f"the Default cell for `{name}` has no backticked literal"
        assert claim.satisfied_by(row[1]), (
            f"docs/reference/parameters.md documents {literals} for `{name}`"
            + (f" under {sorted(claim.labels)}" if claim.labels else "")
            + f", but the code says {_fmt(claim.value)!r}"
            + (
                " — the value must be printed next to its own mode label, so a label swap "
                "fails here even when every number is still on the row"
                if claim.labels
                else ""
            )
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
    the intensity and the bandpass detector — so they collapse in :func:`_claims`
    before the walk. A row that still prints fewer literals than it has entries has
    collapsed values the registry cannot tell apart (``dt`` / ``time_dt`` print one
    ``1.0`` for three separately-named constants); it keeps the per-entry check only.

    A labelled claim additionally has to take a literal that carries its labels, so on
    the two mode-keyed rows this pass fails a swap as well — it does not merely count.
    """
    for row in ROWS:
        parameter_tokens = set(_BACKTICKED_RE.findall(row[0]))
        entries = [claim for claim in REGISTRY if set(claim.tokens) <= parameter_tokens]
        cell = row[1] if len(row) > 1 else ""
        literals = _BACKTICKED_RE.findall(cell)
        if len(entries) < 2 or len(literals) < len(entries):
            continue
        unclaimed = _labelled_literals(cell)
        for claim in entries:
            for i, (text, words) in enumerate(unclaimed):
                if _states(text, claim.value) and claim.labels <= words:
                    del unclaimed[i]
                    break
            else:
                raise AssertionError(
                    f"the row for `{claim.tokens[0]}` prints {literals} but has no literal left "
                    f"for its live value {_fmt(claim.value)!r}"
                    + (f" under {sorted(claim.labels)}" if claim.labels else "")
                    + " — a default on this row has drifted and is hiding behind a sibling's "
                    "value, or its mode label has been swapped"
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
    registered = {token for claim in REGISTRY for token in claim.tokens}
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


# --- The label binding itself -------------------------------------------------
# The passes above assert what the page currently says. These assert that the guard
# would NOTICE if it changed -- which is the whole of #221, since every literal stayed
# present and distinct through the swap that motivated it.

#: The two mode-keyed cells as the page writes them today.
_THRESHOLD_CELL = "`None` (per-mode: `0.5` intensity, `0.98` bandpass)"
_SEPARATION_CELL = "`None` (per-mode: `8.0` wavelet, `3.0` intensity and bandpass)"


@pytest.mark.parametrize(
    ("parameter", "modes"),
    [
        ("detection_threshold", ("intensity", "bandpass")),
        ("min_separation", ("wavelet", "intensity", "bandpass")),
    ],
)
def test_each_mode_label_lands_in_exactly_one_literals_span(
    parameter: str, modes: tuple[str, ...]
) -> None:
    """The premise: the cell parses into labelled clauses, one owner per mode.

    Deliberately structural rather than a byte-for-byte pin on the cell. The claims
    above are all *subset* tests, so a rewording that dropped a mode word entirely
    would make them pass vacuously -- the binding would quietly become the
    set-of-literals check it replaced, and nothing would say so. This is the check
    that notices, while still leaving the prose free to be rewritten.

    A mode owned by two spans is the same defect from the other side: it would let a
    value satisfy a label that also sits next to a different value.
    """
    cell = next(
        row[1] for row in ROWS if parameter in _BACKTICKED_RE.findall(row[0]) and len(row) > 1
    )
    spans = _labelled_literals(cell)
    for mode in modes:
        owners = [literal for literal, words in spans if mode in words]
        assert len(owners) == 1, (
            f"the `{parameter}` Default cell must name the `{mode}` mode next to exactly one "
            f"value for the label binding to mean anything; it is claimed by {owners} in "
            f"{cell!r}"
        )


def test_a_label_swap_fails_even_though_every_literal_survives() -> None:
    """#221's acceptance criterion, and the exact hole it was filed for.

    Swapping the two mode labels leaves `0.5` and `0.98` both present and both
    distinct, so the set-of-literals checks that predate this stay green while the
    page tells a reader to threshold `bandpass` at `0.5`.
    """
    swapped = "`None` (per-mode: `0.98` intensity, `0.5` bandpass)"
    # The literals are untouched by the swap -- which is why counting them cannot help.
    assert set(_BACKTICKED_RE.findall(swapped)) == set(_BACKTICKED_RE.findall(_THRESHOLD_CELL))

    intensity = Claim(("detection_threshold",), 0.5, frozenset({"intensity"}))
    bandpass = Claim(("detection_threshold",), 0.98, frozenset({"bandpass"}))
    assert intensity.satisfied_by(_THRESHOLD_CELL)
    assert bandpass.satisfied_by(_THRESHOLD_CELL)
    assert not intensity.satisfied_by(swapped)
    assert not bandpass.satisfied_by(swapped)


def test_one_literal_may_carry_several_labels() -> None:
    """A value shared by two modes is one literal that must answer for both.

    ``min_separation`` prints a single ``3.0`` for intensity and bandpass. The merged
    claim demands both words in that literal's span, so dropping either from the page
    fails -- while the ``8.0`` sitting beside it cannot satisfy either.
    """
    shared = Claim(("min_separation",), 3.0, frozenset({"intensity", "bandpass"}))
    assert shared.satisfied_by(_SEPARATION_CELL)
    assert not shared.satisfied_by("`None` (per-mode: `8.0` wavelet, `3.0` intensity)")
    assert not shared.satisfied_by("`None` (per-mode: `3.0` intensity, `8.0` bandpass)")

    wavelet = Claim(("min_separation",), 8.0, frozenset({"wavelet"}))
    assert wavelet.satisfied_by(_SEPARATION_CELL)
    assert not wavelet.satisfied_by(
        "`None` (per-mode: `3.0` wavelet, `8.0` intensity and bandpass)"
    )


def test_a_label_is_bounded_by_the_next_literal() -> None:
    """A label may not be satisfied by a word belonging to a different value.

    Without the bound, "the cell contains `0.5` and contains 'intensity'" is true of
    every arrangement of those tokens, which is precisely the check that failed to
    catch the swap. The span ends at the next backtick, so a label written *before* a
    value belongs to the value it follows, not the one it precedes.
    """
    assert _labelled_literals(_THRESHOLD_CELL) == [
        ("None", frozenset({"per-mode"})),
        ("0.5", frozenset({"intensity"})),
        ("0.98", frozenset({"bandpass"})),
    ]


def test_an_unlabelled_claim_is_satisfied_by_any_literal_in_the_cell() -> None:
    """Every ordinary row keeps the behaviour it had before labels existed.

    The empty label set is a subset of every span, so an unlabelled claim never
    consults the prose -- the binding is opt-in per registry entry, and the ~200 rows
    that print one value are unaffected.
    """
    plain = Claim(("window",), 21)
    assert plain.satisfied_by("`21`")
    assert plain.satisfied_by("`None` (per-mode: `21` whatever, `3.0` else)")
    assert not plain.satisfied_by("`22`")


def test_entries_sharing_tokens_and_value_merge_their_labels() -> None:
    """The merge is by live value, so it cannot excuse two defaults drifting apart.

    While intensity and bandpass agree, they are one claim carrying both labels. Give
    them different values and they split into two claims that each demand their own
    labelled literal -- so a code change to one of them alone is still caught.
    """
    tokens = ("min_separation",)
    agreeing = _claims([(tokens, 3.0, ("intensity",)), (tokens, 3.0, ("bandpass",))])
    assert len(agreeing) == 1
    assert agreeing[0].labels == frozenset({"intensity", "bandpass"})

    drifted = _claims([(tokens, 3.0, ("intensity",)), (tokens, 4.0, ("bandpass",))])
    assert len(drifted) == 2
    assert not any(claim.satisfied_by(_SEPARATION_CELL) for claim in drifted if claim.value == 4.0)


def test_the_two_mode_keyed_rows_are_registered_with_labels() -> None:
    """The binding is worthless if the registry stops using it.

    Pins the mode/value pairs #221 names, read from the live registry rather than
    restated, so this fails if an entry loses its label or its source function.
    """
    labelled = {(claim.tokens[0], claim.value): claim.labels for claim in REGISTRY if claim.labels}
    assert labelled == {
        ("detection_threshold", 0.5): frozenset({"intensity"}),
        ("detection_threshold", 0.98): frozenset({"bandpass"}),
        ("min_separation", 8.0): frozenset({"wavelet"}),
        ("min_separation", 3.0): frozenset({"intensity", "bandpass"}),
    }

# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless stage preview of the extraction pipeline (#348; PRD §7.11, Appendix E).

[`iter_extraction_stages`][] runs the stages of
[`extract_movie`][tether.project.extract.extract_movie]
on a movie that is already in memory and yields one [`StageResult`][] per stage, with the
intermediate arrays a preview panel shows. [`preview_extraction`][] collects them. The stage
order is the one ``extract_movie`` executes::

    split_channels -> detection_image / detect_spots_by_mode (both halves)
    -> estimate_*_prealign -> pair_control_points
    -> fit_registration_map | an imported map -> colocalize -> extract_molecules

The preview stops before ``write_extraction``: it writes nothing and imports no Qt. It calls
only the public imaging primitives that ``extract_movie`` calls, with the same arguments, so
its molecules and traces are bit-identical to a real run with the same channel geometry and
registration source (``tests/test_extract_preview.py``).

Each result carries the parameters that produced it. A stage that cannot finish is a
``failed`` result with a message and ends the iteration; it is never raised. An over-gate
native fit is the ``over-gate`` outcome of the ``register`` stage, and the run continues with
the molecules flagged, as ``extract_movie`` does (ADR-0014).

The channel geometry, the registration source and the colocalization policy are arguments.
Today the only policy is ``donor-anchored`` (ADR-0015); #920 adds the second.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from tether import __version__
from tether.imaging import (
    ChannelGeometry,
    OverGateRegistrationWarning,
    RegistrationMap,
    colocalize,
    detect_spots_by_mode,
    detection_image,
    estimate_similarity_prealign,
    estimate_translation_prealign,
    extract_molecules,
    fit_registration_map,
    pair_control_points,
    split_channels,
)
from tether.project.extract import (
    _ACCEPTOR_CHANNEL,
    _DONOR_CHANNEL,
    ExtractOptions,
    _layout_geometry,
    resolve_channel_layout,
)

if TYPE_CHECKING:
    import numpy as np

    from tether.imaging import ColocalizedMolecules, MoleculeTraces

#: The stages in the order ``extract_movie`` runs them.
STAGES = ("split", "detect", "prealign", "pair", "register", "colocalize", "extract")
#: The colocalization rules the preview accepts. #920 adds a second rule.
COLOCALIZATION_POLICIES = ("donor-anchored",)
#: A stage ran and its result is usable.
OUTCOME_OK = "ok"
#: The native fit's residual exceeds the RMS gate; molecules are flagged, not dropped.
OUTCOME_OVER_GATE = "over-gate"
#: The stage does not apply to this run (prealign and pair under an imported map).
OUTCOME_SKIPPED = "skipped"
#: The stage could not finish; ``message`` says why and no later stage runs.
OUTCOME_FAILED = "failed"


_DETECT_KEYS = ("detection_mode", "detection_threshold", "detection_block", "min_separation")
_PREALIGN_KEYS = {
    "translation": ("prealign", "prealign_upsample"),
    "similarity": ("prealign", "prealign_upsample", "prealign_low_sigma", "prealign_high_sigma"),
}


@dataclass(frozen=True)
class StageResult:
    """One stage of a preview: its outcome, the parameters it used and what it produced."""

    name: str
    outcome: str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    outputs: Mapping[str, Any] = field(default_factory=dict)
    message: str = ""

    def __post_init__(self) -> None:
        """Freeze both mappings so a preview panel cannot change a recorded stage."""
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))
        object.__setattr__(self, "outputs", MappingProxyType(dict(self.outputs)))


@dataclass(frozen=True)
class ExtractionPreview:
    """Every stage result of one preview, in run order."""

    stages: tuple[StageResult, ...]

    def stage(self, name: str) -> StageResult | None:
        """Return the result of stage ``name``, or ``None`` if the run did not reach it."""
        return next((s for s in self.stages if s.name == name), None)

    @property
    def completed(self) -> bool:
        """Whether every stage ran and none failed."""
        return len(self.stages) == len(STAGES) and self.stages[-1].outcome != OUTCOME_FAILED

    @property
    def registration(self) -> RegistrationMap | None:
        """The registration map the run used, if it got that far."""
        return self._output("register", "registration")

    @property
    def molecules(self) -> ColocalizedMolecules | None:
        """The colocalized molecules, if the run got that far."""
        return self._output("colocalize", "molecules")

    @property
    def traces(self) -> MoleculeTraces | None:
        """The integrated traces, if the run completed."""
        return self._output("extract", "traces")

    def _output(self, stage: str, key: str) -> Any:
        result = self.stage(stage)
        return None if result is None else result.outputs.get(key)


def _run(name: str, params: dict[str, Any], body: Callable[[], dict[str, Any]]) -> StageResult:
    """Run one stage; a failure becomes a ``failed`` result instead of an exception."""
    try:
        return StageResult(name, OUTCOME_OK, params, body())
    except Exception as exc:  # a preview reports a failed stage, it never raises
        return StageResult(name, OUTCOME_FAILED, params, message=f"{type(exc).__name__}: {exc}")


def _registered(reg_map: RegistrationMap) -> dict[str, Any]:
    """Build the ``register`` outputs, the same for a native fit and a supplied map."""
    return {
        "registration": reg_map,
        "rms_residual": float(reg_map.rms_residual),
        "molecule_tags": reg_map.molecule_tags,
    }


def _geometry_params(side: str, geometry: ChannelGeometry) -> dict[str, Any]:
    """Record the whole transform a channel's split applies: crop, rotation and flip."""
    crop = None if geometry.crop is None else [int(v) for v in geometry.crop]
    return {
        f"{side}_crop": crop,
        f"{side}_rotation_deg": int(geometry.rotation_deg),
        f"{side}_flip": [int(v) for v in geometry.flip],
    }


def _geometry(
    movie: np.ndarray,
    options: ExtractOptions,
    geometry: tuple[ChannelGeometry, ChannelGeometry] | None,
    registration: RegistrationMap | None,
    params: dict[str, Any],
) -> tuple[ChannelGeometry, ChannelGeometry]:
    """Resolve the channel geometry exactly as ``extract_movie`` does, recording its source."""
    if geometry is not None:
        params["geometry_source"] = "argument"
        if registration is not None:
            # The map's coordinates are local to the crops it was fitted on; other
            # crops would read the acceptor at the wrong pixels and still look fine.
            stored = (registration.reference_geometry, registration.moving_geometry)
            if None not in stored and tuple(geometry) != stored:
                raise ValueError(
                    "the geometry argument does not match the registration map's own "
                    "channel geometry, and the map's coordinates are local to that geometry"
                )
        return geometry
    if registration is not None:
        if options.channel_layout is not None:
            # As in extract_movie: an imported map carries its own split, and a layout
            # beside it would name a second, competing one (#908).
            raise ValueError(
                "channel_layout and an imported registration both set the channel split; "
                "give only one"
            )
        params["geometry_source"] = "registration"
        donor, acceptor = registration.reference_geometry, registration.moving_geometry
        if donor is None or acceptor is None:
            raise ValueError("the registration map lacks per-channel crop geometry")
        return donor, acceptor
    if options.channel_layout is None:
        params.update(geometry_source="half-split", donor_side=options.donor_side)
    else:
        params.update(geometry_source="layout", channel_layout=options.channel_layout.to_dict())
    height, width = movie.shape[-2:]
    return _layout_geometry(resolve_channel_layout(options), width, height)


def iter_extraction_stages(
    movie: np.ndarray,
    *,
    options: ExtractOptions | None = None,
    geometry: tuple[ChannelGeometry, ChannelGeometry] | None = None,
    registration: RegistrationMap | None = None,
    policy: str = "donor-anchored",
) -> Iterator[StageResult]:
    """Yield one [`StageResult`][] per extraction stage of ``movie``.

    Parameters
    ----------
    movie:
        The raw ``(T, H, W)`` movie, for example the ``data`` of an
        [`open_movie`][tether.io.movie.open_movie] reader. It is only read.
    options:
        The tunables [`extract_movie`][tether.project.extract.extract_movie] takes. Resolve a
        ``.tdat`` into them first; the preview reads no files.
    geometry:
        The ``(donor, acceptor)`` channel geometry. ``None`` uses the geometry of
        ``registration`` when one is given, else the split of
        ``options.channel_layout``, or the left/right half split that
        ``options.donor_side`` names when no layout is set. With a ``registration``
        that records its own geometry, a different one fails the ``split`` stage,
        because the map's coordinates are local to its own crops. A
        ``channel_layout`` beside a ``registration`` fails it too.
    registration:
        An imported map (for example from
        [`registration_map_from_tmap`][tether.imaging.calibrate.registration_map_from_tmap]).
        ``None`` fits a native
        map from the movie's own spots. With a map, prealign and pair are ``skipped``.
    policy:
        The colocalization rule, one of [`COLOCALIZATION_POLICIES`][].

    Raises
    ------
    ValueError
        For an unknown ``policy``. A stage that fails is yielded, never raised.
    """
    if policy not in COLOCALIZATION_POLICIES:
        raise ValueError(f"policy must be one of {COLOCALIZATION_POLICIES}, got {policy!r}")
    opts = options or ExtractOptions()
    out: dict[str, Any] = {}  # every output so far, keyed as the stages name them

    def split() -> dict[str, Any]:
        donor, acceptor = _geometry(movie, opts, geometry, registration, split_params)
        split_params.update(
            _geometry_params("donor", donor) | _geometry_params("acceptor", acceptor)
        )
        donor_stack, acceptor_stack = split_channels(movie, donor, acceptor)
        return {
            "donor_geometry": donor,
            "acceptor_geometry": acceptor,
            "donor_stack": donor_stack,
            "acceptor_stack": acceptor_stack,
        }

    def detect() -> dict[str, Any]:
        found: dict[str, Any] = {}
        for side in ("donor", "acceptor"):
            image = detection_image(out[f"{side}_stack"], block=opts.detection_block)
            found[f"{side}_detection_image"] = image
            found[f"{side}_spots"] = detect_spots_by_mode(
                image,
                mode=opts.detection_mode,
                threshold=opts.detection_threshold,
                min_separation=opts.min_separation,
            )
        return found

    def prealign() -> dict[str, Any]:
        reference, moving = out["donor_detection_image"], out["acceptor_detection_image"]
        if opts.prealign == "similarity":
            transform = estimate_similarity_prealign(
                reference,
                moving,
                upsample_factor=opts.prealign_upsample,
                low_sigma=opts.prealign_low_sigma,
                high_sigma=opts.prealign_high_sigma,
            )
        else:
            transform = estimate_translation_prealign(
                reference, moving, upsample_factor=opts.prealign_upsample
            )
        return {"prealign": transform}

    def pair() -> dict[str, Any]:
        paired = pair_control_points(
            out["donor_spots"], out["acceptor_spots"], tol=opts.pair_tol, prealign=out["prealign"]
        )
        return {"paired": paired, "n_pairs": len(paired.reference)}

    def register() -> dict[str, Any]:
        # The over-gate verdict is this stage's outcome; the warning would only repeat it.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", OverGateRegistrationWarning)
            reg_map = fit_registration_map(
                out["paired"].reference,
                out["paired"].moving,
                reference_channel=_DONOR_CHANNEL,
                moving_channel=_ACCEPTOR_CHANNEL,
                reference_geometry=out["donor_geometry"],
                moving_geometry=out["acceptor_geometry"],
                gate_px=opts.rms_gate,
                on_over_gate="warn",
                app_version=__version__,
            )
        return _registered(reg_map)

    def coloc() -> dict[str, Any]:
        molecules = colocalize(
            out["donor_spots"],
            out["registration"],
            donor_shape=out["donor_stack"].shape[1:],
            acceptor_shape=out["acceptor_stack"].shape[1:],
            acceptor_spots=out["acceptor_spots"],
            window=opts.window,
            coloc_distance_px=opts.coloc_distance,
        )
        return {"molecules": molecules, "n_molecules": int(molecules.n_molecules)}

    def extract() -> dict[str, Any]:
        stacks = (out["donor_stack"], out["acceptor_stack"])
        return {"traces": extract_molecules(*stacks, out["molecules"], **extract_params)}

    split_params: dict[str, Any] = {"movie_shape": [int(n) for n in movie.shape]}
    extract_params = {
        k: getattr(opts, k)
        for k in ("window", "disk_radius", "ring_inner", "ring_outer", "bg_window")
    }
    plan: list[tuple[str, dict[str, Any], Callable[[], dict[str, Any]] | None]] = [
        ("split", split_params, split),
        ("detect", {k: getattr(opts, k) for k in _DETECT_KEYS}, detect),
    ]
    if registration is None:
        prealign_keys = _PREALIGN_KEYS[opts.prealign]
        plan += [
            ("prealign", {k: getattr(opts, k) for k in prealign_keys}, prealign),
            ("pair", {"pair_tol": opts.pair_tol}, pair),
            (
                "register",
                {"source": "native", "rms_gate": opts.rms_gate, "on_over_gate": "warn"},
                register,
            ),
        ]
    else:  # an imported map: nothing to prealign, pair or fit (``None`` marks a skip)
        imported = {"source": registration.source, "provenance": dict(registration.provenance)}
        plan += [
            ("prealign", {}, None),
            ("pair", {}, None),
            ("register", imported, lambda: _registered(registration)),
        ]
    plan += [
        (
            "colocalize",
            {"policy": policy, "window": opts.window, "coloc_distance": opts.coloc_distance},
            coloc,
        ),
        ("extract", extract_params, extract),
    ]

    for name, params, body in plan:
        if body is None:
            yield StageResult(name, OUTCOME_SKIPPED, message="the registration is imported")
            continue
        result = _run(name, params, body)
        out.update(result.outputs)
        if name == "pair" and result.outcome == OUTCOME_OK and result.outputs["n_pairs"] < 2:
            n = result.outputs["n_pairs"]
            result = replace(
                result,
                outcome=OUTCOME_FAILED,
                message=(
                    f"only {n} control-point pair(s) matched (need >= 2) from "
                    f"{len(out['donor_spots'])} donor / {len(out['acceptor_spots'])} acceptor spots"
                ),
            )
        elif (
            name == "register"
            and result.outcome == OUTCOME_OK
            and out["registration"].low_confidence
        ):
            reg = out["registration"]  # native or supplied: read the map's own gate
            result = replace(
                result,
                outcome=OUTCOME_OVER_GATE,
                message=(
                    f"residual {reg.rms_residual:.3f} px exceeds the {reg.gate_px} px gate; "
                    "the molecules are flagged, not dropped"
                ),
            )
        yield result
        if result.outcome == OUTCOME_FAILED:
            return


def preview_extraction(movie: np.ndarray, **kwargs: Any) -> ExtractionPreview:
    """Run [`iter_extraction_stages`][] to its end and collect the results."""
    return ExtractionPreview(tuple(iter_extraction_stages(movie, **kwargs)))

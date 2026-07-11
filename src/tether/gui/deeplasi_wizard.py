# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Deep-LASI re-analysis wizard — the Qt-free planning state machine (PRD §7.8).

The M7 "New project from Deep-LASI data" workflow (PRD §7.8, goal G8) hands Tether
a folder of legacy files and reconstructs a round-trip-ready project *without
re-extraction*. :mod:`tether.io.intake` does the headless discovery (group files
into acquisitions + cross-check each to its movie); the two importers do the write
(:func:`tether.project.reconstruct.reconstruct_project` for a full round-trip,
:func:`tether.project.analysis_import.import_analysis_only_project` for the degraded
coordinate-less branch). This module is the **brain in between**: it turns a
:class:`~tether.io.intake.DiscoveryResult` into a reviewable, user-confirmable
*execution plan* — one :class:`WizardMode` per acquisition, validated against what
that acquisition's files can actually support.

Like the rest of ``tether.gui`` (cf. :mod:`tether.gui.roundtrip`), the controller is
**Qt-free**: it touches only the discovery dataclasses and the standard library, so
the wizard's decision logic runs in the default test matrix without a display. The
QWizard widget that renders these steps, the executor that decodes each acquisition
and calls the importers, and the shell "Import Deep-LASI bundle…" action are a
follow-up M7 PR that drive this controller through its public surface.

The plan a wizard offers per acquisition (PRD §7.8 "Coordinate sources"):

* **reconstruct** — a full round-trip project. Reconstruction runs *without
  re-extraction*, so it needs the raw movie **and** the ``.mat`` (its pre-integrated
  per-molecule traces); the ``.tdat`` carries coordinates + corrections but **no
  traces**, so a movie + ``.tdat``-only set cannot reconstruct. When the ``.tdat`` is
  present its ``ParticlesColocalized`` supplies the preferred native coordinates, but
  the user may switch to the ``.mat`` coordinates.
* **analysis-only** — the degraded, coordinate-less import (idealization /
  histograms / TDP / kinetics usable; round-trip + patch views disabled). Needs an
  intensity source the analysis-only importer accepts — a tMAVEN **SMD** or a bare
  Deep-LASI **``.txt``**.
* **skip** — excluded from the run (either user-excluded, or a set that can support
  neither path, e.g. a movie with no coordinate/intensity source, or a lone
  coordinate source with no movie to link it and no SMD/``.txt`` to analyze).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import TYPE_CHECKING

from tether.io.intake import (
    AcquisitionFileSet,
    DiscoveryResult,
    MovieRefCheck,
    discover_acquisitions,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path, PurePath

__all__ = [
    "DeepLasiWizard",
    "PlannedAcquisition",
    "WizardError",
    "WizardMode",
    "WizardPlan",
    "WizardSummary",
    "plan_discovery",
]


class WizardError(ValueError):
    """An invalid wizard edit — a mode or coordinate source the files cannot support.

    Raised by the :class:`DeepLasiWizard` mutators (and :meth:`DeepLasiWizard.finalize`)
    rather than silently ignoring the edit, so the widget surfaces the reason to the
    user. A :class:`ValueError` subclass so callers that only catch ``ValueError`` still
    handle it.
    """


class WizardMode(StrEnum):
    """The action planned for one acquisition (PRD §7.8).

    A :class:`~enum.StrEnum` so a mode round-trips through logs / JSON as its value.
    """

    #: Reconstruct a full round-trip ``.tether`` (movie + ``.mat`` traces; the
    #: ``.tdat`` optionally supplies the native coordinates + corrections).
    RECONSTRUCT = "reconstruct"
    #: Import a coordinate-less SMD/``.txt`` source as a degraded analysis-only project.
    ANALYSIS_ONLY = "analysis_only"
    #: Exclude this acquisition from the run.
    SKIP = "skip"


# --------------------------------------------------------------------------- #
# capability probes (what an acquisition's files can support)
# --------------------------------------------------------------------------- #


def _can_reconstruct(fileset: AcquisitionFileSet) -> bool:
    """Whether a full round-trip is possible: a movie **and** the ``.mat``.

    This is deliberately stricter than :attr:`AcquisitionFileSet.round_trip_available`
    (which asks only about *coordinate* availability, movie + ``.tdat``/``.mat``).
    :func:`tether.project.reconstruct.reconstruct_project` reconstructs *without
    re-extraction*, so it needs the pre-integrated per-molecule **traces**, and those
    live only in the ``.mat`` (:class:`~tether.io.deeplasi.DeepLasiExport`) — the
    ``.tdat`` (:class:`~tether.io.tdat.Tdat`) carries coordinates + correction factors
    but **no traces**. So a movie + ``.tdat``-only set has coordinates yet cannot be
    reconstructed; the ``.mat`` is the mandatory trace source, the ``.tdat`` only
    upgrades the coordinate source (and supplies corrections).
    """
    return fileset.has_movie and fileset.mat is not None


def _can_analysis_only(fileset: AcquisitionFileSet) -> bool:
    """Whether an analysis-only import is possible: an SMD or a bare ``.txt``.

    The analysis-only importer accepts only an ``SMDData`` (from an SMD-HDF5) or a
    ``DeepLasiTraces`` (from a ``.txt``); a coordinate source's own traces (``.mat`` /
    ``.tdat``) are *not* an analysis-only input, so a lone coordinate source without a
    movie is not analysis-only-importable.
    """
    return fileset.smd is not None or fileset.txt is not None


def _available_coordinate_sources(fileset: AcquisitionFileSet) -> tuple[str, ...]:
    """The coordinate sources present, ``.tdat`` first (the preferred native source)."""
    sources: list[str] = []
    if fileset.tdat is not None:
        sources.append("tdat")
    if fileset.mat is not None:
        sources.append("mat")
    return tuple(sources)


def _repair_coordinate_source(fileset: AcquisitionFileSet, current: str, mode: WizardMode) -> str:
    """The coordinate source to store for ``mode`` (the shared set_mode/include logic).

    For a non-reconstruct mode the current value is retained verbatim (so a later switch
    back to reconstruct keeps the user's pick). For reconstruct a valid ``current`` is
    kept; an empty/invalid one is repaired to the preferred available source.
    """
    if mode is not WizardMode.RECONSTRUCT:
        return current
    available = _available_coordinate_sources(fileset)
    if current in available:
        return current
    return available[0] if available else ""


def _movie_ref_warning(check: MovieRefCheck | None) -> str | None:
    """The confirm-step warning a movie-reference cross-check contributes, if any.

    A ``"mismatch"`` (grouped movie differs from the export's reference) or a
    ``"movie_absent"`` (the reference names a movie this set lacks) is an actionable
    pairing advisory — its message. ``"confirmed"`` / ``"no_reference"`` contribute none.
    """
    if check is not None and check.status in ("mismatch", "movie_absent"):
        return check.message
    return None


def _no_round_trip_reason(fileset: AcquisitionFileSet) -> str:
    """Why this set cannot round-trip (for the analysis-only / skip rationale)."""
    if not fileset.has_movie and fileset.mat is None:
        return "no movie and no .mat trace source"
    if not fileset.has_movie:
        return "no movie to link the coordinates to pixels"
    return "no .mat trace source (a .tdat alone has coordinates but no traces)"


def _default_runnable_mode(fileset: AcquisitionFileSet) -> WizardMode | None:
    """The best importable mode for a set, or ``None`` when neither importer applies.

    Reconstruct is preferred (a full round-trip), then analysis-only. ``None`` marks a
    blocked set (:func:`_blocked_reason` explains why).
    """
    if _can_reconstruct(fileset):
        return WizardMode.RECONSTRUCT
    if _can_analysis_only(fileset):
        return WizardMode.ANALYSIS_ONLY
    return None


def _blocked_reason(fileset: AcquisitionFileSet) -> str:
    """Why this set supports neither importer (the skip rationale for a blocked set)."""
    present: list[str] = []
    if fileset.has_movie:
        present.append("movie")
    if fileset.tdat is not None:
        present.append(".tdat")
    if fileset.mat is not None:
        present.append(".mat")
    have = ", ".join(present) or "no importable files"
    return (
        f"has {have} but no movie+.mat pair for round-trip and no SMD/.txt "
        "intensity source for analysis-only"
    )


def _default_output_name(fileset: AcquisitionFileSet) -> str:
    """The default ``.tether`` filename for an acquisition (its grouping stem)."""
    return f"{fileset.key}.tether"


# --------------------------------------------------------------------------- #
# the plan (one entry per discovered acquisition)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PlannedAcquisition:
    """One acquisition's planned action, ready for the user to review/confirm (§7.8).

    Built by :func:`plan_discovery` and edited (as new instances) by the
    :class:`DeepLasiWizard` mutators.
    """

    #: The discovered file set this plan acts on.
    fileset: AcquisitionFileSet
    #: The action to take (:class:`WizardMode`).
    mode: WizardMode
    #: Which coordinate source a reconstruction reads (``"tdat"`` | ``"mat"``): the
    #: ``.tdat`` when present (its ``ParticlesColocalized`` is the native registration),
    #: else the ``.mat``. Populated for any reconstruct-capable set and **retained
    #: across mode changes** (so a reconstruct→skip→reconstruct round-trip keeps the
    #: user's pick); read only when :attr:`mode` is reconstruct, and empty ("") for a
    #: set that can never reconstruct.
    coordinate_source: str
    #: The destination ``.tether`` filename (the acquisition stem by default).
    output_name: str
    #: Editable-category-list seeds for the reconstructed condition's vocabulary.
    categories: tuple[str, ...]
    #: A human-readable one-liner explaining the planned mode (for the confirm table).
    rationale: str
    #: Non-fatal advisories carried from discovery + planning (ambiguity, mismatch).
    warnings: tuple[str, ...]
    #: The optional embedded-movie-reference cross-check for this set (annotated by the
    #: widget, which reads the references); ``None`` until annotated.
    movie_ref: MovieRefCheck | None = None

    @property
    def key(self) -> str:
        """The acquisition's grouping stem (its stable identity in the wizard)."""
        return self.fileset.key

    @property
    def runnable(self) -> bool:
        """Whether this acquisition will be imported (any mode other than skip)."""
        return self.mode is not WizardMode.SKIP

    @property
    def is_reconstruct(self) -> bool:
        """Whether the plan is a full round-trip reconstruction."""
        return self.mode is WizardMode.RECONSTRUCT

    @property
    def is_analysis_only(self) -> bool:
        """Whether the plan is a degraded analysis-only import."""
        return self.mode is WizardMode.ANALYSIS_ONLY


def plan_discovery(discovery: DiscoveryResult) -> tuple[PlannedAcquisition, ...]:
    """Propose a default plan for every discovered acquisition (PRD §7.8).

    Round-trip is preferred where possible, then analysis-only, else the set is
    skipped as blocked. The order follows :attr:`DiscoveryResult.acquisitions`
    (sorted by key). Pure — reads no files.
    """
    return tuple(_default_plan(a) for a in discovery.acquisitions)


def _default_plan(fileset: AcquisitionFileSet) -> PlannedAcquisition:
    """The default :class:`PlannedAcquisition` for one file set."""
    warnings = tuple(fileset.warnings)
    output_name = _default_output_name(fileset)
    if _can_reconstruct(fileset):
        source = _available_coordinate_sources(fileset)[0]
        return PlannedAcquisition(
            fileset=fileset,
            mode=WizardMode.RECONSTRUCT,
            coordinate_source=source,
            output_name=output_name,
            categories=(),
            rationale=f"movie + .mat traces + {source} coordinates → full round-trip",
            warnings=warnings,
        )
    if _can_analysis_only(fileset):
        return PlannedAcquisition(
            fileset=fileset,
            mode=WizardMode.ANALYSIS_ONLY,
            coordinate_source="",
            output_name=output_name,
            categories=(),
            rationale=(
                f"{_no_round_trip_reason(fileset)} → analysis-only import "
                "(idealization/histograms/TDP; round-trip disabled)"
            ),
            warnings=warnings,
        )
    reason = _blocked_reason(fileset)
    return PlannedAcquisition(
        fileset=fileset,
        mode=WizardMode.SKIP,
        coordinate_source="",
        output_name=output_name,
        categories=(),
        rationale=f"skipped — {reason}",
        warnings=(*warnings, reason),
    )


# --------------------------------------------------------------------------- #
# the run summary + the finalized plan handed to the executor
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class WizardSummary:
    """A snapshot of the current plan for the review step's status line (PRD §7.8)."""

    n_total: int
    n_reconstruct: int
    n_analysis_only: int
    n_skipped: int
    #: Advisory notes that do not block a run (unpaired SMDs, ignored files).
    advisories: tuple[str, ...]
    #: Reasons the plan cannot run yet (no runnable acquisition, output-name clash);
    #: empty ⇒ ready.
    blocking: tuple[str, ...]

    @property
    def n_runnable(self) -> int:
        """Acquisitions that will be imported (reconstruct + analysis-only)."""
        return self.n_reconstruct + self.n_analysis_only

    @property
    def is_ready(self) -> bool:
        """Whether :meth:`DeepLasiWizard.finalize` will succeed."""
        return self.n_runnable >= 1 and not self.blocking


@dataclass(frozen=True)
class WizardPlan:
    """The finalized, ready-to-run plan handed to the executor (PRD §7.8).

    Produced by :meth:`DeepLasiWizard.finalize`. :attr:`acquisitions` holds only the
    runnable entries; the skipped/unpaired/ignored sets are carried for the run report.
    """

    #: The runnable acquisitions, in review order (reconstruct + analysis-only).
    acquisitions: tuple[PlannedAcquisition, ...]
    #: Acquisitions the user (or the blocked default) excluded from the run.
    skipped: tuple[PlannedAcquisition, ...]
    #: SMD files that matched no acquisition (surfaced, not imported).
    unpaired: tuple[Path, ...]
    #: Unrecognized files preserved by discovery (surfaced, not imported).
    ignored: tuple[Path, ...]
    #: Session/day ``.tmap`` maps found (offered for optional native re-extraction).
    shared_maps: tuple[Path, ...]


# --------------------------------------------------------------------------- #
# the wizard controller (the editable state machine)
# --------------------------------------------------------------------------- #


@dataclass
class DeepLasiWizard:
    """The editable plan behind the Deep-LASI re-analysis wizard (PRD §7.8, M7).

    Wraps a :class:`~tether.io.intake.DiscoveryResult` in a per-acquisition plan the
    user reviews and confirms. Mutators validate every edit against what the
    acquisition's files can support and raise :class:`WizardError` otherwise; the
    widget catches that to show the reason. Acquisitions are addressed by their unique
    grouping ``key`` so edits survive re-ordering.
    """

    discovery: DiscoveryResult
    _plans: list[PlannedAcquisition] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._plans = list(plan_discovery(self.discovery))

    @classmethod
    def from_directory(
        cls, directory: str | PurePath, *, recursive: bool = False
    ) -> DeepLasiWizard:
        """Scan ``directory`` for Deep-LASI acquisitions and build the default plan."""
        return cls(discover_acquisitions(directory, recursive=recursive))

    # -- queries ------------------------------------------------------------ #

    @property
    def plans(self) -> tuple[PlannedAcquisition, ...]:
        """The current per-acquisition plan (review order)."""
        return tuple(self._plans)

    @property
    def runnable(self) -> tuple[PlannedAcquisition, ...]:
        """The acquisitions that will be imported (mode is not skip)."""
        return tuple(p for p in self._plans if p.runnable)

    @property
    def skipped(self) -> tuple[PlannedAcquisition, ...]:
        """The acquisitions excluded from the run (mode is skip)."""
        return tuple(p for p in self._plans if not p.runnable)

    @property
    def advisories(self) -> tuple[str, ...]:
        """Non-blocking notes about files discovery could not import (SMD/unknown)."""
        notes: list[str] = []
        if self.discovery.unpaired:
            notes.append(
                f"{len(self.discovery.unpaired)} SMD file(s) matched no acquisition "
                "and will not be imported"
            )
        if self.discovery.ignored:
            notes.append(f"{len(self.discovery.ignored)} unrecognized file(s) ignored")
        return tuple(notes)

    def _blocking(self) -> tuple[str, ...]:
        """Reasons a run cannot start yet (empty ⇒ ready)."""
        blocking: list[str] = []
        runnable = self.runnable
        if not runnable:
            blocking.append("no acquisition is selected to import")
        for name, keys in self._output_name_collisions().items():
            blocking.append(
                f"output name {name!r} is used by {len(keys)} acquisitions: " + ", ".join(keys)
            )
        return tuple(blocking)

    def _output_name_collisions(self) -> dict[str, list[str]]:
        """Output names (case-insensitive) shared by more than one runnable plan."""
        by_name: dict[str, list[str]] = {}
        for p in self.runnable:
            by_name.setdefault(p.output_name.casefold(), []).append(p.key)
        return {name: keys for name, keys in by_name.items() if len(keys) > 1}

    def summary(self) -> WizardSummary:
        """The current counts + advisories + blocking reasons (for the review step)."""
        modes = [p.mode for p in self._plans]
        return WizardSummary(
            n_total=len(self._plans),
            n_reconstruct=modes.count(WizardMode.RECONSTRUCT),
            n_analysis_only=modes.count(WizardMode.ANALYSIS_ONLY),
            n_skipped=modes.count(WizardMode.SKIP),
            advisories=self.advisories,
            blocking=self._blocking(),
        )

    @property
    def is_ready(self) -> bool:
        """Whether :meth:`finalize` will succeed (≥1 runnable, no output-name clash)."""
        return self.summary().is_ready

    # -- mutators (each validated; raise WizardError on an unsupported edit) - #

    def _locate(self, key: str) -> tuple[int, PlannedAcquisition]:
        for i, p in enumerate(self._plans):
            if p.key == key:
                return i, p
        raise WizardError(f"no acquisition with key {key!r}")

    def set_mode(self, key: str, mode: WizardMode | str) -> PlannedAcquisition:
        """Set an acquisition's mode, validating the files can support it.

        The reconstruction coordinate source is **retained** across mode changes (so a
        reconstruct→skip→reconstruct round-trip keeps the user's pick); switching to
        reconstruct repairs an empty/invalid source to the preferred available one.
        """
        i, p = self._locate(key)
        mode = WizardMode(mode)
        if mode is WizardMode.RECONSTRUCT and not _can_reconstruct(p.fileset):
            raise WizardError(f"{key!r} cannot reconstruct: {_no_round_trip_reason(p.fileset)}")
        if mode is WizardMode.ANALYSIS_ONLY and not _can_analysis_only(p.fileset):
            raise WizardError(
                f"{key!r} cannot import analysis-only: no SMD or .txt intensity source"
            )
        source = _repair_coordinate_source(p.fileset, p.coordinate_source, mode)
        updated = replace(
            p,
            mode=mode,
            coordinate_source=source,
            rationale=f"mode set to {mode.value} by user",
        )
        self._plans[i] = updated
        return updated

    def set_coordinate_source(self, key: str, source: str) -> PlannedAcquisition:
        """Choose which coordinate source a reconstruction reads (``"tdat"``/``"mat"``).

        Allowed for any reconstruct-capable set regardless of its current mode (the pick
        is retained for when the set reconstructs); rejected for a set that can never
        reconstruct.
        """
        i, p = self._locate(key)
        if not _can_reconstruct(p.fileset):
            raise WizardError(
                f"{key!r} is not reconstruct-capable: {_no_round_trip_reason(p.fileset)}"
            )
        available = _available_coordinate_sources(p.fileset)
        if source not in available:
            raise WizardError(
                f"{key!r} has no {source!r} coordinate source (available: "
                f"{', '.join(available) or 'none'})"
            )
        updated = replace(p, coordinate_source=source)
        self._plans[i] = updated
        return updated

    def set_output_name(self, key: str, name: str) -> PlannedAcquisition:
        """Rename an acquisition's destination project (a ``.tether`` suffix is enforced)."""
        i, p = self._locate(key)
        stem = name.strip()
        if not stem:
            raise WizardError(f"output name for {key!r} cannot be empty")
        if not stem.casefold().endswith(".tether"):
            stem = f"{stem}.tether"
        updated = replace(p, output_name=stem)
        self._plans[i] = updated
        return updated

    def set_categories(self, key: str, categories: Sequence[str]) -> PlannedAcquisition:
        """Set the editable-category-list seeds for a reconstructed condition."""
        i, p = self._locate(key)
        updated = replace(p, categories=tuple(categories))
        self._plans[i] = updated
        return updated

    def exclude(self, key: str) -> PlannedAcquisition:
        """Drop an acquisition from the run (set its mode to skip)."""
        return self.set_mode(key, WizardMode.SKIP)

    def include(self, key: str) -> PlannedAcquisition:
        """Re-include a **skipped** acquisition at its default runnable mode.

        Only a skipped plan can be included: calling this on an already-runnable plan
        raises :class:`WizardError`, so a stray toggle never destructively resets a
        user-customized plan. Also raises for a blocked set (one that supports neither
        importer). Everything the user set — output name, categories, coordinate source,
        movie-reference annotation, warnings — is preserved; only the mode (and its
        rationale) is restored.
        """
        i, p = self._locate(key)
        if p.mode is not WizardMode.SKIP:
            raise WizardError(f"{key!r} is already included (mode={p.mode.value})")
        mode = _default_runnable_mode(p.fileset)
        if mode is None:
            raise WizardError(f"{key!r} cannot be included: {_blocked_reason(p.fileset)}")
        source = _repair_coordinate_source(p.fileset, p.coordinate_source, mode)
        updated = replace(
            p,
            mode=mode,
            coordinate_source=source,
            rationale=f"re-included as {mode.value}",
        )
        self._plans[i] = updated
        return updated

    def annotate_movie_ref(self, key: str, check: MovieRefCheck) -> PlannedAcquisition:
        """Attach the embedded-movie-reference cross-check to an acquisition (§7.8).

        The widget reads the ``.tdat``/``.mat`` references (file I/O the controller
        avoids) and pushes the :class:`~tether.io.intake.MovieRefCheck` here. A
        ``"mismatch"`` / ``"movie_absent"`` adds an actionable confirm-step warning
        without changing the mode (the user decides). Re-annotating **replaces** the
        previous movie-ref warning rather than accumulating — so a resolved mismatch
        (re-annotated ``"confirmed"``) clears the stale advisory.
        """
        i, p = self._locate(key)
        stale = _movie_ref_warning(p.movie_ref)
        warnings = [w for w in p.warnings if w != stale] if stale is not None else list(p.warnings)
        fresh = _movie_ref_warning(check)
        if fresh is not None and fresh not in warnings:
            warnings.append(fresh)
        updated = replace(p, movie_ref=check, warnings=tuple(warnings))
        self._plans[i] = updated
        return updated

    def finalize(self) -> WizardPlan:
        """Freeze the reviewed plan into a :class:`WizardPlan` for the executor.

        Raises :class:`WizardError` when the plan is not ready (no runnable acquisition,
        or an output-name collision).
        """
        summary = self.summary()
        if not summary.is_ready:
            reason = "; ".join(summary.blocking) or "no runnable acquisitions"
            raise WizardError(f"plan is not ready to run: {reason}")
        return WizardPlan(
            acquisitions=self.runnable,
            skipped=self.skipped,
            unpaired=self.discovery.unpaired,
            ignored=self.discovery.ignored,
            shared_maps=self.discovery.shared_maps,
        )

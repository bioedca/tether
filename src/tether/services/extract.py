# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The ``extract`` service command: one movie to a new ``.tether`` project.

[`ExtractRequest`][] holds what ``tether extract`` takes on its command line, with the
same defaults, and every extraction option of an extraction profile
([`tether.services.profile`][], #349); [`run`][] passes it to
[`tether.project.extract.extract_movie`][].
The imaging and HDF5 stack is imported when the command runs, not when this module is
imported (ADR-0100).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import TYPE_CHECKING, ClassVar

from tether.services.base import CommandFailed, Request
from tether.services.profile import ExtractionFields

if TYPE_CHECKING:  # pragma: no cover - typing only
    from tether.project.extract import ExtractionSummary

__all__ = ["ExtractRequest", "ExtractResult", "run"]


@dataclasses.dataclass(frozen=True, kw_only=True)
class ExtractRequest(ExtractionFields, Request):
    """Extract traces from one dual-channel movie into a new ``.tether`` project.

    The fields are the options of ``tether extract`` (``docs/cli.md``) under their ``argparse``
    destination names, with the same defaults, and the
    [`ExtractionFields`][tether.services.profile.ExtractionFields]. The channel layout fields
    and seven others have no flag of their own (``prealign_upsample``, ``prealign_low_sigma``,
    ``prealign_high_sigma``, ``disk_radius``, ``ring_inner``, ``ring_outer``, ``bg_window``):
    ``tether extract --profile`` sets them. ``detection_mode=None`` means "no explicit choice":
    the library default applies, or the mode decoded from ``tdat``. ``count_only`` runs the
    stages up to colocalization, writes nothing and returns the molecule count of each
    colocalization rule (#920). ``map_path`` is a Tether ``.npz`` map file (``--map``); it and
    ``tmap`` are the two ways to replace the native fit, and passing both is refused (#353).

    The extraction fields ``donor_side``, ``channel_layout``, ``channel_layout_donor``,
    ``channel_layout_donor_rect``, ``channel_layout_acceptor_rect``, ``detection_mode``,
    ``detection_threshold``, ``detection_block``, ``min_separation``, ``prealign``,
    ``prealign_upsample``, ``prealign_low_sigma``, ``prealign_high_sigma``, ``pair_tol``,
    ``rms_gate``, ``window``, ``coloc_distance``, ``colocalization``, ``disk_radius``,
    ``ring_inner``, ``ring_outer`` and ``bg_window`` are inherited from
    [`ExtractionFields`][tether.services.profile.ExtractionFields]. The
    ``Attributes`` section of that class gives the meaning and the unit of each; the
    section below names the fields of this class.

    Attributes
    ----------
    movie
        Path to the dual-channel TIFF movie.
    output
        Path to the ``.tether`` project to create.
    overwrite
        Overwrite an existing output project.
    tmap
        Path to an imported Deep-LASI ``.tmap`` to apply instead of a native fit, or
        ``None``.
    tdat
        Path to a Deep-LASI ``.tdat`` whose particle-detection mode is applied, or ``None``.
    map_path
        Path to a saved Tether ``.npz`` map file to apply instead of a native fit, or ``None``.
    count_only
        Count the molecules of each colocalization rule and write nothing.
    """

    COMMAND: ClassVar[str] = "extract"
    VERSION: ClassVar[int] = 1

    movie: str
    output: str
    overwrite: bool = False
    tmap: str | None = None
    tdat: str | None = None
    map_path: str | None = None
    count_only: bool = False


@dataclasses.dataclass(frozen=True)
class ExtractResult:
    """The outcome of an ``extract`` command.

    ``summary`` is the core's extraction summary, or ``None`` for a ``count_only``
    request, which writes nothing. ``rule_counts`` is ``{rule: n_molecules}``, the
    molecules each colocalization rule keeps from the movie's spots (#920).
    """

    summary: ExtractionSummary | None
    rule_counts: Mapping[str, int] = dataclasses.field(default_factory=dict)


def run(request: ExtractRequest) -> ExtractResult:
    """Run *request*; raise [`CommandFailed`][tether.services.base.CommandFailed] on a user error.

    The message of the error is the message of the core's
    [`ExtractionError`][tether.project.extract.ExtractionError], of a
    [`LockError`][tether.project.lock.LockError] or of an `OSError`, unchanged.
    """
    from contextlib import nullcontext

    from tether.project.extract import ExtractionError, count_molecules_by_rule, extract_movie
    from tether.project.lock import LockError, publish_lock

    try:
        # The detection mode and threshold, and a .tdat, are two ways to set the same
        # detection settings; combining them is ambiguous (which wins?), so refuse it
        # rather than silently letting one override the other.
        if request.tdat is not None and request.sets_detection():
            raise ExtractionError(
                "--detection-mode/--detection-threshold cannot be combined with --tdat "
                "(the .tdat supplies the detection mode); pass one or the other"
            )
        # None tells "no explicit choice" from a choice; extract_options resolves it to
        # the library default. With a .tdat, extract_movie overrides it with the decoded mode.
        options = request.extract_options()
        if request.count_only:
            counts = count_molecules_by_rule(
                request.movie,
                options=options,
                tmap=request.tmap,
                tdat=request.tdat,
                map_path=request.map_path,
            )
            return ExtractResult(summary=None, rule_counts=counts)
        # An overwrite replaces a project another writer may hold open, so it holds the
        # destination's writer lock across the run and publishes under it (#346).
        with publish_lock(request.output) if request.overwrite else nullcontext() as guard:
            summary = extract_movie(
                request.movie,
                request.output,
                options=options,
                tmap=request.tmap,
                tdat=request.tdat,
                map_path=request.map_path,
                overwrite=request.overwrite,
                publish_guard=guard,
            )
    except (ExtractionError, LockError, OSError) as exc:
        # OSError: publish_lock opens the destination's guard file before
        # extract_movie runs, so a missing or unwritable directory fails there.
        raise CommandFailed(str(exc)) from exc
    return ExtractResult(summary=summary, rule_counts=dict(summary.rule_counts))

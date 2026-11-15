# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The ``extract`` service command: one movie to a new ``.tether`` project.

:class:`ExtractRequest` holds what ``tether extract`` takes on its command line, with the
same defaults; :func:`run` passes it to :func:`tether.project.extract.extract_movie`.
The imaging and HDF5 stack is imported when the command runs, not when this module is
imported (ADR-0100).
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, ClassVar

from tether.services.base import CommandFailed, Request

if TYPE_CHECKING:  # pragma: no cover - typing only
    from tether.project.extract import ExtractionSummary

__all__ = ["ExtractRequest", "ExtractResult", "run"]


@dataclasses.dataclass(frozen=True, kw_only=True)
class ExtractRequest(Request):
    """Extract traces from one dual-channel movie into a new ``.tether`` project.

    The fields are the options of ``tether extract`` (``docs/cli.md``) under their
    ``argparse`` destination names, with the same defaults. ``detection_mode=None`` means
    "no explicit choice": the library default applies, or the mode decoded from ``tdat``.
    """

    COMMAND: ClassVar[str] = "extract"
    VERSION: ClassVar[int] = 1

    movie: str
    output: str
    overwrite: bool = False
    donor_side: str = "left"
    detection_mode: str | None = None
    detection_threshold: float | None = None
    window: int = 21
    min_separation: float | None = None
    detection_block: int = 50
    prealign: str = "translation"
    pair_tol: float = 2.0
    coloc_distance: float = 3.0
    rms_gate: float = 0.5
    tmap: str | None = None
    tdat: str | None = None


@dataclasses.dataclass(frozen=True)
class ExtractResult:
    """The outcome of an ``extract`` command: the core's extraction summary."""

    summary: ExtractionSummary


def run(request: ExtractRequest) -> ExtractResult:
    """Run *request*; raise :class:`~tether.services.base.CommandFailed` on a user error.

    The message of the error is the message of the core's
    :class:`~tether.project.extract.ExtractionError`, unchanged.
    """
    from tether.project.extract import ExtractionError, ExtractOptions, extract_movie

    try:
        # The detection mode and threshold, and a .tdat, are two ways to set the same
        # detection settings; combining them is ambiguous (which wins?), so refuse it
        # rather than silently letting one override the other.
        if request.tdat is not None and (
            request.detection_mode is not None or request.detection_threshold is not None
        ):
            raise ExtractionError(
                "--detection-mode/--detection-threshold cannot be combined with --tdat "
                "(the .tdat supplies the detection mode); pass one or the other"
            )
        options = ExtractOptions(
            donor_side=request.donor_side,
            # None tells "no explicit choice" from a choice; resolve it to the library
            # default here. With a .tdat, extract_movie overrides it with the decoded mode.
            detection_mode="wavelet" if request.detection_mode is None else request.detection_mode,
            detection_threshold=request.detection_threshold,
            window=request.window,
            min_separation=request.min_separation,
            detection_block=request.detection_block,
            prealign=request.prealign,
            pair_tol=request.pair_tol,
            coloc_distance=request.coloc_distance,
            rms_gate=request.rms_gate,
        )
        summary = extract_movie(
            request.movie,
            request.output,
            options=options,
            tmap=request.tmap,
            tdat=request.tdat,
            overwrite=request.overwrite,
        )
    except ExtractionError as exc:
        raise CommandFailed(str(exc)) from exc
    return ExtractResult(summary=summary)

# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The extraction profile: every extraction option, saved as one file (#349, ADR-0127).

An *extraction profile* holds the eighteen fields of
[`ExtractOptions`][tether.project.extract.ExtractOptions], so a run that names one is fully
described by it. Seventeen are one value each; the channel layout (#908) is four fields,
``channel_layout`` (the kind) and ``channel_layout_donor``, ``channel_layout_donor_rect`` and
``channel_layout_acceptor_rect``, because a request field holds one plain value.
``tether extract --profile`` and ``tether batch --profile`` read one, and
[`ExtractionFields`][] is the same set of fields on the ``extract`` and ``batch`` requests, so
a request (and a recipe, #899) carries every value the profile gave.

A profile is a file the user names, not an entry in the application settings store
(ADR-0095): it is an input of an analysis, so it travels with the data and the recipe
instead of with one computer's preferences (ADR-0127).

Its text form is the request text form of ADR-0100 (``command`` is
``"extraction-profile"``)::

    {"command":"extraction-profile","fields":{...},"version":1}

[`write_profile`][] writes every field. [`read_profile`][] takes a missing field at its
default, so a profile written by hand may name only the fields it changes, and it refuses
an unknown field, a value of the wrong type, and a value [`ExtractOptions`][] refuses.

``detection_mode`` and ``detection_threshold`` may be ``null``: no choice, so the library
default applies, or the values decoded from a ``.tdat``. A ``.tdat`` sets those two fields
and no other, and it is refused with a profile that sets either of them.

>>> profile = ExtractionProfile(window=23, disk_radius=2.5)
>>> profile_from_text(profile_to_text(profile)) == profile
True

This module imports only the standard library; [`ExtractionFields.extract_options`][]
imports the extraction core when it is called.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from tether.services.base import Request, RequestError, request_from_mapping, request_to_text

if TYPE_CHECKING:  # pragma: no cover - typing only
    import os

    from tether.project.extract import ExtractOptions

__all__ = [
    "DETECTION_FIELDS",
    "LAYOUT_FIELDS",
    "PROFILE_COMMAND",
    "ExtractionFields",
    "ExtractionProfile",
    "profile_from_text",
    "profile_to_text",
    "read_profile",
    "write_profile",
]

#: The ``command`` key of a profile's text form. A profile is not a command; the key names
#: the kind of object, so a reader of a recipe can tell it from a request.
PROFILE_COMMAND = "extraction-profile"

#: The two fields a ``.tdat`` supplies (``ParticleDetectionMode``, ``DetectionThreshold``).
DETECTION_FIELDS: tuple[str, ...] = ("detection_mode", "detection_threshold")

#: The profile fields that hold ``ExtractOptions.channel_layout`` (a
#: [`ChannelLayout`][tether.imaging.layout.ChannelLayout]): its kind, donor and rectangles.
LAYOUT_FIELDS: tuple[str, ...] = (
    "channel_layout",
    "channel_layout_donor",
    "channel_layout_donor_rect",
    "channel_layout_acceptor_rect",
)


@dataclasses.dataclass(frozen=True, kw_only=True)
class ExtractionFields:
    """The extraction options of a profile, an extract and a batch request.

    The names and defaults are those of
    [`ExtractOptions`][tether.project.extract.ExtractOptions], and a test checks that they
    agree field by field. Two differ. ``detection_mode`` is ``None`` here for "no explicit
    choice", which tells a choice from the default, so that a ``.tdat`` can be refused
    alongside a choice and applied without one. ``ExtractOptions.channel_layout`` is the
    four [`LAYOUT_FIELDS`][]: ``channel_layout`` is the layout kind (``side-by-side``,
    ``top-bottom`` or ``rectangles``; ``None`` for the split ``donor_side`` describes),
    and the other three are the
    [`ChannelLayout`][tether.imaging.layout.ChannelLayout] fields ``donor``,
    ``donor_rect`` and ``acceptor_rect``, each ``[y1, x1, y2, x2]`` (1-based, inclusive).
    """

    donor_side: str = "left"
    channel_layout: str | None = None
    channel_layout_donor: str | None = None
    channel_layout_donor_rect: tuple[int, ...] | None = None
    channel_layout_acceptor_rect: tuple[int, ...] | None = None
    detection_mode: str | None = None
    detection_threshold: float | None = None
    detection_block: int = 50
    min_separation: float | None = None
    prealign: str = "translation"
    prealign_upsample: int = 10
    prealign_low_sigma: float = 1.0
    prealign_high_sigma: float = 20.0
    pair_tol: float = 2.0
    rms_gate: float = 0.5
    window: int = 21
    coloc_distance: float = 3.0
    disk_radius: float = 3.0
    ring_inner: float = 6.0
    ring_outer: float = 8.0
    bg_window: int = 10

    def extraction_values(self) -> dict[str, Any]:
        """Return the extraction fields of this object by name."""
        return {f.name: getattr(self, f.name) for f in dataclasses.fields(ExtractionFields)}

    def sets_detection(self) -> bool:
        """Return whether ``detection_mode`` or ``detection_threshold`` holds a choice."""
        return any(getattr(self, name) is not None for name in DETECTION_FIELDS)

    def extract_options(self) -> ExtractOptions:
        """Build the [`ExtractOptions`][tether.project.extract.ExtractOptions] these fields name.

        ``detection_mode=None`` becomes the library default, ``"wavelet"``; with a ``.tdat``,
        [`extract_movie`][tether.project.extract.extract_movie] then replaces it with the
        decoded mode. The four layout fields become one
        [`ChannelLayout`][tether.imaging.layout.ChannelLayout], or ``None`` when
        ``channel_layout`` is ``None``. Raises
        [`ExtractionError`][tether.project.extract.ExtractionError] for a value the options
        or the layout refuse.
        """
        from tether.imaging.layout import ChannelLayout, ChannelLayoutError
        from tether.project.extract import ExtractionError, ExtractOptions

        values = self.extraction_values()
        if values["detection_mode"] is None:
            values["detection_mode"] = "wavelet"
        kind, donor, donor_rect, acceptor_rect = (values.pop(name) for name in LAYOUT_FIELDS)
        layout = None
        if kind is None:
            if (donor, donor_rect, acceptor_rect) != (None, None, None):
                raise ExtractionError(
                    "channel_layout_donor, channel_layout_donor_rect and "
                    "channel_layout_acceptor_rect need a channel_layout kind"
                )
        else:
            try:
                layout = ChannelLayout(
                    kind=kind, donor=donor, donor_rect=donor_rect, acceptor_rect=acceptor_rect
                )
            except ChannelLayoutError as exc:
                raise ExtractionError(f"channel_layout: {exc}") from exc
        return ExtractOptions(channel_layout=layout, **values)


@dataclasses.dataclass(frozen=True, kw_only=True)
class ExtractionProfile(ExtractionFields, Request):
    """An extraction profile: the [`ExtractionFields`][] and nothing else.

    It is a [`Request`][tether.services.base.Request] only for its text form; no service
    command runs it, and the registry refuses its text as a request.
    """

    COMMAND: ClassVar[str] = PROFILE_COMMAND
    VERSION: ClassVar[int] = 1

    @classmethod
    def from_options(cls, options: ExtractOptions) -> ExtractionProfile:
        """Return the profile of an [`ExtractOptions`][tether.project.extract.ExtractOptions]."""
        names = {f.name for f in dataclasses.fields(ExtractionFields)} - set(LAYOUT_FIELDS)
        values: dict[str, Any] = {name: getattr(options, name) for name in names}
        layout = options.channel_layout
        if layout is not None:
            values.update(
                channel_layout=layout.kind,
                channel_layout_donor=layout.donor,
                channel_layout_donor_rect=layout.donor_rect,
                channel_layout_acceptor_rect=layout.acceptor_rect,
            )
        return cls(**values)


def profile_to_text(profile: ExtractionProfile) -> str:
    """Return the one-line text form of *profile*, with every field."""
    return request_to_text(profile)


def profile_from_text(text: str) -> ExtractionProfile:
    """Read a profile from its text form; raise [`RequestError`][tether.services.base.RequestError].

    The text form is checked, not the values: [`read_profile`][] also checks those.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RequestError(f"an extraction profile is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("command") != PROFILE_COMMAND:
        raise RequestError(
            f"an extraction profile is a JSON object with command {PROFILE_COMMAND!r}"
        )
    profile = request_from_mapping(data, ExtractionProfile)
    assert isinstance(profile, ExtractionProfile)  # noqa: S101 - narrows the type
    return profile


def read_profile(path: str | os.PathLike[str]) -> ExtractionProfile:
    """Read and check the profile file at *path*.

    Raises [`RequestError`][tether.services.base.RequestError], with the file's path in the
    message, when the file cannot be read, is not a profile, or holds a value
    [`ExtractOptions`][tether.project.extract.ExtractOptions] refuses.
    """
    from tether.project.extract import ExtractionError

    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RequestError(f"cannot read extraction profile {path}: {exc}") from exc
    try:
        profile = profile_from_text(text)
        profile.extract_options()
    except (RequestError, ExtractionError) as exc:
        raise RequestError(f"extraction profile {path}: {exc}") from exc
    return profile


def write_profile(profile: ExtractionProfile, path: str | os.PathLike[str]) -> None:
    """Write *profile* to *path* as its text form and a final newline."""
    Path(path).write_text(profile_to_text(profile) + "\n", encoding="utf-8")

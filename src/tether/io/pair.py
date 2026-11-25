# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Donor and acceptor in two files: two movies as one acquisition (#909, plan C9 of #890).

A setup with two cameras writes one movie for the donor and one for the acceptor (G02
note 01, "separate donor/acceptor files"). Tether reads such a **movie pair** as one
acquisition with two channels: each file is one whole channel, so the donor channel is
every pixel of the donor movie and the acceptor channel every pixel of the acceptor
movie. The stages after the split (detection, registration, colocalization, the traces)
are the ones a one-file movie runs, so the later stages see one acquisition.

[`check_pair`][] refuses a pair whose two movies cannot be one acquisition. The frame
count, the frame time and the pixel type (such as ``uint16``) must be the same, and so
must the frame size, which the registration's prealign needs (both channel
images must have one shape). Tether does not align two movies in time: a pair with a
different frame count or frame time is refused, never interpolated.

The pair is stored as one ``/movies`` row. Its ``sha256`` is [`pair_sha256`][] of the
two files' checksums, so the row names both files' bytes and the molecule keys of a
pair never equal those of either file read alone. The two files are stored in the
source registry (``/sources``, #912) with role ``movie``, the pair's ``movie_id`` and
the acquisition ``{"pair_role": "donor"}`` or ``{"pair_role": "acceptor"}``
([`tether.project.sources`][]).
"""

from __future__ import annotations

import hashlib
import math
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from tether.io.frames import ExtractionMovie
    from tether.io.movie import MovieReader

__all__ = [
    "PAIR_ROLE_KEY",
    "PAIR_ROLES",
    "MoviePairError",
    "check_pair",
    "pair_sha256",
]

#: The two roles of the files of a movie pair, in the order of the channels.
PAIR_ROLES = ("donor", "acceptor")

#: The key of a pair file's role in its source record's ``acquisition``.
PAIR_ROLE_KEY = "pair_role"

_PAIR_TAG = b"tether-movie-pair-v1\0"


class MoviePairError(ValueError):
    """Two movies that cannot be one acquisition; the message names what differs."""


def _frame_time(value: float | None) -> str:
    return "unknown" if value is None else f"{value:g} s"


def check_pair(
    donor: ExtractionMovie | MovieReader,
    acceptor: ExtractionMovie | MovieReader,
    *,
    donor_name: str = "the donor movie",
    acceptor_name: str = "the acceptor movie",
) -> None:
    """Refuse two open movies that cannot be the donor and acceptor of one acquisition.

    Parameters
    ----------
    donor, acceptor : ExtractionMovie or MovieReader
        The open donor and acceptor movies.
    donor_name, acceptor_name : str, optional
        The names the message gives the two files.

    Raises
    ------
    MoviePairError
        If the frame count, the frame time (a known time against another or against an
        unknown one), the pixel type or the frame size of the two movies differ. The byte
        order may differ: each file is read in its own. The message names every value that differs.
    """
    problems = []
    if donor.shape[0] != acceptor.shape[0]:
        problems.append(f"frame count {donor.shape[0]} and {acceptor.shape[0]}")
    t_donor, t_acceptor = donor.frame_time, acceptor.frame_time
    if (t_donor is None) != (t_acceptor is None) or (
        t_donor is not None
        and t_acceptor is not None
        and not math.isclose(t_donor, t_acceptor, rel_tol=1e-9, abs_tol=0.0)
    ):
        problems.append(f"frame time {_frame_time(t_donor)} and {_frame_time(t_acceptor)}")
    pixel_donor, pixel_acceptor = np.dtype(donor.dtype).name, np.dtype(acceptor.dtype).name
    if pixel_donor != pixel_acceptor:
        problems.append(f"pixel type {pixel_donor} and {pixel_acceptor}")
    if donor.shape[1:] != acceptor.shape[1:]:
        problems.append(
            f"frame size {donor.shape[1]} x {donor.shape[2]} and "
            f"{acceptor.shape[1]} x {acceptor.shape[2]} px"
        )
    if problems:
        raise MoviePairError(
            f"{donor_name} and {acceptor_name} cannot be one acquisition: they differ in "
            f"{'; '.join(problems)} (donor first). Tether does not align two movies in time "
            "or size; give two movies of the same acquisition"
        )


def pair_sha256(donor_sha256: str, acceptor_sha256: str) -> str:
    """Return the content checksum of a movie pair: one SHA-256 of both files' checksums.

    The order is part of the identity, so a pair with the roles exchanged is another
    acquisition. The tag in the hashed bytes keeps the value from ever being the
    checksum of one file.

    Examples
    --------
    >>> a, b = "0" * 64, "f" * 64
    >>> pair_sha256(a, b) == pair_sha256(a, b) != pair_sha256(b, a)
    True
    """
    digest = hashlib.sha256(_PAIR_TAG)
    digest.update(
        donor_sha256.lower().encode("ascii") + b"\0" + acceptor_sha256.lower().encode("ascii")
    )
    return digest.hexdigest()

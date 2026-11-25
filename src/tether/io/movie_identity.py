# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""A movie's content checksum covers every file that holds its frames (#1398, ADR-0153).

A movie's checksum (``/movies.sha256``, the source registry's checksum) is its identity: it
seeds every ``molecule_key``, and ``tether batch --into`` uses it to find a movie that is
already in a project. For most containers the frames are in the movie file, and the checksum
is the SHA-256 of that file. A cellSens ``.vsi`` file holds only the metadata: its frames are
in the ``.ets`` files in the ``_<name>_`` folder beside it (ADR-0147). Its checksum must
therefore cover those files too, or a changed ``.ets`` file would leave the checksum as it
was, and a project would keep the traces of the old frames.

[`frame_files`][] gives the ``.ets`` files of a ``.vsi`` movie, and
[`content_sha256`][] gives the checksum from the movie file's own SHA-256 and those files.
A movie with no frame files keeps the SHA-256 of its file, so the checksum of every other
container is unchanged. Only the checksum changes: the size and modification time recorded
for a movie are still the movie file's own.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["VSI_FILES_TAG", "content_sha256", "frame_files", "frame_folder"]

#: The bytes that start the hashed text of a ``.vsi`` movie's checksum. Like the pair tag
#: of [`tether.io.pair.pair_sha256`][], it keeps the value from ever being the checksum of
#: one file.
VSI_FILES_TAG = b"tether-vsi-frame-files-v1\0"

_CHUNK = 1 << 20  # 1 MiB, the block size of the other movie checksums


def frame_folder(path: str | Path) -> Path | None:
    """Return the ``_<name>_`` folder that holds a ``.vsi`` movie's frames, or ``None``.

    ``None`` for a file that is not a ``.vsi`` (in any case), or a ``.vsi`` with no such
    folder beside it.
    """
    path = Path(path)
    if path.suffix.lower() != ".vsi":
        return None
    folder = path.with_name(f"_{path.stem}_")
    return folder if folder.is_dir() else None


def frame_files(path: str | Path) -> tuple[Path, ...]:
    """Return the ``.ets`` files that hold a ``.vsi`` movie's frames, in their checksum order.

    The files are those at any depth of the movie's ``_<name>_`` folder ([`frame_folder`][]),
    sorted by their path in that folder, written with ``/``. Other movies give ``()``.
    """
    folder = frame_folder(path)
    if folder is None:
        return ()
    found = (p for p in folder.rglob("*") if p.suffix.lower() == ".ets" and p.is_file())
    return tuple(sorted(found, key=lambda p: p.relative_to(folder).as_posix()))


def content_sha256(
    path: str | Path,
    file_sha256: str,
    *,
    frames: tuple[Path, ...] | None = None,
    on_block: Callable[[int], None] | None = None,
) -> str:
    """Return the content checksum of the movie at ``path``, whose own SHA-256 is ``file_sha256``.

    Without frame files ([`frame_files`][]) this is ``file_sha256``. A ``.vsi`` movie with
    frame files gets one SHA-256 of [`VSI_FILES_TAG`][], ``file_sha256`` and, for each
    frame file in order, its path in the ``_<name>_`` folder and its SHA-256. The folder's
    name is not in it, so a movie whose ``.vsi`` and folder are renamed together keeps its
    checksum, as a renamed TIFF does.

    ``frames`` is the result of [`frame_files`][] when the caller already has it.
    ``on_block(n)`` is called after each block of ``n`` bytes of a frame file is read (a
    caller checks a cancel there and reports progress).
    """
    if frames is None:
        frames = frame_files(path)
    if not frames:
        return file_sha256.lower()
    folder = frame_folder(path)
    if folder is None:  # the folder went away after the frames were listed
        raise FileNotFoundError(f"the frame folder of {Path(path).name} is gone")
    digest = hashlib.sha256(VSI_FILES_TAG)
    digest.update(file_sha256.lower().encode("ascii"))
    for frame in frames:
        name = frame.relative_to(folder).as_posix()
        digest.update(b"\0" + name.encode("utf-8") + b"\0" + _sha256(frame, on_block))
    return digest.hexdigest()


def _sha256(path: Path, on_block: Callable[[int], None] | None) -> bytes:
    """Return the SHA-256 of the file at ``path`` as ASCII hex, read in 1 MiB blocks."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(block)
            if on_block is not None:
                on_block(len(block))
    return digest.hexdigest().encode("ascii")

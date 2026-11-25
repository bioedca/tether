# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The movie reader interface and the adapter registry (PRD §7.1 FR-EXTRACT, Appendix A).

Every movie container that Tether reads is an *adapter*: a [`MovieAdapter`][] that
recognizes a file by its extension and its first bytes, and opens it as a
[`MovieSource`][]. A source gives the geometry, the pixel type and the frame count,
reads a range of frames without a load of the full movie, and gives the
acquisition metadata, each value with its origin ([`MetadataValue`][]).

[`select_adapter`][] chooses the adapter for a path and [`open_movie_source`][]
opens it. **The content decides, and the extension only breaks a tie**: a TIFF
saved as ``movie.dat`` still opens as a TIFF, and a file named ``movie.tif``
whose first bytes are not a TIFF header is refused. A container that no adapter
reads is refused with [`UnsupportedContainerError`][], whose message lists the
containers that Tether reads and names the Bio-Formats route.

This module is Qt-free and imports no reader package at import time; the
built-in adapters, TIFF ([`tether.io.movie.TIFF_ADAPTER`][]), then CZI
([`tether.io.czi.CZI_ADAPTER`][], ADR-0144), ND2
([`tether.io.nd2.ND2_ADAPTER`][], ADR-0148), SIF
([`tether.io.sif.SIF_ADAPTER`][], ADR-0150), then cellSens VSI
([`tether.io.bioformats.BIOFORMATS_ADAPTER`][], the optional Bio-Formats stack of
ADR-0147), are registered the first time the registry is read. ADR-0113 records the
interface and the rule by which a reader package can enter the base lock.
"""

from __future__ import annotations

import dataclasses
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Generic, Literal, Protocol, TypeVar, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable
    from os import PathLike
    from types import TracebackType

    import numpy as np

__all__ = [
    "ORIGINS",
    "InvalidPixelError",
    "LossyCodecError",
    "MetadataValue",
    "MovieAdapter",
    "MovieMetadata",
    "MovieSource",
    "UnsupportedContainerError",
    "open_movie_source",
    "register_adapter",
    "registered_adapters",
    "select_adapter",
]

T = TypeVar("T")

Origin = Literal["file", "user", "unknown"]
ORIGINS: tuple[Origin, ...] = ("file", "user", "unknown")
"""The three origins of a metadata value: read from the file, given by the user, or unknown."""

# Enough for every magic number an adapter checks (TIFF and BigTIFF need 4 bytes).
_SNIFF_BYTES = 16


class UnsupportedContainerError(ValueError):
    """The file is not a movie container that a registered adapter reads."""


class LossyCodecError(ValueError):
    """The frames use a codec that can be lossy, so they may not hold the acquired values."""


class InvalidPixelError(ValueError):
    """The pixels are not one real intensity sample each (for example RGB or a palette)."""


@dataclass(frozen=True)
class MetadataValue(Generic[T]):
    """One metadata value and its origin.

    ``value`` is ``None`` exactly when ``origin`` is ``"unknown"``; the
    constructors [`from_file`][], [`from_user`][] and [`unknown`][] keep that
    invariant, and the direct constructor checks it.
    """

    value: T | None
    origin: Origin

    def __post_init__(self) -> None:
        """Refuse an origin that is not one of [`ORIGINS`][], or a value that contradicts it."""
        if self.origin not in ORIGINS:
            raise ValueError(f"metadata origin must be one of {ORIGINS}, got {self.origin!r}")
        if (self.value is None) != (self.origin == "unknown"):
            raise ValueError(
                "a metadata value is None exactly when its origin is 'unknown'; "
                f"got value={self.value!r} with origin={self.origin!r}"
            )

    @classmethod
    def from_file(cls, value: T) -> MetadataValue[T]:
        """Make a value read from the movie file."""
        return cls(value, "file")

    @classmethod
    def from_user(cls, value: T) -> MetadataValue[T]:
        """Make a value that the user gave."""
        return cls(value, "user")

    @classmethod
    def unknown(cls) -> MetadataValue[T]:
        """Make the unknown value: the file does not carry it and the user did not give it."""
        return cls(None, "unknown")


@dataclass(frozen=True)
class MovieMetadata:
    """The acquisition metadata of a movie; each field is a [`MetadataValue`][].

    ``frame_time`` is seconds per frame, ``pixel_size`` is micrometres per pixel,
    and ``channels`` is the channel names in file order.
    """

    frame_time: MetadataValue[float] = dataclasses.field(default_factory=MetadataValue.unknown)
    pixel_size: MetadataValue[float] = dataclasses.field(default_factory=MetadataValue.unknown)
    channels: MetadataValue[tuple[str, ...]] = dataclasses.field(
        default_factory=MetadataValue.unknown
    )

    def with_user(self, **values: object) -> MovieMetadata:
        """Return a copy in which each named field holds a user-given value.

        ``metadata.with_user(frame_time=0.1)`` replaces the frame time, whatever its
        origin was, with ``0.1`` of origin ``"user"``. An unknown field name raises
        `TypeError`.
        """
        names = {f.name for f in dataclasses.fields(self)}
        unknown = sorted(set(values) - names)
        if unknown:
            raise TypeError(f"unknown metadata field(s): {', '.join(unknown)}")
        return dataclasses.replace(
            self, **{name: MetadataValue.from_user(v) for name, v in values.items()}
        )


@runtime_checkable
class MovieSource(Protocol):
    """An open movie, as every adapter gives it.

    ``read_frames(start, stop)`` returns frames ``start`` to ``stop - 1`` as a
    ``(stop - start, height, width)`` array and must not load the full movie.
    A source is a context manager; [`close`][] is idempotent, and a read after it
    raises `ValueError`.
    """

    @property
    def path(self) -> Path:
        """The movie's filesystem path."""
        ...

    @property
    def shape(self) -> tuple[int, int, int]:
        """``(n_frames, height, width)``."""
        ...

    @property
    def n_frames(self) -> int:
        """The number of frames."""
        ...

    @property
    def pixel_type(self) -> str:
        """The on-disk pixel type as a NumPy dtype string, for example ``'>u2'``."""
        ...

    @property
    def metadata(self) -> MovieMetadata:
        """The acquisition metadata, each value with its origin."""
        ...

    def read_frames(self, start: int, stop: int) -> np.ndarray:
        """Return frames ``start`` to ``stop - 1``; ``0 <= start <= stop <= n_frames``.

        Raise `IndexError` when the range is outside that bound.
        """
        ...

    def close(self) -> None:
        """Release the source (idempotent)."""
        ...

    def __enter__(self) -> MovieSource:
        """Enter the context; return the source."""
        ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the source."""
        ...


@dataclass(frozen=True)
class MovieAdapter:
    """A reader for one movie container.

    ``name`` is what an error message calls the container, ``extensions`` are
    lower-case suffixes with the dot, ``sniff`` answers whether the first bytes of
    a file are this container, and ``open`` opens a path as a [`MovieSource`][].
    """

    name: str
    extensions: tuple[str, ...]
    sniff: Callable[[bytes], bool]
    open: Callable[[Path], MovieSource]

    def describe(self) -> str:
        """``'TIFF (.tif, .tiff)'``: the name and the extensions, for messages."""
        return f"{self.name} ({', '.join(self.extensions)})"


_REGISTRY: list[MovieAdapter] = []
_BUILTINS_LOADED = False
# Guards the registry: the first read can come from two threads at once (a batch
# worker and the GUI), and neither may see the flag set before TIFF is in the list.
_LOCK = threading.RLock()


def _ensure_builtins() -> None:
    """Register the built-in adapters once, on the first read of the registry.

    The flag is set only after the registration succeeds, under the lock, so a
    concurrent reader never sees an empty registry and a failed import is retried
    on the next read instead of leaving the registry empty for good.
    """
    global _BUILTINS_LOADED  # noqa: PLW0603 - a one-time lazy registration flag.
    if _BUILTINS_LOADED:
        return
    with _LOCK:
        if _BUILTINS_LOADED:
            return
        from tether.io.bioformats import BIOFORMATS_ADAPTER
        from tether.io.czi import CZI_ADAPTER
        from tether.io.movie import TIFF_ADAPTER
        from tether.io.nd2 import ND2_ADAPTER
        from tether.io.sif import SIF_ADAPTER

        # All or none: a failure part-way leaves the registry as it was, so the retry
        # does not meet the adapters that the failed attempt had already added.
        before = list(_REGISTRY)
        try:
            for adapter in (
                TIFF_ADAPTER,
                CZI_ADAPTER,
                ND2_ADAPTER,
                SIF_ADAPTER,
                BIOFORMATS_ADAPTER,
            ):
                _register(adapter)
        except BaseException:
            _REGISTRY[:] = before
            raise
        _BUILTINS_LOADED = True


def register_adapter(adapter: MovieAdapter) -> None:
    """Add ``adapter`` to the registry, after the built-in adapters.

    Refuses an adapter whose name is already registered, or that claims an
    extension another adapter claims, so that the extension tie-break of
    [`select_adapter`][] stays unambiguous. The built-ins are registered first,
    so a registration before the first read is checked against them too and
    cannot take the TIFF adapter's place at the head of the order.
    """
    with _LOCK:
        _ensure_builtins()
        _register(adapter)


def _register(adapter: MovieAdapter) -> None:
    """Validate ``adapter`` against the registry and append it."""
    for other in _REGISTRY:
        if other.name == adapter.name:
            raise ValueError(f"a movie adapter named {adapter.name!r} is already registered")
        shared = sorted(set(other.extensions) & set(adapter.extensions))
        if shared:
            raise ValueError(
                f"adapter {adapter.name!r} claims {', '.join(shared)}, "
                f"which adapter {other.name!r} already claims"
            )
    if any(ext != ext.lower() or not ext.startswith(".") for ext in adapter.extensions):
        raise ValueError(
            f"adapter {adapter.name!r}: extensions must be lower-case and start with a dot"
        )
    _REGISTRY.append(adapter)


def registered_adapters() -> tuple[MovieAdapter, ...]:
    """Return the registered adapters, in registration order (TIFF first, then ND2)."""
    _ensure_builtins()
    with _LOCK:
        return tuple(_REGISTRY)


def _readable_list(adapters: tuple[MovieAdapter, ...]) -> str:
    return "; ".join(a.describe() for a in adapters) or "none"


def select_adapter(path: str | PathLike[str]) -> MovieAdapter:
    """Choose the adapter for ``path`` from its first bytes and its extension.

    Every adapter whose ``sniff`` accepts the first bytes is a candidate; of those,
    the one that claims the file's extension wins, else the first registered. A
    file that no adapter recognizes raises [`UnsupportedContainerError`][]. A
    missing file raises `FileNotFoundError`.
    """
    p = Path(path)
    adapters = registered_adapters()
    with p.open("rb") as fh:
        head = fh.read(_SNIFF_BYTES)
    ext = p.suffix.lower()
    by_content = [a for a in adapters if a.sniff(head)]
    for adapter in by_content:
        if ext in adapter.extensions:
            return adapter
    if by_content:
        return by_content[0]
    readable = _readable_list(adapters)
    by_name = [a for a in adapters if ext in a.extensions]
    if by_name:
        raise UnsupportedContainerError(
            f"{p}: the name says {by_name[0].name}, but the content is not a "
            f"{by_name[0].name} file (it may be renamed, corrupt, or an incomplete copy). "
            f"Tether reads: {readable}."
        )
    raise UnsupportedContainerError(
        f"{p}: Tether does not read this movie container "
        f"({ext or 'no extension'}). Tether reads: {readable}. The cellSens .vsi "
        "files go through the optional Bio-Formats stack (see the install guide); for "
        "another container, export the movie to uncompressed TIFF."
    )


def open_movie_source(path: str | PathLike[str]) -> MovieSource:
    """Open ``path`` with the adapter that [`select_adapter`][] chooses."""
    p = Path(path)
    return select_adapter(p).open(p)

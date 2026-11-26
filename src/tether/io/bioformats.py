# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Bio-Formats route: cellSens ``.vsi`` movies through an isolated Java stack (#907, ADR-0147).

No maintained Python reader for Olympus/Evident cellSens ``.vsi`` files is known, and
Bio-Formats, which reads them, needs Java. Java stays out of the base environment: an
optional **fourth** environment (``bioformats/conda-lock.yml``) holds Python, a Java runtime,
JPype and Bio-Formats, and this module talks to it through a pipe. It starts
``tether/io/_bioformats_runner.py`` with that environment's Python, sends one JSON request at a
time and reads the reply (the protocol is in that file). **The base process imports no Java
binding**: this module needs only the standard library and NumPy.

[`BIOFORMATS_ADAPTER`][] is a built-in entry of the adapter registry
([`tether.io.adapters`][]). A ``.vsi`` file begins with a TIFF header, so by content alone it
is a TIFF; the ``.vsi`` extension breaks that tie, as the registry's rule says, and the frames
come from the ``.ets`` files in the ``_<name>_`` folder beside it. Where the fourth environment
is not installed, opening a ``.vsi`` raises [`BioformatsUnavailableError`][], whose message
gives the install command.

The environment is found through ``$TETHER_BIOFORMATS_PYTHON``, the path of its Python
interpreter (see ``docs/install.md``, "The Bio-Formats stack").

Two fixed deadlines keep a runner that stops answering from blocking Tether (#1416): a request
that has no whole reply within **300 s** (``_REPLY_DEADLINE``) kills the runner and raises
[`BioformatsRunnerError`][], and closing a source waits at most **10 s** (``_EXIT_WAIT``) for the
runner to exit before it kills it, without waiting for a reply or for a request in flight.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from tether.io.adapters import (
    InvalidPixelError,
    LossyCodecError,
    MetadataValue,
    MovieAdapter,
    MovieMetadata,
    UnsupportedContainerError,
)

if TYPE_CHECKING:
    from os import PathLike
    from types import TracebackType

    import numpy as np

__all__ = [
    "BIOFORMATS_ADAPTER",
    "BIOFORMATS_PYTHON_ENV",
    "BioformatsMovieSource",
    "BioformatsRunnerError",
    "BioformatsUnavailableError",
    "INSTALL_HINT",
    "bioformats_python",
    "open_bioformats_source",
    "runner_info",
]

BIOFORMATS_PYTHON_ENV = "TETHER_BIOFORMATS_PYTHON"
"""The environment variable that names the Python interpreter of the Bio-Formats stack."""

RUNNER_SCRIPT = Path(__file__).with_name("_bioformats_runner.py")

INSTALL_HINT = (
    "Install the optional Bio-Formats stack once: "
    "`micromamba create -p <folder> -f bioformats/conda-lock.yml` "
    "(the lock file is in the Tether repository), then set "
    f"{BIOFORMATS_PYTHON_ENV} to that folder's Python "
    "(`<folder>/bin/python`, or `<folder>\\python.exe` on Windows). "
    "See the install guide, section 'The Bio-Formats stack'."
)
"""How to install the stack; every 'not installed' message carries it."""

# Seconds to wait for the runner to exit after ``close`` before it is killed (#1416).
_EXIT_WAIT = 10.0

# Seconds a request may wait for its whole reply, the frame bytes included, before the runner
# is killed and the request raises BioformatsRunnerError (#1416). It covers the slowest real
# request: the first ``open`` starts the JVM and indexes a large cellSens movie, and a ``read``
# of one 64-frame block of such a movie decodes up to a few hundred megabytes from the ``.ets``
# files, often over a network share. Both take seconds to a minute; five minutes leaves a wide
# margin and still ends a runner that has stopped answering. Fixed in code, not a user setting.
_REPLY_DEADLINE = 300.0


class BioformatsUnavailableError(UnsupportedContainerError):
    """The container needs the Bio-Formats stack, and the stack is not installed or not found."""


class BioformatsRunnerError(RuntimeError):
    """The Bio-Formats runner failed: it did not start, or it stopped, or it gave a bad reply."""


def bioformats_python() -> Path | None:
    """Return the Bio-Formats stack's Python from ``$TETHER_BIOFORMATS_PYTHON``, or ``None``."""
    value = os.environ.get(BIOFORMATS_PYTHON_ENV, "").strip()
    return Path(value).expanduser() if value else None


def _require_python(what: str) -> Path:
    python = bioformats_python()
    if python is None:
        raise BioformatsUnavailableError(
            f"{what} needs the optional Bio-Formats stack, and {BIOFORMATS_PYTHON_ENV} "
            f"is not set. {INSTALL_HINT}"
        )
    if not python.is_file():
        raise BioformatsUnavailableError(
            f"{what} needs the optional Bio-Formats stack, and {BIOFORMATS_PYTHON_ENV} "
            f"names {python}, which is not a file. {INSTALL_HINT}"
        )
    return python


class _Runner:
    """One runner process and its pipe; one request at a time."""

    def __init__(self, python: Path) -> None:
        # A file, not a pipe: the JVM can write any amount there without a reader.
        self._stderr = tempfile.TemporaryFile()  # noqa: SIM115 - closed in close()
        self._lock = threading.Lock()
        # -I: the runner sees neither the user's site packages nor PYTHON* variables, and
        # never the current folder, so nothing of the base environment leaks into it.
        try:
            self._proc = subprocess.Popen(
                [str(python), "-I", str(RUNNER_SCRIPT)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self._stderr,
            )
        except OSError as exc:
            self._stderr.close()
            raise BioformatsUnavailableError(
                f"the Bio-Formats stack's Python {python} did not start ({exc}). {INSTALL_HINT}"
            ) from exc

    def _stderr_tail(self, limit: int = 2000) -> str:
        try:
            self._stderr.seek(0)
            text = self._stderr.read().decode("utf-8", "replace")
        except (OSError, ValueError):
            return ""
        return text[-limit:].strip()

    def request(self, payload: dict) -> tuple[dict, bytes]:
        """Send ``payload`` and return the reply and the bytes that follow it.

        A runner that gives no whole reply within ``_REPLY_DEADLINE`` seconds is killed, and
        the request raises [`BioformatsRunnerError`][] (#1416).
        """
        with self._lock:
            proc = self._proc
            # A watchdog rather than a timed read: a pipe cannot be read with a timeout on
            # every platform (``select`` does not take pipes on Windows). Killing the runner
            # closes its end of the pipe, so the blocked read returns at once.
            expired = threading.Event()

            def expire() -> None:
                expired.set()
                with contextlib.suppress(OSError):
                    proc.kill()

            watchdog = threading.Timer(_REPLY_DEADLINE, expire)
            watchdog.daemon = True
            watchdog.start()
            try:
                return self._exchange(proc, payload)
            except BioformatsRunnerError as exc:
                if expired.is_set():
                    raise BioformatsRunnerError(
                        f"the Bio-Formats runner gave no reply to {payload.get('op')!r} within "
                        f"{_REPLY_DEADLINE:g} s and was stopped. "
                        f"Its last output: {self._stderr_tail()}"
                    ) from exc
                raise
            finally:
                watchdog.cancel()

    def _exchange(self, proc: subprocess.Popen, payload: dict) -> tuple[dict, bytes]:
        # A runner that fails at start (no jar, no JVM) writes its refusal and exits
        # before it reads a request, so a broken stdin does not mean there is no reply:
        # read stdout either way.
        with contextlib.suppress(BrokenPipeError, OSError, ValueError):
            proc.stdin.write(json.dumps(payload).encode("utf-8") + b"\n")
            proc.stdin.flush()
        try:
            line = proc.stdout.readline()
        except (OSError, ValueError):
            line = b""
        if not line:
            # The pipe can close before the process is reaped (seen on Windows), so wait
            # a bounded time for the exit status rather than read ``None`` from poll().
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=_EXIT_WAIT)
            raise BioformatsRunnerError(
                "the Bio-Formats runner stopped without a reply "
                f"(exit status {proc.returncode}). Its last output: {self._stderr_tail()}"
            )
        try:
            reply = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BioformatsRunnerError(f"the Bio-Formats runner gave a bad reply: {exc}") from exc
        data = b""
        if reply.get("ok") and "nbytes" in reply:
            data = _read_exact(proc.stdout, int(reply["nbytes"]))
        return reply, data

    def close(self) -> None:
        """Stop the runner within a bounded time, whether or not it answers (#1416).

        It neither waits for the reply to ``close`` nor for a request in flight: it asks the
        runner to stop only if no request holds the pipe, waits ``_EXIT_WAIT`` seconds for it
        to exit, and kills it if it has not. A request in flight then sees its pipe close and
        raises [`BioformatsRunnerError`][].
        """
        proc = self._proc
        locked = self._lock.acquire(blocking=False)
        try:
            if proc.poll() is None:
                if locked:
                    # Closing stdin after ``close`` is an end of input too, which the runner
                    # also takes as the end. The reply is not read: it fits the pipe buffer.
                    with contextlib.suppress(BrokenPipeError, OSError, ValueError):
                        proc.stdin.write(b'{"op": "close"}\n')
                        proc.stdin.flush()
                        proc.stdin.close()
                try:
                    proc.wait(timeout=_EXIT_WAIT)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            for stream in (proc.stdin, proc.stdout):
                if stream is not None:
                    # Closing a pipe whose reader has died flushes into a broken pipe, which
                    # Windows reports as EINVAL; the process is gone either way.
                    with contextlib.suppress(OSError):
                        stream.close()
            self._stderr.close()
        finally:
            if locked:
                self._lock.release()


def _read_exact(stream, n: int) -> bytes:
    chunks, got = [], 0
    while got < n:
        try:
            chunk = stream.read(n - got)
        except (OSError, ValueError):
            chunk = b""
        if not chunk:
            raise BioformatsRunnerError(
                f"the Bio-Formats runner stopped after {got} of {n} frame bytes"
            )
        chunks.append(chunk)
        got += len(chunk)
    return b"".join(chunks)


def _raise_refusal(path: Path, reply: dict) -> None:
    kind, message = reply.get("error", "error"), str(reply.get("message", "no message"))
    if kind == "index":
        raise IndexError(message)
    if kind == "not_found":
        raise FileNotFoundError(message)
    if kind == "lossy":
        raise LossyCodecError(message)
    if kind == "invalid_pixel":
        raise InvalidPixelError(message)
    if kind == "unsupported":
        raise UnsupportedContainerError(message)
    if kind == "unavailable":
        raise BioformatsUnavailableError(f"{path}: {message}. {INSTALL_HINT}")
    raise BioformatsRunnerError(f"{path}: {message}")


def _value(raw: object, cast) -> MetadataValue:
    return MetadataValue.unknown() if raw is None else MetadataValue.from_file(cast(raw))


class BioformatsMovieSource:
    """A movie that the Bio-Formats runner reads; a [`tether.io.adapters.MovieSource`][].

    The runner process lives as long as the source: [`close`][] stops it.
    """

    def __init__(self, path: str | PathLike[str]) -> None:
        self._path = Path(path)
        python = _require_python(f"{self._path} ({self._path.suffix or 'this container'})")
        self._runner: _Runner | None = _Runner(python)
        try:
            reply, _ = self._runner.request({"op": "open", "path": str(self._path)})
            if not reply.get("ok"):
                _raise_refusal(self._path, reply)
            n, h, w = (int(x) for x in reply["shape"])
            self._shape = (n, h, w)
            self._pixel_type = str(reply["pixel_type"])
            self._series = int(reply.get("series", 0))
            self._format = str(reply.get("format", ""))
            meta = reply.get("metadata") or {}
            channels = meta.get("channels")
            self._metadata = MovieMetadata(
                frame_time=_value(meta.get("frame_time"), float),
                pixel_size=_value(meta.get("pixel_size"), float),
                channels=_value(None if channels is None else tuple(channels), tuple),
            )
        except BaseException:
            self._runner.close()
            self._runner = None
            raise

    @property
    def path(self) -> Path:
        """The movie's filesystem path."""
        return self._path

    @property
    def shape(self) -> tuple[int, int, int]:
        """``(n_frames, height, width)``."""
        return self._shape

    @property
    def n_frames(self) -> int:
        """The number of frames."""
        return self._shape[0]

    @property
    def pixel_type(self) -> str:
        """The pixel type as a NumPy dtype string, in the byte order Bio-Formats gives."""
        return self._pixel_type

    @property
    def series(self) -> int:
        """The Bio-Formats series that holds the movie (the one with the most frames)."""
        return self._series

    @property
    def format(self) -> str:
        """The name of the Bio-Formats reader that opened the file, e.g. ``'CellSens VSI'``."""
        return self._format

    @property
    def metadata(self) -> MovieMetadata:
        """The acquisition metadata, each value with its origin."""
        return self._metadata

    def read_frames(self, start: int, stop: int) -> np.ndarray:
        """Return frames ``start`` to ``stop - 1`` as a ``(stop - start, height, width)`` array."""
        import numpy as np

        if self._runner is None:
            raise ValueError(f"{self._path}: the movie source is closed")
        n, h, w = self._shape
        if not (0 <= start <= stop <= n):
            raise IndexError(f"frames {start}:{stop} are outside 0:{n}")
        reply, data = self._runner.request({"op": "read", "start": start, "stop": stop})
        if not reply.get("ok"):
            _raise_refusal(self._path, reply)
        return np.frombuffer(data, dtype=np.dtype(self._pixel_type)).reshape(stop - start, h, w)

    def close(self) -> None:
        """Stop the runner (idempotent)."""
        runner, self._runner = self._runner, None
        if runner is not None:
            runner.close()

    def __enter__(self) -> BioformatsMovieSource:
        """Enter the context; return the source."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the source."""
        self.close()

    def __del__(self) -> None:  # pragma: no cover - a safety net for a source never closed
        """Stop the runner of a source that was never closed."""
        runner = getattr(self, "_runner", None)
        if runner is not None:
            with contextlib.suppress(Exception):
                runner.close()

    def __repr__(self) -> str:
        """Show the path, the shape and whether the source is open."""
        state = "closed" if self._runner is None else "open"
        return f"BioformatsMovieSource({str(self._path)!r}, shape={self._shape}, {state})"


def open_bioformats_source(path: str | PathLike[str]) -> BioformatsMovieSource:
    """Open any movie that Bio-Formats reads, through the runner (not only a ``.vsi``)."""
    return BioformatsMovieSource(path)


def runner_info() -> dict:
    """Start the runner once and return its versions: ``bioformats``, ``java``, ``python``.

    Raises [`BioformatsUnavailableError`][] where the stack is not installed.
    """
    runner = _Runner(_require_python("the Bio-Formats route"))
    try:
        reply, _ = runner.request({"op": "hello"})
    finally:
        runner.close()
    if not reply.get("ok"):
        _raise_refusal(Path("the Bio-Formats route"), reply)
    return {k: v for k, v in reply.items() if k != "ok"}


def _is_tiff(head: bytes) -> bool:
    """Tell a TIFF (or BigTIFF) header, which is how a cellSens ``.vsi`` file begins."""
    return head[:4] in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")


BIOFORMATS_ADAPTER = MovieAdapter(
    name="cellSens VSI",
    extensions=(".vsi",),
    sniff=_is_tiff,
    open=BioformatsMovieSource,
)
"""The registry entry of the Bio-Formats route: cellSens ``.vsi`` files (ADR-0147)."""

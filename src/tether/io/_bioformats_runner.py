# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Bio-Formats runner: the process that reads a movie with Bio-Formats (#907, ADR-0147).

**This file runs in the optional Bio-Formats environment** (``bioformats/conda-lock.yml``),
never in the base environment. [`tether.io.bioformats`][] starts it as a script with that
environment's Python, so it imports only the standard library and, inside [`main`][], JPype.
It does not import ``tether``, which is not installed in that environment.

The protocol is one request at a time over the process's standard input and output. A request
is one line of JSON. A reply is one line of JSON and, for ``read``, the raw frame bytes after it:

- ``{"op": "hello"}`` → ``{"ok": true, "bioformats": "8.0.0", "java": "21.0.13", ...}``
- ``{"op": "open", "path": P}`` → ``{"ok": true, "shape": [n, h, w], "pixel_type": "<u2",
  "series": s, "metadata": {...}}``
- ``{"op": "read", "start": a, "stop": b}`` → ``{"ok": true, "nbytes": N}`` then ``N`` bytes:
  frames ``a`` to ``b - 1`` in the byte order that ``pixel_type`` names
- ``{"op": "close"}`` → ``{"ok": true}``, and the process exits

A refusal is ``{"ok": false, "error": KIND, "message": TEXT}``. ``KIND`` is ``unsupported``,
``lossy``, ``invalid_pixel``, ``index``, ``not_found``, ``unavailable`` (no jar or no
Java runtime) or ``error``; the base side maps each to
its exception. Java and Bio-Formats write their own messages to standard output, so before the
JVM starts the runner moves standard output to standard error and keeps a private copy of the
original for the protocol: nothing but replies ever reaches the base side's pipe.
"""

from __future__ import annotations

import contextlib
import glob
import json
import os
import sys
from pathlib import Path

#: Bio-Formats' pixel type names and the NumPy kind of each (``bit`` is refused).
_PIXEL_KINDS = {
    "int8": "i1",
    "uint8": "u1",
    "int16": "i2",
    "uint16": "u2",
    "int32": "i4",
    "uint32": "u4",
    "float": "f4",
    "double": "f8",
}

#: The cellSens ``.ets`` tile codecs that can lose values: JPEG, and JPEG 2000, whose stream
#: does not say whether it was written lossless (the codec numbers of Bio-Formats'
#: ``CellSensReader``: RAW 0, JPEG 2, JPEG_2000 3, JPEG_LOSSLESS 5, PNG 8, BMP 9).
_CELLSENS_LOSSY = {2: "JPEG", 3: "JPEG 2000"}
_CELLSENS_KNOWN = {0, 2, 3, 5, 8, 9}


class Refusal(Exception):
    """A request the runner refuses; ``kind`` names the base-side exception."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def find_jar(prefix: str | os.PathLike[str] | None = None) -> Path | None:
    """Find ``bioformats_package.jar``: ``$TETHER_BIOFORMATS_JAR``, else the environment's ``bin``.

    The bioconda ``bftools`` package puts the jar in ``<prefix>/bin``; on Windows a conda
    package's files can also sit under ``<prefix>/Library``.
    """
    explicit = os.environ.get("TETHER_BIOFORMATS_JAR")
    if explicit:
        return Path(explicit)
    root = Path(prefix if prefix is not None else sys.prefix)
    for candidate in (
        root / "bin" / "bioformats_package.jar",
        root / "Library" / "bin" / "bioformats_package.jar",
        root / "share" / "bftools" / "bioformats_package.jar",
    ):
        if candidate.is_file():
            return candidate
    found = sorted(glob.glob(str(root / "**" / "bioformats_package.jar"), recursive=True))
    return Path(found[0]) if found else None


def find_java_home(prefix: str | os.PathLike[str] | None = None) -> Path | None:
    """Find the Java runtime: ``$JAVA_HOME``, else conda-forge's ``openjdk`` layout.

    The runner is started without activating the environment, so the activation script that
    sets ``JAVA_HOME`` has not run.
    """
    explicit = os.environ.get("JAVA_HOME")
    if explicit and Path(explicit).is_dir():
        return Path(explicit)
    root = Path(prefix if prefix is not None else sys.prefix)
    for candidate in (root / "lib" / "jvm", root / "Library" / "lib" / "jvm", root / "Library"):
        if (candidate / "release").is_file():
            return candidate
    return None


class Session:
    """One Bio-Formats reader, opened on one movie."""

    def __init__(self, jpype) -> None:
        self._jpype = jpype
        self._reader = None
        self._series = 0
        self._indices: list[int] = []
        self._plane_bytes = 0

    def hello(self) -> dict:
        formats_tools = self._jpype.JClass("loci.formats.FormatTools")
        system = self._jpype.JClass("java.lang.System")
        return {
            "bioformats": str(formats_tools.VERSION),
            "java": str(system.getProperty("java.version")),
            "python": sys.version.split()[0],
        }

    def open(self, path: str) -> dict:
        if not Path(path).is_file():
            raise Refusal("not_found", f"{path}: no such file")
        jp = self._jpype
        image_reader = jp.JClass("loci.formats.ImageReader")
        metadata_tools = jp.JClass("loci.formats.MetadataTools")
        format_tools = jp.JClass("loci.formats.FormatTools")
        reader = image_reader()
        meta = metadata_tools.createOMEXMLMetadata()
        reader.setMetadataStore(meta)
        reader.setFlattenedResolutions(False)
        try:
            reader.setId(path)
        except jp.JException as exc:
            reader.close()
            raise Refusal("unsupported", f"{path}: Bio-Formats cannot read it: {exc}") from None
        self._reader = reader
        try:
            return self._describe(path, meta, format_tools)
        except Refusal:
            self.close()
            raise

    def _describe(self, path: str, meta, format_tools) -> dict:
        """Choose the series, check its codec and pixels, and say what it holds."""
        reader = self._reader
        series = self._choose_series(path)
        reader.setSeries(series)
        self._check_codec(path, reader)
        if reader.isRGB() or reader.getRGBChannelCount() != 1 or reader.isIndexed():
            raise Refusal(
                "invalid_pixel",
                f"{path}: series {series} holds color or palette pixels, not one intensity "
                "sample each, so it is not a fluorescence movie",
            )
        if reader.getSizeC() != 1 or reader.getSizeZ() != 1:
            raise Refusal(
                "unsupported",
                f"{path}: series {series} has {reader.getSizeC()} channels and "
                f"{reader.getSizeZ()} focal planes; Tether reads a movie of one channel and "
                "one focal plane over time",
            )
        type_name = str(format_tools.getPixelTypeString(reader.getPixelType()))
        if type_name not in _PIXEL_KINDS:
            raise Refusal("invalid_pixel", f"{path}: pixel type {type_name} is not supported")
        kind = _PIXEL_KINDS[type_name]
        order = "|" if kind.endswith("1") else ("<" if reader.isLittleEndian() else ">")
        n_frames = int(reader.getSizeT())
        height, width = int(reader.getSizeY()), int(reader.getSizeX())
        self._series = series
        self._indices = [int(reader.getIndex(0, 0, t)) for t in range(n_frames)]
        self._plane_bytes = height * width * int(kind[1])
        return {
            "shape": [n_frames, height, width],
            "pixel_type": order + kind,
            "series": series,
            "format": str(reader.getFormat()),
            "metadata": self._metadata(meta, series, n_frames),
        }

    def _choose_series(self, path: str) -> int:
        """Return the series with the most time points; refuse a tie between two movies."""
        reader = self._reader
        counts = []
        for s in range(int(reader.getSeriesCount())):
            reader.setSeries(s)
            counts.append(int(reader.getSizeT()))
        best = max(counts)
        tied = [s for s, n in enumerate(counts) if n == best]
        if best > 1 and len(tied) > 1:
            raise Refusal(
                "unsupported",
                f"{path}: series {', '.join(map(str, tied))} are each a movie of {best} "
                "frames, and Tether cannot tell which one to read",
            )
        return tied[0]

    def _check_codec(self, path: str, reader) -> None:
        """Refuse cellSens tiles that a lossy codec wrote (ADR-0147).

        Bio-Formats decodes the tiles but does not put the codec in its metadata, so the
        runner reads the codec list that ``CellSensReader`` keeps (one entry for each ``.ets``
        file). A codec list it cannot read is a refusal too: the frames would then be values
        that nobody checked.
        """
        inner = reader.getReader()
        if str(inner.getClass().getName()) != "loci.formats.in.CellSensReader":
            return
        try:
            field = inner.getClass().getDeclaredField("compressionType")
            field.setAccessible(True)
            codecs = [int(c) for c in field.get(inner)]
        except Exception as exc:  # noqa: BLE001 - any failure here means "codec not known"
            raise Refusal(
                "lossy",
                f"{path}: Tether cannot read which codec stored the frames ({exc}), so it "
                "cannot tell whether they hold the acquired values",
            ) from None
        lossy = sorted({_CELLSENS_LOSSY[c] for c in codecs if c in _CELLSENS_LOSSY})
        unknown = sorted({c for c in codecs if c not in _CELLSENS_KNOWN})
        if lossy or unknown:
            names = ", ".join(lossy + [f"codec {c}" for c in unknown])
            raise Refusal(
                "lossy",
                f"{path}: the frames are stored with {names}, which can be lossy. A lossy "
                "codec changes the intensities, so Tether does not read them; export the "
                "acquisition again without compression",
            )

    @staticmethod
    def _metadata(meta, series: int, n_frames: int) -> dict:
        jp_units = _units()
        out: dict = {"frame_time": None, "pixel_size": None, "channels": None}
        with contextlib.suppress(Exception):
            size = meta.getPixelsPhysicalSizeX(series)
            if size is not None:
                out["pixel_size"] = float(size.value(jp_units.MICROMETER).doubleValue())
        with contextlib.suppress(Exception):
            step = meta.getPixelsTimeIncrement(series)
            if step is not None and float(step.value(jp_units.SECOND).doubleValue()) > 0:
                out["frame_time"] = float(step.value(jp_units.SECOND).doubleValue())
        if out["frame_time"] is None and n_frames > 1:
            with contextlib.suppress(Exception):
                planes = int(meta.getPlaneCount(series))
                times = [meta.getPlaneDeltaT(series, i) for i in range(planes)]
                if len(times) == n_frames and all(t is not None for t in times):
                    secs = [float(t.value(jp_units.SECOND).doubleValue()) for t in times]
                    step = (secs[-1] - secs[0]) / (n_frames - 1)
                    if step > 0:
                        out["frame_time"] = step
        with contextlib.suppress(Exception):
            name = meta.getChannelName(series, 0)
            if name is not None and str(name):
                out["channels"] = [str(name)]
        return out

    def read(self, start: int, stop: int) -> bytes:
        if self._reader is None:
            raise Refusal("error", "no movie is open")
        n = len(self._indices)
        if not (0 <= start <= stop <= n):
            raise Refusal("index", f"frames {start}:{stop} are outside 0:{n}")
        planes = [bytes(self._reader.openBytes(self._indices[t])) for t in range(start, stop)]
        data = b"".join(planes)
        if len(data) != (stop - start) * self._plane_bytes:
            raise Refusal("error", "Bio-Formats returned a plane of an unexpected size")
        return data

    def close(self) -> None:
        if self._reader is not None:
            with contextlib.suppress(Exception):
                self._reader.close()
            self._reader = None


def _units():
    import jpype

    return jpype.JClass("ome.units.UNITS")


def _start_jvm(jar: Path):
    import jpype

    home = find_java_home()
    if home is not None:
        os.environ.setdefault("JAVA_HOME", str(home))
    jpype.startJVM(classpath=[str(jar)], convertStrings=False)
    debug = jpype.JClass("loci.common.DebugTools")
    debug.setRootLevel("ERROR")
    return jpype


def _reply(stream, payload: dict, data: bytes = b"") -> None:
    stream.write(json.dumps(payload).encode("utf-8") + b"\n")
    if data:
        stream.write(data)
    stream.flush()


def main() -> int:
    """Serve requests until ``close`` or the end of standard input."""
    # The protocol keeps the original standard output; everything else that writes to fd 1
    # (the JVM, Bio-Formats' logger, a stray print) goes to standard error instead.
    proto = os.fdopen(os.dup(sys.stdout.fileno()), "wb")
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    jar = find_jar()
    if jar is None or not jar.is_file():
        _reply(
            proto,
            {"ok": False, "error": "unavailable", "message": "bioformats_package.jar not found"},
        )
        return 2
    try:
        session = Session(_start_jvm(jar))
    except Exception as exc:  # noqa: BLE001 - report any start failure over the protocol
        message = f"the Java runtime did not start: {exc}"
        _reply(proto, {"ok": False, "error": "unavailable", "message": message})
        return 2
    for line in sys.stdin.buffer:
        try:
            request = json.loads(line)
            op = request.get("op")
            if op == "hello":
                _reply(proto, {"ok": True, **session.hello()})
            elif op == "open":
                _reply(proto, {"ok": True, **session.open(str(request["path"]))})
            elif op == "read":
                data = session.read(int(request["start"]), int(request["stop"]))
                _reply(proto, {"ok": True, "nbytes": len(data)}, data)
            elif op == "close":
                session.close()
                _reply(proto, {"ok": True})
                return 0
            else:
                raise Refusal("error", f"unknown request {op!r}")
        except Refusal as refusal:
            _reply(proto, {"ok": False, "error": refusal.kind, "message": str(refusal)})
        except Exception as exc:  # noqa: BLE001 - every failure is a reply, never a dead pipe
            _reply(
                proto, {"ok": False, "error": "error", "message": f"{type(exc).__name__}: {exc}"}
            )
    session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

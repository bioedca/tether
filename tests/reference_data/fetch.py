# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Find, download and verify the reference data sets of ``manifest.toml`` (#696, ADR-0073).

A test asks for a data set by its id and gets a local path to bytes whose SHA-256 equals the
manifest's, or a skip that says why the data is not here::

    from reference_data.fetch import path_for

    trace = path_for("kinsoft-trace-sample")

The four tiers:

``git``
    A committed file of at most 1 MB. Always present.
``lfs``
    A committed Git-LFS file (``tests/fixtures/large/``, ``benchmarks/large/``). Skips while the
    checkout holds only the LFS pointer.
``download``
    A public file of at most 200 MB, downloaded to a cache directory
    (``$TETHER_REFERENCE_CACHE``, default ``~/.cache/tether-reference-data``). A download happens
    only when ``TETHER_REFERENCE_DOWNLOAD=1`` (the weekly ``reference-data`` job sets it), so the
    required matrix makes no network call; a set that is not cached then skips.
``workstation``
    A larger set, read from the directory that the entry's ``env`` variable names. Skips when the
    variable is not set.

Each tier checks the SHA-256 before the first use in a process, and a file whose checksum is
wrong is never returned: :class:`ChecksumMismatch` names the set and both checksums. A download
is written to a temporary file of its own (``<name>.<random>.part``) and renamed only after its
checksum matched.

``python tests/reference_data/fetch.py cache-key`` prints the manifest part of the weekly job's
cache key. It depends only on the ids and checksums of the ``download`` entries, so an edit to a
citation keeps the cache and a new or changed file replaces it; the job adds the month, so that the
first run of each month downloads every set again from upstream.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile
import tomllib
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

REPO = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).with_name("manifest.toml")
DOWNLOAD_ENV = "TETHER_REFERENCE_DOWNLOAD"
CACHE_ENV = "TETHER_REFERENCE_CACHE"
DEFAULT_CACHE = "~/.cache/tether-reference-data"

TIERS = ("git", "lfs", "download", "workstation")
#: The size rules of item 5 of #696, in bytes (decimal megabytes).
GIT_FILE_LIMIT = 1_000_000
LFS_TOTAL_LIMIT = 50_000_000
DOWNLOAD_FILE_LIMIT = 200_000_000
#: The paths that `.gitattributes` sends to Git-LFS.
LFS_ROOTS = ("tests/fixtures/large/", "benchmarks/large/")
LFS_POINTER = b"version https://git-lfs.github.com/spec/v1"

_REQUIRED = ("id", "source", "record_url", "license", "file", "size", "sha256", "tier", "citation")
_OPTIONAL = ("download_url", "env", "extract_of", "extract_script", "extract_member")
_ID = re.compile(r"[a-z0-9][a-z0-9-]*")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SOURCE = re.compile(r"(doi:10\.\d{4,9}/\S+|accession:\S+)")
_ENV = re.compile(r"TETHER_[A-Z0-9_]+")
_CHUNK = 1 << 20


class ChecksumMismatch(RuntimeError):
    """A reference file whose bytes are not the ones the manifest names."""


@dataclass(frozen=True)
class Dataset:
    """One ``[[dataset]]`` entry of the manifest."""

    id: str
    source: str
    record_url: str
    license: str
    file: str
    size: int
    sha256: str
    tier: str
    citation: str
    download_url: str | None = None
    env: str | None = None
    extract_of: tuple[str, ...] = ()
    extract_script: str | None = None
    extract_member: str | None = None


def load_manifest(path: Path = MANIFEST) -> dict[str, Dataset]:
    """Read the manifest. A missing or unknown field is an error here, not a later surprise."""
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    if set(raw) != {"dataset"}:
        raise ValueError(f"{path}: the manifest holds only [[dataset]] tables, got {sorted(raw)}")
    datasets: dict[str, Dataset] = {}
    for entry in raw["dataset"]:
        name = entry.get("id", "<no id>")
        missing = [k for k in _REQUIRED if k not in entry]
        unknown = sorted(set(entry) - set(_REQUIRED) - set(_OPTIONAL))
        if missing or unknown:
            raise ValueError(f"{name}: missing fields {missing}, unknown fields {unknown}")
        if name in datasets:
            raise ValueError(f"{name}: two entries have this id")
        fields = dict(entry)
        fields["extract_of"] = tuple(entry.get("extract_of", ()))
        datasets[name] = Dataset(**fields)
    return datasets


def lfs_size(path: Path) -> int:
    """The size of a Git-LFS file, from its pointer when the object is not materialized."""
    head = path.read_bytes()[:200]
    if head.startswith(LFS_POINTER):
        match = re.search(rb"^size (\d+)$", head, re.MULTILINE)
        if match:
            return int(match.group(1))
    return path.stat().st_size


def lfs_oid(path: Path) -> str | None:
    """The SHA-256 a Git-LFS pointer names, or ``None`` when *path* holds the real bytes."""
    head = path.read_bytes()[:200]
    if not head.startswith(LFS_POINTER):
        return None
    match = re.search(rb"^oid sha256:([0-9a-f]{64})$", head, re.MULTILINE)
    return match.group(1).decode() if match else None


def check_manifest(datasets: dict[str, Dataset], repo: Path = REPO) -> list[str]:
    """Every rule the manifest must keep, as one message for each broken rule."""
    problems: list[str] = []
    licenses = {p.stem for p in (repo / "LICENSES").glob("*.txt")}
    for d in datasets.values():
        say = problems.append
        if not _ID.fullmatch(d.id):
            say(f"{d.id}: an id is lower-case letters, digits and hyphens")
        if not _SOURCE.fullmatch(d.source):
            say(f"{d.id}: source must be 'doi:10.…' or 'accession:…', got {d.source!r}")
        if not d.record_url.startswith("https://"):
            say(f"{d.id}: record_url must be an https URL")
        if d.license not in licenses:
            say(
                f"{d.id}: licence {d.license!r} has no LICENSES/{d.license}.txt, "
                "so REUSE does not know it"
            )
        if not _SHA256.fullmatch(d.sha256):
            say(f"{d.id}: sha256 must be 64 lower-case hex digits")
        if not isinstance(d.size, int) or d.size <= 0:
            say(f"{d.id}: size must be a positive number of bytes")
        if not d.citation.strip():
            say(f"{d.id}: citation is empty")
        if d.tier not in TIERS:
            say(f"{d.id}: tier must be one of {TIERS}, got {d.tier!r}")
            continue
        posix = PurePosixPath(d.file)
        # A backslash or a drive is a separator only on Windows, where it would leave the tier.
        if (
            posix.is_absolute()
            or ".." in posix.parts
            or "\\" in d.file
            or PureWindowsPath(d.file).drive
        ):
            say(f"{d.id}: file must be a relative path inside its tier")
            continue
        in_lfs = d.file.startswith(LFS_ROOTS)
        if d.tier in ("git", "lfs"):
            path = repo / d.file
            if not path.is_file():
                say(f"{d.id}: {d.file} is not committed")
            elif d.tier == "git":
                if in_lfs:
                    say(f"{d.id}: {d.file} is under an LFS path, so its tier is lfs")
                if d.size > GIT_FILE_LIMIT:
                    say(f"{d.id}: a git file is at most {GIT_FILE_LIMIT} B; this one is {d.size} B")
                if path.stat().st_size != d.size:
                    say(
                        f"{d.id}: {d.file} is {path.stat().st_size} B, the manifest says {d.size} B"
                    )
            else:
                if not in_lfs:
                    say(f"{d.id}: an lfs file lives under {' or '.join(LFS_ROOTS)}")
                if lfs_size(path) != d.size:
                    say(f"{d.id}: {d.file} is {lfs_size(path)} B, the manifest says {d.size} B")
                oid = lfs_oid(path)
                if oid is not None and oid != d.sha256:
                    say(f"{d.id}: the LFS pointer names {oid}, the manifest says {d.sha256}")
        if d.tier == "download":
            if not (d.download_url or "").startswith("https://"):
                say(f"{d.id}: a download entry needs an https download_url")
            if d.size > DOWNLOAD_FILE_LIMIT:
                say(f"{d.id}: a download is at most {DOWNLOAD_FILE_LIMIT} B; use tier workstation")
        elif d.download_url is not None:
            say(f"{d.id}: only a download entry has a download_url")
        if d.tier == "workstation":
            if not _ENV.fullmatch(d.env or ""):
                say(f"{d.id}: a workstation entry names its directory variable, TETHER_…")
        elif d.env is not None:
            say(f"{d.id}: only a workstation entry has an env")
        if d.extract_of:
            if d.tier not in ("git", "lfs"):
                say(f"{d.id}: an extract is committed (tier git or lfs)")
            for source in d.extract_of:
                if source not in datasets or datasets[source].tier not in (
                    "download",
                    "workstation",
                ):
                    say(f"{d.id}: extract_of {source!r} is not a download or workstation entry")
            if bool(d.extract_script) == bool(d.extract_member):
                say(f"{d.id}: an extract names either its extract_script or its extract_member")
            if d.extract_member and len(d.extract_of) != 1:
                say(f"{d.id}: an extract_member is a member of exactly one archive")
            if d.extract_script and not (repo / d.extract_script).is_file():
                say(f"{d.id}: extract_script {d.extract_script} does not exist")
        elif d.extract_script or d.extract_member:
            say(f"{d.id}: extract_script and extract_member need extract_of")
    lfs_total = sum(
        lfs_size(p) for root in LFS_ROOTS for p in (repo / root).rglob("*") if p.is_file()
    )
    if lfs_total > LFS_TOTAL_LIMIT:
        problems.append(f"the LFS tier holds {lfs_total} B, more than {LFS_TOTAL_LIMIT} B")
    return problems


def cache_key(datasets: dict[str, Dataset]) -> str:
    """The manifest part of the weekly cache key: the ids and checksums of the download entries."""
    lines = sorted(f"{d.id} {d.sha256}" for d in datasets.values() if d.tier == "download")
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:32]


_VERIFIED: dict[tuple[str, int, int], str] = {}


def _sha256_of(path: Path) -> str:
    stat = path.stat()
    key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
    if key not in _VERIFIED:
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            while chunk := fh.read(_CHUNK):
                digest.update(chunk)
        _VERIFIED[key] = digest.hexdigest()
    return _VERIFIED[key]


def _verified(d: Dataset, path: Path) -> Path:
    actual = _sha256_of(path)
    if actual != d.sha256:
        raise ChecksumMismatch(
            f"{d.id}: SHA-256 of {path} is {actual}, the manifest says {d.sha256}; "
            "the file is not used (delete it to download it again)"
        )
    return path


def _skip(reason: str) -> None:
    import pytest

    pytest.skip(reason)


def _download(d: Dataset, target: Path, opener: Callable) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    # A temporary file of its own, created exclusively, so a second process that downloads the
    # same set into the same cache can neither truncate it nor delete it (#795 review).
    handle, name = tempfile.mkstemp(prefix=target.name + ".", suffix=".part", dir=target.parent)
    part = Path(name)
    digest = hashlib.sha256()
    written = 0
    try:
        with os.fdopen(handle, "wb") as out, opener(d.download_url, timeout=60) as response:
            while chunk := response.read(_CHUNK):
                written += len(chunk)
                if written > d.size:
                    raise ChecksumMismatch(f"{d.id}: the download is larger than {d.size} B")
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest() != d.sha256:
            raise ChecksumMismatch(
                f"{d.id}: SHA-256 of the download from {d.download_url} is {digest.hexdigest()}, "
                f"the manifest says {d.sha256}; the file is not used"
            )
        os.replace(part, target)
    finally:
        part.unlink(missing_ok=True)
    return target


def path_for(
    dataset_id: str,
    *,
    manifest: dict[str, Dataset] | None = None,
    repo: Path = REPO,
    cache_dir: Path | None = None,
    opener: Callable = urllib.request.urlopen,
) -> Path:
    """The local path of a verified reference file, or a skip that says why it is not here."""
    datasets = load_manifest() if manifest is None else manifest
    if dataset_id not in datasets:
        raise KeyError(f"{dataset_id!r} is not in the reference-data manifest")
    d = datasets[dataset_id]
    if d.tier in ("git", "lfs"):
        path = repo / d.file
        if d.tier == "lfs" and lfs_oid(path) is not None:
            _skip(f"{d.id}: {d.file} is a Git-LFS pointer; run `git lfs pull` to fetch it")
        return _verified(d, path)
    if d.tier == "workstation":
        directory = os.environ.get(d.env or "")
        if not directory:
            _skip(f"{d.id}: set {d.env} to the directory that holds {d.file}")
        path = Path(directory) / d.file
        if not path.is_file():
            raise FileNotFoundError(f"{d.id}: {d.env} is set, but {path} does not exist")
        return _verified(d, path)
    cache = cache_dir or Path(os.environ.get(CACHE_ENV) or DEFAULT_CACHE).expanduser()
    path = cache / d.id / PurePosixPath(d.file).name
    if path.is_file():
        return _verified(d, path)
    if os.environ.get(DOWNLOAD_ENV) != "1":
        _skip(
            f"{d.id}: not in the cache {cache}, and downloads are off "
            f"({DOWNLOAD_ENV}=1 allows them)"
        )
    return _download(d, path, opener)


if __name__ == "__main__":
    if sys.argv[1:] != ["cache-key"]:
        sys.exit("usage: python tests/reference_data/fetch.py cache-key")
    print(cache_key(load_manifest()))

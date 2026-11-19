# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Verify a downloaded Tether installer against its build-provenance attestation (#333).

This is the security-critical unit of FR-UPDATE (PRD §7.12, ADR-0060). Tether's installers
carry no OS code signature (ADR-0059), so this check is the **entire** trust boundary
between a downloaded file and running it. Nothing here downloads the installer and nothing
here runs it: the caller passes a file that is already on disk, and the only thing that can
come back for it is a [`VerifiedInstaller`][], which the per-OS hand-off (#331) is the only
consumer of.

**The sequence.** [`verify_update`][] does each step in this order, and a step runs only
when every step before it passed:

1. **Consent.** Unless [`tether.gui.app_settings.update_check_state`][] reads *allowed*,
   nothing else happens: no request, no ``gh``.
2. **Local checks.** The version must be a stable ``MAJOR.MINOR.PATCH``, the file's name
   must be this platform's installer for that version, and the bundled ``gh``
   ([`tether._verifier.bundled_gh`][]) must report a version at or above
   [`GH_MIN_VERSION`][] (CVE-2025-25204 made ``gh attestation verify`` fail open below it).
3. **Tether fetches.** The SHA-256 of the file, then one unauthenticated ``GET`` of
   [`ATTESTATIONS_API`][] for that digest, read as explicit UTF-8, and the Sigstore trusted
   root from ``gh attestation trusted-root``.
4. **``gh`` verifies, offline.** ``gh attestation verify`` with ``--bundle``,
   ``--custom-trusted-root``, ``--repo``, ``--signer-workflow``, ``--source-ref`` and
   ``--deny-self-hosted-runners``, run with every proxy variable pointed at a closed local
   port, so it cannot reach the network even if it tried.
5. **Tether's own policy on the verified output.** From
   ``verificationResult.signature.certificate`` (never from the statement): the source
   repository, and ``sourceRepositoryRef`` matched against the allowlist
   ``refs/tags/vMAJOR.MINOR.PATCH`` and required to equal the version being installed. From
   the verified statement: a subject whose name is this platform's installer and whose
   digest is the file's.

**Refuse-and-report has a limit** (ADR-0060). ``gh`` collapses every verification outcome
to exit 1, so the split is drawn by *which step* failed, which is why step 4 has no network
left in it:

- a step that needs the network failing (offline, a proxy that refuses, a rate limit, an
  answer that is not GitHub's) is [`Outcome.UNAVAILABLE`][]: **silent**, nothing logged
  above ``DEBUG``, nothing shown;
- everything else is [`Outcome.REFUSED`][]: verification ran and failed, and the result
  carries a sentence for the user. That includes anything that cannot be classified at
  all, because refusing loudly when unsure is the safe error.

One network answer is classified as *ran and failed*, not *could not run*: GitHub answering
that it holds **no attestation for these bytes** (HTTP 404, or an empty list). That is an
answer about the file, not a failure to reach anyone, and a tampered or corrupted download
produces exactly it. Either way nothing is installed; the classification decides only
whether the user is told.

**There is no "install anyway".** No argument, setting or environment variable turns any
of the checks off.

**What leaves the machine.** The attestations ``GET`` carries the file's SHA-256 in its
path, ``Accept`` and ``User-Agent: Tether``. ``gh attestation trusted-root`` reaches the
Sigstore TUF service (``tuf-repo-cdn.sigstore.dev``) and GitHub's (``tuf-repo.github.com``).
``gh`` runs with no token (every ``GH_*``/``GITHUB_*`` token variable removed), a fresh
empty configuration and cache folder per check, and its own telemetry switched off
(``GH_TELEMETRY=false``, ``DO_NOT_TRACK=1``): ``gh`` 2.104.0 otherwise reports each command
with a device id. ``docs/privacy.md`` lists every endpoint.

**Blocking.** [`verify_update`][] hashes the file and waits on the network and on ``gh``,
for up to [`TRUSTED_ROOT_TIMEOUT_S`][] and [`VERIFY_TIMEOUT_S`][] plus the request timeout.
It must never run on the GUI thread.

The module imports only the standard library and two Qt-free Tether modules.
"""

from __future__ import annotations

import enum
import hashlib
import http.client
import json
import logging
import os
import platform as _platform_mod
import re
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

from tether import _verifier
from tether.gui import app_settings
from tether.gui.update_check import DEFAULT_TIMEOUT_S, Version

__all__ = [
    "ATTESTATIONS_API",
    "GH_MIN_VERSION",
    "REPOSITORY",
    "SIGNER_WORKFLOW",
    "TRUSTED_ROOT_TIMEOUT_S",
    "VERIFY_TIMEOUT_S",
    "CertificateFacts",
    "Fetch",
    "GhRun",
    "Outcome",
    "Runner",
    "VerificationRefused",
    "VerificationResult",
    "VerificationUnavailable",
    "VerifiedInstaller",
    "channel_version",
    "check_channel",
    "check_gh_version",
    "check_source",
    "check_subject",
    "fetch_attestation_bundles",
    "fetch_trusted_root",
    "installer_name",
    "platform_tag",
    "run_gh",
    "stable_ref_version",
    "verify_provenance",
    "verify_update",
]

_LOG = logging.getLogger(__name__)

REPOSITORY = "bioedca/tether"
"""The only repository whose attestations are accepted (``gh --repo``)."""

SIGNER_WORKFLOW = "bioedca/tether/.github/workflows/release.yml"
"""The only workflow whose signature is accepted (``gh --signer-workflow``)."""

ATTESTATIONS_API = "https://api.github.com/repos/bioedca/tether/attestations/sha256:{digest}"
"""The attestations endpoint, public and unauthenticated; ``{digest}`` is 64 hex digits."""

GH_MIN_VERSION = (2, 67, 0)
"""The lowest ``gh`` accepted at runtime. CVE-2025-25204 (GHSA-fgw4-v983-mgp8): in 2.49.0
through 2.66.x ``gh attestation verify`` exited 0 on a predicate-type mismatch."""

TRUSTED_ROOT_TIMEOUT_S = 30.0
"""Seconds ``gh attestation trusted-root`` may take before the check is abandoned in
silence (PRD §11.2)."""

VERIFY_TIMEOUT_S = 120.0
"""Seconds each offline ``gh`` call (``--version``, then ``attestation verify``, which hashes
the installer) may take before the update is refused (PRD §11.2)."""

_API_HOST = "api.github.com"
_USER_AGENT = "Tether"
_SOURCE_REPOSITORY_URI = f"https://github.com/{REPOSITORY}"
# A real answer for one digest is one bundle of about 12 KB; gh's own default limit is 30.
_MAX_ANSWER_BYTES = 4 * 1024 * 1024
_MAX_ROOT_BYTES = 4 * 1024 * 1024
_MAX_GH_OUTPUT_BYTES = 16 * 1024 * 1024
_HASH_CHUNK = 1024 * 1024
_HEX64 = re.compile(r"[0-9a-f]{64}")
# re.ASCII and [0-9]: \d would also match other scripts' digits. fullmatch, not $, which
# also matches before a trailing newline.
_STABLE_REF = re.compile(r"refs/tags/v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", re.ASCII)
_GH_VERSION_LINE = re.compile(r"gh version ([0-9]+)\.([0-9]+)\.([0-9]+)(?:[ -]|$)", re.ASCII)
# A local port nothing listens on: every proxy variable points here while gh verifies.
_BLACKHOLE_PROXY = "http://127.0.0.1:9"
_PROXY_VARIABLES = (
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "ALL_PROXY",
    "all_proxy",
)
_NO_PROXY_VARIABLES = ("NO_PROXY", "no_proxy")
# Removed from gh's environment: credentials, anything that redirects it to another host or
# repository, and anything that changes its output.
_SCRUBBED_VARIABLES = (
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GH_ENTERPRISE_TOKEN",
    "GITHUB_ENTERPRISE_TOKEN",
    "GH_HOST",
    "GH_REPO",
    "GH_DEBUG",
    "DEBUG",
    "GH_FORCE_TTY",
    "GH_PAGER",
    "PAGER",
    "CLICOLOR_FORCE",
    "GH_PATH",
)

# Installer names as release.yml publishes them (Tether-1.0.0-rc1-Linux-x86_64.sh and so
# on, measured against the v1.0.0-rc1..rc3 assets on 2026-11-19), keyed by
# (sys.platform family, normalised machine).
_INSTALLER_SUFFIX = {
    ("linux", "x86_64"): "Linux-x86_64.sh",
    ("darwin", "arm64"): "MacOSX-arm64.pkg",
    ("darwin", "x86_64"): "MacOSX-x86_64.pkg",
    ("win32", "x86_64"): "Windows-x86_64.exe",
}
_MACHINE_ALIASES = {"amd64": "x86_64", "x86_64": "x86_64", "x64": "x86_64", "arm64": "arm64"}
_CANONICAL_STABLE = re.compile(r"([0-9]+)\.([0-9]+)\.([0-9]+)", re.ASCII)


class Outcome(enum.Enum):
    """How a verification ended."""

    VERIFIED = "verified"
    """Every check passed; [`VerificationResult.installer`][] holds the file."""

    REFUSED = "refused"
    """Verification ran and failed, or could not be classified. Refuse, and tell the user."""

    UNAVAILABLE = "unavailable"
    """Verification could not run (no consent, no network, no verifier). Refuse in silence."""


class VerificationRefused(Exception):
    """A check ran and failed. ``str()`` is a sentence the user can be shown."""


class VerificationUnavailable(Exception):
    """A check could not run. Never shown to the user; logged at ``DEBUG`` only."""


class GhRun(NamedTuple):
    """What one ``gh`` invocation produced."""

    returncode: int
    stdout: bytes
    stderr: bytes


Runner = Callable[[Sequence[str], Mapping[str, str], float], GhRun]
"""Run ``argv`` with exactly ``env`` for at most the given seconds. Raises
``subprocess.TimeoutExpired`` or ``OSError``."""

Fetch = Callable[[str], bytes | None]
"""``GET`` one URL and return the body; ``None`` for HTTP 404. Raises
[`VerificationUnavailable`][] for every other failure."""


@dataclass(frozen=True)
class CertificateFacts:
    """What one verified attestation says, as Tether's policy reads it.

    Built only from ``gh attestation verify --format json`` output after ``gh`` exited 0.
    The source fields come from ``verificationResult.signature.certificate``, which ``gh``
    documents as non-manipulable; ``subjects`` come from the verified statement.
    """

    source_repository_uri: str
    """``sourceRepositoryURI``, e.g. ``https://github.com/bioedca/tether``."""

    source_ref: str
    """``sourceRepositoryRef``, e.g. ``refs/tags/v1.0.0``."""

    source_digest: str
    """``sourceRepositoryDigest``, the commit the release was built from."""

    subjects: tuple[tuple[str, str], ...]
    """``(name, sha256)`` for every subject of the verified statement."""


_MINT = object()


class VerifiedInstaller:
    """A downloaded installer that passed every check. Only [`verify_update`][] makes one.

    Constructing one directly raises ``TypeError``: holding one is the proof that
    verification succeeded, and the hand-off (#331) accepts nothing else. It records what
    was verified, not a promise about the file's future: the file can still be replaced
    after this was made, and closing that window is #331's work (ADR-0060 threat model).
    """

    __slots__ = ("_path", "_sha256", "_source_digest", "_version")

    def __init__(
        self, token: object, path: Path, version: Version, sha256: str, source_digest: str
    ) -> None:
        if token is not _MINT:
            raise TypeError("a VerifiedInstaller is made only by tether.gui.update_verify")
        object.__setattr__(self, "_path", path)
        object.__setattr__(self, "_version", version)
        object.__setattr__(self, "_sha256", sha256)
        object.__setattr__(self, "_source_digest", source_digest)

    def __setattr__(self, name: str, value: object) -> None:
        """Refuse: a verified record cannot be changed."""
        raise AttributeError("a VerifiedInstaller cannot be changed")

    def __delattr__(self, name: str) -> None:
        """Refuse: a verified record cannot be changed."""
        raise AttributeError("a VerifiedInstaller cannot be changed")

    def __reduce__(self) -> Any:
        """Refuse: a copy would be a second handle not made by the verifier."""
        raise TypeError("a VerifiedInstaller cannot be pickled or copied")

    @property
    def path(self) -> Path:
        """The installer file that was verified."""
        return self._path

    @property
    def version(self) -> Version:
        """The release version, equal to the version in the certificate's source tag."""
        return self._version

    @property
    def sha256(self) -> str:
        """The SHA-256 of the file as it was verified, 64 lower-case hex digits."""
        return self._sha256

    @property
    def source_digest(self) -> str:
        """The commit the attestation says the installer was built from."""
        return self._source_digest

    def __repr__(self) -> str:
        """Return the file name and the version."""
        return f"VerifiedInstaller({self._path.name!r}, {str(self._version)!r})"


@dataclass(frozen=True)
class VerificationResult:
    """The outcome of [`verify_update`][]. ``installer`` is set only for *verified*."""

    outcome: Outcome
    reason: str
    """For *refused*, a sentence for the user; for *unavailable*, a note for ``DEBUG`` logs."""

    installer: VerifiedInstaller | None = None

    def __post_init__(self) -> None:
        """Refuse an installer on a result that is not *verified*, and the reverse."""
        if self.outcome is Outcome.VERIFIED:
            valid = isinstance(self.installer, VerifiedInstaller)
        else:
            valid = self.installer is None
        if not valid:
            raise ValueError("only a verified result carries an installer, and it must")


# --- local checks ---------------------------------------------------------------------


def channel_version(version: Version) -> tuple[int, int, int] | None:
    """``(major, minor, patch)`` when ``version`` is a stable release; else ``None``.

    Stable means three numbers at most and nothing else: no pre-release, post-release or
    development part. ``1.0`` is ``1.0.0``.
    """
    if not isinstance(version, Version):
        return None
    # str() is the canonical PEP 440 spelling, padded to three numbers: 1.0.0, 1.0.0rc1,
    # 1.0.0.post1, 1.0.1.dev3, 1.0.0.1. Only the first is stable.
    match = _CANONICAL_STABLE.fullmatch(str(version))
    if match is None:
        return None
    return (int(match[1]), int(match[2]), int(match[3]))


def platform_tag(system: str | None = None, machine: str | None = None) -> str | None:
    """Return this platform's installer-name suffix, e.g. ``Linux-x86_64.sh``, or ``None``.

    ``system`` defaults to [`sys.platform`][] and ``machine`` to
    [`platform.machine`][]. ``None`` means no installer is published for this platform.
    """
    system = sys.platform if system is None else system
    machine = _platform_mod.machine() if machine is None else machine
    family = "win32" if system.startswith("win") else system
    family = "linux" if family.startswith("linux") else family
    normal = _MACHINE_ALIASES.get(machine.lower())
    if normal is None:
        return None
    return _INSTALLER_SUFFIX.get((family, normal))


def installer_name(version: Version, tag: str | None = None) -> str | None:
    """Return this platform's installer file name for a stable ``version``, or ``None``.

    ``Tether-1.0.0-Linux-x86_64.sh`` and so on. ``tag`` defaults to [`platform_tag`][].
    """
    triple = channel_version(version)
    tag = platform_tag() if tag is None else tag
    if triple is None or tag is None:
        return None
    return f"Tether-{triple[0]}.{triple[1]}.{triple[2]}-{tag}"


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


# --- running gh ------------------------------------------------------------------------


def run_gh(argv: Sequence[str], env: Mapping[str, str], timeout: float) -> GhRun:
    """Run ``gh`` with no stdin and no console window: the default [`Runner`][]."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    completed = subprocess.run(  # noqa: S603 - argv is built here, never from input
        list(argv),
        env=dict(env),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=timeout,
        check=False,
        creationflags=flags,
    )
    return GhRun(completed.returncode, completed.stdout or b"", completed.stderr or b"")


def _gh_env(
    workdir: Path, *, offline: bool, base: Mapping[str, str] | None = None
) -> dict[str, str]:
    """``gh``'s environment: no credentials, a fresh config and cache, no telemetry.

    ``offline`` points every proxy variable at a closed local port and empties
    ``NO_PROXY``, so a network call from ``gh`` fails instead of leaving the machine.
    """
    env = dict(os.environ if base is None else base)
    for name in _SCRUBBED_VARIABLES:
        env.pop(name, None)
    config = workdir / "gh-config"
    cache = workdir / "gh-cache"
    config.mkdir(exist_ok=True)
    cache.mkdir(exist_ok=True)
    env.update(
        {
            "GH_CONFIG_DIR": str(config),
            # gh keeps its TUF cache under $XDG_CACHE_HOME/gh on every OS when it is set,
            # so no cache a user can write is consulted.
            "XDG_CACHE_HOME": str(cache),
            "GH_TELEMETRY": "false",
            "DO_NOT_TRACK": "1",
            "GH_NO_UPDATE_NOTIFIER": "1",
            "GH_NO_EXTENSION_UPDATE_NOTIFIER": "1",
            "GH_PROMPT_DISABLED": "1",
            "GH_SPINNER_DISABLED": "1",
            "NO_COLOR": "1",
        }
    )
    if offline:
        for name in _PROXY_VARIABLES:
            env[name] = _BLACKHOLE_PROXY
        for name in _NO_PROXY_VARIABLES:
            env[name] = ""
    return env


def check_gh_version(gh: Path, workdir: Path, *, runner: Runner = run_gh) -> tuple[int, int, int]:
    """Run ``gh --version`` offline and return its version when it is at or above the floor.

    Raises [`VerificationRefused`][] when it is below [`GH_MIN_VERSION`][], when its output
    names no version, or when it cannot be run: a verifier that cannot say what it is is
    not one to trust.
    """
    env = _gh_env(workdir, offline=True)
    try:
        run = runner([str(gh), "--version"], env, VERIFY_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as error:
        raise VerificationRefused("Tether's update verifier could not be started.") from error
    first = run.stdout.decode("utf-8", errors="replace").splitlines()[:1]
    match = _GH_VERSION_LINE.match(first[0].strip()) if first and run.returncode == 0 else None
    if match is None:
        raise VerificationRefused("Tether's update verifier did not report its version.")
    found = (int(match[1]), int(match[2]), int(match[3]))
    if found < GH_MIN_VERSION:
        floor = ".".join(map(str, GH_MIN_VERSION))
        raise VerificationRefused(
            f"Tether's update verifier is gh {'.'.join(map(str, found))}, older than {floor}, "
            "which is the oldest version that verifies safely."
        )
    return found


# --- step 3: Tether fetches ---------------------------------------------------------------


class _ApiOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to ``https://api.github.com``; refuse every other target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        target = urllib.parse.urlsplit(newurl)
        if target.scheme != "https" or target.hostname != _API_HOST or target.port:
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _default_fetch(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
    opener: urllib.request.OpenerDirector | None = None,
) -> bytes | None:
    """Make one unauthenticated ``GET`` to ``api.github.com``: the default [`Fetch`][]."""
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": _USER_AGENT},
        method="GET",
    )
    # build_opener keeps the default ProxyHandler and the default HTTPS handler
    # (certificate and host-name verification on). ``opener`` is for tests.
    opener = opener or urllib.request.build_opener(_ApiOnlyRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            if getattr(response, "status", None) != 200:
                raise VerificationUnavailable(f"HTTP {getattr(response, 'status', None)}")
            body = response.read(_MAX_ANSWER_BYTES + 1)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise VerificationUnavailable(f"HTTP {error.code}") from error
    except (OSError, ValueError, http.client.HTTPException) as error:
        raise VerificationUnavailable(type(error).__name__) from error
    if not isinstance(body, bytes) or len(body) > _MAX_ANSWER_BYTES:
        raise VerificationUnavailable("the answer is too large")
    return body


def fetch_attestation_bundles(digest: str, *, fetch: Fetch = _default_fetch) -> list[dict]:
    """Fetch the attestation bundles GitHub holds for ``digest`` (64 hex digits).

    The answer is decoded as **explicit UTF-8**: the Rekor checkpoint carries an em-dash,
    and a platform default such as ``cp1252`` corrupts it into a verification failure that
    looks exactly like tampering (ADR-0060). Only inline bundles are used; an attestation
    that carries only ``bundle_url`` is skipped, because that URL names a storage host
    outside the endpoints the privacy page lists.

    Raises [`VerificationRefused`][] when GitHub answers that it holds no attestation for
    these bytes (HTTP 404 or an empty list), and [`VerificationUnavailable`][] for anything
    else that is not a usable answer, an answer with no inline bundle included.
    """
    if not isinstance(digest, str) or not _HEX64.fullmatch(digest):
        raise VerificationRefused("The downloaded file's checksum could not be computed.")
    body = fetch(ATTESTATIONS_API.format(digest=digest))
    if body is None:
        raise VerificationRefused(
            "GitHub holds no build-provenance attestation for the downloaded file, so it is "
            "not a file Tether's release workflow built."
        )
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise VerificationUnavailable("the attestations answer is not UTF-8 JSON") from error
    entries = data.get("attestations") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise VerificationUnavailable("the attestations answer has no list")
    if not entries:
        raise VerificationRefused(
            "GitHub holds no build-provenance attestation for the downloaded file, so it is "
            "not a file Tether's release workflow built."
        )
    bundles = [
        entry["bundle"]
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("bundle"), dict)
    ]
    if not bundles:
        raise VerificationUnavailable("no attestation carries an inline bundle")
    return bundles


def _write_bundles(bundles: Sequence[dict], path: Path) -> None:
    """Write ``bundles`` as JSON lines, ASCII-only, so no platform encoding can touch them."""
    lines = (json.dumps(b, ensure_ascii=True, separators=(",", ":")) for b in bundles)
    path.write_bytes(("\n".join(lines) + "\n").encode("ascii"))


def fetch_trusted_root(
    gh: Path, workdir: Path, *, runner: Runner = run_gh, timeout: float = TRUSTED_ROOT_TIMEOUT_S
) -> Path:
    """Fetch the Sigstore trusted root with ``gh attestation trusted-root``; return its file.

    Fetched per check, never pinned: a pinned root goes stale and would refuse every
    genuine release (ADR-0060). Raises [`VerificationUnavailable`][] for any failure: this
    is a network step, so failing here means verification could not run.
    """
    env = _gh_env(workdir, offline=False)
    try:
        run = runner([str(gh), "attestation", "trusted-root"], env, timeout)
    except (OSError, subprocess.SubprocessError) as error:
        raise VerificationUnavailable(f"trusted-root: {type(error).__name__}") from error
    if run.returncode != 0:
        raise VerificationUnavailable(f"trusted-root exited {run.returncode}")
    if not run.stdout or len(run.stdout) > _MAX_ROOT_BYTES:
        raise VerificationUnavailable("trusted-root printed no usable root")
    try:
        lines = [line for line in run.stdout.decode("utf-8").splitlines() if line.strip()]
        roots = [json.loads(line) for line in lines]
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise VerificationUnavailable("trusted-root printed something that is not JSON") from error
    if not roots or not all(isinstance(root, dict) and root for root in roots):
        raise VerificationUnavailable("trusted-root printed no usable root")
    path = workdir / "trusted_root.jsonl"
    path.write_bytes(run.stdout)
    return path


# --- step 4: gh verifies, offline ----------------------------------------------------------


def _facts(item: object) -> CertificateFacts:
    """Read one ``gh --format json`` result. Raises [`VerificationRefused`][] if malformed."""
    try:
        result = item["verificationResult"]  # type: ignore[index]
        certificate = result["signature"]["certificate"]
        subjects = result["statement"]["subject"]
        facts = CertificateFacts(
            source_repository_uri=certificate["sourceRepositoryURI"],
            source_ref=certificate["sourceRepositoryRef"],
            source_digest=certificate["sourceRepositoryDigest"],
            subjects=tuple((s["name"], s["digest"]["sha256"]) for s in subjects),
        )
    except (KeyError, TypeError, IndexError) as error:
        raise VerificationRefused("The verifier's report could not be read.") from error
    strings = (facts.source_repository_uri, facts.source_ref, facts.source_digest)
    if not all(isinstance(v, str) for v in strings) or not all(
        isinstance(n, str) and isinstance(d, str) for n, d in facts.subjects
    ):
        raise VerificationRefused("The verifier's report could not be read.")
    return facts


def verify_provenance(
    installer: Path,
    bundle: Path,
    trusted_root: Path,
    *,
    gh: Path,
    workdir: Path,
    source_ref: str | None = None,
    runner: Runner = run_gh,
    timeout: float = VERIFY_TIMEOUT_S,
) -> list[CertificateFacts]:
    """Run ``gh attestation verify`` offline and return what it verified: the verifier layer.

    The invocation always carries ``--bundle``, ``--custom-trusted-root``, ``--repo``
    [`REPOSITORY`][], ``--signer-workflow`` [`SIGNER_WORKFLOW`][] and
    ``--deny-self-hosted-runners``, and ``--source-ref`` when ``source_ref`` is given. It
    runs with every proxy pointed at a closed port, so there is no network left in it to
    fail: **any** non-zero exit, a timeout, or output that cannot be read raises
    [`VerificationRefused`][]. This layer makes no channel decision; the certificate's
    source tag is returned for the caller's policy, whatever it is.
    """
    argv = [
        str(gh),
        "attestation",
        "verify",
        str(installer),
        "--bundle",
        str(bundle),
        "--custom-trusted-root",
        str(trusted_root),
        "--repo",
        REPOSITORY,
        "--signer-workflow",
        SIGNER_WORKFLOW,
        "--deny-self-hosted-runners",
        "--format",
        "json",
    ]
    if source_ref is not None:
        argv += ["--source-ref", source_ref]
    env = _gh_env(workdir, offline=True)
    try:
        run = runner(argv, env, timeout)
    except (OSError, subprocess.SubprocessError) as error:
        raise VerificationRefused("The update could not be verified.") from error
    if run.returncode != 0:
        _LOG.debug("gh attestation verify exited %s: %r", run.returncode, run.stderr[-2000:])
        raise VerificationRefused(
            "The downloaded update does not match the build-provenance attestation of a "
            "Tether release, so it was not installed."
        )
    if len(run.stdout) > _MAX_GH_OUTPUT_BYTES:
        raise VerificationRefused("The verifier's report could not be read.")
    try:
        results = json.loads(run.stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise VerificationRefused("The verifier's report could not be read.") from error
    if not isinstance(results, list) or not results:
        raise VerificationRefused("The verifier's report could not be read.")
    return [_facts(item) for item in results]


# --- step 5: Tether's policy on the verified certificate -------------------------------------


def stable_ref_version(ref: object) -> tuple[int, int, int] | None:
    """``(major, minor, patch)`` when ``ref`` is exactly ``refs/tags/vMAJOR.MINOR.PATCH``."""
    if not isinstance(ref, str):
        return None
    match = _STABLE_REF.fullmatch(ref)
    if match is None:
        return None
    return (int(match[1]), int(match[2]), int(match[3]))


def check_channel(facts: CertificateFacts, version: Version) -> None:
    """Require the certificate's tag to be stable and to be ``version``: the channel layer.

    Raises [`VerificationRefused`][] unless the certificate's ``sourceRepositoryRef`` is
    exactly ``refs/tags/vMAJOR.MINOR.PATCH`` (an allowlist: ``refs/heads/main``,
    ``v1.0.0.0`` and ``v1.0.0-rc1`` all fail it) and its version equals ``version``, the
    one the strictly-newer comparison used.
    """
    from_certificate = stable_ref_version(facts.source_ref)
    if from_certificate is None:
        raise VerificationRefused(
            "The downloaded update was not built from a stable release tag, so it was not "
            "installed. Tether installs only stable releases."
        )
    if from_certificate != channel_version(version):
        raise VerificationRefused(
            "The downloaded update was built for a different version than the one offered, "
            "so it was not installed."
        )


def check_subject(facts: CertificateFacts, name: str, digest: str) -> None:
    """Require the verified statement to name this file, by this platform's name and digest."""
    if (name, digest) not in facts.subjects:
        raise VerificationRefused(
            "The attestation does not name the downloaded file as this platform's "
            "installer, so it was not installed."
        )


def check_source(facts: CertificateFacts) -> None:
    """Require the certificate's source repository to be Tether's (``gh --repo`` checks too)."""
    if facts.source_repository_uri != _SOURCE_REPOSITORY_URI:
        raise VerificationRefused(
            "The downloaded update was not built from Tether's repository, so it was not installed."
        )


# --- the whole sequence -----------------------------------------------------------------


def verify_update(
    installer: str | os.PathLike[str],
    version: Version,
    *,
    settings_file: Path | None = None,
    override_file: Path | None = None,
    gh: Path | None = None,
    tag: str | None = None,
    fetch: Fetch = _default_fetch,
    runner: Runner = run_gh,
) -> VerificationResult:
    """Verify a downloaded installer for ``version``; never raises.

    ``version`` is the release the strictly-newer comparison chose
    ([`tether.gui.update_check.check_for_update`][]). The result is *verified* with a
    [`VerifiedInstaller`][] only when every step in the module description passed;
    otherwise *refused* (tell the user ``reason``) or *unavailable* (say nothing).

    ``settings_file`` and ``override_file`` go to
    [`tether.gui.app_settings.update_check_state`][]; ``gh`` defaults to
    [`tether._verifier.bundled_gh`][] and ``tag`` to [`platform_tag`][]. ``fetch`` and
    ``runner`` are for tests.
    """
    try:
        return _verify(installer, version, settings_file, override_file, gh, tag, fetch, runner)
    except VerificationUnavailable as error:
        _LOG.debug("update verification could not run: %s", error)
        return VerificationResult(Outcome.UNAVAILABLE, str(error))
    except VerificationRefused as error:
        return VerificationResult(Outcome.REFUSED, str(error))
    except Exception as error:  # noqa: BLE001 - unclassifiable: refuse, never run
        _LOG.debug("update verification failed unexpectedly", exc_info=True)
        return VerificationResult(
            Outcome.REFUSED, f"The update could not be verified ({type(error).__name__})."
        )


def _verify(
    installer: str | os.PathLike[str],
    version: Version,
    settings_file: Path | None,
    override_file: Path | None,
    gh: Path | None,
    tag: str | None,
    fetch: Fetch,
    runner: Runner,
) -> VerificationResult:
    # 1. Consent: before anything else, so nothing below can run without it.
    state = app_settings.update_check_state(settings_file, override_file)
    if not state.network_allowed:
        raise VerificationUnavailable(f"update checks are {state.value}")

    # 2. Local checks.
    if channel_version(version) is None:
        raise VerificationRefused(
            "The offered update is not a stable release. Tether installs only stable releases."
        )
    tag = platform_tag() if tag is None else tag
    expected = installer_name(version, tag)
    if expected is None:
        raise VerificationUnavailable("no installer is published for this platform")
    path = Path(installer)
    if path.name != expected:
        raise VerificationRefused(
            "The downloaded file is not this platform's installer for the offered release, "
            "so it was not installed."
        )
    try:
        info = os.lstat(path)
    except OSError as error:
        raise VerificationRefused("The downloaded update could not be read.") from error
    if not stat.S_ISREG(info.st_mode):
        raise VerificationRefused("The downloaded update is not an ordinary file.")
    gh = _verifier.bundled_gh() if gh is None else gh
    if gh is None:
        raise VerificationUnavailable("this copy of Tether has no bundled verifier")

    with tempfile.TemporaryDirectory(prefix="tether-verify-") as scratch:
        workdir = Path(scratch)
        check_gh_version(gh, workdir, runner=runner)
        try:
            digest = _sha256_of(path)
        except OSError as error:
            raise VerificationRefused("The downloaded update could not be read.") from error

        # 3. Tether fetches (the only network in the sequence).
        bundles = fetch_attestation_bundles(digest, fetch=fetch)
        bundle_file = workdir / "bundle.jsonl"
        _write_bundles(bundles, bundle_file)
        root_file = fetch_trusted_root(gh, workdir, runner=runner)

        # 4. gh verifies, offline.
        triple = channel_version(version)
        assert triple is not None  # checked in step 2
        source_ref = "refs/tags/v{}.{}.{}".format(*triple)
        verified = verify_provenance(
            path,
            bundle_file,
            root_file,
            gh=gh,
            workdir=workdir,
            source_ref=source_ref,
            runner=runner,
        )

        # 5. Tether's policy, on every verified result.
        for facts in verified:
            check_source(facts)
            check_channel(facts, version)
            check_subject(facts, expected, digest)

    return VerificationResult(
        Outcome.VERIFIED,
        "verified",
        VerifiedInstaller(_MINT, path, version, digest, verified[0].source_digest),
    )

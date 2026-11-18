# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Ask GitHub whether a newer Tether release exists: the check half of FR-UPDATE (#248).

This module is the network side of the opt-in update notice, and nothing else. It makes
**one** unauthenticated ``GET`` of [`LATEST_RELEASE_API`][], reads one field of the
answer, ``tag_name``, and compares that version with the installed one. It downloads
nothing, verifies nothing and runs nothing; the user downloads and installs a release
themselves from the fixed [`RELEASES_PAGE`][].

**It never decides whether to ask.** The consent answer and the administrator override
belong to [`tether.gui.app_settings`][] (ADR-0095), and the caller,
[`tether.gui.update_notice`][], reads them first. Nothing here reads them, so nothing
here can make a request on its own: a test fails if any outbound connection is tried
while the answer is not *allowed*.

**It makes no channel decision.** GitHub answers ``releases/latest`` with "the most
recent non-prerelease, non-draft release" (GitHub REST documentation, *Get the latest
release*, read through Context7 on 2026-11-17). That server-side filter is a convenience,
not a control: ADR-0060 decision 4 assumes an attacker can control the release query,
and with it the ``prerelease`` flag. This module therefore never reads that flag or any
other field but ``tag_name``, and it does not derive "stable" from the tag either. That
decision belongs to the verified certificate's ``sourceRepositoryRef``, which #333 reads
before anything is installed. What an attacker who controls the answer can do here is
make the notice name a version; the link always goes to the fixed Releases page, never
to a URL taken from the answer.

**Every failure is silent.** No network, a proxy that refuses, a timeout, a rate limit
(HTTP 403 or 429 with ``X-RateLimit-Remaining: 0``), a 404 because no full release
exists yet, a redirect to another host, an oversized body, malformed JSON or a tag that
is not a version all give ``None``, never an exception, a dialog or a log line at a level
a user sees. An air-gapped machine must not be able to tell the feature exists
(ADR-0060, *Refuse-and-report has a limit worth stating plainly*).

**Proxies.** The request goes through [`urllib.request`][], whose default
``ProxyHandler`` honours ``HTTPS_PROXY`` / ``HTTP_PROXY`` / ``NO_PROXY`` (and, on Windows
and macOS, the system proxy settings). TLS certificate and host-name verification use the
interpreter's defaults and stay on.

**What the request carries.** The method, the path, ``Accept: application/vnd.github+json``
and ``User-Agent: Tether``. No version, no installation id, no account, no cookie. The
endpoint necessarily sees the machine's IP address and that user-agent; ``docs/privacy.md``
says so.

The module imports only the standard library, so the comparison is usable and testable
without Qt.
"""

from __future__ import annotations

import http.client
import json
import re
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import NamedTuple

__all__ = [
    "DEFAULT_TIMEOUT_S",
    "LATEST_RELEASE_API",
    "RELEASES_PAGE",
    "Version",
    "check_for_update",
    "fetch_latest_release",
    "newer_release",
    "parse_version",
]

LATEST_RELEASE_API = "https://api.github.com/repos/bioedca/tether/releases/latest"
"""The one URL the update check requests (GitHub REST *Get the latest release*)."""

RELEASES_PAGE = "https://github.com/bioedca/tether/releases"
"""The page the notice links to. Fixed: never a URL taken from the API's answer."""

DEFAULT_TIMEOUT_S = 5.0
"""Seconds the request may take before it is abandoned in silence (PRD §11.2)."""

_API_HOST = "api.github.com"
_USER_AGENT = "Tether"
# A real releases/latest answer is a few kilobytes plus the asset list; anything far
# larger is not one and is not parsed.
_MAX_BYTES = 512 * 1024
_MAX_TAG_CHARS = 64

# One grammar for both spellings Tether's versions take: a release tag as release.yml
# publishes it (``v1.0.0``, ``v1.0.0-rc1``, ``v1.0.0-rc.1``) and the installed version
# hatch-vcs derives from git (PEP 440: ``1.0.0rc4.dev49+g46fb76b9c``). The local part
# after ``+`` is accepted and ignored, as PEP 440 orders public versions without it.
_VERSION = re.compile(
    r"""
    ^v?
    (?P<release>\d+(?:\.\d+)*)
    (?:[-.]?(?P<pre_label>alpha|a|beta|b|rc|c)[-.]?(?P<pre_n>\d+)?)?
    (?:[-.]?post[-.]?(?P<post>\d+))?
    (?P<dev_mark>[-.]?dev[-.]?(?P<dev>\d+)?)?
    (?:\+[0-9a-z]+(?:[-_.][0-9a-z]+)*)?
    $
    """,
    re.IGNORECASE | re.VERBOSE,
)
_PRE_RANK = {"a": 1, "alpha": 1, "b": 2, "beta": 2, "c": 3, "rc": 3}
_PRE_NAME = {1: "a", 2: "b", 3: "rc"}
_FINAL = 4  # ranks a release with no pre-release part after every pre-release
_DEV_ONLY = 0  # ranks X.devN before X's first pre-release, as PEP 440 does
_NO_DEV = float("inf")


class Version(NamedTuple):
    """A parsed version, ordered as PEP 440 orders public versions.

    Compare two with the ordinary operators: ``parse_version("v1.0.0-rc1") <
    parse_version("1.0.0")``. ``str()`` gives the canonical PEP 440 spelling, which is the
    only text of the API's answer the notice ever shows.
    """

    release: tuple[int, ...]
    """The release numbers with trailing zeros removed, so ``1.0`` equals ``1.0.0``."""

    pre: tuple[int, int]
    """``(rank, number)``: a dev-only build, then alpha, beta, rc, then the release."""

    post: int
    """The post-release number, or ``-1`` for none."""

    dev: float
    """The development number, or infinity for none, so a dev build sorts first."""

    def __str__(self) -> str:
        """Return the canonical spelling, e.g. ``1.0.0``, ``1.0.0rc1`` or ``1.0.1.dev3``."""
        release = list(self.release) or [0]
        while len(release) < 3:
            release.append(0)
        text = ".".join(str(part) for part in release)
        rank, number = self.pre
        if rank in _PRE_NAME:
            text += f"{_PRE_NAME[rank]}{number}"
        if self.post >= 0:
            text += f".post{self.post}"
        if self.dev != _NO_DEV:
            text += f".dev{int(self.dev)}"
        return text


def parse_version(text: object) -> Version | None:
    """Parse a release tag or an installed version; ``None`` when it is not one.

    Accepts ``v1.0.0``, ``v1.0.0-rc1``, ``v1.0.0-rc.1``, ``1.0.0rc1`` and hatch-vcs
    development versions such as ``1.0.0rc4.dev49+g46fb76b9c``. Anything else, including
    a non-string, an empty string and an over-long one, gives ``None``.
    """
    if not isinstance(text, str) or not text or len(text) > _MAX_TAG_CHARS:
        return None
    match = _VERSION.match(text.strip())
    if match is None:
        return None
    release = [int(part) for part in match["release"].split(".")]
    while len(release) > 1 and release[-1] == 0:
        release.pop()
    if release == [0]:
        release = []
    post = int(match["post"]) if match["post"] is not None else -1
    dev: float = _NO_DEV
    if match["dev_mark"] is not None:
        dev = int(match["dev"] or 0)
    label = match["pre_label"]
    if label is not None:
        pre = (_PRE_RANK[label.lower()], int(match["pre_n"] or 0))
    elif post < 0 and dev != _NO_DEV:
        pre = (_DEV_ONLY, 0)
    else:
        pre = (_FINAL, 0)
    return Version(tuple(release), pre, post, dev)


def newer_release(installed: str, payload: bytes | None) -> Version | None:
    """Return the version in a ``releases/latest`` answer when it is newer than ``installed``.

    Reads ``tag_name`` and no other field — in particular never ``prerelease``, ``draft``
    or a URL. ``None`` when the answer is missing, oversized, not UTF-8 JSON, not an
    object, has no usable tag, or names a version equal to or older than the installed one,
    and when the installed version itself cannot be parsed.
    """
    current = parse_version(installed)
    if current is None or payload is None or len(payload) > _MAX_BYTES:
        return None
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    latest = parse_version(data.get("tag_name"))
    if latest is None or latest <= current:
        return None
    return latest


class _ApiOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to ``https://api.github.com``; refuse every other target.

    The check is bounded to one host (PRD §7.12). A refused redirect surfaces as an
    ``HTTPError`` for the 3xx answer, which [`fetch_latest_release`][] turns into ``None``.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        target = urllib.parse.urlsplit(newurl)
        if target.scheme != "https" or target.hostname != _API_HOST or target.port:
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _default_opener() -> urllib.request.OpenerDirector:
    # build_opener keeps the default ProxyHandler (the proxy environment variables) and
    # the default HTTPS handler (certificate and host-name verification on).
    return urllib.request.build_opener(_ApiOnlyRedirect())


def fetch_latest_release(
    *, timeout: float = DEFAULT_TIMEOUT_S, opener: urllib.request.OpenerDirector | None = None
) -> bytes | None:
    """Request [`LATEST_RELEASE_API`][] once and return the body, or ``None`` on any failure.

    One ``GET``, no retry. Blocking for up to ``timeout`` seconds (and the name lookup,
    which the timeout does not bound), so it must never run on the GUI thread;
    [`tether.gui.update_notice`][] runs it on a background thread. ``opener`` is for tests.
    """
    request = urllib.request.Request(
        LATEST_RELEASE_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": _USER_AGENT},
        method="GET",
    )
    try:
        with (opener or _default_opener()).open(request, timeout=timeout) as response:
            if getattr(response, "status", None) != 200:
                return None
            body = response.read(_MAX_BYTES + 1)
    except (OSError, ValueError, http.client.HTTPException):
        # OSError covers URLError, HTTPError (403/429 rate limits, 404 when no full
        # release exists, a refused redirect), timeouts and refused connections.
        return None
    if not isinstance(body, bytes) or len(body) > _MAX_BYTES:
        return None
    return body


def check_for_update(
    installed: str | None = None,
    *,
    fetch: Callable[[], bytes | None] = fetch_latest_release,
) -> Version | None:
    """Fetch the latest release and return its version when newer than the installed one.

    ``installed`` defaults to ``tether.__version__``. Never raises for a network or data
    failure; the caller must already hold the user's consent.
    """
    if installed is None:
        from tether import __version__

        installed = __version__
    return newer_release(installed, fetch())

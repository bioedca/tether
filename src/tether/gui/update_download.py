# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Download the installer of an offered release into a private folder (#1379).

This is the download leg of FR-UPDATE (PRD §7.12, ADR-0060 decision 1: *detect, download,
verify, hand off*). It fetches one file, this platform's installer for one stable release, and
nothing else. It verifies nothing and runs nothing: the file it returns goes to
[`tether.gui.update_verify.verify_update`][] and, only when that says *verified*, to
[`tether.gui.update_handoff.hand_off`][]. [`tether.gui.update_notice`][] runs the three in that
order, after the user pressed **Install update**.

**One URL, pinned.** The request is ``GET`` [`DOWNLOAD_BASE`][] + ``v<version>/<installer>``,
where the installer name is [`tether.gui.update_verify.installer_name`][] and the version is
the stable release the update check offered, re-spelled from its parsed form. No part of the
URL comes from GitHub's answer. GitHub answers that URL with a redirect to its release-asset
host (measured 2026-11-25 on ``v1.0.0-rc3``: HTTP 302 to
``https://release-assets.githubusercontent.com/github-production-release-asset/…``), so a
redirect is followed **only** to [`ASSET_HOST`][] or back to ``github.com``, over HTTPS on the
default port; any other target ends the download.

**Into a private folder.** The file is written to a folder that this call creates (mode
``0700`` on macOS and Linux; on Windows inside the user's own temporary folder), first under a
``.part`` name and then renamed to the installer name once every byte arrived, because
``verify_update`` accepts only that exact name. A failed or cancelled download removes the
folder. The caller removes it after the hand-off, which copies the file into a folder of its
own first.

**Bounded.** Each read may wait at most ``timeout`` seconds ([`DOWNLOAD_TIMEOUT_S`][]); a
body larger than [`MAX_INSTALLER_BYTES`][], or shorter or longer than the length the server
announced, is refused. A ``cancelled`` callable is asked between reads.

**What the request carries.** ``Accept: application/octet-stream`` and ``User-Agent: Tether``,
plus what the standard library adds (``Host``, ``Accept-Encoding: identity``,
``Connection: close``). No account, no token, no installation id. ``docs/privacy.md`` names
both hosts. Proxies and TLS verification are the standard library's defaults, as for the
update check ([`tether.gui.update_check`][]).

It must never run on the GUI thread. The module imports only the standard library and two
Qt-free Tether modules.
"""

from __future__ import annotations

import enum
import http.client
import os
import shutil
import tempfile
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tether.gui.update_check import Version
from tether.gui.update_verify import channel_version, installer_name

__all__ = [
    "ASSET_HOST",
    "DOWNLOAD_BASE",
    "DOWNLOAD_TIMEOUT_S",
    "MAX_INSTALLER_BYTES",
    "DownloadOutcome",
    "DownloadResult",
    "Progress",
    "download_installer",
    "download_url",
]

DOWNLOAD_BASE = "https://github.com/bioedca/tether/releases/download/"
"""The fixed prefix of every installer URL; the tag and the file name follow it."""

ASSET_HOST = "release-assets.githubusercontent.com"
"""The one host besides ``github.com`` that a download may be redirected to."""

DOWNLOAD_TIMEOUT_S = 60.0
"""Seconds one read of the download may wait for data before the download is abandoned
(PRD §11.2). It bounds a stalled connection, not the whole download."""

MAX_INSTALLER_BYTES = 4 * 1024**3
"""The largest installer Tether downloads. A 1.0 release installer is about 0.8 GB."""

_GITHUB_HOST = "github.com"
_ALLOWED_HOSTS = frozenset({_GITHUB_HOST, ASSET_HOST})
_USER_AGENT = "Tether"
_CHUNK = 1024 * 1024

Progress = Callable[[int, int | None], None]
"""Called with the bytes received so far and the announced total (``None`` when unknown)."""


class DownloadOutcome(enum.Enum):
    """How [`download_installer`][] ended."""

    DOWNLOADED = "downloaded"
    """Every byte arrived; [`DownloadResult.path`][] is the installer, under its own name."""

    CANCELLED = "cancelled"
    """The user cancelled; the folder was removed."""

    FAILED = "failed"
    """The download did not finish (network, redirect, size); the folder was removed."""


@dataclass(frozen=True)
class DownloadResult:
    """The outcome of [`download_installer`][]."""

    outcome: DownloadOutcome
    path: Path | None = None
    """The installer, set only for *downloaded*. Its folder is the caller's to remove."""

    reason: str = ""
    """For *failed*, a few words for a ``DEBUG`` log; never shown as is."""


def download_url(version: Version, tag: str | None = None) -> str | None:
    """Return the installer URL for a stable ``version`` on this platform, or ``None``.

    ``tag`` is the platform's installer suffix, as for
    [`tether.gui.update_verify.installer_name`][]. ``None`` when the version is not a stable
    ``MAJOR.MINOR.PATCH`` or no installer is published for the platform.
    """
    triple = channel_version(version)
    name = installer_name(version, tag)
    if triple is None or name is None:
        return None
    return f"{DOWNLOAD_BASE}v{triple[0]}.{triple[1]}.{triple[2]}/{name}"


def _allowed(url: str) -> bool:
    target = urllib.parse.urlsplit(url)
    return target.scheme == "https" and target.hostname in _ALLOWED_HOSTS and not target.port


class _ReleaseHostsOnly(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to ``github.com`` or [`ASSET_HOST`][], over HTTPS."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        if not _allowed(newurl):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _default_opener() -> urllib.request.OpenerDirector:
    # build_opener keeps the default ProxyHandler and the default HTTPS handler
    # (certificate and host-name verification on).
    return urllib.request.build_opener(_ReleaseHostsOnly())


def _private_folder(workroot: Path | None) -> Path:
    folder = Path(tempfile.mkdtemp(prefix="tether-download-", dir=workroot))
    if os.name == "posix":
        folder.chmod(0o700)
    return folder


class _Stop(Exception):
    def __init__(self, outcome: DownloadOutcome, reason: str) -> None:
        super().__init__(reason)
        self.outcome = outcome


def download_installer(
    version: Version,
    *,
    tag: str | None = None,
    progress: Progress | None = None,
    cancelled: Callable[[], bool] | None = None,
    timeout: float = DOWNLOAD_TIMEOUT_S,
    workroot: Path | None = None,
    opener: urllib.request.OpenerDirector | None = None,
) -> DownloadResult:
    """Download this platform's installer for ``version`` into a new private folder.

    Never raises for a network or data failure. ``tag`` defaults to this platform's installer
    suffix; ``workroot`` is the parent of the private folder (the system's temporary folder by
    default). ``progress`` and ``cancelled`` are called on the calling thread between reads.
    ``opener`` is for tests. Blocking: run it on a background thread.
    """
    url = download_url(version, tag)
    if url is None:
        return DownloadResult(DownloadOutcome.FAILED, reason="no installer for this platform")
    name = url.rsplit("/", 1)[1]
    folder = _private_folder(workroot)
    try:
        path = _download(url, folder / name, progress, cancelled, timeout, opener)
    except _Stop as stop:
        shutil.rmtree(folder, ignore_errors=True)
        return DownloadResult(stop.outcome, reason=str(stop))
    except (OSError, ValueError, http.client.HTTPException) as error:
        # OSError covers URLError, HTTPError (404, a refused redirect), timeouts, refused
        # connections and a full disk.
        shutil.rmtree(folder, ignore_errors=True)
        return DownloadResult(DownloadOutcome.FAILED, reason=type(error).__name__)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return DownloadResult(DownloadOutcome.DOWNLOADED, path=path)


def _download(
    url: str,
    target: Path,
    progress: Progress | None,
    cancelled: Callable[[], bool] | None,
    timeout: float,
    opener: urllib.request.OpenerDirector | None,
) -> Path:
    def stop_if_cancelled() -> None:
        if cancelled is not None and cancelled():
            raise _Stop(DownloadOutcome.CANCELLED, "cancelled")

    stop_if_cancelled()
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/octet-stream", "User-Agent": _USER_AGENT},
        method="GET",
    )
    part = target.with_name(target.name + ".part")
    with (opener or _default_opener()).open(request, timeout=timeout) as response:
        if getattr(response, "status", None) != 200:
            raise _Stop(DownloadOutcome.FAILED, f"HTTP {getattr(response, 'status', None)}")
        final = response.geturl() if hasattr(response, "geturl") else url
        if not _allowed(str(final)):
            raise _Stop(DownloadOutcome.FAILED, "the answer came from another host")
        total = _announced_length(response)
        if total is not None and total > MAX_INSTALLER_BYTES:
            raise _Stop(DownloadOutcome.FAILED, "the installer is too large")
        received = 0
        if progress is not None:
            progress(0, total)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        with os.fdopen(os.open(part, flags, 0o600), "wb") as handle:
            while True:
                stop_if_cancelled()
                chunk = response.read(_CHUNK)
                if not chunk:
                    break
                received += len(chunk)
                if received > MAX_INSTALLER_BYTES or (total is not None and received > total):
                    raise _Stop(DownloadOutcome.FAILED, "more bytes than announced")
                handle.write(chunk)
                if progress is not None:
                    progress(received, total)
        if total is not None and received != total:
            raise _Stop(DownloadOutcome.FAILED, "fewer bytes than announced")
        if received == 0:
            raise _Stop(DownloadOutcome.FAILED, "an empty answer")
    stop_if_cancelled()
    os.replace(part, target)
    return target


def _announced_length(response: object) -> int | None:
    headers = getattr(response, "headers", None)
    value = headers.get("Content-Length") if headers is not None else None
    if value is None:
        return None
    try:
        length = int(str(value).strip())
    except ValueError:
        raise _Stop(DownloadOutcome.FAILED, "a malformed length") from None
    if length < 0:
        raise _Stop(DownloadOutcome.FAILED, "a malformed length")
    return length

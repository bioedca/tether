# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The installer download of the in-app update (#1379), stdlib-only and with no network.

What these pin:

* the one URL: the fixed release-download prefix, the stable tag and this platform's installer
  name, re-spelled from the parsed version; a non-stable version or a platform with no
  installer gets no URL and no request;
* a redirect is followed only to ``github.com`` or GitHub's release-asset host, over HTTPS on
  the default port, and an answer that arrives from anywhere else is refused;
* the file lands under its installer name, in a fresh folder only this user can read, and a
  failed, short, oversized or cancelled download leaves no folder behind;
* the request carries ``Accept: application/octet-stream`` and ``User-Agent: Tether`` only.

A recording opener stands in for the network, so nothing here connects anywhere.
"""

from __future__ import annotations

import io
import os
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path

import pytest

from tether.gui import update_download
from tether.gui.update_check import parse_version
from tether.gui.update_download import (
    ASSET_HOST,
    DOWNLOAD_BASE,
    DownloadOutcome,
    download_installer,
    download_url,
)

TAG = "Linux-x86_64.sh"
NAME = f"Tether-1.2.3-{TAG}"
URL = f"https://github.com/bioedca/tether/releases/download/v1.2.3/{NAME}"
ASSET_URL = f"https://{ASSET_HOST}/github-production-release-asset/1/abc?sig=x"


def _v(text: str):
    version = parse_version(text)
    assert version is not None
    return version


class _Response(io.BytesIO):
    def __init__(self, body: bytes, *, status: int = 200, url: str = ASSET_URL, length=True):
        super().__init__(body)
        self.status = status
        self._url = url
        self.headers = Message()
        if length is True:
            self.headers["Content-Length"] = str(len(body))
        elif length is not None:
            self.headers["Content-Length"] = str(length)

    def geturl(self) -> str:
        return self._url


class _Opener:
    """Records each request and answers with a canned response (or raises)."""

    def __init__(self, answer: object) -> None:
        self.answer = answer
        self.requests: list[tuple[urllib.request.Request, float]] = []

    def open(self, request: urllib.request.Request, timeout: float):
        self.requests.append((request, timeout))
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


def _run(tmp_path: Path, answer: object, **kwargs):
    opener = _Opener(answer)
    result = download_installer(_v("1.2.3"), tag=TAG, workroot=tmp_path, opener=opener, **kwargs)  # type: ignore[arg-type]
    return result, opener


def _left(tmp_path: Path) -> list[str]:
    return sorted(p.name for p in tmp_path.iterdir())


# --- the URL ---------------------------------------------------------------------------


def test_the_url_is_the_fixed_prefix_the_tag_and_the_installer_name() -> None:
    assert download_url(_v("v1.2.3"), TAG) == URL
    assert download_url(_v("1.2"), TAG) == URL.replace("1.2.3", "1.2.0")
    assert URL.startswith(DOWNLOAD_BASE)


@pytest.mark.parametrize("text", ["1.2.3rc1", "1.2.3.post1", "1.2.4.dev2", "1.2.3.4"])
def test_a_version_that_is_not_stable_has_no_url_and_makes_no_request(
    tmp_path: Path, text: str
) -> None:
    assert download_url(_v(text), TAG) is None
    opener = _Opener(AssertionError("no request may be made"))
    result = download_installer(_v(text), tag=TAG, workroot=tmp_path, opener=opener)  # type: ignore[arg-type]
    assert result.outcome is DownloadOutcome.FAILED
    assert opener.requests == []
    assert _left(tmp_path) == []


def test_a_platform_with_no_installer_has_no_url(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(update_download, "installer_name", lambda version, tag=None: None)
    opener = _Opener(AssertionError("no request may be made"))
    result = download_installer(_v("1.2.3"), workroot=tmp_path, opener=opener)  # type: ignore[arg-type]
    assert result.outcome is DownloadOutcome.FAILED
    assert opener.requests == []


# --- the request -----------------------------------------------------------------------


def test_a_download_lands_under_its_installer_name_in_a_private_folder(tmp_path: Path) -> None:
    body = b"#!/bin/sh\n" + b"x" * (3 * 1024 * 1024 + 7)
    seen: list[tuple[int, int | None]] = []
    result, opener = _run(tmp_path, _Response(body), progress=lambda r, t: seen.append((r, t)))
    assert result.outcome is DownloadOutcome.DOWNLOADED
    assert result.path is not None and result.path.name == NAME
    assert result.path.read_bytes() == body
    assert sorted(p.name for p in result.path.parent.iterdir()) == [NAME]  # no .part left
    assert result.path.parent.parent == tmp_path
    if os.name == "posix":
        assert stat.S_IMODE(result.path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(result.path.stat().st_mode) == 0o600
    assert seen[0] == (0, len(body)) and seen[-1] == (len(body), len(body))

    [(request, timeout)] = opener.requests
    assert request.full_url == URL
    assert request.get_method() == "GET"
    assert sorted(request.header_items()) == [
        ("Accept", "application/octet-stream"),
        ("User-agent", "Tether"),
    ]
    assert timeout == update_download.DOWNLOAD_TIMEOUT_S


def test_an_answer_with_no_announced_length_still_downloads(tmp_path: Path) -> None:
    seen: list[tuple[int, int | None]] = []
    result, _ = _run(
        tmp_path, _Response(b"abc", length=None), progress=lambda r, t: seen.append((r, t))
    )
    assert result.outcome is DownloadOutcome.DOWNLOADED
    assert seen[-1] == (3, None)


@pytest.mark.parametrize(
    ("answer", "why"),
    [
        (_Response(b"abc", length=10), "short"),
        (_Response(b"abcdef", length=3), "long"),
        (_Response(b"", length=0), "empty"),
        (_Response(b"abc", length="x"), "malformed length"),
        (_Response(b"abc", status=206), "not 200"),
        (_Response(b"abc", url="https://example.invalid/file"), "another host"),
        (urllib.error.URLError("offline"), "offline"),
        (urllib.error.HTTPError(URL, 404, "Not Found", Message(), None), "404"),
        (TimeoutError("stalled"), "timeout"),
    ],
)
def test_a_download_that_does_not_finish_leaves_nothing(
    tmp_path: Path, answer: object, why: str
) -> None:
    result, _ = _run(tmp_path, answer)
    assert result.outcome is DownloadOutcome.FAILED, why
    assert result.path is None
    assert _left(tmp_path) == []


def test_an_announced_size_over_the_limit_is_refused_before_reading(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(update_download, "MAX_INSTALLER_BYTES", 5)
    response = _Response(b"abcdefgh")
    reads: list[int] = []
    real_read = response.read
    response.read = lambda n=-1: reads.append(n) or real_read(n)  # type: ignore[method-assign]
    result, _ = _run(tmp_path, response)
    assert result.outcome is DownloadOutcome.FAILED
    assert reads == []
    assert _left(tmp_path) == []


def test_a_body_over_the_limit_is_refused_while_reading(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(update_download, "MAX_INSTALLER_BYTES", 5)
    result, _ = _run(tmp_path, _Response(b"abcdefgh", length=None))
    assert result.outcome is DownloadOutcome.FAILED
    assert _left(tmp_path) == []


def test_cancel_before_the_request_makes_none(tmp_path: Path) -> None:
    result, opener = _run(tmp_path, _Response(b"abc"), cancelled=lambda: True)
    assert result.outcome is DownloadOutcome.CANCELLED
    assert opener.requests == []
    assert _left(tmp_path) == []


def test_cancel_during_the_download_removes_the_folder(tmp_path: Path) -> None:
    body = b"y" * (3 * 1024 * 1024)
    calls = {"n": 0}

    def cancelled() -> bool:
        calls["n"] += 1
        return calls["n"] > 2  # after the first chunk

    result, _ = _run(tmp_path, _Response(body), cancelled=cancelled)
    assert result.outcome is DownloadOutcome.CANCELLED
    assert _left(tmp_path) == []


# --- folders left behind --------------------------------------------------------------


def _aged(path: Path, seconds: float) -> Path:
    when = time.time() - seconds
    os.utime(path, (when, when))
    return path


def test_a_download_removes_this_users_old_download_folders_first(tmp_path: Path) -> None:
    day = update_download.STALE_AFTER_S
    old = tmp_path / "tether-download-old"
    (old / "sub").mkdir(parents=True)
    (old / NAME).write_bytes(b"left behind")
    _aged(old, day + 60)
    fresh = tmp_path / "tether-download-fresh"
    fresh.mkdir()
    other = tmp_path / "keep-me"
    other.mkdir()
    _aged(other, day + 60)
    plain = tmp_path / "tether-download-file"
    plain.write_bytes(b"x")
    _aged(plain, day + 60)
    result, _ = _run(tmp_path, _Response(b"abc"))
    assert result.outcome is DownloadOutcome.DOWNLOADED
    assert not old.exists()
    assert fresh.exists()
    assert other.exists() and plain.exists()


def _link(link: Path, target: Path) -> None:
    """A symbolic link on macOS and Linux; a junction on Windows, which needs no privilege."""
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def test_a_link_with_the_download_name_is_left_alone(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "keep").write_bytes(b"x")
    link = tmp_path / "tether-download-link"
    _link(link, target)
    monkeypatch.setattr(update_download, "STALE_AFTER_S", -3600.0)  # everything counts as old
    _run(tmp_path, _Response(b"abc"))
    assert os.path.lexists(link) and (target / "keep").exists()


def test_a_failed_sweep_does_not_stop_the_download(tmp_path: Path, monkeypatch) -> None:
    def refuse(_path):
        raise PermissionError("no listing")

    monkeypatch.setattr(update_download.os, "scandir", refuse)
    result, _ = _run(tmp_path, _Response(b"abc"))
    assert result.outcome is DownloadOutcome.DOWNLOADED


# --- the redirect ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("target", "followed"),
    [
        (ASSET_URL, True),
        ("https://github.com/bioedca/tether/releases/download/v1.2.3/x", True),
        (f"http://{ASSET_HOST}/x", False),
        (f"https://{ASSET_HOST}:8443/x", False),
        ("https://objects.githubusercontent.com/x", False),
        ("https://release-assets.githubusercontent.com.evil.example/x", False),
        ("https://example.invalid/x", False),
        ("file:///etc/passwd", False),
    ],
)
def test_a_redirect_is_followed_only_to_the_release_hosts(target: str, followed: bool) -> None:
    handler = update_download._ReleaseHostsOnly()
    request = urllib.request.Request(URL, headers={"User-Agent": "Tether"})
    new = handler.redirect_request(request, None, 302, "Found", Message(), target)
    assert (new is not None) is followed
    if followed:
        assert new.full_url == target


def test_the_default_opener_uses_the_release_hosts_redirect() -> None:
    opener = update_download._default_opener()
    assert any(isinstance(h, update_download._ReleaseHostsOnly) for h in opener.handlers)
    assert not any(
        type(h) is urllib.request.HTTPRedirectHandler for h in opener.handlers
    )  # the stock handler, which follows any host, is replaced


def test_importing_the_download_costs_no_qt_and_makes_no_request() -> None:
    code = (
        "import socket, sys\n"
        "def refuse(*a, **k):\n"
        "    raise SystemExit('a connection was attempted at import')\n"
        "socket.socket.connect = refuse\n"
        "import tether.gui.update_download\n"
        "heavy = sorted({m.split('.')[0] for m in sys.modules} & "
        "{'PySide6', 'PyQt5', 'pyqtgraph', 'numpy', 'h5py'})\n"
        "print(heavy)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=120
    ).stdout.strip()
    assert out == "[]"

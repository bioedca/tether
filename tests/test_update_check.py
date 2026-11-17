# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The network side of the opt-in update check (#248): one GET, one field, every failure silent.

No Qt here. The requests these tests make never leave the machine: a canned HTTPS handler
answers in place of GitHub, and the proxy tests point ``HTTPS_PROXY`` at a socket on
127.0.0.1 that this test owns.
"""

from __future__ import annotations

import contextlib
import email.message
import io
import json
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import urllib.response
from pathlib import Path

import pytest

from tether.gui import update_check
from tether.gui.update_check import (
    DEFAULT_TIMEOUT_S,
    LATEST_RELEASE_API,
    RELEASES_PAGE,
    check_for_update,
    fetch_latest_release,
    newer_release,
    parse_version,
)

_ROOT = Path(__file__).resolve().parents[1]
_PRD = _ROOT / "docs" / "PRD.md"

# The project's own published tags, oldest first (git tag -l on 2026-11-17), then the
# first stable release they lead to.
_TAG_HISTORY = [
    "v0.0.0",
    "v0.1.0",
    "v0.2.0",
    "v0.3.0",
    "v0.4.0",
    "v0.5.0",
    "v0.6.0",
    "v0.7.0",
    "v0.8.0",
    "v1.0.0-rc1",
    "v1.0.0-rc2",
    "v1.0.0-rc3",
    "v1.0.0",
]


def _release(tag: object, **extra: object) -> bytes:
    return json.dumps({"tag_name": tag, **extra}).encode("utf-8")


# --- version comparison ------------------------------------------------------------


def test_the_tag_history_is_in_strictly_increasing_order() -> None:
    versions = [parse_version(tag) for tag in _TAG_HISTORY]
    assert None not in versions
    assert all(a < b for a, b in zip(versions, versions[1:], strict=False))


@pytest.mark.parametrize(
    ("older", "newer"),
    [
        ("v0.8.0", "v1.0.0-rc1"),
        ("v1.0.0-rc1", "v1.0.0"),
        ("v0.8.0", "v1.0.0"),
        # A string compare gets these wrong: "0.10.0" < "0.9.0" and "1.0.0" < "1.0.0-rc1".
        ("v0.9.0", "v0.10.0"),
        ("v1.0.0-rc9", "v1.0.0-rc10"),
        # The installed version is hatch-vcs's PEP 440 spelling, not a tag.
        ("1.0.0rc4.dev49+g46fb76b9c", "v1.0.0"),
        ("1.0.0rc3", "1.0.0rc4.dev49+g46fb76b9c"),
        ("v1.0.0", "1.0.1.dev3+gabc1234"),
        ("1.0.1.dev3+gabc1234", "v1.0.1"),
        ("1.0.0.dev1", "v1.0.0-rc1"),
        ("v1.0.0", "1.0.0.post1"),
    ],
)
def test_versions_compare_as_versions_not_strings(older: str, newer: str) -> None:
    assert parse_version(older) < parse_version(newer)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("v1.0.0", "1.0.0"),
        ("v1.0.0-rc1", "1.0.0rc1"),
        ("v1.0.0-rc.1", "1.0.0rc1"),
        ("1.0", "1.0.0"),
        ("1.0.0+g46fb76b9c", "1.0.0"),
    ],
)
def test_spellings_of_one_version_are_equal(a: str, b: str) -> None:
    assert parse_version(a) == parse_version(b)


@pytest.mark.parametrize(
    "text",
    ["", "main", "refs/heads/main", "v", "v1.0.0-", "1.0.0 rc1", "x1.0.0", None, 1, ["v1.0.0"]],
)
def test_what_is_not_a_version_parses_to_none(text: object) -> None:
    assert parse_version(text) is None


def test_the_canonical_spelling_is_what_a_notice_shows() -> None:
    assert str(parse_version("v1.0.0")) == "1.0.0"
    assert str(parse_version("v1.0.0-rc.2")) == "1.0.0rc2"
    assert str(parse_version("1.0.1.dev3+gabc")) == "1.0.1.dev3"


# --- reading the answer ------------------------------------------------------------


def test_a_newer_release_is_returned() -> None:
    assert newer_release("1.0.0", _release("v1.0.1")) == parse_version("1.0.1")


@pytest.mark.parametrize("tag", ["v1.0.0", "v0.8.0", "v1.0.0-rc3"])
def test_an_equal_or_older_release_is_not(tag: str) -> None:
    assert newer_release("1.0.0", _release(tag)) is None


def test_the_prerelease_flag_changes_nothing() -> None:
    # Not a prerelease filter (the issue forbids one): the flag is never read, so it
    # cannot turn a notice off, and it cannot turn one on.
    assert newer_release("1.0.0", _release("v1.0.1", prerelease=True)) == parse_version("1.0.1")
    assert newer_release("1.0.0", _release("v1.0.0", prerelease=False)) is None


def test_no_url_from_the_answer_is_used() -> None:
    # The notice links to the fixed page whatever the answer carries.
    assert RELEASES_PAGE == "https://github.com/bioedca/tether/releases"
    payload = _release("v9.0.0", html_url="https://example.invalid/fake", assets=[])
    assert newer_release("1.0.0", payload) == parse_version("9.0.0")
    assert "html_url" not in Path(update_check.__file__).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "payload",
    [
        None,
        b"",
        b"not json",
        b"\xff\xfe",
        b"[]",
        b'"v9.0.0"',
        b"{}",
        _release(None),
        _release(9),
        _release("main"),
        _release("v" + "9" * 100),
        # Explicit ids: a node id built from these bytes would exceed Windows' 32767-
        # character limit on the PYTEST_CURRENT_TEST environment variable.
        pytest.param(b"[" * 100_000 + b"]" * 100_000, id="nested-too-deep"),
        pytest.param(b" " * (600 * 1024) + _release("v9.0.0"), id="oversized"),
    ],
)
def test_a_malformed_answer_is_silent(payload: bytes | None) -> None:
    assert newer_release("1.0.0", payload) is None


def test_an_installed_version_that_does_not_parse_shows_nothing() -> None:
    assert newer_release("not a version", _release("v9.0.0")) is None


def test_check_for_update_defaults_to_the_installed_version() -> None:
    from tether import __version__

    assert check_for_update(fetch=lambda: _release(__version__)) is None
    assert check_for_update(fetch=lambda: _release("v999.0.0")) == parse_version("999.0.0")


# --- the request itself -------------------------------------------------------------


class _Canned(urllib.request.HTTPSHandler):
    """Answers HTTPS requests from a table instead of the network, recording each one."""

    def __init__(self, answers: dict[str, tuple[int, dict[str, str], bytes]]) -> None:
        super().__init__()
        self.answers = answers
        self.requests: list[urllib.request.Request] = []

    def https_open(self, req: urllib.request.Request):  # noqa: ANN201
        self.requests.append(req)
        code, headers, body = self.answers[req.full_url]
        message = email.message.Message()
        for key, value in headers.items():
            message[key] = value
        response = urllib.response.addinfourl(io.BytesIO(body), message, req.full_url, code)
        response.msg = "canned"
        return response


def _opener(answers: dict[str, tuple[int, dict[str, str], bytes]]) -> tuple:
    handler = _Canned(answers)
    return urllib.request.build_opener(handler, update_check._ApiOnlyRedirect()), handler


def test_the_request_is_one_get_with_no_identifier() -> None:
    opener, handler = _opener({LATEST_RELEASE_API: (200, {}, _release("v1.0.1"))})
    assert fetch_latest_release(opener=opener) == _release("v1.0.1")
    assert len(handler.requests) == 1
    (request,) = handler.requests
    assert request.get_method() == "GET"
    assert request.full_url == "https://api.github.com/repos/bioedca/tether/releases/latest"
    assert request.data is None
    # Host is urllib's own, from the URL; nothing else is added.
    assert dict(request.header_items()) == {
        "Host": "api.github.com",
        "Accept": "application/vnd.github+json",
        "User-agent": "Tether",
    }


@pytest.mark.parametrize(
    ("code", "headers"),
    [
        (403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1900000000"}),
        (429, {"X-RateLimit-Remaining": "0"}),
        (404, {}),  # no full release exists yet: the state of the repository today
        (500, {}),
        (204, {}),
    ],
)
def test_an_error_answer_is_silent(code: int, headers: dict[str, str]) -> None:
    opener, _ = _opener({LATEST_RELEASE_API: (code, headers, b'{"message": "no"}')})
    assert fetch_latest_release(opener=opener) is None


def test_a_redirect_to_another_host_is_refused() -> None:
    opener, handler = _opener(
        {
            LATEST_RELEASE_API: (302, {"Location": "https://example.invalid/latest"}, b""),
            "https://example.invalid/latest": (200, {}, _release("v9.0.0")),
        }
    )
    assert fetch_latest_release(opener=opener) is None
    assert [r.full_url for r in handler.requests] == [LATEST_RELEASE_API]


def test_a_redirect_within_the_api_host_is_followed() -> None:
    moved = "https://api.github.com/repositories/1/releases/latest"
    opener, _ = _opener(
        {
            LATEST_RELEASE_API: (301, {"Location": moved}, b""),
            moved: (200, {}, _release("v1.0.1")),
        }
    )
    assert fetch_latest_release(opener=opener) == _release("v1.0.1")


def test_an_oversized_body_is_not_read_whole() -> None:
    opener, _ = _opener({LATEST_RELEASE_API: (200, {}, b" " * (600 * 1024))})
    assert fetch_latest_release(opener=opener) is None


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.URLError("no route"),
        TimeoutError("timed out"),
        ConnectionRefusedError(),
        socket.gaierror("no name"),
        ValueError("bad"),
    ],
)
def test_a_network_failure_is_silent(error: Exception) -> None:
    class _Failing:
        def open(self, *_args: object, **_kwargs: object) -> None:
            raise error

    assert fetch_latest_release(opener=_Failing()) is None


class _SilentProxy:
    """A proxy on 127.0.0.1 that reads the client's first line and then never answers."""

    def __init__(self) -> None:
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.first_line = b""
        self.done = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(5)
            with contextlib.suppress(OSError):
                self.first_line = conn.recv(4096).split(b"\r\n", 1)[0]
            self.done.wait(10)

    def close(self) -> None:
        self.done.set()
        self.sock.close()


def test_the_proxy_variable_is_honoured_and_a_timeout_is_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxy = _SilentProxy()
    try:
        for name in ("HTTPS_PROXY", "https_proxy"):
            monkeypatch.setenv(name, f"http://127.0.0.1:{proxy.port}")
        for name in ("NO_PROXY", "no_proxy"):
            monkeypatch.delenv(name, raising=False)
        assert fetch_latest_release(timeout=0.5) is None
        # The request went to the proxy named in the environment, as a tunnel to the
        # one host the check may reach.
        assert proxy.first_line == b"CONNECT api.github.com:443 HTTP/1.1"
    finally:
        proxy.close()


def test_the_default_timeout_is_short_and_registered_in_the_prd() -> None:
    assert 0 < DEFAULT_TIMEOUT_S <= 10
    section = _PRD.read_text(encoding="utf-8").split("### 11.2", 1)[1].split("### 11.3", 1)[0]
    rows = [line for line in section.splitlines() if "DEFAULT_TIMEOUT_S" in line]
    assert rows, "PRD §11.2 has no update-check row"
    assert f"**{DEFAULT_TIMEOUT_S:g} s**" in rows[0]


def test_importing_the_check_costs_no_qt() -> None:
    code = (
        "import sys\n"
        "import tether.gui.update_check\n"
        "heavy = sorted({m.split('.')[0] for m in sys.modules} & "
        "{'PySide6', 'PyQt5', 'pyqtgraph', 'numpy', 'h5py'})\n"
        "print(heavy)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=120
    ).stdout.strip()
    assert out == "[]"

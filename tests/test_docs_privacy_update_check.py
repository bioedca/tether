# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The privacy page names every request the application makes, and every value it sends (#618).

``docs/privacy.md`` is the project's public promise about the network. Its section
*The desktop application* says that the opt-in check for a newer release is the only request
the application makes, and lists the URL and each header of that request. These tests hold
the page to the code:

* the URL that [`tether.gui.update_check.fetch_latest_release`][] requests, and each header
  of the request it builds, appear on the page as code, and so do the two headers that the
  standard library adds to every request it sends (measured on CPython 3.12 with a local
  server on 2026-11-25: ``Accept-Encoding: identity``, ``Connection: close``, plus ``Host``);
* the update verifier (#333, [`tether.gui.update_verify.verify_update`][]) reaches more hosts:
  the attestations endpoint and the two Sigstore trust-root services. The page names them
  only once something in the application calls the verifier. While nothing does, the page
  says the bundled ``gh`` never runs; the moment a module calls it, the last test here fails
  until the page names every host the verifier contacts (#1379 was first);
* the download of **Install update** (#1379, [`tether.gui.update_download`][]): the page names
  its URL prefix, the release-asset host it may be redirected to, and each header of the
  request it builds.

Stdlib only; no Qt and no network.
"""

from __future__ import annotations

import ast
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from tether.gui import update_check, update_download, update_verify

_REPO = Path(__file__).resolve().parents[1]
PRIVACY = _REPO / "docs" / "privacy.md"
SRC = _REPO / "src" / "tether"

#: Headers ``http.client`` adds to every request ``urllib`` sends, besides ``Host``.
STDLIB_HEADERS = ("Accept-Encoding: identity", "Connection: close")

#: The hosts ``gh attestation trusted-root`` reaches (ADR-0060; PRD §7.12 names both).
TRUST_ROOT_HOSTS = ("tuf-repo-cdn.sigstore.dev", "tuf-repo.github.com")

#: The page's sentence while nothing calls the verifier.
VERIFIER_NEVER_RUNS = "This release of Tether never runs it"


def _app_section() -> str:
    text = PRIVACY.read_text(encoding="utf-8")
    _, found, rest = text.partition("\n## The desktop application\n")
    assert found, "docs/privacy.md has no section 'The desktop application'"
    return " ".join(rest.split("\n## ", 1)[0].split())


class _Recorder:
    """An opener that records the request it is given and fails it, as an offline one would."""

    def __init__(self) -> None:
        self.request: urllib.request.Request | None = None

    def open(self, request: urllib.request.Request, timeout: float) -> object:
        self.request = request
        raise OSError("offline")


def _check_request() -> urllib.request.Request:
    recorder = _Recorder()
    assert update_check.fetch_latest_release(opener=recorder) is None  # type: ignore[arg-type]
    assert recorder.request is not None
    return recorder.request


def test_the_page_names_the_url_of_the_update_check() -> None:
    request = _check_request()
    section = _app_section()
    assert request.get_method() == "GET"
    assert request.full_url == update_check.LATEST_RELEASE_API
    assert f"`GET {request.full_url}`" in section
    assert "This is the only request the check makes." in section


def test_the_page_names_every_header_the_update_check_sends() -> None:
    request = _check_request()
    section = _app_section()
    # urllib stores header names capitalised ("User-agent"); the page uses the usual spelling.
    spelled = {name.lower(): name for name in ("Accept", "User-Agent")}
    sent = [f"{spelled[name.lower()]}: {value}" for name, value in request.header_items()]
    assert sorted(sent) == ["Accept: application/vnd.github+json", "User-Agent: Tether"]
    host = f"Host: {urlsplit(request.full_url).hostname}"
    for header in (*sent, host, *STDLIB_HEADERS):
        assert f"`{header}`" in section, header
    assert "these five headers and nothing more" in section


def test_the_page_says_the_check_is_off_until_the_user_turns_it_on() -> None:
    section = _app_section()
    assert "it is off until you turn it on" in section
    assert "Until you answer yes, Tether makes no network request at all." in section
    assert "downloads nothing and installs nothing** until you select **Install update**" in section


def _calls_the_verifier(source: str) -> bool:
    """Whether ``source`` refers to ``verify_update`` in code: a name, an attribute or an import.

    A mention in a docstring or a comment does not count; ``tether.gui.update_handoff``
    names the function in prose, and prose sends nothing.
    """
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name) and node.id == "verify_update":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "verify_update":
            return True
        if isinstance(node, ast.ImportFrom) and any(a.name == "verify_update" for a in node.names):
            return True
    return False


def test_the_scan_tells_a_call_from_a_mention() -> None:
    assert _calls_the_verifier("from tether.gui.update_verify import verify_update\n")
    assert _calls_the_verifier("import tether.gui.update_verify as v\nv.verify_update(1)\n")
    assert not _calls_the_verifier('"""Only [`verify_update`][] makes one."""\n# verify_update\n')
    assert not _calls_the_verifier("from tether.gui.update_verify import VerifiedInstaller\n")


def _modules_that_call_the_verifier() -> list[str]:
    own = Path(update_verify.__file__).resolve()
    return sorted(
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if path.resolve() != own and _calls_the_verifier(path.read_text(encoding="utf-8"))
    )


def test_the_page_names_the_verifier_hosts_once_anything_calls_the_verifier() -> None:
    section = _app_section()
    callers = _modules_that_call_the_verifier()
    if not callers:
        assert VERIFIER_NEVER_RUNS in section
        return
    attestations = update_verify.ATTESTATIONS_API.split("{digest}")[0]
    missing = [host for host in (attestations, *TRUST_ROOT_HOSTS) if host not in section]
    assert not missing, (
        f"{', '.join(callers)} call the update verifier; docs/privacy.md must name "
        f"{', '.join(missing)} and drop the sentence {VERIFIER_NEVER_RUNS!r}"
    )
    assert VERIFIER_NEVER_RUNS not in section


def test_the_page_names_the_download_and_every_header_it_sends(tmp_path: Path) -> None:
    from tether.gui.update_check import parse_version

    recorder = _Recorder()
    version = parse_version("1.0.1")
    result = update_download.download_installer(
        version, tag="Windows-x86_64.exe", workroot=tmp_path, opener=recorder
    )  # type: ignore[arg-type]
    assert result.outcome is update_download.DownloadOutcome.FAILED
    request = recorder.request
    assert request is not None and request.get_method() == "GET"
    section = _app_section()
    assert request.full_url.startswith(update_download.DOWNLOAD_BASE)
    assert f"`GET {update_download.DOWNLOAD_BASE}v<version>/<installer file>`" in section
    assert f"`{update_download.ASSET_HOST}`" in section
    spelled = {"accept": "Accept", "user-agent": "User-Agent"}
    sent = [f"{spelled[name.lower()]}: {value}" for name, value in request.header_items()]
    assert sorted(sent) == ["Accept: application/octet-stream", "User-Agent: Tether"]
    for header in (*sent, *STDLIB_HEADERS):
        assert f"`{header}`" in section, header
    assert "`Tether-1.0.1-Windows-x86_64.exe`" in section

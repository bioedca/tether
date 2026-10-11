# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Liveness guard for the PUBLISHED documentation site (issue #325).

``docs-build`` proves the source builds; nothing proved the site is still *published*. For an
undetermined window ending 2026-07-30 the Pages source had drifted to the legacy Jekyll builder
on ``main:/docs``: every versioned URL 404'd while the root served ``200`` from one unstyled
Jekyll page, and every check stayed green. ``.github/workflows/docs-liveness.yml`` runs this
module daily so that state goes red without a person going looking.

What is asserted, and where:

* **Pages configuration** -- ``source`` is ``gh-pages`` at ``/``. Nothing about ``build_type``
  (``legacy`` *is* "Deploy from a branch", which ``mike`` needs) or ``status`` (it reads
  ``building`` on every legitimate redeploy). A ``403`` is a failure naming the permission,
  never a skip.
* **``/``** -- ``mike``'s redirect stub: ``200``, points at ``latest/``, and is not Jekyll. The
  stub carries no generator, ``md-nav`` or ``md-search``, so none is required here.
* **``/latest/``** -- the Material site: a generator naming ``mkdocs-material`` (the name, never a
  version, so a ``requirements-docs.txt`` bump cannot turn it red), ``md-nav`` and ``md-search``.
* **``/latest/privacy/``** -- ``200`` with a body: the externally published privacy policy, served
  *at that URL*. urllib follows redirects, so a ``200`` alone could be some other page's (#518);
  the final URL must still be the policy's -- see :func:`check_privacy`.
* **``/versions.json``** -- valid JSON with an entry resolving ``latest``; no version number.

Flap policy: each fetch is retried in-run (:data:`MAX_ATTEMPTS`, with backoff) on transport
errors, timeouts, 5xx and 404; a content mismatch is deterministic and is not retried. A fetch
that never connected is reported as a fetch failure, never as a dead site. Each fetch is bounded
*as a whole* -- connect, redirects, headers and body together -- by :data:`TIMEOUT_SECONDS`, and
its body by :data:`MAX_BODY_BYTES`, so the retry budget the workflow states is a real upper bound.

The job token goes to the Pages API and to no other origin. urllib's stock redirect handler
copies every request header onto the redirected request, so the opener here drops
``Authorization`` from any redirect that leaves the origin of the request carrying it.

The fetching is behind an injected transport so every assertion is unit-tested offline
(``tests/test_docs_site_guard.py``). The job runs on the bare runner interpreter, so this module
imports the standard library only.
"""

from __future__ import annotations

import http.client
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import IO, Any

ROOT_URL = "https://bioedca.github.io/tether/"
LATEST_URL = "https://bioedca.github.io/tether/latest/"
PRIVACY_URL = "https://bioedca.github.io/tether/latest/privacy/"
VERSIONS_URL = "https://bioedca.github.io/tether/versions.json"
SITE_URLS = (ROOT_URL, LATEST_URL, PRIVACY_URL, VERSIONS_URL)

PAGES_ENDPOINT = "repos/bioedca/tether/pages"
PAGES_API_URL = "https://api.github.com/" + PAGES_ENDPOINT

#: Attempts per fetch, the first included. In-run only: no cross-run state.
MAX_ATTEMPTS = 3
#: Seconds before the second attempt; doubled before each later one.
BACKOFF_SECONDS = 10.0
#: The whole-fetch deadline: connect, TLS, redirects, headers and body together. It is also the
#: socket timeout, but that alone bounds only each blocking operation -- a server trickling bytes
#: inside it, or a DNS lookup it does not cover at all, would hold a fetch past any budget.
TIMEOUT_SECONDS = 30.0
#: A body longer than this is not read further, and the fetch is a transport failure.
MAX_BODY_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class Response:
    """One fetch result. ``status`` is ``None`` when no HTTP response was received at all."""

    status: int | None
    body: bytes = b""
    error: str = ""
    #: Response headers, names lower-cased.
    headers: Mapping[str, str] = field(default_factory=dict)
    #: The URL the response actually came from, after every redirect urllib followed. Empty when
    #: no HTTP response was received, or when an injected transport does not report one.
    final_url: str = ""

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


Transport = Callable[[str, Mapping[str, str]], Response]


_DEFAULT_PORTS = {"http": 80, "https": 443}


def _same_origin(first: str, second: str) -> bool:
    """Whether two absolute URLs share scheme, host and (defaulted) port. Unparseable is not."""
    try:
        origins = []
        for url in (first, second):
            parts = urllib.parse.urlsplit(url)
            scheme = parts.scheme.lower()
            port = parts.port or _DEFAULT_PORTS.get(scheme)
            origins.append((scheme, (parts.hostname or "").lower(), port))
    except ValueError:
        return False
    return origins[0] == origins[1]


class _OriginBoundRedirects(urllib.request.HTTPRedirectHandler):
    """urllib's redirect handling, except that ``Authorization`` never leaves its origin.

    The stock handler copies every request header onto the redirected request, so a redirect
    answering the Pages API probe would hand the job token to wherever ``Location`` points. A
    same-origin redirect keeps it; a change of scheme, host or port drops it.
    """

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: http.client.HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and not _same_origin(req.full_url, new.full_url):
            new.remove_header("Authorization")
        return new


def _build_opener(*handlers: urllib.request.BaseHandler) -> urllib.request.OpenerDirector:
    """urllib's default opener with :class:`_OriginBoundRedirects` as its redirect handler."""
    return urllib.request.build_opener(_OriginBoundRedirects, *handlers)


#: The opener every real fetch goes through. Tests replace it so nothing here can dial out.
_OPENER = _build_opener()


def _read_bounded(status: int, answer: Any, headers: Mapping[str, str] | None) -> Response:
    """*answer*'s body, read no further than one byte past :data:`MAX_BODY_BYTES`.

    ``answer.url`` is the URL of the request that produced *answer*, which after a followed
    redirect is the redirect's target, not the URL asked for. An ``HTTPError`` carries it too.
    """
    body = answer.read(MAX_BODY_BYTES + 1) or b""
    if len(body) > MAX_BODY_BYTES:
        return Response(None, error=f"the body exceeded {MAX_BODY_BYTES} bytes; not read further")
    return Response(status, body, headers=_lower(headers), final_url=answer.url)


def _exchange(url: str, headers: Mapping[str, str]) -> Response:
    """One request through :data:`_OPENER`, every HTTP status included, as a :class:`Response`."""
    request = urllib.request.Request(url, headers=dict(headers))
    try:
        try:
            with _OPENER.open(request, timeout=TIMEOUT_SECONDS) as answer:
                return _read_bounded(answer.status, answer, answer.headers)
        except urllib.error.HTTPError as exc:
            return _read_bounded(exc.code, exc, exc.headers)
    # Every way the exchange itself can break -- including a body cut short while it is read
    # (http.client.IncompleteRead) or a malformed status line, which are HTTPException and not
    # OSError -- is a transport failure on the retry path, never an exception out of main().
    except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
        return Response(None, error=f"{type(exc).__name__}: {getattr(exc, 'reason', exc)}")


def default_transport(url: str, headers: Mapping[str, str]) -> Response:
    """The real network fetch, bounded as a whole by :data:`TIMEOUT_SECONDS`.

    The exchange runs on a daemon thread that is abandoned at the deadline, which is reported as
    a transport failure; anything else the exchange raises is re-raised here, on the caller's
    thread. An abandoned thread holds at most :data:`MAX_BODY_BYTES` and dies with the process.
    """
    outcome: list[Response | BaseException] = []

    def exchange() -> None:
        try:
            outcome.append(_exchange(url, headers))
        except BaseException as exc:  # handed to the caller below, never swallowed here
            outcome.append(exc)

    worker = threading.Thread(target=exchange, name="docs-liveness fetch", daemon=True)
    worker.start()
    worker.join(TIMEOUT_SECONDS)
    if not outcome:
        return Response(
            None,
            error=f"no complete response within the {TIMEOUT_SECONDS:g} s whole-fetch deadline",
        )
    if isinstance(outcome[0], BaseException):
        raise outcome[0]
    return outcome[0]


def _lower(headers: Mapping[str, str] | None) -> dict[str, str]:
    return {k.lower(): v for k, v in (headers or {}).items()}


def _rate_limited(response: Response) -> bool:
    """GitHub also answers 403 for a primary or secondary rate limit; that is not a permission."""
    return (
        response.headers.get("x-ratelimit-remaining") == "0"
        or "retry-after" in response.headers
        or "rate limit" in response.text.lower()
    )


def _retryable(response: Response) -> bool:
    status = response.status
    return status is None or status == 404 or status >= 500


def fetch(
    url: str,
    transport: Transport,
    sleep: Callable[[float], None],
    headers: Mapping[str, str] | None = None,
) -> tuple[Response, int]:
    """Fetch *url*, retrying the retryable outcomes. Returns the last response and attempts used."""
    attempt = 0
    while True:
        attempt += 1
        response = transport(url, headers or {"User-Agent": "tether-docs-liveness"})
        if not _retryable(response) or attempt >= MAX_ATTEMPTS:
            return response, attempt
        sleep(BACKOFF_SECONDS * 2 ** (attempt - 1))


def _status_problem(url: str, response: Response, attempts: int) -> str | None:
    """The problem for a non-200 outcome, keeping a fetch failure distinct from a dead site."""
    if response.status is None:
        return (
            f"could not fetch {url} on any of {attempts} attempt(s) ({response.error}) -- a "
            "transport failure, not evidence the site is down; re-run before treating it as one"
        )
    if response.status != 200:
        return f"{url} returned HTTP {response.status} (after {attempts} attempt(s))"
    return None


class _Page(HTMLParser):
    """The few facts the probes read from a page: generator metas, refreshes, hrefs, scripts."""

    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=True)
        self.generators: list[str] = []
        self.refreshes: list[str] = []
        self.hrefs: list[str] = []
        self.scripts: list[str] = []
        self._in_script = False
        self.feed(text)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        if tag == "meta" and values.get("name", "").lower() == "generator":
            self.generators.append(values.get("content", ""))
        elif tag == "meta" and values.get("http-equiv", "").lower() == "refresh":
            self.refreshes.append(values.get("content", ""))
        elif tag == "a" and "href" in values:
            self.hrefs.append(values["href"])
        self._in_script = tag == "script"
        if self._in_script:
            self.scripts.append("")

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_script = False

    def handle_data(self, data: str) -> None:
        if self._in_script:
            self.scripts[-1] += data


def check_pages(response: Response, attempts: int) -> list[str]:
    """Pages configuration: ``source`` only -- never ``build_type``, never ``status``."""
    if response.status == 403 and _rate_limited(response):
        return [
            f"the Pages API ({PAGES_ENDPOINT}) returned 403 for a rate limit -- a transient "
            "condition, not a permission problem and not evidence about the site; re-run later"
        ]
    if response.status == 403:
        return [
            f"the Pages API ({PAGES_ENDPOINT}) returned 403: the job token cannot read the Pages "
            "configuration -- the job must declare `permissions: pages: read`; if it already "
            "does, see #325's handoff (elevate to pages: write, or drop this probe)"
        ]
    problem = _status_problem(PAGES_API_URL, response, attempts)
    if problem:
        return [problem]
    try:
        payload = json.loads(response.body)
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        return [f"the Pages API ({PAGES_ENDPOINT}) returned a body that is not a JSON object"]
    source = payload.get("source")
    if (
        not isinstance(source, dict)
        or source.get("branch") != "gh-pages"
        or source.get("path") != "/"
    ):
        return [
            f"the Pages source is {source!r}, not gh-pages at / -- the docs site is no longer "
            "being served from the branch mike publishes (the #325 incident)"
        ]
    return []


def check_root(response: Response, attempts: int) -> list[str]:
    """``/`` is mike's redirect stub: it points at ``latest/`` and is not Jekyll."""
    problem = _status_problem(ROOT_URL, response, attempts)
    if problem:
        return [problem]
    page = _Page(response.text)
    problems = [
        f"{ROOT_URL} is generated by {g!r}: the legacy Jekyll builder is serving the site root "
        "(the #325 incident signature)"
        for g in page.generators
        if "jekyll" in g.lower()
    ]
    missing = []
    if not any("url=latest/" in r.replace(" ", "").lower() for r in page.refreshes):
        missing.append("a <noscript> refresh to url=latest/")
    if "latest/" not in page.hrefs:
        missing.append('an href="latest/" link')
    if not any("location.replace" in s and '"latest/"' in s for s in page.scripts):
        missing.append('a location.replace script naming "latest/"')
    if missing:
        problems.append(
            f"{ROOT_URL} is not mike's redirect to latest/ -- missing {', '.join(missing)}"
        )
    return problems


def check_latest(response: Response, attempts: int) -> list[str]:
    """``/latest/`` is the MkDocs Material site (matched by name, never by version)."""
    problem = _status_problem(LATEST_URL, response, attempts)
    if problem:
        return [problem]
    text = response.text
    problems = []
    if not any("mkdocs-material" in g for g in _Page(text).generators):
        problems.append(f"{LATEST_URL} carries no generator meta naming mkdocs-material")
    for marker in ("md-nav", "md-search"):
        if marker not in text:
            problems.append(f"{LATEST_URL} carries no {marker} -- not the Material site")
    return problems


#: Where a fetch of :data:`PRIVACY_URL` may end up and still be serving the policy: the URL itself,
#: or the ``index.html`` it is the directory form of -- the same file on Pages, so a redirect
#: between the two moves nothing (#518).
PRIVACY_FINAL_URLS = (PRIVACY_URL, PRIVACY_URL + "index.html")


def check_privacy(response: Response, attempts: int) -> list[str]:
    """``/latest/privacy/`` is externally published, so it must resolve -- there -- with a body.

    The fetch follows redirects, so a ``200`` with a body proves nothing on its own: a redirect to
    the site root would pass on the root's page (#518). The final URL must therefore be one of
    :data:`PRIVACY_FINAL_URLS`. An empty ``final_url`` -- a transport that does not report one --
    is read as the URL requested. No content marker is asserted: every candidate is either the
    wording of ``docs/privacy.md``, which an edit there would turn red, or Material/``mike``
    markup this module cannot pin offline; the final-URL check closes the redirect case alone.
    """
    problem = _status_problem(PRIVACY_URL, response, attempts)
    if problem:
        return [problem]
    final = response.final_url or PRIVACY_URL
    if final not in PRIVACY_FINAL_URLS:
        return [
            f"{PRIVACY_URL} redirected to {final} -- the published privacy-policy URL no longer "
            "serves the policy"
        ]
    if not response.body.strip():
        return [f"{PRIVACY_URL} returned 200 with an empty body"]
    return []


def _versions_shape_errors(entries: object) -> list[str]:
    """Every way *entries* departs from mike's ``versions.json`` shape, checked in one place.

    The shape is a list of objects, each with a string ``version`` and, where present, a string
    ``title`` and an ``aliases`` list of strings. It is validated as its own assertion, so a
    malformed entry is reported even when another entry (or the same one) resolves ``latest``.
    """
    if not isinstance(entries, list):
        return [f"top level is {type(entries).__name__}, not a list"]
    errors = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append(f"entry {index} is {type(entry).__name__}, not an object")
            continue
        if not isinstance(entry.get("version"), str):
            errors.append(f"entry {index} has no string `version`")
        if "title" in entry and not isinstance(entry["title"], str):
            errors.append(f"entry {index} has a non-string `title`")
        aliases = entry.get("aliases", [])
        if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
            errors.append(f"entry {index} has `aliases` that is not a list of strings")
    return errors


def check_versions(response: Response, attempts: int) -> list[str]:
    """``versions.json`` is well-formed and resolves ``latest``; no version number is required."""
    problem = _status_problem(VERSIONS_URL, response, attempts)
    if problem:
        return [problem]
    try:
        entries = json.loads(response.body)
    except ValueError:
        return [f"{VERSIONS_URL} is not valid JSON"]
    shape = _versions_shape_errors(entries)
    if shape:
        return [f"{VERSIONS_URL} is not mike's versions.json shape: {'; '.join(shape)}"]
    if not any(e["version"] == "latest" or "latest" in e.get("aliases", []) for e in entries):
        return [f"{VERSIONS_URL} has no entry resolving `latest` (no alias or version 'latest')"]
    return []


_SITE_CHECKS = (
    (ROOT_URL, check_root),
    (LATEST_URL, check_latest),
    (PRIVACY_URL, check_privacy),
    (VERSIONS_URL, check_versions),
)


def _escape(message: str) -> str:
    """Workflow-command data escaping, so one problem is always exactly one line."""
    return message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main(
    transport: Transport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Run every probe; one ``::error::`` per problem, a step-summary block, non-zero on any."""
    environ = os.environ if environ is None else environ
    transport = transport or default_transport
    api_headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "tether-docs-liveness",
    }
    if environ.get("GITHUB_TOKEN"):
        api_headers["Authorization"] = f"Bearer {environ['GITHUB_TOKEN']}"

    problems = check_pages(*fetch(PAGES_API_URL, transport, sleep, api_headers))
    for url, check in _SITE_CHECKS:
        problems += check(*fetch(url, transport, sleep))

    for problem in problems:
        print(f"::error::{_escape(problem)}")
    summary_path = environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write("## docs-liveness\n\n")
            if problems:
                summary.write(f"**{len(problems)} failed assertion(s)** on the published site:\n\n")
                summary.writelines(f"- {p}\n" for p in problems)
            else:
                summary.write("The Pages source and all four published URLs passed.\n")
            summary.write("\n")
    if problems:
        return 1
    print(f"docs site OK: Pages source gh-pages:/ and {', '.join(SITE_URLS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline unit tests for ``scripts/check_docs_site.py``, the published-docs liveness guard (#325).

Every probe runs against fixtures through an injected transport, and an autouse fixture makes
the module's real opener raise, so nothing here can reach ``bioedca.github.io`` and the required
3-OS matrix stays deterministic. The workflow that runs the module is contract-tested in
``tests/test_marker_contract.py``.

``fixtures/docs_site/gh_pages_index.html`` is the verbatim ``origin/gh-pages:index.html`` at
``e080cf6175a7b40cdc33f91d12bc28ffa4dbcc04`` -- mike's redirect stub, the same bytes the live
root served on 2026-08-13 -- pinned by SHA-256 below rather than retyped, because the real
``location.replace`` call is wrapped across lines and a hand-typed stub would hide that.
"""

from __future__ import annotations

import ast
import hashlib
import http.client
import importlib.util
import json
import sys
import urllib.request
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
SCRIPT = _REPO / "scripts" / "check_docs_site.py"
ROOT_STUB = (_REPO / "tests" / "fixtures" / "docs_site" / "gh_pages_index.html").read_bytes()
ROOT_STUB_SHA256 = "36a4bad7470e9a2ae9067a0d61bc99040f4d993c9319213812640ee0169136fd"

_spec = importlib.util.spec_from_file_location("check_docs_site", SCRIPT)
assert _spec is not None and _spec.loader is not None
guard = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = guard  # @dataclass resolves its annotations through sys.modules
_spec.loader.exec_module(guard)
R = guard.Response

#: `gh api repos/bioedca/tether/pages`, captured 2026-08-13 (issue #325).
PAGES_OK = {
    "url": "https://api.github.com/repos/bioedca/tether/pages",
    "status": "built",
    "cname": None,
    "custom_404": False,
    "html_url": "https://bioedca.github.io/tether/",
    "build_type": "legacy",
    "source": {"branch": "gh-pages", "path": "/"},
    "public": True,
    "protected_domain_state": None,
    "pending_domain_unverified_at": None,
    "https_enforced": True,
}
GENERATOR_TODAY = "mkdocs-1.6.1, mkdocs-material-9.7.7"
GENERATOR_BUMPED = "mkdocs-1.7.0, mkdocs-material-9.9.9"
VERSIONS_OK = b'[{"version":"dev","title":"dev","aliases":["latest"]}]'
JEKYLL_ROOT = (
    b'<!DOCTYPE html><html><head><meta name="generator" content="Jekyll v3.10.0">'
    b"<title>tether</title></head><body><h1>Tether</h1></body></html>"
)


def _latest(generator: str | None = GENERATOR_TODAY, nav: bool = True, search: bool = True) -> R:
    meta = f'<meta name="generator" content="{generator}">' if generator else ""
    nav_class, search_class = ("md-nav" if nav else "x"), ("md-search" if search else "y")
    body = f'<nav class="{nav_class}"></nav><div class="{search_class}">'
    return R(200, f"<html><head>{meta}</head><body>{body}</div></body></html>".encode())


def _healthy() -> dict[str, list[R]]:
    return {
        guard.PAGES_API_URL: [R(200, json.dumps(PAGES_OK).encode())],
        guard.ROOT_URL: [R(200, ROOT_STUB)],
        guard.LATEST_URL: [_latest()],
        guard.PRIVACY_URL: [R(200, b"<html><body>Privacy</body></html>")],
        guard.VERSIONS_URL: [R(200, VERSIONS_OK)],
    }


class FakeTransport:
    """Answers per URL from a scripted sequence (the last answer repeats) and records each call."""

    def __init__(self, answers: dict[str, list[R]]) -> None:
        self.answers = {url: list(seq) for url, seq in answers.items()}
        self.requested: list[str] = []

    def __call__(self, url: str, headers: object) -> R:
        self.requested.append(url)
        sequence = self.answers[url]
        return sequence.pop(0) if len(sequence) > 1 else sequence[0]


def _run(overrides: dict[str, list[R]] | None = None, **environ: str) -> tuple[int, FakeTransport]:
    transport = FakeTransport({**_healthy(), **(overrides or {})})
    return guard.main(transport=transport, sleep=lambda _s: None, environ=environ), transport


def _errors(capsys: pytest.CaptureFixture[str]) -> list[str]:
    return [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("::error::")]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the real opener fail the test, so a probe that loses its fake cannot dial out."""

    def _refuse(*_args: object, **_kwargs: object) -> None:
        pytest.fail("check_docs_site tried to reach the network; inject a transport")

    monkeypatch.setattr(guard.urllib.request, "urlopen", _refuse)


def test_the_default_transport_cannot_reach_the_network_under_test() -> None:
    assert guard.urllib.request is urllib.request
    with pytest.raises(pytest.fail.Exception, match="tried to reach the network"):
        guard.main(sleep=lambda _s: None, environ={})


def test_the_url_set_and_the_pages_endpoint_are_pinned() -> None:
    assert guard.SITE_URLS == (
        "https://bioedca.github.io/tether/",
        "https://bioedca.github.io/tether/latest/",
        "https://bioedca.github.io/tether/latest/privacy/",
        "https://bioedca.github.io/tether/versions.json",
    )
    assert guard.PAGES_ENDPOINT == "repos/bioedca/tether/pages"
    assert guard.PAGES_API_URL == "https://api.github.com/repos/bioedca/tether/pages"


def test_one_run_requests_every_url_and_the_endpoint(capsys: pytest.CaptureFixture[str]) -> None:
    rc, transport = _run()
    for url in (*guard.SITE_URLS, guard.PAGES_API_URL):
        assert url in transport.requested, f"main() never requested {url}"
    assert rc == 0
    assert _errors(capsys) == []


def test_the_root_fixture_is_the_verbatim_gh_pages_stub() -> None:
    # Normalised to LF: `* text=auto` checks this file out CRLF on the Windows matrix leg.
    assert hashlib.sha256(ROOT_STUB.replace(b"\r\n", b"\n")).hexdigest() == ROOT_STUB_SHA256
    # The single-line spelling is nowhere in the real bytes; a check built on it would be wrong.
    assert b'window.location.replace("latest/")' not in ROOT_STUB


def test_the_pages_probe_passes_the_captured_payload() -> None:
    assert guard.check_pages(R(200, json.dumps(PAGES_OK).encode()), 1) == []


@pytest.mark.parametrize(
    "source",
    [
        {"branch": "main", "path": "/docs"},
        {"branch": "gh-pages", "path": "/docs"},
        {"branch": "main", "path": "/"},
    ],
)
def test_the_pages_probe_fails_any_other_source(source: dict[str, str]) -> None:
    problems = guard.check_pages(R(200, json.dumps({**PAGES_OK, "source": source}).encode()), 1)
    assert len(problems) == 1 and "gh-pages" in problems[0]


def test_the_pages_probe_ignores_build_type_and_status() -> None:
    payload = {**PAGES_OK, "build_type": "workflow", "status": "building"}
    assert guard.check_pages(R(200, json.dumps(payload).encode()), 1) == []


def test_a_pages_403_is_a_failure_naming_the_permission() -> None:
    problems = guard.check_pages(R(403, b'{"message":"Resource not accessible"}'), 1)
    assert len(problems) == 1 and "permissions: pages: read" in problems[0]


@pytest.mark.parametrize(
    "limited",
    [
        R(403, b"{}", headers={"x-ratelimit-remaining": "0"}),
        R(403, b"{}", headers={"retry-after": "60"}),
        R(403, b'{"message":"You have exceeded a secondary rate limit."}'),
    ],
)
def test_a_rate_limited_403_is_not_diagnosed_as_a_permission(limited: R) -> None:
    (problem,) = guard.check_pages(limited, 1)
    assert (
        "rate limit" in problem and "pages: write" not in problem and "permissions" not in problem
    )


@pytest.mark.parametrize("generator", [GENERATOR_TODAY, GENERATOR_BUMPED])
def test_latest_matches_the_material_generator_by_name_not_version(generator: str) -> None:
    assert guard.check_latest(_latest(generator), 1) == []


@pytest.mark.parametrize(
    ("page", "named"),
    [
        (_latest(generator=None), "generator"),
        (_latest(nav=False), "md-nav"),
        (_latest(search=False), "md-search"),
    ],
)
def test_latest_fails_without_each_material_marker(page: R, named: str) -> None:
    problems = guard.check_latest(page, 1)
    assert len(problems) == 1 and named in problems[0] and guard.LATEST_URL in problems[0]


def test_the_root_accepts_the_verbatim_redirect_stub() -> None:
    # No md-nav, md-search or mkdocs generator is required at the root: the stub has none.
    assert guard.check_root(R(200, ROOT_STUB), 1) == []


def test_a_jekyll_root_fails_as_the_incident_signature() -> None:
    problems = guard.check_root(R(200, JEKYLL_ROOT), 1)
    assert any("Jekyll v3.10.0" in p for p in problems), problems
    # Even a Jekyll page that kept every redirect pointer is still the incident.
    disguised = ROOT_STUB.replace(
        b"<head>", b'<head><meta name="generator" content="Jekyll v3.10.0">'
    )
    assert any("Jekyll" in p for p in guard.check_root(R(200, disguised), 1))


@pytest.mark.parametrize(
    ("needle", "named"),
    [
        (b"url=latest/", "refresh"),
        (b'href="latest/"', "href"),
        (b"location.replace", "location.replace"),
    ],
)
def test_the_root_requires_each_pointer_at_latest(needle: bytes, named: str) -> None:
    problems = guard.check_root(R(200, ROOT_STUB.replace(needle, b"elsewhere")), 1)
    assert len(problems) == 1 and named in problems[0]


def test_privacy_404_fails_naming_the_url(capsys: pytest.CaptureFixture[str]) -> None:
    rc, _t = _run({guard.PRIVACY_URL: [R(404)]})
    (error,) = _errors(capsys)
    assert rc == 1 and guard.PRIVACY_URL in error and "404" in error


def test_privacy_requires_a_non_empty_body() -> None:
    assert guard.check_privacy(R(200, b"  \n"), 1)


def test_versions_json_resolves_latest_without_a_version_number() -> None:
    assert guard.check_versions(R(200, VERSIONS_OK), 1) == []
    assert guard.check_versions(R(200, b'[{"version":"dev","title":"dev","aliases":[]}]'), 1)
    assert guard.check_versions(R(200, b"not json"), 1)


@pytest.mark.parametrize("aliases", [b"1", b'"latest"', b"null", b'{"latest": 1}', b"[1]"])
def test_versions_json_requires_aliases_to_be_a_list(aliases: bytes) -> None:
    body = b'[{"version":"dev","title":"dev","aliases":' + aliases + b"}]"
    (problem,) = guard.check_versions(R(200, body), 1)
    assert guard.VERSIONS_URL in problem


@pytest.mark.parametrize(
    "body",
    [
        # A malformed entry is reported even when `latest` still resolves (#509 review).
        b'[{"version":"latest","aliases":1}]',
        b'[{"version":"dev","aliases":["latest"]},{"version":"0.9","aliases":"old"}]',
        b'[{"version":"dev","aliases":["latest"]},"0.9"]',
        b'[{"version":1,"aliases":["latest"]}]',
        b'[{"version":"dev","title":2,"aliases":["latest"]}]',
        b'{"version":"dev","aliases":["latest"]}',
    ],
)
def test_versions_json_shape_is_validated_independently_of_latest(body: bytes) -> None:
    (problem,) = guard.check_versions(R(200, body), 1)
    assert "shape" in problem and guard.VERSIONS_URL in problem


def test_a_version_named_latest_with_no_aliases_resolves() -> None:
    assert guard.check_versions(R(200, b'[{"version":"latest","title":"latest"}]'), 1) == []


class _BrokenBody:
    """A urlopen result whose body is cut short mid-read, as http.client reports it."""

    status, headers = 200, {}

    def __enter__(self) -> _BrokenBody:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self) -> bytes:
        raise http.client.IncompleteRead(b"<html>", 4096)


@pytest.mark.parametrize(
    "opener",
    [
        lambda *_a, **_k: _BrokenBody(),
        lambda *_a, **_k: (_ for _ in ()).throw(http.client.BadStatusLine("garbage")),
    ],
    ids=["incomplete-read", "bad-status-line"],
)
def test_an_http_protocol_failure_is_a_transport_failure(
    monkeypatch: pytest.MonkeyPatch, opener: object
) -> None:
    monkeypatch.setattr(guard.urllib.request, "urlopen", opener)
    response = guard.default_transport(guard.LATEST_URL, {})
    assert response.status is None and response.error
    # ... so it rides the retry path and is reported, never an exception out of main().
    (problem,) = guard.check_latest(response, guard.MAX_ATTEMPTS)
    assert "transport failure" in problem


def test_a_transient_503_is_retried_and_passes() -> None:
    rc, transport = _run({guard.LATEST_URL: [R(503), _latest()]})
    assert rc == 0
    assert transport.requested.count(guard.LATEST_URL) == 2


def test_a_persistent_503_fails_naming_the_url_and_attempt_count(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc, transport = _run({guard.VERSIONS_URL: [R(503)]})
    assert rc == 1
    assert transport.requested.count(guard.VERSIONS_URL) == guard.MAX_ATTEMPTS >= 3
    (error,) = _errors(capsys)
    assert guard.VERSIONS_URL in error and f"{guard.MAX_ATTEMPTS} attempt" in error


def test_a_transport_failure_is_never_reported_as_a_dead_site() -> None:
    rc, transport = _run({guard.ROOT_URL: [R(None, error="timed out")]})
    assert rc == 1 and transport.requested.count(guard.ROOT_URL) == guard.MAX_ATTEMPTS
    (problem,) = guard.check_root(R(None, error="timed out"), guard.MAX_ATTEMPTS)
    assert "transport failure" in problem and "HTTP" not in problem


def test_a_content_mismatch_is_not_retried() -> None:
    rc, transport = _run({guard.LATEST_URL: [_latest(generator=None)]})
    assert rc == 1 and transport.requested.count(guard.LATEST_URL) == 1


def test_main_reports_exactly_two_errors_for_two_problems(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    summary = tmp_path / "summary.md"
    rc, _t = _run(
        {guard.PRIVACY_URL: [R(404)], guard.VERSIONS_URL: [R(200, b"[]")]},
        GITHUB_STEP_SUMMARY=str(summary),
    )
    errors = _errors(capsys)
    assert rc == 1 and len(errors) == 2
    written = summary.read_text(encoding="utf-8")
    assert guard.PRIVACY_URL in written and guard.VERSIONS_URL in written


def test_the_token_is_sent_only_to_the_pages_api() -> None:
    seen: dict[str, dict[str, str]] = {}
    healthy = FakeTransport(_healthy())

    def recording(url: str, headers: dict[str, str]) -> R:
        seen[url] = dict(headers)
        return healthy(url, headers)

    assert (
        guard.main(transport=recording, sleep=lambda _s: None, environ={"GITHUB_TOKEN": "t0k"}) == 0
    )
    assert seen[guard.PAGES_API_URL]["Authorization"] == "Bearer t0k"
    assert all("Authorization" not in seen[url] for url in guard.SITE_URLS)


def test_the_guard_imports_only_the_standard_library() -> None:
    imported: set[str] = set()
    for node in ast.walk(ast.parse(SCRIPT.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            imported.add(node.module.partition(".")[0])
    assert {n for n in imported if n not in sys.stdlib_module_names} <= {"__future__"}


def test_the_job_timeout_outlasts_the_worst_case_retry_budget() -> None:
    import yaml  # provided by the base conda-lock (a mkdocs dependency)

    workflow = yaml.safe_load((_REPO / ".github/workflows/docs-liveness.yml").read_text("utf-8"))
    (job,) = workflow["jobs"].values()
    fetches = len(guard.SITE_URLS) + 1  # the four URLs and the Pages API
    backoff = sum(guard.BACKOFF_SECONDS * 2**i for i in range(guard.MAX_ATTEMPTS - 1))
    worst = fetches * (guard.MAX_ATTEMPTS * guard.TIMEOUT_SECONDS + backoff)
    assert job["timeout-minutes"] * 60 > worst + 60, (job["timeout-minutes"], worst)

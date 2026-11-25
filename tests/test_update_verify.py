# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The update verifier (#333): adversarial tests of every step and of the failure taxonomy.

No network here, and Qt only in the one headless ``pytest-qt`` gate at the end. ``gh`` is
a scripted stand-in that records every call and the environment it was given; the
attestations endpoint is a dictionary. Two fixtures are real, byte for byte: GitHub's
attestations answer for the ``v1.0.0-rc1`` Linux installer and the report ``gh`` 2.104.0
printed when it verified that installer offline (``tests/fixtures/PROVENANCE.md``). The real
``gh`` against the real installer runs in ``tests/test_update_verify_live.py``.
"""

from __future__ import annotations

import copy
import email.message
import hashlib
import importlib.util
import io
import json
import logging
import os
import pickle
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import urllib.response
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from tether.gui import app_settings, update_verify
from tether.gui.update_check import parse_version
from tether.gui.update_verify import (
    ATTESTATIONS_API,
    GH_MIN_VERSION,
    REPOSITORY,
    SIGNER_WORKFLOW,
    TRUSTED_ROOT_TIMEOUT_S,
    VERIFY_TIMEOUT_S,
    CertificateFacts,
    GhRun,
    Outcome,
    VerificationRefused,
    VerificationResult,
    VerificationUnavailable,
    VerifiedInstaller,
    check_channel,
    fetch_attestation_bundles,
    installer_name,
    platform_tag,
    stable_ref_version,
    verify_update,
)

_ROOT = Path(__file__).resolve().parents[1]
_PRD = _ROOT / "docs" / "PRD.md"
_FIXTURES = _ROOT / "tests" / "fixtures" / "update_verify"
_ANSWER = (_FIXTURES / "attestations-v1.0.0-rc1-linux-64.json").read_bytes()
_REPORT = (_FIXTURES / "gh-2.104.0-verify-v1.0.0-rc1-linux-64.json").read_bytes()
_RC1_DIGEST = "de6ae6ebbab9549d91ba5ff22e370117a80702d2abdc909be275ba74405ddb9c"

_TAG = "Linux-x86_64.sh"
_OFFERED = parse_version("v1.0.1")
_NAME = "Tether-1.0.1-Linux-x86_64.sh"
_ROOT_LINE = b'{"mediaType":"application/vnd.dev.sigstore.trustedroot+json;version=0.1"}\n'
_GH_VERSION = b"gh version 2.104.0 (2026-11-12)\nhttps://github.com/cli/cli/releases/tag/v2.104.0\n"
_TOKENS = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")


# --- stand-ins ------------------------------------------------------------------------


def _report(
    digest: str,
    *,
    ref: str = "refs/tags/v1.0.1",
    names: Sequence[str] = (_NAME,),
    statement_ref: str | None = None,
    repository: str = "https://github.com/bioedca/tether",
    copies: int = 1,
) -> bytes:
    """The real gh report, rewritten to describe a release named by ``ref``."""
    result = json.loads(_REPORT)[0]
    certificate = result["verificationResult"]["signature"]["certificate"]
    certificate["sourceRepositoryRef"] = ref
    certificate["sourceRepositoryURI"] = repository
    statement = result["verificationResult"]["statement"]
    statement["subject"] = [{"name": n, "digest": {"sha256": digest}} for n in names]
    if statement_ref is not None:
        build = statement["predicate"]["buildDefinition"]
        build["externalParameters"]["workflow"]["ref"] = statement_ref
        build["resolvedDependencies"][0]["uri"] = f"git+{repository}@{statement_ref}"
    return json.dumps([copy.deepcopy(result) for _ in range(copies)]).encode("utf-8")


class FakeGh:
    """Answers ``--version``, ``trusted-root`` and ``verify`` from a script, recording each."""

    def __init__(self, **answers: object) -> None:
        self.answers: dict[str, object] = {
            "version": GhRun(0, _GH_VERSION, b""),
            "trusted-root": GhRun(0, _ROOT_LINE, b""),
            "verify": None,  # filled in from the installer once it exists
        }
        self.answers.update(answers)
        self.calls: list[tuple[list[str], dict[str, str], float]] = []
        self.bundle_bytes: bytes | None = None
        self.workdirs: list[Path] = []

    @staticmethod
    def kind(argv: Sequence[str]) -> str:
        if list(argv[1:2]) == ["--version"]:
            return "version"
        return str(argv[2])

    def __call__(self, argv: Sequence[str], env: Mapping[str, str], timeout: float) -> GhRun:
        argv = list(argv)
        self.calls.append((argv, dict(env), timeout))
        kind = self.kind(argv)
        self.workdirs.append(Path(env["GH_CONFIG_DIR"]).parent)
        if kind == "verify":
            self.bundle_bytes = Path(argv[argv.index("--bundle") + 1]).read_bytes()
        answer = self.answers[kind]
        if isinstance(answer, BaseException):
            raise answer
        assert isinstance(answer, GhRun), f"no answer scripted for {kind}"
        return answer

    def kinds(self) -> list[str]:
        return [self.kind(argv) for argv, _env, _timeout in self.calls]

    def call(self, kind: str) -> tuple[list[str], dict[str, str], float]:
        return next(c for c in self.calls if self.kind(c[0]) == kind)


class FakeApi:
    """The attestations endpoint as a dictionary: bytes, ``None`` for 404, or an error."""

    def __init__(self, answers: Mapping[str, object]) -> None:
        self.answers = dict(answers)
        self.urls: list[str] = []

    def __call__(self, url: str) -> bytes | None:
        self.urls.append(url)
        answer = self.answers[url]
        if isinstance(answer, BaseException):
            raise answer
        return answer  # type: ignore[return-value]


def _answer_for(bundles: Sequence[object]) -> bytes:
    entries = [{"bundle": b, "repository_id": 1278484261} for b in bundles]
    return json.dumps({"attestations": entries}).encode("utf-8")


class Rig:
    """One installer on disk, allowed consent, a fake gh and a fake API that all succeed."""

    def __init__(self, tmp_path: Path, *, name: str = _NAME, content: bytes = b"installer") -> None:
        self.dir = tmp_path
        self.installer = tmp_path / "download" / name
        self.installer.parent.mkdir(parents=True, exist_ok=True)
        self.installer.write_bytes(content)
        self.digest = hashlib.sha256(content).hexdigest()
        self.settings = tmp_path / "settings.json"
        self.override = tmp_path / "no-override"
        app_settings.record_update_consent(True, self.settings)
        self.gh_path = tmp_path / "verifier" / "gh"
        self.gh = FakeGh(verify=GhRun(0, _report(self.digest), b""))
        self.api = FakeApi({self.url: _ANSWER})

    @property
    def url(self) -> str:
        return ATTESTATIONS_API.format(digest=self.digest)

    def run(self, version: object = _OFFERED, *, installer: Path | None = None, **kw: object):
        options: dict[str, object] = {
            "settings_file": self.settings,
            "override_file": self.override,
            "gh": self.gh_path,
            "tag": _TAG,
            "fetch": self.api,
            "runner": self.gh,
        }
        options.update(kw)
        return verify_update(installer or self.installer, version, **options)  # type: ignore[arg-type]


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Rig:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
    monkeypatch.setenv("NO_PROXY", "localhost")
    for name in _TOKENS:
        monkeypatch.setenv(name, "secret-token-must-not-reach-gh")
    return Rig(tmp_path)


def _assert_nothing_installable(result: VerificationResult) -> None:
    assert result.outcome is not Outcome.VERIFIED
    assert result.installer is None


# --- the happy path, and what each step carries ---------------------------------------------


def test_every_check_passing_yields_a_verified_installer(rig: Rig) -> None:
    result = rig.run()
    assert result.outcome is Outcome.VERIFIED, result.reason
    installer = result.installer
    assert isinstance(installer, VerifiedInstaller)
    assert installer.path == rig.installer
    assert installer.sha256 == rig.digest
    assert installer.version == _OFFERED
    assert installer.source_digest == "1ba112683a0f2a5ba842e39893fd757bff2d18b3"
    assert rig.gh.kinds() == ["version", "trusted-root", "verify"]
    assert rig.api.urls == [rig.url]


def test_the_invocation_carries_every_identity_flag(rig: Rig) -> None:
    rig.run()
    argv, _env, timeout = rig.gh.call("verify")
    assert argv[:4] == [str(rig.gh_path), "attestation", "verify", str(rig.installer)]

    def flag(name: str) -> str:
        assert argv.count(name) == 1, name
        return argv[argv.index(name) + 1]

    assert Path(flag("--bundle")).name == "bundle.jsonl"
    assert Path(flag("--custom-trusted-root")).name == "trusted_root.jsonl"
    assert flag("--repo") == REPOSITORY == "bioedca/tether"
    assert flag("--signer-workflow") == SIGNER_WORKFLOW
    assert SIGNER_WORKFLOW == "bioedca/tether/.github/workflows/release.yml"
    assert flag("--source-ref") == "refs/tags/v1.0.1"
    assert flag("--format") == "json"
    assert "--deny-self-hosted-runners" in argv
    assert timeout == VERIFY_TIMEOUT_S


def test_the_verify_step_has_no_network_left_in_it(rig: Rig) -> None:
    rig.run()
    _argv, env, _timeout = rig.gh.call("verify")
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY"):
        assert env[name] == "http://127.0.0.1:9", name
    assert env["NO_PROXY"] == "" and env["no_proxy"] == ""


def test_only_the_trusted_root_step_keeps_the_users_proxy(rig: Rig) -> None:
    rig.run()
    _argv, env, timeout = rig.gh.call("trusted-root")
    assert env["HTTPS_PROXY"] == "http://proxy.example:3128"
    assert env["NO_PROXY"] == "localhost"
    assert timeout == TRUSTED_ROOT_TIMEOUT_S
    _argv, env, _timeout = rig.gh.call("version")
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:9"


def test_gh_runs_with_no_token_no_telemetry_and_a_fresh_config(rig: Rig) -> None:
    rig.run()
    for _argv, env, _timeout in rig.gh.calls:
        for name in (*_TOKENS, "GH_HOST", "GH_REPO", "GH_DEBUG"):
            assert name not in env, name
        assert env["GH_TELEMETRY"] == "false"
        assert env["DO_NOT_TRACK"] == "1"
        assert env["GH_NO_UPDATE_NOTIFIER"] == "1"
        assert env["GH_PROMPT_DISABLED"] == "1"
        config, cache = Path(env["GH_CONFIG_DIR"]), Path(env["XDG_CACHE_HOME"])
        assert config.parent == cache.parent
        assert config.parent.name.startswith("tether-verify-")
    # One scratch folder for the whole check, removed afterwards.
    assert len(set(rig.gh.workdirs)) == 1
    assert not rig.gh.workdirs[0].exists()


def test_the_attestation_request_is_unauthenticated() -> None:
    handler = _Canned({ATTESTATIONS_API.format(digest=_RC1_DIGEST): (200, {}, _ANSWER)})
    opener = urllib.request.build_opener(handler, update_verify._ApiOnlyRedirect())
    body = update_verify._default_fetch(ATTESTATIONS_API.format(digest=_RC1_DIGEST), opener=opener)
    assert body == _ANSWER
    (request,) = handler.requests
    assert request.get_method() == "GET"
    headers = {k.lower(): v for k, v in request.header_items()}
    headers.pop("host", None)  # added by urllib from the URL
    assert headers == {"accept": "application/vnd.github+json", "user-agent": "Tether"}
    assert ATTESTATIONS_API.startswith("https://api.github.com/repos/bioedca/tether/attestations/")


# --- requirement 3: explicit UTF-8 ----------------------------------------------------------


def test_the_bundle_handed_to_gh_keeps_the_checkpoint_em_dash(rig: Rig) -> None:
    rig.api.answers[rig.url] = _ANSWER
    rig.run()
    written = rig.gh.bundle_bytes
    assert written is not None
    written.decode("ascii")  # nothing a platform encoding could reinterpret
    (line,) = written.decode("ascii").splitlines()
    original = json.loads(_ANSWER.decode("utf-8"))["attestations"][0]["bundle"]
    assert json.loads(line) == original
    checkpoint = json.dumps(original, ensure_ascii=False)
    assert "— rekor.sigstore.dev" in checkpoint  # the character cp1252 corrupts


def test_the_real_answer_is_read_as_utf8_not_the_platform_default() -> None:
    assert "—".encode() in _ANSWER  # the real answer carries the em-dash
    fetch = FakeApi({ATTESTATIONS_API.format(digest=_RC1_DIGEST): _ANSWER})
    (bundle,) = fetch_attestation_bundles(_RC1_DIGEST, fetch=fetch)
    text = json.dumps(bundle, ensure_ascii=False)
    assert "— rekor" in text
    assert "â€”" not in text  # what cp1252 makes of those three bytes


def test_an_answer_that_is_not_utf8_is_could_not_verify_not_tamper(rig: Rig) -> None:
    # The same answer as a cp1252 encoder writes it: not UTF-8. That is a broken answer,
    # so verification did not run, and the user is not told the file was tampered with.
    rig.api.answers[rig.url] = _ANSWER.decode("utf-8").encode("cp1252")
    result = rig.run()
    assert result.outcome is Outcome.UNAVAILABLE
    assert "verify" not in rig.gh.kinds()


# --- consent and the local checks run before any network ------------------------------------


@pytest.mark.parametrize("answer", [None, False, "override"])
def test_nothing_runs_without_consent(rig: Rig, answer: object) -> None:
    rig.settings.unlink()
    if answer is False:
        app_settings.record_update_consent(False, rig.settings)
    if answer == "override":
        app_settings.record_update_consent(True, rig.settings)
        rig.override.write_text("")
    result = rig.run()
    assert result.outcome is Outcome.UNAVAILABLE
    assert rig.gh.calls == [] and rig.api.urls == []


def test_consent_withdrawn_while_hashing_sends_nothing(rig: Rig, monkeypatch) -> None:
    real_hash = update_verify._sha256_of

    def hash_then_untick(path: Path) -> str:
        digest = real_hash(path)
        app_settings.record_update_consent(False, rig.settings)  # Help item unticked now
        return digest

    monkeypatch.setattr(update_verify, "_sha256_of", hash_then_untick)
    result = rig.run()
    assert result.outcome is Outcome.UNAVAILABLE
    assert rig.api.urls == []
    assert rig.gh.kinds() == ["version"]  # no trusted-root, no verify


@pytest.mark.parametrize("after", [0, 1])
def test_a_cancel_stops_before_the_next_network_step(rig: Rig, after: int) -> None:
    asked = {"n": 0}

    def cancelled() -> bool:
        asked["n"] += 1
        return asked["n"] > after + 1  # the first ask is the consent step

    result = rig.run(cancelled=cancelled)
    assert result.outcome is Outcome.UNAVAILABLE
    _assert_nothing_installable(result)
    assert len(rig.api.urls) == after  # 0: no attestation request; 1: that one only
    assert "trusted-root" not in rig.gh.kinds() and "verify" not in rig.gh.kinds()


@pytest.mark.parametrize(
    "version", ["v1.0.1-rc1", "1.0.1rc1", "1.0.1.dev3", "1.0.1.post1", "1.0.1.1", "1.0.1a1"]
)
def test_an_offered_version_that_is_not_stable_is_refused_before_any_request(
    rig: Rig, version: str
) -> None:
    result = rig.run(parse_version(version))
    assert result.outcome is Outcome.REFUSED
    assert rig.gh.calls == [] and rig.api.urls == []


def test_an_offered_version_that_is_not_a_version_is_refused(rig: Rig) -> None:
    result = rig.run("1.0.1")
    assert result.outcome is Outcome.REFUSED
    assert rig.gh.calls == [] and rig.api.urls == []


@pytest.mark.parametrize(
    "name",
    [
        "Tether-1.0.1-MacOSX-arm64.pkg",
        "Tether-1.0.1-Windows-x86_64.exe",
        "Tether-1.0.0-Linux-x86_64.sh",
        "Tether-1.0.2-Linux-x86_64.sh",
        "tether-1.0.1-linux-x86_64.sh",
        "Tether-1.0.1-Linux-x86_64.sh.part",
    ],
)
def test_a_file_that_is_not_this_platforms_installer_is_refused(tmp_path: Path, name: str) -> None:
    rig = Rig(tmp_path, name=name)
    result = rig.run()
    assert result.outcome is Outcome.REFUSED
    assert rig.gh.calls == [] and rig.api.urls == []


def test_a_link_in_place_of_the_installer_is_refused(rig: Rig, tmp_path: Path) -> None:
    link = tmp_path / "links" / _NAME
    link.parent.mkdir()
    try:
        link.symlink_to(rig.installer)
    except (OSError, NotImplementedError):
        pytest.skip("this system cannot make a symbolic link")
    result = rig.run(installer=link)
    assert result.outcome is Outcome.REFUSED
    assert rig.api.urls == []


def test_a_missing_installer_is_refused(rig: Rig) -> None:
    rig.installer.unlink()
    assert rig.run().outcome is Outcome.REFUSED


def test_a_copy_with_no_bundled_verifier_says_nothing(rig: Rig) -> None:
    # The caller passes tether._verifier.bundled_gh(), which is None outside an install.
    result = rig.run(gh=None)
    assert result.outcome is Outcome.UNAVAILABLE
    assert rig.gh.calls == [] and rig.api.urls == []


def test_a_platform_with_no_published_installer_says_nothing(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(update_verify, "platform_tag", lambda: None)
    result = rig.run(tag=None)
    assert result.outcome is Outcome.UNAVAILABLE
    assert rig.gh.calls == [] and rig.api.urls == []


@pytest.mark.parametrize(
    ("system", "machine", "tag"),
    [
        ("linux", "x86_64", "Linux-x86_64.sh"),
        ("darwin", "arm64", "MacOSX-arm64.pkg"),
        ("darwin", "x86_64", "MacOSX-x86_64.pkg"),
        ("win32", "AMD64", "Windows-x86_64.exe"),
        ("linux", "aarch64", None),
        ("win32", "ARM64", None),
        ("freebsd14", "amd64", None),
    ],
)
def test_the_platform_names_its_published_installer(
    system: str, machine: str, tag: str | None
) -> None:
    assert platform_tag(system, machine) == tag


def test_installer_names_follow_the_published_assets() -> None:
    # The v1.0.0-rc1 assets, with the version replaced: release.yml names them this way.
    assert installer_name(parse_version("1.0"), "Linux-x86_64.sh") == "Tether-1.0.0-Linux-x86_64.sh"
    assert installer_name(parse_version("v2.10.3"), "Windows-x86_64.exe") == (
        "Tether-2.10.3-Windows-x86_64.exe"
    )
    assert installer_name(parse_version("v1.0.0-rc1"), "Linux-x86_64.sh") is None


# --- requirement 4: the runtime gh floor ----------------------------------------------------


@pytest.mark.parametrize(
    ("line", "outcome"),
    [
        (b"gh version 2.67.0 (2025-02-12)\n", Outcome.VERIFIED),
        (b"gh version 2.104.0 (2026-11-12)\n", Outcome.VERIFIED),
        (b"gh version 3.0.0\n", Outcome.VERIFIED),
        (b"gh version 2.66.1 (2025-01-31)\n", Outcome.REFUSED),
        (b"gh version 2.49.0 (2024-05-13)\n", Outcome.REFUSED),
        (b"gh version 1.99.99\n", Outcome.REFUSED),
        (b"gh version 2.67\n", Outcome.REFUSED),
        (b"gh version 2.67.0x\n", Outcome.REFUSED),
        (b"not gh\n", Outcome.REFUSED),
        (b"", Outcome.REFUSED),
    ],
)
def test_the_gh_version_floor_is_enforced_at_runtime(
    rig: Rig, line: bytes, outcome: Outcome
) -> None:
    rig.gh.answers["version"] = GhRun(0, line, b"")
    result = rig.run()
    assert result.outcome is outcome, result.reason
    if outcome is Outcome.REFUSED:
        assert rig.api.urls == []  # refused before any request


def test_the_floor_is_the_cve_floor() -> None:
    assert GH_MIN_VERSION == (2, 67, 0)


@pytest.mark.parametrize(
    "answer",
    [GhRun(1, _GH_VERSION, b""), OSError("not executable"), subprocess.TimeoutExpired("gh", 1)],
)
def test_a_verifier_that_will_not_say_its_version_is_refused(rig: Rig, answer: object) -> None:
    rig.gh.answers["version"] = answer
    assert rig.run().outcome is Outcome.REFUSED
    assert rig.api.urls == []


# --- step 3: what the fetches can answer ----------------------------------------------------


def test_no_attestation_for_these_bytes_is_refused_visibly(rig: Rig) -> None:
    # A tampered or corrupted download has a digest GitHub has never seen.
    rig.api.answers[rig.url] = None
    result = rig.run()
    assert result.outcome is Outcome.REFUSED
    assert "no build-provenance attestation" in result.reason
    assert "verify" not in rig.gh.kinds()


def test_an_empty_attestation_list_is_refused_visibly(rig: Rig) -> None:
    rig.api.answers[rig.url] = b'{"attestations": []}'
    assert rig.run().outcome is Outcome.REFUSED


@pytest.mark.parametrize(
    "answer",
    [
        VerificationUnavailable("URLError"),
        b"<html>captive portal</html>",
        b'{"message": "API rate limit exceeded"}',
        b'{"attestations": "nope"}',
        b"[]",
        b"",
        b"[" * 100_000,
    ],
    # Short ids: the deep-JSON answer would otherwise name a tmp_path too long for Windows.
    ids=["network", "html", "rate-limit", "not-a-list", "bare-list", "empty", "deep-json"],
)
def test_an_answer_that_is_not_usable_is_silent(rig: Rig, answer: object) -> None:
    rig.api.answers[rig.url] = answer
    result = rig.run()
    assert result.outcome is Outcome.UNAVAILABLE
    assert "verify" not in rig.gh.kinds()


def test_a_bundle_url_alone_is_not_followed(rig: Rig) -> None:
    # GitHub has served "bundle": null with only a bundle_url. That URL names a storage
    # host outside the listed endpoints, so it is not fetched: verification cannot run.
    entry = {"bundle": None, "bundle_url": "https://tmaproduction.blob.core.windows.net/x"}
    rig.api.answers[rig.url] = json.dumps({"attestations": [entry]}).encode()
    result = rig.run()
    assert result.outcome is Outcome.UNAVAILABLE
    assert rig.api.urls == [rig.url]
    assert "verify" not in rig.gh.kinds()


def test_the_inline_bundle_is_preferred_and_a_null_one_skipped(rig: Rig) -> None:
    original = json.loads(_ANSWER)["attestations"][0]
    nulled = {"bundle": None, "bundle_url": "https://tmaproduction.blob.core.windows.net/x"}
    rig.api.answers[rig.url] = json.dumps({"attestations": [nulled, original]}).encode()
    assert rig.run().outcome is Outcome.VERIFIED
    (line,) = rig.gh.bundle_bytes.decode("ascii").splitlines()
    assert json.loads(line) == original["bundle"]


def test_every_inline_bundle_is_handed_to_gh(rig: Rig) -> None:
    bundle = json.loads(_ANSWER)["attestations"][0]["bundle"]
    rig.api.answers[rig.url] = _answer_for([bundle, bundle])
    rig.run()
    assert len(rig.gh.bundle_bytes.decode("ascii").splitlines()) == 2


@pytest.mark.parametrize(
    "answer",
    [
        GhRun(1, b"", b"Failed to verify the TUF repository"),
        GhRun(0, b"", b""),
        GhRun(0, b"not json\n", b""),
        GhRun(0, b"[]\n", b""),
        GhRun(0, b"{}\n", b""),
        subprocess.TimeoutExpired("gh", TRUSTED_ROOT_TIMEOUT_S),
        OSError("gone"),
    ],
)
def test_a_trusted_root_that_cannot_be_fetched_is_silent(rig: Rig, answer: object) -> None:
    rig.gh.answers["trusted-root"] = answer
    result = rig.run()
    assert result.outcome is Outcome.UNAVAILABLE
    assert "verify" not in rig.gh.kinds()


# --- step 4: any failure of the offline verify is a refusal ----------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        GhRun(1, b"", b'Error: verifying with issuer "sigstore.dev"'),
        GhRun(1, b"", b"error creating Sigstore verifier"),
        GhRun(4, b"", b"authentication required"),
        GhRun(0, b"", b""),
        GhRun(0, b"not json", b""),
        GhRun(0, b"[]", b""),
        GhRun(0, b"[{}]", b""),
        GhRun(0, b'[{"verificationResult": {"signature": {}}}]', b""),
        subprocess.TimeoutExpired("gh", VERIFY_TIMEOUT_S),
        OSError("gone"),
    ],
)
def test_any_failure_of_the_offline_verify_is_refused(rig: Rig, answer: object) -> None:
    rig.gh.answers["verify"] = answer
    result = rig.run()
    assert result.outcome is Outcome.REFUSED
    assert result.installer is None


def test_an_unexpected_error_is_refused_never_raised(rig: Rig) -> None:
    rig.gh.answers["verify"] = RuntimeError("a bug")
    result = rig.run()
    assert result.outcome is Outcome.REFUSED
    assert "RuntimeError" in result.reason


# --- step 5: Tether's policy on the certificate ----------------------------------------------


@pytest.mark.parametrize(
    "ref",
    [
        "refs/heads/main",
        "refs/tags/v1.0.1.0",
        "refs/tags/v1.0.1-rc1",
        "refs/tags/v1.0.1+build.1",
        "refs/tags/v01.0.1",
        "refs/tags/1.0.1",
        "refs/tags/v1.0.1\n",
        "refs/tags/v1.0.1 ",
        "refs/tags/v١.0.1",  # ARABIC-INDIC DIGIT ONE
        "refs/tags/V1.0.1",
        "refs/pull/1/merge",
        "",
    ],
)
def test_the_certificate_ref_must_match_the_stable_allowlist(rig: Rig, ref: str) -> None:
    rig.gh.answers["verify"] = GhRun(0, _report(rig.digest, ref=ref), b"")
    result = rig.run()
    assert result.outcome is Outcome.REFUSED
    assert result.installer is None


@pytest.mark.parametrize(
    ("ref", "triple"),
    [
        ("refs/tags/v1.0.0", (1, 0, 0)),
        ("refs/tags/v0.10.0", (0, 10, 0)),
        ("refs/tags/v10.20.30", (10, 20, 30)),
        ("refs/heads/main", None),
        ("refs/tags/v1.0.0.0", None),
        ("refs/tags/v1.0.0-rc1", None),
        (None, None),
    ],
)
def test_the_allowlist_itself(ref: object, triple: object) -> None:
    assert stable_ref_version(ref) == triple


def test_the_certificate_version_must_be_the_offered_version(rig: Rig) -> None:
    # A genuine attestation of 1.0.2 does not let the client install it when it compared 1.0.1.
    rig.gh.answers["verify"] = GhRun(0, _report(rig.digest, ref="refs/tags/v1.0.2"), b"")
    result = rig.run()
    assert result.outcome is Outcome.REFUSED
    assert "different version" in result.reason


def test_a_stable_tag_in_the_statement_does_not_rescue_a_prerelease_certificate(
    rig: Rig,
) -> None:
    report = _report(rig.digest, ref="refs/tags/v1.0.1-rc1", statement_ref="refs/tags/v1.0.1")
    rig.gh.answers["verify"] = GhRun(0, report, b"")
    assert rig.run().outcome is Outcome.REFUSED


def test_a_prerelease_tag_in_the_statement_is_not_consulted(rig: Rig) -> None:
    report = _report(rig.digest, ref="refs/tags/v1.0.1", statement_ref="refs/tags/v1.0.1-rc1")
    rig.gh.answers["verify"] = GhRun(0, report, b"")
    assert rig.run().outcome is Outcome.VERIFIED


def test_the_certificate_path_is_the_one_gh_reports() -> None:
    # Measured on gh 2.104.0: the ref is at verificationResult.signature.certificate.
    (result,) = json.loads(_REPORT)
    certificate = result["verificationResult"]["signature"]["certificate"]
    assert certificate["sourceRepositoryRef"] == "refs/tags/v1.0.0-rc1"
    assert certificate["sourceRepositoryURI"] == "https://github.com/bioedca/tether"
    facts = update_verify._facts(result)
    assert facts.source_ref == "refs/tags/v1.0.0-rc1"


@pytest.mark.parametrize(
    "names",
    [
        ("Tether-1.0.1-MacOSX-arm64.pkg",),
        ("Tether-1.0.1-Windows-x86_64.exe", "Tether-1.0.1-MacOSX-x86_64.pkg"),
        ("Tether-1.0.0-Linux-x86_64.sh",),
        (),
    ],
)
def test_the_subject_must_name_this_platforms_installer(rig: Rig, names: tuple[str, ...]) -> None:
    # One bundle covers all four installers, so a digest match alone proves nothing.
    rig.gh.answers["verify"] = GhRun(0, _report(rig.digest, names=names), b"")
    result = rig.run()
    assert result.outcome is Outcome.REFUSED
    assert "this platform" in result.reason


def test_the_subject_digest_must_be_the_files(rig: Rig) -> None:
    rig.gh.answers["verify"] = GhRun(0, _report("0" * 64), b"")
    assert rig.run().outcome is Outcome.REFUSED


def test_the_source_repository_must_be_tethers(rig: Rig) -> None:
    report = _report(rig.digest, repository="https://github.com/someone/tether")
    rig.gh.answers["verify"] = GhRun(0, report, b"")
    assert rig.run().outcome is Outcome.REFUSED


def test_every_verified_result_must_pass_not_just_one(rig: Rig) -> None:
    good = json.loads(_report(rig.digest))[0]
    bad = json.loads(_report(rig.digest, ref="refs/tags/v1.0.1-rc1"))[0]
    rig.gh.answers["verify"] = GhRun(0, json.dumps([good, bad]).encode(), b"")
    assert rig.run().outcome is Outcome.REFUSED


def test_the_channel_layer_refuses_the_real_rc1_certificate() -> None:
    # The channel layer on its own, fed the certificate gh really verified for v1.0.0-rc1.
    facts = update_verify._facts(json.loads(_REPORT)[0])
    with pytest.raises(VerificationRefused, match="stable"):
        check_channel(facts, parse_version("v1.0.0-rc1"))
    with pytest.raises(VerificationRefused):
        check_channel(facts, parse_version("v1.0.0"))


# --- no path reaches an installer without a successful verify -------------------------------


def test_a_verified_installer_cannot_be_made_outside_the_verifier(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        VerifiedInstaller(object(), tmp_path / _NAME, _OFFERED, "0" * 64, "0" * 40)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        VerifiedInstaller()  # type: ignore[call-arg]


def test_a_verified_installer_cannot_be_changed_or_copied(rig: Rig) -> None:
    installer = rig.run().installer
    assert installer is not None
    with pytest.raises(AttributeError):
        installer._path = Path("elsewhere")  # type: ignore[misc]
    with pytest.raises(AttributeError):
        del installer._sha256
    with pytest.raises(TypeError):
        pickle.dumps(installer)
    with pytest.raises(TypeError):
        copy.copy(installer)


def test_a_result_cannot_claim_verified_without_an_installer() -> None:
    with pytest.raises(ValueError):
        VerificationResult(Outcome.VERIFIED, "verified")
    with pytest.raises(ValueError):
        VerificationResult(Outcome.REFUSED, "no", installer="a path")  # type: ignore[arg-type]


_FAILURES = {
    "consent": lambda rig: rig.settings.unlink(),
    "version floor": lambda rig: rig.gh.answers.update(version=GhRun(0, b"gh version 2.0.0", b"")),
    "network": lambda rig: rig.api.answers.update({rig.url: VerificationUnavailable("down")}),
    "no attestation": lambda rig: rig.api.answers.update({rig.url: None}),
    "trusted root": lambda rig: rig.gh.answers.update({"trusted-root": GhRun(1, b"", b"")}),
    "verify": lambda rig: rig.gh.answers.update(verify=GhRun(1, b"", b"")),
    "channel": lambda rig: rig.gh.answers.update(
        verify=GhRun(0, _report(rig.digest, ref="refs/tags/v1.0.1-rc1"), b"")
    ),
    "subject": lambda rig: rig.gh.answers.update(
        verify=GhRun(0, _report(rig.digest, names=("x",)), b"")
    ),
    "unexpected": lambda rig: rig.gh.answers.update(verify=ValueError("?")),
}


@pytest.mark.parametrize("step", sorted(_FAILURES))
def test_no_failing_step_yields_an_installer(rig: Rig, step: str) -> None:
    _FAILURES[step](rig)
    _assert_nothing_installable(rig.run())


def test_the_happy_path_is_the_only_one_that_yields_an_installer(rig: Rig) -> None:
    # The control for the test above: the same rig with nothing broken does verify.
    assert rig.run().outcome is Outcome.VERIFIED


# --- the failure taxonomy as the user meets it ----------------------------------------------


def test_offline_is_silent_quiet_and_quick(rig: Rig, caplog: pytest.LogCaptureFixture) -> None:
    rig.api.answers[rig.url] = VerificationUnavailable("URLError: no route to host")
    caplog.set_level(logging.DEBUG)
    started = time.perf_counter()
    result = rig.run()
    elapsed = time.perf_counter() - started
    assert result.outcome is Outcome.UNAVAILABLE
    assert all(record.levelno <= logging.DEBUG for record in caplog.records)
    assert elapsed < 2.0


def test_a_refusal_carries_a_sentence_and_logs_nothing_above_debug(
    rig: Rig, caplog: pytest.LogCaptureFixture
) -> None:
    rig.gh.answers["verify"] = GhRun(1, b"", b'Error: verifying with issuer "sigstore.dev"')
    caplog.set_level(logging.DEBUG)
    result = rig.run()
    assert result.outcome is Outcome.REFUSED
    assert result.reason.endswith(".") and "not installed" in result.reason
    assert all(record.levelno <= logging.DEBUG for record in caplog.records)


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_the_real_request_with_no_network_is_silent(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed = f"http://127.0.0.1:{_closed_port()}"
    for name in (
        "HTTPS_PROXY",
        "https_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "ALL_PROXY",
        "all_proxy",
    ):
        monkeypatch.setenv(name, closed)  # urllib reads the lower-case spelling first
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    started = time.perf_counter()
    result = rig.run(fetch=update_verify._default_fetch)
    assert result.outcome is Outcome.UNAVAILABLE
    assert time.perf_counter() - started < 5.0
    assert "verify" not in rig.gh.kinds()


def test_the_shell_startup_path_does_not_load_the_verifier() -> None:
    # The verifier is never on the start-up path, so it cannot delay the window or show
    # anything there: the notice module that runs at start-up does not import it.
    code = (
        "import sys\n"
        "import tether.gui.update_notice, tether.gui.update_check\n"
        "print('tether.gui.update_verify' in sys.modules)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=120
    ).stdout.strip()
    assert out == "False"


def test_importing_the_verifier_costs_no_qt_and_makes_no_request() -> None:
    code = (
        "import socket, sys\n"
        "def refuse(*a, **k):\n"
        "    raise SystemExit('a connection was attempted at import')\n"
        "socket.socket.connect = refuse\n"
        "import tether.gui.update_verify\n"
        "heavy = sorted({m.split('.')[0] for m in sys.modules} & "
        "{'PySide6', 'PyQt5', 'pyqtgraph', 'numpy', 'h5py'})\n"
        "print(heavy)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=120
    ).stdout.strip()
    assert out == "[]"


# --- the HTTP request itself ----------------------------------------------------------------


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


def _fetch(answers: dict[str, tuple[int, dict[str, str], bytes]], url: str) -> bytes | None:
    handler = _Canned(answers)
    opener = urllib.request.build_opener(handler, update_verify._ApiOnlyRedirect())
    return update_verify._default_fetch(url, opener=opener)


_URL = ATTESTATIONS_API.format(digest=_RC1_DIGEST)


def test_a_404_is_the_answer_no_attestation() -> None:
    assert _fetch({_URL: (404, {}, b'{"message":"Not Found"}')}, _URL) is None


@pytest.mark.parametrize(
    ("code", "headers"),
    [
        (403, {"X-RateLimit-Remaining": "0"}),
        (429, {}),
        (500, {}),
        (502, {}),
        (401, {}),
    ],
)
def test_every_other_error_is_could_not_run(code: int, headers: dict[str, str]) -> None:
    with pytest.raises(VerificationUnavailable):
        _fetch({_URL: (code, headers, b"")}, _URL)


def test_a_redirect_off_the_api_host_is_not_followed() -> None:
    answers = {_URL: (302, {"Location": "https://evil.example/bundle"}, b"")}
    with pytest.raises(VerificationUnavailable):
        _fetch(answers, _URL)


def test_an_oversized_answer_is_not_read_whole() -> None:
    body = b" " * (update_verify._MAX_ANSWER_BYTES + 10)
    with pytest.raises(VerificationUnavailable):
        _fetch({_URL: (200, {}, body)}, _URL)


@pytest.mark.parametrize("digest", ["", "0" * 63, "G" * 64, "A" * 64, "0" * 64 + "/../x"])
def test_only_a_sha256_digest_is_ever_put_in_the_url(digest: str) -> None:
    fetch = FakeApi({})
    with pytest.raises(VerificationRefused):
        fetch_attestation_bundles(digest, fetch=fetch)
    assert fetch.urls == []


# --- the environment builder ---------------------------------------------------------------


def test_the_environment_builder_scrubs_and_isolates(tmp_path: Path) -> None:
    base = dict.fromkeys((*_TOKENS, "GH_HOST", "GH_REPO", "GH_DEBUG", "PATH"), "t")
    base.update(HTTPS_PROXY="http://p:1", NO_PROXY="corp")
    online = update_verify._gh_env(tmp_path, offline=False, base=base)
    offline = update_verify._gh_env(tmp_path, offline=True, base=base)
    for env in (online, offline):
        assert not set(base) - {"PATH", "HTTPS_PROXY", "NO_PROXY"} & set(env)
        assert env["PATH"] == "t"
        assert Path(env["GH_CONFIG_DIR"]).is_dir() and Path(env["XDG_CACHE_HOME"]).is_dir()
    assert online["HTTPS_PROXY"] == "http://p:1" and online["NO_PROXY"] == "corp"
    assert offline["HTTPS_PROXY"] == "http://127.0.0.1:9" and offline["NO_PROXY"] == ""


# --- what the documentation must keep saying --------------------------------------------------


def test_the_timeouts_are_registered_in_the_prd() -> None:
    section = _PRD.read_text(encoding="utf-8").split("### 11.2", 1)[1].split("### 11.3", 1)[0]
    rows = [line for line in section.splitlines() if "TRUSTED_ROOT_TIMEOUT_S" in line]
    assert rows, "PRD §11.2 has no update-verification row"
    assert "VERIFY_TIMEOUT_S" in rows[0]
    assert f"**{TRUSTED_ROOT_TIMEOUT_S:g} s**" in rows[0]
    assert f"**{VERIFY_TIMEOUT_S:g} s**" in rows[0]


def test_the_prd_names_both_hosts_of_the_trusted_root_service() -> None:
    # gh's TUF client reaches both; an administrator who allows one silently disables the
    # check. The privacy page lists them with the PR that first calls the verifier (#331).
    # Compared as whole code spans, not substrings: a host name is a token, not a fragment.
    spans = set(re.findall(r"`([^`\s]+)`", _PRD.read_text(encoding="utf-8")))
    assert {"tuf-repo-cdn.sigstore.dev", "tuf-repo.github.com"} <= spans


def test_the_manual_command_in_the_release_guide_matches_the_app() -> None:
    guide = (_ROOT / "docs" / "release.md").read_text(encoding="utf-8")
    assert f"--repo {REPOSITORY}" in guide
    assert f"--signer-workflow {SIGNER_WORKFLOW}" in guide
    assert "--custom-trusted-root" in guide


def test_facts_are_plain_data() -> None:
    facts = CertificateFacts("u", "r", "d", (("n", "x"),))
    assert facts.subjects == (("n", "x"),)
    with pytest.raises(AttributeError):
        facts.source_ref = "other"  # type: ignore[misc]


def test_the_environment_keeps_os_variables_gh_needs(rig: Rig) -> None:
    rig.run()
    _argv, env, _timeout = rig.gh.call("trusted-root")
    assert env.get("PATH") == os.environ.get("PATH")


# --- the GUI gate ----------------------------------------------------------------------------

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))


@pytest.mark.gui
@pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed")
def test_the_verifier_gives_the_same_verdicts_inside_the_running_gui(qtbot, rig: Rig) -> None:
    """Headless pytest-qt gate (AGENTS.md): a running GUI changes no verdict.

    The verifier is Qt-free, but it will be called from inside the application (#331). With
    the real shell up, the same file still verifies, a wrong digest is still refused, a
    network failure is still silent, and declining consent still means no ``gh`` call.
    """
    from tether.gui.app import create_shell

    _qt_app, shell = create_shell([], splash=False)
    qtbot.addWidget(shell.window)
    try:
        verified = rig.run()
        assert verified.outcome is Outcome.VERIFIED, verified.reason
        assert isinstance(verified.installer, VerifiedInstaller)

        rig.gh.answers["verify"] = GhRun(0, _report("0" * 64), b"")
        _assert_nothing_installable(rig.run())

        rig.api.answers[rig.url] = VerificationUnavailable("URLError")
        assert rig.run().outcome is Outcome.UNAVAILABLE

        calls = len(rig.gh.calls)
        app_settings.record_update_consent(False, rig.settings)
        _assert_nothing_installable(rig.run())
        assert len(rig.gh.calls) == calls
    finally:
        shell.close()

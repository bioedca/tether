# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The update verifier against a real ``gh`` and a real published installer (#333).

Opt-in, never in the required matrix: these need an ~800 MB release installer on disk, the
network for the attestations API, and a ``gh`` at or above the floor. Point at them with

- ``TETHER_LIVE_GH``: the ``gh`` to run (``packaging/scripts/stage_gh.py stage`` stages the
  pinned one);
- ``TETHER_LIVE_INSTALLER``: a downloaded ``Tether-<version>-<platform>`` installer from the
  Releases page, unrenamed, e.g. ``Tether-1.0.0-rc1-Linux-x86_64.sh``;
- optionally ``TETHER_LIVE_TRUSTED_ROOT``: a ``trusted_root.jsonl`` to use instead of
  fetching one, for a network that blocks the Sigstore TUF hosts (the run then says so);
- optionally ``TETHER_LIVE_FOREIGN``: ``<file>=<owner/repo>``, a genuine artifact of another
  repository whose attestation the API serves unauthenticated (``gh``'s own release archive
  with ``cli/cli`` does).

and run ``pytest -m large tests/test_update_verify_live.py``. Only ``v1.0.0-rc*`` releases
exist, so the installer is a prerelease: the verifier layer must accept it and the channel
layer must refuse it, asserted separately (issue #333, acceptance criteria (a) and (b)).
"""

from __future__ import annotations

import hashlib
import os
import re
import socket
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from tether.gui import update_verify
from tether.gui.update_check import parse_version
from tether.gui.update_verify import (
    GhRun,
    VerificationRefused,
    check_channel,
    check_gh_version,
    fetch_attestation_bundles,
    fetch_trusted_root,
    verify_provenance,
)

_GH = os.environ.get("TETHER_LIVE_GH", "")
_INSTALLER = os.environ.get("TETHER_LIVE_INSTALLER", "")
_ROOT_OVERRIDE = os.environ.get("TETHER_LIVE_TRUSTED_ROOT", "")
_FOREIGN = os.environ.get("TETHER_LIVE_FOREIGN", "")

# A marker and not a module-level skip: a skip at import fires before `-m "not large"` can
# deselect these, and would count against every required leg's skip budget.
pytestmark = [
    pytest.mark.large,
    pytest.mark.skipif(
        not (_GH and _INSTALLER),
        reason="set TETHER_LIVE_GH and TETHER_LIVE_INSTALLER to run the live verifier tests",
    ),
]
# The v1.0.0-rc3 Linux installer's digest, from the release's asset list (2026-11-19): a
# genuine bundle of a DIFFERENT release, for the wrong-release test.
_OTHER_RELEASE_DIGEST = "64179845dae7cd2fd0a96e3cb3fb281de62d1fed96f3f7323bc62970a2ccb1f3"

_NAME = re.compile(r"Tether-(?P<version>[0-9][^-]*(?:-rc[0-9]+)?)-(?P<tag>.+)")


class _Recorder:
    """A proxy on 127.0.0.1 that accepts and drops every connection, counting them."""

    def __init__(self) -> None:
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.requests: list[bytes] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except OSError:
                continue
            with conn:
                conn.settimeout(1)
                try:
                    self.requests.append(conn.recv(200))
                except OSError:
                    self.requests.append(b"")

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        self.sock.close()


@pytest.fixture(scope="module")
def gh() -> Path:
    path = Path(_GH)
    assert path.is_file(), _GH
    return path


@pytest.fixture(scope="module")
def installer() -> Path:
    path = Path(_INSTALLER)
    assert path.is_file(), _INSTALLER
    assert _NAME.fullmatch(path.name), f"keep the published file name: {path.name}"
    return path


@pytest.fixture(scope="module")
def digest(installer: Path) -> str:
    sha = hashlib.sha256()
    with installer.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            sha.update(chunk)
    return sha.hexdigest()


@pytest.fixture(scope="module")
def workdir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("live-verify")


@pytest.fixture(scope="module")
def bundle(digest: str, workdir: Path) -> Path:
    # Fetched unauthenticated, by Tether's own request.
    bundles = fetch_attestation_bundles(digest)
    path = workdir / "bundle.jsonl"
    update_verify._write_bundles(bundles, path)
    return path


@pytest.fixture(scope="module")
def trusted_root(gh: Path, workdir: Path) -> Path:
    if _ROOT_OVERRIDE:
        print("\nthe trusted root comes from TETHER_LIVE_TRUSTED_ROOT; this run did not fetch it")
        return Path(_ROOT_OVERRIDE)
    return fetch_trusted_root(gh, workdir)


@pytest.fixture
def recorder() -> Iterator[_Recorder]:
    rec = _Recorder()
    yield rec
    rec.close()


def _recording_runner(rec: _Recorder):
    """Run the real gh, but with every proxy variable pointed at the recorder."""

    def run(argv, env, timeout):  # noqa: ANN001, ANN202
        env = dict(env)
        for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
            env[name] = f"http://127.0.0.1:{rec.port}"
        return update_verify.run_gh(argv, env, timeout)

    return run


def test_the_real_gh_clears_the_floor(gh: Path, workdir: Path) -> None:
    assert check_gh_version(gh, workdir) >= update_verify.GH_MIN_VERSION


def test_a_the_verifier_layer_accepts_the_real_release_with_all_egress_blocked(
    gh: Path,
    installer: Path,
    digest: str,
    bundle: Path,
    trusted_root: Path,
    workdir: Path,
    recorder: _Recorder,
) -> None:
    facts = verify_provenance(
        installer,
        bundle,
        trusted_root,
        gh=gh,
        workdir=workdir,
        runner=_recording_runner(recorder),
    )
    version = _NAME.fullmatch(installer.name)["version"]
    assert facts, "gh verified nothing"
    for fact in facts:
        assert fact.source_ref == f"refs/tags/v{version}"
        assert fact.source_repository_uri == "https://github.com/bioedca/tether"
        assert (installer.name, digest) in fact.subjects
    assert recorder.requests == [], "the verify step reached for the network"


def test_b_the_channel_layer_refuses_that_same_release(
    gh: Path, installer: Path, bundle: Path, trusted_root: Path, workdir: Path
) -> None:
    facts = verify_provenance(installer, bundle, trusted_root, gh=gh, workdir=workdir)
    version = parse_version(_NAME.fullmatch(installer.name)["version"])
    assert version is not None
    for fact in facts:
        with pytest.raises(VerificationRefused, match="stable"):
            check_channel(fact, version)


def test_dropping_the_trusted_root_reintroduces_the_network(
    gh: Path, installer: Path, bundle: Path, workdir: Path, recorder: _Recorder
) -> None:
    env = update_verify._gh_env(workdir, offline=True)
    run = _recording_runner(recorder)(
        [
            str(gh),
            "attestation",
            "verify",
            str(installer),
            "--bundle",
            str(bundle),
            "--repo",
            update_verify.REPOSITORY,
            "--signer-workflow",
            update_verify.SIGNER_WORKFLOW,
        ],
        env,
        120.0,
    )
    assert run.returncode != 0
    assert recorder.requests, "without the root gh made no network attempt"
    assert all(r.startswith(b"CONNECT tuf-repo") for r in recorder.requests), recorder.requests


@pytest.mark.parametrize(
    ("constant", "value"),
    [
        ("REPOSITORY", "cli/cli"),
        ("SIGNER_WORKFLOW", "bioedca/tether/.github/workflows/ci.yml"),
    ],
)
def test_each_identity_flag_bites(
    gh: Path,
    installer: Path,
    bundle: Path,
    trusted_root: Path,
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
    constant: str,
    value: str,
) -> None:
    monkeypatch.setattr(update_verify, constant, value)
    with pytest.raises(VerificationRefused):
        verify_provenance(installer, bundle, trusted_root, gh=gh, workdir=workdir)


def test_the_source_ref_flag_bites(
    gh: Path, installer: Path, bundle: Path, trusted_root: Path, workdir: Path
) -> None:
    with pytest.raises(VerificationRefused):
        verify_provenance(
            installer, bundle, trusted_root, gh=gh, workdir=workdir, source_ref="refs/tags/v1.0.0"
        )


def test_a_tampered_artifact_is_refused(
    gh: Path, installer: Path, bundle: Path, trusted_root: Path, tmp_path: Path
) -> None:
    fake = tmp_path / installer.name
    fake.write_bytes(b"#!/bin/sh\necho tampered\n")
    with pytest.raises(VerificationRefused):
        verify_provenance(fake, bundle, trusted_root, gh=gh, workdir=tmp_path)


def test_a_bundle_from_another_release_is_refused(
    gh: Path, installer: Path, digest: str, trusted_root: Path, tmp_path: Path
) -> None:
    if digest == _OTHER_RELEASE_DIGEST:
        pytest.skip("the installer under test is the other release")
    other = tmp_path / "other.jsonl"
    update_verify._write_bundles(fetch_attestation_bundles(_OTHER_RELEASE_DIGEST), other)
    with pytest.raises(VerificationRefused):
        verify_provenance(installer, other, trusted_root, gh=gh, workdir=tmp_path)


def test_a_genuine_bundle_from_another_repository_is_refused(
    gh: Path, trusted_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if "=" not in _FOREIGN:
        pytest.skip("set TETHER_LIVE_FOREIGN=<file>=<owner/repo>")
    file_name, repo = _FOREIGN.rsplit("=", 1)
    artifact = Path(file_name)
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    monkeypatch.setattr(
        update_verify,
        "ATTESTATIONS_API",
        f"https://api.github.com/repos/{repo}/attestations/sha256:{{digest}}",
    )
    try:
        bundles = fetch_attestation_bundles(digest)
    except update_verify.VerificationUnavailable as error:
        pytest.skip(f"{repo}'s attestation could not be fetched here: {error}")
    monkeypatch.undo()
    foreign = tmp_path / "foreign.jsonl"
    update_verify._write_bundles(bundles, foreign)
    with pytest.raises(VerificationRefused):
        verify_provenance(artifact, foreign, trusted_root, gh=gh, workdir=tmp_path)
    # The control: the same bundle verifies under its own repository, so the refusal above
    # is the policy biting and not a broken bundle.
    env = update_verify._gh_env(tmp_path, offline=True)
    run: GhRun = update_verify.run_gh(
        [
            str(gh),
            "attestation",
            "verify",
            str(artifact),
            "--bundle",
            str(foreign),
            "--custom-trusted-root",
            str(trusted_root),
            "--repo",
            repo,
        ],
        env,
        120.0,
    )
    assert run.returncode == 0, run.stderr

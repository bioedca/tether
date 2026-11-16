# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The installer bundles a pinned, hash-checked ``gh`` as the update verifier (issue #332).

ADR-0060 picks ``gh attestation verify --bundle`` as the updater's verifier and ``extra_files``
as its delivery, following the single-source pin #306 set for the setuptools wheel. These tests
hold that arrangement in place:

- ``packaging/gh-verifier.toml`` is the one source of the version and the four archive digests,
  and the version clears both advisory floors;
- ``packaging/scripts/stage_gh.py``, its reader, refuses a wrong digest, a wrong version and a
  malformed archive, and stages nothing when it refuses;
- the recipe, both build drivers, ``post_install.sh`` and the install-smoke all name the same
  staged files and the same in-prefix folder that :mod:`tether._verifier` derives, and none of
  them restates the pin;
- ``conda-lock.yml`` carries no ``gh``.

The download itself and the real binary run only on the packaging runners (``packaging.yml``);
here the archive is a small stand-in and ``gh`` is a fake runner.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import os
import re
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from tether import _verifier

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "packaging" / "scripts" / "stage_gh.py"
_PIN = _ROOT / "packaging" / "gh-verifier.toml"
_RECIPE = _ROOT / "packaging" / "construct.yaml"
_POST_INSTALL = _ROOT / "packaging" / "scripts" / "post_install.sh"
_SMOKE = _ROOT / "packaging" / "scripts" / "install_smoke.sh"
_WORKFLOWS = (
    _ROOT / ".github" / "workflows" / "packaging.yml",
    _ROOT / ".github" / "workflows" / "release.yml",
)
_STAGE_DEST = "packaging/staging/gh"

_spec = importlib.util.spec_from_file_location("tether_stage_gh", _SCRIPT)
stage_gh = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = stage_gh
_spec.loader.exec_module(stage_gh)

PINS = stage_gh.load_pins()
VERSION = PINS["linux-64"].version
NOTICES = "third-party notice\n" * 100


def _fake_gh(version: str = VERSION):
    """A runner standing in for the staged binary: ``--version`` and ``licenses``."""

    calls: list[list[str]] = []

    def run(argv):
        calls.append(list(argv))
        if argv[1:] == ["--version"]:
            return f"gh version {version} (2026-11-12)\nhttps://github.com/cli/cli/releases/tag/v{version}\n"
        if argv[1:] == ["licenses"]:
            return NOTICES
        raise AssertionError(f"unexpected gh call: {argv}")

    run.calls = calls
    return run


def _tar_gz(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buf.getvalue()


def _pins_for(platform: str, data: bytes) -> dict:
    """The real pin for ``platform`` with its digest replaced by the stand-in archive's."""
    real = PINS[platform]
    return {
        platform: stage_gh.Pin(real.version, platform, real.asset, hashlib.sha256(data).hexdigest())
    }


# --- the pin -------------------------------------------------------------------------------


def test_the_pin_names_the_four_installer_platforms_with_a_digest_each() -> None:
    assert sorted(PINS) == sorted(("linux-64", "osx-64", "osx-arm64", "win-64"))
    for platform, pin in PINS.items():
        assert re.fullmatch(r"[0-9a-f]{64}", pin.sha256), platform
        assert pin.archive_name == f"gh_{VERSION}_{pin.asset}"
        assert pin.url == (
            f"https://github.com/cli/cli/releases/download/v{VERSION}/gh_{VERSION}_{pin.asset}"
        )
    assert len({pin.sha256 for pin in PINS.values()}) == 4, "two platforms share one digest"
    assert PINS["win-64"].binary_name == "gh.exe"
    assert PINS["linux-64"].binary_name == "gh"


def test_the_pinned_version_clears_both_advisory_floors() -> None:
    """2.67.0 is the hard floor (CVE-2025-25204, fail-open verify); > 2.92.0 clears CVE-2026-48501.

    The two bounds are literals here on purpose: a test that read them from the script would
    pass if the script's own constants were lowered.
    """
    version = stage_gh.version_tuple(VERSION)
    assert version >= (2, 67, 0)
    assert version > (2, 92, 0)


@pytest.mark.parametrize("version", ["2.49.0", "2.66.9", "2.67.0", "2.92.0", "1.0.0"])
def test_a_version_an_advisory_affects_is_refused(version: str) -> None:
    with pytest.raises(stage_gh.StageError):
        stage_gh.check_floor(version)


@pytest.mark.parametrize("version", ["2.92.1", "2.104.0", "3.0.0"])
def test_a_version_past_both_advisories_is_accepted(version: str) -> None:
    stage_gh.check_floor(version)


@pytest.mark.parametrize("version", ["2.104", "v2.104.0", "2.104.0-rc1", ""])
def test_a_version_not_major_minor_patch_is_refused_not_guessed(version: str) -> None:
    with pytest.raises(stage_gh.StageError):
        stage_gh.version_tuple(version)


def test_a_pin_file_with_a_bad_digest_or_a_missing_platform_is_refused(tmp_path: Path) -> None:
    text = _PIN.read_text(encoding="utf-8")
    bad_digest = tmp_path / "bad.toml"
    bad_digest.write_text(text.replace(PINS["osx-64"].sha256, "0" * 63), encoding="utf-8")
    with pytest.raises(stage_gh.StageError, match="osx-64"):
        stage_gh.load_pins(bad_digest)

    missing = tmp_path / "missing.toml"
    missing.write_text(text.split("[platforms.win-64]")[0], encoding="utf-8")
    with pytest.raises(stage_gh.StageError, match="exactly"):
        stage_gh.load_pins(missing)

    low = tmp_path / "low.toml"
    low.write_text(text.replace(f'version = "{VERSION}"', 'version = "2.66.0"'), encoding="utf-8")
    with pytest.raises(stage_gh.StageError, match="2.67.0"):
        stage_gh.load_pins(low)


def test_print_version_reads_the_pin(capsys: pytest.CaptureFixture[str]) -> None:
    assert stage_gh.main(["print-version"]) == 0
    assert capsys.readouterr().out.strip() == VERSION


# --- staging -------------------------------------------------------------------------------


def test_a_wrapped_tar_gz_archive_is_staged_with_its_license_and_notices(tmp_path: Path) -> None:
    """The linux and macOS archives wrap their contents in a gh_<version>_<os> folder."""
    top = f"gh_{VERSION}_linux_amd64"
    data = _tar_gz(
        {
            f"{top}/bin/gh": b"\x7fELF binary",
            f"{top}/LICENSE": b"MIT License",
            f"{top}/share/man/man1/gh.1": b"man page",
        }
    )
    dest = tmp_path / "gh"
    runner = _fake_gh()
    binary = stage_gh.stage(
        "linux-64", dest, pins=_pins_for("linux-64", data), fetch=lambda url: data, run=runner
    )
    assert binary == dest / "gh"
    assert sorted(p.name for p in dest.iterdir()) == ["LICENSE", "THIRD-PARTY-LICENSES.txt", "gh"]
    assert binary.read_bytes() == b"\x7fELF binary"
    assert (dest / "LICENSE").read_bytes() == b"MIT License"
    assert (dest / "THIRD-PARTY-LICENSES.txt").read_text(encoding="utf-8") == NOTICES
    assert [call[1:] for call in runner.calls] == [["--version"], ["licenses"]]
    if os.name != "nt":
        assert binary.stat().st_mode & 0o111, "the staged gh is not executable"


def test_an_unwrapped_windows_zip_is_staged(tmp_path: Path) -> None:
    """The Windows archive has no top-level folder (goreleaser `wrap_in_directory: false`)."""
    data = _zip({"bin/gh.exe": b"MZ binary", "LICENSE": b"MIT License"})
    dest = tmp_path / "gh"
    binary = stage_gh.stage(
        "win-64", dest, pins=_pins_for("win-64", data), fetch=lambda url: data, run=_fake_gh()
    )
    assert binary == dest / "gh.exe"
    assert sorted(p.name for p in dest.iterdir()) == [
        "LICENSE",
        "THIRD-PARTY-LICENSES.txt",
        "gh.exe",
    ]


def test_the_download_is_fetched_from_the_pinned_upstream_url(tmp_path: Path) -> None:
    data = _zip({"gh_x/bin/gh": b"bin", "gh_x/LICENSE": b"MIT"})
    seen: list[str] = []
    pins = _pins_for("osx-arm64", data)
    stage_gh.stage(
        "osx-arm64",
        tmp_path / "gh",
        pins=pins,
        fetch=lambda url: seen.append(url) or data,
        run=_fake_gh(),
    )
    assert seen == [pins["osx-arm64"].url]


def test_a_digest_mismatch_is_refused_before_anything_is_extracted(tmp_path: Path) -> None:
    data = _tar_gz({"gh/bin/gh": b"bin", "gh/LICENSE": b"MIT"})
    dest = tmp_path / "gh"
    dest.mkdir()
    (dest / "stale").write_text("left by an earlier run", encoding="utf-8")
    runner = _fake_gh()
    with pytest.raises(stage_gh.StageError, match="does not match"):
        stage_gh.stage("linux-64", dest, pins=PINS, fetch=lambda url: data, run=runner)
    assert runner.calls == [], "a binary that failed its digest was run"
    assert not (dest / "gh").exists()


def test_a_binary_reporting_another_version_is_refused_and_nothing_stays_staged(
    tmp_path: Path,
) -> None:
    data = _tar_gz({"gh/bin/gh": b"bin", "gh/LICENSE": b"MIT"})
    dest = tmp_path / "gh"
    with pytest.raises(stage_gh.StageError, match="reports"):
        stage_gh.stage(
            "linux-64",
            dest,
            pins=_pins_for("linux-64", data),
            fetch=lambda url: data,
            run=_fake_gh("2.66.0"),
        )
    assert not dest.exists()


@pytest.mark.parametrize(
    "files",
    [
        {"gh/LICENSE": b"MIT"},
        {"gh/bin/gh": b"bin"},
        {"a/bin/gh": b"one", "b/bin/gh": b"two", "a/LICENSE": b"MIT"},
        {"gh/bin/sub/bin/gh": b"deep", "gh/LICENSE": b"MIT"},
    ],
    ids=["no-binary", "no-license", "two-binaries", "binary-too-deep"],
)
def test_an_archive_without_exactly_one_binary_and_license_is_refused(
    tmp_path: Path, files: dict[str, bytes]
) -> None:
    data = _tar_gz(files)
    dest = tmp_path / "gh"
    with pytest.raises(stage_gh.StageError, match="exactly one"):
        stage_gh.stage(
            "linux-64",
            dest,
            pins=_pins_for("linux-64", data),
            fetch=lambda url: data,
            run=_fake_gh(),
        )
    assert not dest.exists()


def test_empty_license_notices_are_refused(tmp_path: Path) -> None:
    data = _tar_gz({"gh/bin/gh": b"bin", "gh/LICENSE": b"MIT"})

    def run(argv):
        return f"gh version {VERSION} (x)\n" if argv[1:] == ["--version"] else ""

    with pytest.raises(stage_gh.StageError, match="licenses"):
        stage_gh.stage(
            "linux-64",
            tmp_path / "gh",
            pins=_pins_for("linux-64", data),
            fetch=lambda url: data,
            run=run,
        )


# --- the in-prefix path the app derives ----------------------------------------------------


def _installed_layout(root: Path) -> Path:
    """``<root>/envs/tether`` beside ``<root>/verifier/gh/<gh>``; return the app env."""
    env = root / "envs" / "tether"
    env.mkdir(parents=True)
    gh = root / "verifier" / "gh" / ("gh.exe" if os.name == "nt" else "gh")
    gh.parent.mkdir(parents=True)
    gh.write_bytes(b"bin")
    return env


def test_the_app_finds_the_bundled_gh_from_its_own_prefix(tmp_path: Path) -> None:
    env = _installed_layout(tmp_path)
    assert _verifier.install_prefix(env) == tmp_path
    found = _verifier.bundled_gh(env)
    assert found is not None
    assert found.parent == tmp_path / "verifier" / "gh"


def test_no_bundled_gh_outside_an_installed_copy(tmp_path: Path) -> None:
    env = tmp_path / "envs" / "tether"
    env.mkdir(parents=True)
    assert _verifier.bundled_gh(env) is None


def test_the_default_prefix_is_the_running_interpreter(monkeypatch, tmp_path: Path) -> None:
    env = _installed_layout(tmp_path)
    monkeypatch.setattr(sys, "prefix", str(env))
    assert _verifier.bundled_gh() is not None


# --- every consumer names the same files, and none restates the pin ------------------------


def _extra_files() -> str:
    recipe = _RECIPE.read_text(encoding="utf-8")
    return recipe.split("\nextra_files:", 1)[1].split("\nlicense_file:", 1)[0]


def test_the_recipe_ships_the_staged_files_into_the_folder_the_app_derives() -> None:
    entries = re.findall(
        r"^\s*-\s*(staging/gh/[\w.-]+):\s*([\w./-]+)\s*(?:#\s*\[(\w+)\])?\s*$",
        _extra_files(),
        flags=re.MULTILINE,
    )
    mapping = {(src, selector or "any"): dest for src, dest, selector in entries}
    folder = _verifier.GH_VERIFIER_DIR
    assert mapping == {
        ("staging/gh/gh", "unix"): f"{folder}/gh",
        ("staging/gh/gh.exe", "win"): f"{folder}/gh.exe",
        ("staging/gh/LICENSE", "any"): f"{folder}/LICENSE",
        ("staging/gh/THIRD-PARTY-LICENSES.txt", "any"): f"{folder}/THIRD-PARTY-LICENSES.txt",
    }
    # Not a folder the install guide asks the user to put on PATH (ADR-0051 shims).
    assert not folder.startswith(("bin", "launchers", "envs")), folder


@pytest.mark.parametrize("workflow", _WORKFLOWS, ids=lambda p: p.name)
def test_both_build_drivers_stage_gh_into_the_recipe_source_before_constructor(
    workflow: Path,
) -> None:
    text = workflow.read_text(encoding="utf-8")
    call = f'python packaging/scripts/stage_gh.py stage --platform "$PLATFORM" --dest {_STAGE_DEST}'
    assert text.count(call) == 1, f"{workflow.name} must stage gh exactly once with: {call}"
    assert text.index(call) < text.index("constructor --output-dir"), (
        f"{workflow.name} stages gh after constructor runs"
    )
    # The recipe's sources are relative to packaging/, so staging/gh is packaging/staging/gh.
    assert "staging/gh/" in _extra_files() and _STAGE_DEST == "packaging/staging/gh"


def test_post_install_makes_the_shipped_binary_executable() -> None:
    text = _POST_INSTALL.read_text(encoding="utf-8")
    assert f'chmod 755 "$PREFIX/{_verifier.GH_VERIFIER_DIR}/gh"' in text


def test_the_install_smoke_runs_the_bundled_gh_on_every_platform() -> None:
    text = _SMOKE.read_text(encoding="utf-8")
    assert "from tether._verifier import bundled_gh" in text
    assert "stage_gh.py print-version" in text
    calls = re.findall(r"^\s*check_gh_verifier \"(.+)\"$", text, flags=re.MULTILINE)
    assert calls == ["$prefix\\\\envs\\\\tether\\\\python.exe", "$prefix/envs/tether/bin/python"], (
        "the install-smoke must check the verifier in both the Windows and the Unix branch"
    )


@pytest.mark.parametrize(
    "path",
    [
        _RECIPE,
        _POST_INSTALL,
        _ROOT / "packaging" / "scripts" / "post_install.bat",
        _SMOKE,
        _ROOT / "packaging" / "README.md",
        _ROOT / "docs" / "maintainers" / "building-installers.md",
        *_WORKFLOWS,
    ],
    ids=lambda p: p.name,
)
def test_no_consumer_restates_the_version_or_a_digest(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert VERSION not in text, f"{path.name} restates the gh version; read gh-verifier.toml"
    for pin in PINS.values():
        assert pin.sha256 not in text, f"{path.name} restates the {pin.platform} digest"


def test_conda_lock_carries_no_gh() -> None:
    """The verifier ships through extra_files; a `gh` entry in the lock means the wrong route."""
    for lock in ("conda-lock.yml", "sidecar/conda-lock.yml"):
        text = (_ROOT / lock).read_text(encoding="utf-8")
        assert not re.search(r"^- name: gh$", text, flags=re.MULTILINE), lock

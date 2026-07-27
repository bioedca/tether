# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Behavioral contract for the reviewed GitHub-wiki index publisher."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "wiki" / "Home.md"
WORKFLOW = ROOT / ".github" / "workflows" / "wiki.yml"
PUBLISHER = ROOT / "scripts" / "publish_wiki.py"

_SPEC = importlib.util.spec_from_file_location("tether_wiki_publish", PUBLISHER)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load wiki publisher from {PUBLISHER}")
publish_wiki = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = publish_wiki
_SPEC.loader.exec_module(publish_wiki)


def _git(*args: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _bare_remote(tmp_path: Path) -> Path:
    remote = tmp_path / "wiki.git"
    _git("init", "--bare", str(remote))
    return remote


def _seed_remote(remote: Path, tmp_path: Path) -> str:
    seed = tmp_path / "seed"
    _git("init", str(seed))
    _git("config", "user.name", "Wiki test", cwd=seed)
    _git("config", "user.email", "wiki-test@example.invalid", cwd=seed)
    (seed / "Home.md").write_text("# Old home\n", encoding="utf-8")
    (seed / "Stale.md").write_text("# Stale page\n", encoding="utf-8")
    _git("add", "--all", cwd=seed)
    _git("commit", "-m", "docs: seed wiki", cwd=seed)
    previous = _git("rev-parse", "HEAD", cwd=seed)
    _git("push", str(remote), "HEAD:refs/heads/master", cwd=seed)
    return previous


def _remote_files(remote: Path) -> list[str]:
    return _git(
        f"--git-dir={remote}",
        "ls-tree",
        "-r",
        "--name-only",
        "refs/heads/master",
    ).splitlines()


def _remote_home(remote: Path) -> str:
    return _git(
        f"--git-dir={remote}",
        "show",
        "refs/heads/master:Home.md",
    )


def test_checked_in_home_is_a_thin_canonical_index() -> None:
    manifest = publish_wiki.validate_source(ROOT / "wiki")

    assert manifest.files == ("Home.md",)
    assert manifest.links == publish_wiki.REQUIRED_LINKS
    assert manifest.visible_word_count <= publish_wiki.MAX_VISIBLE_WORDS
    assert "/dev/" not in HOME.read_text(encoding="utf-8")


def test_issue_owned_wiki_routes_are_backed_by_mkdocs_pages() -> None:
    expected_pages = {
        "Install": "packaging.md",
        "CLI reference": "cli.md",
        "Citing": "citing.md",
        "Architecture decisions": "adr/README.md",
    }
    config = yaml.safe_load((ROOT / "mkdocs.yml").read_text(encoding="utf-8"))
    rendered_nav = str(config["nav"])

    for label, relative_path in expected_pages.items():
        assert (ROOT / "docs" / relative_path).is_file(), f"{label} target is missing"
        assert relative_path in rendered_nav, f"{label} target is not in the MkDocs nav"


@pytest.mark.parametrize(
    ("injected", "message"),
    [
        ('<a href="https://example.invalid/">extra</a>', "raw HTML"),
        ("<https://example.invalid/>", "autolinks"),
        ("https://example.invalid/", "bare URLs"),
    ],
)
def test_home_rejects_unsupported_link_forms(
    tmp_path: Path,
    injected: str,
    message: str,
) -> None:
    source = tmp_path / "wiki"
    source.mkdir()
    text = HOME.read_text(encoding="utf-8")
    (source / "Home.md").write_text(
        text.replace("Tether is", f"{injected} Tether is", 1),
        encoding="utf-8",
    )

    with pytest.raises(publish_wiki.WikiPublishError, match=message):
        publish_wiki.validate_source(source)


def test_home_rejects_dev_links_and_substantive_growth(tmp_path: Path) -> None:
    source = tmp_path / "wiki"
    source.mkdir()
    text = HOME.read_text(encoding="utf-8")
    (source / "Home.md").write_text(
        text.replace("/tether/latest/", "/tether/dev/", 1),
        encoding="utf-8",
    )
    with pytest.raises(publish_wiki.WikiPublishError, match="dev"):
        publish_wiki.validate_source(source)

    (source / "Home.md").write_text(
        text + "\n\n" + "substantive " * (publish_wiki.MAX_VISIBLE_WORDS + 1),
        encoding="utf-8",
    )
    with pytest.raises(publish_wiki.WikiPublishError, match="one-screen"):
        publish_wiki.validate_source(source)


def test_source_rejects_any_second_wiki_page(tmp_path: Path) -> None:
    source = tmp_path / "wiki"
    source.mkdir()
    (source / "Home.md").write_bytes(HOME.read_bytes())
    (source / "Extra.md").write_text("# Extra\n", encoding="utf-8")

    with pytest.raises(publish_wiki.WikiPublishError, match="exactly Home.md"):
        publish_wiki.validate_source(source)


def test_source_rejects_a_symlink_root_before_resolving(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "wiki"
    source.mkdir()
    (source / "Home.md").write_bytes(HOME.read_bytes())
    original_is_symlink = Path.is_symlink
    original_resolve = Path.resolve

    def fake_is_symlink(path: Path) -> bool:
        return path == source or original_is_symlink(path)

    def fail_if_source_is_resolved(path: Path, *, strict: bool = False) -> Path:
        if path == source:
            raise AssertionError("source root was resolved before its symlink check")
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)
    monkeypatch.setattr(Path, "resolve", fail_if_source_is_resolved)

    with pytest.raises(publish_wiki.WikiPublishError, match="real directory"):
        publish_wiki.validate_source(source)


def test_first_publish_creates_only_home_on_a_reachable_empty_remote(tmp_path: Path) -> None:
    remote = _bare_remote(tmp_path)

    result = publish_wiki.mirror_wiki(
        source=ROOT / "wiki",
        remote=str(remote),
        source_sha="a" * 40,
        publish=True,
    )

    assert result.previous_sha is None
    assert result.changed is True
    assert result.dry_run_verified is True
    assert result.published is True
    assert _remote_files(remote) == ["Home.md"]
    assert _remote_home(remote) == HOME.read_text(encoding="utf-8").rstrip("\n")


def test_existing_wiki_is_fast_forward_mirrored_and_stale_pages_are_removed(
    tmp_path: Path,
) -> None:
    remote = _bare_remote(tmp_path)
    previous = _seed_remote(remote, tmp_path)

    result = publish_wiki.mirror_wiki(
        source=ROOT / "wiki",
        remote=str(remote),
        source_sha="b" * 40,
        publish=True,
    )

    assert result.previous_sha == previous
    assert result.published is True
    assert _remote_files(remote) == ["Home.md"]
    assert _remote_home(remote) == HOME.read_text(encoding="utf-8").rstrip("\n")
    parents = _git(
        f"--git-dir={remote}",
        "rev-list",
        "--parents",
        "-n",
        "1",
        "refs/heads/master",
    ).split()
    assert parents == [result.candidate_sha, previous]


def test_dry_run_proves_push_without_changing_remote(tmp_path: Path) -> None:
    remote = _bare_remote(tmp_path)
    previous = _seed_remote(remote, tmp_path)

    result = publish_wiki.mirror_wiki(
        source=ROOT / "wiki",
        remote=str(remote),
        source_sha="c" * 40,
        publish=False,
    )

    assert result.changed is True
    assert result.dry_run_verified is True
    assert result.published is False
    assert _git(f"--git-dir={remote}", "rev-parse", "refs/heads/master") == previous
    assert set(_remote_files(remote)) == {"Home.md", "Stale.md"}


def test_remote_uncertainty_fails_closed_without_creating_a_target(tmp_path: Path) -> None:
    missing = tmp_path / "missing" / "wiki.git"

    with pytest.raises(publish_wiki.WikiPublishError, match="inspect wiki remote"):
        publish_wiki.mirror_wiki(
            source=ROOT / "wiki",
            remote=str(missing),
            source_sha="d" * 40,
            publish=True,
        )

    assert not missing.exists()


def test_workflow_is_main_only_least_privilege_and_reviewable() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    config = yaml.load(text, Loader=yaml.BaseLoader)
    triggers = config["on"]
    job = config["jobs"]["publish"]

    assert set(triggers) == {"push", "workflow_dispatch"}
    assert triggers["push"]["branches"] == ["main"]
    assert set(triggers["push"]["paths"]) == {
        ".github/workflows/wiki.yml",
        "scripts/publish_wiki.py",
        "wiki/**",
    }
    assert triggers["workflow_dispatch"]["inputs"]["publish"]["default"] == "false"
    assert config["permissions"] == {"contents": "read"}
    assert job["permissions"] == {"contents": "write"}
    assert job["timeout-minutes"] == "5"
    assert config["concurrency"]["cancel-in-progress"] == "false"
    assert "github.ref == 'refs/heads/main'" in job["if"]
    assert "github.repository == 'bioedca/tether'" in job["if"]
    assert "pull_request" not in triggers
    assert "merge_group" not in triggers
    assert "workflow_run" not in triggers

    assert "actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0" in text, (
        "use the repository's reviewed checkout SHA pin"
    )
    assert "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1" in text
    assert 'python-version: "3.12"' in text
    assert "persist-credentials: false" in text
    assert "GH_TOKEN: ${{ github.token }}" in text
    assert "WIKI_TOKEN: ${{ github.token }}" in text
    assert "secrets." not in text
    assert ".has_wiki" in text
    assert "--allow-uninitialized" not in text
    assert "scripts/publish_wiki.py" in text
    assert "git push" not in text, "transport belongs in the tested publisher, not YAML"
    assert "--force" not in PUBLISHER.read_text(encoding="utf-8")

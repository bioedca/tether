# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Validate and fast-forward mirror Tether's reviewed wiki index.

Authentication is deliberately outside this module.  The workflow supplies Git with a
short-lived ``GITHUB_TOKEN`` through ``GIT_ASKPASS``; this script never accepts a credential on
the command line or writes one into Git configuration.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

WIKI_REF = "refs/heads/master"
MAX_VISIBLE_WORDS = 110
DOCS_ROOT = "https://bioedca.github.io/tether/"
CANONICAL_ROOT = "https://bioedca.github.io/tether/latest/"
REQUIRED_LINKS = {
    "canonical documentation": DOCS_ROOT,
    "version selector": CANONICAL_ROOT,
    "Install": f"{CANONICAL_ROOT}packaging/",
    "Tutorial": f"{CANONICAL_ROOT}tutorial/",
    "CLI reference": f"{CANONICAL_ROOT}cli/",
    "Citing": f"{CANONICAL_ROOT}citing/",
    "Architecture decisions": f"{CANONICAL_ROOT}adr/",
}

_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")
_WORD_RE = re.compile(r"\b[\w\u2019'-]+\b", re.UNICODE)
_SHA_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
_REPOSITORY_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_EMBEDDED_CREDENTIAL_RE = re.compile(r"https?://[^/\s]*@")


class WikiPublishError(RuntimeError):
    """A validation or transport failure that must stop publication."""


@dataclass(frozen=True)
class WikiManifest:
    """Validated, reviewable wiki-source properties."""

    files: tuple[str, ...]
    links: dict[str, str]
    visible_word_count: int


@dataclass(frozen=True)
class PublishResult:
    """Token-free evidence emitted by one mirror attempt."""

    previous_sha: str | None
    candidate_sha: str
    changed: bool
    dry_run_verified: bool
    published: bool


def _without_html_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)


def _visible_word_count(text: str) -> int:
    visible = _without_html_comments(text)
    visible = _LINK_RE.sub(lambda match: match.group(1), visible)
    visible = re.sub(r"[#>*_`|~-]", " ", visible)
    return len(_WORD_RE.findall(visible))


def _plain_paragraphs(text: str) -> list[str]:
    visible = _without_html_comments(text)
    paragraphs = re.split(r"\n\s*\n", visible)
    return [
        " ".join(line.strip() for line in paragraph.splitlines())
        for paragraph in paragraphs
        if paragraph.strip()
        and not paragraph.lstrip().startswith(("#", ">", "-", "*"))
    ]


def validate_source(source: Path) -> WikiManifest:
    """Return the manifest for a one-page, canonical-only wiki source.

    The intentionally narrow grammar makes "thin index" executable rather than a review-time
    opinion: one regular ``Home.md``, one top warning, one two-sentence orientation paragraph,
    five section links, no extra links or rich content, and a one-screen word ceiling.
    """

    try:
        source = source.resolve(strict=True)
    except FileNotFoundError as exc:
        raise WikiPublishError(f"wiki source does not exist: {source}") from exc
    if not source.is_dir() or source.is_symlink():
        raise WikiPublishError(f"wiki source must be a real directory: {source}")

    entries = sorted(source.rglob("*"))
    if any(entry.is_symlink() for entry in entries):
        raise WikiPublishError("wiki source must not contain symbolic links")
    files = tuple(entry.relative_to(source).as_posix() for entry in entries if entry.is_file())
    directories = [entry for entry in entries if entry.is_dir()]
    if files != ("Home.md",) or directories:
        raise WikiPublishError(
            f"wiki source must contain exactly Home.md and no subdirectories; found {files}"
        )

    home = source / "Home.md"
    try:
        text = home.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise WikiPublishError("Home.md must be valid UTF-8") from exc

    visible = _without_html_comments(text)
    first_visible = next((line.strip() for line in visible.splitlines() if line.strip()), "")
    if not first_visible.startswith("> **Index only:**"):
        raise WikiPublishError("Home.md must begin visibly with the one-line 'Index only' warning")
    if first_visible.count("\n"):
        raise WikiPublishError("the index-only warning must stay on one line")
    if "/dev/" in text.casefold():
        raise WikiPublishError("Home.md must not link to a dev documentation tree")
    if any(marker in visible for marker in ("```", "![", "\n## ", "\n| ")):
        raise WikiPublishError(
            "Home.md must remain an index without code, images, tables, or sections"
        )

    links = _LINK_RE.findall(text)
    if len(links) != len(REQUIRED_LINKS):
        raise WikiPublishError(
            f"Home.md must contain exactly the reviewed links {tuple(REQUIRED_LINKS)}"
        )
    link_map = dict(links)
    if len(link_map) != len(links) or link_map != REQUIRED_LINKS:
        raise WikiPublishError(
            f"Home.md links must match the canonical index exactly; found {link_map}"
        )
    if any(
        target != DOCS_ROOT and not target.startswith(CANONICAL_ROOT)
        for target in link_map.values()
    ):
        raise WikiPublishError("every wiki link must target the docs root or canonical latest tree")

    word_count = _visible_word_count(text)
    if word_count > MAX_VISIBLE_WORDS:
        raise WikiPublishError(
            f"Home.md exceeds the one-screen {MAX_VISIBLE_WORDS}-word ceiling ({word_count})"
        )

    paragraphs = _plain_paragraphs(text)
    if len(paragraphs) != 2:
        raise WikiPublishError(
            "Home.md must have one orientation paragraph and one version-selector sentence"
        )
    sentence_count = len(re.findall(r"[.!?](?:\s|$)", paragraphs[0]))
    if sentence_count != 2 or "Tether" not in paragraphs[0] or "smFRET" not in paragraphs[0]:
        raise WikiPublishError(
            "the orientation paragraph must describe Tether in exactly two sentences"
        )
    if "[version selector]" not in paragraphs[1]:
        raise WikiPublishError("Home.md must explicitly link the docs site's version selector")

    list_items = [line for line in visible.splitlines() if line.startswith("- ")]
    if len(list_items) != 5:
        raise WikiPublishError("Home.md must keep exactly the five approved section links")

    return WikiManifest(files=files, links=link_map, visible_word_count=word_count)


def _git(
    arguments: Sequence[str],
    *,
    cwd: Path,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        check=False,
    )
    if check and completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()
        if len(detail) > 2_000:
            detail = detail[-2_000:]
        raise WikiPublishError(f"git {' '.join(arguments[:2])} failed: {detail}")
    return completed


def _remote_head(repo: Path) -> str | None:
    result = _git(
        ["ls-remote", "--exit-code", "--heads", "wiki", WIKI_REF],
        cwd=repo,
        check=False,
    )
    if result.returncode == 0:
        records = [line.split() for line in result.stdout.splitlines() if line.strip()]
        if len(records) != 1 or len(records[0]) != 2 or records[0][1] != WIKI_REF:
            raise WikiPublishError(f"wiki remote returned an ambiguous {WIKI_REF} record")
        sha = records[0][0]
        if not _SHA_RE.fullmatch(sha):
            raise WikiPublishError(f"wiki remote returned an invalid object id: {sha!r}")
        return sha
    if result.returncode == 2 and not result.stdout.strip():
        # The receive endpoint is reachable and authenticated, but the target ref is absent.
        # This is a provable empty-remote state, unlike GitHub's 404 for a wiki that has never
        # had a page. The mandatory receive-path dry run still precedes any real update.
        return None

    detail = (result.stderr or result.stdout).strip()
    raise WikiPublishError(f"cannot inspect wiki remote; refusing to publish: {detail}")


def _clear_worktree(repo: Path) -> None:
    for entry in repo.iterdir():
        if entry.name == ".git":
            continue
        if entry.is_symlink() or entry.is_file():
            entry.unlink()
        elif entry.is_dir():
            shutil.rmtree(entry)
        else:
            raise WikiPublishError(f"unexpected temporary worktree entry: {entry}")


def _copy_reviewed_source(source: Path, repo: Path) -> None:
    _clear_worktree(repo)
    shutil.copyfile(source / "Home.md", repo / "Home.md", follow_symlinks=False)
    if sorted(path.name for path in repo.iterdir() if path.name != ".git") != ["Home.md"]:
        raise WikiPublishError("temporary wiki tree is not an exact one-page mirror")
    if (repo / "Home.md").read_bytes() != (source / "Home.md").read_bytes():
        raise WikiPublishError("temporary Home.md differs from reviewed source bytes")


def mirror_wiki(
    *,
    source: Path,
    remote: str,
    source_sha: str,
    publish: bool,
) -> PublishResult:
    """Prepare, dry-run, and optionally publish one fast-forward wiki mirror commit."""

    manifest = validate_source(source)
    if manifest.files != ("Home.md",):  # defensive: keep the transport bound to the validator
        raise WikiPublishError("validated manifest is not the one-page publishing contract")
    if not _SHA_RE.fullmatch(source_sha):
        raise WikiPublishError("source SHA must be a full 40- or 64-character lowercase object id")
    if not remote or "\n" in remote or "\r" in remote:
        raise WikiPublishError("wiki remote must be one non-empty line")
    if _EMBEDDED_CREDENTIAL_RE.search(remote):
        raise WikiPublishError("wiki remote must not embed credentials")

    with tempfile.TemporaryDirectory(prefix="tether-wiki-") as temporary:
        repo = Path(temporary)
        _git(["init", "--quiet"], cwd=repo)
        _git(["config", "user.name", "github-actions[bot]"], cwd=repo)
        _git(
            [
                "config",
                "user.email",
                "41898282+github-actions[bot]@users.noreply.github.com",
            ],
            cwd=repo,
        )
        _git(["remote", "add", "wiki", remote], cwd=repo)

        previous = _remote_head(repo)
        if previous is None:
            _git(["checkout", "--quiet", "--orphan", "wiki-sync"], cwd=repo)
        else:
            _git(["fetch", "--quiet", "--depth=1", "wiki", WIKI_REF], cwd=repo)
            _git(["checkout", "--quiet", "-B", "wiki-sync", "FETCH_HEAD"], cwd=repo)

        _copy_reviewed_source(source.resolve(), repo)
        _git(["add", "--all"], cwd=repo)
        staged = _git(["diff", "--cached", "--quiet"], cwd=repo, check=False)
        if staged.returncode == 0:
            if previous is None:
                raise WikiPublishError("uninitialized wiki unexpectedly produced no staged content")
            candidate = _git(["rev-parse", "HEAD"], cwd=repo).stdout.strip()
            return PublishResult(
                previous_sha=previous,
                candidate_sha=candidate,
                changed=False,
                dry_run_verified=False,
                published=False,
            )
        if staged.returncode != 1:
            raise WikiPublishError("cannot inspect staged wiki mirror")

        _git(
            [
                "commit",
                "--quiet",
                "-m",
                f"docs: sync wiki index from {source_sha[:12]}",
            ],
            cwd=repo,
        )
        candidate = _git(["rev-parse", "HEAD"], cwd=repo).stdout.strip()
        if not _SHA_RE.fullmatch(candidate):
            raise WikiPublishError(
                f"publisher created an invalid candidate object id: {candidate!r}"
            )

        _git(
            ["push", "--porcelain", "--dry-run", "wiki", f"HEAD:{WIKI_REF}"],
            cwd=repo,
        )
        if publish:
            _git(["push", "--porcelain", "wiki", f"HEAD:{WIKI_REF}"], cwd=repo)
            published = _remote_head(repo)
            if published != candidate:
                raise WikiPublishError(
                    f"wiki ref verification mismatch: expected {candidate}, found {published}"
                )

        return PublishResult(
            previous_sha=previous,
            candidate_sha=candidate,
            changed=True,
            dry_run_verified=True,
            published=publish,
        )


def _remote_for_repository(repository: str) -> str:
    if not _REPOSITORY_RE.fullmatch(repository):
        raise WikiPublishError("repository must be an exact owner/name slug")
    return f"https://github.com/{repository}.wiki.git"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("wiki"))
    parser.add_argument("--repository", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument(
        "--publish",
        action="store_true",
        help="perform the verified fast-forward push after the mandatory dry run",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = mirror_wiki(
            source=args.source,
            remote=_remote_for_repository(args.repository),
            source_sha=args.source_sha,
            publish=args.publish,
        )
    except WikiPublishError as exc:
        print(f"::error::wiki publish failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(asdict(result), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

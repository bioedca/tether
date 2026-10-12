# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The release-notes generator, run for real against a throwaway git history (#182, ADR-0067).

The GitHub Release body is Tether's only change record, so the script that writes it is tested
as code rather than read as YAML. Each test extracts the ``run:`` script of ``release.yml``'s
changelog step -- the exact text the runner executes, so the workflow and this test cannot drift
apart -- and runs it under bash in a scratch repository whose tags mirror the real sequence: a
stable ``v0.8.0``, two release candidates, then a stable ``v1.0.0``.

Three behaviours are pinned, and each assertion fails if its part of the script is reverted:

* a **stable** tag diffs against the nearest earlier *stable* tag (``v0.8.0..v1.0.0``), not the
  release candidate immediately before it;
* a **prerelease** tag still diffs against the nearest earlier tag of any kind;
* "⚠ Breaking Changes" comes first and takes ``<type>!:`` subjects and ``BREAKING CHANGE:``
  footers away from their type's section, and "Internal" comes last with ``test`` and ``chore``.

A fourth test holds the glob that skips earlier prerelease tags to the release grammar's own
prerelease verdict, since the step has no parsed verdict for any tag but its own.

Dependency-free beyond the ``yaml`` the base matrix already carries, so it runs on all three OSes:
``ci.yml``'s ``test`` job has bash everywhere (Git Bash on Windows).
"""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

RELEASE_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml"
CHANGELOG_STEP_ID = "changelog"
RESOLVE_STEP_ID = "resolve"

#: Tags the release grammar accepts, split by its prerelease verdict (group 4 of ``TAG_ERE``).
#: The prerelease rows include the edge cases of the component's character class.
STABLE_TAGS = ("v0.0.0", "v0.8.0", "v1.0.0", "v10.20.30")
PRERELEASE_TAGS = ("v1.0.0-rc1", "v1.0.0-rc.2", "v1.0.0-0", "v1.0.0--x", "v1.0.0-.a", "v2.1.0-A")

#: The scratch history, oldest first: (subject, body, tag or None). Every type the generator
#: sorts appears at least once, and the two breaking markers sit on different sides of a tag.
HISTORY: tuple[tuple[str, str, str | None], ...] = (
    ("feat: first stable feature", "", "v0.8.0"),
    ("fix: correct the leakage factor", "", None),
    ("feat(api)!: drop the legacy reader", "", None),
    ("perf: vectorise the dwell fit", "", "v1.0.0-rc1"),
    ("test: cover the dwell fit", "", None),
    ("chore: tidy the fixtures", "", None),
    ("refactor: split the store", "Moves the writer.\n\nBREAKING CHANGE: the writer moved", None),
    ("docs: describe the store", "This body mentions BREAKING CHANGE: mid-line only.", None),
    ("ci: pin the runner", "", "v1.0.0-rc2"),
    ("build(installer): bundle the sidecar", "", None),
    ("fix(gui): keep the dock open", "", "v1.0.0"),
)


def _bash() -> str:
    bash = shutil.which("bash")
    if bash is None:
        if sys.platform.startswith("linux"):
            pytest.fail("bash is not on PATH; the changelog-script tests must not go vacuous")
        pytest.skip("bash is not on PATH on this platform")
    return bash


def _changelog_step() -> dict:
    return _step("release", CHANGELOG_STEP_ID)


def _step(job: str, step_id: str) -> dict:
    workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    steps = (workflow["jobs"][job].get("steps")) or []
    found = [s for s in steps if isinstance(s, dict) and s.get("id") == step_id]
    assert len(found) == 1, f"release.yml's `{job}` job must carry exactly one step id {step_id!r}"
    return found[0]


def test_the_stable_base_glob_agrees_with_the_release_grammar() -> None:
    """The ``--exclude`` glob that skips earlier prerelease tags is ``TAG_ERE``'s group 4.

    The changelog step cannot read a parsed verdict for an *earlier* tag, so it restates the
    grammar's prerelease component as a git wildmatch glob. This holds the two to the same answer
    on every row, so a change to either one that the other does not follow turns this red.
    """
    run = _step("verify", RESOLVE_STEP_ID)["run"]
    eres = re.findall(r"^[ \t]*TAG_ERE='([^']+)'[ \t]*$", run, re.MULTILINE)
    assert len(eres) == 1, "the resolve step must declare TAG_ERE exactly once"
    grammar = re.compile(eres[0])
    globs = re.findall(r"--exclude='([^']+)'", _changelog_step()["run"])
    assert len(globs) == 1, f"the changelog step must exclude prerelease tags with one glob: {globs}"
    wrong = []
    for tag in STABLE_TAGS + PRERELEASE_TAGS:
        match = grammar.fullmatch(tag)
        assert match is not None, f"{tag!r} must be a tag the release grammar accepts"
        if fnmatch.fnmatchcase(tag, globs[0]) is not bool(match.group(4)):
            wrong.append(tag)
    assert not wrong, f"the glob {globs[0]!r} and TAG_ERE disagree about {wrong}"


def _git(repo: Path, *args: str) -> str:
    # Pinned identity and signing off, so a developer's global config cannot leak into the
    # scratch history (a signing prompt or a missing key would fail the test for no reason).
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Tether Test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Tether Test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
    }
    proc = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return proc.stdout


@pytest.fixture(scope="module")
def history(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if shutil.which("git") is None:
        pytest.fail("git is not on PATH; the changelog-script tests need it")
    repo = tmp_path_factory.mktemp("history")
    _git(repo, "init", "-q")
    for subject, body, tag in HISTORY:
        message = ["-m", subject] + (["-m", body] if body else [])
        _git(repo, "commit", "-q", "--allow-empty", *message)
        if tag is not None:
            _git(repo, "tag", "-a", tag, "-m", f"Tether {tag}")
    return repo


def _generate(repo: Path, tmp_path: Path, tag: str, prerelease: str) -> str:
    """Run the extracted step script for ``tag`` and return the changelog it wrote."""
    step = _changelog_step()
    declared = set((step.get("env") or {}).keys())
    assert declared == {"TAG", "PRERELEASE"}, (
        f"the changelog step's env keys changed ({sorted(declared)}); update this harness so "
        "every key it declares is set"
    )
    script = tmp_path / "changelog.sh"
    # LF line endings on every OS: bash reads a CR as part of the command word.
    script.write_text(step["run"], encoding="utf-8", newline="\n")
    runner_temp = tmp_path / "runner_temp"
    runner_temp.mkdir()
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")
    out_dir = repo / "out"
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir()
    env = {
        **os.environ,
        "TAG": tag,
        "PRERELEASE": prerelease,
        "RUNNER_TEMP": runner_temp.as_posix(),
        "GITHUB_OUTPUT": output.as_posix(),
    }
    proc = subprocess.run(
        [_bash(), script.as_posix()],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, f"the changelog step failed for {tag}:\n{proc.stdout}{proc.stderr}"
    notes = out_dir / f"CHANGELOG-{tag}.md"
    assert notes.is_file(), f"the step must keep writing out/CHANGELOG-{tag}.md"
    assert "notes=" in output.read_text(encoding="utf-8"), "the step must export `notes`"
    return notes.read_text(encoding="utf-8")


def _sections(notes: str) -> dict[str, list[str]]:
    """``### Title`` -> its bullet subjects (the ``(sha)`` suffix dropped), in document order."""
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in notes.splitlines():
        if line.startswith("### "):
            current = sections.setdefault(line[4:], [])
        elif line.startswith("- ") and current is not None:
            current.append(re.sub(r" \([0-9a-f]+\)$", "", line[2:]))
    return sections


def test_a_stable_tag_diffs_against_the_previous_stable_tag(history: Path, tmp_path: Path) -> None:
    """``v1.0.0`` covers everything since ``v0.8.0``, release candidates included."""
    notes = _generate(history, tmp_path, "v1.0.0", "false")
    assert notes.splitlines()[0] == "## v1.0.0 (changes since v0.8.0)", notes
    listed = [s for subjects in _sections(notes).values() for s in subjects]
    expected = [subject for subject, _, _ in HISTORY[1:]]
    assert sorted(listed) == sorted(expected), notes


def test_a_prerelease_tag_diffs_against_the_tag_before_it(history: Path, tmp_path: Path) -> None:
    """``v1.0.0-rc2`` still diffs against ``v1.0.0-rc1``, as the published candidates did."""
    notes = _generate(history, tmp_path, "v1.0.0-rc2", "true")
    assert notes.splitlines()[0] == "## v1.0.0-rc2 (changes since v1.0.0-rc1)", notes
    listed = sorted(s for subjects in _sections(notes).values() for s in subjects)
    assert listed == sorted(subject for subject, _, _ in HISTORY[4:9]), notes


def test_breaking_comes_first_and_internal_last(history: Path, tmp_path: Path) -> None:
    """Breaking takes both markers out of their type's section; Internal holds test and chore."""
    sections = _sections(_generate(history, tmp_path, "v1.0.0", "false"))
    titles = list(sections)
    assert titles[0] == "⚠ Breaking Changes", titles
    assert titles[-1] == "Internal", titles
    assert sorted(sections["⚠ Breaking Changes"]) == [
        "feat(api)!: drop the legacy reader",
        "refactor: split the store",
    ]
    # Listed once, under Breaking: neither is repeated under its own type, and they were the
    # range's only feat and refactor commits, so both sections are absent.
    assert "Features" not in sections, sections
    assert "Refactoring" not in sections, sections
    # A body that merely mentions the phrase mid-line is not a footer.
    assert sections["Documentation"] == ["docs: describe the store"]
    assert sorted(sections["Internal"]) == ["chore: tidy the fixtures", "test: cover the dwell fit"]
    assert titles == [
        "⚠ Breaking Changes",
        "Bug fixes",
        "Performance",
        "Documentation",
        "Build & packaging",
        "CI",
        "Internal",
    ]

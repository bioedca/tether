# SPDX-FileCopyrightText: 2026 The Tether Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The docs site keeps maintainer-only pages out of the reader's nav (#181).

The nav has two top-level groups, decided on #181: ``Using Tether`` for everything a lab scientist
acts on, and ``Project & maintainers`` for what only the project acts on. These tests pin the
properties that decision exists for, so a later nav edit cannot quietly undo them:

* commands that need write access to ``bioedca/tether`` live only on maintainer pages;
* every maintainer page says, in its first paragraph, what access it needs;
* the standalone-tMAVEN cross-OS acceptance record survives with both ``pending`` rows, on a
  maintainer page rather than a user one (it is ADR-0010's manual GUI leg, not tracker state);
* the deep-classifier page leads with its setup command and keeps its rationale last, and the
  ``#gpu-cuda-setup`` anchor that ``troubleshooting.md`` links still exists.

Stdlib plus PyYAML (a mkdocs dependency in the base lock); it reads Markdown as text.
"""

from __future__ import annotations

import fnmatch
import html
import re
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[1]
DOCS = _REPO / "docs"
USER_GROUP = "Using Tether"
MAINTAINER_GROUP = "Project & maintainers"

# Each needs write or admin rights on the repository; a user page must never instruct them.
_MAINTAINER_COMMANDS = ("git tag -s", "gh workflow run", "gh api repos/bioedca")


def _config() -> dict:
    return yaml.safe_load((_REPO / "mkdocs.yml").read_text(encoding="utf-8"))


def _pages(entries: object) -> list[str]:
    found: list[str] = []
    if isinstance(entries, list):
        for entry in entries:
            found += _pages(entry)
    elif isinstance(entries, dict):
        for value in entries.values():
            found += _pages(value)
    elif isinstance(entries, str):
        found.append(entries)
    return found


def _groups() -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for entry in _config()["nav"]:
        assert isinstance(entry, dict) and len(entry) == 1, f"top-level nav entry {entry!r}"
        ((title, children),) = entry.items()
        groups[html.unescape(title)] = _pages(children)
    return groups


def _not_in_nav_patterns() -> list[str]:
    return (_config().get("not_in_nav") or "").split()


def _first_paragraph(page: str) -> str:
    text = (DOCS / page).read_text(encoding="utf-8")
    body = text.split("\n", 1)[1] if text.startswith("# ") else text
    paragraphs = [p for p in re.split(r"\n\s*\n", body.strip()) if p.strip()]
    return " ".join(paragraphs[0].split()) if paragraphs else ""


def test_the_nav_has_exactly_the_two_decided_groups() -> None:
    assert list(_groups()) == [USER_GROUP, MAINTAINER_GROUP]


def test_maintainer_commands_appear_only_on_maintainer_pages() -> None:
    maintainer_pages = set(_groups()[MAINTAINER_GROUP])
    patterns = _not_in_nav_patterns()
    offenders = []
    for path in sorted(DOCS.rglob("*.md")):
        page = path.relative_to(DOCS).as_posix()
        text = path.read_text(encoding="utf-8")
        if not any(command in text for command in _MAINTAINER_COMMANDS):
            continue
        if page in maintainer_pages or any(fnmatch.fnmatch(page, p) for p in patterns):
            continue
        offenders.append(page)
    assert not offenders, f"write-access commands on pages a user reaches: {offenders}"


def test_every_maintainer_page_opens_with_a_reader_statement_naming_its_access() -> None:
    missing = {}
    for page in _groups()[MAINTAINER_GROUP]:
        first = _first_paragraph(page)
        if not first.startswith("**Who this page is for.**") or "access" not in first:
            missing[page] = first[:120]
    assert not missing, f"maintainer pages without a reader statement naming access: {missing}"


def test_the_cross_os_record_keeps_both_pending_rows_on_a_maintainer_page() -> None:
    rows = [
        (path.relative_to(DOCS).as_posix(), line)
        for path in sorted(DOCS.rglob("*.md"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if "| pending |" in line
    ]
    assert [line.split("|")[1].strip() for _, line in rows] == [
        "Windows 11",
        "macOS / Linux (desktop)",
    ], rows
    maintainer_pages = set(_groups()[MAINTAINER_GROUP])
    assert {page for page, _ in rows} <= maintainer_pages, rows


def test_the_deep_classifier_page_leads_with_setup_and_ends_with_rationale() -> None:
    text = (DOCS / "ml" / "deep-classifier.md").read_text(encoding="utf-8")
    first_fence = re.search(r"^```[^\n]*\n(.*?)^```", text, flags=re.MULTILINE | re.DOTALL)
    assert first_fence is not None
    assert first_fence.group(1).startswith("conda-lock install"), first_fence.group(1)

    headings = re.findall(r"^## (.+)$", text, flags=re.MULTILINE)
    sections = ["Setup", "Using the classifier", "Why a third isolated environment"]
    positions = [headings.index(name) for name in sections]
    assert positions == sorted(positions), headings
    # troubleshooting.md links this anchor; `--strict` fails if the heading moves away from it.
    assert re.search(r"^### GPU \(CUDA\) setup$", text, flags=re.MULTILINE)
    assert "ml/deep-classifier.md#gpu-cuda-setup" in (DOCS / "troubleshooting.md").read_text(
        encoding="utf-8"
    )

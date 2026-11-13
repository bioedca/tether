# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""``docs/install.md`` documents one release, and every pinned value on it agrees.

The install guide is a page of copy-paste commands: installer file names, a
``gh attestation verify`` line with a ``--source-ref`` and a ``--source-digest``, and the
``tether --version`` output to expect. #583 found it still naming ``v1.0.0-rc2`` a month
after ``v1.0.0-rc3`` was published, so a reader who downloaded rc3 found that no command
on the page matched their file. Nothing in ``mkdocs build --strict`` can see that: a stale
file name renders exactly as well as a current one.

The guard has two halves. :data:`INSTALL_RELEASE` and :data:`INSTALL_RELEASE_COMMIT` name
the release the page documents, and every release-specific value on the page must agree
with them, so the page can never mix two releases. Moving the page to a new release is then
one deliberate edit here, which ``docs/release.md`` lists as a step of cutting a release.
Prose *about* an older release ("do not use ``v1.0.0-rc1``") is not a pinned value and is
not checked.

Dependency-free and stdlib-only, so it runs on the base 3-OS ``test`` matrix.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
INSTALL = _REPO / "docs" / "install.md"
COMPATIBILITY = _REPO / "docs" / "compatibility.md"
RELEASE = _REPO / "docs" / "release.md"

#: The release ``docs/install.md`` documents, as its tag spells it without the leading ``v``.
#: Update it together with the page when a newer release is published (``docs/release.md``).
INSTALL_RELEASE = "1.0.0-rc3"

#: The commit that release's tag points at: the page's ``--source-digest``. Read it with
#: ``gh api repos/bioedca/tether/git/tags/<tag object sha>`` or ``git rev-parse v<tag>^{commit}``.
INSTALL_RELEASE_COMMIT = "6de7824fa73bebd70f8fbf6dda037b6857fd1b2f"

_VERSION = r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?"

#: Each kind of release-specific value the page carries, with the version it names as group 1.
_PINNED_VALUES: dict[str, re.Pattern[str]] = {
    "installer file name": re.compile(rf"\bTether-({_VERSION})-(?:Linux|MacOSX|Windows)-"),
    "release link": re.compile(rf"/releases/tag/v({_VERSION})\b"),
    "tag ref": re.compile(rf"\brefs/tags/v({_VERSION})\b"),
}

#: ``tether --version`` output: the package version in its PEP 440 form (``1.0.0rc3``).
_VERSION_LINE = re.compile(r"^tether (\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?)\s*$", re.MULTILINE)

#: The commit the attestation command binds the installer to.
_SOURCE_DIGEST = re.compile(r"--source-digest\s+([0-9a-fA-F]+)")


def _pep440(tag_version: str) -> str:
    """``1.0.0-rc3`` -> ``1.0.0rc3``, the form ``tether --version`` prints."""
    return re.sub(r"-(a|b|rc)(\d+)$", r"\1\2", tag_version)


def _disagreements(text: str, release: str, commit: str) -> list[str]:
    """Every release-specific value in *text* that names something other than *release*."""
    found = [
        f"{kind} names {match.group(1)}: {match.group(0)!r}"
        for kind, pattern in _PINNED_VALUES.items()
        for match in pattern.finditer(text)
        if match.group(1) != release
    ]
    found += [
        f"version line names {match.group(1)}: {match.group(0)!r}"
        for match in _VERSION_LINE.finditer(text)
        if match.group(1) != _pep440(release)
    ]
    found += [
        f"source digest is {match.group(1)}, not {commit}"
        for match in _SOURCE_DIGEST.finditer(text)
        if match.group(1).lower() != commit
    ]
    return found


def test_every_pinned_value_on_the_install_page_names_the_documented_release() -> None:
    """#583: the page cannot mix two releases, or lag behind the constant."""
    offenders = _disagreements(
        INSTALL.read_text(encoding="utf-8"), INSTALL_RELEASE, INSTALL_RELEASE_COMMIT
    )
    assert not offenders, (
        f"docs/install.md does not match the release it documents (v{INSTALL_RELEASE}). "
        "Move every file name, release link, tag ref, version line and the source digest to "
        "one release, and update INSTALL_RELEASE and INSTALL_RELEASE_COMMIT with them:\n"
        + "\n".join(offenders)
    )


def test_the_install_page_carries_each_kind_of_pinned_value() -> None:
    """The guard above is vacuous if the page stops carrying what it checks."""
    text = INSTALL.read_text(encoding="utf-8")
    for kind, pattern in _PINNED_VALUES.items():
        assert pattern.search(text), f"docs/install.md no longer carries a {kind}"
    assert _VERSION_LINE.search(text), "docs/install.md no longer shows `tether --version` output"
    assert _SOURCE_DIGEST.search(text), "docs/install.md no longer carries a --source-digest"
    assert re.fullmatch(r"[0-9a-f]{40}", INSTALL_RELEASE_COMMIT)


def test_the_guard_reports_a_page_that_mixes_two_releases() -> None:
    """An absence-assertion is worthless unless presence is shown, so each kind is pinned."""
    commit = INSTALL_RELEASE_COMMIT
    clean = (
        "[`v1.0.0-rc3`](https://github.com/bioedca/tether/releases/tag/v1.0.0-rc3)\n"
        "sh Tether-1.0.0-rc3-Linux-x86_64.sh\n"
        f"  --source-ref refs/tags/v1.0.0-rc3 \\\n  --source-digest {commit}\n"
        "tether 1.0.0rc3\n"
        "Do not use `v1.0.0-rc1`: an installer from 1.0.0-rc2 or earlier does not.\n"
    )
    assert _disagreements(clean, "1.0.0-rc3", commit) == []
    for stale in (
        "sh Tether-1.0.0-rc2-Linux-x86_64.sh",
        "Open the [release page](https://github.com/bioedca/tether/releases/tag/v1.0.0-rc2)",
        "  --source-ref refs/tags/v1.0.0-rc2 \\",
        "  --source-digest 292dd151c0263f981f8cd0f358d5e2074091d9de",
        "tether 1.0.0rc2",
    ):
        assert _disagreements(clean + stale + "\n", "1.0.0-rc3", commit), stale
    # A stable release prints a bare version, and the guard reads that form too.
    assert _disagreements("tether 1.0.0rc3\n", "1.0.0", commit)
    assert not _disagreements("tether 1.0.0\n", "1.0.0", commit)


def test_the_compatibility_footprint_names_the_release_it_was_measured_on() -> None:
    """#583: a dated measurement may stay, so long as it says which release it measured."""
    text = COMPATIBILITY.read_text(encoding="utf-8")
    paragraph = next(
        (block for block in text.split("\n\n") if block.startswith("**Measured on Linux only")),
        None,
    )
    assert paragraph, "docs/compatibility.md lost its 'Measured on Linux only' footprint paragraph"
    assert re.search(rf"`v{_VERSION}`", paragraph), (
        "the disk-footprint figure must name the release it was measured on"
    )


def test_the_release_procedure_says_to_move_the_install_page() -> None:
    """The constant above is only useful if cutting a release says to update it."""
    text = RELEASE.read_text(encoding="utf-8")
    assert "tests/test_docs_install_release.py" in text
    assert "INSTALL_RELEASE" in text

# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""No page puts Tether's launcher folder in front of the reader's ``PATH`` (#585).

Up to rc3, on Linux and macOS, the installer's ``bin`` folder holds the two launchers and also
the installer's own ``python``, ``pip`` and ``conda`` (#492). ``docs/install.md`` step 4 therefore
*appends* the folder, and says why: at the end, the reader's own tools keep working. From the
next release the launchers have a folder of their own, ``launchers`` (ADR-0094), and the pages
name both; the guard covers both names, so the one instruction stays in one form. The
command-line page once *prepended* it (``export PATH="<install-root>/bin:$PATH"`` and two
PowerShell forms), so a reader who followed that page got the bootstrap ``python`` ahead of
their own. The same instruction had been typed twice, and the two copies disagreed.

The fix gives the instruction one home, install step 4, and the command-line page links to it.
This module guards both halves:

* no page under ``docs/`` and no line of ``README.md`` writes a ``PATH`` value in which the
  Tether ``bin`` folder comes before the existing ``PATH`` (``$PATH``, ``${PATH}``,
  ``$env:PATH``, ``%PATH%`` or ``[Environment]::GetEnvironmentVariable("PATH", …)``);
* ``docs/cli.md`` carries no ``PATH`` command of its own and links to install step 4.

The match is on the *text* of a command, so it is a model of the forms the pages use, not a
shell parser. The self-tests below pin it on the exact lines #585 removed and on the append
forms that must stay legal.

Stdlib only, so it runs on the base 3-OS ``test`` matrix.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
DOCS = _REPO / "docs"
README = _REPO / "README.md"
CLI = DOCS / "cli.md"
INSTALL = DOCS / "install.md"

#: The one home of the PATH instruction.
INSTALL_STEP_4 = "install.md#4-put-tethers-launcher-folder-on-your-path"

#: Tether's launcher folder as the pages spell it: the ``<install-root>`` placeholder, or a
#: concrete install folder named ``tether``/``Tether`` (``$HOME/tether``,
#: ``$env:USERPROFILE\\Tether``, ``C:\\...\\Tether``), followed by ``bin`` (up to rc3) or
#: ``launchers`` (ADR-0094).
_LAUNCHER_DIR = r"(?:<install-root>|[^\s\"';:]*[Tt]ether)[/\\](?:bin|launchers)[/\\]?"

#: The reader's existing PATH, in each shell the pages use.
_EXISTING_PATH = (
    r"(?:\$\{?PATH\}?|\$env:PATH|%PATH%"
    r"|\[Environment\]::GetEnvironmentVariable\(\s*[\"']PATH[\"'])"
)

#: The launcher folder, a list separator (or a fish-style space), optional quote and ``+``,
#: then the existing PATH: the folder is placed first.
_PREPEND = re.compile(
    _LAUNCHER_DIR + r"(?:\s*[:;]\s*[\"']?\s*(?:\+\s*)?|\s+)" + _EXISTING_PATH,
    re.IGNORECASE,
)

#: Any command that sets PATH: a POSIX ``export PATH=``/``PATH=``, a PowerShell
#: ``$env:PATH =`` or a ``SetEnvironmentVariable("PATH"`` call.
_SETS_PATH = re.compile(
    r"(?:\bexport\s+PATH\s*=|^\s*PATH\s*=|\$env:PATH\s*="
    r"|SetEnvironmentVariable\(\s*[\"']PATH[\"'])",
    re.IGNORECASE | re.MULTILINE,
)


def _pages() -> list[Path]:
    return sorted(DOCS.rglob("*.md")) + [README]


def _prepends(text: str) -> list[str]:
    return [m.group(0) for m in _PREPEND.finditer(text)]


@pytest.mark.parametrize(
    "line",
    [
        # The three forms #585 removed from docs/cli.md, verbatim.
        'export PATH="<install-root>/bin:$PATH"',
        '$env:PATH = "<install-root>\\bin;$env:PATH"',
        '    "PATH", "<install-root>\\bin;" + '
        '[Environment]::GetEnvironmentVariable("PATH", "User"), "User")',
        # Other spellings of the same mistake.
        "export PATH=$HOME/tether/bin:${PATH}",
        "set PATH=C:\\Users\\me\\Tether\\bin;%PATH%",
        "set -gx PATH $HOME/Tether/bin $PATH",
        # The launcher folder of ADR-0094, prepended.
        'export PATH="$HOME/tether/launchers:$PATH"',
        '$env:PATH = "$env:USERPROFILE\\Tether\\launchers;$env:PATH"',
    ],
)
def test_prepend_forms_are_caught(line: str) -> None:
    assert _prepends(line), line


@pytest.mark.parametrize(
    "line",
    [
        # The append forms install step 4 and the README use, which must stay legal.
        "echo 'export PATH=\"$PATH:$HOME/tether/bin\"' >> ~/.bashrc",
        "echo 'export PATH=\"$PATH:$HOME/Tether/bin\"' >> ~/.zshrc",
        '  [Environment]::GetEnvironmentVariable("Path", "User") '
        '+ ";$env:USERPROFILE\\Tether\\bin",',
        'export PATH="$PATH:<install-root>/bin"   '
        '# PowerShell: $env:PATH = "$env:PATH;<install-root>\\bin"',
        'export PATH="$PATH:$HOME/tether/launchers"',
        # Someone else's folder first is not this page's business.
        'export PATH="$HOME/miniforge3/bin:$PATH"',
    ],
)
def test_append_forms_are_not_flagged(line: str) -> None:
    assert not _prepends(line), line


@pytest.mark.parametrize("page", _pages(), ids=lambda p: str(p.relative_to(_REPO)))
def test_no_page_prepends_the_launcher_folder(page: Path) -> None:
    found = _prepends(page.read_text(encoding="utf-8"))
    assert not found, (
        f"{page.relative_to(_REPO)} puts Tether's launcher folder before the existing PATH: "
        f"{found}. Append it instead; docs/install.md step 4 says why (#585, #492)."
    )


def test_install_step_4_holds_the_append_instruction() -> None:
    text = INSTALL.read_text(encoding="utf-8")
    assert "## 4. Put Tether's launcher folder on your PATH" in text
    assert 'export PATH="$PATH:$HOME/tether/bin"' in text
    assert 'export PATH="$PATH:$HOME/Tether/bin"' in text


def test_cli_page_links_to_install_step_4_instead_of_repeating_it() -> None:
    text = CLI.read_text(encoding="utf-8")
    assert not _SETS_PATH.search(text), (
        "docs/cli.md sets PATH itself again; link to install step 4 instead (#585)."
    )
    assert f"]({INSTALL_STEP_4})" in text

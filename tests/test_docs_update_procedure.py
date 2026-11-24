# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The "Update Tether" section of the install guide quotes the program it documents (#602).

``docs/install.md`` tells a reader how to move to a newer release and how the opt-in update
check (#248) looks. ``docs/packaging.md`` says what the updater's download check (#333) does,
and ``docs/troubleshooting.md`` sends a reader whose project was written by a newer Tether to
that section. A renamed menu item or a reworded notice renders as well as the real one under
``mkdocs build --strict``, so this module pins the quotes to the source:

* every menu and button label the section quotes is a string literal of
  ``tether.gui.update_notice``;
* the newer-release notice the section quotes is the f-string the notice sets, with each
  placeholder standing for one formatted value and the HTML link markup removed;
* the procedure rests on the packaging workflow's upgrade smoke, which still runs and still
  exercises both paths the section reports (over-install and remove-then-install);
* the troubleshooting row and the packaging page link to the section.

It reads the module with :mod:`ast` and never imports Qt, so it is stdlib-only and runs on
the base 3-OS ``test`` matrix.
"""

from __future__ import annotations

import ast
import html
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
INSTALL = _REPO / "docs" / "install.md"
PACKAGING = _REPO / "docs" / "packaging.md"
TROUBLESHOOTING = _REPO / "docs" / "troubleshooting.md"
NOTICE_SOURCE = _REPO / "src" / "tether" / "gui" / "update_notice.py"
UPGRADE_SMOKE = _REPO / "packaging" / "scripts" / "upgrade_smoke.sh"
PACKAGING_WORKFLOW = _REPO / ".github" / "workflows" / "packaging.yml"

#: The labels the section quotes, each as the program spells it.
LABELS = (
    "Check for new releases at startup",
    "Check for new releases",
    "Don't check",
)

_ANCHOR = "install.md#update-tether"


def _section(text: str, heading: str) -> str:
    """The body of the ``## heading`` section, up to the next ``## `` heading."""
    match = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## )", text, re.MULTILINE | re.DOTALL)
    assert match, f"docs/install.md has no '## {heading}' section"
    return match.group(1)


def _string_literals(tree: ast.AST) -> set[str]:
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def _f_string_templates(tree: ast.AST) -> set[str]:
    """Each f-string with ``{}`` for every formatted value and the HTML tags removed."""
    templates = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):
            continue
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
            else:
                parts.append("{}")
        # The link's href is itself a formatted value inside the tag, so drop whole tags.
        templates.add(re.sub(r"<[^>]*>", "", "".join(parts)).strip())
    return templates


def _quoted_notice(section: str) -> str:
    """The notice as the section quotes it, with ``<...>`` placeholders as ``{}``."""
    block = " ".join(line[1:].strip() for line in section.splitlines() if line.startswith(">"))
    match = re.search(r"\*(Tether .*? to download it\.)\*", block)
    assert match, "the section quotes no newer-release notice, in italics, in a block quote"
    quoted = re.sub(r"\s+", " ", html.unescape(match.group(1)))
    return re.sub(r"<[^>]*>", "{}", quoted)


def test_install_guide_has_the_update_section_before_uninstall() -> None:
    text = INSTALL.read_text(encoding="utf-8")
    assert text.index("\n## Update Tether\n") < text.index("\n## Uninstall\n")
    section = _section(text, "Update Tether")
    assert re.search(r"^1\. ", section, re.MULTILINE), "the procedure is not numbered"
    assert "`tether --version`" in section
    assert "(#uninstall)" in section
    assert "(#3-run-the-installer)" in section


def test_quoted_labels_are_the_programs_labels() -> None:
    section = _section(INSTALL.read_text(encoding="utf-8"), "Update Tether")
    literals = _string_literals(ast.parse(NOTICE_SOURCE.read_text(encoding="utf-8")))
    for label in LABELS:
        assert f"**{label}**" in section or f"→ {label}**" in section, (
            f"the Update Tether section does not quote {label!r}"
        )
        assert label in literals, f"tether.gui.update_notice has no label {label!r}"


def test_quoted_notice_is_the_programs_notice() -> None:
    section = _section(INSTALL.read_text(encoding="utf-8"), "Update Tether")
    templates = _f_string_templates(ast.parse(NOTICE_SOURCE.read_text(encoding="utf-8")))
    quoted = _quoted_notice(section)
    assert quoted in templates, f"the quoted notice {quoted!r} is not one of {sorted(templates)}"


def test_procedure_rests_on_the_upgrade_smoke() -> None:
    workflow = PACKAGING_WORKFLOW.read_text(encoding="utf-8")
    assert "bash packaging/scripts/upgrade_smoke.sh" in workflow
    smoke = UPGRADE_SMOKE.read_text(encoding="utf-8")
    for path in ("--- over-install", "--- remove-then-install"):
        assert path in smoke, f"the upgrade smoke no longer runs the {path[4:]} path"


def test_troubleshooting_and_packaging_link_to_the_section() -> None:
    rows = [
        line
        for line in TROUBLESHOOTING.read_text(encoding="utf-8").splitlines()
        if "written by a newer Tether" in line and line.startswith("|")
    ]
    assert rows, "the troubleshooting table has no 'written by a newer Tether' row"
    assert all(_ANCHOR in row for row in rows)
    assert _ANCHOR in PACKAGING.read_text(encoding="utf-8")


def test_the_template_reader_sees_through_link_markup() -> None:
    tree = ast.parse('f"A {x} b. " f\'<a href="{u}">Open</a> it.\'')
    assert _f_string_templates(tree) == {"A {} b. Open it."}

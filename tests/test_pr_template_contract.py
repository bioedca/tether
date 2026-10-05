# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The PR template exposes every review-evidence field; the launcher prompt names no ``gh`` merge.

#260 retired ``tests/test_review_policy.py`` because it pinned the *wording* of the review policy
and so blocked that policy's own correction. Two of its tests were not prose matchers, and #261
re-covers them structurally:

* the template must carry each review-evidence field the lane records — once each, as a fillable
  ``- <field>:`` bullet under ``## Linked tracking``. A field that goes missing is evidence nobody
  is asked for; a field that appears twice is two places to record one fact, and they drift.
* the worker launcher's default prompt must still name the worker skill and its claim tool, and
  must name no ``gh`` merge in any spelling: merge authority is per-PR and never conferred by a
  launcher.

Only labels and shape are asserted. No sentence of ``AGENTS.md``, ``CONTRIBUTING.md``,
``docs/PRD.md`` or ``docs/adr/**`` is encoded here, and a field's guidance text after its label may
change freely — that is the property #260 needed and #261 requires.

Both files are read with a parser rather than line by line: the template through
``.agents/bin/markdown_structure.py`` (ADR-0066), so a bullet inside an HTML comment, a code block
or a raw HTML block is not counted, and the launcher with PyYAML.
"""

from __future__ import annotations

import importlib.util
import re
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

_REPO = Path(__file__).resolve().parents[1]

TEMPLATE = _REPO / ".github" / "pull_request_template.md"
LAUNCHER = _REPO / ".agents" / "skills" / "tether-worker" / "agents" / "openai.yaml"
SKILL = LAUNCHER.parents[1] / "SKILL.md"

_spec = importlib.util.spec_from_file_location(
    "tether_markdown_structure", _REPO / ".agents" / "bin" / "markdown_structure.py"
)
assert _spec is not None and _spec.loader is not None
_md = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_md)

# The review-evidence fields a pull request records, by label. Each names a leg or an outcome of
# the review lane: the head the evidence binds, each provider's result, the provider that produced
# none, the disposition of findings, and the human sign-off.
REVIEW_EVIDENCE_FIELDS = (
    "Final head SHA",
    "Codex",
    "Codex closing review",
    "Greptile",
    "CodeRabbit",
    "Provider that did not review",
    "Findings",
    "Human sign-off",
)

# A field label ends at its first ":" or at a spaced em dash, whichever comes first — the two
# separators the template uses between a label and its guidance.
_LABEL_END = re.compile(r":| — ")

# The element the page collapses: what sits inside it is not on the page until a reader opens it.
_DISCLOSURE = re.compile(r"<details\b", re.IGNORECASE)


def _drawn_headings(block: Any) -> list[tuple[int, str]]:
    """The level and text of each heading the page draws for ``block``: a Markdown heading, or an
    ``<h1>`` to ``<h6>`` in raw HTML, which the page lays out as a heading too."""
    if isinstance(block, _md.Heading):
        return [(block.level, block.plain)]
    if isinstance(block, (_md.Html, _md.Paragraph)):
        return [(int(s.tag[1]), s.text) for s in block.shown if re.fullmatch(r"h[1-6]", s.tag)]
    return []


def _sections(text: str, heading: str) -> list[list[Any]]:
    """The top-level bullets under each level-two ``heading`` the page draws, each as its first
    paragraph.

    A section runs to the next top-level heading of level one or two, Markdown or raw HTML. A
    bullet is an item of a top-level unordered list whose first block is a paragraph, so a nested
    or quoted bullet is not one, and neither is a bullet inside an HTML comment, a code block or a
    raw HTML block. A footnote, which the page draws at its foot, is not counted, nor is a bullet
    carrying an image, which the page draws as a picture rather than as text.
    """
    sections: list[list[Any]] = []
    bullets: list[Any] | None = None
    for block in _md.parse(text):
        if _md.at_foot(block):
            continue
        for level, title in _drawn_headings(block):
            if level <= 2:
                bullets = [] if level == 2 and title == heading else None
                if bullets is not None:
                    sections.append(bullets)
        if bullets is None or not isinstance(block, _md.ListBlock) or block.ordered:
            continue
        for item in block.items:
            first = item.blocks[0] if item.blocks else None
            if isinstance(first, _md.Paragraph) and not _md.at_foot(first) and not first.pictured:
                bullets.append(first)
    return sections


def _field(item: Any) -> tuple[str, str] | None:
    """Split a bullet's ``<label>: <value>`` text into its label and the rest, or ``None``.

    A task-list item (``[ ] …``) is not a field, and nor is a bullet whose label is not the
    literal start of its source, bold aside: a label drawn from markup — an image's alternative
    text, a tag, a character reference, a link — is not text the page shows the author to fill in.
    """
    text = item.plain
    if text.startswith("["):
        return None
    match = _LABEL_END.search(text)
    if match is None:
        return None
    label = text[: match.start()].strip()
    if not item.text.replace("**", "").startswith(label):
        return None
    return label, text[match.end() :].strip()


def _fields() -> list[tuple[str, str]]:
    text = TEMPLATE.read_text(encoding="utf-8")
    assert not _DISCLOSURE.search(text), (
        "the template carries a <details> element, which collapses what it holds — the review "
        "evidence must stay on the page"
    )
    sections = _sections(text, "Linked tracking")
    assert len(sections) == 1, f"'## Linked tracking' must be drawn once, not {len(sections)} times"
    return [f for f in map(_field, sections[0]) if f is not None]


def test_each_review_evidence_field_appears_exactly_once() -> None:
    """Every field is present, and none is duplicated."""
    counts = Counter(label for label, _ in _fields())
    wrong = {name: counts[name] for name in REVIEW_EVIDENCE_FIELDS if counts[name] != 1}
    assert not wrong, (
        f"review-evidence fields must each appear exactly once under '## Linked tracking'; "
        f"found {wrong} (0 = missing, >1 = duplicated)"
    )


def test_review_evidence_fields_are_fillable_bullets() -> None:
    """A field is a label the author fills in, so a value slot follows it in the same bullet.

    ``Final head SHA:`` is the one field whose slot is deliberately empty; the rest offer the
    states to choose from. What is pinned is that the bullet *is* a field — label, separator,
    slot — not what the guidance says.
    """
    fields = dict(_fields())
    for name in REVIEW_EVIDENCE_FIELDS:
        assert name in fields, f"'{name}' is not a '- {name}: …' bullet under '## Linked tracking'"
    unfilled = [n for n in REVIEW_EVIDENCE_FIELDS if n != "Final head SHA" and not fields[n]]
    assert not unfilled, f"these fields offer no value slot to fill: {unfilled}"


WORKER_SKILL = "$tether-worker"
CLAIM_TOOL = ".agents/bin/claim.py"

# Characters a shell drops from a word rather than reading as text: quotes, and the escapes of
# POSIX shells (`\`), PowerShell (`` ` ``) and cmd (`^`) — each with the line break after it,
# where the escape joins two lines into one.
_SHELL_ELIDED = re.compile(r"[`^\\]\r?\n|['\"`^\\]")


class _Loader(yaml.SafeLoader):
    """PyYAML's safe loader, refusing a repeated mapping key and a ``<<`` merge.

    YAML forbids a repeated key, and PyYAML would otherwise keep the last one silently; a ``<<``
    merge would read a key written under another mapping as this mapping's own.
    """


def _mapping(loader: _Loader, node: yaml.MappingNode) -> dict[Any, Any]:
    where = f"the YAML mapping at line {node.start_mark.line + 1}"
    assert all(key.tag != "tag:yaml.org,2002:merge" for key, _ in node.value), (
        f"{where} merges another in with '<<'"
    )
    keys = [key.value for key, _ in node.value if isinstance(key, yaml.ScalarNode)]
    repeated = sorted({key for key in keys if keys.count(key) > 1})
    assert not repeated, f"{where} repeats {repeated}"
    return loader.construct_mapping(node, deep=True)


_Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _interface() -> dict[str, Any]:
    """The launcher config's ``interface`` mapping, as YAML decodes it."""
    config = yaml.load(LAUNCHER.read_text(encoding="utf-8"), Loader=_Loader)
    interface = config.get("interface") if isinstance(config, dict) else None
    assert isinstance(interface, dict), f"{LAUNCHER.name} has no 'interface' mapping"
    return interface


def _words(text: str) -> set[str]:
    """The whitespace-separated words of ``text``, less surrounding quotes, brackets and trailing
    punctuation, so a name matches only whole: ``claim.py.bak`` is not ``claim.py``."""
    return {w.lstrip("`'\"([").rstrip("`'\")],.;:!?") for w in text.split()}


def test_launcher_names_the_worker_skill_and_its_claim_tool() -> None:
    """The launcher starts one worker on the claim mutex through the skill, so its prompt names
    both, and each name is a real one: the skill this launcher sits in, and the claim tool on disk.

    That the prompt tells the worker to *use* them is wording, which this file does not assert.
    """
    interface = _interface()
    for key in ("display_name", "short_description", "default_prompt"):
        value = interface.get(key)
        assert isinstance(value, str) and value.strip(), (
            f"{LAUNCHER.name} has no non-empty interface.{key}"
        )
    words = _words(interface["default_prompt"])
    assert WORKER_SKILL in words, f"the launcher prompt must name {WORKER_SKILL}"
    assert CLAIM_TOOL in words, f"the launcher prompt must name {CLAIM_TOOL}"
    front = re.match(r"---\n(.*?)\n---\n", SKILL.read_text(encoding="utf-8"), re.DOTALL)
    assert front, "the SKILL.md this launcher sits beside has no front matter"
    skill = yaml.load(front.group(1), Loader=_Loader)
    assert isinstance(skill, dict) and skill.get("name") == WORKER_SKILL.removeprefix("$"), (
        f"the skill it names, {WORKER_SKILL}, is not the one this launcher sits in"
    )
    assert (_REPO / CLAIM_TOOL).is_file(), f"the claim tool it names, {CLAIM_TOOL}, is missing"


def _command_words(text: str) -> list[list[str]]:
    """The lower-cased letters-and-digits words of ``text``, read twice: once with the characters
    a shell elides taken as word breaks, and once with them deleted."""
    return [
        re.findall(r"[a-z0-9]+", variant.lower()) for variant in (text, _SHELL_ELIDED.sub("", text))
    ]


def test_launcher_prompt_carries_no_gh_merge() -> None:
    """Merge authority is per pull request and explicit; the prompt must never embed it.

    The rule is deliberately blunt rather than a command matcher: no word ``gh`` may be followed,
    anywhere later in the prompt, by the word ``merge`` — in any letter case, quoting, escaping or
    path, so ``'gh' pr merge``, ``& "GH.EXE" PR merge`` and ``gh api …/merge`` all fail it. The
    cost is that a prompt naming ``gh`` for another reason and mentioning a merge later fails too;
    reword it. A name a shell builds at run time — a variable, an alias, a command substitution —
    is not looked for.
    """
    for words in _command_words(_interface()["default_prompt"]):
        if "gh" in words:
            assert "merge" not in words[words.index("gh") + 1 :], (
                "the launcher prompt names gh and then a merge — merge authority is never a "
                "launcher default"
            )

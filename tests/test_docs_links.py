# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Every internal link in the docs corpus resolves, including the ones mkdocs cannot see.

``mkdocs build --strict`` only validates links it can *resolve*. A reference-style use
``[text][key]`` whose ``key`` has no ``[key]: …`` definition is not a link at all to
Python-Markdown — it renders as the literal source text — so there is no target for the
build to complain about. That is how the parity gallery reached the published site
showing ``[McCann 2010][mccann2010]`` fourteen times with the required ``docs-build``
gate green (#183, repaired by hand in #207). This module closes that blind spot, and
re-checks the targets the build does see so the same guard runs on all three operating
systems rather than only on the Linux ``docs-build`` job:

* every ``[text][key]`` (and collapsed ``[text][]``) has a definition in the same file;
* every relative link target exists on disk, inside the published ``docs/`` tree;
* every ``#fragment`` names a heading slug or an inline ``<a id="…">`` anchor in the
  target page — the bibliography-anchor form #207 standardised on.

Fenced blocks, inline code spans and HTML comments are blanked before any of this, so
``["condition_id"][mask]`` inside a code span is code, not a reference. The walk is
``docs/**/*.md`` minus ``docs/PRD.md``, which ``mkdocs.yml`` excludes from the site.

Stdlib only (``re`` + ``pathlib``), like ``tests/test_adr_index.py``: ``python-markdown``
is not in the base conda-lock, so it cannot be imported on the 3-OS ``test`` matrix. The
structural parse mirrors the Python-Markdown rules that matter here — a single optional
space between ``]`` and ``[`` still forms a reference, ids are matched case-insensitively,
and heading slugs follow ``markdown.extensions.toc.slugify`` with its ``_1``/``_2``
de-duplication — so it agrees with the renderer on the corpus rather than approximating it.
Cross-checked against a real ``mkdocs build`` of every page, the one deliberate divergence
is a fence inside a blockquote: Python-Markdown's ``fenced_code`` does not recognise it and
renders its lines as prose, but an author who fenced it meant code, so it is never
link-checked here and any ``#`` comment lines in it are not counted as heading anchors.
"""

from __future__ import annotations

import html
import re
import sys
import unicodedata
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parents[1] / "docs"

# `mkdocs.yml` `exclude_docs:` — not part of the site, so neither walked nor linkable.
_EXCLUDED = {DOCS / "PRD.md"}

_QUOTE_RE = re.compile(r"^ {0,3}(?:>[ ]?)+")
_FENCE_OPEN_RE = re.compile(r"^(\s*)(`{3,}|~{3,})(.*)$")
_FENCE_CLOSE_RE = re.compile(r"^\s*(`{3,}|~{3,})\s*$")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
# A code span: a backtick run closed by a run of exactly the same length, never across
# a blank line (a blank line ends the paragraph the span would have to live in).
_CODE_SPAN_RE = re.compile(r"(?<![`\\])(`+)(?!`)((?:(?!\n[ \t]*\n).)+?)(?<!`)\1(?!`)", re.DOTALL)

# Link text: anything but brackets, or one level of nested `[...]`.
_TEXT = r"((?:[^\[\]\\]|\\.|\[[^\[\]]*\])*)"
# `[text](target "title")` and `![alt](src)`. The target may hold one level of balanced
# parentheses, or be wrapped in `<...>`.
_INLINE_LINK_RE = re.compile(
    r"(?<!\\)\[" + _TEXT + r"\]\(\s*(<[^>\n]*>|(?:[^()\s]|\([^()\s]*\))*)"
    r"(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^()]*\)))?\s*\)"
)
# `[text][key]`, `[text] [key]` and the collapsed `[text][]` — Python-Markdown accepts at
# most one whitespace character between the two bracket pairs.
_REF_USE_RE = re.compile(r"(?<![\\\]])\[" + _TEXT + r"\]\s?\[([^\[\]]*)\](?!\()")
# `[key]: target "optional title"` — the whole line, as Python-Markdown requires; a
# bracketed citation key that happens to open a prose line is not a definition.
_REF_DEF_RE = re.compile(
    r"^ {0,3}\[([^\[\]]+)\]:[ \t]*<?(\S+?)>?(?:[ \t]+(?:\"[^\"]*\"|'[^']*'|\([^()]*\)))?[ \t]*$",
    re.MULTILINE,
)
# Python-Markdown, unlike CommonMark, needs no space after the hashes but does need them in
# column 0: `#315's ...` opening a line is a heading on the published site and gets an id,
# while `  #394 and ...` on an indented continuation line is prose.
_ATX_RE = re.compile(r"^(#{1,6})(?!#)[ \t]*(.*?)(?:[ \t]+#+)?[ \t]*$")
_SETEXT_RE = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_HTML_ID_RE = re.compile(r"<[A-Za-z][^>]*?\s(?:id|name)\s*=\s*[\"']([^\"']+)[\"']", re.DOTALL)
_SCHEME_RE = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.-]*:|//)")


def _blank(match: re.Match[str]) -> str:
    """Replace a match with spaces, keeping its newlines so line numbers survive."""
    return re.sub(r"[^\n]", " ", match.group(0))


@dataclass(frozen=True)
class _Doc:
    """One page, parsed twice: blocks blanked (for headings) and code blanked (for links)."""

    path: Path
    blocks: str  # fenced blocks and HTML comments blanked
    prose: str  # additionally, inline code spans blanked

    def line_of(self, offset: int) -> int:
        return self.prose.count("\n", 0, offset) + 1


def _unquote(line: str) -> str:
    """The line with any blockquote markers removed."""
    return _QUOTE_RE.sub("", line, count=1)


def _blank_fences(text: str) -> str:
    """Blank every fenced block, fence lines included.

    A fence counts at any indentation and inside a blockquote, so code an author fenced
    is never link-checked, but only once a closing fence of the same character (and at
    least the same length, with nothing after it) is found: an opener that is never
    closed is not a fence to Python-Markdown, so it does not swallow the rest of the page.
    """
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        m = _FENCE_OPEN_RE.match(_unquote(lines[i]))
        if m and not (m.group(2)[0] == "`" and "`" in m.group(3)):
            char, length = m.group(2)[0], len(m.group(2))
            for j in range(i + 1, len(lines)):
                c = _FENCE_CLOSE_RE.match(_unquote(lines[j]))
                if c and c.group(1)[0] == char and len(c.group(1)) >= length:
                    lines[i : j + 1] = [""] * (j + 1 - i)
                    i = j
                    break
        i += 1
    return "\n".join(lines)


@cache
def _doc(path: Path) -> _Doc:
    blocks = _COMMENT_RE.sub(_blank, _blank_fences(path.read_text(encoding="utf-8")))
    return _Doc(path, blocks, _CODE_SPAN_RE.sub(_blank, blocks))


def _pages() -> list[Path]:
    return sorted(p for p in DOCS.rglob("*.md") if p not in _EXCLUDED)


def _rel(path: Path) -> str:
    return path.relative_to(DOCS.parent).as_posix()


def _norm_key(key: str) -> str:
    return " ".join(key.split()).lower()


def _slugify(value: str) -> str:
    """``markdown.extensions.toc.slugify`` with its default ``-`` separator."""
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^\w\s-]", "", value).strip().lower()
    return re.sub(r"[-\s]+", "-", value)


def _heading_text(raw: str) -> str:
    """The plain text the toc extension slugifies: rendered heading, tags stripped."""
    parts = re.split(r"(`+)(.+?)\1", raw)
    text = []
    # re.split with two groups yields [prose, ticks, code, prose, ticks, code, ...].
    for i in range(0, len(parts), 3):
        prose = parts[i]
        prose = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", prose)  # links / images -> text
        prose = re.sub(r"!?\[([^\]]*)\]\s?\[[^\]]*\]", r"\1", prose)  # reference links
        prose = re.sub(r"<[^>]+>", "", prose)  # inline HTML
        prose = re.sub(r"\\(.)", r"\1", prose)  # backslash escapes
        prose = prose.replace("*", "")
        prose = re.sub(r"(?<!\w)_+|_+(?!\w)", "", prose)  # emphasis, not snake_case
        text.append(html.unescape(prose))
        if i + 2 < len(parts):
            text.append(parts[i + 2].strip())
    return "".join(text)


@cache
def _anchors(path: Path) -> frozenset[str]:
    """Every fragment a page serves: heading slugs and inline ``id``/``name`` anchors."""
    doc = _doc(path)
    ids: set[str] = set()
    lines = [_unquote(line) for line in doc.blocks.split("\n")]
    for i, line in enumerate(lines):
        if m := _ATX_RE.match(line):
            raw = m.group(2)
        elif (
            _SETEXT_RE.match(line)
            and i > 0
            and lines[i - 1].strip()
            and not _ATX_RE.match(lines[i - 1])
            and not _SETEXT_RE.match(lines[i - 1])
        ):
            raw = lines[i - 1].strip()
        else:
            continue
        slug = _slugify(_heading_text(raw))
        while slug in ids or not slug:
            m2 = re.match(r"^(.*)_([0-9]+)$", slug)
            slug = f"{m2.group(1)}_{int(m2.group(2)) + 1}" if m2 else f"{slug}_1"
        ids.add(slug)
    ids.update(m.group(1) for m in _HTML_ID_RE.finditer(doc.prose))
    return frozenset(ids)


def _definitions(doc: _Doc) -> set[str]:
    return {_norm_key(m.group(1)) for m in _REF_DEF_RE.finditer(doc.prose)}


def _target_problem(source: Path, target: str) -> str | None:
    """Why ``target`` (as written in ``source``) does not resolve, or None if it does."""
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    if not target or _SCHEME_RE.match(target):
        return None
    if target.startswith("/"):
        return "absolute path (mkdocs resolves links relative to the page)"
    path_part, _, fragment = target.partition("#")
    path_part = path_part.split("?", 1)[0]
    if path_part:
        resolved = (source.parent / path_part).resolve()
        try:
            resolved.relative_to(DOCS)
        except ValueError:
            return "target is outside docs/, so it is not part of the published site"
        if resolved in _EXCLUDED:
            return "target is excluded from the site by mkdocs.yml exclude_docs"
        if not resolved.exists():
            return "no such file"
    else:
        resolved = source
    is_page = resolved.suffix == ".md" and resolved.is_file()
    if fragment and is_page and fragment not in _anchors(resolved):
        return f"no heading or <a id> anchor '#{fragment}' in {_rel(resolved)}"
    return None


def _undefined_references(path: Path) -> list[str]:
    doc = _doc(path)
    defined = _definitions(doc)
    out = []
    for m in _REF_USE_RE.finditer(doc.prose):
        key = _norm_key(m.group(2) or m.group(1))
        if key not in defined:
            out.append(
                f"{_rel(path)}:{doc.line_of(m.start())}: reference-style link "
                f"{' '.join(m.group(0).split())} has no [{m.group(2) or m.group(1)}]: definition "
                "in this file, so it renders as literal text"
            )
    return out


def _broken_targets(path: Path) -> list[str]:
    doc = _doc(path)
    out = []
    links = [(m.start(), m.group(2)) for m in _INLINE_LINK_RE.finditer(doc.prose)]
    links += [(m.start(), m.group(2)) for m in _REF_DEF_RE.finditer(doc.prose)]
    for start, target in links:
        if problem := _target_problem(path, target):
            out.append(f"{_rel(path)}:{doc.line_of(start)}: link to '{target}': {problem}")
    return out


# --- the corpus ------------------------------------------------------------------------


def test_walk_covers_the_site_and_skips_the_prd() -> None:
    """Sanity: the walk is the published corpus (guards a broken DOCS path or glob)."""
    pages = _pages()
    assert len(pages) >= 50, f"expected the docs corpus; found only {len(pages)} pages"
    assert DOCS / "analysis" / "parity-gallery.md" in pages
    assert DOCS / "PRD.md" not in pages


def test_no_undefined_reference_style_links() -> None:
    """No ``[text][key]`` without a ``[key]: …`` definition — mkdocs cannot see these."""
    problems = [p for page in _pages() for p in _undefined_references(page)]
    assert not problems, "\n".join(problems)


def test_internal_link_targets_and_anchors_resolve() -> None:
    """Every relative link names a real page in the site and a real anchor on it."""
    problems = [p for page in _pages() for p in _broken_targets(page)]
    assert not problems, "\n".join(problems)


# --- the checker itself ----------------------------------------------------------------


def _check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pages: dict[str, str]) -> list[str]:
    """Run both checks over a synthetic ``docs/`` tree under ``tmp_path``."""
    docs = tmp_path / "docs"
    for name, body in pages.items():
        (docs / name).parent.mkdir(parents=True, exist_ok=True)
        (docs / name).write_text(body, encoding="utf-8")
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "DOCS", docs)
    monkeypatch.setattr(module, "_EXCLUDED", {docs / "PRD.md"})
    _doc.cache_clear()
    _anchors.cache_clear()
    try:
        files = sorted(p for p in docs.rglob("*.md") if p.name != "PRD.md")
        return [p for f in files for p in _undefined_references(f) + _broken_targets(f)]
    finally:
        _doc.cache_clear()
        _anchors.cache_clear()


def test_flags_an_undefined_reference_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = _check(tmp_path, monkeypatch, {"a.md": "# A\n\nSee [McCann 2010][mccann2010].\n"})
    assert out == [
        "docs/a.md:3: reference-style link [McCann 2010][mccann2010] has no [mccann2010]: "
        "definition in this file, so it renders as literal text"
    ]


def test_defined_references_and_code_are_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (
        "# A\n\n"
        "See [McCann 2010][MCCANN2010] and [McCann 2010][].\n\n"
        "[mccann2010]: https://doi.org/10.1016/j.bpj.2010.04.063\n"
        "[McCann 2010]: #a\n\n"
        "Inline `data['condition_id'][mask] = x` and ``a[b][c]`` are code.\n\n"
        "```python\nx = table['a'][mask]\n```\n\n"
        "<!-- [commented][out] -->\n"
        "Citations [Hellenkamp2018], [Lee2005] are plain text, not references.\n"
    )
    assert _check(tmp_path, monkeypatch, {"a.md": body}) == []


def test_adjacent_bracket_pairs_form_a_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``[A][B]`` and ``[A] [B]`` are both reference uses to Python-Markdown."""
    out = _check(tmp_path, monkeypatch, {"a.md": "# A\n\n[Hellenkamp2018][Lee2005] and [X] [Y]\n"})
    assert len(out) == 2 and "lee2005" in out[0].lower() and "[Y]" in out[1]


def test_flags_missing_pages_and_anchors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pages = {
        "a.md": (
            "# Page A\n\n## The `tdp` view & more\n\n## Dup\n\n## Dup\n\n"
            '- <a id="mccann2010"></a>**[McCann 2010]** ...\n\n'
            "[ok](#page-a) [ok](#the-tdp-view-more) [ok](#dup_1) [ok](#mccann2010) "
            "[ok](sub/b.md#section-b) [ok](https://example.org/x.md#nope)\n"
            "[bad](#nope)\n[bad](sub/missing.md)\n[bad](sub/b.md#nope)\n[bad](PRD.md)\n"
            "[bad](../outside.md)\n"
        ),
        "sub/b.md": "# B\n\n## Section B\n",
        "PRD.md": "# PRD\n",
    }
    out = _check(tmp_path, monkeypatch, pages)
    assert [o.split(":", 2)[1] for o in out] == ["12", "13", "14", "15", "16"], out
    assert "'#nope'" in out[0] and "no such file" in out[1] and "'#nope' in docs/sub/b.md" in out[2]


def test_deleting_an_inline_anchor_breaks_its_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The #207 bibliography form: removing ``<a id>`` leaves its inline links dangling."""
    with_anchor = '# G\n\nPer [McCann 2010](#mccann2010).\n\n- <a id="mccann2010"></a>McCann 2010\n'
    assert _check(tmp_path, monkeypatch, {"g.md": with_anchor}) == []
    without = with_anchor.replace('<a id="mccann2010"></a>', "")
    out = _check(tmp_path, monkeypatch, {"g.md": without})
    assert len(out) == 1 and out[0].startswith("docs/g.md:3:") and "#mccann2010" in out[0]

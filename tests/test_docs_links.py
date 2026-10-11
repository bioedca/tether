# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Internal links in the docs corpus resolve, including the ones mkdocs cannot see.

``mkdocs build --strict`` only validates links it can *resolve*. A reference-style use
``[text][key]`` whose ``key`` has no ``[key]: …`` definition is not a link at all to
Python-Markdown — it renders as the literal source text — so there is no target for the
build to complain about. That is how the parity gallery reached the published site
showing ``[McCann 2010][mccann2010]`` fourteen times with the required ``docs-build``
gate green (#183, repaired by hand in #207). This module closes that blind spot, and
re-checks the targets the build does see so the same guard runs on all three operating
systems rather than only on the Linux ``docs-build`` job. Exactly these are enforced:

* every ``[text][key]`` (and collapsed ``[text][]``) has a definition in the same file;
* every Markdown link, image and reference definition with a relative target names a
  file that exists inside the published ``docs/`` tree — not a directory, which mkdocs
  leaves unrewritten and ``--strict`` rejects, and not the excluded ``PRD.md``;
* every ``#fragment`` on such a link names a heading slug or an inline ``<a id="…">``
  anchor in the target page — the bibliography-anchor form #207 standardised on;
* a raw HTML ``<a href>`` is either a URL with a scheme, or a same-page ``#fragment``
  that resolves; any other target is refused, because mkdocs does not rewrite raw HTML.

External URLs are out of scope here (the advisory ``external-links`` job checks them).

Fenced blocks, inline code spans and HTML comments are blanked before any of this, so
``["condition_id"][mask]`` inside a code span is code, not a reference. The walk is
``docs/**/*.md`` minus ``docs/PRD.md``, which ``mkdocs.yml`` excludes from the site.

Stdlib only (``re`` + ``pathlib``), like ``tests/test_adr_index.py``: ``python-markdown``
is not in the base conda-lock, so it cannot be imported on the 3-OS ``test`` matrix. The
parse is therefore a structural approximation of the Python-Markdown rules that matter
here — a single optional space between ``]`` and ``[`` still forms a reference, link text
may nest brackets to any depth, ids are matched case-insensitively, a ``#`` line or a
``[key]:`` line inside a raw HTML block (which Python-Markdown passes through unparsed, as
``md_in_html`` is off) is neither a heading nor a definition, and heading slugs follow
``markdown.extensions.toc.slugify`` with its ``_1``/``_2`` de-duplication — cross-checked
against a real ``mkdocs build`` of every page. Where it differs from the renderer it errs
loud, never silent:

* a fence inside a blockquote is code here, though Python-Markdown's ``fenced_code`` does
  not recognise it and renders it as prose, so its ``#`` lines are not heading anchors;
* an indented (four-space) code block is *not* recognised as code, so a ``[a][b]`` inside
  one is reported as a reference (none exists in ``docs/`` today; tracked in #515).
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

# What follows a link's `[text]` (see `_link_text_end` for the text itself):
# `(target "title")`, where the target may hold one level of balanced parentheses or be
# wrapped in `<...>`, for `[text](…)` and `![alt](…)` …
_INLINE_TAIL_RE = re.compile(
    r"\(\s*(<[^>\n]*>|(?:[^()\s]|\([^()\s]*\))*)"
    r"(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^()]*\)))?\s*\)"
)
# … and `[key]` for `[text][key]`, `[text] [key]` and the collapsed `[text][]` —
# Python-Markdown accepts at most one whitespace character between the two bracket pairs.
_REF_TAIL_RE = re.compile(r"\s?\[([^\[\]]*)\](?!\()")
_BLANK_LINE_RE = re.compile(r"\n[ \t]*\n")
# A raw HTML block opens with a block-level start tag at most three spaces into a line —
# not inside a blockquote — and Python-Markdown parses no Markdown inside it.
# `markdown.util.BLOCK_LEVEL_ELEMENTS` (3.11) minus `hr`, which its HTMLExtractor treats
# as empty, so it opens nothing; the tag-name pattern is `html.parser`'s.
_BLOCK_TAGS = frozenset(
    ("address", "article", "aside", "blockquote", "details", "div", "dl", "fieldset")
    + ("figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6")
    + ("header", "hgroup", "main", "menu", "nav", "ol", "p", "pre", "section", "table", "ul")
    + ("canvas", "colgroup", "dd", "body", "dt", "group", "html", "iframe", "li", "legend")
    + ("math", "map", "noscript", "output", "object", "option", "progress", "script", "center")
    + ("style", "summary", "tbody", "td", "textarea", "tfoot", "th", "thead", "tr", "video")
)
_RAW_OPEN_RE = re.compile(r"^[ \t]{0,3}<([A-Za-z][^\t\n\r\f />\x00]*)", re.MULTILINE)
_TAG_OPEN_RE = re.compile(r"<([A-Za-z][^\t\n\r\f />\x00]*)")
# `[key]: target "optional title"`, as Python-Markdown's ReferenceProcessor reads it: the
# target may sit on the next line, and so may the title, but nothing else may follow the
# target on its line — so a bracketed citation key that happens to open a prose line
# (`[Götz2022]: eleven tools …`) is not a definition.
_REF_DEF_RE = re.compile(
    r"^ {0,3}\[([^\[\]]+)\]:[ \t]*\n?[ \t]*<?([^\s>]+)>?[ \t]*"
    r"(?:\n[ \t]*)?(?:\"[^\"\n]*\"|'[^'\n]*'|\([^()\n]*\))?[ \t]*$",
    re.MULTILINE,
)
# Python-Markdown, unlike CommonMark, needs no space after the hashes but does need them in
# column 0: `#315's ...` opening a line is a heading on the published site and gets an id,
# while `  #394 and ...` on an indented continuation line is prose.
_ATX_RE = re.compile(r"^(#{1,6})(?!#)[ \t]*(.*?)(?:[ \t]+#+)?[ \t]*$")
_SETEXT_RE = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_HTML_ID_RE = re.compile(r"<[A-Za-z][^>]*?\s(?:id|name)\s*=\s*[\"']([^\"']+)[\"']", re.DOTALL)
_HTML_HREF_RE = re.compile(r"<a\s[^>]*?\bhref\s*=\s*[\"']([^\"']*)[\"']", re.DOTALL | re.IGNORECASE)
# The sources MkDocs turns into a directory's index.html (named in the error as the fix).
_INDEX_PAGES = ("index.md", "README.md")
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
    raw_html: tuple[tuple[int, int], ...]  # spans of raw HTML blocks, as offsets

    def line_of(self, offset: int) -> int:
        return self.prose.count("\n", 0, offset) + 1

    def markdown(self, text: str) -> str:
        """``text`` (``blocks`` or ``prose``) with the raw HTML blocks blanked too.

        What is left is what Python-Markdown parses as Markdown: a ``# Ghost`` line inside
        ``<div>…</div>`` is not a heading and a ``[k]: …`` line there is not a definition.
        Raw HTML's own ``id``/``name`` attributes and ``<a href>`` are still read from the
        unblanked views, since the renderer passes them through.
        """
        for start, end in self.raw_html:
            text = text[:start] + re.sub(r"[^\n]", " ", text[start:end]) + text[end:]
        return text


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


def _raw_html_blocks(text: str) -> tuple[tuple[int, int], ...]:
    """Spans of the raw HTML blocks in ``text``, as Python-Markdown 3.11's HTMLExtractor cuts them.

    A block opens with a block-level start tag at the start of a line (up to three spaces in)
    and runs to the end tag that closes it, counting nested tags of the same name, or to the
    end of the page if none does — an unclosed ``<div>`` swallows the rest. A block-level
    start tag later on the closing line opens the next block at once. Where this misjudges,
    it treats too much as raw rather than too little, so a heading is lost and a link to it
    is reported: loud, never silent.
    """
    spans = []
    pos = 0
    while (m := _RAW_OPEN_RE.search(text, pos)) is not None:
        if m.group(1).lower() not in _BLOCK_TAGS:
            pos = m.end()
            continue
        start = m.start()
        while True:
            tag = re.escape(m.group(1))
            tag_re = re.compile(rf"<(/?){tag}(?=[\s/>])[^>]*>", re.IGNORECASE)
            depth, end = 0, len(text)
            for t in tag_re.finditer(text, m.start(1) - 1):
                depth += -1 if t.group(1) else 1
                if depth == 0:
                    end = t.end()
                    break
            line_end = text.find("\n", end)
            line_end = len(text) if line_end == -1 else line_end
            tail = next(
                (
                    t
                    for t in _TAG_OPEN_RE.finditer(text, end, line_end)
                    if t.group(1).lower() in _BLOCK_TAGS
                ),
                None,
            )
            if tail is None:
                break
            m = tail
        spans.append((start, line_end))
        pos = line_end
    return tuple(spans)


@cache
def _doc(path: Path) -> _Doc:
    blocks = _COMMENT_RE.sub(_blank, _blank_fences(path.read_text(encoding="utf-8")))
    return _Doc(path, blocks, _CODE_SPAN_RE.sub(_blank, blocks), _raw_html_blocks(blocks))


def _link_text_end(text: str, start: int) -> int | None:
    """Offset just past the ``]`` that closes the ``[`` at ``start``, or None.

    Python-Markdown's ``LinkInlineProcessor.getText`` counts brackets to any depth, so
    ``[[array[i]] result][key]`` is one reference use whose text is ``[array[i]] result``.
    A regular expression cannot nest without bound, so this counts too. A backslash-escaped
    bracket does not count (the escape processor runs first), and the text cannot run past
    a blank line, which ends the paragraph that inline parsing works within.
    """
    depth = 0
    i = start
    while i < len(text):
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return i + 1
        elif c == "\n" and _BLANK_LINE_RE.match(text, i):
            return None
        i += 1
    return None


def _bracketed(
    text: str, tail: re.Pattern[str], not_after: str
) -> list[tuple[int, int, re.Match[str]]]:
    """``(start, text_end, tail_match)`` for each ``[text]`` that ``tail`` follows.

    Scans left to right like ``re.finditer``: a ``[`` preceded by a character in
    ``not_after`` is skipped, a ``[`` whose text does not close or is not followed by
    ``tail`` is retried one character on, and a match resumes the scan after its end.
    """
    out = []
    pos = 0
    while (start := text.find("[", pos)) != -1:
        pos = start + 1
        if start and text[start - 1] in not_after:
            continue
        end = _link_text_end(text, start)
        if end is None or (m := tail.match(text, end)) is None:
            continue
        out.append((start, end, m))
        pos = m.end()
    return out


def _inline_links(prose: str) -> list[tuple[int, str]]:
    """``(start, target)`` for each ``[text](target)`` and ``![alt](src)``."""
    return [(start, m.group(1)) for start, _, m in _bracketed(prose, _INLINE_TAIL_RE, "\\")]


def _ref_uses(prose: str) -> list[tuple[int, str, str, str]]:
    """``(start, source, text, key)`` for each reference-style use; ``key`` may be empty."""
    return [
        (start, prose[start : m.end()], prose[start + 1 : end - 1], m.group(1))
        for start, end, m in _bracketed(prose, _REF_TAIL_RE, "\\]")
    ]


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
    lines = [_unquote(line) for line in doc.markdown(doc.blocks).split("\n")]
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
    return {_norm_key(m.group(1)) for m in _REF_DEF_RE.finditer(doc.markdown(doc.prose))}


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
        if resolved.is_dir():
            # MkDocs only rewrites links to source files: `dir/` is left as is and
            # `--strict` aborts on it ("unrecognized relative link"), index page or not.
            index = [n for n in _INDEX_PAGES if (resolved / n).is_file()]
            hint = f"link {path_part.rstrip('/')}/{index[0]}" if index else "no index page"
            return f"directory link, which mkdocs leaves unresolved ({hint})"
    else:
        resolved = source
    is_page = resolved.suffix == ".md" and resolved.is_file()
    if fragment and is_page and fragment not in _anchors(resolved):
        return f"no heading or <a id> anchor '#{fragment}' in {_rel(resolved)}"
    return None


def _raw_html_problem(source: Path, href: str) -> str | None:
    """Why a raw ``<a href>`` breaks on the site, or None if it does not.

    MkDocs rewrites Markdown links but passes raw HTML through untouched, so a relative
    path there is resolved by the browser against the *rendered* page URL, not against
    the source file. Only a same-page ``#fragment`` (checked) or a URL with a scheme
    (left to the advisory external check) is safe; any other target is refused.
    """
    if not href or _SCHEME_RE.match(href):
        return None
    if href.startswith("#"):
        return _target_problem(source, href)
    return "raw HTML href is not rewritten by mkdocs; write it as a Markdown link"


def _undefined_references(path: Path) -> list[str]:
    doc = _doc(path)
    defined = _definitions(doc)
    out = []
    for start, source, text, key in _ref_uses(doc.prose):
        if _norm_key(key or text) not in defined:
            out.append(
                f"{_rel(path)}:{doc.line_of(start)}: reference-style link "
                f"{' '.join(source.split())} has no [{key or text}]: definition "
                "in this file, so it renders as literal text"
            )
    return out


def _broken_targets(path: Path) -> list[str]:
    doc = _doc(path)
    out = []
    links = _inline_links(doc.prose)
    links += [(m.start(), m.group(2)) for m in _REF_DEF_RE.finditer(doc.prose)]
    for start, target in links:
        if problem := _target_problem(path, target):
            out.append(f"{_rel(path)}:{doc.line_of(start)}: link to '{target}': {problem}")
    for m in _HTML_HREF_RE.finditer(doc.prose):
        if problem := _raw_html_problem(path, m.group(1)):
            out.append(f"{_rel(path)}:{doc.line_of(m.start())}: <a href='{m.group(1)}'>: {problem}")
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


def test_definition_target_and_title_may_sit_on_the_next_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Python-Markdown reads ``[k]:`` + an indented target line as one definition."""
    body = (
        "# A\n\n"
        "See [x][k], [y][t] and [z][g].\n\n"
        "[k]:\n    https://example.org\n"
        '[t]: #a\n    "Title on the next line"\n'
        "[g]:\n    sub/missing.md\n\n"
        "[Götz2022]: eleven tools (14 analyses) is prose, not a definition, so [w][Götz2022]\n"
    )
    out = _check(tmp_path, monkeypatch, {"a.md": body})
    assert len(out) == 2, out
    assert out[0].startswith("docs/a.md:12: reference-style link [w][Götz2022]")
    assert out[1].startswith("docs/a.md:9: link to 'sub/missing.md': no such file")


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


def test_a_directory_link_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """MkDocs leaves ``dir/`` unrewritten and ``--strict`` aborts on it, index page or not."""
    pages = {
        "a.md": "# A\n\n[ok](withreadme/README.md#r) [bad](withreadme/) [bad](bare/)\n",
        "withreadme/README.md": "# R\n",
        "bare/page.md": "# P\n",
    }
    out = _check(tmp_path, monkeypatch, pages)
    assert len(out) == 2, out
    assert "'withreadme/'" in out[0] and "link withreadme/README.md" in out[0]
    assert "'bare/'" in out[1] and "no index page" in out[1]


def test_raw_html_links_are_checked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """MkDocs never rewrites a raw ``<a href>``: a fragment is checked, a path is refused."""
    body = (
        '# A\n\n<a id="x"></a>\n'
        '<a href="#x">ok</a> <a href="https://example.org/">ok</a>\n'
        '<a href="#nope">bad</a>\n<a href="b.md">bad</a>\n'
        '`<a href="b.md">code</a>` is code.\n'
    )
    out = _check(tmp_path, monkeypatch, {"a.md": body, "b.md": "# B\n"})
    assert len(out) == 2, out
    assert out[0].startswith("docs/a.md:5: ") and "'#nope'" in out[0]
    assert out[1].startswith("docs/a.md:6: ") and "Markdown link" in out[1]


def test_link_text_nests_brackets_to_any_depth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Python-Markdown counts brackets without bound, so a deeply nested text is still a link."""
    body = (
        "# A\n\n"
        "See [[array[i]] result][missing] and [[a[b[c]]] d][k].\n\n"
        "Inline [[x[y]] z](nope.md) and [[x[y]] z](#a).\n\n"
        r"Escaped \[ is no bracket: [a \[ b][k]." + "\n\n"
        "A stray [ does not reach past the paragraph.\n\n"
        "[late][missing2]\n\n"
        "[k]: #a\n"
    )
    out = _check(tmp_path, monkeypatch, {"a.md": body})
    assert len(out) == 3, out
    assert out[0].startswith("docs/a.md:3: reference-style link [[array[i]] result][missing]")
    assert out[1].startswith("docs/a.md:11: reference-style link [late][missing2]")
    assert out[2].startswith("docs/a.md:5: link to 'nope.md': no such file")


def test_raw_html_blocks_hold_no_headings_or_definitions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Python-Markdown passes a raw HTML block through unparsed (``md_in_html`` is off)."""
    body = (
        "# Real\n\n"
        "<div>\n# Ghost\n[gk]: #real\n</div>\n\n"
        '<DIV class="x">\n<div>\n</div>\n# Nested Ghost\n</div>\n'
        "# After\n\n"
        "text\n  <details><summary>s</summary>\n\n## Para Ghost\n\n"
        "</details><p>\n# Tail Ghost\n</p>\n\n"
        "<span>\n# Inline Kept\n</span>\n\n"
        "> <div>\n> # Quoted Kept\n> </div>\n\n"
        "<hr>\n# Hr Kept\n\n"
        "[ok](#real) [ok](#after) [ok](#inline-kept) [ok](#quoted-kept) "
        "[ok](#hr-kept) [ok](#in-raw)\n"
        "[bad](#ghost)\n[bad](#nested-ghost)\n[bad](#para-ghost)\n[bad](#tail-ghost)\n"
        "[bad][gk]\n\n"
        '<section>\n<a id="in-raw"></a>\n# Unclosed Ghost\n'
    )
    out = _check(tmp_path, monkeypatch, {"a.md": body})
    assert [o.split(":", 2)[1] for o in out] == ["40", "36", "37", "38", "39"], out
    assert "[bad][gk]" in out[0]
    assert "'#ghost'" in out[1] and "'#nested-ghost'" in out[2]
    assert "'#para-ghost'" in out[3] and "'#tail-ghost'" in out[4]

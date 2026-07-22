# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Drift guard for the repository-wide exact-head review policy."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

POLICY_SURFACES = (
    Path("AGENTS.md"),
    Path("CONTRIBUTING.md"),
    Path("docs/PRD.md"),
    Path("docs/adr/0052-concurrent-agent-swarm-coordination.md"),
    Path(".agents/skills/run-issue-swarm/SKILL.md"),
    Path(".github/pull_request_template.md"),
)
DETAILED_POLICY_SURFACES = POLICY_SURFACES[:-1]
SHORTCUT = Path(".agents/skills/run-issue-swarm/agents/openai.yaml")
COPILOT_SURFACES = (*POLICY_SURFACES, SHORTCUT)

REVIEWER_SOURCE_RE = re.compile(
    r"\b(?:review\w*|walkthrough)\b.{0,180}\bfrom\b.{0,80}\bcodex\b"
    r".{0,80}\bor\b.{0,80}\b(?:coderabbit|coderabbitai)\b",
    re.IGNORECASE,
)
HEAD_BINDING_RE = re.compile(
    r"\b(?:exact|final)(?:[- ]reviewed)?[- ]head(?:[- ]sha)?\b", re.IGNORECASE
)
REVIEW_EVIDENCE_RE = re.compile(r"\b(?:review\w*|walkthrough)\b", re.IGNORECASE)
REVIEW_OBLIGATION_RE = re.compile(
    r"(?:\b(?:requires?|needs?|must|shall)\s+(?:an?\s+)?substantive\b"
    r"|\baccept only\s+(?:an?\s+)?substantive\b"
    r"|\brequired\s+substantive\b)",
    re.IGNORECASE,
)
REVIEW_NEGATION_RES = (
    re.compile(
        r"\b(?:substantive\s+)?(?:final|exact)[- ]head\b.{0,80}"
        r"\b(?:is|are)\s+(?:not\s+required|optional)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:does|do)\s+not\s+(?:require|need)\b.{0,120}"
        r"\b(?:review\w*|walkthrough)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bneed\s+not\b.{0,120}\b(?:review\w*|walkthrough)\b", re.IGNORECASE),
    re.compile(
        r"\bno\s+(?:substantive\s+)?(?:final|exact)[- ]head\b.{0,40}"
        r"\b(?:review\w*|walkthrough)\b.{0,40}\brequired\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bsubstantive\b.{0,200}\b(?:may be skipped|recommended only|not mandatory)\b",
        re.IGNORECASE,
    ),
)
COPILOT_OPTIONAL_RE = re.compile(
    r"(?:\bcopilot(?: cloud agent)?\b.{0,32}\b(?:is\s+)?(?:optional|best[- ]effort)\b"
    r"|\boptional\b.{0,32}\bcopilot\b"
    r"|\bcopilot\b.{0,64}\b(?:never|does not|must not)\s+blocks?\b)",
    re.IGNORECASE,
)
MANDATORY_COPILOT_RES = (
    re.compile(
        r"\b(?:must|shall)\s+(?:request|run|use|await|obtain|invoke)\b.{0,32}"
        r"\bcopilot\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bcopilot\b.{0,48}\b(?:must|shall)\s+"
        r"(?:run|complete|finish|precede\w*|be requested|be used|be awaited)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:required|mandatory)\s+(?:use of |request for |review by )?copilot\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bcopilot\b.{0,48}\b(?:is|are|remains?)\s+(?:a\s+)?"
        r"(?:required|mandatory|gate|prerequisite)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:gate|prerequisite)\b.{0,32}\bcopilot\b", re.IGNORECASE),
    re.compile(
        r"\bcopilot\b.{0,32}\bis\b.{0,24}\b(?:gate|prerequisite)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:request\s+)?copilot\b.{0,32}\b(?:first|precede\w*)\b", re.IGNORECASE),
    re.compile(
        r"\bcopilot\b(?:(?!\bnever\b|\bdoes\s+not\b|\bmust\s+not\b).){0,64}"
        r"\bblocks?\b",
        re.IGNORECASE,
    ),
)
LOW_STANDARD_EITHER_RE = re.compile(
    r"\blow(?:\s+and|/)\s*standard\b.{0,80}\b(?:may|choose|select|use)\b.{0,80}"
    r"(?:\beither\b|\bproviders\b|\bcodex\b.{0,80}\bor\b.{0,80}\bcoderabbit\b)",
    re.IGNORECASE,
)
LOW_STANDARD_NEGATION_RE = re.compile(
    r"\blow(?:\s+and|/)\s*standard\b.{0,100}"
    r"\b(?:must|may|do|need|shall|can)\s+not\b|"
    r"\blow(?:\s+and|/)\s*standard\b.{0,100}\bno longer\b",
    re.IGNORECASE,
)
HIGH_CODERABBIT_RE = re.compile(
    r"\bhigh(?:[-/ ]risk|/load[- ]bearing|[-/ ]load[- ]bearing)?\b.{0,240}"
    r"\brequires?\b.{0,32}\bcoderabbit\b",
    re.IGNORECASE,
)
HIGH_CODERABBIT_NEGATION_RE = re.compile(
    r"\bhigh(?:[-/ ]risk|/load[- ]bearing|[-/ ]load[- ]bearing)?\b.{0,240}"
    r"\b(?:does|do|need|must|shall)\s+not\s+require\b.{0,48}\bcoderabbit\b|"
    r"\bhigh(?:[-/ ]risk|/load[- ]bearing|[-/ ]load[- ]bearing)?\b.{0,240}"
    r"\bno longer requires?\b.{0,48}\bcoderabbit\b",
    re.IGNORECASE,
)
HIGH_CODERABBIT_FALLBACK_RE = re.compile(
    r"\bhigh(?:[-/ ]risk|/load[- ]bearing|[-/ ]load[- ]bearing)?\b.{0,280}"
    r"\bcoderabbit\b.{0,120}\b(?:unless|except)\b|"
    r"\bhigh(?:[-/ ]risk|/load[- ]bearing|[-/ ]load[- ]bearing)?\b.{0,280}"
    r"\bcoderabbit\b.{0,160}\bcodex\b.{0,48}\b(?:substitut\w*|fallback)\b",
    re.IGNORECASE,
)
CODERABBIT_QUOTA_RE = re.compile(
    r"\bcoderabbit(?:'s)?\b.{0,40}\bquota\b.{0,64}"
    r"\b(?:blocks?|occupies|does so)\b.{0,48}\bonly when\b.{0,64}"
    r"\brequired or selected\b",
    re.IGNORECASE,
)
COPILOT_QUOTA_RE = re.compile(r"\bcopilot\b.{0,96}\bquota\b.{0,32}\bnever blocks?\b", re.IGNORECASE)
CONVERSATION_SCOPE_RE = re.compile(
    r"\b(?:every|all|each)\s+(?:conversation(?:s|/threads?)?|review threads?|threads?)\b",
    re.IGNORECASE,
)
FINDING_SCOPE_RE = re.compile(r"\b(?:every|all|each)\s+actionable\s+findings?\b", re.IGNORECASE)
RESOLUTION_RE = re.compile(r"\bresolve(?:s|d)?\b", re.IGNORECASE)
RESOLUTION_NEGATION_RE = re.compile(
    r"\b(?:need not|do not|must not|may|can)\s+resolve\b|"
    r"\b(?:not|never)\s+(?:be\s+)?resolved\b|\bremain(?:s)?\s+unresolved\b",
    re.IGNORECASE,
)
PR_DIFF_RE = re.compile(
    r"(?:\bpr diff\b.{0,48}\b(?:review\w*|walkthrough)\b|"
    r"\b(?:review\w*|walkthrough)\b.{0,48}\bpr diff\b)",
    re.IGNORECASE,
)
AUTHOR_LOCAL_RE = re.compile(r"\b(?:author[- ]side|local coderabbit|local review)\b", re.IGNORECASE)
STATUS_ONLY_RE = re.compile(
    r"\b(?:status(?:/check)?[- ]only|status/check alone|status-only output|"
    r"green or status-only|green/status-only)\b",
    re.IGNORECASE,
)
UNAVAILABLE_RE = re.compile(r"\b(?:unavailability|unavailable)\b", re.IGNORECASE)
SUMMARY_WITHOUT_DIFF_RE = re.compile(
    r"\bsummary\b.{0,48}\bwithout\b.{0,48}\bdiff walkthrough\b", re.IGNORECASE
)
EVIDENCE_REJECTION_RE = re.compile(
    r"\b(?:does not|never)\s+satisf(?:y|ies)\b|"
    r"\bis not\b.{0,32}\bindependent review evidence\b",
    re.IGNORECASE,
)
FALLBACK_ACCEPTANCE_RES = (
    re.compile(
        r"\b(?:if|when)\b.{0,32}\bunavail\w*\b.{0,80}\b(?:author[- ]side|local)\b"
        r".{0,80}\bsatisf(?:y|ies)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:status|check|denial|summary)\b"
        r"(?:(?!\bnever\b|\bdoes\s+not\b).){0,80}\bsatisf(?:y|ies)\b.{0,40}\bgate\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:provider\s+)?unavail\w*\b.{0,80}"
        r"\b(?:waives?|bypasses?|allows?\b.{0,32}\bwithout review|proceed\w*\b.{0,32}"
        r"\bwithout review)\b",
        re.IGNORECASE,
    ),
)
WORKER_PR_READY_RE = re.compile(r"\bworkers?\b.{0,120}\bpr-ready\b", re.IGNORECASE)
WORKER_NO_MERGE_RE = re.compile(
    r"\b(?:never merge|nobody merges|do not enter the merge path)\b",
    re.IGNORECASE,
)
COORDINATOR_ONLY_RE = re.compile(
    r"\b(?:only\b.{0,40}\bcoordinator|coordinator\b.{0,40}\balone)\b", re.IGNORECASE
)
EXPLICIT_AUTHORITY_RE = re.compile(
    r"\bexplicit\w*\b.{0,80}\b(?:authority|authorized)\b|"
    r"\bauthorized coordinator\b",
    re.IGNORECASE,
)
AUTHORITY_NEGATION_RE = re.compile(
    r"\b(?:explicit\w*\s+)?(?:merge\s+)?authority\b.{0,48}"
    r"\b(?:is|remains?)\s+not\s+required\b|"
    r"\b(?:no|without)\s+(?:explicit\w*\s+(?:merge\s+)?|merge\s+)authority\b",
    re.IGNORECASE,
)
EXACT_HEAD_BASE_RE = re.compile(
    r"\bexact(?:[- ]head/[- ]?exact[- ]base|[- ]head[-/ ]exact[- ]base)\b|"
    r"\bexact\b.{0,100}\bhead\b.{0,100}\bbase\b",
    re.IGNORECASE,
)
EXACT_HEAD_BASE_NEGATION_RE = re.compile(
    r"\bexact\b.{0,48}\bhead\b.{0,48}\bbase\b.{0,64}"
    r"\b(?:need not|do not need to|may fail to|may not)\s+(?:match|bind)\b|"
    r"\bhead\b.{0,48}\bbase\b.{0,64}\bmay differ\b",
    re.IGNORECASE,
)
WORKER_MERGE_CONTRADICTION_RE = re.compile(
    r"\bworkers?\b.{0,64}\b(?:may|can|shall|must)\s+(?:also\s+)?merge\b",
    re.IGNORECASE,
)

EXPECTED_SHORTCUT_PROMPT = (
    "/goal $run-issue-swarm Run 4 isolated workers through merge. I explicitly authorize "
    "the coordinator to perform exact-head/exact-base guarded squash merges after all "
    "required gates and reviews pass; keep refilling until the ready queue is empty or I "
    "tell the swarm to stop."
)


def _read(relative_path: Path) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _visible_units(text: str) -> tuple[str, ...]:
    """Collapse visible Markdown paragraphs and list items into policy units."""
    visible = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    visible = re.sub(r"(?ms)^[ \t]*(?:```|~~~).*?^[ \t]*(?:```|~~~)[ \t]*$", "", visible)
    visible = re.sub(r"(?is)<(?:del|s)>.*?</(?:del|s)>", "", visible)
    visible = re.sub(r"~~.*?~~", "", visible, flags=re.DOTALL)
    visible = re.sub(r"(?m)^[ \t]*>.*$", "", visible)
    units: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            units.append(" ".join(current))
            current.clear()

    for raw_line in visible.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            flush()
        elif re.match(r"^(?:[-*+] |\d+[.)] )", line):
            flush()
            current.append(line)
        else:
            current.append(line)
    flush()
    return tuple(units)


def _is_required_review_unit(unit: str) -> bool:
    return bool(
        "substantive" in unit.casefold()
        and REVIEW_EVIDENCE_RE.search(unit)
        and HEAD_BINDING_RE.search(unit)
        and REVIEWER_SOURCE_RE.search(unit)
        and REVIEW_OBLIGATION_RE.search(unit)
        and not any(pattern.search(unit) for pattern in REVIEW_NEGATION_RES)
    )


def _is_evidence_exclusion_unit(unit: str) -> bool:
    return bool(
        PR_DIFF_RE.search(unit)
        and AUTHOR_LOCAL_RE.search(unit)
        and STATUS_ONLY_RE.search(unit)
        and re.search(r"\bdenial\b", unit, re.IGNORECASE)
        and UNAVAILABLE_RE.search(unit)
        and SUMMARY_WITHOUT_DIFF_RE.search(unit)
        and EVIDENCE_REJECTION_RE.search(unit)
        and not any(pattern.search(unit) for pattern in FALLBACK_ACCEPTANCE_RES)
    )


def test_policy_surfaces_bind_substantive_review_to_final_head() -> None:
    """Do not let scattered keywords masquerade as the independent-review rule."""
    for relative_path in POLICY_SURFACES:
        units = _visible_units(_read(relative_path))
        matches = [unit for unit in units if _is_required_review_unit(unit)]
        assert matches, (
            f"{relative_path} needs one policy unit requiring a substantive final-head "
            "review from Codex or CodeRabbit"
        )


def test_policy_surfaces_reject_non_walkthrough_substitutes() -> None:
    for relative_path in DETAILED_POLICY_SURFACES:
        units = _visible_units(_read(relative_path))
        assert any(_is_evidence_exclusion_unit(unit) for unit in units), (
            f"{relative_path} must reject local/author, status-only, denial, unavailable, "
            "and summary-only review substitutes"
        )
        visible = " ".join(units)
        assert not any(pattern.search(visible) for pattern in FALLBACK_ACCEPTANCE_RES), (
            f"{relative_path} explicitly allows a disallowed review substitute"
        )


def test_evidence_matcher_rejects_local_unavailable_fallback() -> None:
    positive = (
        "A substantive PR diff walkthrough is required. Author-side or local review, "
        "status-only output, denial, provider unavailability, or a summary without a diff "
        "walkthrough never satisfies the gate."
    )
    negatives = (
        positive + " If unavailable, local CodeRabbit satisfies the gate.",
        positive + " Provider unavailability waives the review gate.",
    )
    assert _is_evidence_exclusion_unit(positive)
    assert all(not _is_evidence_exclusion_unit(unit) for unit in negatives)


def test_core_review_matcher_rejects_negated_policy() -> None:
    positive = (
        "Every lane requires a substantive review bound to the final head SHA from "
        "Codex or CodeRabbit."
    )
    negatives = (
        positive + " That substantive final-head review is not required.",
        "Every lane does not require a substantive final-head review from Codex or CodeRabbit.",
        "Every lane need not obtain a substantive final-head review from Codex or CodeRabbit.",
        "No substantive final-head review from Codex or CodeRabbit is required.",
        "A substantive final-head review from Codex or CodeRabbit may be skipped.",
        "A substantive final-head review from Codex or CodeRabbit is recommended only.",
        "A substantive final-head review from Codex or CodeRabbit is not mandatory.",
        "Complete the template; a substantive final-head review from Codex or "
        "CodeRabbit may be skipped.",
    )
    assert _is_required_review_unit(positive)
    assert all(not _is_required_review_unit(unit) for unit in negatives)


def test_detailed_surfaces_preserve_risk_and_quota_semantics() -> None:
    for relative_path in DETAILED_POLICY_SURFACES:
        visible = " ".join(_visible_units(_read(relative_path)))
        assert LOW_STANDARD_EITHER_RE.search(visible), (
            f"{relative_path} must let low/standard work select Codex or CodeRabbit"
        )
        assert not LOW_STANDARD_NEGATION_RE.search(visible), (
            f"{relative_path} negates the low/standard reviewer choice"
        )
        assert HIGH_CODERABBIT_RE.search(visible), (
            f"{relative_path} must require CodeRabbit for high/load-bearing work"
        )
        assert not HIGH_CODERABBIT_NEGATION_RE.search(visible), (
            f"{relative_path} negates the high/load-bearing CodeRabbit requirement"
        )
        assert not HIGH_CODERABBIT_FALLBACK_RE.search(visible), (
            f"{relative_path} allows a high/load-bearing CodeRabbit fallback"
        )
        assert CODERABBIT_QUOTA_RE.search(visible), (
            f"{relative_path} must block only for required/selected CodeRabbit quota"
        )
        assert COPILOT_QUOTA_RE.search(visible), (
            f"{relative_path} must say Copilot quota never blocks"
        )


def test_risk_matchers_reject_negated_policy() -> None:
    low_positive = "Low and standard may use either Codex or CodeRabbit."
    low_negative = "Low and standard must not use either Codex or CodeRabbit."
    high_positive = "High/load-bearing work requires CodeRabbit."
    high_negative = "High/load-bearing work does not require CodeRabbit."
    high_fallback = (
        "High/load-bearing work requires CodeRabbit unless quota is exhausted, when "
        "Codex may substitute."
    )

    assert LOW_STANDARD_EITHER_RE.search(low_positive)
    assert not LOW_STANDARD_NEGATION_RE.search(low_positive)
    assert LOW_STANDARD_EITHER_RE.search(low_negative)
    assert LOW_STANDARD_NEGATION_RE.search(low_negative)
    assert HIGH_CODERABBIT_RE.search(high_positive)
    assert not HIGH_CODERABBIT_NEGATION_RE.search(high_positive)
    assert HIGH_CODERABBIT_RE.search(high_negative)
    assert HIGH_CODERABBIT_NEGATION_RE.search(high_negative)
    assert HIGH_CODERABBIT_RE.search(high_fallback)
    assert HIGH_CODERABBIT_FALLBACK_RE.search(high_fallback)


def test_copilot_is_optional_everywhere_it_is_visible() -> None:
    for relative_path in COPILOT_SURFACES:
        units = _visible_units(_read(relative_path))
        copilot_units = [unit for unit in units if "copilot" in unit.casefold()]
        for unit in copilot_units:
            assert COPILOT_OPTIONAL_RE.search(unit), (
                f"{relative_path} has a Copilot policy unit without an optionality signal: {unit!r}"
            )
            for contradiction in MANDATORY_COPILOT_RES:
                assert not contradiction.search(unit), (
                    f"{relative_path} still contains mandatory-Copilot language: "
                    f"{contradiction.pattern!r}"
                )


def test_copilot_matcher_rejects_mandatory_policy() -> None:
    positive = "Copilot is optional and best-effort; Copilot quota never blocks."
    negatives = (
        "Authors must request Copilot.",
        "Copilot shall precede independent review.",
        "Copilot is a merge prerequisite.",
        "Copilot blocks merge when quota is exhausted.",
    )
    valid = (
        "Copilot must not block merges and remains optional.",
        "Authors must record Copilot as optional.",
    )
    assert COPILOT_OPTIONAL_RE.search(positive)
    assert all(COPILOT_OPTIONAL_RE.search(unit) for unit in valid)
    assert all(not any(pattern.search(unit) for pattern in MANDATORY_COPILOT_RES) for unit in valid)
    assert all(any(pattern.search(unit) for pattern in MANDATORY_COPILOT_RES) for unit in negatives)


def test_policy_surfaces_resolve_all_threads_and_findings() -> None:
    for relative_path in POLICY_SURFACES:
        units = _visible_units(_read(relative_path))
        assert any(
            CONVERSATION_SCOPE_RE.search(unit)
            and FINDING_SCOPE_RE.search(unit)
            and RESOLUTION_RE.search(unit)
            and not RESOLUTION_NEGATION_RE.search(unit)
            for unit in units
        ), f"{relative_path} must resolve every conversation and actionable finding"


def test_resolution_matcher_rejects_unresolved_policy() -> None:
    positives = (
        "Resolve every conversation and every actionable finding.",
        "All review threads and all actionable findings must be resolved.",
        "Each conversation and each actionable finding is resolved.",
    )
    negatives = (
        "Every conversation and every actionable finding may remain unresolved.",
        "We need not resolve all review threads and all actionable findings.",
    )

    def qualifies(unit: str) -> bool:
        return bool(
            CONVERSATION_SCOPE_RE.search(unit)
            and FINDING_SCOPE_RE.search(unit)
            and RESOLUTION_RE.search(unit)
            and not RESOLUTION_NEGATION_RE.search(unit)
        )

    assert all(qualifies(unit) for unit in positives)
    assert all(not qualifies(unit) for unit in negatives)


def test_detailed_surfaces_preserve_coordinator_only_guarded_merge() -> None:
    for relative_path in DETAILED_POLICY_SURFACES:
        visible = " ".join(_visible_units(_read(relative_path)))
        assert WORKER_PR_READY_RE.search(visible), f"{relative_path} must keep workers PR-ready"
        assert WORKER_NO_MERGE_RE.search(visible), f"{relative_path} must prohibit worker merges"
        assert COORDINATOR_ONLY_RE.search(visible), (
            f"{relative_path} must reserve merge for the coordinator"
        )
        assert EXPLICIT_AUTHORITY_RE.search(visible), (
            f"{relative_path} must require explicit merge authority"
        )
        assert not AUTHORITY_NEGATION_RE.search(visible), (
            f"{relative_path} negates explicit merge authority"
        )
        assert EXACT_HEAD_BASE_RE.search(visible), (
            f"{relative_path} must bind the guarded merge to exact head/base"
        )
        assert not EXACT_HEAD_BASE_NEGATION_RE.search(visible), (
            f"{relative_path} negates exact head/base binding"
        )
        assert re.search(r"\bsquash(?:[- ]merge)?\b", visible, re.IGNORECASE), (
            f"{relative_path} must preserve squash merge"
        )
        assert re.search(r"\brefill\w*\b", visible, re.IGNORECASE), (
            f"{relative_path} must refill after a confirmed merge"
        )
        assert not WORKER_MERGE_CONTRADICTION_RE.search(visible), (
            f"{relative_path} permits worker merging"
        )


def test_merge_matcher_rejects_worker_merge_permission() -> None:
    positive = "Workers remain PR-ready and never merge."
    negative = positive + " Workers may also merge when checks pass."
    assert WORKER_PR_READY_RE.search(positive)
    assert WORKER_NO_MERGE_RE.search(positive)
    assert not WORKER_MERGE_CONTRADICTION_RE.search(positive)
    assert WORKER_MERGE_CONTRADICTION_RE.search(negative)


def test_merge_matchers_reject_negated_authority_and_binding() -> None:
    positive = (
        "Only the coordinator with explicit merge authority performs the exact head and base "
        "guarded squash merge."
    )
    negative = (
        positive + " Explicit merge authority is not required; exact head and base need not match."
    )
    assert EXPLICIT_AUTHORITY_RE.search(positive)
    assert EXACT_HEAD_BASE_RE.search(positive)
    assert not AUTHORITY_NEGATION_RE.search(positive)
    assert not EXACT_HEAD_BASE_NEGATION_RE.search(positive)
    assert AUTHORITY_NEGATION_RE.search(negative)
    assert EXACT_HEAD_BASE_NEGATION_RE.search(negative)


def test_deprecated_markdown_does_not_count_as_active_policy() -> None:
    canonical = "Every lane requires a substantive final-head review from Codex or CodeRabbit."
    deprecated = (
        f"```text\n{canonical}\n```\n> {canonical}\n<del>{canonical}</del>\n~~{canonical}~~\n"
    )
    assert not any(_is_required_review_unit(unit) for unit in _visible_units(deprecated))


def test_pull_request_template_records_review_evidence() -> None:
    template = re.sub(
        r"<!--.*?-->",
        "",
        _read(Path(".github/pull_request_template.md")),
        flags=re.DOTALL,
    )
    for expected_line in (
        "- Final head SHA:",
        "- Optional Copilot state: not requested | pending | complete | unavailable | "
        "quota-exhausted",
        "- Required independent reviewer: Codex GitHub Code Review | CodeRabbit PR walkthrough",
        "- Required independent review result: pending | substantive exact-head walkthrough "
        "complete",
        "- CodeRabbit state: n/a (Codex selected for low/standard) | queued | triggered | "
        "rate-limited | substantive exact-head walkthrough complete",
        "- Human/domain state: n/a (reason) | pending | complete (reviewer and evidence)",
    ):
        matches = re.findall(rf"^{re.escape(expected_line)}$", template, re.MULTILINE)
        assert len(matches) == 1, f"expected one visible {expected_line!r} field"


def test_agent_contract_stays_compact() -> None:
    assert len(_read(Path("AGENTS.md")).splitlines()) <= 150


def test_swarm_shortcut_requests_guarded_merge_authority() -> None:
    shortcut = _read(SHORTCUT)
    keys = re.findall(r"^\s*default_prompt\s*:", shortcut, re.MULTILINE)
    assert len(keys) == 1, "Run Issue Swarm shortcut must contain exactly one default_prompt key"
    matches = re.findall(r'^\s*default_prompt:\s*("(?:[^"\\]|\\.)*")\s*$', shortcut, re.MULTILINE)
    assert len(matches) == 1, "Run Issue Swarm shortcut must contain exactly one quoted prompt"
    prompt = json.loads(matches[0])
    assert prompt == EXPECTED_SHORTCUT_PROMPT

    skill = re.sub(r"\s+", " ", _read(Path(".agents/skills/run-issue-swarm/SKILL.md"))).casefold()
    assert "`pr-ready`" in skill
    assert (
        "a `pr-ready` run ends with green checks, resolved reviews, and handoff; nobody merges"
        in skill
    )

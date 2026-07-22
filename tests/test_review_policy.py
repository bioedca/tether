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

REVIEWER_PAIR_PATTERN = (
    r"\bfrom\b.{0,64}\bcodex\b.{0,64}\bor\b.{0,64}"
    r"\b(?:coderabbit|coderabbitai)\b"
)
INDEPENDENT_REVIEW_CONTRACT_RE = re.compile(
    rf"\b(?:requires?|needs?|required|accept\s+only)\b[^;.!?]{{0,96}}\bsubstantive\b(?:"
    rf"[^;.!?]{{0,96}}\b(?:final|exact)(?:[- ]reviewed)?[- ]head(?:[- ]sha)?\b"
    rf"[^;.!?]{{0,48}}\b(?:review\w*|walkthrough)\b[^;.!?]{{0,120}}"
    rf"{REVIEWER_PAIR_PATTERN}|"
    rf"[^;.!?]{{0,96}}\b(?:review\w*|walkthrough)\b[^;.!?]{{0,120}}"
    rf"\bbound\s+to\s+(?:the\s+)?(?:final|exact)(?:[- ]reviewed)?[- ]head"
    rf"(?:[- ]sha)?\b[^;.!?]{{0,120}}{REVIEWER_PAIR_PATTERN}|"
    rf"[^;.!?]{{0,96}}\b(?:review\w*|walkthrough)\b[^;.!?]{{0,120}}"
    rf"{REVIEWER_PAIR_PATTERN}[^;.!?]{{0,120}}\bbound\s+to\s+(?:the\s+)?"
    rf"(?:final|exact)(?:[- ]reviewed)?[- ]head(?:[- ]sha)?\b)",
    re.IGNORECASE,
)
REVIEW_NEGATION_RES = (
    re.compile(
        r"\b(?:substantive\s+)?(?:final|exact)[- ]head\b[^.!?]{0,80}"
        r"\b(?:is|are)\s+(?:not\s+required|optional)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:does|do)\s+not\s+(?:require|need)\b[^.!?]{0,120}"
        r"\b(?:review\w*|walkthrough)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bneed\s+not\b[^.!?]{0,120}\b(?:review\w*|walkthrough)\b", re.IGNORECASE),
    re.compile(
        r"\bno\s+(?:substantive\s+)?(?:final|exact)[- ]head\b[^.!?]{0,40}"
        r"\b(?:review\w*|walkthrough)\b[^.!?]{0,40}\brequired\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bsubstantive\b[^.!?]{0,200}"
        r"\b(?:may be skipped|recommended only|not mandatory)\b",
        re.IGNORECASE,
    ),
)
COPILOT_OPTIONAL_RE = re.compile(
    r"(?:\bcopilot(?: cloud agent)?\b.{0,32}\b(?:is\s+)?(?:optional|best[- ]effort)\b"
    r"|\boptional\b.{0,32}\bcopilot\b"
    r"|\bcopilot\b.{0,64}\b(?:never|does not|must not|cannot|can not|need not)\s+blocks?\b"
    r"|\bcopilot\b[^;.!?]{0,48}\b(?:is|remains)\s+(?:not|never)\s+"
    r"(?:a\s+)?(?:merge\s+)?(?:gate|prerequisite)\b)",
    re.IGNORECASE,
)
COPILOT_EXPLICIT_OPTIONAL_RE = re.compile(
    r"\bcopilot(?:\s+cloud\s+agent)?\b[^;.!?]{0,48}"
    r"\b(?:is\s+)?(?:optional|best[- ]effort)\b|"
    r"\boptional\b[^;.!?]{0,48}\bcopilot\b",
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
        r"\bcopilot\b[^;.!?]{0,48}\b(?:is|are|remains?)\s+"
        r"(?!not\b|never\b)(?:a\s+)?(?:merge\s+)?"
        r"(?:required|mandatory|gate|prerequisite)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:request\s+)?copilot\b.{0,32}\b(?:first|precede\w*)\b", re.IGNORECASE),
    re.compile(
        r"\bcopilot\b(?:(?!\bnever\b|\bcannot\b|\bcan\s+not\b|\bdoes\s+not\b|"
        r"\bmust\s+not\b|\bneed\s+not\b).){0,64}"
        r"\bblocks?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bcopilot\b.{0,48}\b(?:has|needs?)\s+to\s+"
        r"(?:run|complete|finish|precede\w*|be requested|be used|be awaited)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:never|do not|cannot|must not)\s+(?:merge|proceed)\b[^;.!?]{0,80}"
        r"\b(?:until|unless)\b[^;.!?]{0,48}\bcopilot\b|"
        r"\b(?:merge|handoff|slot)\b[^;.!?]{0,48}"
        r"\b(?:is\s+)?(?:prohibited|blocked|forbidden)\b[^;.!?]{0,48}"
        r"\b(?:until|unless)\b[^;.!?]{0,48}\bcopilot\b|"
        r"\b(?:merge|handoff|slot)\b[^;.!?]{0,48}\b(?:waits?|must wait)\b"
        r"[^;.!?]{0,48}\bcopilot\b|"
        r"\bmerges?\b(?:(?!\b(?:does|do)\s+not\b).){0,48}\brequires?\b"
        r"[^;.!?]{0,48}\bcopilot\b|"
        r"\bcopilot\b[^;.!?]{0,48}\b(?:review|approval)\b[^;.!?]{0,32}"
        r"\brequired\b[^;.!?]{0,32}\bbefore\b[^;.!?]{0,24}\bmerge\b|"
        r"\bmerges?\b[^;.!?]{0,32}\bmay\s+not\s+proceed\b[^;.!?]{0,32}"
        r"\bbefore\b[^;.!?]{0,48}\bcopilot\b[^;.!?]{0,32}\bcomplete\w*\b|"
        r"\bmerges?\b[^;.!?]{0,48}\bblocked\b[^;.!?]{0,24}"
        r"\b(?:pending|on)\b[^;.!?]{0,32}\bcopilot\b|"
        r"\bcopilot\b[^;.!?]{0,64}\bit\s+still\s+blocks?\b[^;.!?]{0,32}\bmerge\b",
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
LOW_STANDARD_RESTRICTION_RE = re.compile(
    r"\blow(?:\s+and|/)\s*standard\b[^;.!?]{0,120}"
    r"\b(?:use|select|require)\w*\b[^;.!?]{0,48}"
    r"\b(?:(?:codex|coderabbit)\s+only|only\s+(?:codex|coderabbit))\b|"
    r"\b(?:codex|coderabbit)\b[^;.!?]{0,80}"
    r"\b(?:cannot|must\s+not|does\s+not)\b"
    r"[^;.!?]{0,48}\b(?:qualify|serve|be\s+used)\w*\b[^;.!?]{0,80}"
    r"\blow(?:\s+and|/)\s*standard\b",
    re.IGNORECASE,
)
HIGH_CODERABBIT_RE = re.compile(
    r"\bhigh(?:[-/ ]risk|/load[- ]bearing|[-/ ]load[- ]bearing)?\b.{0,240}"
    r"\brequires?\b(?:(?!\bcodex\b|\bor\b|\boptional\b|\breview\b|[.;]).){0,64}"
    r"\bcoderabbit\b",
    re.IGNORECASE,
)
HIGH_CODERABBIT_NEGATION_RE = re.compile(
    r"\bhigh(?:[-/ ]risk|/load[- ]bearing|[-/ ]load[- ]bearing)?\b.{0,240}"
    r"\b(?:does|do|need|must|shall)\s+not\s+require\b.{0,48}\bcoderabbit\b|"
    r"\bhigh(?:[-/ ]risk|/load[- ]bearing|[-/ ]load[- ]bearing)?\b.{0,240}"
    r"\bno longer requires?\b.{0,48}\bcoderabbit\b|"
    r"\bhigh(?:[-/ ]risk|/load[- ]bearing|[-/ ]load[- ]bearing)?\b"
    r"[^.!?]{0,240}\bcoderabbit\b[^.!?]{0,64}\b(?:is|becomes?|remains?)\s+optional\b",
    re.IGNORECASE,
)
HIGH_LANE_CODEX_SUBSTITUTE_RE = re.compile(
    r"\bhigh(?:[-/ ]risk|/load[- ]bearing|[-/ ]load[- ]bearing)?\b[^;.!?]{0,160}(?:"
    r"\b(?:may|can)\s+(?:be\s+)?(?:use|used)\b[^;.!?]{0,48}\bcodex\b"
    r"[^;.!?]{0,32}\b(?:instead|fallback|substitute)\b|"
    r"\b(?:may|can)\s+use\s+codex\b[^;.!?]{0,32}"
    r"\b(?:instead|fallback|substitute)\b|"
    r"\brequires?\b[^;.!?]{0,48}\bcodex\b[^;.!?]{0,24}\bor\b"
    r"[^;.!?]{0,24}\bcoderabbit\b|"
    r"\bcodex\b[^;.!?]{0,48}\b(?:is\s+)?(?:sufficient|a\s+fallback|a\s+substitute)\b)",
    re.IGNORECASE,
)
HIGH_CODERABBIT_FALLBACK_RE = re.compile(
    r"\bhigh(?:[-/ ]risk|/load[- ]bearing|[-/ ]load[- ]bearing)?\b.{0,280}"
    r"\bcoderabbit\b(?:"
    r".{0,160}\b(?:may|can)\s+use\s+codex\b.{0,32}"
    r"\b(?:instead|as\s+(?:a\s+)?fallback)\b|"
    r".{0,160}\bcodex\b.{0,48}\b(?:may|can)\s+(?:be\s+)?"
    r"(?:substitut\w*|replace(?:\s+it)?|serve\s+as\s+(?:a\s+)?fallback)\b|"
    r".{0,160}\bcodex\b.{0,48}\b(?:may|can)\s+(?:be\s+)?used\b"
    r"(?:.{0,24}\binstead\b|.{0,48}\b(?:if|when)\b.{0,48}"
    r"\b(?:quota|rate[- ]limit\w*|unavail\w*)\b)|"
    r".{0,160}\bcodex\b.{0,48}\bis\s+(?:an?\s+)?"
    r"(?:acceptable|allowed|permitted|sufficient)(?:\s+(?:substitute|fallback))?\b"
    r".{0,48}\b(?:if|when)\b.{0,48}"
    r"\b(?:quota|rate[- ]limit\w*|unavail\w*)\b|"
    r".{0,160}\b(?:if|when)\b.{0,48}\b(?:quota|rate[- ]limit\w*|unavail\w*)\b"
    r".{0,80}\bcodex\b.{0,48}(?:"
    r"\b(?:may|can)\s+(?:be\s+)?(?:used|substitut\w*)\b|"
    r"\bis\s+(?:an?\s+)?(?:acceptable|allowed|permitted|sufficient)"
    r"(?:\s+(?:substitute|fallback))?\b|"
    r"\b(?:may|can)\s+(?:satisfy|fulfill)\b.{0,48}\b(?:review|gate|requirement)\b))",
    re.IGNORECASE,
)
CODERABBIT_SUPPLEMENT_ONLY_RE = re.compile(
    r"\bcodex\b.{0,96}\bsupplement(?:al|ary)\b.{0,96}"
    r"\b(?:never|not)\b.{0,48}\b(?:substitut\w*|fallback|replace\w*)\b|"
    r"\b(?:never|not)\b.{0,48}\b(?:substitut\w*|fallback|replace\w*)\b"
    r".{0,96}\bcodex\b.{0,96}\bsupplement(?:al|ary)\b",
    re.IGNORECASE,
)
CODERABBIT_CONTEXTUAL_CODEX_SUBSTITUTE_RE = re.compile(
    r"\b(?:coderabbit\b[^;.!?]{0,48}\b(?:quota|rate[- ]limit\w*|unavail\w*)|"
    r"(?:quota|rate[- ]limit\w*|unavail\w*)\b[^;.!?]{0,48}\bcoderabbit)\b"
    r"[^;.!?]{0,120}(?:"
    r"\b(?:select|use)\s+codex\b[^;.!?]{0,32}\b(?:instead|as\s+(?:a\s+)?fallback)\b|"
    r"\bcodex\b[^;.!?]{0,64}\b(?:fulfills?|satisf(?:y|ies)|is\s+sufficient)\b"
    r"[^;.!?]{0,48}\b(?:review|gate|requirement)?\b)|"
    r"\bcodex\b[^;.!?]{0,64}\b(?:fulfills?|satisf(?:y|ies)|is\s+sufficient)\b"
    r"[^;.!?]{0,64}\b(?:if|when)\b[^;.!?]{0,48}\bcoderabbit\b"
    r"[^;.!?]{0,48}\b(?:quota|rate[- ]limit\w*|unavail\w*)\b",
    re.IGNORECASE,
)
CODERABBIT_EXPLICIT_FALLBACK_RE = re.compile(
    r"\b(?:may|can)\s+use\s+codex\b.{0,32}"
    r"\b(?:instead|as\s+(?:a\s+)?fallback)\b|"
    r"\bcodex\b.{0,48}\b(?:may|can)\s+(?:be\s+)?"
    r"(?:substitut\w*|replace(?:\s+it)?|serve\s+as\s+(?:a\s+)?fallback)\b|"
    r"\bcodex\b.{0,48}\bis\s+(?:an?\s+)?(?:acceptable|allowed|permitted|sufficient)"
    r"(?:\s+(?:substitute|fallback))?\b|"
    r"\bcodex\b.{0,48}\b(?:may|can)\s+(?:satisfy|fulfill)\b.{0,48}"
    r"\b(?:review|gate|requirement)\b",
    re.IGNORECASE,
)
HIGH_CODERABBIT_QUOTA_EXCEPTION_RE = re.compile(
    r"\bhigh(?:[-/ ]risk|/load[- ]bearing|[-/ ]load[- ]bearing)?\b[^;.!?]{0,240}"
    r"\brequires?\b[^;.!?]{0,64}\bcoderabbit\b[^;.!?]{0,64}"
    r"\b(?:unless|except\s+(?:if|when))\b[^;.!?]{0,64}"
    r"\b(?:quota|rate[- ]limit)\b[^;.!?]{0,32}"
    r"\b(?:exhausted|unavailable|limited|denied)\b",
    re.IGNORECASE,
)
CODERABBIT_QUOTA_RE = re.compile(
    r"\bcoderabbit(?:'s)?\b.{0,40}\bquota\b.{0,64}"
    r"\b(?:blocks?|occupies|does so)\b.{0,48}\bonly when\b.{0,64}"
    r"\brequired or selected\b",
    re.IGNORECASE,
)
COPILOT_QUOTA_RE = re.compile(r"\bcopilot\b.{0,96}\bquota\b.{0,32}\bnever blocks?\b", re.IGNORECASE)
HUMAN_DOMAIN_RE = re.compile(
    r"\bqualified\s+human/domain\s+review\s+is\s+required\s+when\s+scientific,\s*"
    r"security,\s*or\s+release\s+judgment\s+is\s+material\b",
    re.IGNORECASE,
)
HUMAN_DOMAIN_NEGATION_RE = re.compile(
    r"\bhuman/domain\b[^;.!?]{0,80}\b(?:is\s+(?:optional|advisory)|not\s+required|"
    r"may\s+be\s+skipped|recommended\s+only)\b",
    re.IGNORECASE,
)
HUMAN_DOMAIN_NO_TRIGGER_RE = re.compile(
    r"\bhuman/domain\s+review\s+is\s+not\s+required\s+when\s+no\s+"
    r"(?:scientific,\s*security,\s*or\s+release\s+)?judgment\s+is\s+material\b",
    re.IGNORECASE,
)
HUMAN_DOMAIN_REVERSE_NEGATION_RE = re.compile(
    r"\b(?:scientific|security|release)\b[^;.!?]{0,48}\b(?:changes?|judgment)\b"
    r"[^;.!?]{0,64}\b(?:may|can)\s+(?:skip|omit)\b[^;.!?]{0,48}"
    r"\bhuman/domain\s+review\b|"
    r"\bhuman/domain\s+review\b.{0,200}\bexcept\s+for\b"
    r"[^;.!?]{0,48}\b(?:scientific|security|release)\b",
    re.IGNORECASE,
)
HEAD_INVALIDATION_RE = re.compile(
    r"\b(?:any|every)\s+head\s+change\s+invalidates\s+final-head\s+review\s+evidence\b"
    r"[^.!?]{0,96}\ba\s+material\s+change\s+requires\s+every\s+affected\s+review\s+"
    r"layer\s+again\b",
    re.IGNORECASE,
)
HEAD_INVALIDATION_NEGATION_RE = re.compile(
    r"\bhead\s+changes?\b(?:(?!\b(?:do|does)\s+not\b).){0,80}"
    r"\b(?:preserves?|retains?|does\s+not\s+invalidate|"
    r"need\s+not\s+invalidate)\b[^;.!?]{0,80}\b(?:review|evidence)\b|"
    r"\bmaterial\s+changes?\b[^;.!?]{0,80}\b(?:need\s+not|does\s+not\s+need\s+to|"
    r"may\s+skip|can\s+skip)\b[^;.!?]{0,80}\b(?:review\s+)?layers?\b|"
    r"\bonly\s+material\s+head\s+changes?\b[^;.!?]{0,64}\binvalidate\w*\b|"
    r"\b(?:any|every)\s+head\s+change\b.{0,240}\binvalidate\w*\b"
    r".{0,240}\b(?:except|unless)\b[^;.!?]{0,64}\bchanges?\b",
    re.IGNORECASE,
)
CONVERSATION_SCOPE_RE = re.compile(
    r"\b(?:every|all|each)\s+(?:conversation(?:s|/threads?)?|review threads?|threads?)\b",
    re.IGNORECASE,
)
FINDING_SCOPE_RE = re.compile(r"\b(?:every|all|each)\s+actionable\s+findings?\b", re.IGNORECASE)
RESOLUTION_RE = re.compile(r"\bresolve(?:s|d)?\b", re.IGNORECASE)
RESOLUTION_NEGATION_RE = re.compile(
    r"\b(?:need not|do not|must not|may|can)\s+resolve\b|"
    r"\b(?:not|never)\s+(?:be\s+)?resolved\b|\bremain(?:s)?\s+unresolved\b|"
    r"\b(?:may|can)\s+remain\s+(?:open|unresolved)\b",
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
PROVIDER_UNAVAILABLE_PATTERN = (
    r"(?:\b(?:provider\s+)?unavail\w*\b|"
    r"\bno\s+(?:review\s+)?provider\b.{0,24}\b(?:is\s+)?available\b|"
    r"\b(?:review\s+)?provider\b.{0,24}\b(?:outage|offline|down)\b)"
)
UNAVAILABLE_RE = re.compile(PROVIDER_UNAVAILABLE_PATTERN, re.IGNORECASE)
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
        rf"{PROVIDER_UNAVAILABLE_PATTERN}.{{0,80}}\b(?:author[- ]side|local)\b"
        r"(?:(?!\b(?:not|never|cannot)\b).){0,80}\bsatisf(?:y|ies)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:status|check|denial|summary)\b"
        r"(?:(?!\bnever\b|\bdoes\s+not\b).){0,80}\bsatisf(?:y|ies)\b.{0,40}\bgate\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:status(?:/check)?[- ]only|status/check alone|status-only output)\b"
        r"(?:(?!\bnever\b|\b(?:does|is)\s+not\b).){0,80}"
        r"\b(?:counts?\s+as|qualif(?:y|ies)\s+as|is\s+accepted\s+as)\b.{0,40}"
        r"\bindependent\s+review\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"{PROVIDER_UNAVAILABLE_PATTERN}"
        r"(?:(?!\b(?:not|never|cannot)\b).){0,80}"
        r"\b(?:waives?|bypasses?|allows?\b.{0,32}\bwithout review|proceed\w*\b.{0,32}"
        r"\bwithout review)\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"{PROVIDER_UNAVAILABLE_PATTERN}"
        r"(?:(?!\b(?:not|never|cannot)\b).){0,96}"
        r"\b(?:(?:makes?|renders?)\b.{0,48}\b(?:review|gate|requirement)\b.{0,24}"
        r"\boptional\b|(?:review|gate|requirement)\b.{0,24}"
        r"\b(?:becomes?|is)\s+optional\b|"
        r"removes?\b.{0,40}\b(?:review\s+)?requirement\b|"
        r"(?:review|gate|requirement)\b.{0,32}\b(?:may|can)\s+be\s+skipped\b)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:denial|denied(?:\s+(?:request|review))?)\b"
        r"(?:(?!\b(?:does\s+not|is\s+not|never|cannot|must\s+not)\b).){0,80}"
        r"\b(?:counts?\s+as|qualif(?:y|ies)\s+as|is\s+accepted\s+as|"
        r"satisf(?:y|ies)|fulfills?)\b.{0,48}\b(?:review|gate|requirement)\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?:\b(?:author[- ]side(?:\s+/review)?|local\s+(?:coderabbit|review)|"
        rf"summary\s+without\s+(?:a\s+)?diff\s+walkthrough|denial|denied\s+request|"
        rf"status(?:/check)?[- ]only(?:\s+output)?|status\s+check|green\s+check)\b|"
        rf"{PROVIDER_UNAVAILABLE_PATTERN})"
        r"(?:(?!\b(?:does\s+not|is\s+not|never|cannot|must\s+not)\b).){0,96}"
        r"\b(?:counts?\s+as|qualif(?:y|ies)\s+as|is\s+accepted\s+as|"
        r"satisf(?:y|ies)|fulfills?)\b.{0,48}"
        r"\b(?:independent\s+|required\s+)?(?:review|gate|requirement|evidence)\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:(?:required|independent)\s+(?:review|gate|evidence)|gate)\b"
        rf"(?:(?!\b(?:does\s+not|is\s+not|never|cannot|must\s+not)\b).){{0,64}}(?:"
        rf"\b(?:may|can)\s+be\s+(?:satisfied|fulfilled|accepted)\s+by\b|"
        rf"\baccepts?\b|\btreats?\b.{{0,24}}\bas\s+sufficient\b)"
        rf".{{0,64}}(?:\b(?:denial|denied\s+request|local\s+(?:coderabbit|review)|"
        rf"summary\s+without\s+(?:a\s+)?diff\s+walkthrough|status(?:/check)?[- ]only|"
        rf"status\s+check|green\s+check)\b|{PROVIDER_UNAVAILABLE_PATTERN})",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:review|gate|evidence)\b"
        rf"(?:(?!\b(?:does\s+not|is\s+not|never|cannot|must\s+not)\b).){{0,64}}"
        rf"\btreats?\b.{{0,64}}(?:\b(?:denial|local\s+(?:coderabbit|review)|"
        rf"summary\s+without\s+(?:a\s+)?diff\s+walkthrough|status(?:/check)?[- ]only|"
        rf"status\s+check|green\s+check)\b|{PROVIDER_UNAVAILABLE_PATTERN})"
        rf".{{0,32}}\bas\s+sufficient\b",
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
    r"\b(?:explicit\w*\s+)?(?:merge\s+)?authority\b.{0,32}"
    r"\bneed\s+not\s+be\s+(?:obtained|held)\b|"
    r"\b(?:does|do)\s+not\s+(?:need|require)\b.{0,48}"
    r"\b(?:explicit\w*\s+)?(?:merge\s+)?authority\b|"
    r"\bneed\s+not\s+(?:obtain|hold)\b.{0,32}"
    r"\b(?:explicit\w*\s+)?(?:merge\s+)?authority\b|"
    r"\b(?:is|are)\s+not\s+required\s+to\s+(?:obtain|hold|have)\b.{0,48}"
    r"\b(?:explicit\w*\s+)?(?:merge\s+)?authority\b|"
    r"\b(?:explicit\w*\s+)?(?:merge\s+)?authority\b.{0,32}"
    r"\b(?:is|becomes?|remains?)\s+optional\b|"
    r"\bno\s+(?:explicit\w*\s+)?(?:merge\s+)?authority\s+is\s+required\b|"
    r"\b(?:may|can|shall|must)\s+merge\b[^;.!?]{0,64}\bwithout\s+"
    r"(?:explicit\w*\s+)?(?:merge\s+)?authority\b|"
    r"\b(?:is|are)\s+(?:allowed|permitted|authorized)\s+to\s+merge\b"
    r"[^;.!?]{0,64}\bwithout\s+(?:explicit\w*\s+)?(?:merge\s+)?authority\b",
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
    r"\bexact\b.{0,48}\bhead\b.{0,48}\bbase\b.{0,64}"
    r"\b(?:is|are)\s+not\s+required\s+to\s+(?:match|bind)\b|"
    r"\bexact\b.{0,48}\bhead\b.{0,48}\bbase\b.{0,64}"
    r"\b(?:does|do)\s+not\s+have\s+to\s+(?:match|bind)\b|"
    r"\bexact\b.{0,48}\bhead\b.{0,48}\bbase\b.{0,64}"
    r"\b(?:validation|binding|matching|check)\b.{0,32}"
    r"\b(?:(?:may|can)\s+be\s+skipped|is\s+optional)\b|"
    r"\bhead\b.{0,48}\bbase\b.{0,64}\bmay differ\b|"
    r"\bexact\b.{0,48}\bhead\b.{0,48}\bbase\b.{0,64}"
    r"\b(?:may\s+be\s+stale|may\s+drift|need\s+not\s+be\s+current)\b",
    re.IGNORECASE,
)
WORKER_MERGE_CONTRADICTION_RE = re.compile(
    r"\bworkers?\b.{0,64}\b(?:may|can|shall|must)\s+(?:also\s+)?merge\b|"
    r"\bworkers?\b.{0,64}\b(?:is|are)\s+(?:allowed|permitted|authorized)\s+to\s+merge\b",
    re.IGNORECASE,
)
COORDINATOR_MERGE_ACTION_RE = re.compile(
    r"(?:\bonly\b[^.!?]{0,180}\bcoordinator\b|"
    r"\bcoordinator\b[^.!?]{0,80}\b(?:alone|only)\b)"
    r"[^.!?]{0,160}\b(?:may\s+)?perform(?:s)?\b[^.!?]{0,96}\bmerge\b",
    re.IGNORECASE,
)
REFILL_AFTER_MERGE_RE = re.compile(
    r"\b(?:perform(?:s)?|confirm(?:s|ed|ing)?)\b[^.!?]{0,160}\bmerge\b"
    r"[^.!?]{0,160}\brefill(?:s|ed|ing)?\b|"
    r"\bmerge\b[^.!?]{0,160}\brefill(?:s|ed|ing)?\b",
    re.IGNORECASE,
)
SQUASH_MERGE_NEGATION_RE = re.compile(
    r"\bsquash[- ]merge\b[^;.!?]{0,48}\b(?:is\s+)?"
    r"(?:forbidden|prohibited|not\s+allowed|optional|may\s+be\s+skipped)\b|"
    r"\b(?:never|do\s+not|must\s+not|cannot)\s+(?:directly\s+)?"
    r"squash[- ]merge\b",
    re.IGNORECASE,
)
REFILL_NEGATION_RE = re.compile(
    r"\b(?:never|do\s+not|must\s+not|cannot)\b[^;.!?]{0,48}\brefill\w*\b"
    r"[^;.!?]{0,48}\b(?:completed|merged|confirmed)\b[^;.!?]{0,24}\bslot\b|"
    r"\b(?:never|do\s+not|must\s+not|cannot)\b[^;.!?]{0,48}\brefill\w*\b"
    r"[^;.!?]{0,48}\bafter\b[^;.!?]{0,32}\bmerge\b|"
    r"\brefill\w*\b[^;.!?]{0,48}\bafter\b[^;.!?]{0,32}\bmerge\b"
    r"[^;.!?]{0,48}\b(?:is\s+)?(?:forbidden|prohibited|not\s+allowed)\b|"
    r"\brefill\w*\b[^;.!?]{0,64}\bbefore\b[^;.!?]{0,48}"
    r"\b(?:confirm\w*|merge\w*)\b|"
    r"\bbefore\b[^;.!?]{0,48}\b(?:confirm\w*|merge\w*)\b"
    r"[^;.!?]{0,64}\brefill\w*\b",
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
    visible = re.sub(r"(?m)^[ \t]*(?:>[ \t]?)+", "", visible)
    visible = re.sub(r"(?ms)^[ \t]*(?:```|~~~).*?^[ \t]*(?:```|~~~)[ \t]*$", "", visible)
    visible = re.sub(r"(?is)<(?:del|s)>.*?</(?:del|s)>", "", visible)
    visible = re.sub(r"~~.*?~~", "", visible, flags=re.DOTALL)
    units: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            units.append(" ".join(current))
            current.clear()

    for raw_line in visible.splitlines():
        line = raw_line.strip()
        line = re.sub(r"^(?:>\s*)+", "", line).strip()
        heading = re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", line)
        if heading:
            flush()
            units.append(heading.group(1).strip())
        elif not line:
            flush()
        elif re.match(r"^(?:[-*+] |\d+[.)] )", line):
            flush()
            current.append(line)
        else:
            current.append(line)
    flush()
    return tuple(units)


def _policy_clauses(units: tuple[str, ...]) -> tuple[str, ...]:
    """Keep semantic checks inside one subject-level policy clause."""
    return tuple(
        clause.strip()
        for unit in units
        for clause in re.split(r"(?<=[.!?])\s+|\s*;\s*|,\s+(?:but|while|whereas)\s+", unit)
        if clause.strip()
    )


def _is_required_review_unit(unit: str) -> bool:
    return bool(
        INDEPENDENT_REVIEW_CONTRACT_RE.search(unit)
        and not any(pattern.search(unit) for pattern in REVIEW_NEGATION_RES)
    )


def _has_required_review_contract(unit: str) -> bool:
    clauses = _policy_clauses((unit,))
    return bool(
        any(_is_required_review_unit(clause) for clause in clauses)
        and not any(pattern.search(unit) for pattern in REVIEW_NEGATION_RES)
    )


def _has_mandatory_copilot(unit: str) -> bool:
    candidates = (unit, *_policy_clauses((unit,)))
    return any(
        pattern.search(candidate) for candidate in candidates for pattern in MANDATORY_COPILOT_RES
    )


def _has_human_domain_contradiction(unit: str) -> bool:
    return bool(
        (HUMAN_DOMAIN_NEGATION_RE.search(unit) and not HUMAN_DOMAIN_NO_TRIGGER_RE.search(unit))
        or HUMAN_DOMAIN_REVERSE_NEGATION_RE.search(unit)
    )


def _has_high_coderabbit_fallback(unit: str) -> bool:
    """Reject explicit substitution; exempt only clearly supplemental Codex feedback."""
    high_context = False
    clauses = _policy_clauses((unit,))
    for index, clause in enumerate(clauses):
        scoped = re.sub(
            r"(?:,\s*(?:while|whereas)\s+|;\s*)?"
            r"\blow(?:\s+and|/)\s*standard\b[^;.!?]*",
            "",
            clause,
            flags=re.IGNORECASE,
        )
        if re.search(r"\blow(?:\s+and|/)\s*standard\b", clause, re.IGNORECASE) and not re.search(
            r"\bhigh(?:[-/ ]risk|/load[- ]bearing|[-/ ]load[- ]bearing)?\b",
            clause,
            re.IGNORECASE,
        ):
            high_context = False
            continue
        if HIGH_CODERABBIT_RE.search(scoped):
            high_context = True
        if not high_context:
            continue
        contextual = scoped
        if not HIGH_CODERABBIT_RE.search(scoped):
            contextual = f"High/load-bearing work requires CodeRabbit; {scoped}"
        if HIGH_CODERABBIT_QUOTA_EXCEPTION_RE.search(contextual):
            return True
        if not HIGH_CODERABBIT_FALLBACK_RE.search(contextual):
            continue
        if CODERABBIT_EXPLICIT_FALLBACK_RE.search(scoped):
            return True
        scoped_with_followup = scoped
        if index > 0:
            scoped_with_followup = f"{clauses[index - 1]}; {scoped_with_followup}"
        if index + 1 < len(clauses):
            scoped_with_followup = f"{scoped_with_followup}; {clauses[index + 1]}"
        if not CODERABBIT_SUPPLEMENT_ONLY_RE.search(scoped_with_followup):
            return True
    return False


def _has_direct_high_codex_substitute(unit: str) -> bool:
    low_standard_context = bool(re.search(r"\blow(?:\s+and|/)\s*standard\b", unit, re.IGNORECASE))
    high_scoped = re.sub(
        r"(?:,\s*(?:but|while|whereas)\s+|;\s*|(?<=[.!?])\s+)"
        r"\blow(?:\s+and|/)\s*standard\b[^;.!?]*",
        "",
        unit,
        flags=re.IGNORECASE,
    )
    return bool(
        (
            HIGH_LANE_CODEX_SUBSTITUTE_RE.search(high_scoped)
            or (CODERABBIT_CONTEXTUAL_CODEX_SUBSTITUTE_RE.search(unit) and not low_standard_context)
        )
        and not CODERABBIT_SUPPLEMENT_ONLY_RE.search(unit)
    )


def _has_coordinator_guarded_merge_contract(unit: str) -> bool:
    return bool(
        COORDINATOR_MERGE_ACTION_RE.search(unit)
        and EXPLICIT_AUTHORITY_RE.search(unit)
        and EXACT_HEAD_BASE_RE.search(unit)
        and re.search(r"\bsquash(?:[- ]merge)?\b", unit, re.IGNORECASE)
        and REFILL_AFTER_MERGE_RE.search(unit)
        and not AUTHORITY_NEGATION_RE.search(unit)
        and not EXACT_HEAD_BASE_NEGATION_RE.search(unit)
        and not SQUASH_MERGE_NEGATION_RE.search(unit)
        and not REFILL_NEGATION_RE.search(unit)
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
        matches = [unit for unit in units if _has_required_review_contract(unit)]
        assert matches, (
            f"{relative_path} needs one policy unit requiring a substantive final-head "
            "review from Codex or CodeRabbit"
        )
        assert not any(pattern.search(unit) for unit in units for pattern in REVIEW_NEGATION_RES), (
            f"{relative_path} negates its substantive final-head review contract"
        )


def test_policy_surfaces_reject_non_walkthrough_substitutes() -> None:
    for relative_path in DETAILED_POLICY_SURFACES:
        units = _visible_units(_read(relative_path))
        assert any(_is_evidence_exclusion_unit(unit) for unit in units), (
            f"{relative_path} must reject local/author, status-only, denial, unavailable, "
            "and summary-only review substitutes"
        )
        clauses = _policy_clauses(units)
        assert not any(
            pattern.search(clause) for clause in clauses for pattern in FALLBACK_ACCEPTANCE_RES
        ), f"{relative_path} explicitly allows a disallowed review substitute"


def test_evidence_matcher_rejects_local_unavailable_fallback() -> None:
    positive = (
        "A substantive PR diff walkthrough is required. Author-side or local review, "
        "status-only output, denial, provider unavailability, or a summary without a diff "
        "walkthrough never satisfies the gate."
    )
    negatives = (
        positive + " If unavailable, local CodeRabbit satisfies the gate.",
        positive + " Provider unavailability waives the review gate.",
        positive + " Provider unavailability makes the review optional.",
        positive + " Provider unavailability renders the review requirement optional.",
        positive + " Provider unavailability removes the review requirement.",
        positive + " If no provider is available, review becomes optional.",
        positive + " A review provider outage means the gate may be skipped.",
        positive + " If no review provider is available, local CodeRabbit satisfies the gate.",
        positive + " Status-only output counts as independent review.",
        positive + " A denial counts as the required review.",
        positive + " A summary without a diff walkthrough counts as independent review.",
        positive + " Local CodeRabbit counts as independent review.",
        positive + " Provider unavailability counts as independent review.",
        positive + " Status-only output counts as required review.",
        positive + " Status-only output counts as required evidence.",
        positive + " A status check qualifies as the required review.",
        positive + " A green check counts as required review evidence.",
        positive + " A denial counts as required evidence.",
        positive + " The required review may be satisfied by a denial.",
        positive + " The independent gate accepts a summary without a diff walkthrough.",
        positive + " The required review can be fulfilled by local CodeRabbit.",
        positive + " The gate treats provider unavailability as sufficient evidence.",
    )
    protections = (
        positive + " Provider unavailability does not waive the review gate.",
        positive + " Provider unavailability never makes review optional.",
        positive + " Provider unavailability cannot remove the review requirement.",
        positive + " Provider unavailability is not sufficient to waive the review gate.",
        positive + " Provider unavailability means local review does not satisfy the gate.",
        positive + " A denial does not count as review.",
        positive + " A summary without a diff walkthrough does not count as review.",
        positive + " Local CodeRabbit does not count as independent review.",
        positive + " Provider unavailability does not count as independent review.",
        positive + " The required review cannot be satisfied by a denial.",
    )
    assert _is_evidence_exclusion_unit(positive)
    assert all(not _is_evidence_exclusion_unit(unit) for unit in negatives)
    assert all(_is_evidence_exclusion_unit(unit) for unit in protections)


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
        "Every lane requires a substantive review of the base branch from Codex or "
        "CodeRabbit; the final head SHA is recorded for reference.",
    )
    assert _has_required_review_contract(positive)
    assert all(not _has_required_review_contract(unit) for unit in negatives)


def test_detailed_surfaces_preserve_risk_and_quota_semantics() -> None:
    for relative_path in DETAILED_POLICY_SURFACES:
        units = _visible_units(_read(relative_path))
        clauses = _policy_clauses(units)
        assert any(LOW_STANDARD_EITHER_RE.search(clause) for clause in clauses), (
            f"{relative_path} must let low/standard work select Codex or CodeRabbit"
        )
        assert not any(LOW_STANDARD_NEGATION_RE.search(clause) for clause in clauses), (
            f"{relative_path} negates the low/standard reviewer choice"
        )
        assert not any(LOW_STANDARD_RESTRICTION_RE.search(clause) for clause in clauses), (
            f"{relative_path} restricts low/standard work to one reviewer"
        )
        assert any(HIGH_CODERABBIT_RE.search(clause) for clause in clauses), (
            f"{relative_path} must require CodeRabbit for high/load-bearing work"
        )
        assert not any(HIGH_CODERABBIT_NEGATION_RE.search(clause) for clause in clauses), (
            f"{relative_path} negates the high/load-bearing CodeRabbit requirement"
        )
        assert not any(_has_high_coderabbit_fallback(unit) for unit in units), (
            f"{relative_path} allows a high/load-bearing CodeRabbit fallback"
        )
        assert not any(_has_direct_high_codex_substitute(unit) for unit in units), (
            f"{relative_path} directly permits Codex to substitute in a high/load-bearing lane"
        )
        assert any(CODERABBIT_QUOTA_RE.search(clause) for clause in clauses), (
            f"{relative_path} must block only for required/selected CodeRabbit quota"
        )
        assert any(COPILOT_QUOTA_RE.search(clause) for clause in clauses), (
            f"{relative_path} must say Copilot quota never blocks"
        )
        assert any(HUMAN_DOMAIN_RE.search(clause) for clause in clauses), (
            f"{relative_path} must preserve applicable qualified human/domain review"
        )
        assert not any(_has_human_domain_contradiction(clause) for clause in clauses), (
            f"{relative_path} negates applicable qualified human/domain review"
        )


def test_risk_matchers_reject_negated_policy() -> None:
    low_positive = "Low and standard may use either Codex or CodeRabbit."
    low_negative = "Low and standard must not use either Codex or CodeRabbit."
    low_restrictions = (
        "Low and standard use Codex only.",
        "Low and standard use CodeRabbit only.",
        "Low and standard select only Codex.",
        "Low and standard select only CodeRabbit.",
        "CodeRabbit cannot qualify for low and standard work.",
        "Codex cannot qualify for low and standard work.",
    )
    high_positive = "High/load-bearing work requires CodeRabbit."
    high_negative = "High/load-bearing work does not require CodeRabbit."
    high_fallbacks = (
        "High/load-bearing work requires CodeRabbit; Codex may substitute when quota is exhausted.",
        "High/load-bearing work requires CodeRabbit, but Codex is acceptable when quota "
        "is exhausted.",
        "High/load-bearing work requires CodeRabbit, but may use Codex instead.",
        "High/load-bearing work requires CodeRabbit; when CodeRabbit is unavailable, "
        "Codex may be used.",
        "High/load-bearing work requires CodeRabbit; Codex may be used when quota is exhausted.",
        "High/load-bearing work requires CodeRabbit; Codex may serve as a fallback.",
        "High/load-bearing work requires CodeRabbit; Codex is an acceptable substitute when "
        "quota is exhausted.",
        "High/load-bearing work requires CodeRabbit; Codex may replace it when quota is exhausted.",
        "High/load-bearing work requires CodeRabbit; Codex may replace it when unavailable, but "
        "supplemental Codex review is not a substitute.",
        "High/load-bearing work requires CodeRabbit unless its quota is exhausted.",
        "High/load-bearing work requires CodeRabbit. Codex is sufficient when CodeRabbit is "
        "rate-limited.",
        "High/load-bearing work requires CodeRabbit. If CodeRabbit quota is exhausted, Codex "
        "may satisfy the required review instead.",
        "High/load-bearing work requires CodeRabbit. When CodeRabbit is rate-limited, select "
        "Codex instead.",
        "High/load-bearing work requires CodeRabbit. Codex fulfills the requirement if "
        "CodeRabbit is unavailable.",
    )
    high_protections = (
        "High/load-bearing work requires CodeRabbit; do not merge unless CodeRabbit completes.",
        "High/load-bearing work requires CodeRabbit; Codex is not acceptable when quota "
        "is exhausted.",
        "High/load-bearing work requires CodeRabbit; do not merge unless CodeRabbit quota "
        "is available.",
        "High/load-bearing work requires CodeRabbit; Codex may be used for supplemental feedback "
        "when quota is exhausted, but never as a substitute.",
        "High/load-bearing work requires CodeRabbit. Low and standard may use Codex instead of "
        "CodeRabbit.",
        "High/load-bearing work requires CodeRabbit, while low and standard may use Codex instead "
        "of CodeRabbit.",
        "High/load-bearing work requires CodeRabbit. Low and standard may use Codex instead of "
        "CodeRabbit during quota waits.",
        "High/load-bearing work may use Codex for supplemental feedback, but never as a "
        "substitute for required CodeRabbit.",
        "High/load-bearing work never uses Codex as a substitute for required CodeRabbit; it "
        "may use Codex for supplemental feedback during quota waits.",
    )
    high_not_required = (
        "High/load-bearing work requires Codex or CodeRabbit.",
        "High/load-bearing work requires a Codex review; CodeRabbit is optional.",
        "High/load-bearing work requires a human review; CodeRabbit is optional.",
    )

    assert LOW_STANDARD_EITHER_RE.search(low_positive)
    assert not LOW_STANDARD_NEGATION_RE.search(low_positive)
    assert LOW_STANDARD_EITHER_RE.search(low_negative)
    assert LOW_STANDARD_NEGATION_RE.search(low_negative)
    assert all(LOW_STANDARD_RESTRICTION_RE.search(unit) for unit in low_restrictions)
    assert HIGH_CODERABBIT_RE.search(high_positive)
    assert not HIGH_CODERABBIT_NEGATION_RE.search(high_positive)
    assert HIGH_CODERABBIT_RE.search(high_negative)
    assert HIGH_CODERABBIT_NEGATION_RE.search(high_negative)
    assert all(HIGH_CODERABBIT_RE.search(unit) for unit in high_fallbacks)
    assert all(
        _has_high_coderabbit_fallback(unit) or _has_direct_high_codex_substitute(unit)
        for unit in high_fallbacks
    )
    assert all(not _has_high_coderabbit_fallback(unit) for unit in high_protections)
    assert all(not _has_direct_high_codex_substitute(unit) for unit in high_protections)
    assert all(not HIGH_CODERABBIT_RE.search(unit) for unit in high_not_required)
    assert all(_has_direct_high_codex_substitute(unit) for unit in high_not_required[:1])
    assert _has_direct_high_codex_substitute("High/load-bearing work may use Codex instead.")


def test_human_domain_matcher_rejects_optional_policy() -> None:
    positive = (
        "Qualified human/domain review is required when scientific, security, or release "
        "judgment is material."
    )
    negatives = (
        "Qualified human/domain review is optional and may be skipped.",
        "Applicable qualified human/domain review is advisory.",
        "Qualified human/domain review is recommended only.",
        "Security changes may skip human/domain review.",
        positive + " Except for release changes.",
    )
    no_trigger = (
        "Human/domain review is not required when no scientific, security, or release "
        "judgment is material."
    )
    assert HUMAN_DOMAIN_RE.search(positive)
    assert not _has_human_domain_contradiction(positive)
    assert all(not HUMAN_DOMAIN_RE.search(negative) for negative in negatives[:-1])
    assert all(_has_human_domain_contradiction(negative) for negative in negatives)
    assert HUMAN_DOMAIN_NO_TRIGGER_RE.search(no_trigger)
    assert not _has_human_domain_contradiction(no_trigger)


def test_detailed_surfaces_invalidate_reviews_after_head_changes() -> None:
    for relative_path in DETAILED_POLICY_SURFACES:
        units = _visible_units(_read(relative_path))
        assert any(HEAD_INVALIDATION_RE.search(unit) for unit in units), (
            f"{relative_path} must invalidate final-head evidence and rerun affected layers"
        )
        assert not any(HEAD_INVALIDATION_NEGATION_RE.search(unit) for unit in units), (
            f"{relative_path} negates review invalidation after a head change"
        )


def test_head_invalidation_matcher_rejects_preserved_review_evidence() -> None:
    positive = (
        "Any head change invalidates final-head review evidence; a material change requires "
        "every affected review layer again."
    )
    negatives = (
        "Head changes preserve prior final-head review evidence.",
        "A material change need not rerun affected review layers.",
        "Material changes may skip affected review layers.",
        "Only material head changes invalidate final-head review evidence.",
        positive + " Except documentation-only changes.",
    )
    protections = (
        "Head changes do not preserve prior final-head review evidence.",
        positive,
    )
    assert HEAD_INVALIDATION_RE.search(positive)
    assert not HEAD_INVALIDATION_NEGATION_RE.search(positive)
    assert all(HEAD_INVALIDATION_NEGATION_RE.search(negative) for negative in negatives)
    assert all(not HEAD_INVALIDATION_NEGATION_RE.search(unit) for unit in protections)


def test_copilot_is_optional_everywhere_it_is_visible() -> None:
    for relative_path in POLICY_SURFACES:
        units = _visible_units(_read(relative_path))
        assert any(COPILOT_EXPLICIT_OPTIONAL_RE.search(unit) for unit in units), (
            f"{relative_path} must explicitly call Copilot optional or best-effort"
        )

    for relative_path in COPILOT_SURFACES:
        units = _visible_units(_read(relative_path))
        copilot_units = [unit for unit in units if "copilot" in unit.casefold()]
        for unit in copilot_units:
            assert COPILOT_OPTIONAL_RE.search(unit), (
                f"{relative_path} has a Copilot policy unit without an optionality signal: {unit!r}"
            )
            assert not _has_mandatory_copilot(unit), (
                f"{relative_path} still contains mandatory-Copilot language: {unit!r}"
            )


def test_copilot_matcher_rejects_mandatory_policy() -> None:
    positive = "Copilot is optional and best-effort; Copilot quota never blocks."
    negatives = (
        "Authors must request Copilot.",
        "Copilot shall precede independent review.",
        "Copilot is a merge prerequisite.",
        "Copilot blocks merge when quota is exhausted.",
        "Copilot has to run before merge.",
        "Copilot needs to run before merge.",
        "Copilot is optional and best-effort, but merge is prohibited until Copilot responds.",
        "Copilot is optional. Never merge until Copilot completes.",
        "Copilot is optional, but merge waits for Copilot.",
        "Merge requires Copilot.",
        "Merges require Copilot.",
        "Copilot review required before merge.",
        "Copilot is optional; it still blocks merge.",
        "Merge may not proceed before Copilot completes.",
    )
    valid = (
        "Copilot must not block merges and remains optional.",
        "Authors must record Copilot as optional.",
        "Copilot is optional and is not a merge prerequisite; Copilot quota never blocks.",
        "Copilot is optional and cannot block merge.",
        "A merge prerequisite must never be Copilot; Copilot remains optional.",
        "Copilot remains optional; merge does not require Copilot.",
    )
    assert COPILOT_OPTIONAL_RE.search(positive)
    assert all(COPILOT_OPTIONAL_RE.search(unit) for unit in valid)
    assert all(not _has_mandatory_copilot(unit) for unit in valid)
    assert all(_has_mandatory_copilot(unit) for unit in negatives)


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
        "Every conversation may remain open and every actionable finding is resolved.",
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
        units = _visible_units(_read(relative_path))
        visible = " ".join(units)
        assert WORKER_PR_READY_RE.search(visible), f"{relative_path} must keep workers PR-ready"
        assert WORKER_NO_MERGE_RE.search(visible), f"{relative_path} must prohibit worker merges"
        assert any(_has_coordinator_guarded_merge_contract(unit) for unit in units), (
            f"{relative_path} must bind coordinator, authority, exact head/base, squash, and refill"
        )
        assert not any(AUTHORITY_NEGATION_RE.search(unit) for unit in units), (
            f"{relative_path} negates explicit merge authority"
        )
        assert EXACT_HEAD_BASE_RE.search(visible), (
            f"{relative_path} must bind the guarded merge to exact head/base"
        )
        assert not any(EXACT_HEAD_BASE_NEGATION_RE.search(unit) for unit in units), (
            f"{relative_path} negates exact head/base binding"
        )
        assert re.search(r"\bsquash(?:[- ]merge)?\b", visible, re.IGNORECASE), (
            f"{relative_path} must preserve squash merge"
        )
        assert re.search(r"\brefill\w*\b", visible, re.IGNORECASE), (
            f"{relative_path} must refill after a confirmed merge"
        )
        assert not any(WORKER_MERGE_CONTRADICTION_RE.search(unit) for unit in units), (
            f"{relative_path} permits worker merging"
        )
        assert not any(SQUASH_MERGE_NEGATION_RE.search(unit) for unit in units), (
            f"{relative_path} negates squash merge"
        )
        assert not any(REFILL_NEGATION_RE.search(unit) for unit in units), (
            f"{relative_path} negates slot refill"
        )


def test_merge_matcher_rejects_worker_merge_permission() -> None:
    positive = "Workers remain PR-ready and never merge."
    negatives = (
        positive + " Workers may also merge when checks pass.",
        positive + " Workers are allowed to merge when checks pass.",
    )
    assert WORKER_PR_READY_RE.search(positive)
    assert WORKER_NO_MERGE_RE.search(positive)
    assert not WORKER_MERGE_CONTRADICTION_RE.search(positive)
    assert all(WORKER_MERGE_CONTRADICTION_RE.search(unit) for unit in negatives)


def test_guarded_merge_matcher_rejects_scattered_or_negated_keywords() -> None:
    positive = (
        "Workers remain PR-ready and never merge. Only the coordinator with explicit run-scoped "
        "merge authority performs the exact-head/exact-base guarded squash merge and refills "
        "the completed slot."
    )
    negative = (
        "Workers remain PR-ready and never merge. Only the coordinator routes reviews. "
        "Explicit merge authority is documented. Exact head and exact base are logged. "
        "Squash merge is forbidden. Never refill a completed slot."
    )
    stale_binding = positive + " Exact head and exact base may be stale."
    early_refill = positive + " Refill the worker slot before merge confirmation."
    assert _has_coordinator_guarded_merge_contract(positive)
    assert not _has_coordinator_guarded_merge_contract(negative)
    assert not _has_coordinator_guarded_merge_contract(stale_binding)
    assert not _has_coordinator_guarded_merge_contract(early_refill)
    assert SQUASH_MERGE_NEGATION_RE.search(negative)
    assert REFILL_NEGATION_RE.search(negative)
    assert EXACT_HEAD_BASE_NEGATION_RE.search(stale_binding)
    assert REFILL_NEGATION_RE.search(early_refill)


def test_merge_matchers_reject_negated_authority_and_binding() -> None:
    positive = (
        "Only the coordinator with explicit merge authority performs the exact head and base "
        "guarded squash merge."
    )
    negatives = (
        positive + " Explicit merge authority is not required; exact head and base need not match.",
        positive
        + " Explicit merge authority need not be obtained; exact head and base are not required "
        "to match.",
        positive
        + " Explicit merge authority need not be held; exact head and base are not required "
        "to bind.",
        positive
        + " The coordinator does not need explicit merge authority; exact head and base do not "
        "have to match.",
        positive
        + " The coordinator is not required to obtain explicit merge authority; exact head and "
        "base validation may be skipped.",
        positive
        + " Explicit merge authority is optional; exact head and base matching is optional.",
    )
    assert EXPLICIT_AUTHORITY_RE.search(positive)
    assert EXACT_HEAD_BASE_RE.search(positive)
    assert not AUTHORITY_NEGATION_RE.search(positive)
    assert not EXACT_HEAD_BASE_NEGATION_RE.search(positive)
    assert all(AUTHORITY_NEGATION_RE.search(unit) for unit in negatives)
    assert all(EXACT_HEAD_BASE_NEGATION_RE.search(unit) for unit in negatives)

    safe = (
        "Never merge without explicit merge authority.",
        "No merge may proceed without explicit run-scoped authority.",
    )
    unsafe = "The coordinator may merge without explicit merge authority."
    assert all(not AUTHORITY_NEGATION_RE.search(unit) for unit in safe)
    assert AUTHORITY_NEGATION_RE.search(unsafe)


def test_inert_markdown_does_not_count_as_active_policy() -> None:
    canonical = "Every lane requires a substantive final-head review from Codex or CodeRabbit."
    inert = f"```text\n{canonical}\n```\n<del>{canonical}</del>\n~~{canonical}~~\n"
    assert not any(_is_required_review_unit(unit) for unit in _visible_units(inert))


def test_visible_markdown_headings_and_blockquotes_count_as_policy() -> None:
    canonical = "Every lane requires a substantive final-head review from Codex or CodeRabbit."
    for visible in (f"## {canonical}\n", f"> {canonical}\n", f"> ### {canonical}\n"):
        assert any(_is_required_review_unit(unit) for unit in _visible_units(visible))


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

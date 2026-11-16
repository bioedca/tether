<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0121 — The tMAVEN class lookup follows the category list

- **Status:** accepted; amends [ADR-0025](0025-tmaven-handoff-and-return-leg-reconcile.md) on the
  tMAVEN class leg
- **Date:** 2026-11-16
- **Deciders:** bioedca
- **PRD anchor:** §5.1 (the per-condition category list and its lookup), §7.3 (the `0`–`9`
  hotkeys), §7.4 FR-IDEALIZE (the return leg), Appendix D.1
- **Milestone:** M16
- **Issue:** [#367](https://github.com/bioedca/tether/issues/367)

## Context and problem statement

tMAVEN keeps a per-trace integer `class`; Tether keeps a free-text `/molecules.category` name
from the condition's editable list (§7.6). ADR-0025 shipped the hand-off with only one leg of
the translation: classes went out as `0`, and on the way back a returning `0` could clear a
category while any other class was recorded as deferred and **not written**, waiting on "the M4
integer↔category lookup". M4 closed without it, and `handoff.py` kept ten comments pointing at
it.

PRD §5.1 already fixes where the lookup lives (a map on the condition, beside its list, so it
travels to every project that holds the condition, ADR-0068), and §7.3 fixes what it says: class
`0` is *uncategorized*, and the named categories are classes `>= 1`, with hotkey `n` assigning
the `n`-th category. What it does not fix is how the persisted map relates to the list once the
list is edited, and what the return leg does with a class or a category the map cannot carry.

## Decision drivers

- One translation for the three surfaces that use it: the outbound SMD, the return leg, and the
  curation hotkeys. A class that means one category on a key press and another on a round trip is
  the defect.
- §5.3 and §7.4: a return leg reports what it cannot resolve and never guesses.
- §7.3: hotkey `n` assigns the first nine categories *of the list*, so the class of a category is
  its position.
- Additive-only schema (ADR-0005): no new group, dataset or field in the frozen skeleton.

## Considered options

1. **Derive the classes from the list every time; persist nothing.** Rejected: §5.1 names a
   persisted map, and a reader would have nothing to check a file against.
2. **Stable class numbers**: each category keeps the class it was first given, through reorders
   and removals. Rejected: it breaks §7.3, where key `n` is the `n`-th entry, and it breaks
   ADR-0068's "same codes in every project" because a copied list would need its history too.
3. **Persist the lookup the list order defines, rewritten by the one list writer in the same
   file session, and refuse a pair that disagrees** (chosen).

## Decision outcome

Chosen option: **3**.

- `/conditions/categories/<condition_id>` carries an attribute `tmaven_class_lookup`, a JSON
  object `{"1": name₁, "2": name₂, …}`. Class `0` is not stored: it always means `""`.
- `_write_category_names` — the writer behind `set_category_list`, `add_category`,
  `rename_category`, `remove_category` and `copy_category_list` — rewrites the attribute with
  the names. `read_class_lookup` is the one reader. A list saved before the attribute existed reads
  as the lookup its order defines, which is exactly what the writer would store; an attribute that
  is malformed or disagrees with the names beside it raises `ValueError` rather than trusting either.
- **Outbound:** each molecule's category goes out as its class through its own condition's
  lookup. A category the lookup cannot express (removed from the list since it was set) goes out
  as `0` and is named in `HandoffManifest.unclassed`.
- **Return leg:** a class the lookup holds resolves to its category and, once accepted, is
  written. A class it does not hold is surfaced with `applicable=False` and never written. A
  returning `0` against a category the lookup cannot express is **no change**, because the outbound
  leg wrote that `0` itself and proposing a clear would invent an edit.
- **Hotkeys:** the shell's `1`–`9` keys and the overflow picker resolve their class through
  `ClassLookup` too, so key `n`, SMD class `n` and the reconcile prompt's class `n` are one
  category.

### Consequences

- Good: a non-zero tMAVEN class now reaches the store; ADR-0025's deferral is discharged and the
  `M4` comments in `handoff.py` are gone.
- Good: an untouched round trip carries no class diff, where before every categorized molecule
  came back as a proposed clear.
- Good: no schema change. The attribute sits on a dataset that is already additive data under
  the frozen `/conditions` group, and the schema-guard diff stays empty.
- Trade-off: the lookup follows the list's *current* order. Reordering or removing categories
  while an SMD is out in tMAVEN changes what its classes mean on the way back. The return leg never
  writes without an accept, and the prompt shows the category each class proposes, so the change
  is visible rather than silent; the user documentation says so.
- Trade-off: the return-leg category write goes straight to `/molecules`, as the window write
  always has, and is not logged to `/settings/molecule_audit` the way `set_category` is. This
  record does not change that.
- Enforced by: `tests/test_project_conditions.py` (the pin, every list edit rewrites the attribute,
  the copy carries it, the pre-attribute list, malformed and disagreeing attributes refused),
  `tests/test_handoff.py` (classes out, the untouched round trip, a mapped class resolved and
  written, an unmapped class deferred, the `0` against an unlisted category), `tests/test_reconcile.py`
  (a mapped non-zero class is committable), `tests/test_shell.py` and `tests/test_curation.py`
  (the hotkeys read the same lookup and pin).

## More information

- [ADR-0023](0023-curation-label-codec-and-labels-log.md) — the curation codec that deferred the
  lookup.
- [ADR-0025](0025-tmaven-handoff-and-return-leg-reconcile.md) — the hand-off and return leg.
- [ADR-0068](0068-a-project-holds-one-experiment.md) — the list and lookup travel with the condition.
- `src/tether/project/conditions.py` (`ClassLookup`, `read_class_lookup`, `_write_category_names`),
  `src/tether/project/handoff.py`.

<!--
SPDX-FileCopyrightText: 2026 The Tether Authors
SPDX-License-Identifier: GPL-3.0-or-later
-->

# Architecture decision records

These rules are part of the agent contract. `AGENTS.md` points here and does not itself carry the
numbering mechanics: **an agent that has not read this page may not add an ADR**, because choosing a
number by hand is how two records end up sharing one.

- Add an ADR in the implementation PR for schema/version, dependency/isolation, architectural, or
  scientifically consequential choices. Index it as required by the existing ADR contract.

## Reserving a number

**Never pick the next number by reading `docs/adr/`.** Two agents reading the same directory get the
same answer, and the collision is invisible to git: two records numbered `0057` under *different
filenames* produce no merge conflict on either record, only on the index, where a careless
resolution ships both.

```sh
python3 .agents/bin/claim.py reserve-adr
```

It takes `max(reservations ∪ committed records) + 1` and creates `refs/adr-reservations/NNNN`.
Creating the ref *is* the mutex, the same compare-and-swap the issue claim uses: `201` to the first
writer, `422` to everyone after, and on `422` it advances and retries. The namespace is deliberately
not `refs/tags/` — `hatch-vcs` derives the package version from tags, so a non-version tag breaks
`pip install -e .` and turns `main` red.

It **fails closed on either read**. A dropped read used to mean "no ADRs exist", which returned
`0001` — whose compare-and-swap succeeds, because no *ref* holds it, while
`docs/adr/0001-provenance-first-data-model.md` has existed since M0. A single 403 or 502 was
therefore enough to hand out the exact duplicate the scheme exists to prevent. A read that cannot be
trusted stops the reservation instead.

A reservation is not a record. The number is yours as soon as the ref exists; the record is owed in
the same PR as the implementation, and `docs/adr/README.md` must list it there too.

## Renumbering

Renumbering an ADR is explicitly **non-material** under the [review gate](review.md) — the record's
body does not change, only its name — so it neither re-arms a review nor grants a round. It does
require the index and every inbound cross-link to move with it in the same PR.

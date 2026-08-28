# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Decide whether a tagged commit's post-merge required checks allow a release (issue #266).

This is the decision half of ``release.yml``'s *Require green post-merge checks* step. The
workflow step is fetch-and-pipe only: it dereferences the tag, fetches the tagged commit's
check runs from the Checks API and hands them here together with the raw publish mode. This
script owns both the *verdict* (which required contexts violate) and the *disposition*
(whether a violation is fatal), so both are exercised by offline unit tests
(``tests/test_verify_postmerge_checks.py``) rather than only by a live release —
``release.yml`` itself cannot run in the local gate matrix, and an untested
workflow-embedded comparison is the same fail-open class as the gap this guard closes.

Disposition contract (the exit status IS the disposition):

* ``--publish-mode true``  — a violation is fatal: exit non-zero, one ``::error::`` per
  violating context naming the context and the 40-hex commit.
* ``--publish-mode false`` — the same input is advisory: exit 0, one ``::warning::`` per
  violating context, plus a ``$GITHUB_STEP_SUMMARY`` verdict.
* any other value          — exit non-zero naming the unrecognised mode. Never fall through
  to advisory: an expression upstream that produced ``True`` or an empty string must fail
  the job loudly instead of blessing a red release.
* an empty record stream   — exit non-zero in BOTH dispositions: no records is a failed or
  truncated fetch, not a verdict, so it is the one case where advisory mode still fails.

Why no aggregate (e.g. "every check run at this commit is green") can substitute: on a tag
push, ``release.yml``'s own ``verify tag`` check run is ``in_progress`` on that same commit
while this guard executes, so an all-runs-green aggregate can never be true. The guard
therefore evaluates exactly the required contexts it is handed, one verdict each.

The interpreter is the bare ``ubuntu-latest`` runner Python — the ``verify`` job provisions
no environment — so this module imports the standard library only (contract-tested with
``ast`` against ``sys.stdlib_module_names``).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

#: The one exemption: ``ci.yml``'s ``commitlint`` job carries
#: ``if: github.event_name == 'pull_request'``, so on every push to ``main`` it reports
#: ``skipped`` — requiring ``success`` there would block every release forever. It is a
#: required context all the same, so it stays in the committed list with ``skipped``
#: accepted for it and for nothing else.
_SKIPPED_OK = frozenset({"commitlint"})

_ACCEPTED = frozenset({"success"})


def parse_contexts(raw: str) -> list[str]:
    """The required-context list: one name per line, blanks and ``#`` comments dropped."""
    contexts: list[str] = []
    for line in raw.splitlines():
        name = line.strip()
        if name and not name.startswith("#"):
            contexts.append(name)
    return contexts


def parse_records(stream: str) -> list[dict]:
    """Flatten a check-run record stream into a list of run dicts.

    ``gh api --paginate`` on ``/commits/<sha>/check-runs`` emits one JSON page object per
    page, concatenated (``{"total_count": …, "check_runs": […]}{"total_count": …}``), so a
    single ``json.loads`` cannot read it. Decode value-by-value and accept every shape a
    caller might reasonably pipe: page objects, bare run objects, or arrays of either.
    """
    decoder = json.JSONDecoder()
    values: list = []
    index = 0
    length = len(stream)
    while index < length:
        while index < length and stream[index].isspace():
            index += 1
        if index >= length:
            break
        value, index = decoder.raw_decode(stream, index)
        values.append(value)

    runs: list[dict] = []
    for value in values:
        if isinstance(value, list):
            values.extend(value)
        elif isinstance(value, dict) and "check_runs" in value:
            runs.extend(run for run in value["check_runs"] if isinstance(run, dict))
        elif isinstance(value, dict):
            runs.append(value)
    return runs


def _recency_key(run: dict) -> tuple[int, str, str]:
    """Sort key under which the LAST element of a sorted run list is the deciding run.

    The Checks API's default ``filter=latest`` dedupes only *within* a check suite — a
    re-run legitimately supersedes its failure there — but one context can still carry runs
    in several suites (the nightly ``schedule`` and the ``push``, proven at ``e70a12c4``),
    so cross-suite recency is applied here: the latest completed run wins, ordered by
    ``completed_at``. An unfinished run (no ``completed_at`` yet) outranks every finished
    one: its verdict is still pending, and a pending verdict must block — fail-closed —
    rather than be outvoted by an older pass.
    """
    completed = run.get("status") == "completed"
    return (
        0 if completed else 1,
        str(run.get("completed_at") or ""),
        str(run.get("started_at") or ""),
    )


def evaluate(runs: list[dict], contexts: list[str], commit: str) -> list[str]:
    """One verdict per required context; the returned list holds the violations."""
    by_context: dict[str, list[dict]] = {}
    for run in runs:
        name = run.get("name")
        if isinstance(name, str):
            by_context.setdefault(name, []).append(run)

    violations: list[str] = []
    for context in contexts:
        candidates = by_context.get(context)
        if not candidates:
            # Never-reported IS a violation: v1.0.0-rc1 predates sidecar.yml's push
            # trigger, so `sidecar / parity` simply has no run at its commit — a guard
            # that read absence as anything but red would have passed it.
            violations.append(f"required context '{context}' has no check run at {commit}")
            continue
        latest = max(candidates, key=_recency_key)
        status = latest.get("status")
        if status != "completed":
            violations.append(
                f"required context '{context}' latest run has status "
                f"'{status}' (not completed) at {commit}"
            )
            continue
        conclusion = latest.get("conclusion")
        accepted = _ACCEPTED | ({"skipped"} if context in _SKIPPED_OK else frozenset())
        if conclusion not in accepted:
            violations.append(
                f"required context '{context}' latest completed run concluded "
                f"'{conclusion}' at {commit}"
            )
    return violations


def _write_step_summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--commit", required=True, help="the 40-hex tagged commit")
    parser.add_argument(
        "--required-contexts",
        required=True,
        help="newline-separated required context names (the frozen ruleset copy)",
    )
    parser.add_argument(
        "--publish-mode",
        required=True,
        help="steps.resolve.outputs.publish, passed through VERBATIM by the caller",
    )
    parser.add_argument(
        "records",
        nargs="?",
        help="check-run JSON stream (a file path; stdin when omitted)",
    )
    args = parser.parse_args(argv)

    if args.publish_mode == "true":
        fatal = True
    elif args.publish_mode == "false":
        fatal = False
    else:
        print(
            f"::error::unrecognised --publish-mode value '{args.publish_mode}' "
            "(expected 'true' or 'false'); refusing to default to advisory"
        )
        return 1

    contexts = parse_contexts(args.required_contexts)
    if not contexts:
        print("::error::the required-context list is empty; refusing a vacuous verdict")
        return 1

    if args.records:
        with open(args.records, encoding="utf-8") as handle:
            stream = handle.read()
    else:
        stream = sys.stdin.read()
    runs = parse_records(stream)
    if not runs:
        # An empty stream is a failed or truncated fetch, never a verdict — fatal in
        # BOTH dispositions, or a broken fetch would bless every advisory rehearsal.
        print(f"::error::no check-run records for {args.commit}; refusing to conclude anything")
        return 1

    print(f"Evaluated {len(runs)} check-run records at {args.commit}.")
    violations = evaluate(runs, contexts, args.commit)

    if not violations:
        print(f"All {len(contexts)} required contexts are green at {args.commit}.")
        if not fatal:
            _write_step_summary(
                f"Post-merge check guard (advisory): all {len(contexts)} required "
                f"contexts green at `{args.commit}`."
            )
        return 0

    if fatal:
        for violation in violations:
            print(f"::error::{violation}")
        print(f"Refusing to publish: {len(violations)} required context(s) not green.")
        return 1

    for violation in violations:
        print(f"::warning::{violation}")
    _write_step_summary(
        f"Post-merge check guard (advisory): would REFUSE to publish — "
        f"{len(violations)} required context(s) not green at `{args.commit}`:\n\n"
        + "\n".join(f"- {violation}" for violation in violations)
    )
    print(f"Advisory: would refuse to publish ({len(violations)} violation(s) above).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

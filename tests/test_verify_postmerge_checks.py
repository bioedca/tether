# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline unit tests for ``scripts/verify_postmerge_checks.py`` (issue #266).

The script is the decision half of ``release.yml``'s post-merge check gate: the workflow
step only fetches and pipes, so the verdict (which required contexts violate) and the
disposition (whether a violation is fatal) are BOTH exercised here, against committed
fixtures, rather than only by a live release.

Every fixture shape below is real, measured against this repository via the Checks API
(``GET /repos/bioedca/tether/commits/<sha>/check-runs``, retrieved 2026-08-13 for issue
#266 and re-verified 2026-08-28):

* ``v1.0.0-rc1`` (``1ba11268…``) — ten of the eleven contexts present, ``sidecar / parity``
  ABSENT: the tag predates ``sidecar.yml``'s ``push`` trigger (#256). Never-reported.
* ``v0.7.0`` (``4cc32d6a…``) — all eleven present, ``sidecar / parity`` concluded
  ``failure`` in suite 78972199572. The exact violation shape this repository has actually
  published (``v0.6.0`` is the second instance).
* ``c3b3170e…`` — nine contexts ``cancelled`` (the following merge's
  ``cancel-in-progress: true`` cancelled the previous push's CI), ``commitlint``
  ``skipped``, ``secret-scan`` ``success``.
* ``e70a12c4…`` — ``sidecar / parity`` completed in TWO suites (the ``push`` and the
  nightly ``schedule``), which is why cross-suite recency is the script's job: the Checks
  API's default filter dedupes only within a suite.

Dependency-free and stdlib-only, so it runs on the base 3-OS ``test`` matrix.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
SCRIPT = _REPO / "scripts" / "verify_postmerge_checks.py"

_spec = importlib.util.spec_from_file_location("verify_postmerge_checks", SCRIPT)
assert _spec is not None and _spec.loader is not None
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

RC1_COMMIT = "1ba112683a0f2a5ba842e39893fd757bff2d18b3"
V070_COMMIT = "4cc32d6a7d2c358249ac34c18577ded04a974d48"

CONTEXTS = [
    "lint",
    "test (ubuntu-latest)",
    "test (macos-latest)",
    "test (windows-latest)",
    "pre-commit",
    "commitlint",
    "secret-scan",
    "conda-lock-verify",
    "docs-build",
    "schema-guard",
    "sidecar / parity",
]
CONTEXTS_ARG = "\n".join(CONTEXTS)


def _run(
    name: str,
    conclusion: str | None = "success",
    status: str = "completed",
    started: str = "2026-07-12T08:08:40Z",
    completed: str | None = "2026-07-12T08:10:00Z",
) -> dict:
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion if status == "completed" else None,
        "started_at": started,
        "completed_at": completed if status == "completed" else None,
    }


def _all_green() -> list[dict]:
    """All eleven contexts green — ``commitlint`` reports ``skipped`` on a `main` push."""
    return [_run(name, "skipped" if name == "commitlint" else "success") for name in CONTEXTS]


def _v070_shape() -> list[dict]:
    runs = [
        _run(name, "skipped" if name == "commitlint" else "success")
        for name in CONTEXTS
        if name != "sidecar / parity"
    ]
    runs.append(
        _run(
            "sidecar / parity",
            "failure",
            started="2026-07-12T08:40:25Z",
            completed="2026-07-12T08:40:33Z",
        )
    )
    return runs


def test_all_green_yields_no_violations() -> None:
    assert guard.evaluate(_all_green(), CONTEXTS, V070_COMMIT) == []


def test_never_reported_is_a_violation_naming_context_and_commit() -> None:
    """The v1.0.0-rc1 shape: ten contexts present, `sidecar / parity` absent.

    Absence must read as red, not as satisfied: eight of the ten ``v*`` tags have no
    ``sidecar / parity`` run at all, and a guard that treated "never reported" as anything
    but a failure would have passed every one of them.
    """
    runs = [r for r in _all_green() if r["name"] != "sidecar / parity"]
    violations = guard.evaluate(runs, CONTEXTS, RC1_COMMIT)
    assert len(violations) == 1
    assert "sidecar / parity" in violations[0]
    assert RC1_COMMIT in violations[0]
    assert "no check run" in violations[0]


def test_v070_shape_reports_the_published_violation() -> None:
    """All eleven present, `sidecar / parity` concluded failure — v0.7.0's real shape."""
    violations = guard.evaluate(_v070_shape(), CONTEXTS, V070_COMMIT)
    assert len(violations) == 1
    assert "sidecar / parity" in violations[0]
    assert "'failure'" in violations[0]
    assert V070_COMMIT in violations[0]


def test_cancelled_shape_violates_per_cancelled_context() -> None:
    """The c3b3170e shape: a following merge cancelled nine of the eleven mid-run."""
    cancelled = [name for name in CONTEXTS if name not in ("commitlint", "secret-scan")]
    runs = [_run("commitlint", "skipped"), _run("secret-scan", "success")]
    runs += [_run(name, "cancelled") for name in cancelled]
    violations = guard.evaluate(runs, CONTEXTS, V070_COMMIT)
    assert len(violations) == len(cancelled)
    assert all("'cancelled'" in violation for violation in violations)


def test_latest_run_not_completed_is_a_violation() -> None:
    """A pending verdict blocks: an in-flight re-run must not be outvoted by an old pass."""
    for status in ("in_progress", "queued"):
        runs = [r for r in _all_green() if r["name"] != "lint"]
        runs.append(_run("lint", "success", completed="2026-07-12T08:09:00Z"))
        runs.append(_run("lint", None, status=status, started="2026-07-12T09:00:00Z"))
        violations = guard.evaluate(runs, CONTEXTS, V070_COMMIT)
        assert len(violations) == 1, f"status={status}"
        assert "not completed" in violations[0]
        assert f"'{status}'" in violations[0]


def test_latest_completed_run_wins_across_suites() -> None:
    """Cross-suite recency, both directions (the e70a12c4 two-suite shape).

    The fetch keeps the endpoint's default filter — a re-run inside ONE suite legitimately
    supersedes its failure (proven at 6d53c980) — so the only recency the script must add
    is across suites, by ``completed_at``: the newest completed verdict is the verdict.
    """
    base = [r for r in _all_green() if r["name"] != "sidecar / parity"]
    older_pass = _run(
        "sidecar / parity",
        "success",
        started="2026-08-11T01:17:43Z",
        completed="2026-08-11T01:28:09Z",
    )
    newer_fail = _run(
        "sidecar / parity",
        "failure",
        started="2026-08-11T07:47:55Z",
        completed="2026-08-11T07:58:07Z",
    )
    violations = guard.evaluate(base + [older_pass, newer_fail], CONTEXTS, V070_COMMIT)
    assert len(violations) == 1 and "'failure'" in violations[0]

    older_fail = dict(older_pass, conclusion="failure")
    newer_pass = dict(newer_fail, conclusion="success")
    assert guard.evaluate(base + [older_fail, newer_pass], CONTEXTS, V070_COMMIT) == []


def test_skipped_is_accepted_for_commitlint_and_nothing_else() -> None:
    """The single exemption, exempt only from the success requirement.

    ``ci.yml``'s ``commitlint`` job carries ``if: github.event_name == 'pull_request'``,
    so it concludes ``skipped`` on every push to `main`; requiring ``success`` there would
    block every release forever. ``skipped`` anywhere else is a missing verdict.
    """
    assert guard.evaluate(_all_green(), CONTEXTS, V070_COMMIT) == []

    runs = [r for r in _all_green() if r["name"] != "lint"] + [_run("lint", "skipped")]
    violations = guard.evaluate(runs, CONTEXTS, V070_COMMIT)
    assert len(violations) == 1 and "lint" in violations[0] and "'skipped'" in violations[0]

    runs = [r for r in _all_green() if r["name"] != "commitlint"]
    runs.append(_run("commitlint", "failure"))
    violations = guard.evaluate(runs, CONTEXTS, V070_COMMIT)
    assert len(violations) == 1 and "commitlint" in violations[0]


def test_paginated_page_stream_is_flattened() -> None:
    """``gh api --paginate`` concatenates page OBJECTS; the parser must read them all.

    Ten of the eleven required contexts sit past the first page on a busy `main` commit,
    so a parser that read only the first JSON value would report a false 'never reported'
    for most of the list.
    """
    import json

    runs = _all_green()
    stream = json.dumps({"total_count": len(runs), "check_runs": runs[:6]}) + json.dumps(
        {"total_count": len(runs), "check_runs": runs[6:]}
    )
    parsed = guard.parse_records(stream)
    assert len(parsed) == len(runs)
    assert guard.evaluate(parsed, CONTEXTS, V070_COMMIT) == []


def test_context_list_parser_drops_blanks_and_comments() -> None:
    parsed = guard.parse_contexts("\nlint\n  # a comment naming sidecar / parity\n\npre-commit\n")
    assert parsed == ["lint", "pre-commit"]


def _main(tmp_path, monkeypatch, records, publish_mode: str, capsys):
    """Run ``main()`` against *records* with ``GITHUB_STEP_SUMMARY`` sandboxed."""
    import json

    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    payload = tmp_path / "records.json"
    payload.write_text(json.dumps({"check_runs": records}), encoding="utf-8")
    code = guard.main(
        [
            "--commit",
            V070_COMMIT,
            "--required-contexts",
            CONTEXTS_ARG,
            "--publish-mode",
            publish_mode,
            str(payload),
        ]
    )
    out = capsys.readouterr().out
    return code, out, summary


def test_publish_true_makes_a_violation_fatal(tmp_path, monkeypatch, capsys) -> None:
    code, out, _ = _main(tmp_path, monkeypatch, _v070_shape(), "true", capsys)
    assert code != 0
    assert "::error::" in out
    assert "sidecar / parity" in out and V070_COMMIT in out


def test_publish_false_is_advisory_with_a_summary_verdict(tmp_path, monkeypatch, capsys) -> None:
    code, out, summary = _main(tmp_path, monkeypatch, _v070_shape(), "false", capsys)
    assert code == 0
    assert "::warning::" in out and "::error::" not in out
    assert "sidecar / parity" in out
    verdict = summary.read_text(encoding="utf-8")
    assert "REFUSE" in verdict and V070_COMMIT in verdict


def test_unrecognised_publish_mode_fails_closed(tmp_path, monkeypatch, capsys) -> None:
    """`True`, an empty string, or garbage must fail the job, never default to advisory."""
    for mode in ("True", "", "banana", "TRUE", "1"):
        code, out, _ = _main(tmp_path, monkeypatch, _v070_shape(), mode, capsys)
        assert code != 0, f"mode={mode!r} must be refused"
        assert "unrecognised" in out and f"'{mode}'" in out


def test_empty_input_is_fatal_in_both_dispositions(tmp_path, monkeypatch, capsys) -> None:
    """No records is a failed or truncated fetch, not a verdict — advisory must fail too."""
    for mode in ("true", "false"):
        code, out, _ = _main(tmp_path, monkeypatch, [], mode, capsys)
        assert code != 0, f"mode={mode} must fail on an empty record stream"
        assert "::error::" in out and "no check-run records" in out


def test_green_input_passes_in_both_dispositions(tmp_path, monkeypatch, capsys) -> None:
    for mode in ("true", "false"):
        code, out, _ = _main(tmp_path, monkeypatch, _all_green(), mode, capsys)
        assert code == 0, f"mode={mode} must pass on a green commit"
        assert "Evaluated" in out  # the record count reaches the step log


def test_script_imports_the_standard_library_only() -> None:
    """The `verify` job provisions no environment: bare-runner Python, stdlib only.

    ``import yaml`` would pass the local matrix (the test env ships it) and die on the
    runner — exactly the gap between local gates and the release path this script exists
    to close, so the constraint is asserted rather than remembered.
    """
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            imported.add(node.module.partition(".")[0])
    non_stdlib = sorted(
        name for name in imported if name not in sys.stdlib_module_names and name != "__future__"
    )
    assert not non_stdlib, (
        f"scripts/{SCRIPT.name} must import only the standard library (the release "
        f"`verify` job runs it on the bare runner interpreter); found {non_stdlib}"
    )

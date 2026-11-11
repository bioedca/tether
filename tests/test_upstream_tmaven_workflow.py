# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The weekly parity run against the newest upstream tMAVEN (#704) only reports.

``.github/workflows/sidecar-upstream.yml`` runs the ``sidecar`` parity tests with tMAVEN at the
head of its default branch. These tests hold it to the issue: weekly and manual only (so it can
never be a required check), the tested commit in the log, a drill that changes one prior, and no
write to the pin. That it calls the #697 report with the right shape is held by
``tests/test_scheduled_failure_report.py``. That the drill really fails the parity tests was
shown by a live dispatch (the pull request records the run).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml  # provided by the base conda-lock (a mkdocs dependency)

_WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
UPSTREAM = _WORKFLOWS / "sidecar-upstream.yml"
PINNED = _WORKFLOWS / "sidecar.yml"
#: The drill: the consensus VB-HMM `b` prior of the fetched tMAVEN, 0.01 -> 1.0, and nothing else.
DRILL_SED = r"sed -i '/^\[modeler\.vbconhmm\.prior\]$/,/^\[/ s/^b = 0\.01$/b = 1.0/'"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _steps() -> dict[str, dict]:
    return {s["name"]: s for s in _load(UPSTREAM)["jobs"]["upstream"]["steps"] if "name" in s}


def test_it_runs_weekly_and_by_hand_and_never_on_a_pull_request_or_push() -> None:
    workflow = _load(UPSTREAM)
    triggers = workflow.get("on", workflow.get(True))  # YAML 1.1 reads `on:` as True
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    ((cron,),) = [[c["cron"] for c in triggers["schedule"]]]
    minute, hour, day, month, weekday = cron.split()
    assert (day, month) == ("*", "*") and re.fullmatch(r"[0-6]", weekday), cron
    assert workflow["permissions"] == {"contents": "read"}


def test_the_log_names_the_upstream_commit_it_tested() -> None:
    steps = _steps()
    fetch = steps["Fetch the newest upstream tMAVEN commit"]["run"]
    assert 'git clone --quiet --filter=blob:none "$TMAVEN_REPO"' in fetch
    assert "Upstream tMAVEN under test: $sha" in fetch
    named = steps["Name the upstream commit and the first failing comparison"]
    assert named["if"] == "failure() && steps.parity.outcome == 'failure'"
    assert "::error::Upstream tMAVEN commit under test: $SHA" in named["run"]
    assert "::error::First failing comparison:" in named["run"]


def test_the_drill_changes_one_vbconhmm_prior_and_reports_as_a_drill() -> None:
    drill = _steps()["Change one prior in the fetched tMAVEN (drill only)"]
    assert drill["if"] == "inputs.drill == 'drift'"
    assert DRILL_SED in drill["run"]
    report = _load(UPSTREAM)["jobs"]["report"]["with"]
    assert report["drill"] == "${{ inputs.drill == 'drift' }}"
    assert "oracle" not in report  # upstream drift is a `type:ci` signal, not a P0 oracle failure


def test_it_installs_the_fetched_tmaven_and_never_touches_the_pin() -> None:
    text = UPSTREAM.read_text(encoding="utf-8")
    assert not re.search(r"TMAVEN_SPEC\s*[:=]", text)  # the pin's one home stays sidecar.yml
    # The only file it edits is in the fetched clone, and the job token cannot push.
    assert re.findall(r"sed -i .*", text) == [DRILL_SED + ' "$f"']
    assert 'f="$TMAVEN_DIR/tmaven/controllers/modeler/modeler.py"' in text
    assert "permissions" not in _load(UPSTREAM)["jobs"]["upstream"]  # the top-level read only
    install = _steps()["Install the fetched tMAVEN + pytest via the guided sidecar-setup script"]
    assert '--tmaven-spec "$TMAVEN_DIR"' in install["run"]
    # The same tests as the pinned gate, so a failure here means the commit, not the selection.
    pinned = "pytest -m sidecar tests/test_*sidecar*.py"
    assert pinned in PINNED.read_text(encoding="utf-8")
    assert pinned in _steps()["Run the parity tests against the upstream tMAVEN"]["run"]


def test_no_run_step_calls_an_explicit_exit() -> None:
    # Under `bash -el {0}` an explicit `exit 0` reports exit 1 (tests/test_marker_contract.py).
    lines = UPSTREAM.read_text(encoding="utf-8").splitlines()
    assert not [ln for ln in lines if re.match(r"\s*exit\b", ln)]

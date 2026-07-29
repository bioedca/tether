# SPDX-FileCopyrightText: 2026 The Tether Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Contract tests for the swarm approval-digest helper."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".agents" / "skills" / "run-issue-swarm" / "scripts" / "swarm_lease.py"

# A frozen snapshot and the digest it must always produce. The normalization behind this digest
# is a published contract: the live markers on #188, #189, #216 and #218 were re-verified against
# this implementation on 2026-07-29 and reproduced their published digests byte-for-byte. Those
# checks need the GitHub API, so this pinned pair is what CI can enforce offline.
PIN_TITLE = "build(packaging): pin the wheel"
PIN_BODY = "Acceptance criteria\n\n- [ ] one source of truth\n"
PIN_DIGEST = "9906a25c28495a649934b2e809e2b70c136b724b25b025db6907f1797100e0dc"
NON_ASCII_DIGEST = "329909edc2df9090ff2861ea36485b53039d0bd28f79d91eeac2bb2a7b9cb8c8"

MAX_INPUT_BYTES = 131_072
SAMPLE_DIGEST = "b" * 64


def _run(*args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        input=input_text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def _marker(digest: str = SAMPLE_DIGEST) -> str:
    payload = json.dumps({"criteria_sha256": digest, "version": 1}, separators=(",", ":"))
    return f"<!-- tether-agent-ready {payload} -->"


def _write(tmp_path: Path, name: str, text: str) -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8", newline="")
    return str(path)


def _write_marker(tmp_path: Path, name: str, payload: str) -> str:
    return _write(tmp_path, name, f"<!-- tether-agent-ready {payload} -->")


def test_scope_hash_is_pinned_for_a_known_snapshot(tmp_path: Path) -> None:
    result = _run(
        "scope-hash", "--title", PIN_TITLE, "--body-file", _write(tmp_path, "body.md", PIN_BODY)
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == PIN_DIGEST


@pytest.mark.parametrize(
    "body",
    [
        "Acceptance criteria\r\n\r\n- [ ] one source of truth\r\n",
        "Acceptance criteria\r\r- [ ] one source of truth\r",
        "Acceptance criteria\n\n- [ ] one source of truth\n\n\n\n",
    ],
    ids=["crlf", "cr", "extra-trailing-newlines"],
)
def test_scope_hash_normalizes_line_endings_and_trailing_newlines(
    tmp_path: Path, body: str
) -> None:
    result = _run("scope-hash", "--title", PIN_TITLE, "--body-file", _write(tmp_path, "b.md", body))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == PIN_DIGEST


def test_scope_hash_is_pinned_for_a_non_ascii_snapshot(tmp_path: Path) -> None:
    """Pins ensure_ascii=False: \\uXXXX-escaping before hashing changes every non-ASCII issue."""
    body = "Rationale: the γ correction factor — see Hellenkamp 2018.\n"
    path = _write(tmp_path, "body.md", body)
    result = _run("scope-hash", "--title", "fix(fret): γ factor", "--body-file", path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == NON_ASCII_DIGEST


def test_scope_hash_separates_title_from_body(tmp_path: Path) -> None:
    """Moving text across the title/body boundary must change the digest."""
    first = _run("scope-hash", "--title", "ab", "--body-file", _write(tmp_path, "1.md", "c"))
    second = _run("scope-hash", "--title", "a", "--body-file", _write(tmp_path, "2.md", "bc"))
    assert first.returncode == 0 and second.returncode == 0
    assert first.stdout.strip() != second.stdout.strip()


def test_ready_marker_round_trips_through_inspect(tmp_path: Path) -> None:
    rendered = _run(
        "ready-marker", "--title", PIN_TITLE, "--body-file", _write(tmp_path, "body.md", PIN_BODY)
    )
    assert rendered.returncode == 0, rendered.stderr
    # Field order matches every marker already published on a live issue, and SKILL.md.
    assert rendered.stdout.strip() == (
        '<!-- tether-agent-ready {"version":1,"criteria_sha256":"' + PIN_DIGEST + '"} -->'
    )

    inspected = _run(
        "ready-inspect",
        "--file",
        _write(tmp_path, "approval.md", rendered.stdout),
        "--comment-id",
        "5113663198",
    )
    assert inspected.returncode == 0, inspected.stderr
    assert json.loads(inspected.stdout) == {
        "version": 1,
        "criteria_sha256": PIN_DIGEST,
        "comment_id": 5113663198,
    }


def test_verify_requires_an_explicit_body_file(tmp_path: Path) -> None:
    """Omitting --body-file must not silently digest an empty body: the failure mode is a confident
    "the body changed, get a fresh approval" against a perfectly valid approval."""
    approval = _write(tmp_path, "approval.md", _marker(PIN_DIGEST))
    result = _run("verify", "--title", PIN_TITLE, "--approval-file", approval)
    assert result.returncode == 2
    assert "fresh approval is required" not in result.stderr
    assert "--body-file" in result.stderr


def test_verify_binds_a_matching_snapshot_and_rejects_an_edited_one(tmp_path: Path) -> None:
    approval = _write(tmp_path, "approval.md", f"Approved as written.\n\n{_marker(PIN_DIGEST)}\n")
    good = _run(
        "verify",
        "--title",
        PIN_TITLE,
        "--body-file",
        _write(tmp_path, "body.md", PIN_BODY),
        "--approval-file",
        approval,
    )
    assert good.returncode == 0, good.stderr
    assert json.loads(good.stdout)["binds"] is True

    edited = _run(
        "verify",
        "--title",
        PIN_TITLE,
        "--body-file",
        _write(tmp_path, "edited.md", PIN_BODY + "\nOne more criterion.\n"),
        "--approval-file",
        approval,
    )
    assert edited.returncode == 2
    assert "fresh approval is required" in edited.stderr


@pytest.mark.parametrize(
    "extra",
    [
        '<!-- tether-agent-lease {"version":1} -->',
        '<!-- tether-swarm-run {"version":1} -->',
        '<!-- tether-swarm-merge-authority {"version":1} -->',
    ],
    ids=["lease", "run", "merge-authority"],
)
def test_inspect_refuses_an_approval_mixed_with_retired_coordination_state(
    tmp_path: Path, extra: str
) -> None:
    path = _write(tmp_path, "mixed.md", f"{_marker()}\n{extra}\n")
    result = _run("ready-inspect", "--file", path)
    assert result.returncode == 2
    assert "mixes or repeats marker tokens" in result.stderr


def test_inspect_refuses_a_repeated_approval_marker(tmp_path: Path) -> None:
    path = _write(tmp_path, "twice.md", f"{_marker()}\n{_marker()}")
    result = _run("ready-inspect", "--file", path)
    assert result.returncode == 2
    assert "found 2" in result.stderr


@pytest.mark.parametrize(
    "comment_id",
    ["0", "-1", "9223372036854775808"],
    ids=["zero", "negative", "above-signed-64-bit"],
)
def test_comment_id_must_be_a_positive_signed_64_bit_integer(
    tmp_path: Path, comment_id: str
) -> None:
    """_positive_github_id is on the keep list, so it needs coverage that fails if it is removed."""
    path = _write(tmp_path, "approval.md", _marker())
    result = _run("ready-inspect", "--file", path, "--comment-id", comment_id)
    assert result.returncode == 2
    assert "positive signed 64-bit integer" in result.stderr


def test_comment_id_accepts_the_largest_valid_id(tmp_path: Path) -> None:
    path = _write(tmp_path, "approval.md", _marker())
    result = _run("ready-inspect", "--file", path, "--comment-id", "9223372036854775807")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["comment_id"] == 9223372036854775807


def test_inspect_ignores_a_prose_mention_of_an_unrelated_anchor(tmp_path: Path) -> None:
    """A grooming or re-scope anchor named in prose is not a claim and must not trip the check."""
    body = f"See `<!-- tether-rescope-v1 -->` in the body above.\n\n{_marker()}\n"
    result = _run("ready-inspect", "--file", _write(tmp_path, "prose.md", body))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["criteria_sha256"] == SAMPLE_DIGEST


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ('{"version":1}', "missing fields: criteria_sha256"),
        (f'{{"version":1,"criteria_sha256":"{SAMPLE_DIGEST}","run_id":"x"}}', "unknown fields"),
        (f'{{"version":2,"criteria_sha256":"{SAMPLE_DIGEST}"}}', "unsupported approval version"),
        (f'{{"version":true,"criteria_sha256":"{SAMPLE_DIGEST}"}}', "unsupported approval version"),
        ('{"version":1,"criteria_sha256":"B00F"}', "lowercase SHA-256 digest"),
        (f'{{"version":1,"criteria_sha256":"{SAMPLE_DIGEST.upper()}"}}', "lowercase SHA-256"),
        (f'{{"version":1,"criteria_sha256":{{"a":"{SAMPLE_DIGEST}"}}}}', "lowercase SHA-256"),
    ],
)
def test_inspect_rejects_malformed_approval_records(
    tmp_path: Path, payload: str, message: str
) -> None:
    result = _run("ready-inspect", "--file", _write_marker(tmp_path, "bad.md", payload))
    assert result.returncode == 2
    assert message in result.stderr


def test_duplicate_json_keys_are_rejected_without_reflecting_input(tmp_path: Path) -> None:
    payload = f'{{"version":1,"criteria_sha256":"{SAMPLE_DIGEST}","version":1}}'
    result = _run("ready-inspect", "--file", _write_marker(tmp_path, "dupe.md", payload))
    assert result.returncode == 2
    assert "repeats a field" in result.stderr
    assert SAMPLE_DIGEST not in result.stderr


def test_nonfinite_json_constants_are_rejected(tmp_path: Path) -> None:
    payload = '{"version":NaN,"criteria_sha256":"x"}'
    result = _run("ready-inspect", "--file", _write_marker(tmp_path, "nan.md", payload))
    assert result.returncode == 2
    assert "non-finite constant" in result.stderr


def test_oversized_stdin_is_rejected_before_unbounded_reading() -> None:
    result = _run("ready-inspect", "--file", "-", input_text="x" * (MAX_INPUT_BYTES + 1))
    assert result.returncode == 2
    assert "safe read limit" in result.stderr


def test_stdin_is_bounded_by_encoded_size_not_character_count() -> None:
    """A multibyte body under the character limit can still exceed the byte limit."""
    result = _run("ready-inspect", "--file", "-", input_text="é" * (MAX_INPUT_BYTES // 2 + 1))
    assert result.returncode == 2
    assert "safe read limit" in result.stderr


def test_the_file_branch_is_bounded_too_and_does_not_truncate(tmp_path: Path) -> None:
    """Cap the path branch too, else an oversized file is truncated and parsed as if complete."""
    path = _write(tmp_path, "huge.md", f"{_marker()}\n" + "x" * MAX_INPUT_BYTES)
    result = _run("ready-inspect", "--file", path)
    assert result.returncode == 2
    assert "safe read limit" in result.stderr


def test_deeply_nested_json_is_rejected_without_a_traceback(tmp_path: Path) -> None:
    """Must be an OBJECT: READY_RE captures only `{...}`, so a bare `[[[...]]]` payload would fail
    the marker count before json.loads ran, and the test would pass for the wrong reason."""
    payload = '{"a":' * 20_000 + "1" + "}" * 20_000
    result = _run("ready-inspect", "--file", _write_marker(tmp_path, "deep.md", payload))
    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    # Names the parser, proving the recursion guard - not the marker-count check - rejected this.
    assert "approval JSON is invalid" in result.stderr


def test_missing_input_file_never_emits_its_path(tmp_path: Path) -> None:
    missing = tmp_path / "absent" / "approval.md"
    result = _run("ready-inspect", "--file", str(missing))
    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert str(missing) not in result.stderr
    assert "absent" not in result.stderr


def test_undecodable_stdin_never_emits_a_traceback_or_local_path() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "ready-inspect", "--file", "-"],
        cwd=ROOT,
        input=b"\xff\xfe\x00garbage",
        capture_output=True,
        check=False,
    )
    stderr = result.stderr.decode("utf-8", errors="replace")
    assert result.returncode == 2
    assert "Traceback" not in stderr
    assert str(ROOT) not in stderr

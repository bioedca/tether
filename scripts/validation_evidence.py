# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Write and check the evidence records of the validation suite (#1439, ADR-0155).

The specification (§9.5) says the validation suite runs end to end when every gate oracle has
passing evidence bound to the release commit: one record per oracle, (a) to (g), and one for the
deep add-on run, each naming the commit it was produced at. A record is one small JSON file of
schema ``tether-validation-record/1``. It holds summary numbers and neutral ids only:

* ``oracle`` — ``a`` to ``g``, or ``deep``;
* ``tier`` — the tier of that oracle's headline assertion (``docs/validation.md``);
* ``commit`` — the 40-character SHA the run was made at;
* ``evidence`` — exactly one of ``run_url`` (a GitHub Actions run of this repository) or
  ``log_sha256`` (the SHA-256 of the local run's log or result file, which is not committed);
* ``measured`` — the measured numbers, a flat map of ``snake_case`` names to numbers;
* ``tolerance`` — the rows of the specification's table of tunables (§11.2) or the frozen files
  the numbers are compared with;
* ``outcome`` — ``passed`` or ``failed``.

The *release set* of a tag ``vX.Y.Z`` is the directory ``docs/validation/release/vX.Y.Z/`` with
one file per record (``oracle-a.json`` … ``oracle-g.json``, ``deep.json``). Every record in it
names the same commit, the *release commit*; every gate oracle and the deep run passed, and (c),
advisory, may have failed. The tag is placed on a commit that differs from the release commit only
by the files of its own set (a record cannot name the commit that contains it).

Subcommands::

    python scripts/validation_evidence.py record --oracle b --commit SHA \\
        --run-url https://github.com/bioedca/tether/actions/runs/N \\
        --measured viterbi_agreement=0.97 --tolerance schema/parity_tolerance.json \\
        --outcome passed --out docs/validation/release/v1.0.0/oracle-b.json
    python scripts/validation_evidence.py from-oracle-a \\
        --record docs/validation/oracle-a-last-run.json --result <workstation result file> \\
        --out docs/validation/release/v1.0.0/oracle-a.json
    python scripts/validation_evidence.py check-set docs/validation/release/v1.0.0
    python scripts/validation_evidence.py check-release --tag v1.0.0 --required true

``check-release`` is the gate in ``release.yml``. With ``--required true`` (a stable tag that
publishes) a missing or broken set fails the job; otherwise it reports and exits 0. Only the
standard library is used, so the release runner needs nothing installed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "tether-validation-record/1"
RELEASE_DIR = "docs/validation/release"
KEYS = {"schema", "oracle", "tier", "commit", "evidence", "measured", "tolerance", "outcome"}
OUTCOMES = {"passed", "failed"}
ADVISORY = {"c"}  # §9.5: (c) may be recorded advisory; no other oracle may.
RUN_URL = re.compile(r"https://github\.com/bioedca/tether/actions/runs/\d+(/attempts/\d+)?")
SHA40 = re.compile(r"[0-9a-f]{40}")
SHA64 = re.compile(r"[0-9a-f]{64}")
NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
TAG = re.compile(r"v\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?")

# The tier of each headline assertion and the tolerances it may name, transcribed from the
# oracle table of docs/validation.md (§The validation suite). A tolerance is a row name of the
# specification's §11.2 table or a frozen file; the test checks that each one exists.
ORACLES: dict[str, dict[str, object]] = {
    "a": {
        "tier": "workstation",
        "tolerance": {"Registration RMS-residual gate", "src/tether/project/oracle.py"},
    },
    "b": {
        "tier": "sidecar",
        "tolerance": {"Idealization parity tolerance", "schema/parity_tolerance.json"},
    },
    "c": {
        "tier": "large",
        "tolerance": {"kinSoftChallenge parity band", "schema/kinsoft_reference.json"},
    },
    "d": {"tier": "required", "tolerance": {"Ranker success target"}},
    "e": {
        "tier": "required",
        "tolerance": {
            "Leakage acceptance ceiling",
            "Leakage-α validation band",
            "γ acceptance ceiling",
        },
    },
    "f": {"tier": "required", "tolerance": {"schema/schema_frozen.json"}},
    "g": {"tier": "required", "tolerance": {"Bleach-frame tolerance"}},
    "deep": {"tier": "deep", "tolerance": {"schema/deep_classifier_reference.json"}},
}
# §9.5: a green deep-gpu.yml run alone is not the deep evidence; the CUDA tests must have run.
DEEP_MEASURED = ("cuda_tests_executed", "cuda_tests_skipped")


def file_name(oracle: str) -> str:
    """The file name of one record in a release set."""
    return "deep.json" if oracle == "deep" else f"oracle-{oracle}.json"


def record_errors(record: object) -> list[str]:
    """Every way ``record`` breaks the record format; an empty list is a well-formed record."""
    if not isinstance(record, dict):
        return ["a record is a JSON object"]
    errors: list[str] = []
    if set(record) != KEYS:
        errors.append(f"keys must be exactly {sorted(KEYS)}, got {sorted(record)}")
    if record.get("schema") != SCHEMA:
        errors.append(f"schema must be {SCHEMA!r}")
    oracle = record.get("oracle")
    spec = ORACLES.get(oracle) if isinstance(oracle, str) else None
    if spec is None:
        errors.append(f"oracle must be one of {sorted(ORACLES)}, got {oracle!r}")
    elif record.get("tier") != spec["tier"]:
        errors.append(
            f"oracle ({oracle}) runs in tier {spec['tier']!r}, not {record.get('tier')!r}"
        )
    if not (isinstance(record.get("commit"), str) and SHA40.fullmatch(record["commit"])):
        errors.append("commit must be a 40-character lower-case SHA")
    evidence = record.get("evidence")
    if not isinstance(evidence, dict) or len(evidence) != 1:
        errors.append("evidence must hold exactly one of run_url or log_sha256")
    else:
        ((kind, value),) = evidence.items()
        pattern = {"run_url": RUN_URL, "log_sha256": SHA64}.get(kind)
        if pattern is None or not (isinstance(value, str) and pattern.fullmatch(value)):
            errors.append(f"evidence {kind!r} is not a run URL of this repository or a SHA-256")
    measured = record.get("measured")
    if not isinstance(measured, dict) or not measured:
        errors.append("measured must be a non-empty map of names to numbers")
    else:
        before = len(errors)
        for key, value in measured.items():
            if not NAME.fullmatch(key):
                errors.append(f"measured name {key!r} is not snake_case")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                errors.append(f"measured {key!r} is not a number")
            elif not math.isfinite(value):
                errors.append(f"measured {key!r} is not finite")
        if oracle == "deep" and len(errors) == before:
            if any(k not in measured for k in DEEP_MEASURED):
                errors.append(f"the deep record must give {', '.join(DEEP_MEASURED)}")
            elif measured["cuda_tests_executed"] < 1 or measured["cuda_tests_skipped"] != 0:
                errors.append("the deep record must show its CUDA tests executed, not skipped")
    tolerance = record.get("tolerance")
    if not isinstance(tolerance, list) or not tolerance:
        errors.append("tolerance must name at least one §11.2 row or frozen file")
    elif spec is not None:
        unknown = [t for t in tolerance if t not in spec["tolerance"]]
        if unknown:
            errors.append(f"oracle ({oracle}) does not compare with {unknown}")
    if record.get("outcome") not in OUTCOMES:
        errors.append(f"outcome must be one of {sorted(OUTCOMES)}")
    return errors


def set_errors(directory: Path) -> list[str]:
    """Every way the release set in ``directory`` falls short; empty means it is complete."""
    if not directory.is_dir():
        return [f"{directory}: no release set"]
    expected = {file_name(o) for o in ORACLES}
    present = {p.name for p in directory.iterdir()}
    errors = [f"missing {name}" for name in sorted(expected - present)]
    errors += [f"unexpected file {name}" for name in sorted(present - expected)]
    commits: dict[str, str] = {}
    for oracle in ORACLES:
        path = directory / file_name(oracle)
        if not path.is_file():
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append(f"{path.name}: unreadable ({exc})")
            continue
        problems = record_errors(record)
        if not problems and record["oracle"] != oracle:
            problems.append(f"names oracle {record['oracle']!r}")
        errors += [f"{path.name}: {p}" for p in problems]
        if problems:
            continue
        commits[path.name] = record["commit"]
        if record["outcome"] != "passed" and oracle not in ADVISORY:
            errors.append(f"{path.name}: outcome {record['outcome']!r}; only (c) may be advisory")
    if len(set(commits.values())) > 1:
        errors.append(f"the records name more than one commit: {commits}")
    return errors


def release_commit(directory: Path) -> str:
    """The one commit a complete release set names."""
    record = json.loads((directory / file_name("a")).read_text(encoding="utf-8"))
    return str(record["commit"])


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def binding_errors(tag: str, tag_commit: str) -> list[str]:
    """Why the tag commit is not its release commit plus the files of its own set."""
    rel = f"{RELEASE_DIR}/{tag}"
    errors = set_errors(ROOT / rel)
    if errors:
        return errors
    commit = release_commit(ROOT / rel)
    try:
        _git("merge-base", "--is-ancestor", commit, tag_commit)
    except subprocess.CalledProcessError:
        return [f"the release commit {commit} is not an ancestor of the tag commit {tag_commit}"]
    changed = _git("diff", "--name-only", commit, tag_commit).splitlines()
    outside = [p for p in changed if not p.startswith(rel + "/")]
    if outside:
        return [f"the tag commit changes more than {rel}/ since {commit[:12]}: {outside}"]
    return []


def _measured(pairs: list[str]) -> dict[str, float | int]:
    values: dict[str, float | int] = {}
    for pair in pairs:
        key, sep, text = pair.partition("=")
        if not sep:
            raise SystemExit(f"--measured wants name=value, got {pair!r}")
        values[key] = int(text) if re.fullmatch(r"-?\d+", text) else float(text)
    return values


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(record: dict, out: Path) -> int:
    errors = record_errors(record)
    if errors:
        print("\n".join(f"error: {e}" for e in errors), file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return 0


def from_oracle_a(record_a: dict, result: Path) -> dict:
    """The suite record of oracle (a) made from one ``tether-oracle-a-record/1`` (#699).

    The workstation result file the record was cut from is hashed and its name must be the one
    the record states, so the evidence is the file the numbers came from.
    """
    if record_a.get("schema") != "tether-oracle-a-record/1":
        raise SystemExit("--record is not a tether-oracle-a-record/1")
    if record_a.get("result_file") not in (None, result.name):
        raise SystemExit(f"--result is {result.name}, the record names {record_a['result_file']}")
    check = record_a["check"]
    numbers = {k: v for ds in check["datasets"].values() for k, v in ds.items()}
    return {
        "schema": SCHEMA,
        "oracle": "a",
        "tier": "workstation",
        "commit": record_a["commit"],
        "evidence": {"log_sha256": _sha256(result)},
        "measured": numbers,
        "tolerance": ["src/tether/project/oracle.py", "Registration RMS-residual gate"],
        "outcome": "passed" if check["outcome"] == "passed" else "failed",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record", help="write one record from a run")
    rec.add_argument("--oracle", required=True, choices=sorted(ORACLES))
    rec.add_argument("--commit", required=True)
    group = rec.add_mutually_exclusive_group(required=True)
    group.add_argument("--run-url")
    group.add_argument("--log", type=Path, help="local-run log; only its SHA-256 is recorded")
    rec.add_argument("--measured", action="append", default=[], metavar="NAME=VALUE")
    rec.add_argument("--tolerance", action="append", required=True)
    rec.add_argument("--outcome", required=True, choices=sorted(OUTCOMES))
    rec.add_argument("--out", required=True, type=Path)
    fa = sub.add_parser("from-oracle-a", help="write the (a) record from the #699 record")
    fa.add_argument("--record", required=True, type=Path)
    fa.add_argument("--result", required=True, type=Path)
    fa.add_argument("--out", required=True, type=Path)
    cs = sub.add_parser("check-set", help="check one release set directory")
    cs.add_argument("directory", type=Path)
    cr = sub.add_parser("check-release", help="the release.yml gate for one tag")
    cr.add_argument("--tag", required=True)
    cr.add_argument("--required", required=True, choices=["true", "false"])
    args = parser.parse_args(argv)

    if args.command == "record":
        evidence = {"run_url": args.run_url} if args.run_url else {"log_sha256": _sha256(args.log)}
        return _write(
            {
                "schema": SCHEMA,
                "oracle": args.oracle,
                "tier": ORACLES[args.oracle]["tier"],
                "commit": args.commit,
                "evidence": evidence,
                "measured": _measured(args.measured),
                "tolerance": args.tolerance,
                "outcome": args.outcome,
            },
            args.out,
        )
    if args.command == "from-oracle-a":
        record_a = json.loads(args.record.read_text(encoding="utf-8"))
        return _write(from_oracle_a(record_a, args.result), args.out)
    if args.command == "check-set":
        errors = set_errors(args.directory)
    else:
        if not TAG.fullmatch(args.tag):
            print(f"error: {args.tag!r} is not a release tag", file=sys.stderr)
            return 1
        try:
            tag_commit = _git("rev-list", "-n1", f"refs/tags/{args.tag}")
        except subprocess.CalledProcessError:
            errors = [f"no local tag {args.tag}"]
        else:
            errors = binding_errors(args.tag, tag_commit)
        if errors and args.required == "false":
            print("\n".join(f"::warning::{e}" for e in errors))
            print("The release set is not required for this run (a prerelease or a dry run).")
            return 0
    if errors:
        print("\n".join(f"::error::{e}" for e in errors))
        return 1
    print("The release set is complete and names one commit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

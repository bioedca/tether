# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Measure the cross-seed idealization-parity spread and freeze §11.2 (M0.5 S4).

Dev/ratification tool (not imported by the package, like ``make_fixtures.py``).
Runs ``tether.idealize.run_vbfret`` ``--n-runs`` times (self-reseeded) on the
committed SMD fixtures, measures the spread of the four parity metrics, and
writes ``schema/parity_tolerance.json`` — the machine-readable freeze that CI's
``sidecar.yml`` asserts against and PRD §11.2 mirrors.

Two comparison anchors (PRD §8 NFR-VALID(b), §9 M0.5):

* **281-mol** fits are compared to the *committed reference model*
  (``tests/fixtures/large/model_281mol.hdf5``) — exactly what ``sidecar.yml``
  will assert (fresh fit vs the frozen reference).
* **4-mol** has no committed model, so its fits are compared cross-seed to the
  first run (the same kind of self-reseeded spread).

Run (sidecar env on PATH via the env var)::

    TETHER_SIDECAR_PYTHON=<sidecar env>/bin/python \
    NUMBA_CACHE_DIR=<persistent dir> PYTHONPATH=src \
    python scripts/measure_parity.py --n-runs 20 --out schema/parity_tolerance.json

The written ``method`` block records **what was compared against** — the sidecar
CPython version and the tMAVEN upstream commit (:func:`probe_sidecar_build`) —
never ``$TETHER_SIDECAR_PYTHON`` itself: an absolute path names a machine, not a
build, and would put a local filesystem layout into a committed public artifact.
That holds on the failure path too: a probe error is rebuilt from safe attributes
(:func:`_sanitized_probe_error`) and never includes child-process diagnostics,
because ``CalledProcessError`` and ``TimeoutExpired`` can carry the whole argv,
stderr, URLs, credentials, and unrelated machine-identifying paths.

The frozen numbers are a one-time M0.5 ratification: regenerate only with an ADR
+ a deliberate re-freeze (PRD §11.2 "frozen from the measured cross-seed spread
at M0.5").
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path

from tether.idealize.parity import PROVISIONAL, freeze, measure_spread

#: Run inside the *sidecar* interpreter to identify the tMAVEN build that was
#: compared against. ``direct_url.json`` is PEP 610 metadata that ``pip install
#: git+…@<ref>`` writes, so it carries the resolved 40-char upstream commit even
#: when the spec named a short ref or a branch.
_BUILD_PROBE = """
import json, platform
out = {"sidecar_python_version": platform.python_version(), "tmaven_commit": "unrecorded"}
try:
    import importlib.metadata as md

    raw = md.distribution("tmaven").read_text("direct_url.json")
    commit = (json.loads(raw).get("vcs_info") or {}).get("commit_id") if raw else None
    if commit:
        out["tmaven_commit"] = commit
except Exception as exc:  # a non-VCS install (wheel/sdist) records no commit
    out["tmaven_commit_probe_error"] = f"{type(exc).__name__}: {exc}"
print(json.dumps(out))
"""

# Fixture -> (reference anchor, fitted state count). 281-mol anchors on the
# committed reference model; 4-mol anchors cross-seed on its own first run.
_FIXTURES = {
    "smd_4mol": {
        "smd": "tests/fixtures/smd_4mol.hdf5",
        "reference": None,  # cross-seed (first run)
        "nstates": 2,
    },
    "smd_281mol": {
        "smd": "tests/fixtures/large/smd_281mol.hdf5",
        "reference": "tests/fixtures/large/model_281mol.hdf5",
        "nstates": 4,
    },
}


_REDACTED = "$TETHER_SIDECAR_PYTHON"
_BUILD_PROVENANCE = (
    "The measurement-time build probe obtains sidecar_python_version by executing "
    "platform.python_version() and tmaven_commit from the installed tMAVEN distribution's "
    "PEP 610 direct_url.json vcs_info.commit_id inside the configured sidecar interpreter; "
    "the interpreter path is intentionally not recorded."
)
_PYTHON_VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+[A-Za-z0-9.+-]*")
_TMAVEN_COMMIT_RE = re.compile(r"[0-9a-fA-F]{40}")


def _sanitized_probe_error(exc: BaseException, _sidecar_python: str) -> str:
    """Describe ``exc`` using only allowlisted, non-payload diagnostics.

    Child-process stderr and generic exception messages are untrusted: either may
    contain paths, URLs, credentials, or other machine-specific data unrelated to
    the configured interpreter. Persist only the exception category plus a numeric
    exit code or configured timeout where applicable. Parsing and validation
    failures use fixed descriptions, and every other failure records only its type.
    """
    if isinstance(exc, subprocess.CalledProcessError):
        return f"CalledProcessError: exit {exc.returncode}"
    if isinstance(exc, subprocess.TimeoutExpired):
        return f"TimeoutExpired: timed out after {exc.timeout}s"
    if isinstance(exc, json.JSONDecodeError):
        return "JSONDecodeError: invalid JSON build probe output"
    if isinstance(exc, ValueError):
        return "ValueError: invalid build probe output"
    return f"{type(exc).__name__}: build probe failed"


def _unrecorded_build(error: str) -> dict[str, str]:
    """Return a complete, path-free record for a probe that could not be trusted."""
    return {
        "sidecar_python_version": "unrecorded",
        "tmaven_commit": "unrecorded",
        "build_provenance": _BUILD_PROVENANCE,
        "build_probe_error": error,
    }


def _validated_build(probed: object) -> dict[str, str]:
    """Select and validate the two safe facts emitted by :data:`_BUILD_PROBE`."""
    if not isinstance(probed, dict):
        raise ValueError("build probe output is not a JSON object")
    python_version = probed.get("sidecar_python_version")
    tmaven_commit = probed.get("tmaven_commit")
    if not isinstance(python_version, str) or not _PYTHON_VERSION_RE.fullmatch(python_version):
        raise ValueError("build probe did not return a valid CPython version")
    if not isinstance(tmaven_commit, str):
        raise ValueError("build probe did not return a 40-hex tMAVEN commit")
    if tmaven_commit == "unrecorded":
        normalized_commit = tmaven_commit
    elif _TMAVEN_COMMIT_RE.fullmatch(tmaven_commit):
        normalized_commit = tmaven_commit.lower()
    else:
        raise ValueError("build probe did not return a 40-hex tMAVEN commit")
    return {
        "sidecar_python_version": python_version,
        "tmaven_commit": normalized_commit,
        "build_provenance": _BUILD_PROVENANCE,
    }


def probe_sidecar_build(sidecar_python: str | None) -> dict[str, str]:
    """Return the reproducibility facts that identify the measured comparison.

    The result always includes ``sidecar_python_version``, ``tmaven_commit``, and
    ``build_provenance``. The first two identify the sidecar CPython and installed
    tMAVEN upstream commit; the last records how both facts were measured. Deliberately
    absent is the interpreter path: the frozen artifact is committed and published,
    and a path identifies a machine rather than a build. A non-VCS tMAVEN install
    preserves the validated Python version while recording only its commit as
    ``"unrecorded"``. Any other failure degrades both facts to ``"unrecorded"`` plus
    a *sanitized* probe-error field (see
    :func:`_sanitized_probe_error`) so a long measurement run is never lost to a
    provenance probe — and never leaks the path through an exception message either.
    """
    if not sidecar_python:
        return _unrecorded_build(f"{_REDACTED} is unset")
    try:
        proc = subprocess.run(  # noqa: S603 - sidecar_python is the configured interpreter
            [sidecar_python, "-c", _BUILD_PROBE],
            capture_output=True,
            text=True,
            timeout=120.0,
            check=True,
        )
        lines = proc.stdout.strip().splitlines()
        if not lines:
            raise ValueError("build probe returned no output")
        probed = json.loads(lines[-1])
        return _validated_build(probed)
    except Exception as exc:
        return _unrecorded_build(_sanitized_probe_error(exc, sidecar_python))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n-runs", type=int, default=20, help="self-reseeded fits per fixture")
    ap.add_argument("--margin", type=float, default=0.5, help="safety margin for freeze()")
    ap.add_argument("--out", default="schema/parity_tolerance.json")
    ap.add_argument("--scratch", default=None, help="scratch dir for run model files")
    ap.add_argument(
        "--fixtures",
        nargs="+",
        default=list(_FIXTURES),
        choices=list(_FIXTURES),
        help="which fixtures to measure",
    )
    ap.add_argument(
        "--model-type",
        default="vbconhmm",
        choices=["vbconhmm", "ebhmm", "vbfret"],
        help="idealizer to measure the cross-seed spread of (default: vbconhmm, the "
        "M0.5 reference method). Use 'ebhmm' to ratify a per-method ebFRET tolerance.",
    )
    ap.add_argument(
        "--cross-seed",
        action="store_true",
        help="anchor every fixture cross-seed on its own first run (reference=None), "
        "ignoring any committed reference model. Required when measuring a method "
        "whose committed reference was fit with a different method — an ebFRET fit's "
        "ELBO is not commensurable with the vbconhmm reference model's.",
    )
    args = ap.parse_args()

    # A non-vbconhmm method compared to the committed (vbconhmm) reference model is
    # not commensurable — its ELBO is a different model's variational bound — so
    # measuring it against that reference silently corrupts the freeze. Enforce the
    # cross-seed anchor the --model-type help promises, rather than only documenting it.
    if args.model_type != "vbconhmm" and not args.cross_seed:
        referenced = [name for name in args.fixtures if _FIXTURES[name]["reference"] is not None]
        if referenced:
            ap.error(
                f"--cross-seed is required for --model-type {args.model_type}: fixtures "
                f"{referenced} carry a committed reference model fit with vbconhmm, whose "
                "ELBO is not commensurable with this method's — measure cross-seed instead"
            )

    scratch = (
        args.scratch or os.environ.get("TMPDIR") or str(Path(args.out).parent / "_parity_runs")
    )

    per_fixture: dict[str, dict] = {}
    # Worst-case spread across *all* measured fixtures defines the frozen bound.
    combined: dict[str, list[float]] = {
        "state_count_fraction": [],
        "state_mean_abs_delta": [],
        "viterbi_agreement": [],
        "relative_elbo": [],
    }

    for name in args.fixtures:
        cfg = _FIXTURES[name]
        reference = None if args.cross_seed else cfg["reference"]
        mode = "cross-seed" if reference is None else "vs-reference"
        print(
            f"\n=== measuring {name} ({args.model_type}, n={args.n_runs}, "
            f"nstates={cfg['nstates']}, {mode}) ==="
        )
        spread, per_run = measure_spread(
            cfg["smd"],
            reference=reference,
            n_runs=args.n_runs,
            model_type=args.model_type,
            nstates=cfg["nstates"],
            scratch_dir=str(Path(scratch) / name),
        )
        per_fixture[name] = {
            "smd": cfg["smd"],
            "reference": reference or "cross-seed (run00 anchor)",
            "nstates": cfg["nstates"],
            "n_comparisons": len(per_run),
            "metrics": {k: v.as_dict() for k, v in spread.items()},
        }
        for k, summ in spread.items():
            combined[k].extend(summ.values)

    # Re-summarise the pooled values to feed freeze().
    from tether.idealize.parity import SpreadSummary

    pooled = {
        "state_count_fraction": SpreadSummary(
            "state_count_fraction", "floor", combined["state_count_fraction"]
        ),
        "state_mean_abs_delta": SpreadSummary(
            "state_mean_abs_delta", "ceiling", combined["state_mean_abs_delta"]
        ),
        "viterbi_agreement": SpreadSummary(
            "viterbi_agreement", "floor", combined["viterbi_agreement"]
        ),
        "relative_elbo": SpreadSummary("relative_elbo", "ceiling", combined["relative_elbo"]),
    }
    tolerance = freeze(pooled, margin=args.margin)

    out = {
        "schema_version": 1,
        "frozen_at_milestone": "M0.5",
        "measured_utc": time.strftime("%Y-%m-%d", time.gmtime()),
        "method": {
            "model_type": args.model_type,
            "n_runs_per_fixture": args.n_runs,
            **probe_sidecar_build(os.environ.get("TETHER_SIDECAR_PYTHON")),
            "note": "tMAVEN self-reseeds; parity is statistical, never bit-exact (PRD §7.4).",
        },
        "coverage": {
            "measured_methods": [args.model_type],
            "applied_to": [
                "vbFRET (per-trace, M2)",
                "consensus VB-HMM (M6)",
                "ebFRET (M6)",
            ],
            "note": (
                "The §11.2 row is ONE tolerance applied to all idealization methods. M0.5 "
                "measured the cross-seed spread on the vb Consensus HMM path only (the committed "
                "reference-model type + the M6 method + the Appendix-D.2 fixture type). Per-trace "
                "vbFRET and ebFRET are asserted against this same frozen row at M2/M6, where their "
                "own fixtures exist; their cross-seed spread is NOT separately measured here. See "
                "ADR-0009."
            ),
        },
        "freeze_policy": (
            "frozen = more permissive of (provisional §11.2 default, measured worst-case "
            f"± margin); margin={args.margin}: ceilings ×(1+margin), floors −margin·(1−worst)."
        ),
        "provisional": PROVISIONAL,
        "tolerance": tolerance,
        "pooled_worst": {k: v.worst for k, v in pooled.items()},
        "spread_by_fixture": per_fixture,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"\nFROZEN tolerance -> {out_path}")
    print(json.dumps(tolerance, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

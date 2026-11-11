# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Measure the donor-side check's false-alarm and detection rates (#646, ADR-0070).

Reproduces every number in ADR-0070's evidence tables, from two sources:

* **The repository's trace fixtures**, each read with the labels as stored ("correct")
  and with the two channels exchanged ("swapped"): ``tests/fixtures/smd_2mol.hdf5``,
  ``tests/fixtures/smd_4mol.hdf5`` and, when Git LFS has materialised them,
  ``tests/fixtures/large/smd_281mol.hdf5`` (real UCKOPSB T-box/tRNA traces) and the three
  kinSoftChallenge levels in ``tests/fixtures/large/kinsoft_sim.hdf5``.
* **Simulated movies** under named conditions (FRET level, brightness, no acceptor,
  conformational dynamics, γ, movie length). Each condition draws one pool of molecules
  and classifies every molecule once.

**From molecules to movies.** The verdict depends only on how many of a movie's molecules
vote each way, so a movie of N molecules drawn from a population whose molecules vote
consistent with probability ``p_c`` and swapped with ``p_s`` has an exactly computable
verdict distribution: the votes are Binomial(N, p_c + p_s) and, given v votes, the swapped
votes are Binomial(v, p_s / (p_c + p_s)). The script estimates ``p_c`` and ``p_s`` from the
pool (or from the 281-molecule fixture) and reports, per movie size:

* **false alarm** — P(verdict ``swapped``) for the labels as stored, computed with ``p_s``
  at its one-sided 95 % Clopper-Pearson upper bound, so a pool with no wrong votes still
  yields a non-zero, conservative rate;
* **detection** — P(verdict ``swapped``) for the swapped labels, at the estimated rates;
* **insufficient** and **mixed** — for the labels as stored, at the estimated rates.

The check is symmetric in the two channels: a molecule's event under the swapped
labelling is its event under the stored one with ``consistent`` and ``swapped``
exchanged. The script re-classifies swapped traces for a subset of every pool and
asserts that, rather than assuming it.

Run from the repository root with the base environment::

    python scripts/measure_side_check.py
    python scripts/measure_side_check.py --json out.json

The simulations draw from ``numpy.random.default_rng(--seed)`` (default 0), so a rerun
reproduces the tables exactly.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.stats import beta, binom

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tether.imaging.side_check import (  # noqa: E402
    EVENT_CONSISTENT,
    EVENT_NONE,
    EVENT_SWAPPED,
    EVENT_TOGETHER,
    EVENT_UNCLEAR,
    SIDE_CHECK_MAJORITY,
    SIDE_CHECK_MIN_EVENTS,
    classify_bleach_event,
    side_check_verdict,
    votes_needed,
)

_FIXTURES = ROOT / "tests" / "fixtures"
_MIRROR = {EVENT_CONSISTENT: EVENT_SWAPPED, EVENT_SWAPPED: EVENT_CONSISTENT}
_KINDS = (EVENT_CONSISTENT, EVENT_SWAPPED, EVENT_TOGETHER, EVENT_UNCLEAR, EVENT_NONE)
_MOVIE_SIZES = (25, 50, 100, 250)


# --- Molecules ---------------------------------------------------------------------


def _classify_all(donor: np.ndarray, acceptor: np.ndarray, lengths=None) -> np.ndarray:
    kinds = []
    for i in range(donor.shape[0]):
        n = donor.shape[1] if lengths is None else int(lengths[i])
        kinds.append(classify_bleach_event(donor[i, :n], acceptor[i, :n]))
    return np.array(kinds)


def _assert_mirror(donor, acceptor, kinds, lengths=None, limit: int = 200) -> None:
    """Re-classify swapped traces and check the event is the mirrored one."""
    for i in range(min(limit, donor.shape[0])):
        n = donor.shape[1] if lengths is None else int(lengths[i])
        swapped = classify_bleach_event(acceptor[i, :n], donor[i, :n])
        expected = _MIRROR.get(str(kinds[i]), str(kinds[i]))
        if swapped != expected:
            raise AssertionError(f"molecule {i}: swapped kind {swapped!r} != {expected!r}")


def _counts(kinds: np.ndarray) -> dict[str, int]:
    return {k: int((kinds == k).sum()) for k in _KINDS}


# --- Movies (exact) ----------------------------------------------------------------


def _verdict_probs(
    p_c: float, p_s: float, n: int, min_events: int, majority: float
) -> dict[str, float]:
    """Exact verdict distribution for a movie of ``n`` i.i.d. molecules."""
    out = {"consistent": 0.0, "swapped": 0.0, "mixed": 0.0, "insufficient": 0.0}
    p_v = p_c + p_s
    v = np.arange(n + 1)
    p_votes = binom.pmf(v, n, p_v)
    out["insufficient"] = float(p_votes[v < min_events].sum())
    share = p_s / p_v if p_v > 0 else 0.0
    for votes in range(max(min_events, 1), n + 1):
        need = votes_needed(votes, majority)
        p_swapped = float(binom.sf(need - 1, votes, share))  # S >= need
        p_consistent = float(binom.cdf(votes - need, votes, share))  # C = v - S >= need
        out["swapped"] += float(p_votes[votes]) * p_swapped
        out["consistent"] += float(p_votes[votes]) * p_consistent
        out["mixed"] += float(p_votes[votes]) * max(0.0, 1.0 - p_swapped - p_consistent)
    return out


def _upper95(k: int, n: int) -> float:
    """One-sided 95 % Clopper-Pearson upper bound on a proportion k / n."""
    return 1.0 if k >= n else float(beta.ppf(0.95, k + 1, n - k))


def movie_rates(counts: dict[str, int], pool: int, min_events: int, majority: float) -> dict:
    """False alarm, detection, insufficient and mixed rates per movie size."""
    p_c = counts[EVENT_CONSISTENT] / pool
    p_s = counts[EVENT_SWAPPED] / pool
    p_s_hi = _upper95(counts[EVENT_SWAPPED], pool)
    rates = {}
    for n in _MOVIE_SIZES:
        correct = _verdict_probs(p_c, p_s, n, min_events, majority)
        rates[str(n)] = {
            "false_alarm_upper95": _verdict_probs(p_c, p_s_hi, n, min_events, majority)[
                "swapped"
            ],
            "detection": _verdict_probs(p_s, p_c, n, min_events, majority)["swapped"],
            "insufficient": correct["insufficient"],
            "mixed": correct["mixed"],
        }
    return rates


# --- Fixtures ----------------------------------------------------------------------


def _load_smd(path: Path) -> tuple[np.ndarray, np.ndarray] | None:
    import h5py  # noqa: PLC0415

    try:
        with h5py.File(path, "r") as f:
            raw = f["dataset/data/raw"][:]
    except OSError:  # an unmaterialised Git LFS pointer
        return None
    return raw[:, :, 0], raw[:, :, 1]


def _fixture_row(name: str, kinds: np.ndarray, min_events: int, majority: float) -> dict:
    counts = _counts(kinds)
    c, s = counts[EVENT_CONSISTENT], counts[EVENT_SWAPPED]
    row = {
        "fixture": name,
        "n_molecules": int(kinds.size),
        "events": counts,
        "verdict_correct": side_check_verdict(c, s, min_events, majority),
        "verdict_swapped": side_check_verdict(s, c, min_events, majority),
    }
    if kinds.size >= 100:
        row["movies"] = movie_rates(counts, int(kinds.size), min_events, majority)
    return row


def measure_fixtures(min_events: int, majority: float) -> list[dict]:
    """Every fixture with both labellings; movie rates for the populous ones."""
    import h5py  # noqa: PLC0415

    rows: list[dict] = []
    for name in ("smd_2mol.hdf5", "smd_4mol.hdf5", "large/smd_281mol.hdf5"):
        loaded = _load_smd(_FIXTURES / name)
        if loaded is None:
            rows.append({"fixture": name, "skipped": "Git LFS file not materialised"})
            continue
        donor, acceptor = loaded
        kinds = _classify_all(donor, acceptor)
        _assert_mirror(donor, acceptor, kinds)
        rows.append(_fixture_row(name, kinds, min_events, majority))

    try:
        with h5py.File(_FIXTURES / "large" / "kinsoft_sim.hdf5", "r") as f:
            levels = {
                lvl: (f[lvl]["idd"][:], f[lvl]["ida"][:], f[lvl]["length"][:])
                for lvl in ("level1", "level2", "level3")
            }
    except OSError:
        rows.append({"fixture": "large/kinsoft_sim.hdf5", "skipped": "Git LFS not materialised"})
        return rows
    for lvl, (idd, ida, length) in levels.items():
        donor, acceptor = idd.astype(np.float64), ida.astype(np.float64)
        kinds = _classify_all(donor, acceptor, length)
        _assert_mirror(donor, acceptor, kinds, length)
        rows.append(_fixture_row(f"large/kinsoft_sim.hdf5:{lvl}", kinds, min_events, majority))
    return rows


# --- Simulated movies --------------------------------------------------------------


@dataclass(frozen=True)
class Condition:
    """One simulated sample. Intensities are background-subtracted counts per frame."""

    name: str
    fret: tuple[float, ...] = (0.5,)  #: FRET level(s); two values = a two-state molecule
    switch: float = 0.0  #: per-frame probability of a FRET transition (two-state only)
    fret_spread: float = 0.05  #: molecule-to-molecule s.d. of a one-state FRET level
    brightness: float = 600.0  #: donor-excitation photon budget I0 (donor-only donor level)
    gamma: float = 1.0
    leakage: float = 0.07  #: donor emission leaking into the acceptor channel (α)
    donor_only: float = 0.1  #: fraction of molecules with no active acceptor
    donor_life: float = 800.0  #: mean donor bleach frame (exponential)
    acceptor_life: float = 500.0  #: mean acceptor bleach frame (exponential)
    n_frames: int = 1700


#: The per-frame noise model, fitted to the 281-molecule fixture: a background floor of
#: about 28 counts (its post-bleach scatter) plus a shot-like term that brings a channel
#: at about 340 counts to its measured scatter of about 49 counts.
_BG_SIGMA = 28.0
_SHOT = 4.75

CONDITIONS: tuple[Condition, ...] = (
    Condition("typical (E 0.5, 10 % donor-only)"),
    Condition("very low FRET (E 0.05)", fret=(0.05,), fret_spread=0.02),
    Condition("low FRET (E 0.1)", fret=(0.1,), fret_spread=0.03),
    Condition("low FRET (E 0.2)", fret=(0.2,)),
    Condition("E 0.3", fret=(0.3,)),
    Condition("E 0.7", fret=(0.7,)),
    Condition("high FRET (E 0.9)", fret=(0.9,), fret_spread=0.03),
    Condition("dim (I0 200)", brightness=200.0),
    Condition("no acceptor (donor-only sample)", donor_only=1.0),
    Condition("half donor-only", donor_only=0.5),
    Condition("gamma 0.5", gamma=0.5),
    Condition("gamma 2", gamma=2.0),
    Condition("dynamics E 0.15 <-> 0.85", fret=(0.15, 0.85), switch=0.02),
    Condition("binding-like E 0 <-> 0.9", fret=(0.0, 0.9), switch=0.01),
    Condition("short movie (300 frames)", n_frames=300),
    Condition(
        "dynamics, no bleaching in view",
        fret=(0.15, 0.85),
        switch=0.02,
        donor_life=1e9,
        acceptor_life=1e9,
    ),
    Condition("binding-like, short movie", fret=(0.0, 0.9), switch=0.01, n_frames=300),
)


def simulate(cond: Condition, n_molecules: int, rng: np.random.Generator):
    """Draw ``n_molecules`` background-subtracted donor/acceptor traces for ``cond``.

    Donor excitation only (no ALEX, no direct acceptor excitation). Before either dye
    bleaches the donor reads ``I0 (1 - E)`` and the acceptor ``γ I0 E`` plus leakage
    ``α`` of the donor; once the acceptor bleaches first the donor reads ``I0`` and the
    acceptor only the leakage; once the donor bleaches both read background.
    """
    n = cond.n_frames
    donor = np.zeros((n_molecules, n))
    acceptor = np.zeros((n_molecules, n))
    t = np.arange(n)
    for i in range(n_molecules):
        t_d = min(n, int(rng.exponential(cond.donor_life)))
        has_acceptor = rng.random() >= cond.donor_only
        t_a = min(n, int(rng.exponential(cond.acceptor_life))) if has_acceptor else 0
        if len(cond.fret) == 1:
            level = float(np.clip(rng.normal(cond.fret[0], cond.fret_spread), 0.0, 1.0))
            e = np.full(n, level)
        else:
            flips = rng.random(n) < cond.switch
            state = (np.cumsum(flips) + rng.integers(2)) % 2
            e = np.asarray(cond.fret)[state]
        fret_on = t < min(t_a, t_d)
        donor_on = t < t_d
        f_d = np.where(fret_on, cond.brightness * (1.0 - e), cond.brightness) * donor_on
        f_a = np.where(fret_on, cond.gamma * cond.brightness * e, 0.0) + cond.leakage * f_d
        donor[i] = f_d + rng.normal(0.0, np.sqrt(_BG_SIGMA**2 + _SHOT * f_d))
        acceptor[i] = f_a + rng.normal(0.0, np.sqrt(_BG_SIGMA**2 + _SHOT * f_a))
    return donor, acceptor


def measure_simulations(rng, pool: int, min_events: int, majority: float) -> list[dict]:
    rows = []
    for cond in CONDITIONS:
        donor, acceptor = simulate(cond, pool, rng)
        kinds = _classify_all(donor, acceptor)
        _assert_mirror(donor, acceptor, kinds, limit=100)
        counts = _counts(kinds)
        votes = counts[EVENT_CONSISTENT] + counts[EVENT_SWAPPED]
        rows.append(
            {
                "condition": cond.name,
                "pool": pool,
                "events": counts,
                "consistent_share": counts[EVENT_CONSISTENT] / votes if votes else None,
                "votes_per_100_molecules": 100.0 * votes / pool,
                "movies": movie_rates(counts, pool, min_events, majority),
            }
        )
    return rows


# --- Report ------------------------------------------------------------------------


def _pct(x: float) -> str:
    if x < 1e-4:
        return "< 0.01 %"
    return f"{100.0 * x:.2f} %"


def report(result: dict) -> str:
    lines = [
        f"min_events = {result['min_events']}, majority = {result['majority']}, "
        f"seed = {result['seed']}, pool = {result['pool']}",
        "",
        "## Fixtures",
        "",
        "| Fixture | Molecules | Votes consistent / swapped | Together | Stored labels "
        "| Swapped labels |",
        "|---|---|---|---|---|---|",
    ]
    for row in result["fixtures"]:
        if "skipped" in row:
            lines.append(f"| `{row['fixture']}` | skipped: {row['skipped']} | | | | |")
            continue
        ev = row["events"]
        lines.append(
            f"| `{row['fixture']}` | {row['n_molecules']} | {ev['consistent']} / {ev['swapped']} "
            f"| {ev['together']} | {row['verdict_correct']} | {row['verdict_swapped']} |"
        )
    lines += [
        "",
        "## Movies of N molecules",
        "",
        "| Source | Votes per 100 molecules | Consistent share | N | False alarm (95 % upper) "
        "| Detection | Insufficient | Mixed |",
        "|---|---|---|---|---|---|---|---|",
    ]
    sources = [
        (f"fixture `{r['fixture']}`", r) for r in result["fixtures"] if "movies" in r
    ] + [(r["condition"], r) for r in result["simulations"]]
    for label, row in sources:
        counts = row["events"]
        total = row.get("pool", row.get("n_molecules"))
        votes = counts[EVENT_CONSISTENT] + counts[EVENT_SWAPPED]
        share = counts[EVENT_CONSISTENT] / votes if votes else None
        for n, rates in row["movies"].items():
            lines.append(
                f"| {label} | {100.0 * votes / total:.1f} | "
                f"{'-' if share is None else f'{share:.3f}'} | {n} | "
                f"{_pct(rates['false_alarm_upper95'])} | {_pct(rates['detection'])} | "
                f"{_pct(rates['insufficient'])} | {_pct(rates['mixed'])} |"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--pool", type=int, default=2000, help="molecules per condition")
    parser.add_argument("--min-events", type=int, default=SIDE_CHECK_MIN_EVENTS)
    parser.add_argument("--majority", type=float, default=SIDE_CHECK_MAJORITY)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    rng = np.random.default_rng(args.seed)
    result = {
        "seed": args.seed,
        "pool": args.pool,
        "min_events": args.min_events,
        "majority": args.majority,
        "fixtures": measure_fixtures(args.min_events, args.majority),
        "simulations": measure_simulations(rng, args.pool, args.min_events, args.majority),
    }
    print(report(result))
    if args.json is not None:
        args.json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

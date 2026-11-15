# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Draw the speed history of the reference workstation as one image (#723).

Usage::

    python scripts/workstation/plot_perf_history.py <state>/perf-history.jsonl --out history.png

Three panels, one point per record of ``main``: the time of each stage (summed over the
acquisitions), the largest peak memory of each stage, and the projection for a condition of
100 movies with the overnight window of ADR-0032. Run it with the Tether environment, which has
``matplotlib-base``.
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

STAGES = ("extract", "correct", "idealize", "analysis", "save", "export")


def _history_module():
    spec = importlib.util.spec_from_file_location(
        "perf_history", Path(__file__).with_name("perf_history.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def plot(history: Path, out: Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ph = _history_module()
    records = [r for r in ph.read_history(history) if r["on_main"]]
    if not records:
        raise SystemExit(f"{history} holds no record of main")
    runs = range(1, len(records) + 1)
    fig, (times, peaks, proj) = plt.subplots(3, 1, figsize=(9, 10), sharex=True)
    for stage in STAGES:
        t = [sum(a.get(f"{stage}_s", 0) for a in r["acquisitions"].values()) for r in records]
        m = [
            max((a.get(f"{stage}_peak_bytes", 0) for a in r["acquisitions"].values()), default=0)
            / 2**30
            for r in records
        ]
        times.plot(runs, t, marker="o", label=stage)
        peaks.plot(runs, m, marker="o", label=stage)
    hours = [r["projection"]["condition_hours"] if r["projection"] else None for r in records]
    proj.plot(runs, hours, marker="o", label=f"{ph.CONDITION_MOVIES} movies (projected)")
    proj.axhline(ph.OVERNIGHT_WINDOW_HOURS, color="grey", linestyle="--", label="overnight window")
    times.set_ylabel("time (s), all acquisitions")
    peaks.set_ylabel("peak memory (GiB), largest")
    proj.set_ylabel("condition time (h)")
    proj.set_xlabel("record of main (oldest first)")
    for ax in (times, peaks, proj):
        ax.legend(fontsize="small")
        ax.grid(alpha=0.3)
    fig.suptitle(f"Speed history, {records[0]['date'][:10]} to {records[-1]['date'][:10]}")
    fig.tight_layout()
    fig.savefig(out, dpi=120)
    plt.close(fig)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("history")
    parser.add_argument("--out", default="perf-history.png")
    args = parser.parse_args(argv)
    print(plot(Path(args.history), Path(args.out)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

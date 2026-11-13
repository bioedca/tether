# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run one stage of the Tether pipeline on one ``.tether`` and measure it (#723).

``tests/test_workstation_perf.py`` starts this script once for each stage, so each stage has
its own process and its own peak memory. It prints one JSON line: ``time_s`` (the stage call,
imports not included) and ``peak_bytes``.

``peak_bytes`` is the largest memory of this process and of the processes it starts (the
idealization starts the sidecar fit). On Linux and macOS it is the largest peak resident set of
this process (``VmHWM`` on Linux) or of one child. On Windows it is the peak committed memory of
a job object that holds this process and its children; if the job cannot be made, the peak
committed memory of this process only. The two operating systems measure different things, so
compare a value only with values of the same computer (the history keeps a hardware summary).

The stages are the PRD pipeline: ``extract``, ``correct``, ``idealize``, ``analysis``, ``save``
and ``export``. The stage ``selftest`` holds 64 MiB for a short time and needs no Tether, so the
required matrix can test the measurement on each operating system.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

STAGES = ("extract", "correct", "idealize", "analysis", "save", "export", "selftest")
SELFTEST_BYTES = 64 << 20


def _win32():
    """kernel32 and psapi with argument types, so a 64-bit handle is never cut to an int."""
    import ctypes
    from ctypes import wintypes

    handle, dword, ptr = ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = handle
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.CreateJobObjectW.restype = handle
    kernel32.CreateJobObjectW.argtypes = [ptr, ctypes.c_wchar_p]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [handle, handle]
    kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    kernel32.QueryInformationJobObject.argtypes = [handle, ctypes.c_int, ptr, dword, ptr]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    psapi.GetProcessMemoryInfo.argtypes = [handle, ptr, dword]
    return kernel32, psapi


def _windows_job():
    """A job object that holds this process (and so its children), or ``None``."""
    kernel32, _ = _win32()
    job = kernel32.CreateJobObjectW(None, None)
    if not job or not kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()):
        return None
    return job


def _windows_peak(job) -> int:
    import ctypes
    from ctypes import wintypes

    kernel32, psapi = _win32()
    size_t = ctypes.c_size_t
    if job is not None:

        class _Basic(ctypes.Structure):
            _fields_ = [
                ("user", ctypes.c_int64),
                ("job_user", ctypes.c_int64),
                ("flags", wintypes.DWORD),
                ("min_ws", size_t),
                ("max_ws", size_t),
                ("processes", wintypes.DWORD),
                ("affinity", size_t),
                ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD),
            ]

        class _Extended(ctypes.Structure):
            _fields_ = [("basic", _Basic), ("io", ctypes.c_uint64 * 6)] + [
                (name, size_t)
                for name in ("process_limit", "job_limit", "peak_process", "peak_job")
            ]

        info = _Extended()
        ok = kernel32.QueryInformationJobObject(
            job, 9, ctypes.byref(info), ctypes.sizeof(info), None
        )  # 9 = JobObjectExtendedLimitInformation
        if ok:
            return int(info.peak_job)

    class _Counters(ctypes.Structure):
        _fields_ = (
            [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)]
            + [
                (name, size_t)
                for name in ("peak_ws", "ws", "peak_paged", "paged", "peak_np", "np", "pagefile")
            ]
            + [("peak_pagefile", size_t)]
        )

    counters = _Counters()
    counters.cb = ctypes.sizeof(counters)
    psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
    return int(counters.peak_pagefile)


def _posix_peak() -> int:
    import resource

    scale = 1 if sys.platform == "darwin" else 1024  # Linux reports KiB
    own = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale
    status = Path("/proc/self/status")
    if status.is_file():
        # On Linux the rusage peak keeps the RSS this process had before exec, that is the RSS of
        # the parent that forked it (pytest); VmHWM is the peak of this program only.
        for line in status.read_text(encoding="ascii", errors="replace").splitlines():
            if line.startswith("VmHWM:"):
                own = int(line.split()[1]) * 1024
    # A child's peak can include this process's RSS at the fork, which is below `own` anyway.
    return max(own, resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * scale)


def _stage(name: str, args: argparse.Namespace) -> Callable[[], object]:
    """Import what stage *name* needs and return the call to time; the imports stay untimed."""
    store = Path(args.store)
    if name == "selftest":

        def hold() -> None:
            block = bytearray(SELFTEST_BYTES)
            block[::4096] = b"\x01" * len(block[::4096])  # touch each page
            time.sleep(0.1)

        return hold
    if name == "extract":
        from tether.project.extract import extract_movie

        return lambda: extract_movie(
            args.movie, store, tmap=args.tmap, tdat=args.tdat, overwrite=True
        )
    if name == "correct":
        from tether.project.batch import run_correct_stage

        return lambda: run_correct_stage(store)
    if name == "idealize":
        from tether.project.idealize import idealize_molecules

        return lambda: idealize_molecules(store, overwrite=True)
    if name == "analysis":
        from tether.analysis.crosscorr import population_cross_correlation
        from tether.analysis.dwell import population_dwell_times
        from tether.analysis.histogram import population_apparent_e_histogram
        from tether.analysis.tdp import population_transition_density
        from tether.project.idealize import list_idealizations

        def analyse() -> None:
            model = list_idealizations(store)[0]
            population_apparent_e_histogram(store)
            population_cross_correlation(store)
            population_transition_density(store, model)
            population_dwell_times(store, model)

        return analyse
    if name == "save":
        from tether.project.export import export_subset_tether

        return lambda: export_subset_tether(store, args.out, include_raw=True, overwrite=True)
    from tether.project.export import export_molecule_table_csv

    return lambda: export_molecule_table_csv(store, args.out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("stage", choices=STAGES)
    parser.add_argument("--store", default="", help="the .tether the stage reads or writes")
    parser.add_argument("--movie", help="extract: the movie")
    parser.add_argument("--tmap", help="extract: the registration map, when there is one")
    parser.add_argument("--tdat", help="extract: the bead registration, when there is one")
    parser.add_argument("--out", help="save and export: the output file")
    args = parser.parse_args(argv)
    job = _windows_job() if os.name == "nt" else None
    call = _stage(args.stage, args)
    start = time.perf_counter()
    call()
    elapsed = time.perf_counter() - start
    peak = _windows_peak(job) if os.name == "nt" else _posix_peak()
    print(json.dumps({"stage": args.stage, "time_s": elapsed, "peak_bytes": peak}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

---
description: "For maintainers: the record of the manual standalone-tMAVEN window check on each operating system, with one row for each result."
---

# Standalone-tMAVEN cross-OS result record

**Who this page is for.** Tether maintainers with write access to `bioedca/tether`, who run the
manual GUI leg on a physical desktop and record its result here. It assumes that you can run
Tether and tMAVEN from their environments. Users do not need this page. The procedure that produces each row is the
[manual GUI leg](../idealize/standalone-tmaven-handoff.md#manual-gui-leg) on the user-facing
[Standalone-tMAVEN hand-off](../idealize/standalone-tmaven-handoff.md) page.

## Cross-OS result record

The M9 acceptance (ADR-0010) has two parts. The exported SMD opens in standalone tMAVEN, and the
setup script runs clean on **≥2 OSes** (Windows + macOS/Linux). The scripted half is verified on
Windows (developer) and Linux (CI). This page records the manual GUI leg as it is completed.

| OS | tMAVEN commit | Setup script | Scripted open-check | Manual GUI open | Date | Notes |
|---|---|---|---|---|---|---|
| Linux (CI) | `10f4230` | clean | pass | n/a (headless CI) | ongoing | `sidecar` workflow: setup script + `test_handoff_sidecar.py` on every live run. |
| Windows 11 | `10f4230` | clean | pass | pass | 2026-11-09 | Runs 1 and 3 below; run 3 is on the locked environment. Scripted check from 2026-07-19 (developer box, `test_handoff_sidecar.py` 3/3). |
| Ubuntu on WSL2 | `10f4230` | clean | n/a (not run) | pass | 2026-11-09 | Runs 2 and 4 below; run 4 is on the locked environment. |

The guided setup script ran clean on two operating systems: Windows 11 and Ubuntu on WSL2. The
manual GUI leg passed on both, on the locked sidecar environment (runs 3 and 4). That was
after #566 bounded the matplotlib of that environment below 3.11. That completes the step that
ADR-0010 deferred to packaging time (#543). Runs 1 and 2 passed earlier only with matplotlib
swapped by hand, and that is how #566 was found.

## Runs of 2026-11-09 (#543)

Both runs were on the same maintainer desktop, from a fresh clone of `main` at `6de7824`.
Tether reports `1.0.0rc3` there. Each run used new micromamba environments. `tether-543` came
from `conda-lock.yml`, and `tether-sidecar-543` came from
`scripts/setup_sidecar.py --conda-exe <micromamba>`. tMAVEN was `0.3.0` at `10f4230`. These are
the steps, run the same way on each OS:

1. Guided setup: `[3/3] … OK - tmaven maven_class ready`.
2. `tether extract` on `docs/tutorial/data/tutorial_movie.tif` → 3 molecules.
3. Tether's window → **Hand-off → Hand to tMAVEN…** → `handoff.hdf5`. Read with
   `read_smd`, it has 3 molecules × 24 frames, windows `(0, 24)`, and `has_superset` is True.
4. `python -m tmaven` → **File → Load → SMD**: all three traces load and step through.
5. The file is read again after tMAVEN closes. Pass means the same SHA-256 and donor/acceptor
   coordinates as in step 3, which shows tMAVEN's open does not touch the superset (ADR-0002).

| Run | OS | micromamba | Before/after SHA-256 | Result |
|---|---|---|---|---|
| 1 | Windows 11 Home 10.0.26300 (PowerShell 7.6) | 2.8.1 | `2629c2ed66985f60e54b1b32f80fc4ebbb9a6ca8dea91220afa5f3366093dd04`, unchanged | pass |
| 2 | Ubuntu 24.04.5 LTS on WSL2 (kernel 5.15.153.1, WSLg) | 2.10.0 | `e0fede502b9dd32d8fe211601621b3ff5db1725d1500f06549f8198c90bfd3ce`, unchanged | pass |

The coordinates were the same on both OSes: (14, 28)/(15, 28), (27, 17)/(28, 17) and
(12, 12)/(13, 12) (donor/acceptor). The SHA-256 differs between the runs because each export is
its own file.

**Caveats.** These runs had four caveats:

- The locked sidecar ships matplotlib 3.11.0, which crashes the window of tMAVEN at startup
  (#566). So step 4 on both OSes ran with `matplotlib-base` 3.10.9 swapped into the sidecar
  environment. Runs 3 and 4 below repeat it on the locked environment after the fix.
- On Linux, the save box wrote the hand-off without the `.hdf5` extension. So the file was
  renamed before the reading of step 3.
- The time-axis labels of tMAVEN do not reflect the frame time of the movie, because tMAVEN does
  not read it from the file. This is display only.
- On Windows, an add-on changed the plot preferences of tMAVEN at startup. The add-on is part of
  neither Tether nor tMAVEN, and it logs `eps_fix: applied … preferences`. This is display only
  too.

## Locked-environment rerun of 2026-11-09 (#543)

Runs 3 and 4 repeated the steps above on the same desktop after #566 merged, from `main` at
`832dc40`. They used a new sidecar environment, `tether-sidecar-locked`. The guided setup built it
from `sidecar/conda-lock.yml`, with nothing swapped by hand. The environment reported
matplotlib 3.10.9, numpy 1.26.4 and tMAVEN 0.3.0. Each run reused that OS's hand-off file from
the earlier run.

| Run | OS | Before/after SHA-256 | Result |
|---|---|---|---|
| 3 | Windows 11 Home 10.0.26300 (PowerShell 7.6) | `2629c2ed66985f60e54b1b32f80fc4ebbb9a6ca8dea91220afa5f3366093dd04`, unchanged | pass |
| 4 | Ubuntu 24.04.5 LTS on WSL2 (kernel 5.15.153.1, WSLg) | `e0fede502b9dd32d8fe211601621b3ff5db1725d1500f06549f8198c90bfd3ce`, unchanged | pass |

On both OSes, the window of tMAVEN opened, and all three traces loaded and stepped through. The
coordinates read after the open were the same as in runs 1 and 2. On Windows, the tMAVEN process
printed `unknown error ; error code 1073741819` as it ended, after the window had worked. Nobody
diagnosed the cause, and the file read afterward was unchanged.

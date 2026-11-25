# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Start the real update helper on a real installer, as Tether would (issue #331, ADR-0141).

CI only: ``packaging/scripts/upgrade_smoke.sh`` runs this with the INSTALLED app's own
interpreter, on the installer that ``packaging.yml`` just built, and then waits for the
helper's result file. It is not shipped in the installer.

It does what ``tether.gui.update_handoff.hand_off`` does after its checks, with the same
functions: a private ``tether-update-*`` folder, a copy of the installer, the helper script
of this platform, the helper started detached with **this** process as the Tether it waits
for, and a wait for the helper to report that it runs. Then it exits, which is the moment
the helper starts the update. ``hand_off`` itself is not called because it accepts only a
``VerifiedInstaller`` for a stable release, and a CI build is neither attested nor a stable
release; ``tests/test_update_handoff.py`` covers that gate.

    python handoff_smoke.py --installer FILE --prefix DIR --version V --result FILE
        [--install-timeout SECONDS]
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from tether.gui import update_handoff


def main() -> int:
    """Start the helper, wait until it runs, and exit so that it can update."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--install-timeout", type=int, default=update_handoff.INSTALL_TIMEOUT_S)
    args = parser.parse_args()

    platform = update_handoff.handoff_platform()
    assert platform is not None, sys.platform
    workdir = Path(tempfile.mkdtemp(prefix="tether-update-"))
    copy = workdir / args.installer.name
    shutil.copyfile(args.installer, copy)
    digest = hashlib.sha256(copy.read_bytes()).hexdigest()
    helper = workdir / ("update_handoff.ps1" if platform == "windows" else "update_handoff.sh")
    helper.write_bytes(update_handoff._helper_source(platform))
    started = workdir / "helper-started"
    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.unlink(missing_ok=True)
    argv = update_handoff.helper_command(
        helper,
        platform=platform,
        tether_pid=os.getpid(),
        prefix=args.prefix,
        installer=copy,
        sha256=digest,
        version=args.version,
        result=args.result,
        started=started,
        installer_argv=update_handoff.installer_command(copy, args.prefix, platform),
        install_timeout=args.install_timeout,
    )
    process = update_handoff._spawn(argv, workdir)
    deadline = time.monotonic() + update_handoff.HELPER_START_TIMEOUT_S
    while not started.exists():
        if process.poll() is not None or time.monotonic() > deadline:
            print("the update helper did not start", file=sys.stderr)
            return 1
        time.sleep(0.1)
    print(f"helper started: {' '.join(argv)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

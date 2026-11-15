# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Where an installed Tether finds its bundled update verifier (issue #332, ADR-0060).

The installer ships a pinned, hash-checked ``gh`` CLI (``packaging/gh-verifier.toml``) so the
in-app updater can run ``gh attestation verify --bundle`` on a downloaded installer before it
executes it. With no OS code signature behind the installer (ADR-0059) that check is the whole
trust boundary, so the verifier is part of the install rather than a tool the user may or may not
have.

This module only *finds* the binary. Calling it is the verification issue's work, not this one's.

The path is derived the way the sidecar interpreter's is (ADR-0049/0051,
[`tether.idealize.driver.bundled_sidecar_python`][]): from `sys.prefix`, never from
``PATH`` and never from a hard-coded location. The app runs in ``<prefix>/envs/tether``, so the
install prefix is two levels up, and the verifier sits in ``<prefix>/verifier/gh/`` — a folder of
its own. It is deliberately NOT in a folder the install guide asks the user to put on ``PATH``: a
``gh`` there would shadow a user's own ``gh``, and the one Tether ships is for Tether only.

``packaging/construct.yaml``'s ``extra_files`` writes the files to `GH_VERIFIER_DIR`, and a
contract test holds the two to the same path.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: The verifier's folder, relative to the install prefix.
GH_VERIFIER_DIR = "verifier/gh"


def install_prefix(prefix: str | os.PathLike[str] | None = None) -> Path:
    """The installer's root prefix, two levels above the app env (``<prefix>/envs/tether``).

    ``prefix`` is the app env to start from; it defaults to `sys.prefix`. Outside an
    installed copy the result is simply some folder that holds no verifier, which
    `bundled_gh` reports as ``None``.
    """
    env = Path(sys.prefix if prefix is None else prefix)
    return env.parent.parent


def bundled_gh(prefix: str | os.PathLike[str] | None = None) -> Path | None:
    """The bundled ``gh`` executable, or ``None`` when this is not an installed copy.

    ``prefix`` is the app env (default `sys.prefix`). A development checkout or a
    conda environment built from the lock has no ``verifier/gh`` beside it and gets ``None``,
    so a caller can treat "no verifier" as "this copy cannot update itself" rather than as a
    verification failure.
    """
    name = "gh.exe" if os.name == "nt" else "gh"
    candidate = install_prefix(prefix) / GH_VERIFIER_DIR / name
    return candidate if candidate.is_file() else None

# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Where this copy of Tether is installed, for the in-app update (#1379, ADR-0060).

**Install update** on the update notice ([`tether.gui.update_notice`][]) needs two facts
about the running copy: its install folder, which the hand-off replaces, and its bundled
update verifier, which checks the download. Both come from
[`tether._verifier`][], which the GUI does not import directly (ADR-0100). Like
[`tether.services.status`][], this is a read that writes nothing, so it is a plain function
and not a registered service command.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

__all__ = ["UpdateSite", "update_site"]


class UpdateSite(NamedTuple):
    """The running copy's install folder and bundled verifier."""

    install_prefix: Path
    """The installer's root folder, two levels above the application environment."""

    gh: Path | None
    """The bundled ``gh``, or ``None`` when this is not an installed copy."""


def update_site() -> UpdateSite:
    """Return the install folder and the bundled verifier of the running copy.

    Outside an installed copy (a development checkout, an environment built from the lock)
    ``gh`` is ``None``, and the notice offers only the link to the Releases page.
    """
    from tether import _verifier  # noqa: PLC0415

    return UpdateSite(_verifier.install_prefix(), _verifier.bundled_gh())

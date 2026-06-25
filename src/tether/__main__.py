# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Enable ``python -m tether`` as an alias for the ``tether`` console script."""

from __future__ import annotations

from tether.cli import main

if __name__ == "__main__":
    raise SystemExit(main())

# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""``python -m tether.gui`` — the module form of the ``tether-gui`` entry point.

The interpreter-relative way to start the shell, which is what the installer's
per-OS launcher invokes when a console-script shim is not on ``PATH``. Delegates to
:func:`tether.gui.app.main` so both spellings share one startup path.
"""

from __future__ import annotations

from tether.gui.app import main

# Guarded even though this is a __main__.py: `python -m tether.gui` sets
# __name__ == "__main__", while tooling that merely *imports* every module (coverage
# walks, doc-example collectors) must not launch a window as a side effect.
if __name__ == "__main__":  # pragma: no cover - module execution entry
    raise SystemExit(main())

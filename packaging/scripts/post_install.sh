#!/bin/sh
# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
#
# constructor post_install (Unix: Linux .sh + macOS .pkg) — ADR-0049, PRD §9 M9.
#
# Runs AFTER all conda packages and the bundled wheels are linked into the prefix.
# It offline-installs the two non-conda wheels (no network: --no-index --no-deps;
# every runtime dependency already comes from the bundled conda envs) and wires the
# isolated sidecar interpreter for conda-activated launches. POSIX sh only.
#
# Both runtime stacks are constructor `extra_envs` (ADR-0049): the GUI/`tether`
# stack lives in envs/tether, the tMAVEN sidecar in envs/sidecar. `base` is only the
# python+conda bootstrap that constructor requires for extra_envs — nothing app is
# installed there.
set -e

WHEELHOUSE="$PREFIX/wheelhouse"
TETHER_PY="$PREFIX/envs/tether/bin/python"
SIDECAR_PY="$PREFIX/envs/sidecar/bin/python"

# tether wheel -> the GUI env (PySide6/napari/current-numpy).
"$TETHER_PY" -m pip install --no-index --no-deps "$WHEELHOUSE"/tether-*.whl

# tMAVEN wheel -> the ISOLATED sidecar env (PyQt5/numpy<2); never the GUI env.
"$SIDECAR_PY" -m pip install --no-index --no-deps "$WHEELHOUSE"/tmaven-*.whl

# Best-effort: point the app at its bundled sidecar interpreter when the GUI env is
# conda-activated (tether.idealize.driver reads $TETHER_SIDECAR_PYTHON). envs/sidecar
# is a sibling of envs/tether, so resolve it relative to $CONDA_PREFIX at activation
# time (the heredoc is single-quoted, so the expansion is deferred, not baked in). A
# prefix-relative app-side default is the more robust follow-up (ADR-0049).
ACT_D="$PREFIX/envs/tether/etc/conda/activate.d"
mkdir -p "$ACT_D"
cat > "$ACT_D/tether-sidecar.sh" <<'EOF'
export TETHER_SIDECAR_PYTHON="$(dirname "$CONDA_PREFIX")/sidecar/bin/python"
EOF

# Drop the staged wheels; the environments now own the installed packages.
rm -f "$WHEELHOUSE"/tether-*.whl "$WHEELHOUSE"/tmaven-*.whl

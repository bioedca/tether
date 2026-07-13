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
set -e

WHEELHOUSE="$PREFIX/wheelhouse"
BASE_PY="$PREFIX/bin/python"
SIDECAR_PY="$PREFIX/envs/sidecar/bin/python"

# tether wheel -> base env (PySide6/napari/current-numpy).
"$BASE_PY" -m pip install --no-index --no-deps "$WHEELHOUSE"/tether-*.whl

# tMAVEN wheel -> the ISOLATED sidecar env (PyQt5/numpy<2); never the base env.
"$SIDECAR_PY" -m pip install --no-index --no-deps "$WHEELHOUSE"/tmaven-*.whl

# Best-effort: point the app at its bundled sidecar interpreter when the base env
# is conda-activated (tether.idealize.driver reads $TETHER_SIDECAR_PYTHON). A
# prefix-relative app-side default is the more robust follow-up (ADR-0049).
ACT_D="$PREFIX/etc/conda/activate.d"
mkdir -p "$ACT_D"
cat > "$ACT_D/tether-sidecar.sh" <<'EOF'
export TETHER_SIDECAR_PYTHON="$CONDA_PREFIX/envs/sidecar/bin/python"
EOF

# Drop the staged wheels; the environments now own the installed packages.
rm -f "$WHEELHOUSE"/tether-*.whl "$WHEELHOUSE"/tmaven-*.whl

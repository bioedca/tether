#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
#
# The per-platform offline install-smoke for a just-built constructor installer, in ONE
# checked-in script with two callers: .github/workflows/packaging.yml (the advisory
# installer build) and .github/workflows/release.yml (the release pipeline, whose
# `build` job runs it on the exact bytes it is about to upload). Keeping the probe
# sequence here rather than in workflow YAML is what stops the two smokes drifting
# apart — the same drift class #213 closed for the wheel-staging steps and #218 closed
# again for the setuptools bound (issue #217).
#
# A CI-runner script by design: it branches on $RUNNER_OS and installs into
# $RUNNER_TEMP. Running it by hand against a downloaded installer is deliberately out
# of scope (issue #217).
#
# `set -euo pipefail` MUST stay the first executable line, and the contract test
# asserts exactly that: the caller's own `-e` only ever sees this script's FINAL exit
# status, and both platform branches deliberately END in non-fatal commands (the
# Windows Start Menu check, the Unix Linux-only desktop-entry block), so without `-e`
# armed before the first probe the script would exit 0 no matter which probe failed.
set -euo pipefail

# Networking is neutralised for the install + smoke so a bundled-offline regression
# (any attempt to reach a channel/PyPI) fails loudly. Exported HERE, not as step-level
# env: in the callers, so the two workflows cannot drift apart on the offline property
# (issue #217); a contract test asserts both halves.
export PIP_NO_INDEX=1
export CONDA_OFFLINE=1

if [ "$RUNNER_OS" = "Windows" ]; then
  # Stop git-bash from mangling the NSIS /S and /D switches. Exported here for the
  # same single-source reason as the offline pair above: the msys runtime reads it
  # from THIS process when it spawns a native one, so the export reaches the
  # `cmd /c` invocations below.
  export MSYS2_ARG_CONV_EXCL="*"
  prefix="$RUNNER_TEMP\\tether-smoke"
  exe="$(ls packaging/dist/*.exe | head -1)"
  # NSIS silent install; /D (target dir, unquoted) must be the LAST arg.
  # Use `cmd /c` (single slash), NOT `//c`: MSYS2_ARG_CONV_EXCL='*' (set so
  # git-bash leaves /S and /D= intact) also stops git-bash rewriting //c to
  # /c, so `cmd //c` is NOT recognised and the installer would never run.
  cmd /c "$(cygpath -w "$exe") /S /D=$prefix"
  # The NSIS installer can return before the prefix is fully written, so
  # wait (bounded) for the app env interpreter to appear before smoking it.
  tether_py_u="$(cygpath -u "$prefix")/envs/tether/python.exe"
  for _ in $(seq 1 40); do [ -f "$tether_py_u" ] && break; sleep 3; done
  # Both runtime stacks are extra_envs (ADR-0049): the app lives in
  # envs\tether, the sidecar in envs\sidecar; `base` is only python+conda.
  "$prefix\\envs\\tether\\python.exe" -m tether --version
  "$prefix\\envs\\sidecar\\python.exe" -c "import tmaven, PyQt5, numpy; assert numpy.__version__ < '2', numpy.__version__; print('sidecar OK', numpy.__version__)"
  # Importing tmaven is NOT enough — see the Unix branch below for why (issue #212).
  # Same order as there and for the same reason: assert the pin first so a regression
  # names its cause, then drive the real headless entry point, resolved from the
  # INSTALLED app env exactly as the driver resolves it.
  "$prefix\\envs\\sidecar\\python.exe" -c "import setuptools, pkg_resources; assert tuple(int(p) for p in setuptools.__version__.split('.')[:1]) < (81,), setuptools.__version__; print('sidecar setuptools OK', setuptools.__version__)"
  runner="$("$prefix\\envs\\tether\\python.exe" -c 'from tether.idealize.driver import _RUNNER; print(_RUNNER)')"
  "$prefix\\envs\\sidecar\\python.exe" "$runner" --probe
  # The USER-FACING launch surface (ADR-0051). The absolute env path above
  # proves the env was built; these prove the app can actually be STARTED.
  prefix_u="$(cygpath -u "$prefix")"
  test -f "$prefix_u/bin/tether.bat" || { echo "missing shim: bin\\tether.bat"; exit 1; }
  test -f "$prefix_u/bin/tether-gui.bat" || { echo "missing shim: bin\\tether-gui.bat"; exit 1; }
  # [project.gui-scripts] must produce the console-less launcher; without it a
  # shortcut flashes a terminal on every start.
  test -f "$prefix_u/envs/tether/Scripts/tether-gui.exe" || {
    echo "missing tether-gui.exe — is [project.gui-scripts] declared?"; exit 1; }
  cmd /c "$prefix\\bin\\tether.bat" --version
  # Start Menu shortcut (created directly; menuinst cannot own a pip-installed
  # wheel — ADR-0051). Non-fatal: a locked-down runner profile may refuse the
  # shortcut. `${APPDATA-}` (a default, never a bare reference): the variable is
  # legitimately absent off-Windows, and `-u` would otherwise turn this
  # intentionally non-fatal check into a hard failure.
  ls "${APPDATA-}/Microsoft/Windows/Start Menu/Programs/Tether/Tether.lnk" \
    || echo "WARNING: Start Menu shortcut absent on this runner"
else
  prefix="$RUNNER_TEMP/tether-smoke"
  if [ "$RUNNER_OS" = "macOS" ]; then
    pkg="$(ls packaging/dist/*.pkg | head -1)"
    installer -pkg "$pkg" -target CurrentUserHomeDirectory
    prefix="$HOME/Tether"
  else
    sh "$(ls packaging/dist/*.sh | head -1)" -b -p "$prefix"
  fi
  # Both runtime stacks are extra_envs (ADR-0049): the app lives in
  # envs/tether, the sidecar in envs/sidecar; `base` is only python+conda.
  "$prefix/envs/tether/bin/tether" --version
  "$prefix/envs/sidecar/bin/python" -c "import tmaven, PyQt5, numpy; assert numpy.__version__ < '2', numpy.__version__; print('sidecar OK', numpy.__version__)"
  # Importing tmaven is NOT enough. tMAVEN does `import pkg_resources` inside
  # `maven_class.__init__`, not at module import, so a sidecar whose setuptools
  # no longer ships pkg_resources (removed in 82.0.0; the lock resolves 82.0.1)
  # imports cleanly and then dies at the first idealization — exactly how issue
  # #212 reached a shipped installer past this smoke.
  #
  # Assert the pin FIRST: `set -e` is armed above, so whichever check fails first
  # is the one the operator sees, and this one names the cause instead of
  # surfacing as an opaque probe failure. `import pkg_resources` is the
  # load-bearing half; the version bound pins the INTENT (the bundled wheel was
  # actually applied over the lock's setuptools) and is deliberately `< 81`,
  # matching the bundled `setuptools<81`, not the 82.0.0 breakage point.
  "$prefix/envs/sidecar/bin/python" -c "import setuptools, pkg_resources; assert tuple(int(p) for p in setuptools.__version__.split('.')[:1]) < (81,), setuptools.__version__; print('sidecar setuptools OK', setuptools.__version__)"
  # Then drive the real headless entry point the batch runner's startup probe
  # uses: it constructs maven_class and exits non-zero with a JSON status on
  # failure. Resolve the runner from the INSTALLED app env, not the checkout: at
  # runtime the driver launches `Path(driver.__file__).with_name("_sidecar_runner.py")`
  # (`_RUNNER`, src/tether/idealize/driver.py — the path supervisor.py hands the
  # sidecar interpreter), so probing the checkout copy would still pass if the
  # wheel never shipped it.
  runner="$("$prefix/envs/tether/bin/python" -c 'from tether.idealize.driver import _RUNNER; print(_RUNNER)')"
  "$prefix/envs/sidecar/bin/python" "$runner" --probe
  # The USER-FACING launch surface (ADR-0051). The absolute env paths above
  # prove the envs were built; these prove the app can actually be STARTED,
  # which is the thing an install guide has to document. Exercised through
  # the prefix shims, exactly as a user with <prefix>/bin on PATH would.
  test -x "$prefix/bin/tether" || { echo "missing shim: $prefix/bin/tether"; exit 1; }
  test -x "$prefix/bin/tether-gui" || { echo "missing shim: $prefix/bin/tether-gui"; exit 1; }
  PATH="$prefix/bin:$PATH" tether --version
  # The GUI entry point must import and answer --version WITHOUT a display;
  # `gui-scripts` on Unix is a normal console script, so this is safe headless.
  PATH="$prefix/bin:$PATH" tether-gui --version
  if [ "$RUNNER_OS" = "Linux" ]; then
    test -f "$HOME/.local/share/applications/tether.desktop" || {
      echo "missing desktop entry"; exit 1; }
  fi
fi

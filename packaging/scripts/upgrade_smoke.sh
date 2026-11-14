#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
#
# The upgrade smoke for the application settings store (issue #330, ADR-0094). It runs
# after install_smoke.sh in .github/workflows/packaging.yml, on the installer that job
# just built, and does two things on every platform:
#
#   MEASURE what a re-install does to the install folder: a marker file is put in the
#   prefix and in the app env, the installer is run again, and the step summary records
#   the exit status and whether each marker is still there.
#
#   PROVE that the stored update-check answer survives the two upgrade paths a user
#   has: running the new installer over the existing install (over-install), and
#   removing the install the way docs/install.md says and installing again
#   (remove-then-install). The answer is written and read by the INSTALLED app's own
#   tether.gui.app_settings, never the checkout's.
#
# Same posture as install_smoke.sh: a CI-runner script that branches on $RUNNER_OS and
# works in $RUNNER_TEMP, offline for the installs. `set -euo pipefail` is the first
# executable line, and a contract test (tests/test_app_settings.py) asserts it.
set -euo pipefail

export PIP_NO_INDEX=1
export CONDA_OFFLINE=1

report() {
  echo "$1"
  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then echo "$1" >> "$GITHUB_STEP_SUMMARY"; fi
}

file_hash() {
  # The settings file's SHA-256, or "absent".
  if [ -f "$1" ]; then sha256sum "$1" | cut -d' ' -f1; else echo absent; fi
}

present() { if [ -e "$1" ]; then echo present; else echo gone; fi; }

if [ "$RUNNER_OS" = "Windows" ]; then
  export MSYS2_ARG_CONV_EXCL="*"
  prefix="$RUNNER_TEMP\\tether-upgrade"
  prefix_u="$(cygpath -u "$prefix")"
  exe_w="$(cygpath -w "$(ls packaging/dist/*.exe | head -1)")"
  py="$prefix\\envs\\tether\\python.exe"
  py_u="$prefix_u/envs/tether/python.exe"
  run_installer() {
    # Start-Process -Wait returns only when the NSIS installer has exited, and passes
    # its exit status on. /D must be the last argument and is not quoted.
    TETHER_EXE="$exe_w" TETHER_PREFIX="$prefix" powershell -NoProfile -NonInteractive -Command \
      '$p = Start-Process -FilePath $env:TETHER_EXE -ArgumentList "/S /D=$env:TETHER_PREFIX" -Wait -PassThru; exit $p.ExitCode'
  }
  remove_install() {
    # The uninstaller a user runs from Installed apps; `_?=` keeps it in place so this
    # call waits for it. Its status is reported, not gated on (see install_smoke.sh).
    "$prefix\\Uninstall-Tether.exe" /S "_?=$prefix"
  }
else
  if [ "$RUNNER_OS" = "macOS" ]; then
    # The .pkg installs to ~/Tether, whatever install_smoke.sh left there.
    prefix="$HOME/Tether"
    pkg="$(ls packaging/dist/*.pkg | head -1)"
    run_installer() { installer -pkg "$pkg" -target CurrentUserHomeDirectory; }
  else
    prefix="$RUNNER_TEMP/tether-upgrade"
    sh_installer="$(ls packaging/dist/*.sh | head -1)"
    run_installer() { sh "$sh_installer" -b -p "$prefix"; }
  fi
  prefix_u="$prefix"
  py="$prefix/envs/tether/bin/python"
  py_u="$py"
  remove_install() { rm -rf "$prefix"; }
fi

state() { "$py" -c "from tether.gui.app_settings import update_check_state; print(update_check_state().value)" | tr -d '\r'; }

expect_declined() {
  # $1 names the path. The file must be byte for byte the one the app wrote, and the
  # installed app must read the answer back as declined.
  local label="$1" now got
  now="$(file_hash "$settings_u")"
  test "$now" = "$written_hash" || {
    echo "$label: the settings file changed ($written_hash -> $now)"; exit 1; }
  got="$(state)"
  report "- **$label**: settings file unchanged; the installed app reads \`$got\`."
  test "$got" = "declined" || { echo "$label: the app reads $got, not declined"; exit 1; }
}

# --- a clean first install -----------------------------------------------------------
remove_install || true
rc=0; run_installer || rc=$?
test "$rc" = 0 || { echo "the first install exited $rc"; exit 1; }
for _ in $(seq 1 40); do [ -f "$py_u" ] && break; sleep 3; done
"$py" -m tether --version

settings="$("$py" -c 'from tether.gui.app_settings import settings_path; print(settings_path())' | tr -d '\r')"
if [ "$RUNNER_OS" = "Windows" ]; then settings_u="$(cygpath -u "$settings")"; else settings_u="$settings"; fi
report "### Settings store upgrade smoke ($RUNNER_OS)"
report ""
report "Install folder \`$prefix\`; settings file \`$settings\`."
report ""
test "$(file_hash "$settings_u")" = absent || {
  echo "a settings file exists before anything wrote one: $settings"; exit 1; }
first="$(state)"
test "$first" = "pending" || { echo "a fresh install reads $first, not pending"; exit 1; }

"$py" -c "from tether.gui.app_settings import record_update_consent; record_update_consent(False)"
written_hash="$(file_hash "$settings_u")"
test "$written_hash" != absent || { echo "record_update_consent wrote no file at $settings"; exit 1; }

report "- **fresh install, nothing answered**: no settings file; the installed app reads \`$first\`."
expect_declined "answer recorded"

# --- over-install: the new installer over the existing install ----------------------
echo "kept?" > "$prefix_u/upgrade-marker-prefix.txt"
echo "kept?" > "$(dirname "$py_u")/upgrade-marker-env.txt"
over_rc=0; run_installer || over_rc=$?
over_note=""
if [ "$RUNNER_OS" = "Linux" ] && [ "$over_rc" != 0 ]; then
  # The .sh installer refuses an existing folder unless told to update it, so the
  # over-install a user can run on Linux is the -u one. Both statuses are reported.
  over_note=" (plain -b -p exited $over_rc; retried with -u)"
  over_rc=0; sh "$sh_installer" -b -u -p "$prefix" || over_rc=$?
fi
prefix_marker="$(present "$prefix_u/upgrade-marker-prefix.txt")"
env_marker="$(present "$(dirname "$py_u")/upgrade-marker-env.txt")"
report "- Over-install measured: installer exited **$over_rc**$over_note; a file in the install folder: **$prefix_marker**; a file in the app env: **$env_marker**."
if [ "$over_rc" = 0 ] && "$py" -m tether --version; then
  expect_declined "over-install"
else
  # The installer would not run over an existing install, so this is not an upgrade
  # path it offers. The store must still be untouched; the remove-then-install path
  # below is then the only one.
  now="$(file_hash "$settings_u")"
  test "$now" = "$written_hash" || {
    echo "over-install: the settings file changed ($written_hash -> $now)"; exit 1; }
  report "- **over-install**: settings file unchanged; not an upgrade path this installer offers."
fi

# --- remove-then-install --------------------------------------------------------------
remove_rc=0; remove_install || remove_rc=$?
left="$(present "$prefix_u")"
if [ "$left" = present ]; then
  residue="$(find "$prefix_u" -mindepth 1 -maxdepth 1 | sed "s|^$prefix_u/||" | sort | tr '\n' ' ')"
  # What the uninstaller leaves is deleted the way a user deletes a folder.
  rm -rf "$prefix_u"
else
  residue=""
fi
report "- Remove measured: exited **$remove_rc**; install folder afterwards: **$left**${residue:+ (left: \`$residue\`, then deleted)}."
rc=0; run_installer || rc=$?
test "$rc" = 0 || { echo "the install after removal exited $rc"; exit 1; }
for _ in $(seq 1 40); do [ -f "$py_u" ] && break; sleep 3; done
expect_declined "remove-then-install"

# --- clean up ---------------------------------------------------------------------------
remove_install || true
rm -rf "$prefix_u" "$(dirname "$settings_u")"

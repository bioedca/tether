#!/bin/sh
# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
#
# The update helper for macOS and Linux (issue #331, ADR-0141). tether.gui.update_handoff
# copies this file and the verified installer into a private folder, starts it with
# /bin/sh, and quits. It must not depend on anything inside the install folder until the
# new install is in place: that folder is moved aside below.
#
#   update_handoff.sh PID PREFIX INSTALLER SHA256 VERSION RESULT STARTED EXIT_TIMEOUT \
#                     INSTALL_TIMEOUT -- INSTALLER-ARGV...
#
# Steps, each only when the one before it passed:
#   1. say it runs (STARTED), then wait for Tether (PID) to exit;
#   2. hash INSTALLER again; it must still be SHA256, the file that was verified;
#   3. move PREFIX aside to PREFIX.tether-update-backup (a rename);
#   4. run INSTALLER-ARGV, which installs into PREFIX;
#   5. ask the NEW install to check its own launch surface;
#   6. success: delete the backup. Failure: delete the partial install, move the backup back;
#   7. write one line to RESULT and start Tether again.
#
# No `set -e`: every failure is handled where it happens, because stopping half way is the
# one outcome this script exists to prevent.
set -u

if [ "$#" -lt 11 ] || [ "${10}" != "--" ]; then
  echo "usage: update_handoff.sh PID PREFIX INSTALLER SHA256 VERSION RESULT STARTED" \
    "EXIT_TIMEOUT INSTALL_TIMEOUT -- INSTALLER-ARGV..." >&2
  exit 2
fi
tether_pid=$1
prefix=$2
installer=$3
want_sha=$4
version=$5
result=$6
started=$7
exit_timeout=$8
install_timeout=$9
shift 10

backup="$prefix.tether-update-backup"
failed="$prefix.tether-update-failed"
workdir=$(dirname "$installer")
log="${result%.txt}.log"

report() {
  # One line, replaced as a whole: STATUS VERSION DETAIL.
  printf '%s %s %s\n' "$1" "$version" "$2" > "$result.tmp" && mv -f "$result.tmp" "$result"
}

relaunch() {
  if [ -x "$prefix/launchers/tether-gui" ]; then
    (cd / && nohup "$prefix/launchers/tether-gui" > /dev/null 2>&1 &)
  fi
}

finish() {
  # The private folder goes, installer copy included; but only a folder that
  # tether.gui.update_handoff made (its name says so). Anything else keeps all but the copy.
  cd / || true
  case "$(basename "$workdir")" in
    tether-update-*) rm -rf "$workdir" ;;
    *) rm -f "$installer" ;;
  esac
  exit "$1"
}

: > "$started"
: > "$log"

# 1. Tether must be gone before the folder is touched.
waited=0
while kill -0 "$tether_pid" 2> /dev/null; do
  if [ "$waited" -ge "$exit_timeout" ]; then
    report unchanged "Tether did not close"
    finish 1
  fi
  sleep 1
  waited=$((waited + 1))
done

# 2. The file about to run is the file that was verified.
if command -v sha256sum > /dev/null 2>&1; then
  got=$(sha256sum "$installer" | cut -d' ' -f1)
elif command -v shasum > /dev/null 2>&1; then
  got=$(shasum -a 256 "$installer" | cut -d' ' -f1)
else
  got=""
fi
if [ "$got" != "$want_sha" ]; then
  report unchanged "the update file is not the verified file"
  relaunch
  finish 1
fi

# 3. Move the install aside. A rename: instant, and the previous copy stays whole.
if [ -e "$backup" ] || [ -e "$failed" ]; then
  report unchanged "an earlier update left $backup or $failed behind"
  relaunch
  finish 1
fi
if ! mv "$prefix" "$backup"; then
  report unchanged "the install folder could not be moved aside"
  relaunch
  finish 1
fi

# 4. The installer, unattended, into the same folder; stopped after INSTALL_TIMEOUT seconds.
echo "installer: $*" >> "$log"
# The installer gets a process group of its own, so that stopping it stops every process
# it started, not only the first one: `setsid` where there is one (Linux), else `set -m`
# (macOS's /bin/sh is bash, which gives a background job its own group that way; dash
# refuses job control without a terminal, which is why setsid comes first).
if command -v setsid > /dev/null 2>&1; then
  setsid "$@" >> "$log" 2>&1 < /dev/null &
  installer_pid=$!
else
  set -m
  "$@" >> "$log" 2>&1 < /dev/null &
  installer_pid=$!
  set +m
fi

stop_installer() {
  # TERM to the whole group, then up to 10 s for it to go, then KILL.
  kill -s TERM -- "-$installer_pid" 2> /dev/null || kill -s TERM "$installer_pid" 2> /dev/null
  n=0
  while kill -s 0 -- "-$installer_pid" 2> /dev/null && [ "$n" -lt 10 ]; do
    sleep 1
    n=$((n + 1))
  done
  kill -s KILL -- "-$installer_pid" 2> /dev/null
}
(
  # Polls once a second, so it ends with the installer instead of outliving it.
  elapsed=0
  while kill -0 "$installer_pid" 2> /dev/null; do
    if [ "$elapsed" -ge "$install_timeout" ]; then
      stop_installer
      break
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done
) > /dev/null 2>&1 &
watchdog_pid=$!
wait "$installer_pid"
rc=$?
kill "$watchdog_pid" 2> /dev/null
echo "installer exit: $rc" >> "$log"
# Nothing the installer started may still be writing when the folder is checked or put back.
if kill -s 0 -- "-$installer_pid" 2> /dev/null; then
  stop_installer
fi

# 5 and 6. The new install checks itself; only then is the backup deleted.
if [ "$rc" -eq 0 ] && "$prefix/envs/tether/bin/python" -m tether.gui.update_handoff check \
  --prefix "$prefix" --version "$version" >> "$log" 2>&1; then
  rm -rf "$backup"
  report updated ""
  relaunch
  finish 0
fi
if [ "$rc" -eq 0 ]; then
  why="the new install failed its launch check"
else
  why="the installer exited $rc"
fi

# Restore. The partial install is deleted, or moved out of the way if it cannot be.
if [ -e "$prefix" ]; then
  rm -rf "$prefix" 2> /dev/null || mv "$prefix" "$failed" 2> /dev/null
fi
if [ -e "$prefix" ] || ! mv "$backup" "$prefix"; then
  report failed "$why; the previous copy is in $backup"
  finish 1
fi
report restored "$why"
relaunch
finish 1

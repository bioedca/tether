@echo off
rem SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
rem SPDX-License-Identifier: GPL-3.0-or-later
rem
rem constructor post_install (Windows .exe / NSIS) — ADR-0049, PRD Section 9 M9.
rem
rem Runs AFTER all conda packages and the bundled wheels are linked into the
rem prefix. Offline-installs the three non-conda wheels (no network: --no-index
rem --no-deps; deps come from the bundled conda envs) and wires the isolated
rem sidecar interpreter for conda-activated launches. Both runtime stacks are
rem constructor `extra_envs` (ADR-0049): the GUI/tether stack in envs\tether and
rem the tMAVEN sidecar in envs\sidecar; `base` is only the python+conda bootstrap.
setlocal

set "WHEELHOUSE=%PREFIX%\wheelhouse"
set "TETHER_PY=%PREFIX%\envs\tether\python.exe"
set "SIDECAR_PY=%PREFIX%\envs\sidecar\python.exe"

rem tether wheel -> the GUI env (PySide6/napari/current-numpy). A `for` over an
rem unmatched glob runs zero iterations and leaves errorlevel untouched, so guard
rem the missing-wheel case explicitly (unlike post_install.sh, where the literal
rem unmatched glob reaches pip and fails under `set -e`).
if not exist "%WHEELHOUSE%\tether-*.whl" (
  echo ERROR: tether wheel not found in %WHEELHOUSE%
  exit /b 1
)
for %%W in ("%WHEELHOUSE%\tether-*.whl") do (
  "%TETHER_PY%" -m pip install --no-index --no-deps "%%W"
  if errorlevel 1 exit /b 1
)

rem setuptools<81 -> the sidecar env, BEFORE tMAVEN. tMAVEN's maven_class.__init__
rem does `import pkg_resources`, which setuptools DEPRECATED by 80.9.0 (still shipped
rem through 81.0.0) and REMOVED in 82.0.0, and the sidecar lock resolves setuptools
rem 82.0.1 — so without this the env builds cleanly and then dies at the first
rem idealization (issue #212). `<81` rather than `<82` is the bound that setuptools'
rem own deprecation warning names ("pin to Setuptools<81").
rem scripts/setup_sidecar.py applies the same pin on the source path, reading the version
rem and its sha256 from packaging/setuptools-compatibility.txt; this is the installer's
rem equivalent, using the wheel that file's hash-checked download staged.
rem
rem pip is given the wheel by PATH, not by requirement spec, so it downgrades the conda
rem setuptools rather than reporting "already satisfied".
if not exist "%WHEELHOUSE%\setuptools-*.whl" (
  echo ERROR: setuptools wheel not found in %WHEELHOUSE%
  exit /b 1
)
for %%W in ("%WHEELHOUSE%\setuptools-*.whl") do (
  "%SIDECAR_PY%" -m pip install --no-index --no-deps "%%W"
  if errorlevel 1 exit /b 1
)

rem tMAVEN wheel -> the ISOLATED sidecar env (PyQt5/numpy<2); never the GUI env.
if not exist "%WHEELHOUSE%\tmaven-*.whl" (
  echo ERROR: tmaven wheel not found in %WHEELHOUSE%
  exit /b 1
)
for %%W in ("%WHEELHOUSE%\tmaven-*.whl") do (
  "%SIDECAR_PY%" -m pip install --no-index --no-deps "%%W"
  if errorlevel 1 exit /b 1
)

rem Best-effort: point the app at its bundled sidecar interpreter when the GUI env
rem is conda-activated. envs\sidecar is a sibling of envs\tether, so resolve it
rem relative to %%CONDA_PREFIX%% at activation time (a prefix-relative app-side
rem default is the more robust follow-up, ADR-0049).
set "ACT_D=%PREFIX%\envs\tether\etc\conda\activate.d"
if not exist "%ACT_D%" mkdir "%ACT_D%"
> "%ACT_D%\tether-sidecar.bat" echo set "TETHER_SIDECAR_PYTHON=%%CONDA_PREFIX%%\..\sidecar\python.exe"

rem --- Launchers (ADR-0051) -------------------------------------------------------
rem envs\tether\Scripts is NEVER on PATH: initialize_conda and register_python are
rem both false, so the installer deliberately does not touch the user's shell or
rem registry. Without the shims below there is no way to start Tether except by
rem typing an env-relative absolute path, and `tether --version` — the documented
rem install-verification step — fails.
rem
rem One stable, documented directory (%PREFIX%\launchers) holds both launchers so
rem the install guide can name a SINGLE path to put on PATH. The installer does NOT
rem edit PATH itself: `setx` silently TRUNCATES PATH beyond 1024 characters, which is
rem not recoverable by documentation, whereas adding a directory is a one-liner.
rem
rem The directory holds the two launchers and nothing else (issue #492, ADR-0096).
rem Until rc3 they went into %PREFIX%\bin. On Linux and macOS that folder is the bin
rem of the python + conda bootstrap env, so the folder name moved on all three
rem systems at once and the install guide names one folder for all of them.
rem
rem Each launcher puts the env's own folders at the FRONT of PATH, for its own
rem process only (`setlocal`), in the order `conda activate` uses on Windows (issue
rem #553). Without them MKL, which the env's BLAS resolves to, loads its own
rem dependent DLLs through the user's ordinary search order, and on a PC with other
rem scientific software that found an incompatible copy: `tether extract` died with
rem 0xC06D007F at its first BLAS call. The doubled percent signs below write a literal
rem PATH reference into each launcher, so the user's PATH is read at launch time and
rem their persistent PATH is never edited (ADR-0051).
set "LAUNCH_D=%PREFIX%\launchers"
set "ENV_D=%PREFIX%\envs\tether"
set "ENV_PATH=%ENV_D%;%ENV_D%\Library\mingw-w64\bin;%ENV_D%\Library\usr\bin;%ENV_D%\Library\bin;%ENV_D%\Scripts;%ENV_D%\bin"
if not exist "%LAUNCH_D%" mkdir "%LAUNCH_D%"
> "%LAUNCH_D%\tether.bat" echo @echo off
>>"%LAUNCH_D%\tether.bat" echo rem Generated by the Tether installer (ADR-0051, issue #553).
>>"%LAUNCH_D%\tether.bat" echo setlocal
>>"%LAUNCH_D%\tether.bat" echo set "PATH=%ENV_PATH%;%%PATH%%"
>>"%LAUNCH_D%\tether.bat" echo "%ENV_D%\Scripts\tether.exe" %%*
> "%LAUNCH_D%\tether-gui.bat" echo @echo off
>>"%LAUNCH_D%\tether-gui.bat" echo rem Generated by the Tether installer (ADR-0051, issue #553).
>>"%LAUNCH_D%\tether-gui.bat" echo setlocal
>>"%LAUNCH_D%\tether-gui.bat" echo set "PATH=%ENV_PATH%;%%PATH%%"
>>"%LAUNCH_D%\tether-gui.bat" echo start "" "%ENV_D%\Scripts\tether-gui.exe" %%*

rem Start Menu shortcut for the GUI. menuinst is not usable here: it attaches
rem shortcuts to a CONDA package, and `tether` is an offline-pip-installed wheel, so
rem menu_packages stays [] and the shortcut is created directly. It targets the
rem console-less tether-gui.exe itself (issue #579, ADR-0076), so no console window
rem appears. A shortcut cannot set environment variables, so the GUI puts the env's
rem folders first on its own PATH at startup, before NumPy and MKL load
rem (tether.gui.env_path; the step the launchers above take for issue #553).
rem rc3 ran tether-gui.bat through cmd.exe instead, and its console flashed. Start
rem lists a shortcut whose target is an .exe; it did not list one aimed at the .bat
rem (issue #578). WindowStyle 1 is a normal window: the launcher hands its startup
rem info to pythonw, and Windows applies that style to the FIRST window the GUI
rem shows, so 7 (minimized) would start the splash minimized.
rem Arguments is CLEARED, not left out: CreateShortcut opens an existing .lnk, and one
rem left by an earlier install still carries `/c ""<bat>""`. tether-gui.exe would get
rem those arguments, its parser would reject them, and the GUI would not open.
rem
rem The shortcut shows the Tether icon, tether.ico from the installed wheel (issue
rem #673; tether.gui.identity.windows_icon_path). tether-gui.exe is a generic pip
rem launcher without the Tether icon. The file is inside the prefix, so the uninstaller
rem removes it with the rest of the installation.
rem
rem Paths are handed to PowerShell through the ENVIRONMENT, never interpolated into
rem its source: a single apostrophe anywhere in %APPDATA% or %PREFIX% (e.g. a user
rem named O'Brien) would close the single-quoted literal early and the shortcut would
rem silently fail to a warning. $env:VAR carries the value verbatim, quotes and all.
rem
rem PowerShell is named by its ABSOLUTE path, never as a bare `powershell` (issue #551).
rem The constructor NSIS template replaces PATH for this script with the prefix's own
rem folders plus %SystemRoot%, System32 and System32\Wbem, which leaves out
rem System32\WindowsPowerShell\v1.0. A bare name therefore resolves to nothing on every
rem machine, and that is how rc2 shipped without a shortcut. Its error output is kept,
rem not sent to nul, so the reason lands in the installer log if this fails again.
set "SM_D=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Tether"
if not exist "%SM_D%" mkdir "%SM_D%"
set "TETHER_LNK=%SM_D%\Tether.lnk"
set "TETHER_GUI_EXE=%ENV_D%\Scripts\tether-gui.exe"
set "TETHER_ICO=%PREFIX%\envs\tether\Lib\site-packages\tether\gui\assets\brand\app-icon\tether.ico"
set "TETHER_GUI_WD=%PREFIX%\envs\tether"
set "PS_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
"%PS_EXE%" -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:TETHER_LNK);" ^
  "$s.TargetPath=$env:TETHER_GUI_EXE;" ^
  "$s.Arguments='';" ^
  "$s.IconLocation=$env:TETHER_ICO + ',0';" ^
  "$s.WindowStyle=1;" ^
  "$s.WorkingDirectory=$env:TETHER_GUI_WD;" ^
  "$s.Description='Tether - single-molecule FRET analysis';" ^
  "$s.Save()"
if errorlevel 1 echo WARNING: could not create the Start Menu shortcut; launch via %TETHER_GUI_EXE%

rem The shortcut carries the same AppUserModelID as the running window (issue #673;
rem tether.gui.identity.WINDOWS_APP_ID). Without it, a shortcut pinned to the taskbar
rem and the window it opens show as two buttons, as the first build of #673 did on a
rem real desktop. WScript.Shell cannot write that property, so the env's own Python
rem writes it through the shortcut's property store. The path reaches Python as an
rem argument, never inside its source, for the same reason the paths above reach
rem PowerShell through the environment.
"%TETHER_PY%" -c "import sys; from tether.gui.identity import set_shortcut_app_id; sys.exit(0 if set_shortcut_app_id(sys.argv[1]) else 1)" "%TETHER_LNK%"
if errorlevel 1 echo WARNING: could not give the Start Menu shortcut the Tether AppUserModelID; a pinned shortcut may show as a second taskbar button

rem Drop the staged wheels; the environments now own the installed packages.
del "%WHEELHOUSE%\tether-*.whl" "%WHEELHOUSE%\tmaven-*.whl" "%WHEELHOUSE%\setuptools-*.whl" 2>nul
exit /b 0

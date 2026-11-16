@echo off
rem SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
rem SPDX-License-Identifier: GPL-3.0-or-later
rem
rem constructor pre_uninstall (Windows .exe / NSIS) - ADR-0051, issue #551, ADR-0118.
rem
rem post_install.bat creates the Start Menu shortcut directly, because menuinst cannot
rem own a shortcut for the pip-installed tether wheel. The uninstaller only removes the
rem install prefix, so without this script the shortcut (or, when its creation failed,
rem the empty folder) outlives the app it points at. The same holds for the .tether file
rem type in the registry (issue #664).
rem
rem Only what post_install creates is removed. `rmdir` without /s leaves the folder in
rem place if anything else is in it. No step may fail the uninstall, so this script
rem always exits 0.
setlocal

rem The .tether file type (issue #664, ADR-0118), removed by the same module that
rem post_install.bat registered it with, while the environment is still in place. It
rem removes the keys only when their open command starts THIS install, and an extension
rem key only when nothing else is left in it, so another program's registration and another
rem Tether install's stay. Its exit status is ignored, as for every step here.
set "TETHER_PY=%PREFIX%\envs\tether\python.exe"
if exist "%TETHER_PY%" "%TETHER_PY%" -m tether.gui.file_type remove --prefix "%PREFIX%"

set "SM_D=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Tether"
if exist "%SM_D%\Tether.lnk" del /f /q "%SM_D%\Tether.lnk"
if exist "%SM_D%" rmdir "%SM_D%" 2>nul

exit /b 0

@echo off
rem SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
rem SPDX-License-Identifier: GPL-3.0-or-later
rem
rem constructor pre_uninstall (Windows .exe / NSIS) - ADR-0051, issue #551.
rem
rem post_install.bat creates the Start Menu shortcut directly, because menuinst cannot
rem own a shortcut for the pip-installed tether wheel. The uninstaller only removes the
rem install prefix, so without this script the shortcut (or, when its creation failed,
rem the empty folder) outlives the app it points at.
rem
rem Only what post_install creates is removed. `rmdir` without /s leaves the folder in
rem place if anything else is in it. Neither step may fail the uninstall, so this
rem script always exits 0.
setlocal

set "SM_D=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Tether"
if exist "%SM_D%\Tether.lnk" del /f /q "%SM_D%\Tether.lnk"
if exist "%SM_D%" rmdir "%SM_D%" 2>nul

exit /b 0

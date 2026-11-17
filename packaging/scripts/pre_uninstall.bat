@echo off
rem SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
rem SPDX-License-Identifier: GPL-3.0-or-later
rem
rem constructor pre_uninstall (Windows .exe / NSIS) - ADR-0051, issue #551.
rem
rem post_install.bat creates the two Start Menu shortcuts directly, because menuinst
rem cannot own a shortcut for the pip-installed tether wheel: Tether and, since issue
rem #666, Tether Command Prompt. The uninstaller only removes the install prefix, so
rem without this script the shortcuts (or, when their creation failed, the empty folder)
rem outlive the app they point at.
rem
rem Only what post_install creates is removed. `rmdir` without /s leaves the folder in
rem place if anything else is in it. No step may fail the uninstall, so this
rem script always exits 0.
setlocal

set "SM_D=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Tether"
if exist "%SM_D%\Tether.lnk" del /f /q "%SM_D%\Tether.lnk"
if exist "%SM_D%\Tether Command Prompt.lnk" del /f /q "%SM_D%\Tether Command Prompt.lnk"
if exist "%SM_D%" rmdir "%SM_D%" 2>nul

exit /b 0

@echo off
rem SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
rem SPDX-License-Identifier: GPL-3.0-or-later
rem
rem constructor post_install (Windows .exe / NSIS) — ADR-0049, PRD Section 9 M9.
rem
rem Runs AFTER all conda packages and the bundled wheels are linked into the
rem prefix. Offline-installs the two non-conda wheels (no network: --no-index
rem --no-deps; deps come from the bundled conda envs) and wires the isolated
rem sidecar interpreter for conda-activated launches. The base-env python is at
rem the prefix root (%PREFIX%\python.exe), NOT under Scripts\.
setlocal

set "WHEELHOUSE=%PREFIX%\wheelhouse"
set "BASE_PY=%PREFIX%\python.exe"
set "SIDECAR_PY=%PREFIX%\envs\sidecar\python.exe"

rem tether wheel -> base env (PySide6/napari/current-numpy).
for %%W in ("%WHEELHOUSE%\tether-*.whl") do "%BASE_PY%" -m pip install --no-index --no-deps "%%W"
if errorlevel 1 exit /b 1

rem tMAVEN wheel -> the ISOLATED sidecar env (PyQt5/numpy<2); never the base env.
for %%W in ("%WHEELHOUSE%\tmaven-*.whl") do "%SIDECAR_PY%" -m pip install --no-index --no-deps "%%W"
if errorlevel 1 exit /b 1

rem Best-effort: point the app at its bundled sidecar interpreter when the base
rem env is conda-activated (a prefix-relative app-side default is the more robust
rem follow-up, ADR-0049).
set "ACT_D=%PREFIX%\etc\conda\activate.d"
if not exist "%ACT_D%" mkdir "%ACT_D%"
> "%ACT_D%\tether-sidecar.bat" echo set "TETHER_SIDECAR_PYTHON=%%CONDA_PREFIX%%\envs\sidecar\python.exe"

rem Drop the staged wheels; the environments now own the installed packages.
del "%WHEELHOUSE%\tether-*.whl" "%WHEELHOUSE%\tmaven-*.whl" 2>nul
exit /b 0

# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
#
# The update helper for Windows (issue #331, ADR-0141). tether.gui.update_handoff copies
# this file and the verified installer into a private folder, starts it with Windows
# PowerShell, and quits. It must not depend on anything inside the install folder until
# the new install is in place: that folder is moved aside below.
#
# The steps are those of update_handoff.sh, each only when the one before it passed:
#   1. say it runs (-Started), then wait for Tether (-TetherPid) to exit. This is the
#      ordering Windows forces: a running Tether keeps python.exe and its DLLs in the
#      install folder open, and an open file can be neither renamed nor replaced;
#   2. hash -Installer again; it must still be -Sha256, the file that was verified;
#   3. move -Prefix aside to <Prefix>.tether-update-backup (a rename);
#   4. run the installer with -InstallerArguments (NSIS: /D= last and unquoted);
#   5. ask the NEW install to check its own launch surface;
#   6. success: delete the backup. Failure: delete the partial install, move the backup back;
#   7. write one line to -Result and start Tether again.
param(
    [Parameter(Mandatory = $true)][int]$TetherPid,
    [Parameter(Mandatory = $true)][string]$Prefix,
    [Parameter(Mandatory = $true)][string]$Installer,
    [Parameter(Mandatory = $true)][string]$InstallerArguments,
    [Parameter(Mandatory = $true)][string]$Sha256,
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$Result,
    [Parameter(Mandatory = $true)][string]$Started,
    [Parameter(Mandatory = $true)][int]$ExitTimeout,
    [Parameter(Mandatory = $true)][int]$InstallTimeout
)

# Errors are handled where they happen; stopping half way is the one outcome this script
# exists to prevent.
$ErrorActionPreference = 'Continue'

$Backup = "$Prefix.tether-update-backup"
$Failed = "$Prefix.tether-update-failed"
$Work = Split-Path -Parent $Installer
$Log = [IO.Path]::ChangeExtension($Result, '.log')
$Utf8 = New-Object Text.UTF8Encoding $false

function Write-Report([string]$Status, [string]$Detail) {
    # One line, replaced as a whole: STATUS VERSION DETAIL.
    $tmp = "$Result.tmp"
    [IO.File]::WriteAllText($tmp, "$Status $Version $Detail`n", $Utf8)
    Move-Item -LiteralPath $tmp -Destination $Result -Force
}

function Write-Log([string]$Line) {
    [IO.File]::AppendAllText($Log, "$Line`r`n", $Utf8)
}

function Start-Tether {
    $gui = Join-Path $Prefix 'envs\tether\Scripts\tether-gui.exe'
    if (Test-Path -LiteralPath $gui -PathType Leaf) {
        Start-Process -FilePath $gui -WorkingDirectory (Join-Path $Prefix 'envs\tether') | Out-Null
    }
}

function Stop-Helper([int]$Code) {
    # The private folder goes, installer copy included; but only a folder that
    # tether.gui.update_handoff made (its name says so). Anything else keeps all but the copy.
    Set-Location -LiteralPath ([IO.Path]::GetTempPath())
    if ((Split-Path -Leaf $Work) -like 'tether-update-*') {
        Remove-Item -LiteralPath $Work -Recurse -Force -ErrorAction SilentlyContinue
    } else {
        Remove-Item -LiteralPath $Installer -Force -ErrorAction SilentlyContinue
    }
    exit $Code
}

[IO.File]::WriteAllText($Started, '', $Utf8)
[IO.File]::WriteAllText($Log, '', $Utf8)

# 1. Tether must be gone before the folder is touched.
$tether = Get-Process -Id $TetherPid -ErrorAction SilentlyContinue
if ($tether -and -not $tether.WaitForExit($ExitTimeout * 1000)) {
    Write-Report 'unchanged' 'Tether did not close'
    Stop-Helper 1
}

# 2. The file about to run is the file that was verified.
$got = ''
try { $got = (Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash.ToLowerInvariant() } catch { }
if ($got -ne $Sha256) {
    Write-Report 'unchanged' 'the update file is not the verified file'
    Start-Tether
    Stop-Helper 1
}

# 3. Move the install aside. A rename: instant, and the previous copy stays whole. Windows
# refuses it while any program still has a file in the folder open, and then nothing has
# changed yet.
if ((Test-Path -LiteralPath $Backup) -or (Test-Path -LiteralPath $Failed)) {
    Write-Report 'unchanged' "an earlier update left $Backup or $Failed behind"
    Start-Tether
    Stop-Helper 1
}
try {
    Rename-Item -LiteralPath $Prefix -NewName (Split-Path -Leaf $Backup) -ErrorAction Stop
} catch {
    Write-Report 'unchanged' 'the install folder could not be moved aside; is another Tether program still running?'
    Start-Tether
    Stop-Helper 1
}

# 4. The installer, unattended, into the same folder; stopped after -InstallTimeout seconds.
Write-Log "installer: $Installer $InstallerArguments"
$rc = -1
try {
    $process = Start-Process -FilePath $Installer -ArgumentList $InstallerArguments -PassThru -ErrorAction Stop
    $null = $process.Handle  # keeps the exit code readable after the process ends
    if ($process.WaitForExit($InstallTimeout * 1000)) {
        $rc = $process.ExitCode
    } else {
        Write-Log 'installer timed out; stopping it'
        & "$env:SystemRoot\System32\taskkill.exe" /PID $process.Id /T /F | Out-Null
        $process.WaitForExit()
    }
} catch {
    Write-Log "installer could not start: $_"
}
Write-Log "installer exit: $rc"

# 5 and 6. The new install checks itself; only then is the backup deleted.
$checked = $false
$python = Join-Path $Prefix 'envs\tether\python.exe'
if ($rc -eq 0 -and (Test-Path -LiteralPath $python -PathType Leaf)) {
    $out = "$Log.check.out"
    $err = "$Log.check.err"
    $check = Start-Process -FilePath $python -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput $out -RedirectStandardError $err `
        -ArgumentList "-m tether.gui.update_handoff check --prefix `"$Prefix`" --version $Version"
    foreach ($part in @($out, $err)) {
        if (Test-Path -LiteralPath $part) {
            Write-Log ([IO.File]::ReadAllText($part))
            Remove-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue
        }
    }
    $checked = ($check.ExitCode -eq 0)
}
if ($checked) {
    Remove-Item -LiteralPath $Backup -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $Backup) {
        Write-Report 'updated' "the previous copy could not be deleted: $Backup"
    } else {
        Write-Report 'updated' ''
    }
    Start-Tether
    Stop-Helper 0
}
if ($rc -eq 0) { $why = 'the new install failed its launch check' } else { $why = "the installer exited $rc" }

# Restore. The partial install is deleted, or moved out of the way if it cannot be.
if (Test-Path -LiteralPath $Prefix) {
    Remove-Item -LiteralPath $Prefix -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $Prefix) {
        Rename-Item -LiteralPath $Prefix -NewName (Split-Path -Leaf $Failed) -ErrorAction SilentlyContinue
    }
}
$restored = $false
if (-not (Test-Path -LiteralPath $Prefix)) {
    try {
        Rename-Item -LiteralPath $Backup -NewName (Split-Path -Leaf $Prefix) -ErrorAction Stop
        $restored = $true
    } catch { }
}
if (-not $restored) {
    Write-Report 'failed' "$why; the previous copy is in $Backup"
    Stop-Helper 1
}
Write-Report 'restored' $why
Start-Tether
Stop-Helper 1

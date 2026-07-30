# SPDX-FileCopyrightText: 2026 The Tether Authors
# SPDX-License-Identifier: GPL-3.0-or-later
<#
.SYNOPSIS
    Admission gate for concurrent Tether worker sessions: at most N holders, and never below a
    free-RAM floor.

.DESCRIPTION
    NOT a named Semaphore, and that is the whole design.

    A `Semaphore(2)` is the obvious implementation and it is unsafe here: a holder killed by the very
    out-of-memory condition this gate defends against leaks its count permanently, and every later
    pytest run then deadlocks with nobody awake to release it. That is precisely the
    fail-closed-with-only-human-escapes trap ADR-0057 was written to remove - the 2026-07-26 run froze
    at 08:03Z because the only recovery required a human who was asleep.

    So admission is held in PID-liveness slot files instead. A dead holder's slot is reclaimed by
    observing that its process is gone, which means the gate self-heals rather than needing a person.

    It also refuses below a free-RAM floor. Root cause #5 of that run was RAM, not slot count: this
    machine has ~15.7 GB total and idles between 1.5 and 2.6 GB available. Two slots each starting a Qt
    test suite in that state is the failure the gate exists to prevent, and a slot count alone cannot
    see it. See -MinFreeMb for what the default rests on and what would replace it.

    Slot files live beside this script under .claude/, which is gitignored, so no state is committed.

.PARAMETER Acquire
    Take a slot. Prints the slot path on stdout when it succeeds; refusals go to stderr. Exit 0 taken,
    4 no slot free, 5 below the RAM floor. Those codes are the interface - a caller must be able to
    tell "wait and retry" from "this machine is too small right now" without parsing prose.

.PARAMETER Release
    Give back the slot this process holds. Always exit 0: releasing something already reclaimed is
    not an error, and making it one would put a failure on the recovery path.

.PARAMETER Status
    Print every slot and whether its holder is alive. Read-only, exit 0.

.PARAMETER Holders
    Maximum concurrent holders. Default 2.

.PARAMETER MinFreeMb
    Refuse admission below this much free physical memory. Default 1024.

    THE DEFAULT IS PROVISIONAL, and here is exactly what it rests on so nobody mistakes it for a
    measured threshold. Measured on this machine (2026-07-30): 16,073 MB total, and available memory
    idling between 1,500 and 2,600 MB across several samples. `Win32_OperatingSystem.FreePhysicalMemory`
    and the `\Memory\Available MBytes` performance counter agreed within 10 MB, so the cheaper CIM call
    is used.

    A floor of 2048 was the first guess and it is wrong for this machine: it would refuse *every*
    admission at idle, which makes the gate useless rather than safe - and a guard that always refuses
    gets switched off. 1024 leaves room for one suite while still refusing when the machine is genuinely
    exhausted.

    What would replace it with a real number: the peak resident set of a Tether GUI/napari pytest run,
    which is the consumer that caused root cause #5 and is NOT measured here. Until that exists, treat
    this as a calibration parameter and pass -MinFreeMb explicitly if you know better.

.EXAMPLE
    $slot = & .agents/bin/gate.ps1 -Acquire
    try { pytest -m "not large and not sidecar and not deep" } finally { & .agents/bin/gate.ps1 -Release }
#>
[CmdletBinding(DefaultParameterSetName = 'Status')]
param(
    [Parameter(ParameterSetName = 'Acquire', Mandatory = $true)][switch]$Acquire,
    [Parameter(ParameterSetName = 'Release', Mandatory = $true)][switch]$Release,
    [Parameter(ParameterSetName = 'Status')][switch]$Status,
    [int]$Holders = 2,
    [int]$MinFreeMb = 1024,
    [string]$SlotDir
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $SlotDir) {
    $repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    $SlotDir = Join-Path $repo '.claude/gate-slots'
}
if (-not (Test-Path -LiteralPath $SlotDir)) {
    New-Item -ItemType Directory -Path $SlotDir -Force | Out-Null
}

function Get-FreeMb {
    # Win32_OperatingSystem reports FreePhysicalMemory in KIBIbytes. Reading it as MB silently
    # over-reports free memory by 1024x, which would make the floor unreachable and the check inert.
    $os = Get-CimInstance -ClassName Win32_OperatingSystem
    return [int]($os.FreePhysicalMemory / 1024)
}

function Test-HolderAlive {
    param([string]$Path)
    # A slot file whose PID is gone is FREE. This is the self-healing property: the OOM kill this gate
    # exists to prevent is also the most likely way a holder dies, so treating a stale file as held
    # would deadlock exactly when the gate mattered.
    # NOT $pid: that is a read-only PowerShell automatic variable, and assigning to it under
    # Set-StrictMode is an error rather than a shadow.
    try { $holder = [int](Get-Content -LiteralPath $Path -TotalCount 1 -ErrorAction Stop) }
    catch { return $false }
    if ($holder -le 0) { return $false }
    try { $null = Get-Process -Id $holder -ErrorAction Stop; return $true }
    catch { return $false }
}

function Get-Slots {
    1..$Holders | ForEach-Object {
        $path = Join-Path $SlotDir ("slot-{0}.pid" -f $_)
        [pscustomobject]@{
            Index = $_
            Path  = $path
            Held  = (Test-Path -LiteralPath $path) -and (Test-HolderAlive -Path $path)
        }
    }
}

if ($Acquire) {
    $freeMb = Get-FreeMb
    if ($freeMb -lt $MinFreeMb) {
        # [Console]::Error, not Write-Error. With $ErrorActionPreference = 'Stop' a Write-Error
        # THROWS, so the `exit 5` below would never run and the caller would see a generic
        # terminating error instead of the documented code - the exit contract in the help above
        # would be a lie. Verified by running it.
        [Console]::Error.WriteLine("refusing admission: $freeMb MB free, floor is $MinFreeMb MB")
        exit 5
    }
    foreach ($slot in Get-Slots) {
        if ($slot.Held) { continue }
        # Claim by CREATING the file exclusively. Test-then-write would let two callers past the same
        # free slot; [System.IO.File]::Open with CreateNew is a compare-and-swap on the filesystem,
        # the same shape as the claim mutex's POST /git/refs.
        try {
            $stream = [System.IO.File]::Open(
                $slot.Path, [System.IO.FileMode]::CreateNew,
                [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        }
        catch [System.IO.IOException] {
            # The file exists: either a live holder, or a stale file whose process is gone.
            #
            # RECLAIM IN PLACE UNDER AN EXCLUSIVE HANDLE. Never delete-then-recreate. Both reviewers
            # independently found the race in that version on #287: two acquirers can each observe the
            # SAME dead pid, then the loser deletes the winner's freshly written slot and recreates it
            # for itself, so both report success and the holder ceiling is defeated.
            #
            # FileShare.None means only one acquirer can hold this handle at a time, so reading the pid
            # and overwriting it happen inside one exclusive window - the check and the write cannot be
            # interleaved, and the file is never removed, so there is nothing for a successor to lose.
            try {
                $stream = [System.IO.File]::Open(
                    $slot.Path, [System.IO.FileMode]::Open,
                    [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
            }
            catch { continue }  # another acquirer holds the handle right now, or the file vanished
            try {
                $buffer = New-Object byte[] 32
                $read = $stream.Read($buffer, 0, $buffer.Length)
                $existing = ([System.Text.Encoding]::ASCII.GetString($buffer, 0, $read)).Trim()
                $alive = $false
                if ($existing -match '^\d+$') {
                    try { $null = Get-Process -Id ([int]$existing) -ErrorAction Stop; $alive = $true }
                    catch { $alive = $false }
                }
                # Unparseable contents count as dead: a truncated write from a holder killed mid-write
                # names no live process, and treating it as held would deadlock the slot forever.
                if ($alive) { $stream.Dispose(); continue }
                $stream.SetLength(0)
                $stream.Position = 0
            }
            catch { $stream.Dispose(); continue }
        }
        try {
            $bytes = [System.Text.Encoding]::ASCII.GetBytes("$PID`n")
            $stream.Write($bytes, 0, $bytes.Length)
            $stream.Flush()
        }
        finally {
            $stream.Dispose()
        }
        Write-Output $slot.Path
        exit 0
    }
    [Console]::Error.WriteLine("refusing admission: all $Holders slots are held by live processes")
    exit 4
}

if ($Release) {
    foreach ($slot in Get-Slots) {
        if (-not (Test-Path -LiteralPath $slot.Path)) { continue }
        try { $held = [int](Get-Content -LiteralPath $slot.Path -TotalCount 1) } catch { continue }
        if ($held -ne $PID) { continue }
        try { Remove-Item -LiteralPath $slot.Path -Force -ErrorAction Stop }
        catch {
            Write-Warning ("slot {0} could not be released; PID liveness will reclaim it" -f $slot.Index)
        }
    }
    # Deliberately exit 0 even when nothing was released. Releasing a slot a reaper already reclaimed
    # is the normal case after a crash, and failing here would put an error on the recovery path.
    exit 0
}

$freeMb = Get-FreeMb
Write-Output ("free: {0} MB (floor {1} MB)" -f $freeMb, $MinFreeMb)
foreach ($slot in Get-Slots) {
    $state = if ($slot.Held) { 'held' } else { 'free' }
    $who = if (Test-Path -LiteralPath $slot.Path) {
        (Get-Content -LiteralPath $slot.Path -TotalCount 1 -ErrorAction SilentlyContinue)
    }
    else { '-' }
    Write-Output ("slot {0}: {1} (pid {2})" -f $slot.Index, $state, $who)
}
exit 0

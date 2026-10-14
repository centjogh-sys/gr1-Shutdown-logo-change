[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z]:?$')]
    [string]$Drive,
    [Parameter(Mandatory = $true)]
    [ValidateSet('install-logo', 'restore-logo')]
    [string]$Stage,
    [switch]$WhatIf,
    [switch]$ConfirmGRCard,
    [switch]$IUnderstandInternalWrite
)

$ErrorActionPreference = 'Stop'

function Flush-FileToDisk {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $stream = $null
    try {
        # Opening for read/write lets FlushFileBuffers flush this file's data.
        $stream = [System.IO.File]::Open(
            $Path,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::ReadWrite)
        $stream.Flush($true)
    } finally {
        if ($null -ne $stream) {
            $stream.Dispose()
        }
    }
}

function Assert-CopiedFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [long]$ExpectedSize,
        [Parameter(Mandatory = $true)]
        [string]$ExpectedSha256
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Verification failed: missing $Path."
    }
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.Length -ne $ExpectedSize) {
        throw "Verification failed: $Path has size $($item.Length), expected $ExpectedSize."
    }
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
    if ($actual -ne $ExpectedSha256) {
        throw "Verification failed: $Path SHA256 is $actual, expected $ExpectedSha256."
    }
}

function Flush-GR1VolumeCache {
    param(
        [Parameter(Mandatory = $true)]
        [string]$DriveRoot
    )

    $letter = [char]$DriveRoot.Substring(0, 1)
    try {
        Write-VolumeCache -DriveLetter $letter -ErrorAction Stop
        Write-Host "Volume cache flushed for $DriveRoot."
        return $true
    } catch {
        Write-Warning "Write-VolumeCache failed for ${DriveRoot}: $($_.Exception.Message)"
    }

    # Fallback for systems where the Storage module cannot open the volume.
    # This is best effort; the per-file FlushFileBuffers above is mandatory.
    $typeName = 'GR1LogoInstaller.NativeMethods'
    try {
        if (-not ([System.Management.Automation.PSTypeName]$typeName).Type) {
            Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

namespace GR1LogoInstaller
{
    public static class NativeMethods
    {
        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode, EntryPoint = "CreateFileW")]
        public static extern IntPtr CreateFile(
            string lpFileName,
            uint dwDesiredAccess,
            uint dwShareMode,
            IntPtr lpSecurityAttributes,
            uint dwCreationDisposition,
            uint dwFlagsAndAttributes,
            IntPtr hTemplateFile);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool FlushFileBuffers(IntPtr hFile);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool CloseHandle(IntPtr hObject);
    }
}
'@ -ErrorAction Stop
        }
        $handle = [GR1LogoInstaller.NativeMethods]::CreateFile(
            ('\\.\' + $letter + ':'),
            0xC0000000,
            0x00000003,
            [IntPtr]::Zero,
            3,
            0,
            [IntPtr]::Zero)
        if ($handle.ToInt64() -eq -1) {
            throw "CreateFile failed with Win32 error $([Runtime.InteropServices.Marshal]::GetLastWin32Error())."
        }
        try {
            if (-not [GR1LogoInstaller.NativeMethods]::FlushFileBuffers($handle)) {
                throw "FlushFileBuffers failed with Win32 error $([Runtime.InteropServices.Marshal]::GetLastWin32Error())."
            }
        } finally {
            [void][GR1LogoInstaller.NativeMethods]::CloseHandle($handle)
        }
        Write-Host "Volume handle flushed for $DriveRoot."
        return $true
    } catch {
        Write-Warning "Volume-handle flush failed for ${DriveRoot}: $($_.Exception.Message)"
        return $false
    }
}

# ---------------------------------------------------------------------------
# This is the only package in the project that writes the camera's internal
# NAND, and only ever by REPLACING or REMOVING the single file B:/dvf_TDSt.brp.
# Both directions require an explicit acknowledgement switch.
# ---------------------------------------------------------------------------
if (-not $IUnderstandInternalWrite) {
    throw 'These stages modify the camera''s internal NAND (one file on B:/). -IUnderstandInternalWrite is required.'
}
if (-not $ConfirmGRCard) { throw 'Explicit confirmation of a GR-formatted card is required: -ConfirmGRCard.' }

$driveRoot = ($Drive.TrimEnd(':') + ':\').ToUpperInvariant()
$logicalDisk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$($driveRoot.Substring(0, 2))'"
if ($null -eq $logicalDisk -or $logicalDisk.DriveType -ne 2) { throw 'Only removable media are allowed.' }
if ($logicalDisk.FileSystem -ne 'FAT32') { throw 'Only FAT32 is permitted.' }
if ((Test-Path -LiteralPath (Join-Path $driveRoot 'PRIVATE\PANA_GRP')) -or
    (Get-ChildItem -LiteralPath $driveRoot -Filter 'GX9*.TXT' -File)) { throw 'Panasonic/GX9 card detected; refusing to write.' }
$dcim = Join-Path $driveRoot 'DCIM'
if (-not (Test-Path -LiteralPath $dcim -PathType Container)) { throw 'DCIM is missing.' }
$ricohPhotoDirectory = @(Get-ChildItem -LiteralPath $dcim -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^\d{3}RICOH$' })
if (-not $ricohPhotoDirectory -and ($logicalDisk.VolumeName -notmatch '^RICOH')) {
    throw 'Neither a numbered RICOH photo directory nor a RICOH volume label; use a spare card formatted by the GR.'
}
if ($logicalDisk.FreeSpace -lt 16777216) { throw 'At least 16 MiB of free card space is required.' }

# The card must be clean: no leftover entry or probe output of any kind.
foreach ($name in @('Startup.ttl', 'Shutdown.ttl', 'update.ttl', 'GR1PIN.TXT', 'GR1NONE.TXT', 'GR1POUT.TXT', 'GR1SOUT.TXT',
    'GR1RECON.TXT', 'GR1SRECON.TXT', 'GR1START.TXT', 'GR1WROTE.TXT', 'GR1REST.TXT',
    'GR1HASA.TXT', 'GR1HASB.TXT', 'GR1HASC.TXT', 'GR1HASPROP.TXT', 'GR1HASAUPD.TXT', 'GR1HASPCMN.TXT',
    'GR1BKB.BRP', 'GR1BKC.BRP', 'GR1BK_TDSt.BRP', 'GR1BK_KEY.BRP', 'GR1BK_PBWB.BRP', 'dvf_TDSt.brp')) {
    if (Test-Path -LiteralPath (Join-Path $driveRoot $name)) {
        throw "Card is not clean: $name is present. Archive and remove it first; nothing was copied."
    }
}
if (Test-Path -LiteralPath (Join-Path $driveRoot 'gr1logo') -PathType Container) {
    throw 'Card is not clean: gr1logo folder already present; nothing was copied.'
}

$interference = @(Get-ChildItem -LiteralPath $driveRoot -Force -ErrorAction SilentlyContinue |
    Where-Object {
        ($_.PSIsContainer -and $_.Name -like 'GR1FILTER*') -or
        ((-not $_.PSIsContainer) -and ($_.Name -like '*.frm' -or $_.Name -ieq 'GR1-CARD-STATE.TXT'))
    })
if ($interference.Count -gt 0) {
    $names = ($interference | ForEach-Object { $_.Name }) -join ', '
    throw "Card contains files from another workflow: $names. Archive/remove them first; nothing was copied. Firmware/filter files can prevent the shutdown-logo hook from running."
}

$packageRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$sourceDirectory = Join-Path $packageRoot $Stage
if (-not (Test-Path -LiteralPath $sourceDirectory -PathType Container)) { throw 'Stage directory is missing.' }
$manifest = Get-Content -LiteralPath (Join-Path $packageRoot 'manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($manifest.internal_write_stages -notcontains $Stage) { throw 'Stage is not declared as an internal-write stage.' }
if ($manifest.writes_internal_nand -ne $true) { throw 'Manifest does not declare internal NAND writes.' }

$entryNames = @('Shutdown.ttl.disabled')
$expectedNames = @($entryNames) + @('GR1PIN.TXT')
$expectLogoFolder = ($Stage -eq 'install-logo')
if ($expectLogoFolder) { $expectedNames += 'gr1logo' }
$actualFiles = @(Get-ChildItem -LiteralPath $sourceDirectory -Force)
if ($actualFiles.Count -ne $expectedNames.Count) { throw 'Unexpected stage file count.' }
foreach ($item in $actualFiles) {
    if ($item.Name -notin $expectedNames) { throw "Unexpected stage entry: $($item.Name)" }
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Reparse points are not allowed: $($item.Name)" }
}

foreach ($name in $expectedNames) {
        $source = Join-Path $sourceDirectory $name
        $record = $manifest.stages.PSObject.Properties[$Stage].Value.PSObject.Properties[$name].Value
        if ($null -eq $record) { throw "Manifest is missing an entry for $name." }
        if ($record.type -eq 'directory') {
            $inner = @(Get-ChildItem -LiteralPath $source -Force)
            if ($inner.Count -ne 1 -or $inner[0].PSIsContainer -or $inner[0].Name -ne 'dvf_TDSt.brp') {
                throw 'Logo folder must contain exactly dvf_TDSt.brp.'
            }
            if ($inner[0].Length -ne $record.inner.size -or
                (Get-FileHash -LiteralPath $inner[0].FullName -Algorithm SHA256).Hash -ne $record.inner.sha256) {
                throw 'Logo payload hash mismatch.'
            }
        } else {
            if ((Get-Item -LiteralPath $source).Length -ne $record.size -or
                (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash -ne $record.sha256) {
                throw "Package hash mismatch: $name."
            }
        }
}

# Strict whitelist for the TTL entry. Anything not listed here is refused.
$scriptLines = @($entryNames | ForEach-Object { Get-Content -LiteralPath (Join-Path $sourceDirectory $_) -Encoding ASCII })
$allowedPairs = @('H:/GR1PIN.TXT|H:/GR1START.TXT', 'H:/GR1PIN.TXT|H:/GR1WROTE.TXT', 'H:/GR1PIN.TXT|H:/GR1REST.TXT')
$installShell = @('nanddrivewp B off', 'remove B:/dvf_TDSt.brp', 'copyfiles H:/gr1logo B:/', 'nanddrivewp B on')
$restoreShell = @('nanddrivewp B off', 'remove B:/dvf_TDSt.brp', 'nanddrivewp B on')
$allowedShell = if ($Stage -eq 'install-logo') { $installShell } else { $restoreShell }
$allowedSearches = @('H:/GR1PIN.TXT', 'H:/GR1NONE.TXT', 'H:/GR1START.TXT', 'H:/GR1WROTE.TXT', 'H:/GR1REST.TXT', 'B:/dvf_TDSt.brp')
$operands = @('a', 'b', 'result')
$depth = 0
foreach ($line in $scriptLines) {
    $statement = $line.Trim()
    if (-not $statement -or $statement.StartsWith(';')) { continue }
    if ($statement -match '^filecopy "([^"]+)" "([^"]+)"$') {
        if (($Matches[1] + '|' + $Matches[2]) -notin $allowedPairs) { throw 'Unapproved copy.' }
    } elseif ($statement -match '^filesearch "([^"]+)"$') {
        if ($Matches[1] -notin $allowedSearches) { throw 'Unapproved search path.' }
    } elseif ($statement -match '^sprintf "([^"]+)"$') {
        if ($Matches[1] -notin $allowedShell) { throw 'Unapproved shell command.' }
    } elseif ($statement -eq 'sendln inputstr') {
        continue
    } elseif ($statement -match '^(a|b) = result$') {
        continue
    } elseif ($statement -match '^if (\S+) (!=) (\S+) then$') {
        if ($Matches[1] -notin $operands -or $Matches[3] -notin $operands) { throw 'Unapproved condition.' }
        $depth++
    } elseif ($statement -eq 'endif') {
        $depth--
        if ($depth -lt 0) { throw 'Unbalanced script.' }
    } elseif ($statement -ne 'exit') { throw "Unapproved script command: $statement" }
}
if ($depth -ne 0) { throw 'Unbalanced script.' }
if ($Stage -eq 'restore-logo' -and ($scriptLines -join "`n") -match 'copyfiles ') { throw 'Restore stage must not copy anything.' }

if ($WhatIf) {
    Write-Output "WhatIf: validated stage '$Stage' for SD card $driveRoot; nothing was copied."
    return
}

foreach ($name in $expectedNames) {
    $source = Join-Path $sourceDirectory $name
    if ($name -eq 'gr1logo') {
        $destinationFolder = Join-Path $driveRoot 'gr1logo'
        New-Item -ItemType Directory -Path $destinationFolder | Out-Null
        Copy-Item -LiteralPath (Join-Path $source 'dvf_TDSt.brp') -Destination (Join-Path $destinationFolder 'dvf_TDSt.brp')
        continue
    }
    $finalName = $name -replace '\.disabled$', ''
    if (Test-Path -LiteralPath (Join-Path $driveRoot $finalName)) { throw "Conflict: $finalName." }
    Copy-Item -LiteralPath $source -Destination (Join-Path $driveRoot $finalName)
}

# Copy-Item returns after handing data to the Windows cache. Flush every
# destination file before reporting success so an immediate card removal
# cannot lose Shutdown.ttl or the logo payload.
$copiedTargets = @()
foreach ($name in $expectedNames) {
    $record = $manifest.stages.PSObject.Properties[$Stage].Value.PSObject.Properties[$name].Value
    if ($name -eq 'gr1logo') {
        $copiedTargets += [pscustomobject]@{
            Path = Join-Path (Join-Path $driveRoot 'gr1logo') 'dvf_TDSt.brp'
            Size = [long]$record.inner.size
            Sha256 = [string]$record.inner.sha256
        }
    } else {
        $finalName = $name -replace '\.disabled$', ''
        $copiedTargets += [pscustomobject]@{
            Path = Join-Path $driveRoot $finalName
            Size = [long]$record.size
            Sha256 = [string]$record.sha256
        }
    }
}

foreach ($target in $copiedTargets) {
    Flush-FileToDisk -Path $target.Path
}
[void](Flush-GR1VolumeCache -DriveRoot $driveRoot)

$verified = $false
$lastVerificationError = $null
for ($attempt = 1; $attempt -le 3; $attempt++) {
    try {
        Start-Sleep -Milliseconds (150 * $attempt)
        foreach ($target in $copiedTargets) {
            Assert-CopiedFile -Path $target.Path -ExpectedSize $target.Size -ExpectedSha256 $target.Sha256
        }
        $verified = $true
        break
    } catch {
        $lastVerificationError = $_
        if ($attempt -lt 3) {
            foreach ($target in $copiedTargets) {
                Flush-FileToDisk -Path $target.Path
            }
            [void](Flush-GR1VolumeCache -DriveRoot $driveRoot)
        }
    }
}
if (-not $verified) {
    throw "Copy verification failed after 3 attempts: $($lastVerificationError.Exception.Message)"
}

Write-Host "Copy flushed and verified on $driveRoot."
Write-Output "GR1_INSTALL_VERIFIED: $Stage on $driveRoot"
Write-Host "Installed for stage '$Stage': $((($expectedNames | ForEach-Object { $_ -replace '\.disabled$', '' }) -join ', '))"
if ($Stage -eq 'install-logo') {
    Write-Host 'On next normal power-off this REPLACES only B:/dvf_TDSt.brp on internal NAND.'
    Write-Host 'The new shutdown logo may appear on the next normal power-off after that.'
} else {
    Write-Host 'On next normal power-off this REMOVES B:/dvf_TDSt.brp from internal NAND, restoring the stock logo.'
}

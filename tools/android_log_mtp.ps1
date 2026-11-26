# Pull ROUT III journals from a phone over USB/MTP, no adb and no downloads.
#
# The phone must be connected as "File transfer" (MTP) and unlocked.  On Android
# 11+ Android/data is sometimes invisible over MTP; then this script finds
# nothing and adb (tools/android_log.py) is the only remaining route.
param(
    [string]$OutDir = (Join-Path $env:TEMP "rout3diag")
)

$ErrorActionPreference = 'Stop'
$wanted = @('rout3-java.log', 'rout3-startup.log', 'rout3-boot.json')
$shell = New-Object -ComObject Shell.Application
$script:found = @()

function Search-Folder($folder, [int]$depth) {
    if ($null -eq $folder -or $depth -gt 12) { return }
    foreach ($item in $folder.Items()) {
        if ($item.IsFolder) {
            # Do not walk local drives: only the removable device is interesting.
            if ($item.Path -match '^[A-Za-z]:\\') { continue }
            try {
                $sub = $shell.NameSpace($item.Path)
                if ($null -ne $sub) { Search-Folder $sub ($depth + 1) }
            } catch { }
        } elseif ($wanted -contains $item.Name) {
            $script:found += $item
        }
    }
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$thisPc = $shell.NameSpace(17)
if ($null -eq $thisPc) { Write-Error 'Shell namespace 17 (This PC) is unavailable'; exit 2 }

foreach ($item in $thisPc.Items()) {
    if (-not $item.IsFolder) { continue }
    if ($item.Path -match '^[A-Za-z]:\\') { continue }
    try {
        $device = $shell.NameSpace($item.Path)
        if ($null -ne $device) {
            Write-Host "Device: $($item.Name)"
            Search-Folder $device 0
        }
    } catch { }
}

if ($script:found.Count -eq 0) {
    Write-Host 'No journals found. Check MTP mode and the lock screen; if Android/data is hidden from MTP, adb is required.'
    exit 1
}

$dest = $shell.NameSpace((Resolve-Path $OutDir).Path)
foreach ($item in $script:found) {
    $dest.CopyHere($item, 16)  # 16 = Yes to All, no overwrite prompt
    Write-Host "Copied: $($item.Name)"
}
Write-Host "Files in: $((Resolve-Path $OutDir).Path)"

# Packs this PC's MastGaane data into one zip for the Mac (see INSTALL-MAC.md):
#   the database (every song, what you liked, the taste it learned), the downloaded songs and
#   programmes (data\archive_audio), the host's recorded voice clips, .env (your API keys) and
#   browser.json (the YouTube Music login).
# Left out: old database backups, voice-debug clips, logs, and chrome-profile (Chrome logins
# cannot move between Windows and a Mac; log in to Instagram again there).
#
#   powershell -ExecutionPolicy Bypass -File installer\pack_for_mac.ps1 [-Out <zip>]
#
# The zip holds your keys and logins: move it by USB drive or your own private cloud folder (iCloud,
# Google Drive, OneDrive), never a public link, and delete it
# once the Mac is set up.
param([string]$Out = (Join-Path $env:USERPROFILE "Downloads\MastGaane-data.zip"))

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$running = Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -match "mastgaane" -and $_.CommandLine -match "serve|run_mastgaane" }
if ($running) {
    Write-Host "MastGaane is running - quit it first (tray icon > Quit MastGaane) so the database is not mid-write." -ForegroundColor Yellow
    exit 1
}
if (-not (Test-Path "data\mastgaane.db")) { Write-Host "No data\mastgaane.db here - nothing to pack." -ForegroundColor Yellow; exit 1 }

$items = @("data")
foreach ($f in ".env", "browser.json") { if (Test-Path $f) { $items += $f } }
$excludes = @("data/backups", "data/*.before-*.db", "data/voice_debug", "data/voice_debug.log", "data/library*.log",
              "data/*.db-journal", "*.tmp")

if (Test-Path $Out) { Remove-Item $Out -Force }
New-Item -ItemType Directory -Force (Split-Path -Parent $Out) | Out-Null
Write-Host "Packing $($items -join ', ') -> $Out (a minute or two)..."
$tarArgs = @("-a", "-c", "-f", $Out)
foreach ($e in $excludes) { $tarArgs += @("--exclude", $e) }
& "$env:SystemRoot\System32\tar.exe" @tarArgs @items
if ($LASTEXITCODE -ne 0) { Write-Host "tar failed ($LASTEXITCODE)" -ForegroundColor Red; exit 1 }

$mb = [Math]::Round((Get-Item $Out).Length / 1MB)
Write-Host "Done: $Out ($mb MB). Copy it to the Mac (USB drive or your private cloud folder) into Downloads, then run there:" -ForegroundColor Green
Write-Host "  bash installer/install_mac.sh ~/Downloads/MastGaane-data.zip"

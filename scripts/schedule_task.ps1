# Registers a nightly Windows Task Scheduler job that runs `mastgaane run-all`.
# Usage (PowerShell, from the project root):  .\scripts\schedule_task.ps1 -At "00:30"
param([string]$At = "00:30", [string]$TaskName = "MastGaane Nightly")

$root = Split-Path -Parent $PSScriptRoot
$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) { throw "uv not found on PATH" }
$log = Join-Path $root "reports\nightly.log"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-NoProfile -WindowStyle Minimized -Command `"Set-Location '$root'; `$env:PYTHONUTF8=1; & '$uv' run mastgaane run-all *>> '$log'`""
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2) -RestartCount 1 -RestartInterval (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -RunLevel Limited -Force | Out-Null
Write-Host "Registered '$TaskName' daily at $At. Log: $log"
Write-Host "Note: the task runs in your interactive session (headed Chrome), so stay logged in to Windows (screen lock is fine)."

# JobPilot daily pipeline (Windows Task Scheduler entry point).
#
# What it does:
#   1. makes sure the Postgres container is up
#   2. runs the collector, which also extracts facts from new postings
#   3. writes everything to logs/run_YYYY-MM-DD.log (git-ignored)
#   4. exits with the collector's exit code, so Task Scheduler's "Last Run Result"
#      shows 0x0 on success and 0x1 when something needs your attention
#
# Register once (run from the repo root in PowerShell):
#   $action   = New-ScheduledTaskAction -Execute "powershell.exe" `
#               -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PWD\scripts\run_daily.ps1`""
#   $trigger  = New-ScheduledTaskTrigger -Daily -At 7:40am
#   $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun `
#               -ExecutionTimeLimit (New-TimeSpan -Hours 1)
#   Register-ScheduledTask -TaskName "JobPilot daily" -Action $action -Trigger $trigger -Settings $settings
#
# Run it now (to test):   Start-ScheduledTask -TaskName "JobPilot daily"

$ErrorActionPreference = "Stop"
$root   = Split-Path -Parent $PSScriptRoot             # repo root (scripts/..)
$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("run_" + (Get-Date -Format "yyyy-MM-dd") + ".log")

$exitCode = 1
Start-Transcript -Path $log -Append | Out-Null
try {
    Write-Output "=== JobPilot daily run: $(Get-Date -Format s) ==="

    docker compose -f "$root\infra\docker-compose.yml" up -d db
    Start-Sleep -Seconds 10                            # let Postgres accept connections

    Set-Location "$root\backend"
    uv run python -m app.collector.run_daily           # collect + extract in one run
    $exitCode = $LASTEXITCODE
    Write-Output "=== finished with exit code $exitCode ==="
}
catch {
    Write-Output "=== FAILED before the collector finished: $_ ==="
}
finally {
    Stop-Transcript | Out-Null
}
exit $exitCode
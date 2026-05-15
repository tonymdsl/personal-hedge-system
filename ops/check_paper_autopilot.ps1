$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$StatePath = Join-Path $ProjectRoot "cache\autopilot_state.json"

if (Test-Path -LiteralPath $StatePath) {
    $State = Get-Content -Raw -LiteralPath $StatePath | ConvertFrom-Json
    [pscustomobject]@{
        Enabled = $State.enabled
        Paused = $State.paused
        LastStatus = $State.last_status
        CurrentStep = $State.current_step
        LastStartedAt = $State.last_started_at
        LastFinishedAt = $State.last_finished_at
        LastError = (($State.last_error -split "`n") | Select-Object -First 1)
    } | Format-List
} else {
    Write-Output "No autopilot state file found at $StatePath"
}

Get-ScheduledTask -TaskName "MeridianPaperAutopilot24x7" -ErrorAction SilentlyContinue |
    Select-Object TaskName, State |
    Format-List

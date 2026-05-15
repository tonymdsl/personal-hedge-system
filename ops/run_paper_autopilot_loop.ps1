param(
    [double]$IntervalSeconds = 900
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$CacheDir = Join-Path $ProjectRoot "cache"
$StdoutLog = Join-Path $CacheDir "paper_autopilot_loop_stdout.log"
$StderrLog = Join-Path $CacheDir "paper_autopilot_loop_stderr.log"

New-Item -ItemType Directory -Force -Path $CacheDir | Out-Null
Set-Location $ProjectRoot

while ($true) {
    python -u run_paper_autopilot.py --loop --interval-seconds $IntervalSeconds 1>> $StdoutLog 2>> $StderrLog
    $ExitCode = $LASTEXITCODE
    $Timestamp = (Get-Date).ToUniversalTime().ToString("o")
    Add-Content -LiteralPath $StderrLog -Value "$Timestamp autopilot process exited with code $ExitCode; restarting in 60 seconds"
    Start-Sleep -Seconds 60
}

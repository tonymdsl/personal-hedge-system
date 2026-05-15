param(
    [string]$TaskName = "MeridianPaperAutopilot24x7",
    [double]$IntervalSeconds = 900
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Runner = Join-Path $ProjectRoot "ops\run_paper_autopilot_loop.ps1"
$UserId = "$env:USERDOMAIN\$env:USERNAME"

$RunnerArguments = "-NoProfile -ExecutionPolicy Bypass -File `"$Runner`" -IntervalSeconds $IntervalSeconds"

function Get-ExistingRunnerProcess {
    $Pattern = [Regex]::Escape($Runner)
    Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and $_.CommandLine -match $Pattern
    }
}

try {
    $Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $RunnerArguments
    $Trigger = New-ScheduledTaskTrigger -AtLogOn
    $Settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -RestartCount 999 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -MultipleInstances IgnoreNew
    $Principal = New-ScheduledTaskPrincipal -UserId $UserId -LogonType Interactive -RunLevel Limited

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $Action `
        -Trigger $Trigger `
        -Settings $Settings `
        -Principal $Principal `
        -Force | Out-Null

    Start-ScheduledTask -TaskName $TaskName
    Write-Output "Installed and started scheduled task: $TaskName"
    exit 0
} catch {
    Write-Warning "Scheduled Task registration failed: $($_.Exception.Message)"
}

$StartupDir = [Environment]::GetFolderPath("Startup")
$ShortcutPath = Join-Path $StartupDir "$TaskName.lnk"
$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = "powershell.exe"
$Shortcut.Arguments = $RunnerArguments
$Shortcut.WorkingDirectory = $ProjectRoot
$Shortcut.WindowStyle = 7
$Shortcut.Description = "Meridian paper autopilot 24x7 loop"
$Shortcut.Save()

$ExistingRunner = Get-ExistingRunnerProcess | Select-Object -First 1
if ($ExistingRunner) {
    Write-Output "Scheduled Task unavailable; installed Startup shortcut and loop is already running: PID $($ExistingRunner.ProcessId)"
    exit 0
}

Start-Process -FilePath "powershell.exe" -ArgumentList $RunnerArguments -WorkingDirectory $ProjectRoot -WindowStyle Hidden
Write-Output "Scheduled Task unavailable; installed Startup shortcut and started hidden loop: $ShortcutPath"

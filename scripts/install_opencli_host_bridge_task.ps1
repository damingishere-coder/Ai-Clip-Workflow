param(
    [int]$BridgePort = 8765,
    [string]$TaskName = "NiuMa Studio OpenCLI Host Bridge"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$startScript = Join-Path $ProjectRoot "scripts\start_opencli_host_bridge.ps1"
if (-not (Test-Path $startScript)) {
    throw "Missing helper start script: $startScript"
}

$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$argument = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$startScript`" -BridgePort $BridgePort"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argument -WorkingDirectory $ProjectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "Starts the NiuMa Studio Windows opencli helper after user logon." `
    -Force | Out-Null

Write-Host ("Scheduled task installed: {0}" -f $TaskName)
Write-Host "Starting the helper once now..."
& $startScript -BridgePort $BridgePort

param(
    [int]$BridgePort = 8765
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$HelperScript = Join-Path $ProjectRoot "scripts\start_opencli_host_bridge.ps1"
$TaskName = "NiuMa Studio OpenCLI Helper"
$CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$HelperScript`" -BridgePort $BridgePort"
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
$principal = New-ScheduledTaskPrincipal `
    -UserId $CurrentUser `
    -LogonType Interactive `
    -RunLevel LeastPrivilege

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Start NiuMa Studio Windows opencli helper for Docker send center." `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 2

$previousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
$health = Invoke-WebRequest -Uri "http://127.0.0.1:$BridgePort/health" -UseBasicParsing -TimeoutSec 5
$ErrorActionPreference = $previousErrorActionPreference

if ($health) {
    Write-Host "NiuMa Studio opencli helper autostart is installed and running."
} else {
    Write-Host "Autostart task was installed, but the helper is not responding yet. Please check opencli_bridge_$BridgePort.err.log."
}

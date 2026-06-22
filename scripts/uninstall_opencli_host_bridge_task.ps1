param(
    [int]$BridgePort = 8765,
    [string]$TaskName = "NiuMa Studio OpenCLI Host Bridge",
    [switch]$KeepRunning
)

$ErrorActionPreference = "Stop"

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host ("Scheduled task removed: {0}" -f $TaskName)
} else {
    Write-Host ("Scheduled task was not found: {0}" -f $TaskName)
}

if ($KeepRunning) {
    Write-Host "The running opencli helper was kept alive."
    exit 0
}

$bridgeConnections = Get-NetTCPConnection -LocalPort $BridgePort -State Listen -ErrorAction SilentlyContinue
$bridgeProcessIds = @($bridgeConnections | Select-Object -ExpandProperty OwningProcess -Unique)
foreach ($processId in $bridgeProcessIds) {
    if (-not $processId -or $processId -eq $PID) {
        continue
    }

    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
    $commandLine = [string]$processInfo.CommandLine
    if ($commandLine -like "*opencli_host_bridge.py*") {
        Write-Host ("Stopping opencli helper: PID {0}" -f $processId)
        Stop-Process -Id $processId -Force
    }
}

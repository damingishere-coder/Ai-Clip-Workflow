param(
    [int]$Port = 8765,
    [string]$TaskName = 'NiuMa Studio Docker Watcher',
    [switch]$KeepWorker
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path.TrimEnd('\')
$WatcherScript = Join-Path $ProjectRoot 'scripts\watch_docker_publish_worker.ps1'
$WorkerScript = Join-Path $ProjectRoot 'scripts\publish_host_worker.py'
$LegacyWorkerScript = Join-Path $ProjectRoot 'scripts\opencli_host_bridge.py'

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    $ownedTask = $false
    foreach ($action in @($task.Actions)) {
        if (
            ([string]$action.Arguments).IndexOf($WatcherScript, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -and
            ([string]$action.WorkingDirectory).TrimEnd('\') -ieq $ProjectRoot
        ) {
            $ownedTask = $true
        }
    }
    if (-not $ownedTask) {
        throw "The scheduled task '$TaskName' does not belong to this project and was preserved."
    }
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host 'Docker watcher task removed.'
}

$watcherProcesses = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
        ([string]$_.CommandLine).IndexOf($WatcherScript, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
    }
foreach ($watcherProcess in $watcherProcesses) {
    if ($watcherProcess.ProcessId -and $watcherProcess.ProcessId -ne $PID) {
        Stop-Process -Id $watcherProcess.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

if (-not $KeepWorker) {
    $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    foreach ($listener in $listeners) {
        $ownedProcessId = [int]$listener.OwningProcess
        $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$ownedProcessId" -ErrorAction SilentlyContinue
        $commandLine = [string]$processInfo.CommandLine
        if (
            $commandLine.IndexOf($WorkerScript, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -or
            $commandLine.IndexOf($LegacyWorkerScript, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
        ) {
            Stop-Process -Id $ownedProcessId -Force -ErrorAction SilentlyContinue
            Write-Host ("Windows publish worker stopped: PID {0}." -f $ownedProcessId)
        }
    }
}

Write-Host 'Uninstall completed. Database, task files, Chrome profiles, and logs were preserved.'

param(
    [int]$Port = 8765,
    [int]$PollSeconds = 3,
    [int]$StopGraceSeconds = 15,
    [string]$TaskName = 'NiuMa Studio Docker Watcher'
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path.TrimEnd('\')
$WatcherScript = Join-Path $ProjectRoot 'scripts\watch_docker_publish_worker.ps1'
$LegacyTaskName = 'NiuMa Studio OpenCLI Host Bridge'

if (-not (Test-Path -LiteralPath $WatcherScript)) {
    throw "Docker watcher was not found: $WatcherScript"
}

function Test-TaskBelongsToProject {
    param($Task, [string]$ExpectedScript)

    foreach ($action in @($Task.Actions)) {
        $arguments = [string]$action.Arguments
        $workingDirectory = [string]$action.WorkingDirectory
        if (
            $arguments.IndexOf($ExpectedScript, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -and
            $workingDirectory.TrimEnd('\') -ieq $ProjectRoot
        ) {
            return $true
        }
    }
    return $false
}

$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existingTask -and -not (Test-TaskBelongsToProject -Task $existingTask -ExpectedScript $WatcherScript)) {
    throw "A scheduled task named '$TaskName' already exists but does not belong to this project."
}
if ($existingTask) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
}

$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$arguments = (
    '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -Port {1} -PollSeconds {2} -StopGraceSeconds {3}' -f
    $WatcherScript, $Port, $PollSeconds, $StopGraceSeconds
)
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory $ProjectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -MultipleInstances IgnoreNew `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable `
    -Hidden

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description 'Waits for the NiuMa Studio Docker container and runs the Windows publish worker only while the project is running.' `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName
$taskRunning = $false
foreach ($attempt in 1..20) {
    Start-Sleep -Milliseconds 500
    $task = Get-ScheduledTask -TaskName $TaskName
    if ($task.State -eq 'Running') {
        $taskRunning = $true
        break
    }
}
if (-not $taskRunning) {
    throw "The Docker watcher task was created but did not enter the Running state."
}

$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
$targetRunning = $false
if ($dockerCommand) {
    try {
        $inspect = @(& $dockerCommand.Source inspect niuma-studio --format '{{.State.Running}}' 2>$null)
        $targetRunning = $LASTEXITCODE -eq 0 -and (($inspect -join '').Trim() -eq 'true')
    } catch {
        $targetRunning = $false
    }
}

if ($targetRunning) {
    $connected = $false
    foreach ($attempt in 1..90) {
        Start-Sleep -Seconds 1
        try {
            $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8001/api/publish/scheduler/health' -TimeoutSec 3
            if ($health.running -and $health.worker_available) {
                $connected = $true
                break
            }
        } catch {
            # Docker or the worker is still starting.
        }
    }
    if (-not $connected) {
        throw 'The watcher is running, but publish center did not connect to the Windows worker within 90 seconds. The legacy task was not removed.'
    }
}

$legacyTask = Get-ScheduledTask -TaskName $LegacyTaskName -ErrorAction SilentlyContinue
if ($legacyTask) {
    $legacyScript = Join-Path $ProjectRoot 'scripts\start_opencli_host_bridge.ps1'
    if (Test-TaskBelongsToProject -Task $legacyTask -ExpectedScript $legacyScript) {
        Stop-ScheduledTask -TaskName $LegacyTaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $LegacyTaskName -Confirm:$false
        Write-Host 'Removed the legacy task that pointed to the deleted startup script.'
    } else {
        Write-Warning "The legacy task '$LegacyTaskName' does not belong to this project and was preserved."
    }
}

Write-Host 'Docker watcher installed successfully.'
Write-Host 'The Windows publish worker will start only while the niuma-studio Docker project is running.'

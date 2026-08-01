param(
    [int]$Port = 8765,
    [int]$PollSeconds = 3,
    [int]$StopGraceSeconds = 15,
    [switch]$RunOnce
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path.TrimEnd('\')
$ContainerName = 'niuma-studio'
$ComposeProject = 'niuma-studio'
$ComposeService = 'workflow'
$WorkerScript = (Join-Path $ProjectRoot 'scripts\publish_host_worker.py')
$LegacyWorkerScript = (Join-Path $ProjectRoot 'scripts\opencli_host_bridge.py')
$StartWorkerScript = (Join-Path $ProjectRoot 'scripts\start_publish_worker.ps1')
$ComposeFile = (Join-Path $ProjectRoot 'docker-compose.yml')
$LogDirectory = Join-Path $ProjectRoot 'data\logs'
$LogFile = Join-Path $LogDirectory 'docker_publish_worker_watcher.log'

$PollSeconds = [Math]::Max(2, $PollSeconds)
$StopGraceSeconds = [Math]::Max(5, $StopGraceSeconds)

function Write-WatcherLog {
    param([string]$Message)

    if (-not (Test-Path -LiteralPath $LogDirectory)) {
        New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
    }
    $line = '{0} {1}{2}' -f (Get-Date).ToString('yyyy-MM-dd HH:mm:ss'), $Message, [Environment]::NewLine
    [System.IO.File]::AppendAllText($LogFile, $line, [System.Text.UTF8Encoding]::new($false))
}

function Get-DockerCommand {
    $command = Get-Command docker -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    $desktopDocker = Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin\docker.exe'
    if (Test-Path -LiteralPath $desktopDocker) {
        return $desktopDocker
    }
    return ''
}

function Get-TargetContainerRunning {
    param([string]$DockerCommand)

    if (-not $DockerCommand) {
        return $false
    }
    try {
        $raw = @(& $DockerCommand inspect $ContainerName 2>$null)
        if ($LASTEXITCODE -ne 0 -or -not $raw) {
            return $false
        }
        $items = @((($raw -join [Environment]::NewLine) | ConvertFrom-Json))
        if (-not $items) {
            return $false
        }
        $container = $items[0]
        $labels = $container.Config.Labels
        $projectLabel = [string]$labels.PSObject.Properties['com.docker.compose.project'].Value
        $serviceLabel = [string]$labels.PSObject.Properties['com.docker.compose.service'].Value
        $workingDirectoryLabel = [string]$labels.PSObject.Properties['com.docker.compose.project.working_dir'].Value
        if (-not $workingDirectoryLabel) {
            return $false
        }
        $labelRoot = [System.IO.Path]::GetFullPath($workingDirectoryLabel).TrimEnd('\')
        return (
            [bool]$container.State.Running -and
            $projectLabel -eq $ComposeProject -and
            $serviceLabel -eq $ComposeService -and
            $labelRoot -ieq $ProjectRoot
        )
    } catch {
        return $false
    }
}

function Get-OwnedWorkerProcessIds {
    $processIds = @()
    $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    foreach ($listener in $listeners) {
        $ownedProcessId = [int]$listener.OwningProcess
        if (-not $ownedProcessId) {
            continue
        }
        $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$ownedProcessId" -ErrorAction SilentlyContinue
        $commandLine = [string]$processInfo.CommandLine
        $belongsToProject = (
            $commandLine.IndexOf($WorkerScript, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -or
            $commandLine.IndexOf($LegacyWorkerScript, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
        )
        if ($belongsToProject) {
            $processIds += $ownedProcessId
        }
    }
    return @($processIds | Select-Object -Unique)
}

function Test-OwnedWorkerHealthy {
    $processIds = @(Get-OwnedWorkerProcessIds)
    if (-not $processIds) {
        return $false
    }
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
        return $health.status -eq 'ok' -and $health.worker -eq 'windows_chrome'
    } catch {
        return $false
    }
}

function Start-OwnedWorker {
    Write-WatcherLog 'Target Docker container is running; starting the Windows publish worker.'
    try {
        & $StartWorkerScript -Port $Port -SkipDockerSync -Restart | Out-Null
        if (Test-OwnedWorkerHealthy) {
            Write-WatcherLog 'Windows publish worker started.'
            return $true
        }
        Write-WatcherLog 'Worker start script returned, but the health check still failed.'
    } catch {
        Write-WatcherLog ("Worker start failed: {0}" -f $_.Exception.Message)
    }
    return $false
}

function Stop-OwnedWorker {
    $processIds = @(Get-OwnedWorkerProcessIds)
    foreach ($ownedProcessId in $processIds) {
        try {
            Stop-Process -Id $ownedProcessId -Force -ErrorAction Stop
            Wait-Process -Id $ownedProcessId -Timeout 10 -ErrorAction SilentlyContinue
            Write-WatcherLog ("Target Docker container stopped; worker PID {0} was stopped." -f $ownedProcessId)
        } catch {
            Write-WatcherLog ("Failed to stop worker PID {0}: {1}" -f $ownedProcessId, $_.Exception.Message)
        }
    }
}

function Test-DockerWorkerConnection {
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8001/api/publish/scheduler/health' -TimeoutSec 3
        return [bool]$health.running -and [bool]$health.worker_available
    } catch {
        return $false
    }
}

function Wait-DockerWorkerConnection {
    param([int]$Seconds)

    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    do {
        if (Test-DockerWorkerConnection) {
            return $true
        }
        Start-Sleep -Seconds 2
    } while ([DateTime]::UtcNow -lt $deadline)
    return $false
}

function Repair-DockerWorkerConnection {
    param([string]$DockerCommand)

    if (Wait-DockerWorkerConnection -Seconds 20) {
        Write-WatcherLog 'Docker publish center connected to the Windows publish worker.'
        return $true
    }

    Write-WatcherLog 'Docker has not completed the authenticated worker check; recreating only workflow to sync local configuration.'
    try {
        Push-Location $ProjectRoot
        & $DockerCommand compose -f $ComposeFile up -d --force-recreate --no-deps $ComposeService 2>&1 | Out-Null
        $composeExitCode = $LASTEXITCODE
    } catch {
        $composeExitCode = 1
        Write-WatcherLog ("Docker configuration sync failed: {0}" -f $_.Exception.Message)
    } finally {
        Pop-Location
    }
    if ($composeExitCode -ne 0) {
        Write-WatcherLog 'Docker configuration sync failed; database and task directories were preserved.'
        return $false
    }
    if (Wait-DockerWorkerConnection -Seconds 60) {
        Write-WatcherLog 'Docker configuration synced and publish center connected to the worker.'
        return $true
    }
    Write-WatcherLog 'Docker is running, but publish center did not connect to the worker within 60 seconds.'
    return $false
}

$createdNew = $false
$watcherMutex = [System.Threading.Mutex]::new($true, 'Local\NiuMaStudioDockerPublishWatcher', [ref]$createdNew)
if (-not $createdNew) {
    $watcherMutex.Dispose()
    exit 0
}

$offlineSince = $null
$connectionCheckedForRun = $false
$lastTargetState = $null

try {
    Write-WatcherLog 'Docker watcher started. The worker will start only while the target container is running.'
    do {
        $dockerCommand = Get-DockerCommand
        $targetRunning = Get-TargetContainerRunning -DockerCommand $dockerCommand
        if ($lastTargetState -ne $targetRunning) {
            Write-WatcherLog $(if ($targetRunning) { 'Target container is running.' } else { 'Target container is not running; waiting.' })
            $lastTargetState = $targetRunning
        }

        if ($targetRunning) {
            $offlineSince = $null
            if (-not (Test-OwnedWorkerHealthy)) {
                $connectionCheckedForRun = $false
                Start-OwnedWorker | Out-Null
            }
            if ((Test-OwnedWorkerHealthy) -and -not $connectionCheckedForRun) {
                $connectionCheckedForRun = Repair-DockerWorkerConnection -DockerCommand $dockerCommand
            }
        } else {
            $connectionCheckedForRun = $false
            if ($null -eq $offlineSince) {
                $offlineSince = [DateTime]::UtcNow
            }
            if ([DateTime]::UtcNow.Subtract($offlineSince).TotalSeconds -ge $StopGraceSeconds) {
                Stop-OwnedWorker
            }
        }

        if (-not $RunOnce) {
            Start-Sleep -Seconds $PollSeconds
        }
    } while (-not $RunOnce)
} catch {
    Write-WatcherLog ("Watcher failed: {0}" -f $_.Exception.Message)
    throw
} finally {
    if ($createdNew) {
        $watcherMutex.ReleaseMutex()
    }
    $watcherMutex.Dispose()
}

[CmdletBinding()]
param(
    [switch]$Demo,
    [switch]$RemoveDemoData,
    [switch]$SkipWorker,
    [int]$WorkerPort = 8765
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
Set-Location $ProjectRoot

# ============================================================
#  第 1 步：停止 Docker 服务
# ============================================================
Write-Host '正在停止牛马片场 Docker 服务……'

$arguments = @('compose')
if ($Demo) {
    $arguments += @('-f', 'docker-compose.yml', '-f', 'docker-compose.demo.yml')
}
$arguments += 'down'

$previousErrorActionPreference = $ErrorActionPreference
try {
    $ErrorActionPreference = 'Continue'
    & docker @arguments
    $dockerExitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previousErrorActionPreference
}
if ($dockerExitCode -ne 0) {
    throw '停止 Docker 服务失败。'
}

Write-Host 'Docker 服务已停止。'

# ============================================================
#  第 2 步：停止 Windows 发布 Worker
# ============================================================
if (-not $SkipWorker) {
    $workerScript = Join-Path $ProjectRoot 'scripts\publish_host_worker.py'
    $legacyWorkerScript = Join-Path $ProjectRoot 'scripts\opencli_host_bridge.py'

    $listeners = @(Get-NetTCPConnection -LocalPort $WorkerPort -State Listen -ErrorAction SilentlyContinue)
    $stoppedCount = 0
    foreach ($listener in $listeners) {
        $ownedProcessId = [int]$listener.OwningProcess
        if (-not $ownedProcessId) {
            continue
        }
        try {
            $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$ownedProcessId" -ErrorAction SilentlyContinue
            $commandLine = [string]$processInfo.CommandLine
            $belongsToProject = (
                $commandLine.IndexOf($workerScript, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -or
                $commandLine.IndexOf($legacyWorkerScript, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
            )
            if ($belongsToProject) {
                Stop-Process -Id $ownedProcessId -Force -ErrorAction SilentlyContinue
                Write-Host ("Windows 发布 Worker 已停止（PID：{0}）。" -f $ownedProcessId)
                $stoppedCount++
            }
        } catch {
            # 进程可能已经自己退出了，忽略。
        }
    }

    # 也检查一下 watcher 进程
    $watcherScript = Join-Path $ProjectRoot 'scripts\watch_docker_publish_worker.ps1'
    $watcherProcesses = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            ([string]$_.CommandLine).IndexOf($watcherScript, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
        }
    foreach ($watcherProcess in $watcherProcesses) {
        if ($watcherProcess.ProcessId -and $watcherProcess.ProcessId -ne $PID) {
            Stop-Process -Id $watcherProcess.ProcessId -Force -ErrorAction SilentlyContinue
            Write-Host ("Docker 监视器已停止（PID：{0}）。" -f $watcherProcess.ProcessId)
        }
    }

    if ($stoppedCount -eq 0) {
        Write-Host '没有检测到正在运行的本项目 Windows Worker。'
    }
}

# ============================================================
#  清理 Demo 数据
# ============================================================
if ($Demo -and $RemoveDemoData) {
    foreach ($path in @(
        (Join-Path $ProjectRoot 'demo-data'),
        (Join-Path $ProjectRoot 'workspace\demo')
    )) {
        if (Test-Path $path) {
            Remove-Item -LiteralPath $path -Recurse -Force
            Write-Host "已删除 Demo 数据：$path"
        }
    }
}

Write-Host ''
Write-Host '牛马片场已完全停止。正式数据库与视频目录未被删除。'

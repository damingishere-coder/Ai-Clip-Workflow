param(
    [int]$WorkerPort = 8765,
    [switch]$NoBrowser,
    [switch]$NoBuild
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
Set-Location $ProjectRoot

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw '没有检测到 Docker。请先启动 Docker Desktop，再重新运行本脚本。'
}

Write-Host '第 1 步：启动或复用 Windows Chrome 发布 Worker……'
& (Join-Path $PSScriptRoot 'start_publish_worker.ps1') -Port $WorkerPort -SkipDockerSync

Write-Host '第 2 步：启动牛马片场 Docker 服务……'
$composeArguments = @('compose', 'up', '-d')
if (-not $NoBuild) {
    $composeArguments += '--build'
}
& docker $composeArguments
if ($LASTEXITCODE -ne 0) {
    throw 'Docker 服务启动失败，请查看上方错误信息。现有数据库和任务文件没有被删除。'
}

Write-Host '第 3 步：检查页面和发布 Worker 连接……'
$appReady = $false
$schedulerRestartAttempted = $false
foreach ($attempt in 1..60) {
    Start-Sleep -Seconds 1
    try {
        $app = Invoke-RestMethod -Uri 'http://127.0.0.1:8001/health' -TimeoutSec 3
        $publisher = Invoke-RestMethod -Uri 'http://127.0.0.1:8001/api/publish/scheduler/health' -TimeoutSec 3
        if ($app.status -eq 'ok' -and $publisher.worker_available -and $publisher.running) {
            $appReady = $true
            break
        }
        if (
            $app.status -eq 'ok' -and
            $publisher.worker_available -and
            -not $publisher.running -and
            -not $schedulerRestartAttempted
        ) {
            Write-Host '检测到页面正常但发布调度器未运行，正在重启本项目 workflow 服务……'
            & docker compose restart workflow
            if ($LASTEXITCODE -ne 0) {
                throw '本项目 workflow 服务重启失败；没有删除数据库或任务文件。'
            }
            $schedulerRestartAttempted = $true
        }
    } catch {
        # Docker 首次构建或启动中，继续等待，最长约 60 秒。
    }
}

if (-not $appReady) {
    throw '项目已经尝试启动，但页面或发布 Worker 在 60 秒内没有连接成功。请查看 Docker 和 publish_worker_8765.err.log。'
}

$publishUrl = 'http://127.0.0.1:8001/publish'
Write-Host ("启动成功：{0}" -f $publishUrl)
if (-not $NoBrowser) {
    Start-Process $publishUrl
}

[CmdletBinding()]
param(
    [switch]$Demo,
    [switch]$ResetDemo,
    [switch]$Development,
    [switch]$WithPublisher,
    [switch]$NoBuild,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
Set-Location $ProjectRoot

if ($Demo -and $WithPublisher) {
    throw 'Demo 模式不会连接真实发布 Worker，请移除 -WithPublisher。'
}
if ($Development -and $WithPublisher) {
    throw '开发热重载与真实发布组合请分别启动，避免误触平台操作。'
}

if (-not (Test-Path (Join-Path $ProjectRoot '.env'))) {
    Write-Host '首次运行，正在初始化本地配置……'
    & (Join-Path $PSScriptRoot 'setup.ps1')
    if ($LASTEXITCODE -ne 0) {
        throw '初始化失败。'
    }
}

if ($WithPublisher) {
    $arguments = @()
    if ($NoBuild) { $arguments += '-NoBuild' }
    if ($NoBrowser) { $arguments += '-NoBrowser' }
    & (Join-Path $PSScriptRoot 'start_niuma_studio.ps1') @arguments
    exit $LASTEXITCODE
}

& (Join-Path $PSScriptRoot 'doctor.ps1')
if ($LASTEXITCODE -ne 0) {
    throw '环境检查存在阻塞项，已停止启动。'
}

$composeArguments = @('compose')
if ($Demo) {
    $composeArguments += @('-f', 'docker-compose.yml', '-f', 'docker-compose.demo.yml')
} elseif ($Development) {
    $composeArguments += @('-f', 'docker-compose.yml', '-f', 'docker-compose.dev.yml')
}
$composeArguments += @('up', '-d')
if (-not $NoBuild) {
    $composeArguments += '--build'
}

Write-Host '正在启动牛马片场……'
& docker @composeArguments
if ($LASTEXITCODE -ne 0) {
    throw 'Docker 启动失败。现有数据库与视频文件没有被删除。'
}

$appReady = $false
foreach ($attempt in 1..90) {
    Start-Sleep -Seconds 1
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8001/health' -TimeoutSec 3
        if ($health.status -eq 'ok') {
            $appReady = $true
            break
        }
    } catch {
        # 首次构建和数据库初始化期间继续等待。
    }
}
if (-not $appReady) {
    throw '服务已经启动，但 90 秒内没有通过健康检查。请运行 docker compose logs workflow。'
}

if ($Demo) {
    $demoDb = Join-Path $ProjectRoot 'demo-data\workflow.sqlite3'
    if ($ResetDemo -or -not (Test-Path $demoDb)) {
        Write-Host '正在写入隔离的演示任务与候选片段……'
        $seedArguments = @(
            'compose', '-f', 'docker-compose.yml', '-f', 'docker-compose.demo.yml',
            'exec', '-T', 'workflow', 'python', '/app/scripts/seed_demo_data.py', '--reset'
        )
        & docker @seedArguments
        if ($LASTEXITCODE -ne 0) {
            throw 'Demo 数据初始化失败，请查看容器日志。'
        }
    } else {
        Write-Host '[OK] 已复用现有 Demo 数据；使用 -ResetDemo 可恢复初始演示内容。'
    }
}

$url = 'http://127.0.0.1:8001'
Write-Host "启动成功：$url"
if ($Demo) {
    Write-Host '当前为隔离 Demo：不连接真实平台，不使用正式数据库。'
} elseif (-not $WithPublisher) {
    Write-Host '当前只启动生产工作台；真实发布请使用 .\scripts\start.ps1 -WithPublisher。'
}

if (-not $NoBrowser) {
    Start-Process $url
}

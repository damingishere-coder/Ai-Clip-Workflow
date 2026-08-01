param(
    [int]$BridgePort = 8765,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $ProjectRoot

Write-Host '此兼容脚本现在会启动 v1.5 Windows Chrome 发布 Worker。'
& (Join-Path $PSScriptRoot 'start_publish_worker.ps1') -Port $BridgePort

Write-Host '正在刷新 Docker 服务：http://127.0.0.1:8001'
docker compose down --remove-orphans
if ($LASTEXITCODE -ne 0) {
    throw 'docker compose down 执行失败，请确认 Docker Desktop 已启动。'
}
docker compose up -d --build
if ($LASTEXITCODE -ne 0) {
    throw 'docker compose up 执行失败，请查看上方 Docker 错误。'
}

Start-Sleep -Seconds 3
try {
    Invoke-WebRequest -Uri 'http://127.0.0.1:8001/health' -UseBasicParsing -TimeoutSec 8 | Out-Null
    Write-Host '牛马片场 Docker 页面已启动：http://127.0.0.1:8001'
} catch {
    Write-Host 'Docker 已启动但页面还在初始化，请稍等 5 秒后刷新。'
}

if (-not $NoBrowser) {
    Start-Process 'http://127.0.0.1:8001/publish'
}

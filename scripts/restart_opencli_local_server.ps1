param(
    [int]$Port = 8001,
    [switch]$NoBrowser
)

Write-Host "现在统一使用 Docker 主页面 http://127.0.0.1:8001。"
Write-Host "这个兼容脚本会改为启动 Windows opencli 辅助服务，并刷新 Docker。"

$arguments = @()
if ($NoBrowser) {
    $arguments += "-NoBrowser"
}

& (Join-Path $PSScriptRoot "start_docker_opencli.ps1") @arguments

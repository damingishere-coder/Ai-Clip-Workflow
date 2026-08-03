[CmdletBinding()]
param(
    [switch]$Demo,
    [switch]$RemoveDemoData
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
Set-Location $ProjectRoot

$arguments = @('compose')
if ($Demo) {
    $arguments += @('-f', 'docker-compose.yml', '-f', 'docker-compose.demo.yml')
}
$arguments += 'down'

$previousErrorActionPreference = $ErrorActionPreference
try {
    # Windows PowerShell 5.1 会把 Docker Compose 的正常进度 stderr 当作 ErrorRecord。
    # 只使用 Docker 的真实退出码判断停止是否成功。
    $ErrorActionPreference = 'Continue'
    & docker @arguments
    $dockerExitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previousErrorActionPreference
}
if ($dockerExitCode -ne 0) {
    throw '停止 Docker 服务失败。'
}

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

Write-Host '牛马片场服务已停止。正式数据库与视频目录未被删除。'

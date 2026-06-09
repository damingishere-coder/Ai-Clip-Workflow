param(
    [int]$Port = 8002,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $ProjectRoot

if ($env:APPDATA) {
    $npmDir = Join-Path $env:APPDATA "npm"
    if (Test-Path $npmDir) {
        $env:PATH = "$npmDir;$env:PATH"
    }
}

$opencli = Get-Command opencli -ErrorAction SilentlyContinue
if (-not $opencli) {
    Write-Host "没有检测到 opencli。请先确认已安装，并在 PowerShell 里执行 where opencli 能看到路径。"
    exit 1
}

Write-Host "已检测到 opencli：$($opencli.Source)"

$connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
$processIds = @($connections | Select-Object -ExpandProperty OwningProcess -Unique)
foreach ($processId in $processIds) {
    if (-not $processId -or $processId -eq $PID) {
        continue
    }
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($process) {
        Write-Host "正在停止占用端口 $Port 的旧后台：PID $processId ($($process.ProcessName))"
        Stop-Process -Id $processId -Force
    }
}

$python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

$outLog = Join-Path $ProjectRoot "opencli_local_server_$Port.out.log"
$errLog = Join-Path $ProjectRoot "opencli_local_server_$Port.err.log"
$arguments = @("-m", "uvicorn", "app.main:app", "--reload", "--host", "127.0.0.1", "--port", "$Port")

Write-Host "正在启动 Windows 本地后台：http://127.0.0.1:$Port"
Start-Process `
    -FilePath $python `
    -ArgumentList $arguments `
    -WorkingDirectory $ProjectRoot `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog `
    -WindowStyle Hidden

Start-Sleep -Seconds 3

$healthUrl = "http://127.0.0.1:$Port/health"
try {
    Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 5 | Out-Null
    Write-Host "后台已启动，健康检查通过：$healthUrl"
} catch {
    Write-Host "后台已启动，但健康检查暂时没有通过。请稍等 5 秒后刷新页面。"
    Write-Host "日志位置：$outLog"
    Write-Host "错误日志：$errLog"
}

if (-not $NoBrowser) {
    Start-Process "http://127.0.0.1:$Port/publish"
}

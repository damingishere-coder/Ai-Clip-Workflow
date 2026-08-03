[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BackupPath,
    [switch]$ConfirmRestore,
    [switch]$RestoreEnv,
    [string]$MediaDestination = '',
    [switch]$StopServices
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
Set-Location $ProjectRoot

function Resolve-PythonCommand {
    $venvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    if (Test-Path $venvPython) {
        return $venvPython
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return $python.Source
    }
    throw '未找到 Python。请安装 Python 3.12，或先创建 .venv。'
}

function Test-AppRunning {
    try {
        $response = Invoke-RestMethod -Uri 'http://127.0.0.1:8001/health' -TimeoutSec 2
        return $response.status -eq 'ok'
    } catch {
        return $false
    }
}

function Stop-DockerServicesSafely {
    Write-Host '正在停止正式与 Demo Docker 服务……'
    & docker compose down 2>$null
    & docker compose -f docker-compose.yml -f docker-compose.demo.yml down 2>$null
    Start-Sleep -Seconds 2
}

if (-not [System.IO.Path]::IsPathRooted($BackupPath)) {
    $BackupPath = Join-Path $ProjectRoot $BackupPath
}
$BackupPath = [System.IO.Path]::GetFullPath($BackupPath)
if (-not (Test-Path -LiteralPath $BackupPath -PathType Leaf)) {
    throw "未找到备份包：$BackupPath"
}

if ($MediaDestination) {
    if (-not [System.IO.Path]::IsPathRooted($MediaDestination)) {
        $MediaDestination = Join-Path $ProjectRoot $MediaDestination
    }
    $MediaDestination = [System.IO.Path]::GetFullPath($MediaDestination)
}

$python = Resolve-PythonCommand
Write-Host '正在验证备份包……'
$verifyOutput = & $python -m scripts.backup_restore_runtime verify $BackupPath 2>&1
if ($LASTEXITCODE -ne 0) {
    throw ($verifyOutput -join [Environment]::NewLine)
}
$manifest = ($verifyOutput | Select-Object -Last 1) | ConvertFrom-Json

Write-Host ''
Write-Host '=== 恢复计划 ==='
Write-Host "备份包：$BackupPath"
Write-Host "备份时间：$($manifest.created_at)"
Write-Host "应用版本：$($manifest.app_version)"
Write-Host "任务：$($manifest.table_counts.tasks)"
Write-Host "候选片段：$($manifest.table_counts.clip_candidates)"
Write-Host "输出片段：$($manifest.table_counts.output_clip)"
Write-Host "发布任务：$($manifest.table_counts.publish_jobs)"
Write-Host "恢复 .env：$RestoreEnv"
Write-Host "媒体恢复目录：$(if ($MediaDestination) { $MediaDestination } else { '不恢复媒体' })"
Write-Host ''
Write-Host '恢复前会自动为当前数据库和 .env 创建 pre-restore 回滚包。'

if (-not $ConfirmRestore) {
    throw '为防止误操作，请确认计划后加上 -ConfirmRestore 再执行。'
}

if (Test-AppRunning) {
    if (-not $StopServices) {
        throw '检测到牛马片场仍在运行。请先停止服务，或添加 -StopServices。'
    }
    Stop-DockerServicesSafely
    if (Test-AppRunning) {
        throw '服务仍在运行，可能是本地 Python 进程。请关闭该进程后重新恢复。'
    }
}

$arguments = @(
    '-m', 'scripts.backup_restore_runtime', 'restore',
    $BackupPath,
    '--backup-dir', (Join-Path $ProjectRoot 'backups')
)
if ($RestoreEnv) {
    $arguments += '--restore-env'
}
if ($MediaDestination) {
    $arguments += @('--media-destination', $MediaDestination)
}

Write-Host '正在创建回滚点并恢复数据……'
$output = & $python @arguments 2>&1
if ($LASTEXITCODE -ne 0) {
    throw ($output -join [Environment]::NewLine)
}
$result = ($output | Select-Object -Last 1) | ConvertFrom-Json

Write-Host ''
Write-Host '=== 恢复完成 ==='
Write-Host "数据库：$($result.database)"
Write-Host "恢复 .env：$($result.environment_restored)"
Write-Host "恢复媒体文件：$($result.media_files_restored)"
if ($result.rollback_archive) {
    Write-Host "恢复前回滚包：$($result.rollback_archive)"
}
Write-Host ''
Write-Host '下一步运行：'
Write-Host '  .\scripts\doctor.ps1'
Write-Host '  .\scripts\start.ps1'

if ($RestoreEnv) {
    Write-Warning '已恢复旧 .env。请核对 API Key、Token、路径和 Worker 配置是否适合当前电脑。'
}

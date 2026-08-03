[CmdletBinding()]
param(
    [string]$OutputDirectory = '',
    [string]$Label = 'manual',
    [switch]$IncludeMedia,
    [switch]$ExcludeEnv
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

if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $ProjectRoot 'backups'
} elseif (-not [System.IO.Path]::IsPathRooted($OutputDirectory)) {
    $OutputDirectory = Join-Path $ProjectRoot $OutputDirectory
}
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)

$python = Resolve-PythonCommand
$arguments = @(
    '-m', 'scripts.backup_restore', 'backup',
    '--output-dir', $OutputDirectory,
    '--label', $Label
)
if ($IncludeMedia) {
    $arguments += '--include-media'
}
if ($ExcludeEnv) {
    $arguments += '--exclude-env'
}

Write-Host '正在检查数据库完整性并创建备份……'
$output = & $python @arguments 2>&1
if ($LASTEXITCODE -ne 0) {
    throw ($output -join [Environment]::NewLine)
}

$jsonLine = ($output | Select-Object -Last 1)
try {
    $result = $jsonLine | ConvertFrom-Json
} catch {
    throw "备份已经执行，但无法解析结果：$jsonLine"
}

Write-Host ''
Write-Host '=== 备份完成 ==='
Write-Host "备份包：$($result.archive)"
Write-Host "任务：$($result.table_counts.tasks)"
Write-Host "候选片段：$($result.table_counts.clip_candidates)"
Write-Host "输出片段：$($result.table_counts.output_clip)"
Write-Host "发布任务：$($result.table_counts.publish_jobs)"
Write-Host "包含媒体：$($result.includes_media)"

if ($result.contains_secrets) {
    Write-Warning '该备份包包含 .env，可能包含 API Key 和 Token。请只保存在可信的本地磁盘或加密存储中。'
} else {
    Write-Host '[OK] 该备份包不包含 .env。'
}

Write-Host ''
Write-Host '验证命令：'
Write-Host "  $python -m scripts.backup_restore verify `"$($result.archive)`""

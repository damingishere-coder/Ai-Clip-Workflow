[CmdletBinding()]
param(
    [string]$OutputDirectory = '',
    [switch]$IncludeMedia,
    [switch]$ExcludeEnv
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
Set-Location $ProjectRoot

Write-Host '=== 牛马片场升级前保护 ==='
Write-Host '此脚本只创建备份，不会自动执行 git pull 或修改代码。'
Write-Host ''

$arguments = @('-Label', 'pre-upgrade')
if ($OutputDirectory) {
    $arguments += @('-OutputDirectory', $OutputDirectory)
}
if ($IncludeMedia) {
    $arguments += '-IncludeMedia'
}
if ($ExcludeEnv) {
    $arguments += '-ExcludeEnv'
}

& (Join-Path $PSScriptRoot 'backup.ps1') @arguments
if ($LASTEXITCODE -ne 0) {
    throw '升级前备份失败，已中止。'
}

$git = Get-Command git -ErrorAction SilentlyContinue
if ($git) {
    $commit = (& git rev-parse --short HEAD 2>$null)
    if ($LASTEXITCODE -eq 0 -and $commit) {
        Write-Host "当前代码提交：$commit"
    }
    $changes = (& git status --porcelain 2>$null)
    if ($changes) {
        Write-Warning '当前仓库存在未提交修改。升级前请先提交、暂存或确认这些文件不会被覆盖。'
    } else {
        Write-Host '[OK] 当前 Git 工作区没有未提交修改。'
    }
}

Write-Host ''
Write-Host '备份完成后，再根据需要执行：'
Write-Host '  git pull --ff-only'
Write-Host '  .\scripts\doctor.ps1'
Write-Host '  .\scripts\acceptance.ps1'

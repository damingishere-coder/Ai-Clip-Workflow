[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
Set-Location $ProjectRoot

$EnvExample = Join-Path $ProjectRoot '.env.example'
$EnvFile = Join-Path $ProjectRoot '.env'

function New-RandomHexToken {
    param([int]$ByteCount = 32)

    $bytes = New-Object byte[] $ByteCount
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    } finally {
        $rng.Dispose()
    }
    return ($bytes | ForEach-Object { $_.ToString('x2') }) -join ''
}

function Get-EnvValue {
    param(
        [string]$Text,
        [string]$Name
    )

    $match = [regex]::Match($Text, "(?m)^$([regex]::Escape($Name))=(.*)$")
    if (-not $match.Success) {
        return ''
    }
    return $match.Groups[1].Value.Trim().Trim('"').Trim("'")
}

function Set-EnvValue {
    param(
        [string]$Text,
        [string]$Name,
        [string]$Value
    )

    $escapedName = [regex]::Escape($Name)
    if ([regex]::IsMatch($Text, "(?m)^$escapedName=.*$")) {
        return [regex]::Replace($Text, "(?m)^$escapedName=.*$", "$Name=$Value")
    }
    return ($Text.TrimEnd() + "`r`n$Name=$Value`r`n")
}

if (-not (Test-Path $EnvExample)) {
    throw '缺少 .env.example，无法初始化项目配置。'
}

if (-not (Test-Path $EnvFile)) {
    Copy-Item -LiteralPath $EnvExample -Destination $EnvFile
    Write-Host '[OK] 已创建本地 .env。'
} elseif ($Force) {
    $backup = "$EnvFile.$([DateTime]::Now.ToString('yyyyMMdd-HHmmss')).bak"
    Copy-Item -LiteralPath $EnvFile -Destination $backup
    Copy-Item -LiteralPath $EnvExample -Destination $EnvFile -Force
    Write-Host "[OK] 已重置 .env，原文件备份为：$backup"
} else {
    Write-Host '[OK] 已保留现有 .env。'
}

$envText = Get-Content -LiteralPath $EnvFile -Raw -Encoding UTF8
$adminToken = Get-EnvValue -Text $envText -Name 'LOCAL_ADMIN_TOKEN'
if (-not $adminToken -or $adminToken -eq 'change-me-to-a-random-string') {
    $envText = Set-EnvValue -Text $envText -Name 'LOCAL_ADMIN_TOKEN' -Value (New-RandomHexToken)
    Write-Host '[OK] 已生成 LOCAL_ADMIN_TOKEN。'
}

$workerToken = Get-EnvValue -Text $envText -Name 'PUBLISH_WORKER_TOKEN'
if (-not $workerToken) {
    $envText = Set-EnvValue -Text $envText -Name 'PUBLISH_WORKER_TOKEN' -Value (New-RandomHexToken)
    Write-Host '[OK] 已生成 PUBLISH_WORKER_TOKEN。'
}

$storageValue = Get-EnvValue -Text $envText -Name 'NIUMA_STORAGE_PATH'
if (-not $storageValue) {
    $storageValue = './workspace/tasks'
    $envText = Set-EnvValue -Text $envText -Name 'NIUMA_STORAGE_PATH' -Value $storageValue
}

Set-Content -LiteralPath $EnvFile -Value $envText -Encoding UTF8

$storagePath = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $storageValue))
if ([System.IO.Path]::IsPathRooted($storageValue)) {
    $storagePath = [System.IO.Path]::GetFullPath($storageValue)
}

$directories = @(
    (Join-Path $ProjectRoot 'data'),
    $storagePath,
    (Join-Path $storagePath '_临时上传'),
    (Join-Path $storagePath '_发布包'),
    (Join-Path $ProjectRoot 'demo-data'),
    (Join-Path $ProjectRoot 'workspace\demo')
)

foreach ($directory in $directories) {
    if (-not (Test-Path $directory)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
        Write-Host "[OK] 已创建目录：$directory"
    }
}

Write-Host ''
Write-Host '初始化完成。下一步运行：'
Write-Host '  .\scripts\doctor.ps1'
Write-Host '  .\scripts\start.ps1'
Write-Host ''
Write-Host '查看演示数据：'
Write-Host '  .\scripts\start.ps1 -Demo'

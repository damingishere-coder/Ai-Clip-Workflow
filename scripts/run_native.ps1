[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8001,
    [string]$StorageRoot = 'E:\直播间切片工作流存储',
    [string]$DataDir = '',
    [string]$DatabasePath = '',
    [ValidateRange(1, 65535)]
    [int]$WorkerPort = 8765
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path.TrimEnd('\')
Set-Location -LiteralPath $ProjectRoot

function Resolve-NativePath {
    param(
        [AllowEmptyString()]
        [string]$Value,
        [Parameter(Mandatory = $true)]
        [string]$Fallback
    )

    $candidate = if ([string]::IsNullOrWhiteSpace($Value)) { $Fallback } else { $Value }
    if ([System.IO.Path]::IsPathRooted($candidate)) {
        return [System.IO.Path]::GetFullPath($candidate)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $candidate))
}

$StorageRootPath = Resolve-NativePath -Value $StorageRoot -Fallback 'E:\直播间切片工作流存储'
$DataDirPath = Resolve-NativePath -Value $DataDir -Fallback (Join-Path $ProjectRoot 'data')
$DatabasePathPath = if ([string]::IsNullOrWhiteSpace($DatabasePath)) {
    Join-Path $DataDirPath 'workflow.sqlite3'
} else {
    Resolve-NativePath -Value $DatabasePath -Fallback (Join-Path $DataDirPath 'workflow.sqlite3')
}
$UploadTempDir = Join-Path $StorageRootPath '_临时上传'
$PublishExportDir = Join-Path $StorageRootPath '_发布包'
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "未找到项目虚拟环境 Python：$Python。请先运行项目安装步骤。"
}

foreach ($directory in @($DataDirPath, $StorageRootPath, $UploadTempDir, $PublishExportDir)) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}
$databaseParent = Split-Path -Parent $DatabasePathPath
if ($databaseParent) {
    New-Item -ItemType Directory -Path $databaseParent -Force | Out-Null
}

$nativeEnvironment = [ordered]@{
    DATA_DIR = $DataDirPath
    DATABASE_PATH = $DatabasePathPath
    STORAGE_ROOT = $StorageRootPath
    TASKS_DIR = $StorageRootPath
    UPLOAD_TEMP_DIR = $UploadTempDir
    PUBLISH_SCHEDULER_EXPORT_DIR = $PublishExportDir
    PUBLISH_HOST_PROJECT_ROOT = $ProjectRoot
    PUBLISH_WORKER_URL = "http://127.0.0.1:$WorkerPort"
    OPENCLI_HOST_BRIDGE_URL = "http://127.0.0.1:$WorkerPort"
    OPENCLI_LOCAL_BASE_URL = "http://127.0.0.1:$Port"
}

$previousEnvironment = @{}
try {
    foreach ($name in $nativeEnvironment.Keys) {
        $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        Set-Item -Path ("Env:{0}" -f $name) -Value ([string]$nativeEnvironment[$name])
    }

    & $Python -m uvicorn app.main:app --host 127.0.0.1 --port ([string]$Port)
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
} finally {
    foreach ($name in $nativeEnvironment.Keys) {
        $oldValue = $previousEnvironment[$name]
        if ($null -eq $oldValue) {
            Remove-Item -Path ("Env:{0}" -f $name) -ErrorAction SilentlyContinue
        } else {
            Set-Item -Path ("Env:{0}" -f $name) -Value ([string]$oldValue)
        }
    }
}

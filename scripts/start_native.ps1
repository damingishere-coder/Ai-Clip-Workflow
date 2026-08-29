[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8001,
    [string]$StorageRoot = 'E:\直播间切片工作流存储',
    [string]$DataDir = '',
    [string]$DatabasePath = '',
    [switch]$SkipWorker,
    [switch]$NoBrowser,
    [ValidateRange(1, 65535)]
    [int]$WorkerPort = 8765
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path.TrimEnd('\')
Set-Location -LiteralPath $ProjectRoot
. (Join-Path $PSScriptRoot 'native_environment.ps1')

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

function Stop-VerifiedProcessTree {
    param([Parameter(Mandatory = $true)][int]$ProcessId)

    $taskKill = Get-Command taskkill.exe -ErrorAction SilentlyContinue
    if ($taskKill) {
        & $taskKill.Source /PID $ProcessId /T /F | Out-Null
        if ($LASTEXITCODE -ne 0 -and (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) {
            throw "无法停止本机服务进程树（PID：$ProcessId）。"
        }
    } else {
        Stop-Process -Id $ProcessId -Force -ErrorAction Stop
    }
}

function Test-PortListener {
    param([Parameter(Mandatory = $true)][int]$PortNumber)
    return @(
        Get-NetTCPConnection -LocalPort $PortNumber -State Listen -ErrorAction SilentlyContinue
    )
}

$RuntimeRoot = Join-Path $ProjectRoot 'data\runtime'
$StatePath = Join-Path $RuntimeRoot 'native-server.json'
$RunnerPath = Join-Path $ProjectRoot 'scripts\run_native.ps1'
$WorkerScript = Join-Path $ProjectRoot 'scripts\publish_host_worker.py'
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $RunnerPath -PathType Leaf)) {
    throw "未找到前台本机启动器：$RunnerPath。"
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "未找到项目虚拟环境 Python：$Python。请先运行项目安装步骤。"
}

$listeners = @(Test-PortListener -PortNumber $Port)
if ($listeners.Count -gt 0) {
    $pids = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
    throw "端口 $Port 已被占用（PID：$($pids -join ', ')），为避免误杀其他程序已停止启动。"
}

if (Test-Path -LiteralPath $StatePath -PathType Leaf) {
    $staleState = $null
    try {
        $staleState = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
    } catch {
        $staleState = $null
    }
    if ($staleState -and $staleState.pid) {
        $existing = Get-Process -Id ([int]$staleState.pid) -ErrorAction SilentlyContinue
        if ($existing -and ([string]$staleState.project_root).TrimEnd('\') -ieq $ProjectRoot) {
            throw "检测到本项目已有本机服务进程（PID：$($staleState.pid)），请先运行 stop_native.ps1。"
        }
    }
    Remove-Item -LiteralPath $StatePath -Force -ErrorAction SilentlyContinue
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
foreach ($directory in @($RuntimeRoot, $DataDirPath, $StorageRootPath, $UploadTempDir, $PublishExportDir)) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}
$databaseParent = Split-Path -Parent $DatabasePathPath
if ($databaseParent) {
    New-Item -ItemType Directory -Path $databaseParent -Force | Out-Null
}

if (-not $SkipWorker) {
    if (-not (Test-Path -LiteralPath $WorkerScript -PathType Leaf)) {
        throw "未找到发布 Worker：$WorkerScript。"
    }
    try {
        & (Join-Path $PSScriptRoot 'start_publish_worker.ps1') -Port $WorkerPort -SkipDockerSync
        if (-not $?) {
            throw '启动脚本返回失败状态。'
        }
    } catch {
        throw "Windows 发布 Worker 启动失败，本机服务未启动：$($_.Exception.Message)"
    }
}

$NativeNoProxy = Merge-NativeNoProxy -ExistingValues @(
    [Environment]::GetEnvironmentVariable('NO_PROXY', 'Process'),
    [Environment]::GetEnvironmentVariable('no_proxy', 'Process')
)
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
    NO_PROXY = $NativeNoProxy
}

$shellCommand = Get-Command powershell.exe -ErrorAction SilentlyContinue
if (-not $shellCommand) {
    $shellCommand = Get-Command pwsh.exe -ErrorAction SilentlyContinue
}
if (-not $shellCommand) {
    throw '未找到 PowerShell 5.1 或 PowerShell 7，无法启动前台 runner。'
}
$shellPath = if ($shellCommand.Source) { $shellCommand.Source } else { $shellCommand.Path }
$quote = { param([string]$Value) '"' + $Value + '"' }
$runnerArguments = @(
    '-NoLogo',
    '-NoProfile',
    '-NonInteractive',
    '-ExecutionPolicy',
    'Bypass',
    '-File',
    (& $quote $RunnerPath),
    '-Port',
    ([string]$Port),
    '-StorageRoot',
    (& $quote $StorageRootPath),
    '-DataDir',
    (& $quote $DataDirPath),
    '-DatabasePath',
    (& $quote $DatabasePathPath),
    '-WorkerPort',
    ([string]$WorkerPort)
)
$commandLine = (($shellPath, $runnerArguments) -join ' ').Trim()

$previousEnvironment = @{}
$serverProcess = $null
try {
    foreach ($name in $nativeEnvironment.Keys) {
        $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        Set-Item -Path ("Env:{0}" -f $name) -Value ([string]$nativeEnvironment[$name])
    }
    $serverProcess = Start-Process -FilePath $shellPath -ArgumentList $runnerArguments -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru
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

if (-not $serverProcess -or $serverProcess.HasExited) {
    throw '本机服务进程未能启动。'
}

$serverProcess.Refresh()
$startTimeUtc = $serverProcess.StartTime.ToUniversalTime()
$state = [ordered]@{
    pid = [int]$serverProcess.Id
    start_time_utc = $startTimeUtc.ToString('o')
    project_root = $ProjectRoot
    port = [int]$Port
    command_line = $commandLine
}
[System.IO.File]::WriteAllText(
    $StatePath,
    ($state | ConvertTo-Json -Depth 3),
    (New-Object System.Text.UTF8Encoding($false))
)

$healthUrl = "http://127.0.0.1:$Port/health"
$healthReady = $false
$healthDeadline = [DateTime]::UtcNow.AddSeconds(30)
while ([DateTime]::UtcNow -lt $healthDeadline) {
    $serverProcess.Refresh()
    if ($serverProcess.HasExited) {
        break
    }
    try {
        $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
        if ($health.status -eq 'ok') {
            $healthReady = $true
            break
        }
    } catch {
        # 应用初始化或数据库准备期间继续等待，最多 30 秒。
    }
    Start-Sleep -Milliseconds 500
}

if (-not $healthReady) {
    $failureReason = if ($serverProcess.HasExited) {
        "进程提前退出（PID：$($serverProcess.Id)，退出码：$($serverProcess.ExitCode)）"
    } else {
        "健康检查在 30 秒内未通过：$healthUrl"
    }
    try {
        $serverProcess.Refresh()
        if (-not $serverProcess.HasExited) {
            Stop-VerifiedProcessTree -ProcessId ([int]$serverProcess.Id)
        }
    } finally {
        Remove-Item -LiteralPath $StatePath -Force -ErrorAction SilentlyContinue
    }
    throw "本机服务启动失败：$failureReason。已清理本次启动的进程和状态。"
}

if (-not $NoBrowser) {
    Start-Process ("http://127.0.0.1:$Port")
}

Write-Host ("本机服务已启动：http://127.0.0.1:{0}（PID：{1}）" -f $Port, $serverProcess.Id)
if ($SkipWorker) {
    Write-Host '已按 -SkipWorker 跳过 Windows 发布 Worker。'
}

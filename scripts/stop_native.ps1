[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8001,
    [switch]$SkipWorker,
    [ValidateRange(1, 65535)]
    [int]$WorkerPort = 8765
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path.TrimEnd('\')
Set-Location -LiteralPath $ProjectRoot

function Test-CommandLinePath {
    param(
        [string]$CommandLine,
        [string]$Path
    )
    return (-not [string]::IsNullOrWhiteSpace($CommandLine)) -and
        ($CommandLine.Replace('/', '\').IndexOf($Path.Replace('/', '\'), [System.StringComparison]::OrdinalIgnoreCase) -ge 0)
}

function Test-CommandLinePort {
    param(
        [string]$CommandLine,
        [int]$PortNumber
    )
    foreach ($token in @(
        ("-Port $PortNumber"),
        ("-Port `"$PortNumber`""),
        ("--port $PortNumber"),
        ("--port `"$PortNumber`"")
    )) {
        if ($CommandLine.IndexOf($token, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
            return $true
        }
    }
    return $false
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

$RuntimeRoot = Join-Path $ProjectRoot 'data\runtime'
$StatePath = Join-Path $RuntimeRoot 'native-server.json'
$RunnerPath = Join-Path $ProjectRoot 'scripts\run_native.ps1'
$WorkerScript = Join-Path $ProjectRoot 'scripts\publish_host_worker.py'

$state = $null
if (Test-Path -LiteralPath $StatePath -PathType Leaf) {
    try {
        $state = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
    } catch {
        Write-Warning '本机服务状态文件无法解析，未停止任何服务进程。'
    }
}

if ($state -and $state.pid) {
    $statePid = [int]$state.pid
    $stateRootMatches = ([string]$state.project_root).TrimEnd('\') -ieq $ProjectRoot
    $statePortMatches = ([int]$state.port) -eq $Port
    $processInfo = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $statePid) -ErrorAction SilentlyContinue
    $process = Get-Process -Id $statePid -ErrorAction SilentlyContinue
    $commandLine = if ($processInfo) { [string]$processInfo.CommandLine } else { '' }
    $commandMatches = (Test-CommandLinePath -CommandLine $commandLine -Path $ProjectRoot) -and
        (Test-CommandLinePath -CommandLine $commandLine -Path $RunnerPath) -and
        (Test-CommandLinePort -CommandLine $commandLine -PortNumber $Port)
    $startTimeMatches = $false
    if ($process -and $state.start_time_utc) {
        try {
            $rawStartTime = $state.start_time_utc
            if ($rawStartTime -is [DateTime]) {
                $expectedStart = $rawStartTime.ToUniversalTime()
            } elseif ($rawStartTime -is [DateTimeOffset]) {
                $expectedStart = $rawStartTime.UtcDateTime
            } else {
                $expectedStart = [DateTime]::ParseExact(
                    [string]$rawStartTime,
                    'o',
                    [Globalization.CultureInfo]::InvariantCulture,
                    [Globalization.DateTimeStyles]::RoundtripKind
                ).ToUniversalTime()
            }
            $actualStart = $process.StartTime.ToUniversalTime()
            $startTimeMatches = ([Math]::Abs(($actualStart - $expectedStart).TotalSeconds) -le 5)
        } catch {
            $startTimeMatches = $false
        }
    }

    if ($process -and $stateRootMatches -and $statePortMatches -and $commandMatches -and $startTimeMatches) {
        Stop-VerifiedProcessTree -ProcessId $statePid
        Remove-Item -LiteralPath $StatePath -Force -ErrorAction SilentlyContinue
        Write-Host ("本机服务已停止（PID：{0}，端口：{1}）。" -f $statePid, $Port)
    } elseif ($process) {
        Write-Warning '状态中的 PID、项目根目录、端口、启动时间或命令行不匹配，未停止任何本机服务进程。'
    } else {
        Remove-Item -LiteralPath $StatePath -Force -ErrorAction SilentlyContinue
        Write-Host '本机服务进程已退出，已清理过期状态。'
    }
} else {
    Write-Host '没有检测到本项目本机服务状态，未停止任何服务进程。'
}

if (-not $SkipWorker) {
    $workerProcesses = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $line = [string]$_.CommandLine
        (Test-CommandLinePath -CommandLine $line -Path $ProjectRoot) -and
            (Test-CommandLinePath -CommandLine $line -Path $WorkerScript) -and
            (Test-CommandLinePort -CommandLine $line -PortNumber $WorkerPort)
    })
    foreach ($worker in $workerProcesses) {
        if ($worker.ProcessId -and $worker.ProcessId -ne $PID) {
            Stop-Process -Id ([int]$worker.ProcessId) -Force -ErrorAction SilentlyContinue
            Write-Host ("本项目 Windows 发布 Worker 已停止（PID：{0}，端口：{1}）。" -f $worker.ProcessId, $WorkerPort)
        }
    }
    if ($workerProcesses.Count -eq 0) {
        Write-Host '没有检测到命令行明确属于本项目的 Windows 发布 Worker。'
    }
}

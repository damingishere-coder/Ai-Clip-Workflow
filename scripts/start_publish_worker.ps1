param(
    [int]$Port = 8765,
    [switch]$SkipDockerSync,
    [switch]$Restart
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $ProjectRoot

$envFile = Join-Path $ProjectRoot ".env"
if (-not (Test-Path $envFile)) {
    New-Item -ItemType File -Path $envFile | Out-Null
}

$envText = Get-Content -LiteralPath $envFile -Raw -ErrorAction SilentlyContinue
$tokenMatch = [regex]::Match($envText, '(?m)^PUBLISH_WORKER_TOKEN=(.+)$')
if ($tokenMatch.Success -and $tokenMatch.Groups[1].Value.Trim()) {
    $workerToken = $tokenMatch.Groups[1].Value.Trim()
} else {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($bytes)
    $rng.Dispose()
    $workerToken = ($bytes | ForEach-Object { $_.ToString('x2') }) -join ''
    if ([regex]::IsMatch($envText, '(?m)^PUBLISH_WORKER_TOKEN=.*$')) {
        $envText = [regex]::Replace($envText, '(?m)^PUBLISH_WORKER_TOKEN=.*$', "PUBLISH_WORKER_TOKEN=$workerToken")
        Set-Content -LiteralPath $envFile -Value $envText -Encoding UTF8
    } else {
        Add-Content -LiteralPath $envFile -Value "`r`nPUBLISH_WORKER_TOKEN=$workerToken" -Encoding UTF8
    }
    Write-Host '已在本地 .env 中生成发布 Worker Token（不会提交到 Git）。'
}
$env:PUBLISH_WORKER_TOKEN = $workerToken

$chromeCandidates = @()
foreach ($basePath in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA)) {
    if ($basePath) {
        $candidate = Join-Path $basePath 'Google\Chrome\Application\chrome.exe'
        if (Test-Path $candidate) {
            $chromeCandidates += $candidate
        }
    }
}
if (-not $chromeCandidates) {
    throw '没有检测到 Google Chrome。请先安装 Chrome，再启动真实发布 Worker。'
}

$listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
$listenerProcessIds = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
$reuseWorker = $false
foreach ($processId in $listenerProcessIds) {
    $commandLine = (Get-CimInstance Win32_Process -Filter "ProcessId=$processId" -ErrorAction SilentlyContinue).CommandLine
    if ($commandLine -match 'publish_host_worker|opencli_host_bridge') {
        if ($Restart) {
            Write-Host ("正在重启本项目的发布 Worker：PID {0}" -f $processId)
            Stop-Process -Id $processId -Force
            continue
        }
        try {
            $existingHealth = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
            $headers = @{ Authorization = "Bearer $workerToken" }
            Invoke-RestMethod -Uri "http://127.0.0.1:$Port/v1/executions/niuma-health-check" -Headers $headers -TimeoutSec 2 | Out-Null
            if ($existingHealth.status -eq 'ok' -and $existingHealth.worker -eq 'windows_chrome') {
                $reuseWorker = $true
                Write-Host ("检测到健康的发布 Worker，直接复用：PID {0}" -f $processId)
            }
        } catch {
            Write-Host ("旧发布 Worker 无法通过健康或 Token 校验，正在安全重启：PID {0}" -f $processId)
            Stop-Process -Id $processId -Force
        }
    } else {
        throw "端口 $Port 已被其他程序占用（PID $processId），为避免误关程序已停止启动。"
    }
}

# Stop-Process 返回时，Windows 可能仍需极短时间才能真正释放监听端口。
# 必须确认端口已经空闲后再启动新进程，否则重复运行脚本时会偶发 WinError 10048。
if ($listenerProcessIds -and -not $reuseWorker) {
    $releaseDeadline = [DateTime]::UtcNow.AddSeconds(10)
    do {
        $remainingListeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
        if (-not $remainingListeners) {
            break
        }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $releaseDeadline)

    if ($remainingListeners) {
        $remainingProcessIds = @($remainingListeners | Select-Object -ExpandProperty OwningProcess -Unique)
        throw "旧发布 Worker 已停止，但端口 $Port 在 10 秒内仍未释放（PID：$($remainingProcessIds -join ', ')）。请稍后重新运行脚本。"
    }
}

$python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    $python = 'python'
}
$workerScript = Join-Path $ProjectRoot 'scripts\publish_host_worker.py'
$outLog = Join-Path $ProjectRoot "publish_worker_$Port.out.log"
$errLog = Join-Path $ProjectRoot "publish_worker_$Port.err.log"
$arguments = @("`"$workerScript`"", '--host', '127.0.0.1', '--port', "$Port")

if (-not $reuseWorker) {
Write-Host ("正在启动 Windows Chrome 发布 Worker：http://127.0.0.1:{0}" -f $Port)
$workerProcess = Start-Process `
    -FilePath $python `
    -ArgumentList $arguments `
    -WorkingDirectory $ProjectRoot `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog `
    -WindowStyle Hidden `
    -PassThru

$health = $null
foreach ($attempt in 1..20) {
    Start-Sleep -Milliseconds 500
    if ($workerProcess.HasExited) {
        break
    }
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
        if ($health.status -eq 'ok') {
            break
        }
        $health = $null
    } catch {
        # Worker 或端口仍在初始化，继续等待，最多等待约 10 秒。
    }
}

if ($health -and $health.status -eq 'ok' -and -not $workerProcess.HasExited) {
    Write-Host '发布 Worker 已启动。登录账号时会打开牛马片场专属 Chrome 窗口。'
} else {
    throw "发布 Worker 未能启动。请查看日志：$errLog"
}
}

# Docker 容器只会在创建时读取 .env。这里自动重建正在运行的 Web 容器，
# 让刚生成的 Worker Token 和当前 compose 连接地址立即生效；SQLite 与任务目录均为挂载卷，不会被删除。
$docker = Get-Command docker -ErrorAction SilentlyContinue
if ($docker -and -not $SkipDockerSync) {
    try {
        $runningServices = @(docker compose ps --services --status running 2>$null)
        if ($runningServices -contains 'workflow') {
            Write-Host '正在同步 Docker 与 Windows Worker 的连接配置……'
            docker compose up -d --force-recreate --no-deps workflow | Out-Host
            if ($LASTEXITCODE -ne 0) {
                throw 'Docker 容器重建失败。'
            }

            $connected = $false
            foreach ($attempt in 1..20) {
                Start-Sleep -Seconds 1
                try {
                    $appHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:8001/api/publish/scheduler/health' -TimeoutSec 3
                    if ($appHealth.worker_available) {
                        $connected = $true
                        break
                    }
                } catch {
                    # Web 容器仍在启动，继续等待。
                }
            }
            if (-not $connected) {
                throw 'Docker 已重启，但发送中心仍未连接 Worker。请查看 Docker 日志和 Worker 错误日志。'
            }
            Write-Host 'Docker 已同步完成，发送中心现在可以连接 Windows Worker。'
        }
    } catch {
        throw "Worker 已启动，但 Docker 同步失败：$($_.Exception.Message)"
    }
}

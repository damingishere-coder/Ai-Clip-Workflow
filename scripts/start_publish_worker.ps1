param(
    [int]$Port = 8765
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
foreach ($listener in $listeners) {
    $processId = $listener.OwningProcess
    $commandLine = (Get-CimInstance Win32_Process -Filter "ProcessId=$processId" -ErrorAction SilentlyContinue).CommandLine
    if ($commandLine -match 'publish_host_worker|opencli_host_bridge') {
        Write-Host ("正在停止旧发布 Worker：PID {0}" -f $processId)
        Stop-Process -Id $processId -Force
    } else {
        throw "端口 $Port 已被其他程序占用（PID $processId），为避免误关程序已停止启动。"
    }
}

$python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    $python = 'python'
}
$workerScript = Join-Path $ProjectRoot 'scripts\publish_host_worker.py'
$outLog = Join-Path $ProjectRoot "publish_worker_$Port.out.log"
$errLog = Join-Path $ProjectRoot "publish_worker_$Port.err.log"
$arguments = @("`"$workerScript`"", '--host', '0.0.0.0', '--port', "$Port")

Write-Host ("正在启动 Windows Chrome 发布 Worker：http://127.0.0.1:{0}" -f $Port)
Start-Process `
    -FilePath $python `
    -ArgumentList $arguments `
    -WorkingDirectory $ProjectRoot `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog `
    -WindowStyle Hidden

Start-Sleep -Seconds 2
try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 5
    if ($health.status -ne 'ok') {
        throw '健康检查返回异常状态。'
    }
    Write-Host '发布 Worker 已启动。登录账号时会打开牛马片场专属 Chrome 窗口。'
} catch {
    throw "发布 Worker 未能启动。请查看日志：$errLog"
}

# Docker 容器只会在创建时读取 .env。这里自动重建正在运行的 Web 容器，
# 让刚生成的 Worker Token 和当前 compose 连接地址立即生效；SQLite 与任务目录均为挂载卷，不会被删除。
$docker = Get-Command docker -ErrorAction SilentlyContinue
if ($docker) {
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

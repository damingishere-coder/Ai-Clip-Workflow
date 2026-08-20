[CmdletBinding()]
param(
    [switch]$Demo,
    [switch]$ResetDemo,
    [switch]$Development,
    [switch]$WithPublisher,
    [switch]$NoBuild,
    [switch]$NoBrowser,
    [switch]$SkipWorker,
    [int]$WorkerPort = 8765
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
Set-Location $ProjectRoot

# Demo 和开发模式自动跳过 Worker，不需要用户额外加 -SkipWorker
if ($WithPublisher) {
    Write-Host '[提示] -WithPublisher 现在是默认行为，无需额外指定。使用 -SkipWorker 可跳过 Worker 启动。'
}

if (-not (Test-Path (Join-Path $ProjectRoot '.env'))) {
    Write-Host '首次运行，正在初始化本地配置……'
    & (Join-Path $PSScriptRoot 'setup.ps1')
    if ($LASTEXITCODE -ne 0) {
        throw '初始化失败。'
    }
}

& (Join-Path $PSScriptRoot 'doctor.ps1')
if ($LASTEXITCODE -ne 0) {
    throw '环境检查存在阻塞项，已停止启动。'
}

# ============================================================
#  第 1 步：启动 Windows Chrome 发布 Worker（默认启用）
# ============================================================
$workerStarted = $false
$workerSkipReason = ''
if ($Demo) {
    $workerSkipReason = 'demo'
    Write-Host 'Demo 模式：使用手动导出，不启动 Windows Worker。'
} elseif ($Development) {
    $workerSkipReason = 'development'
    Write-Host '开发模式：不启动 Windows Worker。'
} elseif ($SkipWorker) {
    $workerSkipReason = 'skip_worker'
    Write-Host '已按 -SkipWorker 跳过 Windows Worker 启动。'
} else {
    Write-Host ''
    Write-Host '========================================'
    Write-Host '  第 1 步：启动 Windows 发布 Worker'
    Write-Host '========================================'

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
        $workerSkipReason = 'chrome_missing'
        Write-Host '[WARN] 没有检测到 Google Chrome，跳过 Windows Worker 启动。'
        Write-Host '       没有 Worker 就无法真实发布到抖音/B站，但项目工作台仍可正常使用。'
        Write-Host '       安装 Chrome 后重新运行本脚本即可自动启动 Worker。'
    } else {
        try {
            & (Join-Path $PSScriptRoot 'start_publish_worker.ps1') -Port $WorkerPort -SkipDockerSync
            $workerStarted = $true
        } catch {
            $workerSkipReason = 'worker_failed'
            Write-Host "[WARN] Windows Worker 启动失败：$($_.Exception.Message)"
            Write-Host '       项目工作台仍可正常使用，但发布功能将不可用。'
            Write-Host '       请排查后重新运行本脚本。'
        }
    }
}

# ============================================================
#  第 2 步：启动 Docker 服务
# ============================================================
Write-Host ''
Write-Host '========================================'
if ($workerStarted) {
    Write-Host '  第 2 步：启动 Docker 服务'
} else {
    Write-Host '  启动 Docker 服务'
}
Write-Host '========================================'

$composeArguments = @('compose')
if ($Demo) {
    $composeArguments += @('-f', 'docker-compose.yml', '-f', 'docker-compose.demo.yml')
} elseif ($Development) {
    $composeArguments += @('-f', 'docker-compose.yml', '-f', 'docker-compose.dev.yml')
}
$composeArguments += @('up', '-d')
if (-not $NoBuild) {
    $composeArguments += '--build'
}

Write-Host '正在启动牛马片场……'
$previousErrorActionPreference = $ErrorActionPreference
try {
    $ErrorActionPreference = 'Continue'
    & docker @composeArguments
    $composeExitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previousErrorActionPreference
}
if ($composeExitCode -ne 0) {
    throw 'Docker 启动失败。现有数据库与视频文件没有被删除。'
}

# ============================================================
#  第 3 步：等待服务就绪
# ============================================================
Write-Host ''
Write-Host '等待服务就绪……'

$appReady = $false
foreach ($attempt in 1..90) {
    Start-Sleep -Seconds 1
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8001/health' -TimeoutSec 3
        if ($health.status -eq 'ok') {
            $appReady = $true
            break
        }
    } catch {
        # 首次构建和数据库初始化期间继续等待。
    }
}
if (-not $appReady) {
    throw '服务已经启动，但 90 秒内没有通过健康检查。请运行 docker compose logs workflow。'
}

# ============================================================
#  第 4 步：验证 Docker 与 Worker 连接
# ============================================================
if ($workerStarted) {
    Write-Host ''
    Write-Host '正在验证 Docker 与 Windows Worker 的连接……'

    # 先尝试直接检查连接（Worker 先启动，Docker 后启动，token 应已正确加载）
    $connected = $false
    foreach ($attempt in 1..15) {
        Start-Sleep -Seconds 1
        try {
            $publisher = Invoke-RestMethod -Uri 'http://127.0.0.1:8001/api/publish/scheduler/health' -TimeoutSec 3
            if ($publisher.worker_available) {
                $connected = $true
                break
            }
        } catch {
            # 继续等待
        }
    }

    # 如果仍未连接，可能是 Docker 先于 Worker 启动导致 token 缺失
    # 这时需要重建容器让它重新读取 .env
    if (-not $connected) {
        Write-Host '首次连接未成功，正在同步容器配置……'

        try {
            $ErrorActionPreference = 'Continue'
            docker compose up -d --force-recreate --no-deps workflow 2>&1 | Out-Null
            $syncExitCode = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = 'Stop'
        }

        if ($syncExitCode -ne 0) {
            Write-Host '[WARN] Docker 容器同步失败。'
        } else {
            # 等待容器恢复 + Worker 连接
            foreach ($attempt in 1..30) {
                Start-Sleep -Seconds 1
                try {
                    $publisher = Invoke-RestMethod -Uri 'http://127.0.0.1:8001/api/publish/scheduler/health' -TimeoutSec 3
                    if ($publisher.worker_available) {
                        $connected = $true
                        break
                    }
                } catch {
                    # 继续等待
                }
            }
        }
    }

    if ($connected) {
        Write-Host 'Windows Worker 已成功连接到 Docker 发送中心。'
    } else {
        Write-Host '[WARN] Docker 已启动，但发送中心仍未连接到 Worker。'
        Write-Host '       可能原因：'
        Write-Host '         1. Windows 防火墙阻止了 Docker 访问端口 8765'
        Write-Host '         2. Worker Token 不一致'
        Write-Host '       请运行 .\scripts\stop.ps1 后重新 .\scripts\start.ps1。'
    }
}

# ============================================================
#  第 5 步：Demo 数据初始化
# ============================================================
if ($Demo) {
    Write-Host '正在检查隔离 Demo 数据……'
    $seedArguments = @(
        'compose', '-f', 'docker-compose.yml', '-f', 'docker-compose.demo.yml',
        'exec', '-T', 'workflow', 'python', '-m', 'scripts.seed_demo_data'
    )
    if ($ResetDemo) {
        $seedArguments += '--reset'
    }
    try {
        $ErrorActionPreference = 'Continue'
        & docker @seedArguments
        $seedExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($seedExitCode -ne 0) {
        throw 'Demo 数据初始化失败，请查看容器日志。'
    }
}

# ============================================================
#  完成
# ============================================================
$url = 'http://127.0.0.1:8001'
Write-Host ''
Write-Host '========================================'
Write-Host "  启动成功：$url"
if ($Demo) {
    Write-Host '  当前为隔离 Demo：不连接真实平台，不使用正式数据库。'
} elseif ($Development) {
    Write-Host '  当前为开发模式：已跳过 Windows Worker，只启动工作台。'
} elseif ($SkipWorker) {
    Write-Host '  已按 -SkipWorker 跳过 Windows Worker，只启动工作台。'
} elseif ($workerStarted) {
    Write-Host '  Windows Worker 已启动，可以正常发布到抖音/B站。'
    Write-Host "  Worker 管理端口：http://127.0.0.1:$WorkerPort"
} else {
    if ($workerSkipReason -eq 'chrome_missing') {
        Write-Host '  当前只启动了工作台：未检测到 Google Chrome，真实发布需要安装 Chrome 后重新运行。'
    } else {
        Write-Host '  当前只启动了工作台：Windows Worker 未启动，真实发布暂不可用。'
    }
}
Write-Host '========================================'

if (-not $NoBrowser) {
    Start-Process $url
}

[CmdletBinding()]
param(
    [switch]$KeepRunning,
    [switch]$NoBuild
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
Set-Location $ProjectRoot

function Assert-Page {
    param(
        [string]$Name,
        [string]$Url
    )

    $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 15
    if ($response.StatusCode -ne 200) {
        throw "$Name 返回异常状态码：$($response.StatusCode)"
    }
    Write-Host "[OK] $Name：$Url"
}

$startedDemo = $false
try {
    Write-Host '=== 牛马片场 Windows 验收 ==='
    Write-Host '本流程只使用隔离 Demo，不连接真实平台账号。'

    & (Join-Path $PSScriptRoot 'setup.ps1')
    if ($LASTEXITCODE -ne 0) {
        throw '项目初始化失败。'
    }

    & (Join-Path $PSScriptRoot 'doctor.ps1')
    if ($LASTEXITCODE -ne 0) {
        throw '环境体检存在阻塞项。'
    }

    $startArguments = @('-Demo', '-ResetDemo', '-NoBrowser')
    if ($NoBuild) {
        $startArguments += '-NoBuild'
    }
    & (Join-Path $PSScriptRoot 'start.ps1') @startArguments
    if ($LASTEXITCODE -ne 0) {
        throw '隔离 Demo 启动失败。'
    }
    $startedDemo = $true

    Assert-Page -Name '健康检查' -Url 'http://127.0.0.1:8001/health'
    Assert-Page -Name '工作台' -Url 'http://127.0.0.1:8001/'
    Assert-Page -Name '任务列表' -Url 'http://127.0.0.1:8001/tasks'
    Assert-Page -Name '片段总览' -Url 'http://127.0.0.1:8001/clips'
    Assert-Page -Name '发送中心' -Url 'http://127.0.0.1:8001/publish'

    $containerHealth = docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' niuma-studio-demo
    if ($LASTEXITCODE -ne 0 -or $containerHealth.Trim() -ne 'healthy') {
        throw "Demo 容器未达到 healthy 状态：$containerHealth"
    }
    Write-Host '[OK] Docker 容器健康状态：healthy'

    $verifyCommand = @'
import sqlite3

db = sqlite3.connect('/app/data/workflow.sqlite3')
counts = {
    'tasks': db.execute("select count(*) from tasks where id like 'demo_%'").fetchone()[0],
    'clips': db.execute("select count(*) from clip_candidates where id like 'demo_%'").fetchone()[0],
    'publish_jobs': db.execute("select count(*) from publish_jobs where id like 'demo_%'").fetchone()[0],
}
assert counts == {'tasks': 3, 'clips': 6, 'publish_jobs': 6}, counts
print(f"tasks={counts['tasks']}, clips={counts['clips']}, publish_jobs={counts['publish_jobs']}")
'@
    $demoCounts = docker exec niuma-studio-demo python -c $verifyCommand
    if ($LASTEXITCODE -ne 0) {
        throw 'Demo 数据数量验证失败。'
    }
    Write-Host "[OK] Demo 数据：$demoCounts"

    Write-Host ''
    Write-Host '=== 验收通过 ==='
    Write-Host '下一步可以打开 http://127.0.0.1:8001 检查页面显示。'
    Write-Host '真实发布仍需单独运行 .\scripts\start.ps1 -WithPublisher 进行低风险验证。'
} catch {
    Write-Host ''
    Write-Host "[FAIL] $($_.Exception.Message)" -ForegroundColor Red
    if ($startedDemo) {
        Write-Host '最近的 Demo 容器日志：'
        docker logs --tail 100 niuma-studio-demo 2>$null
    }
    exit 1
} finally {
    if ($startedDemo -and -not $KeepRunning) {
        try {
            & (Join-Path $PSScriptRoot 'stop.ps1') -Demo
        } catch {
            Write-Warning "验收结束后自动停止 Demo 失败：$($_.Exception.Message)"
        }
    }
}

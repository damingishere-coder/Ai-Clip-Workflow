[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $ProjectRoot

$Python = (Get-Command python -ErrorAction Stop).Source
$TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("niuma-windows-ci-" + [guid]::NewGuid().ToString('N'))
$DataDir = Join-Path $TempRoot 'data'
$TasksDir = Join-Path $TempRoot 'tasks'
$BackupDir = Join-Path $TempRoot 'backups'
$ArtifactDir = Join-Path $ProjectRoot 'acceptance-results\windows-ci'
$ServerStdout = Join-Path $ArtifactDir 'uvicorn.stdout.log'
$ServerStderr = Join-Path $ArtifactDir 'uvicorn.stderr.log'
$ServerProcess = $null

New-Item -ItemType Directory -Path $TempRoot -Force | Out-Null
New-Item -ItemType Directory -Path $ArtifactDir -Force | Out-Null

function Assert-Page {
    param(
        [string]$Name,
        [string]$Url
    )

    $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 15
    if ($response.StatusCode -ne 200) {
        throw "$Name 返回异常状态码：$($response.StatusCode)"
    }
    Write-Host "[OK] $Name：HTTP 200"
}

try {
    Write-Host '=== Windows host smoke test ==='
    Write-Host '该测试不提供 Docker Desktop 实机证据，只验证 Windows 主机行为。'

    $setupFirst = @(& (Join-Path $PSScriptRoot 'setup.ps1') 2>&1)
    $setupFirst | Set-Content -LiteralPath (Join-Path $ArtifactDir 'setup-first.log') -Encoding UTF8
    if ($LASTEXITCODE -ne 0) {
        throw '首次 setup.ps1 失败。'
    }
    $envPath = Join-Path $ProjectRoot '.env'
    $firstHash = (Get-FileHash -LiteralPath $envPath -Algorithm SHA256).Hash

    $setupSecond = @(& (Join-Path $PSScriptRoot 'setup.ps1') 2>&1)
    $setupSecond | Set-Content -LiteralPath (Join-Path $ArtifactDir 'setup-second.log') -Encoding UTF8
    if ($LASTEXITCODE -ne 0) {
        throw '重复 setup.ps1 失败。'
    }
    $secondHash = (Get-FileHash -LiteralPath $envPath -Algorithm SHA256).Hash
    if ($firstHash -ne $secondHash) {
        throw 'setup.ps1 重复执行修改了 .env。'
    }
    Write-Host '[OK] setup.ps1 幂等且保留 .env。'

    $doctorOutput = @(& (Join-Path $PSScriptRoot 'doctor.ps1') -SkipDocker 2>&1)
    $doctorOutput | Set-Content -LiteralPath (Join-Path $ArtifactDir 'doctor.log') -Encoding UTF8
    if ($LASTEXITCODE -ne 0) {
        throw 'Windows 主机 doctor.ps1 检查失败。'
    }
    Write-Host '[OK] doctor.ps1 Windows 主机检查通过。'

    $env:DEMO_MODE = 'true'
    $env:DATA_DIR = $DataDir
    $env:DATABASE_PATH = Join-Path $DataDir 'workflow.sqlite3'
    $env:STORAGE_ROOT = $TasksDir
    $env:TASKS_DIR = $TasksDir
    $env:UPLOAD_TEMP_DIR = Join-Path $TasksDir '_temp'
    $env:PUBLISH_SCHEDULER_ENABLED = 'false'
    $env:PUBLISH_DEFAULT_MODE = 'manual_export'
    $env:LOCAL_ADMIN_TOKEN = 'windows-ci-local-only'

    & $Python -m scripts.seed_demo_data --reset
    if ($LASTEXITCODE -ne 0) {
        throw 'Windows 原生 Demo 建库失败。'
    }

    $ServerProcess = Start-Process `
        -FilePath $Python `
        -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8001') `
        -PassThru `
        -NoNewWindow `
        -RedirectStandardOutput $ServerStdout `
        -RedirectStandardError $ServerStderr

    $ready = $false
    foreach ($attempt in 1..60) {
        Start-Sleep -Seconds 2
        if ($ServerProcess.HasExited) {
            break
        }
        try {
            $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8001/health' -TimeoutSec 3
            if ($health.status -eq 'ok') {
                $ready = $true
                break
            }
        } catch {
            # 应用启动期间继续等待。
        }
    }
    if (-not $ready) {
        throw 'Windows 原生应用未在 120 秒内通过健康检查。'
    }

    Assert-Page '工作台' 'http://127.0.0.1:8001/'
    Assert-Page '任务列表' 'http://127.0.0.1:8001/tasks'
    Assert-Page '片段总览' 'http://127.0.0.1:8001/clips'
    Assert-Page '发送中心' 'http://127.0.0.1:8001/publish'

    $verifyScript = Join-Path $TempRoot 'verify_counts.py'
    @'
import json
import os
import sqlite3

connection = sqlite3.connect(os.environ["DATABASE_PATH"])
counts = {
    "tasks": connection.execute("select count(*) from tasks where id like 'demo_%'").fetchone()[0],
    "clips": connection.execute("select count(*) from clip_candidates where id like 'demo_%'").fetchone()[0],
    "publish_jobs": connection.execute("select count(*) from publish_jobs where id like 'demo_%'").fetchone()[0],
}
assert counts == {"tasks": 3, "clips": 6, "publish_jobs": 6}, counts
print(json.dumps(counts))
'@ | Set-Content -LiteralPath $verifyScript -Encoding UTF8

    $countsJson = & $Python $verifyScript
    if ($LASTEXITCODE -ne 0) {
        throw 'Windows 原生 Demo 数据数量验证失败。'
    }
    $counts = $countsJson | ConvertFrom-Json
    Write-Host '[OK] Demo 数据数量正确。'

    & (Join-Path $PSScriptRoot 'backup.ps1') -OutputDirectory $BackupDir -Label windows-ci -ExcludeEnv
    if ($LASTEXITCODE -ne 0) {
        throw 'Windows 主机备份命令失败。'
    }
    $archive = Get-ChildItem -LiteralPath $BackupDir -Filter 'niuma-studio-windows-ci-*.zip' | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
    if (-not $archive) {
        throw 'Windows 主机备份未生成 ZIP。'
    }
    & $Python -m scripts.backup_restore verify $archive.FullName
    if ($LASTEXITCODE -ne 0) {
        throw 'Windows 主机备份验证失败。'
    }
    Write-Host '[OK] Windows 主机备份与校验通过。'

    $result = [ordered]@{
        result = 'passed'
        os = [System.Environment]::OSVersion.VersionString
        powershell = $PSVersionTable.PSVersion.ToString()
        python = (& $Python --version 2>&1 | Select-Object -First 1)
        tasks = $counts.tasks
        clips = $counts.clips
        publish_jobs = $counts.publish_jobs
        docker_desktop_validated = $false
        note = 'Windows hosted runner smoke test; not a Windows 10/11 + Docker Desktop acceptance report.'
    }
    $result | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $ArtifactDir 'result.json') -Encoding UTF8
    Write-Host '=== Windows host smoke test passed ==='
} finally {
    if ($ServerProcess -and -not $ServerProcess.HasExited) {
        Stop-Process -Id $ServerProcess.Id -Force -ErrorAction SilentlyContinue
        $ServerProcess.WaitForExit(10000) | Out-Null
    }
    if (Test-Path -LiteralPath $TempRoot) {
        Remove-Item -LiteralPath $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

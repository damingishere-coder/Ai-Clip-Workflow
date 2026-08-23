[CmdletBinding()]
param(
    [string]$AcceptanceReport = ''
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $ProjectRoot

if (-not $AcceptanceReport) {
    $AcceptanceReport = Join-Path $ProjectRoot 'acceptance-results\latest.json'
} elseif (-not [System.IO.Path]::IsPathRooted($AcceptanceReport)) {
    $AcceptanceReport = Join-Path $ProjectRoot $AcceptanceReport
}
$AcceptanceReport = [System.IO.Path]::GetFullPath($AcceptanceReport)

function Assert-Gate {
    param(
        [bool]$Condition,
        [string]$Name,
        [string]$FailureMessage
    )

    if (-not $Condition) {
        throw "[$Name] $FailureMessage"
    }
    Write-Host "[PASS] $Name"
}

if (-not (Test-Path -LiteralPath $AcceptanceReport -PathType Leaf)) {
    throw "未找到验收报告：$AcceptanceReport。请先在 Windows 10/11 + Docker Desktop 上运行 .\scripts\acceptance.ps1。"
}

$report = Get-Content -LiteralPath $AcceptanceReport -Raw -Encoding UTF8 | ConvertFrom-Json
Assert-Gate ($report.result -eq 'passed') '验收结果' '最近一次验收没有通过。'
Assert-Gate ($report.environment.os_caption -match 'Windows (10|11)') 'Windows 实机' "验收系统不是 Windows 10/11：$($report.environment.os_caption)"
Assert-Gate (-not [string]::IsNullOrWhiteSpace([string]$report.environment.docker_desktop)) 'Docker Desktop 版本' '验收报告没有记录 Docker Desktop 版本。'
Assert-Gate (-not [string]::IsNullOrWhiteSpace([string]$report.environment.docker_engine)) 'Docker Engine 版本' '验收报告没有记录 Docker Engine 版本。'
Assert-Gate (-not [string]::IsNullOrWhiteSpace([string]$report.environment.docker_compose)) 'Docker Compose 版本' '验收报告没有记录 Docker Compose 版本。'

Assert-Gate ($report.demo_counts.tasks -eq 3) 'Demo 任务数量' '应为 3 条。'
Assert-Gate ($report.demo_counts.clips -eq 6) 'Demo 候选片段数量' '应为 6 条。'
Assert-Gate ($report.demo_counts.publish_jobs -eq 6) 'Demo 发布草稿数量' '应为 6 条。'

$requiredChecks = @(
    'setup 保留已有 .env',
    'setup 幂等性',
    '环境体检',
    '隔离 Demo 启动',
    '健康检查',
    '工作台',
    '任务列表',
    '片段总览',
    '发送中心',
    'Docker 容器健康状态',
    'Demo 数据数量',
    '停止隔离 Demo',
    '正式 .env 保护',
    '正式数据库保护',
    '正式任务目录保护'
)
foreach ($name in $requiredChecks) {
    $check = $report.checks | Where-Object { $_.name -eq $name } | Select-Object -First 1
    Assert-Gate ($null -ne $check -and $check.status -eq 'PASS') $name '验收报告中缺少 PASS 证据。'
}

$currentBranch = (& git rev-parse --abbrev-ref HEAD 2>$null).Trim()
Assert-Gate ($LASTEXITCODE -eq 0 -and $currentBranch -eq 'master') '发布分支' "当前分支是 $currentBranch，应切换到 master。"

$currentCommit = (& git rev-parse HEAD 2>$null).Trim()
Assert-Gate ($LASTEXITCODE -eq 0 -and $currentCommit -eq $report.environment.git_commit) '验收提交一致' '验收报告对应的提交不是当前 master。请重新验收。'

$gitStatus = (& git status --porcelain 2>$null) -join "`n"
Assert-Gate ($LASTEXITCODE -eq 0 -and [string]::IsNullOrWhiteSpace($gitStatus)) '工作区干净' '存在未提交或未跟踪文件。'

$mainText = Get-Content -LiteralPath (Join-Path $ProjectRoot 'app\main.py') -Raw -Encoding UTF8
$readmeText = Get-Content -LiteralPath (Join-Path $ProjectRoot 'README.md') -Raw -Encoding UTF8
$englishReadmeText = Get-Content -LiteralPath (Join-Path $ProjectRoot 'README.en.md') -Raw -Encoding UTF8
$changelogText = Get-Content -LiteralPath (Join-Path $ProjectRoot 'CHANGELOG.md') -Raw -Encoding UTF8

Assert-Gate ($mainText -match 'version="2\.1\.0"') '应用版本' 'app/main.py 不是 2.1.0。'
Assert-Gate ($readmeText -match 'version-2\.1\.0') '中文 README 版本' '版本徽章不是 2.1.0。'
Assert-Gate ($englishReadmeText -match 'version-2\.1\.0') '英文 README 版本' '版本徽章不是 2.1.0。'
Assert-Gate ($changelogText -match '(?m)^## 2\.1\.0 - ') 'Changelog' '缺少 2.1.0 正式记录。'

Write-Host ''
Write-Host '=== v2.1.0 发布门禁通过 ===' -ForegroundColor Green
Write-Host '下一步：核对 docs/RELEASE_CHECKLIST.md，然后创建 tag v2.1.0 和 GitHub Release。'

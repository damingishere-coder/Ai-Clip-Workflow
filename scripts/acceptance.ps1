[CmdletBinding()]
param(
    [switch]$KeepRunning,
    [switch]$NoBuild,
    [string]$ReportDirectory = '',
    [switch]$SkipStorageSnapshot
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $ProjectRoot

if (-not $ReportDirectory) {
    $ReportDirectory = Join-Path $ProjectRoot 'acceptance-results'
} elseif (-not [System.IO.Path]::IsPathRooted($ReportDirectory)) {
    $ReportDirectory = Join-Path $ProjectRoot $ReportDirectory
}
$ReportDirectory = [System.IO.Path]::GetFullPath($ReportDirectory)
$RunStartedAt = [DateTimeOffset]::Now
$RunId = $RunStartedAt.ToString('yyyyMMdd-HHmmss')
$RunDirectory = Join-Path $ReportDirectory "windows-$RunId"
New-Item -ItemType Directory -Path $RunDirectory -Force | Out-Null

$script:Checks = New-Object 'System.Collections.Generic.List[object]'
$script:DemoCounts = $null
$script:ProductionBefore = $null
$script:ProductionAfter = $null
$script:EnvironmentInfo = $null
$script:StartedDemo = $false
$script:Result = 'failed'
$script:FailureMessage = ''

function Protect-Text {
    param([object]$Value)

    $text = [string]$Value
    if ($ProjectRoot) {
        $text = $text.Replace($ProjectRoot, '<PROJECT_ROOT>')
    }
    if ($env:USERPROFILE) {
        $text = $text.Replace($env:USERPROFILE, '<USER_PROFILE>')
    }
    return $text
}

function Add-Check {
    param(
        [ValidateSet('PASS', 'WARN', 'FAIL')]
        [string]$Status,
        [string]$Name,
        [string]$Message
    )

    $safeMessage = Protect-Text $Message
    $script:Checks.Add([pscustomobject]@{
        status = $Status
        name = $Name
        message = $safeMessage
    }) | Out-Null

    $color = 'Gray'
    if ($Status -eq 'PASS') { $color = 'Green' }
    if ($Status -eq 'WARN') { $color = 'Yellow' }
    if ($Status -eq 'FAIL') { $color = 'Red' }
    Write-Host ("[{0}] {1}：{2}" -f $Status, $Name, $safeMessage) -ForegroundColor $color
}

function Get-StringSha256 {
    param([string]$Text)

    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
        $hash = $sha.ComputeHash($bytes)
        return (($hash | ForEach-Object { $_.ToString('x2') }) -join '')
    } finally {
        $sha.Dispose()
    }
}

function Get-FileState {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return [pscustomobject]@{
            exists = $false
            size = 0
            sha256 = ''
            last_write_utc = ''
        }
    }

    $item = Get-Item -LiteralPath $Path
    return [pscustomobject]@{
        exists = $true
        size = [int64]$item.Length
        sha256 = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
        last_write_utc = $item.LastWriteTimeUtc.ToString('o')
    }
}

function Get-DirectoryState {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        return [pscustomobject]@{
            exists = $false
            file_count = 0
            total_bytes = 0
            metadata_sha256 = ''
        }
    }

    $root = (Resolve-Path -LiteralPath $Path).Path
    $files = @(Get-ChildItem -LiteralPath $root -File -Recurse -Force -ErrorAction Stop | Sort-Object FullName)
    $builder = New-Object System.Text.StringBuilder
    [int64]$totalBytes = 0
    foreach ($file in $files) {
        $relative = $file.FullName.Substring($root.Length)
        while ($relative.StartsWith('\') -or $relative.StartsWith('/')) {
            $relative = $relative.Substring(1)
        }
        $totalBytes += $file.Length
        [void]$builder.Append($relative)
        [void]$builder.Append('|')
        [void]$builder.Append($file.Length)
        [void]$builder.Append('|')
        [void]$builder.Append($file.LastWriteTimeUtc.Ticks)
        [void]$builder.Append("`n")
    }

    return [pscustomobject]@{
        exists = $true
        file_count = $files.Count
        total_bytes = $totalBytes
        metadata_sha256 = Get-StringSha256 $builder.ToString()
    }
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

function Resolve-ConfiguredPath {
    param(
        [string]$Value,
        [string]$DefaultPath
    )

    $candidate = $Value
    if (-not $candidate) {
        $candidate = $DefaultPath
    }
    if ([System.IO.Path]::IsPathRooted($candidate)) {
        return [System.IO.Path]::GetFullPath($candidate)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $candidate))
}

function Get-ConfiguredPaths {
    $envPath = Join-Path $ProjectRoot '.env'
    $envText = Get-Content -LiteralPath $envPath -Raw -Encoding UTF8

    $dataDir = Resolve-ConfiguredPath -Value (Get-EnvValue $envText 'DATA_DIR') -DefaultPath 'data'
    $databaseValue = Get-EnvValue $envText 'DATABASE_PATH'
    if ($databaseValue) {
        $databasePath = Resolve-ConfiguredPath -Value $databaseValue -DefaultPath ''
    } else {
        $databasePath = Join-Path $dataDir 'workflow.sqlite3'
    }

    $tasksValue = Get-EnvValue $envText 'TASKS_DIR'
    if (-not $tasksValue) { $tasksValue = Get-EnvValue $envText 'STORAGE_ROOT' }
    if (-not $tasksValue) { $tasksValue = Get-EnvValue $envText 'NIUMA_STORAGE_PATH' }
    $tasksPath = Resolve-ConfiguredPath -Value $tasksValue -DefaultPath 'E:\直播间切片工作流存储'

    return [pscustomobject]@{
        env = $envPath
        database = $databasePath
        tasks = $tasksPath
    }
}

function Get-ProductionState {
    param([object]$Paths)

    $storageState = $null
    if ($SkipStorageSnapshot) {
        $storageState = [pscustomobject]@{
            skipped = $true
            exists = $null
            file_count = $null
            total_bytes = $null
            metadata_sha256 = ''
        }
    } else {
        $storageState = Get-DirectoryState $Paths.tasks
    }

    return [pscustomobject]@{
        env = Get-FileState $Paths.env
        database = Get-FileState $Paths.database
        tasks = $storageState
    }
}

function Assert-FileStateEqual {
    param(
        [string]$Name,
        [object]$Before,
        [object]$After
    )

    $same = ($Before.exists -eq $After.exists)
    if ($same -and $Before.exists) {
        $same = ($Before.size -eq $After.size -and $Before.sha256 -eq $After.sha256)
    }
    if (-not $same) {
        throw "$Name 在 Demo 验收前后发生变化。"
    }
    Add-Check PASS $Name '验收前后保持不变。'
}

function Assert-DirectoryStateEqual {
    param(
        [string]$Name,
        [object]$Before,
        [object]$After
    )

    if ($Before.skipped -or $After.skipped) {
        Add-Check WARN $Name '已按 -SkipStorageSnapshot 跳过目录指纹检查。'
        return
    }

    $same = (
        $Before.exists -eq $After.exists -and
        $Before.file_count -eq $After.file_count -and
        $Before.total_bytes -eq $After.total_bytes -and
        $Before.metadata_sha256 -eq $After.metadata_sha256
    )
    if (-not $same) {
        throw "$Name 在 Demo 验收前后发生变化。"
    }
    Add-Check PASS $Name ("验收前后保持不变，共 {0} 个文件。" -f $After.file_count)
}

function Assert-Page {
    param(
        [string]$Name,
        [string]$Url
    )

    $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 15
    if ($response.StatusCode -ne 200) {
        throw "$Name 返回异常状态码：$($response.StatusCode)"
    }
    Add-Check PASS $Name ("HTTP 200，响应 {0} 字节。" -f $response.RawContentLength)
}

function Get-VersionValue {
    param([scriptblock]$Command)

    try {
        $value = & $Command 2>$null
        if ($LASTEXITCODE -ne 0) {
            return ''
        }
        return ([string]($value | Select-Object -First 1)).Trim()
    } catch {
        return ''
    }
}

function Get-EnvironmentInfo {
    $os = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
    $dockerDesktopVersion = ''
    $dockerDesktopPath = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    if (Test-Path $dockerDesktopPath) {
        $dockerDesktopVersion = (Get-Item $dockerDesktopPath).VersionInfo.ProductVersion
    }
    if ([string]::IsNullOrWhiteSpace([string]$dockerDesktopVersion)) {
        $dockerDesktopVersion = Get-VersionValue { docker version --format '{{.Server.Platform.Name}}' }
    }

    $chromeVersion = ''
    foreach ($basePath in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA)) {
        if (-not $basePath) { continue }
        $candidate = Join-Path $basePath 'Google\Chrome\Application\chrome.exe'
        if (Test-Path $candidate) {
            $chromeVersion = (Get-Item $candidate).VersionInfo.ProductVersion
            break
        }
    }

    return [pscustomobject]@{
        os_caption = $(if ($os) { $os.Caption } else { '' })
        os_version = $(if ($os) { $os.Version } else { '' })
        os_build = $(if ($os) { $os.BuildNumber } else { '' })
        powershell = $PSVersionTable.PSVersion.ToString()
        docker_engine = Get-VersionValue { docker version --format '{{.Server.Version}}' }
        docker_compose = Get-VersionValue { docker compose version --short }
        docker_desktop = $dockerDesktopVersion
        chrome = $chromeVersion
        git_commit = Get-VersionValue { git rev-parse HEAD }
    }
}

function Write-Reports {
    $finishedAt = [DateTimeOffset]::Now
    $report = [ordered]@{
        schema_version = 1
        result = $script:Result
        started_at = $RunStartedAt.ToString('o')
        finished_at = $finishedAt.ToString('o')
        environment = $script:EnvironmentInfo
        demo_counts = $script:DemoCounts
        production_before = $script:ProductionBefore
        production_after = $script:ProductionAfter
        checks = $script:Checks.ToArray()
        error = Protect-Text $script:FailureMessage
    }

    $jsonPath = Join-Path $RunDirectory 'report.json'
    $markdownPath = Join-Path $RunDirectory 'report.md'
    $report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $jsonPath -Encoding UTF8

    $lines = New-Object 'System.Collections.Generic.List[string]'
    $lines.Add('# NiuMa Studio Windows 验收报告') | Out-Null
    $lines.Add('') | Out-Null
    $lines.Add("- 结果：**$($script:Result.ToUpperInvariant())**") | Out-Null
    $lines.Add("- 开始时间：$($RunStartedAt.ToString('o'))") | Out-Null
    $lines.Add("- 结束时间：$($finishedAt.ToString('o'))") | Out-Null
    $lines.Add(('- Git commit：`{0}`' -f $script:EnvironmentInfo.git_commit)) | Out-Null
    $lines.Add("- Windows：$($script:EnvironmentInfo.os_caption) $($script:EnvironmentInfo.os_version) (Build $($script:EnvironmentInfo.os_build))") | Out-Null
    $lines.Add("- PowerShell：$($script:EnvironmentInfo.powershell)") | Out-Null
    $lines.Add("- Docker Desktop：$($script:EnvironmentInfo.docker_desktop)") | Out-Null
    $lines.Add("- Docker Engine：$($script:EnvironmentInfo.docker_engine)") | Out-Null
    $lines.Add("- Docker Compose：$($script:EnvironmentInfo.docker_compose)") | Out-Null
    $lines.Add("- Chrome：$($script:EnvironmentInfo.chrome)") | Out-Null
    $lines.Add('') | Out-Null
    $lines.Add('## 检查结果') | Out-Null
    $lines.Add('') | Out-Null
    $lines.Add('| 状态 | 项目 | 结果 |') | Out-Null
    $lines.Add('| --- | --- | --- |') | Out-Null
    foreach ($check in $script:Checks) {
        $message = ([string]$check.message).Replace('|', '\|').Replace("`r", ' ').Replace("`n", ' ')
        $lines.Add("| $($check.status) | $($check.name) | $message |") | Out-Null
    }

    if ($script:DemoCounts) {
        $lines.Add('') | Out-Null
        $lines.Add('## Demo 数据') | Out-Null
        $lines.Add('') | Out-Null
        $lines.Add("- 任务：$($script:DemoCounts.tasks)") | Out-Null
        $lines.Add("- 候选片段：$($script:DemoCounts.clips)") | Out-Null
        $lines.Add("- 发布草稿：$($script:DemoCounts.publish_jobs)") | Out-Null
    }

    if ($script:FailureMessage) {
        $lines.Add('') | Out-Null
        $lines.Add('## 失败原因') | Out-Null
        $lines.Add('') | Out-Null
        $lines.Add((Protect-Text $script:FailureMessage)) | Out-Null
    }

    $lines.Add('') | Out-Null
    $lines.Add('> 本报告不包含 `.env` 内容、API Key、Token、Cookie、浏览器 Profile 或真实视频。上传前仍应人工检查。') | Out-Null
    $lines | Set-Content -LiteralPath $markdownPath -Encoding UTF8

    Copy-Item -LiteralPath $jsonPath -Destination (Join-Path $ReportDirectory 'latest.json') -Force
    Copy-Item -LiteralPath $markdownPath -Destination (Join-Path $ReportDirectory 'latest.md') -Force

    Write-Host ''
    Write-Host "验收报告：$markdownPath"
    Write-Host "机器可读报告：$jsonPath"
}

try {
    Write-Host '=== 牛马片场 Windows 实机验收 ==='
    Write-Host '本流程只使用隔离 Demo，不连接真实平台账号。'
    Write-Host '验收会启动并停止 Demo，以验证正式配置和数据未被修改。'
    Write-Host ''

    $script:EnvironmentInfo = Get-EnvironmentInfo

    $portListener = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue
    if ($portListener) {
        throw '端口 8001 已被占用。请先停止正式服务或其他占用程序，再执行隔离验收。'
    }
    Add-Check PASS '验收端口隔离' '端口 8001 空闲，未复用正式服务。'

    $envFile = Join-Path $ProjectRoot '.env'
    $envExistedBeforeSetup = Test-Path -LiteralPath $envFile -PathType Leaf
    $envStateBeforeSetup = Get-FileState $envFile

    $setupFirstOutput = @(& (Join-Path $PSScriptRoot 'setup.ps1') 2>&1)
    $setupFirstOutput | Set-Content -LiteralPath (Join-Path $RunDirectory 'setup-first.log') -Encoding UTF8
    if ($LASTEXITCODE -ne 0) {
        throw '项目首次初始化失败。'
    }
    $envStateAfterFirstSetup = Get-FileState $envFile
    if ($envExistedBeforeSetup -and $envStateBeforeSetup.sha256 -ne $envStateAfterFirstSetup.sha256) {
        throw 'setup.ps1 修改了已有 .env。'
    }
    Add-Check PASS 'setup 保留已有 .env' $(if ($envExistedBeforeSetup) { '已有配置哈希保持不变。' } else { '首次创建配置成功。' })

    $setupSecondOutput = @(& (Join-Path $PSScriptRoot 'setup.ps1') 2>&1)
    $setupSecondOutput | Set-Content -LiteralPath (Join-Path $RunDirectory 'setup-second.log') -Encoding UTF8
    if ($LASTEXITCODE -ne 0) {
        throw '项目重复初始化失败。'
    }
    $envStateAfterSecondSetup = Get-FileState $envFile
    if ($envStateAfterFirstSetup.sha256 -ne $envStateAfterSecondSetup.sha256) {
        throw 'setup.ps1 重复运行时修改了 .env。'
    }
    Add-Check PASS 'setup 幂等性' '连续运行两次不会重置本地配置。'

    $paths = Get-ConfiguredPaths
    $script:ProductionBefore = Get-ProductionState $paths

    $doctorOutput = @(& (Join-Path $PSScriptRoot 'doctor.ps1') 2>&1)
    $doctorOutput | Set-Content -LiteralPath (Join-Path $RunDirectory 'doctor.log') -Encoding UTF8
    if ($LASTEXITCODE -ne 0) {
        throw '环境体检存在阻塞项。'
    }
    Add-Check PASS '环境体检' 'Docker、存储、端口与 Compose 无阻塞项。'

    $startParameters = @{
        Demo = $true
        ResetDemo = $true
        NoBrowser = $true
        NoBuild = $NoBuild
    }
    $startOutput = @(& (Join-Path $PSScriptRoot 'start.ps1') @startParameters 2>&1)
    $startOutput | Set-Content -LiteralPath (Join-Path $RunDirectory 'start-demo.log') -Encoding UTF8
    if ($LASTEXITCODE -ne 0) {
        throw '隔离 Demo 启动失败。'
    }
    $script:StartedDemo = $true
    Add-Check PASS '隔离 Demo 启动' 'Demo 使用独立数据库和任务目录。'

    Assert-Page -Name '健康检查' -Url 'http://127.0.0.1:8001/health'
    Assert-Page -Name '工作台' -Url 'http://127.0.0.1:8001/'
    Assert-Page -Name '任务列表' -Url 'http://127.0.0.1:8001/tasks'
    Assert-Page -Name '片段总览' -Url 'http://127.0.0.1:8001/clips'
    Assert-Page -Name '发送中心' -Url 'http://127.0.0.1:8001/publish'

    $containerHealth = ''
    foreach ($attempt in 1..45) {
        $containerHealth = docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' niuma-studio-demo
        if ($LASTEXITCODE -ne 0) {
            break
        }
        $containerHealth = $containerHealth.Trim()
        if ($containerHealth -eq 'healthy') {
            break
        }
        if ($containerHealth -in @('exited', 'dead', 'unhealthy')) {
            break
        }
        Start-Sleep -Seconds 2
    }
    if ($LASTEXITCODE -ne 0 -or $containerHealth -ne 'healthy') {
        throw "Demo 容器未达到 healthy 状态：$containerHealth"
    }
    Add-Check PASS 'Docker 容器健康状态' 'healthy'

    $verifyCommand = @'
import json
import sqlite3

db = sqlite3.connect('/app/data/workflow.sqlite3')
counts = {
    'tasks': db.execute("select count(*) from tasks where id like 'demo_%'").fetchone()[0],
    'clips': db.execute("select count(*) from clip_candidates where id like 'demo_%'").fetchone()[0],
    'publish_jobs': db.execute("select count(*) from publish_jobs where id like 'demo_%'").fetchone()[0],
}
assert counts == {'tasks': 3, 'clips': 6, 'publish_jobs': 6}, counts
print(json.dumps(counts))
'@
    $demoCountsJson = $verifyCommand | docker exec -i niuma-studio-demo python -
    if ($LASTEXITCODE -ne 0) {
        throw 'Demo 数据数量验证失败。'
    }
    $script:DemoCounts = $demoCountsJson | ConvertFrom-Json
    Add-Check PASS 'Demo 数据数量' '3 条任务、6 条候选片段、6 条 manual_export 发布草稿。'

    $stopOutput = @(& (Join-Path $PSScriptRoot 'stop.ps1') -Demo 2>&1)
    $stopOutput | Set-Content -LiteralPath (Join-Path $RunDirectory 'stop-demo.log') -Encoding UTF8
    if ($LASTEXITCODE -ne 0) {
        throw '停止 Demo 失败。'
    }
    $script:StartedDemo = $false
    Add-Check PASS '停止隔离 Demo' 'Demo 容器已正常停止。'

    $script:ProductionAfter = Get-ProductionState $paths
    Assert-FileStateEqual '正式 .env 保护' $script:ProductionBefore.env $script:ProductionAfter.env
    Assert-FileStateEqual '正式数据库保护' $script:ProductionBefore.database $script:ProductionAfter.database
    Assert-DirectoryStateEqual '正式任务目录保护' $script:ProductionBefore.tasks $script:ProductionAfter.tasks

    if ($KeepRunning) {
        $restartOutput = @(& (Join-Path $PSScriptRoot 'start.ps1') -Demo -NoBrowser -NoBuild 2>&1)
        $restartOutput | Set-Content -LiteralPath (Join-Path $RunDirectory 'restart-demo.log') -Encoding UTF8
        if ($LASTEXITCODE -ne 0) {
            throw '验收完成后重新启动 Demo 失败。'
        }
        $script:StartedDemo = $true
        Add-Check PASS '保留 Demo 运行' '已在完成停止与数据保护验证后重新启动。'
    }

    $script:Result = 'passed'
    Write-Host ''
    Write-Host '=== 验收通过 ===' -ForegroundColor Green
    if ($KeepRunning) {
        Write-Host 'Demo 已重新启动，可打开 http://127.0.0.1:8001 检查页面。'
    } else {
        Write-Host 'Demo 已停止，正式配置、数据库和任务目录保持不变。'
    }
} catch {
    $script:FailureMessage = $_.Exception.Message
    Add-Check FAIL '验收流程' $script:FailureMessage
    Write-Host ''
    Write-Host "[FAIL] $(Protect-Text $script:FailureMessage)" -ForegroundColor Red
    if ($script:StartedDemo) {
        try {
            docker logs --tail 200 niuma-studio-demo 2>&1 | Set-Content -LiteralPath (Join-Path $RunDirectory 'docker-demo-failure.log') -Encoding UTF8
        } catch {
            # 失败日志属于辅助信息，不能覆盖原始错误。
        }
    }
} finally {
    if ($script:Result -ne 'passed') {
        try {
            & (Join-Path $PSScriptRoot 'stop.ps1') -Demo | Out-Null
            $script:StartedDemo = $false
        } catch {
            Write-Warning "验收失败后自动停止 Demo 失败：$($_.Exception.Message)"
        }
    }
    if (-not $script:EnvironmentInfo) {
        $script:EnvironmentInfo = Get-EnvironmentInfo
    }
    Write-Reports
}

if ($script:Result -ne 'passed') {
    exit 1
}
exit 0

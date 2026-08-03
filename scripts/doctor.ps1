[CmdletBinding()]
param(
    [switch]$RequirePublisher,
    [switch]$SkipDocker
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
Set-Location $ProjectRoot

$script:Failed = 0
$script:Warnings = 0

function Write-Check {
    param(
        [ValidateSet('OK', 'WARN', 'FAIL')]
        [string]$Status,
        [string]$Name,
        [string]$Message
    )

    if ($Status -eq 'FAIL') {
        $script:Failed += 1
    } elseif ($Status -eq 'WARN') {
        $script:Warnings += 1
    }
    Write-Host ("[{0}] {1}：{2}" -f $Status, $Name, $Message)
}

function Get-EnvValue {
    param([string]$Name)

    $envFile = Join-Path $ProjectRoot '.env'
    if (-not (Test-Path $envFile)) {
        return ''
    }
    $text = Get-Content -LiteralPath $envFile -Raw -Encoding UTF8
    $match = [regex]::Match($text, "(?m)^$([regex]::Escape($Name))=(.*)$")
    if (-not $match.Success) {
        return ''
    }
    return $match.Groups[1].Value.Trim().Trim('"').Trim("'")
}

Write-Host 'NiuMa Studio 环境检查'
Write-Host '========================'

if ($PSVersionTable.PSVersion.Major -ge 5) {
    Write-Check OK 'PowerShell' $PSVersionTable.PSVersion.ToString()
} else {
    Write-Check FAIL 'PowerShell' '需要 PowerShell 5.1 或更高版本。'
}

$envFile = Join-Path $ProjectRoot '.env'
if (Test-Path $envFile) {
    Write-Check OK '.env' '本地配置文件存在。'
} else {
    Write-Check FAIL '.env' '尚未初始化，请先运行 .\scripts\setup.ps1。'
}

$storageValue = Get-EnvValue 'TASKS_DIR'
if (-not $storageValue) { $storageValue = Get-EnvValue 'STORAGE_ROOT' }
if (-not $storageValue) { $storageValue = Get-EnvValue 'NIUMA_STORAGE_PATH' }
if (-not $storageValue) { $storageValue = 'E:\直播间切片工作流存储' }
if ([System.IO.Path]::IsPathRooted($storageValue)) {
    $storagePath = [System.IO.Path]::GetFullPath($storageValue)
} else {
    $storagePath = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $storageValue))
}
try {
    New-Item -ItemType Directory -Path $storagePath -Force | Out-Null
    $probe = Join-Path $storagePath '.niuma-write-test'
    Set-Content -LiteralPath $probe -Value 'ok' -Encoding ASCII
    Remove-Item -LiteralPath $probe -Force
    Write-Check OK '视频存储目录' $storagePath
} catch {
    Write-Check FAIL '视频存储目录' $_.Exception.Message
}

if ($SkipDocker) {
    Write-Check WARN 'Docker 主机检查' '已按 -SkipDocker 跳过；仅允许用于 Windows 云端主机冒烟，不能替代 Docker Desktop 实机验收。'
} else {
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $docker) {
        Write-Check FAIL 'Docker CLI' '未找到 docker，请安装并启动 Docker Desktop。'
    } else {
        Write-Check OK 'Docker CLI' $docker.Source
        try {
            $dockerVersion = (& docker version --format '{{.Server.Version}}' 2>$null)
            if ($LASTEXITCODE -eq 0 -and $dockerVersion) {
                Write-Check OK 'Docker Engine' $dockerVersion
            } else {
                Write-Check FAIL 'Docker Engine' 'Docker Desktop 可能尚未启动。'
            }
        } catch {
            Write-Check FAIL 'Docker Engine' '无法连接 Docker Desktop。'
        }

        try {
            $composeVersion = (& docker compose version --short 2>$null)
            if ($LASTEXITCODE -eq 0 -and $composeVersion) {
                Write-Check OK 'Docker Compose' $composeVersion
            } else {
                Write-Check FAIL 'Docker Compose' '当前 Docker 未提供 compose 子命令。'
            }
        } catch {
            Write-Check FAIL 'Docker Compose' '无法执行 docker compose。'
        }

        try {
            & docker compose config --quiet 2>$null
            if ($LASTEXITCODE -eq 0) {
                Write-Check OK 'Compose 配置' 'docker-compose.yml 校验通过。'
            } else {
                Write-Check FAIL 'Compose 配置' '配置校验失败，请运行 docker compose config 查看详情。'
            }
        } catch {
            Write-Check FAIL 'Compose 配置' $_.Exception.Message
        }
    }
}

$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
if ($ffmpeg) {
    Write-Check OK '宿主机 FFmpeg' $ffmpeg.Source
} else {
    Write-Check WARN '宿主机 FFmpeg' '未检测到；Docker 运行方式不受影响，本地 Python 运行需要安装。'
}

$chromeCandidates = @()
foreach ($basePath in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA)) {
    if ($basePath) {
        $candidate = Join-Path $basePath 'Google\Chrome\Application\chrome.exe'
        if (Test-Path $candidate) {
            $chromeCandidates += $candidate
        }
    }
}
if ($chromeCandidates) {
    Write-Check OK 'Google Chrome' $chromeCandidates[0]
} elseif ($RequirePublisher) {
    Write-Check FAIL 'Google Chrome' '真实发布 Worker 需要安装 Chrome。'
} else {
    Write-Check WARN 'Google Chrome' '未检测到；生产和 Demo 工作台可用，但不能启动真实发布 Worker。'
}

try {
    $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8001/health' -TimeoutSec 2
    if ($health.status -eq 'ok') {
        Write-Check OK '端口 8001' '牛马片场已经在运行。'
    } else {
        Write-Check WARN '端口 8001' '有服务响应，但健康状态不是 ok。'
    }
} catch {
    $listener = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        Write-Check FAIL '端口 8001' '已被其他程序占用。'
    } else {
        Write-Check OK '端口 8001' '当前可用。'
    }
}

Write-Host '========================'
if ($script:Failed -gt 0) {
    Write-Host ("检查完成：{0} 项失败，{1} 项提醒。" -f $script:Failed, $script:Warnings)
    exit 1
}
Write-Host ("检查完成：没有阻塞项，{0} 项提醒。" -f $script:Warnings)
exit 0

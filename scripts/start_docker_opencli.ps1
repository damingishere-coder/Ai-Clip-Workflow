param(
    [int]$BridgePort = 8765,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $ProjectRoot

if ($env:APPDATA) {
    $npmDir = Join-Path $env:APPDATA "npm"
    if (Test-Path $npmDir) {
        $env:PATH = "$npmDir;$env:PATH"
    }
}

$opencli = Get-Command opencli -ErrorAction SilentlyContinue
if (-not $opencli) {
    Write-Host 'opencli was not found. Please install opencli and make sure where opencli returns a path.'
    exit 1
}
Write-Host ('opencli found: {0}' -f $opencli.Source)

$bridgeConnections = Get-NetTCPConnection -LocalPort $BridgePort -State Listen -ErrorAction SilentlyContinue
$bridgeProcessIds = @($bridgeConnections | Select-Object -ExpandProperty OwningProcess -Unique)
foreach ($processId in $bridgeProcessIds) {
    if (-not $processId -or $processId -eq $PID) {
        continue
    }
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($process) {
        Write-Host ('Stopping old opencli helper: PID {0} ({1})' -f $processId, $process.ProcessName)
        Stop-Process -Id $processId -Force
    }
}

$python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

$bridgeOutLog = Join-Path $ProjectRoot "opencli_bridge_$BridgePort.out.log"
$bridgeErrLog = Join-Path $ProjectRoot "opencli_bridge_$BridgePort.err.log"
Write-Host ('Starting Windows opencli helper: http://127.0.0.1:{0}' -f $BridgePort)
$bridgeScript = Join-Path $ProjectRoot "scripts\opencli_host_bridge.py"
$bridgeArguments = @("`"$bridgeScript`"", "--host", "0.0.0.0", "--port", "$BridgePort")
Start-Process `
    -FilePath $python `
    -ArgumentList $bridgeArguments `
    -WorkingDirectory $ProjectRoot `
    -RedirectStandardOutput $bridgeOutLog `
    -RedirectStandardError $bridgeErrLog `
    -WindowStyle Hidden

Start-Sleep -Seconds 2
$previousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
$bridgeHealth = Invoke-WebRequest -Uri "http://127.0.0.1:$BridgePort/health" -UseBasicParsing -TimeoutSec 5
$ErrorActionPreference = $previousErrorActionPreference
if ($bridgeHealth) {
    Write-Host 'opencli helper is running.'
} else {
    Write-Host ('opencli helper is not responding yet. Log: {0}' -f $bridgeErrLog)
}

Write-Host 'Cleaning old Docker services that may occupy port 8001.'
docker compose down --remove-orphans
$portContainerIds = @(docker ps --filter 'publish=8001' --format '{{.ID}}')
foreach ($containerId in $portContainerIds) {
    if ($containerId) {
        Write-Host ('Stopping old container on port 8001: {0}' -f $containerId)
        docker rm -f $containerId | Out-Null
    }
}

Write-Host 'Refreshing Docker service: http://127.0.0.1:8001'
docker compose up -d --build

Start-Sleep -Seconds 3
$previousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
$dockerHealth = Invoke-WebRequest -Uri "http://127.0.0.1:8001/health" -UseBasicParsing -TimeoutSec 8
$ErrorActionPreference = $previousErrorActionPreference
if ($dockerHealth) {
    Write-Host 'Docker page is running: http://127.0.0.1:8001'
} else {
    Write-Host 'Docker started, but health check is not ready yet. Wait 5 seconds and refresh http://127.0.0.1:8001'
}

if (-not $NoBrowser) {
    Start-Process 'http://127.0.0.1:8001/publish'
}

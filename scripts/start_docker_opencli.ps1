param(
    [int]$BridgePort = 8765,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $ProjectRoot

$helperScript = Join-Path $ProjectRoot "scripts\start_opencli_host_bridge.ps1"
& $helperScript -BridgePort $BridgePort

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

$dockerBridgeProbe = "import json, urllib.request; print(urllib.request.urlopen('http://host.docker.internal:$BridgePort/health', timeout=5).read().decode('utf-8'))"
$dockerBridgeHealth = docker compose exec -T workflow python -c $dockerBridgeProbe
if ($LASTEXITCODE -eq 0 -and $dockerBridgeHealth -match '"opencli_available": true') {
    Write-Host 'Docker can reach the Windows opencli helper.'
} else {
    Write-Host 'Docker started, but it could not confirm the Windows opencli helper from inside the container.'
    Write-Host ('Please check http://127.0.0.1:{0}/health and then refresh http://127.0.0.1:8001/publish' -f $BridgePort)
}

if (-not $NoBrowser) {
    Start-Process 'http://127.0.0.1:8001/publish'
}

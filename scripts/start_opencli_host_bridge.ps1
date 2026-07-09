param(
    [int]$BridgePort = 8765
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
    Write-Host "opencli was not found. Please install opencli and make sure 'where opencli' returns a path."
    exit 1
}
Write-Host ("opencli found: {0}" -f $opencli.Source)

$bridgeConnections = Get-NetTCPConnection -LocalPort $BridgePort -State Listen -ErrorAction SilentlyContinue
$bridgeProcessIds = @($bridgeConnections | Select-Object -ExpandProperty OwningProcess -Unique)
foreach ($processId in $bridgeProcessIds) {
    if (-not $processId -or $processId -eq $PID) {
        continue
    }
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($process) {
        Write-Host ("Stopping old opencli helper: PID {0} ({1})" -f $processId, $process.ProcessName)
        Stop-Process -Id $processId -Force
    }
}

$python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

$bridgeOutLog = Join-Path $ProjectRoot "opencli_bridge_$BridgePort.out.log"
$bridgeErrLog = Join-Path $ProjectRoot "opencli_bridge_$BridgePort.err.log"
$bridgeScript = Join-Path $ProjectRoot "scripts\opencli_host_bridge.py"
$bridgeArguments = @("`"$bridgeScript`"", "--host", "0.0.0.0", "--port", "$BridgePort")

Write-Host ("Starting Windows opencli helper: http://127.0.0.1:{0}" -f $BridgePort)
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
    Write-Host "opencli helper is running."
} else {
    Write-Host ("opencli helper is not responding yet. Log: {0}" -f $bridgeErrLog)
}

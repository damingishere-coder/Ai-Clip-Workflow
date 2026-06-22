param(
    [int]$BridgePort = 8765,
    [switch]$Restart
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

$existingHealth = $null
if (-not $Restart) {
    try {
        $existingHealth = Invoke-RestMethod -Uri "http://127.0.0.1:$BridgePort/health" -TimeoutSec 3
    } catch {
        $existingHealth = $null
    }
}

if ($existingHealth -and $existingHealth.opencli_available) {
    $listenerProcessIds = @(
        Get-NetTCPConnection -LocalPort $BridgePort -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
    )
    $listenerParentProcessIds = @(
        foreach ($listenerProcessId in $listenerProcessIds) {
            $listenerProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $listenerProcessId" -ErrorAction SilentlyContinue
            if ($listenerProcess.ParentProcessId) {
                $listenerProcess.ParentProcessId
            }
        }
    )
    $extraBridgeProcesses = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -like "python*" -and
            $_.CommandLine -like "*opencli_host_bridge.py*" -and
            $listenerProcessIds -notcontains $_.ProcessId -and
            $listenerParentProcessIds -notcontains $_.ProcessId
        }
    foreach ($extraBridgeProcess in $extraBridgeProcesses) {
        Write-Host ("Stopping extra opencli helper: PID {0}" -f $extraBridgeProcess.ProcessId)
        Stop-Process -Id $extraBridgeProcess.ProcessId -Force
        Wait-Process -Id $extraBridgeProcess.ProcessId -Timeout 5 -ErrorAction SilentlyContinue
    }
    Write-Host ("opencli helper is already ready. Health: http://127.0.0.1:{0}/health" -f $BridgePort)
    return
}

$bridgeConnections = Get-NetTCPConnection -LocalPort $BridgePort -State Listen -ErrorAction SilentlyContinue
$bridgeProcessIds = @($bridgeConnections | Select-Object -ExpandProperty OwningProcess -Unique)
foreach ($processId in $bridgeProcessIds) {
    if (-not $processId -or $processId -eq $PID) {
        continue
    }

    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
    $commandLine = [string]$processInfo.CommandLine
    if ($commandLine -notlike "*opencli_host_bridge.py*") {
        $processName = if ($processInfo.Name) { $processInfo.Name } else { "unknown" }
        throw "Port $BridgePort is already used by PID $processId ($processName). It is not the NiuMa opencli helper, so it was not stopped."
    }

    Write-Host ("Stopping old opencli helper listener: PID {0}" -f $processId)
    Stop-Process -Id $processId -Force
    Wait-Process -Id $processId -Timeout 5 -ErrorAction SilentlyContinue
}

$oldBridgeProcesses = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like "python*" -and $_.CommandLine -like "*opencli_host_bridge.py*" }
foreach ($oldBridgeProcess in $oldBridgeProcesses) {
    if (-not $oldBridgeProcess.ProcessId -or $oldBridgeProcess.ProcessId -eq $PID) {
        continue
    }
    Write-Host ("Stopping old opencli helper: PID {0}" -f $oldBridgeProcess.ProcessId)
    Stop-Process -Id $oldBridgeProcess.ProcessId -Force
    Wait-Process -Id $oldBridgeProcess.ProcessId -Timeout 5 -ErrorAction SilentlyContinue
}

$python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

$logDir = Join-Path $ProjectRoot "data\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$bridgeOutLog = Join-Path $logDir "opencli_bridge_$BridgePort.out.log"
$bridgeErrLog = Join-Path $logDir "opencli_bridge_$BridgePort.err.log"
$bridgeScript = Join-Path $ProjectRoot "scripts\opencli_host_bridge.py"
$bridgeArguments = @("`"$bridgeScript`"", "--host", "0.0.0.0", "--port", "$BridgePort")

Write-Host ("Starting Windows opencli helper: http://127.0.0.1:{0}" -f $BridgePort)
$process = Start-Process `
    -FilePath $python `
    -ArgumentList $bridgeArguments `
    -WorkingDirectory $ProjectRoot `
    -RedirectStandardOutput $bridgeOutLog `
    -RedirectStandardError $bridgeErrLog `
    -WindowStyle Hidden `
    -PassThru

$deadline = (Get-Date).AddSeconds(15)
$bridgeHealth = $null
while ((Get-Date) -lt $deadline) {
    try {
        $bridgeHealth = Invoke-RestMethod -Uri "http://127.0.0.1:$BridgePort/health" -TimeoutSec 3
        break
    } catch {
        Start-Sleep -Milliseconds 800
    }
}

if (-not $bridgeHealth) {
    Write-Host ("opencli helper did not respond in time. PID: {0}. Error log: {1}" -f $process.Id, $bridgeErrLog)
    exit 1
}

if (-not $bridgeHealth.opencli_available) {
    Write-Host ("opencli helper is running, but it did not detect opencli. Error log: {0}" -f $bridgeErrLog)
    exit 1
}

Write-Host ("opencli helper is ready. PID: {0}. Health: http://127.0.0.1:{1}/health" -f $process.Id, $BridgePort)

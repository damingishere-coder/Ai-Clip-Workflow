param(
    [string]$PythonPath = "",
    [string]$ModelCacheDir = "",
    [string]$AudioPath = "",
    [ValidateRange(1, 120)]
    [int]$Seconds = 20,
    [switch]$SkipDependencyInstall,
    [switch]$UseEnvironmentProxyForPip
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot

function Install-VerifiedWheel {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Url,
        [Parameter(Mandatory = $true)]
        [string]$Sha256,
        [Parameter(Mandatory = $true)]
        [string]$Destination
    )

    if (Test-Path -LiteralPath $Destination) {
        $existingHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Destination).Hash.ToLowerInvariant()
        if ($existingHash -ne $Sha256) {
            Remove-Item -LiteralPath $Destination
        }
    }
    if (-not (Test-Path -LiteralPath $Destination)) {
        & curl.exe --noproxy "*" --location --fail --retry 10 --retry-delay 2 --continue-at - --output $Destination $Url
        if ($LASTEXITCODE -ne 0) {
            throw "GPU 运行库 wheel 下载失败：$Destination"
        }
    }
    $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Destination).Hash.ToLowerInvariant()
    if ($actualHash -ne $Sha256) {
        throw "GPU 运行库 wheel SHA-256 校验失败：$Destination"
    }
}

if (-not $PythonPath) {
    $PythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
}
$resolvedPython = Resolve-Path -LiteralPath $PythonPath -ErrorAction SilentlyContinue
if (-not $resolvedPython) {
    throw "找不到项目 Python：$PythonPath。请通过 -PythonPath 指定 NiuMa Studio 的 .venv Python。"
}
$pythonExe = $resolvedPython.Path

if (-not $SkipDependencyInstall) {
    if ($UseEnvironmentProxyForPip) {
        & $pythonExe -m pip install --disable-pip-version-check --timeout 600 --retries 5 -r (Join-Path $projectRoot "requirements-windows-gpu.txt")
        if ($LASTEXITCODE -ne 0) {
            throw "Windows GPU 运行库安装失败，未继续下载模型。"
        }
    }
    else {
        $wheelDirectory = Join-Path ([System.IO.Path]::GetTempPath()) "niuma-offline-transcription-wheels"
        New-Item -ItemType Directory -Force -Path $wheelDirectory | Out-Null
        $cublasWheel = Join-Path $wheelDirectory "nvidia_cublas_cu12-12.9.2.10-py3-none-win_amd64.whl"
        $nvrtcWheel = Join-Path $wheelDirectory "nvidia_cuda_nvrtc_cu12-12.9.86-py3-none-win_amd64.whl"
        Install-VerifiedWheel `
            -Url "https://files.pythonhosted.org/packages/20/e2/fc9a0e985249d873150276d5afb02e39a66817fedbf1a385724393e505ed/nvidia_cublas_cu12-12.9.2.10-py3-none-win_amd64.whl" `
            -Sha256 "623f43027d40d44ceadf0043f002bd25cf353e8f13ce90b9a87057019f560661" `
            -Destination $cublasWheel
        Install-VerifiedWheel `
            -Url "https://files.pythonhosted.org/packages/52/de/823919be3b9d0ccbf1f784035423c5f18f4267fb0123558d58b813c6ec86/nvidia_cuda_nvrtc_cu12-12.9.86-py3-none-win_amd64.whl" `
            -Sha256 "72972ebdcf504d69462d3bcd67e7b81edd25d0fb85a2c46d3ea3517666636349" `
            -Destination $nvrtcWheel
        & $pythonExe -m pip install --disable-pip-version-check --no-index --no-deps $nvrtcWheel $cublasWheel
        if ($LASTEXITCODE -ne 0) {
            throw "Windows GPU 运行库本地 wheel 安装失败，未继续下载模型。"
        }
    }
}

$arguments = @(
    "-X", "utf8",
    (Join-Path $projectRoot "scripts\setup_local_transcription.py")
)
if ($ModelCacheDir) {
    $arguments += @("--cache-dir", $ModelCacheDir)
}
if ($AudioPath) {
    $arguments += @("--audio", $AudioPath, "--seconds", $Seconds)
}

& $pythonExe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "本地离线转写初始化失败。"
}

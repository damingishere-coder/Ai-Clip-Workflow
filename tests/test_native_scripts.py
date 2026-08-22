from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def test_native_scripts_expose_required_parameters_and_runner_command():
    start = _read("scripts/start_native.ps1")
    runner = _read("scripts/run_native.ps1")
    stop = _read("scripts/stop_native.ps1")

    for script in (start, runner, stop):
        assert "[int]$Port = 8001" in script

    assert "[string]$StorageRoot = 'E:\\直播间切片工作流存储'" in start
    assert "[string]$DataDir = ''" in start
    assert "[string]$DatabasePath = ''" in start
    assert "[switch]$SkipWorker" in start
    assert "[switch]$NoBrowser" in start
    assert "[int]$WorkerPort = 8765" in start
    assert "[int]$WorkerPort = 8765" in runner
    assert "scripts\\run_native.ps1" in start
    assert "-SkipDockerSync" in start
    assert "'-WorkerPort'" in start
    assert "try {" in start
    assert "if (-not $?)" in start
    assert "Windows 发布 Worker 启动失败" in start
    assert "if ($LASTEXITCODE -ne 0)" not in start
    assert "& $Python -m uvicorn app.main:app --host 127.0.0.1 --port" in runner


def test_native_scripts_set_host_paths_and_loopback_urls_without_env_file_access():
    start = _read("scripts/start_native.ps1")
    runner = _read("scripts/run_native.ps1")

    required = (
        "DATA_DIR",
        "DATABASE_PATH",
        "STORAGE_ROOT",
        "TASKS_DIR",
        "UPLOAD_TEMP_DIR",
        "PUBLISH_SCHEDULER_EXPORT_DIR",
        "PUBLISH_HOST_PROJECT_ROOT",
        "PUBLISH_WORKER_URL",
        "OPENCLI_HOST_BRIDGE_URL",
        "OPENCLI_LOCAL_BASE_URL",
    )
    for name in required:
        assert name in start
        assert name in runner
    assert 'PUBLISH_WORKER_URL = "http://127.0.0.1:$WorkerPort"' in start
    assert 'OPENCLI_HOST_BRIDGE_URL = "http://127.0.0.1:$WorkerPort"' in start
    assert 'PUBLISH_WORKER_URL = "http://127.0.0.1:$WorkerPort"' in runner
    assert 'OPENCLI_HOST_BRIDGE_URL = "http://127.0.0.1:$WorkerPort"' in runner
    assert "http://127.0.0.1:$Port" in start
    assert ".env" not in start
    assert ".env" not in runner
    assert "compose" not in start.lower()
    assert "compose" not in runner.lower()


def test_native_stop_requires_state_identity_and_only_stops_owned_worker():
    start = _read("scripts/start_native.ps1")
    stop = _read("scripts/stop_native.ps1")

    for field in ("pid", "start_time_utc", "project_root", "port", "command_line"):
        assert field in start
    assert "Get-CimInstance Win32_Process" in stop
    assert "Test-CommandLinePath" in stop
    assert "Test-CommandLinePort" in stop
    assert "start_time_utc" in stop
    assert "taskkill.exe" in stop
    assert "scripts\\publish_host_worker.py" in stop
    assert "Stop-Process -Name" not in stop
    assert "compose" not in stop.lower()
    assert "if ($rawStartTime -is [DateTime])" in stop
    assert "$rawStartTime.ToUniversalTime()" in stop
    assert "ParseExact" in stop
    assert "InvariantCulture" in stop
    assert "RoundtripKind" in stop


def test_native_start_waits_for_health_before_opening_browser_and_cleans_up_on_failure():
    start = _read("scripts/start_native.ps1")

    assert '"http://127.0.0.1:$Port/health"' in start
    assert "AddSeconds(30)" in start
    assert "Invoke-RestMethod -Uri $healthUrl" in start
    assert "Stop-VerifiedProcessTree -ProcessId ([int]$serverProcess.Id)" in start
    assert "Remove-Item -LiteralPath $StatePath" in start
    assert start.index("if (-not $healthReady)") < start.index("if (-not $NoBrowser)")


def test_native_directory_creation_uses_powershell_compatible_path_parameter():
    for relative_path in ("scripts/run_native.ps1", "scripts/start_native.ps1"):
        script = _read(relative_path)
        assert "New-Item -ItemType Directory -LiteralPath" not in script
        assert "New-Item -ItemType Directory -Path" in script

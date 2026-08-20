from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def test_windows_powershell_entrypoints_keep_utf8_bom():
    utf8_bom = b"\xef\xbb\xbf"

    for relative_path in ("scripts/start.ps1", "scripts/stop.ps1"):
        assert (PROJECT_ROOT / relative_path).read_bytes().startswith(utf8_bom)


def test_start_script_documents_distinct_worker_skip_modes_and_fallbacks():
    script = _read("scripts/start.ps1")

    assert "已按 -SkipWorker 跳过 Windows Worker，只启动工作台。" in script
    assert "当前为开发模式：已跳过 Windows Worker，只启动工作台。" in script
    assert "没有检测到 Google Chrome" in script
    assert "当前只启动了工作台：未检测到 Google Chrome，真实发布需要安装 Chrome 后重新运行。" in script


def test_docker_watcher_targets_only_current_compose_project():
    watcher = _read("scripts/watch_docker_publish_worker.ps1")

    assert "$ContainerName = 'niuma-studio'" in watcher
    assert "$ComposeProject = 'niuma-studio'" in watcher
    assert "$ComposeService = 'workflow'" in watcher
    assert "com.docker.compose.project.working_dir" in watcher
    assert "$labelRoot -ieq $ProjectRoot" in watcher
    assert "$StopGraceSeconds = [Math]::Max(5, $StopGraceSeconds)" in watcher
    assert "start_publish_worker.ps1" in watcher
    assert "Get-OwnedWorkerProcessIds" in watcher


def test_watcher_installation_is_reversible_and_migrates_only_owned_legacy_task():
    installer = _read("scripts/install_docker_publish_worker_watcher.ps1")
    uninstaller = _read("scripts/uninstall_docker_publish_worker_watcher.ps1")

    assert "NiuMa Studio Docker Watcher" in installer
    assert "New-ScheduledTaskTrigger -AtLogOn" in installer
    assert "MultipleInstances IgnoreNew" in installer
    assert "NiuMa Studio OpenCLI Host Bridge" in installer
    assert "Test-TaskBelongsToProject" in installer
    assert "Unregister-ScheduledTask" in uninstaller
    assert "Database, task files, Chrome profiles, and logs were preserved" in uninstaller


def test_publish_center_no_longer_requests_manual_start_command():
    template = _read("app/templates/publish.html")
    javascript = _read("app/static/js/publish-center.js")
    worker_client = _read("app/services/publishers/worker_client.py")

    assert r".\scripts\start_niuma_studio.ps1" not in template
    assert "发送服务会在 Docker 中的牛马片场项目运行后自动启动" in template
    assert "随 Docker 项目自动启动" in javascript
    assert r".\scripts\start_niuma_studio.ps1" not in worker_client


def test_windows_worker_has_no_sqlite_repository_dependency():
    worker = _read("scripts/publish_host_worker.py")

    assert "PublishRepository" not in worker
    assert "get_connection" not in worker
    assert "update_account_status" not in worker
    assert "update_execution_phase" not in worker

import argparse
import json
import os
import shutil
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


OPENCLI_TIMEOUT_SECONDS = 600
OPENCLI_NAMES = {"opencli", "opencli.cmd", "opencli.exe", "opencli.ps1"}


def _opencli_executable() -> str | None:
    for candidate in ("opencli.cmd", "opencli.exe", "opencli", "opencli.ps1"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved

    search_dirs: list[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        search_dirs.append(Path(appdata) / "npm")
    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        search_dirs.append(Path(user_profile) / "AppData" / "Roaming" / "npm")

    for directory in search_dirs:
        for candidate in ("opencli.cmd", "opencli.exe", "opencli", "opencli.ps1"):
            path = directory / candidate
            if path.exists():
                return str(path)
    return None


def _opencli_node_command(executable: str) -> list[str] | None:
    wrapper_path = Path(executable)
    main_js = wrapper_path.parent / "node_modules" / "@jackwener" / "opencli" / "dist" / "src" / "main.js"
    if not main_js.exists():
        return None
    node = shutil.which("node") or "node"
    return [node, str(main_js)]


def _opencli_command() -> list[str]:
    executable = _opencli_executable()
    if not executable:
        return ["opencli"]
    path = Path(executable)
    if path.suffix.lower() in {".cmd", ".ps1"} or path.name.lower() == "opencli":
        node_command = _opencli_node_command(executable)
        if node_command:
            return node_command
    return [executable]


def _normalize_command(command: list[str]) -> list[str]:
    if not command:
        return command
    if Path(command[0]).name.lower() not in OPENCLI_NAMES:
        return command
    return [*_opencli_command(), *command[1:]]


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class OpenCLIHostBridgeHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/health":
            _json_response(self, 404, {"status": "not_found"})
            return
        executable = _opencli_executable()
        _json_response(
            self,
            200,
            {
                "status": "ok",
                "opencli_available": bool(executable),
                "opencli_executable": executable or "",
                "message": "Windows opencli 辅助服务已启动。" if executable else "Windows 辅助服务已启动，但没有检测到 opencli。",
            },
        )

    def do_POST(self) -> None:
        if self.path != "/run":
            _json_response(self, 404, {"status": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            command = payload.get("command") or []
            timeout = int(payload.get("timeout") or OPENCLI_TIMEOUT_SECONDS)
            if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
                raise ValueError("command 必须是字符串数组。")
        except Exception as exc:
            _json_response(self, 400, {"status": "bad_request", "message": str(exc)})
            return

        command = _normalize_command(command)
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            _json_response(
                self,
                200,
                {
                    "status": "ok",
                    "returncode": result.returncode,
                    "stdout": result.stdout or "",
                    "stderr": result.stderr or "",
                },
            )
        except subprocess.TimeoutExpired as exc:
            _json_response(
                self,
                200,
                {
                    "status": "timeout",
                    "returncode": 124,
                    "stdout": exc.stdout or "",
                    "stderr": exc.stderr or "opencli 命令超时。",
                },
            )
        except Exception as exc:
            _json_response(self, 200, {"status": "error", "returncode": 127, "stdout": "", "stderr": str(exc)})

    def log_message(self, format: str, *args) -> None:
        return


def main() -> None:
    # 兼容旧启动命令，但实际启动 v1.5 的受保护发布 Worker。
    from scripts.publish_host_worker import main as worker_main

    worker_main()


if __name__ == "__main__":
    main()

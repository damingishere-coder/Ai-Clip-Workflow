"""可取消、可检测无进展超时的本地子进程工具。"""

from __future__ import annotations

import os
import signal
import subprocess


class ProcessTerminationError(RuntimeError):
    """无法确认本地子进程树已经停止。"""


def popen_process_group(command: list[str], **kwargs) -> subprocess.Popen:
    if os.name == "nt":
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(command, **kwargs)


def terminate_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            if process.poll() is not None:
                return
            raise ProcessTerminationError(f"无法执行 taskkill 终止进程树 PID={process.pid}：{exc}") from exc
        if result.returncode != 0 and process.poll() is None:
            stderr = (result.stderr or b"").decode("utf-8", errors="replace").strip()
            raise ProcessTerminationError(
                f"taskkill 未能终止进程树 PID={process.pid}，退出码 {result.returncode}"
                + (f"：{stderr}" if stderr else "")
            )
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired as exc:
            raise ProcessTerminationError(f"进程树 PID={process.pid} 在 taskkill 后仍未退出") from exc
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            process.wait(timeout=5)
        except ProcessLookupError as exc:
            if process.poll() is None:
                raise ProcessTerminationError(f"无法确认进程树 PID={process.pid} 已退出") from exc
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                process.wait(timeout=5)
            except ProcessLookupError as exc:
                if process.poll() is None:
                    raise ProcessTerminationError(f"无法确认进程树 PID={process.pid} 已退出") from exc
            except subprocess.TimeoutExpired as exc:
                raise ProcessTerminationError(f"进程树 PID={process.pid} 在 SIGKILL 后仍未退出") from exc

"""只对本次视觉 CLI 进程禁用无关工具，不写用户配置或更换认证。"""

import json
import re
import subprocess
import time

from app.services.ai.base import AIProviderError
from app.services.managed_process_service import popen_process_group, terminate_process_tree


VISUAL_TOOL_POLICY_VERSION = "visual-attachments-only-v1"
DISABLED_FEATURES = (
    "shell_tool", "unified_exec", "view_image", "apps", "plugins", "multi_agent",
    "browser_use", "browser_use_external", "in_app_browser", "computer_use",
    "image_generation", "memories", "sleep_tool", "skill_search",
    "skill_mcp_dependency_install", "hooks", "goals",
)


def _list_servers(executable, args, environment, directory, timeout):
    if timeout <= 0:
        raise ValueError("tool preflight budget exhausted")
    process = popen_process_group([executable, "-C", directory, *args, "mcp", "list", "--json"],
        cwd=directory, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace")
    try:
        stdout, _stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        terminate_process_tree(process)
        raise
    if process.returncode:
        raise ValueError("tool preflight failed")
    rows = json.loads(stdout)
    if not isinstance(rows, list) or any(not isinstance(row, dict) or not isinstance(row.get("name"), str) or not row["name"] or not isinstance(row.get("enabled"), bool) for row in rows):
        raise ValueError("invalid MCP inventory")
    return rows


def restricted_tool_args(executable, environment, directory, timeout):
    """空 mcp_servers 表会被配置层合并，必须逐项禁用并复核有效状态。"""
    deadline = time.monotonic() + timeout
    values = [*(f"features.{key}=false" for key in DISABLED_FEATURES),
              "agents.enabled=false", "tools.view_image=false", 'web_search="disabled"']
    args = [part for value in values for part in ("-c", value)]
    try:
        rows = _list_servers(executable, args, environment, directory, min(10, deadline-time.monotonic()))
        for row in rows:
            # CLI -c key paths are dot-separated, not TOML quoted-key syntax.
            # Unknown names must fail closed instead of creating a different server key.
            if not re.fullmatch(r"[A-Za-z0-9_-]+", row["name"]):
                raise ValueError("unsupported MCP key")
            args.extend(["-c", f"mcp_servers.{row['name']}.enabled=false"])
        checked = _list_servers(executable, args, environment, directory, min(10, deadline-time.monotonic()))
        if any(row["enabled"] for row in checked):
            raise ValueError("MCP remains enabled")
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        raise AIProviderError("无法确认视觉工具隔离，未发起模型请求", category="visual_tools_unavailable", safe_to_retry=True) from exc
    return args

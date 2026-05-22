from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings  # noqa: E402
from app.services.ai.base import build_url  # noqa: E402
from app.services.ai.ai_clip_analyzer import AnalysisRequest, inspect_local_analysis_plan  # noqa: E402
from app.services.ai.diagnostics import (  # noqa: E402
    ensure_local_ai_ready,
    remote_key_looks_valid,
    test_local_json_generation,
    test_remote_json_generation,
)
from app.services.task_service import get_artifact_paths, get_task  # noqa: E402


def print_result(name: str, ok: bool, detail: str | dict) -> None:
    status = "OK" if ok else "FAILED"
    print(f"\n[{status}] {name}")
    if isinstance(detail, dict):
        print(json.dumps(detail, ensure_ascii=False, indent=2))
    else:
        print(detail)


def _task_prompt_check(task_id: str) -> bool:
    task = get_task(task_id, include_video_probe=False)
    if not task:
        print_result("Task prompt size check", False, f"Task not found: {task_id}")
        return False
    paths = get_artifact_paths(task_id)
    if not paths["transcript_path"].exists():
        print_result("Task prompt size check", False, f"Transcript not found: {paths['transcript_path']}")
        return False
    plan = inspect_local_analysis_plan(
        AnalysisRequest(
            task_id=task_id,
            transcript_path=paths["transcript_path"],
            max_clip_duration_minutes=int(task["max_clip_duration"]),
            target_clip_count=int(task["candidate_clip_count"]),
            ai_preference=task.get("ai_preference") or "",
            provider_name="local",
        )
    )
    print_result("Task prompt size check", True, plan)
    return True


def main() -> int:
    failures = 0
    task_id = sys.argv[1] if len(sys.argv) > 1 else ""
    remote_path = settings.ai_remote_responses_path
    if settings.ai_remote_protocol == "chat_completions":
        remote_path = "/chat/completions" if settings.ai_remote_base_url.rstrip("/").endswith("/v1") or "deepseek" in settings.ai_remote_base_url.lower() else "/v1/chat/completions"
    remote_url = build_url(settings.ai_remote_base_url, remote_path)
    local_url = build_url(settings.ai_local_base_url, "/chat/completions")

    print("AI environment diagnostics")
    print(f"Remote URL: {remote_url}")
    print(f"Remote model: {settings.ai_remote_review_model or settings.ai_remote_model}")
    print(f"Remote protocol: {settings.ai_remote_protocol}")
    print(f"Remote key length: {len(settings.ai_remote_api_key or '')}")
    print(f"Local URL: {local_url}")
    print(f"Local model: {settings.ai_local_model}")

    if remote_key_looks_valid():
        print_result("Remote key shape", True, "AI_REMOTE_API_KEY / OPENAI_API_KEY looks present.")
    else:
        failures += 1
        print_result(
            "Remote key shape",
            False,
            "Remote key looks missing or invalid. Set AI_REMOTE_API_KEY, or OPENAI_API_KEY as a fallback.",
        )

    try:
        print_result("Local Ollama model check", True, ensure_local_ai_ready())
    except Exception as exc:
        failures += 1
        print_result("Local Ollama model check", False, str(exc))

    try:
        print_result(
            "Local JSON generation",
            True,
            test_local_json_generation(timeout_seconds=settings.ai_local_health_timeout_seconds),
        )
    except Exception as exc:
        failures += 1
        print_result("Local JSON generation", False, str(exc))

    try:
        print_result("Remote JSON generation", True, test_remote_json_generation(timeout_seconds=60))
    except Exception as exc:
        failures += 1
        print_result("Remote JSON generation", False, str(exc))

    if task_id and not _task_prompt_check(task_id):
        failures += 1

    print("\nSummary:", "all checks passed" if failures == 0 else f"{failures} check(s) failed")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

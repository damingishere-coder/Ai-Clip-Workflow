from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess

from app.core.config import settings
from app.db.database import get_connection, init_db


DEMO_TASK_ID = "demo_task_variety_001"
DEMO_ACCOUNT_IDS = ("demo_account_douyin", "demo_account_bilibili")


def _require_demo_mode() -> None:
    value = os.getenv("DEMO_MODE", "").strip().lower()
    if value not in {"1", "true", "yes", "on"}:
        raise RuntimeError(
            "拒绝写入演示数据：必须通过 docker-compose.demo.yml 设置 DEMO_MODE=true。"
        )


def _create_demo_video(path: Path) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        return True

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False

    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=0x172033:s=1280x720:d=12:r=25",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-shortest",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(path),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    return completed.returncode == 0 and path.exists() and path.stat().st_size > 0


def _clear_demo_rows(connection) -> None:
    connection.execute(
        "DELETE FROM publish_job_events WHERE job_id IN "
        "(SELECT id FROM publish_jobs WHERE id LIKE 'demo_%')"
    )
    for table in (
        "publish_jobs",
        "subtitle_jobs",
        "output_clip",
        "clip_feedback",
        "ai_analysis_runs",
        "workflow_jobs",
        "cut_runs",
        "clip_candidates",
        "tasks",
    ):
        connection.execute(f"DELETE FROM {table} WHERE id LIKE 'demo_%'")
    connection.execute("DELETE FROM publish_accounts WHERE id LIKE 'demo_%'")


def seed_demo_data(*, reset: bool) -> None:
    _require_demo_mode()
    init_db()

    task_dir = settings.tasks_dir / DEMO_TASK_ID
    source_video = task_dir / "source" / "demo-source.mp4"
    output_dir = task_dir / "outputs"
    has_video = _create_demo_video(source_video)
    output_paths: list[Path] = []
    for index in range(1, 4):
        output_path = output_dir / f"demo-clip-{index:02d}.mp4"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if has_video and (reset or not output_path.exists()):
            shutil.copy2(source_video, output_path)
        output_paths.append(output_path)

    now = datetime.now(timezone.utc).replace(microsecond=0)
    now_iso = now.isoformat()
    created_times = [
        (now - timedelta(hours=2)).isoformat(),
        (now - timedelta(days=1, hours=3)).isoformat(),
        (now - timedelta(days=2, hours=1)).isoformat(),
    ]

    candidates = [
        {
            "id": "demo_clip_001",
            "title": "嘉宾突然反问，现场全体笑翻",
            "start": "00:03:18",
            "end": "00:04:42",
            "duration": 84,
            "summary": "铺垫、反转和现场反应完整，适合作为独立短视频。",
            "tier": "A",
            "score": 97.4,
            "humor": 98,
            "complete": 96,
            "audio": 95,
            "enabled": 1,
        },
        {
            "id": "demo_clip_002",
            "title": "主持人一句话把跑偏的话题拉回来",
            "start": "00:11:08",
            "end": "00:12:26",
            "duration": 78,
            "summary": "观点清晰，人物关系明确，结尾有自然停顿。",
            "tier": "A",
            "score": 94.1,
            "humor": 91,
            "complete": 97,
            "audio": 90,
            "enabled": 1,
        },
        {
            "id": "demo_clip_003",
            "title": "来宾自曝第一次面试的尴尬经历",
            "start": "00:18:40",
            "end": "00:19:31",
            "duration": 51,
            "summary": "故事完整，但开头节奏略慢，建议人工检查前八秒。",
            "tier": "B",
            "score": 86.8,
            "humor": 84,
            "complete": 89,
            "audio": 82,
            "enabled": 0,
        },
        {
            "id": "demo_clip_004",
            "title": "一句吐槽引出全场连续接梗",
            "start": "00:27:12",
            "end": "00:28:35",
            "duration": 83,
            "summary": "多人反应连续，适合保留节奏并减少中间停顿。",
            "tier": "A",
            "score": 92.6,
            "humor": 95,
            "complete": 90,
            "audio": 93,
            "enabled": 1,
        },
        {
            "id": "demo_clip_005",
            "title": "嘉宾分享职业转型后的真实感受",
            "start": "00:36:05",
            "end": "00:37:48",
            "duration": 103,
            "summary": "信息密度较高，适合知识观点类账号。",
            "tier": "A",
            "score": 90.3,
            "humor": 72,
            "complete": 96,
            "audio": 78,
            "enabled": 1,
        },
        {
            "id": "demo_clip_006",
            "title": "结尾十秒的意外补刀",
            "start": "00:44:20",
            "end": "00:45:02",
            "duration": 42,
            "summary": "笑点明确，但缺少前文时可能显得突兀。",
            "tier": "B",
            "score": 83.7,
            "humor": 90,
            "complete": 75,
            "audio": 88,
            "enabled": 0,
        },
    ]

    with get_connection() as connection:
        if reset:
            _clear_demo_rows(connection)

        existing = connection.execute(
            "SELECT 1 FROM tasks WHERE id = ?", (DEMO_TASK_ID,)
        ).fetchone()
        if existing:
            print("Demo data already exists. Use --reset to rebuild it.")
            return

        tasks = [
            (
                DEMO_TASK_ID,
                "综艺访谈 Demo：高光生产闭环",
                DEMO_TASK_ID,
                "upload",
                "general",
                str(source_video) if has_video else "",
                3,
                12,
                "variety_comedy_v2",
                5,
                "preset_002",
                "pending_review",
                78,
                created_times[0],
                now_iso,
            ),
            (
                "demo_task_interview_002",
                "访谈 Demo：AI 产品运营经验",
                "demo_task_interview_002",
                "upload",
                "general",
                "",
                3,
                8,
                "general",
                4,
                "preset_001",
                "transcribing",
                42,
                created_times[1],
                now_iso,
            ),
            (
                "demo_task_livestream_003",
                "直播回放 Demo：创作者工具测评",
                "demo_task_livestream_003",
                "upload",
                "douyin",
                "",
                2,
                8,
                "general",
                4,
                "preset_001",
                "completed",
                100,
                created_times[2],
                now_iso,
            ),
        ]
        connection.executemany(
            """
            INSERT INTO tasks (
                id, task_name, task_dir_name, source_type, platform,
                original_video_path, max_clip_duration, candidate_clip_count,
                selection_profile, final_clip_target, ai_prompt_preset_id,
                status, progress, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            tasks,
        )

        connection.execute(
            """
            INSERT INTO ai_analysis_runs (
                id, task_id, run_number, provider, provider_label, model,
                ai_prompt_preset_id, ai_prompt_preset_name, requested_clip_count,
                clip_count, analysis_summary, analysis_payload_json, created_at, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "demo_analysis_001",
                DEMO_TASK_ID,
                1,
                "demo",
                "Demo AI",
                "demo-highlight-model",
                "preset_002",
                "综艺笑点优先",
                12,
                len(candidates),
                "演示数据：用于查看候选片段、审核和发送中心，不调用真实 AI。",
                json.dumps({"demo": True}, ensure_ascii=False),
                now_iso,
                1,
            ),
        )

        for index, item in enumerate(candidates, start=1):
            evidence = {
                "why_selected": item["summary"],
                "audio": {"labels": ["现场笑声", "多人接话"] if index <= 4 else []},
            }
            connection.execute(
                """
                INSERT INTO clip_candidates (
                    id, task_id, clip_key, title, start_time, end_time,
                    duration_seconds, cover_time_seconds, summary, reason,
                    highlight_reason, spread_value, suggested_editing,
                    confidence_score, quality_tier, quality_score,
                    text_quality_score, humor_score, completeness_score,
                    audio_reaction_score, topic_key, key_moment_time,
                    quality_evidence_json, rejection_reason, selected_by_default,
                    enabled, reviewed, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"],
                    DEMO_TASK_ID,
                    item["id"],
                    item["title"],
                    item["start"],
                    item["end"],
                    item["duration"],
                    item["duration"] / 2,
                    item["summary"],
                    item["summary"],
                    item["summary"],
                    "适合短视频独立传播",
                    "保留完整铺垫和自然收尾，按需压缩停顿。",
                    item["score"],
                    item["tier"],
                    item["score"],
                    item["score"],
                    item["humor"],
                    item["complete"],
                    item["audio"],
                    f"demo-topic-{index}",
                    item["start"],
                    json.dumps(evidence, ensure_ascii=False),
                    "节奏或上下文需要人工复核" if not item["enabled"] else "",
                    item["enabled"],
                    item["enabled"],
                    1,
                    now_iso,
                    now_iso,
                ),
            )

        connection.execute(
            """
            INSERT INTO cut_runs (
                id, task_id, run_number, status, is_active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("demo_cut_run_001", DEMO_TASK_ID, 1, "completed", 1, now_iso, now_iso),
        )

        for index, (candidate, output_path) in enumerate(
            zip(candidates[:3], output_paths, strict=True), start=1
        ):
            connection.execute(
                """
                INSERT INTO output_clip (
                    id, task_id, clip_candidate_id, output_file_path,
                    output_file_name, status, created_at, updated_at,
                    cut_run_id, is_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"demo_output_{index:03d}",
                    DEMO_TASK_ID,
                    candidate["id"],
                    str(output_path) if output_path.exists() else "",
                    output_path.name,
                    "completed",
                    now_iso,
                    now_iso,
                    "demo_cut_run_001",
                    1,
                ),
            )

        accounts = [
            (
                DEMO_ACCOUNT_IDS[0],
                "douyin",
                "Demo 抖音账号（未连接）",
                "browser_profile",
                "login_required",
                "演示账号，不包含真实登录态",
                now_iso,
                now_iso,
            ),
            (
                DEMO_ACCOUNT_IDS[1],
                "bilibili",
                "Demo B站账号（未连接）",
                "browser_profile",
                "login_required",
                "演示账号，不包含真实登录态",
                now_iso,
                now_iso,
            ),
        ]
        connection.executemany(
            """
            INSERT INTO publish_accounts (
                id, platform, account_name, auth_type, login_status,
                remark, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            accounts,
        )

        for index, candidate in enumerate(candidates[:3], start=1):
            output_id = f"demo_output_{index:03d}"
            output_path = output_paths[index - 1]
            for platform, account_id in (
                ("douyin", DEMO_ACCOUNT_IDS[0]),
                ("bilibili", DEMO_ACCOUNT_IDS[1]),
            ):
                scheduled = now + timedelta(days=1, hours=index * 2)
                status = "SCHEDULED" if index <= 2 else "WAITING"
                connection.execute(
                    """
                    INSERT INTO publish_jobs (
                        id, task_id, output_clip_id, clip_id, account_id,
                        platform, publish_mode, video_source, video_file_path,
                        video_path, title, description, caption, tags, hashtags,
                        cover_text, visibility, scheduled_at, schedule_timezone,
                        timezone, status, audit_status, max_attempts,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"demo_publish_{platform}_{index:03d}",
                        DEMO_TASK_ID,
                        output_id,
                        candidate["id"],
                        account_id,
                        platform,
                        "manual_export",
                        "original",
                        str(output_path) if output_path.exists() else "",
                        str(output_path) if output_path.exists() else "",
                        candidate["title"],
                        candidate["summary"],
                        candidate["summary"],
                        "综艺,高光,访谈,Demo",
                        "#综艺 #高光 #访谈 #Demo",
                        candidate["title"],
                        "private",
                        scheduled.isoformat() if status == "SCHEDULED" else None,
                        "Asia/Shanghai",
                        "Asia/Shanghai",
                        status,
                        "not_submitted",
                        1,
                        now_iso,
                        now_iso,
                    ),
                )

        connection.commit()

    print(f"Demo database: {settings.database_path}")
    print(f"Demo storage: {settings.tasks_dir}")
    print("Created 3 tasks, 6 candidates, 3 outputs and 6 safe publish drafts.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed isolated NiuMa Studio demo data.")
    parser.add_argument("--reset", action="store_true", help="Rebuild demo rows and media.")
    args = parser.parse_args()
    seed_demo_data(reset=args.reset)


if __name__ == "__main__":
    main()

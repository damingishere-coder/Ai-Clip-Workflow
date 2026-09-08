"""隔离重放真实 AI 选片；只读原数据库/转写/视频，不启动服务、不切片、不发布。

输出目录必须全新。中断或不确定结果保留 checkpoint，不自动重跑已计费请求。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from datetime import datetime, timezone


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--task", action="append", required=True)
    parser.add_argument("--config-env", type=Path)
    parser.add_argument("--material-manifest", type=Path)
    parser.add_argument(
        "--prompt", type=Path, help="省略时使用快照中 1 号历史内容修订 1"
    )
    args = parser.parse_args()
    source_db, output = args.source_db.resolve(), args.output.resolve()
    if output.exists() or output == source_db.parent or output in source_db.parents:
        parser.error("输出必须是独立且尚不存在的目录")
    if args.config_env:
        allowed = {
            "AI_CODEX_PATH",
            "AI_CODEX_HOME",
            "AI_CODEX_MODEL",
            "AI_CODEX_TIMEOUT_SECONDS",
        }
        for line in args.config_env.read_text(encoding="utf-8-sig").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() in allowed:
                os.environ[key.strip()] = value.strip().strip('"').strip("'")
    output.mkdir(parents=True)
    snapshot = output / "data" / "workflow.sqlite3"
    snapshot.parent.mkdir()
    with (
        sqlite3.connect(source_db.as_uri() + "?mode=ro", uri=True) as source,
        sqlite3.connect(snapshot) as target,
    ):
        source.backup(target)
    os.environ.update(
        {
            "DATA_DIR": str(output / "data"),
            "DATABASE_PATH": str(snapshot),
            "STORAGE_ROOT": str(output / "storage"),
            "TASKS_DIR": str(output / "storage"),
            "UPLOAD_TEMP_DIR": str(output / "uploads"),
            "PUBLISH_SCHEDULER_EXPORT_DIR": str(output / "exports"),
            "NIUMA_WEEKLY_REVIEW_RUNNER_ENABLED": "false",
        }
    )
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.core.config import settings
    from app.db.database import init_db, get_connection
    from app.services import job_service
    from app.services.ai import content_decision_analyzer as analyzer
    from app.services.ai.variety_comedy_analyzer import ComedyAnalysisRequest
    from scripts.kangxi_materials import resolve_materials

    if (
        settings.database_path.resolve() != snapshot
        or settings.tasks_dir.resolve() != output / "storage"
    ):
        raise RuntimeError("隔离路径校验失败")
    init_db()
    with get_connection() as connection:
        baseline = connection.execute(
            "SELECT prompt_text,prompt_sha256 FROM ai_prompt_versions WHERE preset_id='preset_001' AND version_number=1"
        ).fetchone()
        prompt = (
            args.prompt.read_text(encoding="utf-8-sig")
            if args.prompt
            else baseline["prompt_text"]
        )
        if (
            not args.prompt
            and hashlib.sha256(prompt.encode()).hexdigest()
            != "fac845220c05e37e8ec8372eadfd272a19367d3489ff57f9581f399f1a3f92e6"
        ):
            raise RuntimeError("基线不符")
        tasks = []
        for task_id in args.task:
            task = connection.execute(
                "SELECT * FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            if not task:
                raise RuntimeError(f"任务不存在：{task_id}")
            tasks.append(dict(task))
    (output / "prompt.txt").write_text(prompt, encoding="utf-8")
    manifest = {
        "isolation": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_db": str(source_db),
        "snapshot": str(snapshot),
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "contract": analyzer.CONTRACT_VERSION,
        "tasks": [],
    }
    # 日志只记录单元进度，不打印配置、认证或 Provider 完整输入。
    original_execute = analyzer.execute_checkpointed_ai_unit

    def execute(**kwargs):
        print(
            json.dumps(
                {
                    "stage": kwargs["namespace"],
                    "unit": kwargs["unit_id"],
                    "status": "running",
                }
            ),
            flush=True,
        )
        result = original_execute(**kwargs)
        print(
            json.dumps(
                {
                    "stage": kwargs["namespace"],
                    "unit": kwargs["unit_id"],
                    "status": result.status,
                    "error": result.error,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return result

    analyzer.execute_checkpointed_ai_unit = execute
    for task in tasks:
        task_id = task["id"]
        # 真实任务的托管目录由原视频的位置确定；原片只读引用，转写先复制再分析。
        video, transcript = resolve_materials(
            task, manifest_path=args.material_manifest
        )
        source_root = video.parent.parent
        if not transcript.is_file():
            raise RuntimeError(f"原转写不存在：{transcript}")
        task_output = output / task_id
        task_output.mkdir()
        local_transcript = task_output / "transcript.md"
        local_transcript.write_bytes(transcript.read_bytes())
        with get_connection() as connection:
            old_runs = [
                dict(r)
                for r in connection.execute(
                    "SELECT * FROM ai_analysis_runs WHERE task_id=? ORDER BY run_number",
                    (task_id,),
                )
            ]
        (task_output / "historical_runs.json").write_text(
            json.dumps(old_runs, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        job = job_service.create_job(
            task_id,
            job_service.JOB_TYPE_AI_ANALYSIS,
            payload={"isolated_content_comparison": True},
        )
        claimed = job_service.claim_job(
            job["id"], "isolated-replay", lease_seconds=14400
        )
        if not claimed:
            raise RuntimeError("隔离任务已有活动作业，未调用 AI")
        with job_service.job_lease_context(
            job["id"], "isolated-replay", claimed["lease_token"]
        ):
            result = analyzer.analyze_content_decisions(
                ComedyAnalysisRequest(
                    task_id,
                    local_transcript,
                    source_root / "audio" / "source.wav",
                    12,
                    5,
                    task.get("ai_preference") or "",
                    "codex",
                    prompt,
                    "content",
                )
            )
            if result.analysis_meta.get("analysis_incomplete"):
                job_service.mark_job_failed(
                    job["id"], "隔离分析有未完成单元，保留成功缓存并等待确认"
                )
            else:
                job_service.mark_job_completed(
                    job["id"], {"isolated": True, "analysis_meta": result.analysis_meta}
                )
        payload = result.model_dump()
        payload["analysis_meta"].update(
            {
                "prompt_sha256": manifest["prompt_sha256"],
                "transcript_sha256": digest(local_transcript),
                "model": settings.ai_codex_model,
            }
        )
        result_path = task_output / "result.json"
        result_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        manifest["tasks"].append(
            {
                "task_id": task_id,
                "video": str(video),
                "transcript_sha256": digest(local_transcript),
                "result": str(result_path),
                "result_sha256": digest(result_path),
                "job_id": job["id"],
                "analysis_meta": payload["analysis_meta"],
            }
        )
        (output / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(manifest["tasks"][-1], ensure_ascii=False), flush=True)
    return (
        1
        if any(t["analysis_meta"]["analysis_incomplete"] for t in manifest["tasks"])
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())

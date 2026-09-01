from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_CACHE_DIR = Path(r"E:\直播间切片工作流存储\_模型\faster-whisper")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="使用隔离数据库验证本地长音频转写、分块 checkpoint 和续跑能力。",
    )
    parser.add_argument("audio_path", help="本地音频文件路径")
    parser.add_argument("workspace_dir", help="隔离验收目录；续跑时必须使用同一目录")
    parser.add_argument("--seconds", type=int, default=0, help="仅验证前 N 秒；0 表示完整音频")
    parser.add_argument("--task-id", default="offline-local-benchmark", help="隔离数据库中的固定任务 ID")
    parser.add_argument(
        "--stop-after-chunks",
        type=int,
        default=0,
        help="完成指定数量分块并保存 checkpoint 后主动停止；0 表示运行到结束",
    )
    parser.add_argument(
        "--model-cache-dir",
        default=str(DEFAULT_MODEL_CACHE_DIR),
        help="固定版本 faster-whisper 模型缓存根目录",
    )
    return parser.parse_args()


def configure_isolated_environment(args: argparse.Namespace, workspace_dir: Path) -> None:
    data_dir = workspace_dir / "data"
    storage_dir = workspace_dir / "storage"
    environment = {
        "DATA_DIR": str(data_dir),
        "DATABASE_PATH": str(data_dir / "benchmark.sqlite3"),
        "STORAGE_ROOT": str(storage_dir),
        "TASKS_DIR": str(storage_dir),
        "UPLOAD_TEMP_DIR": str(storage_dir / "_uploads"),
        "TRANSCRIPTION_PROVIDER": "local",
        "TRANSCRIPTION_FALLBACK_PROVIDER": "",
        "TRANSCRIPTION_OFFLINE_ONLY": "true",
        "TRANSCRIPTION_MODEL": "large-v3",
        "TRANSCRIPTION_MODEL_REVISION": "edaa852ec7e145841d8ffdb056a99866b5f0a478",
        "TRANSCRIPTION_MODEL_CACHE_DIR": str(Path(args.model_cache_dir).resolve()),
        "TRANSCRIPTION_LOCAL_FILES_ONLY": "true",
        "TRANSCRIPTION_DEVICE": "cuda",
        "TRANSCRIPTION_COMPUTE_TYPE": "float16",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    }
    os.environ.update(environment)


def main() -> int:
    args = parse_args()
    audio_path = Path(args.audio_path).resolve()
    workspace_dir = Path(args.workspace_dir).resolve()
    if not audio_path.is_file():
        raise SystemExit(f"音频文件不存在：{audio_path}")
    if args.seconds < 0:
        raise SystemExit("--seconds 不能小于 0")
    if args.stop_after_chunks < 0:
        raise SystemExit("--stop-after-chunks 不能小于 0")

    workspace_dir.mkdir(parents=True, exist_ok=True)
    configure_isolated_environment(args, workspace_dir)
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    from app.db.database import get_connection, init_db
    from app.models.task import TaskCreate
    from app.services.task_lifecycle_service import create_task_record
    from app.services.transcript_service import (
        TranscriptChunk,
        _extract_audio_chunk,
        write_transcript_markdown,
    )

    init_db()
    with get_connection() as connection:
        task_exists = connection.execute("SELECT 1 FROM tasks WHERE id = ?", (args.task_id,)).fetchone()
    if not task_exists:
        create_task_record(
            TaskCreate(task_name="完全离线长音频验收", selection_profile="general"),
            task_id=args.task_id,
            task_dir_name=args.task_id,
        )

    benchmark_audio_path = audio_path
    if args.seconds:
        benchmark_audio_path = workspace_dir / f"source-first-{args.seconds}s.wav"
        if not benchmark_audio_path.exists():
            _extract_audio_chunk(
                audio_path,
                benchmark_audio_path,
                TranscriptChunk(index=1, start_seconds=0, end_seconds=max(1, args.seconds)),
            )

    transcript_path = workspace_dir / "transcript.md"
    started_at = time.perf_counter()
    reuse_count = 0

    def report_progress(progress: dict) -> None:
        nonlocal reuse_count
        message = str(progress.get("message") or "")
        if "已复用第" in message and "checkpoint" in message:
            reuse_count += 1
        print(json.dumps(progress, ensure_ascii=False), flush=True)
        if (
            args.stop_after_chunks
            and message.startswith(f"已完成第 {args.stop_after_chunks}/")
        ):
            raise RuntimeError(
                f"验收脚本按计划在第 {args.stop_after_chunks} 个分块落盘后停止；"
                "使用相同命令去掉 --stop-after-chunks 即可续跑。"
            )

    try:
        result = write_transcript_markdown(
            {
                "id": args.task_id,
                "task_name": "完全离线长音频验收",
                "source": str(audio_path),
            },
            benchmark_audio_path,
            transcript_path,
            progress_callback=report_progress,
            provider="local",
        )
    except RuntimeError as exc:
        elapsed_seconds = round(time.perf_counter() - started_at, 2)
        if "验收脚本按计划" in str(exc):
            print(
                json.dumps(
                    {
                        "status": "stopped_after_checkpoint",
                        "elapsed_seconds": elapsed_seconds,
                        "reason": str(exc),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return 75
        raise

    progress_path = transcript_path.with_name("transcript_progress.json")
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    elapsed_seconds = round(time.perf_counter() - started_at, 2)
    print(
        json.dumps(
            {
                "status": "completed",
                "elapsed_seconds": elapsed_seconds,
                "reuse_count": reuse_count,
                "audio_path": str(benchmark_audio_path),
                "transcript_path": str(transcript_path),
                "provider": result.get("provider"),
                "model": progress.get("model"),
                "device": progress.get("device"),
                "compute_type": progress.get("compute_type"),
                "segment_count": result.get("segment_count"),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

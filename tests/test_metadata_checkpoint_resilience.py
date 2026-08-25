from __future__ import annotations

from pathlib import Path

import pytest

from app.services import pipeline_engine
from app.models.task import TaskStatus
from app.services.pipeline_engine import PipelineCheckpointError, PipelineEngine
from app.services.storage_service import get_artifact_paths


def test_partial_metadata_checkpoint_reuses_completed_platform(monkeypatch):
    task_id = "test-metadata-partial-recovery"
    output_clip = {
        "id": "output-1",
        "clip_candidate_id": "candidate-1",
        "task_name": "测试任务",
        "clip_title": "测试片段",
        "clip_summary": "摘要",
        "highlight_reason": "理由",
        "spread_value": "中",
        "suggested_editing": "保留完整表达",
        "status": "completed",
        "file_exists": True,
        "cover_time_seconds": 1,
    }
    monkeypatch.setattr(pipeline_engine.task_service, "list_output_clips", lambda _task_id: [output_clip])
    monkeypatch.setattr(pipeline_engine, "platforms_for_task", lambda _task: ["douyin", "bilibili"])
    engine = PipelineEngine()
    monkeypatch.setattr(engine, "_get_task", lambda _task_id: {"id": task_id, "platform": "general"})
    monkeypatch.setattr(engine, "_write_clip_metadata", lambda *_args, **_kwargs: None)

    cover_path = get_artifact_paths(task_id)["covers_dir"] / "cover.jpg"

    def generate_cover(*_args, **_kwargs):
        cover_path.parent.mkdir(parents=True, exist_ok=True)
        cover_path.write_bytes(b"cover")
        return {"cover_file_path": str(cover_path), "cover_time_seconds": 1}

    monkeypatch.setattr(pipeline_engine, "generate_publish_cover_for_item", generate_cover)
    first_calls: list[str] = []

    class FirstGenerator:
        def __init__(self, *, use_ai: bool):
            assert use_ai is True

        def generate(self, _item: dict, platform: str) -> dict:
            first_calls.append(platform)
            if platform == "bilibili":
                raise RuntimeError("模拟第二个平台中断")
            return {"platform": platform, "title": platform, "risk_flags": []}

    monkeypatch.setattr(pipeline_engine, "MetadataGenerator", FirstGenerator)
    with pytest.raises(RuntimeError, match="第二个平台中断"):
        engine._generate_metadata(task_id, {"config": {"auto_metadata_use_ai": True}})
    assert first_calls == ["douyin", "bilibili"]

    second_calls: list[str] = []

    class SecondGenerator:
        def __init__(self, *, use_ai: bool):
            assert use_ai is True

        def generate(self, _item: dict, platform: str) -> dict:
            second_calls.append(platform)
            return {"platform": platform, "title": platform, "risk_flags": []}

    monkeypatch.setattr(pipeline_engine, "MetadataGenerator", SecondGenerator)
    result = engine._generate_metadata(task_id, {"config": {"auto_metadata_use_ai": True}})
    assert second_calls == ["bilibili"]
    assert result["metadata_count"] == 2
    assert [item["metadata"]["platform"] for item in result["metadata_items"]] == ["douyin", "bilibili"]

    metadata_path = Path(result["metadata_path"])
    assert metadata_path.is_file()


def test_metadata_checkpoint_requires_every_output_platform_pair(monkeypatch):
    engine = PipelineEngine()
    task_id = "test-metadata-pair-coverage"
    monkeypatch.setattr(engine, "_verify_file_evidence", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(engine, "_active_output_ids", lambda _task_id: ["output-1"])
    monkeypatch.setattr(engine, "_get_task", lambda _task_id: {"id": task_id})
    monkeypatch.setattr(pipeline_engine, "platforms_for_task", lambda _task: ["douyin", "bilibili"])
    monkeypatch.setattr(
        engine,
        "_read_json_list",
        lambda _path: [
            {
                "output_clip": {"id": "output-1"},
                "metadata": {"platform": "douyin", "title": "only one", "risk_flags": []},
                "cover": {},
            }
        ],
    )
    with pytest.raises(PipelineCheckpointError, match="active output 与平台"):
        engine._restore_checkpoint_step(
            task_id,
            TaskStatus.METADATA_GENERATING,
            {"metadata": {}, "output_clip_ids": ["output-1"]},
        )

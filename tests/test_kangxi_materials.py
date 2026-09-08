import hashlib
import json

import pytest

from scripts.kangxi_materials import resolve_materials


def test_deleted_original_requires_verified_copy_manifest(tmp_path):
    task = {
        "id": "original",
        "original_video_path": str(tmp_path / "old/source/video.mp4"),
    }
    with pytest.raises(RuntimeError, match="清单"):
        resolve_materials(task)
    replacement = tmp_path / "retained"
    (replacement / "source").mkdir(parents=True)
    (replacement / "transcripts").mkdir()
    video, transcript = (
        replacement / "source/video.mp4",
        replacement / "transcripts/transcript.md",
    )
    video.write_bytes(b"same video")
    transcript.write_text("same transcript")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "original_task_id": "original",
                        "state": "prepared",
                        "task_dir_name": "retained",
                        "source_sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
                        "transcript_sha256": hashlib.sha256(
                            transcript.read_bytes()
                        ).hexdigest(),
                    }
                ]
            }
        )
    )
    assert resolve_materials(task, manifest_path=manifest) == (video, transcript)
    transcript.write_text("changed transcript")
    with pytest.raises(RuntimeError, match="哈希"):
        resolve_materials(task, manifest_path=manifest)

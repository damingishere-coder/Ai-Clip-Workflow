"""只读定位对照素材；原目录已删除时，必须有先前复制清单和文件哈希。"""

import hashlib
import json
from pathlib import Path


def resolve_materials(task, *, manifest_path=None):
    video = Path(task["original_video_path"])
    transcript = video.parent.parent / "transcripts" / "transcript.md"
    if video.is_file() and transcript.is_file():
        return video, transcript
    if manifest_path is None:
        raise RuntimeError("原素材不存在；需要有复制来源和哈希的素材清单，不能猜测替换")
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    matches = [
        item
        for item in manifest["tasks"]
        if item.get("original_task_id") == task["id"]
        and item.get("state") == "prepared"
    ]
    if len(matches) != 1:
        raise RuntimeError("素材清单未唯一对应原任务")
    item = matches[0]
    directory = Path(item["task_dir_name"])
    if (
        directory.is_absolute()
        or len(directory.parts) != 1
        or directory.name in {".", ".."}
    ):
        raise RuntimeError("素材清单目录不安全")
    replacement = video.parent.parent.parent / directory
    video = replacement / "source" / video.name
    transcript = replacement / "transcripts" / "transcript.md"
    for path, key in ((video, "source_sha256"), (transcript, "transcript_sha256")):
        with path.open("rb") as handle:
            actual = hashlib.file_digest(handle, "sha256").hexdigest()
        if actual != item.get(key):
            raise RuntimeError("保留素材与原复制清单哈希不一致，拒绝对照")
    return video, transcript

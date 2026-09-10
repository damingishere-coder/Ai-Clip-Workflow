"""备份并原位把 transcript.md 转为 OpenCC t2s 简体字形。"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.chinese_text_service import simplify_chinese_text  # noqa: E402


_TIMESTAMP_PATTERN = re.compile(r"\b\d{2}:\d{2}:\d{2}\b")


def normalize_transcript_file(transcript_path: Path, backup_dir: Path) -> dict:
    transcript_path = transcript_path.resolve()
    backup_dir = backup_dir.resolve()
    if not transcript_path.is_file():
        raise FileNotFoundError(f"未找到 transcript：{transcript_path}")
    original_bytes = transcript_path.read_bytes()
    original = original_bytes.decode("utf-8")
    converted = simplify_chinese_text(original)
    _validate_structure(original, converted)

    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"transcript.before-t2s-{timestamp}.md"
    if backup_path.exists():
        raise FileExistsError(f"备份文件已存在：{backup_path}")
    shutil.copy2(transcript_path, backup_path)

    temp_path = transcript_path.with_name(f"{transcript_path.name}.t2s.tmp")
    try:
        temp_path.write_bytes(converted.encode("utf-8"))
        if temp_path.read_bytes().decode("utf-8") != converted:
            raise RuntimeError("转换后的临时文件回读校验失败")
        temp_path.replace(transcript_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    final_bytes = transcript_path.read_bytes()
    return {
        "status": "converted" if final_bytes != original_bytes else "unchanged",
        "transcript_path": str(transcript_path),
        "backup_path": str(backup_path),
        "before_sha256": hashlib.sha256(original_bytes).hexdigest(),
        "after_sha256": hashlib.sha256(final_bytes).hexdigest(),
        "line_count": len(original.splitlines()),
        "timestamp_count": len(_TIMESTAMP_PATTERN.findall(original)),
    }


def _validate_structure(original: str, converted: str) -> None:
    original_lines = original.splitlines()
    converted_lines = converted.splitlines()
    if len(original_lines) != len(converted_lines):
        raise RuntimeError("转换前后行数变化，已停止覆盖 transcript")
    if _TIMESTAMP_PATTERN.findall(original) != _TIMESTAMP_PATTERN.findall(converted):
        raise RuntimeError("转换前后时间戳变化，已停止覆盖 transcript")
    if [line.count("|") for line in original_lines] != [line.count("|") for line in converted_lines]:
        raise RuntimeError("转换前后 Markdown 表格结构变化，已停止覆盖 transcript")
    if [len(line) - len(line.lstrip("#")) for line in original_lines] != [
        len(line) - len(line.lstrip("#")) for line in converted_lines
    ]:
        raise RuntimeError("转换前后 Markdown 标题结构变化，已停止覆盖 transcript")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transcript", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            normalize_transcript_file(args.transcript, args.backup_dir),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

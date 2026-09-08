import json

import pytest

from scripts.build_kangxi_review import build_review
from app.services.ai.content_decision_analyzer import _payload
from tests.test_content_decisions import item


def test_review_export_covers_both_sides_and_requires_human_notes(tmp_path):
    video = tmp_path / "source video.mp4"
    dirs = []
    for name in ("baseline", "trial"):
        folder = tmp_path / name
        folder.mkdir()
        clip = _payload(item(), 1)
        clip["title"] = "<script>alert(1)</script>"
        result = folder / "result.json"
        result.write_text(
            json.dumps(
                {
                    "task_id": "29",
                    "clips": [clip],
                    "analysis_meta": {"analysis_incomplete": False},
                }
            ),
            encoding="utf-8",
        )
        (folder / "manifest.json").write_text(
            json.dumps(
                {
                    "tasks": [
                        {
                            "task_id": "29",
                            "video": str(video),
                            "transcript_sha256": "same",
                            "result": str(result),
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        dirs.append(folder)
    output = tmp_path / "review.html"
    build_review(*dirs, output)
    page = output.read_text(encoding="utf-8")
    assert page.count("<textarea ") == 12
    assert "baseline:clip_001" in page and "trial:clip_001" in page
    assert "我已亲自核对" in page and 'id="confirmed"' in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<script>alert(1)</script>" not in page
    assert video.as_uri() in page
    with pytest.raises(FileExistsError):
        build_review(*dirs, output)

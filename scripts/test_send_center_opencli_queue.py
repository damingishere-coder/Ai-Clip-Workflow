import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.services import publish_service  # noqa: E402


def _fake_job(platform: str) -> dict:
    return {
        "id": f"job-{platform}",
        "task_id": f"task-{platform}",
        "output_clip_id": f"clip-{platform}",
        "platform": platform,
        "video_source": "original",
        "title": "High energy clip",
        "description": "A short live stream highlight.",
        "tags": "live,highlight",
        "video_file_path": r"C:\tmp\clip.mp4",
        "cover_file_path": r"C:\tmp\cover.jpg",
        "bilibili_tid": "entertainment",
        "bilibili_copyright": "original",
    }


def _joined(commands: list[list[str]]) -> str:
    return "\n".join(" ".join(command) for command in commands)


def test_douyin_browser_commands() -> None:
    commands = publish_service._build_douyin_browser_commands(  # noqa: SLF001
        _fake_job("douyin"),
        Path(r"C:\tmp\clip.mp4"),
        Path(r"C:\tmp\cover.jpg"),
    )
    assert commands[0][2:6] == ["send-douyin-job-douyin", "--window", "foreground", "open"]
    assert "--window" not in commands[0][6:]
    assert commands[2][3] == "eval"
    assert "(async()=>{" in commands[2][4]
    text = _joined(commands)
    assert "creator.douyin.com" in text
    assert "http://127.0.0.1:8002/media/tasks/task-douyin/output-clips/clip-douyin" in text
    assert "upload input[type='file']" not in " ".join(commands[2])
    assert "fill input[placeholder*='title']" not in text
    assert "fill input[placeholder*='\u6807\u9898'],textarea[placeholder*='\u6807\u9898']" not in text
    assert "fill textarea[placeholder*='\u7b80\u4ecb'],textarea[placeholder*='\u63cf\u8ff0'],div[contenteditable='true']" not in text
    assert "title_field_not_found" in text
    assert "caption_field_not_found" in text
    assert "High energy clip" in text
    assert "#live #highlight" in text
    assert "\u53d1\u5e03" in text
    assert "\u6211\u77e5\u9053\u4e86" in text
    assert r"C:\tmp\cover.jpg" not in text


def test_bilibili_browser_commands() -> None:
    commands = publish_service._build_bilibili_browser_commands(  # noqa: SLF001
        _fake_job("bilibili"),
        Path(r"C:\tmp\clip.mp4"),
        Path(r"C:\tmp\cover.jpg"),
    )
    assert commands[0][2:6] == ["send-bilibili-job-bilibili", "--window", "foreground", "open"]
    assert "--window" not in commands[0][6:]
    text = _joined(commands)
    assert "member.bilibili.com/platform/upload/video/frame" in text
    assert "upload input[type='file']" in text
    assert "fill input[placeholder*='\u6807\u9898'],textarea[placeholder*='\u6807\u9898']" not in text
    assert "fill textarea[placeholder*='\u7b80\u4ecb'],textarea[placeholder*='\u4ecb\u7ecd'],textarea" not in text
    assert "title_field_not_found" in text
    assert "description_field_not_found" in text
    assert "High energy clip" in text
    assert "live" in text
    assert "highlight" in text
    assert "\u7acb\u5373\u6295\u7a3f" in text
    assert r"C:\tmp\cover.jpg" in text


def test_fallback_metadata() -> None:
    metadata = publish_service.generate_publish_metadata(
        {
            "clip_title": "Great live moment",
            "task_name": "Demo task",
            "summary": "Funny audience reaction and useful talking point.",
            "highlight_reason": "High replay value.",
            "transcript_excerpt": "The host explains the key idea clearly.",
        },
        use_ai=False,
    )
    assert metadata["title"] == "Great live moment"
    assert metadata["tags"]
    assert metadata["description"]


def test_publish_metadata_sanitizes_sensitive_words() -> None:
    metadata = publish_service.generate_publish_metadata(
        {
            "clip_title": "笑死了这段屎一样的翻车现场",
            "task_name": "Demo task",
            "clip_summary": "主播聊到诈骗和加微信的话题，现场笑死。",
            "highlight_reason": "高能反应。",
        },
        use_ai=False,
    )
    combined = f"{metadata['title']} {metadata['tags']} {metadata['description']}"
    for blocked in ("屎", "死", "诈骗", "加微信"):
        assert blocked not in combined
    assert metadata["title"]
    assert metadata["tags"]


def test_publish_tags_are_hashtag_topics() -> None:
    tags = publish_service._format_tags("#高光片段 #直播切片 这是标题解释了一下, 死亡现场")  # noqa: SLF001
    hashtag_text = publish_service._hashtags(tags)  # noqa: SLF001
    assert hashtag_text.startswith("#")
    assert " " in hashtag_text
    assert "死亡" not in hashtag_text
    assert "标题解释" not in hashtag_text


def test_caption_for_job_sanitizes_existing_dirty_content() -> None:
    caption = publish_service._caption_for_job(  # noqa: SLF001
        {
            "description": "这段笑死了，还有诈骗套路。",
            "tags": "屎, 高光片段, 加微信",
        }
    )
    for blocked in ("屎", "死", "诈骗", "加微信"):
        assert blocked not in caption
    assert "#高光片段" in caption


def test_opencli_windows_npm_fallback() -> None:
    original_which = publish_service.shutil.which
    original_appdata = publish_service.os.environ.get("APPDATA")
    original_userprofile = publish_service.os.environ.get("USERPROFILE")
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            npm_dir = Path(temp_dir) / "npm"
            npm_dir.mkdir()
            expected = npm_dir / "opencli.cmd"
            expected.write_text("@echo off\n", encoding="utf-8")

            publish_service.shutil.which = lambda _candidate: None
            publish_service.os.environ["APPDATA"] = temp_dir
            publish_service.os.environ.pop("USERPROFILE", None)

            assert publish_service._opencli_executable() == str(expected)  # noqa: SLF001
    finally:
        publish_service.shutil.which = original_which
        if original_appdata is None:
            publish_service.os.environ.pop("APPDATA", None)
        else:
            publish_service.os.environ["APPDATA"] = original_appdata
        if original_userprofile is None:
            publish_service.os.environ.pop("USERPROFILE", None)
        else:
            publish_service.os.environ["USERPROFILE"] = original_userprofile


def main() -> None:
    test_douyin_browser_commands()
    print("douyin browser commands: OK")
    test_bilibili_browser_commands()
    print("bilibili browser commands: OK")
    test_fallback_metadata()
    print("fallback metadata: OK")
    test_publish_metadata_sanitizes_sensitive_words()
    print("publish metadata safety: OK")
    test_publish_tags_are_hashtag_topics()
    print("publish hashtag topics: OK")
    test_caption_for_job_sanitizes_existing_dirty_content()
    print("existing dirty caption safety: OK")
    test_opencli_windows_npm_fallback()
    print("opencli windows npm fallback: OK")


if __name__ == "__main__":
    main()

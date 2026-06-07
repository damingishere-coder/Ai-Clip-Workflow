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


def _browser_args(command: list[str]) -> list[str]:
    browser_index = command.index("browser")
    return command[browser_index:]


def test_douyin_browser_commands() -> None:
    commands = publish_service._build_douyin_browser_commands(  # noqa: SLF001
        _fake_job("douyin"),
        Path(r"C:\tmp\clip.mp4"),
        Path(r"C:\tmp\cover.jpg"),
    )
    assert _browser_args(commands[0])[1:5] == ["send-douyin-job-douyin", "--window", "foreground", "open"]
    assert "--window" not in _browser_args(commands[0])[5:]
    assert _browser_args(commands[2])[2] == "eval"
    assert "(async()=>{" in _browser_args(commands[2])[3]
    text = _joined(commands)
    assert "creator.douyin.com" in text
    assert "http://127.0.0.1:8002/media/tasks/task-douyin/output-clips/clip-douyin" in text
    assert "upload input[type='file']" not in " ".join(commands[2])
    assert "fill input[placeholder*='title']" not in text
    assert "fill input[placeholder*='\u6807\u9898'],textarea[placeholder*='\u6807\u9898']" not in text
    assert "fill textarea[placeholder*='\u7b80\u4ecb'],textarea[placeholder*='\u63cf\u8ff0'],div[contenteditable='true']" not in text
    assert "click --role button --name \u53d1\u5e03" not in text
    assert "title_field_not_found" in text
    assert "description_set:true" in text
    assert "plain_hashtags_removed:true" in text
    assert "duplicate_removed" in text
    assert "data-mention" not in text
    assert "douyin_topic_insert_failed" not in text
    assert "replaceChildren" not in text
    assert "&gt;" not in text
    assert "douyin_ai_cover_not_ready" in text
    assert "AI智能推荐封面" in text
    assert "cover_confirmed" in text
    assert "leftmost_ai_cover_selected" in text
    assert "cover_success_detected" in text
    assert "cover_wait_timeout_ms" in text
    assert "150000" in text
    assert "设为封面" in text
    assert "使用封面" in text
    assert "封面效果检测通过" in text
    assert "douyin_cover_finish_not_found" not in text
    assert "horizontal_cover_selected" not in text
    assert "vertical_cover_selected" not in text
    assert "设置横封面" not in text
    assert "设置竖封面" not in text
    assert "确定" in text
    assert "douyin_publish_button_not_found" in text
    assert "ai_cover_selected" in text
    assert "High energy clip" in text
    assert "#live #highlight" in text
    assert '"live", "highlight"' not in text
    assert "\u53d1\u5e03" in text
    assert "\u6211\u77e5\u9053\u4e86" in text
    assert r"C:\tmp\cover.jpg" not in text


def test_douyin_description_copies_body_and_platform_topics_directly() -> None:
    body = "陈亦飞回忆当年在美国陪S姐妹游玩，目睹小S在雨中打电话给妈妈，回房间后对着镜子自信爆棚，大喊“我真的超正的！”，真实又可爱，满满青春回忆"
    topics = "#小S自恋名场面 #青春回忆杀 #明星搞笑日常 #反差萌瞬间 #姐妹花趣事"
    job = _fake_job("douyin")
    job["description"] = body
    job["tags"] = topics

    description = publish_service._douyin_description_for_job(job, "Fallback title")  # noqa: SLF001

    assert description == f"{body}\n{topics}"
    commands = publish_service._build_douyin_browser_commands(  # noqa: SLF001
        job,
        Path(r"C:\tmp\clip.mp4"),
        Path(r"C:\tmp\cover.jpg"),
    )
    text = _joined(commands)
    assert body in text
    assert topics in text
    assert "data-mention" not in text
    assert "douyin_topic_insert_failed" not in text
    assert "replaceChildren" not in text
    assert not hasattr(publish_service, "_douyin_insert_topics_script")
    assert not hasattr(publish_service, "_browser_insert_douyin_topics_command")


def test_bilibili_browser_commands() -> None:
    commands = publish_service._build_bilibili_browser_commands(  # noqa: SLF001
        _fake_job("bilibili"),
        Path(r"C:\tmp\clip.mp4"),
        Path(r"C:\tmp\cover.jpg"),
    )
    assert _browser_args(commands[0])[1:5] == ["send-bilibili-job-bilibili", "--window", "foreground", "open"]
    assert "--window" not in _browser_args(commands[0])[5:]
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


def test_publish_metadata_ai_uses_publish_remote_interface_even_when_default_local() -> None:
    captured: dict[str, str] = {}

    class FakeProvider:
        def generate_json(self, prompt: str, retry_instruction: str | None = None) -> str:
            captured["prompt"] = prompt
            return '{"title":"AI发布标题","tags":["综艺片段","高光时刻"],"description":"适合发布的简介"}'

    original_builder = publish_service.build_remote_provider
    original_default_provider = publish_service.settings.ai_default_provider
    original_publish_model = publish_service.settings.ai_publish_remote_model
    try:
        object.__setattr__(publish_service.settings, "ai_default_provider", "local")
        object.__setattr__(publish_service.settings, "ai_publish_remote_model", "deepseek-chat")

        def fake_build_remote_provider(model: str | None = None, purpose: str = "analysis") -> FakeProvider:
            captured["model"] = model or ""
            captured["purpose"] = purpose
            return FakeProvider()

        publish_service.build_remote_provider = fake_build_remote_provider
        metadata = publish_service.generate_publish_metadata(
            {
                "clip_title": "Great live moment",
                "task_name": "Demo task",
                "clip_summary": "Funny audience reaction and useful talking point.",
                "highlight_reason": "High replay value.",
            },
            use_ai=True,
        )
    finally:
        publish_service.build_remote_provider = original_builder
        object.__setattr__(publish_service.settings, "ai_default_provider", original_default_provider)
        object.__setattr__(publish_service.settings, "ai_publish_remote_model", original_publish_model)

    assert captured["model"] == "deepseek-chat"
    assert captured["purpose"] == "publish"
    assert "原标题" in captured["prompt"]
    assert metadata["source"] == "ai:remote-publish:deepseek-chat"
    assert metadata["title"] == "AI发布标题"
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
    direct_hashtags = publish_service._format_tags("#小S自夸 #美国往事 #陈亦飞爆料 #姐妹情深 #可爱自恋")  # noqa: SLF001
    assert direct_hashtags == "小S自夸, 美国往事, 陈亦飞爆料, 姐妹情深, 可爱自恋"
    assert publish_service._hashtags(direct_hashtags) == "#小S自夸 #美国往事 #陈亦飞爆料 #姐妹情深 #可爱自恋"  # noqa: SLF001


def test_douyin_description_includes_hashtag_topics() -> None:
    job = _fake_job("douyin")
    description = publish_service._douyin_description_for_job(job, "Fallback title")  # noqa: SLF001
    assert "A short live stream highlight" in description
    assert "#live #highlight" in description


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


def test_opencli_cmd_uses_node_entrypoint() -> None:
    original_opencli_executable = publish_service._opencli_executable
    original_which = publish_service.shutil.which
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            npm_dir = Path(temp_dir) / "npm"
            main_js = npm_dir / "node_modules" / "@jackwener" / "opencli" / "dist" / "src" / "main.js"
            main_js.parent.mkdir(parents=True)
            main_js.write_text("console.log('opencli')\n", encoding="utf-8")
            wrapper = npm_dir / "opencli.cmd"
            wrapper.write_text("@echo off\n", encoding="utf-8")
            publish_service._opencli_executable = lambda: str(wrapper)
            publish_service.shutil.which = lambda candidate: r"C:\Program Files\nodejs\node.exe" if candidate == "node" else None

            command = publish_service._opencli_command()  # noqa: SLF001

            assert command == [r"C:\Program Files\nodejs\node.exe", str(main_js)]
            assert "opencli.cmd" not in command
    finally:
        publish_service._opencli_executable = original_opencli_executable
        publish_service.shutil.which = original_which


def main() -> None:
    test_douyin_browser_commands()
    print("douyin browser commands: OK")
    test_douyin_description_copies_body_and_platform_topics_directly()
    print("douyin direct description topics: OK")
    test_bilibili_browser_commands()
    print("bilibili browser commands: OK")
    test_fallback_metadata()
    print("fallback metadata: OK")
    test_publish_metadata_ai_uses_publish_remote_interface_even_when_default_local()
    print("publish metadata remote interface routing: OK")
    test_publish_metadata_sanitizes_sensitive_words()
    print("publish metadata safety: OK")
    test_publish_tags_are_hashtag_topics()
    print("publish hashtag topics: OK")
    test_douyin_description_includes_hashtag_topics()
    print("douyin description hashtags: OK")
    test_caption_for_job_sanitizes_existing_dirty_content()
    print("existing dirty caption safety: OK")
    test_opencli_windows_npm_fallback()
    print("opencli windows npm fallback: OK")
    test_opencli_cmd_uses_node_entrypoint()
    print("opencli cmd node entrypoint: OK")


if __name__ == "__main__":
    main()

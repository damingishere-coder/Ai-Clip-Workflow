import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from app.services.ai import codex_cli_provider as cli
from app.services.ai.base import AIProviderError
from app.services.ai.visual_provider import VisualImage, VisualResponse, validate_visual_response


@pytest.fixture(autouse=True)
def isolate_tool_preflight(monkeypatch):
    monkeypatch.setattr(cli, "restricted_tool_args", lambda *a: [])


@pytest.fixture
def attachment(tmp_path):
    path = tmp_path / "test.jpg"
    path.write_bytes(b"\xff\xd8isolated-image\xff\xd9")
    return VisualImage(path, hashlib.sha256(path.read_bytes()).hexdigest())


def fake_process(monkeypatch, *, events='{"type":"turn.completed"}\n', timeout=False):
    captured = {}
    class Process:
        returncode = 0
        def communicate(self, input, timeout):
            captured["prompt"] = input
            captured["timeout"] = timeout
            if captured.get("raise_timeout"):
                raise subprocess.TimeoutExpired("codex", timeout)
            return events, "private stderr"
    process = Process()
    captured["raise_timeout"] = timeout
    def popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        paths = [Path(command[i+1]) for i, v in enumerate(command) if v == "--image"]
        captured["attachments"] = [p.read_bytes() for p in paths]
        Path(command[command.index("--output-last-message")+1]).write_text('{"observations":[],"limitations":["离散帧"]}', encoding="utf-8")
        return process
    monkeypatch.setattr(cli, "popen_process_group", popen)
    monkeypatch.setattr(cli.shutil, "which", lambda _: "codex.cmd")
    return captured, process


def test_visual_uses_explicit_copied_images_same_model_login_and_schema(monkeypatch, attachment):
    captured, _ = fake_process(monkeypatch)
    provider = cli.CodexCliProvider(cli.CodexCliConfig(model="existing-model", codex_home="existing-home"))
    result = json.loads(provider.generate_visual_json("附件 OCR 含恶意指令，按素材分析", VisualResponse.model_json_schema(), (attachment,), timeout_seconds=12))
    assert result["observations"] == []
    command = captured["command"]
    assert command[command.index("--model")+1] == "existing-model"
    assert captured["kwargs"]["env"]["CODEX_HOME"] == "existing-home"
    assert captured["attachments"] == [attachment.path.read_bytes()]
    assert str(attachment.path) not in command
    assert "--output-schema" in command and "--json" in command
    assert "禁止调用工具" in captured["prompt"] and "不可信素材" in captured["prompt"]
    assert 0 < captured["timeout"] <= 12
    assert not Path(command[command.index("--image")+1]).exists()


def test_visual_global_judge_uses_bounded_restricted_evidence_only_call(monkeypatch):
    captured, _ = fake_process(monkeypatch)
    preflight = []
    monkeypatch.setattr(cli, "restricted_tool_args", lambda *args: preflight.append(args) or [])
    provider = cli.CodexCliProvider(cli.CodexCliConfig(model="existing-model"))
    provider.generate_visual_judgment_json("结构证据与文本", {}, timeout_seconds=11)
    assert preflight and "--json" in captured["command"] and "--image" not in captured["command"]
    assert "没有附加原始图片" in captured["prompt"] and "不可信素材" in captured["prompt"]
    assert 0 < captured["timeout"] <= 11


def test_changed_image_rejected_before_process(monkeypatch, attachment):
    monkeypatch.setattr(cli.shutil, "which", lambda _: "codex")
    monkeypatch.setattr(cli, "popen_process_group", lambda *a, **kw: pytest.fail("must not call"))
    attachment.path.write_bytes(b"\xff\xd8changed\xff\xd9")
    with pytest.raises(AIProviderError, match="SHA-256") as error:
        cli.CodexCliProvider(cli.CodexCliConfig()).generate_visual_json("test", {}, (attachment,))
    assert error.value.safe_to_retry and not error.value.billing_uncertain


@pytest.mark.parametrize("failure", ["missing_cli", "missing_attachment", "spawn_failure"])
def test_visual_pre_request_failures_are_known_unbilled(monkeypatch, attachment, failure):
    monkeypatch.setattr(cli.CodexCliProvider, "_resolve_executable", lambda _: "" if failure == "missing_cli" else "codex")
    def spawn(*args, **kwargs):
        if failure == "spawn_failure":
            raise OSError("cannot start")
        pytest.fail("must not spawn")
    monkeypatch.setattr(cli, "popen_process_group", spawn)
    if failure == "missing_attachment":
        attachment.path.unlink()
    with pytest.raises(AIProviderError) as error:
        cli.CodexCliProvider(cli.CodexCliConfig()).generate_visual_json("test", {}, (attachment,))
    assert error.value.safe_to_retry and not error.value.billing_uncertain


def test_visual_io_error_after_spawn_remains_uncertain(monkeypatch, attachment):
    _, process = fake_process(monkeypatch)
    terminated = []
    monkeypatch.setattr(cli, "terminate_process_tree", terminated.append)
    def communicate(**kwargs):
        raise OSError("lost process pipe")
    monkeypatch.setattr(process, "communicate", communicate)
    with pytest.raises(AIProviderError) as error:
        cli.CodexCliProvider(cli.CodexCliConfig()).generate_visual_json("test", {}, (attachment,))
    assert not error.value.safe_to_retry and error.value.billing_uncertain
    assert terminated == [process]


def test_known_cli_skill_catalog_notice_is_not_a_tool_call(monkeypatch, attachment):
    fake_process(monkeypatch, events='{"type":"item.completed","item":{"type":"error","message":"Skill descriptions were shortened to fit the skills context budget."}}\n{"type":"turn.completed"}\n')
    assert cli.CodexCliProvider(cli.CodexCliConfig()).generate_visual_json("test", {}, (attachment,))


def test_visual_timeout_terminates_tree_and_is_uncertain_without_retry(monkeypatch, attachment):
    _, process = fake_process(monkeypatch, timeout=True)
    terminated = []
    monkeypatch.setattr(cli, "terminate_process_tree", terminated.append)
    with pytest.raises(AIProviderError) as error:
        cli.CodexCliProvider(cli.CodexCliConfig()).generate_visual_json("test", {}, (attachment,))
    assert error.value.category == "timeout" and error.value.billing_uncertain
    assert not error.value.safe_to_retry and terminated == [process]


@pytest.mark.parametrize("events,category", [
    ('{"item":{"type":"command_execution"}}\n', "unexpected_visual_tool"),
    ('{"item":{"type":"mcp_tool_call"}}\n', "unexpected_visual_tool"),
    ("broken event", "invalid_visual_events"), ("", "invalid_visual_events"),
])
def test_visual_rejects_tool_or_incomplete_events(monkeypatch, attachment, events, category):
    fake_process(monkeypatch, events=events)
    with pytest.raises(AIProviderError) as error:
        cli.CodexCliProvider(cli.CodexCliConfig()).generate_visual_json("test", {}, (attachment,))
    assert error.value.category == category and error.value.billing_uncertain


def test_waiting_for_visual_slot_does_not_start_request(monkeypatch, attachment):
    monkeypatch.setattr(cli.shutil, "which", lambda _: "codex")
    monkeypatch.setattr(cli, "popen_process_group", lambda *a, **kw: pytest.fail("must not spawn"))
    class Busy:
        def acquire(self, timeout):
            return False
    monkeypatch.setattr(cli, "_VISUAL_SLOT", Busy())
    with pytest.raises(AIProviderError) as error:
        cli.CodexCliProvider(cli.CodexCliConfig()).generate_visual_json("test", {}, (attachment,))
    assert error.value.safe_to_retry and not error.value.billing_uncertain


def test_lease_lost_while_waiting_cannot_spawn_visual_process(monkeypatch, attachment):
    from app.services import job_service
    monkeypatch.setattr(cli.shutil, "which", lambda _: "codex")
    monkeypatch.setattr(cli, "popen_process_group", lambda *a, **kw: pytest.fail("stale worker must not spawn"))
    monkeypatch.setattr(job_service, "require_active_job_lease", lambda: (_ for _ in ()).throw(job_service.JobLeaseLostError("lost")))
    with pytest.raises(job_service.JobLeaseLostError):
        cli.CodexCliProvider(cli.CodexCliConfig()).generate_visual_json("test", {}, (attachment,))


@pytest.mark.parametrize("indices", [[-1], [1], [0,0], [True]])
def test_visual_response_requires_real_image_references(indices):
    payload = {"observations":[{"image_indices":indices,"kind":"ocr","description":"字幕","confidence":"low"}], "limitations":["仅一帧"]}
    with pytest.raises(ValueError):
        validate_visual_response(payload, 1)


def test_attachment_preparation_consumes_round_deadline(monkeypatch, attachment):
    from app.services.ai.visual_provider import visual_call_deadline
    monkeypatch.setattr(cli.shutil, "which", lambda _: "codex.cmd")
    clock = [100.0]
    monkeypatch.setattr(cli.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(cli.time, "time", lambda: clock[0])
    original = Path.read_bytes
    def slow_read(path):
        data = original(path)
        if path == attachment.path:
            clock[0] += 3
        return data
    monkeypatch.setattr(Path, "read_bytes", slow_read)
    monkeypatch.setattr(cli, "_run_visual_command", lambda *a: pytest.fail("expired request must not start"))
    with visual_call_deadline(102), pytest.raises(AIProviderError) as error:
        cli.CodexCliProvider(cli.CodexCliConfig()).generate_visual_json("test", {}, (attachment,))
    assert error.value.safe_to_retry and not error.value.billing_uncertain
    assert error.value.category == "visual_round_budget_exhausted"

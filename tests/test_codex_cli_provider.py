from __future__ import annotations

from pathlib import Path
import json
import subprocess

import pytest

from app.services.ai.base import AIProviderError, generate_json_with_safe_retry
from app.services.ai.codex_cli_provider import CodexCliConfig, CodexCliProvider
from app.services.ai.variety_comedy_analyzer import RECALL_OUTPUT_SCHEMA, _generate_payload


def test_codex_cli_provider_reads_strict_json_from_output_file(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr("app.services.ai.codex_cli_provider.shutil.which", lambda _: "codex.cmd")

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["input"] = kwargs["input"]
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text('```json\n{"status":"ok"}\n```', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("app.services.ai.codex_cli_provider.subprocess.run", fake_run)

    provider = CodexCliProvider(CodexCliConfig(model="gpt-test", timeout_seconds=30))
    assert provider.generate_json("分析这段文本") == '{"status":"ok"}'
    assert "--sandbox" in captured["command"]
    assert "--ephemeral" in captured["command"]
    assert "<user_material>" in captured["input"]


def test_codex_cli_provider_rejects_non_json(monkeypatch) -> None:
    monkeypatch.setattr("app.services.ai.codex_cli_provider.shutil.which", lambda _: "codex.cmd")

    def fake_run(command, **kwargs):
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text("不是 JSON", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("app.services.ai.codex_cli_provider.subprocess.run", fake_run)

    with pytest.raises(AIProviderError, match="不是合法 JSON"):
        CodexCliProvider(CodexCliConfig()).generate_json("测试")


def test_recall_passes_schema_to_cli_without_changing_model_or_login(monkeypatch):
    monkeypatch.setattr("app.services.ai.codex_cli_provider.shutil.which", lambda _: "codex.cmd")
    def fake_run(command, **kwargs):
        schema = json.loads(Path(command[command.index("--output-schema") + 1]).read_text(encoding="utf-8"))
        assert schema == RECALL_OUTPUT_SCHEMA
        assert command[command.index("--model") + 1] == "gpt-test"
        assert kwargs["env"]["CODEX_HOME"] == "test-home"
        Path(command[command.index("--output-last-message") + 1]).write_text('{"moments":[]}', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")
    monkeypatch.setattr("app.services.ai.codex_cli_provider.subprocess.run", fake_run)
    provider = CodexCliProvider(CodexCliConfig(model="gpt-test", codex_home="test-home"))
    assert _generate_payload(provider, "召回", expected_key="moments", output_schema=RECALL_OUTPUT_SCHEMA) == {"moments": []}


def test_invalid_response_is_preserved_without_prompt_or_automatic_retry(monkeypatch, tmp_path):
    monkeypatch.setattr("app.services.ai.codex_cli_provider.shutil.which", lambda _: "codex.cmd")
    calls = []
    def fake_run(command, **kwargs):
        calls.append(command)
        Path(command[command.index("--output-last-message") + 1]).write_text('格式错误的原始返回', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "private stderr")
    monkeypatch.setattr("app.services.ai.codex_cli_provider.subprocess.run", fake_run)
    provider = CodexCliProvider(CodexCliConfig(diagnostics_dir=str(tmp_path)))
    with pytest.raises(AIProviderError) as error:
        generate_json_with_safe_retry(provider, "private prompt", output_schema=RECALL_OUTPUT_SCHEMA)
    assert error.value.billing_uncertain
    assert error.value.category == "invalid_response_json"
    assert len(calls) == 1
    files = list(tmp_path.glob("invalid-json-*.json"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    record = json.loads(text)
    assert record["raw_output"] == '格式错误的原始返回'
    assert len(record["request_sha256"]) == 64
    assert "private prompt" not in text and "private stderr" not in text
    assert str(files[0]) in str(error.value)


def test_schema_hint_keeps_other_provider_interface_compatible():
    class OtherProvider:
        def generate_json(self, prompt):
            return '{"moments":[]}'
    assert _generate_payload(OtherProvider(), "召回", expected_key="moments", output_schema=RECALL_OUTPUT_SCHEMA) == {"moments": []}

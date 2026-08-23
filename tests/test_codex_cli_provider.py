from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from app.services.ai.base import AIProviderError
from app.services.ai.codex_cli_provider import CodexCliConfig, CodexCliProvider


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

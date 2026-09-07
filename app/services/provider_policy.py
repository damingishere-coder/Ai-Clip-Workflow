"""应用唯一模型边界。历史适配器仅供代码回退，不再允许执行。"""

from app.services.ai.base import AIProviderError


def require_codex(provider: str | None) -> str:
    if (provider or "codex").strip().lower() != "codex":
        raise AIProviderError(
            "此模型接口已停用，请明确选择 Codex 重新分析；旧结果仍保留，不能继续旧接口检查点。",
            category="provider_disabled",
        )
    return "codex"


def reject_legacy_provider() -> None:
    require_codex("disabled")

import pytest

from app.services.ai import visual_cli_policy as policy
from app.services.ai.base import AIProviderError


def test_visual_policy_disables_all_effective_mcp_servers_without_global_writes(monkeypatch):
    calls = []
    def inventory(executable, args, environment, directory, timeout):
        calls.append(list(args))
        return [{"name":"first", "enabled":len(calls)==1}, {"name":"second_name", "enabled":False}]
    monkeypatch.setattr(policy, "_list_servers", inventory)
    args = policy.restricted_tool_args("existing-codex", {"CODEX_HOME":"unchanged"}, "private-dir", 20)
    assert len(calls) == 2 and args == calls[1]
    for key in policy.DISABLED_FEATURES:
        assert f"features.{key}=false" in args
    assert 'web_search="disabled"' in args and 'agents.enabled=false' in args
    assert 'mcp_servers.first.enabled=false' in args and 'mcp_servers.second_name.enabled=false' in args
    assert not any("model" in value or "auth" in value or "provider" in value for value in args)


def test_effective_mcp_that_cannot_be_disabled_prevents_model_call(monkeypatch):
    monkeypatch.setattr(policy, "_list_servers", lambda *a:[{"name":"managed", "enabled":True}])
    with pytest.raises(AIProviderError) as error:
        policy.restricted_tool_args("codex", {}, "private", 20)
    assert error.value.category == "visual_tools_unavailable"
    assert error.value.safe_to_retry and not error.value.billing_uncertain


def test_preflight_budget_failure_is_unbilled(monkeypatch):
    monkeypatch.setattr(policy, "_list_servers", lambda *a: (_ for _ in ()).throw(ValueError("unknown config")))
    with pytest.raises(AIProviderError) as error:
        policy.restricted_tool_args("codex", {}, "private", 20)
    assert error.value.safe_to_retry and not error.value.billing_uncertain


def test_mcp_name_that_cli_cannot_address_safely_is_rejected(monkeypatch):
    monkeypatch.setattr(policy, "_list_servers", lambda *a:[{"name":"a.b", "enabled":True}])
    with pytest.raises(AIProviderError, match="隔离"):
        policy.restricted_tool_args("codex", {}, "private", 20)

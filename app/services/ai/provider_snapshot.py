"""Freeze non-secret provider identity; configuration drift fails before calls."""

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
from urllib.parse import urlsplit

from app.services.ai.unit_checkpoint import provider_fingerprint_fields

_expected = ContextVar("analysis_provider_snapshot", default=None)


class ProviderConfigurationChangedError(ValueError):
    """The queued configuration must be restored before this job can resume."""


def describe(provider, name):
    config = getattr(provider, "config", None)
    fields = provider_fingerprint_fields(provider)
    # URLs may contain userinfo/query credentials. Identity hashes still detect
    # drift without persisting those credentials or local authentication paths.
    for key in ("base_url", "responses_path", "codex_home", "executable"):
        raw = fields.pop(key, "")
        fields[key + "_sha256"] = hashlib.sha256(raw.encode()).hexdigest()
        if key == "base_url":
            try:
                url = urlsplit(raw)
                fields["endpoint_origin"] = f"{url.scheme}://{url.hostname or ''}" if raw else ""
            except ValueError:
                fields["endpoint_origin"] = "自定义地址"
    return {"name": name, "fields": {**fields,
        "timeout_seconds": getattr(config, "timeout_seconds", None),
        "disable_response_storage": getattr(config, "disable_response_storage", None)}}


def capture(name):
    from app.services.ai.ai_clip_analyzer import _build_provider
    return describe(_build_provider(name), name)


def verify_built_provider(provider, name, purpose):
    expected = _expected.get()
    if purpose == "analysis" and expected is not None and describe(provider, name) != expected:
        raise ProviderConfigurationChangedError("AI Provider 或模型配置已变化，已阻止混用排队时的策略；请恢复排队时的配置后重试，或另建任务使用当前配置")


@contextmanager
def enforce(snapshot):
    # Pre-upgrade jobs lack provider identity; preserve their checkpoint semantics.
    if snapshot is not None and capture(snapshot["name"]) != snapshot:
        raise ProviderConfigurationChangedError("AI Provider 或模型配置与冻结快照不一致，尚未调用模型；请恢复排队时的配置后重试，或另建任务使用当前配置")
    token = _expected.set(snapshot)
    try:
        yield
    finally:
        _expected.reset(token)

import json
from dataclasses import replace
from uuid import uuid4

import pytest

from app.core.config import settings
from app.db.database import get_connection
from app.models.task import TaskCreate
from app.services import content_profile_service as profiles, job_service
from app.services.ai import ai_clip_analyzer as providers, provider_snapshot
from app.services.ai_prompt_preset_service import get_task_ai_prompt_snapshot
from app.services.task_lifecycle_service import create_task_record


@pytest.fixture
def task_id():
    tid = "provider-" + uuid4().hex[:12]
    create_task_record(TaskCreate(task_name="知识 Provider", selection_profile="knowledge_opinion",
                                 ai_prompt_preset_id="preset_002", ai_provider="local"), task_id=tid)
    yield tid
    with get_connection() as c:
        c.execute("DELETE FROM workflow_jobs WHERE task_id=?", (tid,))
        c.execute("DELETE FROM task_generation_rules WHERE task_id=?", (tid,))
        c.execute("DELETE FROM tasks WHERE id=?", (tid,))
        c.commit()


def test_task_prompt_and_provider_are_used_by_auto_job(task_id):
    assert get_task_ai_prompt_snapshot(task_id)["id"] == "preset_002"
    job = job_service.create_job(task_id, job_service.JOB_TYPE_AUTO_PIPELINE)
    frozen = profiles.read_job_snapshot(job)
    assert frozen["provider"] == "local"
    assert frozen["provider_identity"] == provider_snapshot.capture("local")
    assert frozen["prompt"]["id"] == "preset_002"
    assert frozen["selection"]["selection_profile"] == "knowledge_opinion"


def test_explicit_analysis_provider_overrides_task_default_but_not_config(task_id):
    original = settings.ai_codex_model
    job = job_service.create_job(task_id, job_service.JOB_TYPE_AI_ANALYSIS, {"provider": "codex"})
    frozen = profiles.read_job_snapshot(job)
    assert frozen["provider"] == "codex"
    assert settings.ai_codex_model == original
    with get_connection() as c:
        assert profiles._task_provider(c, task_id)["name"] == "local"


def test_configuration_drift_stops_before_provider_use(task_id, monkeypatch):
    job = job_service.create_job(task_id, job_service.JOB_TYPE_AUTO_PIPELINE)
    frozen = profiles.read_job_snapshot(job)
    with provider_snapshot.enforce(frozen["provider_identity"]):
        assert providers.build_provider("local").config.model == settings.ai_local_model
    monkeypatch.setattr(providers, "settings", replace(settings, ai_local_model="different-model"))
    called = False
    with pytest.raises(ValueError, match="尚未调用模型"):
        with provider_snapshot.enforce(frozen["provider_identity"]):
            called = True
    assert not called
    # The queued evidence is not rewritten to hide the mismatch.
    assert profiles.read_job_snapshot(job_service.get_job(job["id"])) == frozen


def test_provider_drift_during_analysis_is_also_checked(task_id, monkeypatch):
    frozen = provider_snapshot.capture("local")
    with provider_snapshot.enforce(frozen):
        monkeypatch.setattr(providers, "settings", replace(settings, ai_local_protocol="changed-protocol"))
        with pytest.raises(ValueError, match="配置已变化"):
            providers.build_provider("local")


def test_no_secrets_are_frozen(task_id, monkeypatch):
    marker = "never-store-this-secret"
    monkeypatch.setattr(providers, "settings", replace(settings, ai_analysis_remote_api_key=marker))
    job = job_service.create_job(task_id, job_service.JOB_TYPE_AI_ANALYSIS, {"provider": "remote"})
    serialized = json.dumps(job["payload_json"])
    assert marker not in serialized and '"api_key"' not in serialized


def test_url_credentials_and_auth_directory_are_hashed(task_id, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    marker = "private-query-credential"
    monkeypatch.setattr(providers, "settings", replace(settings,
        ai_analysis_remote_base_url=f"https://user:{marker}@api.example.test/v1?token={marker}",
        ai_analysis_remote_responses_path=f"/v1/responses?key={marker}"))
    job = job_service.create_job(task_id, job_service.JOB_TYPE_AI_ANALYSIS, {"provider": "remote"})
    response = TestClient(app).get(f"/api/tasks/jobs/{job['id']}")
    assert response.status_code == 200
    assert marker not in response.text and "user:" not in response.text
    identity = profiles.read_job_snapshot(job)["provider_identity"]
    assert identity["fields"]["endpoint_origin"] == "https://api.example.test"
    assert "base_url" not in identity["fields"]


@pytest.mark.parametrize("requested_provider", [None, "remote"])
def test_manual_default_route_uses_task_provider_and_drift_retry_retains_evidence(task_id, monkeypatch, requested_provider):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.models.task import TaskStatus
    from app.services import ai_analysis_workflow_service as workflow
    from app.services.task_lifecycle_service import update_task_status
    from app.services.storage_service import get_artifact_paths
    update_task_status(task_id, TaskStatus.pending_ai)
    transcript = get_artifact_paths(task_id)["transcript_path"]
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text("00:00:00 - 00:01:00 隔离文字", encoding="utf-8")
    client = TestClient(app)
    url = f"/api/tasks/{task_id}/process/ai" + (f"?provider={requested_provider}" if requested_provider else "")
    response = client.post(url)
    assert response.status_code == 200
    job = response.json()["job"]
    frozen = profiles.read_job_snapshot(job)
    assert frozen["provider"] == (requested_provider or "local")
    monkeypatch.setattr(providers, "settings", replace(settings, ai_local_model="changed-model", ai_analysis_remote_model="changed-model"))
    monkeypatch.setattr(workflow, "_analyze_with_provider", lambda *_: pytest.fail("配置漂移时不能调用 Analyzer"))
    claimed = job_service.claim_job(job["id"], "drift-owner")
    with job_service.job_lease_context(job["id"], "drift-owner", claimed["lease_token"]):
        with pytest.raises(ValueError, match="恢复排队时的配置") as error:
            workflow.process_task_ai_analysis(task_id)
        assert isinstance(error.value.__cause__, provider_snapshot.ProviderConfigurationChangedError)
        assert '点击' not in str(error.value)
        job_service.mark_job_failed(job["id"], str(error.value), lease_owner="drift-owner", lease_token=claimed["lease_token"])
    assert job_service.get_job(job["id"])["status"] == job_service.JOB_STATUS_FAILED
    retry = client.post(f"/api/tasks/{task_id}/process/ai")
    assert retry.status_code == 200 and retry.json()["job_id"] == job["id"]
    assert retry.json()["job"]["status"] == job_service.JOB_STATUS_QUEUED
    assert profiles.read_job_snapshot(retry.json()["job"]) == frozen
    with get_connection() as c:
        assert not c.execute("SELECT 1 FROM ai_analysis_runs WHERE task_id=?", (task_id,)).fetchone()


def test_old_job_without_provider_identity_remains_readable(task_id):
    job = job_service.create_job(task_id, job_service.JOB_TYPE_AI_ANALYSIS, {"provider": "local"})
    evidence = job["payload_json"][profiles.JOB_SNAPSHOT_KEY]
    evidence["snapshot"].pop("provider_identity")
    evidence["sha256"] = profiles._snapshot_hash(evidence["snapshot"])
    assert "provider_identity" not in profiles.read_job_snapshot(job)
    with provider_snapshot.enforce(None):
        assert providers.build_provider("local")


def test_archived_prompt_cannot_be_used_during_task_creation():
    with pytest.raises(ValueError, match="归档"):
        create_task_record(TaskCreate(task_name="拒绝归档", selection_profile="knowledge_opinion", ai_prompt_preset_id="preset_004"))


@pytest.mark.parametrize("payload", [[], {profiles.JOB_SNAPSHOT_KEY: []}, {profiles.JOB_SNAPSHOT_KEY: {"snapshot": []}}])
def test_damaged_retry_payload_is_rejected(task_id, payload):
    from app.models.task import TaskStatus
    from app.services import ai_analysis_workflow_service as workflow
    from app.services.task_lifecycle_service import update_task_status
    from app.services.storage_service import get_artifact_paths
    update_task_status(task_id, TaskStatus.pending_ai)
    transcript = get_artifact_paths(task_id)["transcript_path"]
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text("隔离转写", encoding="utf-8")
    job = job_service.create_job(task_id, job_service.JOB_TYPE_AI_ANALYSIS)
    with get_connection() as c:
        c.execute("UPDATE workflow_jobs SET status=?, payload_json=? WHERE id=?",
                  (job_service.JOB_STATUS_FAILED, json.dumps(payload), job["id"]))
        c.commit()
    with pytest.raises(workflow.AIAnalysisConflictError, match="账本已损坏"):
        workflow.queue_task_ai_analysis(task_id)

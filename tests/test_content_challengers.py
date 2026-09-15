from concurrent.futures import ThreadPoolExecutor
import sqlite3
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.db import database as db
from app.main import app
from app.models.content_challenger import ChallengerDraftCreate
from app.services import content_challenger_service as service
from app.services.content_intelligence_service import create_report
from app.services.content_review_service import ContentReviewError
from app.services.ai_prompt_preset_service import list_ai_prompt_presets
from tests.test_human_review import human_db as _human_db_fixture

human_db = _human_db_fixture


def draft_input():
    report = create_report("", 30, str(uuid4()))
    context = service.draft_context(report["id"], "variety_comedy")
    return ChallengerDraftCreate(report_id=report["id"], report_sha256=report["payload_sha256"],
        profile_id="variety_comedy", champion_preset_id=context["baseline"]["preset_id"],
        baseline_sha256=context["baseline"]["sha256"], name="开场试验", hypothesis="减少开场铺垫可能更容易理解",
        prompt_text=context["baseline"]["prompt_text"] + "\n优先选择开头上下文完整的片段。", request_key=uuid4())


def test_human_draft_freezes_all_versions_without_changing_production(human_db):
    payload = draft_input()
    with db.get_connection() as c:
        presets = [tuple(r) for r in c.execute("SELECT * FROM ai_prompt_presets ORDER BY id")]
        heads = [tuple(r) for r in c.execute("SELECT * FROM content_profiles ORDER BY id")]
    draft = service.create_draft(payload)
    assert draft["status"] == "draft"
    assert draft["evidence"]["profile_scoring_changed"] is False
    assert draft["evidence"]["performance_status"] == "no_account"
    assert draft["evidence"]["author_source"] == "explicit_human_input"
    assert "+优先选择" in draft["evidence"]["diff"]
    assert "scoring" in draft["evidence"]["baseline"]["profile"]
    assert draft == service.get_challenger(draft["id"])
    assert draft["challenger_preset_id"] not in {r["id"] for r in list_ai_prompt_presets()}
    with db.get_connection() as c:
        after = [tuple(r) for r in c.execute("SELECT * FROM ai_prompt_presets WHERE id!=? ORDER BY id", (draft["challenger_preset_id"],))]
        assert presets == after
        assert heads == [tuple(r) for r in c.execute("SELECT * FROM content_profiles ORDER BY id")]
        assert c.execute("SELECT is_archived,is_default FROM ai_prompt_presets WHERE id=?", (draft["challenger_preset_id"],)).fetchone()[:] == (1, 0)
        for table in ("tasks", "workflow_jobs", "publish_jobs", "content_improvement_experiments"):
            assert c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_draft_concurrent_retry_returns_same_record_and_rejects_changed_request(human_db):
    payload = draft_input()
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(lambda _: service.create_draft(payload), range(2)))
    assert values[0] == values[1]
    changed = payload.model_copy(update={"hypothesis": "另一假设"})
    with pytest.raises(ContentReviewError, match="重复请求"):
        service.create_draft(changed)
    with db.get_connection() as c:
        assert c.execute("SELECT COUNT(*) FROM content_strategy_challengers").fetchone()[0] == 1


def test_stale_baseline_and_report_or_prompt_tampering_fail_closed(human_db):
    payload = draft_input()
    with db.get_connection() as c:
        c.execute("UPDATE ai_prompt_presets SET prompt_text=prompt_text||'人工改动' WHERE id='preset_001'")
        c.commit()
    with pytest.raises(ContentReviewError, match="正式策略.*已改变"):
        service.create_draft(payload)
    fresh = draft_input()
    draft = service.create_draft(fresh)
    with db.get_connection() as c:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            c.execute("UPDATE content_strategy_challengers SET evidence_json='{}'")
        c.execute("UPDATE ai_prompt_versions SET prompt_text='tampered' WHERE id=?", (draft["challenger_prompt_version_id"],))
        c.commit()
    with pytest.raises(ContentReviewError, match="版本证据损坏"):
        service.get_challenger(draft["id"])


def test_draft_api_requires_actual_change_and_does_not_create_trials(human_db):
    client = TestClient(app)
    payload = draft_input()
    context = client.get("/api/content-review/challengers/context", params={"report_id": payload.report_id, "profile_id": payload.profile_id}).json()
    same = payload.model_copy(update={"prompt_text": context["baseline"]["prompt_text"]})
    assert client.post("/api/content-review/challengers", json=same.model_dump(mode="json")).status_code == 422
    response = client.post("/api/content-review/challengers", json=payload.model_dump(mode="json"))
    assert response.status_code == 200
    draft = response.json()
    assert client.get(f"/api/content-review/challengers/{draft['id']}").json() == draft
    assert client.get("/api/content-review/challengers", params={"report_id": payload.report_id}).json()["challengers"][0]["id"] == draft["id"]
    with db.get_connection() as c:
        c.execute("UPDATE content_intelligence_reports SET payload_json='{}' WHERE id=?", (payload.report_id,))
        c.commit()
    assert client.get(f"/api/content-review/challengers/{draft['id']}").status_code == 409

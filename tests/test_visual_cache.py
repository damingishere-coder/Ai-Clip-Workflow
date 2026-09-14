from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.db.database import get_connection
from app.main import app
from app.services import job_service, visual_cache_service as cache
from tests.test_visual_evidence import visual_task as _visual_task_fixture, lease, prepare, analyze  # noqa: F401

visual_task = _visual_task_fixture


def complete(c):
    with lease(c):
        row = prepare(c)
        row = analyze(c, row)
        job_service.mark_job_completed(c.job["id"], {})
    return row


def cleanup(days):
    return cache.cleanup_visual_cache(now=datetime.now(timezone.utc) + timedelta(days=days), seconds=10, limit=1000)


def row_state(row):
    with get_connection() as connection:
        return dict(connection.execute("SELECT * FROM candidate_visual_evidence WHERE id=?", (row["id"],)).fetchone())


def test_completed_frames_expire_after_seven_days_but_keep_structured_evidence(visual_task):
    c = visual_task
    row = complete(c)
    manifest = c.directory / f"{uuid4().hex}.json"
    manifest.write_text('{"preserve":true}')
    cleanup(6)
    assert c.path.exists() and not row_state(row)["cache_cleaned_at"]
    cleanup(8)
    after = row_state(row)
    assert not c.path.exists() and manifest.exists() and after["cache_cleaned_at"]
    assert after["request_json"] == row["request_json"] and after["evidence_json"] == row["evidence_json"]
    cleanup(8)
    with pytest.raises(ValueError, match="清理"):
        cache.pin_visual(c.task_id, row["id"], True)


def test_pinned_candidate_protects_entire_shared_directory(visual_task):
    c = visual_task
    with lease(c):
        first = prepare(c)
        analyze(c, first)
        from app.services.visual_evidence_service import prepare_candidate_visual
        second = prepare_candidate_visual(task_id=c.task_id, candidate_key="second", input_fingerprint="b"*64, sampling=c.sampling, cache_directory=c.directory, provider=c.provider)
        analyze(c, second)
        job_service.mark_job_completed(c.job["id"], {})
    cache.pin_visual(c.task_id, first["id"], True)
    cleanup(8)
    assert c.path.exists() and not row_state(second)["cache_cleaned_at"]
    cache.pin_visual(c.task_id, first["id"], False)
    cleanup(8)
    assert not c.path.exists() and row_state(second)["cache_cleaned_at"]


@pytest.mark.parametrize("protection", ["queued", "expired_running", "uncertain", "checkpoint_uncertain"])
def test_active_or_uncertain_evidence_is_never_cleaned(visual_task, protection):
    c = visual_task
    row = complete(c)
    with get_connection() as connection:
        if protection == "queued":
            connection.execute("UPDATE workflow_jobs SET status='queued' WHERE id=?", (c.job["id"],))
        elif protection == "expired_running":
            connection.execute("UPDATE workflow_jobs SET status='running',lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (c.job["id"],))
        elif protection == "uncertain":
            connection.execute("UPDATE candidate_visual_evidence SET status='unavailable',call_status='uncertain' WHERE id=?", (row["id"],))
        else:
            connection.execute("UPDATE workflow_jobs SET checkpoint_json=? WHERE id=?", ('{"_ai_analysis_units_v1":{"namespaces":{"optional-visual-global-v1":{"units":{"global":{"status":"uncertain"}}}}}}', c.job["id"]))
        connection.commit()
    cleanup(10)
    assert c.path.exists() and not row_state(row)["cache_cleaned_at"]


def test_failed_uncalled_cache_expires_after_one_day(visual_task):
    c = visual_task
    with lease(c):
        row = prepare(c)
        from app.services.visual_evidence_service import _update_result
        _update_result(c.task_id, row, status="unavailable", call_status="not_called", failure="visual_round_budget_exhausted")
        job_service.mark_job_failed(c.job["id"], "stopped before model")
    cleanup(.5)
    assert c.path.exists()
    cleanup(2)
    assert not c.path.exists() and row_state(row)["cache_cleaned_at"] and c.provider.calls == 0


def test_orphan_cleanup_keeps_manifest_external_source_and_young_files(visual_task):
    c = visual_task
    manifest = c.directory / f"{uuid4().hex}.json"
    manifest.write_text("{}")
    outside = c.directory.parents[2] / f"{uuid4().hex}.jpg"
    outside.write_bytes(b"original")
    with lease(c):
        job_service.mark_job_completed(c.job["id"], {})
    cleanup(.5)
    assert c.path.exists()
    cleanup(2)
    assert not c.path.exists() and manifest.exists() and outside.read_bytes() == b"original"


def test_partial_cleanup_failure_is_visible_and_idempotent(visual_task, monkeypatch):
    c = visual_task
    row = complete(c)
    from app.services.visual_cache_files import CacheFiles
    original = CacheFiles.remove
    def deny(files, name):
        if files.directory / name == c.path:
            raise PermissionError("locked")
        return original(files, name)
    with monkeypatch.context() as scope:
        scope.setattr(CacheFiles, "remove", deny)
        cleanup(8)
    after = row_state(row)
    assert c.path.exists() and after["cache_cleanup_error"] == "PermissionError" and not after["cache_cleaned_at"]
    cleanup(8)
    assert not c.path.exists() and not row_state(row)["cache_cleanup_error"]


def test_pin_racing_cleanup_never_claims_to_preserve_missing_frames(visual_task):
    c = visual_task
    row = complete(c)
    def pin():
        try:
            return cache.pin_visual(c.task_id, row["id"], True)["pinned"]
        except (ValueError, OSError):
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(pin)
        cleaned = pool.submit(cleanup, 8)
        pinned = future.result()
        cleaned.result()
    assert c.path.exists() == pinned
    assert bool(row_state(row)["pinned"]) == pinned


def test_frame_api_checks_owner_integrity_and_cleaned_state(visual_task):
    c = visual_task
    row = complete(c)
    client = TestClient(app)
    url = f"/api/tasks/{c.task_id}/visual-evidence/{row['id']}/frames/0"
    response = client.get(url)
    assert response.status_code == 200 and response.content == c.path.read_bytes()
    assert response.headers["cache-control"] == "private, no-store"
    assert client.get(url.replace(c.task_id, "other-task")).status_code == 404
    assert client.get(url[:-1] + "-1").status_code == 404
    c.path.write_bytes(b"corrupt")
    assert client.get(url).status_code == 404
    assert c.provider.calls == 1


def test_cache_directory_symlink_never_deletes_external_images(visual_task, tmp_path):
    c = visual_task
    complete(c)
    target = tmp_path / "outside"
    target.mkdir()
    protected = target / c.path.name
    protected.write_bytes(b"outside")
    c.path.unlink()
    c.directory.rmdir()
    try:
        c.directory.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("需要 Windows 符号链接权限")
    cleanup(8)
    assert protected.read_bytes() == b"outside"


def test_pending_uncalled_evidence_is_protected_after_job_failure(visual_task):
    c = visual_task
    with lease(c):
        row = prepare(c)
        job_service.mark_job_failed(c.job["id"], "interrupted before call")
    cleanup(10)
    assert c.path.exists() and not row_state(row)["cache_cleaned_at"]


def test_cache_directory_swap_after_validation_cannot_delete_external(visual_task, tmp_path, monkeypatch):
    c = visual_task
    row = complete(c)
    target = tmp_path / "outside-race"
    target.mkdir()
    protected = target / c.path.name
    protected.write_bytes(b"outside")
    original = cache.cache_directory
    swapped = []
    def swap(task, relative):
        directory = original(task, relative)
        if not swapped:
            c.path.unlink()
            c.directory.rmdir()
            try:
                c.directory.symlink_to(target, target_is_directory=True)
            except OSError:
                pytest.skip("需要 Windows 符号链接权限")
            swapped.append(True)
        return directory
    monkeypatch.setattr(cache, "cache_directory", swap)
    cleanup(8)
    assert protected.read_bytes() == b"outside"
    assert row_state(row)["cache_cleanup_error"] and not row_state(row)["cache_cleaned_at"]


def test_orphan_maintenance_rotates_beyond_first_task_page():
    from app.models.task import TaskCreate
    from app.services.task_lifecycle_service import create_task_record
    from app.services.storage_service import get_task_directory
    prefix = "zz-visual-" + uuid4().hex
    created = []
    try:
        for index in range(2):
            task = create_task_record(TaskCreate(task_name="轮转测试", selection_profile="general"), task_id=f"{prefix}-{index}")
            directory = get_task_directory(task["id"], task["task_dir_name"]) / "analysis" / "visual" / uuid4().hex
            directory.mkdir(parents=True)
            frame = directory / (uuid4().hex + ".jpg")
            frame.write_bytes(b"old orphan")
            created.append((task["id"], frame))
        now = datetime.now(timezone.utc) + timedelta(days=2)
        first = cache.cleanup_visual_cache(now=now, seconds=10, limit=1, task_cursor=prefix)
        assert first["next_task_cursor"] == created[0][0]
        assert not created[0][1].exists() and created[1][1].exists()
        second = cache.cleanup_visual_cache(now=now, seconds=10, limit=1, task_cursor=first["next_task_cursor"])
        assert second["next_task_cursor"] == created[1][0] and not created[1][1].exists()
    finally:
        with get_connection() as connection:
            for task_id, _ in created:
                connection.execute("DELETE FROM task_generation_rules WHERE task_id=?", (task_id,))
                connection.execute("DELETE FROM tasks WHERE id=?", (task_id,))
            connection.commit()


def test_task_root_swap_cannot_reanchor_cache_outside_storage(visual_task, tmp_path, monkeypatch):
    c = visual_task
    row = complete(c)
    outside = tmp_path / "outside-root"
    target = outside / row["cache_relative_dir"]
    target.mkdir(parents=True)
    protected = target / c.path.name
    protected.write_bytes(c.path.read_bytes())
    link = tmp_path / "replaced-task-root"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("需要 Windows 符号链接权限")
    # 模拟既有存储校验返回之后、缓存服务处理之前的根目录替换。
    monkeypatch.setattr(cache, "get_task_directory", lambda *a: link)
    with pytest.raises(ValueError):
        cache.read_visual_frame(c.task_id, row["id"], 0)
    cleanup(8)
    assert protected.exists() and not row_state(row)["cache_cleaned_at"]

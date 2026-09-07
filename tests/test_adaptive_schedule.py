from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core.config import settings
from app.db.database import get_connection, init_db
from app.services import adaptive_schedule as service


NOW = datetime.fromisoformat("2026-09-06T20:30:00+08:00")


@pytest.fixture
def db(tmp_path, monkeypatch):
    original = settings.database_path
    object.__setattr__(settings, "database_path", tmp_path / "adaptive.sqlite3")
    init_db()
    monkeypatch.setattr(service, "_now", lambda: NOW)
    with get_connection() as c:
        for account, name in [("target", "康熙来了"), ("other", "其他账号")]:
            c.execute(
                "INSERT INTO publish_accounts(id,platform,account_name,created_at,updated_at) VALUES(?,'douyin',?,?,?)",
                (account, name, NOW.isoformat(), NOW.isoformat()),
            )
        c.commit()
    yield
    object.__setattr__(settings, "database_path", original)


def job(i, time=None, status="SCHEDULED", managed=True, account="target", **extra):
    return dict(
        id=str(i),
        account_id=account,
        platform="douyin",
        status=status,
        scheduled_at=time,
        published_at=None,
        adaptive_managed=int(managed),
        claimed_at=None,
        needs_manual_review=0,
        updated_at=NOW.isoformat(),
        created_at=NOW.isoformat(),
        **extra,
    )


def seed_job(row):
    with get_connection() as c:
        c.execute(
            "INSERT INTO tasks(id,task_name,status,created_at,updated_at) VALUES(?,?,'pending_review',?,?)",
            (row["id"], row["id"], NOW.isoformat(), NOW.isoformat()),
        )
        c.execute(
            "INSERT INTO output_clip(id,task_id,created_at,updated_at) VALUES(?,?,?,?)",
            (row["id"], row["id"], NOW.isoformat(), NOW.isoformat()),
        )
        row = {**row, "output_clip_id": row["id"]}
        columns = ",".join(["task_id", *row.keys()])
        c.execute(
            f"INSERT INTO publish_jobs({columns}) VALUES({','.join('?' for _ in range(len(row) + 1))})",
            [row["id"], *row.values()],
        )
        c.commit()


def scores():
    return dict(
        ready=True,
        sample_count=60,
        reason="test",
        bins=[dict(bin=i, count=5, score=3 if i == 5 else 1) for i in range(12)],
    )


def test_default_fallback_capacity_order_and_gap():
    rows = [job(i, status="WAITING") for i in range(20)]
    out = service.allocate(
        rows,
        rows,
        service.DEFAULTS,
        {"ready": False, "bins": []},
        NOW + timedelta(days=1),
        NOW,
    )
    assert [r["job_id"] for r in out] == [str(i) for i in range(20)]
    times = [
        service.parse_datetime(r["scheduled_at_utc"]).astimezone(NOW.tzinfo)
        for r in out
    ]
    for day in set(t.date() for t in times):
        assert sum(t.date() == day for t in times) <= 8
    assert all(
        b - a >= timedelta(minutes=90) for a, b in zip(times, times[1:], strict=False)
    )
    full_day = [t.hour for t in times if t.date() == NOW.date() + timedelta(days=2)]
    assert full_day == [9, 11, 13, 15, 17, 19, 21, 23]


def test_strong_and_exploration_positions_and_determinism():
    rows = [job(i, status="WAITING") for i in range(8)]
    start = (NOW + timedelta(days=1)).replace(hour=7, minute=0)
    a = service.allocate(rows, rows, service.DEFAULTS, scores(), start, NOW)
    assert a == service.allocate(rows, rows, service.DEFAULTS, scores(), start, NOW)
    assert sum(r["reason"] == "测试" for r in a) == 2
    assert any(
        service.parse_datetime(r["scheduled_at_local"]).hour in [10, 11] for r in a
    )


def test_fixed_published_today_and_adjacent_day_occupancy():
    tomorrow = "2026-09-07"
    protected = job("today", "2026-09-06T23:00:00+08:00")
    fixed = job("fixed", tomorrow + "T10:00:00+08:00", managed=False)
    rows = [protected, *[job(i, status="WAITING") for i in range(12)]]
    out = service.allocate(rows, [*rows, fixed], service.DEFAULTS, scores(), NOW, NOW)
    assert out[0]["scheduled_at_utc"] == service.to_utc_iso(protected["scheduled_at"])
    times = [service.parse_datetime(r["scheduled_at_local"]) for r in out[1:]]
    assert sum(t.date().isoformat() == tomorrow for t in times) <= 7
    assert all(
        abs(t - service.parse_datetime(fixed["scheduled_at"])) >= timedelta(minutes=90)
        for t in times
    )


def test_small_window_rolls_forward_without_compressing():
    rows = [job(i, status="WAITING") for i in range(3)]
    opts = {**service.DEFAULTS, "daily_start_time": "10:00", "daily_end_time": "11:00"}
    out = service.allocate(rows, rows, opts, scores(), NOW + timedelta(days=1), NOW)
    assert len({r["scheduled_at_local"][:10] for r in out}) == 3


def test_non_half_hour_window_rounds_up_and_never_leaks_before_start():
    rows = [job(i, status="WAITING") for i in range(3)]
    opts = {**service.DEFAULTS, "daily_start_time": "07:45", "daily_end_time": "08:00"}
    start = (NOW + timedelta(days=1)).replace(hour=7, minute=0)
    out = service.allocate(rows, rows, opts, scores(), start, NOW)
    assert len({r["scheduled_at_local"][:10] for r in out}) == 3
    assert all(r["scheduled_at_local"][11:16] == "08:00" for r in out)


def seed_metrics(captured=NOW, count=48):
    with get_connection() as c:
        batch = "batch-" + str(captured.timestamp())
        c.execute(
            """INSERT INTO content_metric_import_batches
            (id,account_id,source_kind,source_filename,source_sha256,status,normalized_payload_json,row_count,created_at)
            VALUES(?,'target','douyin_item_export','test.xlsx',?,'committed','[]',?,?)""",
            (batch, batch, count, captured.isoformat()),
        )
        for i in range(count):
            published = (NOW - timedelta(days=4 + i // 12)).replace(
                hour=(i % 12) * 2, minute=0
            )
            c.execute(
                """INSERT INTO douyin_item_metric_snapshots
                (id,batch_id,account_id,aweme_id,title,published_at,captured_at,play_count,duration_seconds,created_at)
                VALUES(?,?,'target',?,'test',?,?,?,45,?)""",
                (
                    batch + str(i),
                    batch,
                    str(i),
                    published.isoformat(),
                    captured.isoformat(),
                    100000 if i == 5 else 1000,
                    captured.isoformat(),
                ),
            )
        c.commit()


def test_metrics_deduplicate_and_shrink_single_outlier(db):
    seed_metrics()
    seed_metrics(NOW - timedelta(minutes=1))
    with get_connection() as c:
        score = service.score_times(c, "target", NOW)
    assert score["ready"] and score["sample_count"] == 48
    assert all(b["score"] == 1 for b in score["bins"])
    assert all(b["status"] == "待验证" for b in score["bins"])


def test_stale_data_and_small_samples_do_not_replan(db):
    seed_metrics(count=12)
    with get_connection() as c:
        assert not service.score_times(c, "target", NOW)["ready"]
        assert not service.score_times(c, "target", NOW + timedelta(days=8))["ready"]


def test_today_fixed_other_account_and_idempotent_request(db, monkeypatch):
    for r in [
        job("today", "2026-09-06T23:00:00+08:00"),
        job("tomorrow", "2026-09-07T07:00:00+08:00"),
        job("fixed", "2026-09-07T11:00:00+08:00", managed=False),
        job("other", "2026-09-07T07:00:00+08:00", account="other"),
    ]:
        seed_job(r)
    monkeypatch.setattr(service, "score_times", lambda *a, **kw: scores())
    service.save_policy("target", {"enabled": True})
    with get_connection() as c:
        service.enqueue(c, "target", "import:1")
        service.enqueue(c, "target", "import:1")
        c.commit()
        assert (
            c.execute(
                "SELECT COUNT(*) FROM adaptive_schedule_requests WHERE source_key='import:1'"
            ).fetchone()[0]
            == 1
        )
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _: service.process_pending(), range(2)))
    with get_connection() as c:
        rows = {r["id"]: dict(r) for r in c.execute("SELECT * FROM publish_jobs")}
        assert rows["today"]["scheduled_at"] == "2026-09-06T23:00:00+08:00"
        assert rows["fixed"]["scheduled_at"] == "2026-09-07T11:00:00+08:00"
        assert rows["other"]["scheduled_at"] == "2026-09-07T07:00:00+08:00"
        assert (
            c.execute(
                "SELECT COUNT(*) FROM adaptive_schedule_requests WHERE status='pending'"
            ).fetchone()[0]
            == 0
        )


def test_replan_transaction_rolls_back_every_change(db, monkeypatch):
    seed_job(job("a", "2026-09-07T07:00:00+08:00"))
    seed_job(job("b", "2026-09-07T08:30:00+08:00"))
    monkeypatch.setattr(service, "score_times", lambda *a, **kw: scores())
    service.save_policy("target", {"enabled": True})
    original = service._write_changes

    def fail(c, account, schedule, token, request_id=None):
        original(c, account, schedule[:1], token, request_id)
        raise ValueError("simulated conflict")

    monkeypatch.setattr(service, "_write_changes", fail)
    result = service.process_pending()
    assert result[0]["status"] == "failed"
    with get_connection() as c:
        assert (
            c.execute("SELECT scheduled_at FROM publish_jobs WHERE id='a'").fetchone()[
                0
            ]
            == "2026-09-07T07:00:00+08:00"
        )
        assert (
            c.execute("SELECT COUNT(*) FROM adaptive_schedule_changes").fetchone()[0]
            == 0
        )


def test_policy_scope_disable_and_manual_fixed(db):
    seed_job(job("a", "2026-09-07T07:00:00+08:00", managed=False))
    with pytest.raises(ValueError):
        service.save_policy("other", {"enabled": True})
    service.save_policy("target", {"enabled": True}, include_existing=True)
    service.set_managed("a", False)
    service.save_policy("target", {"enabled": False})
    service.process_pending()
    with get_connection() as c:
        row = c.execute("SELECT * FROM publish_jobs WHERE id='a'").fetchone()
        assert (
            row["adaptive_managed"] == 0
            and row["scheduled_at"] == "2026-09-07T07:00:00+08:00"
        )


def test_preview_token_changes_when_fixed_occupancy_changes(db):
    seed_job(job("a", status="WAITING"))
    seed_job(job("fixed", "2026-09-07T10:00:00+08:00", managed=False))
    a = service.preview(["a"], "target", service.DEFAULTS, "2026-09-07T07:00")
    with get_connection() as c:
        c.execute(
            "UPDATE publish_jobs SET scheduled_at='2026-09-07T12:00:00+08:00' WHERE id='fixed'"
        )
        c.commit()
    b = service.preview(["a"], "target", service.DEFAULTS, "2026-09-07T07:00")
    assert a["strategy_token"] != b["strategy_token"]


def test_confirmation_api_revalidates_token_and_saves_exact_times(db, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services.publish_scheduler import PublishScheduler

    seed_job(job("a", status="WAITING"))
    monkeypatch.setattr(PublishScheduler, "_require_ready_jobs", lambda *a, **kw: {})
    monkeypatch.setattr(
        PublishScheduler, "_reject_legacy_schedule_apply", lambda *a: None
    )
    monkeypatch.setattr(PublishScheduler, "_public_job", lambda self, i: {"id": i})
    client = TestClient(app)
    payload = dict(
        job_ids=["a"],
        platform="douyin",
        account_id="target",
        schedule_mode="adaptive",
        start_at_local="2026-09-08T07:00",
        daily_end_time="23:59",
    )
    preview = client.post("/api/publish/schedules/preview", json=payload)
    assert preview.status_code == 200, preview.text
    data = preview.json()
    confirmed = [
        {k: row[k] for k in ("job_id", "scheduled_at_utc")} for row in data["schedule"]
    ]
    bad = client.patch(
        "/api/publish/jobs/schedule-batch",
        json={**payload, "confirmed_schedule": confirmed, "strategy_token": "invalid"},
    )
    assert bad.status_code == 400
    response = client.patch(
        "/api/publish/jobs/schedule-batch",
        json={
            **payload,
            "confirmed_schedule": confirmed,
            "strategy_token": data["strategy_token"],
        },
    )
    assert response.status_code == 200, response.text
    with get_connection() as c:
        row = c.execute("SELECT * FROM publish_jobs WHERE id='a'").fetchone()
        assert row["scheduled_at"] == confirmed[0]["scheduled_at_utc"]
        assert row["adaptive_managed"] == 1


def test_reenable_respects_manual_fixed_and_no_data_preserves_future(db):
    seed_job(job("a", "2026-09-09T10:00:00+08:00"))
    service.set_managed("a", False)
    service.save_policy("target", {"enabled": True}, include_existing=True)
    with get_connection() as c:
        assert (
            c.execute(
                "SELECT adaptive_managed FROM publish_jobs WHERE id='a'"
            ).fetchone()[0]
            == 0
        )
    result = service.preview(["a"], "target", service.DEFAULTS, "2026-09-08T07:00")
    assert result["schedule"][0]["scheduled_at_local_display"] == "2026-09-09 10:00"
    assert result["schedule"][0]["reason"] == "数据不足，保留已有排期"


def test_official_import_enqueues_transactionally_and_deduplicates(db, monkeypatch):
    from app.services import content_review_service as review

    service.save_policy("target", {"enabled": True})
    monkeypatch.setattr(review, "_now", lambda: NOW)
    items = [
        dict(
            title="导入测试",
            published_at="2026-09-01T10:00:00+08:00",
            play_count=1000,
            content_genre="视频",
        )
    ]
    result = review.commit_douyin_item_export(
        account_id="target",
        items=items,
        captured_at=NOW.isoformat(),
        source_filename="test.xlsx",
    )
    review.commit_douyin_item_export(
        account_id="target",
        items=items,
        captured_at=NOW.isoformat(),
        source_filename="test.xlsx",
    )
    with get_connection() as c:
        assert (
            c.execute(
                "SELECT COUNT(*) FROM adaptive_schedule_requests WHERE source_key=?",
                (f"import:{result['batch_id']}",),
            ).fetchone()[0]
            == 1
        )
    original = service.enqueue

    def fail(c, account_id, source_key):
        original(c, account_id, source_key)
        raise RuntimeError("after enqueue before commit")

    monkeypatch.setattr(service, "enqueue", fail)
    with pytest.raises(RuntimeError):
        review.commit_douyin_item_export(
            account_id="target",
            items=[{**items[0], "title": "回滚测试"}],
            captured_at=NOW.isoformat(),
            source_filename="second.xlsx",
        )
    with get_connection() as c:
        assert (
            c.execute(
                "SELECT COUNT(*) FROM douyin_item_metric_snapshots WHERE title='回滚测试'"
            ).fetchone()[0]
            == 0
        )
        assert (
            c.execute(
                "SELECT COUNT(*) FROM adaptive_schedule_requests WHERE source_key LIKE 'import:%'"
            ).fetchone()[0]
            == 1
        )


def test_renamed_account_request_fails_without_blocking_the_queue(db):
    service.save_policy("target", {"enabled": True})
    with get_connection() as c:
        c.execute("UPDATE publish_accounts SET account_name='已更名' WHERE id='target'")
        c.commit()
    assert service.process_pending()[0]["status"] == "failed"
    assert service.process_pending() == []

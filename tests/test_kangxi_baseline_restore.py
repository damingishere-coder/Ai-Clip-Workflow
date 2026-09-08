# ruff: noqa: F811
import hashlib
import sqlite3

import pytest

from app.db import database
from app.services.ai_prompt_preset_service import (
    ensure_ai_prompt_version_with_connection,
)
from app.services.content_rule_trial_service import restore_kangxi_baseline
from tests.test_schema_migration_ledger import isolated_database  # noqa: F401


def test_restore_is_backed_up_preserves_versions_and_keeps_archive(isolated_database):
    database.init_db()
    with database.get_connection() as connection:
        preset = dict(
            connection.execute(
                "SELECT * FROM ai_prompt_presets WHERE id='preset_001'"
            ).fetchone()
        )
        baseline = preset["prompt_text"]
        first = ensure_ai_prompt_version_with_connection(
            connection,
            preset_id="preset_001",
            preset_name=preset["name"],
            prompt_text=baseline,
        )
        ensure_ai_prompt_version_with_connection(
            connection,
            preset_id="preset_001",
            preset_name=preset["name"],
            prompt_text="放宽后的正文",
        )
        connection.execute(
            "UPDATE ai_prompt_presets SET prompt_text='放宽后的正文' WHERE id='preset_001'"
        )
        others = [
            tuple(r)
            for r in connection.execute(
                "SELECT * FROM ai_prompt_presets WHERE id!='preset_001' ORDER BY id"
            )
        ]
        versions = [
            tuple(r)
            for r in connection.execute("SELECT * FROM ai_prompt_versions ORDER BY id")
        ]
        connection.commit()
    assert (
        hashlib.sha256(baseline.encode()).hexdigest()
        == "fac845220c05e37e8ec8372eadfd272a19367d3489ff57f9581f399f1a3f92e6"
    )
    result = restore_kangxi_baseline(
        hashlib.sha256("放宽后的正文".encode()).hexdigest()
    )
    assert result["prompt_version_id"] == first["id"]
    database.init_db()
    with database.get_connection() as connection:
        assert (
            connection.execute(
                "SELECT prompt_text FROM ai_prompt_presets WHERE id='preset_001'"
            ).fetchone()[0]
            == baseline
        )
        assert [
            tuple(r)
            for r in connection.execute("SELECT * FROM ai_prompt_versions ORDER BY id")
        ] == versions
        assert [
            tuple(r)
            for r in connection.execute(
                "SELECT * FROM ai_prompt_presets WHERE id!='preset_001' ORDER BY id"
            )
        ] == others
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
        audit = connection.execute("SELECT * FROM prompt_change_audits").fetchone()
        assert (
            audit["before_prompt"] == "放宽后的正文"
            and audit["after_prompt"] == baseline
        )
    backups = list(
        (isolated_database.parent / "backups").glob("*kangxi-baseline-restore*.sqlite3")
    )
    assert backups
    with sqlite3.connect(backups[0]) as backup:
        assert (
            backup.execute(
                "SELECT prompt_text FROM ai_prompt_presets WHERE id='preset_001'"
            ).fetchone()[0]
            == "放宽后的正文"
        )


def test_restore_refuses_concurrent_prompt_changes(isolated_database):
    database.init_db()
    with database.get_connection() as connection:
        preset = dict(
            connection.execute(
                "SELECT * FROM ai_prompt_presets WHERE id='preset_001'"
            ).fetchone()
        )
        ensure_ai_prompt_version_with_connection(
            connection,
            preset_id="preset_001",
            preset_name=preset["name"],
            prompt_text=preset["prompt_text"],
        )
        connection.commit()
    with pytest.raises(ValueError, match="已变化"):
        restore_kangxi_baseline("stale-hash")

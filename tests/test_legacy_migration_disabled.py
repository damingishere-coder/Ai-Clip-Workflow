from scripts import migrate_task_dirs_to_project_names as legacy_migration


def test_legacy_task_directory_migration_is_unconditionally_disabled(
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(
        legacy_migration,
        "apply_plan",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("旧迁移不得执行")
        ),
    )

    assert legacy_migration.main() == 2
    assert "永久停用" in capsys.readouterr().err

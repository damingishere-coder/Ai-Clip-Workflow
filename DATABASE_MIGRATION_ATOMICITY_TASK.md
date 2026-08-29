# 数据库迁移原子性与 Prompt 外键修复任务

## 背景

第二次工程复检确认：内容实验账本迁移在事务内调用 `sqlite3.executescript()`，会隐式提交并破坏整体回滚；历史数据库通过 `ALTER TABLE` 新增的 `ai_analysis_runs.prompt_version_id` 没有新建数据库所具备的外键。

## 目标

- 让内容实验结构、校验与迁移账本处于同一事务，失败时不残留半套表或索引。
- 为已升级的历史数据库补齐 `prompt_version_id → ai_prompt_versions.id` 外键，并保持 AI Run 数据、既有索引和下游引用。
- 迁移前生成可恢复备份；数据或结构无法安全证明时 fail-closed，不猜测或删除历史记录。

## 允许修改范围

- `app/db/database.py`
- `tests/test_schema_migration_ledger.py`
- `docs/DATABASE_SCHEMA.md`
- `DEVELOPMENT_LOG.md`、`NEXT_STEPS.md` 与本任务文件

## 禁止修改范围

- 活动 SQLite、真实 Provider、Chrome Worker、发布任务和运行中服务。
- 其他历史兼容迁移的全面重写。
- 删除或自动清空无法归因的 AI Run。

## 已确定实现要求

1. 内容实验迁移不得在账本事务内使用 `executescript()`。
2. 外键修复使用新的迁移版本和 checksum，不改写已发布迁移账本。
3. 表重建时临时关闭外键仅限该迁移连接，事务提交或回滚后恢复原状态，并执行 `PRAGMA foreign_key_check`。
4. 保留 AI Run 全部规范字段、显式索引和触发器；发现未知字段、临时迁移表或孤儿 Prompt 引用时拒绝迁移。
5. 新建库与历史升级库最终都必须具有同一 `NO ACTION` Prompt 外键语义。

## 验收标准

- 故障注入拒绝创建第二张实验表后，第一张表、索引和账本均不残留；移除故障后可安全重跑。
- 模拟旧库升级后 AI Run、反馈引用和索引保留，Prompt 外键存在，外键检查为空。
- 孤儿 Prompt 引用会使迁移整体失败，原表和数据不变，账本不写入。
- 定向测试、全量测试、Ruff、Compileall、Compose 配置和 `git diff --check` 通过。

## 测试命令

```powershell
pytest -q tests/test_schema_migration_ledger.py tests/test_database_backup_service.py
pytest -q
ruff check app tests scripts
python -m compileall -q app tests scripts
git diff --check
```

## 返回格式

- 原子性与外键修复说明、故障注入和重跑证据。
- 修改文件、测试结果、分支、提交 SHA、远端 SHA 与 PR 状态。

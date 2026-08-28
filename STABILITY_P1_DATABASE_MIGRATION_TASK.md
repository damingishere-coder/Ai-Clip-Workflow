# P1.3c 数据库迁移账本与唯一索引 Fail-Closed

## 背景

当前 `init_db()` 通过表结构探测、`ALTER TABLE` 和 `executescript()` 兼容历史数据库，但没有可查询的迁移版本账本。更高风险的是 `_create_indexes()` 会先删除发布任务的活动唯一索引，再静默吞掉所有索引创建异常；一旦重建失败，应用仍会启动并允许重复活动发布任务进入数据库。

## 本轮目标

1. 建立最小、可扩展的 `schema_migrations` 迁移账本。
2. 每条新迁移记录稳定版本、名称、校验和与完成时间。
3. 版本相同但校验和变化时拒绝启动，避免迁移定义被静默改写。
4. 发布活动任务唯一索引采用“先建立并验证新版索引，再删除旧索引”的顺序。
5. 索引缺失、定义漂移、重复数据或 SQLite 异常时明确失败，不再静默继续运行。
6. 迁移执行与账本写入位于同一 SQLite 事务中；失败不写成功记录。
7. 已有数据库在 P1.3c 首次写入前创建 SQLite Online Backup；备份失败则不开始迁移。

## 允许修改

- `app/db/database.py`
- 新增与本轮直接相关的隔离数据库测试
- `DEVELOPMENT_LOG.md`
- `NEXT_STEPS.md`
- `PROJECT_AUDIT.md`
- `.codemap/*`
- 本任务说明

## 禁止修改

- 不直接运行或迁移正式 `workflow.sqlite3`。
- 不删除、合并或自动修复正式库中的历史发布记录。
- 不修改业务状态机、发布调度、Provider、Worker 或页面逻辑。
- 不引入 Alembic 或其他新依赖，不重写全部历史兼容迁移。
- 不处理 P1.4 Provider 超时或 P1.5 Secret/Auth。

## 已确定实现要求

### 迁移账本

- `schema_migrations.version` 为主键，并保存 `name`、`checksum`、`applied_at`。
- P1.3c 及后续正式 Schema 变更必须通过有序迁移注册表执行。
- 已应用迁移必须校验 checksum 和数据库不变量；不能只看到版本号就假定成功。
- 账本只记录完整成功的迁移，不把失败尝试伪装成已应用。
- 旧的列探测兼容逻辑本轮保留，并明确视为 pre-ledger compatibility，不宣称已经完成全面 Alembic 化。

### 唯一索引

- 新版索引使用独立版本化名称，约束同一 `output_clip_id + platform + publish_mode` 只能存在一条活动发布任务。
- 活动状态继续包括 `DRAFT`、`WAITING`、`SCHEDULED`、`PUBLISHING`、`NEED_REVIEW`。
- 新索引创建和定义验证成功前，不删除旧版保护索引。
- 发现活动重复数据时拒绝迁移，并返回不包含 Secret 的诊断信息；不擅自决定保留或取消哪条发布记录。
- 已应用迁移启动时仍验证索引存在、唯一性和定义，防止手工删索引或 Schema 漂移后继续运行。

### 事务与并发

- 每条账本迁移通过 `BEGIN IMMEDIATE` 串行化。
- Schema 修改、验证和账本写入同事务提交；任一步失败全部回滚。
- 两个本地进程同时初始化同一库时，后到进程应读取已完成账本，而不是重复写入或吞掉锁错误。

## 验收标准

1. 新建隔离库初始化后恰有一条 P1.3c 迁移记录，重复初始化不新增记录。
2. 新版唯一索引存在且旧版索引已在成功后移除。
3. 迁移 checksum 漂移时明确拒绝启动。
4. 已应用迁移的索引缺失或定义错误时明确拒绝启动。
5. 活动重复数据导致索引无法建立时，启动失败、迁移不入账、数据不被自动改写。
6. 模拟新版索引验证失败时，旧版索引仍存在，证明切换顺序和回滚有效。
7. 索引列表中的其他 SQLite 异常不再被静默吞掉。
8. 定向测试、完整 Pytest、Ruff、Compileall 通过；测试只使用临时数据库。
9. 正式数据库文件的大小、mtime、hash 均不因本轮验证改变。

## 测试命令

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_schema_migration_ledger.py tests/test_database_backup_service.py tests/test_publish_scheduler_state_machine.py tests/test_job_fencing.py tests/test_long_live_foundation.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe -m compileall -q app tests scripts
```

## 返回格式

- 迁移账本与唯一索引切换方式。
- 失败/漂移/重复数据的 fail-closed 证据。
- 正式数据库未被修改的证据。
- 定向与全量验证结果。
- 仍保留的 pre-ledger 历史迁移边界。
- Commit、分支、PR 与下一独立轮次。

# 稳定 V1 整改任务书

## 背景

工程审计确认当前项目属于“可用 V1”，但存在会伤害真实数据或让失败状态不可恢复的 P0 风险。本轮目标是按小步、可测试、可回滚的方式把项目提升到“稳定 V1”，不做全面重构，也不扩大产品范围。

## 本轮目标

1. 隔离 Pytest 数据库和媒体目录，任何外部 `DATABASE_PATH` 都不能让测试连接活动库；危险清理夹具必须在删除前再次 fail-closed 校验。
2. 为 SQLite 外键异常提供默认只读预演、应用前强制备份、事务内修复、修复后完整性复查的工具；只在验证备份后处理已确认的孤儿引用。
3. 把永久删除改为“托管目录暂存隔离 -> 数据库提交 -> 延迟清除”；数据库失败时可把文件恢复原位，外部唯一原片始终不动。
4. 完成独立测试与验收，并记录剩余 P1/P2 风险和下一轮顺序。

## 允许修改范围

- `tests/conftest.py` 及与本轮 P0 直接相关的测试。
- `app/services/storage_service.py`
- `app/services/task_lifecycle_service.py`
- `app/services/database_backup_service.py`（仅复用或补充安全备份能力）。
- `scripts/` 下新增或调整本轮修复、验证脚本。
- `DEVELOPMENT_LOG.md`、`NEXT_STEPS.md`、本任务书和必要审计文档。

## 禁止修改范围

- 不改变 AI Provider、投稿平台、字幕和切片的正常业务语义。
- 不更改生产 Schema，不删除任务、发布或字幕历史。
- 不读取、输出或提交 `.env`、Token、Cookie、账号凭据。
- 不绕过平台登录、验证码、风控或人工确认。
- 不自动合并 PR，不强制推送，不删除分支。

## 已确定实现要求

### P0.1 测试隔离

- Pytest 启动时无条件使用进程级临时根目录，不继承调用者传入的活动库路径。
- 临时数据库和媒体目录必须位于同一隔离根目录。
- 对整表清理增加第二道路径校验；路径不在 Pytest 隔离根目录时立即中止。
- 验证从命令行故意传入活动库路径时，测试仍不会连接或改写活动库。

### P0.2 外键修复

- 工具默认 dry-run；只有显式 `--apply` 才写入。
- 应用前使用 SQLite Online Backup API 创建唯一备份并执行 `quick_check`。
- 只处理当前检测到且策略明确的孤儿 `publish_jobs.output_clip_id` 和 `subtitle_jobs.output_clip_id`。
- 修复必须单事务提交；提交前后执行 `foreign_key_check`，不允许产生新异常。
- 尽量保留历史证据；若表约束不允许安全置空，则先归档必要字段再做最小删除，并在报告中逐条列出。

### P0.3 两阶段永久删除

- 只处理经过现有托管根目录校验的目录。
- 文件先原子移动到同盘隔离区并写清单；任一步失败要恢复已经移动的目录。
- 数据库提交失败时必须恢复目录；不得留下“文件没了、任务仍可见”的半成功状态。
- 数据库提交成功后再清除隔离区；清除失败要返回明确的 `cleanup_pending`，不得把逻辑删除回滚成可见状态。
- 重复执行必须幂等；外部原片保持不变。

## 验收标准

- 相关 P0 回归测试全部通过。
- 全量测试、Lint/语法检查、前端语法检查通过，或对既有失败给出可复现证据。
- 活动数据库在运行普通测试前后文件哈希、大小和外键异常计数不发生变化。
- 外键修复应用前生成可读备份；修复后 `PRAGMA quick_check = ok` 且 `PRAGMA foreign_key_check` 为空。
- 模拟数据库提交失败时，暂存文件恢复到原路径，任务仍可见。
- 模拟最终清理失败时，任务保持已删除并返回可恢复的待清理状态。
- `git diff` 只包含本轮范围，且无敏感信息、调试残留或临时产物。

## 测试命令

具体临时目录由执行者生成，不得使用 `data/workflow.sqlite3`：

```powershell
pytest -q tests/test_task_query_service.py tests/test_media_storage_lifecycle.py tests/test_database_backup_service.py
pytest -q
ruff check app tests scripts
python -m compileall -q app scripts
node --check app/static/js/task-detail.js
```

## 返回格式

- 修改文件与关键行为。
- 测试命令、退出码、通过/失败数量。
- 活动数据库备份路径、修复前后外键计数和完整性结果（不含业务内容）。
- Commit、分支、Push 和 PR 状态。
- 未完成的 P1/P2 风险与下一轮建议。

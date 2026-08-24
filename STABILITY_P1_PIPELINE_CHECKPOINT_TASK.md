# P1.3b 自动流水线持久化步骤 Checkpoint

## 背景

P1.3a 已封住 Workflow Job 代际覆盖、Task 状态跳跃、取消竞态和切片批次半提交，但 `PipelineEngine` 的步骤结果仍只保存在进程内 `context`。Web/Worker 重启、子进程异常退出或租约接管后，同一个 Job 可能从头执行，造成重复 AI 调用、重复 FFmpeg、重复封面和上下文缺失。

## 本轮目标

只复用现有 `workflow_jobs.checkpoint_json`，为 `auto_pipeline` 增加版本化、带 lease fencing 的步骤 checkpoint 和重启恢复：

1. 每个步骤开始前持久化 `running`、输入基线和开始时间。
2. 每个步骤完成后只保存路径、ID、hash、计数等紧凑证据，并持久化 `succeeded`。
3. 新 Worker 接管时逐步验证数据库/文件证据，跳过已确认完成的步骤。
4. `running` 步骤先尝试 reconcile；证据完整则补记成功，证据不足才重做。
5. 旧 owner/token 不能写 checkpoint、Task 状态或终态。
6. `METADATA_GENERATING`、`SCHEDULE_CREATING` 和 `PUBLISH_JOB_CREATING` 不再依赖上一进程的内存对象。
7. 失败/取消后的自动重试必须复用同一 Workflow Job，不能把 checkpoint 留在旧 Job 后另建空 Job。

## 允许修改

- `app/services/pipeline_engine.py`
- `app/services/job_service.py`（仅必要的 checkpoint 读取错误边界）
- 新增独立的 auto pipeline checkpoint 服务
- 与本轮直接相关的隔离测试
- `DEVELOPMENT_LOG.md`、`NEXT_STEPS.md`、`PROJECT_AUDIT.md`、`.codemap/*`

## 禁止修改

- 不新增或迁移数据库列。
- 不修改字幕 Job 现有 `{completed: ...}` checkpoint 格式。
- 不调用真实 AI、FFmpeg、Chrome、抖音或 B站。
- 不处理 P1.3c 数据库迁移账本、P1.4 Provider 超时、P1.5 Secret/Auth。
- 不删除历史 cut run、AI run、发布任务或用户数据。

## 已确定实现要求

### Checkpoint envelope

- `kind = auto_pipeline_step_v1`，并记录 `task_id`、`start_step`、`current_step`、`completed_steps`、`steps`。
- completed steps 必须构成从 `start_step` 开始的连续前缀；非连续、跨 Task、未知 step、未知 kind 或非字典 checkpoint 均拒绝自动执行。
- checkpoint 不保存 Secret、完整 Prompt、完整 AI payload 或完整 output clip 对象。

### 恢复与复用

- 已完成步骤必须重新验证证据；证据缺失时 fail-closed，不自动覆盖人工或历史结果。
- 中断步骤允许用本轮开始前基线识别新产生的 AI run、cut run 或 artifact；能确认完整才补记成功。
- 切片证据必须绑定 active `cut_run_id`、候选 ID、任务受控目录、非空文件、size 与 fingerprint；发布草稿同时绑定 schedule、视频、封面和草稿字段。
- 普通转写继续复用现有 transcript/分块 checkpoint；发布草稿继续使用现有 active 去重边界。
- metadata/schedule 从持久化 JSON 恢复下游输入，不能依赖旧进程内存。
- 字幕草稿完成后的 `PENDING_SUBTITLE_REVIEW` 仍是稳定人工门禁，重启不得自动越过。

### 诚实边界

- 外部模型/FFmpeg 与 SQLite checkpoint 无法做到同一事务；本轮目标是通过 reconciliation 显著减少重复，不宣称严格 exactly-once。
- Provider 在返回前进程崩溃且没有可验证产物时，重试仍可能再次计费；该边界保留到 P1.4 的 Provider 幂等策略。

## 验收标准

1. Job 重领后从第一个未确认完成步骤继续，已完成 handler 不再调用。
2. AI/cut 在“副作用已完成、checkpoint 未落盘”场景能根据新 run 证据恢复。
3. metadata/schedule 能从文件恢复完整下游输入，且 checkpoint 只保存紧凑证据。
4. 全部步骤 checkpoint 写入继续受 owner/token fencing 保护。
5. malformed/unknown auto checkpoint 给出明确失败，不静默从头执行。
6. NULL/空 checkpoint 和字幕旧 checkpoint 行为保持兼容。
7. 字幕审核暂停在重启后仍返回 `pending_subtitle_review`。
8. 定向测试、完整 Pytest、Ruff、Compileall 通过；工作区没有测试产物或活动数据改动。
9. `job_id` 没有当前有效 lease 时，在任何 handler、Task 状态或文件副作用前 fail-closed。
10. `/auto-retry` 复用旧 Job 的 running/failed checkpoint，并由新 token 继续恢复。

## 测试命令

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_pipeline_checkpoint.py tests/test_pipeline_state_stability.py tests/test_job_fencing.py tests/test_auto_pipeline.py tests/test_subtitle_auto_workflow.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe -m compileall -q app tests scripts
```

## 返回格式

- 实际修改与非目标。
- 重启/重领/损坏 checkpoint 的验证证据。
- 全量测试结果。
- 仍不能保证 exactly-once 的边界。
- Commit、分支、PR 与下一独立轮次。

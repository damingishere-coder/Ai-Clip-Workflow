# 稳定 V1 P1B 执行代际 Fencing 任务书

## 背景

P0 与 P1A 已封住真实数据、路径和进程终止边界。剩余最高风险集中在异步执行代际：旧 Workflow Worker 在 lease 过期并被接管后仍能写进度或终态；旧 Publish execution 也能覆盖新 claim；Windows Publish Worker 对同一 execution id 会再次调用真实 Publisher。

## 目标

1. Workflow Job 每次 claim 生成唯一 `lease_token`，所有 Worker 写回必须同时匹配 owner + token。
2. 旧子进程在执行副作用前验证租约；失去租约后不能更新 checkpoint、进度或终态。
3. 存活但达到最大尝试次数的 Worker 不得被其他 Worker 提前判为失败。
4. Publish Job 使用现有 `execution_id` 作为代际 token，旧执行不能覆盖新 claim。
5. Publish Worker 对同一 execution id 幂等；路径标识只能是安全的单段 ID。

## 分阶段范围

### P1B.1 Workflow Job

- `app/db/database.py`
- `app/services/job_service.py`
- `app/services/job_worker.py`
- `app/services/job_worker_process.py`
- 必要的 Pipeline/转写/字幕 heartbeat 调用
- `tests/test_job_fencing.py` 及相关既有测试

### P1B.2 Publish Scheduler / Worker

- `app/services/publish_repository.py`
- `app/services/publish_scheduler.py`
- `app/services/publish_executor.py`
- `scripts/publish_host_worker.py`
- `app/services/publishers/browser_runtime.py`
- `app/services/publishers/worker_client.py`
- 发布 fencing/幂等测试

## 禁止范围

- 不改变 AI Provider、模型、账号、Token、Cookie 或 Chrome Profile 内容。
- 不触发真实 AI、真实投稿、登录或平台验证。
- 不删除任务、发布记录、execution journal 或历史证据。
- 不引入消息队列、微服务或新数据库。
- 不自动合并 PR，不强推。

## 数据库变化

- 只为 `workflow_jobs` 新增可空 `lease_token TEXT`。
- 不重建表、不删除列、不改外键。
- 每次 claim 写入新随机 token；release/retry/终态清空 token。
- 正式库应用迁移前必须创建 SQLite Online Backup 并确认 `quick_check=ok`；存在旧 running 且 token 为空时必须停止并人工处理，不能静默视为有效租约。

## 验收标准

- Worker A 过期、Worker B 接管后，A 的 heartbeat/progress/checkpoint/completed/failed/cancelled/release 全部被拒。
- `already_claimed` owner/token 不匹配、lease 过期或状态不正确时，不调用任何业务执行器。
- running 且 lease 有效的任务达到 max_attempts 后仍保持 running；过期后才失败。
- execution A 不能覆盖 execution B 的 Provider 结果、phase 或终态。
- 并发 `repair_and_publish` 只创建一个替代任务。
- 同 execution id 重试不再次调用 Publisher；不同身份复用同 id fail-closed。
- 定向、全量、Ruff、Compileall 通过；真实发布调用次数为 0。

## 测试命令

```powershell
pytest -q tests/test_job_fencing.py tests/test_job_queue.py tests/test_long_live_foundation.py
pytest -q tests/test_publish_fencing.py tests/test_publish_scheduler_state_machine.py tests/test_publish_worker_client.py
pytest -q
ruff check app tests scripts
python -m compileall -q app scripts
```

## 返回格式

- Schema 变化、备份和兼容处理。
- 旧执行被拒的 SQL/测试证据。
- 全量测试与静态检查结果。
- Commit、分支、Push、PR 状态。
- 剩余状态机、半提交和外部调用风险。

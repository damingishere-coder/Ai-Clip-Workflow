# P1.3 任务状态与切片原子性

## 背景

工程审计确认了四个相互关联的稳定性缺口：任务状态接口可以任意跳转；全自动流水线的取消会被记录成失败或继续完成；`READY_TO_PUBLISH` 只短暂存在；切片结果逐条提交，失败时可能留下半成功数据。

基线证据：隔离测试数据库初始化后，相关 8 个测试文件共 `112 passed`，Ruff 检查通过。

## 目标

1. 对外状态更新只能执行明确允许的相邻或恢复转换，禁止把空任务直接标记完成。
2. 自动流水线在步骤前后都检查取消，取消后任务进入明确、可重试的 `CANCELLED` 状态。
3. 自动流水线成功终态稳定保留为 `READY_TO_PUBLISH`，不再立即覆盖为 `COMPLETED`。
4. 同一切片批次的结果写入、旧版本停用和新版本激活在一个 SQLite 事务内完成。
5. 每个切片批次写入独立目录，避免并发/重试覆盖同名媒体文件。
6. 汇总文件写入失败不得遮蔽原始业务错误或取消原因。

## 影响与成本

| 项目 | 影响 | 成本 | 本轮决策 |
| --- | --- | --- | --- |
| 测试数据库统一初始化 | 高 | 低 | 实施 |
| 状态接口受控转换 | 高 | 中 | 实施 |
| 取消终态与 READY 稳定化 | 高 | 中 | 实施 |
| 切片事务与批次目录隔离 | 高 | 中 | 实施 |
| 汇总写入降级 | 中 | 低 | 实施 |
| 流水线跨进程持久化步骤 checkpoint | 高 | 高 | 留到 P1.3b |
| FFprobe、AI Provider、Secret/Auth | 高 | 中至高 | 按后续独立轮次处理 |

## 状态图

修改前：

```text
任意状态 --PATCH /status--> 任意状态
PUBLISH_JOB_CREATING -> READY_TO_PUBLISH -> COMPLETED
运行中 --取消/异常--> FAILED_<STEP> 或残留运行态
```

修改后：

```text
对外 PATCH: 当前状态 --允许表--> 相邻状态/明确恢复状态
PUBLISH_JOB_CREATING -> READY_TO_PUBLISH（稳定终态，等待人工发布）
运行中 --取消--> CANCELLED --重试--> 对应流水线起点
```

## 允许修改范围

- `app/models/task.py`
- `app/routers/tasks.py`
- `app/services/task_lifecycle_service.py`
- `app/services/task_service.py`
- `app/services/pipeline_engine.py`
- `app/services/auto_publish_service.py`
- `app/services/job_service.py`
- `app/services/job_worker.py`
- `app/services/video_cut_workflow_service.py`
- 与上述行为直接相关的测试
- `PROJECT_AUDIT.md`、`DEVELOPMENT_LOG.md`、`NEXT_STEPS.md`
- Codemap 生成数据与产物（必须通过 skill 脚本生成）

## 禁止修改范围

- 数据库 Schema 和生产数据
- AI Provider、Prompt、FFmpeg 参数和发布平台逻辑
- UI 结构与样式
- 依赖版本
- Dead Code 删除和大规模模块拆分

## 已确定实现要求

- 保留手动流程小写状态与自动流程大写状态的兼容性。
- 内部工作流仍可写步骤状态；对外 `PATCH /status` 必须走独立的合法转换检查和数据库条件更新。
- 所有任务状态写入拒绝已永久删除任务。
- 切片数据库提交失败时，新批次标记失败，旧 active 版本保持不变。
- 较旧并发批次不得覆盖已经完成的较新 active 批次。
- 发布中心同步只能在当前批次成功激活后执行。
- 取消检查放在步骤开始前和处理完成后，最终 READY 写入前再检查一次。
- Task 步骤状态、切片提交和 READY 写回必须绑定当前 Workflow Job 的 owner、token 与未过期 lease。
- 公开 Task 取消必须同步请求活跃自动 Job 停止；Job 取消只清理 provider 证据明确关联到本执行代际的未发布任务。
- `running + cancel_requested` 在子进程退出或 lease 过期后必须收敛到 `cancelled`，不能永久残留运行态。

## 验收标准

- 空任务不能通过 API 直接变成 `completed`/`COMPLETED`，返回 HTTP 409。
- 合法相邻状态转换成功；并发状态变化时条件更新失败，不覆盖新状态。
- 取消的自动任务最终为 `CANCELLED`，job 为 `cancelled`，不会写 READY/COMPLETED。
- 旧 lease Worker 不能写 Task 步骤、切片结果或 READY；READY 后取消会清理本轮关联且尚未发布的排期记录。
- 成功流水线最终任务状态和返回状态均为 `READY_TO_PUBLISH`/`ready_to_publish`。
- 人为制造第二条 output clip 插入失败时，数据库中不保留该批次的部分 clip，旧 active 结果不变。
- 并发创建 cut run 时 `run_number` 不重复；批次输出目录不同。
- 汇总写入失败时仍返回原始失败/取消结果。
- 定向测试、全量测试和 Ruff 全部通过。

## 建议测试命令

```powershell
python -m pytest tests/test_task_state_machine.py tests/test_cut_atomicity.py tests/test_auto_pipeline.py tests/test_job_queue.py tests/test_job_fencing.py -q
python -m pytest -q
python -m ruff check app tests
```

## 回滚方式

本轮不迁移 Schema；回滚代码提交即可。新生成的 `clips/run_*` 目录由现有数据库路径引用，旧目录和旧记录不会删除。`provider_response.workflow_job_id` 只是本地关联证据，不改变发布表结构。

## 返回格式

报告修改文件、状态行为变化、测试证据、Codemap 复审结果、提交哈希、分支、Push 与 PR 链接。

# P1.3d 字幕批准原子性与跨进程恢复任务

## 背景

工程审计确认字幕批量烧录仍有三个稳定性缺口：批量批准逐条提交、渲染完成与 active 结果切换分两次提交、进程被接管后旧执行仍可能落下最终文件或激活旧结果。现有实现已经使用 workflow job lease、attempt 专属 `.part.mp4` 和 checkpoint，本轮在这些机制上收口，不引入新队列或新架构。

## 目标

1. 批量批准、固定待渲染 revision、创建或复用字幕 workflow job，以及全自动任务的字幕交付模式写入形成单个数据库事务。
2. 任一 revision 校验或 job 创建失败时，整批批准不得出现部分成功。
3. 只有仍持有当前 workflow job lease 的执行可以把验证通过的临时文件切换为最终文件并激活 subtitle job。
4. 新执行接管或重试时，清理同一 workflow job 遗留的未激活 processing 记录和 attempt 临时文件；不得删除已激活结果、已验证最终文件或其他 job 的临时文件。
5. checkpoint 只接受当前已激活、已验证且文件仍存在的字幕结果，避免恢复时复用已被后续版本替代的记录。
6. 字幕 Job 完成与后续自动流水线 Job 的创建/复用必须同事务提交，取消或 lease 失效时两者都不得落库。

## 允许修改范围

- `app/services/subtitle_data_service.py`
- `app/services/subtitle_auto_workflow_service.py`
- `app/services/subtitle_workflow_service.py`
- 为复用原子 job 创建所必需的 `app/services/job_service.py`
- 为父进程异常退出收口所必需的 `app/services/job_worker.py`
- 为剩余 Workflow lease/follow-up/发布草稿跨进程边界所必需的 `app/services/auto_publish_service.py`
- 为发布草稿已提交但 checkpoint 尚未写入恢复所必需的 `app/services/pipeline_engine.py`
- 字幕、job fencing、pipeline checkpoint 相关测试
- `.codemap/modules.json`、`PROJECT_AUDIT.md`、`DEVELOPMENT_LOG.md`、`NEXT_STEPS.md`

## 禁止修改范围

- 不改变用户可见的字幕审核流程和页面结构。
- 不新增数据库 Schema，不迁移或写入活动 `data/workflow.sqlite3`。
- 不执行真实 FFmpeg、AI Provider、Chrome 或平台投稿。
- 不处理 P1.4 的通用超时/重试策略，不处理 P1.5 的管理员门禁和 XSS。
- 不做字幕模块的大规模拆分或格式化。

## 已确定实现要求

- 所有批量输入先完成无副作用收集；正式批准必须在 `BEGIN IMMEDIATE` 中重新校验 track、revision、cue 数量与时间重叠。
- workflow job 的“查找活动任务或插入新任务”必须复用同一事务连接，不能在批准提交后才创建。
- 渲染成功落库必须检查 `workflow_job_id + status=running + lease_owner + lease_token + 未过期`；无 workflow job 的同步单条渲染保持兼容。
- 文件最终切换与数据库 active 切换在同一短事务保护区完成；后续数据库失败时删除本次唯一命名的孤儿最终文件。
- 清理函数只接受受管理字幕目录中的、名称包含精确 workflow job 标记的 `.part.mp4` 或无数据库引用的本次最终文件，单文件失败需可诊断且不能伪装成清理成功。
- 所有 lease 校验时间必须在取得数据库写锁后计算；续跑 Job 与当前字幕 Job 终态必须原子提交。
- 源轨生成、切片轨同步和字幕导入必须在写锁内重读 active revision，不能用事务外旧基线覆盖人工新版本。
- ASS 文件按 revision 确定性生成并可复用，不作为 attempt 临时文件删除；只有带本次 workflow 标记的临时/孤儿视频属于中断清理范围。

## 验收标准

- 批次第 N 项校验失败，前 N-1 项仍保持原状态，且不创建 workflow job。
- job 插入失败时没有批准或 delivery mode 的半提交。
- stale lease 不能完成、激活或覆盖新执行；旧 active 结果保持不变。
- 新 lease 可标记遗留 processing 记录并清理自己的 `.part.mp4`，其他 job 文件不受影响。
- 有效 checkpoint 可恢复；未激活、revision 不匹配、文件缺失或已被替代的 checkpoint 会重做。
- Pytest 必须明确使用唯一临时 `test_workflow.sqlite3`；活动数据库不得作为测试目标。

## 测试命令

```powershell
.venv\Scripts\python.exe -m pytest -q tests/test_subtitle_editor.py tests/test_subtitle_auto_workflow.py tests/test_job_fencing.py tests/test_job_queue.py tests/test_auto_pipeline.py tests/test_pipeline_checkpoint.py tests/test_pipeline_state_stability.py tests/test_task_state_machine.py tests/test_publish_task_linkage.py tests/test_versioning_rollback.py
.venv\Scripts\python.exe -m ruff check app tests
.venv\Scripts\python.exe -m compileall -q app
```

## 返回格式

- 修改文件与关键行为变化
- 测试命令、通过数、失败证据
- 活动数据库未变化证据
- Codemap 受影响模块复评
- commit、分支、push 与 PR 链接

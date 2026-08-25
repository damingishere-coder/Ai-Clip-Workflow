# P1.4b AI 结果一致性与恢复任务

## 背景

P1.4 已统一 AI/ASR/FFmpeg 的超时、429/5xx、坏 JSON 和不确定计费边界。最终 Codemap 复评仍确认三条 HIGH：AI 候选、run 与任务终态分开提交；人工 AI 入口没有持久化 Job/lease；普通和综艺分析的局部失败会被当成完整成功。

## 目标

1. `/api/tasks/{task_id}/process/ai` 只创建或复用持久化 `ai_analysis` Workflow Job，由现有 Worker 子进程执行并支持过期 lease 接管。
2. 候选替换、active run 切换、新 run 插入和任务 `pending_review` 终态在同一个 SQLite 事务内完成，并在提交点重新验证当前 lease。
3. `candidate_clips.json` 降为可重建派生缓存；数据库 active run 是权威来源，缺失文件可恢复且不会再次调用 Provider。
4. `general` / `variety_comedy` 的局部失败写入结构化 `analysis_meta`；不完整分析可供人工查看，但自动选片、切片和发布必须停止。

## 允许修改范围

- `app/services/ai_analysis_workflow_service.py`
- `app/services/ai/ai_clip_analyzer.py`
- `app/services/ai/variety_comedy_analyzer.py`
- `app/services/ai/long_live_talk_analyzer.py`
- `app/services/ai/unit_checkpoint.py`
- `app/services/job_service.py`
- `app/services/job_worker.py`
- `app/services/pipeline_engine.py`
- `app/models/task.py`
- `app/routers/tasks.py`
- `app/static/js/app.js`
- 对应隔离测试、Codemap 与项目文档

## 禁止修改范围

- 不新增数据库列或迁移活动数据库。
- 不调用真实 AI、FFmpeg、Chrome、抖音或 B站。
- 不重写整个 AI Service、Job 系统或前端框架。
- 不处理 P2 God Service 拆分、代码风格清理或历史 dead code。

## 已确定实现要求

- Job 创建前在同一事务检查任务、下游产物和活动自动流水线；并发重复点击只能得到同一个 AI Job。
- Worker 使用现有 owner/token/heartbeat；旧 lease 不能提交候选、run、任务终态或 Job 终态。
- 最终事务必须检查 job id、task id、允许 job type、running、owner/token、lease 未过期且未取消。
- Provider 成功但派生文件写入失败时，不得把已经提交的 AI 结果当成 Provider 失败而重新计费；后续从 active run 重建文件。
- 旧 `candidate_clips.json` 和旧 run 继续可读，不批量重写历史数据。
- 合法空窗口不算失败；超时、HTTP、坏 JSON、坏结构和无效条目需要记录失败。全窗口失败仍为 hard failure。
- `analysis_incomplete=true` 时保留候选供人工复核，但自动流水线不得进入切片。

## Codemap 复评后的必要范围补充

- 普通/综艺每个 Provider 单元在调用前写入持久化 Job checkpoint；成功后写校验和。前次调用已开始但本地结果未确认时 fail closed，不自动重复计费。
- 长直播严格要求 `moments` 数组；坏条目和计费不确定窗口必须进入不完整状态。综艺全局评审不完整时设置质量降级并锁住自动切片。
- AI Job 取消、父 Worker 异常退出、停机释放和进程树终止失败必须让 Job/Task 进入明确且可恢复的状态。
- 失败/取消后的显式重试必须复用原 Workflow Job 和单元账本；Provider model、endpoint、protocol 变化必须使输入指纹变化，但不得借新 Job 丢弃旧不确定证据。
- active run/meta 损坏、Schema 或选片模式不一致、非规范覆盖率必须 fail closed；手动和自动切片复用同一验证器。
- 安全交叉复审发现的静态媒体 CORS/API 写入 Origin 混用必须一并封口，但不扩大为新的权限系统。

## 验收标准

- 人工 AI API 立即返回 queued/running Job；页面轮询 Job，完成后刷新分析摘要与历史。
- 同一任务并发请求只创建一个 active AI Job；活动 auto pipeline 时仍返回 409 且不创建 AI Job。
- 候选/run/任务状态任一点失败全部回滚；旧 Worker 提交被拒。
- active run 可重建缺失的 `candidate_clips.json`，重建不调用 Provider。
- general/variety 部分失败具有覆盖率和失败单元元数据，自动切片被门禁；完整结果不受影响。
- failed/cancelled AI Job 从原行重排队并保留 checkpoint；损坏或模式漂移的分析元数据无法进入任何切片入口。
- 定向测试、全量隔离 pytest、Ruff、Compileall、JavaScript 语法和 `git diff --check` 通过，活动数据库哈希不变。

## 测试命令

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_split_services.py tests/test_job_queue.py tests/test_job_fencing.py tests/test_auto_pipeline.py tests/test_pipeline_checkpoint.py tests/test_provider_resilience.py tests/test_variety_comedy_selection.py
.\.venv\Scripts\python.exe -m pytest -q --disable-warnings -k "not test_real_ffmpeg_render_supports_three_aspect_ratios"
.\.venv\Scripts\ruff.exe check app tests
.\.venv\Scripts\python.exe -m compileall -q app
node --check app/static/js/app.js
git diff --check
```

## 返回格式

- 列出修改文件、原子性/lease/恢复语义、测试结果和活动 DB 哈希。
- 明确剩余无法消除的外部 Provider 已受理但本地尚未落账窗口。
- 不声称测试等于真实 AI 调用或真实发布验证。

# PR4 执行任务：字幕审核、渲染与自动流水线整合

## 背景

PR3 已建立原片/切片统一字幕轨、不可变 revision、毫秒级 cue、专业编辑器和动态 ASS。当前自动流水线仍在切片后跳过字幕，同步 FFmpeg 会阻塞 HTTP，发送中心也只凭 `subtitle_jobs.status=completed` 判断带字幕成片。

## 目标

1. 自动任务在切片后生成字幕草稿并进入 `PENDING_SUBTITLE_REVIEW`，不创建发布任务。
2. 用户可明确选择“审核并批量烧录”或“跳过字幕，使用原片继续”。
3. 字幕烧录使用 `workflow_jobs` 持久化队列，支持真实进度、取消、失败重试、checkpoint 和重启接管。
4. 渲染固定引用 approved revision，使用临时输出、NVENC 失败回退 libx264、FFprobe 验证和成功后原子切换；失败保留旧 active 成片。
5. AI 纠错只创建非 active 建议 revision，只能改文字和 cue 内断行；用户查看 diff 后选择接受。
6. 发送中心只自动使用“approved revision + verified render”的字幕成片；明确跳过字幕时允许原片继续。

## 允许修改范围

- `app/models/task.py`、`app/models/subtitle.py`
- `app/db/database.py`
- `app/services/pipeline_engine.py`、`job_service.py`、`job_worker.py`
- `app/services/subtitle_data_service.py`、`subtitle_workflow_service.py`、新增字幕 AI 服务
- `app/services/auto_publish_service.py`、`publish_service.py`、必要的发布 readiness 门禁
- `app/routers/tasks.py`、`app/routers/subtitles.py`
- 字幕工作台、任务详情页及其 JS/CSS
- PR4 相关测试与项目文档

## 禁止修改范围

- 不改抖音/B站真实登录、验证码、风控和 Publisher 实现。
- 不自动发布、部署、合并 PR 或删除用户数据。
- 不引入说话人自动分离、翻译、卡拉 OK、多模态游戏选片。
- 不读取或写入 API Key、Token、Cookie、`.env` 内容。

## 已确定实现要求

- 自动任务暂停是正常终态，不作为失败；恢复从 `METADATA_GENERATING` 开始。
- 批量烧录的 payload 固定保存每条 `output_clip_id/revision_id`，重试复用 checkpoint，已成功条目不重复渲染。
- 单条和批量 HTTP 入口都只入队，不同步运行 FFmpeg。
- 字幕成片新增验证状态；旧版 completed 字幕按未验证处理，不自动进入发送中心。
- explicit skip 决策写入任务 `auto_config_json`，不得由“没有字幕”静默推断。
- AI 建议不可激活、不可改变时间戳/说话人；接受建议后生成新的人工草稿 revision。

## 验收标准

- 切片后生成全部 clip track 草稿并暂停，未创建 publish job。
- 审核批量烧录成功后自动排队恢复流水线，发布任务使用 verified subtitle。
- 明确跳过后恢复流水线，发布任务使用 original。
- 未审批、未验证、revision 已过期的字幕不能作为发布源。
- Job 可查询、取消、失败重试；取消/失败不覆盖旧 active 成片，不残留 `.part`。
- 9:16、16:9、1:1、中文字体、音频映射、NVENC 回退、FFprobe 验证均有测试。
- AI diff 支持选择接受，建议 revision 永不自动覆盖 active revision。

## 测试命令

```powershell
.venv\Scripts\python.exe -m ruff check app tests
.venv\Scripts\python.exe -m compileall -q app tests
node --check app/static/js/app.js
node --check app/static/js/subtitle-editor.js
.venv\Scripts\python.exe -m pytest tests/test_subtitle_auto_workflow.py tests/test_subtitle_editor.py tests/test_auto_pipeline.py tests/test_job_queue.py tests/test_publish_readiness.py tests/test_publish_task_linkage.py -q
.venv\Scripts\python.exe -m pytest tests/ -q
```

## 返回格式

- Luna Operator 只返回准确命令、通过/失败数、耗时、警告和失败证据，不修改生产文件。
- 主代理检查实际 diff、范围外修改、敏感信息、TODO/debug、临时文件和 Git 状态后再验收。

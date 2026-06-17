# 数据库结构

当前数据库是 SQLite，默认路径为：

```text
data/workflow.sqlite3
```

测试环境会使用独立测试数据库，不应影响真实数据。

## 1. 初始化方式

数据库初始化在 `app/db/database.py` 中完成：

- `init_db()` 启动时执行。
- 使用 `CREATE TABLE IF NOT EXISTS` 创建表。
- 使用逐列 `ALTER TABLE` 迁移旧数据库。
- 自动写入默认 AI Prompt、字幕样式、发布平台配置。

## 2. 当前真实表清单

当前代码会创建 13 张表：

| 表名 | 作用 |
|---|---|
| `tasks` | 主任务表，保存任务名称、视频来源、状态、进度、错误信息 |
| `clip_candidates` | AI 候选片段，供人工审核和切片使用 |
| `output_clip` | 自动切片输出记录 |
| `ai_prompt_presets` | AI 分析 Prompt 方案 |
| `ai_analysis_runs` | 每次 AI 分析历史，可恢复 |
| `subtitle_style_presets` | 字幕样式预设 |
| `subtitle_jobs` | 字幕生成任务和结果 |
| `publish_platform_configs` | 抖音 / B站平台配置 |
| `publish_accounts` | 发布账号记录 |
| `publish_jobs` | 发送中心任务 |
| `oauth_states` | OAuth state 防重放记录，当前属于预留/安全基础设施 |
| `workflow_jobs` | 本地轻量任务队列，当前主要用于异步切片 |
| `cut_runs` | 每次切片运行记录，用于版本化和失败回滚 |

## 3. 主任务表 tasks

关键字段：

- `id`：任务 ID。
- `task_name`：任务名称。
- `task_dir_name`：任务目录名。
- `source_type`：`upload` 或 `nas`。
- `platform`：素材平台类型，如 `general`、`douyin`、`bilibili`。
- `original_video_path`：上传视频路径。
- `nas_file_path`：本地或 NAS 已有视频路径。
- `max_clip_duration`：候选片段最长分钟数。
- `candidate_clip_count`：希望 AI 生成的候选数量。
- `ai_prompt_preset_id`：使用的 Prompt 方案。
- `status`：主流程状态。
- `progress`：页面进度百分比。
- `error_message`：失败原因。
- `is_deleted` / `deleted_at`：软删除标记。

## 4. AI 相关表

### clip_candidates

保存当前可审核、可切片的候选片段。

关键字段：

- `clip_key`：AI 返回的片段 key。
- `title`：片段标题。
- `start_time` / `end_time`：片段时间范围。
- `duration_seconds`：时长。
- `summary`：内容摘要。
- `highlight_reason`：高光原因。
- `spread_value`：传播价值。
- `suggested_editing`：剪辑建议。
- `confidence_score`：AI 置信度。
- `enabled`：是否参与切片。
- `reviewed`：是否已审核。
- `is_deleted` / `deleted_at`：软删除。

### ai_analysis_runs

保存每次 AI 分析的完整 payload，可用于恢复历史结果。

关键字段：

- `run_number`：第几次分析。
- `provider`：`remote` 或 `local`。
- `provider_label`：展示名称。
- `model`：模型名。
- `ai_prompt_preset_id` / `ai_prompt_preset_name`：Prompt 信息。
- `requested_clip_count`：请求候选数量。
- `clip_count`：实际候选数量。
- `analysis_payload_json`：完整 AI 结果 JSON。
- `is_active`：当前激活结果。

## 5. 切片与字幕表

### cut_runs

每次点击生成切片都会创建一条记录。

- 成功时激活当前 run。
- 新 run 成功后旧 run 的输出会变为非活跃。
- 新 run 全部失败时，旧活跃输出保留。

### output_clip

保存每条切片结果。

关键字段：

- `clip_candidate_id`：来源候选片段。
- `cut_run_id`：来源切片运行。
- `output_file_path` / `output_file_name`：文件路径和文件名。
- `status`：`completed` 或 `failed`。
- `error_message`：失败原因。
- `is_active`：是否为当前活跃结果。

### subtitle_jobs

保存字幕生成记录。

关键字段：

- `output_clip_id`：对应切片。
- `subtitle_file_path`：ASS 字幕文件。
- `output_file_path`：带字幕视频。
- `status`：`pending`、`processing`、`completed`、`failed`。
- `is_active`：是否为当前活跃字幕结果。

## 6. 发送中心表

### publish_jobs

保存发送中心任务。

关键字段：

- `platform`：`douyin` 或 `bilibili`。
- `provider`：当前实际以 `opencli` 为主。
- `output_clip_id`：来源切片。
- `video_source`：`original` 或 `subtitled`。
- `title` / `description` / `tags`：发布文案。
- `cover_file_path` / `cover_time_seconds`：封面信息。
- `status`：`ready`、`publishing`、`published`、`failed`、`cancelled`。
- `scheduled_at`：计划发布时间字段预留，不会自动调度。

### publish_platform_configs / publish_accounts

用于保存平台配置和账号记录。当前发送中心主要依赖 opencli 调用已登录 Chrome，平台 API 能力属于预留边界。

### oauth_states

用于 OAuth state 安全校验。当前属于安全基础设施和后续平台授权能力预留。

## 7. 本地轻量队列表 workflow_jobs

`workflow_jobs` 是本项目自己的轻量队列表，当前主要用于异步切片。

关键字段：

- `job_type`：任务类型。
- `task_id`：关联主任务。
- `status`：`queued`、`running`、`completed`、`failed`。
- `progress`：任务进度。
- `payload_json`：输入参数。
- `result_json`：结果。
- `error_message`：失败原因。

它不是 Celery，也不需要 Redis。

## 8. 文件不入库原则

大文件不写进 SQLite：

- 原始视频。
- 音频。
- 切片视频。
- 字幕视频。
- 封面图。
- 日志文件。

数据库只保存路径、状态和元数据。真实文件保存在任务目录中。

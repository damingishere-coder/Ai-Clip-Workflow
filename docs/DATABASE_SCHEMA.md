# 数据库结构说明

## 2026-05-27：任务目录改为项目名

- `tasks` 表新增 `task_dir_name` 字段，用来记录任务在存储盘里的实际文件夹名。
- `id` 仍是任务唯一 ID，用于数据库关联和网页地址；本地文件夹不再默认使用短 ID，而是使用 `task_dir_name`。
- 新建任务时会根据 `task_name` 生成安全的 Windows 文件夹名；重名时自动追加序号，避免覆盖旧目录。
- `DELETE /api/tasks/{task_id}` 现在会把 `is_deleted` 设为 `1`，写入 `deleted_at`，并把任务文件夹移动到存储根目录下的 `_回收站`；不会删除文件。
- 一次性迁移脚本为 `scripts/migrate_task_dirs_to_project_names.py`，默认 dry-run，带 `--apply` 才会移动文件夹并更新路径字段。

## 2026-05-25：发布后台新增表

- 新增 `publish_platform_configs`：保存抖音 / B站开放平台应用配置、OAuth 地址、上传接口、创建 / 投稿接口和测试结果。
- 新增 `publish_accounts`：保存发布账号、open_id / UID、access_token、refresh_token、授权状态和备注；不保存平台账号密码。
- 新增 `publish_jobs`：保存每条切片的发布任务、视频来源、账号、标题、简介、标签、平台返回 ID、审核状态、错误码、错误信息和重试次数。

### publish_platform_configs 表

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `platform` | TEXT | 平台：`douyin` 或 `bilibili` |
| `client_key` | TEXT | Client Key / App Key |
| `client_secret` | TEXT | Client Secret，本地保存，页面脱敏展示 |
| `redirect_uri` | TEXT | OAuth 回调地址 |
| `upload_url` | TEXT | 视频上传接口 |
| `create_url` | TEXT | 创建视频 / 投稿接口 |
| `last_test_status` | TEXT | 最近一次配置检查状态 |
| `last_test_message` | TEXT | 最近一次配置检查说明 |

### publish_accounts 表

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT | 账号记录 ID |
| `platform` | TEXT | 平台 |
| `account_name` | TEXT | 本地展示昵称 |
| `open_id` | TEXT | 开放平台 open_id |
| `access_token` | TEXT | 接口访问 token |
| `refresh_token` | TEXT | 刷新 token |
| `authorization_status` | TEXT | 授权状态：`manual` / `authorized` |
| `remark` | TEXT | 备注 |

### publish_jobs 表

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT | 发布任务 ID |
| `task_id` | TEXT | 所属视频任务 ID |
| `output_clip_id` | TEXT | 所属输出切片 ID |
| `account_id` | TEXT | 发布账号 ID |
| `platform` | TEXT | 发布平台 |
| `publish_mode` | TEXT | `draft`、`manual_review`、`api_publish` 或 `opencli_publish` |
| `video_source` | TEXT | `original` 或 `subtitled` |
| `video_file_path` | TEXT | 本次发布使用的视频路径 |
| `title` | TEXT | 标题 |
| `description` | TEXT | 简介 / 正文 |
| `tags` | TEXT | 标签 |
| `status` | TEXT | `ready` / `publishing` / `published` / `failed` / `cancelled` |
| `audit_status` | TEXT | 平台审核状态 |
| `platform_item_id` | TEXT | 平台稿件 / 视频 ID |
| `platform_upload_id` | TEXT | 平台上传 ID |
| `error_code` | TEXT | 平台错误码 |
| `error_message` | TEXT | 错误说明 |
| `provider_response` | TEXT | 平台响应摘要 JSON |
| `retry_count` | INTEGER | 重试次数 |
| `scheduled_at` | TEXT | 计划发布时间（v1.2 仅字段预留，尚无后台定时调度器） |

## 2026-05-23：AI Prompt 方案

- `tasks` 表新增 `ai_prompt_preset_id`，记录当前任务使用哪一套 AI 分析 Prompt。
- 新增 `ai_prompt_presets` 表，用于保存全局共用的 1、2、3 号 Prompt 方案；2 号方案为空时会自动写入"综艺访谈完整上下文专家"Prompt，若 2 号已有内容且 3 号为空，则写入 3 号以避免覆盖已有 Prompt。
- 新增 `ai_analysis_runs` 表，用于保存每一次 AI 分析历史，支持刷新后继续展示分析预览和恢复旧分析结果。

### ai_prompt_presets 表

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT | Prompt 方案 ID，例如 `preset_001` |
| `slot` | INTEGER | 方案编号，当前固定为 1、2、3 |
| `name` | TEXT | 用户可编辑的方案名称 |
| `prompt_text` | TEXT | 完整 AI 分析 Prompt |
| `is_default` | INTEGER | 是否默认方案 |
| `created_at` | TEXT | 创建时间 |
| `updated_at` | TEXT | 更新时间 |

### ai_analysis_runs 表

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT | 历史分析 ID |
| `task_id` | TEXT | 所属任务 ID |
| `run_number` | INTEGER | 第几次分析 |
| `provider` | TEXT | 实际使用的 AI 来源 |
| `provider_label` | TEXT | 页面展示用来源名称 |
| `model` | TEXT | 实际使用模型 |
| `ai_prompt_preset_id` | TEXT | 当次使用的 Prompt 方案 ID |
| `ai_prompt_preset_name` | TEXT | 当次使用的 Prompt 方案名称 |
| `requested_clip_count` | INTEGER | 当次请求输出的候选片段数量 |
| `clip_count` | INTEGER | 当次实际生成的候选片段数量 |
| `analysis_summary` | TEXT | 当次整体分析总结 |
| `fallback_notice` | TEXT | 远程降级本地等提示 |
| `analysis_payload_json` | TEXT | 完整 AI 分析结果 JSON |
| `created_at` | TEXT | 创建时间 |

## 存储与数据库位置

当前数据库使用 SQLite，数据库文件默认位于项目目录：

```text
data/workflow.sqlite3
```

大型视频、音频、转写 Markdown 和后续输出文件不放进数据库，统一放在存储根目录下的任务目录中。存储根目录由 `STORAGE_ROOT` 或 `TASKS_DIR` 环境变量配置，默认值为 `E:\直播间切片工作流存储`。

任务产物路径由 `task_dir_name` 决定；`task_id` 只作为数据库关联、URL 和内部唯一 ID 使用，不再直接决定文件夹名。

## tasks 表

`tasks` 表用于保存直播视频处理任务的基础信息和状态。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT | 任务唯一 ID，创建时自动生成 |
| `task_name` | TEXT | 任务名称 |
| `task_dir_name` | TEXT | 存储盘实际任务文件夹名；正常任务通常等于项目名，已移入回收站的任务为 `_回收站\项目名` |
| `source_type` | TEXT | 视频来源：`upload` 或 `nas` |
| `platform` | TEXT | 平台类型：`douyin`、`bilibili`、`general` |
| `original_video_path` | TEXT | 本地上传视频路径，后续接真实上传后写入 |
| `nas_file_path` | TEXT | NAS / 本地已有视频路径 |
| `max_clip_duration` | INTEGER | 单条切片最长时长，单位：分钟；新建任务默认建议为 5 分钟 |
| `candidate_clip_count` | INTEGER | 希望 AI 输出的候选片段数量 |
| `ai_preference` | TEXT | AI 片段选择偏好 |
| `ai_prompt_preset_id` | TEXT | 当前使用的 AI Prompt 方案 ID |
| `status` | TEXT | 当前任务状态 |
| `progress` | INTEGER | 当前进度百分比，后续流水线推进时更新 |
| `error_message` | TEXT | 异常信息 |
| `is_deleted` | INTEGER | 是否已从页面列表隐藏，`1` 表示隐藏，文件不会被删除 |
| `deleted_at` | TEXT | 隐藏时间，ISO 格式 |
| `created_at` | TEXT | 创建时间，ISO 格式 |
| `updated_at` | TEXT | 更新时间，ISO 格式 |

## 任务状态值

`status` 使用英文状态码保存，页面展示时再转换成中文。

| 状态码 | 中文展示 |
| --- | --- |
| `pending_video` | 待提交视频 |
| `pending_processing` | 待处理 |
| `audio_extracting` | 音频提取中 |
| `transcribing` | 转写中 |
| `pending_ai` | 待 AI 分析 |
| `ai_analyzing` | AI 分析中 |
| `pending_review` | AI 结果待检查 |
| `cutting` | 切割中 |
| `completed` | 已完成 |
| `completed_with_errors` | 部分完成，至少有一个切片成功，但也有切片失败 |
| `failed` | 失败 |

注意：`completed` / `completed_with_errors` 表示"自动切割阶段结束"，不是平台发布完成。字幕是 `subtitle_jobs` 独立流程，发送中心是 `publish_jobs` 独立流程，它们不直接混入 `tasks.status`。

## output_clip 表

`output_clip` 表用于保存每一条最终切片输出结果。视频文件本身仍保存在任务目录里，数据库只保存路径、状态和错误信息。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT | 输出记录唯一 ID |
| `task_id` | TEXT | 所属任务 ID |
| `clip_candidate_id` | TEXT | 来源候选片段 ID |
| `output_file_path` | TEXT | 输出视频完整路径 |
| `output_file_name` | TEXT | 输出视频文件名 |
| `status` | TEXT | 输出状态：`pending`、`processing`、`completed`、`failed` |
| `error_message` | TEXT | 单条切片失败原因 |
| `created_at` | TEXT | 创建时间，ISO 格式 |
| `updated_at` | TEXT | 更新时间，ISO 格式 |

## subtitle_jobs 表

`subtitle_jobs` 表用于保存每条切片的字幕生成任务，是独立于 `tasks.status` 的字幕工作流。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT | 字幕任务 ID |
| `task_id` | TEXT | 所属任务 ID |
| `output_clip_id` | TEXT | 所属输出切片 ID |
| `style_preset_id` | TEXT | 字幕样式 ID |
| `status` | TEXT | 状态：`pending`、`processing`、`completed`、`failed` |
| `subtitle_file_path` | TEXT | 字幕文件路径（.ass） |
| `output_file_path` | TEXT | 带字幕视频输出路径 |
| `error_message` | TEXT | 错误信息 |
| `created_at` | TEXT | 创建时间 |
| `updated_at` | TEXT | 更新时间 |

## subtitle_style_presets 表

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT | 样式 ID |
| `name` | TEXT | 样式名称 |
| `font_family` | TEXT | 字体 |
| `font_size` | INTEGER | 字号 |
| `position` | TEXT | 位置：`bottom_center`、`middle_lower`、`top_center` |
| `font_color` | TEXT | 字体颜色 |
| `stroke_color` | TEXT | 描边颜色 |
| `shadow_enabled` | INTEGER | 是否启用阴影 |
| `is_default` | INTEGER | 是否默认样式 |
| `created_at` | TEXT | 创建时间 |
| `updated_at` | TEXT | 更新时间 |

## clip_candidates 表

`clip_candidates` 表用于保存 AI 分析生成、等待人工审核的候选短视频片段，也保存人工审核页写回的编辑结果。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT | 候选片段数据库 ID |
| `task_id` | TEXT | 所属任务 ID |
| `clip_key` | TEXT | AI 返回的片段 key，例如 `clip_001` |
| `title` | TEXT | 片段标题，可在审核页人工修改 |
| `start_time` | TEXT | 开始时间，保存为 `HH:MM:SS` |
| `end_time` | TEXT | 结束时间，保存为 `HH:MM:SS` |
| `duration_seconds` | INTEGER | 片段时长，单位秒，保存审核修改时自动重算 |
| `summary` | TEXT | 片段摘要，可在审核页人工修改 |
| `reason` | TEXT | 兼容旧字段，当前与推荐理由保持一致 |
| `highlight_reason` | TEXT | AI 推荐理由 |
| `spread_value` | TEXT | 传播价值 |
| `suggested_editing` | TEXT | 剪辑建议 |
| `confidence_score` | REAL | AI 置信度，范围 0 到 1 |
| `selected_by_default` | INTEGER | AI 是否建议默认启用 |
| `enabled` | INTEGER | 人工审核时是否启用，`1` 启用，`0` 禁用 |
| `reviewed` | INTEGER | 是否已人工修改或审核，保存后写为 `1` |
| `is_deleted` | INTEGER | 候选片段是否已从审核页隐藏，`1` 表示隐藏 |
| `deleted_at` | TEXT | 候选片段隐藏时间，ISO 格式 |
| `created_at` | TEXT | 创建时间，ISO 格式 |
| `updated_at` | TEXT | 更新时间，ISO 格式 |

## 兼容说明

早期项目骨架曾使用过 `title`、`source_path`、`max_clip_minutes`、`target_clip_count` 等草稿字段。当前初始化逻辑会自动补齐新字段，并把旧字段数据迁移到当前字段中。
为了不破坏已有本地数据库，旧字段不会被强制删除。后续代码以本文件列出的当前字段为准。

任务移入回收站采用软删除方式：`DELETE /api/tasks/{task_id}` 会把 `is_deleted` 改为 `1`、写入 `deleted_at`，并把该任务的存储目录移动到 `_回收站`。工作台、任务列表和片段审核总览默认不显示已移入回收站的任务，原视频、音频、转写、AI 分析文件、切片输出和字幕输出都会保留。

`clip_candidates.reason` 是早期推荐理由字段，当前审核页优先读取 `highlight_reason`。数据库初始化时会把已有 `reason` 自动补到 `highlight_reason`。

候选片段删除是软删除：`DELETE /api/tasks/{task_id}/clips/{clip_id}` 只更新数据库记录，不删除源视频、转写文件、AI 分析文件或已生成切片文件。

## 任务产物路径

任务产物路径当前由 `task_dir_name` 决定；`task_id` 只作为内部唯一 ID 使用，不再直接决定存储文件夹名。

历史任务可能仍兼容 `task_id` 目录，但新逻辑以 `task_dir_name` 为准。

正式任务目录结构：

```text
{task_dir_name}/
  source/          ← 上传源视频
  audio/           ← 提取的音频 source.wav
  transcripts/     ← 转写结果 transcript.md
  analysis/        ← AI 分析结果 candidate_clips.json
  05_clips/        ← 正式切片输出目录
  06_subtitled/    ← 带字幕成片目录
  07_covers/       ← 发送中心候选封面目录
  logs/            ← 处理日志 process.log
```

| 产物 | 路径 |
| --- | --- |
| 任务目录 | `{存储根目录}/{task_dir_name}/` |
| 上传源视频 | `source/原文件名` |
| 提取音频 | `audio/source.wav` |
| 转写 Markdown | `transcripts/transcript.md` |
| AI 分析文件 | `analysis/candidate_clips.json` |
| 输出切片 | `05_clips/` |
| 带字幕成片 | `06_subtitled/` |
| 候选封面 | `07_covers/` |
| 处理日志 | `logs/process.log` |

## 2026-06-09 v1.2 补充说明

- 发送中心当前已有发送队列和 opencli 辅助投稿能力，但还没有真正的定时调度器。
- `publish_jobs.scheduled_at` 当前只是字段预留，可以保存计划发布时间，但 v1.2 还没有后台定时调度器，不会自动按 `scheduled_at` 发送。
- 平台发送依赖 opencli 辅助浏览器操作，不绕过验证码、登录失效、风控和人工确认。
- 代码中仍存在兼容性 `clips` 子目录（`TASK_SUBDIRECTORIES` 同时包含 `clips` 和 `05_clips`），新任务的正式输出目录是 `05_clips`。旧 `clips` 目录为兼容保留，不建议删除。

# 数据库结构说明

当前数据库使用 SQLite，数据库文件默认位于：

```text
data/workflow.sqlite3
```

大型视频、音频、转写 Markdown 和后续输出文件不放进数据库，统一放在：

```text
E:\直播间切片工作流存储\{task_id}\
```

## tasks 表

`tasks` 表用于保存直播视频处理任务的基础信息和状态。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT | 任务唯一 ID，创建时自动生成 |
| `task_name` | TEXT | 任务名称 |
| `source_type` | TEXT | 视频来源：`upload` 或 `nas` |
| `platform` | TEXT | 平台类型：`douyin`、`bilibili`、`general` |
| `original_video_path` | TEXT | 本地上传视频路径，后续接真实上传后写入 |
| `nas_file_path` | TEXT | NAS / 本地已有视频路径 |
| `max_clip_duration` | INTEGER | 单条切片最长时长，单位：分钟 |
| `candidate_clip_count` | INTEGER | 希望 AI 输出的候选片段数量 |
| `ai_preference` | TEXT | AI 片段选择偏好 |
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
| `pending_review` | 待人工审核 |
| `cutting` | 切割中 |
| `completed` | 已完成 |
| `completed_with_errors` | 部分完成，至少有一个切片成功，但也有切片失败 |
| `failed` | 失败 |

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

## 兼容说明

早期项目骨架曾使用过 `title`、`source_path`、`max_clip_minutes`、`target_clip_count` 等草案字段。当前初始化逻辑会自动补齐新字段，并把旧字段数据迁移到当前字段中。

为了不破坏已有本地数据库，旧字段不会被强制删除。后续代码以本文件列出的当前字段为准。

任务隐藏采用软删除方式：`DELETE /api/tasks/{task_id}` 只会把 `is_deleted` 改为 `1` 并写入 `deleted_at`。工作台、任务列表和片段审核总览默认不显示隐藏任务，但 E 盘任务目录、原视频、音频、转写、AI 分析文件和切片输出都会保留。

`clip_candidates.reason` 是早期推荐理由字段，当前审核页优先读取 `highlight_reason`。数据库初始化时会把已有 `reason` 自动补到 `highlight_reason`。

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
| `created_at` | TEXT | 创建时间，ISO 格式 |
| `updated_at` | TEXT | 更新时间，ISO 格式 |

## 任务产物路径

任务产物路径当前由 `task_id` 推导，不额外写入数据库：

| 产物 | 路径 |
| --- | --- |
| 任务目录 | `E:\直播间切片工作流存储\{task_id}\` |
| 上传源视频 | `source\原文件名` |
| 提取音频 | `audio\source.wav` |
| 转写 Markdown | `transcripts\transcript.md` |
| AI 分析文件 | `analysis\candidate_clips.json` |
| 输出切片 | `05_clips\` |
| 处理日志 | `logs\process.log` |

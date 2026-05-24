# 数据库结构说明

当前数据库使用 SQLite，默认文件位置：

```text
data/workflow.sqlite3
```

大型视频、音频、转写 Markdown、AI 分析结果和切片输出不放进数据库，统一保存在：

```text
E:\直播间切片工作流存储\{task_id}\
```

## tasks 表

`tasks` 保存每条直播视频处理任务的基础信息、状态和进度。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT | 任务唯一 ID，创建时自动生成 |
| `task_name` | TEXT | 任务名称 |
| `source_type` | TEXT | 视频来源，当前页面默认使用 `upload`，兼容保留 `nas` |
| `platform` | TEXT | 平台类型：`douyin`、`bilibili`、`general` |
| `original_video_path` | TEXT | 上传视频保存路径 |
| `nas_file_path` | TEXT | 兼容保留的 NAS / 本地已有视频路径 |
| `max_clip_duration` | INTEGER | 单条切片最长时长，单位：分钟 |
| `candidate_clip_count` | INTEGER | 希望 AI 输出的候选片段数量 |
| `ai_preference` | TEXT | 任务级 AI 偏好 |
| `ai_prompt_preset_id` | TEXT | 当前任务选择的 AI Prompt 方案 ID |
| `status` | TEXT | 当前任务状态 |
| `progress` | INTEGER | 当前进度百分比 |
| `error_message` | TEXT | 异常信息 |
| `is_deleted` | INTEGER | 是否从页面列表隐藏，`1` 表示隐藏，文件不会删除 |
| `deleted_at` | TEXT | 隐藏时间，ISO 格式 |
| `created_at` | TEXT | 创建时间，ISO 格式 |
| `updated_at` | TEXT | 更新时间，ISO 格式 |

## 任务状态值

| 状态码 | 页面展示 |
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
| `completed_with_errors` | 部分完成 |
| `failed` | 失败 |

## clip_candidates 表

`clip_candidates` 保存 AI 生成并等待人工审核的候选短视频片段，也保存人工审核页写回的编辑结果。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT | 候选片段数据库 ID |
| `task_id` | TEXT | 所属任务 ID |
| `clip_key` | TEXT | AI 返回的片段 key，例如 `clip_001` |
| `title` | TEXT | 片段标题，可在审核页修改 |
| `start_time` | TEXT | 开始时间，格式 `HH:MM:SS` |
| `end_time` | TEXT | 结束时间，格式 `HH:MM:SS` |
| `duration_seconds` | INTEGER | 片段时长，单位：秒 |
| `summary` | TEXT | 内容摘要 |
| `reason` | TEXT | 兼容旧字段，当前优先读取 `highlight_reason` |
| `highlight_reason` | TEXT | AI 推荐理由 |
| `spread_value` | TEXT | 传播价值 |
| `suggested_editing` | TEXT | 剪辑建议 |
| `confidence_score` | REAL | AI 置信度，范围 0 到 1 |
| `selected_by_default` | INTEGER | AI 是否建议默认启用 |
| `enabled` | INTEGER | 人工审核后是否启用，`1` 启用，`0` 禁用 |
| `reviewed` | INTEGER | 是否已人工修改或审核 |
| `created_at` | TEXT | 创建时间，ISO 格式 |
| `updated_at` | TEXT | 更新时间，ISO 格式 |

## output_clip 表

`output_clip` 保存每条最终切片输出结果。视频文件仍保存在任务目录中，数据库只保存路径、状态和错误信息。

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

## subtitle_style_presets 表

`subtitle_style_presets` 保存自动加字幕使用的默认样式。当前 MVP 只使用 `id = default` 的默认模板。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT | 字幕样式 ID |
| `name` | TEXT | 样式名称 |
| `font_family` | TEXT | 字体，例如 `Microsoft YaHei` |
| `font_size` | INTEGER | 字号 |
| `position` | TEXT | 字幕位置，例如 `bottom_center`、`middle_lower`、`top_center` |
| `font_color` | TEXT | 文字颜色，十六进制格式 |
| `stroke_color` | TEXT | 描边颜色，十六进制格式 |
| `shadow_enabled` | INTEGER | 是否启用阴影和描边，`1` 启用 |
| `is_default` | INTEGER | 是否默认样式，`1` 表示默认 |
| `created_at` | TEXT | 创建时间，ISO 格式 |
| `updated_at` | TEXT | 更新时间，ISO 格式 |

## subtitle_jobs 表

`subtitle_jobs` 保存每条输出切片的字幕生成状态。带字幕视频文件仍保存在任务目录，数据库只保存路径和状态。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT | 字幕任务 ID |
| `task_id` | TEXT | 所属任务 ID |
| `output_clip_id` | TEXT | 对应的输出切片 ID |
| `style_preset_id` | TEXT | 使用的字幕样式 ID，当前默认 `default` |
| `status` | TEXT | 字幕状态：`pending`、`processing`、`completed`、`failed` |
| `subtitle_file_path` | TEXT | 生成的 `.ass` 字幕文件路径 |
| `output_file_path` | TEXT | 生成的带字幕 MP4 文件路径 |
| `error_message` | TEXT | 字幕生成失败原因 |
| `created_at` | TEXT | 创建时间，ISO 格式 |
| `updated_at` | TEXT | 更新时间，ISO 格式 |

## publish_jobs 表

`publish_jobs` 保存每条切片面向抖音 / B站的本地发布任务记录。当前版本不保存账号密码，也不会直接真实发布到平台。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT | 发布任务 ID |
| `task_id` | TEXT | 所属任务 ID |
| `output_clip_id` | TEXT | 对应的输出切片 ID |
| `platform` | TEXT | 发布平台：`douyin`、`bilibili` |
| `video_source` | TEXT | 视频来源：`original` 原始切片、`subtitled` 带字幕成片 |
| `video_file_path` | TEXT | 本次选择用于发布的视频文件路径 |
| `title` | TEXT | 发布标题 |
| `description` | TEXT | 发布简介 |
| `tags` | TEXT | 发布标签，当前用文本保存 |
| `status` | TEXT | 发布状态：`draft`、`ready`、`publishing`、`published`、`failed`、`cancelled` |
| `error_message` | TEXT | 发布失败或取消原因 |
| `provider_response` | TEXT | 后续真实平台接口返回内容或摘要 |
| `created_at` | TEXT | 创建时间，ISO 格式 |
| `updated_at` | TEXT | 更新时间，ISO 格式 |

## ai_prompt_presets 表

`ai_prompt_presets` 保存全局共用的 1、2、3 号 AI 分析 Prompt 方案。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT | Prompt 方案 ID，例如 `preset_001` |
| `slot` | INTEGER | 方案编号，当前固定为 1、2、3 |
| `name` | TEXT | 用户可编辑的方案名称 |
| `prompt_text` | TEXT | 完整 AI 分析 Prompt |
| `is_default` | INTEGER | 是否默认方案 |
| `created_at` | TEXT | 创建时间 |
| `updated_at` | TEXT | 更新时间 |

## ai_analysis_runs 表

`ai_analysis_runs` 保存每一次 AI 分析历史，支持刷新后继续展示分析预览和恢复旧分析结果。

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

## 兼容说明

早期项目骨架曾使用过 `title`、`source_path`、`max_clip_minutes`、`target_clip_count` 等草稿字段。当前初始化逻辑会自动补齐新字段，并把旧字段数据迁移到当前字段中。

为了不破坏已有本地数据库，旧字段不会被强制删除；后续代码以本文档列出的当前字段为准。

任务隐藏采用软删除方式：`DELETE /api/tasks/{task_id}` 只会把 `is_deleted` 改为 `1` 并写入 `deleted_at`。工作台、任务列表和片段审核总览默认不显示隐藏任务，但 E 盘任务目录、原视频、音频、转写、AI 分析文件和切片输出都会保留。

## 任务产物路径

| 产物 | 路径 |
| --- | --- |
| 任务目录 | `E:\直播间切片工作流存储\{task_id}\` |
| 上传源视频 | `source\原文件名` |
| 提取音频 | `audio\source.wav` |
| 转写 Markdown | `transcripts\transcript.md` |
| AI 分析文件 | `analysis\candidate_clips.json` |
| 输出切片 | `05_clips\` |
| 带字幕成片 | `06_subtitled\` |
| 处理日志 | `logs\process.log` |

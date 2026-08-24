# 数据库结构说明

## 2026-08-24：字幕自动流水线字段

`subtitle_jobs` 在原有不可变 revision 引用上增加：

| 字段 | 说明 |
| --- | --- |
| `workflow_job_id` | 所属持久化字幕 Job，用于取消、恢复和精确清理临时文件 |
| `validation_status` | `pending / verified`；旧记录默认为未验证，不能自动发布 |
| `validation_json` | FFprobe 的编码、像素格式、音轨、时长和音频处理证据 |
| `encoder` | 实际成功的 `h264_nvenc` 或 `libx264` |
| `verified_at` | 输出验证通过时间 |

- 全自动字幕交付决定保存在 `tasks.auto_config_json.subtitle_delivery_mode`，值仅允许 `original / subtitled`，并记录 `subtitle_decided_at`。
- `workflow_jobs.payload_json` 固定保存每个 output clip 与 revision 的组合；`checkpoint_json.completed` 只复用对应字幕 job 仍为 `completed + verified` 且文件存在的条目。
- 缺少新字段的已有数据库先创建 `subtitle-auto-workflow` SQLite 在线备份，再执行幂等列/索引迁移；旧字幕文件和 job 不删除，但未验证旧记录不会获得自动发布资格。

## 2026-08-23：字幕 revision 数据结构

- `output_clip` 新增 `source_start_ms / source_end_ms / source_duration_ms / source_fingerprint / snapshot_source`。新切片使用 `cut_commit`，旧切片第一次使用时可从候选边界生成 `legacy_inferred`；之后候选修改不会漂移已保存边界。
- `subtitle_tracks`：任务级原片轨或切片轨，记录 `source_track_id / source_revision_id / active_revision_id / sync_status / has_manual_edits`。
- `subtitle_revisions`：不可变内容版本，记录来源 `asr / markdown / source_sync / manual / import / ai_suggestion`、父版本、状态、cue 数和 checksum。
- `subtitle_cues`：毫秒级 `start_ms / end_ms`、文字、置信度、手工说话人和 `source_cue_id`；按 revision 和时间查询。
- `subtitle_jobs.revision_id` 固定渲染使用的版本；样式表新增描边宽度、阴影深度、安全区百分比与说话人样式 JSON。
- 启动迁移保持幂等；已有数据库缺少上述结构时先通过 SQLite 在线 backup 创建 `subtitle-editor-rebuild` 迁移前快照，不删除旧字幕 job 或历史切片。

## 2026-08-23：长直播 AI 窗口 checkpoint

新增 `ai_analysis_windows`：

| 字段 | 说明 |
| --- | --- |
| `task_id` | 所属任务 |
| `transcript_fingerprint` | 完整转写内容 SHA-256；内容变化后不会复用旧窗口 |
| `provider` / `model` | Provider 与模型隔离键 |
| `window_index` | 当前窗口序号 |
| `start_seconds` / `end_seconds` | 原片主时间轴范围 |
| `status` | `queued / running / completed / failed` |
| `attempt_count` | 累计真实请求次数 |
| `result_json` / `result_checksum` | 成功结果与 SHA-256 校验和 |
| `error_message` / `next_retry_at` | 最后错误与退避时间 |
| `created_at / updated_at / completed_at` | 生命周期时间 |

唯一键由任务、转写指纹、Provider、模型、窗口序号和起止时间组成。迁移使用 `CREATE TABLE/INDEX IF NOT EXISTS`，已有数据库在变更前继续执行 SQLite 在线备份。

## 2026-08-23：长直播基础设施迁移

- `tasks` 增加 `highlight_density_per_hour INTEGER NOT NULL DEFAULT 4` 与 `highlight_total_limit INTEGER NOT NULL DEFAULT 30`；历史任务模式和值不改写。
- `selection_profile` 合法值扩展为 `general / variety_comedy / long_live_talk`，数据库继续保留 `DEFAULT 'general'` 兼容旧数据。
- `workflow_jobs` 增加尝试、退避时间、lease、heartbeat、取消和 checkpoint 字段。
- 新增 `transcription_runs`，按任务、源指纹、Provider、模型、设备、计算类型和分块参数标识一次转写。
- 新增 `transcription_chunks`，逐块保存毫秒边界、状态、尝试次数、结构化 JSON、SHA-256 校验和与错误。
- 新索引覆盖 Job 领取、任务类型状态、活跃转写 run 与转写块状态。
- 已存在数据库缺少新结构时，迁移前使用 SQLite Online Backup API 写入 `data/backups/workflow-before-long-live-foundation-*.sqlite3`，通过 `PRAGMA quick_check` 后才执行幂等增量迁移。

## 2026-08-01：康熙笑点优先 V2 兼容迁移

- `tasks` 新增 `selection_profile TEXT NOT NULL DEFAULT 'general'` 与 `final_clip_target INTEGER NOT NULL DEFAULT 5`。历史任务自动保持 `general`，不会改变原有分析行为。
- `clip_candidates` 新增 `quality_tier`、`quality_score`、`text_quality_score`、`humor_score`、`completeness_score`、`audio_reaction_score`、`topic_key`、`key_moment_time`、`quality_evidence_json` 和 `rejection_reason`。
- 新增 `clip_feedback` 表，保存任务、候选、当次分析、选片模式、保留/拒绝判断、原因、备注和标题/摘要/时间快照；反馈不会删除候选或分析历史。
- 新增索引 `idx_clip_feedback_profile_created` 与 `idx_clip_feedback_task_clip`，用于读取近期个人口味和定位候选反馈。
- 所有变更继续使用 `CREATE TABLE IF NOT EXISTS` 与逐列 `ALTER TABLE ADD COLUMN`；不删除字段、不重建历史表、不改写历史候选。
- `ai_prompt_presets` 新增 4 号内置方案“康熙笑点优先 V2”。若 `preset_004` 已有非空自定义内容，初始化会原样保留，不覆盖用户 Prompt。

## 2026-07-28：SQLite 迁移备份安全与保留规则

- 发布数据迁移只有在发现旧平台值或真正活跃的重复任务时才生成迁移前快照；失败、已发布、已取消和人工复核历史不会触发重复备份。
- 备份与数据修复使用 `BEGIN IMMEDIATE` 串行化。快照由独立只读连接写入唯一临时文件，通过 `PRAGMA quick_check` 后再原子改名；备份失败时数据修复回滚。
- 同类有效备份设置 24 小时冷却时间，并自动保留最近 14 个备份日、每天一份。
- 维护命令为 `.venv\Scripts\python.exe scripts\cleanup_database_backups.py`；默认只预演，添加 `--apply` 才删除 `data/backups/workflow-before-publish-migration-*.sqlite3`。
- 清理前必须保证主数据库和每天拟保留的快照完整；脚本不会删除主数据库、任务素材、浏览器登录状态或其他不匹配的文件。

## 2026-07-28：执行记录安全隐藏与月历查询

- `publish_jobs` 新增 `history_hidden INTEGER NOT NULL DEFAULT 0` 和 `history_hidden_at TEXT`；旧记录迁移后默认可见。
- “删除记录”只允许 `PUBLISHED / FAILED / EXPORTED / CANCELLED`，仅更新上述字段并写入 `publish_job_events`，不删除发布任务、事件、视频、封面、平台链接或重试关系。
- “恢复记录”把 `history_hidden` 恢复为 `0` 并清空 `history_hidden_at`，任务原状态和执行结果保持不变。
- 新增索引 `idx_publish_jobs_history_visibility(history_hidden, platform, status, created_at)`。
- 执行月历日期依次取 `scheduled_at`、`started_at`、`finished_at`、`created_at`；无时区的旧时间按 `Asia/Shanghai` 解释。

## 2026-07-15：v1.5.0 统一真实发布迁移

- 迁移继续使用启动时 `CREATE TABLE IF NOT EXISTS` 和逐列 `ALTER TABLE ADD COLUMN`；不删除旧字段、不重建表、不清空历史数据。
- `publish_jobs` 新增：`claimed_at`、`started_at`、`finished_at`、`max_attempts DEFAULT 3`、`worker_id`、`platform_url`、`needs_manual_review DEFAULT 0`、`timezone DEFAULT 'Asia/Shanghai'`、`next_attempt_at`、`execution_id`、`execution_phase`、`retry_of_job_id`。
- 保留并规范：`publish_mode`、`scheduled_at`、`published_at`、`attempt_count`、`last_error`、`error_code`、`remote_video_id`、`provider_response`、`publish_result`、`schedule_timezone`。
- `publish_accounts` 新增：`login_status`、`login_checked_at`、`login_message`、`last_login_at`、`auth_type`。浏览器账号只记录本地登录状态，不保存账号密码或 Cookie。
- 新增 `publish_job_events`，记录状态流转、原子领取、恢复、安全重试、平台结果及人工操作。
- 新索引：`idx_publish_jobs_due_retry`、`idx_publish_jobs_execution`、`idx_publish_job_events_job_time`；活跃唯一索引只约束 `DRAFT / WAITING / SCHEDULED / PUBLISHING / NEED_REVIEW`，允许失败任务保留并创建重试副本。
- 数据库时间统一为带 `+00:00` 的 UTC ISO 8601；业务时区固定记录为 `Asia/Shanghai`。

## 2026-07-11：发布平台、执行方式、时区与去重迁移

- `publish_jobs.platform` 只保存 `douyin` / `bilibili`；`manual_export`、`local_browser` 等值属于 `publish_mode`。
- 新增 `schedule_timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai'`；`scheduled_at` 统一保存 UTC ISO 8601，页面按该时区转换显示。
- 有效任务唯一索引 `uq_publish_jobs_active_clip_platform_mode` 约束同一 `output_clip_id + platform + publish_mode` 只能有一条未完成任务；`PUBLISHED`、`EXPORTED`、`CANCELLED` 历史不受该索引限制。
- 初始化发现旧值或重复任务时，先通过 SQLite backup API 写入 `data/backups/workflow-before-publish-migration-*.sqlite3`，再迁移数据。
- 旧 `platform=manual_export` 若 `provider_response.target_platform` 存在，会恢复真实目标平台，并按 `PUBLISH_DEFAULT_MODE` 设置执行方式；其他无效平台从任务平台恢复，最后才回退 `douyin`。
- 未发布重复任务保留 `updated_at/created_at` 最新的一条，其他写为 `CANCELLED`，错误码为 `migration_duplicate_cancelled`，并在 `provider_response` 保存迁移原因；已发布历史不会删除。

## 2026-06-25：全自动配置兼容与发送中心排期

- 不新增数据库列，也不执行破坏性迁移。
- `tasks.auto_config_json` 中旧的 `auto_clip_count`、`auto_min_clip_seconds`、`auto_max_clip_seconds` 和排期字段继续保留，供历史任务和旧接口读取；新任务的自动选片以 `candidate_clip_count` 和 `max_clip_duration` 为准。
- 全自动流水线新建的 `publish_jobs` 默认 `scheduled_at=''`、`status='WAITING'`；有风险标记时保持 `NEED_REVIEW`。
- 发送中心批量排期会写入每条 `publish_jobs.scheduled_at` 并改为 `SCHEDULED`；清除排期后普通任务回到 `WAITING`。

## 2026-06-23：v1.3.0 全自动流水线字段

- `tasks` 表新增 `auto_mode`：标记任务是否由全自动流水线接管。
- `tasks` 表新增 `auto_config_json`：保存自动切片数量、最小时长、最大时长、排期模式、间隔小时和是否使用 AI 生成发布文案等配置。
- `tasks` 表新增 `last_error`：保存最近一次失败原因；为兼容旧页面，也会同步写入 `error_message`。
- `publish_jobs` 表新增 `last_error`：给后续自动发布/重试调度预留最近失败原因字段。
- 全自动流水线会生成 `analysis/auto_selected_clips.json`、`analysis/auto_publish_metadata.json`、`analysis/auto_publish_schedule.json`、`analysis/task_summary.json` 和 `05_clips/clip_metadata.json`。
- v1.3.0 只创建发布任务，不真正定时发送；真正按 `scheduled_at` 执行发布计划留到 v1.4.0。

## 2026-05-27：任务目录改为项目名

- `tasks` 表新增 `task_dir_name` 字段，用来记录任务在存储盘里的实际文件夹名。
- `id` 仍是任务唯一 ID，用于数据库关联和网页地址；本地文件夹不再默认使用短 ID，而是使用 `task_dir_name`。
- 新建任务时会根据 `task_name` 生成安全的 Windows 文件夹名；重名时自动追加序号，避免覆盖旧目录。
- `DELETE /api/tasks/{task_id}` 会永久删除系统托管的任务目录和发布包，再把 `is_deleted` 设为 `1` 并写入 `deleted_at`；任务目录外的原片不会删除。
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
| `authorization_status` | TEXT | 兼容授权状态：`manual` / `authorized` |
| `auth_type` | TEXT | 授权方式，浏览器账号默认 `browser_profile` |
| `login_status` | TEXT | `normal` / `login_required` / `invalid` |
| `login_checked_at` | TEXT | 最近一次登录态检查时间（UTC） |
| `login_message` | TEXT | 登录态说明，不包含 Cookie 或账号密码 |
| `last_login_at` | TEXT | 最近一次确认登录成功时间（UTC） |
| `remark` | TEXT | 备注 |

### publish_jobs 表

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT | 发布任务 ID |
| `task_id` | TEXT | 所属视频任务 ID |
| `output_clip_id` | TEXT | 所属输出切片 ID |
| `account_id` | TEXT | 发布账号 ID |
| `platform` | TEXT | 目标平台，只允许 `douyin` / `bilibili` |
| `publish_mode` | TEXT | 执行方式：`opencli_publish` / `manual_export` / `api_publish` / `local_browser` |
| `video_source` | TEXT | `original` 或 `subtitled` |
| `video_file_path` | TEXT | 本次发布使用的视频路径 |
| `title` | TEXT | 标题 |
| `description` | TEXT | 简介 / 正文 |
| `tags` | TEXT | 标签 |
| `status` | TEXT | `DRAFT` / `WAITING` / `SCHEDULED` / `PUBLISHING` / `PUBLISHED` / `EXPORTED` / `FAILED` / `CANCELLED` / `NEED_REVIEW` |
| `audit_status` | TEXT | 平台审核状态 |
| `platform_item_id` | TEXT | 平台稿件 / 视频 ID |
| `platform_upload_id` | TEXT | 平台上传 ID |
| `error_code` | TEXT | 平台错误码 |
| `error_message` | TEXT | 错误说明 |
| `last_error` | TEXT | 最近一次失败说明，供后续自动重试使用 |
| `provider_response` | TEXT | 平台响应摘要 JSON |
| `retry_count` | INTEGER | 重试次数 |
| `attempt_count` | INTEGER | 实际领取执行次数 |
| `max_attempts` | INTEGER | 上传前安全重试上限，默认 3 |
| `scheduled_at` | TEXT | UTC ISO 8601 计划发布时间，例如 `2026-07-16T01:00:00+00:00` |
| `schedule_timezone` | TEXT | 排期计算和页面显示使用的 IANA 时区，例如 `Asia/Shanghai` |
| `timezone` | TEXT | 当前业务时区，默认 `Asia/Shanghai` |
| `next_attempt_at` | TEXT | Worker 未接收前连接失败的下一次安全重试时间 |
| `claimed_at` | TEXT | Scheduler 原子领取时间 |
| `started_at` | TEXT | 开始执行时间 |
| `finished_at` | TEXT | 完成、失败或进入人工复核时间 |
| `worker_id` | TEXT | 成功领取任务的 Scheduler 标识 |
| `execution_id` | TEXT | Windows Worker 执行日志 ID |
| `execution_phase` | TEXT | `claimed`、`upload_started`、`submit_clicked` 等阶段 |
| `retry_of_job_id` | TEXT | 手动重试来源任务 ID |
| `remote_video_id` | TEXT | 平台作品 / 稿件 ID |
| `platform_url` | TEXT | 平台作品 / 稿件链接 |
| `needs_manual_review` | INTEGER | 是否必须人工核对平台结果 |
| `published_at` | TEXT | 平台确认投稿成功时间；`EXPORTED` 不写此字段 |
| `history_hidden` | INTEGER | 是否从正常执行记录和月历安全隐藏，默认 `0` |
| `history_hidden_at` | TEXT | 安全隐藏时间；恢复后清空 |

### publish_job_events 表

| 字段 | 说明 |
| --- | --- |
| `job_id` | 对应发布任务 |
| `event_type` | 领取、排期、重试、结果或人工操作类型 |
| `from_status` / `to_status` | 本次状态流转 |
| `worker_id` | 执行该事件的 Scheduler |
| `error_code` / `message` | 错误或说明 |
| `payload` | 已脱敏的 JSON 摘要 |
| `occurred_at` | UTC ISO 8601 事件时间 |

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
| `task_dir_name` | TEXT | 存储盘实际任务文件夹名；永久删除后保留原值作为隐藏历史记录，但对应目录不再存在 |
| `source_type` | TEXT | 兼容字段；当前统一为 `upload`，新接口不再接受该参数 |
| `platform` | TEXT | 平台类型：`douyin`、`bilibili`、`general` |
| `original_video_path` | TEXT | 当前统一使用的本机上传视频路径 |
| `nas_file_path` | TEXT | 旧版兼容字段；迁移后清空，新接口不再读写 |
| `max_clip_duration` | INTEGER | 单条切片最长时长，单位：分钟；新建任务默认 10 分钟 |
| `candidate_clip_count` | INTEGER | 希望 AI 输出的候选片段数量；新建任务默认 12 条 |
| `ai_preference` | TEXT | AI 片段选择偏好 |
| `ai_prompt_preset_id` | TEXT | 当前使用的 AI Prompt 方案 ID |
| `auto_mode` | INTEGER | 是否开启全自动模式，`1` 表示开启 |
| `auto_config_json` | TEXT | 全自动模式配置 JSON |
| `status` | TEXT | 当前任务状态 |
| `progress` | INTEGER | 当前进度百分比，后续流水线推进时更新 |
| `error_message` | TEXT | 异常信息 |
| `last_error` | TEXT | 最近一次失败原因 |
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

### 全自动模式状态值

| 状态码 | 中文展示 |
| --- | --- |
| `CREATED` | 全自动任务已创建 |
| `PREPARING_SOURCE` | 准备视频中 |
| `TRANSCRIBING` | 转写文本中 |
| `AI_ANALYZING` | AI 分析中 |
| `CLIP_SELECTING` | 自动选片中 |
| `VIDEO_CUTTING` | 原片切割中 |
| `METADATA_GENERATING` | 生成标题文案中 |
| `SCHEDULE_CREATING` | 生成发布计划中 |
| `PUBLISH_JOB_CREATING` | 创建发布任务中 |
| `READY_TO_PUBLISH` | 待人工确认发布 |
| `COMPLETED` | 全自动流程完成 |
| `FAILED_PREPARING_SOURCE` | 准备视频失败 |
| `FAILED_TRANSCRIBING` | 转写失败 |
| `FAILED_AI_ANALYZING` | AI 分析失败 |
| `FAILED_CLIP_SELECTING` | 自动选片失败 |
| `FAILED_VIDEO_CUTTING` | 原片切割失败 |
| `FAILED_METADATA_GENERATING` | 标题文案生成失败 |
| `FAILED_SCHEDULE_CREATING` | 发布计划生成失败 |
| `FAILED_PUBLISH_JOB_CREATING` | 发布任务创建失败 |

全自动模式失败后可以调用 `POST /api/tasks/{task_id}/process/auto-retry` 从失败步骤继续。

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
新任务默认值调整不会批量更新已有 `tasks` 记录；历史任务已经保存的最大时长和候选数量保持不变。
账本迁移 `20260824_02_task_upload_only` 会在写入前创建 SQLite Online Backup：旧 `nas_file_path` 在 `original_video_path` 为空时归一到该字段，随后统一写为 `source_type='upload'` 并清空 `nas_file_path`。物理列继续保留，不删除或移动任何外部视频。

任务删除采用“媒体永久删除、数据库历史隐藏保留”的方式：`DELETE /api/tasks/{task_id}` 只允许删除 `TASKS_DIR` 下与任务精确对应的目录、该任务的手动发布包和可确认归属的旧版项目 `tasks` 目录。删除成功后把 `is_deleted` 改为 `1` 并写入 `deleted_at`，候选片段、切片、字幕和发布历史仍留在 SQLite 中用于审计。任务目录外的原片永远不参与删除；运行中的转写、切片或真实发送任务返回 409，避免后台进程重新生成文件。

`clip_candidates.reason` 是早期推荐理由字段，当前审核页优先读取 `highlight_reason`。数据库初始化时会把已有 `reason` 自动补到 `highlight_reason`。

候选片段删除是软删除：`DELETE /api/tasks/{task_id}/clips/{clip_id}` 只更新数据库记录，不删除源视频、转写文件、AI 分析文件或已生成切片文件。

## 任务产物路径

任务产物路径当前由 `task_dir_name` 决定；`task_id` 只作为内部唯一 ID 使用，不再直接决定存储文件夹名。

历史任务可能仍兼容 `task_id` 目录，但新逻辑以 `task_dir_name` 为准。

浏览器上传超过内存阈值后的临时文件使用 `UPLOAD_TEMP_DIR`，默认位于 `{TASKS_DIR}\_临时上传`；显式手动导出的发布包使用 `PUBLISH_SCHEDULER_EXPORT_DIR`，默认位于 `{STORAGE_ROOT}\_发布包`。应用启动时会验证这些目录可写，不可用时直接报错，不会回退到 C 盘。

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

- 以上 v1.2 说明仅是历史记录。v2.1.0 已由 `PublishScheduler` 执行到期任务，并通过 Windows Worker 调用抖音/B站 Publisher。
- 平台发送不绕过验证码、登录失效、风控和人工确认；结果不确定写 `NEED_REVIEW`。
- 代码中仍存在兼容性 `clips` 子目录（`TASK_SUBDIRECTORIES` 同时包含 `clips` 和 `05_clips`），新任务的正式输出目录是 `05_clips`。旧 `clips` 目录为兼容保留，不建议删除。
# 2026-06-23：v1.4.0 定时发送字段

- `publish_jobs` 已补齐定时发送字段：`clip_id`、`caption`、`hashtags`、`cover_text`、`video_path`、`risk_flags`、`publish_result`、`remote_video_id`、`attempt_count`、`published_at`。
- 旧字段继续兼容：`output_clip_id` 等同于 `clip_id`，`description` 等同于 `caption`，`tags` 等同于 `hashtags`，`video_file_path` 等同于 `video_path`，`provider_response` 兼容 `publish_result`，`retry_count` 兼容 `attempt_count`。
- 发布状态使用：`DRAFT`、`SCHEDULED`、`WAITING`、`PUBLISHING`、`PUBLISHED`、`FAILED`、`CANCELLED`、`NEED_REVIEW`。
- 调度器只扫描 `status = SCHEDULED` 且 `scheduled_at <= 当前时间` 的任务；`NEED_REVIEW`、`CANCELLED`、`PUBLISHED` 不会自动发布。
- 该 2026-06-23 版本曾默认使用 `manual_export`，2026-07-11 曾改为 `opencli_publish`；v2.1.0 当前默认是 `local_browser`。发布包导出成功写 `EXPORTED` 且不写 `published_at`；只有平台确认提交成功才写 `PUBLISHED` 和 `published_at`。
- 没有 `scheduled_at` 的旧手动发送任务迁移为 `WAITING`，避免被自动调度器误执行。

## 2026-07-27：取消发送状态兼容

- 本次没有新增或删除数据库字段。普通“取消发送”把发布任务从 `DRAFT`、`WAITING` 或 `SCHEDULED` 恢复为 `WAITING`，并清空排期与执行占用字段，视频、文案和封面路径保持不变。
- 旧版本中 `CANCELLED` 且错误信息为“用户取消任务”的最后一条记录，会在数据库初始化时安全恢复为 `WAITING`；同一切片和平台已有活跃任务时不恢复，避免重复。
- 用户主动“移出内容准备”的 `user_removed_from_preparation` 记录，以及跳过、发布失败、发布完成和系统取消记录仍保持原状态，不参与兼容恢复。

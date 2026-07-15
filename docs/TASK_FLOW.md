# 任务状态流转

## 1. 主任务状态列表（tasks.status）

| 状态码 | 中文展示 | 说明 |
| --- | --- | --- |
| `pending_video` | 待提交视频 | 任务已创建，尚未上传视频 |
| `pending_processing` | 待处理 | 视频已上传，等待开始处理 |
| `audio_extracting` | 音频提取中 | FFmpeg 正在提取音频 |
| `transcribing` | 转写中 | faster-whisper 或火山引擎正在转写 |
| `pending_ai` | 待 AI 分析 | 转写完成，等待 AI 分析 |
| `ai_analyzing` | AI 分析中 | DeepSeek 或本地 Ollama 正在分析 |
| `pending_review` | AI 结果待检查 | AI 候选片段已生成，可在片段审核页检查 |
| `cutting` | 切割中 | FFmpeg 正在逐条切割视频 |
| `completed` | 已完成 | 所有启用片段切割成功，自动切割阶段结束 |
| `completed_with_errors` | 部分完成 | 至少一个片段切割成功，但也有片段失败 |
| `failed` | 失败 | 全部失败或前置阶段出现不可恢复错误 |

## 2. 视频处理主流程

```text
pending_video          ← 任务已创建，尚未上传视频
→ pending_processing   ← 视频已提交，等待处理
→ audio_extracting     ← FFmpeg 提取音频
→ transcribing         ← 语音转写（faster-whisper 本地 / 火山引擎远程）
→ pending_ai           ← 转写完成，等待 AI 分析
→ ai_analyzing         ← AI 正在分析（DeepSeek 远程 / Ollama 本地）
→ pending_review       ← AI 候选片段已生成
→ cutting              ← FFmpeg 逐条切割
→ completed             ← 所有启用片段切割成功
→ completed_with_errors ← 至少一个成功，部分失败
→ failed                ← 全部失败或不可恢复错误
```

**重要说明：**
- `completed` / `completed_with_errors` 代表"自动切割阶段结束"，不是平台发布完成。
- 字幕和发布是独立于主任务状态的后续工作流。

## 3. v1.3.0 全自动任务状态流

`auto_mode=true` 的任务使用独立的大写状态，不破坏原有手动流程：

```text
CREATED
→ PREPARING_SOURCE
→ TRANSCRIBING
→ AI_ANALYZING
→ CLIP_SELECTING
→ VIDEO_CUTTING
→ METADATA_GENERATING
→ SCHEDULE_CREATING
→ PUBLISH_JOB_CREATING
→ READY_TO_PUBLISH
→ COMPLETED
```

对应失败状态：

```text
FAILED_PREPARING_SOURCE
FAILED_TRANSCRIBING
FAILED_AI_ANALYZING
FAILED_CLIP_SELECTING
FAILED_VIDEO_CUTTING
FAILED_METADATA_GENERATING
FAILED_SCHEDULE_CREATING
FAILED_PUBLISH_JOB_CREATING
```

全自动流程规则：

- 每一步开始前写入对应状态。
- 任意步骤失败时写入对应 `FAILED_*`，并把错误写入 `tasks.last_error` / `tasks.error_message`。
- 失败后可调用 `POST /api/tasks/{task_id}/process/auto-retry` 从失败步骤继续。
- 已有 AI 分析历史但候选片段缺失时，可调用 `POST /api/tasks/{task_id}/process/auto-resume`，恢复最近一次 AI 结果并从自动选片继续。
- 已有 `transcripts/transcript.md` 或文本/字幕文件时优先复用；没有文本时才调用转写。
- 转写文本只作为 AI 分析输入；全自动模式不执行加字幕、字幕样式渲染、字幕叠加或字幕烧录。
- 自动选片数量读取 `tasks.candidate_clip_count`，时长上限读取 `tasks.max_clip_duration`；旧自动数量和最小/最大秒数只保留兼容，不再参与新任务决策。
- 切片输出仍写入 `05_clips/`，并写入 `output_clip`；单个切片失败不会阻断其他成功切片生成文案和发布任务。
- `SCHEDULE_CREATING` 当前表示整理发送队列，不再自动计算发布时间。
- 发布任务先以 `WAITING` / `NEED_REVIEW` 创建；用户在发送中心批量设置时间后进入 `SCHEDULED`，再由 v1.4.0 调度器执行。

## 4. 失败流转

任意处理阶段出现不可恢复错误时，任务进入：

```text
failed
```

失败状态需要记录：

- 出错阶段。
- 错误信息。
- 相关文件路径。
- 是否可以重试。

## 5. 首版进度占比

| 状态 | 进度 |
| --- | --- |
| pending_video | 0% |
| pending_processing | 5% |
| audio_extracting | 20% |
| transcribing | 40% |
| pending_ai | 55% |
| ai_analyzing | 65% |
| pending_review | 72% |
| cutting | 88% |
| completed / completed_with_errors | 100% |

## 6. AI 分析阶段

AI 分析阶段当前已接入真实接口入口：

```text
pending_ai
→ 用户点击"远程 AI 分析"或"本地 AI 分析"
→ ai_analyzing
→ 解析 AI 严格 JSON（兼容 Markdown 代码块、尾随逗号、Python 风格布尔值等）
→ Pydantic 字段校验 + 时间范围校验 + 片段时长校验
→ 写入 clip_candidates
→ pending_review
```

- 远程 DeepSeek 使用完整逐句时间戳原文上下文，整集一次提交；如果旧任务文件仍包含分钟级转写，分析时会自动忽略分钟级重复内容。
- 本地 Ollama 按约 3 分钟小段拆分，每段生成局部候选片段，再合并、去重、按置信度筛选。
- AI 返回非法 JSON 时，程序会自动安全重试一次。重试后仍失败时，任务进入 `failed`。
- 远程 AI 失败时不会自动降级到本地 AI，会暂停并显示原因，用户需手动点击"本地 AI 分析"。

## 7. 转写阶段

转写阶段默认使用火山引擎远程转写：

```text
用户点击"开始处理 / 继续处理"
→ 如果没有 audio/source.wav，先自动提取音频
→ 如果已有 transcripts/transcript.md，直接提示转写已完成，不重复转写
audio/source.wav
→ FFprobe 读取音频时长
→ 火山引擎远程转写（默认）或本地 faster-whisper
→ 按分钟生成逐句时间戳原文
→ 写入 transcripts/transcript.md（只保留"逐句时间戳原文"）
→ pending_ai
```

- 远程转写失败时不会自动改用本地模型，页面会显示"改用本地模型转写"按钮。
- 本地 faster-whisper 默认按 2 分钟切分音频，段与段之间重叠 5 秒。
- 转写完成后进入 `pending_ai`。

## 8. 自动切割阶段

- 用户在片段审核页点击"生成切片"后，任务进入 `cutting`。
- 所有启用片段都切割成功时，任务进入 `completed`。
- 至少一个片段成功、同时存在失败片段时，任务进入 `completed_with_errors`。
- 所有片段都失败时，任务进入 `failed`。
- 切割输出到 `05_clips/` 目录，结果逐条写入 `output_clip` 表。

## 9. 字幕工作流（独立于主任务状态）

字幕是 output_clip 生成之后的独立 `subtitle_jobs` 流程，不直接混入 `tasks.status`：

```text
output_clip 生成成功
→ /subtitles 字幕工作台
→ 选择切片，点击"自动加字幕"
→ 从转写文本按切片时间范围提取字幕行
→ 生成 .ass 字幕文件
→ FFmpeg subtitles 滤镜合成带字幕视频
→ 输出到 06_subtitled/
→ subtitle_jobs.status = completed / failed
```

字幕任务状态：`pending` → `processing` → `completed` / `failed`

## 10. 发送中心工作流（独立于主任务状态）

发送中心是切片生成后的独立 `publish_jobs` 流程，不直接混入 `tasks.status`：

```text
output_clip 生成成功
→ 全自动流程按 metadata.platform 创建 publish_jobs
→ DRAFT / WAITING
→ 用户在“内容准备”复核内容、平台和账号
→ 排期抽屉先调用 POST /api/publish/schedules/preview
→ 确认后将精确时间列表提交 PATCH /api/publish/jobs/schedule-batch
→ SCHEDULED（scheduled_at 保存 UTC +00:00，timezone 保存 Asia/Shanghai）
→ 调度器到点使用 BEGIN IMMEDIATE 原子领取为 PUBLISHING
→ Registry 按 platform + publish_mode 分发
   ├─ local_browser → Windows Worker → DouyinPublisher / BilibiliPublisher
   │    ├─ 平台确认成功 → PUBLISHED
   │    ├─ 明确失败 → FAILED
   │    └─ 登录/验证/风控/结果不确定 → NEED_REVIEW
   ├─ manual_export → 本地发布包 → EXPORTED / FAILED
   └─ opencli_publish → 显式兼容开关 → PUBLISHED / FAILED / NEED_REVIEW
```

`platform` 只能是 `douyin` / `bilibili`；`publish_mode` 只能表示执行方式，禁止互相混用。发送中心的“补充缺失任务”只补缺，不覆盖已有任务的执行方式。

### 发送任务状态（publish_jobs.status）

| 状态 | 说明 |
| --- | --- |
| `DRAFT` | 草稿，尚未进入排期或执行 |
| `WAITING` | 内容已生成，等待排期或立即发送 |
| `SCHEDULED` | 已保存 UTC 计划时间，等待调度器扫描 |
| `NEED_REVIEW` | 登录、验证码、风控或平台结果不确定，需要人工核对 |
| `PUBLISHING` | 已被一个调度器原子领取，正在执行 |
| `PUBLISHED` | 平台 Publisher 已取得作品 ID、稿件 ID、作品链接或明确成功证据 |
| `EXPORTED` | 本地发布包已导出，不代表平台已发布 |
| `FAILED` | 明确失败；手动重试会创建带 `retry_of_job_id` 的新任务并保留旧记录 |
| `CANCELLED` | 用户取消，或迁移时取消了较旧的未发布重复任务 |

### 排期与立即发送

- 浏览器提交北京时间 `start_at_local`；后端按 `Asia/Shanghai` 应用每日开始/结束窗口，跨日后顺延到次日开始时间。
- `scheduled_at` 统一存 UTC ISO 8601，API 同时返回 `scheduled_at_utc` 与 `scheduled_at_local`。
- 自动调度只读取到期的 `SCHEDULED`；`NEED_REVIEW` 即使有时间也不能执行。
- “立即发送”允许 `DRAFT`、`WAITING`、`SCHEDULED`，只把 `scheduled_at` 更新为当前 UTC 并唤醒 Scheduler；不直接调用 opencli 或平台页面。
- 领取任务使用 `BEGIN IMMEDIATE` 与条件更新；只有 `SCHEDULED → PUBLISHING` 更新成功的 Worker 能执行。
- Worker 未接收任务前的连接故障最多安全重试 3 次；上传开始、点击提交或执行阶段未知时禁止自动重试。
- 启动恢复会查询 Worker 执行日志；确认未上传才重新排队，旧版未知 `PUBLISHING` 直接进入 `NEED_REVIEW`。

### 安全边界

- 默认 `local_browser` 依赖 Windows Worker 和专属 Chrome 登录目录；健康状态见 `GET /api/publish/scheduler/health`。
- 不绕过验证码、登录失效、风控和人工确认。
- 遇到平台验证提示或发布结果不确定时进入 `NEED_REVIEW`，不会标记为已发布，也不会自动重传。
- 人工标记 `PUBLISHED` 必须从 `NEED_REVIEW` 操作并填写对应平台作品链接。

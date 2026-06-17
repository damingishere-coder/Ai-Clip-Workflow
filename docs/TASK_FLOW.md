# 任务流程与状态流转

本文档按当前 v1.3.0 代码描述真实流程。主任务负责从视频到切片；字幕和发送中心是切片后的独立工作流。

## 1. 主任务状态

| 状态码 | 中文含义 | 说明 |
|---|---|---|
| `pending_video` | 待提交视频 | 任务已创建，尚未上传或绑定视频 |
| `pending_processing` | 待处理 | 视频已准备好，等待提取音频和转写 |
| `audio_extracting` | 音频提取中 | FFmpeg 正在生成 `audio/source.wav` |
| `transcribing` | 转写中 | 火山引擎或 faster-whisper 正在转写 |
| `pending_ai` | 待 AI 分析 | 转写完成，等待 AI 生成候选片段 |
| `ai_analyzing` | AI 分析中 | 远程或本地模型正在分析文字稿 |
| `pending_review` | 待审核片段 | 候选片段已写入，可人工审核 |
| `cutting` | 切片中 | FFmpeg 正在生成短视频 |
| `completed` | 已完成 | 启用片段全部切片成功 |
| `completed_with_errors` | 部分完成 | 至少一条切片成功，同时有失败片段 |
| `failed` | 失败 | 前置阶段失败或全部切片失败 |

主流程：

```text
pending_video
→ pending_processing
→ audio_extracting
→ transcribing
→ pending_ai
→ ai_analyzing
→ pending_review
→ cutting
→ completed / completed_with_errors / failed
```

`completed` 只代表自动切片阶段结束，不代表已经发布到平台。

## 2. 创建任务

入口：

- 上传视频：`POST /api/tasks/upload`
- 选择已有视频：通过任务创建接口保存 `nas_file_path`

主要行为：

- 创建 `tasks` 记录。
- 分配 `task_dir_name`。
- 上传文件写入 `source/`。
- 已有视频路径必须在允许的媒体根目录内。
- Windows 文件名会做非法字符清理，避免路径穿越。

## 3. 音频提取与转写

入口：

- 页面点击“开始处理 / 继续处理”。
- 后端会先检查是否已有 `audio/source.wav`。

产物：

```text
audio/source.wav
transcripts/transcript.md
transcripts/transcript_progress.json
```

当前 provider：

- `TRANSCRIPTION_PROVIDER=volcengine`：火山引擎远程转写。
- `TRANSCRIPTION_PROVIDER=local`：本地 faster-whisper。

注意：

- 远程转写失败后，当前不会自动切到本地模型；页面会提示用户手动改用本地转写。
- 本地 faster-whisper 默认按 120 秒切块，重叠 5 秒。
- `transcript.md` 是后续 AI 分析、字幕提取和片段审核复用的核心文件。

## 4. AI 分析

入口：

- 页面点击远程 AI 分析或本地 AI 分析。

产物：

```text
analysis/candidate_clips.json
clip_candidates 表
ai_analysis_runs 表
```

当前 provider：

- `remote`：OpenAI-compatible / DeepSeek。
- `local`：本地 Ollama。

真实行为：

- 读取 `transcripts/transcript.md`。
- 按长文本分块分析，再合并、去重、排序候选片段。
- 写入 `analysis/candidate_clips.json`。
- 替换当前任务的 `clip_candidates`。
- 保存一条 `ai_analysis_runs` 历史记录。
- 任务进入 `pending_review`。

恢复历史 AI 分析时，会把历史 payload 重新写回 `candidate_clips.json`，并替换当前候选片段。

## 5. 人工审核候选片段

入口：

```text
/tasks/{task_id}/clips/review
```

可做操作：

- 修改标题。
- 修改开始时间和结束时间。
- 修改摘要。
- 启用或停用候选片段。
- 软删除候选片段。

只有启用且未删除的候选片段会进入切片流程。

## 6. 自动切片

入口：

- 同步切片接口。
- 异步切片接口，使用 `workflow_jobs` 本地轻量队列。

产物：

```text
05_clips/*.mp4
output_clip 表
cut_runs 表
```

行为：

- 每次切片创建一条 `cut_runs`。
- 切片成功后激活新 run，并让旧 run 的输出变为非活跃。
- 如果新切片全部失败，旧的活跃切片结果会保留。
- 至少一条成功时，任务进入 `completed` 或 `completed_with_errors`。

## 7. 字幕工作流

字幕不直接改变 `tasks.status`，状态保存在 `subtitle_jobs`。

产物：

```text
06_subtitled/*.ass
06_subtitled/*_subtitled.mp4
subtitle_jobs 表
```

流程：

```text
选择已完成 output_clip
→ 从 transcript.md 按时间范围提取字幕文本
→ 生成 ASS 字幕
→ FFmpeg subtitles 滤镜烧录
→ 激活新的 subtitle_job
```

失败时旧的活跃字幕结果会保留。

## 8. 发送中心工作流

发送中心不直接改变 `tasks.status`，状态保存在 `publish_jobs`。

产物：

```text
publish_jobs 表
07_covers/*.jpg
```

流程：

```text
刷新发送队列
→ 从 completed output_clip 生成抖音 / B站任务
→ 生成标题、简介、话题
→ 生成候选封面帧
→ 用户人工确认
→ opencli 辅助浏览器投稿
→ published / failed / cancelled
```

发送任务状态：

| 状态 | 说明 |
|---|---|
| `ready` | 待发送 |
| `publishing` | opencli 正在辅助浏览器操作 |
| `published` | 已标记发布成功 |
| `failed` | 发送失败 |
| `cancelled` | 用户取消 |

## 9. scheduled_at 字段

`publish_jobs.scheduled_at` 当前只是字段预留：

- 可以保存计划发布时间。
- 没有后台定时调度器。
- 不会自动按时间发布。
- 仍需要用户在发送中心手动触发。

## 10. 平台安全边界

opencli 只用于辅助操作已登录 Chrome：

- 不绕过验证码。
- 不绕过登录失效。
- 不绕过平台风控。
- 不替用户做无法确认的最终发布动作。
- 失败时写入 `publish_jobs.error_message`，等待人工处理。

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

## 3. 失败流转

任意处理阶段出现不可恢复错误时，任务进入：

```text
failed
```

失败状态需要记录：

- 出错阶段。
- 错误信息。
- 相关文件路径。
- 是否可以重试。

## 4. 首版进度占比

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

## 5. AI 分析阶段

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

## 6. 转写阶段

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

## 7. 自动切割阶段

- 用户在片段审核页点击"生成切片"后，任务进入 `cutting`。
- 所有启用片段都切割成功时，任务进入 `completed`。
- 至少一个片段成功、同时存在失败片段时，任务进入 `completed_with_errors`。
- 所有片段都失败时，任务进入 `failed`。
- 切割输出到 `05_clips/` 目录，结果逐条写入 `output_clip` 表。

## 8. 字幕工作流（独立于主任务状态）

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

## 9. 发送中心工作流（独立于主任务状态）

发送中心是切片生成后的独立 `publish_jobs` 流程，不直接混入 `tasks.status`：

```text
output_clip 生成成功 + 字幕完成
→ /publish 发送中心
→ 刷新发送队列（从已完成切片生成抖音 + B站双平台 opencli 任务）
→ publish_jobs.status = ready
→ 用户确认标题、话题、简介、封面帧
→ 点击"发送此条"
→ opencli 辅助浏览器打开平台投稿页
→ 自动填写标题、简介、上传视频、选择封面
→ 点击发布，等待平台成功信号
→ publish_jobs.status = publishing → published / failed
```

### 发送任务状态（publish_jobs.status）

| 状态 | 说明 |
| --- | --- |
| `ready` | 待发送，已整理好标题、封面和视频 |
| `publishing` | 发送中，opencli 正在操作浏览器 |
| `published` | 已发布，平台返回成功信号 |
| `failed` | 发送失败，error_message 记录具体原因 |
| `cancelled` | 已取消，用户主动取消 |

### scheduled_at 字段说明

- `publish_jobs.scheduled_at` 当前只是字段预留，可以保存计划发布时间。
- v1.2 还没有后台定时调度器，不会自动按 `scheduled_at` 发送。
- 所有发送都需要用户手动在发送中心点击"发送此条"或"开始发送全部"触发。

### 安全边界

- 平台发送依赖 opencli 辅助浏览器操作。
- 不绕过验证码、登录失效、风控和人工确认。
- 遇到平台验证提示时，任务会标记为 `failed` 并记录具体原因，等待人工处理。

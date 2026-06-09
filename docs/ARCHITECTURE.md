# 系统架构

## 1. 总体架构

本项目采用 Windows 本地后台 + Docker 容器化架构：

```text
浏览器后台页面 (http://127.0.0.1:8001)
→ FastAPI 本地服务
→ SQLite 任务数据库
→ 存储根目录（由 STORAGE_ROOT / TASKS_DIR 配置，默认 E:\直播间切片工作流存储）
→ {task_dir_name}/ 任务产物目录
→ FFmpeg / FFprobe
→ 本地 faster-whisper 或火山引擎远程转写
→ AI 分析服务接口（DeepSeek / 本地 Ollama）
→ opencli 辅助浏览器投稿（抖音 / B站）
→ 输出短视频文件
```

## 2. Windows 后台端

Windows 本地端是首版核心，负责：

- 提供后台页面（FastAPI + Jinja2 模板）。
- 管理任务创建、状态流转、进度和异常信息。
- 调用 FFmpeg / FFprobe 进行音视频处理。
- 管理存储盘任务目录。
- 调用本地 faster-whisper 或火山引擎远程转写。
- 调用远程 DeepSeek 或本地 Ollama 进行 AI 候选片段分析。
- 输出最终切片文件（05_clips）、带字幕成片（06_subtitled）和封面（07_covers）。

## 3. 存储与任务目录

### 3.1 存储根目录

存储根目录由 `STORAGE_ROOT` 或 `TASKS_DIR` 环境变量配置，不是只写死 E 盘。默认值为 `E:\直播间切片工作流存储`。

### 3.2 任务目录命名

- 新任务目录以 `task_dir_name` 为准，不再使用 `task_id` 作为文件夹名。
- `task_id` 只作为数据库关联、URL 和内部唯一 ID。
- `task_dir_name` 在新建任务时根据 `task_name` 自动生成安全的 Windows 文件夹名；重名时自动追加序号。
- 历史任务可能仍兼容 `task_id` 目录，但新逻辑以 `task_dir_name` 为准。

### 3.3 正式任务目录结构

```text
{task_dir_name}/
  source/          ← 上传源视频
  audio/           ← 提取的音频（source.wav）
  transcripts/     ← 转写结果（transcript.md）
  analysis/        ← AI 分析结果（candidate_clips.json）
  05_clips/        ← 正式切片输出目录
  06_subtitled/    ← 带字幕成片输出目录
  07_covers/       ← 发送中心候选封面目录
  logs/            ← 处理日志（process.log）
```

### 3.4 目录说明

- `05_clips` 是正式切片输出目录。代码中 `TASK_SUBDIRECTORIES` 同时包含 `clips` 和 `05_clips` 用于兼容历史任务；新任务正式输出到 `05_clips`。
- `06_subtitled` 是带字幕成片目录，由字幕工作流生成。
- `07_covers` 是发送中心候选封面目录，由封面帧截取功能生成。

## 4. NAS 存储

NAS 或本地目录在首版中作为视频来源与文件归档位置。系统需要支持：

- 选择 NAS / 本地目录中的已有视频。
- NAS / 本地已有视频首版只记录原路径，不复制大视频。
- 把任务处理过程中的中间文件保存到存储根目录下的任务目录。
- 后续可扩展为把最终切片回写到 NAS。

## 5. 未来 MacBook 录屏端

首版不实现 MacBook 自动录屏，但架构上预留：

- 录屏任务上报接口。
- 直播间状态检测接口。
- 远程推送视频文件到 Windows 后台或 NAS 的接口。

未来关系可以是：

```text
MacBook 录屏端
→ NAS / 文件同步目录
→ Windows 后台创建处理任务
```

## 6. 数据流转关系

```text
source_video
→ {task_dir_name}/source/
→ {task_dir_name}/audio/
→ {task_dir_name}/transcripts/
→ {task_dir_name}/analysis/
→ 人工审核（片段审核页）
→ {task_dir_name}/05_clips/        ← 正式切片输出
→ {task_dir_name}/06_subtitled/    ← 带字幕成片（字幕工作流，独立于主任务状态）
→ {task_dir_name}/07_covers/       ← 发送中心封面（发布工作流，独立于主任务状态）
```

SQLite 保存任务元数据、状态、候选片段与异常信息。大型视频素材和自动生成文件不直接提交到 Git。

当前约定：

- 数据库保存在项目目录 `data/workflow.sqlite3`。
- 任务产物根目录由 `STORAGE_ROOT` / `TASKS_DIR` 配置。
- 上传视频会保存到任务目录 `source/`。
- NAS / 本地已有视频只保存路径，避免重复占用硬盘。
- FFmpeg 音频提取输出到 `audio/source.wav`。
- 火山引擎远程转写或本地 faster-whisper 输出到 `transcripts/transcript.md`，文件只保留"逐句时间戳原文"作为权威原文。转写阶段只做听写和文本整理，不做 AI 总结。

当前任务数据表说明见：

```text
docs/DATABASE_SCHEMA.md
```

## 7. 服务接口预留

- `transcript_service.py`：本地 faster-whisper 转写、火山引擎远程转写、转写预览解析和进度管理。
- `app/services/ai/`：AI 候选片段分析模块，包含 Provider 抽象、远程 DeepSeek Provider、本地 Ollama Provider、AI JSON 解析和片段分析编排。
- `video_cut_service.py`：FFmpeg 自动切割接口。
- `storage_service.py`：任务目录与文件路径管理接口（含 `task_dir_name` 分配、路径解析、视频文件校验）。
- `task_service.py`：任务状态与业务编排接口（含字幕渲染、字幕样式、发布队列集成）。
- `publish_service.py`：发送中心服务（opencli 队列管理、封面帧生成、AI 文案生成、内容安全清洗、平台发送脚本编排）。
- `ai_config_service.py`：三类 AI 接口配置读写（音频转写 / 候选切片分析 / 发布文案生成）。

## 8. 当前已实现

当前已实现任务管理基础闭环：

```text
新建任务表单
→ /api/tasks
→ SQLite tasks 表
→ 任务列表页读取真实任务
→ 任务详情页按 task_id 查询真实任务
```

当前 AI 片段分析链路已支持：

```text
任务详情页
→ 远程 AI 分析 / 本地 AI 分析
→ /api/tasks/{task_id}/process/ai?provider=remote|local
→ prompts/clip_analysis_prompt.txt
→ 严格 JSON 解析与 Pydantic 校验
→ clip_candidates 表
→ 待人工审核（片段审核页）
```

当前自动切割链路已支持：

```text
片段审核页
→ "生成切片"
→ /api/tasks/{task_id}/process/cuts
→ FFmpeg 逐条切割
→ output_clip 表 + 05_clips/ 目录
→ completed / completed_with_errors / failed
```

当前字幕工作流（独立于主任务状态）：

```text
output_clip 生成成功
→ /subtitles 字幕工作台
→ 自动加字幕（ASS + FFmpeg subtitles 滤镜）
→ 06_subtitled/ 目录
→ subtitle_jobs 表
```

当前发送中心（独立于主任务状态）：

```text
字幕完成后
→ /publish 发送中心
→ 刷新发送队列（从已完成切片生成双平台 opencli 任务）
→ 内容安全清洗 + AI 文案生成
→ 候选封面帧生成（07_covers/）
→ opencli 辅助浏览器投稿（抖音 + B站）
→ publish_jobs 表（ready / publishing / published / failed / cancelled）
```

## 9. 发送中心说明

- 发送中心当前已有发送队列和 opencli 辅助投稿能力，但还没有真正的定时调度器。
- `publish_jobs.scheduled_at` 当前只是字段预留，可以保存计划发布时间。
- v1.2 还没有后台定时调度器，不会自动按 `scheduled_at` 发送。
- 平台发送依赖 opencli 辅助浏览器操作，不绕过验证码、登录失效、风控和人工确认。
- Docker 环境通过 `opencli_host_bridge.py` 辅助服务桥接 Windows 主机上的 opencli。

## 10. UI 设计参考

后续前端页面实现必须优先参考：

```text
docs/design/live_streaming_slicing_workflow_ui_16x9.png
```

视觉方向：Apple 风格、简洁、高级、留白充足、轻量玻璃拟态、卡片式布局、蓝色作为主强调色，适合作为个人本地 AI 高光生产后台。

## 11. 自动切割输出

当前自动切割由 `app/services/video_cut_service.py` 统一封装，路由层只负责触发业务流程，不直接拼接 FFmpeg 命令。

输出目录约定为：

```text
{task_dir_name}/05_clips/
```

每条切片结果写入 `output_clip` 表。成功和失败逐条记录，失败时保存 stderr 摘要或时间校验错误，方便在页面和日志中排查。

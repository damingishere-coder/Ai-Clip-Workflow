# 系统架构

## 1. 总体架构

本项目首版采用 Windows 本地后台架构：

```text
浏览器后台页面
→ FastAPI 本地服务
→ SQLite 任务数据库
→ E:\直播间切片工作流存储\{task_id}\ 独立任务目录
→ FFmpeg / FFprobe
→ 本地 faster-whisper 转写
→ AI 分析服务接口
→ 输出短视频文件
```

## 2. Windows 后台端

Windows 本地端是首版核心，负责：

- 提供后台页面。
- 管理任务创建、状态流转、进度和异常信息。
- 调用 FFmpeg / FFprobe。
- 管理 E 盘任务目录。
- 调用本地转写服务与 AI 分析服务。
- 输出最终切片文件。

## 3. NAS 存储

NAS 或本地目录在首版中作为视频来源与文件归档位置。系统需要支持：

- 选择 NAS / 本地目录中的已有视频。
- NAS / 本地已有视频首版只记录原路径，不复制大视频。
- 把任务处理过程中的中间文件保存到 E 盘任务目录。
- 后续可扩展为把最终切片回写到 NAS。

## 4. 未来 MacBook 录屏端

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

## 5. 数据流转关系

```text
source_video
→ E:\直播间切片工作流存储\{task_id}\source\
→ E:\直播间切片工作流存储\{task_id}\audio\
→ E:\直播间切片工作流存储\{task_id}\transcripts\
→ E:\直播间切片工作流存储\{task_id}\analysis\
→ 人工审核
→ E:\直播间切片工作流存储\{task_id}\clips\
```

SQLite 保存任务元数据、状态、候选片段与异常信息。大型视频素材和自动生成文件不直接提交到 Git。

当前约定：

- 数据库仍保存在项目目录 `data/workflow.sqlite3`。
- 任务产物根目录固定为 `E:\直播间切片工作流存储`。
- 上传视频会保存到任务目录 `source/`。
- NAS / 本地已有视频只保存路径，避免重复占用硬盘。
- FFmpeg 音频提取输出到 `audio/source.wav`。
- 本地 faster-whisper 转写输出到 `transcripts/transcript.md`，文件包含“分钟级转写”和“逐句时间戳原文”。转写阶段只做听写和文本整理，不做 AI 总结。

当前任务数据表说明见：

```text
docs/DATABASE_SCHEMA.md
```

## 6. 服务接口预留

- `transcript_service.py`：本地 faster-whisper 转写、分钟级原文整理和转写预览解析。
- `app/services/ai/`：AI 候选片段分析模块，包含 Provider 抽象、远程中转站 Provider、本地大模型 Provider 和片段分析编排。
- `ai_clip_service.py`：早期 AI 候选片段占位接口，后续以 `app/services/ai/` 为主。
- `video_cut_service.py`：FFmpeg 自动切割接口。
- `storage_service.py`：任务目录与文件路径管理接口。
- `task_service.py`：任务状态与业务编排接口。

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
→ 待人工审核
```

## 7. UI 设计参考

后续前端页面实现必须优先参考：

```text
docs/design/live_streaming_slicing_workflow_ui_16x9.png
```

视觉方向：Apple 风格、简洁、高级、留白充足、轻量玻璃拟态、卡片式布局、蓝色作为主强调色，适合作为个人本地 AI 高光生产后台。

## 8. 自动切割输出

当前自动切割由 `app/services/video_cut_service.py` 统一封装，路由层只负责触发业务流程，不直接拼接 FFmpeg 命令。

输出目录约定为：

```text
E:\直播间切片工作流存储\{task_id}\05_clips\
```

每条切片结果写入 `output_clip` 表。成功和失败逐条记录，失败时保存 stderr 摘要或时间校验错误，方便在页面和日志中排查。

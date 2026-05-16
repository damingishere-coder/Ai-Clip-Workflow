# 系统架构

## 1. 总体架构

本项目首版采用 Windows 本地后台架构：

```text
浏览器后台页面
→ FastAPI 本地服务
→ SQLite 任务数据库
→ tasks/ 独立任务目录
→ FFmpeg / FFprobe
→ 转写服务接口
→ AI 分析服务接口
→ 输出短视频文件
```

## 2. Windows 后台端

Windows 本地端是首版核心，负责：

- 提供后台页面。
- 管理任务创建、状态流转、进度和异常信息。
- 调用 FFmpeg / FFprobe。
- 管理本地任务目录。
- 调用转写服务与 AI 分析服务。
- 输出最终切片文件。

## 3. NAS 存储

NAS 或本地目录在首版中作为视频来源与文件归档位置。系统需要支持：

- 选择 NAS / 本地目录中的已有视频。
- 把任务处理过程中的中间文件保存到项目任务目录。
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
→ tasks/{task_id}/source/
→ tasks/{task_id}/audio/
→ tasks/{task_id}/transcripts/
→ tasks/{task_id}/analysis/
→ 人工审核
→ tasks/{task_id}/clips/
```

SQLite 保存任务元数据、状态、候选片段与异常信息。大型视频素材和自动生成文件不直接提交到 Git。

## 6. 服务接口预留

- `transcript_service.py`：转写服务接口。
- `ai_clip_service.py`：AI 候选片段分析接口。
- `video_cut_service.py`：FFmpeg 自动切割接口。
- `storage_service.py`：任务目录与文件路径管理接口。
- `task_service.py`：任务状态与业务编排接口。

## 7. UI 设计参考

后续前端页面实现必须优先参考：

```text
docs/design/live_streaming_slicing_workflow_ui_16x9.png
```

视觉方向：Apple 风格、简洁、高级、留白充足、轻量玻璃拟态、卡片式布局、蓝色作为主强调色，适合作为个人本地工作流后台。

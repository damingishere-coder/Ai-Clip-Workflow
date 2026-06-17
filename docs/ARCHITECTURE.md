# 系统架构

本文档描述当前 v1.3.0 代码的真实结构。项目优先保证 Windows 本地可运行，不引入大型外部中间件。

## 1. 架构总览

```text
浏览器 127.0.0.1:8001
        │
        ▼
FastAPI 单体应用
        │
        ├─ routers/      页面路由与 API
        ├─ services/     任务、转写、AI、切片、字幕、发送中心
        ├─ models/       Pydantic 请求与结果模型
        ├─ db/           SQLite 建表、迁移、种子数据
        └─ templates/    Jinja2 页面
        │
        ├─ SQLite data/workflow.sqlite3
        ├─ 本地任务目录 tasks 或 STORAGE_ROOT
        ├─ FFmpeg / FFprobe
        ├─ 火山引擎 / faster-whisper
        ├─ DeepSeek / OpenAI-compatible / Ollama
        └─ opencli + 已登录 Chrome
```

核心判断：

- 这是 FastAPI 单体应用，不是前后端分离项目。
- 前端不是 React / Vue，而是 Jinja2 模板 + 原生 JavaScript。
- 数据库是 SQLite，不依赖 MySQL / PostgreSQL。
- 切片异步任务使用本地 `workflow_jobs` 轻量队列，不使用 Celery / Redis。
- 发送中心当前以 opencli 浏览器辅助为主，不绕过平台验证和人工确认。

## 2. 主要模块

| 目录或文件 | 作用 |
|---|---|
| `app/main.py` | FastAPI 应用入口、路由注册、中间件、静态资源挂载 |
| `app/core/config.py` | 读取 `.env` 和默认配置 |
| `app/db/database.py` | SQLite 表结构、迁移、默认数据 |
| `app/models/task.py` | 任务、候选片段、发布、AI 结果等模型 |
| `app/routers/pages.py` | Jinja2 页面入口 |
| `app/routers/tasks.py` | 任务创建、处理、切片、字幕相关 API |
| `app/routers/publish.py` | 发送中心 API |
| `app/services/storage_service.py` | 任务目录、上传文件、路径安全 |
| `app/services/transcript_service.py` | 音频转写和转写 Markdown 写入 |
| `app/services/ai_analysis_workflow_service.py` | AI 分析、候选片段写入、AI 历史恢复 |
| `app/services/video_cut_workflow_service.py` | 切片流程、cut run 版本化、失败回滚 |
| `app/services/subtitle_workflow_service.py` | 字幕样式、ASS 文件、字幕烧录 |
| `app/services/publish_service.py` | 发送队列、文案、封面帧、opencli 调用 |
| `app/services/job_service.py` | 本地轻量任务队列 |
| `app/templates/` | 后台页面模板 |
| `app/static/` | CSS、JS、图片、第三方前端资源 |

`app/services/task_service.py` 目前仍是兼容门面，向旧代码导出任务相关函数；新增逻辑优先放在拆分后的服务文件中。

## 3. 任务目录结构

每个任务有独立目录。官方当前目录约定如下：

```text
任务目录/
├─ source/              原始视频
├─ audio/source.wav     提取后的音频
├─ transcripts/         transcript.md 和转写进度
├─ analysis/            candidate_clips.json
├─ 05_clips/            自动切片输出
├─ 06_subtitled/        字幕文件和带字幕视频
├─ 07_covers/           发送中心候选封面帧
└─ logs/process.log     任务日志
```

`clips/` 是兼容旧版本的历史目录，新文档和新产物说明统一使用 `05_clips/`。

## 4. 数据库

当前 SQLite 由 `app/db/database.py` 初始化，真实表共 13 张：

```text
tasks
clip_candidates
output_clip
ai_prompt_presets
ai_analysis_runs
subtitle_style_presets
subtitle_jobs
publish_platform_configs
publish_accounts
publish_jobs
oauth_states
workflow_jobs
cut_runs
```

启动时会执行 `CREATE TABLE IF NOT EXISTS` 和逐列迁移，尽量兼容旧数据库。

## 5. 工作流边界

主任务状态只覆盖“从视频到切片”的主链路：

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

字幕和发送中心是切片完成后的独立流程：

- 字幕状态保存在 `subtitle_jobs`。
- 发送任务状态保存在 `publish_jobs`。
- `scheduled_at` 只是字段预留，没有定时调度器。

## 6. 外部依赖

| 能力 | 依赖 | 当前行为 |
|---|---|---|
| 视频探测 | FFprobe | 读取时长和文件大小，超时后降级显示未知 |
| 音频提取 | FFmpeg | 生成 `audio/source.wav` |
| 转写 | 火山引擎 / faster-whisper | 远程失败后提示手动改用本地 |
| AI 分析 | DeepSeek / OpenAI-compatible / Ollama | 长文本分块分析并合并候选 |
| 切片 | FFmpeg | 生成 `05_clips`，失败时保留旧活跃结果 |
| 字幕 | FFmpeg subtitles 滤镜 | 生成 `.ass` 和带字幕视频 |
| 发送中心 | opencli + Chrome | 辅助打开投稿页和填写信息，保留人工确认 |

## 7. 安全边界

- 项目设计为本地单用户后台，不建议公网暴露。
- `.env`、数据库、日志、视频、任务产物、浏览器缓存不提交 Git。
- `LOCAL_ADMIN_TOKEN` 用于本地 API 写操作保护。
- `ALLOWED_MEDIA_ROOTS` 控制可选择的外部媒体目录。
- 不绕过抖音 / B站验证码、登录失效、风控或人工确认。

## 8. 当前不做

- 不切换到 React / Vue。
- 不引入 Celery / Redis / 大型调度系统。
- 不做完全无人值守发布。
- 不自动识别平台直播间和开播状态。
- 不做多用户权限系统。

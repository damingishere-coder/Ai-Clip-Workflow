# 牛马片场 / NiuMa Studio

牛马片场是一个运行在 Windows 本地的 AI 高光生产后台。它的目标是把直播录像、综艺访谈、长视频素材整理成一套可追踪的本地任务：转写、AI 分析、人工审核、自动切片、自动加字幕，再进入发送中心做发布前整理。

当前版本：`1.3.0`

## 当前真实状态

已实现：

- Windows 本地 FastAPI 后台，前端使用 HTML、CSS、JavaScript、Jinja2。
- SQLite 本地数据库，保存任务、候选片段、切片、字幕任务、发送任务和配置。
- 上传本地视频，或选择允许目录中的本地 / NAS 视频。
- 每个任务生成独立任务目录。
- FFmpeg / FFprobe 音频提取、视频探测、切片、字幕烧录、封面帧提取。
- 火山引擎远程转写和本地 faster-whisper 转写。
- 远程 OpenAI-compatible / DeepSeek 或本地 Ollama 分析候选高光片段。
- 片段审核页面，可编辑、启用、删除候选片段。
- 切片输出到 `05_clips`，字幕成片输出到 `06_subtitled`，封面候选帧输出到 `07_covers`。
- 发送中心生成抖音 / B站待发送任务、标题、简介、话题和封面候选。
- 通过 opencli 调用已登录 Chrome 辅助投稿。
- 轻量本地任务队列用于切片异步任务，不依赖 Celery / Redis。

当前边界：

- 发送中心不是完全无人值守发布系统，遇到验证码、登录失效、平台风控、人工确认时必须人工处理。
- `publish_jobs.scheduled_at` 只是计划发布时间字段预留，当前没有后台定时调度器。
- OAuth、平台 API Provider、复杂封面模板、AI 生图封面属于预留或后续能力。
- 不包含多用户权限系统，不建议直接暴露到公网。

## 新手快速启动（Windows）

更详细的保姆级教程见 [docs/WINDOWS_SETUP.md](docs/WINDOWS_SETUP.md)。

1. 安装 Python 3.12 或更高版本。
2. 安装 FFmpeg，并确保 PowerShell 里能执行：

```powershell
ffmpeg -version
ffprobe -version
```

3. 打开 PowerShell，进入项目目录：

```powershell
cd "C:\Users\10578\Documents\New project 2"
```

4. 创建并启用虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

5. 安装依赖：

```powershell
pip install -r requirements.txt
```

6. 复制配置模板：

```powershell
copy .env.example .env
```

7. 按需编辑 `.env`。如果电脑没有 `E:` 盘，请把 `STORAGE_ROOT` 和 `TASKS_DIR` 改成你真实存在的目录，例如：

```text
STORAGE_ROOT=C:\NiuMaStudio\tasks
TASKS_DIR=C:\NiuMaStudio\tasks
```

8. 启动后台：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

9. 浏览器打开：

```text
http://127.0.0.1:8001
```

看到任务列表页面，就说明前后端已经启动。

## Docker 启动

Docker 是可选方式。当前 `docker-compose.yml` 默认把 Windows 的 `E:/直播间切片工作流存储` 挂载到容器内 `/workspace/tasks`。

如果你的电脑没有 E 盘，请先打开 `docker-compose.yml`，把这一行左侧路径改成真实存在的目录：

```yaml
- E:/直播间切片工作流存储:/workspace/tasks
```

启动命令：

```powershell
docker compose up --build
```

停止命令：

```powershell
docker compose down
```

## 真实任务流程

完整说明见 [docs/TASK_FLOW.md](docs/TASK_FLOW.md)。

```text
创建任务
→ 上传 / 导入视频
→ 提取 audio/source.wav
→ 转写 transcripts/transcript.md
→ AI 分析 analysis/candidate_clips.json + clip_candidates
→ 人工审核候选片段
→ 切片 05_clips
→ 字幕成片 06_subtitled
→ 发送中心 publish_jobs + 07_covers
→ opencli 辅助浏览器投稿
```

## 运行测试

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe -m pytest -q
```

测试会使用本地测试数据库和测试目录，不应该影响真实任务数据。

## 安全注意事项

- 真实 API Key、Token、Cookie、账号密码只能放在 `.env`，不要写进代码或文档。
- `.env`、数据库、日志、视频、音频、任务产物已被 Git 忽略。
- `.env.backup_*` 这类本地备份文件也可能含密钥，清理前请确认自己还需不需要。
- 项目默认本地使用，不建议开放公网访问。
- 不要把浏览器缓存、Chrome Profile、平台 Cookie 提交到 Git。

## 目录结构

```text
app/                 FastAPI 应用、路由、服务、模板、静态资源
app/core/            配置读取
app/db/              SQLite 建表、迁移、种子数据
app/models/          Pydantic 数据模型
app/routers/         页面和 API 路由
app/services/        任务、存储、转写、AI、切片、字幕、发送中心服务
data/                本地数据库和日志目录，真实数据不提交
tasks/               任务产物目录，真实视频和切片不提交
docs/                项目文档
prompts/             AI 分析 Prompt
scripts/             Windows 启动、opencli、手动诊断脚本
tests/               pytest 自动化测试
```

## 文档入口

- [docs/WINDOWS_SETUP.md](docs/WINDOWS_SETUP.md)：Windows 新手安装与启动。
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)：系统架构和模块边界。
- [docs/TASK_FLOW.md](docs/TASK_FLOW.md)：任务状态和完整工作流。
- [docs/DATABASE_SCHEMA.md](docs/DATABASE_SCHEMA.md)：SQLite 表结构。
- [docs/AI_ANALYSIS.md](docs/AI_ANALYSIS.md)：AI 分析配置与流程。
- [docs/CLIP_REVIEW.md](docs/CLIP_REVIEW.md)：候选片段审核。
- [docs/VIDEO_CUTTING.md](docs/VIDEO_CUTTING.md)：自动切片。
- [docs/UI_REFERENCE.md](docs/UI_REFERENCE.md)：页面和设计参考。
- [docs/SECURITY_AND_GIT.md](docs/SECURITY_AND_GIT.md)：密钥与 Git 安全。
- [DEVELOPMENT_LOG.md](DEVELOPMENT_LOG.md)：开发记录。
- [NEXT_STEPS.md](NEXT_STEPS.md)：下一步计划。

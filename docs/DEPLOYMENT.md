# 部署与启动说明

当前项目优先支持 Windows 本地运行。部署方式分为本地 Python 启动和 Docker 启动。

## 1. 推荐方式：Windows 本地 Python

适合日常开发、调试、连接本机 FFmpeg、Ollama、opencli。

详细步骤见 [WINDOWS_SETUP.md](WINDOWS_SETUP.md)。

核心命令：

```powershell
cd "C:\Users\10578\Documents\New project 2"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

成功后打开：

```text
http://127.0.0.1:8001
```

## 2. Docker 方式

适合隔离 Python 环境。当前 Compose 配置：

- 容器端口：`8001`
- 容器内数据目录：`/app/data`
- 容器内任务目录：`/workspace/tasks`
- 宿主机默认挂载：`E:/直播间切片工作流存储`

启动：

```powershell
docker compose up --build
```

停止：

```powershell
docker compose down
```

如果电脑没有 E 盘，请先修改 `docker-compose.yml`：

```yaml
volumes:
  - 你的真实Windows目录:/workspace/tasks
```

示例：

```yaml
volumes:
  - C:/NiuMaStudio/tasks:/workspace/tasks
```

## 3. 配置文件

复制模板：

```powershell
copy .env.example .env
```

关键配置：

| 配置 | 说明 |
|---|---|
| `STORAGE_ROOT` | 任务产物根目录 |
| `TASKS_DIR` | 任务目录，通常和 `STORAGE_ROOT` 一样 |
| `DATABASE_PATH` | SQLite 数据库路径 |
| `TRANSCRIPTION_PROVIDER` | `volcengine` 或 `local` |
| `AI_DEFAULT_PROVIDER` | `remote` 或 `local` |
| `LOCAL_ADMIN_TOKEN` | 本地写接口保护令牌 |
| `OPENCLI_HOST_BRIDGE_URL` | Docker 调用宿主机 opencli 桥接时使用 |

真实密钥只写入 `.env`，不要写入代码、文档或 `.env.example`。

## 4. 外部依赖

| 能力 | 必需依赖 | 检查命令 |
|---|---|---|
| 视频探测 / 切片 / 字幕 | FFmpeg + FFprobe | `ffmpeg -version` / `ffprobe -version` |
| 火山引擎转写 | 火山引擎 API Key | 检查 `.env` |
| 本地转写 | faster-whisper 依赖 | 运行本地转写时验证 |
| 本地 AI | Ollama | `ollama list` |
| 发送中心浏览器辅助 | opencli + 已登录 Chrome | `opencli --help` |

## 5. 后台任务说明

当前没有 Celery、Redis 或独立 Worker。

代码里存在 `workflow_jobs` 本地轻量队列表，当前主要用于异步切片任务。它运行在同一个 FastAPI 进程内，不需要额外部署服务。

转写流程使用 FastAPI `BackgroundTasks`。AI 分析会在线程池里执行。视频切片既有同步入口，也有异步队列入口。

## 6. 发送中心边界

发送中心当前主要做：

- 从已完成切片生成抖音 / B站待发送任务。
- 生成标题、简介、话题。
- 生成候选封面帧。
- 调用 opencli 辅助已登录 Chrome 打开和填写投稿页。

发送中心当前不做：

- 不保证完全无人值守发布。
- 不绕过验证码。
- 不绕过登录失效。
- 不绕过平台风控。
- 不自动按 `scheduled_at` 定时发布。

## 7. 健康检查

启动后可以访问：

```text
http://127.0.0.1:8001/health
```

成功时应返回健康状态 JSON。

## 8. 常见问题

### 启动时报 E 盘不存在

原因：默认任务目录是历史 Windows 路径 `E:\直播间切片工作流存储`。

解决：

- 本地 Python：修改 `.env` 中的 `STORAGE_ROOT` 和 `TASKS_DIR`。
- Docker：修改 `docker-compose.yml` 的 volume 左侧路径。

### 页面能打开，但视频处理失败

优先检查：

```powershell
ffmpeg -version
ffprobe -version
```

如果命令不可用，说明 FFmpeg 没有安装或没有加入 PATH。

### AI 或转写失败

优先检查：

- `.env` 里是否填了真实 API Key。
- `TRANSCRIPTION_PROVIDER` 是否为 `volcengine` 或 `local`。
- `AI_DEFAULT_PROVIDER` 是否为 `remote` 或 `local`。
- 本地 Ollama 是否已经启动。

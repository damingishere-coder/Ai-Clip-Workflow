# Windows 本地部署与启动说明

这份文档写给第一次运行“牛马片场”的 Windows 用户。下面所有命令都在 PowerShell 里执行。

## 1. 准备软件

需要安装：

- Python 3.12 或更高版本。
- FFmpeg，包含 `ffmpeg` 和 `ffprobe`。
- 可选：Docker Desktop。
- 可选：Ollama，用于本地 AI 分析。
- 可选：opencli，用于发送中心辅助打开投稿页。

检查 Python：

```powershell
python --version
```

成功时会看到类似：

```text
Python 3.12.10
```

检查 FFmpeg：

```powershell
ffmpeg -version
ffprobe -version
```

成功时会看到版本信息。如果提示“不是内部或外部命令”，说明 FFmpeg 还没有加入 Windows PATH。

## 2. 进入项目目录

```powershell
cd "C:\Users\10578\Documents\New project 2"
```

这条命令的作用是进入项目根目录。成功后，PowerShell 左侧路径应该显示 `New project 2`。

## 3. 创建虚拟环境

```powershell
python -m venv .venv
```

这条命令会在项目里创建 `.venv` 文件夹，用来放 Python 依赖。成功时通常没有输出。

启用虚拟环境：

```powershell
.\.venv\Scripts\Activate.ps1
```

成功后，命令行前面会出现 `(.venv)`。

如果 PowerShell 提示脚本不能运行，可以只在当前窗口执行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

## 4. 安装依赖

```powershell
pip install -r requirements.txt
```

这条命令会安装 FastAPI、pytest、faster-whisper 等依赖。成功后不会出现红色报错。

## 5. 准备配置文件

```powershell
copy .env.example .env
```

这条命令会复制一份本地配置。真实 API Key 只写进 `.env`，不要写进 `.env.example`。

重点检查 `.env`：

- `STORAGE_ROOT`：任务产物根目录。
- `TASKS_DIR`：任务目录，通常和 `STORAGE_ROOT` 一样。
- `TRANSCRIPTION_PROVIDER`：只能填 `volcengine` 或 `local`。
- `AI_DEFAULT_PROVIDER`：只能填 `remote` 或 `local`。
- `VOLCENGINE_ASR_API_KEY`：火山引擎转写密钥。
- `AI_ANALYSIS_REMOTE_API_KEY`：AI 分析密钥。
- `AI_PUBLISH_REMOTE_API_KEY`：发送中心文案密钥。

如果电脑没有 E 盘，请把：

```text
STORAGE_ROOT=E:\直播间切片工作流存储
TASKS_DIR=E:\直播间切片工作流存储
```

改成真实存在的目录，例如：

```text
STORAGE_ROOT=C:\NiuMaStudio\tasks
TASKS_DIR=C:\NiuMaStudio\tasks
```

## 6. 启动项目

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

成功时会看到类似：

```text
Uvicorn running on http://127.0.0.1:8001
```

浏览器打开：

```text
http://127.0.0.1:8001
```

看到任务列表页面，就说明后端和前端都已启动。

## 7. 常用检查命令

检查代码风格：

```powershell
.\.venv\Scripts\python.exe -m ruff check app tests
```

运行自动测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

检查 Git 当前状态：

```powershell
git status
```

如果看到 `nothing to commit, working tree clean`，说明没有未保存的 Git 修改。

## 8. Docker 启动

Docker 是可选方式。当前 `docker-compose.yml` 默认挂载：

```yaml
- E:/直播间切片工作流存储:/workspace/tasks
```

如果你的电脑没有 E 盘，请先把左侧路径改成真实存在的 Windows 目录。

启动：

```powershell
docker compose up --build
```

停止：

```powershell
docker compose down
```

## 9. 功能边界提醒

- 发送中心会辅助整理发布任务，但不是完全无人值守发布系统。
- 遇到验证码、登录失效、平台风控、人工确认时，需要用户自己处理。
- `scheduled_at` 当前只是保存计划发布时间，不会自动定时发送。
- 不要把 `.env`、数据库、日志、视频、浏览器缓存提交到 Git。

# 部署说明

## 1. 当前部署模式

v2.2.0 支持两种运行方式。日常使用推荐 Docker Desktop；真实抖音 / B站投稿与抖音作品指标同步无论使用哪种方式，都必须由 Windows 主机上的 Chrome Worker 执行。

### 方式 A：Docker Desktop + Windows Worker（推荐日常使用）

```
你的 Windows 电脑
├── Docker Desktop 运行 niuma-studio
│   └── FastAPI + SQLite Scheduler + FFmpeg
├── NiuMa Studio Docker Watcher
│   └── 容器运行时自动启动 Windows Chrome Worker
├── 系统 Chrome 独立账号目录
└── 浏览器打开 http://127.0.0.1:8001
```

**适用场景**：日常处理、排期和真实灰度发布。平时不需要手动打开 PowerShell。

### 方式 B：Windows 本地 Python 直接运行（开发 / 诊断）

```
你的 Windows 电脑
├── Python 3.12 + .venv
├── FFmpeg（系统安装，需在 PATH 中）
├── uvicorn 监听 8001 端口
├── scripts/publish_host_worker.py（真实投稿时）
└── 浏览器打开 http://127.0.0.1:8001
```

**适用场景**：代码开发、自动化测试、发布 Worker 诊断。

---

## 2. Windows 本地 Python 方式详细步骤

### 2.1 环境要求

| 软件 | 最低版本 | 检查命令 |
| --- | --- | --- |
| Python | 3.12 | `python --version` |
| FFmpeg | 4.0+ | `ffmpeg -version` |
| Git | 2.30+ | `git --version` |

### 2.2 获取代码

打开 PowerShell，进入你想放项目的目录：

```powershell
git clone <仓库地址> "New project 2"
cd "New project 2"
```

### 2.3 创建虚拟环境并安装依赖

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

看到 `Successfully installed ...` 即为成功。

### 2.4 配置环境变量

```powershell
copy .env.example .env
```

然后用记事本或 VS Code 打开 `.env`，按注释填写：

- `STORAGE_ROOT`：任务产物存放目录（默认 `E:\直播间切片工作流存储`）
- `TASKS_DIR`：每条任务的原片、音频、切片和字幕目录，默认与 `STORAGE_ROOT` 相同
- `UPLOAD_TEMP_DIR`：浏览器上传大视频时的临时目录，默认 `E:\直播间切片工作流存储\_临时上传`
- `PUBLISH_SCHEDULER_EXPORT_DIR`：手动发布包目录，默认 `E:\直播间切片工作流存储\_发布包`
- `AI_CODEX_PATH`：受控 Codex CLI 命令路径（默认 `codex`）
- `AI_CODEX_MODEL`：Codex CLI 分析模型（默认 `gpt-5.6-sol`）
- `AI_ANALYSIS_REMOTE_API_KEY`：DeepSeek API Key（可选，用远程 AI 分析时需要）
- `VOLCENGINE_ASR_API_KEY`：火山引擎转写 Key（可选，用远程转写时需要）
- `LOCAL_ADMIN_TOKEN`：管理接口鉴权 Token（可留空或设随机字符串）

### 2.5 启动服务

```powershell
uvicorn app.main:app --reload --port 8001
```

看到类似以下输出说明启动成功：

```text
INFO:     Uvicorn running on http://127.0.0.1:8001
INFO:     Application startup complete.
```

### 2.6 打开页面

浏览器访问：

```text
http://127.0.0.1:8001
```

### 2.7 停止服务

在终端按 `Ctrl + C`。

---

## 3. Docker 方式详细步骤

### 3.1 环境要求

- Docker Desktop for Windows（已安装并启动）

### 3.2 配置环境变量

与本地 Python 方式相同，先 `copy .env.example .env` 并填写配置。

### 3.3 构建并启动

```powershell
docker compose up --build
```

首次启动会下载基础镜像并安装依赖，等待几分钟。

看到以下输出说明启动成功：

```text
niuma-studio  | INFO:     Uvicorn running on http://0.0.0.0:8001
niuma-studio  | INFO:     Application startup complete.
```

### 3.4 打开页面

```text
http://127.0.0.1:8001
```

### 3.5 停止容器

```powershell
docker compose down
```

### 3.6 Docker 特有说明

- **存储目录**：`docker-compose.yml` 默认将 `E:\直播间切片工作流存储` 挂载到容器内 `/workspace/tasks`。如果你的存储目录在其他位置，请修改 `docker-compose.yml` 中的 `volumes` 配置。
- **代码热更新**：`app/` 和 `prompts/` 目录以 volume 方式挂载，修改代码后容器自动重载。
- **Ollama 连接**：如果 Ollama 在宿主机运行，容器内通过 `http://host.docker.internal:11434/v1` 访问。
- **Windows 发布 Worker**：容器内通过 `http://host.docker.internal:8765` 访问宿主机受 Token 保护的 Chrome Worker；旧 opencli 仅是默认关闭的兼容模式。

---

## 4. 当前部署的局限性

这些是当前版本的**已知限制**，不是 Bug，会在后续版本逐步改善：

| 局限 | 说明 | 影响 |
| --- | --- | --- |
| **单业务进程** | API、全自动流水线和 Scheduler 在同一个 FastAPI 进程 | 进程重启会中断正在处理的步骤，需从失败点重试或续跑 |
| **无外部任务队列** | 使用 FastAPI BackgroundTasks 和轻量 SQLite Job 记录，没有 Redis / Celery | 适合个人单机，不适合多机并发处理 |
| **单机主存储** | 生产任务目录由当前 Windows 主机 / Docker 挂载负责 | 可引用 NAS 原片，但不支持多台处理机共同写同一任务 |
| **无负载均衡** | 不支持多实例部署 | 只能一个人用，不能横向扩展 |
| **无 HTTPS** | 只有 HTTP | 只适合本地使用，不要暴露到公网 |
| **单用户** | 没有登录和用户隔离 | 谁打开浏览器都能操作所有任务 |

---

## 5. 未来部署场景（规划中）

### 5.1 短期：Windows 本地 + NAS 存储

```text
Windows 主机（运行 FastAPI）
└── 存储目录指向 NAS 网络路径
    └── \\192.168.1.100\share\切片工作流存储
```

只需修改 `.env` 中的 `STORAGE_ROOT` 为 NAS 路径即可。无架构变更。

### 5.2 中期：API + Worker 分离

```text
┌─────────────────┐     ┌─────────────────────┐
│  API 服务器       │────▶│  Redis Queue         │
│  (FastAPI)       │     │  (消息队列)           │
│  只做调度+查询    │     └─────────┬───────────┘
└─────────────────┘               │
                                  ▼
                        ┌─────────────────────┐
                        │  Worker 1 (Windows)  │
                        │  Worker 2 (Windows)  │
                        │  Worker 3 (Linux)    │
                        │  各自处理耗时任务     │
                        └─────────────────────┘
                                  │
                                  ▼
                        ┌─────────────────────┐
                        │  PostgreSQL          │
                        │  + MinIO / NAS       │
                        └─────────────────────┘
```

- API 服务器：轻量 FastAPI，只做任务创建、状态查询、页面渲染
- Worker：独立进程，从 Redis 取任务，执行 FFmpeg/转写/AI 分析
- 共享存储：NAS 或 MinIO，所有 Worker 可见
- 共享数据库：PostgreSQL，API 和 Worker 共同读写

### 5.3 长期：多用户平台化

```text
┌──────────────────────────────────────────┐
│              负载均衡 / 反向代理            │
│              (Nginx / Traefik)            │
└────────┬─────────────┬───────────────────┘
         ▼             ▼
┌─────────────┐ ┌─────────────┐
│ API 实例 1   │ │ API 实例 2   │  ← 无状态，可横向扩展
└──────┬──────┘ └──────┬──────┘
       │               │
       └───────┬───────┘
               ▼
     ┌─────────────────┐
     │ PostgreSQL +     │
     │ Redis            │
     └─────────────────┘
               │
               ▼
     ┌─────────────────┐
     │ Worker 集群       │  ← 按需扩缩
     │ (Celery / RQ)    │
     └─────────────────┘
               │
               ▼
     ┌─────────────────┐
     │ MinIO / S3       │  ← 对象存储
     │ (共享任务产物)    │
     └─────────────────┘
```

---

## 6. 当前不做的事

以下部署方式**当前明确不推荐、不维护**：

| 不推荐 | 原因 |
| --- | --- |
| 直接暴露到公网 | 无 HTTPS、无用户认证、无速率限制，极不安全 |
| 云服务器生产部署 | 当前架构不支持多用户、无监控告警、无自动恢复 |
| Kubernetes 部署 | 单体应用无益于 K8s，过度设计 |
| 多实例负载均衡 | SQLite 不支持并发写，多实例会数据冲突 |
| macOS / Linux 主机部署 | 未测试，opencli 和部分路径逻辑依赖 Windows |

如果将来需要上云或上 K8s，需先完成 P3（数据库升级 + Worker 分离）。

---

## 7. 环境变量参考

完整环境变量列表见 `.env.example`。以下是最关键的几个：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `STORAGE_ROOT` | `E:\直播间切片工作流存储` | 任务产物根目录 |
| `TASKS_DIR` | 同 `STORAGE_ROOT` | 任务目录（优先级高于 STORAGE_ROOT） |
| `UPLOAD_TEMP_DIR` | `{TASKS_DIR}\_临时上传` | FastAPI 接收大视频时的进程临时目录，不回退到 C 盘 |
| `PUBLISH_SCHEDULER_EXPORT_DIR` | `{STORAGE_ROOT}\_发布包` | 手动导出的本地发布包目录 |
| `DATA_DIR` | 项目目录 `data/` | 数据库存放目录 |
| `DATABASE_PATH` | `data/workflow.sqlite3` | 数据库文件路径 |
| `AI_CODEX_PATH` | `codex` | 受控 Codex CLI 命令路径 |
| `AI_CODEX_MODEL` | `gpt-5.6-sol` | Codex CLI 分析模型 |
| `AI_ANALYSIS_REMOTE_API_KEY` | 空 | DeepSeek API Key |
| `TRANSCRIPTION_PROVIDER` | `volcengine` | 转写引擎：`volcengine` 或 `faster_whisper` |
| `AI_PROVIDER` | `codex` | AI 分析引擎：`codex`、`remote` 或 `local` |

---

## 8. 健康检查

服务启动后，可以访问健康检查接口确认运行正常：

```text
GET http://127.0.0.1:8001/health
```

正常返回：

```json
{"status": "ok", "app": "NiuMa Studio"}
```

---

## 9. 备份建议

### 当前版本（单机 SQLite）

需手动备份两类数据：

1. **数据库**：复制 `data/workflow.sqlite3` 到安全位置
2. **任务产物**：复制 `STORAGE_ROOT` 下所有任务目录到安全位置

建议定期（如每周）执行备份脚本（待开发）。

### 后续版本（PostgreSQL）

- 数据库：`pg_dump` 定期导出
- 任务产物：MinIO / S3 自带的版本管理和复制功能
- 备份自动化：CI 定时任务或 K8s CronJob

# 系统架构

## 1. 当前架构概览

### 1.1 架构形态

当前 v1.3 为 **FastAPI 单体应用**，运行在 Windows 本地，所有组件打包在同一个进程中。

```text
┌─────────────────────────────────────────────────────────┐
│                    浏览器 (127.0.0.1:8001)               │
└─────────────────────┬───────────────────────────────────┘
                      │ HTTP
┌─────────────────────▼───────────────────────────────────┐
│              FastAPI 单体应用 (uvicorn)                   │
│                                                         │
│  ┌──────────┐  ┌──────────┐  ┌────────────────────┐   │
│  │ routers/ │  │services/ │  │  services/ai/       │   │
│  │ 页面+API │──│ 业务逻辑  │──│  AI Provider 抽象   │   │
│  └──────────┘  └──────────┘  └────────────────────┘   │
│                                                         │
│  ┌──────────┐  ┌──────────┐  ┌────────────────────┐   │
│  │ models/  │  │  core/   │  │  db/                │   │
│  │ Pydantic │  │  配置管理  │  │  SQLite 连接+迁移    │   │
│  └──────────┘  └──────────┘  └────────────────────┘   │
└─────────────────────────────────────────────────────────┘
         │                │                  │
         ▼                ▼                  ▼
┌─────────────┐  ┌──────────────┐  ┌──────────────────┐
│   SQLite    │  │  本地文件系统  │  │  外部 AI API     │
│ workflow    │  │  任务产物目录  │  │  DeepSeek /      │
│ .sqlite3    │  │  视频/音频等  │  │  Ollama /        │
│             │  │              │  │  火山引擎         │
└─────────────┘  └──────────────┘  └──────────────────┘
```

### 1.2 技术栈一览

| 层次 | 技术选型 | 说明 |
| --- | --- | --- |
| **Web 框架** | FastAPI + uvicorn | 异步 HTTP 服务，端口 8001 |
| **模板引擎** | Jinja2 | 后台页面渲染，Apple 风格 UI |
| **数据库** | SQLite | 单文件数据库，`data/workflow.sqlite3` |
| **数据校验** | Pydantic v2 | 请求/响应模型校验，AI 结果解析 |
| **文件存储** | 本地文件系统 | Windows 本地目录，默认 `E:\直播间切片工作流存储` |
| **视频处理** | FFmpeg / FFprobe | 音频提取、视频切割、字幕合成、封面帧 |
| **语音转写** | faster-whisper / 火山引擎 | 本地模型或远程 API，输出逐句时间戳 |
| **AI 分析** | DeepSeek API / Ollama | Provider 抽象层，支持 chat/completions 和 responses 协议 |
| **发布辅助** | opencli | 调用已登录 Chrome 辅助浏览器投稿 |
| **容器化** | Docker + docker-compose | 可选部署方式，开发/测试用 |

### 1.3 核心设计决策

- **单体进程**：所有路由、服务、数据库访问在同一 Python 进程中，无独立 Worker。
- **同步 FFmpeg**：视频处理通过 `subprocess` 同步调用，阻塞当前请求直到完成。
- **无消息队列**：任务处理由前端按钮触发，无后台 Job Queue / Celery。
- **无用户体系**：单用户本地使用，通过 `LOCAL_ADMIN_TOKEN` 做简易鉴权。
- **无定时调度**：`publish_jobs.scheduled_at` 仅为字段预留，不自动发送。

---

## 2. 模块分层

```text
app/
├── main.py                  ← FastAPI 应用入口，路由注册，中间件
├── core/
│   └── config.py            ← 环境变量读取，Settings 数据类
├── db/
│   └── database.py          ← SQLite 连接、建表、迁移、种子数据
├── models/
│   ├── task.py              ← Task / ClipCandidate / OutputClip 等 Pydantic 模型
│   └── settings.py          ← 配置相关 Pydantic 模型
├── routers/
│   ├── pages.py             ← 页面路由（Jinja2 模板渲染）
│   ├── tasks.py             ← 任务 CRUD + 处理流程 API
│   ├── files.py             ← 文件上传/路径选择 API
│   ├── media.py             ← 媒体文件访问 API
│   ├── ai_prompts.py        ← AI Prompt 方案管理 API
│   ├── publish.py           ← 发送中心 API
│   └── settings.py          ← 系统设置 API
└── services/
    ├── task_service.py      ← 任务状态编排（含字幕渲染、发布队列集成）
    ├── storage_service.py   ← 任务目录与文件路径管理
    ├── transcript_service.py← 转写服务（faster-whisper + 火山引擎）
    ├── video_cut_service.py ← FFmpeg 切割封装
    ├── ai_clip_service.py   ← AI 片段分析编排
    ├── ai_config_service.py ← AI 配置读写
    ├── ai_prompt_preset_service.py ← Prompt 方案服务
    ├── publish_service.py   ← 发送中心服务
    ├── publish_providers.py ← 发布平台 Provider
    └── ai/
        ├── base.py          ← AI Provider 抽象基类
        ├── local_model_provider.py    ← Ollama 本地 Provider
        ├── remote_responses_provider.py ← DeepSeek 远程 Provider
        ├── ai_clip_analyzer.py        ← AI 分析编排器
        └── diagnostics.py   ← AI 连接诊断
```

---

## 3. 数据存储

### 3.1 数据库

- **类型**：SQLite，单文件 `data/workflow.sqlite3`
- **连接方式**：`sqlite3.connect()`，每次请求 `@contextmanager` 获取连接
- **迁移方式**：`init_db()` 启动时自动执行 `CREATE TABLE IF NOT EXISTS` + 逐列 ALTER TABLE 补齐
- **种子数据**：启动时自动写入默认 AI Prompt 方案、字幕样式、平台配置

### 3.2 表结构（10 张表）

| 表名 | 用途 |
| --- | --- |
| `tasks` | 任务主表，状态流转 |
| `clip_candidates` | AI 候选片段 |
| `output_clip` | 输出切片记录 |
| `ai_prompt_presets` | AI Prompt 方案（3 套） |
| `ai_analysis_runs` | AI 分析历史 |
| `subtitle_style_presets` | 字幕样式预设 |
| `subtitle_jobs` | 字幕任务 |
| `publish_platform_configs` | 平台 OAuth 配置 |
| `publish_accounts` | 发布账号 |
| `publish_jobs` | 发布任务队列 |

详见 [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md)

### 3.3 文件存储

- 存储根目录由 `STORAGE_ROOT` / `TASKS_DIR` 环境变量配置，默认 `E:\直播间切片工作流存储`
- 每个任务一个子目录，以 `task_dir_name` 命名
- 大文件（视频、音频）不入 Git、不入数据库，只存路径

---

## 4. AI Provider 架构

```text
                    ┌─────────────────────┐
                    │   AI Provider 抽象    │
                    │   (base.py)          │
                    └──────────┬──────────┘
                               │
          ┌────────────────────┼────────────────────┐
          ▼                    ▼                    ▼
┌──────────────────┐  ┌──────────────┐  ┌──────────────────┐
│ Remote Responses │  │  Local Model │  │  火山引擎 ASR     │
│ (DeepSeek)       │  │  (Ollama)    │  │  (远程转写)       │
│                  │  │              │  │                  │
│ chat/completions │  │ chat/        │  │ BigModel ASR     │
│ + /v1/responses  │  │ completions  │  │ Flash API        │
└──────────────────┘  └──────────────┘  └──────────────────┘
```

- **Provider 抽象**：`BaseAIProvider` 定义统一接口，支持协议检测和自动降级
- **远程**：OpenAI-compatible API，支持 chat/completions 和 responses 两种协议
- **本地**：Ollama API，按小段拆分长文本后合并结果
- **转写**：火山引擎（默认）或本地 faster-whisper，失败不自动降级，用户手动切换

---

## 5. 发送中心架构

```text
output_clip 生成 + 字幕完成
        │
        ▼
┌───────────────┐
│  发送中心页面   │
│  /publish      │
└───────┬───────┘
        │
        ▼
┌───────────────────────────────────────────┐
│  publish_service.py                       │
│                                           │
│  ┌─────────┐  ┌─────────┐  ┌──────────┐ │
│  │ 队列刷新 │  │ AI 文案 │  │ 封面帧   │ │
│  │ 双平台   │  │ 标题/简介│  │ 07_covers│ │
│  └─────────┘  └─────────┘  └──────────┘ │
│                                           │
│  ┌─────────────────────────────────────┐ │
│  │  opencli 辅助浏览器投稿              │ │
│  │  抖音 + B站 Chrome Profile          │ │
│  └─────────────────────────────────────┘ │
└───────────────────────────────────────────┘
```

**安全边界**：不绕过验证码、登录失效、风控和人工确认。

---

## 6. 部署架构

### 6.1 本地直接运行

```text
Windows 主机
├── Python 3.12 + .venv
├── FFmpeg（系统安装）
├── uvicorn app.main:app --port 8001
└── 浏览器 http://127.0.0.1:8001
```

### 6.2 Docker 部署

```text
Docker 容器 (niuma-studio)
├── Python 3.12 + FFmpeg（容器内预装）
├── uvicorn app.main:app --host 0.0.0.0 --port 8001
├── 代码目录 volume 挂载（热更新）
├── 存储目录 volume 挂载（E:\ → /workspace/tasks）
└── opencli 桥接（host.docker.internal:8765）
```

详见 [DEPLOYMENT.md](DEPLOYMENT.md)

---

## 7. 当前已实现功能

```text
新建任务表单
→ 上传视频 / 选择 NAS 路径
→ FFmpeg 提取音频
→ 转写（火山引擎远程 / faster-whisper 本地）
→ AI 候选片段分析（DeepSeek / Ollama）
→ 候选片段人工审核（启用/禁用/编辑时间）
→ FFmpeg 自动切割 → 05_clips/
→ ASS 字幕 + FFmpeg 合成 → 06_subtitled/
→ 发送中心队列 → opencli 辅助投稿（抖音 + B站）
```

---

## 8. 架构演进路线

### 8.1 当前阶段：P2-1（已完成）

- FastAPI 单体应用
- SQLite 单文件数据库
- 本地文件系统存储
- FFmpeg 同步本地处理
- 本地/远程 AI Provider
- opencli 发布辅助
- 代码检查与 CI 流程

### 8.2 短期演进（P2-2 ~ P2-3）

**目标**：不改变单体形态，增强本地可靠性和安全边界。

| 方向 | 具体措施 |
| --- | --- |
| **数据库** | SQLite 保持不变，增加 WAL 模式、备份脚本 |
| **Job Worker** | 引入本地后台 Job Queue（`threading` / `asyncio`），将耗时任务（转写、AI 分析、切割）异步化，不阻塞 HTTP 请求 |
| **安全增强** | `LOCAL_ADMIN_TOKEN` 鉴权强化，敏感操作确认对话框，操作日志记录 |
| **存储** | 支持 NAS 路径作为存储根目录，`STORAGE_ROOT` 可配置为网络路径 |
| **错误恢复** | 任务失败后可从中断点重试，而非从头开始 |

### 8.3 中期演进（P3）

**目标**：引入消息队列，API 与 Worker 分离，为远程访问做准备。

| 方向 | 具体措施 |
| --- | --- |
| **数据库** | 从 SQLite 迁移到 PostgreSQL，利用 JSONB、全文搜索、行级安全 |
| **消息队列** | 引入 Redis + RQ / Celery，任务处理从同步改为异步队列 |
| **Worker 分离** | API 服务与 Worker 进程独立部署，可横向扩展 Worker |
| **对象存储** | 支持 NAS / MinIO / S3 作为任务产物存储后端 |
| **配置管理** | 从 `.env` 文件迁移到结构化配置（YAML/TOML），支持多环境 |
| **健康检查** | 增加 Worker 心跳、任务超时检测、死信队列 |

### 8.4 长期演进（P4+）

**目标**：多用户支持，为团队协作和 SaaS 化打基础。

| 方向 | 具体措施 |
| --- | --- |
| **用户体系** | 用户注册/登录，JWT Token 鉴权，角色权限（admin/operator/viewer） |
| **多租户** | 按用户隔离任务数据、存储目录、AI 配额 |
| **发布账号托管** | 平台 OAuth Token 加密存储，自动刷新，权限范围最小化 |
| **任务配额** | 按用户/租户限制并发任务数、存储空间、AI 调用次数 |
| **审计日志** | 完整操作记录（谁、何时、做了什么、结果如何），不可篡改 |
| **监控告警** | Prometheus + Grafana，任务失败率、API 延迟、磁盘使用量告警 |
| **API 版本化** | `/api/v1/` → `/api/v2/`，向后兼容，废弃通知 |

---

## 9. 当前明确不做的事

以下事项**暂不在任何阶段计划中**，等有明确需求后再评估：

| 暂不做的 | 原因 |
| --- | --- |
| **SaaS 多租户** | 当前是个人本地工具，不需要租户隔离和计费系统 |
| **真实全自动发布** | 平台有验证码、风控、登录失效，全自动不可行也不安全 |
| **强依赖云部署** | 首版定位 Windows 本地工具，不应强制要求云服务器 |
| **移动端 App** | 核心工作流依赖 FFmpeg 和大文件处理，不适合移动端 |
| **实时直播流处理** | 当前是录播后处理，实时流需要完全不同的技术栈 |
| **多人协作编辑** | 当前是单人工作流，协作需要解决冲突合并和锁的问题 |
| **第三方平台 API 直接发布** | 抖音/B站开放平台 API 权限申请困难，opencli 浏览器辅助是务实选择 |

---

## 10. 数据流转关系

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

---

## 11. 服务接口一览

| 服务文件 | 职责 |
| --- | --- |
| `transcript_service.py` | 本地 faster-whisper 转写、火山引擎远程转写、转写预览解析和进度管理 |
| `services/ai/` | AI 候选片段分析模块：Provider 抽象、远程 DeepSeek Provider、本地 Ollama Provider、AI JSON 解析和片段分析编排 |
| `video_cut_service.py` | FFmpeg 自动切割接口 |
| `storage_service.py` | 任务目录与文件路径管理接口（含 `task_dir_name` 分配、路径解析、视频文件校验） |
| `task_service.py` | 任务状态与业务编排接口（含字幕渲染、字幕样式、发布队列集成） |
| `publish_service.py` | 发送中心服务（opencli 队列管理、封面帧生成、AI 文案生成、内容安全清洗、平台发送脚本编排） |
| `ai_config_service.py` | 三类 AI 接口配置读写（音频转写 / 候选切片分析 / 发布文案生成） |

---

## 12. 设计参考

UI 设计参考文件：

```text
docs/design/live_streaming_slicing_workflow_ui_16x9.png
```

视觉方向：Apple 风格、简洁、高级、留白充足、轻量玻璃拟态、卡片式布局、蓝色作为主强调色，适合作为个人本地 AI 高光生产后台。

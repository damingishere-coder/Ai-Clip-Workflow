# Live Streaming Slicing Workflow

直播切片工作流是一个运行在 Windows 本地的个人后台工具，用来把直播长视频整理成可审核、可切割的短视频候选片段。

当前 MVP 聚焦本地处理链路：新建任务、上传本地视频、提取音频、生成转写、AI 分析候选片段、人工审核、自动切割输出。

## 当前状态

- 后端：FastAPI 可启动。
- 前端：HTML + CSS + JavaScript + Jinja2 后台页面。
- 数据库：SQLite，保存任务、候选片段、输出片段等信息。
- 视频处理：预留并接入 FFmpeg / FFprobe 调用位置。
- 转写：本地 faster-whisper 链路已接入。
- AI 分析：支持远程 OpenAI-compatible API 和本地 Ollama 风格接口。
- 配置安全：真实 `.env` 已被 Git 忽略，不会提交真实 API Key。

## 新手启动方式

第一次使用请先阅读：

```text
docs/PROJECT_GUIDE.md
```

里面按“准备环境、启动项目、打开页面、测试功能”的顺序写好了。

推荐启动方式：Docker 一键启动。

```powershell
docker compose up --build
```

启动后在浏览器打开：

```text
http://127.0.0.1:8001
```

停止项目：

```powershell
docker compose down
```

如果暂时不用 Docker，也可以继续使用本地虚拟环境启动：

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --port 8001
```

## 文档入口

```text
docs/PROJECT_GUIDE.md          新手项目总览与启动说明
docs/SECURITY_AND_GIT.md       API Key、.env、Git 提交安全说明
docs/ARCHITECTURE.md           系统架构
docs/TASK_FLOW.md              任务处理流程
docs/DATABASE_SCHEMA.md        数据库表结构
docs/AI_ANALYSIS.md            AI 分析配置与流程
docs/CLIP_REVIEW.md            候选片段审核说明
docs/VIDEO_CUTTING.md          自动切割说明
docs/UI_REFERENCE.md           UI 页面与设计参考
DEVELOPMENT_LOG.md             开发记录
NEXT_STEPS.md                  下一步计划
```

## 目录结构

```text
app/                 FastAPI 主应用
app/core/            配置读取
app/db/              SQLite 数据库连接
app/models/          数据模型
app/routers/         页面路由与 API 路由
app/services/        任务、存储、转写、AI、切割等服务
app/templates/       Jinja2 页面模板
app/static/          CSS 与 JavaScript
data/                本地数据库目录，真实数据不提交
tasks/               任务产物目录，真实视频和切片不提交
Dockerfile           Docker 镜像构建文件
docker-compose.yml   Docker 一键启动配置
docs/                项目文档
prompts/             AI 分析 Prompt
scripts/             本地测试脚本
```

## 敏感信息规则

真实 API Key 只能放在项目根目录的 `.env` 文件里。

`.env` 已经写入 `.gitignore`，不会保存到 Git，也不会上传到远程仓库。仓库中只保留 `.env.example` 模板，方便以后按模板重新填写配置。

详细说明见：

```text
docs/SECURITY_AND_GIT.md
```

## 视觉参考

后续前端页面实现优先参考：

```text
docs/design/live_streaming_slicing_workflow_ui_16x9.png
```

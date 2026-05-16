# Live Streaming Slicing Workflow

直播切片工作流是一个面向 Windows 本地环境的直播长视频自动切片后台。首版 MVP 聚焦“本地视频任务处理链路”，先把项目骨架、任务状态、页面入口和可替换服务接口搭起来。

## 当前目标

本阶段先完成：

- FastAPI 后端可启动。
- HTML + CSS + JavaScript + Jinja2 后台页面可访问。
- SQLite 数据库连接模块与任务表草案。
- 任务列表、新建任务、任务详情、片段审核页面占位。
- 为后续接入 FFmpeg、转写服务、AI 分析服务和真实切片处理预留模块。

## 技术栈

- 后端：Python + FastAPI
- 前端：HTML + CSS + JavaScript + Jinja2
- 数据库：SQLite
- 视频处理：FFmpeg / FFprobe
- AI 分析：预留 OpenAI-compatible API 或本地大模型接口
- 语音转写：预留服务接口

## 安装环境

在项目根目录执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

如果 PowerShell 提示不能执行激活脚本，可以先执行：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## 启动项目

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload
```

启动后访问：

```text
http://127.0.0.1:8000
```

健康检查地址：

```text
http://127.0.0.1:8000/health
```

## 目录结构

```text
app/                 FastAPI 主应用
app/core/            配置
app/db/              SQLite 数据库连接
app/models/          任务与片段字段草案
app/routers/         页面路由与 API 路由
app/services/        存储、转写、AI 分析、切割服务接口
app/templates/       Jinja2 页面模板
app/static/          CSS 与 JavaScript
data/                本地数据库与轻量数据
tasks/               每条视频任务的独立工作目录
docs/                产品、架构、UI 与流程文档
scripts/             后续脚本工具
```

## 视觉参考

后续前端实现需要优先参考：

```text
docs/design/live_streaming_slicing_workflow_ui_16x9.png
```

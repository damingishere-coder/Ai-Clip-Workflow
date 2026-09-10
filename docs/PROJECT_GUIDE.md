# 牛马片场新手启动指南

这份文档面向第一次使用牛马片场、对代码和终端不熟悉的用户。请按顺序操作，不需要先理解全部技术细节。

适用版本：`2.3.0`

## 1. 项目能做什么

牛马片场运行在 Windows 本地，用来把直播录像、综艺访谈和其他长视频整理成可审核、可切片、可准备平台内容、可排期并发送的短视频。当前前台面向抖音；B站保留后端与历史兼容，前台和自动同步未启用。

```text
导入长视频
→ 提取音频并转写
→ AI 分析高光
→ 人工审核片段
→ 生成短视频
→ 准备标题、简介、话题和封面
→ 排期或立即发送
→ 保存成功、失败或人工复核记录
```

项目不会绕过二维码、短信、验证码、滑块、登录失效或平台风控。真实发送前必须人工核对内容与账号。

## 2. 已安装本机：直接使用

如果已有 RunDock 托管的牛马片场，直接打开 [本地工作台](http://127.0.0.1:8001)，通过原有 Web / Worker 记录启停。无需重新克隆、覆盖配置或再次运行启动脚本。当前版本与交付证据见 [项目进度](../PROJECT_STATUS.md)。

以下步骤用于一台尚未安装的新电脑。

## 3. 首次安装与配置

准备 Windows 10/11、Python 3.12+、Git、FFmpeg 和足够的素材空间；真实投稿还需要系统 Chrome。只有选择 Docker / Demo 时才需要 Docker Desktop。

在 PowerShell 中执行：

```powershell
git clone https://github.com/damingishere-coder/Ai-Clip-Workflow.git
cd Ai-Clip-Workflow
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\scripts\setup.ps1
```

`setup.ps1` 会保留已有 `.env`，首次运行才创建配置与 Token。不要用模板覆盖已配置的文件。随后在本机配置中检查素材、临时上传、转写、AI 和 Worker 参数；真实 Key、Cookie 与 Token 不上传仓库。

离线转写需要另外准备模型；NVIDIA GPU 环境见 [部署说明](DEPLOYMENT.md)。后续命令都在自己的仓库目录执行。

## 4. 日常推荐：Windows 原生运行

确认 8001 / 8765 没有被已有项目服务占用后启动：

```powershell
.\scripts\start_native.ps1
```

默认使用 E 盘素材存储。没有 E 盘时，指定自己的存储目录，例如：

```powershell
.\scripts\start_native.ps1 -StorageRoot D:\NiuMaData
```

选择其中一条启动命令执行即可。打开 [工作台](http://127.0.0.1:8001)，在系统页查看服务就绪状态；仅 `/health` 返回正常不能证明转写、AI 和账号已配置。

手动启动的服务可使用 `.\scripts\stop_native.ps1` 停止。通过 RunDock 托管的服务继续用原记录管理。

## 5. 隔离试用：Docker Demo

安装并启动 Docker Desktop，确保没有原生服务占用 8001，再执行：

```powershell
.\scripts\start.ps1 -Demo
```

Demo 使用独立数据库、虚构任务和手动导出草稿，发布调度关闭，不需要真实账号或 AI Key。停止使用 `.\scripts\stop.ps1 -Demo`。

正式 Docker 方式与开发模式见 [通用启动指南](PORTABLE_SETUP.md)。Docker 不是维护者本机当前日常服务的启动方式。

## 6. 第一次测试：只测生产链路

第一次使用不要直接点击真实发送。建议准备一条时间较短、无隐私、可重复测试的视频，然后按以下顺序检查：

1. 打开首页，确认页面正常加载。
2. 打开“新建任务”，上传测试视频。
3. 选择合适的转写和 AI 配置。
4. 等待音频提取、转写和 AI 分析完成。
5. 打开片段审核页，启用或修改至少一个候选。
6. 保存选择并生成短视频。
7. 确认生成内容进入发送中心。
8. 预览排期，确认时间按北京时间显示。

完成以上步骤，说明这条测试素材的本地生产链路可用；不代表所有素材或真实投稿都已验收。选择 Codex 或远程服务会产生真实模型请求，Demo 验收不会执行这些步骤。

## 7. AI 与转写配置

### Codex CLI（默认推荐）

已安装并登录 Codex CLI 时，系统可以把脱敏后的文字稿分析任务交给本机 `codex` 进程。默认命令、模型和超时由 `AI_CODEX_PATH`、`AI_CODEX_HOME`、`AI_CODEX_MODEL`、`AI_CODEX_TIMEOUT_SECONDS` 控制；不要把登录信息写入仓库，也不要为了本项目改变 Codex 的提供商或认证方式。

### 远程 AI

支持 OpenAI-compatible 接口和 DeepSeek。请在 `.env` 或系统设置页填写自己的地址、模型和 API Key。

### 本地 Ollama

使用本地 Ollama 时，先确认 Ollama 已在 Windows 中启动。Docker 容器通常通过：

```text
http://host.docker.internal:11434
```

访问 Windows 本机服务。

### 转写

支持火山引擎远程转写和本地 faster-whisper。没有 NVIDIA 显卡时，本地转写可能明显变慢。

## 8. 真实发布前检查

只有完成本地生产链路后，才开始真实平台灰度：

1. 确认 Scheduler 健康。
2. 确认 Windows Worker 正常。
3. 在账号管理中新增目标平台账号。
4. 在系统 Chrome 独立窗口中人工完成登录。
5. 只选择一条低风险测试视频。
6. 核对视频、标题、简介、话题、封面、账号、可见范围和北京时间。
7. 执行一次投稿并等待明确成功证据。

遇到验证码、登录失效、风控或结果不确定时，任务会进入 `NEED_REVIEW`。先到平台创作者中心人工核对，不要直接重复发送。

## 9. 常用测试命令

先安装开发依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

以下命令需在已激活该虚拟环境的终端运行，也可将 `python` 换为 `.\.venv\Scripts\python.exe`，将 `pytest` 换为 `.\.venv\Scripts\python.exe -m pytest`。

基础测试：

```powershell
python -m compileall app
python scripts/test_ai_json_validation.py
python scripts/test_mock_transcript_analysis.py
python scripts/test_transcript_markdown_format.py
```

完整测试：

```powershell
pytest -v
```

自动化测试应使用独立数据库和 Mock，不连接真实平台账号，不触发真实投稿。

## 10. 哪些文件不能上传

不要提交：

- `.env`
- 真实 API Key、Token、Cookie
- SQLite 数据库
- 原视频、音频、转写和切片
- 发布包、日志和失败截图
- Chrome Profile 和 storage state
- 用户名、手机号、平台账号和私人路径

详细说明见 [SECURITY.md](../SECURITY.md)。

## 11. 常见问题

### 页面打不开

确认服务已启动，并优先使用：

```text
http://127.0.0.1:8001
```

先在 RunDock 检查原 Web / Worker 记录；若手动运行原生服务，检查启动窗口的错误信息。只有使用 Docker 模式时才运行 `docker compose ps`。

### AI 提示缺少 Key

检查 `.env` 或系统设置页中对应的转写、分析和文案服务配置。不要把真实 Key 发到公开 Issue。

### Windows Worker 未连接

先不要点击“立即发送”。检查 Worker 地址、Token 和系统 Chrome；Docker 模式还需检查容器与本机网络桥接。开发和诊断方式见 [TECHNICAL_REFERENCE.md](TECHNICAL_REFERENCE.md)。

### 本地 Ollama 无法连接

确认 Windows 中 Ollama 已启动，并检查容器是否可以访问 `host.docker.internal:11434`。

### 转写很慢

本地 faster-whisper 在 CPU 模式下可能需要较长时间。可以改用远程转写，或降低测试视频时长。

## 12. 下一步阅读

- [项目首页](../README.md)
- [技术参考](TECHNICAL_REFERENCE.md)
- [路线图](../ROADMAP.md)
- [贡献指南](../CONTRIBUTING.md)
- [安全策略](../SECURITY.md)

# 牛马片场新手启动指南

这份文档面向第一次使用牛马片场、对代码和终端不熟悉的用户。请按顺序操作，不需要先理解全部技术细节。

适用版本：`2.2.0`

## 1. 项目能做什么

牛马片场运行在 Windows 本地，用来把直播录像、综艺访谈和其他长视频整理成可审核、可切片、可准备平台内容、可排期并发送到抖音或 B站的短视频。

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

## 2. 准备环境

推荐准备：

- Windows 10 或 Windows 11。
- Docker Desktop。
- 系统 Chrome。
- 足够的视频存储空间。
- 需要本地开发时，再安装 Python 3.12+ 和 FFmpeg。

先把项目克隆到你自己的目录，例如：

```powershell
git clone https://github.com/damingishere-coder/Ai-Clip-Workflow.git
cd Ai-Clip-Workflow
```

后续命令都在你实际的仓库目录中执行，不要照抄其他用户电脑的绝对路径。

## 3. 准备配置

复制环境变量模板：

```powershell
Copy-Item .env.example .env
```

打开 `.env`，重点检查：

- 视频和任务存储目录是否存在。
- 临时上传目录是否有足够空间。
- 需要使用的转写服务配置。
- 受控 Codex CLI、远程 AI 或本地 Ollama 配置。
- Windows Worker 地址和 Token。

真实 API Key 只能写入本机 `.env`，不要写进代码、README、Issue、截图或提交记录。

仓库中的 E 盘路径是历史默认值。你的电脑没有对应目录时，必须改为自己的路径。

## 4. 推荐方式：Docker Desktop

确保 Docker Desktop 已启动，然后在项目目录执行：

```powershell
docker compose up -d
```

查看容器状态：

```powershell
docker compose ps
```

浏览器打开：

```text
http://127.0.0.1:8001
```

健康检查：

```text
http://127.0.0.1:8001/health
```

停止项目：

```powershell
docker compose down
```

如果你的电脑已经安装项目配套的 Docker Watcher，它会在容器运行后准备 Windows Chrome Worker。外部用户没有安装 Watcher 时，可以使用仓库中的 Worker 启动脚本进行开发或诊断。

## 5. 备用方式：本地 Python

创建虚拟环境：

```powershell
python -m venv .venv
```

激活：

```powershell
.\.venv\Scripts\Activate.ps1
```

安装依赖：

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

如果 PowerShell 不允许运行激活脚本，可以为当前用户调整脚本策略：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

启动 Web 服务：

```powershell
uvicorn app.main:app --reload --port 8001
```

浏览器打开 `http://127.0.0.1:8001`。

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

完成以上步骤，说明本地生产链路基本可用。

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

检查：

```powershell
docker compose ps
```

### AI 提示缺少 Key

检查 `.env` 或系统设置页中对应的转写、分析和文案服务配置。不要把真实 Key 发到公开 Issue。

### Windows Worker 未连接

先不要点击“立即发送”。检查 Worker 地址、Token、系统 Chrome、Docker 与本机网络桥接。开发和诊断方式见 [TECHNICAL_REFERENCE.md](TECHNICAL_REFERENCE.md)。

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

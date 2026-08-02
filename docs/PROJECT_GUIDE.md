# 项目总览与新手启动说明

这份文档给不熟悉代码和终端的新手使用。你只需要按顺序做，不需要理解每一行命令背后的原理。

适用版本：牛马片场 `2.0.0`。

## 1. 项目是做什么的

项目中文名：牛马片场。

它的目标是把一条本地直播录像、综艺访谈或长视频素材，变成一组可审核、可切割、可准备文案和封面、可排期并发送到抖音或 B站的短视频内容。

当前 v2.0 完整流程：

```text
上传本地视频 / 选择 NAS 或本地已有视频
-> 创建独立任务目录
-> 提取音频
-> 火山引擎或 faster-whisper 转写
-> DeepSeek / OpenAI-compatible 或 Ollama 分析候选片段
-> 人工审核片段
-> 自动切割并准备标题、简介、话题和封面
-> 发送中心核对账号与内容
-> 立即发送或按北京时间排期
-> Windows Chrome Worker 投稿抖音 / B站
-> 保存成功、失败或人工复核记录
```

针对《康熙来了》类综艺，可以在新建任务或任务详情里选择“综艺笑点优先”。该模式先按重叠窗口找笑点，再补齐前后文，最后全局去重和评分；候选池默认 12 条，但只会默认启用最多 5 条 A 级内容，质量不足时不会凑数。现有直播和通用长视频继续使用“通用内容价值”模式。

### 1.1 现在已经能做什么

- 素材、音频、转写、AI 结果、切片和封面按任务保存到 E 盘存储目录。
- 全自动任务详情会每 3 秒更新进度与日志，失败后可以重试或继续。
- 片段审核保存后，可一键生成最新切片并同步到发送中心。
- 发送中心按任务分组管理抖音 / B站内容，支持内容补齐、封面、排期月历、跨午夜时间窗和续接最晚排期。
- 立即发送与定时发送共用同一套 Scheduler 和 Windows Worker，执行记录不会因为重新切片而被覆盖。

### 1.2 现在仍需要人工做什么

- 首次使用抖音或 B站账号时，在系统 Chrome 独立窗口完成登录、二维码、短信或平台要求的验证。
- 真实发送前逐条核对视频、标题、简介、话题、封面、账号、可见范围和北京时间。
- 遇到验证码、滑块、登录失效、平台风控或发布结果不确定时，到平台创作者中心人工确认。
- 项目不会绕过平台限制，也不会在结果不确定时自动重复上传。

## 2. 当前项目目录

```text
C:\Users\10578\Documents\New project 2
```

以后所有命令都默认在这个目录里执行。

## 3. 推荐方式：Docker 一键启动

Docker 的好处是：不用每次手动激活 `.venv`，端口映射清楚，关闭也方便。

第一次启动前，先确认 Docker Desktop 已经打开。

日常不需要打开 PowerShell，也不需要输入命令：

1. 打开 Docker Desktop。
2. 在 Containers 中找到并运行 `niuma-studio`。
3. 等待发送中心的“Windows Worker”显示“正常”。

项目已经安装 `NiuMa Studio Docker Watcher` 后台观察器。它只等待当前项目的 Docker 容器；容器运行后自动启动 Windows Chrome Worker，项目停止 15 秒后自动关闭 Worker。旧启动脚本继续保留给开发助手诊断，不作为日常操作。

看到服务启动后，在浏览器打开：

```text
http://127.0.0.1:8001
```

健康检查地址：

```text
http://127.0.0.1:8001/health
```

查看端口和容器状态：

```powershell
docker compose ps
```

停止项目：

```powershell
docker compose down
```

Docker 版会继续使用：

- 项目里的 `data/workflow.sqlite3` 保存数据库。
- E 盘 `E:\直播间切片工作流存储` 保存上传视频、音频、转写、AI 分析和切片结果。这个名字是历史存储目录，本次品牌更新先不改它，避免影响已有任务和视频。
- 项目根目录 `.env` 保存真实 API Key，Docker 启动时会自动读取。

## 4. 备用方式：本地虚拟环境启动

打开 PowerShell 后，先进入项目目录：

```powershell
cd "C:\Users\10578\Documents\New project 2"
```

如果还没有虚拟环境，执行：

```powershell
py -3.12 -m venv .venv
```

激活虚拟环境：

```powershell
.\.venv\Scripts\Activate.ps1
```

安装依赖：

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

如果 PowerShell 提示不允许运行脚本，先执行一次：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

然后重新执行激活命令。

## 5. 配置 API Key

真实 API Key 不要写进代码，也不要写进文档。

第一次使用时，把 `.env.example` 复制成 `.env`，然后只在 `.env` 里填写真实密钥。

```powershell
Copy-Item .env.example .env
```

`.env` 已经被 Git 忽略，不会上传。

也可以启动项目后，在系统状态页里的“三类 AI 接口配置”直接填写。页面保存时会写入 `.env`，并保留 `.env` 里其他无关配置。

## 6. 本地虚拟环境启动项目

每次启动项目，一般执行这两行：

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --port 8001
```

看到服务启动后，在浏览器打开：

```text
http://127.0.0.1:8001
```

健康检查地址：

```text
http://127.0.0.1:8001/health
```

## 7. 页面怎么测试

启动项目后，按这个顺序检查：

1. 打开首页，确认左侧显示 `v2.0 本地高光生产版`。
2. 打开“新建任务”，上传一个短测试视频，保持“全自动流程”开启。
3. 进入任务详情页，确认状态概览和运行日志每 3 秒局部更新，页面不会自动跳回顶部。
4. 转写与 AI 分析完成后，打开片段审核页，勾选或修改候选片段。
5. 点击“保存并同步发送中心”，确认需要时会生成最新切片，并自动进入当前任务的内容准备区。
6. 在发送中心核对抖音 / B站的视频、标题、简介、话题和封面。
7. 只使用测试任务预览排期，确认月历日期详情和任务清单按北京时间从早到晚排列。
8. 如果要测试真实发布，必须先确认 Windows Worker 正常、平台账号已登录，并只发送一条低风险测试内容。

> 只检查生产链路时，不要点击“立即发送”。排期预览不会触发真实投稿，确认排期后任务会等待调度器到点执行。

## 8. 命令行测试

不需要真实 API Key 的基础测试：

```powershell
python -m compileall app
python scripts/test_ai_json_validation.py
python scripts/test_mock_transcript_analysis.py
python scripts/test_transcript_markdown_format.py
```

需要真实远程 API Key 的测试：

```powershell
python scripts/test_remote_ai_connection.py
```

需要本地 Ollama 服务的测试：

```powershell
python scripts/test_local_ai_connection.py
```

## 9. 哪些文件不要上传

不要上传这些内容：

- `.env`：真实 API Key 和本地配置。
- `data/` 里的真实数据库。
- `tasks/` 里的原视频、音频、转写、切片结果。
- `.venv/` 虚拟环境。
- 大视频、大音频和本地生成日志。

Git 当前只保留代码、文档、模板和必要的 `.gitkeep` 占位文件。

## 10. 常见问题

如果打开 `http://127.0.0.1:8000` 页面不对，优先使用：

```text
http://127.0.0.1:8001
```

如果 AI 提示缺少 Key，先到系统状态页检查对应的三类接口：音频转写看火山引擎 Key，文字稿分析看 `AI_ANALYSIS_REMOTE_API_KEY`，发送中心文案看 `AI_PUBLISH_REMOTE_API_KEY`。

如果发送中心提示“Windows Worker 未连接”，先不要点击“立即发送”。刚运行 Docker 项目时先等待十几秒，再点击“重新检测”。如果持续未连接，在 Docker Desktop 中停止 `niuma-studio`，等待 15 秒后重新运行；不需要输入命令。仍未恢复时，把发送中心提示交给开发助手检查 `data/logs/docker_publish_worker_watcher.log` 和 `publish_worker_8765.err.log`。

如果转写速度很慢，可能是没有使用 NVIDIA 显卡。可以在 `.env` 中把转写配置改成 CPU 模式，但速度会慢一些。

如果 Docker 启动后本地 Ollama 不通，确认 Ollama 已经在 Windows 里启动。Docker 容器内会通过 `host.docker.internal:11434` 访问 Windows 本机的 Ollama。

如果 Docker Desktop 里看到端口 `8001:8001`，说明浏览器应该打开 `http://127.0.0.1:8001`。

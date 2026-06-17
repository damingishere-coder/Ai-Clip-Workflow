# 项目总览与新手启动说明

这份文档给不熟悉代码和终端的新手使用。你只需要按顺序做，不需要理解每一行命令背后的原理。

> 2026-06-17 更新：Windows 本地启动请优先看 [WINDOWS_SETUP.md](WINDOWS_SETUP.md)。本文仍作为项目总览保留，后续会继续逐步合并到新的 Windows 文档体系。

## 1. 项目是做什么的

项目中文名：牛马片场。

它的目标是把一条本地直播录像、综艺访谈或长视频素材变成一组可审核、可切割、可继续加字幕和发送的短视频候选片段。

当前 MVP 主要流程：

```text
上传本地视频
-> 创建任务
-> 提取音频
-> 本地语音转写
-> AI 分析候选片段
-> 人工审核片段
-> 自动切割输出短视频
```

## 2. 当前项目目录

```text
C:\Users\10578\Documents\New project 2
```

以后所有命令都默认在这个目录里执行。

## 3. 推荐方式：Docker 一键启动

Docker 的好处是：不用每次手动激活 `.venv`，端口映射清楚，关闭也方便。

第一次启动前，先确认 Docker Desktop 已经打开。

然后打开 PowerShell，进入项目目录：

```powershell
cd "C:\Users\10578\Documents\New project 2"
```

启动项目：

```powershell
docker compose up --build
```

如果你要使用发送中心自动发送，推荐改用这一条。它会同时启动 Windows opencli 辅助服务和 Docker 主页面：

```powershell
.\scripts\start_docker_opencli.ps1
```

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

1. 打开首页，确认工作台能显示。
2. 打开“新建任务”，上传一个本地视频。
3. 进入任务详情页，确认任务信息和处理按钮能显示。
4. 生成转写后，确认详情页能看到转写预览。
5. 点击远程 AI 或本地 AI 分析，生成候选片段。
6. 打开片段审核页，勾选或修改候选片段。
7. 触发切割，确认输出片段记录能展示。

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

如果发送中心提示“还没有连接到 Windows opencli 辅助服务”，先不要点“开始发送全部”。日常仍然只使用 Docker 主页面 `http://127.0.0.1:8001`，按下面顺序处理：

```powershell
cd "C:\Users\10578\Documents\New project 2"
.\scripts\start_docker_opencli.ps1
```

脚本会自动检查 Windows opencli、启动 opencli 辅助服务、刷新 Docker，并打开 `http://127.0.0.1:8001/publish`。页面打开后按 `Ctrl + F5` 强制刷新。如果脚本提示“没有检测到 opencli”，再执行：

```powershell
where opencli
```

如果 `where opencli` 没有显示路径，说明 opencli 还没装好或没有加入 Windows PATH；如果能显示路径但页面仍报错，把页面红色提示和 `where opencli` 输出发给开发助手继续排查。

如果转写速度很慢，可能是没有使用 NVIDIA 显卡。可以在 `.env` 中把转写配置改成 CPU 模式，但速度会慢一些。

如果 Docker 启动后本地 Ollama 不通，确认 Ollama 已经在 Windows 里启动。Docker 容器内会通过 `host.docker.internal:11434` 访问 Windows 本机的 Ollama。

如果 Docker Desktop 里看到端口 `8001:8001`，说明浏览器应该打开 `http://127.0.0.1:8001`。

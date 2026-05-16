# 项目总览与新手启动说明

这份文档给不熟悉代码和终端的新手使用。你只需要按顺序做，不需要理解每一行命令背后的原理。

## 1. 项目是做什么的

项目中文名：直播切片工作流。

它的目标是把一条本地直播长视频变成一组可审核、可切割的短视频候选片段。

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

## 3. 第一次准备环境

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

## 4. 配置 API Key

真实 API Key 不要写进代码，也不要写进文档。

第一次使用时，把 `.env.example` 复制成 `.env`，然后只在 `.env` 里填写真实密钥。

```powershell
Copy-Item .env.example .env
```

`.env` 已经被 Git 忽略，不会上传。

也可以启动项目后，在系统状态页里打开 AI 配置弹窗填写。页面保存时会写入 `.env`。

## 5. 启动项目

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

## 6. 页面怎么测试

启动项目后，按这个顺序检查：

1. 打开首页，确认工作台能显示。
2. 打开“新建任务”，上传一个本地视频。
3. 进入任务详情页，确认任务信息和处理按钮能显示。
4. 生成转写后，确认详情页能看到转写预览。
5. 点击远程 AI 或本地 AI 分析，生成候选片段。
6. 打开片段审核页，勾选或修改候选片段。
7. 触发切割，确认输出片段记录能展示。

## 7. 命令行测试

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

## 8. 哪些文件不要上传

不要上传这些内容：

- `.env`：真实 API Key 和本地配置。
- `data/` 里的真实数据库。
- `tasks/` 里的原视频、音频、转写、切片结果。
- `.venv/` 虚拟环境。
- 大视频、大音频和本地生成日志。

Git 当前只保留代码、文档、模板和必要的 `.gitkeep` 占位文件。

## 9. 常见问题

如果打开 `http://127.0.0.1:8000` 页面不对，优先使用：

```text
http://127.0.0.1:8001
```

如果 AI 提示缺少 Key，检查 `.env` 里是否已经填写远程 API Key，或者在系统状态页重新保存 AI 配置。

如果转写速度很慢，可能是没有使用 NVIDIA 显卡。可以在 `.env` 中把转写配置改成 CPU 模式，但速度会慢一些。

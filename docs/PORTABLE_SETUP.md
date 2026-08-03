# 通用启动、环境检查与 Demo 指南

这份文档说明 P0 工程化后的运行方式。所有命令都在仓库根目录的 PowerShell 中执行。

## 1. 首次初始化

```powershell
.\scripts\setup.ps1
```

脚本会：

- 从 `.env.example` 创建本地 `.env`
- 保留已有 `.env`，不会覆盖真实 API Key
- 自动生成 `LOCAL_ADMIN_TOKEN` 和 `PUBLISH_WORKER_TOKEN`
- 创建 `data/`、`workspace/tasks/`、临时上传与发布包目录
- 把本地 Python 所需的绝对存储路径写入 `.env`

需要重置模板时使用：

```powershell
.\scripts\setup.ps1 -Force
```

原 `.env` 会先备份，不会直接丢失。

## 2. 环境检查

```powershell
.\scripts\doctor.ps1
```

检查内容包括：

- PowerShell 版本
- `.env`
- 视频目录写权限
- Docker CLI、Docker Engine 与 Compose
- Compose 配置是否合法
- 宿主机 FFmpeg
- Google Chrome
- 8001 端口

Chrome 和宿主机 FFmpeg 在普通 Docker 工作台模式下只是提醒。准备真实投稿时执行：

```powershell
.\scripts\doctor.ps1 -RequirePublisher
```

## 3. 正式工作台

```powershell
.\scripts\start.ps1
```

默认行为：

- 启动正式 `docker-compose.yml`
- 不启用代码热重载
- 不自动连接真实平台 Worker
- 等待 `/health` 正常后打开浏览器

停止：

```powershell
.\scripts\stop.ps1
```

## 4. 隔离 Demo

```powershell
.\scripts\start.ps1 -Demo
```

Demo 使用：

```text
demo-data/
workspace/demo/
```

它不会使用正式的 `data/`、`workspace/tasks/` 或平台账号。首次启动会生成：

- 3 条虚构任务
- 6 条 AI 候选片段
- 3 条演示切片
- 6 条安全的 `manual_export` 发布草稿

Demo 调度器默认关闭，账号没有真实登录态。

恢复初始 Demo：

```powershell
.\scripts\start.ps1 -Demo -ResetDemo
```

停止并删除 Demo 数据：

```powershell
.\scripts\stop.ps1 -Demo -RemoveDemoData
```

## 5. 开发热重载

```powershell
.\scripts\start.ps1 -Development
```

等价于叠加：

```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

只有开发模式会挂载 `app/`、`prompts/` 并启用 `uvicorn --reload`。

## 6. 真实发布

```powershell
.\scripts\start.ps1 -WithPublisher
```

此模式复用现有 `start_niuma_studio.ps1` 和 `start_publish_worker.ps1`，需要：

- Windows
- Google Chrome
- 平台账号人工登录
- 二维码、短信、验证码和风控由用户处理

首次真实发布必须使用一条低风险测试视频；结果不确定时先到平台创作者中心核对，不能直接重复投稿。

## 7. 自定义视频目录

`.env` 中修改：

```env
NIUMA_STORAGE_PATH=D:/NiuMaStudio/tasks
```

也可以使用仓库相对路径：

```env
NIUMA_STORAGE_PATH=./workspace/tasks
```

修改后重新运行：

```powershell
.\scripts\setup.ps1
.\scripts\doctor.ps1
```

正式 Compose 会将该目录挂载到容器内的 `/workspace/tasks`。

## 8. Windows 一键验收

在发布版本或更换电脑后，建议执行完整的隔离验收：

```powershell
.\scripts\acceptance.ps1
```

脚本会：

1. 保留现有 `.env` 并完成目录初始化。
2. 运行环境体检。
3. 构建并启动隔离 Demo。
4. 检查容器是否为 `healthy`。
5. 检查工作台、任务列表、片段总览和发送中心。
6. 验证 Demo 数据数量。
7. 验收结束后自动停止 Demo。

需要保留页面继续人工检查：

```powershell
.\scripts\acceptance.ps1 -KeepRunning
```

已经构建过镜像时可以跳过重新构建：

```powershell
.\scripts\acceptance.ps1 -NoBuild -KeepRunning
```

验收脚本不连接真实平台账号，也不会测试真实投稿。

## 9. 依赖安装

普通本地 Python 环境：

```powershell
pip install -r requirements.txt
pip check
```

开发环境：

```powershell
pip install -r requirements-dev.txt
pip check
```

依赖文件的职责、升级流程和重点风险见 [依赖维护策略](DEPENDENCY_POLICY.md)。正式发布前使用 [Release 检查清单](RELEASE_CHECKLIST.md)。

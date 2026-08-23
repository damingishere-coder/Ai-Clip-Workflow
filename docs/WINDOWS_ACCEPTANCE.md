# Windows 实机验收与发布证据

本指南用于在真实的 Windows 10/11 + Docker Desktop 电脑上验证牛马片场 v2.1.0。

GitHub Actions 中的 `Windows host smoke test` 只能验证 PowerShell、Windows 路径、原生 Python Demo、页面访问和备份命令。GitHub 托管的 Windows Runner 不是普通用户的 Windows 10/11 + Docker Desktop 环境，因此不能替代本指南中的实机验收。

## 验收目标

一次正式验收需要证明：

- `setup.ps1` 不会覆盖已有 `.env`。
- `doctor.ps1` 能识别 Docker Desktop、Compose、存储权限、端口、FFmpeg 和 Chrome。
- 隔离 Demo 能启动并达到 `healthy`。
- 工作台、任务列表、片段总览和发送中心都能打开。
- Demo 中有 3 条任务、6 条候选片段和 6 条 `manual_export` 发布草稿。
- Demo 能正常停止。
- `.env`、正式 SQLite 和正式任务目录在验收前后保持不变。
- 验收报告记录 Windows、Docker Desktop、Docker Engine、Compose、Chrome 和 Git commit。

本验收不会连接真实抖音或 B站账号，也不会验证真实投稿。真实发布灰度验证由 Issue #25 单独跟踪。

## 一、验收前准备

在仓库根目录打开 PowerShell：

```powershell
git switch master
git pull --ff-only
```

先创建升级回滚点：

```powershell
.\scripts\pre_upgrade.ps1
```

确认 Docker Desktop 已启动，并关闭正在运行的牛马片场正式服务：

```powershell
.\scripts\stop.ps1
```

如果正式服务本来没有启动，`stop.ps1` 可能提示没有容器；只要端口 `8001` 没有被其他程序占用即可。

如果本机安装了 `NiuMa Studio Docker Watcher` 计划任务，它会自动重新启动正式容器。验收期间可临时停止并禁用，完成后必须恢复：

```powershell
Stop-ScheduledTask -TaskName "NiuMa Studio Docker Watcher"
Disable-ScheduledTask -TaskName "NiuMa Studio Docker Watcher"
.\scripts\stop.ps1

# 验收和发布门禁完成后恢复
Enable-ScheduledTask -TaskName "NiuMa Studio Docker Watcher"
Start-ScheduledTask -TaskName "NiuMa Studio Docker Watcher"
```

这里只是暂停自动拉起，不要删除计划任务，也不要删除 Cookie 或浏览器 Profile。

## 二、运行完整验收

推荐执行：

```powershell
.\scripts\acceptance.ps1
```

脚本会自动：

1. 运行两次 `setup.ps1`，验证 `.env` 不被重置。
2. 运行 `doctor.ps1`。
3. 对正式 `.env`、SQLite 和任务目录生成验收前指纹。
4. 构建并启动隔离 Demo。
5. 检查健康状态和主要页面。
6. 验证 Demo 数据数量。
7. 停止 Demo。
8. 再次计算正式数据指纹并对比。
9. 输出 Markdown 和 JSON 验收报告。

默认报告位置：

```text
acceptance-results/
├── latest.md
├── latest.json
└── windows-YYYYMMDD-HHMMSS/
    ├── report.md
    ├── report.json
    ├── setup-first.log
    ├── setup-second.log
    ├── doctor.log
    ├── start-demo.log
    └── stop-demo.log
```

`acceptance-results/` 已加入 `.gitignore`，不会提交到 GitHub。

### 验收完成后保留 Demo

```powershell
.\scripts\acceptance.ps1 -KeepRunning
```

`-KeepRunning` 不会跳过停止验证。脚本会先停止 Demo、确认正式数据没有变化，然后再用现有镜像重新启动 Demo。

### 使用现有镜像

已经完成镜像构建时，可以减少等待：

```powershell
.\scripts\acceptance.ps1 -NoBuild
```

正式 Release 前建议至少有一次不带 `-NoBuild` 的完整验收，以证明当前提交可以从头构建。

### Docker Hub 或 Debian 软件源不可达

默认构建仍使用 Docker Hub 和 Debian 官方源。如果当前网络无法访问它们，可只在本次 PowerShell 会话中指定可达的等价镜像，然后继续运行完整构建：

```powershell
$env:PYTHON_BASE_IMAGE = "m.daocloud.io/docker.io/library/python:3.12-slim-bookworm"
$env:DEBIAN_MIRROR = "https://mirrors.aliyun.com/debian"
$env:DEBIAN_SECURITY_MIRROR = "https://mirrors.aliyun.com/debian-security"
.\scripts\acceptance.ps1
```

这些变量不会写入 `.env`。关闭当前 PowerShell 窗口后会自动失效；这不是 `-NoBuild`，脚本仍会完整构建镜像并执行全部验收项。

### 自定义报告目录

```powershell
.\scripts\acceptance.ps1 -ReportDirectory D:\NiuMaAcceptance
```

### 跳过任务目录指纹

```powershell
.\scripts\acceptance.ps1 -SkipStorageSnapshot
```

这个参数只适合任务目录非常大、临时排错或非正式验证。使用它生成的报告不能通过 `release_gate.ps1`，不能作为正式 Release 证据。

## 三、运行发布门禁

验收通过后执行：

```powershell
.\scripts\release_gate.ps1
```

发布门禁要求：

- `acceptance-results/latest.json` 的结果为 `passed`。
- 系统是 Windows 10 或 Windows 11。
- 报告记录了 Docker Desktop、Docker Engine 和 Compose 版本。
- 3 / 6 / 6 Demo 数据数量正确。
- setup、页面、容器、停止流程和三类正式数据保护全部为 `PASS`。
- 当前分支为 `master`。
- 当前 Git commit 与验收报告一致。
- Git 工作区干净。
- 应用、README 和 Changelog 都是 `2.1.0`。

通过后会显示：

```text
=== v2.1.0 发布门禁通过 ===
```

## 四、提交 Issue #23 验收证据

打开 `acceptance-results/latest.md`，先人工检查内容。

报告设计上不会包含：

- `.env` 内容
- API Key 或 Token
- Cookie 或 storage state
- 浏览器 Profile
- 用户名和完整项目路径
- 真实视频和数据库内容

但上传前仍应检查错误消息和附加日志。推荐只把 `latest.md` 的正文粘贴到 Issue #23，不要上传 `.env`、SQLite、完整日志或整个 `acceptance-results` 目录。

Issue #23 只有在以下条件同时满足后才能关闭：

- 本机 `acceptance.ps1` 通过。
- `release_gate.ps1` 通过。
- 报告对应当前 `master`。
- 报告中的 Windows、Docker Desktop 和日期明确。

## 五、失败时怎么处理

验收失败时，脚本仍会生成报告，并尽量保存：

```text
doctor.log
start-demo.log
docker-demo-failure.log
```

常见处理：

### Docker Desktop 未启动

```powershell
.\scripts\doctor.ps1
```

确认 `Docker Engine` 和 `Docker Compose` 均为 `OK`。

### 端口 8001 被占用

```powershell
Get-NetTCPConnection -LocalPort 8001 -State Listen
```

关闭占用端口的程序后重新运行验收。

### 正式数据库或任务目录发生变化

不要继续发布。先确认：

- 是否还有正式工作流任务在后台运行。
- 是否有其他程序同时修改视频目录。
- Demo Compose 是否仍正确挂载 `demo-data/` 和 `workspace/demo/`。

必要时使用第三轮提供的备份恢复能力回滚。

### 报告提交不一致

如果验收后又执行了 `git pull`、合并或修改代码，发布门禁会拒绝旧报告。必须重新运行：

```powershell
.\scripts\acceptance.ps1
.\scripts\release_gate.ps1
```

## 六、Release 边界

Windows 实机验收通过后，可以发布 v2.1.0 的基础工作台能力。

Release 中仍需明确：

- 当前是 Windows 本地单用户工具。
- Demo 不连接真实账号。
- 抖音和 B站真实发布需要逐账号灰度验证。
- 登录失效、验证码、风控或结果不确定会进入人工复核。
- 不承诺所有账号和所有平台页面版本都已经验证。

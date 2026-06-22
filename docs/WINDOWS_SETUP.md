# Windows 启动与 opencli 辅助服务

这份文档是 Windows 用户的快速入口。更完整的新手说明仍然看 `docs/PROJECT_GUIDE.md`。

## Docker 主页面

项目默认使用 Docker 主页面：

```text
http://127.0.0.1:8001
```

发送中心也继续使用：

```text
http://127.0.0.1:8001/publish
```

不要为了 opencli 再切到 `8002` 之类的第二个网页。

## 安装 opencli 辅助服务自动启动

在项目目录执行：

```powershell
.\scripts\install_opencli_host_bridge_task.ps1
```

它会创建当前 Windows 用户的计划任务 `NiuMa Studio OpenCLI Host Bridge`，并立刻启动一次辅助服务。

验证地址：

```text
http://127.0.0.1:8765/health
```

正常情况会看到 `opencli_available` 为 `true`。

## 启动 Docker + opencli

在项目目录执行：

```powershell
.\scripts\start_docker_opencli.ps1
```

它会先确认 Windows opencli 辅助服务，再刷新 Docker，并打开发送中心。

## 为什么不把 opencli 放进 Docker

opencli 自动投稿依赖 Windows 上已经登录的 Chrome 和 OpenCLI 扩展。Docker 容器是隔离的 Linux 环境，不能可靠复用 Windows 桌面的登录态、扩展和人工确认窗口。

所以正确结构是：

```text
Docker 容器：运行 NiuMa Studio 后台和 8001 页面
Windows 主机：运行 opencli 辅助服务并控制已登录的 Chrome
```

## 取消自动启动

如果以后不想让辅助服务随 Windows 登录启动，执行：

```powershell
.\scripts\uninstall_opencli_host_bridge_task.ps1
```

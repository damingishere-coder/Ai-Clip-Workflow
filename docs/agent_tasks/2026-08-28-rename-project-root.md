# NiuMa Studio 本地根目录安全迁移任务

## 背景

当前正式本地项目根目录为 `C:\Users\10578\Documents\New project 2`，GitHub 仓库名已经是 `Ai-Clip-Workflow`。本任务把本地根目录改为 `C:\Users\10578\Documents\Ai-Clip-Workflow`，同时保持 Git、SQLite、E 盘素材、Chrome 登录资料和 Alter 托管关系连续可用。

## 目标

- 只迁移本项目根目录，不改变 GitHub 仓库地址或应用公开接口。
- 保留 Alter 中 `Niuma-Studio` 与 `Niuma-Publish-Worker` 的原注册 ID、启用状态和自动重启配置。
- 修复 Git linked worktree、Windows 虚拟环境、SQLite 内旧绝对路径和禁用的 Docker Watcher 路径。
- 完成健康检查、路径测试、Git 提交、推送和 PR。

## 允许修改范围

- 本项目根目录名称及其内部可重建的 `.venv`。
- SQLite 中以旧项目根目录为前缀的已确认路径字段。
- 两个固定 Alter 注册项的 `cwd`、脚本和参数。
- `NiuMa Studio Docker Watcher` 的脚本路径与工作目录，且保持禁用。
- Documents 下明确属于 NiuMa 的启动、停止批处理中的项目根路径。
- 当前路径说明、部署命令、开发日志和下一步文档。

## 禁止修改范围

- 不修改或删除 `.env`、Cookie、Chrome 登录目录、E 盘原片、发布包和外部唯一素材。
- 不停止或更新其他 Alter 项；若用户主动关闭了整个 Alter，仅为完成 API 配置迁移启动守护进程，并确保其他项目继续保持停止。
- 不 reset、stash、清理或覆盖任何 linked worktree 的未提交修改。
- 不执行真实抖音/B站投稿，不自动合并 PR，不 force push。

## 已确定实现要求

1. 停机前确认没有运行中的工作流、`PUBLISHING` 或未来 20 分钟排期。
2. 备份 SQLite、Alter 持久化状态和 Git worktree 元数据，并校验数据库。
3. 仅按固定 ID 停止、更新和启动两个 NiuMa Alter 项。
4. 从父目录执行一次精确重命名，并运行 `git worktree repair`。
5. 使用 `C:\Python312\python.exe` 在新路径重建 `.venv`。
6. SQLite 路径迁移只替换完全匹配的旧根目录前缀，不改 E 盘路径。
7. 任一步失败时恢复旧目录名、数据库、虚拟环境、Git worktree 和 Alter 配置。

## 验收标准

- 旧目录不存在，新目录存在，所有 linked worktree 可访问且原未提交修改仍在。
- Git `origin` 仍为 `https://github.com/damingishere-coder/Ai-Clip-Workflow.git`。
- 两个 Alter 项保留原 ID，均为 `running`、`enabled=true`、`autorestart=true`，路径全部使用新根目录。
- 8001 `/health`、8001 Scheduler 健康和 8765 Worker 健康通过，且无进程继续引用旧路径。
- SQLite 完整性、外键和旧路径残留检查通过；E 盘目录与 Chrome Profile 原样保留。
- Docker Watcher 的动作路径使用新目录并继续保持禁用。

## 测试命令

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_native_scripts.py tests/test_publish_worker_client.py tests/test_schema_migration_ledger.py
.\.venv\Scripts\python.exe -m ruff check .
.\scripts\doctor.ps1
git diff --check
```

## 返回格式

- 报告目录迁移、Alter 两个固定 ID、端口/PID/健康、数据库与 worktree 验证结果。
- 报告修改文件、测试命令与结果、提交哈希、分支、推送和 PR 链接。
- 若发生回滚或阻塞，给出准确失败步骤与已恢复状态。

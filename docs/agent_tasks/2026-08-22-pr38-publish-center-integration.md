# PR #38 与发送中心新版安全整合任务

## 背景

PR #39 已合并到远端 `master`，但本机仍运行 `feature/task-defaults-and-safe-startup`（PR #38）。该分支包含 4 个已提交功能提交，以及尚未提交的 Windows 原生启动脚本、对应测试和文档。当前 8001 页面仍是旧版，没有“全选本任务”，并仍显示 B 站切换入口。

## 目标

1. 完整保留当前分支的任务默认值、安全启停和 Windows 原生运行能力。
2. 将 `origin/master` 中 PR #39 的发送中心账号、全选、抖音文案和单平台前台能力整合到 PR #38。
3. 解决文档冲突，保证新版发送中心说明与原生日常运行说明同时保留。
4. 通过测试后安全重启本机 Web 服务，并验证“全选本任务”可选择当前任务全部可操作视频后进入排期。

## 允许修改范围

- 当前未提交的 `scripts/run_native.ps1`、`scripts/start_native.ps1`、`scripts/stop_native.ps1`、`tests/test_native_scripts.py` 及其已有文档说明。
- 合并 `origin/master` 自动带入的文件。
- 仅用于解决合并冲突和同步本次进度的 `DEVELOPMENT_LOG.md`、`NEXT_STEPS.md`、`README.md`、`docs/UI_REFERENCE.md`。
- 本任务文件。

## 禁止修改范围

- 不删除或覆盖 `.env`、SQLite、日志、浏览器 Profile、Cookie、视频、E 盘任务目录或发布历史。
- 不删除分支，不 force push，不重写 Git 历史，不自动合并 PR #38。
- 不修改 GitHub 权限，不触发真实投稿，不绕过账号登录、验证码或平台风控。
- 不使用 `git reset --hard`、`git checkout --`、`docker compose down --volumes` 或任何 volume prune。

## 已确定实现要求

- 现有原生启动改动先独立测试并提交，防止合并覆盖。
- 合并冲突只人工处理已确认的文档文件；`2026-08-22` 发送中心说明置顶，同时保留 `2026-08-21` 任务默认值与原生运行说明。
- README 同时说明原生模式为日常入口、Docker 为回退，以及发送中心前台和自动同步只启用抖音、B 站后端兼容保留。
- 重启前确认调度器 `publishing_count=0`、`scanning=false`；重启不停止或清理 Worker 登录数据。
- 页面验证只检查 DOM、勾选和排期入口，不保存排期、不点击立即发送。

## 验收标准

- 当前分支完整包含 PR #38 与 PR #39，两类功能均无丢失。
- 工作树没有冲突标记或范围外修改，原生脚本和测试已纳入 Git。
- Ruff、Python 编译、JavaScript 语法、原生脚本测试、发送中心专项测试和完整 pytest 全部通过。
- 本机 `/health` 正常，Scheduler 正常且 Worker 可用。
- `/publish` 不再显示 B 站切换卡；每个原始任务标题处可见“全选本任务”。
- 勾选后只选中该任务内全部可操作视频，显示批量栏和“设置排期”；取消后恢复未选择状态，不写入排期。

## 测试命令

```powershell
& '.\.venv\Scripts\python.exe' -m ruff check app tests
& '.\.venv\Scripts\python.exe' -m compileall -q app tests
node --check app/static/js/publish-center.js
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_native_scripts.py tests/test_publish_copy_rules.py tests/test_publish_center_browser.py tests/test_auto_pipeline.py
& '.\.venv\Scripts\python.exe' -m pytest -q
git diff --check
```

## 返回格式

Luna Operator 只返回：执行的原始命令、每条退出码、通过/失败/跳过数量、失败测试完整名称与最小相关堆栈，以及是否产生范围外 tracked 修改；不得修改生产代码、测试或文档。

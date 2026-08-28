# v2.2.0 Release 检查清单

本清单用于发布牛马片场正式版本。只有代码检查、Windows 实机验收、备份保护和文档核对均通过后，才创建 Git Tag 与 GitHub Release。

## 1. 自动化检查

- [ ] `master` 最新 CI 为绿色
- [ ] Python 编译、Ruff、JavaScript、PowerShell 和 pytest 通过
- [ ] 三套 Docker Compose 配置通过
- [ ] Demo 建库检查通过
- [ ] 备份、恢复、回滚和损坏包保护测试通过
- [ ] Windows host smoke test 通过并生成日志 Artifact
- [ ] 最终 Docker 镜像能启动并通过 `/health`
- [ ] Docker 镜像中的工作台、任务列表、片段总览、发送中心和内容复盘返回 200
- [ ] `/api/system/readiness?deep=1` 不返回 `not_ready`
- [ ] `pip check` 无依赖冲突
- [ ] 敏感文件、备份 ZIP 和本地验收报告检查无异常

> [!IMPORTANT]
> GitHub 的 `windows-latest` 是云端 Windows 主机冒烟，只验证 PowerShell、路径、原生 Demo、页面和备份。它不是普通用户的 Windows 10/11 + Docker Desktop，不能替代第 2 节的实机报告。

## 2. Windows 10/11 + Docker Desktop 实机验收

先同步并创建升级回滚点：

```powershell
git switch master
git pull --ff-only
.\scripts\pre_upgrade.ps1
```

执行完整验收：

```powershell
.\scripts\acceptance.ps1
```

确认：

- [ ] `acceptance-results/latest.json` 的 `result` 为 `passed`
- [ ] 报告记录 Windows 10/11 版本、Build 和验收时间
- [ ] 报告记录 Docker Desktop、Docker Engine 和 Docker Compose 版本
- [ ] `setup.ps1` 保留已有 `.env`
- [ ] 连续运行两次 `setup.ps1` 不会重置配置
- [ ] `doctor.ps1` 输出无阻塞项
- [ ] Demo 容器状态为 `healthy`
- [ ] 工作台、任务列表、片段总览和发送中心均返回 200
- [ ] 内容复盘页面在桌面与 390px 宽度下无整页横向溢出
- [ ] Demo 出现 3 条任务、6 条候选片段、6 条 `manual_export` 草稿
- [ ] Demo 能正常停止
- [ ] `.env` 在验收前后哈希一致
- [ ] 正式 SQLite 在验收前后状态一致
- [ ] 正式任务目录在验收前后文件数量、总大小和元数据指纹一致
- [ ] 报告对应当前 `master` Git commit

正式验收不能使用 `-SkipStorageSnapshot`。

如需验收后继续查看 Demo：

```powershell
.\scripts\acceptance.ps1 -KeepRunning
```

该模式仍会先停止 Demo 并完成数据保护验证，然后再重新启动。

## 3. 发布证据门禁

执行：

```powershell
.\scripts\release_gate.ps1
```

确认输出：

```text
=== v2.2.0 发布门禁通过 ===
```

门禁会阻止以下情况发布：

- 验收报告不是 `passed`
- 报告来自非 Windows 10/11 系统
- Docker Desktop、Engine 或 Compose 版本缺失
- Demo 数量不正确
- 正式 `.env`、SQLite 或任务目录保护缺少 PASS 证据
- 当前分支不是 `master`
- 验收报告对应旧 commit
- Git 工作区不干净
- 应用、README 或 Changelog 版本不是 `2.2.0`

将经过人工检查的 `acceptance-results/latest.md` 正文粘贴到 Issue #23。不要上传整个目录、完整日志、`.env`、SQLite 或视频。

## 4. 备份与恢复验收

发布前至少在 Windows 实机执行一次：

```powershell
.\scripts\backup.ps1 -Label release-candidate
```

确认：

- [ ] 备份包保存在 `backups/` 或指定的可信目录
- [ ] 备份输出的任务、候选片段、输出片段和发布任务数量合理
- [ ] `python -m scripts.backup_restore_runtime verify <备份包>` 通过
- [ ] 包含 `.env` 的备份没有上传到公开位置
- [ ] 损坏的测试备份不会覆盖现有数据库
- [ ] 恢复前会生成 `pre-restore` 回滚包
- [ ] 恢复后数据库数量与备份清单一致
- [ ] 恢复后运行 `doctor.ps1` 和 `acceptance.ps1` 通过

正式升级或发布操作前运行：

```powershell
.\scripts\pre_upgrade.ps1
```

## 5. 真实工作流验收

- [ ] 导入一条低风险短测试视频
- [ ] FFmpeg 音频提取成功
- [ ] 至少一种转写方式成功
- [ ] AI 分析生成候选片段
- [ ] 人工审核与保存成功
- [ ] 生成至少一个本地短视频文件
- [ ] 发送中心能创建 `manual_export` 草稿
- [ ] 审片保存会自动形成保留/淘汰反馈，重复保存不增加重复事件
- [ ] 附件日汇总只能作为未归因账号基线，预览后才允许确认导入
- [ ] 最近 50 条同步遇到登录、验证码、限流或页面变化时立即停止且不自动重试
- [ ] 未匹配/冲突作品不进入 Prompt 主结论；不足 3 周期或 30 条时显示数据不足

真实平台发布不作为基础安装通过条件。抖音与 B站验证单独跟踪在 Issue #25，并且 Release 中必须明确“逐账号灰度验证”。

## 6. 文档和版本一致性

- [ ] `app/main.py` 版本为 `2.2.0`
- [ ] README 中英文版本徽章为 `2.2.0`
- [ ] `CHANGELOG.md` 包含本次版本的重要变化
- [ ] `README.md` 快速开始命令可复制执行
- [ ] `.env.example` 没有个人绝对路径和真实密钥
- [ ] `ROADMAP.md` 链接到公开 Issue
- [ ] `LICENSE`、`SECURITY.md` 和 `CONTRIBUTING.md` 存在
- [ ] `docs/BACKUP_AND_RESTORE.md` 与实际脚本参数一致
- [ ] `docs/WINDOWS_ACCEPTANCE.md` 与验收和门禁脚本一致
- [ ] 占位图说明真实，不冒充实机截图

## 7. 隐私与安全检查

确认仓库没有提交：

- [ ] `.env` 或 API Key
- [ ] SQLite / DB 文件
- [ ] 备份 ZIP 或恢复临时文件
- [ ] `acceptance-results/` 本地报告和日志
- [ ] Cookie、storage state、浏览器 Profile
- [ ] 真实平台账号信息
- [ ] 原始视频、切片、音频
- [ ] 发布日志和失败截图
- [ ] 作者电脑用户名或个人绝对路径

## 8. 创建 GitHub Release

建议填写：

```text
Tag: v2.2.0
Target: master
Title: NiuMa Studio v2.2.0 — Douyin Content Review and Prompt Attribution
Latest release: Yes
Pre-release: No
```

发布正文以 `CHANGELOG.md` 的 2.2.0 内容为基础，并明确：

- Windows 本地单用户工具
- Demo 不连接真实账号
- 抖音与 B站发布需要逐账号灰度验证
- 不绕过登录、验证码或平台风控
- 升级前应使用 `pre_upgrade.ps1` 创建本地回滚包
- v2.2.0 的 Windows 10/11 + Docker Desktop 验收日期和对应 commit

GitHub 会自动提供源码 ZIP 和 tar.gz。当前没有经过签名和实机验证的 Windows 安装包时，不要上传名为“安装包”的临时压缩文件。

## 9. 发布后检查

- [ ] Release 页面显示为 Latest
- [ ] Tag 指向验收报告对应的 `master` 提交
- [ ] 源码压缩包可下载
- [ ] README 中的链接和图片可打开
- [ ] 新用户按 README 能进入 Demo
- [ ] 新用户能找到备份恢复和 Windows 验收文档
- [ ] 创建下一版本的 `Unreleased` 记录
- [ ] Issue #23 有脱敏验收报告并已关闭
- [ ] Issue #25 继续跟踪真实平台灰度，不因基础 Release 被错误关闭

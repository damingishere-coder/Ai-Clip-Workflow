# v2.0.0 Release 检查清单

本清单用于发布牛马片场正式版本。只有代码检查、Windows 实机验收、备份保护和文档核对均通过后，才创建 Git Tag 与 GitHub Release。

## 1. 自动化检查

- [ ] `master` 最新 CI 为绿色
- [ ] Python 编译、Ruff、JavaScript、PowerShell 和 pytest 通过
- [ ] 三套 Docker Compose 配置通过
- [ ] Demo 建库检查通过
- [ ] 备份、恢复、回滚和损坏包保护测试通过
- [ ] 最终 Docker 镜像能启动并通过 `/health`
- [ ] Docker 镜像中的工作台、任务列表、片段总览和发送中心返回 200
- [ ] `pip check` 无依赖冲突
- [ ] 敏感文件和备份 ZIP 检查无异常

## 2. Windows 实机验收

在干净或可回滚的 Windows 10/11 环境中执行：

```powershell
.\scripts\acceptance.ps1 -KeepRunning
```

确认：

- [ ] Docker Desktop 正常
- [ ] `setup.ps1` 不覆盖已有 `.env`
- [ ] `doctor.ps1` 输出无阻塞项
- [ ] Demo 容器状态为 `healthy`
- [ ] 工作台出现 3 条 Demo 任务
- [ ] 片段总览出现 6 条候选片段
- [ ] 发送中心出现 6 条 `manual_export` 草稿
- [ ] 页面中文、按钮、空状态和布局正常
- [ ] 停止 Demo 后正式数据库与视频目录不受影响

验收完成后执行：

```powershell
.\scripts\stop.ps1 -Demo -RemoveDemoData
```

## 3. 备份与恢复验收

发布前至少在 Windows 实机执行一次：

```powershell
.\scripts\backup.ps1 -Label release-candidate
```

确认：

- [ ] 备份包保存在 `backups/` 或指定的可信目录
- [ ] 备份输出的任务、候选片段、输出片段和发布任务数量合理
- [ ] `python -m scripts.backup_restore verify <备份包>` 通过
- [ ] 包含 `.env` 的备份没有上传到公开位置
- [ ] 损坏的测试备份不会覆盖现有数据库
- [ ] 恢复前会生成 `pre-restore` 回滚包
- [ ] 恢复后数据库数量与备份清单一致
- [ ] 恢复后运行 `doctor.ps1` 和 `acceptance.ps1` 通过

正式升级或发布操作前运行：

```powershell
.\scripts\pre_upgrade.ps1
```

## 4. 真实工作流验收

- [ ] 导入一条低风险短测试视频
- [ ] FFmpeg 音频提取成功
- [ ] 至少一种转写方式成功
- [ ] AI 分析生成候选片段
- [ ] 人工审核与保存成功
- [ ] 生成至少一个本地短视频文件
- [ ] 发送中心能创建 `manual_export` 草稿

真实平台发布不作为基础安装通过条件。抖音与 B站验证单独跟踪在 Issue #25。

## 5. 文档和版本一致性

- [ ] `app/main.py` 版本为 `2.0.0`
- [ ] README 中英文版本徽章为 `2.0.0`
- [ ] `CHANGELOG.md` 包含本次版本的重要变化
- [ ] `README.md` 快速开始命令可复制执行
- [ ] `.env.example` 没有个人绝对路径和真实密钥
- [ ] `ROADMAP.md` 链接到公开 Issue
- [ ] `LICENSE`、`SECURITY.md` 和 `CONTRIBUTING.md` 存在
- [ ] `docs/BACKUP_AND_RESTORE.md` 与实际脚本参数一致
- [ ] 占位图说明真实，不冒充实机截图

## 6. 隐私与安全检查

确认仓库没有提交：

- [ ] `.env` 或 API Key
- [ ] SQLite / DB 文件
- [ ] 备份 ZIP 或恢复临时文件
- [ ] Cookie、storage state、浏览器 Profile
- [ ] 真实平台账号信息
- [ ] 原始视频、切片、音频
- [ ] 发布日志和失败截图
- [ ] 作者电脑用户名或个人绝对路径

## 7. 创建 GitHub Release

建议填写：

```text
Tag: v2.0.0
Target: master
Title: NiuMa Studio v2.0.0 — Local AI Highlight Production Workflow
Latest release: Yes
Pre-release: No
```

发布正文以 `CHANGELOG.md` 的 2.0.0 内容为基础，并明确：

- Windows 本地单用户工具
- Demo 不连接真实账号
- 抖音与 B站发布需要逐账号灰度验证
- 不绕过登录、验证码或平台风控
- 升级前应使用 `pre_upgrade.ps1` 创建本地回滚包

GitHub 会自动提供源码 ZIP 和 tar.gz。当前没有经过签名和实机验证的 Windows 安装包时，不要上传名为“安装包”的临时压缩文件。

## 8. 发布后检查

- [ ] Release 页面显示为 Latest
- [ ] Tag 指向预期的 `master` 提交
- [ ] 源码压缩包可下载
- [ ] README 中的链接和图片可打开
- [ ] 新用户按 README 能进入 Demo
- [ ] 新用户能找到备份恢复文档
- [ ] 创建下一版本的 `Unreleased` 记录
- [ ] 公开记录 Windows 实机验收结果

# v2.6.0 Release 检查清单

历史版本清单：[v2.5.5](RELEASE_CHECKLIST_V2_5_5.md)。用户已同意工程/实机验收后在日常使用中补充内容质量检查，不要求先提供专门媒体或完成人工盲审；访谈、知识与视觉保持试用，人工审片、字幕决定与排期操作仍须明确执行。

本清单用于发布牛马片场正式版本。只有代码检查、Windows 实机验收、备份保护和文档核对均通过后，才创建 Git Tag 与 GitHub Release。

## 当前交付状态

v2.6.0 已发布并部署，运行提交 75cdd93。1346 项完整回归、PR/主干三项 CI、Windows 实机 17 项通过；真实 Web/Worker 2.6.0、23 项迁移、18 次页面、7 项资源哈希及配置/媒体/旧记录保护通过。证据与提前迁移纠正见 PROJECT_STATUS.md；下面是以后逐项执行的模板，不能用空勾选覆盖本次实际报告。

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
=== v2.6.0 发布门禁通过 ===
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
- 应用、README 或 Changelog 版本不是 `2.6.0`

本地保留经过检查的实机报告；提交 PROJECT_STATUS 的脱敏结论即可。Issue 评论需另外明确授权，不上传完整日志、`.env`、SQLite 或视频。

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

## 5. 工作流与实际运行验收

本轮按用户批准的工程与实际页面验收推进，不新增真实 AI、同步或投稿作为版本门禁；真实内容质量在日常使用中补验。

- [ ] 十条隔离视频导入，重复/并发提交不重复创建；实际复制哈希与原片保留通过
- [ ] 模拟转写/AI 与真实切片完成到人工审片，单项失败不阻塞后续
- [ ] 租约、进程退出、切片 checkpoint 与重复恢复不重做已确认单元
- [ ] 未确认、过期确认、错误字幕版本不能进入内容准备或排期
- [ ] 统一待办计数、分页、刷新和历史批次异常跳转通过
- [ ] 正式 Web/Worker 版本、监听父链、数据库迁移与旧记录保留通过
- [ ] 正式旧任务、五 Profile、视觉默认关闭及桌面/窄屏资源与页面检查通过
- [ ] 默认保留不冒充人工接受，官方作品按独立作品归因，不足门槛显示数据不足

视觉真实图像能力沿用 v2.5 已保留证据，不为本版重复调用。抖音与 B站仍逐账号灰度验证；发布验收不擅自触发账号操作或改变既有排期。

## 6. 文档和版本一致性

- [ ] `app/main.py` 版本为 `2.6.0`
- [ ] README 中英文版本徽章为 `2.6.0`
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
Tag: v2.6.0
Target: master
Title: NiuMa Studio v2.6.0 — Production Queue
Latest release: Yes
Pre-release: No
```

发布正文以 `CHANGELOG.md` 的 2.6.0 内容为基础，并明确：

- Windows 本地单用户工具
- Demo 不连接真实账号
- 抖音与 B站发布需要逐账号灰度验证
- 不绕过登录、验证码或平台风控
- 升级前应使用 `pre_upgrade.ps1` 创建本地回滚包
- v2.6.0 的 Windows 10/11 + Docker Desktop 验收日期和对应 commit
- 视觉默认关闭；图像/综合评审技术验收不代表节目质量提升
- 访谈/知识模板及视觉功能为试用，真实内容质量未宣称通过；人工审片、字幕及发布边界保留

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

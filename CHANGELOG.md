# Changelog

本文件记录牛马片场面向使用者的重要变化。开发过程中的细节仍可在 Git 历史和项目开发日志中查看。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循语义化版本的基本原则。

## Unreleased

### Planned

- 在 Windows 10/11 + Docker Desktop 实机生成当前 `master` 的脱敏验收报告。
- 完成抖音 / B站真实发布灰度验证矩阵。

## 2.1.0 - 2026-08-23

### Added

- 接入受控 Codex CLI 文本分析与发送中心文案生成，并保留远程 DeepSeek 和本地 Ollama 回退路径。
- 增加 Windows 原生日常启停脚本，统一工作台与 Windows Chrome Worker 的启动、停止、检测和 Docker 回退说明。
- 新建任务固定使用综艺笑点优先模式，并按最近创建时间提供可见任务名称历史候选。
- 片段审核增加当前列表全选、启用数量与半选状态。
- 手动生成切片改为后台任务进度，重复点击或并发请求复用同一条进行中任务。

### Changed

- 发送中心前台固定抖音，同时保留 B站历史数据、API 与 Publisher 兼容能力。
- 统一抖音账号预检、标题、简介和话题规则，旧草稿升级前先创建 SQLite 安全备份。
- 发送中心按原始任务分组并支持组内全选；AI 重写同步刷新标题、简介和话题。
- AI 分析运行期间锁定相关按钮与表单控件，避免并发操作覆盖候选结果。
- 项目、API、Windows Worker、备份清单、页面侧栏和发布文档统一升级到 `2.1.0` / `v2.1`。

### Security

- Codex CLI 仅作为本机受控进程能力，不改变 Codex 登录、提供商或认证配置。
- 继续保留登录、验证码、平台风控和不确定发布结果的人工确认边界。

### Validation

- Ruff、Python 编译、全部前端 JavaScript 与 PowerShell 语法检查通过。
- 完整自动化测试 `448 passed`，三套 Docker Compose 配置和 `pip check` 通过。
- 创建 `v2.1.0` Tag 或 GitHub Release 前，仍须在最终 `master` 上重新完成 Windows 10/11 + Docker Desktop 实机验收和发布门禁。

## 2.0.0 - 2026-08-03

### Added

- 整合素材管理、转写、AI 选片、人工审核、视频切割、内容准备、排期和执行记录。
- 支持“通用内容价值”和“综艺笑点优先”两类 AI 选片模式。
- 支持远程 OpenAI-compatible / DeepSeek 与本地 Ollama。
- 发送中心分为内容准备、排期计划和执行记录。
- 支持批量排期预览、跨午夜每日窗口、月历日期详情和续接当前平台最晚排期。
- 抖音与 B站使用统一 Scheduler、Publisher Registry 和 Windows Chrome Worker。
- 浏览器账号使用独立本地 Profile，不要求保存平台账号密码。
- 重构中英文项目首页，增加产品定位、工作流程、能力矩阵和首次成功标准。
- 增加 MIT License、贡献指南、安全策略、路线图和 GitHub 社区模板。
- 增加正式、开发与隔离 Demo 三套 Docker Compose 配置。
- 增加 `setup.ps1`、`doctor.ps1`、`start.ps1`、`stop.ps1` 和 `acceptance.ps1`。
- 增加不连接真实账号的 Demo 数据，包含任务、候选片段、演示切片和发布草稿。
- 增加 Dependabot、依赖维护策略和 v2.0.0 Release 检查清单。
- 建立公开 Roadmap Issues，持续跟踪实机验收、备份恢复和平台兼容性。
- 增加 `backup.ps1`、`restore.ps1` 和 `pre_upgrade.ps1`。
- 增加带文件哈希、数据库数量和版本信息的可校验备份清单。
- 支持默认备份 SQLite 与 `.env`，并可显式选择媒体文件。
- 恢复前自动创建 `pre-restore` 回滚包，并提供数据库与配置原子切换保护。
- `acceptance.ps1` 增加 Windows、Docker、Chrome、Git commit 和逐项检查证据报告。
- 验收前后自动对比 `.env`、正式 SQLite 和正式任务目录元数据指纹。
- 增加 `release_gate.ps1`，阻止旧报告、非 Windows 10/11 报告、缺失 Docker 证据或脏工作区发布。
- 增加 Windows 云端主机冒烟，验证 PowerShell、路径、setup 幂等性、原生 Demo、页面和备份。
- 增加 Windows 实机验收与发布证据指南。

### Changed

- 立即发送与定时发送统一进入同一套调度与发布链路。
- 只有取得平台作品 ID、稿件 ID、作品链接或其他明确成功证据才进入 `PUBLISHED`。
- 登录失效、验证码、平台风控和结果不确定统一进入 `NEED_REVIEW`。
- 软删除和永久删除分离，外部唯一原片与运行中的任务继续受到保护。
- 全自动主流程暂不强制生成或烧录字幕，字幕工作台保持独立。
- Docker 视频目录不再写死作者电脑的 E 盘，改为 `NIUMA_STORAGE_PATH`。
- 正式 Compose 不再启用源码挂载和 Uvicorn 热重载。
- Docker 基础镜像改为 `python:3.12-slim-bookworm`。
- 运行时和开发直接依赖改为经过 CI 验证的固定版本。
- Scheduler、Worker、发布状态和排期 API 等细节迁移到独立技术文档。
- `acceptance.ps1 -KeepRunning` 先完成 Demo 停止和正式数据保护验证，再重新启动 Demo。

### Validation

- CI 检查 Python 编译、Ruff、pytest、JavaScript 和 PowerShell 语法。
- CI 校验正式、开发和 Demo 三套 Compose。
- CI 检查敏感运行时文件、备份 ZIP、本地验收报告与隔离 Demo 数据库。
- CI 构建最终 Docker 镜像，启动应用并验证主要页面。
- CI 使用 `pip check` 检查依赖冲突。
- CI 验证数据库、`.env`、媒体冲突保护和损坏备份的恢复往返行为。
- CI 在 Windows Runner 上运行独立的原生主机冒烟，并上传脱敏日志 Artifact。
- 正式 Release 仍要求 Windows 10/11 + Docker Desktop 本地报告，云端 Windows Runner 不作为替代证据。

### Security

- `.env`、数据库、视频、备份 ZIP、验收报告、日志、浏览器 Profile、Cookie 和 storage state 不进入 Git。
- Demo 模式使用独立数据库、关闭 Scheduler，并固定使用 `manual_export`。
- 备份包遇到重复路径、越界路径、哈希不一致或 SQLite 损坏时会拒绝恢复。
- 验收报告不写入 `.env` 内容、API Key、Token、Cookie、用户名或完整项目路径。
- 自动化不会绕过二维码、短信、验证码、滑块、登录失效或平台风控。

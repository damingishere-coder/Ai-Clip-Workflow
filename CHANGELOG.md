# Changelog

本文件记录牛马片场面向使用者的重要变化。开发过程中的细节仍可在 Git 历史和项目开发日志中查看。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循语义化版本的基本原则。

## Unreleased

### Planned

- 完成 Windows 实机验收和抖音 / B站灰度验证矩阵。

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

### Validation

- CI 检查 Python 编译、Ruff、pytest、JavaScript 和 PowerShell 语法。
- CI 校验正式、开发和 Demo 三套 Compose。
- CI 检查敏感运行时文件、备份 ZIP 与隔离 Demo 数据库。
- CI 构建最终 Docker 镜像，启动应用并验证主要页面。
- CI 使用 `pip check` 检查依赖冲突。
- CI 验证数据库、`.env`、媒体冲突保护和损坏备份的恢复往返行为。

### Security

- `.env`、数据库、视频、备份 ZIP、日志、浏览器 Profile、Cookie 和 storage state 不进入 Git。
- Demo 模式使用独立数据库、关闭 Scheduler，并固定使用 `manual_export`。
- 备份包遇到重复路径、越界路径、哈希不一致或 SQLite 损坏时会拒绝恢复。
- 自动化不会绕过二维码、短信、验证码、滑块、登录失效或平台风控。

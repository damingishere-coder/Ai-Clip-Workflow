# Changelog

本文件记录牛马片场面向使用者的重要变化。开发过程中的细节仍可在 Git 历史和项目开发日志中查看。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循语义化版本的基本原则。

## [Unreleased]

### Added

- 重构中英文项目首页，增加产品定位、工作流程、能力矩阵和首次成功标准。
- 增加 MIT License、贡献指南、安全策略、路线图和 GitHub 社区模板。
- 把 Scheduler、Worker、发布状态和排期 API 等细节迁移到独立技术文档。

## [2.0.0] - 2026-08-03

### Added

- 整合素材管理、转写、AI 选片、人工审核、视频切割、内容准备、排期和执行记录。
- 支持“通用内容价值”和“综艺笑点优先”两类 AI 选片模式。
- 支持远程 OpenAI-compatible / DeepSeek 与本地 Ollama。
- 发送中心分为内容准备、排期计划和执行记录。
- 支持批量排期预览、跨午夜每日窗口、月历日期详情和续接当前平台最晚排期。
- 抖音与 B站使用统一 Scheduler、Publisher Registry 和 Windows Chrome Worker。
- 浏览器账号使用独立本地 Profile，不要求保存平台账号密码。

### Changed

- 立即发送与定时发送统一进入同一套调度与发布链路。
- 只有取得平台作品 ID、稿件 ID、作品链接或其他明确成功证据才进入 `PUBLISHED`。
- 登录失效、验证码、平台风控和结果不确定统一进入 `NEED_REVIEW`。
- 软删除和永久删除分离，外部唯一原片与运行中的任务继续受到保护。
- 全自动主流程暂不强制生成或烧录字幕，字幕工作台保持独立。

### Security

- `.env`、数据库、视频、日志、浏览器 Profile、Cookie 和 storage state 不进入 Git。
- 自动化不会绕过二维码、短信、验证码、滑块、登录失效或平台风控。

[Unreleased]: https://github.com/damingishere-coder/Ai-Clip-Workflow/compare/v2.0.0...HEAD
[2.0.0]: https://github.com/damingishere-coder/Ai-Clip-Workflow/releases/tag/v2.0.0

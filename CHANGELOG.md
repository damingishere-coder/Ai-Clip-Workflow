# Changelog

## 2026-06-17

### 修复

- 修复 AI 分析和恢复历史分析时候选片段可能被误清空的问题。
- 给字幕烧录 FFmpeg 调用补充超时配置，避免 Windows 本地进程长期卡住。
- 给任务详情视频探测 FFprobe 调用补充超时保护，超时后降级显示未知时长。
- 移除重复的 `LOCAL_ADMIN_TOKEN` 配置字段。
- 将 Pydantic 校验器迁移到 v2 `field_validator` 写法，消除弃用 warning。

### 文档

- 重写 `README.md`，明确当前已实现能力、边界和 Windows 快速启动。
- 新增 `docs/WINDOWS_SETUP.md`，提供新手 Windows 部署教程。
- 重写架构、任务流、数据库、部署、AI 分析、切片、候选审核、字幕发送文档。
- 同步 `docs/UI_REFERENCE.md`、`docs/PROJECT_GUIDE.md`、`DEVELOPMENT_LOG.md`、`NEXT_STEPS.md`。
- 明确 `scheduled_at` 当前只是字段预留，没有真正定时调度器。
- 明确发送中心是人工确认 + opencli 辅助投稿，不是无人值守发布。

### 配置与清理

- 更新 `.env.example`，补齐 Windows 路径、FFmpeg、AI、转写、opencli 配置说明。
- 更新 `.gitignore`，补充 pytest/ruff 缓存和本地备份文件规则。
- 给 `docker-compose.yml` 的 E 盘任务目录挂载补充说明。
- 清理明确可再生成的本地日志、缓存和 `__pycache__` 产物。

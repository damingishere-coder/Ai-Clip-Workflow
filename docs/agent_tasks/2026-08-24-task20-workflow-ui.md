# 任务 20 状态修复与新建流程精简

## 背景

任务 `3210d91ee1fb` 已完成 AI、第二次切片和发送中心 12/12 关联，但随后一次手动 AI 重入删除仍被切片引用的候选记录，触发 SQLite 外键约束并把主状态覆盖为失败。新建任务页同时仍暴露 NAS 入口，长直播设置的 `hidden` 被 CSS 覆盖，跳过字幕会越过片段审核直接恢复自动流水线。

## 目标

- 阻止并发或已有成片任务再次启动手动 AI，且冲突不得降级成功状态。
- 新建任务仅允许上传本机视频，任务名称历史限制为 5 条。
- 长直播密度只在长直播模式显示和提交。
- 跳过字幕后进入片段审核，审核同步成功后任务正确完成。
- 在数据证据完整时仅修复任务 20 主状态，不重跑 AI、切片或发布同步。

## 允许修改范围

- `app/` 下相关模型、路由、服务、模板、JavaScript、CSS 和数据库迁移账本。
- 对应 `tests/` 回归测试。
- `DEVELOPMENT_LOG.md`、`NEXT_STEPS.md`、`docs/UI_REFERENCE.md`、`docs/TASK_FLOW.md`、`docs/DATABASE_SCHEMA.md`。
- 正式 SQLite 中任务 20 的 `status/progress/error_message/last_error/updated_at`，以及一条审计日志；上传单入口迁移只归一旧来源字段。

## 禁止修改范围

- AI Provider、Codex 认证、Prompt、超时、分析算法和任务 20 的候选分析结果。
- 任务 20 的切片文件、候选内容、发布任务、排期和字幕决定。
- 真实平台发布、账号状态、Cookie、Token、`.env` 和外部原片。

## 已确定实现要求

- 手动 AI 冲突返回 HTTP 409；自动流水线持有有效租约的首次 AI 不受影响。
- 候选替换在同一事务内检查 `output_clip` 引用并返回领域冲突，不泄漏 SQLite 错误。
- JSON 已有文件创建和 `/api/files/browse` 下线；数据库旧列保留但不再提供能力。
- `skip-to-review` 写入 `subtitle_delivery_mode=original`，不创建自动恢复 Job，返回审核地址；旧路径保留兼容。
- 审核同步无须重切且完整关联时显式完成任务；失败时保留审核状态。

## 验收标准

- 任务 20 显示已完成、100%、无外键错误，12 个活跃文件和 12 条排期均保持原 ID。
- 全自动运行中或已有活跃切片/发布关联的手动 AI 请求均被拒绝且数据不变。
- 新建页无 NAS 文案、控件和请求；历史名称最多 5 条。
- 通用和康熙模式不显示/提交长直播参数，长直播模式正常显示/提交。
- 跳过字幕后进入审核，保存同步后进入发送中心且不触发真实发布。

## 测试命令

- `python -m pytest -q`（使用项目 `.venv`）
- `python -m compileall -q app tests`
- `node --check app/static/js/app.js`
- `node --check app/static/js/subtitle-editor.js`
- `git diff --check`
- SQLite `PRAGMA integrity_check`、`PRAGMA foreign_key_check`

## 返回格式

报告修改文件、测试结果、任务 20 恢复证据、分支、提交、推送和 PR；任何失败需给出准确命令与错误，不得用模拟测试声称真实 AI 或真实发布成功。

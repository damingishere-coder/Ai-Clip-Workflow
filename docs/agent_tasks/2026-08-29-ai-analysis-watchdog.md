# AI 分析长任务误杀与状态收口修复

## 背景

任务 `d7fdc8d5cdb0` 在综艺 AI 分析阶段完成多个可恢复批次后，父 Worker 仍因 Job 的进度百分比和文案未变化，将整个阶段误判为连续 900 秒无进展并终止。Job 已失败，但任务表保留小写 `ai_analyzing`，页面继续显示 65% 和“AI 分析中”。

## 目标

- 将普通 Workflow Job 的默认无进展阈值从 900 秒延长到 1800 秒，并允许通过非敏感环境变量调整。
- 将 AI 单元 checkpoint 的更新时间视为真实业务进展，避免串行多批次分析被按整个阶段误杀。
- 父 Worker 终止自动流水线时，兼容小写 `ai_analyzing` 并正确落到 `FAILED_AI_ANALYZING`。

## 允许修改范围

- `app/core/config.py`
- `app/services/job_worker.py`
- `app/services/job_service.py`
- `.env.example`
- 直接相关测试与项目进度文档

## 禁止修改范围

- 不重跑当前 AI 任务，不重复调用 Codex CLI 或其他计费模型。
- 不修改活动 SQLite 数据库，不删除 checkpoint、转写、素材或候选数据。
- 不改变 Codex 登录、模型提供商或认证配置。
- 不触发真实发布、远程转写或平台操作。

## 已确定实现要求

1. Worker 的无进展标记包含 `progress`、`message` 和 `checkpoint_updated_at`，不得使用 Worker 自己每 20 秒更新的 heartbeat 充当业务进展。
2. 普通 Job 的默认阈值为 1800 秒，同时继续取媒体处理超时配置中的最大值。
3. 自动流水线异常失败时，小写 `ai_analyzing` 和大写 `AI_ANALYZING` 都必须收口为 `FAILED_AI_ANALYZING / 45%`，并写入错误信息。
4. 保留第 5 个 AI 单元的 `running/uncertain` 安全边界，不自动重复请求。

## 验收标准

- checkpoint 更新时间变化会重置父 Worker 的无进展计时；单纯 heartbeat 变化不会重置。
- 普通自动流水线默认 1800 秒无真实进展后才终止。
- 两种 AI 分析状态大小写都能正确落到失败态。
- 定向 pytest、Ruff、compileall 和 `git diff --check` 通过。

## 测试命令

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_job_fencing.py tests/test_job_queue.py tests/test_ai_job_consistency.py
.\.venv\Scripts\python.exe -m ruff check app/core/config.py app/services/job_worker.py app/services/job_service.py tests/test_job_fencing.py
.\.venv\Scripts\python.exe -m compileall -q app
git diff --check
```

## 返回格式

- 根因和现场证据
- 修改文件与行为变化
- 测试命令和结果
- 是否触发真实 AI、ASR、FFmpeg、发布或活动数据库修改
- Git 提交、分支、推送和 PR

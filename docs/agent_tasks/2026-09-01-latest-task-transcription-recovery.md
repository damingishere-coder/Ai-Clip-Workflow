# 最新任务失败与本地转写简体化修复任务

## 背景

- 最新任务 `614fb38401e0` 已完成本地转写，但 AI 分析的一个召回窗口超时，覆盖率停在 `17/18`。
- 当前本地 faster-whisper 输出包含繁体字；用户确认今后统一转换为简体字，只转换字形并保留台湾用词。
- 现有全自动重试入口无法正确补跑 AI 分析，也没有针对“可能已计费但结果未知”的二次确认。

## 目标

1. 本地转写的段落与逐词结果在写入 checkpoint 前统一执行 OpenCC `t2s` 转换。
2. 将转换规则加入本地转写 checkpoint 身份，避免复用旧繁体分块。
3. AI 覆盖不完整时在 AI 分析阶段失败，并保留选片阶段的防御性门禁。
4. 全自动重试支持结构化确认、未变化输入的失败单元续跑，以及变化输入的全新 AI Job。
5. 将 Codex 默认窗口超时调整为 600 秒，并更新页面交互、项目文档和测试。
6. 安全恢复任务 `614fb38401e0`，不重复转写、不触发真实发布。

## 允许修改范围

- `app/` 中转写、AI 流水线、Job 重试、任务路由和任务详情交互相关文件。
- `tests/` 中与本次行为对应的自动化测试。
- `requirements.in`、`requirements.txt`、`.env.example`。
- `DEVELOPMENT_LOG.md`、`NEXT_STEPS.md`、`docs/UI_REFERENCE.md` 和本任务文件。
- 通过测试后，将代码提交同步到运行工作树并定向调整运行 `.env` 的超时配置。

## 禁止修改范围

- 主目录已有 `.codemap/*` 与 `PROJECT_REAUDIT.md`。
- 发布 Worker、真实抖音/B站发布、排期、账号登录与平台验证。
- PR 合并、强推、历史改写、数据库结构和既有失败 Job 证据。
- API Key、Token、Cookie、密码及其他敏感配置内容。

## 已确定实现要求

- 固定依赖 `opencc==1.4.2`，使用 `t2s.json`，不使用词汇本地化转换。
- 当前 transcript 变化后从 `AI_ANALYZING` 新建独立 Job，不继承旧 AI 单元。
- 输入不变时，确认后只重置不确定单元并复用成功 checkpoint。
- 未传 `confirm_uncertain_ai=true` 时返回 HTTP 409 和结构化确认信息。
- 有下游切片或发布记录时拒绝覆盖式恢复。
- Codex 超时默认值和运行值均设为 600 秒。

## 验收标准

- 段落和逐词文本均转为简体；台湾用词和非中文内容不被错误改写。
- 旧繁体 checkpoint 不会被新规则复用。
- AI 覆盖不完整落入 `FAILED_AI_ANALYZING`。
- 409 确认、同输入单元续跑、变输入新 Job、下游保护均有测试覆盖。
- 相关测试、完整 pytest、Ruff、compileall、JavaScript 语法检查、`git diff --check` 和 Windows smoke/release gate 通过。
- 正式 transcript 有可恢复备份，转换后时间戳、行数和 Markdown 结构不变。
- 新 AI Job 达到 100% 覆盖并进入正常后续阶段，且无发布记录。

## 测试命令

- `python -m pytest tests/test_offline_transcription.py tests/test_pipeline_checkpoint.py tests/test_auto_pipeline.py`
- `python -m pytest`
- `python -m ruff check app tests`
- `python -m compileall app tests`
- `node --check app/static/js/app.js`
- `git diff --check`
- 项目现有 Windows smoke/release gate。

## 返回格式

- 修改文件及核心行为。
- 测试命令、结果与失败证据（如有）。
- Git 分支、提交、Push、PR、CI 与未合并状态。
- 运行服务重启验证、当前任务新 Job、AI 覆盖率和发布记录状态。

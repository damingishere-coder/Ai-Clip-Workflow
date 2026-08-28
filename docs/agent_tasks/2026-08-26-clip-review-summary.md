# 片段审核累计统计精简

## 背景

`/clips` 顶部当前显示“待 AI 分析、待检查、可生成切片、已完成、异常任务”五项阶段统计。用户希望只保留累计审核任务、已通过视频和已完成任务三个长期有用的汇总指标。

## 目标

- 将顶部统计精简为三项：累计审核任务、已通过视频、已完成任务。
- 每项使用明确、可验证且排除软删除数据的 SQLite 口径。
- 保持审核队列、单任务指标和操作入口不变。

## 允许修改范围

- `app/services/task_query_service.py`
- `app/templates/clips_overview.html`
- `app/static/css/styles.css`
- `tests/test_task_query_service.py`
- `tests/test_p1_1_db_performance.py`（仅相关回归需要时）
- `DEVELOPMENT_LOG.md`、`NEXT_STEPS.md`、`docs/UI_REFERENCE.md`

## 禁止修改范围

- 任务、候选片段、切片和发布状态机。
- SQLite Schema、迁移和真实数据。
- AI Provider、Prompt、账号、Cookie、Token、`.env` 和认证配置。
- 真实 AI、切片、字幕或平台发布流程。

## 已确定实现要求

- “累计审核任务”统计当前未删除任务中至少有一条未删除候选片段的去重任务数；当前数据库没有可靠的任务级人工审核事件，不能把 `reviewed=1` 冒充纯人工审核。
- “已通过视频”统计上述未删除任务中 `is_deleted=0 AND enabled=1` 的候选片段总数，页面说明为“当前启用的视频片段”。
- “已完成任务”兼容 `completed / completed_with_errors / COMPLETED`，统一大小写后统计当前未删除任务。
- 三张卡分别提供自己的统计说明，不再统一显示“来自当前任务库”。
- 统计网格由五列改为三列；现有响应式折叠逻辑继续保留。

## 验收标准

- `/clips` 顶部只显示“累计审核任务、已通过视频、已完成任务”。
- 当前活动库预期显示 `20 / 163 / 20`；数值随数据变化时以同一口径实时渲染。
- 软删除任务和软删除候选片段不计入累计审核与通过数。
- 大写自动完成状态 `COMPLETED` 不再被漏算。
- 审核队列结构、任务行指标、详情和进入审核按钮保持不变。
- 桌面与 390px 窄屏无横向溢出。

## 测试命令

- `.venv\Scripts\python.exe -m pytest tests/test_task_query_service.py tests/test_p1_1_db_performance.py -q`
- `.venv\Scripts\python.exe -m pytest -q`
- `.venv\Scripts\python.exe -m compileall -q app tests`
- `.venv\Scripts\python.exe -m ruff check app/services/task_query_service.py tests/test_task_query_service.py`
- `git diff --check`
- 浏览器刷新 `http://127.0.0.1:8001/clips`，核对三项指标、审核队列、窄屏布局和页面错误。

## 返回格式

报告统计口径、修改文件、测试与浏览器证据、分支、提交、推送和 PR；不得把本地统计验证描述为真实 AI、切片或平台发布验证。

# 删除工作台每日任务柱状图

## 背景

工作台当前使用较大区域展示本周每日新增任务柱状图。现阶段任务创建频率较低，图表还会把未来日期显示为 0，并将单条任务按本周峰值拉满，无法提供可靠的趋势判断。

## 目标

- 删除每日新增任务柱状图，不增加替代装饰模块。
- 保留本周任务概览、四项核心指标、周日期范围和最近任务列表。
- 清理不再使用的每日统计、样式和动效代码。

## 允许修改范围

- Dashboard 查询上下文、首页模板、Dashboard 专用样式和动效配置。
- Dashboard 相关测试。
- `DEVELOPMENT_LOG.md`、`NEXT_STEPS.md`、`docs/UI_REFERENCE.md`。

## 禁止修改范围

- SQLite 表结构和真实数据。
- 任务、切片、字幕和发布状态机。
- AI Provider、账号、Cookie、Token、`.env` 和真实平台发布。
- 历史 `docs/agent_tasks` 记录。

## 已确定实现要求

- `weekly_chart` 精简并改名为 `weekly_summary`，只返回本周总数和日期范围。
- 保留应用时区、周一至下周一边界以及旧时间格式兼容逻辑。
- 删除每日计数、星期标签、柱高百分比和当天高亮字段。
- 删除柱状图 HTML、CSS、响应式规则、JavaScript reveal 和专用关键帧。
- 删除图表后为统计条保留自然的底部间距。

## 验收标准

- 首页不再出现“本周每日新增任务”“Task trend”“本周累计”和七日柱图。
- 首页继续显示四项核心指标、周日期范围和最近任务列表。
- 本周总数和上海时区周边界统计保持正确。
- 生产代码和测试中不再残留 `weekly_chart`、`weekly-bar` 或 `dashboard-chart`。
- 桌面和窄屏布局无明显空洞、贴边或横向溢出。

## 测试命令

- `.venv\Scripts\python.exe -m pytest tests/test_task_query_service.py tests/test_p1_1_db_performance.py tests/test_ui_motion.py -q`
- `.venv\Scripts\python.exe -m pytest -q`
- `.venv\Scripts\python.exe -m compileall -q app tests`
- `node --check app/static/js/motion.js`
- `git diff --check`

## 返回格式

报告修改文件、测试与浏览器验收结果、分支、提交、推送和 PR；不得把本地验收描述为真实平台发布验证。

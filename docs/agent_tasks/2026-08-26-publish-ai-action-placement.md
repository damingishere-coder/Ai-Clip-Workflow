# 发送中心 AI 文案操作分层

## 背景

发送中心顶部的“补充缺失任务”和勾选内容后的“批量 AI 重写文案”位置接近、说明不足，容易被理解成重复操作。实际前者通过 `use_ai=false` 补建遗漏的发布草稿并生成默认封面，后者会逐条调用 AI 并覆盖已选草稿的标题、话题和简介。页面加载时还会静默触发一次旧草稿全局 AI 升级，不符合 AI 必须由用户明确选择后执行的交互边界。

## 目标

- 将补建草稿与 AI 文案操作按“维护动作 / 内容动作”清晰分层。
- AI 重写只保留在内容准备中的单条和显式勾选批量入口。
- 排期计划和执行记录不展示 AI 重写。
- 页面打开不再自动调用旧草稿全局 AI 升级。

## 允许修改范围

- `app/templates/publish.html`
- `app/static/css/styles.css`
- `app/static/js/publish-center.js`
- `tests/test_publish_center_cleanup.py`
- `tests/test_publish_center_browser.py`（仅相关断言需要时）
- `DEVELOPMENT_LOG.md`
- `NEXT_STEPS.md`
- `docs/UI_REFERENCE.md`
- `docs/TASK_FLOW.md`

## 禁止修改范围

- 发布任务、调度器、Publisher 和账号的后端状态机。
- AI Prompt、Provider、模型、认证或计费配置。
- SQLite Schema、迁移和活动数据。
- 真实 AI、封面生成、排期或平台发布操作。

## 已确定实现要求

- 页面顶栏只保留“账号管理”；“补充缺失任务”移入内容准备的维护区，改名为“同步遗漏切片”。
- 维护区明确说明：只补建遗漏草稿和默认封面，不调用 AI、不修改已有文案。
- 单条按钮放在发布文案字段上方，命名为“AI 重写本条文案”，明确会立即生成并保存本条标题、话题和简介。
- 勾选后的按钮命名为“AI 重写已选文案”，仅在内容准备标签页显示；排期与执行记录不显示 AI 重写。
- 移除页面加载时对 `/api/publish/jobs/metadata/upgrade-pending-douyin` 的自动调用；保留后端兼容接口，不删除历史能力。
- 补建遗漏草稿继续固定使用 `use_ai=false`，不改变后端语义。

## 验收标准

- 顶部“账号管理”旁不再出现含糊的“补充缺失任务”。
- 内容准备区能看到“同步遗漏切片”和清晰的无 AI 说明。
- 每条内容卡只显示“AI 重写本条文案”；勾选后显示“AI 重写已选文案”。
- 切换到排期计划或执行记录后，批量 AI 按钮隐藏。
- 打开或刷新 `/publish` 不请求旧草稿全局 AI 升级接口。
- 现有单条、批量、补建、封面和排期后端接口保持不变。
- 桌面与 390px 窄屏布局可读，无横向溢出。

## 测试命令

- `.venv\Scripts\python.exe -m pytest tests/test_publish_center_cleanup.py tests/test_publish_center_browser.py tests/test_publish_copy_rules.py tests/test_publish_scheduler.py tests/test_publish_task_grouping.py tests/test_publish_api_flow.py -q`
- `.venv\Scripts\python.exe -m pytest -q`
- `.venv\Scripts\python.exe -m compileall -q app tests`
- `node --check app/static/js/publish-center.js`
- `.venv\Scripts\python.exe -m ruff check tests/test_publish_center_cleanup.py tests/test_publish_center_browser.py`
- `git diff --check`
- 浏览器验证 `/publish` 的桌面与 390px 窄屏按钮层级，并通过前端请求路径断言确认刷新时没有自动 AI 升级调用。

## 返回格式

报告两个动作的最终区别、AI 按钮放置、修改文件、测试与浏览器证据、分支、提交、推送和 PR；不得把本地页面验证描述为真实 AI 或平台发布验证。

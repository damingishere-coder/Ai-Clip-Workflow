# Design QA

## 2026-06-25 全自动入口与发送中心排期

final result: passed

### 对比证据
- source visual truth path：`docs/design/qa/new-task-before-20260625.png`
- implementation screenshot path：`docs/design/qa/new-task-after-20260625.png`
- full-view comparison evidence：`docs/design/qa/new-task-before-after-20260625.png`
- 发送中心桌面实现：`docs/design/qa/publish-schedule-desktop-20260625.png`
- viewport：新建任务 446px 移动端；发送中心 1280 × 900 桌面端。
- state：未填写新建任务表单；发送中心存在历史发布记录且批量排期未选择任务。
- focused region：对比图重点检查新建任务表单的“全自动模式”到主按钮区域；发送中心截图重点检查批量排期面板。

### Findings
- 没有发现 P0 / P1 / P2 问题。
- 字体与层级：沿用现有中文系统字体、标题权重和蓝色 eyebrow，未引入视觉漂移。
- 间距与布局：移除重复字段后表单明显缩短，卡片内边距、字段间距和按钮高度与现有设计一致。
- 颜色与组件：新增说明、状态标签和排期面板继续使用项目现有蓝色、白色卡片、柔和边框与阴影变量。
- 图片与资产：没有新增或替换品牌图片、图标或插画，现有 Logo 和上传图标保持不变。
- 文案：主按钮改为“创建任务 / 创建并自动处理”，明确创建后自动启动；发送中心文案清楚说明未排期不会自动执行。
- 响应式：移动端排期字段折叠为单列；桌面端四列排期字段没有横向溢出。

### 本轮修正
- 删除新建页重复的自动数量、秒数范围和发布计划字段。
- 增加任务创建后自动运行说明与历史任务继续入口。
- 增加发送中心批量排期面板、计划时间展示和本地时间格式化。
- 更新静态资源版本，确保浏览器加载最新交互。

### Follow-up Polish
- P3：发送记录数量很大时，后续可增加分页或按任务折叠；不影响本次排期功能使用。

## 2026-06-08 全页面 Apple 风格美化

final result: passed

### 参考目标
- 视觉参考：`docs/design/live_streaming_slicing_workflow_ui_16x9.png`
- 设计方向：Apple 风格、浅色玻璃拟态、蓝色主强调色、8px 圆角、清爽后台工作台。

### 已检查页面
- `/` 工作台
- `/tasks` 任务列表
- `/tasks/new` 新建任务
- `/tasks/{task_id}` 任务详情
- `/tasks/{task_id}/clips/review` 片段审核
- `/subtitles` 字幕推送
- `/publish` 发送中心
- `/system` 系统状态

### 验收结果
- 桌面宽度：主页面均正常加载，没有横向溢出。
- 手机宽度：导航压缩为横向条，首屏能看到页面标题和主操作，没有横向溢出。
- 片段审核：桌面宽度恢复左侧候选列表 + 右侧视频预览双栏。
- 顶栏：右上角“新建任务”按钮保持单行。
- 后端编译：`.venv\Scripts\python.exe -m compileall app` 通过。

### 剩余可迭代项
- 后续可单独补一套更完整的图标系统。
- 任务详情顶部多操作按钮较多，后续可按“主操作 + 更多操作”继续精简。

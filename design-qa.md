# Design QA

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

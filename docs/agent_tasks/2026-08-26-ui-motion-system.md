# 全站页面适配动效系统

## 背景

当前页面只有少量按钮 hover、进度条宽度变化和发送中的旋转图标，页面进入、标签切换、抽屉打开、列表新增和实时状态更新缺少统一反馈。用户希望参考 GitHub 上成熟的前端动效项目，为每个页面补充适配动效；没有明确交互价值的区域不强行添加。

调研参考：

- Motion：优先使用浏览器原生动画能力，并尽量只动画 `opacity`、`transform` 等合成属性。
- FormKit AutoAnimate：监听 DOM 真实变化，为新增内容提供短时反馈，不持续制造视觉噪声。
- Animate.css：统一时长和延迟变量，并对 `prefers-reduced-motion` 提供完整降级。
- AOS：页面滚动进入视口时只播放一次，避免往返滚动反复晃动。

本项目是无前端构建步骤的 Windows 本地后台，因此不引入 npm/CDN 依赖，只实现小型原生 CSS/JavaScript 动效层。

## 目标

- 为工作台、任务列表、新建任务、任务详情、完整转写、片段总览、单任务片段审核、字幕总览/工作台、发送中心和系统状态配置适合自身结构的动效。
- 仅工作台周统计与柱图在首次进入时采用一次性渐显/增长；其余页面没有合适入场语义时保持静态，通过真实状态和交互变化提供反馈。
- 实时进度、动态新增行、标签页、弹窗和抽屉的变化有明确反馈。
- 系统启用“减少动态效果”时停用非必要动画和过渡。
- 无 JavaScript、脚本失败或动画 API 不可用时，所有内容和操作仍可见、可用。

## 允许修改范围

- `app/templates/base.html`
- `app/static/css/styles.css`
- `app/static/js/motion.js`（新增）
- `app/static/js/app.js`（仅 reduced-motion 滚动降级）
- `app/static/js/publish-center.js`（仅 reduced-motion 滚动降级）
- `tests/test_ui_motion.py`（新增）
- 与本任务直接相关的现有前端测试（仅确有需要时）
- `DEVELOPMENT_LOG.md`
- `NEXT_STEPS.md`
- `docs/UI_REFERENCE.md`
- 本任务文件

## 禁止修改范围

- FastAPI 路由、业务服务、SQLite Schema、迁移和活动数据。
- AI Prompt、Provider、认证、计费或模型配置。
- 转写、切片、字幕烧录、排期、Scheduler、Publisher 和平台发送状态机。
- 真实 AI、封面生成、排期、Windows Worker 发布或平台投稿。
- React/Vue、npm 打包流程、远程 CDN 和第三方运行时依赖。

## 已确定实现要求

- 使用路由级页面配置映射不同选择器，不给所有卡片套同一种动画。
- 初次进入只播放一次；列表最多采用短延迟错峰，不能让内容等待过久。
- 工作台周统计和柱图采用一次性反馈；新建任务只反馈条件设置区和文件选择；任务详情只反馈真实变化的状态、进度和操作数据；片段审核、字幕工作台和发送中心只反馈进度、标签、弹层、抽屉及用户明确触发的新增内容。
- 任务列表、完整转写、片段总览、字幕总览和系统状态没有合适的页面入场语义，保持静态布局，仅使用全局按钮、链接、行状态、焦点和展开收起过渡，不逐行制造动画。
- 不对视频播放、字幕波形、滚动日志、输入中的文本和持续轮询本身添加循环动效。
- 动态 DOM 动效限制在排期预览、AI 分析结果和 AI 历史等用户触发的新增项，不监听或动画轮询重建的执行记录、日历、字幕虚拟列表及整个页面的每次字符变化。
- `prefers-reduced-motion: reduce` 下动画时长和过渡时长压缩到近零，关闭非必要旋转；脚本也应跳过初始/动态动效注册。
- 现有 `scrollIntoView({ behavior: "smooth" })` 在 reduced-motion 下改用即时滚动，不能只关闭 CSS 动画。
- 动画只改变呈现，不改 DOM 语义、业务请求、按钮可用性和已有选择器。

## 验收标准

- 所有正式页面都能得到路由匹配的动效配置，未知路由保持静态可用。
- 动画脚本是本地静态资源，不访问外网，不引入新依赖。
- 工作台周统计与柱图首次进入时短时播放一次；其他静态页面不为了凑数量添加整页入场动画。
- 工作台柱状图、详情进度、弹窗/抽屉、发送中心标签切换和动态新增行有平滑反馈。
- 长文本、日志、视频和字幕编辑高频区域不发生反复闪烁或位移。
- 390px 窄屏无横向溢出；动效不改变最终布局尺寸。
- 减少动态效果模式下内容立即可见，动画和过渡被禁用。
- 现有全量测试通过，JavaScript 语法、Ruff、Compileall 和 `git diff --check` 通过。

## 测试命令

- `.venv\Scripts\python.exe -m pytest tests/test_ui_motion.py -q`
- `.venv\Scripts\python.exe -m pytest -q`
- `.venv\Scripts\python.exe -m compileall -q app tests`
- `node --check app/static/js/motion.js`
- `.venv\Scripts\python.exe -m ruff check tests/test_ui_motion.py`
- `git diff --check`
- 浏览器逐页检查桌面端与 390px 窄屏；检查 `data-motion-page`、初始动画、动态面板动画和 reduced-motion CSS 降级，不点击真实 AI、切片、封面、排期或发送按钮。

## 返回格式

报告 GitHub 调研取舍、各页面动效映射、明确未添加动效的区域、修改文件、测试与浏览器证据、分支、提交、推送和 PR；不得把本地前端验收描述为真实业务流程或平台发布验证。

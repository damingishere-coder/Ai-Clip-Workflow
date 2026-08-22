# 发送中心账号、平台与抖音文案规则修复任务

## 背景

发送中心存在账号已由发送就绪逻辑自动匹配、内容卡片仍显示“缺少：发布账号”的状态不一致；B 站入口和自动任务仍在生成；任务组“全选本任务”交互缺失；抖音标题、标签和简介在 AI、手工保存、自动流水线与最终发布之间使用了不同限制。当前主工作区属于 PR #38，且包含用户未提交的原生启动脚本修改，因此本任务只在隔离 worktree 中实施。

## 目标

1. 统一账号解析与内容状态，唯一正常抖音账号不再误报缺失。
2. 发送中心前台与自动同步只启用抖音，同时保留 B 站历史、API 和 Publisher。
3. 恢复每个原始任务组的“全选本任务”。
4. 统一抖音标题、标签、简介规则，并确保 AI 重写同步实际发送字段。
5. 对旧的未排期抖音草稿执行幂等的一次性自动升级，失败时保留原文。

## 允许修改范围

- 发送中心页面模板、样式和 JavaScript。
- 发布任务模型、路由、就绪判断、发布服务、自动流水线元数据生成与相关测试。
- 为一次性升级复用或新增的发布数据库备份调用，但不新增数据库字段。
- `DEVELOPMENT_LOG.md`、`NEXT_STEPS.md`、`docs/UI_REFERENCE.md` 及相关产品/技术说明。

## 禁止修改范围

- 不删除或迁移现有 B 站任务、执行记录、账号、视频或发布器代码。
- 不覆盖已排期、发送中、历史、用户移除或失效切片的文案。
- 不读取、输出或提交 `.env`、API Key、Token、Cookie、浏览器 Profile 或其他 secrets。
- 不修改主工作区 PR #38 的未提交文件，不自动合并 PR、删除分支或重写 Git 历史。
- 不切换前端技术栈，不改 React/Vue，不进行生产发布或远程服务器操作。

## 已确定实现要求

- 抖音标题 AI 目标 18～26 字，硬上限 30 字；B 站后端标题上限保持 80 字。
- 抖音标签 4～6 个，每个 2～3 字；简介 15～35 字，突出一个冲突、笑点或悬念并去除模板化结尾。
- 唯一正常抖音账号可作为有效账号；多账号需人工选择，失效账号提示登录。
- 任务组全选恢复旧版复选框、取消全选和半选态，且不跨任务组。
- 自动同步默认只创建抖音任务；显式 B 站后端接口和历史数据继续可用。
- 一次性升级只处理有效输出、抖音、DRAFT/WAITING、无排期、旧规则版本的草稿；写入前备份数据库，并以现有 `provider_response` 记录规则版本和结果。
- AI 失败不覆盖原标题、简介和标签；手动批量重写可再次尝试。

## 验收标准

- 唯一正常抖音账号下，内容卡片不再显示“缺少：发布账号”，保存后账号落库。
- B 站切换入口不可见，`platform=bilibili` 不能把发送中心切到 B 站；新自动任务只创建抖音记录。
- 每个任务组可独立全选、取消全选和显示半选态，折叠状态不影响组内选择。
- 所有新生成或重写的抖音标题、标签、简介满足长度和数量规则，排期前即可识别违规手工内容。
- AI 重写同时更新 `description/caption` 与 `tags/hashtags`，批量操作后页面输入框立即刷新。
- 一次性升级幂等，已排期和 B 站记录不变，失败项原文不变。
- 相关单元、集成和浏览器测试通过，完整 pytest 无新增失败。

## 测试命令

在隔离 worktree 中使用主项目虚拟环境：

```powershell
& 'C:\Users\10578\Documents\New project 2\.venv\Scripts\python.exe' -m pytest tests/test_publish_copy_rules.py tests/test_publish_readiness.py tests/test_publish_job_lifecycle.py tests/test_local_browser_publishers.py tests/test_publish_task_grouping.py tests/test_publish_task_linkage.py tests/test_auto_pipeline.py tests/test_publish_center_browser.py -q
& 'C:\Users\10578\Documents\New project 2\.venv\Scripts\python.exe' -m pytest -q
```

## 返回格式

Luna Operator 仅返回：

1. 执行的原始命令。
2. 每条命令的退出码、通过/失败/跳过数量。
3. 失败测试的完整测试名和最小相关堆栈。
4. 是否产生范围外 tracked 修改。
5. 不修改、修补或重构任何生产源代码。

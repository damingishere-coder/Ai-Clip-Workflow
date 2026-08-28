# 2.2 抖音内容复盘与 Prompt 归因任务

## 背景

2.1.0 已通过顶层 PR #60 合并到 `master`。当前抖音发布记录能够关联成片和候选片段，但候选缺少来源 AI Run，AI Run 也没有不可变 Prompt 版本；审片开关不会自动沉淀反馈。用户提供的抖音 Excel 是账号级日汇总，只能作为历史趋势基线，不能直接归因到作品或 Prompt。

## 目标

1. 审片保存时把启用/停用自动记录为保留/淘汰反馈，相同状态不重复写入。
2. 建立作品指标 → 发布记录 → 成片 → 候选 → AI Run → Prompt 版本的可靠关联。
3. 新增独立“内容复盘”页面，支持日汇总 Excel/CSV 两阶段导入、最近 50 条抖音作品的一键只读同步预览、人工匹配和 Prompt 对比。
4. 以三个已确认周周期和明确样本门槛生成调整建议，不自动修改 Prompt。
5. 只补旧迁移停用、恢复完整性校验和 readiness，不进行全面重构。

## 允许修改范围

- `app/db/database.py` 及新的账本迁移、索引和必要模型字段。
- AI 分析、Prompt 预设、审片反馈和任务审核相关 service/router/template/JS。
- 新的内容复盘 service/router/template/独立 JS/CSS，以及 `app/main.py`、`base.html` 导航。
- `scripts/publish_host_worker.py` 的隔离只读 analytics 路由及现有 BrowserRuntime 的最小复用。
- `database_backup_service.py`、`backup_restore.py`、旧任务目录迁移脚本的必要安全护栏。
- `requirements.in`、`requirements.txt`、测试、版本说明和项目文档。

## 禁止修改范围

- 不运行或修改活动数据库，不导入附件到正式库，不停启正式服务。
- 不调用真实 AI、FFmpeg、Chrome 同步、抖音/B站发布或远程平台写接口。
- 不保存 Cookie、Token、原始平台响应、截图或上传的 Excel 原文件。
- 不绕过登录、验证码、二维码、滑块、429 或平台风控；不做自动重试。
- 不开发 B站复盘、评论采集、多账号分析、后台定时抓取、React/Vue 或全面 God Service 重构。
- 不自动合并 PR、不删除分支、不 force push。

## 已确定实现要求

- 新 Schema 使用迁移账本和 checksum；历史关联只有唯一可证明时才回填，否则保持空值。
- Prompt 在一次分析开始时只读取一次不可变快照，Run 与候选共用同一 `run_id` 和 Prompt 版本。
- 审片反馈与候选更新在同一 `BEGIN IMMEDIATE` 事务完成；首次审核或决策/原因变化才写事件。
- Excel 仅支持 `.xlsx/.csv`，最大 10MB、10000 行、50 列；预览批次 24 小时过期，原文件不落库，文件哈希幂等。
- 附件字段作为 `douyin_daily_xlsx` 账号级快照，不参与作品级归因。
- Worker 复用现有登录态和账号锁，只返回白名单指标；固定 fail-closed 错误码，不新增 Cookie 存储。
- 作品匹配优先平台 ID；次选同账号唯一标题、发布时间 ±10 分钟和时长；歧义必须人工确认。
- Prompt 版本满 3 个周批次且准确关联作品不少于 30 条才可评估；版本对比要求双方不少于 20 条。
- `/health` 保持轻量；新的 readiness 默认做快速检查，`deep=1` 才执行完整数据库诊断。
- 旧目录迁移脚本无条件退出并指向受支持流程，文件保留作历史记录。

## 验收标准

- 开关保存自动生成正确反馈；重复保存不重复，旧显式反馈接口保持兼容。
- 新分析候选可追到准确 Run 和 Prompt 版本；运行中修改预设不会污染本次快照。
- 附件同结构文件能预览、确认并形成账号级趋势；重复、损坏、越界、宏和多义工作表均 fail closed。
- 最近 50 条同步在假 Worker 中可预览；登录失效、验证码、429、页面变化无自动重试。
- 内容复盘页面在桌面和 390px 可用，日汇总不显示作品级有效性结论。
- 恢复校验包含 `integrity_check`、`foreign_key_check`、迁移账本和索引；旧迁移脚本无法执行。
- 定向测试、全量 pytest、Ruff、Compileall、JavaScript、PowerShell、Compose 和 `git diff --check` 通过。

## 测试命令

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_content_review.py tests/test_clip_review_publish_sync.py tests/test_ai_job_consistency.py tests/test_schema_migration_ledger.py tests/test_database_backup_service.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check app tests scripts/seed_demo_data.py scripts/backup_restore.py scripts/backup_restore_runtime.py
.\.venv\Scripts\python.exe -m compileall -q app scripts
Get-ChildItem app/static/js -Filter *.js | ForEach-Object { node --check $_.FullName }
git diff --check
```

## 返回格式

- 列出功能、数据库迁移、页面、Worker 和护栏的实际修改。
- 列出测试命令、通过数量、提交哈希、分支、push 和 PR 链接。
- 明确本轮未迁移活动库、未真实同步抖音、未修改 Prompt、未触发真实发布。

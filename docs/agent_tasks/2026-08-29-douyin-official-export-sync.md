# 抖音官方作品报表全量同步任务

## 背景

内容复盘页原“同步最近 50 条作品”依赖监听创作者中心作品接口。抖音当前作品列表已改用 `work_list`，旧过滤器无法识别，因此误报 `PAGE_CHANGED`。用户确认不继续修补私有页面接口，改为复用创作者中心“导出数据”产生的官方 `作品列表导出.xlsx`。

## 目标

1. Windows Chrome Worker 自动点击“导出数据”，下载并解析官方作品报表。
2. 自动同步和人工上传共用同一作品报表解析、幂等和归因逻辑。
3. 全量保存官方 16 列作品数据，不再限制最近 50 条。
4. 保留既有账号日汇总导入、历史作品快照和人工匹配能力。

## 允许修改范围

- `scripts/publish_host_worker.py`、Worker Client、内容复盘路由与页面。
- 内容复盘服务、数据库迁移账本和作品快照查询。
- 相关测试、`DEVELOPMENT_LOG.md`、`NEXT_STEPS.md`、数据库和 UI 文档。

## 禁止修改范围

- 不修改 Prompt、AI 分析、投稿、排期或发布状态机。
- 不保存 Cookie、Token、原始平台响应或原始 Excel。
- 不绕过登录、验证码、429 或平台风控，不自动重试下载。
- 不删除历史批次、账号趋势数据或用户文件。
- Luna 子代理不得修改生产代码、提交、推送、重启服务或访问 secrets。

## 已确定实现要求

- 官方作品表严格识别 16 列表头；`.xlsx` 最大 10MB、10,000 行、50 列。
- 发布时间按 `Asia/Shanghai` 解析；空值和 `-` 归一为 `None`。
- 自动和人工作品导入统一使用 `source_kind=douyin_item_export` 及规范化数据哈希。
- 无平台作品 ID 时生成 `export:<标题+发布时间 SHA-256>` 内部键，页面不冒充平台 ID展示。
- 匹配保留旧平台 ID 精确路径；官方导出按同账号、发布时间 ±10 分钟、标题/描述/正文精确或包含关系匹配，包含文本至少 8 个字符，只有唯一候选才自动关联。
- 新迁移只追加 `completion_rate`、`home_visit_count`、`follower_gain_count`、`content_genre`、`audit_status`，不得修改已应用迁移 checksum。
- Worker 使用账号锁、`expect_download()` 和浏览器临时下载；只返回白名单规范化 JSON，不返回本机路径。
- 登录、验证码、页面变化、429、下载失败和报表损坏固定 fail-closed，不自动重试。

## 验收标准

- 官方 16 列、全部有效作品能由人工上传和 Worker 下载两条路径导入。
- 同一规范化数据跨人工/自动路径重复导入返回 `already_imported`。
- 完整官方指标持久化并在作品归因表展示；账号趋势表保持原语义。
- 匹配唯一性、歧义和标题修改后的最新快照行为有测试覆盖。
- 定向测试、完整 `tests`、Ruff、Compileall、JavaScript、浏览器回归和 `git diff --check` 通过。
- 活动库升级前完成备份和完整性校验；只重启 8001/8765；真实同步两次证明导入成功和幂等，且投稿/排期状态不变。

## 测试命令

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_content_review.py tests/test_douyin_analytics_worker.py tests/test_schema_migration_ledger.py tests/test_content_review_browser.py
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\ruff.exe check app tests scripts/publish_host_worker.py
.\.venv\Scripts\python.exe -m compileall -q app scripts
Get-ChildItem app/static/js -Filter *.js | ForEach-Object { node --check $_.FullName }
git diff --check
```

## 返回格式

- 列出实际功能、数据库、Worker、页面、测试和现场验收结果。
- 报告备份路径、迁移状态、8001/8765 PID 前后、同步/幂等统计。
- 报告提交哈希、分支、push 和 PR 链接；不自动合并 PR。

## 现场验收记录

- 迁移前自动在线备份和停机后的同步前备份均通过完整性与外键检查；迁移账本 checksum 已核对。
- 两次真实导出均为 107 行，每批唯一匹配 104、歧义 0、未匹配 3；第二次有 5 条官方实时指标变化，因此形成新的规范化内容哈希。
- 对第二批规范化载荷做不访问平台的原样重放后返回 `already_imported`，批次和快照数量均未增长，验证相同内容幂等。
- 同步前后发布任务数量、状态和状态哈希一致，`PUBLISHING=0`，未触发投稿或排期变更。

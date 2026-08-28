# NiuMa Studio 全面工程体检任务书

## 背景

当前项目为 Windows 本地 AI 高光生产后台 V2.1.0。本轮联合使用 Codemap、Code Overhaul 与本地 SonarQube，回答项目为什么能运行、哪些部分可靠、哪些部分存在高风险技术债，以及应按什么顺序低风险整改。

## 目标

- 建立当前项目的功能模块图、依赖图、核心数据流与状态流。
- 审计架构、业务、代码、数据、稳定性、测试、安全、性能、可观测性和维护性。
- 运行现有安全测试、Lint、覆盖率与 SonarQube 静态扫描。
- 交叉验证三方结果并生成 `PROJECT_AUDIT.md`。

## 允许修改范围

- `.codemap/` 下的 Codemap 状态、配置和生成报告。
- `sonar-project.properties` 等仅用于本轮静态扫描的工程配置。
- `AUDIT_TASK.md`、`PROJECT_AUDIT.md`。
- 按项目规则追加 `DEVELOPMENT_LOG.md`、`NEXT_STEPS.md` 的审计记录。

## 禁止修改范围

- `app/`、`scripts/`、`tests/`、`prompts/` 中的生产逻辑和测试逻辑。
- 数据库 Schema、Migration、真实数据、浏览器登录态、发布队列和任务文件。
- `.env`、Cookie、Token、API Key、浏览器数据及任何秘密内容。
- 依赖版本、运行时行为、外部平台状态与远端系统。

## 已确定实现要求

- Codemap 以功能模块为单位，核心/高耦合模块独立评分，小型叶子模块可同一子任务内分别评分。
- Code Overhaul 使用 FULL AUDIT 模式，不在各章节暂停整改。
- SonarQube 优先复用现有本地容器；无法获得的指标必须明确写为“未取得”，不得估算成 Sonar 指标。
- Dead/Legacy/Mock/兼容代码只列出，不删除。
- 自动化测试不得连接真实 AI Provider 或触发真实投稿。

## 验收标准

- `PROJECT_AUDIT.md` 包含用户要求的全部章节、100 分健康度、Sonar 指标、P0-P3、Top 10、删除候选、暂不修改区和可独立回滚的整改路线图。
- 关键发现有 `file:line` 证据；Sonar 事实与人工审查结论明确区分。
- 记录实际测试命令、退出码、通过/失败/跳过数量和覆盖率。
- 最终 `git diff` 不包含生产代码、数据库、日志、缓存或敏感信息。

## 测试与扫描命令

- `.venv\Scripts\python.exe -m pytest`
- `.venv\Scripts\python.exe -m ruff check app tests scripts/seed_demo_data.py scripts/backup_restore.py scripts/backup_restore_runtime.py`
- `.venv\Scripts\python.exe -m coverage run -m pytest`（仅在 Coverage 可用时）
- Sonar Scanner（使用本地 SonarQube 与隔离的扫描配置）

## 返回格式

- 子代理返回精确命令、退出码、耗时与 `file:line` 证据。
- 主代理统一输出 `PROJECT_AUDIT.md`，并说明审计产物、测试结果、Git 提交、推送与 PR 状态。

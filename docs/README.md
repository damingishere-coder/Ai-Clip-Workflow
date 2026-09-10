# 牛马片场文档中心

适用版本：**2.3.0**。先按目的选择入口，不需要从头读完所有文档。

## 先看项目进度

| 想了解什么 | 阅读入口 |
| --- | --- |
| 现在完成到哪、是否发布和部署 | [项目当前进度](../PROJECT_STATUS.md) |
| 接下来做什么 | [下一步](../NEXT_STEPS.md) |
| 后续优先级 | [唯一路线图](../ROADMAP.md) |
| 为什么这样改、当时如何验证 | [开发日志](../DEVELOPMENT_LOG.md) |

进度页记录有日期的交付事实；任务页面记录实时处理状态。开发日志、历史待办和审计报告不代替当前运行核验。

## 用户文档

- [通用启动指南](PORTABLE_SETUP.md)：初始化、环境体检、正式模式、Demo、开发模式、真实发布和 Windows 验收。
- [新手启动指南](PROJECT_GUIDE.md)：环境准备、配置、启动、第一次测试和常见问题。
- [数据备份、恢复与升级保护](BACKUP_AND_RESTORE.md)：SQLite、`.env`、媒体文件、恢复回滚和升级前保护。
- [技术参考](TECHNICAL_REFERENCE.md)：架构、存储、AI、Scheduler、Worker、排期和发布状态。

## 维护与发布

- [Windows 实机验收与发布证据](WINDOWS_ACCEPTANCE.md)：验收脚本、数据隔离指纹、脱敏报告、Issue #23 证据和发布门禁。
- [依赖维护策略](DEPENDENCY_POLICY.md)：版本固定、升级流程、CI 验证和重点依赖风险。
- [v2.3.0 Release 检查清单](RELEASE_CHECKLIST.md)：自动化、Windows 实机、隐私和发布后检查。
- [路线图](../ROADMAP.md)：已完成基础建设、公开 Issue 和后续优先级。
- [更新日志](../CHANGELOG.md)：面向使用者的重要版本变化。

## 项目治理

- [贡献指南](../CONTRIBUTING.md)：Issue、开发环境、测试和 Pull Request 规则。
- [安全策略](../SECURITY.md)：API Key、Cookie、本地数据和漏洞报告方式。

## 历史与审计材料

- [历史待办](../NEXT_STEPS_HISTORY.md)、[历史路线图](ROADMAP_HISTORY.md)：完整保留旧计划，仅用于追溯。
- `docs/agent_tasks/`：按日期保存的任务方案和交付记录，不等于全部已进入 master。
- 根目录 `PROJECT_REAUDIT.md` 与 `.codemap/`：专项审计材料，可能包含本地未提交修改；本次未覆盖或混入提交，其结论应结合审计日期阅读。

## 其他现有文档

仓库中可能还保留架构、状态流、部署、开发记录或历史验收文档。它们用于维护和追踪实现细节；第一次使用时不需要全部阅读。

后续整理原则：

- 面向普通用户的操作说明放在用户文档。
- API、状态机和发布链路放在技术参考。
- 依赖、发布、备份和验收规则放在维护与发布文档。
- 开发过程和历史方案放入开发或归档目录。
- README 只保留产品价值、快速开始和关键入口。

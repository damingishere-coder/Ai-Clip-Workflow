# 牛马片场文档中心

这里存放 README 不适合展开的安装、使用、技术和维护说明。

## 用户文档

- [通用启动指南](PORTABLE_SETUP.md)：初始化、环境体检、正式模式、Demo、开发模式、真实发布和 Windows 验收。
- [新手启动指南](PROJECT_GUIDE.md)：环境准备、配置、启动、第一次测试和常见问题。
- [技术参考](TECHNICAL_REFERENCE.md)：架构、存储、AI、Scheduler、Worker、排期和发布状态。

## 维护与发布

- [依赖维护策略](DEPENDENCY_POLICY.md)：版本固定、升级流程、CI 验证和重点依赖风险。
- [v2.0.0 Release 检查清单](RELEASE_CHECKLIST.md)：自动化、Windows 实机、隐私和发布后检查。
- [路线图](../ROADMAP.md)：已完成基础建设、公开 Issue 和后续优先级。
- [更新日志](../CHANGELOG.md)：面向使用者的重要版本变化。

## 项目治理

- [贡献指南](../CONTRIBUTING.md)：Issue、开发环境、测试和 Pull Request 规则。
- [安全策略](../SECURITY.md)：API Key、Cookie、本地数据和漏洞报告方式。

## 其他现有文档

仓库中可能还保留架构、状态流、部署、开发记录或历史验收文档。它们用于维护和追踪实现细节；第一次使用时不需要全部阅读。

后续整理原则：

- 面向普通用户的操作说明放在用户文档。
- API、状态机和发布链路放在技术参考。
- 依赖、发布和验收规则放在维护与发布文档。
- 开发过程和历史方案放入开发或归档目录。
- README 只保留产品价值、快速开始和关键入口。

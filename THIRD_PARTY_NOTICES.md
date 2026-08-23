# 第三方参考与许可证说明

牛马片场 v2.1.0 的发布模块在设计阶段参考了以下开源项目和官方文档。项目没有整仓复制这些外部代码，平台 Publisher、Registry、调度器和 Windows Worker 均按牛马片场现有 FastAPI / SQLite 架构独立实现。

## social-auto-upload-web-ui

- 项目：https://github.com/DevilJie/social-auto-upload-web-ui
- 参考范围：平台注册、统一接口、任务队列、发布状态、历史记录和批量排期的设计模式。
- 许可证：请以该项目仓库当前提供的许可证文件为准。

## social-auto-upload

- 项目：https://github.com/dreammis/social-auto-upload
- 参考范围：抖音与 B站投稿步骤、登录态检查、视频校验、表单填写、上传完成与异常处理思路。
- 许可证：请以该项目仓库当前提供的许可证文件为准。
- 本项目未把该仓库作为运行时黑盒依赖，也未复制整个 uploader 目录。

## Playwright for Python

- 项目与文档：https://playwright.dev/python/
- 用途：Windows Worker 启动系统 Chrome 持久化上下文，并为每个平台/账号使用独立用户目录。
- 许可证：Apache License 2.0（以 Playwright 官方仓库许可证为准）。

第三方网站和平台名称、商标及页面属于各自权利人。使用本项目投稿时，用户仍需遵守抖音、哔哩哔哩及浏览器相关服务条款，不得使用本项目绕过验证或平台风控。

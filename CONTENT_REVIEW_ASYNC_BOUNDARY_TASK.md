# 阶段 3：内容复盘异步边界与陈旧预览保护

## 背景

第二次工程复检确认两项仍会影响真实使用的问题：官方作品导出同步在 FastAPI
异步路由内执行阻塞式 Worker 请求；导入预览请求返回较慢时，旧账号或旧文件的
响应可能覆盖当前页面上下文。

## 目标

1. 将“账号解析、Worker 官方导出、行数校验、SQLite 提交”整体移出事件循环。
2. 为导入预览增加请求代际和取消保护，旧响应不得恢复旧批次或确认按钮。
3. 保持现有错误码、事务边界、真实发布和 Provider 行为不变。

## 允许修改范围

- `app/routers/content_review.py`
- `app/static/js/content-review.js`
- `app/templates/content_review.html`
- `tests/test_content_review.py`
- `tests/test_content_review_browser.py`
- `docs/UI_REFERENCE.md`
- `DEVELOPMENT_LOG.md`
- `NEXT_STEPS.md`
- 本任务文件

## 禁止修改范围

- 不改 Windows Worker、真实浏览器、发布调度器或平台投稿链路。
- 不改数据库结构、活动数据库、Provider 配置或依赖文件。
- 不把官方导出改为自动重试或可取消的后台任务。
- 不扩展为发布中心跨服务事务重构。

## 已确定实现要求

- 使用项目现有的 `starlette.concurrency.run_in_threadpool` 风格。
- 原有 `ContentReviewError`、`PublishError` 和 `PublishWorkerUnavailable` 映射保持不变。
- 预览请求捕获代际、账号和 `File` 对象；仅当前上下文可写入 DOM。
- 文件或账号切换时使旧预览失效，并安全忽略 `AbortError`。
- 更新静态脚本版本参数，避免浏览器继续使用旧缓存。

## 验收标准

- 阻塞导出链路在事件循环线程之外执行，并保持成功及错误响应契约。
- Worker 行数不一致时不提交任何导出数据。
- 预览期间切换文件或账号后，旧响应不能显示预览、恢复批次或启用确认按钮。
- 专项测试、全量测试、Ruff、JS 语法检查、PowerShell Parser、Compose 配置、
  `compileall`、`pip check` 与 `git diff --check` 通过。
- 最终 diff 仅包含允许路径，无依赖变化、敏感信息、TODO/debug 或临时产物。

## 测试命令

```powershell
.venv\Scripts\python.exe -m pytest -q tests/test_content_review.py tests/test_content_review_browser.py tests/test_douyin_analytics_worker.py
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check app tests scripts
node --check app/static/js/content-review.js
```

## 返回格式

报告修改文件、专项/全量测试结果、静态检查结果、范围审计和未覆盖的外部边界；
不得宣称触发过真实导出、发布、Provider 调用或活动数据库迁移。

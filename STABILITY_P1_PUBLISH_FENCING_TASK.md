# P1B.2 发布执行代际与 Worker 幂等整改任务

## 背景

工程审计确认 `publish_jobs.execution_id` 已能标识一次发布执行，但数据库结果写回、重启恢复、旧任务修复和 Windows Worker journal 尚未完整使用该代际。旧执行在任务重新排队并被新执行领取后，仍可能覆盖新状态；重复 HTTP 请求也可能再次进入真实 Publisher。

## 目标

1. 所有发布中的阶段、平台结果和终态写回必须同时匹配 `job_id + PUBLISHING + execution_id`。
2. 重启恢复和 Worker 断连处理只允许改变读取时捕获的 execution；代际已变化时返回 skipped，不记录伪事件。
3. `repair_and_publish` 对源状态复核、既有替代任务检查、克隆和事件写入在同一 `BEGIN IMMEDIATE` 事务中完成。
4. Windows Worker 对同一 execution 串行执行；终态请求直接返回既有结果，危险中间阶段不重复投稿。
5. Worker 使用的 `job_id`、`execution_id`、`account_id` 必须经过 Windows 安全标识校验，不能影响 journal、浏览器 profile 或截图目录边界。

## 允许修改范围

- `app/services/publish_repository.py`
- `app/services/publish_scheduler.py`
- `app/services/publish_service.py`（仅统一 publish_jobs 乐观并发时间版本）
- `app/services/publish_time.py`（仅生成微秒级并发版本时间）
- `app/services/publish_executor.py`
- `app/services/publishers/worker_client.py`
- `app/services/publishers/browser_runtime.py`
- `app/services/publishers/local_browser.py`（仅在 Worker 投稿前触发 dispatch CAS）
- `app/services/publishers/base.py`
- `app/services/publishers/manual_export.py`
- `scripts/publish_host_worker.py`
- `app/main.py`（仅 Scheduler 后台 Task 的优雅停机）
- 发布调度、Worker、幂等与安全标识相关测试
- `PROJECT_AUDIT.md`、`DEVELOPMENT_LOG.md`、`NEXT_STEPS.md`、`.codemap/*`

## 禁止修改范围

- 不改变平台页面脚本、真实投稿步骤、账号认证方式或平台风控边界。
- 不触发真实抖音/B站投稿、真实账号登录或真实 AI 调用。
- 不修改用户活动数据库，不删除 journal、媒体、发布包或浏览器 profile。
- 不引入消息队列、分布式锁、微服务或新数据库。

## 已确定实现要求

- Scheduler claim 返回并保留本次 `execution_id`；生产 executor 在真正调用 Publisher 前以 `execution_id + updated_at` 原子保留 dispatch 权，恢复扫描只能写回自己读取的旧快照。
- `record_provider_result` 与 `update_execution_phase` 支持 expected execution 条件并返回是否写入。
- PUBLISHING 终态函数必须显式接收 expected execution；人工/上传前状态变化必须显式限定来源状态。
- 数据库 UPDATE 未命中时必须回滚同事务 provider result，不写事件、不追加成功/失败日志。
- Worker journal 保存不可变执行身份；相同 execution 的终态只有在 `job_id/platform/account_id` 全部匹配时才可重放，身份冲突或缺失 fail closed。
- 同一 publish job 的旧 execution 一旦进入上传、提交或不确定终态，后续不同 execution 会被 Worker 的持久化 journal 证据阻断，不能再次进入 Publisher。
- job、execution 与账号互斥同时覆盖进程内和操作系统级跨进程文件锁；进程崩溃后由操作系统释放，不能通过竞态删除新锁并重复进入真实 Publisher。
- `received/browser_opening/browser_opened/rejected` 才允许同 execution 在进程重启后安全续试；上传开始后的非终态一律转人工复核，禁止重复 Publisher 调用。
- 标识仅允许 ASCII 字母、数字、点、下划线和连字符；禁止路径分隔符、`..`、控制字符、盘符和 Windows 保留名。
- Scheduler 保存后台 Task 引用；应用关闭时先停止新扫描，再等待当前一轮安全结束。
- 本地发布包先在同卷 staging 目录完整生成，再原子切换；中途失败保留上一份完整包。

## 验收标准

- 旧 execution 的 provider result、PUBLISHED/FAILED/NEED_REVIEW/重排队写回全部被拒绝。
- 代际不匹配时事件数量不增加，最新任务字段不被污染。
- 并发两次旧任务修复只产生一个替代任务。
- 同一 Worker execution 的并发/重复请求只调用一次 Mock Publisher；终态结果可重复读取。
- execution 身份冲突、路径穿越、Windows 保留名均在创建路径前被拒绝。
- 相关测试、全量 Pytest、Ruff、compileall 与 `git diff --check` 通过。

## 测试命令

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_publish_fencing.py tests/test_publish_scheduler_state_machine.py tests/test_publish_scheduler.py tests/test_publish_worker_client.py tests/test_local_browser_publishers.py tests/test_publish_readiness.py tests/test_publish_api_flow.py -q
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check app scripts tests
.\.venv\Scripts\python.exe -m compileall -q app scripts
git diff --check
```

## 返回格式

- 修改文件和关键行为
- 定向/全量测试准确数量与结果
- 未触发真实发布的证据
- 剩余风险和下一轮范围

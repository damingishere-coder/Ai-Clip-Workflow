<!--
  This file:        .codemap/codemap.md   (written report)
  Interactive map:  .codemap/codemap.html
-->

# NiuMa Studio — Functional Module Quality Audit

> **Interactive view:** [`.codemap/codemap.html`](codemap.html) — per-module scores, findings, LoC, and the dependency graph. This file is the written report.

**Generated:** 2026-08-24 · **Modules:** 13 · **Size:** 49906 tracked LoC across 132 files

## Health by layer

| Layer | Modules | Avg score |
|---|--:|--:|
| 界面 · API | 2 | 62 |
| 业务编排 | 3 | 62 |
| 媒体与 AI 处理 | 4 | 69 |
| 外部执行边界 | 2 | 74 |
| 持久化与运维 | 2 | 68 |

## Per-module lines of code & score

_LoC is the representative file/folder per module; folder-level modules overlap and are not additive._

### 界面 · API

| Module | LoC | Score | Tags |
|---|--:|:--|:--|
| Frontend UI | 13,840 | 65 C | god-component, bloat, glue, duplication, legacy |
| API & Runtime | 2,749 | 58 D | fallback, legacy, dual-format, duplication, glue, bloat, god-component, silent-except, any-escape |

### 业务编排

| Module | LoC | Score | Tags |
|---|--:|:--|:--|
| Publish Center | 5,593 | 52 D | god-component, bloat, legacy, dual-format, fallback, silent-except, placeholder, duplication |
| Task Review & Cut | 2,462 | 60 C | god-component, glue, duplication, dual-format, fallback, legacy, over-fit, silent-except |
| Pipeline & Job Queue | 2,048 | 74 C | fallback, silent-except, legacy, stub, god-component, glue, over-fit |

### 媒体与 AI 处理

| Module | LoC | Score | Tags |
|---|--:|:--|:--|
| AI Selection | 4,420 | 63 C | fallback, silent-except, legacy, dual-format, stub, fake-output, bloat, duplication, god-component |
| Subtitle | 2,409 | 70 C | fallback, silent-except, legacy, bloat, god-component, monkeypatch |
| Transcription | 1,960 | 59 D | fallback, silent-except, fake-output, duplication, god-component, glue, over-fit |
| Media & Storage | 1,614 | 84 B | legacy, dual-format, glue, fallback, silent-except |

### 外部执行边界

| Module | LoC | Score | Tags |
|---|--:|:--|:--|
| Publishers & Worker | 2,699 | 76 B | legacy, glue, silent-except, fallback |
| Publish Scheduler | 2,329 | 71 C | god-component, bloat, legacy, silent-except |

### 持久化与运维

| Module | LoC | Score | Tags |
|---|--:|:--|:--|
| Ops & Delivery | 4,347 | 68 C | fallback, legacy, dual-format, duplication, bloat, glue, silent-except, monkeypatch |
| SQLite Persistence | 3,436 | 69 C | silent-except, fallback, legacy, dual-format, glue, bloat, god-component, monkeypatch |

## Worst offenders

- **Publish Center (52/D)** — app/services/publish_service.py:570: _normalize_config/_normalize_account 仅新增 *_masked 字段，却保留 client_secret、access_token、refresh_token 原字段；GET /api/publish/platforms、/accounts 及保存配置/账号响应直接返回这些原始密钥，存在敏感凭据泄漏风险。
- **API & Runtime (58/D)** — app/routers/settings.py:44: AI/发布设置读取接口仍可能返回原始 Secret/API Key/OAuth Token；本地页面脚本和任意能访问接口的调用方可读取敏感配置。
- **Transcription (59/D)** — app/services/transcript_workflow_service.py:317: transcript Markdown 存在即返回 completed，未校验源指纹、Provider、进度状态或文件完整性。
- **Task Review & Cut (60/C)** — app/services/video_cut_workflow_service.py:248-255: 每个 CutResult 通过 _insert_output_clip_record 独立提交事务；中途插入异常时前面的 output_clip 已落库，cut_run 未被标记 failed，任务可能停留在 cutting 并同时暴露半批新结果。
- **AI Selection (63/C)** — app/services/ai_config_service.py:318: get_ai_config_context 返回含 AI/ASR Key 的完整 values，GET /api/settings/ai 无读取鉴权，页面也复用该上下文。
- **Frontend UI (65/C)** — app/static/js/app.js:211: 多个写请求绕过统一 apiFetch；启用 LOCAL_ADMIN_TOKEN 时可能缺失 Authorization 并被 API 拒绝。
- **Ops & Delivery (68/C)** — scripts/migrate_task_dirs_to_project_names.py:211: 任务目录迁移用非 WAL-aware 主库 copy，先移动目录再统一更新提交，无文件补偿，异常会让路径/DB 不一致。
- **SQLite Persistence (69/C)** — app/db/database.py:31: init_db 仍依赖结构探测执行启动迁移，没有 user_version 或 schema_migrations 账本。
- **Subtitle (70/C)** — app/services/subtitle_data_service.py:352: 手工 revision 的 active/base 检查在事务外，并发编辑可覆盖 active 选择。
- **Publish Scheduler (71/C)** — app/services/publish_scheduler.py:756-785: recover_interrupted_jobs 每轮加载全部 PUBLISHING 任务，并对每个任务串行查询 Worker，没有批量上限、并发控制或退避；Worker 不可用或卡住任务较多时，单轮耗时按任务数乘以网络超时增长，会延迟后续排期处理。

## All findings

### HIGH (15)

- **Frontend UI** · `app/static/js/app.js:211` — 多个写请求绕过统一 apiFetch；启用 LOCAL_ADMIN_TOKEN 时可能缺失 Authorization 并被 API 拒绝。
- **Frontend UI** · `app/static/js/app.js:1729` — 接口或任务数据直接拼接到 innerHTML；publish-center.js:2152 有同类路径，存在本地 DOM XSS/页面结构破坏风险。
- **API & Runtime** · `app/routers/settings.py:44` — AI/发布设置读取接口仍可能返回原始 Secret/API Key/OAuth Token；本地页面脚本和任意能访问接口的调用方可读取敏感配置。
- **API & Runtime** · `app/services/task_lifecycle_service.py:152` — 任务主状态仍可由 PATCH /api/tasks/{task_id}/status 直接写入，未校验合法状态转移、前置产物或当前状态；空任务可被标记 completed。
- **Transcription** · `app/services/transcript_workflow_service.py:317` — transcript Markdown 存在即返回 completed，未校验源指纹、Provider、进度状态或文件完整性。
- **Transcription** · `app/services/transcript_service.py:265` — JobLeaseLostError 已显式透传，但 TranscriptCancelledError 仍会被统一包装为 RuntimeError，取消可能落成 failed。
- **Transcription** · `app/services/transcript_service.py:516` — 远程分块请求每次使用随机 request id，缺少持久化幂等键和超时后结果确认，重试可能重复计费。
- **AI Selection** · `app/services/ai_config_service.py:318` — get_ai_config_context 返回含 AI/ASR Key 的完整 values，GET /api/settings/ai 无读取鉴权，页面也复用该上下文。
- **Task Review & Cut** · `app/services/video_cut_workflow_service.py:248-255` — 每个 CutResult 通过 _insert_output_clip_record 独立提交事务；中途插入异常时前面的 output_clip 已落库，cut_run 未被标记 failed，任务可能停留在 cutting 并同时暴露半批新结果。
- **Task Review & Cut** · `app/services/task_lifecycle_service.py:156-180` — update_task_status 仍按 task_id 直接写入任意传入状态和进度，没有合法状态迁移、前置产物或并发条件；API 直接暴露 PATCH /status，可将空任务跳转为 completed。
- **Task Review & Cut** · `app/services/task_service.py:451-478` — _probe_video 调用 ffprobe 没有 timeout，也没有捕获 subprocess OSError、媒体读取异常或 stat 异常；get_task 默认 include_video_probe=True，损坏、卡死或不可达媒体可长期阻塞任务详情或直接 500。
- **Pipeline & Job Queue** · `app/services/pipeline_engine.py:118` — 取消仍只在每个 step 开始前检查；step 执行期间及最后一步完成后缺少统一取消门禁，Workflow Job 与任务主状态仍可能分叉。
- **Pipeline & Job Queue** · `app/services/pipeline_engine.py:158` — READY_TO_PUBLISH 在同一次 run 中立即转为 COMPLETED，待人工确认状态不可稳定观察。
- **Publish Center** · `app/services/publish_service.py:570` — _normalize_config/_normalize_account 仅新增 *_masked 字段，却保留 client_secret、access_token、refresh_token 原字段；GET /api/publish/platforms、/accounts 及保存配置/账号响应直接返回这些原始密钥，存在敏感凭据泄漏风险。
- **Ops & Delivery** · `scripts/migrate_task_dirs_to_project_names.py:211` — 任务目录迁移用非 WAL-aware 主库 copy，先移动目录再统一更新提交，无文件补偿，异常会让路径/DB 不一致。

### MED (74)

- **Frontend UI** · `app/templates/system_status.html:98` — 配置/API Key 字段进入 DOM，base.html:11 还承载本地管理 Token；需确认全链路始终掩码。
- **Frontend UI** · `app/static/css/styles.css:870` — 使用多个未在 :root 定义的 CSS 自定义属性，相关声明可能失效。
- **Frontend UI** · `app/static/js/app.js:1` — 任务、转写、AI、审核、切片、字幕和配置行为集中在超大全局脚本，回归半径较大。
- **Frontend UI** · `tests/test_publish_center_browser.py:16` — Playwright 缺失时核心浏览器测试可静默跳过，字幕交互与鉴权失败路径覆盖不足。
- **API & Runtime** · `app/core/security.py:23` — 本地管理员鉴权是可选配置；token 为空时写接口仅依赖 loopback/CORS 部署假设，若监听范围或反向代理配置改变会扩大未授权操作面。
- **API & Runtime** · `app/services/task_service.py:587` — 任务详情按 id 查询但不统一过滤 is_deleted，已永久删除任务仍可能被详情和后续动作读取，删除语义与查询语义不一致。
- **API & Runtime** · `app/routers/tasks.py:127` — 多个 async 路由直接执行同步 SQLite、文件系统、FFprobe/FFmpeg 辅助逻辑，慢磁盘或损坏媒体可阻塞事件循环。
- **API & Runtime** · `app/models/task.py:12` — 任务状态同时存在枚举、大小写字符串和旧别名，多处路由/服务自行转换，状态口径容易漂移。
- **API & Runtime** · `app/routers/files.py:30` — 已有文件浏览接口在本地鉴权可选时允许匿名枚举白名单根目录中的媒体元数据，部署边界变化后会泄露本地文件信息。
- **API & Runtime** · `app/services/publish_scheduler.py:274` — Scheduler 关闭会等待当前 run_once 完成；真实发布/恢复调用最长可接近配置的 1800 秒，停机延迟虽优先保证不重复投稿，但仍缺独立可观测的 drain 超时策略。
- **Media & Storage** · `app/services/storage_service.py:840-846` — move_task_directory_to_trash 明确传入 reserve=False，仍采用查询后检查目录的非原子分配；当前无运行时调用，属于遗留兼容入口，但未来恢复调用时仍可能并发复用同一回收站目录。
- **Media & Storage** · `app/services/storage_service.py:361-367` — 通用 resolve_video_file_path 对已存在路径仍原样返回。媒体 HTTP 路由已增加任务边界，但 publish/auto-publish/subtitle/task 查询等非 HTTP 调用方仍可把数据库中的现有外部路径交给后续读取或上传逻辑。
- **Transcription** · `app/services/transcript_service.py:516` — HTTP 429、Retry-After、网络瞬断和可恢复服务错误缺少专门退避策略。
- **Transcription** · `app/services/transcript_service.py:556` — 响应 result.message 可能被当作 0-1 秒转写正文写入结果。
- **Transcription** · `app/services/transcript_service.py:831` — 进度文件 IO/JSON 损坏时静默返回空字典，可能隐藏故障并触发重复执行。
- **Transcription** · `app/services/transcript_workflow_service.py:224` — 运行和取消状态仍是进程内 set，多进程/重启时不能可靠去重或取消。
- **Transcription** · `app/services/transcript_service.py:325` — 本地和远程转写复制分块、checkpoint、异常和进度循环，行为容易漂移。
- **Transcription** · `app/services/transcript_service.py:1088` — Provider/模型/设备为模块级可变全局，并发任务可能互相覆盖运行元数据。
- **Transcription** · `app/services/transcript_service.py:83` — 约 1300 行文件混合 FFmpeg、本地模型、远程 HTTP、checkpoint、进度和 Markdown，职责过大。
- **AI Selection** · `app/services/ai/ai_clip_service.py:4` — generate_candidate_clips_placeholder 返回三条硬编码 ClipCandidate；虽当前无调用方，误接线会产生 fake output。
- **AI Selection** · `app/services/ai/ai_clip_analyzer.py:131` — 分段分析失败窗口被跳过，只要其他窗口有候选便返回；通用路径缺少最低覆盖率门槛。
- **AI Selection** · `app/services/ai/variety_comedy_analyzer.py:513` — 全局评审失败时隐式降级到扩展阶段评分，Provider 失败仍可形成候选。
- **AI Selection** · `app/services/ai/local_model_provider.py:19` — fallback_protocol 对任意 AIProviderError 都执行第二协议，未区分限流、认证和协议错误，可能重复调用与计费。
- **AI Selection** · `app/services/ai_analysis_workflow_service.py:738` — 候选事务替换、JSON 文件和 run 历史分步持久化，后续失败会形成数据库与文件/历史不一致。
- **AI Selection** · `app/services/ai/long_live_talk_analyzer.py:427` — checkpoint 更新无 run/lease/owner fencing，并发或旧进程恢复可能互相覆盖窗口状态。
- **AI Selection** · `app/services/ai_analysis_workflow_service.py:71` — 分析元文件不存在、损坏或读取异常时静默返回空字典，无法区分未分析与产物损坏。
- **AI Selection** · `app/services/ai_analysis_workflow_service.py:685` — 单文件集中 profile、Provider、状态、候选、文件、历史与恢复，模块回归半径大。
- **AI Selection** · `app/services/ai/ai_clip_analyzer.py:252` — 多个分析器重复偏好摘要、时间转换、默认字段和 AI 输出解析逻辑。
- **AI Selection** · `tests/test_split_services.py:200` — 缺少真实 Provider 成功路径、文件写入失败一致性、并发运行和旧 checkpoint 覆盖测试。
- **AI Selection** · `tests/test_codex_cli_provider.py:12` — Provider 测试主要 monkeypatch，缺少 HTTP 429/500/超时和协议 fallback 重复调用边界。
- **Task Review & Cut** · `app/services/video_cut_workflow_service.py:18-45` — _next_cut_run_number 先 SELECT MAX(run_number)，随后由独立连接 INSERT，缺少事务序列化或唯一约束；并发重切可能产生重复 run_number。
- **Task Review & Cut** · `app/services/task_lifecycle_service.py:66-71` — allocate_task_dir_name 会先原子预占目录，再由 create_task_directory 和数据库 INSERT 继续执行；后续目录初始化或数据库提交失败时没有统一释放已预占的空目录，可能累积孤儿目录并影响后续命名。
- **Task Review & Cut** · `app/services/task_service.py:14-96` — task_service 仍集中导入 AI、字幕、转写、存储、切片等大量私有服务函数，并与 task_lifecycle_service 形成动态反向依赖，职责边界复杂。
- **Task Review & Cut** · `app/services/task_query_service.py:89-172` — _batch_all_output_clips 复制 task_service.list_output_clips 的路径解析、字幕状态和发布就绪判断；两套口径未来可能分叉。
- **Task Review & Cut** · `app/services/task_service.py:587-603` — get_task 查询只按 id，不过滤 is_deleted；已永久删除且仅应保留历史的任务仍可被详情和后续动作读取。
- **Task Review & Cut** · `app/services/task_service.py:717-718` — list_clip_candidates 直接解析每条 start_time/end_time，单条历史脏时间格式可阻断整个审核页。
- **Task Review & Cut** · `app/services/video_cut_workflow_service.py:269-289` — 切片成功后先把任务更新为完成，再同步发送中心；同步异常只返回 partial，主状态与发布关联可能不一致。
- **Subtitle** · `app/services/subtitle_data_service.py:352` — 手工 revision 的 active/base 检查在事务外，并发编辑可覆盖 active 选择。
- **Subtitle** · `app/services/subtitle_data_service.py:489` — 批准 revision 在事务外校验，并发或重放旧请求可回退 active 版本。
- **Subtitle** · `app/services/subtitle_data_service.py:135` — source/clip track 先查后插，NULL output_clip_id 约束不足，并发可能重复源轨。
- **Subtitle** · `app/services/subtitle_auto_workflow_service.py:87` — 批量批准逐 clip 独立提交，Job 后创建；中途失败会留下部分批准。
- **Subtitle** · `app/services/subtitle_data_service.py:168` — source revision 提交后逐 clip 独立同步，异常会形成混合版本。
- **Subtitle** · `app/services/subtitle_workflow_service.py:475` — 字幕 job 完成与激活分两次写入，迟到 worker 仍可能激活旧成片。
- **Subtitle** · `app/services/subtitle_data_service.py:1045` — 字幕数据服务聚合 track、revision、cue、导入导出、波形和渲染辅助，回归半径大。
- **Subtitle** · `tests/test_subtitle_editor.py:189` — 缺并发 save/approve/ensure track 与批处理中途失败测试。
- **Pipeline & Job Queue** · `app/services/pipeline_engine.py:104` — Pipeline context 仍只保存在内存，缺少按 step 的可重放副作用 checkpoint；重启可能重复 AI、封面或排期动作。
- **Pipeline & Job Queue** · `app/services/pipeline_engine.py:145` — 普通 step 失败后写 summary 仍可能二次失败并遮蔽原始异常。
- **Pipeline & Job Queue** · `app/services/job_worker.py:198` — 子进程 stdout/stderr 仍丢弃，父进程只保留退出码，恢复与诊断证据不足。
- **Pipeline & Job Queue** · `app/services/job_service.py:126` — Job JSON 损坏时静默保留原字符串，执行器可能产生不明确的数据错误。
- **Pipeline & Job Queue** · `app/services/job_service.py:18` — ai_analysis 与 publish 类型仍可创建但 worker 未实现，属于可创建不可执行的 legacy/stub。
- **Publish Center** · `app/services/publish_service.py:266` — publish_service.py 当前约4750行，混合配置/账号/OAuth、文案与封面、队列同步、历史、旧 OpenCLI 脚本、API Provider 和页面上下文，形成高耦合 God Component，任一发布流程改动的 blast radius 很大。
- **Publish Center** · `app/services/publish_service.py:2941` — 历史查询 SQL 只按 PUBLISH_HISTORY_STATUSES 的大写值过滤，之后才调用 _normalize_publish_status；LEGACY_STATUS_MAP 支持旧小写状态但旧记录会在 SQL 层被提前排除，历史页可能漏数据。
- **Publish Center** · `app/services/publish_service.py:2144` — sync_task_publish_jobs 封面生成失败时只把 {'cover_error': ...} 放入 item_covers，仍继续插入 WAITING 任务且不计入 errors；同步可能返回 ok，但生成的任务实际无法通过 local_browser readiness。
- **Publish Center** · `app/services/publish_service.py:2787` — create_batch_publish_jobs 对不存在的 output_clip_id 直接 continue，全部无效或部分无效时仍返回 status='ok'，调用方无法区分‘创建成功’与‘输入被静默丢弃’。
- **Publish Center** · `app/services/publish_readiness.py:167` — publish_domain/create_publish_job 仍允许 api_publish，publishers registry 也保留兼容入口，但统一 readiness 只接受 local_browser/manual_export 并将 api_publish 标记 unsupported_publish_mode，持久化任务与调度契约不一致。
- **Publish Center** · `app/services/publish_providers.py:117` — BilibiliPublishProvider 在完成配置和 token 校验后始终抛出 bilibili_provider_pending；api_publish 模式仍可创建/注册，因此这是已接线但必然失败的 placeholder 路径。
- **Publish Center** · `app/services/publish_providers.py:66` — 旧 Douyin API Provider 将 upload 与 create 分成两个无幂等键的请求；_request_json 只包装 HTTPError/URLError，平台已接收后网络超时或连接异常可能被判失败，重试会重复上传/投稿且没有 NEED_REVIEW 不确定态。
- **Publish Scheduler** · `app/services/publish_scheduler.py:756-785` — recover_interrupted_jobs 每轮加载全部 PUBLISHING 任务，并对每个任务串行查询 Worker，没有批量上限、并发控制或退避；Worker 不可用或卡住任务较多时，单轮耗时按任务数乘以网络超时增长，会延迟后续排期处理。
- **Publish Scheduler** · `app/services/publish_scheduler.py:281-302` — list_due_jobs 每轮 SELECT 全部 SCHEDULED 记录，再逐条在 Python 中解析时间；虽已有状态/排期索引，但没有 SQL due 条件、分页或批量上限，排期量增长后会增加扫描内存和调度延迟。
- **Publish Scheduler** · `app/services/publish_scheduler.py:133-1788` — PublishScheduler 约 1891 行、42 个方法，同时承担排期计算、任务领取、执行结果写回、Worker 恢复、重试修复、人工复核、批量排期和旧版兼容；职责边界过宽，修改任一状态路径的回归半径较大。
- **Publishers & Worker** · `scripts/publish_host_worker.py:321` — _prior_job_execution_requires_review 扫描旧 execution journal 时，若 journal 缺少 identity 或已损坏，直接 continue；跨 execution 无法确认其 job_id 时不会阻断新 execution。旧 Worker 在上传后崩溃并留下无身份/损坏日志时，仍存在重复投稿边界。
- **SQLite Persistence** · `app/db/database.py:31` — init_db 仍依赖结构探测执行启动迁移，没有 user_version 或 schema_migrations 账本。
- **SQLite Persistence** · `app/db/database.py:625` — 索引创建捕获所有 sqlite3.Error 后静默继续，关键唯一索引失败可能无告警。
- **SQLite Persistence** · `app/db/database.py:675` — 历史表迁移主要补列，未系统重建或验证旧库 Foreign Key、UNIQUE、CHECK 约束。
- **SQLite Persistence** · `scripts/backup_restore.py:305` — 备份验证未覆盖 foreign_key_check、关键索引和迁移版本。
- **SQLite Persistence** · `scripts/backup_restore.py:529` — 直接恢复函数内部没有统一进程锁或活动连接门禁。
- **Ops & Delivery** · `.github/workflows/ci.yml:3` — CI 仅监听 master push/PR；feature/docs 直接 push 不即时验证，问题延迟到开 PR。
- **Ops & Delivery** · `scripts/start_docker_opencli.ps1:24` — 健康检查异常只警告不返回非零，服务失败也可能被调用方视为启动成功。
- **Ops & Delivery** · `scripts/start.ps1:30` — Demo 覆盖生效前先对正式 E 盘配置运行 doctor，新机/迁移机的隔离 Demo 可能被旧路径阻断。
- **Ops & Delivery** · `scripts/start.ps1:71` — Worker 启动异常被捕获后继续并报告工作台成功，发布能力不可用时易被误判完整健康。
- **Ops & Delivery** · `scripts/restore.ps1:27` — 恢复只以健康响应判断 App 运行，StopServices 只停 Compose；native/异常健康进程可能仍持有 DB 时执行替换。
- **Ops & Delivery** · `scripts/backup.ps1:33` — 备份默认包含 .env 且不加密，仅显式 ExcludeEnv 才排除，包被共享时泄漏本机秘密。
- **Ops & Delivery** · `tests/test_native_scripts.py:11` — 启停测试主要是源码字符串断言，Windows smoke 不做实际 restore/冲突/清理失败与恢复后健康验证。
- **Ops & Delivery** · `scripts/seed_demo_data.py:32` — 缺 FFmpeg 时仍插入无媒体路径 Demo 数据并成功退出，数量检查通过但媒体 smoke 不可信。

### LOW (37)

- **Frontend UI** · `app/static/js/subtitle-editor.js:171` — 字幕编辑器已有 escapeHtml、虚拟列表、竞态 token 与自动保存版本控制，是可保留的正向实现。
- **Frontend UI** · `app/static/js/publish-center.js:4` — 前端明确只创建抖音任务，B站保留后端兼容；这是当前产品边界而非应机械删除的代码。
- **API & Runtime** · `app/main.py:91` — /health 只返回进程级 ok，未覆盖数据库可写、任务存储、Scheduler/Worker readiness，无法作为完整就绪判断。
- **API & Runtime** · `app/routers/settings.py:170` — OAuth 错误信息通过 query 参数回显到页面，缺少统一长度/字符约束与结构化错误码。
- **API & Runtime** · `app/core/config.py:1` — 配置定义和兼容别名仍分散在 settings、环境变量与发布服务，存在重复默认值和 dual-format 维护成本。
- **API & Runtime** · `app/models/task.py:360` — speaker_styles 等动态结构仍使用 Any，边界校验主要依赖下游调用方。
- **Media & Storage** · `tests/test_storage_boundaries.py:14-32` — 新增边界测试没有覆盖任务目录数据库读取在 sqlite3.Error、锁定或损坏数据库下显式失败的回归；代码已有保护但证据不足。
- **Media & Storage** · `app/services/storage_service.py:257-300` — reserve=True 会在数据库任务记录写入前创建目录；进程在预占后崩溃可能留下无数据库记录的空目录，虽不会覆盖数据，但会造成孤儿目录累积。
- **Transcription** · `tests/test_long_live_foundation.py:65` — 已覆盖失租 checkpoint 和异常透传，仍缺取消、429/坏响应、重复计费与真实 Markdown 闭环。
- **AI Selection** · `app/services/ai/ai_clip_analyzer.py:456` — 输出归一化同时接受多组历史字段并填大量默认值，兼容有效但维护成本高。
- **AI Selection** · `app/services/ai_config_service.py:55` — 旧 AI_REMOTE 与新分析/发布配置并存，运行时还改写全局 settings，形成双路径。
- **AI Selection** · `tests/test_variety_comedy_selection.py:291` — 算法分支覆盖较好，但真实三阶段 Provider、资源消耗和全局评审降级未由集成测试锁定。
- **Task Review & Cut** · `tests/test_path_resolution.py:44-68` — 目录唯一性单元测试为连续调用；并发线程回归由 tests/test_storage_boundaries.py 单独覆盖。
- **Task Review & Cut** · `tests/test_split_services.py:119-137` — 测试将没有产物的任务直接更新为 completed 并断言成功，固化了任意状态跳跃行为。
- **Task Review & Cut** · `tests/test_versioning_rollback.py:163-178` — 版本测试使用虚构输出路径，只验证数据库标记，没有覆盖真实 FFmpeg 输出、文件切换和恢复。
- **Subtitle** · `app/services/subtitle_auto_workflow_service.py:273` — auto_config_json 损坏时静默降为空并写回，可能丢弃其他配置。
- **Subtitle** · `app/services/subtitle_workflow_service.py:312` — 统一 revision 外仍保留旧调用方适配，存在历史口径漂移成本。
- **Subtitle** · `tests/test_subtitle_auto_workflow.py:172` — 核心真实渲染在 FFmpeg/FFprobe 缺失时会跳过，异常 Provider 证据不足。
- **Subtitle** · `app/services/subtitle_data_service.py:761` — 波形处理把完整 PCM 捕获到内存，超长媒体存在时长线性内存峰值。
- **Subtitle** · `app/services/subtitle_data_service.py:1024` — 单条字幕文本可接近文件上限，放大渲染、导出和 Prompt 资源消耗。
- **Pipeline & Job Queue** · `tests/test_job_fencing.py:59` — 已覆盖 owner/token、迁移、转写 checkpoint 与字幕 cleanup fencing，但尚无真实父子进程重启中失租测试。
- **Pipeline & Job Queue** · `tests/test_auto_pipeline.py:204` — 仍缺稳定 READY 状态、最后一步取消和重启副作用去重测试。
- **Pipeline & Job Queue** · `app/services/pipeline_engine.py:572` — JSON/时间配置损坏时静默回退默认排期，故障可见性不足。
- **Publish Center** · `app/services/publish_providers.py:204` — _post_multipart 先 file_path.read_bytes() 再 b''.join(chunks)，完整视频和完整 multipart body 同时驻留内存，大文件发布存在约双倍峰值。
- **Publish Center** · `app/services/publish_service.py:1569` — _batch_find_publish_jobs 对每条记录直接调用 _normalize_job(row)，未传入预取 accounts；_normalize_job 会逐条触发 readiness/account 查询，发布中心批量切片时形成 N+1 查询。
- **Publish Center** · `tests/test_publish_history.py:111` — 现有定向测试未覆盖原始 Secret/Token 响应、旧小写历史状态、同步封面 partial、批量无效 ID，以及 API 超时后已接收的重复投稿边界。
- **Publish Scheduler** · `app/services/publish_scheduler.py:274-279` — shutdown 已能追踪并等待后台 Task，但没有超时或取消兜底；若当前 run_once 被数据库锁、Worker 串行查询或文件操作长期阻塞，应用优雅停机仍可能无限等待。
- **Publish Scheduler** · `app/services/publish_scheduler.py:1807-1812` — queue_snapshot 对非法 scheduled_at 捕获 ValueError 后静默 pass；损坏排期会从“今日任务”视图中消失，未产生告警或错误计数，降低数据异常可见性。
- **Publishers & Worker** · `app/services/publishers/page_scripts.py:14` — 页面脚本入口仍通过延迟导入 publish_service 私有函数来转发大量 DOM 脚本；这是有意保留的兼容层，但形成 legacy glue，脚本变更仍会牵连 4750 行 God Service。
- **Publishers & Worker** · `app/services/publishers/browser_runtime.py:315` — screenshot 捕获裸 Exception 后返回空字符串，截图失败不会进入发布结果或健康信号；发布可以继续但缺少关键诊断证据。
- **Publishers & Worker** · `scripts/opencli_host_bridge.py:74` — 旧 OpenCLIHostBridgeHandler 的 /run 处理器本身没有 Bearer Token 校验；当前 main 已转调受保护的 publish_host_worker，默认入口不再使用它，但保留的可直接实例化旧处理器仍会造成安全边界误解。
- **Publishers & Worker** · `tests/test_publish_worker_client.py:155` — 当前测试已覆盖双进程锁竞争、死进程锁回收和常规 execution fencing，但仍未覆盖无身份/损坏旧 journal 被跨 execution 扫描跳过、screenshot 失败信号及旧 bridge 处理器被直接启动时的鉴权边界。
- **SQLite Persistence** · `app/db/database.py:656` — Workflow claim 索引未覆盖 lease_expires_at，队列规模增长后过期接管扫描可能退化。
- **SQLite Persistence** · `tests/test_job_fencing.py:206` — 已覆盖并发补列和旧 running guard，仍缺完整启动链迁移失败回滚测试。
- **Ops & Delivery** · `scripts/acceptance.ps1:115` — 验收递归扫描正式任务目录，媒体量大/锁文件会显著拖慢或阻断 release gate。
- **Ops & Delivery** · `scripts/backup_restore_runtime.py:142` — import 时 monkeypatch backup core，全局行为依赖导入顺序。
- **Ops & Delivery** · `docs/PORTABLE_SETUP.md:127` — 文档描述与当前 start.ps1 实现不符，旧 Next Steps 又保留兼容入口，启动排障认知漂移。

## Cross-cutting themes

- **本地单体架构是当前可运行的主要原因.** FastAPI + SQLite WAL + 文件产物 + 持久化 Job + Windows Worker 与个人本机规模匹配；不需要微服务化，现有恢复骨架应保留。
- **P0 数据一致性边界已封口，事务型旧债仍需继续.** 活动库外键、测试误删和媒体删除回滚已修复；迁移无账本、切片批次半提交与并发版本号仍是下一阶段重点。
- **Job 与发布执行代际已封口，下一风险转向业务状态原子性.** Workflow lease token、Publish execution fencing 和 Windows Worker journal 幂等已经完成；任务状态跳跃、切片半提交和取消恢复仍是下一阶段重点。
- **可用性 fallback 正在掩盖降级结果.** AI 部分窗口、转写旧产物、损坏 JSON、封面生成、批量发布和风险字段多处静默继续，用户不一定能区分完整成功、部分成功和旧结果。
- **发布与配置读取是主要安全边界.** Publish Center、AI Config 与 Frontend 仍存在原始 Secret 响应、可选写鉴权和 DOM XSS；Worker 路径与 journal 脱敏已收紧，但本地管理员接口门禁尚未完成。
- **复杂度集中而非全项目平均恶化.** publish_service.py、publish-center.js、app.js、publish_scheduler.py、subtitle_data_service.py 和 transcript_service.py 是主要 God Component；应按业务边界渐进拆分。
- **测试已与活动数据隔离，但 Coverage 与真实故障闭环仍不足.** 610 项测试全部通过，Pytest 已强制使用进程级 sandbox；Coverage 尚未采集，真实平台 E2E 和故障注入仍需在不触发生产副作用的边界下补齐。

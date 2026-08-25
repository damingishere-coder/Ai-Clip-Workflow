<!--
  This file:        .codemap/codemap.md   (written report)
  Interactive map:  .codemap/codemap.html
-->

# NiuMa Studio — Functional Module Quality Audit

> **Interactive view:** [`.codemap/codemap.html`](codemap.html) — per-module scores, findings, LoC, and the dependency graph. This file is the written report.

**Generated:** 2026-08-24 · **Modules:** 13 · **Size:** 52028 tracked LoC across 132 files

## Health by layer

| Layer | Modules | Avg score |
|---|--:|--:|
| 界面 · API | 2 | 56 |
| 业务编排 | 3 | 69 |
| 媒体与 AI 处理 | 4 | 66 |
| 外部执行边界 | 2 | 74 |
| 持久化与运维 | 2 | 70 |

## Per-module lines of code & score

_LoC is the representative file/folder per module; folder-level modules overlap and are not additive._

### 界面 · API

| Module | LoC | Score | Tags |
|---|--:|:--|:--|
| Frontend UI | 13,840 | 65 C | god-component, bloat, glue, duplication, legacy |
| API & Runtime | 2,753 | 48 D | fallback, legacy, dual-format, bloat, glue |

### 业务编排

| Module | LoC | Score | Tags |
|---|--:|:--|:--|
| Publish Center | 5,593 | 52 D | god-component, bloat, legacy, dual-format, fallback, silent-except, placeholder, duplication |
| Pipeline & Job Queue | 3,350 | 76 B | fallback, silent-except, legacy, bloat, god-component, glue |
| Task Review & Cut | 3,036 | 78 B | god-component, glue, fallback, dual-format, legacy, over-fit |

### 媒体与 AI 处理

| Module | LoC | Score | Tags |
|---|--:|:--|:--|
| AI Selection | 4,420 | 55 D | fallback, silent-except, legacy, dual-format, bloat, duplication, god-component |
| Subtitle | 2,409 | 64 C | fallback, silent-except, legacy, duplication, bloat, god-component |
| Transcription | 1,960 | 64 C | fallback, silent-except, duplication, god-component |
| Media & Storage | 1,614 | 82 B | legacy, dual-format, fallback, silent-except |

### 外部执行边界

| Module | LoC | Score | Tags |
|---|--:|:--|:--|
| Publishers & Worker | 2,699 | 76 B | legacy, glue, silent-except, fallback |
| Publish Scheduler | 2,329 | 71 C | god-component, bloat, legacy, silent-except |

### 持久化与运维

| Module | LoC | Score | Tags |
|---|--:|:--|:--|
| Ops & Delivery | 4,347 | 68 C | fallback, legacy, dual-format, duplication, bloat, glue, silent-except, monkeypatch |
| SQLite Persistence | 3,678 | 73 C | legacy, dual-format, bloat, god-component, glue |

## Worst offenders

- **API & Runtime (48/D)** — app/routers/settings.py:10: GET /api/settings/ai 未做鉴权，返回 ai_config_service.get_ai_config_context() 中的原始 values；该上下文会从 .env/环境变量读取 API key、token 等 Secret，并同时返回 env_path。前端或本地恶意页面可直接读取凭据。
- **Publish Center (52/D)** — app/services/publish_service.py:570: _normalize_config/_normalize_account 仅新增 *_masked 字段，却保留 client_secret、access_token、refresh_token 原字段；GET /api/publish/platforms、/accounts 及保存配置/账号响应直接返回这些原始密钥，存在敏感凭据泄漏风险。
- **AI Selection (55/D)** — app/services/ai_config_service.py:321: get_ai_config_context 直接返回包含 API Key 等敏感配置的完整 values；该结果由 app/routers/settings.py:7-12 的未认证 GET /api/settings/ai 暴露，仍是当前 P1.5 Secret 泄漏风险。
- **Transcription (64/C)** — app/services/transcript_service.py:1099-1129,1159-1196: faster-whisper 的 model.transcribe 与模型加载没有单次调用或绝对时限；模型/解码器卡住时只能依赖外层 Worker，直接转写路径可能长期占用。
- **Subtitle (64/C)** — app/services/subtitle_auto_workflow_service.py:42-50,51-108: prepare_task_subtitle_review 先执行 ensure_source_track/ensure_clip_track 并提交 revision/track 副作用，之后才检查活动 Job 与当前 lease；旧 Worker 失租或并发冲突时仍可能留下字幕数据库变更。
- **Frontend UI (65/C)** — app/static/js/app.js:211: 多个写请求绕过统一 apiFetch；启用 LOCAL_ADMIN_TOKEN 时可能缺失 Authorization 并被 API 拒绝。
- **Ops & Delivery (68/C)** — scripts/migrate_task_dirs_to_project_names.py:211: 任务目录迁移用非 WAL-aware 主库 copy，先移动目录再统一更新提交，无文件补偿，异常会让路径/DB 不一致。
- **Publish Scheduler (71/C)** — app/services/publish_scheduler.py:756-785: recover_interrupted_jobs 每轮加载全部 PUBLISHING 任务，并对每个任务串行查询 Worker，没有批量上限、并发控制或退避；Worker 不可用或卡住任务较多时，单轮耗时按任务数乘以网络超时增长，会延迟后续排期处理。
- **SQLite Persistence (73/C)** — app/db/database.py:607-608: schema_migrations 账本迁移在旧版兼容迁移和多处 executescript（996、1071、1155、1231、1754、1780）完成并提交后才执行；若历史 helper 或账本迁移失败，前面的 Schema/DML 已持久化而账本事务回滚，init_db 仍存在可重试但非原子的半迁移边界。
- **Pipeline & Job Queue (76/B)** — app/services/pipeline_engine.py:1529: 恢复文案时只按 output_clip_id、platform、request_fingerprint 复用 cached 项，没有排除 metadata.error 或失败状态；MetadataGenerator 会把 AI 失败的规则降级结果持久化，后续同指纹运行可能持续复用该失败结果，阻断真正重试。

## All findings

### HIGH (10)

- **Frontend UI** · `app/static/js/app.js:211` — 多个写请求绕过统一 apiFetch；启用 LOCAL_ADMIN_TOKEN 时可能缺失 Authorization 并被 API 拒绝。
- **Frontend UI** · `app/static/js/app.js:1729` — 接口或任务数据直接拼接到 innerHTML；publish-center.js:2152 有同类路径，存在本地 DOM XSS/页面结构破坏风险。
- **API & Runtime** · `app/routers/settings.py:10` — GET /api/settings/ai 未做鉴权，返回 ai_config_service.get_ai_config_context() 中的原始 values；该上下文会从 .env/环境变量读取 API key、token 等 Secret，并同时返回 env_path。前端或本地恶意页面可直接读取凭据。
- **API & Runtime** · `app/main.py:98` — 本地管理鉴权是可选的：只有 LOCAL_ADMIN_TOKEN 非空时才校验写请求；配置默认值为空，因此未配置 Token 时所有 POST/PUT/PATCH/DELETE API 均可被无认证调用。
- **API & Runtime** · `app/routers/files.py:9` — 未鉴权的 /api/files/browse 调用 browse_video_directory，虽限制了根目录和路径穿越，但仍枚举 configured allowed roots 下的目录名、文件名、绝对路径、父路径和文件大小，造成本地媒体元数据泄露。
- **API & Runtime** · `app/routers/tasks.py:223` — async 路由直接执行同步重任务：音频处理、转写、切片和任务详情 ffprobe 可阻塞 FastAPI event loop，影响无关请求。
- **AI Selection** · `app/services/ai_config_service.py:321` — get_ai_config_context 直接返回包含 API Key 等敏感配置的完整 values；该结果由 app/routers/settings.py:7-12 的未认证 GET /api/settings/ai 暴露，仍是当前 P1.5 Secret 泄漏风险。
- **Task Review & Cut** · `app/services/task_service.py:458-485,594-616` — 任务详情默认执行无 timeout 的 ffprobe，且未捕获 OSError/Path.stat 异常；NAS、损坏或卡死媒体可能阻塞详情请求或返回 500。明确延期至 P1.4。
- **Publish Center** · `app/services/publish_service.py:570` — _normalize_config/_normalize_account 仅新增 *_masked 字段，却保留 client_secret、access_token、refresh_token 原字段；GET /api/publish/platforms、/accounts 及保存配置/账号响应直接返回这些原始密钥，存在敏感凭据泄漏风险。
- **Ops & Delivery** · `scripts/migrate_task_dirs_to_project_names.py:211` — 任务目录迁移用非 WAL-aware 主库 copy，先移动目录再统一更新提交，无文件补偿，异常会让路径/DB 不一致。

### MED (52)

- **Frontend UI** · `app/templates/system_status.html:98` — 配置/API Key 字段进入 DOM，base.html:11 还承载本地管理 Token；需确认全链路始终掩码。
- **Frontend UI** · `app/static/css/styles.css:870` — 使用多个未在 :root 定义的 CSS 自定义属性，相关声明可能失效。
- **Frontend UI** · `app/static/js/app.js:1` — 任务、转写、AI、审核、切片、字幕和配置行为集中在超大全局脚本，回归半径较大。
- **Frontend UI** · `tests/test_publish_center_browser.py:16` — Playwright 缺失时核心浏览器测试可静默跳过，字幕交互与鉴权失败路径覆盖不足。
- **API & Runtime** · `app/routers/tasks.py:35` — 大量 async 路由直接调用同步数据库、文件和服务函数；仅少数入口使用 run_in_threadpool，异步边界不一致，容易出现请求串行化和响应抖动。
- **API & Runtime** · `app/models/task.py:7-41` — TaskStatus 同时保留大写自动流水线状态和小写 legacy 状态；任意状态跳跃虽已修复，但模型、旧页面和 API 客户端仍需兼容两套值。
- **API & Runtime** · `app/routers/tasks.py:35; app/routers/publish.py:1` — tasks.py 与 publish.py 聚合大量路由、异常映射、同步/异步调度和业务编排，新增流程的 blast radius 较大。
- **Media & Storage** · `app/services/storage_service.py:362-368,486-487` — resolve_video_file_path 对已存在路径直接返回，不验证任务受控目录；get_source_video_path 及 publish/subtitle/task 等非 HTTP 调用方共享该入口，输出路径异常时仍可能读取或上传外部文件。
- **Transcription** · `app/services/transcript_service.py:1099-1129,1159-1196` — faster-whisper 的 model.transcribe 与模型加载没有单次调用或绝对时限；模型/解码器卡住时只能依赖外层 Worker，直接转写路径可能长期占用。
- **Transcription** · `app/services/transcript_service.py:164-191` — FFmpeg 音频提取只有无进展 watchdog，没有绝对 wall-clock deadline；进程持续输出进度但实际不结束时仍可能无限运行。
- **Transcription** · `app/services/transcript_service.py:981-989` — 进度文件损坏、读取失败或顶层非 dict 时统一返回空字典，静默丢失故障证据，恢复逻辑无法区分初始状态与持久化损坏。
- **Transcription** · `app/services/transcript_service.py:67-77,1259-1270` — 活动 provider/model/device/compute_type 仍使用模块级可变全局；同一进程并发转写会互相覆盖运行元数据和进度展示。
- **Transcription** · `app/services/transcript_service.py:347-549` — 本地与火山远程分片分别复制 chunk、checkpoint、进度和异常收口逻辑，两个实现继续存在维护分叉。
- **Transcription** · `app/services/transcript_service.py:83-1395` — 单文件仍混合 FFmpeg、faster-whisper、远程 HTTP、幂等 checkpoint、进度文件和 Markdown 生成，约 1400 行，职责边界和回归影响面偏大。
- **AI Selection** · `app/services/ai/long_live_talk_analyzer.py:493` — _assert_current_job_lease 只有在 ContextVar 存在 active lease 时才校验；/api/tasks/{task_id}/process/ai 直接调用 task_service.process_task_ai_analysis，未建立 Workflow Job lease，长直播 checkpoint 写入因此可无 owner fencing。
- **AI Selection** · `app/services/ai/long_live_talk_analyzer.py:119` — 每次 analyze_long_live_talk 调用都重新设置 max_attempts，未读取 checkpoint 的 attempt_count 或 next_retry_at；人工重试/进程重启会重复消耗远程模型调用和计费预算。
- **AI Selection** · `app/services/ai/ai_clip_analyzer.py:121` — 分段分析捕获单段异常后继续执行；只要其他分段生成候选就返回成功，并将失败段写入 summary，没有覆盖率或失败比例门禁，可能把不完整分析当作完整结果。
- **AI Selection** · `app/services/ai/variety_comedy_analyzer.py:527` — _global_judge 捕获所有异常后返回空排序并降级到扩展阶段评分；全局评审失败会被转化为可继续执行的结果，真实 AI 失败与正常降级仍混在同一业务路径。
- **AI Selection** · `app/services/ai_analysis_workflow_service.py:923` — AI 分析先独立替换 clip_candidates，再写分析文件，随后独立插入 ai_analysis_runs；进程在任一边界崩溃时数据库候选、分析文件和历史 active run 可能互相不一致。
- **Task Review & Cut** · `app/services/task_lifecycle_service.py:373-401; tests/test_task_state_machine.py:69-82` — 公共状态转换前置条件只检查数据库字段或路径字符串，不验证源视频、候选产物和活跃 output 文件真实存在。明确延期至 P1.4。
- **Task Review & Cut** · `app/services/task_service.py:1-98,1220-1273; app/services/task_query_service.py:16-24` — TaskService 仍集中承担大量业务、数据库和展示职责，并与 task_query_service 互相导入、保留包装函数，维护 blast radius 较大。建议作为 P2 延后拆分。
- **Task Review & Cut** · `app/services/task_service.py:717-718` — 候选片段列表会直接解析每条历史时间字段，单条异常格式仍可能阻断整个审核列表。
- **Subtitle** · `app/services/subtitle_auto_workflow_service.py:42-50,51-108` — prepare_task_subtitle_review 先执行 ensure_source_track/ensure_clip_track 并提交 revision/track 副作用，之后才检查活动 Job 与当前 lease；旧 Worker 失租或并发冲突时仍可能留下字幕数据库变更。
- **Subtitle** · `app/services/subtitle_data_service.py:1017-1074` — _load_source_cues 对 checksum、JSON 或 segment 错误直接跳过；只要其他 chunk 仍有 cue 就生成部分字幕 revision，损坏转写会被静默降级为缺句结果。
- **Subtitle** · `app/services/subtitle_data_service.py:1222-1230,236-330` — _sync_dependent_clip_tracks 逐条调用各自事务的 sync_clip_track；源轨已提交后任一切片同步失败，会留下部分 up_to_date、部分 pending_sync 状态。
- **Subtitle** · `app/services/subtitle_auto_workflow_service.py:513-569` — checkpoint 与 DB 恢复证据只检查 completed/verified、路径 exists 和 is_file，不检查非空、size/fingerprint 或可被 FFprobe 验证；截断或零字节最终文件仍可能被恢复为成功。
- **Subtitle** · `app/services/subtitle_data_service.py:1222-1230` — 字幕数据层继续集中承担 revision/cue 持久化、导入导出、ASS、波形和 FFprobe，单文件约 1434 行，变更影响面和重复事务路径较大。
- **Pipeline & Job Queue** · `app/services/pipeline_engine.py:1529` — 恢复文案时只按 output_clip_id、platform、request_fingerprint 复用 cached 项，没有排除 metadata.error 或失败状态；MetadataGenerator 会把 AI 失败的规则降级结果持久化，后续同指纹运行可能持续复用该失败结果，阻断真正重试。
- **Pipeline & Job Queue** · `app/services/pipeline_engine.py:1592` — _write_json_atomic 在写临时文件前后各检查一次 lease，但最终 temporary_path.replace(path) 不在数据库锁或 owner 条件内；lease 在第二次检查后失效并被接管时，旧 worker 仍可能覆盖新 worker 的元数据文件。
- **Pipeline & Job Queue** · `app/services/pipeline_engine.py:88` — PipelineEngine 仍集中承担步骤编排、checkpoint 恢复、文件证据、AI 分析、文案生成、发布任务恢复和状态推进，文件约 1800 行，单点修改 blast radius 大。
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
- **SQLite Persistence** · `app/db/database.py:607-608` — schema_migrations 账本迁移在旧版兼容迁移和多处 executescript（996、1071、1155、1231、1754、1780）完成并提交后才执行；若历史 helper 或账本迁移失败，前面的 Schema/DML 已持久化而账本事务回滚，init_db 仍存在可重试但非原子的半迁移边界。
- **SQLite Persistence** · `scripts/backup_restore.py:305-383` — 备份包验证只检查 manifest、哈希、quick_check 和表计数，没有验证 PRAGMA foreign_key_check、schema_migrations 记录或关键 v2 唯一索引定义；结构上可读但业务约束不完整的备份仍可能被接受，恢复后才在启动阶段失败或触发迁移。
- **SQLite Persistence** · `scripts/backup_restore.py:529-634` — restore_backup_bundle 在替换 SQLite 文件前没有应用进程锁或活动连接闸门；服务仍运行时可原子替换数据库路径，现有连接可能继续指向旧 inode，而新连接指向恢复文件，形成恢复期间的双数据库视图。
- **Ops & Delivery** · `.github/workflows/ci.yml:3` — CI 仅监听 master push/PR；feature/docs 直接 push 不即时验证，问题延迟到开 PR。
- **Ops & Delivery** · `scripts/start_docker_opencli.ps1:24` — 健康检查异常只警告不返回非零，服务失败也可能被调用方视为启动成功。
- **Ops & Delivery** · `scripts/start.ps1:30` — Demo 覆盖生效前先对正式 E 盘配置运行 doctor，新机/迁移机的隔离 Demo 可能被旧路径阻断。
- **Ops & Delivery** · `scripts/start.ps1:71` — Worker 启动异常被捕获后继续并报告工作台成功，发布能力不可用时易被误判完整健康。
- **Ops & Delivery** · `scripts/restore.ps1:27` — 恢复只以健康响应判断 App 运行，StopServices 只停 Compose；native/异常健康进程可能仍持有 DB 时执行替换。
- **Ops & Delivery** · `scripts/backup.ps1:33` — 备份默认包含 .env 且不加密，仅显式 ExcludeEnv 才排除，包被共享时泄漏本机秘密。
- **Ops & Delivery** · `tests/test_native_scripts.py:11` — 启停测试主要是源码字符串断言，Windows smoke 不做实际 restore/冲突/清理失败与恢复后健康验证。
- **Ops & Delivery** · `scripts/seed_demo_data.py:32` — 缺 FFmpeg 时仍插入无媒体路径 Demo 数据并成功退出，数量检查通过但媒体 smoke 不可信。

### LOW (23)

- **Frontend UI** · `app/static/js/subtitle-editor.js:171` — 字幕编辑器已有 escapeHtml、虚拟列表、竞态 token 与自动保存版本控制，是可保留的正向实现。
- **Frontend UI** · `app/static/js/publish-center.js:4` — 前端明确只创建抖音任务，B站保留后端兼容；这是当前产品边界而非应机械删除的代码。
- **Media & Storage** · `app/services/storage_service.py:839-853` — move_task_directory_to_trash 使用 reserve=False 的查询后分配，多个并发删除任务可能选中同一回收站目录；当前属于遗留兼容入口。
- **Media & Storage** · `app/services/storage_service.py:256-301` — allocate_task_dir_name(reserve=True) 先创建目录、后写任务数据库记录，进程在两步之间崩溃会留下无记录孤儿目录。
- **Media & Storage** · `app/services/storage_service.py:503-518` — 上传失败时删除部分文件是 best-effort，清理 OSError 被直接 pass；清理失败不会进入待处理清单，可能留下未引用的半成品源文件。
- **AI Selection** · `app/services/ai_analysis_workflow_service.py:76` — _read_analysis_meta 对文件不存在、读取失败或 JSON 损坏都静默返回空字典；损坏产物会被当作没有元数据，降低长直播覆盖率门禁和故障诊断的可见性。
- **Task Review & Cut** · `app/services/task_lifecycle_service.py:139-225` — 任务目录预占后若后续数据库写入失败，仍缺少统一释放空目录的补偿路径，可能留下少量孤儿目录。
- **Subtitle** · `app/services/subtitle_workflow_service.py:240-257` — 遗留 _activate_subtitle_job 仍可无 lease、revision、status 或 task/output_clip 约束直接激活指定记录；当前主渲染路径未调用，但 task_service 仍导入并保留兼容入口。
- **Pipeline & Job Queue** · `app/services/pipeline_engine.py:1135` — PUBLISH_JOB_CREATING 恢复按当前 workflow_job_id 收集全部匹配 publish_jobs，并将 recovered_ids 全部作为 created 返回；若历史任务存在部分已创建/部分跳过，恢复结果仍可能把混合状态压成单一 created 计数。
- **Pipeline & Job Queue** · `app/services/pipeline_engine.py:1790` — _read_json 对 OSError/JSONDecodeError 静默返回 fallback；排期或 checkpoint JSON 损坏时会被当作默认空结构，故障可见性和人工恢复依据不足。
- **Publish Center** · `app/services/publish_providers.py:204` — _post_multipart 先 file_path.read_bytes() 再 b''.join(chunks)，完整视频和完整 multipart body 同时驻留内存，大文件发布存在约双倍峰值。
- **Publish Center** · `app/services/publish_service.py:1569` — _batch_find_publish_jobs 对每条记录直接调用 _normalize_job(row)，未传入预取 accounts；_normalize_job 会逐条触发 readiness/account 查询，发布中心批量切片时形成 N+1 查询。
- **Publish Center** · `tests/test_publish_history.py:111` — 现有定向测试未覆盖原始 Secret/Token 响应、旧小写历史状态、同步封面 partial、批量无效 ID，以及 API 超时后已接收的重复投稿边界。
- **Publish Scheduler** · `app/services/publish_scheduler.py:274-279` — shutdown 已能追踪并等待后台 Task，但没有超时或取消兜底；若当前 run_once 被数据库锁、Worker 串行查询或文件操作长期阻塞，应用优雅停机仍可能无限等待。
- **Publish Scheduler** · `app/services/publish_scheduler.py:1807-1812` — queue_snapshot 对非法 scheduled_at 捕获 ValueError 后静默 pass；损坏排期会从“今日任务”视图中消失，未产生告警或错误计数，降低数据异常可见性。
- **Publishers & Worker** · `app/services/publishers/page_scripts.py:14` — 页面脚本入口仍通过延迟导入 publish_service 私有函数来转发大量 DOM 脚本；这是有意保留的兼容层，但形成 legacy glue，脚本变更仍会牵连 4750 行 God Service。
- **Publishers & Worker** · `app/services/publishers/browser_runtime.py:315` — screenshot 捕获裸 Exception 后返回空字符串，截图失败不会进入发布结果或健康信号；发布可以继续但缺少关键诊断证据。
- **Publishers & Worker** · `scripts/opencli_host_bridge.py:74` — 旧 OpenCLIHostBridgeHandler 的 /run 处理器本身没有 Bearer Token 校验；当前 main 已转调受保护的 publish_host_worker，默认入口不再使用它，但保留的可直接实例化旧处理器仍会造成安全边界误解。
- **Publishers & Worker** · `tests/test_publish_worker_client.py:155` — 当前测试已覆盖双进程锁竞争、死进程锁回收和常规 execution fencing，但仍未覆盖无身份/损坏旧 journal 被跨 execution 扫描跳过、screenshot 失败信号及旧 bridge 处理器被直接启动时的鉴权边界。
- **SQLite Persistence** · `app/db/database.py:758` — workflow_jobs 认领索引仍只有 status、next_attempt_at、created_at，没有包含 lease_expires_at；过期接管查询在队列增长或大量过期任务时可能扫描更多候选行，属于性能退化而非当前正确性故障。
- **Ops & Delivery** · `scripts/acceptance.ps1:115` — 验收递归扫描正式任务目录，媒体量大/锁文件会显著拖慢或阻断 release gate。
- **Ops & Delivery** · `scripts/backup_restore_runtime.py:142` — import 时 monkeypatch backup core，全局行为依赖导入顺序。
- **Ops & Delivery** · `docs/PORTABLE_SETUP.md:127` — 文档描述与当前 start.ps1 实现不符，旧 Next Steps 又保留兼容入口，启动排障认知漂移。

## Cross-cutting themes

- **本地单体架构是当前可运行的主要原因.** FastAPI + SQLite WAL + 文件产物 + 持久化 Job + Windows Worker 与个人本机规模匹配；不需要微服务化，现有恢复骨架应保留。
- **P0 数据一致性边界已封口，事务型旧债仍需继续.** 活动库外键、测试误删、媒体删除回滚和切片批次原子提交已修复；迁移无账本与索引重建失败静默降级仍是下一阶段重点。
- **Job、发布执行和任务状态代际已封口，下一风险转向跨进程精确续跑.** Workflow lease token、Publish execution fencing、任务状态转换、取消恢复和切片原子提交已经完成；下一阶段重点是持久化 step checkpoint 与副作用复用。
- **可用性 fallback 正在掩盖降级结果.** AI 部分窗口、转写旧产物、损坏 JSON、封面生成、批量发布和风险字段多处静默继续，用户不一定能区分完整成功、部分成功和旧结果。
- **发布与配置读取是主要安全边界.** Publish Center、AI Config 与 Frontend 仍存在原始 Secret 响应、可选写鉴权和 DOM XSS；Worker 路径与 journal 脱敏已收紧，但本地管理员接口门禁尚未完成。
- **复杂度集中而非全项目平均恶化.** publish_service.py、publish-center.js、app.js、publish_scheduler.py、subtitle_data_service.py 和 transcript_service.py 是主要 God Component；应按业务边界渐进拆分。
- **测试已与活动数据隔离，但 Coverage 与真实故障闭环仍不足.** 637 项测试全部通过，Pytest 已强制使用进程级 sandbox；Coverage 尚未采集，真实平台 E2E 和故障注入仍需在不触发生产副作用的边界下补齐。


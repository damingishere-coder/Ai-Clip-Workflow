<!--
  This file:        .codemap/codemap.md   (written report)
  Interactive map:  .codemap/codemap.html
-->

# NiuMa Studio — Functional Module Quality Audit

> **Interactive view:** [`.codemap/codemap.html`](codemap.html) — per-module scores, findings, LoC, and the dependency graph. This file is the written report.

**Generated:** 2026-08-24 · **Modules:** 13 · **Size:** 54187 tracked LoC across 131 files

## Health by layer

| Layer | Modules | Avg score |
|---|--:|--:|
| 界面 · API | 2 | 68 |
| 业务编排 | 3 | 73 |
| 媒体与 AI 处理 | 4 | 70 |
| 外部执行边界 | 2 | 77 |
| 持久化与运维 | 2 | 70 |

## Per-module lines of code & score

_LoC is the representative file/folder per module; folder-level modules overlap and are not additive._

### 界面 · API

| Module | LoC | Score | Tags |
|---|--:|:--|:--|
| Frontend UI | 13,737 | 71 C | god-component, bloat, glue, duplication, legacy, silent-except |
| API & Runtime | 2,930 | 65 C | legacy, dual-format, bloat, glue, fallback |

### 业务编排

| Module | LoC | Score | Tags |
|---|--:|:--|:--|
| Publish Center | 5,654 | 65 C | god-component, bloat, legacy, dual-format, fallback, silent-except, placeholder, duplication |
| Pipeline & Job Queue | 3,799 | 76 B | fallback, silent-except, legacy, bloat, god-component, glue |
| Task Review & Cut | 3,067 | 78 B | god-component, glue, fallback, dual-format, legacy, over-fit |

### 媒体与 AI 处理

| Module | LoC | Score | Tags |
|---|--:|:--|:--|
| AI Selection | 4,818 | 68 C | fallback, silent-except, legacy, dual-format, placeholder, fake-output, bloat, god-component, glue, over-fit |
| Subtitle | 3,067 | 64 C | fallback, silent-except, legacy, duplication, bloat, god-component |
| Transcription | 2,276 | 64 C | fallback, silent-except, duplication, god-component |
| Media & Storage | 1,635 | 82 B | legacy, dual-format, fallback, silent-except |

### 外部执行边界

| Module | LoC | Score | Tags |
|---|--:|:--|:--|
| Publishers & Worker | 2,738 | 80 B | legacy, glue, silent-except, fallback |
| Publish Scheduler | 2,362 | 74 C | god-component, bloat, legacy, silent-except |

### 持久化与运维

| Module | LoC | Score | Tags |
|---|--:|:--|:--|
| Ops & Delivery | 4,348 | 68 C | fallback, legacy, dual-format, duplication, bloat, glue, silent-except, monkeypatch |
| SQLite Persistence | 3,756 | 72 C | legacy, dual-format, bloat, god-component, glue |

## Worst offenders

- **Transcription (64/C)** — app/services/transcript_service.py:1099-1129,1159-1196: faster-whisper 的 model.transcribe 与模型加载没有单次调用或绝对时限；模型/解码器卡住时只能依赖外层 Worker，直接转写路径可能长期占用。
- **Subtitle (64/C)** — app/services/subtitle_auto_workflow_service.py:42-50,51-108: prepare_task_subtitle_review 先执行 ensure_source_track/ensure_clip_track 并提交 revision/track 副作用，之后才检查活动 Job 与当前 lease；旧 Worker 失租或并发冲突时仍可能留下字幕数据库变更。
- **API & Runtime (65/C)** — app/routers/publish.py:59: OAuth URL、OAuth callback、任务处理及字幕路由仍将 str(exc) 直接放入 HTTP detail/重定向消息（如 publish.py:59-70、tasks.py:217-218、subtitles.py:248-256），可能泄露内部路径、URL、数据库或 FFmpeg 错误；全局 RequestValidationError 脱敏未覆盖运行时异常。
- **Publish Center (65/C)** — app/services/publish_service.py:1: publish_service.py 仍约4776行，集中承担配置、账号、OAuth、文案、封面、队列、历史、OpenCLI兼容和API发布职责，属于真实 God Component，修改影响面较大。
- **AI Selection (68/C)** — app/services/ai_analysis_workflow_service.py:923-940: AI 分析成功后的落库不是原子操作：先独立替换 clip_candidates，再写 analysis.json，最后才插入 ai_analysis_runs。若中途数据库、文件写入或历史记录插入失败，会留下新候选/新文件但缺少对应历史 run，旧 active run 与当前候选不一致；失败处理只更新任务状态，不能回滚前面已提交的结果。
- **Ops & Delivery (68/C)** — scripts/migrate_task_dirs_to_project_names.py:211: 任务目录迁移用非 WAL-aware 主库 copy，先移动目录再统一更新提交，无文件补偿，异常会让路径/DB 不一致。
- **Frontend UI (71/C)** — app/static/js/app.js:1-2411: app.js 约 2,400 行，集中承担任务、转写、AI、审核、切片、字幕和配置交互；publish-center.js 另有约 2,200 行，前端状态与请求逻辑集中，修改和回归半径较大。
- **SQLite Persistence (72/C)** — app/db/database.py:614: init_db 仍先执行历史列探测迁移 helper，再执行账本迁移；helper 内部使用 executescript（如 :1074），会隐式提交此前 DML，后续 helper 失败时可能留下部分 Schema/数据变更而无对应 schema_migrations 记录。
- **Publish Scheduler (74/C)** — app/services/publish_scheduler.py:756-785: recover_interrupted_jobs 每轮加载全部 PUBLISHING 任务，并逐个同步调用 Worker execution；没有批量上限、并发控制或退避，Worker 超时或不可用时恢复耗时随任务数线性增长并阻塞后续排期。
- **Pipeline & Job Queue (76/B)** — app/services/pipeline_engine.py:1529: 恢复文案时只按 output_clip_id、platform、request_fingerprint 复用 cached 项，没有排除 metadata.error 或失败状态；MetadataGenerator 会把 AI 失败的规则降级结果持久化，后续同指纹运行可能持续复用该失败结果，阻断真正重试。

## All findings

### HIGH (5)

- **AI Selection** · `app/services/ai_analysis_workflow_service.py:923-940` — AI 分析成功后的落库不是原子操作：先独立替换 clip_candidates，再写 analysis.json，最后才插入 ai_analysis_runs。若中途数据库、文件写入或历史记录插入失败，会留下新候选/新文件但缺少对应历史 run，旧 active run 与当前候选不一致；失败处理只更新任务状态，不能回滚前面已提交的结果。
- **AI Selection** · `app/services/ai_analysis_workflow_service.py:289-314,493-507,864-952` — 人工 POST /api/tasks/{task_id}/process/ai 不创建或持有持久化 Workflow Job；无 active lease 时 _assert_current_job_lease 直接返回，_claim_ai_analysis 只有短暂的 ai_analyzing 状态占用，最终还会无条件 update_task_status。人工线程卡住、进程重启或任务被其他流程接管后，旧执行仍可能写结果并覆盖终态，缺少跨进程 fencing 和恢复账本。
- **AI Selection** · `app/services/ai/ai_clip_analyzer.py:105-149; app/services/ai/variety_comedy_analyzer.py:76-142` — 普通/综艺分析会逐窗口捕获异常并跳过失败窗口，只在 analysis_summary 中附带局部失败提示；没有 analysis_incomplete 或覆盖率门禁，流程仍会进入 pending_review 并允许后续切片。AI 5xx、超时或错误 JSON 可能导致候选不完整，但系统把结果当作正常成功结果处理。长直播路径有完整性门禁，三种分析口径不一致。
- **Task Review & Cut** · `app/services/task_service.py:458-485,594-616` — 任务详情默认执行无 timeout 的 ffprobe，且未捕获 OSError/Path.stat 异常；NAS、损坏或卡死媒体可能阻塞详情请求或返回 500。明确延期至 P1.4。
- **Ops & Delivery** · `scripts/migrate_task_dirs_to_project_names.py:211` — 任务目录迁移用非 WAL-aware 主库 copy，先移动目录再统一更新提交，无文件补偿，异常会让路径/DB 不一致。

### MED (49)

- **Frontend UI** · `app/static/js/app.js:1-2411` — app.js 约 2,400 行，集中承担任务、转写、AI、审核、切片、字幕和配置交互；publish-center.js 另有约 2,200 行，前端状态与请求逻辑集中，修改和回归半径较大。
- **Frontend UI** · `app/static/css/styles.css:874-1690` — 多处使用未在 :root 定义的 CSS 变量（--border、--border-color、--muted-text、--primary-color、--text-color），相关边框或文字颜色声明会失效，造成界面表现不一致。
- **Frontend UI** · `tests/test_publish_center_browser.py:13-16` — Playwright 使用 pytest.importorskip；环境缺少 Playwright 时整组浏览器测试静默跳过，发送中心交互、字幕交互及真实浏览器鉴权路径缺少强制回归门槛。
- **API & Runtime** · `app/routers/publish.py:59` — OAuth URL、OAuth callback、任务处理及字幕路由仍将 str(exc) 直接放入 HTTP detail/重定向消息（如 publish.py:59-70、tasks.py:217-218、subtitles.py:248-256），可能泄露内部路径、URL、数据库或 FFmpeg 错误；全局 RequestValidationError 脱敏未覆盖运行时异常。
- **API & Runtime** · `app/routers/tasks.py:211` — async process_audio 直接调用同步长耗时服务；publish.py:251-282 的封面生成、settings.py:10-17 的本地诊断及 pages.py 多个页面也直接执行同步 DB/FFmpeg/子进程调用，长请求会阻塞 FastAPI event loop。
- **API & Runtime** · `app/models/task.py:38` — TaskStatus 仍同时保留 uppercase 自动流程状态和 lowercase legacy 状态（:39-72），API/数据库继续接受双协议，状态转换和校验存在歧义。
- **Media & Storage** · `app/services/storage_service.py:362-368,486-487` — resolve_video_file_path 对已存在路径直接返回，不验证任务受控目录；get_source_video_path 及 publish/subtitle/task 等非 HTTP 调用方共享该入口，输出路径异常时仍可能读取或上传外部文件。
- **Transcription** · `app/services/transcript_service.py:1099-1129,1159-1196` — faster-whisper 的 model.transcribe 与模型加载没有单次调用或绝对时限；模型/解码器卡住时只能依赖外层 Worker，直接转写路径可能长期占用。
- **Transcription** · `app/services/transcript_service.py:164-191` — FFmpeg 音频提取只有无进展 watchdog，没有绝对 wall-clock deadline；进程持续输出进度但实际不结束时仍可能无限运行。
- **Transcription** · `app/services/transcript_service.py:981-989` — 进度文件损坏、读取失败或顶层非 dict 时统一返回空字典，静默丢失故障证据，恢复逻辑无法区分初始状态与持久化损坏。
- **Transcription** · `app/services/transcript_service.py:67-77,1259-1270` — 活动 provider/model/device/compute_type 仍使用模块级可变全局；同一进程并发转写会互相覆盖运行元数据和进度展示。
- **Transcription** · `app/services/transcript_service.py:347-549` — 本地与火山远程分片分别复制 chunk、checkpoint、进度和异常收口逻辑，两个实现继续存在维护分叉。
- **Transcription** · `app/services/transcript_service.py:83-1395` — 单文件仍混合 FFmpeg、faster-whisper、远程 HTTP、幂等 checkpoint、进度文件和 Markdown 生成，约 1400 行，职责边界和回归影响面偏大。
- **AI Selection** · `app/services/ai/long_live_talk_analyzer.py:119-149; app/services/ai/base.py:64-83` — 长直播远程分析自身最多重试 3 次，而每次又调用最多重试 3 次的 generate_json_with_safe_retry；429/连接建立失败时单个窗口最多发起 9 次请求。虽然这些错误被标记为 safe_to_retry，仍会放大延迟、限流压力和故障恢复时间，缺少全链路统一 retry budget。
- **AI Selection** · `app/services/ai/ai_clip_analyzer.py:414-490,597-615` — AI 输出只要能解析为 JSON，就会通过别名映射、literal_eval 和默认值补齐缺失字段；缺少标题、摘要、推荐理由、置信度等字段的响应会被合成为看似完整的候选片段。此兼容逻辑降低了错误 JSON 的可见性，可能把模型 schema 退化误判为有效业务结果。
- **AI Selection** · `app/services/ai_analysis_workflow_service.py:464-465,532-548,638-671,698-703,864-870` — AI 工作流服务约 985 行，同时承担 Provider 编排、任务状态、租约检查、候选替换、文件落盘、历史 run、恢复和 API 返回；大量动态导入 task_service 形成 glue 与循环依赖。修改任一 AI 流程的 blast radius 较大，恢复和查询语义也难以独立测试。
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
- **Publish Center** · `app/services/publish_service.py:1` — publish_service.py 仍约4776行，集中承担配置、账号、OAuth、文案、封面、队列、历史、OpenCLI兼容和API发布职责，属于真实 God Component，修改影响面较大。
- **Publish Center** · `app/services/publish_service.py:2968` — 历史查询 SQL 仍先按大写 PUBLISH_HISTORY_STATUSES 过滤，再做 legacy 状态归一化；数据库中的 lowercase/旧状态记录会在归一化前被排除，历史页与统计可能漏数据。
- **Publish Center** · `app/services/publish_service.py:2173` — sync_task_publish_jobs 中封面生成异常仅写入 cover_error 后继续创建 WAITING 发布任务，未计入 errors；同步接口可能返回 ok，但任务实际缺少封面，后续发送就绪检查才失败。
- **Publish Center** · `app/services/publish_service.py:2818` — create_batch_publish_jobs 对不存在的 output_clip_id 直接 continue，最后无论缺失多少输入都返回 status=ok，调用方无法区分全部无效、部分成功和完整成功。
- **Publish Center** · `app/services/publish_readiness.py:167` — PUBLISH_MODES 仍允许 api_publish，但 build_send_readiness 的 resolved_mode 只接受 local_browser/manual_export；API 模式可以创建，却在发送前统一判定 unsupported_publish_mode。
- **Publish Center** · `app/services/publish_providers.py:141` — BilibiliPublishProvider 完成配置和 access_token 校验后固定抛出 bilibili_provider_pending，属于可创建、可排队但无法执行的真实发布入口，仍是 placeholder/stub。
- **Publish Center** · `app/services/publish_providers.py:81` — DouyinPublishProvider 将上传和创建作品拆成两个非幂等 HTTP 调用；上传已被平台接受但客户端读取响应时超时或断网，重试可能产生重复作品，未转入人工复核或使用幂等键。
- **Publish Scheduler** · `app/services/publish_scheduler.py:756-785` — recover_interrupted_jobs 每轮加载全部 PUBLISHING 任务，并逐个同步调用 Worker execution；没有批量上限、并发控制或退避，Worker 超时或不可用时恢复耗时随任务数线性增长并阻塞后续排期。
- **Publish Scheduler** · `app/services/publish_scheduler.py:283-304` — list_due_jobs 每轮 SELECT 全部 SCHEDULED 记录，再逐条在 Python 中解析并过滤时间，没有 SQL due 条件、分页或批量上限，排期规模增长会增加扫描内存和调度延迟。
- **Publish Scheduler** · `app/services/publish_scheduler.py:133-1789` — PublishScheduler 约 1,700 行并集中承担排期计算、任务领取、结果写回、Worker 恢复、重试修复、人工复核、批量排期及旧版兼容，状态路径修改的回归半径较大。
- **Publishers & Worker** · `scripts/publish_host_worker.py:297` — _prior_job_execution_requires_review 会跳过 corrupt 或缺失/无效 identity 的旧 journal；若旧执行已开始上传但身份未持久化，重试可能无法关联并再次提交。browser_opened 仍属于 safe_retry_phases，存在浏览器动作已发生而 journal 尚未推进的重复发布窗口。
- **SQLite Persistence** · `app/db/database.py:614` — init_db 仍先执行历史列探测迁移 helper，再执行账本迁移；helper 内部使用 executescript（如 :1074），会隐式提交此前 DML，后续 helper 失败时可能留下部分 Schema/数据变更而无对应 schema_migrations 记录。
- **SQLite Persistence** · `scripts/backup_restore.py:362` — 备份验证仅执行 quick_check、必要表/行数和 manifest 文件哈希校验，未执行 PRAGMA foreign_key_check，也未核对 schema_migrations 版本/checksum；外键损坏或结构账本漂移可能被当作可恢复备份。
- **SQLite Persistence** · `scripts/backup_restore.py:602` — restore_backup_bundle 在替换数据库及删除 -wal/-shm 前没有活动应用/Worker 互斥或停止检查；运行中的 SQLite 连接可能继续写入旧 inode/WAL，造成恢复后丢写或双数据库视图。
- **Ops & Delivery** · `.github/workflows/ci.yml:3` — CI 仅监听 master push/PR；feature/docs 直接 push 不即时验证，问题延迟到开 PR。
- **Ops & Delivery** · `scripts/start_docker_opencli.ps1:24` — 健康检查异常只警告不返回非零，服务失败也可能被调用方视为启动成功。
- **Ops & Delivery** · `scripts/start.ps1:30` — Demo 覆盖生效前先对正式 E 盘配置运行 doctor，新机/迁移机的隔离 Demo 可能被旧路径阻断。
- **Ops & Delivery** · `scripts/start.ps1:71` — Worker 启动异常被捕获后继续并报告工作台成功，发布能力不可用时易被误判完整健康。
- **Ops & Delivery** · `scripts/restore.ps1:27` — 恢复只以健康响应判断 App 运行，StopServices 只停 Compose；native/异常健康进程可能仍持有 DB 时执行替换。
- **Ops & Delivery** · `scripts/backup.ps1:33` — 备份默认包含 .env 且不加密，仅显式 ExcludeEnv 才排除，包被共享时泄漏本机秘密。
- **Ops & Delivery** · `tests/test_native_scripts.py:11` — 启停测试主要是源码字符串断言，Windows smoke 不做实际 restore/冲突/清理失败与恢复后健康验证。
- **Ops & Delivery** · `scripts/seed_demo_data.py:32` — 缺 FFmpeg 时仍插入无媒体路径 Demo 数据并成功退出，数量检查通过但媒体 smoke 不可信。

### LOW (25)

- **Frontend UI** · `app/static/js/app.js:1-8,114-1716` — apiFetch 与约 20 余处直接 fetch 并存，重复实现 JSON 解析和错误处理。由于本轮已移除浏览器 Token 注入，远程启用 LOCAL_ADMIN_TOKEN 时这些浏览器请求不会携带 Authorization，页面可加载但 API 交互无法正常认证；本地回环模式不受影响。
- **Frontend UI** · `app/static/js/app.js:241-248` — 转写轮询启动和首次请求使用 catch(() => {}) 静默吞掉异常；请求失败时没有用户可见错误或统一重试反馈，可能使进度页面停留在旧状态。
- **API & Runtime** · `app/core/config.py:157` — 仍维护 AI_REMOTE_* 旧配置与新分域配置两套来源，并通过 _env_first fallback 兼容旧脚本，配置修改可能出现来源漂移和行为不一致。
- **API & Runtime** · `app/routers/tasks.py:1` — tasks.py 与 publish.py 仍是大型路由编排文件，重复 try/except、同步服务调用和状态映射；这种 glue/bloat 使错误脱敏、异步边界和协议兼容规则难以统一维护。
- **Media & Storage** · `app/services/storage_service.py:839-853` — move_task_directory_to_trash 使用 reserve=False 的查询后分配，多个并发删除任务可能选中同一回收站目录；当前属于遗留兼容入口。
- **Media & Storage** · `app/services/storage_service.py:256-301` — allocate_task_dir_name(reserve=True) 先创建目录、后写任务数据库记录，进程在两步之间崩溃会留下无记录孤儿目录。
- **Media & Storage** · `app/services/storage_service.py:503-518` — 上传失败时删除部分文件是 best-effort，清理 OSError 被直接 pass；清理失败不会进入待处理清单，可能留下未引用的半成品源文件。
- **AI Selection** · `app/services/ai_analysis_workflow_service.py:638-671` — _ensure_ai_analysis_history_from_current_file 在数据库没有历史时，会从 analysis.json 兼容性地合成 active run；该路径只做 JSON 可解析检查，不验证结果 checksum、schema、生成代际或文件与数据库的一致性。数据库历史丢失或文件被旧流程遗留时，可能静默恢复出错误的历史记录。
- **AI Selection** · `app/services/ai/ai_clip_service.py:4-39` — generate_candidate_clips_placeholder 返回固定的 fake output，当前 app/tests 搜索不到调用方，属于 legacy/placeholder dead code；虽然暂未影响主流程，但误调用会生成伪造候选片段。
- **Task Review & Cut** · `app/services/task_lifecycle_service.py:139-225` — 任务目录预占后若后续数据库写入失败，仍缺少统一释放空目录的补偿路径，可能留下少量孤儿目录。
- **Subtitle** · `app/services/subtitle_workflow_service.py:240-257` — 遗留 _activate_subtitle_job 仍可无 lease、revision、status 或 task/output_clip 约束直接激活指定记录；当前主渲染路径未调用，但 task_service 仍导入并保留兼容入口。
- **Pipeline & Job Queue** · `app/services/pipeline_engine.py:1135` — PUBLISH_JOB_CREATING 恢复按当前 workflow_job_id 收集全部匹配 publish_jobs，并将 recovered_ids 全部作为 created 返回；若历史任务存在部分已创建/部分跳过，恢复结果仍可能把混合状态压成单一 created 计数。
- **Pipeline & Job Queue** · `app/services/pipeline_engine.py:1790` — _read_json 对 OSError/JSONDecodeError 静默返回 fallback；排期或 checkpoint JSON 损坏时会被当作默认空结构，故障可见性和人工恢复依据不足。
- **Publish Center** · `app/services/publish_service.py:1610` — 批量查询每一行都调用 _normalize_job；该函数随后构建 readiness 并触发账号/运行态解析，批量页面可能产生逐行重复查询，数据量增长后会放大响应延迟。
- **Publish Center** · `app/services/publish_providers.py:204` — _post_multipart 使用 read_bytes 将整个视频读入内存，再与 multipart 头部 join；大文件发送峰值内存约为文件大小的两倍，存在资源受限环境下的稳定性风险。
- **Publish Scheduler** · `app/services/publish_scheduler.py:276-281` — shutdown 会等待后台 Task 完整结束，但没有超时或取消兜底；run_once 被 Worker 长超时、数据库锁或文件操作阻塞时，应用停机可能长时间挂起。
- **Publish Scheduler** · `app/services/publish_scheduler.py:1840-1845` — queue_snapshot 对非法 scheduled_at 静默 pass，损坏排期会从今日任务视图消失，未产生告警或异常计数。
- **Publishers & Worker** · `app/services/publishers/browser_runtime.py:315` — screenshot 捕获 Exception 后直接返回空字符串，不记录错误或阶段；发布失败诊断可能静默丢失截图证据。
- **Publishers & Worker** · `app/services/publishers/page_scripts.py:14` — 通过延迟导入 app.services.publish_service 并调用其私有 DOM 脚本维持旧发布协议，形成 Worker/发布服务间的 legacy glue 耦合。
- **SQLite Persistence** · `app/db/database.py:811` — workflow_jobs claim 索引仅覆盖 status、next_attempt_at、created_at，未包含 lease_expires_at；租约过期接管/恢复扫描仍需过滤大量记录，规模增长后会退化。
- **SQLite Persistence** · `app/db/database.py:169` — tasks.status 无 CHECK 约束且默认仍为 pending_video，历史迁移 :1091-1100 继续归一化 lowercase legacy 状态，而 app/models/task.py:38 同时接受新旧两套枚举，数据库层无法保证唯一状态协议。
- **SQLite Persistence** · `scripts/backup_restore_runtime.py:142` — runtime 入口通过 monkeypatch 替换 backup_restore 的全局函数并重新导出 API；core 与 runtime 两套导入路径的行为可能随新增校验漂移，形成 legacy/dual-format glue。
- **Ops & Delivery** · `scripts/acceptance.ps1:115` — 验收递归扫描正式任务目录，媒体量大/锁文件会显著拖慢或阻断 release gate。
- **Ops & Delivery** · `scripts/backup_restore_runtime.py:142` — import 时 monkeypatch backup core，全局行为依赖导入顺序。
- **Ops & Delivery** · `docs/PORTABLE_SETUP.md:127` — 文档描述与当前 start.ps1 实现不符，旧 Next Steps 又保留兼容入口，启动排障认知漂移。

## Cross-cutting themes

- **本地单体架构是当前可运行的主要原因.** FastAPI + SQLite WAL + 文件产物 + 持久化 Job + Windows Worker 与个人本机规模匹配；不需要微服务化，现有恢复骨架应保留。
- **P0 数据一致性与迁移入口已经 fail closed.** 测试误删、活动库外键、媒体删除回滚、切片/字幕批次和迁移账本已处理；仍需在受控停机窗口首次正式迁移，并逐步把旧 helper 收编到账本。
- **Job、发布执行和字幕恢复代际已封口.** Workflow lease token、Publish execution fencing、任务状态转换、取消恢复、切片与字幕原子提交已经完成；旧 Worker/旧 execution 不能覆盖新结果。
- **AI 结果一致性是进入稳定 V1 前的最后 P1 缺口.** 第三方超时、429/5xx、坏 JSON 和计费不确定重试已经收紧；但候选/run/终态半提交、人工 AI 无持久 lease、普通/综艺局部失败仍需 P1.4b 收口。
- **本地安全边界已从隐式信任变为显式 fail closed.** AI/Publish Secret DTO、旧 Provider 结果脱敏、loopback client 校验、Docker 回环绑定、Origin 白名单、作品 URL 和动态 DOM XSS 已收紧；不扩大为多用户权限系统。
- **复杂度集中而非全项目平均恶化.** publish_service.py、publish-center.js、app.js、publish_scheduler.py、subtitle_data_service.py 和 transcript_service.py 是主要 God Component；应按业务边界渐进拆分。
- **测试已与活动数据隔离，但 Coverage 与真实故障闭环仍不足.** Pytest 已强制使用进程级 sandbox，P0/P1 失败矩阵持续扩充；Coverage 尚未采集，Playwright 可选跳过和真实平台/进程故障闭环仍需在 P2 补齐。


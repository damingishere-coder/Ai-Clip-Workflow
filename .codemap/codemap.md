<!--
  This file:        .codemap/codemap.md   (written report)
  Interactive map:  .codemap/codemap.html
-->

# NiuMa Studio — Functional Module Quality Audit

> **Interactive view:** [`.codemap/codemap.html`](codemap.html) — per-module scores, findings, LoC, and the dependency graph. This file is the written report.

**Generated:** 2026-08-24 · **Modules:** 13 · **Size:** 55768 tracked LoC across 132 files

## Health by layer

| Layer | Modules | Avg score |
|---|--:|--:|
| 界面 · API | 2 | 62 |
| 业务编排 | 3 | 66 |
| 媒体与 AI 处理 | 4 | 69 |
| 外部执行边界 | 2 | 77 |
| 持久化与运维 | 2 | 70 |

## Per-module lines of code & score

_LoC is the representative file/folder per module; folder-level modules overlap and are not additive._

### 界面 · API

| Module | LoC | Score | Tags |
|---|--:|:--|:--|
| Frontend UI | 13,764 | 62 C | bloat, glue, fallback, silent-except, legacy, over-fit |
| API & Runtime | 2,948 | 63 C | fallback, legacy, dual-format, bloat, glue |

### 业务编排

| Module | LoC | Score | Tags |
|---|--:|:--|:--|
| Publish Center | 5,654 | 65 C | god-component, bloat, legacy, dual-format, fallback, silent-except, placeholder, duplication |
| Pipeline & Job Queue | 4,095 | 66 C | fallback, silent-except, legacy, stub, bloat, god-component, glue |
| Task Review & Cut | 3,094 | 68 C | god-component, glue, duplication, fallback, dual-format, legacy |

### 媒体与 AI 处理

| Module | LoC | Score | Tags |
|---|--:|:--|:--|
| AI Selection | 6,031 | 70 C | fallback, legacy, dual-format, placeholder, fake-output, bloat, god-component |
| Subtitle | 3,067 | 64 C | fallback, silent-except, legacy, duplication, bloat, god-component |
| Transcription | 2,276 | 64 C | fallback, silent-except, duplication, god-component |
| Media & Storage | 1,635 | 78 B | fallback, legacy, dual-format, silent-except |

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

- **Frontend UI (62/C)** — app/templates/clip_review.html:154; app/static/js/app.js:103-132: 片段审核空状态的“运行 Codex CLI 分析”仍绑定通用 handleProcessAction；/process/ai 现在返回持久化 job_id 后，该处理器没有等待或轮询 Job，而是在收到排队响应后直接 window.location.reload()。用户会看到仍无候选片段的旧页面，并可能在后台任务未完成时再次点击，造成重复请求或重复计费边界。
- **API & Runtime (63/C)** — app/routers/publish.py:59: OAuth URL 与 callback 仍捕获 broad Exception，并把 str(exc) 直接放入 HTTP detail 或重定向查询参数；tasks.py、subtitles.py 也存在同类映射。异常文本可能包含本地路径、数据库、FFmpeg 或第三方接口诊断，形成浏览器可见的信息泄漏。
- **Transcription (64/C)** — app/services/transcript_service.py:1099-1129,1159-1196: faster-whisper 的 model.transcribe 与模型加载没有单次调用或绝对时限；模型/解码器卡住时只能依赖外层 Worker，直接转写路径可能长期占用。
- **Subtitle (64/C)** — app/services/subtitle_auto_workflow_service.py:42-50,51-108: prepare_task_subtitle_review 先执行 ensure_source_track/ensure_clip_track 并提交 revision/track 副作用，之后才检查活动 Job 与当前 lease；旧 Worker 失租或并发冲突时仍可能留下字幕数据库变更。
- **Publish Center (65/C)** — app/services/publish_service.py:1: publish_service.py 仍约4776行，集中承担配置、账号、OAuth、文案、封面、队列、历史、OpenCLI兼容和API发布职责，属于真实 God Component，修改影响面较大。
- **Pipeline & Job Queue (66/C)** — app/services/pipeline_engine.py:1600: _write_json_atomic 只在写临时文件前后调用 require_active_job_lease，最终 temporary_path.replace(path) 以及 _create_schedule 的直接 write_text（1614-1616）不带 owner/lease 条件；租约在最后检查后被接管时，旧 Worker 仍可能覆盖新 Worker 的 metadata/schedule 派生文件。数据库 Job 状态虽已 fencing，但文件产物仍存在跨进程陈旧写入窗口。
- **Task Review & Cut (68/C)** — app/services/video_cut_workflow_service.py:516-582; app/services/video_cut_workflow_service.py:87-105: process_task_video_cuts 创建切片批次前仍未校验任务必须处于允许切片的状态，也未检查同一任务是否已有其他 queued/running workflow job；_create_cut_run 只排除 CANCELLED，能够把任意非取消任务写成 cutting。同步切片、AI 候选替换和异步切片并发时仍存在过期候选和状态互相覆盖风险。
- **Ops & Delivery (68/C)** — scripts/migrate_task_dirs_to_project_names.py:211: 任务目录迁移用非 WAL-aware 主库 copy，先移动目录再统一更新提交，无文件补偿，异常会让路径/DB 不一致。
- **AI Selection (70/C)** — app/services/ai_analysis_workflow_service.py:1273-1320,1322-1360: active run 的 payload clips、clip_candidates 和 analysis.json 分开读取；恢复/返回路径未校验三者的数量、ID、内容及代际一致性，候选账本漂移时仍可能把 active run 当作可信结果继续展示或恢复。
- **SQLite Persistence (72/C)** — app/db/database.py:614: init_db 仍先执行历史列探测迁移 helper，再执行账本迁移；helper 内部使用 executescript（如 :1074），会隐式提交此前 DML，后续 helper 失败时可能留下部分 Schema/数据变更而无对应 schema_migrations 记录。

## All findings

### HIGH (1)

- **Ops & Delivery** · `scripts/migrate_task_dirs_to_project_names.py:211` — 任务目录迁移用非 WAL-aware 主库 copy，先移动目录再统一更新提交，无文件补偿，异常会让路径/DB 不一致。

### MED (60)

- **Frontend UI** · `app/templates/clip_review.html:154; app/static/js/app.js:103-132` — 片段审核空状态的“运行 Codex CLI 分析”仍绑定通用 handleProcessAction；/process/ai 现在返回持久化 job_id 后，该处理器没有等待或轮询 Job，而是在收到排队响应后直接 window.location.reload()。用户会看到仍无候选片段的旧页面，并可能在后台任务未完成时再次点击，造成重复请求或重复计费边界。
- **Frontend UI** · `app/static/js/app.js:437-490; app/templates/clip_review.html:194-198` — AI 结果卡片始终按“AI 分析完成”渲染并显示“去检查并生成切片”，片段审核页只按是否存在 clips 启用生成切片和同步发送中心按钮；前端没有读取或展示 analysis_incomplete、quality_degraded、partial 等结果标记。后端虽可拒绝不合格切片，但页面会把降级/不完整结果呈现为正常结果，用户仍会尝试后续操作。
- **Frontend UI** · `app/static/js/app.js:1927-1945` — AI 状态轮询只在成功读取后安排下一次定时器；fetch、JSON 解析或临时网络异常会被调用方 catch(() => {}) 静默吞掉，且不会重新调度，导致页面永久停止刷新而无提示。轮询恢复没有退避、重试上限或明确的读取失败状态。
- **Frontend UI** · `app/static/js/app.js:1805-1822` — waitForAiAnalysisJob 是无限 while 循环，每秒直接 fetch，没有 AbortController、总超时或网络错误重试；一次临时读取异常就会跳到外层“AI 分析失败”，但服务端 Job 仍可能继续执行，前端重新启用按钮，用户可能再次发起请求并误判任务已失败。
- **Frontend UI** · `app/static/js/app.js:711-718; app/static/js/app.js:1927-1946` — 页面重新打开时只轮询 AI 状态和运行日志；当后台 Job 在该页面生命周期内完成后，轮询停止，但不会自动重新读取 ai-analysis-runs 或渲染最新结果。跨页面/跨进程恢复场景可能显示 100%/已完成，却仍保留旧候选片段和旧历史，必须人工刷新历史或整页刷新。
- **API & Runtime** · `app/routers/publish.py:59` — OAuth URL 与 callback 仍捕获 broad Exception，并把 str(exc) 直接放入 HTTP detail 或重定向查询参数；tasks.py、subtitles.py 也存在同类映射。异常文本可能包含本地路径、数据库、FFmpeg 或第三方接口诊断，形成浏览器可见的信息泄漏。
- **API & Runtime** · `app/routers/tasks.py:212` — async process_audio 直接调用同步的 process_task_audio，服务层会执行 FFmpeg/文件与数据库操作；process_video_cuts:392-400 同样直接调用同步切片流程。长请求会阻塞 FastAPI event loop，使轮询、健康检查和其他 API 排队，并增加超时后的重复请求风险。
- **API & Runtime** · `app/models/task.py:38` — TaskStatus 同时保留 uppercase 自动流程状态与 lowercase legacy 状态（pending_processing、ai_analyzing、completed 等），同一业务状态存在双协议；API、数据库查询和状态转换需要维护两套值，容易出现状态筛选和迁移口径不一致。
- **API & Runtime** · `app/models/task.py:243` — 批量输入列表缺少统一上限：PublishBatchScheduleUpdate.job_ids/confirmed_schedule、PublishBatchJobCreate.output_clip_ids:287、ClipCandidateBatchUpdate.clips:406 与 PublishBatchTargetUpdate.job_ids 均可任意增长。对应路由会逐项执行数据库、文件或发布操作，缺少请求级资源上限，异常大请求可造成内存和处理时间消耗。
- **Media & Storage** · `app/services/storage_service.py:693` — 任务媒体删除已写入 manifest 并分阶段移动，但没有跨进程扫描、加载或恢复入口。进程在暂存完成、数据库提交前崩溃会使数据库仍可见任务的原目录已被移走；数据库提交后、finalize 前崩溃则 cleanup_pending 只存在于本次响应，重启后不会自动清理或恢复。
- **Media & Storage** · `app/services/storage_service.py:362` — resolve_video_file_path 对已存在路径直接返回，不验证路径是否位于允许根目录；get_source_video_path 及多个非 HTTP 调用方复用该入口，数据库中的异常视频/产物路径仍可能被发布、字幕或任务查询流程读取为本地任意文件。HTTP 媒体路由已有更严格的任务目录校验，但共享原语本身仍不安全。
- **Transcription** · `app/services/transcript_service.py:1099-1129,1159-1196` — faster-whisper 的 model.transcribe 与模型加载没有单次调用或绝对时限；模型/解码器卡住时只能依赖外层 Worker，直接转写路径可能长期占用。
- **Transcription** · `app/services/transcript_service.py:164-191` — FFmpeg 音频提取只有无进展 watchdog，没有绝对 wall-clock deadline；进程持续输出进度但实际不结束时仍可能无限运行。
- **Transcription** · `app/services/transcript_service.py:981-989` — 进度文件损坏、读取失败或顶层非 dict 时统一返回空字典，静默丢失故障证据，恢复逻辑无法区分初始状态与持久化损坏。
- **Transcription** · `app/services/transcript_service.py:67-77,1259-1270` — 活动 provider/model/device/compute_type 仍使用模块级可变全局；同一进程并发转写会互相覆盖运行元数据和进度展示。
- **Transcription** · `app/services/transcript_service.py:347-549` — 本地与火山远程分片分别复制 chunk、checkpoint、进度和异常收口逻辑，两个实现继续存在维护分叉。
- **Transcription** · `app/services/transcript_service.py:83-1395` — 单文件仍混合 FFmpeg、faster-whisper、远程 HTTP、幂等 checkpoint、进度文件和 Markdown 生成，约 1400 行，职责边界和回归影响面偏大。
- **AI Selection** · `app/services/ai_analysis_workflow_service.py:1273-1320,1322-1360` — active run 的 payload clips、clip_candidates 和 analysis.json 分开读取；恢复/返回路径未校验三者的数量、ID、内容及代际一致性，候选账本漂移时仍可能把 active run 当作可信结果继续展示或恢复。
- **AI Selection** · `app/services/ai/ai_clip_analyzer.py:414-490,597-615` — AI JSON 解析通过别名映射、literal_eval 和默认值补齐缺失标题、摘要、理由、置信度等字段；模型 schema 退化时仍可能被合成为看似完整的候选结果。
- **AI Selection** · `app/services/ai_analysis_workflow_service.py:1-1470` — 单文件约 1400 行，同时承担 Provider 编排、任务状态、租约、候选替换、文件落盘、历史 run、恢复和 API 返回；职责与测试边界过宽，修改 blast radius 较大。
- **Task Review & Cut** · `app/services/video_cut_workflow_service.py:516-582; app/services/video_cut_workflow_service.py:87-105` — process_task_video_cuts 创建切片批次前仍未校验任务必须处于允许切片的状态，也未检查同一任务是否已有其他 queued/running workflow job；_create_cut_run 只排除 CANCELLED，能够把任意非取消任务写成 cutting。同步切片、AI 候选替换和异步切片并发时仍存在过期候选和状态互相覆盖风险。
- **Task Review & Cut** · `app/services/task_lifecycle_service.py:373-406` — 公共状态转换的完成前置条件仍只查询 output_clip 数据库记录，不验证 output_file_path 对应文件真实存在，也不核对当前 active cut_run。残留或损坏的 output_clip 索引可以让任务被标记为 completed，主状态与实际产物不一致。
- **Task Review & Cut** · `app/services/task_query_service.py:234-272` — 片段总览的 can_cut 仍只依据 enabled_count 和 source_exists，不读取已统一校验的 analysis_incomplete/quality_degraded，也不排除 AI_ANALYZING 等运行状态。后端已阻止质量降级或不完整分析切片时，页面仍会显示可生成切片。
- **Task Review & Cut** · `app/services/task_query_service.py:204-208; app/services/task_query_service.py:245-304; app/services/task_query_service.py:417-428` — Dashboard、片段总览和系统状态统计只识别 lowercase completed/completed_with_errors，而全自动流水线使用 uppercase COMPLETED 状态，自动完成任务可能漏计或显示为待前置/待检查，继续保留两套状态格式的展示不一致。
- **Task Review & Cut** · `app/services/task_service.py:687-744` — list_clip_candidates 对每条历史候选直接解析 start_time/end_time 及多个评分字段，单条坏时间或坏数值格式会让整个审核列表抛 ValueError，阻断人工检查和后续修复。
- **Task Review & Cut** · `app/services/task_service.py:926-960` — _active_outputs_match_enabled_candidates 只比较启用候选 ID、数量和 output_file_path 是否存在，不验证文件内容、大小、可播放性或 source_fingerprint。路径存在但文件损坏或被外部替换时，审核同步可能错误跳过重新切片。
- **Task Review & Cut** · `app/services/task_service.py:1-101; app/services/task_service.py:1246-1299; app/services/task_query_service.py:89-172` — TaskService 仍约 1,300 行，同时承担任务状态、AI、字幕、转写、切片、数据库写入和页面查询；底部保留多组只转发到 task_query_service 的包装函数，而 task_query_service 又复制 list_output_clips 的路径解析、字幕状态和发布就绪逻辑，核心修改的 blast radius 仍较大。
- **Subtitle** · `app/services/subtitle_auto_workflow_service.py:42-50,51-108` — prepare_task_subtitle_review 先执行 ensure_source_track/ensure_clip_track 并提交 revision/track 副作用，之后才检查活动 Job 与当前 lease；旧 Worker 失租或并发冲突时仍可能留下字幕数据库变更。
- **Subtitle** · `app/services/subtitle_data_service.py:1017-1074` — _load_source_cues 对 checksum、JSON 或 segment 错误直接跳过；只要其他 chunk 仍有 cue 就生成部分字幕 revision，损坏转写会被静默降级为缺句结果。
- **Subtitle** · `app/services/subtitle_data_service.py:1222-1230,236-330` — _sync_dependent_clip_tracks 逐条调用各自事务的 sync_clip_track；源轨已提交后任一切片同步失败，会留下部分 up_to_date、部分 pending_sync 状态。
- **Subtitle** · `app/services/subtitle_auto_workflow_service.py:513-569` — checkpoint 与 DB 恢复证据只检查 completed/verified、路径 exists 和 is_file，不检查非空、size/fingerprint 或可被 FFprobe 验证；截断或零字节最终文件仍可能被恢复为成功。
- **Subtitle** · `app/services/subtitle_data_service.py:1222-1230` — 字幕数据层继续集中承担 revision/cue 持久化、导入导出、ASS、波形和 FFprobe，单文件约 1434 行，变更影响面和重复事务路径较大。
- **Pipeline & Job Queue** · `app/services/pipeline_engine.py:1600` — _write_json_atomic 只在写临时文件前后调用 require_active_job_lease，最终 temporary_path.replace(path) 以及 _create_schedule 的直接 write_text（1614-1616）不带 owner/lease 条件；租约在最后检查后被接管时，旧 Worker 仍可能覆盖新 Worker 的 metadata/schedule 派生文件。数据库 Job 状态虽已 fencing，但文件产物仍存在跨进程陈旧写入窗口。
- **Pipeline & Job Queue** · `app/services/job_worker.py:234` — WorkflowJobRunner.stop 只等待 worker 线程最多 25 秒；若 terminate_process_tree 持续失败，_run_job_subprocess 会保留 lease 并继续重试，但 stop 返回后应用生命周期仍会继续关闭，线程/子进程可能未结束、lease 也未立即释放，下一进程只能等待租约过期恢复。
- **Pipeline & Job Queue** · `app/services/job_worker.py:272` — 父 Worker 启动子进程时将 stdout/stderr 都重定向到 DEVNULL（272-276），job_worker_process 仅把异常文本打印到 stderr；子进程实际堆栈、Provider/FFmpeg 错误完全丢失，父进程最终只能记录退出码，重启恢复和人工诊断证据不足。
- **Pipeline & Job Queue** · `app/services/job_service.py:163` — _row_to_dict 对 payload_json/result_json/checkpoint_json 的 JSONDecodeError 仅 pass，字段保留为原始字符串；job_worker 随后按 dict 调用 payload.get 等操作时会产生不清晰的 AttributeError/失败，损坏队列数据没有明确的不可恢复数据错误状态。
- **Pipeline & Job Queue** · `app/services/job_worker.py:70` — JOB_TYPE_PUBLISH 仍在 job_service 中声明并可由 create_job 创建，但 execute_job 只有未支持分支（70-71）会失败；这是可入队、可查询却没有执行器的遗留/stub 入口，队列状态语义对调用方不完整。
- **Pipeline & Job Queue** · `app/services/pipeline_engine.py:88` — PipelineEngine 文件约 1,926 行，仍集中承担步骤编排、租约/取消检查、AutoPipelineCheckpoint 恢复、文件证据、AI/切片/字幕、文案、排期和发布任务创建；与 job_service（约1,171行）共同形成核心 God Component，修改任一步骤的回归半径很大。
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

### LOW (24)

- **Frontend UI** · `app/static/js/app.js:374-386; app/static/js/app.js:721-776` — AI 分析期间禁用的控件集合不包含动态生成的历史结果恢复按钮，也不包含显示/刷新历史按钮；用户仍可在新分析运行时恢复旧结果或刷新历史，与后台新 Job 的最终写回形成前端竞态。后端若拒绝该操作，页面仍会额外发出冲突请求。
- **API & Runtime** · `app/core/config.py:93` — 新分域 AI_ANALYSIS_REMOTE_* / AI_PUBLISH_REMOTE_* 配置仍通过 _env_first 回退到 AI_REMOTE_*，同时 Settings 继续保留完整旧字段组；兼容来源有文档说明但长期存在来源漂移和配置行为不一致风险。
- **API & Runtime** · `app/routers/tasks.py:1` — tasks.py 约 537 行、publish.py 约 514 行，包含大量重复的 try/except 与一行式 service 转发；路由、错误映射、同步/异步边界和兼容规则集中在大型 glue 文件中，修改入口时 blast radius 较大。
- **Media & Storage** · `app/services/storage_service.py:256` — allocate_task_dir_name(reserve=True) 先独占创建目录，再由调用方写入 tasks 记录；进程在两步之间退出会留下没有数据库记录的孤儿任务目录，当前没有启动扫描或清理机制。
- **Media & Storage** · `app/services/storage_service.py:503` — 上传直接写入最终 source 文件；上传或后续建档失败时的 unlink 异常被静默忽略，进程在上传中途退出也没有跨进程清理清单，可能留下未被任务引用的半成品媒体。
- **Media & Storage** · `app/services/storage_service.py:842` — 遗留 move_task_directory_to_trash 使用 reserve=False 的存在性检查后分配回收站目录，没有独占预占；并发删除同名任务可能选择相同目标目录并发生移动冲突。当前未发现运行时调用，但兼容入口仍可被恢复使用。
- **Media & Storage** · `app/services/video_cut_service.py:185` — cut_single_clip 在 FFmpeg 成功前先删除 plan.output_path；若重复执行同一 CutPlan 后发生超时、启动失败或输出切换失败，之前已经有效的成片会先被删除，临时文件原子替换无法保护旧结果。
- **AI Selection** · `app/services/ai_analysis_workflow_service.py:878-925` — 数据库没有历史 run 时会从 analysis.json 兼容合成 active run，当前只检查 JSON 对象和基础 meta 类型，不验证 checksum、完整 schema、selection_profile、候选字段或文件与数据库代际一致性。
- **AI Selection** · `app/services/ai/ai_clip_service.py:4-39` — generate_candidate_clips_placeholder 仍返回固定 fake output；当前未发现主流程调用，但误调用会产生伪造候选，属于 legacy/placeholder dead code。
- **Task Review & Cut** · `app/services/task_lifecycle_service.py:159-164; app/services/task_lifecycle_service.py:182-229` — create_task_record 在数据库 INSERT 前预创建任务目录；若随后数据库写入失败，服务层本身没有补偿删除空目录的路径，直接调用服务函数时仍可能留下孤儿目录。
- **Subtitle** · `app/services/subtitle_workflow_service.py:240-257` — 遗留 _activate_subtitle_job 仍可无 lease、revision、status 或 task/output_clip 约束直接激活指定记录；当前主渲染路径未调用，但 task_service 仍导入并保留兼容入口。
- **Pipeline & Job Queue** · `app/services/pipeline_engine.py:1804` — _read_json 在 OSError/JSONDecodeError 时静默返回 fallback；损坏的 metadata、schedule 或配置会被当作空结构继续处理，降低故障可见性和人工恢复依据。
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
- **P0/P1 运行时稳定线已经完成.** AI 候选/run/任务终态、持久 Job、单元计费账本、不完整结果门禁与本地安全边界已收口；当前可按本机单用户、人工复核范围判定为稳定 V1。
- **本地安全边界已从隐式信任变为显式 fail closed.** AI/Publish Secret DTO、旧 Provider 结果脱敏、loopback client 校验、Docker 回环绑定、Origin 白名单、作品 URL 和动态 DOM XSS 已收紧；不扩大为多用户权限系统。
- **复杂度集中而非全项目平均恶化.** publish_service.py、publish-center.js、app.js、publish_scheduler.py、subtitle_data_service.py 和 transcript_service.py 是主要 God Component；应按业务边界渐进拆分。
- **测试已与活动数据隔离，但 Coverage 与真实故障闭环仍不足.** Pytest 已强制使用进程级 sandbox，P0/P1 失败矩阵持续扩充；Coverage 尚未采集，Playwright 可选跳过和真实平台/进程故障闭环仍需在 P2 补齐。
- **遗留目录迁移脚本不得用于活动库.** scripts/migrate_task_dirs_to_project_names.py 仍是唯一 Codemap HIGH：它不属于当前运行链路，但缺少 WAL-aware 备份与文件补偿；在 P2 专项替换前必须保持停用。


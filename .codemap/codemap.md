<!--
  This file:        .codemap/codemap.md   (written report)
  Interactive map:  .codemap/codemap.html
-->

# NiuMa Studio — Functional Module Quality Audit

> **Interactive view:** [`.codemap/codemap.html`](codemap.html) — per-module scores, findings, LoC, and the dependency graph. This file is the written report.

**Generated:** 2026-08-30 · **Modules:** 14 · **Size:** 62146 tracked LoC across 139 files

## Health by layer

| Layer | Modules | Avg score |
|---|--:|--:|
| 界面 · API | 2 | 58 |
| 业务编排 | 4 | 65 |
| 媒体与 AI 处理 | 4 | 67 |
| 外部执行边界 | 2 | 76 |
| 持久化与运维 | 2 | 68 |

## Per-module lines of code & score

_LoC is the representative file/folder per module; folder-level modules overlap and are not additive._

### 界面 · API

| Module | LoC | Score | Tags |
|---|--:|:--|:--|
| Frontend UI | 15,416 | 54 D | god-component, bloat, silent-except |
| API & Runtime | 2,995 | 63 C | legacy, dual-format |

### 业务编排

| Module | LoC | Score | Tags |
|---|--:|:--|:--|
| Publish Center | 5,669 | 64 C | god-component, bloat, legacy, dual-format, fallback, silent-except, placeholder, duplication, glue |
| Pipeline & Job Queue | 4,109 | 63 C | fallback, silent-except, bloat, god-component |
| Task Review & Cut | 3,422 | 63 C | god-component, glue, duplication, bloat, fallback, silent-except, dual-format, legacy |
| Content Review & Feedback | 2,830 | 70 C | bloat, god-component, over-fit |

### 媒体与 AI 处理

| Module | LoC | Score | Tags |
|---|--:|:--|:--|
| AI Selection | 6,120 | 70 C | fallback, legacy, dual-format, placeholder, fake-output, bloat, god-component |
| Subtitle | 3,067 | 63 C | fallback, silent-except, legacy, bloat, god-component |
| Transcription | 2,276 | 63 C | fallback, silent-except, legacy, duplication, god-component |
| Media & Storage | 1,639 | 73 C | fallback, legacy, dual-format, silent-except |

### 外部执行边界

| Module | LoC | Score | Tags |
|---|--:|:--|:--|
| Publishers & Worker | 2,945 | 79 B | legacy, glue, silent-except, fallback |
| Publish Scheduler | 2,362 | 74 C | god-component, bloat, legacy, silent-except |

### 持久化与运维

| Module | LoC | Score | Tags |
|---|--:|:--|:--|
| SQLite Persistence | 4,927 | 68 C | legacy, dual-format, fallback, glue |
| Ops & Delivery | 4,369 | 68 C | fallback, legacy, dual-format, duplication, bloat, glue, silent-except |

## Worst offenders

- **Frontend UI (54/D)** — app/templates/clip_review.html:154-155; app/static/js/app.js:109-145: 空态“运行 Codex CLI 分析”走通用 handleProcessAction；/process/ai 返回持久化 job_id 后没有等待或轮询分支，直接 window.location.reload()。按钮随后恢复可用，用户无法在该页确认后台 Job 状态，运行期间可重复点击。
- **API & Runtime (63/C)** — app/routers/tasks.py:67-102,212-215,391-398; app/routers/publish.py:68-108,202-208,251-305,316-446: 多个 async handler 仍直接执行同步重任务或外部 I/O：上传后的 create_task_record 会同步执行媒体预检/FFprobe/FFmpeg，process_audio/process_video_cuts 直接运行 FFmpeg，发布账号与排期接口直接调用 PublishWorkerClient._request(urlopen，默认超时 1800 秒)，刷新队列、封面和 AI 文案也直接执行同步服务，可能阻塞 FastAPI event loop。内容复盘导出已在排除模块中使用 run_in_threadpool 并有专项线程隔离测试，未计入本模块。最小修复应将纯同步 handler 改为 def 或完整包裹 run_in_threadpool，并补充并发健康检查响应测试。
- **Transcription (63/C)** — app/services/transcript_service.py:1131-1158,1191-1228: faster-whisper 的 model.transcribe 调用、模型加载和 raw_segments 生成器迭代均没有绝对时限或内部取消检查；持久 Worker 只能依赖最长约 1800 秒的无进度 watchdog，旧版直连/后台任务路径没有外层 job 截止时间，模型卡死时可能长期占用进程。
- **Task Review & Cut (63/C)** — app/services/video_cut_workflow_service.py:38-107,516-603: _create_cut_run 只校验当前租约和任务未取消，不检查同一任务已有 queued/running 的切片工作；多个入口可同时创建 cut_run 并执行 cut_clips，导致重复 FFmpeg、状态互相覆盖和无效资源消耗。
- **Subtitle (63/C)** — app/services/subtitle_auto_workflow_service.py:42-50,51-108: 自动字幕流程在验证 workflow job lease 之前就创建并提交字幕工作、轨道等副作用；旧 Worker 若失去 lease，仍可能先写入新对象，之后才被 fencing 拦住，形成重复或孤立字幕状态。
- **Pipeline & Job Queue (63/C)** — app/services/pipeline_engine.py:1475-1479,1606-1622,1688-1708,1732-1734,1777-1779: SQLite 侧 Job/Task 写回虽有 lease，但选片、排期、clip_metadata、summary 仍直接 write_text，_update_selected_clips 也直接提交；固定同名 .tmp 及 lease 检查与文件 replace 不在同一原子边界，旧 Worker 或并发重试可能覆盖新代次产物。
- **Publish Center (64/C)** — app/services/publish_service.py:1-4793: 单文件同时承载发布配置、账号/OAuth、内容与封面生成、队列同步、历史查询、元数据升级、浏览器自动化和 API 执行，职责高度集中，修改与回归影响面过大。
- **SQLite Persistence (68/C)** — app/db/database.py:896-918: init_db 先执行多组未纳入 schema_migrations 的历史 ALTER/executescript 迁移，之后才进入账本事务；中途失败会留下已落盘的部分结构或数据变更，并发启动也缺少统一迁移互斥。
- **Ops & Delivery (68/C)** — scripts/start.ps1:22-33,95-100; docker-compose.dev.yml:1-6; docker-compose.yml:17-41: Development/Demo 在选择 Compose overlay 前先执行 doctor；开发 overlay 仍继承正式 data、任务目录和 PUBLISH_SCHEDULER_ENABLED=true，旧存储路径可能阻断隔离 Demo，开发调度器也可能触碰正式任务。
- **AI Selection (70/C)** — app/services/ai_analysis_workflow_service.py:1023-1037,1330-1369: 恢复路径仅从 active ai_analysis_runs 读取 payload 并校验 workflow_job_id，未核对 clip_candidates 的 source_analysis_run_id、候选 ID/数量、转写或源文件代际；随后可直接覆盖 analysis.json。候选表、run payload 与派生文件发生漂移时仍会展示或恢复旧 run。

## All findings

### MED (58)

- **Frontend UI** · `app/templates/clip_review.html:154-155; app/static/js/app.js:109-145` — 空态“运行 Codex CLI 分析”走通用 handleProcessAction；/process/ai 返回持久化 job_id 后没有等待或轮询分支，直接 window.location.reload()。按钮随后恢复可用，用户无法在该页确认后台 Job 状态，运行期间可重复点击。
- **Frontend UI** · `app/static/js/app.js:449-500; app/templates/clip_review.html:195-200` — renderAiAnalysisSummary 只按 clips/clip_summaries 渲染并给出“去检查并生成切片”，没有读取 analysis_incomplete、quality_degraded、coverage_percent、failed_units；审核页生成/同步按钮也仅按 clips 是否存在启用。不完整或降级结果直到后端门禁拒绝才暴露。
- **Frontend UI** · `app/static/js/app.js:1811-1827,1951-1967,720-727` — waitForAiAnalysisJob 使用无限 while(true)，没有超时、AbortController 或读取重试；pollAiAnalysisStatus 仅成功响应才安排下一次定时器，初始调用和定时回调均用 catch(() => {}) 静默吞错。一次网络或 JSON 异常即可停止状态刷新；前台等待失败时服务端 Job 仍可能运行。
- **Frontend UI** · `app/static/js/publish-center.js:1332-1342,1643-1679` — 发送中心保存依次 PATCH target、PATCH send-content、PUT experiment-assignment，没有事务、补偿或失败后回读；最后一步失败时前两步已经持久化，但界面统一显示保存失败并保留旧 experimentId，形成内容已变更而实验归因不确定。
- **Frontend UI** · `app/static/js/publish-center.js:1142-1180,1423-1429,2246` — refreshJobs 每 5 秒把服务端快照直接写入编辑器的 account_id、publish_mode、cover_file_path、cover_time_seconds，没有 focus、dirty 或版本保护。用户编辑尚未保存时轮询到旧快照会覆盖账号、发布方式和封面选择，丢失本地输入。
- **API & Runtime** · `app/routers/tasks.py:67-102,212-215,391-398; app/routers/publish.py:68-108,202-208,251-305,316-446` — 多个 async handler 仍直接执行同步重任务或外部 I/O：上传后的 create_task_record 会同步执行媒体预检/FFprobe/FFmpeg，process_audio/process_video_cuts 直接运行 FFmpeg，发布账号与排期接口直接调用 PublishWorkerClient._request(urlopen，默认超时 1800 秒)，刷新队列、封面和 AI 文案也直接执行同步服务，可能阻塞 FastAPI event loop。内容复盘导出已在排除模块中使用 run_in_threadpool 并有专项线程隔离测试，未计入本模块。最小修复应将纯同步 handler 改为 def 或完整包裹 run_in_threadpool，并补充并发健康检查响应测试。
- **API & Runtime** · `app/routers/publish.py:55-70` — 抖音 OAuth URL/回调捕获裸 Exception，并将 str(exc) 直接写入 HTTP detail 或 redirect 查询参数；底层 provider、网络或数据库异常可能携带诊断信息并进入浏览器历史和日志。相关网络异常来自 app/services/publish_providers.py:216-226。应按类型映射安全错误码，原始异常仅服务端记录，并增加错误哨兵不回显测试。
- **API & Runtime** · `app/models/task.py:243-300,353-410` — PublishBatchScheduleUpdate、PublishScheduleNextStartRequest、PublishBatchJobCreate、PublishBatchTargetUpdate、ClipCandidateBatchUpdate 的批量列表缺少长度上限，现有校验仅做非空/去重；下游 app/services/publish_scheduler.py:1022-1118,1205-1226 和 publish_service.py:2810-2831,3410-3425 会展开 SQL IN 参数并逐项处理，超大请求可能触发 SQLite 变量限制、内存/耗时压力或未转换的 500。应在模型边界设上限并补充超限 API 测试。
- **API & Runtime** · `app/models/task.py:38-72; app/routers/tasks.py:162-170` — TaskStatus 同时暴露 uppercase 全自动状态和 lowercase 手工/遗留状态，路由接受两套值，服务层分别维护状态标签、进度、转换和生命周期集合；大小写协议混用时会出现精确匹配冲突、错误进度或无法转换。应在 API 边界规范化单一状态协议，并补充跨模式状态转换矩阵测试。
- **Media & Storage** · `app/services/storage_service.py:693` — 删除 manifest 只在当前进程内写入并由内存中的 StagedTaskMediaCleanup 继续处理；没有启动扫描、manifest 加载或跨进程恢复入口。进程在目录已移入暂存区、数据库提交前崩溃会留下任务原目录缺失；提交后、finalize 前崩溃则暂存区长期遗留，重复删除也不会扫描清理。
- **Media & Storage** · `app/services/storage_service.py:362` — resolve_video_file_path 对已存在路径直接返回，未验证是否位于允许根目录；validate_source_video_path 虽有根校验，但发布、字幕和查询等调用方直接复用该解析器后仅检查 exists/is_file。异常数据库 output_file_path 因此仍可能被本地发布流程读取或上传为任意现有文件。
- **Transcription** · `app/services/transcript_service.py:1131-1158,1191-1228` — faster-whisper 的 model.transcribe 调用、模型加载和 raw_segments 生成器迭代均没有绝对时限或内部取消检查；持久 Worker 只能依赖最长约 1800 秒的无进度 watchdog，旧版直连/后台任务路径没有外层 job 截止时间，模型卡死时可能长期占用进程。
- **Transcription** · `app/services/transcript_service.py:113-191` — run_ffmpeg_audio_extract 只有无进度 watchdog，没有 started_at 或绝对 wall-clock deadline；循环收到任意 FFmpeg 输出就刷新 last_progress_at，即使 out_time_ms 不再推进也可能持续运行。旧版直连音频处理不带 job_id，无法依赖外层任务 watchdog。
- **Transcription** · `app/services/transcript_service.py:1034-1042; app/services/transcript_workflow_service.py:122-148` — 进度文件损坏、不可读、JSON 顶层非 dict 时统一返回 {}；状态层将其当作‘没有进度’且 is_stale=False，既不暴露损坏证据，也不会触发恢复/人工复核，重启后可能误判为可正常重跑或静默丢失运行状态。
- **Transcription** · `app/services/transcript_service.py:67-77,1191-1228,1291-1367; app/services/transcript_workflow_service.py:221-260,346-430` — 模型缓存、当前 provider/model/device/compute 元数据和运行集合均为模块级可变状态，且运行集合没有锁。旧版 BackgroundTasks 可并发处理不同任务，任务 A 写进度/Markdown 时可能读到任务 B 的 provider 或模型标签；并发模型加载也无锁，设置变化时 _WHISPER_MODEL_KEY 可能与实际加载模型不同。
- **Transcription** · `app/services/transcription_checkpoint_service.py:40-50,68-75; app/services/transcript_workflow_service.py:321-328` — 大于 1 MiB 的音频指纹只哈希文件大小、前 1 MiB 和后 1 MiB，中间内容替换后仍可命中旧 checkpoint；同时工作流仅凭现有 transcript 能读出一行就跳过重转，不校验源文件指纹、provider 或模型，可能把旧分片结果用于新音频。
- **AI Selection** · `app/services/ai_analysis_workflow_service.py:1023-1037,1330-1369` — 恢复路径仅从 active ai_analysis_runs 读取 payload 并校验 workflow_job_id，未核对 clip_candidates 的 source_analysis_run_id、候选 ID/数量、转写或源文件代际；随后可直接覆盖 analysis.json。候选表、run payload 与派生文件发生漂移时仍会展示或恢复旧 run。
- **AI Selection** · `app/services/ai/ai_clip_analyzer.py:485-562` — _normalize_ai_clip_item 为缺失的标题、摘要、推荐理由、剪辑建议、spread、confidence 和封面位置填入默认值；仅有时间范围的稀疏 Provider 响应可通过 Pydantic。通用分析随后固定写入 invalid_item_count=0，并只按失败单元设置 analysis_incomplete，语义不完整的候选会被当作正常结果。
- **AI Selection** · `app/services/ai_analysis_workflow_service.py:1` — ai_analysis_workflow_service.py 当前约 1406 行，同时承担 Provider 编排、任务状态与租约、候选 SQL、Prompt/运行历史、恢复、analysis.json 物化及 API 结果组装，职责和测试边界过宽，局部修改的回归影响面较大。
- **Task Review & Cut** · `app/services/video_cut_workflow_service.py:38-107,516-603` — _create_cut_run 只校验当前租约和任务未取消，不检查同一任务已有 queued/running 的切片工作；多个入口可同时创建 cut_run 并执行 cut_clips，导致重复 FFmpeg、状态互相覆盖和无效资源消耗。
- **Task Review & Cut** · `app/services/task_lifecycle_service.py:373-406` — 完成状态门禁只查询 active、completed 的 output_clip 数据库行，不验证 output_file_path 仍存在且为文件，也不核对记录是否来自当前 active cut_run；残留索引或被删除的成片仍可能让任务进入完成状态。
- **Task Review & Cut** · `app/services/task_service.py:1198-1232; app/services/video_cut_workflow_service.py:274-323` — _active_outputs_match_enabled_candidates 仅比较候选 ID、数量和输出路径存在性；切片落库虽保存 source_fingerprint，但复用判断不读取它，也没有内容指纹、大小或可播放性校验。源视频被替换或输出被截断时，审核同步可能错误跳过重新切片。
- **Task Review & Cut** · `app/services/task_service.py:898-968` — list_clip_candidates 对损坏的 quality_evidence_json 静默回退为空字典，但 start_time/end_time 在 try 块外直接解析；单条历史候选时间格式损坏会抛 ValueError，使审核列表接口整体不可用，且数据损坏被隐藏。
- **Task Review & Cut** · `app/services/task_query_service.py:293-334,469-486` — 片段总览的 can_cut 只依据启用候选和源文件存在性，不考虑 analysis_incomplete、quality_degraded 或运行状态；系统状态页又精确匹配 lowercase 状态，遗漏全自动的 CREATED、FAILED_*、PENDING_SUBTITLE_REVIEW、COMPLETED，页面统计和操作按钮会失真。
- **Task Review & Cut** · `app/services/task_service.py:508-593,1388-1468,1517-1570; app/services/task_query_service.py:165-251` — task_service.py 约 1570 行，继续集中任务状态、AI、转写、切片、字幕、发布同步和查询，并保留多组转发 task_query_service 的包装器；_row_to_task 对每个任务单独查询 output_clip，Dashboard/字幕总览又重复批量查询和映射输出，存在 N+1 查询、重复逻辑和较大的维护回归半径。
- **Content Review & Feedback** · `app/services/content_review_service.py:1` — content_review_service.py 当前约 2387 行，同时承担 CSV/XLSX 解析、官方导出规范化、指标聚合、作品匹配、诊断建议、实验生命周期及多类 SQLite 写入，形成高耦合 God Component，局部修改的维护和回归影响面过大。
- **Subtitle** · `app/services/subtitle_auto_workflow_service.py:42-50,51-108` — 自动字幕流程在验证 workflow job lease 之前就创建并提交字幕工作、轨道等副作用；旧 Worker 若失去 lease，仍可能先写入新对象，之后才被 fencing 拦住，形成重复或孤立字幕状态。
- **Subtitle** · `app/services/subtitle_data_service.py:1017-1074` — 读取 ASR chunk 时遇到坏 JSON 会静默跳过，剩余分片仍可组装为合法字幕；部分转写数据可能被当成完整结果继续审核或烧录，缺少 fail-closed 完整性证据。
- **Subtitle** · `app/services/subtitle_data_service.py:162-190,1222-1230,236-330` — 源字幕 revision 先提交，随后每个片段同步各自独立事务；中途失败会留下部分片段已更新、部分仍是旧版本的非原子状态，重试和人工判断成本高。
- **Subtitle** · `app/services/subtitle_auto_workflow_service.py:513-569` — 恢复已有字幕产物只验证路径存在且是文件，没有校验非空、大小、哈希或媒体可读性；截断或损坏文件可能被误当成可恢复成功。
- **Subtitle** · `app/services/subtitle_data_service.py:1-1443` — SubtitleDataService 同时承担 revision、轨道、片段同步、ASR chunk、波形和文件落盘，且波形路径先把整段 PCM 载入内存再下采样；长视频会放大内存峰值，中心化职责也增加回归面。
- **Pipeline & Job Queue** · `app/services/pipeline_engine.py:1475-1479,1606-1622,1688-1708,1732-1734,1777-1779` — SQLite 侧 Job/Task 写回虽有 lease，但选片、排期、clip_metadata、summary 仍直接 write_text，_update_selected_clips 也直接提交；固定同名 .tmp 及 lease 检查与文件 replace 不在同一原子边界，旧 Worker 或并发重试可能覆盖新代次产物。
- **Pipeline & Job Queue** · `app/services/job_worker.py:260-267` — WorkflowJobRunner._run 调用 claim_next_job 位于 try/except 外；瞬态 SQLite 异常会直接终止 daemon Worker 线程，没有 supervisor、重启或失败信号。
- **Pipeline & Job Queue** · `app/services/job_worker.py:255-258,314-335,475-495` — stop() 只设置停止事件并 join 25 秒；子进程树终止失败时循环可能继续，join 超时后线程和子进程仍可存活，不能保证干净停机。
- **Pipeline & Job Queue** · `app/services/job_worker.py:293-298; app/services/job_worker_process.py:17-27` — 父 Worker 以 stdout/stderr=DEVNULL 启动子进程，子进程异常只打印到被丢弃的 stderr；父进程最终只能记录泛化退出码，显著降低故障定位能力。
- **Pipeline & Job Queue** · `app/services/job_service.py:163-177; app/services/pipeline_engine.py:1313-1322,1810-1816` — payload/result/checkpoint 坏 JSON 被静默保留或降级为默认值；坏 payload 可能以字符串进入后续逻辑，损坏 metadata 可能触发重新生成并隐藏重复成本。
- **Pipeline & Job Queue** · `app/services/pipeline_engine.py:88-1817` — PipelineEngine 单文件约 1932 行并承担九步编排、恢复、指纹、数据库与各业务步骤，后续局部修改存在真实跨步骤回归风险。
- **Publish Center** · `app/services/publish_service.py:1-4793` — 单文件同时承载发布配置、账号/OAuth、内容与封面生成、队列同步、历史查询、元数据升级、浏览器自动化和 API 执行，职责高度集中，修改与回归影响面过大。
- **Publish Center** · `app/services/publish_service.py:2173-2183; 2707-2731` — 封面生成异常被写入 cover_error 后仍创建 WAITING 任务；同步路径未把该异常计入 errors，可能返回 ok，错误延迟到后续 readiness 阶段才暴露。
- **Publish Center** · `app/services/publish_service.py:2810-2831` — create_batch_publish_jobs 对不存在的 output_clip_id 直接 continue，最终仍返回 status=ok，调用方无法区分全部成功、部分成功和输入错误。
- **Publish Center** · `app/services/publish_providers.py:117-142; app/services/publish_service.py:2681-2807` — Bilibili 已被配置校验、api_publish 模式和队列流程接入，但 provider.publish() 在校验通过后必然抛出 bilibili_provider_pending，用户可创建一个确定会失败的发布任务。
- **Publish Center** · `app/services/publish_providers.py:70-114; app/services/publish_service.py:4576-4618` — 抖音发布由非幂等的 upload_url 与 create_url 两次请求组成，上传成功后第二次请求超时会被整体记为失败；重试可能再次上传并产生重复投稿，代码没有幂等键、远端回执持久化或未知结果隔离。
- **Publish Center** · `app/services/publish_service.py:2948-2979` — 发布历史 SQL 直接按大写 PUBLISH_HISTORY_STATUSES 过滤，查询阶段不调用 _normalize_publish_status；若长期运行进程或外部导入仍留下 lower/旧 status，历史与日历会漏项。启动迁移通常会规范化，但查询本身仍不具备兼容保护。
- **Publish Scheduler** · `app/services/publish_scheduler.py:283-304` — list_due_jobs 每轮 SELECT 全部 SCHEDULED，再逐条在 Python 解析和过滤时间，没有 SQL 到期条件、分页或批量上限；排期规模增长会提高扫描内存和调度延迟。
- **Publish Scheduler** · `app/services/publish_scheduler.py:756-870` — recover_interrupted_jobs 每轮加载全部 PUBLISHING，并逐条同步查询 Worker execution；没有批量上限或退避，Worker 超时/不可用时恢复耗时会线性增长并阻塞后续排期。
- **Publish Scheduler** · `app/services/publish_scheduler.py:133-1924` — PublishScheduler 约 1800 行，集中排期计算、任务领取、结果写回、Worker 恢复、重试修复、人工复核、批量排期及旧版兼容；状态机修改的验证范围仍过大。
- **Publishers & Worker** · `scripts/publish_host_worker.py:454-483` — 跨 execution 扫描对损坏或缺失 identity 的旧 journal 直接跳过；若旧执行已产生上传副作用但身份未落盘，新 execution 无法关联并可能再次投稿。
- **Publishers & Worker** · `scripts/publish_host_worker.py:717-719` — 官方作品报表同步的通用异常处理将 str(exc) 直接拼入 HTTP detail；底层 Playwright、文件路径或页面诊断可能暴露给调用方，错误边界未统一脱敏。
- **SQLite Persistence** · `app/db/database.py:896-918` — init_db 先执行多组未纳入 schema_migrations 的历史 ALTER/executescript 迁移，之后才进入账本事务；中途失败会留下已落盘的部分结构或数据变更，并发启动也缺少统一迁移互斥。
- **SQLite Persistence** · `scripts/backup_restore.py:56-96,598-665` — 恢复仅一次性探测健康端点，失败会视为无活动服务；BEGIN EXCLUSIVE 也是瞬时检查，整个检查到替换过程没有应用/调度器/恢复任务共享互斥，存在 TOCTOU 写入窗口。
- **SQLite Persistence** · `app/services/database_backup_service.py:112-129; scripts/backup_restore.py:418-426` — 备份验证强制要求 schema_migrations 表和账本内容；迁移账本引入前创建的历史备份没有兼容升级路径，会被直接拒绝验证或恢复。
- **SQLite Persistence** · `app/db/database.py:2084-2104` — _migrate_tasks_table 每次初始化都把不在硬编码状态白名单内的值改成 pending_video，没有版本或账本保护；未知、未来或旧历史状态可能被静默重置。
- **Ops & Delivery** · `scripts/start.ps1:22-33,95-100; docker-compose.dev.yml:1-6; docker-compose.yml:17-41` — Development/Demo 在选择 Compose overlay 前先执行 doctor；开发 overlay 仍继承正式 data、任务目录和 PUBLISH_SCHEDULER_ENABLED=true，旧存储路径可能阻断隔离 Demo，开发调度器也可能触碰正式任务。
- **Ops & Delivery** · `scripts/start_publish_worker.ps1:108-140,145-175; scripts/start.ps1:70-79,108-140; scripts/start_native.ps1:106-117,170-235` — 启动流程先创建 Worker，再启动 Docker/native Web；后续失败时没有 finally 终止本次创建的 Worker，8765 进程可能驻留并影响后续启动，现有测试仅做静态断言。
- **Ops & Delivery** · `scripts/watch_docker_publish_worker.ps1:164-192,216-224,235-238` — Worker 连接修复失败时会持续等待并 force-recreate workflow，长期 Token 或网络故障可形成约 80 秒一轮的无限容器重建循环。
- **Ops & Delivery** · `scripts/start_docker_opencli.ps1:24-29; scripts/start.ps1:196-204,237-252` — 兼容启动器 health 异常只打印仍在初始化并正常返回；主启动器对 Worker 未连接也仅警告，最终仍返回成功，可能让用户误判发布链路已就绪。
- **Ops & Delivery** · `scripts/restore.ps1:84-92; scripts/backup_restore.py:56-75,598-650` — 恢复前仅做一次健康检查和瞬时 BEGIN EXCLUSIVE，检查、回滚备份、媒体暂存与数据库替换之间没有持续进程互斥，服务可能在窗口内重新写入。
- **Ops & Delivery** · `scripts/backup.ps1:25-43,67-70; scripts/backup_restore.py:267-312` — 普通备份默认包含 .env，只有显式 -ExcludeEnv 才排除；ZIP 内 API Key/Token 明文仍有复制或共享扩散风险。

### LOW (16)

- **Frontend UI** · `app/static/js/app.js:1-2461; app/static/js/publish-center.js:1-2247; app/static/css/styles.css:1-6494` — 前端仍把跨页面请求、多个 Job 轮询、审核/裁切/字幕/配置事件与渲染状态集中在两个约 2K 行脚本和一个约 6.5K 行全局样式中，职责与状态共享使轮询或页面状态改动的回归半径持续偏大。
- **Media & Storage** · `app/services/storage_service.py:490` — save_uploaded_video 从 503 行直接以 wb 写入最终 source 文件，没有 .part 文件和原子替换；请求异常时 unlink 失败被静默忽略，进程崩溃/断电无法执行清理，可能留下无数据库引用的半成品上传并占用最多 4GB 磁盘。
- **Media & Storage** · `app/services/storage_service.py:256` — allocate_task_dir_name(reserve=True) 在数据库 INSERT 前用 mkdir 预占目录；调用方若在预占与建档之间崩溃，目录没有任务记录且当前没有孤儿目录扫描或恢复机制。正常 HTTP 异常可清理，但无法覆盖进程级中断。
- **Media & Storage** · `app/services/video_cut_service.py:185` — cut_single_clip 在 FFmpeg 启动前先 unlink plan.output_path；build_output_path 的正常工作流通常生成新路径，但同一 CutPlan/输出路径重试时，失败或超时会先删除已有有效成片，临时文件原子替换无法保护旧结果。
- **Transcription** · `app/services/transcript_service.py:347-549` — 本地与 Volcengine 转写分别复制了完整的分片遍历、checkpoint、进度、偏移和清理生命周期；两条路径已出现不同的请求不确定态处理，后续修复取消、租约或计数逻辑需同步维护，存在行为漂移风险。该文件同时承担 FFmpeg、模型、远程 HTTP、checkpoint、进度和 Markdown，变更影响面较大。
- **AI Selection** · `app/services/ai_analysis_workflow_service.py:878-925` — 数据库没有历史 run 时，_ensure_ai_analysis_history_from_current_file 会从 analysis.json 兼容合成 active run；当前仅检查 JSON 对象和基础 meta 类型，不验证 checksum、完整 schema、selection_profile、候选字段或文件与数据库代际一致性。
- **AI Selection** · `app/services/ai/ai_clip_service.py:4-39` — generate_candidate_clips_placeholder 仍返回三条固定 ClipCandidate，完全不读取转写、Provider 或任务输入；当前未发现主流程调用，但误调用会产生伪造候选，属于遗留 placeholder/fake output。
- **Content Review & Feedback** · `app/services/content_review_service.py:2092-2100;app/routers/content_review.py:236-239` — delete_item_match 的 UPDATE 仅按 snapshot_id 执行，没有 account_id、平台或当前归属约束；多账号场景下，旧页面或持有已知快照 ID 的请求可解除另一账号的作品关联。
- **Content Review & Feedback** · `app/services/content_review_service.py:388-390,421-424,483-486` — 官方作品缺平台 ID 时，内部键只由规范化标题和发布时间生成；两条同标题同秒、或仅标点不同的合法作品会命中 seen_ids 并静默 continue，导入行数和快照数据减少且没有人工确认提示。
- **Content Review & Feedback** · `app/models/content_review.py:6-7;app/routers/content_review.py:64-70` — ContentMetricImportCommitRequest.confirm 默认 True，提交接口只拒绝显式 false；空 JSON 即可直接确认并写入预览批次，弱化了要求用户明确确认的导入门禁。
- **Subtitle** · `app/services/subtitle_auto_workflow_service.py:140-171` — 旧式字幕 Job 激活逻辑依赖较弱的状态约束，异常恢复或并发执行时缺少与当前 workflow generation 等价的完整校验。
- **Publish Scheduler** · `app/services/publish_scheduler.py:276-281` — shutdown 等待后台 Task 完整结束但没有超时或取消兜底；run_once 若被 Worker 超时、数据库锁或文件操作卡住，应用停机可能长时间等待。
- **Publish Scheduler** · `app/services/publish_scheduler.py:1830-1850` — queue_snapshot 对非法 scheduled_at 静默忽略，损坏排期会从今日视图消失且没有告警或异常计数。
- **Publishers & Worker** · `app/services/publishers/browser_runtime.py:315-322` — 截图失败捕获所有 Exception 后仅返回空字符串且不记录阶段或原因；失败诊断会携带空 screenshot，无法区分取证失败。
- **Publishers & Worker** · `app/services/publishers/page_scripts.py:14-150` — 页面脚本入口通过延迟导入 publish_service，并由多个包装函数调用其私有 DOM 脚本；活动 Worker 依赖约 4.8k 行旧服务的私有符号，形成 legacy glue 耦合。
- **SQLite Persistence** · `app/db/database.py:807-893` — 指标快照、实验及实验项只有单列外键，没有复合账号归属约束；数据库层面仍可接受跨账号批次、作品或实验混绑记录。

## Cross-cutting themes

- **本地单体架构仍与当前产品边界匹配.** FastAPI、SQLite WAL、文件产物、持久化 Job 与 Windows Worker 适合本机单用户规模；本轮没有证据支持微服务、全量 ORM 或前端框架迁移。
- **整改前的 A-F 六项问题已经关闭.** 长直播质量元数据、迁移原子性与 Prompt 外键、反馈来源 Run、作品时长口径、官方导出异步边界和堆叠 PR CI 均有直接回归证据。
- **没有未关闭的 P0/P1 回归.** 独立复审曾发现预览按钮状态回归，已在最终 revision 修复并由宽屏、窄屏浏览器用例覆盖；当前活动库 quick_check 正常、外键违规为 0。
- **剩余风险集中在运行韧性而非正常路径.** 历史兼容迁移、备份恢复持续互斥、文件 checkpoint 与 SQLite lease、Worker 自愈和错误取证仍有条件触发的中等风险。
- **发布与前端仍保留条件性边界.** 部分保存、自动刷新覆盖脏表单、未完成的 B站 API Provider 和旧 journal 身份缺失，仅在启用对应模式或出现真实故障时值得单独处理。
- **复杂度集中但不值得全面重构.** publish_service.py、content_review_service.py、database.py、app.js 与 PipelineEngine 是主要热点；只应在触碰对应业务时按事务和边界渐进拆分。
- **测试与 CI 已形成收口证据.** 全量 867 项测试和 PR #67/#68/#69 的 Linux、Windows、Docker 检查均通过；Sonar 仍无 coverage.xml，质量门禁失败应作为后续质量工程而非继续改业务代码的理由。

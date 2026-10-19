<!--
  This file:        .codemap/codemap.md   (written report)
  Interactive map:  .codemap/codemap.html
-->

# NiuMa Studio — Functional Module Quality Audit

> **Interactive view:** [`.codemap/codemap.html`](codemap.html) — per-module scores, findings, LoC, and the dependency graph. This file is the written report.

**Generated:** 2026-08-30 · **Modules:** 14 · **Size:** 61805 tracked LoC across 139 files

## Health by layer

| Layer | Modules | Avg score |
|---|--:|--:|
| 界面 · API | 2 | 58 |
| 业务编排 | 4 | 63 |
| 媒体与 AI 处理 | 4 | 64 |
| 外部执行边界 | 2 | 76 |
| 持久化与运维 | 2 | 70 |

## Per-module lines of code & score

_LoC is the representative file/folder per module; folder-level modules overlap and are not additive._

### 界面 · API

| Module | LoC | Score | Tags |
|---|--:|:--|:--|
| Frontend UI | 15,378 | 57 D | bloat, glue, fallback, silent-except, legacy, over-fit |
| API & Runtime | 2,995 | 58 D | legacy, dual-format |

### 业务编排

| Module | LoC | Score | Tags |
|---|--:|:--|:--|
| Publish Center | 5,669 | 64 C | god-component, bloat, legacy, dual-format, fallback, silent-except, placeholder, duplication, glue |
| Pipeline & Job Queue | 4,109 | 65 C | fallback, silent-except, legacy, stub, bloat, god-component, glue |
| Task Review & Cut | 3,422 | 63 C | god-component, glue, duplication, bloat, fallback, silent-except, dual-format, legacy |
| Content Review & Feedback | 2,819 | 61 C | legacy, over-fit |

### 媒体与 AI 处理

| Module | LoC | Score | Tags |
|---|--:|:--|:--|
| AI Selection | 6,119 | 55 D | glue, fallback, bloat, god-component, legacy |
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
| SQLite Persistence | 4,635 | 68 C | legacy, dual-format, bloat, god-component |
| Ops & Delivery | 4,370 | 73 C | fallback, legacy, dual-format, duplication, bloat, glue, silent-except, monkeypatch |

## Worst offenders

- **AI Selection (55/D)** — app/services/ai/long_live_talk_analyzer.py:251: 长直播分析的 analysis_meta 包含窗口计数、coverage、analysis_incomplete 等字段，但没有 quality_degraded；返回结果 270-273 也没有补充该字段。工作流 1473-1489 原样合并该 meta，而 validate_ai_analysis_meta_for_cut 120-132 将缺失布尔值判定为完整性失败并强制 degraded，因此即使所有窗口成功，长直播结果也无法通过切片门禁。
- **Frontend UI (57/D)** — app/templates/clip_review.html:154-155; app/static/js/app.js:109-145: 片段审核空状态的“运行 Codex CLI 分析”仍绑定通用 handleProcessAction。/process/ai 返回持久化 job_id 后，处理器直接 reload，不等待或轮询 Job；页面可能仍无候选，用户可在后台执行期间再次发起分析，形成重复分析或计费边界。
- **API & Runtime (58/D)** — app/routers/tasks.py:212: 仍有 async handler 直接执行同步重任务：process_audio :212-214 调用 transcript_workflow_service.py:181-186 的 FFmpeg，process_video_cuts :391-394 调用 video_cut_workflow_service.py:591-596 的逐条切片；publish.py:202、:251、:269、:277、:302 也直接调用队列刷新、封面 FFmpeg 或 AI 文案。长请求会占满 FastAPI event loop，拖住健康检查、轮询和其他 API。
- **Content Review & Feedback (61/C)** — app/routers/content_review.py:59-75: export_sync_douyin_items 是 async handler，却直接调用同步 PublishWorkerClient.analytics_export_sync；底层 urlopen 默认超时 1800 秒，随后仍同步匹配和 SQLite 提交。Worker 卡住时可能阻塞 FastAPI event loop 最长约 30 分钟。
- **Transcription (63/C)** — app/services/transcript_service.py:1131-1158,1191-1228: faster-whisper 的 model.transcribe 调用、模型加载和 raw_segments 生成器迭代均没有绝对时限或内部取消检查；持久 Worker 只能依赖最长约 1800 秒的无进度 watchdog，旧版直连/后台任务路径没有外层 job 截止时间，模型卡死时可能长期占用进程。
- **Task Review & Cut (63/C)** — app/services/video_cut_workflow_service.py:38-105; app/services/video_cut_workflow_service.py:516-583: 切片前仍只排除 CANCELLED；_create_cut_run 未校验允许切片的任务状态，也未查询同一任务已有 queued/running Workflow Job。current_job_lease() 只是当前执行上下文，无法拦截其他请求/线程的活动 Job；同步入口和异步 Job 可同时创建切片批次，并且始终把自动任务写成 lowercase cutting，存在状态互相覆盖和重复 FFmpeg 执行风险。
- **Subtitle (63/C)** — app/services/subtitle_auto_workflow_service.py:42-50,51-108: 自动字幕流程在验证 workflow job lease 之前就创建并提交字幕工作、轨道等副作用；旧 Worker 若失去 lease，仍可能先写入新对象，之后才被 fencing 拦住，形成重复或孤立字幕状态。
- **Publish Center (64/C)** — app/services/publish_service.py:1-4789: publish_service.py 已接近 4800 行，继续集中配置、账号、OAuth、文案、封面、队列、历史、内容实验关联、OpenCLI 兼容和 API 发布职责；本轮又新增内容实验关联查询，使发布域改动的回归半径继续扩大。
- **Pipeline & Job Queue (65/C)** — app/services/pipeline_engine.py:1475-1479,1606-1623,1732-1734,1777-1779: Job lease 只在部分写入前检查，且检查与最终 replace 之间存在 TOCTOU；选片、切片元数据、任务摘要和 schedule 仍有直接 write_text。旧 Worker 失去 lease 后仍可能覆盖新代次的文件，造成数据库 lease 已被正确 fencing 但文件证据、checkpoint 与实际产物不一致。
- **SQLite Persistence (68/C)** — app/db/database.py:844-866,1690-2004,2525-2595: init_db 先执行多组历史列探测/兼容迁移，再进入 schema_migrations ledger；tasks、clip_candidates、ai_analysis_runs、字幕和转写 helper 仍使用 executescript 或逐列 ALTER，未由同一个 BEGIN IMMEDIATE 覆盖。两个启动进程可同时看到缺列并竞争 ALTER，失败或中途异常会留下部分结构/数据变更而没有对应账本记录。

## All findings

### HIGH (1)

- **AI Selection** · `app/services/ai/long_live_talk_analyzer.py:251` — 长直播分析的 analysis_meta 包含窗口计数、coverage、analysis_incomplete 等字段，但没有 quality_degraded；返回结果 270-273 也没有补充该字段。工作流 1473-1489 原样合并该 meta，而 validate_ai_analysis_meta_for_cut 120-132 将缺失布尔值判定为完整性失败并强制 degraded，因此即使所有窗口成功，长直播结果也无法通过切片门禁。

### MED (63)

- **Frontend UI** · `app/templates/clip_review.html:154-155; app/static/js/app.js:109-145` — 片段审核空状态的“运行 Codex CLI 分析”仍绑定通用 handleProcessAction。/process/ai 返回持久化 job_id 后，处理器直接 reload，不等待或轮询 Job；页面可能仍无候选，用户可在后台执行期间再次发起分析，形成重复分析或计费边界。
- **Frontend UI** · `app/static/js/app.js:449-500; app/templates/clip_review.html:195-200` — AI 结果渲染没有展示 analysis_incomplete、quality_degraded、coverage_percent、failed_units；审核页生成和同步按钮也只按 clips 是否为空启用。不完整或降级结果仍显示“去检查并生成切片”，质量门槛到后端拒绝时才暴露。
- **Frontend UI** · `app/static/js/app.js:1951-1967` — AI 状态轮询只有成功读取后才安排下一次定时器；fetch 或 JSON 异常被调用方 catch 静默吞掉且不再调度。一次临时网络异常即可让状态和日志永久停在旧值且无提示。
- **Frontend UI** · `app/static/js/app.js:1811-1827; app/static/js/app.js:720-727` — waitForAiAnalysisJob 无限轮询且没有总时限、取消或读取重试；单次临时读取错误会解锁按钮，但服务端 Job 可能仍在运行。页面重开后也不在 Job 完成时重读历史，可能显示已完成却保留旧候选。
- **Frontend UI** · `app/static/js/content-review.js:606-650; app/static/js/content-review.js:654-696` — 内容复盘预览请求捕获文件和 account_id 后异步等待；文件或账号变更只清空 previewBatchId，不取消请求或校验请求代际。旧响应仍会回写 batch_id，切换期间可能确认旧文件或旧账号的批次。
- **Frontend UI** · `app/static/js/publish-center.js:1332-1342; app/static/js/publish-center.js:1671-1679` — 发送中心保存实验归属先独立 PATCH target、PATCH send-content，再 PUT experiment-assignment。最后一步失败时前两步已成功，但界面把整体显示为失败且保留旧 experimentId，形成内容已变更、实验归因不确定的部分成功。
- **API & Runtime** · `app/routers/tasks.py:212` — 仍有 async handler 直接执行同步重任务：process_audio :212-214 调用 transcript_workflow_service.py:181-186 的 FFmpeg，process_video_cuts :391-394 调用 video_cut_workflow_service.py:591-596 的逐条切片；publish.py:202、:251、:269、:277、:302 也直接调用队列刷新、封面 FFmpeg 或 AI 文案。长请求会占满 FastAPI event loop，拖住健康检查、轮询和其他 API。
- **API & Runtime** · `app/routers/content_review.py:60` — 新增内容复盘路由仍在 async handler 内同步调用 PublishWorkerClient.analytics_export_sync；worker_client.py:64 使用阻塞 urlopen，config.py:192 默认超时 1800 秒。:33-34 的 Excel/CSV 解析与 :53 的提交也同步执行；一次导出或解析请求可能阻塞整个事件循环最长约 30 分钟。
- **API & Runtime** · `app/routers/publish.py:59` — OAuth URL :55-60 和回调 :63-70 捕获裸 Exception，并把 str(exc) 直接放入 HTTP detail 或 redirect 查询参数。底层配置、数据库或第三方异常可能含本地路径、请求诊断等信息，会暴露给浏览器、历史记录和日志；Secret DTO 脱敏并未覆盖此错误边界。
- **API & Runtime** · `app/models/task.py:243` — 多个批量输入仍没有长度上限：PublishBatchScheduleUpdate.job_ids/confirmed_schedule :243-252、NextStart.job_ids :262-275、BatchJob.output_clip_ids :286-300、BatchTarget.job_ids :353-361、ClipCandidateBatchUpdate.clips :408-410；校验只做非空/去重。对应路由把列表交给逐项 SQL/更新循环（publish_scheduler.py:1022-1118、task_service.py:1124-1186），超大请求可造成内存、SQLite 变量数量和同步处理时间压力。
- **API & Runtime** · `app/models/task.py:38` — TaskStatus :38-72 同时暴露 uppercase 自动流水线状态与 lowercase 手工/遗留状态；TaskStatusUpdate :122-124 接受两套值，tasks 路由 :162-170 直接转交，生命周期转换表和服务也分别维护两套词汇。该字段存在两个并行协议，过滤、状态迁移和客户端容易出现大小写/语义不一致，属于仍在运行的 dual-format 遗留。
- **Media & Storage** · `app/services/storage_service.py:693` — 删除 manifest 只在当前进程内写入并由内存中的 StagedTaskMediaCleanup 继续处理；没有启动扫描、manifest 加载或跨进程恢复入口。进程在目录已移入暂存区、数据库提交前崩溃会留下任务原目录缺失；提交后、finalize 前崩溃则暂存区长期遗留，重复删除也不会扫描清理。
- **Media & Storage** · `app/services/storage_service.py:362` — resolve_video_file_path 对已存在路径直接返回，未验证是否位于允许根目录；validate_source_video_path 虽有根校验，但发布、字幕和查询等调用方直接复用该解析器后仅检查 exists/is_file。异常数据库 output_file_path 因此仍可能被本地发布流程读取或上传为任意现有文件。
- **Transcription** · `app/services/transcript_service.py:1131-1158,1191-1228` — faster-whisper 的 model.transcribe 调用、模型加载和 raw_segments 生成器迭代均没有绝对时限或内部取消检查；持久 Worker 只能依赖最长约 1800 秒的无进度 watchdog，旧版直连/后台任务路径没有外层 job 截止时间，模型卡死时可能长期占用进程。
- **Transcription** · `app/services/transcript_service.py:113-191` — run_ffmpeg_audio_extract 只有无进度 watchdog，没有 started_at 或绝对 wall-clock deadline；循环收到任意 FFmpeg 输出就刷新 last_progress_at，即使 out_time_ms 不再推进也可能持续运行。旧版直连音频处理不带 job_id，无法依赖外层任务 watchdog。
- **Transcription** · `app/services/transcript_service.py:1034-1042; app/services/transcript_workflow_service.py:122-148` — 进度文件损坏、不可读、JSON 顶层非 dict 时统一返回 {}；状态层将其当作‘没有进度’且 is_stale=False，既不暴露损坏证据，也不会触发恢复/人工复核，重启后可能误判为可正常重跑或静默丢失运行状态。
- **Transcription** · `app/services/transcript_service.py:67-77,1191-1228,1291-1367; app/services/transcript_workflow_service.py:221-260,346-430` — 模型缓存、当前 provider/model/device/compute 元数据和运行集合均为模块级可变状态，且运行集合没有锁。旧版 BackgroundTasks 可并发处理不同任务，任务 A 写进度/Markdown 时可能读到任务 B 的 provider 或模型标签；并发模型加载也无锁，设置变化时 _WHISPER_MODEL_KEY 可能与实际加载模型不同。
- **Transcription** · `app/services/transcription_checkpoint_service.py:40-50,68-75; app/services/transcript_workflow_service.py:321-328` — 大于 1 MiB 的音频指纹只哈希文件大小、前 1 MiB 和后 1 MiB，中间内容替换后仍可命中旧 checkpoint；同时工作流仅凭现有 transcript 能读出一行就跳过重转，不校验源文件指纹、provider 或模型，可能把旧分片结果用于新音频。
- **AI Selection** · `app/services/ai_analysis_workflow_service.py:996` — _load_active_analysis_payload 只读取 active ai_analysis_runs 的 JSON；_resume_committed_ai_analysis 1330-1369 仅校验 workflow_job_id，不校验 clip_candidates.source_analysis_run_id、候选 ID/数量、转写或源文件代际。_materialize_analysis_payload 1023-1037 还会直接用 active payload 覆盖 analysis.json，发生恢复、旧数据或部分漂移时可能让运行记录、候选表和派生文件代表不同代际。
- **AI Selection** · `app/services/ai/ai_clip_analyzer.py:531` — _normalize_ai_clip_item 会为缺失的 title、summary、highlight_reason、suggested_editing、spread、confidence 等字段填入推导或固定默认值（confidence 默认 0.7，封面位置取中点）。仅有时间范围的稀疏 Provider 响应可因此通过 Pydantic；通用分析随后仍将 invalid_item_count 视为 0、analysis_incomplete 视为 false，语义不完整的候选会被当成正常结果。
- **AI Selection** · `app/services/ai/unit_checkpoint.py:312` — _write_checkpoint 对 workflow_jobs 的 UPDATE 仅使用 WHERE id=?，没有 lease_owner、lease_token、状态或过期条件。_begin_unit/_finish_unit 虽在调用前校验租约，但校验与提交之间租约可能过期或被接管，旧 worker 仍可能覆盖新的 checkpoint 账本，影响后续复用和重复计费判断。
- **AI Selection** · `app/services/clip_feedback_service.py:91` — save_clip_feedback 在事务外先取得候选，进入事务后却读取当前 active run，并在 107-121 写入该 run 的 analysis_run_id，而不是候选自身的 source_analysis_run_id；同文件 record_review_toggle_feedback_with_connection 已采用候选来源。恢复/新分析竞态或旧候选存在时，反馈会归因到错误的分析运行和 Prompt 版本。
- **AI Selection** · `app/services/ai_analysis_workflow_service.py:1` — 文件已达 1539 行，同时承担 Provider 编排、任务状态与租约、候选 SQL、Prompt/运行历史、恢复、analysis.json 物化及结果/API 组装。新增的代际校验、恢复和归因逻辑继续堆叠在该门面中，任何局部修改都容易跨职责回归，维护和测试影响面过大。
- **Task Review & Cut** · `app/services/video_cut_workflow_service.py:38-105; app/services/video_cut_workflow_service.py:516-583` — 切片前仍只排除 CANCELLED；_create_cut_run 未校验允许切片的任务状态，也未查询同一任务已有 queued/running Workflow Job。current_job_lease() 只是当前执行上下文，无法拦截其他请求/线程的活动 Job；同步入口和异步 Job 可同时创建切片批次，并且始终把自动任务写成 lowercase cutting，存在状态互相覆盖和重复 FFmpeg 执行风险。
- **Task Review & Cut** · `app/services/task_lifecycle_service.py:373-406; app/services/video_cut_workflow_service.py:449-460` — 完成状态门禁仍只检查是否存在 active、completed 的 output_clip 数据库行，不验证 output_file_path 当前存在且为文件，也不核对该记录是否来自当前 active cut_run。残留索引、被删除或损坏的切片仍可能让任务进入 completed/completed_with_errors，主状态与实际产物不一致。
- **Task Review & Cut** · `app/services/task_service.py:1198-1266; app/services/video_cut_workflow_service.py:274-323` — _active_outputs_match_enabled_candidates 只比较启用候选 ID、数量和输出路径 exists/is_file；切片落库时虽然保存 source_fingerprint，但同步复用路径未读取它，也没有输出文件内容指纹、大小或可播放性校验。源视频被替换或已有输出被外部替换/截断时，审核同步会错误跳过重新切片并继续同步发布。
- **Task Review & Cut** · `app/services/task_service.py:898-971` — list_clip_candidates 对 quality_evidence_json 的 JSONDecodeError 静默回退为空字典，隐藏候选数据损坏；随后 start_time/end_time 在 try 块外直接解析，单条历史候选时间格式损坏就会让整个审核列表抛 ValueError，/tasks/{id}/clips/review 无法打开，人工也无法通过页面修复该候选。
- **Task Review & Cut** · `app/services/task_query_service.py:293-332; app/services/task_query_service.py:469-486` — 片段总览返回的 can_cut 只依据 enabled_count 和 source_exists，不读取 analysis_incomplete/quality_degraded，也不排除 AI_ANALYZING 等运行状态；同时系统状态页的 failed/pending/review/completed 统计仍精确匹配 lowercase，遗漏 CREATED、FAILED_*、PENDING_SUBTITLE_REVIEW、COMPLETED 等自动流程状态，造成操作页面显示失真。
- **Task Review & Cut** · `app/services/task_service.py:508-594; app/services/task_service.py:1543-1570; app/services/task_query_service.py:165-251` — task_service.py 已约 1570 行，仍集中承担任务状态、AI、转写、切片、字幕、发送同步和查询，并保留多组只转发到 task_query_service 的包装器。list_tasks 的 _row_to_task 对每个任务单独执行 count_output_clips，Dashboard/字幕总览随后又执行批量 output_clip 查询；同时两处重复维护输出路径、文件存在和字幕状态映射，任务数增长时会产生可见的 N+1 查询和较大的修改回归半径。
- **Content Review & Feedback** · `app/routers/content_review.py:59-75` — export_sync_douyin_items 是 async handler，却直接调用同步 PublishWorkerClient.analytics_export_sync；底层 urlopen 默认超时 1800 秒，随后仍同步匹配和 SQLite 提交。Worker 卡住时可能阻塞 FastAPI event loop 最长约 30 分钟。
- **Content Review & Feedback** · `app/services/clip_feedback_service.py:87-121` — 显式反馈接口在事务内按 task 查询当前 active analysis run 并写入 feedback，没有使用候选自身的 source_analysis_run_id。恢复旧候选或与新分析并发时，反馈会错误归因到新 Run/Prompt，污染 Prompt 统计；review_toggle 路径已采用候选来源，口径不一致。
- **Content Review & Feedback** · `app/services/content_review_service.py:417-429,478-491,1208-1234` — 官方 Excel/Worker 规范化路径把每条作品 duration_seconds 固定为 None，而 Prompt 对比的 average_watch_ratio 只用该字段计算。即使已匹配切片并有时长，官方导入的 Prompt 对比仍得到空观看比例；诊断和实验路径已有 effective_duration fallback，统计口径不一致。
- **Subtitle** · `app/services/subtitle_auto_workflow_service.py:42-50,51-108` — 自动字幕流程在验证 workflow job lease 之前就创建并提交字幕工作、轨道等副作用；旧 Worker 若失去 lease，仍可能先写入新对象，之后才被 fencing 拦住，形成重复或孤立字幕状态。
- **Subtitle** · `app/services/subtitle_data_service.py:1017-1074` — 读取 ASR chunk 时遇到坏 JSON 会静默跳过，剩余分片仍可组装为合法字幕；部分转写数据可能被当成完整结果继续审核或烧录，缺少 fail-closed 完整性证据。
- **Subtitle** · `app/services/subtitle_data_service.py:162-190,1222-1230,236-330` — 源字幕 revision 先提交，随后每个片段同步各自独立事务；中途失败会留下部分片段已更新、部分仍是旧版本的非原子状态，重试和人工判断成本高。
- **Subtitle** · `app/services/subtitle_auto_workflow_service.py:513-569` — 恢复已有字幕产物只验证路径存在且是文件，没有校验非空、大小、哈希或媒体可读性；截断或损坏文件可能被误当成可恢复成功。
- **Subtitle** · `app/services/subtitle_data_service.py:1-1443` — SubtitleDataService 同时承担 revision、轨道、片段同步、ASR chunk、波形和文件落盘，且波形路径先把整段 PCM 载入内存再下采样；长视频会放大内存峰值，中心化职责也增加回归面。
- **Pipeline & Job Queue** · `app/services/pipeline_engine.py:1475-1479,1606-1623,1732-1734,1777-1779` — Job lease 只在部分写入前检查，且检查与最终 replace 之间存在 TOCTOU；选片、切片元数据、任务摘要和 schedule 仍有直接 write_text。旧 Worker 失去 lease 后仍可能覆盖新代次的文件，造成数据库 lease 已被正确 fencing 但文件证据、checkpoint 与实际产物不一致。
- **Pipeline & Job Queue** · `app/services/job_worker.py:255-258,314-336,473-495` — WorkflowJobRunner.stop() 仅设置停止事件并 join 25 秒；子进程终止失败时 watchdog 会继续循环，stop 返回后 daemon 线程、子进程和 lease 可能仍存活，后续恢复只能等待 lease 过期，无法保证干净停机。
- **Pipeline & Job Queue** · `app/services/job_worker.py:293-298; app/services/job_worker_process.py:23-27` — 父进程以 stdout=DEVNULL、stderr=DEVNULL 启动 Worker 子进程，子进程异常只 print 到 stderr 后返回 1。Provider、FFmpeg 和 Python traceback 全部丢失，父进程只能记录非零退出码或泛化失败，显著降低故障定位和复盘能力。
- **Pipeline & Job Queue** · `app/services/job_service.py:163-177; app/services/pipeline_engine.py:1810-1816` — _row_to_dict() 遇到 payload/result/checkpoint 的坏 JSON 时静默保留原始字符串；普通执行路径随后可能对字符串调用 .get，或向 API 暴露错误类型。PipelineEngine._read_json() 还将 JSON/OSError 静默转换为 fallback，元数据或 schedule 损坏时可能被当成缺省数据继续处理，隐藏真实数据损坏。
- **Pipeline & Job Queue** · `app/services/pipeline_engine.py:1-1932` — PipelineEngine 仍是约 1932 行的 God Component，同时承担九步流水线编排、checkpoint/恢复、文件指纹、AI、转写、切片、字幕、元数据、排期和发布证据处理。新增 checkpoint/watchdog 继续向该中心组件堆积职责，后续修改容易造成跨步骤回归。
- **Publish Center** · `app/services/publish_service.py:1-4789` — publish_service.py 已接近 4800 行，继续集中配置、账号、OAuth、文案、封面、队列、历史、内容实验关联、OpenCLI 兼容和 API 发布职责；本轮又新增内容实验关联查询，使发布域改动的回归半径继续扩大。
- **Publish Center** · `app/services/publish_service.py:2948-2997` — 历史查询仍在 SQL 中先按大写 PUBLISH_HISTORY_STATUSES 过滤，再做 legacy 状态归一化；数据库中的 lowercase 或旧状态会在归一化前被排除，历史页与统计可能漏数据。
- **Publish Center** · `app/services/publish_service.py:2810-2831` — create_batch_publish_jobs 对不存在的 output_clip_id 直接 continue，最终仍返回 status=ok；调用方无法区分全部无效、部分成功和完整成功，批量操作可能静默漏项。
- **Publish Center** · `app/services/publish_service.py:2173-2183` — 同步任务生成封面失败时只记录 cover_error，仍继续创建 WAITING 发布任务且不计入 errors；同步接口可能显示成功，直到发送 readiness 才暴露缺封面。
- **Publish Center** · `app/services/publish_providers.py:128-146; app/services/publish_readiness.py:150-180` — 兼容 api_publish 模式仍可创建，但 Bilibili API provider 固定返回 bilibili_provider_pending；这是一个可配置、可排队却必然失败的入口，需继续保持非默认并在界面明确限制。
- **Publish Center** · `app/services/publish_providers.py:70-104` — Douyin API 兼容发布把上传和创建作品拆为两个非幂等 HTTP 调用；平台已受理但客户端读响应超时的场景缺少幂等键或人工复核闸门，重试仍有重复作品风险。
- **Publish Scheduler** · `app/services/publish_scheduler.py:283-304` — list_due_jobs 每轮 SELECT 全部 SCHEDULED，再逐条在 Python 解析和过滤时间，没有 SQL 到期条件、分页或批量上限；排期规模增长会提高扫描内存和调度延迟。
- **Publish Scheduler** · `app/services/publish_scheduler.py:756-870` — recover_interrupted_jobs 每轮加载全部 PUBLISHING，并逐条同步查询 Worker execution；没有批量上限或退避，Worker 超时/不可用时恢复耗时会线性增长并阻塞后续排期。
- **Publish Scheduler** · `app/services/publish_scheduler.py:133-1924` — PublishScheduler 约 1800 行，集中排期计算、任务领取、结果写回、Worker 恢复、重试修复、人工复核、批量排期及旧版兼容；状态机修改的验证范围仍过大。
- **Publishers & Worker** · `scripts/publish_host_worker.py:454-479` — 跨 execution 防重复扫描仍跳过损坏日志和缺失/无效 identity 的旧 journal；若旧执行已发生浏览器副作用但身份未成功持久化，新 execution 无法证明关联，只能继续执行，极窄崩溃窗口仍可能重复投稿。
- **Publishers & Worker** · `scripts/publish_host_worker.py:717-719` — 官方报表同步的兜底异常把 str(exc) 直接放入返回给 FastAPI 的错误消息；经过认证的本地调用仍可能收到浏览器路径、页面细节或底层诊断，错误边界未统一脱敏。
- **SQLite Persistence** · `app/db/database.py:844-866,1690-2004,2525-2595` — init_db 先执行多组历史列探测/兼容迁移，再进入 schema_migrations ledger；tasks、clip_candidates、ai_analysis_runs、字幕和转写 helper 仍使用 executescript 或逐列 ALTER，未由同一个 BEGIN IMMEDIATE 覆盖。两个启动进程可同时看到缺列并竞争 ALTER，失败或中途异常会留下部分结构/数据变更而没有对应账本记录。
- **SQLite Persistence** · `app/db/database.py:1533-1570,1644-1684` — 新增 _apply_content_feedback_loop_migration 使用 sqlite3.executescript()，而 _run_schema_migrations 已先 BEGIN IMMEDIATE；executescript 会隐式提交当前事务，因此后续 verify 或账本 INSERT 失败时 rollback 不能撤销已创建的实验表/索引，破坏“Schema、验证、账本同事务”的迁移保证。
- **SQLite Persistence** · `app/db/database.py:367-385,1286-1310,1460-1512` — 新库的 ai_analysis_runs.prompt_version_id 有 FOREIGN KEY，但旧库迁移只 ALTER TABLE ADD COLUMN prompt_version_id TEXT，SQLite 不会补建外键；验证也不检查 foreign_key_list。升级旧库可写入悬空 prompt_version_id，内容复盘 JOIN 会静默缺失 Prompt 归因。
- **SQLite Persistence** · `scripts/backup_restore.py:56-96,598-619,634-665` — 恢复前健康检查和 BEGIN EXCLUSIVE 只形成瞬时检查；检查与回滚备份、媒体暂存、os.replace 数据库之间没有持续的应用/Worker 互斥。健康接口不可达时按无服务继续，服务或 Worker 可在检查后重新写入，仍有恢复后新旧 SQLite 视图分裂或丢写窗口。
- **Ops & Delivery** · `.github/workflows/ci.yml:3-7` — CI 只监听目标为 master 的 push/PR。当前 PR #65 以 fix/douyin-official-export-sync 为 base，GitHub 返回 0 个 checks，因此这批 2400 行新增内容复盘改动没有被仓库 CI 自动验证。
- **Ops & Delivery** · `scripts/start_docker_opencli.ps1:24-30` — Docker 健康检查失败只打印“仍在初始化”并正常继续，调用方可能把启动脚本成功退出误认为服务已健康。
- **Ops & Delivery** · `scripts/start.ps1:22-33` — Demo/Development 覆盖配置生效前先以正式 .env 和 E 盘路径运行 doctor；新机或迁移机本应隔离的 Demo 启动可能被旧正式路径阻断。
- **Ops & Delivery** · `scripts/start.ps1:71-82` — Windows Worker 启动失败被降级为警告并继续启动工作台；虽然页面主功能可用，但自动发布能力不可用时脚本仍整体成功，自动化或新手容易误判为完整健康。
- **Ops & Delivery** · `scripts/backup.ps1:1-6,34-43,66-70` — 手工备份默认包含 .env，仅显式 ExcludeEnv 才排除；脚本虽给出警告，但默认 ZIP 仍含 API Key/Token，复制或上传备份时存在秘密泄漏风险。
- **Ops & Delivery** · `tests/test_native_scripts.py:1-220` — 原生启停/恢复测试仍以源码字符串断言为主，实际 Windows smoke 未覆盖冲突进程、恢复中断、Worker 启动失败与健康检查失败的端到端恢复行为。

### LOW (17)

- **Media & Storage** · `app/services/storage_service.py:490` — save_uploaded_video 从 503 行直接以 wb 写入最终 source 文件，没有 .part 文件和原子替换；请求异常时 unlink 失败被静默忽略，进程崩溃/断电无法执行清理，可能留下无数据库引用的半成品上传并占用最多 4GB 磁盘。
- **Media & Storage** · `app/services/storage_service.py:256` — allocate_task_dir_name(reserve=True) 在数据库 INSERT 前用 mkdir 预占目录；调用方若在预占与建档之间崩溃，目录没有任务记录且当前没有孤儿目录扫描或恢复机制。正常 HTTP 异常可清理，但无法覆盖进程级中断。
- **Media & Storage** · `app/services/video_cut_service.py:185` — cut_single_clip 在 FFmpeg 启动前先 unlink plan.output_path；build_output_path 的正常工作流通常生成新路径，但同一 CutPlan/输出路径重试时，失败或超时会先删除已有有效成片，临时文件原子替换无法保护旧结果。
- **Transcription** · `app/services/transcript_service.py:347-549` — 本地与 Volcengine 转写分别复制了完整的分片遍历、checkpoint、进度、偏移和清理生命周期；两条路径已出现不同的请求不确定态处理，后续修复取消、租约或计数逻辑需同步维护，存在行为漂移风险。该文件同时承担 FFmpeg、模型、远程 HTTP、checkpoint、进度和 Markdown，变更影响面较大。
- **Content Review & Feedback** · `app/services/content_review_service.py:2086-2100; app/routers/content_review.py:230-235` — 解除作品匹配只按 snapshot_id 无条件更新，没有 account_id、平台或当前匹配状态约束；同一 SQLite 管理多个账号时，持有旧快照 ID 的跨账号请求可清空另一账号关联。
- **Content Review & Feedback** · `app/services/content_review_service.py:388-423,468-485` — 官方作品缺平台 ID 时，内部键仅由规范化标题和发布时间生成；同一批相同键直接 continue，两个合法的同标题同秒作品会静默丢行，无法进入人工确认。
- **Content Review & Feedback** · `app/models/content_review.py:6-7; app/routers/content_review.py:46-54` — ContentMetricImportCommitRequest.confirm 默认 True，提交接口只拒绝显式 false；空 JSON 即可直接确认写入预览批次，弱化了界面要求的明确确认。
- **Subtitle** · `app/services/subtitle_auto_workflow_service.py:140-171` — 旧式字幕 Job 激活逻辑依赖较弱的状态约束，异常恢复或并发执行时缺少与当前 workflow generation 等价的完整校验。
- **Pipeline & Job Queue** · `app/services/job_service.py:18-45,180-215; app/services/job_worker.py:55-71` — JOB_TYPE_PUBLISH 已声明、可创建并有标签，但 execute_job() 没有 publish 分支，进入 unsupported job type 并失败。当前未发现生产代码创建该类型，因此是潜在未接线能力。
- **Publish Center** · `app/services/publish_providers.py:198-205` — multipart 兼容上传使用 read_bytes 后再拼接完整请求体，大视频峰值内存接近文件大小的两倍；当前默认 Windows Browser 路径不受影响，但 API 兼容模式仍有资源风险。
- **Publish Scheduler** · `app/services/publish_scheduler.py:276-281` — shutdown 等待后台 Task 完整结束但没有超时或取消兜底；run_once 若被 Worker 超时、数据库锁或文件操作卡住，应用停机可能长时间等待。
- **Publish Scheduler** · `app/services/publish_scheduler.py:1830-1850` — queue_snapshot 对非法 scheduled_at 静默忽略，损坏排期会从今日视图消失且没有告警或异常计数。
- **Publishers & Worker** · `app/services/publishers/browser_runtime.py:315-323` — 截图捕获失败后直接返回空字符串，不记录错误或阶段；发布失败时可能静默缺失关键取证。
- **Publishers & Worker** · `app/services/publishers/page_scripts.py:1-28` — Worker 页面脚本通过延迟导入 publish_service 私有 DOM 脚本维持旧协议，形成发布服务与宿主 Worker 的 legacy glue 耦合。
- **SQLite Persistence** · `app/db/database.py:734-868,1318-1400,1533-1562` — 内容复盘新表缺少复合约束保证快照账号与导入批次账号一致、实验基线批次属于实验账号、实验作品属于实验账号。Service 层可覆盖正常入口，但直接 SQL、未来新入口或修复脚本仍可写入跨账号数据。
- **SQLite Persistence** · `app/db/database.py:1-2751` — database.py 已集中完整建表、历史兼容迁移、迁移账本、内容复盘回填、索引、Prompt 种子和备份探测，约 2751 行；它仍是所有启动与 Schema 变更的高耦合入口。
- **Ops & Delivery** · `scripts/backup_restore_runtime.py:130-155` — 兼容 runtime 模块在 import 时 monkeypatch backup core，行为依赖导入顺序，给备份恢复测试和后续维护增加隐式耦合。

## Cross-cutting themes

- **本地单体架构仍与当前产品边界匹配.** FastAPI、SQLite WAL、文件产物、持久化 Job 与 Windows Worker 适合本机单用户规模；本轮没有证据支持微服务、全量 ORM 或前端框架迁移。
- **原有 P0 数据风险保持关闭.** 测试数据库继续强制隔离，活动库 quick_check 正常且 foreign_key_check 为 0，可恢复删除与发布不确定态边界仍保留。
- **AI 与内容归因出现高置信回归.** 长直播结果缺少 quality_degraded 会被切片门禁判坏；显式候选反馈又绑定当前 active run 而非候选来源 run，可能阻断正常切片并污染 Prompt 统计。
- **新迁移破坏上轮建立的原子契约.** 内容反馈迁移在 BEGIN IMMEDIATE 内调用 executescript，导致结构先提交、验证和账本后写；升级旧库的 Prompt 外键也没有等价补建。
- **异步路由与前端多步操作仍缺少协调状态.** 30 分钟同步 Worker 调用可阻塞事件循环；预览切换、实验归属三请求和 AI 轮询存在旧响应覆盖、部分成功或停止刷新。
- **复杂度集中但不值得全面重构.** publish_service.py、content_review_service.py、database.py、app.js 与 PipelineEngine 是主要热点；只应在触碰对应业务时按事务和边界渐进拆分。
- **本地测试增长但 CI 与 Coverage 证据仍有缺口.** 当前堆叠 PR 没有 GitHub checks，Coverage 仍未生成；因此本地测试只能证明隔离回归，不能替代分支 CI、真实 Provider、Chrome 与平台验收。

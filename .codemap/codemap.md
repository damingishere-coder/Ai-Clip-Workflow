<!--
  This file:        .codemap/codemap.md   (written report)
  Interactive map:  .codemap/codemap.html
-->

# NiuMa Studio — Functional Module Quality Audit

> **Interactive view:** [`.codemap/codemap.html`](codemap.html) — per-module scores, findings, LoC, and the dependency graph. This file is the written report.

**Generated:** 2026-08-24 · **Modules:** 13 · **Size:** 48709 tracked LoC across 132 files

## Health by layer

| Layer | Modules | Avg score |
|---|--:|--:|
| 界面 · API | 2 | 62 |
| 业务编排 | 3 | 62 |
| 媒体与 AI 处理 | 4 | 69 |
| 外部执行边界 | 2 | 62 |
| 持久化与运维 | 2 | 68 |

## Per-module lines of code & score

_LoC is the representative file/folder per module; folder-level modules overlap and are not additive._

### 界面 · API

| Module | LoC | Score | Tags |
|---|--:|:--|:--|
| Frontend UI | 13,840 | 65 C | god-component, bloat, glue, duplication, legacy |
| API & Runtime | 2,742 | 58 D | fallback, legacy, dual-format, duplication, glue, bloat, god-component, silent-except, any-escape |

### 业务编排

| Module | LoC | Score | Tags |
|---|--:|:--|:--|
| Publish Center | 5,561 | 52 D | god-component, bloat, legacy, dual-format, fallback, silent-except, placeholder, duplication |
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
| Publishers & Worker | 2,116 | 62 C | any-escape, fallback, legacy, dual-format, glue, god-component, duplication |
| Publish Scheduler | 1,754 | 62 C | god-component, bloat, glue, fallback, legacy, silent-except |

### 持久化与运维

| Module | LoC | Score | Tags |
|---|--:|:--|:--|
| Ops & Delivery | 4,347 | 68 C | fallback, legacy, dual-format, duplication, bloat, glue, silent-except, monkeypatch |
| SQLite Persistence | 3,436 | 69 C | silent-except, fallback, legacy, dual-format, glue, bloat, god-component, monkeypatch |

## Worst offenders

- **Publish Center (52/D)** — app/services/publish_service.py:564: 配置/账号归一化仅新增 masked 字段却保留原始 Secret/Token，GET 接口直接返回。
- **API & Runtime (58/D)** — app/routers/settings.py:10-12: GET /api/settings/ai 无读取鉴权，直接返回 get_ai_config_context；该上下文的 values 包含 AI/ASR API Key 原始值，匿名读取请求即可获得本地配置密钥。
- **Transcription (59/D)** — app/services/transcript_workflow_service.py:317: transcript Markdown 存在即返回 completed，未校验源指纹、Provider、进度状态或文件完整性。
- **Task Review & Cut (60/C)** — app/services/video_cut_workflow_service.py:248-255: 每个 CutResult 通过 _insert_output_clip_record 独立提交事务；中途插入异常时前面的 output_clip 已落库，cut_run 未被标记 failed，任务可能停留在 cutting 并同时暴露半批新结果。
- **Publish Scheduler (62/C)** — app/services/publish_repository.py:54: 发布结果只按 job_id 写回，无 execution/worker/claim 代际条件；旧执行可覆盖新 claim 终态。
- **Publishers & Worker (62/C)** — scripts/publish_host_worker.py:74: execution_id 直接拼 journal 路径且仅限长度，../、分隔符或盘符可逃逸 Worker 状态目录，读写异常 JSON。
- **AI Selection (63/C)** — app/services/ai_config_service.py:318: get_ai_config_context 返回含 AI/ASR Key 的完整 values，GET /api/settings/ai 无读取鉴权，页面也复用该上下文。
- **Frontend UI (65/C)** — app/static/js/app.js:211: 多个写请求绕过统一 apiFetch；启用 LOCAL_ADMIN_TOKEN 时可能缺失 Authorization 并被 API 拒绝。
- **Ops & Delivery (68/C)** — scripts/migrate_task_dirs_to_project_names.py:211: 任务目录迁移用非 WAL-aware 主库 copy，先移动目录再统一更新提交，无文件补偿，异常会让路径/DB 不一致。
- **SQLite Persistence (69/C)** — app/db/database.py:31: init_db 仍依赖结构探测执行启动迁移，没有 user_version 或 schema_migrations 账本。

## All findings

### HIGH (19)

- **Frontend UI** · `app/static/js/app.js:211` — 多个写请求绕过统一 apiFetch；启用 LOCAL_ADMIN_TOKEN 时可能缺失 Authorization 并被 API 拒绝。
- **Frontend UI** · `app/static/js/app.js:1729` — 接口或任务数据直接拼接到 innerHTML；publish-center.js:2152 有同类路径，存在本地 DOM XSS/页面结构破坏风险。
- **API & Runtime** · `app/routers/settings.py:10-12` — GET /api/settings/ai 无读取鉴权，直接返回 get_ai_config_context；该上下文的 values 包含 AI/ASR API Key 原始值，匿名读取请求即可获得本地配置密钥。
- **API & Runtime** · `app/routers/tasks.py:174-179` — PATCH /api/tasks/{task_id}/status 接受任意 TaskStatus 后直接调用 update_task_status；底层更新按 id 直接写入，没有检查当前状态、前置产物或合法状态转移，调用者可把空任务直接标记为 completed。
- **Transcription** · `app/services/transcript_workflow_service.py:317` — transcript Markdown 存在即返回 completed，未校验源指纹、Provider、进度状态或文件完整性。
- **Transcription** · `app/services/transcript_service.py:265` — JobLeaseLostError 已显式透传，但 TranscriptCancelledError 仍会被统一包装为 RuntimeError，取消可能落成 failed。
- **Transcription** · `app/services/transcript_service.py:516` — 远程分块请求每次使用随机 request id，缺少持久化幂等键和超时后结果确认，重试可能重复计费。
- **AI Selection** · `app/services/ai_config_service.py:318` — get_ai_config_context 返回含 AI/ASR Key 的完整 values，GET /api/settings/ai 无读取鉴权，页面也复用该上下文。
- **Task Review & Cut** · `app/services/video_cut_workflow_service.py:248-255` — 每个 CutResult 通过 _insert_output_clip_record 独立提交事务；中途插入异常时前面的 output_clip 已落库，cut_run 未被标记 failed，任务可能停留在 cutting 并同时暴露半批新结果。
- **Task Review & Cut** · `app/services/task_lifecycle_service.py:156-180` — update_task_status 仍按 task_id 直接写入任意传入状态和进度，没有合法状态迁移、前置产物或并发条件；API 直接暴露 PATCH /status，可将空任务跳转为 completed。
- **Task Review & Cut** · `app/services/task_service.py:451-478` — _probe_video 调用 ffprobe 没有 timeout，也没有捕获 subprocess OSError、媒体读取异常或 stat 异常；get_task 默认 include_video_probe=True，损坏、卡死或不可达媒体可长期阻塞任务详情或直接 500。
- **Pipeline & Job Queue** · `app/services/pipeline_engine.py:118` — 取消仍只在每个 step 开始前检查；step 执行期间及最后一步完成后缺少统一取消门禁，Workflow Job 与任务主状态仍可能分叉。
- **Pipeline & Job Queue** · `app/services/pipeline_engine.py:158` — READY_TO_PUBLISH 在同一次 run 中立即转为 COMPLETED，待人工确认状态不可稳定观察。
- **Publish Center** · `app/services/publish_service.py:564` — 配置/账号归一化仅新增 masked 字段却保留原始 Secret/Token，GET 接口直接返回。
- **Publish Scheduler** · `app/services/publish_repository.py:54` — 发布结果只按 job_id 写回，无 execution/worker/claim 代际条件；旧执行可覆盖新 claim 终态。
- **Publish Scheduler** · `app/services/publish_scheduler.py:495` — repair_and_publish 检查与克隆间无状态锁；并发确认/重复点击可在源任务已发布后仍新建排期。
- **Publishers & Worker** · `scripts/publish_host_worker.py:74` — execution_id 直接拼 journal 路径且仅限长度，../、分隔符或盘符可逃逸 Worker 状态目录，读写异常 JSON。
- **Publishers & Worker** · `scripts/publish_host_worker.py:264` — 同 execution_id 不检查既有终态即重新发布；实例内锁无法协调同 ID 并发/重放，可能重复投稿。
- **Ops & Delivery** · `scripts/migrate_task_dirs_to_project_names.py:211` — 任务目录迁移用非 WAL-aware 主库 copy，先移动目录再统一更新提交，无文件补偿，异常会让路径/DB 不一致。

### MED (78)

- **Frontend UI** · `app/templates/system_status.html:98` — 配置/API Key 字段进入 DOM，base.html:11 还承载本地管理 Token；需确认全链路始终掩码。
- **Frontend UI** · `app/static/css/styles.css:870` — 使用多个未在 :root 定义的 CSS 自定义属性，相关声明可能失效。
- **Frontend UI** · `app/static/js/app.js:1` — 任务、转写、AI、审核、切片、字幕和配置行为集中在超大全局脚本，回归半径较大。
- **Frontend UI** · `tests/test_publish_center_browser.py:16` — Playwright 缺失时核心浏览器测试可静默跳过，字幕交互与鉴权失败路径覆盖不足。
- **API & Runtime** · `app/main.py:40-46,97-107` — 写 API 仅在 LOCAL_ADMIN_TOKEN 非空时校验，默认配置为空；同时 localhost/127.0.0.1 任意端口均允许跨源请求。服务被 Docker、局域网或其他本地网页访问时，写操作可能缺少有效鉴权。
- **API & Runtime** · `app/routers/tasks.py:128-133` — 任务详情接口调用 get_task(task_id)，而查询 SQL 未过滤 is_deleted；已软删除任务仍可能从详情入口读取，并继续成为后续媒体或操作接口的上下文。
- **API & Runtime** · `app/routers/tasks.py:220-241` — 多个 async 路由直接调用同步的音频提取、转写处理函数，另有 process_video_cuts 在 390-399 行直接调用同步切片；FFmpeg、网络或文件操作期间会阻塞 FastAPI 事件循环。
- **API & Runtime** · `app/models/task.py:7-40` — TaskStatus 同时定义大写自动流水线状态和小写手动状态，API 使用同一个枚举承载两套状态语义；结合无状态机校验，容易形成状态口径漂移和非预期跳转。
- **API & Runtime** · `app/routers/files.py:9-11` — 媒体目录浏览接口为匿名 GET，虽然服务层限制了允许根目录，但仍会返回绝对路径、目录名和视频文件列表；非纯本机部署时会扩大本地文件布局泄露面。
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
- **Publish Center** · `app/services/publish_service.py:4562` — 4k+ 行文件混合 CRUD、OAuth、内容、封面、同步、历史、多个 Publisher 与页面上下文，是高耦合 God Component。
- **Publish Center** · `app/services/publish_service.py:2935` — 历史 SQL 先用大写状态过滤再规范化，旧小写 ready/published/failed 记录可能被提前排除。
- **Publish Center** · `app/services/publish_service.py:2137` — 封面生成异常只写 item cover_error 未计入 errors，整体仍可能返回 ok，形成隐式部分成功。
- **Publish Center** · `app/services/publish_service.py:2784` — 批量创建遇无效 output_clip_id 直接跳过且仍返回 ok，全无效时也可能显示成功创建 0 条。
- **Publish Center** · `app/services/publish_readiness.py:167` — api_publish 可被创建且兼容入口仍在，但 readiness 正常调度统一判 unsupported，模式契约不一致。
- **Publish Center** · `app/services/publish_providers.py:216` — 旧 API 发布超时统一 failed；若平台已接收而响应丢失，重试可能重复投稿且未进入 NEED_REVIEW。
- **Publish Scheduler** · `app/services/publish_scheduler.py:1403` — 丢弃 create_task 引用，关闭只 stop 不 await，存在 Sonar S7502 的生命周期/优雅停机缺口。
- **Publish Scheduler** · `app/services/publish_scheduler.py:264` — 每轮加载全部 SCHEDULED 再由 Python 判断到期，无 SQL due 条件/批量上限。
- **Publish Scheduler** · `app/services/publish_scheduler.py:617` — 全部 PUBLISHING 任务串行查询 Worker execution；多个超时会拖长整轮扫描。
- **Publish Scheduler** · `app/services/publish_scheduler.py:1305` — 风险 JSON 损坏时静默回空列表，可能绕过风险复核继续投稿，属于 fail-open。
- **Publish Scheduler** · `app/services/publish_scheduler.py:961` — 人工发布 URL 只做域名子串匹配，不解析 hostname，错误域名/query 也可通过。
- **Publish Scheduler** · `tests/test_publish_scheduler_state_machine.py:302` — 未覆盖旧 execution 回写、新旧 claim、repair 并发、后台 Task 生命周期和损坏 risk_flags。
- **Publishers & Worker** · `scripts/publish_host_worker.py:231` — 账号操作先 check locked 再后台获取锁，存在竞态；锁仅进程内，多 Worker/重启不持久。
- **Publishers & Worker** · `app/services/publishers/browser_runtime.py:32` — platform/account_id 直接拼浏览器 profile/artifact 路径，异常 ID 可路径穿越或造成账号目录冲突。
- **Publishers & Worker** · `scripts/publish_host_worker.py:293` — Worker 把 result/diagnostics 原样写 journal，兼容 Provider 异常输出中的 Token/Cookie 可能长期落盘并经接口返回。
- **Publishers & Worker** · `app/services/publishers/page_scripts.py:14` — Worker 页面脚本延迟导入超大 publish_service 私有函数，形成 legacy glue 与高回归半径。
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

### LOW (36)

- **Frontend UI** · `app/static/js/subtitle-editor.js:171` — 字幕编辑器已有 escapeHtml、虚拟列表、竞态 token 与自动保存版本控制，是可保留的正向实现。
- **Frontend UI** · `app/static/js/publish-center.js:4` — 前端明确只创建抖音任务，B站保留后端兼容；这是当前产品边界而非应机械删除的代码。
- **API & Runtime** · `app/main.py:153-155` — /health 只返回进程级 status/app，不检查 SQLite、持久化 Job Runner、Scheduler、FFmpeg 或发布 Worker，就绪状态不足。
- **API & Runtime** · `app/routers/publish.py:63-71` — OAuth callback 捕获通用 Exception，并将原始异常字符串拼入 redirect query；错误细节可能进入浏览器地址栏、历史记录或 Referer。
- **API & Runtime** · `app/core/config.py:80,244-250` — local_admin_token 在 Settings 中重复定义，AI 新旧配置字段也并存并通过兼容 fallback 读取，配置所有权和运行时来源不够单一。
- **API & Runtime** · `app/models/subtitle.py:86` — speaker_styles 使用 Any 嵌套字典，API 模型层无法约束渲染输入结构，非法配置可能延迟到字幕渲染阶段才失败。
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
- **Publish Center** · `app/services/publish_providers.py:204` — 旧 multipart 发布把完整视频和完整请求体同时载入内存，大文件有约双倍峰值。
- **Publish Center** · `app/services/publish_service.py:1579` — 批量 job 规范化在缺 accounts 时逐项查询账号，存在 N+1。
- **Publish Center** · `app/services/publish_service.py:61` — 旧小写与新大写状态字典重复定义/覆盖，增加双格式维护成本。
- **Publish Center** · `tests/test_publish_history.py:111` — 未覆盖旧状态 SQL 过滤、原始 Secret 响应、批量无效 ID、封面 partial 和 API 超时不确定结果。
- **Publish Scheduler** · `app/services/publish_repository.py:33` — provider_response 脱敏但 publish_result 直接序列化完整结果，兼容 Publisher 敏感字段可能入库。
- **Publish Scheduler** · `app/services/publish_scheduler.py:1321` — 发布日志写异常被静默吞，审计日志缺失无健康告警。
- **Publishers & Worker** · `scripts/opencli_host_bridge.py:74` — 旧 OpenCLI Bridge 类无 Worker Token 校验，虽 main 已转调新 Worker，双入口容易造成安全边界误解。
- **Publishers & Worker** · `tests/test_publish_worker_client.py:138` — 未覆盖 execution id 重放/并发、恶意路径、账号锁竞态、profile 隔离和 journal 敏感字段清洗。
- **SQLite Persistence** · `app/db/database.py:656` — Workflow claim 索引未覆盖 lease_expires_at，队列规模增长后过期接管扫描可能退化。
- **SQLite Persistence** · `tests/test_job_fencing.py:206` — 已覆盖并发补列和旧 running guard，仍缺完整启动链迁移失败回滚测试。
- **Ops & Delivery** · `scripts/acceptance.ps1:115` — 验收递归扫描正式任务目录，媒体量大/锁文件会显著拖慢或阻断 release gate。
- **Ops & Delivery** · `scripts/backup_restore_runtime.py:142` — import 时 monkeypatch backup core，全局行为依赖导入顺序。
- **Ops & Delivery** · `docs/PORTABLE_SETUP.md:127` — 文档描述与当前 start.ps1 实现不符，旧 Next Steps 又保留兼容入口，启动排障认知漂移。

## Cross-cutting themes

- **本地单体架构是当前可运行的主要原因.** FastAPI + SQLite WAL + 文件产物 + 持久化 Job + Windows Worker 与个人本机规模匹配；不需要微服务化，现有恢复骨架应保留。
- **P0 数据一致性边界已封口，事务型旧债仍需继续.** 活动库外键、测试误删和媒体删除回滚已修复；迁移无账本、切片批次半提交与并发版本号仍是下一阶段重点。
- **旧执行覆盖新执行是跨模块重复风险.** Workflow Job、Publish Scheduler 和 Windows Worker 都缺少完整的 owner/attempt/execution fencing；异常恢复顺序变化时可能重复执行或写错终态。
- **可用性 fallback 正在掩盖降级结果.** AI 部分窗口、转写旧产物、损坏 JSON、封面生成、批量发布和风险字段多处静默继续，用户不一定能区分完整成功、部分成功和旧结果。
- **发布与配置读取是主要安全边界.** Publish Center、AI Config、Frontend 和 Worker 共同存在原始 Secret 响应、可选写鉴权、DOM XSS、路径字符和 journal 脱敏缺口。
- **复杂度集中而非全项目平均恶化.** publish_service.py、publish-center.js、app.js、publish_scheduler.py、subtitle_data_service.py 和 transcript_service.py 是主要 God Component；应按业务边界渐进拆分。
- **测试已与活动数据隔离，但 Coverage 与真实故障闭环仍不足.** 546 项测试全部通过，Pytest 已强制使用进程级 sandbox；Coverage 尚未采集，真实 E2E、并发恢复和外部副作用幂等仍需补齐。

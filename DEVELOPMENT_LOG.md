# Development Log

## 2026-06-04 AI 置信度分数兼容修复
- 修复远程 AI 返回 `confidence_score` 为 8.9、7.8 这类十分制分数时，Pydantic 校验要求 0 到 1 导致 `AI 返回非法 JSON，安全重试后仍失败` 的问题。
- `app/services/ai/ai_clip_analyzer.py` 在 AI JSON 进入字段校验前会统一规范化置信度：0 到 1 原样保留，1 到 10 自动除以 10，10 到 100 自动除以 100，非法值兜底为 0.7，最终夹在 0 到 1 范围内。
- `scripts/test_ai_json_validation.py` 新增十分制和百分比格式回归测试，覆盖 `8.9 -> 0.89` 和 `92% -> 0.92`，避免后续远程 AI 再因同类分数字段失败。
- 已验证：`.venv\Scripts\python.exe scripts\test_ai_json_validation.py` 通过；`.venv\Scripts\python.exe -m py_compile app\services\ai\ai_clip_analyzer.py scripts\test_ai_json_validation.py` 通过。
- 本次不调整页面结构，因此不需要同步更新 `docs/UI_REFERENCE.md`。

## 2026-06-04 任务详情日志侧栏精简
- 按浏览器标注反馈，移除任务详情页右侧“日志 / 元信息”路径清单，避免和基础信息、运行日志重复。
- 右侧侧栏现在只保留“运行日志”，继续使用原有 `runtime-log-lines` 和 `runtime-log-state` 节点读取并刷新 `logs/process.log`。
- 同步更新 `docs/UI_REFERENCE.md` 和 `NEXT_STEPS.md`，记录新的页面结构和验收方式。

## 2026-06-04 AI 远程分析旧字段兼容修复
- 修复远程 AI 返回旧字段导致分析失败的问题：当 AI 输出 `clip_key`、`viral_value`、`reason`、`editing_suggestion` 等旧字段时，程序会在 Pydantic 校验前自动转换为当前需要的 `clip_id`、`spread_value`、`highlight_reason`、`suggested_editing`。
- 新增 AI 片段字段保底逻辑：缺少 `clip_id` 时自动生成 `clip_001` 这类编号；缺少 `summary`、`spread_value`、`suggested_editing` 时填入可审核的安全默认值；缺少 `duration_seconds` 但有起止时间时自动计算片段时长。
- `scripts/test_ai_json_validation.py` 新增旧字段回归用例，覆盖本次报错里的 `clip_key` / `viral_value` / `reason` 格式。
- 本次不调整页面结构、不扩大 Prompt 或模型接入范围，因此不需要同步更新 `docs/UI_REFERENCE.md`。

## 2026-06-02 DeepSeek AI JSON 解析稳定性修复
- 修复使用 2 号“康熙来了综艺短视频切片专家”Prompt 运行 DeepSeek AI 分析时，AI 返回接近 JSON 但存在尾随逗号、Markdown 代码块、未加双引号字段名或 Python 风格布尔值，导致 `AI 返回非法 JSON，安全重试后仍失败` 的问题。
- `app/services/ai/ai_clip_analyzer.py` 新增 AI JSON 提取和轻量修复流程：先尝试标准 JSON，再提取正文里的 JSON 主体，并兼容常见 AI 输出瑕疵；修复后仍会继续走 Pydantic 字段校验、时间范围校验和片段时长校验。
- `app/services/ai/remote_responses_provider.py` 将 DeepSeek Chat Completions 的 `max_tokens` 从 4096 提高到 8192，并加入较低 `temperature`，降低长 Prompt 输出半截 JSON 或格式漂移的概率。
- `scripts/test_ai_json_validation.py` 新增本地回归测试，覆盖标准 JSON、Markdown fenced JSON、尾随逗号、未加双引号字段名和 Python literal 五种情况。
- 已验证：`.venv\Scripts\python.exe scripts\test_ai_json_validation.py` 通过；`.venv\Scripts\python.exe -m py_compile app\services\ai\ai_clip_analyzer.py app\services\ai\remote_responses_provider.py scripts\test_ai_json_validation.py` 通过；`.venv\Scripts\python.exe scripts\test_remote_ai_connection.py` 通过。

## 2026-06-02 停止转写卡住修复
- 修复任务详情页点击“停止转写”后一直停留在“正在停止 / 转写中”的问题。
- 原因：如果后台服务重启过，内存里的运行中任务标记会丢失，但 `transcript_progress.json` 仍可能停在 `cancelling`，前端会持续轮询并一直显示正在停止。
- `app/services/task_service.py` 新增停止转写自愈逻辑：当状态查询或再次点击停止时，发现 `cancelling` 进度已经没有真实后台任务承接，会自动写成 `cancelled`，并把任务状态退回可重新处理的 `pending_processing`。
- `scripts/test_transcript_background_start.py` 新增回归测试，覆盖服务重启后遗留 `cancelling` 进度的自动收尾场景。

## 2026-05-30 火山引擎远程转写请求修复
- 已定位“测试4”远程转写失败原因：程序把 MP3 二进制直接 POST 到火山引擎接口，服务端按 JSON 解析时遇到 MP3 文件头 `ID3`，返回 HTTP 400：`invalid character 'I' looking for beginning of value`。
- `app/services/transcript_service.py` 已改为按火山引擎极速版要求发送 JSON 请求体，将音频内容 base64 后放入 `audio.data`，并设置 `Content-Type: application/json`、`X-Api-Sequence: -1`。
- 新增火山引擎业务状态码检查；当分段转写遇到 `20000003` 无有效人声且当前允许空结果时，会跳过该空白小段继续处理，避免静音片段导致整条视频失败。
- `scripts/test_volcengine_transcription_connection.py` 已兼容当前 `.env` 中的 `VOLCENGINE_ASR_APP_KEY` / `VOLCENGINE_ASR_ACCESS_KEY` 配置，不再只检查 `VOLCENGINE_ASR_API_KEY`。
- 已运行 `.venv\Scripts\python.exe scripts\test_volcengine_transcription_provider.py`、`.venv\Scripts\python.exe -m compileall app scripts`。
- 已用“测试4”的音频做真实火山引擎连通性测试：前 8 秒静音片段按空结果跳过，前 60 秒可成功识别 16 句。

## 2026-05-30 分支合并确认
- 已检查当前目录为 `C:\Users\10578\Documents\New project 2`，当前分支原本为 `codex/subtitlefunction`，工作区干净，没有未提交改动。
- 本地没有 `main` 分支，当前项目主分支为 `master`。
- 已确认 `codex/subtitlefunction` 已经是 `master` 的祖先分支，执行 `git merge codex/subtitlefunction` 后 Git 返回 `Already up to date.`，说明当前分支内容已经合入主分支。
- 当前已停留在 `master` 分支；本次合并没有产生代码冲突，也没有改动业务文件。

## 2026-05-27 片段审核功能优化
- 片段审核页新增候选片段删除能力：每张候选卡片提供“删除”按钮，点击后立即从页面移除，并通过 `DELETE /api/tasks/{task_id}/clips/{clip_id}` 写入数据库隐藏状态。
- `clip_candidates` 表新增 `is_deleted` 和 `deleted_at` 字段；候选片段列表、启用片段统计和自动切片流程默认排除已删除片段，删除不会影响源视频、转写文件或已生成切片文件。
- 候选片段卡片改为更紧凑的审核视图：保留标题、起止时间、摘要和常用操作按钮，推荐理由、传播价值、剪辑建议和 AI 来源收进可展开区域，方便连续浏览多个片段。
- 前端删除成功后会自动禁用空列表下的“保存修改 / 生成切片”按钮，避免误操作。

## 2026-05-27 AI 片段完整性优化
- 新增“综艺访谈完整上下文专家”Prompt，面向康熙来了、综艺和访谈内容，要求 AI 选择包含铺垫、核心爆点 / 观点、反应和自然收尾的完整短视频单元；数据库初始化优先写入空的 2 号方案，如果 2 号已有内容则写入空的 3 号方案，不覆盖用户已编辑 Prompt。
- 默认切片最长建议从 2 分钟调整为 5 分钟；新建任务页补充提示：直播干货建议 3-5 分钟，综艺访谈建议 4-6 分钟。
- AI 分析结果继续只做格式、时间范围和最大时长校验；新增低于 45 秒候选片段的日志提醒，提示可改用 2 号 Prompt 或调高最大切片时长后重跑。
- 片段审核页文案调整为“AI 结果检查入口”，AI 分析完成后提示可检查后直接生成切片，不再把审核页描述为必须逐条补剪的人工环节。

## 2026-05-26 字幕页片段内剪切重构
- 重构 `/subtitles/{task_id}` 的“修改剪切”弹窗。弹窗现在加载当前已生成的短视频片段，不再加载整段源视频；剪切时间轴改为片段内相对时间，默认范围为 `00:00` 到片段结尾。
- 字幕页剪切交互改为复用项目已有的 noUiSlider 双把手滑块，保留类似手机剪切的左侧播放按钮、黑色帧条、黄色选区和左右把手；保存时会把片段内相对入点 / 出点换算回原视频绝对时间，并写回片段审核数据。
- 已验证：`node --check app/static/js/app.js` 通过；`python -m compileall app` 通过；浏览器已确认页面加载新版 `app.js`、noUiSlider 资源和当前输出片段媒体地址。

## 2026-05-26 片段审核源监视器滑块升级
- 将片段审核页源监视器的手写时间轴拖拽替换为本地静态依赖 `noUiSlider 15.8.1`，保留现有深色弹窗、视频预览、入点 / 出点按钮和“应用到片段”流程。
- noUiSlider 负责双手柄范围选择、蓝色选区、刻度、1 / 5 / 15 秒步长、最小时长和任务最大片段时长限制，拖动时会实时同步入点、出点、片段时长和播放头。
- 新增本地 vendor 文件 `app/static/vendor/nouislider/`，不依赖 CDN，也不引入 npm / React / Vue。
- 已验证：`node --check app/static/js/app.js` 通过；`.venv\Scripts\python.exe -m compileall app` 通过。

## 2026-05-25 抖音 + B站发布后台接入
- 新增独立 `/publish` 发布中心，左侧导航增加“发布中心”，字幕工作台的推送入口改为跳转到发布中心。
- 新增抖音后台和 B站后台两个页签，每个后台包含开放平台应用配置、发布账号、待发布切片、批量配置和发布记录。
- 新增 `publish_platform_configs`、`publish_accounts`、`publish_jobs` 三类发布数据表，保存平台配置、授权账号、发布任务、平台返回 ID、审核状态、错误码和重试次数。
- 新增 `/api/publish/...` 系列接口，支持保存平台配置、测试配置、抖音 OAuth 授权链接、保存账号、创建草稿、创建人工发布任务、真实接口发布、失败重试和人工标记状态。
- 新增 `DouyinPublishProvider` 和 `BilibiliPublishProvider`；抖音 provider 已准备官方上传视频和创建视频调用链路，B站 provider 先完成配置、字段和状态适配，等待开放平台投稿接口权限后补齐真实调用。
- 安全边界：不保存平台账号密码、不使用 Cookie、不模拟网页登录；真实发布前前端会二次确认，未配置账号或凭证时后端会拦截且不会创建错误任务。
- 已验证：`.venv\Scripts\python.exe -m py_compile ...` 通过；`/publish`、`/api/publish/platforms`、`/api/publish/accounts`、`/api/publish/jobs` 返回 200；未配置账号时调用真实抖音发布返回 400，发布任务数量不增加。

## 2026-05-25 字幕工作台剪切弹窗升级
- 按浏览器标注和参考截图，把 `/subtitles/{task_id}` 的“修改剪切”从简单预览改为真正的入点 / 出点编辑弹窗。
- 弹窗改为读取源视频时间线，显示黑色帧条、左侧播放区、粉色外框和黄色选区，左右黄色手柄可拖动调整入点 / 出点。
- 时间轴会围绕当前片段自动放大显示，避免 40 分钟以上源视频里选区太窄不好拖。
- 弹窗新增入点 / 出点输入框、“设当前位置为入点 / 出点”、“预览这一段”和“保存入点 / 出点”；保存会调用现有候选片段更新接口写回数据库。
- 保存入点 / 出点后，需要回到片段审核页重新生成切片，字幕工作台里的旧切片视频才会被新时间范围替换。
- 已验证：`node --check app/static/js/app.js` 通过；`python -m compileall app` 通过；浏览器打开 `http://localhost:8001/subtitles/f8e1edf6a57f` 后，弹窗可打开并显示放大的黄色剪切选区。

## 2026-05-25 片段审核源监视器
- 片段审核页每条候选片段新增“编辑出入点”按钮，可打开深色源监视器弹窗。
- 源监视器支持源视频预览、当前时间码、时间轴缩放、蓝色入点 / 出点范围、播放头、左右手柄拖拽和 1 / 5 / 15 秒微调。
- 支持“设为入点”“设为出点”“跳到入点”“跳到出点”“预览当前片段”和“应用到片段”；应用后先回填当前卡片，仍需点击“保存修改”写入数据库。
- 片段转写接口支持传入页面上未保存的 `start_time` / `end_time`，方便调整后立即核对对应原文。

## 2026-05-25 运行日志浅色化
- 任务详情页右侧“运行日志”从黑色终端风格改为浅色玻璃卡片风格，和当前 Apple 风格后台页面保持一致。
- 日志内容区域改为浅蓝白背景、深灰文字和蓝色细滚动条，保留自动换行、固定高度和滚动查看最新日志的能力。
- 本次只调整前端样式文件，不改变 AI 分析、日志读取和任务处理逻辑。

## 2026-05-24 AI 分析日志和进度条
- 任务详情页 AI 分析区域新增独立进度条，点击 DeepSeek AI 分析或本地 AI 分析后会立即显示当前阶段和百分比。
- 右侧“日志 / 元信息”侧栏新增运行日志框，会读取 `logs/process.log` 的最新内容，AI 分析时前端每 3 秒自动刷新一次。
- 新增 `GET /api/tasks/{task_id}/ai-analysis-status`，统一返回 AI 分析状态、进度、任务日志尾部和分析文件是否存在。
- AI 分析接口改为在线程池中执行，避免长时间 AI 请求阻塞同一服务里的状态 / 日志查询。

## 2026-05-24 长视频 AI 分析稳定性修复
- 远程 DeepSeek 和本地 Ollama 现在统一使用 3 分钟左右的小段分段分析，不再把 40 分钟或 1 小时以上转写全文一次性塞给 AI，避免长 JSON 输出被截断。
- 新增 AI JSON 兼容层：可自动把 `clip_key` 转为 `clip_id`，从 `viral_value` 等字段补齐 `spread_value`，并为缺失的摘要、推荐理由、剪辑建议提供保底值。
- 分段分析改为“部分失败可跳过”：单个小段失败会写入任务日志，只要其他小段生成了可用候选，就会合并、去重、排序并进入人工审核。
- DeepSeek Chat Completions 的 `max_tokens` 提高到 8192，同时页面端会把超长技术错误压缩成可读摘要，详细错误继续写入任务日志。
- 已同步更新当前 SQLite 中的 2 号“康熙来了综艺短视频切片专家”Prompt，让它直接输出程序需要的 `clip_id`、`spread_value` 等字段。
- 新增 `scripts/test_ai_long_analysis_resilience.py`，覆盖远程分段、旧字段兼容和部分分段失败仍保留可用候选。
- 已运行 `.venv\Scripts\python.exe -m compileall app scripts`、`.venv\Scripts\python.exe scripts\test_ai_json_validation.py`、`.venv\Scripts\python.exe scripts\test_local_ai_chunked_analysis.py`、`.venv\Scripts\python.exe scripts\test_ai_long_analysis_resilience.py`。

## 2026-05-24 国内远程转写服务商接入
- 新增可切换转写服务商配置：`TRANSCRIPTION_PROVIDER`、`TRANSCRIPTION_FALLBACK_PROVIDER`，默认优先使用火山引擎远程转写，失败后可自动退回本地 faster-whisper。
- 接入火山引擎大模型录音文件极速版识别：分段压缩为 16k 单声道 MP3/OGG 后请求 `https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash`，再把 `utterances` 转成现有 `transcript.md` 时间戳格式。
- 预留阿里云、腾讯云、讯飞 Provider 名称；当前版本只完整实现 `volcengine` 和 `local`，未实现厂商会给出明确错误提示。
- 任务详情页转写进度会显示实际来源，例如“火山引擎远程转写 / volc.bigasr.auc_turbo / remote / mp3”。
- 任务详情页新增“停止转写”按钮；后台任务会在当前分段结束后停止，并把进度标记为 `cancelled`，方便重新生成转写。
- 新增 `scripts/test_volcengine_transcription_provider.py` 验证火山引擎结果解析和远程失败本地兜底；新增 `scripts/test_volcengine_transcription_connection.py` 用真实短音频检查火山引擎 Key 和接口。
- 已运行 `.venv\Scripts\python.exe -m compileall app scripts`、`scripts/test_transcript_chunking.py`、`scripts/test_transcript_background_start.py`、`scripts/test_volcengine_transcription_provider.py`。

## 2026-05-24 自动加字幕功能完善
- 将 `/subtitles` 调整为“字幕任务列表”一级页面，切片生成后按任务分组展示，用户需要先进入具体任务再处理字幕，避免不同视频混在一个工作台里。
- 新增 `/subtitles/{task_id}` 单任务字幕工作台，展示该任务下每条输出切片、原视频预览、字幕状态、带字幕成片预览和后续推送入口。
- 新增 `subtitle_style_presets` 和 `subtitle_jobs` 数据表，字幕样式从浏览器本地保存改为写入 SQLite，字幕生成状态也会落库。
- 自动加字幕已接入真实 FFmpeg 流程：从任务转写文本按切片时间范围生成 `.ass` 字幕文件，再输出到任务目录 `06_subtitled`。
- 新增带字幕视频访问接口 `/media/tasks/{task_id}/subtitled-clips/{output_clip_id}`，页面可直接对比原切片和带字幕成片。
- “修改剪切”改为弹出剪切预览窗口，提供类似手机剪切时间轴的滑动预览入口；真正保存起止时间仍回到片段审核页执行。
- 修复 Windows 本地服务读取 Docker 路径 `/workspace/tasks/...` 的兼容问题，会自动映射回当前任务存储目录。
- 已验证：`python -m compileall app` 通过；`/subtitles` 和 `/subtitles/37601f8548fd` 页面返回 200；任务 `37601f8548fd` 已成功生成 1 条带字幕视频并能通过媒体接口访问。

## 2026-05-24 v1.1 清理与呈现优化

- 将 FastAPI 版本号更新为 `1.1.0`，侧边栏文案更新为“v1.1 本地切片全流程”。
- 删除根目录重复 UI 图片副本，保留 `docs/design/live_streaming_slicing_workflow_ui_16x9.png` 作为正式设计参考。
- 移除旧的 `/api/tasks/{task_id}/clips/generate` 切片占位接口，页面继续统一调用真实切割接口 `/api/tasks/{task_id}/process/cuts`。
- 删除早期未引用的 `app/services/ai_clip_service.py` 占位服务，当前 AI 分析以 `app/services/ai/` 为主。
- 工作台、任务列表和字幕推送页新增或强化输出切片、待加字幕、待推送数据呈现。
- 重写 `docs/DATABASE_SCHEMA.md`，修复乱码并同步当前 `tasks`、`clip_candidates`、`output_clip`、AI Prompt 和 AI 分析历史表结构。
- 同步更新 README、UI 参考、任务流、架构说明、片段审核说明和下一步计划。

## 2026-05-23 新增字幕、推送功能 demo

- 新增 `/subtitles` 字幕与推送工作台，切片生成后的 `output_clip` 记录会进入这里作为后续字幕工作流队列。
- 新增切片输出视频预览接口 `/media/tasks/{task_id}/output-clips/{output_clip_id}`，字幕工作台可直接播放本地已生成切片。
- 左侧导航新增“字幕推送”，任务列表新增“后续工作流”列，切片输出存在时会提示“待加字幕”。
- 片段审核页生成切片操作区新增“去字幕推送”入口，生成成功提示用户继续进入字幕、打码和发布配置。
- 字幕工作台预留视频查看、删除、返回修改剪切、自动加字幕、字幕样式保存、打码区域和抖音 / B站一键推送入口。
- 新增 `docs/SUBTITLE_AND_PUBLISH_PLAN.md`，记录字幕渲染、打码和平台推送的后续落地计划。

## 2026-05-23 Ai分析预览修改和数目修改

- 新增 `ai_analysis_runs` 历史表，每次 AI 分析完成都会保存一次完整结果，包括分析编号、AI 来源、模型、Prompt 方案、目标候选数量、整体总结、降级提示和候选片段 JSON。
- 任务详情页 AI 分析区域新增“候选片段数量”输入框，默认 5 条，分析前会自动保存到任务配置。
- AI 分析预览改为可持久显示：刷新页面后会读取最新历史记录；旧任务如果还没有历史记录，会回退读取当前 `analysis/candidate_clips.json`。
- 新增“历史分析结果”面板，可查看每次分析的来源、模型、Prompt 方案、时间、候选条数和总结，并支持一键恢复整次历史分析结果。
- 恢复历史分析时会重新写入当前 `analysis/candidate_clips.json` 和 `clip_candidates` 表，片段审核页会立即使用恢复后的候选片段。

## 2026-05-23 Ai分析UI修改和审核问题修改

- 任务详情页的 AI Prompt 方案从三张并排卡片改为文件夹标签式布局，顶部只显示 1、2、3 号方案，下面只展示当前选中方案的名称和可拉长 Prompt 输入框。
- AI 分析完成后不再立即刷新页面，而是在 AI 分析区域下方显示本次选取结果摘要，包括实际 AI 来源 / 模型、候选数量、每条片段标题、开始时间、结束时间和视频长度。
- AI 分析结果摘要新增“转到片段审核”按钮，可直接进入当前任务的片段审核页。
- AI 分析接口补充返回 `provider_label`、`model`、`analysis_summary`、`clip_summaries` 和 `/clips/review` 审核地址；远程失败自动降级本地时会把原因返回给页面。
- 片段审核页继续读取 `analysis/candidate_clips.json` 中的 `analysis_meta` 展示 AI 来源；如果远程不可用后自动降级，审核页显示本地 Ollama 属于实际执行结果。
- 远程分析按钮文案改为“DeepSeek AI 分析”，本地 `.env` 已按 DeepSeek OpenAI-compatible 配置更新为 `https://api.deepseek.com`、`deepseek-v4-flash`、`chat_completions`，并已通过远程连通性测试。
- 更新全局静态资源版本号，避免 Docker 重建后浏览器继续使用旧版 CSS / JS 缓存，导致 AI Prompt 标签页仍按旧三列样式显示。
- Docker 本地开发模式改为挂载 `app/` 和 `prompts/` 到容器内，并使用 `uvicorn --reload` 启动；后续修改页面、样式、JS、Python 或 Prompt 后，刷新网页即可看到，Python 改动会自动重载服务。
- `/static` 静态资源增加本地 no-cache 响应头，避免浏览器继续使用旧 CSS / JS。

## 2026-05-23 Docker 名称统一与旧镜像清理

- `docker-compose.yml` 新增 Compose 项目名 `live-streaming-slicing-workflow`，避免 Docker Desktop 继续按目录名显示为 `newproject2`。
- `workflow` 服务新增固定镜像名 `live-streaming-slicing-workflow:latest`，后续重新构建会覆盖这个明确命名的镜像，不再继续生成 `newproject2-workflow`。
- 保留容器名 `live-streaming-slicing-workflow`、端口 `8001:8001`、项目内数据库挂载和 E 盘任务产物挂载，避免影响已有数据和视频文件。
- 确认 Docker Desktop 中多个 2.39GB 的 `<none>` 主要来自历史重新构建留下的悬空旧镜像；普通启动不会每次复制一份 2GB 项目内容。
- 本轮清理策略只处理旧 Compose 项目资源、悬空镜像和无用构建缓存，不删除当前可运行镜像、数据库、任务目录或 E 盘产物。

## 2026-05-23 Ai分析prompt制定
- 任务详情页的“AI 偏好”升级为“AI Prompt 方案”：提供 1、2、3 号全局共用方案卡片，可编辑方案名称和完整 Prompt。
- 新增全局 `ai_prompt_presets` 表，并为任务增加当前选中的 `ai_prompt_preset_id`；数据库初始化时自动写入 1 号默认直播切片分析专家 Prompt。
- AI 分析时改为读取当前任务选择的 Prompt 方案，再注入 `{{MAX_CLIP_DURATION}}`、`{{TARGET_CLIP_COUNT}}`、`{{AI_PREFERENCE}}`、`{{TRANSCRIPT_TEXT}}`。
- 远程 AI 分析前新增二次确认，提醒会使用当前 Prompt 方案重新生成候选片段并覆盖已有 AI 候选结果。
- AI 返回 JSON 允许不包含 `task_id`，程序会自动补当前任务 ID，兼容新的默认 Prompt 输出格式。
- 已运行 `python -m compileall app scripts`、`scripts/test_ai_json_validation.py`、`scripts/test_mock_transcript_analysis.py` 和 `scripts/test_local_ai_chunked_analysis.py`。

## 2026-05-21 列表序列变更
- 根据浏览器标注调整左侧导航菜单顺序：工作台、新建任务、任务列表、片段审核、系统状态。
- 本次只调整全局基础模板 `app/templates/base.html` 的导航排列，不改变页面路由和功能逻辑。
- 同步更新 `NEXT_STEPS.md` 和 `docs/UI_REFERENCE.md`，记录当前菜单顺序。

## 2026-05-21 第二十二轮：Docker 一键启动接入

- 新增 `Dockerfile`，使用可从微软镜像源拉取的 Python 3.12 Bookworm 镜像构建后端运行环境，并在容器内安装 `ffmpeg`，让 `ffmpeg/ffprobe` 可在 Docker 里直接使用。
- 构建时会移除基础镜像里本项目不需要的 Yarn apt 源，避免 Yarn 签名问题阻断 FFmpeg 安装。
- 新增 `docker-compose.yml`，固定映射 `8001:8001`，容器名为 `live-streaming-slicing-workflow`，并读取本地 `.env`。
- Docker 版数据库继续挂载到项目 `data/`，任务产物继续挂载到 E 盘 `E:\直播间切片工作流存储`，容器内路径为 `/workspace/tasks`。
- `app/core/config.py` 新增 `DATA_DIR`、`DATABASE_PATH`、`STORAGE_ROOT`、`TASKS_DIR` 环境变量读取能力，本地运行默认值保持不变。
- 新增 `.dockerignore`，避免 `.venv`、真实 `.env`、数据库、任务产物、大视频和音频文件进入 Docker 镜像。
- 更新 `.env.example`、`README.md`、`docs/PROJECT_GUIDE.md` 和 `NEXT_STEPS.md`，补充 Docker 启动、停止、查看端口和测试说明。
- 修复任务详情页已完成转写后反复自动刷新的问题：转写轮询完成后只停止轮询，不再自动刷新整页，并给 `app.js` 增加版本参数避免浏览器继续使用旧缓存。

## 2026-05-20 第二十一轮：AI 调用配置诊断与本地降级修复

- 远程 AI Key 读取改为兼容 `AI_REMOTE_API_KEY` 和 `OPENAI_API_KEY`；系统状态页会提示 Key 是否看起来有效，避免把 1 位或空密钥误认为已配置。
- AI URL 拼接增加防呆：`https://ai.oneinfinityai.com/v1` + `/v1/responses` 不会再拼成重复 `/v1/v1/responses`，本地完整 endpoint 也不会重复拼接 `/chat/completions`。
- 片段审核远程分析明确使用 `AI_REMOTE_REVIEW_MODEL`，默认仍为 `gpt-5.5`、Responses 协议、`xhigh` 推理强度和不保存响应内容。
- 本地 AI 默认模型改为 `qwen3:8b`，并新增 `AI_LOCAL_HEALTH_TIMEOUT_SECONDS`；远程失败自动降级本地前会先检查 Ollama 是否在线、目标模型是否已安装。
- 新增 `scripts/diagnose_ai_environment.py`，用于一次性检查远程 Key、远程 JSON 调用、本地 Ollama 模型和本地 JSON 调用。
- 已清空本机 `.env` 里明显无效的 `AI_REMOTE_API_KEY=1`，避免它覆盖后续 `OPENAI_API_KEY` 兜底；没有把聊天中暴露过的真实 Key 写入文件。
- 本地 AI 片段审核改为分段分析：先从 `transcript.md` 提取时间戳正文，再按约 3 分钟小段分别请求 Ollama，最后合并、去重、按置信度筛选候选片段，避免完整 19 分钟转写一次性塞进 4096 context。
- 诊断脚本支持传入任务 ID，例如 `scripts\diagnose_ai_environment.py 37601f8548fd`，会显示真实任务转写长度、分段数量和最大 prompt 字数。
- 新增 `scripts/test_local_ai_chunked_analysis.py`，用模拟本地 Provider 验证长转写会被拆段、逐段分析并合并候选。
- 已用真实任务 `37601f8548fd` 跑通本地分段 AI 分析，生成 5 条候选片段，任务状态进入 `pending_review`。

## 2026-05-20 第二十轮：任务详情一键处理与 AI 自动降级

- 任务详情顶部操作改为“一键处理”：没有音频时自动提取音频，随后启动后台转写；已有 `transcript.md` 时不会重复转写。
- 保留“重新生成转写”作为明确的二级操作，点击前需要确认，避免误触后覆盖已有转写。
- 新增完整转写原文页面 `/tasks/{task_id}/transcript`，转写预览卡片可直接打开完整 `transcript.md`。
- 从任务详情顶部移除“生成切片”，切片生成只保留在片段审核页，并改为调用真实切割接口。
- 远程 AI 分析遇到 403、网络、超时、权限或余额类错误时，会自动降级尝试本地 AI，并在页面和日志中记录远程 / 本地结果。
- 调整任务详情布局和长路径换行，右侧日志 / 元信息栏更宽，不再轻易截断。

## 2026-05-20 第十九轮：转写 0% 卡住排查与稳定优先修复

- 将本地转写默认配置从 `large-v3 / cuda / float16` 改为 `medium / cpu / int8`，先保证 Windows 本地 MVP 可以稳定跑通；CUDA 加速仍可通过 `.env` 手动开启。
- 默认分段从 10 分钟缩短为 2 分钟，并在进度文件中写入模型、设备、计算类型、分段时长和更细的阶段说明，避免第 1 段长时间显示 0%。
- 新增 `/api/tasks/{task_id}/transcript-status`，任务详情页会自动轮询转写进度和预览内容；失败、完成或进度长时间不更新时会显示更明确的状态。
- 点击“生成转写 MD”前会清理旧的转写临时分段目录；如果发现过期的后台转写状态，会允许重新开始。
- 新增 `scripts/diagnose_transcription_environment.py` 和 `scripts/test_real_transcription_smoke.py`，用于检查 FFmpeg / FFprobe / faster-whisper / Python 环境，并用真实短音频验证转写是否真的可用。

## 2026-05-19 第十八轮：长音频后台分段转写

- 将本地 faster-whisper 转写从“一次性整段识别”改为“10 分钟分段 + 5 秒重叠 + 时间戳回填 + 自动拼接 Markdown”，降低 1 小时以上音频的失败风险。
- `/api/tasks/{task_id}/process/transcript` 现在会立即返回后台启动结果，不再让网页一直等待长时间转写完成。
- 新增 `transcripts/transcript_progress.json`，记录转写状态、当前分段、总分段数、百分比、进度说明和更新时间；任务详情页会显示转写进度条。
- CUDA / cuBLAS 失败后仍会自动 CPU 兜底，CPU 兜底模型改为 `medium / int8`，比 `large-v3` 在 CPU 上更适合长音频。
- 新增 `TRANSCRIPTION_CPU_FALLBACK_MODEL`、`TRANSCRIPTION_CHUNK_SECONDS`、`TRANSCRIPTION_CHUNK_OVERLAP_SECONDS` 配置示例。
- 新增 `scripts/test_transcript_chunking.py` 和 `scripts/test_transcript_background_start.py`，覆盖 65 分钟分段、时间戳偏移、Markdown 拼接、后台启动和重复点击保护。

## 2026-05-17 第十七轮：修复本地转写 CUDA 依赖缺失兜底

- 定位异常 `Library cublas64_12.dll is not found or cannot be loaded` 的原因：当前本地 faster-whisper 默认使用 `TRANSCRIPTION_DEVICE=cuda` 和 `TRANSCRIPTION_COMPUTE_TYPE=float16`，但 Windows 环境缺少 CUDA 12 运行库 DLL，导致 GPU 转写无法启动。
- 修改 `app/services/transcript_service.py`：当 GPU/CUDA/cuBLAS/cuDNN/NVIDIA 相关加载或转写异常出现时，自动切换到 `cpu / int8` 再执行一次转写，避免任务直接失败。
- `transcript.md` 的任务信息中，转写设备现在记录实际运行配置，例如 `cuda / float16` 或自动兜底后的 `cpu / int8`。
- 新增 `scripts/test_transcript_cpu_fallback.py`，用模拟的 `cublas64_12.dll` 缺失错误验证 GPU 失败后会自动请求 CPU 兜底模型。
- 已通过 `python -m compileall app` 和 `python scripts/test_transcript_cpu_fallback.py` 验证。

## 2026-05-17 第十六轮：项目文档整理、Git 安全提交准备

- 重写 `README.md`，改为清晰的项目入口、文档索引、启动方式和敏感信息说明。
- 新增 `docs/PROJECT_GUIDE.md`，用新手友好的方式说明项目用途、启动步骤、页面测试和命令行测试。
- 新增 `docs/SECURITY_AND_GIT.md`，说明 `.env`、API Key、任务产物、大视频和 Git 提交流程。
- 强化 `.gitignore`，继续忽略真实 `.env`，并补充 `.env.*`、数据库、视频、音频、日志等本地生成物规则。
- 提交前确认 `.env` 未被 Git 追踪，真实 API Key 不会保存到 Git。

## 2026-05-17 第十五轮：本地 faster-whisper 真实转写

- 将 `transcripts/transcript.md` 从占位 Markdown 升级为本地真实语音转写输出。
- 新增 `faster-whisper` 依赖，默认使用 `large-v3`、中文、CUDA、float16，适配本机 RTX 显卡优先的本地工作流。
- 转写阶段只做语音识别和文本整理，不调用 AI、不生成内容总结。
- `transcript.md` 现在包含“分钟级转写”和“逐句时间戳原文”两部分；分钟级内容只是按分钟拼接真实原文，后续 AI 分析仍由独立 AI 分析按钮负责。
- 转写失败时会把任务状态更新为 `failed`，并记录清晰错误信息，避免把失败误认为成功。
- 新增 `scripts/test_transcript_markdown_format.py`，用于验证分钟级整理、逐句时间戳 Markdown 和页面预览解析。
- 同步更新 `.env.example`、`NEXT_STEPS.md`、`docs/ARCHITECTURE.md`、`docs/TASK_FLOW.md` 和 `docs/UI_REFERENCE.md`。

## 2026-05-17 第十四轮：新建任务表单简化与 AI 偏好迁移

- 新建任务页的视频来源简化为“上传本机视频”，移除 NAS / 本地文件选择、路径输入、浏览路径和打开目录入口。
- 重新设计上传视频入口，改为更清爽的自定义上传按钮，并在选择文件后显示文件名。
- “单条切片最长”从下拉选择改为分钟数输入框，支持用户直接填写 1-60 分钟。
- 新建任务页移除 AI 偏好输入；右侧小贴士说明 AI 偏好会在任务详情的“AI 分析”板块填写，并解释它只影响候选片段推荐方向，不直接决定最终成片。
- 任务详情页新增“AI 分析”板块，支持保存 AI 偏好，并在点击远程 / 本地 AI 分析前先保存偏好。
- 新增 `PATCH /api/tasks/{task_id}/ai-preference`，用于更新任务的 AI 分析偏好。
- 同步更新 `NEXT_STEPS.md` 和 `docs/UI_REFERENCE.md`。

## 2026-05-17 第十三轮：任务详情页信息定位、地铁进度线与转写预览优化

- 任务详情页标题区新增任务定位信息：任务 ID、写入时间、主题和当前状态，方便对照本地任务文件夹。
- 将“处理进度时间线”合并进“状态概览”卡片，改为地铁路线图节点表达。
- 进度节点颜色调整为：绿色表示已通过，蓝色表示当前阶段，黄色表示异常或待处理问题。
- 转写预览不再直接展示 Markdown 元信息，只解析带时间戳的真实转写文本。
- 如果转写文件只是占位内容，页面会明确提示“还没有真实语音转写内容”，避免误以为已经完成真实转写。
- 已通过 `python -m compileall app`、详情页 HTTP 访问和转写预览解析小测试。
- 尝试使用 Codex in-app Browser 做视觉验证时连接超时，本轮已改用本地 HTTP 返回内容确认页面结构。

## 2026-05-17 第十一轮：AI 配置弹窗修复与默认配置预置

- 修复系统状态页 AI 配置弹窗关闭体验：关闭按钮、取消按钮、点击遮罩和 ESC 都可以关闭弹窗。
- 修复 Jinja 模板中 `ai_config.values.xxx` 与字典 `values()` 方法冲突的问题，AI 配置表单现在能正确回显默认值。
- AI 配置弹窗改为固定头部和底部操作区，避免内容过高时找不到取消/保存按钮。
- 远程中转站默认配置为 `https://ai.oneinfinityai.com`、`gpt-5.5`、Responses 协议、`xhigh` 推理强度，并在 Responses 请求中设置不保存响应内容。
- 本地 AI 默认改为 Ollama：`http://127.0.0.1:11434/v1`、`chat_completions`，页面只需要选择模型。
- 本地 Ollama 模型选项预置为 `qwen3:8b`、`gemma3:12b`、`qwen3:14b`。
- 继续保持真实 `.env` 被 `.gitignore` 忽略，页面保存配置时 API Key 留空会保留旧密钥，不会写入 Git 管理文件。
- 同步更新 `.env.example`、`docs/AI_ANALYSIS.md`、`docs/UI_REFERENCE.md` 和 `NEXT_STEPS.md`。

## 2026-05-17 第十轮：运行入口、任务隐藏、AI 配置与空白页补齐

- 定位上传失败原因：电脑上同时存在旧服务 `http://127.0.0.1:8000` 和新服务 `http://127.0.0.1:8001`，旧服务未加载 `/api/tasks/upload`、`/clips`、`/system`、`/api/files/browse`，所以会出现 `Method Not Allowed` 或 404。
- 本轮固定后续验证地址为 `http://127.0.0.1:8001`，并在系统状态页展示推荐运行地址，避免误开旧端口。
- `tasks` 表新增软隐藏字段 `is_deleted`、`deleted_at`，任务列表、工作台、片段审核总览默认不显示已隐藏任务。
- 新增 `DELETE /api/tasks/{task_id}`，只隐藏任务记录，不删除原视频、切片文件和任务目录。
- 任务列表新增“隐藏”按钮，点击前会二次确认，并明确提示文件不会删除。
- 新增 AI 配置读写能力：`GET /api/settings/ai` 和 `POST /api/settings/ai`，配置保存到项目根目录 `.env`，API Key 留空时保留旧值。
- 系统状态页新增 AI 配置弹窗，可保存默认 Provider、请求超时、远程 AI、本地 AI 相关字段，并展示 AI 配置是否已保存。
- `/clips` 片段审核总览升级为审核工作台，展示待 AI、待审核、可切片、已完成、异常任务，并提供详情和审核入口。
- 单任务片段审核页没有候选片段时，显示更清晰的空状态和下一步按钮：返回详情、生成转写、运行远程 AI 分析。
- 同步更新 `NEXT_STEPS.md`、`docs/UI_REFERENCE.md`、`docs/AI_ANALYSIS.md`、`docs/DATABASE_SCHEMA.md`。

## 2026-05-16 第七轮：AI 片段分析模块

- 新增 `.env.example`，补充远程中转站 API 和本地大模型 API 配置项，真实 `.env` 继续由 `.gitignore` 忽略。
- `app/core/config.py` 新增根目录 `.env` 读取能力，支持 `AI_REMOTE_*`、`AI_LOCAL_*` 和默认 Provider 配置。
- 新增 `prompts/clip_analysis_prompt.txt`，把 AI 片段分析 Prompt 从 Python 代码中拆出，并支持最大时长、候选数量、AI 偏好、转写文本变量注入。
- 新增 `app/services/ai/` Provider 抽象层，包含远程 Responses Provider、本地模型 Provider 和 AI 片段分析编排。
- AI 输出现在会经过严格 JSON 解析、Pydantic 字段校验、片段时长校验、转写时间范围校验；非法 JSON 会自动安全重试一次。
- 补齐 `clip_candidates` 表兼容迁移字段：`clip_key`、`highlight_reason`、`suggested_editing`、`confidence_score`、`selected_by_default`、`reviewed`。
- 新增 `/api/tasks/{task_id}/process/ai?provider=remote|local` 接口，流程为 `pending_ai -> ai_analyzing -> pending_review`，失败进入 `failed` 并记录错误。
- 任务详情页新增“远程 AI 分析”“本地 AI 分析”按钮，并展示 AI 分析 JSON 文件路径状态。
- 片段审核页继续读取 `clip_candidates` 表，展示推荐理由、传播价值、剪辑建议和 AI 置信度。
- 新增 `scripts/test_remote_ai_connection.py`、`scripts/test_local_ai_connection.py`、`scripts/test_ai_json_validation.py`、`scripts/test_mock_transcript_analysis.py`。
- 新增 `docs/AI_ANALYSIS.md`，并同步更新架构、任务流、UI 参考、数据库结构和下一步计划。

## 2026-05-16

- 检查项目目录，确认 `C:\Users\10578\Documents\New project 2` 已存在。
- 确认当前目录已是 Git 仓库，但还没有提交。
- 确认没有旧 Node.js 原型结构，没有 `package.json`、`src`、`public`。
- 保留现有 `REAMD.txt` 与根目录 PNG 图片。
- 将现有 PNG 复制为 `docs/design/live_streaming_slicing_workflow_ui_16x9.png`，作为后续 UI 参考图。
- 创建 FastAPI + Jinja2 + SQLite 项目骨架。
- 创建工作台、任务列表、新建任务、任务详情、片段审核五个页面的首版占位页面。
- 创建任务模型字段草案、SQLite 初始化模块和服务接口占位。
- 创建 README、PRD、架构、任务流、UI 参考、下一步计划和 Codex 协作说明文档。
- 创建 `.venv` 虚拟环境并安装首版依赖。
- 修复新版 Starlette / FastAPI 下 `TemplateResponse` 参数调用方式。
- 本地启动服务并验证 `/`、`/tasks`、`/tasks/new`、`/tasks/demo-001`、`/tasks/demo-001/clips`、`/health` 均可访问。
- 查看 UI 设计图 `ChatGPT Image 2026年5月16日 11_54_05.png`，确认视觉方向为浅色 Apple 风格后台。
- 读取 `docs/UI_REFERENCE.md`、`docs/PRD_v0.1.md`、`docs/ARCHITECTURE.md`、`docs/TASK_FLOW.md`、`AGENTS.md`。
- 根据 UI 图升级 5 个页面：工作台、任务列表、新建任务、任务详情、片段审核。
- 扩充模拟任务数据和候选片段数据，用于前端页面展示。
- 系统性升级 `app/static/css/styles.css`，补充浅色背景、左侧导航、统计卡片、流程线、筛选栏、表格、片段审核卡片和视频预览占位样式。
- 使用本地 Chrome 无头截图验证主要页面渲染正常，截图保存于 `data/screenshots/`，该目录内容不提交到 Git。
- 实现任务管理基础闭环：新建任务表单提交到 `/api/tasks`，写入 SQLite，任务列表页读取真实数据库任务，任务详情页按 `task_id` 查询真实任务。
- 将任务状态改为英文状态码保存，并在页面层转换为中文展示。
- 为后续处理流程预留 `update_task_status(task_id, new_status)` 状态更新方法。
- 新增 `docs/DATABASE_SCHEMA.md`，记录当前 `tasks` 表字段和状态码。
- 通过 API 创建“闭环验证任务”，验证“创建任务 → 列表展示 → 详情查看 → 状态更新方法”可用。
- 将任务资料根目录切换为 `E:\直播间切片工作流存储`，数据库仍保留在项目内 `data/workflow.sqlite3`。
- 新建任务时会在 E 盘为每个任务创建 `source`、`audio`、`transcripts`、`analysis`、`clips`、`logs` 子目录。
- 接入真实上传接口 `/api/tasks/upload`，上传视频会保存到任务目录 `source/`。
- 接入 NAS / 本地路径任务创建校验，首版只记录原视频路径，不复制大视频。
- 新增目录浏览接口 `/api/files/browse`，用于在页面中浏览 Windows 可访问的视频文件。
- 新增 FFmpeg 音频提取接口 `/api/tasks/{task_id}/process/audio`，输出 `audio/source.wav`。
- 新增转写 Markdown 生成接口 `/api/tasks/{task_id}/process/transcript`，输出 `transcripts/transcript.md`。
- 任务详情页新增“提取音频”“生成转写 MD”操作，并展示任务目录、音频路径、转写路径和日志路径。
- 片段审核页改为优先读取真实候选片段，没有候选时显示空状态，不再误用模拟数据。
- 新增系统状态页面 `/system`，展示 E 盘存储、FFmpeg、FFprobe、数据库、任务数量和最近异常。
- 使用临时 1 秒测试视频验证“创建任务 → 提取音频 → 生成转写 MD → 状态推进到待 AI 分析”流程可用，并清理临时测试数据。
- 第八轮开发：将片段审核页接入真实 `clip_candidates` 数据，不再依赖静态 UI 或占位候选片段。
- 为 `clip_candidates` 表补充 `clip_key`、`highlight_reason`、`suggested_editing`、`confidence_score`、`selected_by_default`、`reviewed` 字段迁移，并兼容旧字段 `reason`。
- 新增 `/tasks/{task_id}/clips/review` 页面入口，保留 `/tasks/{task_id}/clips` 兼容旧入口。
- 片段审核页顶部展示任务名称、候选片段总数、启用片段数和最大切片时长。
- 片段审核页支持筛选“全部片段 / 仅启用 / 高传播价值”，支持按“推荐分 / 置信度”和“时间顺序”排序。
- 每条候选片段展示并读取真实字段：启用状态、标题、开始时间、结束时间、时长、摘要、推荐理由、传播价值、剪辑建议、AI 置信度。
- 新增候选片段编辑保存接口 `/api/tasks/{task_id}/clips/{clip_id}/update` 和 `/api/tasks/{task_id}/clips/batch-update`。
- 保存时校验时间格式、结束时间大于开始时间、自动重算 `duration_seconds`，并限制片段时长不得超过任务最大切片时长。
- 新增 `/api/tasks/{task_id}/clips/generate` 生成切片预留接口，当前返回“待视频切割模块接入”提示，不改变任务状态。
- 更新任务详情页、任务列表页、片段审核总览页的审核入口到 `/tasks/{task_id}/clips/review`。
- 新增 `docs/CLIP_REVIEW.md`，记录片段审核页真实数据来源、人工编辑能力、保存逻辑和后续切割流程。
- 第九轮开发：实现自动切割视频能力，`/api/tasks/{task_id}/process/cuts` 会读取启用片段并调用 FFmpeg 输出短视频。
- 新增 `output_clip` 表，用于逐条记录输出视频文件名、路径、状态和失败原因。
- 将切割逻辑封装到 `app/services/video_cut_service.py`，支持 FFmpeg 可用性检查、原视频存在检查、时间校验、安全文件名、重复文件自动加序号和批量切割。
- 切片输出目录约定为任务目录下 `05_clips/`，不覆盖已有文件。
- 新增任务状态 `completed_with_errors`，用于表示部分切片成功、部分切片失败。
- 片段审核页保留真实切割结果展示区域；按第八轮边界，“生成切片”按钮当前暂接 `/api/tasks/{task_id}/clips/generate` 预留接口，提示“待视频切割模块接入”。
- 任务详情页已展示切片输出数量、切片目录和已生成视频列表。
- 使用 8 秒测试视频验证 2 条正常片段成功生成 mp4，1 条结束时间早于开始时间的错误片段被单独记录为失败，任务状态更新为 `completed_with_errors`。
- 新增 `docs/VIDEO_CUTTING.md`，记录自动切割流程、FFmpeg 依赖、输出目录、错误处理和测试方式。
- 第十轮开发：解决片段审核页视频预览无法显示的问题。
- 新增 `/media/tasks/{task_id}/source-video` 本地源视频读取路由，按任务 ID 校验源视频存在后以内联视频响应给浏览器播放。
- 片段审核页右侧从静态视频占位改为真实 `<video controls>` 播放器；点击左侧候选片段“预览”按钮会跳到对应开始时间，并在片段结束时间自动暂停。
- 第十一轮开发：按浏览器标注优化片段审核卡片内容。
- 片段卡片中将“AI 置信度”改为“AI 来源 / 模型”，优先读取分析结果元信息和任务日志，展示远程 AI 或本地 Ollama 以及对应模型名。
- 新增 `/api/tasks/{task_id}/clips/{clip_id}/transcript-excerpt`，按候选片段起止时间读取逐句转写原文。
- 片段审核页新增右侧可折叠转写抽屉，点击“查看这一段转写”即可查看对应片段原文；播放预览按钮改为更明确的可点击按钮。
- 更新 AI 分析 Prompt，要求摘要、推荐理由和剪辑建议输出更完整、可判断价值的内容。
- 第十二轮开发：按浏览器标注调整视频预览尺寸。
- 片段审核页右侧预览栏从 380px 缩小到更稳的 340px，并限制播放器高度，竖屏视频会完整缩放显示，不再把页面撑出横向滚动。
- 第十三轮开发：修复 DeepSeek 远程 AI 分析配置。
- 远程 AI 默认配置改为 DeepSeek OpenAI-compatible Chat Completions：`https://api.deepseek.com`、`deepseek-v4-flash`、`chat_completions`，避免继续用 Responses 协议和 `gpt-5.5` 模型名请求 DeepSeek。
- 按用户要求将远程默认模型从 `deepseek-v4-pro` 调整为 `deepseek-v4-flash`。
- 系统状态页 AI 配置弹窗新增远程协议和 DeepSeek 模型选择；保存配置时如果检测到 DeepSeek，会自动使用 `chat_completions` 并让 Review Model 跟随 DeepSeek 模型。
# 2026-05-27 项目文件夹改名与回收站

- 已把 E 盘任务目录从短 ID 改为项目名：`测试2 - 19min`、`测试3 - 康熙来了20160104` 现在直接位于 `E:\直播间切片工作流存储` 根目录。
- 已新增 `E:\直播间切片工作流存储\_回收站`，并把已隐藏任务 `测试1`、`闭环验证任务` 移入回收站，文件没有删除。
- `tasks` 表新增 `task_dir_name` 字段，任务 ID 仍用于网页地址和数据库关联，本地文件夹名改由 `task_dir_name` 控制。
- 新建任务时会用项目名创建本地文件夹；遇到 Windows 不允许的字符会自动替换，遇到重名会自动加序号，避免覆盖旧项目。
- 任务列表删除动作已从“隐藏”改为“移入回收站”：页面仍会隐藏任务，但对应项目目录会移动到 `_回收站`。
- 新增一次性迁移脚本 `scripts/migrate_task_dirs_to_project_names.py`；默认 dry-run 只预览，带 `--apply` 才会真正移动文件夹并更新数据库路径。
- 已执行迁移，数据库备份为 `data/workflow.sqlite3.bak_20260527_011841`。

## 2026-05-29 封面 MVP 方案添加

- 发布中心新增封面 MVP：每条待发布切片都可以在发布前输入封面时间点并生成封面预览。
- 封面生成采用“视频截图 + 暗色遮罩 + 标题大字”的稳定方案，暂不调用 AI 生图。
- 新增发布封面接口：`POST /api/publish/covers` 可在创建发布任务前生成封面；`POST /api/publish/jobs/{job_id}/cover` 预留给已有发布任务重新生成封面。
- 封面文件保存到任务目录 `07_covers/`，并通过 `/media/tasks/{task_id}/covers/{file_name}` 以内联图片方式预览。
- 发布任务创建时会保存 `cover_file_path`，发布记录里能看到已生成封面缩略图。

## 2026-06-02 发送中心 2.0 版本

- 已把 `/publish` 从“发布中心”改为“发送中心”，页面不再展示抖音 / B站开放平台 API 配置、Client Key、Access Token、OAuth 和账号表单。
- 新页面以待发送队列为主：从已完成切片读取切好的原片、封面帧、AI 标题、AI 话题和平台状态，默认生成抖音 + B站双平台队列。
- 新增发送队列接口：`POST /api/publish/queue/refresh` 可从已完成切片生成 opencli 发送任务；`POST /api/publish/send/start` 可按队列逐条发送；`POST /api/publish/jobs/{job_id}/send` 可发送单条任务。
- 封面逻辑改为“从视频中选一帧”：新增 `POST /api/publish/covers/frames` 生成多张候选帧，页面可手动切换并保存，不再默认叠加标题大字。
- AI 元数据补齐已接入：优先使用切片标题，话题和简介可由 AI 根据标题、摘要、推荐理由和转写片段生成；缺失时页面可一键重新生成。
- 自动发送改为 opencli 网页自动化：抖音打开 `creator.douyin.com` 投稿页，B站打开创作中心投稿页；发送批次一次只执行一条，避免平台窗口互相抢焦点。
- 已新增 `scripts/test_send_center_opencli_queue.py`，验证抖音 / B站 opencli 命令组装和备用元数据生成；已通过页面渲染检查，确认 `/publish` 不再出现 API 配置词。

## 2026-06-04 牛马片场品牌更新

- 项目品牌从“直播切片工作流”更新为“牛马片场 / NiuMa Studio”，副标题为“本地 AI 高光生产后台”。
- 新增牛马吉祥物 logo：`app/static/img/brand/niuma-studio-logo.png`，使用蓝色片场灯和圆角应用图标风格，页面文字由 HTML 渲染。
- 左侧导航品牌位已改为吉祥物图标 + `牛马片场`，不再显示 `LS` 文字标。
- `app/core/config.py` 的应用名改为 `NiuMa Studio` / `牛马片场`，Docker Compose 项目名、镜像名和容器名改为 `niuma-studio`。
- 暂时保留 E 盘历史存储目录 `E:\直播间切片工作流存储`，避免影响已有任务、数据库记录和视频产物。

## 2026-06-04 浏览器 favicon 更新

- 已基于牛马吉祥物主 logo 生成浏览器图标资源：`niuma-studio-favicon.ico`、`niuma-studio-favicon-32.png`、`niuma-studio-apple-touch-icon.png`、`niuma-studio-icon-192.png` 和 `niuma-studio-icon-512.png`。
- `base.html` 的 `<head>` 已新增 `rel="icon"`、32x32 PNG、Apple touch icon 和 `theme-color`，用于浏览器标签页、收藏夹和保存快捷方式。
- `app/main.py` 新增 `/favicon.ico` 路由，兼容浏览器默认请求根路径 favicon 的行为。

## 2026-06-04 发送中心 opencli 检测修复

- 修复 Windows 本地环境下发送中心误判“没有检测到 opencli”的问题：检测逻辑现在会优先识别 `opencli.cmd`、`opencli.exe`、`opencli` 和 `opencli.ps1`。
- 当普通 PATH 检测不到时，会额外检查 npm 全局安装目录：`%APPDATA%\npm` 和 `%USERPROFILE%\AppData\Roaming\npm`。
- opencli 自动发送命令现在会使用检测到的完整可执行文件路径，避免后台服务环境变量不完整时启动失败。
- 已补充 `scripts/test_send_center_opencli_queue.py` 测试，覆盖 Windows npm 目录里的 `opencli.cmd` 备用检测。

## 2026-06-04 自动字幕中文方块修复

- 修复自动加字幕后中文显示成小方块的问题：ASS 字幕生成会优先使用已保存的中文字体，并在字体不可用时兜底到 Windows 本机可用中文字体。
- FFmpeg `subtitles` 滤镜现在会显式传入 `C:\Windows\Fonts` 作为字体目录，避免 libass 找不到微软雅黑、黑体、Noto Sans SC 等中文字体。
- 字幕样式页面补充 `Noto Sans SC` 和 `Source Han Sans CN` 选项，方便后续选择更稳定的中文字体。
- 已验证：`python -m compileall app` 通过。

## 2026-06-04 发送中心标题话题安全与自动封面

- 发送中心新增本地内容安全清洗：AI 或人工填写的标题、平台话题和简介会自动规避低俗脏话、死亡血腥、暴力恐怖、色情、赌博博彩、诈骗引流、绝对化夸张等高风险表达。
- AI 元数据 Prompt 已明确要求 `tags` 返回平台 `#话题` 关键词，不再把标题重新解释成话题；页面字段同步改为“平台 #话题”。
- 刷新发送队列时会自动为每条切片截取一张默认封面帧，并写入发送任务；已有任务如果没有封面，刷新队列也会自动补封面。
- 保留“更换封面帧”能力：需要人工挑图时仍可生成多张候选帧并切换。
- 已补充 `scripts/test_send_center_opencli_queue.py` 测试，覆盖敏感词清洗、平台 #话题格式和旧脏数据发送前清洗。
- 已验证：`python -m compileall app`、`python scripts/test_send_center_opencli_queue.py` 通过。
